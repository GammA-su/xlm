"""Integration tests for cleaning pipeline orchestration, validation, reporting, and quarantine
security.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from xlm.core.contracts import CanonicalDocument
from xlm.data.cleaning.base import IncompatiblePipelineError
from xlm.data.cleaning.html import HtmlExtractionTransform
from xlm.data.cleaning.pipeline import CleaningPipeline, create_pipeline_preset
from xlm.data.cleaning.quarantine import QuarantineManager
from xlm.data.cleaning.reporting import QualityReporter
from xlm.data.cleaning.structured import StructuredExampleRenderTransform
from xlm.data.normalization import compute_sha256


def create_sample_doc(
    doc_id: str,
    text: str,
    document_kind: str = "prose",
    source_metadata: dict[str, Any] | None = None,
) -> CanonicalDocument:
    b = text.encode("utf-8")
    return CanonicalDocument(
        doc_id=doc_id,
        source_id="test_pipeline",
        source_revision="rev_1",
        source_file="test.jsonl",
        source_row=1,
        raw_hash=compute_sha256(b),
        clean_hash=compute_sha256(b),
        text=text,
        utf8_byte_count=len(b),
        language="en",
        language_confidence=1.0,
        document_kind=document_kind,
        source_metadata=source_metadata or {},
        parent_ids=[],
        license_reference="mit",
        transform_log=[],
        quality_reasons=[],
        cluster_ids={},
        split="train",
    )


def test_incompatible_pipeline_order_raises() -> None:
    """Verify that placing a text-mutating transform after structured answer span tracking raises
    IncompatiblePipelineError.

    Adheres strictly to Amendment 1.
    """
    # structured_render mutates text and produces answer spans
    # html_extraction mutates text and would invalidate previously computed spans
    invalid_transforms = [
        StructuredExampleRenderTransform(),
        HtmlExtractionTransform(),
    ]
    with pytest.raises(IncompatiblePipelineError, match="mutates text after 'structured_render'"):
        CleaningPipeline(invalid_transforms)


def test_pipeline_hash_invalidation_on_config_or_code() -> None:
    """Verify pipeline_hash changes deterministically when transform configuration changes."""
    pipe1 = create_pipeline_preset("educational_prose")
    pipe2 = create_pipeline_preset("code")

    hash1 = pipe1.compute_pipeline_hash()
    hash2 = pipe2.compute_pipeline_hash()

    assert hash1 != hash2
    assert len(hash1) == 64
    assert len(hash2) == 64


def test_pipeline_execution_and_quarantine_flow(tmp_path: Path) -> None:
    """Verify end-to-end pipeline execution with quarantine and yield statistics."""
    canary_secret = "CANARY_SECRET_PIPELINE_PASS_123"
    pipeline = create_pipeline_preset("educational_prose")
    q_dir = tmp_path / "quarantine"
    q_mgr = QuarantineManager(q_dir)

    docs = [
        # 1. Clean valid prose -> ACCEPT
        create_sample_doc(
            "doc_valid_1",
            "This is a high quality article discussing transformer architecture. "
            "Self-attention connects all pairs of positions in an ongoing sequence.",
        ),
        # 2. Secret-bearing document -> REJECT & OMITTED IN QUARANTINE
        create_sample_doc(
            "doc_secret",
            f"Deployment credentials for internal system: {canary_secret} "
            "and hf_0123456789012345678901234567890123.",
        ),
        # 3. Excessive repetition -> REJECT
        create_sample_doc(
            "doc_repeat",
            "\n".join(["Click to subscribe to newsletter"] * 30),
        ),
        # 4. Clean valid prose -> ACCEPT
        create_sample_doc(
            "doc_valid_2",
            "Deep neural networks require careful initialization and normalization. "
            "Root mean square normalization stabilizes gradient propagation in deep layers.",
        ),
    ]

    accepted_iter, summary = pipeline.run_stream(docs, quarantine_mgr=q_mgr)
    accepted = list(accepted_iter)

    # 2 accepted, 2 rejected
    assert len(accepted) == 2
    assert summary.total_input_docs == 4
    assert summary.total_output_docs == 2
    assert summary.total_rejected_docs == 2
    assert summary.document_yield_ratio == 0.50

    # Verify quarantine file
    q_file = q_dir / "quarantine.jsonl"
    assert q_file.exists()
    q_content = q_file.read_text(encoding="utf-8")

    # Security check: canary secret must NOT appear in quarantine file!
    assert canary_secret not in q_content


def test_quality_reporter_html_escaping_and_zero_handling(tmp_path: Path) -> None:
    """Verify quality reporter escapes HTML in samples and safely handles empty inputs."""
    pipeline = create_pipeline_preset("educational_prose")

    # 1. Empty input handling (no ZeroDivisionError, no NaN)
    _, summary_empty = pipeline.run_stream([])
    assert summary_empty.total_input_docs == 0
    assert summary_empty.document_yield_ratio == 0.0
    assert summary_empty.byte_yield_ratio == 0.0

    reporter_empty = QualityReporter(summary_empty)
    md_empty = reporter_empty.generate_markdown()
    assert "0.00%" in md_empty
    assert "NaN" not in md_empty
    assert "Infinity" not in md_empty

    # 2. HTML escaping test
    xss_doc = create_sample_doc(
        "doc_xss",
        "<script>alert('XSS_PAYLOAD_TEST');</script> Normal text after script.",
    )
    _, summary_xss = pipeline.run_stream([xss_doc])
    reporter = QualityReporter(summary_xss)
    reporter.add_review_sample(xss_doc, None, "REJECT", "test", ["test_reason"])

    html_report = reporter.generate_html()
    # Script tag must be escaped, never raw
    assert "<script>" not in html_report
    assert "&lt;script&gt;" in html_report


def test_base_environment_zero_torch_isolation() -> None:
    """Verify importing the cleaning package pulls in zero PyTorch dependency.

    This must run in a fresh interpreter: inside the shared pytest process another
    test may already have imported torch, which would make an in-process check on
    ``sys.modules`` vacuous.
    """
    probe = "import sys; import xlm.data.cleaning; sys.exit(1 if 'torch' in sys.modules else 0)"
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, (
        "Importing xlm.data.cleaning loaded torch into a clean interpreter. "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
