"""SQLite transactional ledger for artifact indexing, run states, and durable recovery."""

from __future__ import annotations

import json
import sqlite3
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

    def rebuild_from_filesystem(self, paths: ArtifactPaths, store: ArtifactStore) -> dict[str, Any]:
        """Reconstruct SQLite catalog from disk manifests and durable run records."""
        rebuilt_artifacts = 0
        corrupt_artifacts: list[str] = []
        rebuilt_runs = 0
        legacy_artifacts: list[str] = []

        # Scan artifact directories
        for kind_dir in sorted(bounded_children(paths.root)) if paths.root.is_dir() else []:
            if (
                not kind_dir.is_dir()
                or kind_dir.name.startswith(".")
                or kind_dir.name.casefold() in INTERNAL_KINDS
            ):
                continue
            ensure_plain_path(kind_dir)
            for candidate in bounded_children(kind_dir):
                if candidate.is_dir() and (candidate / "manifest.json").exists():
                    try:
                        manifest = store.verify_artifact(candidate)
                        self.record_artifact(
                            artifact_id=manifest.artifact_id,
                            kind=manifest.kind,
                            path=candidate,
                            manifest_json=manifest.model_dump_json(),
                            status=manifest.status,
                        )
                        rebuilt_artifacts += 1
                        if manifest.schema_version == 1:
                            legacy_artifacts.append(manifest.artifact_id)
                    except Exception as exc:  # noqa: BLE001
                        corrupt_artifacts.append(f"{candidate.name}: {exc}")

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
            "corrupt_artifacts": corrupt_artifacts,
            "rebuilt_runs": rebuilt_runs,
            "legacy_artifacts": legacy_artifacts,
        }
