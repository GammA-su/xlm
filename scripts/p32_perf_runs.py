"""One bounded controller/job at a time; immutable named evidence, no retries."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

import psutil

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts/p32-perf-closeout"


def run(name: str, arguments: list[str]) -> None:
    log = OUT / f"{name}.log"
    if log.exists():
        raise ValueError("Fresh evidence name required")
    argv = [sys.executable, *arguments]
    started, peak = time.monotonic(), 0
    with log.open("xb") as stream:
        child = subprocess.Popen(
            argv,
            cwd=ROOT,
            stdout=stream,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        root = psutil.Process(child.pid)
        while child.poll() is None:
            owned = [root]
            try:
                owned += root.children(recursive=True)
                peak = max(peak, sum(p.memory_info().rss for p in owned))
            except psutil.Error:
                pass
            if time.monotonic() - started > 1800 or peak > 24 * 1024**3:
                for process in reversed(owned):
                    try:
                        process.kill()
                    except psutil.NoSuchProcess:
                        pass
                raise RuntimeError("1800 s / 24 GiB experiment bound exceeded")
            time.sleep(0.5)
        code = child.wait()
    result = dict(
        argv=argv, exit=code, wall_seconds=time.monotonic() - started, peak_tree_rss_bytes=peak
    )
    (OUT / f"{name}.run.json").write_text(json.dumps(result, indent=2))
    print(name, json.dumps(result), flush=True)
    if code:
        raise SystemExit(code)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("focused", "related", "crashes", "gate", "pipeline"))
    args = parser.parse_args()
    if Path.cwd().resolve() != Path("G:/Project/xlm-p32-perf-closeout"):
        raise ValueError("Closeout worktree only")
    import p32_heavy_runs as tests

    tests.OUT = OUT
    if args.mode == "related":
        raise SystemExit(
            tests.run(
                "related",
                [
                    "tests/test_production_ingest.py",
                    "tests/test_selected_record_encoding.py",
                    "tests/test_selected_record_concurrency.py",
                    "tests/test_opus_review_accounting.py",
                    "tests/test_opus_review_journal.py",
                    "tests/test_opus_review_encoding.py",
                    "tests/test_opus_review_prefix.py",
                    "-m",
                    "not performance and not network",
                    "-n",
                    "0",
                ],
            )
        )
    if args.mode == "focused":
        files = [str(p) for p in Path("tests").glob("test_acquisition*.py")]
        files += [
            "tests/test_p32_publication.py",
            "tests/test_p32_orphans.py",
            "tests/test_opus_review_equivalence.py",
        ]
        raise SystemExit(
            tests.run("focused", files + ["-m", "not performance and not network", "-n", "0"])
        )
    if args.mode == "crashes":
        for name in ("early", "mature", "selected", "parallel"):
            script = (
                "review_opus_crashes.py"
                if name in ("early", "mature")
                else "review_opus_selected_crashes.py"
            )
            arguments = [f"scripts/{script}", "--output", str(OUT / f"crashes-{name}")]
            if name == "mature":
                arguments += ["--mature"]
            if name == "parallel":
                arguments += ["--parallel-publication"]
            run(f"crashes-{name}", arguments)
        return
    if args.mode in ("gate", "pipeline"):
        assert json.loads((OUT / "focused.run.json").read_text())["exit"] == 0
        assert json.loads((OUT / "related.run.json").read_text())["exit"] == 0
        assert json.loads((OUT / "exact-comparison.json").read_text())["equal"]
        for name, count in (("early", 44), ("mature", 44), ("selected", 20), ("parallel", 4)):
            rows = json.loads((OUT / f"crashes-{name}/report.json").read_text())
            assert len(rows) == count and all(row["restart_exit"] == 0 for row in rows)
            assert not list((OUT / f"crashes-{name}").rglob("*.tmp")), "orphan survived recovery"
    if args.mode == "gate":
        from p32_gate import LEGS

        for name, (expression, workers, distribution) in LEGS.items():
            selection = ["-m", expression, "-n", str(workers)]
            if distribution:
                selection += ["--dist", distribution, "--max-worker-restart=0"]
            code = tests.run(f"gate-{name}", selection)
            if code:
                raise SystemExit(code)
    elif args.mode == "pipeline":
        reference = OUT / "reference-100k"
        destination = OUT / "pipeline-100k"
        run(
            "pipeline",
            [
                str(OUT / "methods/benchmark_pipeline.py"),
                "--output",
                str(destination),
                "--documents",
                "100000",
                "--workers",
                "6",
                "--token-workers",
                "8",
                "--dedup-workers",
                "8",
                "--max-seconds",
                "1800",
                "--acquisition-loopback",
                "--source-fixture",
                str(reference / "source.jsonl.gz"),
                "--tokenizer",
                str(reference / "tokenizer"),
            ],
        )
        run(
            "pipeline-exact",
            [
                str(OUT / "methods/compare_pipeline.py"),
                str(reference),
                str(destination),
                "--output",
                str(OUT / "pipeline-comparison.json"),
            ],
        )


if __name__ == "__main__":
    main()
