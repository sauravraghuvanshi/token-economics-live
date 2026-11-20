import json

from app.fake import FIXTURES
from app.levers.retry_killswitch import Extraction, evidence_key, simulated_fx, validate


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
