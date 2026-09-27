"""SYNTH live certification against REAL pinned rows (read-only evidence).

Reads ``D:\\Project\\xlm-operator-pilot\\adapter-cert-synth01`` without
mutating it. Joins core + seed-context records on ``synth_id`` to build the
3 real logical records of shard ``synth_001.parquet``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from xlm.data.adapters.mix01_adapters import (
    MissingFieldError,
    RecordRejectedError,
    SynthExplanationsAdapter,
)
from xlm.data.adapters.source_ids import canonical_source_doc_id

CERT_DIR = Path("D:/Project/xlm-operator-pilot/adapter-cert-synth01")
REVISION = "0d6813a2966662c39f22f0b9af28a0c1c9f7a437"
SOURCE_FILE = "synth_001.parquet"


def _read_lines(name: str) -> list[dict[str, Any]]:
    path = CERT_DIR / name
    assert path.is_file(), "live certification evidence is absent"
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def _real_records() -> list[dict[str, Any]]:
    core = _read_lines("real-core-records.jsonl")
    seed = {row["synth_id"]: row for row in _read_lines("real-seed-context.jsonl")}
    assert len(core) == 3
    records = []
    for row in core:
        context = seed[row["synth_id"]]
        records.append(
            {
                **row,
                "query_seed_text": context["query_seed_text"],
                "query_seed_url": context["query_seed_url"],
                "additional_seed_url": context["additional_seed_url"],
            }
        )
    return records


def _base_record(**overrides: Any) -> dict[str, Any]:
    record: dict[str, Any] = {
        "synth_id": "authored_s",
        "language": "en",
        "query": "Why is the sky blue?",
        "query_seed_text": "The sky is blue because of Rayleigh scattering.",
        "query_seed_url": None,
        "additional_seed_url": None,
        "seed_license": "CC-By-SA (4.0)",
        "exercise": "memorization",
        "model": "authored-model",
        "synthetic_answer": "Because of Rayleigh scattering.",
        "synthetic_reasoning": "Authored reasoning that stays excluded.",
        "words": 42,
    }
    record.update(overrides)
    return record


def test_real_row_0_german_rejected() -> None:
    adapter = SynthExplanationsAdapter()
    rows = _real_records()
    assert rows[0]["synth_id"] == "memorization_german_10_150696"
    assert rows[0]["language"] == "de"
    with pytest.raises(RecordRejectedError, match="explicit 'en' rows only"):
        adapter.adapt(rows[0], source_file=SOURCE_FILE, source_row=0, source_revision=REVISION)


def test_real_rows_1_and_2_accepted() -> None:
    adapter = SynthExplanationsAdapter()
    rows = _real_records()
    assert rows[1]["synth_id"] == "memorization_94_6343"
    assert rows[2]["synth_id"] == "memorization_82_52457"
    for index in (1, 2):
        row = rows[index]
        doc = adapter.adapt(
            row, source_file=SOURCE_FILE, source_row=index, source_revision=REVISION
        )
        assert doc.text == (
            f"Context: {row['query_seed_text']}\n"
            f"Question: {row['query']}\n"
            f"Answer: {row['synthetic_answer']}"
        )
        assert "Explanation:" not in doc.text
        assert doc.language == "en"
        assert doc.source_id == "synth"
        assert doc.source_revision == REVISION
        assert doc.source_file == SOURCE_FILE
        assert doc.source_row == index
        assert doc.doc_id == canonical_source_doc_id("synth", SOURCE_FILE, index)
        assert doc.source_metadata["synth_id"] == row["synth_id"]
        assert doc.source_metadata["seed_license"] == "CC-By-SA (4.0)"
        assert doc.source_metadata["exercise"] == "memorization"
        assert doc.source_metadata["reasoning_treatment"] == "excluded"
        assert doc.license_reference == "cc-by-4.0"
        again = adapter.adapt(
            row, source_file=SOURCE_FILE, source_row=index, source_revision=REVISION
        )
        assert again.to_dict() == doc.to_dict()


def test_real_rows_carry_no_reasoning_leak() -> None:
    adapter = SynthExplanationsAdapter()
    rows = _real_records()
    doc = adapter.adapt(rows[1], source_file=SOURCE_FILE, source_row=1, source_revision=REVISION)
    dumped_metadata = json.dumps(doc.source_metadata, ensure_ascii=False)
    assert "kaleidoscope" not in doc.text
    assert "kaleidoscope" not in dumped_metadata
    assert "Dihedral group structure" not in doc.text


def test_old_fake_shape_refused() -> None:
    adapter = SynthExplanationsAdapter()
    with pytest.raises(MissingFieldError, match="'synth_id'"):
        adapter.adapt(
            {
                "context": "c",
                "question": "q",
                "explanation": "e",
                "language": "en",
            },
            source_file="f",
            source_row=0,
            source_revision="r",
        )


def test_missing_and_malformed_render_fields() -> None:
    adapter = SynthExplanationsAdapter()
    for key in ("query", "query_seed_text", "synthetic_answer", "seed_license", "synth_id"):
        with pytest.raises(MissingFieldError, match=f"'{key}'"):
            adapter.adapt(
                {k: v for k, v in _base_record().items() if k != key},
                source_file="f",
                source_row=0,
                source_revision="r",
            )
    for key in ("query", "query_seed_text", "synthetic_answer"):
        with pytest.raises(MissingFieldError, match=f"'{key}'"):
            adapter.adapt(
                _base_record(**{key: 42}),
                source_file="f",
                source_row=0,
                source_revision="r",
            )
        # Unusable content is a recorded policy drop, not a schema failure.
        with pytest.raises(RecordRejectedError, match=f"'{key}'"):
            adapter.adapt(
                _base_record(**{key: ""}),
                source_file="f",
                source_row=0,
                source_revision="r",
            )


def test_language_policy() -> None:
    adapter = SynthExplanationsAdapter()
    with pytest.raises(RecordRejectedError, match="explicit 'en' rows only"):
        adapter.adapt(
            _base_record(language="de"), source_file="f", source_row=0, source_revision="r"
        )
    with pytest.raises(RecordRejectedError, match="explicit 'en' rows only"):
        adapter.adapt(
            _base_record(language="fr"), source_file="f", source_row=0, source_revision="r"
        )
    with pytest.raises(RecordRejectedError, match="no usable language label"):
        adapter.adapt(
            _base_record(language=None), source_file="f", source_row=0, source_revision="r"
        )
    with pytest.raises(RecordRejectedError, match="no usable language label"):
        adapter.adapt(
            _base_record(language="  "), source_file="f", source_row=0, source_revision="r"
        )
    with pytest.raises(MissingFieldError, match="'language'"):
        adapter.adapt(
            {k: v for k, v in _base_record().items() if k != "language"},
            source_file="f",
            source_row=0,
            source_revision="r",
        )
    with pytest.raises(MissingFieldError, match="'language'"):
        adapter.adapt(_base_record(language=42), source_file="f", source_row=0, source_revision="r")
    doc = adapter.adapt(_base_record(), source_file="f", source_row=0, source_revision="r")
    assert doc.language == "en"


def test_reasoning_presence_never_rejects_and_never_leaks() -> None:
    adapter = SynthExplanationsAdapter()
    marker = "REASONING-MARKER-XYZ-123"
    doc = adapter.adapt(
        _base_record(synthetic_reasoning=marker),
        source_file="f",
        source_row=0,
        source_revision="r",
    )
    assert marker not in doc.text
    assert marker not in json.dumps(doc.source_metadata, ensure_ascii=False)
    assert doc.source_metadata["reasoning_treatment"] == "excluded"
    bare = adapter.adapt(
        {k: v for k, v in _base_record().items() if k != "synthetic_reasoning"},
        source_file="f",
        source_row=0,
        source_revision="r",
    )
    assert bare.source_metadata["reasoning_treatment"] == "excluded"


def test_optional_metadata_absent_or_null() -> None:
    adapter = SynthExplanationsAdapter()
    record = {k: v for k, v in _base_record().items() if k not in ("query_seed_url", "words")}
    doc = adapter.adapt(record, source_file="f", source_row=0, source_revision="r")
    assert "query_seed_url" not in doc.source_metadata
    assert "words" not in doc.source_metadata
    assert doc.source_metadata["exercise"] == "memorization"
    nulled = adapter.adapt(
        _base_record(query_seed_url=None, additional_seed_url=None, words=None),
        source_file="f",
        source_row=0,
        source_revision="r",
    )
    assert "query_seed_url" not in nulled.source_metadata
    assert nulled.source_metadata["seed_license"] == "CC-By-SA (4.0)"


def test_malformed_optional_metadata_refused() -> None:
    adapter = SynthExplanationsAdapter()
    with pytest.raises(MissingFieldError, match="'query_seed_url'"):
        adapter.adapt(
            _base_record(query_seed_url=42), source_file="f", source_row=0, source_revision="r"
        )
    with pytest.raises(MissingFieldError, match="'words'"):
        adapter.adapt(
            _base_record(words="many"), source_file="f", source_row=0, source_revision="r"
        )
    with pytest.raises(MissingFieldError, match="'seed_license'"):
        adapter.adapt(
            _base_record(seed_license=""), source_file="f", source_row=0, source_revision="r"
        )


def test_central_doc_ids_preserved() -> None:
    adapter = SynthExplanationsAdapter()
    first = adapter.adapt(
        _base_record(), source_file="a/synth_001.parquet", source_row=0, source_revision="r"
    )
    second = adapter.adapt(
        _base_record(), source_file="b/synth_001.parquet", source_row=0, source_revision="r"
    )
    assert first.doc_id == canonical_source_doc_id("synth", "a/synth_001.parquet", 0)
    assert first.doc_id != second.doc_id
    assert (
        adapter.adapt(
            _base_record(), source_file="a/synth_001.parquet", source_row=0, source_revision="r"
        ).doc_id
        == first.doc_id
    )
