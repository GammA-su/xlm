"""Science-v1 comparison tracks: three-way field classification (P35 M4, §B/§E/§M).

Every scientific field of a run's evidence is classified per track as

- ``MUST_MATCH``: must be known and equal within a pair;
- ``INTENTIONALLY_VARIED``: may differ **only** when the comparison manifest
  explicitly declares it as an intended difference;
- ``RECORDED_MAY_DIFFER``: shown in the field diff, never invalidating.

Tracks classify *every* field explicitly; a field missing from a track's table
is a programming error, not a silent pass. Unknown (``None``) values of a
MUST_MATCH or INTENTIONALLY_VARIED field make a pair ineligible: null means
unknown, never equal.

Only the microbatch-grouping and data-mixture tracks carry certified
eligibility semantics in M4. The architecture, objective, optimizer and
tokenizer tracks declare their intended-difference allowlists so a future
milestone can certify them without replacing this framework; any comparison
under them is INELIGIBLE today.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

TRACK_TABLE_VERSION = "xlm-science-tracks-v1"


class FieldClass(StrEnum):
    MUST_MATCH = "MUST_MATCH"
    INTENTIONALLY_VARIED = "INTENTIONALLY_VARIED"
    RECORDED_MAY_DIFFER = "RECORDED_MAY_DIFFER"


#: Scientific evidence fields, in report order, with their meaning.
SCIENTIFIC_FIELDS: dict[str, str] = {
    # model / objective
    "model_architecture": "resolved model config excluding the attention backend",
    "model_component": "architecture component key and serializer",
    "model_parameter_count": "unique deployed parameters (meta count)",
    "attention_backend": "model attention backend",
    "initial_model_state_digest": "weights digest at the checkpoint@0 milestone",
    "objective": "resolved objective config and component",
    # seeds / order (pairing identity)
    "init_seed": "initialization seed",
    "training_seed": "post-construction training RNG seed",
    "data_seed": "source scheduling seed",
    "order_manifest_id": "independent within-source document-order manifest (M5)",
    "within_source_order_policy": "within-source document order policy",
    # data
    "tokenizer_identity": "tokenizer fingerprint bound at execution",
    "vocab_size": "model vocabulary size",
    "data_input_identity": "bound training-input identity (pool/shards)",
    "mixture_components": "mixture components and valid-target weights",
    "mixture_exhaustion_policy": "repeat/exhaustion policy",
    "mixture_scheduler": "source scheduler algorithm",
    "packing_policy": "source-local packing / cross-document policy",
    "context_length": "training context length",
    "exposure_plan_identity": "frozen token exposure-plan identity",
    "data_trace_digest": "chained committed per-target trace (source/doc/offset/label/bytes)",
    "per_source_exposure": "committed per-source valid/repeated/content/EOS targets and bytes",
    # budget / batch
    "global_batch_valid_targets": "valid targets per optimizer update",
    "microbatch_sequences": "sequences per accumulation call",
    "budget_targets": "hard declared target budget",
    "committed_targets": "actually committed valid targets",
    "update_boundaries_digest": "digest of every update's (committed_before, valid_targets)",
    # optimization
    "optimizer": "resolved optimizer config (type, LR, betas, eps, decay flags)",
    "gradient_clip_norm": "global gradient clip norm",
    "schedule": "resolved LR schedule (warmup, horizon, minimum ratio)",
    "lr_policy": "first-update LR policy",
    "science_version": "scientific contract version",
    # execution
    "precision": "training precision policy",
    "runtime_policy": "attention determinism / TF32 / BF16 reduction policy",
    "compile": "torch.compile flag",
    "activation_checkpointing": "activation checkpointing flag",
    "producer_prefetch": "data producer mode",
    "device": "training device",
    "runtime_device_name": "observed accelerator name(s) from runtime receipts",
    "runtime_torch": "observed torch version(s) from runtime receipts",
    "code_hash": "captured source snapshot hash",
    "dependency_hash": "lockfile hash",
    "environment_digest": "installed runtime environment identity",
    # evaluation
    "evaluation_plan_digest": "frozen evaluation cadence identity",
    "evaluator_identities": "per-tier evaluator identity (inventory, scoring policy, scorer)",
    "checkpoint_plan_digest": "frozen checkpoint cadence identity",
    # recorded run facts
    "run_id": "run identifier",
    "plan_hash": "frozen plan hash",
    "execution_hash": "execution envelope hash",
    "checkpoint_id": "endpoint checkpoint artifact id",
    "final_model_state_digest": "endpoint weights digest",
    "observed_attention_ops": "attention operators observed by the runtime probe",
}

#: Fields always recorded but never a comparability condition.
_ALWAYS_RECORDED = frozenset(
    {
        "run_id",
        "plan_hash",
        "execution_hash",
        "checkpoint_id",
        "final_model_state_digest",
        "observed_attention_ops",
    }
)

#: Pairing fields; they must match within a pair on every track.
PAIRING_FIELDS = ("init_seed", "training_seed", "data_seed", "order_manifest_id")


@dataclass(frozen=True)
class ScienceTrack:
    """One versioned comparison track and its field classification."""

    track_id: str
    certified: bool
    varied: frozenset[str]
    description: str
    #: Consequential fields that may only be declared together with a cause.
    consequences: Mapping[str, frozenset[str]]

    def classify(self, name: str) -> FieldClass:
        if name not in SCIENTIFIC_FIELDS:
            raise KeyError(f"unknown scientific field '{name}'")
        if name in self.varied:
            return FieldClass.INTENTIONALLY_VARIED
        if name in _ALWAYS_RECORDED:
            return FieldClass.RECORDED_MAY_DIFFER
        return FieldClass.MUST_MATCH

    def table(self) -> dict[str, str]:
        return {name: self.classify(name).value for name in SCIENTIFIC_FIELDS}

    def must_match(self) -> list[str]:
        return [n for n in SCIENTIFIC_FIELDS if self.classify(n) is FieldClass.MUST_MATCH]

    def to_dict(self) -> dict[str, Any]:
        return {
            "track_id": self.track_id,
            "track_table_version": TRACK_TABLE_VERSION,
            "certified": self.certified,
            "description": self.description,
            "classification": self.table(),
            "consequences": {k: sorted(v) for k, v in sorted(self.consequences.items())},
        }


MICROBATCH_TRACK = ScienceTrack(
    track_id="microbatch_grouping_v1",
    certified=True,
    # Grouping legitimately changes reduction order, so the endpoint weights and
    # the plan/execution hashes differ; they are recorded, never compared.
    varied=frozenset({"microbatch_sequences"}),
    description=(
        "Only the accumulation grouping (sequences per call) may differ. Model, "
        "initialization, global valid targets per update, budget, the exact committed "
        "target trace and update boundaries, per-source exposure, tokenizer, optimizer, "
        "LR policy/horizon, precision, attention policy and evaluation are fixed. "
        "Parameter hashes are not compared. No microbatch winner is selected in M4."
    ),
    consequences={},
)

MIXTURE_TRACK = ScienceTrack(
    track_id="data_mixture_v1",
    certified=True,
    varied=frozenset(
        {
            "mixture_components",
            "mixture_exhaustion_policy",
            "data_input_identity",
            "exposure_plan_identity",
            "data_trace_digest",
            "per_source_exposure",
        }
    ),
    description=(
        "Mixture weights/quotas are the intervention; the global token order, source "
        "counts, exposure plan and committed trace may differ as their consequence. "
        "Model, initialization, tokenizer, budget, update boundaries, optimization "
        "contract, seed pairing, within-source order policy, scheduler, packing and "
        "evaluation definitions are fixed. Repeat/exhaustion rules may differ only when "
        "declared as an intervention; the bound input set only as a declared consequence "
        "of changed components."
    ),
    consequences={
        "exposure_plan_identity": frozenset({"mixture_components", "mixture_exhaustion_policy"}),
        "data_trace_digest": frozenset({"mixture_components", "mixture_exhaustion_policy"}),
        "per_source_exposure": frozenset({"mixture_components", "mixture_exhaustion_policy"}),
        "data_input_identity": frozenset({"mixture_components"}),
    },
)


def _future(track_id: str, varied: set[str], description: str) -> ScienceTrack:
    return ScienceTrack(
        track_id=track_id,
        certified=False,
        varied=frozenset(varied),
        description=description + " NOT CERTIFIED in M4: every comparison is INELIGIBLE.",
        consequences={},
    )


FUTURE_TRACKS = (
    _future(
        "architecture_v0_uncertified",
        {
            "model_architecture",
            "model_component",
            "model_parameter_count",
            "initial_model_state_digest",
            "code_hash",
        },
        "Declared allowlist for a future architecture track.",
    ),
    _future("objective_v0_uncertified", {"objective", "code_hash"}, "Future objective track."),
    _future(
        "optimizer_v0_uncertified",
        {"optimizer", "gradient_clip_norm", "code_hash"},
        "Future optimizer track (equal tuning allowance required).",
    ),
    _future(
        "tokenizer_v0_uncertified",
        {
            "tokenizer_identity",
            "vocab_size",
            "model_parameter_count",
            "initial_model_state_digest",
            "model_architecture",
            "data_input_identity",
            "exposure_plan_identity",
            "data_trace_digest",
            "per_source_exposure",
        },
        "Future tokenizer track: BPB primary on identical canonical bytes.",
    ),
)

SCIENCE_TRACKS: dict[str, ScienceTrack] = {
    t.track_id: t for t in (MICROBATCH_TRACK, MIXTURE_TRACK, *FUTURE_TRACKS)
}


def get_track(track_id: str) -> ScienceTrack | None:
    return SCIENCE_TRACKS.get(track_id)
