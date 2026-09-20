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
import struct
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import safetensors
import torch
from safetensors.torch import save_file as safetensors_save

from xlm.artifacts.manifest import identity_digest
from xlm.data.normalization import compute_sha256
from xlm.export.manifest import (
    EXPORT_FORMAT_VERSION,
    STORAGE_LAYOUT_SINGLE_COPY,
    ExportError,
    ExportFile,
    ExportManifest,
)
from xlm.models.aliases import (
    ALIAS_SCHEMA_VERSION,
    AliasError,
    TensorAlias,
    alias_map,
    assert_complete_alias,
)
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


def build_single_copy_payload(
    state_dict: dict[str, Any], aliases: tuple[TensorAlias, ...]
) -> tuple[dict[str, Any], dict[str, str]]:
    """Drop alias entries so each tied tensor is serialized exactly once (C08, D07).

    The previous implementation cloned every storage-shared tensor so the safe
    format would accept the dict, which wrote the tied payload twice. Here the
    alias names are removed instead: only canonical names carry payload, and the
    manifest's alias map is what rebuilds the tie on load.

    Any storage sharing that the model did not declare is refused rather than
    cloned away, so an unsupported view cannot quietly become an ordinary
    duplicated tensor.

    :returns: the payload to serialize and the ``alias -> canonical`` mapping.
    :raises ExportError: for a dangling, self-referential or undeclared alias,
        or for undeclared shared storage between distinct entries.
    """
    mapping = alias_map(aliases)
    for alias, target in sorted(mapping.items()):
        if alias not in state_dict:
            raise ExportError(f"declared alias '{alias}' is absent from the model state")
        if target not in state_dict:
            raise ExportError(
                f"alias '{alias}' targets '{target}', which is absent from the model state"
            )
        assert_complete_alias(alias, state_dict[alias], target, state_dict[target])

    payload = {name: value for name, value in state_dict.items() if name not in mapping}

    seen: dict[int, str] = {}
    for name, value in payload.items():
        if not hasattr(value, "data_ptr") or value.numel() == 0:
            continue
        pointer = value.data_ptr()
        if pointer in seen:
            raise ExportError(
                f"'{name}' and '{seen[pointer]}' share storage but are not a declared "
                "tie; XLM refuses to clone undeclared shared storage into the bundle"
            )
        seen[pointer] = name
    return payload, mapping


def _payload_inventory(weights_path: Path) -> tuple[int, int]:
    """Read back the container header: entry count and true payload bytes.

    Measured from the written file rather than from the dict handed to the
    serializer, so the manifest's inventory reports what the bundle actually
    contains. File and header overhead are excluded.
    """
    raw = weights_path.read_bytes()
    (header_len,) = struct.unpack("<Q", raw[:8])
    header = json.loads(raw[8 : 8 + header_len].decode("utf-8"))
    entries = [meta for name, meta in header.items() if name != "__metadata__"]
    payload_bytes = sum(int(m["data_offsets"][1]) - int(m["data_offsets"][0]) for m in entries)
    return len(entries), payload_bytes


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
    publish: bool = False,
    artifact_store: Any | None = None,
) -> ExportManifest:
    """Export a model and tokenizer to a portable, verifiable directory.

    With ``publish=True`` the written bundle is additionally registered through
    the existing D01 artifact store, which supplies staging, checksums, verified
    reuse and immutable-conflict behavior. The directory itself stays standalone
    and loadable without the store either way.
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    if any((out / name).exists() for name in EXPORT_BUNDLE_FILES):
        raise ExportError(f"export directory '{out}' already holds a bundle; refusing to mix")

    config = getattr(model, "config", None)
    if config is None:
        raise ExportError("model does not possess a valid 'config' attribute")

    # `state_dict()` lists every alias name, all pointing at one tensor. Detach
    # to CPU first, then keep only canonical names so each tied payload is
    # written once. Aliases come from the model's own declaration, never from
    # value equality between independent parameters.
    state_dict = {key: value.detach().to("cpu") for key, value in model.state_dict().items()}
    try:
        declared_aliases = model.tied_parameter_aliases()
    except AliasError as exc:
        raise ExportError(f"model declares unsupported tensor aliasing: {exc}") from exc
    # Detaching to CPU produces fresh tensors, so re-derive the tie over the
    # detached copies the serializer will actually see.
    detached_aliases = tuple(
        TensorAlias(alias=entry.alias, target=entry.target) for entry in declared_aliases
    )
    for entry in detached_aliases:
        if entry.alias in state_dict and entry.target in state_dict:
            if not torch.equal(state_dict[entry.alias], state_dict[entry.target]):
                raise ExportError(
                    f"declared tie '{entry.alias}' -> '{entry.target}' holds differing "
                    "values; refusing to export an inconsistent model"
                )
            state_dict[entry.alias] = state_dict[entry.target]

    try:
        payload, mapping = build_single_copy_payload(state_dict, detached_aliases)
    except AliasError as exc:
        raise ExportError(f"tensor aliasing cannot be serialized: {exc}") from exc
    dtypes = {str(value.dtype) for value in payload.values()}
    if len(dtypes) != 1 or next(iter(dtypes)) not in (
        "torch.float32",
        "torch.float16",
        "torch.bfloat16",
    ):
        raise ExportError(f"export requires uniform float weights, found dtypes {sorted(dtypes)}")
    precision_stored = next(iter(dtypes)).split(".", 1)[1]

    # Human-readable grouping of the same fact the alias map carries, kept so a
    # v2 manifest still answers "which names are tied" in the v1 vocabulary.
    tied_mapping: dict[str, list[str]] = {}
    for alias, target in sorted(mapping.items()):
        tied_mapping.setdefault(f"tied_{target.replace('.', '_')}", [target]).append(alias)

    weights_path = out / "model.safetensors"
    safetensors_save(payload, str(weights_path))
    serialized_entries, serialized_payload_bytes = _payload_inventory(weights_path)

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
        export_format_version=EXPORT_FORMAT_VERSION,
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
        alias_map=dict(mapping),
        alias_schema_version=ALIAS_SCHEMA_VERSION,
        storage_layout=STORAGE_LAYOUT_SINGLE_COPY,
        serialized_tensor_entries=serialized_entries,
        serialized_payload_bytes=serialized_payload_bytes,
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
    if publish:
        publish_export_bundle(out, manifest, artifact_store=artifact_store)
    return manifest


def export_representation_identity(manifest: ExportManifest) -> str:
    """Identity of the *serialized representation*, not of the trained model.

    Format version, alias schema, storage layout, serializer and the alias map
    all change how the same trained weights are laid out on disk. They belong to
    the representation, which is kept separate from the checkpoint's training
    provenance: re-exporting an unchanged model under a new layout is a new
    representation of the same model, not a new model.
    """
    return identity_digest(
        {
            "export_format_version": manifest.export_format_version,
            "alias_schema_version": manifest.alias_schema_version,
            "storage_layout": manifest.storage_layout,
            "serializer": manifest.serializer,
            "alias_map": dict(sorted(manifest.alias_map.items())),
            "precision_stored": manifest.precision_stored,
            "config_hash": manifest.config_hash,
            "tokenizer_hash": manifest.tokenizer_hash,
        }
    )


def _exporter_code_hash() -> str:
    """Digest of the exporter's own sources, as its producer identity."""
    from xlm.export import loader as loader_module
    from xlm.export import manifest as manifest_module
    from xlm.models import aliases as aliases_module

    sources = {}
    for module in (manifest_module, loader_module, aliases_module):
        path = Path(str(module.__file__))
        sources[module.__name__] = hashlib.sha256(path.read_bytes()).hexdigest()
    sources[__name__] = hashlib.sha256(Path(str(__file__)).read_bytes()).hexdigest()
    return identity_digest(sources)


def _exporter_dependency_hash() -> str:
    import importlib.metadata
    import sys

    return identity_digest(
        {
            "python": sys.version.split()[0],
            "torch": importlib.metadata.version("torch"),
            "safetensors": importlib.metadata.version("safetensors"),
        }
    )


def publish_export_bundle(
    bundle_dir: Path,
    manifest: ExportManifest,
    *,
    artifact_store: Any | None = None,
    kind: str = "exports",
) -> Path:
    """Publish a written bundle through the existing D01 artifact store.

    This is a caller-side integration only: staging, checksums, verified reuse
    and immutable-conflict behavior are D01's, used through its public API. No
    second artifact store is introduced and the store's semantics are unchanged.

    The bundle stays standalone. Publication copies the same files, so a copied
    directory still loads with no store, database or machine dependency.
    """
    from xlm.artifacts.store import ArtifactStore
    from xlm.core.paths import ArtifactPaths

    store = (
        artifact_store if artifact_store is not None else ArtifactStore(ArtifactPaths.from_env())
    )
    files: dict[str, Path | bytes | str] = {
        entry.path: bundle_dir / entry.path for entry in manifest.files
    }
    files["export_manifest.json"] = bundle_dir / "export_manifest.json"
    return store.publish_artifact(
        artifact_id=manifest.export_id,
        kind=kind,
        files=files,
        producer_code_hash=_exporter_code_hash(),
        dependency_hash=_exporter_dependency_hash(),
        resolved_config_hash=export_representation_identity(manifest),
        metadata={
            "export_format_version": manifest.export_format_version,
            "alias_schema_version": manifest.alias_schema_version,
            "storage_layout": manifest.storage_layout,
            "alias_map": dict(sorted(manifest.alias_map.items())),
            "serializer": manifest.serializer,
            "model_hash": manifest.model_hash,
            "config_hash": manifest.config_hash,
            "tokenizer_hash": manifest.tokenizer_hash,
            "parameters_deployed": manifest.parameters_deployed,
            "serialized_tensor_entries": manifest.serialized_tensor_entries,
            "serialized_payload_bytes": manifest.serialized_payload_bytes,
        },
        # Wall-clock only: excluded from equivalence so two identical exports
        # reuse rather than collide.
        cosmetic_metadata={"exported_at": datetime.now(UTC).isoformat()},
    )
