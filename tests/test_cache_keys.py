from app.dataset import load_dataset
from app.levers.response_cache import build_cache_key


def test_guarded_keys_share_only_exact_facts():
    dataset = load_dataset()
    requests = dataset.requests["response_cache"]
    keys = [build_cache_key(row.text, dataset.patterns, "prefix", "frontier") for row in requests]
    assert keys[0] == keys[1] and keys[0][1] == "v1:en:refund_timing:destination=card"
    assert keys[2] is not None and keys[2] != keys[0]
    assert keys[3] == keys[4] and keys[5] != keys[3]
    assert keys[6] == keys[7] and keys[6] is not None
    assert keys[8] == keys[0]
    assert keys[9] is None
    combined = "When will the card refund arrive and how far back can Business export audit logs?"
    assert build_cache_key(combined, dataset.patterns, "prefix", "frontier") is None
    other_model = build_cache_key(requests[0].text, dataset.patterns, "prefix", "other")
    assert other_model and keys[0][0] != other_model[0]
