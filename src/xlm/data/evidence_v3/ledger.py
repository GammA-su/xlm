"""v3 prospective execution ledger: zero-start, durable, idempotent.

At epoch start v3 network counters are zero because no v3 network event
has yet occurred. Old v2 receipts are adopted_planning_observation /
historical_provenance / closed_non_executable_lineage: referenced from v3
provenance but excluded from the v3 execution-budget interval.

Every v3 event is durably metered: request reservation, actual physical
request, redirects, retries, failures, response-body bytes, partial
bodies, decompressed work, scanned rows, elapsed active machine time,
scratch/final disk reservations, memory supervision events.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from xlm.data.evidence_v2 import budgets, canonical
from xlm.data.evidence_v3 import frozen_v3


class LedgerError(ValueError):
    """Any ledger conservation, replay, or budget violation: refuse."""


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


def _check_event(event: V3Event) -> None:
    if not event.event_id:
        raise LedgerError("v3 event needs an id")
    if event.kind not in ("request", "body", "retry", "redirect", "failure", "work"):
        raise LedgerError(f"unknown v3 event kind: {event.kind}")
    if event.category not in ("footer", "data"):
        raise LedgerError(f"unknown v3 event category: {event.category}")
    for name in ("requests", "body_bytes", "decompressed_bytes", "scanned_rows"):
        value = getattr(event, name)
        if type(value) is not int or value < 0:
            raise LedgerError(f"v3 event {name} must be a non-negative integer")
    if not isinstance(event.elapsed_seconds, (int, float)) or event.elapsed_seconds < 0:
        raise LedgerError("v3 event elapsed must be non-negative")
    for name in ("scratch_bytes", "final_bytes"):
        value = getattr(event, name)
        if type(value) is not int or value < 0:
            raise LedgerError(f"v3 event {name} must be a non-negative integer")


class V3Ledger:
    """Durable per-arm v3 execution ledger with crash conservation.

    Wraps ArmLedger counters plus an explicit event log. Same event ID +
    same event replays idempotently; same ID + conflict refuses. Open
    in-flight reservations are conserved on load (never refunded).
    """

    def __init__(self, arm: str, *, use_v22_m: bool = False, use_v21_t: bool = False) -> None:
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
            # v2.2 M footer/file ceiling is already transcribed (16); the
            # use_v22_m flag only documents the lineage choice.
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

    # -- event application -------------------------------------------------
    def apply(self, event: V3Event) -> bool:
        """Apply one event; idempotent on identical replay, refuse on conflict."""
        _check_event(event)
        prior = self.events.get(event.event_id)
        current = _event_dict(event)
        if prior is not None:
            if prior == current:
                self.duplicates.append(event.event_id)
                return False
            raise LedgerError(f"conflicting replay of {event.event_id}: refusing")
        # Durably reserve the attempt BEFORE charging shared counters.
        token = f"v3-{event.event_id}"
        self.in_flight[token] = dict(current)
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
            # Conservation: the failed attempt stays reserved as in-flight
            # uncertainty; it is never silently dropped.
            raise LedgerError(f"v3 budget refusal for {event.event_id}: {exc}") from exc
        del self.in_flight[token]
        self.events[event.event_id] = current
        return True

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
        }
        return canonical.write_canonical_json(path, body)

    @staticmethod
    def load(path: Path, arm: str) -> V3Ledger:
        body = canonical.loads_bytes_strict(path.read_bytes())
        if not isinstance(body, dict) or body.get("arm") != arm:
            raise LedgerError("v3 ledger file does not match this arm")
        if body.get("epoch_id") != frozen_v3.EPOCH_ID:
            raise LedgerError("v3 ledger file binds a different epoch")
        fresh = V3Ledger(arm)
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
                )
            )
        # Crash conservation: open in-flight work from the saved file is
        # reserved (charged) rather than refunded.
        live = body.get("live")
        if isinstance(live, dict):
            replayed_requests = fresh.ledger.snapshot()["requests"]
            if replayed_requests > int(live.get("requests", -1)):
                raise LedgerError("v3 ledger live snapshot older than replayed events")
        for raw in body.get("in_flight", []):
            token = str(raw.get("token", "?"))
            fresh.in_flight[token] = {k: v for k, v in raw.items() if k != "token"}
            reserved = int(raw.get("body_bytes", 0))
            if reserved:
                try:
                    fresh.ledger.charge_transfer(
                        str(raw.get("file", "unknown")), reserved, kind="footer"
                    )
                except budgets.BudgetRefusal as exc:
                    raise LedgerError(f"crash reservation exceeds caps: {exc}") from exc
        fresh.duplicates = list(body.get("duplicates", []))
        return fresh


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
    }


def new_m_ledger() -> V3Ledger:
    return V3Ledger("M", use_v22_m=True)


def new_t_ledger() -> V3Ledger:
    return V3Ledger("T", use_v21_t=True)
