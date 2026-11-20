"""Foundry probe snapshot. Anonymous polling never incurs inference spend."""

import asyncio
import time
from typing import Any

from .foundry import CallContext, ModelError
from .guards import SpendLimit
from .runs import Demo


class StatusProbe:
    def __init__(self, demo: Demo, problem: str | None):
        self.demo = demo
        self.problem = problem
        self.checked = -1e9
        self.deployments: dict[str, str] = {}
        self.detail = problem or "Checking Foundry deployments."
        self.task: asyncio.Task | None = None

    def refresh(self) -> None:
        if self.problem or self.demo.settings.source != "live" or self.demo.busy:
            return
        if self.task and not self.task.done() or time.monotonic() - self.checked < 60:
            return
        self.task = asyncio.create_task(self.check())

    async def check(self) -> None:
        async def probe(role: str, name: str) -> tuple[str, str, str]:
            if not name and role == "decision":
                return ("Decision fallback", "ok", "")
            ctx = CallContext("status", "probe", role, "status")
            calls = self.demo.session(ctx)
            kind = role if role in {"decision", "embedding", "router"} else "chat"
            body: dict[str, Any]
            if kind == "decision":
                body = {"state": "OK", "questions": {"ready": {"type": "noul",
                                                               "instructions": "Does the state say OK?"}}}
            elif kind == "embedding":
                body = {"input": ["ok"]}
            else:
                body = {"messages": [{"role": "user", "content": "Reply OK."}], "max_completion_tokens": 16}
            try:
                await calls.invoke(kind, name, body)
                self.demo.missing.update(m for c in calls.calls for m in c.get("missing_prices", []))
                calls.report(None)
                return (name, "ok", "")
            except (ModelError, SpendLimit) as error:
                calls.report(None)
                return (name, "error", str(error))

        results = await asyncio.gather(*(probe(role, name) for role, name in
                                         self.demo.settings.deployments.items()))
        self.checked = time.monotonic()
        self.deployments = {name: status for name, status, _ in results}
        self.detail = next((f"{name}: {detail}" for name, status, detail in results if status == "error"),
                           "All configured deployments responded.")

    def payload(self) -> dict[str, Any]:
        mode = self.demo.settings.source
        detail = self.detail
        if mode == "fake":
            detail = "Synthetic data and fixture prices. No Azure calls."
        elif mode == "replay-only":
            detail = "Offline playback. No Azure calls."
        ok = bool(self.deployments) and all(v == "ok" for v in self.deployments.values())
        return {
            "mode": mode, "foundry": "ok" if ok else "error", "detail": detail, "deployments": self.deployments,
            "prices_verified": self.demo.prices_verified, "verified_on": self.demo.prices.verified_on,
            "missing_prices": sorted(self.demo.missing),
        }
