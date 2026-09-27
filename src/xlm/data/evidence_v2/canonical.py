"""Canonical JSON, strict parsing, and atomic publication for evidence-v2.

Protocol section 2 / 12 construction: ``C(x)`` is UTF-8 bytes of Python
3.12 ``json.dumps(x, sort_keys=True, separators=(",", ":"),
ensure_ascii=False, allow_nan=False)`` with no BOM or trailing newline.
``H(x)`` is the lowercase SHA-256 hex digest of ``C(x)``. Parsing
rejects duplicate object keys, non-finite numbers, invalid Unicode, and
unknown fields. Receipts self-digest excluding only top-level ``digest``.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any


class CanonicalError(ValueError):
    """Any canonical-form, parse, or binding violation: refuse, never coerce."""


def canonical_bytes(value: Any) -> bytes:
    """Serialize ``C(x)``; rejects non-finite floats at dump time."""
    try:
        text = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (ValueError, TypeError) as exc:
        raise CanonicalError(f"not canonical-serializable: {exc}") from exc
    return text.encode("utf-8")


def digest(value: Any) -> str:
    """``H(x)``: lowercase SHA-256 hex of ``C(x)``."""
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    obj: dict[str, Any] = {}
    for key, val in pairs:
        if key in obj:
            raise CanonicalError(f"duplicate object key: {key!r}")
        obj[key] = val
    return obj


def _reject_nonfinite(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        raise CanonicalError("non-finite number in canonical payload")
    if isinstance(value, dict):
        return {k: _reject_nonfinite(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_reject_nonfinite(v) for v in value]
    return value


def loads_strict(text: str) -> Any:
    """Parse JSON, rejecting duplicate keys and non-finite numbers."""
    try:
        value = json.loads(text, object_pairs_hook=_reject_duplicates, parse_constant=_bad)
    except CanonicalError:
        raise
    except (ValueError, TypeError) as exc:
        raise CanonicalError(f"invalid JSON: {exc}") from exc
    return _reject_nonfinite(value)


def _bad(name: str) -> Any:
    raise CanonicalError(f"non-finite constant in canonical payload: {name}")


def loads_bytes_strict(raw: bytes) -> Any:
    """Decode strict UTF-8 (no BOM tolerance) then parse strictly."""
    if raw.startswith(b"\xef\xbb\xbf"):
        raise CanonicalError("byte-order mark is not canonical")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CanonicalError(f"invalid UTF-8: {exc}") from exc
    return loads_strict(text)


def check_known_fields(obj: Mapping[str, Any], allowed: frozenset[str], *, what: str) -> None:
    """Reject unknown fields; every receipt declares its exact schema."""
    unknown = sorted(k for k in obj if k not in allowed)
    if unknown:
        raise CanonicalError(f"{what} has unknown fields: {unknown}")


def self_digest(body: Mapping[str, Any]) -> str:
    """Canonical digest of a receipt excluding only top-level ``digest``."""
    return digest({k: v for k, v in body.items() if k != "digest"})


def file_binding(path: Path) -> dict[str, int | str]:
    """Byte length plus file SHA-256 for one artifact path."""
    raw = path.read_bytes()
    return {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def descriptor(relative_path: str, raw: bytes, media_type: str) -> dict[str, Any]:
    """Non-JSON artifact descriptor, itself canonically digested."""
    body: dict[str, Any] = {
        "relative_path": relative_path,
        "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "media_type": media_type,
    }
    body["descriptor_digest"] = digest(body)
    return body


def write_atomic(path: Path, payload: bytes) -> None:
    """Write bytes atomically (tmp + fsync + os.replace); never partial."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(tmp, path)


def write_canonical_json(path: Path, obj: Mapping[str, Any]) -> dict[str, int | str]:
    """Write the exact canonical bytes ``C(x)``: no BOM, no trailing newline.

    Returns the file binding (bytes + SHA-256) for receipt embedding.
    """
    raw = canonical_bytes(obj)
    write_atomic(path, raw)
    return {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def verify_binding(path: Path, expected: Mapping[str, Any], *, what: str) -> None:
    """Refuse when a file's bytes or hash drift from its bound receipt."""
    binding = file_binding(path)
    if binding["bytes"] != expected["bytes"] or binding["sha256"] != expected["sha256"]:
        raise CanonicalError(f"{what} drifted from its bound bytes/hash: {path}")


def check_sequence_of_strings(values: Any, *, what: str) -> tuple[str, ...]:
    """Validate an exact ordered string array (selection keys); no coercion."""
    if not isinstance(values, list) or not all(type(v) is str for v in values):
        raise CanonicalError(f"{what} must be an array of strings")
    return tuple(values)


def check_exact_int(value: Any, *, what: str) -> int:
    """Integers only; bools are not integers here."""
    if type(value) is not int:
        raise CanonicalError(f"{what} must be a JSON integer")
    return value


def check_ordered_paths(files: Sequence[str], *, what: str) -> None:
    """Sorted-ascending UTF-8 path arrays stay sorted; refuse drift."""
    ordered = sorted(files)
    if list(files) != ordered:
        raise CanonicalError(f"{what} is not sorted-ascending by UTF-8 bytes")
