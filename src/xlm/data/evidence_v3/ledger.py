"""v3 durable execution ledger: reserve-before-I/O with crash conservation.

At epoch start v3 network counters are zero because no v3 network event
has yet occurred. Old v2 receipts are adopted_planning_observation /
historical_provenance / closed_non_executable_lineage: referenced from v3
provenance but excluded from the v3 execution-budget interval.

Durability contract (Astra remediation):

- Every event transition is durably persisted (append + fsync) BEFORE the
  caller may proceed. ``apply()`` fsyncs before returning; the multi-step
  ``reserve_event()`` flow fsyncs the RESERVED record before any transport
  may issue the request.
- State machine per event: RESERVED -> ISSUED -> RESPONSE_STARTED ->
  COMPLETE, with FAILED / REFUSED terminals and CRASH_RESERVED for work
  found open at reload. Crash/restart conserves attempted work: no refund,
  category preserved, request count preserved, elapsed preserved, partial
  bytes preserved.
- Body category (footer/control vs data) is persisted explicitly on every
  line and never inferred or hard-coded during recovery.
- Each journal line carries a hash chain (prev_digest -> line_digest);
  truncation, reordering, corruption, epoch mismatch, or rollback behind
  the bound snapshot refuses.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from xlm.data.evidence_v2 import budgets, canonical
from xlm.data.evidence_v3 import frozen_v3


class LedgerError(ValueError):
    """Any ledger conservation, replay, or budget violation: refuse."""


STATUSES = (
    "RESERVED",
    "ISSUED",
    "RESPONSE_STARTED",
    "COMPLETE",
    "FAILED",
    "REFUSED",
    "CRASH_RESERVED",
)

_OPEN_STATUSES = ("RESERVED", "ISSUED", "RESPONSE_STARTED")


@dataclass(frozen=True)
class V3Event:
    """One metered v3 execution event."""

    event_id: str
    kind: str  # "request" | "body" | "retry" | "redirect" | "failure" | "work"
    file: str
    category: str  # "footer" | "data"
    requests: int = 0
    body_bytes: int = 0
    decompressed_bytes: int = 0
    scanned_rows: int = 0
    elapsed_seconds: float = 0.0
    scratch_bytes: int = 0
    final_bytes: int = 0
    # Extended durable fields (persisted by the journal; defaulted for compat).
    phase: str = "P"
    logical_request: int = 0
    physical_attempt: int = 0
    redirect_count: int = 0
    retry_count: int = 0


def _check_event(event: V3Event) -> None:
    if not event.event_id:
        raise LedgerError("v3 event needs an id")
    if event.kind not in ("request", "body", "retry", "redirect", "failure", "work"):
        raise LedgerError(f"unknown v3 event kind: {event.kind}")
    if event.category not in ("footer", "data"):
        raise LedgerError(f"unknown v3 event category: {event.category}")
    if event.phase not in ("P", "D"):
        raise LedgerError(f"unknown v3 event phase: {event.phase}")
    for name in (
        "requests",
        "body_bytes",
        "decompressed_bytes",
        "scanned_rows",
        "logical_request",
        "physical_attempt",
        "redirect_count",
        "retry_count",
    ):
        value = getattr(event, name)
        if type(value) is not int or value < 0:
            raise LedgerError(f"v3 event {name} must be a non-negative integer")
    if not isinstance(event.elapsed_seconds, (int, float)) or event.elapsed_seconds < 0:
        raise LedgerError("v3 event elapsed must be non-negative")
    for name in ("scratch_bytes", "final_bytes"):
        value = getattr(event, name)
        if type(value) is not int or value < 0:
            raise LedgerError(f"v3 event {name} must be a non-negative integer")


def _event_dict(event: V3Event) -> dict[str, Any]:
    return {
        "event_id": event.event_id,
        "kind": event.kind,
        "file": event.file,
        "category": event.category,
        "requests": event.requests,
        "body_bytes": event.body_bytes,
        "decompressed_bytes": event.decompressed_bytes,
        "scanned_rows": event.scanned_rows,
        "elapsed_seconds": float(event.elapsed_seconds),
        "scratch_bytes": event.scratch_bytes,
        "final_bytes": event.final_bytes,
        "phase": event.phase,
        "logical_request": event.logical_request,
        "physical_attempt": event.physical_attempt,
        "redirect_count": event.redirect_count,
        "retry_count": event.retry_count,
    }


def _journal_paths(directory: Path) -> tuple[Path, Path]:
    return directory / "journal.jsonl", directory / "snapshot.json"


def _append_line(journal: Path, record: dict[str, Any]) -> dict[str, Any]:
    """Append one canonical JSON line and fsync before returning."""
    journal.parent.mkdir(parents=True, exist_ok=True)
    payload = canonical.canonical_bytes(record) + b"\n"
    with journal.open("ab") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    return record


def _read_lines(journal: Path) -> list[dict[str, Any]]:
    raw = journal.read_bytes().split(b"\n")
    lines: list[dict[str, Any]] = []
    for index, blob in enumerate(raw):
        if not blob:
            continue
        try:
            record = canonical.loads_bytes_strict(blob)
        except Exception as exc:
            raise LedgerError(f"journal line {index} is corrupt: {exc}") from exc
        if not isinstance(record, dict):
            raise LedgerError(f"journal line {index} is not a mapping")
        lines.append(record)
    return lines


def _verify_chain(lines: list[dict[str, Any]], *, epoch_id: str, arm: str) -> None:
    prev = "GENESIS"
    for index, record in enumerate(lines):
        for key in (
            "seq",
            "epoch_id",
            "arm",
            "event_id",
            "status",
            "body_category",
            "prev_digest",
            "line_digest",
        ):
            if key not in record:
                raise LedgerError(f"journal line {index} is missing {key}")
        if record["seq"] != index:
            raise LedgerError(f"journal line {index} has non-monotonic seq")
        if record["epoch_id"] != epoch_id:
            raise LedgerError(f"journal line {index} binds a different epoch")
        if record["arm"] != arm:
            raise LedgerError(f"journal line {index} binds a different arm")
        if record["status"] not in STATUSES:
            raise LedgerError(f"journal line {index} has unknown status")
        if record["body_category"] not in ("footer", "data"):
            raise LedgerError(f"journal line {index} has unknown body category")
        if record["prev_digest"] != prev:
            raise LedgerError(f"journal line {index} breaks the hash chain")
        check = dict(record)
        expect = check.pop("line_digest")
        got = hashlib.sha256(canonical.canonical_bytes(check)).hexdigest()
        if got != expect:
            raise LedgerError(f"journal line {index} is corrupt (digest mismatch)")
        prev = str(expect)


class V3Ledger:
    """Durable per-arm v3 execution ledger with crash conservation.

    Wraps ArmLedger counters plus an explicit event log. Same event ID +
    same event replays idempotently; same ID + conflict refuses. When bound
    to a journal directory (``path``), every transition is appended and
    fsynced before the caller proceeds, so ``save`` + ``apply`` + ``load``
    round-trips preserve requests, category, bytes, and elapsed.
    """

    def __init__(
        self,
        arm: str,
        *,
        use_v22_m: bool = False,
        use_v21_t: bool = False,
        path: Path | None = None,
    ) -> None:
        if arm == "M":
            ceilings = dict(frozen_v3.ARM_M_CAPS)
            inner = budgets.ArmLedger(
                "M",
                ceilings,
                requests_key="requests_total",
                transfer_arm_key="response_body_bytes_total",
                decompressed_arm_key="decompressed_bytes_arm",
                disk_scratch_key="disk_scratch_bytes",
                disk_final_key="disk_final_bytes",
                disk_combined_key="disk_combined_bytes",
                time_arm_key="time_seconds_arm",
            )
            _ = use_v22_m
        elif arm == "T":
            ceilings = dict(frozen_v3.ARM_T_CAPS)
            inner = budgets.ArmLedger(
                "T",
                ceilings,
                requests_key="requests_arm_max",
                transfer_arm_key="transfer_bytes_arm_max",
                decompressed_arm_key="decompressed_bytes_arm_max",
                disk_scratch_key="disk_scratch_bytes",
                disk_final_key="disk_final_bytes",
                disk_combined_key="disk_combined_bytes",
                time_arm_key="time_seconds_arm",
                combined_file_requests_key="requests_per_file_max",
                data_file_key="data_bytes_per_file_max",
                data_arm_key="data_bytes_arm_max",
                total_file_key="transfer_bytes_per_file_max",
                total_arm_key="transfer_bytes_arm_max",
                footer_file_key="footer_bytes_per_file_max",
                footer_arm_key="footer_bytes_total_max",
            )
            _ = use_v21_t
        else:
            raise LedgerError(f"unknown arm: {arm}")
        self.arm = arm
        self.ledger = inner
        self.events: dict[str, dict[str, Any]] = {}
        self.in_flight: dict[str, dict[str, Any]] = {}
        self.duplicates: list[str] = []
        self._dir: Path | None = Path(path) if path is not None else None
        self._seq = 0
        self._head = "GENESIS"
        if self._dir is not None:
            journal, _ = _journal_paths(self._dir)
            if journal.exists():
                raise LedgerError(f"ledger directory already holds a journal: {self._dir}")

    # -- journal internals ---------------------------------------------------
    def _record(
        self,
        event: V3Event,
        status: str,
        *,
        body_bytes: int = 0,
        elapsed_seconds: float = 0.0,
        decompressed_bytes: int = 0,
        scanned_rows: int = 0,
    ) -> dict[str, Any]:
        if self._dir is None:
            raise LedgerError("ledger is not bound to a journal directory")
        journal, _ = _journal_paths(self._dir)
        record: dict[str, Any] = {
            "seq": self._seq,
            "epoch_id": frozen_v3.EPOCH_ID,
            "arm": self.arm,
            "event_id": event.event_id,
            "status": status,
            "file": event.file,
            "phase": event.phase,
            "kind": event.kind,
            "logical_request": event.logical_request,
            "physical_attempt": event.physical_attempt,
            "request_count": event.requests,
            "redirect_count": event.redirect_count,
            "retry_count": event.retry_count,
            "body_category": event.category,
            "body_bytes": body_bytes,
            "elapsed_seconds": float(elapsed_seconds),
            "decompressed_bytes": decompressed_bytes,
            "scanned_rows": scanned_rows,
            "scratch_bytes": event.scratch_bytes,
            "final_bytes": event.final_bytes,
            "prev_digest": self._head,
        }
        record["line_digest"] = hashlib.sha256(canonical.canonical_bytes(record)).hexdigest()
        _append_line(journal, record)
        self._seq += 1
        self._head = str(record["line_digest"])
        return record

    def _charge(self, event: V3Event) -> None:
        try:
            if event.requests:
                for _ in range(event.requests):
                    self.ledger.charge_file_request(event.file, kind=event.category)
            if event.body_bytes:
                self.ledger.charge_transfer(event.file, event.body_bytes, kind=event.category)
            if event.decompressed_bytes:
                self.ledger.charge_decompressed(
                    event.file, event.decompressed_bytes, kind=event.category
                )
            if event.scanned_rows:
                arm_key = "max_scan_rows_arm" if self.arm == "M" else "scan_rows_arm_max"
                self.ledger.charge_scanned(event.file, event.scanned_rows, arm_key=arm_key)
            if event.elapsed_seconds:
                self.ledger.charge_time(float(event.elapsed_seconds), kind=event.category)
            if event.scratch_bytes or event.final_bytes:
                self.ledger.reserve_disk(
                    scratch=event.scratch_bytes, final=event.final_bytes, kind=event.category
                )
        except budgets.BudgetRefusal as exc:
            raise LedgerError(f"v3 budget refusal for {event.event_id}: {exc}") from exc

    # -- event application -------------------------------------------------
    def apply(self, event: V3Event) -> bool:
        """Apply one event; idempotent on identical replay, refuse on conflict.

        When journal-bound, the COMPLETE record is appended and fsynced
        before this returns, so a later ``load`` observes it.
        """
        _check_event(event)
        prior = self.events.get(event.event_id)
        current = _event_dict(event)
        if prior is not None:
            if prior == current:
                self.duplicates.append(event.event_id)
                return False
            raise LedgerError(f"conflicting replay of {event.event_id}: refusing")
        token = f"v3-{event.event_id}"
        self.in_flight[token] = dict(current)
        try:
            self._charge(event)
        except LedgerError:
            if self._dir is not None:
                self._record(
                    event,
                    "FAILED",
                    body_bytes=event.body_bytes,
                    elapsed_seconds=event.elapsed_seconds,
                )
                del self.in_flight[token]
            else:
                del self.in_flight[token]
            raise
        if self._dir is not None:
            self._record(
                event,
                "COMPLETE",
                body_bytes=event.body_bytes,
                elapsed_seconds=event.elapsed_seconds,
                decompressed_bytes=event.decompressed_bytes,
                scanned_rows=event.scanned_rows,
            )
        del self.in_flight[token]
        self.events[event.event_id] = current
        return True

    def reserve_event(self, event: V3Event) -> str:
        """Reserve before I/O: charge request counts and fsync RESERVED first.

        The returned token must be used for subsequent ``mark_*`` calls.
        Only after this returns may transport issue the request.
        """
        _check_event(event)
        if event.event_id in self.events:
            raise LedgerError(f"event {event.event_id} is already settled: refusing")
        token = f"v3-{event.event_id}"
        if token in self.in_flight:
            raise LedgerError(f"event {event.event_id} is already reserved: refusing")
        try:
            if event.requests:
                for _ in range(event.requests):
                    self.ledger.charge_file_request(event.file, kind=event.category)
            if event.scratch_bytes or event.final_bytes:
                self.ledger.reserve_disk(
                    scratch=event.scratch_bytes, final=event.final_bytes, kind=event.category
                )
        except budgets.BudgetRefusal as exc:
            if self._dir is not None:
                self._record(event, "REFUSED")
            raise LedgerError(f"v3 budget refusal for {event.event_id}: {exc}") from exc
        self.in_flight[token] = dict(_event_dict(event))
        if self._dir is not None:
            self._record(event, "RESERVED")
        return token

    def mark_issued(self, token: str) -> None:
        self._transition(token, "ISSUED")

    def mark_response_started(
        self, token: str, *, body_bytes_delta: int = 0, elapsed_delta: float = 0.0
    ) -> None:
        """Meter body bytes incrementally while reading, not after parse.

        Deltas are charged immediately and journaled, so invalid-status,
        redirect, error, partial, and retry bodies all count even when the
        response is later rejected. A validation failure never erases
        consumed bytes.
        """
        event = self._require_open(token)
        if body_bytes_delta:
            self.ledger.charge_transfer(event.file, body_bytes_delta, kind=event.category)
        if elapsed_delta:
            self.ledger.charge_time(float(elapsed_delta), kind=event.category)
        if self._dir is not None:
            self._record(
                event,
                "RESPONSE_STARTED",
                body_bytes=body_bytes_delta,
                elapsed_seconds=elapsed_delta,
            )

    def complete_event(
        self, token: str, *, body_bytes: int = 0, elapsed_seconds: float = 0.0
    ) -> None:
        event = self._require_open(token)
        if body_bytes:
            self.ledger.charge_transfer(event.file, body_bytes, kind=event.category)
        if elapsed_seconds:
            self.ledger.charge_time(float(elapsed_seconds), kind=event.category)
        if event.decompressed_bytes:
            self.ledger.charge_decompressed(
                event.file, event.decompressed_bytes, kind=event.category
            )
        if event.scanned_rows:
            arm_key = "max_scan_rows_arm" if self.arm == "M" else "scan_rows_arm_max"
            self.ledger.charge_scanned(event.file, event.scanned_rows, arm_key=arm_key)
        if self._dir is not None:
            self._record(
                event,
                "COMPLETE",
                body_bytes=body_bytes,
                elapsed_seconds=elapsed_seconds,
                decompressed_bytes=event.decompressed_bytes,
                scanned_rows=event.scanned_rows,
            )
        self.events[event.event_id] = _event_dict(event)
        del self.in_flight[token]

    def fail_event(self, token: str, *, body_bytes: int = 0, elapsed_seconds: float = 0.0) -> None:
        """Failed/partial reads still charge observed bytes and time."""
        event = self._require_open(token)
        if body_bytes:
            self.ledger.charge_transfer(event.file, body_bytes, kind=event.category)
        if elapsed_seconds:
            self.ledger.charge_time(float(elapsed_seconds), kind=event.category)
        if self._dir is not None:
            self._record(event, "FAILED", body_bytes=body_bytes, elapsed_seconds=elapsed_seconds)
        self.events[event.event_id] = _event_dict(event)
        del self.in_flight[token]

    def _require_open(self, token: str) -> V3Event:
        raw = self.in_flight.get(token)
        if raw is None:
            raise LedgerError(f"unknown in-flight token: {token}")
        return V3Event(
            event_id=str(raw["event_id"]),
            kind=str(raw["kind"]),
            file=str(raw["file"]),
            category=str(raw["category"]),
            requests=int(raw.get("requests", 0)),
            body_bytes=int(raw.get("body_bytes", 0)),
            decompressed_bytes=int(raw.get("decompressed_bytes", 0)),
            scanned_rows=int(raw.get("scanned_rows", 0)),
            elapsed_seconds=float(raw.get("elapsed_seconds", 0.0)),
            scratch_bytes=int(raw.get("scratch_bytes", 0)),
            final_bytes=int(raw.get("final_bytes", 0)),
            phase=str(raw.get("phase", "P")),
            logical_request=int(raw.get("logical_request", 0)),
            physical_attempt=int(raw.get("physical_attempt", 0)),
            redirect_count=int(raw.get("redirect_count", 0)),
            retry_count=int(raw.get("retry_count", 0)),
        )

    def _transition(self, token: str, status: str) -> None:
        event = self._require_open(token)
        if self._dir is not None:
            self._record(event, status)

    def import_v2_counters(self, *_: Any, **__: Any) -> None:
        """Old network usage is never imported as v3 consumption: refuse."""
        raise LedgerError("v2 counters cannot be imported into the v3 execution interval")

    # -- durability ----------------------------------------------------------
    def snapshot(self) -> dict[str, Any]:
        return {
            "arm": self.arm,
            "epoch": frozen_v3.EPOCH_ID,
            "counters": self.ledger.snapshot(),
            "event_count": len(self.events),
            "in_flight_count": len(self.in_flight),
            "journal_seq": self._seq,
            "journal_head": self._head,
            "duplicates_not_recharged": list(self.duplicates),
        }

    def save(self, path: Path) -> dict[str, int | str]:
        body = {
            "kind": "essential_web_evidence_v3_ledger",
            "protocol_version": frozen_v3.PROTOCOL_VERSION,
            "epoch_id": frozen_v3.EPOCH_ID,
            "arm": self.arm,
            "events": [dict(v) for _, v in sorted(self.events.items())],
            "live": self.ledger.snapshot(),
            "in_flight": [dict(token=k, **v) for k, v in sorted(self.in_flight.items())],
            "duplicates": list(self.duplicates),
            "journal_seq": self._seq,
            "journal_head": self._head,
        }
        return canonical.write_canonical_json(path, body)

    @staticmethod
    def load(path: Path, arm: str, *, journal_dir: Path | None = None) -> V3Ledger:
        """Reload ledger state, replaying the journal when bound.

        Open journal statuses become CRASH_RESERVED conservation records:
        request counts, category, bytes, and elapsed are preserved, never
        refunded. Truncated/corrupt journals, epoch mismatch, or rollback
        behind the snapshot refuse.
        """
        body = canonical.loads_bytes_strict(Path(path).read_bytes())
        if not isinstance(body, dict) or body.get("arm") != arm:
            raise LedgerError("v3 ledger file does not match this arm")
        if body.get("epoch_id") != frozen_v3.EPOCH_ID:
            raise LedgerError("v3 ledger file binds a different epoch")
        directory = Path(journal_dir) if journal_dir is not None else None
        fresh = V3Ledger(arm)
        if directory is not None:
            journal, _ = _journal_paths(directory)
            if not journal.exists():
                raise LedgerError("journal directory holds no journal: refusing (loss?)")
            lines = _read_lines(journal)
            _verify_chain(lines, epoch_id=frozen_v3.EPOCH_ID, arm=arm)
            saved_seq = int(body.get("journal_seq", 0))
            if len(lines) < saved_seq:
                raise LedgerError("journal is shorter than the bound snapshot: rollback/loss")
            if saved_seq > 0:
                saved_head = str(body.get("journal_head", ""))
                if lines[saved_seq - 1].get("line_digest") != saved_head:
                    raise LedgerError("journal prefix diverges from the bound snapshot")
            # The journal is the source of truth: replay every line in order.
            # Requests charge once per event: on the terminal line when one
            # exists, otherwise on the open RESERVED line (crash flow).
            # Disk reservations charge on RESERVED lines. Body/elapsed/
            # decompression/scan deltas sum over non-RESERVED lines.
            terminal_status: dict[str, str] = {}
            for record in lines:
                if record["status"] in ("COMPLETE", "FAILED", "REFUSED"):
                    terminal_status[str(record["event_id"])] = str(record["status"])
            last_status: dict[str, str] = {}
            settled: dict[str, dict[str, Any]] = {}
            for record in lines:
                event_id = str(record["event_id"])
                category = str(record["body_category"])
                filename = str(record["file"])
                status = str(record["status"])
                if status == "RESERVED":
                    if event_id not in terminal_status:
                        for _ in range(int(record["request_count"])):
                            fresh.ledger.charge_file_request(filename, kind=category)
                    if int(record.get("scratch_bytes", 0)) or int(record.get("final_bytes", 0)):
                        fresh.ledger.reserve_disk(
                            scratch=int(record.get("scratch_bytes", 0)),
                            final=int(record.get("final_bytes", 0)),
                            kind=category,
                        )
                elif status in ("RESPONSE_STARTED", "COMPLETE", "FAILED", "REFUSED"):
                    if status in ("COMPLETE", "FAILED", "REFUSED"):
                        for _ in range(int(record["request_count"])):
                            fresh.ledger.charge_file_request(filename, kind=category)
                    if int(record.get("body_bytes", 0)):
                        fresh.ledger.charge_transfer(
                            filename, int(record["body_bytes"]), kind=category
                        )
                    if float(record.get("elapsed_seconds", 0.0)):
                        fresh.ledger.charge_time(float(record["elapsed_seconds"]), kind=category)
                    if int(record.get("decompressed_bytes", 0)):
                        fresh.ledger.charge_decompressed(
                            filename, int(record["decompressed_bytes"]), kind=category
                        )
                    if int(record.get("scanned_rows", 0)):
                        arm_key = "max_scan_rows_arm" if arm == "M" else "scan_rows_arm_max"
                        fresh.ledger.charge_scanned(
                            filename, int(record["scanned_rows"]), arm_key=arm_key
                        )
                    if status in ("COMPLETE", "FAILED", "REFUSED"):
                        settled[event_id] = {
                            "event_id": event_id,
                            "kind": str(record.get("kind", "request")),
                            "file": filename,
                            "category": category,
                            "requests": int(record["request_count"]),
                            "body_bytes": int(record.get("body_bytes", 0)),
                            "decompressed_bytes": int(record.get("decompressed_bytes", 0)),
                            "scanned_rows": int(record.get("scanned_rows", 0)),
                            "elapsed_seconds": float(record.get("elapsed_seconds", 0.0)),
                            "scratch_bytes": int(record.get("scratch_bytes", 0)),
                            "final_bytes": int(record.get("final_bytes", 0)),
                            "phase": str(record.get("phase", "P")),
                            "logical_request": int(record.get("logical_request", 0)),
                            "physical_attempt": int(record.get("physical_attempt", 0)),
                            "redirect_count": int(record.get("redirect_count", 0)),
                            "retry_count": int(record.get("retry_count", 0)),
                        }
                last_status[event_id] = status
            for event_id, status in sorted(last_status.items()):
                if status in _OPEN_STATUSES:
                    open_rec = next(r for r in reversed(lines) if str(r["event_id"]) == event_id)
                    fresh.in_flight[f"v3-{event_id}-crash"] = {
                        "event_id": event_id,
                        "status": "CRASH_RESERVED",
                        "body_category": str(open_rec["body_category"]),
                        "file": str(open_rec["file"]),
                        "phase": str(open_rec.get("phase", "P")),
                    }
            fresh.events = settled
            fresh._seq = len(lines)
            fresh._head = str(lines[-1]["line_digest"]) if lines else "GENESIS"
            fresh.duplicates = list(body.get("duplicates", []))
            return fresh
        # Legacy in-memory snapshot path (no journal): replay + conserve.
        for raw in body.get("events", []):
            fresh.apply(
                V3Event(
                    event_id=str(raw["event_id"]),
                    kind=str(raw["kind"]),
                    file=str(raw["file"]),
                    category=str(raw["category"]),
                    requests=int(raw.get("requests", 0)),
                    body_bytes=int(raw.get("body_bytes", 0)),
                    decompressed_bytes=int(raw.get("decompressed_bytes", 0)),
                    scanned_rows=int(raw.get("scanned_rows", 0)),
                    elapsed_seconds=float(raw.get("elapsed_seconds", 0.0)),
                    scratch_bytes=int(raw.get("scratch_bytes", 0)),
                    final_bytes=int(raw.get("final_bytes", 0)),
                    phase=str(raw.get("phase", "P")),
                    logical_request=int(raw.get("logical_request", 0)),
                    physical_attempt=int(raw.get("physical_attempt", 0)),
                    redirect_count=int(raw.get("redirect_count", 0)),
                    retry_count=int(raw.get("retry_count", 0)),
                )
            )
        live = body.get("live")
        if isinstance(live, dict):
            replayed_requests = fresh.ledger.snapshot()["requests"]
            if replayed_requests > int(live.get("requests", -1)):
                raise LedgerError("v3 ledger live snapshot older than replayed events")
        for raw in body.get("in_flight", []):
            token = str(raw.get("token", "?"))
            category = raw.get("category", raw.get("body_category", "footer"))
            if category not in ("footer", "data"):
                raise LedgerError("in-flight record has unknown body category")
            fresh.in_flight[token] = {k: v for k, v in raw.items() if k != "token"}
            reserved = int(raw.get("body_bytes", 0))
            if reserved:
                try:
                    fresh.ledger.charge_transfer(
                        str(raw.get("file", "unknown")), reserved, kind=str(category)
                    )
                except budgets.BudgetRefusal as exc:
                    raise LedgerError(f"crash reservation exceeds caps: {exc}") from exc
        fresh.duplicates = list(body.get("duplicates", []))
        return fresh


def new_m_ledger(path: Path | None = None) -> V3Ledger:
    return V3Ledger("M", use_v22_m=True, path=path)


def new_t_ledger(path: Path | None = None) -> V3Ledger:
    return V3Ledger("T", use_v21_t=True, path=path)
