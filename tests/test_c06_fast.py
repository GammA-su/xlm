"""C06 FAST path: exact equivalence to the bb886bd reference, adversaries, mutations.

Authored/generated fixtures only (the generated C05 flow with diagnostic_val and
audit partitions, planted excluded/duplicate records). No real corpus or proof.
The reference is ``tokenizer_fit.fit_tokenizer`` (bb886bd), run in-process.
"""

# ruff: noqa: F811  (pytest fixtures imported from test_c06_tokenizer_fit)

from __future__ import annotations

import hashlib
import io
import json
import random
import re
import shutil
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from fractions import Fraction
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from scripts import c05_synthetic_flow as flow_module
from scripts.c05_authored_pilot import KEY
from scripts.c05_synthetic_flow import ISSUER, KEY_ENV, decide_and_plan, prepare, run_c05

from test_c06_tokenizer_fit import (  # noqa: F401 (fixtures)
    TARGET,
    VOCAB,
    c05,
    key,
    sample_rows,
    write_policy,
)
from test_c06_tokenizer_fit import fit as reference_fit
from xlm.data.evidence_v2 import canonical
from xlm.data.exclusion import fitfast, fitscan
from xlm.data.exclusion.artifacts import signed
from xlm.data.exclusion.fitfast import (
    FIT_PATH,
    KEPT_INDEX_DIR,
    bpe_environment,
    fit_tokenizer_fast,
    open_streamed,
    plan_from_proof_fast,
    select_exact,
    verify_fit_fast,
    verify_kept_index,
)
from xlm.data.exclusion.keptindex import MANIFEST, open_index
from xlm.data.exclusion.policy import C05Error, ProductionPolicy
from xlm.data.exclusion.progress import RunProgress
from xlm.data.exclusion.tokenizer_fit import (
    FIT_MANIFEST,
    FIT_SAMPLE,
    TOKENIZER_DIR,
    Budget,
    FitDeficit,
    _Allocation,
    _Entry,
    load_fit_policy,
)
from xlm.tokenizers.bpe import ByteLevelBPETokenizer

load = canonical.loads_bytes_strict
# Provenance/operational fields: the only manifest differences allowed vs bb886bd.
PROVENANCE = {"fit_path", "kept_index", "bpe_spool", "resource_plan_digest", "implementation"}


def envelope_for(**changes: Any) -> fitfast.OperationalEnvelope:
    values: dict[str, Any] = {"source_workers": 1, "bpe_threads": 16, "free_reserve_bytes": 0}
    values.update(changes)
    return fitfast.OperationalEnvelope(**values)


def fast_fit(
    c05: dict[str, Any],
    policy_path: Path,
    out: Path,
    *,
    workers: int = 1,
    bpe_threads: int = 16,
    progress: RunProgress | None = None,
    deadline: float = 1200.0,
    rss_ceiling: int = fitfast.DEFAULT_RSS_CEILING,
    inline: bool | None = None,
    **changes: Any,
) -> dict[str, Any]:
    """Library-level fast fit; ``workers=1`` runs inline unless ``inline=False``."""
    envelope = envelope_for(
        source_workers=workers,
        bpe_threads=bpe_threads,
        deadline_seconds=deadline,
        ram_ceiling_bytes=rss_ceiling,
        **changes,
    )
    policy, sha = load_fit_policy(policy_path)
    locations = fitfast.Locations(out / "scratch", out / "fit", out / "deficit.json")
    planned = plan_from_proof_fast(
        c05["proof"], policy, c05["quotas"], c05["ifm"], envelope, locations
    )
    view = open_streamed(c05["proof"], allow_authored=True, consumes=[])
    return fit_tokenizer_fast(
        view,
        policy,
        sha,
        quotas=c05["quotas"],
        ifm_split=c05["ifm"],
        scratch=out / "scratch",
        output=out / "fit",
        issuer=ISSUER,
        key=KEY.encode(),
        accepted_plan_digest=planned["digest"],
        envelope=envelope,
        deficit_report_path=out / "deficit.json",
        progress=progress,
        inline=(workers == 1) if inline is None else inline,
        heartbeat_seconds=0.05,
    )


@pytest.fixture(scope="module")
def reference(c05: dict[str, Any], tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    out = tmp_path_factory.mktemp("reference")
    policy = write_policy(out / "policy.yaml", c05)
    envelope = reference_fit(c05, policy, out)
    return {"out": out, "policy": policy, "fit": out / "fit", "envelope": envelope}


@pytest.fixture(scope="module")
def fast(
    c05: dict[str, Any], reference: dict[str, Any], tmp_path_factory: pytest.TempPathFactory
) -> dict[str, Any]:
    out = tmp_path_factory.mktemp("fast")
    stream = io.StringIO()
    progress = RunProgress(interval=0.001, stream=stream, label="C06", min_span=0.001)
    envelope = fast_fit(c05, reference["policy"], out, workers=1, progress=progress)
    return {"out": out, "fit": out / "fit", "envelope": envelope, "progress": stream.getvalue()}


def assert_equivalent(reference_dir: Path, fast_dir: Path) -> None:
    """Every scientific artifact identical; only provenance fields may differ."""
    assert (fast_dir / FIT_SAMPLE).read_bytes() == (reference_dir / FIT_SAMPLE).read_bytes()
    for name in ("tokenizer.json", "tokenizer_manifest.json", "c05-binding.json"):
        assert (fast_dir / TOKENIZER_DIR / name).read_bytes() == (
            reference_dir / TOKENIZER_DIR / name
        ).read_bytes(), name
    model = json.loads((fast_dir / TOKENIZER_DIR / "tokenizer.json").read_bytes())["model"]
    expected = json.loads((reference_dir / TOKENIZER_DIR / "tokenizer.json").read_bytes())["model"]
    assert model["vocab"] == expected["vocab"] and model["merges"] == expected["merges"]
    ours = load((fast_dir / FIT_MANIFEST).read_bytes())["payload"]
    theirs = load((reference_dir / FIT_MANIFEST).read_bytes())["payload"]
    assert set(ours) - set(theirs) == {"fit_path", "kept_index", "bpe_spool"}
    for name in set(theirs) - PROVENANCE:
        assert ours[name] == theirs[name], name
    assert ours["fit_path"] == FIT_PATH


# -- exact equivalence ---------------------------------------------------------------------


def test_fast_fit_is_identical_to_reference(
    reference: dict[str, Any], fast: dict[str, Any]
) -> None:
    assert_equivalent(reference["fit"], fast["fit"])
    body = fast["envelope"]["payload"]
    assert (
        body["sample"]["training_input_hash"]
        == reference["envelope"]["payload"]["sample"]["training_input_hash"]
    )
    assert sorted(p.name for p in fast["fit"].iterdir()) == sorted(
        [
            TOKENIZER_DIR,
            FIT_MANIFEST,
            FIT_SAMPLE,
            "tokenizer_fit_resource_plan.json",
            KEPT_INDEX_DIR,
        ]
    )
    assert not list(fast["out"].glob("fit.partial-*"))
    assert not list((fast["out"] / "scratch").glob("c06-fit-*"))


@pytest.mark.parametrize("workers", [2, 4, 8])
def test_worker_count_never_changes_any_output(
    c05: dict[str, Any],
    reference: dict[str, Any],
    fast: dict[str, Any],
    tmp_path: Path,
    workers: int,
) -> None:
    envelope = fast_fit(c05, reference["policy"], tmp_path, workers=workers)
    assert_equivalent(reference["fit"], tmp_path / "fit")
    for name in ("rows.bin", "ids.bin", "ids.off", "by_location.u4", "tables.json"):
        assert (tmp_path / "fit" / KEPT_INDEX_DIR / name).read_bytes() == (
            fast["fit"] / KEPT_INDEX_DIR / name
        ).read_bytes()
    assert envelope["payload"]["tokenizer"] == fast["envelope"]["payload"]["tokenizer"]


@pytest.mark.parametrize("threads", [1, 8])
def test_bpe_thread_setting_never_changes_the_tokenizer(
    c05: dict[str, Any], reference: dict[str, Any], tmp_path: Path, threads: int
) -> None:
    fast_fit(c05, reference["policy"], tmp_path, workers=2, bpe_threads=threads)
    assert_equivalent(reference["fit"], tmp_path / "fit")


@pytest.mark.parametrize(
    "changes",
    [
        {"seed": 8},
        {"max_document_bytes": 620, "target_sample_bytes": 11 * 1024},
        {"target_sample_bytes": 11 * 1024},
        {"target_sample_bytes": 3},
    ],
)
def test_equivalence_across_policies(
    c05: dict[str, Any], tmp_path: Path, changes: dict[str, Any]
) -> None:
    policy = write_policy(tmp_path / "p.yaml", c05, **changes)
    reference_fit(c05, policy, tmp_path / "ref")
    fast_fit(c05, policy, tmp_path / "fast", workers=2)
    assert_equivalent(tmp_path / "ref/fit", tmp_path / "fast/fit")


@pytest.mark.parametrize(
    "changes", [{"target_sample_bytes": 11 * 8192}, {"max_document_bytes": 560}]
)
def test_identical_deficit_reports(
    c05: dict[str, Any], tmp_path: Path, changes: dict[str, Any]
) -> None:
    policy = write_policy(tmp_path / "p.yaml", c05, **changes)
    with pytest.raises(FitDeficit) as theirs:
        reference_fit(c05, policy, tmp_path / "ref")
    with pytest.raises(FitDeficit) as ours:
        fast_fit(c05, policy, tmp_path / "fast", workers=2)
    assert ours.value.report == theirs.value.report
    assert not (tmp_path / "fast/fit").exists()
    assert not list((tmp_path / "fast/scratch").glob("c06-fit-*"))


def _brute(entries: list[_Entry], budget: int) -> list[str]:
    taken, total = [], 0
    for entry in sorted(entries, key=_Entry.key):
        if total >= budget:
            break
        taken.append(entry.doc_id)
        total += entry.size
    return taken


@pytest.mark.parametrize("budget", [0, 1, 250, 5_000])
def test_vectorized_selection_equals_reference_heaps(budget: int) -> None:
    """Random ranks with forced rank ties and zero sizes, in shuffled membership order."""
    rng = random.Random(budget)
    n = 3_000
    ids = sorted({f"d{rng.getrandbits(40):012x}" for _ in range(n)})
    n = len(ids)
    ranks = [rng.randbytes(32) for _ in range(n)]
    ranks[5] = ranks[7] = ranks[100]  # Exact SHA ties resolved by doc id.
    sizes = [rng.choice([0, 1, 7, 100, 999]) for _ in range(n)]
    blob = "".join(ids).encode()
    offsets = np.zeros(n + 1, dtype=np.uint64)
    np.cumsum([len(i) for i in ids], out=offsets[1:])
    m = fitfast.Membership(
        rows=n,
        ids=blob,
        id_offsets=offsets,
        file=np.zeros(n, np.uint32),
        row=np.arange(1, n + 1, dtype=np.uint32),
        nbytes=np.asarray(sizes, np.uint64),
        content=np.zeros((n, 32), np.uint8),
        split=np.zeros(n, np.uint8),
        allocation=np.zeros(n, np.uint16),
        rank=np.frombuffer(b"".join(ranks), np.uint8).reshape(n, 32),
    )
    policy = load_fit_policy(Path("recipes/tokenizer/mix01_fit_shares_v1.yaml"))[0]
    budgets = {"a": Budget("c", 1, 1, budget, Fraction(1, 1))}
    states, selected = select_exact(m, policy, budgets, ["a"])
    entries = [_Entry(ranks[i], ids[i], "0" * 64, sizes[i]) for i in range(n)]
    expected = _brute(entries, budget)
    assert sorted(e.doc_id for e in states["a"].heap) == sorted(expected)
    heap = _Allocation(budget)
    order = list(range(n))
    rng.shuffle(order)
    for i in order:
        heap.offer(entries[i])
    assert sorted(e.doc_id for e in heap.heap) == sorted(expected)
    assert states["a"].held == heap.held and int(selected.sum()) == len(expected)


# -- Astra original-split adversary --------------------------------------------------------


@pytest.fixture(scope="module")
def adversary(tmp_path_factory: pytest.TempPathFactory) -> Iterator[dict[str, Any]]:
    """C05 assigns family splits; several records carry original split diagnostic_val."""
    environment = pytest.MonkeyPatch()
    environment.setenv(KEY_ENV, KEY)
    monkey = pytest.MonkeyPatch()
    original_doc = flow_module.doc

    def doc(number: int, source: str, text: str, metadata: Any = None) -> Any:
        record = original_doc(number, source, text, metadata)
        return replace(record, split="diagnostic_val") if number % 9 == 4 else record

    monkey.setattr(flow_module, "doc", doc)
    root = tmp_path_factory.mktemp("adversary") / "root"
    paths = prepare(root)
    monkey.undo()
    plan_path = decide_and_plan(paths)
    result = run_c05(paths, plan_path)
    yield {
        "root": root,
        "proof": result["proof"],
        "quotas": root / "quotas.yaml",
        "ifm": root / "ifm-split.json",
        "completion": result["completion"]["payload"],
    }
    environment.undo()


def test_original_split_adversary_refuses_exactly_like_reference(
    adversary: dict[str, Any], tmp_path: Path
) -> None:
    completion_dir = open_streamed(adversary["proof"], allow_authored=True, consumes=[]).directory
    membership = [load(x) for x in (completion_dir / "membership.jsonl").read_bytes().splitlines()]
    assert any(r["split"] == "train" for r in membership)
    policy = write_policy(tmp_path / "p.yaml", adversary)
    with pytest.raises(C05Error, match="record split differs from C05 membership"):
        reference_fit(adversary, policy, tmp_path / "ref")
    for workers in (1, 4):
        with pytest.raises(C05Error, match="record split differs from C05 membership"):
            fast_fit(adversary, policy, tmp_path / f"fast{workers}", workers=workers)
        assert not (tmp_path / f"fast{workers}/fit").exists()


# -- mutation / TOCTOU ----------------------------------------------------------------------


@contextmanager
def mutated(path: Path, change: Any) -> Iterator[None]:
    original = path.read_bytes()
    try:
        path.write_bytes(change(original))
        yield
    finally:
        path.write_bytes(original)


def _flip(data: bytes, position: int) -> bytes:
    replacement = b"x" if data[position : position + 1] != b"x" else b"y"
    return data[:position] + replacement + data[position + 1 :]


def _plan_file(c05: dict[str, Any], ordinal: int = 0) -> Path:
    view = open_streamed(c05["proof"], allow_authored=True, consumes=[])
    return Path(view.plan.data_root) / view.plan.files[ordinal].path


def _membership_file(c05: dict[str, Any]) -> Path:
    return open_streamed(c05["proof"], allow_authored=True, consumes=[]).directory / (
        "membership.jsonl"
    )


def _refuses(
    c05: dict[str, Any], tmp_path: Path, match: str, workers: int = 1, **changes: Any
) -> None:
    policy = write_policy(tmp_path / "p.yaml", c05)
    with pytest.raises(C05Error, match=match):
        fast_fit(c05, policy, tmp_path, workers=workers, **changes)
    assert not (tmp_path / "fit").exists() and not list(tmp_path.glob("fit.partial-*"))
    assert not list((tmp_path / "scratch").glob("c06-fit-*"))


def test_membership_byte_change_refuses(c05: dict[str, Any], tmp_path: Path) -> None:
    path = _membership_file(c05)
    data = path.read_bytes()
    position = data.index(b'"content":"') + len(b'"content":"') + 3
    swap = b"a" if data[position : position + 1] != b"a" else b"b"
    with mutated(path, lambda d: d[:position] + swap + d[position + 1 :]):
        _refuses(c05, tmp_path, "completion membership changed")


def test_membership_changed_during_stream_refuses(
    c05: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _membership_file(c05)
    original = path.read_bytes()
    blocks = fitfast._membership_blocks

    def racing(*args: Any) -> Iterator[bytes]:
        for number, block in enumerate(blocks(*args)):
            if number == 0:
                position = len(original) - 200
                with path.open("r+b") as stream:  # Same size, later bytes change.
                    stream.seek(position)
                    stream.write(b"0")
            yield block

    monkeypatch.setattr(fitfast, "_membership_blocks", racing)
    try:
        _refuses(
            c05, tmp_path, "completion membership changed|membership", membership_chunk_bytes=4096
        )
    finally:
        path.write_bytes(original)


@pytest.mark.parametrize(
    "change",
    [
        pytest.param(lambda d: d[:-10], id="truncated"),
        pytest.param(lambda d: d + d.splitlines(keepends=True)[0], id="extended"),
        pytest.param(
            lambda d: d.replace(b"\n", b"\n" + d.splitlines(keepends=True)[1], 1),
            id="line-inserted",
        ),
        pytest.param(lambda d: b"".join(d.splitlines(keepends=True)[1:]), id="line-deleted"),
        pytest.param(lambda d: d.replace(b" w", b"\nw", 1), id="row-count-same-size"),
        pytest.param(lambda d: _flip(d, len(d) // 2), id="byte-changed"),
    ],
)
def test_source_mutations_before_scan_refuse(
    c05: dict[str, Any], tmp_path: Path, change: Any
) -> None:
    with mutated(_plan_file(c05, 3), change):
        _refuses(c05, tmp_path, "input|C05|canonical", workers=2)


def _row_bytes(c05: dict[str, Any], doc_id: str) -> tuple[Path, int]:
    view = open_streamed(c05["proof"], allow_authored=True, consumes=[])
    for item in view.plan.files:
        path = Path(view.plan.data_root) / item.path
        data = path.read_bytes()
        marker = f'"doc_id":"{doc_id}"'.encode()
        if marker in data:
            line_start = data.rfind(b"\n", 0, data.index(marker)) + 1
            return path, data.index(b'"text":"', line_start) + len(b'"text":"') + 2
    raise AssertionError("doc not found")


@pytest.mark.parametrize("which", ["selected", "unselected"])
def test_selected_or_unselected_row_change_refuses(
    c05: dict[str, Any], reference: dict[str, Any], tmp_path: Path, which: str
) -> None:
    sample = {r["doc_id"] for r in sample_rows(reference["fit"])}
    completion_dir = open_streamed(c05["proof"], allow_authored=True, consumes=[]).directory
    kept = [load(x) for x in (completion_dir / "membership.jsonl").read_bytes().splitlines()]
    choices = [r["doc_id"] for r in kept if (r["doc_id"] in sample) == (which == "selected")]
    path, position = _row_bytes(c05, choices[len(choices) // 2])
    with mutated(path, lambda d: _flip(d, position)):
        _refuses(c05, tmp_path, "input content changed|differs", workers=2)


def test_source_changed_during_scan_refuses(
    c05: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _plan_file(c05, 0)
    original = target.read_bytes()
    base = type(Path())

    class Racing(base):  # type: ignore[valid-type,misc]
        def open(self, *args: Any, **kwargs: Any) -> Any:
            handle = super().open(*args, **kwargs)
            if Path(str(self)) != target:
                return handle
            outer = self

            class Wrapped:
                calls = 0

                def read(self, size: int = -1) -> bytes:
                    data = handle.read(size)
                    Wrapped.calls += 1
                    if Wrapped.calls == 1:
                        with Path(str(outer)).open("r+b") as stream:
                            stream.seek(len(original) - 50)
                            stream.write(b"Z")
                    return bytes(data)

                def __enter__(self) -> Any:
                    return self

                def __exit__(self, *exc: object) -> None:
                    handle.close()

            return Wrapped()

    monkeypatch.setattr(fitscan, "Path", Racing)
    try:
        _refuses(
            c05,
            tmp_path,
            "input content changed since C05|differs from its C05",
            source_block_bytes=1024,
        )
    finally:
        target.write_bytes(original)


# -- deadline / environment / ceilings -----------------------------------------------------


def test_deadline_exceeded_publishes_nothing(c05: dict[str, Any], tmp_path: Path) -> None:
    policy = write_policy(tmp_path / "p.yaml", c05)
    with pytest.raises(C05Error, match="deadline"):
        fast_fit(c05, policy, tmp_path, deadline=1e-6)
    assert not (tmp_path / "fit").exists()


def test_deadline_terminates_the_bpe_child(
    c05: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import time

    monkeypatch.setattr(
        fitfast, "bpe_command", lambda job: [sys.executable, "-c", "import time; time.sleep(120)"]
    )
    policy = write_policy(tmp_path / "p.yaml", c05)
    started = time.monotonic()
    with pytest.raises(C05Error, match="deadline"):
        fast_fit(c05, policy, tmp_path, deadline=8)
    assert time.monotonic() - started < 60
    assert not (tmp_path / "fit").exists() and not list(tmp_path.glob("fit.partial-*"))
    assert not list((tmp_path / "scratch").glob("c06-fit-*"))


def test_failed_bpe_child_publishes_nothing(
    c05: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        fitfast, "bpe_command", lambda job: [sys.executable, "-c", "raise SystemExit(3)"]
    )
    _refuses(c05, tmp_path, "BPE child failed")


def test_inherited_serial_tokenizers_setting_is_overridden(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TOKENIZERS_PARALLELISM", "false")
    monkeypatch.setenv("RAYON_NUM_THREADS", "1")
    environment = bpe_environment(8)
    assert environment["TOKENIZERS_PARALLELISM"] == "true"
    assert environment["RAYON_NUM_THREADS"] == "8"
    with pytest.raises(C05Error):
        bpe_environment(3)


def test_process_tree_rss_ceiling_refuses(c05: dict[str, Any], tmp_path: Path) -> None:
    policy = write_policy(tmp_path / "p.yaml", c05)
    with pytest.raises(C05Error, match="RSS"):
        fast_fit(c05, policy, tmp_path, rss_ceiling=1)
    assert not (tmp_path / "fit").exists()


# -- kept index ----------------------------------------------------------------------------


def test_kept_index_locates_every_kept_record(c05: dict[str, Any], fast: dict[str, Any]) -> None:
    view = open_streamed(c05["proof"], allow_authored=True, consumes=[])
    index = open_index(fast["fit"] / KEPT_INDEX_DIR, view)
    kept = [load(x) for x in (view.directory / "membership.jsonl").read_bytes().splitlines()]
    assert len(index) == len(kept) == view.completion["kept"]
    for position, row in enumerate(kept):
        assert index.doc_id(position) == row["doc_id"]
        assert index.find(row["doc_id"]) == position
        record = index.rows[position]
        path = Path(view.plan.data_root) / index.files[int(record["file"])]["path"]
        with path.open("rb") as stream:
            stream.seek(int(record["offset"]))
            line = stream.read(int(record["length"]))
        source = load(line)
        assert source["doc_id"] == row["doc_id"] and int(record["row"]) == row["row"]
        assert (
            int(record["original_split"])
            == {"train": 0, "diagnostic_val": 1, "audit": 2}[source["split"]]
        )
        assert bytes(record["content"]).hex() == row["content"]
    assert index.find("not-a-document") is None
    order = [(int(index.rows[p]["file"]), int(index.rows[p]["row"])) for p in index.by_location]
    assert order == sorted(order)
    manifest = (fast["fit"] / KEPT_INDEX_DIR / MANIFEST).read_text(encoding="utf-8")
    for row in kept:  # Public kept membership only: no group ledgers.
        assert row["duplicate_group"] not in manifest


def _resign(path: Path, change: dict[str, Any]) -> None:
    envelope = load(path.read_bytes())
    body = {k: v for k, v in envelope["payload"].items() if k != "issuer"}
    body.update(change)
    path.unlink()
    canonical.write_canonical_json(path, signed(body, ISSUER, KEY.encode()))


@pytest.mark.parametrize(
    "change",
    [
        {"membership_sha256": "0" * 64},
        {"plan_digest": "0" * 64},
        {"completion_digest": "0" * 64},
        {"files_digest": "0" * 64},
        {"kept": 1},
    ],
)
def test_stale_index_bindings_refuse(
    c05: dict[str, Any], fast: dict[str, Any], tmp_path: Path, change: dict[str, Any]
) -> None:
    copy = tmp_path / "index"
    shutil.copytree(fast["fit"] / KEPT_INDEX_DIR, copy)
    _resign(copy / MANIFEST, change)
    view = open_streamed(c05["proof"], allow_authored=True, consumes=[])
    with pytest.raises(C05Error, match="stale|section"):
        open_index(copy, view)


def test_index_section_and_source_staleness_refuse(
    c05: dict[str, Any], fast: dict[str, Any], tmp_path: Path, reference: dict[str, Any]
) -> None:
    view = open_streamed(c05["proof"], allow_authored=True, consumes=[])
    policy = load_fit_policy(reference["policy"])[0]
    result = verify_kept_index(
        view,
        fast["fit"] / KEPT_INDEX_DIR,
        policy,
        c05["quotas"],
        c05["ifm"],
        membership=True,
        sources=True,
        workers=2,
    )
    assert result["membership_rederived"] and result["sources_rehashed"]
    copy = tmp_path / "index"
    shutil.copytree(fast["fit"] / KEPT_INDEX_DIR, copy)
    rows = copy / "rows.bin"
    rows.write_bytes(_flip(rows.read_bytes(), 40))
    with pytest.raises(C05Error, match="section changed"):
        open_index(copy, view)
    with mutated(_plan_file(c05, 2), lambda d: _flip(d, len(d) // 3)):
        with pytest.raises(C05Error, match="input content changed"):
            verify_kept_index(
                view,
                fast["fit"] / KEPT_INDEX_DIR,
                policy,
                c05["quotas"],
                c05["ifm"],
                sources=True,
                membership=True,
                workers=1,
            )


# -- verification / progress / CLI ---------------------------------------------------------


def test_verify_fit_fast_rederives_everything(
    c05: dict[str, Any], fast: dict[str, Any], reference: dict[str, Any], tmp_path: Path
) -> None:
    view = open_streamed(c05["proof"], allow_authored=True, consumes=[])
    policy = load_fit_policy(reference["policy"])[0]
    result = verify_fit_fast(
        view, fast["fit"], policy, c05["quotas"], c05["ifm"], workers=2, sources=True
    )
    assert result["verified"] and result["sources_rehashed"]
    copy = tmp_path / "fit"
    shutil.copytree(fast["fit"], copy)
    sample = copy / FIT_SAMPLE
    lines = sample.read_bytes().splitlines(keepends=True)
    sample.write_bytes(b"".join(lines[1:]))
    with pytest.raises(C05Error, match="sample differs"):
        verify_fit_fast(view, copy, policy, c05["quotas"], c05["ifm"], workers=1)


def test_fast_progress_is_staged_content_free_and_reports_slo(
    c05: dict[str, Any], fast: dict[str, Any]
) -> None:
    text = fast["progress"]
    for stage in (
        "MEMBERSHIP STREAM",
        "SAMPLE SELECT",
        "SOURCE PASS",
        "INDEX WRITE",
        "TOKENIZER FIT",
        "TOKENIZER SAVE",
        "VERIFY",
        "COMPLETE",
    ):
        assert f"[C06] {stage} |" in text, stage
    assert "SLO | membership" in text and "SLO | source" in text and "SLO | total" in text
    assert "MB/s" in text and "rows/s" in text and "GB/s" in text
    assert not re.search(r"[0-9a-f]{16}", text)
    assert not re.search(r"\bw[0-9a-f]{7}\b", text)
    assert str(c05["root"]) not in text and ":\\" not in text
    plan = load((fast["fit"] / "tokenizer_fit_resource_plan.json").read_bytes())
    measured = plan["measured"]
    assert measured["deadline_seconds"] == 1200 and measured["bpe_seconds"] > 0
    assert plan["plan"]["inputs"]["source_passes"] == 1
    assert plan["plan"]["inputs"]["sqlite_queries"] == 0


def test_operator_cli_fast_plan_fit_verify_index_count_select(
    c05: dict[str, Any],
    reference: dict[str, Any],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from xlm.data.exclusion.operator import main as operator

    common = [
        "--c05-proof",
        str(c05["proof"]),
        "--fit-shares",
        str(reference["policy"]),
        "--quotas",
        str(c05["quotas"]),
        "--ifm-split",
        str(c05["ifm"]),
    ]
    fit_args = [
        "fit-tokenizer",
        *common,
        "--scratch",
        str(tmp_path / "scratch"),
        "--output",
        str(tmp_path / "fit"),
        "--deficit-report",
        str(tmp_path / "deficit.json"),
        "--workers",
        "2",
        "--bpe-threads",
        "8",
        "--deadline-seconds",
        "1200",
        "--free-reserve-gib",
        "0",
        "--no-progress",
    ]
    assert operator([*fit_args, "--plan-only"]) == 0
    planned = json.loads(capsys.readouterr().out)
    assert planned["resource_plan"]["kind"] == fitfast.RESOURCE_PLAN_KIND
    signing = ["--issuer", ISSUER, "--key-env", KEY_ENV]
    assert operator([*fit_args, *signing, "--resource-plan-digest", "0" * 64]) == 1
    capsys.readouterr()
    assert (
        operator(
            [
                *fit_args,
                *signing,
                "--resource-plan-digest",
                planned["resource_plan_digest"],
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert_equivalent(reference["fit"], tmp_path / "fit")
    assert (
        operator(
            [
                "verify-tokenizer-fit",
                *common,
                "--fit",
                str(tmp_path / "fit"),
                "--sources",
                "--workers",
                "2",
                "--no-progress",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["sources_rehashed"] is True
    assert (
        operator(["verify-tokenizer-fit", *common, "--fit", str(reference["fit"]), "--no-progress"])
        == 0
    )
    capsys.readouterr()
    assert (
        operator(
            [
                "verify-kept-index",
                *common,
                "--index",
                str(tmp_path / "fit" / KEPT_INDEX_DIR),
                "--sources",
                "--workers",
                "2",
                "--no-progress",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["sources_rehashed"] is True
    tokenizer = str(tmp_path / "fit" / TOKENIZER_DIR)
    chain = ["--c05-proof", str(c05["proof"]), "--tokenizer", tokenizer]
    assert (
        operator(
            [
                "count-tokens",
                *chain,
                "--scratch",
                str(tmp_path / "s"),
                "--output",
                str(tmp_path / "counts"),
                *signing,
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert (
        operator(
            [
                "select",
                *chain,
                "--scratch",
                str(tmp_path / "s"),
                "--counts",
                str(tmp_path / "counts"),
                "--quotas",
                str(c05["quotas"]),
                "--ifm-split",
                str(c05["ifm"]),
                "--deficit-report",
                str(tmp_path / "select-deficit.json"),
                "--output",
                str(tmp_path / "selection"),
                *signing,
            ]
        )
        == 0
    )


# -- exact count-only API ------------------------------------------------------------------

COUNT_TEXTS = [
    "",
    " ",
    "a",
    "\n",
    "\n\n\t  \r\n",
    "Hello, world!",
    "ASCII only text with numbers 12345 and symbols !@#$%^&*()",
    "François naïve café — über Straße",
    "é combining à marks ỗ and NFC é",
    "emoji \U0001f680\U0001f469‍\U0001f4bb\U0001f1ef\U0001f1f5 mixed",
    "def f(x: int) -> int:\n    return x + 42  # code\n",
    "<eos>",
    "<bos><eos><pad><unk>",
    "literal <eos> inside <bos> text <pad> and <unk>.",
    "中文文本 日本語 한국어 العربية",
    "multi\nline\ntext\n\nwith blank lines\n",
    "x" * 20_000,
    " ".join(f"word{i}" for i in range(5_000)),
]


def _random_texts(count: int) -> list[str]:
    rng = random.Random(7)
    alphabet = [chr(c) for c in range(32, 127)] + list("éüß́̀中文\U0001f680‍\n\t <>/")
    return ["".join(rng.choice(alphabet) for _ in range(rng.randint(0, 400))) for _ in range(count)]


def _reference_counts(tokenizer: Any, texts: list[str]) -> list[int]:
    return [max(0, len(tokenizer.encode_with_offsets(t, True)[0]) - 1) for t in texts]


def test_count_only_api_equals_encode_with_offsets(
    reference: dict[str, Any], tmp_path: Path
) -> None:
    texts = COUNT_TEXTS + _random_texts(2_000)
    fitted = ByteLevelBPETokenizer.load(reference["fit"] / TOKENIZER_DIR)
    assert fitted.count_valid_targets(texts) == _reference_counts(fitted, texts)
    from test_performance_tokenization import document

    corpus = [document(t, i) for i, t in enumerate(COUNT_TEXTS[5:] * 30 + _random_texts(300))]
    larger = ByteLevelBPETokenizer.train_from_documents(corpus, target_vocab_size=1200)
    assert larger.actual_vocab_size > 600
    assert larger.count_valid_targets(texts) == _reference_counts(larger, texts)
    from xlm.tokenizers.byte import ByteTokenizer

    byte = ByteTokenizer()
    assert byte.count_valid_targets(texts) == _reference_counts(byte, texts)
    assert fitted.count_valid_targets([]) == []


def test_count_only_api_handles_tokens_equal_to_framing_ids() -> None:
    """A backend whose content can start/end with BOS/EOS ids keeps the exact framing."""
    from tokenizers import Tokenizer
    from tokenizers.models import WordLevel
    from tokenizers.pre_tokenizers import Whitespace

    backend = Tokenizer(
        WordLevel({"<pad>": 0, "<bos>": 1, "<eos>": 2, "<unk>": 3, "a": 4}, unk_token="<unk>")
    )
    backend.pre_tokenizer = Whitespace()
    from tokenizers import AddedToken

    backend.add_special_tokens(
        [AddedToken("<bos>", special=True), AddedToken("<eos>", special=True)]
    )
    tokenizer = ByteLevelBPETokenizer(backend, target_vocab_size=5)
    texts = ["<bos> a <eos>", "<bos>", "<eos>", "a", "", "<bos> <eos>"]
    assert tokenizer.count_valid_targets(texts) == _reference_counts(tokenizer, texts)


def test_spool_training_equals_document_training(tmp_path: Path) -> None:
    from test_performance_tokenization import document
    from xlm.tokenizers.bpe import fit_input_line, write_fit_frame

    docs = [document(t, i) for i, t in enumerate(COUNT_TEXTS[1:] * 5)]
    direct = ByteLevelBPETokenizer.train_from_documents(docs, target_vocab_size=600)
    spool = tmp_path / "spool"
    digest = hashlib.sha256()
    size = 0
    with spool.open("xb") as stream:
        for n, doc in enumerate(docs):
            size += write_fit_frame(stream, doc)
            if n:
                digest.update(b"\n")
            digest.update(fit_input_line(doc))
    trained = ByteLevelBPETokenizer.train_from_spool(
        spool, 600, digest.hexdigest(), documents=len(docs), spool_bytes=size
    )
    direct.save(tmp_path / "a")
    trained.save(tmp_path / "b")
    for name in ("tokenizer.json", "tokenizer_manifest.json"):
        assert (tmp_path / "a" / name).read_bytes() == (tmp_path / "b" / name).read_bytes()
    with pytest.raises(ValueError):
        ByteLevelBPETokenizer.train_from_spool(
            spool, 600, digest.hexdigest(), documents=len(docs) - 1, spool_bytes=size
        )


def test_production_audit_partition_fixture_is_in_effect(c05: dict[str, Any]) -> None:
    assert ProductionPolicy  # The shared c05 fixture enables a real audit partition.
    view = open_streamed(c05["proof"], allow_authored=True, consumes=[])
    splits = {
        load(x)["split"] for x in (view.directory / "membership.jsonl").read_bytes().splitlines()
    }
    assert splits == {"train", "diagnostic_val", "audit"}
