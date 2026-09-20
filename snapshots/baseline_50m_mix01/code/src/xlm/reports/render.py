"""Renderers for JSON, CSV, Markdown and self-contained HTML (P19, A34).

All text originating from data (previews, reasons, identifiers) is escaped for
its target format, so an HTML-injection fixture renders as inert text. Missing
values render as "n/a" in every format; nothing becomes zero. Pages are fully
self-contained (inline CSS/SVG, no external assets) so static reports stay
readable after moving them with their directory.
"""

from __future__ import annotations

import csv
import html
import io
import json
from collections.abc import Mapping, Sequence
from typing import Any

REPORT_CSS = (
    "body{font-family:system-ui,sans-serif;max-width:960px;margin:2em auto;padding:0 1em;}"
    "table{border-collapse:collapse;width:100%;margin:1em 0;}"
    "th,td{border:1px solid #999;padding:4px 8px;text-align:left;}"
    "th{background:#eee;}.missing{color:#900;}.fail{background:#fee;}"
    ".note{color:#555;font-size:0.9em;}svg{border:1px solid #ccc;background:#fff;}"
)


def escape_html_text(value: Any) -> str:
    """Escape arbitrary values for HTML body text."""
    return html.escape(str(value), quote=True)


def escape_markdown_text(value: Any) -> str:
    """Escape characters that could inject markup, links or HTML into markdown.

    Only emphasis/code/link/HTML vectors are escaped; plain punctuation such as
    `.`, `-` or `#` is left readable since it cannot alter structure mid-line.
    """
    text = str(value)
    for char in ("\\", "`", "*", "_", "{", "}", "[", "]", "(", ")", "!"):
        text = text.replace(char, f"\\{char}")
    return text.replace("<", "&lt;").replace(">", "&gt;").replace("&", "&amp;")


def to_json(payload: Mapping[str, Any]) -> str:
    """Canonical JSON rendering."""
    return json.dumps(payload, indent=2, sort_keys=True, default=str)


def _cell(value: Any) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def metrics_to_csv(rows: Sequence[Mapping[str, Any]], columns: Sequence[str]) -> str:
    """Render metric rows to CSV. Missing cells are 'n/a', never zero."""
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(columns), extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({column: _cell(row.get(column)) for column in columns})
    return buffer.getvalue()


def run_metrics_rows(report: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Flatten a run report's metrics into CSV rows with provenance."""
    rows: list[dict[str, Any]] = []
    losses = report.get("losses", {})
    if isinstance(losses, dict):
        for name, metric in losses.items():
            if isinstance(metric, dict):
                rows.append(
                    {
                        "run_id": report.get("run_id"),
                        "section": "loss",
                        "metric": name,
                        "value": metric.get("value"),
                        "unit": metric.get("unit", ""),
                        "provenance": metric.get("provenance", ""),
                    }
                )
    benchmarks = report.get("benchmarks", {})
    if isinstance(benchmarks, dict):
        for task_name, task in (benchmarks.get("tasks", {}) or {}).items():
            if isinstance(task, dict):
                for metric_name in ("acc", "acc_norm"):
                    rows.append(
                        {
                            "run_id": report.get("run_id"),
                            "section": f"benchmark:{task_name}",
                            "metric": metric_name,
                            "value": task.get(metric_name),
                            "unit": "fraction",
                            "provenance": "evaluation evidence",
                        }
                    )
    return rows


def _smoothed(values: Sequence[float], window: int = 5) -> list[float | None]:
    """Trailing moving average labeled as smoothing; raw points stay separate."""
    if window < 1:
        raise ValueError("smoothing window must be positive")
    result: list[float | None] = []
    for index in range(len(values)):
        if index + 1 < window:
            result.append(None)
        else:
            result.append(sum(values[index + 1 - window : index + 1]) / window)
    return result


def curve_svg(
    points: Sequence[Mapping[str, Any]],
    x_key: str,
    y_key: str,
    width: int = 560,
    height: int = 200,
) -> str:
    """Inline SVG of raw points plus a labeled trailing-average smoothing line."""
    pairs: list[tuple[float, float]] = []
    for point in points:
        try:
            pairs.append((float(point[x_key]), float(point[y_key])))
        except (KeyError, TypeError, ValueError):
            continue
    if len(pairs) < 2:
        return '<p class="note">insufficient points for a curve view</p>'
    xs = [x for x, _ in pairs]
    ys = [y for _, y in pairs]
    x_lo, x_hi = min(xs), max(xs)
    y_lo, y_hi = min(ys), max(ys)
    if x_hi == x_lo:
        x_hi = x_lo + 1.0
    if y_hi == y_lo:
        y_hi = y_lo + 1.0

    def place(x: float, y: float) -> tuple[float, float]:
        px = 40 + (x - x_lo) / (x_hi - x_lo) * (width - 60)
        py = height - 25 - (y - y_lo) / (y_hi - y_lo) * (height - 45)
        return px, py

    raw = " ".join(f"{px:.1f},{py:.1f}" for px, py in (place(x, y) for x, y in pairs))
    smooth = _smoothed(ys)
    smooth_pairs = [
        place(x, value) for (x, _), value in zip(pairs, smooth, strict=True) if value is not None
    ]
    smooth_line = " ".join(f"{px:.1f},{py:.1f}" for px, py in smooth_pairs)
    dots = "".join(
        f'<circle cx="{px:.1f}" cy="{py:.1f}" r="2.5" fill="#2255cc"/>'
        for px, py in (place(x, y) for x, y in pairs)
    )
    return (
        f'<svg width="{width}" height="{height}" role="img" aria-label="learning curve">'
        f'<polyline points="{raw}" fill="none" stroke="#2255cc" stroke-width="1.5"/>'
        f"{dots}"
        + (
            f'<polyline points="{smooth_line}" fill="none" stroke="#cc5522" '
            'stroke-width="1.5" stroke-dasharray="5,3"/>'
            '<text x="45" y="14" font-size="11" fill="#cc5522">trailing-average (smoothing)</text>'
            if smooth_pairs
            else ""
        )
        + f'<text x="45" y="{height - 8}" font-size="11">raw points</text></svg>'
    )


def _kv_table(rows: Sequence[tuple[str, Any]], missing_class: bool = True) -> str:
    body = []
    for key, value in rows:
        display = "n/a" if value is None else escape_html_text(value)
        cls = ' class="missing"' if value is None and missing_class else ""
        body.append(f"<tr><th>{escape_html_text(key)}</th><td{cls}>{display}</td></tr>")
    return "<table>" + "".join(body) + "</table>"


def to_markdown_run(report: Mapping[str, Any]) -> str:
    """Markdown run report with escaped data text."""
    esc = escape_markdown_text
    lines = [
        f"# Run report: {esc(report.get('run_id', 'unknown'))}",
        "",
        f"- Status: **{esc(report.get('status', 'unknown'))}**",
        f"- Experiment: {esc(report.get('experiment_id', 'unknown'))}",
        f"- Plan: {esc(report.get('plan_id', 'n/a'))} (`{esc(report.get('plan_hash') or 'n/a')}`)",
        "",
        "## Losses",
        "",
    ]
    losses = report.get("losses", {})
    if isinstance(losses, dict):
        for name, metric in losses.items():
            if isinstance(metric, dict):
                value = metric.get("value")
                lines.append(
                    f"- {esc(name)}: "
                    f"{esc(f'{value:.4f}' if isinstance(value, float) else 'n/a')} "
                    f"{esc(metric.get('unit', ''))} _({esc(metric.get('provenance', ''))})_"
                )
    lines += ["", "## Benchmarks", ""]
    benchmarks = report.get("benchmarks", {})
    if isinstance(benchmarks, dict):
        lines.append(f"- Coverage: {esc(benchmarks.get('coverage_note', 'n/a'))}")
        for task_name, task in (benchmarks.get("tasks", {}) or {}).items():
            if isinstance(task, dict):
                lines.append(
                    f"- {esc(task_name)}: acc={esc(task.get('acc', 'n/a'))} "
                    f"acc_norm={esc(task.get('acc_norm', 'n/a'))} "
                    f"scored {esc(task.get('scored', 'n/a'))}/{esc(task.get('total', 'n/a'))}"
                )
    lines += ["", "## Failures", ""]
    failures = report.get("failures", [])
    lines.append("None recorded." if not failures else "")
    for failure in failures:
        lines.append(f"- {esc(failure)}")
    missing = report.get("missing", [])
    if missing:
        lines += ["", "## Missing (not zero-filled)", ""]
        for item in missing:
            lines.append(f"- {esc(item)}")
    return "\n".join(lines) + "\n"


def to_html_run(report: Mapping[str, Any]) -> str:
    """Self-contained HTML run report with escaped data and visible failures."""
    esc = escape_html_text
    parts = [
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>",
        f"<title>Run report {esc(report.get('run_id', 'unknown'))}</title>",
        f"<style>{REPORT_CSS}</style></head><body>",
        f"<h1>Run report: {esc(report.get('run_id', 'unknown'))}</h1>",
        _kv_table(
            [
                ("Status", report.get("status", "unknown")),
                ("Experiment", report.get("experiment_id", "unknown")),
                ("Plan", report.get("plan_id")),
                ("Plan hash", (report.get("plan_hash") or "")[:16] or None),
            ]
        ),
        "<h2>Losses</h2>",
    ]
    losses = report.get("losses", {})
    loss_rows = []
    if isinstance(losses, dict):
        for name, metric in losses.items():
            if isinstance(metric, dict):
                loss_rows.append(
                    f"<tr><td>{esc(name)}</td><td>{esc(metric.get('value', 'n/a'))}</td>"
                    f"<td>{esc(metric.get('unit', ''))}</td>"
                    f"<td>{esc(metric.get('provenance', ''))}</td></tr>"
                )
    parts.append(
        "<table><tr><th>metric</th><th>value</th><th>unit</th><th>provenance</th></tr>"
        + "".join(loss_rows)
        + "</table>"
    )

    parts.append("<h2>Benchmarks</h2>")
    benchmarks = report.get("benchmarks", {})
    if isinstance(benchmarks, dict):
        parts.append(f"<p>Coverage: {esc(benchmarks.get('coverage_note', 'n/a'))}</p>")
        task_rows = []
        for task_name, task in (benchmarks.get("tasks", {}) or {}).items():
            if isinstance(task, dict):
                task_rows.append(
                    f"<tr><td>{esc(task_name)}</td><td>{esc(task.get('acc', 'n/a'))}</td>"
                    f"<td>{esc(task.get('acc_norm', 'n/a'))}</td>"
                    f"<td>{esc(task.get('scored', 'n/a'))}/{esc(task.get('total', 'n/a'))}</td>"
                    f"<td>{esc(task.get('omitted', 0))}</td></tr>"
                )
        parts.append(
            "<table><tr><th>task</th><th>acc</th><th>acc_norm</th>"
            "<th>scored</th><th>omitted</th></tr>" + "".join(task_rows) + "</table>"
        )

    failures = report.get("failures", [])
    parts.append("<h2>Failures</h2>")
    if failures:
        parts.append(
            '<div class="fail"><ul>'
            + "".join(f"<li>{esc(failure)}</li>" for failure in failures)
            + "</ul></div>"
        )
    else:
        parts.append("<p>None recorded.</p>")

    curves = report.get("curves", {})
    if isinstance(curves, dict) and curves:
        parts.append("<h2>Learning curves</h2>")
        for name, points in curves.items():
            if isinstance(points, list):
                parts.append(f"<h3>{esc(name)} vs tokens</h3>")
                parts.append(curve_svg(points, "x_tokens", "value"))

    comparison = report.get("comparison", {})
    if isinstance(comparison, dict):
        parts.append("<h2>Comparison</h2>")
        parts.append(
            _kv_table(
                [
                    ("Eligible", comparison.get("eligible")),
                    ("Uncertainty", comparison.get("uncertainty")),
                ]
            )
        )
        violations = comparison.get("violations", [])
        if violations:
            parts.append("<ul>" + "".join(f"<li>{esc(v)}</li>" for v in violations) + "</ul>")

    missing = report.get("missing", [])
    if missing:
        parts.append(
            "<h2>Missing (not zero-filled)</h2><ul>"
            + "".join(f"<li>{esc(item)}</li>" for item in missing)
            + "</ul>"
        )
    parts.append("</body></html>")
    return "".join(parts)


def to_markdown_data(report: Mapping[str, Any]) -> str:
    """Markdown dataset-side report with escaped previews."""
    esc = escape_markdown_text
    lines = ["# Dataset report", ""]
    for section in ("admission", "retention", "lineage", "quality", "drift", "storage"):
        payload = json.dumps(report.get(section, {}), indent=2, sort_keys=True, default=str)
        lines += ["## " + section.capitalize(), "", "```json", payload, "```", ""]
    blocked = report.get("blocked", [])
    lines += ["## Blocked components", ""]
    lines += [f"- {esc(b)}" for b in blocked] or ["None."]
    lines += ["", "## Previews (escaped, redacted)", ""]
    for preview in report.get("previews", []):
        if isinstance(preview, dict):
            lines.append(
                f"- {esc(preview.get('doc_id', '?'))}: {esc(preview.get('text', ''))[:200]}"
            )
    missing = report.get("missing", [])
    if missing:
        lines += ["", "## Missing", ""]
        lines += [f"- {esc(m)}" for m in missing]
    return "\n".join(lines) + "\n"


def to_html_data(report: Mapping[str, Any]) -> str:
    """Self-contained HTML dataset report with escaped previews."""
    esc = escape_html_text
    parts = [
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>",
        "<title>Dataset report</title>",
        f"<style>{REPORT_CSS}</style></head><body><h1>Dataset report</h1>",
    ]
    for section in ("admission", "retention", "lineage", "quality", "drift", "storage"):
        payload = json.dumps(report.get(section, {}), indent=2, sort_keys=True, default=str)
        parts.append(f"<h2>{esc(section.capitalize())}</h2><pre>{esc(payload)}</pre>")
    blocked = report.get("blocked", [])
    parts.append("<h2>Blocked components</h2>")
    parts.append(
        "<ul>" + "".join(f"<li>{esc(b)}</li>" for b in blocked) + "</ul>"
        if blocked
        else "<p>None.</p>"
    )
    parts.append("<h2>Previews (escaped, redacted)</h2><ul>")
    for preview in report.get("previews", []):
        if isinstance(preview, dict):
            parts.append(
                f"<li>{esc(preview.get('doc_id', '?'))}: {esc(preview.get('text', ''))[:200]}</li>"
            )
    parts.append("</ul>")
    missing = report.get("missing", [])
    if missing:
        parts.append(
            "<h2>Missing</h2><ul>" + "".join(f"<li>{esc(m)}</li>" for m in missing) + "</ul>"
        )
    parts.append("</body></html>")
    return "".join(parts)


def to_markdown_campaign(report: Mapping[str, Any]) -> str:
    """Markdown campaign report: plans are labeled plans, never results."""
    esc = escape_markdown_text
    lines = [
        f"# Campaign report: {esc(report.get('campaign_id', 'unknown'))}",
        "",
        f"- Trials: {esc(report.get('total_trials', 'n/a'))}",
        f"- Total targets: {esc(report.get('total_valid_targets', 'n/a'))}",
        f"- Cost basis: {esc(report.get('cost_basis', 'unmeasured'))}",
        "",
        "## Trials",
        "",
    ]
    for trial in report.get("trials", []):
        if isinstance(trial, dict):
            blocked = trial.get("blocked_reason")
            lines.append(
                f"- `{esc(trial.get('trial_id', '?'))}` "
                f"{esc(trial.get('model_preset', '?'))}/{esc(trial.get('mixture_preset', '?'))} "
                f"{esc(trial.get('budget_valid_targets', '?'))} targets "
                f"{'(BLOCKED: ' + esc(blocked) + ')' if blocked else ''}"
            )
            executed = (report.get("runs", {}) or {}).get(trial.get("trial_id", ""), {})
            if isinstance(executed, dict) and executed.get("status"):
                lines.append(f"  - run status: {esc(executed.get('status'))}")
            elif not blocked:
                lines.append("  - plan only (no executed run)")
    blockers = report.get("blockers", [])
    if blockers:
        lines += ["", "## Blockers", ""]
        lines += [f"- {esc(b)}" for b in blockers]
    missing = report.get("missing", [])
    if missing:
        lines += ["", "## Missing", ""]
        lines += [f"- {esc(m)}" for m in missing]
    return "\n".join(lines) + "\n"


def to_html_campaign(report: Mapping[str, Any]) -> str:
    """Self-contained HTML campaign report."""
    esc = escape_html_text
    parts = [
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>",
        f"<title>Campaign {esc(report.get('campaign_id', 'unknown'))}</title>",
        f"<style>{REPORT_CSS}</style></head><body>",
        f"<h1>Campaign report: {esc(report.get('campaign_id', 'unknown'))}</h1>",
        _kv_table(
            [
                ("Trials", report.get("total_trials")),
                ("Total targets", report.get("total_valid_targets")),
                ("Cost basis", report.get("cost_basis")),
            ]
        ),
        "<h2>Trials</h2><table><tr><th>trial</th><th>model</th><th>mixture</th>"
        "<th>budget</th><th>status</th></tr>",
    ]
    for trial in report.get("trials", []):
        if isinstance(trial, dict):
            executed = (report.get("runs", {}) or {}).get(trial.get("trial_id", ""), {})
            status = executed.get("status") if isinstance(executed, dict) else None
            blocked = trial.get("blocked_reason")
            row_class = ' class="fail"' if blocked else ""
            parts.append(
                f"<tr{row_class}><td>{esc(trial.get('trial_id', '?'))}</td>"
                f"<td>{esc(trial.get('model_preset', '?'))}</td>"
                f"<td>{esc(trial.get('mixture_preset', '?'))}</td>"
                f"<td>{esc(trial.get('budget_valid_targets', '?'))}</td>"
                f"<td>{esc(status or ('BLOCKED' if blocked else 'plan only'))}</td></tr>"
            )
    parts.append("</table>")
    blockers = report.get("blockers", [])
    if blockers:
        parts.append(
            "<h2>Blockers</h2><ul>" + "".join(f"<li>{esc(b)}</li>" for b in blockers) + "</ul>"
        )
    missing = report.get("missing", [])
    if missing:
        parts.append(
            "<h2>Missing</h2><ul>" + "".join(f"<li>{esc(m)}</li>" for m in missing) + "</ul>"
        )
    parts.append("</body></html>")
    return "".join(parts)
