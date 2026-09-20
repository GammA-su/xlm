"""JSONL source adapter adhering to C02, C03, and C04."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from xlm.core.contracts import CanonicalDocument
from xlm.data.normalization import canonical_normalize, compute_sha256, decode_utf8_strict

DEFAULT_MAX_RECORD_BYTES = 50 * 1024 * 1024  # 50 MB bound per record


def _pairs_hook_reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """JSON object_pairs_hook that raises ValueError on duplicate keys."""
    res: dict[str, Any] = {}
    for key, val in pairs:
        if key in res:
            raise ValueError(f"Duplicate JSON key encountered: '{key}'")
        res[key] = val
    return res


class JsonlAdapter:
    """Adapter for importing JSONL documents with strict validation."""

    def __init__(
        self,
        source_id: str,
        source_revision: str = "local_snapshot",
        license_reference: str = "unknown",
        text_field: str = "text",
        id_field: str = "id",
        split_field: str = "split",
        max_record_bytes: int = DEFAULT_MAX_RECORD_BYTES,
    ) -> None:
        self.source_id = source_id
        self.source_revision = source_revision
        self.license_reference = license_reference
        self.text_field = text_field
        self.id_field = id_field
        self.split_field = split_field
        self.max_record_bytes = max_record_bytes
        self._seen_doc_ids: set[str] = set()

    def process_line(
        self,
        raw_line_bytes: bytes,
        source_file: str,
        source_row: int,
        default_split: str | None = None,
    ) -> CanonicalDocument:
        """Process raw bytes of a single JSONL line into a CanonicalDocument."""
        if len(raw_line_bytes) > self.max_record_bytes:
            raise ValueError(
                f"Line {source_row} in {source_file} exceeds max record limit of "
                f"{self.max_record_bytes} bytes: got {len(raw_line_bytes)} bytes"
            )

        # Hash raw bytes before decoding or newline conversion
        raw_hash = compute_sha256(raw_line_bytes)

        # Decode UTF-8 strictly at adapter boundary
        try:
            raw_text_line = decode_utf8_strict(raw_line_bytes)
        except UnicodeDecodeError as e:
            raise ValueError(
                f"Malformed UTF-8 sequence at line {source_row} in {source_file}: {e}"
            ) from e

        # Parse JSON rejecting duplicate keys
        clean_json_str = raw_text_line.strip("\r\n")
        if not clean_json_str:
            raise ValueError(f"Empty line encountered at line {source_row} in {source_file}")

        try:
            record = json.loads(clean_json_str, object_pairs_hook=_pairs_hook_reject_duplicates)
        except json.JSONDecodeError as e:
            raise ValueError(f"Malformed JSON at line {source_row} in {source_file}: {e}") from e

        if not isinstance(record, dict):
            raise ValueError(
                f"JSON record must be an object at line {source_row} in {source_file}, "
                f"got {type(record).__name__}"
            )

        # Validate text field
        if self.text_field not in record:
            raise ValueError(
                f"Missing required text field '{self.text_field}' at line {source_row} "
                f"in {source_file}"
            )
        raw_text_val = record[self.text_field]
        if not isinstance(raw_text_val, str):
            raise ValueError(
                f"Text field '{self.text_field}' must be a string at line {source_row} "
                f"in {source_file}, got {type(raw_text_val).__name__}. Never stringifying."
            )

        # Validate doc ID
        doc_id_val = record.get(self.id_field)
        if doc_id_val is None:
            # If id not present in json, create deterministic row locator
            doc_id = f"{self.source_id}_{Path(source_file).stem}_{source_row}"
        elif isinstance(doc_id_val, str | int):
            doc_id = str(doc_id_val)
        else:
            raise ValueError(
                f"ID field '{self.id_field}' must be str or int at line {source_row} "
                f"in {source_file}, got {type(doc_id_val).__name__}"
            )

        if doc_id in self._seen_doc_ids:
            raise ValueError(
                f"Duplicate document ID '{doc_id}' encountered at line {source_row} "
                f"in {source_file}"
            )
        self._seen_doc_ids.add(doc_id)

        # Validate split
        split_val = record.get(self.split_field, default_split)
        if not split_val:
            raise ValueError(
                f"Missing split assignment for doc '{doc_id}' at line {source_row} in {source_file}"
            )
        if split_val not in ("train", "diagnostic_val", "audit"):
            raise ValueError(
                f"Invalid split '{split_val}' for doc '{doc_id}' at line {source_row} "
                f"in {source_file}. Must be 'train', 'diagnostic_val', or 'audit'"
            )

        # Canonical normalization
        clean_text = canonical_normalize(raw_text_val)
        clean_hash = compute_sha256(clean_text)
        utf8_byte_count = len(clean_text.encode("utf-8"))

        language = record.get("language", "en")
        if not isinstance(language, str):
            language = "en"

        language_confidence = float(record.get("language_confidence", 1.0))
        document_kind = record.get("document_kind", "prose")
        if not isinstance(document_kind, str):
            document_kind = "prose"

        source_metadata = {
            k: v
            for k, v in record.items()
            if k not in (self.text_field, self.id_field, self.split_field)
        }

        transform_log: list[dict[str, Any]] = [
            {"transform": "nfc_and_newline_normalize", "version": "1.0"}
        ]

        return CanonicalDocument(
            doc_id=doc_id,
            source_id=self.source_id,
            source_revision=self.source_revision,
            source_file=source_file,
            source_row=source_row,
            raw_hash=raw_hash,
            clean_hash=clean_hash,
            text=clean_text,
            utf8_byte_count=utf8_byte_count,
            language=language,
            language_confidence=language_confidence,
            document_kind=document_kind,
            source_metadata=source_metadata,
            parent_ids=record.get("parent_ids", []),
            license_reference=record.get("license_reference", self.license_reference),
            transform_log=transform_log,
            quality_reasons=[],
            cluster_ids={},
            split=split_val,
        )

    def process_file(
        self,
        path: Path,
        default_split: str | None = None,
    ) -> Iterator[CanonicalDocument]:
        """Read and process a JSONL file in bounded streaming fashion."""
        if not path.is_file():
            raise FileNotFoundError(f"Source JSONL file not found: {path}")

        source_file = path.name
        with path.open("rb") as f:
            row_num = 0
            for line in f:
                row_num += 1
                if not line.strip():
                    continue  # skip completely blank line between rows
                yield self.process_line(
                    raw_line_bytes=line,
                    source_file=source_file,
                    source_row=row_num,
                    default_split=default_split,
                )
