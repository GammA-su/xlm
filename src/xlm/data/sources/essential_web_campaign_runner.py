"""Bounded automatic runner for the fast Essential-Web campaign: pure, offline logic.

The runner is an outer layer only. What a batch is, what it may fetch and
whether it may run stay decided by the fast campaign: membership, dry plans,
per-batch authorization digests, gates, C04 admission, sealing and the stop
decision are reused unchanged. This module holds what the runner adds:

* the bounded auto-authorization envelope, which binds the campaign identity,
  the running code and the exact per-batch authorization digest of every batch
  it may start;
* the classification of a started batch from its authoritative artifacts;
* the projection, header and completion banner shown to the operator;
* the append-only runner log (metadata only, never document text).

The receipts stay the only source of campaign state. The log is used for one
thing besides display: a batch the runner itself saw fail stays in human
review, and a batch the runner itself interrupted may resume, each only while
the batch's own artifacts are exactly as they were at that moment.
"""

from __future__ import annotations

import json
import math
import os
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from xlm.data.evidence_v2 import canonical
from xlm.data.sources import essential_web_calibration as calibration
from xlm.data.sources.essential_web_bulk import GIB, BulkError

ENVELOPE_KIND = "essential_web_auto_campaign_authorization"
ENVELOPE_VERSION = 1
#: Safety ceiling of one envelope, not an acquisition target.
MAX_AUTO_BATCHES = 20
#: Operator automation guard on the durable volume, in addition to the campaign reserve.
DEFAULT_MIN_DURABLE_FREE_BYTES = 128 * GIB
#: Rolling window of complete batches for the projection, and the fewest it needs.
ETA_WINDOW = 3
ETA_MIN_BATCHES = 2

COMPLETE = "COMPLETE"
CLEAN = "CLEAN_NOT_STARTED"
RESUMABLE = "RESUMABLE"
HUMAN_REVIEW = "HUMAN_REVIEW_REQUIRED"

FIRST_PASS_COMPLETE = "FIRST_PASS_COMPLETE"
REFUSED = "REFUSED"
ENVELOPE_EXHAUSTED = "ENVELOPE_EXHAUSTED"
GUARD_STOP = "AUTOMATION_GUARD"
INTERRUPTED = "INTERRUPTED"
EXIT_CODES = {
    FIRST_PASS_COMPLETE: 0,
    REFUSED: 1,
    ENVELOPE_EXHAUSTED: 5,
    HUMAN_REVIEW: 6,
    GUARD_STOP: 7,
    INTERRUPTED: 130,
}

#: Batch event kinds that open one execution attempt (written by the fast monitor).
ATTEMPT_START = frozenset({"run", "resume"})
#: Runner log marks that are tied to one exact batch artifact fingerprint.
MARK_INTERRUPTED = "batch_interrupted"
MARK_FAILED = "batch_failed"

STOP_CONDITIONS = (
    "campaign, source, revision, selector, adapter, inventory, limits or code identity "
    "differs from the envelope",
    "a child batch's membership, plan hash or authorization digest differs from the envelope",
    "the next batch is outside the envelope or beyond the campaign ceiling",
    "the next batch needs human review (recorded unit failure, unexplained fatal stop, "
    "unit without receipt, retained-source or checkpoint identity conflict, recovery "
    "amendment in scope)",
    "the campaign gate says anything other than RUN (disk reserve, footprint cap, order, "
    "ceiling, benchmark, superseded campaign)",
    "C04 production admission of the authorized plan fails",
    "an automation guard: durable free space or scratch free space or scratch occupancy",
    "the batch process exits with any code other than 0 (continue) or 3 (targets met)",
    "the batch is not complete and verified after its process exits",
    "operator interruption (Ctrl+C)",
)


# ------------------------------------------------------------------- envelope


def envelope_identity(
    config: Mapping[str, Any],
    *,
    running_code: Mapping[str, str],
    adapter_code: Mapping[str, str],
    compatibility: Mapping[str, Any],
) -> dict[str, Any]:
    """Everything a child batch depends on besides its own membership and plan."""
    binding = config["binding"]
    return {
        "campaign": config["digest"],
        "source": {
            "source_id": binding["source_id"],
            "repository": binding["repository"],
            "revision": binding["revision"],
        },
        "selector": dict(binding["selector"]),
        "adapter": {
            "adapter_id": binding["adapter_id"],
            "canonicalization": binding["canonicalization"],
            "frozen_code_sha256": dict(config["adapter_code_sha256"]),
            "running_code_sha256": dict(adapter_code),
        },
        "code": {
            "frozen_transport_sha256": dict(config["transport_code_sha256"]),
            "running_sha256": dict(running_code),
            "compatibility": dict(compatibility),
        },
        "inventory": dict(config["inventory"]),
        "batch_files": int(config["batch"]["files"]),
        "benchmark_plan_digest": config["benchmark"]["plan_digest"],
        "targets": dict(config["stop"]["targets"]),
        "limits": dict(config["limits"]),
        "scratch": dict(config["scratch"]),
        "concurrency": dict(config["concurrency"]),
        "disk": dict(config["disk"]),
        "ceiling": dict(config["ceiling"]),
        "c05": {"status": config["c05"]["status"], "obligation": config["c05"]["obligation"]},
    }


def batch_range(
    start: int, max_batches: int, ceiling: int, inventory_batches: int
) -> tuple[int, bool]:
    """Exclusive end of an envelope; True when the campaign ceiling shortened it."""
    if not 1 <= max_batches <= MAX_AUTO_BATCHES:
        raise BulkError(f"max batches must be 1..{MAX_AUTO_BATCHES}; a new envelope is needed")
    limit = min(ceiling, inventory_batches)
    if start < 0 or start >= limit:
        raise BulkError(f"batch {start} is beyond the campaign ceiling ({limit} batches)")
    end = min(start + max_batches, limit)
    return end, end < start + max_batches


def make_envelope(
    identity: Mapping[str, Any],
    children: Sequence[Mapping[str, Any]],
    *,
    start: int,
    end: int,
    max_batches: int,
    clamped: bool,
    guards: Mapping[str, int],
    operator: str,
) -> dict[str, Any]:
    """The bounded auto-authorization the operator approves by its digest."""
    if not operator.strip():
        raise BulkError("the envelope needs an explicit operator identity")
    if [int(child["batch"]) for child in children] != list(range(start, end)):
        raise BulkError("envelope children must be exactly the batches of its range")
    body: dict[str, Any] = {
        "kind": ENVELOPE_KIND,
        "version": ENVELOPE_VERSION,
        "identity": dict(identity),
        "range": {
            "start_batch": start,
            "end_batch_exclusive": end,
            "max_batches": max_batches,
            "clamped_by_campaign_ceiling": clamped,
        },
        "children": [dict(child) for child in children],
        "automation_guards": dict(guards),
        "operator": operator.strip(),
        "may_approve": "only the listed child authorization digests, each re-derived and "
        "compared before its batch starts",
        "never_approves": [
            "a recovery amendment",
            "an oversized-record exception or any changed record bound",
            "a changed source file identity",
            "a changed code compatibility contract",
            "a top-up after the first-pass targets are met",
        ],
    }
    body["digest"] = canonical.digest(body)
    return body


def check_envelope(envelope: Mapping[str, Any]) -> None:
    body = {key: value for key, value in envelope.items() if key != "digest"}
    if (
        envelope.get("kind") != ENVELOPE_KIND
        or envelope.get("version") != ENVELOPE_VERSION
        or canonical.digest(body) != envelope.get("digest")
    ):
        raise BulkError("auto-authorization envelope is altered or of another kind")


def differences(stored: Any, current: Any, path: str = "") -> list[str]:
    """Dotted paths at which two JSON values differ (for a structured refusal)."""
    if isinstance(stored, Mapping) and isinstance(current, Mapping):
        found: list[str] = []
        for key in sorted(set(stored) | set(current)):
            where = f"{path}.{key}" if path else str(key)
            if key not in stored or key not in current:
                found.append(where)
            else:
                found += differences(stored[key], current[key], where)
        return found
    return [] if stored == current else [path or "<root>"]


def child_for(envelope: Mapping[str, Any], batch: int) -> dict[str, Any] | None:
    for child in envelope["children"]:
        if int(child["batch"]) == batch:
            return dict(child)
    return None


# ------------------------------------------------------------- classification


def latest_attempt(events: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """Events of the most recent execution attempt of one batch."""
    starts = [index for index, event in enumerate(events) if event.get("event") in ATTEMPT_START]
    return list(events[starts[-1] :]) if starts else list(events)


def matching_mark(
    batch: int, fingerprint: Mapping[str, int], log: Sequence[Mapping[str, Any]]
) -> Mapping[str, Any] | None:
    """The runner's latest mark on this batch, if the batch is exactly as it left it."""
    marks = [
        record
        for record in log
        if record.get("event") in (MARK_INTERRUPTED, MARK_FAILED)
        and record.get("batch") == batch
        and record.get("fingerprint") == dict(fingerprint)
    ]
    return marks[-1] if marks else None


def failed_review(batch: int, mark: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if mark is None or mark["event"] != MARK_FAILED:
        return None
    code = mark.get("exit_code")
    ended = f"exited {code}" if code is not None else f"raised {mark.get('exception')}"
    return {
        "state": HUMAN_REVIEW,
        "reasons": [
            f"the runner's last attempt on batch {batch} {ended} and nothing changed since; "
            "review it, then resume with the fast operator driver"
        ],
    }


def classify_started(
    batch: int,
    events: Sequence[Mapping[str, Any]],
    fingerprint: Mapping[str, int],
    log: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """RESUMABLE or HUMAN_REVIEW for a started batch whose artifacts already verified.

    ``fingerprint`` counts the batch's event lines and performance records. A
    runner mark applies only while the fingerprint is exactly the one it saw.
    """
    mark = matching_mark(batch, fingerprint, log)
    review = failed_review(batch, mark)
    if review is not None:
        return review
    attempt = latest_attempt(events)
    failed = [event for event in attempt if event.get("event") == "failed"]
    fatal = [event for event in attempt if event.get("event") == "fatal"]
    if failed or fatal:
        if mark is not None and mark["event"] == MARK_INTERRUPTED:
            return {
                "state": RESUMABLE,
                "reasons": ["the runner interrupted this batch on operator request"],
            }
        causes = sorted({f"{event.get('key')}:{event.get('exception')}" for event in failed})
        return {
            "state": HUMAN_REVIEW,
            "reasons": [
                "the last attempt recorded "
                + (f"unit failures {causes}" if failed else "a fatal stop")
                + "; the runner does not resume a failure it cannot explain"
            ],
        }
    return {
        "state": RESUMABLE,
        "reasons": ["no failure recorded (crash, power loss or kill); verified state resumes"],
    }


# ----------------------------------------------------------------- projection


def projection(
    history: Sequence[Mapping[str, Any]],
    views: Mapping[str, Mapping[str, Any]],
    *,
    window: int = ETA_WINDOW,
    minimum: int = ETA_MIN_BATCHES,
) -> dict[str, Any]:
    """Informational projection from the last complete batches; never a stop rule.

    ``history`` holds one entry per complete batch in order: estimated tokens
    per view, durable footprint and measured execution seconds.
    """
    recent = list(history[-window:])
    measured = [entry for entry in recent if entry.get("seconds") is not None]
    if len(measured) < minimum or len(measured) != len(recent):
        return {
            "status": "calculating",
            "basis_batches": [int(entry["batch"]) for entry in recent],
            "note": f"needs {minimum} complete batches with measured durations",
        }
    count = len(recent)
    per_view: dict[str, Any] = {}
    remaining = 0
    unknown = False
    for view, entry in views.items():
        yield_tokens = sum(float(e["tokens"][view]) for e in recent) / count
        deficit = float(entry["deficit_tokens"])
        if deficit <= 0:
            batches: int | None = 0
        elif yield_tokens <= 0:
            batches, unknown = None, True
        else:
            batches = math.ceil(deficit / yield_tokens)
        per_view[view] = {"recent_tokens_per_batch": yield_tokens, "remaining_batches": batches}
        remaining = max(remaining, batches or 0)
    seconds = sum(float(entry["seconds"]) for entry in recent) / count
    footprint = sum(int(entry["footprint_bytes"]) for entry in recent) / count
    return {
        "status": "projected" if not unknown else "unbounded",
        "basis_batches": [int(entry["batch"]) for entry in recent],
        "views": per_view,
        "remaining_batches": None if unknown else remaining,
        "recent_batch_seconds": seconds,
        "eta_seconds": None if unknown else remaining * seconds,
        "recent_footprint_bytes_per_batch": footprint,
        "projected_additional_footprint_bytes": None if unknown else remaining * footprint,
        "note": "projected from the rolling mean of recent complete batches; measured "
        "execution time only, excluding pauses between runs; not a promise",
    }


# ---------------------------------------------------------------------- display


def _duration(seconds: float | None, missing: str = "calculating...") -> str:
    if seconds is None:
        return missing
    seconds = max(0, int(seconds))
    hours, minutes = seconds // 3600, seconds // 60 % 60
    return f"{hours}h{minutes:02d}m" if hours else f"{minutes}m{seconds % 60:02d}s"


def _gib(value: float | None, missing: str = "?") -> str:
    return missing if value is None else f"{value / GIB:,.1f} GiB"


def _tokens(value: float) -> str:
    return f"{value / 1e6:,.1f}M"


def campaign_header(status: Mapping[str, Any]) -> list[str]:
    """The campaign dashboard printed around each batch."""
    disk, clock = status["disk"], status.get("time", {})
    lines = [
        "=" * 72,
        "ESSENTIAL-WEB AUTO CAMPAIGN  " + str(status["campaign"])[:16],
        f"batch {status['next_batch']} next  complete batches {status['complete_batches']}  "
        f"sealed files {status['sealed_files']:,}  cumulative rows {status['rows']:,}",
        "TARGETS (estimated tokens, complete batches only)",
    ]
    for view, entry in status["views"].items():
        target = float(entry["target_tokens"])
        lines.append(
            f"  {view.removeprefix('essential_').upper():9} {_tokens(entry['estimated_tokens'])} / "
            f"{_tokens(target)}  {100 * float(entry['estimated_tokens']) / target:5.1f}%  "
            f"{entry['status']}"
        )
    lines += [
        "DISK",
        f"  durable footprint {_gib(disk['footprint_bytes'])} / cap "
        f"{_gib(disk['footprint_cap_bytes'])}",
        f"  durable free {_gib(disk['durable_free_bytes'])}  campaign reserve "
        f"{_gib(disk['campaign_min_free_bytes'])}  automation guard "
        f"{_gib(disk.get('automation_min_durable_free_bytes'), 'set by PrepareAuto')}",
        f"  scratch used {_gib(disk['scratch_used_bytes'])} / cap {_gib(disk['scratch_cap_bytes'])}"
        f"  scratch free {_gib(disk['scratch_free_bytes'])}  guard "
        f"{_gib(disk.get('automation_min_scratch_free_bytes'), 'set by PrepareAuto')}",
        "TIME",
        f"  campaign elapsed {_duration(clock.get('campaign_elapsed_seconds'), 'not started')}  "
        f"last batch this session {_duration(clock.get('last_batch_seconds'), '-')}",
    ]
    projected = status["projection"]
    if projected["status"] == "projected":
        science = projected["views"].get("essential_science", {})
        lines += [
            f"  recent batch {_duration(projected['recent_batch_seconds'])} (mean of batches "
            f"{projected['basis_batches']})",
            f"  science recent yield {_tokens(science.get('recent_tokens_per_batch', 0))}/batch",
            f"  projected remaining ~{projected['remaining_batches']} batches  "
            f"ETA ~{_duration(projected['eta_seconds'])} (projected, not a promise)",
            f"  projected additional footprint "
            f"~{_gib(projected['projected_additional_footprint_bytes'])}",
        ]
    elif projected["status"] == "unbounded":
        lines.append("  projection: a deficient component had zero recent yield; no ETA")
    else:
        lines.append("  ETA calculating...")
    lines.append("=" * 72)
    return lines


def completion_banner(status: Mapping[str, Any]) -> list[str]:
    lines = [
        "#" * 72,
        "ESSENTIAL-WEB FIRST-PASS ACQUISITION COMPLETE",
        "#" * 72,
        f"batches completed      {status['complete_batches']}",
        f"files sealed           {status['sealed_files']:,}",
        f"rows scanned           {status['rows']:,}",
        f"durable footprint      {status['disk']['footprint_bytes']:,} bytes",
    ]
    for view, entry in status["views"].items():
        lines.append(
            f"{view:22} {entry['canonical_bytes']:,} canonical bytes, "
            f"{entry['estimated_tokens']:,.0f} estimated tokens ({entry['status']})"
        )
    lines += [
        "C05 status             NOT RUN",
        "TRAINING NOT YET PERMITTED",
        "next: freeze canonical pool -> C05 exclusion receipt -> tokenizer -> exact count",
        "#" * 72,
    ]
    return lines


# ---------------------------------------------------------------------- the log


class EventLog:
    """Append-only JSONL of runner decisions; metadata only, fsynced per record."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def append(self, event: str, **metadata: Any) -> dict[str, Any]:
        record = {"at": datetime.now(UTC).isoformat(), "event": event, **metadata}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record, sort_keys=True, ensure_ascii=True) + "\n"
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(line)
            stream.flush()
            os.fsync(stream.fileno())
        return record

    def read(self) -> list[dict[str, Any]]:
        """All records; a torn final line from a power loss is ignored, nothing else."""
        if not self.path.is_file():
            return []
        lines = self.path.read_text(encoding="utf-8").splitlines()
        records: list[dict[str, Any]] = []
        for index, line in enumerate(lines):
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as exc:
                if index != len(lines) - 1:
                    raise BulkError(f"{self.path.name} line {index + 1} is corrupt") from exc
        return records


def read_events(path: Path) -> list[dict[str, Any]]:
    """A batch's append-only event log, tolerating only a torn final line."""
    return EventLog(path).read()


def tokens(canonical_bytes: float) -> float:
    return canonical_bytes / calibration.BYTES_PER_TOKEN["central"]
