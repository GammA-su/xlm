"""Essential-Web live certification against 3 REAL pinned rows (read-only).

Reads ``D:\\Project\\xlm-operator-pilot\\adapter-cert-essential-web01`` without
mutating it. Fails closed if the sample is absent: certification cannot be
claimed without the evidence.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from xlm.data.adapters.mix01_adapters import (
    ESSENTIAL_WEB_COMPONENTS,
    EssentialWebAdapter,
    EssentialWebSelectedAdapter,
    MissingFieldError,
    RecordRejectedError,
)
from xlm.data.adapters.source_ids import canonical_source_doc_id

CERT_DIR = Path("D:/Project/xlm-operator-pilot/adapter-cert-essential-web01")
RECORDS_PATH = CERT_DIR / "real-records.jsonl"
META_PATH = CERT_DIR / "repository-meta.json"

CERT_FILES = [
    "data/v1/train/00001.parquet",
    "data/v1/train/00002.parquet",
    "data/v1/train/00003.parquet",
]


def _repository_meta() -> dict[str, Any]:
    assert META_PATH.is_file(), "live certification evidence is absent"
    return json.loads(META_PATH.read_text(encoding="utf-8"))


def _real_rows() -> list[dict[str, Any]]:
    assert RECORDS_PATH.is_file(), "live certification evidence is absent"
    rows = [
        json.loads(line)
        for line in RECORDS_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(rows) == 3, "certification sample must hold exactly 3 real rows"
    return rows


def _revision() -> str:
    revision = _repository_meta()["sha"]
    assert isinstance(revision, str) and len(revision) == 40
    return revision


def test_certification_evidence_shape() -> None:
    meta = _repository_meta()
    assert meta["repository"] == "EssentialAI/essential-web-v1.0"
    assert meta["license"] == "odc-by"
    rows = _real_rows()
    for row in rows:
        assert set(row) == {
            "eai_taxonomy",
            "id",
            "line_start_n_end_idx",
            "metadata",
            "pid",
            "quality_signals",
            "text",
        }
        assert "taxonomy" not in row
        assert "quality_tier" not in row
        assert "language" not in row


def test_all_three_real_rows_adapt() -> None:
    revision = _revision()
    rows = _real_rows()
    adapter = EssentialWebAdapter("essential_science")
    docs = [
        adapter.adapt(
            row, source_file=CERT_FILES[index], source_row=index, source_revision=revision
        )
        for index, row in enumerate(rows)
    ]
    assert len({doc.doc_id for doc in docs}) == 3
    for index, (doc, row) in enumerate(zip(docs, rows, strict=True)):
        assert doc.text == row["text"]
        assert doc.text == row["text"] and len(doc.text) == len(row["text"])
        assert doc.language == "en"
        assert doc.source_id == "essential_web"
        assert doc.source_revision == revision
        assert doc.source_file == CERT_FILES[index]
        assert doc.source_row == index
        assert doc.doc_id == canonical_source_doc_id("essential_web", CERT_FILES[index], index)
        assert doc.source_metadata["upstream_id"] == row["id"]
        assert doc.source_metadata["pid"] == row["pid"]
        assert (
            doc.source_metadata["fasttext_english"] == row["quality_signals"]["fasttext"]["english"]
        )
        assert doc.source_metadata["mix01_component"] == "essential_science"
        assert "taxonomy" not in doc.source_metadata
        assert "quality_tier" not in doc.source_metadata


def test_real_row_metadata_bounded_and_truthful() -> None:
    revision = _revision()
    row = _real_rows()[0]
    doc = EssentialWebAdapter("essential_practical").adapt(
        row, source_file=CERT_FILES[0], source_row=0, source_revision=revision
    )
    metadata = doc.source_metadata
    assert metadata["source_domain"] == row["metadata"]["source_domain"]
    assert metadata["snapshot_id"] == row["metadata"]["snapshot_id"]
    assert metadata["fdc_primary_code"] == "746.92"
    assert metadata["fdc_level_1"] == "Arts"
    assert metadata["document_type_v2_primary"] == "Personal Blog"
    assert metadata["bloom_knowledge_domain_primary"] == "Conceptual"
    assert metadata["bloom_cognitive_process_primary"] == "Understand"
    assert (
        metadata["fasttext_fineweb_edu_approx"]
        == row["quality_signals"]["fasttext"]["fineweb_edu_approx"]
    )
    assert "warc_info" not in metadata
    assert "warc_metadata" not in metadata
    assert "url" not in metadata
    assert "line_start_n_end_idx" not in metadata
    assert "red_pajama_v2" not in metadata
    assert "downstream language cleaning still decides" in metadata["language_provenance"]


def test_each_explicit_component_certifies_real_row() -> None:
    assert tuple(ESSENTIAL_WEB_COMPONENTS) == (
        "essential_science",
        "essential_practical",
        "essential_prose",
    )
    revision = _revision()
    row = _real_rows()[1]
    seen: set[str] = set()
    for component in ESSENTIAL_WEB_COMPONENTS:
        doc = EssentialWebAdapter(component).adapt(
            row, source_file=CERT_FILES[1], source_row=1, source_revision=revision
        )
        assert doc.source_metadata["mix01_component"] == component
        seen.add(doc.doc_id)
    # One upstream document keeps one identity however it is slice-assigned.
    assert seen == {
        canonical_source_doc_id("essential_web", CERT_FILES[1], 1),
    }


def test_adaptation_is_deterministic() -> None:
    revision = _revision()
    rows = _real_rows()
    adapter = EssentialWebAdapter("essential_prose")
    first = adapter.adapt(
        rows[2], source_file=CERT_FILES[2], source_row=2, source_revision=revision
    )
    second = adapter.adapt(
        rows[2], source_file=CERT_FILES[2], source_row=2, source_revision=revision
    )
    assert first.to_dict() == second.to_dict()


def test_old_authored_shape_refused() -> None:
    adapter = EssentialWebAdapter("essential_science")
    with pytest.raises(MissingFieldError, match="'eai_taxonomy'"):
        adapter.adapt(
            {"text": "t", "taxonomy": "science", "quality_tier": "x", "language": "en"},
            source_file="f",
            source_row=0,
            source_revision="r",
        )


def test_wrong_schema_variants_refused() -> None:
    adapter = EssentialWebAdapter("essential_science")
    base = dict(_real_rows()[0])
    cases: list[tuple[str, dict[str, Any]]] = [
        ("'text'", {k: v for k, v in base.items() if k != "text"}),
        ("'eai_taxonomy'", {k: v for k, v in base.items() if k != "eai_taxonomy"}),
        ("'quality_signals'", {k: v for k, v in base.items() if k != "quality_signals"}),
        ("'id'", {k: v for k, v in base.items() if k != "id"}),
        ("'pid'", {k: v for k, v in base.items() if k != "pid"}),
        ("'pid'", {**base, "pid": "   "}),
        ("'metadata'", {k: v for k, v in base.items() if k != "metadata"}),
    ]
    for pattern, record in cases:
        with pytest.raises(MissingFieldError, match=pattern):
            adapter.adapt(record, source_file="f", source_row=0, source_revision="r")
    bad_types: list[tuple[str, dict[str, Any]]] = [
        ("non-empty string", {**base, "text": 42}),
        ("mapping", {**base, "eai_taxonomy": ["not", "a", "mapping"]}),
        ("mapping", {**base, "quality_signals": "nope"}),
        ("integer", {**base, "id": "42"}),
        ("number", {**base, "quality_signals": {"fasttext": {"english": "high"}}}),
    ]
    for pattern, record in bad_types:
        with pytest.raises(MissingFieldError, match=pattern):
            adapter.adapt(record, source_file="f", source_row=0, source_revision="r")


def test_malformed_nested_classifier_refused() -> None:
    import copy

    adapter = EssentialWebAdapter("essential_science")
    broken = copy.deepcopy(_real_rows()[0])
    broken["eai_taxonomy"]["document_type_v2"] = ["Personal Blog"]
    with pytest.raises(MissingFieldError, match="mapping when present"):
        adapter.adapt(broken, source_file="f", source_row=0, source_revision="r")
    broken = copy.deepcopy(_real_rows()[0])
    broken["eai_taxonomy"]["document_type_v2"] = {"primary": "Personal Blog"}
    with pytest.raises(MissingFieldError, match="mapping"):
        adapter.adapt(broken, source_file="f", source_row=0, source_revision="r")


def test_frozen_selector_adapter_on_the_real_rows() -> None:
    """The production B-normal adapter runs end to end on the three real rows.

    Each real row is admitted by at most one component, exactly the one the
    frozen selector names, and an admitted document keeps the certified
    verbatim rendering and identity.
    """
    revision = _revision()
    adapters = {name: EssentialWebSelectedAdapter(name) for name in ESSENTIAL_WEB_COMPONENTS}
    for index, row in enumerate(_real_rows()):
        finals = {name: adapter.selector_final(row) for name, adapter in adapters.items()}
        assert len(set(finals.values())) == 1
        final = finals["essential_science"]
        admitted: list[str] = []
        for name, adapter in adapters.items():
            try:
                doc = adapter.adapt(
                    row, source_file=CERT_FILES[index], source_row=index, source_revision=revision
                )
            except RecordRejectedError:
                continue
            admitted.append(name)
            assert doc.text == row["text"]
            assert doc.doc_id == canonical_source_doc_id("essential_web", CERT_FILES[index], index)
            assert doc.source_metadata["mix01_component"] == name
            assert doc.source_metadata["essential_web_selector"] == "B-normal"
        assert admitted == ([final] if final in ESSENTIAL_WEB_COMPONENTS else [])
