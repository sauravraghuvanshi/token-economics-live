import json

from app.fake import FIXTURES
from app.levers.retry_killswitch import (
    Extraction,
    corroborated_amounts,
    evidence_key,
    printed_subtotal,
    reconcile_retry,
    simulated_fx,
    validate,
)


def test_evidence_tracks_only_structured_fields_and_sorted_codes():
    docs = json.loads((FIXTURES / "invoices.json").read_text(encoding="utf-8"))
    original = Extraction.model_validate(docs["inv-03"]["first"])
    updated = original.model_copy(update={"notes": "A different explanation does not change the evidence."})
    errors = ["FX_RATE_UNAVAILABLE:USD", "MISSING_FIELD:example"]
    assert evidence_key(original, errors) == evidence_key(updated, errors[::-1])
    assert evidence_key(original, errors) != evidence_key(original, errors[:1])
    assert simulated_fx(original) == {"error": "FX_RATE_UNAVAILABLE:USD"}
    recoverable = docs["inv-02"]
    assert validate(Extraction.model_validate(recoverable["first"])) == ["TOTAL_MISMATCH"]
    assert validate(Extraction.model_validate(recoverable["corrected"])) == []


def test_reformatting_is_not_new_evidence_for_an_unavailable_rate():
    docs = json.loads((FIXTURES / "invoices.json").read_text(encoding="utf-8"))
    original = Extraction.model_validate(docs["inv-03"]["first"])
    updated = original.model_copy(update={"invoice_date": "2026/11/07", "subtotal": None})
    errors = ["FX_RATE_UNAVAILABLE:USD"]
    assert evidence_key(original, errors) == evidence_key(updated, errors)
    assert evidence_key(original, [*errors, "TOTAL_MISMATCH"]) != evidence_key(updated, errors)


def test_retry_repairs_line_without_allowing_a_regression_in_the_printed_total():
    docs = json.loads((FIXTURES / "invoices.json").read_text(encoding="utf-8"))
    first = Extraction.model_validate(docs["inv-05"]["first"])
    fresh = Extraction.model_validate(docs["inv-05"]["corrected"]).model_copy(update={"total": 56400.0})
    proof = corroborated_amounts(first, validate(first))
    repaired, receipt = reconcile_retry(first, fresh, proof)
    assert validate(repaired) == []
    assert repaired.total == 56640
    assert receipt is not None and receipt["adjusted_retry_fields"] == ["total"]
    assert fresh.total == 56400

    changed = fresh.model_copy(update={"subtotal": 51000.0})
    assert reconcile_retry(first, changed, proof) == (changed, None)


def test_retry_reconciles_a_total_only_when_subtotal_and_tax_corroborate_it():
    docs = json.loads((FIXTURES / "invoices.json").read_text(encoding="utf-8"))
    first = Extraction.model_validate(docs["inv-02"]["first"])
    proof = corroborated_amounts(first, validate(first))
    repaired, receipt = reconcile_retry(first, first, proof)
    assert validate(repaired) == []
    assert repaired.total == 139240 and receipt is not None
    wrong_tax = first.taxes[0].model_copy(update={"amount": 21000.0})
    unproved = first.model_copy(update={"taxes": [wrong_tax]})
    assert corroborated_amounts(unproved, validate(unproved)) is None


def test_printed_subtotal_resolves_an_omitted_indian_grouped_number():
    docs = json.loads((FIXTURES / "invoices.json").read_text(encoding="utf-8"))
    extracted = Extraction.model_validate(docs["inv-01"]["first"]).model_copy(update={"subtotal": None})
    assert validate(extracted) == ["MISSING_FIELD:subtotal"]
    recovered, source = printed_subtotal(extracted, "Subtotal                     1,06,000\n")
    assert recovered.subtotal == 106000 and source == "1,06,000"
    assert validate(recovered) == []


def test_printed_subtotal_does_not_infer_missing_or_disagreeing_values():
    docs = json.loads((FIXTURES / "invoices.json").read_text(encoding="utf-8"))
    extracted = Extraction.model_validate(docs["inv-01"]["first"]).model_copy(update={"subtotal": None})
    for text in ("", "Subtotal Pending", "Subtotal 10,6,000", "Subtotal 1,05,000",
                 "Subtotal 1,06,000\nSubtotal 106,000", "Subtotal 106,000\nSubtotal unreadable"):
        assert printed_subtotal(extracted, text) == (extracted, None)
    inconsistent = extracted.model_copy(update={"total": 125081.0})
    assert printed_subtotal(inconsistent, "Subtotal 106,000") == (inconsistent, None)
    different = extracted.model_copy(update={"subtotal": 105000.0})
    assert printed_subtotal(different, "Subtotal 106,000") == (different, None)
