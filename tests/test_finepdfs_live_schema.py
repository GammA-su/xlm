"""Focused live-schema repair tests for the FinePDFs-Edu adapter.

The pinned live sample (``HuggingFaceFW/finepdfs-edu`` @ ``9cfabe2``,
config ``eng_Latn``) proved the stale contract wrong: there is no
``ocr_fallback`` field — the extraction method is the ``extractor`` string
(``docling`` vs ``rolmOCR``) — and ``language == "eng_Latn"`` is a routing
label, not a monolingual purity guarantee.

All records here are authored synthetic rows shaped like the observed live
schema. No network, no real acquisition bytes.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from xlm.data.adapters.mix01_adapters import (
    FinePdfsAdapter,
    MissingFieldError,
    RecordRejectedError,
)
from xlm.data.adapters.source_ids import canonical_source_doc_id

REPO_ROOT = Path(__file__).resolve().parents[1]

FINEPDFS_REVISION = "9cfabe2127faca99b3d5c4dc6d1fcb397399ebde"
FINEPDFS_FILE = "data/eng_Latn/train/000_00083.parquet"
FINEPDFS_FILE_2 = "data/eng_Latn/train/000_00084.parquet"


def live_row(**overrides: Any) -> dict[str, Any]:
    """One authored row shaped like the observed live FinePDFs-Edu schema."""
    record: dict[str, Any] = {
        "text": "Docling-extracted section on river deltas and sediment transport.",
        "language": "eng_Latn",
        "extractor": "docling",
        "is_truncated": False,
        "token_count": 42,
        "full_doc_lid": "eng_Latn",
        "full_doc_lid_score": 0.97,
        "page_average_lid": "eng_Latn",
        "page_average_lid_score": 0.91,
    }
    record.update(overrides)
    return record


def adapt(record: dict[str, Any], **kwargs: Any) -> Any:
    params = {
        "source_file": FINEPDFS_FILE,
        "source_row": 0,
        "source_revision": FINEPDFS_REVISION,
    }
    params.update(kwargs)
    return FinePdfsAdapter().adapt(record, **params)  # type: ignore[arg-type]


# ------------------------------------------------------- extraction method


def test_valid_docling_row_accepted_with_live_contract() -> None:
    doc = adapt(live_row())
    assert doc.text == live_row()["text"]
    assert doc.language == "en"
    assert doc.source_id == "finepdfs_edu"
    assert doc.license_reference == "odc-by"
    assert doc.doc_id == canonical_source_doc_id("finepdfs", FINEPDFS_FILE, 0)
    assert doc.source_revision == FINEPDFS_REVISION
    assert doc.source_file == FINEPDFS_FILE
    assert doc.source_row == 0
    assert FinePdfsAdapter.REQUIRED_FIELDS == ("text", "language", "extractor")
    assert FinePdfsAdapter().contract().required_fields == ["text", "language", "extractor"]


def test_rolmocr_rows_rejected_as_image_ocr() -> None:
    """Real rows 0/1 shape (rolmOCR, truncated) is an explicit policy rejection."""
    with pytest.raises(RecordRejectedError, match="RolmOCR image-based OCR"):
        adapt(live_row(extractor="rolmOCR", is_truncated=True))


def test_missing_extractor_fails_closed() -> None:
    for bad in ({}, {"extractor": None}, {"extractor": "   "}):
        record = live_row()
        record.pop("extractor", None)
        record.update(bad)
        with pytest.raises(MissingFieldError, match="'extractor'"):
            adapt(record)


def test_unknown_extractor_fails_closed() -> None:
    with pytest.raises(RecordRejectedError, match="knows extractors"):
        adapt(live_row(extractor="tesseract"))


def test_non_string_extractor_refused_not_coerced() -> None:
    with pytest.raises(MissingFieldError, match="'extractor'"):
        adapt(live_row(extractor=7))


def test_no_fake_ocr_fallback_metadata() -> None:
    """The ghost field is neither read nor written, even when present."""
    doc = adapt(live_row(ocr_fallback=True))
    assert "ocr_fallback" not in doc.source_metadata
    assert doc.source_metadata["extractor"] == "docling"


def test_stale_ocr_fallback_cannot_satisfy_contract() -> None:
    record = live_row()
    record.pop("extractor")
    record["ocr_fallback"] = True
    with pytest.raises(MissingFieldError, match="'extractor'"):
        adapt(record)


# ------------------------------------------------------------------ language


def test_wrong_language_rejected() -> None:
    with pytest.raises(RecordRejectedError, match="rows only"):
        adapt(live_row(language="deu_Latn"))


def test_missing_language_fails_closed() -> None:
    record = live_row()
    record.pop("language")
    with pytest.raises(MissingFieldError, match="'language'"):
        adapt(record)


def test_mixed_language_row_accepted_with_routing_provenance() -> None:
    """eng_Latn is a routing label: mixed-language Docling text still adapts."""
    text = "虽然本段以中文开头，但随后包含 English discussion of sediment transport."
    doc = adapt(live_row(text=text))
    assert doc.language == "en"
    assert doc.text == text
    provenance = doc.source_metadata["language_provenance"]
    assert "eng_Latn" in provenance
    assert "not a monolingual purity guarantee" in provenance
    assert "downstream language cleaning decides" in provenance


# ---------------------------------------------------------------------- text


def test_text_preserved_verbatim() -> None:
    text = (
        "# Delta formation\n\n| Stage | Process |\n|---|---|\n| 1 | Deposition |\n\nTrailing note."
    )
    doc = adapt(live_row(text=text))
    assert doc.text == text


def test_missing_or_blank_text_fails_closed() -> None:
    record = live_row()
    record.pop("text")
    with pytest.raises(MissingFieldError, match="'text'"):
        adapt(record)
    with pytest.raises(MissingFieldError, match="'text'"):
        adapt(live_row(text="   "))


def test_non_string_text_refused_not_coerced() -> None:
    with pytest.raises(MissingFieldError, match="'text'"):
        adapt(live_row(text=12345))


def test_extra_live_fields_tolerated_but_not_stuffed() -> None:
    """The 20-field live rows adapt; page-level arrays stay out of metadata."""
    doc = adapt(
        live_row(
            url="https://example.invalid/paper.pdf",
            file_path="papers/paper.pdf",
            per_page_languages=["eng_Latn", "unknown"],
            page_ends=[120, 340],
            fw_edu_scores=[0.5, 0.7],
            minhash_cluster_size=3,
        )
    )
    assert doc.text == live_row()["text"]
    for key in (
        "url",
        "file_path",
        "per_page_languages",
        "page_ends",
        "fw_edu_scores",
        "minhash_cluster_size",
    ):
        assert key not in doc.source_metadata


# ------------------------------------------------------------------- metadata


def test_truthful_extractor_and_bounded_annotations() -> None:
    doc = adapt(live_row())
    metadata = doc.source_metadata
    assert metadata["mix01_component"] == "finepdfs_en"
    assert metadata["config_name"] == "eng_Latn"
    assert metadata["extractor"] == "docling"
    assert metadata["is_truncated"] is False
    assert metadata["upstream_token_count"] == 42
    assert metadata["upstream_full_doc_lid"] == "eng_Latn"
    assert metadata["upstream_full_doc_lid_score"] == 0.97
    assert metadata["upstream_page_average_lid"] == "eng_Latn"
    assert metadata["upstream_page_average_lid_score"] == 0.91


def test_optional_annotations_absent_when_undeclared() -> None:
    record = live_row()
    for key in (
        "is_truncated",
        "token_count",
        "full_doc_lid",
        "full_doc_lid_score",
        "page_average_lid",
        "page_average_lid_score",
    ):
        record.pop(key, None)
    doc = adapt(record)
    for key in (
        "is_truncated",
        "upstream_token_count",
        "upstream_full_doc_lid",
        "upstream_full_doc_lid_score",
        "upstream_page_average_lid",
        "upstream_page_average_lid_score",
    ):
        assert key not in doc.source_metadata


def test_token_count_is_metadata_only_and_validated() -> None:
    doc = adapt(live_row(token_count=0))
    assert doc.source_metadata["upstream_token_count"] == 0
    for bad in (-1, True, "42", 4.5):
        with pytest.raises(MissingFieldError, match="'token_count'"):
            adapt(live_row(token_count=bad))


def test_malformed_optionals_refused() -> None:
    with pytest.raises(MissingFieldError, match="'is_truncated'"):
        adapt(live_row(is_truncated="yes"))
    with pytest.raises(MissingFieldError, match="'full_doc_lid_score'"):
        adapt(live_row(full_doc_lid_score="high"))
    with pytest.raises(MissingFieldError, match="'full_doc_lid'"):
        adapt(live_row(full_doc_lid="  "))


# ------------------------------------------------------- identity/determinism


def test_doc_id_unique_across_files_for_same_row() -> None:
    """Acquisition row_index is file-local: the file key disambiguates."""
    first = adapt(live_row(), source_file=FINEPDFS_FILE, source_row=0)
    second = adapt(live_row(), source_file=FINEPDFS_FILE_2, source_row=0)
    assert first.doc_id == canonical_source_doc_id("finepdfs", FINEPDFS_FILE, 0)
    assert second.doc_id == canonical_source_doc_id("finepdfs", FINEPDFS_FILE_2, 0)
    assert first.doc_id != second.doc_id


def test_adaptation_is_deterministic() -> None:
    assert adapt(live_row()).to_dict() == adapt(live_row()).to_dict()


# ------------------------------------------------------- data adapt CLI seam


def _finepdfs_plan(tmp_path: Path) -> Path:
    from xlm.data.acquisition.plan import (
        AcquisitionLimits,
        AcquisitionMode,
        AcquisitionPlan,
        PlanAuthorization,
        save_acquisition_plan,
    )

    plan = AcquisitionPlan(
        plan_id="plan_adapt_finepdfs_fixture",
        source_id="finepdfs_edu",
        view_id="eng_Latn",
        provider="huggingface",
        repository="HuggingFaceFW/finepdfs-edu",
        revision=FINEPDFS_REVISION,
        mode=AcquisitionMode.SELECTED_RECORDS,
        selected_files=[FINEPDFS_FILE],
        row_ranges={FINEPDFS_FILE: (0, 3)},
        output_artifact_id="raw_finepdfs_edu",
        is_pilot=True,
        limits=AcquisitionLimits(max_transferred_bytes=16 * 1024**2, max_records=100),
        authorization=PlanAuthorization(
            authorization_hash="",
            authorized_by="closeout-fixture",
            authorized_at="2026-09-21T00:00:00Z",
            scope="pilot",
            is_pilot_approved=True,
        ),
    )
    authorized = plan.model_copy(
        update={
            "authorization": plan.authorization.model_copy(
                update={"authorization_hash": plan.compute_behavioral_hash()}
            )
        }
    )
    plan_path = tmp_path / "plan.json"
    save_acquisition_plan(authorized, plan_path)
    return plan_path


def _selection_lines(plan_hash: str, rows: list[dict[str, Any]]) -> str:
    lines = []
    for row_index, row in enumerate(rows):
        lines.append(
            json.dumps(
                {
                    **row,
                    "_xlm_acquisition": {
                        "source_id": "finepdfs_edu",
                        "repository": "HuggingFaceFW/finepdfs-edu",
                        "revision": FINEPDFS_REVISION,
                        "source_file": FINEPDFS_FILE,
                        "row_index": row_index,
                        "selection_hash": plan_hash,
                    },
                },
                ensure_ascii=False,
            )
        )
    return "\n".join(lines) + "\n"


def _run_adapt(home: Path, *args: str, success: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [sys.executable, "-m", "xlm.cli.main", *args],
        cwd=REPO_ROOT,
        env={**os.environ, "XLM_HOME": str(home)},
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=120,
    )
    assert (result.returncode == 0) == success, result.stdout + result.stderr
    return result


def test_data_adapt_docling_row_to_canonical(tmp_path: Path) -> None:
    from xlm.data.acquisition.plan import load_acquisition_plan

    plan_path = _finepdfs_plan(tmp_path)
    plan_hash = load_acquisition_plan(plan_path).compute_behavioral_hash()
    selected = tmp_path / "selected_records.jsonl"
    selected.write_text(
        _selection_lines(plan_hash, [live_row(), live_row(text="Second Docling section.")]),
        encoding="utf-8",
    )
    out_dir = tmp_path / "canonical"
    _run_adapt(
        tmp_path / "home",
        "data",
        "adapt",
        "--plan",
        str(plan_path),
        "--adapter",
        "finepdfs_en",
        "--input",
        str(selected),
        "--output-dir",
        str(out_dir),
    )
    docs = [
        json.loads(line)
        for line in (out_dir / "documents.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(docs) == 2
    assert docs[0]["doc_id"] == canonical_source_doc_id("finepdfs", FINEPDFS_FILE, 0)
    assert docs[0]["text"] == live_row()["text"]
    assert docs[0]["language"] == "en"
    assert docs[0]["source_id"] == "finepdfs_edu"
    assert docs[0]["source_revision"] == FINEPDFS_REVISION
    assert docs[0]["source_file"] == FINEPDFS_FILE
    assert docs[0]["source_row"] == 0
    assert docs[0]["source_metadata"]["extractor"] == "docling"
    assert "ocr_fallback" not in docs[0]["source_metadata"]
    assert docs[1]["doc_id"] == canonical_source_doc_id("finepdfs", FINEPDFS_FILE, 1)
    out_dir2 = tmp_path / "canonical2"
    _run_adapt(
        tmp_path / "home",
        "data",
        "adapt",
        "--plan",
        str(plan_path),
        "--adapter",
        "finepdfs_en",
        "--input",
        str(selected),
        "--output-dir",
        str(out_dir2),
    )
    assert (out_dir2 / "documents.jsonl").read_bytes() == (out_dir / "documents.jsonl").read_bytes()


def test_data_adapt_rolmocr_row_refused_without_output(tmp_path: Path) -> None:
    """A source-policy rejection fails the run closed: nothing is written."""
    from xlm.data.acquisition.plan import load_acquisition_plan

    plan_path = _finepdfs_plan(tmp_path)
    plan_hash = load_acquisition_plan(plan_path).compute_behavioral_hash()
    selected = tmp_path / "selected_records.jsonl"
    selected.write_text(
        _selection_lines(plan_hash, [live_row(extractor="rolmOCR", is_truncated=True)]),
        encoding="utf-8",
    )
    out_dir = tmp_path / "canonical"
    result = _run_adapt(
        tmp_path / "home",
        "data",
        "adapt",
        "--plan",
        str(plan_path),
        "--adapter",
        "finepdfs_en",
        "--input",
        str(selected),
        "--output-dir",
        str(out_dir),
        success=False,
    )
    assert "refused line 1" in result.stderr
    assert "RolmOCR image-based OCR" in result.stderr
    assert not (out_dir / "documents.jsonl").exists()


def test_data_adapt_mixed_rows_fail_closed_with_no_partial_output(tmp_path: Path) -> None:
    """A valid Docling row followed by a RolmOCR row still refuses the run."""
    from xlm.data.acquisition.plan import load_acquisition_plan

    plan_path = _finepdfs_plan(tmp_path)
    plan_hash = load_acquisition_plan(plan_path).compute_behavioral_hash()
    selected = tmp_path / "selected_records.jsonl"
    selected.write_text(
        _selection_lines(
            plan_hash,
            [live_row(), live_row(extractor="rolmOCR", is_truncated=True)],
        ),
        encoding="utf-8",
    )
    out_dir = tmp_path / "canonical"
    result = _run_adapt(
        tmp_path / "home",
        "data",
        "adapt",
        "--plan",
        str(plan_path),
        "--adapter",
        "finepdfs_en",
        "--input",
        str(selected),
        "--output-dir",
        str(out_dir),
        success=False,
    )
    assert "refused line 2" in result.stderr
    assert not (out_dir / "documents.jsonl").exists()
