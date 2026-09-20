"""Strict model serialization, tied-weight validation, and artifact store integration."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

import torch

from xlm.artifacts.manifest import ArtifactFile, ArtifactManifest
from xlm.artifacts.store import ArtifactStore
from xlm.core.paths import ArtifactPaths
from xlm.core.registry import architectures
from xlm.data.normalization import compute_sha256
from xlm.models.base import BaseModel


def save_model_to_directory(model: BaseModel, output_dir: Path) -> dict[str, Any]:
    """Save model configuration and weights to a directory.

    Complying with Amendment 7:
    - Persists architecture configuration to config.json.
    - Persists state_dict to model.pt using torch.save.
    - Computes and returns file manifest information.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    # 1. Save config
    config = getattr(model, "config", None)
    if config is None:
        raise ValueError("Model does not possess a valid 'config' attribute.")

    config_dict = config.model_dump() if hasattr(config, "model_dump") else dict(config)
    config_path = output_dir / "config.json"
    config_json = json.dumps(config_dict, indent=2, sort_keys=True)
    config_path.write_text(config_json, encoding="utf-8")

    # 2. Save weights
    weights_path = output_dir / "model.pt"
    state_dict = model.state_dict()
    torch.save(state_dict, weights_path)

    # 3. Compute manifest file entries
    file_entries: list[ArtifactFile] = []
    for path in sorted(output_dir.iterdir()):
        if path.is_file():
            data = path.read_bytes()
            file_entries.append(
                ArtifactFile(
                    path=path.name,
                    size_bytes=len(data),
                    sha256=compute_sha256(data),
                )
            )

    return {
        "config": config_dict,
        "files": file_entries,
    }


def load_model_from_directory(model_dir: Path, device: Any = "cpu") -> BaseModel:
    """Load model from directory with strict key/shape and tied-alias validation.

    Complying with Amendment 7:
    - Uses weights_only=True.
    - Dispatches through architectures registry.
    - Strictly validates tied weight equality in state dict if both are present.
    - Re-establishes Parameter object identity for tied weights.
    """
    config_path = model_dir / "config.json"
    if not config_path.is_file():
        config_path = model_dir / "model_config.json"
    weights_path = model_dir / "model.pt"
    safetensors_path = model_dir / "model.safetensors"

    if not config_path.is_file():
        cfg_cand = f"{model_dir / 'config.json'} or {model_dir / 'model_config.json'}"
        raise FileNotFoundError(f"Model config not found at: {cfg_cand}")
    use_safetensors = False
    if not weights_path.is_file():
        # Native export bundles carry safe weights instead of pickled tensors.
        if safetensors_path.is_file():
            use_safetensors = True
        else:
            raise FileNotFoundError(
                f"Model weights not found at: {weights_path} or {safetensors_path}"
            )

    config_dict = json.loads(config_path.read_text(encoding="utf-8"))
    arch_name = config_dict.get("architecture", "transformer_baseline")

    reg_entry = architectures.get(arch_name)
    if reg_entry.factory is None:
        raise ValueError(f"No factory registered for architecture '{arch_name}'")

    # Instantiate model via registered factory
    model = reg_entry.factory(config_dict)
    if not isinstance(model, BaseModel):
        raise TypeError(f"Factory for '{arch_name}' did not return a BaseModel instance.")

    # Load state dict safely: safetensors never executes pickle opcodes, and the
    # legacy torch path stays weights-only.
    if use_safetensors:
        from safetensors.torch import load_file as safetensors_load

        state_dict = safetensors_load(str(safetensors_path), device=str(device))
    else:
        state_dict = torch.load(weights_path, map_location=device, weights_only=True)

    # Strict tied-weight validation: if both embed_tokens and lm_head weights exist,
    # they must be bitwise identical!
    tie_embeddings = config_dict.get("tie_embeddings", True)
    if tie_embeddings:
        has_embed = "embed_tokens.weight" in state_dict
        has_head = "lm_head.weight" in state_dict
        if has_embed and has_head:
            embed_w = state_dict["embed_tokens.weight"]
            head_w = state_dict["lm_head.weight"]
            if not torch.equal(embed_w, head_w):
                raise ValueError(
                    "Conflicting weights detected in serialized state dict for tied "
                    "'embed_tokens.weight' and 'lm_head.weight'."
                )

    # Load weights into model
    model.load_state_dict(state_dict)

    # Re-establish Parameter object identity for tied weights
    if tie_embeddings:
        embed_tokens = getattr(model, "embed_tokens", None)
        lm_head = getattr(model, "lm_head", None)
        if embed_tokens is not None and lm_head is not None:
            lm_head.weight = embed_tokens.weight
            assert lm_head.weight is embed_tokens.weight, "Tied weight identity restoration failed"

    return model


def load_model_for_inference(
    target: Path | str,
    device: Any = "cpu",
    artifact_store: ArtifactStore | None = None,
    backend_override: str | None = None,
) -> BaseModel:
    """Load model architecture and weights for read-only inference or evaluation.

    Complies with Amendment 2:
    - Dedicated inference-only path: does NOT touch or restore training optimizer,
      learning rate scheduler, or global RNG state.
    - Sets model.eval() immediately.
    - Accepts directory path, file path, or artifact ID.
    """
    path = Path(target)
    if not path.exists():
        store = artifact_store or ArtifactStore(ArtifactPaths.from_env())
        for candidate in [
            store.paths.root / "model" / str(target),
            store.paths.checkpoints / str(target),
            store.paths.runs / str(target),
        ]:
            if candidate.exists():
                path = candidate
                break

    if path.is_file():
        path = path.parent

    if not path.is_dir():
        raise FileNotFoundError(f"Model or checkpoint directory not found at: {target}")

    model = load_model_from_directory(path, device=device)
    cfg = getattr(model, "config", None)
    backend_to_set = backend_override
    if (
        backend_to_set is None
        and cfg is not None
        and getattr(cfg, "attention_backend", None) == "profile_required"
    ):
        backend_to_set = "eager"
    if backend_to_set is not None:
        if cfg is not None:
            object.__setattr__(cfg, "attention_backend", backend_to_set)
        for module in model.modules():
            if hasattr(module, "attention_backend"):
                setattr(module, "attention_backend", backend_to_set)  # noqa: B010
    model.eval()
    return model


def publish_model_artifact(
    model: BaseModel,
    artifact_id: str,
    artifact_store: ArtifactStore | None = None,
    producer_code_hash: str = "P03_MODEL_DEF_01",
    dependency_hash: str = "TORCH_CPU_V2_14",
) -> ArtifactManifest:
    """Publish an initialized model to the immutable artifact store."""
    store = artifact_store or ArtifactStore(ArtifactPaths.from_env())

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        save_model_to_directory(model, tmp_path)
        files = {
            "config.json": tmp_path / "config.json",
            "model.pt": tmp_path / "model.pt",
        }
        cfg = getattr(model, "config", None)
        dest_dir = store.publish_artifact(
            artifact_id=artifact_id,
            kind="model",
            files=files,
            producer_code_hash=producer_code_hash,
            dependency_hash=dependency_hash,
            resolved_config_hash=compute_sha256((tmp_path / "config.json").read_bytes())[:16],
            metadata={
                "architecture": getattr(cfg, "architecture", "unknown"),
                "vocab_size": getattr(cfg, "vocab_size", 0),
                "hidden_size": getattr(cfg, "hidden_size", 0),
                "num_layers": getattr(cfg, "num_layers", 0),
                "num_attention_heads": getattr(cfg, "num_attention_heads", 0),
                "expected_unique_parameters": getattr(cfg, "expected_unique_parameters", None),
            },
        )
        return store.load_manifest(dest_dir)
