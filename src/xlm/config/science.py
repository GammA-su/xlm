"""Versioned P35 scientific training semantics (``xlm-science-v1``), data only.

Schema-v1 configurations never name these fields. They resolve through
:meth:`ScientificPolicy.from_training` to the historical behavior and never
gain identity keys, so existing envelopes, plan hashes and checkpoints keep
their original bytes. A science-v1 configuration must select every field
explicitly; there is no default that silently moves a run between versions.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

SCIENCE_V1 = "xlm-science-v1"

LEGACY_LR_POLICY = "legacy_base_then_postcommit_v1"
ENDPOINT_LR_POLICY = "target_endpoint_before_update_v1"
LR_POLICIES = (LEGACY_LR_POLICY, ENDPOINT_LR_POLICY)

LEGACY_RNG_POLICY = "legacy_init_seed_coupled_v1"
TRAINING_RNG_POLICY = "training_seed_after_construction_v1"

STATISTICAL_ATTENTION = "statistical_efficient_v1"
STRICT_ATTENTION = "strict_deterministic_v1"
ATTENTION_POLICIES = (STATISTICAL_ATTENTION, STRICT_ATTENTION)
# Deterministic cuBLAS workspace setting documented by PyTorch for strict mode.
STRICT_CUBLAS_WORKSPACE = ":4096:8"

# Training-section keys introduced by science-v1. Legacy resolution omits them.
SCIENCE_TRAINING_FIELDS = ("science_version", "lr_policy", "training_seed", "runtime")

# Envelope runtime policies. The legacy value is the historical frozen constant
# the worker applies globally; science-v1 runtime flags are owned and restored
# by the trainer's scoped runtime, never set process-wide by the worker.
LEGACY_RUNTIME_POLICY: dict[str, Any] = {"torch_threads": 1, "deterministic_algorithms": True}
SCIENCE_RUNTIME_POLICY: dict[str, Any] = {
    "torch_threads": 1,
    "scientific_runtime": "trainer_scoped_v1",
}


class ScientificPolicyError(ValueError):
    """A scientific policy is unknown, incomplete or incompatible with its run."""


@dataclass(frozen=True)
class ScientificPolicy:
    """Resolved scientific semantics of one training run.

    ``runtime`` is the requested runtime block (attention determinism, TF32,
    BF16 reduction). It is ``None`` for legacy runs, whose runtime is whatever
    the historical execution path did.
    """

    science_version: str | None
    lr_policy: str
    rng_policy: str
    training_seed: int | None
    runtime: dict[str, str] | None = field(default=None)

    @property
    def is_science(self) -> bool:
        return self.science_version is not None

    @property
    def endpoint_lr(self) -> bool:
        return self.lr_policy == ENDPOINT_LR_POLICY

    @classmethod
    def legacy(cls) -> ScientificPolicy:
        return cls(None, LEGACY_LR_POLICY, LEGACY_RNG_POLICY, None, None)

    @classmethod
    def from_training(cls, training: Mapping[str, Any]) -> ScientificPolicy:
        """Version-aware reader: an absent ``science_version`` is historical schema v1."""
        version = training.get("science_version")
        if version is None:
            present = [k for k in SCIENCE_TRAINING_FIELDS if training.get(k) is not None]
            if present:
                raise ScientificPolicyError(
                    f"training fields {present} require science_version '{SCIENCE_V1}'; "
                    "schema-v1 resolves the legacy policy implicitly"
                )
            return cls.legacy()
        if version != SCIENCE_V1:
            raise ScientificPolicyError(f"unknown science_version '{version}'")
        if training.get("lr_policy") != ENDPOINT_LR_POLICY:
            raise ScientificPolicyError(
                f"{SCIENCE_V1} requires explicit lr_policy '{ENDPOINT_LR_POLICY}'"
            )
        seed = training.get("training_seed")
        if not isinstance(seed, int) or isinstance(seed, bool) or seed < 0:
            raise ScientificPolicyError(f"{SCIENCE_V1} requires an explicit training_seed")
        runtime = training.get("runtime")
        if not isinstance(runtime, Mapping):
            raise ScientificPolicyError(f"{SCIENCE_V1} requires an explicit runtime policy")
        if runtime.get("attention_policy") not in ATTENTION_POLICIES:
            raise ScientificPolicyError(
                f"unknown attention_policy '{runtime.get('attention_policy')}'"
            )
        return cls(
            SCIENCE_V1,
            ENDPOINT_LR_POLICY,
            TRAINING_RNG_POLICY,
            seed,
            {str(k): str(v) for k, v in sorted(runtime.items())},
        )

    def identity(self) -> dict[str, Any]:
        return {
            "science_version": self.science_version,
            "lr_policy": self.lr_policy,
            "rng_policy": self.rng_policy,
            "training_seed": self.training_seed,
            "runtime": dict(self.runtime) if self.runtime is not None else None,
        }

    @classmethod
    def from_identity(cls, saved: Mapping[str, Any]) -> ScientificPolicy:
        if saved.get("lr_policy") not in LR_POLICIES:
            raise ScientificPolicyError(f"unknown saved lr_policy '{saved.get('lr_policy')}'")
        runtime = saved.get("runtime")
        return cls(
            saved.get("science_version"),
            str(saved["lr_policy"]),
            str(saved["rng_policy"]),
            saved.get("training_seed"),
            dict(runtime) if runtime is not None else None,
        )


#: Optional science-v1 training blocks that are not part of the policy identity
#: (P35 M3 checkpoint cadence; pilot-readiness recoverability policy and update
#: payload receipt). Absent keys are dropped exactly like the policy fields, so
#: configurations without them keep their historical bytes.
OPTIONAL_SCIENCE_TRAINING_BLOCKS = (
    "checkpoint_cadence",
    "evaluation_recoverability",
    "update_payload_receipt",
)


def omit_absent_science_fields(training: dict[str, Any]) -> dict[str, Any]:
    """Drop unset science keys so legacy resolution reproduces historical bytes."""
    for key in (*SCIENCE_TRAINING_FIELDS, *OPTIONAL_SCIENCE_TRAINING_BLOCKS):
        if training.get(key, ...) is None:
            del training[key]
    return training


def seed_fields(training: Mapping[str, Any]) -> dict[str, int]:
    """Plan seed tuple: historical keys, plus ``training_seed`` for science-v1 only."""
    seeds = {k: int(training[k]) for k in ("init_seed", "data_seed")}
    if training.get("training_seed") is not None:
        seeds["training_seed"] = int(training["training_seed"])
    return seeds


def runtime_policy_for(config: Mapping[str, Any], purpose: str) -> dict[str, Any]:
    """The only envelope runtime policy a resolved configuration may carry."""
    training = config.get("training", {}) if purpose == "training" else {}
    if isinstance(training, Mapping) and training.get("science_version") is not None:
        return dict(SCIENCE_RUNTIME_POLICY)
    return dict(LEGACY_RUNTIME_POLICY)
