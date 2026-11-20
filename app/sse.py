"""Resumable server-sent events without a polling or buffering dependency."""

import json
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .runs import Run


async def stream(run: "Run", cursor: int = 0, heartbeat: float = 15) -> AsyncIterator[str]:
    while True:
        while cursor < len(run.events):
            item = run.events[cursor]
            cursor += 1
            payload = json.dumps(item["data"], ensure_ascii=False, separators=(",", ":"))
            yield f"id: {cursor}\nevent: {item['event']}\ndata: {payload}\n\n"
        if run.done:
            break
        try:
            import asyncio

            await asyncio.wait_for(run.wake.wait(), timeout=heartbeat)
        except TimeoutError:
            yield ": ping\n\n"
