"""Nearest-neighbor backend protocol and capability-detected backends (P28-P/Q/R/Y).

Backends: ``python`` (pure-Python exact cosine for small fixtures, runs
everywhere), ``numpy`` (exact chunked cosine, needs NumPy), ``faiss-cpu``
(exact IndexFlatIP, needs the ``faiss`` package), ``faiss-gpu`` (exact
IndexFlatIP on GPU resources, needs ``faiss`` with GPU support plus a
visible GPU). ``auto`` selects the fastest available compatible backend
and records the resolution; an explicitly pinned ``faiss-gpu`` that cannot
be satisfied fails loudly instead of silently falling back.

FAISS is optional: this module imports it lazily and the project stays
fully usable when it is absent.
"""

from __future__ import annotations

from typing import Any, Protocol

BackendChoice = str

BACKEND_AUTO = "auto"
BACKEND_PYTHON = "python"
BACKEND_NUMPY = "numpy"
BACKEND_FAISS_CPU = "faiss-cpu"
BACKEND_FAISS_GPU = "faiss-gpu"


class NeighborBackend(Protocol):
    """Exact top-k cosine backend over pre-normalized vectors."""

    @property
    def name(self) -> str: ...

    def build(self, vectors: Any, ordinals: list[int]) -> None:
        """Ingest normalized vectors with their document ordinals (add once)."""
        ...

    def search(
        self, queries: Any, query_ordinals: list[int], top_k: int
    ) -> tuple[list[list[int]], list[list[float]]]:
        """Top-k (ordinal, score) per query, excluding self-pairs.

        Ties at the cutoff resolve by ascending ordinal so results are
        deterministic for every backend.
        """
        ...


def _as_rows(vectors: Any) -> list[list[float]]:
    """Materialize nested float rows from arrays or nested lists."""
    if isinstance(vectors, list):
        return [[float(v) for v in row] for row in vectors]
    try:
        return [[float(v) for v in row] for row in vectors.tolist()]
    except AttributeError:
        return [[float(v) for v in row] for row in vectors]


class PythonBackend:
    """Pure-Python exact cosine search (small fixtures; runs everywhere)."""

    def __init__(self) -> None:
        self._vectors: list[list[float]] = []
        self._ordinals: list[int] = []

    @property
    def name(self) -> str:
        return BACKEND_PYTHON

    def build(self, vectors: Any, ordinals: list[int]) -> None:
        rows = _as_rows(vectors)
        if len(rows) != len(ordinals):
            raise ValueError(f"build got {len(rows)} vectors for {len(ordinals)} ordinals")
        self._vectors = rows
        self._ordinals = list(ordinals)

    def search(
        self, queries: Any, query_ordinals: list[int], top_k: int
    ) -> tuple[list[list[int]], list[list[float]]]:
        if top_k < 1:
            raise ValueError(f"top_k must be positive, got {top_k}")
        query_rows = _as_rows(queries)
        if len(query_rows) != len(query_ordinals):
            raise ValueError("query/ordinal count mismatch")
        out_ids: list[list[int]] = []
        out_scores: list[list[float]] = []
        for query, query_ordinal in zip(query_rows, query_ordinals, strict=True):
            scored: list[tuple[float, int]] = []
            for ordinal, candidate in zip(self._ordinals, self._vectors, strict=True):
                if ordinal == query_ordinal:
                    continue
                score = sum(a * b for a, b in zip(query, candidate, strict=True))
                scored.append((score, ordinal))
            # Score descending, ordinal ascending: deterministic ties.
            scored.sort(key=lambda item: (-item[0], item[1]))
            top = scored[:top_k]
            out_ids.append([ordinal for _, ordinal in top])
            out_scores.append([score for score, _ in top])
        return out_ids, out_scores


class NumpyBackend:
    """Exact chunked cosine search with NumPy (needs NumPy installed)."""

    def __init__(self, query_batch_size: int = 512) -> None:
        if query_batch_size < 1:
            raise ValueError("query batch size must be positive")
        self._query_batch_size = query_batch_size
        self._matrix: Any = None
        self._ordinals: list[int] = []

    @property
    def name(self) -> str:
        return BACKEND_NUMPY

    def build(self, vectors: Any, ordinals: list[int]) -> None:
        import numpy as np  # type: ignore[import-not-found]

        matrix = np.ascontiguousarray(vectors, dtype=np.float32)
        if matrix.ndim != 2:
            raise ValueError(f"expected 2-D vectors, got shape {matrix.shape}")
        if matrix.shape[0] != len(ordinals):
            raise ValueError("vector/ordinal count mismatch")
        self._matrix = matrix
        self._ordinals = list(ordinals)

    def search(
        self, queries: Any, query_ordinals: list[int], top_k: int
    ) -> tuple[list[list[int]], list[list[float]]]:
        import numpy as np

        if self._matrix is None:
            raise RuntimeError("backend has no index; call build first")
        if top_k < 1:
            raise ValueError(f"top_k must be positive, got {top_k}")
        queries_array = np.ascontiguousarray(queries, dtype=np.float32)
        if queries_array.shape[0] != len(query_ordinals):
            raise ValueError("query/ordinal count mismatch")
        n_index = self._matrix.shape[0]
        k = min(top_k, max(0, n_index - 1))
        out_ids: list[list[int]] = []
        out_scores: list[list[float]] = []
        for start in range(0, queries_array.shape[0], self._query_batch_size):
            batch = queries_array[start : start + self._query_batch_size]
            # Cosine == inner product on normalized vectors; one C-level
            # matmul per batch, never an N x N matrix across batches. The
            # full score block is never materialized as Python objects.
            # Exactness with ties: argpartition finds the cutoff score, then
            # a mask pulls EVERY index at or above it, so ordinal tie-breaks
            # at the cutoff resolve identically to the exhaustive backends.
            scores = batch @ self._matrix.T
            want = min(k + 1, scores.shape[1])
            part = np.argpartition(-scores, kth=want - 1, axis=1)[:, :want]
            shortlist = np.take_along_axis(scores, part, axis=1)
            cutoff = shortlist.min(axis=1, keepdims=True)
            mask = scores >= cutoff
            for row_idx, query_ordinal in enumerate(
                query_ordinals[start : start + self._query_batch_size]
            ):
                tied = np.flatnonzero(mask[row_idx]).tolist()
                order = sorted(
                    (
                        (float(scores[row_idx, position]), self._ordinals[position])
                        for position in tied
                        if self._ordinals[position] != query_ordinal
                    ),
                    key=lambda item: (-item[0], item[1]),
                )[:k]
                out_ids.append([ordinal for _, ordinal in order])
                out_scores.append([score for score, _ in order])
        return out_ids, out_scores


def _import_faiss() -> Any:
    try:
        import faiss  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError(
            "FAISS is not installed in this environment (no network installs "
            "happen here); use backend 'numpy' or 'python', or run in an "
            "operator environment with faiss-cpu/faiss-gpu installed."
        ) from exc
    return faiss


class FaissCpuBackend:
    """Exact IndexFlatIP cosine search on CPU (needs the ``faiss`` package).

    Returned rows are re-sorted by (score descending, ordinal ascending) on
    CPU, but tied sets truncated inside FAISS before return can differ from
    exhaustive backends at exact-tie cutoffs (documented limitation; the
    NumPy/Python backends are exhaustive and identical).
    """

    def __init__(self) -> None:
        self._index: Any = None
        self._ordinals: list[int] = []
        self._dim = 0

    @property
    def name(self) -> str:
        return BACKEND_FAISS_CPU

    def build(self, vectors: Any, ordinals: list[int]) -> None:
        faiss = _import_faiss()
        import numpy as np

        matrix = np.ascontiguousarray(vectors, dtype=np.float32)
        if matrix.ndim != 2 or matrix.shape[0] != len(ordinals):
            raise ValueError("vector/ordinal shape mismatch")
        self._dim = int(matrix.shape[1])
        self._index = faiss.IndexFlatIP(self._dim)
        self._index.add(matrix)
        self._ordinals = list(ordinals)

    def search(
        self, queries: Any, query_ordinals: list[int], top_k: int
    ) -> tuple[list[list[int]], list[list[float]]]:
        import numpy as np

        if self._index is None:
            raise RuntimeError("backend has no index; call build first")
        if top_k < 1:
            raise ValueError(f"top_k must be positive, got {top_k}")
        queries_array = np.ascontiguousarray(queries, dtype=np.float32)
        k = min(top_k + 1, self._index.ntotal)
        scores, positions = self._index.search(queries_array, k)
        out_ids: list[list[int]] = []
        out_scores: list[list[float]] = []
        for row_scores, row_positions, query_ordinal in zip(
            scores.tolist(), positions.tolist(), query_ordinals, strict=True
        ):
            order = sorted(
                (
                    (score, self._ordinals[position])
                    for score, position in zip(row_scores, row_positions, strict=True)
                    if position >= 0 and self._ordinals[position] != query_ordinal
                ),
                key=lambda item: (-item[0], item[1]),
            )[:top_k]
            out_ids.append([ordinal for _, ordinal in order])
            out_scores.append([score for score, _ in order])
        return out_ids, out_scores


class FaissGpuBackend(FaissCpuBackend):
    """Exact IndexFlatIP cosine search on GPU resources (needs faiss + GPU).

    Uses ``StandardGpuResources`` + ``index_cpu_to_gpu`` when both exist;
    construction fails loudly when either is missing so a pinned
    ``faiss-gpu`` research run can never silently run on CPU.
    """

    @property
    def name(self) -> str:
        return BACKEND_FAISS_GPU

    def build(self, vectors: Any, ordinals: list[int]) -> None:
        faiss = _import_faiss()
        if not hasattr(faiss, "StandardGpuResources") or not hasattr(faiss, "index_cpu_to_gpu"):
            raise RuntimeError(
                "faiss-gpu requested but this faiss build exposes no "
                "StandardGpuResources/index_cpu_to_gpu; refusing to run."
            )
        gpu_count = int(faiss.get_num_gpus()) if hasattr(faiss, "get_num_gpus") else 0
        if gpu_count < 1:
            raise RuntimeError("faiss-gpu requested but FAISS reports zero GPUs; refusing to run.")
        super().build(vectors, ordinals)
        resources = faiss.StandardGpuResources()
        self._index = faiss.index_cpu_to_gpu(resources, 0, self._index)


def faiss_version() -> str | None:
    """Installed faiss version, or None when faiss is absent."""
    try:
        import faiss
    except ImportError:
        return None
    return str(getattr(faiss, "__version__", "unknown"))


def faiss_gpu_count() -> int:
    """GPUs visible to FAISS, or 0 when faiss/GPU support is absent."""
    try:
        import faiss
    except ImportError:
        return 0
    if not hasattr(faiss, "get_num_gpus"):
        return 0
    try:
        return int(faiss.get_num_gpus())
    except Exception:
        return 0


def has_numpy() -> bool:
    """Whether NumPy is importable in this environment."""
    try:
        import numpy  # noqa: F401
    except ImportError:
        return False
    return True


def describe_capabilities() -> dict[str, Any]:
    """Audit the current environment's neighbor-search capabilities (P28-O)."""
    faiss = None
    try:
        import faiss as _faiss

        faiss = _faiss
    except ImportError:
        pass
    return {
        "numpy_available": has_numpy(),
        "faiss_available": faiss is not None,
        "faiss_version": str(getattr(faiss, "__version__", None)) if faiss else None,
        "faiss_gpu_count": faiss_gpu_count(),
        "faiss_has_standard_gpu_resources": bool(faiss and hasattr(faiss, "StandardGpuResources")),
        "faiss_has_index_cpu_to_gpu": bool(faiss and hasattr(faiss, "index_cpu_to_gpu")),
    }


def resolve_backend(choice: BackendChoice) -> NeighborBackend:
    """Resolve a backend choice, recording the decision in the instance.

    ``auto`` prefers faiss-gpu (when GPUs are visible) then faiss-cpu, then
    numpy, then pure python; explicit pins never silently downgrade.
    """
    caps = describe_capabilities()
    if choice == BACKEND_AUTO:
        if caps["faiss_gpu_count"] > 0:
            return FaissGpuBackend()
        if caps["faiss_available"]:
            return FaissCpuBackend()
        if caps["numpy_available"]:
            return NumpyBackend()
        return PythonBackend()
    if choice == BACKEND_FAISS_GPU:
        if caps["faiss_gpu_count"] < 1:
            raise RuntimeError(
                "backend 'faiss-gpu' pinned but no FAISS GPU is available "
                f"(capabilities: {caps}); refusing to fall back."
            )
        return FaissGpuBackend()
    if choice == BACKEND_FAISS_CPU:
        if not caps["faiss_available"]:
            raise RuntimeError("backend 'faiss-cpu' pinned but faiss is not installed.")
        return FaissCpuBackend()
    if choice == BACKEND_NUMPY:
        if not caps["numpy_available"]:
            raise RuntimeError("backend 'numpy' pinned but NumPy is not installed.")
        return NumpyBackend()
    if choice == BACKEND_PYTHON:
        return PythonBackend()
    raise ValueError(
        f"unknown backend {choice!r}; want one of auto/python/numpy/faiss-cpu/faiss-gpu"
    )


def backend_metadata(backend: NeighborBackend) -> dict[str, Any]:
    """Performance metadata recording the resolved backend (P28-P/AA)."""
    caps = describe_capabilities()
    return {
        "backend": backend.name,
        "faiss_version": caps["faiss_version"],
        "gpu_count": caps["faiss_gpu_count"],
        "numpy_available": caps["numpy_available"],
    }
