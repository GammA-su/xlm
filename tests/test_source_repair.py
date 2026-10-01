"""Repair of an authorized, partially sealed source plan (offline, authored fixtures).

A unit that fails under its plan's own limits is never retried by editing the
plan: a new repair plan re-plans exactly the unsealed ranks under changed
limits, binds the retained verified bytes, keeps the predecessor's cursor and
makes the predecessor unrunnable. Sufficiency and the first-pass seal count a
rank once, through the plan that sealed it, and never drop an unsealed rank.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from mix01_source_fixtures import parquet_bytes, ultrax_row
from test_source_plan import PIN, build, frozen_policy, inventory, layout, requirement
from test_source_run import (  # noqa: F401 - pytest fixtures
    ROWS_PER_FILE,
    World,
    bind_row_group_parallel,
    loopback_only,
    serial_unit,
    served,
    tokens_for_files,
    world,
)
from xlm.data.acquisition import source_plan as planner
from xlm.data.acquisition import source_run as runner
from xlm.data.acquisition import transport_policy as tp
from xlm.data.acquisition.plan import AcquisitionLimits, AcquisitionMode, AcquisitionPlan
from xlm.data.acquisition.records import RecordLimitError
from xlm.data.acquisition.source_parquet import load_durable_source

MIB = 1024 * 1024
KEY = (PIN["source_id"], PIN["view_id"])
#: Authored bounds: the long fixture row sits between them.
V1, V2 = 4096, 32768
FAILED = {"key": "f00000", "exception": "RecordLimitError", "root": True}


def failure(key: str = "f00000") -> dict[str, Any]:
    return {
        "receipt": "performance-00.json",
        "digest": "5" * 64,
        "root_failure": {**FAILED, "key": key},
    }


def repaired(first: dict[str, Any], **changes: Any) -> planner.Repaired:
    files = first["selection"]["files"]
    values: dict[str, Any] = {
        "plan": first,
        "authorized_digest": first["digest"],
        "sealed_ranks": tuple(int(e["rank"]) for e in files[1:]),
        "sealed_canonical_bytes": 2_700_000,
        "accounting_digest": "4" * 64,
        "failures": (failure(),),
        "retained": {files[0]["file"]: "6" * 64},
    }
    values.update(changes)
    return planner.Repaired(**values)


def repair(first: dict[str, Any], **changes: Any) -> dict[str, Any]:
    arguments: dict[str, Any] = {
        "source_key": "ultrax",
        "pin": PIN,
        "requirement": requirement(),
        "inventory": inventory(),
        "inventory_sha256": "3" * 64,
        "layout": layout(),
        "calibration": {"rows": "0" * 64},
        "policy": frozen_policy(),
        "admission": {"probe_fingerprint": "f" * 64, "bridge_receipt_digest": "b" * 64},
        "repaired": repaired(first),
    }
    arguments.update(changes)
    return planner.build_repair_plan(**arguments)


# ------------------------------------------------------------------ planner


def test_record_bound_is_independent_of_the_parser_bound_only_for_whole_files() -> None:
    limits = AcquisitionLimits(max_record_bytes=48 * MIB, max_parser_bytes=32 * MIB)
    common: dict[str, Any] = {
        "plan_id": "p",
        "source_id": "s",
        "provider": "huggingface",
        "repository": "r/r",
        "revision": "0" * 40,
        "selected_files": ["a.parquet"],
        "output_artifact_id": "o",
        "limits": limits,
    }
    assert AcquisitionPlan(**common).limits.max_record_bytes == 48 * MIB
    with pytest.raises(ValueError, match="record byte bound exceeds parser byte bound"):
        AcquisitionPlan(
            **common, mode=AcquisitionMode.SELECTED_RECORDS, row_ranges={"a.parquet": (0, 1)}
        )


def test_finepdfs_v2_bound_generic_and_ultrax_bounds() -> None:
    assert planner.record_bound("finepdfs_edu", "eng_Latn") == (
        48 * MIB,
        planner.SOURCE_RECORD_BYTES[("finepdfs_edu", "eng_Latn")][1],
    )
    assert planner.SOURCE_RECORD_BYTES[("finepdfs_edu", "eng_Latn")][1].startswith(
        "finepdfs-record-v2"
    )
    assert planner.record_bound(*KEY) == (8 * MIB, None)
    assert planner.MAX_RECORD_BYTES == 8 * MIB and planner.MAX_PARSER_BYTES == 32 * MIB


def test_old_plan_reproduces_under_its_own_bound_and_a_new_bound_is_a_new_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with monkeypatch.context() as patch:
        patch.setitem(planner.SOURCE_RECORD_BYTES, KEY, (V1, "authored v1"))
        old = build()
        assert build()["digest"] == old["digest"]
    planner.check_plan(old)
    assert planner.minted_from_record(old).plan_hash == old["acquisition_plan"]["plan_hash"]
    with monkeypatch.context() as patch:
        patch.setitem(planner.SOURCE_RECORD_BYTES, KEY, (V2, "authored v2"))
        new = build()
    assert new["limits"]["max_record_bytes"] == V2 != old["limits"]["max_record_bytes"]
    assert new["digest"] != old["digest"]
    assert new["acquisition_plan"]["plan_hash"] != old["acquisition_plan"]["plan_hash"]


def test_repair_replans_exactly_the_unsealed_ranks(monkeypatch: pytest.MonkeyPatch) -> None:
    first = build()
    before = copy.deepcopy(first)
    monkeypatch.setitem(planner.SOURCE_RECORD_BYTES, KEY, (16 * MIB, "authored v2"))
    record = repair(first)
    assert first == before  # the repaired plan is not edited
    rank0 = first["selection"]["files"][0]
    assert record["sequence"] == 2
    assert record["selection"]["files"] == [rank0]
    assert record["selection"]["ranks"] == [0]
    assert record["selection"]["next_cursor"] == first["selection"]["next_cursor"]
    assert record["acquired_before"]["canonical_bytes"] == 2_700_000
    assert record["lineage"]["previous_plan_digest"] == first["digest"]
    assert record["repair"]["plan_digest"] == first["digest"]
    assert record["repair"]["sealed_ranks"] == list(range(1, len(first["selection"]["files"])))
    assert record["repair"]["changed_limits"] == {
        "max_record_bytes": {"from": 8 * MIB, "to": 16 * MIB}
    }
    assert record["acquisition_plan"]["expected_file_digests"] == {rank0["file"]: "6" * 64}
    assert record["expected"]["transfer_bytes"] == 0 and record["expected"]["requests"] == 0
    minted = planner.minted_from_record(record)
    assert minted.plan_hash == record["acquisition_plan"]["plan_hash"]
    assert minted.expected_file_digests == {rank0["file"]: "6" * 64}
    assert repair(first)["digest"] == record["digest"]  # deterministic
    # Without retained bytes the unit is downloaded and verified upstream.
    fresh = repair(first, repaired=repaired(first, retained={}))
    assert "expected_file_digests" not in fresh["acquisition_plan"]
    assert fresh["expected"]["requests"] == 2 and fresh["digest"] != record["digest"]


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"authorized_digest": "0" * 64}, "only an authorized plan"),
        ({"failures": ()}, "failed-run receipts"),
        ({"failures": (failure("f00003"),)}, "no failed run names an unsealed unit"),
        ({"sealed_ranks": tuple(range(10))}, "nothing to repair"),
        ({"sealed_ranks": (1, 99)}, "not ranks of the repaired plan"),
        ({"retained": {"data/elsewhere.parquet": "6" * 64}}, "SHA-256 of unsealed files"),
        ({"retained": {"x": "not-a-digest"}}, "SHA-256 of unsealed files"),
    ],
)
def test_repair_refuses_without_its_evidence(
    monkeypatch: pytest.MonkeyPatch, changes: dict[str, Any], message: str
) -> None:
    first = build()
    monkeypatch.setitem(planner.SOURCE_RECORD_BYTES, KEY, (16 * MIB, "authored v2"))
    with pytest.raises(planner.PlanError, match=message):
        repair(first, repaired=repaired(first, **changes))


def test_repair_with_unchanged_limits_is_a_resume_not_a_repair() -> None:
    first = build()
    with pytest.raises(planner.PlanError, match="resume the plan instead"):
        repair(first)


def test_repair_refuses_another_source_or_inventory(monkeypatch: pytest.MonkeyPatch) -> None:
    first = build()
    monkeypatch.setitem(planner.SOURCE_RECORD_BYTES, KEY, (16 * MIB, "authored v2"))
    with pytest.raises(planner.PlanError, match="another source or inventory"):
        repair(first, source_key="finepdfs")
    with pytest.raises(planner.PlanError, match="another source or inventory"):
        repair(first, inventory=inventory(seed=7))


# ------------------------------------------------------------------- runner


def long_row(index: int) -> dict[str, Any]:
    row = ultrax_row(index)
    text = "Authored long paragraph about measurement and tides. " * 120
    return {**row, "cleaned_content": text, "raw_content": f"RAW {index} {text}"}


def oversize(w: World, rank: int) -> str:
    """Serve the file at ``rank`` with one row between the authored bounds."""
    name = w.ordered()[rank]
    index = int(name.split("-")[1].split(".")[0])
    rows = [
        long_row(index * ROWS_PER_FILE + r)
        if r == 150
        else ultrax_row(index * ROWS_PER_FILE + r, empty=r % 50 == 7)
        for r in range(ROWS_PER_FILE)
    ]
    w.served.files[name] = parquet_bytes(rows, 100)
    return name


def failed_first_plan(
    w: World, monkeypatch: pytest.MonkeyPatch, tokens: int, files: int
) -> dict[str, Any]:
    """Plan 1 under V1; its rank-0 unit fails closed, every other unit seals.

    File processes let in-flight units finish after the root failure, as in a
    real run; a resume picks up any unit that was not yet started.
    """
    monkeypatch.setitem(planner.SOURCE_RECORD_BYTES, KEY, (V1, "authored v1"))
    oversize(w, 0)
    first = w.plan(tokens)
    assert len(first["selection"]["files"]) == files
    for _ in range(files):
        with pytest.raises(RecordLimitError, match="row=150 "):
            w.run(download_workers=2, process_workers=2)
        if runner.resume_state(w.roots, first)["sealed"] == files - 1:
            break
    assert [r["rank"] for r in runner.resume_state(w.roots, first)["receipts"]] == list(
        range(1, files)
    )
    return first


def repaired_of(w: World, sequence: int) -> planner.Repaired:
    """What the driver gathers (scripts/mix01_source.py repaired_of), on the fixture store."""
    roots = w.roots
    record = runner.load_plan(roots, sequence)
    resume = runner.resume_state(roots, record)
    totals = runner.account(roots, record)
    failures = []
    for path in sorted(roots.plan_dir(sequence).glob("performance-*.json")):
        receipt = runner.read_json(path)
        if receipt["outcome"]["status"] == "failed":
            failures.append(
                {
                    "receipt": path.name,
                    "digest": receipt["digest"],
                    "root_failure": receipt["outcome"]["root_failure"],
                }
            )
    retained = {}
    for entry in resume["remaining"]:
        source = load_durable_source(roots.raw_path(entry["file"]))
        if source is not None:
            retained[entry["file"]] = source["sha256"]
    return planner.Repaired(
        plan=record,
        authorized_digest=runner.read_json(roots.plan_dir(sequence) / "authorization.json")[
            "plan_digest"
        ],
        sealed_ranks=tuple(int(r["rank"]) for r in resume["receipts"]),
        sealed_canonical_bytes=int(totals["canonical_bytes"]),
        accounting_digest=totals["digest"],
        failures=tuple(failures),
        retained=retained,
    )


def world_repair(w: World, tokens: int) -> dict[str, Any]:
    quotas = {"first_pass_headroom_quotas": {"ultrax_ultrafineweb": tokens}}
    estimate = {
        "assumptions": {"bytes_per_token_base": 4.0, "safety_margin": 1.15},
        "sources": {
            "ultrax_ultrafineweb": {
                "status": "ESTIMATED",
                "first_pass_usable_token_target": tokens,
                "required_canonical_bytes_base": tokens * 4.0,
            }
        },
    }
    record = planner.build_repair_plan(
        source_key="ultrax",
        pin=PIN,
        requirement=planner.requirement_from(
            "ultrax_ultrafineweb",
            quotas,
            estimate,
            quotas_sha256="1" * 64,
            estimate_sha256="2" * 64,
        ),
        inventory=w.inventory,
        inventory_sha256="3" * 64,
        layout=w.layout,
        calibration={"rows": "0" * 64},
        policy=w.policy,
        admission={"probe_fingerprint": "f" * 64, "bridge_receipt_digest": "b" * 64},
        repaired=repaired_of(w, 1),
    )
    runner.store_plan(w.roots, record)
    return record


def snapshot(w: World, sequence: int) -> dict[str, bytes]:
    """Every durable byte of one plan: its directory and its sealed units."""
    found: dict[str, bytes] = {}
    for root in (w.roots.plan_dir(sequence), w.roots.canonical / f"p{sequence:02d}"):
        for path in sorted(root.rglob("*")):
            if path.is_file():
                found[path.relative_to(w.roots.data_root).as_posix()] = path.read_bytes()
    return found


def test_failed_unit_is_repaired_offline_and_sealed_once(
    world: World,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bind_row_group_parallel(monkeypatch)
    tokens = tokens_for_files(world, 3)
    first = failed_first_plan(world, monkeypatch, tokens, 3)
    failed = json.loads((world.roots.plan_dir(1) / "performance-00.json").read_text())
    assert failed["outcome"]["root_failure"]["key"] == "f00000"
    status = runner.sufficiency(world.roots)
    assert status["status"] == "INCOMPLETE" and status["unresolved_ranks"] == {"1": [0]}
    with pytest.raises(runner.RunError, match="INCOMPLETE"):
        runner.first_pass_seal(world.roots)
    # Ordinary top-up would start at cursor 3 and skip rank 0: it is refused.
    totals = runner.account(world.roots, first)
    with pytest.raises(planner.PlanError, match="not completely sealed"):
        world.plan(
            tokens,
            planner.Predecessor(first, int(totals["canonical_bytes"]), 2, totals["digest"]),
        )
    # Retrying under the unchanged bound is a resume, not a repair.
    with pytest.raises(planner.PlanError, match="resume the plan instead"):
        world_repair(world, tokens)
    history = snapshot(world, 1)
    monkeypatch.setitem(planner.SOURCE_RECORD_BYTES, KEY, (V2, "authored v2"))
    record = world_repair(world, tokens)
    name = first["selection"]["files"][0]["file"]
    assert record["sequence"] == 2 and record["selection"]["ranks"] == [0]
    assert record["selection"]["next_cursor"] == 3
    assert record["repair"]["changed_limits"]["max_record_bytes"] == {"from": V1, "to": V2}
    retained = load_durable_source(world.roots.raw_path(name))
    assert retained is not None
    assert record["acquisition_plan"]["expected_file_digests"] == {name: retained["sha256"]}
    # The repaired plan can no longer run; the repair needs its own authorization.
    with pytest.raises(runner.RunError, match="repaired by a later plan"):
        world.run(sequence=1)
    with pytest.raises(runner.RunError, match="not authorized"):
        world.run(sequence=2, offline=True)
    runner.authorize(world.roots, 2, record["digest"], "tester", runner_admitted)
    hits = {n: world.served.hits(n) for n in world.served.files}
    report = world.run(sequence=2, offline=True)
    assert report["outcome"]["status"] == "completed"
    assert {n: world.served.hits(n) for n in world.served.files} == hits  # zero network
    assert report["transfer"]["files"] == 0 and report["transfer"]["transferred_bytes"] == 0
    (unit,) = runner.resume_state(world.roots, record)["receipts"]
    assert (unit["rank"], unit["file"], unit["raw"]["sha256"]) == (0, name, retained["sha256"])
    assert unit["transfer"]["transferred_bytes"] == 0
    assert unit["selected_records"]["max_record_bytes"] > V1
    # Row-group parallel output equals the serial adaptation of the same bytes.
    serial = serial_unit(world, record, unit)
    assert serial["row_group_workers"] == 1
    assert unit["documents_sha256"] == serial["documents_sha256"]
    assert unit["rejections"]["sha256"] == serial["rejections_sha256"]
    # The failed plan, its failure evidence and its sealed units are untouched.
    assert snapshot(world, 1) == history
    assert runner.verify_plan(world.roots, first, content=True)["units_verified"] == 2
    assert runner.verify_plan(world.roots, record, content=True)["units_verified"] == 1
    assert runner.account(world.roots, first)["complete"] is False
    status = runner.sufficiency(world.roots)
    assert status["status"] == "SUFFICIENT"
    assert status["repairs"] == [{"sequence": 2, "repairs_sequence": 1, "ranks": [0]}]
    assert status["next_cursor"] == 3
    seal = runner.first_pass_seal(world.roots)
    assert [(u["sequence"], u["rank"]) for u in seal["units"]] == [(1, 1), (1, 2), (2, 0)]
    assert sorted(u["file"] for u in seal["units"]) == sorted(world.ordered()[:3])
    assert seal["plans"][0]["repaired_ranks"] == {"2": [0]}
    assert seal["plans"][1]["repair_of"] == {
        "plan_sequence": 1,
        "plan_digest": first["digest"],
        "ranks": [0],
    }
    assert runner.first_pass_seal(world.roots)["digest"] == seal["digest"]


def runner_admitted(plan: Any) -> None:
    assert plan.source_id == PIN["source_id"]


def test_top_up_after_a_repair_continues_at_the_unchanged_cursor(
    world: World,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A doubled-yield estimate plans 3 files that cannot meet a 5-file requirement.
    tokens = tokens_for_files(world, 5)
    doubled = 2 * world.layout.canonical_bytes_per_row
    world.layout = tp.SourceLayout(**{**world.layout.__dict__, "canonical_bytes_per_row": doubled})
    failed_first_plan(world, monkeypatch, tokens, 3)
    monkeypatch.setitem(planner.SOURCE_RECORD_BYTES, KEY, (V2, "authored v2"))
    record = world_repair(world, tokens)
    runner.authorize(world.roots, 2, record["digest"], "tester", runner_admitted)
    world.run(sequence=2, offline=True)
    status = runner.sufficiency(world.roots)
    assert status["status"] == "TOP_UP"
    with pytest.raises(runner.RunError, match="TOP_UP"):
        runner.first_pass_seal(world.roots)
    sealed = sum(
        int(runner.account(world.roots, runner.load_plan(world.roots, s))["canonical_bytes"])
        for s in (1, 2)
    )
    totals = runner.account(world.roots, record)
    third = world.plan(
        tokens,
        planner.Predecessor(record, int(totals["canonical_bytes"]), 1, totals["digest"]),
    )
    assert third["sequence"] == 3 and third["selection"]["start_rank"] == 3
    assert third["acquired_before"]["canonical_bytes"] == sealed
    files = [e["file"] for e in third["selection"]["files"]]
    assert files == world.ordered()[3 : 3 + len(files)]


def test_an_unrepaired_failed_unit_is_never_dropped_silently(
    world: World,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 1.9 files' requirement plans 3 files (1.9 x 1.15 > 2); 2 sealed files exceed it.
    tokens = int(1.9 * world.layout.rows_per_file * world.layout.canonical_bytes_per_row / 4)
    first = failed_first_plan(world, monkeypatch, tokens, 3)
    acquired = runner.account(world.roots, first)["canonical_bytes"]
    assert acquired >= first["requirement"]["required_canonical_bytes"]
    status = runner.sufficiency(world.roots)
    assert status["status"] == "INCOMPLETE" and status["unresolved_ranks"] == {"1": [0]}
    with pytest.raises(runner.RunError, match="INCOMPLETE"):
        runner.first_pass_seal(world.roots)
    assert not (world.roots.plans / "first-pass-seal.json").exists()


def test_a_retained_source_must_match_the_bound_digest(
    world: World,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tokens = tokens_for_files(world, 3)
    failed_first_plan(world, monkeypatch, tokens, 3)
    monkeypatch.setitem(planner.SOURCE_RECORD_BYTES, KEY, (V2, "authored v2"))
    record = world_repair(world, tokens)
    runner.authorize(world.roots, 2, record["digest"], "tester", runner_admitted)
    name = record["selection"]["files"][0]["file"]
    raw = world.roots.raw_path(name)
    sidecar = Path(str(raw) + ".identity.json")
    identity = json.loads(sidecar.read_text())
    other = world.served.files[world.ordered()[5]]
    raw.write_bytes(other)  # another verified-looking file under the same name
    identity.update(sha256=hashlib.sha256(other).hexdigest(), length=len(other))
    sidecar.write_text(json.dumps(identity))
    with pytest.raises(runner.RunError, match="bound SHA-256"):
        world.run(sequence=2, offline=True)
    assert runner.resume_state(world.roots, record)["sealed"] == 0
