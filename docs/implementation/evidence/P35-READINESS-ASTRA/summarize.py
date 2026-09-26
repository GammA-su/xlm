"""Collect bounded local XML and worker resource observations, not benchmark scores."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[4]
EVIDENCE = Path(__file__).resolve().parent


def main() -> None:
    suites: dict[str, Any] = {}
    for path in sorted(EVIDENCE.glob("*.xml")):
        root = ET.parse(path).getroot()
        groups = [root] if root.tag == "testsuite" else root.findall("testsuite")
        counts = {
            key: sum(int(g.get(key, "0")) for g in groups)
            for key in ("tests", "failures", "errors", "skipped")
        }
        suites[path.stem] = {
            **counts,
            "seconds": sum(float(g.get("time", "0")) for g in groups),
            "runtime_nodes": len(
                [
                    t
                    for t in root.iter("testcase")
                    if t.get("classname", "") == "tests.test_p35_readiness_runtime"
                ]
            ),
            "failed_nodes": [
                t.get("classname", "") + "::" + t.get("name", "")
                for t in root.iter("testcase")
                if t.find("failure") is not None or t.find("error") is not None
            ],
        }
    resources = []
    files, disk = 0, 0
    for path in (ROOT / ".astra-scratch").rglob("*"):
        if not path.is_file():
            continue
        files += 1
        assert files <= 100_000, "scratch inventory bound"
        disk += path.stat().st_size
        if path.name == "process.json" and path.stat().st_size < 100_000:
            data = json.loads(path.read_text(encoding="utf-8"))
            resources.append(
                {
                    "path": path.relative_to(ROOT).as_posix(),
                    **{
                        k: data.get(k)
                        for k in (
                            "elapsed_seconds",
                            "sampled_peak_tree_rss_bytes",
                            "sampled_peak_work_bytes",
                            "output_bytes",
                            "reason",
                            "exit_code",
                        )
                    },
                }
            )
    report = {
        "suites": suites,
        "scratch_files": files,
        "scratch_logical_bytes": disk,
        "worker_measurements": resources,
        "max_sampled_worker_tree_rss_bytes": max(
            (r["sampled_peak_tree_rss_bytes"] or 0 for r in resources), default=0
        ),
        "max_sampled_worker_work_bytes": max(
            (r["sampled_peak_work_bytes"] or 0 for r in resources), default=0
        ),
        "scope": "retained worker process.json samples; not an aggregate session peak",
    }
    (EVIDENCE / "summary.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "worker_measurements"}, indent=2))


if __name__ == "__main__":
    main()
