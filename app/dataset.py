"""Load and check the fixed, synthetic CloudDesk fixtures once per app instance."""

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .settings import ROOT

POLICY_IDS = ("BILL-1", "BILL-2", "BILL-3", "SEAT-1", "SEAT-2", "DATA-1", "DATA-2",
              "SEC-1", "SEC-2", "SLA-1", "AI-1")
DECISIONS = ("inform", "approve", "partial", "refuse", "escalate")
SUPPORT_LEVERS = ("prompt_caching", "model_routing", "response_cache")
WORKLOAD_VERSION = "english-foundry-v6.4"


class Expectations(BaseModel):
    model_config = ConfigDict(extra="forbid")
    policies: list[str]
    must: list[str] = Field(default_factory=list)
    must_not: list[str] = Field(default_factory=list)
    decision: list[str] | None = None


class Request(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    lang: Literal["en"]
    text: str = Field(min_length=1)
    category: Literal["routine", "multi-policy", "trap", "deep-reasoning"] = "routine"
    tier: Literal["small", "frontier"] | None = None
    expect: Expectations
    guarded: str | None = None
    naive: str | None = None


class InvoiceExpectation(BaseModel):
    currency: Literal["INR", "USD", "EUR"]
    gstin: str | None = None
    total: int
    outcome: str


class Invoice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    title: str
    kind: Literal["clean", "recoverable", "runaway"]
    ocr_text: str
    expected: InvoiceExpectation


class CachePattern(BaseModel):
    topic: str
    match_all: list[list[str]] = Field(min_length=1)
    params: dict[str, dict[str, list[str]]]


class CachePatterns(BaseModel):
    version: str
    product: Literal["clouddesk"]
    strip_phrases: list[str]
    patterns: list[CachePattern] = Field(min_length=1)


@dataclass(frozen=True)
class Dataset:
    policies: str
    requests: dict[str, list[Request]]
    invoices: list[Invoice]
    patterns: CachePatterns
    prefix_layout: dict[str, int]


def load_dataset(directory: Path = ROOT / "app" / "data") -> Dataset:
    policies = (directory / "policies.md").read_bytes().decode("utf-8")
    if set(re.findall(r"^## ([A-Z]+-\d+) ", policies, re.M)) != set(POLICY_IDS):
        raise ValueError("Policy corpus does not match the support answer schema.")
    if "\r" in policies:
        raise ValueError("Policy corpus must use LF line endings.")

    requests: dict[str, list[Request]] = {}
    for lever, prefix in zip(SUPPORT_LEVERS, ("pc", "mr", "rc"), strict=True):
        filename = f"requests_{lever}.json"
        rows = [Request.model_validate(row) for row in json.loads(
            (directory / filename).read_text(encoding="utf-8")
        )]
        if [row.id for row in rows] != [f"{prefix}-{i:02}" for i in range(1, 11)]:
            raise ValueError(f"{filename} must contain ten distinct requests in order.")
        for row in rows:
            if set(row.expect.policies) - set(POLICY_IDS):
                raise ValueError(f"{row.id} references an unknown policy.")
            if set(row.expect.decision or []) - set(DECISIONS):
                raise ValueError(f"{row.id} references an unknown decision.")
            for pattern in row.expect.must + row.expect.must_not:
                re.compile(pattern, re.IGNORECASE)
        requests[lever] = rows

    invoices = [Invoice.model_validate(row) for row in json.loads(
        (directory / "invoices.json").read_text(encoding="utf-8")
    )]
    if [row.id for row in invoices] != [f"inv-{i:02}" for i in range(1, 7)]:
        raise ValueError("invoices.json must contain six distinct documents in order.")
    patterns = CachePatterns.model_validate_json((directory / "cache_patterns.json").read_bytes())
    if len({pattern.topic for pattern in patterns.patterns}) != len(patterns.patterns):
        raise ValueError("Cache topics must be unique.")
    layout = json.loads((directory / "prefix_layout.json").read_text(encoding="utf-8"))
    if set(layout) != {"system", "policies", "customer", "prefix"} or any(
        type(value) is not int or value < 1 for value in layout.values()
    ) or layout["prefix"] < 1024:
        raise ValueError("Invalid measured prefix layout.")
    return Dataset(policies, requests, invoices, patterns, layout)
