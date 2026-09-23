"""P26 batched accounting + streaming adaptation: offline fixtures only.

Structural assertions are deterministic; wall times are reported, never
threshold-gated.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import time
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from xlm.cli.data_cmd import app as data_app
from xlm.data.acquisition.disk import DiskCeilingExceededError, StorageCapacityManager
from xlm.data.acquisition.fetcher import BoundedFetcher
from xlm.data.acquisition.plan import AcquisitionLimits, AcquisitionPlan, PlanAuthorization
from xlm.data.acquisition.progress import ProgressJournal
from xlm.data.sources.transport import BudgetExhaustedError


def _manager(tmp_path: Path, **overrides: Any) -> StorageCapacityManager:
    journal = ProgressJournal(tmp_path / "j.progress.json", "p", "h")
    limits = {
        "max_transferred_bytes": 1024**2,
        "max_decompressed_bytes": 1024**2,
        "max_temp_disk_bytes": 1024**2,
        "max_output_disk_bytes": 1024**2,
    }
    limits.update(overrides)
    return StorageCapacityManager(journal=journal, **limits)


def test_commit_batch_exact_and_error_types(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    manager.commit_batch(temp_bytes=100, scanned_records=10, scanned_maximum=100)
    snapshot = manager.snapshot()
    assert snapshot["temp_disk_bytes"] == 100
    assert manager.journal.state.accounting.consumed["records_scanned"] == 10
    manager.commit_batch(temp_bytes=50, scanned_records=40, scanned_maximum=100)
    assert manager.snapshot()["temp_disk_bytes"] == 150
    with pytest.raises(BudgetExhaustedError, match="records_scanned"):
        manager.commit_batch(temp_bytes=0, scanned_records=60, scanned_maximum=100)
    # Overflow fills exactly to the ceiling like the per-record refusal point.
    assert manager.journal.state.accounting.consumed["records_scanned"] == 100
    with pytest.raises(DiskCeilingExceededError, match="temp"):
        manager.commit_batch(temp_bytes=1024**2, scanned_records=0)
    with pytest.raises(ValueError, match="negative"):
        manager.commit_batch(temp_bytes=-1)
    with pytest.raises(ValueError, match="maximum"):
        manager.commit_batch(scanned_records=1)


def test_commit_batch_deadline_and_empty(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    manager.bind_deadline(0.01)
    time.sleep(0.02)
    with pytest.raises(TimeoutError):
        manager.commit_batch()
    fresh = _manager(tmp_path / "other")
    fresh.commit_batch()
    assert fresh.snapshot()["temp_disk_bytes"] == 0


def _rows(n: int) -> list[bytes]:
    return [
        (
            f'{{"text":"lorem ipsum dolor sit amet {i:05d} '
            + hashlib.sha256(f"unique-{i}".encode()).hexdigest()
            + ' consectetur adipiscing elit"}'
            + "\n"
        ).encode()
        for i in range(n)
    ]


class _GzOpener:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    def open(self, request: Any, *, timeout: float) -> Any:
        payload = self.payload

        class Response(io.BytesIO):
            status = 200
            headers = {"Content-Length": str(len(payload)), "ETag": '"p26-v1"'}

        return Response(payload)


def _plan(n: int, plan_id: str, **changes: Any) -> AcquisitionPlan:
    limits = AcquisitionLimits(
        max_transferred_bytes=200 * 1024**2,
        max_decompressed_bytes=400 * 1024**2,
        max_temp_disk_bytes=1024**3,
        max_output_disk_bytes=1024**3,
        max_records=n + 100,
        max_requests=n + 100,
        max_retries=0,
        max_workers=1,
        overall_deadline_seconds=600,
    )
    values: dict[str, Any] = {
        "plan_id": plan_id,
        "source_id": "common_pile",
        "provider": "https",
        "repository": "http://127.0.0.1:9/unused",
        "revision": "base-rev",
        "mode": "selected_records",
        "selected_files": ["news/rows.jsonl.gz"],
        "row_ranges": {"news/rows.jsonl.gz": (0, n)},
        "output_artifact_id": "base",
        "limits": limits,
    }
    values.update(changes)
    plan = AcquisitionPlan(**values)
    return plan.model_copy(
        update={
            "authorization": PlanAuthorization(
                authorization_hash=plan.compute_behavioral_hash(),
                authorized_by="p26",
                authorized_at="t",
                is_pilot_approved=True,
            )
        }
    )


def _acquire(tmp_path: Path, n: int, payload: bytes, plan_id: str) -> tuple[bytes, dict[str, int]]:
    plan = _plan(n, plan_id)
    fetcher = BoundedFetcher(plan, tmp_path / "scratch", tmp_path / "output")
    fetcher.opener = _GzOpener(payload)  # type: ignore[assignment]
    state = fetcher.run()
    assert state.status == "COMPLETED"
    return (tmp_path / "output/selected_records.jsonl").read_bytes(), fetcher.journal.io_stats()


def test_batch_sizes_identical_bytes_and_journal(tmp_path: Path, monkeypatch: Any) -> None:
    import xlm.data.acquisition.selection as selection_mod

    rows = _rows(400)
    payload = gzip.compress(b"".join(rows))
    outputs: dict[int, bytes] = {}
    stats: dict[int, dict[str, int]] = {}
    for batch in (64, 256, 1024):
        monkeypatch.setattr(selection_mod, "ACCOUNTING_BATCH_RECORDS", batch)
        data, io_stats = _acquire(tmp_path / f"b{batch}", 400, payload, f"plan{batch}")
        outputs[batch] = data
        stats[batch] = io_stats
    assert outputs[64] == outputs[256] == outputs[1024]
    for batch in (64, 256, 1024):
        assert stats[batch]["journal_persisted_writes"] < 120, stats
    writes = [stats[b]["journal_persisted_writes"] for b in (64, 256, 1024)]
    print(f"persisted writes 64/256/1024 = {writes}")


def test_scanned_limit_exact_under_batching(tmp_path: Path) -> None:
    rows = _rows(400)
    payload = gzip.compress(b"".join(rows))
    plan = _plan(
        400,
        "tight",
        limits=AcquisitionLimits(
            max_transferred_bytes=200 * 1024**2,
            max_decompressed_bytes=400 * 1024**2,
            max_temp_disk_bytes=1024**3,
            max_output_disk_bytes=1024**3,
            max_records=500,
            max_requests=500,
            max_retries=0,
            max_workers=1,
            overall_deadline_seconds=600,
            max_scanned_records=300,
        ),
    )
    fetcher = BoundedFetcher(plan, tmp_path / "scratch", tmp_path / "output")
    fetcher.opener = _GzOpener(payload)  # type: ignore[assignment]
    with pytest.raises(BudgetExhaustedError, match="scanned"):
        fetcher.run()
    assert not (tmp_path / "output/selected_records.jsonl").exists()
    journal = ProgressJournal(
        tmp_path / "scratch/journals/tight.progress.json", "tight", plan.compute_behavioral_hash()
    )
    assert journal.state.status in ("FAILED", "INTERRUPTED")
    assert journal.state.accounting.consumed["records_scanned"] == 300
    # Retry with headroom succeeds deterministically and matches an
    # uninterrupted generous run byte-for-byte.
    retry_root, ref_root = tmp_path / "retry", tmp_path / "ref"
    for root, ident in ((retry_root, "retry"), (ref_root, "ref")):
        generous = _plan(400, ident)
        worker = BoundedFetcher(generous, root / "scratch", root / "output")
        worker.opener = _GzOpener(payload)  # type: ignore[assignment]
        worker.run()
    assert (retry_root / "output/selected_records.jsonl").read_bytes() == (
        ref_root / "output/selected_records.jsonl"
    ).read_bytes()


def _adapt_plan(tmp_path: Path, plan_id: str, n: int) -> Path:
    plan = _plan(n, plan_id)
    path = tmp_path / f"{plan_id}.json"
    path.write_text(plan.with_computed_hash().model_dump_json(), encoding="utf-8")
    return path


def _adapt_input(tmp_path: Path, name: str, rows: list[bytes], sel_hash: str) -> Path:
    lines = []
    for i, raw in enumerate(rows):
        record = json.loads(raw.decode())
        record["_xlm_acquisition"] = {
            "source_id": "common_pile",
            "repository": "http://127.0.0.1:9/unused",
            "revision": "base-rev",
            "source_file": "news/rows.jsonl.gz",
            "row_index": i,
            "selection_hash": sel_hash,
        }
        lines.append(json.dumps(record))
    path = tmp_path / name
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _adapt(plan_path: Path, selected: Path, out_dir: Path, *extra: str) -> Any:
    return CliRunner().invoke(
        data_app,
        [
            "adapt",
            "--plan",
            str(plan_path),
            "--adapter",
            "common_pile",
            "--input",
            str(selected),
            "--output-dir",
            str(out_dir),
            *extra,
        ],
    )


def test_adapt_batch_sizes_identical(tmp_path: Path) -> None:
    from xlm.data.acquisition.plan import load_acquisition_plan

    rows = _rows(600)
    plan_path = _adapt_plan(tmp_path, "adapt_eq", 600)
    sel_hash = load_acquisition_plan(plan_path).compute_behavioral_hash()
    selected = _adapt_input(tmp_path, "selected.jsonl", rows, sel_hash)
    outputs: dict[int, dict[str, bytes]] = {}
    for batch in (128, 512, 4096):
        out_dir = tmp_path / f"out{batch}"
        result = _adapt(plan_path, selected, out_dir, "--batch-records", str(batch))
        assert result.exit_code == 0, result.output
        outputs[batch] = {name: (out_dir / name).read_bytes() for name in ("documents.jsonl",)}
    assert outputs[128] == outputs[512] == outputs[4096]


def test_adapt_record_mode_batch_equivalence(tmp_path: Path) -> None:
    from xlm.data.acquisition.plan import load_acquisition_plan

    rows = _rows(300)
    plan_path = _adapt_plan(tmp_path, "adapt_rec", 300)
    sel_hash = load_acquisition_plan(plan_path).compute_behavioral_hash()
    selected = _adapt_input(tmp_path, "selected.jsonl", rows, sel_hash)
    outputs: dict[int, dict[str, bytes]] = {}
    for batch in (128, 1024):
        out_dir = tmp_path / f"rec{batch}"
        result = _adapt(
            plan_path,
            selected,
            out_dir,
            "--batch-records",
            str(batch),
            "--on-reject",
            "record",
        )
        assert result.exit_code == 0, result.output
        outputs[batch] = {
            name: (out_dir / name).read_bytes()
            for name in (
                "documents.jsonl",
                "adaptation_rejections.jsonl",
                "adaptation_summary.json",
            )
        }
    assert outputs[128] == outputs[1024]


def test_adapt_exotic_framing_and_malformed(tmp_path: Path) -> None:
    from xlm.data.acquisition.plan import load_acquisition_plan

    plan_path = _adapt_plan(tmp_path, "adapt_ex", 10)
    sel_hash = load_acquisition_plan(plan_path).compute_behavioral_hash()

    def line(record: dict[str, object], index: int) -> str:
        record["_xlm_acquisition"] = {
            "source_id": "common_pile",
            "repository": "http://127.0.0.1:9/unused",
            "revision": "base-rev",
            "source_file": "news/rows.jsonl.gz",
            "row_index": index,
            "selection_hash": sel_hash,
        }
        return json.dumps(record, ensure_ascii=False)

    exotic = line({"text": "a b"}, 0)
    selected = tmp_path / "selected.jsonl"
    selected.write_text(exotic + "\n", encoding="utf-8")
    out_dir = tmp_path / "out"
    result = _adapt(plan_path, selected, out_dir)
    assert result.exit_code == 0, result.output
    # Frame on raw newlines: the text itself holds a literal U+2028 character.
    docs = (out_dir / "documents.jsonl").read_bytes().split(b"\n")
    docs = [line for line in docs if line.strip()]
    assert len(docs) == 1 and json.loads(docs[0])["text"] == "a b"
    bad = tmp_path / "bad.jsonl"
    bad.write_text(exotic + "\n{broken\n", encoding="utf-8")
    result = _adapt(plan_path, bad, tmp_path / "out2")
    assert result.exit_code == 1
    assert "line 2" in result.output
    assert not (tmp_path / "out2" / "documents.jsonl").exists()


def test_adapt_adapter_config(tmp_path: Path) -> None:
    from xlm.data.acquisition.plan import load_acquisition_plan

    plan = _plan(2, "adapt_nem")
    plan = plan.model_copy(update={"source_id": "nemotron_cc21"})
    plan_path = tmp_path / "plan_nem.json"
    plan_path.write_text(plan.with_computed_hash().model_dump_json(), encoding="utf-8")
    sel_hash = load_acquisition_plan(plan_path).compute_behavioral_hash()
    record = {"text": "t", "quality_category": "High-Quality"}
    record["_xlm_acquisition"] = {
        "source_id": "nemotron_cc21",
        "repository": "http://127.0.0.1:9/unused",
        "revision": "base-rev",
        "source_file": "news/rows.jsonl.gz",
        "row_index": 0,
        "selection_hash": sel_hash,
    }
    selected = tmp_path / "selected.jsonl"
    selected.write_text(json.dumps(record) + "\n", encoding="utf-8")
    runner = CliRunner()
    missing = runner.invoke(
        data_app,
        [
            "adapt",
            "--plan",
            str(plan_path),
            "--adapter",
            "nemotron_organic",
            "--input",
            str(selected),
            "--output-dir",
            str(tmp_path / "o1"),
        ],
    )
    assert missing.exit_code == 1
    good = runner.invoke(
        data_app,
        [
            "adapt",
            "--plan",
            str(plan_path),
            "--adapter",
            "nemotron_organic",
            "--adapter-config",
            "High-Quality",
            "--input",
            str(selected),
            "--output-dir",
            str(tmp_path / "o2"),
        ],
    )
    assert good.exit_code == 0, good.output
    assert len((tmp_path / "o2" / "documents.jsonl").read_text(encoding="utf-8").splitlines()) == 1
    bad = runner.invoke(
        data_app,
        [
            "adapt",
            "--plan",
            str(plan_path),
            "--adapter",
            "nemotron_organic",
            "--adapter-config",
            "Nope",
            "--input",
            str(selected),
            "--output-dir",
            str(tmp_path / "o3"),
        ],
    )
    assert bad.exit_code == 1


def test_adapt_throughput_reported(tmp_path: Path) -> None:
    from xlm.data.acquisition.plan import load_acquisition_plan

    rows = _rows(2000)
    plan_path = _adapt_plan(tmp_path, "adapt_tp", 2000)
    sel_hash = load_acquisition_plan(plan_path).compute_behavioral_hash()
    selected = _adapt_input(tmp_path, "selected.jsonl", rows, sel_hash)
    out_dir = tmp_path / "out"
    started = time.monotonic()
    result = _adapt(plan_path, selected, out_dir)
    wall = time.monotonic() - started
    assert result.exit_code == 0, result.output
    assert "Adapt throughput:" in result.output and "/s" in result.output
    print(f"adapt 2k wall={wall:.2f}s")
@pytest.mark.performance
@pytest.mark.serial


def test_large_scale_jsonl_vs_ipc(tmp_path: Path) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq  # noqa: F401  (import parity check only)

    records = [{"text": f"record {i:06d} " + ("z" * 800), "n": i} for i in range(100000)]
    started = time.monotonic()
    lines = [json.dumps(record).encode() + b"\n" for record in records]
    json_serialize = time.monotonic() - started
    blob = b"".join(lines)
    path = tmp_path / "big.jsonl"
    started = time.monotonic()
    path.write_bytes(blob)
    json_write = time.monotonic() - started
    started = time.monotonic()
    table = pa.Table.from_pylist(records)
    arrow_build = time.monotonic() - started
    ipc_path = tmp_path / "big.arrow"
    started = time.monotonic()
    with pa.OSFile(str(ipc_path), "wb") as sink:
        with pa.ipc.new_file(sink, table.schema) as writer:
            writer.write_table(table)
    ipc_write = time.monotonic() - started
    print(
        f"100k jsonl serialize={json_serialize:.2f}s write={json_write:.3f}s "
        f"size={len(blob)} arrow_build={arrow_build:.2f}s "
        f"ipc_write={ipc_write:.3f}s size={ipc_path.stat().st_size}"
    )
    assert len(table) == 100000
