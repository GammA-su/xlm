"""Tested attention-backend policy, optional compile and execution reporting.

Contract C08/C10: the configured ``profile_required`` backend must be resolved to
``eager`` or ``sdpa`` before any numerical execution -- never guessed silently.
Arbitrary document masks may force SDPA onto its slower math path; that fallback
is reported rather than hidden behind a blanket "flash attention" claim. Which
SDPA kernels actually run is probed functionally, not assumed from the device name.
``torch.compile`` is an opt-in verified performance mode, never a correctness
dependency: parity against eager is asserted by tests, not promised by flags.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

try:
    import torch
    import torch.nn as nn
except ImportError:
    torch = None  # type: ignore[assignment]
    nn = None  # type: ignore[assignment]


class BackendPolicyError(ValueError):
    """Raised when an attention backend cannot be resolved or is unsupported."""


class PrecisionUnsupportedError(ValueError):
    """Raised when a precision mode is requested the device cannot execute.

    Precision is never downgraded silently: asking for BF16 on a CPU-only
    host fails loudly instead of running fp32 under a bf16 label.
    """


VALID_PRECISIONS = ("fp32", "bf16_fp32_master", "fp16", "fp16_scaler")


def validate_precision(precision: str, device: str) -> str:
    """Validate a precision mode against the execution device, loudly.

    Returns the validated precision unchanged. Unknown modes and device
    combinations that cannot execute the mode raise instead of falling back.
    """
    if precision not in VALID_PRECISIONS:
        raise PrecisionUnsupportedError(
            f"Unknown precision '{precision}'. Supported: {', '.join(VALID_PRECISIONS)}."
        )
    if precision != "fp32" and device != "cuda":
        raise PrecisionUnsupportedError(
            f"Precision '{precision}' requires a CUDA device, but device is '{device}'. "
            "Request 'fp32' explicitly for CPU execution."
        )
    if precision == "bf16_fp32_master" and device == "cuda" and torch is not None:
        try:
            capable = bool(torch.cuda.is_bf16_supported())
        except Exception:
            capable = False
        if torch.cuda.is_available() and not capable:
            raise PrecisionUnsupportedError(
                "Precision 'bf16_fp32_master' requires a BF16-capable CUDA device "
                "(compute capability >= 8.0); this device reports no BF16 support."
            )
    return precision


@dataclass
class BackendReport:
    """What the backend policy resolved and what the hardware actually supports."""

    configured: str
    selected: str
    device: str
    arbitrary_mask: bool
    sdpa_kernels: dict[str, bool] = field(default_factory=dict)
    fallback_notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def probe_sdpa_kernels(device: str) -> dict[str, bool]:
    """Functionally probe which SDPA kernel paths execute on this device.

    Each kernel is exercised through a tiny real attention op under its
    ``sdpa_kernel`` context. A kernel that raises is reported unavailable --
    this is a measurement, not a device-name lookup.
    """
    kernels: dict[str, bool] = {"flash": False, "mem_efficient": False, "math": False}
    if torch is None or device != "cuda" or not torch.cuda.is_available():
        if torch is not None:
            kernels["math"] = True  # CPU SDPA dispatches to the math path.
        return kernels

    backends = []
    try:
        from torch.nn.attention import SDPBackend

        backends = [
            ("flash", SDPBackend.FLASH_ATTENTION),
            ("mem_efficient", SDPBackend.EFFICIENT_ATTENTION),
            ("math", SDPBackend.MATH),
        ]
    except Exception:
        return kernels

    for name, backend in backends:
        try:
            with torch.nn.attention.sdpa_kernel(backend):
                q = torch.randn(1, 2, 4, 8, device="cuda", dtype=torch.float32)
                k = torch.randn(1, 2, 4, 8, device="cuda", dtype=torch.float32)
                v = torch.randn(1, 2, 4, 8, device="cuda", dtype=torch.float32)
                out = torch.nn.functional.scaled_dot_product_attention(q, k, v, is_causal=True)
                torch.cuda.synchronize()
                kernels[name] = bool(torch.isfinite(out).all().item())
        except Exception:
            kernels[name] = False
    return kernels


def resolve_attention_backend(
    configured: str,
    device: str,
    arbitrary_mask: bool = False,
) -> BackendReport:
    """Resolve a configured backend to an executable one, with a recorded report.

    ``profile_required`` always raises: it must be resolved by profiling before a
    run, never defaulted at execution time. An SDPA selection on an arbitrary
    document mask records that torch may fall back to the slower math kernel.
    """
    if configured not in ("profile_required", "eager", "sdpa"):
        raise BackendPolicyError(
            f"Invalid attention_backend '{configured}'. Must be one of: "
            "'profile_required', 'eager', 'sdpa'."
        )
    if configured == "profile_required":
        raise BackendPolicyError(
            "attention_backend 'profile_required' must be resolved to 'eager' or 'sdpa' "
            "by profiling before numerical execution."
        )

    kernels = probe_sdpa_kernels(device)
    notes: list[str] = []
    if configured == "sdpa":
        if device != "cuda":
            notes.append("SDPA on CPU dispatches to the math kernel; no flash path exists.")
        else:
            active = [k for k, ok in kernels.items() if ok] or ["math"]
            notes.append(f"SDPA kernel availability (probed): {', '.join(active)}.")
            if not kernels.get("flash", False):
                notes.append("Flash attention unavailable; SDPA uses a slower kernel.")
        if arbitrary_mask:
            notes.append(
                "Arbitrary document masks may force SDPA onto its slower math path; "
                "throughput may differ from the unmasked fast path."
            )
    return BackendReport(
        configured=configured,
        selected=configured,
        device=device,
        arbitrary_mask=arbitrary_mask,
        sdpa_kernels=kernels,
        fallback_notes=notes,
    )


_compile_probe_cache: dict[str, tuple[bool, str]] = {}


def compile_probe(device: str) -> tuple[bool, str]:
    """Honestly test whether torch.compile executes on this device.

    Compiles and runs a trivial function once per device and caches the verdict.
    A missing Triton/codegen backend is reported, never worked around.
    """
    if device in _compile_probe_cache:
        return _compile_probe_cache[device]
    if torch is None:
        verdict = (False, "torch is not installed")
    else:
        try:
            fn = torch.compile(lambda x: x + 1, mode="default")
            probe_device = device if (device == "cuda" and torch.cuda.is_available()) else "cpu"
            out = fn(torch.ones(4, device=probe_device))
            if probe_device == "cuda":
                torch.cuda.synchronize()
            verdict = (True, "ok") if bool((out == 2).all().item()) else (False, "wrong result")
        except Exception as exc:
            verdict = (False, f"{type(exc).__name__}: {exc}")
    _compile_probe_cache[device] = verdict
    return verdict


def maybe_compile(model: Any, enabled: bool, mode: str = "default") -> tuple[Any, dict[str, Any]]:
    """Optionally compile a model, reporting exactly what was done.

    Returns the (possibly compiled) model and a report dict. When disabled, the
    model is returned untouched and the report says so -- compile is never
    implied.
    """
    if not enabled:
        return model, {"compiled": False, "mode": None}
    if torch is None:
        raise BackendPolicyError("torch.compile requested but torch is not installed.")
    try:
        compiled = torch.compile(model, mode=mode)
    except Exception as exc:
        raise BackendPolicyError(f"torch.compile failed: {exc}") from exc
    return compiled, {"compiled": True, "mode": mode}
