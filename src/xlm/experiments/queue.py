"""Bounded local queue: leases, sequential runner, recovery and retries (P16, A28).

One GPU lease per device is enforced through a lock-guarded lease file, so two
jobs can never hold the same accelerator. Stale leases (crashed holders) are
reclaimable after a heartbeat timeout, and the theft is recorded. CPU
preparation runs in a bounded worker pool. The runner executes the captured
snapshot in a verified fresh process, records heartbeats, honors
cancellation, and never launches a duplicate: crash recovery requeues only when
the job's explicit retry policy permits it, and every attempt keeps its failure
evidence. Queue code is generic over the resolved plan; it contains no
architecture or dataset special cases.
"""

from __future__ import annotations

import json
import sqlite3
import time
import traceback
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from filelock import FileLock, Timeout

from xlm.artifacts.ledger import RunLedger
from xlm.core.contracts import RunStatus
from xlm.core.paths import ArtifactPaths
from xlm.experiments.plans import ExecutablePlan


class Executor(Protocol):
    """Signature for plan execution functions used by the runner."""

    def __call__(
        self,
        plan: ExecutablePlan,
        job: QueueJob,
        should_cancel: Callable[[], bool],
        *,
        lease_manager: GpuLeaseManager | None = None,
    ) -> dict[str, Any]: ...


QUEUE_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS queue_jobs (
        job_id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL,
        experiment_id TEXT NOT NULL,
        plan_hash TEXT NOT NULL,
        device TEXT NOT NULL,
        state TEXT NOT NULL,
        max_retries INTEGER NOT NULL,
        attempts_made INTEGER NOT NULL,
        snapshot_dir TEXT NOT NULL,
        work_dir TEXT NOT NULL,
        plan_path TEXT NOT NULL,
        cancel_requested INTEGER NOT NULL DEFAULT 0,
        heartbeat_at TEXT,
        last_reason TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS queue_attempts (
        attempt_id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id TEXT NOT NULL,
        attempt_no INTEGER NOT NULL,
        state TEXT NOT NULL,
        reason TEXT,
        started_at TEXT NOT NULL,
        ended_at TEXT
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_queue_jobs_state ON queue_jobs(state);",
    "CREATE INDEX IF NOT EXISTS idx_queue_jobs_plan_hash ON queue_jobs(plan_hash);",
)

LEASE_TIMEOUT_SECONDS = 120.0
HEARTBEAT_STALE_SECONDS = 120.0


class QueueError(RuntimeError):
    """Raised for queue misuse (duplicates, busy leases, illegal transitions)."""


class LeaseBusyError(QueueError):
    """Raised when a GPU lease is already held by a live job."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class QueueJob:
    """One queued run, keyed by its plan hash."""

    job_id: str
    run_id: str
    experiment_id: str
    plan_hash: str
    device: str
    state: str
    max_retries: int
    attempts_made: int
    snapshot_dir: str
    work_dir: str
    plan_path: str
    cancel_requested: bool = False
    heartbeat_at: str | None = None
    last_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class GpuLeaseManager:
    """Single-lease-per-device guard with stale-holder recovery.

    The lease file is updated under a short file lock; holders refresh their
    heartbeat while running. A holder whose heartbeat is older than the timeout
    is presumed crashed and its lease may be reclaimed, with the theft recorded
    on the new holder's job.
    """

    def __init__(
        self, leases_dir: Path | str, timeout_seconds: float = LEASE_TIMEOUT_SECONDS
    ) -> None:
        self.leases_dir = Path(leases_dir)
        self.leases_dir.mkdir(parents=True, exist_ok=True)
        self.timeout_seconds = timeout_seconds

    def _paths(self, device: str) -> tuple[Path, Path]:
        safe = device.replace(":", "_").replace("/", "_")
        return self.leases_dir / f"gpu_{safe}.json", self.leases_dir / f"gpu_{safe}.lock"

    def _read_lease(self, lease_path: Path) -> dict[str, Any] | None:
        if not lease_path.is_file():
            return None
        try:
            data = json.loads(lease_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        return data if isinstance(data, dict) else None

    def _heartbeat_age(self, lease: dict[str, Any]) -> float:
        try:
            seen = datetime.fromisoformat(str(lease.get("heartbeat_at", "")))
        except ValueError:
            return float("inf")
        return (datetime.now(UTC) - seen).total_seconds()

    def acquire(self, device: str, job_id: str) -> dict[str, Any]:
        """Acquire the device lease or raise LeaseBusyError naming the holder."""
        lease_path, lock_path = self._paths(device)
        with FileLock(str(lock_path), timeout=10.0):
            existing = self._read_lease(lease_path)
            if existing is not None and self._heartbeat_age(existing) <= self.timeout_seconds:
                raise LeaseBusyError(
                    f"device '{device}' is leased by job '{existing.get('job_id')}' "
                    f"(heartbeat {self._heartbeat_age(existing):.0f}s ago); "
                    "simultaneous GPU jobs are forbidden"
                )
            stolen = existing is not None
            record = {
                "device": device,
                "job_id": job_id,
                "acquired_at": _now(),
                "heartbeat_at": _now(),
                "stole_stale_lease": stolen,
                "previous_holder": existing["job_id"] if existing is not None else None,
            }
            lease_path.write_text(json.dumps(record, indent=2), encoding="utf-8")
            return record

    def heartbeat(self, device: str, job_id: str) -> None:
        lease_path, lock_path = self._paths(device)
        with FileLock(str(lock_path), timeout=10.0):
            existing = self._read_lease(lease_path)
            if existing is None or existing.get("job_id") != job_id:
                raise QueueError(f"job '{job_id}' does not hold the '{device}' lease")
            existing["heartbeat_at"] = _now()
            lease_path.write_text(json.dumps(existing, indent=2), encoding="utf-8")

    def release(self, device: str, job_id: str) -> None:
        lease_path, lock_path = self._paths(device)
        with FileLock(str(lock_path), timeout=10.0):
            existing = self._read_lease(lease_path)
            if existing is not None and existing.get("job_id") == job_id:
                lease_path.unlink(missing_ok=True)

    def holder(self, device: str) -> dict[str, Any] | None:
        lease_path, _ = self._paths(device)
        return self._read_lease(lease_path)


class PrepPool:
    """Bounded worker pool for CPU preparation steps (validation, verification)."""

    def __init__(self, max_workers: int = 2) -> None:
        if max_workers < 1:
            raise QueueError("preparation pool needs at least one worker")
        self.max_workers = max_workers
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="xlm-prep")

    def map(self, fn: Callable[[Any], Any], items: list[Any]) -> list[Any]:
        """Run preparation over items with bounded parallelism, preserving order."""
        return list(self._pool.map(fn, items))

    def shutdown(self) -> None:
        self._pool.shutdown(wait=True)


class ExperimentQueue:
    """Durable queue over the run ledger with crash-safe recovery."""

    def __init__(
        self,
        ledger: RunLedger,
        paths: ArtifactPaths,
        tree_root: Path | str | None = None,
        heartbeat_stale_seconds: float = HEARTBEAT_STALE_SECONDS,
    ) -> None:
        self.ledger = ledger
        self.paths = paths
        self.tree_root = Path(tree_root) if tree_root is not None else Path.cwd()
        self.heartbeat_stale_seconds = heartbeat_stale_seconds
        self.leases = GpuLeaseManager(paths.root / "leases")
        self._ensure_tables()

    # ------------------------------------------------------------ persistence

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.ledger.db_path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_tables(self) -> None:
        conn = self._connect()
        try:
            for statement in QUEUE_SCHEMA_STATEMENTS:
                conn.execute(statement)
            conn.commit()
        finally:
            conn.close()

    def _write_job_row(self, job: QueueJob) -> None:
        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO queue_jobs
                (job_id, run_id, experiment_id, plan_hash, device, state, max_retries,
                 attempts_made, snapshot_dir, work_dir, plan_path, cancel_requested,
                 heartbeat_at, last_reason, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    job.job_id,
                    job.run_id,
                    job.experiment_id,
                    job.plan_hash,
                    job.device,
                    job.state,
                    job.max_retries,
                    job.attempts_made,
                    job.snapshot_dir,
                    job.work_dir,
                    job.plan_path,
                    int(job.cancel_requested),
                    job.heartbeat_at,
                    job.last_reason,
                    _now(),
                    _now(),
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def get_job(self, job_id: str) -> QueueJob | None:
        conn = self._connect()
        try:
            row = conn.execute("SELECT * FROM queue_jobs WHERE job_id = ?;", (job_id,)).fetchone()
        finally:
            conn.close()
        if row is None:
            return None
        return self._row_to_job(row)

    @staticmethod
    def _row_to_job(row: sqlite3.Row) -> QueueJob:
        data = dict(row)
        return QueueJob(
            job_id=data["job_id"],
            run_id=data["run_id"],
            experiment_id=data["experiment_id"],
            plan_hash=data["plan_hash"],
            device=data["device"],
            state=data["state"],
            max_retries=int(data["max_retries"]),
            attempts_made=int(data["attempts_made"]),
            snapshot_dir=data["snapshot_dir"],
            work_dir=data["work_dir"],
            plan_path=data["plan_path"],
            cancel_requested=bool(data["cancel_requested"]),
            heartbeat_at=data["heartbeat_at"],
            last_reason=data["last_reason"],
        )

    def list_jobs(self, states: list[str] | None = None) -> list[QueueJob]:
        conn = self._connect()
        try:
            if states:
                placeholders = ",".join("?" for _ in states)
                rows = conn.execute(
                    f"SELECT * FROM queue_jobs WHERE state IN ({placeholders}) "
                    "ORDER BY created_at;",
                    tuple(states),
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM queue_jobs ORDER BY created_at;").fetchall()
        finally:
            conn.close()
        return [self._row_to_job(row) for row in rows]

    def find_by_plan_hash(self, plan_hash: str) -> QueueJob | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM queue_jobs WHERE plan_hash = ? ORDER BY created_at LIMIT 1;",
                (plan_hash,),
            ).fetchone()
        finally:
            conn.close()
        return self._row_to_job(row) if row is not None else None

    # ---------------------------------------------------------------- submit

    def submit(
        self,
        plan: ExecutablePlan,
        plan_path: Path | str,
        snapshot_dir: Path | str,
        device: str,
        authorization_token: str,
        max_retries: int = 0,
        allow_duplicate: bool = False,
        authorization: Any = None,
    ) -> tuple[str, bool]:
        """Enqueue a plan, deduplicating identical plan hashes.

        Returns (job_id, is_duplicate). Refuses plans with blockers and
        negative retry budgets.
        """
        if plan.blockers:
            raise QueueError(
                "plan carries approval blockers: "
                + "; ".join(f"{b.code}: {b.detail}" for b in plan.blockers)
            )
        if max_retries < 0:
            raise QueueError("max_retries cannot be negative")
        from xlm.artifacts.manifest import identity_digest
        from xlm.experiments.authorization import validate_against_ticket
        from xlm.experiments.environment import runtime_locations
        from xlm.experiments.execution import validate_envelope, write_json

        plan.validate_identity()
        persisted = ExecutablePlan.load(plan_path)
        persisted.validate_identity()
        if persisted.plan_hash != plan.plan_hash:
            raise QueueError("submitted plan differs from persisted plan")
        snapshot_path = Path(snapshot_dir).resolve()
        if not (snapshot_path / "manifest.json").is_file():
            raise QueueError(f"snapshot manifest missing at {snapshot_path}")
        assert plan.execution_envelope is not None
        validate_envelope(
            plan.execution_envelope,
            snapshot_path,
            check_environment=False,
            resolve_components=False,
        )
        effective = validate_against_ticket(plan.to_dict(), authorization)
        if not allow_duplicate:
            existing = self.find_by_plan_hash(plan.plan_hash)
            if existing is not None:
                return existing.job_id, True

        job_id = f"run_{plan.plan_hash[:16]}"
        if self.get_job(job_id) is not None and not allow_duplicate:
            return job_id, True
        if allow_duplicate and self.get_job(job_id) is not None:
            suffix = 1
            while self.get_job(f"{job_id}_{suffix}") is not None:
                suffix += 1
            job_id = f"{job_id}_{suffix}"

        work_dir = self.paths.runs / job_id
        work_dir.mkdir(parents=True, exist_ok=True)

        try:
            self.ledger.register_run(job_id, plan.draft_id, plan.plan_hash)
        except sqlite3.IntegrityError:
            if not allow_duplicate:
                return job_id, True
            raise
        self.ledger.transition_run(job_id, RunStatus.DRAFT, RunStatus.PLANNED)
        frozen_plan_path = work_dir / "plan.json"
        plan.save(frozen_plan_path)
        job = QueueJob(
            job_id=job_id,
            run_id=job_id,
            experiment_id=plan.draft_id,
            plan_hash=plan.plan_hash,
            device=device,
            state=RunStatus.AUTHORIZED.value,
            max_retries=max_retries,
            attempts_made=0,
            snapshot_dir=str(snapshot_path.resolve()),
            work_dir=str(work_dir),
            plan_path=str(frozen_plan_path.resolve()),
        )
        admission = {
            "job": {
                key: job.to_dict()[key]
                for key in (
                    "job_id",
                    "run_id",
                    "experiment_id",
                    "plan_hash",
                    "device",
                    "max_retries",
                    "snapshot_dir",
                    "work_dir",
                    "plan_path",
                )
            },
            "authorization": effective.to_dict(),
            "runtime_locations": runtime_locations(),
        }
        write_json(work_dir / "admission.json", admission)
        self.ledger.authorize_run(job_id, plan.plan_hash, identity_digest(admission))
        self._write_job_row(job)
        self._write_run_record(job, plan, {"authorization_token": identity_digest(admission)})
        return job_id, False

    # --------------------------------------------------------------- cancel

    def cancel(self, job_id: str) -> QueueJob:
        """Cancel a job: queued jobs stop immediately, running jobs see a flag."""
        job = self.get_job(job_id)
        if job is None:
            raise QueueError(f"unknown job '{job_id}'")
        terminal = (
            RunStatus.SUCCEEDED.value,
            RunStatus.FAILED.value,
            RunStatus.CANCELLED.value,
            RunStatus.INTERRUPTED.value,
        )
        if job.state in terminal:
            return job
        cancelled = QueueJob(**{**job.to_dict(), "cancel_requested": True})
        if job.state in (RunStatus.PLANNED.value, RunStatus.AUTHORIZED.value):
            cancelled = QueueJob(**{**cancelled.to_dict(), "state": RunStatus.CANCELLED.value})
            self.ledger.transition_run(job_id, RunStatus(job.state), RunStatus.CANCELLED)
        self._write_job_row(cancelled)
        (Path(job.work_dir) / "cancel.requested").write_text(
            json.dumps({"job_id": job_id, "at": _now()}), encoding="utf-8"
        )
        return cancelled

    # -------------------------------------------------------------- recovery

    def _heartbeat_stale(self, job: QueueJob) -> bool:
        if not job.heartbeat_at:
            return True
        try:
            seen = datetime.fromisoformat(job.heartbeat_at)
        except ValueError:
            return True
        return (datetime.now(UTC) - seen).total_seconds() > self.heartbeat_stale_seconds

    def recover_stale_jobs(self) -> list[str]:
        """Reap crashed RUNNING jobs: INTERRUPTED, then requeue iff retries remain.

        Idempotent: a second call finds no stale RUNNING jobs and changes nothing,
        so no duplicate run is ever launched by recovery itself.
        """
        recovered: list[str] = []
        for job in self.list_jobs([RunStatus.RUNNING.value]):
            if not self._heartbeat_stale(job):
                continue
            # A lost parent heartbeat is not proof that its worker stopped.
            import psutil

            from xlm.experiments.execution import read_json

            worker_dir = Path(job.work_dir) / f"worker-{job.attempts_made}"
            alive = False
            for identity_path in (
                self.paths.runs / job.job_id / "runner_identity.json",
                worker_dir / "process_identity.json",
                worker_dir / "worker_identity.json",
            ):
                if identity_path.is_file():
                    identity = read_json(identity_path)
                    try:
                        alive |= (
                            psutil.Process(identity["pid"]).create_time() == identity["create_time"]
                        )
                    except psutil.NoSuchProcess:
                        pass
            if alive:
                if job.device.startswith("cuda"):
                    self.leases.heartbeat(job.device, job.job_id)
                continue
            reason = (
                f"holder heartbeat stale beyond {self.heartbeat_stale_seconds:.0f}s; "
                "presumed crashed"
            )
            attempts = max(1, job.attempts_made)
            if any(a["attempt_no"] == attempts for a in self.attempts(job.job_id)):
                self._close_attempt(job.job_id, attempts, RunStatus.INTERRUPTED.value, reason)
            else:
                self._record_attempt(job.job_id, attempts, RunStatus.INTERRUPTED.value, reason)
            if job.device.startswith("cuda"):
                self.leases.release(job.device, job.job_id)
            if attempts <= job.max_retries:
                # Requeue: the ledger keeps the INTERRUPTED mark (crash evidence)
                # while the queue returns the job to AUTHORIZED. The runner
                # transitions from the ledger's current state, and both
                # AUTHORIZED->RUNNING and INTERRUPTED->RUNNING are legal.
                requeued = QueueJob(
                    **{
                        **job.to_dict(),
                        "state": RunStatus.AUTHORIZED.value,
                        "attempts_made": attempts,
                        "last_reason": reason,
                        "heartbeat_at": None,
                    }
                )
                self._write_job_row(requeued)
                self.ledger.transition_run(job.job_id, RunStatus.RUNNING, RunStatus.INTERRUPTED)
                recovered.append(job.job_id)
            else:
                failed = QueueJob(
                    **{
                        **job.to_dict(),
                        "state": RunStatus.FAILED.value,
                        "attempts_made": attempts,
                        "last_reason": reason + "; retry budget exhausted",
                    }
                )
                self._write_job_row(failed)
                self.ledger.transition_run(job.job_id, RunStatus.RUNNING, RunStatus.FAILED)
                recovered.append(job.job_id)
        return recovered

    def _record_attempt(self, job_id: str, attempt_no: int, state: str, reason: str | None) -> None:
        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT INTO queue_attempts (job_id, attempt_no, state, reason, started_at, ended_at)
                VALUES (?, ?, ?, ?, ?, ?);
                """,
                (
                    job_id,
                    attempt_no,
                    state,
                    reason,
                    _now(),
                    _now() if state != RunStatus.RUNNING.value else None,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def attempts(self, job_id: str) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM queue_attempts WHERE job_id = ? ORDER BY attempt_no;",
                (job_id,),
            ).fetchall()
        finally:
            conn.close()
        return [dict(row) for row in rows]

    # --------------------------------------------------------------- runner

    def _write_run_record(
        self, job: QueueJob, plan: ExecutablePlan | None, extra: dict[str, Any]
    ) -> None:
        record = {
            "run_id": job.run_id,
            "experiment_id": job.experiment_id,
            "status": job.state,
            "plan_hash": job.plan_hash,
            "plan_id": plan.plan_id if plan else None,
            "device": job.device,
            "attempts_made": job.attempts_made,
            "heartbeat_at": job.heartbeat_at,
            "created_at": _now(),
            "updated_at": _now(),
            **extra,
        }
        (Path(job.work_dir) / "run_record.json").write_text(
            json.dumps(record, indent=2, sort_keys=True, default=str), encoding="utf-8"
        )

    def _heartbeat(self, job: QueueJob) -> QueueJob:
        current = self.get_job(job.job_id) or job
        updated = QueueJob(**{**current.to_dict(), "heartbeat_at": _now()})
        self._write_job_row(updated)
        return updated

    def _cancel_requested(self, job: QueueJob) -> bool:
        current = self.get_job(job.job_id)
        if current is not None and current.cancel_requested:
            return True
        return (Path(job.work_dir) / "cancel.requested").is_file()

    def next_job(self, device: str | None = None) -> QueueJob | None:
        """Oldest AUTHORIZED job, optionally filtered by device."""
        for job in self.list_jobs([RunStatus.AUTHORIZED.value]):
            if device is None or job.device == device:
                return job
        return None

    def run_next(
        self,
        device: str | None = None,
        executor: Executor | None = None,
    ) -> dict[str, Any]:
        """Execute the next authorized job sequentially. Returns a result summary."""
        job = self.next_job(device)
        if job is None:
            return {"ran": False, "reason": "no authorized jobs"}
        return self.run_job(job.job_id, executor=executor)

    def run_job(
        self,
        job_id: str,
        executor: Executor | None = None,
    ) -> dict[str, Any]:
        from xlm.artifacts.manifest import ensure_plain_path, validate_component

        validate_component(job_id)
        if self.get_job(job_id) is None:
            raise QueueError(f"unknown job '{job_id}'")
        lock_path = self.paths.runs / f".{job_id}.run.lock"
        ensure_plain_path(lock_path)
        try:
            with FileLock(str(lock_path), timeout=0):
                import os

                import psutil

                from xlm.experiments.execution import write_json

                write_json(
                    self.paths.runs / job_id / "runner_identity.json",
                    {
                        "pid": os.getpid(),
                        "create_time": psutil.Process().create_time(),
                    },
                )
                return self._run_job_owned(job_id, executor)
        except Timeout:
            return {"ran": False, "job_id": job_id, "reason": "job already owned by another runner"}

    def _run_job_owned(self, job_id: str, executor: Executor | None = None) -> dict[str, Any]:
        """Execute one job: verify, lease, run, release. Never runs a duplicate."""
        job = self.get_job(job_id)
        if job is None:
            raise QueueError(f"unknown job '{job_id}'")
        if job.state != RunStatus.AUTHORIZED.value:
            return {"ran": False, "job_id": job_id, "reason": f"job is {job.state}, not authorized"}

        plan = None

        # 1. Bind durable job, authorization, resolved plan and captured bytes.
        try:
            from xlm.artifacts.manifest import identity_digest
            from xlm.experiments.authorization import AuthorizationTicket, validate_against_ticket
            from xlm.experiments.execution import read_json, validate_envelope

            plan = ExecutablePlan.load(job.plan_path)
            plan.validate_identity()
            admission = read_json(Path(job.work_dir) / "admission.json")
            registered = self.ledger.get_run(job.job_id)
            if registered is None or registered["authorization_token"] != identity_digest(
                admission
            ):
                raise QueueError("job authorization/admission identity mismatch")
            if admission["job"] != {key: job.to_dict()[key] for key in admission["job"]}:
                raise QueueError("persisted job identity mismatch")
            if job.plan_hash != plan.plan_hash or registered["plan_hash"] != plan.plan_hash:
                raise QueueError("persisted job/plan identity mismatch")
            validate_against_ticket(
                plan.to_dict(), AuthorizationTicket.from_dict(admission["authorization"])
            )
            assert plan.execution_envelope is not None
            validate_envelope(
                plan.execution_envelope,
                Path(job.snapshot_dir),
                check_environment=False,
                resolve_components=False,
            )
        except Exception as exc:
            return self._finish_blocked(job, plan, f"frozen execution verification: {exc}")

        # Plan execution device must match the leased device family.
        plan_device = str(plan.resolved_config.get("training", {}).get("device", "cpu"))
        job_family = "cuda" if job.device.startswith("cuda") else "cpu"
        if plan_device != job_family:
            return self._finish_blocked(
                job,
                plan,
                f"plan device '{plan_device}' does not match job device '{job.device}'",
            )

        if self._cancel_requested(job):
            return self._finish_cancelled(job, plan, "cancelled before start")

        # 2. GPU lease for accelerator jobs.
        lease_held = False
        if job.device.startswith("cuda"):
            try:
                self.leases.acquire(job.device, job.job_id)
                lease_held = True
            except LeaseBusyError as exc:
                return {"ran": False, "job_id": job_id, "reason": f"lease busy: {exc}"}

        # 3. Transition to RUNNING from the ledger's current state. Both
        # AUTHORIZED->RUNNING and INTERRUPTED->RUNNING (requeue after a crash)
        # are legal; anything else refuses rather than forcing the ledger.
        current = self.ledger.get_run(job_id)
        if current is None:
            raise QueueError(f"run '{job_id}' missing from ledger")
        self.ledger.transition_run(job_id, RunStatus(current["status"]), RunStatus.RUNNING)
        job = QueueJob(
            **{
                **job.to_dict(),
                "state": RunStatus.RUNNING.value,
                "attempts_made": job.attempts_made + 1,
            }
        )
        self._write_job_row(job)
        job = self._heartbeat(job)
        self._record_attempt(job_id, job.attempts_made, RunStatus.RUNNING.value, None)

        outcome: dict[str, Any] = {"job_id": job_id}
        try:
            if executor is not None:
                raise QueueError("callable executor overrides cannot execute a frozen job")
            from xlm.experiments.launcher import launch_worker

            def heartbeat() -> None:
                self._heartbeat(job)
                if lease_held:
                    self.leases.heartbeat(job.device, job.job_id)

            result = launch_worker(
                {
                    "action": "queue",
                    "envelope": plan.execution_envelope,
                    "plan": plan.to_dict(),
                    "job": job.to_dict(),
                    "snapshot_dir": job.snapshot_dir,
                    "runtime_locations": admission["runtime_locations"],
                    "artifact_home": str(self.paths.root.resolve()),
                    "max_owned_disk_bytes": int(
                        (admission["authorization"].get("max_new_disk_gib") or 2) * 1024**3
                    ),
                },
                Path(job.work_dir) / f"worker-{job.attempts_made}",
                should_cancel=lambda: self._cancel_requested(job),
                heartbeat=heartbeat,
            )
            outcome.update(result)
            if result.get("cancelled"):
                return self._finish_cancelled(job, plan, str(result.get("reason", "cancelled")))
            return self._finish_succeeded(job, plan, result)
        except Exception as exc:  # noqa: BLE001 - failure evidence must be recorded
            reason = f"{type(exc).__name__}: {exc}\n{traceback.format_exc(limit=8)}"
            return self._finish_failed(job, plan, reason)
        finally:
            if lease_held:
                self.leases.release(job.device, job.job_id)

    def _finish_succeeded(
        self, job: QueueJob, plan: ExecutablePlan, result: dict[str, Any]
    ) -> dict[str, Any]:
        done = QueueJob(
            **{**job.to_dict(), "state": RunStatus.SUCCEEDED.value, "last_reason": None}
        )
        self._write_job_row(done)
        self.ledger.transition_run(job.job_id, RunStatus.RUNNING, RunStatus.SUCCEEDED)
        self._close_attempt(job.job_id, job.attempts_made, RunStatus.SUCCEEDED.value, None)
        self._write_run_record(done, plan, {"result": result})
        return {"ran": True, "job_id": job.job_id, "state": "SUCCEEDED", **result}

    def _finish_failed(self, job: QueueJob, plan: ExecutablePlan, reason: str) -> dict[str, Any]:
        failed = QueueJob(
            **{
                **job.to_dict(),
                "state": RunStatus.FAILED.value,
                "last_reason": reason.splitlines()[0] if reason else "failed",
            }
        )
        self._write_job_row(failed)
        self.ledger.transition_run(job.job_id, RunStatus.RUNNING, RunStatus.FAILED)
        self._close_attempt(job.job_id, job.attempts_made, RunStatus.FAILED.value, reason)
        self._write_run_record(failed, plan, {"failure_reason": reason})
        return {"ran": True, "job_id": job.job_id, "state": "FAILED", "reason": reason}

    def _finish_cancelled(self, job: QueueJob, plan: ExecutablePlan, reason: str) -> dict[str, Any]:
        cancelled = QueueJob(
            **{**job.to_dict(), "state": RunStatus.CANCELLED.value, "last_reason": reason}
        )
        self._write_job_row(cancelled)
        current = self.ledger.get_run(job.job_id)
        if current and current["status"] == RunStatus.RUNNING.value:
            self.ledger.transition_run(job.job_id, RunStatus.RUNNING, RunStatus.CANCELLED)
        elif current and current["status"] == RunStatus.AUTHORIZED.value:
            self.ledger.transition_run(job.job_id, RunStatus.AUTHORIZED, RunStatus.CANCELLED)
        self._close_attempt(job.job_id, job.attempts_made, RunStatus.CANCELLED.value, reason)
        self._write_run_record(cancelled, plan, {"cancel_reason": reason})
        return {"ran": True, "job_id": job.job_id, "state": "CANCELLED", "reason": reason}

    def _finish_blocked(
        self, job: QueueJob, plan: ExecutablePlan | None, reason: str
    ) -> dict[str, Any]:
        blocked = QueueJob(
            **{**job.to_dict(), "state": RunStatus.BLOCKED.value, "last_reason": reason}
        )
        self._write_job_row(blocked)
        current = self.ledger.get_run(job.job_id)
        if current and current["status"] == RunStatus.AUTHORIZED.value:
            self.ledger.transition_run(job.job_id, RunStatus.AUTHORIZED, RunStatus.BLOCKED)
        self._write_run_record(blocked, plan, {"blocked_reason": reason})
        return {"ran": False, "job_id": job.job_id, "state": "BLOCKED", "reason": reason}

    def _close_attempt(self, job_id: str, attempt_no: int, state: str, reason: str | None) -> None:
        conn = self._connect()
        try:
            conn.execute(
                "UPDATE queue_attempts SET state = ?, reason = ?, ended_at = ? "
                "WHERE job_id = ? AND attempt_no = ?;",
                (state, reason, _now(), job_id, attempt_no),
            )
            conn.commit()
        finally:
            conn.close()


def execute_plan_run(
    plan: ExecutablePlan,
    job: QueueJob,
    should_cancel: Callable[[], bool],
    lease_manager: GpuLeaseManager | None = None,
    execution: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Generic plan execution with no architecture or dataset special cases.

    Components are built from the resolved plan through the existing factories;
    the data source is whatever the plan declares (an explicit token list or a
    resolvable artifact path), interpreted only through `TrainingBatcher`'s own
    input types. Cancellation is honored between optimizer steps.
    """

    from xlm.training.checkpoint import CheckpointManager
    from xlm.training.components import construct_training_components
    from xlm.training.trainer import Trainer

    resolved = plan.resolved_config
    training = resolved["training"]
    device = str(training["device"])
    if device not in ("cpu", "cuda"):
        raise QueueError(f"unsupported execution device '{device}'")
    components = construct_training_components(resolved, device=device)
    model, objective = components.model, components.objective
    optimizer, manifest = components.optimizer, components.optimizer_manifest
    schedule, batcher = components.schedule, components.batcher

    work = Path(job.work_dir)
    if execution is None:
        raise QueueError("frozen execution context is required")
    manager = CheckpointManager(
        paths=ArtifactPaths(root=work / "artifacts"),
        execution=execution,
        runtime_config={
            "version": 2,
            "plan": resolved,
            "data_identity": execution["envelope"]["bindings"]["data"],
        },
    )
    trainer = Trainer(
        model=model,
        objective=objective,
        optimizer=optimizer,
        optimizer_manifest=manifest,
        schedule=schedule,
        batcher=batcher,
        checkpoint_manager=manager,
        run_id=job.job_id,
        plan_id=plan.plan_id,
        device=device,
        precision=str(training.get("precision", "fp32")),
        gradient_clip_norm=float(training.get("gradient_clip_norm", 1.0)),
        max_valid_targets=plan.budget_valid_targets,
        max_train_seconds=training.get("budget", {}).get("max_train_seconds"),
        checkpoint_every_valid_targets=plan.checkpoint_every_valid_targets,
        activation_checkpointing=bool(training.get("activation_checkpointing", False)),
        compile_model=bool(training.get("compile", False)),
        science=components.science,
    )

    # Resume an interrupted attempt through the existing checkpoint loader. Never
    # restart at zero and collide with a checkpoint already committed by this job.
    from xlm.experiments.execution import read_json

    candidates: list[tuple[int, int, bool, Path]] = []
    if job.attempts_made > 1 and manager.paths.checkpoints.is_dir():
        for index, checkpoint in enumerate(manager.paths.checkpoints.iterdir()):
            if index >= 10000:
                raise QueueError("retry checkpoint scan exceeds entry limit")
            if not checkpoint.name.startswith(job.job_id + "_"):
                continue
            manager.store.verify_artifact(checkpoint)
            metadata = read_json(checkpoint / "checkpoint_meta.json")
            if metadata["run_id"] != job.run_id or metadata["plan_id"] != plan.plan_id:
                raise QueueError("retry checkpoint belongs to another run/plan")
            candidates.append(
                (
                    metadata["committed_valid_targets"],
                    metadata["step"],
                    checkpoint.name.endswith("_final"),
                    checkpoint,
                )
            )
            manager._published_bytes += sum(
                p.stat().st_size for p in checkpoint.iterdir() if p.is_file()
            )
    restored: Path | None = None
    already_final = False
    if candidates:
        _, _, already_final, restored = max(candidates)
        meta = manager.load_checkpoint(
            restored,
            model=model,
            objective=objective,
            optimizer=optimizer,
            optimizer_manifest=manifest,
            schedule=schedule,
            batcher=batcher,
            expected_plan_id=plan.plan_id,
            device=device,
            scaler=trainer.scaler,
            science=components.science,
        )
        if meta.committed_valid_targets > plan.budget_valid_targets:
            raise QueueError("retry checkpoint exceeds frozen target budget")
        trainer.step = meta.step
        trainer.committed_valid_targets = meta.committed_valid_targets
        trainer.processed_valid_targets = meta.processed_valid_targets
        trainer.next_checkpoint_target = (
            meta.committed_valid_targets + plan.checkpoint_every_valid_targets
        )

    steps = 0
    last_loss: float | None = None
    started = time.monotonic()
    while True:
        if should_cancel():
            trainer.batcher.rollback()
            checkpoint = trainer._save_checkpoint(f"{job.job_id}_cancelled")
            return {
                "cancelled": True,
                "reason": "cancel requested",
                "steps": steps,
                "checkpoint": str(checkpoint),
            }
        if time.monotonic() - started >= (plan.budget_max_seconds or 600.0):
            trainer._save_checkpoint(f"{job.job_id}_time_limit")
            raise QueueError("wall-time limit reached before target budget completion")
        metrics = trainer.train_step()
        if metrics is None:
            if trainer._last_step_skipped:
                continue
            break
        steps += 1
        last_loss = metrics.loss
        if lease_manager is not None and job.device.startswith("cuda"):
            lease_manager.heartbeat(job.device, job.job_id)
    summary = {
        "steps": steps,
        "committed_valid_targets": trainer.committed_valid_targets,
        "final_loss": last_loss,
        "checkpoint_id": f"{job.job_id}_final",
        "resumed_checkpoint": str(restored) if restored else None,
    }
    if trainer.committed_valid_targets != plan.budget_valid_targets:
        raise QueueError("training stopped before the exact target budget; refusing success")
    if not already_final:
        trainer._save_checkpoint(str(summary["checkpoint_id"]))
    return summary
