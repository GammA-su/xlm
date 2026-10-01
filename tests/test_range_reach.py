"""Range-v1 reach audit, block compressed-byte semantics and calibration versions (offline)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from xlm.data.acquisition import range_reach as reach
from xlm.data.acquisition import transport_policy as tp
from xlm.data.acquisition.sampling import (
    ColumnChunkSpec,
    FileLayout,
    RowGroupSpec,
    SamplingRequest,
    discover_layout_local,
    plan_sample_blocks,
)

PIN = {
    "source_id": "authored",
    "view_id": "default",
    "component_id": "authored",
    "provider": "huggingface",
    "repository": "authored/repo",
    "revision": "a" * 40,
    "adapter_id": "authored",
}
NAME = "data/part-0000.parquet"
SHA = "4" * 64


def group(index: int, *, usable: bool, text: int, meta: int = 10) -> RowGroupSpec:
    return RowGroupSpec(
        index=index,
        start_row=index * 1000,
        num_rows=1000,
        total_byte_size=3 * (text + meta),
        uncompressed_bytes=3 * (text + meta),
        num_columns=2,
        usable=usable,
        refusal=None
        if usable
        else f"Parquet row group {index} exceeds parser byte bound: compared total_byte_size=1",
        columns=(ColumnChunkSpec("text", text, 3 * text), ColumnChunkSpec("url", meta, 3 * meta)),
    )


def layout(flags: list[bool]) -> FileLayout:
    groups = tuple(group(i, usable=ok, text=100 if ok else 400) for i, ok in enumerate(flags))
    return FileLayout(name=NAME, num_rows=1000 * len(flags), groups=groups)


def audit(flags: list[bool], **changes: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "subject": PIN,
        "file_sha256": SHA,
        "file_bytes": 123_456,
        "max_parser_bytes": 32 * 1024 * 1024,
        "max_decompression_ratio": 15.0,
        "projected_fields": ("text",),
    }
    values.update(changes)
    return reach.reach_audit(layout(flags), **values)


def test_partially_refused_file_is_non_comparable() -> None:
    record = audit([True, True, False, True, False, False, True])
    assert record["status"] == "non_comparable"
    assert record["refused_groups"] == [2, 4, 5]
    assert record["refusal_reasons"] == {"parser_byte_bound": 3}
    assert (record["rows"], record["refused_rows"]) == (7000, 3000)
    # Only projected columns count: 4 x 100 reachable + 3 x 400 refused.
    assert record["projected_compressed_bytes"] == 1600
    assert record["refused_projected_compressed_bytes"] == 1200
    assert record["reachable_runs"] == [[0, 2], [3, 4], [6, 7]]
    assert record["longest_reachable_run_rows"] == 2000
    assert record["file"] == {"name": NAME, "sha256": SHA, "bytes": 123_456}
    assert record == audit([True, True, False, True, False, False, True])  # deterministic
    disposition = reach.disposition_of(record, "5" * 64)
    assert disposition["mode"] == "range_selected"
    assert disposition["status"] == "non_comparable"
    assert disposition["evidence_digest"] == record["digest"]
    assert disposition["contract"] == reach.REACH_CONTRACT
    assert "3 of 7 row groups" in disposition["summary"]


def test_all_refused_is_infeasible_and_all_reachable_cannot_be_disposed() -> None:
    assert audit([False, False])["status"] == "infeasible"
    reachable = audit([True, True, True])
    assert reachable["status"] == "comparable" and reachable["refused_groups"] == []
    with pytest.raises(tp.PolicyError, match="matched range benchmark is required"):
        reach.disposition_of(reachable, "5" * 64)


def test_reach_record_is_bound_to_view_file_and_digest() -> None:
    record = audit([True, False])
    assert reach.check_reach(record, PIN, [NAME]) is record
    with pytest.raises(tp.PolicyError, match="another source view"):
        reach.check_reach(record, {**PIN, "revision": "b" * 40}, [NAME])
    with pytest.raises(tp.PolicyError, match="did not measure"):
        reach.check_reach(record, PIN, ["data/part-0001.parquet"])
    with pytest.raises(tp.PolicyError, match="digest"):
        reach.check_reach({**record, "status": "comparable"}, PIN, [NAME])
    with pytest.raises(tp.PolicyError, match="verified file identity"):
        audit([True], file_sha256="not-a-sha")
    # Any bound input changes the identity.
    assert audit([True, False], file_sha256="6" * 64)["digest"] != record["digest"]
    assert audit([True, False], max_parser_bytes=1)["digest"] != record["digest"]


def _compressible_file(path: Path) -> Path:
    rows = [("abc " * 2000) + str(i) for i in range(400)]
    pq.write_table(pa.table({"text": rows}), path, row_group_size=100, compression="zstd")
    return path


def test_block_bytes_are_column_compressed_with_explicit_logical_size(tmp_path: Path) -> None:
    path = _compressible_file(tmp_path / "c.parquet")
    found = discover_layout_local(
        path, name=path.name, max_parser_bytes=32 * 1024 * 1024, max_decompression_ratio=1e9
    )
    footer = pq.ParquetFile(path).metadata
    result = plan_sample_blocks(
        {path.name: found},
        SamplingRequest(
            source_id="authored",
            view_id="default",
            revision="authored-rev",
            files=(path.name,),
            seed=7,
            target_records=200,
        ),
    )
    (block,) = result.blocks
    groups = range(block.group_start, block.group_stop_exclusive)
    compressed = sum(footer.row_group(g).column(0).total_compressed_size for g in groups)
    logical = sum(footer.row_group(g).total_byte_size for g in groups)
    assert compressed < logical  # a real compressed group, not a label
    assert block.compressed_bytes == compressed
    assert block.total_byte_size == logical == block.uncompressed_bytes
    report = result.to_report()
    assert report["sampling_plan_version"] == 2
    assert report["blocks"][0]["compressed_bytes"] == compressed
    assert report["blocks"][0]["total_byte_size"] == logical
    assert report["estimated_compressed_bytes"] == compressed


def _calibration(version: int | None) -> dict[str, Any]:
    rows: dict[str, Any] = {
        "mode": "rowgroup",
        "blocks": [{"num_rows": 100, "compressed_bytes": 10_000}],
    }
    if version is not None:
        rows["sampling_plan_version"] = version
    return rows


def test_layout_reads_historical_and_v2_rows_evidence() -> None:
    journal = {
        "source_validators": {"f.parquet": {"etag": '"e"', "length": 1_000_000}},
        "file_progress": {"selected_records.jsonl": {"bytes_downloaded": 6_000}},
    }
    perf = {
        "transferred_bytes": 5_000,
        "requests_made": 6,
        "telemetry": {
            "parquet_groups": 1,
            "projection_selected_bytes": 4_000,
            "coalesced_ranges": 2,
        },
    }
    measurement = {"records_sampled": 100, "canonical_bytes": 5_000}

    def found(version: int | None) -> tp.SourceLayout:
        return tp.layout_from_calibration(
            "s",
            _calibration(version),
            perf,
            journal,
            measurement,
            source_files=None,
            evidence_names={},
        )

    # Historical v1 (absent or 1) is read unchanged so earlier layouts reproduce.
    assert found(None) == found(1)
    assert found(None).group_bytes == found(2).group_bytes == 10_000
    assert found(None).rows_per_file == 10_000
    with pytest.raises(tp.PolicyError, match="unknown"):
        found(3)
