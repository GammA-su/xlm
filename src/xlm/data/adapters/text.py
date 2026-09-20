"""Plain text source adapter adhering to C02, C03, and C04."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

from xlm.core.contracts import CanonicalDocument
from xlm.data.normalization import canonical_normalize, compute_sha256, decode_utf8_strict

DEFAULT_MAX_DOC_BYTES = 50 * 1024 * 1024  # 50 MB bound per document


class TextAdapter:
    """Adapter for importing UTF-8 plain text documents."""

    def __init__(
        self,
        source_id: str,
        source_revision: str = "local_snapshot",
        license_reference: str = "unknown",
        max_doc_bytes: int = DEFAULT_MAX_DOC_BYTES,
    ) -> None:
        self.source_id = source_id
        self.source_revision = source_revision
        self.license_reference = license_reference
        self.max_doc_bytes = max_doc_bytes
        self._seen_doc_ids: set[str] = set()

    def process_file(
        self,
        path: Path,
        doc_id: str | None = None,
        split: str = "train",
        document_kind: str = "prose",
        language: str = "en",
        metadata: dict[str, Any] | None = None,
    ) -> CanonicalDocument:
        """Process a single plain text file into a CanonicalDocument."""
        if not path.is_file():
            raise FileNotFoundError(f"Source file not found: {path}")

        file_size = path.stat().st_size
        if file_size > self.max_doc_bytes:
            raise ValueError(
                f"File {path.name} exceeds max record limit of {self.max_doc_bytes} bytes: "
                f"got {file_size} bytes"
            )

        raw_bytes = path.read_bytes()
        raw_hash = compute_sha256(raw_bytes)

        try:
            raw_text = decode_utf8_strict(raw_bytes)
        except UnicodeDecodeError as e:
            raise ValueError(f"Malformed UTF-8 sequence in {path}: {e}") from e

        clean_text = canonical_normalize(raw_text)
        clean_hash = compute_sha256(clean_text)
        clean_bytes = clean_text.encode("utf-8")
        utf8_byte_count = len(clean_bytes)

        actual_doc_id = doc_id if doc_id is not None else path.stem
        if actual_doc_id in self._seen_doc_ids:
            raise ValueError(f"Duplicate document ID encountered: '{actual_doc_id}'")
        self._seen_doc_ids.add(actual_doc_id)

        transform_log: list[dict[str, Any]] = [
            {"transform": "nfc_and_newline_normalize", "version": "1.0"}
        ]

        return CanonicalDocument(
            doc_id=actual_doc_id,
            source_id=self.source_id,
            source_revision=self.source_revision,
            source_file=path.name,
            source_row=0,
            raw_hash=raw_hash,
            clean_hash=clean_hash,
            text=clean_text,
            utf8_byte_count=utf8_byte_count,
            language=language,
            language_confidence=1.0,
            document_kind=document_kind,
            source_metadata=metadata or {},
            parent_ids=[],
            license_reference=self.license_reference,
            transform_log=transform_log,
            quality_reasons=[],
            cluster_ids={},
            split=split,
        )

    def process_directory(
        self,
        directory: Path,
        split_map: dict[str, str] | None = None,
        default_split: str = "train",
        extension: str = ".txt",
    ) -> Iterator[CanonicalDocument]:
        """Process all matching files in directory in deterministic lexicographic order."""
        if not directory.is_dir():
            raise NotADirectoryError(f"Directory not found: {directory}")

        files = sorted([p for p in directory.iterdir() if p.is_file() and p.suffix == extension])
        for p in files:
            doc_id = p.stem
            split = split_map.get(doc_id, default_split) if split_map else default_split
            yield self.process_file(p, doc_id=doc_id, split=split)
