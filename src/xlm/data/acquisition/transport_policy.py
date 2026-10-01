"""Explicit transport modes and the versioned wall-time selection policy (offline).

A transport mode only changes how the same planned rows travel; it never
changes which rows are selected. The mode is chosen once per source, frozen in
a self-digested policy record and bound into every production plan, so a run
can never switch modes silently.

Selection rule (``mix01-transport-policy-v1``): among the modes that fit the
source's request, scratch and durable ceilings, choose the smallest modeled
wall time; when two modes are within ``TIE_FRACTION`` of each other, prefer
the one with fewer HTTP requests (fewer rate-limit and retry exposures).
There is deliberately no fixed byte-amplification threshold: extra bytes are
charged at the modeled bandwidth and against the disk ceilings instead.

Wall time is modeled, not assumed:

- network: one stream per file, files in waves of ``streams``: ``max(bytes /
  aggregate rate, waves * (requests * latency + bytes / per-stream rate) / files)``;
- local processing: rows over (rows per process-second * processes);
- local modes pipeline transfer and processing with one process per file, so
  their wall time is the slower of (all transfers, then the last file's
  processing) and (the first transfer, then all processing); the range path
  fetches first and adapts afterwards in one process, so its stages add.

Model inputs are either ``modeled`` (named prior measurements, such as the
Essential-Web campaign on the same Hugging Face endpoints) or ``measured``
(bounded benchmark receipts of this source). The basis is always recorded.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any

from xlm.data.evidence_v2 import canonical

POLICY_ID = "mix01-transport-policy-v1"
POLICY_KIND = "mix01_transport_policy"
TIE_FRACTION = 0.10
#: Whole-source transfer at or below this size is planned as one direct batch:
#: at the modeled 25 MB/s floor it is about 90 s and always fits scratch.
SMALL_SOURCE_MAX_BYTES = 2 * 1024**3
#: Default request ceiling per plan; the range path above it is infeasible.
DEFAULT_MAX_REQUESTS = 20_000
MB = 1_000_000


class TransportMode(StrEnum):
    """How planned rows travel. Receipt-bound; never switched at run time."""

    #: Whole upstream files stream to scratch, are verified, retained and processed locally.
    WHOLE_FILE_LOCAL = "whole_file_local"
    #: Whole files are transferred; only the planned row ranges are processed locally.
    ROW_GROUP_LOCAL = "row_group_local"
    #: Projected HTTP range reads of the planned row groups (``xlm data fetch``).
    RANGE_SELECTED = "range_selected"
    #: The whole (small) source in one bounded batch with low concurrency.
    SMALL_SOURCE_DIRECT = "small_source_direct"


LOCAL_MODES = frozenset(
    {
        TransportMode.WHOLE_FILE_LOCAL,
        TransportMode.ROW_GROUP_LOCAL,
        TransportMode.SMALL_SOURCE_DIRECT,
    }
)
#: Modes the production planner (``source_plan``) executes end to end. The range
#: path has no deterministic production planner yet (footer row ranges + limits).
EXECUTABLE_MODES = LOCAL_MODES
ALL_MODES = frozenset(TransportMode)


class PolicyError(ValueError):
    """A layout, model input or frozen policy is invalid."""


@dataclass(frozen=True)
class SourceLayout:
    """Measured physical facts of one source, each traced to a real artifact."""

    source_id: str
    #: Byte size of the one real file whose length a fetch observed.
    file_bytes: int
    #: Rows and all-column bytes of one real row group.
    group_rows: int
    group_bytes: int
    #: Bytes of the adapter's projected columns in that row group.
    projected_group_bytes: int
    #: HTTP range requests per row group and fixed requests per file (range path).
    range_requests_per_group: float
    metadata_requests_per_file: int
    #: Canonical bytes per sampled upstream row (rejections included).
    canonical_bytes_per_row: float
    #: Selected-record JSONL bytes per row written by the range path.
    range_record_bytes_per_row: float
    #: Footer and request-framing bytes the range path reads once per file.
    metadata_bytes_per_file: int
    #: Files in the source (None when no inventory exists).
    source_files: int | None
    evidence: Mapping[str, str]

    def __post_init__(self) -> None:
        if (
            min(self.file_bytes, self.group_rows, self.group_bytes, self.projected_group_bytes) < 1
            or self.canonical_bytes_per_row <= 0
            or self.range_requests_per_group <= 0
            or self.projected_group_bytes > self.group_bytes
        ):
            raise PolicyError(f"layout of '{self.source_id}' is not physically consistent")

    @property
    def whole_bytes_per_row(self) -> float:
        return self.group_bytes / self.group_rows

    @property
    def projected_bytes_per_row(self) -> float:
        return self.projected_group_bytes / self.group_rows

    @property
    def rows_per_file(self) -> int:
        """Estimated rows per file from the measured file size and row-group density."""
        return max(1, int(self.file_bytes / self.whole_bytes_per_row))


@dataclass(frozen=True)
class ThroughputModel:
    """Network and local-processing rates of one mode, with their provenance."""

    streams: int
    per_stream_bytes_per_second: float
    aggregate_bytes_per_second: float
    seconds_per_request: float
    rows_per_process_second: float
    processes: int
    basis: str

    def __post_init__(self) -> None:
        if (
            not 1 <= self.streams <= 16
            or not 1 <= self.processes <= 16
            or min(
                self.per_stream_bytes_per_second,
                self.aggregate_bytes_per_second,
                self.rows_per_process_second,
            )
            <= 0
            or self.seconds_per_request < 0
        ):
            raise PolicyError("throughput model needs positive rates and 1..16 workers")

    def network_seconds(self, transfer_bytes: float, requests: float, files: int) -> float:
        """Streams are per file (both the whole-file and the range path): files run in waves."""
        files = max(1, files)
        per_file = (
            requests * self.seconds_per_request + transfer_bytes / self.per_stream_bytes_per_second
        ) / files
        waves = math.ceil(files / self.streams)
        return max(transfer_bytes / self.aggregate_bytes_per_second, waves * per_file)

    def processing_seconds(self, rows: float, processes: int | None = None) -> float:
        return rows / (self.rows_per_process_second * (processes or self.processes))


@dataclass(frozen=True)
class Requirement:
    """How much canonical text the plan must make available."""

    source_id: str
    required_canonical_bytes: float
    safety_margin: float

    @property
    def planned_canonical_bytes(self) -> float:
        return self.required_canonical_bytes * self.safety_margin


@dataclass(frozen=True)
class Ceilings:
    max_requests: int = DEFAULT_MAX_REQUESTS
    scratch_bytes: int = 64 * 1024**3
    durable_bytes: int | None = None
    in_flight_files: int = 10


@dataclass(frozen=True)
class ModeWorkload:
    mode: TransportMode
    files: int
    rows_processed: int
    transfer_bytes: int
    requests: int
    scratch_peak_bytes: int
    durable_raw_bytes: int
    note: str


def required_rows(layout: SourceLayout, requirement: Requirement) -> int:
    return math.ceil(requirement.planned_canonical_bytes / layout.canonical_bytes_per_row)


def workloads(
    layout: SourceLayout, requirement: Requirement, ceilings: Ceilings
) -> list[ModeWorkload]:
    """The same planned rows under every applicable mode.

    The Mix-01 prefix planner selects whole files, so ``row_group_local`` (whole
    transfer, partial processing) is not a distinct candidate here; it applies
    only to plans that select row ranges inside files.
    """
    rows = required_rows(layout, requirement)
    files = math.ceil(rows / layout.rows_per_file)
    groups = math.ceil(rows / layout.group_rows)
    whole_rows = files * layout.rows_per_file
    whole_bytes = files * layout.file_bytes
    in_flight = min(files, ceilings.in_flight_files)
    result = [
        ModeWorkload(
            TransportMode.WHOLE_FILE_LOCAL,
            files,
            whole_rows,
            whole_bytes,
            2 * files,
            in_flight * layout.file_bytes,
            whole_bytes,
            "whole files in frozen inventory order; every row of each file is processed; "
            "the verified upstream file is retained",
        ),
        ModeWorkload(
            TransportMode.RANGE_SELECTED,
            files,
            rows,
            math.ceil(rows * layout.projected_bytes_per_row)
            + files * layout.metadata_bytes_per_file,
            math.ceil(groups * layout.range_requests_per_group)
            + files * layout.metadata_requests_per_file,
            math.ceil(rows * layout.range_record_bytes_per_row),
            math.ceil(rows * layout.range_record_bytes_per_row),
            "projected row-group ranges; selected-record JSONL staged, then adapted",
        ),
    ]
    if layout.source_files is not None:
        source_bytes = layout.source_files * layout.file_bytes
        if (
            source_bytes <= SMALL_SOURCE_MAX_BYTES
            and layout.source_files * layout.rows_per_file >= rows
        ):
            result.append(
                ModeWorkload(
                    TransportMode.SMALL_SOURCE_DIRECT,
                    layout.source_files,
                    layout.source_files * layout.rows_per_file,
                    source_bytes,
                    2 * layout.source_files,
                    min(layout.source_files, ceilings.in_flight_files) * layout.file_bytes,
                    source_bytes,
                    "the whole small source in one direct batch",
                )
            )
    return result


def wall_seconds(
    work: ModeWorkload, model: ThroughputModel, layout: SourceLayout
) -> dict[str, float]:
    network = model.network_seconds(work.transfer_bytes, work.requests, work.files)
    if work.mode in LOCAL_MODES:
        # One process per file: parallelism never exceeds the file count. The
        # pipeline ends after the slower of (all transfers, then the last file's
        # processing) and (the first transfer, then all processing).
        processing = model.processing_seconds(
            work.rows_processed, min(model.processes, max(1, work.files))
        )
        first = (
            layout.file_bytes / model.per_stream_bytes_per_second + 2 * model.seconds_per_request
        )
        last = model.processing_seconds(layout.rows_per_file, 1)
        total = max(network + last, first + processing)
    else:
        processing = model.processing_seconds(work.rows_processed, 1)
        total = network + processing
    return {
        "network_seconds": network,
        "request_latency_seconds": work.requests
        * model.seconds_per_request
        / min(model.streams, max(1, work.files)),
        "processing_seconds": processing,
        "wall_seconds": total,
    }


def evaluate(
    layout: SourceLayout,
    requirement: Requirement,
    ceilings: Ceilings,
    models: Mapping[TransportMode, ThroughputModel],
    executable: frozenset[TransportMode] = EXECUTABLE_MODES,
) -> dict[str, Any]:
    """Per-mode estimates, the selected mode with its reason, and the fastest modeled mode.

    Only modes a production planner can execute end to end are selectable; a
    faster mode without one is reported (``fastest_modeled``), never silently
    used.
    """
    rows = required_rows(layout, requirement)
    candidates: list[dict[str, Any]] = []
    for work in workloads(layout, requirement, ceilings):
        model = models.get(work.mode)
        if model is None:
            continue
        timing = wall_seconds(work, model, layout)
        refusals = []
        if work.requests > ceilings.max_requests:
            refusals.append(f"requests {work.requests} exceed {ceilings.max_requests}")
        if work.scratch_peak_bytes > ceilings.scratch_bytes:
            refusals.append("scratch peak exceeds the scratch ceiling")
        if ceilings.durable_bytes is not None and work.durable_raw_bytes > ceilings.durable_bytes:
            refusals.append("durable raw bytes exceed the durable ceiling")
        if work.mode not in executable:
            refusals.append(f"no production planner executes this mode under {POLICY_ID}")
        candidates.append(
            {
                **asdict(work),
                "mode": work.mode.value,
                "byte_amplification": work.transfer_bytes
                / max(1, math.ceil(rows * layout.projected_bytes_per_row)),
                **timing,
                "feasible": not refusals,
                "refusals": refusals,
                "model": asdict(model),
            }
        )
    fastest = min(candidates, key=lambda c: (c["wall_seconds"], c["requests"]), default=None)
    feasible = sorted(
        (c for c in candidates if c["feasible"]),
        key=lambda c: (c["wall_seconds"], c["requests"]),
    )
    if not feasible:
        raise PolicyError(f"no transport mode of '{layout.source_id}' fits its ceilings")
    best = feasible[0]
    near = [c for c in feasible if c["wall_seconds"] <= best["wall_seconds"] * (1 + TIE_FRACTION)]
    chosen = min(near, key=lambda c: (c["requests"], c["wall_seconds"]))
    reason = (
        f"smallest modeled wall time among feasible modes ({chosen['wall_seconds']:.0f} s)"
        if chosen is best
        else f"within {TIE_FRACTION:.0%} of the fastest ({best['mode']}, "
        f"{best['wall_seconds']:.0f} s) with {chosen['requests']} instead of "
        f"{best['requests']} requests"
    )
    return {
        "policy": POLICY_ID,
        "source_id": layout.source_id,
        "required_canonical_bytes": requirement.required_canonical_bytes,
        "safety_margin": requirement.safety_margin,
        "planned_canonical_bytes": requirement.planned_canonical_bytes,
        "required_rows": rows,
        "layout": {**asdict(layout), "rows_per_file_estimate": layout.rows_per_file},
        "ceilings": asdict(ceilings),
        "candidates": candidates,
        "selected_mode": chosen["mode"],
        "reason": reason,
        "fastest_modeled": None
        if fastest is None
        else {
            "mode": fastest["mode"],
            "wall_seconds": fastest["wall_seconds"],
            "executable": fastest["mode"] in {m.value for m in executable},
        },
    }


# ------------------------------------------------------------------ freeze


def freeze(report: Mapping[str, Any], *, basis: str, inputs: Mapping[str, str]) -> dict[str, Any]:
    """Self-digested policy record; the plan binds its digest, never a recomputation."""
    if basis not in ("modeled", "measured"):
        raise PolicyError("policy basis must be 'modeled' or 'measured'")
    if basis == "measured" and not inputs:
        raise PolicyError("a measured policy must bind its benchmark receipts")
    body = {
        "kind": POLICY_KIND,
        "policy": POLICY_ID,
        "basis": basis,
        "inputs": dict(sorted(inputs.items())),
        "source_id": report["source_id"],
        "selected_mode": report["selected_mode"],
        "reason": report["reason"],
        "report": dict(report),
    }
    body["digest"] = canonical.digest(body)
    return body


def check_frozen(record: Mapping[str, Any], source_id: str) -> TransportMode:
    body = dict(record)
    if body.pop("digest", None) != canonical.digest(body):
        raise PolicyError("transport policy digest does not verify")
    if record.get("kind") != POLICY_KIND or record.get("policy") != POLICY_ID:
        raise PolicyError("not a mix01-transport-policy-v1 record")
    if record.get("source_id") != source_id:
        raise PolicyError("transport policy belongs to another source")
    return TransportMode(str(record["selected_mode"]))


# ------------------------------------------------------------ model inputs


#: Named prior measurements on the same Hugging Face endpoints (Essential-Web
#: fast campaign, 2026-09-30; calibration fetch telemetry). Planning inputs only.
ESSENTIAL_WEB_MEASUREMENTS = {
    "one_stream_bytes_per_second": 22.7 * MB,
    "eight_stream_bytes_per_second": 142.7 * MB,
    "production_batch_end_to_end_bytes_per_second": 36.0 * MB,
    "local_rows_per_second_12_processes": 11_363.0,
}


def modeled_models(
    seconds_per_request: float,
    range_adapt_rows_per_second: float,
    *,
    streams: int = 8,
    processes: int = 12,
    range_workers: int = 8,
    aggregate_bytes_per_second: float | None = None,
) -> dict[TransportMode, ThroughputModel]:
    """Models from the named prior measurements; ``aggregate`` defaults to the campaign floor.

    The conservative aggregate is the end-to-end rate of a whole production
    batch (36 MB/s), not the 142.7 MB/s peak benchmark. Local processing uses
    the Essential-Web per-process rate (decode, serialize and three views:
    conservative for one view). The range path decodes inside the fetch and
    then adapts in one process at ``range_adapt_rows_per_second``, the rate
    ``xlm data adapt`` measured on this source's calibration.
    """
    measured = ESSENTIAL_WEB_MEASUREMENTS
    aggregate = (
        aggregate_bytes_per_second or measured["production_batch_end_to_end_bytes_per_second"]
    )
    rows_per_process = measured["local_rows_per_second_12_processes"] / 12
    basis = (
        "modeled: Essential-Web fast campaign rates on the same endpoints and this source's "
        "calibration request latency"
    )
    local = ThroughputModel(
        streams=streams,
        per_stream_bytes_per_second=measured["one_stream_bytes_per_second"],
        aggregate_bytes_per_second=aggregate,
        seconds_per_request=seconds_per_request,
        rows_per_process_second=rows_per_process,
        processes=processes,
        basis=basis,
    )
    ranged = ThroughputModel(
        streams=range_workers,
        per_stream_bytes_per_second=measured["one_stream_bytes_per_second"],
        aggregate_bytes_per_second=aggregate,
        seconds_per_request=seconds_per_request,
        rows_per_process_second=range_adapt_rows_per_second,
        processes=1,
        basis=basis,
    )
    return {
        TransportMode.WHOLE_FILE_LOCAL: local,
        TransportMode.ROW_GROUP_LOCAL: local,
        TransportMode.SMALL_SOURCE_DIRECT: local,
        TransportMode.RANGE_SELECTED: ranged,
    }


def measured_model(receipt: Mapping[str, Any]) -> ThroughputModel:
    """A throughput model from one bounded benchmark performance receipt."""
    transfer, rows = receipt["transfer"], receipt["processing"]
    wall = float(transfer["wall_seconds"])
    moved = float(transfer["transferred_bytes"])
    requests = int(transfer["requests"])
    streams = int(receipt["concurrency"]["download_workers"])
    if wall <= 0 or moved <= 0 or requests < 1:
        raise PolicyError("benchmark receipt has no usable transfer measurement")
    rows_per_second = float(rows["rows_per_process_second"])
    latency = float(transfer.get("mean_request_open_seconds") or 0.0)
    aggregate = moved / wall
    return ThroughputModel(
        streams=streams,
        per_stream_bytes_per_second=max(aggregate / streams, 1.0),
        aggregate_bytes_per_second=aggregate,
        seconds_per_request=latency,
        rows_per_process_second=rows_per_second,
        processes=max(1, int(receipt["concurrency"]["process_workers"])),
        basis=f"measured: benchmark receipt {receipt['digest']}",
    )


def layout_from_calibration(
    source_id: str,
    rows_evidence: Mapping[str, Any],
    perf: Mapping[str, Any],
    journal: Mapping[str, Any],
    measurement: Mapping[str, Any],
    *,
    source_files: int | None,
    evidence_names: Mapping[str, str],
) -> SourceLayout:
    """Physical layout from one real calibration: footer block, fetch telemetry, journal."""
    validators = journal.get("source_validators") or {}
    if len(validators) != 1:
        raise PolicyError("layout needs exactly one observed calibration file")
    (validator,) = validators.values()
    telemetry = perf["telemetry"]
    groups = max(1, int(telemetry.get("parquet_groups", 1)))
    rows_sampled = int(measurement["records_sampled"])
    record_bytes = float(perf_selected_record_bytes(journal)) / rows_sampled
    transferred = int(perf["transferred_bytes"])
    if rows_evidence.get("mode") == "rowgroup":
        (block,) = rows_evidence["blocks"]
        group_rows, group_bytes = int(block["num_rows"]), int(block["compressed_bytes"])
        projected = int(telemetry["projection_selected_bytes"]) // groups
        ranges = float(telemetry["coalesced_ranges"]) / groups
        metadata = int(perf["requests_made"]) - int(telemetry["coalesced_ranges"])
        metadata_bytes = max(0, transferred - int(telemetry["projection_selected_bytes"]))
    elif rows_evidence.get("mode") == "window":
        (window,) = rows_evidence["windows"]
        group_rows = int(window["group_rows"])
        group_bytes = int(window["group_compressed_bytes"])
        projected = int(window["physical_transfer_ceiling_bytes"])
        buffer = int(rows_evidence["window_policy"]["stream_buffer_bytes"])
        columns = len(window["selected_columns"])
        # Calculation, not a measurement: one request per stream buffer plus one per column.
        ranges = float(math.ceil(projected / buffer) + columns)
        metadata = 4
        scanned = int(telemetry.get("scanned_records", 0))
        metadata_bytes = max(0, transferred - math.ceil(projected * scanned / group_rows))
    else:
        raise PolicyError("calibration rows evidence has an unknown mode")
    return SourceLayout(
        source_id=source_id,
        file_bytes=int(validator["length"]),
        group_rows=group_rows,
        group_bytes=group_bytes,
        projected_group_bytes=projected,
        range_requests_per_group=ranges,
        metadata_requests_per_file=metadata,
        canonical_bytes_per_row=float(measurement["canonical_bytes"]) / rows_sampled,
        range_record_bytes_per_row=record_bytes,
        metadata_bytes_per_file=metadata_bytes,
        source_files=source_files,
        evidence=dict(evidence_names),
    )


_ADAPT_RATE = re.compile(r"Adapt throughput: (\d+) records in ([0-9.]+)s \(([0-9.]+)/s")


def adapt_rate_from_log(text: str) -> float | None:
    """Records per second ``xlm data adapt`` printed for a calibration; None when absent."""
    match = _ADAPT_RATE.search(text)
    return None if match is None else float(match[3])


def perf_selected_record_bytes(journal: Mapping[str, Any]) -> int:
    progress = (journal.get("file_progress") or {}).get("selected_records.jsonl") or {}
    value = progress.get("bytes_downloaded")
    if not isinstance(value, int) or value < 1:
        raise PolicyError("calibration journal records no selected-record bytes")
    return value


def request_latency(perf: Mapping[str, Any]) -> float:
    """Mean per-request open time measured by a calibration fetch."""
    telemetry = perf["telemetry"]
    return float(telemetry["open_seconds"]) / max(1, int(perf["requests_made"]))


def summarize(reports: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Compact per-source lines for operator display."""
    lines = []
    for report in reports:
        by_mode = {c["mode"]: c for c in report["candidates"]}
        lines.append(
            {
                "source_id": report["source_id"],
                "required_canonical_bytes": report["required_canonical_bytes"],
                "selected_mode": report["selected_mode"],
                "reason": report["reason"],
                "fastest_modeled": report.get("fastest_modeled"),
                "modes": {
                    mode: {
                        "transfer_bytes": c["transfer_bytes"],
                        "requests": c["requests"],
                        "byte_amplification": round(c["byte_amplification"], 3),
                        "rows_processed": c["rows_processed"],
                        "scratch_peak_bytes": c["scratch_peak_bytes"],
                        "durable_raw_bytes": c["durable_raw_bytes"],
                        "wall_seconds": round(c["wall_seconds"], 1),
                        "feasible": c["feasible"],
                    }
                    for mode, c in by_mode.items()
                },
            }
        )
    return lines
