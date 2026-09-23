"""P29B offline, bounded production-size vocabulary experiments (not research fitting)."""

from __future__ import annotations

import argparse
import cProfile
import hashlib
import itertools
import json
import os
import random
import struct
import time
from collections.abc import Iterator
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

from benchmark_pipeline import Measurements

from xlm.core.contracts import CanonicalDocument
from xlm.data.canonical_io import CanonicalDatasetReader
from xlm.data.normalization import canonical_normalize
from xlm.data.tokens import TokenShardWriter
from xlm.tokenizers.bpe import ByteLevelBPETokenizer


def fixture_document(text: str, index: int) -> CanonicalDocument:
    digest = hashlib.sha256(text.encode()).hexdigest()
    return CanonicalDocument(
        str(index),
        "fixture",
        "v1",
        "authored",
        index,
        digest,
        digest,
        text,
        len(text.encode()),
        "en",
        1.0,
        "prose",
        {},
        [],
        "authored",
        [],
        [],
        {},
        "train",
    )


def prepare(root: Path) -> dict[str, Any]:
    rng = random.Random(20260923)
    words = ["".join(rng.choices("abcdefghijklmnopqrstuvwxyz", k=12)) for _ in range(50000)]
    docs = (
        fixture_document(" ".join(words[i : i + 100]) * 3, i) for i in range(0, len(words), 100)
    )
    start = time.perf_counter()
    tokenizer = ByteLevelBPETokenizer.train_from_documents(docs, target_vocab_size=32768)
    if tokenizer.actual_vocab_size != 32768:
        raise ValueError("fixture failed to reach required vocabulary")
    tokenizer.save(root / "tokenizer")
    return {
        "fit_seconds": time.perf_counter() - start,
        "actual_vocab": tokenizer.actual_vocab_size,
        "fingerprint": tokenizer.fingerprint,
        "production": tokenizer.is_production_baseline,
    }


def probe(root: Path, source: Path, tokenizer_path: Path, count: int) -> dict[str, Any]:
    measure = Measurements(root, 900, 1)
    with measure.stage("load_tokenizer"):
        tok = ByteLevelBPETokenizer.load(tokenizer_path)
    documents = list(itertools.islice(CanonicalDatasetReader.read_jsonl(source), count))
    texts = [canonical_normalize(d.text) for d in documents]
    total = 0
    with measure.stage("native_encode"):
        for text in texts:
            total += len(tok._tok.encode(text, add_special_tokens=False).ids)
    for name, encode in (("ids_only", tok.encode), ("with_offsets", tok.encode_with_offsets)):
        with measure.stage(name) as row:
            for text in texts:
                encode(text)
        row["tokens_per_second"] = total / row["wall_seconds"]
    for size in (16, 64, 128, 512):
        with measure.stage(f"native_batch_{size}") as row:
            for start in range(0, len(texts), size):
                batch = tok._tok.encode_batch(texts[start : start + size], add_special_tokens=False)
                for encoded in batch:
                    # Exact oracle is outside the timed experiment below; this loop
                    # deliberately only materializes the native IDs.
                    _ = encoded.ids
        row["tokens_per_second"] = total / row["wall_seconds"]
    for text, encoded in zip(
        texts[:512], tok._tok.encode_batch(texts[:512], add_special_tokens=False), strict=True
    ):
        assert encoded.ids == tok.encode(text)
    records = [tok.encode_with_offsets(t) for t in texts[:128]]
    for size in (4096, 16384, 65536, 262144):
        ids = list(itertools.chain.from_iterable(e[0] for e in records)) * 4
        with measure.stage(f"binary_block_{size}") as row:
            for _ in range(8):
                digest = hashlib.sha256()
                for start in range(0, len(ids), size):
                    block = ids[start : start + size]
                    for tid in block:
                        if tid < 0 or tid > 65535:
                            raise ValueError("invalid fixture ID")
                    digest.update(struct.pack(f"<{len(block)}H", *block))
        row["sha256"] = digest.hexdigest()
    profiler = cProfile.Profile()
    with measure.stage("writer_profile"):
        profiler.enable()
        TokenShardWriter(root / "profile_tokens", "fixture", "fixture", tok).write_documents(
            documents, True
        )
        profiler.disable()
    profiler.dump_stats(str(root / "writer.pstats"))
    return {"documents": len(documents), "tokens": total, "stages": measure.rows}


class _BatchProxy:
    def __init__(self, backend: Any) -> None:
        self.backend = backend
        self.pending: dict[str, Any] = {}

    def __getattr__(self, name: str) -> Any:
        return getattr(self.backend, name)

    def encode(self, text: str, **kwargs: Any) -> Any:
        return self.pending[text]

    def documents(
        self, documents: Iterator[CanonicalDocument], size: int
    ) -> Iterator[CanonicalDocument]:
        while docs := list(itertools.islice(documents, size)):
            texts = [canonical_normalize(d.text) for d in docs]
            self.pending.clear()
            self.pending.update(
                zip(texts, self.backend.encode_batch(texts, add_special_tokens=False), strict=True)
            )
            yield from docs


def worker(
    tokenizer_path: Path, assignments: list[tuple[int, Path]], output: Path, batch: int
) -> dict[str, Any]:
    # One task owns several whole shards; no documents cross process queues.
    begin_cpu = time.process_time()
    begin = time.perf_counter()
    tok = ByteLevelBPETokenizer.load(tokenizer_path)
    init = time.perf_counter() - begin
    manifests = []
    proxy = _BatchProxy(tok._tok)
    if batch:
        tok._tok = proxy  # type: ignore[assignment]
    for ordinal, source in assignments:
        # Batch candidate intentionally benchmark-only until evidence warrants API work.
        documents = CanonicalDatasetReader.read_jsonl(source)
        stream = proxy.documents(documents, batch) if batch else documents
        manifest = TokenShardWriter(
            output / f"shard-{ordinal:05d}", f"shard-{ordinal:05d}", "authored_mix", tok
        ).write_documents(stream, True)
        manifests.append((ordinal, manifest.to_dict()))
    return {
        "init_seconds": init,
        "wall_seconds": time.perf_counter() - begin,
        "cpu_seconds": time.process_time() - begin_cpu,
        "manifests": manifests,
    }


def scaling(root: Path, source: Path, tokenizer: Path, workers: int, batch: int) -> dict[str, Any]:
    measure = Measurements(root, 900, workers)
    shards = sorted(source.glob("shard-*.jsonl"))
    if not shards:
        raise ValueError("no input shards")
    assignments = [list(enumerate(shards))[i::workers] for i in range(workers)]
    with measure.stage("shard_tokenization") as row:
        if workers == 1:
            results = [worker(tokenizer, assignments[0], root, batch)]
        else:
            with ProcessPoolExecutor(max_workers=workers) as executor:
                futures = [executor.submit(worker, tokenizer, a, root, batch) for a in assignments]
                results = [f.result() for f in futures]
    manifests = sorted(itertools.chain.from_iterable(r["manifests"] for r in results))
    tokens = sum(m[1]["num_tokens"] for m in manifests)
    docs = sum(m[1]["num_documents"] for m in manifests)
    row.update(
        tokens_per_second=tokens / row["wall_seconds"],
        docs_per_second=docs / row["wall_seconds"],
        output_mib_per_second=row["artifact_bytes_after_stage"] / 1024**2 / row["wall_seconds"],
    )
    return {
        "workers": workers,
        "batch": batch,
        "stages": measure.rows,
        "workers_detail": results,
        "manifest_digest": hashlib.sha256(json.dumps(manifests).encode()).hexdigest(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("prepare", "probe", "scaling", "shard", "direct"))
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--tokenizer", type=Path)
    parser.add_argument("--documents", type=int, choices=(10000, 100000), default=10000)
    parser.add_argument("--workers", type=int, choices=(1, 2, 4, 8), default=1)
    parser.add_argument("--batch", type=int, choices=(0, 64, 128, 512), default=0)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    if args.mode == "prepare":
        report = prepare(args.output)
    elif args.mode == "probe":
        report = probe(args.output, args.source, args.tokenizer, args.documents)
    elif args.mode == "shard":
        from xlm.data.datasets.shards import ShardedJsonlWriter

        writer = ShardedJsonlWriter(
            args.output, dataset_id="p29b-fixture", target_shard_bytes=4 * 1024**2
        )
        for d in itertools.islice(CanonicalDatasetReader.read_jsonl(args.source), args.documents):
            writer.write_line(json.dumps(d.to_dict(), ensure_ascii=False), d.doc_id)
        report = writer.finish().to_dict()
    elif args.mode == "direct":
        measure = Measurements(args.output, 900, 1)
        with measure.stage("tokenizer_load"):
            tok = ByteLevelBPETokenizer.load(args.tokenizer)
        with measure.stage("tokenization_shards") as row:
            manifest = TokenShardWriter(
                args.output / "tokens",
                "fixture",
                "authored_mix",
                tok,
                max_output_bytes=2 * 1024**3,
                batch_size=args.batch or 1,
            ).write_documents(
                itertools.islice(CanonicalDatasetReader.read_jsonl(args.source), args.documents),
                True,
            )
            row["manifest"] = manifest.to_dict()
        row["tokens_per_second"] = manifest.num_tokens / row["wall_seconds"]
        report = {"stages": measure.rows, "batch_size": args.batch or 1}
    else:
        report = scaling(args.output, args.source, args.tokenizer, args.workers, args.batch)
    report["tokenizers_parallelism"] = os.environ.get("TOKENIZERS_PARALLELISM")
    report["rayon_num_threads"] = os.environ.get("RAYON_NUM_THREADS")
    (args.output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
