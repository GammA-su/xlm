"""Bounded quadratic-regex diagnostic; authored punctuation, no actual credentials."""

from __future__ import annotations

import json
import multiprocessing
import time
from pathlib import Path
from typing import Any

from benchmark_cleaning_long import reference
from benchmark_pipeline import Measurements
from benchmark_token_path import fixture_document

from xlm.data.cleaning.features import TextFeatures
from xlm.data.cleaning.pii import PiiSecretFilter


def worker(connection: Any, size: int, before: bool) -> None:
    transform = reference("pii").PiiSecretFilter() if before else PiiSecretFilter()
    doc = fixture_document(("a." * (size // 2)), 0)
    connection.send({"ready": True})
    start = time.perf_counter()
    result = transform.apply(doc, TextFeatures())
    elapsed = time.perf_counter() - start
    connection.send(
        {
            "seconds": elapsed,
            "action": result.action,
            "reasons": result.reasons,
            "metrics": result.metrics.to_dict(),
        }
    )
    connection.close()


def main() -> None:
    root = Path("artifacts/perf/p29c-pii-nearmiss")
    root.mkdir(parents=True, exist_ok=False)
    measure = Measurements(root, 120, 2, max_artifact_bytes=1024**2)
    ctx = multiprocessing.get_context("spawn")
    rows = []
    with measure.stage("pii_nearmiss"):
        for size in (1024, 10240, 102400, 1024**2):
            expected = None
            for before in (True, False):
                receiver, sender = ctx.Pipe(duplex=False)
                process = ctx.Process(target=worker, args=(sender, size, before))
                process.start()
                sender.close()
                try:
                    if not receiver.poll(15) or receiver.recv() != {"ready": True}:
                        raise RuntimeError("benchmark worker failed to initialize")
                    if receiver.poll(3):
                        result = receiver.recv()
                        semantic = {k: v for k, v in result.items() if k != "seconds"}
                        if expected is not None:
                            assert semantic == expected
                        expected = semantic
                        rows.append(
                            {"bytes": size, "before": before, "status": "MEASURED", **result}
                        )
                    else:
                        rows.append(
                            {
                                "bytes": size,
                                "before": before,
                                "status": "TIMEOUT",
                                "limit_seconds": 3,
                            }
                        )
                finally:
                    if process.is_alive():
                        process.terminate()
                    process.join(timeout=5)
                    if process.is_alive():
                        process.kill()
                        process.join(timeout=5)
                    process.close()
                    receiver.close()
    (root / "report.json").write_text(
        json.dumps({"rows": rows, "measurement": measure.rows}, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            [{k: v for k, v in row.items() if k not in ("metrics", "reasons")} for row in rows],
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
