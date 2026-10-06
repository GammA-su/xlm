"""Science-v1 pilot plans: draft + operator bindings -> reviewed, hash-bound plan (P35 M3).

The checked-in pilot draft states every scientific field of contract §W and
leaves every real artifact, root, capacity input and pin null. Planning merges
an operator-supplied *bindings* file of local artifacts, verifies them, and
emits a review document plus, only when nothing is unresolved, the standard
frozen :class:`~xlm.experiments.plans.ExecutablePlan` whose ``plan_hash`` the
user authorizes with the existing ``xlm experiment authorize`` ticket.

States: ``DRAFT`` (no bindings; never launchable), ``BLOCKED`` (bindings given
but something is missing, invalid or unverifiable), ``RESOLVED`` (a concrete,
frozen plan hash exists; not authorized), ``EXECUTABLE`` (``validate`` found an
operator ticket covering exactly that plan). Planning and validation never
train, never allocate a GPU and never authorize themselves.

Nothing here downloads, fits a tokenizer, prepares data, or fills a value that
was not supplied: unknown is BLOCKED, never zero, "latest" or a default.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import shutil
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, StrictInt, field_validator, model_validator

from xlm.artifacts.manifest import identity_digest
from xlm.config.schemas import (
    BenchmarkInputsRef,
    LMInventoryRef,
    LMScoringConfig,
    StrictConfigModel,
)

PILOT_VERSION = "xlm-science-pilot-v1"
BINDINGS_VERSION = "xlm-science-pilot-bindings-v1"
REVIEW_VERSION = "xlm-science-pilot-review-v1"
P35_PILOT = "p35_w_32m_pilot"
AUTHORED_PILOT = "authored_fixture"
GIB = 1024**3

#: §M anchor mix01: the 11 admitted M0 components the pilot requires.
M0_COMPONENTS = (
    "essential_science",
    "essential_practical",
    "essential_prose",
    "ultrax_ultrafineweb",
    "finepdfs_en",
    "synth_en_explanations",
    "nemotron_wiki_rewrite",
    "finewiki_en",
    "ifm_behaviors_general_planning",
    "common_pile_prose",
    "simple_stories",
)

#: Upper bounds that come from the code, not from assumptions (bytes).
LAUNCHER_OUTPUT_CAP = 16 * 1024**2
CHECKPOINT_RECEIPT_CAP = 4 * 1024**2
EXECUTION_JSON_CAP = 8 * 1024**2


class PilotStatus(StrEnum):
    DRAFT = "DRAFT"
    BLOCKED = "BLOCKED"
    RESOLVED = "RESOLVED"
    EXECUTABLE = "EXECUTABLE"


class PilotPlanError(ValueError):
    """A pilot draft or bindings file is malformed (not merely unresolved)."""


# ------------------------------------------------------------------ schemas


def _refuse_unpinned(value: str, where: str) -> str:
    text = value.strip()
    if not text or text.casefold() in {"latest", "null", "none", "todo", "tbd"}:
        raise ValueError(f"{where} must be an explicit identity, not {value!r}")
    if any(ch in text for ch in "*?[]"):
        raise ValueError(f"{where} must not be a wildcard pattern: {value!r}")
    return text


def _absolute_path(value: str, where: str) -> str:
    text = _refuse_unpinned(value, where)
    if not Path(text).is_absolute():
        raise ValueError(f"{where} must be an absolute local path, got {value!r}")
    return text


def _sha256(value: str, where: str) -> str:
    text = _refuse_unpinned(value, where)
    if len(text) != 64 or any(c not in "0123456789abcdef" for c in text):
        raise ValueError(f"{where} must be a lowercase 64-hex SHA-256, got {value!r}")
    return text


class PilotSeeds(StrictConfigModel):
    init_seed: StrictInt
    training_seed: StrictInt
    data_seed: StrictInt


class PilotLR(StrictConfigModel):
    policy: Literal["target_endpoint_before_update_v1"]
    base_lr: float
    warmup_valid_targets: StrictInt
    horizon_valid_targets: StrictInt
    min_lr_ratio: float


class PilotLimits(StrictConfigModel):
    total_wall_seconds: float = Field(gt=0)
    max_gpu_allocated_gib: float = Field(gt=0)
    max_process_tree_rss_gib: float = Field(gt=0)
    max_new_output_gib: float = Field(gt=0)
    max_gpu_processes: StrictInt = Field(gt=0)


class PilotExpectation(StrictConfigModel):
    """The scientific values the plan must execute, stated explicitly in the draft."""

    parameters: StrictInt
    budget_valid_targets: StrictInt
    global_batch_valid_targets: StrictInt
    full_updates: StrictInt
    final_update_targets: StrictInt
    total_updates: StrictInt
    microbatch_sequences: StrictInt
    context_length: StrictInt
    vocab_size: StrictInt
    dropout: float
    seeds: PilotSeeds
    lr: PilotLR
    precision: str
    attention_policy: str
    attention_backend: str
    producer_prefetch: str
    checkpoint_cadence: str
    evaluation_cadence: str
    mixture_preset: str
    mixture_components: list[str] = Field(min_length=1, max_length=64)
    limits: PilotLimits


class PilotStorageRoots(StrictConfigModel):
    data_root: str | None
    checkpoint_root: str | None
    temp_root: str | None
    evaluation_input_roots: list[str] | None
    output_root: str | None


class CheckpointSizeSource(StrictConfigModel):
    kind: Literal["measured_profile", "checkpoint_artifact"]
    path: str | None

    @model_validator(mode="after")
    def validate_path(self) -> CheckpointSizeSource:
        if self.kind == "checkpoint_artifact":
            if self.path is None:
                raise ValueError("a checkpoint_artifact size source needs its path")
            _absolute_path(self.path, "checkpoint_size_source.path")
        elif self.path is not None:
            raise ValueError("a measured_profile size source is the --profile file, not a path")
        return self


class PilotCapacity(StrictConfigModel):
    checkpoint_size_source: CheckpointSizeSource | None
    evaluation_evidence_bytes: StrictInt | None = Field(ge=0)
    cache_bytes: StrictInt | None = Field(ge=0)
    safety_margin_bytes: StrictInt | None = Field(ge=0)
    #: Written by the planner from the verified size source; never hand-written.
    checkpoint_bytes: StrictInt | None = Field(default=None, gt=0)


class PilotTokenizerPins(StrictConfigModel):
    artifact_digest: str | None
    fit_input_hash: str | None


class GroupAssignment(StrictConfigModel):
    """Operator-supplied item -> group mapping for one grouped benchmark task."""

    path: str
    sha256: str
    group_key: str = Field(min_length=1)

    @field_validator("path")
    @classmethod
    def absolute(cls, value: str) -> str:
        return _absolute_path(value, "group assignment path")

    @field_validator("sha256")
    @classmethod
    def pinned(cls, value: str) -> str:
        return _sha256(value, "group assignment sha256")


class PilotBenchmarks(StrictConfigModel):
    max_items_per_task: StrictInt = Field(gt=0)
    group_half_tasks: list[str]
    group_assignments: dict[str, GroupAssignment] | None


class PilotColdData(StrictConfigModel):
    min_source_transitions: StrictInt = Field(ge=0)
    each_component_visited: bool
    runtime_cold_open_evidence: Literal["not_instrumented_report_not_run"]


class PilotEvaluationSpec(StrictConfigModel):
    """Evaluation plan of the pilot; inputs and the frozen scoring policy may be null."""

    cadence: Literal["pilot_32m", "authored_fixture"]
    fixture_thresholds: dict[str, list[StrictInt]] | None
    confirmation_registered: Literal[False]
    quick_lm: LMInventoryRef | None
    full_lm: LMInventoryRef | None
    search_benchmark: BenchmarkInputsRef | None
    scoring: LMScoringConfig | None


class PilotResume(StrictConfigModel):
    from_threshold: StrictInt = Field(ge=0)
    fresh_process: bool


#: P35 M5: the §W pilot trains under ONE supplied frozen order manifest. It is a
#: fixed order, explicitly not an independent document-order replicate.
PILOT_ORDER_ROLE = "fixed_single_order_not_an_independent_replicate"
SHARD_NATIVE_PILOT_ORDER = "shard_native_within_source_order"
ORDER_PIN_KEYS = ("manifest", "order_manifest_id", "canonical_membership_id")


class PilotDocumentOrder(StrictConfigModel):
    """The pilot's M5 order binding: drafts leave the pins null, bindings supply them."""

    policy: Literal["m5-independent-document-order-v1"]
    role: Literal["fixed_single_order_not_an_independent_replicate"]
    manifest: str | None
    order_manifest_id: str | None
    canonical_membership_id: str | None


class SciencePilotConfig(StrictConfigModel):
    """The ``science_pilot`` section of a pilot draft or of a resolved plan."""

    version: Literal["xlm-science-pilot-v1"]
    contract: Literal["p35_w_32m_pilot", "authored_fixture"]
    status: Literal["draft_nonexecutable", "operator_resolved"]
    device: Literal["cuda"]
    seed_tuple: str
    #: ``shard_native_within_source_order`` is accepted for authored fixtures only;
    #: the §W contract requires a pinned M5 order (``check_document_order``).
    document_order: Literal["shard_native_within_source_order"] | PilotDocumentOrder
    expected: PilotExpectation
    storage_roots: PilotStorageRoots
    capacity: PilotCapacity
    tokenizer: PilotTokenizerPins
    #: Present in drafts; materialized as ``evaluation.science`` and cleared on resolution.
    evaluation: PilotEvaluationSpec | None
    cold_data: PilotColdData
    benchmarks: PilotBenchmarks
    resume_verification: PilotResume


class BindingData(StrictConfigModel):
    sources: dict[str, str] = Field(min_length=1, max_length=64)
    exposure_plan: str
    pool_artifact: str | None

    @field_validator("sources")
    @classmethod
    def absolute_sources(cls, value: dict[str, str]) -> dict[str, str]:
        return {k: _absolute_path(v, f"data.sources.{k}") for k, v in value.items()}

    @field_validator("exposure_plan")
    @classmethod
    def absolute_plan(cls, value: str) -> str:
        return _absolute_path(value, "data.exposure_plan")


class BindingTokenizer(StrictConfigModel):
    artifact: str
    artifact_digest: str
    fit_input_hash: str

    @field_validator("artifact")
    @classmethod
    def absolute(cls, value: str) -> str:
        return _absolute_path(value, "tokenizer.artifact")

    @field_validator("artifact_digest")
    @classmethod
    def pinned(cls, value: str) -> str:
        return _sha256(value, "tokenizer.artifact_digest")

    @field_validator("fit_input_hash")
    @classmethod
    def fit(cls, value: str) -> str:
        return _refuse_unpinned(value, "tokenizer.fit_input_hash")


class BindingEvaluation(StrictConfigModel):
    quick_lm: LMInventoryRef | None
    full_lm: LMInventoryRef | None
    search_benchmark: BenchmarkInputsRef | None
    scoring: LMScoringConfig
    group_assignments: dict[str, GroupAssignment] | None


class BindingRoots(StrictConfigModel):
    data_root: str
    checkpoint_root: str
    temp_root: str
    evaluation_input_roots: list[str] = Field(min_length=1, max_length=16)
    output_root: str

    @model_validator(mode="after")
    def absolute(self) -> BindingRoots:
        for name in ("data_root", "checkpoint_root", "temp_root", "output_root"):
            _absolute_path(getattr(self, name), name)
        for root in self.evaluation_input_roots:
            _absolute_path(root, "evaluation_input_roots")
        return self


class BindingCapacity(StrictConfigModel):
    checkpoint_size_source: CheckpointSizeSource
    evaluation_evidence_bytes: StrictInt = Field(ge=0)
    cache_bytes: StrictInt = Field(ge=0)
    safety_margin_bytes: StrictInt = Field(ge=0)


class BindingDocumentOrder(StrictConfigModel):
    """The frozen M5 order manifest and both identities it must verify to."""

    manifest: str
    order_manifest_id: str
    canonical_membership_id: str

    @field_validator("manifest")
    @classmethod
    def absolute(cls, value: str) -> str:
        return _absolute_path(value, "document_order.manifest")

    @field_validator("order_manifest_id", "canonical_membership_id")
    @classmethod
    def pinned(cls, value: str) -> str:
        return _sha256(value, "document_order identity")


class PilotBindings(StrictConfigModel):
    """Operator-supplied local artifacts; every field required, nothing defaulted.

    ``document_order`` (P35 M5) is required whenever the draft declares an M5
    order; only authored shard-native fixtures may omit it.
    """

    version: Literal["xlm-science-pilot-bindings-v1"]
    data: BindingData
    tokenizer: BindingTokenizer
    evaluation: BindingEvaluation
    profile_artifact: str
    storage_roots: BindingRoots
    capacity: BindingCapacity
    document_order: BindingDocumentOrder | None = None

    @field_validator("profile_artifact")
    @classmethod
    def pinned_profile(cls, value: str) -> str:
        return _refuse_unpinned(value, "profile_artifact")


#: Contract §W, transcribed. A ``p35_w_32m_pilot`` draft must state exactly these.
P35_PILOT_EXPECTATION = PilotExpectation(
    parameters=49_883_648,
    budget_valid_targets=32_000_000,
    global_batch_valid_targets=65_536,
    full_updates=488,
    final_update_targets=18_432,
    total_updates=489,
    microbatch_sequences=8,
    context_length=512,
    vocab_size=32_768,
    dropout=0.0,
    seeds=PilotSeeds(init_seed=101, training_seed=10001, data_seed=20260918),
    lr=PilotLR(
        policy="target_endpoint_before_update_v1",
        base_lr=0.001,
        warmup_valid_targets=10_000_000,
        horizon_valid_targets=1_000_000_000,
        min_lr_ratio=0.1,
    ),
    precision="bf16_fp32_master",
    attention_policy="statistical_efficient_v1",
    attention_backend="sdpa",
    producer_prefetch="process_depth1",
    checkpoint_cadence="pilot_32m",
    evaluation_cadence="pilot_32m",
    mixture_preset="mix01",
    mixture_components=list(M0_COMPONENTS),
    limits=PilotLimits(
        total_wall_seconds=3600,
        max_gpu_allocated_gib=20,
        max_process_tree_rss_gib=16,
        max_new_output_gib=8,
        max_gpu_processes=1,
    ),
)
#: §A AdamW settings that apply to every 50M recipe.
P35_ADAMW = {
    "betas": [0.9, 0.95],
    "eps": 1e-08,
    "weight_decay": 0.1,
    "decay_norms": False,
    "decay_embeddings": True,
}
#: §W benchmarks: the frozen search-only subset, at most 100 items per task.
P35_MAX_ITEMS_PER_TASK = 100
P35_GROUP_HALF_TASKS = ["hellaswag", "piqa"]
P35_RESUME_FROM = 16_000_000


# --------------------------------------------------------------- findings


@dataclass
class Findings:
    """Blockers, warnings and per-preflight evidence of one planning/validation pass."""

    blockers: list[Any] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    preflight: dict[str, dict[str, Any]] = field(default_factory=dict)

    def block(self, code: str, detail: str) -> None:
        from xlm.experiments.plans import PlanBlocker

        if not any(b.code == code and b.detail == detail for b in self.blockers):
            self.blockers.append(PlanBlocker(code=code, detail=detail))

    def section(self, name: str, *, before: int, **details: Any) -> None:
        own = [b.to_dict() for b in self.blockers[before:]]
        status = "BLOCKED" if own else details.pop("status", "VERIFIED")
        self.preflight[name] = {"status": status, "blockers": own, **details}


def _get(config: Mapping[str, Any], dotted: str) -> Any:
    value: Any = config
    for key in dotted.split("."):
        if not isinstance(value, Mapping) or key not in value:
            return None
        value = value[key]
    return value


def _expect(findings: Findings, code: str, where: str, actual: Any, expected: Any) -> None:
    if actual != expected:
        findings.block(code, f"{where} is {actual!r}; the pilot requires {expected!r}")


def _sha256_file(path: Path) -> str:
    from xlm.artifacts.store import compute_file_sha256

    return compute_file_sha256(path)


# ----------------------------------------------------------- contract checks


def check_expectation(pilot: SciencePilotConfig, findings: Findings) -> None:
    """The pilot section is internally consistent and, for §W, exactly the contract."""
    from xlm.evaluation.cadence import update_arithmetic

    before = len(findings.blockers)
    expected = pilot.expected
    arithmetic = update_arithmetic(
        expected.budget_valid_targets, expected.global_batch_valid_targets
    )
    for key in ("full_updates", "final_update_targets", "total_updates"):
        _expect(findings, "arithmetic_mismatch", f"expected.{key}", getattr(expected, key),
                arithmetic[key])  # fmt: skip
    if expected.lr.warmup_valid_targets > expected.lr.horizon_valid_targets:
        findings.block("lr_schedule_invalid", "warmup exceeds the schedule horizon")
    if pilot.contract == P35_PILOT:
        declared = expected.model_dump(mode="json")
        contract = P35_PILOT_EXPECTATION.model_dump(mode="json")
        for key in sorted(set(declared) | set(contract)):
            if declared.get(key) != contract.get(key):
                findings.block(
                    "contract_deviation",
                    f"expected.{key} is {declared.get(key)!r}; §W requires {contract.get(key)!r}",
                )
        _expect(findings, "contract_deviation", "seed_tuple", pilot.seed_tuple, "P0")
        _expect(findings, "contract_deviation", "benchmarks.max_items_per_task",
                pilot.benchmarks.max_items_per_task, P35_MAX_ITEMS_PER_TASK)  # fmt: skip
        _expect(findings, "contract_deviation", "benchmarks.group_half_tasks",
                sorted(pilot.benchmarks.group_half_tasks), P35_GROUP_HALF_TASKS)  # fmt: skip
        _expect(findings, "contract_deviation", "cold_data.min_source_transitions",
                pilot.cold_data.min_source_transitions, 2)  # fmt: skip
        _expect(findings, "contract_deviation", "cold_data.each_component_visited",
                pilot.cold_data.each_component_visited, True)  # fmt: skip
        _expect(findings, "contract_deviation", "resume_verification.from_threshold",
                pilot.resume_verification.from_threshold, P35_RESUME_FROM)  # fmt: skip
        _expect(findings, "contract_deviation", "resume_verification.fresh_process",
                pilot.resume_verification.fresh_process, True)  # fmt: skip
        if pilot.evaluation is not None:
            _expect(findings, "contract_deviation", "evaluation.cadence",
                    pilot.evaluation.cadence, "pilot_32m")  # fmt: skip
    findings.section(
        "contract",
        before=before,
        contract=pilot.contract,
        research=pilot.contract == P35_PILOT,
        arithmetic=arithmetic,
    )


def check_config(config: Mapping[str, Any], pilot: SciencePilotConfig, findings: Findings) -> None:
    """The configuration executes exactly the stated expectation (draft or resolved)."""
    from xlm.training.components import inspect_model_shape

    before = len(findings.blockers)
    ex = pilot.expected
    code = "config_deviation"
    try:
        parameters = inspect_model_shape(dict(config))
    except (ValueError, KeyError, TypeError) as exc:
        findings.block(code, f"model cannot be instantiated for counting: {exc}")
        parameters = None
    _expect(findings, code, "model unique deployed parameters", parameters, ex.parameters)
    for dotted, expected in (
        ("model.vocab_size", ex.vocab_size),
        ("model.context_length", ex.context_length),
        ("model.dropout", ex.dropout),
        ("model.attention_backend", ex.attention_backend),
        ("training.device", pilot.device),
        ("training.budget.max_valid_targets", ex.budget_valid_targets),
        ("training.global_batch_valid_targets", ex.global_batch_valid_targets),
        ("training.microbatch_sequences", ex.microbatch_sequences),
        ("training.context_length", ex.context_length),
        ("training.init_seed", ex.seeds.init_seed),
        ("training.training_seed", ex.seeds.training_seed),
        ("training.data_seed", ex.seeds.data_seed),
        ("training.science_version", "xlm-science-v1"),
        ("training.lr_policy", ex.lr.policy),
        ("training.precision", ex.precision),
        ("training.producer_prefetch", ex.producer_prefetch),
        ("training.compile", False),
        ("training.activation_checkpointing", False),
        ("training.runtime.attention_policy", ex.attention_policy),
        ("training.schedule.type", "warmup_cosine"),
        ("training.schedule.warmup_valid_targets", ex.lr.warmup_valid_targets),
        ("training.schedule.horizon_valid_targets", ex.lr.horizon_valid_targets),
        ("training.schedule.min_lr_ratio", ex.lr.min_lr_ratio),
        ("training.checkpoint_cadence.cadence", ex.checkpoint_cadence),
        ("optimizer.lr", ex.lr.base_lr),
        ("resources.total_wall_seconds", ex.limits.total_wall_seconds),
        ("resources.max_gpu_processes", ex.limits.max_gpu_processes),
    ):
        _expect(findings, code, dotted, _get(config, dotted), expected)
    if pilot.contract == P35_PILOT:
        from xlm.config.schemas import AdamWConfig

        optimizer: dict[str, Any] = {}
        try:
            # Compare validated values: YAML 1.1 reads a bare ``1e-08`` as text.
            optimizer = AdamWConfig.model_validate(config.get("optimizer", {})).model_dump(
                mode="json"
            )
        except ValueError as exc:
            findings.block(code, f"optimizer is not a valid AdamW configuration: {exc}")
        for key, required in P35_ADAMW.items():
            _expect(findings, code, f"optimizer.{key}", optimizer.get(key), required)
    for dotted, ceiling in (
        ("resources.max_gpu_allocated_gib", ex.limits.max_gpu_allocated_gib),
        ("resources.max_process_tree_rss_gib", ex.limits.max_process_tree_rss_gib),
        ("resources.max_new_disk_gib", ex.limits.max_new_output_gib),
    ):
        value = _get(config, dotted)
        if value is None or not 0 < float(value) <= ceiling:
            findings.block("resource_limit_unset", f"{dotted} must be declared in (0, {ceiling}]")
    per_attempt = _get(config, "training.budget.max_train_seconds")
    if per_attempt is None or float(per_attempt) > ex.limits.total_wall_seconds:
        findings.block(
            "per_attempt_limit",
            "training.budget.max_train_seconds must be explicit and within the total "
            "wall allowance (it limits one attempt; the total deadline is separate)",
        )
    components = _mixture_components(config)
    if components is None or sorted(components) != sorted(ex.mixture_components):
        findings.block(
            "mixture_components_mismatch",
            f"mixture components {sorted(components or [])} differ from the required "
            f"{sorted(ex.mixture_components)}; a missing component is never replaced",
        )
    science = _get(config, "evaluation.science")
    if science is not None:
        _expect(findings, code, "evaluation.science.cadence", science.get("cadence"),
                ex.evaluation_cadence)  # fmt: skip
    findings.section("configuration", before=before, parameters=parameters)


def _mixture_components(config: Mapping[str, Any]) -> list[str] | None:
    mixture = _get(config, "data.mixture")
    if isinstance(mixture, Mapping):
        return [str(c["source_id"]) for c in mixture.get("components", [])]
    details = _get(config, "data.mixture_details")
    if isinstance(details, Mapping):
        return [str(s) for s in details.get("weights", {})]
    return None


# ---------------------------------------------------------------- bindings


def unresolved_fields(config: Mapping[str, Any], pilot: SciencePilotConfig) -> list[str]:
    """Every required value the checked-in draft leaves null (never guessed)."""
    missing = [
        dotted
        for dotted in (
            "data.pool_artifact",
            "data.tokenizer_artifact",
            "data.exposure_plan",
            "resources.profile_artifact",
        )
        if _get(config, dotted) is None
    ]
    if _get(config, "data.sources") is None:
        missing.append("data.sources (one verified shard per mix01 component)")
    order = pilot.document_order
    if isinstance(order, PilotDocumentOrder):
        missing += [
            f"science_pilot.document_order.{k}" for k in ORDER_PIN_KEYS if getattr(order, k) is None
        ]
    elif pilot.contract == P35_PILOT:
        missing.append("science_pilot.document_order (a pinned M5 order manifest is required)")
    roots = pilot.storage_roots.model_dump()
    missing += [f"science_pilot.storage_roots.{k}" for k, v in roots.items() if v is None]
    capacity = pilot.capacity.model_dump()
    missing += [
        f"science_pilot.capacity.{k}"
        for k, v in capacity.items()
        if v is None and k != "checkpoint_bytes"
    ]
    missing += [f"science_pilot.tokenizer.{k}" for k, v in pilot.tokenizer if v is None]
    spec = pilot.evaluation
    if spec is not None:
        for name in ("quick_lm", "full_lm", "search_benchmark", "scoring"):
            if getattr(spec, name) is None:
                missing.append(f"science_pilot.evaluation.{name}")
    if pilot.benchmarks.group_assignments is None:
        missing.append("science_pilot.benchmarks.group_assignments (HellaSwag/PIQA halves)")
    missing.append("search benchmark BLiMP universe (complete subdataset list)")
    missing.append("operator authorization ticket for the concrete plan hash")
    return missing


def apply_bindings(
    composed: Mapping[str, Any], pilot: SciencePilotConfig, bindings: PilotBindings
) -> dict[str, Any]:
    """Merge operator bindings into a copy of the draft; the draft must leave them null."""
    merged = copy.deepcopy(dict(composed))
    data = merged.setdefault("data", {})
    for key in ("pool_artifact", "tokenizer_artifact", "exposure_plan", "sources"):
        if data.get(key) is not None:
            raise PilotPlanError(f"the draft pre-binds data.{key}; bindings supply it")
    data["sources"] = dict(bindings.data.sources)
    data["exposure_plan"] = bindings.data.exposure_plan
    data["pool_artifact"] = bindings.data.pool_artifact
    data["tokenizer_artifact"] = bindings.tokenizer.artifact
    order = pilot.document_order
    if bindings.document_order is not None:
        if not isinstance(order, PilotDocumentOrder):
            raise PilotPlanError(
                "bindings supply a document order but the draft declares the shard-native order"
            )
        if any(getattr(order, k) is not None for k in ORDER_PIN_KEYS):
            raise PilotPlanError("the draft pre-binds science_pilot.document_order")
        if data.get("document_order") is not None:
            raise PilotPlanError("the draft pre-binds data.document_order; bindings supply it")
        data["document_order"] = bindings.document_order.model_dump(mode="json")
        merged["science_pilot"]["document_order"] = {
            **order.model_dump(mode="json"),
            **bindings.document_order.model_dump(mode="json"),
        }
    spec = pilot.evaluation
    if spec is None:
        raise PilotPlanError("the draft carries no pilot evaluation specification")
    for name in ("quick_lm", "full_lm", "search_benchmark", "scoring"):
        if getattr(spec, name) is not None:
            raise PilotPlanError(f"the draft pre-binds evaluation {name}; bindings supply it")
    evaluation = merged.setdefault("evaluation", {})
    if evaluation.get("science") is not None:
        raise PilotPlanError("the draft must not declare evaluation.science directly")

    def dump(model: Any) -> Any:
        return model.model_dump(mode="json") if model is not None else None

    evaluation["science"] = {
        "version": "xlm-eval-cadence-v1",
        "cadence": spec.cadence,
        "fixture_thresholds": spec.fixture_thresholds,
        "confirmation_registered": False,
        "quick_lm": dump(bindings.evaluation.quick_lm),
        "full_lm": dump(bindings.evaluation.full_lm),
        "search_benchmark": dump(bindings.evaluation.search_benchmark),
        "endpoint_confirmation": None,
        "scoring": dump(bindings.evaluation.scoring),
    }
    resources = merged.setdefault("resources", {})
    if resources.get("profile_artifact") is not None:
        raise PilotPlanError("the draft pre-binds resources.profile_artifact")
    resources["profile_artifact"] = bindings.profile_artifact
    section = merged["science_pilot"]
    section["status"] = "operator_resolved"
    section["evaluation"] = None
    section["storage_roots"] = bindings.storage_roots.model_dump(mode="json")
    section["capacity"] = {**bindings.capacity.model_dump(mode="json"), "checkpoint_bytes": None}
    section["tokenizer"] = {
        "artifact_digest": bindings.tokenizer.artifact_digest,
        "fit_input_hash": bindings.tokenizer.fit_input_hash,
    }
    section["benchmarks"]["group_assignments"] = (
        {k: v.model_dump(mode="json") for k, v in bindings.evaluation.group_assignments.items()}
        if bindings.evaluation.group_assignments is not None
        else None
    )
    return merged


# ------------------------------------------------------------ storage roots


def _plain_dir(path: Path) -> bool:
    from xlm.artifacts.manifest import ensure_plain_path

    try:
        ensure_plain_path(path)
    except ValueError:
        return False
    return path.is_dir()


def _inside(path: Path, roots: Sequence[Path]) -> bool:
    resolved = path.resolve()
    return any(resolved == root or resolved.is_relative_to(root) for root in roots)


def evaluation_input_files(science: Mapping[str, Any]) -> list[Path]:
    """Every local file the evaluation inputs read (manifests and their documents)."""
    from xlm.evaluation.inputs import load_evaluation_inputs
    from xlm.evaluation.lm_validation import load_validation_manifest

    files: list[Path] = []
    for tier in ("quick_lm", "full_lm"):
        ref = science.get(tier)
        if ref is None:
            continue
        manifest_path = Path(ref["manifest"])
        files.append(manifest_path)
        if manifest_path.is_file():
            manifest = load_validation_manifest(manifest_path)
            files += [manifest_path.parent / d.documents_file for d in manifest.domains]
    search = science.get("search_benchmark")
    if search is not None:
        inputs_path = Path(search["inputs"])
        files.append(inputs_path)
        if inputs_path.is_file():
            declared = load_evaluation_inputs(inputs_path)
            for selection in declared.selections:
                candidate = Path(selection.data_file)
                files.append(
                    candidate if candidate.is_absolute() else inputs_path.parent / candidate
                )
    return files


def check_storage_roots(
    config: Mapping[str, Any],
    pilot: SciencePilotConfig,
    findings: Findings,
    *,
    artifact_home: Path,
    outputs: Sequence[Path],
) -> dict[str, Any]:
    """Every input and output lies inside its declared root; the queue honors the roots."""
    before = len(findings.blockers)
    roots = pilot.storage_roots
    declared: dict[str, list[Path]] = {}
    for name in ("data_root", "checkpoint_root", "temp_root", "output_root"):
        value = getattr(roots, name)
        declared[name] = [Path(value).resolve()] if value is not None else []
    declared["evaluation_input_roots"] = [
        Path(v).resolve() for v in (roots.evaluation_input_roots or [])
    ]
    for name, paths in declared.items():
        if not paths:
            findings.block("storage_root_unresolved", f"science_pilot.storage_roots.{name} is null")
        for root in paths:
            if not _plain_dir(root):
                findings.block(
                    "storage_root_invalid", f"{name} {root} is not an existing plain directory"
                )
    data_paths = [Path(p) for p in (_get(config, "data.sources") or {}).values()]
    for dotted in ("data.exposure_plan", "data.tokenizer_artifact"):
        if isinstance(_get(config, dotted), str):
            data_paths.append(Path(_get(config, dotted)))
    for path in data_paths:
        if declared["data_root"] and not _inside(path, declared["data_root"]):
            findings.block("outside_data_root", f"{path} is outside the declared data root")
    science = _get(config, "evaluation.science") or {}
    try:
        eval_files = evaluation_input_files(science)
    except (ValueError, OSError) as exc:
        findings.block("evaluation_inputs_unreadable", str(exc))
        eval_files = []
    for assignment in (pilot.benchmarks.group_assignments or {}).values():
        eval_files.append(Path(assignment.path))
    for path in eval_files:
        if declared["evaluation_input_roots"] and not _inside(
            path, declared["evaluation_input_roots"]
        ):
            findings.block(
                "outside_evaluation_roots", f"{path} is outside the declared evaluation roots"
            )
    source = pilot.capacity.checkpoint_size_source
    if source is not None and source.path is not None:
        allowed = declared["data_root"] + declared["checkpoint_root"]
        if allowed and not _inside(Path(source.path), allowed):
            findings.block(
                "outside_declared_roots", f"size source {source.path} is outside the declared roots"
            )
    for path in outputs:
        if declared["output_root"] and not _inside(path, declared["output_root"]):
            findings.block("outside_output_root", f"output {path} is outside the output root")
    # The existing queue writes a job's store and worker scratch under
    # <XLM_HOME>/runs/<job>; the declared roots must contain exactly that.
    runs = (artifact_home / "runs").resolve()
    if declared["checkpoint_root"] and not _inside(runs, declared["checkpoint_root"]):
        findings.block(
            "checkpoint_root_not_honored",
            f"queue checkpoints are written under {runs} (XLM_HOME), outside checkpoint_root",
        )
    if declared["temp_root"] and not _inside(runs, declared["temp_root"]):
        findings.block(
            "temp_root_not_honored",
            f"queue worker scratch/staging is written under {runs} (XLM_HOME), outside temp_root",
        )
    same_volume = None
    if declared["checkpoint_root"] and declared["temp_root"]:
        try:
            same_volume = (
                os.stat(declared["checkpoint_root"][0]).st_dev
                == os.stat(declared["temp_root"][0]).st_dev
            )
        except OSError:
            same_volume = None
    if same_volume:
        findings.warnings.append(
            "checkpoint and temp/staging roots share one volume: the peak includes the "
            "2x publication transient on that volume"
        )
    findings.section(
        "storage_roots",
        before=before,
        roots={k: [str(p) for p in v] for k, v in declared.items()},
        artifact_home=str(artifact_home.resolve()),
        checkpoint_and_temp_same_volume=same_volume,
    )
    return {"declared": declared}


# --------------------------------------------------------------- tokenizer


def check_tokenizer(
    resolved: Mapping[str, Any],
    exec_bindings: Mapping[str, Any],
    pilot: SciencePilotConfig,
    findings: Findings,
) -> None:
    before = len(findings.blockers)
    identity = dict(exec_bindings.get("tokenizer") or {})
    location = Path(str(_get(resolved, "data.tokenizer_artifact")))
    details: dict[str, Any] = {"identity": identity, "artifact": str(location)}
    try:
        manifest = json.loads((location / "tokenizer_manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        findings.block("tokenizer_unreadable", f"{location}: {exc}")
        manifest = {}
    expected_vocab = pilot.expected.vocab_size
    _expect(findings, "tokenizer_mismatch", "tokenizer class", identity.get("type"),
            "ByteLevelBPETokenizer")  # fmt: skip
    _expect(findings, "tokenizer_mismatch", "tokenizer manifest type", manifest.get("type"), "bpe")
    _expect(findings, "tokenizer_mismatch", "tokenizer target vocabulary",
            manifest.get("target_vocab_size"), expected_vocab)  # fmt: skip
    _expect(findings, "tokenizer_mismatch", "tokenizer actual vocabulary",
            manifest.get("actual_vocab_size"), expected_vocab)  # fmt: skip
    _expect(findings, "tokenizer_unpinned", "tokenizer artifact digest",
            identity.get("artifact_hash"), pilot.tokenizer.artifact_digest)  # fmt: skip
    fit = manifest.get("training_input_hash")
    if fit is None:
        findings.block("tokenizer_fit_unrecorded", "the tokenizer records no fit-input identity")
    _expect(findings, "tokenizer_unpinned", "tokenizer fit input hash", fit,
            pilot.tokenizer.fit_input_hash)  # fmt: skip
    specials = manifest.get("special_tokens") or {}
    ids = [specials.get(k) for k in ("pad", "bos", "eos", "unk")]
    if any(not isinstance(i, int) or not 0 <= i < expected_vocab for i in ids) or len(
        set(ids)
    ) != len(ids):
        findings.block(
            "tokenizer_special_tokens", f"special token ids {specials} are not distinct in range"
        )
    details.update(
        special_tokens=specials,
        fingerprint=manifest.get("fingerprint"),
        fit_input_hash=fit,
        normalization="byte-level BPE; canonical NFC/newline normalization applied upstream",
    )
    findings.section("tokenizer", before=before, **details)


# --------------------------------------------------------------- data/mixture


def check_data(
    resolved: Mapping[str, Any], pilot: SciencePilotConfig, findings: Findings
) -> dict[str, Any]:
    """Frozen components, quotas, exhaustion policy and 32M exposure are satisfiable."""
    before = len(findings.blockers)
    mixture = _get(resolved, "data.mixture") or {}
    components = [str(c["source_id"]) for c in mixture.get("components", [])]
    exhaustion = mixture.get("exhaustion") or {}
    if pilot.contract == P35_PILOT:
        _expect(findings, "exposure_policy", "exhaustion.repeat", exhaustion.get("repeat"), False)
        _expect(findings, "exposure_policy", "exhaustion.max_epochs",
                exhaustion.get("max_epochs"), 1)  # fmt: skip
    exposure = _get(resolved, "data.exposure_plan")
    table: dict[str, Any] = {}
    if not isinstance(exposure, Mapping):
        findings.block("exposure_plan_missing", "no frozen token exposure plan is bound")
    else:
        planned_total = 0
        for source, projection in sorted(exposure.get("projections", {}).items()):
            planned = int(projection["planned_targets"])
            available = int(projection["unique_targets_available"])
            planned_total += planned
            table[source] = {
                "configured_weight": projection["configured_weight"],
                "planned_targets": planned,
                "unique_targets_available": available,
                "exposure_ratio": planned / available if available else None,
                "repeated_targets": int(projection["repeated_targets"]),
            }
            if int(projection["repeated_targets"]) > 0 or planned > available:
                findings.block(
                    "insufficient_exposure",
                    f"source '{source}' needs {planned:,} targets but offers {available:,}; "
                    "exhaustion is an error and no component is repeated or replaced",
                )
        _expect(findings, "exposure_budget", "planned exposure total", planned_total,
                pilot.expected.budget_valid_targets)  # fmt: skip
        if sorted(exposure.get("projections", {})) != sorted(components):
            findings.block("exposure_components", "exposure plan sources differ from the mixture")
    input_bytes = 0
    for path in (_get(resolved, "data.sources") or {}).values():
        for name in ("tokens.bin", "offsets.jsonl", "shard_manifest.json", "shard_counters.json"):
            item = Path(path) / name
            if item.is_file():
                input_bytes += item.stat().st_size
    findings.section(
        "data",
        before=before,
        components=components,
        per_source=table,
        input_shard_bytes=input_bytes,
        document_order=_order_description(pilot),
    )
    return {"components": components}


def _order_description(pilot: SciencePilotConfig) -> str:
    order = pilot.document_order
    if isinstance(order, PilotDocumentOrder):
        return (
            f"frozen M5 order {order.order_manifest_id} over membership "
            f"{order.canonical_membership_id} ({PILOT_ORDER_ROLE})"
        )
    return "shard native within-source order (not an independent order replicate)"


def check_document_order(
    config: Mapping[str, Any], pilot: SciencePilotConfig, findings: Findings
) -> None:
    """The pilot's frozen order is pinned, verifies, and orders exactly its shards (M5)."""
    from xlm.data.ordering import OrderManifestError, resolve_document_order
    from xlm.data.tokens import TokenShardReader

    before = len(findings.blockers)
    order = pilot.document_order
    if not isinstance(order, PilotDocumentOrder):
        if pilot.contract == P35_PILOT:
            findings.block(
                "document_order_unbound",
                "the §W pilot trains under a supplied frozen M5 order manifest; the "
                "shard-native order is accepted for authored fixtures only",
            )
        findings.section("document_order", before=before, policy="shard_native_offset_order_v1")
        return
    pins = {k: getattr(order, k) for k in ORDER_PIN_KEYS}
    reference = _get(config, "data.document_order")
    if any(v is None for v in pins.values()) or reference is None:
        findings.block(
            "document_order_unresolved",
            "the frozen order manifest, its order_manifest_id and canonical_membership_id "
            "must all be bound",
        )
        findings.section("document_order", before=before, role=order.role)
        return
    if dict(reference) != pins:
        findings.block(
            "document_order_pin_mismatch",
            "data.document_order differs from the science_pilot.document_order pins",
        )
    data_root = pilot.storage_roots.data_root
    if data_root is not None and not _inside(
        Path(str(reference["manifest"])), [Path(data_root).resolve()]
    ):
        findings.block(
            "document_order_outside_root",
            "the order manifest must lie inside science_pilot.storage_roots.data_root",
        )
    sources = _get(config, "data.sources") or {}
    try:
        readers = {sid: TokenShardReader(Path(path)) for sid, path in sources.items()}
        verified, manifest = resolve_document_order(reference, readers)
    except OrderManifestError as exc:
        findings.block(exc.code, str(exc)[:800])
    except (OSError, ValueError, KeyError) as exc:
        findings.block("document_order_unverifiable", f"{type(exc).__name__}: {exc}"[:800])
    else:
        findings.section(
            "document_order",
            before=before,
            role=order.role,
            order_manifest_id=verified["order_manifest_id"],
            canonical_membership_id=verified["canonical_membership_id"],
            order_seed=manifest["derivation"]["order_seed"],
            independent_order_replicate=False,
        )
        return
    findings.section("document_order", before=before, role=order.role)


def open_mixture(resolved: Mapping[str, Any]) -> Any:
    """Verified readers of the resolved mixture (bounded, read-only)."""
    from xlm.core.paths import ArtifactPaths
    from xlm.training.inputs import resolve_training_input

    source, _ = resolve_training_input(
        copy.deepcopy(dict(resolved["data"])), ArtifactPaths.from_env()
    )
    return source


def check_heldout_membership(resolved: Mapping[str, Any], mixture: Any, findings: Findings) -> None:
    """No validation document may appear in the training shards' membership."""
    from xlm.evaluation.lm_validation import load_validation_manifest

    before = len(findings.blockers)
    heldout: set[str] = set()
    for tier in ("quick_lm", "full_lm"):
        ref = _get(resolved, f"evaluation.science.{tier}")
        if ref is not None:
            heldout |= load_validation_manifest(ref["manifest"]).document_ids()
    overlap: list[str] = []
    scanned = 0
    for reader in mixture.readers.values():
        with (reader.directory / "offsets.jsonl").open("rb") as stream:
            while raw := stream.readline(8 * 1024**2 + 1):
                scanned += 1
                doc_id = json.loads(raw).get("doc_id")
                if doc_id in heldout:
                    overlap.append(str(doc_id))
                    if len(overlap) >= 20:
                        break
    if overlap:
        findings.block(
            "heldout_in_training",
            f"validation documents appear in training membership: {sorted(overlap)[:20]}",
        )
    findings.section(
        "heldout_membership",
        before=before,
        training_documents_scanned=scanned,
        heldout_documents=len(heldout),
    )


def check_cold_coverage(
    resolved: Mapping[str, Any], mixture: Any, pilot: SciencePilotConfig, findings: Findings
) -> None:
    """§W: the first budget of exposure crosses source transitions and visits every component."""
    from xlm.data.sampling import MixtureBatcher, compile_exposure_plan, validate_mixture
    from xlm.data.sampling.plan import iter_exposure_blocks

    batcher = MixtureBatcher(mixture.recipe, mixture.readers)
    try:
        availability = batcher.availability
        exposure = _get(resolved, "data.exposure_plan") or {}
        plan = compile_exposure_plan(
            mixture.recipe,
            validate_mixture(mixture.recipe, availability),
            pilot.expected.budget_valid_targets,
            block_size=int(exposure.get("block_size", 8192)),
        )
        transitions = 0
        previous: str | None = None
        first_visit: dict[str, int] = {}
        produced = 0
        for block in iter_exposure_blocks(plan, availability):
            if previous is not None and block.source_id != previous:
                transitions += 1
            first_visit.setdefault(block.source_id, produced)
            previous = block.source_id
            produced += block.token_count
    finally:
        batcher.close()
    unvisited = sorted(set(plan.source_order) - set(first_visit))
    covered = transitions >= pilot.cold_data.min_source_transitions and (
        not pilot.cold_data.each_component_visited or not unvisited
    )
    status = "VERIFIED" if covered else "NOT_RUN"
    if not covered:
        findings.warnings.append(
            "cold-shard coverage NOT RUN by this layout: plan a separate bounded storage "
            "diagnostic; quotas are never altered to force a cache experiment"
        )
    findings.preflight["cold_data"] = {
        "status": status,
        "blockers": [],
        "source_transitions_in_budget": transitions,
        "first_visit_target_offset": dict(sorted(first_visit.items())),
        "unvisited_components": unvisited,
        "runtime_cold_open_evidence": "not instrumented in M3; the pilot reports it NOT RUN "
        "unless measured",
        "scope": "unpacked quota preview of the frozen exposure plan (not a packed trace)",
    }


# -------------------------------------------------------------- evaluation


def check_evaluation(
    resolved: Mapping[str, Any],
    pilot: SciencePilotConfig,
    components: Sequence[str],
    tokenizer: Any,
    findings: Findings,
) -> None:
    """Pinned inventories, required domains, split/tier firewall, item caps, group halves."""
    from xlm.evaluation.lm_validation import load_pinned_inventory
    from xlm.training.evaluation import plan_from_config

    before = len(findings.blockers)
    science = _get(resolved, "evaluation.science")
    details: dict[str, Any] = {}
    if science is None:
        findings.block("evaluation_unresolved", "evaluation.science is not materialized")
        findings.section("evaluation", before=before)
        return
    plan = plan_from_config(science, pilot.expected.budget_valid_targets)
    planned = sorted({e.tier.value for e in plan.events})
    details["planned_tiers"] = planned
    inventories: dict[str, Any] = {}
    for tier in ("quick_lm", "full_lm"):
        ref = science.get(tier)
        if ref is None:
            continue
        try:
            inventories[tier] = load_pinned_inventory(
                ref["manifest"],
                manifest_id=ref["manifest_id"],
                tokenizer_fingerprint=tokenizer.fingerprint,
            )
        except (ValueError, OSError) as exc:
            findings.block("inventory_unverified", f"{tier}: {exc}")
    quick, full = inventories.get("quick_lm"), inventories.get("full_lm")
    if quick is not None and full is not None:
        if quick.manifest.nested_in != full.manifest_id or not (
            quick.manifest.document_ids() <= full.manifest.document_ids()
        ):
            findings.block(
                "inventory_nesting", "the quick subset is not nested in the full inventory"
            )
        if sorted(quick.domain_ids) != sorted(full.domain_ids):
            findings.block("inventory_domains", "quick and full inventories differ in domains")
    for tier, inventory in inventories.items():
        manifest = inventory.manifest
        if pilot.contract == P35_PILOT and manifest.scope_kind != "frozen_validation_inventory":
            findings.block(
                "authored_inventory_in_pilot", f"{tier} is '{manifest.scope_kind}', not frozen"
            )
        sources = sorted({s for d in manifest.domains for s in d.source_ids})
        if sources != sorted(components):
            findings.block(
                "validation_domains_do_not_cover_mixture",
                f"{tier} domain sources {sources} differ from the mixture {sorted(components)}; "
                "equal-domain weights are frozen over the admitted components",
            )
        details[tier] = {
            **inventory.summary(),
            "text_utf8_bytes": sum(d.text_utf8_bytes for d in inventory.domains),
        }
    search = science.get("search_benchmark")
    if search is not None:
        details["search_benchmark"] = _check_search_benchmark(search, pilot, findings)
    details["later_requirements"] = [
        "endpoint confirmation LM inventory (1B full_1b only; not part of the pilot tier)",
        "complete frozen search BLiMP at 128M (screen_128m); needs the complete universe",
    ]
    findings.section("evaluation", before=before, **details)


def group_half_problems(
    records: Sequence[Mapping[str, Any]], group_key: str, declared: set[str]
) -> tuple[list[str], list[str]]:
    """Declared search items lacking a group, and those in the confirmation half.

    The halves are the repository's own deterministic whole-group partition
    (``suites.partition_grouped_items``); the item -> group mapping is data the
    operator supplies, pinned by hash, never inferred here.
    """
    from xlm.evaluation.suites import SuiteTier, partition_grouped_items

    known = {str(r.get("item_id")) for r in records}
    search_half = {
        str(r.get("item_id")) for r in partition_grouped_items(records, SuiteTier.SEARCH, group_key)
    }
    return sorted(declared - known), sorted((declared & known) - search_half)


def _check_search_benchmark(
    search: Mapping[str, Any], pilot: SciencePilotConfig, findings: Findings
) -> dict[str, Any]:
    from xlm.evaluation.inputs import load_evaluation_inputs
    from xlm.evaluation.search_tier import BenchmarkInputSpec, verify_tier_inputs
    from xlm.evaluation.suites import SuiteTier

    details: dict[str, Any] = {"manifest_id": search["manifest_id"]}
    universe = list(search.get("blimp_universe") or [])
    try:
        from xlm.evaluation.harness import harness_version

        spec = BenchmarkInputSpec(
            inputs_path=Path(search["inputs"]),
            manifest_id=str(search["manifest_id"]),
            tier=SuiteTier.SEARCH,
            blimp_universe=tuple(universe),
        )
        verify_tier_inputs(spec, harness_version=harness_version())
    except Exception as exc:  # noqa: BLE001 - every verification failure blocks
        findings.block("search_benchmark_unverified", f"{type(exc).__name__}: {exc}"[:600])
        return details
    manifest = load_evaluation_inputs(Path(search["inputs"]))
    if pilot.contract == P35_PILOT and manifest.is_authored_fixture:
        findings.block("authored_benchmark_in_pilot", "search inputs are an authored fixture")
    per_task: dict[str, int] = {}
    for selection in manifest.selections:
        per_task[selection.task] = per_task.get(selection.task, 0) + len(selection.item_ids)
    for task, count in per_task.items():
        if count > pilot.benchmarks.max_items_per_task:
            findings.block(
                "benchmark_items_exceed_cap",
                f"{task} declares {count} items; the pilot allows at most "
                f"{pilot.benchmarks.max_items_per_task} per task (strictly partial)",
            )
    assignments = pilot.benchmarks.group_assignments or {}
    for task in pilot.benchmarks.group_half_tasks:
        selections = [s for s in manifest.selections if s.task == task]
        if not selections:
            continue
        assignment = assignments.get(task)
        if assignment is None:
            findings.block(
                "group_half_assignment_missing",
                f"{task} search items need the grouped-half assignment; none is bound",
            )
            continue
        path = Path(assignment.path)
        if not path.is_file() or _sha256_file(path) != assignment.sha256:
            findings.block("group_half_assignment_unpinned", f"{path} differs from its pin")
            continue
        records = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(records, list):
            findings.block("group_half_assignment_malformed", f"{path} must be a JSON list")
            continue
        declared = {str(i) for s in selections for i in s.item_ids}
        unassigned, outside = group_half_problems(records, assignment.group_key, declared)
        if unassigned:
            findings.block(
                "group_half_unassigned_items",
                f"{task} items {unassigned[:10]} have no group assignment",
            )
        if outside:
            findings.block(
                "group_half_violation",
                f"{task} items {outside[:10]} belong to the confirmation half",
            )
    details.update(items_per_task=per_task, blimp_universe_size=len(universe), scope="partial")
    return details


# ----------------------------------------------------------------- capacity


# ------------------------------------------------ recoverability (readiness)

#: The only recoverability policy the §W research pilot accepts (P35 pilot readiness).
P35_RECOVERABILITY = {
    "version": "xlm-evaluation-recoverability-v1",
    "required_events": "all_planned_events_v1",
    "initial_barrier": "required_initial_evaluation_barrier_v1",
    "recovery_checkpoints": "evaluation_recovery_checkpoints_v1",
    "endpoint": "endpoint_live_retry_then_fail_stop_v1",
}


def pilot_run_plans(config: Mapping[str, Any], pilot: SciencePilotConfig) -> tuple[Any, Any, Any]:
    """Evaluation plan, checkpoint plan (with evaluation-recovery events) and policy.

    The same derivation the trainer's components use, so the review, the
    capacity bound and the runtime agree. A draft states its evaluation plan in
    ``science_pilot.evaluation``; a resolved plan in ``evaluation.science``.
    """
    from xlm.evaluation.cadence import build_plan
    from xlm.evaluation.recoverability import RecoverabilityPolicy, checkpoint_plan_with_recovery

    ex = pilot.expected
    budget = ex.budget_valid_targets
    spec = _get(config, "evaluation.science") or (
        pilot.evaluation.model_dump(mode="json") if pilot.evaluation is not None else {}
    )
    evaluation = build_plan(
        str(spec.get("cadence", ex.evaluation_cadence)),
        budget,
        confirmation_registered=False,
        fixture_thresholds=spec.get("fixture_thresholds"),
    )
    raw = _get(config, "training.evaluation_recoverability")
    policy = RecoverabilityPolicy.from_config(raw) if raw is not None else None
    cadence = _get(config, "training.checkpoint_cadence") or {"cadence": ex.checkpoint_cadence}
    checkpoints = checkpoint_plan_with_recovery(
        {
            "cadence": cadence.get("cadence", ex.checkpoint_cadence),
            "fixture_milestones": cadence.get("fixture_milestones"),
            "fixture_recovery": cadence.get("fixture_recovery"),
        },
        budget,
        evaluation_plan=evaluation,
        policy=policy,
    )
    return evaluation, checkpoints, policy


def check_recoverability(
    config: Mapping[str, Any], pilot: SciencePilotConfig, findings: Findings
) -> list[dict[str, Any]]:
    """Every required evaluation event has a recovery or fail-stop route (fail closed)."""
    from xlm.evaluation.recoverability import (
        RecoverabilityError,
        recoverability_table,
        table_digest,
        unrecoverable_required,
    )

    before = len(findings.blockers)
    raw = _get(config, "training.evaluation_recoverability")
    if pilot.contract == P35_PILOT and raw != P35_RECOVERABILITY:
        findings.block(
            "recoverability_policy_required",
            f"training.evaluation_recoverability is {raw!r}; the §W pilot requires "
            f"{P35_RECOVERABILITY!r}",
        )
    try:
        evaluation, checkpoints, policy = pilot_run_plans(config, pilot)
    except (RecoverabilityError, ValueError) as exc:
        findings.block("recoverability_unresolvable", str(exc)[:800])
        findings.section("recoverability", before=before)
        return []
    rows = recoverability_table(evaluation, checkpoints, policy)
    for event in unrecoverable_required(rows):
        findings.block(
            "required_event_unrecoverable",
            f"{event}: no retained exact state and no retry/fail-stop route; a clean "
            "evaluation failure could leave the pilot permanently evaluation-incomplete",
        )
    findings.section(
        "recoverability",
        before=before,
        policy=policy.identity() if policy is not None else None,
        table=rows,
        table_digest=table_digest(rows, policy),
        evaluation_recovery_checkpoints=[
            e.event_id for e in checkpoints.events if e.role.value == "evaluation_recovery"
        ],
    )
    return rows


def frozen_input_sizes(
    sources: Mapping[str, str], policy_binding: Any = None
) -> dict[str, dict[str, int]]:
    """Observed shard file sizes by ``stat`` only: nothing is opened, read or hashed."""
    from xlm.data.input_limits import SHARD_INPUT_FILES
    from xlm.data.input_policy import file_limits, policy_from_binding

    policy = policy_from_binding(policy_binding)
    names = file_limits(policy) if policy is not None else SHARD_INPUT_FILES
    sizes: dict[str, dict[str, int]] = {}
    for source, path in sorted(sources.items()):
        files: dict[str, int] = {}
        for name in names:
            item = Path(path) / name
            if item.is_file():
                files[name] = item.stat().st_size
        sizes[source] = files
    return sizes


def frozen_input_report(
    sizes: Mapping[str, Mapping[str, int]], policy_binding: Any = None
) -> dict[str, Any]:
    """Observed bytes against the unchanged frozen-input caps; which sources exceed them."""
    from xlm.data.input_limits import (
        AGGREGATE_INPUT_FILES,
        MAX_AGGREGATE_FROZEN_INPUT_BYTES,
        MAX_FROZEN_SHARD_INPUT_BYTES,
        MAX_SHARD_JSON_BYTES,
    )
    from xlm.data.input_policy import admit_sizes, file_limits, policy_from_binding

    policy = policy_from_binding(policy_binding)
    shard_cap = policy.shard_bytes if policy is not None else MAX_FROZEN_SHARD_INPUT_BYTES
    aggregate_cap = (
        policy.aggregate_bytes if policy is not None else MAX_AGGREGATE_FROZEN_INPUT_BYTES
    )
    json_cap = policy.sidecar_bytes if policy is not None else MAX_SHARD_JSON_BYTES
    aggregate_files = file_limits(policy) if policy is not None else AGGREGATE_INPUT_FILES
    if policy is not None:
        admit_sizes(sizes, policy)
    per_source: dict[str, Any] = {}
    aggregate = 0
    for source, files in sorted(sizes.items()):
        shard_total = sum(int(v) for v in files.values())
        counted = sum(int(files.get(name, 0)) for name in aggregate_files)
        aggregate += counted
        oversized_json = sorted(
            n for n, v in files.items() if n.endswith(".json") and int(v) > json_cap
        )
        per_source[source] = {
            "files": dict(sorted((k, int(v)) for k, v in files.items())),
            "shard_bytes": shard_total,
            "aggregate_counted_bytes": counted,
            "exceeds_shard_cap": shard_total > shard_cap,
            "oversized_json": oversized_json,
        }
    return {
        "basis": "stat() of the bound shard files before any hashing or index scan",
        "caps": {
            "per_shard_bytes": shard_cap,
            "aggregate_bytes": aggregate_cap,
            "json_file_bytes": json_cap,
        },
        "per_source": per_source,
        "aggregate_bytes": aggregate,
        "exceeds_aggregate_cap": aggregate > aggregate_cap,
        "aggregate_excess_bytes": max(0, aggregate - aggregate_cap),
        "sources_exceeding_shard_cap": sorted(
            s for s, r in per_source.items() if r["exceeds_shard_cap"] or r["oversized_json"]
        ),
    }


def check_input_bytes(config: Mapping[str, Any], findings: Findings) -> dict[str, Any] | None:
    """Report frozen input sizes against the unchanged caps before resolution hashes anything.

    The caps are not relaxed: an excess is a blocker naming the source(s) and
    bytes, so the operator can tell immediately whether the cap is the blocker.
    """
    before = len(findings.blockers)
    sources = _get(config, "data.sources")
    if not isinstance(sources, Mapping) or not sources:
        findings.section("input_bytes", before=before, status="NOT_RUN", note="no bound sources")
        return None
    binding = _get(config, "data.training_input_policy")
    try:
        paths = {str(k): str(v) for k, v in sources.items()}
        report = frozen_input_report(
            frozen_input_sizes(paths) if binding is None else frozen_input_sizes(paths, binding),
            binding,
        )
    except ValueError as exc:
        findings.block("frozen_input_policy_refused", str(exc))
        findings.section("input_bytes", before=before, status="BLOCKED")
        return None
    for source in report["sources_exceeding_shard_cap"]:
        row = report["per_source"][source]
        findings.block(
            "frozen_input_cap_exceeded",
            f"source '{source}' shard files total {row['shard_bytes']:,} bytes (cap "
            f"{report['caps']['per_shard_bytes']:,}); oversized JSON {row['oversized_json']}",
        )
    if report["exceeds_aggregate_cap"]:
        findings.block(
            "frozen_input_cap_exceeded",
            f"aggregate frozen shard inputs {report['aggregate_bytes']:,} bytes exceed the "
            f"unchanged {report['caps']['aggregate_bytes']:,}-byte cap by "
            f"{report['aggregate_excess_bytes']:,}; largest sources: "
            + ", ".join(
                f"{s}={r['aggregate_counted_bytes']:,}"
                for s, r in sorted(
                    report["per_source"].items(), key=lambda kv: -kv[1]["aggregate_counted_bytes"]
                )[:3]
            ),
        )
    findings.section("input_bytes", before=before, **report)
    return report


def checkpoint_size_from_source(
    source: CheckpointSizeSource,
    resolved: Mapping[str, Any],
    *,
    measured_profile: Mapping[str, Any] | None,
) -> tuple[int, dict[str, Any]]:
    """Measured bytes of one full checkpoint of this model/optimizer/precision; never guessed."""
    if source.kind == "measured_profile":
        from xlm.training.profile import resolve_measured_profile

        if measured_profile is None:
            raise PilotPlanError("the size source is the measured profile, but none was given")
        plan = resolve_measured_profile(
            measured_profile, model_config=resolved["model"], training=resolved["training"]
        )
        gib = plan.get("checkpoint_gib")
        if not isinstance(gib, (int, float)) or not math.isfinite(gib) or gib <= 0:
            raise PilotPlanError("the measured profile records no checkpoint size")
        return int(math.ceil(float(gib) * GIB)), {"kind": source.kind, "checkpoint_gib": gib}
    from xlm.artifacts.store import ArtifactStore
    from xlm.core.paths import ArtifactPaths

    assert source.path is not None
    path = Path(source.path).resolve()
    manifest = ArtifactStore(ArtifactPaths(root=path.parent.parent)).verify_artifact(path)
    model_config = json.loads((path / "model_config.json").read_text(encoding="utf-8"))
    meta = json.loads((path / "checkpoint_meta.json").read_text(encoding="utf-8"))
    for key in ("vocab_size", "num_layers", "hidden_size", "num_attention_heads",
                "intermediate_size", "context_length", "tie_embeddings"):  # fmt: skip
        if model_config.get(key) != resolved["model"].get(key):
            raise PilotPlanError(f"size source checkpoint differs in model {key}")
    if meta.get("precision") != resolved["training"]["precision"]:
        raise PilotPlanError("size source checkpoint differs in precision")
    if not (path / "optimizer.pt").is_file():
        raise PilotPlanError("size source checkpoint has no optimizer state")
    size = sum(p.stat().st_size for p in path.rglob("*") if p.is_file())
    return size, {
        "kind": source.kind,
        "artifact_id": manifest.artifact_id,
        "manifest_sha256": _sha256_file(path / "manifest.json"),
        "bytes": size,
    }


def capacity_plan(
    config: Mapping[str, Any],
    pilot: SciencePilotConfig,
    *,
    checkpoint_bytes: int,
    snapshot_bytes: int,
) -> dict[str, Any]:
    """Peak new bytes: retained + new + staging copies over the planned publications."""
    from xlm.artifacts.retention import KEEP_RECOVERY
    from xlm.evaluation.cadence import CheckpointRole

    _, plan, _ = pilot_run_plans(config, pilot)
    size = checkpoint_bytes
    pinned = 0
    recovery = 0
    evaluation_recovery = 0
    steps: list[dict[str, Any]] = []
    peak = 0
    for event in plan.events:
        # Up to KEEP_RECOVERY unplanned recovery states (interrupted/cancelled/
        # time-limit) may coexist; every recovery point may be held by an
        # unresolved evaluation. Both are counted: an upper bound, not a guess.
        # Evaluation-recovery states are released once their evaluation completes,
        # but a failed evaluation pins its state until the at-budget rescore, so
        # the worst case (every one of them failed) keeps them all: counted too.
        retained = (pinned + recovery + evaluation_recovery + KEEP_RECOVERY) * size
        transient = 2 * size  # serialization directory + store staging copy
        peak = max(peak, retained + transient)
        steps.append(
            {
                "event": event.event_id,
                "role": event.role.value,
                "retained_before_bytes": retained,
                "retained_before_states": {
                    "milestones": pinned,
                    "rolling_recovery": recovery,
                    "evaluation_recovery_worst_case": evaluation_recovery,
                    "unplanned_recovery_bound": KEEP_RECOVERY,
                },
                "transient_bytes": transient,
            }
        )
        if event.role is CheckpointRole.MILESTONE:
            pinned += 1
        elif event.role is CheckpointRole.EVALUATION_RECOVERY:
            evaluation_recovery += 1
        else:
            recovery += 1
    capacity = pilot.capacity
    receipts = (len(plan.events) + KEEP_RECOVERY) * 2 * CHECKPOINT_RECEIPT_CAP
    job_json = 6 * EXECUTION_JSON_CAP
    job_peak = (
        peak
        + receipts
        + int(capacity.evaluation_evidence_bytes or 0)
        + int(capacity.cache_bytes or 0)
        + LAUNCHER_OUTPUT_CAP
        + job_json
    )
    output_bytes = snapshot_bytes + 2 * EXECUTION_JSON_CAP
    return {
        "checkpoint_bytes": size,
        "publications": len(plan.events),
        "evaluation_recovery_publications": evaluation_recovery,
        "worst_case": "every evaluation-recovery state stays pinned by a failed evaluation "
        "until the at-budget rescore; two unplanned terminal recovery states coexist; each "
        "publication adds a serialization copy and a store staging copy",
        "publication_steps": steps,
        "peak_checkpoint_bytes": peak,
        "checkpoint_receipt_bound_bytes": receipts,
        "evaluation_evidence_bytes": capacity.evaluation_evidence_bytes,
        "cache_bytes": capacity.cache_bytes,
        "worker_log_bound_bytes": LAUNCHER_OUTPUT_CAP,
        "job_json_bound_bytes": job_json,
        "job_directory_peak_bytes": job_peak,
        "output_root_bytes": output_bytes,
        "safety_margin_bytes": capacity.safety_margin_bytes,
        "basis": "measured checkpoint size; code-enforced bounds; operator-declared evidence, "
        "cache and margin",
    }


def check_capacity(
    config: Mapping[str, Any],
    pilot: SciencePilotConfig,
    findings: Findings,
    *,
    snapshot_bytes: int,
) -> dict[str, Any] | None:
    before = len(findings.blockers)
    capacity = pilot.capacity
    missing = [
        k
        for k in ("evaluation_evidence_bytes", "cache_bytes", "safety_margin_bytes")
        if getattr(capacity, k) is None
    ]
    if capacity.checkpoint_bytes is None:
        missing.append("checkpoint_bytes (measured size source)")
    if missing:
        findings.block("capacity_unresolved", f"unresolved capacity inputs: {missing}")
        findings.section("capacity", before=before)
        return None
    assert capacity.checkpoint_bytes is not None
    report = capacity_plan(
        config, pilot, checkpoint_bytes=capacity.checkpoint_bytes, snapshot_bytes=snapshot_bytes
    )
    limit = int(pilot.expected.limits.max_new_output_gib * GIB)
    declared = _get(config, "resources.max_new_disk_gib")
    if report["job_directory_peak_bytes"] > limit:
        findings.block(
            "new_output_exceeds_limit",
            f"peak {report['job_directory_peak_bytes']:,} bytes exceeds the "
            f"{pilot.expected.limits.max_new_output_gib} GiB new-output limit",
        )
    if declared is not None and report["job_directory_peak_bytes"] > float(declared) * GIB:
        findings.block(
            "new_output_exceeds_declared_cap",
            f"peak exceeds resources.max_new_disk_gib = {declared} GiB",
        )
    margin = int(capacity.safety_margin_bytes or 0)
    for name, needed in (
        ("checkpoint_root", report["job_directory_peak_bytes"]),
        ("output_root", report["output_root_bytes"]),
    ):
        root = getattr(pilot.storage_roots, name)
        if root is None or not Path(root).is_dir():
            continue
        free = shutil.disk_usage(root).free
        report[f"{name}_free_bytes"] = free
        if free < needed + margin:
            findings.block(
                "insufficient_capacity",
                f"{name} has {free:,} free bytes; needs {needed:,} plus a {margin:,} margin",
            )
    report["storage_estimate_gib"] = (
        math.ceil(report["job_directory_peak_bytes"] / GIB * 1000) / 1000
    )
    findings.section("capacity", before=before, **report)
    return report


# --------------------------------------------------------------- schedules


def schedules(config: Mapping[str, Any], pilot: SciencePilotConfig) -> dict[str, Any]:
    """Exact planned events with their projected first-crossing boundaries."""
    from xlm.artifacts.retention import KEEP_RECOVERY, POLICY
    from xlm.evaluation.cadence import (
        BOUNDARY_PHASES,
        projected_first_crossings,
        update_arithmetic,
    )
    from xlm.evaluation.recoverability import (
        RecoverabilityError,
        recoverability_table,
        table_digest,
    )

    ex = pilot.expected
    budget, batch = ex.budget_valid_targets, ex.global_batch_valid_targets
    try:
        evaluation, checkpoints, policy = pilot_run_plans(config, pilot)
    except RecoverabilityError:
        # The recoverability preflight blocks such a plan; schedules still show M3 rows.
        evaluation, checkpoints, policy = pilot_run_plans(
            {
                **config,
                "training": {**config.get("training", {}), "evaluation_recoverability": None},
            },
            pilot,
        )
    project = {
        **projected_first_crossings(
            [e.threshold for e in checkpoints.events],
            budget_valid_targets=budget,
            global_batch_valid_targets=batch,
        ),
        **projected_first_crossings(
            [e.threshold for e in evaluation.events],
            budget_valid_targets=budget,
            global_batch_valid_targets=batch,
        ),
    }
    checkpoint_rows = [
        {**event.to_dict(), **project[event.threshold]} for event in checkpoints.events
    ]
    boundaries = {row["projected_committed_targets"] for row in checkpoint_rows}
    evaluation_rows = [
        {
            **event.to_dict(),
            **project[event.threshold],
            "rescorable_from_planned_checkpoint": project[event.threshold][
                "projected_committed_targets"
            ]
            in boundaries
            and event.tier.value in ("quick_lm", "full_lm"),
        }
        for event in evaluation.events
    ]
    table = recoverability_table(evaluation, checkpoints, policy)
    return {
        "arithmetic": update_arithmetic(budget, batch),
        "checkpoints": checkpoint_rows,
        "evaluations": evaluation_rows,
        "recoverability": {
            "policy": policy.identity() if policy is not None else None,
            "table": table,
            "table_digest": table_digest(table, policy),
        },
        "boundary_order": list(BOUNDARY_PHASES),
        "retention": {
            "policy": POLICY,
            "keep_recovery": KEEP_RECOVERY,
            "pinned": [r["event_id"] for r in checkpoint_rows if r["role"] == "milestone"],
            "evaluation_recovery": [
                r["event_id"] for r in checkpoint_rows if r["role"] == "evaluation_recovery"
            ],
            "also_kept": [
                "last good state",
                "declared/fork-parent references",
                "states needed by unresolved evaluation events (by model-state digest)",
            ],
        },
        "resume_verification": pilot.resume_verification.model_dump(mode="json"),
    }


def scientific_identity(config: Mapping[str, Any], pilot: SciencePilotConfig) -> dict[str, Any]:
    training = config.get("training", {})
    return {
        "contract": pilot.contract,
        "seed_tuple": pilot.seed_tuple,
        "seeds": {k: training.get(k) for k in ("init_seed", "training_seed", "data_seed")},
        "science_version": training.get("science_version"),
        "lr_policy": training.get("lr_policy"),
        "schedule": training.get("schedule"),
        "optimizer": config.get("optimizer"),
        "precision": training.get("precision"),
        "runtime": training.get("runtime"),
        "producer_prefetch": training.get("producer_prefetch"),
        "producer_content_verification": "always on for process_depth1 (build_training_batcher)",
        "device": training.get("device"),
        "microbatch_sequences": training.get("microbatch_sequences"),
        "document_order": pilot.document_order
        if isinstance(pilot.document_order, str)
        else pilot.document_order.model_dump(mode="json"),
        "model": {
            k: config.get("model", {}).get(k)
            for k in (
                "vocab_size",
                "num_layers",
                "hidden_size",
                "num_attention_heads",
                "intermediate_size",
                "context_length",
                "dropout",
                "attention_backend",
            )
        },  # fmt: skip
    }


# --------------------------------------------------------------- preflight


@dataclass
class PreflightContext:
    resolved: dict[str, Any] | None = None
    exec_bindings: dict[str, Any] | None = None
    capacity: dict[str, Any] | None = None
    profile: dict[str, Any] | None = None


def run_preflight(
    config: dict[str, Any],
    pilot: SciencePilotConfig,
    findings: Findings,
    *,
    artifact_home: Path,
    outputs: Sequence[Path],
    snapshot_bytes: int,
    measured_profile: Mapping[str, Any] | None,
    measure_size: bool,
    bound_cost: Mapping[str, Any] | None = None,
) -> PreflightContext:
    """Every verification of a bound pilot configuration; nothing trains or allocates a GPU.

    Planning (``measure_size``) verifies the measured profile and the checkpoint
    size source and writes the measured size into the configuration. Validation
    of a frozen plan passes ``bound_cost``: the plan hash already binds both.
    """
    from xlm.experiments.execution import resolve_execution_config
    from xlm.training.components import component_catalog
    from xlm.training.inputs import resolve_training_tokenizer

    context = PreflightContext()
    check_storage_roots(config, pilot, findings, artifact_home=artifact_home, outputs=outputs)
    # Order validation opens shard readers too: gate both it and resolution on
    # the cheap size diagnostic, before reading or hashing any shard contents.
    before = len(findings.blockers)
    check_input_bytes(config, findings)
    if len(findings.blockers) > before:
        return context
    check_document_order(config, pilot, findings)
    check_recoverability(config, pilot, findings)
    before = len(findings.blockers)
    try:
        resolved, exec_bindings = resolve_execution_config(copy.deepcopy(config))
    except Exception as exc:  # noqa: BLE001 - any resolution failure blocks the plan
        findings.block("execution_resolution", f"{type(exc).__name__}: {exc}"[:800])
        findings.section("resolution", before=before)
        return context
    findings.section("resolution", before=before, bindings=exec_bindings)
    context.resolved, context.exec_bindings = resolved, exec_bindings
    check_config(resolved, pilot, findings)
    check_tokenizer(resolved, exec_bindings, pilot, findings)
    data = check_data(resolved, pilot, findings)
    tokenizer, _ = resolve_training_tokenizer(
        copy.deepcopy(resolved["data"]), component_catalog(resolved.get("plugins"))
    )
    check_evaluation(resolved, pilot, data["components"], tokenizer, findings)
    mixture = open_mixture(resolved)
    try:
        check_heldout_membership(resolved, mixture, findings)
        check_cold_coverage(resolved, mixture, pilot, findings)
    finally:
        for reader in mixture.readers.values():
            close = getattr(reader, "close", None)
            if close is not None:
                close()
    before = len(findings.blockers)
    if bound_cost is not None:
        findings.section(
            "profile", before=before, status="BOUND_BY_PLAN_HASH", cost=dict(bound_cost)
        )
    elif measured_profile is None and pilot.contract == P35_PILOT:
        findings.block("profile_missing", "no measured profile bound (--profile)")
    elif measured_profile is None:
        findings.section(
            "profile",
            before=before,
            status="NOT_RUN",
            note="authored fixture: cost is unmeasured and recorded as such, never as zero",
        )
    else:
        from xlm.training.profile import resolve_measured_profile

        try:
            context.profile = resolve_measured_profile(
                measured_profile, model_config=resolved["model"], training=resolved["training"]
            )
        except ValueError as exc:
            findings.block("profile_mismatch", str(exc))
    if context.profile is not None:
        low, high = context.profile["throughput_tokens_per_sec_range"][:2]
        eta = [
            pilot.expected.budget_valid_targets / high,
            pilot.expected.budget_valid_targets / low,
        ]
        if eta[1] > pilot.expected.limits.total_wall_seconds:
            findings.warnings.append(
                f"the measured training-only ETA upper bound {eta[1]:.0f}s exceeds the total "
                "wall allowance; the pilot would end INCOMPLETE"
            )
        findings.section("profile", before=before, eta_seconds_training_only=eta)
    elif bound_cost is None and measured_profile is not None:
        findings.section("profile", before=before)
    if measure_size and pilot.capacity.checkpoint_size_source is not None:
        try:
            size, source = checkpoint_size_from_source(
                pilot.capacity.checkpoint_size_source, resolved, measured_profile=measured_profile
            )
        except (PilotPlanError, ValueError, OSError) as exc:
            findings.block("checkpoint_size_unresolved", str(exc))
        else:
            config["science_pilot"]["capacity"]["checkpoint_bytes"] = size
            pilot.capacity.checkpoint_bytes = size
            findings.preflight["checkpoint_size_source"] = {"status": "VERIFIED", **source}
    context.capacity = check_capacity(config, pilot, findings, snapshot_bytes=snapshot_bytes)
    return context


# ------------------------------------------------------------------ result


@dataclass
class PilotPlanResult:
    status: PilotStatus
    review: dict[str, Any]
    plan: Any = None


def _draft_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def render_commands(
    status: PilotStatus,
    *,
    plan: Any,
    plan_path: Path | None,
    snapshot_dir: Path | None,
    artifact_home: Path,
    blockers: Sequence[Any],
) -> dict[str, Any]:
    """Existing user-only commands, or an explicit BLOCKED launch."""
    prefix = "uv run --offline --locked --extra cuda --extra eval"
    if status not in (PilotStatus.RESOLVED, PilotStatus.EXECUTABLE) or plan is None:
        return {
            "launch": "BLOCKED",
            "reasons": [f"{b.code}: {b.detail}" for b in blockers],
            "next": f"{prefix} xlm experiment plan <draft> --bindings <bindings.json> "
            "--profile <profile.json> --output <plan.json> --review <review.json> "
            "--snapshot-dir <snapshot dir>",
        }
    plan_arg = str(plan_path) if plan_path is not None else "<plan.json>"
    snap = str(snapshot_dir) if snapshot_dir is not None else "<snapshot dir>"
    budget = plan.budget_valid_targets
    return {
        "launch": "USER ONLY: review, authorize, then submit; nothing here was executed",
        "environment": f"$env:XLM_HOME = '{artifact_home}'",
        "validate": f"{prefix} xlm experiment validate {plan_arg}",
        "authorize": (
            f"{prefix} xlm experiment authorize --plan-hash {plan.plan_hash} "
            f"--max-targets {budget} --max-seconds {plan.budget_max_seconds} "
            f"--max-disk-gib {plan.storage_estimate_gib} --approver <YOUR NAME> "
            "--ticket-id <TICKET ID> --output <ticket.json>"
        ),
        "validate_authorized": (
            f"{prefix} xlm experiment validate {plan_arg} --ticket <ticket.json>"
        ),
        "submit": (
            f"{prefix} xlm experiment submit {plan_arg} --ticket <ticket.json> --device cuda "
            f"--snapshot-dir {snap} --max-retries 1"
        ),
        "run": f"{prefix} xlm queue run --device cuda --once",
        "resume": (
            f"{prefix} xlm queue run --device cuda --once  "
            "# after an interrupted runner: stale-job recovery requeues within --max-retries; "
            "the attempt receives only the remaining total wall allowance"
        ),
    }


def plan_science_pilot(
    draft_path: Path,
    *,
    workspace_root: Path,
    bindings_path: Path | None = None,
    snapshot_dir: Path | None = None,
    output_path: Path | None = None,
    review_path: Path | None = None,
    measured_profile: Mapping[str, Any] | None = None,
    artifact_home: Path | None = None,
) -> PilotPlanResult:
    """DRAFT + bindings -> BLOCKED or RESOLVED (frozen, hash-bound, unauthorized)."""
    from xlm.config.composer import ConfigComposer
    from xlm.config.schemas import ExperimentDraftConfig
    from xlm.core.paths import ArtifactPaths

    home = (artifact_home or ArtifactPaths.from_env().root).resolve()
    composed = ConfigComposer(workspace_root).compose(draft_path)
    if "science_pilot" not in composed:
        raise PilotPlanError("the draft has no science_pilot section")
    ExperimentDraftConfig.model_validate(composed)
    pilot = SciencePilotConfig.model_validate(composed["science_pilot"])
    findings = Findings()
    check_expectation(pilot, findings)
    review: dict[str, Any] = {
        "review_version": REVIEW_VERSION,
        "draft": {
            "path": str(draft_path),
            "sha256": _draft_hash(draft_path),
            "id": composed.get("id"),
        },
        "contract": pilot.contract,
        "research": pilot.contract == P35_PILOT,
        "device": pilot.device,
    }
    if bindings_path is None:
        if pilot.status != "draft_nonexecutable":
            findings.block("draft_status", "a checked-in pilot draft must be draft_nonexecutable")
        check_config(composed, pilot, findings)
        check_recoverability(composed, pilot, findings)
        for item in unresolved_fields(composed, pilot):
            findings.block("unresolved", item)
        review.update(
            status=PilotStatus.DRAFT.value,
            schedules=schedules(composed, pilot),
            scientific_identity=scientific_identity(composed, pilot),
            candidate_identity=identity_digest(composed),
        )
        return _finish(review, findings, PilotStatus.DRAFT, None, None, None, home)
    raw = json.loads(Path(bindings_path).read_text(encoding="utf-8"))
    bindings = PilotBindings.model_validate(raw)
    merged = apply_bindings(composed, pilot, bindings)
    pilot = SciencePilotConfig.model_validate(merged["science_pilot"])
    review["bindings"] = {"path": str(bindings_path), "sha256": _draft_hash(Path(bindings_path))}
    outputs = [p for p in (output_path, review_path, snapshot_dir) if p is not None]
    context = run_preflight(
        merged,
        pilot,
        findings,
        artifact_home=home,
        outputs=outputs,
        snapshot_bytes=0,
        measured_profile=measured_profile,
        measure_size=True,
    )
    review.update(
        schedules=schedules(merged, pilot),
        scientific_identity=scientific_identity(context.resolved or merged, pilot),
        candidate_identity=identity_digest(merged),
    )
    if findings.blockers:
        review["status"] = PilotStatus.BLOCKED.value
        return _finish(review, findings, PilotStatus.BLOCKED, None, None, None, home)
    plan = _freeze(merged, pilot, context, workspace_root, snapshot_dir, composed)
    # The captured code snapshot is an output of planning: count it on its root.
    before = len(findings.blockers)
    report = check_capacity(merged, pilot, findings, snapshot_bytes=plan.code_snapshot.total_bytes)
    if report is not None:
        review["capacity"] = report
    if len(findings.blockers) > before:
        review["status"] = PilotStatus.BLOCKED.value
        return _finish(review, findings, PilotStatus.BLOCKED, None, None, None, home)
    if output_path is not None:
        plan.save(output_path)
    review["status"] = PilotStatus.RESOLVED.value
    return _finish(review, findings, PilotStatus.RESOLVED, plan, output_path, snapshot_dir, home)


def _freeze(
    merged: dict[str, Any],
    pilot: SciencePilotConfig,
    context: PreflightContext,
    workspace_root: Path,
    snapshot_dir: Path | None,
    composed: Mapping[str, Any],
) -> Any:
    from datetime import UTC, datetime

    from xlm.config.science import seed_fields
    from xlm.experiments.plans import (
        PLAN_VERSION,
        ExecutablePlan,
        _classify_horizon,
        dependency_hash,
        freeze_execution,
    )
    from xlm.experiments.snapshot import capture_snapshot

    if snapshot_dir is None:
        raise PilotPlanError("freezing a resolved pilot plan requires --snapshot-dir")
    assert context.capacity is not None
    training = merged["training"]
    budget = int(training["budget"]["max_valid_targets"])
    snapshot = capture_snapshot(workspace_root, snapshot_dir)
    draft_id = str(composed.get("id", "science_pilot"))
    cost: dict[str, Any]
    if context.profile is not None:
        low, high = context.profile["throughput_tokens_per_sec_range"][:2]
        cost = {
            "basis": "measured_profile",
            "eta_seconds": [budget / high, budget / low],
            "profile": merged["resources"]["profile_artifact"],
            "scope": "training steps only; evaluation, checkpoints and startup excluded",
        }
    else:
        cost = {
            "basis": "unmeasured_authored_fixture",
            "eta_seconds": None,
            "profile": merged["resources"]["profile_artifact"],
        }
    plan = ExecutablePlan(
        plan_version=PLAN_VERSION,
        plan_id=f"plan_{draft_id}",
        plan_hash="",
        draft_id=draft_id,
        track=str(merged.get("track", "baseline")),
        horizon_kind=_classify_horizon(budget, int(training["schedule"]["horizon_valid_targets"])),
        resolved_config=merged,
        code_snapshot=snapshot,
        dependency_hash=dependency_hash(workspace_root),
        seeds=seed_fields(training),
        budget_valid_targets=budget,
        budget_max_seconds=float(training["budget"]["max_train_seconds"]),
        estimated_new_disk_gib=context.capacity["storage_estimate_gib"],
        gpu_processes=int(merged["resources"].get("max_gpu_processes", 1)),
        evaluation_tier=str(merged["evaluation"].get("suite", "search")),
        checkpoint_every_valid_targets=int(training["checkpoint_every_valid_targets"]),
        evaluation_every_valid_targets=int(merged["evaluation"].get("every_valid_targets", budget)),
        exposure={
            "mixture_id": (merged["data"].get("mixture_details") or {}).get("id"),
            "weights": dict((merged["data"].get("mixture_details") or {}).get("weights", {})),
            "data_seed": training["data_seed"],
        },
        cost_estimate=cost,
        storage_estimate_gib=context.capacity["storage_estimate_gib"],
        created_at=datetime.now(UTC).isoformat(),
    )
    return freeze_execution(plan, snapshot_dir, [pilot.device])


def _finish(
    review: dict[str, Any],
    findings: Findings,
    status: PilotStatus,
    plan: Any,
    plan_path: Path | None,
    snapshot_dir: Path | None,
    home: Path,
) -> PilotPlanResult:
    review["blockers"] = [b.to_dict() for b in findings.blockers]
    review["warnings"] = list(findings.warnings)
    review["preflight"] = findings.preflight
    review["plan"] = (
        {"plan_id": plan.plan_id, "plan_hash": plan.plan_hash, "path": str(plan_path)}
        if plan is not None
        else None
    )
    review["authorization"] = {
        "state": "not_authorized",
        "mechanism": "existing plan-hash ticket: xlm experiment authorize (user only)",
        "planning_never_authorizes": True,
    }
    review["commands"] = render_commands(
        status,
        plan=plan,
        plan_path=plan_path,
        snapshot_dir=snapshot_dir,
        artifact_home=home,
        blockers=findings.blockers,
    )
    return PilotPlanResult(status, review, plan)


def validate_science_pilot_plan(
    plan: Any,
    *,
    ticket: Any = None,
    plan_path: Path | None = None,
    artifact_home: Path | None = None,
) -> PilotPlanResult:
    """Re-verify a frozen pilot plan now; a covering operator ticket makes it EXECUTABLE."""
    from xlm.core.paths import ArtifactPaths
    from xlm.experiments.authorization import AuthorizationError, validate_against_ticket

    home = (artifact_home or ArtifactPaths.from_env().root).resolve()
    findings = Findings()
    review: dict[str, Any] = {"review_version": REVIEW_VERSION, "validation": True}
    try:
        plan.validate_identity()
    except Exception as exc:  # noqa: BLE001 - an unfrozen or altered plan never validates
        findings.block("plan_identity", str(exc))
        review["status"] = PilotStatus.BLOCKED.value
        return _finish(review, findings, PilotStatus.BLOCKED, None, plan_path, None, home)
    config = copy.deepcopy(plan.resolved_config)
    if "science_pilot" not in config:
        raise PilotPlanError("not a science pilot plan")
    pilot = SciencePilotConfig.model_validate(config["science_pilot"])
    review.update(contract=pilot.contract, research=pilot.contract == P35_PILOT)
    check_expectation(pilot, findings)
    context = run_preflight(
        config,
        pilot,
        findings,
        artifact_home=home,
        outputs=[p for p in (plan_path,) if p is not None],
        snapshot_bytes=plan.code_snapshot.total_bytes,
        measured_profile=None,
        measure_size=False,
        bound_cost=plan.cost_estimate,
    )
    if context.exec_bindings is not None and plan.execution_envelope is not None:
        if context.exec_bindings != plan.execution_envelope["bindings"]:
            findings.block(
                "inputs_changed_since_planning",
                "data, tokenizer or component bindings differ from the frozen envelope",
            )
    review.update(
        plan_hash=plan.plan_hash,
        schedules=schedules(config, pilot),
        scientific_identity=scientific_identity(config, pilot),
    )
    status = PilotStatus.BLOCKED if findings.blockers else PilotStatus.RESOLVED
    if status is PilotStatus.RESOLVED and ticket is not None:
        try:
            effective = validate_against_ticket(plan.to_dict(), ticket)
        except AuthorizationError as exc:
            findings.block("ticket_does_not_authorize", str(exc))
            status = PilotStatus.BLOCKED
        else:
            limit = pilot.expected.limits.max_new_output_gib
            if effective.max_new_disk_gib is None or effective.max_new_disk_gib > limit:
                findings.block(
                    "ticket_exceeds_output_limit",
                    f"the ticket must bound new output at <= {limit} GiB",
                )
                status = PilotStatus.BLOCKED
            else:
                status = PilotStatus.EXECUTABLE
                review["authorization"] = {"ticket_id": effective.ticket_id}
    review["status"] = status.value
    result = _finish(review, findings, status, plan, plan_path, None, home)
    if status is PilotStatus.EXECUTABLE:
        result.review["authorization"] = {
            "state": "authorized_by_operator_ticket",
            "ticket_id": ticket.ticket_id,
            "plan_hash": plan.plan_hash,
        }
    return result


def pilot_runtime_preflight(plan: Any, artifact_home: Path) -> list[str]:
    """Cheap checks the queue repeats before leasing a GPU for a pilot plan."""
    config = plan.resolved_config
    if "science_pilot" not in config:
        return []
    pilot = SciencePilotConfig.model_validate(config["science_pilot"])
    findings = Findings()
    check_storage_roots(config, pilot, findings, artifact_home=artifact_home, outputs=[])
    check_capacity(config, pilot, findings, snapshot_bytes=0)
    return [f"{b.code}: {b.detail}" for b in findings.blockers]
