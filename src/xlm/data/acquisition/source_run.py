"""High-throughput whole-file acquisition for any admitted single-adapter Mix-01 source.

Generalizes the Essential-Web fast transport without touching it:

    bounded concurrent whole-file streams (resumable ``.part`` + Range/If-Range)
      -> fast scratch (byte-capped, partials counted)
      -> independent SHA-256 verification
      -> exclusive hashed copy of the unmodified upstream file into the durable store
      -> local adaptation in worker processes (pipelined with later downloads)
      -> sealed per-file receipt, then atomic publication of the canonical unit

Durable layout under the data root (``G:\\XLM``), per source key:

    plans/<key>/pNN/plan.json            write-once production plan (digest = authorization)
    plans/<key>/pNN/authorization.json   the operator's explicit authorization
    plans/<key>/pNN/acquisition.plan.json  the authorized AcquisitionPlan
    plans/<key>/pNN/performance-XX.json  one performance receipt per run
    plans/<key>/pNN/events.jsonl         dashboard event log
    acq-raw/<key>/source/<file>          verified upstream Parquet + identity sidecar
    canonical/<key>/pNN/fRRRRR/          documents, compressed ledger, summary, receipt

Scratch (``C:\\XLM-scratch``) holds only ``<key>/pNN/fRRRRR.parquet.part`` and
its state file until the unit is sealed. Nothing sealed is ever redone,
rewritten or deleted; a failed run keeps every verified byte.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import threading
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO

import psutil
import pyarrow.parquet as pq
from filelock import FileLock, Timeout

from xlm.artifacts.manifest import ensure_plain_path
from xlm.data.acquisition.plan import (
    AcquisitionPlan,
    PlanAuthorization,
    load_acquisition_plan,
    save_acquisition_plan,
    validate_plan_authorization,
)
from xlm.data.acquisition.sampling import canonical_range_url
from xlm.data.acquisition.source_dashboard import (
    FRESH_DOWNLOAD,
    LOCAL_COMPLETE_REUSE,
    LOCAL_PROCESSING_RETRY,
    RESTART_CLASSES,
    RESUMABLE_PARTIAL,
    SEALED_SKIP,
    Dashboard,
    ObservedScratch,
    Snapshot,
    fatal_lines,
    free_bytes,
)
from xlm.data.acquisition.source_growth import ProcessingGrowth, bounded_json, check_result
from xlm.data.acquisition.source_local import (
    LEDGER_FILENAME,
    RECEIPT_FILENAME,
    SourceAdaptError,
    process_source_unit,
    scan_documents,
)
from xlm.data.acquisition.source_parquet import (
    READ_BYTES,
    TransferLimits,
    TransferMeter,
    TransferResult,
    file_sha256,
    identity_path,
    identity_record,
    load_durable_source,
)
from xlm.data.acquisition.source_plan import check_plan, minted_from_record
from xlm.data.acquisition.source_reservations import SourceReservations, reserve_metadata
from xlm.data.acquisition.source_rowgroups import RowGroupError, check_concurrency
from xlm.data.adapters.rejections import DOCUMENTS_FILENAME, SUMMARY_FILENAME
from xlm.data.evidence_v2 import canonical
from xlm.data.sources import essential_web_local as pipeline
from xlm.data.sources.essential_web_local import Unit, failure_detail, read_progress

RECEIPT_KIND = "mix01_source_unit_receipt"
RECEIPT_VERSION = 1
PERFORMANCE_KIND = "mix01_source_performance_receipt"
PERFORMANCE_VERSION = 1
ACCOUNT_KIND = "mix01_source_accounting"
SEAL_KIND = "mix01_source_first_pass_seal"
RAW_CONTRACT_ID = "mix01-source-raw-artifact-v1"
RAW_REPRESENTATION = "verified_source_parquet"
UNIT_FILES = frozenset({DOCUMENTS_FILENAME, LEDGER_FILENAME, SUMMARY_FILENAME, RECEIPT_FILENAME})
MAX_JSON_BYTES = 8 * 1024**2


class RunError(RuntimeError):
    """A run, resume, verification or seal rule is violated; nothing is overwritten."""


# -------------------------------------------------------------------- files


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    """Atomic replace; for mutable status files only."""
    ensure_plain_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(dict(payload), indent=2, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def write_once(path: Path, payload: Mapping[str, Any]) -> bool:
    """Exclusive publication; an identical rerun is a no-op, a different one refuses."""
    ensure_plain_path(path)
    data = bounded_json(payload, MAX_JSON_BYTES)
    if path.exists():
        if path.read_bytes() != data:
            raise RunError(f"'{path.name}' already exists with different content; refusing")
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return True


def read_json(path: Path, limit: int = MAX_JSON_BYTES) -> dict[str, Any]:
    if not path.is_file() or path.stat().st_size > limit:
        raise RunError(f"'{path}' is missing or exceeds its bounded size")
    value = json.loads(path.read_bytes().decode("utf-8"))
    if not isinstance(value, dict):
        raise RunError(f"'{path.name}' is not a JSON object")
    return value


def self_digest(body: dict[str, Any]) -> dict[str, Any]:
    body.pop("digest", None)
    body["digest"] = canonical.digest(body)
    return body


def check_digest(record: Mapping[str, Any], what: str) -> None:
    body = dict(record)
    if body.pop("digest", None) != canonical.digest(body):
        raise RunError(f"{what} digest does not verify")


# -------------------------------------------------------------------- roots


@dataclass(frozen=True)
class Roots:
    """Durable data root, fast scratch root and the source key (both roots disjoint)."""

    data_root: Path
    scratch_root: Path
    source_key: str

    def __post_init__(self) -> None:
        if not self.source_key.isidentifier():
            raise RunError("source key must be a plain identifier")
        durable, scratch = self.data_root.resolve(), self.scratch_root.resolve()
        if durable == scratch or scratch.is_relative_to(durable) or durable.is_relative_to(scratch):
            raise RunError("scratch root and durable data root must be disjoint")

    @property
    def plans(self) -> Path:
        return self.data_root / "plans" / self.source_key

    def plan_dir(self, sequence: int) -> Path:
        return self.plans / f"p{sequence:02d}"

    def raw_path(self, source_file: str) -> Path:
        return self.data_root / "acq-raw" / self.source_key / "source" / source_file

    @property
    def canonical(self) -> Path:
        return self.data_root / "canonical" / self.source_key

    def unit_dir(self, sequence: int, rank: int) -> Path:
        return self.canonical / f"p{sequence:02d}" / f"f{rank:05d}"

    def staging(self, label: str) -> Path:
        return self.canonical / ".staging" / label

    def scratch(self, *parts: str) -> Path:
        return self.scratch_root.joinpath(self.source_key, *parts)

    def sequences(self) -> list[int]:
        if not self.plans.is_dir():
            return []
        found = []
        for child in self.plans.iterdir():
            if child.name.startswith("p") and child.name[1:].isdigit():
                if (child / "plan.json").is_file():
                    found.append(int(child.name[1:]))
        return sorted(found)


def unit_key(rank: int) -> str:
    return f"f{rank:05d}"


# ------------------------------------------------------------- plan storage


def store_plan(roots: Roots, record: Mapping[str, Any]) -> Path:
    check_plan(record)
    if record["source_key"] != roots.source_key:
        raise RunError("plan belongs to another source key")
    sequence = int(record["sequence"])
    existing = roots.sequences()
    if sequence not in existing and existing and sequence != existing[-1] + 1:
        raise RunError("plans are numbered contiguously; a top-up follows the last plan")
    path = roots.plan_dir(sequence) / "plan.json"
    write_once(path, record)
    return path


def load_plan(roots: Roots, sequence: int) -> dict[str, Any]:
    record = read_json(roots.plan_dir(sequence) / "plan.json")
    check_plan(record)
    if record["source_key"] != roots.source_key or int(record["sequence"]) != sequence:
        raise RunError("plan record does not belong here")
    return record


def authorize(
    roots: Roots,
    sequence: int,
    digest: str,
    operator: str,
    admitted: Callable[[AcquisitionPlan], None],
) -> AcquisitionPlan:
    """Record the operator's explicit authorization of this exact digest; mint the plan."""
    record = load_plan(roots, sequence)
    if digest != record["digest"]:
        raise RunError("digest does not match this plan; nothing authorized")
    if not operator.strip():
        raise RunError("authorization needs a named operator")
    target = roots.plan_dir(sequence) / "authorization.json"
    if target.exists():
        if read_json(target)["plan_digest"] != digest:
            raise RunError("a different authorization is already recorded")
    else:
        write_once(
            target,
            {
                "plan_digest": digest,
                "operator": operator.strip(),
                "authorized_at": datetime.now(UTC).isoformat(),
            },
        )
    minted = minted_from_record(record)
    plan = minted.model_copy(
        update={
            "authorization": PlanAuthorization(
                authorization_hash=minted.plan_hash,
                authorized_by=f"operator:{operator.strip()}",
                authorized_at=read_json(target)["authorized_at"],
                scope="production",
                is_pilot_approved=False,
            )
        }
    )
    admitted(plan)
    validate_plan_authorization(plan, catalog_source_approved=True)
    save_acquisition_plan(plan, roots.plan_dir(sequence) / "acquisition.plan.json")
    return plan


def repairs_of(records: Sequence[Mapping[str, Any]]) -> dict[int, list[Mapping[str, Any]]]:
    """Repair plans by the sequence they repair; each must name that plan's digest."""
    by_sequence = {int(r["sequence"]): r for r in records}
    repairs: dict[int, list[Mapping[str, Any]]] = {}
    for record in records:
        repair = record.get("repair")
        if repair is None:
            continue
        target = by_sequence.get(int(repair["plan_sequence"]))
        if (
            target is None
            or target["digest"] != repair["plan_digest"]
            or int(record["sequence"]) <= int(target["sequence"])
        ):
            raise RunError(f"plan {record['sequence']} repairs no earlier plan of this source")
        repairs.setdefault(int(target["sequence"]), []).append(record)
    return repairs


def resolution(roots: Roots, records: Sequence[Mapping[str, Any]]) -> dict[int, list[int]]:
    """Unresolved ranks per plan: unsealed there and not re-planned by a repair.

    A rank re-planned by a repair belongs to that repair, where it is
    unresolved until sealed. A rank sealed both in a plan and in its repair is
    refused, never counted twice.
    """
    repairs = repairs_of(records)
    unresolved: dict[int, list[int]] = {}
    for record in records:
        sequence = int(record["sequence"])
        sealed = {int(r["rank"]) for r in resume_state(roots, record)["receipts"]}
        ranks = [int(e["rank"]) for e in record["selection"]["files"]]
        moved: set[int] = set()
        for repair in repairs.get(sequence, []):
            taken = {int(rank) for rank in repair["repair"]["ranks"]}
            if taken & sealed or taken & moved:
                raise RunError(f"plan {sequence} ranks {sorted(taken)} are sealed twice")
            moved |= taken
        unresolved[sequence] = [rank for rank in ranks if rank not in sealed | moved]
    return unresolved


def load_authorized(roots: Roots, sequence: int) -> tuple[dict[str, Any], AcquisitionPlan]:
    record = load_plan(roots, sequence)
    later = [load_plan(roots, s) for s in roots.sequences() if s > sequence]
    if any(int(r["repair"]["plan_sequence"]) == sequence for r in later if r.get("repair")):
        raise RunError(f"plan {sequence} is repaired by a later plan; run the repair instead")
    directory = roots.plan_dir(sequence)
    if not (directory / "authorization.json").is_file():
        raise RunError("plan is not authorized: review its digest, then run authorize")
    if read_json(directory / "authorization.json")["plan_digest"] != record["digest"]:
        raise RunError("authorization belongs to another plan digest")
    plan = load_acquisition_plan(directory / "acquisition.plan.json")
    if plan.plan_hash != record["acquisition_plan"]["plan_hash"] or plan.selected_files != [
        entry["file"] for entry in record["selection"]["files"]
    ]:
        raise RunError("authorized acquisition plan is not this plan record")
    validate_plan_authorization(plan, catalog_source_approved=True)
    return record, plan


# -------------------------------------------------------------- resume


def load_receipt(path: Path, record: Mapping[str, Any]) -> dict[str, Any]:
    receipt = read_json(path)
    check_digest(receipt, f"unit receipt {path.parent.name}")
    if (
        receipt.get("kind") != RECEIPT_KIND
        or receipt["plan"]["digest"] != record["digest"]
        or receipt["source_key"] != record["source_key"]
    ):
        raise RunError(f"receipt {path.parent.name} belongs to another plan")
    return receipt


def resume_state(roots: Roots, record: Mapping[str, Any]) -> dict[str, Any]:
    """Sealed units (valid receipts) and the remaining ranks of one plan, in plan order."""
    sequence = int(record["sequence"])
    receipts: list[dict[str, Any]] = []
    remaining: list[dict[str, Any]] = []
    for entry in record["selection"]["files"]:
        directory = roots.unit_dir(sequence, int(entry["rank"]))
        if directory.exists():
            receipt = load_receipt(directory / RECEIPT_FILENAME, record)
            if (receipt["rank"], receipt["file"]) != (entry["rank"], entry["file"]):
                raise RunError(f"unit {directory.name} holds another file")
            receipts.append(receipt)
        else:
            remaining.append(dict(entry))
    total = len(record["selection"]["files"])
    return {
        "receipts": receipts,
        "sealed": len(receipts),
        "total": total,
        "remaining": remaining,
        "completion_percent": 100.0 * len(receipts) / total,
    }


def prefix_sha256(path: Path, length: int) -> str | None:
    import hashlib

    digest, left = hashlib.sha256(), length
    with path.open("rb") as stream:
        while left:
            chunk = stream.read(min(READ_BYTES, left))
            if not chunk:
                return None
            digest.update(chunk)
            left -= len(chunk)
    return digest.hexdigest()


def classify(
    roots: Roots, record: Mapping[str, Any], resume: Mapping[str, Any], label: str
) -> dict[str, Any]:
    """Restart class of every unit, judged as the transport and worker will judge it. Read-only."""
    units: dict[str, list[str]] = {name: [] for name in RESTART_CLASSES}
    units[SEALED_SKIP] = [unit_key(int(r["rank"])) for r in resume["receipts"]]
    kept = known = unknown = 0
    pin = record["source"]
    max_file = int(record["limits"]["max_file_bytes"])
    for entry in resume["remaining"]:
        key, name = unit_key(int(entry["rank"])), str(entry["file"])
        retained = load_durable_source(roots.raw_path(name))
        if retained is not None:
            if (retained["source_file"], retained["repository"], retained["revision"]) != (
                name,
                pin["repository"],
                pin["revision"],
            ):
                raise RunError(f"retained source identity differs for {key}")
            units[LOCAL_PROCESSING_RETRY].append(key)
            continue
        partial = roots.scratch(label, f"{key}.parquet.part")
        state_path = roots.scratch(label, f"{key}.state.json")
        state = read_json(state_path) if state_path.is_file() else {}
        length, verified = state.get("length"), int(state.get("verified_bytes", 0))
        if state.get("complete") and partial.is_file():
            if file_sha256(partial) == (state.get("sha256"), length):
                units[LOCAL_COMPLETE_REUSE].append(key)
                continue
        elif verified and length is not None and partial.is_file():
            if prefix_sha256(partial, verified) == state.get("prefix_sha256"):
                units[RESUMABLE_PARTIAL].append(key)
                kept += verified
                known += int(length) - verified
                continue
        units[FRESH_DOWNLOAD].append(key)
        if length is None:
            unknown += 1
        else:
            known += int(length)
    return {
        "counts": {name: len(keys) for name, keys in units.items()},
        "units": units,
        "resumable_verified_bytes": kept,
        "known_network_bytes": known,
        "worst_case_network_bytes": known + unknown * max_file,
    }


def source_url(pin: Mapping[str, str], name: str) -> str:
    return canonical_range_url(pin["provider"], pin["repository"], pin["revision"], name)


PROCESS_LIMIT_KEYS = (
    "max_decompression_ratio",
    "max_parser_bytes",
    "max_rows_per_file",
    "max_record_bytes",
    "max_decoded_bytes_per_file",
    "max_ledger_bytes",
    "max_canonical_bytes_per_file",
    "max_durable_bytes_per_file",
    "scratch_min_free_bytes",
)


def prepare_units(
    roots: Roots,
    record: Mapping[str, Any],
    plan: AcquisitionPlan,
    entries: Sequence[Mapping[str, Any]],
    label: str,
    staging: Path,
    url_for: Callable[[str], str],
    *,
    durable: bool = True,
) -> tuple[list[Unit], dict[str, int], int]:
    """Pipeline units; retained durable sources are reused, never downloaded again."""
    pin = record["source"]
    if entries and "processing_growth" not in record["limits"]:
        raise RunError("unsealed source work needs a new plan with bounded processing growth")
    limits = {key: record["limits"][key] for key in PROCESS_LIMIT_KEYS}
    for optional in ("processing_growth", "row_group_parallel"):
        if optional in record["limits"]:
            limits[optional] = record["limits"][optional]
    units: list[Unit] = []
    ranks: dict[str, int] = {}
    charged = 0
    for entry in entries:
        rank, name = int(entry["rank"]), str(entry["file"])
        key = unit_key(rank)
        partial = roots.scratch(label, f"{key}.parquet.part")
        state = roots.scratch(label, f"{key}.state.json")
        ranks[key] = rank
        if state.is_file():
            charged += int(read_json(state).get("charged_bytes", 0))
        job = {
            "source_file": name,
            "staging_dir": str(staging / key),
            "source_id": pin["source_id"],
            "view_id": pin["view_id"],
            "adapter_id": pin["adapter_id"],
            "repository": pin["repository"],
            "revision": pin["revision"],
            "plan_id": plan.plan_id,
            "plan_hash": plan.plan_hash,
            "selection_hash": plan.compute_selection_hash(),
            "limits": limits,
            "row_range": None,
            "progress_path": str(staging / key / "progress.json"),
        }
        retained = load_durable_source(roots.raw_path(name)) if durable else None
        if retained is not None:
            if (retained["source_file"], retained["repository"], retained["revision"]) != (
                name,
                pin["repository"],
                pin["revision"],
            ):
                raise RunError(f"retained source identity differs for {key}")
            expected = plan.expected_file_digests.get(name)
            if expected is not None and retained["sha256"] != expected:
                raise RunError(f"retained source of {key} is not the plan's bound SHA-256")
            if not state.is_file():
                charged += int(retained["length"])
            units.append(
                Unit(
                    key=key,
                    source_file=name,
                    url=None,
                    partial=partial,
                    state=state,
                    job={**job, "source_path": str(roots.raw_path(name)), "durable_path": None},
                    identity_record=retained,
                )
            )
        else:
            units.append(
                Unit(
                    key=key,
                    source_file=name,
                    url=url_for(name),
                    partial=partial,
                    state=state,
                    job={
                        **job,
                        "source_path": str(partial),
                        "durable_path": str(roots.raw_path(name)) if durable else None,
                    },
                    expected_sha256=plan.expected_file_digests.get(name),
                )
            )
    return units, ranks, charged


def check_durable_space(roots: Roots, units: int, per_unit: int) -> None:
    """Refuse before any transfer when the durable volume cannot hold the remaining units."""
    free = free_bytes(roots.data_root)
    if free is None or free < units * per_unit:
        raise RunError(
            f"durable volume has {free} free bytes; the remaining {units} units may need "
            f"{units * per_unit} (planned per-unit ceiling)"
        )


def transfer_limits(record: Mapping[str, Any]) -> TransferLimits:
    limits = record["limits"]
    return TransferLimits(
        max_file_bytes=int(limits["max_file_bytes"]),
        max_transfer_bytes=2 * int(limits["max_file_bytes"]),
        max_requests=int(limits["max_requests_per_file"]),
        max_retries=int(limits["max_retries"]),
        request_timeout_seconds=float(limits["request_timeout_seconds"]),
        deadline_seconds=float(limits["file_deadline_seconds"]),
        max_state_bytes=None
        if "processing_growth" not in limits
        else int(limits["processing_growth"]["state_bytes"]),
    )


# ------------------------------------------------------------------ sealing


def unit_receipt(
    record: Mapping[str, Any],
    rank: int,
    source: Mapping[str, Any],
    raw_path: str,
    result: Mapping[str, Any],
    transfer: Mapping[str, Any],
    bookkeeping_bytes: int,
) -> dict[str, Any]:
    """The immutable receipt of one whole-file unit; row conservation is checked here."""
    rows = int(result["rows"])
    if rows != int(result["file_rows"]) or list(result["row_range"]) != [0, rows]:
        raise RunError(f"{result['source_file']}: a plan unit must cover the whole file")
    if int(result["documents"]) + int(result["rejected"]) != rows:
        raise RunError(f"{result['source_file']}: documents and rejections do not conserve rows")
    if result["source_file"] != source["source_file"]:
        raise RunError("adapted file differs from the verified source")
    return self_digest(
        {
            "kind": RECEIPT_KIND,
            "version": RECEIPT_VERSION,
            "source_key": record["source_key"],
            "source": dict(record["source"]),
            "plan": {
                "sequence": record["sequence"],
                "digest": record["digest"],
                "plan_id": record["acquisition_plan"]["plan_id"],
                "plan_hash": record["acquisition_plan"]["plan_hash"],
                "selection_hash": record["acquisition_plan"]["selection_hash"],
                "transport_mode": record["transport_mode"],
            },
            "rank": rank,
            "file": source["source_file"],
            "identity": dict(source),
            "raw": {
                "contract_id": RAW_CONTRACT_ID,
                "representation": RAW_REPRESENTATION,
                "path": raw_path,
                "bytes": int(source["length"]),
                "sha256": source["sha256"],
            },
            "rows": rows,
            "row_groups": int(result["row_groups"]),
            "selected_records": {
                "sha256": result["selected_records_sha256"],
                "bytes": int(result["selected_records_bytes"]),
                "max_record_bytes": int(result["max_selected_record_bytes"]),
                "stored": False,
            },
            "documents": int(result["documents"]),
            "rejected": int(result["rejected"]),
            "rejection_counts_by_code": dict(result["rejection_counts_by_code"]),
            "canonical_bytes": int(result["canonical_bytes"]),
            "documents_sha256": result["documents_sha256"],
            "documents_file_bytes": int(result["documents_file_bytes"]),
            "adaptation_summary_sha256": result["adaptation_summary_sha256"],
            "rejections": {
                "sha256": result["rejections_sha256"],
                "uncompressed_bytes": int(result["rejections_uncompressed_bytes"]),
                "file_bytes": int(result["rejections_file_bytes"]),
                "file_sha256": result["rejections_file_sha256"],
            },
            "transfer": dict(transfer),
            "processing": {
                "seconds": result["process_seconds"],
                "cpu_seconds": result["process_cpu_seconds"],
                "promote_seconds": result.get("promote_seconds", 0.0),
                "decoded_bytes": result["decoded_bytes"],
            },
            "footprint_bytes": int(source["length"])
            + int(result["documents_file_bytes"])
            + int(result["rejections_file_bytes"])
            + bookkeeping_bytes,
        }
    )


def seal_unit(
    roots: Roots,
    record: Mapping[str, Any],
    rank: int,
    unit: Unit,
    transfer: TransferResult | None,
    result: Mapping[str, Any],
) -> dict[str, Any]:
    """Cross-check one adapted unit, write its receipt and publish it atomically."""
    staging = Path(str(result["staging_dir"]))
    if scan_documents(staging / DOCUMENTS_FILENAME) != (
        int(result["documents"]),
        int(result["canonical_bytes"]),
        result["documents_sha256"],
    ):
        raise RunError(f"{unit.source_file}: canonical output does not reconcile")
    durable = roots.raw_path(unit.source_file)
    source = read_json(identity_path(durable))
    if durable.stat().st_size != int(source["length"]):
        raise RunError(f"{unit.source_file}: retained source has the wrong size")
    if transfer is None:
        metrics: dict[str, Any] = {
            "transferred_bytes": 0,
            "requests": 0,
            "retries": 0,
            "accounting": "retained source reused; its transfer belongs to an earlier run",
        }
        if unit.state.is_file():
            previous = read_json(unit.state)
            metrics.update(
                transferred_bytes=int(previous.get("charged_bytes", 0)),
                requests=int(previous.get("requests", 0)),
                retries=int(previous.get("retries", 0)),
                accounting="retained source reused; prior transfer charged from its checkpoint",
            )
    else:
        metrics = {
            key: value for key, value in asdict(transfer).items() if key not in ("identity", "path")
        }
    receipt = unit_receipt(
        record,
        rank,
        source,
        durable.relative_to(roots.data_root).as_posix(),
        result,
        metrics,
        (staging / SUMMARY_FILENAME).stat().st_size + identity_path(durable).stat().st_size,
    )
    limits = record["limits"]
    if "processing_growth" in limits:
        check_result(result, limits, int(source["length"]))
        growth = ProcessingGrowth.model_validate(limits["processing_growth"])
        bounded_json(receipt, growth.metadata_bytes)
    if receipt["canonical_bytes"] > int(limits["max_canonical_bytes_per_file"]) or receipt[
        "footprint_bytes"
    ] > int(limits["max_durable_bytes_per_file"]):
        raise RunError(f"{unit.source_file}: unit exceeds its planned canonical/durable ceiling")
    write_once(staging / RECEIPT_FILENAME, receipt)
    final = roots.unit_dir(int(record["sequence"]), rank)
    if final.exists():
        raise RunError(f"unit {final.name} already exists; refusing overwrite")
    final.parent.mkdir(parents=True, exist_ok=True)
    os.rename(staging, final)
    try:
        staging.parent.rmdir()
    except OSError:
        pass
    # Scratch is released only now: the durable copy reproduced the verified hash
    # and the canonical unit with its receipt is published.
    unit.partial.unlink(missing_ok=True)
    unit.state.unlink(missing_ok=True)
    return receipt


# ---------------------------------------------------------------- telemetry


class Sampler(threading.Thread):
    """One-second CPU, network, disk and resident-memory samples of this machine."""

    def __init__(self, interval: float = 1.0) -> None:
        super().__init__(daemon=True)
        self.interval, self._halt = interval, threading.Event()
        self.samples: list[tuple[float, float, int, int, int]] = []

    @staticmethod
    def _reading() -> tuple[int, int, int]:
        network = psutil.net_io_counters()
        disk = psutil.disk_io_counters()
        resident = 0
        try:
            process = psutil.Process()
            resident = process.memory_info().rss + sum(
                child.memory_info().rss for child in process.children(recursive=True)
            )
        except psutil.Error:
            pass
        return int(network.bytes_recv), 0 if disk is None else int(disk.write_bytes), resident

    def run(self) -> None:
        psutil.cpu_percent(None)
        while not self._halt.wait(self.interval):
            self.samples.append((time.monotonic(), psutil.cpu_percent(None), *self._reading()))

    def stop(self) -> dict[str, Any]:
        self._halt.set()
        self.join(timeout=5)
        if len(self.samples) < 2:
            return {"samples": len(self.samples)}
        first, last = self.samples[0], self.samples[-1]
        seconds = last[0] - first[0]
        return {
            "samples": len(self.samples),
            "seconds": seconds,
            "logical_cpus": psutil.cpu_count(),
            "cpu_percent_mean": sum(s[1] for s in self.samples) / len(self.samples),
            "cpu_percent_max": max(s[1] for s in self.samples),
            "network_received_bytes": last[2] - first[2],
            "disk_written_bytes": last[3] - first[3],
            "peak_resident_bytes": max(s[4] for s in self.samples),
            "scope": "whole machine for CPU, network and disk; this process tree for memory",
        }


def transfer_summary(transfers: Sequence[TransferResult], wall: float) -> dict[str, Any]:
    moved = sum(t.transferred_bytes for t in transfers)
    requests = sum(t.requests for t in transfers)
    return {
        "files": len(transfers),
        "file_bytes": sum(t.identity.length for t in transfers),
        "transferred_bytes": moved,
        "requests": requests,
        "redirects": sum(t.redirects for t in transfers),
        "retries": sum(t.retries for t in transfers),
        "resumed_bytes": sum(t.resumed_bytes for t in transfers),
        "cache_hits": sum(1 for t in transfers if t.cache_hit),
        "wall_seconds": wall,
        "megabytes_per_second": moved / 1e6 / wall if wall > 0 else None,
        "mean_request_open_seconds": None,
        "sha256_independently_verified": sum(
            1 for t in transfers if t.identity.sha256_independently_verified
        ),
    }


def performance_receipt(
    *,
    record: Mapping[str, Any],
    label: str,
    mode: str,
    downloads: int,
    processes: int,
    outcome: Mapping[str, Any],
    transfers: Sequence[TransferResult],
    receipts: Sequence[Mapping[str, Any]],
    results: Sequence[Mapping[str, Any]],
    stats: Mapping[str, Any],
    system: Mapping[str, Any],
    benchmark: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Durable per-run performance record; console output is never the only record."""
    wall = float(stats.get("wall_seconds", system.get("seconds", 0.0)) or 0.0)
    rows = sum(int(r["rows"]) for r in results)
    cpu = sum(float(r["process_cpu_seconds"]) for r in results)
    canonical_bytes = sum(int(r["canonical_bytes"]) for r in results)
    transfer = transfer_summary(transfers, wall)
    return self_digest(
        {
            "kind": PERFORMANCE_KIND,
            "version": PERFORMANCE_VERSION,
            "source_key": record["source_key"],
            "source": dict(record["source"]),
            "run": label,
            "plan": {
                "sequence": record.get("sequence"),
                "digest": record["digest"],
                "plan_hash": record["acquisition_plan"]["plan_hash"],
            },
            "benchmark": dict(benchmark) if benchmark is not None else None,
            "transport_mode": mode,
            "concurrency": {"download_workers": downloads, "process_workers": processes},
            "settings": {key: record["limits"][key] for key in sorted(record["limits"])},
            "outcome": dict(outcome),
            "transfer": transfer,
            "processing": {
                "units": len(results),
                "rows": rows,
                "rows_per_second": rows / wall if wall > 0 else None,
                "cpu_seconds": cpu,
                "rows_per_process_second": rows / cpu if cpu > 0 else None,
                "documents": sum(int(r["documents"]) for r in results),
                "rejected": sum(int(r["rejected"]) for r in results),
                "canonical_bytes": canonical_bytes,
                "canonical_bytes_per_transferred_byte": canonical_bytes
                / transfer["transferred_bytes"]
                if transfer["transferred_bytes"]
                else None,
            },
            "units_sealed_this_run": len(receipts),
            "pipeline": dict(stats),
            "system": dict(system),
            "finished_at": datetime.now(UTC).isoformat(),
        }
    )


def next_index(directory: Path, prefix: str) -> int:
    return len(list(directory.glob(f"{prefix}-*.json"))) if directory.is_dir() else 0


# ---------------------------------------------------------------- monitor


class Monitor:
    """Parent-process telemetry plus worker progress snapshots, rendered on the dashboard."""

    def __init__(
        self,
        roots: Roots,
        record: Mapping[str, Any],
        resume: Mapping[str, Any],
        units: list[Unit],
        scratch: ObservedScratch,
        meter: TransferMeter,
        stream: TextIO,
        log: Path,
        *,
        title: str,
        target: int | None,
        prior_canonical: int,
        staging: Path,
    ) -> None:
        self.roots, self.record, self.resume, self.units = roots, record, resume, units
        self.scratch, self.meter, self.title, self.staging = scratch, meter, title, staging
        self.target, self.prior = target, prior_canonical
        self.dashboard = Dashboard(
            stream, log, max_growth=record["limits"].get("processing_growth", {}).get("event_bytes")
        )
        self.receipts: dict[str, dict[str, Any]] = {}
        self.progress: dict[str, dict[str, Any]] = {}
        self.status: dict[str, Any] = {}
        self.rows: dict[str, int] = {}
        self.lengths: dict[str, int] = {}
        self.failed: set[str] = set()
        self.failures: list[dict[str, Any]] = []
        self.cancelled: list[str] = []
        self.retries_seen = 0
        self.last = -1.0
        for unit in units:
            if unit.identity_record is not None and unit.job is not None:
                self.lengths[unit.key] = int(unit.identity_record["length"])
                with pq.ParquetFile(unit.job["source_path"]) as parquet:
                    self.rows[unit.key] = int(parquet.metadata.num_rows)
        self.dashboard.event(
            "resume" if resume["sealed"] else "run",
            sealed=resume["sealed"],
            total=resume["total"],
            remaining=len(units),
        )
        self.update({}, force=True)

    def sealed(self, key: str, receipt: dict[str, Any]) -> None:
        self.receipts[key] = receipt
        self.dashboard.event(
            "sealed",
            key=key,
            rows=receipt["rows"],
            documents=receipt["documents"],
            estimated_tokens=receipt["canonical_bytes"] // 4,
        )
        self.update({}, force=True)

    def update(self, status: dict[str, Any], *, force: bool = False) -> None:
        if "failed" in status:
            key = str(status["failed"])
            if key not in self.failed:
                self.failed.add(key)
                detail = {k: v for k, v in status.items() if k != "failed" and v is not None}
                self.failures.append({"key": key, **detail})
                self.dashboard.event("failed", key=key, **detail)
            force = True
        elif "cancelled" in status:
            key = str(status["cancelled"])
            if key not in self.cancelled:
                self.cancelled.append(key)
                root = self.failures[0]["key"] if self.failures else None
                self.dashboard.event("cancelled", key=key, cause=root)
            force = True
        else:
            self.status.update(status)
        now = time.monotonic()
        if not force and now - self.last < 1:
            return
        self.last = now
        self.dashboard.update(self.snapshot(), now=now, force=force)

    def snapshot(self) -> Snapshot:
        received, retries = self.meter.telemetry()
        for name, attempt in retries[self.retries_seen :]:
            self.dashboard.event("retry", file=name, attempt=attempt)
        self.retries_seen = len(retries)
        old = self.resume["receipts"]
        snap = Snapshot(
            source=self.record["source"]["source_id"],
            plan=self.title,
            mode=str(self.record.get("transport_mode", "whole_file_local")),
            total_units=int(self.resume["total"]),
            sealed_units=len(old) + len(self.receipts),
            target_canonical_bytes=self.target,
            prior_canonical_bytes=self.prior,
        )
        for receipt in old:
            snap.processed_rows += int(receipt["rows"])
            snap.downloaded_bytes += int(receipt["raw"]["bytes"])
            snap.durable_used += int(receipt["footprint_bytes"])
            snap.documents += int(receipt["documents"])
            snap.rejected += int(receipt["rejected"])
            snap.canonical_bytes += int(receipt["canonical_bytes"])
        for unit in self.units:
            if unit.key in self.receipts:
                receipt = self.receipts[unit.key]
                self.lengths[unit.key] = int(receipt["raw"]["bytes"])
                self.rows[unit.key] = int(receipt["rows"])
                snap.downloaded_bytes += int(receipt["raw"]["bytes"])
                snap.durable_used += int(receipt["footprint_bytes"])
                snap.processed_rows += int(receipt["rows"])
                snap.work_rows += int(receipt["rows"])
                snap.documents += int(receipt["documents"])
                snap.rejected += int(receipt["rejected"])
                snap.canonical_bytes += int(receipt["canonical_bytes"])
                continue
            if unit.key in self.scratch.declared:
                self.lengths[unit.key] = self.scratch.declared[unit.key]
            if unit.identity_record is not None:
                snap.downloaded_bytes += int(unit.identity_record["length"])
            elif unit.partial.is_file():
                snap.downloaded_bytes += unit.partial.stat().st_size
            if unit.job is not None and unit.job.get("progress_path"):
                latest = read_progress(Path(unit.job["progress_path"]))
                if latest is not None:
                    self.progress[unit.key] = latest
                value = self.progress.get(unit.key)
                if value:
                    self.rows[unit.key] = int(value["total"])
                    snap.processed_rows += int(value["rows"])
                    snap.work_rows += int(value["rows"])
                    snap.documents += int(value["documents"])
                    snap.rejected += int(value["rejected"])
                    snap.canonical_bytes += int(value["canonical_bytes"])
        if len(self.lengths) == len(self.units):
            snap.expected_bytes = sum(int(r["raw"]["bytes"]) for r in old) + sum(
                self.lengths.values()
            )
        if len(self.rows) == len(self.units):
            snap.known_rows = sum(int(r["rows"]) for r in old) + sum(self.rows.values())
        snap.transfer_bytes = received
        snap.requests = self.meter.requests
        snap.retries = len(retries)
        snap.downloading = list(self.status.get("downloading", []))
        snap.processing = list(self.status.get("processing", []))
        snap.process_workers = int(self.status.get("process_workers", 0))
        snap.backlog = int(self.status.get("backlog", 0))
        snap.failed, snap.cancelled = len(self.failed), len(self.cancelled)
        snap.scratch_used, snap.scratch_cap = self.scratch.occupied(), self.scratch.cap_bytes
        snap.scratch_free = free_bytes(self.scratch.root)
        snap.durable_free = free_bytes(self.roots.data_root)
        if self.staging.is_dir():
            for path in self.staging.rglob("*"):
                if path.is_file():
                    snap.durable_used += path.stat().st_size
        return snap

    def fatal(self, restart: dict[str, Any]) -> list[str]:
        sealed = len(self.resume["receipts"]) + len(self.receipts)
        lines = fatal_lines(
            self.failures,
            self.cancelled,
            sealed,
            int(self.resume["total"]),
            len(self.receipts),
            restart,
        )
        self.dashboard.event(
            "fatal",
            root=self.failures[0] if self.failures else None,
            cancelled=self.cancelled,
            other_failures=[f["key"] for f in self.failures[1:]],
            sealed=sealed,
            total=self.resume["total"],
            restart=restart["counts"],
        )
        self.dashboard.clear()
        self.dashboard.stream.write("\n".join(lines) + "\n")
        self.dashboard.stream.flush()
        return lines


# -------------------------------------------------------------------- run


def run_plan(
    roots: Roots,
    sequence: int,
    *,
    admitted: Callable[[AcquisitionPlan], None],
    download_workers: int | None = None,
    process_workers: int | None = None,
    stream: TextIO | None = None,
    url_for: Callable[[str], str] | None = None,
    target: int | None = None,
    prior_canonical: int = 0,
    offline: bool = False,
) -> dict[str, Any]:
    """NETWORK: execute one authorized plan; resumes without redoing sealed units."""
    record, plan = load_authorized(roots, sequence)
    admitted(plan)
    limits = record["limits"]
    downloads = download_workers or int(limits["download_workers"])
    processes = int(limits["process_workers"]) if process_workers is None else process_workers
    if not 1 <= downloads <= int(limits["download_workers_max"]) or not (
        0 <= processes <= int(limits["process_workers_max"])
    ):
        raise RunError("requested workers exceed the plan's authorized concurrency ceiling")
    if downloads > plan.limits.max_workers:
        raise RunError("requested download workers exceed the authorized plan")
    try:
        check_concurrency(processes, limits)
    except RowGroupError as exc:
        raise RunError(str(exc)) from exc
    roots.plans.mkdir(parents=True, exist_ok=True)
    try:
        with FileLock(str(roots.plans / "run.lock"), timeout=1):
            return _execute(
                roots,
                record,
                plan,
                downloads,
                processes,
                stream or sys.stdout,
                url_for or (lambda name: source_url(record["source"], name)),
                target,
                prior_canonical,
                offline,
            )
    except Timeout as exc:
        raise RunError("another run of this source is active") from exc


def _execute(
    roots: Roots,
    record: dict[str, Any],
    plan: AcquisitionPlan,
    downloads: int,
    processes: int,
    stream: TextIO,
    url_for: Callable[[str], str],
    target: int | None,
    prior_canonical: int,
    offline: bool,
) -> dict[str, Any]:
    sequence = int(record["sequence"])
    label = f"p{sequence:02d}"
    resume = resume_state(roots, record)
    staging = roots.staging(label)
    # Only private interrupted staging, never sealed output; guard the deletion root.
    if not staging.resolve().is_relative_to((roots.canonical / ".staging").resolve()):
        raise RunError("staging deletion escapes the source staging root")
    shutil.rmtree(staging, ignore_errors=True)
    units, ranks, charged = prepare_units(
        roots, record, plan, resume["remaining"], label, staging, url_for
    )
    if offline and any(unit.url is not None for unit in units):
        raise RunError("offline resume requires every remaining source to be retained")
    limits = record["limits"]
    check_durable_space(roots, len(units), int(limits["max_durable_bytes_per_file"]))
    scratch = ObservedScratch(
        roots.scratch(),
        int(limits["scratch_cap_bytes"]),
        int(limits["scratch_min_free_bytes"]),
    )
    meter = TransferMeter(
        plan.limits.max_transferred_bytes,
        plan.limits.max_requests,
        bytes_used=charged
        + sum(int(r["transfer"].get("transferred_bytes", 0)) for r in resume["receipts"]),
    )
    reservations = None
    directory = roots.plan_dir(sequence)
    if "processing_growth" in limits:
        growth = ProcessingGrowth.model_validate(limits["processing_growth"])
        output_budget = ObservedScratch(
            staging, plan.limits.max_output_disk_bytes, int(limits["scratch_min_free_bytes"])
        )
        reservations = SourceReservations(
            scratch,
            output_budget,
            transfer_limits(record),
            growth,
            plan.revision,
            reserve_metadata(directory, growth, int(limits["scratch_min_free_bytes"])),
        )
    directory = roots.plan_dir(sequence)
    monitor = Monitor(
        roots,
        record,
        resume,
        units,
        scratch,
        meter,
        stream,
        directory / "events.jsonl",
        title=f"plan {sequence} {record['digest'][:12]}",
        target=target,
        prior_canonical=prior_canonical,
        staging=staging,
    )
    transfers: list[TransferResult] = []
    sealed: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []

    def on_done(unit: Unit, transfer: TransferResult | None, result: dict[str, Any] | None) -> None:
        if result is None:
            raise RunError("plan unit finished without local processing")
        if transfer is not None:
            transfers.append(transfer)
        results.append(result)
        receipt = seal_unit(roots, record, ranks[unit.key], unit, transfer, result)
        sealed.append(receipt)
        monitor.sealed(unit.key, receipt)

    sampler = Sampler()
    sampler.start()
    stats: dict[str, Any] = {}
    outcome: dict[str, Any] = {"status": "completed"}
    try:
        if units:
            stats = pipeline.run_pipeline(
                units,
                limits=transfer_limits(record),
                revision=plan.revision,
                download_workers=downloads,
                process_workers=processes,
                scratch=scratch,
                meter=meter,
                deadline_seconds=plan.limits.overall_deadline_seconds,
                identity_for=lambda unit, transfer: identity_record(
                    transfer.identity,
                    source_file=unit.source_file,
                    repository=plan.repository,
                    revision=plan.revision,
                ),
                on_done=on_done,
                process=process_source_unit,
                max_in_flight=int(limits["max_in_flight_files"]),
                on_progress=monitor.update,
                reserve_unit=None if reservations is None else reservations.reserve,
                release_unit=None if reservations is None else reservations.release,
                before_process=None if reservations is None else reservations.processing,
            )
        monitor.update(
            {"downloading": [], "processing": [], "process_workers": 0, "backlog": 0}, force=True
        )
    except BaseException as exc:
        restart = classify(roots, record, resume_state(roots, record), label)
        outcome = {
            "status": "failed",
            "root_failure": monitor.failures[0] if monitor.failures else failure_detail(exc),
            "cancelled_because_of_root_failure": list(monitor.cancelled),
            "other_failures": [f["key"] for f in monitor.failures[1:]],
            "restart": restart["counts"],
            "resumable_verified_bytes": restart["resumable_verified_bytes"],
        }
        try:
            monitor.fatal(restart)
        except Exception as summary:  # the root failure stays the raised error
            stream.write(f"failure summary unavailable: {type(summary).__name__}\n")
        raise
    finally:
        monitor.dashboard.clear()
        system = sampler.stop()
        report = performance_receipt(
            record=record,
            label=label,
            mode=str(record["transport_mode"]),
            downloads=downloads,
            processes=processes,
            outcome=outcome,
            transfers=transfers,
            receipts=sealed,
            results=results,
            stats=stats,
            system=system,
        )
        index = next_index(directory, "performance")
        if "processing_growth" in limits:
            bounded_json(report, growth.run_metadata_bytes)
        write_once(directory / f"performance-{index:02d}.json", report)
    shutil.rmtree(staging, ignore_errors=True)
    return report


# ------------------------------------------------------------- accounting


def account(roots: Roots, record: Mapping[str, Any]) -> dict[str, Any]:
    """Receipt-derived totals of one plan (recomputed every time; never edited)."""
    resume = resume_state(roots, record)
    receipts = resume["receipts"]
    return self_digest(
        {
            "kind": ACCOUNT_KIND,
            "plan_digest": record["digest"],
            "sequence": record["sequence"],
            "units_planned": resume["total"],
            "units_sealed": resume["sealed"],
            "complete": resume["sealed"] == resume["total"],
            "rows": sum(int(r["rows"]) for r in receipts),
            "documents": sum(int(r["documents"]) for r in receipts),
            "rejected": sum(int(r["rejected"]) for r in receipts),
            "canonical_bytes": sum(int(r["canonical_bytes"]) for r in receipts),
            "estimated_tokens": sum(int(r["canonical_bytes"]) for r in receipts) // 4,
            "raw_bytes": sum(int(r["raw"]["bytes"]) for r in receipts),
            "transferred_bytes": sum(
                int(r["transfer"].get("transferred_bytes", 0)) for r in receipts
            ),
            "requests": sum(int(r["transfer"].get("requests", 0)) for r in receipts),
            "footprint_bytes": sum(int(r["footprint_bytes"]) for r in receipts),
            "receipt_digests": [r["digest"] for r in receipts],
        }
    )


def sufficiency(roots: Roots) -> dict[str, Any]:
    """Cumulative canonical availability of every plan against the first-pass requirement."""
    sequences = roots.sequences()
    if not sequences:
        raise RunError("this source has no plan")
    records = [load_plan(roots, s) for s in sequences]
    accounts = [account(roots, r) for r in records]
    acquired = sum(int(a["canonical_bytes"]) for a in accounts)
    required = int(records[-1]["requirement"]["required_canonical_bytes"])
    # Complete means every planned rank is sealed in its plan or in that plan's
    # repair. An incomplete pass is never sufficient: no unit is dropped silently.
    unresolved = resolution(roots, records)
    complete = not any(unresolved.values())
    status = ("SUFFICIENT" if acquired >= required else "TOP_UP") if complete else "INCOMPLETE"
    repairs = repairs_of(records)
    extra: dict[str, Any] = {}
    if repairs:
        extra["repairs"] = [
            {
                "sequence": r["sequence"],
                "repairs_sequence": r["repair"]["plan_sequence"],
                "ranks": r["repair"]["ranks"],
            }
            for target in sorted(repairs)
            for r in repairs[target]
        ]
    if not complete:
        extra["unresolved_ranks"] = {str(s): ranks for s, ranks in unresolved.items() if ranks}
    return self_digest(
        {
            **extra,
            "kind": "mix01_source_sufficiency",
            "source_key": roots.source_key,
            "source": records[-1]["source"],
            "plans": [{"sequence": r["sequence"], "digest": r["digest"]} for r in records],
            "accounting": [a["digest"] for a in accounts],
            "acquired_canonical_bytes": acquired,
            "acquired_estimated_tokens": acquired // 4,
            "required_canonical_bytes": required,
            "first_pass_tokens": records[-1]["requirement"]["first_pass_tokens"],
            "status": status,
            "deficit_canonical_bytes": max(0, required - acquired),
            "next_cursor": records[-1]["selection"]["next_cursor"],
            "token_method": "canonical UTF-8 bytes / 4 (estimate, never exact XLM tokens)",
            "action": {
                "SUFFICIENT": "seal the first pass; no top-up",
                "TOP_UP": "plan the next inventory ranks (a new plan; nothing is edited)",
                "INCOMPLETE": "resume the incomplete plan, or repair units that fail under "
                "its limits; never top up over it",
            }[status],
        }
    )


def verify_plan(roots: Roots, record: Mapping[str, Any], *, content: bool) -> dict[str, Any]:
    """Re-check every sealed unit: receipt digest, file set, sizes and (optionally) hashes."""
    resume = resume_state(roots, record)
    checked = 0
    for receipt in resume["receipts"]:
        directory = roots.unit_dir(int(record["sequence"]), int(receipt["rank"]))
        names = {p.name for p in directory.iterdir()}
        if names != UNIT_FILES:
            raise RunError(f"unit {directory.name} holds unexpected or missing files")
        raw = roots.data_root / receipt["raw"]["path"]
        if raw.stat().st_size != int(receipt["raw"]["bytes"]):
            raise RunError(f"{receipt['file']}: retained source size differs")
        if read_json(identity_path(raw)) != json.loads(json.dumps(receipt["identity"])):
            raise RunError(f"{receipt['file']}: identity sidecar differs from the receipt")
        if content:
            if file_sha256(raw) != (receipt["raw"]["sha256"], int(receipt["raw"]["bytes"])):
                raise RunError(f"{receipt['file']}: retained source content differs")
            try:
                scanned = scan_documents(directory / DOCUMENTS_FILENAME)
            except SourceAdaptError as exc:
                raise RunError(f"{receipt['file']}: canonical documents are malformed") from exc
            if scanned != (
                int(receipt["documents"]),
                int(receipt["canonical_bytes"]),
                receipt["documents_sha256"],
            ):
                raise RunError(f"{receipt['file']}: canonical documents differ")
            if file_sha256(directory / LEDGER_FILENAME)[0] != receipt["rejections"]["file_sha256"]:
                raise RunError(f"{receipt['file']}: rejection ledger differs")
            if file_sha256(directory / SUMMARY_FILENAME)[0] != receipt["adaptation_summary_sha256"]:
                raise RunError(f"{receipt['file']}: adaptation summary differs")
        checked += 1
    return {
        "plan_digest": record["digest"],
        "units_verified": checked,
        "units_planned": resume["total"],
        "mode": "content" if content else "structure",
    }


def membership_digest(receipts: Sequence[Mapping[str, Any]]) -> str:
    return canonical.digest(
        [
            f"{r['plan']['sequence']}|{r['rank']}|{r['file']}|{r['documents_sha256']}|"
            f"{r['documents']}|{r['canonical_bytes']}"
            for r in receipts
        ]
    )


def first_pass_seal(roots: Roots, *, content: bool = True) -> dict[str, Any]:
    """Write-once seal of the source's first pass; refuses an insufficient or partial pass.

    Every planned rank is bound: a rank an authorized plan could not seal is
    bound through the repair plan that sealed it, never dropped.
    """
    status = sufficiency(roots)
    if status["status"] != "SUFFICIENT":
        raise RunError(f"first pass is {status['status']}; nothing sealed")
    records = [load_plan(roots, s) for s in roots.sequences()]
    repairs = repairs_of(records)
    receipts: list[dict[str, Any]] = []
    plans: list[dict[str, Any]] = []
    for record in records:
        verify_plan(roots, record, content=content)
        sequence = int(record["sequence"])
        authorization = read_json(roots.plan_dir(sequence) / "authorization.json")
        if authorization["plan_digest"] != record["digest"]:
            raise RunError(f"plan {sequence} authorization belongs to another digest")
        entry: dict[str, Any] = {
            "sequence": record["sequence"],
            "digest": record["digest"],
            "authorization": authorization["plan_digest"],
            "selection": record["selection"],
        }
        # Present only when repairs exist, so earlier seals keep their bytes.
        if record.get("repair") is not None:
            entry["repair_of"] = {
                key: record["repair"][key] for key in ("plan_sequence", "plan_digest", "ranks")
            }
        if sequence in repairs:
            entry["repaired_ranks"] = {
                str(r["sequence"]): r["repair"]["ranks"] for r in repairs[sequence]
            }
        plans.append(entry)
        receipts.extend(resume_state(roots, record)["receipts"])
    files = [str(r["file"]) for r in receipts]
    if len(set(files)) != len(files):
        raise RunError("a source file is sealed in more than one unit; nothing sealed")
    seal = self_digest(
        {
            "kind": SEAL_KIND,
            "version": 1,
            "stage": "first_pass_canonical_availability",
            "source_key": roots.source_key,
            "source": records[-1]["source"],
            "raw_contract": RAW_CONTRACT_ID,
            "plans": plans,
            "units": [
                {
                    "sequence": r["plan"]["sequence"],
                    "rank": r["rank"],
                    "file": r["file"],
                    "receipt_digest": r["digest"],
                    "raw_sha256": r["raw"]["sha256"],
                    "documents": r["documents"],
                    "canonical_bytes": r["canonical_bytes"],
                    "documents_sha256": r["documents_sha256"],
                }
                for r in receipts
            ],
            "membership_digest": membership_digest(receipts),
            "sufficiency": {k: v for k, v in status.items() if k != "digest"},
            "pending": [
                "global C05 deduplication and benchmark exclusion over the combined Mix-01 pool",
                "tokenizer training and exact XLM-token counting",
                "deterministic top-up after exact counting, if required",
            ],
            "training_permitted": False,
        }
    )
    write_once(roots.plans / "first-pass-seal.json", seal)
    return seal
