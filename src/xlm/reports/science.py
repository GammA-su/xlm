"""Standard science-v1 comparison result reports (P35 §U, M4).

Renders an existing, hash-verified comparison record as JSON, Markdown and CSV
with the existing ``reports.render`` conventions: missing values are ``n/a``,
``NOT RUN`` or ``PARTIAL n/N`` (never zero), and every data-derived string is
escaped. Rendering never recomputes or changes a decision: it reads the
record's frozen fields only, after verifying the record hash.
"""

from __future__ import annotations

import math
import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from xlm.comparison.science_compare import summary_state, verify_comparison_record
from xlm.reports.render import escape_markdown_text, metrics_to_csv, to_json

SCIENCE_REPORT_VERSION = "xlm-science-report-v1"

SUMMARY_COLUMNS = (
    "comparison_id",
    "parent_reference",
    "track",
    "changed_variable",
    "model",
    "parameter_count",
    "target_tokens",
    "paired_n",
    "primary_metric",
    "primary_control",
    "primary_candidate",
    "paired_delta",
    "seed_sd",
    "seed_ci",
    "multiplicity",
    "curve_area_delta",
    "secondary",
    "throughput",
    "vram",
    "failures",
    "completeness",
    "eligibility",
    "decision",
    "promotion_state",
    "manifest_hash",
    "record_hash",
    "synthetic",
)


def _num(value: Any) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.6g}"
    return str(value)


def _mean(values: Sequence[float]) -> float | None:
    return math.fsum(values) / len(values) if values else None


def _runs_for(record: Mapping[str, Any], arm_ids: Sequence[str]) -> list[Mapping[str, Any]]:
    return [r for r in record["runs"] if r.get("arm_id") in arm_ids]


def _measurement(
    runs: Sequence[Mapping[str, Any]], arm_id: str, name: str
) -> tuple[float | None, int, int]:
    counted = [r for r in runs if r.get("arm_id") == arm_id and r.get("state") == "complete"]
    values = [
        float(m["value"])
        for r in counted
        if (m := (r.get("measurements") or {}).get(name)) is not None
        and isinstance(m.get("value"), (int, float))
    ]
    return _mean(values), len(values), len(counted)


def _coverage_text(mean: float | None, found: int, total: int) -> str:
    if total == 0 or found == 0:
        return "NOT RUN"
    text = _num(mean)
    return text if found == total else f"{text} (PARTIAL {found}/{total})"


def summary_rows(record: Mapping[str, Any]) -> list[dict[str, Any]]:
    """One §U row per candidate arm; invalid comparisons are unmistakable."""
    manifest = record.get("manifest") or {}
    validation = record["manifest_validation"]
    base = {
        "comparison_id": manifest.get("comparison_id"),
        "parent_reference": (
            f"{manifest['parent_baseline']['id']}@{manifest['parent_baseline']['version']}"
            if isinstance(manifest.get("parent_baseline"), Mapping)
            else None
        ),
        "track": manifest.get("track"),
        "changed_variable": ", ".join(manifest.get("intended_differences") or []) or None,
        "target_tokens": manifest.get("training_budget_targets"),
        "primary_metric": (manifest.get("primary_metric") or {}).get("name"),
        "manifest_hash": record.get("manifest_hash"),
        "record_hash": record.get("record_hash"),
        "synthetic": "SYNTHETIC" if record.get("synthetic_evidence") else "no",
    }
    if not validation["valid"]:
        problems = "; ".join(f"{p['field']}: {p['problem']}" for p in validation["problems"])
        return [
            {
                **base,
                "eligibility": f"INELIGIBLE: manifest {problems}",
                "decision": "INELIGIBLE",
                "promotion_state": "NOT_ELIGIBLE",
                "completeness": "NOT RUN",
            }
        ]
    control_id = manifest["control_arm"]["arm_id"]
    rows: list[dict[str, Any]] = []
    for candidate in record["candidates"]:
        arm_id = candidate["arm_id"]
        runs = _runs_for(record, (control_id, arm_id))
        counted = [r for r in runs if r.get("state") == "complete"]
        counts = sorted({r["parameter_count"] for r in counted if r.get("parameter_count")})
        pairing = candidate["pairing"]
        stats = candidate.get("statistics")
        decision = candidate.get("decision") or {}
        failures = [r for r in runs if r.get("state") != "complete"]
        row: dict[str, Any] = {
            **base,
            "changed_variable": f"{base['changed_variable']} = {candidate['intervention']}",
            "model": _model_label(counted),
            "parameter_count": " / ".join(str(c) for c in counts) if counts else None,
            "paired_n": f"{pairing['n_complete']}/{pairing['n_required']} pairs",
            "failures": (
                f"{len(failures)}: " + "; ".join(f"{r['label']} ({r['state']})" for r in failures)
                if failures
                else "0"
            ),
            "promotion_state": candidate["promotion"]["state"],
            "decision": decision.get("result"),
        }
        if not candidate["eligible"]:
            fields = sorted({v["field"] for v in candidate["field_violations"]})
            row["eligibility"] = "INELIGIBLE: field diff " + (
                ", ".join(fields) if fields else "; ".join(candidate["ineligible_reasons"])
            )
            for key in ("primary_control", "primary_candidate", "paired_delta", "seed_sd"):
                row[key] = "INELIGIBLE (no effect estimate)"
            row["seed_ci"] = "INELIGIBLE"
            row["completeness"] = f"{pairing['n_complete']}/{pairing['n_required']}"
            rows.append(row)
            continue
        row["eligibility"] = "ELIGIBLE"
        missing = ", ".join(p["tuple_id"] for p in pairing["pairs"] if p["state"] != "COMPLETE")
        row["completeness"] = (
            "COMPLETE"
            if pairing["n_complete"] == pairing["n_required"]
            else f"INCOMPLETE {pairing['n_complete']}/{pairing['n_required']} (missing: {missing})"
        )
        if stats is None:
            for key in ("primary_control", "primary_candidate", "paired_delta", "seed_sd"):
                row[key] = "NOT RUN"
            row["seed_ci"] = "NOT RUN"
        else:
            partial = pairing["n_complete"] < pairing["n_required"]
            tag = (
                f"PROVISIONAL {pairing['n_complete']}/{pairing['n_required']}: " if partial else ""
            )
            row["primary_control"] = _num(_mean([p["control"] for p in stats["pairs"]]))
            row["primary_candidate"] = _num(_mean([p["candidate"] for p in stats["pairs"]]))
            row["paired_delta"] = (
                f"{tag}{_num(stats['mean_raw_delta'])} (candidate-control; improvement "
                f"{_num(stats['mean_improvement'])}, {stats['direction']})"
            )
            row["seed_sd"] = _num(stats["sd"]) if stats["sd"] is not None else "n/a (n=1)"
            row["seed_ci"] = (
                f"{tag}[{_num(stats['ci_raw_delta'][0])}, {_num(stats['ci_raw_delta'][1])}] "
                f"t(df={stats['df']})={_num(stats['t_critical'])}"
                if stats["ci_raw_delta"] is not None
                else "none (n<2: no seed CI)"
            )
            row["multiplicity"] = (
                f"Bonferroni m={stats['family_size']} family "
                f"{stats['multiplicity']['family_id']}; per-comparison level "
                f"{_num(stats['per_comparison_ci_level'])}"
            )
        curve = candidate.get("curve")
        if curve is None:
            row["curve_area_delta"] = "NOT RUN" if manifest.get("curve_metric") else "n/a"
        elif not curve["complete"]:
            row["curve_area_delta"] = "INCOMPLETE"
        else:
            row["curve_area_delta"] = _num(curve["statistics"]["mean_raw_delta"])
        parts = []
        for guard in candidate.get("guardrails", []):
            parts.append(f"{guard['name']}: guardrail {guard['status']}")
        for item in candidate.get("secondary", []):
            s = item.get("statistics")
            parts.append(
                f"{item['name']}: "
                + (_num(s["mean_raw_delta"]) if s else "NOT RUN")
                + f" [{item['coverage']}]"
            )
        declared = [s["name"] for s in manifest.get("secondary_metrics", [])]
        row["secondary"] = "; ".join(parts) if parts else ("NOT RUN" if declared else "n/a")
        for column, name in (
            ("throughput", "successful_targets_per_second"),
            ("vram", "peak_vram_gib"),
        ):
            control_value = _measurement(runs, control_id, name)
            candidate_value = _measurement(runs, arm_id, name)
            row[column] = (
                f"control {_coverage_text(*control_value)} / candidate "
                f"{_coverage_text(*candidate_value)} (declared)"
            )
        rows.append(row)
    return rows


def _model_label(runs: Sequence[Mapping[str, Any]]) -> str | None:
    labels = sorted({str(r.get("model")) for r in runs if r.get("model")})
    return ", ".join(labels) if labels else None


def _cell(value: Any) -> str:
    text = "n/a" if value is None else str(value)
    return escape_markdown_text(text.replace("\r", " ").replace("\n", " ")).replace("|", "\\|")


def _table(columns: Sequence[str], rows: Sequence[Mapping[str, Any]]) -> list[str]:
    lines = [
        "| " + " | ".join(_cell(c) for c in columns) + " |",
        "|" + "---|" * len(columns),
    ]
    for row in rows:
        lines.append("| " + " | ".join(_cell(row.get(c)) for c in columns) + " |")
    return lines


def to_markdown(record: Mapping[str, Any]) -> str:
    verify_comparison_record(record)
    manifest = record.get("manifest") or {}
    lines = [f"# Science-v1 comparison: {_cell(manifest.get('comparison_id'))}", ""]
    if record.get("synthetic_evidence"):
        lines += ["**SYNTHETIC EVIDENCE: authored values, not results.**", ""]
    lines += [
        f"- Title: {_cell(manifest.get('title'))}",
        f"- Manifest hash: `{_cell(record.get('manifest_hash'))}`",
        f"- Record hash: `{_cell(record.get('record_hash'))}`",
        f"- State: **{_cell(summary_state(record))}**",
        "- Versions: "
        + _cell(", ".join(f"{k}={v}" for k, v in sorted(record["versions"].items()))),
        "- Seed uncertainty: paired two-sided Student-t over independent training-seed "
        "pairs (Bonferroni); item-level uncertainty is separate and never decides.",
        "",
        "## Summary (§U)",
        "",
    ]
    lines += _table(SUMMARY_COLUMNS[:-3], summary_rows(record))
    validation = record["manifest_validation"]
    if not validation["valid"]:
        lines += ["", "## INELIGIBLE: manifest problems", ""]
        lines += _table(("field", "problem"), validation["problems"])
    if validation["promotion_blockers"]:
        lines += ["", "## Promotion blockers in the manifest", ""]
        lines += _table(("field", "problem"), validation["promotion_blockers"])
    for candidate in record["candidates"]:
        lines += ["", f"## Candidate {_cell(candidate['arm_id'])}", ""]
        promotion = candidate["promotion"]
        lines.append(
            f"- Promotion: **{_cell(promotion['state'])}**: "
            + _cell("; ".join(promotion["reasons"]))
        )
        for blocker in promotion["blockers"]:
            lines.append(f"- Blocker {_cell(blocker['requirement'])}: {_cell(blocker['reason'])}")
        decision = candidate.get("decision") or {}
        lines.append(
            f"- Decision: **{_cell(decision.get('result'))}**: "
            + _cell("; ".join(decision.get("reasons", [])))
        )
        if candidate["field_violations"] or not candidate["eligible"]:
            lines += ["", "### INELIGIBLE field diff", ""]
            rows = [
                {
                    "tuple": v.get("tuple_id"),
                    "field": v.get("field"),
                    "expected/control": v.get("control", v.get("expected")),
                    "observed/candidate": v.get("candidate", v.get("observed")),
                    "classification": v.get("classification"),
                    "reason": v.get("reason"),
                }
                for v in candidate["field_violations"]
            ]
            lines += _table(
                (
                    "tuple",
                    "field",
                    "expected/control",
                    "observed/candidate",
                    "classification",
                    "reason",
                ),
                rows,
            )
            for reason in candidate["ineligible_reasons"]:
                lines.append(f"- {_cell(reason)}")
        stats = candidate.get("statistics") or {}
        pair_values = {p["pair_id"]: p for p in stats.get("pairs", [])}
        pair_rows = []
        for pair in candidate["pairing"]["pairs"]:
            value = pair_values.get(pair["tuple_id"], {})
            pair_rows.append(
                {
                    "tuple": pair["tuple_id"],
                    "state": pair["state"],
                    "control": _num(value.get("control")) if value else "NOT RUN",
                    "candidate": _num(value.get("candidate")) if value else "NOT RUN",
                    "raw delta": _num(value.get("raw_delta")) if value else "n/a",
                    "attempts": "; ".join(
                        f"{a['label']}:{a['state']}"
                        for a in (*pair["control_attempts"], *pair["candidate_attempts"])
                    ),
                }
            )
        lines += ["", "### Pairs (explicit replicate identity)", ""]
        lines += _table(
            ("tuple", "state", "control", "candidate", "raw delta", "attempts"), pair_rows
        )
        if candidate.get("item_level_uncertainty") is not None:
            lines += [
                "",
                "- Item-level uncertainty (separate source; not used for the decision): "
                + _cell(candidate["item_level_uncertainty"]["value"]),
            ]
    lines += ["", "## All attempts (failed and excluded attempts stay visible)", ""]
    lines += _table(
        ("label", "arm", "tuple", "state", "run", "reasons"),
        [
            {
                "label": r["label"],
                "arm": r.get("arm_id"),
                "tuple": r.get("tuple_id"),
                "state": r["state"],
                "run": r.get("run_id"),
                "reasons": "; ".join(r.get("reasons", [])) or "",
            }
            for r in record["runs"]
        ],
    )
    return "\n".join(lines) + "\n"


def to_csv(record: Mapping[str, Any]) -> str:
    verify_comparison_record(record)
    return metrics_to_csv(summary_rows(record), SUMMARY_COLUMNS)


def _write(path: Path, text: str) -> None:
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    with temporary.open("x", encoding="utf-8", newline="") as stream:
        stream.write(text)
    temporary.replace(path)


def write_science_report(record: Mapping[str, Any], output_dir: Path) -> dict[str, Path]:
    """Write ``comparison.json``, ``report.md`` and ``summary.csv`` (atomic replaces)."""
    verify_comparison_record(record)
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "json": output_dir / "comparison.json",
        "markdown": output_dir / "report.md",
        "csv": output_dir / "summary.csv",
    }
    _write(paths["json"], to_json(record) + "\n")
    _write(paths["markdown"], to_markdown(record))
    _write(paths["csv"], to_csv(record))
    return paths
