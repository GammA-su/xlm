"""Bounded AUTHORED throughput benchmark for the Phase-A quality audit.

Generates a deterministic synthetic corpus (no real text) shaped like the Mix-01
canonical files (about 6.9 KB per JSONL row, 5.4 KB of text), runs the real audit at
the requested worker counts and reports measured throughput plus a PROJECTION for the
real input manifest totals. A projection is arithmetic on an authored measurement,
not a measurement of the real corpus or of the operator's disks.

    uv run --offline --locked --no-sync python scripts/quality_audit_benchmark.py \
        --root <scratch-dir> --output <result.json> --target-mib 256 --workers 1 4 8
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import time
from pathlib import Path
from typing import Any

import numpy as np

from xlm.core.contracts import CanonicalDocument
from xlm.data.evidence_v2 import canonical
from xlm.data.quality.runner import Limits, run_audit

REAL_DOCUMENTS = 15_097_174
REAL_FILE_BYTES = 104_506_534_003
REAL_CANONICAL_BYTES = 81_859_652_239
MAX_TARGET_MIB = 2048
FILES = 8
SEED = 20261004


def _vocabulary(rng: np.random.Generator) -> list[str]:
    letters = np.array(list("etaoinshrdlucmfwypvbgkjqxz"))
    weights = np.linspace(2.0, 0.1, letters.size)
    weights /= weights.sum()
    words = []
    for _ in range(20_000):
        size = int(rng.integers(2, 11))
        words.append("".join(rng.choice(letters, size=size, p=weights)))
    return words


def _sentences(rng: np.random.Generator, words: list[str], count: int) -> list[str]:
    out = []
    for _ in range(count):
        picks = rng.integers(0, len(words), size=int(rng.integers(6, 24)))
        sentence = " ".join(words[i] for i in picks)
        out.append(sentence[0].upper() + sentence[1:] + "...?,"[int(rng.integers(0, 5))])
    return out


def _document(kind: str, rng: np.random.Generator, sentences: list[str], size: int) -> str:
    def prose(target: int) -> str:
        parts: list[str] = []
        length = 0
        while length < target:
            paragraph = " ".join(
                sentences[i] for i in rng.integers(0, len(sentences), size=int(rng.integers(3, 9)))
            )
            parts.append(paragraph)
            length += len(paragraph) + 2
        return "\n\n".join(parts)

    if kind == "prose":
        return prose(size)
    if kind == "code":
        body = []
        for n in range(max(size // 40, 3)):
            body.append(f"    value_{n} = compute(value_{n - 1}, {n});")
        return "int main() {\n" + "\n".join(body) + "\n    return 0;\n}\n"
    if kind == "html":
        cells = "".join(
            f"<li><a href='/p/{n}'>{s[:30]}</a></li>"
            for n, s in enumerate(
                sentences[i] for i in rng.integers(0, len(sentences), size=max(size // 80, 2))
            )
        )
        head = "<!DOCTYPE html><html><head><title>t</title></head>"
        return f"{head}<body><ul>{cells}</ul></body></html>"
    if kind == "table":
        rows = [f"| {n} | {n * 3.5:.1f} | {n % 7} |" for n in range(max(size // 20, 2))]
        return "| a | b | c |\n|---|---|---|\n" + "\n".join(rows)
    if kind == "ocr":
        text = prose(size)
        lines = [text[i : i + 60] for i in range(0, len(text), 60)]
        out = []
        for n, line in enumerate(lines):
            out.append(line + ("-" if n % 7 == 0 else ""))
            if n % 40 == 39:
                out += ["", f"Journal of Synthetic Studies {n // 40}", str(n // 40 + 1), ""]
        return "\n".join(out)
    if kind == "web":
        return (
            "Home\nAbout us\nAccept all cookies\nPrivacy Policy\n"
            + prose(size)
            + "\n© 2024 Synthetic. All rights reserved.\nFollow us on Twitter\n"
        )
    if kind == "loop":
        unit = sentences[int(rng.integers(0, len(sentences)))] + " "
        return unit * max(size // len(unit), 2)
    return sentences[int(rng.integers(0, len(sentences)))][:12]


KINDS = (
    ("prose", 0.70),
    ("code", 0.07),
    ("html", 0.05),
    ("table", 0.04),
    ("ocr", 0.06),
    ("web", 0.05),
    ("loop", 0.02),
    ("tiny", 0.01),
)


def generate(root: Path, target_bytes: int) -> Path:
    rng = np.random.default_rng(SEED)
    words = _vocabulary(rng)
    sentences = _sentences(rng, words, 5000)
    names = [k for k, _ in KINDS]
    probabilities = np.array([p for _, p in KINDS])
    data = root / "data"
    files = []
    per_file = target_bytes // FILES
    for f in range(FILES):
        path = data / "canonical" / f"c{f % 4}" / "v" / f"f{f}" / "documents.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        written = rows = text_bytes = 0
        digest = hashlib.sha256()
        with path.open("wb") as stream:
            while written < per_file:
                kind = str(rng.choice(names, p=probabilities))
                size = int(min(rng.lognormal(mean=8.1, sigma=0.9), 400_000))
                text = _document(kind, rng, sentences, size)
                rows += 1
                encoded = len(text.encode("utf-8"))
                row = CanonicalDocument(
                    doc_id=f"bench:{f}:{rows}",
                    source_id="authored-benchmark",
                    source_revision="authored",
                    source_file=f"f{f}",
                    source_row=rows,
                    raw_hash="0" * 64,
                    clean_hash="0" * 64,
                    text=text,
                    utf8_byte_count=encoded,
                    language="en",
                    language_confidence=1.0,
                    document_kind="prose",
                    source_metadata={"mix01_component": f"c{f % 4}", "fasttext_english": 0.9},
                    parent_ids=[],
                    license_reference="authored",
                    transform_log=[{"transform": "nfc_and_newline_normalize", "version": "1.0"}],
                    quality_reasons=[],
                    cluster_ids={},
                    split="train",
                ).to_dict()
                line = canonical.canonical_bytes(row) + b"\n"
                stream.write(line)
                digest.update(line)
                written += len(line)
                text_bytes += encoded
        files.append(
            {
                "path": path.relative_to(data).as_posix(),
                "source_key": f"c{f % 4}",
                "component": f"c{f % 4}",
                "view": "v",
                "upstream_component": None,
                "documents_sha256": digest.hexdigest(),
                "file_bytes": written,
                "canonical_bytes": text_bytes,
                "documents": rows,
            }
        )
    manifest: dict[str, Any] = {
        "kind": "authored_c05_input",
        "data_root": str(data.resolve()),
        "files": files,
    }
    manifest["digest"] = canonical.self_digest(manifest)
    target = root / "manifest.json"
    canonical.write_canonical_json(target, manifest)
    return target


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--target-mib", type=int, default=256)
    parser.add_argument("--workers", type=int, nargs="+", default=[1, 2, 4, 8])
    args = parser.parse_args()
    if not 0 < args.target_mib <= MAX_TARGET_MIB:
        raise SystemExit("--target-mib outside (0, 2048]")
    if args.root.exists():
        raise SystemExit("--root must be a new directory")
    started = time.monotonic()
    manifest = generate(args.root, args.target_mib * 1024**2)
    generation = time.monotonic() - started
    body = json.loads(manifest.read_bytes())
    totals = {
        "documents": sum(f["documents"] for f in body["files"]),
        "file_bytes": sum(f["file_bytes"] for f in body["files"]),
        "canonical_bytes": sum(f["canonical_bytes"] for f in body["files"]),
    }
    runs = []
    digests = set()
    for workers in args.workers:
        output = args.root / f"audit-w{workers}"
        limits = Limits(
            workers=workers,
            max_rss_bytes=16 * 1024**3,
            free_reserve_bytes=1024**3,
            max_output_bytes=2 * 1024**3,
            line_ceiling=64 * 1024**2,
            deadline_seconds=6 * 3600,
        )
        result = run_audit(manifest, output, limits=limits, progress_interval=None)
        scan = result["scan"]
        digests.add(result["result_digest"])
        seconds = scan["scan_seconds"]
        runs.append(
            {
                "workers": workers,
                "scan_seconds": seconds,
                "wall_seconds": result["wall_seconds"],
                "file_mb_per_s": scan["file_mb_per_s"],
                "documents_per_s": scan["documents_per_s"],
                "peak_process_tree_rss_gib": round(scan["peak_process_tree_rss_bytes"] / 2**30, 3),
                "PROJECTION_real_scan_hours_by_bytes": round(
                    REAL_FILE_BYTES / (scan["file_mb_per_s"] * 1e6) / 3600, 2
                ),
                "PROJECTION_real_scan_hours_by_documents": round(
                    REAL_DOCUMENTS / scan["documents_per_s"] / 3600, 2
                ),
            }
        )
        shutil.rmtree(output / "units")
    report = {
        "kind": "xlm_quality_audit_benchmark_v1",
        "label": "AUTHORED SYNTHETIC CORPUS MEASUREMENT + PROJECTION (not a real-corpus run)",
        "machine": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "logical_cpus": os.cpu_count(),
        },
        "corpus": {
            **totals,
            "files": len(body["files"]),
            "generation_seconds": round(generation, 1),
        },
        "mean_jsonl_row_bytes": round(totals["file_bytes"] / totals["documents"], 1),
        "real_target": {
            "documents": REAL_DOCUMENTS,
            "file_bytes": REAL_FILE_BYTES,
            "canonical_bytes": REAL_CANONICAL_BYTES,
            "mean_jsonl_row_bytes": round(REAL_FILE_BYTES / REAL_DOCUMENTS, 1),
        },
        "worker_independent_result_digest": sorted(digests),
        "runs": runs,
        "projection_caveats": [
            "authored text mix, not the real detector-cost mix",
            "corpus generated on this machine; the real corpus is read from the operator's G: SSD",
            "projection excludes overlay membership streaming and final aggregation",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    print(json.dumps(report, indent=1, sort_keys=True))


if __name__ == "__main__":
    main()
