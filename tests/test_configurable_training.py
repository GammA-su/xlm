"""Maintained D03 configurable-training regressions using authored CPU fixtures."""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from xlm.core.contracts import CanonicalDocument
from xlm.data.normalization import compute_sha256
from xlm.data.sampling import MixtureBatcher, MixtureComponent, MixtureRecipe, PackingPolicy
from xlm.data.tokens import TokenShardReader, TokenShardWriter
from xlm.tokenizers.byte import ByteTokenizer


def authored_shard(
    root: Path, source_id: str = "authored", text: str = "abcdefghijklmnop" * 4
) -> TokenShardReader:
    doc = CanonicalDocument(
        doc_id="authored_long",
        source_id=source_id,
        source_revision="fixture-v1",
        source_file="authored.txt",
        source_row=0,
        raw_hash=compute_sha256(text),
        clean_hash=compute_sha256(text),
        text=text,
        utf8_byte_count=len(text),
        language="en",
        language_confidence=1.0,
        document_kind="prose",
        source_metadata={},
        parent_ids=[],
        license_reference="authored fixture",
        transform_log=[],
        quality_reasons=[],
        cluster_ids={},
        split="train",
    )
    TokenShardWriter(root, source_id + "_shard", source_id, ByteTokenizer()).write_documents(
        [doc], add_special_tokens=True
    )
    reader = TokenShardReader(root)
    reader.verify_integrity()
    return reader


def test_document_visit_respects_declared_cap(tmp_path: Path) -> None:
    reader = authored_shard(tmp_path / "shard")
    recipe = MixtureRecipe(
        mixture_id="capped",
        components=[MixtureComponent(source_id="authored", weight=1.0)],
        packing=PackingPolicy(max_document_tokens=4),
    )
    batcher = MixtureBatcher(
        recipe, {"authored": reader}, context_length=16, global_batch_valid_targets=16
    )
    before = batcher._uncommitted["cursors"]["authored"]
    window, _ = batcher._next_window("authored")
    drawn = batcher._uncommitted["cursors"]["authored"] - before
    (tmp_path / "observed.json").write_text(
        json.dumps({"declared_cap": 4, "drawn_tokens": drawn, "window_tokens": len(window)})
    )
    assert drawn <= 4, "PackingPolicy.max_document_tokens must bound one document/source visit"


def test_uncapped_authored_stream_positive_control(tmp_path: Path) -> None:
    reader = authored_shard(tmp_path / "shard")
    recipe = MixtureRecipe(
        mixture_id="uncapped", components=[MixtureComponent(source_id="authored", weight=1.0)]
    )
    batcher = MixtureBatcher(
        recipe, {"authored": reader}, context_length=16, global_batch_valid_targets=16
    )
    batches = batcher.next_step_microbatches()
    assert sum(batch.metadata["valid_targets"] for batch in batches) == 16
    assert all(
        source == "authored"
        for batch in batches
        for row in batch.source_attribution
        for source in row
    )


@pytest.mark.serial
def test_public_cli_executes_registered_objective(tmp_path: Path) -> None:
    from xlm.config import schemas  # register the existing built-ins
    from xlm.core.registry import objectives

    assert schemas is not None and objectives.get("noop_objective", "1").factory is not None
    plan = {
        "id": "d03_registered_objective",
        "model": {
            "architecture": "transformer_baseline",
            "vocab_size": 64,
            "hidden_size": 16,
            "intermediate_size": 32,
            "num_layers": 1,
            "num_attention_heads": 2,
            "context_length": 8,
            "attention_backend": "eager",
        },
        "objective": {"type": "noop_objective", "version": "1"},
        "optimizer": {"type": "adamw", "lr": 0.01},
        "data": {"synthetic_tokens": [4 + i % 50 for i in range(64)]},
        "training": {
            "device": "cpu",
            "precision": "fp32",
            "context_length": 8,
            "global_batch_valid_targets": 8,
            "budget": {"max_valid_targets": 8, "max_train_seconds": 10},
            "schedule": {
                "type": "warmup_cosine",
                "warmup_valid_targets": 0,
                "horizon_valid_targets": 8,
            },
        },
    }
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan), encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-m", "xlm.cli.main", "train", str(path), "--device", "cpu"],
        env={**os.environ, "XLM_HOME": str(tmp_path / "home")},
        capture_output=True,
        text=True,
        timeout=180,
    )
    (tmp_path / "observed.json").write_text(
        json.dumps(
            {
                "returncode": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "checkpoints": [str(p) for p in (tmp_path / "home").rglob("model.pt")],
            }
        ),
        encoding="utf-8",
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("cap", [1, 4, 17])
@pytest.mark.parametrize("mode", ["causal_stream", "isolated_document"])
def test_capped_visits_preserve_every_target_and_real_document(
    tmp_path: Path, cap: int, mode: str
) -> None:
    reader = authored_shard(tmp_path / "shard", text="abcdefghijklmnop")
    recipe = MixtureRecipe(
        mixture_id="complete",
        components=[MixtureComponent(source_id="authored", weight=1)],
        packing=PackingPolicy(
            mode=mode, cross_document_attention=mode == "causal_stream", max_document_tokens=cap
        ),
    )
    stream = MixtureBatcher(
        recipe, {"authored": reader}, context_length=8, global_batch_valid_targets=7
    )
    actual = []
    for remaining in (7, 7, 3):
        batches = stream.next_step_microbatches(remaining)
        stream.commit()
        trace = stream.get_state()["last_step_trace"]
        assert len(trace) == remaining
        actual.extend(trace)
        assert sum(b.metadata["valid_targets"] for b in batches) == remaining
        assert all(t["doc_id"] == "authored_long" for t in trace)
    tokens = reader.read_tokens()
    assert [t["token_offset"] for t in actual] == list(range(1, len(tokens)))
    assert [t["label"] for t in actual] == tokens[1:]
    assert sum(t["label"] == ByteTokenizer().eos_token_id for t in actual) == 1
    assert sum(end - start for start, end in (t["byte_span"] for t in actual)) == 16
    assert stream.get_state()["cursors"]["authored"] == len(tokens)


def test_cap_changes_real_source_visits_and_shares(tmp_path: Path) -> None:
    readers = {
        source: authored_shard(tmp_path / source, source, text=letter * 256)
        for source, letter in (("alpha", "A"), ("zeta", "z"))
    }

    def run(cap: int | None, weights: tuple[float, float]) -> tuple[list[dict], dict]:
        recipe = MixtureRecipe(
            mixture_id="visits",
            components=[
                MixtureComponent(source_id=s, weight=w)
                for s, w in zip(readers, weights, strict=True)
            ],
            packing=PackingPolicy(max_document_tokens=cap),
        )
        stream = MixtureBatcher(recipe, readers, context_length=16, global_batch_valid_targets=64)
        stream.next_step_microbatches()
        stream.commit()
        return stream.get_state()["last_step_trace"], stream.share_report()

    capped, report = run(4, (0.5, 0.5))
    uncapped, _ = run(None, (0.5, 0.5))
    skewed, skew_report = run(4, (0.75, 0.25))

    def longest_visit(trace: list[dict]) -> int:
        from itertools import groupby

        return max(len(list(group)) for _, group in groupby(trace, lambda t: t["source_id"]))

    assert longest_visit(capped) <= 4
    assert longest_visit(uncapped) > 4
    assert [t["source_id"] for t in capped] != [t["source_id"] for t in uncapped]
    assert report["max_drift"] <= 4 / 64
    assert abs(skew_report["target_shares"]["alpha"] - 0.75) <= 4 / 64
    assert sum(t["source_id"] == "alpha" for t in skewed) > sum(
        t["source_id"] == "alpha" for t in capped
    )
    assert all(
        t["label"] == (ord("A") + 4 if t["source_id"] == "alpha" else ord("z") + 4) for t in capped
    )


def test_mixture_partial_budget_rollback_and_resume_preserve_suffix(tmp_path: Path) -> None:
    reader = authored_shard(tmp_path / "shard")
    recipe = MixtureRecipe(
        mixture_id="suffix",
        components=[MixtureComponent(source_id="authored", weight=1)],
        packing=PackingPolicy(max_document_tokens=4),
    )

    def fresh() -> MixtureBatcher:
        return MixtureBatcher(
            recipe, {"authored": reader}, context_length=16, global_batch_valid_targets=9
        )

    stream = fresh()
    stream.next_step_microbatches(5)
    stream.commit()
    saved = copy.deepcopy(stream.get_state())
    assert saved["cursors"]["authored"] == 6
    stream.next_step_microbatches(7)
    stream.rollback()
    assert stream.get_state() == saved
    restored = fresh()
    restored.load_state(saved)
    for candidate in (stream, restored):
        candidate.next_step_microbatches(7)
        candidate.commit()
    assert stream.get_state() == restored.get_state()
    assert [t["token_offset"] for t in restored.get_state()["last_step_trace"]] == list(
        range(6, 13)
    )
