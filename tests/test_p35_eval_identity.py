"""P35 M2: evaluation identity binds every scientifically material field.

A cache hit (adopting an earlier attempt instead of scoring) requires the exact
computation identity, so each field below must move it. Benchmark task-revision
identity is covered with the pinned harness in ``test_p35_eval_firewall.py``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import xlm.evaluation.lm_validation as lm_validation
from p35_eval_support import (
    FULL_DOCS,
    CodepointTokenizer,
    RecordingEvaluator,
    build_trainer,
    fixture_plan,
    write_inventory,
)
from xlm.artifacts.manifest import identity_digest
from xlm.evaluation.cadence import EventTier
from xlm.evaluation.lm_validation import (
    LMScoringPolicy,
    LMValidationEvaluator,
    load_pinned_inventory,
)
from xlm.tokenizers.byte import ByteTokenizer
from xlm.training.evaluation import EvaluationController

FULL = EventTier.FULL_LM


def evaluator(
    root: Path,
    *,
    tokenizer: Any = None,
    docs: dict | None = None,
    policy: LMScoringPolicy | None = None,
) -> LMValidationEvaluator:
    tokenizer = tokenizer or ByteTokenizer()
    path, manifest_id = write_inventory(root, docs or FULL_DOCS, tokenizer)
    return LMValidationEvaluator(
        FULL,
        load_pinned_inventory(
            path, manifest_id=manifest_id, tokenizer_fingerprint=tokenizer.fingerprint
        ),
        tokenizer,
        policy or LMScoringPolicy(context_length=8, rolling_stride=4),
    )


def digest(value: LMValidationEvaluator) -> str:
    return identity_digest(value.identity())


def test_evaluator_identity_moves_with_every_material_field(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base = digest(evaluator(tmp_path / "base"))
    assert digest(evaluator(tmp_path / "same")) == base  # location is not identity
    more_docs = {**FULL_DOCS, "prose": [*FULL_DOCS["prose"], ("p3", "one more item")]}
    edited = {**FULL_DOCS, "prose": [("p1", "the cat sat on the hat"), FULL_DOCS["prose"][1]]}
    variants = {
        "tokenizer": evaluator(tmp_path / "tok", tokenizer=CodepointTokenizer()),
        "item_coverage": evaluator(tmp_path / "items", docs=more_docs),
        "manifest_content": evaluator(tmp_path / "content", docs=edited),
        "logprob_precision": evaluator(
            tmp_path / "fp32",
            policy=LMScoringPolicy(context_length=8, rolling_stride=4, logprob_dtype="fp32"),
        ),
        "forward_precision": evaluator(
            tmp_path / "bf16",
            policy=LMScoringPolicy(
                context_length=8, rolling_stride=4, forward_precision="bf16_autocast"
            ),
        ),
        "rolling_window": evaluator(
            tmp_path / "stride", policy=LMScoringPolicy(context_length=8, rolling_stride=2)
        ),
    }
    digests = {name: digest(value) for name, value in variants.items()}
    assert base not in digests.values() and len(set(digests.values())) == len(digests)
    monkeypatch.setattr(lm_validation, "SCORING_POLICY_VERSION", "xlm-lm-scoring-v2")
    assert digest(evaluator(tmp_path / "v2")) != base
    monkeypatch.setattr(lm_validation, "EVALUATOR_VERSION", "xlm-native-lm-validation-v2")
    assert digest(evaluator(tmp_path / "impl")) != base


def test_computation_identity_binds_model_state_device_and_runtime(tmp_path: Path) -> None:
    recorder = RecordingEvaluator(EventTier.QUICK_LM)
    plan = fixture_plan(64, quick_lm=[16])
    trainer = build_trainer(tmp_path, evaluators={EventTier.QUICK_LM: recorder}, plan=plan)
    controller = trainer.evaluation
    assert isinstance(controller, EvaluationController)
    event = plan.events[0]
    one = identity_digest(controller.computation(trainer, event, "1" * 64))
    assert identity_digest(controller.computation(trainer, event, "1" * 64)) == one
    assert identity_digest(controller.computation(trainer, event, "2" * 64)) != one
    trainer.device = "cuda"  # identity only; nothing is executed here
    assert identity_digest(controller.computation(trainer, event, "1" * 64)) != one
    trainer.device = "cpu"
    policy = trainer.science.policy
    trainer.science.policy = type(policy)(
        policy.science_version,
        policy.lr_policy,
        policy.rng_policy,
        policy.training_seed,
        {**(policy.runtime or {}), "matmul_tf32": "enabled"},
    )
    assert identity_digest(controller.computation(trainer, event, "1" * 64)) != one


def test_receipts_carry_tier_threshold_actual_count_and_bindings(tmp_path: Path) -> None:
    ev = evaluator(tmp_path / "inputs")
    plan = fixture_plan(64, full_lm=[20])
    trainer = build_trainer(tmp_path, evaluators={FULL: ev}, plan=plan)
    trainer.train_step()
    trainer.train_step()  # C = 32 is the first boundary at or above 20
    assert trainer.evaluation is not None
    receipt = trainer.evaluation.canonical_receipts()["full_lm@20"]
    assert receipt is not None
    assert receipt["event"] == {
        "event_id": "full_lm@20",
        "tier": "full_lm",
        "planned_threshold": 20,
        "is_endpoint": False,
    }
    assert receipt["actual_committed_targets"] == 32 and receipt["step"] == 2
    assert receipt["model_state"]["kind"] == "in_memory_replica_v1"
    assert receipt["run"]["run_id"] == "m2_run"
    assert receipt["run"]["scientific_policy"]["science_version"] == "xlm-science-v1"
    assert receipt["evaluator"]["tokenizer"] == ByteTokenizer().fingerprint
    assert receipt["evaluator"]["inputs"]["manifest_id"] == ev.inventory.manifest_id
    assert receipt["computation"]["evaluator"] == identity_digest(ev.identity())
    assert receipt["coverage"]["complete"] is True
    assert receipt["canonical_rule"] == "first_complete_attempt_v1"
    rebuilt = {k: v for k, v in receipt.items() if k != "receipt_identity"}
    assert identity_digest(rebuilt) == receipt["receipt_identity"]
