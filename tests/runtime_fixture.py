"""Immutable, session-only runtime prerequisite for explicitly audited queue fixtures.

This is not a production memoizer. The selected installation is read-only for the
session; a fresh inventory at teardown checks that precondition. Only fixture plan
capture may use the seed. Validators, real workers and explicit alternate sites
always inventory actual bytes. No mutable plans, queues or outputs are retained.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import sys
import sysconfig
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from packaging.markers import default_environment

from xlm.artifacts import manifest, store
from xlm.experiments import environment, execution
from xlm.experiments.plans import ExecutablePlan, freeze_execution


def _inputs(lock: Path, extras: list[str]) -> bytes:
    """All explicit capture inputs, plus the identity of the immutable producer.

    Installed content is bound separately by the seed's full real inventory hash.
    Locations are included conservatively even though runtime identity normalizes
    them. Environment-changing tests are deliberately outside the consumer list.
    """
    files = [lock, lock.parent / "pyproject.toml", lock.parent / ".python-version"]
    producers = []
    for module in (environment, manifest, store):
        if module.__file__ is None:
            raise RuntimeError("runtime fixture producer has no source file")
        producers.append(Path(module.__file__))
    value = {
        "fixture_version": 1,
        "contracts": [store.compute_file_sha256(p, max_bytes=8 * 1024**2) for p in files],
        "producer": [store.compute_file_sha256(p, max_bytes=8 * 1024**2) for p in producers],
        "extras": sorted(extras),
        "site": str(Path(sysconfig.get_paths()["purelib"]).resolve()),
        "interpreters": [
            [str(p), store.compute_file_sha256(p, max_bytes=256 * 1024**2)]
            for p in (Path(sys.executable), Path(getattr(sys, "_base_executable", sys.executable)))
        ],
        "markers": default_environment(),
        "implementation": platform.python_implementation(),
        "processor": platform.processor(),
        "os_version": platform.version(),
        "os_name": os.name,
    }
    return manifest.canonical_json(value)


@dataclass(frozen=True)
class RuntimeSeed:
    inputs: bytes
    payload: bytes
    key: str

    @classmethod
    def capture(cls, lock: Path) -> RuntimeSeed:
        inputs = _inputs(lock, ["cpu"])
        payload = manifest.canonical_json(environment.installed_runtime(lock, ["cpu"]))
        if _inputs(lock, ["cpu"]) != inputs:
            raise RuntimeError("runtime fixture inputs changed during capture")
        key = hashlib.sha256(b"queue-runtime-seed-v1\0" + inputs + payload).hexdigest()
        return cls(inputs, payload, key)

    def matches(self, lock: Path, extras: list[str]) -> bool:
        try:
            return self.inputs == _inputs(lock, extras)
        except (OSError, ValueError):
            return False  # The real inventory below supplies its original failure.

    def inventory(self, lock: Path, extras: list[str], site: Path | None = None) -> dict[str, Any]:
        if site is not None or not self.matches(lock, extras):
            return environment.installed_runtime(lock, extras, site)
        value: dict[str, Any] = json.loads(self.payload)
        return value  # A new nested dictionary, never the cached object.

    def freeze(
        self, plan: ExecutablePlan, snapshot: Path, extras: list[str]
    ) -> tuple[ExecutablePlan, bool]:
        if vars(execution)[
            "installed_runtime"
        ] is not environment.installed_runtime or not self.matches(
            snapshot / "code/uv.lock", extras
        ):
            return freeze_execution(plan, snapshot, extras), False
        # This context ends before admission/execution, including the cancellation
        # test's background thread. Only the fixture's real freeze is in scope.
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(execution, "installed_runtime", self.inventory)
            return freeze_execution(plan, snapshot, extras), True

    def verify_unchanged(self, lock: Path) -> None:
        fresh = RuntimeSeed.capture(lock)
        if fresh != self:
            raise RuntimeError("installed runtime changed during immutable fixture session")


# Explicit opt-in: these tests exercise queue behavior, not environment discovery.
# Unknown tests, CPU/CUDA capability refusal, all frozen/environment/compiler tests
# and subprocess CLI paths keep their original capture and verification behavior.
RUNTIME_FIXTURE_NODES = frozenset(
    f"tests/test_queue.py::{name}"
    for name in (
        "test_submit_deduplicates_identical_plans",
        "test_submit_refuses_blocked_plans_and_missing_snapshots",
        "test_submit_allows_explicit_duplicates",
        "test_toy_campaign_runs_sequentially_to_success",
        "test_changed_live_tree_preserves_frozen_run",
        "test_failed_jobs_keep_evidence_and_stay_terminal",
        "test_crash_recovery_requeues_once_when_retries_remain",
        "test_crash_without_retries_fails_loudly",
        "test_cancel_stops_a_queued_job_and_a_running_job",
        "test_status_lists_jobs_with_attempts",
    )
) | frozenset({"tests/test_reports.py::test_cli_runs_list_empty_and_populated"})
