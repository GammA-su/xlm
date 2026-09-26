"""P35 M3: the non-executable science-v1 pilot draft, plan generation and validation.

The checked-in §W draft is exercised as-is (it must stay DRAFT). Full
resolution uses an ``authored_fixture`` toy pilot built from generated text;
it is never the 32M pilot. Freezing a plan binds the installed CUDA-extra
runtime, so those tests are marked ``cuda``; none of them allocates a GPU.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
import torch

from p35_m3_support import (
    ROOT,
    authored_bindings,
    authored_pilot_draft,
    build_inputs,
    fixture_size_checkpoint,
)
from xlm.experiments.authorization import issue_ticket
from xlm.experiments.plans import ExecutablePlan, PlanError, resolve_experiment_plan
from xlm.experiments.science_pilot import (
    P35_PILOT_EXPECTATION,
    Findings,
    PilotBindings,
    PilotStatus,
    SciencePilotConfig,
    capacity_plan,
    check_capacity,
    group_half_problems,
    plan_science_pilot,
    validate_science_pilot_plan,
)

DRAFT = ROOT / "recipes/experiments/draft_science_v1_pilot_32m.yaml"
BUDGET = 4096
needs_cuda_env = pytest.mark.skipif(
    not torch.cuda.is_available(), reason="freezing binds the CUDA-extra runtime"
)


def codes(result: Any) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for blocker in result.review["blockers"]:
        found.setdefault(blocker["code"], []).append(blocker["detail"])
    return found


def write_json(path: Path, payload: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


# ------------------------------------------------------------ checked-in draft


def test_checked_in_draft_is_visibly_nonexecutable(tmp_path: Path) -> None:
    result = plan_science_pilot(DRAFT, workspace_root=ROOT, artifact_home=tmp_path)
    assert result.status is PilotStatus.DRAFT and result.plan is None
    review = result.review
    assert review["status"] == "DRAFT" and review["research"] is True
    assert review["preflight"]["contract"]["status"] == "VERIFIED"
    assert review["preflight"]["configuration"]["status"] == "VERIFIED"
    assert review["preflight"]["configuration"]["parameters"] == 49_883_648
    assert set(codes(result)) == {"unresolved"}
    assert codes(result)["unresolved"] == [
        "data.pool_artifact",
        "data.tokenizer_artifact",
        "data.exposure_plan",
        "resources.profile_artifact",
        "data.sources (one verified shard per mix01 component)",
        "science_pilot.document_order.manifest",
        "science_pilot.document_order.order_manifest_id",
        "science_pilot.document_order.canonical_membership_id",
        "science_pilot.storage_roots.data_root",
        "science_pilot.storage_roots.checkpoint_root",
        "science_pilot.storage_roots.temp_root",
        "science_pilot.storage_roots.evaluation_input_roots",
        "science_pilot.storage_roots.output_root",
        "science_pilot.capacity.checkpoint_size_source",
        "science_pilot.capacity.evaluation_evidence_bytes",
        "science_pilot.capacity.cache_bytes",
        "science_pilot.capacity.safety_margin_bytes",
        "science_pilot.tokenizer.artifact_digest",
        "science_pilot.tokenizer.fit_input_hash",
        "science_pilot.evaluation.quick_lm",
        "science_pilot.evaluation.full_lm",
        "science_pilot.evaluation.search_benchmark",
        "science_pilot.evaluation.scoring",
        "science_pilot.benchmarks.group_assignments (HellaSwag/PIQA halves)",
        "search benchmark BLiMP universe (complete subdataset list)",
        "operator authorization ticket for the concrete plan hash",
    ]
    assert review["commands"]["launch"] == "BLOCKED"
    assert "authorize" not in review["commands"] and "submit" not in review["commands"]
    assert review["plan"] is None
    assert list(tmp_path.iterdir()) == []  # planning a draft writes nothing


def test_draft_states_the_exact_p35_pilot(tmp_path: Path) -> None:
    raw = json.loads(DRAFT.read_text(encoding="utf-8"))
    pilot = SciencePilotConfig.model_validate(raw["science_pilot"])
    assert pilot.expected == P35_PILOT_EXPECTATION
    assert pilot.status == "draft_nonexecutable" and pilot.device == "cuda"
    training = raw["training"]
    assert (training["init_seed"], training["training_seed"], training["data_seed"]) == (
        101,
        10001,
        20260918,
    )
    assert training["lr_policy"] == "target_endpoint_before_update_v1"
    assert training["runtime"]["attention_policy"] == "statistical_efficient_v1"
    assert training["producer_prefetch"] == "process_depth1"
    assert training["checkpoint_cadence"]["cadence"] == "pilot_32m"
    assert raw["resources"]["total_wall_seconds"] == 3600
    review = plan_science_pilot(DRAFT, workspace_root=ROOT, artifact_home=tmp_path).review
    schedules = review["schedules"]
    assert schedules["arithmetic"] == {
        "budget_valid_targets": 32_000_000,
        "global_batch_valid_targets": 65_536,
        "full_updates": 488,
        "final_update_targets": 18_432,
        "total_updates": 489,
    }
    checkpoints = [
        (c["planned_threshold"], c["projected_committed_targets"], c["projected_step"], c["role"])
        for c in schedules["checkpoints"]
    ]
    assert checkpoints == [
        (0, 0, 0, "milestone"),
        (8_000_000, 8_060_928, 123, "milestone"),
        (16_000_000, 16_056_320, 245, "milestone"),
        (32_000_000, 32_000_000, 489, "milestone"),
    ]
    evaluations = {e["event_id"]: e for e in schedules["evaluations"]}
    assert sorted(evaluations) == sorted(
        [f"quick_lm@{t}" for t in (0, 1_000_000, 4_000_000, 8_000_000, 16_000_000, 32_000_000)]
        + ["full_lm@0", "full_lm@32000000", "search_benchmark@0", "search_benchmark@32000000"]
    )
    assert evaluations["quick_lm@1000000"]["projected_committed_targets"] == 1_048_576
    # Only events at a planned checkpoint boundary can be rescored from an exact state.
    assert not evaluations["quick_lm@1000000"]["rescorable_from_planned_checkpoint"]
    assert not evaluations["quick_lm@4000000"]["rescorable_from_planned_checkpoint"]
    assert evaluations["quick_lm@8000000"]["rescorable_from_planned_checkpoint"]
    assert schedules["boundary_order"] == [
        "evaluation_crossing",
        "checkpoint",
        "evaluation_attempt",
    ]
    identity = review["scientific_identity"]
    assert identity["device"] == "cuda" and identity["microbatch_sequences"] == 8


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (lambda d: d["training"].update(training_seed=10002), "config_deviation"),
        (
            lambda d: d["training"]["schedule"].update(warmup_valid_targets=5_000_000),
            "config_deviation",
        ),
        (lambda d: d["training"].update(microbatch_sequences=16), "config_deviation"),
        (lambda d: d["optimizer"].update(weight_decay=0.0), "config_deviation"),
        (lambda d: d["resources"].update(total_wall_seconds=7200), "config_deviation"),
        (lambda d: d["resources"].update(max_new_disk_gib=12), "resource_limit_unset"),
        (lambda d: d["science_pilot"]["expected"].update(total_updates=490), "arithmetic_mismatch"),
        (
            lambda d: d["science_pilot"]["expected"]["seeds"].update(training_seed=1),
            "contract_deviation",
        ),
        (
            lambda d: d["science_pilot"]["benchmarks"].update(max_items_per_task=500),
            "contract_deviation",
        ),
    ],
)
def test_any_deviation_from_the_contract_is_blocked(tmp_path: Path, mutate: Any, code: str) -> None:
    draft = json.loads(DRAFT.read_text(encoding="utf-8"))
    mutate(draft)
    path = write_json(tmp_path / "draft.yaml", draft)
    result = plan_science_pilot(path, workspace_root=ROOT, artifact_home=tmp_path)
    assert code in codes(result), codes(result)


def test_the_generic_planner_cannot_bypass_pilot_preflight(tmp_path: Path) -> None:
    with pytest.raises(PlanError, match="operator bindings"):
        resolve_experiment_plan(DRAFT, workspace_root=ROOT, snapshot_dir=tmp_path / "snap")
    assert not (tmp_path / "snap").exists()


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        (("tokenizer", "artifact_digest"), "latest", "explicit identity"),
        (("tokenizer", "artifact_digest"), "ABC", "64-hex"),
        (("tokenizer", "artifact"), "tokenizers/current", "absolute"),
        (("data", "exposure_plan"), "/data/*.json", "wildcard"),
        (("profile_artifact",), "latest", "explicit identity"),
        (("storage_roots", "data_root"), "relative/root", "absolute"),
        # Placeholder strings posing as resolved values are unresolved, not identities.
        (("tokenizer", "artifact_digest"), "null", "explicit identity"),
        (("tokenizer", "fit_input_hash"), "TODO", "explicit identity"),
        (("data", "exposure_plan"), "None", "explicit identity"),
        (("storage_roots", "output_root"), " tbd ", "explicit identity"),
    ],
)
def test_bindings_refuse_latest_wildcards_and_relative_paths(
    tmp_path: Path, path: tuple[str, ...], value: str, message: str
) -> None:
    inputs_like = {
        "version": "xlm-science-pilot-bindings-v1",
        "data": {
            "sources": {"a": str(tmp_path)},
            "exposure_plan": str(tmp_path / "e.json"),
            "pool_artifact": None,
        },  # fmt: skip
        "tokenizer": {
            "artifact": str(tmp_path),
            "artifact_digest": "0" * 64,
            "fit_input_hash": "f",
        },
        "evaluation": {
            "quick_lm": None,
            "full_lm": None,
            "search_benchmark": None,
            "scoring": {"forward_precision": "fp32", "logprob_dtype": "fp64", "rolling_stride": 4},
            "group_assignments": None,
        },  # fmt: skip
        "profile_artifact": "profile_1",
        "storage_roots": {
            k: str(tmp_path) for k in ("data_root", "checkpoint_root", "temp_root", "output_root")
        }  # fmt: skip
        | {"evaluation_input_roots": [str(tmp_path)]},
        "capacity": {
            "checkpoint_size_source": {"kind": "measured_profile", "path": None},
            "evaluation_evidence_bytes": 0,
            "cache_bytes": 0,
            "safety_margin_bytes": 0,
        },  # fmt: skip
    }
    PilotBindings.model_validate(inputs_like)  # the well-formed baseline is accepted
    broken = copy.deepcopy(inputs_like)
    target = broken
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(ValueError, match=message):
        PilotBindings.model_validate(broken)
    broken = copy.deepcopy(inputs_like)
    broken["extra"] = "anything"
    with pytest.raises(ValueError):
        PilotBindings.model_validate(broken)


def test_group_half_membership_uses_the_frozen_partition() -> None:
    from xlm.evaluation.suites import SuiteTier, partition_grouped_items

    records = [{"item_id": str(i), "group": f"g{i // 2}"} for i in range(20)]
    search = {r["item_id"] for r in partition_grouped_items(records, SuiteTier.SEARCH, "group")}
    confirmation = {r["item_id"] for r in records} - search
    assert search and confirmation and not search & confirmation
    assert group_half_problems(records, "group", set(sorted(search)[:3])) == ([], [])
    one = sorted(confirmation)[0]
    assert group_half_problems(records, "group", {one, "999"}) == (["999"], [one])


def test_capacity_counts_retained_new_and_staging_bytes() -> None:
    raw = json.loads(DRAFT.read_text(encoding="utf-8"))
    raw["science_pilot"]["capacity"] = {
        "checkpoint_size_source": {"kind": "measured_profile", "path": None},
        "evaluation_evidence_bytes": 64 * 1024**2,
        "cache_bytes": 0,
        "safety_margin_bytes": 0,
        "checkpoint_bytes": None,
    }
    pilot = SciencePilotConfig.model_validate(raw["science_pilot"])
    size = 600 * 1000**2
    report = capacity_plan(raw, pilot, checkpoint_bytes=size, snapshot_bytes=0)
    steps = {s["event"]: s for s in report["publication_steps"]}
    # Before the exact-32M publication: 3 pinned milestones + 2 unplanned recovery
    # states retained, plus the new checkpoint and its staging copy.
    assert steps["checkpoint@32000000"]["retained_before_bytes"] == 5 * size
    assert steps["checkpoint@32000000"]["transient_bytes"] == 2 * size
    assert report["peak_checkpoint_bytes"] == 7 * size
    assert report["job_directory_peak_bytes"] > 7 * size + 64 * 1024**2
    findings = Findings()
    raw["science_pilot"]["capacity"]["checkpoint_bytes"] = size
    pilot = SciencePilotConfig.model_validate(raw["science_pilot"])
    assert check_capacity(raw, pilot, findings, snapshot_bytes=0) is not None
    assert not findings.blockers  # 4.2 GB fits the 8 GiB pilot new-output limit
    raw["science_pilot"]["capacity"]["checkpoint_bytes"] = 2 * 1024**3
    findings = Findings()
    check_capacity(raw, SciencePilotConfig.model_validate(raw["science_pilot"]), findings,
                   snapshot_bytes=0)  # fmt: skip
    assert {b.code for b in findings.blockers} >= {"new_output_exceeds_limit"}
    raw["science_pilot"]["capacity"]["checkpoint_bytes"] = None
    findings = Findings()
    check_capacity(raw, SciencePilotConfig.model_validate(raw["science_pilot"]), findings,
                   snapshot_bytes=0)  # fmt: skip
    assert [b.code for b in findings.blockers] == ["capacity_unresolved"]


# ------------------------------------------------------------ authored pilot


@pytest.fixture
def authored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("XLM_HOME", str(home))
    inputs = build_inputs(tmp_path / "inputs", budget=BUDGET)
    size = fixture_size_checkpoint(home, inputs)
    draft = authored_pilot_draft(
        inputs, budget=BUDGET, milestones=[0, 2048, BUDGET], recovery=[1024, 3072],
        quick=[0, 2048], full=[BUDGET],
    )  # fmt: skip
    out = tmp_path / "out"
    out.mkdir()
    bindings = authored_bindings(inputs, home=home, output_root=out, size_source=str(size))
    return {
        "tmp": tmp_path,
        "home": home,
        "inputs": inputs,
        "draft": draft,
        "bindings": bindings,
        "out": out,
        "size": sum(p.stat().st_size for p in Path(size).rglob("*") if p.is_file()),
    }


def run_plan(case: dict[str, Any], name: str = "a", **edits: Any) -> Any:
    draft = copy.deepcopy(case["draft"])
    bindings = copy.deepcopy(case["bindings"])
    for key, value in edits.items():
        if key == "draft":
            value(draft)
        elif key == "bindings":
            value(bindings)
    draft_path = write_json(case["tmp"] / f"draft_{name}.yaml", draft)
    bindings_path = write_json(case["tmp"] / f"bindings_{name}.json", bindings)
    out = case["out"]
    return plan_science_pilot(
        draft_path,
        workspace_root=ROOT,
        bindings_path=bindings_path,
        snapshot_dir=out / f"snapshot_{name}",
        output_path=out / f"plan_{name}.json",
        review_path=out / f"review_{name}.json",
        artifact_home=case["home"],
    )


@needs_cuda_env
@pytest.mark.cuda
def test_authored_pilot_resolves_to_a_frozen_hash_bound_plan(authored: dict[str, Any]) -> None:
    result = run_plan(authored)
    assert result.status is PilotStatus.RESOLVED, codes(result)
    review = result.review
    statuses = {name: section["status"] for name, section in review["preflight"].items()}
    assert statuses == {
        "contract": "VERIFIED",
        "storage_roots": "VERIFIED",
        "resolution": "VERIFIED",
        "configuration": "VERIFIED",
        "tokenizer": "VERIFIED",
        "data": "VERIFIED",
        "document_order": "VERIFIED",
        "evaluation": "VERIFIED",
        "heldout_membership": "VERIFIED",
        "cold_data": "VERIFIED",
        "profile": "NOT_RUN",
        "checkpoint_size_source": "VERIFIED",
        "capacity": "VERIFIED",
    }
    plan = ExecutablePlan.load(authored["out"] / "plan_a.json")
    plan.validate_identity()
    assert plan.plan_hash == review["plan"]["plan_hash"] == result.plan.plan_hash
    pilot = plan.resolved_config["science_pilot"]
    assert pilot["status"] == "operator_resolved" and pilot["evaluation"] is None
    assert pilot["capacity"]["checkpoint_bytes"] == authored["size"]
    assert (
        plan.resolved_config["evaluation"]["science"]["quick_lm"]["manifest_id"]
        == (authored["inputs"]["inventories"]["quick"][1])
    )
    assert plan.storage_estimate_gib == review["capacity"]["storage_estimate_gib"]
    assert plan.cost_estimate["basis"] == "unmeasured_authored_fixture"
    assert review["preflight"]["cold_data"]["source_transitions_in_budget"] >= 2
    commands = review["commands"]
    assert plan.plan_hash in commands["authorize"]
    assert "--device cuda" in commands["submit"] and "--max-retries 1" in commands["submit"]
    assert review["authorization"]["state"] == "not_authorized"
    # The review is plain JSON (the CLI writes it with the repository's canonical writer).
    assert json.loads(json.dumps(review))["plan"]["plan_hash"] == plan.plan_hash


@needs_cuda_env
@pytest.mark.cuda
def test_plan_hash_is_stable_and_follows_scientific_inputs(authored: dict[str, Any]) -> None:
    first = run_plan(authored, "one")
    again = run_plan(authored, "two")
    assert first.status is again.status is PilotStatus.RESOLVED
    assert first.plan.plan_hash == again.plan.plan_hash

    def seed(draft: dict[str, Any]) -> None:
        draft["training"]["training_seed"] = 10002
        draft["science_pilot"]["expected"]["seeds"]["training_seed"] = 10002

    changed = run_plan(authored, "seed", draft=seed)
    assert changed.status is PilotStatus.RESOLVED
    assert changed.plan.plan_hash != first.plan.plan_hash

    def stride(bindings: dict[str, Any]) -> None:
        bindings["evaluation"]["scoring"]["rolling_stride"] = 3

    assert run_plan(authored, "stride", bindings=stride).plan.plan_hash != first.plan.plan_hash


@needs_cuda_env
@pytest.mark.cuda
def test_validation_never_authorizes_and_needs_the_operator_ticket(
    authored: dict[str, Any],
) -> None:
    result = run_plan(authored)
    plan_path = authored["out"] / "plan_a.json"
    plan = ExecutablePlan.load(plan_path)
    validated = validate_science_pilot_plan(
        plan, plan_path=plan_path, artifact_home=authored["home"]
    )
    assert validated.status is PilotStatus.RESOLVED, codes(validated)
    assert validated.review["authorization"]["state"] == "not_authorized"
    ticket = issue_ticket(
        plan_hash=plan.plan_hash,
        max_valid_targets=plan.budget_valid_targets,
        approver="m3-test-fixture",
        ticket_id="T-M3",
        max_train_seconds=plan.budget_max_seconds,
        max_new_disk_gib=plan.storage_estimate_gib,
    )
    executable = validate_science_pilot_plan(plan, ticket=ticket, artifact_home=authored["home"])
    assert executable.status is PilotStatus.EXECUTABLE
    assert executable.review["authorization"]["ticket_id"] == "T-M3"
    other = issue_ticket(plan_hash="f" * 64, max_valid_targets=BUDGET, approver="x",
                         ticket_id="T-other", max_new_disk_gib=0.5)  # fmt: skip
    wrong = validate_science_pilot_plan(plan, ticket=other, artifact_home=authored["home"])
    assert wrong.status is PilotStatus.BLOCKED and "ticket_does_not_authorize" in codes(wrong)
    unbounded = issue_ticket(plan_hash=plan.plan_hash, max_valid_targets=BUDGET, approver="x",
                             ticket_id="T-unbounded")  # fmt: skip
    assert validate_science_pilot_plan(
        plan, ticket=unbounded, artifact_home=authored["home"]
    ).status is (PilotStatus.BLOCKED)
    # Inputs changed after planning: the frozen identity no longer verifies.
    manifest = Path(authored["inputs"]["inventories"]["full"][0])
    (manifest.parent / "alpha.jsonl").write_text('{"doc_id": "x", "text": "changed"}\n')
    stale = validate_science_pilot_plan(plan, ticket=ticket, artifact_home=authored["home"])
    assert stale.status is PilotStatus.BLOCKED
    assert result.status is PilotStatus.RESOLVED


@needs_cuda_env
@pytest.mark.cuda
def test_cli_plan_and_validate_expose_states_and_exit_codes(
    authored: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from typer.testing import CliRunner

    from xlm.cli.main import app

    monkeypatch.chdir(ROOT)
    draft = write_json(authored["tmp"] / "cli_draft.yaml", authored["draft"])
    bindings = write_json(authored["tmp"] / "cli_bindings.json", authored["bindings"])
    out = authored["out"]
    runner = CliRunner()
    planned = runner.invoke(app, ["experiment", "plan", str(draft), "--bindings", str(bindings),
                                  "--output", str(out / "cli_plan.json"), "--review",
                                  str(out / "cli_review.json"), "--snapshot-dir",
                                  str(out / "cli_snapshot")])  # fmt: skip
    assert planned.exit_code == 0, planned.output
    assert "Science pilot:   RESOLVED" in planned.output
    assert json.loads((out / "cli_review.json").read_text(encoding="utf-8"))["status"] == "RESOLVED"
    ok = runner.invoke(app, ["experiment", "validate", str(out / "cli_plan.json")])
    assert ok.exit_code == 0, ok.output
    assert "Science pilot:   RESOLVED" in ok.output
    other = issue_ticket(plan_hash="e" * 64, max_valid_targets=BUDGET, approver="x",
                         ticket_id="T-cli", max_new_disk_gib=0.5)  # fmt: skip
    from xlm.experiments.authorization import save_ticket

    save_ticket(other, out / "wrong_ticket.json")
    refused = runner.invoke(app, ["experiment", "validate", str(out / "cli_plan.json"),
                                  "--ticket", str(out / "wrong_ticket.json")])  # fmt: skip
    assert refused.exit_code == 1 and "ticket_does_not_authorize" in refused.output
    drafted = runner.invoke(app, ["experiment", "plan", str(DRAFT)])
    assert drafted.exit_code == 0 and "Science pilot:   DRAFT" in drafted.output
    assert "Launch:          BLOCKED" in drafted.output


def test_missing_component_or_bad_pins_block_and_write_no_plan(authored: dict[str, Any]) -> None:
    def drop_zeta(bindings: dict[str, Any]) -> None:
        del bindings["data"]["sources"]["zeta"]

    missing = run_plan(authored, "missing", bindings=drop_zeta)
    assert missing.status is PilotStatus.BLOCKED and missing.plan is None
    assert any("zeta" in d for d in codes(missing)["execution_resolution"])
    assert not (authored["out"] / "plan_missing.json").exists()
    assert missing.review["commands"]["launch"] == "BLOCKED"

    def bad_digest(bindings: dict[str, Any]) -> None:
        bindings["tokenizer"]["artifact_digest"] = "0" * 64
        bindings["tokenizer"]["fit_input_hash"] = "not-the-fit"

    pinned = run_plan(authored, "pins", bindings=bad_digest)
    assert pinned.status is PilotStatus.BLOCKED
    assert len(codes(pinned)["tokenizer_unpinned"]) == 2


def test_every_path_must_stay_inside_its_declared_root(authored: dict[str, Any]) -> None:
    elsewhere = authored["tmp"] / "elsewhere"
    elsewhere.mkdir()

    def traversal(bindings: dict[str, Any]) -> None:
        root = Path(bindings["storage_roots"]["data_root"])
        bindings["data"]["exposure_plan"] = str(root / ".." / ".." / "elsewhere" / "e.json")
        bindings["storage_roots"]["evaluation_input_roots"] = [str(elsewhere)]
        bindings["storage_roots"]["checkpoint_root"] = str(elsewhere)

    result = run_plan(authored, "roots", bindings=traversal)
    found = codes(result)
    assert result.status is PilotStatus.BLOCKED
    assert "outside_data_root" in found and "outside_evaluation_roots" in found
    assert "checkpoint_root_not_honored" in found  # the queue writes under XLM_HOME/runs


def test_heldout_document_in_training_membership_blocks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("XLM_HOME", str(home))
    inputs = build_inputs(
        tmp_path / "inputs",
        budget=BUDGET,
        extra_training_docs={"alpha": [("val_alpha_1", "the harbor current turns at dusk")]},
    )
    case = {
        "tmp": tmp_path,
        "home": home,
        "inputs": inputs,
        "draft": authored_pilot_draft(
            inputs, budget=BUDGET, milestones=[0, BUDGET], recovery=[], quick=[0], full=[BUDGET]
        ),  # fmt: skip
        "out": tmp_path / "out",
    }
    case["out"].mkdir()
    size = fixture_size_checkpoint(home, inputs)
    case["bindings"] = authored_bindings(inputs, home=home, output_root=case["out"],
                                         size_source=str(size))  # fmt: skip
    result = run_plan(case, "heldout")
    assert "heldout_in_training" in codes(result)
    assert "val_alpha_1" in codes(result)["heldout_in_training"][0]


def test_capacity_shortfall_or_unmeasured_size_blocks(authored: dict[str, Any]) -> None:
    def margin(bindings: dict[str, Any]) -> None:
        bindings["capacity"]["safety_margin_bytes"] = 10**18

    assert "insufficient_capacity" in codes(run_plan(authored, "margin", bindings=margin))

    def unmeasured(bindings: dict[str, Any]) -> None:
        bindings["capacity"]["checkpoint_size_source"] = {"kind": "measured_profile", "path": None}

    blocked = run_plan(authored, "size", bindings=unmeasured)
    assert "checkpoint_size_unresolved" in codes(blocked)
    assert "capacity_unresolved" in codes(blocked)
