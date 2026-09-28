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
        self.in_flight: dict[str, dict[str, Any]] = {}

    def adopt(self, entry: CarryEntry) -> bool:
        """Adopt one historical fact once; duplicates are logged, not charged.

        Same ID with identical data is idempotent (no double charge).
        Same ID with conflicting data refuses: history must not silently
        fork.
        """
        existing = self.entries.get(entry.entry_id)
        if existing is not None:
            if (
                existing.requests == entry.requests
                and existing.bytes == entry.bytes
                and existing.category == entry.category
                and existing.file == entry.file
                and existing.source_receipt == entry.source_receipt
            ):
                self.duplicates.append(entry.entry_id)
                return False
            raise CarryError(f"conflicting re-adoption of {entry.entry_id}: refusing")
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

    def begin_range(self, source_file: str, start: int, end: int) -> str:
        """Open an in-flight range attempt: charged on completion, reserved
        on crash (attempted work is never refunded). Returns a token."""
        if not 0 <= start <= end:
            raise CarryError("in-flight range is empty or negative")
        token = f"inflight-{len(self.in_flight)}-{source_file}-{start}-{end}"
        self.in_flight[token] = {
            "file": source_file,
            "start": start,
            "end": end,
            "estimated_bytes": end - start,
        }
        return token

    def complete_range(self, token: str) -> None:
        """Close an in-flight attempt after its actuals were charged."""
        if token not in self.in_flight:
            raise CarryError(f"unknown in-flight token: {token}")
        del self.in_flight[token]

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
        """Persist entries, live counters, in-flight work, unknowns, gaps.

        The live ledger snapshot is authoritative for post-adoption charges
        (requests, bodies, elapsed, disk, scan, decompression); replaying
        entries alone would drop them, so both are stored and cross-checked
        on load.
        """
        body = {
            "kind": "essential_web_evidence_v2_durable_carry",
            "protocol_version": frozen.V21_PROTOCOL_VERSION,
            "arm": self.ledger.arm,
            "entries": [dict(_entry_dict(e)) for e in self.entries.values()],
            "live": self.ledger.snapshot(),
            "in_flight": [dict(token=token, **detail) for token, detail in self.in_flight.items()],
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
        """Load persisted carry state: replay adoption, restore live counters.

        Entries replay idempotently into the fresh ledger; the saved live
        snapshot is then restored authoritatively after a consistency check
        (replayed adopted subtotals must not exceed the saved counters, or
        the file is corrupt). Open in-flight attempts are converted into
        crash reservations: their estimated bytes are charged, never
        refunded, and their requests were already charged pre-issue.
        """
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
        live = body.get("live")
        if not isinstance(live, dict):
            raise CarryError("carry file lacks a live ledger snapshot")
        replayed_requests = ledger.snapshot()["requests"]
        replayed_bodies = ledger.snapshot()["response_body_bytes"]
        if replayed_requests > int(live.get("requests", -1)):
            raise CarryError("carry file live snapshot is older than replayed entries")
        if replayed_bodies > int(live.get("response_body_bytes", -1)):
            raise CarryError("carry file live bodies are older than replayed entries")
        _restore_snapshot(ledger, live)
        for raw in body.get("in_flight", []):
            estimated = int(raw.get("estimated_bytes", 0))
            if estimated < 0:
                raise CarryError("in-flight estimate is negative")
            if estimated:
                ledger.charge_transfer(str(raw.get("file", "unknown")), estimated, kind="footer")
            durable.in_flight[str(raw.get("token", "?"))] = {
                k: v for k, v in raw.items() if k != "token"
            }
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


def _restore_snapshot(ledger: budgets.ArmLedger, live: Mapping[str, Any]) -> None:
    """Restore live counters authoritatively; every value validated."""

    def _int(value: Any, name: str) -> int:
        if type(value) is bool or not isinstance(value, (int, float)) or value < 0:
            raise CarryError(f"carry snapshot has invalid {name}")
        return int(value)

    ledger.requests = _int(live.get("requests"), "requests")
    ledger.failed_requests = _int(live.get("failed_requests", 0), "failed_requests")
    ledger.retries = _int(live.get("retries", 0), "retries")
    ledger.redirects = _int(live.get("redirects", 0), "redirects")
    ledger.response_body_bytes = _int(live.get("response_body_bytes"), "response_body_bytes")
    ledger.footer_body_bytes = _int(live.get("footer_body_bytes", 0), "footer_body_bytes")
    kinds = live.get("kind_requests", {})
    if not isinstance(kinds, dict):
        raise CarryError("carry snapshot kind_requests malformed")
    ledger.kind_requests = {str(k): _int(v, "kind_requests") for k, v in kinds.items()}
    per_file = live.get("transfer_per_file", {})
    if not isinstance(per_file, dict):
        raise CarryError("carry snapshot transfer_per_file malformed")
    ledger.transfer_per_file = {}
    for name, cell in per_file.items():
        if not isinstance(cell, dict):
            raise CarryError("carry snapshot transfer cell malformed")
        ledger.transfer_per_file[str(name)] = {
            "footer": _int(cell.get("footer", 0), "cell"),
            "data": _int(cell.get("data", 0), "cell"),
        }
    by_category = live.get("transfer_by_category", {})
    if isinstance(by_category, dict):
        ledger.transfer_by_category = {
            "footer": _int(by_category.get("footer", 0), "category"),
            "data": _int(by_category.get("data", 0), "category"),
        }
    ledger.decompressed_arm = _int(live.get("decompressed_arm", 0), "decompressed")
    ledger.disk_scratch = _int(live.get("disk_scratch", 0), "scratch")
    ledger.disk_final = _int(live.get("disk_final", 0), "final")
    ledger.scanned_arm = _int(live.get("scanned_arm", 0), "scanned")
    elapsed = live.get("elapsed_seconds", 0)
    if not isinstance(elapsed, (int, float)) or elapsed < 0:
        raise CarryError("carry snapshot elapsed malformed")
    ledger.elapsed_seconds = float(elapsed)
    ledger.refusals = list(live.get("refusals", []))
    file_requests = live.get("requests_per_file", {})
    if not isinstance(file_requests, dict):
        raise CarryError("carry snapshot requests_per_file malformed")
    ledger._file_requests = {
        str(name): {str(k): _int(v, "file_requests") for k, v in per.items()}
        for name, per in file_requests.items()
        if isinstance(per, dict)
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
