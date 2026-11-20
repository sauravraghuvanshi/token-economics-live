"""Authentication and rolling spend accounting for a single App Service worker."""

import hmac
import json
import logging
import time
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import HTTPException

from .settings import Settings

LOG = logging.getLogger("tokecon")


def save_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False),
                             encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def authorized(settings: Settings, provided: str | None) -> bool:
    return bool(settings.access_code_configured and provided and len(provided) <= 512
                and hmac.compare_digest(settings.demo_access_code.get_secret_value().encode(), provided.encode()))


def check_access(settings: Settings, provided: str | None) -> None:
    if settings.source == "fake" and not settings.access_code_configured:
        return
    if not settings.access_code_configured:
        raise HTTPException(503, "Demo access code is not configured.")
    if not authorized(settings, provided):
        raise HTTPException(401, "The demo access code is required or incorrect.")


class SpendLimit(RuntimeError):
    pass


class SpendWindow:
    def __init__(self, path: Path, limit: Decimal):
        self.path, self.limit = path, limit
        self.entries: list[tuple[float, str | None]] = []
        self.healthy = True
        try:
            if path.exists():
                self.entries = [(float(at), None if cost is None else str(Decimal(cost)))
                                for at, cost in json.loads(path.read_text(encoding="utf-8"))]
            self.prune()
        except (OSError, ValueError, TypeError, ArithmeticError):
            self.healthy = False
            LOG.error("Spend ledger could not be loaded; live inference is disabled.")

    def prune(self) -> None:
        cutoff = time.time() - 3600
        self.entries = [(at, cost) for at, cost in self.entries if at > cutoff]

    def check(self) -> None:
        if not self.healthy:
            raise SpendLimit("The spend ledger is unavailable; live calls are paused.")
        self.prune()
        total = sum((Decimal(cost) for _, cost in self.entries if cost is not None), start=Decimal(0))
        unknown = sum(cost is None for _, cost in self.entries)
        if total >= self.limit:
            raise SpendLimit("Hourly demo spend limit reached. Try again after the rolling hour.")
        if unknown >= 24:
            raise SpendLimit("Too many unpriced calls this hour. Correct the missing prices or usage first.")

    def record(self, cost: Decimal | None) -> None:
        self.prune()
        self.entries.append((time.time(), str(cost) if cost is not None else None))
        try:
            save_json(self.path, self.entries)
        except OSError:
            self.healthy = False
            LOG.error("Could not save the spend ledger; subsequent live calls are paused.")
