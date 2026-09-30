"""Fast-track freeze of the Essential-Web production selector (B-normal).

Scientific/operational amendment ``essential-web-selector-fasttrack-v1``.
The frozen evidence protocol planned a blinded review of the Arm-T texts
by two independent human reviewers before any selector decision. Only
one human is available, so that review is NOT RUN. This module records
that status, keeps the sealed Arm-T package bound for possible future
work, and freezes the production selector from metadata evidence only:
the sealed Arm-M result and the hash-verified development sweep.

Nothing here reads Arm-T text, labels, review IDs or mappings: the only
Arm-T input is the Git-committed package manifest (hashes and counts).
No label is fabricated and no model stands in for a reviewer. The
selector is the unchanged pre-registered policy ``B`` at tier ``normal``;
this module extracts its definition from the frozen spec and never
re-implements or tunes it.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml

from xlm.data.evidence_v2 import canonical, m_analysis

FREEZE_VERSION = "essential-web-selector-fasttrack-v1"
FREEZE_KIND = "essential_web_selector_fasttrack_freeze"
FREEZE_NAME = "freeze.json"
SELECTOR_ID = "essential-web-b-normal"
SELECTED_POLICY = "B"
SELECTED_TIER = "normal"
SELECTED_CONDITION = "B-normal"
M_SEAL_DIGEST = "afc972ccf570a5587bf7bd3b6b8d1d728a09f2230c49f1b0a19c1c74cda0b9bc"
M_RESULT_COMMIT = "65edfd0a9e3b33b2b98cc9423223e31506db59a5"
POLICY_DIGEST = "f4357f61f434d5105d823266153dac6372e122f12767187631e8796b4899dd07"
T_PACKAGE_DIGEST = "18c95b95699922db325626fd8776c2317231c51666f9bcc42898f5811dabc58d"
T_PACKAGE_COMMIT = "e596f19cde59d6f9b922ebc4069cf79b231c27c0"
T_ARM_STATUS = "NOT_RUN_NO_TWO_INDEPENDENT_HUMAN_REVIEWERS"
T_MANIFEST_NAME = "package_manifest.json"
T_MANIFEST_KIND = "essential_web_t_blinded_review_package"
T_LOCATORS = 118
T_REVIEWABLE = 117
T_UNREVIEWABLE = 1
ARM_ROWS = m_analysis.ARM_ROWS
COMBOS = m_analysis.COMBOS
ADMITTED_COMPONENTS = m_analysis.SELECTED
NON_ADMITTED_FINALS = ("unassigned", "rejected")
PRECEDENCE = ("science", "practical", "prose")
_COUNT_KEYS = ("science", "practical", "prose", "unassigned", "rejected")
_FINAL_FOR = {
    "science": "essential_science",
    "practical": "essential_practical",
    "prose": "essential_prose",
    "unassigned": "unassigned",
    "rejected": "rejected",
}
_B_RULES = {
    "science": {"any": ["S5", "S61"]},
    "practical": {"all": ["P"]},
    "prose": {"all": ["R"]},
}
_T_REVIEW_PARTS = frozenset(
    {"reviewer-1", "reviewer-2", "custodian", "sealed", "texts", "view", "forms", "xlm-review"}
)
_UNIVERSES = (
    "knowledge_labels",
    "correctness_allowed",
    "correctness_rejected",
    "doctype_union",
    "doctype_known_excluded",
    "artifacts_known",
    "missing_known",
)


class FreezeError(ValueError):
    """Any binding, count, status or identity deviation: refuse."""


def _binding(raw: bytes) -> dict[str, Any]:
    return {"bytes": len(raw), "sha256": m_analysis.sha256_bytes(raw)}


def refuse_t_review_material(path: Path) -> None:
    """Only the Git-committed package manifest may be read; never review material."""
    parts = {part.lower() for part in path.parts}
    if path.name != T_MANIFEST_NAME or parts & _T_REVIEW_PARTS:
        raise FreezeError(f"refusing Arm-T review material: {path.name}")


def condition_counts(summary: Mapping[str, Any], *, what: str) -> dict[str, dict[str, int]]:
    """Eight-condition final counts from one sweep summary; conservation enforced."""
    combos = summary.get("combos")
    if not isinstance(combos, Mapping) or sorted(combos) != sorted(COMBOS):
        raise FreezeError(f"{what} summary does not hold exactly the eight frozen conditions")
    if summary.get("input_records") != ARM_ROWS:
        raise FreezeError(f"{what} summary is not a {ARM_ROWS}-row replicate")
    if summary.get("multi_final_violations") != 0:
        raise FreezeError(f"{what} summary reports rows with more than one final component")
    counts: dict[str, dict[str, int]] = {}
    for combo in COMBOS:
        final = combos[combo].get("final")
        if not isinstance(final, Mapping) or sorted(final) != sorted(_FINAL_FOR.values()):
            raise FreezeError(f"{what} {combo} does not hold exactly the five final components")
        row = {key: final[_FINAL_FOR[key]] for key in _COUNT_KEYS}
        if any(type(value) is not int or value < 0 for value in row.values()):
            raise FreezeError(f"{what} {combo} holds a non-integer count")
        if sum(row.values()) != ARM_ROWS or combos[combo].get("conservation_ok") is not True:
            raise FreezeError(f"{what} {combo} does not conserve {ARM_ROWS} rows")
        counts[combo] = row
    return counts


def load_m_evidence(
    m_dir: Path, *, expected_seal_digest: str = M_SEAL_DIGEST
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Verify the sealed M package on disk; return (seal, M summary, binding)."""
    seal = m_analysis.verify_seal(m_dir)
    if seal["seal_digest"] != expected_seal_digest:
        raise FreezeError("M seal digest is not the expected sealed M result")
    if seal.get("status") != "SEALED" or seal.get("arm") != "M":
        raise FreezeError("M seal is not a SEALED Arm-M result")
    if seal.get("other_arm_material_bound") is not False:
        raise FreezeError("M seal binds other-arm material")
    name = "m_sweep/summary.json"
    raw = (m_dir / name).read_bytes()
    if _binding(raw) != seal["results"].get(name):
        raise FreezeError("M summary is not the sealed M summary")
    binding = {
        "seal_digest": seal["seal_digest"],
        "seal_file": _binding((m_dir / m_analysis.SEAL_NAME).read_bytes()),
        "artifact_manifest_digest": seal["artifact_manifest_digest"],
        "summary": {"name": name, **_binding(raw)},
        "records": seal["adapted_input"]["records"],
        "derived_input_sha256": seal["adapted_input"]["sha256"],
        "source_records_sha256": seal["source"]["records"]["sha256"],
        "post_seal_rule": seal["post_seal_rule"],
        "modified_by_this_freeze": False,
    }
    return seal, json.loads(raw.decode("utf-8")), binding


def load_development(dev_dir: Path, seal: Mapping[str, Any]) -> tuple[dict[str, Any], Any]:
    """Hash-verify the frozen development summary against the M seal's binding."""
    declared = seal["development"]
    manifest_raw = (dev_dir / "sweep_manifest.json").read_bytes()
    if m_analysis.sha256_bytes(manifest_raw) != declared["manifest"]["sha256"]:
        raise FreezeError("development sweep manifest differs from the M seal's binding")
    raw = (dev_dir / "summary.json").read_bytes()
    if _binding(raw) != declared["artifacts"]["summary.json"]:
        raise FreezeError("development summary differs from the M seal's binding")
    binding = {
        "manifest": dict(declared["manifest"]),
        "summary": {"name": "summary.json", **_binding(raw)},
        "binding": dict(declared["binding"]),
        "artifacts": dict(declared["artifacts"]),
        "source": "hash-verified frozen sweep artifacts",
    }
    return json.loads(raw.decode("utf-8")), binding


def bind_evaluator(
    evaluator_path: Path,
    policy_path: Path,
    seal: Mapping[str, Any],
    *,
    expected_policy_digest: str = POLICY_DIGEST,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Bind the unchanged evaluator and policy files; return (spec, binding)."""
    sealed = seal["evaluator"]
    evaluator_raw = evaluator_path.read_bytes()
    if _binding(evaluator_raw) != {"bytes": sealed["bytes"], "sha256": sealed["sha256"]}:
        raise FreezeError("evaluator file differs from the evaluator bound in the M seal")
    policy_raw = policy_path.read_bytes()
    spec = yaml.safe_load(policy_raw.decode("utf-8"))
    if not isinstance(spec, dict):
        raise FreezeError("policy spec is not a YAML mapping")
    digest = canonical.digest(spec)
    sealed_policy = sealed["policy"]
    if digest != expected_policy_digest or digest != sealed_policy["digest"]:
        raise FreezeError("policy spec digest differs from the frozen selector policy digest")
    if _binding(policy_raw) != {"bytes": sealed_policy["bytes"], "sha256": sealed_policy["sha256"]}:
        raise FreezeError("policy spec file differs from the file bound in the M seal")
    binding = {
        "path": sealed["path"],
        **_binding(evaluator_raw),
        "tool_version": sealed["tool_version"],
        "report_schema_version": sealed["report_schema_version"],
        "policy": {
            "path": "recipes/selectors/essential_web_selector_sweep_v1.yaml",
            **_binding(policy_raw),
            "digest": digest,
            "policy_spec_version": spec.get("policy_spec_version"),
        },
    }
    return spec, binding


def selector_semantics(spec: Mapping[str, Any]) -> dict[str, Any]:
    """The exact frozen definition of B-normal, extracted (never rewritten)."""
    policy = spec["policies"][SELECTED_POLICY]
    gate_name = policy[SELECTED_TIER]
    if gate_name != "GN":
        raise FreezeError("policy B tier normal no longer binds gate GN")
    rules = {name: policy[name] for name in PRECEDENCE}
    if rules != _B_RULES:
        raise FreezeError("policy B component rules differ from the reviewed definition")
    if list(spec["precedence"]) != list(PRECEDENCE):
        raise FreezeError("precedence is not science, practical, prose")
    return {
        "policy": SELECTED_POLICY,
        "tier": SELECTED_TIER,
        "validity": {key: spec[key] for key in ("fdc_syntax", "english_min", "english_max")},
        "label_universes": {key: list(spec[key]) for key in _UNIVERSES},
        "gate_name": gate_name,
        "gate": dict(spec["gates"][gate_name]),
        "component_rules": rules,
        "predicates": dict(spec["predicates"]),
        "precedence": list(spec["precedence"]),
        "final_components": list(spec["final_components"]),
    }


def t_arm_record(
    manifest_path: Path,
    *,
    expected_package_digest: str = T_PACKAGE_DIGEST,
    expected_seal_digest: str = M_SEAL_DIGEST,
) -> dict[str, Any]:
    """Arm-T status from the committed package manifest only: NOT RUN."""
    refuse_t_review_material(manifest_path)
    raw = manifest_path.read_bytes()
    manifest = json.loads(raw.decode("utf-8"))
    if manifest.get("kind") != T_MANIFEST_KIND or manifest.get("status") != "SEALED":
        raise FreezeError("Arm-T package manifest is not a SEALED blinded review package")
    if manifest.get("package_digest") != expected_package_digest:
        raise FreezeError("Arm-T package digest is not the sealed package commitment")
    if manifest["m_seal_parent"]["seal_digest"] != expected_seal_digest:
        raise FreezeError("Arm-T package is not bound to the sealed M result")
    if manifest.get("human_labels_assigned") != 0 or manifest.get("unblinded") is not False:
        raise FreezeError("Arm-T package records labels or unblinding; NOT RUN cannot be claimed")
    if manifest.get("selector_decision") != "NOT MADE":
        raise FreezeError("Arm-T package records a selector decision")
    counts = manifest["counts"]
    observed = (
        counts["master_entries"],
        counts["reviewable"],
        counts["unreviewable_oversized"],
        counts["missing_or_invalid"],
    )
    if observed != (T_LOCATORS, T_REVIEWABLE, T_UNREVIEWABLE, 0):
        raise FreezeError("Arm-T package counts differ from 118 = 117 reviewable + 1 oversized")
    return {
        "status": T_ARM_STATUS,
        "semantic_review": "NOT RUN",
        "reason": "only one human is available; the frozen design needs two independent "
        "human reviewers and no substitute is permitted",
        "frozen_review_design": manifest["review_identity"]["adjudication"],
        "acquired_locators": counts["master_entries"],
        "reviewable": counts["reviewable"],
        "unreviewable_oversized": counts["unreviewable_oversized"],
        "blinded_package": {
            "status": "SEALED",
            "package_digest": manifest["package_digest"],
            "key_commitment": manifest["blinding"]["key_commitment"],
            "manifest": {"name": T_MANIFEST_NAME, **_binding(raw)},
            "commit": T_PACKAGE_COMMIT,
            "preserved_for_future_research": True,
        },
        "human_labels_collected": 0,
        "adjudication": "NOT RUN",
        "unblinding": "NOT RUN",
        "substitutes_used": {
            "model_labels": False,
            "synthetic_labels": False,
            "single_reviewer_labels": False,
        },
        "text_inspected_for_this_freeze": False,
        "used_for_selector_decision": False,
        "evidence_contribution": "none: the arm supplies neither acceptance nor rejection "
        "evidence to this decision",
    }


def _pp(count: int) -> float:
    return round(100.0 * count / ARM_ROWS, 4)


def _pair(dev: Mapping[str, int], m: Mapping[str, int], key: str) -> dict[str, Any]:
    return {
        "development": dev[key],
        "m": m[key],
        "delta_rows": m[key] - dev[key],
        "delta_percentage_points": round(_pp(m[key]) - _pp(dev[key]), 4),
    }


def _selected(row: Mapping[str, int]) -> int:
    return row["science"] + row["practical"] + row["prose"]


def build_rationale(
    dev: Mapping[str, Mapping[str, int]],
    m: Mapping[str, Mapping[str, int]],
    spec: Mapping[str, Any],
    invariants: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Descriptive metadata-only reasons; every number is recomputed from the counts."""
    b_dev, b_m = dev["B-normal"], m["B-normal"]
    moves = {key: _pair(b_dev, b_m, key) for key in _COUNT_KEYS}
    largest = max(abs(move["delta_percentage_points"]) for move in moves.values())
    strict_equal = all(
        side["B-strict"] == side["D-strict"]
        and invariants[name].get("b_strict_d_strict_agreement") == ARM_ROWS
        for name, side in (("development", dev), ("m", m))
    )
    if not strict_equal:
        raise FreezeError("B-strict and D-strict are not identical in both replicates")
    gates = spec["gates"]
    relaxed = sorted(set(gates["GD"]["artifacts"]) - set(gates["GN"]["artifacts"]))
    return [
        {
            "id": "replication",
            "statement": "B-normal counts on the confirmation replicate M are close to the "
            "development replicate.",
            "facts": {**moves, "largest_absolute_share_move_percentage_points": largest},
        },
        {
            "id": "coverage_versus_c",
            "statement": "B-normal keeps substantially more practical and prose rows than "
            "C-normal; science is identical.",
            "facts": {
                name: {
                    "b_practical": side["B-normal"]["practical"],
                    "c_practical": side["C-normal"]["practical"],
                    "b_prose": side["B-normal"]["prose"],
                    "c_prose": side["C-normal"]["prose"],
                    "b_science": side["B-normal"]["science"],
                    "c_science": side["C-normal"]["science"],
                }
                for name, side in (("development", dev), ("m", m))
            },
        },
        {
            "id": "science_extension_versus_a",
            "statement": "B-normal includes the S61 science extension that A-normal lacks.",
            "facts": {
                name: {
                    "a_science": side["A-normal"]["science"],
                    "b_science": side["B-normal"]["science"],
                    "added_science_rows": side["B-normal"]["science"] - side["A-normal"]["science"],
                }
                for name, side in (("development", dev), ("m", m))
            },
        },
        {
            "id": "narrower_than_d",
            "statement": "B-normal avoids D-normal's relaxed artifact admission, which "
            "roughly doubles the admitted rows.",
            "facts": {
                "artifact_labels_admitted_only_by_d_normal_gate": relaxed,
                **{
                    name: {
                        "b_selected_rows": _selected(side["B-normal"]),
                        "d_selected_rows": _selected(side["D-normal"]),
                        "rows_b_rejects_that_d_passes": side["B-normal"]["rejected"]
                        - side["D-normal"]["rejected"],
                    }
                    for name, side in (("development", dev), ("m", m))
                },
            },
        },
        {
            "id": "strict_not_selected",
            "statement": "Strict science counts are sparse and moved more between "
            "replicates, so the strict tier is not selected; B-strict and D-strict are "
            "identical.",
            "facts": {
                "b_strict_science": _pair(dev["B-strict"], m["B-strict"], "science"),
                "b_normal_science": moves["science"],
                "b_strict_equals_d_strict_in_both_replicates": True,
            },
        },
    ]


LIMITATIONS = (
    "The Arm-T semantic review was not run, so no semantic quality of any policy was "
    "assessed; B-normal is not a T-validated winner.",
    "The condition was chosen with both replicates visible, so M is a descriptive "
    "replication of the counts, not an independent test of the choice.",
    "The frozen evidence protocol planned the blinded two-reviewer T review before the "
    "selector decision; this amendment departs from that plan by explicit operator decision.",
    "Both replicates are one contiguous 512-row window per crawl: clustered, not iid. No "
    "confidence interval, test or ranking statistic is reported.",
    "Science remains sparse under B-normal (every crawl cell below 20 rows in both replicates).",
    "Rows that pass the gate but match no component are unassigned and are not admitted.",
)


def build_freeze(
    *,
    seal: Mapping[str, Any],
    m_binding: Mapping[str, Any],
    m_summary: Mapping[str, Any],
    development_summary: Mapping[str, Any],
    development_binding: Mapping[str, Any],
    spec: Mapping[str, Any],
    evaluator_binding: Mapping[str, Any],
    t_arm: Mapping[str, Any],
    mixture: Mapping[str, Any],
    code: Mapping[str, Any],
    parent_commit: str,
    decision_date: str,
    commands: Sequence[str],
    environment: Mapping[str, Any],
) -> dict[str, Any]:
    """Canonical freeze over every parent, the decision and its rationale."""
    dev_counts = condition_counts(development_summary, what="development")
    m_counts = condition_counts(m_summary, what="M")
    semantics = selector_semantics(spec)
    invariants = {
        "development": development_summary["identity_invariants"],
        "m": m_summary["identity_invariants"],
    }
    body: dict[str, Any] = {
        "kind": FREEZE_KIND,
        "version": FREEZE_VERSION,
        "status": "FROZEN",
        "decision_date": decision_date,
        "scientific_namespace": seal["scientific_namespace"],
        "source": {
            "repository": seal["source"]["repository"],
            "revision": seal["source_revision"],
        },
        "m_evidence": {**dict(m_binding), "result_commit": M_RESULT_COMMIT},
        "development_evidence": dict(development_binding),
        "evaluator": dict(evaluator_binding),
        "production_selector": {
            "id": SELECTOR_ID,
            "condition": SELECTED_CONDITION,
            "policy": SELECTED_POLICY,
            "tier": SELECTED_TIER,
            "role": "production Essential-Web selector for Mix-01",
            "policy_digest": evaluator_binding["policy"]["digest"],
            "evaluator_sha256": evaluator_binding["sha256"],
            "semantics": semantics,
            "semantics_digest": canonical.digest(semantics),
            "admitted_components": list(ADMITTED_COMPONENTS),
            "non_admitted_finals": list(NON_ADMITTED_FINALS),
            "precedence": list(PRECEDENCE),
            "tuning_after_m": "none",
        },
        "counts": {"development": dev_counts, "m": m_counts},
        "t_arm": dict(t_arm),
        "decision": {
            "selector": SELECTED_CONDITION,
            "type": "explicit operator and scientific fast-track decision",
            "evidence_basis": [
                "sealed Arm-M metadata evidence",
                "hash-verified development metadata evidence",
            ],
            "t_validated": False,
            "semantic_quality_claims": "none",
            "prior_state": {
                "m_seal_selector_decision": seal["selector_decision"],
                "m_seal_policy_ranking": seal["policy_ranking"],
            },
        },
        "rationale": build_rationale(dev_counts, m_counts, spec, invariants),
        "limitations": list(LIMITATIONS),
        "mixture": dict(mixture),
        "code": dict(code),
        "parent_commit": parent_commit,
        "commands": list(commands),
        "environment": dict(environment),
    }
    body["freeze_digest"] = canonical.digest(body)
    return body


def freeze_digest_of(freeze: Mapping[str, Any]) -> str:
    return canonical.digest({k: v for k, v in freeze.items() if k != "freeze_digest"})


def load_freeze(path: Path) -> dict[str, Any]:
    """Load a freeze file and recheck its self-digest."""
    freeze = json.loads(path.read_bytes().decode("utf-8"))
    if not isinstance(freeze, dict) or freeze.get("kind") != FREEZE_KIND:
        raise FreezeError("not an Essential-Web fast-track freeze")
    if freeze_digest_of(freeze) != freeze.get("freeze_digest"):
        raise FreezeError("freeze digest mismatch")
    return freeze
