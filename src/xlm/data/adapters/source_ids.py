"""Centralized deterministic document-ID convention for file-row corpora.

Identity version 1 (``SOURCE_DOC_ID_VERSION``):

- File key: ``sha256(normalized upstream relative path)[:16]`` where the
  path is UTF-8 encoded. Normalization is exactly: ``\\`` separators become
  ``/``, leading ``./`` segments are stripped, and duplicate ``//`` runs are
  collapsed. Case is preserved (case-sensitive upstream stores stay distinct).
  The digest is lowercase hex truncated to 16 characters (64 bits), which is
  collision-resistant across millions of shards. Python's ``hash()`` is never
  used (it is salted per process and therefore not stable across reruns).
- Document ID: ``{namespace}:v1:{file_key}:{source_row}`` with a non-negative
  integer row. ``namespace`` carries the logical source/subset (for example
  ``ifm_behaviors:general`` vs ``ifm_behaviors:planning``, which stay
  distinct) and must not contain whitespace. Colons may appear inside the
  namespace itself; mechanical parsing therefore anchors on the right
  (``rsplit(":", 3)`` yields namespace, version tag, file key, row).

The helpers are pure string functions of ``(namespace, source_file,
source_row)``. They never depend on local filesystem paths, attempt or
worker counts, timing, machines, or ``XLM_HOME``, so IDs are stable across
reruns and identical on Windows vs POSIX spellings of the same upstream
relative path. ``Path.stem`` alone is NOT used because ``a/000.parquet`` and
``b/000.parquet`` share a stem and would collide.

This helper is for sources whose canonical identity is fundamentally
file + row. Sources with a trustworthy globally-unique upstream document ID
(such as ``JsonlAdapter`` records carrying an ``id`` field) keep using that
ID untouched.
"""

from __future__ import annotations

import hashlib

#: Identity contract version. Bump explicitly (with a migration note) if the
#: file-key algorithm or the document-ID format ever changes.
SOURCE_DOC_ID_VERSION = 1

#: Hex characters kept from the file-key digest (64-bit truncation).
SOURCE_FILE_KEY_HEX_CHARS = 16


def normalize_source_file(source_file: str) -> str:
    """Normalize an upstream relative path deterministically to ``/`` form."""
    if not isinstance(source_file, str) or not source_file:
        raise ValueError("source_file must be a non-empty string")
    normalized = source_file.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    while "//" in normalized:
        normalized = normalized.replace("//", "/")
    if not normalized or normalized == "/":
        raise ValueError("source_file is empty after normalization")
    return normalized


def source_file_key(source_file: str) -> str:
    """Stable collision-resistant key for an upstream relative source file."""
    normalized = normalize_source_file(source_file)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:SOURCE_FILE_KEY_HEX_CHARS]


def canonical_source_doc_id(namespace: str, source_file: str, source_row: int) -> str:
    """Build ``{namespace}:v{version}:{file_key}:{row}`` for file-row corpora."""
    if not isinstance(namespace, str) or not namespace:
        raise ValueError("namespace must be a non-empty string")
    if any(character.isspace() for character in namespace):
        raise ValueError("namespace must not contain whitespace")
    if isinstance(source_row, bool) or not isinstance(source_row, int) or source_row < 0:
        raise ValueError("source_row must be a non-negative integer")
    return f"{namespace}:v{SOURCE_DOC_ID_VERSION}:{source_file_key(source_file)}:{source_row}"
