"""Repair-edge probes for the targeted recheck of 1883093 (I04, I08, I10, I11).

Authored fixtures only. The I11 probes re-sign an AUTHORED public membership with the
authored key of the synthetic C05 fixture; no real or protected C05 material is used.
"""

# ruff: noqa: F811  (pytest fixtures imported from other test modules)

from __future__ import annotations

import json
import math
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from quality_fixtures import build_corpus, standard_layout
from test_quality_audit import audit, c05_flow  # noqa: F401 (fixture)
from test_quality_hardening import flags_of, resign
from xlm.data.evidence_v2 import canonical
from xlm.data.exclusion import supervisor as supervisor_module
from xlm.data.quality import runner, scan
from xlm.data.quality.cli import main
from xlm.data.quality.envelope import EnvelopeError, validate_envelope
from xlm.data.quality.overlay import IDENTITY
from xlm.data.quality.runner import verify_report
from xlm.data.quality.scan import RECEIPT_FILE, QualityError

# -- I04 single authoritative final gate -----------------------------------------------------


@pytest.fixture
def corpus(tmp_path: Path) -> Path:
    return build_corpus(tmp_path / "corpus", standard_layout())


@pytest.fixture
def guards(monkeypatch: pytest.MonkeyPatch) -> list[runner.Guard]:
    seen: list[runner.Guard] = []
    original = runner.Guard

    class Recording(original):  # type: ignore[valid-type,misc]
        def __init__(self, **kwargs: Any) -> None:
            super().__init__(**kwargs)
            seen.append(self)

    monkeypatch.setattr(runner, "Guard", Recording)
    return seen


def assert_refused_without_receipt(corpus: Path, output: Path, match: str) -> None:
    with pytest.raises(QualityError, match=match):
        audit(corpus, output)
    assert not (output / RECEIPT_FILE).exists()


def test_i04_deadline_failure_during_finalization(
    corpus: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Only the final gate reads ``remaining``: the deadline expires exactly there.
    monkeypatch.setattr(supervisor_module.Supervisor, "remaining", lambda self: -1.0)
    assert_refused_without_receipt(corpus, tmp_path / "out", "deadline")


def test_i04_rss_failure_during_finalization(
    corpus: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(runner, "_tree_rss", lambda: 1 << 60)
    assert_refused_without_receipt(corpus, tmp_path / "out", "RSS")


def test_i04_fresh_free_space_check_at_finalization(
    corpus: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(runner, "_free_bytes", lambda _path: -1)
    assert_refused_without_receipt(corpus, tmp_path / "out", "free space")


def test_i04_disk_failure_recorded_during_shutdown(
    corpus: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = supervisor_module.Supervisor.__exit__

    def failing_shutdown(self: Any, kind: Any, *rest: Any) -> None:
        if kind is None:
            self.fail(supervisor_module.DISK_REASON)  # the monitor's last sample
        original(self, kind, *rest)

    monkeypatch.setattr(supervisor_module.Supervisor, "__exit__", failing_shutdown)
    assert_refused_without_receipt(corpus, tmp_path / "out", "free space")


def test_i04_monitor_failure_after_last_worker_result(
    corpus: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, guards: list[runner.Guard]
) -> None:
    real = scan.process_chunk

    def last_then_fail(task: scan.ChunkTask) -> Any:
        result = real(task)
        if task.last and task.ordinal == 3:
            guards[0].supervisor.fail(supervisor_module.RSS_REASON)
        return result

    monkeypatch.setattr(runner, "process_chunk", last_then_fail)
    assert_refused_without_receipt(corpus, tmp_path / "out", "RSS")


def test_i04_failure_immediately_before_complete_publication(
    corpus: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, guards: list[runner.Guard]
) -> None:
    real = canonical.write_atomic

    def fail_then_publish(path: Path, payload: bytes) -> None:
        if path.name == RECEIPT_FILE:
            guards[0].supervisor.fail(supervisor_module.DISK_REASON)
        real(path, payload)

    monkeypatch.setattr(canonical, "write_atomic", fail_then_publish)
    assert_refused_without_receipt(corpus, tmp_path / "out", "free space|refused")


def test_i04_cli_returns_no_success_json(
    corpus: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(runner, "_tree_rss", lambda: 1 << 60)
    output = tmp_path / "out"
    code = main(
        [
            "audit",
            "--manifest",
            str(corpus),
            "--output",
            str(output),
            "--workers",
            "1",
            "--no-progress",
            "--free-reserve-gib",
            "0",
        ]
    )
    printed = json.loads(capsys.readouterr().out)
    assert code == 1 and printed.get("refused") is True and "complete" not in printed
    assert not (output / RECEIPT_FILE).exists()


def test_i04_report_success_needs_the_final_gate(
    corpus: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "out"
    audit(corpus, output)
    monkeypatch.setattr(runner, "_tree_rss", lambda: 1 << 60)
    with pytest.raises(QualityError, match="RSS"):
        verify_report(corpus, output)


# -- I08 code/example context --------------------------------------------------------------

I08_CASES = {
    "inline_html": ("Use the `<html>` element as the root.", False),
    "inline_body": ("Put the page content in `<body>` tags.", False),
    "indented_snippet": (
        "Example:\n\n    <html>\n    <body>\n    <p>Hi</p>\n    </body>\n    </html>\n\nDone.",
        False,
    ),
    "fenced_html": (
        "```html\n<!DOCTYPE html><html><body>x</body></html>\n```",
        False,
    ),
    "fenced_xml": ("```xml\n<!DOCTYPE note>\n<note><to>Ada</to></note>\n```", False),
    "prose_mentioning_html": ("The <html> element is the root of every web page.", False),
    "real_page": (
        "<!DOCTYPE html>\n<html><head><title>t</title></head><body><p>x</p></body></html>",
        True,
    ),
    "real_page_without_doctype": (
        "<html><head></head><body><p>x</p></body></html>",
        True,
    ),
}


@pytest.mark.parametrize("name", sorted(I08_CASES))
def test_i08_example_context_is_never_full_html(name: str) -> None:
    text, full = I08_CASES[name]
    flags = flags_of(text)
    assert ("markup_full_html" in flags) is full
    if name.startswith(("inline", "indented", "fenced")):
        assert "markup_code_example" in flags
        assert "markup_light" not in flags


def test_i08_raw_example_tag_counts_are_kept() -> None:
    from xlm.data.quality.detectors import analyze
    from xlm.data.quality.policy import METRIC_INDEX

    text = I08_CASES["indented_snippet"][0]
    values = analyze(text, len(text)).values
    assert values[METRIC_INDEX["code_example_tags"]] == 6
    assert values[METRIC_INDEX["html_tags"]] == 0


# -- I10 strict operational envelope --------------------------------------------------------


@pytest.fixture(scope="module")
def completed(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[Path, Path]]:
    root = tmp_path_factory.mktemp("i10")
    corpus = build_corpus(root / "corpus", standard_layout())
    output = root / "out"
    audit(corpus, output, workers=2)
    yield corpus, output


ENVELOPE_CHANGES: dict[str, Any] = {
    "workers_zero": {"workers": 0},
    "workers_negative": {"workers": -2},
    "workers_unsupported": {"workers": 3},
    "workers_above_max": {"workers": 32, "queue_tasks": 64},
    "workers_bool": {"workers": True},
    "queue_below_workers": {"queue_tasks": 1},
    "queue_grossly_above": {"queue_tasks": 1000},
    "rss_zero": {"max_rss_bytes": 0},
    "rss_huge": {"max_rss_bytes": 1 << 60},
    "rss_string": {"max_rss_bytes": "8589934592"},
    "deadline_zero": {"deadline_seconds": 0.0},
    "deadline_negative": {"deadline_seconds": -1.0},
    "deadline_huge": {"deadline_seconds": 1e12},
    "deadline_int_type": {"deadline_seconds": 600},
    "reserve_negative": {"free_reserve_bytes": -1},
    "output_zero": {"max_output_bytes": 0},
    "document_zero": {"max_document_bytes": 0},
    "document_huge": {"max_document_bytes": 1 << 40},
    "chunk_zero": {"chunk_bytes": 0},
    "verify_threads_zero": {"verify_threads": 0},
    "pending_zero": {"max_pending_commits": 0},
    "pending_huge": {"max_pending_commits": 10**6},
    "interval_zero": {"supervisor_interval_seconds": 0.0},
    "margin_negative": {"publication_margin_seconds": -1.0},
    "margin_above_deadline": {"publication_margin_seconds": 1e9},
    "review_zero": {"review_per_stratum": 0},
    "review_huge": {"review_per_stratum": 10**6},
    "unknown_field": {"surprise": 1},
}


@pytest.mark.parametrize("where", ["envelope", "producer"])
@pytest.mark.parametrize("name", sorted([*ENVELOPE_CHANGES, "missing_field"]))
def test_i10_invalid_envelopes_refuse(
    completed: tuple[Path, Path], tmp_path: Path, name: str, where: str
) -> None:
    corpus, source = completed
    output = tmp_path / "out"
    shutil.copytree(source, output)
    path = output / RECEIPT_FILE
    body = json.loads(path.read_bytes())
    target = body["envelope"] if where == "envelope" else body["producer_envelopes"][0]
    if name == "missing_field":
        del target["workers"]
    else:
        target.update(ENVELOPE_CHANGES[name])
    body["digest"] = canonical.self_digest(body)
    path.write_bytes(canonical.canonical_bytes(body))
    with pytest.raises(QualityError, match="receipt invalid"):
        verify_report(corpus, output)


def test_i10_nonfinite_values_refuse(completed: tuple[Path, Path]) -> None:
    _, output = completed
    valid = json.loads((output / RECEIPT_FILE).read_bytes())["envelope"]
    validate_envelope(valid)
    for value in (math.nan, math.inf, -math.inf):
        with pytest.raises(EnvelopeError):
            validate_envelope({**valid, "deadline_seconds": value})


def test_i10_real_receipt_still_verifies(completed: tuple[Path, Path], tmp_path: Path) -> None:
    corpus, source = completed
    output = tmp_path / "out"
    shutil.copytree(source, output)
    assert verify_report(corpus, output)["verified"] is True


# -- I11 full content digest -----------------------------------------------------------------


def _flip_hex(char: str) -> str:
    return "0" if char != "0" else "1"


def _suffix_change(keep_hex: int) -> Any:
    def change(rows: list[dict[str, Any]]) -> None:
        digest = rows[0]["content"]
        rows[0]["content"] = digest[:keep_hex] + "".join(_flip_hex(c) for c in digest[keep_hex:])

    return change


def _last_bit(rows: list[dict[str, Any]]) -> None:
    raw = bytearray(bytes.fromhex(rows[0]["content"]))
    raw[-1] ^= 1
    rows[0]["content"] = raw.hex()


def _different(rows: list[dict[str, Any]]) -> None:
    rows[0]["content"] = "".join(_flip_hex(c) for c in rows[0]["content"])


@pytest.mark.parametrize(
    "change",
    [_suffix_change(2), _suffix_change(16), _suffix_change(32), _last_bit, _different],
    ids=["same_first_1_byte", "same_first_8_bytes", "same_first_16_bytes", "final_bit", "all"],
)
def test_i11_every_content_digest_mismatch_refuses(
    c05_flow: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: Any
) -> None:
    monkeypatch.setenv(c05_flow["key_env"], c05_flow["key"])
    proof = resign(c05_flow, tmp_path, change)
    with pytest.raises(QualityError, match="kept-row content differs"):
        audit(c05_flow["manifest"], tmp_path / "out", proof=proof, allow_authored_proof=True)


def test_i11_identity_holds_full_digests() -> None:
    assert IDENTITY["doc"].shape == (32,) and IDENTITY["content"].shape == (32,)
