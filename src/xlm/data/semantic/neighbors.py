"""Semantic candidate generation contract (P28-U) and threshold analysis (P28-W).

Inputs: normalized vectors, document ordinals, top_k, minimum similarity.
Output: deterministic candidate pairs ``(doc_a, doc_b, score)`` streamed to
JSONL, one per line, with:

- no self-pairs (backends exclude them; rechecked here),
- canonical ordering ``a < b``,
- duplicate pair suppression (same pair from several queries kept once,
  highest score wins),
- deterministic emission order (score descending, then ordinals ascending),
- the similarity threshold applied consistently,
- bounded RAM: backend results stream per query batch; only the surviving
  pair map is retained.

FAISS (or any backend) is candidate generation only (P28-V): the default
product is this sidecar. An explicit versioned experimental policy may
additionally cluster pairs above threshold, but it never runs by default
and never touches survivor output.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from xlm.data.semantic.backends import NeighborBackend, backend_metadata

SEMANTIC_CANDIDATE_VERSION = "1"
SEMANTIC_POLICY_NONE = "none-v1"
SEMANTIC_POLICY_V0_EXPERIMENTAL = "v0-experimental-threshold-union"


@dataclass
class SemanticCandidateArtifact:
    """Identity of one candidate-generation run (P28-X)."""

    schema_version: int = 1
    artifact_type: str = "semantic_candidates"
    clean_artifact_identity: dict[str, Any] = field(default_factory=dict)
    embedding_identity: str = ""
    backend_family: str = ""
    index_params: dict[str, Any] = field(default_factory=dict)
    top_k: int = 0
    threshold: float = 0.0
    candidate_file: str = "candidates.jsonl"
    candidate_count: int = 0
    policy: str = SEMANTIC_POLICY_NONE

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def identity(self) -> str:
        """Logical identity: hardware timing excluded, backend family included."""
        payload = json.dumps(
            {
                "schema_version": self.schema_version,
                "clean_artifact_identity": self.clean_artifact_identity,
                "embedding_identity": self.embedding_identity,
                "backend_family": self.backend_family,
                "index_params": self.index_params,
                "top_k": self.top_k,
                "threshold": self.threshold,
                "policy": self.policy,
            },
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def generate_candidates(
    *,
    backend: NeighborBackend,
    vectors: Any,
    ordinals: list[int],
    top_k: int,
    threshold: float,
    output_path: Path,
    query_batch_size: int = 2048,
    index_params: dict[str, Any] | None = None,
) -> tuple[SemanticCandidateArtifact, dict[str, Any]]:
    """Generate the deterministic candidate sidecar; return (artifact, telemetry).

    Vectors are added once; queries run in bounded batches. No N x N matrix
    is ever allocated: backend search is batched and pair emission streams.
    """
    if top_k < 1:
        raise ValueError(f"top_k must be positive, got {top_k}")
    if not 0.0 <= threshold <= 1.0:
        raise ValueError(f"threshold must lie in [0, 1], got {threshold}")
    if len(ordinals) != _row_count(vectors):
        raise ValueError("vector/ordinal count mismatch")
    if len(set(ordinals)) != len(ordinals):
        raise ValueError("candidate ordinals are not unique")

    wall_start = time.monotonic()
    backend.build(vectors, ordinals)
    build_seconds = time.monotonic() - wall_start

    # Highest score wins per canonical pair; bounded by pairs above threshold.
    best: dict[tuple[int, int], float] = {}
    search_seconds = 0.0
    for start in range(0, len(ordinals), query_batch_size):
        batch_ordinals = ordinals[start : start + query_batch_size]
        batch_vectors = _slice_rows(vectors, start, start + query_batch_size)
        searched = time.monotonic()
        hit_ids, hit_scores = backend.search(batch_vectors, batch_ordinals, top_k)
        search_seconds += time.monotonic() - searched
        for query_ordinal, ids, scores in zip(batch_ordinals, hit_ids, hit_scores, strict=True):
            for other, score in zip(ids, scores, strict=True):
                if other == query_ordinal:
                    continue
                pair = (query_ordinal, other) if query_ordinal < other else (other, query_ordinal)
                if score >= threshold and score > best.get(pair, float("-inf")):
                    best[pair] = score

    ordered = sorted(best.items(), key=lambda item: (-item[1], item[0][0], item[0][1]))
    temporary = output_path.parent / f"{output_path.name}.{uuid.uuid4().hex}.tmp"
    with temporary.open("w", encoding="utf-8") as stream:
        for (left, right), score in ordered:
            stream.write(json.dumps({"doc_a": left, "doc_b": right, "score": score}) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, output_path)

    artifact = SemanticCandidateArtifact(
        backend_family=backend.name,
        index_params=dict(index_params or {}),
        top_k=top_k,
        threshold=threshold,
        candidate_count=len(ordered),
    )
    telemetry = {
        **backend_metadata(backend),
        "index_build_seconds": build_seconds,
        "search_seconds": search_seconds,
        "top_k": top_k,
        "threshold": threshold,
        "candidate_count": len(ordered),
        "wall_seconds": round(time.monotonic() - wall_start, 3),
    }
    return artifact, telemetry


def summarize_thresholds(
    candidate_path: Path, thresholds: list[float] | None = None
) -> dict[str, Any]:
    """Threshold sweep analysis over a candidate sidecar (P28-W, analysis only).

    Reports score quantiles, per-threshold candidate counts, and the
    cluster-size distribution of the threshold-union graph at each point.
    These are analysis points, never default policy recommendations.
    """
    points = thresholds or [0.90, 0.92, 0.94, 0.96, 0.98]
    scores: list[float] = []
    edges: list[tuple[int, int, float]] = []
    with candidate_path.open("r", encoding="utf-8") as stream:
        for line in stream:
            stripped = line.strip()
            if not stripped:
                continue
            record = json.loads(stripped)
            score = float(record["score"])
            scores.append(score)
            edges.append((int(record["doc_a"]), int(record["doc_b"]), score))
    scores.sort()
    count = len(scores)

    def quantile(q: float) -> float | None:
        if not scores:
            return None
        position = q * (count - 1)
        lower = int(position)
        upper = min(count - 1, lower + 1)
        fraction = position - lower
        return scores[lower] * (1.0 - fraction) + scores[upper] * fraction

    sweep: dict[str, Any] = {}
    for point in points:
        members = [(a, b) for a, b, s in edges if s >= point]
        parent: dict[int, int] = {}

        def find(node: int, _parent: dict[int, int] = parent) -> int:
            while _parent.get(node, node) != node:
                _parent[node] = _parent.get(_parent[node], _parent[node])
                node = _parent[node]
            return _parent.get(node, node)

        for a, b in members:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[rb] = ra
        sizes: dict[int, int] = {}
        for a, _b in members:
            root = find(a)
            sizes[root] = sizes.get(root, 0) + 1
        distribution = sorted(sizes.values(), reverse=True)[:10]
        sweep[f"{point:.2f}"] = {
            "candidate_pairs": len(members),
            "components": len(sizes),
            "largest_component_edges": distribution[0] if distribution else 0,
            "top_component_sizes": distribution,
        }
    return {
        "candidate_count": count,
        "score_min": scores[0] if scores else None,
        "score_max": scores[-1] if scores else None,
        "score_quantiles": {f"p{int(q * 100)}": quantile(q) for q in (0.5, 0.9, 0.99)},
        "threshold_sweep": sweep,
    }


def _row_count(vectors: Any) -> int:
    if isinstance(vectors, list):
        return len(vectors)
    try:
        return int(vectors.shape[0])
    except AttributeError:
        return len(list(vectors))


def _slice_rows(vectors: Any, start: int, stop: int) -> Any:
    if isinstance(vectors, list):
        return vectors[start:stop]
    try:
        return vectors[start:stop]
    except TypeError:
        rows = list(vectors)
        return rows[start:stop]
