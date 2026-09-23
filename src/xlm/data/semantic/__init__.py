"""Semantic-neighbor candidate infrastructure (P28-L..Z, EXPERIMENTAL, OFF by default).

FAISS is candidate generation only: no document is ever removed on the basis of a
vector similarity score. The default product of this package is a candidate
sidecar/report; survivor decisions stay exclusively with the lexical dedup engine
unless an explicit versioned experimental policy flag is passed.
"""

from __future__ import annotations

from xlm.data.semantic.backends import (
    BackendChoice,
    FaissCpuBackend,
    FaissGpuBackend,
    NeighborBackend,
    NumpyBackend,
    PythonBackend,
    describe_capabilities,
    resolve_backend,
)
from xlm.data.semantic.embeddings import (
    EMBEDDING_MANIFEST_VERSION,
    EmbeddingManifest,
    load_embedding_manifest,
    read_vector_rows_stdlib,
    read_vector_shard,
    validate_embedding_artifact,
    write_vector_shard,
)
from xlm.data.semantic.neighbors import (
    SEMANTIC_CANDIDATE_VERSION,
    SEMANTIC_POLICY_NONE,
    SEMANTIC_POLICY_V0_EXPERIMENTAL,
    generate_candidates,
    summarize_thresholds,
)
from xlm.data.semantic.providers import (
    EmbeddingProvider,
    SyntheticEmbeddingProvider,
    TextHashEmbeddingProvider,
)

__all__ = [
    "EMBEDDING_MANIFEST_VERSION",
    "SEMANTIC_CANDIDATE_VERSION",
    "SEMANTIC_POLICY_NONE",
    "SEMANTIC_POLICY_V0_EXPERIMENTAL",
    "BackendChoice",
    "EmbeddingManifest",
    "EmbeddingProvider",
    "FaissCpuBackend",
    "FaissGpuBackend",
    "NeighborBackend",
    "NumpyBackend",
    "PythonBackend",
    "SyntheticEmbeddingProvider",
    "TextHashEmbeddingProvider",
    "describe_capabilities",
    "generate_candidates",
    "load_embedding_manifest",
    "read_vector_rows_stdlib",
    "read_vector_shard",
    "resolve_backend",
    "summarize_thresholds",
    "validate_embedding_artifact",
    "write_vector_shard",
]
