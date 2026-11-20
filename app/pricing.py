"""List-price lookup and Decimal arithmetic for the measured token meters."""

import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

from .usage import Usage

D = Decimal
PER_MILLION = D(1_000_000)
Rate = Annotated[Decimal, Field(ge=0, allow_inf_nan=False)]


class InputRate(BaseModel):
    key: str
    match: list[str] = Field(min_length=1)
    input: Rate | None
    source: str


class ModelRate(InputRate):
    cached_input: Rate | None
    cache_write_30m: Rate | None
    output: Rate | None


class RouterRate(BaseModel):
    routing_fee_per_1M_input: Rate | None
    verified: bool
    source: str


@dataclass(frozen=True)
class Bill:
    cost: Decimal | None
    lines: list[dict[str, Any]]
    missing: list[str]


def usd(cost: Decimal | None) -> str | None:
    return None if cost is None else format(cost, "f")


def percentage(numerator: Decimal | int, denominator: Decimal | int) -> str | None:
    return None if denominator == 0 else usd(D(numerator) * 100 / D(denominator))


class PriceBook(BaseModel):
    currency: Literal["USD"]
    unit: Literal["per_1M_tokens"]
    deployment_type: Literal["GlobalStandard"]
    verified_on: date | None
    cache_write_additive: bool = False
    notes: str = ""
    chat_models: list[ModelRate] = Field(min_length=1)
    router: RouterRate
    decision: InputRate
    embeddings: list[InputRate] = Field(min_length=1)

    @classmethod
    def load(cls, path: Path, override: str = "") -> "PriceBook":
        body = override or path.read_text(encoding="utf-8")
        book = cls.model_validate(json.loads(body, parse_float=Decimal))
        for entries in (book.chat_models, book.embeddings):
            prefixes = [prefix.lower() for entry in entries for prefix in entry.match]
            if "" in prefixes or len(set(prefixes)) != len(prefixes):
                raise ValueError("Price prefixes must be nonempty and unique in each table.")
        return book

    @staticmethod
    def match[T: InputRate](name: str, entries: list[T]) -> T | None:
        matches = [(len(prefix), entry) for entry in entries for prefix in entry.match
                   if name.lower().startswith(prefix.lower())]
        return max(matches, default=(0, None), key=lambda row: row[0])[1]

    def bill(self, kind: str, model: str, usage: Usage) -> Bill:
        parts: list[tuple[str, int, Decimal | None]] = []
        if kind in {"embedding", "decision"}:
            price = self.match(model, self.embeddings if kind == "embedding" else [self.decision])
            parts.append((f"{kind} input", usage.prompt, price.input if price else None))
        elif kind in {"chat", "router"}:
            chat = self.match(model, self.chat_models)
            parts += [
                ("uncached input", usage.uncached, chat.input if chat else None),
                ("cached read", usage.cached, chat.cached_input if chat else None),
                ("cache write (30m)", usage.cache_write, chat.cache_write_30m if chat else None),
                ("output (includes reasoning)", usage.completion, chat.output if chat else None),
            ]
            if self.cache_write_additive:
                parts.append(("ordinary input on written tokens", usage.cache_write, chat.input if chat else None))
            if kind == "router":
                parts.append(("router fee (all input)", usage.prompt, self.router.routing_fee_per_1M_input))
        else:
            raise ValueError(f"Unknown billed call kind: {kind}")
        total, lines, missing = D(0), [], []
        for label, tokens, rate in parts:
            if tokens == 0:
                continue
            cost = None if rate is None else D(tokens) * rate / PER_MILLION
            if cost is None:
                missing.append(f"{model}: {label}")
            else:
                total += cost
            lines.append({"label": label, "tokens": tokens, "per_1m_usd": usd(rate), "cost_usd": usd(cost)})
        return Bill(None if missing else total, lines, missing)
