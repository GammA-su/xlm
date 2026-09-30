"""Essential-Web Arm-T blinded review-package materialization.

Every document, locator, receipt and seal here is authored and synthetic;
nothing reads the real Phase-D or selection evidence, no test touches the
network (sockets are blocked), and no real review text exists in any
fixture. The only repository files read are the committed protocol and
rubric sources. Real-evidence materialization is recorded separately in
the T blinded-package report.
"""

from __future__ import annotations

import dataclasses
import hashlib
import importlib.util
import json
import socket
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from xlm.data.evidence_v2 import blinding, canonical, frozen, m_analysis, rubric, t_package

_ROOT = Path(__file__).resolve().parents[1]
_CLI_PATH = _ROOT / "scripts" / "essential_web_t_package.py"
_SPEC = importlib.util.spec_from_file_location("essential_web_t_package", _CLI_PATH)
assert _SPEC is not None and _SPEC.loader is not None
cli = importlib.util.module_from_spec(_SPEC)
sys.modules["essential_web_t_package"] = cli
_SPEC.loader.exec_module(cli)

PROTOCOL_PATH = _ROOT / "docs/implementation/reports/ESSENTIAL-WEB-EVIDENCE-V2-PROTOCOL.md"
RUBRIC_PATH = _ROOT / "src/xlm/data/evidence_v2/rubric.py"
REPO = "test/repo"
REV = "cd" * 20
FILES = tuple(f"data/crawl=CC-TEST-{i}/part-0000{i}.parquet" for i in range(8))
ENTRIES = 118
OVERSIZED_INDEX = 5
GROUP_FIRST = 900
SECRET = bytes(range(32))
ENVIRONMENT = {"python": "fixture", "network": "none"}
CODE = {"fixture.py": {"bytes": 1, "sha256": "00" * 32}}
COMMIT = "ab" * 20

Docs = list[dict[str, Any]]


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_: Any, **__: Any) -> None:
        raise AssertionError("network access attempted in an offline test")

    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _bind(raw: bytes) -> dict[str, Any]:
    return {"bytes": len(raw), "sha256": _sha(raw)}


def _sealed(body: dict[str, Any]) -> bytes:
    body["digest"] = canonical.self_digest(body)
    return canonical.canonical_bytes(body)


def _text(index: int) -> str:
    return (
        f"Authored fixture document number {index}, written only for this test.\n"
        f"It describes an invented topic with the serial marker FIXTURE-{index:04d}.\n"
        f'<script>alert("{index}")</script> & see http://example.invalid/{index}\n'
    )


def _docs() -> Docs:
    docs: Docs = []
    for index in range(ENTRIES):
        row = 1000 + index
        doc: dict[str, Any] = {
            "locator": [REPO, REV, FILES[index * len(FILES) // ENTRIES], row],
            "row_group": 3,
            "row_in_group": row - GROUP_FIRST,
            "t_ordinal": index * len(FILES) // ENTRIES,
        }
        if index == OVERSIZED_INDEX:
            doc.update(status=t_package.OVERSIZED, text=None, sha256=None, utf8_bytes=70000)
        else:
            encoded = _text(index).encode("utf-8")
            doc.update(
                status=t_package.REVIEWABLE,
                text=_text(index),
                sha256=_sha(encoded),
                utf8_bytes=len(encoded),
            )
        docs.append(doc)
    return docs


@dataclasses.dataclass(frozen=True)
class World:
    sources: t_package.Sources
    pins: t_package.Pins
    docs: Docs

    def m_parent(self) -> dict[str, Any]:
        return t_package.verify_m_seal(self.sources.m_seal_dir, self.pins, COMMIT)

    def source(self) -> t_package.TSource:
        return t_package.load_sources(self.sources, self.pins, self.m_parent())

    def build(self, secret: bytes = SECRET, mode: str = t_package.FIRST_MATERIALIZATION) -> Any:
        source = self.source()
        blind = t_package.assign_blinding(source.entries, None, lambda: secret)
        built = t_package.build(
            source, blind, self.m_parent(), self.pins, mode=mode, code=CODE, environment=ENVIRONMENT
        )
        return source, blind, built


def make_world(
    root: Path,
    *,
    doc_hook: Callable[[Docs], None] | None = None,
    selection_hook: Callable[[list[list[Any]]], None] | None = None,
) -> World:
    """Write one complete, hash-consistent synthetic Arm-T evidence chain."""
    docs = _docs()
    identities = [list(doc["locator"]) for doc in docs]
    if doc_hook is not None:
        doc_hook(docs)
    if selection_hook is not None:
        selection_hook(identities)
    phase_d = root / "phase-d"
    (phase_d / "sealed").mkdir(parents=True)
    lines = [canonical.canonical_bytes(doc) for doc in docs]
    documents_raw = b"".join(line + b"\n" for line in lines)
    (phase_d / t_package.DOCUMENTS_NAME).write_bytes(documents_raw)

    provenance_raw = _sealed(
        {
            "kind": "fixture_t_provenance",
            "synthetic": True,
            "locators": [
                {
                    "locator": doc["locator"],
                    "file": doc["locator"][2],
                    "t_ordinal": doc["t_ordinal"],
                    "row_group": doc["row_group"],
                    "row_in_group": doc["row_in_group"],
                    "row_group_first_row": doc["locator"][3] - doc["row_in_group"],
                    "operations": [f"T-{index % 8:02d}-d00"],
                    "chunk_span_half_open": [10, 20],
                    "status": doc["status"],
                    "utf8_bytes": doc["utf8_bytes"],
                    "sha256": doc["sha256"],
                }
                for index, doc in enumerate(docs)
            ],
        }
    )
    (phase_d / t_package.PROVENANCE_NAME).write_bytes(provenance_raw)

    selection_raw = _sealed(
        {
            "kind": "fixture_text_selection",
            "total_selected": len(identities),
            "cells": [
                {"stratum": "fixture_census", "identities": identities[:50]},
                {
                    "stratum": "fixture_ranked",
                    "crawls": [
                        {"crawl": "fixture-a", "identities": identities[50:90]},
                        {"crawl": "fixture-b", "identities": identities[90:]},
                    ],
                },
            ],
        }
    )
    selection_path = root / "selection.json"
    selection_path.write_bytes(selection_raw)
    selection_digest = json.loads(selection_raw)["digest"]

    reviewable = [doc for doc in docs if doc["status"] == t_package.REVIEWABLE]
    counts = {
        t_package.REVIEWABLE: len(reviewable),
        t_package.OVERSIZED: len(docs) - len(reviewable),
        t_package.INVALID: 0,
    }
    text_bytes = sum(doc["utf8_bytes"] for doc in reviewable)
    manifest_raw = _sealed(
        {
            "kind": "fixture_t_acquisition_manifest",
            "synthetic": True,
            "status": "COMPLETE",
            "selected_locators": len(docs),
            "status_counts": counts,
            "selection_digest": selection_digest,
            "scientific_namespace": frozen.PROTOCOL_VERSION,
            "policy_digest": frozen.POLICY_DIGEST,
            "plan_digest": "11" * 32,
            "retained_text_bytes": text_bytes,
            "sealed_outputs": {
                t_package.DOCUMENTS_NAME: _bind(documents_raw),
                t_package.PROVENANCE_NAME: _bind(provenance_raw),
            },
        }
    )
    (phase_d / t_package.T_MANIFEST_NAME).write_bytes(manifest_raw)
    receipt_raw = _sealed(
        {
            "kind": "fixture_phase_d_receipt",
            "synthetic": True,
            "run_status": "COMPLETE",
            "arms": {"T": {"status": "COMPLETE"}},
            "outputs": {
                t_package.DOCUMENTS_NAME: {"arm": "T", **_bind(documents_raw)},
                t_package.PROVENANCE_NAME: {"arm": "T", **_bind(provenance_raw)},
                t_package.T_MANIFEST_NAME: {"arm": "T", **_bind(manifest_raw)},
            },
        }
    )
    (phase_d / t_package.RECEIPT_NAME).write_bytes(receipt_raw)

    bindings_raw = canonical.canonical_bytes(
        {
            "source_binding": _bind(documents_raw),
            "entries": [
                {
                    "source_line": index + 1,
                    "locator_sha256": canonical.digest(doc["locator"]),
                    "status": doc["status"],
                    "utf8_bytes": doc["utf8_bytes"],
                    "text_sha256": doc["sha256"],
                    "record_sha256": _sha(lines[index] + b"\n"),
                }
                for index, doc in enumerate(docs)
            ],
        }
    )
    bindings_path = root / "entry-bindings.json"
    bindings_path.write_bytes(bindings_raw)

    prep_raw = _sealed(
        {
            "arm": "T",
            "entries": ENTRIES,
            "reviewable_full_text_entries": ENTRIES - 1,
            "unreviewable_oversized_entries": 1,
            "full_text_bytes": text_bytes,
            "scientific_namespace": frozen.PROTOCOL_VERSION,
            "selection_digest": selection_digest,
            "policy_digest": frozen.POLICY_DIGEST,
            "blinding": {
                "namespace": frozen.PROTOCOL_VERSION,
                "id_tag": frozen.REVIEW_ID_TAG,
                "review_order_seed": frozen.REVIEW_ORDER_SEED,
                "reviewers": list(frozen.REVIEWERS),
                "prohibited_fields": sorted(frozen.FORBIDDEN_PACKAGE_FIELDS),
            },
            "input": {
                "documents": _bind(documents_raw),
                "provenance": _bind(provenance_raw),
                "selection": _bind(selection_raw),
                "entry_bindings": _bind(bindings_raw),
            },
            "scientific_protocol": _bind(PROTOCOL_PATH.read_bytes()),
            "rubric": _bind(RUBRIC_PATH.read_bytes()),
        }
    )
    prep_path = root / "t-preparation.json"
    prep_path.write_bytes(prep_raw)

    seal_dir = root / "m-seal"
    seal_dir.mkdir()
    result_raw = b'{"fixture": "m result"}\n'
    (seal_dir / "m_result.json").write_bytes(result_raw)
    artifacts = {"m_result.json": _bind(result_raw)}
    listing: dict[str, Any] = {"kind": "fixture_m_artifacts", "artifacts": artifacts}
    listing["digest"] = canonical.self_digest(listing)
    (seal_dir / m_analysis.ARTIFACT_MANIFEST_NAME).write_bytes(m_analysis.dumps(listing))
    seal: dict[str, Any] = {
        "kind": m_analysis.SEAL_KIND,
        "status": "SEALED",
        "arm": "M",
        "sealed_before_other_arm_unblinding": True,
        "other_arm_material_bound": False,
        "scientific_namespace": frozen.PROTOCOL_VERSION,
        "selection_digest": selection_digest,
        "policy_digest": frozen.POLICY_DIGEST,
        "source_revision": REV,
        "source": {"phase_d_receipt": {"sha256": _sha(receipt_raw)}},
        "artifact_manifest_digest": listing["digest"],
        "results": artifacts,
        "selector_decision": "NOT MADE",
        "policy_ranking": "NOT PERFORMED",
        "post_seal_rule": "fixture",
    }
    seal["seal_digest"] = m_analysis.seal_digest_of(seal)
    (seal_dir / m_analysis.SEAL_NAME).write_bytes(m_analysis.dumps(seal))

    pins = t_package.Pins(
        m_seal_digest=seal["seal_digest"],
        preparation_digest=json.loads(prep_raw)["digest"],
        selection_sha256=_sha(selection_raw),
        selection_digest=selection_digest,
        repository=REPO,
        revision=REV,
        source_files=FILES,
        entries=ENTRIES,
        reviewable=ENTRIES - 1,
        oversized=1,
    )
    sources = t_package.Sources(
        preparation=prep_path,
        entry_bindings=bindings_path,
        phase_d_root=phase_d,
        selection=selection_path,
        m_seal_dir=seal_dir,
        protocol=PROTOCOL_PATH,
        rubric_code=RUBRIC_PATH,
    )
    return World(sources=sources, pins=pins, docs=docs)


@pytest.fixture(scope="module")
def world(tmp_path_factory: pytest.TempPathFactory) -> World:
    return make_world(tmp_path_factory.mktemp("t-world"))


@pytest.fixture(scope="module")
def baseline(world: World) -> Any:
    return world.build()


def _reviewer_bytes(built: t_package.Built) -> bytes:
    return b"\n".join(
        raw for name, raw in built.files.items() if name.startswith(tuple(frozen.REVIEWERS))
    )


# --------------------------------------------------------------------------
# Identity
# --------------------------------------------------------------------------


def test_real_pins_are_the_frozen_identity() -> None:
    pins = t_package.REAL_PINS
    assert pins.m_seal_digest == (
        "afc972ccf570a5587bf7bd3b6b8d1d728a09f2230c49f1b0a19c1c74cda0b9bc"
    )
    assert pins.selection_digest == (
        "975ba3dee4af0598e665ea05c69bbe49336c3e3a35fca777a863073190b78474"
    )
    assert pins.revision == "ce4eccc7e9604667b6d7f32cb6274b8b41f3113d"
    assert (pins.entries, pins.reviewable, pins.oversized) == (118, 117, 1)
    assert len(pins.source_files) == 8
    assert frozen.PROTOCOL_VERSION == "essential-web-evidence-v2.0"
    assert frozen.REVIEW_ORDER_SEED == 20260928
    assert frozen.REVIEW_ID_TAG == "ew2-"
    assert frozen.REVIEWERS == ("reviewer-1", "reviewer-2")
    assert frozen.POLICY_DIGEST == (
        "f4357f61f434d5105d823266153dac6372e122f12767187631e8796b4899dd07"
    )


# --------------------------------------------------------------------------
# Materialization
# --------------------------------------------------------------------------


def test_118_entries_become_117_reviewable_and_one_oversized(baseline: Any) -> None:
    source, blind, built = baseline
    ledger = json.loads(built.files[t_package.LEDGER_PATH])
    assert ledger["counts"] == {"entries": 118, "reviewable": 117, "unreviewable_oversized": 1}
    assert len(ledger["entries"]) == 118
    assert len({tuple(row["locator"]) for row in ledger["entries"]}) == 118
    assert built.manifest["counts"]["master_entries"] == 118
    assert built.manifest["counts"]["reviewable"] == 117
    assert built.manifest["counts"]["unreviewable_oversized"] == 1
    assert built.manifest["counts"]["oversized_document_bytes"] == [70000]
    for reviewer in frozen.REVIEWERS:
        package = json.loads(built.files[f"{reviewer}/package.json"])
        assert len(package["forms"]) == 117
        assert built.manifest["reviewers"][reviewer]["reviewable_items"] == 117
    row = ledger["entries"][0]
    assert row["source_file"] == FILES[0] and row["source_row"] == 1000
    assert row["row_group"] == 3 and row["row_group_first_row"] == GROUP_FIRST
    assert row["text_sha256"] == source.entries[0].text_sha256
    assert row["review_id"] == blind.ids[source.entries[0].locator]
    assert built.manifest["human_labels_assigned"] == 0
    assert built.manifest["selector_decision"] == "NOT MADE"


def test_duplicate_locator_refuses(tmp_path: Path) -> None:
    def duplicate(docs: Docs) -> None:
        docs[7]["locator"] = list(docs[6]["locator"])
        docs[7]["row_in_group"] = docs[6]["row_in_group"]

    broken = make_world(tmp_path, doc_hook=duplicate)
    with pytest.raises(t_package.PackageError, match="duplicate locator"):
        broken.source()


def test_missing_locator_refuses(tmp_path: Path) -> None:
    def replace(identities: list[list[Any]]) -> None:
        identities[3] = [REPO, REV, FILES[0], 99999]

    broken = make_world(tmp_path, selection_hook=replace)
    with pytest.raises(t_package.PackageError, match="frozen locator.s. missing"):
        broken.source()


def test_unexpected_locator_refuses(world: World, baseline: Any) -> None:
    source, _, _ = baseline
    frozen_ids = [e.locator for e in source.entries]
    with pytest.raises(t_package.PackageError, match="unexpected locator"):
        t_package._check_membership(source.entries, frozen_ids[:-1], world.pins)
    foreign = dataclasses.replace(source.entries[0], locator=(REPO, REV, "data/other.parquet", 1))
    with pytest.raises(t_package.PackageError, match="outside the frozen"):
        t_package._check_membership(
            [foreign, *source.entries[1:]], [foreign.locator, *frozen_ids[1:]], world.pins
        )


def test_reordered_source_refuses(tmp_path: Path) -> None:
    def swap(docs: Docs) -> None:
        docs[10], docs[11] = docs[11], docs[10]

    broken = make_world(tmp_path, doc_hook=swap)
    with pytest.raises(t_package.PackageError, match="out of frozen acquisition order"):
        broken.source()


def test_text_hash_mismatch_refuses(tmp_path: Path) -> None:
    def corrupt(docs: Docs) -> None:
        docs[9]["sha256"] = "0" * 64

    broken = make_world(tmp_path, doc_hook=corrupt)
    with pytest.raises(t_package.PackageError, match="text hash mismatch"):
        broken.source()


def test_truncated_or_excerpted_oversized_entry_refuses(tmp_path: Path) -> None:
    def excerpt(docs: Docs) -> None:
        docs[OVERSIZED_INDEX]["text"] = "an excerpt"

    broken = make_world(tmp_path, doc_hook=excerpt)
    with pytest.raises(t_package.PackageError, match="oversized entry retains text"):
        broken.source()


def test_source_acquisition_hash_mismatch_refuses(tmp_path: Path) -> None:
    broken = make_world(tmp_path)
    documents = broken.sources.phase_d_root / t_package.DOCUMENTS_NAME
    documents.write_bytes(documents.read_bytes() + b"\n")
    with pytest.raises(t_package.PackageError, match="source T acquisition hash mismatch"):
        broken.source()


def test_acquisition_manifest_drift_refuses(tmp_path: Path) -> None:
    broken = make_world(tmp_path)
    manifest = broken.sources.phase_d_root / t_package.T_MANIFEST_NAME
    manifest.write_bytes(manifest.read_bytes().replace(b"COMPLETE", b"PARTIAL!"))
    with pytest.raises(t_package.PackageError, match="self-digest mismatch"):
        broken.source()


def test_m_seal_mismatch_refuses(tmp_path: Path) -> None:
    good = make_world(tmp_path)
    wrong_pin = dataclasses.replace(good.pins, m_seal_digest="0" * 64)
    with pytest.raises(t_package.PackageError, match="not the sealed M result"):
        t_package.verify_m_seal(good.sources.m_seal_dir, wrong_pin, COMMIT)
    (good.sources.m_seal_dir / "m_result.json").write_bytes(b"changed after sealing\n")
    with pytest.raises(t_package.PackageError, match="M seal does not verify"):
        good.m_parent()


def test_receipt_not_bound_by_m_seal_refuses(world: World) -> None:
    parent = {**world.m_parent(), "phase_d_receipt_sha256": "0" * 64}
    with pytest.raises(t_package.PackageError, match="not the receipt bound by the M seal"):
        t_package.load_sources(world.sources, world.pins, parent)


# --------------------------------------------------------------------------
# Blinding reuse and determinism
# --------------------------------------------------------------------------


def _never() -> bytes:
    raise AssertionError("a new custodian key was generated although one exists")


def test_existing_key_ids_and_orders_are_reused_not_regenerated(
    tmp_path: Path, world: World, baseline: Any
) -> None:
    source, blind, built = baseline
    t_package.write_package(tmp_path, built, blind.secret)
    prior = t_package.load_prior(tmp_path / t_package.CUSTODIAN)
    assert prior is not None and prior.secret == SECRET
    assert prior.ids is not None and prior.orders is not None
    again = t_package.assign_blinding(source.entries, prior, _never)
    assert not again.generated
    assert dict(again.ids) == dict(blind.ids) and dict(again.orders) == dict(blind.orders)
    rebuilt = t_package.build(
        source,
        again,
        world.m_parent(),
        world.pins,
        mode=t_package.FIRST_MATERIALIZATION,
        code=CODE,
        environment=ENVIRONMENT,
    )
    assert dict(rebuilt.files) == dict(built.files)


def test_prior_material_that_differs_refuses(baseline: Any) -> None:
    source, blind, _ = baseline
    first = frozen.REVIEWERS[0]
    swapped = list(blind.orders[first])
    swapped[0], swapped[1] = swapped[1], swapped[0]
    orders = {**blind.orders, first: tuple(swapped)}
    with pytest.raises(t_package.PackageError, match="refusing to regenerate"):
        t_package.assign_blinding(source.entries, t_package.Prior(SECRET, None, orders), _never)
    ids = dict(blind.ids)
    ids[source.entries[0].locator] = "ew2-" + "0" * 64
    with pytest.raises(t_package.PackageError, match="refusing to regenerate"):
        t_package.assign_blinding(source.entries, t_package.Prior(SECRET, ids, None), _never)


def test_prior_ledger_without_its_key_refuses(tmp_path: Path, baseline: Any) -> None:
    _, _, built = baseline
    assert t_package.load_prior(tmp_path) is None
    (tmp_path / "master_ledger.json").write_bytes(built.files[t_package.LEDGER_PATH])
    with pytest.raises(t_package.PackageError, match="without their custodian key"):
        t_package.load_prior(tmp_path)


def test_first_materialization_is_deterministic_given_the_key(world: World, baseline: Any) -> None:
    _, blind, built = baseline
    _, blind_again, built_again = world.build()
    assert dict(built_again.files) == dict(built.files)
    assert dict(built_again.public) == dict(built.public)
    assert built_again.manifest["package_digest"] == built.manifest["package_digest"]
    _, other, built_other = world.build(secret=bytes(range(1, 33)))
    assert set(other.ids.values()).isdisjoint(blind.ids.values())
    assert built_other.manifest["package_digest"] != built.manifest["package_digest"]
    assert blind_again.generated


def test_review_ids_and_orders_follow_the_frozen_construction(baseline: Any) -> None:
    source, blind, built = baseline
    reviewable = [e for e in source.entries if e.reviewable]
    for entry in source.entries:
        expected = blinding.review_id(SECRET, list(entry.locator))
        assert blind.ids[entry.locator] == expected and expected.startswith("ew2-")
    ids = [blind.ids[e.locator] for e in reviewable]
    orders = {}
    for reviewer in frozen.REVIEWERS:
        expected_order = sorted(ids, key=lambda i, r=reviewer: (blinding.order_key(r, i), i))
        package = json.loads(built.files[f"{reviewer}/package.json"])
        assert package["order"] == expected_order == list(blind.orders[reviewer])
        assert [form["review_id"] for form in package["forms"]] == expected_order
        assert package["order_digest"] == canonical.digest(expected_order)
        assert built.manifest["reviewers"][reviewer]["order_digest"] == package["order_digest"]
        orders[reviewer] = expected_order
    first, second = (orders[r] for r in frozen.REVIEWERS)
    assert set(first) == set(second) and first != second
    assert len(set(first)) == len(first) == 117


# --------------------------------------------------------------------------
# Reviewer packages: content, blinding and forms
# --------------------------------------------------------------------------


def test_oversized_entry_never_enters_a_reviewer_package(baseline: Any) -> None:
    source, blind, built = baseline
    oversized = source.entries[OVERSIZED_INDEX]
    assert oversized.text is None and oversized.text_sha256 is None
    hidden = blind.ids[oversized.locator].encode("ascii")
    assert hidden not in _reviewer_bytes(built)
    ledger = json.loads(built.files[t_package.LEDGER_PATH])
    row = ledger["entries"][OVERSIZED_INDEX]
    assert row["status"] == "unreviewable_full_document_due_to_size"
    assert row["reviewable"] is False and row["text_sha256"] is None
    assert row["utf8_bytes"] == 70000
    assert row["order_position"] == {"reviewer-1": None, "reviewer-2": None}
    assert row["dimension_status"] == "not_reviewed"
    assert row["review_id"] == blind.ids[oversized.locator]
    for reviewer in frozen.REVIEWERS:
        names = [n for n in built.files if n.startswith(f"{reviewer}/texts/")]
        assert len(names) == 117


def test_reviewer_packages_hold_every_text_unaltered(baseline: Any) -> None:
    source, blind, built = baseline
    by_id = {blind.ids[e.locator]: e for e in source.entries}
    for reviewer in frozen.REVIEWERS:
        for index, rid in enumerate(blind.orders[reviewer]):
            name = t_package.position_name(index + 1)
            raw = built.files[f"{reviewer}/texts/{name}.txt"]
            entry = by_id[rid]
            assert entry.text is not None and raw == entry.text.encode("utf-8")
            assert _sha(raw) == entry.text_sha256


def test_reviewer_packages_contain_no_forbidden_fields_or_values(
    world: World, baseline: Any
) -> None:
    source, blind, built = baseline
    blob = _reviewer_bytes(built).decode("utf-8")
    for value in (REPO, REV, *FILES, "CC-TEST", SECRET.hex(), "fixture_census", "fixture_ranked"):
        assert value not in blob
    for reviewer in frozen.REVIEWERS:
        for name, raw in built.files.items():
            if name.startswith(f"{reviewer}/") and name.endswith(".json"):
                keys = t_package._keys(json.loads(raw))
                assert not keys & t_package.FORBIDDEN_KEYS, name
                assert not keys & frozen.FORBIDDEN_PACKAGE_FIELDS, name
        package = json.loads(built.files[f"{reviewer}/package.json"])
        assert set(package) == {
            "kind",
            "protocol_version",
            "reviewer",
            "rubric_version",
            "key_commitment",
            "order",
            "order_digest",
            "forms",
        }
        assert set(package["forms"][0]) == {
            "review_id",
            "text",
            "rubric_version",
            "reviewer",
            "labels",
        }
    audit = json.loads(built.files[t_package.AUDIT_PATH])
    assert audit["structural_hits"] == 0
    assert audit["reviewer_files_scanned"] == 2 * (5 + 3 * 117)


@pytest.mark.parametrize(
    ("path", "payload", "match"),
    [
        ("reviewer-1/README.txt", FILES[2].encode(), "source_file value"),
        ("reviewer-1/README.txt", SECRET.hex().encode(), "secret_key value"),
        ("reviewer-1/README.txt", SECRET, "raw secret key bytes"),
        ("reviewer-2/README.txt", b"B-normal", "condition_label pattern"),
        ("reviewer-2/README.txt", b"D-strict", "condition_label pattern"),
        ("reviewer-2/README.txt", b"CC-MAIN-2014-15", "crawl_id pattern"),
        ("reviewer-2/README.txt", b"selector", "generic term"),
        ("reviewer-1/README.txt", b"reviewer-2", "names reviewer-2"),
    ],
)
def test_leakage_audit_refuses_an_injected_leak(
    world: World, baseline: Any, path: str, payload: bytes, match: str
) -> None:
    source, blind, built = baseline
    files = dict(built.files)
    files[path] = files[path] + b"\n" + payload + b"\n"
    with pytest.raises(t_package.PackageError, match=match):
        t_package.leakage_audit(files, source, blind, world.pins)


def test_leakage_audit_inspects_field_names(world: World, baseline: Any) -> None:
    source, blind, built = baseline
    for field in ("stratum", "locator", "sha256", "unlisted_field"):
        files = dict(built.files)
        order = json.loads(files["reviewer-1/order.json"])
        order[field] = "x"
        files["reviewer-1/order.json"] = t_package.dumps(order)
        with pytest.raises(t_package.PackageError, match=f"'{field}'"):
            t_package.leakage_audit(files, source, blind, world.pins)
    files = dict(built.files)
    hidden = blind.ids[source.entries[OVERSIZED_INDEX].locator]
    files["reviewer-2/README.txt"] += hidden.encode("ascii")
    with pytest.raises(t_package.PackageError, match="unreviewable_review_id"):
        t_package.leakage_audit(files, source, blind, world.pins)


def test_views_are_escaped_and_round_trip(baseline: Any) -> None:
    _, _, built = baseline
    page = built.files["reviewer-1/view/0001.html"]
    assert b"<script" not in page and b"<a " not in page and b"href" not in page
    assert b"&lt;script&gt;" in page and b"default-src 'none'" in page
    text = built.files["reviewer-1/texts/0001.txt"].decode("utf-8")
    assert "<script>" in text
    assert t_package.view_text(page) == text


def test_forms_are_blank_and_carry_all_18_dimensions(baseline: Any) -> None:
    _, blind, built = baseline
    for reviewer in frozen.REVIEWERS:
        package = json.loads(built.files[f"{reviewer}/package.json"])
        assert all(form["labels"] is None for form in package["forms"])
        form = json.loads(built.files[f"{reviewer}/forms/0001.json"])
        assert set(form) == set(rubric.FORM_FIELDS)
        assert form["review_id"] == blind.orders[reviewer][0] and form["reviewer"] == reviewer
        assert list(form["dimensions"]) == sorted(d["name"] for d in rubric.DIMENSIONS)
        assert len(form["dimensions"]) == 18
        assert all(value is None for value in form["dimensions"].values())
        assert form["dimension_notes"] == {}
        for field in ("submitted_utc", "expertise", "confidence", "disposition_rationale"):
            assert form[field] is None
        errors = rubric.validate_form(form)
        assert sum("missing judgment" in error for error in errors) == 18


def test_rubric_is_bound_verbatim(baseline: Any) -> None:
    source, _, built = baseline
    protocol = PROTOCOL_PATH.read_bytes()
    excerpt = built.files["reviewer-1/rubric.md"]
    assert excerpt == built.files["reviewer-2/rubric.md"] == source.rubric_excerpt
    assert excerpt in protocol and excerpt.startswith(b"## 8. Frozen human review rubric")
    body = json.loads(built.files["reviewer-1/rubric.json"])
    assert [d["name"] for d in body["dimensions"]] == [d["name"] for d in rubric.DIMENSIONS]
    assert [d["levels"] for d in body["dimensions"]] == [
        list(d["levels"]) for d in rubric.DIMENSIONS
    ]
    assert [d["anchors"] for d in body["dimensions"]] == [d["anchors"] for d in rubric.DIMENSIONS]
    bound = built.manifest["rubric"]
    assert bound["dimensions"] == 18 and bound["rubric_version"] == rubric.RUBRIC_VERSION
    assert bound["protocol_section_8"]["sha256"] == _sha(excerpt)
    assert bound["rubric_code"]["sha256"] == _sha(RUBRIC_PATH.read_bytes())
    altered = protocol.replace(b"`impaired_but_readable`", b"`hard_to_read`")
    with pytest.raises(t_package.PackageError, match="dimension 2 differs"):
        t_package.rubric_excerpt(altered)


# --------------------------------------------------------------------------
# Commitments and Git-safe bindings
# --------------------------------------------------------------------------


def test_changing_a_text_changes_the_package_hashes(tmp_path: Path, baseline: Any) -> None:
    _, _, built = baseline

    def edit(docs: Docs) -> None:
        text = docs[20]["text"] + "one more authored sentence.\n"
        docs[20].update(
            text=text, sha256=_sha(text.encode("utf-8")), utf8_bytes=len(text.encode("utf-8"))
        )

    _, _, changed = make_world(tmp_path, doc_hook=edit).build()
    for reviewer in frozen.REVIEWERS:
        before, after = (b.manifest["reviewers"][reviewer] for b in (built, changed))
        assert before["package_json"]["sha256"] != after["package_json"]["sha256"]
        assert before["tree_digest"] != after["tree_digest"]
        # The locators, and therefore the opaque IDs and the order, are unchanged.
        assert before["order_digest"] == after["order_digest"]
    assert (
        built.manifest["custodian"]["tree_digest"] != changed.manifest["custodian"]["tree_digest"]
    )
    assert built.manifest["package_digest"] != changed.manifest["package_digest"]


def test_changing_the_order_changes_the_commitment(baseline: Any) -> None:
    _, blind, built = baseline
    first = frozen.REVIEWERS[0]
    swapped = list(blind.orders[first])
    swapped[3], swapped[4] = swapped[4], swapped[3]
    altered = dataclasses.replace(blind, orders={**blind.orders, first: tuple(swapped)})
    before = t_package.reviewer_commitment(built.files, blind, first)
    after = t_package.reviewer_commitment(built.files, altered, first)
    assert before["order_digest"] == built.manifest["reviewers"][first]["order_digest"]
    assert before["order_digest"] != after["order_digest"]


def test_no_text_secret_id_or_mapping_enters_the_git_artifacts(baseline: Any) -> None:
    source, blind, built = baseline
    assert set(built.public) == {
        "package_manifest.json",
        "reviewer1_commitment.json",
        "reviewer2_commitment.json",
        "custodian_commitment.json",
        "rubric_binding.json",
        "m_seal_parent.json",
        "leakage_audit.json",
        "artifact_manifest.json",
    }
    blob = b"\n".join(built.public.values())
    text = blob.decode("utf-8")
    assert SECRET not in blob and SECRET.hex() not in text
    assert "FIXTURE-" not in text and "Authored fixture document" not in text
    assert not any(rid in text for rid in blind.ids.values())
    assert not any(name in text for name in FILES)
    manifest = json.loads(built.public["package_manifest.json"])
    assert manifest["blinding"]["key_commitment"] == _sha(SECRET)
    assert (
        manifest["m_seal_parent"]["seal_digest"] == built.manifest["m_seal_parent"]["seal_digest"]
    )
    listing = json.loads(built.public["artifact_manifest.json"])
    assert canonical.self_digest(listing) == listing["digest"]
    for name, bound in listing["artifacts"].items():
        assert bound == _bind(built.public[name])
    leaking = {**built.public, "extra.json": source.entries[0].text.encode("utf-8")}
    with pytest.raises(t_package.PackageError, match="document text would enter"):
        t_package.assert_public_safe(leaking, source, blind)
    with pytest.raises(t_package.PackageError, match="review ID would enter"):
        t_package.assert_public_safe(
            {"x.json": next(iter(blind.ids.values())).encode()}, source, blind
        )
    with pytest.raises(t_package.PackageError, match="secret key would enter"):
        t_package.assert_public_safe({"x.json": SECRET.hex().encode()}, source, blind)


def test_package_manifest_binds_the_m_seal_and_sources(world: World, baseline: Any) -> None:
    _, _, built = baseline
    manifest = built.manifest
    assert manifest["m_seal_parent"]["seal_digest"] == world.pins.m_seal_digest
    assert manifest["m_seal_parent"]["result_commit"] == COMMIT
    assert manifest["source"]["documents"]["sha256"] == _sha(
        (world.sources.phase_d_root / t_package.DOCUMENTS_NAME).read_bytes()
    )
    assert manifest["review_identity"]["order_seed"] == 20260928
    assert manifest["scientific_namespace"] == "essential-web-evidence-v2.0"
    assert manifest["code"] == CODE
    body = {k: v for k, v in manifest.items() if k != "package_digest"}
    assert canonical.digest(body) == manifest["package_digest"]


# --------------------------------------------------------------------------
# Write, verify and location
# --------------------------------------------------------------------------


def test_write_then_verify_and_refuse_any_later_change(tmp_path: Path, baseline: Any) -> None:
    _, blind, built = baseline
    root, out = tmp_path / "package", tmp_path / "git"
    t_package.write_package(root, built, blind.secret)
    for name, raw in built.public.items():
        canonical.write_atomic(out / name, raw)
    assert (root / t_package.KEY_PATH).read_bytes() == SECRET
    assert (
        _sha((root / t_package.KEY_PATH).read_bytes())
        == (built.manifest["blinding"]["key_commitment"])
    )
    assert not list(out.rglob("*.txt")) and not list(out.rglob("*.bin"))
    t_package.verify_package(root, out, built, blind.secret)
    with pytest.raises(t_package.PackageError, match="already holds a sealed package"):
        t_package.write_package(root, built, blind.secret)
    target = root / "reviewer-1" / "texts" / "0003.txt"
    original = target.read_bytes()
    target.write_bytes(original + b"edited")
    with pytest.raises(t_package.PackageError, match="changed after sealing"):
        t_package.verify_package(root, out, built, blind.secret)
    target.write_bytes(original)
    (root / "reviewer-2" / "notes.txt").write_bytes(b"stray")
    with pytest.raises(t_package.PackageError, match="file set differs"):
        t_package.verify_package(root, out, built, blind.secret)
    (root / "reviewer-2" / "notes.txt").unlink()
    (out / "rubric_binding.json").write_bytes(b"{}")
    with pytest.raises(t_package.PackageError, match="Git binding differs"):
        t_package.verify_package(root, out, built, blind.secret)


def test_write_refuses_a_different_existing_key(tmp_path: Path, baseline: Any) -> None:
    _, blind, built = baseline
    t_package.write_key(tmp_path, bytes(32))
    with pytest.raises(t_package.PackageError, match="existing custodian key differs"):
        t_package.write_package(tmp_path, built, blind.secret)
    assert t_package.existing_files(tmp_path) == {t_package.KEY_PATH}


def test_package_root_must_be_outside_git_and_the_evidence_roots(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence"
    with pytest.raises(t_package.PackageError, match="must be outside"):
        cli.refuse_location(_ROOT / "docs" / "package", [_ROOT, evidence])
    with pytest.raises(t_package.PackageError, match="must be outside"):
        cli.refuse_location(evidence / "phase-d" / "sealed", [_ROOT, evidence])
    cli.refuse_location(tmp_path / "review", [_ROOT, evidence])
