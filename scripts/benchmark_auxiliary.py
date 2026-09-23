"""Small offline audit of acquisition regimes, Parquet, pools, evaluation and saves."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import torch
from benchmark_hotspots import documents
from benchmark_pipeline import Measurements, fixture_rows

from xlm.config.schemas import TransformerBaselineConfig
from xlm.core.paths import ArtifactPaths
from xlm.data.acquisition.plan import AcquisitionLimits
from xlm.data.acquisition.records import jsonl_records
from xlm.data.canonical_io import CanonicalDatasetReader, CanonicalDatasetWriter
from xlm.data.pools.builder import detect_split_leaks
from xlm.data.pools.views import OverlapPolicy, SourceView, resolve_view_membership
from xlm.evaluation.likelihood import BoundaryPolicy, ConditionalLikelihoodScorer
from xlm.models.transformer import TransformerBaseline
from xlm.reports.render import to_html_data
from xlm.tokenizers.byte import ByteTokenizer
from xlm.training.checkpoint import CheckpointManager


class SimulatedReader(io.BytesIO):
    """In-memory compressed bytes with explicit per-read latency/bandwidth costs."""

    def __init__(self, data: bytes, latency: float, bytes_per_second: int) -> None:
        super().__init__(data)
        self.latency = latency
        self.bytes_per_second = bytes_per_second
        self.requests = 0
        self.transferred = 0
        self.wait_seconds = 0.0

    def read(self, size: int | None = -1) -> bytes:
        data = super().read(size)
        self.requests += 1
        self.transferred += len(data)
        if self.requests > 1000 or self.transferred > 1024 * 1024:
            raise RuntimeError("simulated request/byte cap exceeded")
        wait = self.latency + len(data) / self.bytes_per_second
        if wait:
            start = time.perf_counter()
            time.sleep(wait)
            self.wait_seconds += time.perf_counter() - start
        return data


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root: Path = args.output
    root.mkdir(parents=True, exist_ok=False)
    measurements = Measurements(root, 180, 1)
    raw = b"".join((json.dumps(r) + "\n").encode() for r in fixture_rows(256))
    compressed = gzip.compress(raw, mtime=0)
    regimes = []
    for name, latency, bandwidth in (
        ("low_latency_high_bw", 0.001, 100 * 1024 * 1024),
        ("high_latency_high_bw", 0.04, 100 * 1024 * 1024),
        ("medium_bandwidth", 0.005, 1024 * 1024),
        ("cpu_decode", 0.0, 10**15),
    ):
        stream = SimulatedReader(compressed, latency, bandwidth)
        with measurements.stage(name) as row:
            with gzip.GzipFile(fileobj=stream) as decoded:
                lines = [
                    line
                    for _, _, line, _ in jsonl_records(
                        decoded,
                        AcquisitionLimits(
                            max_record_bytes=8192,
                            max_decompressed_bytes=4 * 1024 * 1024,
                            max_records=256,
                            max_scanned_records=256,
                        ),
                    )
                ]
            assert b"".join(lines) == raw
            row.update(
                requests=stream.requests,
                transferred_bytes=stream.transferred,
                measured_wait_seconds=stream.wait_seconds,
                latency_seconds=latency,
                bytes_per_second=bandwidth,
            )
        regimes.append(row)
    docs = documents(2000)
    with measurements.stage("parquet_write"):
        path = CanonicalDatasetWriter(root / "parquet").write_parquet(docs)
    with measurements.stage("parquet_decode"):
        assert list(CanonicalDatasetReader.read_parquet(path)) == docs
    with measurements.stage("pool_views_leaks") as row:
        views = [SourceView(view_id="authored", family_id="authored_mix")]
        membership = resolve_view_membership(docs, views, OverlapPolicy.EXCLUSIVE_ASSIGNMENT)
        assert not detect_split_leaks(docs)
        row["documents"] = membership.distinct_documents()
    torch.manual_seed(923)
    model = TransformerBaseline(
        TransformerBaselineConfig(
            vocab_size=260,
            num_layers=1,
            hidden_size=16,
            num_attention_heads=2,
            intermediate_size=32,
            context_length=64,
            attention_backend="eager",
        )
    )
    tokenizer = ByteTokenizer()
    scorer = ConditionalLikelihoodScorer(
        model, tokenizer, device="cpu", boundary_policy=BoundaryPolicy.SEPARATE_V1
    )
    with measurements.stage("authored_eval_candidates") as row:
        results = [
            scorer.score_continuation("A visitor saw", answer)
            for answer in (" a river.", " a forest.", " a bird.", " a bridge.") * 8
        ]
        row["candidate_count"] = len(results)
        row["diagnostic_only"] = True
        row["result_digest"] = hashlib.sha256(
            str([r.log_likelihood for r in results]).encode()
        ).hexdigest()
    manager = CheckpointManager(paths=ArtifactPaths(root=root / "checkpoint-home"))
    with measurements.stage("checkpoint_save") as row:
        checkpoint = manager.save_checkpoint(
            "authored",
            "authored",
            0,
            0,
            0,
            "authored-no-training",
            model,
            None,
            None,
            None,
            None,
            None,
        )
        row["optimizer_state"] = "NOT MEASURED: no training or optimizer updates"
        row["unique_parameters"] = sum(p.numel() for p in model.parameters())
    with measurements.stage("checkpoint_verify"):
        manager.store.verify_artifact(checkpoint)
    with measurements.stage("report_render") as row:
        html = to_html_data(
            {
                "quality": {"documents": len(docs)},
                "previews": [{"doc_id": d.doc_id, "text": d.text} for d in docs[:100]],
            }
        )
        row["html_bytes"] = len(html.encode())
    report: dict[str, Any] = {
        "fixture_only": True,
        "python": sys.version,
        "stages": measurements.rows,
        "regimes": regimes,
        "limitations": [
            "transport is in-memory delay simulation, no HTTP/network claims",
            "no official benchmark scores, optimizer steps, CUDA or real training",
        ],
    }
    temporary = root / "report.tmp"
    with temporary.open("w", encoding="utf-8") as output:
        json.dump(report, output, indent=2)
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary, root / "report.json")


if __name__ == "__main__":
    main()
