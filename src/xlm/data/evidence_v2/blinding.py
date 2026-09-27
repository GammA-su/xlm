"""Blinded review-package mechanisms (protocol section 9).

Custodian-side only: fresh 32-byte secret K (never in reviewer files),
SHA-256 key commitment, HMAC-SHA256 opaque review IDs
(``"ew2-" + hexdigest``), and separate deterministic per-reviewer orders
from the frozen review-order seed. Reviewer packages carry opaque ID,
plain text, rubric, and blank forms only; a leak assertion refuses any
forbidden source attribute. Sealed mapping, key commitment, ranks, and
acquisition receipt stay access-restricted. No real labeling here.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from collections.abc import Mapping, Sequence
from typing import Any

from xlm.data.evidence_v2 import canonical, frozen


class BlindingError(ValueError):
    """Any ID collision, leak, or construction violation: STOP."""


def generate_secret() -> bytes:
    """Fresh 32-byte cryptographically random custodian secret K."""
    return secrets.token_bytes(32)


def check_secret(secret: bytes) -> None:
    if type(secret) is not bytes or len(secret) != 32:
        raise BlindingError("custodian secret K must be exactly 32 bytes")


def key_commitment(secret: bytes) -> str:
    """Published SHA-256 commitment; K itself stays sealed."""
    check_secret(secret)
    return hashlib.sha256(secret).hexdigest()


def review_id(secret: bytes, locator: Sequence[Any]) -> str:
    """Opaque ID for ``[repository, revision, source_file, source_row]``."""
    check_secret(secret)
    if len(locator) != 4 or type(locator[3]) is not int:
        raise BlindingError("locator must be [repository, revision, file, int row]")
    mac = hmac.new(
        secret,
        canonical.canonical_bytes(
            [frozen.PROTOCOL_VERSION, frozen.REVIEW_ID_PREFIX, list(locator)]
        ),
        hashlib.sha256,
    ).hexdigest()
    return frozen.REVIEW_ID_TAG + mac


def order_key(reviewer: str, review_id_value: str) -> str:
    """Frozen per-reviewer order hash for one review ID."""
    if reviewer not in frozen.REVIEWERS:
        raise BlindingError(f"unknown reviewer: {reviewer}")
    return canonical.digest(
        [
            frozen.PROTOCOL_VERSION,
            frozen.REVIEW_ORDER_PREFIX,
            frozen.REVIEW_ORDER_SEED,
            reviewer,
            review_id_value,
        ]
    )


def order_ids(ids: Sequence[str], reviewer: str) -> list[str]:
    """Deterministic reviewer order; ties (impossible in practice) by ID."""
    if len(set(ids)) != len(ids):
        raise BlindingError("duplicate review IDs presented for ordering")
    return sorted(ids, key=lambda i: (order_key(reviewer, i), i))


def build_package(
    items: Sequence[Mapping[str, Any]],
    secret: bytes,
    reviewer: str,
    rubric_version: str,
) -> dict[str, Any]:
    """Assemble one reviewer's package; refuse any source-attribute leak.

    Each item carries ``review_id``, ``text``, ``stratum`` (for sealed
    bookkeeping only, never packaged), and ``locator`` (sealed only).
    Returns the reviewer package plus the sealed entries for the mapping.
    """
    check_secret(secret)
    package_forms: list[dict[str, Any]] = []
    sealed: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        rid = review_id(secret, item["locator"])
        if rid in seen:
            raise BlindingError(f"review-ID collision: {rid}")
        seen.add(rid)
        text = item.get("text")
        if not isinstance(text, str) or not text:
            raise BlindingError(f"item {rid} has no reviewable text")
        form: dict[str, Any] = {
            "review_id": rid,
            "text": text,
            "rubric_version": rubric_version,
            "reviewer": reviewer,
            "labels": None,
        }
        leaked = sorted(k for k in form if k in frozen.FORBIDDEN_PACKAGE_FIELDS)
        if leaked:
            raise BlindingError(f"package form leaks source fields: {leaked}")
        package_forms.append(form)
        sealed.append(
            {
                "review_id": rid,
                "locator": list(item["locator"]),
                "stratum": item.get("stratum"),
                "rank": item.get("rank"),
            }
        )
    ordered = order_ids([f["review_id"] for f in package_forms], reviewer)
    by_id = {f["review_id"]: f for f in package_forms}
    return {
        "kind": "essential_web_evidence_v2_review_package",
        "protocol_version": frozen.PROTOCOL_VERSION,
        "reviewer": reviewer,
        "rubric_version": rubric_version,
        "key_commitment": key_commitment(secret),
        "order": ordered,
        "order_digest": canonical.digest(ordered),
        "forms": [by_id[i] for i in ordered],
        "sealed_entries": sealed,
    }


def split_sealed(package: Mapping[str, Any]) -> tuple[dict[str, Any], list[Any]]:
    """Separate the reviewer-safe package from access-restricted entries."""
    public = {k: v for k, v in package.items() if k != "sealed_entries"}
    return public, list(package.get("sealed_entries", []))
