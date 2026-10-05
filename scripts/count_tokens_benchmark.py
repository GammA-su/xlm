"""Bounded, reproducible count-tokens benchmark over a generated (authored) C05 chain.

``build`` writes a seeded generated corpus with a heavy-tailed file-size mix (like the
real cleaned corpus: many small files and a few large ones), planted exact duplicates
and benchmark-contaminated rows, runs an authored C05 through the operator CLI, writes
the proof and fits a BPE tokenizer (production vocabulary size) on screened records.
``run`` times one count-tokens configuration in a fresh subprocess; ``matrix`` runs
the reference and every worker count and checks byte identity of every artifact.

Generated text only: no real corpus, proof, tokenizer, key, G:/X: or network. The
trust root is a synthetic environment key and the plan mode is ``authored``.
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
import os
import random
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import psutil
from scripts.c05_authored_pilot import KEY, PROMPT, doc
from scripts.c05_synthetic_flow import ISSUER, KEY_ENV, prepare_benchmark

from xlm.data.evidence_v2 import canonical
from xlm.data.exclusion.artifacts import ExecutionPlan
from xlm.data.exclusion.identity import implementation_identity
from xlm.data.exclusion.operator import main as operator
from xlm.data.exclusion.policy import ProductionPolicy, Resources
from xlm.data.exclusion.runner import file_sha

VOCAB = 32768
# Relative file sizes: a heavy tail like the real corpus (median ~12 MB, max ~5 GB).
FILE_WEIGHTS = [40, 18, 12, 6, 4, 3, 2, 2, *([1] * 24)]
COMPONENTS = ("essential_prose", "ultrax_ultrafineweb", "finewiki_en", "simple_stories")


def _words(rng: random.Random, count: int) -> list[str]:
    syllables = [a + b for a in "bcdfghjklmnprstvwz" for b in ("a", "e", "i", "o", "u", "ai", "ou")]
    words: set[str] = set()
    while len(words) < count:
        words.add("".join(rng.choice(syllables) for _ in range(rng.choice((1, 2, 2, 3, 3, 4)))))
    return sorted(words)


def _text(rng: random.Random, words: list[str], cumulative: list[float], size: int) -> str:
    parts: list[str] = []
    length = 0
    while length < size:
        sentence = rng.choices(words, cum_weights=cumulative, k=rng.randint(6, 22))
        sentence[0] = sentence[0].capitalize()
        if rng.random() < 0.15:
            sentence.insert(rng.randrange(len(sentence)), str(rng.randint(1, 99999)))
        if rng.random() < 0.03:
            sentence.append("café — naïve 東京")
        line = " ".join(sentence) + rng.choice((".", ".", ".", "?", "!", ";"))
        parts.append(line)
        length += len(line) + 1
        if rng.random() < 0.12:
            parts.append("\n")
    return " ".join(parts)


def build(root: Path, documents: int, seed: int) -> dict[str, Any]:
    """Corpus, authored C05 run, proof and a fitted production-size BPE tokenizer."""
    root.mkdir(parents=True, exist_ok=False)
    os.environ[KEY_ENV] = KEY
    started = time.perf_counter()
    rng = random.Random(seed)
    words = _words(rng, 30_000)
    cumulative = list(itertools.accumulate(1.0 / (n + 1) ** 1.05 for n in range(len(words))))
    data = root / "data"
    total = sum(FILE_WEIGHTS)
    files: list[dict[str, Any]] = []
    number = 0
    texts: list[str] = []
    for ordinal, weight in enumerate(FILE_WEIGHTS):
        component = COMPONENTS[ordinal % len(COMPONENTS)]
        source = f"src{ordinal:02d}"
        path = data / "canonical" / component / f"f{ordinal:02d}" / "documents.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        rows = []
        for _ in range(max(4, documents * weight // total)):
            if texts and rng.random() < 0.03:
                body = rng.choice(texts)  # exact duplicate of an earlier record
            elif rng.random() < 0.002:
                body = f"Generated wrapper {number}. {PROMPT} End."  # contaminated
            else:
                size = int(min(60_000, max(200, rng.lognormvariate(math.log(4200), 0.8))))
                body = _text(rng, words, cumulative, size)
                if len(texts) < 4096:
                    texts.append(body)
            rows.append(doc(number, source, body))
            number += 1
        raw = b"".join(canonical.canonical_bytes(r.to_dict()) + b"\n" for r in rows)
        path.write_bytes(raw)
        files.append(
            {
                "path": path.relative_to(data).as_posix(),
                "source_key": source,
                "source_id": source,
                "source_revision": "authored-revision",
                "component": component,
                "view": "default",
                "upstream_component": None,
                "source_file": "generated",
                "documents_sha256": file_sha(path),
                "file_bytes": path.stat().st_size,
                "canonical_bytes": sum(r.utf8_byte_count for r in rows),
                "documents": len(rows),
            }
        )
    sources = [
        {
            "source_key": f["source_key"],
            "seal_digest": canonical.digest([f["source_key"], [f]]),
            "source": {"source_id": f["source_id"], "revision": "authored-revision"},
        }
        for f in files
    ]
    manifest: dict[str, Any] = {
        "kind": "authored_c05_input",
        "data_root": str(data.resolve()),
        "files": files,
        "sources": sources,
    }
    manifest["digest"] = canonical.self_digest(manifest)
    canonical.write_canonical_json(root / "manifest.json", manifest)
    corpus_seconds = time.perf_counter() - started
    trust = root / "trust.json"
    canonical.write_canonical_json(trust, {ISSUER: KEY_ENV})
    prepare_benchmark(root)
    plan_path = _plan(root, manifest, documents, trust)
    c05_started = time.perf_counter()
    _run_c05(root, plan_path, trust)
    c05_seconds = time.perf_counter() - c05_started
    proof = root / "proof.json"
    _run(
        ["proof", "--plan", str(plan_path), "--trust", str(trust), "--manifest"]
        + [str(root / "manifest.json"), "--scratch", str(root / "lookup"), "--output", str(proof)]
        + ["--signer", ISSUER, "--signer-key-env", KEY_ENV]
    )
    tok_started = time.perf_counter()
    _fit_tokenizer(proof, root / "tokenizer")
    summary = {
        "documents": number,
        "files": len(files),
        "file_bytes": sum(f["file_bytes"] for f in files),
        "canonical_bytes": sum(f["canonical_bytes"] for f in files),
        "largest_file_bytes": max(f["file_bytes"] for f in files),
        "corpus_seconds": round(corpus_seconds, 1),
        "c05_seconds": round(c05_seconds, 1),
        "tokenizer_seconds": round(time.perf_counter() - tok_started, 1),
        "seed": seed,
        "vocab": VOCAB,
    }
    canonical.write_canonical_json(root / "benchmark.json", summary)
    return summary


def _run(arguments: list[str]) -> None:
    if operator(arguments):
        raise RuntimeError("operator command refused: " + arguments[0])


def _plan(root: Path, manifest: dict[str, Any], documents: int, trust: Path) -> Path:
    resources = Resources(
        free_bytes=0,
        index_bytes=8 * 1024**3,
        journal_bytes=12 * 1024**3,
        decision_bytes=1024**3,
        output_bytes=2 * 1024**3,
        benchmark_bytes=8 * 1024**2,
        scratch_bytes=64 * 1024**3,
        ram_bytes=16 * 1024**3,
        records=documents * 2,
        attempted_records=documents * 4,
        files=64,
        comparisons=10**9,
        stage_seconds=7200,
        overall_seconds=14400,
        workers=16,
    )
    values = {
        "lineage-policy": {"choice": "KNOWN_GROUP_ONLY"},
        "resources": resources.model_dump(mode="json"),
        "policy": ProductionPolicy(
            diagnostic_bytes=4 * 1024**2, quick_bytes=0, audit_bytes=1024**2
        ).model_dump(mode="json"),
    }
    signing = ["--trust", str(trust), "--issuer", ISSUER, "--key-env", KEY_ENV]
    for purpose, value in values.items():
        canonical.write_canonical_json(root / f"{purpose}-value.json", value)
        _run(
            [purpose, "freeze" if purpose == "policy" else "record", "--value"]
            + [str(root / f"{purpose}-value.json"), "--input-manifest-digest", manifest["digest"]]
            + ["--evidence-digest", canonical.digest("count-benchmark"), "--operator", "bench"]
            + ["--output", str(root / f"{purpose}.json"), *signing]
        )
    _run(
        ["plan", "--mode", "authored", "--trust", str(trust), "--manifest"]
        + [str(root / "manifest.json"), "--benchmark-receipt"]
        + [str(root / "prepared/benchmark-preparation.receipt.json")]
        + ["--lineage-policy", str(root / "lineage-policy.json"), "--resources"]
        + [str(root / "resources.json"), "--policy", str(root / "policy.json")]
        + ["--plan-root", str(root / "plans"), "--scratch", str(root / "scratch")]
        + ["--output", str(root / "output")]
    )
    plan_path = root / "plans/p0001.json"
    plan = ExecutionPlan.model_validate_json(plan_path.read_bytes())
    _run(
        ["authorize", "--plan", str(plan_path), "--plan-digest", plan.identity()]
        + ["--output", str(root / "authorization.json"), *signing]
    )
    return plan_path


def _run_c05(root: Path, plan_path: Path, trust: Path) -> None:
    _run(
        ["run", "--plan", str(plan_path), "--authorization", str(root / "authorization.json")]
        + ["--benchmark-receipt", str(root / "prepared/benchmark-preparation.receipt.json")]
        + ["--index", str(root / "prepared/index.jsonl"), "--trust", str(trust)]
        + ["--issuer", ISSUER, "--key-env", KEY_ENV, "--no-progress"]
    )
    _run(["verify", "--plan", str(plan_path), "--trust", str(trust)])


def _fit_tokenizer(proof: Path, directory: Path) -> None:
    """A production-size BPE fitted only on screened train records (bounded sample)."""
    from xlm.data.acquisition.source_run import write_once
    from xlm.data.exclusion.selection import iter_plan_documents
    from xlm.data.exclusion.transport import open_gate
    from xlm.tokenizers.bpe import ByteLevelBPETokenizer

    with open_gate(proof, allow_authored=True) as gate:
        assert gate is not None
        budget = [64 * 1024**2]

        def sample() -> Any:
            for _, document in iter_plan_documents(gate):
                row = gate.lookup(document.doc_id)
                if row is not None and row[2] == "train" and budget[0] > 0:
                    budget[0] -= document.utf8_byte_count
                    yield document

        tokenizer = ByteLevelBPETokenizer.train_from_documents(
            sample(), target_vocab_size=VOCAB, c05_gate=gate
        )
        tokenizer.save(directory)
        write_once(
            directory / "c05-binding.json",
            {
                "plan_digest": gate.plan_digest,
                "completion_digest": gate.receipt_digest,
                "tokenizer_fingerprint": tokenizer.fingerprint,
            },
        )


# -- measurement ---------------------------------------------------------------------------


class Sampler:
    """Process-tree RSS/CPU and scratch-size sampler for one child command (display only)."""

    def __init__(self, pid: int, scratch: Path, interval: float = 0.25) -> None:
        self.process = psutil.Process(pid)
        self.scratch, self.interval = scratch, interval
        self.peak_rss = self.peak_scratch = 0
        self.cpu: dict[int, float] = {}
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        while not self.stop.wait(self.interval):
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
            self.peak_rss = max(self.peak_rss, rss)
            size = 0
            if self.scratch.exists():
                for path in self.scratch.rglob("*"):
                    try:
                        size += path.stat().st_size if path.is_file() else 0
                    except OSError:
                        continue
            self.peak_scratch = max(self.peak_scratch, size)


def run_one(root: Path, mode: str, workers: int, label: str) -> dict[str, Any]:
    """Time one count-tokens command (fresh process); return content-free metrics."""
    out = root / "runs" / label
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    command = [
        sys.executable,
        "-m",
        "xlm.data.exclusion.operator",
        "count-tokens-reference" if mode == "reference" else "count-tokens",
        "--c05-proof",
        str(root / "proof.json"),
        "--tokenizer",
        str(root / "tokenizer"),
        "--scratch",
        str(out / "scratch"),
        "--output",
        str(out / "counts"),
        "--issuer",
        ISSUER,
        "--key-env",
        KEY_ENV,
        "--progress-format",
        "jsonl",
        "--progress-interval",
        "1",
    ]
    if mode != "reference":
        command += ["--workers", str(workers)]
    env = {**os.environ, KEY_ENV: KEY, "PYTHONHASHSEED": "0"}
    started = time.perf_counter()
    with (out / "progress.jsonl").open("wb") as progress:
        child = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=progress, env=env)
        sampler = Sampler(child.pid, out / "scratch")
        sampler.thread.start()
        stdout, _ = child.communicate()
        sampler.stop.set()
        sampler.thread.join()
    wall = time.perf_counter() - started
    if child.returncode != 0:
        raise RuntimeError(f"{label} exited {child.returncode}")
    summary = json.loads((root / "benchmark.json").read_text(encoding="utf-8"))
    stages: dict[str, float] = {}
    for line in (out / "progress.jsonl").read_text(encoding="utf-8").splitlines():
        event = json.loads(line)
        if event.get("event") == "finish":
            stages[event["stage"]] = round(event["stage_seconds"], 2)
    counts = out / "counts"
    cpu = sum(sampler.cpu.values())
    return {
        "label": label,
        "mode": mode,
        "workers": workers,
        "wall_seconds": round(wall, 2),
        "docs_per_s": round(summary["documents"] / wall, 1),
        "mib_per_s": round(summary["file_bytes"] / 2**20 / wall, 2),
        "cpu_seconds": round(cpu, 1),
        "cpu_utilization_cores": round(cpu / wall, 2),
        "peak_tree_rss_mib": round(sampler.peak_rss / 2**20, 1),
        "peak_scratch_mib": round(sampler.peak_scratch / 2**20, 1),
        "stages": stages,
        "stdout": json.loads(stdout),
        "counts_jsonl_sha256": hashlib.sha256((counts / "counts.jsonl").read_bytes()).hexdigest(),
        "counts_json_sha256": hashlib.sha256((counts / "counts.json").read_bytes()).hexdigest(),
    }


def matrix(root: Path, workers: list[int], output: Path, repeat: int) -> dict[str, Any]:
    results = []
    for n in range(repeat):
        results.append(run_one(root, "reference", 0, f"reference-{n}"))
        for count in workers:
            results.append(run_one(root, "fast", count, f"fast-w{count}-{n}"))
    reference = [r for r in results if r["mode"] == "reference"]
    best_reference = min(r["wall_seconds"] for r in reference)
    identity = {(r["counts_jsonl_sha256"], r["counts_json_sha256"]) for r in results}
    for result in results:
        result["speedup_vs_reference"] = round(best_reference / result["wall_seconds"], 2)
    body = {
        "benchmark": json.loads((root / "benchmark.json").read_text(encoding="utf-8")),
        "machine": {"logical_cpus": psutil.cpu_count(), "physical_cpus": psutil.cpu_count(False)},
        "implementation": implementation_identity(),
        "all_artifacts_identical": len(identity) == 1,
        "results": results,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(body, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return body


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    making = commands.add_parser("build")
    making.add_argument("--root", type=Path, required=True)
    making.add_argument("--documents", type=int, default=120_000)
    making.add_argument("--seed", type=int, default=20261005)
    running = commands.add_parser("matrix")
    running.add_argument("--root", type=Path, required=True)
    running.add_argument("--workers", type=int, nargs="+", default=[1, 2, 4, 8, 16])
    running.add_argument("--repeat", type=int, default=1)
    running.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "build":
        print(json.dumps(build(args.root, args.documents, args.seed), sort_keys=True))
    else:
        body = matrix(args.root, args.workers, args.output, args.repeat)
        print(json.dumps({"all_artifacts_identical": body["all_artifacts_identical"]}))


if __name__ == "__main__":
    main()
