"""Phase-A audit orchestration: bind, resume, scan, aggregate, publish (receipt last)."""

from __future__ import annotations

import hashlib
import json
import shutil
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from xlm.data.evidence_v2 import canonical
from xlm.data.quality.overlay import KeptOverlay, load_overlay
from xlm.data.quality.report import ARTIFACTS, build_artifacts
from xlm.data.quality.scan import (
    BINDING_FILE,
    MAX_LINE_CEILING,
    RECEIPT_FILE,
    UNITS_DIR,
    WORKER_CHOICES,
    FileAccumulator,
    InputManifest,
    OrderedPool,
    OutputBudget,
    Progress,
    QualityError,
    audit_binding,
    commit_unit,
    file_tasks,
    implementation,
    load_manifest,
    load_unit,
    process_chunk,
    unit_path,
)

RECEIPT_KIND = "xlm_quality_audit_receipt_v1"
MAX_RSS_BYTES = 16 * 1024**3


@dataclass(frozen=True)
class Limits:
    workers: int
    max_rss_bytes: int
    free_reserve_bytes: int
    max_output_bytes: int
    line_ceiling: int
    deadline_seconds: float | None

    def check(self) -> None:
        if self.workers not in WORKER_CHOICES:
            raise QualityError("workers must be 1, 2, 4, 8 or 16")
        if not 0 < self.max_rss_bytes <= MAX_RSS_BYTES:
            raise QualityError("--max-rss-gib must be in (0, 16]")
        if not 0 < self.line_ceiling <= MAX_LINE_CEILING:
            raise QualityError("--max-document-mib outside its bound")
        if self.max_output_bytes <= 0 or self.free_reserve_bytes < 0:
            raise QualityError("output limits must be positive")
        if self.deadline_seconds is not None and not 0 < self.deadline_seconds <= 7 * 86400:
            raise QualityError("--deadline-hours outside (0, 168]")


def _overlaps(a: Path, b: Path) -> bool:
    a, b = a.resolve(), b.resolve()
    return a.is_relative_to(b) or b.is_relative_to(a)


def stream_units(
    output: Path, manifest: InputManifest, digest: str, identities: list[dict[str, Any]]
) -> Iterator[dict[str, Any]]:
    """Verified units one at a time, in manifest order (never all in memory).

    ``identities`` collects each file's content-free source identity for the receipt.
    """
    for item in manifest.files:
        unit = load_unit(output, item, digest, manifest.data_root)
        identities.append(
            {
                "path": unit["file"]["path"],
                "documents_sha256": unit["file"]["documents_sha256"],
                "file_bytes": unit["file"]["file_bytes"],
                "documents": unit["file"]["documents"],
                "size": unit["stat"]["size"],
                "mtime_ns": unit["stat"]["mtime_ns"],
            }
        )
        yield unit


def _check_output(output: Path, manifest: InputManifest) -> None:
    """The output may live under the data root (as C05/C06 outputs do), but never in,
    above or below a directory that holds an audited corpus file."""
    root = manifest.data_root.resolve()
    if _overlaps(output, root) and root.is_relative_to(output.resolve()):
        raise QualityError("output directory contains the corpus data root")
    for parent in sorted({(root / f.path).parent for f in manifest.files}):
        if _overlaps(output, parent):
            raise QualityError("output directory overlaps a corpus file directory")


def prepare(
    manifest_path: Path,
    output: Path,
    *,
    data_root: Path | None,
    proof: Path | None,
    allow_authored_proof: bool,
    line_ceiling: int,
) -> tuple[InputManifest, KeptOverlay | None, dict[str, Any], dict[str, str]]:
    manifest = load_manifest(manifest_path, data_root)
    _check_output(output, manifest)
    overlay = None
    if proof is not None:
        overlay = load_overlay(
            proof,
            manifest_digest=manifest.digest,
            documents={f.path: f.documents for f in manifest.files},
            allow_authored=allow_authored_proof,
            consumes=[manifest.data_root, output],
        )
    identity = implementation()
    binding = audit_binding(manifest, overlay, line_ceiling, identity)
    return manifest, overlay, binding, identity


def run_audit(
    manifest_path: Path,
    output: Path,
    *,
    limits: Limits,
    data_root: Path | None = None,
    proof: Path | None = None,
    allow_authored_proof: bool = False,
    progress_interval: float | None = 5.0,
) -> dict[str, Any]:
    limits.check()
    started = time.monotonic()
    manifest, overlay, binding, identity = prepare(
        manifest_path,
        output,
        data_root=data_root,
        proof=proof,
        allow_authored_proof=allow_authored_proof,
        line_ceiling=limits.line_ceiling,
    )
    output.mkdir(parents=True, exist_ok=True)
    if (output / RECEIPT_FILE).exists():
        raise QualityError("audit already complete in this output directory; use `report`")
    binding_path = output / BINDING_FILE
    if binding_path.exists():
        existing = canonical.loads_bytes_strict(binding_path.read_bytes())
        if existing != binding:
            raise QualityError(
                "output directory belongs to a different audit binding (code, policy, "
                "manifest, overlay or limits changed); use a new output directory"
            )
    else:
        canonical.write_canonical_json(binding_path, binding)
    (output / UNITS_DIR).mkdir(exist_ok=True)
    for stale in (output / UNITS_DIR).glob("*.unit.zz.tmp"):
        stale.unlink()  # Only our own interrupted atomic-write staging files.
    if shutil.disk_usage(output).free < limits.free_reserve_bytes:
        raise QualityError("free space below --free-reserve-gib before the scan")
    budget = OutputBudget(limits.max_output_bytes, output)
    root = manifest.data_root
    digest = str(binding["digest"])
    resumed = [f for f in manifest.files if unit_path(output, f.ordinal).exists()]
    for item in resumed:
        load_unit(output, item, digest, root)  # refuses on any binding/file change
    done = {f.ordinal for f in resumed}
    pending = [f for f in manifest.files if f.ordinal not in done]
    totals = manifest.totals
    reporter = Progress(progress_interval, totals)
    measured = _scan(output, manifest, overlay, pending, digest, limits, budget, reporter)
    identities: list[dict[str, Any]] = []
    artifacts, result_digest = build_artifacts(
        binding, stream_units(output, manifest, digest, identities)
    )
    for name in ARTIFACTS:
        budget.charge(len(artifacts[name]))
        canonical.write_atomic(output / name, artifacts[name])
    elapsed = time.monotonic() - started
    receipt = {
        "kind": RECEIPT_KIND,
        "status": "COMPLETE",
        "phase": "A_READ_ONLY_AUDIT",
        "corpus_modified": False,
        "actions_executed": [],
        "binding": binding,
        "code_commit": identity["code_commit"],
        "source_files": identities,
        "artifacts": {
            name: {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
            for name, data in sorted(artifacts.items())
        },
        "result_digest": result_digest,
        "execution": {
            "workers": limits.workers,
            "files_scanned": len(pending),
            "files_resumed": len(resumed),
            "wall_seconds": round(elapsed, 3),
            **measured,
            "note": "execution facts are operational and excluded from result_digest",
        },
    }
    raw = (json.dumps(receipt, sort_keys=True, indent=1) + "\n").encode("utf-8")
    budget.charge(len(raw))
    canonical.write_atomic(output / RECEIPT_FILE, raw)
    return {
        "complete": True,
        "result_digest": result_digest,
        "output": str(output),
        "files_scanned": len(pending),
        "files_resumed": len(resumed),
        "wall_seconds": round(elapsed, 3),
        "scan": measured,
    }


def _scan(
    output: Path,
    manifest: InputManifest,
    overlay: KeptOverlay | None,
    pending: list[Any],
    digest: str,
    limits: Limits,
    budget: OutputBudget,
    reporter: Progress,
) -> dict[str, Any]:
    from xlm.data.exclusion.policy import C05Error
    from xlm.data.exclusion.supervisor import (
        DEADLINE_REASON,
        DISK_REASON,
        RSS_REASON,
        Deadline,
        Supervisor,
    )

    root = manifest.data_root
    started = time.monotonic()
    scanned_bytes = sum(f.file_bytes for f in pending)
    done_files = len(manifest.files) - len(pending)
    done_docs = sum(f.documents for f in manifest.files) - sum(f.documents for f in pending)
    done_bytes = sum(f.file_bytes for f in manifest.files) - scanned_bytes
    progress = {"docs": done_docs, "bytes": done_bytes, "files": done_files}

    def tasks() -> Any:
        for item in pending:
            kept = None if overlay is None else overlay.bitmap(item.path)
            yield from file_tasks(root, item, kept, limits.line_ceiling)

    supervisor = Supervisor(
        Deadline(limits.deadline_seconds, started),
        limits.max_rss_bytes,
        interval=0.5,
        volumes={str(output): limits.free_reserve_bytes},
        warn=lambda _message: None,
    )
    reasons = {
        RSS_REASON: "process-tree RSS exceeded --max-rss-gib; nothing committed for open files",
        DISK_REASON: "free space fell below --free-reserve-gib",
        DEADLINE_REASON: "--deadline-hours exceeded; committed units are kept for resume",
    }
    by_ordinal = {f.ordinal: f for f in pending}
    current: FileAccumulator | None = None
    try:
        with supervisor, OrderedPool(limits.workers, supervisor) as pool:
            for result in pool.map(process_chunk, tasks()):
                if current is None or current.item.ordinal != result.ordinal:
                    current = FileAccumulator(by_ordinal[result.ordinal])
                current.add(result)
                progress["docs"] += result.rows
                if result.last:
                    commit_unit(output, digest, current, root, budget)
                    progress["files"] += 1
                    progress["bytes"] += current.item.file_bytes
                    current = None
                reporter.update(
                    progress["files"], progress["docs"], progress["bytes"], supervisor.peak_rss
                )
    except C05Error as exc:
        raise QualityError(reasons.get(str(exc), "supervisor refused the scan")) from None
    seconds = time.monotonic() - started
    reporter.update(
        progress["files"], progress["docs"], progress["bytes"], supervisor.peak_rss, force=True
    )
    scanned_docs = sum(f.documents for f in pending)
    return {
        "scan_seconds": round(seconds, 3),
        "scanned_documents": scanned_docs,
        "scanned_file_bytes": scanned_bytes,
        "file_mb_per_s": round(scanned_bytes / 1e6 / max(seconds, 1e-9), 3),
        "documents_per_s": round(scanned_docs / max(seconds, 1e-9), 1),
        "peak_process_tree_rss_bytes": supervisor.peak_rss,
    }


def verify_report(
    manifest_path: Path,
    output: Path,
    *,
    data_root: Path | None = None,
    proof: Path | None = None,
    allow_authored_proof: bool = False,
) -> dict[str, Any]:
    """Re-derive every artifact from the committed units and compare byte-for-byte."""
    receipt_path = output / RECEIPT_FILE
    if not receipt_path.exists():
        raise QualityError("no completion receipt: the audit is incomplete (never a result)")
    receipt = json.loads(receipt_path.read_bytes())
    binding_on_disk = canonical.loads_bytes_strict((output / BINDING_FILE).read_bytes())
    if receipt.get("binding") != binding_on_disk:
        raise QualityError("receipt binding differs from the output directory binding")
    manifest, _overlay, binding, _identity = prepare(
        manifest_path,
        output,
        data_root=data_root,
        proof=proof,
        allow_authored_proof=allow_authored_proof,
        line_ceiling=int(binding_on_disk["line_ceiling"]),
    )
    if binding != binding_on_disk:
        raise QualityError("current code/policy/manifest/overlay differ from the audit binding")
    identities: list[dict[str, Any]] = []
    artifacts, result_digest = build_artifacts(
        binding, stream_units(output, manifest, str(binding["digest"]), identities)
    )
    if identities != receipt["source_files"]:
        raise QualityError("source file identities differ from the receipt")
    if result_digest != receipt["result_digest"]:
        raise QualityError("re-derived result digest differs from the receipt")
    for name, data in artifacts.items():
        if (output / name).read_bytes() != data:
            raise QualityError(f"artifact {name} differs from its re-derivation")
        expected = receipt["artifacts"][name]
        if expected != {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}:
            raise QualityError(f"artifact {name} differs from the receipt")
    return {"verified": True, "result_digest": result_digest, "artifacts": len(artifacts)}
