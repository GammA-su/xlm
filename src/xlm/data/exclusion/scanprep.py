"""Pure per-document C05 fact preparation shared by in-process and spawned scan workers.

Everything here is a deterministic function of one raw canonical line, the frozen
plan file entry, the policy and the compiled exact matcher. It never touches
authoritative state: the single parent integrates prepared batches in file/row
order, verifies whole-file identity and publishes the immutable fact unit.

The logical facts are exactly those of the historical SQLite engine
(:class:`xlm.data.exclusion.disk.DiskGroups.add`); the ``fragment`` of each
document is the byte string the historical ``facts_digest`` hashed for it, so a
file's logical facts digest is unchanged.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Final, Protocol

import numpy as np
import numpy.typing as npt

from xlm.core.contracts import CanonicalDocument
from xlm.data.dedup.lineage import lineage_keys_v3
from xlm.data.dedup.matchview import match_normalize
from xlm.data.dedup.minhash import MinHasher, signature_from_array
from xlm.data.evidence_v2 import canonical
from xlm.data.exclusion.policy import C05Error, ProductionPolicy

FORMAT: Final = "c05-facts-v2"
PERMUTATIONS: Final = 128
#: Fixed-width per-document record: canonical byte count, exact normalized-content
#: SHA-256, canonical content digest, document-id SHA-256 (lookup key only).
RECORD: Final = np.dtype(
    [("bytes", "<u8"), ("exact", "u1", (32,)), ("content", "u1", (32,)), ("id_digest", "u1", (32,))]
)
_BLAKE2B = hashlib.blake2b
_SHA256 = hashlib.sha256


class Matcher(Protocol):
    def match(self, tokens: Sequence[str]) -> str | None: ...


@dataclass(frozen=True)
class FileContext:
    """Content-free plan entry fields that every document of one file shares."""

    ordinal: int
    path: str
    source_id: str
    source_revision: str
    component: str
    view: str
    upstream: str | None
    document_bytes: int
    document_tokens: int


@dataclass(frozen=True)
class Strings:
    """A per-document list of UTF-8 strings: blob, string offsets, per-doc counts, digests."""

    blob: bytes
    offsets: bytes  # uint64, one more than the number of strings
    counts: bytes  # uint32 per document
    digests: bytes  # 32-byte SHA-256 per string


@dataclass(frozen=True)
class PreparedBatch:
    """Compact immutable facts for ``count`` consecutive rows of one plan file.

    ``error`` names the first failing row (1-based in the file) and a content-free
    reason; rows before it are omitted because the whole file transaction fails.
    """

    file: int
    sequence: int
    first_row: int
    count: int
    records: bytes
    signatures: bytes
    ids: bytes
    id_offsets: bytes
    lineage: Strings
    parents: Strings
    hits: bytes  # (row-in-batch uint32, 32-byte pattern identity) per matched document
    fragments: bytes
    fragment_offsets: bytes
    review: tuple[tuple[int, tuple[str, ...]], ...] | None
    error: tuple[int, str] | None = None


def shingle_hashes(normalized: str, size: int) -> npt.NDArray[np.uint64]:
    """The historical shingle hash multiset of ``DiskGroups.add`` (duplicates removed).

    Shingles are ``" ".join(tokens[i:i + size])`` for ``i`` in
    ``range(max(1, len(tokens) - size + 1))``; for an empty view that is the
    single empty string. Match-view tokens are separated by exactly one ASCII
    space in ``normalized`` and UTF-8 never encodes another character with byte
    0x20, so each shingle is a byte slice of ``normalized.encode()`` and needs no
    per-shingle join/encode. Hash: big-endian 64-bit BLAKE2b (``stable_hash64``).
    """
    data = normalized.encode("utf-8")
    parts = data.split(b" ") if data else []
    count = len(parts)
    if count <= size:
        digests = _BLAKE2B(data, digest_size=8).digest()
    else:
        starts = [0] * (count + 1)
        position = 0
        for index, part in enumerate(parts):
            position += len(part) + 1
            starts[index + 1] = position
        view = memoryview(data)
        digests = b"".join(
            [
                _BLAKE2B(view[starts[i] : starts[i + size] - 1], digest_size=8).digest()
                for i in range(count - size + 1)
            ]
        )
    return np.unique(np.frombuffer(digests, dtype=">u8").astype(np.uint64))


def _string_list(values: Sequence[Sequence[str]]) -> Strings:
    encoded = [[value.encode("utf-8") for value in row] for row in values]
    flat = [item for row in encoded for item in row]
    offsets = np.zeros(len(flat) + 1, dtype="<u8")
    if flat:
        np.cumsum([len(item) for item in flat], out=offsets[1:])
    return Strings(
        blob=b"".join(flat),
        offsets=offsets.tobytes(),
        counts=np.array([len(row) for row in encoded], dtype="<u4").tobytes(),
        digests=b"".join(_SHA256(item).digest() for item in flat),
    )


def _document(raw: bytes, context: FileContext) -> CanonicalDocument:
    value = canonical.loads_bytes_strict(raw)
    if not isinstance(value, dict):
        raise C05Error("canonical record must be an object")
    doc = CanonicalDocument(**value)
    if not isinstance(doc.text, str) or len(doc.text.encode("utf-8")) != doc.utf8_byte_count:
        raise C05Error("canonical text byte count mismatch")
    if type(doc.utf8_byte_count) is not int:
        raise C05Error("canonical text byte count type")
    if doc.source_id != context.source_id or doc.source_revision != context.source_revision:
        raise C05Error("canonical source identity mismatch")
    if not isinstance(doc.doc_id, str):
        raise C05Error("canonical document id type")
    if not isinstance(doc.source_metadata, dict) or not isinstance(doc.parent_ids, list):
        raise C05Error("canonical lineage field type")
    if any(p and not isinstance(p, str) for p in doc.parent_ids):
        raise C05Error("canonical parent id type")
    return doc


class Preparer:
    """Stateless per-process helper: MinHash parameters and the policy checks."""

    def __init__(self, policy: ProductionPolicy, matcher: Matcher, review: bool) -> None:
        self.policy = policy
        self.matcher = matcher
        self.review = review
        self.hasher = MinHasher(policy.minhash())
        if policy.permutations != PERMUTATIONS:
            raise C05Error("compact facts store 128 MinHash permutations")

    def batch(
        self, context: FileContext, sequence: int, first_row: int, lines: Sequence[bytes]
    ) -> PreparedBatch:
        records = np.zeros(len(lines), dtype=RECORD)
        signatures = np.empty((len(lines), PERMUTATIONS), dtype="<u8")
        ids: list[bytes] = []
        lineage: list[tuple[str, ...]] = []
        parents: list[tuple[str, ...]] = []
        hits: list[bytes] = []
        fragments: list[bytes] = []
        review: list[tuple[int, tuple[str, ...]]] = []
        for index, raw in enumerate(lines):
            row = first_row + index
            try:
                prepared = self._one(raw, context, row)
            except Exception as exc:  # noqa: BLE001 - the whole file fails closed
                return self._failed(context, sequence, first_row, row, exc)
            doc_bytes, exact, content, identity, signature, keys, unique, hit, piece, tokens = (
                prepared
            )
            signatures[index] = signature
            record = records[index]
            record["bytes"] = doc_bytes
            record["exact"] = np.frombuffer(exact, dtype=np.uint8)
            record["content"] = np.frombuffer(content, dtype=np.uint8)
            record["id_digest"] = np.frombuffer(_SHA256(identity).digest(), dtype=np.uint8)
            ids.append(identity)
            lineage.append(keys)
            parents.append(unique)
            if hit is not None:
                hits.append(np.uint32(index).tobytes() + hit)
            fragments.append(piece)
            if self.review:
                review.append((len(tokens), tuple(sorted(set(tokens)))))
        id_offsets = np.zeros(len(ids) + 1, dtype="<u8")
        np.cumsum([len(i) for i in ids], out=id_offsets[1:])
        fragment_offsets = np.zeros(len(fragments) + 1, dtype="<u8")
        np.cumsum([len(f) for f in fragments], out=fragment_offsets[1:])
        return PreparedBatch(
            file=context.ordinal,
            sequence=sequence,
            first_row=first_row,
            count=len(lines),
            records=records.tobytes(),
            signatures=signatures.tobytes(),
            ids=b"".join(ids),
            id_offsets=id_offsets.tobytes(),
            lineage=_string_list(lineage),
            parents=_string_list(parents),
            hits=b"".join(hits),
            fragments=b"".join(fragments),
            fragment_offsets=fragment_offsets.tobytes(),
            review=tuple(review) if self.review else None,
        )

    def _one(self, raw: bytes, context: FileContext, row: int) -> tuple[Any, ...]:
        doc, normalized, tokens, hit = self._parse(raw, context)
        self._gutenberg(doc, context)
        best = signature_from_array(
            shingle_hashes(normalized, self.policy.shingle_size), self.hasher._params
        )
        signature = np.array(best, dtype="<u8")
        exact = _SHA256(normalized.encode("utf-8")).digest()
        content = canonical.digest(doc.to_dict())
        keys = lineage_keys_v3(doc)
        unique = tuple(sorted({p for p in doc.parent_ids if p}))
        if hit is not None and (len(hit) != 64 or hit != hit.lower()):
            raise C05Error("matcher identity is not a SHA-256 digest")
        piece = fragment(
            doc.doc_id,
            context,
            row,
            content,
            doc.utf8_byte_count,
            exact.hex(),
            signature,
            hit,
            self.hasher.band_keys(best),
            keys,
            unique,
        )
        return (
            doc.utf8_byte_count,
            exact,
            bytes.fromhex(content),
            doc.doc_id.encode("utf-8"),
            signature,
            keys,
            unique,
            None if hit is None else bytes.fromhex(hit),
            piece,
            tokens,
        )

    def _parse(
        self, raw: bytes, context: FileContext
    ) -> tuple[CanonicalDocument, str, list[str], str | None]:
        doc = _document(raw, context)
        normalized = match_normalize(doc.text)
        tokens = normalized.split(" ") if normalized else []
        if len(tokens) > context.document_tokens:
            raise C05Error("normalized document token ceiling")
        return doc, normalized, tokens, self.matcher.match(tokens)

    def _gutenberg(self, doc: CanonicalDocument, context: FileContext) -> None:
        if (
            self.policy.gutenberg == "require_book_ids"
            and (
                context.upstream == "project_gutenberg"
                or doc.source_metadata.get("upstream_component") == "project_gutenberg"
            )
            and not doc.source_metadata.get("book_id")
        ):
            raise C05Error("Gutenberg whole-book policy requires real book identity")

    @staticmethod
    def _failed(
        context: FileContext, sequence: int, first_row: int, row: int, exc: Exception
    ) -> PreparedBatch:
        # C05Error reasons are fixed authored strings; any other message may hold text.
        reason = str(exc)[:200] if isinstance(exc, C05Error) else type(exc).__name__
        empty = Strings(b"", np.zeros(1, "<u8").tobytes(), b"", b"")
        return PreparedBatch(
            file=context.ordinal,
            sequence=sequence,
            first_row=first_row,
            count=0,
            records=b"",
            signatures=b"",
            ids=b"",
            id_offsets=np.zeros(1, "<u8").tobytes(),
            lineage=empty,
            parents=empty,
            hits=b"",
            fragments=b"",
            fragment_offsets=np.zeros(1, "<u8").tobytes(),
            review=None,
            error=(row, reason if isinstance(exc, C05Error) else "record rejected: " + reason),
        )


def fragment(
    doc_id: str,
    context: FileContext,
    row: int,
    content: str,
    size: int,
    exact: str,
    signature: npt.NDArray[Any],
    hit: str | None,
    bands: Sequence[str],
    lineage: Sequence[str],
    parents: Sequence[str],
) -> bytes:
    """The bytes the historical ``DiskGroups.facts_digest`` hashed for one document.

    Row ``[id,source,component,view,file,row,content,bytes,exact,signature,hit,
    upstream]`` (signature as little-endian hex), then band, lineage and parent
    postings, each in SQLite ``ORDER BY key`` (UTF-8 byte) order.
    """
    head = canonical.canonical_bytes(
        [
            doc_id,
            context.source_id,
            context.component,
            context.view,
            context.path,
            row,
            content,
            size,
            exact,
            np.asarray(signature, dtype="<u8").tobytes().hex(),
            hit,
            context.upstream,
        ]
    )
    parts = [head]
    parts.extend(b'["bands","' + key.encode("ascii") + b'"]' for key in sorted(bands))
    parts.extend(canonical.canonical_bytes(["lineage", key]) for key in _utf8_sorted(lineage))
    parts.extend(canonical.canonical_bytes(["parent", key]) for key in _utf8_sorted(parents))
    return b"".join(parts)


def review_fragment(rows: Sequence[tuple[str, str, int, int]]) -> bytes:
    """Historical review-queue postings of one document, ``ORDER BY ref,pattern``."""
    ordered = sorted(rows, key=lambda r: (r[0].encode("utf-8"), r[1].encode("utf-8")))
    return b"".join(canonical.canonical_bytes(["review", *row]) for row in ordered)


def _utf8_sorted(values: Sequence[str]) -> list[str]:
    return sorted(values, key=lambda value: value.encode("utf-8"))
