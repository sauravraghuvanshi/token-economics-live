"""Persist complete runs and replay the recorded stream without a model client."""

import json
import logging
from pathlib import Path
from typing import Any

from .dataset import WORKLOAD_VERSION
from .guards import save_json
from .settings import ROOT, Settings

LOG = logging.getLogger("tokecon")


class Recordings:
    def __init__(self, cfg: Settings):
        self.cfg = cfg
        self.board = cfg.data_dir / "scoreboard.json"

    def path(self, lever: str, mode: str, suffix: str = "") -> Path:
        return self.cfg.recordings / f"{lever}-{mode}{suffix}.json"

    def read(self, lever: str, mode: str) -> dict[str, Any] | None:
        path = self.path(lever, mode)
        fake = self.path(lever, mode, ".fake")
        committed = ROOT / "replays" / path.name
        options = [fake, path, committed] if self.cfg.source == "fake" else [path, committed]
        for candidate in options:
            if not candidate.is_file():
                continue
            try:
                data = json.loads(candidate.read_text(encoding="utf-8"))
                if data.get("workload_version") != WORKLOAD_VERSION:
                    continue
                if (data["schema"] != 1 or data["lever"] != lever or data["mode"] != mode
                        or data["source"] not in {"live", "fake"} or not data["summary"]["complete"]
                        or data["events"][0]["event"] != "run_started"
                        or data["events"][-1]["event"] != "run_completed"):
                    raise ValueError("Invalid recorded run")
                return data
            except (OSError, ValueError, KeyError, TypeError, IndexError):
                LOG.warning("Invalid recording for %s / %s; trying fallback.", lever, mode)
        return None

    def save(self, payload: dict[str, Any]) -> None:
        suffix = ".fake" if payload["source"] == "fake" else ""
        save_json(self.path(payload["lever"], payload["mode"], suffix), payload)

    def scoreboard(self) -> list[dict[str, Any]]:
        try:
            if not self.board.exists():
                return []
            return [row for row in json.loads(self.board.read_text(encoding="utf-8"))
                    if row.get("workload_version") == WORKLOAD_VERSION]
        except (OSError, ValueError):
            LOG.error("The scoreboard could not be loaded.")
            return []

    def update_scoreboard(self, row: dict[str, Any]) -> None:
        old = [other for other in self.scoreboard()
               if (other["lever"], other["mode"]) != (row["lever"], row["mode"])]
        save_json(self.board, [row, *old])
