"""Authored-synthetic C05 compact-engine benchmark (informational; never a CI gate).

Synthetic vocabulary and documents only: no benchmark material, corpus text, network
or production state is read. ``generate`` writes a deterministic corpus (realistic
document sizes, exact and near duplicates, URL lineage, parents, benchmark hits
from an authored index); ``run`` executes one full authored C05 run (compact or the
SQLite reference engine) and reports per-stage timings from the engine's own JSONL
progress stream, throughput, peak process-tree RSS and storage; ``profile`` times
per-document preparation components in one process.

Usage (uv only)::

    uv run --offline --locked --extra cpu --extra eval python scripts/c05_compact_benchmark.py \
        generate --docs 100000 --out C:/t/c05bench/corpus-100k
    uv run ... scripts/c05_compact_benchmark.py run --corpus C:/t/c05bench/corpus-100k \
        --workers 16 --work C:/t/c05bench/run-100k-w16
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np

KEY = b"authored-benchmark-key-not-a-protected-issuer"
TRUST = {"bench": KEY}
SYLLABLES = (
    "ka ri to me lo sa ne vi du pa ro ti ma le su na de fo gu bi "
    "an er in on us ar el or is et om ul ex ya zu qui sch tré ße ño"
).split()


def vocabulary(size: int, seed: int) -> list[str]:
    rng = np.random.default_rng(seed)
    words: set[str] = set()
    out: list[str] = []
    while len(out) < size:
        count = int(rng.integers(1, 5))
        word = "".join(SYLLABLES[int(i)] for i in rng.integers(0, len(SYLLABLES), count))
        if word not in words:
            words.add(word)
            out.append(word)
    return out


def zipf_sampler(size: int) -> np.ndarray:
    weights = 1.0 / (np.arange(size) + 10.0)
    cumulative = np.cumsum(weights)
    return cumulative / cumulative[-1]


def generate(
    out: Path, docs: int, seed: int, files: int | None, patterns: int, kind: str | None = None
) -> dict[str, Any]:
    from xlm.data.evidence_v2 import canonical

    rng = np.random.default_rng(seed)
    words = vocabulary(60_000, seed)
    cdf = zipf_sampler(len(words))
    punctuation = np.array(["", "", "", "", ",", ".", ";", "!", "?", ":"])
    out.mkdir(parents=True, exist_ok=False)
    (out / "data").mkdir()

    def sample(count: int) -> list[str]:
        return [words[i] for i in np.searchsorted(cdf, rng.random(count))]

    # Authored protected-pattern index: 13-token windows of synthetic items.
    index_rows = []
    for n in range(patterns):
        # ``kind``: a real provenance kind (c05-production-v3 triggers need one).
        reference = f"authored-{n}:{kind}" if kind else f"authored:{n}"
        index_rows.append({"tokens": sample(13), "provenance": [reference]})
    index = out / "index.jsonl"
    with index.open("wb") as stream:
        for row in index_rows:
            stream.write(canonical.canonical_bytes(row) + b"\n")
    file_count = files or max(1, docs // 7400)
    # Skewed file sizes: one file holds ~8% of documents, the rest share the remainder.
    shares = rng.dirichlet(np.ones(file_count) * 2.0)
    if file_count > 4:
        shares[0] = 0.08
        shares[1:] = shares[1:] / shares[1:].sum() * 0.92
    counts = np.maximum(1, np.floor(shares * docs).astype(int))
    counts[-1] += docs - counts.sum()
    recent: list[list[str]] = []
    serial = 0
    entries = []
    text_bytes_total = 0
    for f, count in enumerate(counts.tolist()):
        source = f"src{f % 7}"
        path = out / "data" / f"{f:05d}.jsonl"
        canonical_bytes = 0
        with path.open("wb") as stream:
            for _ in range(count):
                serial += 1
                kind = rng.random()
                if kind < 0.03 and recent:
                    tokens = list(recent[int(rng.integers(len(recent)))])
                elif kind < 0.08 and recent:
                    tokens = list(recent[int(rng.integers(len(recent)))])
                    for _edit in range(int(rng.integers(1, 4))):
                        tokens[int(rng.integers(len(tokens)))] = words[
                            int(rng.integers(len(words)))
                        ]
                else:
                    # Mean ~5.4 KB of text: the measured production mean (81.86 GB / 15.1M).
                    length = int(np.clip(rng.lognormal(6.24, 0.75), 20, 60_000))
                    tokens = sample(length)
                    if rng.random() < 0.005:
                        at = int(rng.integers(len(tokens)))
                        tokens[at:at] = index_rows[int(rng.integers(len(index_rows)))]["tokens"]
                if len(recent) < 2048:
                    recent.append(tokens)
                elif rng.random() < 0.05:
                    recent[int(rng.integers(len(recent)))] = tokens
                marks = punctuation[rng.integers(0, len(punctuation), len(tokens))]
                text = " ".join(
                    (t.capitalize() if i % 17 == 0 else t) + str(m)
                    for i, (t, m) in enumerate(zip(tokens, marks, strict=True))
                )
                metadata: dict[str, Any] = {}
                if rng.random() < 0.5:
                    host = int(rng.integers(0, 50_000 if rng.random() < 0.98 else 50))
                    metadata["url"] = f"https://www.site{host}.example/p/{serial % 9973}"
                parents: list[str] = []
                if rng.random() < 0.01 and serial > 10:
                    parents = [f"{source}:{int(rng.integers(1, serial)):012d}"]
                doc_id = f"{source}:{serial:012d}"
                size = len(text.encode("utf-8"))
                canonical_bytes += size
                record = {
                    "doc_id": doc_id,
                    "source_id": source,
                    "source_revision": "r" * 40,
                    "source_file": path.name,
                    "source_row": serial,
                    "raw_hash": "0" * 64,
                    "clean_hash": "0" * 64,
                    "text": text,
                    "utf8_byte_count": size,
                    "language": "en",
                    "language_confidence": 1.0,
                    "document_kind": "web",
                    "source_metadata": metadata,
                    "parent_ids": parents,
                    "license_reference": "authored",
                    "transform_log": [],
                    "quality_reasons": [],
                    "cluster_ids": {},
                    "split": "train",
                }
                stream.write(canonical.canonical_bytes(record) + b"\n")
        text_bytes_total += canonical_bytes
        entries.append(
            {
                "path": path.name,
                "source": source,
                "documents": count,
                "canonical_bytes": canonical_bytes,
                "file_bytes": path.stat().st_size,
                "sha256": _sha(path),
            }
        )
    manifest = {
        "fixture": "authored-synthetic-only",
        "seed": seed,
        "documents": docs,
        "files": entries,
        "index_sha256": _sha(index),
        "index_bytes": index.stat().st_size,
        "patterns": patterns,
        "canonical_bytes": text_bytes_total,
        "input_bytes": sum(e["file_bytes"] for e in entries),
    }
    (out / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return {k: v for k, v in manifest.items() if k != "files"} | {"file_count": len(entries)}


def _sha(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1 << 22):
            value.update(block)
    return value.hexdigest()


def build_plan(
    corpus: Path, work: Path, workers: int, ram_gib: float, policy_json: dict[str, Any] | None
) -> tuple[Any, Path, dict[str, Any]]:
    from xlm.data.exclusion.artifacts import ExecutionPlan, InputFile, output_contract, signed
    from xlm.data.exclusion.capacity import probe_geometry
    from xlm.data.exclusion.policy import ProductionPolicy, Resources, production_policy

    manifest = json.loads((corpus / "manifest.json").read_text(encoding="utf-8"))
    index = corpus / "index.jsonl"
    receipt = signed(
        {
            "index_sha256": manifest["index_sha256"],
            "index_bytes": manifest["index_bytes"],
            "isolation": {"mode": "authored"},
            "items": manifest["patterns"],
            "patterns": manifest["patterns"],
        },
        "bench",
        KEY,
    )
    files = tuple(
        InputFile(
            path=e["path"],
            source_key=e["source"],
            source_id=e["source"],
            source_revision="r" * 40,
            component=e["source"],
            view="view",
            source_file=e["path"],
            documents_sha256=e["sha256"],
            file_bytes=e["file_bytes"],
            canonical_bytes=e["canonical_bytes"],
            documents=e["documents"],
        )
        for e in manifest["files"]
    )
    docs = manifest["documents"]
    resources = Resources(
        ram_bytes=int(ram_gib * 1024**3),
        index_bytes=64 * 1024**3,
        journal_bytes=96 * 1024**3,
        scratch_bytes=200 * 1024**3,
        output_bytes=16 * 1024**3,
        decision_bytes=16 * 1024**3,
        free_bytes=64 * 1024**3,
        benchmark_bytes=max(2 * 1024**3, manifest["index_bytes"]),
        benchmark_patterns=max(2_000_000, manifest["patterns"] + 1),
        automaton_nodes=max(8_000_000, 20 * manifest["patterns"]),
        records=max(docs, 1),
        attempted_records=2 * docs + 4096,
        files=max(len(files), 1),
        comparisons=1_000_000_000,
        bytes_read=4 * manifest["input_bytes"] + 1024**3,
        stage_seconds=86400,
        overall_seconds=259200,
        workers=workers,
    )
    policy = (
        production_policy(policy_json)
        if (policy_json or {}).get("version")
        else ProductionPolicy.model_validate(policy_json or {})
    )
    plan = ExecutionPlan(
        sequence=1,
        mode="authored",
        input_manifest_digest="1" * 64,
        source_seals={f.source_key: "2" * 64 for f in files},
        files=files,
        benchmark_receipt_digest=receipt["digest"],
        index_sha256=manifest["index_sha256"],
        policy=policy,
        resources=resources,
        storage=probe_geometry(work / "scratch"),
        data_root=str((corpus / "data").resolve()),
        scratch_root=str((work / "scratch").resolve()),
        output_root=str((work / "output").resolve()),
        code_commit="3" * 40,
        code_identity="4" * 64,
        dependency_sha256="5" * 64,
        output_contract=output_contract(policy),
    )
    return plan, index, receipt


def run_once(args: argparse.Namespace) -> dict[str, Any]:
    from xlm.data.exclusion import extsort, grouping, reference, runner
    from xlm.data.exclusion.artifacts import authorize
    from xlm.data.exclusion.progress import RunProgress
    from xlm.data.exclusion.runner import run

    if args.batch_rows:
        runner.BATCH_ROWS = args.batch_rows  # type: ignore[misc]
    if args.batch_bytes:
        runner.BATCH_BYTES = args.batch_bytes  # type: ignore[misc]
    derive = grouping.MemoryPlan.derive
    if args.band_pass or args.lineage_run:

        def forced(resources: Any, documents: int) -> Any:
            plan = derive(resources, documents)
            return grouping.MemoryPlan(
                band_pass=args.band_pass or plan.band_pass,
                lineage_run_records=args.lineage_run or plan.lineage_run_records,
                near_chunk_docs=plan.near_chunk_docs,
                job_docs=plan.job_docs,
            )

        grouping.MemoryPlan.derive = staticmethod(forced)  # type: ignore[method-assign]
        original = extsort.ExternalSorter.__init__

        def unbounded(self: Any, *a: Any, **k: Any) -> None:
            k["max_runs"] = 4096
            original(self, *a, **k)

        extsort.ExternalSorter.__init__ = unbounded  # type: ignore[method-assign]
    work = Path(args.work)
    work.mkdir(parents=True, exist_ok=getattr(args, "regroup", False))
    plan, index, receipt = build_plan(
        Path(args.corpus), work, args.workers, args.ram_gib, json.loads(args.policy or "{}")
    )
    if getattr(args, "regroup", False):
        rewind_to_group(plan)
    stream = io.StringIO()
    progress = RunProgress(interval=args.interval, fmt="jsonl", stream=stream)
    sampler = MemorySampler()
    sampler.start()
    started = time.perf_counter()
    cpu_started = time.process_time()
    kwargs: dict[str, Any] = {
        "index": index,
        "benchmark": receipt,
        "trusted": TRUST,
        "issuer": "bench",
        "key": KEY,
        "current_code": "4" * 64,
        "current_dependencies": "5" * 64,
    }
    if args.engine == "reference":
        result = reference.run(plan, authorize(plan, "bench", KEY), **kwargs)
    else:
        result = run(plan, authorize(plan, "bench", KEY), progress=progress, **kwargs)
    wall = time.perf_counter() - started
    sampler.stop()
    payload = result["payload"]
    stages: dict[str, float] = {}
    for line in stream.getvalue().splitlines():
        event = json.loads(line)
        if event["stage"]:
            stages[event["stage"]] = round(event["stage_seconds"], 3)
    (work / "progress.jsonl").write_text(stream.getvalue(), encoding="utf-8")
    scratch = work / "scratch" / plan.identity()
    facts = scratch / "facts"
    manifest = json.loads((Path(args.corpus) / "manifest.json").read_text(encoding="utf-8"))
    report = {
        "fixture": "authored-synthetic-only",
        "engine": args.engine,
        "workers": args.workers,
        "ram_gib": args.ram_gib,
        "batch_rows": runner.BATCH_ROWS,
        "batch_bytes": runner.BATCH_BYTES,
        "memory_plan": grouping.MemoryPlan.derive(plan.resources, payload["documents"]).__dict__,
        "documents": payload["documents"],
        "input_bytes": manifest["input_bytes"],
        "wall_seconds": round(wall, 2),
        "parent_cpu_seconds": round(time.process_time() - cpu_started, 2),
        "docs_per_second": round(payload["documents"] / wall, 1),
        "stage_seconds": stages,
        "peak_rss_sampled": payload["peak_rss_sampled"],
        "memory_samples": sampler.report(),
        "peak_scratch_sampled": payload["peak_scratch_sampled"],
        "facts_bytes": sum(p.stat().st_size for p in facts.rglob("*") if p.is_file())
        if facts.exists()
        else None,
        "facts_sqlite_bytes": (scratch / "facts.sqlite").stat().st_size
        if (scratch / "facts.sqlite").exists()
        else None,
        "membership_sha256": payload["membership_sha256"],
        "membership_bytes": payload["membership_bytes"],
        "kept": payload["kept"],
        "excluded": payload["excluded"],
        "duplicates": payload["duplicates"],
        "dedup_stats": payload["dedup_stats"],
        "decisions_sha256": _sha(scratch / "decisions.jsonl"),
    }
    seal = scratch / "seal.json"
    if seal.exists():
        from xlm.data.evidence_v2 import canonical

        report["group_digest"] = canonical.loads_bytes_strict(seal.read_bytes())["payload"][
            "groups"
        ]
    scan = stages.get("SCAN")
    if scan:
        report["scan_docs_per_second"] = round(payload["documents"] / scan, 1)
        report["scan_mib_per_second"] = round(manifest["input_bytes"] / scan / 1024**2, 1)
    return report


class MemorySampler:
    """Benchmark-only: process-tree RSS (the gate's measure) vs USS (private bytes).

    On Windows RSS is the working set, which includes shared mapped pages (the
    compiled matcher, fact units) once per process that touched them; USS counts
    only pages private to each process, so the difference exposes double counting.
    """

    def __init__(self, interval: float = 3.0) -> None:
        import threading

        self.interval = interval
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.peak: dict[str, int] = {"rss_tree": 0, "uss_tree": 0, "parent_rss": 0}
        self.worker_peak: dict[str, int] = {"rss": 0, "uss": 0}
        self.samples = 0

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        self.thread.join(30)

    def _loop(self) -> None:
        import psutil

        me = psutil.Process()
        while not self.stop_event.wait(self.interval):
            rss = uss = 0
            try:
                parent = me.memory_full_info()
                rss, uss = parent.rss, parent.uss
                self.peak["parent_rss"] = max(self.peak["parent_rss"], parent.rss)
                for child in me.children(recursive=True):
                    try:
                        info = child.memory_full_info()
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        continue
                    rss += info.rss
                    uss += info.uss
                    self.worker_peak["rss"] = max(self.worker_peak["rss"], info.rss)
                    self.worker_peak["uss"] = max(self.worker_peak["uss"], info.uss)
            except psutil.Error:
                continue
            self.samples += 1
            self.peak["rss_tree"] = max(self.peak["rss_tree"], rss)
            self.peak["uss_tree"] = max(self.peak["uss_tree"], uss)

    def report(self) -> dict[str, Any]:
        return {
            "samples": self.samples,
            **{f"peak_{k}_bytes": v for k, v in self.peak.items()},
            "peak_single_process_rss_bytes": self.worker_peak["rss"],
            "peak_single_process_uss_bytes": self.worker_peak["uss"],
        }


def rewind_to_group(plan: Any) -> None:
    """Authored benchmark only: re-sign a finished run's state back to ``group``.

    Fact units are kept (and re-verified by the run); the group directory, seal,
    decisions and publication are removed so only grouping and publication rerun.
    """
    import shutil

    from xlm.data.evidence_v2 import canonical
    from xlm.data.exclusion.artifacts import signed, verify_signed

    scratch = Path(plan.scratch_root) / plan.identity()
    state_path = scratch / "state.json"
    body = verify_signed(canonical.loads_bytes_strict(state_path.read_bytes()), TRUST)
    body.update(stage="group", stage_started=time.time())
    canonical.write_atomic(state_path, canonical.canonical_bytes(signed(body, "bench", KEY)))
    for name in ("group",):
        if (scratch / name).exists():
            shutil.rmtree(scratch / name)
    for name in ("seal.json", "decisions.jsonl"):
        (scratch / name).unlink(missing_ok=True)
    final = Path(plan.output_root) / plan.identity()
    if final.exists():
        shutil.rmtree(final)


def profile(args: argparse.Namespace) -> dict[str, Any]:
    """Single-process per-document component timings (ms/doc) on the first N documents."""
    import hashlib as h

    from xlm.core.contracts import CanonicalDocument
    from xlm.data.dedup import minhash
    from xlm.data.dedup.lineage import lineage_keys_v3
    from xlm.data.dedup.matchview import match_normalize
    from xlm.data.evidence_v2 import canonical
    from xlm.data.exclusion import compact
    from xlm.data.exclusion.policy import ProductionPolicy
    from xlm.data.exclusion.scanprep import shingle_hashes

    corpus = Path(args.corpus)
    manifest = json.loads((corpus / "manifest.json").read_text(encoding="utf-8"))
    lines: list[bytes] = []
    for entry in manifest["files"]:
        with (corpus / "data" / entry["path"]).open("rb") as stream:
            for raw in stream:
                lines.append(raw)
                if len(lines) >= args.docs:
                    break
        if len(lines) >= args.docs:
            break
    work = Path(args.work)
    matcher = compact.prepare(
        work / "matcher",
        corpus / "index.jsonl",
        index_sha256=manifest["index_sha256"],
        index_bytes=manifest["index_bytes"],
        max_record=1 << 26,
        max_records=None,
        max_logical_nodes=None,
    )
    hasher = minhash.MinHasher(ProductionPolicy().minhash())
    clock = time.perf_counter
    t = dict.fromkeys(
        (
            "parse",
            "normalize",
            "matcher",
            "shingle_hash",
            "minhash_fast",
            "minhash_v1",
            "band_keys",
            "lineage",
            "digests",
        ),
        0.0,
    )
    for raw in lines:
        a = clock()
        doc = CanonicalDocument(**canonical.loads_bytes_strict(raw))
        b = clock()
        normalized = match_normalize(doc.text)
        tokens = normalized.split(" ") if normalized else []
        c = clock()
        matcher.match(tokens)
        d = clock()
        hashes = shingle_hashes(normalized, 5)
        e = clock()
        signature = minhash.signature_from_array(hashes, hasher._params)
        f = clock()
        if args.v1:
            minhash._signature_vectorized_v1(set(hashes.tolist()), hasher._params)
        g = clock()
        hasher.band_keys(signature)
        i = clock()
        lineage_keys_v3(doc)
        j = clock()
        canonical.digest(doc.to_dict())
        h.sha256(normalized.encode()).digest()
        k = clock()
        for name, value in (
            ("parse", b - a),
            ("normalize", c - b),
            ("matcher", d - c),
            ("shingle_hash", e - d),
            ("minhash_fast", f - e),
            ("minhash_v1", g - f),
            ("band_keys", i - g),
            ("lineage", j - i),
            ("digests", k - j),
        ):
            t[name] += value
    matcher.close()
    count = len(lines)
    report = {k: round(v / count * 1000, 4) for k, v in t.items()}
    report["docs"] = count
    report["mean_line_bytes"] = round(sum(len(x) for x in lines) / count)
    report["total_ms_per_doc_fast"] = round(
        sum(v for k, v in report.items() if k not in ("minhash_v1", "docs", "mean_line_bytes")), 4
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    gen = sub.add_parser("generate")
    gen.add_argument("--docs", type=int, required=True)
    gen.add_argument("--out", type=Path, required=True)
    gen.add_argument("--seed", type=int, default=20261003)
    gen.add_argument("--files", type=int)
    gen.add_argument("--patterns", type=int, default=200_000)
    gen.add_argument("--provenance-kind", choices=["prompt", "sentence", "answer", "combined"])
    runner = sub.add_parser("run")
    runner.add_argument("--corpus", type=Path, required=True)
    runner.add_argument("--work", type=Path, required=True)
    runner.add_argument("--workers", type=int, default=16)
    runner.add_argument("--ram-gib", type=float, default=48.0)
    runner.add_argument("--engine", choices=["compact", "reference"], default="compact")
    runner.add_argument("--interval", type=float, default=2.0)
    runner.add_argument("--policy")
    # Authored-benchmark knobs only (performance shape; never production CLI options).
    runner.add_argument("--batch-rows", type=int)
    runner.add_argument("--batch-bytes", type=int)
    runner.add_argument("--band-pass", type=int)
    runner.add_argument("--lineage-run", type=int)
    runner.add_argument("--regroup", action="store_true")
    prof = sub.add_parser("profile")
    prof.add_argument("--corpus", type=Path, required=True)
    prof.add_argument("--work", type=Path, required=True)
    prof.add_argument("--docs", type=int, default=3000)
    prof.add_argument("--v1", action="store_true")
    args = parser.parse_args()
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    if args.command == "generate":
        started = time.perf_counter()
        report = generate(
            args.out, args.docs, args.seed, args.files, args.patterns, args.provenance_kind
        )
        report["generate_seconds"] = round(time.perf_counter() - started, 1)
    elif args.command == "run":
        report = run_once(args)
    else:
        report = profile(args)
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
