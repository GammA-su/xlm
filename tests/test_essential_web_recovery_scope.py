"""Offline authored scope/normal-bound regressions; no production acquisition."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from test_essential_web_fast import World, rows_of, served  # noqa: F401
from test_essential_web_fast import world as world
from xlm.data.acquisition import source_parquet as sp
from xlm.data.acquisition.plan import load_acquisition_plan
from xlm.data.acquisition.records import RecordLimitError, encode_record
from xlm.data.sources import essential_web_recovery as recovery
from xlm.data.sources.essential_web_bulk import BulkError

NORMAL = 8 * 1024**2
EXCEPTION = 11_494_172
FILE = "data/crawl=CC-MAIN-2016-50/train-02164-of-03132.parquet"


def scoped(world: World) -> tuple[Any, dict[str, Any]]:
    campaign = world.campaign()
    config = json.loads(json.dumps(campaign.config))
    config["batch"]["files"] = 32
    config["limits"]["max_record_bytes"] = NORMAL
    names = [f"data/authored-{i:05d}.parquet" for i in range(96)]
    names[26] = FILE
    amendment = {
        "campaign": config["digest"],
        "batch": 0,
        "file": FILE,
        "source_sha256": "a" * 64,
        "max_record_bytes": EXCEPTION,
        "digest": "b" * 64,
        "sealed_receipts": {},
    }
    campaign = replace(
        campaign,
        config=config,
        recovery=amendment,
        inventory={"files": [{"file": name} for name in names]},
    )
    source = {
        "source_file": FILE,
        "sha256": "a" * 64,
        "repository": config["binding"]["repository"],
        "revision": config["binding"]["revision"],
    }
    return campaign, source


@pytest.mark.parametrize(
    "batch,rank,path,sha,expected",
    [
        (0, 26, FILE, "a" * 64, EXCEPTION),
        (0, 25, FILE, "a" * 64, NORMAL),
        (0, 26, "data/wrong.parquet", "a" * 64, NORMAL),
        (0, 26, FILE, "c" * 64, NORMAL),
        (1, 26, FILE, "a" * 64, NORMAL),
        (2, 26, FILE, "a" * 64, NORMAL),
    ],
)
def test_exact_unit_path_hash_scope(
    world: World, batch: int, rank: int, path: str, sha: str, expected: int
) -> None:
    world.authorize(0)
    campaign, source = scoped(world)
    source.update(source_file=path, sha256=sha)
    plan = load_acquisition_plan(campaign.batch_dir(0) / "batch.plan.json")
    job = world.tool.unit_job(
        campaign, plan, path, campaign.staging(batch), batch=batch, rank=rank, source=source
    )
    assert job["limits"]["max_record_bytes"] == expected
    assert (recovery.lookup_matching_recovery(campaign, batch, rank, path, source) is not None) == (
        expected == EXCEPTION
    )
    # A path alone is never sufficient to enable the exception.
    assert (
        world.tool.unit_job(campaign, plan, FILE, campaign.staging(0))["limits"]["max_record_bytes"]
        == NORMAL
    )


@pytest.mark.parametrize("over", [0, 1])
def test_batch1_record_keeps_normal_eight_mib_bound(
    world: World, tmp_path: Path, over: int
) -> None:
    world.authorize(0)
    campaign, source = scoped(world)
    plan = load_acquisition_plan(campaign.batch_dir(0) / "batch.plan.json")
    row = rows_of(1)[0]
    row["text"] = ""
    row["text"] = "x" * (NORMAL + over - len(encode_record(row)))
    assert len(encode_record(row)) == NORMAL + over
    path = tmp_path / "large.parquet"
    pq.write_table(pa.Table.from_pylist([row]), path, compression=None)
    job = world.tool.unit_job(
        campaign, plan, FILE, tmp_path / "out", batch=1, rank=26, source=source
    )
    options: dict[str, Any] = {
        "source_file": FILE,
        "locator": {},
        "etag": '"a"',
        "columns": list(row),
        "max_record_bytes": job["limits"]["max_record_bytes"],
        "max_parser_bytes": 32 * 1024**2,
        "max_decoded_bytes": 32 * 1024**2,
    }
    if over:
        with pytest.raises(RecordLimitError, match="record byte bound exceeded"):
            list(sp.selected_payloads(path, **options))
    else:
        assert len(list(sp.selected_payloads(path, **options))) == 1


def test_future_batches_run_resume_and_seal_without_recovery_authorization(
    world: World,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world.pass_benchmark()
    world.authorize(0)
    assert world.run("run", "--batch", "0") == 0
    protected = {
        p: (p.read_bytes(), p.stat().st_mtime_ns)
        for p in world.campaign().unit_dir(0, 0).parent.rglob("*")
        if p.is_file()
    }
    amendment = {
        "campaign": world.config["digest"],
        "batch": 0,
        "file": FILE,
        "source_sha256": "a" * 64,
        "max_record_bytes": EXCEPTION,
        "digest": "b" * 64,
        "sealed_receipts": {k: v["digest"] for k, v in world.receipts().items()},
    }
    original = world.tool.load_campaign

    def load(*args: Any, **kwargs: Any) -> Any:
        return replace(original(*args, **kwargs), recovery=amendment)

    monkeypatch.setattr(world.tool, "load_campaign", load)
    for batch in (1, 2):
        world.authorize(batch)
        before_auth = (world.campaign().batch_dir(batch) / "authorization.json").read_bytes()
        if batch == 1:
            second = world.campaign().members(batch)[1]
            world.state.drops[second] = [1000, 1000, 1000]
            assert world.run("run", "--batch", "1", "--workers", "1") == 1
            assert recovery.resume_state(world.campaign(), world.batch(1))["sealed"] == 1
        assert (
            world.run("run", "--batch", str(batch), "--top-up-reason", "authored scope test") == 3
        )
        calls = len(world.state.requests)
        assert (
            world.run("run", "--batch", str(batch), "--top-up-reason", "authored scope test") == 4
        )
        assert len(world.state.requests) == calls
        state = recovery.resume_state(world.campaign(), world.batch(batch))
        assert state["sealed"] == 2 and state["scheduled"] == 0
        assert all("recovery" not in r for r in state["receipts"])
        assert not (world.campaign().batch_dir(batch) / "recovery-authorization.json").exists()
        assert (
            world.campaign().batch_dir(batch) / "authorization.json"
        ).read_bytes() == before_auth
    assert all((p.read_bytes(), p.stat().st_mtime_ns) == value for p, value in protected.items())
    assert recovery.resume_state(world.campaign(), world.batch(0))["sealed"] == 2


def test_exact_recovery_still_requires_approval_and_verified_content(world: World) -> None:
    from test_essential_web_fast import parquet_bytes

    world.authorize(0)
    campaign, _ = scoped(world)
    plan = load_acquisition_plan(campaign.batch_dir(0) / "batch.plan.json")
    record = world.batch(0)
    amendment = campaign.recovery
    assert amendment is not None
    amendment["batch_digest"] = record["digest"]
    raw = campaign.raw_path(FILE)
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_bytes(parquet_bytes(rows_of(1), 1))
    sha, size = sp.file_sha256(raw)
    amendment["source_sha256"] = sha
    identity = sp.identity_record(
        sp.SourceIdentity('"a"', size, sha),
        source_file=FILE,
        repository=plan.repository,
        revision=plan.revision,
    )
    sp.promote_source(raw, raw, identity)
    resume = {"receipts": [], "remaining": [{"file": FILE, "rank": 26}]}

    def prepare() -> Any:
        return world.tool.prepare_units(
            campaign,
            plan,
            record,
            campaign.scratch("b0000"),
            resume,
            require_recovery_authorization=True,
        )

    with pytest.raises(BulkError, match="operator authorization"):
        prepare()
    world.tool.write_json(
        campaign.batch_dir(0) / "recovery-authorization.json",
        {"digest": amendment["digest"], "operator": "authored"},
    )
    units, _, _ = prepare()
    assert units[0].key == "f00026" and units[0].url is None
    assert units[0].job["limits"]["max_record_bytes"] == EXCEPTION
    amendment["source_sha256"] = "0" * 64
    with pytest.raises(BulkError, match="exact retained source"):
        prepare()


def test_scope_fix_does_not_allow_unfrozen_code_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from xlm.data.evidence_v2 import canonical

    old = {
        "scripts/essential_web_fast.py": "old",
        "src/xlm/data/sources/essential_web_recovery.py": "old",
        "reader.py": "unchanged",
    }
    new = {
        **old,
        "scripts/essential_web_fast.py": "new",
        "src/xlm/data/sources/essential_web_recovery.py": "new",
    }
    manifest = {"code": old, "digest": "b" * 64, "campaign": "a" * 64}
    monkeypatch.setattr(recovery, "code_identity", lambda _: new)
    assert not recovery.compatible_code(tmp_path, manifest)
    fix = {
        "kind": "essential_web_recovery_scope_fix_v1",
        "previous_code": old,
        "code": new,
        "campaign": manifest["campaign"],
        "recovery_digest": manifest["digest"],
    }
    fix["digest"] = canonical.digest(fix)
    path = tmp_path / recovery.SCOPE_FIX
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(fix))
    assert recovery.compatible_code(tmp_path, manifest)
    monkeypatch.setattr(recovery, "code_identity", lambda _: {**new, "reader.py": "changed"})
    assert not recovery.compatible_code(tmp_path, manifest)
