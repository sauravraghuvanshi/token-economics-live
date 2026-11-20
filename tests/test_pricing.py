from decimal import Decimal as D

import pytest

from app.fake import FIXTURES
from app.pricing import PriceBook, percentage
from app.usage import Usage


def test_worked_examples():
    book = PriceBook.load(FIXTURES / "pricing.json")
    large, small = "gpt-6.1-sol", "GPT-4.1-mini-2025-04-14"
    a = book.bill("chat", large, Usage(3600, cache_write=3074, completion=150)).cost
    b = book.bill("chat", large, Usage(3600, cached=3074, completion=150)).cost
    c = book.bill("chat", large, Usage(3600, completion=150)).cost
    d = book.bill("router", small, Usage(3600, completion=120))
    e = book.bill("decision", "Microsoft-Decision-1.1", Usage(45, completion=12))
    assert (a, b, c) == (D("0.009937"), D("0.0028668"), D("0.0084"))
    assert [D(row["cost_usd"]) for row in d.lines] == [D("0.001440"), D("0.000192"), D("0.000504")]
    assert d.cost == D("0.002136")
    assert e.cost == D("0.00000189")
    control, candidate = c * 5, a + 4 * b
    assert (control, candidate) == (D("0.0420"), D("0.0214042"))
    assert D(percentage(control - candidate, control)).quantize(D("0.1")) == D("49.0")
    control_cpa, candidate_cpa = control / 5, candidate / 4
    assert (control_cpa, candidate_cpa) == (D("0.0084"), D("0.00535105"))
    assert D(percentage(candidate_cpa - control_cpa, control_cpa)).quantize(D("0.1")) == D("-36.3")


def test_unknown_rate_is_not_free_and_only_required_for_nonzero_usage():
    book = PriceBook.load(FIXTURES / "pricing.json")
    assert book.bill("chat", "gpt-4.1-mini", Usage(prompt=100)).cost == D("0.00004")
    bill = book.bill("chat", "gpt-4.1-mini", Usage(prompt=100, cache_write=100))
    assert bill.cost is None
    assert bill.missing == ["gpt-4.1-mini: cache write (30m)"]
    assert book.bill("chat", "unknown", Usage(10, completion=2)).cost is None
    book.cache_write_additive = True
    assert book.bill("chat", "gpt-6.1-sol", Usage(3600, cache_write=3074, completion=150)).cost == D("0.016085")


@pytest.mark.parametrize("kind", ["embedding", "decision", "chat"])
def test_missing_usage_is_rejected(kind):
    with pytest.raises(ValueError, match="usage counter"):
        Usage.normalise(kind, {})
