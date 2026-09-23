"""Cross-source deduplication, lineage grouping and duplicate cluster resolution."""

from __future__ import annotations

from xlm.data.dedup.clusters import (
    CLUSTER_ID_VERSION,
    SURVIVOR_RULE_VERSION,
    DocumentFacts,
    DuplicateCluster,
    UnionFind,
    build_clusters,
    compute_cluster_id,
    select_survivor,
)
from xlm.data.dedup.engine import (
    DEDUP_ENGINE_VERSION,
    DedupConfig,
    DeduplicationEngine,
    DedupResult,
    DedupStats,
    detect_hash_collision_risk,
    iter_surviving_documents,
)
from xlm.data.dedup.index import PartitionedKeyIndex, partition_for
from xlm.data.dedup.lineage import LINEAGE_RULE_VERSION, canonical_url, lineage_key
from xlm.data.dedup.matchview import MATCH_VIEW_VERSION, match_normalize, match_tokens
from xlm.data.dedup.minhash import (
    MINHASH_ALGORITHM_VERSION,
    MinHashConfig,
    MinHasher,
    estimated_jaccard,
    shingles,
    shingles_from_tokens,
    stable_hash64,
)
from xlm.data.dedup.sharded import (
    AssembledDedup,
    ShardedDedupTelemetry,
    dedup_unit_worker,
    run_sharded_dedup,
)

__all__ = [
    "CLUSTER_ID_VERSION",
    "DEDUP_ENGINE_VERSION",
    "LINEAGE_RULE_VERSION",
    "MATCH_VIEW_VERSION",
    "MINHASH_ALGORITHM_VERSION",
    "SURVIVOR_RULE_VERSION",
    "AssembledDedup",
    "DedupConfig",
    "DedupResult",
    "DedupStats",
    "DeduplicationEngine",
    "DocumentFacts",
    "DuplicateCluster",
    "MinHashConfig",
    "MinHasher",
    "PartitionedKeyIndex",
    "ShardedDedupTelemetry",
    "UnionFind",
    "build_clusters",
    "canonical_url",
    "compute_cluster_id",
    "dedup_unit_worker",
    "detect_hash_collision_risk",
    "estimated_jaccard",
    "iter_surviving_documents",
    "lineage_key",
    "match_normalize",
    "match_tokens",
    "partition_for",
    "run_sharded_dedup",
    "select_survivor",
    "shingles",
    "shingles_from_tokens",
    "stable_hash64",
]
