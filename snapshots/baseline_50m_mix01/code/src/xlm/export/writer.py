"""Native export writer: safe weights plus provenance, never secrets (P20, A35).

The bundle carries safe-tensor weights, config, tokenizer, generation
defaults and a manifest with every hash. It never bundles raw corpora (only
manifest references), credentials, unrelated run files, or optimizer state
unless explicitly opted in. Training-only objective heads are excluded with
accounting that explains the difference.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import safetensors
import torch
from safetensors.torch import save_file as safetensors_save

from xlm.data.normalization import compute_sha256
from xlm.export.manifest import ExportError, ExportFile, ExportManifest
from xlm.models.base import BaseModel
from xlm.tokenizers.base import BaseTokenizer

EXPORT_BUNDLE_FILES = ("model.safetensors", "config.json", "export_manifest.json")

# Basename patterns that must never be staged into an export bundle.
EXPORT_SECRET_PATTERNS = (
    "*.pem",
    "*.key",
    "*api*token*",
    "*auth*token*",
    "*access*token*",
    "*secret*",
    "*credential*",
    "*password*",
    ".env",
)


def _refuse_secrets(staging: Path) -> None:
    hits: list[str] = []
    for path in sorted(staging.rglob("*")):
        if not path.is_file():
            continue
        name = path.name.lower()
        for pattern in EXPORT_SECRET_PATTERNS:
            if fnmatch.fnmatchcase(name, pattern.lower()):
                hits.append(f"{path.relative_to(staging).as_posix()} (matches {pattern})")
                break
    if hits:
        raise ExportError(
            "export refused: secret-like files would enter the bundle: " + "; ".join(hits)
        )


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _expand_shared_tensors(state_dict: dict[str, Any]) -> dict[str, Any]:
    """Clone storage-shared tensors so safetensors accepts the bundle.

    Tied aliases share one storage, which the safe format refuses to write
    twice. Cloning keeps both aliases bitwise identical on disk; the manifest
    records the tied mapping and the loader re-verifies equality before
    restoring Parameter identity.
    """
    seen: dict[int, str] = {}
    expanded: dict[str, Any] = {}
    for key, value in state_dict.items():
        pointer = value.data_ptr() if hasattr(value, "data_ptr") else id(value)
        if pointer in seen:
            expanded[key] = value.clone()
        else:
            seen[pointer] = key
            expanded[key] = value
    return expanded


def export_model(
    model: BaseModel,
    tokenizer: BaseTokenizer,
    output_dir: Path | str,
    export_id: str,
    *,
    plugin_id: str | None = None,
    plugin_version: str | None = None,
    data_manifest_refs: dict[str, str] | None = None,
    budget_valid_targets: int | None = None,
    evidence_status: str = "none",
    generation_defaults: dict[str, Any] | None = None,
    normalization_policy: str = "canonical_normalize_v1",
    include_optimizer_state: bool = False,
    optimizer_state: dict[str, Any] | None = None,
    objective: Any | None = None,
) -> ExportManifest:
    """Export a model and tokenizer to a portable, verifiable directory."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    if any((out / name).exists() for name in EXPORT_BUNDLE_FILES):
        raise ExportError(f"export directory '{out}' already holds a bundle; refusing to mix")

    config = getattr(model, "config", None)
    if config is None:
        raise ExportError("model does not possess a valid 'config' attribute")

    state_dict = {key: value.detach().to("cpu") for key, value in model.state_dict().items()}
    state_dict = _expand_shared_tensors(state_dict)
    dtypes = {str(value.dtype) for value in state_dict.values()}
    if len(dtypes) != 1 or next(iter(dtypes)) not in (
        "torch.float32",
        "torch.float16",
        "torch.bfloat16",
    ):
        raise ExportError(f"export requires uniform float weights, found dtypes {sorted(dtypes)}")
    precision_stored = next(iter(dtypes)).split(".", 1)[1]

    # Tied-weight mapping: both aliases are stored and must match bitwise, so a
    # released bundle can restore exact Parameter identity on load.
    tied_mapping: dict[str, list[str]] = {}
    embed_key, head_key = "embed_tokens.weight", "lm_head.weight"
    if (
        getattr(config, "tie_embeddings", False)
        and embed_key in state_dict
        and head_key in state_dict
    ):
        if not torch.equal(state_dict[embed_key], state_dict[head_key]):
            raise ExportError("tied weights differ; refusing to export an inconsistent model")
        tied_mapping["tied_lm_head"] = [embed_key, head_key]

    weights_path = out / "model.safetensors"
    safetensors_save(state_dict, str(weights_path))

    config_dict = config.model_dump() if hasattr(config, "model_dump") else dict(config)
    config_path = out / "config.json"
    config_path.write_text(json.dumps(config_dict, indent=2, sort_keys=True), encoding="utf-8")

    tokenizer_dir = out / "tokenizer"
    tokenizer.save(tokenizer_dir)

    if include_optimizer_state:
        if optimizer_state is None:
            raise ExportError("optimizer state inclusion requested but no state supplied")
        torch.save(optimizer_state, out / "optimizer.pt")

    # Training-only heads are excluded; accounting explains the difference.
    excluded_heads: list[str] = []
    training_only_params = 0
    if objective is not None:
        for name, parameter in objective.named_parameters():
            training_only_params += parameter.numel()
            excluded_heads.append(f"objective:{name}")

    counts = model.count_parameters()
    deployed = int(counts.unique_deployed)
    training_only_params += int(counts.training_only)

    manifest = ExportManifest(
        export_format_version="1",
        export_id=export_id,
        architecture=str(getattr(config, "architecture", "transformer_baseline")),
        architecture_version=str(getattr(config, "architecture_version", "1")),
        plugin_id=plugin_id,
        plugin_version=plugin_version,
        serializer=f"safetensors-{'.'.join(safetensors.__version__.split('.')[:2])}",
        model_hash=_sha256_file(weights_path),
        config_hash=_sha256_file(config_path),
        tokenizer_type=type(tokenizer).__name__,
        tokenizer_hash=tokenizer.fingerprint,
        normalization_policy=normalization_policy,
        vocab_size=int(tokenizer.actual_vocab_size),
        special_ids={
            "pad": int(tokenizer.pad_token_id),
            "bos": int(tokenizer.bos_token_id),
            "eos": int(tokenizer.eos_token_id),
            "unk": int(tokenizer.unk_token_id),
        },
        context_length=int(getattr(config, "context_length", 0)),
        generation_defaults=dict(generation_defaults or {"max_new_tokens": 64, "do_sample": False}),
        tied_mapping=tied_mapping,
        parameters_deployed=deployed,
        parameters_active=int(counts.active),
        parameters_training_only=training_only_params,
        excluded_training_heads=excluded_heads,
        data_manifest_refs=dict(data_manifest_refs or {}),
        budget_valid_targets=budget_valid_targets,
        evidence_status=evidence_status,
        precision_stored=precision_stored,
        cache_support=bool(getattr(model.get_capabilities(), "supports_kv_cache", False)),
        includes_optimizer_state=include_optimizer_state,
        created_at=datetime.now(UTC).isoformat(),
    )

    _refuse_secrets(out)

    manifest_path = out / "export_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest.to_dict(), indent=2, sort_keys=True), encoding="utf-8"
    )
    # The manifest itself is the trust root and is never self-hashed: its
    # content changes when file hashes are recorded. Payload integrity comes
    # from the per-file hashes below; manifest authenticity is a release
    # signature concern (P21), not a self-hash.
    files = [
        ExportFile(
            path=name,
            sha256=compute_sha256((out / name).read_bytes()),
            size_bytes=(out / name).stat().st_size,
        )
        for name in sorted(
            p.relative_to(out).as_posix()
            for p in out.rglob("*")
            if p.is_file() and p.name != "export_manifest.json"
        )
    ]
    manifest.files = files
    manifest_path.write_text(
        json.dumps(manifest.to_dict(), indent=2, sort_keys=True), encoding="utf-8"
    )
    return manifest
