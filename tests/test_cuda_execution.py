"""Acceptance tests for P14: CUDA execution, precision, profiling and recovery.

CPU-safe policy tests run everywhere. Tests marked `cuda` need a real NVIDIA GPU
and the CUDA torch wheel; they assert numerics, parity and continuity on the
device rather than certifying performance from CPU runs.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import torch

from xlm.artifacts.ledger import RunLedger
from xlm.artifacts.store import ArtifactStore
from xlm.config.schemas import (
    AdamWConfig,
    CrossEntropyObjectiveConfig,
    TransformerBaselineConfig,
    WarmupCosineScheduleConfig,
)
from xlm.core.contracts import LMOutput, TrainingBatch
from xlm.core.paths import ArtifactPaths
from xlm.models.backends import (
    BackendPolicyError,
    PrecisionUnsupportedError,
    maybe_compile,
    probe_sdpa_kernels,
    resolve_attention_backend,
    validate_precision,
)
from xlm.models.base import BaseModel
from xlm.models.transformer import TransformerBaseline
from xlm.objectives.base import accumulate_microbatch_gradient
from xlm.objectives.cross_entropy import CrossEntropyObjective
from xlm.optimizers.adamw import create_adamw_optimizer
from xlm.schedules.cosine import WarmupCosineSchedule
from xlm.training.checkpoint import CheckpointManager, IncompatibleCheckpointError
from xlm.training.data import TrainingBatcher
from xlm.training.profile import (
    MicrobatchResult,
    PreflightError,
    ProfileRequest,
    ProfileResult,
    ResourcePlan,
    UnfamiliarArchitectureError,
    plan_resources,
    preflight_check,
    transformer_flops_per_token,
)
from xlm.training.recovery import OOMRecoveryError, plan_oom_recovery
from xlm.training.trainer import Trainer, TrainerError

needs_cuda = pytest.mark.cuda


def tiny_config(**overrides: Any) -> TransformerBaselineConfig:
    base: dict[str, Any] = {
        "architecture": "transformer_baseline",
        "vocab_size": 64,
        "num_layers": 2,
        "hidden_size": 32,
        "num_attention_heads": 2,
        "intermediate_size": 64,
        "context_length": 16,
        "attention_backend": "eager",
        "tie_embeddings": True,
    }
    base.update(overrides)
    return TransformerBaselineConfig(**base)


def tiny_trainer(
    tmp_path: Path,
    device: str,
    precision: str = "fp32",
    max_targets: int = 64,
    seed: int = 7,
    **trainer_kwargs: Any,
) -> tuple[Trainer, TransformerBaseline, TrainingBatcher]:
    paths = ArtifactPaths(root=tmp_path / "artifacts")
    manager = CheckpointManager(
        artifact_store=ArtifactStore(paths),
        run_ledger=RunLedger(paths.ledger / "ledger.sqlite"),
        paths=paths,
    )
    config = tiny_config()
    model = TransformerBaseline(config, device=device, seed=seed)
    objective = CrossEntropyObjective(CrossEntropyObjectiveConfig())
    optimizer, manifest = create_adamw_optimizer(
        AdamWConfig(lr=0.01, weight_decay=0.0), model=model
    )
    schedule = WarmupCosineSchedule(
        WarmupCosineScheduleConfig(
            warmup_valid_targets=max_targets // 4,
            horizon_valid_targets=max_targets,
            min_lr_ratio=0.1,
        ),
        base_lr=0.01,
    )
    tokens = [((i % 60) + 4) for i in range(400)]
    batcher = TrainingBatcher(
        data_source=tokens,
        context_length=16,
        global_batch_valid_targets=32,
        exhaustion_policy="repeat_bounded",
        max_document_exposures=100,
    )
    trainer = Trainer(
        model=model,
        objective=objective,
        optimizer=optimizer,
        optimizer_manifest=manifest,
        schedule=schedule,
        batcher=batcher,
        checkpoint_manager=manager,
        run_id="p14_run",
        plan_id="p14_plan",
        device=device,
        precision=precision,
        max_valid_targets=max_targets,
        **trainer_kwargs,
    )
    return trainer, model, batcher


# ------------------------------------------------------- backend policy (CPU)


def test_profile_required_backend_is_never_defaulted() -> None:
    with pytest.raises(BackendPolicyError, match="must be resolved"):
        resolve_attention_backend("profile_required", "cpu")
    with pytest.raises(BackendPolicyError, match="Invalid attention_backend"):
        resolve_attention_backend("flash", "cpu")


def test_backend_resolution_reports_the_slower_cpu_path() -> None:
    report = resolve_attention_backend("sdpa", "cpu", arbitrary_mask=True)
    assert report.selected == "sdpa"
    joined = " ".join(report.fallback_notes)
    assert "math" in joined
    assert "slower" in joined


def test_sdpa_kernel_probe_is_honest_on_cpu() -> None:
    kernels = probe_sdpa_kernels("cpu")
    assert kernels["math"] is True
    assert kernels["flash"] is False
    assert kernels["mem_efficient"] is False


def test_precision_modes_are_validated_loudly() -> None:
    with pytest.raises(PrecisionUnsupportedError, match="Unknown precision"):
        validate_precision("int8", "cpu")
    with pytest.raises(PrecisionUnsupportedError, match="requires a CUDA device"):
        validate_precision("bf16_fp32_master", "cpu")
    with pytest.raises(PrecisionUnsupportedError, match="requires a CUDA device"):
        validate_precision("fp16", "cpu")
    assert validate_precision("fp32", "cpu") == "fp32"
    assert validate_precision("fp32", "cuda") == "fp32"


def test_trainer_rejects_unsupported_execution_modes(tmp_path: Path) -> None:
    trainer, _, _ = tiny_trainer(tmp_path / "ok", "cpu")
    assert trainer.execution_report()["precision"] == "fp32"
    with pytest.raises(PrecisionUnsupportedError, match="Unknown precision"):
        tiny_trainer(tmp_path / "bad", "cpu", precision="int8")
    with pytest.raises(PrecisionUnsupportedError, match="requires a CUDA device"):
        tiny_trainer(tmp_path / "bad2", "cpu", precision="bf16_fp32_master")


def test_maybe_compile_disabled_returns_the_model_untouched() -> None:
    model = TransformerBaseline(tiny_config(), seed=1)
    same, report = maybe_compile(model, False)
    assert same is model
    assert report == {"compiled": False, "mode": None}


def test_activation_checkpointing_parity_on_cpu() -> None:
    """Recompute must match the reference forward: memory saved, numerics kept."""
    torch.manual_seed(11)
    plain = TransformerBaseline(tiny_config(), seed=11)
    checking = TransformerBaseline(tiny_config(), seed=11, activation_checkpointing=True)
    plain.eval()
    checking.eval()
    ids = torch.randint(4, 64, (2, 16))
    with torch.no_grad():
        ref = plain(ids)
        got = checking(ids)
    assert torch.allclose(ref.logits, got.logits, atol=1e-6, rtol=1e-6)


def test_trainer_refuses_checkpointing_on_unsupported_models(tmp_path: Path) -> None:
    class MinimalModel(BaseModel):
        def forward(self, input_ids: Any, **kwargs: Any) -> Any:  # type: ignore[override]
            raise NotImplementedError

        def get_capabilities(self) -> Any:
            raise NotImplementedError

        def count_parameters(self) -> Any:
            raise NotImplementedError

    trainer, _, _ = tiny_trainer(tmp_path / "ok", "cpu")
    with pytest.raises(TrainerError, match="does not support it"):
        Trainer(
            model=MinimalModel(),
            objective=trainer.objective,
            optimizer=trainer.optimizer,
            optimizer_manifest=trainer.optimizer_manifest,
            schedule=trainer.schedule,
            batcher=trainer.batcher,
            checkpoint_manager=trainer.checkpoint_manager,
            run_id="x",
            plan_id="y",
            device="cpu",
            activation_checkpointing=True,
        )


# ------------------------------------------------- plans, preflight, recovery


def _fake_profile() -> ProfileResult:
    request = ProfileRequest(model_id="tiny", model_config={"architecture": "x"})
    sizes = [
        MicrobatchResult(
            microbatch_sequences=1,
            feasible=True,
            measured_valid_targets_per_step=64,
            tokens_per_sec=1000.0,
            peak_reserved_gib=1.0,
            optimizer_state_bytes=8,
            checkpoint_bytes=16,
            checkpoint_save_seconds=0.5,
        ),
        MicrobatchResult(
            microbatch_sequences=2,
            feasible=True,
            measured_valid_targets_per_step=128,
            tokens_per_sec=1800.0,
            peak_reserved_gib=2.0,
            optimizer_state_bytes=8,
            checkpoint_bytes=16,
            checkpoint_save_seconds=0.5,
        ),
    ]
    return ProfileResult(
        profile_version="1",
        model_id="tiny",
        request=request.to_dict(),
        preflight={},
        sizes=sizes,
        selected_microbatch_sequences=2,
        accumulation_steps=4,
        backend=None,
        resource_plan=ResourcePlan(
            throughput_tokens_per_sec_range=[1000.0, 1800.0],
            eta_seconds_range=[0.0, 0.0],
            budget_valid_targets=0,
            peak_reserved_gib=2.0,
            optimizer_state_gib=0.0,
            checkpoint_gib=0.0,
            checkpoint_save_seconds=0.5,
            unique_parameters=10,
            forward_flops_per_token_estimate=None,
            train_flops_per_token_estimate=None,
        ),
        freeze={},
    )


def test_resource_plan_reports_measured_ranges_not_peak_flops() -> None:
    profile = _fake_profile()
    plan = plan_resources(
        profile,
        budget_valid_targets=3600,
        unique_params=1_000_000,
        architecture="transformer_baseline",
    )
    assert plan.throughput_tokens_per_sec_range == [1000.0, 1800.0]
    assert plan.eta_seconds_range == pytest.approx([2.0, 3.6])
    assert plan.forward_flops_per_token_estimate == 2_000_000
    assert plan.train_flops_per_token_estimate == 6_000_000
    assert any("not a promise" in note for note in plan.uncertainty_notes)


def test_resource_plan_refuses_unfamiliar_architectures() -> None:
    with pytest.raises(UnfamiliarArchitectureError, match="no verified FLOP formula"):
        transformer_flops_per_token(1_000_000, "mamba_like_v9")


def test_preflight_refuses_instead_of_reshaping(tmp_path: Path) -> None:
    request = ProfileRequest(
        model_id="tiny",
        model_config={"vocab_size": 64, "num_layers": 1, "hidden_size": 8, "intermediate_size": 16},
        device="cpu",
        attention_backend="profile_required",
    )
    with pytest.raises(PreflightError, match="must be resolved"):
        preflight_check(request, tmp_path)

    request.attention_backend = "eager"
    request.min_disk_gib = 10**12
    with pytest.raises(PreflightError, match="required for profiles"):
        preflight_check(request, tmp_path)


def test_oom_recovery_cannot_skip_data_or_silently_change_contract() -> None:
    meta = {
        "checkpoint_id": "ckpt_1",
        "committed_valid_targets": 100,
        "precision": "bf16_fp32_master",
    }
    with pytest.raises(OOMRecoveryError, match="cursor mismatch"):
        plan_oom_recovery(meta, {"precision": "bf16_fp32_master"}, batcher_committed=90)

    same = plan_oom_recovery(meta, {"precision": "bf16_fp32_master"}, batcher_committed=100)
    assert same.action == "restart_same_contract"
    assert same.changes == {}

    forked = plan_oom_recovery(meta, {"precision": "fp32"}, batcher_committed=100)
    assert forked.action == "fork_new_contract"
    assert forked.changes == {"precision": ["bf16_fp32_master", "fp32"]}


# ------------------------------------------------------------------ CUDA


@needs_cuda
def test_cpu_reference_versus_cuda_numerics() -> None:
    """The CUDA path must reproduce the CPU reference within declared tolerance."""
    torch.cuda.is_available() or pytest.skip("CUDA unavailable")
    config = tiny_config()
    reference = TransformerBaseline(config, device="cpu", seed=5)
    candidate = TransformerBaseline(config, device="cuda", seed=5)
    candidate.load_state_dict(reference.state_dict())
    reference.eval()
    candidate.eval()
    ids = torch.randint(4, 64, (2, 16))
    with torch.no_grad():
        ref_logits = reference(ids).logits
        got_logits = candidate(ids.to("cuda")).logits
    assert torch.allclose(ref_logits, got_logits.cpu(), atol=1e-4, rtol=1e-4)


@needs_cuda
def test_cuda_sdpa_matches_eager_within_tolerance() -> None:
    torch.cuda.is_available() or pytest.skip("CUDA unavailable")
    model = TransformerBaseline(tiny_config(), device="cuda", seed=5)
    model.eval()
    ids = torch.randint(4, 64, (2, 16), device="cuda")
    with torch.no_grad():
        eager_logits = model(ids, backend_override="eager").logits
        sdpa_logits = model(ids, backend_override="sdpa").logits
    assert torch.allclose(eager_logits, sdpa_logits, atol=1e-4, rtol=1e-4)


@needs_cuda
def test_cuda_bf16_finite_backward_and_update() -> None:
    """BF16 autocast with FP32 master weights: finite loss, real update."""
    torch.cuda.is_available() or pytest.skip("CUDA unavailable")
    if not torch.cuda.is_bf16_supported():
        pytest.skip("device lacks BF16 support")
    trainer, model, _ = tiny_trainer(
        Path(__file__).parent / "tmp_probe_never",
        "cuda",
        precision="bf16_fp32_master",
        max_targets=64,
    )
    assert trainer.execution_report()["precision"] == "bf16_fp32_master"
    before = [p.detach().float().clone() for p in model.parameters()]
    first = trainer.train_step()
    second = trainer.train_step()
    assert first is not None and second is not None
    assert all(abs(m.loss) != float("inf") and m.loss == m.loss for m in (first, second))
    assert any(
        not torch.equal(a, b.float()) for a, b in zip(before, model.parameters(), strict=True)
    )


@needs_cuda
def test_cuda_activation_checkpointing_parity() -> None:
    torch.cuda.is_available() or pytest.skip("CUDA unavailable")
    plain = TransformerBaseline(tiny_config(), device="cuda", seed=9)
    checking = TransformerBaseline(
        tiny_config(), device="cuda", seed=9, activation_checkpointing=True
    )
    plain.eval()
    checking.eval()
    ids = torch.randint(4, 64, (2, 16), device="cuda")
    with torch.no_grad():
        ref = plain(ids).logits
        got = checking(ids).logits
    assert torch.allclose(ref, got, atol=1e-4, rtol=1e-4)


def _assert_compile_parity(device: str) -> None:
    from xlm.models.backends import compile_probe

    ok, reason = compile_probe(device)
    if not ok:
        pytest.skip(f"torch.compile unavailable on {device}: {reason}")
    model = TransformerBaseline(tiny_config(), device=device, seed=9)
    model.eval()
    compiled, report = maybe_compile(model, True)
    assert report["compiled"] is True
    ids = torch.randint(4, 64, (2, 16), device=device)
    with torch.no_grad():
        ref = model(ids).logits
        got = compiled(ids).logits
    assert torch.allclose(ref, got, atol=1e-4, rtol=1e-4)


def test_cpu_compile_eager_parity() -> None:
    _assert_compile_parity("cpu")


@needs_cuda
def test_cuda_compile_eager_parity() -> None:
    torch.cuda.is_available() or pytest.skip("CUDA unavailable")
    _assert_compile_parity("cuda")


@needs_cuda
def test_cuda_accumulation_matches_single_shot_token_norm() -> None:
    """Token-normalized accumulation on CUDA equals one global batch (A06 on GPU)."""
    torch.cuda.is_available() or pytest.skip("CUDA unavailable")

    def fresh() -> tuple[TransformerBaseline, Any, CrossEntropyObjective]:
        m = TransformerBaseline(tiny_config(), device="cuda", seed=3)
        o = CrossEntropyObjective(CrossEntropyObjectiveConfig())
        opt, _ = create_adamw_optimizer(AdamWConfig(lr=0.01, weight_decay=0.0), model=m)
        return m, opt, o

    torch.manual_seed(0)
    whole_ids = torch.randint(4, 64, (4, 16), device="cuda")

    def batch_of(rows: torch.Tensor) -> TrainingBatch:
        return TrainingBatch(
            input_ids=rows,
            labels=torch.roll(rows, shifts=-1, dims=1),
            loss_mask=torch.ones_like(rows),
            position_ids=None,
        )

    model_a, opt_a, obj_a = fresh()
    model_a.train()
    out1 = model_a(batch_of(whole_ids[:2]).input_ids).logits
    out2 = model_a(batch_of(whole_ids[2:]).input_ids).logits

    n_global = 2 * 16
    accumulate_microbatch_gradient(
        obj_a(LMOutput(logits=out1, auxiliary_outputs={}, state=None), batch_of(whole_ids[:2])),
        total_valid_targets=n_global,
    )
    accumulate_microbatch_gradient(
        obj_a(LMOutput(logits=out2, auxiliary_outputs={}, state=None), batch_of(whole_ids[2:])),
        total_valid_targets=n_global,
    )
    opt_a.step()

    model_b, opt_b, obj_b = fresh()
    model_b.train()
    out = model_b(whole_ids).logits
    accumulate_microbatch_gradient(
        obj_b(LMOutput(logits=out, auxiliary_outputs={}, state=None), batch_of(whole_ids)),
        total_valid_targets=n_global,
    )
    opt_b.step()

    for pa, pb in zip(model_a.parameters(), model_b.parameters(), strict=True):
        assert torch.allclose(pa, pb, atol=1e-5, rtol=1e-5)


@needs_cuda
def test_cuda_interrupted_resume_continuity(tmp_path: Path) -> None:
    """Interrupted CUDA run resumes to the same losses as the uninterrupted run."""
    torch.cuda.is_available() or pytest.skip("CUDA unavailable")

    reference, _, _ = tiny_trainer(tmp_path / "ref", "cuda", max_targets=128)
    ref_losses = []
    for _ in range(4):
        metrics = reference.train_step()
        assert metrics is not None
        ref_losses.append(metrics.loss)

    interrupted, _, batcher = tiny_trainer(tmp_path / "cut", "cuda", max_targets=128)
    first_losses = []
    for _ in range(2):
        metrics = interrupted.train_step()
        assert metrics is not None
        first_losses.append(metrics.loss)
    ckpt_dir = interrupted._save_checkpoint("p14_interrupt_ckpt")
    state = batcher.get_state()

    resumed, _, resumed_batcher = tiny_trainer(tmp_path / "res", "cuda", max_targets=128)
    resumed.checkpoint_manager.load_checkpoint(
        ckpt_dir,
        model=resumed.model,
        objective=resumed.objective,
        optimizer=resumed.optimizer,
        optimizer_manifest=resumed.optimizer_manifest,
        schedule=resumed.schedule,
        batcher=resumed_batcher,
        expected_plan_id="p14_plan",
        device="cuda",
    )
    assert resumed_batcher.get_state() == state
    resumed.step = interrupted.step
    resumed.committed_valid_targets = interrupted.committed_valid_targets
    resumed.processed_valid_targets = interrupted.processed_valid_targets
    resumed_losses = []
    for _ in range(2):
        metrics = resumed.train_step()
        assert metrics is not None
        resumed_losses.append(metrics.loss)

    assert first_losses == pytest.approx(ref_losses[:2], rel=1e-5)
    assert resumed_losses == pytest.approx(ref_losses[2:], rel=1e-5)


@needs_cuda
def test_cuda_fp16_scaler_checkpoint_resume(tmp_path: Path) -> None:
    """FP16 scaler state round-trips through checkpoints and continues finite."""
    torch.cuda.is_available() or pytest.skip("CUDA unavailable")
    trainer, _, _ = tiny_trainer(tmp_path / "fp16", "cuda", precision="fp16", max_targets=96)
    assert trainer.scaler is not None
    # Early steps may overflow and skip under the scaler protocol; drive until
    # an update commits with finite loss.
    committed = None
    for _ in range(60):
        metrics = trainer.train_step()
        if metrics is not None:
            committed = metrics
            break
    assert committed is not None and committed.loss == committed.loss
    ckpt_dir = trainer._save_checkpoint("p14_fp16_ckpt")
    assert (ckpt_dir / "scaler.pt").is_file()

    resumed, _, _ = tiny_trainer(tmp_path / "fp16b", "cuda", precision="fp16", max_targets=96)
    # Damage the fresh scaler so the restore is observable, then reload.
    damaged = dict(resumed.scaler.state_dict())
    damaged["scale"] = 1.0
    resumed.scaler.load_state_dict(damaged)
    assert resumed.scaler.get_scale() == 1.0
    resumed.checkpoint_manager.load_checkpoint(
        ckpt_dir, expected_plan_id="p14_plan", device="cuda", scaler=resumed.scaler
    )
    assert resumed.scaler.get_scale() == trainer.scaler.get_scale()
    resumed_committed = None
    for _ in range(60):
        metrics = resumed.train_step()
        if metrics is not None:
            resumed_committed = metrics
            break
    assert resumed_committed is not None
    assert resumed_committed.loss == resumed_committed.loss

    # A checkpoint carrying scaler state resumed without one is refused.
    plain, _, _ = tiny_trainer(tmp_path / "fp16c", "cuda", precision="fp32", max_targets=96)
    with pytest.raises(IncompatibleCheckpointError, match="gradient scaler state"):
        plain.checkpoint_manager.load_checkpoint(ckpt_dir, expected_plan_id="p14_plan")


@needs_cuda
def test_cuda_per_size_forward_smoke() -> None:
    """Separate real-GPU forward evidence for each tested size preset."""
    torch.cuda.is_available() or pytest.skip("CUDA unavailable")
    import json as _json

    root = Path(__file__).resolve().parents[1]
    for preset_id in ("50m", "150m", "300m"):
        data = _json.loads((root / "recipes" / "models" / f"{preset_id}.yaml").read_text())
        data.pop("schema_version", None)
        data.pop("kind", None)
        data.pop("id", None)
        data["attention_backend"] = "sdpa"
        config = TransformerBaselineConfig(**data)
        model = TransformerBaseline(config, device="cuda")
        model.eval()
        ids = torch.randint(4, config.vocab_size, (1, 8), device="cuda")
        with torch.no_grad(), torch.autocast(device_type="cuda", dtype=torch.bfloat16):
            logits = model(ids).logits
        assert logits.shape == (1, 8, config.vocab_size)
        assert torch.isfinite(logits.float()).all()
