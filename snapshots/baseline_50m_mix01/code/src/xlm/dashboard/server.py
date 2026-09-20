"""Loopback-bound read-only dashboard reusing the report services (P19, A34).

One Python UI on the standard library: no extra dependencies, so headless
training can never break on a missing UI package. Every route is a read-only
GET over already-collected report data; anything else gets 405. Binding off
loopback requires an explicit authenticated deployment configuration file --
otherwise the server refuses to start. No telemetry is ever emitted.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

DASHBOARD_VERSION = "1"

LOOPBACK_HOSTS = ("127.0.0.1", "::1", "localhost")


class DashboardConfigError(RuntimeError):
    """Raised when dashboard binding or inputs are unsafe."""


def validate_bind_address(host: str, deployment_config: Path | str | None) -> None:
    """Allow loopback unconditionally; anything else needs an explicit config.

    The deployment config must exist and declare authentication; its presence
    alone is the operator's explicit opt-in to a non-loopback bind.
    """
    if host in LOOPBACK_HOSTS:
        return
    if deployment_config is None:
        raise DashboardConfigError(
            f"refusing non-loopback bind '{host}' without an explicit authenticated "
            "deployment configuration (--deployment-config)."
        )
    config_path = Path(deployment_config)
    if not config_path.is_file():
        raise DashboardConfigError(
            f"deployment configuration '{config_path}' not found; refusing bind '{host}'."
        )
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise DashboardConfigError(f"deployment configuration unreadable: {exc}") from exc
    if not isinstance(payload, dict) or not payload.get("authentication"):
        raise DashboardConfigError(
            f"deployment configuration must declare 'authentication'; refusing bind '{host}'."
        )


class DashboardHandler(BaseHTTPRequestHandler):
    """GET-only handler. The server object carries a `data_provider` callable."""

    server_version = f"XLM-Dashboard/{DASHBOARD_VERSION}"

    def _provider(self) -> Callable[[str, dict[str, list[str]]], tuple[int, str, str]]:
        provider: Callable[[str, dict[str, list[str]]], tuple[int, str, str]] | None = getattr(
            self.server, "data_provider", None
        )
        if provider is None:
            raise DashboardConfigError("dashboard has no data provider")
        return provider

    def _respond(self, status: int, body: str, content_type: str) -> None:
        encoded = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self) -> None:  # noqa: N802 (http.server naming)
        parsed = urlparse(self.path)
        try:
            status, content_type, body = self._provider()(parsed.path, parse_qs(parsed.query))
        except KeyError:
            self._respond(404, "unknown dashboard route", "text/plain; charset=utf-8")
            return
        except DashboardConfigError as exc:
            self._respond(400, f"dashboard error: {exc}", "text/plain; charset=utf-8")
            return
        self._respond(status, body, content_type)

    def _reject(self) -> None:
        self._respond(405, "dashboard is read-only", "text/plain; charset=utf-8")

    do_POST = _reject  # noqa: N802
    do_PUT = _reject  # noqa: N802
    do_DELETE = _reject  # noqa: N802
    do_PATCH = _reject  # noqa: N802

    def log_message(self, *args: Any) -> None:
        return


def build_data_provider(
    runs: dict[str, dict[str, Any]],
    campaigns: dict[str, dict[str, Any]] | None = None,
    data_report: dict[str, Any] | None = None,
) -> Callable[[str, dict[str, list[str]]], tuple[int, str, str]]:
    """Build a read-only provider over pre-collected report dicts (already escaped at render)."""
    from xlm.reports import render as render_mod

    campaigns = campaigns or {}

    def index() -> str:
        rows = "".join(
            f"<tr><td><a href='/run?id={run_id}'>{run_id}</a></td>"
            f"<td>{report.get('status', 'unknown')}</td></tr>"
            for run_id, report in sorted(runs.items())
        )
        campaign_rows = "".join(
            f"<tr><td><a href='/campaign?id={campaign_id}'>{campaign_id}</a></td>"
            f"<td>{report.get('total_trials', 'n/a')}</td></tr>"
            for campaign_id, report in sorted(campaigns.items())
        )
        return (
            "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
            f"<title>XLM dashboard</title><style>{render_mod.REPORT_CSS}</style>"
            "</head><body><h1>XLM dashboard (read-only)</h1>"
            "<h2>Runs</h2><table><tr><th>run</th><th>status</th></tr>"
            f"{rows}</table><h2>Campaigns</h2>"
            "<table><tr><th>campaign</th><th>trials</th></tr>"
            f"{campaign_rows}</table>"
            "<p><a href='/data'>Dataset report</a></p></body></html>"
        )

    def provide(path: str, query: dict[str, list[str]]) -> tuple[int, str, str]:
        if path in ("", "/"):
            return 200, "text/html; charset=utf-8", index()
        if path == "/run":
            run_id = (query.get("id") or [""])[0]
            report = runs.get(run_id)
            if report is None:
                raise KeyError(run_id)
            return 200, "text/html; charset=utf-8", render_mod.to_html_run(report)
        if path == "/campaign":
            campaign_id = (query.get("id") or [""])[0]
            report = campaigns.get(campaign_id)
            if report is None:
                raise KeyError(campaign_id)
            return 200, "text/html; charset=utf-8", render_mod.to_html_campaign(report)
        if path == "/data":
            if data_report is None:
                raise KeyError("data")
            return 200, "text/html; charset=utf-8", render_mod.to_html_data(data_report)
        raise KeyError(path)

    return provide


class DashboardServer:
    """A bound, loopback, read-only dashboard server."""

    def __init__(
        self,
        provider: Callable[[str, dict[str, list[str]]], tuple[int, str, str]],
        host: str = "127.0.0.1",
        port: int = 0,
        deployment_config: Path | str | None = None,
    ) -> None:
        validate_bind_address(host, deployment_config)
        self._server = ThreadingHTTPServer((host, port), DashboardHandler)
        self._server.data_provider = provider  # type: ignore[attr-defined]
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        host, port = self._server.server_address[:2]
        host_text = host.decode("utf-8") if isinstance(host, bytes) else str(host)
        return f"http://{host_text}:{int(port)}"

    def start(self) -> DashboardServer:
        self._thread = threading.Thread(
            target=self._server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
        )
        self._thread.start()
        return self

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
