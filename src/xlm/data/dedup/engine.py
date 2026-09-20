"""Deduplication engine: exact pass, bounded near-duplicate pass, lineage grouping.

Contract C05: deduplicate across all selected source families before splits, use
exact hashes plus a scalable near-duplicate method with bounded candidate
verification, and never do an all-pairs comparison.

The engine makes two streaming passes over the corpus. The first builds the on-disk
exact-hash and LSH-band indexes and retains only small per-document facts. The second
is not over documents at all -- it walks the indexes partition by partition. Signatures
are kept only for documents that actually share an LSH band with something else.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from pydantic import Field

from xlm.config.schemas import StrictConfigModel
from xlm.core.contracts import CanonicalDocument
from xlm.data.dedup.clusters import (
    CLUSTER_ID_VERSION,
    SURVIVOR_RULE_VERSION,
    DocumentFacts,
    DuplicateCluster,
    UnionFind,
    build_clusters,
)
from xlm.data.dedup.index import PartitionedKeyIndex
from xlm.data.dedup.lineage import LINEAGE_RULE_VERSION, lineage_key
from xlm.data.dedup.matchview import MATCH_VIEW_VERSION, match_normalize
from xlm.data.dedup.minhash import (
    MINHASH_ALGORITHM_VERSION,
    MinHashConfig,
    MinHasher,
    estimated_jaccard,
    stable_hash64,
)
from xlm.data.normalization import compute_sha256

DEDUP_ENGINE_VERSION = "1"


class DedupConfig(StrictConfigModel):
    """Frozen deduplication configuration."""

    minhash: MinHashConfig = Field(default_factory=MinHashConfig)
    partition_count: int = Field(
        default=16,
        ge=1,
        le=4096,
        description="On-disk index partitions. Affects memory and IO only, never results.",
    )
    max_candidates_per_document: int = Field(
        default=64,
        ge=1,
        description="Hard ceiling on verified candidate pairs per document (bounded verification).",
    )
    max_bucket_size: int = Field(
        default=256,
        ge=2,
        description=(
            "LSH buckets larger than this are recorded as oversized rather than expanded pairwise."
        ),
    )
    enable_near_duplicates: bool = Field(default=True)

    def identity(self) -> str:
        """Stable behavioral identity of the whole dedup configuration."""
        payload = (
            f"dedup:v{DEDUP_ENGINE_VERSION}:view{MATCH_VIEW_VERSION}:"
            f"cluster{CLUSTER_ID_VERSION}:survivor{SURVIVOR_RULE_VERSION}:"
            f"lineage{LINEAGE_RULE_VERSION}:minhash{MINHASH_ALGORITHM_VERSION}:"
            f"{self.minhash.identity()}:cand={self.max_candidates_per_document}:"
            f"bucket={self.max_bucket_size}:near={self.enable_near_duplicates}"
        )
        return compute_sha256(payload)[:32]


@dataclass
class DedupStats:
    """Observable counters for a deduplication run."""

    documents_seen: int = 0
    exact_duplicate_pairs: int = 0
    near_candidate_pairs_considered: int = 0
    near_duplicate_pairs_confirmed: int = 0
    candidate_pairs_skipped_budget: int = 0
    oversized_buckets: int = 0
    signatures_retained: int = 0
    index_entries_written: int = 0
    hash_collisions_checked: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DedupResult:
    """Outcome of a deduplication run."""

    config_identity: str
    clusters: list[DuplicateCluster]
    lineage_groups: dict[str, list[str]]
    stats: DedupStats
    survivor_doc_ids: list[str] = field(default_factory=list)
    dropped_doc_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "config_identity": self.config_identity,
            "clusters": [c.to_dict() for c in self.clusters],
            "lineage_groups": self.lineage_groups,
            "stats": self.stats.to_dict(),
            "survivor_doc_ids": self.survivor_doc_ids,
            "dropped_doc_ids": self.dropped_doc_ids,
        }


class DeduplicationEngine:
    """Streaming exact and near-duplicate detection across source families."""

    def __init__(self, config: DedupConfig | None = None) -> None:
        self.config = config or DedupConfig()
        self.hasher = MinHasher(self.config.minhash)

    def run(
        self,
        documents: Iterable[CanonicalDocument],
        work_dir: Path,
    ) -> DedupResult:
        """Deduplicate ``documents``, using ``work_dir`` for on-disk indexes."""
        work_dir.mkdir(parents=True, exist_ok=True)
        stats = DedupStats()
        facts: dict[str, DocumentFacts] = {}
        match_kinds: dict[str, set[str]] = {}
        lineage_members: dict[str, list[str]] = {}
        union = UnionFind()

        exact_index = PartitionedKeyIndex(work_dir / "exact", self.config.partition_count)
        band_index = PartitionedKeyIndex(work_dir / "bands", self.config.partition_count)
        signature_path = work_dir / "signatures.jsonl"

        # --- Pass 1: stream documents, writing indexes and small per-doc facts. ---
        with exact_index, band_index, signature_path.open("w", encoding="utf-8") as sig_handle:
            for doc in documents:
                stats.documents_seen += 1
                rule, key = lineage_key(doc)
                facts[doc.doc_id] = DocumentFacts(
                    doc_id=doc.doc_id,
                    source_id=doc.source_id,
                    clean_hash=doc.clean_hash,
                    utf8_byte_count=doc.utf8_byte_count,
                    lineage_key=key,
                    lineage_rule=rule,
                )
                lineage_members.setdefault(key, []).append(doc.doc_id)
                union.add(doc.doc_id)

                # Exact duplicates are matched on the normalized match view, so that
                # two documents differing only in punctuation or case still collide.
                exact_index.add(compute_sha256(match_normalize(doc.text)), doc.doc_id)

                if self.config.enable_near_duplicates:
                    signature = self.hasher.signature(doc.text)
                    sig_handle.write(json.dumps({"doc_id": doc.doc_id, "sig": signature}) + "\n")
                    for band_key in self.hasher.band_keys(signature):
                        band_index.add(band_key, doc.doc_id)

        stats.index_entries_written = exact_index.entries_written + band_index.entries_written

        # Lineage groups are deliberately NOT unioned into the duplicate forest.
        # A book's chapters or a conversation's turns are a family, not duplicates:
        # they must stay together in one split, but all of them must be retained.
        # Split-safe co-assignment happens in xlm.data.pools.splits.

        # --- Pass 2a: exact duplicates, one index partition at a time. ---
        for _key, doc_ids in exact_index.groups(min_size=2):
            for other in doc_ids[1:]:
                union.union(doc_ids[0], other)
                stats.exact_duplicate_pairs += 1
                match_kinds.setdefault(other, set()).add("exact")
            match_kinds.setdefault(doc_ids[0], set()).add("exact")

        # --- Pass 2b: bounded near-duplicate verification over LSH buckets. ---
        if self.config.enable_near_duplicates:
            self._verify_near_duplicates(band_index, signature_path, union, match_kinds, stats)

        clusters = build_clusters(union, facts, match_kinds)
        dropped = sorted({d for c in clusters for d in c.dropped_doc_ids})
        dropped_set = set(dropped)
        survivors = sorted(doc_id for doc_id in facts if doc_id not in dropped_set)

        return DedupResult(
            config_identity=self.config.identity(),
            clusters=clusters,
            lineage_groups={k: sorted(v) for k, v in sorted(lineage_members.items())},
            stats=stats,
            survivor_doc_ids=survivors,
            dropped_doc_ids=dropped,
        )

    def _verify_near_duplicates(
        self,
        band_index: PartitionedKeyIndex,
        signature_path: Path,
        union: UnionFind,
        match_kinds: dict[str, set[str]],
        stats: DedupStats,
    ) -> None:
        """Confirm candidate pairs from LSH buckets under a hard per-document budget."""
        candidate_pairs = self._collect_candidate_pairs(band_index, stats)
        if not candidate_pairs:
            return

        needed = {doc_id for pair in candidate_pairs for doc_id in pair}
        signatures = self._load_signatures(signature_path, needed)
        stats.signatures_retained = len(signatures)

        threshold = self.config.minhash.jaccard_threshold
        for left, right in sorted(candidate_pairs):
            stats.near_candidate_pairs_considered += 1
            left_sig, right_sig = signatures.get(left), signatures.get(right)
            if left_sig is None or right_sig is None:
                continue
            if estimated_jaccard(left_sig, right_sig) >= threshold:
                union.union(left, right)
                stats.near_duplicate_pairs_confirmed += 1
                match_kinds.setdefault(left, set()).add("near")
                match_kinds.setdefault(right, set()).add("near")

    def _collect_candidate_pairs(
        self, band_index: PartitionedKeyIndex, stats: DedupStats
    ) -> set[tuple[str, str]]:
        """Expand LSH buckets into candidate pairs under explicit bounds.

        Two bounds keep this sub-quadratic: an oversized bucket is recorded and
        skipped rather than expanded, and each document contributes at most
        ``max_candidates_per_document`` pairs.
        """
        pairs: set[tuple[str, str]] = set()
        per_doc_budget: dict[str, int] = {}
        budget = self.config.max_candidates_per_document

        for _band_key, doc_ids in band_index.groups(min_size=2):
            if len(doc_ids) > self.config.max_bucket_size:
                # An oversized bucket usually means boilerplate shared by many
                # documents. Expanding it would be quadratic, so it is reported
                # instead of silently dropped or silently expanded.
                stats.oversized_buckets += 1
                continue

            for i, left in enumerate(doc_ids):
                for right in doc_ids[i + 1 :]:
                    pair = (left, right) if left < right else (right, left)
                    if pair in pairs:
                        continue
                    if per_doc_budget.get(left, 0) >= budget or (
                        per_doc_budget.get(right, 0) >= budget
                    ):
                        stats.candidate_pairs_skipped_budget += 1
                        continue
                    pairs.add(pair)
                    per_doc_budget[left] = per_doc_budget.get(left, 0) + 1
                    per_doc_budget[right] = per_doc_budget.get(right, 0) + 1
        return pairs

    @staticmethod
    def _load_signatures(path: Path, needed: set[str]) -> dict[str, list[int]]:
        """Load only the signatures of documents that reached candidate verification."""
        signatures: dict[str, list[int]] = {}
        if not path.is_file():
            return signatures
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                stripped = line.strip()
                if not stripped:
                    continue
                record = json.loads(stripped)
                if record["doc_id"] in needed:
                    signatures[record["doc_id"]] = record["sig"]
        return signatures


def iter_surviving_documents(
    documents: Iterable[CanonicalDocument],
    result: DedupResult,
) -> Iterator[CanonicalDocument]:
    """Yield surviving documents, annotating each with its duplicate cluster.

    Survivors carry their cluster's retained source aliases, so dropping duplicates
    does not lose the provenance of the sources that also contained the text (C05).
    """
    cluster_by_member = {
        member: cluster for cluster in result.clusters for member in cluster.member_doc_ids
    }
    dropped = set(result.dropped_doc_ids)

    for doc in documents:
        if doc.doc_id in dropped:
            continue
        cluster = cluster_by_member.get(doc.doc_id)
        if cluster is None:
            yield doc
            continue

        cluster_ids = dict(doc.cluster_ids)
        cluster_ids["duplicate_cluster"] = cluster.cluster_id
        metadata = dict(doc.source_metadata)
        metadata["duplicate_source_aliases"] = cluster.source_aliases
        metadata["duplicate_cluster_size"] = cluster.size
        metadata["duplicate_match_kinds"] = cluster.match_kinds

        yield CanonicalDocument(
            doc_id=doc.doc_id,
            source_id=doc.source_id,
            source_revision=doc.source_revision,
            source_file=doc.source_file,
            source_row=doc.source_row,
            raw_hash=doc.raw_hash,
            clean_hash=doc.clean_hash,
            text=doc.text,
            utf8_byte_count=doc.utf8_byte_count,
            language=doc.language,
            language_confidence=doc.language_confidence,
            document_kind=doc.document_kind,
            source_metadata=metadata,
            parent_ids=list(doc.parent_ids),
            license_reference=doc.license_reference,
            transform_log=list(doc.transform_log),
            quality_reasons=list(doc.quality_reasons),
            cluster_ids=cluster_ids,
            split=doc.split,
        )


def detect_hash_collision_risk(facts: dict[str, DocumentFacts]) -> list[str]:
    """Report document IDs whose 64-bit shingle-space identity collides.

    SHA-256 clean hashes are treated as collision-free for corpus scale; the 64-bit
    BLAKE2b shingle hashes are not. This surfaces the risk rather than asserting its
    absence, as required by the milestone's hash-collision documentation duty.
    """
    seen: dict[int, str] = {}
    collisions: list[str] = []
    for doc_id in sorted(facts):
        truncated = stable_hash64(facts[doc_id].clean_hash)
        previous = seen.get(truncated)
        if previous is not None and facts[previous].clean_hash != facts[doc_id].clean_hash:
            collisions.append(doc_id)
        else:
            seen[truncated] = doc_id
    return collisions
