"""Deterministic, content-free Phase-B dry-run artifacts from committed units.

Every artifact is a pure function of the units (manifest order), the dry-run binding
and the frozen cleaning policy: byte-identical for every worker count and
re-derivable by ``clean-report``. Counts are exact integers (documents and canonical
UTF-8 bytes). The DROP and REVIEW unions are the documents whose final outcome is
DROP / REVIEW; they are cross-checked against the exact rule-combination table.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from xlm.data.evidence_v2 import canonical
from xlm.data.quality.aggregate import AggregateError
from xlm.data.quality.cleaning import (
    GROUPS,
    N_OCR,
    N_REP,
    CleanStats,
    expand_strata,
    merge_candidates,
    quota_of,
    rule_names,
)
from xlm.data.quality.cleaning_policy import (
    DROP,
    DROP_MASK,
    KEEP,
    OCR_SIGNALS,
    OUTCOMES,
    REP_SIGNALS,
    REVIEW,
    REVIEW_MASK,
    RULE_IDS,
    RULES,
    CompiledPolicy,
    threshold_table,
)
from xlm.data.quality.policy import CLASS_ORDER

ARTIFACTS = (
    "cleaning-dry-run-summary.md",
    "cleaning-dry-run.json",
    "cleaning-by-component.json",
    "cleaning-by-rule.json",
    "cleaning-rule-intersections.json",
    "cleaning-review-manifest.jsonl",
)
REVIEW_MANIFEST = "cleaning-review-manifest.jsonl"
REQUIRES_REVIEW = "POLICY_REQUIRES_REVIEW"
WITHIN_GUARDRAILS = "POLICY_WITHIN_GUARDRAILS"
STALENESS = (
    "This is a DRY RUN: no document was dropped, transformed or written. A later "
    "production DROP changes the corpus and makes every existing C05 dedup, "
    "contamination, lineage, split and membership artifact stale (see the Phase-A runbook)."
)
ROW_KEYS = frozenset(
    {
        "selected_by",
        "strata",
        "component",
        "path",
        "row",
        "offset",
        "doc_id_sha256",
        "row_sha256",
        "doc_class",
        "outcome",
        "rules",
        "severe_repetition_signals",
        "severe_repetition_signal_count",
        "ocr_signals",
        "ocr_signal_count",
        "kept",
        "values",
        "rank",
    }
)


def _json(obj: Any) -> bytes:
    return (
        json.dumps(obj, sort_keys=True, indent=1, ensure_ascii=False, allow_nan=False) + "\n"
    ).encode("utf-8")


def _pct(part: int, whole: int) -> float:
    return round(100.0 * part / whole, 6) if whole else 0.0


def impact(docs: int, nbytes: int, stats: CleanStats) -> dict[str, Any]:
    return {
        "docs": int(docs),
        "bytes": int(nbytes),
        "docs_pct": _pct(int(docs), stats.docs),
        "bytes_pct": _pct(int(nbytes), stats.bytes),
    }


def _pair(values: Any, stats: CleanStats) -> dict[str, Any]:
    return impact(int(values[0]), int(values[1]), stats)


# -- summaries -----------------------------------------------------------------------------


def outcomes(stats: CleanStats) -> dict[str, Any]:
    return {name: _pair(stats.arrays["outcome"][n], stats) for n, name in enumerate(OUTCOMES)}


def rule_impacts(stats: CleanStats) -> dict[str, Any]:
    a = stats.arrays
    return {
        name: {
            "action": OUTCOMES[action],
            "marginal": _pair(a["rule"][n], stats),
            "exclusive": _pair(a["rule_exclusive"][n], stats),
        }
        for n, (name, action) in enumerate(RULES)
    }


def summarize(stats: CleanStats) -> dict[str, Any]:
    a = stats.arrays
    severe = a["severe"]  # (group, count, outcome, 2)
    histogram: dict[str, Any] = {}
    for count in range(N_REP + 1):
        cell = severe[:, count].sum(axis=(0, 1))
        histogram[str(count)] = {
            **_pair(cell, stats),
            "by_class_group": {
                group: _pair(severe[g, count].sum(axis=0), stats) for g, group in enumerate(GROUPS)
            },
            "by_outcome": {
                OUTCOMES[o]: _pair(severe[:, count, o].sum(axis=0), stats) for o in range(3)
            },
        }
    ocr = a["ocr"]
    return {
        "documents": stats.docs,
        "canonical_bytes": stats.bytes,
        "outcomes": outcomes(stats),
        "drop_union": _pair(a["outcome"][DROP], stats),
        "review_union": _pair(a["outcome"][REVIEW], stats),
        "keep": _pair(a["outcome"][KEEP], stats),
        "severe_repetition_signal_count": histogram,
        "review_sampling_eligible_single_signal_keep": _pair(severe[0, 1, KEEP], stats),
        "severe_repetition_signals": {
            name: {
                **_pair(a["signal"][:, k].sum(axis=0), stats),
                "by_class_group": {
                    group: _pair(a["signal"][g, k], stats) for g, group in enumerate(GROUPS)
                },
                "not_applicable": _pair(a["signal_na"][k], stats),
            }
            for k, name in enumerate(REP_SIGNALS)
        },
        "ocr_scope": {
            "documents_in_scope": _pair(ocr.sum(axis=(0, 1)), stats),
            "ocr_signal_count": {
                str(c): {
                    **_pair(ocr[c].sum(axis=0), stats),
                    "by_outcome": {OUTCOMES[o]: _pair(ocr[c, o], stats) for o in range(3)},
                }
                for c in range(N_OCR + 1)
            },
            "ocr_signals": {
                name: _pair(a["ocr_signal"][k], stats) for k, name in enumerate(OCR_SIGNALS)
            },
        },
        "class_distribution": {
            OUTCOMES[o]: {
                cls: _pair(a["class_outcome"][c, o], stats) for c, cls in enumerate(CLASS_ORDER)
            }
            for o in range(3)
        },
        "rules": rule_impacts(stats),
    }


def combinations(stats: CleanStats) -> list[dict[str, Any]]:
    """Exact rule-combination table (every observed fired-rule set)."""
    rows = []
    for mask, (docs, nbytes) in stats.combos.items():
        if mask & DROP_MASK:
            outcome = "DROP"
        elif mask & REVIEW_MASK:
            outcome = "REVIEW"
        else:
            outcome = "KEEP"
        rows.append({"rules": rule_names(mask), "outcome": outcome, **impact(docs, nbytes, stats)})
    rows.sort(key=lambda r: (-r["docs"], r["rules"]))
    return rows


def union_check(stats: CleanStats) -> dict[str, Any]:
    """Exact DROP / REVIEW unions recomputed from rule combinations (must agree)."""
    drop = [0, 0]
    review = [0, 0]
    for mask, (docs, nbytes) in stats.combos.items():
        target = drop if mask & DROP_MASK else review if mask & REVIEW_MASK else None
        if target is not None:
            target[0] += docs
            target[1] += nbytes
    if drop != stats.arrays["outcome"][DROP].tolist():
        raise AggregateError("DROP union differs from the rule-combination table")
    if review != stats.arrays["outcome"][REVIEW].tolist():
        raise AggregateError("REVIEW union differs from the rule-combination table")
    return {
        "drop_union": impact(drop[0], drop[1], stats),
        "review_union": impact(review[0], review[1], stats),
        "identity": (
            "DROP union = documents with >= 1 DROP rule; REVIEW union = documents with "
            ">= 1 REVIEW rule and no DROP rule; KEEP = the rest. Recomputed from the exact "
            "rule-combination table and equal to the outcome counts."
        ),
    }


def overlaps(stats: CleanStats) -> dict[str, Any]:
    pair = stats.arrays["rule_pair"]
    out: dict[str, Any] = {}
    for i, left in enumerate(RULE_IDS):
        for j in range(i + 1, len(RULE_IDS)):
            docs, nbytes = int(pair[i, j, 0]), int(pair[i, j, 1])
            if docs:
                out[f"{left} & {RULE_IDS[j]}"] = impact(docs, nbytes, stats)
    return out


# -- guardrails ------------------------------------------------------------------------------


def guardrails(
    global_stats: CleanStats, components: Mapping[str, CleanStats], policy: CompiledPolicy
) -> dict[str, Any]:
    """Exact integer comparison ``part * 100 > limit * whole`` for every guardrail."""
    limits = dict(policy.params.guardrails)
    breaches: list[dict[str, Any]] = []
    checks: list[tuple[str, CleanStats, str, int]] = [
        ("global", global_stats, "docs", limits["global_drop_docs_pct_gt"]),
        ("global", global_stats, "bytes", limits["global_drop_bytes_pct_gt"]),
    ]
    for name in sorted(components):
        stats = components[name]
        checks += [
            (f"component:{name}", stats, "docs", limits["component_drop_docs_pct_gt"]),
            (f"component:{name}", stats, "bytes", limits["component_drop_bytes_pct_gt"]),
        ]
    for scope, stats, measure, limit in checks:
        index = 0 if measure == "docs" else 1
        part = int(stats.arrays["outcome"][DROP, index])
        whole = stats.docs if measure == "docs" else stats.bytes
        if part * 100 > limit * whole:
            breaches.append(
                {
                    "scope": scope,
                    "measure": f"drop_{measure}_pct",
                    "value_pct": _pct(part, whole),
                    "limit_pct_gt": limit,
                    "drop": part,
                    "total": whole,
                }
            )
    status = REQUIRES_REVIEW if breaches else WITHIN_GUARDRAILS
    return {
        "status": status,
        "limits_pct_strictly_greater_than": limits,
        "breaches": breaches,
        "note": (
            "Crossing a guardrail is NOT an execution failure (read-only dry run); it "
            "marks the policy POLICY_REQUIRES_REVIEW for the operator."
        ),
    }


# -- review selection ------------------------------------------------------------------------


def select_review(
    candidates: Mapping[str, list[Any]], policy: CompiledPolicy, components: Sequence[str]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Deterministic selection: strata in policy order; a stratum adds new documents
    only until ``quota`` selected rows belong to it; stop at ``max_rows``."""
    params = policy.params
    selected: list[dict[str, Any]] = []
    keys: set[tuple[str, int]] = set()
    report: list[dict[str, Any]] = []
    for stratum in expand_strata(params, components):
        have = sum(1 for row in selected if stratum.name in row["strata"])
        pool = candidates.get(stratum.name, [])
        for entry in pool:
            if have >= stratum.quota or len(selected) >= params.max_rows:
                break
            key = (str(entry["path"]), int(entry["row"]))
            if key in keys:
                continue
            keys.add(key)
            row = {k: entry[k] for k in sorted(entry) if k != "rank"}
            row["selected_by"] = stratum.name
            row["rank"] = entry["rank"]
            selected.append(row)
            have += 1
        report.append(
            {
                "stratum": stratum.name,
                "quota": stratum.quota,
                "selected_members": have,
                "shortfall": max(stratum.quota - have, 0),
                "shortfall_reason": None
                if have >= stratum.quota
                else ("max_rows" if len(selected) >= params.max_rows else "no further members"),
            }
        )
    for row in selected:
        if set(row) != ROW_KEYS:
            raise AggregateError("review row schema")
    return selected, report


# -- assembly --------------------------------------------------------------------------------


def build_artifacts(
    binding: Mapping[str, Any],
    units: Iterable[Mapping[str, Any]],
    policy: CompiledPolicy,
    components: Sequence[str],
) -> tuple[dict[str, bytes], str, str]:
    """All artifacts, the worker-independent result digest and the policy status."""
    overlay = binding["overlay"] is not None
    names = ("all", "c05_kept", "c05_removed") if overlay else ("all",)
    scopes: dict[str, dict[str, CleanStats]] = {}
    candidates: dict[str, list[Any]] = {}
    quotas = quota_of(policy.params)
    for unit in units:
        parts = {k: CleanStats.from_json(v) for k, v in unit["populations"].items()}
        allowed = {"c05_kept", "c05_removed"} if overlay else {"all"}
        if not set(parts) <= allowed:
            raise AggregateError("cleaning unit population names")
        component = unit["file"]["component"]
        for scope in ("global", f"component:{component}"):
            target = scopes.setdefault(scope, {n: CleanStats() for n in names})
            for name, stats in parts.items():
                target[name].merge(stats)
                if overlay:
                    target["all"].merge(stats)
        merge_candidates(candidates, unit["review"], quotas)
    if "global" not in scopes:
        raise AggregateError("no cleaning units")
    for populations in scopes.values():
        for stats in populations.values():
            stats.check()
    ordered = sorted(scopes)
    global_all = scopes["global"]["all"]
    by_component = {
        s.split(":", 1)[1]: scopes[s]["all"] for s in ordered if s.startswith("component:")
    }
    rails = guardrails(global_all, by_component, policy)
    rows, strata_report = select_review(candidates, policy, components)
    for entry in strata_report:
        members = global_all.strata.get(entry["stratum"], [0, 0])
        entry["members"] = {"docs": members[0], "bytes": members[1]}
    header = {
        "phase": "B_POLICY_DRY_RUN",
        "completion": (
            "NOT a completion signal: valid only together with a cleaning-dry-run-receipt.json "
            "that passes strict validation (`clean-report` re-derives and verifies it)"
        ),
        "mode": binding["input_manifest"]["mode"],
        "content_free": True,
        "corpus_modified": False,
        "cleaned_corpus_written": False,
        "transform_executed": False,
        "decisions_executed": [],
        "policy": {
            "version": policy.version,
            "digest": policy.digest,
            "file_sha256": policy.file_sha256,
            "phase_a_receipt_digest": policy.provenance["phase_a"]["receipt_digest"],
            "candidate_policy_sha256": policy.provenance["candidate_policy"]["sha256"],
        },
        "bindings": {
            "dry_run_binding_digest": binding["digest"],
            "input_manifest_digest": binding["input_manifest"]["digest"],
            "detector_policy": binding["detector_policy"],
        },
        "policy_status": rails["status"],
    }
    overlay_section: Any = None
    if overlay:
        overlay_section = {
            "note": (
                "DIAGNOSTIC ONLY: C05 kept/removed membership never enters a decision; the "
                "historical p0002 C05 result becomes stale once the corpus is cleaned."
            ),
            "scopes": {
                s: {n: outcomes(scopes[s][n]) for n in ("c05_kept", "c05_removed")} for s in ordered
            },
        }
    artifacts: dict[str, bytes] = {}
    artifacts["cleaning-dry-run.json"] = _json(
        {
            "kind": "xlm_quality_cleaning_dry_run_v1",
            **header,
            "guardrails": rails,
            "global": summarize(global_all),
            "components": {name: summarize(stats) for name, stats in by_component.items()},
            "frozen_thresholds": threshold_table(policy),
            "diagnostic_c05_overlay": overlay_section,
            "review_manifest": {
                "rows": len(rows),
                "max_rows": policy.params.max_rows,
                "strata": strata_report,
            },
            "staleness": STALENESS,
        }
    )
    artifacts["cleaning-by-component.json"] = _json(
        {
            "kind": "xlm_quality_cleaning_by_component_v1",
            **header,
            "components": {
                name: {
                    "summary": summarize(stats),
                    "guardrail_breaches": [
                        b for b in rails["breaches"] if b["scope"] == f"component:{name}"
                    ],
                }
                for name, stats in by_component.items()
            },
        }
    )
    actions = {name: OUTCOMES[action] for name, action in RULES}
    definitions = _rule_definitions(policy)
    artifacts["cleaning-by-rule.json"] = _json(
        {
            "kind": "xlm_quality_cleaning_by_rule_v1",
            **header,
            "impact_meaning": {
                "marginal": "documents on which the rule fires (regardless of other rules)",
                "exclusive": (
                    "DROP rule: the ONLY DROP rule fired (removing it would keep the document "
                    "out of DROP); REVIEW rule: the outcome is REVIEW and it is the ONLY "
                    "REVIEW rule fired"
                ),
            },
            "rules": {
                name: {
                    "action": actions[name],
                    "definition": definitions[name],
                    "global": {
                        **rule_impacts(global_all)[name],
                        "by_class": {
                            cls: _pair(global_all.arrays["rule_class"][n, c], global_all)
                            for c, cls in enumerate(CLASS_ORDER)
                        },
                    },
                    "components": {
                        comp: rule_impacts(stats)[name] for comp, stats in by_component.items()
                    },
                }
                for n, name in enumerate(RULE_IDS)
            },
        }
    )
    artifacts["cleaning-rule-intersections.json"] = _json(
        {
            "kind": "xlm_quality_cleaning_rule_intersections_v1",
            **header,
            "scopes": {
                s: {
                    "unions": union_check(scopes[s]["all"]),
                    "pairwise_overlaps": overlaps(scopes[s]["all"]),
                    "exact_rule_combinations": combinations(scopes[s]["all"]),
                }
                for s in ordered
            },
        }
    )
    artifacts[REVIEW_MANIFEST] = b"".join(
        json.dumps(r, sort_keys=True, ensure_ascii=False).encode("utf-8") + b"\n" for r in rows
    )
    artifacts["cleaning-dry-run-summary.md"] = summary_markdown(
        global_all, by_component, rails, policy, rows, strata_report, binding
    )
    result = canonical.digest(
        {name: hashlib.sha256(data).hexdigest() for name, data in sorted(artifacts.items())}
    )
    return artifacts, result, str(rails["status"])


def _rule_definitions(policy: CompiledPolicy) -> dict[str, str]:
    p = policy.params
    return {
        "hard.full_html": "markup_full_html flag (true HTML page; code/example markup excluded)",
        "hard.nul": f"nul >= {p.nul_at_least}",
        "hard.noncharacters": f"noncharacters >= {p.noncharacters_at_least}",
        "rep.severe_default": (
            f"default class group and severe_repetition_signal_count >= {p.default_drop_at_least}"
        ),
        "rep.severe_structured": (
            "structured class group and severe_repetition_signal_count >= "
            f"{p.structured_drop_at_least}"
        ),
        "rep.structured_two_signals": (
            "structured class group and severe_repetition_signal_count == "
            f"{p.structured_review_equals}"
        ),
        "ocr.finepdfs_with_repetition": (
            f"OCR-scoped component, ocr_signal_count >= {p.ocr_at_least} and "
            f"severe_repetition_signal_count >= {p.ocr_drop_severe_at_least}"
        ),
        "ocr.finepdfs_review": (
            f"OCR-scoped component, ocr_signal_count >= {p.ocr_at_least} and "
            "severe_repetition_signal_count == 0"
        ),
        "enc.replacement_review": f"replacement_chars >= {p.replacement_at_least}",
        "enc.mojibake_review": f"mojibake_hits >= {p.mojibake_at_least}",
        "enc.replacement_and_mojibake": "enc.replacement_review and enc.mojibake_review",
        "enc.with_forbidden_controls": (
            "(enc.replacement_review or enc.mojibake_review) and any of "
            f"{', '.join(p.forbidden_controls)} >= 1"
        ),
    }


def summary_markdown(
    global_all: CleanStats,
    by_component: Mapping[str, CleanStats],
    rails: Mapping[str, Any],
    policy: CompiledPolicy,
    rows: Sequence[Mapping[str, Any]],
    strata_report: Sequence[Mapping[str, Any]],
    binding: Mapping[str, Any],
) -> bytes:
    def cell(stats: CleanStats, outcome: int) -> str:
        docs, nbytes = (int(x) for x in stats.arrays["outcome"][outcome])
        return (
            f"{docs:,} ({_pct(docs, stats.docs):.4f}%) / {nbytes:,} B "
            f"({_pct(nbytes, stats.bytes):.4f}%)"
        )

    lines = [
        "# XLM Phase-B cleaning policy DRY RUN (content-free)",
        "",
        f"**{rails['status']}**. Policy `{policy.version}` (`{policy.digest[:16]}`), "
        f"input manifest `{binding['input_manifest']['digest'][:16]}` "
        f"({binding['input_manifest']['mode']}).",
        "No document was dropped, transformed or written; no cleaned corpus exists.",
        "",
        "## Outcomes (docs / canonical UTF-8 bytes)",
        "",
        "| scope | documents | bytes | KEEP | DROP | REVIEW |",
        "|---|---:|---:|---|---|---|",
    ]
    for scope, stats in [("global", global_all), *sorted(by_component.items())]:
        lines.append(
            f"| {scope} | {stats.docs:,} | {stats.bytes:,} | {cell(stats, KEEP)} | "
            f"{cell(stats, DROP)} | {cell(stats, REVIEW)} |"
        )
    lines += ["", "## Guardrails", ""]
    if rails["breaches"]:
        for b in rails["breaches"]:
            lines.append(
                f"- {b['scope']}: {b['measure']} = {b['value_pct']:.4f}% > {b['limit_pct_gt']}%"
            )
    else:
        lines.append("- no guardrail crossed")
    severe = global_all.arrays["severe"]
    lines += [
        "",
        "## Severe repetition signal count (global documents)",
        "",
        "| signals | docs | default group | structured group |",
        "|---:|---:|---:|---:|",
    ]
    for count in range(N_REP + 1):
        lines.append(
            f"| {count} | {int(severe[:, count, :, 0].sum()):,} | "
            f"{int(severe[0, count, :, 0].sum()):,} | {int(severe[1, count, :, 0].sum()):,} |"
        )
    lines += [
        "",
        "## Rules (global): marginal and exclusive documents",
        "",
        "| rule | action | marginal docs | marginal bytes | exclusive docs |",
        "|---|---|---:|---:|---:|",
    ]
    a = global_all.arrays
    for n, (name, action) in enumerate(RULES):
        lines.append(
            f"| {name} | {OUTCOMES[action]} | {int(a['rule'][n, 0]):,} | "
            f"{int(a['rule'][n, 1]):,} | {int(a['rule_exclusive'][n, 0]):,} |"
        )
    lines += [
        "",
        f"## Review manifest: {len(rows)} rows (max {policy.params.max_rows}; locators only)",
        "",
        "| stratum | quota | selected members | shortfall |",
        "|---|---:|---:|---|",
    ]
    for entry in strata_report:
        reason = entry["shortfall_reason"] or ""
        lines.append(
            f"| {entry['stratum']} | {entry['quota']} | {entry['selected_members']} | "
            f"{entry['shortfall']} {reason} |"
        )
    lines += ["", "## Staleness", "", STALENESS, ""]
    return "\n".join(lines).encode("utf-8")
