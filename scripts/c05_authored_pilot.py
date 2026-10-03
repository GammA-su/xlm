"""Generated-only bounded pilot of the actual C05 operator control plane."""

from __future__ import annotations

import argparse
import hashlib
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

from xlm.core.contracts import CanonicalDocument
from xlm.data.evidence_v2 import canonical
from xlm.data.exclusion.artifacts import ExecutionPlan
from xlm.data.exclusion.identity import implementation_identity
from xlm.data.exclusion.operator import main as operator
from xlm.data.exclusion.policy import MatcherPolicy, ProductionPolicy, Resources
from xlm.data.exclusion.preparation import benchmark_requirements
from xlm.data.exclusion.protected import MaterialSpec, build
from xlm.data.exclusion.runner import file_sha

KEY = "authored-pilot-only-not-a-protected-trust-root"
PROMPT = "Why do copper bridges expand during summer?"


def doc(
    number: int, source: str, text: str, metadata: dict[str, Any] | None = None
) -> CanonicalDocument:
    return CanonicalDocument(
        hashlib.sha256(f"authored-document-{number}".encode()).hexdigest(),
        source,
        "authored-revision",
        "generated",
        number,
        hashlib.sha256(text.encode()).hexdigest(),
        hashlib.sha256(text.encode()).hexdigest(),
        text,
        len(text.encode()),
        "en",
        1.0,
        "text",
        metadata or {},
        [],
        "authored",
        [],
        [],
        {},
        "train",
    )


def prepare(root: Path, count: int, *, word_floor: int = 32) -> dict[str, Path]:
    if not 1 <= count <= 200_000 or not 32 <= word_floor <= 512:
        raise ValueError("authored pilot bound is 1..200000 clean documents")
    root.mkdir(parents=True, exist_ok=False)
    data = root / "data"
    material = root / "material"
    data.mkdir()
    material.mkdir()
    os.environ["XLM_AUTHORED_C05_KEY"] = KEY
    write = canonical.write_canonical_json
    trust = root / "trust.json"
    write(trust, {"authored-pilot": "XLM_AUTHORED_C05_KEY"})
    pins = benchmark_requirements(Path("manifests/eval_dataset_pins.yaml"))["tasks"]
    rows = {
        "arc_easy": {"question": PROMPT, "choices": {"text": ["yes", "no"]}},
        "piqa": {"goal": "Fasten a loose wooden shelf", "sol1": "yes", "sol2": "no"},
        "blimp": {
            "sentence_good": "Those clever owls sing.",
            "sentence_bad": "Those clever owls sings.",
        },
        "hellaswag": {"ctx": "A sailor repairs the sail.", "endings": ["yes", "no"]},
    }
    material_files = []
    for task, row in rows.items():
        path = material / (task + ".jsonl")
        path.write_bytes(canonical.canonical_bytes(row) + b"\n")
        material_files.append(
            {
                "task": task,
                "repository": pins[task]["repository"],
                "revision": pins[task]["revision"],
                "config": "authored-config",
                "split": "authored-split",
                "path": path.name,
                "bytes": path.stat().st_size,
                "sha256": file_sha(path),
                "items": 1,
            }
        )
    spec = MaterialSpec.model_validate(
        {
            "files": material_files,
            "publisher_inventory_sha256": canonical.digest(material_files),
            "all_published_configs_splits_reviewed": True,
            "isolation": {
                "mode": "authored",
                "operator_principal": "fixture-operator",
                "denied_agent_principal": "fixture-agent",
                "attestation_sha256": canonical.digest("authored"),
                "access_controls_verified": True,
            },
        }
    )
    resources = Resources(
        free_bytes=0,
        records=count + 105,
        attempted_records=2 * count + 4096,
        files=4096,
        index_bytes=4 * 1024**3,
        # Admission requires the derived rollback-journal bound (~5.01 GiB here).
        scratch_bytes=16 * 1024**3,
        # Authored index is a few KiB; this also sizes the derived compiled-matcher bound.
        benchmark_bytes=64 * 1024**2,
        output_bytes=512 * 1024**2,
        decision_bytes=1024**3,
        journal_bytes=6 * 1024**3,
        ram_bytes=2 * 1024**3,
        comparisons=10_000_000,
        stage_seconds=1800,
        overall_seconds=3600,
    )
    policy = ProductionPolicy(diagnostic_bytes=0, quick_bytes=0, audit_bytes=0)
    identity = implementation_identity()
    build(
        spec,
        material,
        root / "prepared",
        policy=MatcherPolicy(),
        resources=resources,
        issuer="authored-pilot",
        key=KEY.encode(),
        code_commit=identity["code_commit"],
        code_identity=identity["code_identity"],
        dependency_sha256=identity["dependency_sha256"],
    )
    sources = ["authored-science", "authored-prose", "authored-synth"]
    files = []
    for source_no, source in enumerate(sources):
        numbers = list(range(source_no, count + 105, len(sources)))
        for part, start in enumerate(range(0, len(numbers), 512)):
            path = (
                data
                / f"canonical/authored-source-{source_no}/view/revision/part-{part:05d}"
                / "documents.jsonl"
            )
            path.parent.mkdir(parents=True, exist_ok=True)
            nrows = text_bytes = 0
            with path.open("xb") as stream:
                for number in numbers[start : start + 512]:
                    if number >= count:
                        text = PROMPT
                    else:
                        # Generated lexical tokens; vary record length without reusing
                        # a common template that would make the whole pilot near-duplicate.
                        text = " ".join(
                            "word" + hashlib.sha256(f"{number}:{n}".encode()).hexdigest()[:12]
                            for n in range(word_floor + number % 96)
                        )
                    document = doc(number, source, text)
                    stream.write(canonical.canonical_bytes(document.to_dict()) + b"\n")
                    nrows += 1
                    text_bytes += document.utf8_byte_count
            files.append(
                {
                    "path": path.relative_to(data).as_posix(),
                    "source_key": source,
                    "source_id": source,
                    "source_revision": "authored-revision",
                    "component": source,
                    "view": "authored",
                    "source_file": "generated",
                    "documents_sha256": file_sha(path),
                    "file_bytes": path.stat().st_size,
                    "canonical_bytes": text_bytes,
                    "documents": nrows,
                }
            )
    manifest: dict[str, Any] = {
        "kind": "authored_c05_input",
        "data_root": str(data.resolve()),
        "files": files,
        "sources": [
            {
                "source_key": s,
                "seal_digest": canonical.digest([s, files]),
                "source": {"source_id": s, "revision": "authored-revision"},
            }
            for s in sources
        ],
    }
    manifest["digest"] = canonical.self_digest(manifest)
    write(root / "manifest.json", manifest)
    values = {
        "lineage-policy": {"choice": "KNOWN_GROUP_ONLY"},
        "resources": resources.model_dump(mode="json"),
        "policy": policy.model_dump(mode="json"),
    }
    for purpose, value in values.items():
        write(root / (purpose + "-value.json"), value)
        command = [
            purpose,
            "freeze" if purpose == "policy" else "record",
            "--value",
            str(root / (purpose + "-value.json")),
            "--input-manifest-digest",
            manifest["digest"],
            "--evidence-digest",
            canonical.digest("authored-only"),
            "--operator",
            "authored-pilot",
            "--output",
            str(root / (purpose + ".json")),
            "--trust",
            str(trust),
            "--issuer",
            "authored-pilot",
            "--key-env",
            "XLM_AUTHORED_C05_KEY",
        ]
        if operator(command):
            raise ValueError("authored decision command failed")
    args = [
        "plan",
        "--mode",
        "authored",
        "--trust",
        str(trust),
        "--manifest",
        str(root / "manifest.json"),
        "--benchmark-receipt",
        str(root / "prepared/benchmark-preparation.receipt.json"),
        "--lineage-policy",
        str(root / "lineage-policy.json"),
        "--resources",
        str(root / "resources.json"),
        "--policy",
        str(root / "policy.json"),
        "--plan-root",
        str(root / "plans"),
        "--scratch",
        str(root / "scratch"),
        "--output",
        str(root / "output"),
    ]
    if operator(args):
        raise ValueError("authored plan command failed")
    plan_path = root / "plans/p0001.json"
    plan = ExecutionPlan.model_validate_json(plan_path.read_bytes())
    auth = [
        "authorize",
        "--plan",
        str(plan_path),
        "--plan-digest",
        plan.identity(),
        "--output",
        str(root / "authorization.json"),
        "--trust",
        str(trust),
        "--issuer",
        "authored-pilot",
        "--key-env",
        "XLM_AUTHORED_C05_KEY",
    ]
    if operator(auth):
        raise ValueError("authored authorization failed")
    return {"root": root, "plan": plan_path, "trust": trust}


def execute(paths: dict[str, Path]) -> dict[str, Any]:
    root, plan_path, trust = paths["root"], paths["plan"], paths["trust"]
    plan = ExecutionPlan.model_validate_json(plan_path.read_bytes())
    started, cpu = time.perf_counter(), time.process_time()
    args = [
        "run",
        "--plan",
        str(plan_path),
        "--trust",
        str(trust),
        "--issuer",
        "authored-pilot",
        "--key-env",
        "XLM_AUTHORED_C05_KEY",
        "--authorization",
        str(root / "authorization.json"),
        "--index",
        str(root / "prepared/index.jsonl"),
        "--benchmark-receipt",
        str(root / "prepared/benchmark-preparation.receipt.json"),
    ]
    if operator(args):
        raise ValueError("authored production-path run failed")
    wall, cpu_seconds = time.perf_counter() - started, time.process_time() - cpu
    if operator(["verify", "--plan", str(plan_path), "--trust", str(trust)]):
        raise ValueError("authored verification failed")
    completion = canonical.loads_bytes_strict(
        (Path(plan.output_root) / plan.identity() / "completion.json").read_bytes()
    )
    body = completion["payload"]
    if body["excluded"] != 105 or body["kept"] != sum(f.documents for f in plan.files) - 105:
        raise ValueError("pilot membership oracle disagrees")
    db_path = Path(plan.scratch_root) / plan.identity() / "facts.sqlite"
    with sqlite3.connect(db_path) as db:
        sizes = dict(db.execute("SELECT name,SUM(pgsize) FROM dbstat GROUP BY name"))
        signature_bytes = db.execute("SELECT SUM(LENGTH(signature)) FROM docs").fetchone()[0]
    return {
        "fixture": "generated-only",
        "plan_digest": plan.identity(),
        "mode": plan.mode,
        "wall_seconds": wall,
        "cpu_seconds": cpu_seconds,
        "documents": body["documents"],
        "kept": body["kept"],
        "excluded": body["excluded"],
        "files": len(plan.files),
        "physical_input_bytes": sum(f.file_bytes for f in plan.files),
        "sqlite_bytes": db_path.stat().st_size,
        "sqlite_pages_by_object": sizes,
        "raw_signature_bytes": signature_bytes,
        "journal_bytes_after_commit": sum(p.stat().st_size for p in db_path.parent.glob("*.json")),
        "membership_bytes": body["membership_bytes"],
        "dedup_stats": body["dedup_stats"],
        "peak_rss_sampled": body["peak_rss_sampled"],
        "peak_scratch_sampled": body["peak_scratch_sampled"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--documents", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--word-floor", type=int, default=32)
    args = parser.parse_args()
    result = execute(prepare(args.root, args.documents, word_floor=args.word_floor))
    result["generated_word_floor"] = args.word_floor
    result["document_id_characters"] = 64
    canonical.write_canonical_json(args.output, result)
    print(canonical.canonical_bytes(result).decode())


if __name__ == "__main__":
    main()
