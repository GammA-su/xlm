"""Bitwise run-to-run determinism of memory-efficient SDPA at P34 model shapes.

Repeats forward+backward on identical BF16 inputs and counts distinct gradient
digests. Also records whether torch's deterministic-algorithms mode accepts the
op. Diagnostic only; no product change.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import torch
from torch.nn.attention import SDPBackend, sdpa_kernel

sys.path.insert(0, str(Path(__file__).resolve().parent))
import benchmark_p34 as bench  # noqa: E402

SHAPES = {"50m_b8": (8, 8, 512, 64), "150m_b16": (16, 12, 512, 64), "300m_b8": (8, 16, 512, 64)}
REPEATS = 30


def digest(*tensors: torch.Tensor) -> str:
    value = hashlib.sha256()
    for tensor in tensors:
        value.update(tensor.detach().float().cpu().numpy().tobytes())
    return value.hexdigest()


def attend(
    q: torch.Tensor, k: torch.Tensor, v: torch.Tensor, grad: torch.Tensor, kwargs: dict[str, Any]
) -> tuple[torch.Tensor, list[torch.Tensor]]:
    query, key, value = (t.clone().requires_grad_(True) for t in (q, k, v))
    with sdpa_kernel(SDPBackend.EFFICIENT_ATTENTION):
        out = torch.nn.functional.scaled_dot_product_attention(query, key, value, **kwargs)
    torch.autograd.backward([out], [grad])
    grads = [t.grad for t in (query, key, value)]
    assert all(g is not None for g in grads)
    return out, [g for g in grads if g is not None]


def main() -> None:
    if bench.foreign_xlm_processes(bench.own_pids()):
        raise RuntimeError("GPU not exclusive")
    output = bench.EVIDENCE / "sdpa_determinism.json"
    if output.exists():
        raise FileExistsError(output)
    result: dict[str, object] = {"torch": torch.__version__, "gpu": torch.cuda.get_device_name()}
    generator = torch.Generator(device="cuda").manual_seed(7)
    for name, shape in SHAPES.items():
        q, k, v = (
            torch.randn(shape, device="cuda", dtype=torch.bfloat16, generator=generator)
            for _ in range(3)
        )
        grad = torch.randn(shape, device="cuda", dtype=torch.bfloat16, generator=generator)
        causal = torch.ones(512, 512, device="cuda", dtype=torch.bool).tril()
        padding = torch.ones(shape[0], 1, 1, 512, device="cuda", dtype=torch.bool)
        padding[0, ..., 400:] = False  # one partial window, as in real updates
        mask = causal & padding
        for label, kwargs in (
            ("causal_flag", {"is_causal": True}),
            ("explicit_mask", {"attn_mask": mask}),
        ):
            outputs, gradients = set(), set()
            for _ in range(REPEATS):
                out, grads = attend(q, k, v, grad, kwargs)
                outputs.add(digest(out))
                gradients.add(digest(*grads))
            result[f"{name}_{label}"] = {
                "repeats": REPEATS,
                "distinct_outputs": len(outputs),
                "distinct_gradients": len(gradients),
            }
            print(name, label, len(outputs), len(gradients), flush=True)
    # torch warns that this kernel "defaults to a non-deterministic algorithm" and
    # switches to a deterministic one under use_deterministic_algorithms(True).
    torch.use_deterministic_algorithms(True)
    try:
        attend(q, k, v, grad, {"is_causal": True})
        result["deterministic_mode"] = "runs (deterministic variant selected)"
    except RuntimeError as exc:
        result["deterministic_mode"] = f"refused: {str(exc)[:300]}"
    finally:
        torch.use_deterministic_algorithms(False)
    bench.write_json(output, result)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
