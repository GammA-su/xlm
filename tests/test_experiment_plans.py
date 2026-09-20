"""Acceptance tests for P16 planning: snapshots, authorization, plans, sweeps, campaigns.

Offline and CPU-only. The toy campaign execution itself lives in test_queue.py;
these tests cover immutable planning inputs, exact plan identity, budget gates,
sweep determinism and campaign expansion with horizon rules.
"""

from __future__ import annotations

from fractions import Fraction
from pathlib import Path

import pytest
import yaml

from xlm.experiments.authorization import (
    AuthorizationError,
    AuthorizationTicket,
    check_ticket,
    issue_ticket,
    load_ticket,
    save_ticket,
    smoke_ticket,
    validate_against_ticket,
)
from xlm.experiments.campaigns import (
    CampaignError,
    campaign_hash,
    check_campaign_budget,
    expand_campaign,
    record_selection,
)
from xlm.experiments.plans import (
    BudgetHorizonError,
    ExecutablePlan,
    PlanError,
    check_budget_against_horizon,
    extend_plan_budget,
    resolve_experiment_plan,
)
from xlm.experiments.snapshot import (
    SnapshotError,
    capture_snapshot,
    load_snapshot,
    verify_snapshot,
)
from xlm.experiments.sweeps import (
    SweepError,
    expand_list,
    grid_sweep,
    mixture_weight_diff,
    random_mixture_proposals,
    simplex_mixture_proposals,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def make_tree(root: Path, *, secret: str | None = None) -> Path:
    """Build a minimal snapshot-capable workspace tree."""
    (root / "src" / "xlm").mkdir(parents=True, exist_ok=True)
    (root / "src" / "xlm" / "core.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / "recipes" / "experiments").mkdir(parents=True, exist_ok=True)
    (root / "recipes" / "experiments" / "demo.yaml").write_text("id: demo\n", encoding="utf-8")
    (root / "pyproject.toml").write_text("[project]\nname = 'demo'\n", encoding="utf-8")
    (root / "uv.lock").write_text("lock-bytes\n", encoding="utf-8")
    (root / "data").mkdir(exist_ok=True)
    (root / "data" / "big.bin").write_bytes(b"x" * 1024)
    if secret is not None:
        (root / "src" / "xlm" / secret).write_text("s3cret\n", encoding="utf-8")
    return root


# ------------------------------------------------------------------ snapshots


def test_snapshot_round_trip_and_immutability(tmp_path: Path) -> None:
    tree = make_tree(tmp_path / "ws")
    snapshot = capture_snapshot(tree, tmp_path / "snap")
    assert snapshot.code_hash
    assert (tmp_path / "snap" / "manifest.json").is_file()
    assert (tmp_path / "snap" / "code" / "src" / "xlm" / "core.py").is_file()
    # Data directories never enter the capture.
    assert not (tmp_path / "snap" / "code" / "data").exists()

    assert verify_snapshot(tree, snapshot) == []
    assert load_snapshot(tmp_path / "snap").code_hash == snapshot.code_hash

    (tree / "src" / "xlm" / "core.py").write_text("VALUE = 2\n", encoding="utf-8")
    diverged = verify_snapshot(tree, snapshot)
    assert len(diverged) == 1 and "core.py" in diverged[0]


def test_snapshot_refuses_secrets_fail_closed(tmp_path: Path) -> None:
    tree = make_tree(tmp_path / "ws", secret="api_token.py")
    with pytest.raises(SnapshotError, match="api_token.py"):
        capture_snapshot(tree, tmp_path / "snap")
    assert not (tmp_path / "snap" / "code").exists()


def test_snapshot_missing_tree_is_refused(tmp_path: Path) -> None:
    with pytest.raises(SnapshotError, match="not a directory"):
        capture_snapshot(tmp_path / "absent", tmp_path / "snap")


# -------------------------------------------------------------- authorization


def test_smoke_ticket_covers_only_smoke_scale() -> None:
    ticket = smoke_ticket("plan_hash_abc")
    assert ticket.smoke_tier is True
    assert check_ticket(ticket, "plan_hash_abc", 200_000) == []
    violations = check_ticket(ticket, "plan_hash_abc", 200_001)
    assert len(violations) == 1 and "200,001" in violations[0]


def test_ticket_binds_one_plan_hash() -> None:
    ticket = issue_ticket(
        plan_hash="aaa", max_valid_targets=10_000_000, approver="op", ticket_id="T-1"
    )
    assert check_ticket(ticket, "bbb", 100) != []
    assert check_ticket(ticket, "aaa", 10_000_001) != []


def test_ticket_round_trip_and_malformed_refusal(tmp_path: Path) -> None:
    ticket = issue_ticket(
        plan_hash="aaa",
        max_valid_targets=1_000_000,
        approver="op",
        ticket_id="T-2",
        max_train_seconds=60.0,
        max_new_disk_gib=1.0,
    )
    path = save_ticket(ticket, tmp_path / "t.json")
    assert load_ticket(path) == ticket
    (tmp_path / "bad.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(AuthorizationError, match="not valid JSON"):
        load_ticket(tmp_path / "bad.json")
    with pytest.raises(AuthorizationError, match="not found"):
        load_ticket(tmp_path / "missing.json")


def test_validate_against_ticket_applies_smoke_caps_by_default() -> None:
    plan = {"plan_hash": "h", "budget_valid_targets": 100, "gpu_processes": 1}
    assert validate_against_ticket(plan, None).smoke_tier is True
    big = {"plan_hash": "h", "budget_valid_targets": 10_000_000, "gpu_processes": 1}
    with pytest.raises(AuthorizationError, match="smoke caps"):
        validate_against_ticket(big, None)
    ticket: AuthorizationTicket | None = None
    with pytest.raises(AuthorizationError, match="smoke caps"):
        validate_against_ticket(big, ticket)


# --------------------------------------------------------------------- plans


def test_resolve_reports_blockers_instead_of_defaults(tmp_path: Path) -> None:
    from xlm.core.paths import ArtifactPaths

    plan = resolve_experiment_plan(
        REPO_ROOT / "recipes" / "experiments" / "baseline_50m.yaml",
        workspace_root=REPO_ROOT,
        artifact_paths=ArtifactPaths(root=tmp_path / "empty_store"),
        snapshot_dir=tmp_path / "snap",
    )
    codes = {b.code for b in plan.blockers}
    assert "missing_pool_artifact" in codes
    assert "missing_tokenizer_artifact" in codes
    assert "cost_unestimated" in codes
    assert plan.horizon_kind == "continuation_prefix"  # 128M budget < 1B horizon
    assert plan.budget_valid_targets == 128_000_000
    assert plan.exposure["weights"]["synth_en_explanations"] == pytest.approx(0.15)


def test_seed_and_source_changes_alter_plan_identity(tmp_path: Path) -> None:
    from xlm.core.paths import ArtifactPaths

    def resolve_with_seed(seed: int) -> str:
        draft = yaml.safe_load(
            (REPO_ROOT / "recipes" / "experiments" / "baseline_50m.yaml").read_text(
                encoding="utf-8"
            )
        )
        draft["training"]["data_seed"] = seed
        draft_path = tmp_path / f"draft_{seed}.yaml"
        draft_path.write_text(yaml.safe_dump(draft), encoding="utf-8")
        plan = resolve_experiment_plan(
            draft_path,
            workspace_root=REPO_ROOT,
            artifact_paths=ArtifactPaths(root=tmp_path / "store"),
            snapshot_dir=tmp_path / f"snap_{seed}",
        )
        return plan.plan_hash

    assert resolve_with_seed(1) != resolve_with_seed(2)


def test_plan_round_trips_through_json(tmp_path: Path) -> None:
    from xlm.core.paths import ArtifactPaths

    plan = resolve_experiment_plan(
        REPO_ROOT / "recipes" / "experiments" / "baseline_50m.yaml",
        workspace_root=REPO_ROOT,
        artifact_paths=ArtifactPaths(root=tmp_path / "store"),
        snapshot_dir=tmp_path / "snap",
    )
    path = plan.save(tmp_path / "plan.json")
    assert ExecutablePlan.load(path).plan_hash == plan.plan_hash


def test_budget_beyond_horizon_requires_a_fork() -> None:
    check_budget_against_horizon(100, 1000, is_fork=False)
    check_budget_against_horizon(100, 1000, is_fork=True)
    with pytest.raises(BudgetHorizonError, match="requires a fork|fork explicitly"):
        check_budget_against_horizon(1001, 1000, is_fork=False)
    check_budget_against_horizon(1001, 1000, is_fork=True)


def test_extend_plan_budget_forks_with_lineage(tmp_path: Path) -> None:
    from xlm.core.paths import ArtifactPaths

    plan = resolve_experiment_plan(
        REPO_ROOT / "recipes" / "experiments" / "baseline_50m.yaml",
        workspace_root=REPO_ROOT,
        artifact_paths=ArtifactPaths(root=tmp_path / "store"),
        snapshot_dir=tmp_path / "snap",
    )
    forked = extend_plan_budget(plan, 256_000_000)
    assert forked.plan_hash != plan.plan_hash
    assert forked.parent_plan_id == plan.plan_id
    assert forked.budget_valid_targets == 256_000_000
    assert forked.horizon_kind == "standalone"
    assert "256,000,000" in (forked.fork_reason or "")
    with pytest.raises(PlanError, match="must exceed"):
        extend_plan_budget(plan, plan.budget_valid_targets)


# --------------------------------------------------------------------- sweeps


def test_grid_sweep_counts_and_orders_deterministically() -> None:
    trials = grid_sweep({"lr": [0.01, 0.001], "seed": [1, 2]})
    assert len(trials) == 4
    assert trials[0] == {"lr": 0.01, "seed": 1}
    assert trials[-1] == {"lr": 0.001, "seed": 2}
    with pytest.raises(SweepError, match="above the cap"):
        grid_sweep({f"axis_{i}": [1, 2] for i in range(9)}, max_trials=256)


def test_random_proposals_are_deterministic_and_valid() -> None:
    first = random_mixture_proposals(["a", "b", "c"], 5, seed=42)
    second = random_mixture_proposals(["a", "b", "c"], 5, seed=42)
    assert first == second
    for proposal in first:
        assert abs(sum(proposal.values()) - 1.0) < 1e-9
        assert set(proposal) == {"a", "b", "c"}


def test_simplex_proposals_cover_the_lattice() -> None:
    proposals = simplex_mixture_proposals(["a", "b", "c"], granularity=Fraction(1, 2))
    assert len(proposals) == 6  # C(2+3-1, 2)
    assert {"a": 1.0, "b": 0.0, "c": 0.0} in proposals
    assert {"a": 0.5, "b": 0.5, "c": 0.0} in proposals
    with pytest.raises(SweepError, match="above the cap"):
        simplex_mixture_proposals(["a", "b", "c", "d"], granularity=Fraction(1, 16))


def test_explicit_lists_and_diffs() -> None:
    trials = expand_list([{"a": 1}, {"a": 2}])
    assert trials == [{"a": 1}, {"a": 2}]
    with pytest.raises(SweepError, match="empty"):
        expand_list([])
    diff = mixture_weight_diff({"a": 0.5, "b": 0.5}, {"a": 0.6, "c": 0.4})
    assert diff["a"] == pytest.approx(0.1)
    assert diff["b"] == pytest.approx(-0.5)
    assert diff["c"] == pytest.approx(0.4)


# ------------------------------------------------------------------ campaigns


def test_six_mixture_campaign_expands_with_gated_later_rounds() -> None:
    plan = expand_campaign(
        REPO_ROOT / "recipes" / "campaigns" / "data_search.yaml",
        workspace_root=REPO_ROOT,
    )
    mixtures = sorted({t.mixture_preset for t in plan.trials})
    assert mixtures == [
        "m1_less_synth",
        "m2_more_synth",
        "m3_more_pdfs",
        "m4_txt360_web",
        "m5_more_practical",
        "mix01",
    ]
    # round_0: 6 mixtures x 1 seed; later rounds all gated on selection.
    round_0 = [t for t in plan.trials if t.round_id == "round_0"]
    assert len(round_0) == 6
    assert all(t.blocked_reason is None for t in round_0)
    expected_50m = 6 * 128_000_000 + 12 * 512_000_000 + 18 * 1_000_000_000
    assert plan.tokens_by_size == {"50m": expected_50m, "150m": 18 * 1_000_000_000}
    assert plan.total_valid_targets == expected_50m + 18 * 1_000_000_000
    assert plan.total_trials == 6 + 12 + 18 + 18
    later = [t for t in plan.trials if t.round_id != "round_0"]
    assert later and all(t.blocked_reason for t in later)
    # Continuation horizon is shared; standalone rounds match their own budget.
    assert all(t.horizon_kind == "continuation_prefix" for t in round_0)
    assert all(t.schedule_horizon_valid_targets == 1_000_000_000 for t in round_0)
    standalone = [t for t in later if t.round_id in ("round_1", "round_2", "round_3")]
    assert all(t.horizon_kind == "standalone" for t in standalone)
    assert all(t.schedule_horizon_valid_targets == t.budget_valid_targets for t in standalone)


def test_selection_unblocks_a_later_round(tmp_path: Path) -> None:
    selections = record_selection(tmp_path / "sel.json", "round_1", ["mix01", "m1_less_synth"])
    plan = expand_campaign(
        REPO_ROOT / "recipes" / "campaigns" / "data_search.yaml",
        workspace_root=REPO_ROOT,
        selections=selections,
    )
    unblocked = [t for t in plan.trials if t.round_id == "round_1" and t.blocked_reason is None]
    # Two seeds per mixture.
    assert sorted(t.mixture_preset for t in unblocked) == [
        "m1_less_synth",
        "m1_less_synth",
        "mix01",
        "mix01",
    ]
    still_blocked = [
        t for t in plan.trials if t.round_id == "round_1" and t.blocked_reason is not None
    ]
    assert len(still_blocked) == 4 * 2  # other mixtures x 2 seeds


def test_campaign_plan_is_hash_stable_and_budget_gated() -> None:
    plan = expand_campaign(
        REPO_ROOT / "recipes" / "campaigns" / "data_search.yaml",
        workspace_root=REPO_ROOT,
    )
    assert campaign_hash(plan) == campaign_hash(plan)
    with pytest.raises(CampaignError, match="smoke cap"):
        check_campaign_budget(plan, None)
    ticket = issue_ticket(
        plan_hash=campaign_hash(plan),
        max_valid_targets=plan.total_valid_targets,
        approver="op",
        ticket_id="C-1",
    )
    check_campaign_budget(plan, ticket)
    short = issue_ticket(
        plan_hash=campaign_hash(plan),
        max_valid_targets=plan.total_valid_targets - 1,
        approver="op",
        ticket_id="C-2",
    )
    with pytest.raises(CampaignError, match="exceeds ticket"):
        check_campaign_budget(plan, short)
