"""Content-free live C05 progress: math, rate limiting, CLI streams and artifact isolation."""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest

from test_c05_engine import KEY, PROMPT, document, execute, membership, setup_run
from xlm.data.evidence_v2 import canonical
from xlm.data.exclusion.artifacts import authorize
from xlm.data.exclusion.policy import C05Error
from xlm.data.exclusion.progress import NullProgress, RollingRate, RunProgress, clock, render


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def reporter(**kwargs: Any) -> tuple[RunProgress, io.StringIO, FakeClock]:
    stream, fake = io.StringIO(), FakeClock()
    return RunProgress(stream=stream, clock_fn=fake, fmt="jsonl", **kwargs), stream, fake


def events(stream: io.StringIO) -> list[dict[str, Any]]:
    return [json.loads(line) for line in stream.getvalue().splitlines()]


def test_rolling_rate_uses_only_the_recent_window() -> None:
    rate = RollingRate(window=30, min_span=5)
    rate.add(0, 0)
    assert rate.rate() is None  # One sample: no rate.
    rate.add(4, 400)
    assert rate.rate() is None  # Below the minimum span.
    for second in range(5, 200):
        # Fast startup (100/s) then a slower steady state (10/s) after t=100.
        rate.add(second, 100 * min(second, 100) + 10 * max(0, second - 100))
    value = rate.rate()
    assert value is not None and abs(value - 10.0) < 0.5  # Startup no longer counts.


def test_eta_from_rolling_rate_and_unknown_denominators() -> None:
    progress, stream, fake = reporter(interval=1.0)
    progress.stage("SCAN", 1000, "docs")
    for second in range(1, 21):
        fake.now += 1
        progress.update(second * 10)
    snap = events(stream)[-1]
    assert snap["rolling_rate"] == pytest.approx(10.0)
    assert snap["eta_seconds"] == pytest.approx((1000 - 200) / 10.0)
    progress.stage("GROUP: NEAR", None, "docs")
    fake.now += 3
    progress.update(50, force=True)
    assert events(stream)[-1]["eta_seconds"] is None
    assert "ETA --:--:--" in render(events(stream)[-1], "progress")
    assert clock(None) == "--:--:--" and clock(3725) == "01:02:05"


def test_eta_unavailable_until_enough_samples() -> None:
    progress, stream, fake = reporter(interval=0.5, min_span=10)
    progress.stage("SCAN", 1000, "docs")
    for second in range(1, 9):
        fake.now += 1
        progress.update(second * 50, force=True)
    assert all(e["eta_seconds"] is None for e in events(stream)[1:])
    fake.now += 3
    progress.update(550, force=True)
    assert events(stream)[-1]["eta_seconds"] is not None


def test_interval_is_honored_and_transitions_are_forced() -> None:
    progress, stream, fake = reporter(interval=5.0)
    progress.stage("SCAN", 100, "docs")
    first = len(events(stream))
    for _ in range(40):
        fake.now += 0.1
        progress.update(1)
    assert len(events(stream)) == first  # 4 s since the stage line: still rate-limited.
    fake.now += 5
    progress.update(2)
    assert len(events(stream)) == first + 1
    progress.stage("GROUP: EXACT", 10, "docs")  # Forced: finish line + new stage line.
    kinds = [(e["event"], e["stage"]) for e in events(stream)[-2:]]
    assert kinds == [("finish", "SCAN"), ("stage", "GROUP: EXACT")]
    progress.complete()
    assert events(stream)[-1]["event"] == "complete"


def test_stage_reset_discards_previous_rate() -> None:
    progress, stream, fake = reporter(interval=1.0)
    progress.stage("SCAN", 10**6, "docs")
    for second in range(1, 40):
        fake.now += 1
        progress.update(second * 1000)
    progress.stage("GROUP: NEAR", 1000, "docs")
    fake.now += 1
    progress.update(1, force=True)
    assert events(stream)[-1]["rolling_rate"] is None


def test_only_numeric_fields_and_telemetry_are_accepted() -> None:
    progress, _stream, _fake = reporter()
    progress.stage("SCAN", 1, "docs")
    with pytest.raises(C05Error, match="numeric"):
        progress.update(1, label="document text")
    progress.attach(lambda: {"rss": "secret"})  # type: ignore[dict-item]
    with pytest.raises(C05Error, match="numeric"):
        progress.update(1, force=True)
    with pytest.raises(C05Error, match="interval"):
        RunProgress(interval=0)


def test_telemetry_sampling_is_rate_limited() -> None:
    calls = {"n": 0}

    def telemetry() -> dict[str, int]:
        calls["n"] += 1
        return {"rss": calls["n"]}

    progress, _stream, fake = reporter(interval=0.1, telemetry_interval=5.0)
    progress.attach(telemetry)
    progress.stage("SCAN", 100, "docs")
    for _ in range(100):
        fake.now += 0.2
        progress.update(1, force=True)
    assert calls["n"] == 5  # 20 simulated seconds / 5-second telemetry interval.


def test_text_line_has_scan_counters_without_content() -> None:
    progress, stream, fake = reporter(interval=1.0)
    progress.fmt = "text"
    progress.stage("SCAN", 15_097_174, "docs")
    fake.now += 30
    progress.update(
        4_812_391,
        committed=4_600_210,
        files_committed=621,
        files_total=2035,
        bytes_done=5 * 1024**3,
        bytes_total=97 * 1024**3,
        workers=16,
        busy=15,
        tasks=24,
        capacity=32,
        results=11,
        mib_per_s=54.1,
    )
    line = stream.getvalue().splitlines()[-1]
    assert line.startswith("[C05] SCAN | 4,812,391/15,097,174 docs (31.88%)")
    for part in (
        "committed 4,600,210 docs",
        "files 621/2,035",
        "workers 15/16 busy",
        "tasks 24/32 results 11",
        "54.1 MiB/s",
        "ETA",
    ):
        assert part in line, part


def test_null_progress_is_silent() -> None:
    progress = NullProgress()
    progress.stage("SCAN", 1)
    progress.update(1, force=True, anything=1)
    progress.complete()


# --- engine and CLI integration ------------------------------------------------------


def test_progress_never_changes_artifacts_and_covers_every_stage(tmp_path: Path) -> None:
    docs = [
        document("doc-zebra-alpha", PROMPT),
        document("doc-zebra-beta", "A quiet lighthouse keeper's notebook."),
    ]
    quiet, quiet_index, quiet_receipt = setup_run(tmp_path / "quiet", docs)
    execute(quiet, quiet_index, quiet_receipt)
    loud, loud_index, loud_receipt = setup_run(tmp_path / "loud", docs)
    stream = io.StringIO()
    execute(loud, loud_index, loud_receipt, progress=RunProgress(stream=stream, fmt="jsonl"))
    assert membership(quiet) == membership(loud)
    quiet_out = Path(quiet.output_root) / quiet.identity() / "membership.jsonl"
    loud_out = Path(loud.output_root) / loud.identity() / "membership.jsonl"
    assert quiet_out.read_bytes() == loud_out.read_bytes()
    stages = {e["stage"] for e in events(stream)}
    for expected in (
        "PREFLIGHT",
        "INDEX VERIFY",
        "SCAN",
        "GROUP: PREPARE",
        "GROUP: EXACT",
        "GROUP: BAND INDEX BUILD",
        "GROUP: NEAR",
        "GROUP: LINEAGE INDEX BUILD",
        "GROUP: LINEAGE",
        "GROUP: PARENTS",
        "GROUP: FAMILY PROPAGATION",
        "GROUP: PATH COMPRESSION",
        "GROUP: SURVIVORS",
        "GROUP: FAMILY BUILD",
        "GROUP: SPLITS",
        "GROUP: DIGEST",
        "PUBLISH: MEMBERSHIP",
        "PUBLISH: COMPLETION",
        "COMPLETE",
    ):
        assert expected in stages, expected
    assert any(s.startswith("MATCHER COMPILE: ") for s in stages)
    assert any(s.startswith("MATCHER VERIFY: ") for s in stages)
    raw = stream.getvalue().lower()
    for secret in ("lighthouse", "notebook", "copper", "summer", "zebra", "authored-index"):
        assert secret not in raw, secret
    scan = [e for e in events(stream) if e["stage"] == "SCAN"][-1]
    assert scan["total"] == 2 and scan["done"] == 2
    assert scan["fields"]["committed"] == 2 and scan["fields"]["files_committed"] == 2


def cli_layout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    from xlm.data.exclusion import control

    plan, index, receipt = setup_run(
        tmp_path, [document("a", PROMPT), document("b", "A lantern maker's quiet ledger.")]
    )
    monkeypatch.setattr(
        control,
        "implementation_identity",
        lambda: {"code_commit": "3" * 40, "code_identity": "4" * 64, "dependency_sha256": "5" * 64},
    )
    monkeypatch.setenv("C05_PROGRESS_TEST_KEY", KEY.decode())
    canonical.write_canonical_json(tmp_path / "plan.json", plan.model_dump(mode="json"))
    canonical.write_canonical_json(tmp_path / "trust.json", {"fixture": "C05_PROGRESS_TEST_KEY"})
    canonical.write_canonical_json(tmp_path / "auth.json", authorize(plan, "fixture", KEY))
    canonical.write_canonical_json(tmp_path / "benchmark.json", receipt)
    return [
        "--plan",
        str(tmp_path / "plan.json"),
        "--trust",
        str(tmp_path / "trust.json"),
        "--issuer",
        "fixture",
        "--key-env",
        "C05_PROGRESS_TEST_KEY",
        "--authorization",
        str(tmp_path / "auth.json"),
        "--benchmark-receipt",
        str(tmp_path / "benchmark.json"),
        "--index",
        str(index),
    ]


@pytest.mark.parametrize("command", ["run", "resume"])
def test_cli_progress_defaults_to_stderr_and_keeps_stdout_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    command: str,
) -> None:
    from xlm.data.exclusion.control import main

    arguments = cli_layout(tmp_path, monkeypatch)
    assert main([command, *arguments, "--progress-interval", "0.5"]) == 0
    captured = capsys.readouterr()
    lines = captured.out.splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["verified"] is True
    assert "[C05] SCAN" in captured.err and "[C05] COMPLETE" in captured.err
    assert "lantern" not in captured.err.lower() and "copper" not in captured.err.lower()


def test_cli_no_progress_is_silent_and_jsonl_is_machine_readable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from xlm.data.exclusion.control import main

    arguments = cli_layout(tmp_path / "quiet", monkeypatch)
    assert main(["run", *arguments, "--no-progress"]) == 0
    captured = capsys.readouterr()
    assert captured.err == "" and len(captured.out.splitlines()) == 1
    arguments = cli_layout(tmp_path / "jsonl", monkeypatch)
    assert main(["run", *arguments, "--progress-format", "jsonl"]) == 0
    captured = capsys.readouterr()
    parsed = [json.loads(line) for line in captured.err.splitlines()]
    assert parsed and {"stage", "done", "total", "eta_seconds"} <= set(parsed[0])
    assert main(["run", *arguments, "--progress-interval", "0"]) == 1  # Refused, typed only.


def test_no_worker_override_on_the_production_cli(tmp_path: Path) -> None:
    from xlm.data.exclusion.control import parser

    with pytest.raises(SystemExit):
        parser().parse_args(["run", "--workers", "4"])
