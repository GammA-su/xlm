"""Bounded real-shape CUDA kernel/transfer probes; no model training or downloads."""

from __future__ import annotations

import time
from collections.abc import Callable
from functools import partial
from typing import Any

import torch
import torch.nn.functional as F
from benchmark_p33 import EVIDENCE, cuda_event, write_json
from torch.nn.attention import SDPBackend, sdpa_kernel

from xlm.models.rmsnorm import RMSNorm


def measure(function: Callable[[], Any], iterations: int = 50) -> dict[str, float]:
    for _ in range(10):
        function()
    torch.cuda.synchronize()
    start, end = cuda_event(), cuda_event()
    start.record()
    begin = time.perf_counter()
    for _ in range(iterations):
        function()
    end.record()
    torch.cuda.synchronize()
    return {
        "cuda_ms": start.elapsed_time(end) / iterations,
        "wall_ms": (time.perf_counter() - begin) * 1000 / iterations,
    }


def transfers() -> list[dict[str, Any]]:
    rows = []
    for size in (128 * 1024, 8 * 1024**2):
        base = torch.arange(size // 8, dtype=torch.int64)
        target = torch.empty_like(base, device="cuda")
        for pinned, nonblocking in ((False, False), (True, False), (True, True)):
            source = base.pin_memory() if pinned else base
            timing = measure(partial(target.copy_, source, non_blocking=nonblocking), 100)
            assert torch.equal(target.cpu(), base)
            rows.append(
                {
                    "bytes": size,
                    "pinned": pinned,
                    "nonblocking": nonblocking,
                    **timing,
                    "gib_per_second": size / 1024**3 / (timing["wall_ms"] / 1000),
                }
            )
    return rows


def attention() -> list[dict[str, Any]]:
    torch.manual_seed(33)
    q, k, v = [
        torch.randn(8, 8, 512, 64, device="cuda", dtype=torch.bfloat16, requires_grad=True)
        for _ in range(3)
    ]
    mask = torch.ones(512, 512, device="cuda", dtype=torch.bool).tril()
    rows = []
    reference = None
    for backend in (SDPBackend.MATH, SDPBackend.EFFICIENT_ATTENTION, SDPBackend.FLASH_ATTENTION):
        for explicit in (True, False):

            def operation(explicit: bool = explicit) -> torch.Tensor:
                for value in (q, k, v):
                    value.grad = None
                output = F.scaled_dot_product_attention(
                    q, k, v, attn_mask=mask if explicit else None, is_causal=not explicit
                )
                (output.float().sum() * 0.0001).backward()  # type: ignore[no-untyped-call]
                return output

            row: dict[str, Any] = {"backend": str(backend), "explicit_mask": explicit}
            try:
                with sdpa_kernel(backend):
                    result = operation().detach()
                    gradients = []
                    for value in (q, k, v):
                        assert value.grad is not None
                        gradients.append(value.grad.detach().clone())
                    if reference is None:
                        reference = (result.clone(), gradients)
                    row["max_output_error"] = (result - reference[0]).abs().max().item()
                    row["max_gradient_error"] = max(
                        (a - b).abs().max().item()
                        for a, b in zip(gradients, reference[1], strict=True)
                    )
                    torch.testing.assert_close(result, reference[0], atol=0.032, rtol=0.003)
                    for actual, expected in zip(gradients, reference[1], strict=True):
                        torch.testing.assert_close(actual, expected, atol=2e-5, rtol=0.003)
                    row.update(measure(operation))
                    row["status"] = "VERIFIED"
            except RuntimeError as exc:
                row.update(status="NOT RUN", reason=str(exc))
            rows.append(row)
    return rows


def normalization() -> list[dict[str, Any]]:
    torch.manual_seed(33)
    x = torch.randn(8, 512, 512, device="cuda", requires_grad=True)
    norm = RMSNorm(512, device="cuda")
    rows = []
    reference = None
    for native in (False, True):

        def operation(native: bool = native) -> torch.Tensor:
            x.grad = None
            norm.weight.grad = None
            out = F.rms_norm(x, [512], norm.weight, norm.eps) if native else norm(x)
            (out.sum() * 0.0001).backward()
            return out

        out = operation().detach()
        assert x.grad is not None and norm.weight.grad is not None
        state = (out.clone(), x.grad.clone(), norm.weight.grad.clone())
        if reference is None:
            reference = state
        errors = [float((a - b).abs().max()) for a, b in zip(state, reference, strict=True)]
        for actual, expected in zip(state, reference, strict=True):
            torch.testing.assert_close(actual, expected, atol=2e-7, rtol=2e-5)
        rows.append({"native": native, "max_errors": errors, **measure(operation)})
    return rows


if __name__ == "__main__":
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA required")
    torch.set_num_threads(1)
    result = {"transfers": transfers(), "attention": attention(), "normalization": normalization()}
    write_json(EVIDENCE / "kernel_probes.json", result)
    print(result)
