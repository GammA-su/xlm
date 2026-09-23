"""P25 production ingestion: projection, coalescing, writer, gzip, benchmarks.

Offline loopback fixtures only. Structural assertions (bytes, counts) are
deterministic; wall times are reported, never threshold-gated.
"""

from __future__ import annotations

import gzip
import hashlib
import http.server
import io
import json
import threading
import time
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from xlm.data.acquisition.fetcher import BoundedFetcher
from xlm.data.acquisition.perf import compare_perf_docs, load_perf_doc
from xlm.data.acquisition.plan import AcquisitionLimits, AcquisitionPlan, PlanAuthorization
from xlm.data.acquisition.records import StreamingJsonlWriter
from xlm.data.acquisition.verifier import AcquisitionVerifier
from xlm.data.adapters.columns import columns_for, parse_adapter_spec


class CountingRangeHandler(http.server.BaseHTTPRequestHandler):
    """Loopback file server with Range support plus request/byte counters."""

    payloads: dict[str, bytes] = {}
    lock = threading.Lock()
    range_requests = 0
    plain_requests = 0
    bytes_served = 0

    def log_message(self, *_: Any) -> None:
        pass

    @classmethod
    def reset(cls, payloads: dict[str, bytes]) -> None:
        with cls.lock:
            cls.payloads = dict(payloads)
            cls.range_requests = 0
            cls.plain_requests = 0
            cls.bytes_served = 0

    def _send(self, status: int, headers: list[tuple[str, str]], body: bytes) -> None:
        self.send_response(status)
        for key, value in headers:
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass
        with type(self).lock:
            type(self).bytes_served += len(body)

    def do_GET(self) -> None:
        from urllib.parse import urlparse

        name = urlparse(self.path).path.rsplit("/", 1)[-1]
        payload = type(self).payloads.get(name)
        if payload is None:
            self._send(404, [], b"missing")
            return
        etag = f'"{name}-v1"'
        range_header = self.headers.get("Range")
        if range_header:
            with type(self).lock:
                type(self).range_requests += 1
            start_s, end_s = range_header.removeprefix("bytes=").split("-")
            start, end = int(start_s), int(end_s)
            body = payload[start : end + 1]
            self._send(
                206,
                [
                    ("Content-Range", f"bytes {start}-{end}/{len(payload)}"),
                    ("ETag", etag),
                ],
                body,
            )
            return
        with type(self).lock:
            type(self).plain_requests += 1
        self._send(200, [("ETag", etag)], payload)


def serve(payloads: dict[str, bytes]) -> tuple[Any, str]:
    CountingRangeHandler.reset(payloads)
    service = http.server.ThreadingHTTPServer(("127.0.0.1", 0), CountingRangeHandler)
    thread = threading.Thread(target=service.serve_forever, daemon=True)
    thread.start()
    return service, f"http://127.0.0.1:{service.server_port}"


def teardown(service: Any) -> None:
    service.shutdown()
    service.server_close()


def server_counts() -> tuple[int, int, int]:
    with CountingRangeHandler.lock:
        return (
            CountingRangeHandler.range_requests,
            CountingRangeHandler.plain_requests,
            CountingRangeHandler.bytes_served,
        )


def synth_like_table(n: int, reasoning_size: int) -> pa.Table:
    return pa.table(
        {
            "synth_id": [f"s{i}" for i in range(n)],
            "language": ["en"] * n,
            "query": [f"question {i}?" for i in range(n)],
            "query_seed_text": [f"context passage number {i}." for i in range(n)],
            "synthetic_answer": [f"answer {i}." for i in range(n)],
            "seed_license": ["CC-By-SA (4.0)"] * n,
            "exercise": ["memorization"] * n,
            "model": ["m"] * n,
            "words": list(range(n)),
            "query_seed_url": ["http://127.0.0.1:9/x"] * n,
            "additional_seed_url": ["http://127.0.0.1:9/y"] * n,
            "synthetic_reasoning": ["R" * reasoning_size for _ in range(n)],
        }
    )


def selection_plan(
    url: str,
    files: list[str],
    ranges: dict[str, tuple[int, int]],
    *,
    workers: int = 1,
    projected: list[str] | None = None,
    coalesce: int | None = None,
    plan_id: str = "authored_p25",
    output_artifact_id: str = "authored_p25",
    **limit_changes: Any,
) -> AcquisitionPlan:
    limits = AcquisitionLimits(
        max_transferred_bytes=256 * 1024**2,
        max_decompressed_bytes=256 * 1024**2,
        max_temp_disk_bytes=256 * 1024**2,
        max_output_disk_bytes=256 * 1024**2,
        max_records=1000,
        max_requests=10000,
        max_retries=0,
        max_workers=workers,
        overall_deadline_seconds=300,
    )
    if limit_changes:
        limits = limits.model_copy(update=limit_changes)
    plan = AcquisitionPlan(
        plan_id=plan_id,
        source_id="authored",
        provider="https",
        repository=url,
        revision="authored-fixture-v1",
        mode="selected_records",
        selected_files=files,
        row_ranges=ranges,
        output_artifact_id=output_artifact_id,
        limits=limits,
        projected_fields=projected,
        range_coalesce_bytes=coalesce,
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


def run_plan(tmp_path: Path, plan: AcquisitionPlan) -> tuple[BoundedFetcher, bytes]:
    fetcher = BoundedFetcher(plan, tmp_path / "scratch", tmp_path / "output")
    state = fetcher.run()
    assert state.status == "COMPLETED"
    return fetcher, (tmp_path / "output/selected_records.jsonl").read_bytes()


def _bou(local: Path, plan: AcquisitionPlan) -> tuple[BoundedFetcher, bytes]:
    return run_plan(local, plan)


# --------------------------------------------------------------------------
# B/C: contracts and identity
# --------------------------------------------------------------------------


def test_adapter_column_contracts() -> None:
    assert columns_for("synth_en") == columns_for("synth_en", None)
    assert "synthetic_reasoning" not in columns_for("synth_en")
    assert "constraints" not in columns_for("synth_en")
    assert "script" not in columns_for("synth_en")
    assert columns_for("nemotron_organic", "High-Quality") == (
        "text",
        "quality_category",
    )
    assert columns_for("wiki_rewrite") == ("text", "license", "metadata", "uuid")
    assert columns_for("ifm_general") == ("text", "token_count")
    assert columns_for("common_pile") == ("text",)
    with pytest.raises(ValueError, match="explicit config"):
        columns_for("nemotron_organic")
    with pytest.raises(ValueError, match="explicit config"):
        columns_for("essential_web")
    with pytest.raises(ValueError, match="no certified column contract"):
        columns_for("nope")
    assert parse_adapter_spec("synth_en") == ("synth_en", None)
    assert parse_adapter_spec("nemotron_organic:High-Quality") == (
        "nemotron_organic",
        "High-Quality",
    )
    with pytest.raises(ValueError, match="adapter spec"):
        parse_adapter_spec("bad:")
    with pytest.raises(ValueError, match="adapter spec"):
        parse_adapter_spec("")


def test_projection_identity_binding() -> None:
    base = selection_plan("https://huggingface.co/u", ["a.parquet"], {"a.parquet": (0, 1)})
    assert base.projected_fields is None
    assert base.range_coalesce_bytes is None
    legacy = base.model_copy(update={"projected_fields": None, "range_coalesce_bytes": None})
    assert legacy.compute_behavioral_hash() == base.compute_behavioral_hash()
    assert legacy.compute_selection_hash() == base.compute_selection_hash()
    projected = base.model_copy(update={"projected_fields": ["text"]})
    assert projected.compute_behavioral_hash() != base.compute_behavioral_hash()
    assert projected.compute_selection_hash() != base.compute_selection_hash()
    coalesced = base.model_copy(update={"range_coalesce_bytes": 65536})
    assert coalesced.compute_behavioral_hash() != base.compute_behavioral_hash()
    # Transport framing only: identical selected bytes, identical selection hash.
    assert coalesced.compute_selection_hash() == base.compute_selection_hash()


def test_whole_file_rejects_projection_options() -> None:
    from xlm.data.acquisition.plan import AcquisitionMode

    plan = selection_plan("https://huggingface.co/u", ["a.parquet"], {"a.parquet": (0, 1)})
    with pytest.raises(Exception, match="whole-file"):
        AcquisitionPlan(
            **{
                **plan.model_dump(),
                "mode": AcquisitionMode.WHOLE_FILE,
                "row_ranges": None,
                "projected_fields": ["text"],
            }
        )


# --------------------------------------------------------------------------
# Equivalence: legacy vs projected
# --------------------------------------------------------------------------


def test_projection_all_columns_matches_legacy(tmp_path: Path) -> None:
    table = synth_like_table(8, 200)
    sink = io.BytesIO()
    pq.write_table(table, sink, row_group_size=4, compression="NONE")
    payload = sink.getvalue()
    service, url = serve({"r.parquet": payload})
    try:
        legacy_plan = selection_plan(url, ["r.parquet"], {"r.parquet": (0, 6)})
        _, legacy_bytes = _bou(tmp_path / "legacy", legacy_plan)
        all_columns = selection_plan(
            url,
            ["r.parquet"],
            {"r.parquet": (0, 6)},
            projected=list(table.schema.names),
            coalesce=65536,
            plan_id="authored_proj",
            output_artifact_id="authored_proj",
        )
        _, projected_bytes = _bou(tmp_path / "projected", all_columns)
        # Identity-bound selection hashes differ by design; all other bytes match.
        assert all_columns.compute_selection_hash() != legacy_plan.compute_selection_hash()
        normalized_legacy = legacy_bytes.replace(
            legacy_plan.compute_selection_hash().encode(),
            b"<selection-hash>",
        )
        normalized_projected = projected_bytes.replace(
            all_columns.compute_selection_hash().encode(),
            b"<selection-hash>",
        )
        assert normalized_projected == normalized_legacy
    finally:
        teardown(service)


def test_narrow_projection_skips_giant_column(tmp_path: Path) -> None:
    table = synth_like_table(12, 20000)
    sink = io.BytesIO()
    pq.write_table(table, sink, row_group_size=6, compression="NONE")
    payload = sink.getvalue()
    service, url = serve({"r.parquet": payload})
    try:
        full_plan = selection_plan(url, ["r.parquet"], {"r.parquet": (0, 12)})
        full_root = tmp_path / "full"
        _, full_bytes = _bou(full_root, full_plan)
        _, _, full_served = server_counts()
        narrow = selection_plan(
            url,
            ["r.parquet"],
            {"r.parquet": (0, 12)},
            projected=list(columns_for("synth_en")),
            coalesce=65536,
            plan_id="authored_narrow",
            output_artifact_id="authored_narrow",
        )
        narrow_root = tmp_path / "narrow"
        CountingRangeHandler.reset({"r.parquet": payload})
        narrow_fetcher, narrow_bytes = _bou(narrow_root, narrow)
        _, _, narrow_served = server_counts()
        # Normalized equivalence: legacy rows restricted to projected columns.
        full_rows = [json.loads(line) for line in full_bytes.splitlines()]
        narrow_rows = [json.loads(line) for line in narrow_bytes.splitlines()]
        assert len(full_rows) == len(narrow_rows) == 12
        projected_set = set(columns_for("synth_en"))
        for full_record, narrow_record in zip(full_rows, narrow_rows, strict=True):
            assert set(narrow_record) - {"_xlm_acquisition"} <= projected_set
            for key in projected_set:
                assert narrow_record[key] == full_record[key]
            assert (
                narrow_record["_xlm_acquisition"]["row_index"]
                == full_record["_xlm_acquisition"]["row_index"]
            )
        # Structural win: strictly fewer served bytes (giant column untouched).
        assert narrow_served < full_served
        doc = load_perf_doc(narrow_root / "scratch/performance/authored_narrow.perf.json")
        telemetry = doc["telemetry"]
        assert telemetry["projection_skipped_bytes"] > 0
        assert telemetry["column_chunks_read"] == 2 * len(projected_set)
        assert doc["projected_fields"] == sorted(projected_set)
        assert doc["range_coalesce_bytes"] == 65536
        assert narrow_fetcher.journal.state.status == "COMPLETED"
    finally:
        teardown(service)


def test_coalescing_thresholds_identical_bytes(tmp_path: Path) -> None:
    table = synth_like_table(12, 5000)
    sink = io.BytesIO()
    pq.write_table(table, sink, row_group_size=6, compression="NONE")
    payload = sink.getvalue()
    service, url = serve({"r.parquet": payload})
    try:
        outputs: dict[int, bytes] = {}
        counts: dict[int, tuple[int, int, int]] = {}
        for threshold in (0, 65536, 1048576):
            CountingRangeHandler.reset({"r.parquet": payload})
            plan = selection_plan(
                url,
                ["r.parquet"],
                {"r.parquet": (0, 12)},
                projected=list(columns_for("synth_en")),
                coalesce=threshold,
                plan_id=f"authored_c{threshold}",
                output_artifact_id=f"authored_c{threshold}",
            )
            _, data = _bou(tmp_path / f"c{threshold}", plan)
            outputs[threshold] = data
            counts[threshold] = server_counts()
        assert outputs[0] == outputs[65536] == outputs[1048576]
        # Wider coalescing never needs more range requests (structural).
        assert counts[1048576][0] <= counts[0][0]
        print(f"coalesce ranges(0/64K/1M)={counts[0][0]}/{counts[65536][0]}/{counts[1048576][0]}")
    finally:
        teardown(service)


def test_unknown_projected_field_refused(tmp_path: Path) -> None:
    table = synth_like_table(4, 50)
    sink = io.BytesIO()
    pq.write_table(table, sink, row_group_size=2, compression="NONE")
    service, url = serve({"r.parquet": sink.getvalue()})
    try:
        plan = selection_plan(url, ["r.parquet"], {"r.parquet": (0, 2)}, projected=["text", "nope"])
        fetcher = BoundedFetcher(plan, tmp_path / "scratch", tmp_path / "output")
        with pytest.raises(ValueError, match="not present"):
            fetcher.run()
        assert not (tmp_path / "output/selected_records.jsonl").exists()
    finally:
        teardown(service)


def test_projected_workers_1v2_identical(tmp_path: Path) -> None:
    first = io.BytesIO()
    pq.write_table(synth_like_table(6, 300), first, row_group_size=3, compression="NONE")
    second = io.BytesIO()
    pq.write_table(synth_like_table(6, 300), second, row_group_size=3, compression="NONE")
    service, url = serve({"a.parquet": first.getvalue(), "b.parquet": second.getvalue()})
    try:
        outputs = []
        for workers, leaf in ((1, "one"), (2, "two")):
            plan = selection_plan(
                url,
                ["a.parquet", "b.parquet"],
                {"a.parquet": (0, 4), "b.parquet": (0, 4)},
                workers=workers,
                projected=list(columns_for("synth_en")),
                coalesce=65536,
                plan_id=f"authored_w{workers}",
                output_artifact_id=f"authored_w{workers}",
            )
            _, data = _bou(tmp_path / leaf, plan)
            outputs.append(data)
        assert outputs[0] == outputs[1]
    finally:
        teardown(service)


def test_compare_refuses_projection_mismatch() -> None:
    base = selection_plan("https://huggingface.co/u", ["a.parquet"], {"a.parquet": (0, 1)})
    other = selection_plan(
        "https://huggingface.co/u",
        ["a.parquet"],
        {"a.parquet": (0, 1)},
        projected=["text"],
        plan_id="other",
        output_artifact_id="other",
    )
    from xlm.data.acquisition.perf import PerfTelemetry

    one = PerfTelemetry().snapshot(
        plan=base,
        status="COMPLETED",
        wall_seconds=2.0,
        transferred_bytes=100,
        journal_decompressed_bytes=100,
        journal_requests=2,
        journal_cache_hits=0,
        journal_records=1,
    )
    two = PerfTelemetry().snapshot(
        plan=other,
        status="COMPLETED",
        wall_seconds=1.0,
        transferred_bytes=50,
        journal_decompressed_bytes=50,
        journal_requests=2,
        journal_cache_hits=0,
        journal_records=1,
    )
    result = compare_perf_docs([one, two])
    assert result["comparable"] is False
    assert any("projected" in reason for reason in result["refusals"])
    coalesced = PerfTelemetry().snapshot(
        plan=base.model_copy(update={"range_coalesce_bytes": 65536}),
        status="COMPLETED",
        wall_seconds=1.0,
        transferred_bytes=100,
        journal_decompressed_bytes=100,
        journal_requests=2,
        journal_cache_hits=0,
        journal_records=1,
    )
    allowed = compare_perf_docs([one, coalesced])
    assert allowed["comparable"] is True


def test_verifier_accepts_projected_output(tmp_path: Path) -> None:
    table = synth_like_table(4, 100)
    sink = io.BytesIO()
    pq.write_table(table, sink, row_group_size=2, compression="NONE")
    service, url = serve({"r.parquet": sink.getvalue()})
    try:
        plan = selection_plan(
            url, ["r.parquet"], {"r.parquet": (0, 4)}, projected=list(columns_for("synth_en"))
        )
        fetcher, _ = _bou(tmp_path, plan)
        receipt = AcquisitionVerifier(plan, tmp_path / "output", fetcher.journal).verify()
        assert receipt.files[0].record_count == 4
    finally:
        teardown(service)


# --------------------------------------------------------------------------
# K: JSONL.GZ streaming
# --------------------------------------------------------------------------


def _gz_payload(rows: list[bytes]) -> bytes:
    return gzip.compress(b"".join(rows))


def test_gz_selected_matches_plain_and_stops_early(tmp_path: Path) -> None:
    # Incompressible-ish payloads so the object spans several 64 KiB reads.
    # High-entropy payloads so the object spans several 64 KiB reads without
    # tripping the decompression-ratio guard.
    rows = [
        (
            f'{{"text":"authored {i:04d} '
            + "".join(hashlib.sha256(f"row-{i}-{j}".encode()).hexdigest() for j in range(32))
            + '"}\n'
        ).encode()
        for i in range(120)
    ]
    plain = b"".join(rows)
    payload = _gz_payload(rows)
    assert len(payload) > 65536
    service, url = serve({"rows.jsonl": plain, "rows.jsonl.gz": payload})
    try:
        plain_plan = selection_plan(url, ["rows.jsonl"], {"rows.jsonl": (2, 5)})
        _, plain_bytes = _bou(tmp_path / "plain", plain_plan)
        CountingRangeHandler.reset({"rows.jsonl": plain, "rows.jsonl.gz": payload})
        gz_plan = selection_plan(url, ["rows.jsonl.gz"], {"rows.jsonl.gz": (2, 5)})
        gz_fetcher, gz_bytes = _bou(tmp_path / "gz", gz_plan)
        plain_rows = [json.loads(line) for line in plain_bytes.splitlines()]
        gz_rows = [json.loads(line) for line in gz_bytes.splitlines()]
        assert [r["text"] for r in gz_rows] == [r["text"] for r in plain_rows]
        assert [r["_xlm_acquisition"]["row_index"] for r in gz_rows] == [2, 3, 4]
        assert gz_rows[0]["_xlm_acquisition"]["encoding"] == "gzip"
        # Early stop: the journal charges only consumed prefix bytes, far less
        # than the full object (server-side counters cannot observe the
        # client's early close on loopback, so journal transfer is the proof).
        assert gz_fetcher.journal.state.transferred_bytes < len(payload)
    finally:
        teardown(service)


def test_gz_chunk_sizes_identical_bytes(tmp_path: Path, monkeypatch: Any) -> None:
    import xlm.data.acquisition.selection as selection_mod

    rows = [
        (
            f'{{"text":"row {i:04d} '
            + "".join(hashlib.sha256(f"cell-{i}-{j}".encode()).hexdigest() for j in range(8))
            + '"}\n'
        ).encode()
        for i in range(400)
    ]
    payload = _gz_payload(rows)
    assert len(payload) > 65536
    service, url = serve({"rows.jsonl.gz": payload})
    try:
        walls: dict[int, float] = {}
        outputs: dict[int, bytes] = {}
        for chunk in (8192, 65536):
            monkeypatch.setattr(selection_mod, "GZ_SELECT_CHUNK_BYTES", chunk)
            CountingRangeHandler.reset({"rows.jsonl.gz": payload})
            plan = selection_plan(
                url,
                ["rows.jsonl.gz"],
                {"rows.jsonl.gz": (0, 400)},
                plan_id=f"authored_gz{chunk}",
                output_artifact_id=f"authored_gz{chunk}",
            )
            started = time.monotonic()
            _, data = _bou(tmp_path / f"gz{chunk}", plan)
            walls[chunk] = time.monotonic() - started
            outputs[chunk] = data
        assert outputs[8192] == outputs[65536]
        print(f"gz chunk walls 8K={walls[8192]:.3f}s 64K={walls[65536]:.3f}s")
    finally:
        teardown(service)


def test_gz_beyond_end_refused(tmp_path: Path) -> None:
    rows = [b'{"text":"a"}\n', b'{"text":"b"}\n']
    service, url = serve({"rows.jsonl.gz": _gz_payload(rows)})
    try:
        plan = selection_plan(url, ["rows.jsonl.gz"], {"rows.jsonl.gz": (0, 5)})
        fetcher = BoundedFetcher(plan, tmp_path / "scratch", tmp_path / "output")
        with pytest.raises(ValueError, match="extends beyond"):
            fetcher.run()
        assert not (tmp_path / "output/selected_records.jsonl").exists()
    finally:
        teardown(service)


# --------------------------------------------------------------------------
# G/M: writer unit + IPC benchmark decision
# --------------------------------------------------------------------------


def test_streaming_writer_equivalence(tmp_path: Path) -> None:
    lines = [json.dumps({"n": i, "text": "x" * 100}).encode() + b"\n" for i in range(300)]
    target = tmp_path / "out.jsonl"
    with StreamingJsonlWriter(target, buffer_bytes=65536) as writer:
        for line in lines:
            writer.write_line(line)
    assert target.read_bytes() == b"".join(lines)
    assert writer.count == 300 and writer.size == sum(map(len, lines))
    assert writer.digest.hexdigest() == hashlib.sha256(b"".join(lines)).hexdigest()
    assert writer.peak_rss_bytes >= 0 and writer.flush_seconds >= 0.0


def test_plan_cli_projection_flags(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from xlm.cli.data_cmd import app as data_app

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
                        "revision": "authored-fixture-v1",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    ranges = tmp_path / "ranges.json"
    ranges.write_text('{"r.parquet":[0,2]}', encoding="utf-8")
    runner = CliRunner()
    result = runner.invoke(
        data_app,
        [
            "plan",
            "--source",
            "authored",
            "--catalog",
            str(catalog),
            "--files",
            "r.parquet",
            "--mode",
            "selected_records",
            "--row-ranges",
            str(ranges),
            "--adapter-spec",
            "synth_en",
            "--coalesce-bytes",
            "65536",
            "--output",
            str(tmp_path / "plan.json"),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Projected:" in result.output and "Coalesce:" in result.output
    saved = json.loads((tmp_path / "plan.json").read_text(encoding="utf-8"))
    assert saved["projected_fields"] == list(columns_for("synth_en"))
    assert saved["range_coalesce_bytes"] == 65536
    explicit = runner.invoke(
        data_app,
        [
            "plan",
            "--source",
            "authored",
            "--catalog",
            str(catalog),
            "--files",
            "r.parquet",
            "--mode",
            "selected_records",
            "--row-ranges",
            str(ranges),
            "--project-fields",
            "text,query",
            "--output",
            str(tmp_path / "plan2.json"),
        ],
    )
    assert explicit.exit_code == 0, explicit.output
    assert json.loads((tmp_path / "plan2.json").read_text(encoding="utf-8"))[
        "projected_fields"
    ] == ["text", "query"]
    bad_spec = runner.invoke(
        data_app,
        [
            "plan",
            "--source",
            "authored",
            "--catalog",
            str(catalog),
            "--files",
            "r.parquet",
            "--mode",
            "selected_records",
            "--row-ranges",
            str(ranges),
            "--adapter-spec",
            "nemotron_organic",
            "--output",
            str(tmp_path / "plan3.json"),
        ],
    )
    assert bad_spec.exit_code == 1
    assert "explicit config" in bad_spec.output
    both = runner.invoke(
        data_app,
        [
            "plan",
            "--source",
            "authored",
            "--catalog",
            str(catalog),
            "--files",
            "r.parquet",
            "--mode",
            "selected_records",
            "--row-ranges",
            str(ranges),
            "--project-fields",
            "text",
            "--adapter-spec",
            "synth_en",
            "--output",
            str(tmp_path / "plan4.json"),
        ],
    )
    assert both.exit_code == 1


def test_ipc_vs_jsonl_benchmark(tmp_path: Path) -> None:
    records = [
        {"text": f"record {i:05d} " + ("y" * 900), "n": i, "source_file": "r.parquet"}
        for i in range(20000)
    ]
    started = time.monotonic()
    lines = [json.dumps(record).encode() + b"\n" for record in records]
    json_serialize = time.monotonic() - started
    blob = b"".join(lines)
    json_path = tmp_path / "records.jsonl"
    started = time.monotonic()
    json_path.write_bytes(blob)
    json_write = time.monotonic() - started
    started = time.monotonic()
    read_back = json_path.read_bytes().splitlines()
    json_read = time.monotonic() - started
    assert len(read_back) == 20000
    table = pa.Table.from_pylist(records)
    ipc_path = tmp_path / "records.arrow"
    started = time.monotonic()
    with pa.OSFile(str(ipc_path), "wb") as sink:
        with pa.ipc.new_file(sink, table.schema) as writer:
            writer.write_table(table)
    ipc_write = time.monotonic() - started
    started = time.monotonic()
    with pa.memory_map(str(ipc_path), "r") as source:
        loaded = pa.ipc.open_file(source).read_all().to_pylist()
    ipc_read = time.monotonic() - started
    assert len(loaded) == 20000
    assert [r["text"] for r in loaded] == [r["text"] for r in records]
    print(
        f"jsonl serialize={json_serialize:.3f}s write={json_write:.3f}s read={json_read:.3f}s "
        f"size={len(blob)} "
        f"ipc write={ipc_write:.3f}s read={ipc_read:.3f}s size={ipc_path.stat().st_size}"
    )


# --------------------------------------------------------------------------
# O: offline benchmark summary (relative, printed)
# --------------------------------------------------------------------------


def test_offline_benchmark_summary(tmp_path: Path) -> None:
    table = synth_like_table(24, 8000)
    sink = io.BytesIO()
    pq.write_table(table, sink, row_group_size=12, compression="NONE")
    payload = sink.getvalue()
    service, url = serve({"r.parquet": payload})
    try:
        CountingRangeHandler.reset({"r.parquet": payload})
        started = time.monotonic()
        _, legacy_bytes = _bou(
            tmp_path / "legacy",
            selection_plan(url, ["r.parquet"], {"r.parquet": (0, 24)}),
        )
        legacy_wall = time.monotonic() - started
        _, _, legacy_served = server_counts()
        CountingRangeHandler.reset({"r.parquet": payload})
        started = time.monotonic()
        _, narrow_bytes = _bou(
            tmp_path / "narrow",
            selection_plan(
                url,
                ["r.parquet"],
                {"r.parquet": (0, 24)},
                projected=list(columns_for("synth_en")),
                coalesce=65536,
                plan_id="authored_bench",
                output_artifact_id="authored_bench",
            ),
        )
        narrow_wall = time.monotonic() - started
        _, _, narrow_served = server_counts()
        legacy_rows = [json.loads(line) for line in legacy_bytes.splitlines()]
        narrow_rows = [json.loads(line) for line in narrow_bytes.splitlines()]
        assert len(legacy_rows) == len(narrow_rows) == 24
        print(
            f"legacy wall={legacy_wall:.3f}s served={legacy_served} "
            f"projected wall={narrow_wall:.3f}s served={narrow_served} "
            f"ratio={narrow_served / legacy_served:.3f}"
        )
        assert narrow_served < legacy_served
    finally:
        teardown(service)
