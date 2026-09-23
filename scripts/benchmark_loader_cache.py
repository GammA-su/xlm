"""Offline repeated-window mmap experiment, with cold admission reported separately."""

from __future__ import annotations

import argparse
import cProfile
import hashlib
import json
import mmap
import struct
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch

import psutil

from xlm.data.token_cache import TokenMapCache
from xlm.data.tokens import TokenShardReader


def run(path: Path, windows: int) -> dict[str, object]:
    reader = TokenShardReader(path)
    process = psutil.Process()
    rows = []
    for mode in ("file", "mmap_per_read", "persistent"):
        with TokenMapCache() as cache:
            begin = time.perf_counter()
            if mode == "persistent":
                cache.read(reader, 0, 513)
            startup = time.perf_counter() - begin
            before = process.memory_info()
            digest = hashlib.sha256()
            start = time.perf_counter()
            cpu = time.process_time()
            counters = {"opens": 0, "maps": 0}
            original_open, original_map = Path.open, mmap.mmap

            def counted_open(
                *args: Any,
                _counts: dict[str, int] = counters,
                _open: Any = original_open,
                **kwargs: Any,
            ) -> Any:
                _counts["opens"] += 1
                return _open(*args, **kwargs)

            def counted_map(
                *args: Any,
                _counts: dict[str, int] = counters,
                _map: Any = original_map,
                **kwargs: Any,
            ) -> Any:
                _counts["maps"] += 1
                return _map(*args, **kwargs)

            with patch("mmap.mmap", counted_map):
                with patch.object(Path, "open", counted_open):
                    for i in range(windows):
                        offset = (i * 512) % (reader.manifest.num_tokens - 513)
                        if mode == "persistent":
                            ids = cache.read(reader, offset, 513)
                        elif mode == "file":
                            ids = reader.read_tokens(offset, 513)
                        else:
                            ids = reader.read_tokens_mmap(offset, 513)
                        digest.update(struct.pack("<513I", *ids))
            after = process.memory_info()
            wall = time.perf_counter() - start
            rows.append(
                dict(
                    mode=mode,
                    windows=windows,
                    wall_seconds=wall,
                    cpu_seconds=time.process_time() - cpu,
                    windows_per_second=windows / wall,
                    init_seconds=startup,
                    **counters,
                    rss_bytes=after.rss,
                    page_faults=getattr(after, "num_page_faults", 0)
                    - getattr(before, "num_page_faults", 0),
                    digest=digest.hexdigest(),
                )
            )
    assert len({r["digest"] for r in rows}) == 1
    return {
        "rows": rows,
        "instrumented": True,
        "note": "one Python counter increment per open/map; SHA digest included in timings",
    }


def mixture_run(path: Path, steps: int) -> dict[str, Any]:
    from xlm.data.sampling import MixtureBatcher, MixtureComponent, MixtureRecipe

    reader = TokenShardReader(path)
    source = reader.manifest.source_id
    recipe = MixtureRecipe(
        mixture_id="fixture", components=[MixtureComponent(source_id=source, weight=1.0)]
    )
    rows = []
    deadline = time.monotonic() + 180
    for maps in (0, 8):
        with MixtureBatcher(
            recipe,
            {source: reader},
            context_length=512,
            global_batch_valid_targets=8192,
            max_open_shards=maps,
        ) as batcher:
            begin = time.perf_counter()
            batcher._read(source, 0, 513)
            init = time.perf_counter() - begin
            digest = hashlib.sha256()
            elapsed = 0.0
            for _ in range(steps):
                if time.monotonic() > deadline:
                    raise TimeoutError("180 second mixture benchmark cap exceeded")
                start = time.perf_counter()
                batches = batcher.next_step_microbatches()
                batcher.commit()
                elapsed += time.perf_counter() - start
                for batch in batches:
                    digest.update(json.dumps(vars(batch), sort_keys=True).encode())
            digest.update(json.dumps(batcher.get_state(), sort_keys=True).encode())
            rows.append(
                {
                    "max_open_shards": maps,
                    "steps": steps,
                    "wall_seconds": elapsed,
                    "steps_per_second": steps / elapsed,
                    "init_seconds": init,
                    "digest": digest.hexdigest(),
                    "rss_bytes": psutil.Process().memory_info().rss,
                }
            )
            print(json.dumps(rows[-1]), flush=True)
    assert rows[0]["digest"] == rows[1]["digest"]
    return {
        "mixture": rows,
        "note": "batch and final cursor digests exact; digest work excluded from timing",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shard", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--windows", type=int, default=10000)
    parser.add_argument("--mixture-steps", type=int, default=0)
    parser.add_argument("--profile", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.windows <= 100000:
        parser.error("windows must be 1..100000")
    if not 0 <= args.mixture_steps <= 100:
        parser.error("mixture steps must be 0..100")
    profiler = cProfile.Profile()
    if args.profile:
        profiler.enable()
    result = (
        mixture_run(args.shard, args.mixture_steps)
        if args.mixture_steps
        else run(args.shard, args.windows)
    )
    if args.profile:
        profiler.disable()
        profiler.dump_stats(str(args.output.with_suffix(".pstats")))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
