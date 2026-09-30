"""Hash-bound dry preparation only: no selector evaluation, K, text copies or labels."""

from __future__ import annotations

import subprocess
from pathlib import Path

from review_result import (
    EXPECTED,
    OUT,
    REPO,
    D,
    binding,
    canonical,
    check,
    load,
    sealed,
    sha,
    write,
)

from xlm.data.evidence_v2 import frozen
from xlm.data.evidence_v4 import phase_d
from xlm.data.evidence_v4 import phase_d_plan as pd


def main() -> None:
    result = load(OUT / "review-result.json")
    check(result["status"] == "PASS", "result review required")
    plan = pd.load_committed_plan()
    dev_root = Path("F:/Project/xlm-selector-sweeps/essential-web-v2")
    dev_manifest_path = dev_root / "sweep_manifest.json"
    dev = sealed(dev_manifest_path)
    check(dev["digest"] == frozen.SWEEP_MANIFEST_DIGEST, "development manifest identity")
    check(
        binding(dev_manifest_path)["sha256"] == frozen.SWEEP_MANIFEST_FILE_SHA256,
        "development manifest bytes",
    )
    check(
        dev["policy_digest"] == EXPECTED["policy_digest"] and dev["tool_version"] == "2",
        "development policy/tool",
    )
    check(
        dev["binding"]
        == dict(
            bundle_digest=frozen.DEV_BUNDLE_DIGEST,
            combined_sha256=frozen.DEV_COMBINED_SHA256,
            execution_digest=frozen.DEV_EXECUTION_DIGEST,
            parts=8,
            records=4096,
            revision=frozen.REVISION,
        ),
        "development input identity",
    )
    dev_bindings = {}
    for name, bound in dev["artifacts"].items():
        check(Path(name).name == name, "development artifact name")
        measured = binding(dev_root / name)
        check(measured == bound, "development artifact hash: " + name)
        dev_bindings[name] = measured
    check(
        sha(canonical(load(dev_root / "policy_spec.json"))) == EXPECTED["policy_digest"],
        "development policy body",
    )
    evaluator = "scripts/essential_web_selector_sweep.py"
    selection_path = Path("F:/Project/xlm-evidence-v2/essential-web/text_selection_manifest.json")
    selection = sealed(selection_path)
    evaluator_blob = subprocess.check_output(["git", "show", f"HEAD:{evaluator}"], cwd=REPO)
    evaluator_working = (REPO / evaluator).read_bytes()
    check(sha(evaluator_blob) == selection["evaluator_code_sha256"], "frozen evaluator code")
    check(
        evaluator_working.replace(b"\r\n", b"\n") == evaluator_blob,
        "working evaluator semantics differs",
    )
    code = {
        name: binding(REPO / name)
        for name in [
            evaluator,
            "src/xlm/data/evidence_v2/blinding.py",
            "src/xlm/data/evidence_v2/rubric.py",
            "src/xlm/data/evidence_v2/frozen.py",
            "recipes/selectors/essential_web_selector_sweep_v1.yaml",
        ]
    }
    protocol = REPO / "docs/implementation/reports/ESSENTIAL-WEB-EVIDENCE-V2-PROTOCOL.md"
    common = {
        "kind": "essential_web_phase_d_analysis_preparation",
        "status": "DRY_PREPARATION_ONLY",
        "audience": "CUSTODIAN ONLY; never distribute this manifest as a reviewer package",
        "result_review": binding(OUT / "review-result.json"),
        "phase_d_plan_digest": plan.digest,
        "scientific_namespace": frozen.PROTOCOL_VERSION,
        "selection_digest": EXPECTED["selection_digest"],
        "policy_digest": EXPECTED["policy_digest"],
        "scientific_protocol": {"path": str(protocol), **binding(protocol)},
        "human_labels_assigned": 0,
        "selector_evaluation_run": False,
        "final_selector_decision": "NOT MADE",
    }
    m = {
        **common,
        "arm": "M",
        "input": {
            "path": str(D / phase_d.M_OUTPUT),
            **binding(D / phase_d.M_OUTPUT),
            "records": 4096,
            "projection": ["eai_taxonomy", "quality_signals"],
            "manifest": {"path": str(D / phase_d.M_MANIFEST), **binding(D / phase_d.M_MANIFEST)},
        },
        "windows": [
            {"file": f.file, "start": f.window[0], "stop": f.window[1], "records": 512}
            for f in plan.m_files
        ],
        "frozen_evaluator": {
            "path": evaluator,
            "tool_version": 2,
            "report_schema_version": 2,
            "git_blob_sha256": sha(evaluator_blob),
            "working_file": code[evaluator],
        },
        "development": {
            "resolved_path": str(dev_root),
            "original_protocol_path": "G:/Project/xlm-selector-sweeps/essential-web-v2",
            "relocation_verified_by_exact_frozen_hashes": True,
            "manifest": binding(dev_manifest_path),
            "digest": dev["digest"],
            "binding": dev["binding"],
            "artifacts": dev_bindings,
        },
        "policies": ["A", "B", "C", "D"],
        "tiers": ["normal", "strict"],
        "input_bridge": {
            "required": True,
            ("reason"): (
                "Phase-D acquisition provenance uses row; existing sweep CLI requi"
                "res row_index plus original bundle/execution schemas."
            ),
            ("mapping"): (
                "_xlm_acquisition.row -> _xlm_acquisition.row_index in a separate "
                "derived analysis view only"
            ),
            ("row_identity_and_order"): (
                "preserve repository, revision, source_file, absolute row, all 409"
                "6 identities, and original raw input hash"
            ),
            ("metadata_fields"): (
                "eai_taxonomy and quality_signals unchanged; no coercion or filtering"
            ),
            ("binding"): (
                "write an explicit Phase-D analysis-input binding; never fabricate"
                " legacy bundle/execution receipts or alter frozen raw evidence"
            ),
            ("evaluation"): (
                "reuse existing validate_row and Sweep policy/aggregation kernels;"
                " bind bridge code and test correspondence before executing analys"
                "is"
            ),
            "current_sweep_cli_accepts_raw_phase_d_input": False,
        },
        ("comparisons_contract"): (
            "scientific protocol section 5, all predeclared tables; 512/crawl,"
            " 4096/replicate; separate development/M and matched-crawl differe"
            "nces"
        ),
        "seal_before_T_unblinding": True,
        "limitations": [
            "clustered contiguous windows, not iid",
            "no policy tuning, winner selection, token extrapolation or relabeling as final test",
            (
                "development raw X: bundle unavailable at its historical path; eig"
                "ht frozen corrected result artifacts verified at resolved F: path"
            ),
        ],
        "analysis_caps": dev["caps"],
    }
    m["digest"] = sha(canonical(m))
    write("m-analysis-preparation.json", m)
    entries = load(OUT / "t-entry-bindings.json")
    check(len(entries["entries"]) == 118, "T entry preparation count")
    t = {
        **common,
        "arm": "T",
        "input": {
            "documents": {"path": str(D / phase_d.T_DOCUMENTS), **binding(D / phase_d.T_DOCUMENTS)},
            "provenance": {
                "path": str(D / phase_d.T_PROVENANCE),
                **binding(D / phase_d.T_PROVENANCE),
            },
            "selection": {"path": str(selection_path), **binding(selection_path)},
            "entry_bindings": binding(OUT / "t-entry-bindings.json"),
        },
        "entries": 118,
        "reviewable_full_text_entries": 117,
        "unreviewable_oversized_entries": 1,
        "unique_reviewable_texts": result["T"]["unique_full_texts"],
        "full_text_bytes": result["T"]["retained_text_bytes"],
        "review_ids_materialized": False,
        "secret_generated": False,
        "review_order_materialized": False,
        "existing_id_artifacts_in_validated_roots": [],
        "blinding": {
            "implementation": "src/xlm/data/evidence_v2/blinding.py",
            "namespace": frozen.PROTOCOL_VERSION,
            "id_tag": frozen.REVIEW_ID_TAG,
            ("id_formula"): (
                "ew2- + HMAC-SHA256(K,C([essential-web-evidence-v2.0,review-id,loc"
                "ator])).hexdigest()"
            ),
            ("K"): (
                "reuse any previously sealed matching K/IDs/order; if none exist, "
                "generate fresh 32-byte K ONCE outside Git and outside both eviden"
                "ce roots, keep access-restricted, publish SHA-256 commitment only"
            ),
            "review_order_seed": frozen.REVIEW_ORDER_SEED,
            "reviewers": list(frozen.REVIEWERS),
            ("order_formula"): (
                "ascending H([essential-web-evidence-v2.0,review-order,20260928,re"
                "viewer,review_id]), tie by ID; no reroll"
            ),
            "prohibited_fields": sorted(frozen.FORBIDDEN_PACKAGE_FIELDS),
            ("oversized"): (
                "retain the 118th opaque-ID status entry, with no text or excerpt;"
                " keep its scientific dimensions not_reviewed, never substantive l"
                "abels"
            ),
            ("helper_limit"): (
                "build_package requires nonempty text; pass 117 reviewable entries"
                " to it and keep the oversized opaque-ID nonreviewable record sepa"
                "rately in the same 118-entry master ledger. Do not pass null text"
                " as reviewable or drop the locator."
            ),
            ("reviewer_payload"): (
                "opaque ID, full unmodified text where reviewable, rubric, blank f"
                "orms; no selector ownership/category, B-vs-D, source file/row/cra"
                "wl, hypothesis condition or selection manifest"
            ),
            ("rendering"): (
                "offline escaped plain text; no active links, scripts, remote asse"
                "ts, or embedded source metadata"
            ),
        },
        "workflow": [
            "verify source hashes and any pre-existing custodian package before materialization",
            (
                "materialize in a new access-controlled external directory; no doc"
                "ument text, secret or locator-to-ID mapping in Git"
            ),
            (
                "preserve all 118 selected locators and original status; 117 full-"
                "text review forms per reviewer"
            ),
            "seal M comparison report before any T unblinding",
            (
                "separate explicit authorization before assigning labels; no label"
                "s in this preparation"
            ),
        ],
        "rubric": code["src/xlm/data/evidence_v2/rubric.py"],
        "output_limits": {
            "selected_entries": 118,
            "retained_text_bytes": 8388608,
            "all_preparation_storage_including_partial_files_bytes": 33554432,
            "network_requests": 0,
        },
        "reviewer_distribution_ready": False,
    }
    t["digest"] = sha(canonical(t))
    write("t-analysis-preparation.json", t)
    write("analysis-code-bindings.json", code)
    print(
        "DRY PREPARATION READY: M input/windows/evaluator and all eight de"
        "velopment artifacts bound; T 118 entry bindings, 117 reviewable, "
        "one oversized. No evaluation, secret, IDs, order, text copy or la"
        "bels generated."
    )


if __name__ == "__main__":
    main()
