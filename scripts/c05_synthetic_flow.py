"""Generated-only end-to-end rehearsal of the production C05 allocation chain.

Every step uses the operator CLI or the production functions it calls: input
manifest, decisions, policy freeze, plan, authorize, run (interrupted), resume-check,
resume, verify, exact counts, quota selection, selected-membership tokenization,
final freeze, training-input resolution and the schema-3 claim receipt. The trust
root is a synthetic environment key and the plan is ``authored``: every artifact
carries that mode, so none can satisfy Mix-01 training or an official claim.
No network, real benchmark material, real corpus, production tokenizer or training.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import time
from pathlib import Path
from typing import Any

import yaml
from scripts.c05_authored_pilot import KEY, PROMPT, doc

from xlm.data.evidence_v2 import canonical
from xlm.data.exclusion.artifacts import ExecutionPlan
from xlm.data.exclusion.identity import implementation_identity
from xlm.data.exclusion.operator import main as operator
from xlm.data.exclusion.policy import MatcherPolicy, ProductionPolicy, Resources
from xlm.data.exclusion.preparation import benchmark_requirements
from xlm.data.exclusion.protected import MaterialSpec, build
from xlm.data.exclusion.runner import file_sha

ISSUER = "authored-pilot"
KEY_ENV = "XLM_AUTHORED_C05_KEY"
TOTAL = 10_000
# Same shares as recipes/mixtures/mix01_quotas_6b.yaml, scaled to 10,000 targets.
FINALS = {
    "essential_science": 1000,
    "essential_practical": 1000,
    "essential_prose": 500,
    "ultrax_ultrafineweb": 2000,
    "finepdfs_en": 1500,
    "synth_en_explanations": 1500,
    "nemotron_wiki_rewrite": 800,
    "finewiki_en": 500,
    "ifm_behaviors_general_planning": 500,
    "common_pile_prose": 500,
    "simple_stories": 200,
}
IFM_VIEWS = {"general": 250, "planning": 250}
COMMON_PILE = {
    "libretexts": 80,
    "news": 80,
    "oercommons": 60,
    "pressbooks": 80,
    "project_gutenberg": 160,
    "public_domain_review": 40,
}
# (component, source_key, view, upstream, quota)
ALLOCATIONS: list[tuple[str, str, str, str | None, int]] = [
    ("essential_science", "ew-fast", "essential_science", None, 1000),
    ("essential_practical", "ew-fast", "essential_practical", None, 1000),
    ("essential_prose", "ew-fast", "essential_prose", None, 500),
    ("ultrax_ultrafineweb", "ultrax", "UltraX-Ultra-FineWeb", None, 2000),
    ("finepdfs_en", "finepdfs", "eng_Latn", None, 1500),
    ("synth_en_explanations", "synth", "default", None, 1500),
    ("nemotron_wiki_rewrite", "wiki_rewrite", "Nemotron-Pretraining-Wiki-Rewrite", None, 800),
    ("finewiki_en", "finewiki", "en", None, 500),
    ("ifm_behaviors_general_planning", "ifm_general", "general", None, 250),
    ("ifm_behaviors_general_planning", "ifm_planning", "planning", None, 250),
    *(
        ("common_pile_prose", "common_pile", "common_pile_prose", name, quota)
        for name, quota in COMMON_PILE.items()
    ),
    ("simple_stories", "simple_stories", "default", None, 200),
]
VOCAB = 320
WORDS = 60
# Generated clean records per allocation: quota // TARGETS_PER_RECORD + 6.
TARGETS_PER_RECORD = 40


def text(number: int) -> str:
    return " ".join(
        "w" + hashlib.sha256(f"flow:{number}:{n}".encode()).hexdigest()[:7]
        for n in range(WORDS + number % 17)
    )


def run_operator(arguments: list[str]) -> None:
    if operator(arguments):
        raise RuntimeError("operator command refused: " + arguments[0])


def prepare(root: Path) -> dict[str, Any]:
    """Generated corpus, quota table, IFM split, manifest and benchmark receipt."""
    root.mkdir(parents=True, exist_ok=False)
    os.environ[KEY_ENV] = KEY
    write = canonical.write_canonical_json
    trust = root / "trust.json"
    write(trust, {ISSUER: KEY_ENV})
    quotas = root / "quotas.yaml"
    quotas.write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "kind": "mix01_token_quotas",
                "quota_id": "authored_flow_v1",
                "tokenizer_vocab_size": VOCAB,
                "final_valid_targets": TOTAL,
                "final_quotas": FINALS,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    quota_sha = file_sha(quotas)
    split: dict[str, Any] = {
        "kind": "mix01_view_requirement_split",
        "version": 1,
        "component_id": "ifm_behaviors_general_planning",
        "quotas_sha256": quota_sha,
        "views": {v: {"final_tokens": q} for v, q in IFM_VIEWS.items()},
        "operator": "authored-flow",
        "rationale": "Generated rehearsal split; not an operator decision.",
    }
    split["digest"] = canonical.self_digest(split)
    write(root / "ifm-split.json", split)
    common: dict[str, Any] = {
        "kind": "mix01-upstream-component-requirements-v1",
        "components": {k: {"final_tokens": v} for k, v in COMMON_PILE.items()},
        "total_final_tokens": FINALS["common_pile_prose"],
    }
    common["digest"] = canonical.self_digest(common)
    data = root / "data"
    files: list[dict[str, Any]] = []
    number = 0
    planted = {"excluded": 0, "duplicates": 0}
    previous = 0
    for component, source, view, upstream, quota in ALLOCATIONS:
        path = data / "canonical" / source / view / (upstream or "all") / "documents.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        first = number
        rows = []
        for _ in range(quota // TARGETS_PER_RECORD + 6):
            rows.append(doc(number, source, text(number)))
            number += 1
        # One benchmark-contaminated record and one cross-allocation exact copy.
        rows.append(doc(number, source, f"Generated wrapper {number}. {PROMPT} End."))
        number += 1
        # Exact copy of the previous allocation's first record (cross-source alias).
        rows.append(doc(number, source, text(previous)))
        number += 1
        previous = first
        planted["excluded"] += 1
        planted["duplicates"] += 1
        raw = b"".join(canonical.canonical_bytes(r.to_dict()) + b"\n" for r in rows)
        path.write_bytes(raw)
        files.append(
            {
                "path": path.relative_to(data).as_posix(),
                "source_key": source,
                "source_id": source,
                "source_revision": "authored-revision",
                "component": component,
                "view": view,
                "upstream_component": upstream,
                "source_file": "generated",
                "documents_sha256": file_sha(path),
                "file_bytes": path.stat().st_size,
                "canonical_bytes": sum(r.utf8_byte_count for r in rows),
                "documents": len(rows),
            }
        )
    sources = []
    for key in sorted({f["source_key"] for f in files}):
        binding: dict[str, Any] = {"quotas_sha256": quota_sha}
        if key in {"ifm_general", "ifm_planning"}:
            binding["requirement_split"] = {"split_digest": split["digest"]}
        if key == "common_pile":
            binding["component_split"] = common
        sources.append(
            {
                "source_key": key,
                "seal_digest": canonical.digest(
                    [key, [f for f in files if f["source_key"] == key]]
                ),
                "source": {"source_id": key, "revision": "authored-revision"},
                **({} if key == "ew-fast" else {"adapter_binding": {"1": binding}}),
            }
        )
    manifest: dict[str, Any] = {
        "kind": "authored_c05_input",
        "data_root": str(data.resolve()),
        "files": files,
        "sources": sources,
    }
    manifest["digest"] = canonical.self_digest(manifest)
    write(root / "manifest.json", manifest)
    prepare_benchmark(root)
    return {"root": root, "trust": trust, "quotas": quotas, "planted": planted}


def prepare_benchmark(root: Path) -> None:
    material = root / "material"
    material.mkdir()
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
    entries = []
    for task, row in rows.items():
        path = material / (task + ".jsonl")
        path.write_bytes(canonical.canonical_bytes(row) + b"\n")
        entries.append(
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
            "files": entries,
            "publisher_inventory_sha256": canonical.digest(entries),
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
    identity = implementation_identity()
    build(
        spec,
        material,
        root / "prepared",
        policy=MatcherPolicy(),
        resources=resources(),
        issuer=ISSUER,
        key=KEY.encode(),
        code_commit=identity["code_commit"],
        code_identity=identity["code_identity"],
        dependency_sha256=identity["dependency_sha256"],
    )


def resources() -> Resources:
    return Resources(
        free_bytes=0,
        index_bytes=64 * 1024**2,
        journal_bytes=96 * 1024**2,
        decision_bytes=8 * 1024**2,
        output_bytes=8 * 1024**2,
        benchmark_bytes=8 * 1024**2,
        scratch_bytes=1024**3,
        ram_bytes=2 * 1024**3,
        records=10_000,
        attempted_records=40_000,
        files=64,
        comparisons=1_000_000,
        stage_seconds=1800,
        overall_seconds=3600,
    )


def decide_and_plan(paths: dict[str, Any]) -> Path:
    root, trust = paths["root"], paths["trust"]
    manifest = canonical.loads_bytes_strict((root / "manifest.json").read_bytes())
    values = {
        "lineage-policy": {"choice": "KNOWN_GROUP_ONLY"},
        "resources": resources().model_dump(mode="json"),
        "policy": ProductionPolicy(diagnostic_bytes=4096, quick_bytes=0, audit_bytes=0).model_dump(
            mode="json"
        ),
    }
    signing = ["--trust", str(trust), "--issuer", ISSUER, "--key-env", KEY_ENV]
    for purpose, value in values.items():
        canonical.write_canonical_json(root / f"{purpose}-value.json", value)
        run_operator(
            [
                purpose,
                "freeze" if purpose == "policy" else "record",
                "--value",
                str(root / f"{purpose}-value.json"),
                "--input-manifest-digest",
                manifest["digest"],
                "--evidence-digest",
                canonical.digest("authored-flow-only"),
                "--operator",
                "authored-flow",
                "--output",
                str(root / f"{purpose}.json"),
                *signing,
            ]
        )
    run_operator(
        [
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
    )
    plan_path: Path = root / "plans/p0001.json"
    plan = ExecutionPlan.model_validate_json(plan_path.read_bytes())
    run_operator(
        [
            "authorize",
            "--plan",
            str(plan_path),
            "--plan-digest",
            plan.identity(),
            "--output",
            str(root / "authorization.json"),
            *signing,
        ]
    )
    return plan_path


def run_c05(paths: dict[str, Any], plan_path: Path) -> dict[str, Any]:
    """Interrupted run, read-only resume check, resume, verify."""
    from xlm.data.exclusion.runner import run

    root, trust = paths["root"], paths["trust"]
    plan = ExecutionPlan.model_validate_json(plan_path.read_bytes())
    identity = implementation_identity()

    def interrupt(event: str) -> None:
        if event == "grouped":
            raise RuntimeError("authored interruption inside grouping")

    load = canonical.loads_bytes_strict
    try:
        run(
            plan,
            load((root / "authorization.json").read_bytes()),
            index=root / "prepared/index.jsonl",
            benchmark=load((root / "prepared/benchmark-preparation.receipt.json").read_bytes()),
            trusted={ISSUER: KEY.encode()},
            issuer=ISSUER,
            key=KEY.encode(),
            current_code=identity["code_identity"],
            current_dependencies=identity["dependency_sha256"],
            checkpoint=interrupt,
        )
    except RuntimeError:
        pass
    else:
        raise RuntimeError("interruption was not exercised")
    common = ["--plan", str(plan_path), "--trust", str(trust)]
    resume = [
        "--authorization",
        str(root / "authorization.json"),
        "--benchmark-receipt",
        str(root / "prepared/benchmark-preparation.receipt.json"),
        "--index",
        str(root / "prepared/index.jsonl"),
    ]
    run_operator(["resume-check", *common, *resume])
    run_operator(["resume", *common, *resume, "--issuer", ISSUER, "--key-env", KEY_ENV])
    run_operator(["verify", *common])
    completion_path = Path(plan.output_root) / plan.identity() / "completion.json"
    completion = load(completion_path.read_bytes())
    proof = root / "proof.json"
    canonical.write_canonical_json(
        proof,
        {
            "plan": str(plan_path),
            "manifest": str(root / "manifest.json"),
            "completion": str(completion_path.parent),
            "trust": str(trust),
            "scratch": str(root / "lookup"),
            "plan_digest": plan.identity(),
            "completion_digest": completion["digest"],
            "signer": ISSUER,
            "signer_key_env": KEY_ENV,
        },
    )
    return {"proof": proof, "completion": completion}


def fit_tokenizer(proof: Path, directory: Path) -> None:
    """Tokenizer identity fixture: a tiny BPE fitted only on screened train records."""
    from xlm.data.acquisition.source_run import write_once
    from xlm.data.exclusion.selection import iter_plan_documents
    from xlm.data.exclusion.transport import open_gate
    from xlm.tokenizers.bpe import ByteLevelBPETokenizer

    with open_gate(proof, allow_authored=True) as gate:
        if gate is None:
            raise RuntimeError("proof missing")
        kept = (
            d
            for _, d in iter_plan_documents(gate)
            if (row := gate.lookup(d.doc_id)) is not None and row[2] == "train"
        )
        tokenizer = ByteLevelBPETokenizer.train_from_documents(
            kept, target_vocab_size=VOCAB, c05_gate=gate
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


def allocate(paths: dict[str, Any], c05: dict[str, Any], plan_path: Path) -> dict[str, Any]:
    root, trust, proof = paths["root"], paths["trust"], c05["proof"]
    tokenizer = root / "tokenizer"
    fit_tokenizer(proof, tokenizer)
    common = ["--c05-proof", str(proof), "--tokenizer", str(tokenizer)]
    signing = ["--issuer", ISSUER, "--key-env", KEY_ENV]
    run_operator(
        [
            "count-tokens",
            *common,
            "--scratch",
            str(root / "allocation-scratch"),
            "--output",
            str(root / "counts"),
            *signing,
        ]
    )
    run_operator(
        [
            "select",
            *common,
            "--scratch",
            str(root / "allocation-scratch"),
            "--counts",
            str(root / "counts"),
            "--quotas",
            str(paths["quotas"]),
            "--ifm-split",
            str(root / "ifm-split.json"),
            "--deficit-report",
            str(root / "deficit.json"),
            "--output",
            str(root / "selection"),
            *signing,
        ]
    )
    run_operator(
        [
            "tokenize-selection",
            *common,
            "--selection",
            str(root / "selection"),
            "--output-root",
            str(root / "shards"),
        ]
    )
    run_operator(
        [
            "freeze",
            *common,
            "--selection",
            str(root / "selection"),
            "--shards",
            str(root / "shards"),
            "--output",
            str(root / "freeze"),
            *signing,
        ]
    )
    from xlm.core.paths import ArtifactPaths
    from xlm.training.inputs import resolve_training_input

    training = canonical.loads_bytes_strict((root / "freeze/training-data.json").read_bytes())
    _, data_identity = resolve_training_input(training, ArtifactPaths(root=root / "artifacts"))
    benchmark = str(root / "prepared/benchmark-preparation.receipt.json")
    run_operator(
        [
            "final-receipt",
            "--plan",
            str(plan_path),
            "--benchmark-receipt",
            benchmark,
            "--c05-proof",
            str(proof),
            "--freeze",
            str(root / "freeze/freeze.json"),
            "--output",
            str(root / "final-receipt.json"),
            "--trust",
            str(trust),
            *signing,
        ]
    )
    run_operator(
        [
            "claim-binding",
            "--c05-proof",
            str(proof),
            "--plan",
            str(plan_path),
            "--freeze",
            str(root / "freeze/freeze.json"),
            "--checkpoint-hash",
            "authored-checkpoint",
            "--suite-fingerprint",
            "authored-suite",
            "--output",
            str(root / "claim-binding.json"),
        ]
    )
    # Expected refusal: an authored chain yields a development receipt.
    claim_exit = operator(
        [
            "claim-check",
            "--receipt",
            str(root / "final-receipt.json"),
            "--binding",
            str(root / "claim-binding.json"),
            "--trust",
            str(trust),
        ]
    )
    return {
        "training_identity": data_identity,
        "training_binding": training["c05_binding"],
        "official_claim_exit": claim_exit,
    }


def execute(root: Path) -> dict[str, Any]:
    started = time.perf_counter()
    paths = prepare(root)
    plan_path = decide_and_plan(paths)
    c05 = run_c05(paths, plan_path)
    result = allocate(paths, c05, plan_path)
    load = canonical.loads_bytes_strict
    selection = load((root / "selection/selection.json").read_bytes())["payload"]
    freeze = load((root / "freeze/freeze.json").read_bytes())
    receipt = load((root / "final-receipt.json").read_bytes())
    body = c05["completion"]["payload"]
    return {
        "fixture": "generated-only",
        "mode": body["mode"],
        "plan_digest": body["plan_digest"],
        "completion_digest": c05["completion"]["digest"],
        "c05": {k: body[k] for k in ("documents", "kept", "excluded", "duplicates")},
        "planted": paths["planted"],
        "storage_admissions": body["storage"]["admissions"],
        "worst_case_storage_bytes": body["storage"]["worst_case_bytes"],
        "peak_aggregate_sampled": body["peak_scratch_sampled"],
        "selection_digest": load((root / "selection/selection.json").read_bytes())["digest"],
        "selected_documents": selection["selected_documents"],
        "selected_valid_targets": selection["selected_valid_targets"],
        "truncated_documents": sum(
            a["truncated_documents"] for a in selection["allocations"].values()
        ),
        "allocations": {
            k: {n: a[n] for n in ("quota", "selected_valid_targets", "status")}
            for k, a in selection["allocations"].items()
        },
        "freeze_digest": freeze["digest"],
        "recipe_identity": freeze["payload"]["recipe_identity"],
        "receipt_schema": receipt["schema_version"],
        "receipt_mode": receipt["mode"],
        **result,
        "wall_seconds": time.perf_counter() - started,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    summary = execute(args.root)
    canonical.write_canonical_json(args.output, summary)
    print(canonical.canonical_bytes(summary).decode())


if __name__ == "__main__":
    main()
