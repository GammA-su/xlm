"""Bounded CUDA/CPU profiling with calibrated workers and resource plans.

Contract C10: microbatch sizes are searched by measurement, the intended global
valid-token batch is preserved through accumulation, and context, precision or
architecture are never changed silently to make a run fit. Calibration runs in a
separate process per candidate size so an OOM cannot poison the calling process.
Resource plans quote measured throughput ranges with documented uncertainty --
never theoretical peak FLOPs as a promised training time -- and operation counts
are exact where counted, labeled estimates elsewhere. An unfamiliar architecture
cannot default to the ordinary Transformer formula unnoticed.
"""

from __future__ import annotations

import hashlib
import json
import math
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

try:
    import torch
except ImportError:
    torch = None  # type: ignore[assignment]


class PreflightError(RuntimeError):
    """Raised when profiling cannot start safely; nothing is auto-adjusted."""


class UnfamiliarArchitectureError(ValueError):
    """Raised when FLOP estimation is requested for a non-Transformer architecture."""


PROFILE_VERSION = "1"


@dataclass
class ProfileRequest:
    """A bounded profiling request. Every performance-relevant setting is explicit."""

    model_id: str
    model_config: dict[str, Any]
    device: str = "cuda"
    precision: str = "bf16_fp32_master"
    attention_backend: str = "sdpa"
    global_batch_valid_targets: int = 65536
    context_length: int = 512
    microbatch_candidates: list[int] = field(default_factory=lambda: [1, 2, 4, 8, 16])
    dry_steps: int = 2
    seed: int = 101
    compile: bool = False
    compile_mode: str = "default"
    activation_checkpointing: bool = False
    max_vram_fraction: float = 0.9
    min_disk_gib: float = 5.0
    worker_timeout_seconds: float = 600.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class MicrobatchResult:
    """Measured outcome for one microbatch candidate size."""

    microbatch_sequences: int
    feasible: bool
    failure_reason: str | None = None
    measured_valid_targets_per_step: int = 0
    tokens_per_sec: float = 0.0
    step_seconds: float = 0.0
    peak_allocated_gib: float = 0.0
    peak_reserved_gib: float = 0.0
    cpu_rss_gib: float = 0.0
    loader_wait_seconds: float = 0.0
    optimizer_state_bytes: int = 0
    checkpoint_bytes: int = 0
    checkpoint_save_seconds: float = 0.0
    build_seconds: float = 0.0
    backend: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ResourcePlan:
    """A resource plan built from measurements, with labeled uncertainty."""

    throughput_tokens_per_sec_range: list[float]
    eta_seconds_range: list[float]
    budget_valid_targets: int
    peak_reserved_gib: float
    optimizer_state_gib: float
    checkpoint_gib: float
    checkpoint_save_seconds: float
    unique_parameters: int
    forward_flops_per_token_estimate: int | None
    train_flops_per_token_estimate: int | None
    uncertainty_notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ProfileResult:
    """The full calibrated profile for one model preset."""

    profile_version: str
    model_id: str
    request: dict[str, Any]
    preflight: dict[str, Any]
    sizes: list[MicrobatchResult]
    selected_microbatch_sequences: int | None
    accumulation_steps: int | None
    backend: dict[str, Any] | None
    resource_plan: ResourcePlan | None
    freeze: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["resource_plan"] = self.resource_plan.to_dict() if self.resource_plan else None
        return payload


def _config_hash(model_config: dict[str, Any]) -> str:
    encoded = json.dumps(model_config, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()[:16]


def preflight_check(request: ProfileRequest, output_dir: Path) -> dict[str, Any]:
    """Refuse to profile when the environment cannot honor the request.

    Checks device availability and wheel build, VRAM lower bound, and disk
    headroom. A failure names its reason; the request is never reshaped to fit.
    """
    if torch is None:
        raise PreflightError("Profiling requires torch; no torch installation found.")
    if not request.microbatch_candidates:
        raise PreflightError("At least one microbatch candidate size is required.")
    if request.attention_backend == "profile_required":
        raise PreflightError(
            "attention_backend 'profile_required' must be resolved to 'eager' or 'sdpa' "
            "explicitly (e.g. --attention-backend) before profiling."
        )

    report: dict[str, Any] = {
        "device": request.device,
        "torch_version": getattr(torch, "__version__", "unknown"),
        "torch_cuda_build": getattr(getattr(torch, "version", None), "cuda", None),
    }
    if request.device == "cuda":
        if not torch.cuda.is_available():
            raise PreflightError(
                "device 'cuda' requested but torch.cuda.is_available() is False. "
                "Install the CUDA torch wheel or profile explicitly on 'cpu'."
            )
        props = torch.cuda.get_device_properties(0)
        total_gib = props.total_memory / (1024**3)
        report.update(
            {
                "gpu_name": props.name,
                "compute_capability": f"{props.major}.{props.minor}",
                "vram_total_gib": round(total_gib, 2),
            }
        )
        # Lower bound: parameters + gradients + FP32 Adam states, before activations.
        unique_params = _estimate_unique_params(request.model_config)
        lower_bound_gib = unique_params * (2 + 2 + 4 + 4) / (1024**3)
        report["vram_lower_bound_gib"] = round(lower_bound_gib, 2)
        if lower_bound_gib > total_gib:
            raise PreflightError(
                f"Model lower bound {lower_bound_gib:.2f} GiB exceeds device VRAM "
                f"{total_gib:.2f} GiB. Profiling refused rather than shrinking "
                "context, precision or architecture."
            )

    output_dir.mkdir(parents=True, exist_ok=True)
    free_gib = shutil.disk_usage(output_dir).free / (1024**3)
    report["disk_free_gib"] = round(free_gib, 2)
    if free_gib < request.min_disk_gib:
        raise PreflightError(
            f"Only {free_gib:.2f} GiB free under {output_dir}; "
            f"{request.min_disk_gib:.2f} GiB required for profiles and checkpoints."
        )
    return report


def _estimate_unique_params(model_config: dict[str, Any]) -> int:
    """Parameter lower-bound estimate from config for the preflight VRAM check."""
    vocab = int(model_config.get("vocab_size", 0))
    layers = int(model_config.get("num_layers", 0))
    hidden = int(model_config.get("hidden_size", 0))
    ffn = int(model_config.get("intermediate_size", 0))
    if not (vocab and layers and hidden and ffn):
        return 0
    # Embedding + per-layer attention/FFN/RMSNorm terms; tied head counted once.
    return vocab * hidden + layers * (4 * hidden * hidden + 3 * hidden * ffn + 2 * hidden)


def _run_worker(request: ProfileRequest, microbatch: int, work_dir: Path) -> MicrobatchResult:
    """Calibrate one microbatch size in an isolated process with a timeout."""
    work_dir.mkdir(parents=True, exist_ok=True)
    # Each worker calibrates under its own artifact root: shared checkpoint IDs
    # must never resolve to another model's artifacts through idempotent publish.
    worker_home = work_dir / "xlm_home"
    worker_home.mkdir(parents=True, exist_ok=True)
    req_path = work_dir / f"req_{microbatch}.json"
    res_path = work_dir / f"res_{microbatch}.json"
    worker_request = {
        "model_id": request.model_id,
        "model_config": request.model_config,
        "device": request.device,
        "precision": request.precision,
        "attention_backend": request.attention_backend,
        "microbatch_sequences": microbatch,
        "context_length": request.context_length,
        "dry_steps": request.dry_steps,
        "seed": request.seed,
        "compile": request.compile,
        "compile_mode": request.compile_mode,
        "activation_checkpointing": request.activation_checkpointing,
    }
    req_path.write_text(json.dumps(worker_request), encoding="utf-8")

    try:
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "xlm.training.profile_worker",
                str(req_path),
                str(res_path),
                str(worker_home),
            ],
            capture_output=True,
            text=True,
            timeout=request.worker_timeout_seconds,
            cwd=Path.cwd(),
        )
    except subprocess.TimeoutExpired:
        return MicrobatchResult(
            microbatch_sequences=microbatch,
            feasible=False,
            failure_reason=f"worker timeout after {request.worker_timeout_seconds}s",
        )
    if completed.returncode != 0 or not res_path.is_file():
        stderr = (completed.stderr or "")[-500:]
        return MicrobatchResult(
            microbatch_sequences=microbatch,
            feasible=False,
            failure_reason=f"worker crashed (exit {completed.returncode}): {stderr}",
        )
    payload = json.loads(res_path.read_text(encoding="utf-8"))
    if not payload.get("feasible", False):
        return MicrobatchResult(
            microbatch_sequences=microbatch,
            feasible=False,
            failure_reason=str(payload.get("failure_reason", "unknown")),
        )
    return MicrobatchResult(
        microbatch_sequences=microbatch,
        feasible=True,
        measured_valid_targets_per_step=int(payload["measured_valid_targets_per_step"]),
        tokens_per_sec=float(payload["tokens_per_sec"]),
        step_seconds=float(payload["step_seconds"]),
        peak_allocated_gib=float(payload["peak_allocated_gib"]),
        peak_reserved_gib=float(payload["peak_reserved_gib"]),
        cpu_rss_gib=float(payload["cpu_rss_gib"]),
        loader_wait_seconds=float(payload["loader_wait_seconds"]),
        optimizer_state_bytes=int(payload["optimizer_state_bytes"]),
        checkpoint_bytes=int(payload["checkpoint_bytes"]),
        checkpoint_save_seconds=float(payload["checkpoint_save_seconds"]),
        build_seconds=float(payload["build_seconds"]),
        backend=dict(payload.get("backend") or {}),
    )


def run_calibration(request: ProfileRequest, output_dir: Path) -> ProfileResult:
    """Search microbatch sizes ascending; freeze the largest feasible setting.

    Ascending order bounds wasted work: the first infeasible size stops the
    search instead of crashing through larger ones. The global batch is
    preserved exactly through integer accumulation steps.
    """
    preflight = preflight_check(request, output_dir)
    work_dir = output_dir / "workers"
    sizes: list[MicrobatchResult] = []
    for microbatch in sorted(request.microbatch_candidates):
        result = _run_worker(request, microbatch, work_dir)
        sizes.append(result)
        if not result.feasible:
            break

    feasible = [s for s in sizes if s.feasible]
    total_vram = float(preflight.get("vram_total_gib", 0.0))
    selected: MicrobatchResult | None = None
    for candidate in reversed(feasible):
        if request.device != "cuda" or total_vram <= 0:
            selected = candidate
            break
        if candidate.peak_reserved_gib <= request.max_vram_fraction * total_vram:
            selected = candidate
            break
    if feasible and selected is None:
        # Every feasible size exceeds the headroom fraction: take the smallest
        # feasible size and say so, rather than failing or shrinking the model.
        selected = feasible[0]

    selected_size: int | None = selected.microbatch_sequences if selected is not None else None
    accumulation: int | None = None
    backend: dict[str, Any] | None = selected.backend if selected is not None else None
    if selected is not None and selected.measured_valid_targets_per_step > 0:
        accumulation = math.ceil(
            request.global_batch_valid_targets / selected.measured_valid_targets_per_step
        )

    freeze = {
        "profile_version": PROFILE_VERSION,
        "model_id": request.model_id,
        "model_config_hash": _config_hash(request.model_config),
        "device": request.device,
        "precision": request.precision,
        "attention_backend": request.attention_backend,
        "microbatch_sequences": selected.microbatch_sequences if selected else None,
        "accumulation_steps": accumulation,
        "global_batch_valid_targets": request.global_batch_valid_targets,
        "context_length": request.context_length,
        "activation_checkpointing": request.activation_checkpointing,
        "compile": request.compile,
        "torch_version": preflight.get("torch_version"),
    }
    return ProfileResult(
        profile_version=PROFILE_VERSION,
        model_id=request.model_id,
        request=request.to_dict(),
        preflight=preflight,
        sizes=sizes,
        selected_microbatch_sequences=selected_size,
        accumulation_steps=accumulation,
        backend=backend,
        resource_plan=None,
        freeze=freeze,
    )


def transformer_flops_per_token(unique_params: int, architecture: str) -> tuple[int, int]:
    """Estimate forward/train FLOPs per token for the baseline Transformer only.

    Any other architecture raises: its compute cannot default to the ordinary
    Transformer formula unnoticed. Returned values are labeled estimates.
    """
    if architecture != "transformer_baseline":
        raise UnfamiliarArchitectureError(
            f"Architecture '{architecture}' has no verified FLOP formula; refusing "
            "to quote Transformer estimates for it."
        )
    return 2 * unique_params, 6 * unique_params


def plan_resources(
    profile: ProfileResult,
    budget_valid_targets: int,
    unique_params: int,
    architecture: str,
) -> ResourcePlan:
    """Build a resource plan from measured throughput with documented uncertainty."""
    feasible = [s for s in profile.sizes if s.feasible]
    if not feasible:
        raise PreflightError("No feasible microbatch size; cannot plan resources.")
    throughputs = sorted(s.tokens_per_sec for s in feasible if s.tokens_per_sec > 0)
    if not throughputs:
        raise PreflightError("Feasible sizes reported no positive throughput.")
    lo, hi = throughputs[0], throughputs[-1]
    eta_hi = budget_valid_targets / lo
    eta_lo = budget_valid_targets / hi

    selected = next(
        (s for s in feasible if s.microbatch_sequences == profile.selected_microbatch_sequences),
        feasible[-1],
    )
    fwd_flops, train_flops = transformer_flops_per_token(unique_params, architecture)
    return ResourcePlan(
        throughput_tokens_per_sec_range=[lo, hi],
        eta_seconds_range=[eta_lo, eta_hi],
        budget_valid_targets=budget_valid_targets,
        peak_reserved_gib=selected.peak_reserved_gib,
        optimizer_state_gib=selected.optimizer_state_bytes / (1024**3),
        checkpoint_gib=selected.checkpoint_bytes / (1024**3),
        checkpoint_save_seconds=selected.checkpoint_save_seconds,
        unique_parameters=unique_params,
        forward_flops_per_token_estimate=fwd_flops,
        train_flops_per_token_estimate=train_flops,
        uncertainty_notes=[
            "Throughput range spans feasible microbatch sizes on the profiled device;",
            "it is a measurement interval, not a promise.",
            "ETA covers training steps only; evaluation and checkpoint saves are",
            "reported separately (checkpoint_save_seconds per save).",
            "FLOP figures are formula estimates for transformer_baseline, not profiler counts.",
            "Loader wait is measured on synthetic in-memory tokens; real loader",
            "wait must be re-measured against the production loader.",
        ],
    )


def write_profile_artifacts(result: ProfileResult, output_dir: Path) -> dict[str, Path]:
    """Write profile.json and the frozen matched-run settings."""
    output_dir.mkdir(parents=True, exist_ok=True)
    profile_path = output_dir / "profile.json"
    profile_path.write_text(
        json.dumps(result.to_dict(), indent=2, sort_keys=True), encoding="utf-8"
    )
    freeze_path = output_dir / "profile_freeze.json"
    freeze_path.write_text(json.dumps(result.freeze, indent=2, sort_keys=True), encoding="utf-8")
    return {"profile": profile_path, "freeze": freeze_path}
