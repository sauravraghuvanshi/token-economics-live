"""Deterministic fact keys and deliberately unsafe fuzzy matching for the demo."""

import hashlib
import json
import math
import re
import unicodedata
from dataclasses import dataclass
from typing import Any

from ..calls import CallSession
from ..dataset import CachePatterns
from ..foundry import ModelError
from ..grading import parse_answer
from ..prompts import stable_prefix
from ..settings import Settings
from .prompt_caching import answer

DEVANAGARI = re.compile(r"[\u0900-\u097f]")


def normalise(text: str) -> str:
    folding = unicodedata.normalize("NFKC", text).casefold()
    return " ".join(re.sub(r"[^\w\s\u0900-\u097f]", " ", folding).split())


def has(text: str, term: str) -> bool:
    term = normalise(term)
    return term in text if DEVANAGARI.search(term) else bool(re.search(rf"(?<!\w){re.escape(term)}(?!\w)", text))


def build_cache_key(message: str, patterns: CachePatterns, prefix_hash: str,
                    deployment: str) -> tuple[str, str] | None:
    text = normalise(message)
    for phrase in patterns.strip_phrases:
        text = re.sub(rf"(?<!\w){re.escape(normalise(phrase))}(?!\w)", " ", text)
    text = " ".join(text.split())
    lang = "hi" if DEVANAGARI.search(text) else "en"
    matches = [p for p in patterns.patterns if all(any(has(text, term) for term in group)
                                                  for group in p.match_all)]
    if len(matches) != 1:
        return None
    pattern, = matches
    params: dict[str, str] = {}
    for param, values in pattern.params.items():
        present = [value for value, terms in values.items() if any(has(text, term) for term in terms)]
        if len(present) != 1:
            return None
        params[param] = present[0]
    material = {"version": patterns.version, "language": lang, "topic": pattern.topic,
                "params": params, "product": patterns.product, "system_prompt_sha256": prefix_hash,
                "model": deployment}
    encoded = json.dumps(material, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    display = ":".join([patterns.version, lang, pattern.topic, *(f"{k}={v}" for k, v in sorted(params.items()))])
    return hashlib.sha256(encoded).hexdigest(), display


def cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        return 0
    denominator = math.hypot(*a) * math.hypot(*b)
    return sum(x * y for x, y in zip(a, b, strict=True)) / denominator if denominator else 0


@dataclass
class Entry:
    id: str
    key: str
    display_key: str
    question: str
    answer: str
    model: str
    vector: list[float] | None
    hits: int = 0

    def public(self) -> dict[str, Any]:
        parsed = parse_answer(self.answer)
        return {"id": self.id, "key": self.display_key, "question": self.question,
                "answer": parsed.answer if parsed else self.answer, "hits": self.hits}


class ResponseCache:
    def __init__(self, cfg: Settings, policies: str, patterns: CachePatterns):
        self.cfg, self.policies, self.patterns = cfg, policies, patterns
        self.prefix_hash = hashlib.sha256(stable_prefix(policies).encode()).hexdigest()
        self.entries: dict[str, list[Entry]] = {"fact_guarded": [], "naive": []}

    def clear(self, mode: str | None = None) -> None:
        for bucket in (self.entries.values() if mode is None else [self.entries[mode]]):
            bucket.clear()

    async def lookup(self, calls: CallSession, text: str, timestamp: str,
                     ticket: str) -> tuple[str, dict[str, Any]]:
        mode = calls.context.mode
        bucket = self.entries[mode]
        vector = None
        key, display = None, None
        detail: dict[str, Any] = {"cache_action": "bypass"}
        entry = None
        if mode == "fact_guarded":
            resolved = build_cache_key(text, self.patterns, self.prefix_hash, self.cfg.frontier_deployment)
            if resolved:
                key, display = resolved
                entry = next((e for e in bucket if e.key == key), None)
            else:
                detail["reason"] = "no single exact pattern"
        else:
            reply = await calls.invoke("embedding", self.cfg.embedding_deployment, {"input": [normalise(text)]})
            if not isinstance(reply.result, list) or not reply.result or not all(
                isinstance(v, (int, float)) and math.isfinite(v) for v in reply.result
            ):
                raise ModelError("Embedding deployment returned an invalid vector.")
            vector = reply.result
            best, score = max(((e, cosine(vector, e.vector or [])) for e in bucket),
                              key=lambda row: row[1], default=(None, 0.0))
            if best and score >= self.cfg.naive_similarity_threshold:
                entry = best
            if best:
                detail["similarity"] = score
            key = hashlib.sha256(normalise(text).encode()).hexdigest()
            display = f"similarity:{key[:8]}"
        if entry:
            entry.hits += 1
            detail |= {"cache_action": "hit", "entry": entry.id, "key": entry.display_key,
                       "answered_by": entry.model}
            result = entry.answer
        else:
            reply = await answer(calls, self.cfg, self.policies, text, timestamp, ticket, cache_prompt=False)
            result = str(reply.result)
            if key is not None and display is not None:
                detail |= {"cache_action": "miss", "key": display}
                if len(bucket) < 40 and parse_answer(result) is not None:
                    entry = Entry(f"#{len(bucket) + 1:02}", key, display, text, result, reply.model, vector)
                    bucket.append(entry)
                    detail |= {"cache_action": "store", "entry": entry.id}
        detail["entries"] = [e.public() for e in bucket]
        return result, detail
