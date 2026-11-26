"""Deterministic, explicitly synthetic model responses. No cloud clients are created here."""

import asyncio
import json
from pathlib import Path
from typing import Any

from .foundry import CallContext, Reply
from .settings import ROOT, Settings

FIXTURES = ROOT / "tests" / "fixtures"


class FakeClient:
    def __init__(self, settings: Settings, fixtures: Path = FIXTURES):
        self.settings = settings
        self.answers: dict[str, Any] = json.loads((fixtures / "answers.json").read_text(encoding="utf-8"))
        self.samples: dict[str, Any] = json.loads((fixtures / "usage.json").read_text(encoding="utf-8"))
        self.invoices: dict[str, Any] = json.loads((fixtures / "invoices.json").read_text(encoding="utf-8"))

    async def invoke(self, kind: str, deployment: str, body: dict[str, Any], ctx: CallContext) -> Reply:
        await asyncio.sleep(0.011 if ctx.lane == "control" else 0.007)
        small = deployment == self.settings.small_deployment
        model = "gpt-4.1-mini" if small else "gpt-6.1-sol"
        request = ctx.request_id
        if kind == "embedding":
            vector = self.samples["vectors"].get(request, [0, 0, 0, 0, 0, 1])
            return Reply("text-embedding-3-small", vector, self.samples["embedding"], fixture_latency_ms=21)
        if kind == "decision":
            answers: dict[str, Any]
            if "tier" in body["questions"]:
                tier = "frontier" if request in {"mr-03", "mr-05", "mr-07", "mr-09"} else "small"
                answers = {"tier": {"type": "choice", "choice": tier,
                                    "probabilities": {tier: 0.98, ("small" if tier == "frontier"
                                                                    else "frontier"): 0.02}}}
            else:
                answers = {key: {"type": "noul", "noul": 0.98} for key in body["questions"]}
            return Reply("microsoft-decision-1.1", answers, self.samples["decision"], fixture_latency_ms=34)
        schema = body.get("response_format", {}).get("json_schema", {}).get("name")
        routing = body.get("extra_body", {}).get("routing_config")
        if kind == "router" and routing:
            model = routing["models"][-1]
            small = model == "gpt-4.1-mini"
        if schema == "invoice":
            fixture = self.invoices[request]
            value = fixture["corrected"] if ctx.attempt > 1 and fixture.get("corrected") else fixture["first"]
            return Reply(model, json.dumps(value), self.samples["extraction"], fixture_latency_ms=463)
        if schema == "invoice_review":
            return Reply(model, '{"accepted":"yes"}', self.samples["review"], fixture_latency_ms=215)
        if schema != "support_answer":
            return Reply(model, "OK", self.samples["probe"], fixture_latency_ms=25)
        answer = self.answers.get(request, {
            "answer": "This fixture does not cover that question.", "policy_ids": [], "decision": "escalate",
        })
        if small and request in {"mr-03", "mr-05", "mr-07", "mr-09"}:
            answer = {"answer": "Please contact support.", "policy_ids": [], "decision": "escalate"}
        cache_key = body.get("extra_body", {}).get("prompt_cache_key")
        if cache_key and ctx.index > 0:
            sample = "small_read" if small else "read"
        elif cache_key and not small and "prompt_cache_options" in body["extra_body"]:
            sample = "write"
        else:
            sample = "small" if small else "uncached"
        return Reply(model, json.dumps(answer, ensure_ascii=False), self.samples[sample],
                     fixture_latency_ms=385 if small else (714 if sample == "read" else 1180),
                     routing_mode=routing["mode"] if kind == "router" and routing else None)

    async def close(self) -> None:
        pass
