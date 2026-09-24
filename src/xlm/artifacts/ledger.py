"""SQLite transactional ledger for artifact indexing, run states, and durable recovery."""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from xlm.artifacts.manifest import INTERNAL_KINDS, bounded_children, ensure_plain_path
from xlm.artifacts.store import ArtifactStore
from xlm.core.contracts import RunStatus, validate_run_transition
from xlm.core.paths import ArtifactPaths

CURRENT_SCHEMA_VERSION = 1

#: Ledger status recorded when a referenced store artifact fails verification.
#: Republish (or a healing audit) restores "completed"; rows are never deleted.
STATUS_UNVERIFIABLE = "unverifiable"


def _declared_payload_bytes(manifest_path: Path) -> int:
    """Sum declared payload bytes from a manifest document (no payload reads)."""
    try:
        data = json.loads(manifest_path.read_bytes().decode("utf-8"))
    except Exception as exc:
        raise ValueError(f"unparsable manifest {manifest_path.name}: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("files"), list):
        raise ValueError(f"malformed manifest {manifest_path.name}: no file list")
    total = 0
    for entry in data["files"]:
        if not isinstance(entry, dict):
            raise ValueError(f"malformed manifest {manifest_path.name}: bad file entry")
        size = entry.get("size_bytes", 0)
        if not isinstance(size, int) or size < 0:
            raise ValueError(f"malformed manifest {manifest_path.name}: bad size")
        total += size
    return total


def _row_payload_bytes(manifest_json: str) -> int:
    """Sum declared payload bytes from a stored manifest document."""
    try:
        data = json.loads(manifest_json)
    except Exception as exc:
        raise ValueError(f"unparsable stored manifest: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("files"), list):
        raise ValueError("malformed stored manifest: no file list")
    total = 0
    for entry in data["files"]:
        if not isinstance(entry, dict):
            raise ValueError("malformed stored manifest: bad file entry")
        size = entry.get("size_bytes", 0)
        if not isinstance(size, int) or size < 0:
            raise ValueError("malformed stored manifest: bad size")
        total += size
    return total


class RunLedger:
    """Transactional SQLite ledger for experiment runs and artifact catalogs."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @contextmanager
    def _get_connection(self) -> Generator[sqlite3.Connection, None, None]:
        conn = sqlite3.connect(str(self.db_path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA busy_timeout=5000;")
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._get_connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_info (
                    version INTEGER PRIMARY KEY
                );
                """
            )
            cur = conn.execute("SELECT version FROM schema_info;")
            row = cur.fetchone()
            if not row:
                conn.execute(
                    "INSERT INTO schema_info (version) VALUES (?);", (CURRENT_SCHEMA_VERSION,)
                )
            elif row["version"] != CURRENT_SCHEMA_VERSION:
                raise ValueError(
                    f"Incompatible ledger schema version: {row['version']} "
                    f"!= {CURRENT_SCHEMA_VERSION}"
                )

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS artifacts (
                    artifact_id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    path TEXT NOT NULL,
                    manifest_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    experiment_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    plan_hash TEXT NOT NULL,
                    authorization_token TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )

    def record_artifact(
        self,
        artifact_id: str,
        kind: str,
        path: Path,
        manifest_json: str,
        status: str = "completed",
    ) -> None:
        """Insert or update published artifact in catalog."""
        now_str = datetime.now(UTC).isoformat()
        with self._get_connection() as conn:
            existing = conn.execute(
                "SELECT kind, path FROM artifacts WHERE artifact_id = ?", (artifact_id,)
            ).fetchone()
            if existing and (
                existing["kind"] != kind or Path(existing["path"]).resolve() != path.resolve()
            ):
                raise ValueError(f"Artifact catalog identity conflict: {artifact_id!r}")
            conn.execute(
                """
                INSERT OR REPLACE INTO artifacts
                    (artifact_id, kind, path, manifest_json, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?);
                """,
                (artifact_id, kind, str(path.resolve()), manifest_json, status, now_str),
            )

    def get_artifact(self, artifact_id: str) -> dict[str, Any] | None:
        """Retrieve artifact record from ledger."""
        with self._get_connection() as conn:
            cur = conn.execute("SELECT * FROM artifacts WHERE artifact_id = ?;", (artifact_id,))
            row = cur.fetchone()
            if not row:
                return None
            return dict(row)

    def _record_if_missing(
        self,
        artifact_id: str,
        kind: str,
        path: Path,
        manifest_json: str,
        status: str,
    ) -> bool:
        """Insert the row only when no identical row exists; True if written.

        The check and the write share one IMMEDIATE transaction, so two
        concurrent reconcilers serialize: exactly one inserts, the other
        observes the row and skips. A same-ID row with a different kind or
        path is an identity conflict and raises (never papered over); a row
        that differs only in status or manifest bytes is replaced. No-op
        duplicates never touch the row — not even its timestamps.
        """
        with self._get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT kind, path, manifest_json, status FROM artifacts WHERE artifact_id = ?;",
                (artifact_id,),
            ).fetchone()
            resolved = str(path.resolve())
            if existing is not None:
                if (
                    existing["kind"] != kind
                    or Path(existing["path"]).resolve() != Path(resolved).resolve()
                ):
                    raise ValueError(f"Artifact catalog identity conflict: {artifact_id!r}")
                if existing["manifest_json"] == manifest_json and existing["status"] == status:
                    return False
            conn.execute(
                """
                INSERT OR REPLACE INTO artifacts
                    (artifact_id, kind, path, manifest_json, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?);
                """,
                (
                    artifact_id,
                    kind,
                    resolved,
                    manifest_json,
                    status,
                    datetime.now(UTC).isoformat(),
                ),
            )
            return True

    def register_run(self, run_id: str, experiment_id: str, plan_hash: str) -> None:
        """Register a new run in DRAFT status."""
        now_str = datetime.now(UTC).isoformat()
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO runs
                    (run_id, experiment_id, status, plan_hash,
                     authorization_token, created_at, updated_at)
                VALUES (?, ?, ?, ?, NULL, ?, ?);
                """,
                (run_id, experiment_id, RunStatus.DRAFT.value, plan_hash, now_str, now_str),
            )

    def transition_run(self, run_id: str, from_state: RunStatus, to_state: RunStatus) -> None:
        """Atomically transition run state, enforcing legal state machine progression."""
        validate_run_transition(from_state, to_state)
        now_str = datetime.now(UTC).isoformat()
        with self._get_connection() as conn:
            cur = conn.execute(
                """
                UPDATE runs
                SET status = ?, updated_at = ?
                WHERE run_id = ? AND status = ?;
                """,
                (to_state.value, now_str, run_id, from_state.value),
            )
            if cur.rowcount == 0:
                # Retrieve current state for error reporting
                cur_curr = conn.execute("SELECT status FROM runs WHERE run_id = ?;", (run_id,))
                curr_row = cur_curr.fetchone()
                if not curr_row:
                    raise KeyError(f"Run '{run_id}' not found in ledger")
                raise ValueError(
                    f"State transition failed: run '{run_id}' "
                    f"is currently in state '{curr_row['status']}', "
                    f"expected '{from_state.value}'"
                )

    def authorize_run(self, run_id: str, plan_hash: str, auth_token: str) -> None:
        """Authorize a PLANNED run, binding authorization token to exact plan hash."""
        now_str = datetime.now(UTC).isoformat()
        with self._get_connection() as conn:
            cur = conn.execute("SELECT status, plan_hash FROM runs WHERE run_id = ?;", (run_id,))
            row = cur.fetchone()
            if not row:
                raise KeyError(f"Run '{run_id}' not found in ledger")

            if row["status"] != RunStatus.PLANNED.value:
                raise ValueError(
                    f"Cannot authorize run '{run_id}' in state '{row['status']}'. "
                    f"Run must be in state '{RunStatus.PLANNED.value}'"
                )

            if row["plan_hash"] != plan_hash:
                raise ValueError(
                    f"Stale authorization rejected: plan hash '{plan_hash}' does not match "
                    f"run plan hash '{row['plan_hash']}'"
                )

            conn.execute(
                """
                UPDATE runs
                SET status = ?, authorization_token = ?, updated_at = ?
                WHERE run_id = ?;
                """,
                (RunStatus.AUTHORIZED.value, auth_token, now_str, run_id),
            )

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        """Retrieve run record from ledger."""
        with self._get_connection() as conn:
            cur = conn.execute("SELECT * FROM runs WHERE run_id = ?;", (run_id,))
            row = cur.fetchone()
            if not row:
                return None
            return dict(row)

    def rebuild_from_filesystem(
        self,
        paths: ArtifactPaths,
        store: ArtifactStore,
        *,
        max_artifacts: int | None = None,
        max_verify_bytes: int | None = None,
        deadline_seconds: float | None = None,
    ) -> dict[str, Any]:
        """Reconstruct SQLite catalog from disk manifests and durable run records.

        Authority model: store bytes + manifest + ``_COMPLETED`` + full
        :meth:`ArtifactStore.verify_artifact` are authoritative proof an
        artifact exists; the ledger only records durable workflow knowledge.
        Only fully verified artifacts are recorded — corrupt, incomplete,
        and marker-less states are reported, never inserted.

        Idempotence: a candidate whose verified manifest bytes, kind, and
        path already match the recorded row is skipped without touching the
        row (duplicate reconciliation is a true no-op, not a timestamp
        refresh). Identity conflicts (same ID, different kind/path) are
        reported, never papered over.

        Bounds: ``max_artifacts`` caps examined manifest-bearing candidates,
        ``max_verify_bytes`` caps declared payload bytes put through
        verification, and ``deadline_seconds`` caps wall time. Any cap stops
        the scan with ``truncated: True`` and a reason — never silently.
        """
        if max_artifacts is not None and max_artifacts < 1:
            raise ValueError("max_artifacts must be positive")
        if max_verify_bytes is not None and max_verify_bytes < 1:
            raise ValueError("max_verify_bytes must be positive")
        if deadline_seconds is not None and deadline_seconds <= 0:
            raise ValueError("deadline_seconds must be positive")
        deadline = None if deadline_seconds is None else time.monotonic() + deadline_seconds

        rebuilt_artifacts = 0
        already_recorded = 0
        corrupt_artifacts: list[str] = []
        incomplete_artifacts: list[str] = []
        conflicts: list[str] = []
        rebuilt_runs = 0
        legacy_artifacts: list[str] = []
        examined = 0
        verified_bytes = 0
        scan_seconds = 0.0
        verify_seconds = 0.0
        ledger_seconds = 0.0
        truncated = False
        truncated_reason: str | None = None

        def located(candidate: Path) -> str:
            return f"{candidate.parent.name}/{candidate.name}"

        # Scan artifact directories
        scan_start = time.monotonic()
        kind_dirs = sorted(bounded_children(paths.root)) if paths.root.is_dir() else []
        scan_seconds += time.monotonic() - scan_start
        for kind_dir in kind_dirs:
            if truncated:
                break
            if (
                not kind_dir.is_dir()
                or kind_dir.name.startswith(".")
                or kind_dir.name.casefold() in INTERNAL_KINDS
            ):
                continue
            ensure_plain_path(kind_dir)
            scan_start = time.monotonic()
            candidates = list(bounded_children(kind_dir))
            scan_seconds += time.monotonic() - scan_start
            for candidate in candidates:
                if deadline is not None and time.monotonic() >= deadline:
                    truncated, truncated_reason = True, "deadline exceeded"
                    break
                if max_artifacts is not None and examined >= max_artifacts:
                    truncated, truncated_reason = True, "artifact count cap reached"
                    break
                if not candidate.is_dir():
                    continue
                manifest_path = candidate / "manifest.json"
                marker_path = candidate / "_COMPLETED"
                if not manifest_path.is_file():
                    if marker_path.is_file():
                        incomplete_artifacts.append(
                            f"{located(candidate)}: completion marker without manifest"
                        )
                    continue
                if not marker_path.is_file():
                    incomplete_artifacts.append(
                        f"{located(candidate)}: manifest without completion marker"
                    )
                    continue
                try:
                    ensure_plain_path(candidate)
                    declared = _declared_payload_bytes(manifest_path)
                except (ValueError, OSError) as exc:
                    corrupt_artifacts.append(f"{candidate.name}: {exc}")
                    continue
                if max_verify_bytes is not None and verified_bytes + declared > max_verify_bytes:
                    truncated, truncated_reason = True, "verify byte cap reached"
                    break
                examined += 1
                verify_start = time.monotonic()
                try:
                    manifest = store.verify_artifact(candidate)
                except Exception as exc:  # noqa: BLE001
                    verify_seconds += time.monotonic() - verify_start
                    corrupt_artifacts.append(f"{candidate.name}: {exc}")
                    continue
                verify_seconds += time.monotonic() - verify_start
                verified_bytes += declared
                ledger_start = time.monotonic()
                try:
                    if manifest.schema_version == 1:
                        legacy_artifacts.append(manifest.artifact_id)
                    wrote = self._record_if_missing(
                        artifact_id=manifest.artifact_id,
                        kind=manifest.kind,
                        path=candidate,
                        manifest_json=manifest.model_dump_json(),
                        status=manifest.status,
                    )
                    if wrote:
                        rebuilt_artifacts += 1
                    else:
                        already_recorded += 1
                except ValueError as exc:
                    conflicts.append(f"{manifest.artifact_id}: {exc}")
                finally:
                    ledger_seconds += time.monotonic() - ledger_start

        # Scan durable run records in runs directory
        runs_dir = paths.runs
        if runs_dir.is_dir():
            for run_dir in runs_dir.iterdir():
                record_file = run_dir / "run_record.json"
                if record_file.is_file():
                    try:
                        rec = json.loads(record_file.read_text(encoding="utf-8"))
                        with self._get_connection() as conn:
                            conn.execute(
                                """
                                INSERT OR REPLACE INTO runs
                                    (run_id, experiment_id, status, plan_hash,
                                     authorization_token, created_at, updated_at)
                                VALUES (?, ?, ?, ?, ?, ?, ?);
                                """,
                                (
                                    rec["run_id"],
                                    rec["experiment_id"],
                                    rec["status"],
                                    rec["plan_hash"],
                                    rec.get("authorization_token"),
                                    rec["created_at"],
                                    rec.get("updated_at", rec["created_at"]),
                                ),
                            )
                        rebuilt_runs += 1
                    except Exception:  # noqa: BLE001
                        pass

        return {
            "rebuilt_artifacts": rebuilt_artifacts,
            "already_recorded": already_recorded,
            "corrupt_artifacts": corrupt_artifacts,
            "incomplete_artifacts": incomplete_artifacts,
            "conflicts": conflicts,
            "rebuilt_runs": rebuilt_runs,
            "legacy_artifacts": legacy_artifacts,
            "examined_artifacts": examined,
            "verified_bytes": verified_bytes,
            "truncated": truncated,
            "truncated_reason": truncated_reason,
            "scan_seconds": round(scan_seconds, 3),
            "verify_seconds": round(verify_seconds, 3),
            "ledger_seconds": round(ledger_seconds, 3),
        }

    def audit_ledger_references(
        self,
        paths: ArtifactPaths,
        store: ArtifactStore,
        *,
        max_artifacts: int | None = None,
        max_verify_bytes: int | None = None,
        deadline_seconds: float | None = None,
    ) -> dict[str, Any]:
        """Re-verify every ledger artifact reference against the store.

        Verdict per record: ``ok`` (valid, untouched), ``healed`` (valid
        again after an ``unverifiable`` marking — status restored to
        ``completed``), or ``unusable`` (missing/corrupt store artifact or a
        reference escaping the store root: status flipped to
        ``"unverifiable"``, identity columns preserved, row never deleted).
        A missing/corrupt store artifact is never repaired by inventing
        data; the reference just stops being trusted. Same bounds and
        truncation honesty as :meth:`rebuild_from_filesystem`.
        """
        if max_artifacts is not None and max_artifacts < 1:
            raise ValueError("max_artifacts must be positive")
        if max_verify_bytes is not None and max_verify_bytes < 1:
            raise ValueError("max_verify_bytes must be positive")
        if deadline_seconds is not None and deadline_seconds <= 0:
            raise ValueError("deadline_seconds must be positive")
        deadline = None if deadline_seconds is None else time.monotonic() + deadline_seconds

        verdicts: list[dict[str, Any]] = []
        examined = 0
        verified_bytes = 0
        verify_seconds = 0.0
        truncated = False
        truncated_reason: str | None = None
        with self._get_connection() as conn:
            query = (
                "SELECT artifact_id, kind, path, manifest_json, status "
                "FROM artifacts ORDER BY artifact_id"
            )
            if max_artifacts is not None:
                query += " LIMIT ?"
                rows = conn.execute(query, (max_artifacts,)).fetchall()
            else:
                rows = conn.execute(query).fetchall()
        root = paths.root.resolve()
        for row in rows:
            if deadline is not None and time.monotonic() >= deadline:
                truncated, truncated_reason = True, "deadline exceeded"
                break
            artifact_id = str(row["artifact_id"])
            try:
                declared = _row_payload_bytes(str(row["manifest_json"]))
            except ValueError:
                declared = 0
            if max_verify_bytes is not None and verified_bytes + declared > max_verify_bytes:
                truncated, truncated_reason = True, "verify byte cap reached"
                break
            examined += 1
            target = Path(str(row["path"]))
            resolved = target.resolve()
            if resolved != root and root not in resolved.parents:
                verdicts.append(
                    {
                        "artifact_id": artifact_id,
                        "verdict": "unusable",
                        "detail": "reference escapes the artifact root",
                    }
                )
                self._mark_unverifiable(artifact_id)
                continue
            verify_start = time.monotonic()
            try:
                manifest = store.verify_artifact(target)
                usable = manifest.artifact_id == artifact_id and manifest.kind == str(row["kind"])
                detail = "valid" if usable else "identity drift"
            except Exception as exc:  # noqa: BLE001
                usable = False
                detail = f"{type(exc).__name__}: {exc}"
            finally:
                verify_seconds += time.monotonic() - verify_start
            verified_bytes += declared
            if usable:
                if str(row["status"]) != "completed":
                    self._record_if_missing(
                        artifact_id=artifact_id,
                        kind=str(row["kind"]),
                        path=target,
                        manifest_json=str(row["manifest_json"]),
                        status="completed",
                    )
                    verdicts.append(
                        {"artifact_id": artifact_id, "verdict": "healed", "detail": detail}
                    )
                else:
                    verdicts.append({"artifact_id": artifact_id, "verdict": "ok", "detail": detail})
            else:
                self._mark_unverifiable(artifact_id)
                verdicts.append(
                    {"artifact_id": artifact_id, "verdict": "unusable", "detail": detail}
                )
        return {
            "examined": examined,
            "verdicts": verdicts,
            "ok": sum(1 for v in verdicts if v["verdict"] == "ok"),
            "healed": sum(1 for v in verdicts if v["verdict"] == "healed"),
            "unusable": sum(1 for v in verdicts if v["verdict"] == "unusable"),
            "verified_bytes": verified_bytes,
            "truncated": truncated,
            "truncated_reason": truncated_reason,
            "verify_seconds": round(verify_seconds, 3),
        }

    def _mark_unverifiable(self, artifact_id: str) -> None:
        """Flip a reference to unusable without touching its identity columns.

        Already-unusable rows are left alone, so repeated audits never churn
        timestamps either.
        """
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT kind, path, manifest_json, status FROM artifacts WHERE artifact_id = ?;",
                (artifact_id,),
            ).fetchone()
            if row is None or str(row["status"]) == STATUS_UNVERIFIABLE:
                return
        self._record_if_missing(
            artifact_id=artifact_id,
            kind=str(row["kind"]),
            path=Path(str(row["path"])),
            manifest_json=str(row["manifest_json"]),
            status=STATUS_UNVERIFIABLE,
        )
