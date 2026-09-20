"""Resolve recorded tokenizer artifacts without substituting a missing input."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from xlm.tokenizers.base import BaseTokenizer
from xlm.tokenizers.bpe import ByteLevelBPETokenizer
from xlm.tokenizers.byte import ByteTokenizer


def load_inference_tokenizer(checkpoint: Path, explicit: str | None = None) -> BaseTokenizer:
    candidates = (
        [Path(explicit)]
        if explicit is not None
        else [
            checkpoint / "tokenizer",
            checkpoint.parent / "tokenizer",
        ]
    )
    for path in candidates:
        if (path / "tokenizer.json").is_file():
            return ByteLevelBPETokenizer.load(path)
        manifest = path / "tokenizer_manifest.json"
        if (
            manifest.is_file()
            and json.loads(manifest.read_text(encoding="utf-8")).get("type") == "byte_fixture"
        ):
            return ByteTokenizer.load(path)
    if explicit is not None:
        raise FileNotFoundError(f"declared tokenizer unavailable: {explicit}; no fallback")
    # Legacy authored byte-fixture checkpoints predate bundled tokenizers.
    # Only their exact vocabulary is supported; BPE checkpoints need an artifact.
    for name in ("config.json", "model_config.json"):
        config = checkpoint / name
        if config.is_file():
            if json.loads(config.read_text(encoding="utf-8")).get("vocab_size") == 260:
                return ByteTokenizer()
            break
    raise ValueError("checkpoint requires its frozen tokenizer; provide --tokenizer")


def checkpoint_weights_hash(checkpoint: Path) -> str:
    for name in ("model.safetensors", "model.pt"):
        path = checkpoint / name
        if path.is_file():
            with path.open("rb") as stream:
                return hashlib.file_digest(stream, "sha256").hexdigest()
    raise FileNotFoundError(f"no checkpoint weights in {checkpoint}")
