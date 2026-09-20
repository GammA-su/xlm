"""Declared tokenizers cannot silently disagree with the actual training vocabulary."""

from __future__ import annotations

import pytest

from xlm.experiments.execution import resolve_execution_config


def test_authored_ids_do_not_excuse_a_declared_tokenizer_vocabulary_mismatch() -> None:
    config = {
        "model": {
            "architecture": "transformer_baseline",
            "vocab_size": 32,
            "hidden_size": 16,
            "intermediate_size": 32,
            "num_layers": 1,
            "num_attention_heads": 2,
            "context_length": 8,
            "attention_backend": "eager",
        },
        "data": {"synthetic_tokens": [4, 5, 6], "tokenizer_artifact": "byte"},
        "objective": {"type": "cross_entropy"},
        "optimizer": {"type": "adamw", "lr": 0.01},
        "training": {
            "device": "cpu",
            "precision": "fp32",
            "context_length": 8,
            "budget": {"max_valid_targets": 2},
            "schedule": {"type": "constant"},
        },
    }
    with pytest.raises(ValueError, match="model vocabulary differs"):
        resolve_execution_config(config)
    config["data"].pop("tokenizer_artifact")
    _, bindings = resolve_execution_config(config)
    assert bindings["tokenizer"] == {"type": "authored-token-ids", "text_tokenizer": None}
