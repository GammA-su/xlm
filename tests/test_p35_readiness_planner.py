"""P35 pilot readiness: plan recoverability table, capacity worst case, input-cap diagnostic.

Pure planner functions over the checked-in §W draft and authored numbers (no
torch, no real artifacts, no files larger than a few bytes).
"""

from __future__ import annotations

import ast
import copy
import json
from pathlib import Path
from typing import Any

import pytest

from xlm.artifacts.manifest import identity_digest
from xlm.data.input_limits import (
    MAX_AGGREGATE_FROZEN_INPUT_BYTES,
    MAX_FROZEN_SHARD_INPUT_BYTES,
    MAX_SHARD_JSON_BYTES,
)
from xlm.experiments.science_pilot import (
    P35_RECOVERABILITY,
    Findings,
    SciencePilotConfig,
    capacity_plan,
    check_capacity,
    check_input_bytes,
    check_recoverability,
    frozen_input_report,
    schedules,
)

ROOT = Path(__file__).resolve().parents[1]
DRAFT = ROOT / "recipes/experiments/draft_science_v1_pilot_32m.yaml"
M = 1_000_000
GIB = 1024**3


def draft() -> dict[str, Any]:
    return json.loads(DRAFT.read_text(encoding="utf-8"))


def pilot_of(raw: dict[str, Any]) -> SciencePilotConfig:
    return SciencePilotConfig.model_validate(raw["science_pilot"])


def codes(findings: Findings) -> list[str]:
    return [b.code for b in findings.blockers]


# ------------------------------------------------------------ recoverability


def test_checked_in_draft_declares_the_v1_policy_and_stays_nonexecutable() -> None:
    raw = draft()
    assert raw["training"]["evaluation_recoverability"] == P35_RECOVERABILITY
    assert raw["science_pilot"]["status"] == "draft_nonexecutable"
    assert raw["authorization"] == {"state": "not_authorized", "plan_hash": None}
    for key in ("pool_artifact", "tokenizer_artifact", "exposure_plan"):
        assert raw["data"][key] is None
    assert raw["resources"]["profile_artifact"] is None
    assert raw["science_pilot"]["capacity"]["checkpoint_bytes"] is None


def test_draft_review_table_has_a_route_for_every_required_event() -> None:
    raw = draft()
    findings = Findings()
    rows = check_recoverability(raw, pilot_of(raw), findings)
    assert findings.blockers == []
    section = findings.preflight["recoverability"]
    assert section["status"] == "VERIFIED"
    assert section["evaluation_recovery_checkpoints"] == [
        "checkpoint@1000000",
        "checkpoint@4000000",
    ]
    table = {r["event"]: r for r in rows}
    assert len(table) == 10 and all(r["required"] for r in rows)
    expected = {
        "quick_lm@0": ("initial_milestone_checkpoint", "BARRIER"),
        "full_lm@0": ("initial_milestone_checkpoint", "BARRIER"),
        "search_benchmark@0": ("initial_milestone_checkpoint", "BARRIER"),
        "quick_lm@1000000": ("evaluation_recovery_checkpoint", "EXACT_CHECKPOINT"),
        "quick_lm@4000000": ("evaluation_recovery_checkpoint", "EXACT_CHECKPOINT"),
        "quick_lm@8000000": ("milestone_checkpoint", "EXACT_CHECKPOINT"),
        "quick_lm@16000000": ("milestone_checkpoint", "EXACT_CHECKPOINT"),
        "quick_lm@32000000": ("endpoint_milestone_checkpoint", "EXACT_CHECKPOINT"),
        "full_lm@32000000": ("endpoint_milestone_checkpoint", "EXACT_CHECKPOINT"),
        "search_benchmark@32000000": (
            "endpoint_milestone_checkpoint",
            "LIVE_ENDPOINT_THEN_FAIL_STOP",
        ),
    }
    assert {e: (r["state_retention"], r["recoverability"]) for e, r in table.items()} == expected
    review = schedules(raw, pilot_of(raw))
    assert review["recoverability"]["table"] == rows
    assert review["recoverability"]["table_digest"] == section["table_digest"]


def test_missing_policy_blocks_the_research_pilot_with_every_lost_event() -> None:
    raw = draft()
    del raw["training"]["evaluation_recoverability"]
    findings = Findings()
    check_recoverability(raw, pilot_of(raw), findings)
    assert "recoverability_policy_required" in codes(findings)
    lost = sorted(b.detail.split(":")[0] for b in findings.blockers
                  if b.code == "required_event_unrecoverable")  # fmt: skip
    assert lost == sorted(
        ["search_benchmark@0", "quick_lm@1000000", "quick_lm@4000000", "search_benchmark@32000000"]
    )
    assert findings.preflight["recoverability"]["status"] == "BLOCKED"


@pytest.mark.parametrize(
    ("part", "lost"),
    [
        ("initial_barrier", ["search_benchmark@0"]),
        ("recovery_checkpoints", ["quick_lm@1000000", "quick_lm@4000000"]),
        ("endpoint", ["search_benchmark@32000000"]),
    ],
)
def test_authored_fixtures_also_fail_closed_on_a_missing_route(part: str, lost: list[str]) -> None:
    raw = draft()
    raw["science_pilot"]["contract"] = "authored_fixture"  # not the §W research contract
    raw["training"]["evaluation_recoverability"][part] = "disabled"
    findings = Findings()
    check_recoverability(raw, pilot_of(raw), findings)
    assert "recoverability_policy_required" not in codes(findings)
    blocked = sorted(b.detail.split(":")[0] for b in findings.blockers)
    assert blocked == sorted(lost)


def test_recoverability_policy_is_bound_into_the_plan_hash() -> None:
    from xlm.experiments.plans import ExecutablePlan
    from xlm.experiments.snapshot import CodeSnapshot

    def plan_hash(config: dict[str, Any]) -> str:
        plan = ExecutablePlan(
            plan_version="2",
            plan_id="p",
            plan_hash="",
            draft_id="d",
            track="baseline",
            horizon_kind="k",
            resolved_config=config,
            code_snapshot=CodeSnapshot("1", "c" * 64, None, [], {}, 0),
            dependency_hash="d" * 64,
            seeds={},
            budget_valid_targets=32 * M,
            budget_max_seconds=3600.0,
            estimated_new_disk_gib=None,
            gpu_processes=1,
            evaluation_tier="search",
            checkpoint_every_valid_targets=16 * M,
            evaluation_every_valid_targets=16 * M,
            exposure={},
            cost_estimate={},
            storage_estimate_gib=None,
        )
        return identity_digest(plan.identity_payload())

    base = draft()
    changed = copy.deepcopy(base)
    changed["training"]["evaluation_recoverability"]["endpoint"] = "disabled"
    absent = copy.deepcopy(base)
    del absent["training"]["evaluation_recoverability"]
    hashes = {plan_hash(base), plan_hash(changed), plan_hash(absent)}
    assert len(hashes) == 3
    assert plan_hash(copy.deepcopy(base)) == plan_hash(base)


# ------------------------------------------------------------------ capacity


def with_capacity(raw: dict[str, Any], size: int | None) -> SciencePilotConfig:
    raw["science_pilot"]["capacity"] = {
        "checkpoint_size_source": {"kind": "measured_profile", "path": None},
        "evaluation_evidence_bytes": 64 * 1024**2,
        "cache_bytes": 0,
        "safety_margin_bytes": 0,
        "checkpoint_bytes": size,
    }
    return pilot_of(raw)


def test_capacity_counts_worst_case_recovery_states_and_transients() -> None:
    raw = draft()
    size = 600 * 1000**2
    report = capacity_plan(raw, with_capacity(raw, None), checkpoint_bytes=size, snapshot_bytes=0)
    steps = {s["event"]: s for s in report["publication_steps"]}
    assert report["evaluation_recovery_publications"] == 2
    assert steps["checkpoint@1000000"]["role"] == "evaluation_recovery"
    # 1M publication: t0 + 2 unplanned states retained.
    assert steps["checkpoint@1000000"]["retained_before_bytes"] == 3 * size
    # 8M publication: t0 + t1M + t4M (both evaluations failed: still pinned) + 2 unplanned.
    assert steps["checkpoint@8000000"]["retained_before_states"] == {
        "milestones": 1,
        "rolling_recovery": 0,
        "evaluation_recovery_worst_case": 2,
        "unplanned_recovery_bound": 2,
    }
    final = steps["checkpoint@32000000"]
    assert final["retained_before_bytes"] == 7 * size  # t0,t8,t16 + t1M,t4M + 2 unplanned
    assert final["transient_bytes"] == 2 * size  # serialization dir + store staging copy
    assert report["peak_checkpoint_bytes"] == 9 * size
    evidence = 64 * 1024**2
    assert report["job_directory_peak_bytes"] >= 9 * size + evidence


def test_capacity_without_recovery_checkpoints_is_the_m3_bound() -> None:
    raw = draft()
    del raw["training"]["evaluation_recoverability"]
    size = 600 * 1000**2
    report = capacity_plan(raw, with_capacity(raw, None), checkpoint_bytes=size, snapshot_bytes=0)
    assert report["peak_checkpoint_bytes"] == 7 * size
    assert report["evaluation_recovery_publications"] == 0


def test_insufficient_capacity_blocks_and_the_measured_size_is_mandatory() -> None:
    raw = draft()
    findings = Findings()
    assert check_capacity(raw, with_capacity(raw, 600 * 1000**2), findings, snapshot_bytes=0)
    assert findings.blockers == []  # 9 x 600 MB + bounds fits 8 GiB
    findings = Findings()
    # 8 x 1 GiB = 8 GiB < limit, but 9 x 1 GiB does not: the recovery states matter.
    check_capacity(raw, with_capacity(raw, GIB), findings, snapshot_bytes=0)
    assert "new_output_exceeds_limit" in codes(findings)
    findings = Findings()
    assert check_capacity(raw, with_capacity(raw, None), findings, snapshot_bytes=0) is None
    assert codes(findings) == ["capacity_unresolved"]


# ------------------------------------------------------- frozen-input caps


def test_caps_are_unchanged_and_the_resolver_uses_the_named_constants() -> None:
    assert MAX_FROZEN_SHARD_INPUT_BYTES == 2 * 1024**3
    assert MAX_AGGREGATE_FROZEN_INPUT_BYTES == 2 * 1024**3
    assert MAX_SHARD_JSON_BYTES == 8 * 1024**2
    source = (ROOT / "src/xlm/training/inputs.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    literal_caps = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.BinOp)
        and isinstance(node.right, ast.BinOp)
        and isinstance(node.left, ast.Constant)
        and node.left.value == 2
    ]
    assert literal_caps == []  # no second, silently diverging 2 GiB literal
    assert "MAX_AGGREGATE_FROZEN_INPUT_BYTES" in source
    assert "MAX_FROZEN_SHARD_INPUT_BYTES" in source


def sizes(tokens: dict[str, int], offsets: int = 1000) -> dict[str, dict[str, int]]:
    return {
        s: {
            "shard_manifest.json": 600,
            "manifest.json": 900,
            "tokens.bin": n,
            "offsets.jsonl": offsets,
            "shard_counters.json": 400,
        }
        for s, n in tokens.items()
    }


def test_input_report_names_the_sources_and_bytes_against_the_caps() -> None:
    small = frozen_input_report(sizes({"a": 10_000, "b": 20_000}))
    assert not small["exceeds_aggregate_cap"] and small["sources_exceeding_shard_cap"] == []
    assert small["aggregate_bytes"] == (10_000 + 1000 + 600 + 400) + (20_000 + 1000 + 600 + 400)
    # Authored numbers only: 12 sources of 200 MiB tokens exceed the aggregate cap.
    big = frozen_input_report(sizes({f"s{i:02d}": 200 * 1024**2 for i in range(12)}))
    assert big["exceeds_aggregate_cap"]
    assert big["aggregate_excess_bytes"] == big["aggregate_bytes"] - 2 * 1024**3
    assert big["sources_exceeding_shard_cap"] == []
    one = frozen_input_report(sizes({"huge": 2 * 1024**3, "ok": 1}))
    assert one["sources_exceeding_shard_cap"] == ["huge"]
    json_heavy = sizes({"a": 1})
    json_heavy["a"]["shard_counters.json"] = MAX_SHARD_JSON_BYTES + 1
    assert frozen_input_report(json_heavy)["per_source"]["a"]["oversized_json"] == [
        "shard_counters.json"
    ]


def test_input_check_stats_real_files_without_reading_them(tmp_path: Path) -> None:
    shard = tmp_path / "alpha"
    shard.mkdir()
    for name, payload in (("tokens.bin", b"\x00" * 64), ("offsets.jsonl", b"{}\n")):
        (shard / name).write_bytes(payload)
    raw = {"data": {"sources": {"alpha": str(shard)}}}
    findings = Findings()
    report = check_input_bytes(raw, findings)
    assert report is not None and findings.blockers == []
    assert report["per_source"]["alpha"]["files"] == {"offsets.jsonl": 3, "tokens.bin": 64}
    assert findings.preflight["input_bytes"]["status"] == "VERIFIED"


def test_input_check_blocks_without_raising_the_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    import xlm.experiments.science_pilot as module

    monkeypatch.setattr(
        module,
        "frozen_input_sizes",
        lambda sources: sizes({s: 1024**3 for s in sources}),  # authored stat results
    )
    findings = Findings()
    module.check_input_bytes({"data": {"sources": {"a": "/x/a", "b": "/x/b"}}}, findings)
    [blocker] = findings.blockers
    assert blocker.code == "frozen_input_cap_exceeded"
    assert "a=" in blocker.detail and "b=" in blocker.detail and "2,147,483,648" in blocker.detail
    assert findings.preflight["input_bytes"]["caps"]["aggregate_bytes"] == 2 * 1024**3
