"""Authored offline fixtures for the narrow recovery and live display."""

from __future__ import annotations

import io
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from test_essential_web_fast import (
    PROCESS_LIMITS,
    REVISION,
    VIEWS,
    World,
    parquet_bytes,
    rows_of,
    served,  # noqa: F401 -- shared authored fixtures
)
from test_essential_web_fast import world as world
from xlm.data.acquisition import source_parquet as sp
from xlm.data.acquisition.plan import load_acquisition_plan
from xlm.data.acquisition.records import RecordLimitError, encode_record
from xlm.data.evidence_v2 import canonical
from xlm.data.sources import essential_web_bulk as bulk
from xlm.data.sources import essential_web_local as local
from xlm.data.sources import essential_web_recovery as recovery
from xlm.data.sources.essential_web_progress import Dashboard, Rate, Snapshot, pipeline_eta


@pytest.mark.parametrize("size", [512, 11_494_172])
def test_record_exact_bound_and_one_byte_over(tmp_path: Path, size: int) -> None:
    record = {"text": "x" * (size - len(encode_record({"text": ""})))}
    assert len(encode_record(record)) == size
    import pyarrow as pa
    import pyarrow.parquet as pq

    path = tmp_path / "record.parquet"
    pq.write_table(pa.Table.from_pylist([record]), path)
    options: dict[str, Any] = dict(
        source_file=path.name,
        locator={},
        etag='"a"',
        columns=["text"],
        max_parser_bytes=32 * 1024**2,
        max_decoded_bytes=32 * 1024**2,
    )
    assert len(list(sp.selected_payloads(path, max_record_bytes=size, **options))) == 1
    with pytest.raises(RecordLimitError, match=f"encoded_bytes={size} limit={size - 1}"):
        list(sp.selected_payloads(path, max_record_bytes=size - 1, **options))


def test_large_bound_preserves_malformed_refusal(tmp_path: Path) -> None:
    from xlm.data.adapters.malformed import MalformedLimitError

    path = tmp_path / "bad.parquet"
    path.write_bytes(parquet_bytes(rows_of(10, malformed=frozenset({0, 1, 2})), 5))
    with pytest.raises(MalformedLimitError):
        local.adapt_source_file(
            path,
            tmp_path / "out",
            source_file=path.name,
            views=VIEWS,
            source_id="essential_web",
            repository="authored",
            revision=REVISION,
            plan_id="p",
            plan_hash="a" * 64,
            selection_hash="b" * 64,
            identity={"etag": '"a"', "sha256": "c" * 64, "length": path.stat().st_size},
            limits={**PROCESS_LIMITS, "max_record_bytes": 11_494_172},
        )


def test_large_valid_practical_record_keeps_canonical_semantics(tmp_path: Path) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    from test_essential_web_fast import expected_documents

    row = rows_of(5)[1]
    row["text"] = "Authored practical recovery fixture. " * 270_000
    size = len(encode_record(row))
    assert 8 * 1024**2 < size <= 11_494_172
    path = tmp_path / "large.parquet"
    pq.write_table(pa.Table.from_pylist([row]), path, compression=None)
    result = local.adapt_source_file(
        path,
        tmp_path / "out",
        source_file=path.name,
        views=VIEWS,
        source_id="essential_web",
        repository="authored",
        revision=REVISION,
        plan_id="p",
        plan_hash="a" * 64,
        selection_hash="b" * 64,
        identity={"etag": '"a"', "sha256": "c" * 64, "length": path.stat().st_size},
        limits={**PROCESS_LIMITS, "max_record_bytes": 11_494_172},
    )
    assert result["views"][VIEWS[1]]["documents"] == 1
    assert (tmp_path / "out" / VIEWS[1] / "documents.jsonl").read_bytes() == expected_documents(
        path.name, [row], VIEWS[1]
    )


def rate(value: int) -> Rate:
    result = Rate()
    for now in (0, 1, 2):
        result.update(now, now * value)
    return result


@pytest.mark.parametrize(
    "byte_work,row_work,expected", [(1000, 10, 100), (10, 1000, 100), (0, 400, 40), (0, 0, 0)]
)
def test_eta_critical_path(byte_work: int, row_work: int, expected: int) -> None:
    assert pipeline_eta(byte_work, row_work, rate(10), rate(10), 2) == pytest.approx(expected)


def test_eta_unknown_stalls_retry_and_completion() -> None:
    assert pipeline_eta(10, 10, Rate(), Rate(), 0) is None
    assert pipeline_eta(None, 10, rate(10), rate(10), 2) is None
    download, process = rate(10), rate(10)
    download.update(18, 20)
    assert pipeline_eta(100, 10, download, process, 18) is None
    assert pipeline_eta(0, 10, download, process, 18) is None  # stalled CPU
    download.update(19, 5)  # counter reset/retry never produces a negative rate
    assert download.value >= 0
    assert pipeline_eta(None, None, download, process, 20, complete=True) == 0
    # Repeated transfer bytes are throughput, never unique completion bytes.
    download.update(20, 100)
    process.update(20, 100)
    assert pipeline_eta(10, 10, download, process, 20) is not None


class TTY(io.StringIO):
    def isatty(self) -> bool:
        return True


@pytest.mark.parametrize(
    "sealed,percent", [(0, "0.0%"), (16, "50.0%"), (31, "96.9%"), (32, "100.0%")]
)
@pytest.mark.parametrize("tty", [True, False])
def test_dashboard_render_and_durable_events(
    tmp_path: Path, sealed: int, percent: str, tty: bool
) -> None:
    stream = TTY() if tty else io.StringIO()
    dashboard = Dashboard(stream, tmp_path / "events.jsonl", now=0)
    state = Snapshot(0, "a" * 64, "RESUME", 32, sealed)
    state.targets = {VIEWS[0]: 660_000_000, VIEWS[1]: 660_000_000, VIEWS[2]: 330_000_000}
    state.views = {
        v: {"documents": 2, "canonical_bytes": target * 2} for v, target in state.targets.items()
    }
    dashboard.update(state, now=0)
    before = stream.getvalue()
    assert percent in before and "/? B ?" in before
    assert before.count("50.0%") >= 3
    dashboard.update(state, now=1)
    assert (stream.getvalue() != before) == tty
    for kind in ("sealed", "retry", "warning", "failed", "resume"):
        dashboard.event(kind, now=2, key="f00026")
    dashboard.update(state, now=31)
    assert ("\x1b[" in stream.getvalue()) == tty
    events = [json.loads(line) for line in (tmp_path / "events.jsonl").read_text().splitlines()]
    assert [e["event"] for e in events] == ["sealed", "retry", "warning", "failed", "resume"]


def test_real_scheduler_31_of_32_offline_resume(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    world.authorize(0)
    tool = world.tool
    base = world.campaign()
    names = [f"data/authored-{i:05d}.parquet" for i in range(32)]
    config = json.loads(json.dumps(base.config))
    config["batch"]["files"] = 32
    config = tool.self_digest(config)
    campaign = replace(base, config=config, inventory={"files": [{"file": n} for n in names]})
    old = load_acquisition_plan(campaign.batch_dir(0) / "batch.plan.json")
    plan = old.model_copy(update={"selected_files": names}).with_computed_hash()
    assert old.authorization is not None
    plan = plan.model_copy(
        update={
            "authorization": old.authorization.model_copy(
                update={"authorization_hash": plan.plan_hash}
            )
        }
    )
    record = tool.self_digest(
        {
            **world.batch(0),
            "campaign": config["digest"],
            "files": names,
            "plan_hash": plan.plan_hash,
            "selection_hash": plan.compute_selection_hash(),
        }
    )
    tool.write_json(campaign.batch_dir(0) / "batch.json", record)
    source_bytes = parquet_bytes(rows_of(5), 5)
    for rank, name in enumerate(names):
        raw = campaign.raw_path(name)
        raw.parent.mkdir(parents=True, exist_ok=True)
        raw.write_bytes(source_bytes)
        digest, size = sp.file_sha256(raw)
        identity = sp.identity_record(
            sp.SourceIdentity('"a"', size, digest),
            source_file=name,
            repository=plan.repository,
            revision=plan.revision,
        )
        sp.promote_source(raw, raw, identity)
        if rank == 26:
            continue
        key = f"f{rank:05d}"
        job = tool.unit_job(campaign, plan, name, campaign.staging(0) / key)
        job.update(source_path=str(raw), durable_path=None, identity_record=identity, key=key)
        unit = local.Unit(
            key,
            name,
            None,
            world.scratch / f"{key}.part",
            world.scratch / f"{key}.json",
            job,
            identity,
        )
        result = local.process_unit(job)
        tool.seal_unit(campaign, plan, 0, rank, unit, None, result)
    prior = {
        p: (p.read_bytes(), p.stat().st_mtime_ns)
        for p in (world.root / "canonical/ew-fast/b0000").rglob("*")
        if p.is_file()
    }
    state = recovery.resume_state(campaign, record)
    assert (state["sealed"], state["scheduled"], state["already_sealed_scheduled"]) == (31, 1, 0)
    assert state["completion_percent"] == 96.875
    scratch = campaign.scratch("b0000")
    scratch.mkdir(parents=True)
    # Exact production failure shape: durable source AND complete scratch checkpoint.
    raw = campaign.raw_path(names[26])
    retained = sp.load_durable_source(raw)
    assert retained is not None
    (scratch / "f00026.parquet.part").write_bytes(source_bytes)
    tool.write_json(
        scratch / "f00026.state.json",
        {
            "name": names[26],
            "url": tool.source_url(plan, names[26]),
            "complete": True,
            "sha256": retained["sha256"],
            "length": len(source_bytes),
            "etag": '"a"',
            "charged_bytes": len(source_bytes),
            "requests": 2,
            "retries": 0,
        },
    )
    units, _, _ = tool.prepare_units(campaign, plan, record, scratch, state)
    assert [u.key for u in units] == ["f00026"] and units[0].url is None
    original = local.run_pipeline
    scheduled: list[str] = []

    def pipeline(units: Any, **kwargs: Any) -> Any:
        scheduled.extend(u.key for u in units)
        return original(units, **kwargs)

    def refuse(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("offline recovery attempted download")

    monkeypatch.setattr(local, "run_pipeline", pipeline)
    monkeypatch.setattr(local, "download_source", refuse)
    assert tool.execute_batch(campaign, plan, record, scratch, 1, 0, offline=True) == 3
    assert scheduled == ["f00026"]
    assert all((p.read_bytes(), p.stat().st_mtime_ns) == before for p, before in prior.items())
    assert recovery.resume_state(campaign, record)["scheduled"] == 0
    # Corruption of a sealed document refuses before scheduling anything.
    document = next(p for p in prior if p.name == "documents.jsonl")
    document.write_bytes(b"corrupt")
    with pytest.raises(bulk.BulkError, match="hash differs"):
        recovery.resume_state(campaign, record)


def test_recovery_identity_and_authorization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = {
        "digest": "a" * 64,
        "transport_code_sha256": {"old": "b" * 64},
        "limits": {"max_record_bytes": 8, "max_parser_bytes": 32},
    }
    monkeypatch.setattr(recovery, "code_identity", lambda _: {"new": "c" * 64})
    payload = {
        "kind": "essential_web_batch_recovery_v1",
        "campaign": config["digest"],
        "original_code": config["transport_code_sha256"],
        "code": {"new": "c" * 64},
        "max_record_bytes": 12,
    }
    payload["digest"] = canonical.digest(payload)
    path = tmp_path / recovery.MANIFEST
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(payload))
    assert recovery.load_manifest(tmp_path, config) == payload
    with pytest.raises(bulk.BulkError, match="operator authorization"):
        recovery.check_authorization(payload, tmp_path)
    auth = tmp_path / "recovery-authorization.json"
    auth.write_text(json.dumps({"digest": "wrong", "operator": "t"}))
    with pytest.raises(bulk.BulkError, match="does not match"):
        recovery.check_authorization(payload, tmp_path)
    auth.write_text(json.dumps({"digest": payload["digest"], "operator": "t"}))
    recovery.check_authorization(payload, tmp_path)
    monkeypatch.setattr(recovery, "code_identity", lambda _: {"new": "d" * 64})
    with pytest.raises(bulk.BulkError, match="code changed"):
        recovery.load_manifest(tmp_path, config)


def test_offline_run_refuses_missing_source_before_pipeline(world: World) -> None:
    world.authorize(0)
    world.pass_benchmark()
    assert world.run("run", "--batch", "0", "--offline") == 1
    assert world.state.requests == []
    assert world.receipts() == {}
