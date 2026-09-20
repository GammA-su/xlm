"""Bounded verification and D01 publication of originals or distinct selections."""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
from pathlib import Path
from typing import Any

from xlm.artifacts.manifest import ensure_plain_path
from xlm.artifacts.store import ArtifactStore, compute_file_sha256
from xlm.data.acquisition.plan import AcquisitionMode, AcquisitionPlan
from xlm.data.acquisition.progress import ProgressJournal
from xlm.data.acquisition.receipt import AcquiredFileInfo, AcquisitionReceipt
from xlm.data.acquisition.records import inspect_records


class VerificationError(RuntimeError):
    """Acquired content does not satisfy its declared integrity/selection contract."""


class ChecksumMismatchError(VerificationError):
    """An independent expected digest differs from local content."""


class AcquisitionVerifier:
    def __init__(self, plan: AcquisitionPlan, output_dir: Path,
                 journal: ProgressJournal | None = None) -> None:
        AcquisitionPlan.model_validate(plan.model_dump())
        ensure_plain_path(output_dir)
        self.plan, self.output_dir, self.journal = plan, output_dir, journal

    def verify(self) -> AcquisitionReceipt:
        plan = self.plan
        names = ["selected_records.jsonl"] if plan.mode == AcquisitionMode.SELECTED_RECORDS else plan.selected_files
        acquired: list[AcquiredFileInfo] = []
        total_bytes = total_records = 0
        if self.journal:
            self.journal.save()
            if self.journal.state.status != "COMPLETED":
                raise VerificationError("acquisition journal is not completed")
        for name in names:
            path = self.output_dir / name
            ensure_plain_path(path)
            if not path.is_file():
                raise VerificationError(f"selected file '{name}' does not exist")
            size = path.stat().st_size
            total_bytes += size
            if not size or total_bytes > plan.limits.max_output_disk_bytes:
                raise VerificationError("empty file or aggregate output byte limit exceeded")
            digest = compute_file_sha256(path, max_bytes=plan.limits.max_output_disk_bytes)
            expected = plan.expected_file_digests.get(name) if plan.mode == AcquisitionMode.WHOLE_FILE else None
            if expected and digest != expected.lower():
                raise ChecksumMismatchError(f"Checksum mismatch for '{name}'")
            if self.journal:
                progress = self.journal.state.file_progress.get(name)
                if not progress or progress.status != "completed" or digest != progress.content_sha256:
                    raise VerificationError("completed original integrity mismatch")
                count = progress.record_count
            else:
                try:
                    count = inspect_records(path, name, plan.limits)
                except Exception as exc:
                    label = "Parquet check failed" if name.endswith(".parquet") else "record verification failed"
                    raise VerificationError(f"{label}: {exc}") from exc
            if plan.mode == AcquisitionMode.SELECTED_RECORDS:
                count = self._verify_selection(path)
            total_records += count or 0
            if total_records > plan.limits.max_records:
                raise VerificationError("aggregate record limit exceeded")
            acquired.append(AcquiredFileInfo(relative_path=name, size_bytes=size,
                locally_computed_sha256=digest, independent_expected_sha256=expected,
                independent_checksum_verified=expected is not None, record_count=count))
        metrics: dict[str, Any] = {}
        if self.journal:
            metrics = self.journal.state.model_dump(include={"transferred_bytes", "requests_made", "decompressed_bytes", "temp_disk_bytes", "output_disk_bytes", "records_acquired", "accounting"})
        return AcquisitionReceipt(receipt_id=f"receipt_{plan.plan_id}", plan_id=plan.plan_id,
            plan_hash=plan.compute_behavioral_hash(), source_id=plan.source_id, view_id=plan.view_id,
            provider=plan.provider, repository=plan.repository, revision=plan.revision,
            mode=plan.mode.value, eligibility="pilot_only" if plan.is_pilot else "admission_required",
            files=acquired, resource_metrics=metrics,
            created_at=plan.authorization.authorized_at if plan.authorization else "not-recorded",
            is_verified=True, verification_notes=[
                "Transfer counts application response-body bytes; wire/TLS overhead is not measured.",
                "Opaque files have unknown record counts; metadata transfer is not corpus admission.",
                "Selected-row hashes do not verify an untransferred full-file expected digest."
                if plan.mode == AcquisitionMode.SELECTED_RECORDS else "Whole original identity retained.",
            ])

    def _verify_selection(self, path: Path) -> int:
        ranges = self.plan.row_ranges or {}
        expected = [(name, index) for name in self.plan.selected_files for index in range(*ranges[name])]
        if len(expected) > self.plan.limits.max_records:
            raise VerificationError("selected record limit exceeded")
        count = 0
        with path.open("rb") as stream:
            while raw := stream.readline(self.plan.limits.max_record_bytes + 8193):
                if len(raw) > self.plan.limits.max_record_bytes + 8192 or count >= len(expected):
                    raise VerificationError("selected serialization exceeds record bound")
                record = json.loads(raw)
                locator = record.get("_xlm_acquisition", {})
                name, index = expected[count]
                if (locator.get("source_file"), locator.get("row_index"), locator.get("revision"), locator.get("selection_hash")) != (name, index, self.plan.revision, self.plan.compute_behavioral_hash()):
                    raise VerificationError("original row locator or selection identity mismatch")
                count += 1
        if count != len(expected):
            raise VerificationError("incomplete selected artifact")
        return count

    def publish_artifact(self, store: ArtifactStore, receipt: AcquisitionReceipt) -> Path:
        # Recheck requested bytes immediately before D01 stages/hashes them.
        verified = self.verify()
        if receipt != verified:
            raise VerificationError("receipt differs from current verified request")
        files: dict[str, Path | str] = {item.relative_path: self.output_dir / item.relative_path for item in receipt.files}
        files["acquisition_receipt.json"] = json.dumps(receipt.model_dump(), sort_keys=True, indent=2)
        code = hashlib.sha256()
        for path in sorted(Path(__file__).parent.glob("*.py")):
            code.update(path.name.encode())
            code.update(path.read_bytes())
        dependencies = {name: importlib.metadata.version(name) for name in ("pydantic", "pyarrow", "filelock")}
        metadata = {"source_id": self.plan.source_id, "view_id": self.plan.view_id,
            "revision": self.plan.revision, "mode": self.plan.mode.value,
            "eligibility": receipt.eligibility, "is_pilot": self.plan.is_pilot,
            "plan_hash": self.plan.compute_behavioral_hash(), "dependencies": dependencies}
        return store.publish_artifact(artifact_id=self.plan.output_artifact_id,
            kind="raw_dataset", files=files, producer_code_hash=code.hexdigest(),
            dependency_hash=hashlib.sha256(json.dumps(dependencies, sort_keys=True).encode()).hexdigest(),
            resolved_config_hash=self.plan.compute_behavioral_hash(), metadata=metadata)
