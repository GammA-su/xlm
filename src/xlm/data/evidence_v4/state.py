"""The SQLite receipt store (protocol section 8).

One ``state.sqlite`` per execution root, stdlib :mod:`sqlite3` with
``synchronous=FULL`` and ``locking_mode=EXCLUSIVE``: the lock is taken at
open, so a second process refuses instead of duplicating work. Every write is
its own explicit transaction. An attempt row is committed before the request
it describes is issued.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from xlm.data.evidence_v4 import frozen

DB_NAME = "state.sqlite"

SCHEMA = """
CREATE TABLE run (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    protocol_version TEXT NOT NULL,
    plan_digest TEXT NOT NULL,
    selection_digest TEXT NOT NULL,
    source_revision TEXT NOT NULL,
    synthetic INTEGER NOT NULL,
    created_utc TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('RUNNING', 'STOPPED', 'COMPLETE')),
    stop_reason TEXT,
    updated_utc TEXT NOT NULL
);
CREATE TABLE operations (
    op_id TEXT PRIMARY KEY,
    seq INTEGER NOT NULL UNIQUE,
    arm TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    file TEXT NOT NULL,
    kind TEXT NOT NULL,
    range_start INTEGER,
    range_end INTEGER,
    status TEXT NOT NULL CHECK (status IN ('PENDING', 'COMPLETE'))
);
CREATE TABLE attempts (
    attempt_id INTEGER PRIMARY KEY AUTOINCREMENT,
    op_id TEXT NOT NULL REFERENCES operations (op_id),
    arm TEXT NOT NULL,
    try_number INTEGER NOT NULL,
    hop INTEGER NOT NULL,
    url_scheme TEXT NOT NULL,
    url_host TEXT NOT NULL,
    url_path TEXT NOT NULL,
    url_query_sha256 TEXT,
    range_start INTEGER NOT NULL,
    range_end INTEGER NOT NULL,
    started_utc TEXT NOT NULL,
    completed_utc TEXT,
    http_status INTEGER,
    response_bytes INTEGER NOT NULL DEFAULT 0,
    outcome TEXT NOT NULL,
    error TEXT,
    temp_path TEXT NOT NULL,
    response_sha256 TEXT
);
CREATE TABLE outputs (
    op_id TEXT PRIMARY KEY REFERENCES operations (op_id),
    attempt_id INTEGER NOT NULL UNIQUE REFERENCES attempts (attempt_id),
    retained_file TEXT NOT NULL,
    bytes INTEGER NOT NULL,
    sha256 TEXT NOT NULL
);
"""

IN_PROGRESS = "IN_PROGRESS"
SUCCESS = "SUCCESS"
REDIRECT = "REDIRECT"
HTTP_ERROR = "HTTP_ERROR"
TRANSPORT_ERROR = "TRANSPORT_ERROR"
TIMEOUT = "TIMEOUT"
INTERRUPTED = "INTERRUPTED"
POLICY_REFUSED = "POLICY_REFUSED"
IDENTITY_MISMATCH = "IDENTITY_MISMATCH"
VERIFICATION_FAILED = "VERIFICATION_FAILED"
CAP_EXCEEDED = "CAP_EXCEEDED"
OUTCOMES = (
    IN_PROGRESS,
    SUCCESS,
    REDIRECT,
    HTTP_ERROR,
    TRANSPORT_ERROR,
    TIMEOUT,
    INTERRUPTED,
    POLICY_REFUSED,
    IDENTITY_MISMATCH,
    VERIFICATION_FAILED,
    CAP_EXCEEDED,
)

ATTEMPT_COLUMNS = (
    "attempt_id",
    "op_id",
    "arm",
    "try_number",
    "hop",
    "url_scheme",
    "url_host",
    "url_path",
    "url_query_sha256",
    "range_start",
    "range_end",
    "started_utc",
    "completed_utc",
    "http_status",
    "response_bytes",
    "outcome",
    "error",
    "temp_path",
    "response_sha256",
)


class StateError(RuntimeError):
    """The store is locked, corrupt or inconsistent with the plan: STOP for review."""


@dataclass(frozen=True)
class RunRow:
    protocol_version: str
    plan_digest: str
    selection_digest: str
    source_revision: str
    synthetic: bool
    created_utc: str
    status: str
    stop_reason: str | None
    updated_utc: str


def temp_name(op_id: str, attempt_id: int) -> str:
    if not frozen.OP_ID.match(op_id) or type(attempt_id) is not int or attempt_id < 1:
        raise StateError("temp names derive only from plan operation IDs and attempt IDs")
    return f"tmp/{op_id}.a{attempt_id}.part"


def payload_name(op_id: str) -> str:
    if not frozen.OP_ID.match(op_id):
        raise StateError("payload names derive only from plan operation IDs")
    return f"payload/{op_id}.bin"


class Store:
    """Exclusive read-write connection to one root's ``state.sqlite``."""

    def __init__(self, path: Path, *, create: bool) -> None:
        if create == path.exists():
            raise StateError(f"{path} {'already exists' if create else 'is missing'}")
        self._conn = sqlite3.connect(str(path), isolation_level=None, timeout=0)
        self._conn.row_factory = sqlite3.Row
        try:
            self._conn.execute("PRAGMA locking_mode=EXCLUSIVE")
            self._conn.execute("PRAGMA synchronous=FULL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.execute("BEGIN EXCLUSIVE")
            if create:
                for statement in SCHEMA.split(";"):
                    if statement.strip():
                        self._conn.execute(statement)
            self._conn.execute("COMMIT")
        except sqlite3.OperationalError as exc:
            self._conn.close()
            raise StateError(f"state store is locked or unreadable: {exc}") from exc

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            yield self._conn
        except BaseException:
            self._conn.execute("ROLLBACK")
            raise
        self._conn.execute("COMMIT")

    # -- run ---------------------------------------------------------------

    def init_run(self, plan: frozen.Plan, now: str) -> None:
        with self._tx() as db:
            db.execute(
                "INSERT INTO run VALUES (1, ?, ?, ?, ?, ?, ?, 'RUNNING', NULL, ?)",
                (
                    plan.profile.protocol_version,
                    plan.digest,
                    frozen.SELECTION_DIGEST,
                    plan.source.revision,
                    int(plan.synthetic),
                    now,
                    now,
                ),
            )
            for op in plan.operations:
                start, end = op.range if op.range is not None else (None, None)
                db.execute(
                    "INSERT INTO operations VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'PENDING')",
                    (
                        op.op_id,
                        op.seq,
                        op.arm,
                        op.ordinal,
                        op.source_file.file,
                        op.kind,
                        start,
                        end,
                    ),
                )

    def run(self) -> RunRow:
        return read_run(self._conn)

    def set_status(self, status: str, reason: str | None, now: str) -> None:
        with self._tx() as db:
            db.execute(
                "UPDATE run SET status = ?, stop_reason = ?, updated_utc = ? WHERE id = 1",
                (status, reason, now),
            )

    # -- operations / attempts --------------------------------------------

    def operations(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self._conn.execute("SELECT * FROM operations ORDER BY seq")]

    def attempts(self) -> list[dict[str, Any]]:
        return read_attempts(self._conn)

    def outputs(self) -> dict[str, dict[str, Any]]:
        return {r["op_id"]: dict(r) for r in self._conn.execute("SELECT * FROM outputs")}

    def tries_used(self, op_id: str) -> int:
        row = self._conn.execute(
            "SELECT COALESCE(MAX(try_number), 0) FROM attempts WHERE op_id = ?", (op_id,)
        ).fetchone()
        return int(row[0])

    def arm_totals(self, arm: str) -> tuple[int, int]:
        row = self._conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(response_bytes), 0) FROM attempts WHERE arm = ?",
            (arm,),
        ).fetchone()
        return int(row[0]), int(row[1])

    def begin_attempt(
        self,
        *,
        op: frozen.Operation,
        try_number: int,
        hop: int,
        url: dict[str, str | None],
        start: int,
        end: int,
        now: str,
    ) -> tuple[int, str]:
        """Commit the attempt row BEFORE the request is issued."""
        with self._tx() as db:
            cursor = db.execute(
                "INSERT INTO attempts (op_id, arm, try_number, hop, url_scheme, url_host, "
                "url_path, url_query_sha256, range_start, range_end, started_utc, outcome, "
                "temp_path) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '')",
                (
                    op.op_id,
                    op.arm,
                    try_number,
                    hop,
                    url["scheme"],
                    url["host"],
                    url["path"],
                    url["query_sha256"],
                    start,
                    end,
                    now,
                    IN_PROGRESS,
                ),
            )
            attempt_id = int(cursor.lastrowid or 0)
            name = temp_name(op.op_id, attempt_id)
            db.execute("UPDATE attempts SET temp_path = ? WHERE attempt_id = ?", (name, attempt_id))
        return attempt_id, name

    def record_bytes(self, attempt_id: int, received: int) -> None:
        with self._tx() as db:
            db.execute(
                "UPDATE attempts SET response_bytes = ? WHERE attempt_id = ? AND outcome = ?",
                (received, attempt_id, IN_PROGRESS),
            )

    def finish_attempt(
        self,
        attempt_id: int,
        *,
        outcome: str,
        http_status: int | None,
        received: int,
        sha256: str | None,
        error: str | None,
        now: str,
    ) -> None:
        if outcome not in OUTCOMES or outcome in (IN_PROGRESS, SUCCESS):
            raise StateError(f"invalid terminal outcome {outcome!r}")
        with self._tx() as db:
            updated = db.execute(
                "UPDATE attempts SET outcome = ?, http_status = ?, response_bytes = ?, "
                "response_sha256 = ?, error = ?, completed_utc = ? "
                "WHERE attempt_id = ? AND outcome = ?",
                (outcome, http_status, received, sha256, error, now, attempt_id, IN_PROGRESS),
            ).rowcount
            if updated != 1:
                raise StateError(f"attempt {attempt_id} is not in progress")

    def complete_operation(
        self,
        attempt_id: int,
        *,
        op: frozen.Operation,
        received: int,
        sha256: str,
        now: str,
        derived: tuple[str, int, int] | None,
    ) -> str:
        """One transaction: attempt SUCCESS, output row, operation COMPLETE (+ derived range)."""
        retained = payload_name(op.op_id)
        with self._tx() as db:
            updated = db.execute(
                "UPDATE attempts SET outcome = ?, http_status = 206, response_bytes = ?, "
                "response_sha256 = ?, completed_utc = ? WHERE attempt_id = ? AND outcome = ?",
                (SUCCESS, received, sha256, now, attempt_id, IN_PROGRESS),
            ).rowcount
            if updated != 1:
                raise StateError(f"attempt {attempt_id} is not in progress")
            db.execute(
                "INSERT INTO outputs VALUES (?, ?, ?, ?, ?)",
                (op.op_id, attempt_id, retained, received, sha256),
            )
            done = db.execute(
                "UPDATE operations SET status = 'COMPLETE' WHERE op_id = ? AND status = 'PENDING'",
                (op.op_id,),
            ).rowcount
            if done != 1:
                raise StateError(f"{op.op_id} is not pending")
            if derived is not None:
                target, start, end = derived
                set_range = db.execute(
                    "UPDATE operations SET range_start = ?, range_end = ? "
                    "WHERE op_id = ? AND range_start IS NULL AND status = 'PENDING'",
                    (start, end, target),
                ).rowcount
                if set_range != 1:
                    raise StateError(f"{target}: derived range already set or not pending")
        return retained

    def mark_interrupted(self, attempt_id: int, received: int, sha256: str, now: str) -> None:
        self.finish_attempt(
            attempt_id,
            outcome=INTERRUPTED,
            http_status=None,
            received=received,
            sha256=sha256,
            error="process ended while the attempt was in progress; reconciled on restart",
            now=now,
        )


def read_run(conn: sqlite3.Connection) -> RunRow:
    rows = conn.execute("SELECT * FROM run").fetchall()
    if len(rows) != 1:
        raise StateError("state store has no single run row")
    row = rows[0]
    return RunRow(
        protocol_version=row["protocol_version"],
        plan_digest=row["plan_digest"],
        selection_digest=row["selection_digest"],
        source_revision=row["source_revision"],
        synthetic=bool(row["synthetic"]),
        created_utc=row["created_utc"],
        status=row["status"],
        stop_reason=row["stop_reason"],
        updated_utc=row["updated_utc"],
    )


def read_attempts(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [
        {k: r[k] for k in ATTEMPT_COLUMNS}
        for r in conn.execute("SELECT * FROM attempts ORDER BY attempt_id")
    ]


def open_read_only(path: Path) -> sqlite3.Connection:
    """Read-only inspection connection (fails while a run holds the exclusive lock)."""
    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=0)
    conn.row_factory = sqlite3.Row
    return conn
