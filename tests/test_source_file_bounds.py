"""Source-specific whole-file row/canonical ceilings and repairs with zero sealed units.

Offline, authored fixtures. A plan whose every unit is unresolved after a
row-count failure is repaired as a whole: the repair re-plans both ranks under
the changed ceilings, reuses the retained complete source with zero network,
resumes the predecessor's verified partial from its prefix, keeps the cursor,
and the seal counts each rank once.
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any

import pytest

from test_source_plan import PIN, build, layout
from test_source_repair import repair, repaired, repaired_of, runner_admitted, snapshot
from test_source_run import (  # noqa: F401 - pytest fixtures
    ROWS_PER_FILE,
    World,
    loopback_only,
    served,
    tokens_for_files,
    world,
)
from xlm.data.acquisition import source_plan as planner
from xlm.data.acquisition import source_run as runner
from xlm.data.acquisition.source_local import SourceAdaptError
from xlm.data.acquisition.source_parquet import load_durable_source

KEY = (PIN["source_id"], PIN["view_id"])
CANONICAL = 10**9


def test_finewiki_bounds_are_explicit_and_other_sources_keep_theirs() -> None:
    assert set(planner.SOURCE_FILE_BOUNDS) == {("finewiki", "en")}
    rows, canonical, basis = planner.SOURCE_FILE_BOUNDS[("finewiki", "en")]
    # ceil(1.35x) the largest observed: 446,535 rows; 2,733,162,301 canonical bytes.
    assert rows == math.ceil(446_535 * 1.35) and canonical == math.ceil(2_733_162_301 * 1.35)
    assert basis.startswith("finewiki-file-v1")
    record, record_basis = planner.SOURCE_RECORD_BYTES[("finewiki", "en")]
    assert record == 18 * 1024**2 >= 1.35 * 13_566_858 and record_basis.startswith(
        "finewiki-record-v1"
    )
    assert planner.SOURCE_RECORD_BYTES[("finepdfs_edu", "eng_Latn")][0] == 48 * 1024**2
    assert (planner.MAX_RECORD_BYTES, planner.ROWS_TOLERANCE) == (8 * 1024**2, 2.0)
    policy, limits = planner.plan_limits(2, layout(), planner.TransportMode.WHOLE_FILE_LOCAL, PIN)
    assert "file_bounds_basis" not in policy and "max_record_bytes_basis" not in policy
    assert policy["max_rows_per_file"] == math.ceil(layout().rows_per_file * 2.0)
    assert limits.max_records == 2 * policy["max_rows_per_file"]


def test_file_bounds_change_only_rows_and_canonical(monkeypatch: pytest.MonkeyPatch) -> None:
    mode = planner.TransportMode.WHOLE_FILE_LOCAL
    before, _ = planner.plan_limits(2, layout(), mode, PIN)
    old = build()
    monkeypatch.setitem(planner.SOURCE_FILE_BOUNDS, KEY, (777, CANONICAL, "authored"))
    after, limits = planner.plan_limits(2, layout(), mode, PIN)
    changed = {k for k in after if after.get(k) != before.get(k)}
    assert changed == {"max_rows_per_file", "max_canonical_bytes_per_file", "file_bounds_basis"}
    assert limits.max_records == limits.max_scanned_records == 2 * 777
    new = build()
    assert new["digest"] != old["digest"]
    assert new["acquisition_plan"]["plan_hash"] != old["acquisition_plan"]["plan_hash"]
    monkeypatch.delitem(planner.SOURCE_FILE_BOUNDS, KEY)
    assert build()["digest"] == old["digest"]  # the old identity reproduces byte-identically


def test_zero_sealed_repair_covers_every_rank(monkeypatch: pytest.MonkeyPatch) -> None:
    first = build()
    files = first["selection"]["files"]
    args = {"sealed_ranks": (), "sealed_canonical_bytes": 0}
    with pytest.raises(planner.PlanError, match="resume the plan instead"):
        repair(first, repaired=repaired(first, **args))
    monkeypatch.setitem(planner.SOURCE_FILE_BOUNDS, KEY, (999_999, CANONICAL, "authored"))
    record = repair(first, repaired=repaired(first, **args))
    assert record["selection"]["ranks"] == [int(e["rank"]) for e in files]
    assert record["selection"]["next_cursor"] == first["selection"]["next_cursor"]
    assert set(record["repair"]["changed_limits"]) == {
        "max_rows_per_file",
        "max_canonical_bytes_per_file",
    }
    assert record["expected"]["transfer_bytes"] == (len(files) - 1) * layout().file_bytes


def _make_partial(w: World, sequence: int, rank: int, name: str) -> int:
    """Turn one unit's retained download into a verified, resumable scratch partial."""
    raw = w.roots.raw_path(name)
    data = raw.read_bytes()
    raw.unlink()
    raw.with_name(raw.name + ".identity.json").unlink()
    label, key = f"p{sequence:02d}", f"f{rank:05d}"
    partial, state_path = (
        w.roots.scratch(label, f"{key}.parquet.part"),
        w.roots.scratch(label, f"{key}.state.json"),
    )
    state = json.loads(state_path.read_text())
    verified = len(data) // 2
    partial.write_bytes(data[:verified])
    state.update(
        complete=False,
        verified_bytes=verified,
        prefix_sha256=hashlib.sha256(data[:verified]).hexdigest(),
    )
    state.pop("sha256", None)
    state_path.write_text(json.dumps(state))
    return verified


def test_row_bound_failure_with_no_sealed_unit_is_repaired_and_sealed_once(
    world: World,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tokens = tokens_for_files(world, 2)
    monkeypatch.setitem(
        planner.SOURCE_FILE_BOUNDS, KEY, (ROWS_PER_FILE - 1, CANONICAL, "authored v1")
    )
    first = world.plan(tokens)
    names = [str(e["file"]) for e in first["selection"]["files"]]
    assert len(names) == 2 and first["selection"]["next_cursor"] == 2
    with pytest.raises(SourceAdaptError, match="row count exceeds the per-file bound"):
        world.run(download_workers=2, process_workers=2)
    assert runner.resume_state(world.roots, first)["receipts"] == []
    for name in names:  # the fixture retains both; make rank 0 a verified partial
        assert load_durable_source(world.roots.raw_path(name)) is not None
    verified = _make_partial(world, 1, 0, names[0])
    restart = runner.classify(world.roots, first, runner.resume_state(world.roots, first), "p01")
    assert restart["units"]["local_processing_retry"] == ["f00001"]
    assert restart["units"]["resumable_partial"] == ["f00000"]
    status = runner.sufficiency(world.roots)
    assert status["status"] == "INCOMPLETE" and status["unresolved_ranks"] == {"1": [0, 1]}
    with pytest.raises(runner.RunError, match="INCOMPLETE"):
        runner.first_pass_seal(world.roots)
    totals = runner.account(world.roots, first)
    with pytest.raises(planner.PlanError, match="not completely sealed"):
        world.plan(tokens, planner.Predecessor(first, 0, 0, totals["digest"]))
    history = snapshot(world, 1)
    monkeypatch.setitem(
        planner.SOURCE_FILE_BOUNDS, KEY, (ROWS_PER_FILE * 2, CANONICAL, "authored v2")
    )
    gathered = repaired_of(world, 1)
    assert gathered.sealed_ranks == () and set(gathered.retained) == {names[1]}
    record = planner.build_repair_plan(
        source_key="ultrax",
        pin=PIN,
        requirement=first_requirement(first),
        inventory=world.inventory,
        inventory_sha256="3" * 64,
        layout=world.layout,
        calibration={"rows": "0" * 64},
        policy=world.policy,
        admission={"probe_fingerprint": "f" * 64, "bridge_receipt_digest": "b" * 64},
        repaired=gathered,
    )
    runner.store_plan(world.roots, record)
    assert record["selection"]["ranks"] == [0, 1] and record["selection"]["next_cursor"] == 2
    assert record["repair"]["changed_limits"]["max_rows_per_file"] == {
        "from": ROWS_PER_FILE - 1,
        "to": ROWS_PER_FILE * 2,
    }
    # Before the repair runs, p02 already sees p01's verified partial and retained source.
    restart = runner.classify(world.roots, record, runner.resume_state(world.roots, record), "p02")
    assert restart["units"]["local_processing_retry"] == ["f00001"]
    assert restart["units"]["resumable_partial"] == ["f00000"]
    assert restart["resumable_verified_bytes"] == verified
    with pytest.raises(runner.RunError, match="repaired by a later plan"):
        world.run(sequence=1)
    runner.authorize(world.roots, 2, record["digest"], "tester", runner_admitted)
    hits = world.served.hits(names[1])
    requests = len(world.served.requests)
    report = world.run(sequence=2, download_workers=2, process_workers=2)
    assert report["outcome"]["status"] == "completed"
    assert world.served.hits(names[1]) == hits  # retained complete source: zero network
    ranges = [r for _, n, r in world.served.requests[requests:] if n == names[0] and r]
    assert ranges and all(r.startswith(f"bytes={verified}-") for r in ranges)  # no prefix
    assert snapshot(world, 1) == history
    units = runner.resume_state(world.roots, record)["receipts"]
    assert sorted((u["rank"], u["file"]) for u in units) == [(0, names[0]), (1, names[1])]
    status = runner.sufficiency(world.roots)
    assert status["status"] == "SUFFICIENT" and status["next_cursor"] == 2
    seal = runner.first_pass_seal(world.roots)
    assert [(u["sequence"], u["rank"]) for u in seal["units"]] == [(2, 0), (2, 1)]


def first_requirement(first: dict[str, Any]) -> planner.Requirement:
    tokens = int(first["requirement"]["first_pass_tokens"])
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
    return planner.requirement_from(
        "ultrax_ultrafineweb", quotas, estimate, quotas_sha256="1" * 64, estimate_sha256="2" * 64
    )
