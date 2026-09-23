"""Operator-only bounded synthetic H2D measurement; never constructs a model."""

from __future__ import annotations

import argparse
import json
import time

import torch


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mib", type=int, default=8)
    parser.add_argument("--iterations", type=int, default=100)
    args = parser.parse_args()
    if not 1 <= args.mib <= 64 or not 1 <= args.iterations <= 1000:
        parser.error("mib must be 1..64 and iterations 1..1000")
    if not torch.cuda.is_available():
        print(
            json.dumps(
                {"status": "NOT RUN", "reason": "CUDA unavailable", "torch": torch.__version__}
            )
        )
        raise SystemExit(77)
    deadline = time.monotonic() + 60
    base = torch.arange(args.mib * 1024**2 // 8, dtype=torch.int64)
    rows = []
    for pinned, nonblocking in ((False, False), (True, False), (True, True)):
        source = base.pin_memory() if pinned else base
        target = torch.empty_like(base, device="cuda")
        target.copy_(source, non_blocking=nonblocking)
        torch.cuda.synchronize()
        start = time.perf_counter()
        for _ in range(args.iterations):
            if time.monotonic() > deadline:
                raise TimeoutError("60 second transfer benchmark cap exceeded")
            target.copy_(source, non_blocking=nonblocking)
        torch.cuda.synchronize()
        elapsed = time.perf_counter() - start
        assert torch.equal(target.cpu(), base)
        rows.append(
            {
                "pinned": pinned,
                "non_blocking": nonblocking,
                "seconds": elapsed,
                "gib_per_second": args.iterations * args.mib / 1024 / elapsed,
            }
        )
    print(json.dumps({"rows": rows, "note": "transfer-only; no compute-overlap claim"}, indent=2))


if __name__ == "__main__":
    main()
