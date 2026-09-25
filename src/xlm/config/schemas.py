"""Strict Pydantic schemas for XLM models, mixtures, components, and experiment recipes."""

from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from xlm.core.registry import (
    architectures,
    objectives,
    optimizers,
    schedules,
    source_adapters,
    tokenizers,
)


class StrictConfigModel(BaseModel):
    """Base model enforcing strict field presence and forbidding unknown keys."""

    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
    )

    @field_validator("*", mode="before")
    @classmethod
    def reject_non_finite_floats(cls, value: Any) -> Any:
        """Reject NaN, Infinity, and -Infinity values."""
        if isinstance(value, float):
            if math.isnan(value) or math.isinf(value):
                raise ValueError(
                    f"Non-finite float value '{value}' is forbidden in strict configuration"
                )
        return value


# -------------------------------------------------------------------------
# Architecture Schemas
# -------------------------------------------------------------------------


class TransformerBaselineConfig(StrictConfigModel):
    """Configuration for baseline causal Transformer complying with Contract C08."""

    architecture: Literal["transformer_baseline"] = "transformer_baseline"
    architecture_version: str = "1"
    vocab_size: int = Field(default=32768, gt=0)
    num_layers: int = Field(gt=0)
    hidden_size: int = Field(gt=0)
    num_attention_heads: int = Field(gt=0)
    intermediate_size: int = Field(gt=0)
    tie_embeddings: bool = True
    bias: bool = False
    dropout: float = Field(default=0.0, ge=0.0, le=1.0)
    normalization: str = "rmsnorm"
    position_encoding: str = "rope"
    ffn: str = "swiglu"
    expected_unique_parameters: int | None = Field(default=None, gt=0)
    max_deployed_parameters: int | None = Field(default=None, gt=0)
    context_length: int = Field(default=512, gt=0)
    attention_backend: str = "profile_required"
    initialization_policy: str = "baseline_v1"

    @model_validator(mode="after")
    def validate_architecture_dimensions(self) -> TransformerBaselineConfig:
        if self.hidden_size % self.num_attention_heads != 0:
            raise ValueError(
                f"hidden_size ({self.hidden_size}) must be divisible by "
                f"num_attention_heads ({self.num_attention_heads})"
            )
        head_dim = self.hidden_size // self.num_attention_heads
        if head_dim % 2 != 0:
            raise ValueError(
                f"head_dim ({head_dim} = {self.hidden_size} // {self.num_attention_heads}) "
                f"must be even for rotary position embeddings"
            )
        if self.attention_backend not in ("profile_required", "eager", "sdpa"):
            raise ValueError(
                f"Invalid attention_backend '{self.attention_backend}'. "
                "Must be one of: 'profile_required', 'eager', 'sdpa'"
            )
        if self.initialization_policy not in ("baseline_v1",):
            raise ValueError(
                f"Invalid initialization_policy '{self.initialization_policy}'. "
                "Must be 'baseline_v1'"
            )
        if self.normalization not in ("rmsnorm",):
            raise ValueError(f"Invalid normalization '{self.normalization}'. Must be 'rmsnorm'")
        if self.position_encoding not in ("rope",):
            raise ValueError(
                f"Invalid position_encoding '{self.position_encoding}'. Must be 'rope'"
            )
        if self.ffn not in ("swiglu",):
            raise ValueError(f"Invalid ffn '{self.ffn}'. Must be 'swiglu'")
        return self


class ModelPresetConfig(TransformerBaselineConfig):
    """Preset file wrapper matching recipes/models/*.yaml."""

    schema_version: int = 1
    kind: Literal["model_preset"] = "model_preset"
    id: str


# -------------------------------------------------------------------------
# Optimizer Schemas
# -------------------------------------------------------------------------


class AdamWConfig(StrictConfigModel):
    """AdamW optimizer configuration complying with Contract C09."""

    type: Literal["adamw"] = "adamw"
    version: str = "1"
    lr: float = Field(gt=0.0)
    betas: tuple[float, float] = (0.9, 0.95)
    eps: float = Field(default=1e-8, gt=0.0)
    weight_decay: float = Field(default=0.1, ge=0.0)
    decay_norms: bool = False
    decay_embeddings: bool = True

    @field_validator("betas")
    @classmethod
    def validate_betas(cls, v: Any) -> tuple[float, float]:
        if len(v) != 2:
            raise ValueError("betas must be a 2-tuple (beta1, beta2)")
        b1, b2 = float(v[0]), float(v[1])
        if not (0.0 <= b1 < 1.0 and 0.0 <= b2 < 1.0):
            raise ValueError(f"Invalid beta values: ({b1}, {b2})")
        return (b1, b2)


# -------------------------------------------------------------------------
# Schedule Schemas
# -------------------------------------------------------------------------


class WarmupCosineScheduleConfig(StrictConfigModel):
    """Warmup + cosine decay schedule complying with Contract C09."""

    type: Literal["warmup_cosine"] = "warmup_cosine"
    version: str = "1"
    counter: str = "committed_valid_targets"
    horizon_valid_targets: int = Field(gt=0)
    warmup_valid_targets: int = Field(ge=0)
    min_lr_ratio: float = Field(default=0.1, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def check_warmup_le_horizon(self) -> WarmupCosineScheduleConfig:
        if self.warmup_valid_targets > self.horizon_valid_targets:
            raise ValueError("warmup_valid_targets cannot exceed horizon_valid_targets")
        return self


class ConstantScheduleConfig(StrictConfigModel):
    """Constant learning rate fixture schedule complying with Contract C09."""

    type: Literal["constant"] = "constant"
    version: str = "1"
    counter: str = "committed_valid_targets"


# -------------------------------------------------------------------------
# Objective Schemas
# -------------------------------------------------------------------------


class CrossEntropyObjectiveConfig(StrictConfigModel):
    """Standard causal next-token cross entropy objective."""

    type: Literal["cross_entropy"] = "cross_entropy"
    version: str = "1"
    label_smoothing: float = Field(default=0.0, ge=0.0, le=1.0)


class NoOpObjectiveConfig(StrictConfigModel):
    """Pass-through no-op objective plugin configuration."""

    type: Literal["noop_objective"] = "noop_objective"
    version: str = "1"


# -------------------------------------------------------------------------
# Mixture Schemas
# -------------------------------------------------------------------------


class MixtureConfig(StrictConfigModel):
    """Data mixture preset schema complying with Contract C07."""

    schema_version: int = 1
    kind: Literal["mixture_preset"] = "mixture_preset"
    id: str
    status: str = "draft_unvalidated"
    description: str | None = None
    weight_unit: Literal["valid_target_tokens"] = "valid_target_tokens"
    weights: dict[str, float]
    missing_source_policy: Literal["error", "warn"] = "error"
    exhaustion_policy: Literal["error", "repeat_bounded"] = "error"
    allow_silent_renormalization: Literal[False] = False
    max_document_exposures: int = Field(default=1, gt=0)
    scheduler: str = "token_deficit_v1"
    source_seed: int = 20260918
    pool_artifact: str | None = None
    tokenizer_artifact: str | None = None

    @field_validator("weights")
    @classmethod
    def validate_weights(cls, w: dict[str, float]) -> dict[str, float]:
        if not w:
            raise ValueError("Mixture weights cannot be empty")
        for k, v in w.items():
            if v <= 0.0:
                raise ValueError(f"Weight for source '{k}' must be > 0.0, got {v}")
        return w


# -------------------------------------------------------------------------
# Experiment Sub-Specifications
# -------------------------------------------------------------------------


class BudgetConfig(StrictConfigModel):
    max_valid_targets: int = Field(gt=0)
    max_train_seconds: float | None = Field(default=None, gt=0.0)


class TrainingConfig(StrictConfigModel):
    device: Literal["cuda", "cpu"] = "cuda"
    precision: str = "bf16_fp32_master"
    context_length: int = Field(default=512, gt=0)
    global_batch_valid_targets: int = Field(default=65536, gt=0)
    microbatch_sequences: int | None = Field(default=None, gt=0)
    gradient_clip_norm: float = Field(default=1.0, gt=0.0)
    budget: BudgetConfig
    schedule: dict[str, Any]  # Validated via schedule registry
    activation_checkpointing: bool = False
    producer_prefetch: Literal["off", "process_depth1"] = "off"
    compile: bool = False
    init_seed: int = 101
    data_seed: int = 20260918
    checkpoint_every_valid_targets: int = Field(default=16000000, gt=0)


class EvaluationConfig(StrictConfigModel):
    suite: Literal["search", "confirmation", "final"] = "search"
    policy_artifact: str | None = None
    every_valid_targets: int = Field(default=16000000, gt=0)
    checkpoint_selection: str = "last_at_declared_budget"
    allow_final: bool = False


class ResourceConfig(StrictConfigModel):
    profile_artifact: str | None = None
    max_new_disk_gib: float | None = Field(default=None, gt=0.0)
    max_gpu_processes: int = Field(default=1, gt=0)


class AuthorizationConfig(StrictConfigModel):
    state: str = "not_authorized"
    plan_hash: str | None = None


# -------------------------------------------------------------------------
# Experiment Draft and Executable Plan Schemas
# -------------------------------------------------------------------------


class ExperimentDraftConfig(StrictConfigModel):
    """Draft experiment configuration matching recipes/experiments/*.yaml."""

    schema_version: int = 1
    kind: Literal["experiment_draft", "experiment_plan"] = "experiment_draft"
    id: str
    status: str = "draft_requires_artifacts_and_approval"
    track: str = "baseline"
    model: dict[str, Any]
    data: dict[str, Any]
    objective: dict[str, Any]
    optimizer: dict[str, Any]
    training: TrainingConfig
    evaluation: EvaluationConfig
    resources: ResourceConfig
    authorization: AuthorizationConfig
    plugins: list[str] = Field(default_factory=list, max_length=16)


class ExecutableExperimentPlanConfig(StrictConfigModel):
    """Fully resolved and executable experiment plan ready for execution."""

    schema_version: int = 1
    kind: Literal["experiment_plan"] = "experiment_plan"
    id: str
    status: Literal["planned", "authorized", "running"] = "planned"
    track: str = "baseline"
    model: TransformerBaselineConfig
    data: dict[str, Any]
    objective: dict[str, Any]
    optimizer: dict[str, Any]
    training: TrainingConfig
    evaluation: EvaluationConfig
    resources: ResourceConfig
    authorization: AuthorizationConfig

    # Additional executable provenance fields
    code_hash: str = Field(min_length=8)
    dependency_hash: str = Field(min_length=8)

    @field_validator("data")
    @classmethod
    def validate_executable_data(cls, d: dict[str, Any]) -> dict[str, Any]:
        # Required artifacts must be non-null and not placeholders
        for art_field in ("pool_artifact", "tokenizer_artifact"):
            val = d.get(art_field)
            if not val or val in ("latest", "TODO", "null"):
                raise ValueError(
                    f"Executable plan requires explicit, immutable '{art_field}', got '{val}'"
                )
        return d


# -------------------------------------------------------------------------
# Register Built-in Components
# -------------------------------------------------------------------------


def _load_transformer_baseline_factory(config: Any) -> Any:
    from xlm.models.transformer import create_transformer_baseline

    return create_transformer_baseline(config)


def _load_adamw_optimizer_factory(config: Any, **kwargs: Any) -> Any:
    from xlm.optimizers.adamw import create_adamw_optimizer

    return create_adamw_optimizer(config, **kwargs)


def _load_warmup_cosine_schedule_factory(config: Any, **kwargs: Any) -> Any:
    from xlm.schedules.cosine import create_warmup_cosine_schedule

    return create_warmup_cosine_schedule(config, **kwargs)


def _load_constant_schedule_factory(config: Any, **kwargs: Any) -> Any:
    from xlm.schedules.constant import create_constant_schedule

    return create_constant_schedule(config, **kwargs)


def _load_cross_entropy_objective_factory(config: Any, **kwargs: Any) -> Any:
    from xlm.objectives.cross_entropy import create_cross_entropy_objective

    return create_cross_entropy_objective(config)


def _load_noop_objective_factory(config: Any, **kwargs: Any) -> Any:
    from xlm.objectives.noop import create_noop_objective

    return create_noop_objective(config)


architectures.register(
    identifier="transformer_baseline",
    version="1",
    config_schema=TransformerBaselineConfig,
    capabilities={"supports_kv_cache": False, "supports_rope": True, "supports_swiglu": True},
    serializer_version="1",
    factory=_load_transformer_baseline_factory,
)

optimizers.register(
    identifier="adamw",
    version="1",
    config_schema=AdamWConfig,
    capabilities={"supports_weight_decay": True, "second_order": False},
    serializer_version="1",
    factory=_load_adamw_optimizer_factory,
)

schedules.register(
    identifier="warmup_cosine",
    version="1",
    config_schema=WarmupCosineScheduleConfig,
    capabilities={"deterministic": True},
    serializer_version="1",
    factory=_load_warmup_cosine_schedule_factory,
)

schedules.register(
    identifier="constant",
    version="1",
    config_schema=ConstantScheduleConfig,
    capabilities={"deterministic": True},
    serializer_version="1",
    factory=_load_constant_schedule_factory,
)

objectives.register(
    identifier="cross_entropy",
    version="1",
    config_schema=CrossEntropyObjectiveConfig,
    capabilities={"loss_protocol": "token_additive"},
    serializer_version="1",
    factory=_load_cross_entropy_objective_factory,
)

objectives.register(
    identifier="noop_objective",
    version="1",
    config_schema=NoOpObjectiveConfig,
    capabilities={"loss_protocol": "token_additive"},
    serializer_version="1",
    factory=_load_noop_objective_factory,
)


# -------------------------------------------------------------------------
# Tokenizer & Adapter Schemas & Registrations
# -------------------------------------------------------------------------


class ByteTokenizerConfig(StrictConfigModel):
    type: Literal["byte_fixture"] = "byte_fixture"
    version: str = "1"
    vocab_size: int = 260


class BPETokenizerConfig(StrictConfigModel):
    type: Literal["bpe"] = "bpe"
    version: str = "1"
    target_vocab_size: int = Field(default=32768, ge=260)
    max_train_docs: int = Field(default=100000, gt=0)
    max_train_bytes: int = Field(default=500 * 1024 * 1024, gt=0)
    is_production_baseline: bool = False


class TextAdapterConfig(StrictConfigModel):
    type: Literal["text"] = "text"
    version: str = "1"
    source_id: str
    source_revision: str = "local_snapshot"
    license_reference: str = "unknown"


class JsonlAdapterConfig(StrictConfigModel):
    type: Literal["jsonl"] = "jsonl"
    version: str = "1"
    source_id: str
    source_revision: str = "local_snapshot"
    license_reference: str = "unknown"
    text_field: str = "text"
    id_field: str = "id"
    split_field: str = "split"


def _load_byte_tokenizer_factory(config: Any) -> Any:
    from xlm.tokenizers.byte import ByteTokenizer

    if config.vocab_size != 260:
        raise ValueError("byte_fixture requires exactly 260 vocabulary entries")
    return ByteTokenizer()


def _load_bpe_tokenizer_factory(config: Any, *, artifact: Any) -> Any:
    from xlm.tokenizers.bpe import ByteLevelBPETokenizer

    tokenizer = ByteLevelBPETokenizer.load(artifact)
    if tokenizer._target_vocab_size != config.target_vocab_size:
        raise ValueError("BPE artifact differs from configured vocabulary target")
    return tokenizer


tokenizers.register(
    identifier="byte_fixture",
    version="1",
    config_schema=ByteTokenizerConfig,
    capabilities={"reversible": True, "requires_training": False},
    serializer_version="1",
    factory=_load_byte_tokenizer_factory,
)

tokenizers.register(
    identifier="bpe",
    version="1",
    config_schema=BPETokenizerConfig,
    capabilities={"reversible": True, "requires_training": True, "loads_artifact": True},
    serializer_version="1",
    factory=_load_bpe_tokenizer_factory,
)

source_adapters.register(
    identifier="text",
    version="1",
    config_schema=TextAdapterConfig,
    capabilities={"streaming": True, "file_formats": [".txt"]},
    serializer_version="1",
)

source_adapters.register(
    identifier="jsonl",
    version="1",
    config_schema=JsonlAdapterConfig,
    capabilities={"streaming": True, "file_formats": [".jsonl"]},
    serializer_version="1",
)
