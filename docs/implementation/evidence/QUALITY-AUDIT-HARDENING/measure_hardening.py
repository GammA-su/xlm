"""Hardening measurements (authored data only): run-detector scaling, detector profile,
overlay staging rate. Writes JSON/text evidence next to this file. No real corpus.

    uv run --offline --locked --no-sync python \
        docs/implementation/evidence/QUALITY-AUDIT-HARDENING/measure_hardening.py
"""

from __future__ import annotations

import cProfile
import hashlib
import io
import json
import pstats
import random
import time
from pathlib import Path
from typing import Any

import numpy as np

from xlm.core.contracts import CanonicalDocument
from xlm.data.evidence_v2 import canonical
from xlm.data.quality import detectors
from xlm.data.quality.overlay import IDENTITY, doc_digest
from xlm.data.quality.policy import METRICS
from xlm.data.quality.scan import ChunkTask, process_chunk

HERE = Path(__file__).resolve().parent


def run_scaling() -> list[dict[str, Any]]:
    """Astra's adversary: N distinct CJK code points, each repeated 8 times."""
    rows = []
    for distinct in (1000, 2000, 4000, 8000, 16000, 32000):
        text = "".join(chr(0x4E00 + i) * 8 for i in range(distinct))
        codes = detectors.code_points(text)
        trials = []
        for _ in range(3):
            values: list[Any] = [None] * len(METRICS)
            started = time.perf_counter()
            detectors._runs(text, codes, values)
            trials.append(time.perf_counter() - started)
        full = []
        for _ in range(3):
            started = time.perf_counter()
            detectors.analyze(text, len(text.encode("utf-8")))
            full.append(time.perf_counter() - started)
        rows.append(
            {
                "distinct_code_points": distinct,
                "utf8_bytes": len(text.encode("utf-8")),
                "best_run_detector_seconds": round(min(trials), 6),
                "best_full_analyze_seconds": round(min(full), 6),
            }
        )
    return rows


def corpus_chunk(count: int) -> bytes:
    rng = random.Random(5)
    words = [
        "".join(rng.choice("etaoinshrdlucmfw") for _ in range(rng.randint(2, 9)))
        for _ in range(4000)
    ]
    lines = []
    for n in range(count):
        paragraphs = []
        for _ in range(rng.randint(2, 8)):
            paragraphs.append(
                " ".join(rng.choice(words) for _ in range(rng.randint(40, 160))) + "."
            )
        text = "\n\n".join(paragraphs)
        row = CanonicalDocument(
            doc_id=f"profile:{n}",
            source_id="authored",
            source_revision="authored",
            source_file="authored",
            source_row=n + 1,
            raw_hash="0" * 64,
            clean_hash="0" * 64,
            text=text,
            utf8_byte_count=len(text.encode("utf-8")),
            language="en",
            language_confidence=1.0,
            document_kind="prose",
            source_metadata={"fasttext_english": 0.9},
            parent_ids=[],
            license_reference="authored",
            transform_log=[],
            quality_reasons=[],
            cluster_ids={},
            split="train",
        ).to_dict()
        lines.append(canonical.canonical_bytes(row) + b"\n")
    return b"".join(lines)


def profile_chunk(data: bytes) -> tuple[str, dict[str, Any]]:
    rows = data.count(b"\n")
    task = ChunkTask(0, "profile", 1, 0, data, None, 64 * 1024**2, True)
    started = time.perf_counter()
    process_chunk(task)
    plain = time.perf_counter() - started
    # Same chunk with every row C05-kept: adds identity verification.
    identity = np.zeros(rows, dtype=IDENTITY)
    offset = 0
    for n in range(rows):
        end = data.index(b"\n", offset)
        body = data[offset:end]
        row = json.loads(body)
        identity["doc"][n] = np.frombuffer(doc_digest(row["doc_id"]), dtype=np.uint8)
        identity["content"][n] = np.frombuffer(hashlib.sha256(body).digest()[:16], np.uint8)
        identity["bytes"][n] = row["utf8_byte_count"]
        offset = end + 1
    kept = ChunkTask(
        0,
        "profile",
        1,
        0,
        data,
        np.ones(rows, bool).tobytes(),
        64 * 1024**2,
        True,
        identity.tobytes(),
    )
    started = time.perf_counter()
    process_chunk(kept)
    with_overlay = time.perf_counter() - started
    profiler = cProfile.Profile()
    profiler.enable()
    process_chunk(task)
    profiler.disable()
    stream = io.StringIO()
    pstats.Stats(profiler, stream=stream).strip_dirs().sort_stats("tottime").print_stats(25)
    return stream.getvalue(), {
        "rows": rows,
        "bytes": len(data),
        "plain_seconds": round(plain, 3),
        "plain_mb_per_s_one_core": round(len(data) / 1e6 / plain, 2),
        "with_kept_identity_seconds": round(with_overlay, 3),
        "identity_overhead_ratio": round(with_overlay / plain, 3),
    }


def overlay_staging_rate() -> dict[str, Any]:
    """Per-row work of overlay staging (strict parse + per-file field writes)."""
    rows = 200_000
    documents = rows
    identity = np.zeros(documents, dtype=IDENTITY)
    kept = np.zeros(documents, dtype=bool)
    lines = [
        canonical.canonical_bytes(
            {
                "doc_id": f"x:{n:09d}",
                "source_id": "s",
                "component": "c",
                "view": "v",
                "file": "f.jsonl",
                "row": n + 1,
                "content": hashlib.sha256(str(n).encode()).hexdigest(),
                "bytes": 5000,
                "duplicate_group": "d" * 64,
                "lineage_group": "l" * 64,
                "decision": "kept",
                "split": "train",
                "quick": False,
                "upstream_component": None,
            }
        )
        for n in range(rows)
    ]
    started = time.perf_counter()
    for line in lines:
        row = canonical.loads_bytes_strict(line)
        number = row["row"]
        kept[number - 1] = True
        identity["doc"][number - 1] = np.frombuffer(doc_digest(row["doc_id"]), dtype=np.uint8)
        identity["content"][number - 1] = np.frombuffer(
            bytes.fromhex(row["content"])[:16], dtype=np.uint8
        )
        identity["bytes"][number - 1] = row["bytes"]
    seconds = time.perf_counter() - started
    rate = rows / seconds
    return {
        "rows": rows,
        "row_bytes": len(lines[0]),
        "rows_per_s": round(rate, 1),
        "PROJECTION_seconds_per_million_kept_rows": round(1e6 / rate, 1),
        "algorithm": (
            "one pass; each row indexes its own file array (O(kept rows)), no per-file masks"
        ),
    }


def main() -> None:
    scaling = run_scaling()
    profile_text, profile = profile_chunk(corpus_chunk(1500))
    staging = overlay_staging_rate()
    result = {
        "label": "AUTHORED MEASUREMENTS (not a real-corpus run)",
        "run_detector_scaling": scaling,
        "chunk_profile": profile,
        "overlay_staging": staging,
    }
    (HERE / "measurements.json").write_text(
        json.dumps(result, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    (HERE / "kernel-profile.txt").write_text(profile_text, encoding="utf-8", newline="\n")
    print(json.dumps(result, indent=1, sort_keys=True))


if __name__ == "__main__":
    main()
