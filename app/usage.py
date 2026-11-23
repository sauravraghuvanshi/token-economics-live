"""Normalise actual billable usage; missing primary counters are errors, never zero."""

from dataclasses import asdict, dataclass
from typing import Any


def token_count(value: Any, field: str, *, optional: bool = False) -> int:
    if value is None and optional:
        return 0
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"Missing or invalid usage counter: {field}")
    return value


@dataclass(frozen=True)
class Usage:
    prompt: int = 0
    cached: int = 0
    cache_write: int = 0
    completion: int = 0
    reasoning: int = 0

    @property
    def uncached(self) -> int:
        return max(self.prompt - self.cached - self.cache_write, 0)

    @property
    def total(self) -> int:
        return self.prompt + self.completion

    def json(self) -> dict[str, int]:
        return {**asdict(self), "uncached": self.uncached}

    @classmethod
    def normalise(cls, kind: str, raw: dict[str, Any]) -> "Usage":
        if kind == "decision":
            return cls(
                prompt=token_count(raw.get("input_tokens", raw.get("prompt_tokens")), "input_tokens"),
                completion=token_count(raw.get("output_tokens", raw.get("completion_tokens")),
                                       "output_tokens", optional=True),
            )
        # Prompt Agents use the Responses API; health probes still use Chat Completions.
        responses = kind in {"chat", "router"} and "input_tokens" in raw
        prompt_field = "input_tokens" if responses else "prompt_tokens"
        completion_field = "output_tokens" if responses else "completion_tokens"
        prompt = token_count(raw.get(prompt_field), prompt_field)
        if kind == "embedding":
            return cls(prompt=prompt)
        pd = raw.get(f"{prompt_field}_details") or {}
        cd = raw.get(f"{completion_field}_details") or {}
        if not isinstance(pd, dict) or not isinstance(cd, dict):
            raise ValueError("Invalid nested token usage counters.")
        return cls(
            prompt=prompt,
            cached=token_count(pd.get("cached_tokens"), "cached_tokens", optional=True),
            cache_write=token_count(pd.get("cache_write_tokens"), "cache_write_tokens", optional=True),
            completion=token_count(raw.get(completion_field), completion_field),
            reasoning=token_count(cd.get("reasoning_tokens"), "reasoning_tokens", optional=True),
        )
