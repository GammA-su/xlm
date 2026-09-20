"""Tests for CheckpointManager: atomic save/load, checksumming, tied weights, and resume."""

from __future__ import annotations

import hashlib
import json
import pickle
from pathlib import Path
from typing import Any

import pytest
import torch

from xlm.artifacts.ledger import RunLedger
from xlm.artifacts.store import ArtifactStore
from xlm.config.schemas import (
    AdamWConfig,
    TransformerBaselineConfig,
    WarmupCosineScheduleConfig,
)
from xlm.core.paths import ArtifactPaths
from xlm.models.transformer import TransformerBaseline
from xlm.objectives.auxiliary_fixture import AuxiliaryLearningObjective
from xlm.optimizers.adamw import create_adamw_optimizer
from xlm.schedules.cosine import WarmupCosineSchedule
from xlm.training.checkpoint import (
    CheckpointManager,
    CorruptCheckpointError,
    IncompatibleCheckpointError,
)
from xlm.training.data import TrainingBatcher


class _UntrustedRng:
    def __reduce__(self) -> Any:
        return eval, ("{'untrusted_pickle_was_executed': True}",)


def test_checksum_valid_checkpoint_still_refuses_pickle_globals(tmp_path: Path) -> None:
    manager = CheckpointManager(paths=ArtifactPaths(root=tmp_path / "home"))
    model = TransformerBaseline(
        TransformerBaselineConfig(
            num_layers=1,
            hidden_size=16,
            num_attention_heads=2,
            intermediate_size=32,
            vocab_size=64,
            attention_backend="eager",
        )
    )
    checkpoint = manager.save_checkpoint(
        "untrusted", "run", 0, 0, 0, "plan", model, None, None, None, None, None
    )
    payload = checkpoint / "rng_state.pt"
    torch.save(_UntrustedRng(), payload)
    manifest_path = checkpoint / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for entry in manifest["files"]:
        if entry["path"] == payload.name:
            entry["size_bytes"] = payload.stat().st_size
            entry["sha256"] = hashlib.sha256(payload.read_bytes()).hexdigest()
    # Keep the hostile fixture's v2 envelope valid to reach the pickle guard.
    manifest["content_hash"] = hashlib.sha256(
        json.dumps(
            sorted(manifest["files"], key=lambda entry: entry["path"]),
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    ArtifactStore(ArtifactPaths(root=tmp_path / "home")).verify_artifact(checkpoint)
    with pytest.raises(pickle.UnpicklingError, match="Weights only load failed"):
        manager.load_checkpoint(checkpoint)


def test_checkpoint_save_and_reload_roundtrip(tmp_path: Path) -> None:
    """Verify complete checkpoint save, atomic publication, and reload cycle."""
    paths = ArtifactPaths(root=tmp_path / "artifacts")
    store = ArtifactStore(paths)
    ledger = RunLedger(paths.ledger / "ledger.sqlite")
    manager = CheckpointManager(artifact_store=store, run_ledger=ledger, paths=paths)

    config = TransformerBaselineConfig(
        architecture="transformer_baseline",
        vocab_size=64,
        num_layers=2,
        hidden_size=32,
        num_attention_heads=2,
        intermediate_size=64,
        context_length=8,
        attention_backend="eager",
        tie_embeddings=True,
    )
    opt_config = AdamWConfig(lr=0.01, weight_decay=0.05)
    sched_config = WarmupCosineScheduleConfig(
        warmup_valid_targets=10,
        horizon_valid_targets=100,
        min_lr_ratio=0.1,
    )

    model = TransformerBaseline(config, seed=42)
    obj = AuxiliaryLearningObjective(init_val=1.5, target_val=3.0)
    opt, manifest = create_adamw_optimizer(opt_config, model=model, objective=obj)
    sched = WarmupCosineSchedule(sched_config, base_lr=0.01)

    tokens = list(range(10, 50))
    batcher = TrainingBatcher(
        data_source=tokens,
        context_length=8,
        global_batch_valid_targets=8,
    )

    # Perform 1 mock step to populate optimizer moments
    mbs = batcher.next_step_microbatches()
    out = model(mbs[0].input_ids)
    loss_res = obj(out, mbs[0])
    loss_res.loss.backward()
    opt.step()
    opt.zero_grad()
    sched.apply_lr_to_optimizer(opt, counter_value=8)
    batcher.commit()

    # Save checkpoint
    chk_dir = manager.save_checkpoint(
        checkpoint_id="chk_step_1",
        run_id="run_001",
        step=1,
        committed_valid_targets=8,
        processed_valid_targets=8,
        plan_id="plan_001_id",
        model=model,
        objective=obj,
        optimizer=opt,
        optimizer_manifest=manifest,
        schedule=sched,
        batcher=batcher,
    )
    assert chk_dir.is_dir()
    assert (chk_dir / "manifest.json").is_file()
    assert (chk_dir / "_COMPLETED").is_file()
    assert (chk_dir / "model.pt").is_file()

    # Verify ledger recorded artifact
    art_record = ledger.get_artifact("chk_step_1")
    assert art_record is not None
    assert art_record["kind"] == "checkpoints"

    # Construct fresh objects
    model_fresh = TransformerBaseline(config, seed=999)
    obj_fresh = AuxiliaryLearningObjective(init_val=0.0, target_val=3.0)
    opt_fresh, manifest_fresh = create_adamw_optimizer(
        opt_config, model=model_fresh, objective=obj_fresh
    )
    sched_fresh = WarmupCosineSchedule(sched_config, base_lr=0.01)
    batcher_fresh = TrainingBatcher(
        data_source=tokens,
        context_length=8,
        global_batch_valid_targets=8,
    )

    # Load checkpoint
    meta = manager.load_checkpoint(
        chk_dir,
        model=model_fresh,
        objective=obj_fresh,
        optimizer=opt_fresh,
        optimizer_manifest=manifest_fresh,
        schedule=sched_fresh,
        batcher=batcher_fresh,
        expected_plan_id="plan_001_id",
    )

    assert meta.step == 1
    assert meta.committed_valid_targets == 8
    assert meta.plan_id == "plan_001_id"

    # Verify bitwise equality of parameters
    for (n1, p1), (n2, p2) in zip(
        model.named_parameters(), model_fresh.named_parameters(), strict=True
    ):
        assert n1 == n2
        assert torch.equal(p1, p2)

    # Verify tied weights restoration
    assert model_fresh.lm_head.weight is model_fresh.embed_tokens.weight

    # Verify objective parameter equality
    assert torch.equal(obj.aux_param, obj_fresh.aux_param)

    # Verify optimizer moments equality
    for p1, p2 in zip(model.parameters(), model_fresh.parameters(), strict=True):
        if p1 in opt.state:
            assert torch.equal(opt.state[p1]["exp_avg"], opt_fresh.state[p2]["exp_avg"])
            assert torch.equal(opt.state[p1]["exp_avg_sq"], opt_fresh.state[p2]["exp_avg_sq"])

    # Verify data batcher state restored
    assert batcher_fresh.get_state() == batcher.get_state()


def test_checkpoint_checksum_corruption_detection(tmp_path: Path) -> None:
    """Verify that tampered or corrupted files in a checkpoint are detected and rejected."""
    paths = ArtifactPaths(root=tmp_path / "artifacts")
    manager = CheckpointManager(paths=paths)

    config = TransformerBaselineConfig(
        architecture="transformer_baseline",
        vocab_size=32,
        num_layers=1,
        hidden_size=16,
        num_attention_heads=2,
        intermediate_size=32,
        context_length=8,
        attention_backend="eager",
    )
    model = TransformerBaseline(config, seed=42)

    chk_dir = manager.save_checkpoint(
        checkpoint_id="chk_corrupt",
        run_id="run_002",
        step=1,
        committed_valid_targets=8,
        processed_valid_targets=8,
        plan_id="plan_002",
        model=model,
        objective=None,
        optimizer=None,
        optimizer_manifest=None,
        schedule=None,
        batcher=None,
    )

    # Tamper with model.pt
    model_pt = chk_dir / "model.pt"
    raw_data = bytearray(model_pt.read_bytes())
    raw_data[20] ^= 0xFF  # flip bit
    model_pt.write_bytes(bytes(raw_data))

    fresh_model = TransformerBaseline(config, seed=99)
    with pytest.raises(CorruptCheckpointError):
        manager.load_checkpoint(chk_dir, model=fresh_model)


def test_checkpoint_plan_compatibility_validation(tmp_path: Path) -> None:
    """Verify that architecture or plan incompatibilities raise IncompatibleCheckpointError."""
    paths = ArtifactPaths(root=tmp_path / "artifacts")
    manager = CheckpointManager(paths=paths)

    config_1 = TransformerBaselineConfig(
        architecture="transformer_baseline",
        vocab_size=32,
        num_layers=1,
        hidden_size=16,
        num_attention_heads=2,
        intermediate_size=32,
        context_length=8,
        attention_backend="eager",
    )
    model_1 = TransformerBaseline(config_1, seed=42)

    chk_dir = manager.save_checkpoint(
        checkpoint_id="chk_compat",
        run_id="run_003",
        step=1,
        committed_valid_targets=8,
        processed_valid_targets=8,
        plan_id="plan_A_experiment",
        model=model_1,
        objective=None,
        optimizer=None,
        optimizer_manifest=None,
        schedule=None,
        batcher=None,
    )

    # 1. Mismatched expected plan_id
    fresh_model = TransformerBaseline(config_1, seed=99)
    with pytest.raises(IncompatibleCheckpointError, match="plan_id"):
        manager.load_checkpoint(chk_dir, model=fresh_model, expected_plan_id="plan_B_experiment")

    # 2. Incompatible architecture (hidden_size 32 vs 16)
    config_2 = TransformerBaselineConfig(
        architecture="transformer_baseline",
        vocab_size=32,
        num_layers=1,
        hidden_size=32,  # different!
        num_attention_heads=2,
        intermediate_size=64,
        context_length=8,
        attention_backend="eager",
    )
    model_2 = TransformerBaseline(config_2, seed=99)
    with pytest.raises(IncompatibleCheckpointError, match="hidden_size"):
        manager.load_checkpoint(chk_dir, model=model_2)


def test_checkpoint_tied_weights_mismatch_detected(tmp_path: Path) -> None:
    """Verify that conflicting tied weights in model.pt trigger CorruptCheckpointError."""
    paths = ArtifactPaths(root=tmp_path / "artifacts")
    manager = CheckpointManager(paths=paths)

    config = TransformerBaselineConfig(
        architecture="transformer_baseline",
        vocab_size=32,
        num_layers=1,
        hidden_size=16,
        num_attention_heads=2,
        intermediate_size=32,
        context_length=8,
        attention_backend="eager",
        tie_embeddings=True,
    )
    model = TransformerBaseline(config, seed=42)

    chk_dir = manager.save_checkpoint(
        checkpoint_id="chk_tied_test",
        run_id="run_004",
        step=1,
        committed_valid_targets=8,
        processed_valid_targets=8,
        plan_id="plan_004_test",
        model=model,
        objective=None,
        optimizer=None,
        optimizer_manifest=None,
        schedule=None,
        batcher=None,
    )

    # Corrupt model.pt by breaking tied weight equality
    model_pt = chk_dir / "model.pt"
    state = torch.load(model_pt, weights_only=True)
    state["lm_head.weight"] = (
        state["lm_head.weight"] + 1.0
    )  # now different from embed_tokens.weight!
    torch.save(state, model_pt)

    fresh_model = TransformerBaseline(config, seed=99)
    # Note: checksum check in verify_artifact will catch this first if manifest is checked,
    # or tied-weights check will catch it. Either way, CorruptCheckpointError is raised!
    with pytest.raises(CorruptCheckpointError):
        manager.load_checkpoint(chk_dir, model=fresh_model)


def test_checkpoint_fork_lineage(tmp_path: Path) -> None:
    """Verify that checkpoint metadata records fork parent lineage."""
    paths = ArtifactPaths(root=tmp_path / "artifacts")
    manager = CheckpointManager(paths=paths)

    config = TransformerBaselineConfig(
        architecture="transformer_baseline",
        vocab_size=32,
        num_layers=1,
        hidden_size=16,
        num_attention_heads=2,
        intermediate_size=32,
        context_length=8,
        attention_backend="eager",
    )
    model = TransformerBaseline(config, seed=42)

    chk_dir = manager.save_checkpoint(
        checkpoint_id="chk_fork_child",
        run_id="run_fork_002",
        step=10,
        committed_valid_targets=100,
        processed_valid_targets=100,
        plan_id="plan_fork_child_id",
        model=model,
        objective=None,
        optimizer=None,
        optimizer_manifest=None,
        schedule=None,
        batcher=None,
        parent_checkpoint_id="chk_parent_step_5",
        parent_plan_id="plan_parent_original_id",
    )

    meta = manager.load_checkpoint(chk_dir, model=model, expected_plan_id="plan_fork_child_id")
    assert meta.parent_checkpoint_id == "chk_parent_step_5"
    assert meta.parent_plan_id == "plan_parent_original_id"
    assert meta.committed_valid_targets == 100
