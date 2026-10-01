"""Bounded real transport benchmarks that produce comparable performance receipts.

Two receipts describe the same kind of work under two modes:

- ``whole_file_local``: a small authorized plan of benchmark-reserved files
  (the last ranks of the frozen inventory, which production plans never
  select, or an explicitly named calibration file when a source has no
  inventory). Files stream to scratch, are verified and processed locally;
  nothing is promoted to the durable store and the scratch copies are removed
  once the receipt is written, because benchmark rows are not production data.
- ``range_selected``: a pilot-scope ``xlm data fetch`` of planned rows of the
  same file, normalized from its own performance file, journal, adaptation
  summary and documents.

The transport policy can then be frozen on ``measured`` inputs. A benchmark
never consumes a production inventory position.
"""

from __future__ import annotations

import os
import shutil
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, TextIO

from filelock import FileLock, Timeout

from xlm.data.acquisition.plan import (
    AcquisitionPlan,
    PlanAuthorization,
    load_acquisition_plan,
    save_acquisition_plan,
    validate_plan_authorization,
)
from xlm.data.acquisition.source_dashboard import ObservedScratch
from xlm.data.acquisition.source_local import process_source_unit, scan_documents
from xlm.data.acquisition.source_parquet import (
    _LINKED_SHA256,
    STATE_VERSION,
    TransferMeter,
    TransferResult,
    file_sha256,
    identity_record,
)
from xlm.data.acquisition.source_plan import acquisition_plan, plan_limits
from xlm.data.acquisition.source_run import (
    Monitor,
    Roots,
    RunError,
    Sampler,
    check_digest,
    next_index,
    performance_receipt,
    prepare_units,
    read_json,
    self_digest,
    source_url,
    transfer_limits,
    unit_key,
    write_once,
)
from xlm.data.acquisition.transport_policy import (
    SourceLayout,
    TransportMode,
    adapt_rate_from_log,
)
from xlm.data.sources import essential_web_local as pipeline

BENCHMARK_KIND = "mix01_source_benchmark_plan"
RANGE_RECEIPT_KIND = "mix01_source_range_benchmark"
MAX_BENCHMARK_FILES = 4


def reserved_entries(
    inventory_files: Sequence[str], reserved: int, count: int
) -> list[dict[str, Any]]:
    """The benchmark-reserved inventory tail (production plans never select it)."""
    if not 1 <= count <= reserved <= len(inventory_files):
        raise RunError("benchmark files must come from the reserved inventory tail")
    start = len(inventory_files) - reserved
    return [
        {"rank": start + i, "file": inventory_files[start + i], "reason": "benchmark-reserved rank"}
        for i in range(count)
    ]


def build_benchmark(
    *,
    source_key: str,
    label: str,
    pin: Mapping[str, str],
    entries: Sequence[Mapping[str, Any]],
    layout: SourceLayout,
    seed: int,
    admission: Mapping[str, str],
    download_workers: int,
    process_workers: int,
) -> dict[str, Any]:
    """A bounded, self-digested whole-file benchmark plan; authorized like production."""
    if not label.isidentifier() or not 1 <= len(entries) <= MAX_BENCHMARK_FILES:
        raise RunError(f"benchmark needs a plain label and 1..{MAX_BENCHMARK_FILES} files")
    files = [str(e["file"]) for e in entries]
    policy, limits = plan_limits(len(files), layout, TransportMode.WHOLE_FILE_LOCAL, pin)
    # One stream and at most one process per file: record the effective concurrency.
    policy["download_workers"] = min(download_workers, len(files))
    policy["process_workers"] = min(process_workers, len(files))
    if not 1 <= download_workers <= 16 or not 0 <= process_workers <= 16:
        raise RunError("benchmark concurrency must be 1..16 streams and 0..16 processes")
    minted = acquisition_plan(pin, files, limits, seed, f"benchmark {label}; not production data")
    return self_digest(
        {
            "kind": BENCHMARK_KIND,
            "version": 1,
            "source_key": source_key,
            "label": label,
            "source": dict(pin),
            "transport_mode": TransportMode.WHOLE_FILE_LOCAL.value,
            "files": [dict(e) for e in entries],
            "expected": {
                "transfer_bytes": len(files) * layout.file_bytes,
                "requests": 2 * len(files),
                "rows": len(files) * layout.rows_per_file,
            },
            "limits": policy,
            "acquisition_plan": {
                "plan_id": minted.plan_id,
                "plan_hash": minted.plan_hash,
                "limits": minted.limits.model_dump(),
                "seed": seed,
            },
            "inputs": {"admission": dict(admission)},
            "retention": "scratch only; verified files and outputs are removed after the "
            "receipt; nothing enters the durable store or the production inventory",
            "authorization": "STOP: review the digest, then authorize the benchmark explicitly",
        }
    )


def benchmark_dir(roots: Roots, label: str) -> Path:
    return roots.plans / "benchmarks" / label


def store_benchmark(roots: Roots, record: Mapping[str, Any]) -> Path:
    check_digest(record, "benchmark plan")
    path = benchmark_dir(roots, str(record["label"])) / "benchmark.json"
    write_once(path, record)
    return path


def _minted(record: Mapping[str, Any]) -> AcquisitionPlan:
    from xlm.data.acquisition.plan import AcquisitionLimits

    minted = acquisition_plan(
        record["source"],
        [str(e["file"]) for e in record["files"]],
        AcquisitionLimits.model_validate(record["acquisition_plan"]["limits"]),
        int(record["acquisition_plan"]["seed"]),
        f"benchmark {record['label']}; not production data",
    )
    if minted.plan_hash != record["acquisition_plan"]["plan_hash"]:
        raise RunError("benchmark record does not reproduce its plan hash")
    return minted


def authorize_benchmark(
    roots: Roots,
    label: str,
    digest: str,
    operator: str,
    admitted: Callable[[AcquisitionPlan], None],
) -> AcquisitionPlan:
    directory = benchmark_dir(roots, label)
    record = read_json(directory / "benchmark.json")
    check_digest(record, "benchmark plan")
    if digest != record["digest"] or not operator.strip():
        raise RunError("digest/operator does not match this benchmark; nothing authorized")
    from datetime import UTC, datetime

    target = directory / "authorization.json"
    if not target.exists():
        write_once(
            target,
            {
                "benchmark_digest": digest,
                "operator": operator.strip(),
                "authorized_at": datetime.now(UTC).isoformat(),
            },
        )
    elif read_json(target)["benchmark_digest"] != digest:
        raise RunError("a different benchmark authorization is already recorded")
    minted = _minted(record)
    plan = minted.model_copy(
        update={
            "authorization": PlanAuthorization(
                authorization_hash=minted.plan_hash,
                authorized_by=f"operator:{operator.strip()}",
                authorized_at=read_json(target)["authorized_at"],
                scope="production",
            )
        }
    )
    admitted(plan)
    validate_plan_authorization(plan, catalog_source_approved=True)
    save_acquisition_plan(plan, directory / "acquisition.plan.json")
    return plan


def _scratch_key(entries: Sequence[Mapping[str, Any]], name: str) -> str:
    for i, entry in enumerate(entries):
        if entry["file"] == name:
            return unit_key(int(entry["rank"]) if entry.get("rank") is not None else i)
    raise RunError(f"'{name}' is not a file of the donor benchmark")


def adopt_benchmark_download(roots: Roots, label: str, donor: str) -> dict[str, Any]:
    """OFFLINE: reuse a donor benchmark's complete download of the same pinned files.

    Only for an authorized benchmark of the same source, repository and
    revision. The donor's scratch state must be complete, name the identical
    canonical URL and resolved commit, and carry a repository-declared SHA-256
    equal to the recorded one; the hard-linked bytes are hashed again before
    their state is written. The donor's file stays where it is, and the
    adopted state charges no transfer, so the new run reports a cache hit.
    """
    if donor == label:
        raise RunError("a benchmark cannot adopt its own download")
    record = read_json(benchmark_dir(roots, label) / "benchmark.json")
    check_digest(record, "benchmark plan")
    authorization = benchmark_dir(roots, label) / "authorization.json"
    if (
        not authorization.is_file()
        or read_json(authorization)["benchmark_digest"] != (record["digest"])
    ):
        raise RunError("only an authorized benchmark adopts a download")
    plan = load_acquisition_plan(benchmark_dir(roots, label) / "acquisition.plan.json")
    donor_record = read_json(benchmark_dir(roots, donor) / "benchmark.json")
    check_digest(donor_record, "donor benchmark plan")
    keys = ("provider", "repository", "revision", "source_id", "view_id")
    if any(donor_record["source"][k] != record["source"][k] for k in keys):
        raise RunError("donor benchmark belongs to another source, repository or revision")
    adopted = []
    for i, entry in enumerate(record["files"]):
        name = str(entry["file"])
        key = unit_key(int(entry["rank"]) if entry.get("rank") is not None else i)
        donor_key = _scratch_key(donor_record["files"], name)
        part = roots.scratch(f"bench-{donor}", f"{donor_key}.parquet.part")
        state = read_json(roots.scratch(f"bench-{donor}", f"{donor_key}.state.json"))
        declared = _LINKED_SHA256.fullmatch(str(state.get("linked_etag") or ""))
        sha256, length = str(state.get("sha256")), int(state.get("length", -1))
        expected = plan.expected_file_digests.get(name)
        if (
            state.get("version") != STATE_VERSION
            or state.get("complete") is not True
            or state.get("name") != name
            or state.get("url") != source_url(record["source"], name)
            or state.get("repo_commit") != record["source"]["revision"]
            or state.get("verified_bytes") != length
            or declared is None
            or declared[1].lower() != sha256
            or (expected is not None and expected.lower() != sha256)
        ):
            raise RunError(f"{name}: donor download is incomplete or not independently bound")
        target = roots.scratch(f"bench-{label}", f"{key}.parquet.part")
        target_state = roots.scratch(f"bench-{label}", f"{key}.state.json")
        if target.exists() or target_state.exists():
            raise RunError(f"{name}: benchmark {label} already holds a download; refusing")
        target.parent.mkdir(parents=True, exist_ok=True)
        os.link(part, target)
        if file_sha256(target) != (sha256, length):
            target.unlink()
            raise RunError(f"{name}: donor bytes do not match their recorded SHA-256")
        provenance = {"benchmark": donor, "digest": donor_record["digest"], "sha256": sha256}
        write_once(
            target_state,
            {
                **state,
                "charged_bytes": 0,
                "requests": 0,
                "redirects": 0,
                "retries": 0,
                "adopted_from": provenance,
            },
        )
        adopted.append({"file": name, "length": length, **provenance})
    receipt = self_digest(
        {"kind": "mix01_source_benchmark_adoption", "label": label, "files": adopted}
    )
    write_once(benchmark_dir(roots, label) / "adoption.json", receipt)
    return receipt


def run_benchmark(
    roots: Roots,
    label: str,
    *,
    admitted: Callable[[AcquisitionPlan], None],
    stream: TextIO | None = None,
    url_for: Callable[[str], str] | None = None,
) -> dict[str, Any]:
    """NETWORK: run one authorized whole-file benchmark; write its performance receipt."""
    directory = benchmark_dir(roots, label)
    record = read_json(directory / "benchmark.json")
    check_digest(record, "benchmark plan")
    if read_json(directory / "authorization.json")["benchmark_digest"] != record["digest"]:
        raise RunError("benchmark is not authorized")
    plan = load_acquisition_plan(directory / "acquisition.plan.json")
    if plan.plan_hash != record["acquisition_plan"]["plan_hash"]:
        raise RunError("authorized benchmark plan differs from its record")
    validate_plan_authorization(plan, catalog_source_approved=True)
    admitted(plan)
    output = stream or sys.stdout
    url = url_for or (lambda name: source_url(record["source"], name))
    run_label = f"bench-{label}"
    staging = roots.scratch(run_label, "staging")
    roots.plans.mkdir(parents=True, exist_ok=True)
    try:
        lock = FileLock(str(roots.plans / "run.lock"), timeout=1)
        lock.acquire()
    except Timeout as exc:
        raise RunError("another run of this source is active") from exc
    try:
        shutil.rmtree(staging, ignore_errors=True)
        entries = [
            {"rank": e["rank"] if e.get("rank") is not None else i, "file": e["file"]}
            for i, e in enumerate(record["files"])
        ]
        units, _, charged = prepare_units(
            roots, record, plan, entries, run_label, staging, url, durable=False
        )
        limits = record["limits"]
        scratch = ObservedScratch(
            roots.scratch(),
            int(limits["scratch_cap_bytes"]),
            int(limits["scratch_min_free_bytes"]),
        )
        meter = TransferMeter(
            plan.limits.max_transferred_bytes, plan.limits.max_requests, bytes_used=charged
        )
        resume = {"receipts": [], "sealed": 0, "total": len(units)}
        monitor = Monitor(
            roots,
            record,
            resume,
            units,
            scratch,
            meter,
            output,
            directory / "events.jsonl",
            title=f"benchmark {label} {record['digest'][:12]}",
            target=None,
            prior_canonical=0,
            staging=staging,
        )
        transfers: list[TransferResult] = []
        results: list[dict[str, Any]] = []

        def on_done(
            unit: pipeline.Unit, transfer: TransferResult | None, result: dict[str, Any] | None
        ) -> None:
            if result is None or transfer is None:
                raise RunError("benchmark unit finished without transfer and processing")
            documents = Path(str(result["staging_dir"])) / "documents.jsonl"
            if scan_documents(documents) != (
                int(result["documents"]),
                int(result["canonical_bytes"]),
                result["documents_sha256"],
            ):
                raise RunError(f"{unit.source_file}: benchmark output does not reconcile")
            transfers.append(transfer)
            results.append(result)
            monitor.dashboard.event(
                "measured", key=unit.key, rows=result["rows"], seconds=result["process_seconds"]
            )
            shutil.rmtree(Path(str(result["staging_dir"])), ignore_errors=True)
            unit.partial.unlink(missing_ok=True)
            unit.state.unlink(missing_ok=True)

        sampler = Sampler()
        sampler.start()
        stats: dict[str, Any] = {}
        outcome: dict[str, Any] = {"status": "completed"}
        try:
            stats = pipeline.run_pipeline(
                units,
                limits=transfer_limits(record),
                revision=plan.revision,
                download_workers=int(limits["download_workers"]),
                process_workers=int(limits["process_workers"]),
                scratch=scratch,
                meter=meter,
                deadline_seconds=plan.limits.overall_deadline_seconds,
                identity_for=lambda unit, transfer: identity_record(
                    transfer.identity,
                    source_file=unit.source_file,
                    repository=plan.repository,
                    revision=plan.revision,
                ),
                on_done=on_done,
                process=process_source_unit,
                on_progress=monitor.update,
            )
        except BaseException as exc:
            outcome = {
                "status": "failed",
                "root_failure": monitor.failures[0]
                if monitor.failures
                else {"exception": type(exc).__name__},
                "cancelled_because_of_root_failure": list(monitor.cancelled),
                "preserved": "verified partial benchmark downloads stay on scratch for a rerun",
            }
            raise
        finally:
            monitor.dashboard.clear()
            system = sampler.stop()
            receipt = performance_receipt(
                record={**record, "sequence": None},
                label=run_label,
                mode=TransportMode.WHOLE_FILE_LOCAL.value,
                downloads=int(limits["download_workers"]),
                processes=int(limits["process_workers"]),
                outcome=outcome,
                transfers=transfers,
                receipts=[],
                results=results,
                stats=stats,
                system=system,
                benchmark={"label": label, "digest": record["digest"], "files": record["files"]},
            )
            index = next_index(directory, "performance")
            write_once(directory / f"performance-{index:02d}.json", receipt)
        shutil.rmtree(staging, ignore_errors=True)
        return receipt
    finally:
        lock.release()


def range_benchmark_receipt(
    *,
    source_key: str,
    pin: Mapping[str, str],
    plan: Mapping[str, Any],
    perf: Mapping[str, Any],
    journal: Mapping[str, Any],
    documents: Path,
    adapt_log: str,
) -> dict[str, Any]:
    """Normalize one completed pilot range fetch + adapt into a comparable receipt."""
    if (plan.get("source_id"), plan.get("view_id"), plan.get("revision")) != (
        pin["source_id"],
        pin["view_id"],
        pin["revision"],
    ):
        raise RunError("range benchmark plan belongs to another source, view or revision")
    if perf.get("plan_id") != plan.get("plan_id") or journal.get("plan_hash") != plan.get(
        "plan_hash"
    ):
        raise RunError("range benchmark files belong to different plans")
    if perf.get("status") != "COMPLETED" or journal.get("status") != "COMPLETED":
        raise RunError("range benchmark fetch did not complete")
    rate = adapt_rate_from_log(adapt_log)
    if rate is None:
        raise RunError("adapt log carries no throughput line")
    count, canonical_bytes, digest = scan_documents(documents)
    telemetry = perf["telemetry"]
    requests = int(perf["requests_made"])
    rows = int(perf["records_acquired"])
    wall = float(perf["wall_seconds"])
    transferred = int(perf["transferred_bytes"])
    return self_digest(
        {
            "kind": RANGE_RECEIPT_KIND,
            "version": 1,
            "source_key": source_key,
            "source": dict(pin),
            "transport_mode": TransportMode.RANGE_SELECTED.value,
            "plan": {"plan_id": plan["plan_id"], "plan_hash": plan["plan_hash"]},
            # The range fetcher runs one stream per file: effective streams.
            "concurrency": {
                "download_workers": max(
                    1,
                    min(
                        int(perf.get("max_workers_configured", 1)),
                        len(plan.get("selected_files") or [None]),
                    ),
                ),
                "process_workers": 1,
            },
            "transfer": {
                "transferred_bytes": transferred,
                "requests": requests,
                "retries": int(telemetry.get("retries", 0)),
                "wall_seconds": wall,
                "megabytes_per_second": transferred / 1e6 / wall if wall > 0 else None,
                "mean_request_open_seconds": float(telemetry["open_seconds"]) / max(1, requests),
                "row_groups": int(telemetry.get("parquet_groups", 0)),
            },
            "processing": {
                "rows": rows,
                "rows_per_process_second": rate,
                "documents": count,
                "canonical_bytes": canonical_bytes,
                "documents_sha256": digest,
                "canonical_bytes_per_transferred_byte": canonical_bytes / transferred
                if transferred
                else None,
            },
            "evidence": "pilot-scope xlm data fetch performance file, journal, adapt log and "
            "canonical documents of the same benchmark-reserved file",
        }
    )
