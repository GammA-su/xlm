"""Bounded parallel protected preparation: equivalence, ceilings, failure, progress.

Authored synthetic material only (tests/c05_parallel_fixture.py); no real
benchmark payload, network or protected root is used.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import time
from pathlib import Path
from typing import Any

import psutil
import pytest

from c05_parallel_fixture import WORDS, synthetic_material
from xlm.data.evidence_v2 import canonical
from xlm.data.exclusion import protected
from xlm.data.exclusion.operator import main
from xlm.data.exclusion.policy import C05Error, MatcherPolicy, Resources
from xlm.data.exclusion.prepare_workers import ProcessTree, Progress, Task, run_tasks
from xlm.data.exclusion.protected import INCOMPLETE_MARKER, MaterialSpec, build
from xlm.data.exclusion.runner import file_sha

KEY = b"authored-preparation-key-not-an-operator-key"
# Produced by the pre-parallel serial implementation (HEAD 6863bc0) on the same
# authored 60-row fixture: the refactor must reproduce it byte for byte.
GOLDEN_INDEX_SHA256 = "8c7ba6bfd24e0dc910f53c6f396e4065f691986dd27e6a4499dc2832a859bd44"
GOLDEN_ENVELOPE_DIGEST = "4a8417ddd6a0f593c9ca27a1602e6cc9635a7b6503f774b5455cf1b567cce4c9"
GOLDEN_COUNTS = {
    "items": 480,
    "duplicate_items": 50,
    "variants": 4800,
    "patterns": 5266,
    "items_without_patterns": 0,
}


def run(
    spec: MaterialSpec, root: Path, destination: Path, workers: int, **overrides: Any
) -> dict[str, Any]:
    progress = overrides.pop("progress", None)
    return build(
        spec,
        root,
        destination,
        policy=MatcherPolicy(),
        resources=Resources(free_bytes=0, workers=workers, **overrides),
        issuer="fixture",
        key=KEY,
        code_commit="4" * 40,
        code_identity="5" * 64,
        dependency_sha256="6" * 64,
        progress=progress,
    )


def assert_no_children() -> None:
    deadline = time.monotonic() + 15
    while psutil.Process().children(recursive=True) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert psutil.Process().children(recursive=True) == []


def assert_failed_closed(destination: Path) -> None:
    assert not (destination / "benchmark-preparation.receipt.json").exists()
    assert (destination / INCOMPLETE_MARKER).exists()
    assert_no_children()


@pytest.fixture(scope="module")
def material(tmp_path_factory: pytest.TempPathFactory) -> tuple[MaterialSpec, Path]:
    root = tmp_path_factory.mktemp("c05-parallel") / "material"
    return synthetic_material(root, 60), root


def test_worker_counts_reproduce_serial_golden_bytes(
    material: tuple[MaterialSpec, Path], tmp_path: Path
) -> None:
    spec, root = material
    outputs = {}
    for workers in (1, 2, 4):
        destination = tmp_path / f"w{workers}"
        envelope = run(spec, root, destination, workers)
        assert {k: envelope["payload"][k] for k in GOLDEN_COUNTS} == GOLDEN_COUNTS
        assert canonical.digest(envelope) == GOLDEN_ENVELOPE_DIGEST
        assert not (destination / INCOMPLETE_MARKER).exists()
        outputs[workers] = (
            (destination / "index.jsonl").read_bytes(),
            (destination / "benchmark-preparation.receipt.json").read_bytes(),
        )
    assert hashlib.sha256(outputs[1][0]).hexdigest() == GOLDEN_INDEX_SHA256
    assert outputs[1] == outputs[2] == outputs[4]
    assert_no_children()


def test_sixteen_workers_are_honored(material: tuple[MaterialSpec, Path], tmp_path: Path) -> None:
    assert Resources(workers=16).workers == 16
    spec, root = material
    seen: list[int] = []

    class Spy(ProcessTree):
        def rss(self) -> int:
            value = super().rss()
            seen.append(len(self.known))
            return value

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(protected, "ProcessTree", Spy)
        envelope = run(spec, root, tmp_path / "w16", 16)
    assert canonical.digest(envelope) == GOLDEN_ENVELOPE_DIGEST
    # 4 JSONL files + 4 x 9 Parquet row groups = 40 tasks, so all 16 spawn.
    assert max(seen) >= 16
    assert_no_children()


@pytest.mark.parametrize("value", [0, 17, -1, 1.5, None])
def test_unreasonable_worker_counts_are_rejected(value: Any) -> None:
    with pytest.raises(ValueError):
        Resources.model_validate({"workers": value})
    assert Resources().workers == 1


def test_workers_run_concurrently_and_each_reports(material: tuple[MaterialSpec, Path]) -> None:
    from xlm.data.exclusion.prepare_workers import plan_tasks

    spec, root = material
    tasks = plan_tasks([root / f.path for f in spec.files], spec.files, spec.max_record_bytes)
    events = list(
        run_tasks(
            tasks,
            workers=3,
            policy=MatcherPolicy(),
            max_record=spec.max_record_bytes,
            check=lambda: None,
            # All three must be alive and waiting together before any task starts.
            barrier=True,
        )
    )
    ready = {e[1] for e in events if e[0] == "ready"}
    done = [e for e in events if e[0] == "done"]
    assert len(ready) == 3 and psutil.Process().pid not in ready
    assert sorted(e[1] for e in done) == list(range(len(tasks)))
    assert len({e[3] for e in done}) >= 2
    assert sum(e[2] for e in done) == sum(f.items for f in spec.files)
    assert_no_children()


@pytest.mark.parametrize("workers", [1, 2])
def test_changed_file_during_processing_is_refused(tmp_path: Path, workers: int) -> None:
    root = tmp_path / "material"
    spec = synthetic_material(root, 30)
    target = root / spec.files[-2].path  # a JSONL file processed after the first
    raw = target.read_bytes()
    word = next(w for w in WORDS if w.encode() in raw).encode()

    def mutate(phase: str, **_: Any) -> None:
        if phase == "process" and target.read_bytes() == raw:
            # Same length, still valid JSON: only the identity re-check can catch it.
            target.write_bytes(raw.replace(word, word.upper(), 1))

    with pytest.raises(C05Error, match="changed during preparation"):
        run(spec, root, tmp_path / "out", workers, progress=mutate)
    assert_failed_closed(tmp_path / "out")


@pytest.mark.parametrize("workers", [1, 2])
def test_malformed_row_in_any_worker_fails_the_build(tmp_path: Path, workers: int) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    root = tmp_path / "material"
    spec = synthetic_material(root, 30)
    entry = spec.files[3]
    path = root / entry.path
    rows = pq.read_table(path).to_pylist()
    rows[17]["ctx_a"] = "   "
    pq.write_table(pa.Table.from_pylist(rows), path, row_group_size=7)
    replacement = entry.model_copy(update={"sha256": file_sha(path), "bytes": path.stat().st_size})
    spec = spec.model_copy(update={"files": (*spec.files[:3], replacement, *spec.files[4:])})
    with pytest.raises(C05Error, match="benchmark text field absent or invalid"):
        run(spec, root, tmp_path / "out", workers)
    assert_failed_closed(tmp_path / "out")


def test_worker_exception_fails_and_terminates_children(tmp_path: Path) -> None:
    entry = {"task": "piqa"}
    tasks = [Task(n, 0, str(tmp_path / f"absent-{n}.jsonl"), entry, None, 1, 5) for n in range(4)]
    with pytest.raises(C05Error, match="worker failed: FileNotFoundError"):
        list(
            run_tasks(tasks, workers=2, policy=MatcherPolicy(), max_record=1024, check=lambda: None)
        )
    assert_no_children()


def test_pattern_and_record_ceilings_are_global(
    material: tuple[MaterialSpec, Path], tmp_path: Path
) -> None:
    spec, root = material
    # Far above any single batch, below the global total generated across workers.
    with pytest.raises(C05Error, match="pattern generation ceiling"):
        run(spec, root, tmp_path / "patterns", 4, benchmark_patterns=5000)
    assert_failed_closed(tmp_path / "patterns")
    with pytest.raises(C05Error, match="aggregate ceiling"):
        run(spec, root, tmp_path / "records", 4, records=479)


# Measured timing: worker spawn must fit the authored 2 s deadline, so it runs
# in the exclusive -n 0 selection, never beside other multiprocessing tests.
@pytest.mark.serial
def test_deadline_is_enforced_while_workers_run(
    material: tuple[MaterialSpec, Path], tmp_path: Path
) -> None:
    spec, root = material
    stalled: list[bool] = []

    def stall(phase: str, rows: int = 0, **_: Any) -> None:
        if phase == "process" and rows and not stalled:
            stalled.append(bool(psutil.Process().children(recursive=True)))
            time.sleep(2.5)

    with pytest.raises(C05Error, match="deadline"):
        run(spec, root, tmp_path / "out", 2, stage_seconds=2.0, progress=stall)
    assert stalled == [True]  # the refusal happened with live workers
    assert_failed_closed(tmp_path / "out")


def test_ram_ceiling_counts_children_and_disk_ceiling_is_checked(
    material: tuple[MaterialSpec, Path], tmp_path: Path
) -> None:
    spec, root = material
    samples: list[tuple[int, int, int]] = []

    class Spy(ProcessTree):
        def rss(self) -> int:
            value = super().rss()
            samples.append((len(self.known), self.parent.memory_info().rss, value))
            return value

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(protected, "ProcessTree", Spy)
        run(spec, root, tmp_path / "ok", 2)
    assert any(known >= 2 and total > parent for known, parent, total in samples)
    with pytest.raises(C05Error, match="RAM ceiling"):
        run(spec, root, tmp_path / "ram", 2, ram_bytes=1)
    assert_failed_closed(tmp_path / "ram")
    with pytest.raises(C05Error, match="aggregate disk ceiling"):
        run(spec, root, tmp_path / "disk", 2, scratch_bytes=1)
    assert_failed_closed(tmp_path / "disk")


def test_interrupt_terminates_workers_and_publishes_nothing(
    material: tuple[MaterialSpec, Path], tmp_path: Path
) -> None:
    spec, root = material

    def interrupt(phase: str, rows: int = 0, **_: Any) -> None:
        if phase == "process" and rows:
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        run(spec, root, tmp_path / "out", 2, progress=interrupt)
    assert_failed_closed(tmp_path / "out")
    assert not (tmp_path / "out" / "index.jsonl").exists()


def test_progress_is_rate_limited_and_forced_at_milestones() -> None:
    now = [0.0]
    stream = io.StringIO()
    progress = Progress(
        files=2, rows=100, workers=4, interval=1.0, stream=stream, clock=lambda: now[0]
    )
    progress.update("process", force=True)
    for rows in range(1, 50):
        now[0] += 0.01
        progress.update("process", rows=rows)
    assert progress.lines == 1
    now[0] += 1.0
    progress.update("process", rows=60)
    progress.update("process", files=1, rows=61, force=True)
    assert progress.lines == 3
    with pytest.raises(C05Error):
        Progress(files=1, rows=1, workers=1, interval=0)


def test_progress_is_content_free(material: tuple[MaterialSpec, Path], tmp_path: Path) -> None:
    spec, root = material
    stream = io.StringIO()
    progress = Progress(files=8, rows=480, workers=2, interval=3600, stream=stream)
    run(spec, root, tmp_path / "out", 2, progress=progress.update)
    text = stream.getvalue()
    # verify + process start + 8 file completions + index + done; nothing in between.
    assert progress.lines == 12 == len(text.splitlines())
    assert "files 8/8 | rows 480/480 (100.0%)" in text
    lowered = text.lower()
    assert not any(word in lowered for word in WORDS)
    assert not re.search(r"[0-9a-f]{16}", lowered)
    index = (tmp_path / "out" / "index.jsonl").read_bytes().splitlines()[0]
    assert json.loads(index)["provenance"][0].split(":")[0] not in text


@pytest.mark.parametrize("quiet", [False, True])
def test_cli_progress_on_stderr_and_result_on_stdout(
    material: tuple[MaterialSpec, Path],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    quiet: bool,
) -> None:
    spec, root = material
    files = {
        "spec": spec.model_dump(mode="json"),
        "policy": MatcherPolicy().model_dump(mode="json"),
        "resources": Resources(free_bytes=0, workers=2).model_dump(mode="json"),
    }
    for name, value in files.items():
        canonical.write_canonical_json(tmp_path / f"{name}.json", value)
    monkeypatch.setenv("C05_FIXTURE_KEY", KEY.decode())
    code = main(
        [
            "build-local",
            *("--spec", str(tmp_path / "spec.json"), "--material-root", str(root)),
            *("--output", str(tmp_path / "out"), "--policy", str(tmp_path / "policy.json")),
            *("--resources", str(tmp_path / "resources.json"), "--issuer", "fixture"),
            *("--key-env", "C05_FIXTURE_KEY", "--code-commit", "4" * 40),
            *("--code-identity", "5" * 64, "--dependency-sha256", "6" * 64),
            *(["--no-progress"] if quiet else ["--progress-interval", "0.5"]),
        ]
    )
    captured = capsys.readouterr()
    assert code == 0
    assert json.loads(captured.out) == {"prepared": True, "mode": "authored"}
    assert captured.out.count("\n") == 1
    if quiet:
        assert captured.err == ""
    else:
        assert captured.err.startswith("[C05 prepare] verify")
        assert "[C05 prepare] done | files 8/8" in captured.err
        assert not any(word in captured.err.lower() for word in WORDS)
