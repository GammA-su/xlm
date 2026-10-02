"""Intra-file row-group parallelism: exact serial identity, ordering, bounds, failures.

Authored offline Parquet fixtures; worker processes are real spawn-context
processes, so ordering and failure handling cross a process boundary.
"""

from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path
from typing import Any

import psutil
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from mix01_source_fixtures import ETAG, FIELDS, ultrax_row
from test_source_plan import PIN, layout
from xlm.data.acquisition import source_local
from xlm.data.acquisition import source_plan as planner
from xlm.data.acquisition import transport_policy as tp
from xlm.data.acquisition.records import RecordLimitError
from xlm.data.acquisition.source_growth import GrowthLimitError, ProcessingGrowth
from xlm.data.acquisition.source_rowgroups import (
    ROW_INDEPENDENT_ADAPTERS,
    GroupResult,
    GroupTask,
    ProcessingMemoryError,
    RowGroupError,
    RowGroupParallel,
    adapt_assigns_state,
    check_concurrency,
)
from xlm.data.adapters.columns import columns_for
from xlm.data.adapters.registry import ADAPTERS_BY_ID
from xlm.data.adapters.rejections import DOCUMENTS_FILENAME, SUMMARY_FILENAME

SOURCE_FILE = "data/part-0000.parquet"
COLUMNS = tuple(columns_for(PIN["adapter_id"], PIN["view_id"]))
GROUP_ROWS = 600
ROWS = 1900
#: Keys whose values are timings or execution-shape metadata, never output.
VOLATILE = {"process_seconds", "process_cpu_seconds", "row_group_workers", "row_group_pool"}
LIMITS: dict[str, Any] = {
    "max_decompression_ratio": 15.0,
    "max_parser_bytes": 32 * 1024**2,
    "max_rows_per_file": 10_000,
    "max_record_bytes": 64 * 1024,
    "max_decoded_bytes_per_file": 64 * 1024**2,
    "max_ledger_bytes": 1024**2,
    "max_canonical_bytes_per_file": 64 * 1024**2,
    "processing_growth": ProcessingGrowth(output_bytes=64 * 1024**2).model_dump(),
    "scratch_min_free_bytes": 0,
}


def parallel(workers: int = 2, lookahead: int = 1, **changes: Any) -> dict[str, Any]:
    config = {
        "workers": workers,
        "lookahead": lookahead,
        "processing_slots": 16,
        "memory_bytes": 64 * 1024**3,
        **changes,
    }
    return RowGroupParallel.model_validate(config).model_dump()


def write_source(path: Path, rows: list[dict[str, Any]], group_rows: int = GROUP_ROWS) -> Path:
    table = pa.Table.from_pylist(rows, schema=pa.schema([(name, pa.string()) for name in FIELDS]))
    pq.write_table(table, path, row_group_size=group_rows)
    return path


def authored_rows(count: int = ROWS) -> list[dict[str, Any]]:
    return [ultrax_row(i, empty=i % 7 == 3) for i in range(count)]


@pytest.fixture
def source(tmp_path: Path) -> Path:
    return write_source(tmp_path / "source.parquet", authored_rows())


def adapt(
    path: Path, out: Path, limits: dict[str, Any], row_range: tuple[int, int] | None = None
) -> dict[str, Any]:
    return source_local.adapt_source_file(
        path,
        out,
        source_file=SOURCE_FILE,
        source_id=PIN["source_id"],
        view_id=PIN["view_id"],
        adapter_id=PIN["adapter_id"],
        repository=PIN["repository"],
        revision=PIN["revision"],
        plan_id="plan_fixture",
        plan_hash="1" * 64,
        selection_hash="2" * 64,
        identity={"etag": ETAG, "sha256": "3" * 64, "length": path.stat().st_size},
        limits=limits,
        row_range=row_range,
    )


def outputs(directory: Path) -> dict[str, bytes]:
    return {path.name: path.read_bytes() for path in sorted(directory.iterdir())}


def stable(result: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in result.items() if key not in VOLATILE}


def failure(path: Path, out: Path, limits: dict[str, Any]) -> tuple[type[BaseException], str]:
    with pytest.raises(Exception) as caught:
        adapt(path, out, limits)
    assert not (out / SUMMARY_FILENAME).exists()
    return type(caught.value), str(caught.value)


def children() -> set[int]:
    return {child.pid for child in psutil.Process().children(recursive=True)}


# ------------------------------------------------------------ test workers
# Module-level so spawned worker processes import them by name.


def crashing_worker(task: GroupTask) -> GroupResult:
    if task.group == 1:
        os._exit(7)
    return source_local.adapt_row_group(task)


def reversed_worker(task: GroupTask) -> GroupResult:
    """Later row groups finish first: completion order is the reverse of file order."""
    time.sleep(0.4 * (3 - min(task.group, 3)))
    return source_local.adapt_row_group(task)


def unpicklable_failure_worker(task: GroupTask) -> GroupResult:
    result = source_local.adapt_row_group(task)
    if task.group == 2:

        class LocalError(Exception):
            pass

        result.error = source_local._portable(LocalError("authored local failure"))
    return result


# ------------------------------------------------------------------- tests


def test_partition_is_deterministic_row_group_aligned_and_clipped(source: Path) -> None:
    def partition(row_range: tuple[int, int]) -> list[tuple[int, int, int, tuple[int, int]]]:
        tasks = source_local.group_tasks(
            source,
            source_file=SOURCE_FILE,
            source_id=PIN["source_id"],
            revision=PIN["revision"],
            adapter_id=PIN["adapter_id"],
            locator={"source_id": PIN["source_id"]},
            etag=ETAG,
            columns=COLUMNS,
            limits=LIMITS,
            row_range=row_range,
        )
        return [(t.group, t.base, t.rows, t.row_range) for t in tasks]

    whole = partition((0, ROWS))
    assert whole == partition((0, ROWS))
    assert whole == [
        (0, 0, 600, (0, ROWS)),
        (1, 600, 600, (0, ROWS)),
        (2, 1200, 600, (0, ROWS)),
        (3, 1800, 100, (0, ROWS)),
    ]
    assert [g for g, *_ in partition((650, 1250))] == [1, 2]
    with pytest.raises(ValueError, match="beyond Parquet corpus"):
        partition((0, ROWS + 1))


@pytest.mark.parametrize(("workers", "lookahead"), [(2, 0), (3, 2), (4, 1)])
def test_parallel_output_is_byte_identical_to_serial(
    source: Path, tmp_path: Path, workers: int, lookahead: int
) -> None:
    serial = adapt(source, tmp_path / "serial", LIMITS)
    fast = adapt(
        source,
        tmp_path / "parallel",
        {**LIMITS, "row_group_parallel": parallel(workers, lookahead)},
    )
    assert serial["rejected"] > 0 and serial["documents"] > 0
    assert fast["row_group_workers"] == min(workers, 4) and serial["row_group_workers"] == 1
    assert stable(fast) == stable(serial)
    assert outputs(tmp_path / "parallel") == outputs(tmp_path / "serial")
    # Rows are ordered as in the file, whatever the worker that produced them.
    lines = (tmp_path / "parallel" / DOCUMENTS_FILENAME).read_bytes().splitlines()
    rows = [int(line.split(b'"source_row":')[1].split(b",")[0]) for line in lines]
    assert rows == sorted(rows) and len(rows) == serial["documents"]


def test_completion_order_never_changes_output(
    source: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    serial = adapt(source, tmp_path / "serial", LIMITS)
    monkeypatch.setattr(source_local, "adapt_row_group", reversed_worker)
    fast = adapt(source, tmp_path / "parallel", {**LIMITS, "row_group_parallel": parallel(4, 0)})
    assert stable(fast) == stable(serial)
    assert outputs(tmp_path / "parallel") == outputs(tmp_path / "serial")
    assert fast["row_group_pool"]["head_of_line_wait_seconds"] > 0.5


def test_partial_row_range_matches_serial(source: Path, tmp_path: Path) -> None:
    serial = adapt(source, tmp_path / "serial", LIMITS, (650, 1830))
    fast = adapt(
        source, tmp_path / "parallel", {**LIMITS, "row_group_parallel": parallel()}, (650, 1830)
    )
    assert serial["rows"] == 1180 and stable(fast) == stable(serial)
    assert outputs(tmp_path / "parallel") == outputs(tmp_path / "serial")


def test_one_row_group_and_absent_config_take_the_legacy_path(tmp_path: Path) -> None:
    path = write_source(tmp_path / "one.parquet", authored_rows(300), group_rows=1000)
    legacy = adapt(path, tmp_path / "legacy", LIMITS)
    single = adapt(path, tmp_path / "single", {**LIMITS, "row_group_parallel": parallel()})
    assert single["row_group_workers"] == 1 and single["row_group_pool"] is None
    assert stable(single) == stable(legacy)
    assert outputs(tmp_path / "single") == outputs(tmp_path / "legacy")


def test_pathological_records_raise_the_serial_first_error(tmp_path: Path) -> None:
    rows = authored_rows()
    for index in (700, 1850):  # row groups 1 and 3: the later one may finish first
        rows[index]["cleaned_content"] = "x" * 80_000
    path = write_source(tmp_path / "bad.parquet", rows)
    serial = failure(path, tmp_path / "serial", LIMITS)
    fast = failure(path, tmp_path / "parallel", {**LIMITS, "row_group_parallel": parallel(4, 0)})
    assert serial == fast
    assert serial[0] is RecordLimitError and "row=700 " in serial[1]


@pytest.mark.parametrize(
    "change",
    [
        {"max_decoded_bytes_per_file": 200_000},
        {"max_canonical_bytes_per_file": 50_000},
        {"max_ledger_bytes": 4_000},
        {"processing_growth": ProcessingGrowth(output_bytes=120_000).model_dump()},
    ],
    ids=["decoded", "canonical", "ledger", "output"],
)
def test_bound_breaches_match_serial(source: Path, tmp_path: Path, change: dict[str, Any]) -> None:
    limits = {**LIMITS, **change}
    serial = failure(source, tmp_path / "serial", limits)
    fast = failure(source, tmp_path / "parallel", {**limits, "row_group_parallel": parallel(3, 1)})
    assert fast == serial
    assert serial[0] in (RecordLimitError, GrowthLimitError, source_local.SourceAdaptError)


def test_memory_ceiling_refuses_and_stops_every_worker(source: Path, tmp_path: Path) -> None:
    before = children()
    with pytest.raises(ProcessingMemoryError):
        adapt(
            source,
            tmp_path / "parallel",
            {**LIMITS, "row_group_parallel": parallel(2, 0, memory_bytes=1)},
        )
    assert not (tmp_path / "parallel" / SUMMARY_FILENAME).exists()
    assert children() <= before


def test_worker_crash_propagates_without_publication(
    source: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = children()
    monkeypatch.setattr(source_local, "adapt_row_group", crashing_worker)
    with pytest.raises(RowGroupError, match="terminated abnormally"):
        adapt(source, tmp_path / "parallel", {**LIMITS, "row_group_parallel": parallel(2, 1)})
    assert not (tmp_path / "parallel" / SUMMARY_FILENAME).exists()
    assert children() <= before
    # A restart into a fresh private directory reproduces the serial unit exactly.
    monkeypatch.undo()
    serial = adapt(source, tmp_path / "serial", LIMITS)
    again = adapt(source, tmp_path / "again", {**LIMITS, "row_group_parallel": parallel(2, 1)})
    assert stable(again) == stable(serial)


def test_unportable_worker_error_is_summarized(
    source: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(source_local, "adapt_row_group", unpicklable_failure_worker)
    with pytest.raises(RowGroupError, match="LocalError: authored local failure"):
        adapt(source, tmp_path / "parallel", {**LIMITS, "row_group_parallel": parallel()})


def test_source_change_is_refused(source: Path, tmp_path: Path) -> None:
    tasks = source_local.group_tasks(
        source,
        source_file=SOURCE_FILE,
        source_id=PIN["source_id"],
        revision=PIN["revision"],
        adapter_id=PIN["adapter_id"],
        locator={"source_id": PIN["source_id"]},
        etag=ETAG,
        columns=COLUMNS,
        limits=LIMITS,
        row_range=(0, ROWS),
    )
    os.utime(source, ns=(time.time_ns(), tasks[0].source.mtime_ns + 10**9))
    result = source_local.adapt_row_group(tasks[0])
    assert isinstance(result.error, RowGroupError) and not result.kinds


def test_ineligible_adapter_refuses_before_any_output(tmp_path: Path) -> None:
    out = tmp_path / "out"
    with pytest.raises(source_local.SourceAdaptError, match="not declared row-independent"):
        source_local.adapt_source_file(
            tmp_path / "absent.parquet",
            out,
            source_file=SOURCE_FILE,
            source_id="essential_web",
            view_id="v",
            adapter_id="essential_web",
            repository="r",
            revision="a" * 40,
            plan_id="p",
            plan_hash="1" * 64,
            selection_hash="2" * 64,
            identity={"etag": ETAG, "sha256": "3" * 64, "length": 1},
            limits={**LIMITS, "row_group_parallel": parallel()},
        )
    assert not out.exists()


def test_row_independent_adapters_assign_no_state() -> None:
    assert ROW_INDEPENDENT_ADAPTERS <= set(ADAPTERS_BY_ID)
    assert not {"essential_web", "essential_web_bnormal"} & ROW_INDEPENDENT_ADAPTERS
    for adapter_id in sorted(ROW_INDEPENDENT_ADAPTERS):
        assert not adapt_assigns_state(ADAPTERS_BY_ID[adapter_id]), adapter_id

    class Counting:
        def adapt(self, record: dict[str, Any]) -> None:
            self.seen = record

    class Accumulating:
        def adapt(self, record: dict[str, Any]) -> None:
            self.cache[record["id"]] = record  # type: ignore[attr-defined]

    assert adapt_assigns_state(Counting) and adapt_assigns_state(Accumulating)


def test_global_slot_budget_bounds_files_times_workers() -> None:
    config = parallel(4, 2, processing_slots=15)
    check_concurrency(3, {"row_group_parallel": config})
    check_concurrency(0, {"row_group_parallel": config})
    check_concurrency(16, {})
    with pytest.raises(RowGroupError, match="exceed the 15 authorized processing slots"):
        check_concurrency(4, {"row_group_parallel": config})
    with pytest.raises(ValueError, match="exceed the processing slots"):
        RowGroupParallel(workers=4, lookahead=0, processing_slots=4, memory_bytes=1)
    assert RowGroupParallel(workers=4, lookahead=0, processing_slots=5, memory_bytes=1)


def test_plan_binds_parallelism_only_for_listed_views(monkeypatch: pytest.MonkeyPatch) -> None:
    base, _ = planner.plan_limits(5, layout(), tp.TransportMode.WHOLE_FILE_LOCAL, PIN)
    assert not {"row_group_parallel", "row_group_parallel_basis"} & set(base)
    config = RowGroupParallel(workers=4, lookahead=2, processing_slots=15, memory_bytes=8 * 1024**3)
    monkeypatch.setitem(
        planner.SOURCE_ROW_GROUP_PARALLEL,
        (PIN["source_id"], PIN["view_id"]),
        (config, "authored evidence"),
    )
    bound, _ = planner.plan_limits(5, layout(), tp.TransportMode.WHOLE_FILE_LOCAL, PIN)
    assert bound["row_group_parallel"] == config.model_dump()
    assert bound["row_group_parallel_basis"] == "authored evidence"
    assert bound["process_workers"] == 3 and base["process_workers"] == 5
    assert bound["max_in_flight_files"] <= base["max_in_flight_files"]
    check_concurrency(bound["process_workers"], bound)
    ineligible = {**PIN, "adapter_id": "essential_web"}
    with pytest.raises(planner.PlanError, match="row-independent"):
        planner.plan_limits(5, layout(), tp.TransportMode.WHOLE_FILE_LOCAL, ineligible)


def test_payload_stream_hash_matches_the_certified_reader(source: Path, tmp_path: Path) -> None:
    fast = adapt(source, tmp_path / "parallel", {**LIMITS, "row_group_parallel": parallel(3, 0)})
    digest = hashlib.sha256()
    from xlm.data.acquisition.source_parquet import selected_payloads

    for _, payload in selected_payloads(
        source,
        source_file=SOURCE_FILE,
        locator={
            "source_id": PIN["source_id"],
            "repository": PIN["repository"],
            "revision": PIN["revision"],
            "selection_hash": "2" * 64,
        },
        etag=ETAG,
        columns=COLUMNS,
        max_record_bytes=LIMITS["max_record_bytes"],
        max_parser_bytes=LIMITS["max_parser_bytes"],
        max_decoded_bytes=LIMITS["max_decoded_bytes_per_file"],
    ):
        digest.update(payload)
    assert fast["selected_records_sha256"] == digest.hexdigest()
