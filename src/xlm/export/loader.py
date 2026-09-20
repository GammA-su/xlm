"""Verified export loading: hashes first, safe tensors only, exact restore (P20).

Every file hash is checked before anything is deserialized. Weights load from
safetensors (never pickle); tied aliases must match bitwise and regain Parameter
identity; the tokenizer must reconstruct with an identical fingerprint. Unknown
formats, serializers or plugins are refused with migration guidance instead of
best-effort parsing. CPU and supported CUDA dtype/device loads are supported;
anything else fails loudly.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import torch
from safetensors.torch import load_file as safetensors_load

from xlm.core.registry import architectures
from xlm.export.manifest import ExportManifest
from xlm.models.base import BaseModel
from xlm.tokenizers.base import BaseTokenizer

SUPPORTED_DTYPE_LOADS = ("float32", "float16", "bfloat16")


class ExportLoadError(RuntimeError):
    """Raised when an export bundle cannot be verified or restored."""


def read_manifest(export_dir: Path | str) -> ExportManifest:
    """Read an export manifest without loading any weights."""
    manifest_path = Path(export_dir) / "export_manifest.json"
    if not manifest_path.is_file():
        raise ExportLoadError(f"export manifest not found: {manifest_path}")
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ExportLoadError(f"export manifest is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ExportLoadError("export manifest must be a mapping")
    try:
        return ExportManifest.from_dict(data)
    except (KeyError, TypeError, ValueError) as exc:
        raise ExportLoadError(f"export manifest is malformed: {exc}") from exc


def verify_export_files(export_dir: Path | str, manifest: ExportManifest) -> None:
    """Check every bundled file hash before deserialization. Names failures."""
    root = Path(export_dir)
    if not manifest.files:
        raise ExportLoadError("export manifest lists no files; refusing an empty bundle")
    for entry in manifest.files:
        candidate = root / entry.path
        if not candidate.is_file():
            raise ExportLoadError(f"incomplete export: bundled file '{entry.path}' is missing")
        actual = hashlib.sha256(candidate.read_bytes()).hexdigest()
        if actual != entry.sha256:
            raise ExportLoadError(
                f"corrupted export: '{entry.path}' hash mismatch "
                f"(expected {entry.sha256[:16]}, got {actual[:16]})"
            )


def _resolve_plugin(entry_desc: str, registry: Any, plugin_id: str) -> Any:
    try:
        return registry.get(plugin_id)
    except KeyError as exc:
        available = sorted(e.key for e in registry.list_entries())
        raise ExportLoadError(
            f"{entry_desc} requires missing plugin '{plugin_id}'. "
            f"Install and register it first; available: {available or ['(none)']}. "
            "Exports never bundle executable plugin code."
        ) from exc


def load_exported_model(
    export_dir: Path | str,
    device: Any = "cpu",
    dtype: str = "float32",
    expected_export_id: str | None = None,
) -> tuple[BaseModel, BaseTokenizer, ExportManifest]:
    """Verify and load an export bundle onto a device and dtype."""
    root = Path(export_dir)
    manifest = read_manifest(root)
    manifest.check_compatible()
    if expected_export_id is not None and manifest.export_id != expected_export_id:
        raise ExportLoadError(
            f"export id '{manifest.export_id}' does not match expected '{expected_export_id}'"
        )
    verify_export_files(root, manifest)

    if dtype not in SUPPORTED_DTYPE_LOADS:
        raise ExportLoadError(
            f"dtype '{dtype}' is not a supported load dtype {list(SUPPORTED_DTYPE_LOADS)}"
        )
    if dtype == "bfloat16" and str(device) != "cuda":
        raise ExportLoadError("bfloat16 loads require a CUDA device; request float32 on CPU")
    if dtype == "float16" and str(device) != "cuda":
        raise ExportLoadError("float16 loads require a CUDA device; request float32 on CPU")

    # Resolve model construction: baseline factory or a registered plugin family.
    if manifest.plugin_id is not None:
        entry = _resolve_plugin("model", architectures, manifest.plugin_id)
        if entry.factory is None:
            raise ExportLoadError(f"plugin '{manifest.plugin_id}' registers no model factory")
        factory = entry.factory
    else:
        try:
            baseline_entry = architectures.get(manifest.architecture)
        except KeyError as exc:
            raise ExportLoadError(
                f"architecture '{manifest.architecture}' is not registered in this build"
            ) from exc
        if baseline_entry.factory is None:
            raise ExportLoadError(f"architecture '{manifest.architecture}' registers no factory")
        factory = baseline_entry.factory

    config_path = root / "config.json"
    config_dict = json.loads(config_path.read_text(encoding="utf-8"))
    model = factory(config_dict)
    if not isinstance(model, BaseModel):
        raise ExportLoadError("model factory did not return a BaseModel instance")

    weights_path = root / "model.safetensors"
    if not weights_path.is_file():
        raise ExportLoadError("incomplete export: 'model.safetensors' is missing")
    try:
        state_dict = safetensors_load(str(weights_path), device="cpu")
    except Exception as exc:
        raise ExportLoadError(f"safe weights failed to load: {exc}") from exc

    # Tied aliases must match bitwise before identity is restored.
    for _, aliases in manifest.tied_mapping.items():
        present = [a for a in aliases if a in state_dict]
        if len(present) > 1:
            first = state_dict[present[0]]
            for alias in present[1:]:
                if not torch.equal(first, state_dict[alias]):
                    raise ExportLoadError(
                        f"tied weights diverged for '{alias}'; refusing a corrupt bundle"
                    )
    model.load_state_dict(state_dict)
    for _, aliases in manifest.tied_mapping.items():
        if "embed_tokens.weight" in aliases and "lm_head.weight" in aliases:
            embed_tokens = getattr(model, "embed_tokens", None)
            lm_head = getattr(model, "lm_head", None)
            if embed_tokens is not None and lm_head is not None:
                lm_head.weight = embed_tokens.weight

    # Tokenizer reconstruction must be unchanged. No-op plugin tokenizers
    # serialize in their base format, so the base loader plus the fingerprint
    # equality check below is the exactness proof. Anything else needs a
    # verified plugin-family loader first.
    from xlm.tokenizers.bpe import ByteLevelBPETokenizer
    from xlm.tokenizers.byte import ByteTokenizer

    tokenizer_dir = root / "tokenizer"
    tokenizer: BaseTokenizer
    if manifest.tokenizer_type == "ByteLevelBPETokenizer":
        tokenizer = ByteLevelBPETokenizer.load(tokenizer_dir)
    elif manifest.tokenizer_type in ("ByteTokenizer", "NoOpByteTokenizer"):
        tokenizer = ByteTokenizer.load(tokenizer_dir)
    else:
        raise ExportLoadError(
            f"tokenizer type '{manifest.tokenizer_type}' has no verified loader; "
            "register it as a plugin family first"
        )
    if not isinstance(tokenizer, BaseTokenizer):
        raise ExportLoadError("tokenizer factory did not return a BaseTokenizer instance")
    if tokenizer.fingerprint != manifest.tokenizer_hash:
        raise ExportLoadError(
            "reconstructed tokenizer fingerprint differs from the manifest; "
            "the tokenizer directory was modified after export"
        )
    specials = {
        "pad": int(tokenizer.pad_token_id),
        "bos": int(tokenizer.bos_token_id),
        "eos": int(tokenizer.eos_token_id),
        "unk": int(tokenizer.unk_token_id),
    }
    if specials != manifest.special_ids:
        raise ExportLoadError(
            f"tokenizer special IDs {specials} do not match the manifest {manifest.special_ids}"
        )

    torch_dtype = {"float32": torch.float32, "float16": torch.float16, "bfloat16": torch.bfloat16}[
        dtype
    ]
    model.to(device)
    if torch_dtype is not torch.float32:
        model.to(torch_dtype)
    model.eval()
    return model, tokenizer, manifest
