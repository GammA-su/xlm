"""Bounded offline pipeline benchmark; generated shapes, never live source evidence.

Run with uv and the existing locked environment. See docs/PERFORMANCE.md.
"""

from __future__ import annotations

import argparse
import cProfile
import gzip
import hashlib
import json
import os
import platform
import random
import struct
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import patch

import psutil

from xlm.data.adapters.jsonl import JsonlAdapter
from xlm.data.canonical_io import CanonicalDatasetReader, CanonicalDatasetWriter
from xlm.data.cleaning.quarantine import QuarantinePolicy
from xlm.data.cleaning.sharded import run_sharded_clean
from xlm.data.datasets.shards import ShardedJsonlWriter
from xlm.data.pools.splits import SplitConfig, apply_splits, assign_splits
from xlm.data.sampling.packing import CausalStreamPacker
from xlm.data.tokens import TokenShardReader, TokenShardWriter
from xlm.tokenizers.bpe import ByteLevelBPETokenizer
from xlm.training.data import TrainingBatcher

MIB = 1024 * 1024
WORDS = (
    "river mountain forest ocean valley garden bridge tower village market bakery school "
    "library museum theater farmer baker teacher doctor writer painter singer captain pilot "
    "sailor merchant trader lawyer apple bread cheese honey lemon olive pepper wheat "
    "table chair window door roof floor clock lamp mirror carpet pencil paper book letter "
    "photo radio horse bird fish sheep rabbit oak pine maple willow cedar spring summer "
    "winter morning evening night bright gentle quiet brave calm eager fair quick ready "
    "sharp simple soft sweet warm wide wise young early steady run walk jump climb swim "
    "fly slide march wander sing dance paint write draw build weave sew bake cook plant "
    "harvest gather store carry bring share open close lift push turn fold stack clean "
    "clear cloudy rainy sunny windy cold cool red blue green yellow purple brown silver"
).split()
SHAPES = ("common_pile", "simple_stories", "finewiki", "finepdf", "synth", "wiki_rewrite")


def fixture_rows(count: int) -> Iterator[dict[str, Any]]:
    """Six authored text/metadata shapes, deterministic and at most 8 KiB per row."""
    rng = random.Random(20260923)
    for index in range(count):
        shape = index % len(SHAPES)
        length = (120, 40, 180, 400, 80, 160)[shape]
        words = [rng.choice(WORDS) if i % 5 else "the" for i in range(length)]
        text = " ".join(words)
        if shape == 2:
            text = "# A local history\n\n" + text + "\n\n| Place | Year |\n| --- | --- |\n"
        elif shape == 3:
            text = text[: len(text) // 2] + "\n\n" + text[len(text) // 2 :]
        elif shape == 4:
            text = "Question: What did the visitor observe?\nAnswer: " + text
        if index % 31 == 0:
            text += " Contact sample@example.org for details."
        if index % 47 == 0:
            text += " Contact researcher@authored.invalid for details."
        if index % 29 == 0:
            text += " Caf\u00e9 \u6771\u4eac \U0001f642."
        yield {
            "id": str(index),
            "text": text,
            "split": "train",
            "fixture_shape": SHAPES[shape],
            "language": "en",
            "metadata": {"title": f"Authored document {index}", "ordinal": index},
        }


class Measurements:
    """Stage-local sampled process-tree RSS/CPU, process I/O, and parent fsync calls."""

    def __init__(
        self, root: Path, max_seconds: float, workers: int, max_artifact_bytes: int = 2 * 1024 * MIB
    ) -> None:
        self.root = root
        self.deadline = time.monotonic() + max_seconds
        self.rows: list[dict[str, Any]] = []
        self.workers = workers
        self.max_artifact_bytes = max_artifact_bytes

    @contextmanager
    def stage(self, name: str, *, workers: int | None = None) -> Iterator[dict[str, Any]]:
        process = psutil.Process()
        before_io = process.io_counters()
        before_cpu = time.process_time()
        start = time.perf_counter()
        peak = [0]
        disk_peak = [0]
        failure: list[str] = []
        stop = threading.Event()
        worker_count = self.workers if workers is None else workers

        def sample() -> None:
            children: list[psutil.Process] = []
            last_children = 0.0
            last_disk = 0.0
            while not stop.is_set():
                try:
                    if worker_count > 1 and time.monotonic() - last_children > 1:
                        children = process.children(recursive=True)
                        last_children = time.monotonic()
                    rss = sum(p.memory_info().rss for p in [process, *children])
                    peak[0] = max(peak[0], rss)
                    if worker_count > 1 and time.monotonic() - last_disk > 1:
                        disk = sum(p.stat().st_size for p in self.root.rglob("*") if p.is_file())
                        disk_peak[0] = max(disk_peak[0], disk)
                        if disk > self.max_artifact_bytes:
                            failure.append("artifact byte cap exceeded, including scratch")
                        last_disk = time.monotonic()
                    if rss > 3 * 1024 * MIB:
                        failure.append("3 GiB process-tree RSS cap exceeded")
                    if time.monotonic() > self.deadline:
                        failure.append("benchmark wall-time cap exceeded")
                    # Kill only this benchmark's children/self on a breached hard cap.
                    if failure:
                        sys.stderr.write(failure[-1] + "\n")
                        for child in process.children(recursive=True):
                            child.kill()
                        os._exit(2)
                except (psutil.NoSuchProcess, FileNotFoundError):
                    pass
                stop.wait(0.1)

        monitor = threading.Thread(target=sample, daemon=True)
        monitor.start()
        row: dict[str, Any] = {"stage": name, "measurement_workers": worker_count}
        try:
            with patch("os.fsync", wraps=os.fsync) as fsync:
                yield row
                row["parent_fsync_calls"] = fsync.call_count
        finally:
            stop.set()
            monitor.join()
        io = process.io_counters()
        row.update(
            wall_seconds=time.perf_counter() - start,
            parent_cpu_seconds=time.process_time() - before_cpu,
            peak_tree_rss_bytes=peak[0],
            parent_read_bytes=io.read_bytes - before_io.read_bytes,
            parent_write_bytes=io.write_bytes - before_io.write_bytes,
        )
        disk = sum(p.stat().st_size for p in self.root.rglob("*") if p.is_file())
        if disk > self.max_artifact_bytes:
            raise RuntimeError("artifact byte cap exceeded")
        row["artifact_bytes_after_stage"] = disk
        row["sampled_peak_artifact_bytes"] = max(disk, disk_peak[0])
        self.rows.append(row)
        print(f"{name:24s} {row['wall_seconds']:9.3f}s  RSS {peak[0] / MIB:8.1f} MiB", flush=True)


def run(
    root: Path,
    count: int,
    workers: int,
    max_seconds: float,
    tokenizer_path: Path | None = None,
    token_workers: int = 0,
    reference_p29: bool = False,
) -> dict[str, Any]:
    """Run actual local stages, with dedup explicitly omitted and no model updates."""
    root.mkdir(parents=True, exist_ok=False)
    tokenizer_class = ByteLevelBPETokenizer
    writer_class = TokenShardWriter
    packer_class = CausalStreamPacker
    if reference_p29:
        if token_workers:
            raise ValueError("P29 reference is scalar; do not mix implementations")
        from benchmark_token_oracles import reference

        tokenizer_class = reference(
            "src/xlm/tokenizers/bpe.py", "pipeline_reference_bpe"
        ).ByteLevelBPETokenizer
        writer_class = reference(
            "src/xlm/data/tokens.py", "pipeline_reference_tokens"
        ).TokenShardWriter
        packer_class = reference(
            "src/xlm/data/sampling/packing.py", "pipeline_reference_packing"
        ).CausalStreamPacker
    measurements = Measurements(
        root,
        max_seconds,
        1,
        max_artifact_bytes=(3 if token_workers else 2) * 1024 * MIB,
    )
    source = root / "source.jsonl.gz"
    selected = root / "selected.jsonl"
    with measurements.stage("fixture_generation"):
        with gzip.open(source, "wb") as output:
            for record in fixture_rows(count):
                line = (json.dumps(record, ensure_ascii=False) + "\n").encode()
                if len(line) > 8192:
                    raise ValueError("fixture row exceeds byte cap")
                output.write(line)
    with measurements.stage("gzip_decode_publication") as row:
        digest = hashlib.sha256()
        with gzip.open(source, "rb") as stream, selected.open("wb") as output:
            for line in stream:
                # Decode validation models selected-row publication, no transport used.
                json.loads(line)
                output.write(line)
                digest.update(line)
            output.flush()
            os.fsync(output.fileno())
        row["sha256"] = digest.hexdigest()
    adapter = JsonlAdapter("authored_mix", source_revision="fixture-v1")
    with measurements.stage("adaptation_jsonl"):
        CanonicalDatasetWriter(root / "adapted").write_jsonl(adapter.process_file(selected))
    adapted = root / "adapted" / "documents.jsonl"
    with measurements.stage("canonical_sharding"):
        writer = ShardedJsonlWriter(
            root / "sharded", dataset_id="authored-v1", target_shard_bytes=4 * MIB
        )
        for doc in CanonicalDatasetReader.read_jsonl(adapted):
            writer.write_line(json.dumps(doc.to_dict(), ensure_ascii=False), doc.doc_id)
        writer.finish()
    with measurements.stage("cleaning", workers=workers) as row:
        summary, timing, _, _, _ = run_sharded_clean(
            input_path=root / "sharded",
            output_dir=root / "cleaned",
            preset="prose",
            workers=workers,
            input_shard_bytes=4 * MIB,
            output_shard_bytes=None,
            quarantine_dir=root / "quarantine",
            quarantine_shard_bytes=None,
            quarantine_policy=QuarantinePolicy(max_records=count, store_previews=False),
            max_docs=count,
            max_input_bytes=1024 * MIB,
        )
        row["accepted"] = summary.total_output_docs
        row["timing"] = timing.to_dict()
    cleaned = root / "cleaned" / "documents.jsonl"
    with measurements.stage("split_and_publish") as row:
        assignment = assign_splits(
            CanonicalDatasetReader.read_jsonl(cleaned),
            config=SplitConfig(
                diagnostic_val_target_bytes=4096,
                quick_val_target_bytes=1024,
                audit_target_bytes=2048,
            ),
        )
        row["membership_digest"] = assignment.membership_digest()
        CanonicalDatasetWriter(root / "split").write_jsonl(
            apply_splits(
                CanonicalDatasetReader.read_jsonl(cleaned),
                assignment,
            )
        )
    split = root / "split" / "documents.jsonl"
    with measurements.stage("fixture_tokenizer_fit") as row:
        tokenizer = (
            tokenizer_class.load(tokenizer_path)
            if tokenizer_path
            else tokenizer_class.train_from_documents(
                (doc for doc in CanonicalDatasetReader.read_jsonl(split) if doc.split == "train"),
                target_vocab_size=512,
                max_train_docs=256,
                max_train_bytes=MIB,
            )
        )
        row["fingerprint"] = tokenizer.fingerprint
        tokenizer.save(root / "tokenizer")
    with measurements.stage("tokenization_shards", workers=max(1, token_workers)) as row:
        if token_workers:
            import tempfile

            from xlm.data.parallel_tokens import TokenizationLimits, tokenize_to_single_shard

            with tempfile.TemporaryDirectory(prefix="token-input-", dir=root) as temporary:
                input_dir = Path(temporary)
                writer = ShardedJsonlWriter(
                    input_dir, dataset_id="fixture", target_shard_bytes=4 * MIB
                )
                for doc in CanonicalDatasetReader.read_jsonl(split):
                    writer.write_line(json.dumps(doc.to_dict(), ensure_ascii=False), doc.doc_id)
                writer.finish()
                existing_bytes = sum(p.stat().st_size for p in root.rglob("*") if p.is_file())
                manifest, token_timing = tokenize_to_single_shard(
                    input_dir,
                    root / "tokenizer",
                    root / "tokens",
                    source_id="authored_mix",
                    shard_id="fixture",
                    pool_hash="p02_local_pool",
                    workers=token_workers,
                    limits=TokenizationLimits(
                        max_output_bytes=min(
                            2 * 1024 * MIB, measurements.max_artifact_bytes - existing_bytes
                        )
                    ),
                )
                row["parallel_timing"] = token_timing
        else:
            manifest = writer_class(
                root / "tokens", "fixture", "authored_mix", tokenizer
            ).write_documents(
                CanonicalDatasetReader.read_jsonl(split),
                add_special_tokens=True,
            )
        row["manifest"] = manifest.to_dict()
    reader = TokenShardReader(root / "tokens")
    with measurements.stage("token_verification"):
        reader.verify_integrity()
    with measurements.stage("mmap_packing") as row:
        packer = packer_class(context_length=512)
        targets = 0
        with reader.mmap_tokens() as view:
            for start in range(0, reader.manifest.num_tokens - 1, 512):
                size = min(513, reader.manifest.num_tokens - start)
                ids = list(struct.unpack_from(f"<{size}H", view, start * 2))
                packed = packer.pack(ids, ["authored_mix"] * size, carry_context=start > 0)
                targets += sum(packed.loss_mask)
        row["valid_targets"] = targets
    with measurements.stage("loader_dummy_consume") as row:
        batcher = TrainingBatcher(reader, context_length=512, global_batch_valid_targets=8192)
        digest = hashlib.sha256()
        # Fixed bounded prefix, no forward/backward/optimizer operation.
        steps = min(16, (manifest.num_tokens - 1024) // 9000)
        for _ in range(max(0, steps)):
            for batch in batcher.next_step_microbatches():
                digest.update(str(batch.input_ids.tolist()).encode())
            batcher.commit()
        row["steps"] = steps
        row["batch_digest"] = digest.hexdigest()
    return {
        "schema_version": 1,
        "fixture_only": True,
        "dedup": "SKIPPED: P28 ownership",
        "documents": count,
        "workers": workers,
        "token_workers": token_workers,
        "reference_p29": reference_p29,
        "head": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "artifact_byte_cap": measurements.max_artifact_bytes,
        "python": sys.version,
        "platform": platform.platform(),
        "cpu_count": os.cpu_count(),
        "stages": measurements.rows,
        "pipeline_wall_seconds": sum(r["wall_seconds"] for r in measurements.rows[1:]),
        "limitations": [
            "parent CPU/I/O and fsync exclude workers",
            "RSS sampled at 100 ms; child discovery 1 s",
            "source-like shapes use generic JSONL adapter, not live schema certification",
            "packing and loader are separately measured consumers of identical tokens",
            "no network, dedup, production tokenizer, GPU training or official evaluation",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--documents", type=int, default=10_000)
    parser.add_argument("--workers", type=int, choices=(1, 2, 4, 8), default=1)
    parser.add_argument("--max-seconds", type=float, default=600)
    parser.add_argument("--profile", action="store_true")
    parser.add_argument("--tokenizer", type=Path, help="Existing local fixture; never downloaded")
    parser.add_argument("--token-workers", type=int, choices=(0, 1, 2, 4, 8), default=0)
    parser.add_argument("--reference-p29", action="store_true")
    args = parser.parse_args()
    if not 100 <= args.documents <= 100_000 or not 1 <= args.max_seconds <= 1800:
        parser.error("documents must be 100..100000 and max-seconds 1..1800")
    profiler = cProfile.Profile()
    if args.profile:
        profiler.enable()
    report = run(
        args.output,
        args.documents,
        args.workers,
        args.max_seconds,
        args.tokenizer,
        args.token_workers,
        args.reference_p29,
    )
    if args.profile:
        profiler.disable()
        profiler.dump_stats(str(args.output / "profile.pstats"))
    report_path = args.output / "report.json"
    temporary = report_path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, report_path)
    print(f"Pipeline: {report['pipeline_wall_seconds']:.3f}s; {report_path}")


if __name__ == "__main__":
    main()
