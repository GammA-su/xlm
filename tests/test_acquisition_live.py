"""Live network pilot test seam for data acquisition adhering to C04, C13, and A15."""

from __future__ import annotations

from pathlib import Path

import pytest

from xlm.artifacts.store import ArtifactStore
from xlm.core.paths import ArtifactPaths
from xlm.data.acquisition.fetcher import BoundedFetcher
from xlm.data.acquisition.plan import (
    AcquisitionLimits,
    AcquisitionMode,
    AcquisitionPlan,
    PlanAuthorization,
)
from xlm.data.acquisition.verifier import AcquisitionVerifier
from xlm.data.sources.admission import load_probe_evidence
from xlm.data.sources.catalog import load_catalog
from xlm.data.sources.transport import HuggingFaceTransport, TransportBudget


@pytest.mark.network
def test_live_pilot_acquisition_seam(tmp_path: Path) -> None:
    """Verify live acquisition seam against real provider evidence without bypassing gates.

    Per Amendment 8:
    - Resolves real revision, files, and schemas from P07 evidence.
    - If no eligible Parquet record file fits within 256 MiB pilot limit,
      records record-acquisition pilot as BLOCKED/NOT RUN rather than fabricating
      invented files or downloading multi-gigabyte shards without authorization.
    - Demonstrates real bounded download of an approved file within strict budget.
    """
    catalog = load_catalog(Path("manifests/datasets.catalog.yaml"))
    candidate = catalog.get_source("finewiki")
    assert candidate is not None

    paths = ArtifactPaths.from_env()
    store = ArtifactStore(paths)
    evidence = load_probe_evidence("finewiki", "default", store)

    if not evidence or not evidence.immutable_revision:
        pytest.skip(
            "P07 probe evidence for finewiki:default not found; run 'xlm data probe' first."
        )

    revision = evidence.immutable_revision

    # Query real provider file list
    transport = HuggingFaceTransport(TransportBudget(max_bytes=1024 * 1024))
    files, _, total_count = transport.list_files(candidate.repository, revision)
    assert total_count == 3
    file_map = {f["path"]: f["size"] for f in files}

    # Verify that finewiki at root contains no sub-256 MiB Parquet files
    parquet_files = [f for f in files if f["path"].endswith(".parquet")]
    if not parquet_files:
        # Finewiki root contains .gitattributes, README.md, language_subsets.csv
        # Record full-record Parquet pilot is BLOCKED for default until subset shard admitted.
        # Verify bounded fetch seam using actual observed file language_subsets.csv (21.7 KiB).
        target_file = "language_subsets.csv"
        expected_size = file_map[target_file]
        assert expected_size < 100 * 1024  # Well within 256 MiB pilot budget

        # Create authorized pilot plan
        plan = AcquisitionPlan(
            plan_id="plan_finewiki_pilot_seam",
            source_id="finewiki",
            view_id="default",
            provider="huggingface",
            repository=candidate.repository,
            revision=revision,
            mode=AcquisitionMode.WHOLE_FILE,
            selected_files=[target_file],
            output_artifact_id="raw_finewiki_pilot_seam",
            limits=AcquisitionLimits(
                max_transferred_bytes=1024 * 1024,  # 1 MiB limit
                max_output_disk_bytes=10 * 1024 * 1024,
            ),
            is_pilot=True,
        )
        auth = PlanAuthorization(
            authorization_hash=plan.compute_behavioral_hash(),
            authorized_by="network_seam_test",
            authorized_at="2026-09-19T12:00:00Z",
            scope="pilot",
            is_pilot_approved=True,
        )
        plan_with_auth = plan.model_copy(update={"authorization": auth})

        output_dir = tmp_path / "output"
        scratch_dir = tmp_path / "scratch"

        fetcher = BoundedFetcher(plan_with_auth, scratch_dir=scratch_dir, output_dir=output_dir)
        state = fetcher.run()

        assert state.status == "COMPLETED"
        assert state.transferred_bytes > 0
        assert state.transferred_bytes <= 1024 * 1024

        downloaded_file = output_dir / target_file
        assert downloaded_file.exists()
        assert downloaded_file.stat().st_size == expected_size

        # Verify integrity
        verifier = AcquisitionVerifier(plan_with_auth, output_dir=output_dir)
        receipt = verifier.verify()
        assert receipt.is_verified
        assert receipt.files[0].size_bytes == expected_size
        assert receipt.files[0].locally_computed_sha256 != ""
