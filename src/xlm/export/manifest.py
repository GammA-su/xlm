"""Export manifest: hashes, provenance and accounting for one portable bundle (P20)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

EXPORT_FORMAT_VERSION = "1"

# Serializer releases the loader understands. Anything else is refused with
# migration guidance instead of a best-effort parse.
SUPPORTED_SERIALIZERS = ("safetensors-0.8",)


class ExportError(RuntimeError):
    """Raised when an export cannot be written or verified safely."""


class IncompatibleExportError(ExportError):
    """Raised when an export's format, serializer or plugin cannot be honored."""


@dataclass(frozen=True)
class ExportFile:
    """One file in the bundle with its integrity hash."""

    path: str
    sha256: str
    size_bytes: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ExportManifest:
    """Complete provenance and accounting for a portable model bundle."""

    export_format_version: str
    export_id: str
    architecture: str
    architecture_version: str
    plugin_id: str | None
    plugin_version: str | None
    serializer: str
    model_hash: str
    config_hash: str
    tokenizer_type: str
    tokenizer_hash: str
    normalization_policy: str
    vocab_size: int
    special_ids: dict[str, int]
    context_length: int
    generation_defaults: dict[str, Any]
    tied_mapping: dict[str, list[str]]
    parameters_deployed: int
    parameters_active: int
    parameters_training_only: int
    excluded_training_heads: list[str] = field(default_factory=list)
    data_manifest_refs: dict[str, str] = field(default_factory=dict)
    budget_valid_targets: int | None = None
    evidence_status: str = "none"
    precision_stored: str = "float32"
    cache_support: bool = False
    includes_optimizer_state: bool = False
    files: list[ExportFile] = field(default_factory=list)
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["files"] = [f.to_dict() for f in self.files]
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExportManifest:
        payload = dict(data)
        payload["files"] = [ExportFile(**f) for f in data.get("files", [])]
        payload["special_ids"] = dict(data.get("special_ids", {}))
        payload["generation_defaults"] = dict(data.get("generation_defaults", {}))
        payload["tied_mapping"] = {k: list(v) for k, v in data.get("tied_mapping", {}).items()}
        payload["excluded_training_heads"] = list(data.get("excluded_training_heads", []))
        payload["data_manifest_refs"] = dict(data.get("data_manifest_refs", {}))
        return cls(**payload)

    def check_compatible(self) -> None:
        """Refuse formats and serializers this loader cannot honor, with guidance."""
        if self.export_format_version != EXPORT_FORMAT_VERSION:
            raise IncompatibleExportError(
                f"export format '{self.export_format_version}' is not supported by this "
                f"loader (supports '{EXPORT_FORMAT_VERSION}'). "
                "Re-export with the matching XLM release; do not hand-edit the bundle."
            )
        if self.serializer not in SUPPORTED_SERIALIZERS:
            raise IncompatibleExportError(
                f"serializer '{self.serializer}' is not supported "
                f"(supports {list(SUPPORTED_SERIALIZERS)}). "
                "Re-export with a supported serializer release."
            )
