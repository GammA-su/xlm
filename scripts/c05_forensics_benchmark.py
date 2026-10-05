"""Bounded authored benchmark for the C05 component forensics (no real data).

``build`` scales the generated Mix-01-shaped flow (``scripts.select_benchmark``) and runs a
real authored C05 over it, so C05 itself builds the graph:

* SYNTH rows carry seed URLs (one ``query_seed_url`` per seed article, every seventh row
  also an ``additional_seed_url`` naming the next seed): a transitive co-citation chain,
  one giant SYNTH component excluded through a single planted direct hit;
* SYNTH text is unique per row and about 3 KB (duplicate groups ~= rows, like production);
* about 70 % of Common Pile rows embed the benchmark prompt: a Gutenberg-style
  direct-hit distribution with no propagation;
* the flow's planted cross-allocation exact copies attach other-source passengers.

``run`` times one forensic configuration in a fresh process (progress off).
Generated text only; synthetic trust key; no G:/X:, network or protected material.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import psutil
from scripts import c05_synthetic_flow as flow
from scripts import select_benchmark as bench
from scripts.c05_authored_pilot import KEY, PROMPT

SEED_ROWS = 30  # rows per seed article
SYNTH_REPEAT = 28  # unique words repeated to about 3 KB per SYNTH row


def build(root: Path, documents: int, synth_rows: int) -> dict[str, Any]:
    os.environ[flow.KEY_ENV] = KEY
    bench._scale(documents)
    flow.ALLOCATIONS = [
        (c, s, v, u, synth_rows * flow.TARGETS_PER_RECORD if c == "synth_en_explanations" else q)
        for c, s, v, u, q in flow.ALLOCATIONS
    ]
    seeds = max(60, synth_rows // SEED_ROWS)
    state: dict[str, int] = {}
    original = flow.doc

    def long_text(number: int) -> str:
        return " ".join([flow.text(number)] * SYNTH_REPEAT)

    def doc(number: int, source: str, text: str, metadata: dict[str, Any] | None = None) -> Any:
        if source == "synth":
            state.setdefault("first", number)
            if PROMPT in text:
                metadata = {"query_seed_url": f"https://en.wikipedia.org/wiki/Seed_{0}"}
            else:
                if text == flow.text(number):
                    text = long_text(number)
                seed = number % seeds
                metadata = {
                    "query_seed_url": f"https://en.wikipedia.org/wiki/Seed_{seed}",
                    "exercise": "rag",
                }
                if number % 37 == 5:
                    metadata = {"exercise": "memorization"}
                elif number % 7 == 0:
                    metadata["additional_seed_url"] = (
                        f"https://en.wikipedia.org/wiki/Seed_{(seed + 1) % seeds}"
                    )
        elif source == "wiki_rewrite" and "first" in state and text == flow.text(state["first"]):
            text = long_text(state["first"])  # keep the cross-source exact copy exact
        elif source == "common_pile" and number % 10 < 7 and PROMPT not in text:
            text = f"{text} {PROMPT}"
        return original(number, source, text, metadata)

    flow.doc = doc
    started = time.perf_counter()
    paths = flow.prepare(root)
    corpus_seconds = time.perf_counter() - started
    plan_path = bench._plan(root, documents + synth_rows)
    started = time.perf_counter()
    bench._c05(root, plan_path)
    summary = {
        "documents": sum(1 for _ in _ledger_rows(root)),
        "synth_rows": synth_rows,
        "seeds": seeds,
        "corpus_seconds": round(corpus_seconds, 1),
        "c05_seconds": round(time.perf_counter() - started, 1),
        "plan": str(plan_path.relative_to(root)),
        "quotas": str(paths["quotas"].relative_to(root)),
    }
    (root / "forensics-benchmark.json").write_text(json.dumps(summary, sort_keys=True) + "\n")
    return summary


def _ledger_rows(root: Path) -> Any:
    path = next((root / "scratch").rglob("decisions.jsonl"))
    with path.open("rb") as stream:
        yield from stream


class Sampler:
    """Process-tree peak RSS and CPU seconds of one child (display only)."""

    def __init__(self, pid: int) -> None:
        self.process = psutil.Process(pid)
        self.peak = 0
        self.cpu: dict[int, float] = {}
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        while not self.stop.wait(0.2):
            try:
                tree = [self.process, *self.process.children(recursive=True)]
            except psutil.Error:
                return
            rss = 0
            for process in tree:
                try:
                    rss += process.memory_info().rss
                    times = process.cpu_times()
                    self.cpu[process.pid] = times.user + times.system
                except psutil.Error:
                    continue
            self.peak = max(self.peak, rss)


def run(root: Path, module: str, workers: int | None, extra: list[str]) -> dict[str, Any]:
    plan = root / json.loads((root / "forensics-benchmark.json").read_text())["plan"]
    command = [sys.executable, "-m", module, "--plan", str(plan), "--read-corpus"]
    command += ["--focus-allocation", "common_pile_prose/common_pile_prose/project_gutenberg"]
    command += ["--focus-allocation", "synth_en_explanations/default/-"]
    command += ["--benchmark-index", str(root / "prepared" / "index.jsonl"), *extra]
    if workers is not None:
        command += ["--workers", str(workers), "--no-progress"]
    env = {**os.environ, "XLM_FORENSICS_BENCH_SALT": "bench-salt"}
    command += ["--salt-env", "XLM_FORENSICS_BENCH_SALT"]
    started = time.perf_counter()
    child = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    sampler = Sampler(child.pid)
    sampler.thread.start()
    stdout, stderr = child.communicate()
    sampler.stop.set()
    sampler.thread.join()
    wall = time.perf_counter() - started
    if child.returncode != 0:
        raise RuntimeError(f"{module} exited {child.returncode}: {stderr[-400:]!r}")
    cpu = sum(sampler.cpu.values())
    return {
        "module": module,
        "workers": workers,
        "wall_seconds": round(wall, 2),
        "cpu_seconds": round(cpu, 1),
        "cpu_cores": round(cpu / wall, 2),
        "peak_tree_rss_mib": round(sampler.peak / 2**20, 1),
        "report_sha256": hashlib.sha256(stdout).hexdigest(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    making = commands.add_parser("build")
    making.add_argument("--root", type=Path, required=True)
    making.add_argument("--documents", type=int, default=700_000)
    making.add_argument("--synth-rows", type=int, default=300_000)
    timing = commands.add_parser("run")
    timing.add_argument("--root", type=Path, required=True)
    timing.add_argument("--module", default="scripts.c05_component_forensics")
    timing.add_argument("--workers", type=int)
    args = parser.parse_args()
    if args.command == "build":
        print(json.dumps(build(args.root, args.documents, args.synth_rows), sort_keys=True))
    else:
        print(json.dumps(run(args.root, args.module, args.workers, []), sort_keys=True))


if __name__ == "__main__":
    main()
