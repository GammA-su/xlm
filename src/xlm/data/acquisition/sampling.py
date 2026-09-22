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

SAMPLING_PLAN_VERSION = 1

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
            )
        )
        base += rows
    return FileLayout(name=name, num_rows=num_rows, groups=tuple(groups))


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
            specs: list[tuple[int, int, int, int, int, tuple[tuple[int, int], ...]]] = []
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
                    )
                )
    except SamplingRefusal:
        raise
    except Exception as exc:
        raise SamplingRefusal(f"cannot discover Parquet layout for '{name}': {exc}") from exc
    groups: list[RowGroupSpec] = []
    base = 0
    for index, (rows, num_columns, total, compressed, uncompressed, pairs) in enumerate(specs):
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

    def to_report(self) -> dict[str, Any]:
        """Deterministic evidence document (no paths, timing, or network order)."""
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
    if request.mode not in ("rowgroup", "contiguous"):
        raise SamplingRefusal("sampling mode must be 'rowgroup' or 'contiguous'")
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
    """
    _validate_request(request, layouts)
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
