"""Acceptance tests for the optional loopback dashboard (P19, A34).

The dashboard has no third-party dependencies: tests start a real server on an
ephemeral loopback port and fetch it with the standard library. Screenshot and
manual rendering checks are NOT RUN in this headless environment (recorded in
the milestone report); structural assertions below are their automated proxy.
"""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from xlm.dashboard.server import (
    DashboardConfigError,
    DashboardServer,
    build_data_provider,
    validate_bind_address,
)

INJECTION = "<script>alert('board')</script>"


def _reports() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    runs = {
        "run_ok": {
            "run_id": "run_ok",
            "status": "SUCCEEDED",
            "losses": {"optimized_loss": {"value": 1.5}},
            "benchmarks": {"tasks": {}, "coverage_note": "full"},
            "failures": [],
            "comparison": {},
            "curves": {},
            "missing": [],
        },
        "run_bad": {
            "run_id": "run_bad",
            "status": "FAILED",
            "losses": {},
            "benchmarks": {"tasks": {}, "coverage_note": "none"},
            "failures": [f"FAILED: boom {INJECTION}"],
            "comparison": {},
            "curves": {},
            "missing": ["everything"],
        },
    }
    campaigns = {
        "camp": {
            "campaign_id": "camp",
            "total_trials": 2,
            "total_valid_targets": 200,
            "cost_basis": "unmeasured",
            "trials": [],
            "blockers": [],
            "runs": {},
            "missing": [],
        }
    }
    data = {
        "admission": {"admitted": 1},
        "retention": {},
        "lineage": {},
        "quality": {},
        "drift": {},
        "storage": {},
        "blocked": [],
        "previews": [],
        "missing": [],
    }
    return runs, campaigns, data


def _fetch(server: DashboardServer, path: str, method: str = "GET") -> tuple[int, str]:
    request = urllib.request.Request(server.url + path, method=method)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8")


def test_dashboard_serves_runs_campaigns_and_data() -> None:
    runs, campaigns, data = _reports()
    server = DashboardServer(build_data_provider(runs, campaigns, data)).start()
    try:
        status, index = _fetch(server, "/")
        assert status == 200
        assert "run_ok" in index and "run_bad" in index and "camp" in index

        status, failed = _fetch(server, "/run?id=run_bad")
        assert status == 200
        assert "FAILED" in failed and "boom" in failed
        assert "<script>" not in failed and "&lt;script&gt;" in failed

        status, campaign = _fetch(server, "/campaign?id=camp")
        assert status == 200 and "camp" in campaign

        status, dataset = _fetch(server, "/data")
        assert status == 200 and "Dataset report" in dataset

        status, _ = _fetch(server, "/nope")
        assert status == 404
    finally:
        server.stop()


def test_dashboard_is_read_only() -> None:
    runs, campaigns, data = _reports()
    server = DashboardServer(build_data_provider(runs, campaigns, data)).start()
    try:
        for method in ("POST", "PUT", "DELETE", "PATCH"):
            status, body = _fetch(server, "/run?id=run_ok", method=method)
            assert status == 405
            assert "read-only" in body
    finally:
        server.stop()


def test_non_loopback_bind_requires_authenticated_deployment(tmp_path: Path) -> None:
    validate_bind_address("127.0.0.1", None)
    with pytest.raises(DashboardConfigError, match="without an explicit authenticated"):
        validate_bind_address("0.0.0.0", None)
    missing = tmp_path / "deploy.json"
    with pytest.raises(DashboardConfigError, match="not found"):
        validate_bind_address("0.0.0.0", missing)
    unauthenticated = tmp_path / "plain.json"
    unauthenticated.write_text(json.dumps({"note": "no auth"}), encoding="utf-8")
    with pytest.raises(DashboardConfigError, match="declare 'authentication'"):
        validate_bind_address("0.0.0.0", unauthenticated)
    authenticated = tmp_path / "auth.json"
    authenticated.write_text(
        json.dumps({"authentication": "operator-token-hash"}), encoding="utf-8"
    )
    validate_bind_address("0.0.0.0", authenticated)


def test_failed_runs_are_shown_not_hidden() -> None:
    runs, campaigns, data = _reports()
    server = DashboardServer(build_data_provider(runs, campaigns, data)).start()
    try:
        _, index = _fetch(server, "/")
        assert "run_bad" in index and "FAILED" in index
    finally:
        server.stop()


def test_dashboard_pages_carry_no_external_assets() -> None:
    runs, campaigns, data = _reports()
    provider = build_data_provider(runs, campaigns, data)
    routes = [
        ("/", {}),
        ("/run", {"id": ["run_ok"]}),
        ("/campaign", {"id": ["camp"]}),
        ("/data", {}),
    ]
    for path, query in routes:
        status, content_type, body = provider(path, query)
        assert status == 200
        assert "<script src" not in body and "<link" not in body
    # Screenshot and manual rendering checks are NOT RUN headless; the
    # structure above plus escaped content is the automated proxy.
