"""Authored fixtures shared by the P35 M2 evaluation tests (no real data, no network)."""

from __future__ import annotations

import json
import math
import random
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import torch

from xlm.artifacts.ledger import RunLedger
from xlm.artifacts.store import ArtifactStore
from xlm.config.schemas import (
    AdamWConfig,
    CrossEntropyObjectiveConfig,
    TransformerBaselineConfig,
    WarmupCosineScheduleConfig,
)
from xlm.config.science import ENDPOINT_LR_POLICY, SCIENCE_V1, ScientificPolicy
from xlm.core.contracts import InferenceInput, LMOutput, ModelCapabilities
from xlm.core.paths import ArtifactPaths
from xlm.data.normalization import canonical_normalize, compute_sha256
from xlm.evaluation.cadence import AUTHORED_FIXTURE, EventTier, build_plan
from xlm.evaluation.lm_validation import build_validation_manifest, save_validation_manifest
from xlm.evaluation.outcome import EvaluationContext, EvaluationOutcome
from xlm.evaluation.receipts import AttemptOutcome
from xlm.evaluation.state_digest import model_state_digest
from xlm.models.base import BaseModel
from xlm.models.parameter_counts import ParameterCounts
from xlm.models.transformer import TransformerBaseline
from xlm.objectives.cross_entropy import CrossEntropyObjective
from xlm.optimizers.adamw import create_adamw_optimizer
from xlm.schedules.cosine import WarmupCosineSchedule
from xlm.tokenizers.byte import ByteTokenizer
from xlm.training.checkpoint import CheckpointManager
from xlm.training.data import TrainingBatcher
from xlm.training.evaluation import EvaluationController
from xlm.training.science import ScientificState, reseed_training_rng
from xlm.training.trainer import Trainer

CPU_RUNTIME = {
    "attention_policy": "strict_deterministic_v1",
    "matmul_tf32": "disabled",
    "bf16_reduced_precision_reduction": "allowed",
}
VOCAB = 260
BETA = math.log(1000.0)  # successor logit: p(hit) = 1000 / (1000 + 259)


# --------------------------------------------------------------- scoring oracles


class CodepointTokenizer(ByteTokenizer):
    """One token per Unicode code point, so tokens differ from UTF-8 bytes."""

    @property
    def fingerprint(self) -> str:
        return compute_sha256("P35M2-CodepointTokenizer:v1")

    def encode_with_offsets(
        self, text: str, add_special_tokens: bool = False
    ) -> tuple[list[int], list[tuple[int, int]]]:
        clean = canonical_normalize(text)
        ids: list[int] = []
        offsets: list[tuple[int, int]] = []
        position = 0
        for char in clean:
            width = len(char.encode("utf-8"))
            ids.append(4 + ord(char) % 256)
            offsets.append((position, position + width))
            position += width
        return ids, offsets


class SuccessorModel(BaseModel):
    """logits[next = prev + 1] = BETA, all others 0: a hand-computable Markov oracle."""

    def __init__(self, context_length: int = 64) -> None:
        super().__init__()
        self.config = SimpleNamespace(context_length=context_length)
        self.anchor = torch.nn.Parameter(torch.zeros(1, dtype=torch.float64))

    def get_capabilities(self) -> ModelCapabilities:
        return ModelCapabilities(max_context_length=self.config.context_length)

    def count_parameters(self) -> ParameterCounts:
        return ParameterCounts(
            total_instantiated=1,
            unique_deployed=1,
            active=1,
            non_embedding=1,
            training_only=0,
            tied_parameters=0,
            formula_estimate=1,
            formula_matches=True,
            max_deployed_cap=None,
            cap_respected=True,
            device="cpu",
        )

    def forward(
        self,
        input_ids: torch.Tensor | InferenceInput,
        attention_mask: torch.Tensor | None = None,
        position_ids: torch.Tensor | None = None,
        state: Any | None = None,
        requested_outputs: dict[str, bool] | None = None,
    ) -> LMOutput:
        ids = input_ids.input_ids if isinstance(input_ids, InferenceInput) else input_ids
        logits = torch.zeros((*ids.shape, VOCAB), dtype=torch.float64, device=ids.device)
        logits.scatter_(-1, ((ids + 1) % VOCAB).unsqueeze(-1), BETA)
        return LMOutput(logits=logits + self.anchor)


def successor_nll(previous: int, target: int) -> float:
    """Closed form NLL of one target under :class:`SuccessorModel`."""
    log_z = math.log(math.exp(BETA) + VOCAB - 1)
    return log_z - BETA if target == (previous + 1) % VOCAB else log_z


def hand_document(tokenizer: Any, text: str) -> dict[str, float]:
    """Independent per-document sufficient statistics under the successor oracle."""
    ids, _ = tokenizer.encode_with_offsets(text)
    framed = [tokenizer.bos_token_id, *ids, tokenizer.eos_token_id]
    text_nll = sum(successor_nll(framed[i - 1], framed[i]) for i in range(1, len(ids) + 1))
    eos_nll = successor_nll(framed[-2], framed[-1])
    return {
        "text_nll": text_nll,
        "tokens": len(ids),
        "bytes": len(canonical_normalize(text).encode("utf-8")),
        "eos_nll": eos_nll,
    }


# ------------------------------------------------------------------ inventories


def write_inventory(
    directory: Path,
    domains: Mapping[str, Sequence[tuple[str, str]]],
    tokenizer: Any,
    *,
    split: str = "diagnostic_val",
    subset: str = "full",
    nested_in: str | None = None,
    scope_kind: str = "authored_fixture",
) -> tuple[Path, str]:
    """Write JSONL documents plus a manifest; return (manifest path, manifest id)."""
    directory.mkdir(parents=True, exist_ok=True)
    entries = []
    for domain, documents in domains.items():
        path = directory / f"{domain}.jsonl"
        path.write_bytes(
            "".join(
                json.dumps({"doc_id": doc_id, "text": text}, ensure_ascii=False) + "\n"
                for doc_id, text in documents
            ).encode("utf-8")
        )
        entries.append(
            {"domain_id": domain, "source_ids": [f"src_{domain}"], "documents_file": path.name}
        )
    manifest = build_validation_manifest(
        split=split,
        subset=subset,
        scope_kind=scope_kind,
        tokenizer_fingerprint=tokenizer.fingerprint,
        domains=entries,
        base_dir=directory,
        nested_in=nested_in,
    )
    path = save_validation_manifest(manifest, directory / "manifest.json")
    return path, manifest.manifest_id()


FULL_DOCS: dict[str, list[tuple[str, str]]] = {
    "prose": [("p1", "the cat sat on the mat"), ("p2", "abc abc")],
    "science": [("s1", "E = mc^2 holds"), ("s2", "é and ü are non-ASCII")],
}
QUICK_DOCS: dict[str, list[tuple[str, str]]] = {
    "prose": [("p1", "the cat sat on the mat")],
    "science": [("s1", "E = mc^2 holds")],
}


def dev_inventories(directory: Path, tokenizer: Any) -> dict[str, tuple[Path, str]]:
    full = write_inventory(directory / "full", FULL_DOCS, tokenizer)
    quick = write_inventory(
        directory / "quick", QUICK_DOCS, tokenizer, subset="quick", nested_in=full[1]
    )
    return {"full": full, "quick": quick}


# ----------------------------------------------------------------- evaluators


class RecordingEvaluator:
    """Returns a complete outcome and records every call; may consume every RNG."""

    def __init__(self, tier: EventTier, *, consume_rng: bool = False, label: str = "rec") -> None:
        self.tier = tier
        self.consume_rng = consume_rng
        self.label = label
        self.calls: list[dict[str, Any]] = []

    def identity(self) -> dict[str, Any]:
        return {"implementation": "tests.RecordingEvaluator", "label": self.label}

    def evaluate(self, model: Any, *, device: str, context: EvaluationContext) -> EvaluationOutcome:
        if self.consume_rng:
            random.random()
            np.random.rand(4)
            torch.rand(4)
            if torch.cuda.is_available():
                torch.rand(4, device="cuda")
        digest = model_state_digest(model)
        self.calls.append(
            {
                "event_id": context.event_id,
                "committed": context.actual_committed_targets,
                "digest": digest,
                "model_training": model.training,
            }
        )
        return EvaluationOutcome(
            AttemptOutcome.COMPLETE,
            {"replica_digest": digest, "calls": len(self.calls)},
            {"kind": "recording", "complete": True},
        )


class CallbackEvaluator(RecordingEvaluator):
    """Runs an arbitrary authored action before recording (failure/mutation fixtures)."""

    def __init__(self, tier: EventTier, action: Callable[[Any], Any], label: str = "cb") -> None:
        super().__init__(tier, label=label)
        self.action = action

    def evaluate(self, model: Any, *, device: str, context: EvaluationContext) -> EvaluationOutcome:
        result = self.action(model)
        if isinstance(result, EvaluationOutcome):
            return result
        return super().evaluate(model, device=device, context=context)


# ------------------------------------------------------------------- trainers


def science_state(seed: int = 10001, runtime: Mapping[str, str] | None = None) -> ScientificState:
    return ScientificState(
        ScientificPolicy.from_training(
            {
                "science_version": SCIENCE_V1,
                "lr_policy": ENDPOINT_LR_POLICY,
                "training_seed": seed,
                "runtime": dict(runtime or CPU_RUNTIME),
            }
        )
    )


def fixture_plan(budget: int, **thresholds: Sequence[int]) -> Any:
    return build_plan(
        AUTHORED_FIXTURE,
        budget,
        confirmation_registered=False,
        fixture_thresholds={k: list(v) for k, v in thresholds.items()},
    )


def default_tokens(budget: int) -> list[int]:
    return [4 + (i * 7) % 250 for i in range(2 * budget + 256)]


def build_trainer(
    root: Path,
    *,
    evaluators: Mapping[EventTier, Any] | None = None,
    plan: Any = None,
    budget: int = 64,
    global_batch: int = 16,
    context: int = 8,
    dropout: float = 0.0,
    init_seed: int = 7,
    tokens: list[int] | None = None,
    checkpoint_every: int | None = None,
    device: str = "cpu",
    runtime: Mapping[str, str] | None = None,
    science: bool = True,
    warmup: int = 32,
    horizon: int | None = None,
    run_id: str = "m2_run",
) -> Trainer:
    """A tiny science-v1 trainer; training RNG is reseeded like a fresh science run."""
    paths = ArtifactPaths(root=root)
    manager = CheckpointManager(
        artifact_store=ArtifactStore(paths),
        run_ledger=RunLedger(paths.ledger / "ledger.sqlite"),
        paths=paths,
    )
    config = TransformerBaselineConfig(
        vocab_size=VOCAB,
        num_layers=1,
        hidden_size=16,
        num_attention_heads=2,
        intermediate_size=32,
        context_length=context,
        attention_backend="eager",
        dropout=dropout,
    )
    model = TransformerBaseline(config, seed=init_seed).to(device)
    optimizer, manifest = create_adamw_optimizer(
        AdamWConfig(lr=1e-3, weight_decay=0.0), model=model
    )
    schedule = WarmupCosineSchedule(
        WarmupCosineScheduleConfig(
            warmup_valid_targets=warmup,
            horizon_valid_targets=horizon or max(budget, 64),
            min_lr_ratio=0.1,
        ),
        base_lr=1e-3,
    )
    batcher = TrainingBatcher(
        tokens if tokens is not None else default_tokens(budget),
        context_length=context,
        global_batch_valid_targets=global_batch,
    )
    state = science_state(runtime=runtime) if science else None
    controller = None
    if evaluators is not None:
        controller = EvaluationController(plan, evaluators)
        if state is not None:
            controller.attach(state)
    if state is not None:
        state.train_start_rng = reseed_training_rng(10001, device)
    return Trainer(
        model=model,
        objective=CrossEntropyObjective(CrossEntropyObjectiveConfig()),
        optimizer=optimizer,
        optimizer_manifest=manifest,
        schedule=schedule,
        batcher=batcher,
        checkpoint_manager=manager,
        run_id=run_id,
        plan_id="m2_plan",
        device=device,
        max_valid_targets=budget,
        checkpoint_every_valid_targets=checkpoint_every,
        science=state,
        evaluation=controller,
    )


def reload(trainer: Trainer, checkpoint: Path, **kwargs: Any) -> Any:
    meta = trainer.checkpoint_manager.load_checkpoint(
        checkpoint,
        model=trainer.model,
        objective=trainer.objective,
        optimizer=trainer.optimizer,
        optimizer_manifest=trainer.optimizer_manifest,
        schedule=trainer.schedule,
        batcher=trainer.batcher,
        device=trainer.device,
        science=trainer.science,
        **kwargs,
    )
    trainer.step = meta.step
    trainer.committed_valid_targets = meta.committed_valid_targets
    trainer.processed_valid_targets = meta.processed_valid_targets
    if trainer.checkpoint_every_valid_targets is not None:
        trainer.next_checkpoint_target = (
            meta.committed_valid_targets + trainer.checkpoint_every_valid_targets
        )
    return meta


def run_all(trainer: Trainer) -> None:
    while trainer.train_step() is not None:
        pass


def science_json(checkpoint: Path) -> dict[str, Any]:
    payload = json.loads((checkpoint / "science.json").read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def events_by_id(ledger_payload: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {entry["event_id"]: entry for entry in ledger_payload["events"]}
