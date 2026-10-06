"""Canonical train-membership identity over prepared token shards (P35 M5, §H/§M).

Membership answers *which documents, with which immutable content, belong to the
admitted train split of each source*. It is computed from the token shards the
trainer actually reads (``offsets.jsonl`` + ``tokens.bin``), because that is the
only training-side record of the documents; upstream pool digests are not
carried into shards (P35-M5 audit, row 1).

The identity is **order-independent**:

- every positional index field (``token_start``, ``byte_start``, ``byte_end``) is
  excluded from a document's digest;
- per source, documents are folded in ``doc_id`` order, never in physical order.

It is **content-sensitive**: a document's digest covers every other index field
(doc/source/lineage/split/split group, token and byte counts, per-token byte
spans, BOS/EOS positions, valid targets, ...) plus the SHA-256 of its exact
token bytes. Adding, removing, substituting or changing a document, or moving it
to another source, lineage or split, changes the identity.

Only ``split == "train"`` documents are admitted: a held-out record anywhere in a
shard refuses the whole membership (split firewall).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from xlm.artifacts.manifest import identity_digest
from xlm.data.tokens import TokenShardReader

MEMBERSHIP_VERSION = "xlm-canonical-train-membership-v1"
TRAIN_SPLIT = "train"
#: Index fields that encode physical position; excluded from content identity.
POSITIONAL_FIELDS = frozenset({"token_start", "byte_start", "byte_end"})
#: Bound on admitted documents per membership (memory: a few hundred bytes each).
MAX_DOCUMENTS = 20_000_000
MAX_INDEX_LINE_BYTES = 8 * 1024**2
_TOKEN_BYTES = {"uint16": 2, "uint32": 4}


class MembershipError(ValueError):
    """The shards cannot define a trustworthy canonical train membership."""

    def __init__(self, message: str, code: str = "membership_invalid") -> None:
        super().__init__(message)
        self.code = code


def shard_identity(reader: TokenShardReader) -> dict[str, Any]:
    """Physical artifact identity of one source shard (order-dependent checksums)."""
    manifest = reader.manifest
    return {
        "shard_id": manifest.shard_id,
        "source_id": manifest.source_id,
        "checksum_sha256": manifest.checksum_sha256,
        "offsets_checksum_sha256": manifest.offsets_checksum_sha256,
        "num_tokens": manifest.num_tokens,
        "num_documents": manifest.num_documents,
        "token_dtype": manifest.token_dtype,
        "tokenizer_hash": manifest.tokenizer_hash,
    }


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def member_digest(record: Mapping[str, Any], token_dtype: str, token_bytes: bytes) -> str:
    """Order-independent digest of one document's immutable identity and content."""
    index = {
        key: value
        for key, value in record.items()
        if key not in POSITIONAL_FIELDS and not key.startswith("_xlm")
    }
    return hashlib.sha256(
        _canonical(
            {
                "version": MEMBERSHIP_VERSION,
                "index": index,
                "token_dtype": token_dtype,
                "token_sha256": hashlib.sha256(token_bytes).hexdigest(),
            }
        )
    ).hexdigest()


def membership_digest(entries: list[tuple[str, str]]) -> str:
    """Fold ``(doc_id, member_digest)`` pairs in ``doc_id`` order (order-independent)."""
    digest = hashlib.sha256(f"{MEMBERSHIP_VERSION}\n".encode())
    for doc_id, value in sorted(entries):
        digest.update(_canonical([doc_id, value]) + b"\n")
    return digest.hexdigest()


@dataclass(frozen=True)
class SourceMembership:
    """Admitted train documents of one source, in physical (native) order."""

    source_id: str
    shard: dict[str, Any]
    doc_ids: tuple[str, ...]
    member_digests: tuple[str, ...]
    token_count: int
    byte_count: int
    membership_digest: str

    @property
    def document_count(self) -> int:
        return len(self.doc_ids)

    def summary(self) -> dict[str, Any]:
        return {
            "document_count": self.document_count,
            "token_count": self.token_count,
            "byte_count": self.byte_count,
            "membership_digest": self.membership_digest,
        }


@dataclass(frozen=True)
class CanonicalMembership:
    """Canonical train membership across all sources of one training input."""

    tokenizer_hash: str
    sources: Mapping[str, SourceMembership]

    def summary(self) -> dict[str, Any]:
        return {
            "membership_version": MEMBERSHIP_VERSION,
            "split": TRAIN_SPLIT,
            "tokenizer_hash": self.tokenizer_hash,
            "sources": {sid: self.sources[sid].summary() for sid in sorted(self.sources)},
        }

    @property
    def membership_id(self) -> str:
        return identity_digest(self.summary())


def summary_membership_id(summary: Mapping[str, Any]) -> str:
    """Recompute a membership id from its summary, after structural checks."""
    if not isinstance(summary, Mapping) or set(summary) != {
        "membership_version",
        "split",
        "tokenizer_hash",
        "sources",
    }:
        raise MembershipError("membership summary has an unexpected shape")
    if summary["membership_version"] != MEMBERSHIP_VERSION or summary["split"] != TRAIN_SPLIT:
        raise MembershipError("membership summary is not a train-split v1 membership")
    sources = summary["sources"]
    if not isinstance(sources, Mapping) or not sources:
        raise MembershipError("membership summary names no sources")
    for sid, entry in sources.items():
        if not isinstance(entry, Mapping) or set(entry) != {
            "document_count",
            "token_count",
            "byte_count",
            "membership_digest",
        }:
            raise MembershipError(f"membership summary for source '{sid}' is malformed")
    return identity_digest(dict(summary))


def _source_membership(
    reader: TokenShardReader, *, admitted_so_far: int, max_documents: int
) -> SourceMembership:
    manifest = reader.manifest
    size = _TOKEN_BYTES.get(manifest.token_dtype)
    if size is None:
        raise MembershipError(f"unsupported token dtype '{manifest.token_dtype}'")
    seen: set[str] = set()
    doc_ids: list[str] = []
    digests: list[str] = []
    cursor = byte_total = 0
    with (
        (reader.directory / "offsets.jsonl").open("rb") as index,
        (reader.directory / "tokens.bin").open("rb") as tokens,
    ):
        while raw := index.readline(reader.index_record_bytes + 1):
            if len(raw) > reader.index_record_bytes:
                raise MembershipError("document index entry exceeds schema byte limit")
            if not raw.strip():
                continue
            record = json.loads(raw)
            if not isinstance(record, dict):
                raise MembershipError("document index entry must be an object")
            doc_id = record.get("doc_id")
            if not isinstance(doc_id, str) or not doc_id:
                raise MembershipError("every document needs a non-empty string doc_id")
            if record.get("source_id") != manifest.source_id:
                raise MembershipError(
                    f"document '{doc_id}' names source {record.get('source_id')!r}, shard "
                    f"source is '{manifest.source_id}'",
                    code="membership_source_mismatch",
                )
            if record.get("split") != TRAIN_SPLIT:
                raise MembershipError(
                    f"document '{doc_id}' in source '{manifest.source_id}' has split "
                    f"{record.get('split')!r}; only admitted train documents enter a training "
                    "membership (held-out documents are never ordered into training)",
                    code="membership_split_violation",
                )
            if doc_id in seen:
                raise MembershipError(
                    f"duplicate doc_id '{doc_id}' in source '{manifest.source_id}'",
                    code="membership_duplicate_document",
                )
            count = record.get("token_count")
            if type(count) is not int or count < 1 or record.get("token_start") != cursor:
                raise MembershipError("document token index must be contiguous and nonempty")
            byte_count = record.get("byte_count")
            if type(byte_count) is not int or byte_count < 0:
                raise MembershipError(f"document '{doc_id}' has an invalid byte_count")
            reader.check_read_window(count)
            payload = tokens.read(count * size)
            if len(payload) != count * size:
                raise MembershipError(f"token payload of '{doc_id}' is truncated")
            if reader.index_schema == "c07-offsets-v2":
                # Scientific document identity includes spans under either storage schema.
                record = reader.with_byte_spans(record)
            if admitted_so_far + len(doc_ids) >= max_documents:
                raise MembershipError(f"membership exceeds {max_documents} documents")
            seen.add(doc_id)
            doc_ids.append(doc_id)
            digests.append(member_digest(record, manifest.token_dtype, payload))
            cursor += count
            byte_total += byte_count
        if tokens.read(1):
            raise MembershipError("tokens.bin holds tokens beyond the document index")
    if cursor != manifest.num_tokens or len(doc_ids) != manifest.num_documents:
        raise MembershipError("document index coverage differs from the shard manifest")
    return SourceMembership(
        source_id=manifest.source_id,
        shard=shard_identity(reader),
        doc_ids=tuple(doc_ids),
        member_digests=tuple(digests),
        token_count=cursor,
        byte_count=byte_total,
        membership_digest=membership_digest(list(zip(doc_ids, digests, strict=True))),
    )


def build_membership(
    readers: Mapping[str, TokenShardReader],
    *,
    verify: bool = True,
    max_documents: int = MAX_DOCUMENTS,
) -> CanonicalMembership:
    """Canonical train membership of verified source shards (bounded, streaming).

    ``verify`` re-checks each shard's checksums first, so the membership is bound
    to exactly the bytes the manifests name.
    """
    if not readers:
        raise MembershipError("a membership needs at least one source shard")
    tokenizers = {reader.manifest.tokenizer_hash for reader in readers.values()}
    if len(tokenizers) != 1:
        raise MembershipError("source shards were tokenized by different tokenizers")
    sources: dict[str, SourceMembership] = {}
    admitted = 0
    for source_id in sorted(readers):
        reader = readers[source_id]
        if reader.manifest.source_id != source_id:
            raise MembershipError(
                f"shard for '{source_id}' declares source '{reader.manifest.source_id}'",
                code="membership_source_mismatch",
            )
        if verify:
            reader.verify_integrity()
        source = _source_membership(reader, admitted_so_far=admitted, max_documents=max_documents)
        admitted += source.document_count
        sources[source_id] = source
    return CanonicalMembership(tokenizer_hash=tokenizers.pop(), sources=sources)
