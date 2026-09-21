"""Acceptance tests for P19 reports: collection honesty and escaped rendering.

All content is authored synthetic fixture data, including a deliberate
HTML-injection string that must render as inert text in every format.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from xlm.reports.collect import (
    collect_campaign_report,
    collect_data_report,
    collect_run_report,
    strip_protected,
)
from xlm.reports.render import (
    curve_svg,
    escape_html_text,
    escape_markdown_text,
    metrics_to_csv,
    run_metrics_rows,
    to_html_campaign,
    to_html_data,
    to_html_run,
    to_markdown_campaign,
    to_markdown_data,
    to_markdown_run,
)

INJECTION = "<script>alert('xss')</script>"
SECRET = "hf_abcdefghij1234567890abcdefghij1234567890"


def _run_record() -> dict[str, Any]:
    return {
        "run_id": "run_demo_1",
        "experiment_id": "demo_exp",
        "status": "SUCCEEDED",
        "plan_hash": "abc123",
        "plan_id": "plan_demo",
        "plan": {
            "model": {"preset": "tiny", "vocab_size": 260},
            "mixture": {"id": "mix_demo", "weights": {"a": 0.6, "b": 0.4}},
        },
        "exposure": {"unique_targets": 1000},
        "metrics": {"optimized_loss": 2.5, "independent_ce": 2.7},
        "seeds": {"init_seed": 7, "data_seed": 8},
        "compute_seconds": 12.5,
        "compute_basis": "measured",
        "learning_curves": {
            "loss_vs_tokens": [
                {"x_tokens": 100, "value": 3.0},
                {"x_tokens": 200, "value": 2.8},
                {"x_tokens": 300, "value": 2.5},
            ]
        },
    }


def _write_run(tmp_path: Path, record: dict[str, Any] | None = None) -> Path:
    runs_dir = tmp_path / "runs"
    run_dir = runs_dir / "run_demo_1"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "run_record.json").write_text(
        json.dumps(record if record is not None else _run_record()), encoding="utf-8"
    )
    return runs_dir


def _write_evidence(tmp_path: Path) -> Path:
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    (evidence_dir / "evidence_run_demo_1.json").write_text(
        json.dumps(
            {
                "tasks": {
                    "arc_easy": {
                        "acc": 0.5,
                        "acc_norm": 0.5,
                        "scored_items": 4,
                        "total_items": 4,
                        "omitted_items": 0,
                    }
                },
                "index": {"index": None, "complete": False, "missing": ["blimp"]},
                "notes": ["bounded run at limit=4: smoke, not the benchmark"],
            }
        ),
        encoding="utf-8",
    )
    return evidence_dir


# ------------------------------------------------------------- collection


def test_missing_values_are_recorded_never_zero_filled(tmp_path: Path) -> None:
    report = collect_run_report("run_demo_1", _write_run(tmp_path))
    payload = report.to_dict()
    # repeated_targets absent from exposure, text_bpb absent from metrics.
    assert "exposure.repeated_targets not recorded" in payload["missing"]
    assert payload["losses"]["text_bpb"]["value"] is None
    assert payload["benchmarks"]["coverage_note"] == "no benchmark coverage"
    assert payload["compute"]["measured_seconds"] == 12.5


def test_protected_keys_and_secrets_are_stripped_at_collection(tmp_path: Path) -> None:
    record = _run_record()
    record["status"] = "FAILED"
    record["failure_reason"] = f"contact {SECRET} for access"
    record["sealed_text"] = "must never export"
    record["labels"] = [0, 1, 1]
    report = collect_run_report("run_demo_1", _write_run(tmp_path, record))
    payload = report.to_dict()
    dumped = json.dumps(payload)
    assert "sealed_text" not in payload
    assert "labels" not in payload
    assert SECRET not in dumped
    assert "[REDACTED]" in dumped


def test_evidence_tasks_index_and_partial_labels_land_in_report(tmp_path: Path) -> None:
    report = collect_run_report(
        "run_demo_1", _write_run(tmp_path), evidence_dir=_write_evidence(tmp_path)
    )
    payload = report.to_dict()
    assert payload["benchmarks"]["tasks"]["arc_easy"]["acc"] == 0.5
    assert payload["benchmarks"]["index"] is None
    assert "bounded run at limit=4" in payload["benchmarks"]["coverage_note"]


def test_failures_stay_visible_with_attempt_history(tmp_path: Path) -> None:
    record = _run_record()
    record["status"] = "FAILED"
    record["failure_reason"] = "simulated boom"
    record["attempts"] = [{"attempt_no": 1, "state": "FAILED", "reason": "boom detail"}]
    report = collect_run_report("run_demo_1", _write_run(tmp_path, record))
    assert any("simulated boom" in f for f in report.failures)
    assert any("boom detail" in f for f in report.failures)


def test_campaign_report_marks_unexecuted_trials() -> None:
    plan = {
        "campaign_id": "camp_x",
        "trials": [
            {
                "trial_id": "t1",
                "model_preset": "50m",
                "mixture_preset": "mix01",
                "budget_valid_targets": 100,
            },
            {
                "trial_id": "t2",
                "model_preset": "50m",
                "mixture_preset": "m1",
                "budget_valid_targets": 100,
                "blocked_reason": "needs selection",
            },
        ],
        "total_trials": 2,
        "tokens_by_size": {"50m": 200},
        "total_valid_targets": 200,
        "blockers": ["round_1 needs selection"],
    }
    report = collect_campaign_report(plan)
    payload = report.to_dict()
    assert any("no executed run" in m for m in payload["missing"])
    assert payload["blockers"] == ["round_1 needs selection"]


def test_data_report_keeps_aggregates_drops_details() -> None:
    report = collect_data_report(
        {
            "admission": {"admitted": 0, "pending": 3},
            "retention": {"docs_in": 10, "docs_out": 7},
            "exclusion": {
                "match_count": 2,
                "receipt_ids": ["r1"],
                "matched_text": ["SECRET BENCHMARK SENTENCE"],
            },
            "previews": [{"doc_id": "d1", "text": f"hello {INJECTION}"}],
            "blocked": ["src_x"],
        }
    )
    payload = report.to_dict()
    assert payload["lineage"]["benchmark_exclusion_matches"] == 2
    assert "SECRET BENCHMARK SENTENCE" not in json.dumps(payload)
    assert any("not provided" in m for m in payload["missing"])


# -------------------------------------------------------------- rendering


def test_injection_fixture_is_escaped_everywhere(tmp_path: Path) -> None:
    record = _run_record()
    record["failure_reason"] = INJECTION
    record["status"] = "FAILED"
    report = collect_run_report("run_demo_1", _write_run(tmp_path, record))
    payload = report.to_dict()
    assert "<script>" in json.dumps(payload)  # JSON is data, not markup
    html_out = to_html_run(payload)
    assert "<script>" not in html_out
    assert "&lt;script&gt;" in html_out
    markdown = to_markdown_run(payload)
    assert "<script>" not in markdown
    assert escape_html_text(INJECTION) == "&lt;script&gt;alert(&#x27;xss&#x27;)&lt;/script&gt;"
    assert "**" not in escape_markdown_text("**bold**")


def test_missing_renders_as_na_never_zero(tmp_path: Path) -> None:
    report = collect_run_report("run_demo_1", _write_run(tmp_path))
    payload = report.to_dict()
    assert "n/a" in to_markdown_run(payload)
    assert ">n/a<" in to_html_run(payload)
    rows = run_metrics_rows(payload)
    csv_text = metrics_to_csv(rows, ["run_id", "section", "metric", "value", "unit", "provenance"])
    assert "n/a" in csv_text
    text_bpb_rows = [r for r in rows if r["metric"] == "text_bpb"]
    assert text_bpb_rows and text_bpb_rows[0]["value"] is None


def test_failed_runs_render_visibly_not_hidden(tmp_path: Path) -> None:
    record = _run_record()
    record["status"] = "INTERRUPTED"
    record["failure_reason"] = "power cut"
    payload = collect_run_report("run_demo_1", _write_run(tmp_path, record)).to_dict()
    html_out = to_html_run(payload)
    assert "power cut" in html_out and "INTERRUPTED" in html_out
    assert 'class="fail"' in html_out


def test_comparison_violations_and_seeds_are_visible(tmp_path: Path) -> None:
    comparison_dir = tmp_path / "comparisons"
    comparison_dir.mkdir(parents=True, exist_ok=True)
    (comparison_dir / "cmp_run_demo_1.json").write_text(
        json.dumps(
            {
                "eligibility": {"eligible": False, "reasons": ["tokenizer differs"]},
                "suite": {"index_interval": {"point": 1.2, "ci_lo": 0.1, "ci_hi": 2.3}},
                "seeds": {"baseline": ["s1"], "candidate": ["s1", "s2"]},
            }
        ),
        encoding="utf-8",
    )
    payload = collect_run_report(
        "run_demo_1", _write_run(tmp_path), comparison_dir=comparison_dir
    ).to_dict()
    assert payload["comparison"]["eligible"] is False
    assert payload["comparison"]["violations"] == ["tokenizer differs"]
    html_out = to_html_run(payload)
    assert "tokenizer differs" in html_out


def test_curves_distinguish_raw_from_smoothing() -> None:
    points = [{"x_tokens": i * 100, "value": 3.0 - i * 0.1} for i in range(8)]
    svg = curve_svg(points, "x_tokens", "value")
    assert "trailing-average (smoothing)" in svg
    assert "raw points" in svg
    assert svg.count("<circle") == 8
    assert curve_svg([{"x_tokens": 1, "value": 2.0}], "x_tokens", "value").startswith("<p")


def test_html_pages_are_self_contained_and_portable(tmp_path: Path) -> None:
    payload = collect_run_report("run_demo_1", _write_run(tmp_path)).to_dict()
    for page in (
        to_html_run(payload),
        to_html_campaign(collect_campaign_report({}).to_dict()),
        to_html_data(collect_data_report({}).to_dict()),
    ):
        assert "<script src" not in page and "<link" not in page
        assert "<style>" in page and page.startswith("<!doctype html>")


def test_campaign_and_data_markdown_render() -> None:
    campaign = collect_campaign_report(
        {
            "campaign_id": "c",
            "trials": [
                {
                    "trial_id": "t1",
                    "model_preset": "50m",
                    "mixture_preset": "m",
                    "budget_valid_targets": 10,
                }
            ],
            "total_trials": 1,
            "tokens_by_size": {},
            "total_valid_targets": 10,
        }
    ).to_dict()
    assert "plan only" in to_markdown_campaign(campaign)
    data = collect_data_report({"blocked": ["s1"]}).to_dict()
    assert "s1" in to_markdown_data(data)


def test_strip_protected_is_recursive() -> None:
    nested = {"a": [{"labels": [1], "text": "ok"}], "sealed_example": "x", "keep": 1}
    assert strip_protected(nested) == {"a": [{"text": "ok"}], "keep": 1}


# ------------------------------------------------------------------------ CLI


def _invoke(args: list[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Any:
    from typer.testing import CliRunner

    from xlm.cli.main import app

    monkeypatch.setenv("XLM_HOME", str(tmp_path / "home"))
    return CliRunner().invoke(app, args)


def test_cli_report_runs_in_all_formats(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runs_dir = _write_run(tmp_path)
    record_path = runs_dir / "run_demo_1" / "run_record.json"
    for fmt, marker in (
        ("json", '"run_demo_1"'),
        ("csv", "optimized_loss"),
        ("md", "## Losses"),
        ("html", "<!doctype html>"),
    ):
        result = _invoke(
            [
                "report",
                "--run-record",
                str(record_path),
                "--format",
                fmt,
                "--output",
                str(tmp_path / f"r.{fmt}"),
            ],
            monkeypatch,
            tmp_path,
        )
        assert result.exit_code == 0, result.output
        assert marker in (tmp_path / f"r.{fmt}").read_text(encoding="utf-8")


def test_cli_report_requires_exactly_one_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _invoke(["report", "--format", "json"], monkeypatch, tmp_path)
    assert result.exit_code == 1
    assert "exactly one" in result.output
    result = _invoke(["report", "--run", "x", "--format", "yaml"], monkeypatch, tmp_path)
    assert result.exit_code == 1


def test_cli_report_campaign_and_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    campaign = tmp_path / "camp.json"
    campaign.write_text(json.dumps({"campaign_id": "c", "trials": []}), encoding="utf-8")
    result = _invoke(
        [
            "report",
            "--campaign",
            str(campaign),
            "--format",
            "md",
            "--output",
            str(tmp_path / "c.md"),
        ],
        monkeypatch,
        tmp_path,
    )
    assert result.exit_code == 0, result.output
    assert "Campaign report: c" in (tmp_path / "c.md").read_text(encoding="utf-8")

    fragments = tmp_path / "frag.json"
    fragments.write_text(
        json.dumps({"admission": {"a": 1}, "previews": [{"doc_id": "d", "text": INJECTION}]}),
        encoding="utf-8",
    )
    result = _invoke(
        [
            "report",
            "--data-inputs",
            str(fragments),
            "--format",
            "html",
            "--output",
            str(tmp_path / "d.html"),
        ],
        monkeypatch,
        tmp_path,
    )
    assert result.exit_code == 0, result.output
    assert "<script>" not in (tmp_path / "d.html").read_text(encoding="utf-8")


def test_cli_runs_list_empty_and_populated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from test_queue import make_plan
    from xlm.artifacts.ledger import RunLedger
    from xlm.core.paths import ArtifactPaths
    from xlm.experiments.queue import ExperimentQueue

    empty = _invoke(["runs", "list"], monkeypatch, tmp_path)
    assert empty.exit_code == 0 and "No runs recorded" in empty.output

    # A current frozen plan: the queue reads execution identity, so the
    # fixture must be frozen (version 2 with an envelope), not a legacy
    # version-1 plan without one. Legacy refusal is covered separately by
    # test_submit_refuses_legacy_unfrozen_plan.
    plan, plan_path, snapshot_dir = make_plan(tmp_path, "runslist")
    paths = ArtifactPaths(root=tmp_path / "home")
    ledger = RunLedger(paths.ledger / "ledger.sqlite")
    queue = ExperimentQueue(ledger, paths, tree_root=tmp_path / "ws_runslist")
    job_id, _ = queue.submit(plan, plan_path, snapshot_dir, "cpu", authorization_token="t")
    listed = _invoke(["runs", "list"], monkeypatch, tmp_path)
    assert listed.exit_code == 0, listed.output
    assert job_id in listed.output
