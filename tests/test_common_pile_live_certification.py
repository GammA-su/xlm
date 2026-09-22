"""Common Pile live certification against 9 REAL pinned rows (read-only).

Reads ``D:\\Project\\xlm-operator-pilot\\adapter-cert-common-pile01`` without
mutating it. Fails closed if the sample is absent: certification cannot be
claimed without the evidence.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from xlm.data.adapters.mix01_adapters import CommonPileAdapter, MissingFieldError
from xlm.data.adapters.source_ids import canonical_source_doc_id

CERT_DIR = Path("D:/Project/xlm-operator-pilot/adapter-cert-common-pile01")
REVISION = "5afc546db324e7f39f297ba757c9a60547151e7c"


def _real_rows() -> list[dict[str, Any]]:
    path = CERT_DIR / "real-records.jsonl"
    assert path.is_file(), "live certification evidence is absent"
    rows = [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    assert len(rows) == 9, "certification sample must hold exactly 9 real rows"
    return rows


def test_certification_evidence_shape() -> None:
    rows = _real_rows()
    files = [row["_cert_source_file"] for row in rows]
    assert files == [
        "news/news.chunk.09.jsonl.gz",
        "news/news.chunk.09.jsonl.gz",
        "news/news.chunk.09.jsonl.gz",
        "libretexts/libretexts.chunk.44.jsonl.gz",
        "libretexts/libretexts.chunk.44.jsonl.gz",
        "libretexts/libretexts.chunk.44.jsonl.gz",
        "public_domain_review/public_domain_review.chunk.56.jsonl.gz",
        "public_domain_review/public_domain_review.chunk.56.jsonl.gz",
        "public_domain_review/public_domain_review.chunk.56.jsonl.gz",
    ]
    assert [row["_cert_source_row"] for row in rows] == [0, 1, 2, 0, 1, 2, 0, 1, 2]
    for row in rows:
        upstream = {k: v for k, v in row.items() if not k.startswith("_cert_")}
        assert set(upstream) == {"text"}
        assert isinstance(row["text"], str)


def test_all_nine_real_rows_accepted() -> None:
    rows = _real_rows()
    adapter = CommonPileAdapter()
    docs = [
        adapter.adapt(
            {k: v for k, v in row.items() if not k.startswith("_cert_")},
            source_file=row["_cert_source_file"],
            source_row=row["_cert_source_row"],
            source_revision=REVISION,
        )
        for row in rows
    ]
    assert len({doc.doc_id for doc in docs}) == 9
    for doc, row in zip(docs, rows, strict=True):
        assert doc.text == row["text"]
        assert len(doc.text) == len(row["text"])
        assert doc.language == "en"
        assert doc.source_id == "common_pile"
        assert doc.source_revision == REVISION
        assert doc.source_file == row["_cert_source_file"]
        assert doc.source_row == row["_cert_source_row"]
        assert doc.doc_id == canonical_source_doc_id(
            "common_pile", row["_cert_source_file"], row["_cert_source_row"]
        )
        assert doc.source_metadata["upstream_component"] == row["_cert_component"]
        assert doc.source_metadata["mix01_component"] == "common_pile_prose"
        assert doc.license_reference == "unknown"
        assert (
            "downstream language cleaning still decides"
            in doc.source_metadata["language_provenance"]
        )
        assert "separate admission review" in doc.source_metadata["license_provenance"]
    again = adapter.adapt(
        {k: v for k, v in rows[0].items() if not k.startswith("_cert_")},
        source_file=rows[0]["_cert_source_file"],
        source_row=rows[0]["_cert_source_row"],
        source_revision=REVISION,
    )
    assert again.to_dict() == docs[0].to_dict()


def test_component_derivation_matrix() -> None:
    adapter = CommonPileAdapter()
    assert adapter.upstream_component("news/news.chunk.09.jsonl.gz") == "news"
    assert adapter.upstream_component("libretexts/libretexts.chunk.44.jsonl.gz") == "libretexts"
    assert (
        adapter.upstream_component("public_domain_review/public_domain_review.chunk.56.jsonl.gz")
        == "public_domain_review"
    )
    assert adapter.upstream_component("a\\b\\shard.jsonl.gz") == "a"
    assert adapter.upstream_component("./news/shard.jsonl.gz") == "news"
    with pytest.raises(MissingFieldError, match="componentless"):
        adapter.upstream_component("common_pile.jsonl")
    with pytest.raises(MissingFieldError, match="componentless"):
        adapter.upstream_component("single-segment")
    with pytest.raises(MissingFieldError, match="cannot derive"):
        adapter.upstream_component("../escape.jsonl.gz")
    with pytest.raises(MissingFieldError, match="non-empty source_file"):
        adapter.upstream_component("")


def test_missing_and_malformed_text() -> None:
    adapter = CommonPileAdapter()
    with pytest.raises(MissingFieldError, match="'text'"):
        adapter.adapt({}, source_file="news/f.jsonl.gz", source_row=0, source_revision="r")
    for bad in (None, 42, ["x"], {"x": 1}, "", True):
        with pytest.raises(MissingFieldError, match="'text'|non-empty string"):
            adapter.adapt(
                {"text": bad},
                source_file="news/f.jsonl.gz",
                source_row=0,
                source_revision="r",
            )


def test_verbatim_preservation() -> None:
    adapter = CommonPileAdapter()
    text = "  padded\twhitespace\n\nand\ttabs  "
    doc = adapter.adapt(
        {"text": text}, source_file="news/f.jsonl.gz", source_row=3, source_revision="r"
    )
    assert doc.text == text
