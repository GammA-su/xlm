"""Bounded, content-free throughput probe of the exact count backend on a real-text sample.

Read-only. A small sample of complete lines is read from the start of plan files (two
per component, bytes proportional to the component's physical size); only the parsed
``text`` values are kept in memory and nothing derived from them except sizes,
timings and SHA-256 digests of count vectors is printed or written. Nothing is
written next to the corpus, the plan or the tokenizer.

``single`` times one thread of :meth:`count_valid_targets` (and the per-row reference
checks) with the stock BPE word cache and with larger caches, and requires identical
count vectors. ``pool`` starts N worker processes that each load the tokenizer, wait on
a barrier and count an equal-byte shard; aggregate throughput is total bytes over the
makespan. ``disk`` times full-file SHA-256 reads with 1 and k concurrent readers over
distinct plan files (OS cache state is reported as a caveat, not controlled).

Every mode has a wall-time budget; ``--seconds`` bounds the sampled work.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import multiprocessing as mp
import os
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

from xlm.data.evidence_v2 import canonical

MiB = 1024**2


def _plan(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    return value.get("payload", value)


def sample_lines(plan_path: Path, total_bytes: int, per_component_files: int = 2) -> list[bytes]:
    """Complete lines from the start of the largest files of every component."""
    plan = _plan(plan_path)
    root = Path(plan["data_root"])
    by_component: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in plan["files"]:
        by_component[item["component"]].append(item)
    physical = {k: sum(f["file_bytes"] for f in v) for k, v in by_component.items()}
    everything = sum(physical.values())
    lines: list[bytes] = []
    for component in sorted(by_component):
        budget = max(1 * MiB, total_bytes * physical[component] // everything)
        chosen = sorted(by_component[component], key=lambda f: -f["file_bytes"])
        chosen = chosen[:per_component_files]
        for item in chosen:
            share, taken = budget // len(chosen), 0
            with (root / item["path"]).open("rb") as stream:
                while taken < share and (raw := stream.readline(64 * MiB)):
                    if raw.endswith(b"\n"):
                        lines.append(raw)
                        taken += len(raw)
    return lines


def load_tokenizer(directory: Path, cache: int | None) -> Any:
    """The verified loader; ``cache`` rebuilds only the BPE model with another word cache."""
    from xlm.data.exclusion.selection import load_tokenizer as load

    tokenizer = load(directory)
    if cache is not None:
        from xlm.tokenizers.bpe import with_bpe_cache

        with_bpe_cache(tokenizer, cache)
    return tokenizer


def _digest(counts: list[int]) -> str:
    return hashlib.sha256(json.dumps(counts).encode()).hexdigest()


def _texts(lines: list[bytes]) -> list[str]:
    return [canonical.loads_bytes_strict(line)["text"] for line in lines]


def single(args: argparse.Namespace) -> dict[str, Any]:
    os.environ["TOKENIZERS_PARALLELISM"] = "false"
    os.environ["RAYON_NUM_THREADS"] = "1"
    from xlm.core.contracts import CanonicalDocument

    lines = sample_lines(args.plan, args.sample_mib * MiB)
    texts = _texts(lines)
    text_bytes = sum(len(t.encode("utf-8")) for t in texts)
    out: dict[str, Any] = {
        "mode": "single",
        "rows": len(lines),
        "line_bytes": sum(map(len, lines)),
        "text_bytes": text_bytes,
    }
    # Reference per-row checks other than tokenization (parse, document, content digest).
    began = time.perf_counter()
    for line in lines:
        value = canonical.loads_bytes_strict(line)
        document = CanonicalDocument(**value)
        hashlib.sha256(canonical.canonical_bytes(document.to_dict())).digest()
    out["verify_s"] = round(time.perf_counter() - began, 3)
    results: dict[str, Any] = {}
    reference: str | None = None
    for cache in [None, *args.caches]:
        tokenizer = load_tokenizer(args.tokenizer, cache)
        began = time.perf_counter()
        counts: list[int] = []
        batch: list[str] = []
        size = 0
        for text in texts:
            batch.append(text)
            size += len(text)
            if size >= 4 * MiB:
                counts += tokenizer.count_valid_targets(batch)
                batch, size = [], 0
        counts += tokenizer.count_valid_targets(batch)
        seconds = time.perf_counter() - began
        digest = _digest(counts)
        reference = reference or digest
        results[str(cache or "stock")] = {
            "seconds": round(seconds, 3),
            "text_mib_s": round(text_bytes / MiB / seconds, 3),
            "tokens": sum(counts),
            "counts_sha256": digest,
            "identical_to_stock": digest == reference,
        }
    out["count"] = results
    out["tokens_per_text_byte"] = round(results["stock"]["tokens"] / text_bytes, 4)
    return out


def _pool_worker(
    number: int, shard: list[str], tokenizer: str, cache: int | None, barrier: Any, queue: Any
) -> None:
    os.environ["TOKENIZERS_PARALLELISM"] = "false"
    os.environ["RAYON_NUM_THREADS"] = "1"
    counter = load_tokenizer(Path(tokenizer), cache)
    barrier.wait()
    began = time.perf_counter()
    cpu = time.process_time()
    counts: list[int] = []
    for start in range(0, len(shard), 64):
        counts += counter.count_valid_targets(shard[start : start + 64])
    size = sum(len(t.encode("utf-8")) for t in shard)
    queue.put((number, time.perf_counter() - began, time.process_time() - cpu, counts, size))


def pool(args: argparse.Namespace) -> dict[str, Any]:
    lines = sample_lines(args.plan, args.sample_mib * MiB)
    texts = _texts(lines)
    del lines
    sizes = [len(t.encode("utf-8")) for t in texts]
    total = sum(sizes)
    out: dict[str, Any] = {"mode": "pool", "rows": len(texts), "text_bytes": total, "runs": {}}
    context = mp.get_context("spawn")
    for workers in args.workers:
        for cache in [None, *args.caches]:
            # Equal-byte contiguous shards in row order.
            shards: list[list[str]] = [[] for _ in range(workers)]
            acc, target = 0, total / workers
            for text, size in zip(texts, sizes, strict=True):
                shards[min(workers - 1, int(acc // target))].append(text)
                acc += size
            barrier, queue = context.Barrier(workers + 1), context.Queue()
            procs = [
                context.Process(
                    target=_pool_worker, args=(n, s, str(args.tokenizer), cache, barrier, queue)
                )
                for n, s in enumerate(shards)
            ]
            for p in procs:
                p.start()
            barrier.wait(timeout=120)
            began = time.perf_counter()
            results = sorted(queue.get(timeout=180) for _ in procs)
            makespan = time.perf_counter() - began
            for p in procs:
                p.join(timeout=60)
            worker_cpu = sum(r[2] for r in results)
            out["runs"][f"w{workers}-{cache or 'stock'}"] = {
                "workers": workers,
                "cache": cache or "stock",
                "makespan_s": round(makespan, 3),
                "text_mib_s": round(total / MiB / makespan, 2),
                "tokens": sum(sum(r[3]) for r in results),
                "worker_cpu_s": round(worker_cpu, 2),
                "cpu_cores_used": round(worker_cpu / makespan, 2),
                # Busy-time rate of each worker: its shard's text bytes over its own time.
                "per_worker_mib_s_min": round(min(r[4] / MiB / r[1] for r in results), 2),
                "per_worker_mib_s_max": round(max(r[4] / MiB / r[1] for r in results), 2),
                "cpu_text_mib_per_core_s": round(total / MiB / worker_cpu, 3),
            }
            print(json.dumps(out["runs"][f"w{workers}-{cache or 'stock'}"]), file=sys.stderr)
    return out


def _hash_file(path: str, limit: int) -> tuple[int, float]:
    began = time.perf_counter()
    digest, done = hashlib.sha256(), 0
    with open(path, "rb") as stream:
        while done < limit and (block := stream.read(8 * MiB)):
            digest.update(block)
            done += len(block)
    return done, time.perf_counter() - began


def disk(args: argparse.Namespace) -> dict[str, Any]:
    """Full-speed sequential reads, 1 reader vs k concurrent readers, distinct files."""
    from concurrent.futures import ThreadPoolExecutor

    plan = _plan(args.plan)
    root = Path(plan["data_root"])
    # Mid-sized files, skipping those the sample modes read first (largest per component).
    files = sorted(plan["files"], key=lambda f: f["file_bytes"])
    files = [f for f in files if 200 * MiB <= f["file_bytes"] <= 700 * MiB]
    cursor = int(args.disk_offset)
    out: dict[str, Any] = {"mode": "disk", "runs": {}}
    for readers in args.readers:
        chosen = files[cursor : cursor + readers]
        cursor += readers
        per = args.disk_mib * MiB // readers
        began = time.perf_counter()
        with ThreadPoolExecutor(readers) as pool_:
            paths = [str(root / f["path"]) for f in chosen]
            done = list(pool_.map(_hash_file, paths, [per] * len(paths)))
        seconds = time.perf_counter() - began
        out["runs"][f"r{readers}"] = {
            "readers": readers,
            "bytes": sum(d[0] for d in done),
            "seconds": round(seconds, 3),
            "mib_s": round(sum(d[0] for d in done) / MiB / seconds, 1),
        }
    out["caveat"] = "OS file cache not controlled; distinct files per run"
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["single", "pool", "disk"])
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--tokenizer", type=Path, required=True)
    parser.add_argument("--sample-mib", type=int, default=32)
    parser.add_argument("--caches", type=int, nargs="*", default=[])
    parser.add_argument("--workers", type=int, nargs="*", default=[8, 12, 16])
    parser.add_argument("--readers", type=int, nargs="*", default=[1, 4, 16])
    parser.add_argument("--disk-mib", type=int, default=1536)
    parser.add_argument("--disk-offset", type=int, default=0)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    result = {"single": single, "pool": pool, "disk": disk}[args.mode](args)
    text = json.dumps(result, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes((text + "\n").encode("utf-8"))  # LF on every platform
    print(text)
    return 0


if __name__ == "__main__":
    mp.freeze_support()
    raise SystemExit(main())
