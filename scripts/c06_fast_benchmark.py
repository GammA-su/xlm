"""Bounded authored benchmark of the C06 fast path, plus a labelled production projection.

Everything is generated (no corpus, proof or network). Generated files are read back
from the OS file cache, so source/membership figures measure CPU throughput; the
production SATA read floor is applied separately in the projection. The projection is
arithmetic on these measurements, NOT a measurement of the real run.

    python -m scripts.c06_fast_benchmark --root <new scratch dir> --output <summary.json>
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import numpy as np
import psutil

from xlm.core.contracts import CanonicalDocument
from xlm.data.evidence_v2 import canonical
from xlm.data.exclusion import fitfast
from xlm.data.exclusion.fitscan import (
    FileRef,
    MembershipTables,
    OrderedPool,
    SourceTask,
    parse_membership_chunk,
    scan_source_file,
)
from xlm.data.exclusion.keptindex import ROW_DTYPE, _section_sha, _write_section
from xlm.data.exclusion.tokenizer_fit import RANK_TAG, Budget, load_fit_policy
from xlm.tokenizers.bpe import _read_frames, fit_input_line, write_fit_frame

# Actual production scale supplied by the operator (C05 p0002).
PRODUCTION = {
    "membership_bytes": 6_360_000_000,
    "membership_rows": 12_632_103,
    "source_bytes": 104_506_534_003,
    "source_rows": 15_097_174,
    "kept_rows": 12_632_103,
    "fit_sample_bytes": 536_870_912,
    "sata_read_bytes_per_s": (500_000_000, 550_000_000),
}
WORDS = 0


class Peak:
    """Process-tree peak RSS sampler for one benchmark segment."""

    def __init__(self) -> None:
        self.peak = 0
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)

    def sample(self) -> int:
        me = psutil.Process()
        total = me.memory_info().rss
        for child in me.children(recursive=True):
            try:
                total += child.memory_info().rss
            except psutil.Error:
                continue
        return int(total)

    def run(self) -> None:
        while not self.stop.wait(0.05):
            self.peak = max(self.peak, self.sample())

    def __enter__(self) -> Peak:
        self.thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.stop.set()
        self.thread.join()


def timed(function: Callable[[], Any]) -> tuple[float, int, float, Any]:
    cpu0 = psutil.Process().cpu_times()
    started = time.perf_counter()
    with Peak() as peak:
        result = function()
    wall = time.perf_counter() - started
    cpu1 = psutil.Process().cpu_times()
    return wall, peak.peak, (cpu1.user - cpu0.user) + (cpu1.system - cpu0.system), result


def vocabulary(rng: random.Random, size: int) -> list[str]:
    letters = "abcdefghijklmnopqrstuvwxyzéüßçø"
    return [
        "".join(rng.choice(letters) for _ in range(max(1, int(rng.expovariate(1 / 6)))))
        for _ in range(size)
    ]


def zipf_text(rng: random.Random, words: list[str], count: int) -> str:
    out = []
    for _ in range(count):
        index = min(len(words) - 1, int(rng.paretovariate(1.1)) - 1)
        word = words[index * 7919 % len(words)]
        out.append(word)
        if rng.random() < 0.08:
            out.append(rng.choice([",", ".", "\n", ";", " (", ")", " 42", " 2025"]))
    return " ".join(out)


def record(i: int, text: str) -> CanonicalDocument:
    metadata = {
        "eai_taxonomy": {
            f"k{j}": {"primary": {"code": str(j), "label": "label" + str(j)}} for j in range(6)
        },
        "quality_signals": {"fasttext": {"english": 0.93}},
        "url": f"https://example.org/{i}",
    }
    return CanonicalDocument(
        hashlib.sha256(f"bench-{i}".encode()).hexdigest(),
        "essential_web",
        "rev",
        "f.parquet",
        i,
        "a" * 64,
        "b" * 64,
        text,
        len(text.encode()),
        "en",
        0.98,
        "web",
        metadata,
        [],
        "odc-by",
        [{"step": "normalize"}],
        [],
        {},
        "train",
    )


# -- fixtures ---------------------------------------------------------------------------------


def make_sources(root: Path, files: int, rows: int) -> list[dict[str, Any]]:
    rng = random.Random(1)
    words = vocabulary(rng, 50_000)
    texts = [zipf_text(rng, words, 800) for _ in range(400)]
    entries = []
    number = 0
    for ordinal in range(files):
        path = root / f"source-{ordinal:03d}.jsonl"
        digest = hashlib.sha256()
        kept_rows, ids, sizes, selected, contents = [], [], [], [], []
        with path.open("wb") as stream:
            for row in range(1, rows + 1):
                doc = record(number, texts[number % len(texts)] + f" {number}")
                line = canonical.canonical_bytes(doc.to_dict()) + b"\n"
                stream.write(line)
                digest.update(line)
                if number % 6:  # ~83% kept rows (production: 83.7%)
                    kept_rows.append(row)
                    ids.append(doc.doc_id.encode())
                    sizes.append(doc.utf8_byte_count)
                    chosen = number % 150 == 1  # ~0.67% selected
                    selected.append(chosen)
                    if chosen:
                        contents.append(canonical.digest(doc.to_dict()))
                number += 1
        entries.append(
            {
                "path": str(path),
                "sha256": digest.hexdigest(),
                "bytes": path.stat().st_size,
                "rows": rows,
                "kept_rows": kept_rows,
                "ids": ids,
                "sizes": sizes,
                "selected": selected,
                "contents": contents,
            }
        )
    return entries


def source_tasks(entries: list[dict[str, Any]], parse: bool) -> Iterator[SourceTask]:
    for ordinal, entry in enumerate(entries):
        yield SourceTask(
            ordinal=ordinal,
            path=entry["path"],
            sha256=entry["sha256"],
            file_bytes=entry["bytes"],
            documents=entry["rows"],
            line_ceiling=64 * 1024**2,
            rows=np.asarray(entry["kept_rows"] if parse else [], dtype=np.uint32),
            ids=entry["ids"] if parse else [],
            nbytes=np.asarray(entry["sizes"] if parse else [], dtype=np.uint64),
            assigned=np.zeros(len(entry["kept_rows"]) if parse else 0, dtype=np.uint8),
            selected=np.asarray(entry["selected"] if parse else [], dtype=np.bool_),
            selected_content=entry["contents"] if parse else [],
            parse=parse,
        )


def make_membership(root: Path, rows: int, files: int) -> tuple[Path, MembershipTables]:
    rng = random.Random(2)
    keys = [canonical.canonical_bytes([f"c{n}", f"c{n}", None]).decode() for n in range(17)]
    per_file = -(-rows // files)
    refs = {
        f"canonical/f{n:04d}.jsonl": FileRef(
            n, n % 17, f"c{n % 17}", f"c{n % 17}", None, "s", per_file
        )
        for n in range(files)
    }
    names = list(refs)
    ids = sorted(f"{rng.getrandbits(128):032x}{n:08d}" for n in range(rows))
    path = root / "membership.jsonl"
    with path.open("wb") as stream:
        for n, doc_id in enumerate(ids):
            name = names[n % files]
            ref = refs[name]
            stream.write(
                canonical.canonical_bytes(
                    {
                        "doc_id": doc_id,
                        "source_id": "s",
                        "component": ref.component,
                        "view": ref.view,
                        "file": name,
                        "row": n // files + 1,
                        "content": f"{rng.getrandbits(256):064x}",
                        "bytes": rng.randint(200, 20_000),
                        "duplicate_group": doc_id,
                        "lineage_group": doc_id,
                        "decision": "kept",
                        "split": "train" if n % 400 else "diagnostic_val",
                        "quick": False,
                        "upstream_component": None,
                    }
                )
                + b"\n"
            )
    tables = MembershipTables(
        files=refs,
        allocation_keys=tuple(keys),
        seed=20260919,
        fit_document_cap=1024**2,
        line_ceiling=64 * 1024**2,
        rank_tag=RANK_TAG,
    )
    return path, tables


# -- benchmarks -------------------------------------------------------------------------------


def bench_membership(path: Path, tables: MembershipTables, workers: int) -> dict[str, Any]:
    size = path.stat().st_size

    def run() -> Any:
        state: dict[str, Any] = {"sha": hashlib.sha256(), "bytes": 0}
        with OrderedPool(workers, tables) as pool:
            parts = list(
                pool.map(
                    parse_membership_chunk,
                    fitfast._membership_blocks(path, size, tables.line_ceiling, state),
                )
            )
        return sum(p.rows for p in parts), parts

    wall, peak, cpu, (rows, parts) = timed(run)
    return {
        "workers": workers,
        "seconds": round(wall, 3),
        "rows_per_s": round(rows / wall),
        "mb_per_s": round(size / 1e6 / wall, 1),
        "peak_tree_rss_mib": round(peak / 2**20),
        "parent_cpu_s": round(cpu, 2),
        "rows": rows,
        "_parts": parts,
    }


def bench_selection(parts: list[Any], tables: MembershipTables) -> dict[str, Any]:
    from fractions import Fraction

    rows = sum(p.rows for p in parts)
    lengths = np.concatenate([p.id_lengths for p in parts])
    offsets = np.zeros(rows + 1, dtype=np.uint64)
    np.cumsum(lengths, out=offsets[1:])
    m = fitfast.Membership(
        rows=rows,
        ids=b"".join(p.ids for p in parts),
        id_offsets=offsets,
        file=np.concatenate([p.file for p in parts]),
        row=np.concatenate([p.row for p in parts]),
        nbytes=np.concatenate([p.nbytes for p in parts]),
        content=np.concatenate([p.content for p in parts]),
        split=np.concatenate([p.split for p in parts]),
        allocation=np.concatenate([p.allocation for p in parts]),
        rank=np.concatenate([p.rank for p in parts]),
    )
    policy = load_fit_policy(Path("recipes/tokenizer/mix01_fit_shares_v1.yaml"))[0]
    keys = list(tables.allocation_keys)
    budgets = {k: Budget("c", 1, 1, 536_870_912 // 17, Fraction(1, 17)) for k in keys}
    wall, peak, _, (states, selected) = timed(
        lambda: fitfast.select_exact(m, policy, budgets, keys)
    )
    return {
        "rows": rows,
        "seconds": round(wall, 3),
        "selected": int(selected.sum()),
        "peak_tree_rss_mib": round(peak / 2**20),
    }


def bench_source(entries: list[dict[str, Any]], workers: int, parse: bool) -> dict[str, Any]:
    size = sum(e["bytes"] for e in entries)
    rows = sum(e["rows"] for e in entries)
    tables = MembershipTables({}, (), 0, 0, 64 * 1024**2, RANK_TAG)

    def run() -> Any:
        parsed = selected = 0
        with OrderedPool(workers, tables) as pool:
            for result in pool.map(scan_source_file, source_tasks(entries, parse)):
                parsed += result.parsed
                selected += len(result.selected)
        return parsed, selected

    wall, peak, cpu, (parsed, selected) = timed(run)
    return {
        "workers": workers,
        "parse": parse,
        "seconds": round(wall, 3),
        "gb_per_s": round(size / 1e9 / wall, 3),
        "rows_per_s": round(rows / wall),
        "split_checks_per_s": round(parsed / wall),
        "selected_parsed": selected,
        "peak_tree_rss_mib": round(peak / 2**20),
        "parent_cpu_s": round(cpu, 2),
    }


def bpe_corpus(root: Path, megabytes: int) -> tuple[Path, str, int, int]:
    rng = random.Random(3)
    words = vocabulary(rng, 400_000)
    spool = root / f"bpe-{megabytes}.spool"
    digest = hashlib.sha256()
    size = documents = 0
    with spool.open("wb") as stream:
        while size < megabytes * 2**20:
            doc = record(documents, zipf_text(rng, words, 900))
            size += write_fit_frame(stream, doc)
            if documents:
                digest.update(b"\n")
            digest.update(fit_input_line(doc))
            documents += 1
    return spool, digest.hexdigest(), documents, size


def bench_feed(spool: Path) -> dict[str, Any]:
    def run() -> int:
        count = 0
        with spool.open("rb") as stream:
            for _ in _read_frames(stream):
                count += 1
        return count

    wall, _, _, count = timed(run)
    return {
        "documents": count,
        "seconds": round(wall, 3),
        "mb_per_s": round(spool.stat().st_size / 1e6 / wall, 1),
    }


def bench_bpe(
    root: Path, spool: Path, digest: str, documents: int, size: int, threads: int, vocab: int
) -> dict[str, Any]:
    out = root / f"bpe-out-{threads}"
    shutil.rmtree(out, ignore_errors=True)
    job = root / f"bpe-job-{threads}.json"
    job.write_text(
        json.dumps(
            {
                "spool": str(spool),
                "output": str(out),
                "target_vocab_size": vocab,
                "training_input_hash": digest,
                "documents": documents,
                "spool_bytes": size,
                "production": False,
            }
        ),
        encoding="utf-8",
    )
    started = time.perf_counter()
    child = subprocess.Popen(fitfast.bpe_command(job), env=fitfast.bpe_environment(threads))
    peak = 0
    watcher = psutil.Process(child.pid)  # The venv launcher; the interpreter is its child.
    while child.poll() is None:
        try:
            members = [watcher, *watcher.children(recursive=True)]
            peak = max(peak, sum(m.memory_info().rss for m in members))
        except psutil.Error:
            pass
        time.sleep(0.05)
    wall = time.perf_counter() - started
    if child.returncode != 0:
        raise RuntimeError("bpe child failed")
    return {
        "threads": threads,
        "seconds": round(wall, 2),
        "peak_child_rss_mib": round(peak / 2**20),
        "tokenizer_json_sha256": hashlib.sha256((out / "tokenizer.json").read_bytes()).hexdigest(),
    }


def bench_index(root: Path, rows: int) -> dict[str, Any]:
    data = np.zeros(rows, dtype=ROW_DTYPE)
    data["row"] = np.arange(rows)
    ids = b"".join(f"{n:064x}".encode() for n in range(rows))
    directory = root / "index"
    shutil.rmtree(directory, ignore_errors=True)
    directory.mkdir()

    def write() -> int:
        _write_section(directory / "rows.bin", memoryview(data).cast("B"))
        _write_section(directory / "ids.bin", ids)
        return int(data.nbytes + len(ids))

    write_wall, _, _, written = timed(write)

    def read() -> int:
        return _section_sha(directory / "rows.bin")[0] + _section_sha(directory / "ids.bin")[0]

    read_wall, _, _, read_bytes = timed(read)
    return {
        "rows": rows,
        "write_mb_per_s": round(written / 1e6 / write_wall, 1),
        "verify_read_mb_per_s": round(read_bytes / 1e6 / read_wall, 1),
    }


def projection(results: dict[str, Any]) -> dict[str, Any]:
    p = PRODUCTION
    membership_rate = max(r["mb_per_s"] for r in results["membership"]) * 1e6
    source_cpu = {r["workers"]: r["gb_per_s"] * 1e9 for r in results["source"] if r["parse"]}
    best_workers = max(source_cpu, key=lambda w: source_cpu[w])
    lo_io, hi_io = p["sata_read_bytes_per_s"]
    membership_s = max(p["membership_bytes"] / membership_rate, p["membership_bytes"] / hi_io)
    source_cpu_s = p["source_bytes"] / source_cpu[best_workers]
    source_io = (p["source_bytes"] / hi_io, p["source_bytes"] / lo_io)
    source_s = (max(source_cpu_s, source_io[0]), max(source_cpu_s, source_io[1]))
    bpe = {r["threads"]: r["seconds"] for r in results["bpe"]}
    best_threads = min(bpe, key=lambda t: bpe[t])
    scale = p["fit_sample_bytes"] / (results["bpe_sample_mib"] * 2**20)
    bpe_linear = bpe[best_threads] * scale
    selection_s = (
        results["selection"]["seconds"] * p["membership_rows"] / results["selection"]["rows"]
    )
    index_s = (p["kept_rows"] * (ROW_DTYPE.itemsize + 76)) / (
        results["index"]["write_mb_per_s"] * 1e6
    )
    fixed = 15.0  # spawn, policy/plan checks, sample export, tokenizer save/verify (model)
    pre_bpe = (
        membership_s + selection_s + source_s[0] + index_s + fixed,
        membership_s + selection_s + source_s[1] + index_s + fixed,
    )
    return {
        "label": "PROJECTION from authored measurements; not a measurement of the real run",
        "inputs": p,
        "membership_seconds": round(membership_s, 1),
        "selection_seconds": round(selection_s, 1),
        "source_seconds_range": [round(s, 1) for s in source_s],
        "source_cpu_seconds_at_best_workers": round(source_cpu_s, 1),
        "source_workers": best_workers,
        "index_write_seconds": round(index_s, 1),
        "pre_bpe_seconds_range": [round(s, 1) for s in pre_bpe],
        "bpe_threads": best_threads,
        "bpe_seconds_linear_scaling_model": round(bpe_linear, 1),
        "bpe_basis": (
            f"{results['bpe_sample_mib']} MiB authored Zipf text x{scale:.1f} linear scaling; "
            "real-text merge cost is not measured and may differ"
        ),
        "total_seconds_model_range": [
            round(pre_bpe[0] + bpe_linear, 1),
            round(pre_bpe[1] + bpe_linear, 1),
        ],
        "slo_seconds": 1200,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-files", type=int, default=24)
    parser.add_argument("--rows-per-file", type=int, default=9_000)
    parser.add_argument("--membership-rows", type=int, default=1_500_000)
    parser.add_argument("--bpe-mib", type=int, default=64)
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args()
    args.root.mkdir(parents=True, exist_ok=False)
    results: dict[str, Any] = {"cpu": os.cpu_count(), "python": sys.version.split()[0]}
    try:
        started = time.perf_counter()
        entries = make_sources(args.root, args.source_files, args.rows_per_file)
        results["source_fixture"] = {
            "files": len(entries),
            "rows": sum(e["rows"] for e in entries),
            "bytes": sum(e["bytes"] for e in entries),
            "kept_rows": sum(len(e["kept_rows"]) for e in entries),
            "selected_rows": sum(len(e["contents"]) for e in entries),
            "generation_seconds": round(time.perf_counter() - started, 1),
        }
        print(json.dumps(results["source_fixture"]), flush=True)
        results["source"] = [bench_source(entries, 1, False), bench_source(entries, 8, False)]
        for workers in (1, 2, 4, 8, 16):
            results["source"].append(bench_source(entries, workers, True))
            print(json.dumps(results["source"][-1]), flush=True)
        path, tables = make_membership(args.root, args.membership_rows, 2000)
        results["membership_fixture"] = {"rows": args.membership_rows, "bytes": path.stat().st_size}
        results["membership"] = []
        parts: list[Any] = []
        for workers in (1, 2, 4, 8):
            row = bench_membership(path, tables, workers)
            parts = row.pop("_parts")
            results["membership"].append(row)
            print(json.dumps(row), flush=True)
        results["selection"] = bench_selection(parts, tables)
        del parts
        print(json.dumps(results["selection"]), flush=True)
        spool, digest, documents, size = bpe_corpus(args.root, args.bpe_mib)
        results["bpe_sample_mib"] = args.bpe_mib
        results["bpe_feed_overhead"] = bench_feed(spool)
        results["bpe"] = []
        for threads in (8, 16):
            results["bpe"].append(
                bench_bpe(args.root, spool, digest, documents, size, threads, 32768)
            )
            print(json.dumps(results["bpe"][-1]), flush=True)
        results["bpe_identical_across_threads"] = (
            len({r["tokenizer_json_sha256"] for r in results["bpe"]}) == 1
        )
        results["index"] = bench_index(args.root, 1_000_000)
        results["projection"] = projection(results)
    finally:
        if not args.keep:
            shutil.rmtree(args.root, ignore_errors=True)
    canonical.write_canonical_json(args.output, results)
    print(json.dumps(results["projection"], indent=2))


if __name__ == "__main__":
    main()
