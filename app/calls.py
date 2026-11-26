"""Collect usage, measurements and cost for every paid call, including intermediate roles."""

import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from .foundry import CallContext, ModelClient, ModelError, Reply, with_retry
from .guards import SpendWindow
from .pricing import PriceBook, usd
from .telemetry import model_span
from .usage import Usage

D = Decimal


@dataclass
class CallSession:
    client: ModelClient
    prices: PriceBook
    context: CallContext
    spend: SpendWindow | None
    calls: list[dict[str, Any]] = field(default_factory=list)
    metric_spans: list[tuple[dict[str, Any], int, int]] = field(default_factory=list)

    async def invoke(self, kind: str, deployment: str, body: dict[str, Any],
                     *, context: CallContext | None = None) -> Reply:
        if self.spend is not None:
            self.spend.check()
        start = time.time_ns()
        sent = {"kind": kind, "deployment": deployment, "sent": body}
        try:
            reply, retries = await with_retry(lambda: self.client.invoke(kind, deployment, body,
                                                                         context or self.context))
        except ModelError as error:
            end = time.time_ns()
            self.calls.append({**sent, "error": str(error), "usage": None, "cost_usd": None,
                               "latency_ms": (end - start) // 1_000_000, "transport_retries": error.retries})
            self.metric_spans.append((self.calls[-1], start, end))
            if self.spend is not None and error.status not in {400, 401, 403, 404, 429}:
                self.spend.record(None)
            raise
        end = time.time_ns()
        row = {**sent, "response_model": reply.model, "raw_usage": reply.usage,
               "x_request_id": reply.request_id, "transport_retries": retries,
               "latency_ms": reply.fixture_latency_ms if reply.fixture_latency_ms is not None
               else max(0, (end - start) // 1_000_000)}
        if reply.agent:
            row.update(foundry_agent=reply.agent, foundry_agent_version=reply.agent_version,
                       foundry_response_id=reply.response_id, sent=reply.sent)
        if reply.routing_mode is not None:
            row["routing_mode"] = reply.routing_mode
        try:
            usage = Usage.normalise(kind, reply.usage)
        except ValueError as exc:
            self.calls.append({**row, "error": str(exc), "usage": None, "cost_usd": None})
            self.metric_spans.append((self.calls[-1], start, end))
            if self.spend is not None:
                self.spend.record(None)
            raise ModelError(str(exc)) from None
        bill = self.prices.bill(kind, reply.model, usage)
        self.calls.append({**row, "usage": usage.json(), "cost_usd": usd(bill.cost),
                           "calculation": bill.lines, "missing_prices": bill.missing,
                           "output": reply.result if kind != "embedding" else "Embedding vector omitted"})
        self.metric_spans.append((self.calls[-1], start, end))
        if self.spend is not None:
            self.spend.record(bill.cost)
        return reply

    def role(self, kind: str, output: Any) -> None:
        self.calls.append({"kind": kind, "deployment": None, "response_model": None,
                           "cost_usd": "0", "usage": Usage().json(), "latency_ms": 0,
                           "transport_retries": 0, "x_request_id": None, "output": output})

    @property
    def cost(self) -> Decimal | None:
        if any(call["cost_usd"] is None for call in self.calls):
            return None
        return sum((D(call["cost_usd"]) for call in self.calls), start=D(0))

    @property
    def tokens(self) -> dict[str, int]:
        tokens = Usage().json()
        for call in self.calls:
            if call["usage"] is not None:
                for key in tokens:
                    tokens[key] += call["usage"][key]
        return tokens

    @property
    def live(self) -> bool:
        return self.spend is not None

    def report(self, passed: bool | None) -> None:
        if not self.live:
            return
        ctx = self.context
        for row, start, end in self.metric_spans:
            u = row["usage"] or {}
            model_span({
                "lever": ctx.lever, "mode": ctx.mode, "lane": ctx.lane, "request_id": ctx.request_id,
                "deployment": row["deployment"], "response_model": row.get("response_model"),
                "foundry_agent": row.get("foundry_agent"),
                "foundry_agent_version": row.get("foundry_agent_version"),
                "foundry_response_id": row.get("foundry_response_id"),
                "routing_mode": row.get("routing_mode"),
                "prompt_tokens": u.get("prompt"), "cached_tokens": u.get("cached"),
                "cache_write_tokens": u.get("cache_write"), "completion_tokens": u.get("completion"),
                "reasoning_tokens": u.get("reasoning"), "cost_usd": row["cost_usd"], "pass": passed,
                "latency_ms": row["latency_ms"], "transport_retries": row["transport_retries"],
                "kind": row["kind"], "error": bool(row.get("error")),
            }, start, end)
