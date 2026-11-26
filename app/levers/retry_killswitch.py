"""Invoice extraction, simulated FX, deterministic checks and bounded review."""

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import replace
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from ..calls import CallSession
from ..dataset import Invoice
from ..foundry import ModelError
from ..pricing import usd
from ..prompts import EXTRACT_SCHEMA, response_schema
from ..settings import MAX_DOCUMENT_RETRIES, Settings
from .model_routing import probability

EXTRACTOR = (
    "Extract this invoice into the JSON schema. On the first attempt transcribe the printed numeric fields "
    "exactly, even when they disagree with arithmetic or the amount in words. Do not repair them until the "
    "validator provides evidence. Use plain numbers without separators. Leave an unprinted subtotal null, "
    "and write notes in English."
)
RETRY = (
    "This is a retry, so the first-attempt transcription rule no longer applies to the flagged field. "
    "The previous extraction failed these checks: {errors}. Previous extraction: {previous}. "
    "Keep the printed identifiers, currency, quantities, unit prices, and any unflagged fields. "
    "Repair only the fields supported by the validator diagnostics and the printed evidence. "
    "Give a short factual description of any correction in notes."
)
GSTIN = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")
SUBTOTAL_FIELD = re.compile(r"^[ \t]*Subtotal\b([^\r\n]*)", re.IGNORECASE | re.MULTILINE)
PRINTED_AMOUNT = re.compile(
    r"(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]{1,2}(?:,[0-9]{2})+,[0-9]{3}|[0-9]+)"
    r"(?:\.[0-9]{1,2})?",
)
REVIEW_SCHEMA = response_schema("invoice_review", {"accepted": {"type": "string", "enum": ["yes", "no"]}})
REVIEW = {
    "type": "noul",
    "instructions": (
        "Should this structured invoice be accepted? Compare the vendor, invoice number, date, GSTIN, currency, "
        "quantities, unit prices and tax rates to the source invoice. Accept harmless formatting differences. "
        "For a conflicting printed line amount or total, use the verified arithmetic in the state: "
        "a quantity times its unchanged unit price gives the correct line amount, and an unchanged subtotal "
        "plus tax gives the correct total. The printed subtotal, taxes or amount in words can corroborate "
        "a repair. Accept a repaired value supported by these checks; do not reject it solely because it "
        "differs from a printed number that contradicts those checks. Reject unsupported changes."
    ),
}
PRESENTER_STOP = "presenter stopped retries"


class Fields(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class Line(Fields):
    description: str
    qty: float
    unit_price: float
    amount: float


class Tax(Fields):
    name: str
    rate_percent: float
    amount: float


class Extraction(Fields):
    vendor_name: str
    gstin: str | None
    invoice_number: str
    invoice_date: str
    currency: str
    line_items: list[Line]
    subtotal: float | None
    taxes: list[Tax]
    total: float
    notes: str


def decimal(value: float | int) -> Decimal:
    return Decimal(str(value))


def printed_subtotal(value: Extraction, ocr_text: str) -> tuple[Extraction, str | None]:
    if value.subtotal is not None or value.currency != "INR" or not value.line_items:
        return value, None
    fields = SUBTOTAL_FIELD.findall(ocr_text)
    if len(fields) != 1:
        return value, None
    printed = fields[0].strip().removeprefix(":").strip()
    if not PRINTED_AMOUNT.fullmatch(printed):
        return value, None
    subtotal = Decimal(printed.replace(",", ""))
    line_amounts = [decimal(line.qty) * decimal(line.unit_price) for line in value.line_items]
    if (line_amounts != [decimal(line.amount) for line in value.line_items]
            or sum(line_amounts) != subtotal
            or subtotal + sum(decimal(tax.amount) for tax in value.taxes) != decimal(value.total)):
        return value, None
    return value.model_copy(update={"subtotal": float(subtotal)}), printed


def validate(value: Extraction) -> list[str]:
    errors = [f"MISSING_FIELD:{name}" for name in ("vendor_name", "invoice_number", "invoice_date", "line_items")
              if not getattr(value, name)]
    if value.currency not in {"INR", "USD", "EUR"}:
        errors.append("MISSING_FIELD:currency")
    elif value.currency == "INR":
        if not value.gstin:
            errors.append("MISSING_FIELD:gstin")
        elif not GSTIN.fullmatch(value.gstin):
            errors.append("GSTIN_FORMAT")
        if value.subtotal is None:
            errors.append("MISSING_FIELD:subtotal")
    line_sum = sum((decimal(line.amount) for line in value.line_items), start=Decimal(0))
    for index, line in enumerate(value.line_items, start=1):
        if abs(decimal(line.qty) * decimal(line.unit_price) - decimal(line.amount)) > 1:
            errors.append(f"LINE_MISMATCH:{index}")
    base = decimal(value.subtotal) if value.subtotal is not None else line_sum
    if abs(line_sum - base) > 1:
        errors.append("SUBTOTAL_MISMATCH")
    if abs(base + sum(decimal(tax.amount) for tax in value.taxes) - decimal(value.total)) > 1:
        errors.append("TOTAL_MISMATCH")
    return errors


def evidence_key(value: Extraction | None, errors: list[str]) -> str:
    # Reformatting a date or description cannot repair an unavailable rate for the same currency.
    fx_only = len(errors) == 1 and errors[0].startswith("FX_RATE_UNAVAILABLE:")
    material = {"fields": None if fx_only or value is None else value.model_dump(exclude={"notes"}),
                "errors": sorted(errors)}
    return hashlib.sha256(json.dumps(material, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def simulated_fx(value: Extraction | None) -> dict[str, Any]:
    if value is None:
        return {"status": "not called"}
    if value.currency == "INR":
        return {"amount_inr": value.total}
    return {"error": f"FX_RATE_UNAVAILABLE:{value.currency}"}


def corroborated_amounts(value: Extraction | None, errors: list[str]) -> tuple[list[Decimal], Decimal] | None:
    if value is None or value.currency != "INR" or value.subtotal is None or not value.line_items:
        return None
    amounts = [decimal(line.qty) * decimal(line.unit_price) for line in value.line_items]
    subtotal = decimal(value.subtotal)
    if sum(amounts) != subtotal or any(
        decimal(tax.amount) != subtotal * decimal(tax.rate_percent) / 100 for tax in value.taxes
    ):
        return None
    total = subtotal + sum(decimal(tax.amount) for tax in value.taxes)
    bad_lines = {f"LINE_MISMATCH:{i}" for i, (line, amount) in enumerate(zip(value.line_items, amounts), 1)
                 if decimal(line.amount) != amount}
    error_codes = set(errors)
    if bad_lines:
        if not bad_lines <= error_codes <= bad_lines | {"SUBTOTAL_MISMATCH"} or decimal(value.total) != total:
            return None
    elif error_codes != {"TOTAL_MISMATCH"}:
        return None
    return amounts, total


def retry_anchors(value: Extraction) -> tuple[
    str, str | None, str, float | None, list[tuple[float, float]], list[tuple[float, float]],
]:
    return (value.currency, value.gstin, value.invoice_number, value.subtotal,
            [(line.qty, line.unit_price) for line in value.line_items],
            [(tax.rate_percent, tax.amount) for tax in value.taxes])


def reconcile_retry(
    previous: Extraction | None, current: Extraction, amounts: tuple[list[Decimal], Decimal] | None,
) -> tuple[Extraction, dict[str, Any] | None]:
    # A second reading must corroborate the printed inputs before we replace derived values.
    if previous is None or amounts is None or retry_anchors(previous) != retry_anchors(current):
        return current, None
    line_amounts, total = amounts
    adjusted = [f"line {i} amount" for i, (line, amount) in enumerate(zip(current.line_items, line_amounts), 1)
                if decimal(line.amount) != amount]
    if decimal(current.total) != total:
        adjusted.append("total")
    output = current.model_copy(update={
        "line_items": [line.model_copy(update={"amount": float(amount)})
                       for line, amount in zip(current.line_items, line_amounts)],
        "total": float(total),
        "notes": "Printed quantities, prices, subtotal and taxes agreed across both extractions. "
                 "Confirmed line amounts and total using arithmetic before review.",
    })
    return output, {"line_amounts": [str(amount) for amount in line_amounts],
                    "total": str(total), "adjusted_retry_fields": adjusted}


def retry_evidence(
    value: Extraction | None, errors: list[str], amounts: tuple[list[Decimal], Decimal] | None,
) -> str:
    if value is None:
        return ""
    line_sum = sum((decimal(line.amount) for line in value.line_items), Decimal(0))
    tax = sum((decimal(row.amount) for row in value.taxes), Decimal(0))
    subtotal = decimal(value.subtotal) if value.subtotal is not None else line_sum
    checks = [
        f"LINE_MISMATCH:{index}: quantity {line.qty:g} times unit price {line.unit_price:g} "
        f"equals {decimal(line.qty) * decimal(line.unit_price)}, not the extracted amount {line.amount:g}."
        for index, line in enumerate(value.line_items, start=1)
        if f"LINE_MISMATCH:{index}" in errors
    ]
    if amounts is not None:
        confirmed_lines, confirmed_total = amounts
        checks.append(f"The printed quantities and unit prices give line amounts {', '.join(map(str, confirmed_lines))}. "
                      f"They sum to the printed subtotal {subtotal}; adding the printed tax {tax} gives "
                      f"{confirmed_total}. Keep these reconciled amounts and the printed subtotal and taxes.")
    elif checks:
        checks.append("If the printed quantity and unit price are clear, use their product as the line amount "
                      "before reconciling the subtotal and taxes.")
    if amounts is None and "TOTAL_MISMATCH" in errors and not any(code.startswith("LINE_MISMATCH:") for code in errors):
        checks.append(f"TOTAL_MISMATCH: subtotal {subtotal} plus taxes {tax} equals {subtotal + tax}; "
                      "cross-check with the amount in words.")
    if any(error.startswith("FX_RATE_UNAVAILABLE:") for error in errors):
        checks.append("The FX rate is unavailable in the simulated tool. This is not an extraction error; "
                      "keep the printed currency and amounts.")
    return "\n\nValidator diagnostics from the previous extraction:\n" + "\n".join(checks) if checks else ""


def review_state(invoice: Invoice, value: Extraction) -> dict[str, Any]:
    line_amounts = [decimal(line.qty) * decimal(line.unit_price) for line in value.line_items]
    subtotal = decimal(value.subtotal) if value.subtotal is not None else sum(line_amounts, Decimal(0))
    tax = sum((decimal(row.amount) for row in value.taxes), Decimal(0))
    return {
        "ocr_text": invoice.ocr_text,
        "extraction": value.model_dump(mode="json", exclude={"notes"}),
        "validator_arithmetic": {
            "line_amounts_from_qty_times_unit_price": [str(amount) for amount in line_amounts],
            "subtotal_from_lines": str(sum(line_amounts, Decimal(0))),
            "total_from_subtotal_plus_tax": str(subtotal + tax),
        },
    }


async def document(
    calls: CallSession, cfg: Settings, invoice: Invoice, progress: Callable[[str, dict[str, Any]], None],
    stop_requested: Callable[[], bool],
) -> tuple[dict[str, Any], str, list[str]]:
    prior = None
    output: Extraction | None = None
    errors: list[str] = []
    wasted = 0
    attempts: list[dict[str, Any]] = []
    stop = "safety ceiling"
    mode, control = calls.context.mode, calls.context.lane == "control"
    count = cfg.safety_ceiling if control else MAX_DOCUMENT_RETRIES + 1
    if not control and mode == "no_retries":
        count = 1
    for num in range(1, count + 1):
        if control and num > 1 and stop_requested():
            stop = PRESENTER_STOP
            break
        progress("attempt_started", {"number": num, "limit": count})
        before = calls.tokens["prompt"] + calls.tokens["completion"]
        ctx = replace(calls.context, attempt=num)
        instructions = EXTRACTOR
        evidence = ""
        previous = output
        amounts = None
        if num > 1:
            amounts = corroborated_amounts(previous, errors)
            instructions += "\n\n" + RETRY.format(
                errors=", ".join(errors), previous=previous.model_dump_json(exclude={"notes"}) if previous else "null")
            evidence = retry_evidence(previous, errors, amounts)
        result = await calls.invoke("chat", cfg.small_deployment, {
            "messages": [{"role": "system", "content": instructions},
                         {"role": "user", "content": invoice.ocr_text + evidence}],
            "response_format": EXTRACT_SCHEMA, "max_completion_tokens": 700,
        }, context=ctx)
        reconciliation, subtotal_source = None, None
        try:
            output = Extraction.model_validate_json(str(result.result))
            output, subtotal_source = printed_subtotal(output, invoice.ocr_text)
            output, reconciliation = reconcile_retry(previous, output, amounts)
            errors = validate(output)
        except ValidationError as error:
            output = None
            errors = [f"MISSING_FIELD:{item['loc'][0]}" for item in error.errors() if item["type"] == "missing"]
            errors = errors or ["UNPARSEABLE_EXTRACTION"]
        fx = simulated_fx(output)
        calls.role("tool", {"simulated": True, **fx})
        if "error" in fx:
            errors.append(fx["error"])
        calls.role("validator", {"errors": list(errors)})
        review_score = None
        if not errors and output is not None:
            state = review_state(invoice, output)
            if cfg.decision_deployment:
                d = await calls.invoke("decision", cfg.decision_deployment,
                                       {"state": state, "questions": {"accepted": REVIEW}}, context=ctx)
                try:
                    review_score = probability(d.result["accepted"]["noul"])
                except (KeyError, TypeError):
                    raise ModelError("Decision reviewer did not return a noul probability.") from None
            else:
                review = await calls.invoke("chat", cfg.small_deployment, {
                    "messages": [{"role": "system", "content": REVIEW["instructions"]},
                                 {"role": "user", "content": json.dumps(state)}],
                    "response_format": REVIEW_SCHEMA, "max_completion_tokens": 40,
                }, context=ctx)
                try:
                    review_score = 1.0 if json.loads(review.result)["accepted"] == "yes" else 0.0
                except (KeyError, TypeError, json.JSONDecodeError):
                    review_score = 0.0
            if review_score < 0.7:
                errors.append("REVIEWER_REJECTED")
        key = evidence_key(output, errors)
        repeated = key == prior
        tokens = calls.tokens["prompt"] + calls.tokens["completion"]
        if repeated:
            wasted += tokens - before
        attempts.append({"number": num, "errors": list(errors), "evidence_key": key, "repeated": repeated,
                         "review_probability": review_score, "tokens": tokens - before})
        if reconciliation is not None:
            attempts[-1]["arithmetic_reconciliation"] = reconciliation
        if subtotal_source is not None:
            attempts[-1]["subtotal_from_ocr"] = subtotal_source
        progress("attempt_checked", {"number": num, "limit": count, "errors": list(errors), "repeated": repeated})
        if not errors:
            stop = "accepted"
            break
        if not control:
            if mode == "tight" and cfg.tight_kill_switch_usd is not None:
                if calls.cost is None or calls.cost >= cfg.tight_kill_switch_usd:
                    stop = "kill switch"
                    break
            if tokens >= cfg.doc_token_budget:
                stop = "document token budget"
                break
            if repeated and mode == "tight":
                stop = "no new evidence"
                break
            if num == count:
                stop = "no retries" if mode == "no_retries" else "retry limit"
        prior = key
    accepted = stop == "accepted"
    manual = stop == PRESENTER_STOP
    detail = {"attempts": attempts, "stop": stop, "manual_stop": manual,
              "outcome": "accepted" if accepted else "safety ceiling" if control and not manual else "handoff",
              "wasted_tokens": wasted,
              "output": output.model_dump(mode="json") if output else None, "errors": errors,
              "document_spend_usd": usd(calls.cost), "simulated_tool": True}
    return detail, f"{invoice.title} · {detail['outcome']}", [] if accepted else [stop, *errors]
