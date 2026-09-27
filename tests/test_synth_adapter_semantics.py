"""SYNTH adapter row semantics: fatal schema faults vs recorded policy drops.

Authored records only (no real SYNTH rows, nothing under X:\\XLM). Pins the
contract documented on ``SynthExplanationsAdapter``:

| Condition | Result |
|---|---|
| required schema column absent | fatal MissingFieldError |
| required content wrong type | fatal MissingFieldError |
| query / query_seed_text / synthetic_answer null or blank | RecordRejectedError |
| language null/blank or != "en" | RecordRejectedError |
| optional metadata missing / null / blank | omitted |
| optional metadata valid string | preserved verbatim |
| optional metadata wrong type | fatal MissingFieldError |
| synth_id / seed_license null, blank or wrong type | fatal MissingFieldError |
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from xlm.cli.data_cmd import app as data_app
from xlm.data.acquisition.plan import (
    AcquisitionLimits,
    AcquisitionMode,
    AcquisitionPlan,
    load_acquisition_plan,
    save_acquisition_plan,
)
from xlm.data.adapters.mix01_adapters import (
    MissingFieldError,
    RecordRejectedError,
    SynthExplanationsAdapter,
)
from xlm.data.adapters.source_ids import canonical_source_doc_id

CONTENT_FIELDS = ("query", "query_seed_text", "synthetic_answer")
OPTIONAL_FIELDS = ("exercise", "model", "query_seed_url", "additional_seed_url")
STRICT_FIELDS = ("synth_id", "seed_license")
REVISION = "authored-rev-synth"
SOURCE_FILE = "synth_001.parquet"


def _record(**overrides: Any) -> dict[str, Any]:
    record: dict[str, Any] = {
        "synth_id": "authored_1",
        "language": "en",
        "query": "Why do bridges have expansion joints?",
        "query_seed_text": "Bridges expand in summer heat.",
        "synthetic_answer": "Joints absorb thermal expansion.",
        "seed_license": "CC-By-SA (4.0)",
        "exercise": "memorization",
        "model": "authored-model",
        "query_seed_url": "https://example.invalid/seed",
        "additional_seed_url": "https://example.invalid/extra",
        "synthetic_reasoning": "AUTHORED-REASONING-MARKER",
        "words": 42,
    }
    record.update(overrides)
    return record


def _without(key: str) -> dict[str, Any]:
    return {k: v for k, v in _record().items() if k != key}


def _adapt(record: dict[str, Any]) -> Any:
    return SynthExplanationsAdapter().adapt(
        record, source_file=SOURCE_FILE, source_row=0, source_revision=REVISION
    )


def test_valid_english_row_adapts() -> None:
    doc = _adapt(_record())
    assert doc.text == (
        "Context: Bridges expand in summer heat.\n"
        "Question: Why do bridges have expansion joints?\n"
        "Answer: Joints absorb thermal expansion."
    )
    assert doc.language == "en"
    assert "AUTHORED-REASONING-MARKER" not in doc.text
    for key in OPTIONAL_FIELDS:
        assert doc.source_metadata[key] == _record()[key]


@pytest.mark.parametrize("field", CONTENT_FIELDS)
def test_content_absent_is_fatal(field: str) -> None:
    with pytest.raises(MissingFieldError, match=f"'{field}'"):
        _adapt(_without(field))


@pytest.mark.parametrize("field", CONTENT_FIELDS)
@pytest.mark.parametrize("value", [None, "", "   ", "\n\t "])
def test_content_null_or_blank_is_policy_drop(field: str, value: Any) -> None:
    with pytest.raises(RecordRejectedError, match=f"usable '{field}' content"):
        _adapt(_record(**{field: value}))


@pytest.mark.parametrize("field", CONTENT_FIELDS)
@pytest.mark.parametrize("value", [42, 1.5, True, ["text"], {"text": "x"}])
def test_content_wrong_type_is_fatal(field: str, value: Any) -> None:
    with pytest.raises(MissingFieldError, match=f"'{field}'"):
        _adapt(_record(**{field: value}))


def test_content_surrounding_whitespace_preserved_verbatim() -> None:
    doc = _adapt(
        _record(
            query="  padded question?\n",
            query_seed_text="\tseed with tab ",
            synthetic_answer=" answer ",
        )
    )
    assert doc.text == (
        "Context: \tseed with tab \nQuestion:   padded question?\n\nAnswer:  answer "
    )


def test_language_semantics() -> None:
    with pytest.raises(MissingFieldError, match="'language'"):
        _adapt(_without("language"))
    with pytest.raises(MissingFieldError, match="'language'"):
        _adapt(_record(language=7))
    for unlabelled in (None, "", "  "):
        with pytest.raises(RecordRejectedError, match="no usable language label"):
            _adapt(_record(language=unlabelled))
    for other in ("de", "fr", "la", "EN"):
        with pytest.raises(RecordRejectedError, match="explicit 'en' rows only"):
            _adapt(_record(language=other))


@pytest.mark.parametrize("field", OPTIONAL_FIELDS)
@pytest.mark.parametrize("state", ["missing", None, "", "   "])
def test_optional_metadata_absent_null_blank_omitted(field: str, state: Any) -> None:
    record = _without(field) if state == "missing" else _record(**{field: state})
    doc = _adapt(record)
    assert field not in doc.source_metadata


@pytest.mark.parametrize("field", OPTIONAL_FIELDS)
def test_optional_metadata_valid_string_preserved_exactly(field: str) -> None:
    value = "  https://example.invalid/a b?c=ü  "
    assert _adapt(_record(**{field: value})).source_metadata[field] == value


@pytest.mark.parametrize("field", OPTIONAL_FIELDS)
@pytest.mark.parametrize("value", [42, False, ["u"], {"u": 1}])
def test_optional_metadata_wrong_type_is_fatal(field: str, value: Any) -> None:
    with pytest.raises(MissingFieldError, match=f"'{field}'"):
        _adapt(_record(**{field: value}))


@pytest.mark.parametrize("field", STRICT_FIELDS)
@pytest.mark.parametrize("value", ["missing", None, "", "   ", 42])
def test_identity_and_license_stay_strict(field: str, value: Any) -> None:
    record = _without(field) if value == "missing" else _record(**{field: value})
    with pytest.raises(MissingFieldError, match=f"'{field}'"):
        _adapt(record)


@pytest.mark.parametrize(
    ("overrides", "fatal_field"),
    [
        ({"query": "", "seed_license": ""}, "seed_license"),
        ({"query_seed_text": None, "synthetic_answer": 42}, "synthetic_answer"),
        ({"language": "de", "query": 42}, "query"),
        ({"language": None, "exercise": 3}, "exercise"),
        ({"query": "", "words": "many"}, "words"),
    ],
)
def test_fatal_schema_faults_never_hidden_by_policy_drops(
    overrides: dict[str, Any], fatal_field: str
) -> None:
    with pytest.raises(MissingFieldError, match=f"'{fatal_field}'"):
        _adapt(_record(**overrides))


def test_content_drop_reported_before_language_drop() -> None:
    with pytest.raises(RecordRejectedError, match="usable 'synthetic_answer' content"):
        _adapt(_record(language=None, synthetic_answer=""))
    with pytest.raises(RecordRejectedError, match="usable 'query_seed_text' content"):
        _adapt(_record(language="de", query_seed_text="  "))


# ------------------------------------------------------- recorded batch adapt


def _plan(tmp_path: Path, rows: int) -> tuple[Path, str]:
    plan = AcquisitionPlan(
        plan_id="plan_synth_semantics",
        source_id="synth",
        view_id="default",
        provider="https",
        repository="http://127.0.0.1:9/unused",
        revision=REVISION,
        mode=AcquisitionMode.SELECTED_RECORDS,
        selected_files=[SOURCE_FILE],
        row_ranges={SOURCE_FILE: (0, rows)},
        output_artifact_id="raw_synth_semantics",
        is_pilot=True,
        limits=AcquisitionLimits(max_transferred_bytes=1024**2, max_records=100),
    )
    path = tmp_path / "plan.json"
    save_acquisition_plan(plan, path)
    return path, load_acquisition_plan(path).compute_behavioral_hash()


def _selected(rows: list[dict[str, Any]], selection_hash: str) -> str:
    lines = []
    for index, record in enumerate(rows):
        raw = json.dumps(record, ensure_ascii=False).encode("utf-8")
        locator = {
            "source_id": "synth",
            "repository": "http://127.0.0.1:9/unused",
            "revision": REVISION,
            "source_file": SOURCE_FILE,
            "row_index": index,
            "selection_hash": selection_hash,
            "original_record_sha256": hashlib.sha256(raw).hexdigest(),
        }
        lines.append(json.dumps({**record, "_xlm_acquisition": locator}, ensure_ascii=False))
    return "\n".join(lines) + "\n"


def _batch_rows() -> list[dict[str, Any]]:
    def row(index: int, **overrides: Any) -> dict[str, Any]:
        unique = {
            "synth_id": f"authored_{index}",
            "query": f"UNIQUE-QUERY-{index}?",
            "query_seed_text": f"UNIQUE-SEED-{index}.",
            "synthetic_answer": f"UNIQUE-ANSWER-{index}.",
        }
        return _record(**{**unique, **overrides})

    return [
        row(0),  # accepted
        row(1, language="de"),  # language drop
        row(2, language=None),  # unlabelled drop
        row(3, query_seed_text=""),  # content drop
        row(4, synthetic_answer="  "),  # content drop
        row(5, additional_seed_url=""),  # accepted, key omitted
        row(6, query_seed_url=None),  # accepted, key omitted
        row(7, language="la"),  # language drop
        row(8, language=None, synthetic_answer=""),  # overlap: content drop first
        row(9, query=None),  # content drop
        row(10),  # accepted
        row(11, additional_seed_url="   ", exercise=None),  # accepted, keys omitted
    ]


def _adapt_cli(tmp_path: Path, rows: list[dict[str, Any]]) -> Any:
    plan_path, selection_hash = _plan(tmp_path, len(rows))
    selected = tmp_path / "selected.jsonl"
    selected.write_text(_selected(rows, selection_hash), encoding="utf-8")
    return CliRunner().invoke(
        data_app,
        [
            "adapt",
            "--plan",
            str(plan_path),
            "--adapter",
            "synth_en",
            "--input",
            str(selected),
            "--output-dir",
            str(tmp_path / "canonical"),
            "--on-reject",
            "record",
        ],
    )


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def test_record_mode_ledger_for_synth_batch(tmp_path: Path) -> None:
    rows = _batch_rows()
    result = _adapt_cli(tmp_path, rows)
    assert result.exit_code == 0, result.output
    out = tmp_path / "canonical"
    docs = _jsonl(out / "documents.jsonl")
    ledger = _jsonl(out / "adaptation_rejections.jsonl")
    summary = json.loads((out / "adaptation_summary.json").read_text(encoding="utf-8"))

    accepted_rows = [0, 5, 6, 10, 11]
    rejected_rows = [1, 2, 3, 4, 7, 8, 9]
    assert [doc["doc_id"] for doc in docs] == [
        canonical_source_doc_id("synth", SOURCE_FILE, index) for index in accepted_rows
    ]
    assert [entry["source_row"] for entry in ledger] == rejected_rows
    assert summary["accepted_records"] + summary["rejected_records"] == len(rows)
    assert summary["total_input_records"] == len(rows)
    assert (summary["accepted_records"], summary["rejected_records"]) == (5, 7)
    assert all(entry["rejection_category"] == "policy" for entry in ledger)

    reasons = {entry["source_row"]: entry["reason"] for entry in ledger}
    assert "language 'de'" in reasons[1] and "language 'la'" in reasons[7]
    assert "no usable language label" in reasons[2]
    assert "'query_seed_text' content" in reasons[3]
    assert "'synthetic_answer' content" in reasons[4]
    assert "'synthetic_answer' content" in reasons[8]
    assert "'query' content" in reasons[9]

    # Blank/null optional metadata never creates a rejection and is omitted.
    by_row = {doc["source_row"]: doc for doc in docs}
    assert "additional_seed_url" not in by_row[5]["source_metadata"]
    assert "query_seed_url" not in by_row[6]["source_metadata"]
    assert "exercise" not in by_row[11]["source_metadata"]
    assert by_row[0]["source_metadata"]["additional_seed_url"] == "https://example.invalid/extra"

    # The ledger carries no raw training text; canonical output only accepted rows.
    ledger_text = (out / "adaptation_rejections.jsonl").read_text(encoding="utf-8")
    for index in rejected_rows:
        for marker in ("QUERY", "SEED", "ANSWER"):
            assert f"UNIQUE-{marker}-{index}" not in ledger_text
    assert "AUTHORED-REASONING-MARKER" not in ledger_text
    documents_text = (out / "documents.jsonl").read_text(encoding="utf-8")
    for index in rejected_rows:
        assert f"UNIQUE-QUERY-{index}?" not in documents_text


@pytest.mark.parametrize(
    "fatal",
    [
        {"seed_license": ""},
        {"synth_id": None},
        {"query_seed_text": 42},
        {"additional_seed_url": 7},
    ],
)
def test_record_mode_schema_faults_stay_fatal(tmp_path: Path, fatal: dict[str, Any]) -> None:
    rows = _batch_rows()
    rows[6] = {**rows[6], **fatal}
    result = _adapt_cli(tmp_path, rows)
    assert result.exit_code == 1
    assert "refused line 7" in result.output
    out = tmp_path / "canonical"
    for name in ("documents.jsonl", "adaptation_rejections.jsonl", "adaptation_summary.json"):
        assert not (out / name).exists()
