"""Authored BPE-stage benchmark at the production fit-sample size (bounded, generated).

Generates heavy-tailed pseudo-natural text (syllable-built vocabulary of millions of
word types, Zipf-like frequencies, capitalization, numbers, punctuation, newlines),
spools it in the exact fit frame format, and times the real C06 BPE child
(``fitfast_child``: tokenizers 0.23.2 ``train_from_iterator``, vocab 32,768) under
explicit thread settings. Measures wall time, process-tree peak RSS and whether
``tokenizer.json`` is byte-identical across thread settings. Synthetic text is not
real web/PDF text; merge cost on the real sample is NOT measured here.

    python -m scripts.c06_bpe_benchmark --root <new dir> --output <json>
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

import numpy as np
import psutil

from xlm.data.evidence_v2 import canonical
from xlm.data.exclusion import fitfast

FRAME = 8


def vocabulary(rng: np.random.Generator, size: int) -> list[str]:
    letters = list("abcdefghijklmnopqrstuvwxyz") + ["é", "ü", "ß", "ç"]
    syllables = list(
        {"".join(rng.choice(letters, size=int(n))) for n in rng.integers(1, 4, size=6_000)}
    )
    counts = rng.integers(1, 5, size=size)
    picks = rng.integers(0, len(syllables), size=int(counts.sum()))
    words: list[str] = []
    position = 0
    for count in counts.tolist():
        words.append("".join(syllables[i] for i in picks[position : position + count].tolist()))
        position += count
    return words


def spool(path: Path, megabytes: int, words: list[str], seed: int) -> dict[str, Any]:
    rng = np.random.default_rng(seed)
    target = megabytes * 2**20
    size = documents = 0
    novel = 0
    seen_types: set[int] = set()
    digest = hashlib.sha256()
    with path.open("wb") as stream:
        while size < target:
            n = int(rng.integers(300, 1500))
            ranks = rng.zipf(1.07, size=n) - 1
            overflow = ranks >= len(words)
            ranks[overflow] = rng.integers(0, len(words), size=int(overflow.sum()))
            tokens = []
            for rank, roll in zip(ranks.tolist(), rng.random(n).tolist(), strict=True):
                word = words[rank]
                seen_types.add(rank)
                if roll < 0.10:
                    word = word.capitalize()
                elif roll < 0.13:
                    word = str(int(roll * 1e7))
                elif roll < 0.135:
                    word = f"{word}{int(roll * 1e9):x}"  # Novel token (ids, typos, urls).
                    novel += 1
                tokens.append(word)
                if roll > 0.92:
                    tokens.append(",.;:!?()\n"[int(roll * 1000) % 9])
            raw = " ".join(tokens).encode("utf-8")
            stream.write(len(raw).to_bytes(FRAME, "little"))
            stream.write(raw)
            if documents:
                digest.update(b"\n")
            digest.update(f"bench:{documents}".encode())
            size += len(raw)
            documents += 1
    return {
        "megabytes": megabytes,
        "documents": documents,
        "spool_bytes": size,
        "word_types_used": len(seen_types),
        "novel_tokens": novel,
        "training_input_hash": digest.hexdigest(),
    }


def tree_rss(process: psutil.Process) -> int:
    total = 0
    for member in [process, *process.children(recursive=True)]:
        try:
            total += member.memory_info().rss
        except psutil.Error:
            continue
    return total


def fit(root: Path, corpus: Path, facts: dict[str, Any], threads: int) -> dict[str, Any]:
    out = root / f"out-{facts['megabytes']}-{threads}"
    shutil.rmtree(out, ignore_errors=True)
    job = root / f"job-{facts['megabytes']}-{threads}.json"
    job.write_text(
        json.dumps(
            {
                "spool": str(corpus),
                "output": str(out),
                "target_vocab_size": 32768,
                "training_input_hash": facts["training_input_hash"],
                "documents": facts["documents"],
                "spool_bytes": facts["spool_bytes"],
                "production": False,
            }
        ),
        encoding="utf-8",
    )
    started = time.perf_counter()
    child = subprocess.Popen(fitfast.bpe_command(job), env=fitfast.bpe_environment(threads))
    watcher = psutil.Process(child.pid)
    peak = 0
    while child.poll() is None:
        try:
            peak = max(peak, tree_rss(watcher))
        except psutil.Error:
            pass
        time.sleep(0.1)
    wall = time.perf_counter() - started
    if child.returncode != 0:
        raise RuntimeError("bpe child failed")
    model = json.loads((out / "tokenizer.json").read_bytes())["model"]
    return {
        "megabytes": facts["megabytes"],
        "threads": threads,
        "seconds": round(wall, 1),
        "peak_tree_rss_gib": round(peak / 2**30, 2),
        "vocab": len(model["vocab"]),
        "merges": len(model["merges"]),
        "tokenizer_json_sha256": hashlib.sha256((out / "tokenizer.json").read_bytes()).hexdigest(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--sizes", type=int, nargs="+", default=[128, 512])
    parser.add_argument("--threads", type=int, nargs="+", default=[16, 8])
    args = parser.parse_args()
    args.root.mkdir(parents=True, exist_ok=False)
    result: dict[str, Any] = {"corpora": [], "fits": []}
    try:
        started = time.perf_counter()
        words = vocabulary(np.random.default_rng(11), 3_000_000)
        result["vocabulary_seconds"] = round(time.perf_counter() - started, 1)
        for megabytes in args.sizes:
            corpus = args.root / f"corpus-{megabytes}.spool"
            started = time.perf_counter()
            facts = spool(corpus, megabytes, words, megabytes)
            facts["generation_seconds"] = round(time.perf_counter() - started, 1)
            result["corpora"].append(facts)
            print(json.dumps(facts), flush=True)
            for threads in args.threads:
                result["fits"].append(fit(args.root, corpus, facts, threads))
                print(json.dumps(result["fits"][-1]), flush=True)
            corpus.unlink()
        by_size: dict[int, set[str]] = {}
        for row in result["fits"]:
            by_size.setdefault(row["megabytes"], set()).add(row["tokenizer_json_sha256"])
        result["identical_across_threads"] = {str(k): len(v) == 1 for k, v in by_size.items()}
    finally:
        shutil.rmtree(args.root, ignore_errors=True)
    canonical.write_canonical_json(args.output, result)
    print(json.dumps(result["identical_across_threads"]))


if __name__ == "__main__":
    main()
