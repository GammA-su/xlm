"""One authorized, offline b1 move and metadata-only verification; no benchmark run."""

from __future__ import annotations

import json
import os
import platform
import sys
import time
from pathlib import Path
from typing import Any

from xlm.data.acquisition import source_archive as archive
from xlm.data.acquisition import source_benchmark as bench
from xlm.data.acquisition import source_parquet as sp
from xlm.data.acquisition import source_run as run
from xlm.data.acquisition.plan import load_acquisition_plan, validate_plan_authorization
from xlm.data.acquisition.source_dashboard import free_bytes


def no_network(event: str, args: tuple[Any, ...]) -> None:
    if event in {"socket.connect", "socket.getaddrinfo", "socket.bind"}:
        raise RuntimeError("network forbidden during archive verification")


def main() -> None:
    sys.addaudithook(no_network)
    started = time.monotonic()
    roots = run.Roots(Path("G:/XLM"), Path("C:/XLM-scratch"), "finepdfs")
    destination = Path("C:/XLM-scratch-history/finepdfs/bench-b1")
    directory = bench.benchmark_dir(roots, "b2")
    record = run.read_json(directory / "benchmark.json")
    run.check_digest(record, "b2 benchmark")
    assert record["digest"] == "5a9080d424b18b39f10fd81325515ec8a870e9b81984f4e7a0f40bf4839bd298"
    plan = load_acquisition_plan(directory / "acquisition.plan.json")
    validate_plan_authorization(plan, catalog_source_approved=True)
    assert bench._minted(record).plan_hash == plan.plan_hash
    assert plan.plan_hash == "25bcb8ae9121d1744d945d9a9a687f918a28b1ce54cb9a6945c0aaa277f5bffb"
    assert run.read_json(directory / "authorization.json")["benchmark_digest"] == record["digest"]
    part = roots.scratch("bench-b2", "f00000.parquet.part")
    state_path = roots.scratch("bench-b2", "f00000.state.json")
    state_bytes = state_path.read_bytes()
    state = run.read_json(state_path)
    expected = ("4eeb58bc769a194f472100ed4acd1d960e35c669caecd96fd4ce6425fabca38d", 2_771_021_138)
    assert sp.file_sha256(part) == expected
    assert (
        state["sha256"] == state["prefix_sha256"] == state["linked_etag"].strip('"') == expected[0]
    )
    assert state["length"] == state["verified_bytes"] == expected[1]
    assert state["repo_commit"] == plan.revision
    assert state["complete"] is True and state["version"] == sp.STATE_VERSION
    assert state["url"] == run.source_url(record["source"], state["name"])
    history = {
        str(p): {
            "sha256": sp.file_sha256(p)[0],
            "size": p.stat().st_size,
            "mtime_ns": p.stat().st_mtime_ns,
        }
        for label in ("b1", "b2")
        for p in bench.benchmark_dir(roots, label).iterdir()
        if p.is_file() and p.suffix in {".json", ".jsonl"}
    }
    limits = record["limits"]
    budget = sp.ScratchBudget(
        roots.scratch(), int(limits["scratch_cap_bytes"]), int(limits["scratch_min_free_bytes"])
    )
    before = budget.occupied()
    assert before == 5_678_988_129, "stop if the reviewed scratch inventory has changed"
    receipt = archive.archive_benchmark(roots, "b1", destination, max_bytes=2_907_965_893)
    assert sp.file_sha256(part) == expected
    assert state_path.read_bytes() == state_bytes
    assert part.samefile(destination / "f00000.parquet.part")
    assert budget.occupied() == 2_771_022_236
    for name, original in history.items():
        path = Path(name)
        assert {
            "sha256": sp.file_sha256(path)[0],
            "size": path.stat().st_size,
            "mtime_ns": path.stat().st_mtime_ns,
        } == original
    classification = run.classify(
        roots,
        record,
        {"receipts": [], "remaining": [{"rank": 0, "file": state["name"]}]},
        "bench-b2",
    )
    assert classification["counts"]["local_complete_reuse"] == 1
    assert classification["worst_case_network_bytes"] == 0
    # Source-only corrected reservation fits. This does NOT reserve processing output.
    assert budget.reserve("source-only-diagnostic", expected[1], part)
    budget.release("source-only-diagnostic")
    processing_allowance = int(limits["max_durable_bytes_per_file"]) - int(limits["max_file_bytes"])
    available = budget.cap_bytes - budget.occupied()
    result = run.self_digest(
        {
            "kind": "finepdfs_b1_archive_b2_capacity_check",
            "archive_digest": receipt["digest"],
            "benchmark_digest": record["digest"],
            "plan_hash": plan.plan_hash,
            "source_sha256": expected[0],
            "source_bytes": expected[1],
            "state_sha256": sp.file_sha256(state_path)[0],
            "pre_occupied": before,
            "post_occupied": budget.occupied(),
            "remaining_capacity": available,
            "planned_processing_allowance": processing_allowance,
            "processing_allowance_deficit": processing_allowance - available,
            "physical_free_bytes": free_bytes(roots.scratch()),
            "scratch_min_free_bytes": budget.min_free_bytes,
            "classification": classification,
            "preserved_history": history,
            "environment": {
                "python": platform.python_version(),
                "platform": platform.platform(),
                "offline": {
                    name: os.environ[name]
                    for name in (
                        "UV_OFFLINE",
                        "HF_HUB_OFFLINE",
                        "HF_DATASETS_OFFLINE",
                        "TRANSFORMERS_OFFLINE",
                    )
                },
            },
            "seconds": time.monotonic() - started,
            "verdict": "FINEPDFS B2 REQUIRES B3 FOR SCRATCH CAPACITY",
        }
    )
    assert processing_allowance > available
    run.write_once(directory / "b1-archive-capacity-check.json", result)
    evidence = Path(__file__).parent
    run.write_once(evidence / "scratch-archive.json", receipt)
    run.write_once(evidence / "capacity-check.json", result)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
