"""Bounded authored long-document scaling and exact pinned-reference checks."""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import subprocess
import sys
import time
import tracemalloc
import types
from pathlib import Path
from typing import Any

from benchmark_pipeline import WORDS, Measurements
from benchmark_token_path import fixture_document

from xlm.data.cleaning.features import TextFeatures
from xlm.data.cleaning.language import LanguageFilter
from xlm.data.cleaning.pii import PiiSecretFilter
from xlm.data.cleaning.repetition import RepetitionFilter

BASELINE = "5a43a816505fa374371bc10fdea4eacc64186e6f"


def reference(module: str) -> Any:
    name = f"p29c_reference_{module}"
    loaded = types.ModuleType(name)
    sys.modules[name] = loaded
    source = subprocess.check_output(
        ["git", "show", f"{BASELINE}:src/xlm/data/cleaning/{module}.py"]
    )
    exec(compile(source, module, "exec"), loaded.__dict__)
    return loaded


def normalized_result(result: Any) -> dict[str, Any]:
    return {
        "action": result.action,
        "reasons": result.reasons,
        "metrics": result.metrics.to_dict(),
        "document": result.document.to_dict() if result.document else None,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    measurement = Measurements(args.output, 180, 1, max_artifact_bytes=1024**2)
    transforms = {
        "language": (reference("language").LanguageFilter(), LanguageFilter()),
        "repetition": (reference("repetition").RepetitionFilter(), RepetitionFilter()),
        "pii": (reference("pii").PiiSecretFilter(), PiiSecretFilter()),
    }
    rows = []
    with measurement.stage("long_documents"):
        for size in (1024, 10240, 102400, 1024**2):
            rng = random.Random(29)
            clean = " ".join(rng.choices(WORDS + ["the"] * 30, k=size // 3))[:size]
            for kind, text in (
                ("clean", clean),
                (
                    "repetitive",
                    ("the repeated paragraph about the river\n" * (size // 20 + 1))[:size],
                ),
                (
                    "noisy",
                    ("@@ 54321 %# / : 東京 \n" * (size // 10 + 1))
                    .encode()[:size]
                    .decode("utf-8", errors="ignore"),
                ),
            ):
                doc = fixture_document(text, size)
                for name, pair in transforms.items():
                    expected = None
                    for mode, transform in zip(("before", "after"), pair, strict=True):
                        durations = []
                        for _ in range(3):
                            features = TextFeatures()
                            start, cpu = time.perf_counter(), time.process_time()
                            result = transform.apply(doc, features)
                            durations.append(
                                {
                                    "wall": time.perf_counter() - start,
                                    "cpu": time.process_time() - cpu,
                                }
                            )
                        actual = normalized_result(result)
                        if expected is None:
                            expected = actual
                        else:
                            assert actual == expected, (name, size, kind)
                        tracemalloc.start()
                        transform.apply(doc, TextFeatures())
                        _, peak = tracemalloc.get_traced_memory()
                        tracemalloc.stop()
                        rows.append(
                            {
                                "bytes": doc.utf8_byte_count,
                                "kind": kind,
                                "filter": name,
                                "mode": mode,
                                "trials": durations,
                                "python_peak_bytes": peak,
                                "result_digest": hashlib.sha256(
                                    json.dumps(actual, sort_keys=True).encode()
                                ).hexdigest(),
                            }
                        )
    (args.output / "report.json").write_text(
        json.dumps({"rows": rows, "measurement": measurement.rows, "baseline": BASELINE}, indent=2),
        encoding="utf-8",
    )
    print(f"Exact long-document cases: {len(rows) // 2}")


if __name__ == "__main__":
    main()
