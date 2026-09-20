"""Unit tests for AcquisitionVerifier, checksum checking, and artifact publication."""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from xlm.artifacts.store import ArtifactStore
from xlm.core.paths import ArtifactPaths
from xlm.data.acquisition.plan import AcquisitionMode, AcquisitionPlan
from xlm.data.acquisition.verifier import (
    AcquisitionVerifier,
    ChecksumMismatchError,
    VerificationError,
)


def test_verifier_independent_checksum_positive(tmp_path: Path) -> None:
    """Verify that matching independent expected checksum passes and is recorded in receipt."""
    file_content = b'{"text": "Sample document one"}\n{"text": "Sample document two"}\n'
    expected_sha = hashlib.sha256(file_content).hexdigest()

    output_dir = tmp_path / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "sample.jsonl").write_bytes(file_content)

    plan = AcquisitionPlan(
        plan_id="plan_chk_pass",
        source_id="finewiki",
        view_id="default",
        provider="https",
        repository="https://mock.repo",
        revision="rev123",
        mode=AcquisitionMode.WHOLE_FILE,
        selected_files=["sample.jsonl"],
        expected_file_digests={"sample.jsonl": expected_sha},
        output_artifact_id="raw_sample_pass",
    )

    verifier = AcquisitionVerifier(plan, output_dir=output_dir)
    receipt = verifier.verify()

    assert receipt.is_verified
    assert len(receipt.files) == 1
    assert receipt.files[0].independent_checksum_verified
    assert receipt.files[0].record_count == 2


def test_verifier_corrupted_jsonl_mismatch(tmp_path: Path) -> None:
    """Verify that syntactically valid but altered JSONL fails independent expected digest."""
    original_content = b'{"text": "Original text content"}\n'
    expected_sha = hashlib.sha256(original_content).hexdigest()

    altered_valid_json = b'{"text": "Altered but syntactically valid JSON text"}\n'
    output_dir = tmp_path / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "sample.jsonl").write_bytes(altered_valid_json)

    plan = AcquisitionPlan(
        plan_id="plan_chk_fail",
        source_id="finewiki",
        view_id="default",
        provider="https",
        repository="https://mock.repo",
        revision="rev123",
        mode=AcquisitionMode.WHOLE_FILE,
        selected_files=["sample.jsonl"],
        expected_file_digests={"sample.jsonl": expected_sha},
        output_artifact_id="raw_sample_fail",
    )

    verifier = AcquisitionVerifier(plan, output_dir=output_dir)
    with pytest.raises(ChecksumMismatchError, match="Checksum mismatch"):
        verifier.verify()


def test_verifier_parquet_format_verification(tmp_path: Path) -> None:
    """Verify valid Parquet row count recorded and corrupt Parquet fails."""
    output_dir = tmp_path / "output"
    output_dir.mkdir(parents=True, exist_ok=True)

    # 1. Valid Parquet
    valid_parquet = output_dir / "valid.parquet"
    table = pa.Table.from_pydict({"text": ["doc1", "doc2", "doc3"], "id": [1, 2, 3]})
    pq.write_table(table, valid_parquet)

    plan_valid = AcquisitionPlan(
        plan_id="plan_pq_valid",
        source_id="parquet_src",
        view_id="default",
        provider="https",
        repository="https://mock.repo",
        revision="rev123",
        mode=AcquisitionMode.WHOLE_FILE,
        selected_files=["valid.parquet"],
        output_artifact_id="raw_pq_valid",
    )
    verifier = AcquisitionVerifier(plan_valid, output_dir=output_dir)
    receipt = verifier.verify()
    assert receipt.files[0].record_count == 3

    # 2. Corrupt Parquet (bad footer)
    corrupt_parquet = output_dir / "corrupt.parquet"
    corrupt_parquet.write_bytes(b"PAR1_NOT_A_VALID_PARQUET_FOOTER_CORRUPT")

    plan_corrupt = AcquisitionPlan(
        plan_id="plan_pq_corrupt",
        source_id="parquet_src",
        view_id="default",
        provider="https",
        repository="https://mock.repo",
        revision="rev123",
        mode=AcquisitionMode.WHOLE_FILE,
        selected_files=["corrupt.parquet"],
        output_artifact_id="raw_pq_corrupt",
    )
    verifier_corrupt = AcquisitionVerifier(plan_corrupt, output_dir=output_dir)
    with pytest.raises(VerificationError, match="Parquet check failed"):
        verifier_corrupt.verify()


def test_verifier_missing_file_fails(tmp_path: Path) -> None:
    """Verify that a plan with a missing file raises VerificationError."""
    output_dir = tmp_path / "output"
    output_dir.mkdir(parents=True, exist_ok=True)

    plan = AcquisitionPlan(
        plan_id="plan_missing_file",
        source_id="src",
        view_id="default",
        provider="https",
        repository="https://mock.repo",
        revision="rev123",
        mode=AcquisitionMode.WHOLE_FILE,
        selected_files=["non_existent_file.parquet"],
        output_artifact_id="raw_missing",
    )
    verifier = AcquisitionVerifier(plan, output_dir=output_dir)
    with pytest.raises(VerificationError, match="does not exist"):
        verifier.verify()


def test_verifier_artifact_store_publication_and_post_corruption(tmp_path: Path) -> None:
    """Verify publication into P01 ArtifactStore and detection of post-publication tampering."""
    output_dir = tmp_path / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "data.jsonl").write_bytes(b'{"text": "Hello world"}\n')

    plan = AcquisitionPlan(
        plan_id="plan_pub_test",
        source_id="src",
        view_id="default",
        provider="https",
        repository="https://mock.repo",
        revision="rev123",
        mode=AcquisitionMode.WHOLE_FILE,
        selected_files=["data.jsonl"],
        output_artifact_id="raw_pub_artifact_001",
    )

    paths = ArtifactPaths(root=tmp_path / "artifacts")
    store = ArtifactStore(paths)

    verifier = AcquisitionVerifier(plan, output_dir=output_dir)
    receipt = verifier.verify()
    art_dir = verifier.publish_artifact(store, receipt)

    # Verify store validated the artifact
    store.verify_artifact(art_dir)
    assert (art_dir / "_COMPLETED").exists()
    assert (art_dir / "acquisition_receipt.json").exists()

    # Tamper with the published file
    published_file = art_dir / "data.jsonl"
    published_file.write_bytes(b'{"text": "TAMPERED_CONTENT"}\n')

    # Re-verifying must fail with ValueError from ArtifactStore
    with pytest.raises(ValueError):
        store.verify_artifact(art_dir)


def test_base_environment_zero_torch_isolation() -> None:
    """Verify that importing xlm.data.acquisition operates with 100% zero-torch dependency."""
    cmd = [
        sys.executable,
        "-c",
        (
            "import sys; "
            "import xlm.data.acquisition; "
            "assert 'torch' not in sys.modules, 'torch leaked into sys.modules!'"
        ),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    assert res.returncode == 0, f"Import isolation check failed: {res.stderr}"
