"""Run registry and ordered two-lane orchestration."""

import asyncio
import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from .calls import CallSession
from .dataset import Dataset, Invoice, Request
from .foundry import CallContext, ModelClient, ModelError
from .grading import grade, parse_answer
from .guards import SpendLimit, SpendWindow
from .kpis import kpis
from .levers.model_routing import route
from .levers.prompt_caching import answer
from .levers.response_cache import ResponseCache
from .levers.retry_killswitch import document
from .pricing import PriceBook, usd
from .recordings import Recordings
from .settings import Settings
from .telemetry import completed

LOG = logging.getLogger("tokecon")

TABS: dict[str, dict[str, Any]] = {
    "prompt_caching": {
        "title": "Prompt caching", "subtitle": "Same content, same tokens. Only the order changes.",
        "modes": {"default": "Policies first"},
    },
    "model_routing": {
        "title": "Model routing", "subtitle": "Send routine work to a cheaper model; keep hard cases on the frontier.",
        "modes": {"foundry_router": "Foundry model router", "decision_router": "Decision router (pre-RAG)",
                  "small_all": "Small model for all · trap"},
    },
    "response_cache": {
        "title": "Response caching", "subtitle": "A hit skips the whole model call, but only when the key is exact.",
        "modes": {"fact_guarded": "Fact-guarded cache", "naive": "Naive similarity · trap"},
    },
    "retry_killswitch": {
        "title": "Retry & kill switch", "subtitle": "Keep retries that fix real errors; stop loops that never change.",
        "modes": {"guardrails": "New evidence + guardrails", "no_retries": "No retries",
                  "tight": "Tight kill switch"},
    },
}


def now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class Run:
    lever: str
    mode: str
    count: int
    source: str
    id: str = field(default_factory=lambda: str(uuid4()))
    started_at: str = field(default_factory=now)
    events: list[dict[str, Any]] = field(default_factory=list)
    results: list[dict[str, Any]] = field(default_factory=list)
    summary: dict[str, Any] | None = None
    done: bool = False
    recorded_at: str | None = None
    recording_source: str | None = None
    started_clock: float = field(default_factory=time.monotonic)
    wake: asyncio.Event = field(default_factory=asyncio.Event)

    def emit(self, event: str, data: dict[str, Any]) -> None:
        payload = {**data, "run_id": self.id}
        self.events.append({
            "event": event, "at_ms": round((time.monotonic() - self.started_clock) * 1000), "data": payload,
        })
        self.wake.set()
        self.wake = asyncio.Event()

    def finish(self) -> None:
        self.done = True
        self.wake.set()

    def snapshot(self) -> dict[str, Any]:
        return {"run_id": self.id, "lever": self.lever, "mode": self.mode, "count": self.count,
                "source": self.source, "started_at": self.started_at, "summary": self.summary,
                "done": self.done, "results": self.results, "recorded_at": self.recorded_at,
                "recording_source": self.recording_source}


class Demo:
    def __init__(self, settings: Settings, data: Dataset, prices: PriceBook, client: ModelClient | None):
        self.settings, self.data, self.prices, self.client = settings, data, prices, client
        self.spend = SpendWindow(settings.data_dir / "spend.json", settings.max_spend_per_hour_usd)
        self.cache = ResponseCache(settings, data.policies, data.patterns)
        self.recordings = Recordings(settings)
        self.registry: dict[str, Run] = {}
        self.active: asyncio.Task | None = None
        self.active_run_id: str | None = None
        self.missing: set[str] = set()

    @property
    def busy(self) -> bool:
        return self.active is not None and not self.active.done()

    @property
    def prices_verified(self) -> bool:
        return self.settings.source != "fake" and self.prices.verified_on is not None and self.prices.router.verified

    def track(self, run: Run) -> None:
        self.registry[run.id] = run
        if len(self.registry) > 24:
            oldest = next(iter(self.registry))
            if self.registry[oldest].done:
                del self.registry[oldest]

    def workload(self, lever: str) -> Sequence[Request | Invoice]:
        return self.data.invoices if lever == "retry_killswitch" else self.data.requests[lever]

    def start(self, lever: str, mode: str, count: int) -> Run:
        run = Run(lever, mode, count, self.settings.source)
        self.track(run)
        self.active_run_id = run.id
        self.active = asyncio.create_task(self.run(run))
        return run

    def start_replay(self, recording: dict[str, Any], speed: int = 1) -> Run:
        run = Run(recording["lever"], recording["mode"], recording["count"], "replay",
                  recorded_at=recording["started_at"], recording_source=recording["source"])
        self.track(run)
        self.active_run_id = run.id
        self.active = asyncio.create_task(self.replay(run, recording, speed))
        return run

    async def replay(self, run: Run, recording: dict[str, Any], speed: int) -> None:
        try:
            for item in recording["events"]:
                target = item["at_ms"] / 1000 / speed
                await asyncio.sleep(max(0, target - (time.monotonic() - run.started_clock)))
                data = item["data"]
                if item["event"] == "run_started":
                    data = {**data, "source": "replay", "replay": True, "recorded_at": run.recorded_at,
                            "recording_source": run.recording_source}
                elif item["event"] == "lane_result":
                    run.results.append({**data, "run_id": run.id})
                elif item["event"] == "run_completed":
                    run.summary = data["summary"]
                    self.write_board(run)
                    data = {**data, "saved_replay": False, "replay": True}
                run.emit(item["event"], data)
        except (OSError, ValueError, KeyError, TypeError):
            LOG.error("Could not play recording for %s / %s.", run.lever, run.mode)
            run.emit("run_error", {"message": "The recording could not be played.", "fatal": True})
        finally:
            run.finish()

    def write_board(self, run: Run) -> None:
        try:
            self.recordings.update_scoreboard({
                "lever": run.lever, "mode": run.mode, "count": run.count, "source": run.source,
                "results_source": run.recording_source or run.source, "started_at": run.started_at,
                "recorded_at": run.recorded_at, "summary": run.summary,
            })
        except OSError:
            LOG.error("Could not save scoreboard for run %s.", run.id)
            run.emit("run_error", {"message": "The run finished, but the scoreboard could not be saved.",
                                   "fatal": False})

    def session(self, ctx: CallContext) -> CallSession:
        if self.client is None:
            raise ModelError("The live client is not available.")
        return CallSession(self.client, self.prices, ctx, self.spend if self.settings.source == "live" else None)

    async def lane(self, run: Run, row: Request | Invoice, index: int, lane: str) -> dict[str, Any]:
        calls = self.session(CallContext(run.lever, run.mode, lane, row.id, index))
        detail: dict[str, Any] = {}
        reasons: list[str] = []
        preview = ""
        status = "ok"
        try:
            detail, preview, reasons = await self.process(run, row, calls)
        except (ModelError, SpendLimit) as error:
            status, reasons = "error", [str(error)]
        missing = [item for call in calls.calls for item in call.get("missing_prices", [])]
        if missing:
            self.missing.update(missing)
            reasons += [f"price missing: {item}" for item in missing]
            if status == "ok":
                status = "price_missing"
        passed = not [reason for reason in reasons if not reason.startswith("price missing: ")]
        calls.report(passed)
        return {
            "run_id": run.id, "idx": index + 1, "request_id": row.id, "lane": lane, "status": status,
            "calls": calls.calls, "totals": {"cost_usd": usd(calls.cost) if status == "ok" else None,
                                            "latency_ms": sum(c["latency_ms"] for c in calls.calls),
                                            "tokens": calls.tokens},
            "pass": passed, "miss_reasons": reasons, "lever_detail": detail,
            "answer_preview": preview,
        }

    async def process(self, run: Run, row: Request | Invoice,
                      calls: CallSession) -> tuple[dict[str, Any], str, list[str]]:
        if isinstance(row, Invoice):
            return await document(calls, self.settings, row)
        ticket = f"{run.id[:8]}-{row.id}"
        detail: dict[str, Any] = {}
        if run.lever == "model_routing":
            reply, detail = await route(calls, self.settings, self.data.policies, row.text, run.started_at, ticket)
            detail["expected_tier"] = row.tier
            text = str(reply.result)
        elif run.lever == "response_cache" and calls.context.lane == "candidate":
            text, detail = await self.cache.lookup(calls, row.text, run.started_at, ticket)
        else:
            reply = await answer(calls, self.settings, self.data.policies, row.text, run.started_at, ticket,
                                 policies_first=run.lever != "prompt_caching" or calls.context.lane == "candidate")
            text = str(reply.result)
        parsed = parse_answer(text)
        reasons = grade(text, row.expect)
        if detail.get("cache_action") == "hit":
            detail["wrong_hit"] = bool(reasons)
        return detail, parsed.answer if parsed else text, reasons

    async def run(self, run: Run) -> None:
        try:
            if run.lever == "response_cache":
                self.cache.clear(run.mode)
            run.emit("run_started", {"lever": run.lever, "mode": run.mode, "count": run.count,
                                     "started_at": run.started_at, "source": run.source,
                                     "prices_verified": self.prices_verified})
            for index, row in enumerate(self.workload(run.lever)[:run.count]):
                for lane in ("control", "candidate"):
                    run.emit("request_started", {"idx": index + 1, "request_id": row.id, "lane": lane,
                                                 "text": row.text if isinstance(row, Request) else row.title})
                async with asyncio.TaskGroup() as group:
                    tasks = [group.create_task(self.lane(run, row, index, lane)) for lane in
                             ("control", "candidate")]
                    for future in asyncio.as_completed(tasks):
                        result = await future
                        run.results.append(result)
                        run.emit("lane_result", result)
                run.emit("kpis", kpis(run.results))
            run.summary = {**kpis(run.results), "elapsed_ms": round((time.monotonic() - run.started_clock) * 1000)}
            saved = False
            if run.summary["complete"]:
                final_event = {"event": "run_completed", "at_ms": run.summary["elapsed_ms"],
                               "data": {"run_id": run.id, "summary": run.summary, "saved_replay": True}}
                try:
                    self.recordings.save({
                        "schema": 1, "lever": run.lever, "mode": run.mode, "count": run.count,
                        "source": run.source, "started_at": run.started_at, "summary": run.summary,
                        "events": [*run.events, final_event],
                    })
                    saved = True
                except OSError:
                    LOG.error("Could not save replay for run %s.", run.id)
                    run.emit("run_error", {"message": "The run finished, but the recording could not be saved.",
                                           "fatal": False})
            self.write_board(run)
            if run.source == "live":
                completed({
                    "lever": run.lever, "mode": run.mode, "run_id": run.id, "count": run.count,
                    "control_cost_usd": run.summary["control"]["cost_usd"] or "incomplete",
                    "candidate_cost_usd": run.summary["candidate"]["cost_usd"] or "incomplete",
                    "control_accepted": run.summary["control"]["accepted"],
                    "candidate_accepted": run.summary["candidate"]["accepted"],
                    "elapsed_ms": run.summary["elapsed_ms"], "complete": run.summary["complete"],
                })
            run.emit("run_completed", {"summary": run.summary, "saved_replay": saved})
        except Exception as error:
            LOG.error("Run %s failed: %s", run.id, type(error).__name__)
            run.emit("run_error", {"message": "The run could not complete. Check the service log.", "fatal": True})
        finally:
            run.finish()
