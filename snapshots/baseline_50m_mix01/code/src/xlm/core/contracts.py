"""Core domain contracts, protocols, and data structures for XLM."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class RunStatus(StrEnum):
    """Explicit state machine states for experiment runs."""

    DRAFT = "DRAFT"
    PLANNED = "PLANNED"
    AUTHORIZED = "AUTHORIZED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    INTERRUPTED = "INTERRUPTED"
    CANCELLED = "CANCELLED"
    BLOCKED = "BLOCKED"


LEGAL_RUN_TRANSITIONS: dict[RunStatus, set[RunStatus]] = {
    RunStatus.DRAFT: {RunStatus.PLANNED, RunStatus.CANCELLED},
    RunStatus.PLANNED: {RunStatus.AUTHORIZED, RunStatus.CANCELLED, RunStatus.BLOCKED},
    RunStatus.AUTHORIZED: {RunStatus.RUNNING, RunStatus.CANCELLED, RunStatus.BLOCKED},
    RunStatus.RUNNING: {
        RunStatus.SUCCEEDED,
        RunStatus.FAILED,
        RunStatus.INTERRUPTED,
        RunStatus.CANCELLED,
        RunStatus.BLOCKED,
    },
    RunStatus.INTERRUPTED: {RunStatus.RUNNING, RunStatus.CANCELLED},
    RunStatus.BLOCKED: {RunStatus.PLANNED, RunStatus.CANCELLED},
    RunStatus.SUCCEEDED: set(),
    RunStatus.FAILED: set(),
    RunStatus.CANCELLED: set(),
}


def validate_run_transition(current_state: RunStatus, next_state: RunStatus) -> None:
    """Validate that a run state transition is legally permissible."""
    allowed = LEGAL_RUN_TRANSITIONS.get(current_state, set())
    if next_state not in allowed:
        raise ValueError(
            f"Illegal run state transition: cannot transition from {current_state.value} "
            f"to {next_state.value}. Allowed transitions: {[s.value for s in allowed]}"
        )


@dataclass(frozen=True)
class CanonicalDocument:
    """Canonical document record complying with Contract C02."""

    doc_id: str
    source_id: str
    source_revision: str
    source_file: str
    source_row: int
    raw_hash: str
    clean_hash: str
    text: str
    utf8_byte_count: int
    language: str
    language_confidence: float
    document_kind: str
    source_metadata: dict[str, Any]
    parent_ids: list[str]
    license_reference: str
    transform_log: list[dict[str, Any]]
    quality_reasons: list[str]
    cluster_ids: dict[str, str]
    split: str

    def __post_init__(self) -> None:
        computed_bytes = len(self.text.encode("utf-8"))
        if self.utf8_byte_count != computed_bytes:
            raise ValueError(
                f"utf8_byte_count mismatch for doc '{self.doc_id}': "
                f"declared {self.utf8_byte_count} != actual {computed_bytes}"
            )
        if self.split not in ("train", "diagnostic_val", "audit"):
            raise ValueError(
                f"Invalid split '{self.split}' for doc '{self.doc_id}'. "
                "Must be one of: 'train', 'diagnostic_val', 'audit'"
            )

    def to_dict(self) -> dict[str, Any]:
        """Serialize record to dictionary."""
        return asdict(self)


@dataclass(frozen=True)
class TokenShardManifest:
    """Manifest for an immutable token array shard complying with Contract C07."""

    shard_id: str
    source_id: str
    num_tokens: int
    num_documents: int
    token_dtype: str  # 'uint16' | 'uint32'
    endianness: str  # 'little' | 'big'
    tokenizer_hash: str
    pool_hash: str
    checksum_sha256: str
    offsets_checksum_sha256: str
    byte_coverage_ratio: float

    def __post_init__(self) -> None:
        if self.token_dtype not in ("uint16", "uint32"):
            raise ValueError(
                f"Invalid token_dtype: {self.token_dtype}. Must be 'uint16' or 'uint32'"
            )
        if self.endianness not in ("little", "big"):
            raise ValueError(f"Invalid endianness: {self.endianness}. Must be 'little' or 'big'")
        if not (0.0 <= self.byte_coverage_ratio <= 1.0):
            raise ValueError(
                f"byte_coverage_ratio must be in [0, 1], got {self.byte_coverage_ratio}"
            )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class InferenceInput:
    """Strict inference-only model input excluding training targets and exposure metadata."""

    input_ids: Any
    attention_mask: Any | None = None
    position_ids: Any | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "input_ids": self.input_ids,
            "attention_mask": self.attention_mask,
            "position_ids": self.position_ids,
        }


@dataclass
class TrainingBatch:
    """Training batch container with input IDs, shifted labels, and loss masks."""

    input_ids: Any
    labels: Any  # Pre-shifted target positions
    loss_mask: Any  # 1 for valid next-token target positions, 0 for padding/ignored
    position_ids: Any | None = None
    segment_ids: Any | None = None
    source_attribution: list[str] | Any | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_inference_view(self) -> InferenceInput:
        """Extract an inference-only view, strictly stripping labels, loss masks, and metadata."""
        return InferenceInput(
            input_ids=self.input_ids,
            attention_mask=self.loss_mask,
            position_ids=self.position_ids,
        )


@dataclass
class LMOutput:
    """Standardized output from a model forward pass complying with Contract C08.

    Target-dependent loss is strictly excluded from LMOutput.
    """

    logits: Any
    auxiliary_outputs: dict[str, Any] = field(default_factory=dict)
    state: Any | None = None


@dataclass(frozen=True)
class LossResult:
    """Result of an objective evaluation complying with Contract C09."""

    loss_protocol: str  # 'token_additive' | 'custom_batch'
    loss: Any
    unscaled_loss_sum: float
    valid_target_denominator: int
    diagnostics: dict[str, float] = field(default_factory=dict)
    auxiliary_loss: float = 0.0

    def __post_init__(self) -> None:
        if self.loss_protocol not in ("token_additive", "custom_batch"):
            raise ValueError(
                f"Invalid loss_protocol: '{self.loss_protocol}'. "
                "Must be 'token_additive' or 'custom_batch'"
            )
        if self.loss_protocol == "token_additive" and self.valid_target_denominator < 0:
            raise ValueError("valid_target_denominator cannot be negative")


@dataclass(frozen=True)
class ModelCapabilities:
    """Declared capabilities of an architecture plugin."""

    supports_kv_cache: bool = False
    supports_cross_document_attention: bool = True
    supports_bidirectional: bool = False
    max_context_length: int = 512
    custom_state: bool = False


@dataclass(frozen=True)
class RunPlan:
    """Executable plan record capturing frozen behavioral identity and resource limits."""

    plan_id: str
    experiment_id: str
    resolved_config_hash: str
    code_hash: str
    dependency_hash: str
    seed_policy: Mapping[str, int]
    resource_caps: Mapping[str, Any]
    created_at: str
    status: RunStatus = RunStatus.PLANNED

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "experiment_id": self.experiment_id,
            "resolved_config_hash": self.resolved_config_hash,
            "code_hash": self.code_hash,
            "dependency_hash": self.dependency_hash,
            "seed_policy": dict(self.seed_policy),
            "resource_caps": dict(self.resource_caps),
            "created_at": self.created_at,
            "status": self.status.value,
        }


@dataclass(frozen=True)
class EvaluationReceipt:
    """Complete versioned evaluation receipt complying with Contract C11."""

    receipt_id: str
    checkpoint_hash: str
    tokenizer_hash: str
    dataset_revision: str
    split_id: str
    task_source_and_scorer_version: str
    prompt_template_version: str
    metric_normalization_policy: str
    context_truncation_policy: str
    precision_mode: str
    metrics: Mapping[str, float]
    scored_items_count: int
    holdout_mode: str  # 'search' | 'confirmation' | 'isolated_operator'
    created_at: str

    def __post_init__(self) -> None:
        if self.holdout_mode not in ("search", "confirmation", "isolated_operator"):
            raise ValueError(
                f"Invalid holdout_mode '{self.holdout_mode}'. "
                "Must be one of: 'search', 'confirmation', 'isolated_operator'"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "receipt_id": self.receipt_id,
            "checkpoint_hash": self.checkpoint_hash,
            "tokenizer_hash": self.tokenizer_hash,
            "dataset_revision": self.dataset_revision,
            "split_id": self.split_id,
            "task_source_and_scorer_version": self.task_source_and_scorer_version,
            "prompt_template_version": self.prompt_template_version,
            "metric_normalization_policy": self.metric_normalization_policy,
            "context_truncation_policy": self.context_truncation_policy,
            "precision_mode": self.precision_mode,
            "metrics": dict(self.metrics),
            "scored_items_count": self.scored_items_count,
            "holdout_mode": self.holdout_mode,
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class ComparisonContract:
    """Pairwise comparison contract complying with Contract C12."""

    contract_id: str
    track: str  # 'architecture' | 'loss' | 'optimizer' | 'tokenizer'
    baseline_run_id: str
    candidate_run_id: str
    allowed_differences: tuple[str, ...]
    materiality_threshold: float
    frozen_fields_verified: bool

    def __post_init__(self) -> None:
        if self.track not in ("architecture", "loss", "optimizer", "tokenizer"):
            raise ValueError(f"Invalid comparison track: '{self.track}'")

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_id": self.contract_id,
            "track": self.track,
            "baseline_run_id": self.baseline_run_id,
            "candidate_run_id": self.candidate_run_id,
            "allowed_differences": list(self.allowed_differences),
            "materiality_threshold": self.materiality_threshold,
            "frozen_fields_verified": self.frozen_fields_verified,
        }
