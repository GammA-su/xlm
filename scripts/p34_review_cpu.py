"""Offline lifecycle/resource and paired content-verification review probes."""

from __future__ import annotations

import gc
import json
import pickle
import statistics
import threading
import time
from pathlib import Path
from typing import Any

import psutil
from benchmark_p34 import P33, write_json

from xlm.data.sampling.prefetch import PrefetchingBatcher, ProducerSpec

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    result: dict[str, Any] = {"scope": "authored offline CPU probes; no GPU training"}
    me = psutil.Process()
    rows = []
    pids = []
    source = P33.batcher(8, 65536)
    spec, state = ProducerSpec.from_batcher(source), source.get_state()
    for iteration in range(8):
        pre = PrefetchingBatcher(spec, state, verify_content=True, timeout_seconds=10)
        assert pre.producer_pid is not None
        pids.append(pre.producer_pid)
        pre.next_step_microbatches(3 * 65536)
        expected = pre.pending_update
        assert expected is not None
        pre.rollback()
        pre.next_step_microbatches(3 * 65536)
        assert pre.pending_update is not None
        assert pre.pending_update.content_digest == expected.content_digest
        if iteration == 3:
            pre._process.kill()
            pre.restart()
            assert pre.producer_pid is not None
            pids.append(pre.producer_pid)
            pre.next_step_microbatches(3 * 65536)
            assert pre.pending_update is not None
            assert pre.pending_update.content_digest == expected.content_digest
        pre.commit()
        if iteration != 5:
            pre.close()
        del pre, expected
        gc.collect()
        assert all(not psutil.pid_exists(pid) for pid in pids)
        assert not [t for t in threading.enumerate() if t.name.startswith("xlm-prefetch-")]
        rows.append(
            {
                "iteration": iteration,
                "rss": me.memory_info().rss,
                "handles": me.num_handles(),
                "remaining_children": 0,
            }
        )
    result["lifecycle"] = rows
    pairs: dict[bool, list[float]] = {False: [], True: []}
    with PrefetchingBatcher(spec, state, timeout_seconds=10) as pre:
        budget = 30 * 65536
        for iteration in range(26):
            enabled = bool(iteration % 2)
            pre.verify_content = enabled
            # Await the actual complete frame, outside the timed trainer receive.
            # First iteration bootstraps the producer; discard both warmups.
            if iteration:
                assert pre._conn is not None
                assert pre._conn.poll(10)
            begin = time.perf_counter()
            pre.next_step_microbatches(budget)
            elapsed = time.perf_counter() - begin
            if iteration >= 2:
                pairs[enabled].append(elapsed * 1000)
            if iteration == 2:
                update = pre.pending_update
                assert update is not None
                result["payload_bytes"] = len(pickle.dumps(("update", 0, update), protocol=5))
                result["positions"] = update.input_ids.size
            pre.commit()
            budget -= 65536
        result["verify_seconds_total"] = pre.stats()["verify_content_seconds"]
    result["paired_consumer_ms"] = {str(k): v for k, v in pairs.items()}
    result["paired_difference_median_ms"] = statistics.median(
        b - a for a, b in zip(pairs[False], pairs[True], strict=True)
    )
    source.close()
    write_json(ROOT / "docs/implementation/evidence/p34/review_cpu.json", result)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
