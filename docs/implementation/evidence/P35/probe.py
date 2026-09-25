"""P35 offline scientific audit; synthetic attention only, no optimizer or training."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import statistics
import time
from pathlib import Path
from typing import Any

import psutil
import torch
from torch.nn.attention import SDPBackend, sdpa_kernel

from xlm.config.schemas import TransformerBaselineConfig
from xlm.models.transformer import TransformerBaseline

ROOT = Path(__file__).resolve().parents[4]
OUT = Path(__file__).with_name("probe.json")


def digest(tensors: list[torch.Tensor]) -> str:
    value = hashlib.sha256()
    for tensor in tensors:
        value.update(tensor.detach().float().cpu().numpy().tobytes())
    return value.hexdigest()


def main() -> None:
    started = time.monotonic()
    if OUT.exists():
        raise FileExistsError(OUT)
    torch.set_num_threads(1)
    torch.cuda.set_per_process_memory_fraction(0.10)
    result: dict[str, Any] = {
        "scope": "authored synthetic single attention operator, not model training",
        "python": platform.python_version(),
        "platform": platform.platform(),
        "torch": torch.__version__,
        "cuda": torch.version.cuda,
        "gpu": torch.cuda.get_device_name(),
        "gpu_memory_cap_fraction": 0.10,
        "deadline_seconds": 120,
        "counts": {},
        "attention": [],
    }
    for name in ("50m", "150m", "300m"):
        raw = json.loads((ROOT / "recipes/models" / f"{name}.yaml").read_text())
        config = TransformerBaselineConfig.model_validate(
            {
                key: value
                for key, value in raw.items()
                if key not in {"schema_version", "kind", "id"}
            }
        )
        with torch.device("meta"):
            model = TransformerBaseline(config)
        count = sum(p.numel() for p in model.parameters())
        assert count == raw["expected_unique_parameters"]
        result["counts"][name] = count
        del model
    shapes = {"50m_b8": (8, 8), "150m_b16": (16, 12), "300m_b8": (8, 16)}
    generator = torch.Generator(device="cuda").manual_seed(3501)
    for name, (batch, heads) in shapes.items():
        # Match projected/transposed attention layout; BF16 values are synthetic.
        q, k, v, grad = [
            torch.randn(
                (batch, 512, heads, 64), device="cuda", dtype=torch.bfloat16, generator=generator
            ).transpose(1, 2)
            for _ in range(4)
        ]
        for value in (q, k, v):
            value.requires_grad_(True)
        causal = torch.ones((512, 512), device="cuda", dtype=torch.bool).tril()
        padding = torch.ones((batch, 1, 1, 512), device="cuda", dtype=torch.bool)
        padding[0, ..., 400:] = False
        for mask_name, kwargs in (
            ("causal", {"is_causal": True}),
            ("partial_mask", {"attn_mask": causal & padding}),
        ):
            outputs: dict[bool, torch.Tensor] = {}
            for deterministic in (False, True):
                if time.monotonic() - started > 120:
                    raise TimeoutError("P35 120-second diagnostic deadline")
                torch.use_deterministic_algorithms(deterministic)

                # This closure is called synchronously only, before loop variables
                # change or buffers are deleted. Preserve the executed probe body.
                def run() -> torch.Tensor:
                    for tensor in (q, k, v):  # noqa: B023, F821
                        tensor.grad = None
                    with sdpa_kernel(SDPBackend.EFFICIENT_ATTENTION):
                        out = torch.nn.functional.scaled_dot_product_attention(
                            q,  # noqa: B023, F821
                            k,  # noqa: B023, F821
                            v,  # noqa: B023, F821
                            **kwargs,  # noqa: B023
                        )
                    out.backward(grad)  # noqa: B023, F821
                    return out

                for _ in range(3):
                    run()
                torch.cuda.synchronize()
                torch.cuda.reset_peak_memory_stats()
                timings, forward_hashes, gradient_hashes = [], set(), set()
                for _ in range(12):
                    start, end = (
                        torch.cuda.Event(enable_timing=True),
                        torch.cuda.Event(enable_timing=True),
                    )
                    start.record()
                    out = run()
                    end.record()
                    end.synchronize()
                    timings.append(start.elapsed_time(end))
                    forward_hashes.add(digest([out]))
                    gradient_hashes.add(digest([t.grad for t in (q, k, v)]))
                outputs[deterministic] = out.detach().cpu()
                with torch.profiler.profile(
                    activities=[torch.profiler.ProfilerActivity.CPU]
                ) as prof:
                    run()
                    torch.cuda.synchronize()
                operators = sorted({e.key for e in prof.key_averages() if "attention" in e.key})
                result["attention"].append(
                    {
                        "shape": name,
                        "q_shape": list(q.shape),
                        "q_stride": list(q.stride()),
                        "mask": mask_name,
                        "deterministic": deterministic,
                        "repeats": 12,
                        "operators": operators,
                        "milliseconds": timings,
                        "median_ms": statistics.median(timings),
                        "peak_allocated_bytes": torch.cuda.max_memory_allocated(),
                        "peak_reserved_bytes": torch.cuda.max_memory_reserved(),
                        "distinct_forward": len(forward_hashes),
                        "distinct_backward": len(gradient_hashes),
                    }
                )
            result["attention"][-1]["forward_equal_to_default"] = torch.equal(
                outputs[False], outputs[True]
            )
            result["attention"][-1]["forward_max_abs_difference"] = float(
                (outputs[False].float() - outputs[True].float()).abs().max()
            )
            print(
                name,
                mask_name,
                [
                    (r["deterministic"], r["median_ms"], r["distinct_backward"])
                    for r in result["attention"][-2:]
                ],
                flush=True,
            )
        del q, k, v, grad, out, outputs
        torch.cuda.empty_cache()
    torch.use_deterministic_algorithms(False)
    result["wall_seconds"] = time.monotonic() - started
    result["process_rss_bytes_at_end"] = psutil.Process().memory_info().rss
    temporary = OUT.with_suffix(".tmp")
    temporary.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, OUT)


if __name__ == "__main__":
    main()
