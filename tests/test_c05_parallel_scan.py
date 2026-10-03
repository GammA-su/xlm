"""Parallel C05 scan: ordering, failure, crash, interruption and resume (authored only)."""

from __future__ import annotations

import os
import shutil
import time
from collections import namedtuple
from dataclasses import replace
from pathlib import Path
from typing import Any

import psutil
import pytest

from c05_compact_support import mixed_corpus, setup_files
from test_c05_engine import KEY, PROMPT, TRUST, document, execute, small_resources
from xlm.data.evidence_v2 import canonical
from xlm.data.exclusion import runner, scanpool
from xlm.data.exclusion.artifacts import signed, verify_signed
from xlm.data.exclusion.policy import C05Error, ProductionPolicy

Usage = namedtuple("Usage", "total used free")


def corpus(files: int = 3, per_file: int = 40, seed: int = 31) -> list[list[Any]]:
    return mixed_corpus(seed, files=files, per_file=per_file)


def setup(tmp_path: Path, files: list[list[Any]], workers: int, **resources: Any) -> Any:
    return setup_files(tmp_path, files, resources=small_resources(workers=workers, **resources))


def membership_bytes(plan: Any) -> bytes:
    return (Path(plan.output_root) / plan.identity() / "membership.jsonl").read_bytes()


def state(plan: Any) -> dict[str, Any]:
    raw = (Path(plan.scratch_root) / plan.identity() / "state.json").read_bytes()
    return verify_signed(canonical.loads_bytes_strict(raw), TRUST)


def live_descendants() -> list[psutil.Process]:
    alive = []
    for child in psutil.Process().children(recursive=True):
        try:
            if child.is_running() and child.status() != psutil.STATUS_ZOMBIE:
                alive.append(child)
        except psutil.NoSuchProcess:
            continue
    return alive


def assert_no_workers(timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while live_descendants() and time.monotonic() < deadline:
        time.sleep(0.1)
    assert live_descendants() == []


class Reordering:
    """Test pool: last-in-first-out completion (first batch delivered last)."""

    def __init__(self, inner: Any, capacity: int) -> None:
        self.inner, self.capacity, self.workers = inner, capacity, capacity
        self.pending: list[Any] = []
        self.max_in_flight = 0
        self.pids: list[int] = []

    def start(self, check: Any) -> None:
        return None

    def submit(self, job: Any) -> None:
        self.pending.append(self.inner.handler(job))
        self.max_in_flight = max(self.max_in_flight, len(self.pending))
        assert len(self.pending) <= self.capacity

    def get(self, check: Any) -> Any:
        return self.pending.pop()

    def telemetry(self) -> dict[str, int]:
        return {"workers": 1, "busy": 0, "tasks": 0, "results": 0, "capacity": self.capacity}

    def close(self) -> None:
        self.inner.close()


def test_out_of_order_completion_and_delayed_first_batch_are_harmless(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    files = corpus(per_file=60)
    clean, index, receipt = setup(tmp_path / "clean", files, 1)
    execute(clean, index, receipt)
    pools: list[Reordering] = []
    original = runner.make_pool

    def reordering(workers: int, role: str, init: Any, inline: Any) -> Any:
        pool = original(1, role, init, inline)
        if role != "scan":
            return pool
        pools.append(Reordering(pool, capacity=6))
        return pools[-1]

    monkeypatch.setattr(runner, "make_pool", reordering)
    monkeypatch.setattr(runner, "BATCH_ROWS", 7)  # Many batches per file.
    shuffled, index2, receipt2 = setup(tmp_path / "shuffled", files, 1)
    execute(shuffled, index2, receipt2)
    assert membership_bytes(shuffled) == membership_bytes(clean)
    assert pools and pools[0].max_in_flight == 6  # Bounded by capacity, and saturated.


@pytest.mark.parametrize("rows,size", [(1, 1 << 22), (3, 1 << 22), (256, 2048), (512, 1)])
def test_batch_row_and_byte_bounds_never_change_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rows: int, size: int
) -> None:
    files = corpus(per_file=25)
    clean, index, receipt = setup(tmp_path / "clean", files, 1)
    execute(clean, index, receipt)
    monkeypatch.setattr(runner, "BATCH_ROWS", rows)
    monkeypatch.setattr(runner, "BATCH_BYTES", size)
    other, index2, receipt2 = setup(tmp_path / "bounded", files, 2)
    execute(other, index2, receipt2)
    assert membership_bytes(other) == membership_bytes(clean)


@pytest.mark.parametrize("workers", [1, 2])
def test_malformed_row_refuses_identically_and_publishes_nothing(
    tmp_path: Path, workers: int
) -> None:
    plan, index, receipt = setup(tmp_path, corpus(files=2, per_file=30), workers)
    path = Path(plan.data_root) / plan.files[1].path
    lines = path.read_bytes().splitlines(keepends=True)
    lines[17] = b"{" + b"x" * (len(lines[17]) - 3) + b"}\n"  # Same length, not JSON.
    path.write_bytes(b"".join(lines))
    files = tuple(
        f.model_copy(
            update={"documents_sha256": runner.file_sha(path), "file_bytes": path.stat().st_size}
        )
        if n == 1
        else f
        for n, f in enumerate(plan.files)
    )
    plan = plan.model_copy(update={"files": files})
    with pytest.raises(C05Error, match="record rejected: CanonicalError"):
        execute(plan, index, receipt)
    work = Path(plan.scratch_root) / plan.identity()
    assert (work / "facts" / "00000.unit").is_file()  # The earlier file stays committed.
    assert not (work / "facts" / "00001.unit").exists()
    assert not list((work / "facts").glob("*.staging"))
    assert not (Path(plan.output_root) / plan.identity()).exists()
    assert_no_workers()


@pytest.mark.parametrize("drift", ["sha", "rows_extra", "rows_missing", "canonical_bytes"])
def test_input_identity_drift_rolls_the_file_back(tmp_path: Path, drift: str) -> None:
    plan, index, receipt = setup(tmp_path, corpus(files=2, per_file=20), 2)
    path = Path(plan.data_root) / plan.files[1].path
    raw = path.read_bytes()
    if drift == "sha":
        lines = raw.splitlines(keepends=True)
        doc = canonical.loads_bytes_strict(lines[3])
        doc["source_row"] = doc["source_row"] + 1 if doc["source_row"] < 9 else 1
        replacement = canonical.canonical_bytes(doc) + b"\n"
        assert len(replacement) == len(lines[3])
        lines[3] = replacement
        path.write_bytes(b"".join(lines))
        expected = "identity mismatch"
    elif drift == "rows_extra":
        lines = raw.splitlines(keepends=True)
        path.write_bytes(raw + lines[0])
        files = list(plan.files)
        files[1] = files[1].model_copy(update={"file_bytes": path.stat().st_size})
        plan = plan.model_copy(update={"files": tuple(files)})
        expected = "exceed frozen file"
    elif drift == "rows_missing":
        lines = raw.splitlines(keepends=True)
        path.write_bytes(b"".join(lines[:-1]))
        files = list(plan.files)
        files[1] = files[1].model_copy(update={"file_bytes": path.stat().st_size})
        plan = plan.model_copy(update={"files": tuple(files)})
        expected = "identity mismatch"
    else:
        files = list(plan.files)
        files[1] = files[1].model_copy(update={"canonical_bytes": files[1].canonical_bytes - 1})
        plan = plan.model_copy(update={"files": tuple(files)})
        expected = "exceed frozen file"
    with pytest.raises(C05Error, match=expected):
        execute(plan, index, receipt)
    work = Path(plan.scratch_root) / plan.identity()
    assert not (work / "facts" / "00001.unit").exists()
    assert not list((work / "facts").glob("*.staging"))
    assert_no_workers()


@pytest.mark.parametrize("where", ["within", "across"])
def test_duplicate_document_ids_refuse(tmp_path: Path, where: str) -> None:
    a = document("same-id", "First authored text.")
    b = document("same-id", "Second authored text.")
    files = [[a, b]] if where == "within" else [[a], [b]]
    plan, index, receipt = setup(tmp_path, files, 2)
    with pytest.raises(C05Error, match="duplicate canonical document id"):
        execute(plan, index, receipt)
    assert not (Path(plan.output_root) / plan.identity()).exists()


def test_hard_worker_crash_terminates_the_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(runner, "BATCH_ROWS", 2)
    plan, index, receipt = setup(tmp_path, corpus(files=2, per_file=80), 2)
    killed: list[int] = []

    def kill_workers(event: str) -> None:
        if event == "row" and not killed:
            for child in psutil.Process().children(recursive=True):
                killed.append(child.pid)
                child.kill()

    with pytest.raises(C05Error, match="worker"):
        execute(plan, index, receipt, checkpoint=kill_workers)
    assert killed
    assert not (Path(plan.output_root) / plan.identity()).exists()
    assert_no_workers()
    result = execute(plan, index, receipt)  # Resumable; nothing trusted from the attempt.
    assert result["payload"]["documents"] == 160


def test_ctrl_c_terminates_children_and_resume_matches(tmp_path: Path) -> None:
    files = corpus(files=3, per_file=40)
    plan, index, receipt = setup(tmp_path / "interrupted", files, 2)

    def interrupt(event: str) -> None:
        if event == "row":
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        execute(plan, index, receipt, checkpoint=interrupt)
    assert_no_workers()
    first = state(plan)
    execute(plan, index, receipt)
    assert state(plan)["spent_attempted_records"] >= first["spent_attempted_records"]
    clean, index2, receipt2 = setup(tmp_path / "clean", files, 2)
    execute(clean, index2, receipt2)
    assert membership_bytes(plan) == membership_bytes(clean)


def test_ram_ceiling_while_workers_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plan, index, receipt = setup(tmp_path, corpus(files=2, per_file=40), 2)
    original = scanpool.ProcessTree.sample

    def sample(self: scanpool.ProcessTree) -> tuple[int, int]:
        rss, children = original(self)
        return (rss + plan.resources.ram_bytes if children else rss), children

    monkeypatch.setattr(scanpool.ProcessTree, "sample", sample)
    with pytest.raises(C05Error, match="RAM ceiling"):
        execute(plan, index, receipt)
    assert_no_workers()


def test_disk_and_stage_deadline_refusals_with_workers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan, index, receipt = setup(tmp_path / "disk", corpus(files=3, per_file=20), 2)
    real = shutil.disk_usage
    exhausted = {"on": False}

    def usage(path: Any) -> Any:
        result = real(path)
        return Usage(result.total, result.used, 0) if exhausted["on"] else result

    def exhaust(event: str) -> None:
        if event == "file_committed":
            exhausted["on"] = True

    monkeypatch.setattr(shutil, "disk_usage", usage)
    with pytest.raises(C05Error, match="reserve"):
        execute(plan, index, receipt, checkpoint=exhaust)
    assert_no_workers()
    monkeypatch.setattr(shutil, "disk_usage", real)

    plan, index, receipt = setup(tmp_path / "stage", corpus(files=2, per_file=20), 2)

    def stop(event: str) -> None:
        if event == "file_committed":
            raise RuntimeError("authored stop")

    with pytest.raises(RuntimeError):
        execute(plan, index, receipt, checkpoint=stop)
    work = Path(plan.scratch_root) / plan.identity()
    body = state(plan)
    body["stage_started"] -= plan.resources.stage_seconds + 1
    canonical.write_canonical_json(work / "state.json", signed(body, "fixture", KEY))
    with pytest.raises(C05Error, match="stage deadline"):
        execute(plan, index, receipt)
    assert_no_workers()


@pytest.mark.parametrize("event", ["row", "file_committed", "grouped", "before_publication"])
def test_resume_after_interruption_skips_committed_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, event: str
) -> None:
    from xlm.data.exclusion import scanprep

    files = corpus(files=4, per_file=15)
    plan, index, receipt = setup(tmp_path / "resumed", files, 1)
    seen = {"commits": 0}

    def crash(point: str) -> None:
        if point == "file_committed":
            seen["commits"] += 1
        if point != event:
            return
        # "row": first row of the third file; "file_committed": right after the second.
        if event in ("row", "file_committed") and seen["commits"] != 2:
            return
        raise RuntimeError("authored interruption")

    with pytest.raises(RuntimeError):
        execute(plan, index, receipt, checkpoint=crash)
    work = Path(plan.scratch_root) / plan.identity()
    committed = len(list((work / "facts").glob("*.unit")))
    prepared = {"rows": 0}
    original = scanprep.Preparer.batch

    def counting(self: Any, *args: Any) -> Any:
        batch = original(self, *args)
        prepared["rows"] += batch.count
        return batch

    monkeypatch.setattr(scanprep.Preparer, "batch", counting)
    before = state(plan)
    execute(plan, index, receipt)
    expected = sum(len(f) for f in files[committed:])
    assert prepared["rows"] == expected  # Committed files are verified, never re-prepared.
    after = state(plan)
    for name in ("spent_bytes_read", "spent_attempted_records"):
        assert after[name] >= before.get(name, 0)
    clean, index2, receipt2 = setup(tmp_path / "clean", files, 1)
    execute(clean, index2, receipt2)
    assert membership_bytes(plan) == membership_bytes(clean)


def test_matcher_mapping_is_released_by_every_process(tmp_path: Path) -> None:
    plan, index, receipt = setup(tmp_path, corpus(files=2, per_file=20), 4)
    execute(plan, index, receipt)
    assert_no_workers()
    matcher = Path(plan.scratch_root) / plan.identity() / "matcher"
    moved = matcher.with_name("matcher-moved")
    os.rename(matcher, moved)  # Refused on Windows while any process maps its files.
    os.rename(moved, matcher)


def test_pool_capacities_are_bounded_and_child_errors_are_typed(tmp_path: Path) -> None:
    from xlm.data.exclusion.grouping import GroupInit

    init = GroupInit((), (0,), str(tmp_path), 1, 128, 32, ())
    pool = scanpool.ProcessPool(3, "group", init)
    assert pool.capacity == 2 * 3
    try:
        pool.start(lambda: None)
        pool.submit(("not-a-job",))
        with pytest.raises(C05Error, match="C05 worker failed: C05Error unknown grouping job"):
            pool.get(lambda: None)
    finally:
        pool.close()
    assert_no_workers()
    with pytest.raises(C05Error):
        scanpool.ProcessPool(17, "group", init)
    with pytest.raises(C05Error):
        scanpool.ProcessPool(2, "arbitrary.module", init)


def test_spilled_unit_sections_are_identical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from xlm.data.exclusion import factstore

    files = corpus(files=2, per_file=30)
    clean, index, receipt = setup(tmp_path / "memory", files, 1)
    execute(clean, index, receipt)
    monkeypatch.setattr(factstore, "SPILL_BYTES", 512)
    spilled, index2, receipt2 = setup(tmp_path / "spilled", files, 1)
    execute(spilled, index2, receipt2)
    assert membership_bytes(spilled) == membership_bytes(clean)
    for n in range(2):
        a = (Path(clean.scratch_root) / clean.identity() / "facts" / f"{n:05d}.unit").read_bytes()
        b = (
            Path(spilled.scratch_root) / spilled.identity() / "facts" / f"{n:05d}.unit"
        ).read_bytes()
        # Same sections and logical facts; only the signed header's plan id differs.
        assert a[-4096:] == b[-4096:]


def test_memory_plan_changes_never_change_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from xlm.data.exclusion import grouping

    files = corpus(files=3, per_file=40, seed=44)
    clean, index, receipt = setup(tmp_path / "default", files, 1)
    execute(clean, index, receipt)
    tight = grouping.MemoryPlan(band_pass=1, lineage_run_records=7, near_chunk_docs=5, job_docs=3)
    monkeypatch.setattr(grouping.MemoryPlan, "derive", staticmethod(lambda r, n: tight))
    small, index2, receipt2 = setup(tmp_path / "tight", files, 2)
    from xlm.data.exclusion import extsort

    monkeypatch.setattr(
        extsort.ExternalSorter,
        "__init__",
        _sorter_without_run_cap(extsort.ExternalSorter.__init__),
    )
    execute(small, index2, receipt2)
    assert membership_bytes(small) == membership_bytes(clean)
    seal = lambda p: canonical.loads_bytes_strict(  # noqa: E731
        (Path(p.scratch_root) / p.identity() / "seal.json").read_bytes()
    )["payload"]["groups"]
    assert seal(small) == seal(clean)


def _sorter_without_run_cap(init: Any) -> Any:
    def wrapped(self: Any, *args: Any, **kwargs: Any) -> None:
        kwargs["max_runs"] = 10_000
        init(self, *args, **kwargs)

    return wrapped


def test_external_sort_runs_and_merge_are_exact(tmp_path: Path) -> None:
    import numpy as np

    from xlm.data.exclusion.extsort import ExternalSorter
    from xlm.data.exclusion.factstore import IndexLedger
    from xlm.data.exclusion.grouping import LINEAGE, LINEAGE_KEYS

    rng = np.random.default_rng(3)
    records = np.zeros(5000, dtype=LINEAGE)
    for column in ("w0", "w1", "w2", "w3"):
        records[column] = rng.integers(0, 4, 5000, dtype=np.uint64)  # Many equal keys.
    records["dense"] = rng.permutation(5000).astype(np.uint32)
    ledger = IndexLedger(1 << 30, 0)
    expected = np.sort(records, order=list(LINEAGE_KEYS))
    for run_records, block in ((10**6, 1 << 18), (37, 5), (499, 64), (1, 1)):
        sorter = ExternalSorter(
            LINEAGE,
            LINEAGE_KEYS,
            run_records=run_records,
            directory=tmp_path / f"runs-{run_records}",
            ledger=ledger,
            check=lambda: None,
            block_records=block,
            max_runs=10_000,
        )
        sorter.add(records)
        merged = np.concatenate(list(sorter.merged(chunk_records=123)))
        keys = list(LINEAGE_KEYS)
        assert np.array_equal(merged[keys], expected[keys])
        assert not any((tmp_path / f"runs-{run_records}").glob("*.bin"))
    assert ledger.used == 0


def test_external_sort_refuses_beyond_its_run_ceiling(tmp_path: Path) -> None:
    import numpy as np

    from xlm.data.exclusion.extsort import ExternalSorter
    from xlm.data.exclusion.factstore import IndexLedger
    from xlm.data.exclusion.grouping import LINEAGE, LINEAGE_KEYS

    sorter = ExternalSorter(
        LINEAGE,
        LINEAGE_KEYS,
        run_records=2,
        directory=tmp_path,
        ledger=IndexLedger(1 << 20, 0),
        check=lambda: None,
        max_runs=3,
    )
    with pytest.raises(C05Error, match="run ceiling"):
        sorter.add(np.zeros(20, dtype=LINEAGE))


def test_working_index_ceiling_counts_fact_units(tmp_path: Path) -> None:
    plan, index, receipt = setup(tmp_path, corpus(files=3, per_file=20), 2, index_bytes=96 * 1024)
    with pytest.raises(C05Error, match="working-index ceiling"):
        execute(plan, index, receipt)
    assert_no_workers()


def test_hit_document_propagates_through_parent_with_workers(tmp_path: Path) -> None:
    parent = document("p-1", PROMPT)
    child = replace(document("c-1", "Harmless child text."), parent_ids=["p-1"])
    plan, index, receipt = setup(tmp_path, [[parent], [child]], 2)
    result = execute(plan, index, receipt)
    assert result["payload"]["excluded"] == 2
    _ = ProductionPolicy
