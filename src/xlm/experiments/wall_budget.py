"""A job's total wall-clock allowance, persisted across queue attempts (P35 M3).

``resources.total_wall_seconds`` bounds the whole job: worker startup,
construction, training, checkpoints, evaluation and every recovery attempt.
It is not the trainer's per-attempt ``max_train_seconds``. The queue runner
measures elapsed time around each worker launch, persists the consumed total
while the worker runs (so a crashed runner still leaves an accurate record)
and gives a resumed attempt only the remaining allowance. An allowance that
expires before the exact budget is committed makes the job INCOMPLETE; it is
never reset and never reported as success.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from xlm.experiments.execution import read_json, write_json

ALLOWANCE_VERSION = "xlm-wall-allowance-v1"
ALLOWANCE_FILE = "wall_allowance.json"
INCOMPLETE = "INCOMPLETE"
MAX_ATTEMPTS = 256
#: A crashed runner can under-record at most about this much of its last attempt.
PERSIST_EVERY_SECONDS = 1.0


def _monotonic() -> float:
    """The attempt clock (a seam so tests can advance time without sleeping)."""
    return time.monotonic()


class WallAllowanceError(RuntimeError):
    """The persisted allowance is missing, inconsistent or already exhausted."""


class WallAllowanceExpired(WallAllowanceError):
    """No wall time remains; the job cannot start or continue."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def declared_total_seconds(resources: Any) -> float | None:
    """The plan's total job allowance, or ``None`` when the plan declares none."""
    if not isinstance(resources, dict) or resources.get("total_wall_seconds") is None:
        return None
    value = resources["total_wall_seconds"]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise WallAllowanceError("resources.total_wall_seconds must be a number")
    total = float(value)
    if not math.isfinite(total) or total <= 0:
        raise WallAllowanceError("resources.total_wall_seconds must be positive and finite")
    return total


@dataclass
class WallAllowance:
    """The persisted allowance of one queue job, keyed to its plan hash."""

    path: Path
    plan_hash: str
    total_seconds: float
    consumed_seconds: float
    attempts: list[dict[str, Any]]
    status: str

    @property
    def remaining_seconds(self) -> float:
        return max(0.0, self.total_seconds - self.consumed_seconds)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": ALLOWANCE_VERSION,
            "plan_hash": self.plan_hash,
            "total_seconds": self.total_seconds,
            "consumed_seconds": self.consumed_seconds,
            "remaining_seconds": self.remaining_seconds,
            "status": self.status,
            "attempts": [dict(a) for a in self.attempts],
        }

    def save(self) -> None:
        write_json(self.path, self.to_dict())

    @classmethod
    def load_or_create(
        cls, work_dir: Path, *, plan_hash: str, total_seconds: float
    ) -> WallAllowance:
        path = work_dir / ALLOWANCE_FILE
        if not path.is_file():
            allowance = cls(path, plan_hash, total_seconds, 0.0, [], "active")
            allowance.save()
            return allowance
        saved = read_json(path)
        if saved.get("version") != ALLOWANCE_VERSION:
            raise WallAllowanceError("unsupported wall allowance record")
        if saved.get("plan_hash") != plan_hash or float(saved["total_seconds"]) != total_seconds:
            raise WallAllowanceError("wall allowance belongs to a different plan or total")
        consumed = float(saved["consumed_seconds"])
        if not math.isfinite(consumed) or consumed < 0:
            raise WallAllowanceError("persisted consumed wall time is invalid")
        attempts = [dict(a) for a in saved.get("attempts", [])]
        if len(attempts) > MAX_ATTEMPTS:
            raise WallAllowanceError("wall allowance attempt log exceeds its bound")
        for attempt in attempts:
            if attempt.get("outcome") == "running":
                # The runner died mid-attempt: its last persisted consumption stands.
                attempt["outcome"] = "interrupted_runner_lost"
        return cls(path, plan_hash, total_seconds, consumed, attempts, str(saved["status"]))


class AttemptClock:
    """Measures one worker attempt and persists its consumption as it happens."""

    def __init__(self, allowance: WallAllowance, attempt: int) -> None:
        if allowance.remaining_seconds <= 0:
            allowance.status = "expired"
            allowance.save()
            raise WallAllowanceExpired(
                f"total wall allowance of {allowance.total_seconds:.0f}s is exhausted "
                f"({allowance.consumed_seconds:.1f}s consumed); the job is {INCOMPLETE}"
            )
        self.allowance = allowance
        self.base = allowance.consumed_seconds
        self.limit = allowance.remaining_seconds
        self.started = _monotonic()
        self.last_persist = 0.0
        self.entry: dict[str, Any] = {
            "attempt": attempt,
            "started_at": _now(),
            "remaining_at_start": self.limit,
            "seconds": 0.0,
            "outcome": "running",
        }
        allowance.attempts.append(self.entry)
        allowance.save()

    def elapsed(self) -> float:
        return _monotonic() - self.started

    def tick(self) -> None:
        """Persist consumption periodically so a crash cannot hand back spent time."""
        now = _monotonic()
        if now - self.last_persist < PERSIST_EVERY_SECONDS:
            return
        self.last_persist = now
        self._record(self.elapsed())

    def _record(self, seconds: float) -> None:
        self.entry["seconds"] = seconds
        self.allowance.consumed_seconds = self.base + seconds
        self.allowance.save()

    def finish(self, outcome: str) -> None:
        self.entry["ended_at"] = _now()
        self.entry["outcome"] = outcome
        self._record(self.elapsed())
        if outcome == "succeeded":
            self.allowance.status = "completed"
        elif self.allowance.remaining_seconds <= 0:
            self.allowance.status = "expired"
        self.allowance.save()
