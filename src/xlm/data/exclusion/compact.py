"""Compact exact token matcher: the physical C05 backend for frozen v4 signatures.

This module changes only how the frozen pattern set is represented and searched.
Rendering, normalization, signatures and the protected index are untouched.

Observable contract (identical to :class:`~xlm.data.exclusion.streaming.StreamingMatcher`):
``match(tokens)`` returns ``None`` when no frozen token sequence occurs contiguously
in ``tokens``; otherwise the canonical digest of the pattern that the historical
Aho-Corasick scan reports, namely the longest pattern among those ending at the
smallest end position. Provenance never enters the compiled artifact.

Representation (all packed, little-endian, memory-mapped read-only):

* vocabulary: the distinct benchmark tokens sorted by code point (= UTF-8 byte
  order), ids ``1..V``; id ``0`` is reserved for every corpus token outside the
  vocabulary, and no pattern contains it, so an unknown token never equals one;
* unique patterns: duplicate provenance is ignored, each distinct token sequence
  is stored once in lexicographic id order as flattened ids plus offsets;
* one anchor per pattern: patterns of at least ``q`` tokens are anchored on their
  rarest internal ``q``-gram (fewest occurrences of its fingerprint over all
  unique patterns; ties: smallest offset), shorter patterns on
  themselves; anchors are bucketed by a 64-bit fingerprint in sorted arrays.

Exactness argument. Let pattern ``P`` (anchor length ``a``, offset ``o``) occur at
document start ``s``. Its anchor tokens are ``doc[s+o : s+o+a]``; the fingerprint is a
pure function of a token-id window, so that window's fingerprint equals the stored
key and the bucket lookup at anchor position ``t = s+o`` yields the entry ``(P, o)``.
The candidate start ``t - o = s`` is then checked by the full-sequence fingerprint
(again equal for a real occurrence) and accepted only after exact id-by-id
comparison. So every real occurrence is found (no false negative) and nothing is
accepted without exact equality (no false positive); fingerprint collisions cost
time only. A match ending at ``e`` has its anchor at ``t <= e``, so after the best
verified ``(end, -length)`` is known, anchors beyond that end cannot improve it and
the scan stops: the returned pattern is the historical one.

``automaton_nodes`` keeps its historical meaning as the exact logical trie size
(root plus every distinct non-empty pattern prefix) that ``StreamingMatcher`` would
allocate. It is derived without a trie: over lexicographically sorted unique
patterns, distinct prefixes are ``sum(len) - sum(lcp with predecessor)``.
"""

from __future__ import annotations

import hashlib
import math
import mmap
import os
from array import array
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from types import MappingProxyType, TracebackType
from typing import Any, Final

import numpy as np
import numpy.typing as npt

from xlm.data.evidence_v2 import canonical
from xlm.data.exclusion.policy import C05Error
from xlm.data.exclusion.streaming import index_record

BACKEND: Final = "c05-compact-exact-v1"
MANIFEST_KIND: Final = "c05_compact_matcher_manifest_v1"
ANCHOR_POLICY: Final = "rarest-internal-q-gram-v1"
HASH_FUNCTION: Final = "splitmix64-token-polynomial-mod-2^64-v1"
ANCHOR_Q: Final = 5
SUPPORTED_Q: Final = frozenset({4, 5, 6})
# Hard per-anchor candidate ceiling: per document position at most q buckets of at
# most this many entries are verified. An index that needs more fails closed.
MAX_ANCHOR_BUCKET: Final = 4096
FULL_MASK: Final = (1 << 64) - 1
MATCHER_DIR: Final = "matcher"
STAGING_DIR: Final = "matcher.staging"
MANIFEST: Final = "manifest.json"
MANIFEST_BYTES: Final = 64 * 1024
FILES: Final[Mapping[str, str]] = {
    "vocab_bytes.bin": "|u1",
    "vocab_offsets.bin": "<u8",
    "pattern_tokens.bin": "<u4",
    "pattern_offsets.bin": "<u8",
    "pattern_hashes.bin": "<u8",
    "anchor_keys.bin": "<u8",
    "anchor_starts.bin": "<u8",
    "anchor_patterns.bin": "<u4",
    "anchor_offsets.bin": "<u4",
}
COMPILED_ENTRIES: Final = frozenset({*FILES, MANIFEST, MANIFEST + ".tmp"})
# Bound derivation inputs (see ``compiled_bound``).
MIN_RECORD_BYTES: Final = 36  # {"provenance":["x"],"tokens":["y"]}\n
UINT32_MAX: Final = (1 << 32) - 1
SORT_COLUMNS: Final = 16
CHUNK_PATTERNS: Final = 1 << 18
CANDIDATE_BATCH: Final = 1 << 16
WRITE_BLOCK: Final = 16 * 1024 * 1024

_MULTIPLIER: Final = 0xD6E8FEB86659FD93  # odd, hence invertible modulo 2**64
_INVERSE: Final = pow(_MULTIPLIER, -1, 1 << 64)
_B = np.uint64(_MULTIPLIER)

U64 = npt.NDArray[np.uint64]
I64 = npt.NDArray[np.int64]
U32 = npt.NDArray[np.uint32]


class CeilingExceeded(C05Error):
    """A hard ceiling refused compilation; ``details`` are content-free aggregates."""

    def __init__(self, ceiling: str, details: Mapping[str, Any] | None = None) -> None:
        super().__init__(f"compact matcher {ceiling} ceiling exceeded")
        self.ceiling = ceiling
        self.details = dict(details or {})


def _noop() -> None:
    return None


#: Content-free compile/verify progress: (phase name, done, total or None).
Report = Callable[[str, int, int | None], None]


def _no_report(phase: str, done: int, total: int | None) -> None:
    return None


def mix(values: U64) -> U64:
    """splitmix64 finalizer of token ids (vectorized, wraps modulo 2**64)."""
    z = values + np.uint64(0x9E3779B97F4A7C15)
    z = (z ^ (z >> np.uint64(30))) * np.uint64(0xBF58476D1CE4E5B9)
    z = (z ^ (z >> np.uint64(27))) * np.uint64(0x94D049BB133111EB)
    result: U64 = z ^ (z >> np.uint64(31))
    return result


def powers(base: int, count: int) -> U64:
    table = np.empty(count, np.uint64)
    table[0] = 1
    table[1:] = np.uint64(base)
    np.cumprod(table, out=table)
    return table


def compiled_bound(benchmark_bytes: int, benchmark_patterns: int, slack_per_file: int) -> int:
    """Worst-case compiled bytes for an index admitted by the two ceilings.

    Each token occurrence costs at least ``u + 3`` index bytes (two quotes plus a
    separator, ``u`` >= 1 UTF-8 bytes); it is stored as 4 id bytes and, at its first
    occurrence, ``u`` vocabulary bytes plus an 8-byte offset: at most ``13/4`` of the
    index bytes overall. A record needs at least ``MIN_RECORD_BYTES``, and every
    unique pattern costs 40 bytes across offsets, hashes and anchor arrays.
    """
    patterns = min(benchmark_patterns, benchmark_bytes // MIN_RECORD_BYTES)
    return (
        13 * benchmark_bytes // 4
        + 8
        + 40 * patterns
        + 16
        + MANIFEST_BYTES
        + (len(FILES) + 1) * slack_per_file
    )


# --- compilation -----------------------------------------------------------


def _encode(
    index: Path,
    *,
    index_sha256: str,
    index_bytes: int,
    max_record: int,
    max_records: int | None,
    check: Callable[[], None],
    report: Report = _no_report,
) -> tuple[list[str], U32, I64]:
    """One streaming pass: provisional ids, then a remap to sorted vocabulary ids."""
    if array("I").itemsize != 4:
        raise C05Error("platform lacks a 4-byte unsigned array type")
    provisional: dict[str, int] = {}
    flat = array("I")
    lengths = array("I")
    digest = hashlib.sha256()
    size = 0
    with index.open("rb") as stream:
        while raw := stream.readline(max_record + 1):
            digest.update(raw)
            size += len(raw)
            tokens = index_record(raw, max_record).tokens
            if max_records is not None and len(lengths) >= max_records:
                raise CeilingExceeded("benchmark_patterns", {"index_records_over": max_records})
            if len(lengths) % 256 == 0:
                check()
                report("encode", size, index_bytes)
            for token in tokens:
                if token not in provisional:
                    if len(provisional) >= UINT32_MAX - 1:
                        raise C05Error("compact matcher vocabulary exceeds uint32 ids")
                    provisional[token] = len(provisional) + 1
            flat.extend([provisional[token] for token in tokens])
            lengths.append(len(tokens))
    if (digest.hexdigest(), size) != (index_sha256, index_bytes):
        raise C05Error("benchmark index changed during matcher compilation")
    if not lengths:
        raise C05Error("benchmark index has no patterns")
    report("encode", size, index_bytes)
    report("vocabulary remap", 0, None)
    vocabulary = sorted(provisional)  # code point order is UTF-8 byte order
    remap = np.zeros(len(vocabulary) + 1, np.uint32)
    previous = np.fromiter(
        (provisional[t] for t in vocabulary), dtype=np.int64, count=len(vocabulary)
    )
    remap[previous] = np.arange(1, len(vocabulary) + 1, dtype=np.uint32)
    del provisional
    ids: U32 = remap[np.frombuffer(flat, dtype=np.uint32)]
    return vocabulary, ids, np.frombuffer(lengths, dtype=np.uint32).astype(np.int64)


def _tail(flat: U32, start: int, length: int, skip: int) -> tuple[int, ...]:
    return tuple(int(x) for x in flat[start + skip : start + length])


def _common(first: tuple[int, ...], second: tuple[int, ...]) -> int:
    shared = 0
    for a, b in zip(first, second, strict=False):
        if a != b:
            break
        shared += 1
    return shared


def _unique_sorted(flat: U32, lengths: I64, check: Callable[[], None]) -> tuple[I64, I64, int]:
    """Lexicographic order of distinct patterns, their starts and the logical nodes.

    Rows are sorted on the first ``SORT_COLUMNS`` ids (zero padding sorts a prefix
    first) with length as the final key; rows still tied and longer than that are
    ordered by their exact tails. Duplicates are adjacent and dropped.
    """
    count = lengths.size
    starts = np.zeros(count, np.int64)
    np.cumsum(lengths[:-1], out=starts[1:])
    width = int(min(int(lengths.max()), SORT_COLUMNS))
    columns: list[U32] = []
    for k in range(width):
        check()
        column = np.zeros(count, np.uint32)
        present = lengths > k
        column[present] = flat[starts[present] + k]
        columns.append(column)
    # The last key is primary: column 0, then 1, ..., then length (a prefix first).
    keys: list[npt.NDArray[Any]] = [lengths, *reversed(columns)]
    order: I64 = np.lexsort(keys).astype(np.int64)
    del keys
    first = np.full(max(count - 1, 0), width, np.int64)
    empty = np.zeros(0, np.uint32)
    for k in range(width):
        check()
        ranked = columns[k][order]
        first[(ranked[1:] != ranked[:-1]) & (first == width)] = k
        columns[k] = empty
    del columns
    ordered = lengths[order]
    lcp = np.minimum(first, np.minimum(ordered[1:], ordered[:-1]))
    duplicate = (first == width) & (ordered[1:] == ordered[:-1]) & (ordered[1:] <= width)
    tied = np.flatnonzero((first == width) & (ordered[1:] > width) & (ordered[:-1] > width))
    run_start = 0
    while run_start < tied.size:
        check()
        run_end = run_start
        while run_end + 1 < tied.size and tied[run_end + 1] == tied[run_end] + 1:
            run_end += 1
        low, high = int(tied[run_start]), int(tied[run_end]) + 2
        rows = sorted(
            (int(r) for r in order[low:high]),
            key=lambda r: _tail(flat, int(starts[r]), int(lengths[r]), width),
        )
        order[low:high] = rows
        for pair in range(low, high - 1):
            a = _tail(flat, int(starts[rows[pair - low]]), int(lengths[rows[pair - low]]), width)
            b = _tail(
                flat, int(starts[rows[pair - low + 1]]), int(lengths[rows[pair - low + 1]]), width
            )
            lcp[pair] = width + _common(a, b)
            duplicate[pair] = a == b
        run_start = run_end + 1
    keep = np.ones(count, bool)
    keep[1:] = ~duplicate
    unique = order[keep]
    nodes = 1 + int(lengths[unique].sum()) - int(lcp[keep[1:]].sum())
    return unique, starts, nodes


def _anchors(
    tokens: U32,
    offsets: I64,
    vocabulary: int,
    *,
    q: int,
    mask: int,
    check: Callable[[], None],
    report: Report = _no_report,
) -> tuple[U64, I64, U64, I64]:
    """Full-pattern fingerprints and one (length, key, offset) anchor per pattern."""
    total_patterns = offsets.size - 1
    lengths = np.diff(offsets)
    longest = int(lengths.max())
    # Windows are produced pattern-major, offset-minor: exactly ``len - q + 1``
    # consecutive windows per pattern of at least q tokens, none for shorter ones.
    mixed_table = mix(np.arange(vocabulary + 1, dtype=np.uint64))
    power = powers(_MULTIPLIER, longest)
    umask = np.uint64(mask)
    hashes = np.zeros(total_patterns, np.uint64)
    windows: list[U64] = []
    for c0 in range(0, total_patterns, CHUNK_PATTERNS):
        check()
        report("anchor generation", c0, total_patterns)
        c1 = min(total_patterns, c0 + CHUNK_PATTERNS)
        segment = tokens[offsets[c0] : offsets[c1]]
        mixed = mixed_table[segment]
        size = lengths[c0:c1]
        local = offsets[c0:c1] - offsets[c0]
        owner = np.repeat(np.arange(c1 - c0), size)
        position = np.arange(segment.size) - local[owner]
        exponent = size[owner] - 1 - position
        hashes[c0:c1] = np.add.reduceat(mixed * power[exponent], local) & umask
        if segment.size < q:
            continue
        span = segment.size - q + 1
        window = mixed[:span].copy()
        for k in range(1, q):
            window = window * _B + mixed[k : k + span]
        valid = np.flatnonzero(position[:span] <= size[owner[:span]] - q)
        windows.append(window[valid] & umask)
    check()
    anchor_length = np.minimum(lengths, q)
    anchor_key = hashes.copy()
    anchor_offset = np.zeros(total_patterns, np.int64)
    every = np.concatenate(windows) if windows else np.zeros(0, np.uint64)
    del windows
    anchored = np.flatnonzero(lengths >= q)
    if anchored.size:
        per_pattern = lengths[anchored] - q + 1
        first = np.zeros(anchored.size, np.int64)
        np.cumsum(per_pattern[:-1], out=first[1:])
        if every.size != int(per_pattern.sum()):
            raise C05Error("compact matcher window accounting mismatch")
        check()
        _, inverse, tally = np.unique(every, return_inverse=True, return_counts=True)
        frequency = tally[inverse]  # Occurrences of each window's q-gram fingerprint.
        del inverse, tally
        check()
        rarest = np.flatnonzero(
            frequency == np.repeat(np.minimum.reduceat(frequency, first), per_pattern)
        )
        del frequency
        # The first rarest window at or after each pattern's first window is its own.
        chosen = rarest[np.searchsorted(rarest, first)]
        anchor_offset[anchored] = chosen - first
        anchor_key[anchored] = every[chosen]
    return hashes, anchor_length.astype(np.int64), anchor_key, anchor_offset


def sorted_logical_nodes(tokens: U32, offsets: npt.NDArray[Any], check: Callable[[], None]) -> int:
    """Logical trie nodes of strictly lexicographically sorted unique patterns.

    Refuses unless every pattern sorts strictly after its predecessor (no duplicate),
    so the count is ``1 + sum(len) - sum(lcp)``: one node per distinct prefix.
    """
    starts = offsets[:-1].astype(np.int64)
    lengths = np.diff(offsets).astype(np.int64)
    previous, current = starts[:-1], starts[1:]
    limit = np.minimum(lengths[:-1], lengths[1:])
    common = np.zeros(previous.size, np.int64)
    active = np.arange(previous.size)
    depth = 0
    while active.size:
        check()
        active = active[limit[active] > depth]
        same = tokens[previous[active] + depth] == tokens[current[active] + depth]
        active = active[same]
        common[active] += 1
        depth += 1
    prefix = common == lengths[:-1]
    differ = np.flatnonzero(~prefix)
    ordered = bool(np.all(lengths[1:][prefix] > lengths[:-1][prefix])) and bool(
        np.all(tokens[previous[differ] + common[differ]] < tokens[current[differ] + common[differ]])
    )
    if not ordered:
        raise C05Error("compiled matcher refused: patterns not strictly ordered")
    return 1 + int(lengths.sum()) - int(common.sum())


def _bucket_statistics(sizes: I64) -> dict[str, Any]:
    ordered = np.sort(sizes)
    n = int(ordered.size)

    def rank(fraction: float) -> int:
        return int(ordered[max(0, math.ceil(fraction * n) - 1)])

    largest = int(ordered[-1])
    return {
        "anchor_keys": n,
        "mean_bucket": round(float(ordered.sum()) / n, 6),
        "p50_bucket": rank(0.5),
        "p95_bucket": rank(0.95),
        "p99_bucket": rank(0.99),
        "p999_bucket": rank(0.999),
        "max_bucket": largest,
        "buckets_at_max": int((ordered == largest).sum()),
    }


def _write(
    path: Path, values: npt.NDArray[Any], dtype: str, check: Callable[[], None]
) -> dict[str, Any]:
    data = np.ascontiguousarray(values, dtype=np.dtype(dtype))
    if data.size == 0:
        raise C05Error("compact matcher array unexpectedly empty")
    view = memoryview(data).cast("B")
    value = hashlib.sha256()
    with path.open("xb") as stream:
        for begin in range(0, len(view), WRITE_BLOCK):
            check()
            block = view[begin : begin + WRITE_BLOCK]
            value.update(block)
            stream.write(block)
        stream.flush()
        os.fsync(stream.fileno())
    return {
        "dtype": dtype,
        "count": int(data.size),
        "bytes": len(view),
        "sha256": value.hexdigest(),
    }


def compile_index(
    index: Path,
    destination: Path,
    *,
    index_sha256: str,
    index_bytes: int,
    max_record: int,
    max_records: int | None,
    max_logical_nodes: int | None,
    check: Callable[[], None] = _noop,
    q: int = ANCHOR_Q,
    max_bucket: int = MAX_ANCHOR_BUCKET,
    hash_mask: int = FULL_MASK,
    report: Report = _no_report,
) -> dict[str, Any]:
    """Write the packed arrays, then the manifest last, into empty ``destination``.

    ``None`` ceilings are report-only (the content-free capacity audit); the runner
    always passes its reviewed ``benchmark_patterns`` and ``automaton_nodes``.
    ``hash_mask`` is an authored-test seam that forces fingerprint collisions; it is
    recorded in the manifest and production refuses a masked artifact.
    """
    if q not in SUPPORTED_Q or not 1 <= max_bucket <= UINT32_MAX or not 0 <= hash_mask <= FULL_MASK:
        raise C05Error("unsupported compact matcher parameters")
    if any(destination.iterdir()):
        raise C05Error("compact matcher destination is not empty")
    vocabulary, flat, lengths = _encode(
        index,
        index_sha256=index_sha256,
        index_bytes=index_bytes,
        max_record=max_record,
        max_records=max_records,
        check=check,
        report=report,
    )
    records = int(lengths.size)
    report("unique sort", 0, records)
    unique, starts, nodes = _unique_sorted(flat, lengths, check)
    report("unique sort", records, records)
    counts: dict[str, int] = {
        "index_records": records,
        "unique_patterns": int(unique.size),
        "vocabulary_tokens": len(vocabulary),
        "logical_trie_nodes": nodes,
    }
    if max_logical_nodes is not None and nodes > max_logical_nodes:
        raise CeilingExceeded("automaton_nodes", counts)
    if unique.size > UINT32_MAX:
        raise C05Error("compact matcher pattern ids exceed uint32")
    size = lengths[unique]
    offsets = np.zeros(unique.size + 1, np.int64)
    np.cumsum(size, out=offsets[1:])
    tokens = np.empty(int(offsets[-1]), np.uint32)
    for c0 in range(0, unique.size, CHUNK_PATTERNS):
        check()
        report("materialize", c0, int(unique.size))
        c1 = min(unique.size, c0 + CHUNK_PATTERNS)
        local = offsets[c0:c1] - offsets[c0]
        source = np.repeat(starts[unique[c0:c1]] - local, size[c0:c1])
        tokens[offsets[c0] : offsets[c1]] = flat[source + np.arange(source.size)]
    del flat, lengths, starts, unique
    hashes, anchor_length, anchor_key, anchor_offset = _anchors(
        tokens, offsets, len(vocabulary), q=q, mask=hash_mask, check=check, report=report
    )
    check()
    report("anchor sort", 0, None)
    order = np.lexsort((anchor_key, anchor_length))
    sorted_length = anchor_length[order]
    sorted_key = anchor_key[order]
    opening = np.r_[
        True, (sorted_length[1:] != sorted_length[:-1]) | (sorted_key[1:] != sorted_key[:-1])
    ]
    first = np.flatnonzero(opening)
    bucket_starts = np.r_[first, order.size].astype(np.int64)
    statistics = _bucket_statistics(np.diff(bucket_starts))
    counts.update(
        flattened_tokens=int(tokens.size),
        min_pattern_tokens=int(np.diff(offsets).min()),
        max_pattern_tokens=int(np.diff(offsets).max()),
    )
    if statistics["max_bucket"] > max_bucket:
        raise CeilingExceeded("anchor_bucket", {**counts, **statistics})
    ranges: dict[str, list[int]] = {}
    for length in np.unique(sorted_length[first]).tolist():
        inside = np.flatnonzero(sorted_length[first] == length)
        ranges[str(length)] = [int(inside[0]), int(inside[-1]) + 1]
    encoded = [token.encode("utf-8") for token in vocabulary]
    vocabulary_offsets = np.zeros(len(encoded) + 1, np.int64)
    np.cumsum([len(token) for token in encoded], out=vocabulary_offsets[1:])
    blob = np.frombuffer(b"".join(encoded), dtype=np.uint8)
    del encoded, vocabulary
    counts["vocabulary_bytes"] = int(blob.size)
    arrays: dict[str, npt.NDArray[Any]] = {
        "vocab_bytes.bin": blob,
        "vocab_offsets.bin": vocabulary_offsets,
        "pattern_tokens.bin": tokens,
        "pattern_offsets.bin": offsets,
        "pattern_hashes.bin": hashes,
        "anchor_keys.bin": sorted_key[first],
        "anchor_starts.bin": bucket_starts,
        "anchor_patterns.bin": order,
        "anchor_offsets.bin": anchor_offset[order],
    }
    total_bytes = sum(int(np.asarray(arrays[name]).nbytes) for name in FILES)
    written = 0
    files = {}
    for name in FILES:
        report("packed write", written, total_bytes)
        files[name] = _write(destination / name, arrays[name], FILES[name], check)
        written += int(files[name]["bytes"])
    report("packed write", written, total_bytes)
    manifest = {
        "kind": MANIFEST_KIND,
        "backend": BACKEND,
        "source_index": {"sha256": index_sha256, "bytes": index_bytes},
        "hash": {"function": HASH_FUNCTION, "mask": f"{hash_mask:016x}"},
        "anchor": {"policy": ANCHOR_POLICY, "q": q, "max_bucket": max_bucket, "lengths": ranges},
        "counts": counts,
        "anchor_statistics": statistics,
        "files": files,
        "compiled_bytes": sum(int(f["bytes"]) for f in files.values()),
    }
    # Self-digest: catches accidental edits of fields the arrays cannot re-derive.
    manifest["digest"] = canonical.digest(manifest)
    raw = canonical.canonical_bytes(manifest)
    if len(raw) > MANIFEST_BYTES:
        raise C05Error("compact matcher manifest ceiling")
    canonical.write_atomic(destination / MANIFEST, raw)
    return manifest


# --- verified open and matching ----------------------------------------------


def _expect(condition: bool, what: str) -> None:
    if not condition:
        raise C05Error("compiled matcher refused: " + what)


def read_manifest(directory: Path) -> dict[str, Any]:
    _expect(directory.is_dir() and not directory.is_symlink(), "not a plain directory")
    entries = {p.name: p for p in directory.iterdir()}
    _expect(set(entries) == {*FILES, MANIFEST}, "incomplete or unexpected compiled files")
    _expect(all(p.is_file() and not p.is_symlink() for p in entries.values()), "non-regular file")
    raw = (directory / MANIFEST).read_bytes()
    _expect(len(raw) <= MANIFEST_BYTES, "manifest size")
    manifest = canonical.loads_bytes_strict(raw)
    _expect(
        isinstance(manifest, dict) and canonical.canonical_bytes(manifest) == raw, "manifest form"
    )
    body = {k: v for k, v in manifest.items() if k != "digest"}
    _expect(manifest.get("digest") == canonical.digest(body), "manifest digest")
    return dict(manifest)


class CompactExactMatcher:
    """Read-only memory-mapped compiled matcher. Close it before moving its files."""

    def __init__(
        self,
        directory: Path,
        *,
        index_sha256: str,
        index_bytes: int,
        max_records: int | None = None,
        max_logical_nodes: int | None = None,
        q: int = ANCHOR_Q,
        max_bucket: int = MAX_ANCHOR_BUCKET,
        hash_mask: int = FULL_MASK,
        check: Callable[[], None] = _noop,
        report: Report = _no_report,
    ) -> None:
        """Verify every binding and file hash before the first lookup is possible."""
        self._maps: list[tuple[Any, mmap.mmap]] = []
        try:
            self._open(
                directory,
                index_sha256=index_sha256,
                index_bytes=index_bytes,
                max_records=max_records,
                max_logical_nodes=max_logical_nodes,
                q=q,
                max_bucket=max_bucket,
                hash_mask=hash_mask,
                check=check,
                report=report,
            )
        except BaseException:
            self.close()
            raise

    def _open(
        self,
        directory: Path,
        *,
        index_sha256: str,
        index_bytes: int,
        max_records: int | None,
        max_logical_nodes: int | None,
        q: int,
        max_bucket: int,
        hash_mask: int,
        check: Callable[[], None],
        report: Report = _no_report,
    ) -> None:
        self.directory = directory
        manifest = read_manifest(directory)
        _expect(
            (manifest.get("kind"), manifest.get("backend")) == (MANIFEST_KIND, BACKEND),
            "backend version",
        )
        _expect(
            manifest.get("source_index") == {"sha256": index_sha256, "bytes": index_bytes},
            "source index digest/bytes differ",
        )
        _expect(
            manifest.get("hash") == {"function": HASH_FUNCTION, "mask": f"{hash_mask:016x}"},
            "fingerprint function or mask",
        )
        anchor = manifest.get("anchor")
        _expect(
            isinstance(anchor, dict)
            and (anchor.get("policy"), anchor.get("q"), anchor.get("max_bucket"))
            == (ANCHOR_POLICY, q, max_bucket),
            "anchor policy",
        )
        counts = manifest.get("counts")
        statistics = manifest.get("anchor_statistics")
        files = manifest.get("files")
        _expect(
            isinstance(counts, dict) and isinstance(statistics, dict) and isinstance(files, dict),
            "manifest sections",
        )
        assert isinstance(anchor, dict) and isinstance(counts, dict)
        assert isinstance(statistics, dict) and isinstance(files, dict)
        _expect(set(files) == set(FILES), "file table")
        if max_records is not None:
            _expect(counts.get("index_records", max_records + 1) <= max_records, "pattern ceiling")
        if max_logical_nodes is not None:
            _expect(
                counts.get("logical_trie_nodes", max_logical_nodes + 1) <= max_logical_nodes,
                "automaton node ceiling",
            )
        _expect(statistics.get("max_bucket", max_bucket + 1) <= max_bucket, "anchor bucket ceiling")
        arrays: dict[str, npt.NDArray[Any]] = {}
        total = 0
        expected_bytes = sum(
            e["bytes"]
            for e in files.values()
            if isinstance(e, dict) and type(e.get("bytes")) is int
        )
        for name, dtype in FILES.items():
            entry = files[name]
            path = directory / name
            itemsize = np.dtype(dtype).itemsize
            _expect(
                isinstance(entry, dict)
                and entry.get("dtype") == dtype
                and isinstance(entry.get("count"), int)
                and entry["count"] > 0
                and entry.get("bytes") == entry["count"] * itemsize == path.stat().st_size,
                "file size/type " + name,
            )
            stream = path.open("rb")
            try:
                mapped = mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ)
            except BaseException:
                stream.close()
                raise
            self._maps.append((stream, mapped))
            value = hashlib.sha256()
            view = memoryview(mapped)
            try:
                for begin in range(0, len(view), WRITE_BLOCK):
                    check()
                    report("verify open", total + begin, expected_bytes)
                    value.update(view[begin : begin + WRITE_BLOCK])
            finally:
                view.release()
            _expect(value.hexdigest() == entry.get("sha256"), "file hash " + name)
            arrays[name] = np.frombuffer(mapped, dtype=np.dtype(dtype), count=entry["count"])
            total += entry["bytes"]
        _expect(manifest.get("compiled_bytes") == total, "compiled byte total")
        report("verify open", total, expected_bytes)
        check()
        report("verify structure", 0, None)
        blob = arrays["vocab_bytes.bin"]
        vocabulary_offsets = arrays["vocab_offsets.bin"]
        tokens = arrays["pattern_tokens.bin"]
        offsets = arrays["pattern_offsets.bin"]
        keys = arrays["anchor_keys.bin"]
        starts = arrays["anchor_starts.bin"]
        patterns = arrays["anchor_patterns.bin"]
        anchor_offsets = arrays["anchor_offsets.bin"]
        vocabulary = vocabulary_offsets.size - 1
        unique = offsets.size - 1

        def increasing(values: npt.NDArray[Any], last: int) -> bool:
            return bool(values[0] == 0 and values[-1] == last and np.all(values[1:] > values[:-1]))

        _expect(increasing(vocabulary_offsets, blob.size), "vocabulary offsets")
        _expect(increasing(offsets, tokens.size), "pattern offsets")
        _expect(increasing(starts, unique) and starts.size == keys.size + 1, "anchor buckets")
        _expect(arrays["pattern_hashes.bin"].size == unique == patterns.size, "pattern tables")
        _expect(anchor_offsets.size == unique, "anchor offsets")
        _expect(int(tokens.min()) >= 1 and int(tokens.max()) <= vocabulary, "token id range")
        _expect(int(patterns.max()) < unique, "anchor entry range")
        _expect(bool(np.all(np.bincount(patterns, minlength=unique) == 1)), "anchor entries")
        sizes_all = np.diff(offsets)
        # Re-derived from the verified arrays, never trusted from the manifest alone.
        expected_counts = {
            "unique_patterns": unique,
            "vocabulary_tokens": vocabulary,
            "flattened_tokens": int(tokens.size),
            "vocabulary_bytes": int(blob.size),
            "min_pattern_tokens": int(sizes_all.min()),
            "max_pattern_tokens": int(sizes_all.max()),
            "logical_trie_nodes": sorted_logical_nodes(tokens, offsets, check),
        }
        _expect(
            statistics == _bucket_statistics(np.diff(starts).astype(np.int64)),
            "anchor statistics",
        )
        _expect(all(counts.get(k) == v for k, v in expected_counts.items()), "manifest counts")
        ranges: dict[int, tuple[int, int]] = {}
        cursor = 0
        raw_ranges = anchor.get("lengths")
        _expect(isinstance(raw_ranges, dict) and bool(raw_ranges), "anchor length table")
        assert isinstance(raw_ranges, dict)
        for text in sorted(raw_ranges, key=int):
            low, high = raw_ranges[text]
            length = int(text)
            _expect(1 <= length <= q and low == cursor and high > low, "anchor length ranges")
            _expect(bool(np.all(keys[low + 1 : high] > keys[low : high - 1])), "anchor key order")
            entry_low, entry_high = int(starts[low]), int(starts[high])
            members = patterns[entry_low:entry_high].astype(np.int64)
            sizes = offsets[members + 1] - offsets[members]
            expected_length = np.minimum(sizes, q) if length == q else sizes
            _expect(bool(np.all(expected_length == length)), "anchor lengths")
            _expect(
                bool(np.all(anchor_offsets[entry_low:entry_high] + length <= sizes)),
                "anchor offsets in range",
            )
            ranges[length] = (low, high)
            cursor = high
        _expect(cursor == keys.size, "anchor length coverage")
        check()
        text_blob = bytes(blob)
        bounds = vocabulary_offsets.tolist()
        words = [text_blob[a:b].decode("utf-8") for a, b in zip(bounds, bounds[1:], strict=False)]
        _expect(all(a < b for a, b in zip(words, words[1:], strict=False)), "vocabulary order")
        check()
        self._vocabulary: dict[str, int] = {word: i for i, word in enumerate(words, 1)}
        self._words = words
        self._mixed = mix(np.arange(vocabulary + 1, dtype=np.uint64))
        # Zero-copy views of the mappings; gathered slices are cast to int64 on use.
        self._tokens = tokens
        self._offsets = offsets
        self._hashes = arrays["pattern_hashes.bin"]
        self._keys = keys
        self._starts = starts
        self._patterns = patterns
        self._anchor_offsets = anchor_offsets
        self._ranges = ranges
        self._q = q
        self._mask = np.uint64(hash_mask)
        self._masked = hash_mask != FULL_MASK
        self._minimum = int(counts["min_pattern_tokens"])
        self._power = powers(_MULTIPLIER, 1)
        self._inverse = powers(_INVERSE, 1)
        self.manifest = manifest

    def close(self) -> None:
        """Drop every array view, then unmap and close each file (idempotent)."""
        for name in (
            "_tokens",
            "_hashes",
            "_keys",
            "_offsets",
            "_starts",
            "_patterns",
            "_anchor_offsets",
        ):
            if hasattr(self, name):
                delattr(self, name)
        maps, self._maps = self._maps, []
        for stream, mapped in maps:
            try:
                mapped.close()
            except BufferError:
                # Only after a refused open: views held by the propagating traceback
                # keep the mapping alive; it is released when they are collected.
                pass
            stream.close()

    def __enter__(self) -> CompactExactMatcher:
        return self

    def __exit__(
        self,
        kind: type[BaseException] | None,
        value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def _ensure_powers(self, size: int) -> None:
        if self._power.size < size:
            size = max(size, 2 * int(self._power.size))
            self._power = powers(_MULTIPLIER, size)
            self._inverse = powers(_INVERSE, size)

    @property
    def vocabulary(self) -> Mapping[str, int]:
        return MappingProxyType(self._vocabulary)

    def tokens(self, pattern: int) -> list[str]:
        """Protected tokens of one unique pattern (private; never reported)."""
        begin, end = int(self._offsets[pattern]), int(self._offsets[pattern + 1])
        return [self._words[int(i) - 1] for i in self._tokens[begin:end]]

    def identity(self, pattern: int) -> str:
        """Canonical digest of a pattern's tokens, as ``StreamingMatcher`` reports it."""
        return canonical.digest(self.tokens(pattern))

    def match(self, tokens: Iterable[str]) -> str | None:
        """Return the historical first exact token-boundary hit; ``None`` when absent."""
        sequence = tokens if isinstance(tokens, (list, tuple)) else list(tokens)
        n = len(sequence)
        if n < self._minimum:
            return None
        lookup = self._vocabulary.get
        ids = np.fromiter((lookup(token, 0) for token in sequence), dtype=np.uint32, count=n)
        unknown = np.zeros(n + 1, np.int64)
        np.cumsum(ids == 0, out=unknown[1:])
        if unknown[-1] == n:
            return None
        mixed = self._mixed[ids]
        window = np.zeros(n, np.uint64)
        found_at: list[I64] = []
        found_bucket: list[I64] = []
        for length in range(1, self._q + 1):
            width = n - length + 1
            if width <= 0:
                break
            window = window[:width] * _B + mixed[length - 1 : length - 1 + width]
            span = self._ranges.get(length)
            if span is None:
                continue
            clean = np.flatnonzero(unknown[length : length + width] == unknown[:width])
            if not clean.size:
                continue
            probe = window[clean] & self._mask if self._masked else window[clean]
            low, high = span
            keys = self._keys[low:high]
            slot = np.minimum(np.searchsorted(keys, probe), high - low - 1)
            hit = keys[slot] == probe
            if hit.any():
                found_at.append(clean[hit])
                found_bucket.append(slot[hit].astype(np.int64) + low)
        if not found_at:
            return None
        at = np.concatenate(found_at)
        bucket = np.concatenate(found_bucket)
        order = np.argsort(at, kind="stable")
        at, bucket = at[order], bucket[order]
        entry_low = self._starts[bucket].astype(np.int64)
        sizes = self._starts[bucket + 1].astype(np.int64) - entry_low
        cumulative = np.cumsum(sizes)
        self._ensure_powers(n + 1)
        prefix = np.zeros(n + 1, np.uint64)
        np.cumsum(mixed * self._inverse[:n], out=prefix[1:])
        best: tuple[int, int, int] | None = None  # (exclusive end, -length, pattern)
        i, count = 0, int(at.size)
        while i < count:
            if best is not None and at[i] >= best[0]:
                break  # Every later anchor lies beyond the best end.
            base = int(cumulative[i - 1]) if i else 0
            j = max(int(np.searchsorted(cumulative, base + CANDIDATE_BATCH, side="right")), i + 1)
            if best is not None:
                j = min(j, int(np.searchsorted(at, best[0], side="left")))
            size = sizes[i:j]
            owner = np.repeat(np.arange(j - i), size)
            entry = entry_low[i:j][owner] + (
                np.arange(int(size.sum())) - (np.cumsum(size) - size)[owner]
            )
            pattern = self._patterns[entry].astype(np.int64)
            start = at[i:j][owner] - self._anchor_offsets[entry].astype(np.int64)
            begin = self._offsets[pattern].astype(np.int64)
            end = start + (self._offsets[pattern + 1].astype(np.int64) - begin)
            keep = np.flatnonzero((start >= 0) & (end <= n))
            pattern, start, begin, end = pattern[keep], start[keep], begin[keep], end[keep]
            keep = np.flatnonzero(unknown[end] == unknown[start])
            pattern, start, begin, end = pattern[keep], start[keep], begin[keep], end[keep]
            full = (prefix[end] - prefix[start]) * self._power[end - 1]
            if self._masked:
                full &= self._mask
            keep = np.flatnonzero(full == self._hashes[pattern])
            pattern, start, begin, end = pattern[keep], start[keep], begin[keep], end[keep]
            length = end - start
            for k in np.lexsort((-length, end)).tolist():
                key = (int(end[k]), -int(length[k]))
                if best is not None and key >= best[:2]:
                    break
                b, s = int(begin[k]), int(start[k])
                if np.array_equal(ids[s : int(end[k])], self._tokens[b : b + int(length[k])]):
                    best = (key[0], key[1], int(pattern[k]))
                    break
            i = j
        return None if best is None else self.identity(best[2])


# --- crash-safe publication --------------------------------------------------


def discard_staging(staging: Path) -> None:
    """Remove an interrupted compile; only known compiled file names are deleted."""
    if not staging.exists():
        return
    _expect(staging.is_dir() and not staging.is_symlink(), "staging is not a plain directory")
    entries = list(staging.iterdir())
    if any(p.name not in COMPILED_ENTRIES or not p.is_file() or p.is_symlink() for p in entries):
        raise C05Error("unexpected entry in incomplete compact matcher staging")
    for entry in entries:
        entry.unlink()
    staging.rmdir()


def prepare(
    directory: Path,
    index: Path,
    *,
    index_sha256: str,
    index_bytes: int,
    max_record: int,
    max_records: int | None,
    max_logical_nodes: int | None,
    check: Callable[[], None] = _noop,
    before_publish: Callable[[], None] = _noop,
    q: int = ANCHOR_Q,
    max_bucket: int = MAX_ANCHOR_BUCKET,
    hash_mask: int = FULL_MASK,
    report: Report = _no_report,
) -> CompactExactMatcher:
    """Reuse a fully verified published matcher, or compile it staging-then-rename.

    A published directory exists only after its manifest was written last and the
    staging directory renamed; any mismatch on reuse refuses (no silent rebuild).
    An interrupted staging directory is never trusted: it is discarded and rebuilt.
    """
    parameters: dict[str, Any] = {"q": q, "max_bucket": max_bucket, "hash_mask": hash_mask}
    binding: dict[str, Any] = {
        "index_sha256": index_sha256,
        "index_bytes": index_bytes,
        "max_records": max_records,
        "max_logical_nodes": max_logical_nodes,
    }
    if not directory.exists():
        staging = directory.with_name(directory.name + ".staging")
        discard_staging(staging)
        staging.parent.mkdir(parents=True, exist_ok=True)
        staging.mkdir()
        compile_index(
            index,
            staging,
            max_record=max_record,
            check=check,
            report=report,
            **binding,
            **parameters,
        )
        before_publish()
        os.rename(staging, directory)
    report("verify open", 0, None)
    return CompactExactMatcher(directory, check=check, report=report, **binding, **parameters)
