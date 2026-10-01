"""Offline retained-artifact verification and one b3 plan; never authorize/adopt/run."""

from __future__ import annotations

import json
import platform
import runpy
import shutil
import sys
import time
from pathlib import Path
from typing import Any

from xlm.data.acquisition import source_archive as archive
from xlm.data.acquisition import source_benchmark as bench
from xlm.data.acquisition import source_parquet as sp
from xlm.data.acquisition import source_run as run
from xlm.data.acquisition.plan import load_acquisition_plan, validate_plan_authorization
from xlm.data.acquisition.source_growth import ProcessingGrowth
from xlm.data.sources import essential_web_recovery as recovery


def no_network(event: str, args: tuple[Any, ...]) -> None:
    if event in {"socket.connect", "socket.getaddrinfo", "socket.bind"}:
        raise RuntimeError("network forbidden during b3 planning")


def snapshot(path: Path) -> dict[str, Any]:
    digest, length = sp.file_sha256(path)
    return {"sha256": digest, "size": length, "mtime_ns": path.stat().st_mtime_ns}


def main() -> None:
    sys.addaudithook(no_network)
    started = time.monotonic()
    repo = Path.cwd()
    evidence = Path(__file__).resolve().parent
    roots = run.Roots(Path("G:/XLM"), Path("C:/XLM-scratch"), "finepdfs")
    b3_dir = bench.benchmark_dir(roots, "b3")
    assert not b3_dir.exists(), "b3 already exists; review it instead of replanning"
    history_paths = [
        p
        for label in ("b1", "b2")
        for p in bench.benchmark_dir(roots, label).iterdir()
        if p.is_file()
    ]
    b2_dir = bench.benchmark_dir(roots, "b2")
    b2 = run.read_json(b2_dir / "benchmark.json")
    assert b2["digest"] == "5a9080d424b18b39f10fd81325515ec8a870e9b81984f4e7a0f40bf4839bd298"
    for label in ("b1", "b2"):
        directory = bench.benchmark_dir(roots, label)
        record = run.read_json(directory / "benchmark.json")
        run.check_digest(record, label)
        plan = load_acquisition_plan(directory / "acquisition.plan.json")
        validate_plan_authorization(plan, catalog_source_approved=True)
        assert bench._minted(record).plan_hash == plan.plan_hash
    history = {str(p): snapshot(p) for p in history_paths}
    b1 = run.read_json(bench.benchmark_dir(roots, "b1") / "benchmark.json")
    archived = archive.donor_directory(roots, "b1", b1)
    assert archived == Path("C:/XLM-scratch-history/finepdfs/bench-b1")
    archive_before = archive.inventory(archived, 2_907_965_893)
    part = roots.scratch("bench-b2", "f00000.parquet.part")
    state_path = roots.scratch("bench-b2", "f00000.state.json")
    source_before, state_before = snapshot(part), snapshot(state_path)
    assert source_before["sha256"] == (
        "4eeb58bc769a194f472100ed4acd1d960e35c669caecd96fd4ce6425fabca38d"
    )
    assert source_before["size"] == 2_771_021_138
    assert part.samefile(archived / part.name)
    budget = sp.ScratchBudget(
        roots.scratch(), b2["limits"]["scratch_cap_bytes"], b2["limits"]["scratch_min_free_bytes"]
    )
    occupied = budget.occupied()
    assert occupied == 2_771_022_236
    resume = sp.inspect_source(
        run.source_url(b2["source"], b2["files"][0]["file"]),
        part,
        state_path,
        name=b2["files"][0]["file"],
        limits=run.transfer_limits(b2),
        revision=b2["source"]["revision"],
    )
    assert resume.kind == "local_complete_reuse" and resume.new_growth == 0
    ultrax = run.Roots(roots.data_root, roots.scratch_root, "ultrax")
    ultra_paths = [p for p in ultrax.plans.rglob("*.json") if p.is_file()]
    ultra_before = {str(p): snapshot(p) for p in ultra_paths}
    seal = run.first_pass_seal(ultrax, content=True)  # Identical publication is a read-only no-op.
    assert ultra_before == {str(p): snapshot(p) for p in ultra_paths}
    manifest = run.read_json(repo / recovery.MANIFEST)
    assert recovery.compatible_code(repo, manifest)
    cli = runpy.run_path(str(repo / "scripts/mix01_source.py"), run_name="b3_offline_cli")
    exit_code = cli["main"](
        [
            "benchmark",
            "plan",
            "--source-key",
            "finepdfs",
            "--label",
            "b3",
            "--file",
            b2["files"][0]["file"],
        ]
    )
    assert exit_code == 0
    b3 = run.read_json(b3_dir / "benchmark.json")
    run.check_digest(b3, "b3")
    assert bench._minted(b3).plan_hash == b3["acquisition_plan"]["plan_hash"]
    assert b3["source"] == b2["source"] and b3["files"] == b2["files"]
    allowed = {"scratch_cap_bytes", "processing_growth", "scratch_retained_bytes"}
    changed = {
        k
        for k in set(b2["limits"]) | set(b3["limits"])
        if b2["limits"].get(k) != b3["limits"].get(k)
    }
    assert changed == allowed, changed
    assert b3["inputs"] == b2["inputs"]
    growth = ProcessingGrowth.model_validate(b3["limits"]["processing_growth"])
    cap = occupied + b3["limits"]["max_file_bytes"] + growth.processing_peak + growth.state_peak
    assert b3["limits"]["scratch_cap_bytes"] == cap
    assert history == {str(p): snapshot(p) for p in history_paths}
    assert source_before == snapshot(part) and state_before == snapshot(state_path)
    assert archive_before == archive.inventory(archived, 2_907_965_893)
    assert occupied == budget.occupied()
    assert {p.name for p in b3_dir.iterdir()} == {"benchmark.json"}
    assert not roots.scratch("bench-b3").exists()
    result = run.self_digest(
        {
            "kind": "finepdfs_b3_offline_plan_verification",
            "benchmark_digest": b3["digest"],
            "plan_hash": b3["acquisition_plan"]["plan_hash"],
            "scratch_cap_bytes": cap,
            "active_scratch_bytes": occupied,
            "source": source_before,
            "state": state_before,
            "new_source_growth_on_reuse": resume.new_growth,
            "limits_changed_from_b2": sorted(changed),
            "growth": growth.model_dump(),
            "b1_b2_history_unchanged": history,
            "archive_inventory_unchanged": archive_before,
            "ultrax_operator_files_unchanged": ultra_before,
            "ultrax_first_pass_seal": seal["digest"],
            "ultrax_units": len(seal["units"]),
            "essential_compatibility": True,
            "physical_free_bytes": shutil.disk_usage(part).free,
            "scratch_min_free_bytes": b3["limits"]["scratch_min_free_bytes"],
            "platform": platform.platform(),
            "python": platform.python_version(),
            "seconds": time.monotonic() - started,
            "network": "socket audit hook refused",
            "authorized": False,
            "adopted": False,
            "executed": False,
        }
    )
    run.write_once(evidence / "verification.json", result)
    run.write_once(evidence / "benchmark.json", b3)
    print(
        json.dumps(
            {
                k: result[k]
                for k in ("digest", "benchmark_digest", "plan_hash", "scratch_cap_bytes", "seconds")
            }
        )
    )


if __name__ == "__main__":
    main()
