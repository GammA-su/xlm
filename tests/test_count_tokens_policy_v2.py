"""count-tokens over the c05-production-v3 chain (``c05_membership_v3``) and its C06 fit.

Authored fixtures only (the old/new chains and fits of ``test_c06_policy_v2``); no real
corpus, proof, tokenizer or key. Covers:

* fast path at workers 1/4/8/16 byte-identical to the reference, with and without the
  C06 binding (``--c06-fit``), against an independent oracle that reads only the kept
  rows' identity fields and the source texts;
* only C05 ``train`` kept rows counted: diagnostic_val, audit, excluded and duplicate
  rows absent; ``split_group``/``exclusion_group`` values cannot change counting input;
* the C06 binding: signed ``c06_fit`` digests, kept-index agreement, operator pins
  refusing before output, stale tokenizer/fit/kept-index/C05/output combinations;
* ``verify-counts``: accepts the published artifact, refuses tampering and wrong pins;
* the larger worker BPE cache: identical serialization, fingerprint and counts.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from collections import defaultdict
from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from scripts.c05_synthetic_flow import ISSUER, KEY_ENV

from test_c06_policy_v2 import (  # noqa: F401 (fixtures)
    key,
    new,
    new_fit,
    old,
    old_fit,
    rows_of,
    view_of,
)
from xlm.core.contracts import CanonicalDocument
from xlm.data.evidence_v2 import canonical
from xlm.data.exclusion import fitscan
from xlm.data.exclusion.countfast import count_tables
from xlm.data.exclusion.fitfast import KEPT_INDEX_DIR, verify_kept_index
from xlm.data.exclusion.operator import main as operator
from xlm.data.exclusion.policy import C05Error
from xlm.data.exclusion.selection import allocation_key, load_tokenizer, valid_targets
from xlm.data.exclusion.tokenizer_fit import FIT_MANIFEST, TOKENIZER_DIR
from xlm.tokenizers.bpe import with_bpe_cache

load = canonical.loads_bytes_strict


def digest_of(path: Path) -> str:
    return str(load(path.read_bytes())["digest"])


def bindings(run: dict[str, Any], fit: dict[str, Any]) -> dict[str, str]:
    tokenizer = load((fit["fit"] / TOKENIZER_DIR / "c05-binding.json").read_bytes())
    return {
        "plan": run["plan_digest"],
        "completion": run["completion_digest"],
        "fingerprint": tokenizer["tokenizer_fingerprint"],
        "fit": digest_of(fit["fit"] / FIT_MANIFEST),
        "index": digest_of(fit["fit"] / KEPT_INDEX_DIR / "kept-index.json"),
    }


def train_documents(run: dict[str, Any]) -> int:
    return sum(r["decision"] == "kept" and r["split"] == "train" for r in rows_of(run))


def pins(chain: dict[str, Any], fitted: dict[str, Any], /, **override: Any) -> list[str]:
    values = {**bindings(chain, fitted), "documents": str(train_documents(chain)), **override}
    return [
        "--expect-c05-plan-digest",
        values["plan"],
        "--expect-c05-completion-digest",
        values["completion"],
        "--expect-tokenizer-fingerprint",
        values["fingerprint"],
        "--expect-c06-fit-digest",
        values["fit"],
        "--expect-kept-index-digest",
        values["index"],
        "--expect-documents",
        values["documents"],
    ]


def count_cli(
    command: str,
    run: dict[str, Any],
    tokenizer: Path,
    out: Path,
    *extra: str,
    workers: int | None = None,
) -> list[str]:
    args = [
        command,
        "--c05-proof",
        str(run["proof"]),
        "--tokenizer",
        str(tokenizer),
        "--scratch",
        str(out / "scratch"),
        "--output",
        str(out / "counts"),
        "--issuer",
        ISSUER,
        "--key-env",
        KEY_ENV,
        "--no-progress",
        *extra,
    ]
    if workers is not None:
        args += ["--workers", str(workers)]
    return args


def artifact(out: Path) -> tuple[bytes, bytes]:
    counts = out / "counts"
    return (counts / "counts.jsonl").read_bytes(), (counts / "counts.json").read_bytes()


def refused(capsys: pytest.CaptureFixture[str], args: list[str]) -> dict[str, Any]:
    capsys.readouterr()
    assert operator(args) == 1
    result: dict[str, Any] = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert result["refused"] is True
    return result


@pytest.fixture(scope="module")
def published(
    new: dict[str, Any],  # noqa: F811
    new_fit: dict[str, Any],  # noqa: F811
    tmp_path_factory: pytest.TempPathFactory,
) -> dict[str, Any]:
    """The reference and fast counts (C06-bound, pinned) of the new chain."""
    root = tmp_path_factory.mktemp("published")
    tokenizer = new_fit["fit"] / TOKENIZER_DIR
    bound = ["--c06-fit", str(new_fit["fit"]), *pins(new, new_fit)]
    outputs: dict[str, Path] = {}
    with pytest.MonkeyPatch.context() as monkey:
        from scripts.c05_authored_pilot import KEY

        monkey.setenv(KEY_ENV, KEY)
        outputs["reference"] = root / "reference"
        args = count_cli("count-tokens-reference", new, tokenizer, outputs["reference"], *bound)
        assert operator(args) == 0
        for workers in (1, 4, 8, 16):
            outputs[f"w{workers}"] = root / f"w{workers}"
            args = count_cli(
                "count-tokens", new, tokenizer, outputs[f"w{workers}"], *bound, workers=workers
            )
            assert operator(args) == 0
    return {"outputs": outputs, "tokenizer": tokenizer}


# -- exactness ---------------------------------------------------------------------------------


def test_v3_counts_identical_at_every_worker_count(published: dict[str, Any]) -> None:
    outputs = published["outputs"]
    reference = artifact(outputs["reference"])
    for name in ("w1", "w4", "w8", "w16"):
        assert artifact(outputs[name]) == reference, name
        assert sorted(p.name for p in (outputs[name] / "counts").iterdir()) == [
            "counts.json",
            "counts.jsonl",
        ]
        # No job residue: the scratch holds nothing the job created.
        assert not any((outputs[name] / "scratch").rglob("*"))


def test_unbound_counts_are_identical_and_carry_no_c06_record(
    new: dict[str, Any],  # noqa: F811
    published: dict[str, Any],
    tmp_path: Path,
) -> None:
    tokenizer = published["tokenizer"]
    assert operator(count_cli("count-tokens-reference", new, tokenizer, tmp_path / "r")) == 0
    assert operator(count_cli("count-tokens", new, tokenizer, tmp_path / "f", workers=4)) == 0
    assert artifact(tmp_path / "r") == artifact(tmp_path / "f")
    unbound = load(artifact(tmp_path / "f")[1])["payload"]
    bound = load(artifact(published["outputs"]["w4"])[1])["payload"]
    assert "c06_fit" not in unbound and "c06_fit" in bound
    # Binding adds exactly one payload key; the canonical count schema is unchanged.
    assert {k: v for k, v in bound.items() if k != "c06_fit"} == unbound
    assert artifact(tmp_path / "f")[0] == artifact(published["outputs"]["w4"])[0]


def test_v3_counts_match_independent_oracle(
    new: dict[str, Any],  # noqa: F811
    published: dict[str, Any],
) -> None:
    """Only kept C05-train rows, each with the reference count rule over its source text."""
    plan = load(new["plan"].read_bytes())
    plan = plan.get("payload", plan)
    kept = {r["doc_id"]: r for r in rows_of(new) if r["decision"] == "kept"}
    train = {doc_id for doc_id, r in kept.items() if r["split"] == "train"}
    held_out = {doc_id for doc_id, r in kept.items() if r["split"] != "train"}
    dropped = {r["doc_id"] for r in new["decisions"] if r["decision"] != "kept"}
    assert {r["split"] for r in kept.values()} >= {"train", "diagnostic_val", "audit"}
    assert dropped and held_out
    tokenizer = load_tokenizer(published["tokenizer"])
    expected: dict[str, dict[str, Any]] = {}
    for item in plan["files"]:
        allocation = [item["component"], item["view"], item["upstream_component"]]
        for line in (Path(plan["data_root"]) / item["path"]).read_bytes().splitlines():
            document = CanonicalDocument(**load(line))
            if document.doc_id not in train:
                continue
            expected[document.doc_id] = {
                "allocation": allocation,
                "content": canonical.digest(document.to_dict()),
                "doc_id": document.doc_id,
                "valid_targets": valid_targets(tokenizer, document.text),
            }
    rows = [load(line) for line in artifact(published["outputs"]["w16"])[0].splitlines()]
    assert [r["doc_id"] for r in rows] == sorted(expected, key=lambda d: d.encode("utf-8"))
    assert rows == [expected[r["doc_id"]] for r in rows]
    ids = {r["doc_id"] for r in rows}
    assert not ids & held_out and not ids & dropped
    totals: dict[str, dict[str, int]] = defaultdict(lambda: {"documents": 0, "valid_targets": 0})
    for row in rows:
        total = totals[allocation_key(*row["allocation"])]
        total["documents"] += 1
        total["valid_targets"] += row["valid_targets"]
    payload = load(artifact(published["outputs"]["w16"])[1])["payload"]
    assert payload["allocations"] == dict(sorted(totals.items()))
    assert payload["documents"] == len(train) == train_documents(new)
    assert payload["mode"] == "authored"
    assert payload["completion_digest"] == new["completion_digest"]
    assert payload["plan_digest"] == new["plan_digest"]


def test_group_fields_never_reach_counting_membership(new: dict[str, Any]) -> None:  # noqa: F811
    """Any split_group/exclusion_group values parse to the same counting columns."""
    view = view_of(new)
    tables, _ = count_tables(view)
    assert tables.contract == "c05_membership_v3"
    rewritten = b"".join(
        canonical.canonical_bytes(
            {
                **row,
                "split_group": hashlib.sha256(b"s" + row["doc_id"].encode()).hexdigest(),
                "exclusion_group": "",
            }
        )
        + b"\n"
        for row in rows_of(new)
    )
    fitscan.init_worker(tables)
    try:
        original = fitscan.parse_membership_chunk(new["membership_bytes"])
        changed = fitscan.parse_membership_chunk(rewritten)
        for name in (
            "ids",
            "id_lengths",
            "file",
            "row",
            "nbytes",
            "content",
            "split",
            "allocation",
        ):
            assert np.array_equal(
                np.asarray(getattr(original, name)), np.asarray(getattr(changed, name))
            ), name
        # A v2-shaped row (lineage_group) is refused by the v3 contract.
        legacy = canonical.canonical_bytes(
            {
                **{k: v for k, v in rows_of(new)[0].items() if k not in GROUPS},
                "lineage_group": "x",
            }
        )
        with pytest.raises(C05Error, match="membership record schema"):
            fitscan.parse_membership_chunk(legacy + b"\n")
    finally:
        fitscan._TABLES = None


GROUPS = ("split_group", "exclusion_group")


# -- C06 binding -------------------------------------------------------------------------------


def test_signed_c06_record_and_kept_index_agreement(
    new: dict[str, Any],  # noqa: F811
    new_fit: dict[str, Any],  # noqa: F811
    published: dict[str, Any],
) -> None:
    payload = load(artifact(published["outputs"]["w8"])[1])["payload"]
    expected = bindings(new, new_fit)
    assert payload["c06_fit"] == {
        "fit_digest": expected["fit"],
        "kept_index_digest": expected["index"],
    }
    assert payload["tokenizer"]["fingerprint"] == expected["fingerprint"]
    index = verify_kept_index(view_of(new), new_fit["fit"] / KEPT_INDEX_DIR, None, None, None)  # type: ignore[arg-type]
    assert index["index_digest"] == expected["index"]
    assert payload["documents"] == index["assigned_splits"]["train"]
    assert sum(index["assigned_splits"].values()) == index["rows"]


@pytest.mark.parametrize(
    ("override", "reason"),
    [
        ({"plan": "0" * 64}, "C05 proof is not the pinned chain: plan_digest"),
        ({"completion": "0" * 64}, "C05 proof is not the pinned chain: completion_digest"),
        ({"fingerprint": "0" * 64}, "tokenizer is not the pinned fingerprint"),
        ({"fit": "0" * 64}, "C06 fit is not the pinned fit"),
        ({"index": "0" * 64}, "C06 kept index is not the pinned kept index"),
        ({"documents": "1"}, "kept train document count differs from the pinned count"),
    ],
)
@pytest.mark.parametrize("command", ["count-tokens", "count-tokens-reference"])
def test_wrong_pins_refuse_and_publish_nothing(
    new: dict[str, Any],  # noqa: F811
    new_fit: dict[str, Any],  # noqa: F811
    override: dict[str, str],
    reason: str,
    command: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    extra = ["--c06-fit", str(new_fit["fit"]), *pins(new, new_fit, **override)]
    workers = 1 if command == "count-tokens" else None
    args = count_cli(
        command, new, new_fit["fit"] / TOKENIZER_DIR, tmp_path, *extra, workers=workers
    )
    result = refused(capsys, args)
    assert result["error_type"] == "C05Error"
    if command == "count-tokens":  # The reference CLI refusal names only the error type.
        assert result["reason"] == reason
    assert not (tmp_path / "counts").exists()
    assert not list(tmp_path.glob("counts.partial-*"))


def test_fit_pins_require_the_fit_directory(
    new: dict[str, Any],  # noqa: F811
    new_fit: dict[str, Any],  # noqa: F811
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    args = count_cli(
        "count-tokens",
        new,
        new_fit["fit"] / TOKENIZER_DIR,
        tmp_path,
        "--expect-kept-index-digest",
        bindings(new, new_fit)["index"],
        workers=1,
    )
    assert refused(capsys, args)["reason"] == (
        "C06 fit or kept-index pins require the C06 fit directory"
    )
    assert not (tmp_path / "counts").exists()


def test_stale_combinations_refuse(
    old: dict[str, Any],  # noqa: F811
    new: dict[str, Any],  # noqa: F811
    old_fit: dict[str, Any],  # noqa: F811
    new_fit: dict[str, Any],  # noqa: F811
    published: dict[str, Any],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    new_tok, old_tok = new_fit["fit"] / TOKENIZER_DIR, old_fit["fit"] / TOKENIZER_DIR
    cases = {
        # The old chain's fit (and its kept index) with the new proof and tokenizer.
        "old-fit": (new, new_tok, old_fit["fit"]),
        # The new fit with the old chain's tokenizer.
        "old-tokenizer": (new, old_tok, new_fit["fit"]),
        # The old proof with the new fit and tokenizer.
        "old-proof": (old, new_tok, new_fit["fit"]),
    }
    # The new fit with the old chain's kept index swapped in.
    swapped = tmp_path / "swapped-fit"
    shutil.copytree(new_fit["fit"], swapped)
    shutil.rmtree(swapped / KEPT_INDEX_DIR)
    shutil.copytree(old_fit["fit"] / KEPT_INDEX_DIR, swapped / KEPT_INDEX_DIR)
    cases["old-kept-index"] = (new, new_tok, swapped)
    for name, (run, tokenizer, fit) in cases.items():
        out = tmp_path / name
        args = count_cli("count-tokens", run, tokenizer, out, "--c06-fit", str(fit), workers=1)
        refused(capsys, args)
        assert not (out / "counts").exists(), name
    # A published output is write-once.
    existing = published["outputs"]["w1"]
    before = artifact(existing)
    args = count_cli(
        "count-tokens", new, new_tok, existing, "--c06-fit", str(new_fit["fit"]), workers=1
    )
    assert refused(capsys, args)["reason"] == "selection artifacts are write-once"
    assert artifact(existing) == before


# -- verify-counts -----------------------------------------------------------------------------


def verify_cli(run: dict[str, Any], tokenizer: Path, counts: Path, *extra: str) -> list[str]:
    return [
        "verify-counts",
        "--c05-proof",
        str(run["proof"]),
        "--tokenizer",
        str(tokenizer),
        "--counts",
        str(counts),
        "--no-progress",
        *extra,
    ]


def test_verify_counts_accepts_and_reports(
    new: dict[str, Any],  # noqa: F811
    new_fit: dict[str, Any],  # noqa: F811
    published: dict[str, Any],
    capsys: pytest.CaptureFixture[str],
) -> None:
    counts = published["outputs"]["w16"] / "counts"
    capsys.readouterr()
    args = verify_cli(
        new, published["tokenizer"], counts, "--c06-fit", str(new_fit["fit"]), *pins(new, new_fit)
    )
    assert operator(args) == 0
    result = json.loads(capsys.readouterr().out)
    envelope = load((counts / "counts.json").read_bytes())
    assert result["verified"] is True and result["kept_index_rows_compared"] is True
    assert result["counts_digest"] == envelope["digest"]
    assert result["documents"] == envelope["payload"]["documents"] == train_documents(new)
    assert result["allocations"] == envelope["payload"]["allocations"]
    assert result["c06_fit"] == envelope["payload"]["c06_fit"]
    assert result["tokenizer_fingerprint"] == bindings(new, new_fit)["fingerprint"]
    # Without the fit: the same artifact verifies, rows are not compared to the index.
    assert operator(verify_cli(new, published["tokenizer"], counts)) == 0
    assert json.loads(capsys.readouterr().out)["kept_index_rows_compared"] is False


def test_verify_counts_refuses_tampering_and_wrong_pins(
    new: dict[str, Any],  # noqa: F811
    new_fit: dict[str, Any],  # noqa: F811
    published: dict[str, Any],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    tokenizer, fit = published["tokenizer"], ["--c06-fit", str(new_fit["fit"])]
    source = published["outputs"]["w4"] / "counts"
    wrong = verify_cli(new, tokenizer, source, *fit, *pins(new, new_fit, documents="7"))
    assert refused(capsys, wrong)["reason"] == (
        "kept train document count differs from the pinned count"
    )

    def tampered(name: str, change: Any) -> Path:
        directory = tmp_path / name
        shutil.copytree(source, directory)
        change(directory)
        return directory

    def bump_count(directory: Path) -> None:
        path = directory / "counts.jsonl"
        data = path.read_bytes()
        at = data.index(b'"valid_targets":') + len(b'"valid_targets":')
        digit = data[at : at + 1]
        path.write_bytes(data[:at] + (b"1" if digit != b"1" else b"2") + data[at + 1 :])

    cases = {
        "count": (tampered("count", bump_count), "exact count artifact changed"),
        "extra": (
            tampered("extra", lambda d: (d / "notes.txt").write_bytes(b"x")),
            "count artifact directory holds unaccounted files",
        ),
    }
    for name, (directory, reason) in cases.items():
        assert refused(capsys, verify_cli(new, tokenizer, directory, *fit))["reason"] == reason, (
            name
        )
    # An unbound artifact does not verify as bound to a fit.
    unbound = tmp_path / "unbound"
    assert operator(count_cli("count-tokens", new, tokenizer, unbound, workers=1)) == 0
    result = refused(capsys, verify_cli(new, tokenizer, unbound / "counts", *fit))
    assert result["reason"] == "exact counts are not bound to this C06 fit"


# -- BPE word cache ----------------------------------------------------------------------------


def test_larger_bpe_cache_changes_nothing_but_speed(
    new: dict[str, Any],  # noqa: F811
    published: dict[str, Any],
) -> None:
    stock = load_tokenizer(published["tokenizer"])
    cached = load_tokenizer(published["tokenizer"])
    serialized, fingerprint = stock._tok.to_str(), stock.fingerprint
    with_bpe_cache(cached, 100_000)
    assert cached._tok.to_str() == serialized and cached.fingerprint == fingerprint
    plan = load(new["plan"].read_bytes())
    plan = plan.get("payload", plan)
    texts = [
        CanonicalDocument(**load(line)).text
        for item in plan["files"]
        for line in (Path(plan["data_root"]) / item["path"]).read_bytes().splitlines()
    ]
    texts += ["", " ", "a" * 5000, "naïve café 東京 \n\t x"]
    for _ in range(2):  # the second pass is served from the cache
        assert cached.count_valid_targets(texts) == stock.count_valid_targets(texts)
    assert stock.count_valid_targets(texts) == [valid_targets(stock, t) for t in texts]
    with pytest.raises(ValueError, match="positive integer"):
        with_bpe_cache(cached, 0)
    dropout = load_tokenizer(published["tokenizer"])
    model = json.loads(serialized)
    model["model"]["dropout"] = 0.1
    from tokenizers import Tokenizer

    dropout._tok = Tokenizer.from_str(json.dumps(model))
    with pytest.raises(ValueError, match="deterministic BPE"):
        with_bpe_cache(dropout, 100_000)


def test_count_tables_follow_the_plan_contract(new: dict[str, Any]) -> None:  # noqa: F811
    tables, keys = count_tables(view_of(new))
    assert tables.contract == "c05_membership_v3"
    assert keys == sorted(keys) and len(set(keys)) == len(keys)
    with pytest.raises(C05Error, match="unknown C05 membership contract"):
        fitscan.init_worker(replace(tables, contract="c05_membership_v4"))
        try:
            fitscan.parse_membership_chunk(new["membership_bytes"])
        finally:
            fitscan._TABLES = None


def test_kept_index_comparison_detects_any_column_difference(
    new: dict[str, Any],  # noqa: F811
    new_fit: dict[str, Any],  # noqa: F811
) -> None:
    """compare_membership refuses unless every kept-index column equals membership."""
    from xlm.data.exclusion.countbind import CountPins, compare_membership, open_c06
    from xlm.data.exclusion.fitfast import Membership
    from xlm.data.exclusion.selection import tokenizer_identity

    view = view_of(new)
    _, identity = tokenizer_identity(new_fit["fit"] / TOKENIZER_DIR, view)
    binding = open_c06(new_fit["fit"], view, identity, CountPins())
    index = binding.index
    assert index is not None
    _, keys = count_tables(view)
    position = {k: n for n, k in enumerate(keys)}
    mapped = np.asarray([position[allocation_key(*a)] for a in index.allocations])
    rows = index.rows

    def membership(**changes: Any) -> Membership:
        columns: dict[str, Any] = {
            "rows": len(rows),
            "ids": bytes(index.ids),
            "id_offsets": index.id_offsets.copy(),
            "file": rows["file"].copy(),
            "row": rows["row"].copy(),
            "nbytes": rows["bytes"].copy(),
            "content": rows["content"].copy(),
            "split": rows["assigned_split"].copy(),
            "allocation": mapped[rows["allocation"]].astype(np.uint16),
            "rank": np.zeros((len(rows), 32), dtype=np.uint8),
        }
        columns.update(changes)
        return Membership(**columns)

    compare_membership(binding, membership(), keys)
    flipped = rows["assigned_split"].copy()
    flipped[0] = 1 - min(flipped[0], 1)
    content = rows["content"].copy()
    content[-1, 0] ^= 1
    ids = bytearray(index.ids)
    ids[0] ^= 1
    for name, changed in {
        "assigned_split": {"split": flipped},
        "content": {"content": content},
        "ids": {"ids": bytes(ids)},
        "row": {"row": rows["row"] + 1},
    }.items():
        with pytest.raises(C05Error, match="kept index differs from authenticated membership"):
            compare_membership(binding, membership(**changed), keys)
        assert name
    binding.release()
    with pytest.raises(C05Error, match="released"):
        compare_membership(binding, membership(), keys)
    binding.reverify()  # The fit on disk is unchanged.
