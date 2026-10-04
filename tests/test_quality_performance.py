"""Quality-audit performance work: exact equivalence, measured parallelism, progress,
progress log, read-only status, benchmark helpers (authored data only, offline).

Every optimized detector path is compared with the frozen literal implementation of
accepted commit c517fe0 (``quality_reference_detectors``); the strict JSON loader with
``canonical.loads_bytes_strict``; the membership parser with numpy scalar assignment.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import random
import subprocess
import sys
import time
import types
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np
import pytest

import quality_reference_detectors as reference
from quality_fixtures import CANARIES, TEXTS, build_corpus, standard_layout
from xlm.data.evidence_v2 import canonical
from xlm.data.quality import bench, detectors, progress, scan, status
from xlm.data.quality.cli import main
from xlm.data.quality.overlay import IDENTITY, MembershipParser, OverlayError, doc_digest
from xlm.data.quality.policy import RUN_MIN
from xlm.data.quality.report import ARTIFACTS
from xlm.data.quality.runner import Limits, check_progress_log, run_audit
from xlm.data.quality.scan import OrderedPool, QualityError
from xlm.data.quality.strictjson import loads_strict_bytes

REPO = Path(__file__).resolve().parents[1]


def limits(workers: int = 1) -> Limits:
    return Limits(
        workers=workers,
        max_rss_bytes=8 * 1024**3,
        free_reserve_bytes=0,
        max_output_bytes=256 * 1024**2,
        line_ceiling=1024**2,
        deadline_seconds=600.0,
    )


def artifacts(output: Path) -> dict[str, bytes]:
    return {name: (output / name).read_bytes() for name in ARTIFACTS}


@pytest.fixture
def corpus(tmp_path: Path) -> Path:
    return build_corpus(tmp_path / "corpus", standard_layout())


@pytest.fixture(scope="module")
def bench_corpus(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """~3 MiB of the benchmark's authored mix in variable-size files."""
    root = tmp_path_factory.mktemp("bench-corpus")
    return bench.generate(root, 3 * 1024**2, [256 * 1024, 768 * 1024, 1024**2, 1024**2])


# -- exact detector equivalence -------------------------------------------------------------


def same_analysis(a: Any, b: Any) -> bool:
    if a.flags != b.flags or a.doc_class != b.doc_class or len(a.values) != len(b.values):
        return False
    for x, y in zip(a.values, b.values, strict=True):
        if x is None or y is None:
            if x is not y:
                return False
        elif type(x) is not type(y):
            return False
        elif not (x == y or (isinstance(x, float) and math.isnan(x) and math.isnan(y))):
            return False
    return True


ALPHABET = (
    "a", "b", "z", "Q", ",", ";", ".", "\n", "\n", "\n\n", " \n \n", " ", "  ", "\t",
    "\r", "\x0b", "\x0c", "\x1c", "\x85", "\xa0", " ", "　", "12", "iv", "XI",
    "Page 3 of 9", "- 4 -", "p. 7", "-\n", "İ", "ı", "K", "ﬀ", "€", "日本", "Ã©",
    "â€™", "aaaaaaaa", "--------", "        ", "<b>x</b>", "&amp;", "```\ncode\n```",
    "http://x.org", "www.", "\\frac", "\ud800", "\U0001f600", "​", "﻿",
    "the", "and", "cookies", "Privacy Policy", "|a|b|", "\x00",
)  # fmt: skip


def adversarial_texts(count: int, seed: int) -> list[str]:
    rng = random.Random(seed)
    return ["".join(rng.choice(ALPHABET) for _ in range(rng.randint(0, 80))) for _ in range(count)]


def ngram_texts() -> list[str]:
    rng = random.Random(11)
    out = []
    for n in (0, 1, 4, 5, 9, 10, 11, 47, 48, 49, 50, 300, 6208, 6209, 7000):
        for vocab in (1, 2, 7, max(n, 1)):
            out.append(" ".join(f"w{rng.randrange(vocab)}" for _ in range(n)))
    return out


def authored_documents(count: int) -> list[str]:
    rng = np.random.default_rng(5)
    words = bench._vocabulary(rng)
    sentences = bench._sentences(rng, words, 400)
    kinds = [k for k, _ in bench.KINDS]
    return [
        bench._document(kinds[i % len(kinds)], rng, sentences, int(rng.integers(20, 9000)))
        for i in range(count)
    ]


def test_analyze_equals_the_frozen_reference_implementation() -> None:
    texts = [
        *TEXTS.values(),
        *adversarial_texts(4000, 3),
        *ngram_texts(),
        *authored_documents(160),
        "x" * 100_001,
        " ".join(["same"] * 100_005),  # past NGRAM_MAX_WORDS: truncated analysis
    ]
    for text in texts:
        nbytes = len(text.encode("utf-8", "surrogatepass"))
        outcomes = []
        for implementation in (reference, detectors):
            try:
                outcomes.append(implementation.analyze(text, nbytes))
            except UnicodeEncodeError as exc:  # lone surrogates (refused before analysis)
                outcomes.append(type(exc))
        expected, actual = outcomes
        if isinstance(expected, type) or isinstance(actual, type):
            assert expected is actual, repr(text[:60])
        else:
            assert same_analysis(expected, actual), repr(text[:60])


def test_whitespace_is_never_printable_except_the_ascii_space() -> None:
    # The property ``detectors.collapse`` relies on, over every code point.
    offenders = [
        c for c in range(0x110000) if chr(c).isspace() and chr(c).isprintable() and c != 0x20
    ]
    assert offenders == []
    for text in adversarial_texts(2000, 9):
        stripped = text.strip()
        assert detectors.collapse(stripped) == " ".join(stripped.split())


def test_line_level_layout_counts_equal_the_regex_counts() -> None:
    for text in [*adversarial_texts(3000, 5), *TEXTS.values()]:
        lines = text.split("\n")
        expected_pages = len(detectors.PAGE_NUMBER_RE.findall(text))
        expected_soft = len(detectors.SOFT_BREAK_RE.findall(text))
        assert detectors.page_number_lines(lines) == expected_pages
        assert detectors.soft_breaks(lines) == expected_soft
        if lines and lines[-1] == "":
            assert detectors.page_number_lines(lines[:-1]) == expected_pages
            assert detectors.soft_breaks(lines[:-1]) == expected_soft
    # Overlapping candidates: "a\nb\nc" matches once, "ab\ncd\nef" twice.
    assert detectors.soft_breaks("a\nb\nc".split("\n")) == 1
    assert detectors.soft_breaks("ab\ncd\nef".split("\n")) == 2


def test_packed_ngram_counts_equal_tuple_counting() -> None:
    for text in ngram_texts():
        words = text.split()
        five = len(set(zip(*(words[k:] for k in range(5)), strict=False)))
        ten = Counter(zip(*(words[k:] for k in range(10)), strict=False))
        expected = (
            len(set(words)),
            five if len(words) >= 5 else 0,
            len(ten) if len(words) >= 10 else 0,
            max(ten.values()) if len(words) >= 10 else 0,
        )
        assert detectors.ngram_counts(words) == expected


def test_run_probe_agrees_with_maximal_runs() -> None:
    rng = np.random.default_rng(2)
    for size in (0, 1, 7, 8, 9, 50, 400):
        for alphabet in (1, 2, 3, 40):
            codes = rng.integers(0, alphabet, size=size).astype("<u4")
            expected = int(detectors.maximal_runs(codes, RUN_MIN)[0].shape[0]) > 0
            assert detectors.has_run(codes, RUN_MIN) is expected


# -- strict JSON and the membership parser ------------------------------------------------------


STRICT_CASES = (
    b'{"a":1.5,"b":[1,2,{"c":-0.0}],"d":"x"}',
    b'{"a":1e999}',
    b'{"a":[-1e999]}',
    b'{"a":{"b":NaN}}',
    b'{"a":Infinity}',
    b'{"a":-Infinity}',
    b'{"a":1,"a":2}',
    b'{"a":{"b":1,"b":1}}',
    b"\xef\xbb\xbf{}",
    b'{"a":"\xff"}',
    b'{"a":"\\ud800"}',
    b'{"a":1} x',
    b"[1e308, 2.5e-320, 123456789012345678901234567890]",
    b'{"a":"\t"}',
    b"",
    b"   {}   ",
)


@pytest.mark.parametrize("raw", STRICT_CASES)
def test_strict_loader_accepts_and_refuses_exactly_like_canonical(raw: bytes) -> None:
    try:
        expected: Any = ("ok", canonical.loads_bytes_strict(raw))
    except ValueError:
        expected = ("refused",)
    try:
        actual: Any = ("ok", loads_strict_bytes(raw))
    except ValueError:
        actual = ("refused",)
    assert repr(actual) == repr(expected)


def membership_rows(count: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    files = {
        f"f{i}.jsonl": types.SimpleNamespace(
            documents=count, component=f"c{i}", view="v", upstream_component=None, source_id="s"
        )
        for i in range(3)
    }
    rng = random.Random(4)
    rows = []
    for n in range(count):
        path = f"f{n % 3}.jsonl"
        rows.append(
            {
                "bytes": rng.randrange(0, 2**40),
                "component": files[path].component,
                "content": hashlib.sha256(str(n).encode()).hexdigest(),
                "decision": "kept",
                "doc_id": f"id-{n:08d}-é",
                "duplicate_group": "g",
                "file": path,
                "lineage_group": "l",
                "quick": False,
                "row": n // 3 + 1,
                "source_id": "s",
                "split": ("train", "diagnostic_val", "audit")[n % 3],
                "upstream_component": None,
                "view": "v",
            }
        )
    return files, rows


def test_membership_parser_arrays_equal_numpy_scalar_assignment() -> None:
    files, rows = membership_rows(300)
    documents = {p: f.documents for p, f in files.items()}
    parser = MembershipParser(files, documents, len(rows))
    for row in rows:
        parser.stage(canonical.canonical_bytes(row))
    kept, identity = parser.arrays()
    for path, count in documents.items():
        want_kept = np.zeros(count, dtype=np.bool_)
        want = np.zeros(count, dtype=IDENTITY)
        for row in rows:
            if row["file"] == path:
                index = row["row"] - 1
                want_kept[index] = True
                want["doc"][index] = np.frombuffer(doc_digest(row["doc_id"]), dtype=np.uint8)
                want["content"][index] = np.frombuffer(
                    bytes.fromhex(row["content"]), dtype=np.uint8
                )
                want["bytes"][index] = row["bytes"]
        assert kept[path].dtype == np.bool_ and np.array_equal(kept[path], want_kept)
        assert identity[path].dtype == IDENTITY and identity[path].tobytes() == want.tobytes()
    assert parser.kept_by_split == {"train": 100, "diagnostic_val": 100, "audit": 100}


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda rows: rows.insert(1, dict(rows[0])), "ascending"),
        (
            lambda rows: rows[1].update(
                row=rows[0]["row"], file=rows[0]["file"], component=rows[0]["component"]
            ),
            "repeats",
        ),
        (lambda rows: rows[0].update(content="0" * 63), "schema"),
        (lambda rows: rows[0].update(row=10**6), "outside"),
        (lambda rows: rows[0].update(view="other"), "allocation"),
    ],
)
def test_membership_parser_refuses_like_before(change: Any, message: str) -> None:
    files, rows = membership_rows(9)
    change(rows)
    parser = MembershipParser(files, {p: f.documents for p, f in files.items()}, 100)
    with pytest.raises(OverlayError, match=message):
        for row in rows:
            parser.stage(canonical.canonical_bytes(row))


# -- measured parallelism and scheduling ------------------------------------------------------


def test_workers_really_execute_chunks_concurrently(
    bench_corpus: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(scan, "CHUNK_BYTES", 96 * 1024)  # ~30 chunk tasks
    result = run_audit(bench_corpus, tmp_path / "w4", limits=limits(4), progress_interval=None)
    activity = result["activity"]
    assert activity["chunks"] >= 25
    assert activity["worker_processes_used"] >= 2  # distinct OS processes did the work
    assert activity["max_concurrent_tasks"] >= 2  # overlapping measured execution spans
    assert 0 < activity["worker_busy_fraction"] <= 1.0
    assert activity["worker_cpu_seconds"] > 0
    assert result["scan"]["peak_tasks_in_flight"] <= 8


def test_worker_counts_1_2_4_8_12_produce_identical_artifacts(
    bench_corpus: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(scan, "CHUNK_BYTES", 256 * 1024)
    seen = {}
    for workers in (1, 2, 4, 8, 12):
        output = tmp_path / f"w{workers}"
        result = run_audit(bench_corpus, output, limits=limits(workers), progress_interval=None)
        seen[workers] = (result["result_digest"], artifacts(output))
        assert result["activity"]["worker_processes_used"] <= workers
    assert len({digest for digest, _ in seen.values()}) == 1
    assert all(found == seen[1][1] for _, found in seen.values())


def slow_head(task: int) -> int:
    time.sleep(1.5 if task == 0 else 0.05)
    return task


def test_a_slow_head_task_does_not_stall_the_other_workers() -> None:
    telemetry = progress.Telemetry(2, time.monotonic())
    pool = OrderedPool(2, None, telemetry)
    completed_at_first_result = None
    with pool:
        out = []
        for value in pool.map(slow_head, range(12)):
            if completed_at_first_result is None:
                completed_at_first_result = telemetry.snapshot()["chunks_completed"]
            out.append(value)
    assert out == list(range(12))  # strict task order
    assert pool.peak_in_flight <= pool.limit == 4  # chunk-holding tasks stay bounded
    # The old pool could finish at most limit - 1 = 3 later tasks while the head ran;
    # now the second worker keeps going up to the result backlog (8 tasks in total).
    assert completed_at_first_result is not None and completed_at_first_result >= 5


def test_twelve_workers_is_an_accepted_operational_setting() -> None:
    from xlm.data.quality.envelope import validate_envelope

    envelope = limits(12).envelope()
    assert envelope["workers"] == 12 and envelope["queue_tasks"] == 24
    validate_envelope(envelope)


# -- progress -------------------------------------------------------------------------------


def snapshot(**changes: Any) -> dict[str, Any]:
    telemetry = progress.Telemetry(8, time.monotonic() - 470)
    telemetry.set_totals(
        {
            "files": 2035,
            "documents": 15_097_174,
            "file_bytes": 104_506_534_003,
            "canonical_bytes": 1,
        }
    )
    telemetry.set_phase("scan", 104_506_534_003, "bytes")
    with telemetry.lock:
        telemetry.files_done = 646
        telemetry.docs_done = 4_820_000
        telemetry.bytes_done = 33_100_000_000
        telemetry.bytes_scanned = 33_100_000_000
        telemetry.inflight = 16
        telemetry.chunks_completed = 3900
        telemetry.chunks_submitted = 3916
        telemetry.output_bytes = 220 * 1024**2
        for key, value in changes.items():
            setattr(telemetry, key, value)
    return telemetry.snapshot()


def test_progress_line_holds_the_operator_fields() -> None:
    snap = snapshot()
    usage = {"parent_cores": 0.9, "child_cores": 7.8, "active": 8, "children": 8}
    line = progress.format_line(
        snap, snap["totals"], 8, 470.0, (71.8e6, 70.2e6), usage, int(3.2 * 2**30)
    )
    for part in (
        "[quality-audit] scan 31.7%",
        "646/2035 files",
        "4.82M/15.10M docs",
        "33.1/104.5 GB",
        "71.8 MB/s now",
        "70.2 MB/s ewma",
        f"ETA {progress.duration((104_506_534_003 - 33_100_000_000) / 70.2e6)}",
        "elapsed 7m50s",
        "workers 8/8 active",
        "inflight 16",
        "chunks 3900/3916",
        "resumed 0 new 0",
        "CPU 0.9+7.8 cores",
        "RSS 3.20 GiB",
        "out 0.21 GiB",
    ):
        assert part in line


def test_rates_use_monotonic_windows_and_a_smooth_ewma() -> None:
    meter = progress.RateMeter(window=15.0, tau=30.0)
    assert meter.update(0.0, 0.0) == (None, None)
    for t in range(1, 11):  # startup: nothing measured yet
        _, ewma = meter.update(float(t), 0.0)
        assert ewma is None
    value = 0.0
    for t in range(11, 71):
        value += 70e6
        window, ewma = meter.update(float(t), value)
    assert window == pytest.approx(70e6) and ewma == pytest.approx(70e6)
    for t in range(71, 76):  # 5 s at twice the rate barely moves the ETA rate
        value += 140e6
        window, ewma = meter.update(float(t), value)
    assert ewma is not None and 70e6 < ewma < 90e6
    assert progress.duration(994.0) == "16m34s" and progress.duration(None) == "--"


def test_progress_interval_bounds() -> None:
    assert progress.check_interval(5.0) == 5.0
    for bad in (0.0, 0.1, float("nan"), float("inf"), 4000.0):
        with pytest.raises(progress.ProgressError):
            progress.check_interval(bad)


def run_cli(argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, Any, str]:
    code = main(argv)
    captured = capsys.readouterr()
    return code, json.loads(captured.out), captured.err


def audit_args(manifest: Path, output: Path, *extra: str) -> list[str]:
    return [
        "audit",
        "--manifest",
        str(manifest),
        "--output",
        str(output),
        "--workers",
        "2",
        "--max-rss-gib",
        "8",
        "--free-reserve-gib",
        "0",
        "--max-document-mib",
        "1",
        *extra,
    ]


def test_progress_enabled_goes_to_stderr_and_log_without_text(
    corpus: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    log = tmp_path / "logs" / "progress.log"
    log.parent.mkdir()
    output = tmp_path / "out"
    argv = audit_args(
        corpus, output, "--progress-interval-seconds", "0.2", "--progress-log", str(log)
    )
    code, result, err = run_cli(argv, capsys)
    assert code == 0 and result["complete"] is True  # stdout: exactly one JSON object
    lines = log.read_text(encoding="utf-8").splitlines()
    assert lines[0].startswith("[quality-audit] start pid ")
    assert lines[-1].startswith("[quality-audit] complete 100.0%") and lines[-1].endswith("ok")
    assert all(line.startswith("[quality-audit] ") for line in lines)
    assert "[quality-audit] complete" in err
    body = log.read_text(encoding="utf-8") + err
    for canary in CANARIES:
        assert canary not in body
    for private in (str(corpus.parent), str(output), "doc_id", "web-0"):
        assert private not in body
    assert not any(p.name == log.name for p in output.rglob("*"))
    # Progress is operational: identical scientific artifacts without it.
    quiet = tmp_path / "quiet"
    code, again, _ = run_cli(audit_args(corpus, quiet, "--no-progress"), capsys)
    assert code == 0 and again["result_digest"] == result["result_digest"]
    assert artifacts(quiet) == artifacts(output)
    assert set(result["phase_seconds"]) >= {"prepare", "scan", "aggregate", "write", "publish"}


def test_progress_disabled_writes_nothing_to_stderr_but_keeps_an_explicit_log(
    corpus: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, _, err = run_cli(audit_args(corpus, tmp_path / "a", "--no-progress"), capsys)
    assert code == 0 and "[quality-audit]" not in err
    log = tmp_path / "p.log"
    argv = audit_args(corpus, tmp_path / "b", "--no-progress", "--progress-log", str(log))
    code, _, err = run_cli(argv, capsys)
    assert code == 0 and "[quality-audit]" not in err
    assert log.read_text(encoding="utf-8").count("[quality-audit]") >= 2


def test_progress_log_must_stay_outside_output_data_and_inputs(
    corpus: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output = tmp_path / "out"
    output.mkdir()
    data = corpus.parent / "data"
    for bad in (output / "p.log", data / "p.log", corpus, tmp_path / "missing" / "p.log"):
        with pytest.raises(QualityError):
            check_progress_log(bad, output, corpus, None, None)
    code, refusal, _ = run_cli(
        audit_args(corpus, tmp_path / "x", "--progress-log", str(data / "p.log")), capsys
    )
    assert code == 1 and refusal["refused"] is True
    assert not (data / "p.log").exists() and not (tmp_path / "x").exists()
    code, refusal, _ = run_cli(
        audit_args(corpus, tmp_path / "y", "--progress-interval-seconds", "0"), capsys
    )
    assert code == 1 and "progress-interval" in refusal["error"]


def test_resume_reports_the_resume_verification_phase(
    corpus: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from xlm.data.quality import runner

    output = tmp_path / "out"
    real = scan.commit_unit
    calls = {"n": 0}

    def flaky(*args: Any, **kwargs: Any) -> None:
        calls["n"] += 1
        if calls["n"] == 3:
            raise KeyboardInterrupt
        real(*args, **kwargs)

    monkeypatch.setattr(runner, "commit_unit", flaky)
    log = tmp_path / "p.log"
    with pytest.raises(KeyboardInterrupt):
        run_audit(corpus, output, limits=limits(1), progress_interval=None, progress_log=log)
    assert log.read_text(encoding="utf-8").splitlines()[-1].endswith("stopped")
    monkeypatch.setattr(runner, "commit_unit", real)
    result = run_audit(corpus, output, limits=limits(1), progress_interval=None, progress_log=log)
    assert (result["files_resumed"], result["files_scanned"]) == (2, 2)
    assert result["phase_seconds"]["resume-verify"] >= 0
    lines = log.read_text(encoding="utf-8").splitlines()
    assert lines[-1].startswith("[quality-audit] complete 100.0% | 4/4 files")


# -- status ---------------------------------------------------------------------------------


def tree_state(root: Path) -> dict[str, tuple[int, int, str]]:
    return {
        p.relative_to(root).as_posix(): (
            p.stat().st_size,
            p.stat().st_mtime_ns,
            hashlib.sha256(p.read_bytes()).hexdigest(),
        )
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def test_status_is_read_only_and_derives_committed_progress(
    corpus: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    from xlm.data.quality import runner

    output = tmp_path / "out"
    real = scan.commit_unit
    calls = {"n": 0}

    def flaky(*args: Any, **kwargs: Any) -> None:
        calls["n"] += 1
        if calls["n"] == 3:
            raise KeyboardInterrupt
        real(*args, **kwargs)

    monkeypatch.setattr(runner, "commit_unit", flaky)
    with pytest.raises(KeyboardInterrupt):
        run_audit(corpus, output, limits=limits(1), progress_interval=None)
    before = tree_state(output)
    data_before = tree_state(corpus.parent)
    partial = status.audit_status(corpus, output)
    assert partial["state"] == "INCOMPLETE" and partial["files_complete"] == 2
    assert partial["binding"] == "matches-manifest" and partial["process"] is None
    manifest = scan.load_manifest(corpus)
    assert partial["file_bytes_complete"] == sum(f.file_bytes for f in manifest.files[:2])
    code = main(["status", "--manifest", str(corpus), "--output", str(output), "--no-process"])
    assert code == 0 and json.loads(capsys.readouterr().out)["files_complete"] == 2
    monkeypatch.setattr(runner, "commit_unit", real)
    run_audit(corpus, output, limits=limits(1), progress_interval=None)
    before = tree_state(output)
    done = status.watch(lambda: status.audit_status(corpus, output), 0.2, 5.0)
    assert done["state"] == "COMPLETE" and done["percent_bytes"] == 100.0
    assert tree_state(output) == before  # status never modified the audit
    assert tree_state(corpus.parent) == data_before
    absent = status.audit_status(corpus, tmp_path / "nothing", processes=False)
    assert absent["output_exists"] is False and not (tmp_path / "nothing").exists()


def test_status_discovers_a_running_audit_process_tree(bench_corpus: Path, tmp_path: Path) -> None:
    output = tmp_path / "running"
    command = [
        sys.executable,
        "-m",
        "xlm.data.quality",
        *audit_args(bench_corpus, output, "--no-progress")[0:],
    ]
    command[command.index("--max-document-mib") + 1] = "64"
    process = subprocess.Popen(
        command, cwd=REPO, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=os.environ.copy()
    )
    found = None
    try:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline and process.poll() is None and found is None:
            state = status.audit_status(bench_corpus, output)
            found = state["process"]
    finally:
        out, _ = process.communicate(timeout=120)
    assert process.returncode == 0 and json.loads(out)["complete"] is True
    assert found is not None, "the running audit was never discovered"
    assert found["pid"] == process.pid
    assert found["process_tree_rss_bytes"] > 0 and found["elapsed_seconds"] >= 0


# -- benchmark helpers ------------------------------------------------------------------------


def test_benchmark_corpus_is_authored_valid_and_reproducible(tmp_path: Path) -> None:
    first = bench.generate(tmp_path / "a", 512 * 1024)
    second = bench.generate(tmp_path / "b", 512 * 1024)
    one = json.loads(first.read_bytes())["files"]
    two = json.loads(second.read_bytes())["files"]
    assert one == two and len(one) == bench.FILES  # the original script's layout
    full = bench.generate(tmp_path / "c", 600 * 1024, [100 * 1024, 200 * 1024, 300 * 1024])
    small = scan.load_manifest(bench.subset_manifest(full, 200 * 1024))
    total = sum(f.file_bytes for f in scan.load_manifest(full).files)
    assert 200 * 1024 <= sum(f.file_bytes for f in small.files) < total


def test_projection_arithmetic() -> None:
    projected = bench.projection(
        70.0,
        membership_rows_per_s=100_000.0,
        kept_rows=10_000_000,
        aggregate_seconds=20.0,
        startup_seconds=3.0,
    )
    assert projected["overlay_seconds"] == 100.0
    assert projected["fixed_seconds"] == 133.0
    likely = projected["likely"]
    assert likely["scan_seconds"] == pytest.approx(104_506_534_003 / 70e6, abs=0.1)
    assert projected["conservative"]["total_seconds"] > likely["total_seconds"]
    assert projected["optimistic"]["total_seconds"] < likely["total_seconds"]


def test_measure_run_reports_process_cpu_and_activity(bench_corpus: Path, tmp_path: Path) -> None:
    measured = bench.measure_run(bench_corpus, tmp_path / "m", 2)
    assert measured.worker_processes_used == 2 or measured.max_concurrent_tasks >= 1
    assert measured.child_cpu_seconds > 0 and measured.parent_cpu_seconds > 0
    assert measured.steady_mb_per_s >= measured.mb_per_s > 0
    assert measured.peak_in_flight <= measured.queue_tasks == 4
    assert bench.membership_rate(2000) > 0
