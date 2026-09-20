"""Schema inspection, Arrow data type descriptors, nested field analysis, and secret redaction."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class FieldDescriptor(BaseModel):
    """Descriptor for a dataset schema field with recursive nested type support."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    name: str
    type_name: str
    nullable: bool = True
    nested_fields: dict[str, FieldDescriptor] = Field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type_name": self.type_name,
            "nullable": self.nullable,
            "nested_fields": {k: v.to_dict() for k, v in self.nested_fields.items()},
        }


class ViewSchema(BaseModel):
    """Schema descriptor for a specific dataset view or subset."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    view_id: str
    fields: dict[str, FieldDescriptor] = Field(default_factory=dict)
    raw_schema_type: str = "arrow"
    is_nested: bool = False
    row_count_estimate: int | None = None
    byte_size_estimate: int | None = None

    def to_canonical_dict(self) -> dict[str, Any]:
        return {
            "view_id": self.view_id,
            "raw_schema_type": self.raw_schema_type,
            "is_nested": self.is_nested,
            "fields": {
                k: v.to_dict() for k, sorted_v in sorted(self.fields.items()) for v in [sorted_v]
            },
        }


def arrow_schema_to_view_schema(
    schema: Any,  # pyarrow.Schema
    view_id: str,
    row_count_estimate: int | None = None,
    byte_size_estimate: int | None = None,
) -> ViewSchema:
    """Convert a PyArrow Schema to a ViewSchema with full nested struct/list inspection."""
    import pyarrow as pa

    fields: dict[str, FieldDescriptor] = {}
    is_nested = False

    def _convert_field(f: pa.Field) -> FieldDescriptor:
        nonlocal is_nested
        t = f.type
        t_str = str(t)

        nested: dict[str, FieldDescriptor] = {}
        if pa.types.is_struct(t):
            is_nested = True
            for i in range(t.num_fields):
                child = t.field(i)
                nested[child.name] = _convert_field(child)
        elif pa.types.is_list(t) or pa.types.is_large_list(t):
            is_nested = True
            val_field = t.value_field
            nested["item"] = _convert_field(val_field)

        return FieldDescriptor(
            name=f.name,
            type_name=t_str,
            nullable=f.nullable,
            nested_fields=nested,
        )

    for arrow_field in schema:
        fd = _convert_field(arrow_field)
        fields[arrow_field.name] = fd

    return ViewSchema(
        view_id=view_id,
        fields=fields,
        raw_schema_type="arrow",
        is_nested=is_nested,
        row_count_estimate=row_count_estimate,
        byte_size_estimate=byte_size_estimate,
    )


class RowExtractorContract(BaseModel):
    """Extraction contract specifying what fields an adapter requires.

    Missing fields are evaluated against the adapter's declared contract;
    they are never guessed or silently concatenated.
    """

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    adapter_id: str
    text_field: str | None = None
    structured_renderer: str | None = None
    required_fields: list[str] = Field(default_factory=list)

    def evaluate_against_schema(self, schema: ViewSchema) -> tuple[bool, list[str]]:
        """Verify that all required fields for this extractor exist in the schema."""
        missing: list[str] = []

        if self.text_field:
            if self.text_field not in schema.fields:
                missing.append(self.text_field)

        for req in self.required_fields:
            # Handle dot notation for nested fields e.g. metadata.taxonomy
            parts = req.split(".")
            cur_fields = schema.fields
            found = True
            for part in parts:
                if part in cur_fields:
                    cur_fields = cur_fields[part].nested_fields
                else:
                    found = False
                    break
            if not found:
                missing.append(req)

        return (len(missing) == 0, missing)


class RedactionUtility:
    """Operational credentials and sensitive fields scrubber.

    Operational safeguard for logs, previews, and persisted reports;
    does not purport to guarantee mathematical privacy sanitization.
    """

    HF_TOKEN_PATTERN = re.compile(r"hf_[A-Za-z0-9]{34,}")
    BEARER_PATTERN = re.compile(r"(?i)bearer\s+[A-Za-z0-9\._\-]{16,}")
    URL_CREDS_PATTERN = re.compile(r"(https?://)([^:]+):([^@]+)@")
    SENSITIVE_KEYS = frozenset(
        [
            "token",
            "secret",
            "password",
            "api_key",
            "apikey",
            "authorization",
            "auth",
            "hf_token",
            "access_token",
        ]
    )

    @classmethod
    def redact_string(cls, text: str, max_preview_len: int = 200) -> str:
        """Scrub tokens, credentials, escape control characters, and bound length."""
        s = cls.HF_TOKEN_PATTERN.sub("[REDACTED_HF_TOKEN]", text)
        s = cls.BEARER_PATTERN.sub("Bearer [REDACTED]", s)
        s = cls.URL_CREDS_PATTERN.sub(r"\1***:***@", s)

        # Escape non-printable control characters except newline and tab
        escaped_chars = []
        for ch in s:
            code = ord(ch)
            if code < 32 and ch not in ("\n", "\t"):
                escaped_chars.append(f"\\x{code:02x}")
            else:
                escaped_chars.append(ch)
        s = "".join(escaped_chars)

        if len(s) > max_preview_len:
            s = s[:max_preview_len] + "...[truncated]"
        return s

    @classmethod
    def redact_data(cls, obj: Any, max_preview_len: int = 200) -> Any:
        """Recursively scrub secrets from dicts, lists, and strings."""
        if isinstance(obj, dict):
            clean_dict: dict[str, Any] = {}
            for k, v in obj.items():
                k_lower = str(k).lower().replace("-", "_")
                if k_lower in cls.SENSITIVE_KEYS:
                    clean_dict[k] = "[REDACTED]"
                else:
                    clean_dict[k] = cls.redact_data(v, max_preview_len=max_preview_len)
            return clean_dict
        elif isinstance(obj, list):
            return [cls.redact_data(item, max_preview_len=max_preview_len) for item in obj]
        elif isinstance(obj, str):
            return cls.redact_string(obj, max_preview_len=max_preview_len)
        return obj


def compute_probe_fingerprint(
    provider: str,
    repository: str,
    immutable_revision: str,
    view_id: str,
    schema_dict: dict[str, Any],
    file_inventory: list[dict[str, Any]],
) -> str:
    """Compute a deterministic SHA-256 fingerprint for a probed snapshot view.

    Stable across runs: excludes observation timestamps and ephemeral resource metrics.
    Any modification to revision, schema structure, or file inventory alters the fingerprint.
    """
    canonical_files = sorted(
        [{"path": f.get("path", ""), "size": f.get("size", 0)} for f in file_inventory],
        key=lambda x: str(x["path"]),
    )

    payload = {
        "provider": provider.lower().strip(),
        "repository": repository.lower().strip(),
        "immutable_revision": immutable_revision.strip(),
        "view_id": view_id.lower().strip(),
        "schema": schema_dict,
        "files": canonical_files,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
