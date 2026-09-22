"""Offline dense row-group sampling: authored local/loopback Parquet only."""

from __future__ import annotations

import http.server
import io
import json
import threading
import urllib.parse
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from typer.testing import CliRunner

from xlm.cli.data_cmd import app as data_app
from xlm.data.acquisition.fetcher import BoundedFetcher
from xlm.data.acquisition.plan import AcquisitionLimits, AcquisitionPlan, PlanAuthorization
from xlm.data.acquisition.sampling import (
    FileLayout,
    RowGroupSpec,
    SamplingRefusal,
    SamplingRequest,
    _det_index,
    canonical_range_url,
    discover_layout_local,
    discover_layout_over_ranges,
    plan_sample_blocks,
)


def _write_groups(path: Path, prefix: str, counts: list[int]) -> None:
    rows: list[str] = []
    for group, count in enumerate(counts):
        for index in range(count):
            rows.append(f"{prefix}-g{group}-r{index:05d}-pad")
    pq.write_table(pa.table({"text": rows}), path, row_group_size=max(counts))


def _fixture_dir(tmp_path: Path) -> Path:
    root = tmp_path / "shards"
    root.mkdir()
    _write_groups(root / "shard_a.parquet", "a", [100, 100, 100, 100])
    _write_groups(root / "shard_b.parquet", "b", [500, 500])
    _write_groups(root / "shard_c.parquet", "c", [50, 50, 50])
    return root


def _layouts(root: Path, names: list[str]) -> dict[str, FileLayout]:
    return {
        name: discover_layout_local(
            root / name,
            name=name,
            max_parser_bytes=32 * 1024 * 1024,
            max_decompression_ratio=15.0,
        )
        for name in names
    }


def _request(files: list[str], **changes: Any) -> SamplingRequest:
    values: dict[str, Any] = {
        "source_id": "authored",
        "view_id": "default",
        "revision": "authored-rev",
        "files": tuple(files),
        "seed": 7,
        "target_records": 600,
        "max_overshoot_records": 1000,
    }
    values.update(changes)
    return SamplingRequest(**values)


def test_layout_discovery_reports_groups_and_rows(tmp_path: Path) -> None:
    root = _fixture_dir(tmp_path)
    layout = discover_layout_local(
        root / "shard_a.parquet",
        name="shard_a.parquet",
        max_parser_bytes=32 * 1024 * 1024,
        max_decompression_ratio=15.0,
    )
    assert layout.num_rows == 400
    assert [group.num_rows for group in layout.groups] == [100, 100, 100, 100]
    assert [group.start_row for group in layout.groups] == [0, 100, 200, 300]
    assert all(group.usable for group in layout.groups)


def test_det_index_stable() -> None:
    first = _det_index(7, ("a", "b"), 1000)
    assert _det_index(7, ("a", "b"), 1000) == first
    assert 0 <= first < 1000


def test_same_seed_is_deterministic(tmp_path: Path) -> None:
    root = _fixture_dir(tmp_path)
    names = ["shard_a.parquet", "shard_b.parquet", "shard_c.parquet"]
    layouts = _layouts(root, names)
    first = plan_sample_blocks(layouts, _request(names))
    second = plan_sample_blocks(layouts, _request(names))
    assert first.row_ranges == second.row_ranges
    assert first.to_report() == second.to_report()


def test_different_seed_changes_block_choice(tmp_path: Path) -> None:
    root = _fixture_dir(tmp_path)
    names = ["shard_a.parquet", "shard_b.parquet", "shard_c.parquet"]
    layouts = _layouts(root, names)
    seen = {
        json.dumps(
            plan_sample_blocks(layouts, _request(names, seed=seed)).row_ranges, sort_keys=True
        )
        for seed in range(12)
    }
    assert len(seen) > 1


def test_no_always_group_zero_bias(tmp_path: Path) -> None:
    root = _fixture_dir(tmp_path)
    names = ["shard_a.parquet"]
    layouts = _layouts(root, names)
    starts = {
        plan_sample_blocks(layouts, _request(names, seed=seed, target_records=100))
        .blocks[0]
        .group_start
        for seed in range(12)
    }
    assert len(starts) > 1


def test_diversify_files_first(tmp_path: Path) -> None:
    root = _fixture_dir(tmp_path)
    names = ["shard_a.parquet", "shard_b.parquet", "shard_c.parquet"]
    layouts = _layouts(root, names)
    result = plan_sample_blocks(layouts, _request(names))
    assert set(result.selected_files) == set(names)
    assert len(result.blocks) == 3
    assert all(block.group_stop_exclusive - block.group_start == 1 for block in result.blocks)
    assert result.planned_records == 100 + 500 + 50


def test_rowgroup_alignment(tmp_path: Path) -> None:
    root = _fixture_dir(tmp_path)
    names = ["shard_a.parquet", "shard_b.parquet"]
    layouts = _layouts(root, names)
    result = plan_sample_blocks(layouts, _request(names, target_records=1000))
    for block in result.blocks:
        layout = layouts[block.file]
        starts = {group.start_row for group in layout.groups}
        ends = {group.start_row + group.num_rows for group in layout.groups}
        assert block.start_row in starts
        assert block.stop_row in ends
        start, stop = result.row_ranges[block.file]
        assert (start, stop) == (block.start_row, block.stop_row)


def test_contiguous_mode_covers_block_records(tmp_path: Path) -> None:
    root = _fixture_dir(tmp_path)
    names = ["shard_a.parquet"]
    layouts = _layouts(root, names)
    # Seeded start varies; scan a fixed seed range for a start with room ahead.
    result = None
    for seed in range(50):
        candidate = plan_sample_blocks(
            layouts,
            _request(names, mode="contiguous", block_records=250, target_records=300, seed=seed),
        )
        if candidate.blocks[0].num_rows >= 250:
            result = candidate
            break
    assert result is not None
    (block,) = result.blocks
    assert block.num_rows >= 250
    assert block.num_rows == 300
    assert block.group_start == block.group_stop_exclusive - 3


def test_max_blocks_per_file_caps_depth(tmp_path: Path) -> None:
    root = _fixture_dir(tmp_path)
    names = ["shard_a.parquet"]
    layouts = _layouts(root, names)
    result = plan_sample_blocks(
        layouts,
        _request(names, target_records=10000, max_blocks_per_file=2, max_overshoot_records=10**9),
    )
    assert result.planned_records == 200
    assert all(b.group_stop_exclusive - b.group_start <= 2 for b in result.blocks)


def test_max_records_hard_cap_refuses_first_block(tmp_path: Path) -> None:
    root = _fixture_dir(tmp_path)
    names = ["shard_c.parquet"]
    layouts = _layouts(root, names)
    with pytest.raises(SamplingRefusal, match="max records"):
        plan_sample_blocks(layouts, _request(names, max_records=10))


def test_max_bytes_hard_cap_refuses_first_block(tmp_path: Path) -> None:
    root = _fixture_dir(tmp_path)
    names = ["shard_c.parquet"]
    layouts = _layouts(root, names)
    with pytest.raises(SamplingRefusal, match="uncompressed bytes"):
        plan_sample_blocks(layouts, _request(names, max_uncompressed_bytes=10))


def test_oversized_group_refusal_numeric_style(tmp_path: Path) -> None:
    root = _fixture_dir(tmp_path)
    names = ["shard_a.parquet"]
    tiny = {
        name: discover_layout_local(
            root / name, name=name, max_parser_bytes=500, max_decompression_ratio=15.0
        )
        for name in names
    }
    with pytest.raises(SamplingRefusal, match="total_byte_size.*max_parser_bytes"):
        plan_sample_blocks(layouts=tiny, request=_request(names))


def test_target_overshoot_documented() -> None:
    groups = (
        RowGroupSpec(
            index=0,
            start_row=0,
            num_rows=1000,
            total_byte_size=1000,
            uncompressed_bytes=1000,
            num_columns=1,
            usable=True,
        ),
    )
    layouts = {"big.parquet": FileLayout(name="big.parquet", num_rows=1000, groups=groups)}
    result = plan_sample_blocks(layouts, _request(["big.parquet"], target_records=950))
    assert result.planned_records == 1000
    assert result.overshoot_records == 50
    assert any("overshoot" in warning for warning in result.warnings)


def test_overshoot_bound_gates_second_block() -> None:
    def group(index: int) -> RowGroupSpec:
        return RowGroupSpec(
            index=index,
            start_row=index * 1000,
            num_rows=1000,
            total_byte_size=1000,
            uncompressed_bytes=1000,
            num_columns=1,
            usable=True,
        )

    layouts = {
        "big.parquet": FileLayout(name="big.parquet", num_rows=2000, groups=(group(0), group(1)))
    }
    tight = plan_sample_blocks(
        layouts, _request(["big.parquet"], target_records=1200, max_overshoot_records=0)
    )
    assert tight.planned_records == 1000
    assert any("short" in warning for warning in tight.warnings)
    loose = plan_sample_blocks(
        layouts, _request(["big.parquet"], target_records=1200, max_overshoot_records=1000)
    )
    assert loose.planned_records == 2000


def test_tokens_require_factor_and_estimate() -> None:
    from xlm.data.acquisition.sampling import RowGroupSpec

    layouts = {
        "big.parquet": FileLayout(
            name="big.parquet",
            num_rows=100,
            groups=(
                RowGroupSpec(
                    index=0,
                    start_row=0,
                    num_rows=100,
                    total_byte_size=100,
                    uncompressed_bytes=100,
                    num_columns=1,
                    usable=True,
                ),
            ),
        )
    }
    result = plan_sample_blocks(
        layouts, _request(["big.parquet"], target_records=100, tokens_per_record=4.0)
    )
    assert result.estimated_tokens == 400.0
    assert result.to_report()["estimated_tokens"] == 400.0
    assert result.to_report()["estimated_transfer_bytes"] is None


def test_unknown_transfer_stays_unknown(tmp_path: Path) -> None:
    root = _fixture_dir(tmp_path)
    names = ["shard_a.parquet"]
    result = plan_sample_blocks(_layouts(root, names), _request(names, target_records=100))
    assert result.estimated_transfer_bytes is None
    report = result.to_report()
    assert report["estimated_transfer_bytes"] is None
    assert "never fabricated" in report["transfer_estimate_note"]


def test_revision_file_range_sensitivity(tmp_path: Path) -> None:
    root = _fixture_dir(tmp_path)
    names = ["shard_a.parquet", "shard_b.parquet"]
    layouts = _layouts(root, names)
    other_rev = plan_sample_blocks(layouts, _request(names, revision="other-rev"))
    assert other_rev.to_report()["revision"] == "other-rev"
    assert other_rev == plan_sample_blocks(layouts, _request(names, revision="other-rev"))
    smaller = plan_sample_blocks(layouts, _request(["shard_a.parquet"], target_records=100))
    assert set(smaller.row_ranges) == {"shard_a.parquet"}
    assert smaller.to_report()["requested_records"] == 100


def test_report_bias_and_no_paths(tmp_path: Path) -> None:
    root = _fixture_dir(tmp_path)
    names = ["shard_a.parquet"]
    result = plan_sample_blocks(_layouts(root, names), _request(names, target_records=100))
    report = result.to_report()
    assert "not uniform record sampling" in report["bias"]
    dumped = json.dumps(report)
    assert str(root) not in dumped
    assert report["sampling_plan_version"] == 1


class RangeFileHandler(http.server.BaseHTTPRequestHandler):
    payloads: dict[str, bytes] = {}

    def log_message(self, *_: Any) -> None:
        pass

    def do_GET(self) -> None:
        name = urllib.parse.urlparse(self.path).path.rsplit("/", 1)[-1]
        payload = self.payloads.get(name)
        if payload is None:
            body = b"missing"
            self.send_response(404)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        etag = f'"{name}-v1"'
        range_header = self.headers.get("Range")
        if range_header:
            start_s, end_s = range_header.removeprefix("bytes=").split("-")
            start, end = int(start_s), int(end_s)
            body = payload[start : end + 1]
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{len(payload)}")
            self.send_header("ETag", etag)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(200)
        self.send_header("ETag", etag)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def _serve_range(payloads: dict[str, bytes]) -> tuple[Any, str]:
    RangeFileHandler.payloads = dict(payloads)
    service = http.server.ThreadingHTTPServer(("127.0.0.1", 0), RangeFileHandler)
    thread = threading.Thread(target=service.serve_forever, daemon=True)
    thread.start()
    return service, f"http://127.0.0.1:{service.server_port}"


def _catalog(path: Path, base: str, revision: str = "authored-rev") -> Path:
    catalog = path / "catalog.json"
    catalog.write_text(
        json.dumps(
            {
                "catalog_id": "authored",
                "sources": [
                    {
                        "candidate_number": 1,
                        "source_id": "authored",
                        "provider": "https",
                        "repository": base,
                        "revision": revision,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return catalog


def test_cli_sample_blocks_local_and_plan_acceptance(tmp_path: Path) -> None:
    root = _fixture_dir(tmp_path)
    catalog = _catalog(tmp_path, "http://127.0.0.1:9/unused")
    out = tmp_path / "ranges.json"
    runner = CliRunner()
    result = runner.invoke(
        data_app,
        [
            "sample-blocks",
            "--source",
            "authored",
            "--catalog",
            str(catalog),
            "--revision",
            "authored-rev",
            "--files",
            "shard_a.parquet,shard_b.parquet",
            "--local-dir",
            str(root),
            "--seed",
            "7",
            "--target-records",
            "600",
            "--max-overshoot-records",
            "1000",
            "--output",
            str(out),
        ],
    )
    assert result.exit_code == 0, result.output
    ranges = json.loads(out.read_text(encoding="utf-8"))
    assert set(ranges) == {"shard_a.parquet", "shard_b.parquet"}
    report_path = tmp_path / "ranges.evidence.json"
    assert report_path.is_file()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["seed"] == 7 and report["revision"] == "authored-rev"
    # Deterministic rerun is byte-identical.
    out2 = tmp_path / "ranges2.json"
    rerun = runner.invoke(
        data_app,
        [
            "sample-blocks",
            "--source",
            "authored",
            "--catalog",
            str(catalog),
            "--revision",
            "authored-rev",
            "--files",
            "shard_a.parquet,shard_b.parquet",
            "--local-dir",
            str(root),
            "--seed",
            "7",
            "--target-records",
            "600",
            "--max-overshoot-records",
            "1000",
            "--output",
            str(out2),
        ],
    )
    assert rerun.exit_code == 0, rerun.output
    assert out.read_bytes() == out2.read_bytes()
    assert str(root) not in out.read_text(encoding="utf-8")
    # Produced ranges are accepted by the existing plan command.
    plan_out = tmp_path / "plan.json"
    planned = runner.invoke(
        data_app,
        [
            "plan",
            "--source",
            "authored",
            "--catalog",
            str(catalog),
            "--files",
            "shard_a.parquet,shard_b.parquet",
            "--mode",
            "selected_records",
            "--row-ranges",
            str(out),
            "--seed",
            "7",
            "--max-records",
            "25000",
            "--output",
            str(plan_out),
        ],
    )
    assert planned.exit_code == 0, planned.output


def test_cli_sample_blocks_remote_matches_local(tmp_path: Path) -> None:
    root = _fixture_dir(tmp_path)
    payloads = {name: (root / name).read_bytes() for name in ("shard_a.parquet", "shard_c.parquet")}
    service, base = _serve_range(payloads)
    try:
        catalog = _catalog(tmp_path, base)
        runner = CliRunner()
        remote_out = tmp_path / "remote.json"
        remote = runner.invoke(
            data_app,
            [
                "sample-blocks",
                "--source",
                "authored",
                "--catalog",
                str(catalog),
                "--revision",
                "authored-rev",
                "--files",
                "shard_a.parquet,shard_c.parquet",
                "--seed",
                "3",
                "--target-records",
                "200",
                "--max-overshoot-records",
                "500",
                "--output",
                str(remote_out),
            ],
        )
        assert remote.exit_code == 0, remote.output
        local_out = tmp_path / "local.json"
        local = runner.invoke(
            data_app,
            [
                "sample-blocks",
                "--source",
                "authored",
                "--catalog",
                str(catalog),
                "--revision",
                "authored-rev",
                "--files",
                "shard_a.parquet,shard_c.parquet",
                "--local-dir",
                str(root),
                "--seed",
                "3",
                "--target-records",
                "200",
                "--max-overshoot-records",
                "500",
                "--output",
                str(local_out),
            ],
        )
        assert local.exit_code == 0, local.output
        assert json.loads(remote_out.read_text()) == json.loads(local_out.read_text())
    finally:
        service.shutdown()
        service.server_close()


def test_cli_sample_blocks_budget_refusal(tmp_path: Path) -> None:
    root = _fixture_dir(tmp_path)
    catalog = _catalog(tmp_path, "http://127.0.0.1:9/unused")
    runner = CliRunner()
    refused = runner.invoke(
        data_app,
        [
            "sample-blocks",
            "--source",
            "authored",
            "--catalog",
            str(catalog),
            "--revision",
            "authored-rev",
            "--files",
            "shard_a.parquet",
            "--local-dir",
            str(root),
            "--max-parser-bytes",
            "500",
            "--output",
            str(tmp_path / "refused.json"),
        ],
    )
    assert refused.exit_code == 1
    assert "total_byte_size" in refused.output
    assert not (tmp_path / "refused.json").exists()


def test_canonical_url_parity_with_fetcher() -> None:
    from xlm.data.acquisition.fetcher import BoundedFetcher

    limits = AcquisitionLimits(max_requests=10)
    plan = AcquisitionPlan(
        plan_id="parity",
        source_id="authored",
        provider="https",
        repository="http://127.0.0.1:9/base",
        revision="rev",
        mode="whole_file",
        selected_files=["a/b.parquet"],
        output_artifact_id="parity",
        limits=limits,
    )
    import tempfile

    with tempfile.TemporaryDirectory() as scratch, tempfile.TemporaryDirectory() as output:
        authed = plan.model_copy(
            update={
                "authorization": PlanAuthorization(
                    authorization_hash=plan.compute_behavioral_hash(),
                    authorized_by="t",
                    authorized_at="t",
                    is_pilot_approved=True,
                )
            }
        )
        fetcher = BoundedFetcher(authed, Path(scratch), Path(output), catalog_source_approved=True)
        assert fetcher._resolve_url("a/b.parquet") == canonical_range_url(
            "https", "http://127.0.0.1:9/base", "rev", "a/b.parquet"
        )
        fetcher.close()


class ParquetOpener:
    """Offline opener serving local Parquet bytes with Range support."""

    def __init__(self, payloads: dict[str, bytes]) -> None:
        self.payloads = payloads

    def open(self, request: Any, *, timeout: float) -> Any:
        name = urllib.parse.urlparse(request.full_url).path.rsplit("/", 1)[-1]
        payload = self.payloads[name]
        headers = dict(request.header_items())
        total = len(payload)
        if "Range" in headers:
            start_s, end_s = headers["Range"].removeprefix("bytes=").split("-")
            start, end = int(start_s), int(end_s)

            class R(io.BytesIO):
                status = 206
                headers = {
                    "Content-Range": f"bytes {start}-{end}/{total}",
                    "Content-Length": str(end - start + 1),
                    "ETag": '"plan-v1"',
                }

            return R(payload[start : end + 1])

        class F(io.BytesIO):
            status = 200
            headers = {"Content-Length": str(total), "ETag": '"plan-v1"'}

        return F(payload)


def _selected_plan(
    files: list[str], ranges: dict[str, tuple[int, int]], workers: int, attempt: int = 1
) -> AcquisitionPlan:
    limits = AcquisitionLimits(
        max_transferred_bytes=64 * 1024**2,
        max_decompressed_bytes=64 * 1024**2,
        max_temp_disk_bytes=64 * 1024**2,
        max_output_disk_bytes=64 * 1024**2,
        max_records=10000,
        max_requests=1000,
        max_retries=0,
        max_workers=workers,
        overall_deadline_seconds=120,
    )
    plan = AcquisitionPlan(
        plan_id=f"authored_sample_{workers}_{attempt}",
        source_id="authored",
        provider="https",
        repository="http://127.0.0.1:9/unused",
        revision="authored-rev",
        mode="selected_records",
        selected_files=files,
        row_ranges=ranges,
        output_artifact_id="authored_sample",
        limits=limits,
        attempt=attempt,
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


def test_sampled_ranges_acquire_deterministically(tmp_path: Path) -> None:
    root = _fixture_dir(tmp_path)
    names = ["shard_a.parquet", "shard_b.parquet"]
    result = plan_sample_blocks(_layouts(root, names), _request(names))
    ranges = {name: (start, stop) for name, (start, stop) in result.row_ranges.items()}
    payloads = {name: (root / name).read_bytes() for name in names}
    outputs: list[bytes] = []
    for workers, leaf in [(1, "one"), (2, "two")]:
        leaf_root = tmp_path / leaf
        plan = _selected_plan(names, ranges, workers)
        fetcher = BoundedFetcher(plan, leaf_root / "scratch", leaf_root / "output")
        fetcher.opener = ParquetOpener(payloads)  # type: ignore[assignment]
        state = fetcher.run()
        assert state.status == "COMPLETED"
        outputs.append((leaf_root / "output/selected_records.jsonl").read_bytes())
        fetcher.close()
    assert outputs[0] == outputs[1]
    assert len(outputs[0].splitlines()) == result.planned_records
    # Attempt renewal over the same sampled ranges stays byte-identical.
    retry_root = tmp_path / "retry"
    retry = _selected_plan(names, ranges, 2, attempt=14)
    retry_fetcher = BoundedFetcher(retry, retry_root / "scratch", retry_root / "output")
    retry_fetcher.opener = ParquetOpener(payloads)  # type: ignore[assignment]
    retry_fetcher.run()
    assert (retry_root / "output/selected_records.jsonl").read_bytes() == outputs[0]
    retry_fetcher.close()


def test_layout_discovery_over_ranges_matches_local(tmp_path: Path) -> None:
    root = _fixture_dir(tmp_path)
    payload = (root / "shard_b.parquet").read_bytes()

    def fetch(start: int, end: int) -> tuple[bytes, int]:
        return payload[start : end + 1], len(payload)

    over_ranges = discover_layout_over_ranges(
        "shard_b.parquet",
        fetch,
        max_parser_bytes=32 * 1024 * 1024,
        max_decompression_ratio=15.0,
    )
    local = discover_layout_local(
        root / "shard_b.parquet",
        name="shard_b.parquet",
        max_parser_bytes=32 * 1024 * 1024,
        max_decompression_ratio=15.0,
    )
    assert over_ranges == local
