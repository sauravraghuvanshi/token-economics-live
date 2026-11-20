"""Invoice extraction, simulated FX, deterministic checks and bounded review."""

import hashlib
import json
import re
from dataclasses import replace
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from ..calls import CallSession
from ..dataset import Invoice
from ..foundry import ModelError
from ..pricing import usd
from ..prompts import object_schema, response_schema
from ..settings import Settings
from .model_routing import probability

EXTRACTOR = "Extract this invoice into the JSON schema. Copy numbers exactly as printed; use plain numbers without separators."
RETRY = ("Your previous extraction failed these checks: {errors}. Previous extraction: {previous}. "
         "Re-read the document and fix only what the errors point to. If a printed number conflicts with the "
         "arithmetic or with the amount in words, use the arithmetic or the amount in words and explain in notes.")
GSTIN = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")
STRING = {"type": "string"}
NUMBER = {"type": "number"}
EXTRACT_SCHEMA = response_schema("invoice", {
    "vendor_name": STRING, "gstin": {"type": ["string", "null"]}, "invoice_number": STRING,
    "invoice_date": STRING, "currency": {"type": "string", "enum": ["INR", "USD", "EUR"]},
    "line_items": {"type": "array", "items": object_schema({
        "description": STRING, "qty": NUMBER, "unit_price": NUMBER, "amount": NUMBER,
    })},
    "subtotal": {"type": ["number", "null"]},
    "taxes": {"type": "array", "items": object_schema({"name": STRING, "rate_percent": NUMBER, "amount": NUMBER})},
    "total": NUMBER, "notes": STRING,
})
REVIEW_SCHEMA = response_schema("invoice_review", {"accepted": {"type": "string", "enum": ["yes", "no"]}})
REVIEW = {
    "type": "noul",
    "instructions": (
        "Does the extraction faithfully represent the source invoice, with only supported corrections? "
        "Check the invoice identifiers, currency, quantities and unit prices against the source. When "
        "printed amounts conflict, use the quantity times unit price, subtotal plus tax, or amount in "
        "words to decide which number is supported. A supported correction is not a discrepancy."
    ),
}


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
    material = {"fields": value.model_dump(exclude={"notes"}) if value else None, "errors": sorted(errors)}
    return hashlib.sha256(json.dumps(material, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def simulated_fx(value: Extraction | None) -> dict[str, Any]:
    if value is None:
        return {"status": "not called"}
    if value.currency == "INR":
        return {"amount_inr": value.total}
    return {"error": f"FX_RATE_UNAVAILABLE:{value.currency}"}


def retry_evidence(value: Extraction | None, errors: list[str]) -> str:
    if value is None:
        return ""
    checks = [
        f"LINE_MISMATCH:{index}: quantity {line.qty:g} times unit price {line.unit_price:g} "
        f"equals {decimal(line.qty) * decimal(line.unit_price)}, not the extracted amount {line.amount:g}."
        for index, line in enumerate(value.line_items, start=1)
        if f"LINE_MISMATCH:{index}" in errors
    ]
    if checks:
        checks.append("If the printed quantity and unit price are clear, use their product as the line amount "
                      "before reconciling the subtotal and taxes.")
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
        "extraction": value.model_dump(mode="json"),
        "validator_arithmetic": {
            "line_amounts_from_qty_times_unit_price": [str(amount) for amount in line_amounts],
            "subtotal_from_lines": str(sum(line_amounts, Decimal(0))),
            "total_from_subtotal_plus_tax": str(subtotal + tax),
        },
    }


async def document(calls: CallSession, cfg: Settings, invoice: Invoice) -> tuple[dict[str, Any], str, list[str]]:
    prior = None
    output: Extraction | None = None
    errors: list[str] = []
    wasted = 0
    attempts: list[dict[str, Any]] = []
    stop = "safety ceiling"
    mode, control = calls.context.mode, calls.context.lane == "control"
    count = cfg.safety_ceiling if control else cfg.retry_limit + 1
    if not control and mode == "no_retries":
        count = 1
    for num in range(1, count + 1):
        before = calls.tokens["prompt"] + calls.tokens["completion"]
        ctx = replace(calls.context, attempt=num)
        instructions = EXTRACTOR
        evidence = ""
        if num > 1:
            instructions += "\n\n" + RETRY.format(
                errors=", ".join(errors), previous=output.model_dump_json() if output else "null")
            evidence = retry_evidence(output, errors)
        result = await calls.invoke("chat", cfg.small_deployment, {
            "messages": [{"role": "system", "content": instructions},
                         {"role": "user", "content": invoice.ocr_text + evidence}],
            "response_format": EXTRACT_SCHEMA, "max_completion_tokens": 700,
        }, context=ctx)
        try:
            output = Extraction.model_validate_json(str(result.result))
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
            if repeated:
                stop = "no new evidence"
                break
            if num == count:
                stop = "no retries" if mode == "no_retries" else "retry limit"
        prior = key
    accepted = stop == "accepted"
    detail = {"attempts": attempts, "stop": stop, "outcome": ("accepted" if accepted else
              "safety ceiling" if control else "handoff"), "wasted_tokens": wasted,
              "output": output.model_dump(mode="json") if output else None, "errors": errors,
              "document_spend_usd": usd(calls.cost), "simulated_tool": True}
    return detail, f"{invoice.title} · {detail['outcome']}", [] if accepted else [stop, *errors]
