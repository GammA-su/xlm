"""global_update_payload_digest_v1 parity: identical digests before and after hardening.

Usage: python v1_parity.py <scratch_dir> <output.json>

Imports ``xlm`` and the authored test helpers from ``PYTHONPATH``, so the same
script runs against a clean export of the starting commit and the hardened
tree. AUTHORED/SYNTHETIC token shards only; no training.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from p35_m5_support import write_sources
from test_p35_readiness_payload import updates
from xlm.data.sampling.prefetch import _encode
from xlm.data.sampling.update_payload import (
    UpdatePayloadChain,
    canonical_from_microbatches,
    canonical_from_prepared,
)


def main(scratch: Path, output: Path) -> None:
    import xlm

    readers = write_sources(scratch / "shards")
    result: dict[str, object] = {"xlm": str(Path(xlm.__file__).resolve().parent)}
    for group in (8, 16, 32):
        batches = updates(readers, group, count=3)
        direct = [canonical_from_microbatches(b).digest() for b in batches]
        producer = [
            canonical_from_prepared(_encode(b, 0, 0, None, "s", None, 0.0)).digest()
            for b in batches
        ]
        chain = UpdatePayloadChain()
        before = 0
        for step, (b, payload) in enumerate(zip(batches, direct, strict=True), start=1):
            valid = canonical_from_microbatches(b).valid_targets
            chain.stage(step=step, committed_before=before, valid_targets=valid, payload=payload)
            chain.commit()
            before += valid
        result[f"b{group}"] = {"direct": direct, "producer": producer, "head": chain.head}
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
