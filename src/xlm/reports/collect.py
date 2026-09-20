"""Collectors that assemble reports from authoritative stores only (P19, A34).

Every figure comes from the ledger, run records, artifact manifests, saved
evaluation evidence, comparison outputs, mixture plans or profiles. Anything
absent is recorded as missing and rendered as "n/a" -- never backfilled with
zeroes, means of nothing, or fixture numbers. Protected content (sealed text,
label-bearing exclusion details, secrets) is stripped at collection time, so no
renderer can leak it later.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

REPORT_VERSION = "1"

# Keys that must never appear in any export, at any depth.
PROTECTED_KEYS = frozenset(
    {
        "raw_text",
        "sealed_text",
        "sealed_example",
        "gold_labels",
        "labels",
        "label",
        "answer",
        "benchmark_example",
        "excluded_text",
        "quarantine_preview",
        "hf_token",
        "api_key",
        "secret",
        "password",
        "authorization",
        "auth_token",
    }
)

_SECRET_VALUE_PATTERNS = (
    re.compile(r"hf_[A-Za-z0-9]{10,}"),
    re.compile(r"sk-[A-Za-z0-9]{10,}"),
)


class CollectionError(RuntimeError):
    """Raised when report inputs are unreadable (never when merely incomplete)."""


def strip_protected(obj: Any) -> Any:
    """Remove protected keys and redact secret-like values, recursively."""
    if isinstance(obj, dict):
        cleaned: dict[str, Any] = {}
        for key, value in obj.items():
            if str(key).lower() in PROTECTED_KEYS:
                continue
            cleaned[key] = strip_protected(value)
        return cleaned
    if isinstance(obj, list):
        return [strip_protected(item) for item in obj]
    if isinstance(obj, str):
        redacted = obj
        for pattern in _SECRET_VALUE_PATTERNS:
            redacted = pattern.sub("[REDACTED]", redacted)
        return redacted
    return obj


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return data if isinstance(data, dict) else None


@dataclass
class MetricPoint:
    """One metric value with its provenance. Missing stays missing."""

    name: str
    value: float | None
    unit: str = ""
    provenance: str = ""
    missing_reason: str = ""

    def display(self) -> str:
        if self.value is None:
            return "n/a"
        return f"{self.value:.4f}"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RunReport:
    """Everything known about one run, with gaps labeled."""

    report_version: str
    run_id: str
    experiment_id: str
    status: str
    plan_hash: str | None
    plan_id: str | None
    model: dict[str, Any] = field(default_factory=dict)
    mixture: dict[str, Any] = field(default_factory=dict)
    exposure: dict[str, Any] = field(default_factory=dict)
    losses: dict[str, MetricPoint | dict[str, Any]] = field(default_factory=dict)
    benchmarks: dict[str, Any] = field(default_factory=dict)
    compute: dict[str, Any] = field(default_factory=dict)
    seeds: dict[str, Any] = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)
    comparison: dict[str, Any] = field(default_factory=dict)
    curves: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = asdict(self)
        payload["losses"] = {
            key: (value.to_dict() if isinstance(value, MetricPoint) else value)
            for key, value in self.losses.items()
        }
        cleaned: dict[str, Any] = strip_protected(payload)
        return cleaned


def _metric(
    store: dict[str, MetricPoint | dict[str, Any]],
    name: str,
    value: Any,
    unit: str,
    provenance: str,
    missing_reason: str = "",
) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        store[name] = {
            "value": None,
            "unit": unit,
            "provenance": provenance,
            "missing_reason": missing_reason or "no numeric value recorded",
        }
        return
    store[name] = MetricPoint(name=name, value=float(value), unit=unit, provenance=provenance)


def collect_run_report(
    run_id: str,
    runs_dir: Path | str,
    ledger: Any | None = None,
    evidence_dir: Path | str | None = None,
    comparison_dir: Path | str | None = None,
) -> RunReport:
    """Assemble a run report from the run record, ledger, evidence and comparisons."""
    runs_path = Path(runs_dir)
    record = _read_json(runs_path / run_id / "run_record.json") or {}
    ledger_row: dict[str, Any] = {}
    if ledger is not None:
        try:
            ledger_row = ledger.get_run(run_id) or {}
        except Exception:
            ledger_row = {}

    status = str(record.get("status") or ledger_row.get("status") or "unknown")
    missing: list[str] = []
    if not record:
        missing.append("run_record.json absent; ledger row only")

    plan_raw = record.get("plan")
    plan: dict[str, Any] = dict(plan_raw) if isinstance(plan_raw, dict) else {}
    model_raw = plan.get("model", {})
    model: dict[str, Any] = dict(model_raw) if isinstance(model_raw, dict) else {}
    mixture_raw = plan.get("mixture", plan.get("data", {}))
    mixture: dict[str, Any] = dict(mixture_raw) if isinstance(mixture_raw, dict) else {}
    exposure_raw = record.get("exposure", {})
    exposure: dict[str, Any] = dict(exposure_raw) if isinstance(exposure_raw, dict) else {}
    for key in ("unique_targets", "repeated_targets"):
        if key not in exposure:
            missing.append(f"exposure.{key} not recorded")

    losses: dict[str, MetricPoint | dict[str, Any]] = {}
    metrics = record.get("metrics", {})
    if isinstance(metrics, dict):
        _metric(
            losses,
            "optimized_loss",
            metrics.get("optimized_loss"),
            "",
            "run_record.metrics",
            "optimized loss not recorded",
        )
        _metric(
            losses,
            "independent_ce",
            metrics.get("independent_ce"),
            "nats",
            "run_record.metrics",
            "independent CE not recorded",
        )
        _metric(
            losses,
            "text_bpb",
            metrics.get("text_bpb"),
            "bits/byte",
            "run_record.metrics",
            "text BPB not recorded",
        )
    else:
        missing.append("run_record.metrics absent")

    benchmarks: dict[str, Any] = {"tasks": {}, "index": None, "coverage_note": ""}
    evidence: dict[str, Any] | None = None
    if evidence_dir is not None:
        candidates = sorted(Path(evidence_dir).glob(f"*{run_id}*.json"))
        if candidates:
            evidence = _read_json(candidates[0])
    if evidence is None:
        missing.append("evaluation evidence absent (no suite ran for this run)")
        benchmarks["coverage_note"] = "no benchmark coverage"
    else:
        for task_name, task in evidence.get("tasks", {}).items():
            benchmarks["tasks"][task_name] = {
                "acc": task.get("acc"),
                "acc_norm": task.get("acc_norm"),
                "scored": task.get("scored_items"),
                "total": task.get("total_items"),
                "expected": task.get("expected_items"),
                "omitted": task.get("omitted_items", 0),
            }
        index_payload = evidence.get("index") or {}
        coverage = evidence.get("coverage")
        # An index is republished only when the evidence itself states that the
        # declared scope was covered. Silence in the notes is not evidence of
        # full coverage, which is exactly what this used to assume (D05).
        complete = bool(index_payload.get("complete")) and bool(
            (coverage or {}).get("complete", False)
        )
        benchmarks["index"] = index_payload.get("index") if complete else None
        benchmarks["scope_label"] = (coverage or {}).get("scope_label", "undeclared")
        benchmarks["scope_kind"] = (coverage or {}).get("scope_kind", "undeclared")
        benchmarks["coverage_status"] = (coverage or {}).get("status", "legacy_unverified")
        benchmarks["coverage_complete"] = complete
        benchmarks["research_eligible"] = bool((coverage or {}).get("research_eligible", False))
        reasons = list((coverage or {}).get("eligibility_reasons", []))
        if coverage is None:
            reasons.append("evidence predates coverage recording; completeness is unverified")
            missing.append("evaluation evidence carries no coverage record (legacy)")
        elif not complete:
            missing.append(
                "evaluation coverage incomplete for scope "
                f"'{benchmarks['scope_label']}'; suite index withheld"
            )
        parts = [(coverage or {}).get("scope_statement", "coverage unverified")]
        parts.extend(reasons)
        parts.extend(evidence.get("notes", []))
        benchmarks["coverage_note"] = "; ".join(part for part in parts if part)

    compute: dict[str, Any] = {
        "measured_seconds": record.get("compute_seconds"),
        "profile_basis": record.get("compute_basis", "unmeasured"),
        "peak_vram_gib": record.get("peak_vram_gib"),
    }
    if compute["measured_seconds"] is None:
        missing.append("measured compute time not recorded; estimates are not substituted")

    seeds = dict(record.get("seeds", {}))
    if not seeds:
        missing.append("seed record absent")

    failures: list[str] = []
    if status in ("FAILED", "INTERRUPTED", "CANCELLED", "BLOCKED"):
        reason = (
            record.get("failure_reason")
            or record.get("cancel_reason")
            or record.get("blocked_reason")
            or ledger_row.get("last_reason")
            or "no reason recorded"
        )
        failures.append(f"{status}: {reason}")
    for attempt in record.get("attempts", []):
        if isinstance(attempt, dict) and attempt.get("state") in ("FAILED", "INTERRUPTED"):
            failures.append(f"attempt {attempt.get('attempt_no')}: {attempt.get('reason')}")

    curves: dict[str, list[dict[str, Any]]] = {}
    raw_curves = record.get("learning_curves", {})
    if isinstance(raw_curves, dict):
        for name, points in raw_curves.items():
            if isinstance(points, list):
                curves[str(name)] = [dict(p) for p in points if isinstance(p, dict)]
    if not curves:
        missing.append("learning curves absent (no per-step history recorded)")

    comparison: dict[str, Any] = {"eligible": None, "violations": [], "uncertainty": None}
    if comparison_dir is not None:
        matches = sorted(Path(comparison_dir).glob(f"*{run_id}*.json"))
        if matches:
            compared = _read_json(matches[0]) or {}
            eligibility = compared.get("eligibility", {})
            comparison = {
                "eligible": eligibility.get("eligible"),
                "violations": eligibility.get("reasons", []),
                "uncertainty": (compared.get("suite") or {}).get("index_interval"),
                "seeds": compared.get("seeds", {}),
            }
        else:
            missing.append("comparison output absent for this run")

    return RunReport(
        report_version=REPORT_VERSION,
        run_id=run_id,
        experiment_id=str(
            record.get("experiment_id") or ledger_row.get("experiment_id") or "unknown"
        ),
        status=status,
        plan_hash=record.get("plan_hash") or ledger_row.get("plan_hash"),
        plan_id=record.get("plan_id"),
        model=model,
        mixture=mixture,
        exposure=exposure,
        losses=losses,
        benchmarks=benchmarks,
        compute=compute,
        seeds=seeds,
        failures=failures,
        comparison=comparison,
        curves=curves,
        missing=missing,
    )


@dataclass
class CampaignReport:
    """Aggregate view over a campaign expansion plus any executed trials."""

    report_version: str
    campaign_id: str
    total_trials: int
    tokens_by_size: dict[str, int]
    total_valid_targets: int
    trials: list[dict[str, Any]]
    blockers: list[str] = field(default_factory=list)
    runs: dict[str, Any] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        cleaned: dict[str, Any] = strip_protected(asdict(self))
        return cleaned


def collect_campaign_report(
    campaign_plan: Mapping[str, Any],
    runs: Mapping[str, RunReport] | None = None,
) -> CampaignReport:
    """Aggregate a P16 campaign expansion with optional per-trial run reports."""
    trials = list(campaign_plan.get("trials", []))
    run_map = {trial_id: report.to_dict() for trial_id, report in (runs or {}).items()}
    missing: list[str] = []
    executed = [t for t in trials if isinstance(t, dict) and t.get("trial_id") in run_map]
    if len(executed) != len(trials):
        missing.append(
            f"{len(trials) - len(executed)} of {len(trials)} trials have no executed run; "
            "plans are not results"
        )
    return CampaignReport(
        report_version=REPORT_VERSION,
        campaign_id=str(campaign_plan.get("campaign_id", "unknown")),
        total_trials=int(campaign_plan.get("total_trials", len(trials))),
        tokens_by_size=dict(campaign_plan.get("tokens_by_size", {})),
        total_valid_targets=int(campaign_plan.get("total_valid_targets", 0)),
        trials=[dict(t) for t in trials if isinstance(t, dict)],
        blockers=list(campaign_plan.get("blockers", [])),
        runs=run_map,
        missing=missing,
    )


@dataclass
class DataReport:
    """Dataset-side rollup: admission, retention, lineage, drift, storage, blocks."""

    report_version: str
    admission: dict[str, Any] = field(default_factory=dict)
    retention: dict[str, Any] = field(default_factory=dict)
    lineage: dict[str, Any] = field(default_factory=dict)
    quality: dict[str, Any] = field(default_factory=dict)
    drift: dict[str, Any] = field(default_factory=dict)
    storage: dict[str, Any] = field(default_factory=dict)
    blocked: list[str] = field(default_factory=list)
    previews: list[dict[str, Any]] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        cleaned: dict[str, Any] = strip_protected(asdict(self))
        return cleaned


def collect_data_report(inputs: Mapping[str, Any]) -> DataReport:
    """Assemble dataset facts from caller-provided authoritative fragments.

    Previews are escaped downstream at render time; secrets are stripped here.
    Label-bearing exclusion details never survive: only aggregate counts pass.
    """
    admission = dict(inputs.get("admission", {}))
    retention = dict(inputs.get("retention", {}))
    lineage = dict(inputs.get("lineage", {}))
    quality = dict(inputs.get("quality", {}))
    drift = dict(inputs.get("drift", {}))
    storage = dict(inputs.get("storage", {}))
    blocked = [str(b) for b in inputs.get("blocked", [])]
    previews = [dict(p) for p in inputs.get("previews", [])]

    exclusion = inputs.get("exclusion", {})
    if isinstance(exclusion, dict):
        # Aggregates only: matched example text or per-item details are dropped.
        lineage["benchmark_exclusion_matches"] = exclusion.get("match_count")
        lineage["benchmark_exclusion_receipts"] = exclusion.get("receipt_ids", [])

    missing = [str(m) for m in inputs.get("missing", [])]
    for section_name, section in (
        ("admission", admission),
        ("retention", retention),
        ("lineage", lineage),
        ("quality", quality),
        ("drift", drift),
        ("storage", storage),
    ):
        if not section:
            missing.append(f"data section '{section_name}' not provided")
    return DataReport(
        report_version=REPORT_VERSION,
        admission=admission,
        retention=retention,
        lineage=lineage,
        quality=quality,
        drift=drift,
        storage=storage,
        blocked=blocked,
        previews=previews,
        missing=missing,
    )
