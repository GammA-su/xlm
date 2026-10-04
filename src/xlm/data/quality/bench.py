"""Bounded, authored-only throughput benchmark for the quality audit.

``python -m xlm.data.quality benchmark --scratch DIR`` generates a deterministic
AUTHORED corpus (no real corpus text, no network) inside a NEW scratch directory,
runs the production :func:`~xlm.data.quality.runner.run_audit` path on it for a set
of worker counts, measures throughput and process-tree CPU, checks that every run
produced byte-identical scientific artifacts, and projects the full-corpus runtime.

The generator is the original ``scripts/quality_audit_benchmark.py`` mix (70 % prose
plus code, HTML, tables, OCR-like, web boilerplate, loops; lognormal sizes; about 5.7 KB
rows), moved here unchanged so earlier measurements stay comparable; the benchmark
corpus only varies the file sizes (8-104 MiB) so files end inside chunks as in the real
manifest, and is large enough for every worker to process several chunks. It is a
workload model, not a sample of the corpus.

Rates: ``mb_per_s`` is scanned bytes over the whole scan phase (process spawn and the
last chunk's tail included); ``steady_mb_per_s`` is scanned bytes over the span from
the first chunk a worker started to the last it finished. Projections use the steady
rate for the scan plus measured fixed costs.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import time
import types
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from xlm.core.contracts import CanonicalDocument
from xlm.data.evidence_v2 import canonical

MIB = 1024**2
GIB = 1024**3
# The real C05 input the projection is made for (operator-supplied manifest totals).
REAL_FILES = 2035
REAL_DOCUMENTS = 15_097_174
REAL_FILE_BYTES = 104_506_534_003
REAL_CANONICAL_BYTES = 81_859_652_239
MAX_BENCH_BYTES = 4 * GIB
DEFAULT_BENCH_WORKERS = (1, 2, 4, 8, 12, 16)
SMALL_WORKERS = (1, 2)  # measured on the small subset so the benchmark stays bounded
SMALL_CORPUS_BYTES = 96 * MIB
# Authored file sizes cycle through this pattern (mean 56 MiB; the real mean is 51 MB).
FILE_PATTERN_MIB = (8, 24, 40, 56, 72, 88, 104)
MEMBERSHIP_BENCH_ROWS = 100_000

FILES = 8
SEED = 20261004


def _vocabulary(rng: np.random.Generator) -> list[str]:
    letters = np.array(list("etaoinshrdlucmfwypvbgkjqxz"))
    weights = np.linspace(2.0, 0.1, letters.size)
    weights /= weights.sum()
    words = []
    for _ in range(20_000):
        size = int(rng.integers(2, 11))
        words.append("".join(rng.choice(letters, size=size, p=weights)))
    return words


def _sentences(rng: np.random.Generator, words: list[str], count: int) -> list[str]:
    out = []
    for _ in range(count):
        picks = rng.integers(0, len(words), size=int(rng.integers(6, 24)))
        sentence = " ".join(words[i] for i in picks)
        out.append(sentence[0].upper() + sentence[1:] + "...?,"[int(rng.integers(0, 5))])
    return out


def _document(kind: str, rng: np.random.Generator, sentences: list[str], size: int) -> str:
    def prose(target: int) -> str:
        parts: list[str] = []
        length = 0
        while length < target:
            paragraph = " ".join(
                sentences[i] for i in rng.integers(0, len(sentences), size=int(rng.integers(3, 9)))
            )
            parts.append(paragraph)
            length += len(paragraph) + 2
        return "\n\n".join(parts)

    if kind == "prose":
        return prose(size)
    if kind == "code":
        body = []
        for n in range(max(size // 40, 3)):
            body.append(f"    value_{n} = compute(value_{n - 1}, {n});")
        return "int main() {\n" + "\n".join(body) + "\n    return 0;\n}\n"
    if kind == "html":
        cells = "".join(
            f"<li><a href='/p/{n}'>{s[:30]}</a></li>"
            for n, s in enumerate(
                sentences[i] for i in rng.integers(0, len(sentences), size=max(size // 80, 2))
            )
        )
        head = "<!DOCTYPE html><html><head><title>t</title></head>"
        return f"{head}<body><ul>{cells}</ul></body></html>"
    if kind == "table":
        rows = [f"| {n} | {n * 3.5:.1f} | {n % 7} |" for n in range(max(size // 20, 2))]
        return "| a | b | c |\n|---|---|---|\n" + "\n".join(rows)
    if kind == "ocr":
        text = prose(size)
        lines = [text[i : i + 60] for i in range(0, len(text), 60)]
        out = []
        for n, line in enumerate(lines):
            out.append(line + ("-" if n % 7 == 0 else ""))
            if n % 40 == 39:
                out += ["", f"Journal of Synthetic Studies {n // 40}", str(n // 40 + 1), ""]
        return "\n".join(out)
    if kind == "web":
        return (
            "Home\nAbout us\nAccept all cookies\nPrivacy Policy\n"
            + prose(size)
            + "\n© 2024 Synthetic. All rights reserved.\nFollow us on Twitter\n"
        )
    if kind == "loop":
        unit = sentences[int(rng.integers(0, len(sentences)))] + " "
        return unit * max(size // len(unit), 2)
    return sentences[int(rng.integers(0, len(sentences)))][:12]


KINDS = (
    ("prose", 0.70),
    ("code", 0.07),
    ("html", 0.05),
    ("table", 0.04),
    ("ocr", 0.06),
    ("web", 0.05),
    ("loop", 0.02),
    ("tiny", 0.01),
)


def generate(root: Path, target_bytes: int, file_sizes: Sequence[int] | None = None) -> Path:
    """The authored corpus (``FILES`` equal files by default, exactly as the original
    ``scripts/quality_audit_benchmark.py`` generated it; or the given file sizes)."""
    if not 0 < target_bytes <= MAX_BENCH_BYTES:
        raise ValueError("benchmark corpus size outside (0, 4 GiB]")
    rng = np.random.default_rng(SEED)
    words = _vocabulary(rng)
    sentences = _sentences(rng, words, 5000)
    names = [k for k, _ in KINDS]
    probabilities = np.array([p for _, p in KINDS])
    data = root / "data"
    files = []
    sizes = list(file_sizes) if file_sizes is not None else [target_bytes // FILES] * FILES
    for f, per_file in enumerate(sizes):
        path = data / "canonical" / f"c{f % 4}" / "v" / f"f{f}" / "documents.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        written = rows = text_bytes = 0
        digest = hashlib.sha256()
        with path.open("wb") as stream:
            while written < per_file:
                kind = str(rng.choice(names, p=probabilities))
                size = int(min(rng.lognormal(mean=8.1, sigma=0.9), 400_000))
                text = _document(kind, rng, sentences, size)
                rows += 1
                encoded = len(text.encode("utf-8"))
                row = CanonicalDocument(
                    doc_id=f"bench:{f}:{rows}",
                    source_id="authored-benchmark",
                    source_revision="authored",
                    source_file=f"f{f}",
                    source_row=rows,
                    raw_hash="0" * 64,
                    clean_hash="0" * 64,
                    text=text,
                    utf8_byte_count=encoded,
                    language="en",
                    language_confidence=1.0,
                    document_kind="prose",
                    source_metadata={"mix01_component": f"c{f % 4}", "fasttext_english": 0.9},
                    parent_ids=[],
                    license_reference="authored",
                    transform_log=[{"transform": "nfc_and_newline_normalize", "version": "1.0"}],
                    quality_reasons=[],
                    cluster_ids={},
                    split="train",
                ).to_dict()
                line = canonical.canonical_bytes(row) + b"\n"
                stream.write(line)
                digest.update(line)
                written += len(line)
                text_bytes += encoded
        files.append(
            {
                "path": path.relative_to(data).as_posix(),
                "source_key": f"c{f % 4}",
                "component": f"c{f % 4}",
                "view": "v",
                "upstream_component": None,
                "documents_sha256": digest.hexdigest(),
                "file_bytes": written,
                "canonical_bytes": text_bytes,
                "documents": rows,
            }
        )
    manifest: dict[str, Any] = {
        "kind": "authored_c05_input",
        "data_root": str(data.resolve()),
        "files": files,
    }
    manifest["digest"] = canonical.self_digest(manifest)
    target = root / "manifest.json"
    canonical.write_canonical_json(target, manifest)
    return target


def build_corpus(root: Path, total_bytes: int) -> Path:
    """Benchmark corpus: variable file sizes (``FILE_PATTERN_MIB``, mean 56 MiB; the real
    mean is 51 MB) so files end inside chunks, as in the real manifest."""
    sizes: list[int] = []
    while sum(sizes) < total_bytes:
        pattern = FILE_PATTERN_MIB[len(sizes) % len(FILE_PATTERN_MIB)] * MIB
        sizes.append(min(pattern, total_bytes - sum(sizes)))
    return generate(root, total_bytes, sizes)


def subset_manifest(manifest: Path, minimum_bytes: int) -> Path:
    """``manifest-small.json``: the leading files totalling at least ``minimum_bytes``."""
    body = canonical.loads_strict(manifest.read_text(encoding="utf-8"))
    files: list[dict[str, Any]] = []
    for entry in body["files"]:
        files.append(entry)
        if sum(f["file_bytes"] for f in files) >= minimum_bytes:
            break
    small: dict[str, Any] = {k: v for k, v in body.items() if k not in ("files", "digest")}
    small["files"] = files
    small["digest"] = canonical.self_digest(small)
    target = manifest.with_name("manifest-small.json")
    canonical.write_canonical_json(target, small)
    return target


# -- measurements ----------------------------------------------------------------------------


@dataclass
class RunMeasure:
    workers: int
    corpus: str
    seconds: float
    scan_seconds: float
    file_bytes: int
    mb_per_s: float
    parent_cpu_seconds: float
    parent_cores: float
    child_cpu_seconds: float
    cpu_utilization: float
    steady_mb_per_s: float
    peak_rss_bytes: int
    peak_in_flight: int
    queue_tasks: int
    worker_processes_used: int
    max_concurrent_tasks: int
    worker_busy_fraction: float | None
    phase_seconds: dict[str, float]
    result_digest: str
    artifacts_sha256: str

    def to_json(self) -> dict[str, Any]:
        return dict(self.__dict__)


def _artifacts_digest(output: Path) -> str:
    from xlm.data.quality.report import ARTIFACTS

    digest = hashlib.sha256()
    for name in sorted(ARTIFACTS):
        digest.update(name.encode() + b"\0" + (output / name).read_bytes())
    return digest.hexdigest()


def measure_run(
    manifest: Path,
    output: Path,
    workers: int,
    *,
    max_rss_gib: float = 12.0,
    progress_interval: float | None = None,
) -> RunMeasure:
    """One production ``run_audit`` with parent and worker CPU accounting."""
    import psutil

    from xlm.data.quality.envelope import QUEUE_FACTOR
    from xlm.data.quality.runner import Limits, run_audit

    me = psutil.Process()
    before = me.cpu_times()
    started = time.monotonic()
    result = run_audit(
        manifest,
        output,
        limits=Limits(
            workers=workers,
            max_rss_bytes=int(max_rss_gib * GIB),
            free_reserve_bytes=0,
            max_output_bytes=4 * GIB,
            line_ceiling=64 * MIB,
            deadline_seconds=3600.0,
        ),
        progress_interval=progress_interval,
        started=started,
    )
    seconds = time.monotonic() - started
    after = me.cpu_times()
    parent = (after.user - before.user) + (after.system - before.system)
    scan, activity = result["scan"], result["activity"]
    # Worker CPU is measured inside each worker per chunk (portable; on Windows psutil
    # does not report reaped children). For workers=1 the work is in-process.
    child = 0.0 if workers == 1 else float(activity["worker_cpu_seconds"])
    nbytes = int(scan["scanned_file_bytes"])
    cpus = os.cpu_count() or 1
    scan_seconds = float(scan["scan_seconds"])
    return RunMeasure(
        workers=workers,
        corpus=manifest.name,
        seconds=round(seconds, 3),
        scan_seconds=scan_seconds,
        file_bytes=nbytes,
        mb_per_s=round(nbytes / 1e6 / max(scan_seconds, 1e-9), 2),
        steady_mb_per_s=round(nbytes / 1e6 / max(float(activity["worker_span_seconds"]), 1e-9), 2),
        parent_cpu_seconds=round(parent, 2),
        parent_cores=round(parent / max(seconds, 1e-9), 2),
        child_cpu_seconds=round(child, 2),
        cpu_utilization=round((parent + child) / max(seconds, 1e-9) / cpus, 3),
        peak_rss_bytes=int(scan["peak_process_tree_rss_bytes"]),
        peak_in_flight=int(scan["peak_tasks_in_flight"]),
        queue_tasks=QUEUE_FACTOR * workers if workers > 1 else 1,
        worker_processes_used=int(activity["worker_processes_used"]),
        max_concurrent_tasks=int(activity["max_concurrent_tasks"]),
        worker_busy_fraction=activity["worker_busy_fraction"],
        phase_seconds=dict(result["phase_seconds"]),
        result_digest=str(result["result_digest"]),
        artifacts_sha256=_artifacts_digest(output),
    )


def membership_rate(rows: int = MEMBERSHIP_BENCH_ROWS) -> float:
    """Authored C05 kept-membership rows parsed per second by the production parser."""
    from xlm.data.quality.overlay import MembershipParser

    files = {
        f"canonical/c{i}/v/documents.jsonl": types.SimpleNamespace(
            documents=rows // 8 + 1,
            component=f"bench{i}",
            view="v",
            upstream_component=None,
            source_id=f"bench-source-{i}",
        )
        for i in range(8)
    }
    paths = sorted(files)
    lines = []
    for n in range(rows):
        path = paths[n % 8]
        item = files[path]
        lines.append(
            canonical.canonical_bytes(
                {
                    "bytes": 600 + n % 50,
                    "component": item.component,
                    "content": hashlib.sha256(n.to_bytes(8, "little")).hexdigest(),
                    "decision": "kept",
                    "doc_id": f"{n:064x}",
                    "duplicate_group": f"{n:064x}",
                    "file": path,
                    "lineage_group": f"{n:064x}",
                    "quick": False,
                    "row": n // 8 + 1,
                    "source_id": item.source_id,
                    "split": "train",
                    "upstream_component": None,
                    "view": "v",
                }
            )
        )
    parser = MembershipParser(files, {p: f.documents for p, f in files.items()}, rows)
    started = time.perf_counter()
    for line in lines:
        parser.stage(line)
    return rows / (time.perf_counter() - started)


def projection(
    scan_mb_per_s: float,
    *,
    membership_rows_per_s: float | None,
    kept_rows: int,
    aggregate_seconds: float,
    startup_seconds: float,
) -> dict[str, Any]:
    """Real-corpus wall-time projection (seconds) from measured authored-data rates.

    likely = the measured steady scan rate; optimistic = +10 %; conservative = -25 %
    (real text differs from authored text; thermals, antivirus, page-cache misses).
    Fixed costs: kept-membership parsing (``kept_rows`` at the measured rate), unit
    aggregation (measured per-unit cost x 2,035 files), pool start-up and publication.
    """
    overlay = kept_rows / membership_rows_per_s if membership_rows_per_s else 0.0
    fixed = overlay + aggregate_seconds + startup_seconds + 10.0
    out: dict[str, Any] = {
        "overlay_seconds": round(overlay, 1),
        "aggregate_seconds": round(aggregate_seconds, 1),
        "fixed_seconds": round(fixed, 1),
    }
    for name, factor in (("optimistic", 1.10), ("likely", 1.0), ("conservative", 0.75)):
        scan = REAL_FILE_BYTES / 1e6 / max(scan_mb_per_s * factor, 1e-9)
        out[name] = {
            "scan_mb_per_s": round(scan_mb_per_s * factor, 1),
            "scan_seconds": round(scan, 1),
            "total_seconds": round(scan + fixed, 1),
            "total_minutes": round((scan + fixed) / 60, 1),
        }
    return out


def aggregate_cost(runs: Sequence[RunMeasure], manifests: dict[str, Path]) -> float:
    """Projected aggregation seconds for ``REAL_FILES`` units: intercept plus per-unit
    slope from the two corpus sizes measured (the larger value of either if only one)."""
    points: dict[int, float] = {}
    for run in runs:
        body = canonical.loads_strict(manifests[run.corpus].read_text(encoding="utf-8"))
        units = len(body["files"])
        seconds = run.phase_seconds.get("aggregate", 0.0)
        points[units] = max(points.get(units, 0.0), seconds)
    if len(points) >= 2:
        (u1, t1), (u2, t2) = sorted(points.items())[0], sorted(points.items())[-1]
        slope = max((t2 - t1) / (u2 - u1), 0.0)
        return max(t1 - slope * u1, 0.0) + slope * REAL_FILES
    ((units, seconds),) = points.items()
    return seconds / max(units, 1) * REAL_FILES  # pessimistic: all cost treated as per unit


def run_benchmark(
    scratch: Path,
    *,
    corpus_mib: int,
    workers: Sequence[int] | None,
    budget_seconds: float,
    keep: bool,
    log: Callable[[str], None],
    kept_rows: int = REAL_DOCUMENTS,
) -> dict[str, Any]:
    """Generate the authored corpus, run each worker count, compare, project."""
    if not 64 <= corpus_mib <= MAX_BENCH_BYTES // MIB:
        raise ValueError("--corpus-mib outside [64, 4096]")
    if not 30 <= budget_seconds <= 3600:
        raise ValueError("--budget-seconds outside [30, 3600]")
    counts = tuple(workers or DEFAULT_BENCH_WORKERS)
    if scratch.exists() and (not scratch.is_dir() or any(scratch.iterdir())):
        raise ValueError("benchmark scratch directory must be absent or empty")
    scratch.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    try:
        log(f"generating a {corpus_mib} MiB AUTHORED corpus (no real data is read)")
        manifest = build_corpus(scratch / "corpus", corpus_mib * MIB)
        small = subset_manifest(manifest, SMALL_CORPUS_BYTES)
        log(f"corpus ready in {time.monotonic() - started:.1f}s")
        rate = membership_rate()
        log(f"C05 kept-membership parse: {rate:,.0f} rows/s (authored rows)")
        runs: list[RunMeasure] = []
        skipped: list[int] = []
        for count in counts:
            target = small if count in SMALL_WORKERS else manifest
            body = canonical.loads_strict(target.read_text(encoding="utf-8"))
            target_bytes = sum(f["file_bytes"] for f in body["files"])
            elapsed = time.monotonic() - started
            per_worker = max((r.steady_mb_per_s / r.workers for r in runs), default=None)
            if per_worker is not None:
                parallel = min(count, os.cpu_count() or 1)
                expected = target_bytes / 1e6 / (per_worker * parallel) + 10.0
                if elapsed + expected > budget_seconds:
                    skipped.append(count)
                    log(f"workers {count}: skipped (would exceed the {budget_seconds:.0f}s budget)")
                    continue
            output = scratch / f"out-w{count}"
            measure = measure_run(target, output, count)
            runs.append(measure)
            log(
                f"workers {count:>2}: steady {measure.steady_mb_per_s:6.1f} MB/s (whole scan "
                f"{measure.mb_per_s:.1f}) | busy {(measure.worker_busy_fraction or 0) * 100:3.0f}%"
                f" | CPU {measure.cpu_utilization * 100:3.0f}% | parent "
                f"{measure.parent_cores:.2f} cores | {measure.worker_processes_used} procs, "
                f"{measure.max_concurrent_tasks} concurrent | RSS "
                f"{measure.peak_rss_bytes / GIB:.2f} GiB"
            )
            shutil.rmtree(output, ignore_errors=True)
        if not runs:
            raise ValueError("no benchmark run fitted the budget")
        identical = all(
            len({(r.result_digest, r.artifacts_sha256) for r in runs if r.corpus == name}) == 1
            for name in {r.corpus for r in runs}
        )
        best_run = max(runs, key=lambda r: r.steady_mb_per_s)
        recommended = min(
            (r for r in runs if r.steady_mb_per_s >= 0.97 * best_run.steady_mb_per_s),
            key=lambda r: r.workers,
        )
        single = next((r for r in runs if r.workers == 1), None)
        aggregate = aggregate_cost(runs, {manifest.name: manifest, small.name: small})
        startup = recommended.scan_seconds - recommended.file_bytes / 1e6 / max(
            recommended.steady_mb_per_s, 1e-9
        )
        projected = projection(
            recommended.steady_mb_per_s,
            membership_rows_per_s=rate,
            kept_rows=kept_rows,
            aggregate_seconds=aggregate,
            startup_seconds=max(startup, 0.0),
        )
        result = {
            "label": "AUTHORED SYNTHETIC CORPUS MEASUREMENT + PROJECTION (not a real-corpus run)",
            "cpu_count": os.cpu_count(),
            "corpus_bytes": {r.corpus: r.file_bytes for r in runs},
            "runs": [r.to_json() for r in runs],
            "scaling_vs_1_worker": {
                str(r.workers): round(r.steady_mb_per_s / single.steady_mb_per_s, 2) for r in runs
            }
            if single
            else None,
            "skipped_workers": skipped,
            "artifacts_identical_across_workers": identical,
            "best": {"workers": best_run.workers, "steady_mb_per_s": best_run.steady_mb_per_s},
            "recommended_workers": recommended.workers,
            "membership_rows_per_s": round(rate, 1),
            "projection_104_5GB": projected,
            "projection_assumptions": (
                f"scan: measured steady authored-data rate x {REAL_FILE_BYTES:,} bytes; "
                f"overlay: {kept_rows:,} kept rows (upper bound: every document) at the "
                f"measured parse rate; aggregation: measured per-unit cost x {REAL_FILES} "
                "files; real text, disks and antivirus may differ"
            ),
            "benchmark_seconds": round(time.monotonic() - started, 1),
        }
        log(
            f"best: {best_run.workers} workers {best_run.steady_mb_per_s:.1f} MB/s; recommended"
            f" --workers {recommended.workers}; projected 104.5 GB audit: "
            + ", ".join(
                f"{k} {projected[k]['total_minutes']} min"
                for k in ("optimistic", "likely", "conservative")
            )
            + ("" if identical else "; WARNING: artifacts differed across worker counts")
        )
        return result
    finally:
        if not keep:
            shutil.rmtree(scratch, ignore_errors=True)
