"""Wiki-Rewrite live certification against 3 REAL pinned rows (read-only).

Reads ``D:\\Project\\xlm-operator-pilot\\adapter-cert-wiki-rewrite01`` without
mutating it. Fails closed if the sample is absent: certification cannot be
claimed without the evidence.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from xlm.data.adapters.mix01_adapters import (
    MissingFieldError,
    RecordRejectedError,
    WikiRewriteAdapter,
)
from xlm.data.adapters.source_ids import canonical_source_doc_id

CERT_DIR = Path("D:/Project/xlm-operator-pilot/adapter-cert-wiki-rewrite01")
REVISION = "9ed3718b5f2ae29074c5e34e64115432b7c4320f"
SOURCE_FILE = "Nemotron-Pretraining-Wiki-Rewrite/part_000003.parquet"
EXPECTED_UUIDS = [
    "628c7183-dca7-44e9-a102-c7a98d788355",
    "c4732004-4147-4273-b57d-8e4d827b5228",
    "a6e54270-f113-4526-bd3c-4568dfd51a6e",
]
EXPECTED_ROWS = [791896, 791897, 791898]


def _real_rows() -> list[dict[str, Any]]:
    path = CERT_DIR / "real-records.jsonl"
    assert path.is_file(), "live certification evidence is absent"
    rows = [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    assert len(rows) == 3, "certification sample must hold exactly 3 real rows"
    return rows


def test_certification_evidence_shape() -> None:
    rows = _real_rows()
    for row in rows:
        assert set(row) == {"text", "license", "metadata", "uuid", "_cert_source_row"}
        assert set(row["metadata"]) == {"category", "models_used"}
    assert [row["_cert_source_row"] for row in rows] == EXPECTED_ROWS
    assert [row["uuid"] for row in rows] == EXPECTED_UUIDS
    assert {row["license"] for row in rows} == {"cc-by-sa-4.0,gfdl"}


def test_all_three_real_rows_accepted() -> None:
    rows = _real_rows()
    adapter = WikiRewriteAdapter()
    docs = [
        adapter.adapt(
            row,
            source_file=SOURCE_FILE,
            source_row=row["_cert_source_row"],
            source_revision=REVISION,
        )
        for row in rows
    ]
    assert len({doc.doc_id for doc in docs}) == 3
    for doc, row in zip(docs, rows, strict=True):
        assert doc.text == row["text"]
        assert len(doc.text) == len(row["text"])
        assert doc.language == "en"
        assert doc.source_id == "nemotron_specialized"
        assert doc.source_revision == REVISION
        assert doc.source_file == SOURCE_FILE
        assert doc.source_row == row["_cert_source_row"]
        assert doc.doc_id == canonical_source_doc_id(
            "wiki_rewrite", SOURCE_FILE, row["_cert_source_row"]
        )
        assert doc.source_metadata["upstream_uuid"] == row["uuid"]
        assert doc.source_metadata["upstream_license"] == "cc-by-sa-4.0,gfdl"
        assert doc.source_metadata["category"] == "Nemotron-Pretraining-Wiki-Rewrite"
        assert doc.source_metadata["models_used"] == "Qwen3-30B-A3B"
        assert doc.source_metadata["mix01_component"] == "nemotron_wiki_rewrite"
        assert doc.source_metadata["config_name"] == "Nemotron-Pretraining-Wiki-Rewrite"
        assert (
            "downstream language cleaning still applies"
            in doc.source_metadata["language_provenance"]
        )
        assert doc.license_reference == "cc-by-4.0"
    again = adapter.adapt(
        rows[0],
        source_file=SOURCE_FILE,
        source_row=rows[0]["_cert_source_row"],
        source_revision=REVISION,
    )
    assert again.to_dict() == docs[0].to_dict()


def test_wrong_category_record_rejected() -> None:
    adapter = WikiRewriteAdapter()
    row = dict(_real_rows()[0])
    row["metadata"] = {**row["metadata"], "category": "Nemotron-Pretraining-Science-QA"}
    with pytest.raises(RecordRejectedError, match="selects category"):
        adapter.adapt(row, source_file="f", source_row=0, source_revision="r")


def test_missing_category_fails_closed() -> None:
    adapter = WikiRewriteAdapter()
    row = dict(_real_rows()[0])
    row["metadata"] = {k: v for k, v in row["metadata"].items() if k != "category"}
    with pytest.raises(MissingFieldError, match="metadata.category"):
        adapter.adapt(row, source_file="f", source_row=0, source_revision="r")


def test_non_string_category_fails_closed() -> None:
    adapter = WikiRewriteAdapter()
    row = dict(_real_rows()[0])
    row["metadata"] = {**row["metadata"], "category": 42}
    with pytest.raises(MissingFieldError, match="metadata.category"):
        adapter.adapt(row, source_file="f", source_row=0, source_revision="r")


def test_missing_and_malformed_top_level_fields() -> None:
    adapter = WikiRewriteAdapter()
    base = _real_rows()[0]
    for key in ("text", "license", "metadata", "uuid"):
        with pytest.raises(MissingFieldError, match=f"'{key}'"):
            adapter.adapt(
                {k: v for k, v in base.items() if k != key},
                source_file="f",
                source_row=0,
                source_revision="r",
            )
    bad_values: list[tuple[str, Any]] = [
        ("non-empty string", {"text": 42}),
        ("non-empty upstream field", {"text": ""}),
        ("non-empty", {"license": ""}),
        ("mapping", {"metadata": ["not", "a", "mapping"]}),
        ("non-empty", {"uuid": "  "}),
        ("non-empty string", {"uuid": 42}),
    ]
    for pattern, override in bad_values:
        record = {**base, **override}
        with pytest.raises(MissingFieldError, match=pattern):
            adapter.adapt(record, source_file="f", source_row=0, source_revision="r")


def test_models_used_optional_lifecycle() -> None:
    adapter = WikiRewriteAdapter()
    base = _real_rows()[0]
    assert (
        adapter.adapt(base, source_file="f", source_row=0, source_revision="r").source_metadata[
            "models_used"
        ]
        == "Qwen3-30B-A3B"
    )
    absent = {**base, "metadata": {k: v for k, v in base["metadata"].items() if k != "models_used"}}
    assert (
        "models_used"
        not in adapter.adapt(
            absent, source_file="f", source_row=0, source_revision="r"
        ).source_metadata
    )
    nulled = {
        **base,
        "metadata": {**base["metadata"], "models_used": None},
    }
    assert (
        "models_used"
        not in adapter.adapt(
            nulled, source_file="f", source_row=0, source_revision="r"
        ).source_metadata
    )
    with pytest.raises(MissingFieldError, match="metadata.models_used"):
        adapter.adapt(
            {**base, "metadata": {**base["metadata"], "models_used": 42}},
            source_file="f",
            source_row=0,
            source_revision="r",
        )
