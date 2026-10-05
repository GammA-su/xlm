"""count-tokens FAST path: exact reference equivalence, integrity refusals, progress.

Authored fixtures only: the generated C05 flow (planted excluded/duplicate records,
diagnostic_val and audit partitions; ``test_c06_tokenizer_fit.c05``) and a tiny BPE
fitted on its screened train records. No real corpus, proof, tokenizer, key or network.
"""

# ruff: noqa: F811  (pytest fixtures imported from test_c06_tokenizer_fit)

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import numpy as np
import psutil
import pytest
from scripts import c05_synthetic_flow as flow_module
from scripts.c05_authored_pilot import KEY
from scripts.c05_authored_pilot import doc as authored_doc
from scripts.c05_synthetic_flow import ISSUER, KEY_ENV

from count_workers import crash_task, ok_task
from test_c06_tokenizer_fit import c05, key  # noqa: F401 (fixtures)
from xlm.data.evidence_v2 import canonical
from xlm.data.exclusion import countfast
from xlm.data.exclusion.artifacts import ExecutionPlan
from xlm.data.exclusion.countfast import (
    ChunkTask,
    CountTables,
    KeptRows,
    _row,
    _verified_text,
    count_chunk,
    count_tables,
    count_tokens_fast,
    init_count_worker,
)
from xlm.data.exclusion.fitfast import open_streamed
from xlm.data.exclusion.fitscan import OrderedPool
from xlm.data.exclusion.inputs import read_metadata
from xlm.data.exclusion.operator import main as operator
from xlm.data.exclusion.policy import C05Error
from xlm.data.exclusion.progress import RunProgress
from xlm.data.exclusion.selection import (
    allocation_key,
    iter_plan_documents,
    load_tokenizer,
    valid_targets,
)
from xlm.data.exclusion.transport import open_gate

load = canonical.loads_bytes_strict
ARTIFACTS = ("counts.jsonl", "counts.json")


@pytest.fixture(scope="module")
def tok(c05: dict[str, Any], tmp_path_factory: pytest.TempPathFactory) -> Path:
    directory = tmp_path_factory.mktemp("count-tok") / "tokenizer"
    os.environ[KEY_ENV] = KEY
    flow_module.fit_tokenizer(c05["proof"], directory)
    return directory


def cli(command: str, c05: dict[str, Any], tok: Path, out: Path, *extra: str) -> list[str]:
    return [
        command,
        "--c05-proof",
        str(c05["proof"]),
        "--tokenizer",
        str(tok),
        "--scratch",
        str(out / "scratch"),
        "--output",
        str(out / "counts"),
        "--issuer",
        ISSUER,
        "--key-env",
        KEY_ENV,
        *extra,
    ]


@pytest.fixture(scope="module")
def reference(
    c05: dict[str, Any], tok: Path, tmp_path_factory: pytest.TempPathFactory
) -> dict[str, Any]:
    out = tmp_path_factory.mktemp("count-ref")
    os.environ[KEY_ENV] = KEY
    assert operator(cli("count-tokens-reference", c05, tok, out, "--no-progress")) == 0
    return {
        "dir": out / "counts",
        "bytes": {n: (out / "counts" / n).read_bytes() for n in ARTIFACTS},
    }


def fast(
    c05: dict[str, Any],
    tok: Path,
    out: Path,
    *,
    workers: int = 1,
    inline: bool = True,
    progress: Any = None,
    proof: Path | None = None,
) -> dict[str, Any]:
    return count_tokens_fast(
        proof or c05["proof"],
        tok,
        out / "counts",
        ISSUER,
        KEY.encode(),
        scratch=out / "scratch",
        workers=workers,
        inline=inline,
        progress=progress,
    )


def artifacts(directory: Path) -> dict[str, bytes]:
    return {name: (directory / name).read_bytes() for name in ARTIFACTS}


def assert_nothing_published(out: Path) -> None:
    assert not (out / "counts").exists()
    assert not list(out.glob("counts.partial-*"))
    scratch = out / "scratch"
    assert not scratch.exists() or not list(scratch.glob("count-tokenizer-*"))


@contextmanager
def changed(path: Path, data: bytes) -> Iterator[None]:
    saved = path.read_bytes()
    path.write_bytes(data)
    try:
        yield
    finally:
        path.write_bytes(saved)


def plan_files(c05: dict[str, Any]) -> list[Path]:
    spec = read_metadata(c05["proof"], digested=False)
    plan = ExecutionPlan.model_validate(read_metadata(Path(spec["plan"]), digested=False))
    return [Path(plan.data_root) / f.path for f in plan.files]


# -- exact equivalence ----------------------------------------------------------------------


@pytest.mark.parametrize("workers", [1, 2, 4, 8, 16])
def test_spawned_worker_counts_are_byte_identical_to_the_reference(
    c05: dict[str, Any],
    tok: Path,
    reference: dict[str, Any],
    tmp_path: Path,
    workers: int,
    capsys: pytest.CaptureFixture[str],
) -> None:
    capsys.readouterr()
    args = cli("count-tokens", c05, tok, tmp_path, "--workers", str(workers), "--no-progress")
    assert operator(args) == 0
    stdout = capsys.readouterr()
    result = json.loads(stdout.out)
    assert stdout.err == ""
    assert artifacts(tmp_path / "counts") == reference["bytes"]
    envelope = load(reference["bytes"]["counts.json"])
    assert result == {"digest": envelope["digest"], "mode": "authored"}
    assert sorted(p.name for p in (tmp_path / "counts").iterdir()) == sorted(ARTIFACTS)
    assert not list(tmp_path.glob("counts.partial-*"))
    assert list((tmp_path / "scratch").iterdir()) == []


@pytest.mark.parametrize("inline", [True, False])
def test_chunked_large_file_path_is_byte_identical(
    c05: dict[str, Any],
    tok: Path,
    reference: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    inline: bool,
) -> None:
    # Every file takes the scan-then-chunk path, with many small chunks.
    monkeypatch.setattr(countfast, "SPLIT_TEXT_BYTES", 0)
    monkeypatch.setattr(countfast, "CHUNK_SPAN_BYTES", 2048)
    submitted: list[str] = []
    original = OrderedPool.submit

    def spy(self: OrderedPool, function: Any, task: Any) -> Any:
        submitted.append(type(task).__name__)
        return original(self, function, task)

    monkeypatch.setattr(OrderedPool, "submit", spy)
    envelope = fast(c05, tok, tmp_path, workers=1 if inline else 4, inline=inline)
    assert artifacts(tmp_path / "counts") == reference["bytes"]
    assert envelope == load(reference["bytes"]["counts.json"])
    assert submitted.count("ChunkTask") > len(plan_files(c05))
    assert "FileTask" in submitted


def test_inline_mixed_path_is_byte_identical(
    c05: dict[str, Any],
    tok: Path,
    reference: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Some files whole-file counted, some chunked; small tokenizer batches.
    monkeypatch.setattr(countfast, "SPLIT_TEXT_BYTES", 6000)
    monkeypatch.setattr(countfast, "BATCH_TEXT_BYTES", 700)
    fast(c05, tok, tmp_path)
    assert artifacts(tmp_path / "counts") == reference["bytes"]


def test_counts_are_exact_and_only_kept_train_rows_appear(
    c05: dict[str, Any], tok: Path, reference: dict[str, Any]
) -> None:
    rows = [load(line) for line in reference["bytes"]["counts.jsonl"].splitlines()]
    body = load(reference["bytes"]["counts.json"])["payload"]
    tokenizer = load_tokenizer(tok)
    with open_gate(c05["proof"], allow_authored=True) as gate:
        assert gate is not None
        membership = {
            i: (d, s) for i, d, s in gate.db.execute("SELECT id,decision,split FROM membership")
        }
        expected: dict[str, dict[str, Any]] = {}
        all_ids = set()
        for item, doc in iter_plan_documents(gate):
            all_ids.add(doc.doc_id)
            if membership.get(doc.doc_id) == ("kept", "train"):
                expected[doc.doc_id] = {
                    "doc_id": doc.doc_id,
                    "content": canonical.digest(doc.to_dict()),
                    "allocation": [item.component, item.view, item.upstream_component],
                    "valid_targets": valid_targets(tokenizer, doc.text),
                }
    non_kept = all_ids - set(membership)
    non_train = {i for i, (_, s) in membership.items() if s != "train"}
    assert non_kept and non_train  # planted excluded/duplicate and diag/audit rows exist
    assert [r["doc_id"] for r in rows] == sorted(expected, key=lambda i: i.encode())
    assert rows == [expected[r["doc_id"]] for r in rows]
    assert not {r["doc_id"] for r in rows} & (non_kept | non_train)
    totals: dict[str, dict[str, int]] = {}
    for row in rows:
        bucket = totals.setdefault(
            allocation_key(*row["allocation"]), {"documents": 0, "valid_targets": 0}
        )
        bucket["documents"] += 1
        bucket["valid_targets"] += row["valid_targets"]
    assert body["allocations"] == dict(sorted(totals.items()))
    assert body["documents"] == len(rows)
    assert body["counts_sha256"] == hashlib.sha256(reference["bytes"]["counts.jsonl"]).hexdigest()


def test_native_count_api_equals_the_reference_rule(c05: dict[str, Any], tok: Path) -> None:
    tokenizer = load_tokenizer(tok)
    with open_gate(c05["proof"], allow_authored=True) as gate:
        assert gate is not None
        texts = [doc.text for _, doc in iter_plan_documents(gate)]
    texts += ["", " ", "<eos>", "<bos>x<eos>", "café\n\tnaïve 東京", "x" * 5000]
    assert tokenizer.count_valid_targets(texts) == [valid_targets(tokenizer, t) for t in texts]


@pytest.mark.parametrize(
    "doc_id",
    ["plain", 'with "quote" and \\ backslash', "tab\tnew\nline", "ctrl\x01\x1f", "é東京🚀", "/"],
)
def test_row_serializer_equals_canonical_bytes(doc_id: str) -> None:
    allocation = ["common_pile_prose", "common_pile_prose", "news"]
    for upstream in (allocation, ["finewiki_en", "en", None]):
        fragment = canonical.canonical_bytes(upstream)
        expected = canonical.canonical_bytes(
            {"doc_id": doc_id, "content": "ab" * 32, "allocation": upstream, "valid_targets": 7}
        )
        assert _row(fragment, doc_id, "ab" * 32, 7) == expected + b"\n"


# -- integrity refusals -------------------------------------------------------------------


def test_changed_appended_truncated_and_duplicated_source_refused(
    c05: dict[str, Any], tok: Path, tmp_path: Path
) -> None:
    path = max(plan_files(c05), key=lambda p: p.stat().st_size)
    raw = path.read_bytes()
    lines = raw.splitlines(keepends=True)
    flipped = bytearray(raw)
    flipped[len(raw) // 2] ^= 0x01
    for n, data in enumerate(
        (
            bytes(flipped),  # changed document bytes
            raw + b"\n",  # appended
            raw[:-1],  # truncated
            b"".join(lines[:-1]) + lines[0],  # same row count, a repeated doc id
        )
    ):
        with changed(path, data), pytest.raises(C05Error):
            fast(c05, tok, tmp_path / str(n))
        assert_nothing_published(tmp_path / str(n))


def test_changed_content_with_the_same_hash_layout_is_refused_by_the_row_check() -> None:
    document = authored_doc(1, "src", "original text").to_dict()
    line = canonical.canonical_bytes(document) + b"\n"
    digest = hashlib.sha256(canonical.canonical_bytes(document)).digest()
    doc_id = document["doc_id"].encode()
    assert _verified_text(line, doc_id, digest, 0) == "original text"
    assert _verified_text(line, doc_id, digest, 1) is None  # kept, not train
    # A valid record with the same id but other text (and consistent byte counts).
    other = canonical.canonical_bytes(authored_doc(1, "src", "changed text").to_dict()) + b"\n"
    with pytest.raises(C05Error, match="differs from C05 kept membership"):
        _verified_text(other, doc_id, digest, 0)
    with pytest.raises(C05Error, match="membership location"):
        _verified_text(line, b"another-id", digest, 0)
    held_out = {**document, "split": "diagnostic_val"}
    held_line = canonical.canonical_bytes(held_out) + b"\n"
    held_digest = hashlib.sha256(canonical.canonical_bytes(held_out)).digest()
    with pytest.raises(C05Error, match="split differs"):
        _verified_text(held_line, doc_id, held_digest, 0)
    with pytest.raises(C05Error, match="strict canonical JSON"):
        _verified_text(b'{"a":1,"a":2}\n', doc_id, digest, 0)


def test_chunk_refuses_a_file_changed_after_its_verified_pass(tmp_path: Path) -> None:
    path = tmp_path / "f.jsonl"
    document = authored_doc(1, "src", "chunk text").to_dict()
    line = canonical.canonical_bytes(document) + b"\n"
    path.write_bytes(line)
    task = ChunkTask(
        ordinal=0,
        first=0,
        path=str(path),
        file_bytes=len(line),
        mtime_ns=path.stat().st_mtime_ns + 1,
        offsets=np.zeros(1, dtype=np.uint64),
        lengths=np.asarray([len(line)], dtype=np.uint32),
        kept=KeptRows(
            [document["doc_id"].encode()],
            hashlib.sha256(canonical.canonical_bytes(document)).digest(),
            bytes([1]),
        ),
    )
    with pytest.raises(C05Error, match="changed after it was verified"):
        count_chunk(task)
    ok = ChunkTask(**{**task.__dict__, "mtime_ns": path.stat().st_mtime_ns})
    assert count_chunk(ok).tokens.tolist() == [-1]
    shifted = ChunkTask(**{**ok.__dict__, "lengths": np.asarray([len(line) - 1], np.uint32)})
    with pytest.raises(C05Error, match="line boundary"):
        count_chunk(shifted)


def test_wrong_or_changed_tokenizer_refused(
    c05: dict[str, Any], tok: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    other = tmp_path / "other-tokenizer"
    shutil.copytree(tok, other)
    binding = json.loads((other / "c05-binding.json").read_text(encoding="utf-8"))
    (other / "c05-binding.json").write_text(
        json.dumps({**binding, "completion_digest": "0" * 64}), encoding="utf-8"
    )
    with pytest.raises(C05Error, match="different C05 membership"):
        fast(c05, other, tmp_path / "a")
    assert_nothing_published(tmp_path / "a")
    # Changed after start: the original directory no longer equals the snapshot.
    changed_tok = tmp_path / "changing-tokenizer"
    shutil.copytree(tok, changed_tok)
    original = countfast.source_count

    def mutate(*args: Any, **kwargs: Any) -> Any:
        result = original(*args, **kwargs)
        with (changed_tok / "tokenizer_manifest.json").open("a", encoding="utf-8") as stream:
            stream.write(" ")
        return result

    monkeypatch.setattr(countfast, "source_count", mutate)
    with pytest.raises(C05Error, match="tokenizer changed after counting started"):
        fast(c05, changed_tok, tmp_path / "b")
    assert_nothing_published(tmp_path / "b")


def test_worker_with_a_different_tokenizer_refuses(c05: dict[str, Any], tok: Path) -> None:
    view = open_streamed(c05["proof"], allow_authored=True, consumes=[])
    tables, _ = count_tables(view)
    init_count_worker(CountTables(tables, str(tok), "f" * 64))
    with pytest.raises(C05Error, match="differs from its verified identity"):
        countfast._tokenizer()
    init_count_worker(CountTables(tables, str(tok), load_tokenizer(tok).fingerprint))
    assert countfast._tokenizer().fingerprint == load_tokenizer(tok).fingerprint


def test_wrong_proof_completion_and_membership_refused(
    c05: dict[str, Any], tok: Path, tmp_path: Path
) -> None:
    spec = read_metadata(c05["proof"], digested=False)
    for name, change in (
        ("plan", {"plan_digest": "1" * 64}),
        ("completion", {"completion_digest": "2" * 64}),
    ):
        proof = tmp_path / f"{name}.proof.json"
        canonical.write_canonical_json(proof, {**spec, **change})
        with pytest.raises(C05Error):
            fast(c05, tok, tmp_path / name, proof=proof)
        assert_nothing_published(tmp_path / name)
    membership = Path(spec["completion"]) / "membership.jsonl"
    raw = membership.read_bytes()
    with changed(membership, raw.replace(b'"train"', b'"audit"', 1)):
        with pytest.raises(C05Error, match="completion membership changed"):
            fast(c05, tok, tmp_path / "membership")
    assert_nothing_published(tmp_path / "membership")


def test_protected_volume_guard_refuses_before_any_work(
    c05: dict[str, Any], tok: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from xlm.data.exclusion import transport

    def mounted(*_: Any) -> None:
        raise C05Error("protected benchmark volume is mounted; detach it")

    monkeypatch.setattr(transport, "protected_guard", mounted)
    with pytest.raises(C05Error, match="mounted"):
        fast(c05, tok, tmp_path)
    assert not (tmp_path / "scratch").exists() and not (tmp_path / "counts").exists()


def test_write_once_and_root_overlaps_refused(
    c05: dict[str, Any], tok: Path, tmp_path: Path, reference: dict[str, Any]
) -> None:
    (tmp_path / "counts").mkdir()
    with pytest.raises(C05Error, match="write-once"):
        fast(c05, tok, tmp_path)
    spec = read_metadata(c05["proof"], digested=False)
    plan = ExecutionPlan.model_validate(read_metadata(Path(spec["plan"]), digested=False))
    for scratch, output in (
        (Path(plan.data_root) / "scratch", tmp_path / "o1"),
        (tmp_path / "s2", Path(spec["completion"]) / "counts"),
        (tmp_path / "s3", tok / "counts"),
        (tmp_path / "same", tmp_path / "same" / "counts"),
    ):
        with pytest.raises(C05Error, match="overlaps"):
            count_tokens_fast(
                c05["proof"], tok, output, ISSUER, KEY.encode(), scratch=scratch, workers=1
            )
        assert not output.exists() and not scratch.exists()
    with pytest.raises(C05Error, match="signer is not trusted"):
        count_tokens_fast(
            c05["proof"], tok, tmp_path / "o5", ISSUER, b"wrong", scratch=tmp_path / "s5", workers=1
        )
    with pytest.raises(C05Error, match="1, 2, 4, 8 or 16"):
        fast(c05, tok, tmp_path / "o6", workers=3)


def test_worker_failure_reaps_workers_and_removes_only_owned_files(
    c05: dict[str, Any], tok: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "scratch").mkdir()
    (tmp_path / "scratch" / "unrelated.bin").write_bytes(b"keep me")
    path = max(plan_files(c05), key=lambda p: p.stat().st_size)
    raw = path.read_bytes()
    flipped = bytearray(raw)
    flipped[-10] ^= 0x01
    before = {p.pid for p in psutil.Process().children(recursive=True)}
    capsys.readouterr()
    with changed(path, bytes(flipped)):
        args = cli("count-tokens", c05, tok, tmp_path, "--workers", "4", "--no-progress")
        assert operator(args) == 1
    refusal = json.loads(capsys.readouterr().out)
    assert refusal["refused"] is True and refusal["error_type"] == "C05Error"
    assert {p.pid for p in psutil.Process().children(recursive=True)} <= before
    assert_nothing_published(tmp_path)
    assert (tmp_path / "scratch" / "unrelated.bin").read_bytes() == b"keep me"


def test_abrupt_worker_exit_is_a_refusal() -> None:
    with pytest.raises(C05Error, match="terminated unexpectedly"):
        with OrderedPool(2, None, initializer=_no_init) as pool:
            futures = {pool.submit(ok_task, 1), pool.submit(crash_task, 3)}
            while futures:
                for future in pool.completed(futures):
                    futures.discard(future)


def _no_init(_: object) -> None:
    return None


def test_interruption_removes_staging_and_publishes_nothing(
    c05: dict[str, Any], tok: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = {"n": 0}
    original = countfast.count_file

    def interrupted(task: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 3:
            raise KeyboardInterrupt
        return original(task)

    monkeypatch.setattr(countfast, "count_file", interrupted)
    with pytest.raises(KeyboardInterrupt):
        fast(c05, tok, tmp_path)
    assert_nothing_published(tmp_path)
    # Interruption during export: the staged directory is removed, nothing published.
    monkeypatch.setattr(countfast, "count_file", original)

    def broken_export(*_: Any, **__: Any) -> Any:
        raise KeyboardInterrupt

    monkeypatch.setattr(countfast, "export_counts", broken_export)
    with pytest.raises(KeyboardInterrupt):
        fast(c05, tok, tmp_path / "export")
    assert_nothing_published(tmp_path / "export")


def test_reference_failure_removes_its_staging(
    c05: dict[str, Any], tok: Path, tmp_path: Path
) -> None:
    path = max(plan_files(c05), key=lambda p: p.stat().st_size)
    raw = path.read_bytes()
    with changed(path, raw + b"\n"):
        assert operator(cli("count-tokens-reference", c05, tok, tmp_path, "--no-progress")) == 1
    assert not (tmp_path / "counts").exists() and not list(tmp_path.glob("counts.partial-*"))


# -- progress -------------------------------------------------------------------------------

STAGES = (
    "PROOF VERIFY",
    "TOKENIZER VERIFY",
    "MEMBERSHIP VERIFY",
    "SOURCE COUNT",
    "AGGREGATE",
    "EXPORT COUNTS",
    "FSYNC",
    "VERIFY",
    "PUBLISH",
    "COMPLETE",
)


def _assert_content_free(c05: dict[str, Any], tok: Path, text: str) -> None:
    # No ids, digests or signatures (hex runs with a letter; numbers are measurements).
    assert not re.search(r"(?=[0-9]*[a-f])[0-9a-f]{16,}", text)
    assert not re.search(r"\bw[0-9a-f]{7}\b", text)  # no generated corpus words
    for path in (c05["root"], tok):
        assert str(path) not in text and Path(path).as_posix() not in text
    assert ":\\" not in text


def test_text_progress_is_staged_on_stderr_and_content_free(
    c05: dict[str, Any], tok: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    capsys.readouterr()
    args = cli("count-tokens", c05, tok, tmp_path, "--workers", "2", "--progress-interval", "0.001")
    assert operator(args) == 0
    captured = capsys.readouterr()
    result = json.loads(captured.out)  # stdout: exactly the one final JSON result
    assert set(result) == {"digest", "mode"} and captured.out.count("\n") == 1
    lines = captured.err.splitlines()
    assert lines and all(line.startswith("[COUNT] ") for line in lines)
    positions = [next(n for n, line in enumerate(lines) if f"] {s} |" in line) for s in STAGES]
    assert positions == sorted(positions)
    source = [line for line in lines if "] SOURCE COUNT |" in line]
    assert any("docs (" in line and "text " in line and "GiB input" in line for line in source)
    assert any("workers " in line and "busy" in line and "tasks " in line for line in source)
    stage_lines = [line for line in lines if not line.startswith("[COUNT] SLO |")]
    assert all("ETA " in line and "elapsed " in line for line in stage_lines)
    assert any("RSS " in line for line in lines)
    _assert_content_free(c05, tok, captured.err)


def test_jsonl_progress_and_no_progress(
    c05: dict[str, Any], tok: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    capsys.readouterr()
    args = cli("count-tokens", c05, tok, tmp_path / "j", "--progress-format", "jsonl")
    assert operator([*args, "--progress-interval", "0.001", "--workers", "1"]) == 0
    captured = capsys.readouterr()
    events = [json.loads(line) for line in captured.err.splitlines()]
    assert {e["event"] for e in events} >= {"stage", "progress", "finish", "complete"}
    staged = [e["stage"] for e in events if e["event"] == "stage"]
    assert [s for s in STAGES if s != "COMPLETE"] == staged
    source = [e for e in events if e.get("stage") == "SOURCE COUNT" and e["event"] == "finish"]
    assert source and source[-1]["work_done"] == source[-1]["work_total"] > 0
    assert source[-1]["done"] == source[-1]["total"] > 0
    _assert_content_free(c05, tok, captured.err)
    assert operator(cli("count-tokens", c05, tok, tmp_path / "n", "--no-progress")) == 0
    quiet = capsys.readouterr()
    assert quiet.err == "" and set(json.loads(quiet.out)) == {"digest", "mode"}


def test_eta_only_when_meaningful() -> None:
    now = [0.0]
    stream = io.StringIO()
    progress = RunProgress(interval=0.001, stream=stream, clock_fn=lambda: now[0], fmt="jsonl")
    progress.stage("FSYNC", None, "steps")
    progress.stage("SOURCE COUNT", 100, "docs", work_total=1000)
    for second in range(1, 31):
        now[0] = float(second)
        progress.update(second, work=second * 10)
    events = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert events[0]["stage"] == "FSYNC" and events[0]["eta_seconds"] is None
    source = [e for e in events if e["stage"] == "SOURCE COUNT" and e["event"] == "progress"]
    # No ETA before the minimum observation span; then from the rolling work rate.
    assert all(e["eta_seconds"] is None for e in source if e["stage_seconds"] < 10)
    last = source[-1]
    assert last["work_rolling_rate"] == pytest.approx(10.0)
    assert last["eta_seconds"] == pytest.approx((1000 - 300) / 10.0)


def test_backward_compatible_command_without_new_options(
    c05: dict[str, Any],
    tok: Path,
    tmp_path: Path,
    reference: dict[str, Any],
    capsys: pytest.CaptureFixture[str],
) -> None:
    capsys.readouterr()
    assert operator(cli("count-tokens", c05, tok, tmp_path)) == 0  # default workers 8
    assert json.loads(capsys.readouterr().out)["mode"] == "authored"
    assert artifacts(tmp_path / "counts") == reference["bytes"]
