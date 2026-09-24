"""P32 correctness comparison and fixed 1/8/16-worker regression measurement."""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts/p32-recovery"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("prepare", "exact", "compare", "whole"))
    parser.add_argument("--label", choices=("baseline", "current"), default="current")
    args = parser.parse_args()
    if Path.cwd().resolve() != ROOT or ROOT != Path("G:/Project/xlm-p32-recovery"):
        raise ValueError("P32 worktree only")
    if args.label == "baseline":
        sys.path.insert(0, str(OUT / "baseline/src"))
    import review_opus_measure as method

    method.OUT = OUT
    method.METHODS = OUT / "methods"
    if args.mode == "prepare":
        method.prepare()
    elif args.mode == "exact":
        method.exact(args.label)
    elif args.mode == "compare":
        baseline = json.loads((OUT / "exact-baseline.json").read_text())
        current = json.loads((OUT / "exact-current.json").read_text())
        assert baseline == current, "successful acquisition contract changed"
        (OUT / "exact-comparison.json").write_text(
            json.dumps({"equal": True, "cases": len(current), "baseline": "26c1238"}, indent=2)
        )
        print("All eight exact acquisitions, receipts and final accounting match")
    else:
        sys.path.insert(0, str(method.METHODS))
        from benchmark_systems import MIB, fetch_case, save

        rng = random.Random(31)
        payload = b"".join(rng.randbytes(MIB) for _ in range(64))
        digest = hashlib.sha256(payload).hexdigest()
        destination = OUT / f"whole-{args.label}"
        if destination.exists():
            raise ValueError("Fresh benchmark output required")
        rows = []
        for workers in (1, 8, 16):
            row = fetch_case(
                destination / f"w{workers}", payload, workers, 16, transfer_chunk_bytes=65536
            )
            assert row["status"] == "COMPLETED" and row["bytes"] == 16 * len(payload)
            assert len(row["output_hashes"]) == 16
            assert set(row["output_hashes"].values()) == {digest}
            rows.append(row)
            save(destination / "report.json", rows)
            print(args.label, workers, row["MBps"], flush=True)


if __name__ == "__main__":
    main()
