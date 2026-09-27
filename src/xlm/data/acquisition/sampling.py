"""Deterministic dense row-group/block sampling plans for selected-record acquisition.

Planning only: inspects Parquet footers through bounded read-only access and
derives dense row-group-aligned ``row_ranges`` for ``xlm data plan``. Never
fetches record payloads, never writes journals or receipts, and never alters
acquisition fetch semantics.

Cost model (from real FinePDFs evidence): remote selected-record cost is
strongly row-group oriented -- decoding a group costs ~one group whether one
row or one hundred rows are retained. Sparse isolated-row selection therefore
pays full group decodes per kept row. This planner maximizes retained rows
per decoded group while keeping broad shard coverage, at the price of
explicitly documented non-uniformity: row-group/block sampling is NOT uniform
record sampling.
"""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass, field
from typing import Any, Protocol

import pyarrow.parquet as pq

from xlm.data.acquisition.plan import (
    PILOT_MAX_DECOMPRESSED_BYTES,
    PILOT_MAX_REQUESTS,
    PILOT_MAX_SCANNED_RECORDS,
    PILOT_MAX_TRANSFERRED_BYTES,
    ParquetWindowDecode,
)

SAMPLING_PLAN_VERSION = 1

WINDOW_WARNING = (
    "Window sampling is clustered, nonuniform sampling: each selected file "
    "contributes ONE contiguous window of rows from ONE row group, and the "
    "window start is restricted to the first scan-bounded rows of that group. "
    "Rows before the window start are physically decoded (scanned) but not "
    "retained. Do not treat screened statistics as unbiased corpus estimates."
)

#: Footer/header requests charged before any column bytes (header + footer
#: tail + metadata); a conservative constant for the request estimate.
WINDOW_METADATA_REQUESTS = 4

BIAS_WARNING = (
    "Row-group/block sampling is not uniform record sampling. Retained rows "
    "cluster in dense aligned blocks; shard, temporal, and domain clustering "
    "effects apply. Do not treat screened statistics as unbiased corpus estimates."
)


class SamplingRefusal(ValueError):
    """A sampling selection cannot be constructed within the supplied bounds."""


def canonical_range_url(provider: str, repository: str, revision: str, rel_path: str) -> str:
    """Canonical per-file URL for bounded footer discovery.

    Must stay identical to ``BoundedFetcher._resolve_url`` (same provider
    branches and quoting); covered by a parity test. Redirect handling,
    pooling, and budgets all behave as in acquisition.
    """
    import urllib.parse

    path = urllib.parse.quote(rel_path, safe="/")
    if provider == "huggingface":
        quoted_revision = urllib.parse.quote(revision, safe="")
        return f"https://huggingface.co/datasets/{repository}/resolve/{quoted_revision}/{path}"
    if provider == "https":
        return f"{repository.rstrip('/')}/{path}"
    raise ValueError(
        "sampling supports reviewed HTTP(S)/Hub endpoints; use import-local for local data"
    )


def _det_index(seed: int, parts: tuple[str, ...], modulus: int) -> int:
    """Deterministic index in ``[0, modulus)`` from seed plus identity parts.

    SHA-256 hash chain: stable across processes, Python versions, and
    platforms. Never mixes in timing, paths, worker counts, or network order.
    """
    if modulus <= 0:
        raise SamplingRefusal("deterministic choice requires at least one option")
    digest = hashlib.sha256("|".join([str(seed), *parts]).encode("utf-8")).hexdigest()
    return int(digest, 16) % modulus


@dataclass(frozen=True)
class ColumnChunkSpec:
    """One column chunk's footer sizes (``path_in_schema`` identifies it)."""

    path: str
    compressed: int
    uncompressed: int


@dataclass(frozen=True)
class RowGroupSpec:
    """One Parquet row group as observed in a bounded footer read."""

    index: int
    start_row: int
    num_rows: int
    total_byte_size: int
    uncompressed_bytes: int
    num_columns: int
    usable: bool
    refusal: str | None = None
    columns: tuple[ColumnChunkSpec, ...] = ()


@dataclass(frozen=True)
class FileLayout:
    """Footer-derived layout of one candidate Parquet file."""

    name: str
    num_rows: int
    groups: tuple[RowGroupSpec, ...] = field(default_factory=tuple)


def _refusal_for_group(
    group_index: int,
    num_rows: int,
    num_columns: int,
    total_byte_size: int,
    compressed_total: int,
    uncompressed_total: int,
    columns: tuple[tuple[int, int], ...],
    max_parser_bytes: int,
    max_decompression_ratio: float,
) -> str | None:
    """Parser/ratio refusal in the numeric style of acquisition diagnostics."""
    if total_byte_size > max_parser_bytes:
        return (
            f"Parquet row group {group_index} exceeds parser byte bound: "
            f"compared total_byte_size={total_byte_size} against "
            f"max_parser_bytes={max_parser_bytes}; "
            f"num_rows={num_rows}; "
            f"num_columns={num_columns}; "
            f"columns_total_compressed_size={compressed_total}; "
            f"columns_total_uncompressed_size={uncompressed_total}"
        )
    # Per-column ratio rule mirrors acquisition `check_row_group` exactly.
    for compressed, uncompressed in columns:
        if uncompressed > max_decompression_ratio * max(1, compressed):
            return (
                f"Parquet row group {group_index} exceeds decompression ratio bound: "
                f"columns_total_uncompressed_size={uncompressed_total} against "
                f"max_decompression_ratio={max_decompression_ratio} * "
                f"columns_total_compressed_size={compressed_total}; "
                f"num_rows={num_rows}"
            )
    return None


def discover_layout_local(
    path: Any,
    *,
    name: str,
    max_parser_bytes: int,
    max_decompression_ratio: float,
) -> FileLayout:
    """Read only a local Parquet footer into a :class:`FileLayout`.

    ``pyarrow`` opens footer metadata without decoding record batches.
    Oversized groups are flagged unusable (with numeric diagnostics), never
    fetched.
    """
    try:
        parquet = pq.ParquetFile(
            path,
            pre_buffer=False,
            thrift_string_size_limit=max_parser_bytes,
            thrift_container_size_limit=max_parser_bytes,
        )
        num_rows = int(parquet.metadata.num_rows)
        num_row_groups = int(parquet.num_row_groups)
    except SamplingRefusal:
        raise
    except Exception as exc:
        raise SamplingRefusal(f"cannot read Parquet footer for '{name}': {exc}") from exc
    if num_rows < 0 or num_row_groups < 0:
        raise SamplingRefusal(f"invalid Parquet metadata for '{name}'")
    groups: list[RowGroupSpec] = []
    base = 0
    for index in range(num_row_groups):
        meta = parquet.metadata.row_group(index)
        rows = int(meta.num_rows)
        columns = [meta.column(position) for position in range(meta.num_columns)]
        pairs = tuple(
            (int(column.total_compressed_size), int(column.total_uncompressed_size))
            for column in columns
        )
        chunk_specs = tuple(
            ColumnChunkSpec(str(column.path_in_schema), first, second)
            for column, (first, second) in zip(columns, pairs, strict=True)
        )
        compressed = sum(first for first, _ in pairs)
        uncompressed = sum(second for _, second in pairs)
        refusal = _refusal_for_group(
            index,
            rows,
            int(meta.num_columns),
            int(meta.total_byte_size),
            compressed,
            uncompressed,
            pairs,
            max_parser_bytes,
            max_decompression_ratio,
        )
        groups.append(
            RowGroupSpec(
                index=index,
                start_row=base,
                num_rows=rows,
                total_byte_size=int(meta.total_byte_size),
                uncompressed_bytes=uncompressed,
                num_columns=int(meta.num_columns),
                usable=refusal is None,
                refusal=refusal,
                columns=chunk_specs,
            )
        )
        base += rows
    return FileLayout(name=name, num_rows=num_rows, groups=tuple(groups))


#: (rows, columns, total_byte_size, compressed, uncompressed, pairs, chunks)
_GroupFooter = tuple[
    int, int, int, int, int, tuple[tuple[int, int], ...], tuple[ColumnChunkSpec, ...]
]


class RangeFetch(Protocol):
    """Bounded exact-range fetch returning ``(bytes, total_length)``."""

    def __call__(self, start: int, end: int) -> tuple[bytes, int]: ...


class SeekableRangeInput(io.RawIOBase):
    """Seekable Parquet input over a bounded range-fetch callback (footer only)."""

    def __init__(self, fetch: RangeFetch) -> None:
        self._fetch, self.position = fetch, 0
        magic, self.length = fetch(0, 3)
        if magic != b"PAR1":
            raise SamplingRefusal("selected Parquet input lacks PAR1 header")

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self.position

    def seek(self, offset: int, whence: int = 0) -> int:
        position = (
            offset if whence == 0 else (self.position if whence == 1 else self.length) + offset
        )
        if whence not in (0, 1, 2) or not 0 <= position <= self.length:
            raise SamplingRefusal("invalid Parquet range seek")
        self.position = position
        return position

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            raise SamplingRefusal("unbounded Parquet metadata read refused")
        size = min(size, self.length - self.position)
        if size == 0:
            return b""
        value, total = self._fetch(self.position, self.position + size - 1)
        if total != self.length:
            raise SamplingRefusal("Parquet source length changed during discovery")
        self.position += size
        return value


def discover_layout_over_ranges(
    name: str,
    fetch: RangeFetch,
    *,
    max_parser_bytes: int,
    max_decompression_ratio: float,
) -> FileLayout:
    """Derive a :class:`FileLayout` through bounded range reads (no full shard)."""
    try:
        with SeekableRangeInput(fetch) as stream:
            parquet = pq.ParquetFile(
                stream,
                pre_buffer=False,
                buffer_size=0,
                thrift_string_size_limit=max_parser_bytes,
                thrift_container_size_limit=max_parser_bytes,
            )
            num_rows = int(parquet.metadata.num_rows)
            num_row_groups = int(parquet.num_row_groups)
            specs: list[_GroupFooter] = []
            for index in range(num_row_groups):
                meta = parquet.metadata.row_group(index)
                columns = [meta.column(position) for position in range(meta.num_columns)]
                pairs = tuple(
                    (int(c.total_compressed_size), int(c.total_uncompressed_size)) for c in columns
                )
                specs.append(
                    (
                        int(meta.num_rows),
                        int(meta.num_columns),
                        int(meta.total_byte_size),
                        sum(first for first, _ in pairs),
                        sum(second for _, second in pairs),
                        pairs,
                        tuple(
                            ColumnChunkSpec(str(c.path_in_schema), first, second)
                            for c, (first, second) in zip(columns, pairs, strict=True)
                        ),
                    )
                )
    except SamplingRefusal:
        raise
    except Exception as exc:
        raise SamplingRefusal(f"cannot discover Parquet layout for '{name}': {exc}") from exc
    groups: list[RowGroupSpec] = []
    base = 0
    for index, (
        rows,
        num_columns,
        total,
        compressed,
        uncompressed,
        pairs,
        chunk_specs,
    ) in enumerate(specs):
        refusal = _refusal_for_group(
            index,
            rows,
            num_columns,
            total,
            compressed,
            uncompressed,
            pairs,
            max_parser_bytes,
            max_decompression_ratio,
        )
        groups.append(
            RowGroupSpec(
                index=index,
                start_row=base,
                num_rows=rows,
                total_byte_size=total,
                uncompressed_bytes=uncompressed,
                num_columns=num_columns,
                usable=refusal is None,
                refusal=refusal,
                columns=chunk_specs,
            )
        )
        base += rows
    return FileLayout(name=name, num_rows=num_rows, groups=tuple(groups))


@dataclass(frozen=True)
class SamplingRequest:
    """Pure logical inputs for block selection (no timing, workers, or paths)."""

    source_id: str
    view_id: str
    revision: str
    files: tuple[str, ...]
    seed: int
    mode: str = "rowgroup"
    block_records: int = 1000
    target_records: int = 1000
    max_overshoot_records: int = 0
    max_blocks_per_file: int | None = None
    max_records: int | None = None
    max_uncompressed_bytes: int | None = None
    max_parser_bytes: int = 32 * 1024 * 1024
    max_decompression_ratio: float = 15.0
    tokens_per_record: float | None = None
    projected_fields: tuple[str, ...] | None = None
    window: ParquetWindowDecode | None = None


@dataclass(frozen=True)
class ChosenBlock:
    file: str
    group_start: int
    group_stop_exclusive: int
    start_row: int
    stop_row: int
    num_rows: int
    compressed_bytes: int
    uncompressed_bytes: int


@dataclass(frozen=True)
class ChosenWindow:
    """One deterministic sub-row-group window and its physical work.

    Byte estimates assume rows of roughly uniform size within the group; they
    are labeled estimates. The hard ceilings are the runtime budgets, and
    ``selected_compressed_bytes`` bounds what the window path can transfer
    from column chunks (it never requests unprojected chunks).
    """

    file: str
    row_group: int
    group_start_row: int
    group_rows: int
    start_domain_rows: int
    start_in_group: int
    stop_in_group: int
    expected_scan_rows: int
    selected_columns: tuple[ColumnChunkSpec, ...]
    group_total_byte_size: int
    group_compressed_bytes: int
    estimated_scan_compressed_bytes: int
    estimated_scan_uncompressed_bytes: int
    estimated_transfer_upper_bytes: int
    estimated_requests: int
    domain_scan_rows: int = 0
    domain_estimated_transfer_upper_bytes: int = 0
    domain_estimated_scan_uncompressed_bytes: int = 0
    domain_estimated_requests: int = 0

    @property
    def start_row(self) -> int:
        return self.group_start_row + self.start_in_group

    @property
    def stop_row(self) -> int:
        return self.group_start_row + self.stop_in_group

    @property
    def num_rows(self) -> int:
        return self.stop_in_group - self.start_in_group

    @property
    def selected_compressed_bytes(self) -> int:
        return sum(column.compressed for column in self.selected_columns)

    @property
    def selected_uncompressed_bytes(self) -> int:
        return sum(column.uncompressed for column in self.selected_columns)

    def to_report(self) -> dict[str, Any]:
        largest = max(self.selected_columns, key=lambda column: (column.compressed, column.path))
        return {
            "file": self.file,
            "row_group": self.row_group,
            "group_start_row": self.group_start_row,
            "group_rows": self.group_rows,
            "start_domain_rows": self.start_domain_rows,
            "start_in_group": self.start_in_group,
            "stop_in_group": self.stop_in_group,
            "start_row": self.start_row,
            "stop_row": self.stop_row,
            "selected_count": self.num_rows,
            "expected_scan_rows": self.expected_scan_rows,
            "expected_skipped_decoded_rows": self.expected_scan_rows - self.num_rows,
            "group_total_byte_size": self.group_total_byte_size,
            "group_compressed_bytes": self.group_compressed_bytes,
            "selected_columns": [
                {
                    "path": column.path,
                    "compressed_bytes": column.compressed,
                    "uncompressed_bytes": column.uncompressed,
                }
                for column in self.selected_columns
            ],
            "selected_compressed_bytes": self.selected_compressed_bytes,
            "selected_uncompressed_bytes": self.selected_uncompressed_bytes,
            "unselected_compressed_bytes": (
                self.group_compressed_bytes - self.selected_compressed_bytes
            ),
            "largest_selected_chunk": {
                "path": largest.path,
                "compressed_bytes": largest.compressed,
                "uncompressed_bytes": largest.uncompressed,
            },
            "physical_transfer_ceiling_bytes": self.selected_compressed_bytes,
            "estimated_scan_compressed_bytes": self.estimated_scan_compressed_bytes,
            "estimated_scan_uncompressed_bytes": self.estimated_scan_uncompressed_bytes,
            "estimated_transfer_upper_bytes": self.estimated_transfer_upper_bytes,
            "estimated_requests": self.estimated_requests,
            "eligibility_worst_case": {
                "basis": "any admissible start: window ending at start_domain_rows",
                "expected_scan_rows": self.domain_scan_rows,
                "estimated_transfer_upper_bytes": self.domain_estimated_transfer_upper_bytes,
                "estimated_scan_uncompressed_bytes": (
                    self.domain_estimated_scan_uncompressed_bytes
                ),
                "estimated_requests": self.domain_estimated_requests,
            },
        }


@dataclass(frozen=True)
class SamplingResult:
    row_ranges: dict[str, tuple[int, int]]
    selected_files: tuple[str, ...]
    blocks: tuple[ChosenBlock, ...]
    requested_records: int
    planned_records: int
    overshoot_records: int
    estimated_compressed_bytes: int
    estimated_uncompressed_bytes: int
    estimated_transfer_bytes: None = None
    estimated_tokens: float | None = None
    seed: int = 0
    mode: str = "rowgroup"
    source_id: str = ""
    view_id: str = ""
    revision: str = ""
    warnings: tuple[str, ...] = ()
    windows: tuple[ChosenWindow, ...] = ()
    window_policy: ParquetWindowDecode | None = None
    projected_fields: tuple[str, ...] | None = None

    def to_report(self) -> dict[str, Any]:
        """Deterministic evidence document (no paths, timing, or network order)."""
        report = self._legacy_report()
        if self.window_policy is not None:
            # Window evidence is additive: legacy-mode reports stay byte-identical.
            report["window_policy"] = {
                **self.window_policy.model_dump(),
                "start_construction": (
                    "sha256('|'.join([seed, source_id, view_id, revision, file, "
                    "row_group, 'window-v<policy_version>', 'start'])) mod "
                    "(start_domain_rows - selected_count + 1)"
                ),
                "scan_accounting": (
                    "rows [group start, window stop) are decoded in whole batches of "
                    "batch_rows and ALL are charged as scanned; nothing past the "
                    "batch reaching the window stop is decoded"
                ),
                "estimate_basis": (
                    "byte/request estimates assume uniform row size within the row "
                    "group; runtime transfer/decompression/request/scan budgets are "
                    "the hard bounds"
                ),
            }
            report["projected_fields"] = sorted(self.projected_fields or ())
            report["windows"] = [window.to_report() for window in self.windows]
            report["expected_scan_rows"] = sum(w.expected_scan_rows for w in self.windows)
            report["physical_transfer_ceiling_bytes"] = sum(
                w.selected_compressed_bytes for w in self.windows
            )
            report["estimated_transfer_upper_bytes"] = sum(
                w.estimated_transfer_upper_bytes for w in self.windows
            )
            report["estimated_requests"] = sum(w.estimated_requests for w in self.windows)
            report["bias"] = WINDOW_WARNING
        return report

    def _legacy_report(self) -> dict[str, Any]:
        return {
            "sampling_plan_version": SAMPLING_PLAN_VERSION,
            "source_id": self.source_id,
            "view_id": self.view_id,
            "revision": self.revision,
            "seed": self.seed,
            "mode": self.mode,
            "requested_records": self.requested_records,
            "planned_records": self.planned_records,
            "overshoot_records": self.overshoot_records,
            "selected_files": list(self.selected_files),
            "row_ranges": {
                name: [start, stop] for name, (start, stop) in sorted(self.row_ranges.items())
            },
            "blocks": [
                {
                    "file": block.file,
                    "group_start": block.group_start,
                    "group_stop_exclusive": block.group_stop_exclusive,
                    "start_row": block.start_row,
                    "stop_row": block.stop_row,
                    "num_rows": block.num_rows,
                    "compressed_bytes": block.compressed_bytes,
                    "uncompressed_bytes": block.uncompressed_bytes,
                }
                for block in self.blocks
            ],
            "estimated_rows": self.planned_records,
            "estimated_compressed_bytes": self.estimated_compressed_bytes,
            "estimated_uncompressed_bytes": self.estimated_uncompressed_bytes,
            "estimated_transfer_bytes": None,
            "transfer_estimate_note": (
                "unknown: range-request transfer depends on column projection and "
                "request framing, not only footer sizes; never fabricated"
            ),
            "estimated_tokens": self.estimated_tokens,
            "warnings": list(self.warnings),
            "bias": BIAS_WARNING,
        }


def _validate_request(request: SamplingRequest, layouts: dict[str, FileLayout]) -> None:
    if not request.files:
        raise SamplingRefusal("at least one candidate file is required")
    if len(set(request.files)) != len(request.files):
        raise SamplingRefusal("duplicate candidate files are not allowed")
    if len(request.files) > 256:
        raise SamplingRefusal("sampling supports at most 256 candidate files")
    unknown = [name for name in request.files if name not in layouts]
    if unknown:
        raise SamplingRefusal(f"missing layouts for candidate files: {unknown}")
    for name in request.files:
        if not name.endswith(".parquet"):
            raise SamplingRefusal(
                f"row-group sampling requires Parquet files; '{name}' is unsupported"
            )
    if request.mode not in ("rowgroup", "contiguous", "window"):
        raise SamplingRefusal("sampling mode must be 'rowgroup', 'contiguous', or 'window'")
    if request.mode == "window":
        if request.window is None or not request.projected_fields:
            raise SamplingRefusal(
                "window mode requires an explicit window policy and column projection"
            )
        if len(set(request.projected_fields)) != len(request.projected_fields):
            raise SamplingRefusal("duplicate projected fields are not allowed")
        if request.window.stream_buffer_bytes > request.max_parser_bytes:
            raise SamplingRefusal("window stream buffer exceeds the per-range byte bound")
        if request.window.batch_rows > request.window.max_window_scan_rows:
            raise SamplingRefusal("window batch rows exceed the window scan bound")
    elif request.window is not None or request.projected_fields is not None:
        raise SamplingRefusal("window policy/projection apply to window mode only")
    if request.block_records < 1:
        raise SamplingRefusal("block records must be positive")
    if request.target_records < 1:
        raise SamplingRefusal("target records must be positive")
    if request.max_overshoot_records < 0:
        raise SamplingRefusal("max overshoot records cannot be negative")
    if request.max_blocks_per_file is not None and request.max_blocks_per_file < 1:
        raise SamplingRefusal("max blocks per file must be positive")
    if request.max_records is not None and request.max_records < 1:
        raise SamplingRefusal("max records bound must be positive")
    if request.max_uncompressed_bytes is not None and request.max_uncompressed_bytes < 1:
        raise SamplingRefusal("max uncompressed bytes bound must be positive")
    if request.tokens_per_record is not None and request.tokens_per_record <= 0:
        raise SamplingRefusal("tokens per record factor must be positive")


def plan_sample_blocks(layouts: dict[str, FileLayout], request: SamplingRequest) -> SamplingResult:
    """Choose dense row-group-aligned intervals deterministically.

    Diversity-first round robin over seed-rotated file order; one aligned
    block per selected file per round. Pure function of logical inputs.
    Window mode delegates to :func:`plan_sample_windows`.
    """
    _validate_request(request, layouts)
    if request.mode == "window":
        return plan_sample_windows(layouts, request)
    usable: dict[str, list[RowGroupSpec]] = {}
    skipped: dict[str, str] = {}
    for name in request.files:
        ok = [group for group in layouts[name].groups if group.usable]
        if ok:
            usable[name] = ok
        else:
            first = next(
                (group.refusal for group in layouts[name].groups if group.refusal is not None),
                f"no row groups observed in '{name}'",
            )
            skipped[name] = str(first)
    if not usable:
        detail = "; ".join(f"{name}: {reason}" for name, reason in sorted(skipped.items()))
        raise SamplingRefusal(f"no usable row groups within bounds: {detail}")
    ordered = sorted(usable)
    rotation = _det_index(
        request.seed,
        (request.source_id, request.view_id, request.revision, request.mode, "order"),
        len(ordered),
    )
    order = ordered[rotation:] + ordered[:rotation]
    by_index: dict[str, dict[int, RowGroupSpec]] = {
        name: {group.index: group for group in layouts[name].groups} for name in usable
    }
    start_choice: dict[str, int] = {}
    for name in usable:
        options = usable[name]
        position = _det_index(
            request.seed,
            (request.source_id, request.view_id, request.revision, name, request.mode, "start"),
            len(options),
        )
        start_choice[name] = options[position].index

    chosen: dict[str, list[RowGroupSpec]] = {name: [] for name in usable}
    truncated: set[str] = set()

    def initial_run(name: str) -> list[RowGroupSpec] | None:
        groups = by_index[name]
        first = groups[start_choice[name]]
        run = [first]
        if request.mode == "contiguous":
            rows = first.num_rows
            cursor = first.index + 1
            while rows < request.block_records:
                nxt = groups.get(cursor)
                if nxt is None or not nxt.usable:
                    break
                if request.max_blocks_per_file is not None and len(run) >= (
                    request.max_blocks_per_file
                ):
                    break
                run.append(nxt)
                rows += nxt.num_rows
                cursor += 1
        if request.max_blocks_per_file is not None and len(run) > request.max_blocks_per_file:
            run = run[: request.max_blocks_per_file]
        return run

    def extend_run(name: str) -> list[RowGroupSpec] | None:
        current = chosen[name]
        if request.max_blocks_per_file is not None and len(current) >= (
            request.max_blocks_per_file
        ):
            return None
        nxt = by_index[name].get(current[-1].index + 1)
        if nxt is None:
            return None
        if not nxt.usable:
            truncated.add(name)
            return None
        return [nxt]

    planned = 0
    planned_bytes = 0
    while planned < request.target_records:
        progressed = False
        for name in order:
            if planned >= request.target_records:
                break
            run = initial_run(name) if not chosen[name] else extend_run(name)
            if not run:
                continue
            rows = sum(group.num_rows for group in run)
            uncompressed = sum(group.uncompressed_bytes for group in run)
            if planned == 0:
                if request.max_records is not None and rows > request.max_records:
                    raise SamplingRefusal(
                        f"first aligned block ({rows} records) exceeds max records "
                        f"bound {request.max_records}"
                    )
                if (
                    request.max_uncompressed_bytes is not None
                    and uncompressed > request.max_uncompressed_bytes
                ):
                    raise SamplingRefusal(
                        f"first aligned block ({uncompressed} bytes) exceeds max "
                        f"uncompressed bytes bound {request.max_uncompressed_bytes}"
                    )
            else:
                if request.max_records is not None and planned + rows > request.max_records:
                    continue
                if (
                    request.max_uncompressed_bytes is not None
                    and planned_bytes + uncompressed > request.max_uncompressed_bytes
                ):
                    continue
                if rows - (request.target_records - planned) > request.max_overshoot_records:
                    continue
            chosen[name].extend(run)
            planned += rows
            planned_bytes += uncompressed
            progressed = True
        if not progressed:
            break
    if planned == 0:
        raise SamplingRefusal("no blocks selected within the supplied bounds")
    blocks: list[ChosenBlock] = []
    row_ranges: dict[str, tuple[int, int]] = {}
    compressed_total = 0
    ordered_blocks: list[tuple[str, list[RowGroupSpec]]] = [
        (name, chosen[name]) for name in order if chosen[name]
    ]
    for name, groups in ordered_blocks:
        for position, group in enumerate(groups[1:], start=1):
            if group.start_row != groups[position - 1].start_row + groups[position - 1].num_rows:
                raise SamplingRefusal(f"noncontiguous row groups selected in '{name}'")
        start_row = groups[0].start_row
        stop_row = groups[-1].start_row + groups[-1].num_rows
        rows = stop_row - start_row
        compressed = sum(group.total_byte_size for group in groups)
        uncompressed = sum(group.uncompressed_bytes for group in groups)
        blocks.append(
            ChosenBlock(
                file=name,
                group_start=groups[0].index,
                group_stop_exclusive=groups[-1].index + 1,
                start_row=start_row,
                stop_row=stop_row,
                num_rows=rows,
                compressed_bytes=compressed,
                uncompressed_bytes=uncompressed,
            )
        )
        row_ranges[name] = (start_row, stop_row)
        compressed_total += compressed
    shortfall = max(0, request.target_records - planned)
    overshoot = max(0, planned - request.target_records)
    warnings: list[str] = [BIAS_WARNING]
    if shortfall:
        warnings.append(
            f"planned {planned} records fall {shortfall} short of target "
            f"{request.target_records}; bounds or file exhaustion limited coverage"
        )
    if overshoot:
        warnings.append(
            f"planned {planned} records overshoot target {request.target_records} by "
            f"{overshoot} to preserve row-group alignment"
        )
    for name in sorted(skipped):
        warnings.append(f"skipped '{name}': {skipped[name]}")
    for name in sorted(truncated):
        warnings.append(f"'{name}' truncated at an unusable row group boundary")
    estimated_tokens: float | None = None
    if request.tokens_per_record is not None:
        estimated_tokens = planned * request.tokens_per_record
    return SamplingResult(
        row_ranges=row_ranges,
        selected_files=tuple(name for name, _ in ordered_blocks),
        blocks=tuple(blocks),
        requested_records=request.target_records,
        planned_records=planned,
        overshoot_records=overshoot,
        estimated_compressed_bytes=compressed_total,
        estimated_uncompressed_bytes=planned_bytes,
        estimated_tokens=estimated_tokens,
        seed=request.seed,
        mode=request.mode,
        source_id=request.source_id,
        view_id=request.view_id,
        revision=request.revision,
        warnings=tuple(warnings),
    )


def _window_domain(group_rows: int, window: ParquetWindowDecode) -> int:
    """Rows a window may end within: whole batches inside the scan bound."""
    if group_rows <= window.max_window_scan_rows:
        return group_rows
    return (window.max_window_scan_rows // window.batch_rows) * window.batch_rows


def _window_estimates(
    columns: tuple[ColumnChunkSpec, ...],
    group_rows: int,
    scan_rows: int,
    buffer_bytes: int,
) -> tuple[int, int, int, int]:
    """(scan compressed, scan uncompressed, transfer upper, requests) estimates."""
    scan_compressed = scan_uncompressed = transfer = requests = 0
    for column in columns:
        prefix = -(-column.compressed * scan_rows // group_rows)
        scan_compressed += prefix
        scan_uncompressed += -(-column.uncompressed * scan_rows // group_rows)
        # One buffered read may run up to a buffer past the needed prefix.
        read = min(column.compressed, prefix + buffer_bytes)
        transfer += read
        requests += max(1, -(-read // buffer_bytes))
    return scan_compressed, scan_uncompressed, transfer, requests + WINDOW_METADATA_REQUESTS


def _window_group_refusal(
    group: RowGroupSpec,
    projected: tuple[str, ...],
    request: SamplingRequest,
) -> tuple[str | None, tuple[ColumnChunkSpec, ...]]:
    """Projection-aware eligibility of one row group for a scan-bounded window."""
    window = request.window
    if window is None:
        raise SamplingRefusal("window eligibility requires a window policy")
    if group.num_rows < 1:
        return f"row group {group.index} is empty", ()
    by_path = {column.path: column for column in group.columns}
    missing = [name for name in projected if name not in by_path]
    if missing:
        nested = [name for name in missing if any(p.startswith(name + ".") for p in by_path)]
        if nested:
            return (
                f"window mode supports flat projected columns only; nested: {sorted(nested)}",
                (),
            )
        return f"projected fields not present: {sorted(missing)}", ()
    selected = tuple(by_path[name] for name in projected)
    ratio = window.ratio_refusal(
        ((column.path, column.compressed, column.uncompressed) for column in selected),
        request.max_decompression_ratio,
    )
    if ratio is not None:
        return f"row group {group.index} {ratio}", ()
    domain = _window_domain(group.num_rows, window)
    worst_scan = window.expected_scan_rows(group.num_rows, domain)
    scan_c, scan_u, transfer, requests = _window_estimates(
        selected, group.num_rows, worst_scan, window.stream_buffer_bytes
    )
    for label, value, bound in (
        ("expected_scan_rows", worst_scan, PILOT_MAX_SCANNED_RECORDS),
        ("estimated_transfer_upper_bytes", transfer, PILOT_MAX_TRANSFERRED_BYTES),
        ("estimated_scan_uncompressed_bytes", scan_u, PILOT_MAX_DECOMPRESSED_BYTES),
        ("estimated_requests", requests, PILOT_MAX_REQUESTS),
    ):
        if value > bound:
            return (
                f"row group {group.index} worst-case window exceeds pilot bound: "
                f"{label}={value} against {bound}; num_rows={group.num_rows}; "
                f"estimated_scan_compressed_bytes={scan_c}"
            ), ()
    return None, selected


def plan_sample_windows(layouts: dict[str, FileLayout], request: SamplingRequest) -> SamplingResult:
    """Deterministic scan-bounded sub-row-group windows (at most one per file).

    For a row group of N rows, target window K, and start domain D (N, or
    the whole batches inside ``max_window_scan_rows``), the start is a
    versioned SHA-256 choice in ``[0, D - K]`` and the window is
    ``[start, start + min(K, D))``. Eligibility uses the worst-case window
    (ending at D), so it never depends on the chosen start. Pure function
    of logical inputs.
    """
    _validate_request(request, layouts)
    window = request.window
    projected = request.projected_fields
    if request.mode != "window" or window is None or not projected:
        raise SamplingRefusal("window planning requires window mode, policy, and projection")
    version = f"window-v{window.policy_version}"
    eligible: dict[str, list[tuple[RowGroupSpec, tuple[ColumnChunkSpec, ...]]]] = {}
    skipped: dict[str, str] = {}
    for name in request.files:
        options: list[tuple[RowGroupSpec, tuple[ColumnChunkSpec, ...]]] = []
        first_refusal: str | None = None
        for group in layouts[name].groups:
            refusal, selected = _window_group_refusal(group, projected, request)
            if refusal is None:
                options.append((group, selected))
            elif first_refusal is None:
                first_refusal = refusal
        if options:
            eligible[name] = options
        else:
            skipped[name] = first_refusal or f"no row groups observed in '{name}'"
    if not eligible:
        detail = "; ".join(f"{name}: {reason}" for name, reason in sorted(skipped.items()))
        raise SamplingRefusal(f"no row groups eligible for window sampling: {detail}")
    ordered = sorted(eligible)
    rotation = _det_index(
        request.seed,
        (request.source_id, request.view_id, request.revision, request.mode, version, "order"),
        len(ordered),
    )
    order = ordered[rotation:] + ordered[:rotation]
    windows: list[ChosenWindow] = []
    planned = 0
    planned_bytes = 0
    truncation_notes: list[str] = []
    for name in order:
        if planned >= request.target_records:
            break
        options = eligible[name]
        group, selected = options[
            _det_index(
                request.seed,
                (request.source_id, request.view_id, request.revision, name, version, "group"),
                len(options),
            )
        ]
        domain = _window_domain(group.num_rows, window)
        wanted = min(request.block_records, request.target_records - planned)
        size = min(wanted, domain)
        start = _det_index(
            request.seed,
            (
                request.source_id,
                request.view_id,
                request.revision,
                name,
                str(group.index),
                version,
                "start",
            ),
            domain - size + 1,
        )
        stop = start + size
        scan_rows = window.expected_scan_rows(group.num_rows, stop)
        scan_c, scan_u, transfer, requests = _window_estimates(
            selected, group.num_rows, scan_rows, window.stream_buffer_bytes
        )
        domain_scan = window.expected_scan_rows(group.num_rows, domain)
        _, domain_scan_u, domain_transfer, domain_requests = _window_estimates(
            selected, group.num_rows, domain_scan, window.stream_buffer_bytes
        )
        if request.max_records is not None and planned + size > request.max_records:
            if planned == 0:
                raise SamplingRefusal(
                    f"first window ({size} records) exceeds max records bound {request.max_records}"
                )
            continue
        if (
            request.max_uncompressed_bytes is not None
            and planned_bytes + scan_u > request.max_uncompressed_bytes
        ):
            if planned == 0:
                raise SamplingRefusal(
                    f"first window (estimated {scan_u} decoded bytes) exceeds max "
                    f"uncompressed bytes bound {request.max_uncompressed_bytes}"
                )
            continue
        if size < wanted:
            truncation_notes.append(
                f"'{name}' window truncated to {size} rows by its row group/scan domain"
            )
        windows.append(
            ChosenWindow(
                file=name,
                row_group=group.index,
                group_start_row=group.start_row,
                group_rows=group.num_rows,
                start_domain_rows=domain,
                start_in_group=start,
                stop_in_group=stop,
                expected_scan_rows=scan_rows,
                selected_columns=selected,
                group_total_byte_size=group.total_byte_size,
                group_compressed_bytes=sum(column.compressed for column in group.columns),
                estimated_scan_compressed_bytes=scan_c,
                estimated_scan_uncompressed_bytes=scan_u,
                estimated_transfer_upper_bytes=transfer,
                estimated_requests=requests,
                domain_scan_rows=domain_scan,
                domain_estimated_transfer_upper_bytes=domain_transfer,
                domain_estimated_scan_uncompressed_bytes=domain_scan_u,
                domain_estimated_requests=domain_requests,
            )
        )
        planned += size
        planned_bytes += scan_u
    if not windows:
        raise SamplingRefusal("no windows selected within the supplied bounds")
    for label, value, bound in (
        (
            "expected_scan_rows",
            sum(w.expected_scan_rows for w in windows),
            PILOT_MAX_SCANNED_RECORDS,
        ),
        (
            "estimated_transfer_upper_bytes",
            sum(w.estimated_transfer_upper_bytes for w in windows),
            PILOT_MAX_TRANSFERRED_BYTES,
        ),
        (
            "estimated_scan_uncompressed_bytes",
            sum(w.estimated_scan_uncompressed_bytes for w in windows),
            PILOT_MAX_DECOMPRESSED_BYTES,
        ),
        ("estimated_requests", sum(w.estimated_requests for w in windows), PILOT_MAX_REQUESTS),
    ):
        if value > bound:
            raise SamplingRefusal(
                f"combined windows exceed pilot bound: {label}={value} against {bound}"
            )
    warnings: list[str] = [WINDOW_WARNING]
    shortfall = max(0, request.target_records - planned)
    if shortfall:
        warnings.append(
            f"planned {planned} records fall {shortfall} short of target "
            f"{request.target_records}; one window per file and bounds limited coverage"
        )
    warnings.extend(truncation_notes)
    for chosen in windows:
        if chosen.start_domain_rows < chosen.group_rows:
            warnings.append(
                f"'{chosen.file}' window start restricted to the first "
                f"{chosen.start_domain_rows} of {chosen.group_rows} rows of row group "
                f"{chosen.row_group} by max_window_scan_rows"
            )
        warnings.append(
            f"'{chosen.file}' decodes {chosen.expected_scan_rows} rows to retain {chosen.num_rows}"
        )
    for name in sorted(skipped):
        warnings.append(f"skipped '{name}': {skipped[name]}")
    estimated_tokens: float | None = None
    if request.tokens_per_record is not None:
        estimated_tokens = planned * request.tokens_per_record
    return SamplingResult(
        row_ranges={chosen.file: (chosen.start_row, chosen.stop_row) for chosen in windows},
        selected_files=tuple(chosen.file for chosen in windows),
        blocks=(),
        requested_records=request.target_records,
        planned_records=planned,
        overshoot_records=0,
        estimated_compressed_bytes=sum(w.estimated_scan_compressed_bytes for w in windows),
        estimated_uncompressed_bytes=planned_bytes,
        estimated_tokens=estimated_tokens,
        seed=request.seed,
        mode=request.mode,
        source_id=request.source_id,
        view_id=request.view_id,
        revision=request.revision,
        warnings=tuple(warnings),
        windows=tuple(windows),
        window_policy=window,
        projected_fields=projected,
    )
