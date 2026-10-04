"""Authored end-to-end chain for C05 over a Phase-C cleaned corpus (synthetic only).

The chain is the real one, run on authored text: original manifest (with source
identities) -> Phase-A audit -> v1/v2 policy freeze -> authored cuts -> v2 dry run ->
production cleaning -> independent production verification -> cleaned manifest ->
independent post-clean audit -> its ``report`` (saved stdout). C05 then plans and runs
over the cleaned manifest through the operator CLI. No real corpus, no protected
benchmark material, no G:/X:, no network.

The authored layout adds C05 features to the cleaner fixture: an exact duplicate pair
across files, a near-duplicate pair, a benchmark-contaminated document whose URL
lineage family also holds a clean document, and a clean URL-linked family.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from cleaning_fixtures import override_thresholds
from production_fixtures import (
    NO_NEWLINE,
    TEMPLATE_V1,
    TEMPLATE_V2,
    limits,
    production_layout,
    strip_final_newline,
    with_predecessor_cuts,
)
from quality_fixtures import build_corpus, document
from xlm.data.evidence_v2 import canonical
from xlm.data.exclusion.cleaned import Evidence
from xlm.data.exclusion.control import main as control
from xlm.data.exclusion.identity import implementation_identity
from xlm.data.exclusion.policy import MatcherPolicyV4, ProductionPolicy, Resources
from xlm.data.exclusion.preparation import benchmark_requirements
from xlm.data.exclusion.protected import MaterialSpec, build
from xlm.data.exclusion.runner import file_sha
from xlm.data.quality.cleaning_policy import freeze_policy
from xlm.data.quality.cleaning_runner import load_receipt, run_dry_run
from xlm.data.quality.production import run_production
from xlm.data.quality.production_verify import (
    build_cleaned_manifest,
    load_production_receipt,
    verify_production,
)
from xlm.data.quality.runner import run_audit, verify_report

REPO = Path(__file__).resolve().parents[1]
KEY = "authored-cleaned-c05-only-not-a-protected-trust-root"
KEY_ENV = "XLM_CLEANED_C05_TEST_KEY"
ISSUER = "authored-cleaned"
PROMPT = "Why do copper bridges expand during summer?"
SOURCE_ID = "authored"
REVISION = "authored-revision"
HARBOR = (
    "Harbor pilots guide large vessels through narrow channels where tides shift "
    "sandbars from season to season. Each pilot memorizes depth markers, studies "
    "weather reports at dawn and talks with tug crews before boarding a ship."
)
RAIL = (
    "Mountain railways climb steep valleys with switchbacks, tunnels and long "
    "viaducts built from local stone. Engineers inspect every bridge pier after "
    "spring floods and repair drainage channels before the autumn rains arrive."
)
LIBRARY = (
    "The county library lends seed packets alongside books, and volunteers sort "
    "donated envelopes by planting season so families can grow beans and squash."
)
GARDEN = (
    "Community gardeners rotate tomatoes, onions and peppers between raised beds "
    "each year to keep the soil healthy and reduce pests without heavy spraying."
)
ORCHARD = (
    "Orchard keepers prune apple trees in late winter, sealing large cuts and "
    "thinning crowded branches so that light reaches the fruit through summer."
)
BENCH_URL = "https://example.org/workshops/thermal-notes"
KEEP_URL = "https://example.org/gardening/rotation-guide"
# Doc ids of the C05 feature documents (component finewiki_en).
EXACT = ("c05-exact-a1", "c05-exact-a2")
NEAR = ("c05-near-b1", "c05-near-b2")
CONTAMINATED = "c05-bench-1"
CONTAMINATED_FAMILY = "c05-bench-family"
LINEAGE = ("c05-lineage-k1", "c05-lineage-k2")


def c05_layout() -> dict[str, list[dict[str, Any]]]:
    layout = production_layout()
    layout["finewiki_en/default/c05a"] = [
        document(EXACT[0], HARBOR, 1),
        document(NEAR[0], RAIL, 2),
        document(
            CONTAMINATED,
            f"Workshop notes recall a familiar classroom quiz. {PROMPT} Students "
            "answered with sketches of metal joints and thermometers.",
            3,
            metadata={"url": BENCH_URL},
        ),
        document(LINEAGE[0], GARDEN, 4, metadata={"url": KEEP_URL}),
    ]
    layout["finewiki_en/default/c05b"] = [
        document(EXACT[1], HARBOR, 1),
        document(NEAR[1], RAIL + " The depot keeps careful records of every crossing.", 2),
        document(CONTAMINATED_FAMILY, LIBRARY, 3, metadata={"url": BENCH_URL}),
        document(LINEAGE[1], ORCHARD, 4, metadata={"url": KEEP_URL}),
    ]
    return layout


def set_sources(manifest: Path, kind: str) -> None:
    """Give the authored original manifest a kind and source identities, re-digested."""
    body = json.loads(manifest.read_bytes())
    body.pop("digest")
    body["kind"] = kind
    body["sources"] = [
        {
            "source_key": key,
            "seal_digest": canonical.digest(["authored-seal", key]),
            "source": {"source_id": SOURCE_ID, "revision": REVISION},
        }
        for key in sorted({f["source_key"] for f in body["files"]})
    ]
    body["digest"] = canonical.self_digest(body)
    canonical.write_canonical_json(manifest, body)


def make_chain(root: Path, *, production_kind: bool = False) -> dict[str, Any]:
    """Run the real cleaning chain on authored text; return paths and pins."""
    root.mkdir(parents=True, exist_ok=True)
    manifest = build_corpus(root / "corpus", c05_layout())
    strip_final_newline(manifest, NO_NEWLINE)
    set_sources(manifest, "c05_global_input_manifest" if production_kind else "authored_c05_input")
    audit = root / "audit-pre"
    run_audit(manifest, audit, limits=limits(), progress_interval=None)
    policies = root / "policies"
    policies.mkdir()
    frozen_v1 = policies / "v1.frozen.yaml"
    freeze_policy(TEMPLATE_V1, audit, frozen_v1)
    frozen_v2 = policies / "v2.frozen.yaml"
    freeze_policy(TEMPLATE_V2, audit, frozen_v2, predecessor=frozen_v1)
    cuts_v1 = override_thresholds(frozen_v1, policies / "v1.cuts.yaml")
    cuts_v2 = with_predecessor_cuts(frozen_v2, cuts_v1, policies / "v2.cuts.yaml")
    dry = root / "dry-run"
    run_dry_run(manifest, dry, cuts_v2, limits=limits(), progress_interval=None)
    approved = load_receipt(dry)["result_digest"]
    clean, state = root / "clean", root / "clean-state"
    run_production(
        manifest,
        cuts_v2,
        dry,
        clean,
        state,
        approved_result_digest=approved,
        limits=limits(),
        progress_interval=None,
    )
    verified = verify_production(
        manifest,
        cuts_v2,
        dry,
        clean,
        state,
        approved_result_digest=approved,
        workers=1,
        compare_sources=True,
        progress_interval=None,
    )
    built = build_cleaned_manifest(state, clean)
    cleaned = Path(built["cleaned_manifest"])
    post = root / "audit-clean"
    run_audit(cleaned, post, limits=limits(), progress_interval=None)
    report = verify_report(cleaned, post, workers=1)
    report_path = root / "audit-clean.report.json"
    report_path.write_text(json.dumps(report, sort_keys=True) + "\n", encoding="utf-8")
    receipt = load_production_receipt(state)
    return {
        "root": root,
        "original": manifest,
        "audit_pre": audit,
        "manifest": cleaned,
        "manifest_digest": built["digest"],
        "manifest_sha256": built["file_sha256"],
        "totals": built["totals"],
        "files": built["files"],
        "clean": clean,
        "state": state,
        "audit": post,
        "report": report_path,
        "production_result": receipt["result_digest"],
        "production_receipt": receipt["digest"],
        "verification": verified["verification_digest"],
        "audit_result": report["result_digest"],
    }


def evidence(chain: dict[str, Any], **changes: Any) -> Evidence:
    values: dict[str, Any] = {
        "manifest": chain["manifest"],
        "original_manifest": chain["original"],
        "cleaning_state": chain["state"],
        "audit_output": chain["audit"],
        "audit_report": chain["report"],
        "expect_manifest_digest": chain["manifest_digest"],
        "expect_production_result_digest": chain["production_result"],
        "expect_production_receipt_digest": chain["production_receipt"],
        "expect_verification_digest": chain["verification"],
        "expect_audit_result_digest": chain["audit_result"],
    }
    values.update(changes)
    return Evidence(**values)


def admit_args(chain: dict[str, Any], output: Path, **changes: Any) -> list[str]:
    chosen = evidence(chain, **changes)
    return [
        "admit-cleaned",
        "--manifest",
        str(chosen.manifest),
        "--original-manifest",
        str(chosen.original_manifest),
        "--cleaning-state",
        str(chosen.cleaning_state),
        "--audit-output",
        str(chosen.audit_output),
        "--audit-report",
        str(chosen.audit_report),
        "--expect-manifest-digest",
        chosen.expect_manifest_digest,
        "--expect-production-result-digest",
        chosen.expect_production_result_digest,
        "--expect-production-receipt-digest",
        chosen.expect_production_receipt_digest,
        "--expect-verification-digest",
        chosen.expect_verification_digest,
        "--expect-audit-result-digest",
        chosen.expect_audit_result_digest,
        "--output",
        str(output),
    ]


# -- C05 control plane (authored trust root) ---------------------------------------------


def trust(root: Path) -> Path:
    os.environ[KEY_ENV] = KEY
    path = root / "trust.json"
    if not path.exists():
        canonical.write_canonical_json(path, {ISSUER: KEY_ENV})
    return path


def signing(root: Path) -> list[str]:
    return ["--trust", str(trust(root)), "--issuer", ISSUER, "--key-env", KEY_ENV]


def resources(workers: int = 1) -> Resources:
    return Resources(
        free_bytes=0,
        index_bytes=64 * 1024**2,
        journal_bytes=96 * 1024**2,
        decision_bytes=8 * 1024**2,
        output_bytes=8 * 1024**2,
        benchmark_bytes=8 * 1024**2,
        scratch_bytes=1024**3,
        ram_bytes=8 * 1024**3,
        records=10_000,
        attempted_records=40_000,
        files=64,
        comparisons=1_000_000,
        stage_seconds=1800,
        overall_seconds=3600,
        workers=workers,
    )


def policy() -> ProductionPolicy:
    return ProductionPolicy(
        matcher=MatcherPolicyV4(), diagnostic_bytes=600, quick_bytes=0, audit_bytes=0
    )


def prepare_benchmark(root: Path, *, code_identity: str | None = None) -> dict[str, Path]:
    """Authored benchmark preparation (four tasks, one item each) under a code identity."""
    material = root / "material"
    material.mkdir(parents=True)
    pins = benchmark_requirements(REPO / "manifests/eval_dataset_pins.yaml")["tasks"]
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
    prepared = root / "prepared"
    build(
        spec,
        material,
        prepared,
        policy=policy().matcher,
        resources=resources(),
        issuer=ISSUER,
        key=KEY.encode(),
        code_commit=identity["code_commit"],
        code_identity=code_identity or identity["code_identity"],
        dependency_sha256=identity["dependency_sha256"],
    )
    return {
        "receipt": prepared / "benchmark-preparation.receipt.json",
        "index": prepared / "index.jsonl",
    }


def record_decisions(
    root: Path,
    manifest_digest: str,
    evidence_digest: str,
    *,
    workers: int = 1,
    name: str = "decisions",
) -> dict[str, Path]:
    """Fresh signed lineage/resources/policy decisions through the operator CLI."""
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    values = {
        "lineage-policy": {"choice": "KNOWN_GROUP_ONLY"},
        "resources": resources(workers).model_dump(mode="json"),
        "policy": policy().model_dump(mode="json"),
    }
    paths = {}
    for purpose, value in values.items():
        source = directory / f"{purpose}-value.json"
        canonical.write_canonical_json(source, value)
        paths[purpose] = directory / f"{purpose}.json"
        code = control(
            [
                purpose,
                "freeze" if purpose == "policy" else "record",
                "--value",
                str(source),
                "--input-manifest-digest",
                manifest_digest,
                "--evidence-digest",
                evidence_digest,
                "--operator",
                "authored-operator",
                "--output",
                str(paths[purpose]),
                *signing(root),
            ]
        )
        assert code == 0, purpose
    return paths


def plan_args(
    root: Path,
    manifest: Path,
    decisions: dict[str, Path],
    benchmark: dict[str, Path],
    roots: dict[str, Path],
    *,
    admission: Path | None = None,
    mode: str = "authored",
) -> list[str]:
    return [
        "plan",
        "--mode",
        mode,
        "--trust",
        str(trust(root)),
        "--manifest",
        str(manifest),
        "--benchmark-receipt",
        str(benchmark["receipt"]),
        "--lineage-policy",
        str(decisions["lineage-policy"]),
        "--resources",
        str(decisions["resources"]),
        "--policy",
        str(decisions["policy"]),
        "--plan-root",
        str(roots["plans"]),
        "--scratch",
        str(roots["scratch"]),
        "--output",
        str(roots["output"]),
        *([] if admission is None else ["--admission", str(admission)]),
    ]


def fresh_roots(base: Path) -> dict[str, Path]:
    return {"plans": base / "plans", "scratch": base / "scratch", "output": base / "output"}


def tree_digest(*roots: Path) -> str:
    """SHA-256 of every file below ``roots`` (proves historical state is untouched)."""
    rows = []
    for root in roots:
        for path in sorted(root.rglob("*")):
            if path.is_file():
                rows.append([path.as_posix(), hashlib.sha256(path.read_bytes()).hexdigest()])
    return canonical.digest(rows)
