"""Development-tier benchmark events over declared local inputs (P35 §J, science-v1).

Search-tier events resolve only an explicitly declared local input manifest,
pinned by its content identity, verified against the tier's policy splits and,
for BLiMP, against the tier's whole-subdataset partition. The protected final
tier is unreachable here: no final authorization is ever constructed, a final
manifest or an ``isolated_final`` exposure class is refused, and nothing is
downloaded or resolved by default.

Coverage decides status. A run that scored fewer items than declared, lost a
task or subdataset, or changed task definitions is PARTIAL (or FAILED), never
COMPLETE, and a task that scored no items reports ``None``, never ``0.0``.
"""

from __future__ import annotations

import contextlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from xlm.artifacts.manifest import identity_digest
from xlm.evaluation.cadence import EventTier
from xlm.evaluation.lm_validation import LMScoringPolicy, LMValidationEvaluator
from xlm.evaluation.outcome import EvaluationContext, EvaluationOutcome, InvalidMetricError
from xlm.evaluation.receipts import AttemptOutcome
from xlm.evaluation.suites import (
    FinalAuthorizationRequiredError,
    SuiteTier,
    assert_blimp_tier_membership,
)

BENCHMARK_EVALUATOR_VERSION = "xlm-declared-benchmark-v1"
BOUNDARY_POLICY = "joint_prefix_match_v1"


class BenchmarkTierError(RuntimeError):
    """A benchmark input or tier request violates the development firewall."""


@dataclass(frozen=True)
class BenchmarkInputSpec:
    """Pinned local benchmark inputs for one developer tier."""

    inputs_path: Path
    manifest_id: str
    tier: SuiteTier
    blimp_universe: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.tier is SuiteTier.FINAL:
            raise FinalAuthorizationRequiredError(
                "science-v1 evaluation events never resolve the protected final tier"
            )
        if len(self.manifest_id) != 64:
            raise BenchmarkTierError("benchmark inputs must be pinned by a 64-hex manifest id")


def verify_tier_inputs(spec: BenchmarkInputSpec, *, harness_version: str | None) -> Any:
    """Load and verify pinned inputs; every check is fatal and nothing is fetched."""
    from xlm.evaluation.inputs import load_evaluation_inputs, verify_evaluation_inputs

    manifest = load_evaluation_inputs(spec.inputs_path)
    if manifest.manifest_id() != spec.manifest_id:
        raise BenchmarkTierError(
            f"benchmark input manifest {manifest.manifest_id()} differs from the pinned "
            f"{spec.manifest_id}"
        )
    if manifest.tier is not spec.tier:
        raise BenchmarkTierError(
            f"manifest tier '{manifest.tier}' is not the event's '{spec.tier}'"
        )
    if manifest.exposure_class == "isolated_final":
        raise BenchmarkTierError("isolated_final inputs are never a development event")
    verified = verify_evaluation_inputs(
        manifest, tier=spec.tier, harness_version=harness_version, final_authorization=None
    )
    blimp = [s.subdataset for s in manifest.selections if s.task == "blimp" and s.subdataset]
    if blimp:
        assert_blimp_tier_membership(blimp, spec.blimp_universe, spec.tier)
    return verified


class BenchmarkTierEvaluator:
    """Declared-input harness scoring for one developer tier."""

    tier = EventTier.SEARCH_BENCHMARK

    def __init__(self, spec: BenchmarkInputSpec, tokenizer: Any, policy: LMScoringPolicy) -> None:
        from xlm.evaluation.harness import (
            bind_verified_inputs_to_tasks,
            build_task_manager,
            harness_version,
            require_harness,
        )

        require_harness()
        self.spec = spec
        self.tokenizer = tokenizer
        self.policy = policy
        self.harness_version = harness_version()
        self.verified = verify_tier_inputs(spec, harness_version=self.harness_version)
        _, _, definitions = bind_verified_inputs_to_tasks(self.verified, build_task_manager(None))
        self.task_definitions = identity_digest(definitions)

    def identity(self) -> dict[str, Any]:
        manifest = self.verified.manifest
        return {
            "implementation": "xlm.evaluation.search_tier.BenchmarkTierEvaluator",
            "version": BENCHMARK_EVALUATOR_VERSION,
            "harness_version": self.harness_version,
            "tokenizer": self.tokenizer.fingerprint,
            "tier": str(self.spec.tier),
            "inputs": {
                "manifest_id": self.verified.manifest_id,
                "scope_kind": manifest.scope_kind,
                "scope_label": manifest.scope_label,
                "exposure_class": manifest.exposure_class,
                "declared_items": {
                    v.namespace: len(v.selection.item_ids) for v in self.verified.selections
                },
                "blimp_universe_digest": identity_digest(sorted(self.spec.blimp_universe)),
            },
            "task_definitions": self.task_definitions,
            "scoring": {
                "logprob_dtype": self.policy.logprob_dtype,
                "forward_precision": self.policy.forward_precision,
                "boundary_policy": BOUNDARY_POLICY,
                "context_length": self.policy.context_length,
                "runtime_limit": None,
            },
        }

    def evaluate(self, model: Any, *, device: str, context: EvaluationContext) -> EvaluationOutcome:
        import torch

        from xlm.evaluation.harness_runner import run_declared_input_suite
        from xlm.evaluation.likelihood import BoundaryPolicy

        autocast: Any = contextlib.nullcontext()
        if self.policy.forward_precision == "bf16_autocast":
            if device != "cuda":
                raise InvalidMetricError("bf16_autocast scoring requires a CUDA device")
            autocast = torch.autocast(device_type="cuda", dtype=torch.bfloat16)
        with autocast:
            evidence = run_declared_input_suite(
                model,
                self.tokenizer,
                self.verified,
                checkpoint_hash=context.model_state_digest,
                precision=self.policy.logprob_dtype,
                device=device,
                limit=None,
                batch_size=1,
                output_dir=None,
                boundary_policy=BoundaryPolicy.JOINT_PREFIX_MATCH_V1,
                use_evidence_cache=False,
                execution_provenance=context.provenance,
            )
        if evidence.identity.get("extra") is not None:
            definitions = dict(evidence.identity["extra"]).get("task_definitions")
            if definitions is not None and definitions != self.task_definitions:
                raise InvalidMetricError("task definitions changed after the event was planned")
        coverage = evidence.coverage
        tasks: dict[str, Any] = {}
        for name, task in sorted(evidence.tasks.items()):
            scored = int(task.scored_items)
            value = task.acc_norm if task.metric_name == "acc_norm" else task.acc
            tasks[name] = {
                "metric": task.metric_name,
                "value": value if scored > 0 else None,
                "scored_items": scored,
                "expected_items": task.expected_items,
                "chance": task.chance,
                "subdataset_scores": dict(task.subdataset_scores) if scored > 0 else {},
            }
        complete = bool(coverage.complete)
        index = evidence.index
        metrics = {
            "tasks": tasks,
            "declared_scope_index": index.index if (complete and index.complete) else None,
            "index_withheld_reasons": list(index.withheld_reasons),
            "covers_full_suite": bool(coverage.covers_full_suite),
            "research_eligible": bool(coverage.research_eligible()),
        }
        return EvaluationOutcome(
            AttemptOutcome.COMPLETE if complete else AttemptOutcome.PARTIAL,
            metrics,
            {"kind": "declared_benchmark", **coverage.to_dict()},
            extra_files={
                "evidence.json": json.dumps(evidence.to_dict(), sort_keys=True).encode("utf-8")
            },
            notes=tuple(evidence.notes),
        )


class EndpointConfirmationEvaluator:
    """Endpoint-only confirmation: confirmation LM plus optional confirmation benchmarks."""

    tier = EventTier.ENDPOINT_CONFIRMATION

    def __init__(
        self, lm: LMValidationEvaluator, benchmark: BenchmarkTierEvaluator | None = None
    ) -> None:
        if lm.tier is not EventTier.ENDPOINT_CONFIRMATION:
            raise BenchmarkTierError("confirmation requires a confirmation LM evaluator")
        if benchmark is not None and benchmark.spec.tier is not SuiteTier.CONFIRMATION:
            raise BenchmarkTierError("confirmation benchmarks must use confirmation-tier inputs")
        self.lm = lm
        self.benchmark = benchmark

    def identity(self) -> dict[str, Any]:
        return {
            "implementation": "xlm.evaluation.search_tier.EndpointConfirmationEvaluator",
            "lm": self.lm.identity(),
            "benchmark": self.benchmark.identity() if self.benchmark is not None else None,
        }

    def evaluate(self, model: Any, *, device: str, context: EvaluationContext) -> EvaluationOutcome:
        lm = self.lm.evaluate(model, device=device, context=context)
        parts = {"lm": lm}
        if self.benchmark is not None:
            parts["benchmark"] = self.benchmark.evaluate(model, device=device, context=context)
        complete = all(p.status is AttemptOutcome.COMPLETE for p in parts.values())
        return EvaluationOutcome(
            AttemptOutcome.COMPLETE if complete else AttemptOutcome.PARTIAL,
            {name: part.metrics for name, part in parts.items()},
            {name: part.coverage for name, part in parts.items()},
            extra_files={
                f"{name}_{file}": data
                for name, part in parts.items()
                for file, data in part.extra_files.items()
            },
        )
