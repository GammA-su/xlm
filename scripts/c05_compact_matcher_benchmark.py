"""Informational authored-synthetic comparison: historical automaton vs compact matcher.

Synthetic vocabulary/items only (``syn<N>`` words), never benchmark material. Each
phase runs in its own process so its peak working set is attributable; timings are
informational and never a CI criterion. Output: one JSON object on stdout.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import psutil


def peak_rss() -> int:
    info = psutil.Process().memory_info()
    return int(getattr(info, "peak_wset", 0) or getattr(info, "rss", 0))


def generate(directory: Path, patterns: int, documents: int, seed: int) -> None:
    """Items of 40 Zipf-ish words emit 13-token windows (stride 6) plus short prompts."""
    from xlm.data.evidence_v2 import canonical

    rng = random.Random(seed)
    words = [f"syn{i}" for i in range(60_000)]
    weights = [1.0 / (rank + 10) for rank in range(len(words))]
    rows: list[list[str]] = []
    while len(rows) < patterns:
        item = rng.choices(words, weights, k=40)
        rows.extend(item[i : i + 13] for i in range(0, 28, 6))
        rows.append(item[:4])
    rows = rows[:patterns]
    with (directory / "index.jsonl").open("wb") as stream:
        for n, tokens in enumerate(rows):
            record = {"tokens": tokens, "provenance": [f"synthetic:{n}"]}
            stream.write(canonical.canonical_bytes(record) + b"\n")
    with (directory / "corpus.jsonl").open("w", encoding="utf-8") as stream:
        for _ in range(documents):
            doc = rng.choices(words, weights, k=rng.randint(200, 1200))
            if rng.random() < 0.05:
                at = rng.randint(0, len(doc))
                doc[at:at] = rng.choice(rows)
            stream.write(json.dumps(doc) + "\n")


def corpus(directory: Path) -> list[list[str]]:
    with (directory / "corpus.jsonl").open(encoding="utf-8") as stream:
        return [json.loads(line) for line in stream]


def phase(directory: Path, backend: str) -> dict[str, Any]:
    from xlm.data.exclusion import compact
    from xlm.data.exclusion.runner import file_sha, index_patterns
    from xlm.data.exclusion.streaming import StreamingMatcher

    index = directory / "index.jsonl"
    documents = corpus(directory)
    tokens = sum(len(d) for d in documents)
    base = peak_rss()
    started = time.perf_counter()
    result: dict[str, Any] = {"backend": backend, "corpus_tokens": tokens}
    if backend == "streaming":
        matcher: Any = StreamingMatcher(
            index_patterns(index, 1 << 20), max_patterns=10**9, max_nodes=10**9
        )
        result["logical_trie_nodes"] = matcher.nodes
    else:
        target = directory / f"scratch-{os.getpid()}" / compact.MATCHER_DIR
        matcher = compact.prepare(
            target,
            index,
            index_sha256=file_sha(index),
            index_bytes=index.stat().st_size,
            max_record=1 << 20,
            max_records=None,
            max_logical_nodes=None,
        )
        counts = matcher.manifest["counts"]
        compiled = sum(p.stat().st_size for p in target.iterdir())
        result.update(
            logical_trie_nodes=counts["logical_trie_nodes"],
            unique_patterns=counts["unique_patterns"],
            flattened_tokens=counts["flattened_tokens"],
            compiled_bytes=compiled,
            bytes_per_unique_pattern=round(compiled / counts["unique_patterns"], 2),
            bytes_per_flattened_token=round(compiled / counts["flattened_tokens"], 2),
            anchor_statistics=matcher.manifest["anchor_statistics"],
        )
    result["build_seconds"] = round(time.perf_counter() - started, 3)
    result["build_peak_rss_bytes"] = peak_rss()
    result["process_baseline_peak_rss_bytes"] = base
    started = time.perf_counter()
    hits = [matcher.match(d) for d in documents]
    elapsed = time.perf_counter() - started
    result.update(
        hits=sum(h is not None for h in hits),
        hit_digest=hashlib.sha256(json.dumps(hits).encode()).hexdigest(),
        match_seconds=round(elapsed, 3),
        match_tokens_per_second=round(tokens / elapsed),
        final_peak_rss_bytes=peak_rss(),
    )
    if backend == "compact":
        matcher.close()
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--patterns", type=int, default=200_000)
    parser.add_argument("--documents", type=int, default=2_000)
    parser.add_argument("--seed", type=int, default=20261003)
    parser.add_argument("--phase", choices=["streaming", "compact"])
    parser.add_argument("--directory", type=Path)
    args = parser.parse_args()
    if args.phase:
        print(json.dumps(phase(args.directory, args.phase)))
        return 0
    with tempfile.TemporaryDirectory(prefix="c05-compact-bench-") as raw:
        directory = Path(raw)
        generate(directory, args.patterns, args.documents, args.seed)
        report: dict[str, Any] = {
            "fixture": "authored-synthetic-only",
            "patterns": args.patterns,
            "documents": args.documents,
            "seed": args.seed,
            "index_bytes": (directory / "index.jsonl").stat().st_size,
            "python": sys.version.split()[0],
        }
        for backend in ("streaming", "compact"):
            completed = subprocess.run(
                [sys.executable, __file__, "--phase", backend, "--directory", str(directory)],
                check=True,
                capture_output=True,
                text=True,
            )
            report[backend] = json.loads(completed.stdout)
        report["identical_hits"] = (
            report["streaming"]["hit_digest"] == report["compact"]["hit_digest"]
        )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["identical_hits"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
