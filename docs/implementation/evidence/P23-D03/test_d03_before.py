"""D03 proposal evidence only: authored fixtures, no product implementation."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

from xlm.core.contracts import CanonicalDocument
from xlm.data.normalization import compute_sha256
from xlm.data.sampling import MixtureBatcher, MixtureComponent, MixtureRecipe, PackingPolicy
from xlm.data.tokens import TokenShardReader, TokenShardWriter
from xlm.tokenizers.byte import ByteTokenizer


def authored_shard(root: Path) -> TokenShardReader:
    text = "abcdefghijklmnop" * 4
    doc = CanonicalDocument(
        doc_id="authored_long", source_id="authored", source_revision="fixture-v1",
        source_file="authored.txt", source_row=0, raw_hash=compute_sha256(text),
        clean_hash=compute_sha256(text), text=text, utf8_byte_count=len(text),
        language="en", language_confidence=1.0, document_kind="prose", source_metadata={},
        parent_ids=[], license_reference="authored fixture", transform_log=[],
        quality_reasons=[], cluster_ids={}, split="train",
    )
    TokenShardWriter(root, "authored_shard", "authored", ByteTokenizer()).write_documents([doc], add_special_tokens=True)
    reader = TokenShardReader(root)
    reader.verify_integrity()
    return reader


def test_document_visit_respects_declared_cap(tmp_path: Path) -> None:
    reader = authored_shard(tmp_path / "shard")
    recipe = MixtureRecipe(mixture_id="capped", components=[MixtureComponent(source_id="authored", weight=1.0)],
                           packing=PackingPolicy(max_document_tokens=4))
    batcher = MixtureBatcher(recipe, {"authored": reader}, context_length=16, global_batch_valid_targets=16)
    before = batcher._uncommitted["cursors"]["authored"]
    window, _ = batcher._next_window("authored")
    drawn = batcher._uncommitted["cursors"]["authored"] - before
    (tmp_path / "observed.json").write_text(json.dumps({"declared_cap": 4, "drawn_tokens": drawn, "window_tokens": len(window)}))
    assert drawn <= 4, "PackingPolicy.max_document_tokens must bound one document/source visit"


def test_uncapped_authored_stream_positive_control(tmp_path: Path) -> None:
    reader = authored_shard(tmp_path / "shard")
    recipe = MixtureRecipe(mixture_id="uncapped", components=[MixtureComponent(source_id="authored", weight=1.0)])
    batcher = MixtureBatcher(recipe, {"authored": reader}, context_length=16, global_batch_valid_targets=16)
    batches = batcher.next_step_microbatches()
    assert sum(batch.metadata["valid_targets"] for batch in batches) == 16
    assert all(source == "authored" for batch in batches for row in batch.source_attribution for source in row)


def test_public_cli_executes_registered_objective(tmp_path: Path) -> None:
    from xlm.config import schemas  # register the existing built-ins
    from xlm.core.registry import objectives

    assert schemas is not None and objectives.get("noop_objective", "1").factory is not None
    plan = {
        "id": "d03_registered_objective", "model": {"architecture": "transformer_baseline", "vocab_size": 64,
            "hidden_size": 16, "intermediate_size": 32, "num_layers": 1, "num_attention_heads": 2,
            "context_length": 8, "attention_backend": "eager"},
        "objective": {"type": "noop_objective", "version": "1"},
        "optimizer": {"type": "adamw", "lr": 0.01},
        "data": {"synthetic_tokens": [4 + i % 50 for i in range(64)]},
        "training": {"device": "cpu", "precision": "fp32", "context_length": 8, "global_batch_valid_targets": 8,
                     "budget": {"max_valid_targets": 8, "max_train_seconds": 10},
                     "schedule": {"type": "warmup_cosine", "warmup_valid_targets": 0, "horizon_valid_targets": 8}},
    }
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan), encoding="utf-8")
    result = subprocess.run([sys.executable, "-m", "xlm.cli.main", "train", str(path), "--device", "cpu"],
        env={**os.environ, "XLM_HOME": str(tmp_path / "home")}, capture_output=True, text=True, timeout=180)
    (tmp_path / "observed.json").write_text(json.dumps({"returncode": result.returncode, "stdout": result.stdout,
        "stderr": result.stderr, "checkpoints": [str(p) for p in (tmp_path / "home").rglob("model.pt")]}), encoding="utf-8")
    assert result.returncode == 0, result.stderr
