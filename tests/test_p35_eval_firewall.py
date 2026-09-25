"""P35 M2: search-tier benchmark events, the protected-final firewall and coverage status.

Authored benchmark-shaped fixtures only (``fixtures/eval/inputs``); nothing is
downloaded and no official benchmark content is used. Tests that execute the
pinned harness are ``optional_dependency`` and need the installed ``eval`` extra.
"""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from typing import Any

import pytest
import yaml

from xlm.evaluation.cadence import EventTier
from xlm.evaluation.coverage import ObservedItem, expected_population, reconcile_coverage
from xlm.evaluation.evidence import EvaluationEvidence, TaskEvidence
from xlm.evaluation.inputs import (
    InputVerificationError,
    TierViolationError,
    load_evaluation_inputs,
)
from xlm.evaluation.lm_validation import (
    LMScoringPolicy,
    LMValidationEvaluator,
    load_pinned_inventory,
)
from xlm.evaluation.outcome import EvaluationContext
from xlm.evaluation.receipts import AttemptOutcome
from xlm.evaluation.search_tier import (
    BenchmarkInputSpec,
    BenchmarkTierError,
    EndpointConfirmationEvaluator,
    verify_tier_inputs,
)
from xlm.evaluation.suites import (
    FinalAuthorizationRequiredError,
    SplitFirewallError,
    SuiteTier,
    compute_four_task_index,
    partition_blimp_subdatasets,
)
from xlm.tokenizers.byte import ByteTokenizer

REPO = Path(__file__).resolve().parents[1]
FIXTURE = REPO / "fixtures" / "eval" / "inputs" / "dev_fixture_v1"
BLIMP = ("blimp_adjunct_island", "blimp_anaphor_gender_agreement")
HARNESS = "0.4.13"


def _key(name: str) -> str:
    return hashlib.sha256(f"20260918:{name}".encode()).hexdigest()


def universe(tier: SuiteTier) -> list[str]:
    """Ten names whose frozen partition puts both fixture subdatasets in ``tier``."""
    low, high = min(map(_key, BLIMP)), max(map(_key, BLIMP))
    fillers = (f"blimp_filler_{i:04d}" for i in range(10_000))
    if tier is SuiteTier.SEARCH:
        chosen = [f for f in fillers if _key(f) > high][:8]
    else:  # both fixture names sort last: the final share
        chosen = [f for f in fillers if _key(f) < low][:8]
    names = [*BLIMP, *chosen]
    assert set(BLIMP) <= set(partition_blimp_subdatasets(names)[tier])
    return names


@pytest.fixture
def manifest_path(tmp_path: Path) -> Path:
    shutil.copytree(FIXTURE, tmp_path / "inputs")
    return tmp_path / "inputs" / "manifest.yaml"


def spec(
    path: Path, *, tier: SuiteTier = SuiteTier.SEARCH, names: Any = None
) -> BenchmarkInputSpec:
    return BenchmarkInputSpec(
        inputs_path=path,
        manifest_id=load_evaluation_inputs(path).manifest_id(),
        tier=tier,
        blimp_universe=tuple(universe(SuiteTier.SEARCH) if names is None else names),
    )


def rewrite(path: Path, **changes: Any) -> None:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    selection_changes = changes.pop("selection", None)
    payload.update(changes)
    if selection_changes is not None:
        index, fields = selection_changes
        payload["selections"][index].update(fields)
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def test_search_tier_resolves_only_pinned_local_development_inputs(manifest_path: Path) -> None:
    verified = verify_tier_inputs(spec(manifest_path), harness_version=HARNESS)
    assert verified.manifest.tier is SuiteTier.SEARCH
    assert dict(verified.policy_splits) == {
        "arc_easy": "train",
        "hellaswag": "train",
        "piqa": "train",
        "blimp": "train",
    }
    assert all(v.resolved_path.is_relative_to(manifest_path.parent) for v in verified.selections)


def test_protected_final_is_unreachable_from_the_search_tier(
    manifest_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(FinalAuthorizationRequiredError):
        spec(manifest_path, tier=SuiteTier.FINAL)
    import xlm.evaluation.inputs as inputs_module

    seen: dict[str, Any] = {}
    real = inputs_module.verify_evaluation_inputs

    def capture(*args: Any, **kwargs: Any) -> Any:
        seen.update(kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr(inputs_module, "verify_evaluation_inputs", capture)
    verify_tier_inputs(spec(manifest_path), harness_version=HARNESS)
    assert seen["final_authorization"] is None and seen["tier"] is SuiteTier.SEARCH
    # A manifest labelled final can never pass as a search input.
    rewrite(manifest_path, tier="final")
    with pytest.raises(BenchmarkTierError, match="is not the event's"):
        verify_tier_inputs(spec(manifest_path), harness_version=HARNESS)


def test_final_splits_and_isolated_inputs_are_refused(manifest_path: Path) -> None:
    piqa = next(
        i
        for i, s in enumerate(yaml.safe_load(manifest_path.read_text("utf-8"))["selections"])
        if s["task"] == "piqa"
    )
    original = manifest_path.read_text(encoding="utf-8")
    rewrite(manifest_path, selection=(piqa, {"source_split": "validation"}))
    with pytest.raises(TierViolationError, match="evaluation policy assigns 'train'"):
        verify_tier_inputs(spec(manifest_path), harness_version=HARNESS)
    manifest_path.write_text(original, encoding="utf-8")
    rewrite(manifest_path, exposure_class="isolated_final", scope_kind="full_official_split")
    with pytest.raises(BenchmarkTierError, match="isolated_final"):
        verify_tier_inputs(spec(manifest_path), harness_version=HARNESS)


def test_blimp_subdatasets_outside_the_search_partition_are_refused(manifest_path: Path) -> None:
    with pytest.raises(SplitFirewallError, match="another tier's partition"):
        verify_tier_inputs(
            spec(manifest_path, names=universe(SuiteTier.FINAL)), harness_version=HARNESS
        )
    with pytest.raises(SplitFirewallError, match="declared full subdataset universe"):
        verify_tier_inputs(spec(manifest_path, names=()), harness_version=HARNESS)
    with pytest.raises(SplitFirewallError, match="not in the declared universe"):
        verify_tier_inputs(
            spec(manifest_path, names=universe(SuiteTier.SEARCH)[1:]), harness_version=HARNESS
        )


def test_unpinned_or_missing_local_inputs_fail(manifest_path: Path) -> None:
    good = spec(manifest_path)
    with pytest.raises(BenchmarkTierError, match="64-hex"):
        BenchmarkInputSpec(manifest_path, "latest", SuiteTier.SEARCH, good.blimp_universe)
    with pytest.raises(BenchmarkTierError, match="differs from the pinned"):
        verify_tier_inputs(
            BenchmarkInputSpec(manifest_path, "0" * 64, SuiteTier.SEARCH, good.blimp_universe),
            harness_version=HARNESS,
        )
    data = manifest_path.parent / "piqa.json"
    original = data.read_bytes()
    data.write_bytes(original.replace(b"syn", b"SYN", 1))
    with pytest.raises(InputVerificationError, match="does not match the declared"):
        verify_tier_inputs(good, harness_version=HARNESS)
    data.unlink()
    with pytest.raises(InputVerificationError, match="not a regular file"):
        verify_tier_inputs(good, harness_version=HARNESS)


def test_confirmation_events_accept_only_confirmation_tier_inputs(tmp_path: Path) -> None:
    from p35_eval_support import write_inventory

    tokenizer = ByteTokenizer()
    path, manifest_id = write_inventory(
        tmp_path, {"d": [("c1", "held out text")]}, tokenizer, split="lm_confirmation"
    )
    lm = LMValidationEvaluator(
        EventTier.ENDPOINT_CONFIRMATION,
        load_pinned_inventory(
            path, manifest_id=manifest_id, tokenizer_fingerprint=tokenizer.fingerprint
        ),
        tokenizer,
        LMScoringPolicy(context_length=8, rolling_stride=4),
    )

    class SearchBenchmark:
        spec = type("Spec", (), {"tier": SuiteTier.SEARCH})()

    with pytest.raises(BenchmarkTierError, match="confirmation-tier inputs"):
        EndpointConfirmationEvaluator(lm, SearchBenchmark())  # type: ignore[arg-type]
    assert EndpointConfirmationEvaluator(lm).identity()["benchmark"] is None


# ------------------------------------------------------------- pinned harness

harness = pytest.mark.usefixtures("installed_eval_runtime")


def tiny_model() -> Any:
    from xlm.config.schemas import TransformerBaselineConfig
    from xlm.models.transformer import TransformerBaseline

    return TransformerBaseline(
        TransformerBaselineConfig(
            vocab_size=260,
            num_layers=1,
            hidden_size=32,
            num_attention_heads=2,
            intermediate_size=64,
            context_length=128,
            attention_backend="eager",
        ),
        seed=42,
    ).eval()


def benchmark_evaluator(path: Path, names: Any = None) -> Any:
    from xlm.evaluation.search_tier import BenchmarkTierEvaluator

    return BenchmarkTierEvaluator(
        spec(path, names=names),
        ByteTokenizer(),
        LMScoringPolicy(context_length=128, rolling_stride=64),
    )


@pytest.mark.optional_dependency
@harness
def test_search_benchmark_completes_only_with_exact_declared_coverage(manifest_path: Path) -> None:
    evaluator = benchmark_evaluator(manifest_path)
    outcome = evaluator.evaluate(
        tiny_model(),
        device="cpu",
        context=EvaluationContext("search_benchmark@0", 0, "a" * 64, {"route": "test"}),
    )
    assert outcome.status is AttemptOutcome.COMPLETE
    assert outcome.coverage["complete"] is True and outcome.coverage["research_eligible"] is False
    assert all(task["value"] is not None for task in outcome.metrics["tasks"].values())
    assert outcome.metrics["declared_scope_index"] is not None
    assert "evidence.json" in outcome.extra_files
    identity = evaluator.identity()
    assert identity["inputs"]["manifest_id"] == load_evaluation_inputs(manifest_path).manifest_id()
    assert identity["harness_version"] == HARNESS and len(identity["task_definitions"]) == 64


@pytest.mark.optional_dependency
@harness
def test_task_revision_or_blimp_universe_changes_benchmark_identity(manifest_path: Path) -> None:
    from xlm.artifacts.manifest import identity_digest

    base = identity_digest(benchmark_evaluator(manifest_path).identity())
    extra = next(
        name
        for name in (f"blimp_extra_{i}" for i in range(1000))
        if _key(name) > max(map(_key, BLIMP))
    )
    other_universe = [*universe(SuiteTier.SEARCH), extra]
    assert set(BLIMP) <= set(partition_blimp_subdatasets(other_universe)[SuiteTier.SEARCH])
    widened = identity_digest(benchmark_evaluator(manifest_path, other_universe).identity())
    rewrite(manifest_path, selection=(0, {"source_revision": "f" * 40}))
    revised = identity_digest(benchmark_evaluator(manifest_path).identity())
    assert len({base, widened, revised}) == 3


@pytest.mark.optional_dependency
@harness
def test_incomplete_benchmark_coverage_remains_partial_and_never_zero(
    manifest_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import xlm.evaluation.harness_runner as runner

    evaluator = benchmark_evaluator(manifest_path)
    verified = evaluator.verified

    def truncated_run(*_args: Any, **_kwargs: Any) -> EvaluationEvidence:
        expected = expected_population(verified)
        observed = {
            s.task: [ObservedItem(s.namespace, i.split("#", 1)[1]) for i in s.item_ids[:-1]]
            for s in expected.selections
            if s.task != "arc_easy"
        }
        coverage = reconcile_coverage(expected, observed)
        tasks = {
            "arc_easy": TaskEvidence(
                "arc_easy", "v", "acc_norm", 0.0, 0.0, 0.25, 0, 0, expected_items=4
            ),
            "piqa": TaskEvidence("piqa", "v", "acc", 0.5, 0.5, 0.5, 3, 3, expected_items=4),
        }
        index = compute_four_task_index({k: v.score() for k, v in tasks.items()}, coverage=coverage)
        return EvaluationEvidence(
            "1", "f" * 64, {"extra": ()}, tasks, index, None, coverage=coverage
        )

    monkeypatch.setattr(runner, "run_declared_input_suite", truncated_run)
    outcome = evaluator.evaluate(
        tiny_model(), device="cpu", context=EvaluationContext("search_benchmark@0", 0, "a" * 64, {})
    )
    assert outcome.status is AttemptOutcome.PARTIAL
    assert outcome.coverage["complete"] is False and outcome.coverage["missing_tasks"] == [
        "arc_easy"
    ]
    assert outcome.metrics["tasks"]["arc_easy"]["value"] is None  # never a 0.0 score
    assert outcome.metrics["tasks"]["piqa"]["value"] == 0.5
    assert outcome.metrics["declared_scope_index"] is None
