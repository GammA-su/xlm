"""C06 FAST hardening regressions for the independent-audit findings A1-A8.

Authored/generated fixtures only. Every refusal must leave no published output and
no owned staging/scratch; scientific outputs stay byte-identical to bb886bd.
"""

# ruff: noqa: F811  (pytest fixtures imported from test_c06_tokenizer_fit / test_c06_fast)

from __future__ import annotations

import hashlib
import io
import json
import shutil
import struct
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import psutil
import pytest
from scripts.c05_authored_pilot import KEY
from scripts.c05_synthetic_flow import ISSUER, KEY_ENV

from c06_hardening_workers import sleep_task, spawn_stubborn_grandchild
from test_c06_fast import assert_equivalent, envelope_for, fast_fit
from test_c06_tokenizer_fit import c05, key, write_policy  # noqa: F401 (fixtures)
from test_c06_tokenizer_fit import fit as reference_fit
from xlm.data.evidence_v2 import canonical
from xlm.data.exclusion import fitfast, fitscan, keptindex, supervisor
from xlm.data.exclusion.artifacts import signed
from xlm.data.exclusion.control import main
from xlm.data.exclusion.fitfast import KEPT_INDEX_DIR, open_streamed
from xlm.data.exclusion.keptindex import MANIFEST, open_index
from xlm.data.exclusion.policy import C05Error
from xlm.data.exclusion.progress import NullProgress, RunProgress
from xlm.data.exclusion.supervisor import Deadline, Projection, Supervisor, tree_rss
from xlm.data.exclusion.tokenizer_fit import FIT_SAMPLE, load_fit_policy
from xlm.tokenizers.bpe import FitSampleBoundError, train_spool

load = canonical.loads_bytes_strict
MiB = 1024**2


@pytest.fixture(scope="module")
def reference(c05: dict[str, Any], tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    out = tmp_path_factory.mktemp("hardref")
    policy = write_policy(out / "policy.yaml", c05)
    reference_fit(c05, policy, out)
    return {"out": out, "policy": policy, "fit": out / "fit"}


@pytest.fixture(scope="module")
def fast(
    c05: dict[str, Any], reference: dict[str, Any], tmp_path_factory: pytest.TempPathFactory
) -> dict[str, Any]:
    out = tmp_path_factory.mktemp("hardfast")
    envelope = fast_fit(c05, reference["policy"], out, workers=2)
    return {"out": out, "fit": out / "fit", "envelope": envelope}


def nothing_published(out: Path) -> None:
    assert not (out / "fit").exists()
    assert not list(out.glob("fit.partial-*"))
    scratch = out / "scratch"
    assert not scratch.exists() or not list(scratch.glob("c06-fit-*"))


def tables() -> fitscan.MembershipTables:
    return fitscan.MembershipTables({}, (), 7, MiB, 4 * MiB, "t")


def cli_fit_args(c05: dict[str, Any], policy: Path, out: Path, *extra: str) -> list[str]:
    return [
        "fit-tokenizer",
        "--c05-proof",
        str(c05["proof"]),
        "--fit-shares",
        str(policy),
        "--quotas",
        str(c05["quotas"]),
        "--ifm-split",
        str(c05["ifm"]),
        "--scratch",
        str(out / "scratch"),
        "--output",
        str(out / "fit"),
        "--deficit-report",
        str(out / "deficit.json"),
        "--workers",
        "1",
        "--bpe-threads",
        "1",
        "--free-reserve-gib",
        "0",
        "--no-progress",
        *extra,
    ]


def plan_digest(capsys: pytest.CaptureFixture[str], args: list[str]) -> str:
    assert main([*args, "--plan-only"]) == 0
    return str(json.loads(capsys.readouterr().out)["resource_plan_digest"])


def signing(digest: str) -> list[str]:
    return ["--resource-plan-digest", digest, "--issuer", ISSUER, "--key-env", KEY_ENV]


# == A1: total deadline, supervised waits, bounded shutdown ===============================


def test_a1_cli_deadline_includes_the_proof_stage(
    c05: dict[str, Any],
    reference: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from xlm.data.exclusion import transport

    args = cli_fit_args(c05, reference["policy"], tmp_path, "--deadline-seconds", "1.5")
    digest = plan_digest(capsys, args)
    original = transport.guard_proof

    def slow_guard(*a: Any, **k: Any) -> None:
        time.sleep(2)
        original(*a, **k)

    monkeypatch.setattr(transport, "guard_proof", slow_guard)
    began = time.monotonic()
    code = main([*args, *signing(digest)])
    elapsed = time.monotonic() - began
    out = json.loads(capsys.readouterr().out)
    assert code == 1 and "deadline" in out["reason"]
    assert elapsed < 6
    nothing_published(tmp_path)


@pytest.mark.parametrize("seconds", [float("nan"), float("inf"), -1.0, 0.0])
def test_a1_deadline_must_be_finite_and_positive(seconds: float) -> None:
    with pytest.raises(C05Error):
        Deadline(seconds, time.monotonic())
    with pytest.raises(ValueError):
        envelope_for(deadline_seconds=seconds)


def test_a1_tiny_deadline_cannot_publish(
    c05: dict[str, Any], reference: dict[str, Any], tmp_path: Path
) -> None:
    with pytest.raises(C05Error, match="deadline"):
        fast_fit(c05, reference["policy"], tmp_path, deadline=0.01)
    nothing_published(tmp_path)


def test_a1_deadline_expiring_just_before_publication_refuses(
    c05: dict[str, Any], reference: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = {"n": 0}
    original = fitfast._verify_tokenizer

    def slow_second_verify(*args: Any, **kwargs: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 2:  # The pre-publication re-verification.
            time.sleep(3.5)
        return original(*args, **kwargs)

    monkeypatch.setattr(fitfast, "_verify_tokenizer", slow_second_verify)
    began = time.monotonic()
    first = fast_fit(c05, reference["policy"], tmp_path / "probe", deadline=1200)
    budget = time.monotonic() - began
    assert first["payload"]["fit_path"] == "c06-fast-v1"
    calls["n"] = 0
    with pytest.raises(C05Error, match="deadline"):
        fast_fit(c05, reference["policy"], tmp_path, deadline=budget + 1.0)
    nothing_published(tmp_path)


def test_a1_blocked_workers_are_killed_within_a_bound() -> None:
    began = time.monotonic()
    with pytest.raises(C05Error, match="deadline"):
        with fitscan.OrderedPool(2, tables(), Deadline(0.3, began)) as pool:
            for _ in pool.map(sleep_task, [30.0, 30.0, 30.0]):
                pass
    assert time.monotonic() - began < 8
    assert not psutil.Process().children(recursive=True)


def test_a1_interrupt_kills_rather_than_drains_workers() -> None:
    began = time.monotonic()
    with pytest.raises(KeyboardInterrupt):
        with fitscan.OrderedPool(2, tables()) as pool:
            for _ in pool.map(sleep_task, [0.01, 30.0]):
                raise KeyboardInterrupt
    assert time.monotonic() - began < 8
    assert not psutil.Process().children(recursive=True)


def test_a1_stubborn_descendants_are_killed_and_reaped(tmp_path: Path) -> None:
    pid_file = tmp_path / "grandchild.pid"
    began = time.monotonic()
    with pytest.raises(C05Error, match="deadline"):
        with fitscan.OrderedPool(1, tables(), Deadline(2.0, began)) as pool:
            for _ in pool.map(spawn_stubborn_grandchild, [str(pid_file)]):
                pass
    assert time.monotonic() - began < 10
    assert pid_file.exists()
    grandchild = int(pid_file.read_text(encoding="utf-8"))
    assert not psutil.pid_exists(grandchild) or psutil.Process(grandchild).status() == "zombie"


def test_a1_supervisor_kills_the_bpe_child_tree_on_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pid_file = tmp_path / "pid"
    code = (
        "import subprocess,sys,time,pathlib;"
        "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(120)']);"
        f"pathlib.Path(r'{pid_file}').write_text(str(p.pid)); time.sleep(120)"
    )
    monkeypatch.setattr(fitfast, "bpe_command", lambda job: [sys.executable, "-c", code])
    began = time.monotonic()
    with Supervisor(Deadline(3.0, began), None, interval=0.1) as guard:
        with pytest.raises(C05Error, match="deadline"):
            fitfast.run_bpe_child(
                tmp_path / "job", 1, _reporter(), guard, None, 0.05, supervisor=guard
            )
    assert time.monotonic() - began < 12
    assert pid_file.exists()
    assert not psutil.pid_exists(int(pid_file.read_text(encoding="utf-8")))


# == A2: process-tree RAM enforcement independent of progress =============================


def _reporter(progress: RunProgress | NullProgress | None = None, ceiling: int = 1 << 40) -> Any:
    return fitfast._TreeReporter(progress or NullProgress(), ceiling, ceiling)


ALLOCATE = "import time; x = bytearray({size}); x[::4096] = b'1' * len(x[::4096]); time.sleep(20)"
GRANDCHILD = "import subprocess,sys,time;subprocess.Popen([sys.executable,'-c',{inner!r}]).wait()"


@pytest.mark.parametrize("display", ["disabled", "enabled_fast", "enabled_hour"])
@pytest.mark.parametrize("shape", ["child", "grandchild"])
def test_a2_child_over_ram_immediately_is_killed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, display: str, shape: str
) -> None:
    inner = ALLOCATE.format(size=512 * MiB)
    code = inner if shape == "child" else GRANDCHILD.format(inner=inner)
    monkeypatch.setattr(fitfast, "bpe_command", lambda job: [sys.executable, "-c", code])
    progress: RunProgress | NullProgress
    if display == "disabled":
        progress = NullProgress()
    else:
        interval = 0.001 if display == "enabled_fast" else 3600.0
        progress = RunProgress(interval=interval, stream=io.StringIO())
    ceiling = tree_rss() + 160 * MiB
    began = time.monotonic()
    with pytest.raises(C05Error, match="RSS"):
        fitfast.run_bpe_child(
            tmp_path / "job", 1, _reporter(progress, ceiling), Deadline(60, began), None, 3600
        )
    assert time.monotonic() - began < 15
    assert not psutil.Process().children(recursive=True)


def test_a2_cli_ram_ceiling_holds_with_progress_disabled(
    c05: dict[str, Any],
    reference: dict[str, Any],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    args = cli_fit_args(c05, reference["policy"], tmp_path, "--rss-ceiling-gib", "0.01")
    digest = plan_digest(capsys, args)
    assert main([*args, *signing(digest)]) == 1
    assert "RSS" in json.loads(capsys.readouterr().out)["reason"]
    nothing_published(tmp_path)


def test_a2_supervisor_aggregates_the_whole_tree() -> None:
    code = ALLOCATE.format(size=256 * MiB)
    child = psutil.Popen([sys.executable, "-c", code])
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if tree_rss() - psutil.Process().memory_info().rss > 200 * MiB:
                break
            time.sleep(0.1)
        assert tree_rss() - psutil.Process().memory_info().rss > 200 * MiB
    finally:
        supervisor.terminate_processes([child, *child.children(recursive=True)], 2.0)


# == A3: authenticated BPE spool ===========================================================


def _frames(data: bytes) -> list[bytes]:
    frames, position = [], 0
    while position < len(data):
        size = struct.unpack("<Q", data[position : position + 8])[0]
        frames.append(data[position : position + 8 + size])
        position += 8 + size
    return frames


def _payload_flip(data: bytes) -> bytes:
    return data[:20] + (b"Z" if data[20:21] != b"Z" else b"Y") + data[21:]


SPOOL_MUTATIONS: dict[str, Callable[[bytes], bytes]] = {
    "same_length_payload": _payload_flip,
    "frame_length_change": lambda d: b"".join(
        [struct.pack("<Q", len(f) - 9) + f[8:-1] for f in _frames(d)[:1]] + _frames(d)[1:]
    ),
    "truncation": lambda d: d[:-5],
    "extension": lambda d: d + _frames(d)[0],
    "reorder": lambda d: b"".join(reversed(_frames(d))),
    "duplicate_frame": lambda d: b"".join([*_frames(d)[:-1], _frames(d)[0]]),
    "missing_frame": lambda d: b"".join(_frames(d)[:-1]),
}


def _mutate_spool(job: Path, change: Callable[[bytes], bytes]) -> None:
    spec = json.loads(job.read_text(encoding="utf-8"))
    path = Path(spec["spool"])
    path.chmod(0o666)  # An adversary with write permission (the spool is read-only).
    path.write_bytes(change(path.read_bytes()))


@pytest.mark.parametrize("name", sorted(SPOOL_MUTATIONS))
def test_a3_spool_mutation_before_bpe_refuses(
    c05: dict[str, Any],
    reference: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
) -> None:
    original = fitfast.run_bpe_child

    def mutating(job: Path, *args: Any, **kwargs: Any) -> None:
        _mutate_spool(job, SPOOL_MUTATIONS[name])
        original(job, *args, **kwargs)

    monkeypatch.setattr(fitfast, "run_bpe_child", mutating)
    with pytest.raises(C05Error, match="BPE|spool"):
        fast_fit(c05, reference["policy"], tmp_path)
    nothing_published(tmp_path)


def test_a3_spool_mutation_while_the_child_reads_refuses(
    c05: dict[str, Any], reference: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = fitfast.run_bpe_child

    def racing(job: Path, *args: Any, **kwargs: Any) -> None:
        def later() -> None:
            time.sleep(0.4)
            _mutate_spool(job, _payload_flip)

        thread = threading.Thread(target=later)
        thread.start()
        try:
            original(job, *args, **kwargs)
        finally:
            thread.join()

    monkeypatch.setattr(fitfast, "run_bpe_child", racing)
    with pytest.raises(C05Error, match="BPE|spool"):
        fast_fit(c05, reference["policy"], tmp_path)
    nothing_published(tmp_path)


def test_a3_consumed_spool_digest_is_bound_and_rederivable(
    c05: dict[str, Any], reference: dict[str, Any], fast: dict[str, Any]
) -> None:
    from xlm.data.exclusion.selection import iter_plan_documents
    from xlm.data.exclusion.transport import open_gate
    from xlm.tokenizers.bpe import fit_frame

    body = fast["envelope"]["payload"]
    spool = body["bpe_spool"]
    assert spool["consumed_by_bpe"] is True
    selected = {load(x)["doc_id"] for x in (fast["fit"] / FIT_SAMPLE).read_bytes().splitlines()}
    digest = hashlib.sha256()
    total = frames = 0
    with open_gate(c05["proof"], allow_authored=True) as gate:
        assert gate is not None
        for _, doc in iter_plan_documents(gate):  # Independent feed-order re-derivation.
            if doc.doc_id in selected:
                frame = fit_frame(doc)
                digest.update(frame)
                total += len(frame)
                frames += 1
    assert (spool["sha256"], spool["file_bytes"], spool["frames"]) == (
        digest.hexdigest(),
        total,
        frames,
    )
    view = open_streamed(c05["proof"], allow_authored=True, consumes=[])
    policy = load_fit_policy(reference["policy"])[0]
    result = fitfast.verify_fit_fast(
        view, fast["fit"], policy, c05["quotas"], c05["ifm"], workers=2, sources=True
    )
    assert result["verified"] and result["sources_rehashed"]


def test_a3_trainer_refuses_unauthenticated_or_oversized_frames(tmp_path: Path) -> None:
    from test_performance_tokenization import document
    from xlm.tokenizers.bpe import fit_frame

    docs = [document(f"text number {i} " * 20, i) for i in range(20)]
    data = b"".join(fit_frame(d) for d in docs)
    spool = tmp_path / "spool"
    spool.write_bytes(data)
    payload = sum(len(f) - 8 for f in _frames(data))
    good = hashlib.sha256(data).hexdigest()
    tokenizer, consumed = train_spool(
        spool, 300, "h", documents=20, payload_bytes=payload, expected_sha256=good,
        expected_file_bytes=len(data),
    )  # fmt: skip
    assert consumed.sha256 == good and consumed.frames == 20
    with pytest.raises(FitSampleBoundError, match="authenticated"):
        train_spool(spool, 300, "h", documents=20, payload_bytes=payload, expected_sha256="0" * 64)
    huge = tmp_path / "huge"
    huge.write_bytes(struct.pack("<Q", 10**12) + b"x")
    with pytest.raises(FitSampleBoundError, match="exceeds its bound"):
        train_spool(huge, 300, "h", documents=1, payload_bytes=1, max_frame_bytes=1024)


# == A4: kept-index snapshot ===============================================================


def _index_copy(fast: dict[str, Any], tmp_path: Path) -> Path:
    directory = tmp_path / "index"
    shutil.copytree(fast["fit"] / KEPT_INDEX_DIR, directory)
    return directory


def _poke(path: Path, offset: int = 8, value: bytes = b"\xff") -> None:
    data = bytearray(path.read_bytes())
    data[offset : offset + len(value)] = value
    path.write_bytes(bytes(data))


def test_a4_mutation_before_the_snapshot_refuses(
    c05: dict[str, Any], fast: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = _index_copy(fast, tmp_path)
    view = open_streamed(c05["proof"], allow_authored=True, consumes=[])
    original = keptindex._snapshot

    def racing(path: Path, size: int, sha: str) -> bytes:
        if path.name == "rows.bin":
            _poke(path)
        return original(path, size, sha)

    monkeypatch.setattr(keptindex, "_snapshot", racing)
    with pytest.raises(C05Error, match="section changed"):
        open_index(directory, view)


@pytest.mark.parametrize("moment", ["before_first_lookup", "during_lookups", "after_lookups"])
def test_a4_mutation_after_the_snapshot_never_reaches_consumers(
    c05: dict[str, Any], fast: dict[str, Any], tmp_path: Path, moment: str
) -> None:
    directory = _index_copy(fast, tmp_path)
    view = open_streamed(c05["proof"], allow_authored=True, consumes=[])
    index = open_index(directory, view)
    expected = [(index.doc_id(i), int(index.rows[i]["offset"])) for i in range(len(index))]
    if moment == "before_first_lookup":
        _poke(directory / "rows.bin")
    observed = []
    for i, (doc_id, _) in enumerate(expected):
        if moment == "during_lookups" and i == len(expected) // 2:
            _poke(directory / "rows.bin")
            _poke(directory / "ids.bin", 0, b"~")
        position = index.find(doc_id)
        assert position == i
        observed.append((index.doc_id(i), int(index.rows[i]["offset"])))
    assert observed == expected  # Consumption used only the authenticated snapshot.
    if moment == "after_lookups":
        _poke(directory / "rows.bin")
    with pytest.raises(C05Error, match="section changed"):
        index.reverify()  # And the change still prevents any success report.


def test_a4_mutation_before_publication_refuses_the_fit(
    c05: dict[str, Any], reference: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = fitfast.reverify_sections

    def tamper(directory: Path, sections: Any) -> None:
        _poke(directory / "rows.bin")
        original(directory, sections)

    monkeypatch.setattr(fitfast, "reverify_sections", tamper)
    with pytest.raises(C05Error, match="section changed"):
        fast_fit(c05, reference["policy"], tmp_path)
    nothing_published(tmp_path)


@pytest.mark.parametrize("case", ["truncate", "extend", "swap", "tables", "foreign_same_size"])
def test_a4_file_level_substitutions_refuse(
    c05: dict[str, Any], fast: dict[str, Any], tmp_path: Path, case: str
) -> None:
    directory = _index_copy(fast, tmp_path)
    view = open_streamed(c05["proof"], allow_authored=True, consumes=[])
    rows = directory / "rows.bin"
    if case == "truncate":
        rows.write_bytes(rows.read_bytes()[:-1])
    elif case == "extend":
        rows.write_bytes(rows.read_bytes() + b"\x00")
    elif case == "swap":
        a, b = directory / "ids.off", directory / "by_location.u4"
        data_a, data_b = a.read_bytes(), b.read_bytes()
        a.write_bytes(data_b)
        b.write_bytes(data_a)
    elif case == "tables":
        tables_path = directory / "tables.json"
        tables_path.write_bytes(tables_path.read_bytes().replace(b'"train"', b'"trian"'))
    else:
        data = bytearray(rows.read_bytes())
        data[-1] ^= 0x01  # Same size, different file.
        rows.write_bytes(bytes(data))
    with pytest.raises(C05Error, match="section changed|section length"):
        open_index(directory, view)


# == A5: structural validation of correctly re-signed indexes ===============================


def _resign(directory: Path, change: Callable[[dict[str, bytes], dict[str, Any]], None]) -> None:
    manifest = directory / MANIFEST
    envelope = load(manifest.read_bytes())
    body = {k: v for k, v in envelope["payload"].items() if k != "issuer"}
    sections = {name: (directory / name).read_bytes() for name in keptindex.SECTIONS}
    change(sections, body)
    for name, data in sections.items():
        (directory / name).write_bytes(data)
        body["sections"][name] = {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    manifest.unlink()
    canonical.write_canonical_json(manifest, signed(body, ISSUER, KEY.encode()))


def _rows(sections: dict[str, bytes]) -> np.ndarray:
    return np.frombuffer(sections["rows.bin"], dtype=keptindex.ROW_DTYPE).copy()


def _set_rows(sections: dict[str, bytes], rows: np.ndarray) -> None:
    sections["rows.bin"] = rows.tobytes()


def _field(
    column: str, value: int, row: int = 0
) -> Callable[[dict[str, bytes], dict[str, Any]], None]:
    def change(sections: dict[str, bytes], _: dict[str, Any]) -> None:
        rows = _rows(sections)
        rows[column][row] = value
        _set_rows(sections, rows)

    return change


def _rows_count(sections: dict[str, bytes], body: dict[str, Any]) -> None:
    body["rows"] = 1


def _short_rows(sections: dict[str, bytes], body: dict[str, Any]) -> None:
    sections["rows.bin"] = sections["rows.bin"][:-64]


def _trailing_ids(sections: dict[str, bytes], body: dict[str, Any]) -> None:
    sections["ids.bin"] = sections["ids.bin"] + b"x"


def _duplicate_ids(sections: dict[str, bytes], body: dict[str, Any]) -> None:
    offsets = np.frombuffer(sections["ids.off"], dtype="<u8")
    ids = bytearray(sections["ids.bin"])
    first = ids[int(offsets[0]) : int(offsets[1])]
    second = slice(int(offsets[1]), int(offsets[2]))
    assert len(first) == second.stop - second.start
    ids[second] = first
    sections["ids.bin"] = bytes(ids)


def _bad_permutation(sections: dict[str, bytes], body: dict[str, Any]) -> None:
    order = np.frombuffer(sections["by_location.u4"], dtype="<u4").copy()
    order[1] = order[0]
    sections["by_location.u4"] = order.tobytes()


def _reallocated(sections: dict[str, bytes], body: dict[str, Any]) -> None:
    rows = _rows(sections)
    rows["allocation"][0] = (int(rows["allocation"][0]) + 1) % 17
    _set_rows(sections, rows)


def _bad_offsets(sections: dict[str, bytes], body: dict[str, Any]) -> None:
    offsets = np.frombuffer(sections["ids.off"], dtype="<u8").copy()
    offsets[1] = offsets[2] + 1
    sections["ids.off"] = offsets.tobytes()


STRUCTURAL = {
    "rows_count_mismatch": (_rows_count, "row count"),
    "section_length": (_short_rows, "length differs"),
    "trailing_bytes": (_trailing_ids, "offsets out of range"),
    "duplicate_ids": (_duplicate_ids, "strictly ascending"),
    "bad_id_offsets": (_bad_offsets, "strictly increasing|ascending"),
    "permutation": (_bad_permutation, "permutation"),
    "offset_out_of_file": (_field("offset", 10**12), "outside its plan file"),
    "row_out_of_file": (_field("row", 10**6), "outside its plan file"),
    "file_ordinal": (_field("file", 10**6), "file ordinal"),
    "split_enum": (_field("assigned_split", 7), "split code"),
    "train_with_audit_original": (_field("original_split", 2), "original split"),
    "allocation_enum": (_field("allocation", 999), "allocation reference"),
    "accounting": (_reallocated, "accounting"),
    "zero_length": (_field("length", 0), "line length"),
}


@pytest.mark.parametrize("case", sorted(STRUCTURAL))
def test_a5_signed_but_inconsistent_index_refuses(
    c05: dict[str, Any], fast: dict[str, Any], tmp_path: Path, case: str
) -> None:
    directory = _index_copy(fast, tmp_path)
    view = open_streamed(c05["proof"], allow_authored=True, consumes=[])
    change, message = STRUCTURAL[case]
    if case == "train_with_audit_original":
        rows = open_index(directory, view).rows
        target = int(np.flatnonzero(rows["assigned_split"] == 0)[0])
        change = _field("original_split", 2, target)
    _resign(directory, change)
    with pytest.raises(C05Error, match=message):
        open_index(directory, view)


# == A6: storage envelope, read ceilings, cleanup accountability ============================


def test_a6_insufficient_free_space_refuses_before_work(
    c05: dict[str, Any], reference: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from collections import namedtuple

    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(fitfast.shutil, "disk_usage", lambda path: usage(10, 10, 1))
    with pytest.raises(C05Error, match="insufficient free space"):
        fast_fit(c05, reference["policy"], tmp_path)
    nothing_published(tmp_path)


def test_a6_reserve_violation_during_the_run_refuses(
    c05: dict[str, Any], reference: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from collections import namedtuple

    usage = namedtuple("usage", "total used free")
    real = shutil.disk_usage
    calls = {"n": 0}

    def shrinking(path: Any) -> Any:
        calls["n"] += 1
        return real(path) if calls["n"] < 4 else usage(10, 10, 0)

    monkeypatch.setattr(supervisor.shutil, "disk_usage", shrinking)
    with pytest.raises(C05Error, match="free-space reserve"):
        fast_fit(c05, reference["policy"], tmp_path, free_reserve_bytes=1)
    nothing_published(tmp_path)


@pytest.mark.parametrize(
    ("attribute", "value", "message"),
    [
        ("index_bound", lambda kept, membership: 4096, "kept index exceeds"),
        ("spool_bound", lambda policy, allocations: 64, "spool exceeds"),
        ("TOKENIZER_CEILING", 16, "BPE child failed|ceiling"),
        ("SAMPLE_CEILING", 16, "sample artifact ceiling"),
    ],
)
def test_a6_hard_write_ceilings_refuse(
    c05: dict[str, Any],
    reference: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    attribute: str,
    value: Any,
    message: str,
) -> None:
    monkeypatch.setattr(fitfast, attribute, value)
    with pytest.raises(C05Error, match=message):
        fast_fit(c05, reference["policy"], tmp_path)
    nothing_published(tmp_path)


def test_a6_source_reads_stop_at_the_frozen_size(tmp_path: Path) -> None:
    path = tmp_path / "source.jsonl"
    path.write_bytes(b'{"a":1}\n' * 100)
    size = path.stat().st_size
    task = fitscan.SourceTask(
        0, str(path), "0" * 64, size, 100, MiB, np.zeros(0, np.uint32), [],
        np.zeros(0, np.uint64), np.zeros(0, np.uint8), np.zeros(0, np.bool_), [],
    )  # fmt: skip
    path.write_bytes(b'{"a":1}\n' * 200)  # Grew after planning, same stat would refuse too.
    with pytest.raises(C05Error, match="size changed|exceeds frozen"):
        fitscan.scan_source_file(task)

    class Lying(type(Path())):  # type: ignore[misc]
        def stat(self, *args: Any, **kwargs: Any) -> Any:
            real = super().stat(*args, **kwargs)
            return type("S", (), {"st_size": size, "st_mtime": real.st_mtime})()

    original = fitscan.Path
    fitscan.Path = Lying  # type: ignore[misc]
    try:
        with pytest.raises(C05Error, match="exceeds frozen bytes"):
            fitscan.scan_source_file(task)
    finally:
        fitscan.Path = original  # type: ignore[misc]


def test_a6_cleanup_failure_is_visible_and_never_success(
    c05: dict[str, Any], reference: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def foreign_then_fail(job: Path, *args: Any, **kwargs: Any) -> None:
        stage = next(tmp_path.glob("fit.partial-*"))
        (stage / "unregistered.txt").write_text("not owned", encoding="utf-8")
        raise C05Error("authored BPE failure")

    monkeypatch.setattr(fitfast, "run_bpe_child", foreign_then_fail)
    with pytest.raises(C05Error, match="authored BPE failure; cleanup residue in"):
        fast_fit(c05, reference["policy"], tmp_path)
    assert not (tmp_path / "fit").exists()
    stage = next(tmp_path.glob("fit.partial-*"))
    assert sorted(p.name for p in stage.iterdir()) == ["unregistered.txt"]  # Not deleted.


# == A7: total projection =================================================================


def _projection(deadline: float = 1200.0) -> Projection:
    return envelope_for(deadline_seconds=deadline).projection()


def test_a7_projection_reserves_bpe_and_finalization() -> None:
    projection = _projection()
    projection.plan("membership", 6.36e9)
    projection.plan("source", 104.5e9)
    view = projection.evaluate(elapsed=0.0)
    assert view["bpe_reserve_s"] == 240 and view["finalization_reserve_s"] == 60
    expected = 6.36e9 / 40e6 + 20 + 104.5e9 / 0.2e9 + 240 + 60
    assert float(view["projected_total_s"]) == pytest.approx(expected)
    assert view["measured"] is False


def test_a7_impossible_total_warns_then_aborts_early() -> None:
    projection = _projection()
    projection.plan("membership", 1.0)
    projection.finish("membership")
    projection.plan("source", 100e9)
    projection.progress("source", 2.5e9, 100e9)
    projection.stages["source"].started = time.monotonic() - 60  # ~0.04 GB/s measured.
    messages: list[str] = []
    guard = Supervisor(
        Deadline(1200, time.monotonic()), None, interval=0.1, projection=projection,
        warn=messages.append,
    )  # fmt: skip
    guard.sample()
    assert messages and "projected total" in messages[0] and "1,200 s deadline" in messages[0]
    assert guard.failure == supervisor.PROJECTION_REASON


def test_a7_slow_planning_rates_warn_but_never_abort_without_measurement(
    c05: dict[str, Any],
    reference: dict[str, Any],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fast_fit(c05, reference["policy"], tmp_path, source_planning_bytes_per_s=1.0)
    err = capsys.readouterr().err
    assert "SLO WARNING | projected total" in err
    assert (tmp_path / "fit").exists()
    import re

    assert not re.search(r"[0-9a-f]{16}", err) and str(c05["root"]) not in err


# == A8: the reviewed operational envelope is bound =======================================


@pytest.mark.parametrize(
    "changed",
    [
        ("--workers", "2"),
        ("--bpe-threads", "8"),
        ("--deadline-seconds", "1199"),
        ("--rss-ceiling-gib", "23"),
        ("--free-reserve-gib", "1"),
    ],
)
def test_a8_changed_operational_setting_rejects_the_reviewed_digest(
    c05: dict[str, Any],
    reference: dict[str, Any],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    changed: tuple[str, str],
) -> None:
    base = cli_fit_args(c05, reference["policy"], tmp_path)
    digest = plan_digest(capsys, base)
    flag, value = changed
    run = list(base)
    if flag in run:
        run[run.index(flag) + 1] = value
    else:
        run += [flag, value]
    assert main([*run, *signing(digest)]) == 1
    assert "identical operational settings" in json.loads(capsys.readouterr().out)["reason"]
    nothing_published(tmp_path)


def test_a8_plan_binds_the_full_envelope(
    c05: dict[str, Any],
    reference: dict[str, Any],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    args = cli_fit_args(c05, reference["policy"], tmp_path)
    assert main([*args, "--plan-only"]) == 0
    plan = json.loads(capsys.readouterr().out)["resource_plan"]
    operational = plan["operational"]
    for name in (
        "source_workers",
        "bpe_threads",
        "deadline_seconds",
        "ram_ceiling_bytes",
        "free_reserve_bytes",
        "monitor_interval_seconds",
        "worker_queue_tasks",
        "shutdown_grace_seconds",
        "bpe_reserve_seconds",
        "finalization_reserve_seconds",
    ):
        assert name in operational
    assert plan["inputs"]["membership_read_bytes"] > 0 and plan["inputs"]["source_read_bytes"] > 0
    items = plan["storage"]["items"]
    for name in (
        "bpe_spool",
        "kept_index",
        "sample",
        "tokenizer",
        "fit_manifest_and_resource_plan",
    ):
        assert items[name]["bytes"] > 0
    assert all(g["required_free_bytes"] >= g["growth_bytes"] for g in plan["storage"]["devices"])
    assert plan["digest"] == canonical.self_digest(plan)


def test_a8_cli_success_with_reviewed_envelope_is_equivalent(
    c05: dict[str, Any],
    reference: dict[str, Any],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    args = cli_fit_args(c05, reference["policy"], tmp_path)
    digest = plan_digest(capsys, args)
    assert main([*args, *signing(digest)]) == 0
    capsys.readouterr()
    assert_equivalent(reference["fit"], tmp_path / "fit")
    measured = load((tmp_path / "fit" / "tokenizer_fit_resource_plan.json").read_bytes())
    assert measured["measured"]["source_workers"] == 1 and measured["measured"]["bpe_threads"] == 1


# == Equivalence across operational settings (spawned workers, BPE threads) ==============


@pytest.mark.parametrize(("workers", "threads"), [(1, 1), (2, 16), (4, 8), (8, 16)])
def test_equivalence_spawned_workers_and_threads(
    c05: dict[str, Any], reference: dict[str, Any], tmp_path: Path, workers: int, threads: int
) -> None:
    fast_fit(c05, reference["policy"], tmp_path, workers=workers, bpe_threads=threads, inline=False)
    assert_equivalent(reference["fit"], tmp_path / "fit")
