"""Acceptance-fix probes for aa7b580: I04 two-phase publication, worker-pool failure
normalization (historical ``test_child_rss_included``), I10 semantic envelope checks.

Authored fixtures only; no network, no real corpus, no real C05.
"""

from __future__ import annotations

import json
import os
import shutil
import threading
import time
import zlib
from collections.abc import Callable, Iterator
from concurrent.futures.process import BrokenProcessPool
from pathlib import Path
from typing import Any

import pytest

from quality_fixtures import build_corpus, standard_layout
from test_quality_audit import audit
from xlm.data.evidence_v2 import canonical
from xlm.data.exclusion import supervisor as supervisor_module
from xlm.data.exclusion.policy import C05Error
from xlm.data.quality import outputs, receipt, runner, scan
from xlm.data.quality.cli import main
from xlm.data.quality.outputs import STAGE_DIR, OutputTree
from xlm.data.quality.receipt import build_receipt
from xlm.data.quality.report import build_artifacts
from xlm.data.quality.review import read_review_rows
from xlm.data.quality.runner import verify_report
from xlm.data.quality.scan import RECEIPT_FILE, QualityError, WorkerPoolError

REASONS = {
    "rss": supervisor_module.RSS_REASON,
    "disk": supervisor_module.DISK_REASON,
    "deadline": supervisor_module.DEADLINE_REASON,
}


@pytest.fixture
def corpus(tmp_path: Path) -> Path:
    return build_corpus(tmp_path / "corpus", standard_layout())


@pytest.fixture
def guards(monkeypatch: pytest.MonkeyPatch) -> list[runner.Guard]:
    seen: list[runner.Guard] = []
    original = runner.Guard

    class Recording(original):  # type: ignore[valid-type,misc]
        def __init__(self, **kwargs: Any) -> None:
            super().__init__(**kwargs)
            seen.append(self)

    monkeypatch.setattr(runner, "Guard", Recording)
    return seen


def run_cli(corpus: Path, output: Path, capsys: pytest.CaptureFixture[str]) -> tuple[int, Any]:
    code = main(
        [
            "audit",
            "--manifest",
            str(corpus),
            "--output",
            str(output),
            "--workers",
            "1",
            "--no-progress",
            "--free-reserve-gib",
            "0",
        ]
    )
    return code, json.loads(capsys.readouterr().out)


def assert_no_completion(output: Path) -> None:
    assert not (output / RECEIPT_FILE).exists()
    stage = output / STAGE_DIR
    assert not stage.exists() or not any(stage.iterdir())


# -- I04 two-phase publication: fault injection at every point -------------------------------

POINTS = (
    "serialization",
    "staging_write",
    "staging_fsync",
    "staging_dir_fsync",
    "monitor_shutdown",
    "before_rename",
    "after_rename",
)


def _violate(mode: str, guard: runner.Guard, state: dict[str, bool]) -> None:
    """A recorded monitor failure, or (``measured:*``) every later fresh measurement
    violating its limit."""
    if mode.startswith("recorded:"):
        guard.supervisor.fail(REASONS[mode.split(":", 1)[1]])
    else:
        state[mode.split(":", 1)[1]] = True


def _inject(
    monkeypatch: pytest.MonkeyPatch,
    point: str,
    act: Callable[[], None],
) -> None:
    if point == "serialization":
        real_build = build_receipt

        def build(**kwargs: Any) -> bytes:
            raw = real_build(**kwargs)
            act()
            return raw

        monkeypatch.setattr(runner, "build_receipt", build)
    elif point == "staging_write":
        real_write = canonical.write_atomic

        def write(path: Path, payload: bytes) -> None:
            if path.parent.name == STAGE_DIR:
                act()
            real_write(path, payload)

        monkeypatch.setattr(canonical, "write_atomic", write)
    elif point == "staging_fsync":
        real_fsync = os.fsync

        def fsync(fd: int) -> None:
            if os.readlink(f"/proc/self/fd/{fd}").endswith(f"{STAGE_DIR}/{RECEIPT_FILE}.tmp"):
                act()
            real_fsync(fd)

        monkeypatch.setattr(os, "fsync", fsync)
    elif point == "staging_dir_fsync":
        real_dir = outputs.fsync_directory

        def fsync_dir(directory: Path) -> None:
            if directory.name == STAGE_DIR:
                act()
            real_dir(directory)

        monkeypatch.setattr(outputs, "fsync_directory", fsync_dir)
    elif point == "monitor_shutdown":
        real_exit = supervisor_module.Supervisor.__exit__

        def shutdown(self: Any, kind: Any, *rest: Any) -> None:
            if kind is None:
                act()
            real_exit(self, kind, *rest)

        monkeypatch.setattr(supervisor_module.Supervisor, "__exit__", shutdown)
    else:
        real_publish = OutputTree.publish

        def publish(self: OutputTree, name: str) -> Path:
            if point == "before_rename":
                act()
            path = real_publish(self, name)
            if point == "after_rename":
                act()
            return path

        monkeypatch.setattr(OutputTree, "publish", publish)


def _measurements(monkeypatch: pytest.MonkeyPatch, state: dict[str, bool]) -> None:
    real_rss, real_free = runner._tree_rss, runner._free_bytes
    real_remaining = supervisor_module.Supervisor.remaining
    real_used = OutputTree.used_bytes
    monkeypatch.setattr(runner, "_tree_rss", lambda: 1 << 60 if state["rss"] else real_rss())
    monkeypatch.setattr(
        runner, "_free_bytes", lambda path: -1 if state["disk"] else real_free(path)
    )
    monkeypatch.setattr(
        supervisor_module.Supervisor,
        "remaining",
        lambda self: -1.0 if state["deadline"] else real_remaining(self),
    )
    monkeypatch.setattr(
        OutputTree, "used_bytes", lambda self: 1 << 60 if state["output"] else real_used(self)
    )


MODES = (
    "recorded:rss",
    "recorded:disk",
    "recorded:deadline",
    "measured:rss",
    "measured:disk",
    "measured:deadline",
    "measured:output",
)


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("point", POINTS)
def test_i04_late_failure_never_publishes(
    corpus: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    guards: list[runner.Guard],
    point: str,
    mode: str,
) -> None:
    if point == "staging_fsync" and not Path("/proc/self/fd").is_dir():
        pytest.skip("fd path inspection needs /proc (POSIX); other fsync points still run")
    state = dict.fromkeys(("rss", "disk", "deadline", "output"), False)
    _measurements(monkeypatch, state)
    fired: list[str] = []

    def act() -> None:
        if not fired:
            fired.append(point)
            _violate(mode, guards[-1], state)

    _inject(monkeypatch, point, act)
    output = tmp_path / "out"
    code, printed = run_cli(corpus, output, capsys)
    assert fired == [point], "the injection point was not reached"
    assert code == 1 and printed.get("refused") is True
    assert "complete" not in printed and "result_digest" not in printed
    assert_no_completion(output)
    with pytest.raises(QualityError, match="incomplete"):
        verify_report(corpus, output)


def test_i04_protocol_order(
    corpus: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, guards: list[runner.Guard]
) -> None:
    """Staging happens with the monitor alive; publication after it is joined and
    after the pre-publication gate; the post-publication gate runs last."""
    events: list[str] = []
    real_stage, real_publish = OutputTree.stage, OutputTree.publish
    real_final = runner.Guard.final

    def stage(self: OutputTree, name: str, payload: bytes) -> Path:
        events.append(f"stage(monitor_alive={guards[-1].supervisor.thread.is_alive()})")
        return real_stage(self, name, payload)

    def publish(self: OutputTree, name: str) -> Path:
        assert not (self.root / name).exists()
        events.append(f"rename(monitor_alive={guards[-1].supervisor.thread.is_alive()})")
        return real_publish(self, name)

    def final(self: runner.Guard, margin: float, check: Any = None) -> None:
        events.append(f"gate(margin={margin})")
        real_final(self, margin, check)

    monkeypatch.setattr(OutputTree, "stage", stage)
    monkeypatch.setattr(OutputTree, "publish", publish)
    monkeypatch.setattr(runner.Guard, "final", final)
    output = tmp_path / "out"
    result = audit(corpus, output)
    assert result["complete"] is True
    assert result["scan"]["peak_process_tree_rss_bytes"] > 0  # benchmark consumers
    assert events == [
        "stage(monitor_alive=True)",
        f"gate(margin={runner.PUBLICATION_MARGIN})",
        "rename(monitor_alive=False)",
        "gate(margin=0.0)",
    ]
    assert verify_report(corpus, output)["verified"] is True
    assert not any((output / STAGE_DIR).iterdir())


def test_i04_withdrawn_receipt_directory_is_fsynced(
    corpus: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, guards: list[runner.Guard]
) -> None:
    synced: list[str] = []
    real_dir = outputs.fsync_directory

    def record(directory: Path) -> None:
        synced.append(directory.name)
        real_dir(directory)

    monkeypatch.setattr(outputs, "fsync_directory", record)
    real_publish = OutputTree.publish

    def publish(self: OutputTree, name: str) -> Path:
        path = real_publish(self, name)
        assert path.exists()
        synced.append("<published>")
        guards[-1].supervisor.fail(supervisor_module.RSS_REASON)
        return path

    monkeypatch.setattr(OutputTree, "publish", publish)
    output = tmp_path / "out"
    with pytest.raises(QualityError, match="RSS"):
        audit(corpus, output)
    assert_no_completion(output)
    after = synced[synced.index("<published>") + 1 :]
    assert after[0] == output.name  # the withdrawal is fsynced in the output root


def test_i04_partial_staging_is_discarded_and_resume_succeeds(
    corpus: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Stop(Exception):
        pass

    real_write = canonical.write_atomic

    def partial(path: Path, payload: bytes) -> None:
        if path.parent.name == STAGE_DIR:
            path.with_name(path.name + ".tmp").write_bytes(b"partial")
            raise Stop()
        real_write(path, payload)

    monkeypatch.setattr(canonical, "write_atomic", partial)
    output = tmp_path / "out"
    with pytest.raises(Stop):
        audit(corpus, output)
    assert_no_completion(output)
    monkeypatch.setattr(canonical, "write_atomic", real_write)
    assert audit(corpus, output)["files_resumed"] == len(json.loads(corpus.read_bytes())["files"])
    assert verify_report(corpus, output)["verified"] is True


def test_i04_foreign_file_in_staging_refuses(corpus: Path, tmp_path: Path) -> None:
    output = tmp_path / "out"
    audit(corpus, output)
    (output / STAGE_DIR / "notes.txt").write_bytes(b"keep me")
    with pytest.raises(QualityError, match="does not own"):
        verify_report(corpus, output)
    assert (output / STAGE_DIR / "notes.txt").read_bytes() == b"keep me"


# -- worker-pool failure normalization -------------------------------------------------------


def sleeper(seconds: float) -> float:
    time.sleep(seconds)
    return seconds


def crasher(code: int) -> None:
    os._exit(code)


def crash_chunk(task: scan.ChunkTask) -> None:
    os._exit(7)


class Recorded:
    """A supervisor that has recorded a failure, then terminates the workers."""

    def __init__(self) -> None:
        self.failure: str | None = None

    def check(self) -> None:
        if self.failure is not None:
            raise C05Error(self.failure)


@pytest.mark.parametrize("reason", sorted(REASONS))
def test_pool_breakage_resolves_to_recorded_supervisor_reason(reason: str) -> None:
    import psutil

    holder = Recorded()

    def kill_workers(pool: scan.OrderedPool) -> None:
        time.sleep(0.5)
        holder.failure = REASONS[reason]  # recorded BEFORE termination, as Supervisor.fail
        assert pool.executor is not None
        workers = pool.executor._processes.values()
        processes = [psutil.Process(p.pid) for p in workers]
        supervisor_module.terminate_processes(processes, 2.0)

    with pytest.raises(C05Error) as caught:
        with scan.OrderedPool(2, holder) as pool:
            threading.Thread(target=kill_workers, args=(pool,), daemon=True).start()
            list(pool.map(sleeper, [5.0, 5.0]))
    assert str(caught.value) == REASONS[reason]
    assert not isinstance(caught.value, BrokenProcessPool)


def test_pool_breakage_without_reason_is_controlled() -> None:
    with pytest.raises(WorkerPoolError) as caught:
        with scan.OrderedPool(2, Recorded()) as pool:
            list(pool.map(crasher, [7]))
    assert isinstance(caught.value, QualityError)
    assert isinstance(caught.value, BrokenProcessPool)  # still classifiable as breakage


def slow_chunk(task: scan.ChunkTask) -> Any:
    time.sleep(1.0)
    return scan.process_chunk(task)


@pytest.mark.parametrize("reason", sorted(REASONS))
def test_audit_pool_killed_by_supervisor_reports_its_reason(
    corpus: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    guards: list[runner.Guard],
    reason: str,
) -> None:
    monkeypatch.setattr(runner, "process_chunk", slow_chunk)

    def fail_soon() -> None:
        while not guards:
            time.sleep(0.01)
        time.sleep(0.4)
        guards[-1].supervisor.fail(REASONS[reason])  # terminates every worker

    threading.Thread(target=fail_soon, daemon=True).start()
    output = tmp_path / "out"
    code = main(
        [
            "audit",
            "--manifest",
            str(corpus),
            "--output",
            str(output),
            "--workers",
            "2",
            "--no-progress",
            "--free-reserve-gib",
            "0",
        ]
    )
    printed = json.loads(capsys.readouterr().out)
    expected = {"rss": "RSS", "disk": "free space", "deadline": "deadline"}[reason]
    assert code == 1 and expected in printed["error"]
    assert_no_completion(output)


def test_audit_worker_crash_without_reason_is_controlled(
    corpus: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(runner, "process_chunk", crash_chunk)
    with pytest.raises(QualityError, match="worker process terminated"):
        audit(corpus, tmp_path / "out", workers=2)


# -- I10 semantic envelope verification ------------------------------------------------------


@pytest.fixture(scope="module")
def completed(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[Path, Path]]:
    root = tmp_path_factory.mktemp("i10-semantic")
    corpus = build_corpus(root / "corpus", standard_layout())
    output = root / "out"
    real = build_artifacts

    def slow(*args: Any, **kwargs: Any) -> Any:
        time.sleep(1.2)  # a measurable supervised elapsed time above the margin
        return real(*args, **kwargs)

    patch = pytest.MonkeyPatch()
    patch.setattr(runner, "build_artifacts", slow)
    try:
        audit(corpus, output, workers=2)
    finally:
        patch.undo()
    yield corpus, output


def facts(output: Path) -> dict[str, Any]:
    body = json.loads((output / RECEIPT_FILE).read_bytes())
    assert isinstance(body, dict)
    return body


def rewrite(output: Path, change: Callable[[dict[str, Any]], None]) -> None:
    path = output / RECEIPT_FILE
    body = json.loads(path.read_bytes())
    change(body)
    body["digest"] = canonical.self_digest(body)  # an attacker re-digests correctly
    path.write_bytes(canonical.canonical_bytes(body))


def _env(**values: Any) -> Callable[[dict[str, Any]], None]:
    def change(body: dict[str, Any]) -> None:
        body["envelope"].update(values)

    return change


def _exe(**values: Any) -> Callable[[dict[str, Any]], None]:
    def change(body: dict[str, Any]) -> None:
        body["execution"].update(values)

    return change


def _attacks(body: dict[str, Any], output: Path) -> dict[str, Callable[[dict[str, Any]], None]]:
    env, exe = body["envelope"], body["execution"]
    total = sum(p.stat().st_size for p in output.rglob("*") if p.is_file())
    longest = exe["max_document_bytes_observed"]
    peak = exe["peak_process_tree_rss_bytes"]
    floor = exe["free_space"][0]["min_observed_free_bytes"]

    def both(**pairs: tuple[Any, Any]) -> Callable[[dict[str, Any]], None]:
        def change(b: dict[str, Any]) -> None:
            for name, (envelope_value, execution_value) in pairs.items():
                b["envelope"][name] = envelope_value
                b["execution"][execution_value[0]] = execution_value[1]

        return change

    return {
        # Envelope fields alone (artifacts and execution facts unchanged).
        "output_1_byte": _env(max_output_bytes=1),
        "output_just_below_actual": _env(max_output_bytes=total - 1),
        "rss_1_byte": _env(max_rss_bytes=1),
        "rss_just_below_peak": _env(max_rss_bytes=peak - 1),
        "deadline_below_elapsed": _env(deadline_seconds=0.75),
        "document_1_byte": _env(max_document_bytes=1),
        "document_just_below_largest": _env(max_document_bytes=longest - 1),
        "review_1_per_stratum": _env(review_per_stratum=1),
        "reserve_above_observed": _env(free_reserve_bytes=floor + 1),
        "workers_other_setting": _env(workers=4, queue_tasks=8),
        "workers_inline_setting": _env(workers=1, queue_tasks=1),
        "chunk_other": _env(chunk_bytes=env["chunk_bytes"] // 2),
        "verify_threads_other": _env(verify_threads=env["verify_threads"] + 1),
        "pending_commits_other": _env(max_pending_commits=env["max_pending_commits"] + 1),
        "interval_other": _env(supervisor_interval_seconds=1.0),
        "margin_other": _env(publication_margin_seconds=0.25),
        # Execution facts alone.
        "exec_rss_tiny": _exe(peak_process_tree_rss_bytes=1),
        "exec_rss_above_ceiling": _exe(peak_process_tree_rss_bytes=env["max_rss_bytes"] + 1),
        "exec_elapsed_zero": _exe(wall_seconds=0.0),
        "exec_elapsed_above_deadline": _exe(wall_seconds=env["deadline_seconds"] + 1.0),
        "exec_output_bytes": _exe(
            output_bytes_before_receipt=exe["output_bytes_before_receipt"] - 1
        ),
        "exec_largest_row_1": _exe(max_document_bytes_observed=1),
        "exec_largest_row_above_ceiling": _exe(
            max_document_bytes_observed=env["max_document_bytes"] + 1
        ),
        "exec_free_below_reserve": _exe(
            free_space=[{"reserve_bytes": env["free_reserve_bytes"], "min_observed_free_bytes": -1}]
        ),
        "exec_free_inflated": _exe(
            free_space=[
                {"reserve_bytes": env["free_reserve_bytes"], "min_observed_free_bytes": 1 << 62}
            ]
        ),
        "exec_workers": _exe(workers=4),
        "exec_in_flight_above_queue": _exe(peak_tasks_in_flight=env["queue_tasks"] + 1),
        "exec_in_flight_zero": _exe(peak_tasks_in_flight=0),
        "exec_no_samples": _exe(supervisor_samples=0),
        "exec_files_scanned": _exe(files_scanned=exe["files_scanned"] + 1),
        "exec_unknown_field": _exe(surprise=1),
        "exec_float_as_int": _exe(wall_seconds=int(exe["wall_seconds"]) + 1),
        # Envelope and execution shrunk TOGETHER, consistently with each other.
        "both_rss_1": both(max_rss_bytes=(1, ("peak_process_tree_rss_bytes", 1))),
        "both_deadline": both(deadline_seconds=(0.75, ("wall_seconds", 0.7))),
        "both_document_1": both(max_document_bytes=(1, ("max_document_bytes_observed", 1))),
        "both_reserve": both(
            free_reserve_bytes=(
                1 << 61,
                ("free_space", [{"reserve_bytes": 1 << 61, "min_observed_free_bytes": 1 << 61}]),
            )
        ),
        "both_output_1": both(max_output_bytes=(1, ("output_bytes_before_receipt", 0))),
    }


ATTACKS = (
    "output_1_byte",
    "output_just_below_actual",
    "rss_1_byte",
    "rss_just_below_peak",
    "deadline_below_elapsed",
    "document_1_byte",
    "document_just_below_largest",
    "review_1_per_stratum",
    "reserve_above_observed",
    "workers_other_setting",
    "workers_inline_setting",
    "chunk_other",
    "verify_threads_other",
    "pending_commits_other",
    "interval_other",
    "margin_other",
    "exec_rss_tiny",
    "exec_rss_above_ceiling",
    "exec_elapsed_zero",
    "exec_elapsed_above_deadline",
    "exec_output_bytes",
    "exec_largest_row_1",
    "exec_largest_row_above_ceiling",
    "exec_free_below_reserve",
    "exec_free_inflated",
    "exec_workers",
    "exec_in_flight_above_queue",
    "exec_in_flight_zero",
    "exec_no_samples",
    "exec_files_scanned",
    "exec_unknown_field",
    "exec_float_as_int",
    "both_rss_1",
    "both_deadline",
    "both_document_1",
    "both_reserve",
    "both_output_1",
)


@pytest.mark.parametrize("name", ATTACKS)
def test_i10_redigested_receipt_contradiction_refuses(
    completed: tuple[Path, Path], tmp_path: Path, name: str
) -> None:
    corpus, source = completed
    output = tmp_path / "out"
    shutil.copytree(source, output)
    body = facts(output)
    assert body["execution"]["wall_seconds"] > 1.0  # the deadline attacks are contradictions
    rewrite(output, _attacks(body, output)[name])
    with pytest.raises(QualityError, match="receipt"):
        verify_report(corpus, output)
    # The CLI refuses too, and materialization never starts.
    assert main(["report", "--manifest", str(corpus), "--output", str(output)]) == 1
    assert (
        main(
            [
                "materialize-review",
                "--output",
                str(output),
                "--destination",
                str(tmp_path / "review"),
                "--operator-confirm",
            ]
        )
        == 1
    )
    assert not (tmp_path / "review").exists()


def test_i10_attack_list_is_complete(completed: tuple[Path, Path]) -> None:
    _, output = completed
    assert sorted(_attacks(facts(output), output)) == sorted(ATTACKS)


def test_i10_genuine_receipt_binds_measured_facts(completed: tuple[Path, Path]) -> None:
    corpus, output = completed
    body = facts(output)
    env, exe = body["envelope"], body["execution"]
    assert exe["workers"] == env["workers"] == 2
    assert 1 <= exe["peak_tasks_in_flight"] <= env["queue_tasks"]
    assert 0 < exe["peak_process_tree_rss_bytes"] <= env["max_rss_bytes"]
    assert 1.0 < exe["wall_seconds"] <= env["deadline_seconds"]
    assert exe["free_space"][0]["min_observed_free_bytes"] >= env["free_reserve_bytes"]
    assert 0 < exe["max_document_bytes_observed"] <= env["max_document_bytes"]
    on_disk = sum(p.stat().st_size for p in output.rglob("*") if p.is_file())
    receipt_bytes = (output / RECEIPT_FILE).stat().st_size
    assert exe["output_bytes_before_receipt"] + receipt_bytes == on_disk
    assert verify_report(corpus, output)["verified"] is True


def test_i10_review_strata_rederived_independently(
    completed: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With the implementation-constant check bypassed and the receipt AND every
    unit's producer envelope forged consistently, the re-read review manifest alone
    still refuses a one-row-per-stratum claim."""
    corpus, source = completed
    output = tmp_path / "out"
    shutil.copytree(source, output)
    rows = read_review_rows(output / "review-manifest.jsonl")
    strata: dict[tuple[str, str, int], int] = {}
    for row in rows:
        key = (row["component"], row["detector"], row["coarse_bin"])
        strata[key] = strata.get(key, 0) + 1
    assert max(strata.values()) > 1
    real = receipt.implementation_constants
    monkeypatch.setattr(
        receipt, "implementation_constants", lambda: {**real(), "review_per_stratum": 1}
    )
    files = len(json.loads(corpus.read_bytes())["files"])
    for ordinal in range(files):
        path = scan.unit_path(output, ordinal)
        unit = json.loads(zlib.decompress(path.read_bytes()))
        unit["producer_envelope"]["review_per_stratum"] = 1
        del unit["digest"]
        path.write_bytes(scan.encode_unit(unit))

    def forge(body: dict[str, Any]) -> None:
        body["envelope"]["review_per_stratum"] = 1
        body["producer_envelopes"] = [dict(body["envelope"])]
        body["execution"]["output_bytes_before_receipt"] = (
            (output / scan.BINDING_FILE).stat().st_size
            + sum(scan.unit_path(output, n).stat().st_size for n in range(files))
            + sum(entry["bytes"] for entry in body["artifacts"].values())
        )

    rewrite(output, forge)
    with pytest.raises(QualityError, match="per stratum"):
        verify_report(corpus, output)


def _rewrite_unit(output: Path, change: Callable[[dict[str, Any]], None]) -> None:
    path = scan.unit_path(output, 0)
    body = json.loads(zlib.decompress(path.read_bytes()))
    change(body)
    del body["digest"]
    path.write_bytes(scan.encode_unit(body))


UNIT_ATTACKS: dict[str, Callable[[dict[str, Any]], None]] = {
    "largest_row_above_producer_ceiling": lambda u: u.update(
        max_line_bytes=u["producer_envelope"]["max_document_bytes"] + 1
    ),
    "largest_row_below_largest_text": lambda u: u.update(max_line_bytes=1),
    "producer_rss_above_ceiling": lambda u: u["producer_facts"].update(
        peak_process_tree_rss_bytes=u["producer_envelope"]["max_rss_bytes"] + 1
    ),
    "producer_elapsed_above_deadline": lambda u: u["producer_facts"].update(
        elapsed_seconds=u["producer_envelope"]["deadline_seconds"] + 1.0
    ),
    "producer_free_below_reserve": lambda u: u["producer_envelope"].update(
        free_reserve_bytes=u["producer_facts"]["min_observed_free_bytes"] + 1
    ),
    "producer_facts_missing": lambda u: u.pop("producer_facts"),
    "producer_facts_bool": lambda u: u["producer_facts"].update(peak_process_tree_rss_bytes=True),
}


@pytest.mark.parametrize("name", sorted(UNIT_ATTACKS))
def test_i10_unit_facts_contradicting_their_producer_refuse(
    completed: tuple[Path, Path], tmp_path: Path, name: str
) -> None:
    corpus, source = completed
    output = tmp_path / "out"
    shutil.copytree(source, output)
    _rewrite_unit(output, UNIT_ATTACKS[name])
    with pytest.raises(QualityError):
        verify_report(corpus, output)
