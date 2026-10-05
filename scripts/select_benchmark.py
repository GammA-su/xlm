"""Bounded, reproducible select benchmark over a generated 17-allocation authored C05 chain.

``build`` scales the generated Mix-01-shaped flow (:mod:`scripts.c05_synthetic_flow`:
the same 17 allocations, IFM views and Common Pile upstreams, planted duplicates and
benchmark contamination) to about ``--documents`` short records, runs an authored C05
through the operator CLI, writes the proof, fits the flow's small BPE on screened
records and publishes exact counts with ``count-tokens``. Selection cost depends on the
number of count rows, not on text size, so records are short; quotas are scaled so
about 40 % of the train records are selected, and each count row is about as large as
a real one. ``run`` times one select configuration in a fresh subprocess; ``matrix``
runs the reference and the given worker counts and checks byte identity.

Generated text only: no real corpus, proof, tokenizer, key, G:/X: or network. The
trust root is a synthetic environment key and the plan mode is ``authored``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import psutil
from scripts import c05_synthetic_flow as flow
from scripts.c05_authored_pilot import KEY
from scripts.count_tokens_benchmark import Sampler

from xlm.data.evidence_v2 import canonical
from xlm.data.exclusion.artifacts import ExecutionPlan
from xlm.data.exclusion.identity import implementation_identity
from xlm.data.exclusion.operator import main as operator
from xlm.data.exclusion.policy import ProductionPolicy, Resources

ISSUER, KEY_ENV = flow.ISSUER, flow.KEY_ENV
WORDS = 8  # about 105 valid targets per record with the flow's 320-entry BPE
TARGETS_PER_RECORD = 42  # quota per generated record: about 2.5x eligible supply


def _run(arguments: list[str]) -> None:
    if operator(arguments):
        raise RuntimeError("operator command refused: " + arguments[0])


def _scale(documents: int) -> None:
    """Scale the flow's quota tables so prepare() writes about ``documents`` records."""
    scale = max(1, documents * TARGETS_PER_RECORD // flow.TOTAL)
    flow.TOTAL *= scale
    flow.FINALS = {k: v * scale for k, v in flow.FINALS.items()}
    flow.IFM_VIEWS = {k: v * scale for k, v in flow.IFM_VIEWS.items()}
    flow.COMMON_PILE = {k: v * scale for k, v in flow.COMMON_PILE.items()}
    flow.ALLOCATIONS = [(c, s, v, u, q * scale) for c, s, v, u, q in flow.ALLOCATIONS]
    flow.WORDS = WORDS
    flow.TARGETS_PER_RECORD = TARGETS_PER_RECORD


def _resources(documents: int) -> Resources:
    return Resources(
        free_bytes=0,
        index_bytes=8 * 1024**3,
        journal_bytes=12 * 1024**3,
        decision_bytes=1024**3,
        output_bytes=4 * 1024**3,
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


def _plan(root: Path, documents: int) -> Path:
    trust = root / "trust.json"
    manifest = canonical.loads_bytes_strict((root / "manifest.json").read_bytes())
    values = {
        "lineage-policy": {"choice": "KNOWN_GROUP_ONLY"},
        "resources": _resources(documents).model_dump(mode="json"),
        "policy": ProductionPolicy(
            diagnostic_bytes=64 * 1024, quick_bytes=0, audit_bytes=16 * 1024
        ).model_dump(mode="json"),
    }
    signing = ["--trust", str(trust), "--issuer", ISSUER, "--key-env", KEY_ENV]
    for purpose, value in values.items():
        canonical.write_canonical_json(root / f"{purpose}-value.json", value)
        _run(
            [purpose, "freeze" if purpose == "policy" else "record", "--value"]
            + [str(root / f"{purpose}-value.json"), "--input-manifest-digest", manifest["digest"]]
            + ["--evidence-digest", canonical.digest("select-benchmark"), "--operator", "bench"]
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


def _c05(root: Path, plan_path: Path) -> Path:
    trust = root / "trust.json"
    _run(
        ["run", "--plan", str(plan_path), "--authorization", str(root / "authorization.json")]
        + ["--benchmark-receipt", str(root / "prepared/benchmark-preparation.receipt.json")]
        + ["--index", str(root / "prepared/index.jsonl"), "--trust", str(trust)]
        + ["--issuer", ISSUER, "--key-env", KEY_ENV, "--no-progress"]
    )
    _run(["verify", "--plan", str(plan_path), "--trust", str(trust)])
    plan = ExecutionPlan.model_validate_json(plan_path.read_bytes())
    completion = Path(plan.output_root) / plan.identity() / "completion.json"
    envelope = canonical.loads_bytes_strict(completion.read_bytes())
    proof = root / "proof.json"
    canonical.write_canonical_json(
        proof,
        {
            "plan": str(plan_path),
            "manifest": str(root / "manifest.json"),
            "completion": str(completion.parent),
            "trust": str(trust),
            "scratch": str(root / "lookup"),
            "plan_digest": plan.identity(),
            "completion_digest": envelope["digest"],
            "signer": ISSUER,
            "signer_key_env": KEY_ENV,
        },
    )
    return proof


def _fit_tokenizer(proof: Path, directory: Path) -> None:
    """The flow's 320-entry BPE, fitted on a bounded sample of screened train records."""
    from xlm.data.acquisition.source_run import write_once
    from xlm.data.exclusion.selection import iter_plan_documents
    from xlm.data.exclusion.transport import open_gate
    from xlm.tokenizers.bpe import ByteLevelBPETokenizer

    with open_gate(proof, allow_authored=True) as gate:
        if gate is None:
            raise RuntimeError("proof missing")
        budget = [8 * 1024**2]

        def sample() -> Any:
            for _, document in iter_plan_documents(gate):
                row = gate.lookup(document.doc_id)
                if row is not None and row[2] == "train" and budget[0] > 0:
                    budget[0] -= document.utf8_byte_count
                    yield document

        tokenizer = ByteLevelBPETokenizer.train_from_documents(
            sample(), target_vocab_size=flow.VOCAB, c05_gate=gate
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


def build(root: Path, documents: int) -> dict[str, Any]:
    """Corpus, authored C05, proof, tokenizer and exact counts (setup; not timed as select)."""
    os.environ[KEY_ENV] = KEY
    _scale(documents)
    times: dict[str, float] = {}
    started = time.perf_counter()
    flow.prepare(root)
    times["corpus_seconds"] = time.perf_counter() - started
    plan_path = _plan(root, documents)
    started = time.perf_counter()
    proof = _c05(root, plan_path)
    times["c05_seconds"] = time.perf_counter() - started
    started = time.perf_counter()
    _fit_tokenizer(proof, root / "tokenizer")
    times["tokenizer_seconds"] = time.perf_counter() - started
    started = time.perf_counter()
    _run(
        ["count-tokens", "--c05-proof", str(proof), "--tokenizer", str(root / "tokenizer")]
        + ["--scratch", str(root / "count-scratch"), "--output", str(root / "counts")]
        + ["--issuer", ISSUER, "--key-env", KEY_ENV, "--workers", "16", "--no-progress"]
    )
    times["count_seconds"] = time.perf_counter() - started
    counts = canonical.loads_bytes_strict((root / "counts/counts.json").read_bytes())["payload"]
    plan = ExecutionPlan.model_validate_json(plan_path.read_bytes())
    completion = canonical.loads_bytes_strict(
        (Path(plan.output_root) / plan.identity() / "completion.json").read_bytes()
    )["payload"]
    summary = {
        "documents": completion["documents"],
        "kept": completion["kept"],
        "count_rows": counts["documents"],
        "counts_bytes": counts["counts_bytes"],
        "membership_bytes": completion["membership_bytes"],
        "allocations": len(counts["allocations"]),
        "valid_targets": sum(a["valid_targets"] for a in counts["allocations"].values()),
        "quota": flow.TOTAL,
        **{k: round(v, 1) for k, v in times.items()},
    }
    canonical.write_canonical_json(root / "benchmark.json", summary)
    return summary


def run_one(root: Path, mode: str, workers: int, label: str) -> dict[str, Any]:
    """Time one select command (fresh process); return content-free metrics."""
    out = root / "runs" / label
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    command = [
        sys.executable,
        "-m",
        "xlm.data.exclusion.operator",
        "select-reference" if mode == "reference" else "select",
        "--c05-proof",
        str(root / "proof.json"),
        "--tokenizer",
        str(root / "tokenizer"),
        "--scratch",
        str(out / "scratch"),
        "--counts",
        str(root / "counts"),
        "--quotas",
        str(root / "quotas.yaml"),
        "--ifm-split",
        str(root / "ifm-split.json"),
        "--deficit-report",
        str(out / "deficit.json"),
        "--output",
        str(out / "selection"),
        "--issuer",
        ISSUER,
        "--key-env",
        KEY_ENV,
    ]
    if mode != "reference":
        command += ["--workers", str(workers), "--progress-format", "jsonl"]
        command += ["--progress-interval", "1"]
    # The reference's membership lookup lives in the proof's scratch (C:/ NVMe here).
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
        if line.startswith("{"):
            event = json.loads(line)
            if event.get("event") == "finish":
                stages[event["stage"]] = round(event["stage_seconds"], 2)
    selection = out / "selection"
    cpu = sum(sampler.cpu.values())
    return {
        "label": label,
        "mode": mode,
        "workers": workers,
        "wall_seconds": round(wall, 2),
        "count_rows_per_s": round(summary["count_rows"] / wall, 1),
        "counts_mib_per_s": round(summary["counts_bytes"] / 2**20 / wall, 2),
        "cpu_seconds": round(cpu, 1),
        "cpu_utilization_cores": round(cpu / wall, 2),
        "peak_tree_rss_mib": round(sampler.peak_rss / 2**20, 1),
        "peak_scratch_mib": round(sampler.peak_scratch / 2**20, 1),
        "stages": stages,
        "stdout": json.loads(stdout),
        "selected_jsonl_sha256": hashlib.sha256(
            (selection / "selected.jsonl").read_bytes()
        ).hexdigest(),
        "selection_json_sha256": hashlib.sha256(
            (selection / "selection.json").read_bytes()
        ).hexdigest(),
    }


def matrix(root: Path, modes: list[str], output: Path) -> dict[str, Any]:
    results = []
    for mode in modes:
        if mode == "reference":
            results.append(run_one(root, "reference", 0, "reference"))
        else:
            results.append(run_one(root, "fast", int(mode), f"fast-w{mode}"))
    identity = {(r["selected_jsonl_sha256"], r["selection_json_sha256"]) for r in results}
    reference = [r["wall_seconds"] for r in results if r["mode"] == "reference"]
    for result in results:
        if reference:
            result["speedup_vs_reference"] = round(reference[0] / result["wall_seconds"], 2)
    body = {
        "benchmark": json.loads((root / "benchmark.json").read_text(encoding="utf-8")),
        "machine": {"logical_cpus": psutil.cpu_count(), "physical_cpus": psutil.cpu_count(False)},
        "implementation": implementation_identity(),
        "all_artifacts_identical": len(identity) == 1,
        "results": results,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    previous = json.loads(output.read_text(encoding="utf-8")) if output.exists() else None
    if previous is not None and previous.get("benchmark") == body["benchmark"]:
        body["results"] = previous["results"] + results
        identity |= {
            (r["selected_jsonl_sha256"], r["selection_json_sha256"]) for r in previous["results"]
        }
        body["all_artifacts_identical"] = len(identity) == 1
    text = json.dumps(body, indent=1, sort_keys=True) + "\n"
    output.write_text(text, encoding="utf-8", newline="\n")
    return body


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    making = commands.add_parser("build")
    making.add_argument("--root", type=Path, required=True)
    making.add_argument("--documents", type=int, default=400_000)
    running = commands.add_parser("matrix")
    running.add_argument("--root", type=Path, required=True)
    # "reference" and/or worker counts, run in this order; results append to --output.
    running.add_argument("--modes", nargs="+", default=["reference", "8", "16"])
    running.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "build":
        print(json.dumps(build(args.root, args.documents), sort_keys=True))
    else:
        body = matrix(args.root, args.modes, args.output)
        print(json.dumps({"all_artifacts_identical": body["all_artifacts_identical"]}))


if __name__ == "__main__":
    main()
