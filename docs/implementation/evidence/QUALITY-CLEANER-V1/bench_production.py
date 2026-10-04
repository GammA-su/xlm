"""Bounded AUTHORED-data throughput comparison: Phase-B dry run vs Phase-C production.

Synthetic corpus only (``xlm.data.quality.bench.build_corpus``); never a real corpus.
Chain: corpus -> Phase-A audit -> frozen v1 -> frozen v2 -> v2 dry run (16 workers) ->
production cleaning (16 workers) -> verification (16 workers, --compare-sources).
Prints one JSON object with the measured wall seconds and file MB/s of each step.

Usage (from the repository root, scratch directory NEW or absent):
    uv run --offline --locked --extra cpu --extra eval python \
        docs/implementation/evidence/QUALITY-CLEANER-V1/bench_production.py <scratch> [MiB]
"""

from __future__ import annotations

import json
import shutil
import sys
import time
from collections.abc import Callable
from pathlib import Path

from xlm.data.quality.bench import build_corpus
from xlm.data.quality.cleaning_policy import freeze_policy
from xlm.data.quality.cleaning_runner import load_receipt, run_dry_run
from xlm.data.quality.production import run_production
from xlm.data.quality.production_verify import verify_production
from xlm.data.quality.runner import Limits, run_audit

REPO = Path(__file__).resolve().parents[4]
MIB = 1024**2


def main() -> None:
    scratch = Path(sys.argv[1])
    mib = int(sys.argv[2]) if len(sys.argv) > 2 else 512
    if scratch.exists():
        raise SystemExit("scratch must be NEW or absent")
    scratch.mkdir(parents=True)
    limits = Limits(16, 12 * 1024**3, 0, 4 * 1024**3, 64 * MIB, 3600.0)
    manifest = build_corpus(scratch / "corpus", mib * MIB)
    file_bytes = sum(f["file_bytes"] for f in json.loads(manifest.read_bytes())["files"])
    timings: dict[str, float] = {}

    def timed(name: str, call: Callable[[], object]) -> None:
        started = time.monotonic()
        call()
        timings[name] = round(time.monotonic() - started, 3)

    timed(
        "audit",
        lambda: run_audit(manifest, scratch / "audit", limits=limits, progress_interval=None),
    )
    v1, v2 = scratch / "v1.yaml", scratch / "v2.yaml"
    freeze_policy(REPO / "recipes/quality/cleaning_policy_v1.yaml", scratch / "audit", v1)
    freeze_policy(
        REPO / "recipes/quality/cleaning_policy_v2.yaml", scratch / "audit", v2, predecessor=v1
    )
    dry = scratch / "dry"
    timed(
        "dry_run",
        lambda: run_dry_run(manifest, dry, v2, limits=limits, progress_interval=None),
    )
    receipt = load_receipt(dry)
    result: dict[str, object] = {}

    def produce() -> None:
        result.update(
            run_production(
                manifest,
                v2,
                dry,
                scratch / "clean",
                scratch / "state",
                approved_result_digest=receipt["result_digest"],
                limits=limits,
                progress_interval=None,
            )
        )

    timed("production", produce)
    timed(
        "verify_compare_sources",
        lambda: verify_production(
            manifest,
            v2,
            dry,
            scratch / "clean",
            scratch / "state",
            approved_result_digest=receipt["result_digest"],
            workers=16,
            compare_sources=True,
            progress_interval=None,
        ),
    )
    print(
        json.dumps(
            {
                "synthetic_corpus_file_bytes": file_bytes,
                "policy_status": receipt["policy_status"],
                "wall_seconds": timings,
                "file_mb_per_s": {k: round(file_bytes / 1e6 / v, 2) for k, v in timings.items()},
                "production_clean_phase": result["clean"],
                "production_phase_seconds": result["phase_seconds"],
                "accounting": result["accounting"],
            },
            indent=1,
            sort_keys=True,
        )
    )
    shutil.rmtree(scratch / "corpus")


if __name__ == "__main__":
    main()
