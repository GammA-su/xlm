"""Selected-file bounds, IFM footer bounds, repair and supersession (offline, authored).

The IFM fixture copies the real calibration layout numbers (one 698-row group
of the 407,007,573-byte calibration shard) and gives the selected files the
exact sizes of the frozen inventory, so the pre-fix rule reproduces General
p01's limits byte for byte. Under the selected-size anchor no plan's file
bound excludes a known selected size; a failed plan is repaired and an
unauthorized one is superseded, both without editing the original.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path
from typing import Any

import pytest

from test_mix01_source_cli import load_cli
from test_source_plan import PIN as RUN_PIN
from test_source_plan import models
from test_source_run import (  # noqa: F401 - pytest fixtures
    World,
    admitted,
    loopback_only,
    served,
    tokens_for_files,
    world,
)
from xlm.data.acquisition import source_plan as planner
from xlm.data.acquisition import source_run as runner
from xlm.data.acquisition import transport_policy as tp
from xlm.data.acquisition.source_growth import ProcessingGrowth

MIB = 1024**2
REVISION = "3345e13d7f3f6d0ecb5fdd67b37aed289f3191f5"
COMPONENT = "ifm_behaviors_general_planning"
#: (view, calibration file bytes, group rows, group bytes, canonical bytes, selected sizes)
VIEWS = {
    "general": (407_007_573, 698, 10_226_373, 10_216_553, (2_013_330_256, 2_014_409_401)),
    "planning": (706_323_152, 1903, 25_446_776, 25_422_431, (2_007_884_297, 2_007_493_650)),
}
#: Bounded footer reads of the four selected files (rows, text uncompressed bytes).
FOOTERS = {
    "general": ((329_409, 4_844_485_958), (328_568, 4_844_194_575)),
    "planning": ((350_701, 4_644_490_344), (346_354, 4_643_743_770)),
}
FAILURE = {
    "receipt": "performance-00.json",
    "digest": "e5f6e98e80792caf716ef80e77cb276f0f3e80181a1fa97a76abb709589706f3",
    "root_failure": {
        "exception": "SourceTransferError",
        "key": "f00001",
        "root": True,
        "site": "source_parquet.py:575 in _bind",
    },
}
ADMISSION = {"probe_fingerprint": "f" * 64, "bridge_receipt_digest": "b" * 64}


def pin(view: str) -> dict[str, str]:
    return {
        "source_id": "ifm_behaviors",
        "view_id": view,
        "component_id": COMPONENT,
        "provider": "huggingface",
        "repository": "IFM/Pretrain-Behaviors",
        "revision": REVISION,
        "adapter_id": f"ifm_{view}",
    }


def layout(view: str) -> tp.SourceLayout:
    file_bytes, rows, group_bytes, canonical, _ = VIEWS[view]
    return tp.SourceLayout(
        source_id="ifm_behaviors",
        file_bytes=file_bytes,
        group_rows=rows,
        group_bytes=group_bytes,
        projected_group_bytes=group_bytes // 2,
        range_requests_per_group=1.0,
        metadata_requests_per_file=3,
        canonical_bytes_per_row=canonical / rows,
        range_record_bytes_per_row=canonical / rows,
        metadata_bytes_per_file=66_706,
        source_files=None,
        evidence={"rows": "0" * 64},
    )


def inventory(view: str, count: int = 12) -> dict[str, Any]:
    """Hash-ordered names; ranks 0 and 1 carry the real selected sizes, the last
    rank the calibration shard's (the inventory minimum)."""
    seed = 20260918
    names = [f"{view}/{view}.chunk0-test-{i:05d}.parquet" for i in range(count)]
    entries: list[dict[str, Any]] = sorted(
        (
            {
                "file": name,
                "order_key": hashlib.sha256(
                    f"{seed}|IFM/Pretrain-Behaviors|{REVISION}|{name}".encode()
                ).hexdigest(),
            }
            for name in names
        ),
        key=lambda e: (e["order_key"], e["file"]),
    )
    sizes = [*VIEWS[view][4], *(2_010_000_000 + i for i in range(count - 3)), VIEWS[view][0]]
    for entry, size in zip(entries, sizes, strict=True):
        entry["size_bytes"] = size
    value: dict[str, Any] = {
        "inventory_version": 1,
        "source_id": "ifm_behaviors",
        "repository": "IFM/Pretrain-Behaviors",
        "revision": REVISION,
        "seed": seed,
        "selection": "SHA-256(seed|repository|revision|file) ascending, filename tiebreak",
        "files": entries,
        "file_count": count,
        "known_size_bytes": sum(sizes),
    }
    value["inventory_digest"] = planner.inventory_digest(value)
    return value


def requirement(view: str) -> planner.Requirement:
    tokens = 165_000_000
    quotas = {"first_pass_headroom_quotas": {COMPONENT: tokens}}
    estimate = {
        "assumptions": {"bytes_per_token_base": 4.0, "safety_margin": 1.15},
        "sources": {
            COMPONENT: {
                "status": "ESTIMATED",
                "first_pass_usable_token_target": tokens,
                "required_canonical_bytes_base": tokens * 4.0,
            }
        },
    }
    return planner.requirement_from(
        COMPONENT, quotas, estimate, quotas_sha256="1" * 64, estimate_sha256="2" * 64
    )


def policy(view: str) -> dict[str, Any]:
    report = tp.evaluate(
        layout(view),
        tp.Requirement("ifm_behaviors", 660_000_000, 1.15),
        tp.Ceilings(),
        {tp.TransportMode.WHOLE_FILE_LOCAL: models()[tp.TransportMode.WHOLE_FILE_LOCAL]},
    )
    assert report["selected_mode"] == "whole_file_local"
    return tp.freeze(report, basis="modeled", inputs={"model": "authored"})


def arguments(view: str) -> dict[str, Any]:
    return {
        "source_key": f"ifm_{view}",
        "pin": pin(view),
        "requirement": requirement(view),
        "inventory": inventory(view),
        "inventory_sha256": "3" * 64,
        "layout": layout(view),
        "calibration": {"rows": "0" * 64},
        "policy": policy(view),
        "admission": ADMISSION,
    }


def pre_fix(view: str, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """The plan the planner emitted before selected sizes participated (p01)."""
    with monkeypatch.context() as patch:
        patch.setattr(planner, "known_sizes", lambda inventory, files: [])
        patch.delitem(planner.SOURCE_FILE_BOUNDS, ("ifm_behaviors", view))
        return planner.build_plan(**arguments(view))


def roots_of(tmp_path: Path, view: str) -> runner.Roots:
    return runner.Roots(tmp_path / "data", tmp_path / "scratch", f"ifm_{view}")


def tree(roots: runner.Roots, sequence: int) -> dict[str, str]:
    directory = roots.plan_dir(sequence)
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in directory.iterdir()}


# ------------------------------------------------------------------ planner


@pytest.mark.parametrize("view", sorted(VIEWS))
def test_pre_fix_rule_reproduces_p01_and_its_violation_is_refused(
    view: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = pre_fix(view, monkeypatch)
    expected = {"general": (814_743_552, 55_560), "planning": (1_413_480_448, 105_642)}[view]
    assert (old["limits"]["max_file_bytes"], old["limits"]["max_rows_per_file"]) == expected
    assert [e["rank"] for e in old["selection"]["files"]] == [0, 1]
    # Both selected files are known to exceed it: authorization would be refused.
    with pytest.raises(planner.PlanError, match="exceed the plan's max_file_bytes"):
        planner.check_selected_file_bounds(old, inventory(view))


@pytest.mark.parametrize("view", sorted(VIEWS))
def test_selected_sizes_bound_every_per_file_ceiling_consistently(view: str) -> None:
    record = planner.build_plan(**arguments(view))
    limits, acq = record["limits"], record["acquisition_plan"]["limits"]
    sizes = VIEWS[view][4]
    planner.check_selected_file_bounds(record, inventory(view))
    max_file = limits["max_file_bytes"]
    assert max_file == math.ceil(max(sizes) / MIB) * MIB >= max(sizes)
    anchor = limits["file_size_anchor"]
    assert anchor["largest_selected_file_bytes"] == max(sizes)
    assert anchor["calibration_file_bytes"] == VIEWS[view][0]
    assert anchor["estimate_max_file_bytes"] < max(sizes)
    # Footer facts fit the IFM file bounds; text bounds canonical text.
    rows, canonical, basis = planner.SOURCE_FILE_BOUNDS[("ifm_behaviors", view)]
    assert basis.startswith(f"ifm-{view}-file-v1")
    assert rows == math.ceil(1.35 * max(r for r, _ in FOOTERS[view]))
    assert canonical == math.ceil(1.35 * max(t for _, t in FOOTERS[view]))
    assert (
        limits["max_rows_per_file"] == rows and limits["max_canonical_bytes_per_file"] == canonical
    )
    # Every derived ceiling follows the anchored file bound.
    growth = limits["processing_growth"]
    assert limits["max_decoded_bytes_per_file"] == 4 * max_file > 4_846_739_434
    assert growth["source_max_bytes"] == max_file
    assert limits["max_durable_bytes_per_file"] == max_file + growth["output_bytes"]
    assert canonical <= growth["output_bytes"]
    model = ProcessingGrowth.model_validate(growth)
    unit_peak = max_file + model.processing_peak + model.state_peak
    assert limits["scratch_cap_bytes"] == limits["max_in_flight_files"] * unit_peak
    assert limits["scratch_cap_bytes"] <= planner.SCRATCH_CAP_BYTES
    assert limits["file_deadline_seconds"] == max_file / planner.MIN_FILE_RATE_BYTES_PER_SECOND
    assert acq["max_transferred_bytes"] >= sum(sizes)
    assert acq["max_output_disk_bytes"] >= 2 * (max_file + growth["output_bytes"])
    assert record["expected"]["transfer_bytes"] == sum(sizes)
    assert record["expected"]["basis"] == planner.ANCHORED_BASIS
    assert record["selection"]["next_cursor"] == 2


def test_plans_whose_files_fit_the_estimate_keep_their_exact_limits() -> None:
    general = layout("general")
    mode = tp.TransportMode.WHOLE_FILE_LOCAL
    estimate = planner.plan_limits(2, general, mode, pin("general"))
    bound = estimate[0]["max_file_bytes"]
    for sizes in ([], [general.file_bytes], [bound - 1, bound]):
        assert planner.plan_limits(2, general, mode, pin("general"), known_file_bytes=sizes) == (
            estimate
        )
    assert "file_size_anchor" not in estimate[0]
    for other in ({**pin("general"), "source_id": "finewiki", "view_id": "en"},):
        assert (
            "file_size_anchor"
            not in planner.plan_limits(1, general, mode, other, known_file_bytes=[bound])[0]
        )


# ------------------------------------------------------------------- repair


def general_repair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[runner.Roots, dict[str, Any], dict[str, Any], dict[str, str]]:
    roots = roots_of(tmp_path, "general")
    first = pre_fix("general", monkeypatch)
    runner.store_plan(roots, first)
    runner.write_once(
        roots.plan_dir(1) / "authorization.json",
        {"plan_digest": first["digest"], "operator": "tester", "authorized_at": "t"},
    )
    before = tree(roots, 1)
    repaired = planner.Repaired(
        plan=first,
        authorized_digest=first["digest"],
        sealed_ranks=(),
        sealed_canonical_bytes=0,
        accounting_digest="4" * 64,
        failures=(FAILURE,),
        retained={},
    )
    record = planner.build_repair_plan(**arguments("general"), repaired=repaired)
    runner.store_plan(roots, record)
    return roots, first, record, before


def test_general_repair_covers_both_ranks_once_and_keeps_p01(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    roots, first, record, before = general_repair(tmp_path, monkeypatch)
    assert tree(roots, 1) == before  # p01 byte-identical
    assert record["sequence"] == 2 and record["selection"]["ranks"] == [0, 1]
    assert record["selection"]["next_cursor"] == first["selection"]["next_cursor"] == 2
    assert record["repair"]["failures"] == [FAILURE] and record["repair"]["retained_sha256"] == {}
    assert record["lineage"]["previous_plan_digest"] == first["digest"]
    changed = record["repair"]["changed_limits"]
    assert {
        "max_file_bytes",
        "max_rows_per_file",
        "max_canonical_bytes_per_file",
        "max_decoded_bytes_per_file",
        "max_durable_bytes_per_file",
        "processing_growth",
        "file_deadline_seconds",
    } <= set(changed)
    for key, change in changed.items():
        assert change == {"from": first["limits"].get(key), "to": record["limits"].get(key)}
    # Changed limits are identity-bound: altering one breaks the digest.
    forged = copy.deepcopy(record)
    forged["repair"]["changed_limits"]["max_file_bytes"]["to"] += MIB
    with pytest.raises(planner.PlanError, match="digest does not verify"):
        planner.check_plan(forged)
    assert planner.minted_from_record(record).plan_hash == record["acquisition_plan"]["plan_hash"]
    # Zero retained bytes: both units are fresh downloads, never double counted.
    resume = runner.resume_state(roots, record)
    restart = runner.classify(roots, record, resume, "p02")
    assert restart["counts"]["fresh_download"] == 2
    assert (
        restart["counts"]["resumable_partial"] == restart["counts"]["local_processing_retry"] == 0
    )
    assert restart["resumable_verified_bytes"] == 0
    records = [runner.load_plan(roots, s) for s in roots.sequences()]
    assert runner.resolution(roots, records) == {1: [], 2: [0, 1]}
    status = runner.sufficiency(roots)
    assert status["status"] == "INCOMPLETE" and status["unresolved_ranks"] == {"2": [0, 1]}
    assert status["next_cursor"] == 2
    with pytest.raises(runner.RunError, match="repaired by a later plan"):
        runner.load_authorized(roots, 1)
    assert record["expected"]["transfer_bytes"] == sum(VIEWS["general"][4])


def test_a_repair_under_unchanged_limits_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    fixed = planner.build_plan(**arguments("general"))
    repaired = planner.Repaired(
        plan=fixed,
        authorized_digest=fixed["digest"],
        sealed_ranks=(),
        sealed_canonical_bytes=0,
        accounting_digest="4" * 64,
        failures=(FAILURE,),
        retained={},
    )
    with pytest.raises(planner.PlanError, match="resume the plan instead"):
        planner.build_repair_plan(**arguments("general"), repaired=repaired)


# -------------------------------------------------------------- supersession


def test_planning_p01_is_superseded_without_being_edited_or_runnable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    roots = roots_of(tmp_path, "planning")
    first = pre_fix("planning", monkeypatch)
    runner.store_plan(roots, first)
    before = tree(roots, 1)
    cli = load_cli()
    superseded = cli.superseded_of(roots, 1)
    record = planner.build_plan(**arguments("planning"), superseded=superseded)
    runner.store_plan(roots, record)
    assert tree(roots, 1) == before == {"plan.json": before["plan.json"]}
    assert record["sequence"] == 2 and "repair" not in record
    assert record["supersedes"]["plan_digest"] == first["digest"]
    assert record["lineage"]["previous_plan_digest"] == first["digest"]
    assert record["selection"]["start_rank"] == 0 and record["selection"]["next_cursor"] == 2
    assert record["supersedes"]["changed_sections"] == ["limits"]
    assert record["supersedes"]["changed_limits"]["max_file_bytes"] == {
        "from": 1_413_480_448,
        "to": math.ceil(max(VIEWS["planning"][4]) / MIB) * MIB,
    }
    planner.check_selected_file_bounds(record, inventory("planning"))
    with pytest.raises(planner.PlanError):
        planner.check_selected_file_bounds(first, inventory("planning"))
    with pytest.raises(runner.RunError, match="superseded by plan 2"):
        runner.authorize(roots, 1, first["digest"], "tester", admitted)
    with pytest.raises(runner.RunError, match="superseded by plan 2"):
        runner.load_authorized(roots, 1)
    assert not (roots.plan_dir(1) / "authorization.json").exists()
    records = [runner.load_plan(roots, s) for s in roots.sequences()]
    assert runner.resolution(roots, records) == {1: [], 2: [0, 1]}
    status = runner.sufficiency(roots)
    assert status["unresolved_ranks"] == {"2": [0, 1]}
    assert status["supersessions"] == [{"sequence": 2, "supersedes_sequence": 1}]
    # The superseding plan itself is an ordinary, unauthorized plan.
    assert not (roots.plan_dir(2) / "authorization.json").exists()


def test_supersession_refuses_run_plans_repairs_and_identical_replans(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cli = load_cli()
    fixed = planner.build_plan(**arguments("planning"))
    with pytest.raises(planner.PlanError, match="reproduce the unauthorized plan"):
        planner.build_plan(**arguments("planning"), superseded=planner.Superseded(fixed))
    roots, _, record, _ = general_repair(tmp_path, monkeypatch)
    with pytest.raises(cli.DriverError, match="only the latest plan"):
        cli.superseded_of(roots, 1)
    with pytest.raises(planner.PlanError, match="never superseded"):
        planner.build_plan(**arguments("general"), superseded=planner.Superseded(record))
    (roots.plan_dir(2) / "events.jsonl").write_text("{}\n")
    with pytest.raises(cli.DriverError, match="never superseded"):
        cli.superseded_of(roots, 2)
    other = roots_of(tmp_path / "x", "planning")
    runner.store_plan(other, pre_fix("planning", monkeypatch))
    other.scratch("p01").mkdir(parents=True)
    (other.scratch("p01") / "f00000.state.json").write_text("{}")
    with pytest.raises(cli.DriverError, match="left work"):
        cli.superseded_of(other, 1)


def test_superseded_plan_is_bound_but_never_sealed_by_the_first_pass(
    world: World,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tokens = tokens_for_files(world, 2)
    key = (RUN_PIN["source_id"], RUN_PIN["view_id"])
    with monkeypatch.context() as patch:
        patch.setitem(planner.SOURCE_FILE_BOUNDS, key, (1, 1, "authored: too small"))
        first = unauthorized_plan(world, tokens)
    runner.store_plan(world.roots, first)
    superseded = load_cli().superseded_of(world.roots, 1)
    record = unauthorized_plan(world, tokens, superseded)
    runner.store_plan(world.roots, record)
    runner.authorize(world.roots, 2, record["digest"], "tester", admitted)
    report = world.run(sequence=2, download_workers=2, process_workers=2)
    assert report["outcome"]["status"] == "completed"
    assert runner.sufficiency(world.roots)["status"] == "SUFFICIENT"
    seal = runner.first_pass_seal(world.roots)
    assert seal["plans"][0] == {
        "sequence": 1,
        "digest": first["digest"],
        "superseded_by": 2,
    }
    assert seal["plans"][1]["supersedes"] == {"plan_sequence": 1, "plan_digest": first["digest"]}
    assert [(u["sequence"], u["rank"]) for u in seal["units"]] == [(2, 0), (2, 1)]
    assert json.loads((world.roots.plan_dir(1) / "plan.json").read_bytes()) == first


def unauthorized_plan(
    w: World, tokens: int, superseded: planner.Superseded | None = None
) -> dict[str, Any]:
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
    return planner.build_plan(
        source_key="ultrax",
        pin=RUN_PIN,
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
        admission=ADMISSION,
        superseded=superseded,
    )
