"""Batch-1 Windows failure: live-progress publication race, root/cancel reporting, restart.

Offline authored fixtures and a loopback server only. The Windows cases use the
real OS semantics of this machine (a replace onto a file another handle holds
open raises WinError 5); they are skipped, never passed, elsewhere.
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import json
import os
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from test_essential_web_fast import (  # noqa: F401
    REVISION,
    SCIENCE,
    World,
    adapt_local,
    parquet_bytes,
    rows_of,
    served,
    transfer_limits,
)
from test_essential_web_fast import fast_tool as fast_tool
from test_essential_web_fast import world as world
from xlm.data.acquisition import source_parquet as sp
from xlm.data.evidence_v2 import canonical
from xlm.data.sources import essential_web_local as local
from xlm.data.sources import essential_web_recovery as recovery
from xlm.data.sources.essential_web_monitor import (
    LOCAL,
    NETWORK,
    RESUMABLE,
    Monitor,
    ObservedScratch,
    restart_class,
)
from xlm.data.sources.essential_web_progress import Dashboard, Snapshot

windows = pytest.mark.skipif(sys.platform != "win32", reason="needs Windows replace semantics")


def publish_many(path: str, count: int) -> tuple[int, int]:
    """Worker-process side of the coordinated protocol, as fast as possible."""
    published = deferred = 0
    for index in range(count):
        if local.publish_progress(Path(path), {"i": index}):
            published += 1
        else:
            deferred += 1
    return published, deferred


def replace_many(path: str, count: int) -> list[int | None]:
    """Worker-process side of the pre-fix protocol: a bare atomic replace."""
    failures = []
    target = Path(path)
    for index in range(count):
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps({"i": index}), encoding="utf-8")
        try:
            os.replace(temporary, target)
        except PermissionError as exc:
            failures.append(getattr(exc, "winerror", None))
    return failures


def race(path: Path, worker: Any, read: Any, count: int) -> tuple[Any, list[str]]:
    """Run ``worker`` in another process while this one reads ``path`` continuously."""
    errors: list[str] = []
    with concurrent.futures.ProcessPoolExecutor(1) as pool:
        future = pool.submit(worker, str(path), count)
        while not future.done():
            try:
                read(path)
            except OSError as exc:
                errors.append(f"{type(exc).__name__}:{getattr(exc, 'winerror', None)}")
        return future.result(), errors


# ------------------------------------------------------------- the root case


@windows
def test_unsynchronized_replace_reproduces_the_batch1_permission_error(tmp_path: Path) -> None:
    """The pre-fix protocol, across processes: WinError 5 and a stranded ``.tmp``."""
    target = tmp_path / "f00035.progress.json"
    target.write_text("{}", encoding="utf-8")
    with target.open("rb"):
        failures = replace_many(str(target), 1)
    assert failures == [5]
    assert (tmp_path / "f00035.progress.tmp").exists()  # the forensic signature left in staging
    failures, reader_errors = race(target, replace_many, lambda p: p.read_bytes(), 2000)
    assert failures and set(failures) == {5}


@windows
def test_worker_failure_reports_the_exact_windows_operation(tmp_path: Path) -> None:
    """An unsynchronized foreign handle still fails closed, and the report names the replace."""
    target = tmp_path / "f00035.progress.json"
    local.publish_progress(target, {"rows": 1})
    with target.open("rb"), concurrent.futures.ProcessPoolExecutor(1) as pool:
        future = pool.submit(local.publish_progress, target, {"rows": 2})
        with pytest.raises(PermissionError) as caught:
            future.result()
    detail = local.failure_detail(caught.value)
    assert detail["exception"] == "PermissionError"
    assert (detail["errno"], detail["winerror"]) == (13, 5)
    assert (detail["path"], detail["path2"]) == ("f00035.progress.tmp", "f00035.progress.json")
    assert detail["site"].startswith("essential_web_local.py:")
    assert detail["site"].endswith("in publish_progress (worker process)")
    assert "access denied" in detail["reason"]
    # Only code-authored facts: never the exception message.
    assert str(caught.value) not in json.dumps(detail)


@windows
def test_coordinated_protocol_never_fails_under_a_continuous_reader(tmp_path: Path) -> None:
    target = tmp_path / "f00035.progress.json"
    local.publish_progress(target, {"i": -1})
    seen: list[int] = []

    def read(path: Path) -> None:
        value = local.read_progress(path)
        if value is not None:
            seen.append(int(value["i"]))

    (published, deferred), errors = race(target, publish_many, read, 2000)
    assert errors == []
    assert published + deferred == 2000 and published > 0
    assert seen and seen == sorted(seen)  # never a torn or reordered snapshot
    assert local.read_progress(target) is not None


# ------------------------------------------------------- deterministic rules


def test_publish_defers_and_read_skips_while_the_other_side_holds_the_snapshot(
    tmp_path: Path,
) -> None:
    target = tmp_path / "f00001.progress.json"
    assert local.read_progress(target) is None
    assert local.publish_progress(target, {"rows": 1})
    lock = local._progress_lock(target)
    lock.acquire()
    try:
        assert local.publish_progress(target, {"rows": 2}) is False  # deferred, never raised
        assert local.read_progress(target) is None  # skipped, never raised
    finally:
        lock.release()
    assert local.read_progress(target) == {"rows": 1}
    assert local.publish_progress(target, {"rows": 3})
    assert local.read_progress(target) == {"rows": 3}


def test_deferred_snapshot_goes_out_with_a_later_row_and_outputs_are_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.parquet"
    source.write_bytes(parquet_bytes(rows_of(10), 5))
    plain = adapt_local(source, tmp_path / "plain", "data/a.parquet", '"e"')
    real = local.publish_progress
    calls: list[int] = []

    def contended(path: Path, value: dict[str, Any]) -> bool:
        calls.append(int(value["rows"]))
        return False if len(calls) == 1 else real(path, value)

    monkeypatch.setattr(local, "publish_progress", contended)
    progress = tmp_path / "staging" / "f00000.progress.json"
    observed = adapt_local(
        source, tmp_path / "observed", "data/a.parquet", '"e"', progress_path=progress
    )
    assert calls[:2] == [0, 1]  # retried on the very next row, with no wait
    final = local.read_progress(progress)
    assert final is not None and final["rows"] == final["total"] == 10
    for view in plain["views"]:
        for key in ("documents_sha256", "rejections_sha256", "adaptation_summary_sha256"):
            assert observed["views"][view][key] == plain["views"][view][key]
    source.unlink()  # the worker released its source handle before returning
    assert not source.exists()


def test_monitor_never_opens_state_of_a_stream_started_in_this_run(tmp_path: Path) -> None:
    scratch = ObservedScratch(tmp_path / "scratch", 1000, 0)
    unit = local.Unit("f00033", "a", "http://x", tmp_path / "a.part", tmp_path / "a.json", None)
    unit.state.write_text('{"length": 7}', encoding="utf-8")
    stub = SimpleNamespace(scratch=scratch, lengths={})
    Monitor._length(stub, unit)  # type: ignore[arg-type]
    assert stub.lengths == {"f00033": 7}  # no stream yet: the file has no writer
    stub.lengths.clear()
    assert scratch.reserve("f00033", 100, unit.partial)
    unit.state.write_text("not json: a live stream is replacing it", encoding="utf-8")
    Monitor._length(stub, unit)  # type: ignore[arg-type]
    assert stub.lengths == {}
    scratch.shrink("f00033", 42)
    Monitor._length(stub, unit)  # type: ignore[arg-type]
    assert stub.lengths == {"f00033": 42}


def test_dashboard_shows_cancellations_apart_from_failures(tmp_path: Path) -> None:
    import io

    stream = io.StringIO()
    dashboard = Dashboard(stream, tmp_path / "events.jsonl", now=0.0)
    snapshot = Snapshot(1, "c" * 64, "RUN", 32, 10, failed=1, cancelled=8)
    dashboard.update(snapshot, now=1.0, force=True)
    assert "FILES 10/32 31.2% remaining=22" in stream.getvalue()
    assert "failed units=1 cancelled=8" in stream.getvalue()


# -------------------------------------------------------------- the pipeline


def test_root_failure_cancels_streams_which_are_not_reported_as_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    started = threading.Barrier(4)

    def stream(url: str, partial: Path, state: Path, **options: Any) -> Any:
        started.wait(timeout=10)
        if not options["cancel"].wait(timeout=10):
            raise AssertionError("stream was never cancelled")
        raise sp.TransferCancelledError(f"'{options['name']}' cancelled after a sibling failure")

    def process(job: Any) -> dict[str, Any]:
        started.wait(timeout=10)
        raise PermissionError(13, "Access is denied", "f00035.progress.tmp", 5, "f.progress.json")

    monkeypatch.setattr(local, "download_source", stream)
    units = [
        local.Unit(
            f"f0003{i}", f"d/{i}", "http://x", tmp_path / f"{i}.part", tmp_path / f"{i}.s", {}
        )
        for i in (3, 4, 6)
    ]
    units.append(
        local.Unit("f00035", "d/5", None, tmp_path / "5.part", tmp_path / "5.s", {}, {"length": 1})
    )
    events: list[dict[str, Any]] = []
    with pytest.raises(PermissionError):
        local.run_pipeline(
            units,
            limits=transfer_limits(),
            revision=REVISION,
            download_workers=3,
            process_workers=0,
            scratch=sp.ScratchBudget(tmp_path / "scratch", 64 * 1024**2, 0),
            meter=None,
            deadline_seconds=60,
            identity_for=lambda unit, transfer: {},
            on_done=lambda *args: None,
            process=process,
            on_progress=lambda status: events.append(status),
        )
    failed = [e for e in events if "failed" in e]
    assert failed == [
        {
            "failed": "f00035",
            "root": True,
            "exception": "PermissionError",
            "errno": 13,
            "winerror": 5 if sys.platform == "win32" else None,
            "reason": local._WINDOWS_ERRORS[5] if sys.platform == "win32" else None,
            "path": "f00035.progress.tmp",
            "path2": "f.progress.json",
            "site": failed[0]["site"],
        }
    ]
    assert sorted(e["cancelled"] for e in events if "cancelled" in e) == [
        "f00033",
        "f00034",
        "f00036",
    ]


def test_cancellation_without_a_root_failure_is_still_a_failure(tmp_path: Path) -> None:
    events: list[dict[str, Any]] = []
    unit = local.Unit("f00001", "d/1", None, tmp_path / "p", tmp_path / "s", {}, {})

    def process(job: Any) -> dict[str, Any]:
        raise sp.TransferCancelledError("stray")

    with pytest.raises(sp.TransferCancelledError):
        local.run_pipeline(
            [unit],
            limits=transfer_limits(),
            revision=REVISION,
            download_workers=1,
            process_workers=0,
            scratch=sp.ScratchBudget(tmp_path / "scratch", 1024, 0),
            meter=None,
            deadline_seconds=60,
            identity_for=lambda unit, transfer: {},
            on_done=lambda *args: None,
            process=process,
            on_progress=events.append,
        )
    assert [e["failed"] for e in events if "failed" in e] == ["f00001"]
    assert not [e for e in events if "cancelled" in e]


# ---------------------------------------------------------- campaign restart


def seed_retained(world: World, name: str) -> None:
    durable = world.root / "acq-raw/ew-fast/source" / name
    durable.parent.mkdir(parents=True, exist_ok=True)
    seed = world.scratch / "seed.part"
    seed.parent.mkdir(parents=True, exist_ok=True)
    seed.write_bytes(world.state.files[name])
    sha256, size = sp.file_sha256(seed)
    identity = sp.SourceIdentity('"opaque"', size, sha256)
    sp.promote_source(
        seed,
        durable,
        sp.identity_record(
            identity,
            source_file=name,
            repository=world.config["binding"]["repository"],
            revision=REVISION,
        ),
    )
    seed.unlink()


@windows
def test_batch_failure_reports_root_cancellation_and_resumes_without_redoing_work(
    world: World, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The Batch-1 shape end to end: a foreign handle breaks one progress replace."""
    world.pass_benchmark()
    world.authorize(0)
    first, second = world.campaign().members(0)
    seed_retained(world, second)  # f00001 processes locally while f00000 streams
    world.state.delay = 0.5  # keep f00000 in flight when f00001 fails
    real = local.publish_progress

    def foreign_reader(path: Path, value: dict[str, Any]) -> bool:
        if path.name == "f00001.progress.json" and path.exists():
            with path.open("rb"):  # an unsynchronized handle, as the old monitor held
                return real(path, value)
        return real(path, value)

    monkeypatch.setattr(local, "publish_progress", foreign_reader)
    assert world.run("run", "--batch", "0", "--workers", "1") == 1
    out = capsys.readouterr().out
    for line in (
        "ROOT FAILURE",
        "  f00001 PermissionError errno=13 winerror=5",
        "  operation: replace f00001.progress.tmp -> f00001.progress.json",
        "CANCELLED",
        "  1 in-flight units cancelled because of the root failure: f00000",
        "PRESERVED",
        "  0 / 2 sealed (0.0%); 0 sealed in this run",
        "  2 remaining",
        "  1 reusable locally (retained source or complete scratch)",
        "  1 require a fresh download",
    ):
        assert line in out.splitlines(), line
    assert "in publish_progress" in out
    events = [
        json.loads(line)
        for line in (world.root / "plans/ew-fast/b0000/events.jsonl").read_text().splitlines()
    ]
    kinds = [e["event"] for e in events]
    assert kinds.count("failed") == 1 and kinds.count("cancelled") == 1
    fatal = next(e for e in events if e["event"] == "fatal")
    assert fatal["root"]["key"] == "f00001" and fatal["root"]["winerror"] == 5
    assert fatal["cancelled"] == ["f00000"] and fatal["other_failures"] == []
    assert fatal["restart"][LOCAL] == ["f00001"] and fatal["restart"][NETWORK] == ["f00000"]
    assert not world.receipts()
    # The restart reuses the retained source and never re-requests it.
    monkeypatch.setattr(local, "publish_progress", real)
    world.state.delay = 0.0
    before = world.state.hits("object", second)
    assert world.run("run", "--batch", "0", "--workers", "1") == 0
    assert world.state.hits("object", second) == before == 0
    assert sorted(world.receipts()) == ["f00000", "f00001"] and not world.scratch_files()
    sealed = {k: v["digest"] for k, v in world.receipts().items()}
    assert world.run("run", "--batch", "0") == 4  # nothing is redone
    assert {k: v["digest"] for k, v in world.receipts().items()} == sealed


def test_restart_plan_reproduces_the_batch1_classes(tmp_path: Path, fast_tool: Any) -> None:
    """22 unsealed units as Batch 1 left them: the network work a restart needs, verified."""
    tool = fast_tool
    body = b"PAR1" + bytes(range(256)) * 64 + b"PAR1"
    units: list[local.Unit] = [
        local.Unit("f00035", "d/35", None, tmp_path / "35.part", tmp_path / "35.s", {})
    ]

    def unit(key: str) -> local.Unit:
        return local.Unit(
            key, f"d/{key}", "http://x", tmp_path / f"{key}.part", tmp_path / f"{key}.s", {}
        )

    for index, key in enumerate(("f00033", "f00034", "f00040", "f00044", "f00046")):
        u = unit(key)
        verified = 1024 * (index + 1)
        u.partial.write_bytes(body[: verified + 100])  # an unverified tail is truncated later
        u.state.write_text(
            json.dumps(
                {
                    "length": len(body),
                    "verified_bytes": verified,
                    "prefix_sha256": hashlib.sha256(body[:verified]).hexdigest(),
                    "complete": False,
                }
            )
        )
        units.append(u)
    for key in ("f00048", "f00049", "f00050"):
        u = unit(key)
        u.partial.write_bytes(body[:512])
        u.state.write_text(json.dumps({"length": len(body), "verified_bytes": 0}))
        units.append(u)
    units += [unit(f"f{rank:05d}") for rank in range(51, 64)]
    plan = tool.restart_plan(units, 4096)
    assert (plan["local_complete_reuse"], plan["partial_resume"], plan["fresh_download"]) == (
        1,
        5,
        16,
    )
    assert plan["resumable_verified_bytes"] == 1024 * 15
    assert plan["known_network_bytes"] == 5 * len(body) - 1024 * 15 + 3 * len(body)
    assert plan["unknown_length_units"] == 13
    assert plan["worst_case_network_bytes"] == plan["known_network_bytes"] + 13 * 4096
    # A checkpoint that no longer reproduces its prefix hash is a fresh download, never reused.
    units[1].partial.write_bytes(b"X" + body[1 : 1024 + 100])
    assert tool.restart_plan(units, 4096)["partial_resume"] == 4
    # A complete scratch file is reused only when it rehashes to its checkpoint.
    done = unit("f00099")
    done.partial.write_bytes(body)
    good = {"complete": True, "length": len(body), "sha256": hashlib.sha256(body).hexdigest()}
    done.state.write_text(json.dumps(good))
    assert tool.restart_plan([done], 4096)["local_complete_reuse"] == 1
    done.state.write_text(json.dumps({**good, "sha256": "0" * 64}))
    assert tool.restart_plan([done], 4096)["fresh_download"] == 1


@pytest.mark.parametrize(
    "state,durable,expected",
    [
        (None, True, LOCAL),
        ({"complete": True}, False, LOCAL),
        ({"verified_bytes": 64}, False, RESUMABLE),
        ({"verified_bytes": 0, "length": 9}, False, NETWORK),
        (None, False, NETWORK),
    ],
)
def test_restart_class(state: dict[str, Any] | None, durable: bool, expected: str) -> None:
    assert restart_class(state, durable) == expected


def test_resume_check_reports_the_restart_plan_and_schedules_no_sealed_unit(
    world: World, capsys: pytest.CaptureFixture[str]
) -> None:
    world.pass_benchmark()
    world.authorize(0)
    first, second = world.campaign().members(0)
    world.state.drops[second] = [1000, 1000, 1000]  # the second stream fails after 1000 bytes
    assert world.run("run", "--batch", "0", "--workers", "1") == 1
    capsys.readouterr()
    assert world.run("resume-check", "--batch", "0") == 0
    report = json.loads(capsys.readouterr().out)
    assert report["sealed"] == 1 and report["already_sealed_scheduled"] == 0
    assert report["scheduled_keys"] == ["f00001"]
    restart = report["restart"]
    assert restart["sealed_skip"] == 1 and restart["fresh_download"] == 1
    assert restart["charged_bytes"] > 0 and restart["transfer_ceiling_bytes"] > 0


# ------------------------------------------------------ campaign code identity


def chain(tmp_path: Path, records: list[dict[str, Any]]) -> None:
    for (relative, kind, _), record in zip(recovery.COMPATIBILITY, records, strict=False):
        value = {"kind": kind, **record}
        value["digest"] = canonical.digest(value)
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))


def test_windows_fix_extends_the_compatibility_chain_only_with_its_own_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    names = sorted(
        {name for _, _, allowed in recovery.COMPATIBILITY for name in allowed} | {"columns.py"}
    )
    old = dict.fromkeys(names, "old")
    scope = {**old, **dict.fromkeys(recovery.COMPATIBILITY[0][2], "scope")}
    fixed = {**scope, **dict.fromkeys(recovery.COMPATIBILITY[1][2], "windows")}
    manifest = {"code": old, "digest": "b" * 64, "campaign": "a" * 64}
    common = {"campaign": "a" * 64, "recovery_digest": "b" * 64}
    records = [
        {**common, "previous_code": old, "code": scope},
        {**common, "previous_code": scope, "code": fixed},
    ]
    monkeypatch.setattr(recovery, "code_identity", lambda _: fixed)
    assert not recovery.compatible_code(tmp_path, manifest)
    chain(tmp_path, records)
    assert recovery.compatible_code(tmp_path, manifest)
    # The historical states stay valid: the chain never rewrites an earlier record.
    monkeypatch.setattr(recovery, "code_identity", lambda _: scope)
    assert recovery.compatible_code(tmp_path, manifest)
    # Any further drift, a skipped link or a record touching another file is refused.
    monkeypatch.setattr(recovery, "code_identity", lambda _: {**fixed, "columns.py": "new"})
    assert not recovery.compatible_code(tmp_path, manifest)
    monkeypatch.setattr(recovery, "code_identity", lambda _: fixed)
    chain(tmp_path, [records[0], {**records[1], "previous_code": old}])
    assert not recovery.compatible_code(tmp_path, manifest)
    wider = {**fixed, "columns.py": "windows"}
    monkeypatch.setattr(recovery, "code_identity", lambda _: wider)
    chain(tmp_path, [records[0], {**records[1], "code": wider}])
    assert not recovery.compatible_code(tmp_path, manifest)
    monkeypatch.setattr(recovery, "code_identity", lambda _: fixed)
    chain(tmp_path, records)
    path = tmp_path / recovery.WINDOWS_FIX
    tampered = json.loads(path.read_bytes())
    tampered["authorization_basis"] = "widened"
    path.write_text(json.dumps(tampered))
    assert not recovery.compatible_code(tmp_path, manifest)


def test_committed_windows_fix_binds_the_running_code(fast_campaign: Any) -> None:
    """The real campaign loads only because the committed record binds this exact code."""
    manifest = json.loads((recovery_repo() / recovery.MANIFEST).read_bytes())
    assert recovery.compatible_code(recovery_repo(), manifest)
    record = json.loads((recovery_repo() / recovery.WINDOWS_FIX).read_bytes())
    assert record["code"] == recovery.code_identity(recovery_repo())
    assert (
        record["previous_code"]
        == json.loads((recovery_repo() / recovery.SCOPE_FIX).read_bytes())["code"]
    )
    assert fast_campaign["digest"] == record["campaign"] == manifest["campaign"]


def recovery_repo() -> Path:
    return Path(__file__).resolve().parents[1]


@pytest.fixture
def fast_campaign() -> dict[str, Any]:
    path = (
        recovery_repo() / "docs/implementation/evidence/ESSENTIAL-WEB-FAST-TRANSPORT/campaign.json"
    )
    value: dict[str, Any] = json.loads(path.read_bytes())
    return value
