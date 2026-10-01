"""Offline scan-bounded Parquet window sampling/acquisition: authored fixtures only.

The large fixture mirrors the OBSERVED SYNTH physical shape (one ~155k-row
row group, 11 projected + 3 unprojected top-level columns) with small
authored values so the test stays bounded. Values, column sizes and the
three unprojected column names other than ``synthetic_reasoning`` are
authored, never real SYNTH data. No network: every byte is served from
local memory by an in-process opener.
"""

from __future__ import annotations

import hashlib
import io
import json
import urllib.parse
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from typer.testing import CliRunner

from xlm.cli.data_cmd import app as data_app
from xlm.data.acquisition.fetcher import BoundedFetcher
from xlm.data.acquisition.perf import compare_perf_docs
from xlm.data.acquisition.plan import (
    AcquisitionLimits,
    AcquisitionPlan,
    ParquetWindowDecode,
    PlanAuthorization,
    plan_requires_production_admission,
)
from xlm.data.acquisition.records import RecordLimitError
from xlm.data.acquisition.sampling import (
    ColumnChunkSpec,
    FileLayout,
    RowGroupSpec,
    SamplingRefusal,
    SamplingRequest,
    discover_layout_local,
    discover_layout_over_ranges,
    plan_sample_blocks,
)
from xlm.data.acquisition.verifier import AcquisitionVerifier
from xlm.data.adapters.columns import columns_for
from xlm.data.adapters.mix01_adapters import SynthExplanationsAdapter
from xlm.data.sources.transport import BudgetExhaustedError

BIG_ROWS = 155_000
SYNTH_PROJECTION = tuple(columns_for("synth_en"))
REVISION = "authored-rev"
#: Test policy: small buffer (many ranges) and a 1 MiB ratio exemption so the
#: authored multi-MB ratio bombs below are above the exemption.
WINDOW = ParquetWindowDecode(
    stream_buffer_bytes=64 * 1024,
    max_window_scan_rows=16_384,
    batch_rows=256,
    ratio_exempt_bytes=1024 * 1024,
)
#: The SYNTH calibration unit's policy in scripts/operator_calibrate_remaining.ps1.
DRIVER_WINDOW = ParquetWindowDecode(
    stream_buffer_bytes=4 * 1024 * 1024, max_window_scan_rows=16_384, batch_rows=256
)


def _write_bomb(table: pa.Table, path: Path, bomb: str) -> None:
    """Plain-encode ``bomb`` so its repetition shows up as a compression ratio."""
    dictionary = [name for name in table.column_names if name != bomb]
    pq.write_table(
        table, path, row_group_size=table.num_rows, compression="zstd", use_dictionary=dictionary
    )


def _hex(index: int, salt: str) -> str:
    return hashlib.sha256(f"{salt}{index}".encode()).hexdigest()


def _synth_shaped_table(rows: int, *, reasoning_repeat: int = 2) -> pa.Table:
    """Authored SYNTH-shaped columns; unprojected columns are incompressible."""
    return pa.table(
        {
            "synth_id": [f"authored_{_hex(i, 'i')[:20]}" for i in range(rows)],
            "language": ["de" if _hex(i, "l")[0] in "01" else "en" for i in range(rows)],
            "query": [f"Authored question {_hex(i, 'q')[:24]}?" for i in range(rows)],
            "query_seed_text": [f"Authored seed {_hex(i, 's')[:32]}." for i in range(rows)],
            "synthetic_answer": [f"Authored answer {_hex(i, 'n')[:24]}." for i in range(rows)],
            "seed_license": ["CC-By-SA (4.0)"] * rows,
            "exercise": ["memorization"] * rows,
            "model": ["authored-model"] * rows,
            "words": pa.array([int(_hex(i, "w")[:5], 16) for i in range(rows)], type=pa.int64()),
            "query_seed_url": [
                None if i % 50 == 0 else f"https://example.invalid/{_hex(i, 'u')[:16]}"
                for i in range(rows)
            ],
            "additional_seed_url": [None] * rows,
            "synthetic_reasoning": [_hex(i, "r") * reasoning_repeat for i in range(rows)],
            "authored_extra_a": [_hex(i, "a") for i in range(rows)],
            "authored_extra_b": pa.array(
                [int(_hex(i, "b")[:12], 16) for i in range(rows)], type=pa.int64()
            ),
        }
    )


@pytest.fixture(scope="module")
def big_file(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("big")
    path = root / "synth_big.parquet"
    pq.write_table(
        _synth_shaped_table(BIG_ROWS),
        path,
        row_group_size=BIG_ROWS,
        compression="zstd",
        data_page_size=64 * 1024,
    )
    return path


def _normal_file(path: Path, groups: int = 4, rows: int = 1000) -> Path:
    """UltraX-shaped: several small row groups, well inside every bound."""
    pq.write_table(
        _synth_shaped_table(groups * rows, reasoning_repeat=1),
        path,
        row_group_size=rows,
        compression="zstd",
    )
    return path


def _layout(path: Path, name: str | None = None) -> dict[str, FileLayout]:
    label = name or path.name
    return {
        label: discover_layout_local(
            path, name=label, max_parser_bytes=32 * 1024 * 1024, max_decompression_ratio=15.0
        )
    }


def _window_request(files: tuple[str, ...], **changes: Any) -> SamplingRequest:
    values: dict[str, Any] = {
        "source_id": "authored",
        "view_id": "default",
        "revision": REVISION,
        "files": files,
        "seed": 20260918,
        "mode": "window",
        "block_records": 1000,
        "target_records": 1000,
        "projected_fields": SYNTH_PROJECTION,
        "window": WINDOW,
    }
    values.update(changes)
    return SamplingRequest(**values)


class RecordingOpener:
    """Offline opener: serves local bytes, records every request, optional fault."""

    def __init__(self, payloads: dict[str, bytes], fail_after: int | None = None) -> None:
        self.payloads = payloads
        self.requests: list[tuple[str, int, int] | tuple[str, None, None]] = []
        self.fail_after = fail_after

    def open(self, request: Any, *, timeout: float) -> Any:
        name = urllib.parse.urlparse(request.full_url).path.rsplit("/", 1)[-1]
        payload = self.payloads[name]
        headers = dict(request.header_items())
        if self.fail_after is not None and len(self.requests) >= self.fail_after:
            raise RuntimeError("authored fault: connection torn down mid-selection")
        total = len(payload)
        if "Range" not in headers:
            self.requests.append((name, None, None))

            class Full(io.BytesIO):
                status = 200
                headers = {"Content-Length": str(total), "ETag": '"authored-v1"'}

            return Full(payload)
        start_s, end_s = headers["Range"].removeprefix("bytes=").split("-")
        start, end = int(start_s), int(end_s)
        self.requests.append((name, start, end))

        class Part(io.BytesIO):
            status = 206
            headers = {
                "Content-Range": f"bytes {start}-{end}/{total}",
                "Content-Length": str(end - start + 1),
                "ETag": '"authored-v1"',
            }

        return Part(payload[start : end + 1])


def _plan(
    files: list[str],
    ranges: dict[str, tuple[int, int]],
    *,
    window: ParquetWindowDecode | None = WINDOW,
    projection: tuple[str, ...] | None = SYNTH_PROJECTION,
    attempt: int = 1,
    source_id: str = "authored",
    **limit_changes: Any,
) -> AcquisitionPlan:
    limits = AcquisitionLimits(
        max_requests=100, max_retries=0, max_workers=1, overall_deadline_seconds=120
    )
    if limit_changes:
        limits = AcquisitionLimits.model_validate({**limits.model_dump(), **limit_changes})
    plan = AcquisitionPlan(
        plan_id=f"{source_id}_window_{attempt}_{'w' if window else 'n'}",
        source_id=source_id,
        provider="https",
        repository="http://127.0.0.1:9/unused",
        revision=REVISION,
        mode="selected_records",
        selected_files=files,
        row_ranges=ranges,
        output_artifact_id="authored_window",
        limits=limits,
        attempt=attempt,
        projected_fields=list(projection) if projection is not None else None,
        parquet_window=window,
    )
    return plan.model_copy(
        update={
            "authorization": PlanAuthorization(
                authorization_hash=plan.compute_behavioral_hash(),
                authorized_by="authored offline test",
                authorized_at="fixture",
                is_pilot_approved=True,
            )
        }
    )


def _run(plan: AcquisitionPlan, root: Path, opener: RecordingOpener) -> BoundedFetcher:
    fetcher = BoundedFetcher(plan, root / "scratch", root / "output")
    fetcher.opener = opener  # type: ignore[assignment]
    try:
        fetcher.run()
    finally:
        fetcher.close()
    return fetcher


def _chunk_spans(path: Path, names: set[str]) -> list[tuple[int, int]]:
    group = pq.ParquetFile(path).metadata.row_group(0)
    spans: list[tuple[int, int]] = []
    for index in range(group.num_columns):
        column = group.column(index)
        if column.path_in_schema in names:
            start = column.data_page_offset
            if column.dictionary_page_offset is not None:
                start = min(start, column.dictionary_page_offset)
            spans.append((start, start + column.total_compressed_size))
    return spans


def _sha_start(seed: int, file: str, group: int, modulus: int) -> int:
    """Independent recomputation of the documented window-v1 start construction."""
    text = "|".join(
        [str(seed), "authored", "default", REVISION, file, str(group), "window-v1", "start"]
    )
    return int(hashlib.sha256(text.encode("utf-8")).hexdigest(), 16) % modulus


# ---------------------------------------------------------------- identities

LEGACY_PINS = {
    # Computed at 60a14e4 (before window decode existed); must never move.
    "whole_file": (
        "ced2578688266f41d3acb6be26ab2d53157598ff877c50f45583869b356f700f",
        "3b75ba1be88a63ddbda43ab5a6d9a7cc000e843b9f828cc8d3a6bfb56a8e3b39",
    ),
    "projected": (
        "0e7c084295ee1737f3e026ec8a6240557a4066427288ecec50ae319428f5cc1a",
        "3f3c17a51daa01a5378644b8c0c86f7dd2957c11eba80c77718797036b654744",
    ),
    "coalesced_attempt3": (
        "05f7b6dfeeaf07e3da147988c75a6595de2ddc8cd7834b5e32fc02a799e19d83",
        "46b9c476b72caf4f227622d81e871bcbafa7b1db47b89e9322cfc1812b5123d1",
    ),
}


def _legacy_plans() -> dict[str, AcquisitionPlan]:
    base: dict[str, Any] = {
        "plan_id": "pin",
        "source_id": "authored",
        "provider": "https",
        "repository": "https://example.invalid/base",
        "revision": "a" * 40,
        "output_artifact_id": "pin_out",
    }
    return {
        "whole_file": AcquisitionPlan(**base, selected_files=["x.parquet"]),
        "projected": AcquisitionPlan(
            **base,
            mode="selected_records",
            selected_files=["x.parquet"],
            row_ranges={"x.parquet": (5, 1005)},
            projected_fields=["text", "id"],
        ),
        "coalesced_attempt3": AcquisitionPlan(
            **base,
            mode="selected_records",
            selected_files=["x.parquet", "y.parquet"],
            row_ranges={"x.parquet": (0, 100), "y.parquet": (7, 9)},
            range_coalesce_bytes=4096,
            attempt=3,
            limits=AcquisitionLimits(max_workers=4),
        ),
    }


def test_legacy_plan_hashes_are_pinned() -> None:
    for key, plan in _legacy_plans().items():
        assert (plan.compute_behavioral_hash(), plan.compute_selection_hash()) == LEGACY_PINS[key]
        assert "parquet_window" not in plan.model_dump(exclude_none=True)


def test_window_binds_behavior_not_selection_identity() -> None:
    ranges = {"a.parquet": (100, 1100)}
    windowed = _plan(["a.parquet"], ranges)
    legacy = _plan(["a.parquet"], ranges, window=None)
    assert windowed.compute_behavioral_hash() != legacy.compute_behavioral_hash()
    assert windowed.compute_selection_hash() == legacy.compute_selection_hash()
    wider = _plan(
        ["a.parquet"], ranges, window=WINDOW.model_copy(update={"max_window_scan_rows": 20_000})
    )
    assert wider.compute_behavioral_hash() != windowed.compute_behavioral_hash()


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"projection": None}, "explicit column projection"),
        ({"stream_buffer_bytes": 64 * 1024 * 1024}, "per-range byte bound"),
        ({"max_window_scan_rows": 200_000}, "cumulative scanned-record bound"),
        ({"ranges": {"a.parquet": (0, 20_000)}}, "window scan bound"),
        ({"ratio_exempt_bytes": 1024**3}, "ratio exemption exceeds"),
    ],
)
def test_window_plan_validation_refuses(changes: dict[str, Any], message: str) -> None:
    window = WINDOW
    if "stream_buffer_bytes" in changes:
        window = window.model_copy(update={"stream_buffer_bytes": changes["stream_buffer_bytes"]})
    if "ratio_exempt_bytes" in changes:
        window = window.model_copy(update={"ratio_exempt_bytes": changes["ratio_exempt_bytes"]})
    if "max_window_scan_rows" in changes:
        window = window.model_copy(update={"max_window_scan_rows": changes["max_window_scan_rows"]})
    with pytest.raises(ValueError, match=message):
        _plan(
            ["a.parquet"],
            changes.get("ranges", {"a.parquet": (0, 1000)}),
            window=window,
            projection=changes.get("projection", SYNTH_PROJECTION),
        )


def test_window_plan_rejects_coalescing_and_whole_file() -> None:
    with pytest.raises(ValueError, match="coalescing"):
        AcquisitionPlan(
            plan_id="p",
            source_id="authored",
            provider="https",
            repository="https://example.invalid/b",
            revision=REVISION,
            mode="selected_records",
            selected_files=["a.parquet"],
            row_ranges={"a.parquet": (0, 10)},
            output_artifact_id="o",
            projected_fields=["text"],
            range_coalesce_bytes=0,
            parquet_window=WINDOW,
        )
    with pytest.raises(ValueError, match="selected_records"):
        AcquisitionPlan(
            plan_id="p",
            source_id="authored",
            provider="https",
            repository="https://example.invalid/b",
            revision=REVISION,
            selected_files=["a.parquet"],
            output_artifact_id="o",
            parquet_window=WINDOW,
        )


def test_ratio_rule_separates_amplification_from_ratio() -> None:
    # Low-entropy ids: 18x ratio but only 3 MB total -> cannot amplify; exempt.
    ids = [("synth_id", 169_503, 3_067_974)]
    assert DRIVER_WINDOW.ratio_refusal(ids, 15.0) is None
    strict = DRIVER_WINDOW.model_copy(update={"ratio_exempt_bytes": 0})
    assert "synth_id" in (strict.ratio_refusal(ids, 15.0) or "")
    # Above the exemption the per-column ratio still binds.
    bomb = [("query", 1_000_000, 20_000_000)]
    assert "projected column 'query'" in (DRIVER_WINDOW.ratio_refusal(bomb, 15.0) or "")
    # Individually exempt columns cannot hide an aggregate bomb.
    spread = [(f"c{i}", 500_000, 10_000_000) for i in range(4)]
    assert "aggregate" in (DRIVER_WINDOW.ratio_refusal(spread, 15.0) or "")
    # Ordinary text at the observed SYNTH aggregate ratio (~1.69) passes.
    text = [("query_seed_text", 40_000_000, 67_600_000)]
    assert DRIVER_WINDOW.ratio_refusal(text, 15.0) is None


def test_production_gate_binds_window_physical_work() -> None:
    ranges = {"a.parquet": (0, 1000)}
    assert not plan_requires_production_admission(_plan(["a.parquet"], ranges))
    raised: list[dict[str, Any]] = [
        {"max_scanned_records": 200_000},
        {"max_decompressed_bytes": 1024**3},
        {"max_requests": 1000},
    ]
    for changes in raised:
        assert plan_requires_production_admission(_plan(["a.parquet"], ranges, **changes))
    big_buffer = WINDOW.model_copy(update={"stream_buffer_bytes": 16 * 1024 * 1024})
    assert plan_requires_production_admission(_plan(["a.parquet"], ranges, window=big_buffer))
    lax = WINDOW.model_copy(update={"ratio_exempt_bytes": 64 * 1024 * 1024})
    assert plan_requires_production_admission(_plan(["a.parquet"], ranges, window=lax))
    # Legacy plans keep the original three-limit gate (no silent semantic change).
    legacy = _plan(["a.parquet"], ranges, window=None, max_scanned_records=200_000)
    assert not plan_requires_production_admission(legacy)


# ------------------------------------------------------------------ sampling


def test_legacy_rowgroup_mode_still_refuses_large_group(big_file: Path) -> None:
    layouts = _layout(big_file)
    group = layouts[big_file.name].groups[0]
    assert group.num_rows == BIG_ROWS and group.total_byte_size > 32 * 1024 * 1024
    with pytest.raises(SamplingRefusal, match="total_byte_size"):
        plan_sample_blocks(
            layouts,
            SamplingRequest(
                source_id="authored",
                view_id="default",
                revision=REVISION,
                files=(big_file.name,),
                seed=1,
            ),
        )


def test_window_sampling_is_deterministic_and_documented(big_file: Path) -> None:
    layouts = _layout(big_file)
    first = plan_sample_blocks(layouts, _window_request((big_file.name,)))
    again = plan_sample_blocks(layouts, _window_request((big_file.name,)))
    assert first.to_report() == again.to_report()
    (chosen,) = first.windows
    assert chosen.num_rows == 1000 and chosen.row_group == 0
    assert chosen.start_domain_rows == 16_384
    expected_start = _sha_start(20260918, big_file.name, 0, 16_384 - 1000 + 1)
    assert chosen.start_in_group == expected_start
    assert first.row_ranges == {big_file.name: (expected_start, expected_start + 1000)}
    report = first.to_report()
    (evidence,) = report["windows"]
    batches = -(-(expected_start + 1000) // 256)
    assert evidence["expected_scan_rows"] == batches * 256 == report["expected_scan_rows"]
    assert evidence["selected_count"] == 1000
    assert [c["path"] for c in evidence["selected_columns"]] == list(SYNTH_PROJECTION)
    group_compressed = evidence["group_compressed_bytes"]
    assert 0 < evidence["selected_compressed_bytes"] < group_compressed
    assert evidence["unselected_compressed_bytes"] > 0
    assert report["window_policy"]["policy_version"] == 1
    worst = evidence["eligibility_worst_case"]
    assert worst["expected_scan_rows"] == 16_384 >= evidence["expected_scan_rows"]
    assert worst["estimated_transfer_upper_bytes"] >= evidence["estimated_transfer_upper_bytes"]
    assert worst["estimated_requests"] >= evidence["estimated_requests"]
    assert "clustered" in report["bias"] and "nonuniform" in report["bias"]
    assert any("decodes" in warning for warning in report["warnings"])
    assert any("restricted to the first 16384" in warning for warning in report["warnings"])


def test_window_start_identity_components(big_file: Path) -> None:
    layouts = _layout(big_file)
    base = plan_sample_blocks(layouts, _window_request((big_file.name,))).row_ranges
    seeds = {
        json.dumps(
            plan_sample_blocks(layouts, _window_request((big_file.name,), seed=s)).row_ranges
        )
        for s in range(8)
    }
    assert len(seeds) > 1
    revised = plan_sample_blocks(
        layouts, _window_request((big_file.name,), revision="authored-rev-2")
    ).row_ranges
    renamed = plan_sample_blocks(
        _layout(big_file, "synth_other.parquet"), _window_request(("synth_other.parquet",))
    ).row_ranges
    assert revised != base
    assert list(renamed.values()) != list(base.values())
    # A different seed changes the selection identity of the resulting plan.
    other = plan_sample_blocks(layouts, _window_request((big_file.name,), seed=1)).row_ranges
    assert other != base
    assert (
        _plan([big_file.name], dict(other)).compute_selection_hash()
        != _plan([big_file.name], dict(base)).compute_selection_hash()
    )


def test_legacy_reports_carry_no_window_keys(tmp_path: Path) -> None:
    path = _normal_file(tmp_path / "normal.parquet")
    report = plan_sample_blocks(
        _layout(path),
        SamplingRequest(
            source_id="authored",
            view_id="default",
            revision=REVISION,
            files=(path.name,),
            seed=3,
            target_records=1000,
        ),
    ).to_report()
    assert not {"window_policy", "windows", "projected_fields"} & set(report)
    # Block reports moved to v2 (true compressed bytes); window reports carry no
    # blocks and keep version 1.
    assert report["sampling_plan_version"] == 2
    windowed = plan_sample_blocks(_layout(path), _window_request((path.name,))).to_report()
    assert windowed["sampling_plan_version"] == 1 and windowed["blocks"] == []


def test_projection_drives_ratio_safety(tmp_path: Path) -> None:
    rows = 4000
    table = _synth_shaped_table(rows, reasoning_repeat=1).set_column(
        11, "synthetic_reasoning", pa.array(["z" * 4000] * rows)
    )
    path = tmp_path / "bomb.parquet"
    _write_bomb(table, path, "synthetic_reasoning")
    layouts = _layout(path)
    # Unprojected ratio bomb: never read, so it blocks neither sampling nor fetch.
    chosen = plan_sample_blocks(layouts, _window_request((path.name,)))
    assert chosen.windows
    _run(
        _plan([path.name], dict(chosen.row_ranges)),
        tmp_path / "fetch",
        RecordingOpener({path.name: path.read_bytes()}),
    )
    assert (tmp_path / "fetch/output/selected_records.jsonl").is_file()
    # Projected ratio bomb: refused before any payload is planned.
    with pytest.raises(SamplingRefusal, match="decompression ratio bound"):
        plan_sample_blocks(
            layouts,
            _window_request(
                (path.name,), projected_fields=(*SYNTH_PROJECTION, "synthetic_reasoning")
            ),
        )


def _synth_observed_layout() -> dict[str, FileLayout]:
    """Footer aggregates the USER observed for synth_002 (per-column split unknown).

    Worst case for the window: every compressed/uncompressed byte is assigned
    to projected columns (11 equal parts); the 3 unprojected columns get 1.
    """
    rows, compressed, uncompressed = 155_736, 473_875_189, 799_640_471
    names = [*SYNTH_PROJECTION, "synthetic_reasoning", "authored_x", "authored_y"]
    columns = tuple(
        ColumnChunkSpec(name, compressed // 11, uncompressed // 11)
        if name in SYNTH_PROJECTION
        else ColumnChunkSpec(name, 1, 1)
        for name in names
    )
    group = RowGroupSpec(
        index=0,
        start_row=0,
        num_rows=rows,
        total_byte_size=uncompressed,
        uncompressed_bytes=uncompressed,
        num_columns=14,
        usable=False,
        refusal="legacy whole-group parser refusal",
        columns=columns,
    )
    return {"synth_002.parquet": FileLayout("synth_002.parquet", rows, (group,))}


def test_synth_observed_shape_fits_pilot_bounds_in_worst_case() -> None:
    result = plan_sample_blocks(
        _synth_observed_layout(), _window_request(("synth_002.parquet",), window=DRIVER_WINDOW)
    )
    (chosen,) = result.windows
    report = result.to_report()
    assert chosen.expected_scan_rows <= 16_384 < 100_000
    assert report["estimated_transfer_upper_bytes"] <= 256 * 1024 * 1024
    assert report["estimated_requests"] <= 100
    assert chosen.estimated_scan_uncompressed_bytes <= 512 * 1024 * 1024
    # Any admissible start stays inside pilot bounds, not only the sampled one.
    assert chosen.domain_scan_rows == 16_384
    assert chosen.domain_estimated_transfer_upper_bytes <= 256 * 1024 * 1024
    assert chosen.domain_estimated_requests <= 100
    assert chosen.domain_estimated_scan_uncompressed_bytes <= 512 * 1024 * 1024
    # Whole-group decode of the same shape would scan 9.5x more rows.
    assert chosen.group_rows / chosen.expected_scan_rows > 9


def test_oversized_projection_refuses_clearly() -> None:
    layouts = _synth_observed_layout()
    wide = DRIVER_WINDOW.model_copy(update={"max_window_scan_rows": 100_000})
    with pytest.raises(SamplingRefusal, match="exceeds pilot bound"):
        plan_sample_blocks(layouts, _window_request(("synth_002.parquet",), window=wide))


def test_final_partial_window_and_target_truncation(tmp_path: Path) -> None:
    small = tmp_path / "small.parquet"
    pq.write_table(_synth_shaped_table(600), small, row_group_size=600)
    second = _normal_file(tmp_path / "normal.parquet")
    layouts = {**_layout(small), **_layout(second)}
    result = plan_sample_blocks(
        layouts, _window_request((small.name, second.name), target_records=2000)
    )
    sizes = {w.file: w.num_rows for w in result.windows}
    # One window per file: the 600-row group caps its window (final partial
    # window), and the target cannot be met, which is disclosed.
    assert sizes == {small.name: 600, second.name: 1000}
    assert result.planned_records == 1600
    assert all(w.stop_in_group <= w.group_rows for w in result.windows)
    (partial,) = [w for w in result.windows if w.file == small.name]
    assert (partial.start_in_group, partial.stop_in_group) == (0, 600)
    assert any("short of target 2000" in warning for warning in result.warnings)
    assert any("truncated to 600" in warning for warning in result.warnings)


def test_window_needs_projection_and_policy(big_file: Path) -> None:
    with pytest.raises(SamplingRefusal, match="window policy and column projection"):
        plan_sample_blocks(_layout(big_file), _window_request((big_file.name,), window=None))
    with pytest.raises(SamplingRefusal, match="projected fields not present"):
        plan_sample_blocks(
            _layout(big_file), _window_request((big_file.name,), projected_fields=("absent",))
        )


def test_remote_footer_discovery_captures_column_specs(big_file: Path) -> None:
    payload = big_file.read_bytes()

    def fetch(start: int, end: int) -> tuple[bytes, int]:
        return payload[start : end + 1], len(payload)

    remote = discover_layout_over_ranges(
        big_file.name, fetch, max_parser_bytes=32 * 1024 * 1024, max_decompression_ratio=15.0
    )
    assert remote == _layout(big_file)[big_file.name]
    assert len(remote.groups[0].columns) == 14


# --------------------------------------------------------------- acquisition


def test_window_fetch_streams_prefix_and_counts_true_scan(big_file: Path, tmp_path: Path) -> None:
    layouts = _layout(big_file)
    result = plan_sample_blocks(layouts, _window_request((big_file.name,)))
    (chosen,) = result.windows
    payload = big_file.read_bytes()
    opener = RecordingOpener({big_file.name: payload})
    plan = _plan([big_file.name], dict(result.row_ranges))
    fetcher = _run(plan, tmp_path / "w", opener)
    state = fetcher.journal.state
    assert state.status == "COMPLETED"
    # Scanned rows are the rows decoded from the group start, not the 1000 kept.
    assert state.accounting.consumed["records_scanned"] == chosen.expected_scan_rows
    assert chosen.expected_scan_rows > 1000
    assert fetcher.perf.scanned_records == chosen.expected_scan_rows
    assert fetcher.perf.retained_records == 1000
    # Every request is an exact bounded range; nothing near whole-file.
    assert opener.requests and all(start is not None for _, start, _ in opener.requests)
    assert all(end - start + 1 <= plan.limits.max_parser_bytes for _, start, end in opener.requests)  # type: ignore[operator]
    assert state.transferred_bytes < len(payload) // 4
    # No byte of an unprojected column chunk is ever requested as column data.
    # (Header magic and Arrow's speculative footer-tail read are metadata.)
    unprojected = _chunk_spans(
        big_file, {"synthetic_reasoning", "authored_extra_a", "authored_extra_b"}
    )
    data_requests = [
        request for request in opener.requests if request[1] != 0 and request[2] != len(payload) - 1
    ]
    assert len(data_requests) == len(opener.requests) - 2
    for _, start, end in data_requests:
        for low, high in unprojected:
            assert end < low or start >= high  # type: ignore[operator]
    # Projected chunks are read only as a prefix (early stop), never whole.
    projected_total = sum(high - low for low, high in _chunk_spans(big_file, set(SYNTH_PROJECTION)))
    assert state.transferred_bytes < projected_total
    lines = (tmp_path / "w/output/selected_records.jsonl").read_bytes().splitlines()
    assert len(lines) == 1000
    first = json.loads(lines[0])
    assert first["_xlm_acquisition"]["row_index"] == chosen.start_row
    assert first["_xlm_acquisition"]["row_in_group"] == chosen.start_in_group
    assert set(first) == {*SYNTH_PROJECTION, "_xlm_acquisition"}
    verifier = AcquisitionVerifier(plan, tmp_path / "w/output", journal=fetcher.journal)
    assert verifier.verify().files


def test_window_output_matches_legacy_projected_decode(tmp_path: Path) -> None:
    path = _normal_file(tmp_path / "normal.parquet")
    result = plan_sample_blocks(_layout(path), _window_request((path.name,)))
    ranges = dict(result.row_ranges)
    payloads = {path.name: path.read_bytes()}
    outputs = []
    for label, window in (("window", WINDOW), ("legacy", None)):
        _run(_plan([path.name], ranges, window=window), tmp_path / label, RecordingOpener(payloads))
        outputs.append((tmp_path / label / "output/selected_records.jsonl").read_bytes())
    assert outputs[0] == outputs[1]
    # Same canonical adapter semantics: identical records adapt identically.
    adapter = SynthExplanationsAdapter()
    adapted = 0
    for line in outputs[0].splitlines():
        record = json.loads(line)
        locator = record.pop("_xlm_acquisition")
        if record["language"] != "en":
            continue
        doc = adapter.adapt(
            record,
            source_file=locator["source_file"],
            source_row=locator["row_index"],
            source_revision=REVISION,
        )
        assert doc.text.startswith("Context: Authored seed")
        adapted += 1
    assert adapted > 0


def test_window_crossing_group_boundary_refuses(tmp_path: Path) -> None:
    path = _normal_file(tmp_path / "normal.parquet")
    opener = RecordingOpener({path.name: path.read_bytes()})
    with pytest.raises(RecordLimitError, match="crosses row group"):
        _run(_plan([path.name], {path.name: (900, 1100)}), tmp_path / "x", opener)


def test_scan_bound_refuses_before_column_bytes(big_file: Path, tmp_path: Path) -> None:
    opener = RecordingOpener({big_file.name: big_file.read_bytes()})
    # Hand-written range past the scan domain: refused, never silently scanned.
    with pytest.raises(RecordLimitError, match="window scan exceeds bound"):
        _run(_plan([big_file.name], {big_file.name: (60_000, 61_000)}), tmp_path / "x", opener)
    footer_start = len(big_file.read_bytes()) - 256 * 1024
    assert all(start == 0 or start >= footer_start for _, start, _ in opener.requests)  # type: ignore[operator]


def test_cumulative_scanned_record_bound(tmp_path: Path) -> None:
    files = []
    for name in ("one.parquet", "two.parquet"):
        pq.write_table(_synth_shaped_table(4000), tmp_path / name, row_group_size=4000)
        files.append(name)
    payloads = {name: (tmp_path / name).read_bytes() for name in files}
    window = WINDOW.model_copy(update={"max_window_scan_rows": 4000})
    plan = _plan(
        files,
        {name: (2900, 3000) for name in files},
        window=window,
        max_scanned_records=5000,
    )
    with pytest.raises(BudgetExhaustedError, match="records_scanned"):
        _run(plan, tmp_path / "x", RecordingOpener(payloads))


def test_transfer_bound_stops_window(big_file: Path, tmp_path: Path) -> None:
    result = plan_sample_blocks(_layout(big_file), _window_request((big_file.name,)))
    plan = _plan([big_file.name], dict(result.row_ranges), max_transferred_bytes=128 * 1024)
    with pytest.raises(BudgetExhaustedError):
        _run(plan, tmp_path / "x", RecordingOpener({big_file.name: big_file.read_bytes()}))
    assert not (tmp_path / "x/output/selected_records.jsonl").exists()


def test_projected_ratio_violation_refuses_at_fetch(tmp_path: Path) -> None:
    rows = 2000
    table = _synth_shaped_table(rows).set_column(2, "query", pa.array(["q" * 4000] * rows))
    path = tmp_path / "bomb.parquet"
    _write_bomb(table, path, "query")
    with pytest.raises(
        RecordLimitError, match="projected column 'query' exceeds decompression ratio bound"
    ):
        _run(
            _plan([path.name], {path.name: (0, 100)}),
            tmp_path / "x",
            RecordingOpener({path.name: path.read_bytes()}),
        )


def test_page_larger_than_range_bound_refuses(tmp_path: Path) -> None:
    rows = 2000
    table = _synth_shaped_table(rows).set_column(
        3, "query_seed_text", pa.array([_hex(i, "p") * 64 for i in range(rows)])
    )
    path = tmp_path / "bigpage.parquet"
    pq.write_table(
        table, path, row_group_size=rows, compression="none", data_page_size=4 * 1024 * 1024
    )
    opener = RecordingOpener({path.name: path.read_bytes()})
    plan = _plan(
        [path.name],
        {path.name: (0, 10)},
        max_parser_bytes=512 * 1024,
        max_record_bytes=64 * 1024,
    )
    with pytest.raises(ValueError, match="range exceeds parser bound"):
        _run(plan, tmp_path / "x", opener)
    assert all(end - start + 1 <= 512 * 1024 for _, start, end in opener.requests)  # type: ignore[operator]


def test_interrupted_window_retries_to_identical_bytes(big_file: Path, tmp_path: Path) -> None:
    result = plan_sample_blocks(_layout(big_file), _window_request((big_file.name,)))
    ranges = dict(result.row_ranges)
    payloads = {big_file.name: big_file.read_bytes()}
    clean = tmp_path / "clean"
    _run(_plan([big_file.name], ranges), clean, RecordingOpener(payloads))
    expected = (clean / "output/selected_records.jsonl").read_bytes()

    retry_root = tmp_path / "retry"
    plan = _plan([big_file.name], ranges)
    with pytest.raises(RuntimeError, match="authored fault"):
        _run(plan, retry_root, RecordingOpener(payloads, fail_after=6))
    spent = json.loads(next((retry_root / "scratch/journals").glob("*.progress.json")).read_text())
    resumed = _run(plan, retry_root, RecordingOpener(payloads))
    assert resumed.journal.state.status == "COMPLETED"
    assert (retry_root / "output/selected_records.jsonl").read_bytes() == expected
    # Spent budgets survive the retry (never reset by a fresh private attempt).
    assert resumed.journal.state.transferred_bytes > spent["transferred_bytes"] > 0
    # Rerunning a completed plan is a cache hit: no new requests, same bytes.
    again = RecordingOpener(payloads)
    _run(plan, retry_root, again)
    assert again.requests == []
    # Attempt renewal: new execution identity, byte-identical selection.
    renewed = _plan([big_file.name], ranges, attempt=2)
    assert renewed.compute_behavioral_hash() != plan.compute_behavioral_hash()
    _run(renewed, tmp_path / "renewed", RecordingOpener(payloads))
    assert (tmp_path / "renewed/output/selected_records.jsonl").read_bytes() == expected


def test_perf_sidecar_declares_calibration_window(tmp_path: Path) -> None:
    path = _normal_file(tmp_path / "normal.parquet")
    ranges = dict(plan_sample_blocks(_layout(path), _window_request((path.name,))).row_ranges)
    payloads = {path.name: path.read_bytes()}
    docs = []
    for label, window in (("window", WINDOW), ("legacy", None)):
        _run(_plan([path.name], ranges, window=window), tmp_path / label, RecordingOpener(payloads))
        (sidecar,) = (tmp_path / label / "scratch").rglob("*.perf.json")
        docs.append(json.loads(sidecar.read_text(encoding="utf-8")))
    assert docs[0]["measurement_class"] == "calibration_window"
    assert any("CALIBRATION-ONLY" in note for note in docs[0]["notes"])
    assert "parquet_window" not in docs[1] and "measurement_class" not in docs[1]
    comparison = compare_perf_docs(docs)
    assert not comparison["comparable"]
    assert any("parquet window" in refusal for refusal in comparison["refusals"])


# ----------------------------------------------------------------------- CLI


def _catalog(tmp_path: Path) -> Path:
    catalog = tmp_path / "catalog.json"
    catalog.write_text(
        json.dumps(
            {
                "catalog_id": "authored",
                "sources": [
                    {
                        "candidate_number": 1,
                        "source_id": "authored",
                        "provider": "https",
                        "repository": "http://127.0.0.1:9/unused",
                        "revision": REVISION,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return catalog


def test_cli_window_sample_then_plan(big_file: Path, tmp_path: Path) -> None:
    catalog = _catalog(tmp_path)
    runner = CliRunner()
    common = [
        "sample-blocks",
        "--source",
        "authored",
        "--catalog",
        str(catalog),
        "--revision",
        REVISION,
        "--files",
        big_file.name,
        "--local-dir",
        str(big_file.parent),
        "--seed",
        "20260918",
        "--target-records",
        "1000",
    ]
    legacy = runner.invoke(data_app, [*common, "--output", str(tmp_path / "legacy.json")])
    assert legacy.exit_code == 1 and "total_byte_size" in legacy.output
    rows = tmp_path / "rows.json"
    sampled = runner.invoke(
        data_app,
        [
            *common,
            "--mode",
            "window",
            "--adapter-spec",
            "synth_en",
            "--window-max-scan-rows",
            "16384",
            "--window-buffer-bytes",
            str(64 * 1024),
            "--output",
            str(rows),
        ],
    )
    assert sampled.exit_code == 0, sampled.output
    assert "--parquet-window-scan-rows 16384" in sampled.output
    evidence = json.loads((tmp_path / "rows.evidence.json").read_text(encoding="utf-8"))
    assert evidence["mode"] == "window" and evidence["windows"][0]["selected_count"] == 1000
    plan_path = tmp_path / "plan.json"
    planned = runner.invoke(
        data_app,
        [
            "plan",
            "--source",
            "authored",
            "--catalog",
            str(catalog),
            "--files",
            big_file.name,
            "--mode",
            "selected_records",
            "--row-ranges",
            str(rows),
            "--adapter-spec",
            "synth_en",
            "--seed",
            "20260918",
            "--parquet-window-scan-rows",
            "16384",
            "--parquet-window-buffer-bytes",
            str(64 * 1024),
            "--pilot-approved",
            "--output",
            str(plan_path),
        ],
    )
    assert planned.exit_code == 0, planned.output
    saved = json.loads(plan_path.read_text(encoding="utf-8"))
    assert saved["parquet_window"]["max_window_scan_rows"] == 16384
    assert saved["is_pilot"] is True
    refused = runner.invoke(
        data_app,
        [*common, "--mode", "window", "--output", str(tmp_path / "noproj.json")],
    )
    assert refused.exit_code == 1 and "exactly one of --adapter-spec" in refused.output


def _inventory_tool() -> Any:
    import importlib.util
    import sys

    path = Path(__file__).resolve().parents[1] / "scripts" / "mix01_inventory.py"
    spec = importlib.util.spec_from_file_location("mix01_inventory_window", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_window_calibration_chain_discloses_transfer_basis(
    big_file: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from xlm.data.acquisition.plan import load_acquisition_plan, save_acquisition_plan

    result = plan_sample_blocks(_layout(big_file), _window_request((big_file.name,)))
    (chosen,) = result.windows
    plan_path = tmp_path / "plan.json"
    save_acquisition_plan(
        _plan([big_file.name], dict(result.row_ranges), source_id="synth"), plan_path
    )
    plan = load_acquisition_plan(plan_path)
    fetcher = _run(plan, tmp_path, RecordingOpener({big_file.name: big_file.read_bytes()}))
    state = fetcher.journal.state
    raw_output = tmp_path / "output" / "selected_records.jsonl"
    assert AcquisitionVerifier(plan, tmp_path / "output", journal=fetcher.journal).verify()
    adapted = CliRunner().invoke(
        data_app,
        [
            "adapt",
            "--plan",
            str(plan_path),
            "--adapter",
            "synth_en",
            "--input",
            str(raw_output),
            "--output-dir",
            str(tmp_path / "canonical"),
            "--on-reject",
            "record",
        ],
    )
    assert adapted.exit_code == 0, adapted.output
    tool = _inventory_tool()
    measurement = tmp_path / "record_inputs.json"
    measure_argv = [
        "measure",
        "--plan",
        str(plan_path),
        "--scratch-dir",
        str(tmp_path / "scratch"),
        "--canonical-dir",
        str(tmp_path / "canonical"),
        "--source",
        "synth",
        "--view",
        "default",
        "--revision",
        REVISION,
        "--output",
        str(measurement),
    ]
    assert tool.main(measure_argv) == 0
    measured = json.loads(measurement.read_text(encoding="utf-8"))
    raw = state.transferred_bytes
    scanned = state.accounting.consumed["records_scanned"]
    assert scanned == chosen.expected_scan_rows > measured["records_sampled"] == 1000
    assert measured["transfer_basis"] == "calibration_window_retained_share"
    assert measured["raw_transferred_bytes"] == raw
    assert measured["records_scanned"] == scanned
    # Sizing input is the retained-row share, never the raw prefix transfer.
    assert measured["transferred_bytes"] == -(-raw * 1000 // scanned) < raw
    assert "CALIBRATION-ONLY" in measured["transfer_note"]
    calibration = tmp_path / "calibration.json"
    record_argv = [
        "record",
        "--calibration",
        str(calibration),
        "--source",
        "synth_en_explanations",
        "--measurement",
        str(measurement),
        "--adopt",
    ]
    assert tool.main(record_argv) == 0
    entry = json.loads(calibration.read_text(encoding="utf-8"))["sources"]["synth_en_explanations"]
    assert entry["transfer_basis"] == "calibration_window_retained_share"
    assert entry["raw_transferred_bytes"] == raw and entry["records_scanned"] == scanned
    assert entry["transferred_bytes"] == measured["transferred_bytes"]
    assert tool.main(record_argv) == 0  # identical re-record is adopted
    # A tampered measurement (raw transfer as sizing input) is refused.
    measurement.write_text(json.dumps({**measured, "transferred_bytes": raw}), encoding="utf-8")
    capsys.readouterr()
    assert tool.main([*record_argv[:-1], "--replace"]) == 1
    assert "not the retained-row share" in capsys.readouterr().err
