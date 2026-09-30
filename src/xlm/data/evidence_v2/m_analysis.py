"""Frozen Arm-M selector-evidence analysis: evaluate, compare, seal.

Evidence reporting only. The frozen evaluator module and policy spec are
hash-verified and used unchanged; this module never re-implements selector
logic, never ranks policies and never makes a selector decision. The
development replicate is read from its hash-verified frozen result
artifacts (its raw bundle is unavailable and is not re-evaluated).

Protocol section 5 governs reporting: counts use 512/crawl and 4096/arm
denominators, component shares use selected-component denominators, a
zero denominator is undefined (``None``), and no binomial confidence
interval, bootstrap or significance claim is made. The seal binds source,
adapter, evaluator, policy, development parents and every result; it
holds no Arm-T material and refuses any path that names it.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from xlm.data.evidence_v2 import canonical

PREPARATION_DIGEST = "925ed40b6709519863eb6c0069386da5a0f9ae3f649beb63205f9b0694a0a246"
SEAL_KIND = "essential_web_m_selector_evidence_seal"
SEAL_NAME = "m_seal.json"
ARTIFACT_MANIFEST_NAME = "artifact_manifest.json"
SELECTED = ("essential_science", "essential_practical", "essential_prose")
_PRECEDENCE = ("science", "practical", "prose")
COMPONENTS = (*SELECTED, "unassigned", "rejected")
POLICIES = ("A", "B", "C", "D")
TIERS = ("normal", "strict")
COMBOS = tuple(f"{policy}-{tier}" for policy in POLICIES for tier in TIERS)
EFFECT_PAIRS = (("A", "B"), ("B", "C"), ("B", "D"))
GATES = ("GN", "GS", "GD")
ROWS_PER_CRAWL = 512
ARM_ROWS = 4096
SPARSE_CELL = 20
DEVELOPMENT_ARTIFACTS = (
    "attrition.json",
    "crosstabs.json",
    "diagnostics.json",
    "overlaps.json",
    "per_crawl.json",
    "policy_spec.json",
    "summary.json",
    "summary.md",
)
SWEEP_PAYLOADS = (
    "summary.json",
    "per_crawl.json",
    "attrition.json",
    "overlaps.json",
    "crosstabs.json",
    "diagnostics.json",
)
_T_NAME_PREFIXES = ("t_", "t-")
_T_DIRECTORIES = ("sealed",)
STATISTICS_NOTE = (
    "Descriptive only. Both replicates are one contiguous 512-row window per crawl/file: "
    "clustered, not iid. Protocol section 5 forbids binomial iid confidence intervals, "
    "eight-window bootstrap confidence claims and significance claims, so none is reported; "
    "uncertainty is shown as per-crawl ranges, spreads and matched-crawl differences."
)


class AnalysisError(ValueError):
    """Any binding, conservation or sealing failure: refuse."""


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def dumps(obj: Any) -> bytes:
    """Deterministic readable JSON (sorted keys, LF, finite numbers only)."""
    text = json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False)
    return (text + "\n").encode("utf-8")


def refuse_t_material(path: Path) -> None:
    """Arm-T files are never opened or bound before the M seal exists."""
    parts = [part.lower() for part in path.parts]
    name = path.name.lower()
    if name.startswith(_T_NAME_PREFIXES) or any(part in _T_DIRECTORIES for part in parts):
        raise AnalysisError(f"refusing Arm-T material before the M seal: {path.name}")


def load_preparation(path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """Load the frozen M preparation manifest; pin its canonical digest."""
    refuse_t_material(path)
    raw = path.read_bytes()
    body = canonical.loads_bytes_strict(raw)
    if not isinstance(body, dict) or canonical.self_digest(body) != body.get("digest"):
        raise AnalysisError("preparation manifest self-digest mismatch")
    if body["digest"] != PREPARATION_DIGEST:
        raise AnalysisError("preparation manifest is not the frozen M preparation")
    if body.get("arm") != "M" or body.get("seal_before_T_unblinding") is not True:
        raise AnalysisError("preparation manifest is not an Arm-M seal-before-T preparation")
    return body, {"bytes": len(raw), "sha256": sha256_bytes(raw), "digest": body["digest"]}


def verify_evaluator(
    evaluator_path: Path, policy_path: Path, preparation: Mapping[str, Any], evaluator: Any
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Bind the unchanged evaluator file and policy spec; return (spec, binding)."""
    frozen = preparation["frozen_evaluator"]
    raw = evaluator_path.read_bytes()
    if sha256_bytes(raw) != frozen["working_file"]["sha256"]:
        raise AnalysisError("evaluator file differs from the frozen evaluator hash")
    if Path(str(evaluator.__file__)).resolve() != evaluator_path.resolve():
        raise AnalysisError("loaded evaluator module is not the hash-verified file")
    if str(evaluator.TOOL_VERSION) != str(frozen["tool_version"]):
        raise AnalysisError("evaluator tool version differs from the frozen version")
    if evaluator.REPORT_SCHEMA_VERSION != frozen["report_schema_version"]:
        raise AnalysisError("evaluator report schema differs from the frozen version")
    spec, digest = evaluator.load_policy_spec(policy_path)
    if digest != preparation["policy_digest"]:
        raise AnalysisError("policy spec digest differs from the frozen selector policy digest")
    if tuple(evaluator.POLICIES) != POLICIES or tuple(evaluator.TIERS) != TIERS:
        raise AnalysisError("evaluator policy/tier set differs from A-D normal/strict")
    if tuple(evaluator.FINAL_COMPONENTS) != COMPONENTS:
        raise AnalysisError("evaluator final components differ")
    policy_raw = policy_path.read_bytes()
    binding = {
        "path": frozen["path"],
        "bytes": len(raw),
        "sha256": sha256_bytes(raw),
        "tool_version": str(evaluator.TOOL_VERSION),
        "report_schema_version": evaluator.REPORT_SCHEMA_VERSION,
        "policy": {
            "bytes": len(policy_raw),
            "sha256": sha256_bytes(policy_raw),
            "digest": digest,
        },
    }
    return spec, binding


def verify_development(
    dev_dir: Path, preparation: Mapping[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Hash-verify the frozen development artifacts; return (artifacts, binding)."""
    declared = preparation["development"]
    if sorted(declared["artifacts"]) != sorted(DEVELOPMENT_ARTIFACTS):
        raise AnalysisError("preparation lists an unexpected development artifact set")
    manifest_raw = (dev_dir / "sweep_manifest.json").read_bytes()
    if sha256_bytes(manifest_raw) != declared["manifest"]["sha256"]:
        raise AnalysisError("development sweep manifest differs from its frozen hash")
    manifest = json.loads(manifest_raw.decode("utf-8"))
    if canonical.self_digest(manifest) != manifest.get("digest"):
        raise AnalysisError("development sweep manifest self-digest mismatch")
    if manifest["digest"] != declared["digest"] or manifest["binding"] != declared["binding"]:
        raise AnalysisError("development sweep manifest differs from the frozen binding")
    if manifest.get("policy_digest") != preparation["policy_digest"]:
        raise AnalysisError("development sweep used a different policy digest")
    artifacts: dict[str, Any] = {}
    bound: dict[str, Any] = {}
    for name in DEVELOPMENT_ARTIFACTS:
        raw = (dev_dir / name).read_bytes()
        want = declared["artifacts"][name]
        got = {"bytes": len(raw), "sha256": sha256_bytes(raw)}
        if got != want or manifest["artifacts"].get(name) != want:
            raise AnalysisError(f"development artifact {name} differs from its frozen hash")
        bound[name] = got
        if name.endswith(".json"):
            artifacts[name] = json.loads(raw.decode("utf-8"))
    binding = {
        "resolved_path": str(dev_dir),
        "manifest": {
            "bytes": len(manifest_raw),
            "sha256": sha256_bytes(manifest_raw),
            "digest": manifest["digest"],
        },
        "binding": dict(manifest["binding"]),
        "artifacts": bound,
        "raw_bundle_reevaluated": False,
    }
    return artifacts, binding


# --------------------------------------------------------------------------
# M-only row-level tables, computed with the frozen evaluator's own kernels.
# --------------------------------------------------------------------------


def _bump(store: dict[str, Counter[str]], key: str, cell: str) -> None:
    store.setdefault(key, Counter())[cell] += 1


def _sorted_counts(store: Mapping[str, Counter[str]]) -> dict[str, dict[str, int]]:
    return {key: {k: store[key][k] for k in sorted(store[key])} for key in sorted(store)}


def kernel_tables(
    evaluator: Any, payload: bytes, spec: Mapping[str, Any], binding: Mapping[str, Any]
) -> dict[str, Any]:
    """Row transition matrices and gate-failure structure for the M replicate.

    Every assignment comes from the frozen ``validate_row``/``evaluate_policy``
    and every gate reason from the frozen ``gate_gn``/``gate_gs``/``gate_gd``.
    """
    gate_fn = {"GN": evaluator.gate_gn, "GS": evaluator.gate_gs, "GD": evaluator.gate_gd}
    finals: dict[str, Counter[str]] = {}
    transitions: dict[str, Counter[str]] = {}
    gate_marginal: dict[str, Counter[str]] = {}
    gate_sole: dict[str, Counter[str]] = {}
    gate_sets: dict[str, Counter[str]] = {}
    strict_loss: dict[str, Counter[str]] = {}
    d_only: dict[str, Counter[str]] = {}
    b_to_c: dict[str, Counter[str]] = {}
    science: dict[str, Counter[str]] = {}
    invalid = 0
    rows = 0
    multi_final = 0
    for raw in payload.splitlines():
        record = json.loads(raw)
        crawl = binding["files"][record["_xlm_acquisition"]["source_file"]]["crawl"]
        fields, reasons, _ = evaluator.validate_row(record, spec)
        rows += 1
        final: dict[str, str] = {}
        if reasons:
            invalid += 1
            final = dict.fromkeys(COMBOS, "rejected")
        else:
            for policy in POLICIES:
                for tier in TIERS:
                    result = evaluator.evaluate_policy(fields, policy, tier, spec)
                    final[f"{policy}-{tier}"] = result["final"]
                    # Independent precedence check: the single final must be the
                    # first matching component in science, practical, prose order.
                    policy_def = spec["policies"][policy]
                    matched = [
                        name
                        for name, part in zip(SELECTED, _PRECEDENCE, strict=True)
                        if evaluator._component_match(policy_def[part], result["matches"], spec)
                    ]
                    expected = matched[0] if matched else "unassigned"
                    if result["gate_pass"] and result["final"] != expected:
                        multi_final += 1
            for gate in GATES:
                failed = gate_fn[gate](fields, spec)
                _bump(gate_sets, gate, "+".join(failed) if failed else "pass")
                for reason in failed:
                    _bump(gate_marginal, gate, reason)
                if len(failed) == 1:
                    _bump(gate_sole, gate, failed[0])
        for combo, value in final.items():
            _bump(finals, f"{combo}|all", value)
            _bump(finals, f"{combo}|{crawl}", value)
        for first, second in EFFECT_PAIRS:
            for tier in TIERS:
                before, after = final[f"{first}-{tier}"], final[f"{second}-{tier}"]
                _bump(transitions, f"{first}->{second}/{tier}", f"{before}->{after}")
        for policy in POLICIES:
            before, after = final[f"{policy}-normal"], final[f"{policy}-strict"]
            _bump(transitions, f"{policy}/normal->strict", f"{before}->{after}")
        if reasons:
            continue
        b_normal, b_strict = final["B-normal"], final["B-strict"]
        if b_normal != "rejected" and b_strict == "rejected":
            lost = sorted(evaluator.gate_gs(fields, spec))
            _bump(strict_loss, b_normal, "+".join(lost))
        d_normal = final["D-normal"]
        if d_normal in SELECTED and b_normal != d_normal:
            if b_normal != "rejected":
                raise AnalysisError("D-only row was not B-rejected: integrity STOP")
            band = evaluator._english_band(fields["e"], spec["english_bands"])
            _bump(d_only, d_normal, "rows")
            for label, value in (
                ("artifact", fields["a"]),
                ("genre", fields["d"]),
                ("missing", fields["m"]),
                ("correctness", fields["t"]),
                ("english_band", str(band)),
            ):
                _bump(d_only, d_normal, f"{label}={value}")
        for tier in TIERS:
            b_final, c_final = final[f"B-{tier}"], final[f"C-{tier}"]
            if b_final in ("essential_practical", "essential_prose") and c_final != b_final:
                key = f"{tier}|{b_final}->{c_final}"
                _bump(b_to_c, key, "rows")
                _bump(b_to_c, key, f"genre={fields['d']}")
                _bump(b_to_c, key, f"fdc_digit1={fields['digit1']}")
                if b_final == "essential_practical":
                    _, branch = evaluator.predicate_p(fields, spec)
                    _bump(b_to_c, key, f"branch={branch}")
        for policy in ("B", "D"):
            for tier in TIERS:
                if final[f"{policy}-{tier}"] != "essential_science":
                    continue
                s5 = evaluator.predicate_s5(fields, spec)
                s61 = evaluator.predicate_s61(fields, spec)
                kind = "S5&S61" if s5 and s61 else "S5" if s5 else "S61"
                key = f"{policy}-{tier}"
                _bump(science, key, f"predicate={kind}")
                _bump(science, key, f"{kind}|genre={fields['d']}")
                _bump(science, key, f"{kind}|prefix3={fields['prefix3']}")
    return {
        "rows": rows,
        "invalid_rows": invalid,
        "multi_final_violations": multi_final,
        "finals": _sorted_counts(finals),
        "transition_matrices": _sorted_counts(transitions),
        "gate_marginal_failures": _sorted_counts(gate_marginal),
        "gate_sole_failures": _sorted_counts(gate_sole),
        "gate_failure_sets": _sorted_counts(gate_sets),
        "gate_denominator": "valid_rows",
        "b_normal_to_strict_loss_by_gs_reason": _sorted_counts(strict_loss),
        "d_only_normal": _sorted_counts(d_only),
        "b_to_c_losses": _sorted_counts(b_to_c),
        "science_predicates": _sorted_counts(science),
    }


def build_results(
    payloads: Mapping[str, Any], kernel: Mapping[str, Any], crawls: Sequence[str]
) -> dict[str, Any]:
    """Compact eight-condition results; refuses any conservation or overlap gap."""
    summary = payloads["summary.json"]
    per_crawl = payloads["per_crawl.json"]
    if sorted(summary["combos"]) != sorted(COMBOS):
        raise AnalysisError("evaluator did not emit exactly the eight frozen conditions")
    if summary["input_records"] != ARM_ROWS or kernel["rows"] != ARM_ROWS:
        raise AnalysisError("M replicate is not exactly 4096 rows")
    if summary["multi_final_violations"] != 0 or kernel["multi_final_violations"] != 0:
        raise AnalysisError("a row survived with more than one final component")
    if list(summary["crawls"]) != list(crawls) or len(crawls) != 8:
        raise AnalysisError("evaluator crawls differ from the frozen eight windows")
    conditions: dict[str, Any] = {}
    for combo in COMBOS:
        final = {c: int(summary["combos"][combo]["final"][c]) for c in COMPONENTS}
        if sum(final.values()) != ARM_ROWS or not summary["combos"][combo]["conservation_ok"]:
            raise AnalysisError(f"{combo}: counts do not sum to {ARM_ROWS}")
        if kernel["finals"].get(f"{combo}|all", {}) != {k: v for k, v in final.items() if v}:
            raise AnalysisError(f"{combo}: kernel pass disagrees with the evaluator summary")
        by_crawl: dict[str, dict[str, int]] = {}
        for crawl in crawls:
            cell = {c: int(per_crawl[crawl]["combos"][combo]["final"][c]) for c in COMPONENTS}
            if sum(cell.values()) != ROWS_PER_CRAWL or per_crawl[crawl]["input"] != ROWS_PER_CRAWL:
                raise AnalysisError(f"{combo}/{crawl}: counts do not sum to {ROWS_PER_CRAWL}")
            if kernel["finals"].get(f"{combo}|{crawl}", {}) != {k: v for k, v in cell.items() if v}:
                raise AnalysisError(f"{combo}/{crawl}: kernel pass disagrees with the evaluator")
            by_crawl[crawl] = cell
        for component in COMPONENTS:
            if sum(by_crawl[crawl][component] for crawl in crawls) != final[component]:
                raise AnalysisError(f"{combo}/{component}: per-crawl totals do not reconcile")
        conditions[combo] = {"final": final, "by_crawl": by_crawl}
    return {
        "kind": "essential_web_m_sweep_results",
        "replicate": "M (Phase-D confirmation replicate)",
        "input_records": ARM_ROWS,
        "crawls": list(crawls),
        "precedence": list(_PRECEDENCE),
        "conditions": conditions,
        "identity_invariants": dict(summary["identity_invariants"]),
        "multi_final_violations": 0,
        "invalid_rows": kernel["invalid_rows"],
        "row_level_tables": {k: v for k, v in kernel.items() if k != "finals"},
        "selector_decision": "NOT MADE",
        "policy_ranking": "NOT PERFORMED",
    }


# --------------------------------------------------------------------------
# Development-versus-M comparison (descriptive; both artifact sets share the
# frozen evaluator's shapes, field paths and denominators).
# --------------------------------------------------------------------------


def _share(count: int, denominator: int) -> float | None:
    return round(count / denominator, 6) if denominator else None


def _pp(first: float | None, second: float | None) -> float | None:
    if first is None or second is None:
        return None
    return round((second - first) * 100, 3)


def _final(arts: Mapping[str, Any], combo: str) -> dict[str, int]:
    return {c: int(arts["summary.json"]["combos"][combo]["final"][c]) for c in COMPONENTS}


def _crawl_final(arts: Mapping[str, Any], crawl: str, combo: str) -> dict[str, int]:
    cell = arts["per_crawl.json"][crawl]["combos"][combo]["final"]
    return {c: int(cell[c]) for c in COMPONENTS}


def _range(counts: Sequence[int]) -> dict[str, Any]:
    return {
        "min": min(counts),
        "max": max(counts),
        "spread_pp": round((max(counts) - min(counts)) / ROWS_PER_CRAWL * 100, 3),
        "zero_cells": sum(1 for c in counts if c == 0),
        "cells_below_20": sum(1 for c in counts if c < SPARSE_CELL),
    }


def _totals(dev: Mapping[str, Any], m: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for combo in COMBOS:
        d, n = _final(dev, combo), _final(m, combo)
        out[combo] = {
            c: {
                "development": d[c],
                "m": n[c],
                "development_share": _share(d[c], ARM_ROWS),
                "m_share": _share(n[c], ARM_ROWS),
                "difference": n[c] - d[c],
                "difference_pp": _pp(_share(d[c], ARM_ROWS), _share(n[c], ARM_ROWS)),
            }
            for c in COMPONENTS
        }
    return out


def _per_crawl(dev: Mapping[str, Any], m: Mapping[str, Any], crawls: Sequence[str]) -> Any:
    out: dict[str, Any] = {}
    for combo in COMBOS:
        per_component: dict[str, Any] = {}
        for component in COMPONENTS:
            cells: dict[str, Any] = {}
            diffs: list[int] = []
            for crawl in crawls:
                d = _crawl_final(dev, crawl, combo)[component]
                n = _crawl_final(m, crawl, combo)[component]
                diffs.append(n - d)
                cells[crawl] = {
                    "development": d,
                    "m": n,
                    "difference": n - d,
                    "difference_pp": round((n - d) / ROWS_PER_CRAWL * 100, 3),
                }
            per_component[component] = {
                "cells": cells,
                "development": _range([cells[c]["development"] for c in crawls]),
                "m": _range([cells[c]["m"] for c in crawls]),
                "matched_crawl_difference": {
                    "min": min(diffs),
                    "max": max(diffs),
                    "max_abs_pp": round(max(abs(v) for v in diffs) / ROWS_PER_CRAWL * 100, 3),
                    "equal_crawl_mean_pp": round(sum(diffs) / len(diffs) / ROWS_PER_CRAWL * 100, 3),
                    "crawls_up": sum(1 for v in diffs if v > 0),
                    "crawls_down": sum(1 for v in diffs if v < 0),
                    "crawls_equal": sum(1 for v in diffs if v == 0),
                },
            }
        out[combo] = per_component
    return out


def _attrition_side(arts: Mapping[str, Any], policy: str) -> dict[str, Any]:
    normal, strict = _final(arts, f"{policy}-normal"), _final(arts, f"{policy}-strict")
    out: dict[str, Any] = {}
    for component in (*SELECTED, "selected_total"):
        if component == "selected_total":
            before, after = (sum(v[c] for c in SELECTED) for v in (normal, strict))
        else:
            before, after = normal[component], strict[component]
        out[component] = {
            "normal": before,
            "strict": after,
            "lost": before - after,
            "retained_share": _share(after, before),
        }
    out["evaluator_transition"] = arts["overlaps.json"]["transitions"][f"{policy}/normal->strict"]
    return out


def _strict_attrition(dev: Mapping[str, Any], m: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for policy in POLICIES:
        d, n = _attrition_side(dev, policy), _attrition_side(m, policy)
        out[policy] = {
            "development": d,
            "m": n,
            "retained_share_difference_pp": {
                c: _pp(d[c]["retained_share"], n[c]["retained_share"])
                for c in (*SELECTED, "selected_total")
            },
        }
    return out


def _effects(dev: Mapping[str, Any], m: Mapping[str, Any], kernel: Mapping[str, Any]) -> Any:
    out: dict[str, Any] = {}
    for first, second in EFFECT_PAIRS:
        per_tier: dict[str, Any] = {}
        for tier in TIERS:
            key = f"{first}->{second}/{tier}"
            sides: dict[str, Any] = {}
            for label, arts in (("development", dev), ("m", m)):
                a, b = _final(arts, f"{first}-{tier}"), _final(arts, f"{second}-{tier}")
                sides[label] = {
                    "count_change": {c: b[c] - a[c] for c in COMPONENTS},
                    "evaluator_transition": arts["overlaps.json"]["transitions"][key],
                }
            sides["m_row_transition_matrix"] = kernel["transition_matrices"][key]
            sides["development_row_transition_matrix"] = (
                "NOT AVAILABLE: raw development bundle absent; frozen artifacts carry "
                "additions/removals/reassignments only"
            )
            per_tier[tier] = sides
        out[f"{first}_vs_{second}"] = per_tier
    return out


def _flags(arts: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in arts["diagnostics.json"]["temporal_cells"].items():
        zero = [c["crawl"] for c in value["cells"] if c.get("flag") == "zero_final_rows"]
        sparse = [c["crawl"] for c in value["cells"] if c.get("flag") == "sparse_cell"]
        out[key] = {
            "zero_final_rows": zero,
            "sparse_cell": sparse,
            "retention_fold_note": value.get("retention_fold_note"),
            "s61_note": value.get("s61_note"),
        }
    return out


def _waterfall(arts: Mapping[str, Any], combo: str) -> dict[str, Any]:
    counts = arts["attrition.json"][combo]["waterfall"]["all"]
    stages = arts["attrition.json"]["stages"]
    return {
        "counts": {stage: int(counts[stage]) for stage in stages},
        "conditional_retention": {
            stages[i]: _share(int(counts[stages[i]]), int(counts[stages[i - 1]]))
            for i in range(1, len(stages))
        },
    }


def _gates(dev: Mapping[str, Any], m: Mapping[str, Any], kernel: Mapping[str, Any]) -> Any:
    out: dict[str, Any] = {"waterfall_note": "sequential fixed order; stops at first failure"}
    waterfalls: dict[str, Any] = {}
    for combo in COMBOS:
        d, n = _waterfall(dev, combo), _waterfall(m, combo)
        waterfalls[combo] = {
            "development": d,
            "m": n,
            "stage_loss": {
                stage: {
                    "development": d["counts"][prev] - d["counts"][stage],
                    "m": n["counts"][prev] - n["counts"][stage],
                }
                for prev, stage in zip(list(d["counts"])[:-2], list(d["counts"])[1:-1], strict=True)
            },
            "conditional_retention_difference_pp": {
                stage: _pp(d["conditional_retention"][stage], n["conditional_retention"][stage])
                for stage in d["conditional_retention"]
            },
            "crawl_spread_flags": {
                "development": dev["diagnostics.json"]["conditional_gate_spreads_pp"][combo],
                "m": m["diagnostics.json"]["conditional_gate_spreads_pp"][combo],
            },
        }
    out["waterfalls"] = waterfalls
    out["validity_reasons"] = {
        "development": dev["attrition.json"]["A-normal"]["validity_reasons"]["all"],
        "m": m["attrition.json"]["A-normal"]["validity_reasons"]["all"],
    }
    out["unknown_values"] = {
        "development": dev["diagnostics.json"]["unknown_values"],
        "m": m["diagnostics.json"]["unknown_values"],
    }
    out["fdc_anomaly_count"] = {
        "development": dev["diagnostics.json"]["fdc_anomaly"]["count"],
        "m": m["diagnostics.json"]["fdc_anomaly"]["count"],
    }
    out["sensitivity_joint_pass_counts"] = {
        f"{policy}/all": {
            "development": dev["diagnostics.json"]["sensitivity_joint_pass_counts"][
                f"{policy}/all"
            ],
            "m": m["diagnostics.json"]["sensitivity_joint_pass_counts"][f"{policy}/all"],
        }
        for policy in POLICIES
    }
    out["m_only_failure_structure"] = {
        "denominator": kernel["gate_denominator"],
        "marginal": kernel["gate_marginal_failures"],
        "sole": kernel["gate_sole_failures"],
        "failure_sets": kernel["gate_failure_sets"],
        "b_normal_to_strict_loss_by_gs_reason": kernel["b_normal_to_strict_loss_by_gs_reason"],
        "development": "NOT AVAILABLE: raw development bundle absent",
    }
    return out


def _label_table(
    dev_counts: Mapping[str, int], m_counts: Mapping[str, int], prefix: str
) -> dict[str, Any]:
    d = {k[len(prefix) :]: int(v) for k, v in dev_counts.items() if k.startswith(prefix)}
    n = {k[len(prefix) :]: int(v) for k, v in m_counts.items() if k.startswith(prefix)}
    d_total, n_total = sum(d.values()), sum(n.values())
    rows: dict[str, Any] = {}
    for label in sorted(set(d) | set(n)):
        d_share, n_share = _share(d.get(label, 0), d_total), _share(n.get(label, 0), n_total)
        rows[label] = {
            "development": d.get(label, 0),
            "m": n.get(label, 0),
            "development_share": d_share,
            "m_share": n_share,
            "difference_pp": _pp(d_share, n_share),
        }
    return {"denominators": {"development": d_total, "m": n_total}, "labels": rows}


_CLASSIFIERS = (
    ("doctype", "labels", "D="),
    ("knowledge", "labels", "K="),
    ("artifacts", "labels", "A="),
    ("missing_content", "labels", "M="),
    ("correctness", "labels", "T="),
    ("fdc_digit1", "fdc", "digit1="),
)


def _input_counts(arts: Mapping[str, Any], group: str) -> Counter[str]:
    total: Counter[str] = Counter()
    for component in COMPONENTS:
        total.update(arts["crosstabs.json"]["compositions"][f"A-normal/{component}"][group])
    return total


def _composition(dev: Mapping[str, Any], m: Mapping[str, Any]) -> dict[str, Any]:
    dc, mc = dev["crosstabs.json"], m["crosstabs.json"]
    out: dict[str, Any] = {
        "denominator_note": (
            "component tables use selected-component denominators per replicate; "
            "input tables use rows with a validated value; zero denominators are undefined"
        ),
        "input": {
            name: _label_table(_input_counts(dev, group), _input_counts(m, group), prefix)
            for name, group, prefix in _CLASSIFIERS
        },
    }
    components: dict[str, Any] = {}
    for combo in COMBOS:
        for component in SELECTED:
            key = f"{combo}/{component}"
            components[key] = {
                name: _label_table(
                    dc["compositions"][key][group], mc["compositions"][key][group], prefix
                )
                for name, group, prefix in _CLASSIFIERS
            }
    out["components"] = components
    out["science_balance"] = {
        combo: {"development": dc["science_balance"][combo], "m": mc["science_balance"][combo]}
        for combo in COMBOS
    }
    practical: dict[str, Any] = {}
    prose: dict[str, Any] = {}
    for combo in COMBOS:
        sides: dict[str, Any] = {}
        for label, arts in (("development", dc), ("m", mc)):
            cell = arts["practical_branches"][combo]
            total = int(cell["explicit_branch"]) + int(cell["conditional_branch"])
            sides[label] = {
                **cell,
                "explicit_share": _share(int(cell["explicit_branch"]), total),
                "domain_share": {k: _share(int(v), total) for k, v in cell["domain"].items()},
            }
        practical[combo] = sides
        prose[combo] = _label_table(
            {f"G={k}": v for k, v in dc["prose_genres"][combo].items()},
            {f"G={k}": v for k, v in mc["prose_genres"][combo].items()},
            "G=",
        )
    out["practical_branches"] = practical
    out["prose_genres"] = prose
    out["component_genre_share_spread_flags"] = {
        key: {
            "development": sorted(
                dev["diagnostics.json"]["component_genre_share_spreads_pp"][key]["flags"]
            ),
            "m": sorted(m["diagnostics.json"]["component_genre_share_spreads_pp"][key]["flags"]),
            "eligible_crawls": {
                "development": len(
                    dev["diagnostics.json"]["component_genre_share_spreads_pp"][key][
                        "eligible_crawls"
                    ]
                ),
                "m": len(
                    m["diagnostics.json"]["component_genre_share_spreads_pp"][key][
                        "eligible_crawls"
                    ]
                ),
            },
        }
        for key in sorted(dev["diagnostics.json"]["component_genre_share_spreads_pp"])
    }
    out["english_input"] = {
        "development": dc["english_distributions"]["input"],
        "m": mc["english_distributions"]["input"],
    }
    out["word_count_input"] = {
        "development": dc["word_count_distributions"]["input"],
        "m": mc["word_count_distributions"]["input"],
        "unit_note": dc["word_count_distributions"]["unit_note"],
    }
    out["level1_consistency"] = {
        "development": dc["level1_consistency"]["all"],
        "m": mc["level1_consistency"]["all"],
    }
    out["predicate_overlaps"] = {
        "development": dev["overlaps.json"]["predicate_overlaps"]["all"],
        "m": m["overlaps.json"]["predicate_overlaps"]["all"],
    }
    return out


def build_comparison(
    dev: Mapping[str, Any],
    m: Mapping[str, Any],
    kernel: Mapping[str, Any],
    bindings: Mapping[str, Any],
) -> dict[str, Any]:
    """Descriptive development-versus-M comparison; no ranking, no decision."""
    crawls = list(m["summary.json"]["crawls"])
    if list(dev["summary.json"]["crawls"]) != crawls:
        raise AnalysisError("development and M crawls differ: no matched-crawl comparison")
    for label, arts in (("development", dev), ("m", m)):
        if arts["summary.json"]["input_records"] != ARM_ROWS:
            raise AnalysisError(f"{label} replicate is not {ARM_ROWS} rows")
        if arts["crosstabs.json"]["report_schema_version"] != 2:
            raise AnalysisError(f"{label} artifacts are not corrected report schema 2")
    return {
        "kind": "essential_web_m_development_comparison",
        "contract": "scientific protocol section 5",
        "replicates": {
            "development": "frozen corrected sweep-v2 artifacts (hash-verified, not re-evaluated)",
            "m": "Phase-D confirmation replicate via derived_analysis_input adapter",
            "rows_each": ARM_ROWS,
            "rows_per_crawl": ROWS_PER_CRAWL,
            "crawls": crawls,
            "disjoint_files": True,
        },
        "input_bindings": dict(bindings),
        "statistics_note": STATISTICS_NOTE,
        "selector_decision": "NOT MADE",
        "policy_ranking": "NOT PERFORMED",
        "totals": _totals(dev, m),
        "per_crawl": _per_crawl(dev, m, crawls),
        "strict_attrition": _strict_attrition(dev, m),
        "effects": _effects(dev, m, kernel),
        "temporal_flags": {"development": _flags(dev), "m": _flags(m)},
        "gates": _gates(dev, m, kernel),
        "composition": _composition(dev, m),
        "m_only": {
            "d_only_normal": kernel["d_only_normal"],
            "b_to_c_losses": kernel["b_to_c_losses"],
            "science_predicates": kernel["science_predicates"],
        },
        "identity_invariants": {
            "development": dev["summary.json"]["identity_invariants"],
            "m": m["summary.json"]["identity_invariants"],
            "note": "mechanical implementation checks, not evidence for any policy",
        },
    }


# --------------------------------------------------------------------------
# Markdown rendering of the comparison (generated, bound by the seal).
# --------------------------------------------------------------------------

_SHORT = {
    "essential_science": "science",
    "essential_practical": "practical",
    "essential_prose": "prose",
    "unassigned": "unassigned",
    "rejected": "rejected",
    "selected_total": "selected total",
}


def _fmt(value: Any, suffix: str = "") -> str:
    if value is None:
        return "undefined"
    if isinstance(value, float):
        return f"{value:+.3f}{suffix}" if suffix == " pp" else f"{value:.4f}"
    return f"{value}{suffix}"


def _pct(value: float | None) -> str:
    return "undefined" if value is None else f"{value * 100:.2f}%"


def render_comparison(comparison: Mapping[str, Any]) -> str:
    """Deterministic Markdown tables for the sealed comparison."""
    crawls: list[str] = comparison["replicates"]["crawls"]
    short = [c.replace("crawl=CC-MAIN-", "") for c in crawls]
    lines = [
        "# Essential-Web M selector evidence: development versus M (generated)",
        "",
        "Evidence reporting only. No policy is ranked and no selector decision is made.",
        "",
        comparison["statistics_note"],
        "",
        "`dev` = frozen development sweep-v2; `M` = Phase-D confirmation replicate.",
        "Counts use 4096 rows per replicate and 512 rows per crawl.",
        "",
        "## 1. Final assignments (count, share of 4096)",
        "",
        "| condition | component | dev | M | dev share | M share | M-dev | M-dev pp |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for combo in COMBOS:
        for component in COMPONENTS:
            cell = comparison["totals"][combo][component]
            lines.append(
                f"| {combo} | {_SHORT[component]} | {cell['development']} | {cell['m']} | "
                f"{_pct(cell['development_share'])} | {_pct(cell['m_share'])} | "
                f"{cell['difference']:+d} | {_fmt(cell['difference_pp'], ' pp')} |"
            )
    lines += ["", "## 2. Per-crawl counts (dev / M, of 512)", ""]
    for combo in COMBOS:
        lines += [
            f"### {combo}",
            "",
            "| component | " + " | ".join(short) + " | dev min-max | M min-max | max abs diff pp |",
            "|---|" + "---:|" * (len(short) + 3),
        ]
        for component in COMPONENTS:
            entry = comparison["per_crawl"][combo][component]
            cells = " | ".join(
                f"{entry['cells'][c]['development']} / {entry['cells'][c]['m']}" for c in crawls
            )
            lines.append(
                f"| {_SHORT[component]} | {cells} | "
                f"{entry['development']['min']}-{entry['development']['max']} | "
                f"{entry['m']['min']}-{entry['m']['max']} | "
                f"{entry['matched_crawl_difference']['max_abs_pp']:.3f} |"
            )
        lines.append("")
    lines += [
        "## 3. Strict-versus-normal attrition",
        "",
        "| policy | component | dev normal | dev strict | dev retained | "
        "M normal | M strict | M retained | retained diff pp |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for policy in POLICIES:
        entry = comparison["strict_attrition"][policy]
        for component in (*SELECTED, "selected_total"):
            d, n = entry["development"][component], entry["m"][component]
            lines.append(
                f"| {policy} | {_SHORT[component]} | {d['normal']} | {d['strict']} | "
                f"{_pct(d['retained_share'])} | {n['normal']} | {n['strict']} | "
                f"{_pct(n['retained_share'])} | "
                f"{_fmt(entry['retained_share_difference_pp'][component], ' pp')} |"
            )
    lines += ["", "## 4. Policy-pair count changes (second minus first)", ""]
    lines += [
        "| pair | tier | replicate | science | practical | prose | unassigned | rejected | "
        "additions | removals | reassignments |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for pair, per_tier in comparison["effects"].items():
        for tier in TIERS:
            for label in ("development", "m"):
                side = per_tier[tier][label]
                change = side["count_change"]
                moves = side["evaluator_transition"]
                lines.append(
                    f"| {pair.replace('_', ' ')} | {tier} | {'dev' if label != 'm' else 'M'} | "
                    + " | ".join(f"{change[c]:+d}" for c in COMPONENTS)
                    + f" | {moves['additions']} | {moves['removals']} | "
                    f"{moves['reassignments']} |"
                )
    lines += ["", "### M row transition matrices (rows moved between finals)", ""]
    for pair, per_tier in comparison["effects"].items():
        for tier in TIERS:
            matrix = per_tier[tier]["m_row_transition_matrix"]
            moved = {k: v for k, v in matrix.items() if k.split("->")[0] != k.split("->")[1]}
            text = ", ".join(f"{k}: {v}" for k, v in moved.items()) or "no row changes"
            lines.append(f"- {pair.replace('_', ' ')} / {tier}: {text}")
    lines += [
        "",
        "## 5. Gate waterfall (sequential; rows surviving each stage)",
        "",
        "| condition | replicate | input | valid | english | artifacts | missing | "
        "correctness | doctype | joint |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for combo in COMBOS:
        for label in ("development", "m"):
            counts = comparison["gates"]["waterfalls"][combo][label]["counts"]
            lines.append(
                f"| {combo} | {'dev' if label != 'm' else 'M'} | "
                + " | ".join(str(v) for v in counts.values())
                + " |"
            )
    structure = comparison["gates"]["m_only_failure_structure"]
    lines += ["", "### M gate failures over valid rows (marginal / sole)", ""]
    lines += ["| gate | reason | marginal | sole |", "|---|---|---:|---:|"]
    for gate in GATES:
        for reason, count in structure["marginal"].get(gate, {}).items():
            sole = structure["sole"].get(gate, {}).get(reason, 0)
            lines.append(f"| {gate} | {reason} | {count} | {sole} |")
    lines += ["", "B normal-to-strict losses by GS reason (M):", ""]
    for component, reasons in structure["b_normal_to_strict_loss_by_gs_reason"].items():
        text = ", ".join(f"{k}: {v}" for k, v in reasons.items())
        lines.append(f"- {_SHORT[component]}: {text}")
    lines += [
        "",
        "## 6. Input composition drift (share of rows with a validated value)",
        "",
        "| classifier | label | dev | M | dev share | M share | M-dev pp |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for name, table in comparison["composition"]["input"].items():
        for label, cell in table["labels"].items():
            lines.append(
                f"| {name} | {label} | {cell['development']} | {cell['m']} | "
                f"{_pct(cell['development_share'])} | {_pct(cell['m_share'])} | "
                f"{_fmt(cell['difference_pp'], ' pp')} |"
            )
    lines += [
        "",
        "## 7. Component composition (selected-component denominators)",
        "",
        "### Science: S5 / S61 final rows",
        "",
        "| condition | dev S5 | dev S61 | dev S61 share | M S5 | M S61 | M S61 share |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for combo in COMBOS:
        cell = comparison["composition"]["science_balance"][combo]
        d, n = cell["development"], cell["m"]
        lines.append(
            f"| {combo} | {d['s5_final']} | {d['s61_final']} | "
            f"{_pct(d['selected_61x_fraction'])} | {n['s5_final']} | {n['s61_final']} | "
            f"{_pct(n['selected_61x_fraction'])} |"
        )
    lines += [
        "",
        "### Practical: explicit / conditional branch and FDC domain",
        "",
        "| condition | replicate | explicit | conditional | explicit share | 0xx | 6xx | other |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for combo in COMBOS:
        for label in ("development", "m"):
            cell = comparison["composition"]["practical_branches"][combo][label]
            lines.append(
                f"| {combo} | {'dev' if label != 'm' else 'M'} | {cell['explicit_branch']} | "
                f"{cell['conditional_branch']} | {_pct(cell['explicit_share'])} | "
                f"{cell['domain']['0xx']} | {cell['domain']['6xx']} | {cell['domain']['other']} |"
            )
    lines += [
        "",
        "### Prose genres",
        "",
        "| condition | genre | dev | M | dev share | M share | M-dev pp |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for combo in COMBOS:
        for label, cell in comparison["composition"]["prose_genres"][combo]["labels"].items():
            lines.append(
                f"| {combo} | {label} | {cell['development']} | {cell['m']} | "
                f"{_pct(cell['development_share'])} | {_pct(cell['m_share'])} | "
                f"{_fmt(cell['difference_pp'], ' pp')} |"
            )
    lines += ["", "## 8. Inherited diagnostic flags (not tests)", ""]
    for label in ("development", "m"):
        flags = comparison["temporal_flags"][label]
        zero = sorted(k for k, v in flags.items() if v["zero_final_rows"])
        fold = sorted(k for k, v in flags.items() if v["retention_fold_note"])
        s61 = sorted(k for k, v in flags.items() if v["s61_note"])
        name = "dev" if label != "m" else "M"
        lines += [
            f"- {name} zero-row crawl cells: {', '.join(zero) or 'none'}",
            f"- {name} twofold retention notes: {', '.join(fold) or 'none'}",
            f"- {name} S61 > 0.5 notes: {', '.join(s61) or 'none'}",
        ]
    lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Seal.
# --------------------------------------------------------------------------


def artifact_manifest(files: Mapping[str, bytes]) -> dict[str, Any]:
    """Bytes and SHA-256 of every sealed result file, self-digested."""
    for name in files:
        refuse_t_material(Path(name))
    body: dict[str, Any] = {
        "kind": "essential_web_m_analysis_artifact_manifest",
        "artifacts": {
            name: {"bytes": len(raw), "sha256": sha256_bytes(raw)}
            for name, raw in sorted(files.items())
        },
    }
    body["digest"] = canonical.self_digest(body)
    return body


def build_seal(
    *,
    preparation: Mapping[str, Any],
    preparation_binding: Mapping[str, Any],
    adapted_manifest: Mapping[str, Any],
    derived_input: Mapping[str, Any],
    evaluator: Mapping[str, Any],
    development: Mapping[str, Any],
    analysis_code: Mapping[str, Any],
    manifest: Mapping[str, Any],
    commands: Sequence[str],
    environment: Mapping[str, Any],
) -> dict[str, Any]:
    """Canonical seal over every input, code and result binding."""
    for name in manifest["artifacts"]:
        refuse_t_material(Path(name))
    body: dict[str, Any] = {
        "kind": SEAL_KIND,
        "status": "SEALED",
        "arm": "M",
        "scientific_namespace": preparation["scientific_namespace"],
        "selection_digest": preparation["selection_digest"],
        "policy_digest": preparation["policy_digest"],
        "phase_d_plan_digest": preparation["phase_d_plan_digest"],
        "source_revision": adapted_manifest["source"]["revision"],
        "preparation": dict(preparation_binding),
        "source": dict(adapted_manifest["source"]),
        "adapter": dict(adapted_manifest["adapter"]),
        "adapted_input": {
            **dict(derived_input),
            "kind": adapted_manifest["kind"],
            "records": adapted_manifest["records"],
            "row_identity_digest": adapted_manifest["row_identity_digest"],
            "metadata_digest": adapted_manifest["metadata_digest"],
            "manifest_digest": adapted_manifest["digest"],
        },
        "evaluator": dict(evaluator),
        "analysis_code": dict(analysis_code),
        "development": dict(development),
        "conditions": list(COMBOS),
        "results": dict(manifest["artifacts"]),
        "artifact_manifest_digest": manifest["digest"],
        "commands": list(commands),
        "environment": dict(environment),
        "selector_decision": "NOT MADE",
        "policy_ranking": "NOT PERFORMED",
        "other_arm_material_bound": False,
        "sealed_before_other_arm_unblinding": True,
        "post_seal_rule": "M analysis must not change in response to later semantic review",
    }
    body["seal_digest"] = canonical.digest(body)
    return body


def seal_digest_of(seal: Mapping[str, Any]) -> str:
    return canonical.digest({k: v for k, v in seal.items() if k != "seal_digest"})


def verify_seal(output_dir: Path) -> dict[str, Any]:
    """Recheck the seal self-digest and every bound result file on disk."""
    seal = json.loads((output_dir / SEAL_NAME).read_bytes().decode("utf-8"))
    if seal.get("kind") != SEAL_KIND or seal_digest_of(seal) != seal.get("seal_digest"):
        raise AnalysisError("seal digest mismatch")
    manifest = json.loads((output_dir / ARTIFACT_MANIFEST_NAME).read_bytes().decode("utf-8"))
    if canonical.self_digest(manifest) != manifest.get("digest"):
        raise AnalysisError("artifact manifest self-digest mismatch")
    if manifest["digest"] != seal["artifact_manifest_digest"]:
        raise AnalysisError("artifact manifest is not the sealed manifest")
    if manifest["artifacts"] != seal["results"]:
        raise AnalysisError("artifact manifest and seal disagree on results")
    for name, binding in seal["results"].items():
        raw = (output_dir / name).read_bytes()
        if {"bytes": len(raw), "sha256": sha256_bytes(raw)} != binding:
            raise AnalysisError(f"sealed result {name} changed after sealing")
    return dict(seal)
