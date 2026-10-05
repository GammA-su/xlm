"""select FAST path: exact reference equivalence, integrity refusals, lifecycle, progress.

Authored fixtures only: the generated C05 flow (17 allocations; planted excluded and
duplicate records; diagnostic_val and audit partitions; ``test_c06_tokenizer_fit.c05``),
a tiny BPE fitted on its screened train records, and exact counts from count-tokens.
Counts edited by a test are re-signed with the synthetic trusted issuer, so only the
row-content checks can refuse them. No real corpus, proof, tokenizer, key or network.
"""

# ruff: noqa: F811  (pytest fixtures imported from other test modules)

from __future__ import annotations

import hashlib
import io
import json
import os
import random
import re
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from scripts.c05_authored_pilot import KEY
from scripts.c05_synthetic_flow import ISSUER, KEY_ENV

from test_c06_tokenizer_fit import c05, key  # noqa: F401 (fixtures)
from test_count_tokens_fast import tok  # noqa: F401 (fixture)
from xlm.data.evidence_v2 import canonical
from xlm.data.exclusion import selectfast
from xlm.data.exclusion.artifacts import signed
from xlm.data.exclusion.countfast import count_tokens_fast
from xlm.data.exclusion.inputs import read_metadata
from xlm.data.exclusion.operator import main as operator
from xlm.data.exclusion.policy import C05Error
from xlm.data.exclusion.progress import RunProgress
from xlm.data.exclusion.runner import file_sha
from xlm.data.exclusion.selectfast import (
    ARTIFACTS,
    count_head,
    json_string,
    rank_digest,
    rank_heads,
    select_by_buckets,
    select_fast,
    selected_row,
)
from xlm.data.exclusion.selection import SelectionDeficit, SelectionPolicy, allocation_key, select
from xlm.data.exclusion.transport import open_gate

load = canonical.loads_bytes_strict
STAGES = [
    "PROOF VERIFY",
    "OUTPUT PREFLIGHT",
    "TOKENIZER VERIFY",
    "COUNTS VERIFY",
    "MEMBERSHIP VERIFY",
    "RANK PASS",
    "QUOTA CROSSINGS",
    "CROSSING SORT",
    "EXPORT SELECTION",
    "FSYNC",
    "SIGN",
    "VERIFY",
    "PUBLISH",
    "COMPLETE",
]


# -- fixtures and helpers -----------------------------------------------------------------


def artifacts(directory: Path) -> dict[str, bytes]:
    return {name: (directory / name).read_bytes() for name in ARTIFACTS}


def reference_outcome(c05: dict[str, Any], tok: Path, counts: Path, out: Path) -> tuple[str, Any]:
    with open_gate(c05["proof"], allow_authored=True) as gate:
        try:
            select(
                gate,
                counts,
                tok,
                c05["quotas"],
                c05["ifm"],
                out / "selection",
                ISSUER,
                KEY.encode(),
                scratch=out / "scratch",
            )
        except SelectionDeficit as deficit:
            return "deficit", canonical.canonical_bytes(deficit.report)
    return "ok", artifacts(out / "selection")


def run_fast(
    c05: dict[str, Any], tok: Path, counts: Path, out: Path, **options: Any
) -> dict[str, Any]:
    options.setdefault("workers", 1)
    options.setdefault("inline", True)
    return select_fast(
        options.pop("proof", c05["proof"]),
        counts,
        tok,
        c05["quotas"],
        c05["ifm"],
        out / "selection",
        ISSUER,
        KEY.encode(),
        scratch=out / "scratch",
        **options,
    )


def fast_outcome(
    c05: dict[str, Any], tok: Path, counts: Path, out: Path, **options: Any
) -> tuple[str, Any]:
    try:
        run_fast(c05, tok, counts, out, **options)
    except SelectionDeficit as deficit:
        return "deficit", canonical.canonical_bytes(deficit.report)
    return "ok", artifacts(out / "selection")


def assert_nothing_published(out: Path) -> None:
    assert not (out / "selection").exists()
    if out.exists():
        assert not [p.name for p in out.iterdir() if ".partial-" in p.name]
    scratch = out / "scratch"
    assert not scratch.exists() or list(scratch.iterdir()) == []


@pytest.fixture(scope="module")
def chain(
    c05: dict[str, Any], tok: Path, tmp_path_factory: pytest.TempPathFactory
) -> dict[str, Any]:
    root = tmp_path_factory.mktemp("select")
    os.environ[KEY_ENV] = KEY
    count_tokens_fast(
        c05["proof"],
        tok,
        root / "counts",
        ISSUER,
        KEY.encode(),
        scratch=root / "count-scratch",
        workers=1,
        inline=True,
    )
    outcome = reference_outcome(c05, tok, root / "counts", root / "reference")
    assert outcome[0] == "ok"
    spec = read_metadata(c05["proof"], digested=False)
    membership = [
        load(r) for r in (Path(spec["completion"]) / "membership.jsonl").read_bytes().splitlines()
    ]
    decisions = [
        load(r)
        for path in (c05["root"] / "scratch").rglob("decisions.jsonl")
        for r in path.read_bytes().splitlines()
    ]
    return {
        "counts": root / "counts",
        "reference": outcome[1],
        "membership": membership,
        "decisions": decisions,
    }


def resigned(source: Path, target: Path, edit: Callable[[list[bytes]], list[bytes]]) -> Path:
    """A trusted-issuer re-signed copy whose raw count lines are edited."""
    shutil.copytree(source, target)
    lines = (target / "counts.jsonl").read_bytes().splitlines(keepends=True)
    raw = b"".join(edit(lines))
    (target / "counts.jsonl").write_bytes(raw)
    body = load((target / "counts.json").read_bytes())["payload"]
    body.update(
        counts_sha256=file_sha(target / "counts.jsonl"),
        counts_bytes=len(raw),
        documents=len(raw.splitlines()),
    )
    (target / "counts.json").unlink()
    canonical.write_canonical_json(target / "counts.json", signed(body, ISSUER, KEY.encode()))
    return target


def rows_edit(change: Callable[[list[dict[str, Any]]], list[dict[str, Any]]]) -> Any:
    def edit(lines: list[bytes]) -> list[bytes]:
        return [canonical.canonical_bytes(r) + b"\n" for r in change([load(x) for x in lines])]

    return edit


# -- canonical bytes ---------------------------------------------------------------------------

ADVERSARIAL_IDS = [
    "plain:0001",
    'q"uote',
    "back" + chr(92) + "slash",
    "line" + chr(10) + "feed",
    chr(1) + "control" + chr(31),
    "del" + chr(127),
    "東京-ünïcode",
    "sep" + chr(0x2028) + chr(0x2029),
    "emoji" + chr(0x1F600),
    "/" + chr(9) + "tab",
]


@pytest.mark.parametrize("doc_id", ADVERSARIAL_IDS)
def test_canonical_bytes_helpers_match_the_reference_serializers(doc_id: str) -> None:
    content = hashlib.sha256(doc_id.encode()).hexdigest()
    allocation = ["common_pile_prose", "common_pile_prose", "news"]
    fragment = canonical.canonical_bytes(allocation)
    raw_id = doc_id.encode("utf-8")
    assert json_string(raw_id) == canonical.canonical_bytes(doc_id)
    for tokens in (0, 7, 10**17):
        row = {
            "doc_id": doc_id,
            "content": content,
            "allocation": allocation,
            "valid_targets": tokens,
        }
        head = count_head(fragment, content.encode(), json_string(raw_id))
        assert head + str(tokens).encode() + b"}" == canonical.canonical_bytes(row)
        chosen = {
            "doc_id": doc_id,
            "content": content,
            "allocation": allocation,
            "counted_valid_targets": tokens,
            "selected_valid_targets": tokens // 2,
        }
        assert (
            selected_row(fragment, content.encode(), json_string(raw_id), tokens, tokens // 2)
            == canonical.canonical_bytes(chosen) + b"\n"
        )
    key_name = allocation_key(*allocation)
    seed = SelectionPolicy().seed
    (head,) = rank_heads(seed, [key_name])
    expected = canonical.digest([seed, key_name, doc_id, content])
    assert rank_digest(head, json_string(raw_id), content.encode()).hex() == expected


# -- exact bucket selection: property tests against the full (rank, doc id) walk ---------------


def walk_reference(
    allocation: list[int], tokens: list[int], ranks: list[bytes], quotas: list[int]
) -> tuple[list[int], list[tuple[int, ...]]]:
    """``selection._select_allocation`` on in-memory rows: (rank, row) order, full sort."""
    selected = [0] * len(tokens)
    stats: list[tuple[int, ...]] = []
    for code, quota in enumerate(quotas):
        rows = sorted(
            (r for r in range(len(tokens)) if allocation[r] == code), key=lambda r: (ranks[r], r)
        )
        total = documents = truncated = 0
        for row in rows:
            if total == quota:
                break
            if tokens[row] == 0:
                continue
            used = min(tokens[row], quota - total)
            truncated += int(used < tokens[row])
            selected[row] = used
            total += used
            documents += 1
        eligible = [r for r in rows if tokens[r] > 0]
        stats.append((len(eligible), sum(tokens[r] for r in eligible), documents, total, truncated))
    return selected, stats


def generated(rng: random.Random) -> dict[str, Any]:
    codes = rng.randint(1, 17)
    rows = rng.choice((0, 1, 2, 5, 40, 300))
    prefixes = [rng.getrandbits(64).to_bytes(8, "big") for _ in range(rng.randint(1, 6))]
    suffixes = [rng.randbytes(24) for _ in range(rng.randint(1, 4))]
    allocation, tokens, ranks = [], [], []
    for _ in range(rows):
        allocation.append(rng.randrange(codes))
        draw = rng.random()
        tokens.append(
            0 if draw < 0.2 else rng.randint(1, 50) if draw < 0.9 else rng.randint(1, 10**9)
        )
        # Deliberate collisions: shared 64-bit prefixes, shared full ranks, or uniform.
        mode = rng.random()
        if mode < 0.4:
            ranks.append(rng.choice(prefixes) + rng.randbytes(24))
        elif mode < 0.6:
            ranks.append(rng.choice(prefixes) + rng.choice(suffixes))
        else:
            ranks.append(rng.randbytes(32))
    quotas = []
    for code in range(codes):
        total = sum(t for a, t in zip(allocation, tokens, strict=True) if a == code)
        quotas.append(
            rng.choice((0, 1, total, total + 1, max(1, total // 2), rng.randint(1, total + 5)))
        )
    return {"allocation": allocation, "tokens": tokens, "ranks": ranks, "quotas": quotas}


@pytest.mark.parametrize("bits", [1, 2, 3, 8, 16, 24])
def test_bucket_selection_equals_full_rank_order_on_random_datasets(bits: int) -> None:
    rng = random.Random(20261005 + bits)
    for _ in range(250):
        data = generated(rng)
        expected, stats = walk_reference(
            data["allocation"], data["tokens"], data["ranks"], data["quotas"]
        )
        ranks = data["ranks"]
        chosen, results = select_by_buckets(
            np.asarray(data["allocation"], dtype=np.uint16),
            np.asarray(data["tokens"], dtype=np.int64),
            np.asarray([int.from_bytes(r[:8], "big") for r in ranks], dtype=np.uint64),
            data["quotas"],
            lambda rows, ranks=ranks: [ranks[r] for r in rows.tolist()],
            bits=bits,
        )
        assert chosen.tolist() == expected
        assert [
            (
                r.eligible_documents,
                r.eligible_valid_targets,
                r.selected_documents,
                r.selected_valid_targets,
                r.truncated_documents,
            )
            for r in results
        ] == stats


def test_bucket_selection_prefix_collisions_ties_zero_tokens_and_truncation() -> None:
    shared = bytes(8)
    # Every row shares one 64-bit prefix (one bucket at any width); rows 1 and 3 share a
    # full rank (doc-id order decides); row 0 has zero tokens and the lowest rank.
    ranks = [
        shared + bytes(24),
        shared + b"\x05" * 24,
        shared + b"\x09" * 24,
        shared + b"\x05" * 24,
    ]
    tokens = [0, 10, 10, 10]
    chosen, results = select_by_buckets(
        np.zeros(4, dtype=np.uint16),
        np.asarray(tokens, dtype=np.int64),
        np.zeros(4, dtype=np.uint64),
        [15],
        lambda rows: [ranks[r] for r in rows.tolist()],
    )
    # Walk: row 0 skipped (zero), row 1 whole, row 3 (same rank, later id) truncated to 5.
    assert chosen.tolist() == [0, 10, 0, 5]
    report = results[0].report()
    assert report == {
        "quota": 15,
        "eligible_documents": 3,
        "eligible_valid_targets": 30,
        "selected_documents": 2,
        "selected_valid_targets": 15,
        "truncated_documents": 1,
        "deficit": 0,
        "status": "EXACT",
    }
    assert results[0].crossing_candidates == 3
    # Deficit: everything eligible is taken whole, nothing truncated.
    chosen, results = select_by_buckets(
        np.zeros(4, dtype=np.uint16),
        np.asarray(tokens, dtype=np.int64),
        np.zeros(4, dtype=np.uint64),
        [31],
        lambda rows: [ranks[r] for r in rows.tolist()],
    )
    assert chosen.tolist() == tokens and results[0].report()["status"] == "DEFICIT"
    assert results[0].report()["deficit"] == 1 and results[0].crossing_candidates == 0


def test_crossing_bucket_is_a_small_fraction_of_a_large_allocation() -> None:
    rng = np.random.default_rng(7)
    rows = 200_000
    tokens = rng.integers(0, 2_000, rows, dtype=np.int64)
    prefix = rng.integers(0, np.iinfo(np.uint64).max, rows, dtype=np.uint64)
    quota = int(tokens.sum() // 3)
    sorted_rows: list[int] = []

    def ranks(chosen: Any) -> list[bytes]:
        sorted_rows.extend(chosen.tolist())
        return [int(prefix[r]).to_bytes(8, "big") + bytes(24) for r in chosen.tolist()]

    chosen, results = select_by_buckets(
        np.zeros(rows, dtype=np.uint16), tokens, prefix, [quota], ranks
    )
    assert int(chosen.sum()) == quota and results[0].report()["status"] == "EXACT"
    assert 0 < len(sorted_rows) < 50  # about rows / 65,536 rows are fully sorted
    order = np.lexsort((np.arange(rows), prefix))
    walk = np.cumsum(np.where(tokens[order] > 0, tokens[order], 0))
    stop = int(np.searchsorted(walk, quota, side="left"))
    assert set(np.flatnonzero(chosen).tolist()) == {
        int(r) for r in order[: stop + 1] if tokens[r] > 0
    }


# -- equivalence with the reference --------------------------------------------------------


def test_fast_is_byte_identical_to_the_reference(
    c05: dict[str, Any], tok: Path, chain: dict[str, Any], tmp_path: Path
) -> None:
    # Tiny rank blocks and export tasks: hundreds of ordered tasks, multi-line blocks.
    envelope = run_fast(c05, tok, chain["counts"], tmp_path, block_bytes=700, export_rows=3)
    assert artifacts(tmp_path / "selection") == chain["reference"]
    payload = envelope["payload"]
    assert len(payload["allocations"]) == 17  # the 17-allocation shape
    assert sum(a["truncated_documents"] for a in payload["allocations"].values()) >= 1
    assert all(a["status"] == "EXACT" for a in payload["allocations"].values())
    assert payload["selected_valid_targets"] == sum(
        a["selected_valid_targets"] for a in payload["allocations"].values()
    )
    train = sum(1 for r in chain["membership"] if r["split"] == "train")
    assert train < len(chain["membership"])  # kept non-train rows exist and are skipped
    assert_nothing_published_except_selection(tmp_path)


def assert_nothing_published_except_selection(out: Path) -> None:
    assert sorted(p.name for p in out.iterdir()) == ["scratch", "selection"]
    assert sorted(p.name for p in (out / "selection").iterdir()) == sorted(ARTIFACTS)
    assert list((out / "scratch").iterdir()) == []


@pytest.mark.parametrize("workers", [1, 2, 4, 8, 16])
def test_spawned_workers_are_byte_identical(
    c05: dict[str, Any], tok: Path, chain: dict[str, Any], tmp_path: Path, workers: int
) -> None:
    run_fast(
        c05,
        tok,
        chain["counts"],
        tmp_path,
        workers=workers,
        inline=False,
        block_bytes=2048,
        export_rows=16,
    )
    assert artifacts(tmp_path / "selection") == chain["reference"]
    assert_nothing_published_except_selection(tmp_path)


def cli(
    command: str, c05: dict[str, Any], tok: Path, counts: Path, out: Path, *extra: str
) -> list[str]:
    return [
        command,
        "--c05-proof",
        str(c05["proof"]),
        "--tokenizer",
        str(tok),
        "--scratch",
        str(out / "scratch"),
        "--counts",
        str(counts),
        "--quotas",
        str(c05["quotas"]),
        "--ifm-split",
        str(c05["ifm"]),
        "--deficit-report",
        str(out / "deficit.json"),
        "--output",
        str(out / "selection"),
        "--issuer",
        ISSUER,
        "--key-env",
        KEY_ENV,
        *extra,
    ]


def test_cli_select_matches_select_reference(
    c05: dict[str, Any],
    tok: Path,
    chain: dict[str, Any],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    capsys.readouterr()
    assert operator(cli("select-reference", c05, tok, chain["counts"], tmp_path / "r")) == 0
    reference = capsys.readouterr()
    out = tmp_path / "f"
    assert (
        operator(cli("select", c05, tok, chain["counts"], out, "--workers", "2", "--no-progress"))
        == 0
    )
    fast = capsys.readouterr()
    assert fast.out == reference.out and len(fast.out.splitlines()) == 1
    assert set(json.loads(fast.out)) == {"digest", "mode"}
    assert fast.err == ""  # --no-progress: nothing on stderr
    assert artifacts(out / "selection") == artifacts(tmp_path / "r" / "selection")
    assert artifacts(out / "selection") == chain["reference"]
    assert not (out / "deficit.json").exists()


@pytest.mark.parametrize("fmt", ["text", "jsonl"])
def test_progress_is_stderr_only_staged_and_content_free(
    c05: dict[str, Any],
    tok: Path,
    chain: dict[str, Any],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    fmt: str,
) -> None:
    capsys.readouterr()
    args = cli(
        "select",
        c05,
        tok,
        chain["counts"],
        tmp_path,
        "--workers",
        "1",
        "--progress-interval",
        "0.001",
        "--progress-format",
        fmt,
    )
    assert operator(args) == 0
    captured = capsys.readouterr()
    assert set(json.loads(captured.out)) == {"digest", "mode"}
    lines = captured.err.splitlines()
    if fmt == "jsonl":
        events = [json.loads(line) for line in lines]
        stages = [e["stage"] for e in events if e["event"] in ("stage", "complete")]
        rank = [e for e in events if e.get("stage") == "RANK PASS" and e["event"] == "finish"]
        assert rank and rank[-1]["done"] == rank[-1]["total"]
        assert {"bytes_done", "bytes_total", "workers"} <= set(rank[-1]["fields"])
    else:
        assert all(line.startswith("[SELECT] ") for line in lines)
        stages = [
            line.removeprefix("[SELECT] ").split(" | ")[0]
            for line in lines
            if " | started" in line or line.startswith("[SELECT] COMPLETE")
        ]
        assert any("ETA " in line for line in lines) and any("rows/s" in line for line in lines)
    assert stages == STAGES
    # No doc id, content digest, rank, path or text reaches the progress stream.
    text = captured.err
    assert not re.search(r"[0-9a-f]{32,}", text)
    assert str(tmp_path) not in text and str(c05["root"]) not in text
    for row in chain["membership"]:
        assert row["doc_id"] not in text


def test_progress_object_is_display_only(
    c05: dict[str, Any], tok: Path, chain: dict[str, Any], tmp_path: Path
) -> None:
    stream = io.StringIO()
    run_fast(
        c05,
        tok,
        chain["counts"],
        tmp_path,
        progress=RunProgress(interval=0.001, stream=stream, fmt="jsonl", label="SELECT"),
    )
    assert artifacts(tmp_path / "selection") == chain["reference"]
    assert stream.getvalue().count('"event": "complete"') == 1


# -- edited (re-signed) counts: identical outcomes, or a refusal --------------------------


def test_zero_token_documents_select_identically(
    c05: dict[str, Any], tok: Path, chain: dict[str, Any], tmp_path: Path
) -> None:
    def zero(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [{**r, "valid_targets": 0} if n % 4 == 1 else r for n, r in enumerate(rows)]

    counts = resigned(chain["counts"], tmp_path / "counts", rows_edit(zero))
    reference = reference_outcome(c05, tok, counts, tmp_path / "r")
    assert fast_outcome(c05, tok, counts, tmp_path / "f") == reference
    assert reference[0] == "ok" and reference[1] != chain["reference"]


def test_deficit_report_is_byte_identical_and_nothing_is_published(
    c05: dict[str, Any],
    tok: Path,
    chain: dict[str, Any],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def scarce(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {**r, "valid_targets": 1} if r["allocation"][0] != "finewiki_en" else r for r in rows
        ]

    counts = resigned(chain["counts"], tmp_path / "counts", rows_edit(scarce))
    reference = reference_outcome(c05, tok, counts, tmp_path / "r")
    assert reference[0] == "deficit"
    assert fast_outcome(c05, tok, counts, tmp_path / "f", workers=2, inline=False) == reference
    assert_nothing_published(tmp_path / "f")
    reports = {}
    for command in ("select-reference", "select"):
        out = tmp_path / command
        capsys.readouterr()
        assert (
            operator(
                cli(
                    command,
                    c05,
                    tok,
                    counts,
                    out,
                    *(("--no-progress",) if command == "select" else ()),
                )
            )
            == 2
        )
        assert json.loads(capsys.readouterr().out) == {
            "deficit": True,
            "report": str(out / "deficit.json"),
        }
        assert not (out / "selection").exists()
        reports[command] = (out / "deficit.json").read_bytes()
    assert reports["select"] == reports["select-reference"]
    report = load(reports["select"])
    assert report["kind"] == "c05_selection_deficit_v1"
    assert any(a["status"] == "DEFICIT" for a in report["allocations"].values())


def _kept_non_train(chain: dict[str, Any]) -> dict[str, Any]:
    row = next(r for r in chain["membership"] if r["split"] != "train")
    return {
        "doc_id": row["doc_id"],
        "content": row["content"],
        "allocation": [row["component"], row["view"], row["upstream_component"]],
        "valid_targets": 10_000,
    }


def _excluded(chain: dict[str, Any]) -> dict[str, Any]:
    row = next(r for r in chain["decisions"] if r["decision"] == "excluded")
    return {
        "doc_id": row["doc_id"],
        "content": row["content"],
        "allocation": [row["component"], row["view"], row["upstream_component"]],
        "valid_targets": 10_000,
    }


def _insert(row: dict[str, Any]) -> Any:
    return lambda rows: sorted([*rows, row], key=lambda r: r["doc_id"].encode("utf-8"))


def _content(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows[5] = {**rows[5], "content": "0" * 64}
    return rows


def _allocation(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    other = next(r["allocation"] for r in rows if r["allocation"] != rows[5]["allocation"])
    rows[5] = {**rows[5], "allocation": other}
    return rows


REFUSALS: dict[str, tuple[Any, str]] = {
    "missing": (
        rows_edit(lambda rows: rows[:7] + rows[8:]),
        "omit or reorder a kept training record",
    ),
    "missing-last": (rows_edit(lambda rows: rows[:-1]), "do not cover every kept training record"),
    "duplicate": (
        rows_edit(lambda rows: rows[:8] + [rows[7]] + rows[8:]),
        "repeated or out-of-order",
    ),
    "extra-last": (lambda lines: [*lines, lines[-1]], "beyond kept training membership"),
    "changed-content": (rows_edit(_content), "differs from C05 kept membership"),
    "wrong-allocation": (rows_edit(_allocation), "allocation differs from C05 membership"),
    "negative": (
        rows_edit(lambda rows: [{**rows[0], "valid_targets": -1}, *rows[1:]]),
        "count record value",
    ),
    "schema": (rows_edit(lambda rows: [{**rows[0], "extra": 1}, *rows[1:]]), "count record schema"),
    "not-json": (lambda lines: [b"{not json}\n", *lines[1:]], "not strict canonical JSON"),
}


@pytest.mark.parametrize("case", sorted(REFUSALS))
def test_count_row_refusals(
    c05: dict[str, Any], tok: Path, chain: dict[str, Any], tmp_path: Path, case: str
) -> None:
    edit, message = REFUSALS[case]
    counts = resigned(chain["counts"], tmp_path / "counts", edit)
    with pytest.raises(C05Error, match=message) as raised:
        run_fast(c05, tok, counts, tmp_path / "f", block_bytes=1024)
    assert getattr(raised.value, "select_stage", None) == "RANK PASS"
    assert_nothing_published(tmp_path / "f")
    with pytest.raises(Exception):  # noqa: B017 - the reference refuses too (any reason)
        reference_outcome(c05, tok, counts, tmp_path / "r")


@pytest.mark.parametrize("kind", ["excluded", "non-train"])
def test_records_outside_kept_train_refuse(
    c05: dict[str, Any], tok: Path, chain: dict[str, Any], tmp_path: Path, kind: str
) -> None:
    row = _excluded(chain) if kind == "excluded" else _kept_non_train(chain)
    message = "not covered by C05" if kind == "excluded" else "non-training record"
    counts = resigned(chain["counts"], tmp_path / "counts", rows_edit(_insert(row)))
    with pytest.raises(C05Error, match=message):
        run_fast(c05, tok, counts, tmp_path / "f")
    assert_nothing_published(tmp_path / "f")
    with pytest.raises(C05Error, match=message):
        reference_outcome(c05, tok, counts, tmp_path / "r")


@pytest.mark.parametrize("case", ["unordered", "non-canonical"])
def test_fast_refuses_what_no_count_path_writes_never_reinterprets(
    c05: dict[str, Any], tok: Path, chain: dict[str, Any], tmp_path: Path, case: str
) -> None:
    """The reference accepts these (same selection); the fast path refuses them."""

    def unordered(lines: list[bytes]) -> list[bytes]:
        return [lines[1], lines[0], *lines[2:]]

    def spaced(lines: list[bytes]) -> list[bytes]:
        return [lines[0].replace(b'":', b'": ', 1), *lines[1:]]

    counts = resigned(
        chain["counts"], tmp_path / "counts", unordered if case == "unordered" else spaced
    )
    reference = reference_outcome(c05, tok, counts, tmp_path / "r")
    assert reference[0] == "ok"
    assert reference[1]["selected.jsonl"] == chain["reference"]["selected.jsonl"]
    message = "omit or reorder" if case == "unordered" else "not canonical bytes"
    with pytest.raises(C05Error, match=message):
        run_fast(c05, tok, counts, tmp_path / "f")
    assert_nothing_published(tmp_path / "f")


def test_unsigned_count_changes_refuse(
    c05: dict[str, Any], tok: Path, chain: dict[str, Any], tmp_path: Path
) -> None:
    counts = tmp_path / "counts"
    shutil.copytree(chain["counts"], counts)
    raw = bytearray((counts / "counts.jsonl").read_bytes())
    at = raw.index(b'"valid_targets":') + len(b'"valid_targets":')
    raw[at] = ord("1") if raw[at] != ord("1") else ord("2")  # same size, canonical row
    (counts / "counts.jsonl").write_bytes(bytes(raw))
    with pytest.raises(C05Error, match="exact count artifact changed") as raised:
        run_fast(c05, tok, counts, tmp_path / "f")
    assert getattr(raised.value, "select_stage", None) == "RANK PASS"
    assert_nothing_published(tmp_path / "f")
    (counts / "counts.jsonl").write_bytes(bytes(raw) + b"\n")
    with pytest.raises(C05Error, match="exact count artifact changed") as raised:
        run_fast(c05, tok, counts, tmp_path / "g")
    assert getattr(raised.value, "select_stage", None) == "COUNTS VERIFY"
    with pytest.raises(C05Error, match="exact count artifact changed"):
        reference_outcome(c05, tok, counts, tmp_path / "r")


def test_wrong_proof_tokenizer_or_signer_refuse(
    c05: dict[str, Any], tok: Path, chain: dict[str, Any], tmp_path: Path
) -> None:
    from xlm.tokenizers.byte import ByteTokenizer

    spec = read_metadata(c05["proof"], digested=False)
    canonical.write_canonical_json(tmp_path / "proof.json", {**spec, "completion_digest": "0" * 64})
    with pytest.raises(C05Error, match="C05 completion changed"):
        run_fast(c05, tok, chain["counts"], tmp_path / "a", proof=tmp_path / "proof.json")
    ByteTokenizer().save(tmp_path / "byte")
    with pytest.raises(C05Error, match="different tokenizer"):
        run_fast(c05, tmp_path / "byte", chain["counts"], tmp_path / "b")
    assert_nothing_published(tmp_path / "b")
    rebound = tmp_path / "rebound"
    shutil.copytree(tok, rebound)
    binding = load((rebound / "c05-binding.json").read_bytes())
    (rebound / "c05-binding.json").write_bytes(
        canonical.canonical_bytes({**binding, "completion_digest": "0" * 64})
    )
    with pytest.raises(C05Error, match="different C05 membership"):
        run_fast(c05, rebound, chain["counts"], tmp_path / "c")
    with pytest.raises(C05Error, match="signer is not trusted"):
        select_fast(
            c05["proof"],
            chain["counts"],
            tok,
            c05["quotas"],
            c05["ifm"],
            tmp_path / "d" / "selection",
            ISSUER,
            b"wrong-key",
            scratch=tmp_path / "d" / "s",
            workers=1,
            inline=True,
        )
    with pytest.raises(C05Error, match="workers must be"):
        run_fast(c05, tok, chain["counts"], tmp_path / "e", workers=3)


# -- lifecycle: preflight, cleanup, interruption ------------------------------------------


def test_missing_output_parent_is_created_like_the_reference(
    c05: dict[str, Any], tok: Path, chain: dict[str, Any], tmp_path: Path
) -> None:
    out = tmp_path / "G" / "XLM" / "selection-parent"
    select_fast(
        c05["proof"],
        chain["counts"],
        tok,
        c05["quotas"],
        c05["ifm"],
        out / "selection",
        ISSUER,
        KEY.encode(),
        scratch=tmp_path / "s",
        workers=1,
        inline=True,
    )
    assert artifacts(out / "selection") == chain["reference"]
    assert sorted(p.name for p in out.iterdir()) == ["selection"]


def test_output_preflight_refuses_before_any_membership_or_count_work(
    c05: dict[str, Any],
    tok: Path,
    chain: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[str] = []
    for name in ("stream_membership", "rank_pass", "tokenizer_identity"):
        original = getattr(selectfast, name)

        def spy(*args: Any, _name: str = name, _original: Any = original, **kwargs: Any) -> Any:
            calls.append(_name)
            return _original(*args, **kwargs)

        monkeypatch.setattr(selectfast, name, spy)
    blocker = tmp_path / "not-a-directory"
    blocker.write_bytes(b"x")
    args = cli("select", c05, tok, chain["counts"], tmp_path, "--workers", "1", "--no-progress")
    args[args.index("--output") + 1] = str(blocker / "selection")
    capsys.readouterr()
    assert operator(args) == 1
    refusal = json.loads(capsys.readouterr().out)
    assert refusal["refused"] is True and refusal["stage"] == "OUTPUT PREFLIGHT"
    assert str(tmp_path) not in json.dumps(refusal) and calls == []

    (tmp_path / "old-deficit.json").write_bytes(b"{}")
    with pytest.raises(C05Error, match="deficit report already exists") as raised:
        run_fast(
            c05, tok, chain["counts"], tmp_path / "d", deficit_report=tmp_path / "old-deficit.json"
        )
    assert getattr(raised.value, "select_stage", None) == "OUTPUT PREFLIGHT" and calls == []

    def no_links(*_: Any) -> None:
        raise OSError(1, "hard links unsupported")

    monkeypatch.setattr(selectfast.os, "link", no_links)
    with pytest.raises(OSError) as failed:
        run_fast(c05, tok, chain["counts"], tmp_path / "nolink")
    assert getattr(failed.value, "select_stage", None) == "OUTPUT PREFLIGHT" and calls == []
    assert_nothing_published(tmp_path / "nolink")


def test_existing_output_refuses_write_once(
    c05: dict[str, Any], tok: Path, chain: dict[str, Any], tmp_path: Path
) -> None:
    (tmp_path / "selection").mkdir()
    with pytest.raises(C05Error, match="write-once"):
        run_fast(c05, tok, chain["counts"], tmp_path)
    assert list((tmp_path / "selection").iterdir()) == []


@pytest.mark.parametrize("where", ["rank", "export"])
@pytest.mark.parametrize("error", [OSError(28, "disk full"), KeyboardInterrupt()])
def test_failure_and_interrupt_remove_every_owned_file(
    c05: dict[str, Any],
    tok: Path,
    chain: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    where: str,
    error: BaseException,
) -> None:
    def broken(*_: Any, **__: Any) -> Any:
        raise error

    monkeypatch.setattr(selectfast, "rank_pass" if where == "rank" else "export_selection", broken)
    with pytest.raises(type(error)) as raised:
        run_fast(c05, tok, chain["counts"], tmp_path)
    if isinstance(error, OSError):
        stage = "RANK PASS" if where == "rank" else "EXPORT SELECTION"
        assert getattr(raised.value, "select_stage", None) == stage
    assert_nothing_published(tmp_path)


def test_spawned_interrupt_reaps_workers_and_cleans_up(
    c05: dict[str, Any],
    tok: Path,
    chain: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import psutil

    original = selectfast.export_selection

    def interrupted(*args: Any, **kwargs: Any) -> Any:
        original(*args, **kwargs)  # the export ran in spawned workers
        raise KeyboardInterrupt

    monkeypatch.setattr(selectfast, "export_selection", interrupted)
    with pytest.raises(KeyboardInterrupt):
        run_fast(c05, tok, chain["counts"], tmp_path, workers=4, inline=False)
    assert psutil.Process().children(recursive=True) == []
    assert_nothing_published(tmp_path)


def test_inputs_changed_during_selection_refuse_before_publication(
    c05: dict[str, Any],
    tok: Path,
    chain: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    counts = tmp_path / "counts"
    shutil.copytree(chain["counts"], counts)
    original = selectfast.export_selection

    def touched(*args: Any, **kwargs: Any) -> Any:
        result = original(*args, **kwargs)
        stat = (counts / "counts.jsonl").stat()
        os.utime(counts / "counts.jsonl", ns=(stat.st_atime_ns, stat.st_mtime_ns + 10**9))
        return result

    monkeypatch.setattr(selectfast, "export_selection", touched)
    with pytest.raises(C05Error, match="changed during selection") as raised:
        run_fast(c05, tok, counts, tmp_path / "f")
    assert getattr(raised.value, "select_stage", None) == "VERIFY"
    assert_nothing_published(tmp_path / "f")
