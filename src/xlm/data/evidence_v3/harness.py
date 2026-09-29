"""Synthetic execution harness (disposable roots only; never the frozen root).

A harness is the ONLY way to substitute anything in the Phase-P boundary:
root location, authored synthetic plans, a transport, a clock and a
memory reader. The executor refuses a harness whose root is (or lies
under) the frozen execution root's parent project directory, and refuses a
harness carrying the live network transport. REAL mode takes no harness
and constructs every dependency itself. Everything else — authorization
and approval parsing, runtime environment and code identity probing,
genesis, journal, reservations, containment, streaming accounting,
identity checks, supervision — is the same code in both modes.
"""

from __future__ import annotations

import datetime
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, TypedDict

from xlm.data.evidence_v3 import transport


class Clock(Protocol):
    def monotonic_ns(self) -> int: ...

    def sleep(self, seconds: float) -> None: ...

    def utc_now(self) -> str: ...


class RealClock:
    def monotonic_ns(self) -> int:
        return time.monotonic_ns()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)

    def utc_now(self) -> str:
        return datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class EpochPaths(TypedDict):
    """The only inputs the public Phase-P API accepts (besides a harness)."""

    repo_root: Path
    root: Path
    authorization_path: Path
    approval_path: Path
    review_path: Path


@dataclass(frozen=True)
class SyntheticHarness:
    """Substitutions for a disposable synthetic epoch."""

    root: Path
    plan_m_bytes: bytes
    plan_t_bytes: bytes
    transport: transport.Transport
    implementation_commit: str
    clock: Clock
    memory_reader: Callable[[], int] | None = None
    sampling_interval_s: float = 0.1
