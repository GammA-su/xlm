"""Phase-C production cleaning on authored fixtures only (no real corpus, no G:/X:).

Proves the DROP-only semantics (KEEP rows byte-identical and in order, DROP rows absent,
nothing transformed), the binding to the approved dry run and its refusals, the path
mapping and output-tree safety, per-file atomic publication with interruption at every
publication point and resume, the fatal dry-run accounting comparison, worker and
resume determinism of the corpus, membership and inventory, the independent verifier
(it catches corruption and never writes the corpus), the cleaned-manifest builder (only
after verification, new digest), no source mutation and no document text in any
receipt or evidence file.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import shutil
import zlib
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from production_fixtures import (
    ALL_DROP,
    ALL_KEEP,
    EMPTY,
    NO_NEWLINE,
    TEXT_CANARIES,
    limits,
    make_flow,
    oracle,
)
from quality_fixtures import CANARIES
from xlm.data.evidence_v2 import canonical
from xlm.data.quality import cleaning, production, production_verify
from xlm.data.quality.cleaning_policy import DROP, KEEP, PolicyError
from xlm.data.quality.cli import main
from xlm.data.quality.outputs import OutputError, OutputTree
from xlm.data.quality.production import (
    BINDING_FILE,
    CLEANED_MANIFEST_FILE,
    RECEIPT_FILE,
    VERIFICATION_FILE,
    run_production,
    unit_path,
)
from xlm.data.quality.production_approval import ApprovalError, verify_approved_dry_run
from xlm.data.quality.production_paths import (
    TEMP_SUFFIX,
    CorpusTree,
    PathMappingError,
    build_mapping,
    output_relpath,
)
from xlm.data.quality.production_report import (
    ARTIFACTS,
    CLEANED_INVENTORY,
    DROPPED_MEMBERSHIP,
    membership_rows,
)
from xlm.data.quality.production_verify import (
    OutputCheckTask,
    build_cleaned_manifest,
    check_output_file,
    verify_production,
)
from xlm.data.quality.scan import AuditFile, QualityError, load_manifest


@pytest.fixture(scope="module")
def flow(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    return make_flow(tmp_path_factory.mktemp("prod"))


@pytest.fixture(scope="module")
def expected(flow: dict[str, Any]) -> dict[str, Any]:
    return oracle(flow)


def clean(flow: dict[str, Any], out: Path, *, workers: int = 1, **kwargs: Any) -> dict[str, Any]:
    out.mkdir(parents=True, exist_ok=True)
    return run_production(
        flow["manifest"],
        kwargs.pop("policy", flow["policy"]),
        kwargs.pop("dry", flow["dry"]),
        out / "clean",
        out / "state",
        approved_result_digest=kwargs.pop("digest", flow["digest"]),
        limits=limits(workers),
        progress_interval=kwargs.pop("progress_interval", None),
        **kwargs,
    )


def verify(flow: dict[str, Any], out: Path, **kwargs: Any) -> dict[str, Any]:
    return verify_production(
        flow["manifest"],
        flow["policy"],
        flow["dry"],
        out / "clean",
        out / "state",
        approved_result_digest=flow["digest"],
        workers=kwargs.pop("workers", 1),
        progress_interval=None,
        **kwargs,
    )


def corpus_bytes(out: Path) -> dict[str, bytes]:
    root = out / "clean"
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def source_hashes(flow: dict[str, Any]) -> dict[str, str]:
    return {
        p.relative_to(flow["data"]).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(flow["data"].rglob("*"))
        if p.is_file()
    }


@pytest.fixture(scope="module")
def reference(flow: dict[str, Any], tmp_path_factory: pytest.TempPathFactory) -> Path:
    """One complete production run (workers=1), verified, with its cleaned manifest."""
    out = tmp_path_factory.mktemp("reference")
    before = source_hashes(flow)
    result = clean(flow, out)
    assert result["complete"] is True and result["dry_run_accounting_match"] == "IDENTICAL"
    assert source_hashes(flow) == before  # no source mutation
    return out


# -- fixture premises -------------------------------------------------------------------------


def test_fixture_premises(flow: dict[str, Any], expected: dict[str, Any]) -> None:
    assert flow["policy_status"] == "POLICY_WITHIN_GUARDRAILS"
    assert all(o == DROP for o in expected[ALL_DROP]["outcomes"]) and expected[ALL_DROP]["drops"]
    assert all(o == KEEP for o in expected[ALL_KEEP]["outcomes"])
    assert expected[EMPTY]["outcomes"] == []
    mixed = expected[NO_NEWLINE]["outcomes"]
    assert DROP in mixed and mixed[-1] == KEEP and mixed[0] == KEEP
    assert not (flow["data"] / NO_NEWLINE).read_bytes().endswith(b"\n")


# -- DROP-only semantics -------------------------------------------------------------------------


def test_keep_rows_are_original_bytes_in_order_and_drops_absent(
    flow: dict[str, Any], expected: dict[str, Any], reference: Path
) -> None:
    corpus = corpus_bytes(reference)
    assert set(corpus) == set(expected)  # one output per input, same relative path
    for path, entry in expected.items():
        assert corpus[path] == entry["output"], path  # raw-line byte identity and order
    # Mixed file: the KEEP lines are exactly the source lines minus the DROP lines.
    source = (flow["data"] / NO_NEWLINE).read_bytes().splitlines(keepends=True)
    kept = corpus[NO_NEWLINE].splitlines(keepends=True)
    assert len(kept) == len(source) - expected[NO_NEWLINE]["drops"]
    assert kept[-1] == source[-1] and not kept[-1].endswith(b"\n")  # no newline added
    # No transformation: every kept line equals some source line, and parses unchanged.
    assert set(kept) <= set(source)
    # All-KEEP file: byte-identical copy; all-DROP and empty inputs: zero-byte outputs.
    assert corpus[ALL_KEEP] == (flow["data"] / ALL_KEEP).read_bytes()
    assert corpus[ALL_DROP] == b"" and corpus[EMPTY] == b""


def test_no_transformations_of_unusual_text(flow: dict[str, Any], reference: Path) -> None:
    """Unicode, combining marks, emoji and escaped controls survive byte for byte."""
    path = "canonical/common_pile_prose/default/a/documents.jsonl"
    source = (flow["data"] / path).read_bytes()
    assert (reference / "clean" / path).read_bytes() == source  # all KEEP in this file
    assert "日本語".encode() in source


def test_receipt_and_artifacts(
    flow: dict[str, Any], expected: dict[str, Any], reference: Path
) -> None:
    state = reference / "state"
    receipt = production_verify.load_production_receipt(state)
    dry = json.loads((flow["dry"] / "cleaning-dry-run.json").read_bytes())
    totals = receipt["accounting"]["global"]
    drop = dry["global"]["outcomes"]["DROP"]
    keep = dry["global"]["outcomes"]["KEEP"]
    assert (totals["drop"]["documents"], totals["drop"]["canonical_bytes"]) == (
        drop["docs"],
        drop["bytes"],
    )
    assert (totals["keep"]["documents"], totals["keep"]["canonical_bytes"]) == (
        keep["docs"],
        keep["bytes"],
    )
    for name, summary in dry["components"].items():
        mine = receipt["accounting"]["components"][name]
        assert mine["drop"]["documents"] == summary["outcomes"]["DROP"]["docs"]
        assert mine["drop"]["canonical_bytes"] == summary["outcomes"]["DROP"]["bytes"]
    assert receipt["dry_run_accounting_match"]["compared"] == [
        "global",
        "components",
        "by_component",
        "rules",
        "guardrails",
    ]
    assert receipt["binding"]["cleaning_policy"]["version"] == "cleaning_policy_v2"
    assert receipt["binding"]["approved_dry_run"]["result_digest"] == flow["digest"]
    for name in (*ARTIFACTS, BINDING_FILE, RECEIPT_FILE):
        assert (state / name).is_file()
    rows = membership_rows((state / DROPPED_MEMBERSHIP).read_bytes())
    assert len(rows) == sum(e["drops"] for e in expected.values())
    assert {r["decision"] for r in rows} == {"DROP"}
    inventory = json.loads((state / CLEANED_INVENTORY).read_bytes())
    by_path = {f["output"]["path"]: f for f in inventory["files"]}
    for path, entry in expected.items():
        out = by_path[path]["output"]
        assert out["sha256"] == hashlib.sha256(entry["output"]).hexdigest()
        assert out["file_bytes"] == len(entry["output"])
        assert by_path[path]["input"]["path"] == path
    # No operational record in the corpus tree.
    assert not any(
        p.name.endswith((".json", ".zz", TEMP_SUFFIX)) for p in (reference / "clean").rglob("*")
    )


def test_no_document_text_in_receipts_or_evidence(flow: dict[str, Any], reference: Path) -> None:
    body = json.loads(flow["manifest"].read_bytes())
    doc_ids = [
        json.loads(line)["doc_id"].encode()
        for f in body["files"]
        for line in (flow["data"] / f["path"]).read_bytes().splitlines()
    ]
    payloads = []
    for path in (reference / "state").rglob("*"):
        if path.is_file():
            raw = path.read_bytes()
            payloads.append(zlib.decompress(raw) if path.suffix == ".zz" else raw)
    for payload in payloads:
        for canary in (*CANARIES, *TEXT_CANARIES):
            assert canary.encode("utf-8") not in payload, canary
        for doc_id in doc_ids:
            assert b'"' + doc_id + b'"' not in payload


# -- determinism ---------------------------------------------------------------------------------


def test_worker_and_rerun_determinism(
    flow: dict[str, Any], reference: Path, tmp_path: Path
) -> None:
    """Same paths, workers 1/2/4: corpus, membership, inventory and every artifact identical."""
    out = tmp_path / "w"
    seen: list[dict[str, bytes]] = []
    for workers in (1, 2, 4):
        if out.exists():
            shutil.rmtree(out)
        result = clean(flow, out, workers=workers)
        assert result["clean"]["workers"] == workers
        state = {name: (out / "state" / name).read_bytes() for name in ARTIFACTS}
        seen.append({**corpus_bytes(out), **{f"state/{k}": v for k, v in state.items()}})
    assert seen[0] == seen[1] == seen[2]
    # Across output roots only the binding-bound headers differ; corpus and rows do not.
    assert {k: v for k, v in seen[0].items() if not k.startswith("state/")} == corpus_bytes(
        reference
    )
    assert (
        seen[0][f"state/{DROPPED_MEMBERSHIP}"]
        == (reference / "state" / DROPPED_MEMBERSHIP).read_bytes()
    )


# -- path mapping -------------------------------------------------------------------------------


def _file(ordinal: int, path: str) -> AuditFile:
    return AuditFile(ordinal, path, "s", "c", "v", None, "0" * 64, 0, 0, 0)


@pytest.mark.parametrize(
    "path",
    [
        "../x/documents.jsonl",
        "a/../b.jsonl",
        "/abs/x.jsonl",
        "a//b.jsonl",
        "a/./b.jsonl",
        "a\\b.jsonl",
        "C:/x.jsonl",
        "a/CON/b.jsonl",
        "a/nul.txt",
        "a/b. ",
        "a/b\x01.jsonl",
        f"a/b.jsonl{TEMP_SUFFIX}",
        "",
    ],
)
def test_traversal_and_nonportable_paths_refuse(path: str) -> None:
    with pytest.raises(PathMappingError):
        output_relpath(path)


def test_mapping_is_one_to_one_and_stable() -> None:
    files = [_file(0, "canonical/a/documents.jsonl"), _file(1, "canonical/b/documents.jsonl")]
    mapping = build_mapping(files)
    assert [m.output_path for m in mapping] == [f.path for f in files]
    assert build_mapping(files) == mapping  # deterministic
    with pytest.raises(PathMappingError, match="two inputs map to one output"):
        build_mapping([_file(0, "canonical/A/x.jsonl"), _file(1, "canonical/a/x.jsonl")])
    with pytest.raises(PathMappingError, match="two inputs map to one output"):
        build_mapping([_file(0, "c/\u00e9.jsonl"), _file(1, "c/e\u0301.jsonl")])  # NFC
    with pytest.raises(PathMappingError, match="also a directory"):
        build_mapping([_file(0, "c/a"), _file(1, "c/a/x.jsonl")])


# -- root safety -------------------------------------------------------------------------------


def test_overlap_and_destination_refusals(flow: dict[str, Any], tmp_path: Path) -> None:
    data = flow["data"]
    cases: list[tuple[Path, Path, str]] = [
        (data, tmp_path / "s1", "overlaps the input data root"),
        (data / "canonical" / "new", tmp_path / "s2", "overlaps the input data root"),
        (data.parent, tmp_path / "s3", "overlaps the input data root"),  # input inside output
        (tmp_path / "o4", tmp_path / "o4" / "state", "state output overlaps the output root"),
        (tmp_path / "o5", data.parent, "state output contains the input data root"),
        (flow["dry"] / "clean", tmp_path / "s6", "overlaps an input"),
        (tmp_path / "o7", flow["dry"], "overlaps an input"),
    ]
    for out, state, message in cases:
        with pytest.raises(QualityError, match=message):
            run_production(
                flow["manifest"],
                flow["policy"],
                flow["dry"],
                out,
                state,
                approved_result_digest=flow["digest"],
                limits=limits(),
                progress_interval=None,
            )
    # Pre-existing unrelated data (even under an expected output name) refuses.
    for content in ("unrelated.txt", NO_NEWLINE):
        out = tmp_path / f"busy-{len(content)}"
        target = out / "clean" / content
        target.parent.mkdir(parents=True)
        target.write_bytes(b"not ours")
        with pytest.raises(OutputError, match="not empty"):
            clean(flow, out)
        assert target.read_bytes() == b"not ours"  # never deleted or overwritten


def _junction(link: Path, target: Path) -> None:
    try:
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
    except (ImportError, AttributeError):
        try:
            os.symlink(target, link, target_is_directory=True)
        except OSError:
            pytest.skip("no junction or symlink support")


def test_junction_and_reparse_protection(flow: dict[str, Any], tmp_path: Path) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    out = tmp_path / "j1"
    out.mkdir()
    _junction(out / "clean", elsewhere)
    with pytest.raises(OutputError, match="link/junction"):
        clean(flow, out)
    # A link inside the tree of an interrupted run refuses the resume.
    out2 = tmp_path / "j2"
    interrupt_after_units(flow, out2)
    _junction(out2 / "clean" / "canonical" / "evil", elsewhere)
    with pytest.raises(OutputError, match="link/junction"):
        clean(flow, out2)
    assert not any(elsewhere.iterdir())


# -- approval binding refusals -------------------------------------------------------------------


def test_stale_or_unapproved_dry_run_refuses(flow: dict[str, Any], tmp_path: Path) -> None:
    with pytest.raises(ApprovalError, match="differs from the approved digest"):
        clean(flow, tmp_path / "a", digest="0" * 64)
    with pytest.raises(ApprovalError, match="not a SHA-256"):
        clean(flow, tmp_path / "b", digest="ca1135")
    # A tampered artifact or unit in a copy of the dry run refuses.
    for victim in ("cleaning-dry-run.json", "units/f00001.unit.zz"):
        copy = tmp_path / f"dry-{victim.replace('/', '-')}"
        shutil.copytree(flow["dry"], copy)
        raw = bytearray((copy / victim).read_bytes())
        raw[len(raw) // 2] ^= 1
        (copy / victim).write_bytes(bytes(raw))
        with pytest.raises(QualityError):
            clean(flow, tmp_path / f"o-{copy.name}", dry=copy)
        assert not (tmp_path / f"o-{copy.name}" / "clean").exists()
    # An incomplete dry run (no receipt) refuses.
    incomplete = tmp_path / "dry-incomplete"
    shutil.copytree(flow["dry"], incomplete)
    (incomplete / "cleaning-dry-run-receipt.json").unlink()
    with pytest.raises(QualityError, match="incomplete"):
        clean(flow, tmp_path / "c", dry=incomplete)


def test_stale_policy_refuses(flow: dict[str, Any], tmp_path: Path) -> None:
    with pytest.raises(PolicyError, match="requires cleaning_policy_v2"):
        clean(flow, tmp_path / "v1", policy=flow["cuts_v1"])
    # The genuine frozen v2 (Phase-A cuts) is not the policy the dry run used.
    with pytest.raises(ApprovalError, match="different cleaning policy"):
        clean(flow, tmp_path / "v2", policy=flow["frozen_v2"])
    edited = tmp_path / "edited.yaml"
    edited.write_bytes(flow["policy"].read_bytes().replace(b"cut: 64", b"cut: 65", 1))
    with pytest.raises(PolicyError, match="self-digest"):
        clean(flow, tmp_path / "e", policy=edited)


def test_dry_run_of_another_manifest_refuses(tmp_path: Path, flow: dict[str, Any]) -> None:
    other = make_flow(tmp_path / "other")
    manifest = load_manifest(flow["manifest"])
    from xlm.data.quality.cleaning_policy import load_frozen

    with pytest.raises(ApprovalError):
        verify_approved_dry_run(
            other["dry"], manifest, load_frozen(flow["policy"]), other["digest"]
        )


def test_wrong_dry_run_accounting_is_fatal(
    flow: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A decision differing from the approved dry run refuses before publication."""
    original = cleaning.evaluate
    flipped = {"done": False}

    def evaluate(*args: Any, **kwargs: Any) -> Any:
        decision = original(*args, **kwargs)
        if decision.outcome == DROP and not flipped["done"]:
            flipped["done"] = True
            return dataclasses.replace(decision, outcome=KEEP, rules=0)
        return decision

    monkeypatch.setattr(cleaning, "evaluate", evaluate)
    out = tmp_path / "flip"
    with pytest.raises(QualityError, match="differ from the approved dry run"):
        clean(flow, out)
    assert not (out / "state" / RECEIPT_FILE).exists()
    finals = [p for p in (out / "clean").rglob("*") if p.is_file()]
    assert all(not p.name.endswith(TEMP_SUFFIX) for p in finals)  # temporaries withdrawn


def test_global_accounting_mismatch_is_fatal(
    flow: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = verify_approved_dry_run

    def tampered(*args: Any, **kwargs: Any) -> Any:
        approved = real(*args, **kwargs)
        accounting = json.loads(json.dumps(approved.accounting))
        accounting["global"]["outcomes"]["DROP"]["docs"] += 1
        return type(approved)(
            approved.path, approved.receipt, approved.binding, approved.expected, accounting
        )

    monkeypatch.setattr(production, "verify_approved_dry_run", tampered)
    out = tmp_path / "global"
    with pytest.raises(QualityError, match=r"differ from the approved dry run \(global\)"):
        clean(flow, out)
    assert not (out / "state" / RECEIPT_FILE).exists()
    assert not (out / "state" / "receipt-staging" / RECEIPT_FILE).exists()


# -- atomic publication, interruption and resume -------------------------------------------------


class Interrupt(Exception):
    pass


def _fail_once(
    monkeypatch: pytest.MonkeyPatch, owner: Any, name: str, when: Callable[..., bool]
) -> None:
    real = getattr(owner, name)
    state = {"fired": False}

    def wrapper(*args: Any, **kwargs: Any) -> Any:
        if not state["fired"] and when(*args, **kwargs):
            state["fired"] = True
            raise Interrupt
        return real(*args, **kwargs)

    monkeypatch.setattr(owner, name, wrapper)


def interrupt_after_units(flow: dict[str, Any], out: Path) -> None:
    """All units committed, then an interruption before the artifacts/receipt."""
    with pytest.MonkeyPatch.context() as patch:
        _fail_once(patch, production, "build_production_artifacts", lambda *a, **k: True)
        with pytest.raises(Interrupt):
            clean(flow, out)
    assert not (out / "state" / RECEIPT_FILE).exists()


@pytest.fixture
def resumed_equals(reference: Path) -> Iterator[Callable[[Path], None]]:
    def check(out: Path) -> None:
        assert corpus_bytes(out) == corpus_bytes(reference)
        assert (out / "state" / DROPPED_MEMBERSHIP).read_bytes() == (
            reference / "state" / DROPPED_MEMBERSHIP
        ).read_bytes()
        mine = json.loads((out / "state" / CLEANED_INVENTORY).read_bytes())
        theirs = json.loads((reference / "state" / CLEANED_INVENTORY).read_bytes())
        for section in ("files", "components", "totals", "mapping"):
            assert mine[section] == theirs[section], section

    yield check


def test_interrupt_before_publication_then_resume(
    flow: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    resumed_equals: Callable[[Path], None],
) -> None:
    out = tmp_path / "pre"
    calls = {"n": 0}

    def second(*_: Any) -> bool:
        calls["n"] += 1
        return calls["n"] == 2

    with monkeypatch.context() as patch:
        _fail_once(patch, CorpusTree, "publish", second)
        with pytest.raises(Interrupt):
            clean(flow, out)
    assert unit_path(out / "state", 0).exists() and not unit_path(out / "state", 1).exists()
    assert not list((out / "clean").rglob(f"*{TEMP_SUFFIX}"))  # uncommitted temps withdrawn
    # A crash can still leave temporaries: plant garbage ones; they never count.
    for path in (NO_NEWLINE, ALL_KEEP):
        temp = out / "clean" / (path + TEMP_SUFFIX)
        temp.parent.mkdir(parents=True, exist_ok=True)
        temp.write_bytes(b"partial garbage")
    result = clean(flow, out)
    assert result["files_resumed"] == 1 and result["files_cleaned"] == len(flow_files(flow)) - 1
    resumed_equals(out)
    assert verify(flow, out)["verified"] is True


def flow_files(flow: dict[str, Any]) -> list[Any]:
    return list(json.loads(flow["manifest"].read_bytes())["files"])


def test_interrupt_after_rename_before_unit_then_adopt(
    flow: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    resumed_equals: Callable[[Path], None],
) -> None:
    out = tmp_path / "orphan"
    with monkeypatch.context() as patch:
        _fail_once(
            patch,
            OutputTree,
            "write",
            lambda _self, relative, _payload: relative == "units/f00001.unit.zz",
        )
        with pytest.raises(Interrupt):
            clean(flow, out)
    orphan = flow_files(flow)[1]["path"]
    assert (out / "clean" / orphan).is_file() and not unit_path(out / "state", 1).exists()
    result = clean(flow, out)
    assert result["files_adopted"] == 1 and result["files_resumed"] == 1
    resumed_equals(out)


def test_orphan_with_different_bytes_refuses(
    flow: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = tmp_path / "orphan-bad"
    with monkeypatch.context() as patch:
        _fail_once(
            patch,
            OutputTree,
            "write",
            lambda _self, relative, _payload: relative == "units/f00001.unit.zz",
        )
        with pytest.raises(Interrupt):
            clean(flow, out)
    orphan = out / "clean" / flow_files(flow)[1]["path"]
    orphan.write_bytes(orphan.read_bytes() + b"tampered\n")
    before = orphan.read_bytes()
    with pytest.raises(QualityError, match="never overwritten"):
        clean(flow, out)
    assert orphan.read_bytes() == before


def test_interrupt_after_units_and_before_receipt_publication(
    flow: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    resumed_equals: Callable[[Path], None],
) -> None:
    out = tmp_path / "units"
    interrupt_after_units(flow, out)
    result = clean(flow, out)
    assert result["files_resumed"] == len(flow_files(flow)) and result["files_cleaned"] == 0
    resumed_equals(out)
    # Interruption between receipt staging and publication: nothing published.
    out2 = tmp_path / "staged"
    with monkeypatch.context() as patch:
        _fail_once(patch, production, "_publish_receipt", lambda *a, **k: True)
        with pytest.raises(Interrupt):
            clean(flow, out2)
    assert not (out2 / "state" / RECEIPT_FILE).exists()
    assert not (out2 / "state" / "receipt-staging" / RECEIPT_FILE).exists()
    assert clean(flow, out2)["complete"] is True
    resumed_equals(out2)
    with pytest.raises(QualityError, match="already complete"):
        clean(flow, out2)


def test_resume_refuses_changed_or_missing_output(flow: dict[str, Any], tmp_path: Path) -> None:
    out = tmp_path / "changed"
    interrupt_after_units(flow, out)
    target = out / "clean" / ALL_KEEP
    target.write_bytes(target.read_bytes().replace(b"fox", b"cat", 1))
    with pytest.raises(QualityError, match="changed output"):
        clean(flow, out)
    target.unlink()
    with pytest.raises(QualityError, match="missing"):
        clean(flow, out)


def test_resume_refuses_a_different_binding(flow: dict[str, Any], tmp_path: Path) -> None:
    out = tmp_path / "rebind"
    interrupt_after_units(flow, out)
    binding = json.loads((out / "state" / BINDING_FILE).read_bytes())
    binding["implementation"]["code_identity"] = "0" * 64  # as if the code had changed
    binding["digest"] = canonical.self_digest(binding)
    canonical.write_canonical_json(out / "state" / BINDING_FILE, binding)
    with pytest.raises(QualityError, match="different production binding"):
        clean(flow, out)


def test_stale_source_refuses(tmp_path: Path) -> None:
    private = make_flow(tmp_path / "private")
    out = tmp_path / "out"
    interrupt_after_units(private, out)
    source = private["data"] / ALL_KEEP
    raw = bytearray(source.read_bytes())
    raw[raw.index(b"fox")] = ord("b")  # same size, different bytes
    source.write_bytes(bytes(raw))
    with pytest.raises(QualityError, match="source file differs"):
        clean(private, out)
    with pytest.raises(QualityError, match="differ from the manifest"):
        clean(private, tmp_path / "fresh")  # a fresh run hashes while it reads


def test_resource_limits_refuse(flow: dict[str, Any], tmp_path: Path) -> None:
    from dataclasses import replace

    tight = replace(limits(), max_output_bytes=1024)
    with pytest.raises(QualityError, match="max-output-gib"):
        run_production(
            flow["manifest"],
            flow["policy"],
            flow["dry"],
            tmp_path / "o-clean",
            tmp_path / "o-state",
            approved_result_digest=flow["digest"],
            limits=tight,
            progress_interval=None,
        )
    huge = replace(limits(), free_reserve_bytes=2**60)
    with pytest.raises(QualityError, match="free"):
        run_production(
            flow["manifest"],
            flow["policy"],
            flow["dry"],
            tmp_path / "r-clean",
            tmp_path / "r-state",
            approved_result_digest=flow["digest"],
            limits=huge,
            progress_interval=None,
        )


# -- verification ---------------------------------------------------------------------------------


def test_verify_full_and_manifest_builder(flow: dict[str, Any], tmp_path: Path) -> None:
    out = tmp_path / "v"
    clean(flow, out)
    with pytest.raises(QualityError, match="no verification record"):
        build_cleaned_manifest(out / "state", out / "clean")
    before = corpus_bytes(out)
    result = verify(flow, out, compare_sources=True, reevaluate=True, workers=2)
    assert result["verified"] is True and result["corpus_modified"] is False
    assert result["rows_reevaluated"] == result["documents"] > 0
    assert corpus_bytes(out) == before  # verification never writes the corpus
    built = build_cleaned_manifest(out / "state", out / "clean")
    original = load_manifest(flow["manifest"])
    cleaned = load_manifest(out / "state" / CLEANED_MANIFEST_FILE)
    assert built["digest"] == cleaned.digest != original.digest
    assert cleaned.kind == "authored_cleaned_input" and cleaned.mode == "authored"
    assert cleaned.data_root.resolve() == (out / "clean").resolve()
    assert [f.path for f in cleaned.files] == [f.path for f in original.files]
    receipt = production_verify.load_production_receipt(out / "state")
    assert cleaned.totals["documents"] == receipt["accounting"]["global"]["keep"]["documents"]
    assert build_cleaned_manifest(out / "state", out / "clean") == built  # idempotent
    with pytest.raises(QualityError, match="not the verified cleaned corpus root"):
        build_cleaned_manifest(out / "state", tmp_path)


def test_verifier_catches_corruption(flow: dict[str, Any], tmp_path: Path) -> None:
    out = tmp_path / "corrupt"
    clean(flow, out)
    verify(flow, out)
    build_cleaned_manifest(out / "state", out / "clean")
    target = out / "clean" / NO_NEWLINE
    good = target.read_bytes()
    target.write_bytes(good.replace(b"Rivers", b"Rovers", 1))  # same size
    with pytest.raises(QualityError, match="SHA-256 or size differs"):
        verify(flow, out)
    # A failed verification withdraws the verification record and the cleaned manifest.
    assert not (out / "state" / VERIFICATION_FILE).exists()
    assert not (out / "state" / CLEANED_MANIFEST_FILE).exists()
    target.write_bytes(good)
    for intruder in (out / "clean" / "extra.jsonl", out / "clean" / (ALL_KEEP + TEMP_SUFFIX)):
        intruder.write_bytes(b"x")
        with pytest.raises(QualityError):
            verify(flow, out)
        intruder.unlink()
    (out / "clean" / ALL_DROP).unlink()
    with pytest.raises(QualityError, match="exactly the expected outputs"):
        verify(flow, out)


def test_output_check_finds_dropped_rows_and_order(
    flow: dict[str, Any], reference: Path, tmp_path: Path
) -> None:
    """The per-file check is independent of the hashes: a dropped row present (or a kept
    row out of order) is found even when the expected SHA-256 and size are satisfied."""
    source = (flow["data"] / NO_NEWLINE).read_bytes().splitlines(keepends=True)
    rows = membership_rows((reference / "state" / DROPPED_MEMBERSHIP).read_bytes())
    mine = [r for r in rows if r["input_path"] == NO_NEWLINE]
    dropped = tuple((r["row"], r["row_sha256"]) for r in mine)
    bad = tmp_path / "bad.jsonl"
    bad.write_bytes(b"".join(source[:-1]))  # all rows except the last, DROP row included
    raw = bad.read_bytes()

    def task(**extra: Any) -> OutputCheckTask:
        return OutputCheckTask(
            ordinal=0,
            output=str(bad),
            file_bytes=len(raw),
            sha256=hashlib.sha256(raw).hexdigest(),
            documents=len(source) - 1,
            canonical_bytes=0,
            line_ceiling=1024**2,
            dropped=dropped,
            **extra,
        )

    assert check_output_file(task()).drop_rows_present == len(mine) > 0
    entry = json.loads(flow["manifest"].read_bytes())["files"]
    meta = next(e for e in entry if e["path"] == NO_NEWLINE)
    with pytest.raises(QualityError, match="byte for byte"):
        check_output_file(
            task(
                source=str(flow["data"] / NO_NEWLINE),
                source_sha256=meta["documents_sha256"],
                source_bytes=meta["file_bytes"],
                source_documents=meta["documents"],
            )
        )


def test_verifier_refuses_tampered_receipt(flow: dict[str, Any], tmp_path: Path) -> None:
    out = tmp_path / "receipt"
    clean(flow, out)
    path = out / "state" / RECEIPT_FILE
    body = json.loads(path.read_bytes())
    body["accounting"]["global"]["drop"]["documents"] += 1
    body["digest"] = canonical.self_digest(body)
    path.write_bytes(canonical.canonical_bytes(body))
    with pytest.raises(QualityError, match="receipt invalid"):
        verify(flow, out)


# -- CLI and progress ------------------------------------------------------------------------------


def test_cli_commands_and_progress(flow: dict[str, Any], tmp_path: Path, capsys: Any) -> None:
    log = tmp_path / "progress.log"
    common = [
        "--manifest",
        str(flow["manifest"]),
        "--policy",
        str(flow["policy"]),
        "--approved-dry-run",
        str(flow["dry"]),
        "--approved-result-digest",
        flow["digest"],
        "--output-root",
        str(tmp_path / "clean"),
        "--state-output",
        str(tmp_path / "state"),
    ]
    code = main(
        [
            "clean-production",
            *common,
            "--workers",
            "2",
            "--max-rss-gib",
            "8",
            "--free-reserve-gib",
            "0",
            "--max-output-gib",
            "1",
            "--deadline-hours",
            "1",
            "--progress-interval-seconds",
            "0.2",
            "--progress-log",
            str(log),
        ]
    )
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert code == 0 and result["complete"] is True, captured.out
    lines = log.read_text(encoding="utf-8").splitlines()
    assert any(" clean " in line or "| clean" in line or "] clean" in line for line in lines)
    final = lines[-1]
    for field in ("files", "docs", "GB", "out", "DROP", "free", "RSS", "CPU", "elapsed"):
        assert field in final, field
    code = main(["clean-production-verify", *common, "--workers", "1", "--compare-sources"])
    assert code == 0 and json.loads(capsys.readouterr().out)["verified"] is True
    code = main(
        [
            "clean-production-manifest",
            "--state-output",
            str(tmp_path / "state"),
            "--output-root",
            str(tmp_path / "clean"),
        ]
    )
    assert code == 0 and json.loads(capsys.readouterr().out)["status"].startswith("CANDIDATE")
    code = main(["clean-production", *common, "--workers", "1"])
    refused = json.loads(capsys.readouterr().out)
    assert code == 1 and "already complete" in refused["error"]


# -- approved dry run with the diagnostic C05 overlay ------------------------------------------


def test_overlay_dry_run_is_bound_without_the_proof(tmp_path: Path) -> None:
    """The real v2 dry run used --c05-proof: its units split each file into c05_kept /
    c05_removed populations. Production needs no proof: the merged per-file statistics
    must still equal its own re-evaluation exactly."""
    from scripts.c05_authored_pilot import KEY
    from scripts.c05_synthetic_flow import KEY_ENV, decide_and_plan, prepare, run_c05

    from production_fixtures import TEMPLATE_V1, TEMPLATE_V2
    from xlm.data.quality.cleaning_policy import freeze_policy
    from xlm.data.quality.cleaning_runner import load_receipt, run_dry_run
    from xlm.data.quality.runner import run_audit

    root = tmp_path / "root"
    with pytest.MonkeyPatch.context() as environment:
        environment.setenv(KEY_ENV, KEY)
        paths = prepare(root)
        proof = Path(run_c05(paths, decide_and_plan(paths))["proof"])
        manifest = root / "manifest.json"
        run_audit(manifest, tmp_path / "audit", limits=limits(), progress_interval=None)
        frozen_v1, frozen_v2 = tmp_path / "v1.yaml", tmp_path / "v2.yaml"
        freeze_policy(TEMPLATE_V1, tmp_path / "audit", frozen_v1)
        freeze_policy(TEMPLATE_V2, tmp_path / "audit", frozen_v2, predecessor=frozen_v1)
        dry = tmp_path / "dry"
        run_dry_run(
            manifest,
            dry,
            frozen_v2,
            limits=limits(),
            progress_interval=None,
            proof=proof,
            allow_authored_proof=True,
        )
    receipt = load_receipt(dry)
    assert receipt["overlay"] is not None
    assert receipt["policy_status"] == "POLICY_WITHIN_GUARDRAILS"
    flow = {
        "manifest": manifest,
        "policy": frozen_v2,
        "dry": dry,
        "digest": receipt["result_digest"],
    }
    out = tmp_path / "out"
    result = clean(flow, out)
    assert result["dry_run_accounting_match"] == "IDENTICAL"
    assert verify(flow, out, compare_sources=True)["verified"] is True
