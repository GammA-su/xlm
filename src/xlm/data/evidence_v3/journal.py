"""Authoritative epoch journal and its strict state reducer.

The journal (``state/journal.jsonl``) is the ONLY source of truth for
Phase-P accounting: requests, body bytes, reservations, active runtime,
disk inventory, plan progress and phase state. There is no snapshot.

Durability: every record is one canonical-JSON line appended with
``O_APPEND`` and fsynced before the caller may proceed; the head file
(``state/journal.head``) is then rewritten with a write-through rename.
Load replays every record through :class:`EpochState`, verifying the
sequence, the hash chain (seeded by the epoch_start digest, so a journal
from another epoch/root cannot be spliced in), canonical bytes, and that
the journal is not shorter than its durable head (tail truncation /
rollback refuses). A torn final line is never repaired automatically: the
epoch is BLOCKED for review.

The reducer enforces the same invariants on replay as live: an attempt is
reserved only for the next plan operation, inside the P ceilings (frozen
caps minus the future-D reservation); bodies grow monotonically; nothing
is refunded; crash recovery charges full reservations.
"""

from __future__ import annotations

import copy
import hashlib
import os
import re
from dataclasses import dataclass, field
from typing import Any

from xlm.data.evidence_v2 import canonical
from xlm.data.evidence_v3 import fsroot


class JournalError(RuntimeError):
    """Journal integrity failure: BLOCKED, never silently repaired."""


class StateError(RuntimeError):
    """A record violates the epoch state machine or a reserved ceiling."""


JOURNAL_REL = "state/journal.jsonl"
HEAD_REL = "state/journal.head"
HEAD_TMP_REL = "state/journal.head.tmp"
JOURNAL_ALLOWANCE = 16 * 1024 * 1024
HEAD_ALLOWANCE = 8192
MAX_RECORD_BYTES = 262144
_HEX64 = re.compile(r"\A[0-9a-f]{64}\Z")
_ID = re.compile(r"\A[A-Za-z0-9._:\-]{1,120}\Z")

RECORD_TYPES = frozenset(
    {
        "GENESIS",
        "SESSION_OPEN",
        "SESSION_CLOSE",
        "CRASH_ATTEMPT",
        "CRASH_TIME",
        "CRASH_DISK",
        "PHASE",
        "TIME",
        "ATTEMPT_RESERVE",
        "ATTEMPT_ISSUED",
        "ATTEMPT_HEADERS",
        "ATTEMPT_BODY",
        "ATTEMPT_SETTLE",
        "DISK_RESERVE",
        "DISK_COMMIT",
        "DISK_ABORT",
        "ABORT",
    }
)
SETTLE_OUTCOMES = frozenset({"COMPLETE", "REDIRECT", "FAILED", "REFUSED"})


def chain_seed(epoch_id: str, epoch_start_digest: str) -> str:
    return hashlib.sha256(
        f"essential-web-evidence-v3.0 journal|{epoch_id}|{epoch_start_digest}".encode()
    ).hexdigest()


def _record_digest(record: dict[str, Any]) -> str:
    return canonical.digest({k: v for k, v in record.items() if k != "digest"})


# --------------------------------------------------------------------------
# State
# --------------------------------------------------------------------------


@dataclass
class Attempt:
    attempt_id: str
    arm: str
    file: str
    op_index: int
    logical_attempt: int
    hop: int
    reservation: int
    status: str = "RESERVED"
    body_total: int = 0
    charged: int | None = None
    outcome: str | None = None
    body_sha256: str | None = None


@dataclass
class ArmState:
    phase: str = "NOT_STARTED"
    requests: int = 0
    requests_by_file: dict[str, int] = field(default_factory=dict)
    body_charged: int = 0
    body_charged_by_file: dict[str, int] = field(default_factory=dict)
    body_held: int = 0
    body_held_by_file: dict[str, int] = field(default_factory=dict)
    time_ns: int = 0
    time_ns_by_file: dict[str, int] = field(default_factory=dict)
    hold_ns: int = 0
    hold_file: str | None = None
    ops_done: list[dict[str, Any]] = field(default_factory=list)
    result_digest: str | None = None
    incomplete_reason: str | None = None


@dataclass
class DiskEntry:
    size: int
    sha256: str | None
    category: str
    arms: tuple[str, ...]
    control_allowance: bool


@dataclass
class DiskPending:
    rel: str
    temp_rel: str
    size: int
    sha256: str
    category: str
    arms: tuple[str, ...]
    replaces: bool
    op_complete: dict[str, Any] | None


@dataclass
class EpochState:
    epoch_id: str
    epoch_start_digest: str
    plans: dict[str, Any]
    disk_caps: dict[str, dict[str, int]]
    arms: dict[str, ArmState]
    attempts: dict[str, Attempt] = field(default_factory=dict)
    open_attempt: str | None = None
    disk: dict[str, DiskEntry] = field(default_factory=dict)
    pending: dict[str, DiskPending] = field(default_factory=dict)
    session: str | None = None
    sessions: int = 0
    aborts: list[dict[str, Any]] = field(default_factory=list)
    seq: int = -1

    # ----- queries --------------------------------------------------------

    def arm_plan(self, arm: str) -> dict[str, Any]:
        plan = self.plans.get(arm)
        if not isinstance(plan, dict):
            raise StateError(f"no plan summary for arm {arm!r}")
        return plan

    def next_op(self, arm: str) -> int:
        return len(self.arms[arm].ops_done)

    def occupancy(self, arm: str, category: str | None = None) -> int:
        total = 0
        for entry in self.disk.values():
            if arm in entry.arms and (category is None or entry.category == category):
                total += entry.size
        for pend in self.pending.values():
            if arm in pend.arms and (category is None or pend.category == category):
                total += pend.size
        return total

    def attempts_for(self, arm: str, op_index: int) -> list[Attempt]:
        return [a for a in self.attempts.values() if a.arm == arm and a.op_index == op_index]

    # ----- reducer --------------------------------------------------------

    def apply(self, record: dict[str, Any]) -> None:
        kind = record["type"]
        body = record["body"]
        if not isinstance(body, dict):
            raise StateError("record body must be an object")
        handler = getattr(self, f"_on_{kind.lower()}", None)
        if handler is None:
            raise StateError(f"unknown record type {kind}")
        if kind != "GENESIS" and self.seq < 0:
            raise StateError("journal must start with GENESIS")
        if kind not in ("GENESIS", "SESSION_OPEN", "CRASH_ATTEMPT", "CRASH_TIME", "CRASH_DISK"):
            if kind != "SESSION_CLOSE" and self.session is None:
                raise StateError(f"{kind} outside an open session")
        handler(body)
        self.seq = int(record["seq"])

    def _arm(self, arm: Any) -> ArmState:
        if arm not in self.arms:
            raise StateError(f"unknown arm {arm!r}")
        return self.arms[arm]

    def _on_genesis(self, body: dict[str, Any]) -> None:
        if self.seq >= 0:
            raise StateError("second GENESIS record")
        for rel, cell in body["initial_inventory"].items():
            fsroot.check_rel(rel)
            self.disk[rel] = DiskEntry(
                size=int(cell["size"]),
                sha256=cell["sha256"],
                category=str(cell["category"]),
                arms=tuple(cell["arms"]),
                control_allowance=bool(cell["control_allowance"]),
            )
        for arm in self.arms:
            for category in ("scratch", "final"):
                if self.occupancy(arm, category) > self.disk_caps[arm][category]:
                    raise StateError("initial inventory exceeds a disk subcap")
            if self.occupancy(arm) > self.disk_caps[arm]["combined"]:
                raise StateError("initial inventory exceeds the combined disk cap")

    def _on_session_open(self, body: dict[str, Any]) -> None:
        if self.session is not None:
            raise StateError("session already open; crash recovery must close it first")
        if self.open_attempt is not None or any(a.hold_ns for a in self.arms.values()):
            raise StateError("open work must be conserved before a new session")
        if self.pending:
            raise StateError("pending disk work must be recovered before a new session")
        session = body["session"]
        if not isinstance(session, str) or not _ID.match(session):
            raise StateError("invalid session id")
        self.session = session
        self.sessions += 1

    def _on_session_close(self, body: dict[str, Any]) -> None:
        if self.session is None or body.get("session") != self.session:
            raise StateError("closing a session that is not open")
        if self.open_attempt is not None or self.pending:
            raise StateError("cannot close a session with open work")
        if any(a.hold_ns for a in self.arms.values()):
            raise StateError("cannot close a session with an open time hold")
        self.session = None

    def _on_crash_attempt(self, body: dict[str, Any]) -> None:
        attempt = self.attempts.get(body["attempt_id"])
        if (
            attempt is None
            or attempt.charged is not None
            or self.open_attempt != attempt.attempt_id
        ):
            raise StateError("crash conservation for a non-open attempt")
        charged = int(body["charged"])
        if charged != max(attempt.reservation, attempt.body_total):
            raise StateError("crash conservation must charge the full reservation")
        self._settle(attempt, charged, "CRASH_RESERVED", None)

    def _on_crash_time(self, body: dict[str, Any]) -> None:
        arm = self._arm(body["arm"])
        if int(body["charged_ns"]) != arm.hold_ns or arm.hold_ns <= 0:
            raise StateError("crash time must charge exactly the open hold")
        self._charge_time(arm, arm.hold_file, arm.hold_ns)
        arm.hold_ns = 0
        arm.hold_file = None

    def _on_crash_disk(self, body: dict[str, Any]) -> None:
        rel = body["rel"]
        pend = self.pending.get(rel)
        if pend is None:
            raise StateError("crash disk recovery for a non-pending write")
        action = body["action"]
        if action == "committed":
            self._commit_pending(pend)
        elif action == "aborted":
            del self.pending[rel]
        else:
            raise StateError("crash disk action must be committed|aborted")

    def _on_phase(self, body: dict[str, Any]) -> None:
        arm_name = body["arm"]
        arm = self._arm(arm_name)
        to = body["to"]
        if to == "P_RUNNING":
            if arm.phase != "NOT_STARTED":
                raise StateError(f"arm {arm_name} cannot start P from {arm.phase}")
            if arm_name == "T" and self.arms["M"].phase != "P_COMPLETE_SEALED":
                raise StateError("T Phase P requires M Phase P sealed first (frozen order)")
        elif to == "P_COMPLETE_SEALED":
            if arm.phase != "P_RUNNING":
                raise StateError("seal requires P_RUNNING")
            if len(arm.ops_done) != len(self.arm_plan(arm_name)["ops"]):
                raise StateError("seal requires every plan operation complete")
            if self.open_attempt is not None or arm.hold_ns:
                raise StateError("seal requires no open attempt or time hold")
            digest = body.get("result_digest")
            if not isinstance(digest, str) or not _HEX64.match(digest):
                raise StateError("seal requires a result digest")
            arm.result_digest = digest
        elif to == "P_INCOMPLETE":
            if arm.phase != "P_RUNNING":
                raise StateError("INCOMPLETE only from P_RUNNING")
            if self.open_attempt is not None:
                raise StateError("settle the open attempt before INCOMPLETE")
            reason = body.get("reason")
            if not isinstance(reason, str) or not reason:
                raise StateError("INCOMPLETE requires a reason")
            arm.incomplete_reason = reason
        else:
            raise StateError(f"illegal phase target {to!r}")
        arm.phase = to

    def _charge_time(self, arm: ArmState, file: str | None, delta: int) -> None:
        arm.time_ns += delta
        if file is not None:
            arm.time_ns_by_file[file] = arm.time_ns_by_file.get(file, 0) + delta

    def _on_time(self, body: dict[str, Any]) -> None:
        """Charge elapsed time to the open hold's file, then open the next hold.

        The file an interval is charged to is decided by the durable state
        (the step that was holding), never by the record writer.
        """
        arm_name = body["arm"]
        arm = self._arm(arm_name)
        if arm.phase != "P_RUNNING":
            raise StateError("active time is charged only while P_RUNNING")
        delta = int(body["delta_ns"])
        hold = int(body["hold_ns"])
        hold_file = body["hold_file"]
        step = body["step"]
        if delta < 0 or hold < 0:
            raise StateError("time deltas/holds are non-negative")
        if step not in ("request", "work", "backoff", "close"):
            raise StateError("unknown time step")
        self._charge_time(arm, arm.hold_file, delta)
        arm.hold_ns = 0
        arm.hold_file = None
        if step == "close":
            if hold or hold_file is not None:
                raise StateError("a close step opens no hold")
            return
        if hold <= 0:
            raise StateError("a step must hold a positive reservation")
        if hold_file is not None and hold_file not in self.arm_plan(arm_name)["files"]:
            raise StateError(f"time hold for a file outside the plan: {hold_file!r}")
        seconds = self.arm_plan(arm_name)["time_seconds"]
        if arm.time_ns + hold > seconds["arm"] * 1_000_000_000:
            raise StateError("time hold exceeds the remaining arm budget")
        if hold_file is not None and (
            arm.time_ns_by_file.get(hold_file, 0) + hold > seconds["file"] * 1_000_000_000
        ):
            raise StateError("time hold exceeds the remaining file budget")
        if step == "request" and hold > seconds["request"] * 1_000_000_000:
            raise StateError("request hold exceeds 30 s")
        arm.hold_ns = hold
        arm.hold_file = hold_file

    def _on_attempt_reserve(self, body: dict[str, Any]) -> None:
        arm_name = body["arm"]
        arm = self._arm(arm_name)
        if arm.phase != "P_RUNNING":
            raise StateError("attempts are reserved only while P_RUNNING")
        if self.open_attempt is not None:
            raise StateError("attempts are strictly sequential")
        attempt_id = body["attempt_id"]
        if not isinstance(attempt_id, str) or not _ID.match(attempt_id):
            raise StateError("invalid attempt id")
        if attempt_id in self.attempts:
            raise StateError(f"duplicate attempt id {attempt_id}")
        plan = self.arm_plan(arm_name)
        op_index = int(body["op_index"])
        if op_index != len(arm.ops_done) or op_index >= len(plan["ops"]):
            raise StateError("attempt is not for the next authorized plan operation")
        op = plan["ops"][op_index]
        file = body["file"]
        if file != op["file"] or body["op"] != op["op"]:
            raise StateError("attempt file/op differs from the authorized plan operation")
        expected_range = op["range"]
        if expected_range is None:
            prior = arm.ops_done[op_index - 1] if op_index else {}
            expected_range = prior.get("next_range")
        if body["range"] != expected_range:
            raise StateError("attempt range differs from the authorized plan range")
        if not arm.hold_ns or arm.hold_file != file:
            raise StateError("an attempt needs an open time hold for its own file")
        reservation = int(body["reservation"])
        if not 0 < reservation <= plan["response_body_bytes_max"]:
            raise StateError("body reservation outside (0, 4 MiB]")
        ceiling = plan["files"][file]
        arm_ceiling = plan["arm_ceiling"]
        req_file = arm.requests_by_file.get(file, 0) + 1
        if req_file > ceiling["requests"] or arm.requests + 1 > arm_ceiling["requests"]:
            raise StateError("request ceiling (future-D reservation retained) exhausted")
        used_file = arm.body_charged_by_file.get(file, 0) + arm.body_held_by_file.get(file, 0)
        used_arm = arm.body_charged + arm.body_held
        for key in ("footer_bytes", "transfer_bytes"):
            if key in ceiling and used_file + reservation > ceiling[key]:
                raise StateError(f"{key} file ceiling cannot hold this reservation")
            if key in arm_ceiling and used_arm + reservation > arm_ceiling[key]:
                raise StateError(f"{key} arm ceiling cannot hold this reservation")
        arm.requests += 1
        arm.requests_by_file[file] = req_file
        arm.body_held += reservation
        arm.body_held_by_file[file] = arm.body_held_by_file.get(file, 0) + reservation
        self.attempts[attempt_id] = Attempt(
            attempt_id=attempt_id,
            arm=arm_name,
            file=file,
            op_index=op_index,
            logical_attempt=int(body["logical_attempt"]),
            hop=int(body["hop"]),
            reservation=reservation,
        )
        self.open_attempt = attempt_id

    def _open(self, body: dict[str, Any], allowed: tuple[str, ...]) -> Attempt:
        attempt = self.attempts.get(body["attempt_id"])
        if attempt is None or self.open_attempt != attempt.attempt_id:
            raise StateError("record for an attempt that is not open")
        if attempt.status not in allowed:
            raise StateError(f"attempt in {attempt.status} cannot move here")
        return attempt

    def _on_attempt_issued(self, body: dict[str, Any]) -> None:
        self._open(body, ("RESERVED",)).status = "ISSUED"

    def _on_attempt_headers(self, body: dict[str, Any]) -> None:
        attempt = self._open(body, ("ISSUED",))
        if type(body["status"]) is not int:
            raise StateError("status must be an integer")
        attempt.status = "HEADERS"

    def _on_attempt_body(self, body: dict[str, Any]) -> None:
        attempt = self._open(body, ("HEADERS", "BODY"))
        total = int(body["total"])
        if total <= attempt.body_total:
            raise StateError("body accounting must grow monotonically")
        attempt.body_total = total
        attempt.status = "BODY"

    def _settle(self, attempt: Attempt, charged: int, outcome: str, sha: str | None) -> None:
        arm = self.arms[attempt.arm]
        arm.body_held -= attempt.reservation
        arm.body_held_by_file[attempt.file] -= attempt.reservation
        arm.body_charged += charged
        arm.body_charged_by_file[attempt.file] = (
            arm.body_charged_by_file.get(attempt.file, 0) + charged
        )
        attempt.charged = charged
        attempt.outcome = outcome
        attempt.body_sha256 = sha
        attempt.status = "SETTLED"
        self.open_attempt = None

    def _on_attempt_settle(self, body: dict[str, Any]) -> None:
        attempt = self._open(body, ("RESERVED", "ISSUED", "HEADERS", "BODY"))
        outcome = body["outcome"]
        if outcome not in SETTLE_OUTCOMES:
            raise StateError("unknown settle outcome")
        charged = int(body["charged"])
        if charged < attempt.body_total:
            raise StateError("settlement may never refund observed bytes")
        if outcome in ("COMPLETE", "REDIRECT") and charged != attempt.body_total:
            raise StateError("certain outcomes charge exactly the observed bytes")
        if outcome not in ("COMPLETE", "REDIRECT") and charged not in (
            attempt.body_total,
            max(attempt.reservation, attempt.body_total),
        ):
            raise StateError("uncertain outcomes charge observed bytes or the full reservation")
        sha = body.get("body_sha256")
        if outcome == "COMPLETE" and (not isinstance(sha, str) or not _HEX64.match(sha)):
            raise StateError("COMPLETE settlement binds the body SHA-256")
        self._settle(attempt, charged, outcome, sha if outcome == "COMPLETE" else None)

    def _on_disk_reserve(self, body: dict[str, Any]) -> None:
        rel = body["rel"]
        temp_rel = body["temp_rel"]
        fsroot.check_rel(rel)
        fsroot.check_rel(temp_rel)
        if rel in self.pending or temp_rel in self.disk:
            raise StateError(f"{rel} already has pending disk work")
        replaces = body["replaces"] is True
        if replaces != (rel in self.disk):
            raise StateError(f"{rel}: replace flag does not match the inventory")
        if replaces and self.disk[rel].control_allowance:
            raise StateError("control allowances are never replaced through data writes")
        category = body["category"]
        arms = tuple(body["arms"])
        if category not in ("scratch", "final") or not arms or not set(arms) <= set(self.arms):
            raise StateError("disk category/arms invalid")
        size = int(body["size"])
        if size < 0 or not _HEX64.match(str(body["sha256"])):
            raise StateError("disk reservation needs size and SHA-256")
        for arm in arms:
            caps = self.disk_caps[arm]
            # Peak: existing old file + new temp coexist until the rename.
            if self.occupancy(arm, category) + size > caps[category]:
                raise StateError(f"{category} subcap exceeded for arm {arm}")
            if self.occupancy(arm) + size > caps["combined"]:
                raise StateError(f"combined disk cap exceeded for arm {arm}")
        op_complete = body.get("op_complete")
        if op_complete is not None:
            self._check_op_complete(op_complete, rel)
        self.pending[rel] = DiskPending(
            rel=rel,
            temp_rel=temp_rel,
            size=size,
            sha256=str(body["sha256"]),
            category=category,
            arms=arms,
            replaces=replaces,
            op_complete=op_complete,
        )

    def _check_op_complete(self, op_complete: dict[str, Any], rel: str) -> None:
        arm = self._arm(op_complete["arm"])
        index = int(op_complete["op_index"])
        if arm.phase != "P_RUNNING" or index != len(arm.ops_done):
            raise StateError("operation completion out of plan order")
        attempts = self.attempts_for(op_complete["arm"], index)
        if not attempts or attempts[-1].outcome != "COMPLETE":
            raise StateError("operation completion requires a COMPLETE final attempt")
        if op_complete.get("staged") != rel:
            raise StateError("operation completion must bind its staged file")
        op = self.arm_plan(op_complete["arm"])["ops"][index]
        if op_complete.get("file") != op["file"] or op_complete.get("op") != op["op"]:
            raise StateError("operation completion differs from the plan operation")
        next_range = op_complete.get("next_range")
        if op["op"] == "T_TRAILER":
            n = int(op["remote_length"])
            if (
                not isinstance(next_range, list)
                or len(next_range) != 2
                or next_range[1] != n - 9
                or not 4 <= int(next_range[0]) <= n - 9
            ):
                raise StateError("derived T footer range must be N-8-L..N-9 inside the file")
        elif next_range is not None:
            raise StateError("only the T trailer derives the next range")

    def _commit_pending(self, pend: DiskPending) -> None:
        self.disk[pend.rel] = DiskEntry(
            size=pend.size,
            sha256=pend.sha256,
            category=pend.category,
            arms=pend.arms,
            control_allowance=False,
        )
        del self.pending[pend.rel]
        if pend.op_complete is not None:
            cell = dict(pend.op_complete)
            arm = self.arms[cell["arm"]]
            if int(cell["op_index"]) != len(arm.ops_done):
                raise StateError("operation completion out of plan order")
            arm.ops_done.append(cell)

    def _on_disk_commit(self, body: dict[str, Any]) -> None:
        pend = self.pending.get(body["rel"])
        if pend is None:
            raise StateError("commit for a write that was not reserved")
        if int(body["size"]) != pend.size or body["sha256"] != pend.sha256:
            raise StateError("committed bytes differ from the reservation")
        self._commit_pending(pend)

    def _on_disk_abort(self, body: dict[str, Any]) -> None:
        if body["rel"] not in self.pending:
            raise StateError("abort for a write that was not reserved")
        del self.pending[body["rel"]]

    def _on_abort(self, body: dict[str, Any]) -> None:
        self.aborts.append(dict(body))


def new_state(genesis_body: dict[str, Any], epoch_id: str) -> EpochState:
    return EpochState(
        epoch_id=epoch_id,
        epoch_start_digest=str(genesis_body["epoch_start_digest"]),
        plans=copy.deepcopy(genesis_body["plans"]),
        disk_caps=copy.deepcopy(genesis_body["disk_caps"]),
        arms={"M": ArmState(), "T": ArmState()},
    )


# --------------------------------------------------------------------------
# Journal file
# --------------------------------------------------------------------------


def _line(record: dict[str, Any]) -> bytes:
    raw = canonical.canonical_bytes(record) + b"\n"
    if len(raw) > MAX_RECORD_BYTES:
        raise JournalError("journal record exceeds the record size bound")
    return raw


class Journal:
    """Append-only, fsynced, hash-chained journal with a durable head."""

    def __init__(self, fs: fsroot.RootFS, *, epoch_id: str, seed: str) -> None:
        self._fs = fs
        self._epoch_id = epoch_id
        self._seed = seed
        self._seq = -1
        self._last = seed
        self._size = 0
        self._fd: int | None = None
        self._failed = False

    @property
    def seq(self) -> int:
        return self._seq

    def _record(self, kind: str, body: dict[str, Any]) -> dict[str, Any]:
        if kind not in RECORD_TYPES:
            raise JournalError(f"unknown record type {kind}")
        record: dict[str, Any] = {
            "seq": self._seq + 1,
            "prev": self._last,
            "type": kind,
            "body": body,
        }
        record["digest"] = _record_digest(record)
        return record

    def _write_head(self) -> None:
        head = canonical.canonical_bytes(
            {"epoch_id": self._epoch_id, "seq": self._seq, "digest": self._last}
        )
        if self._fs.exists(HEAD_TMP_REL):
            self._fs.remove(HEAD_TMP_REL)
        self._fs.create_exclusive(HEAD_TMP_REL, head)
        self._fs.rename(HEAD_TMP_REL, HEAD_REL, replace=True)

    def create(self, genesis_body: dict[str, Any]) -> dict[str, Any]:
        record = self._record("GENESIS", genesis_body)
        line = _line(record)
        self._fs.create_exclusive(JOURNAL_REL, line)
        self._seq, self._last, self._size = 0, record["digest"], len(line)
        self._fd = self._fs.open_append(JOURNAL_REL)
        self._write_head()
        return record

    def append(self, kind: str, body: dict[str, Any]) -> dict[str, Any]:
        """Durably append one record; returns only after fsync + head."""
        if self._failed or self._fd is None:
            raise JournalError("journal is not writable (earlier failure or not open)")
        record = self._record(kind, body)
        line = _line(record)
        if self._size + len(line) > JOURNAL_ALLOWANCE:
            self._failed = True
            raise JournalError("journal would exceed its reserved disk allowance")
        try:
            view = memoryview(line)
            while view:
                written = os.write(self._fd, view)
                view = view[written:]
            os.fsync(self._fd)
        except OSError as exc:
            self._failed = True
            raise JournalError(f"journal append failed durably: {exc}") from exc
        self._seq, self._last = record["seq"], record["digest"]
        self._size += len(line)
        try:
            self._write_head()
        except (OSError, fsroot.ContainmentError) as exc:
            self._failed = True
            raise JournalError(f"journal head update failed: {exc}") from exc
        return record

    def close(self) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None

    def load(self) -> list[dict[str, Any]]:
        """Read, verify and position after the last record (no repair)."""
        raw = self._fs.read_bytes(JOURNAL_REL, max_bytes=JOURNAL_ALLOWANCE)
        if not raw.endswith(b"\n"):
            raise JournalError("journal has a torn final record; epoch BLOCKED for review")
        lines = raw[:-1].split(b"\n")
        records: list[dict[str, Any]] = []
        prev = self._seed
        for index, line in enumerate(lines):
            try:
                record = canonical.loads_bytes_strict(line)
            except canonical.CanonicalError as exc:
                raise JournalError(f"journal record {index} is corrupt: {exc}") from exc
            if canonical.canonical_bytes(record) != line:
                raise JournalError(f"journal record {index} is not canonical")
            if not isinstance(record, dict) or set(record) != {
                "seq",
                "prev",
                "type",
                "body",
                "digest",
            }:
                raise JournalError(f"journal record {index} has the wrong shape")
            if record["seq"] != index or record["prev"] != prev:
                raise JournalError(f"journal chain broken at record {index}")
            if _record_digest(record) != record["digest"]:
                raise JournalError(f"journal record {index} digest mismatch")
            prev = record["digest"]
            records.append(record)
        if not records or records[0]["type"] != "GENESIS":
            raise JournalError("journal does not start with GENESIS")
        head_raw = self._fs.read_bytes(HEAD_REL, max_bytes=HEAD_ALLOWANCE)
        try:
            head = canonical.loads_bytes_strict(head_raw)
        except canonical.CanonicalError as exc:
            raise JournalError(f"journal head is corrupt: {exc}") from exc
        if not isinstance(head, dict) or head.get("epoch_id") != self._epoch_id:
            raise JournalError("journal head binds a different epoch")
        head_seq = head.get("seq")
        if type(head_seq) is not int or head_seq < 0:
            raise JournalError("journal head is malformed")
        last = len(records) - 1
        if last < head_seq:
            raise JournalError(
                f"journal truncated/rolled back: {last + 1} records < durable head {head_seq + 1}"
            )
        if last > head_seq + 1:
            raise JournalError("journal is more than one record ahead of its durable head")
        if records[head_seq]["digest"] != head.get("digest"):
            raise JournalError("journal head digest does not match the journal")
        self._seq, self._last, self._size = last, prev, len(raw)
        self._fd = self._fs.open_append(JOURNAL_REL)
        if self._fs.exists(HEAD_TMP_REL):
            self._fs.remove(HEAD_TMP_REL)
        self._write_head()
        return records
