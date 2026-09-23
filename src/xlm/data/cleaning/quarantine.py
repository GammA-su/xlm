"""Bounded, secure quarantine storage adhering to C03, C05, and Amendment 7."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from pydantic import Field

from xlm.config.schemas import StrictConfigModel
from xlm.core.contracts import CanonicalDocument
from xlm.data.cleaning.base import CleaningBudgetExhaustedError
from xlm.data.cleaning.pii import redact_sensitive_text
from xlm.data.cleaning.types import QualityMetrics


class QuarantineRecorder(Protocol):
    """Structural sink for rejected documents (reference manager or buffered writer)."""

    def record_rejection(
        self,
        doc: CanonicalDocument,
        reasons: list[str],
        stage_name: str,
        metrics: QualityMetrics | None = None,
    ) -> None: ...


class QuarantinePolicy(StrictConfigModel):
    """Configurable retention and access controls for quarantined rejections (C03).

    Retention and access are separate concerns: ``retention_days`` bounds how long a
    rejected sample may be kept, while ``store_previews`` bounds who can learn
    anything about its content at all. Secret-bearing rejections are always stored
    metadata-only regardless of these settings.
    """

    max_records: int = Field(default=10_000, ge=1)
    max_quarantine_bytes: int = Field(default=100 * 1024 * 1024, ge=1)
    max_preview_chars: int = Field(default=256, ge=0)
    retention_days: int | None = Field(
        default=30,
        ge=1,
        description="Days a quarantined record may be retained; None disables expiry.",
    )
    store_previews: bool = Field(
        default=True,
        description="When false, no text preview is stored for any rejection.",
    )
    owner_only_permissions: bool = Field(
        default=True,
        description="Apply owner-only file permissions where the platform supports it.",
    )


def is_secret_rejection(reasons: list[str]) -> bool:
    """True when rejection reasons carry secrets (quarantine stores metadata only)."""
    return any(
        r.startswith("detected_secret:") or r.startswith("canary_credential") for r in reasons
    )


def build_quarantine_entry(
    doc: CanonicalDocument,
    reasons: list[str],
    stage_name: str,
    metrics: QualityMetrics | None,
    policy: QuarantinePolicy,
    recorded_at: str,
) -> tuple[bytes, int]:
    """Build one quarantine JSONL line and its UTF-8 byte length (pure function).

    The emitted bytes are exactly what :meth:`QuarantineManager.record_rejection`
    writes: same keys in the same order, same secret-omission and preview rules,
    same metrics rendering. Both the reference manager and the buffered
    throughput writer share this function so neither can drift from the other.
    """
    secret_omitted = is_secret_rejection(reasons)

    sanitized_preview: str | None = None
    if policy.store_previews and not secret_omitted and doc.text:
        # Non-secret rejections get a bounded, sanitized preview
        raw_preview = doc.text[: policy.max_preview_chars]
        sanitized_preview = redact_sensitive_text(raw_preview)

    # Build sanitized quarantine entry
    entry: dict[str, Any] = {
        "doc_id": doc.doc_id,
        "source_id": doc.source_id,
        "source_file": doc.source_file,
        "source_row": doc.source_row,
        "raw_hash": doc.raw_hash,
        "split": doc.split,
        "reasons": reasons,
        "stage": stage_name,
        "utf8_byte_count": doc.utf8_byte_count,
        "is_secret_omitted": secret_omitted,
        "sanitized_preview": sanitized_preview,
        "recorded_at": recorded_at,
    }
    if metrics:
        entry["metrics"] = metrics.to_dict()

    line = json.dumps(entry, ensure_ascii=False) + "\n"
    payload = line.encode("utf-8")
    return payload, len(payload)


class QuarantineManager:
    """Manages secure quarantine storage for rejected documents.

    Adheres strictly to Amendment 7:
    - Sensitive secret rejections strictly omit raw text payload (metadata-only quarantine).
    - Permitted non-secret preview samples are sanitized via redact_sensitive_text and length-
    bounded.
    - Never stores sealed benchmark labels or protected matches (Contracts C03 & C05).
    - Enforces hard storage ceilings (max_records and max_quarantine_bytes).
    - Enforces restrictive file permissions.
    """

    def __init__(
        self,
        quarantine_dir: Path,
        max_records: int | None = None,
        max_quarantine_bytes: int | None = None,
        max_preview_chars: int | None = None,
        policy: QuarantinePolicy | None = None,
    ) -> None:
        base = policy or QuarantinePolicy()
        overrides: dict[str, Any] = {}
        if max_records is not None:
            overrides["max_records"] = max_records
        if max_quarantine_bytes is not None:
            overrides["max_quarantine_bytes"] = max_quarantine_bytes
        if max_preview_chars is not None:
            overrides["max_preview_chars"] = max_preview_chars
        self.policy = base.model_copy(update=overrides) if overrides else base

        self.quarantine_dir = quarantine_dir
        self.max_records = self.policy.max_records
        self.max_quarantine_bytes = self.policy.max_quarantine_bytes
        self.max_preview_chars = self.policy.max_preview_chars

        self.quarantine_dir.mkdir(parents=True, exist_ok=True)
        self.quarantine_file = self.quarantine_dir / "quarantine.jsonl"

        self.recorded_count = 0
        self.total_bytes_written = 0

        # Apply restrictive permissions on quarantine directory
        self._apply_file_security(self.quarantine_file)

    def _apply_file_security(self, path: Path) -> None:
        """Apply owner-only permissions where supported and enabled by policy."""
        if self.policy.owner_only_permissions and os.name == "posix":
            try:
                os.chmod(path.parent, 0o700)
                if path.exists():
                    os.chmod(path, 0o600)
            except OSError:
                pass

    def record_rejection(
        self,
        doc: CanonicalDocument,
        reasons: list[str],
        stage_name: str,
        metrics: QualityMetrics | None = None,
    ) -> None:
        """Record a rejected document into quarantine adhering to secret-omission policy."""
        if self.recorded_count >= self.max_records:
            return  # Cap reached

        if self.total_bytes_written >= self.max_quarantine_bytes:
            raise CleaningBudgetExhaustedError(
                f"Quarantine storage exceeded ceiling of {self.max_quarantine_bytes:,} bytes."
            )

        payload, line_bytes = build_quarantine_entry(
            doc,
            reasons,
            stage_name,
            metrics,
            self.policy,
            datetime.now(UTC).isoformat(),
        )

        with self.quarantine_file.open("ab") as f:
            f.write(payload)

        self._apply_file_security(self.quarantine_file)
        self.recorded_count += 1
        self.total_bytes_written += line_bytes

    def purge_expired(self, now: datetime | None = None) -> int:
        """Drop quarantined records older than the configured retention window.

        Returns the number of records removed. Entries with an unparsable or absent
        timestamp are kept rather than silently discarded, so a malformed record
        cannot quietly destroy an audit trail.
        """
        if self.policy.retention_days is None or not self.quarantine_file.exists():
            return 0

        reference = now or datetime.now(UTC)
        cutoff = reference - timedelta(days=self.policy.retention_days)

        retained: list[str] = []
        removed = 0
        retained_bytes = 0

        with self.quarantine_file.open("r", encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    entry = json.loads(stripped)
                    recorded_at = datetime.fromisoformat(entry["recorded_at"])
                except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                    retained.append(stripped)
                    retained_bytes += len(stripped.encode("utf-8")) + 1
                    continue

                if recorded_at < cutoff:
                    removed += 1
                else:
                    retained.append(stripped)
                    retained_bytes += len(stripped.encode("utf-8")) + 1

        if removed:
            temp_path = self.quarantine_file.with_suffix(".jsonl.tmp")
            payload = "".join(line + "\n" for line in retained)
            temp_path.write_text(payload, encoding="utf-8")
            temp_path.replace(self.quarantine_file)
            self._apply_file_security(self.quarantine_file)
            self.recorded_count = len(retained)
            self.total_bytes_written = retained_bytes

        return removed
