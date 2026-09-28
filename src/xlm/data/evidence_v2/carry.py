"""Durable cross-version Arm-T carry-in ledger and reconciliation.

One durable ledger spans v2.0 attempts, v2.1 adoption, future
revalidation, future data execution, retries, redirects, failures, and
process restarts/resumes. Counters are never reset on version change;
every adopted historical event carries an explicit attempt identity;
duplicate adoption never double-charges; unknown usage is never zero.

Adoption granularity is per (source receipt, file, category): v2.0
receipts identify arm totals plus per-file byte cells, so per-file
request splits that receipts do not break down are recorded with
explicit measurement status (measured vs derived) rather than invented
precision. Persistence replays the entry list into a fresh ledger, so
restarts are idempotent by construction.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from xlm.data.evidence_v2 import budgets, canonical, frozen


class CarryError(ValueError):
    """Any adoption, persistence, or reconciliation violation: refuse."""


@dataclass(frozen=True)
class CarryEntry:
    """One adopted historical cost fact with its source receipt."""

    entry_id: str
    source_receipt: str
    source_path: str
    file: str | None
    category: str
    requests: int
    bytes: int
    measurement: str
    note: str = ""


@dataclass
class UnknownUsage:
    """Explicitly unknown history: blocks execution readiness, never zero."""

    file: str | None
    description: str


@dataclass
class BoundedGap:
    """Bounded residual history with an explicit maximum (tracked, not blocking)."""

    file: str | None
    max_requests: int
    max_bytes: int
    reason: str


def _check_non_negative(name: str, value: int) -> None:
    if type(value) is not int or value < 0:
        raise CarryError(f"carry {name} must be a non-negative integer")


class DurableLedger:
    """Version-spanning ledger: adopted history plus live counters."""

    def __init__(self, ledger: budgets.ArmLedger) -> None:
        self.ledger = ledger
        self.entries: dict[str, CarryEntry] = {}
        self.duplicates: list[str] = []
        self.unknowns: list[UnknownUsage] = []
        self.gaps: list[BoundedGap] = []

    def adopt(self, entry: CarryEntry) -> bool:
        """Adopt one historical fact once; duplicates are logged, not charged."""
        if entry.entry_id in self.entries:
            self.duplicates.append(entry.entry_id)
            return False
        _check_non_negative("requests", entry.requests)
        _check_non_negative("bytes", entry.bytes)
        if entry.category not in ("footer", "data"):
            raise CarryError(f"unknown carry category: {entry.category}")
        if not entry.source_receipt or not entry.entry_id:
            raise CarryError("carry entry needs an id and a source receipt")
        self.ledger.adopt_history(
            kind="footer" if entry.category == "footer" else "data",
            requests=entry.requests,
            response_bytes=entry.bytes,
            source_file=entry.file,
            category=entry.category,
        )
        self.entries[entry.entry_id] = entry
        return True

    def register_unknown(self, file: str | None, description: str) -> None:
        """Explicit unknown history: execution readiness stays blocked."""
        self.unknowns.append(UnknownUsage(file=file, description=description))

    def register_gap(
        self, file: str | None, max_requests: int, max_bytes: int, reason: str
    ) -> None:
        """Bounded residual history with an explicit maximum."""
        _check_non_negative("max_requests", max_requests)
        _check_non_negative("max_bytes", max_bytes)
        self.gaps.append(
            BoundedGap(file=file, max_requests=max_requests, max_bytes=max_bytes, reason=reason)
        )

    def effective(self) -> dict[str, Any]:
        """Ledger snapshot including adopted history (single counters)."""
        return self.ledger.snapshot()

    def readiness(self) -> dict[str, Any]:
        """Execution readiness: unknowns block; overruns block; gaps ride along."""
        reasons: list[str] = []
        for unknown in self.unknowns:
            reasons.append(f"unknown history blocks: {unknown.file}: {unknown.description}")
        snapshot = self.ledger.snapshot()
        for name, cell in snapshot.get("requests_per_file", {}).items():
            total = sum(cell.values())
            cap = self.ledger.ceilings.get("requests_per_file_max")
            if cap is not None and total > cap:
                reasons.append(f"carried per-file requests overrun: {name} has {total} > {cap}")
        horizon = self.ledger.ceilings.get(
            "requests_arm_max", self.ledger.ceilings.get("requests_total", 0)
        )
        if snapshot["requests"] > horizon:
            reasons.append(f"carried arm requests overrun: {snapshot['requests']} > {horizon}")
        return {"ready": not reasons, "reasons": reasons}

    def save(self, path: Any) -> dict[str, int | str]:
        """Persist entries/unknowns/gaps atomically (replayable, idempotent)."""
        body = {
            "kind": "essential_web_evidence_v2_durable_carry",
            "protocol_version": frozen.V21_PROTOCOL_VERSION,
            "arm": self.ledger.arm,
            "entries": [dict(_entry_dict(e)) for e in self.entries.values()],
            "duplicates": list(self.duplicates),
            "unknowns": [dict(file=u.file, description=u.description) for u in self.unknowns],
            "gaps": [
                dict(
                    file=g.file,
                    max_requests=g.max_requests,
                    max_bytes=g.max_bytes,
                    reason=g.reason,
                )
                for g in self.gaps
            ],
        }
        return canonical.write_canonical_json(path, body)

    @staticmethod
    def load(path: Any, ledger: budgets.ArmLedger) -> DurableLedger:
        """Load persisted carry state and replay adoption into a fresh ledger."""
        body = canonical.loads_bytes_strict(path.read_bytes())
        if not isinstance(body, dict) or body.get("arm") != ledger.arm:
            raise CarryError("carry file does not match this arm ledger")
        durable = DurableLedger(ledger)
        for raw in body.get("entries", []):
            durable.adopt(
                CarryEntry(
                    entry_id=str(raw["entry_id"]),
                    source_receipt=str(raw["source_receipt"]),
                    source_path=str(raw.get("source_path", "")),
                    file=raw.get("file"),
                    category=str(raw["category"]),
                    requests=int(raw["requests"]),
                    bytes=int(raw["bytes"]),
                    measurement=str(raw.get("measurement", "measured")),
                    note=str(raw.get("note", "")),
                )
            )
        for raw in body.get("unknowns", []):
            durable.register_unknown(raw.get("file"), str(raw["description"]))
        for raw in body.get("gaps", []):
            durable.register_gap(
                raw.get("file"), int(raw["max_requests"]), int(raw["max_bytes"]), str(raw["reason"])
            )
        return durable

    def reconciliation_receipt(self, *, command: str, exit_status: int = 0) -> dict[str, Any]:
        """Every adopted entry with its source receipt; duplicates/unknowns/gaps."""
        adopted = [
            {
                "entry_id": e.entry_id,
                "source_receipt": e.source_receipt,
                "source_path": e.source_path,
                "file": e.file,
                "category": e.category,
                "requests": e.requests,
                "bytes": e.bytes,
                "measurement": e.measurement,
                "note": e.note,
            }
            for e in self.entries.values()
        ]
        return {
            "kind": "essential_web_evidence_v2_carry_reconciliation",
            "protocol_version": frozen.V21_PROTOCOL_VERSION,
            "freeze_digest": frozen.V21_FREEZE_DIGEST,
            "arm": self.ledger.arm,
            "adopted": adopted,
            "adopted_requests": sum(e.requests for e in self.entries.values()),
            "adopted_bytes": sum(e.bytes for e in self.entries.values()),
            "duplicates_not_recharged": list(self.duplicates),
            "unknowns": [{"file": u.file, "description": u.description} for u in self.unknowns],
            "gaps": [
                {
                    "file": g.file,
                    "max_requests": g.max_requests,
                    "max_bytes": g.max_bytes,
                    "reason": g.reason,
                }
                for g in self.gaps
            ],
            "effective": self.effective(),
            "readiness": self.readiness(),
            "command": command,
            "exit_status": exit_status,
        }


def _entry_dict(entry: CarryEntry) -> dict[str, Any]:
    return {
        "entry_id": entry.entry_id,
        "source_receipt": entry.source_receipt,
        "source_path": entry.source_path,
        "file": entry.file,
        "category": entry.category,
        "requests": entry.requests,
        "bytes": entry.bytes,
        "measurement": entry.measurement,
        "note": entry.note,
    }


def v21_t_carry_entries(
    attempt2_digest: str,
    attempt2_path: str,
    attempt2_files: Mapping[str, Mapping[str, int]],
    prior_digest: str,
    prior_path: str,
    prior_files: Mapping[str, Mapping[str, int]],
) -> list[CarryEntry]:
    """Build the frozen v2.1 T carry-in entry list (54 requests / 2231492 bytes).

    Per-file byte cells come from receipt transfer cells (measured);
    per-file request splits receipts do not break down are recorded as
    derived-uniform with explicit status, never invented precision.
    """
    entries: list[CarryEntry] = []
    for name, cell in sorted(attempt2_files.items()):
        entries.append(
            CarryEntry(
                entry_id=f"{attempt2_digest}:file:{name}:footer",
                source_receipt=attempt2_digest,
                source_path=attempt2_path,
                file=name,
                category="footer",
                requests=int(cell.get("requests", 0)),
                bytes=int(cell.get("bytes", 0)),
                measurement=str(cell.get("measurement", "measured")),
                note="v2.0 attempt-2 cost-map planning",
            )
        )
    for name, cell in sorted(prior_files.items()):
        entries.append(
            CarryEntry(
                entry_id=f"{prior_digest}:file:{name}:footer",
                source_receipt=prior_digest,
                source_path=prior_path,
                file=name,
                category="footer",
                requests=int(cell.get("requests", 0)),
                bytes=int(cell.get("bytes", 0)),
                measurement=str(cell.get("measurement", "measured")),
                note="v2.0 first-file failed planning",
            )
        )
    total_requests = sum(e.requests for e in entries)
    total_bytes = sum(e.bytes for e in entries)
    if total_requests != frozen.V21_T_CARRY_REQUESTS or total_bytes != frozen.V21_T_CARRY_BYTES:
        raise CarryError(
            f"carry entries sum to {total_requests}/{total_bytes}, "
            f"not the frozen {frozen.V21_T_CARRY_REQUESTS}/{frozen.V21_T_CARRY_BYTES}"
        )
    return entries
