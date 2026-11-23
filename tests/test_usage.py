from app.usage import Usage


def test_normalisation_and_billable_buckets():
    usage = Usage.normalise("chat", {
        "prompt_tokens": 2000, "completion_tokens": 150,
        "prompt_tokens_details": {"cached_tokens": 1408, "cache_write_tokens": 128},
        "completion_tokens_details": {"reasoning_tokens": 22},
    })
    assert usage == Usage(2000, 1408, 128, 150, 22)
    assert (usage.uncached, usage.total) == (464, 2150)
    assert Usage.normalise("chat", {"prompt_tokens": 50, "completion_tokens": 5}) == Usage(50, completion=5)
    assert Usage.normalise("decision", {"input_tokens": 45, "output_tokens": 1}) == Usage(45, completion=1)
    assert Usage.normalise("embedding", {"prompt_tokens": 17, "total_tokens": 17}) == Usage(17)


def test_foundry_responses_usage_counts_cache_reads_writes_and_reasoning():
    raw = {"input_tokens": 1900, "input_tokens_details": {"cached_tokens": 1500, "cache_write_tokens": 128},
           "output_tokens": 85, "output_tokens_details": {"reasoning_tokens": 36}}
    parsed = Usage.normalise("router", raw)
    assert (parsed.uncached, parsed.cached, parsed.cache_write, parsed.reasoning, parsed.total) == (
        272, 1500, 128, 36, 1985,
    )
