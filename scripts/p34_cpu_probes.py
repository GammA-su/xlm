"""CPU-only P34 probes on the frozen P33 fixture (no CUDA).

decompose: per-stage loader time and an exact replay split of the trace chain,
           for the P33 loader and the current loader.
ipc:       transport cost of one real B8 65,536-target update between spawned
           processes, for full TrainingBatch pickles, compact arrays and a
           shared-memory slot, plus exact provenance-list reconstruction cost.
ceiling:   updates/s one producer process sustains with an idle consumer.
Refuses to run beside another XLM Python process (CPU contention).
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import multiprocessing
import pickle
import statistics
import subprocess
import sys
import time
from multiprocessing import shared_memory
from pathlib import Path
from typing import Any

import numpy as np
import torch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import benchmark_p34 as bench  # noqa: E402

from xlm.data.sampling.prefetch import PrefetchingBatcher, ProducerSpec  # noqa: E402
from xlm.data.sampling.trace import target_trace_digest  # noqa: E402

UPDATES = 10


def timed(store: dict[str, float], name: str, function: Any) -> Any:
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        begin = time.perf_counter()
        try:
            return function(*args, **kwargs)
        finally:
            store[name] = store.get(name, 0.0) + time.perf_counter() - begin

    return wrapped


def decompose(loader: str) -> dict[str, Any]:
    """Uninstrumented totals first, then coarse per-window stage wrappers."""
    plain = bench.make_batcher(loader, 8, 65536)
    totals = []
    for _ in range(UPDATES):
        begin = time.perf_counter()
        plain.next_step_microbatches()
        plain.commit()
        totals.append(time.perf_counter() - begin)
    plain.close()
    source = bench.make_batcher(loader, 8, 65536)
    stages: dict[str, float] = {}
    source._read = timed(stages, "mmap_token_read", source._read)
    source._document_at = timed(stages, "document_index_lookup", source._document_at)
    source._next_window = timed(stages, "window_selection_total", source._next_window)
    source._pack_window = timed(stages, "packing_and_provenance_lists", source._pack_window)
    source._to_microbatches = timed(
        stages, "microbatch_tensors_and_metadata", source._to_microbatches
    )
    source.scheduler.select_source = timed(
        stages, "mixture_scheduling", source.scheduler.select_source
    )
    source.scheduler.record_exposure = timed(
        stages, "exposure_accounting", source.scheduler.record_exposure
    )
    source.commit = timed(stages, "commit_state_copy", source.commit)
    rows = []
    for _ in range(UPDATES):
        stages.clear()
        begin = time.perf_counter()
        batches = source.next_step_microbatches()
        stages["next_step_total"] = time.perf_counter() - begin
        begin = time.perf_counter()
        for batch in batches:  # the trainer's list-to-tensor attention mask
            torch.tensor(batch.metadata["input_attention_mask"], dtype=torch.bool)
        stages["trainer_attention_mask_from_lists"] = time.perf_counter() - begin
        source.commit()
        rows.append(dict(stages))
    source.close()
    records = capture_traces(loader)
    steady = rows[2:]
    return {
        "uninstrumented_ms": {
            "median": statistics.median(totals[2:]) * 1000,
            "all": [t * 1000 for t in totals],
        },
        "stage_ms_median": {k: statistics.median(r[k] for r in steady) * 1000 for k in steady[0]},
        "trace_replay": replay(records),
    }


def capture_traces(loader: str) -> list[tuple[str, dict[str, Any]]]:
    del loader  # the replay input is loader-independent: both chains are identical
    """The exact per-target (previous, trace) pairs of update 3, via the P33 loop."""
    reference = bench.p33_stream()
    with bench.P33.batcher(8, 65536) as described:
        source = reference.MixtureBatcher(
            described.recipe,
            {"synthetic": described.readers["synthetic"]},
            context_length=512,
            global_batch_valid_targets=65536,
            microbatch_sequences=8,
            emit_tensors=True,
            max_open_shards=1,
        )
    captured: list[tuple[str, dict[str, Any]]] = []
    for index in range(3):
        if index == 2:
            original = reference.target_trace_digest

            def capture(previous: str, trace: dict[str, Any], original: Any = original) -> str:
                captured.append((previous, trace))
                return str(original(previous, trace))

            reference.target_trace_digest = capture
        source.next_step_microbatches()
        source.commit()
    source.close()
    return captured


def replay(records: list[tuple[str, dict[str, Any]]]) -> dict[str, Any]:
    from xlm.data.sampling.trace import extend_target_trace_chain

    def best(function: Any) -> float:
        times = []
        for _ in range(5):
            begin = time.perf_counter()
            function()
            times.append(time.perf_counter() - begin)
        return min(times) * 1000

    first = records[0][0]
    raws = [
        json.dumps(
            {"previous": p, "target": t}, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
        for p, t in records
    ]

    def chain() -> str:
        previous = first
        for _, trace in records:
            previous = target_trace_digest(previous, trace)
        return previous

    columns = {
        k: [t[k] for _, t in records] for k in ("doc_id", "lineage_id", "token_offset", "label")
    }
    spans = [tuple(t["byte_span"]) for _, t in records]
    # An update can cross an epoch boundary; the product folds each window with
    # its own (source, epoch), so the replay folds each consecutive run.
    runs: list[tuple[str, int, list[int]]] = []
    for index, (_, trace) in enumerate(records):
        key = (trace["source_id"], trace["epoch"])
        if not runs or runs[-1][:2] != key:
            runs.append((*key, []))
        runs[-1][2].append(index)

    def fold() -> str:
        previous = first
        for source, epoch, indices in runs:
            previous = extend_target_trace_chain(
                previous,
                source,
                epoch,
                columns["doc_id"],
                columns["lineage_id"],
                columns["token_offset"],
                columns["label"],
                spans,
                indices,
            )
        return previous

    assert fold() == chain()
    return {
        "targets": len(records),
        "mean_record_bytes": sum(map(len, raws)) / len(raws),
        "p33_chain_ms": best(chain),
        "json_dumps_only_ms": best(
            lambda: [
                json.dumps(
                    {"previous": p, "target": t},
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                )
                for p, t in records
            ]
        ),
        "sha256_hexdigest_only_ms": best(lambda: [hashlib.sha256(r).hexdigest() for r in raws]),
        "exact_fold_ms": best(fold),
        "fold_equals_chain": True,
    }


def _ipc_child(conn: Any, mode: str, shm_name: str | None, reps: int) -> None:
    batches, state, payload = _update()
    shm = shared_memory.SharedMemory(name=shm_name) if shm_name else None
    conn.send("ready")
    for _ in range(reps):
        conn.recv()
        begin = time.perf_counter()
        if mode == "full_trainingbatch_pickle":
            conn.send((batches, state))
        elif mode == "compact_arrays_and_state":
            conn.send(payload)
        else:
            offset = 0
            for value in payload["arrays"].values():
                view = np.ndarray(value.shape, value.dtype, buffer=shm.buf, offset=offset)  # type: ignore[union-attr]
                np.copyto(view, value)
                offset += value.nbytes
            conn.send({"rows": payload["rows"], "state": state})
        conn.send(time.perf_counter() - begin)
    if shm is not None:
        shm.close()


def _update() -> tuple[Any, dict[str, Any], dict[str, Any]]:
    source = bench.make_batcher("current", 8, 65536)
    source.next_step_microbatches()
    source.commit()
    batches = source.next_step_microbatches()
    source.commit()
    state = source.get_state()
    source.close()
    arrays = {
        name: np.concatenate([getattr(b, name).numpy() for b in batches])
        for name in ("input_ids", "labels", "loss_mask", "position_ids")
    }
    arrays["attention"] = arrays["input_ids"] != 0
    return (
        batches,
        state,
        {"rows": [len(b.input_ids) for b in batches], "arrays": arrays, "state": state},
    )


def ipc(reps: int = 20) -> dict[str, Any]:
    context = multiprocessing.get_context("spawn")
    batches, state, payload = _update()
    size = sum(v.nbytes for v in payload["arrays"].values())
    result: dict[str, Any] = {
        "windows": sum(payload["rows"]),
        "tensor_bytes": size,
        "pickle_bytes": {
            "full_trainingbatch_pickle": len(pickle.dumps((batches, state), protocol=5)),
            "compact_arrays_and_state": len(pickle.dumps(payload, protocol=5)),
            "state_only_json": len(json.dumps(state)),
        },
    }
    source = bench.make_batcher("current", 8, 65536)
    spec = ProducerSpec.from_batcher(source)
    with PrefetchingBatcher(spec, source.get_state()) as prefetcher:
        prefetcher.next_step_microbatches()
        prefetcher.commit()
        prefetcher.next_step_microbatches()
        update = prefetcher.pending_update
        assert update is not None
        times = []
        for _ in range(5):
            begin = time.perf_counter()
            for index in range(len(update.rows)):
                update.microbatch_provenance(index)
            times.append(time.perf_counter() - begin)
        result["exact_provenance_materialization_ms"] = min(times) * 1000
        result["prepared_update_pickle_bytes"] = len(pickle.dumps(update, protocol=5))
    source.close()
    shm = shared_memory.SharedMemory(create=True, size=size)
    try:
        for mode in ("full_trainingbatch_pickle", "compact_arrays_and_state", "shared_memory_slot"):
            parent, child = context.Pipe()
            begin = time.perf_counter()
            process = context.Process(target=_ipc_child, args=(child, mode, shm.name, reps))
            process.start()
            assert parent.recv() == "ready"
            startup = time.perf_counter() - begin
            receive, send = [], []
            for _ in range(reps):
                parent.send("go")
                while not parent.poll(0.0005):
                    pass
                begin = time.perf_counter()
                received = parent.recv()
                if mode == "shared_memory_slot":
                    offset = 0
                    for value in payload["arrays"].values():
                        torch.from_numpy(
                            np.ndarray(value.shape, value.dtype, buffer=shm.buf, offset=offset)
                        )
                        offset += value.nbytes
                elif mode == "compact_arrays_and_state":
                    for value in received["arrays"].values():
                        torch.from_numpy(value)
                receive.append((time.perf_counter() - begin) * 1000)
                send.append(parent.recv() * 1000)
                del received
            process.join(30)
            result[mode] = {
                "child_startup_s": startup,
                "consumer_receive_ms_median": statistics.median(receive),
                "producer_send_ms_median": statistics.median(send),
                "exitcode": process.exitcode,
            }
    finally:
        shm.close()
        shm.unlink()
    return result


def ceiling(loader: str, updates: int = 20) -> dict[str, Any]:
    with bench.P33.batcher(8, 65536) as described:
        spec = ProducerSpec.from_batcher(described)
        state = described.get_state()
    if loader == "p33":
        spec = dataclasses.replace(spec, factory=bench.p33_producer_factory)
    begin = time.perf_counter()
    with PrefetchingBatcher(spec, state) as prefetcher:
        startup = time.perf_counter() - begin
        budget = 65536 * (updates + 5)
        for _ in range(2):  # warm the producer
            batches = prefetcher.next_step_microbatches(budget)
            budget -= sum(int(b.loss_mask.sum()) for b in batches)
            prefetcher.commit()
        before = prefetcher.stats()
        begin = time.perf_counter()
        for _ in range(updates):
            batches = prefetcher.next_step_microbatches(budget)
            budget -= sum(int(b.loss_mask.sum()) for b in batches)
            prefetcher.commit()
        wall = time.perf_counter() - begin
        after = prefetcher.stats()
    return {
        "loader": loader,
        "producer_startup_s": startup,
        "updates": updates,
        "updates_per_second": updates / wall,
        "targets_per_second": updates * 65536 / wall,
        "producer_ms_per_update": (after["produce_seconds"] - before["produce_seconds"])
        / updates
        * 1000,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("probe", choices=["decompose", "ipc", "ceiling", "all"])
    parser.add_argument("--name", required=True)
    args = parser.parse_args()
    output = bench.EVIDENCE / f"{args.name}.json"
    if output.exists():
        raise FileExistsError(output)
    if bench.foreign_xlm_processes(bench.own_pids()):
        raise RuntimeError("another XLM Python process is running; CPU probes need a quiet host")
    torch.set_num_threads(1)
    result: dict[str, Any] = {
        "head": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=bench.ROOT, text=True
        ).strip()
    }
    if args.probe in ("decompose", "all"):
        result["decompose"] = {loader: decompose(loader) for loader in ("p33", "current")}
    if args.probe in ("ipc", "all"):
        result["ipc"] = ipc()
    if args.probe in ("ceiling", "all"):
        result["ceiling"] = [ceiling("current"), ceiling("p33")]
    result["foreign_processes_after"] = bench.foreign_xlm_processes(bench.own_pids())
    result["status"] = "INVALID_CONTENDED" if result["foreign_processes_after"] else "VERIFIED"
    bench.write_json(output, result)
    print(json.dumps(result, indent=2, default=str)[:6000])


if __name__ == "__main__":
    main()
