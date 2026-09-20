"""Reference-checkpoint evaluation registration and bounded download plans (C11).

A reference checkpoint registration pins the exact checkpoint/tokenizer
revisions, the measured unique parameter count, and the common scoring/context
policy used for every comparator evaluation. Model-card numbers are not copied:
a comparator must be reevaluated locally before its scores are recorded.

Any model download is a separate, bounded, explicitly authorized plan: remote
hosts must be allowlisted, byte and file caps are mandatory, and unattended
authorization does not exist. The default unit-test oracle is an authored tiny
model, never a secretly downloaded public checkpoint.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from xlm.data.sources.transport import ALLOWLISTED_HOSTS

REFERENCE_REGISTRY_VERSION = "1"
COMMON_SCORING_POLICY: dict[str, Any] = {
    "boundary_policy": "joint_prefix_match_v1",
    "metric_normalization": "character_length",
    "context_policy": "rolling",
    "bos_context_only": True,
    "eos_included_in_text_metrics": False,
}


class DownloadPlanError(PermissionError):
    """Raised when a model download plan is unauthorized or unbounded."""


@dataclass(frozen=True)
class ReferenceCheckpoint:
    """A locally evaluated comparator with its exact revision and policy."""

    name: str
    checkpoint_hash: str
    tokenizer_hash: str
    unique_parameters: int
    context_length: int
    precision: str
    harness_version: str
    common_policy_id: str
    source_repository: str | None = None
    source_revision: str | None = None
    notes: tuple[str, ...] = ()

    def identity(self) -> str:
        payload = json.dumps(
            {
                "name": self.name,
                "checkpoint": self.checkpoint_hash,
                "tokenizer": self.tokenizer_hash,
                "params": self.unique_parameters,
                "context": self.context_length,
                "precision": self.precision,
                "harness": self.harness_version,
                "policy": self.common_policy_id,
                "repo": self.source_repository,
                "revision": self.source_revision,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["notes"] = list(self.notes)
        payload["identity"] = self.identity()
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ReferenceCheckpoint:
        payload = {k: v for k, v in data.items() if k not in ("identity",)}
        payload["notes"] = tuple(payload.get("notes", []))
        return cls(**payload)


def register_reference_from_model(
    name: str,
    model: Any,
    tokenizer_hash: str,
    checkpoint_hash: str,
    harness_version: str,
    *,
    source_repository: str | None = None,
    source_revision: str | None = None,
    notes: tuple[str, ...] = (),
) -> ReferenceCheckpoint:
    """Register a comparator from a locally loaded model, measured not copied.

    The unique parameter count comes from the model's own counting logic, so a
    registration cannot silently inherit a model-card number.
    """
    counts = model.count_parameters()
    return ReferenceCheckpoint(
        name=name,
        checkpoint_hash=checkpoint_hash,
        tokenizer_hash=tokenizer_hash,
        unique_parameters=int(counts.unique_deployed),
        context_length=int(model.config.context_length),
        precision="fp32",
        harness_version=harness_version,
        common_policy_id=COMMON_SCORING_POLICY["boundary_policy"],
        source_repository=source_repository,
        source_revision=source_revision,
        notes=notes,
    )


@dataclass
class ReferenceRegistry:
    """The set of registered comparators, keyed by name."""

    registry_version: str = REFERENCE_REGISTRY_VERSION
    common_policy: dict[str, Any] = field(default_factory=lambda: dict(COMMON_SCORING_POLICY))
    entries: dict[str, ReferenceCheckpoint] = field(default_factory=dict)

    def register(self, checkpoint: ReferenceCheckpoint, replace: bool = False) -> None:
        if checkpoint.name in self.entries and not replace:
            raise ValueError(
                f"reference checkpoint '{checkpoint.name}' already registered; "
                "an unchanged name must not silently change meaning."
            )
        self.entries[checkpoint.name] = checkpoint

    def to_dict(self) -> dict[str, Any]:
        return {
            "registry_version": self.registry_version,
            "common_policy": self.common_policy,
            "entries": {k: v.to_dict() for k, v in sorted(self.entries.items())},
        }

    def save(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
        return path

    @classmethod
    def load(cls, path: Path) -> ReferenceRegistry:
        data = json.loads(path.read_text(encoding="utf-8"))
        registry = cls(
            registry_version=str(data.get("registry_version", REFERENCE_REGISTRY_VERSION)),
            common_policy=dict(data.get("common_policy", COMMON_SCORING_POLICY)),
        )
        for name, payload in data.get("entries", {}).items():
            registry.entries[name] = ReferenceCheckpoint.from_dict(payload)
        return registry


@dataclass(frozen=True)
class ModelDownloadPlan:
    """A bounded, explicitly authorized plan to fetch comparator weights."""

    source_repository: str
    source_revision: str
    files: tuple[str, ...]
    max_total_bytes: int
    allowlisted_hosts: tuple[str, ...] = tuple(sorted(ALLOWLISTED_HOSTS))
    operator_authorized: bool = False
    operator_ticket: str = ""
    scratch_dir: str = ""

    def validate(self) -> None:
        if not self.operator_authorized:
            raise DownloadPlanError(
                "model download refused: no explicit operator authorization ticket. "
                "Downloads are never authorized by default."
            )
        if not self.source_revision:
            raise DownloadPlanError("model download refused: an immutable revision is mandatory.")
        if not self.files:
            raise DownloadPlanError("model download refused: no files selected.")
        if self.max_total_bytes <= 0:
            raise DownloadPlanError("model download refused: byte cap must be positive.")
        for host in self.allowlisted_hosts:
            if host not in ALLOWLISTED_HOSTS:
                raise DownloadPlanError(f"host '{host}' is not on the XLM allowlist.")
        if not self.scratch_dir:
            raise DownloadPlanError(
                "model download refused: an explicit scratch directory is required so the "
                "storage cap covers partial and temporary files."
            )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
