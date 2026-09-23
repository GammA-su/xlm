"""Bounded packing experiment on existing authored token shards."""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
import time
from pathlib import Path
from typing import Any

from xlm.data.sampling.packing import CausalStreamPacker
from xlm.data.tokens import TokenShardReader


def run(path: Path) -> dict[str, Any]:
    from benchmark_token_oracles import reference

    baseline = reference("src/xlm/data/sampling/packing.py", "p29b_reference_packing")
    reader = TokenShardReader(path)
    packer = CausalStreamPacker(512)
    old_packer = baseline.CausalStreamPacker(512)
    with reader.mmap_tokens() as view:
        windows = [
            list(struct.unpack_from("<513H", view, start * 2))
            for start in range(0, min(reader.manifest.num_tokens - 513, 512 * 10000), 512)
        ]
    rows = []
    for iteration in range(2):
        for mode in ("reference", "candidate") if iteration == 0 else ("candidate", "reference"):
            elapsed = 0.0
            digest = hashlib.sha256()
            for ids in windows:
                start = time.perf_counter()
                packed = (
                    old_packer.pack(ids, ["fixture"] * 513)
                    if mode == "reference"
                    else packer.pack(ids, ["fixture"] * 513)
                )
                elapsed += time.perf_counter() - start
                digest.update(json.dumps(vars(packed)).encode())
            rows.append(
                {
                    "mode": mode,
                    "iteration": iteration,
                    "seconds": elapsed,
                    "windows": len(windows),
                    "digest": digest.hexdigest(),
                }
            )
    for ids in ([1] * 513, [0] * 513, [1, 2], [5] * 18, list(range(600))):
        assert vars(old_packer.pack(ids, ["fixture"] * len(ids))) == vars(
            packer.pack(ids, ["fixture"] * len(ids))
        )
    assert len({r["digest"] for r in rows}) == 1
    return {"rows": rows, "fixture_only": True, "digest_outside_timing": True}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shard", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run(args.shard)
    args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
