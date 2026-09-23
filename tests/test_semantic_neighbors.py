"""P28 semantic-neighbor lane: offline fixtures only, no models, no FAISS required.

Vector math backends needing NumPy/FAISS are skip-gated; the pure-Python
backend, artifact contracts, determinism, and CLI surface run everywhere.
FAISS is candidate generation only, off by default, and never affects
lexical dedup survivors.
"""

from __future__ import annotations

import hashlib
import json
import math
import struct
from pathlib import Path

import pytest
from typer.testing import CliRunner

from xlm.cli.data_cmd import app as data_app
from xlm.data.dedup.engine import DedupConfig
from xlm.data.dedup.sharded import run_sharded_dedup
from xlm.data.semantic.backends import (
    PythonBackend,
    describe_capabilities,
    resolve_backend,
)
from xlm.data.semantic.embeddings import (
    EMBEDDING_MANIFEST_FILENAME,
    EmbeddingManifest,
    EmbeddingShardRef,
    load_embedding_manifest,
    validate_embedding_artifact,
)
from xlm.data.semantic.neighbors import (
    SEMANTIC_POLICY_NONE,
    SEMANTIC_POLICY_V0_EXPERIMENTAL,
    generate_candidates,
    summarize_thresholds,
)
from xlm.data.semantic.providers import SyntheticEmbeddingProvider, TextHashEmbeddingProvider


def _rows(provider: object, texts: list[str]) -> list[list[float]]:
    vectors = provider.encode(texts)  # type: ignore[union-attr]
    if isinstance(vectors, list):
        return [[float(v) for v in row] for row in vectors]
    return [[float(v) for v in row] for row in vectors.tolist()]


def test_providers_are_deterministic_and_normalized() -> None:
    texts = ["first document", "second document here", "first document"]

    def fresh(kind: str) -> object:
        if kind == "synthetic":
            return SyntheticEmbeddingProvider(dim=32, seed=7)
        return TextHashEmbeddingProvider(dim=32)

    for kind in ("synthetic", "texthash"):
        first = _rows(fresh(kind), texts)
        second = _rows(fresh(kind), texts)
        assert first == second
        for row in first:
            assert math.isclose(sum(v * v for v in row), 1.0, rel_tol=1e-5)
    hashed = TextHashEmbeddingProvider(dim=32)
    assert _rows(hashed, texts)[0] == _rows(hashed, texts)[2]
    assert _rows(hashed, texts)[0] != _rows(hashed, texts)[1]
    assert "synthetic" in SyntheticEmbeddingProvider(dim=32).model_identity
    assert "texthash" in TextHashEmbeddingProvider(dim=32).model_identity


def test_synthetic_provider_streams_and_realigns() -> None:
    texts = ["doc a", "doc b"]
    first_instance = SyntheticEmbeddingProvider(dim=16, seed=7)
    batch_one = _rows(first_instance, texts)
    batch_two = _rows(first_instance, texts)
    assert batch_one != batch_two, "stream must advance between calls"
    fresh_instance = SyntheticEmbeddingProvider(dim=16, seed=7)
    assert _rows(fresh_instance, texts) == batch_one, "fresh instances restart the stream"


def test_python_backend_contract() -> None:
    backend = PythonBackend()
    # Ordinals 0..3; 0 and 1 share an identical direction (tie), 2 is
    # orthogonal-ish, 3 is opposite.
    vectors = [
        [1.0, 0.0, 0.0],
        [1.0, 0.0, 0.0],
        [0.0, 1.0, 0.0],
        [-1.0, 0.0, 0.0],
    ]
    backend.build(vectors, [0, 1, 2, 3])
    ids, scores = backend.search([[1.0, 0.0, 0.0]], [0], top_k=3)
    # Self (ordinal 0) excluded; identical twin first; tie broken by ordinal.
    assert ids[0][0] == 1 and scores[0][0] == pytest.approx(1.0)
    assert set(ids[0]) == {1, 2, 3}
    with pytest.raises(ValueError, match="top_k"):
        backend.search([[1.0, 0.0, 0.0]], [0], top_k=0)
    with pytest.raises(ValueError, match="vectors for"):
        backend.build([[1.0]], [0, 1])


def test_tie_cutoff_resolves_by_ordinal() -> None:
    # Six identical directions: any top_k < 5 cuts inside a tie run, so the
    # smallest ordinals (excluding self) must win deterministically.
    backend = PythonBackend()
    vectors = [[1.0, 0.0]] * 6
    backend.build(vectors, [0, 1, 2, 3, 4, 5])
    ids, scores = backend.search([[1.0, 0.0]], [0], top_k=3)
    assert ids[0] == [1, 2, 3]
    assert scores[0] == pytest.approx([1.0, 1.0, 1.0])
    ids, _ = backend.search([[1.0, 0.0]], [4], top_k=4)
    assert ids[0] == [0, 1, 2, 3]


def test_candidate_contract_and_determinism(tmp_path: Path) -> None:
    backend = PythonBackend()
    vectors = [
        [1.0, 0.0],
        [0.99, 0.141],
        [0.0, 1.0],
        [0.98, 0.199],
    ]
    ordinals = [0, 1, 2, 3]
    first = tmp_path / "c1.jsonl"
    artifact, telemetry = generate_candidates(
        backend=backend,
        vectors=vectors,
        ordinals=ordinals,
        top_k=3,
        threshold=0.9,
        output_path=first,
    )
    assert artifact.candidate_count > 0
    assert telemetry["candidate_count"] == artifact.candidate_count
    records = [json.loads(line) for line in first.read_text(encoding="utf-8").splitlines()]
    assert records
    for record in records:
        assert record["doc_a"] < record["doc_b"], "canonical pair ordering"
        assert record["score"] >= 0.9, "threshold applied"
    pairs = [(r["doc_a"], r["doc_b"]) for r in records]
    assert len(set(pairs)) == len(pairs), "duplicate pair suppression"
    assert all(a != b for a, b in pairs), "no self-pairs"
    ordered = sorted(records, key=lambda r: (-r["score"], r["doc_a"], r["doc_b"]))
    assert records == ordered, "deterministic emission order"
    second = tmp_path / "c2.jsonl"
    artifact2, _ = generate_candidates(
        backend=PythonBackend(),
        vectors=vectors,
        ordinals=ordinals,
        top_k=3,
        threshold=0.9,
        output_path=second,
    )
    assert first.read_bytes() == second.read_bytes()
    assert artifact2.identity() == artifact.identity()
    assert (
        artifact2.identity()
        != generate_candidates(
            backend=PythonBackend(),
            vectors=vectors,
            ordinals=ordinals,
            top_k=3,
            threshold=0.95,
            output_path=tmp_path / "c3.jsonl",
        )[0].identity()
    )
    with pytest.raises(ValueError, match="top_k"):
        generate_candidates(
            backend=PythonBackend(),
            vectors=vectors,
            ordinals=ordinals,
            top_k=0,
            threshold=0.9,
            output_path=tmp_path / "x.jsonl",
        )


def _write_npy(path: Path, rows: list[list[float]], dtype: str = "<f4") -> None:
    """Minimal stdlib .npy writer (test-only; production uses NumPy)."""
    n, d = len(rows), len(rows[0])
    header = f"{{'descr': '{dtype}', 'fortran_order': False, 'shape': ({n}, {d}), }}".encode(
        "latin1"
    )
    header += b" " * (64 - (10 + len(header)) % 64) + b"\n"
    with path.open("wb") as stream:
        stream.write(b"\x93NUMPY\x01\x00")
        stream.write(struct.pack("<H", len(header)))
        stream.write(header)
        for row in rows:
            stream.write(struct.pack(f"<{d}f", *row))


def _write_embedding_artifact(
    directory: Path, doc_ids: list[str], shards_rows: list[list[list[float]]]
) -> EmbeddingManifest:
    directory.mkdir(parents=True, exist_ok=True)
    manifest = EmbeddingManifest(
        source_artifact={"input_kind": "test", "total_documents": len(doc_ids)},
        model_identity="test-model:v1",
        dim=len(shards_rows[0][0]),
        dtype="float32",
        normalized=True,
        truncation_policy="test:v1",
        doc_ids=list(doc_ids),
    )
    total_bytes = 0
    for index, rows in enumerate(shards_rows):
        path = directory / f"vectors-{index:05d}.npy"
        _write_npy(path, rows)
        payload = path.read_bytes()
        total_bytes += len(payload)
        manifest.shards.append(
            EmbeddingShardRef(
                path=path.name,
                rows=len(rows),
                dim=manifest.dim,
                dtype="float32",
                sha256=hashlib.sha256(payload).hexdigest(),
            )
        )
    manifest.total_vectors = len(doc_ids)
    manifest.vector_bytes = total_bytes
    (directory / EMBEDDING_MANIFEST_FILENAME).write_text(
        json.dumps(manifest.to_dict(), indent=2), encoding="utf-8"
    )
    return manifest


def test_embedding_manifest_roundtrip_and_validation(tmp_path: Path) -> None:
    vectors = [[1.0, 0.0], [0.0, 1.0], [0.6, 0.8]]
    manifest = _write_embedding_artifact(tmp_path / "emb", ["a", "b", "c"], [vectors])
    loaded = load_embedding_manifest(tmp_path / "emb" / EMBEDDING_MANIFEST_FILENAME)
    assert loaded.to_dict() == manifest.to_dict()
    assert validate_embedding_artifact(tmp_path / "emb") == manifest
    assert (
        validate_embedding_artifact(
            tmp_path / "emb", expected_source={"input_kind": "test", "total_documents": 3}
        )
        == manifest
    )
    with pytest.raises(ValueError, match="source mismatch"):
        validate_embedding_artifact(tmp_path / "emb", expected_source={"input_kind": "other"})
    # Tampered bytes fail closed.
    shard = tmp_path / "emb" / "vectors-00000.npy"
    shard.write_bytes(shard.read_bytes() + b"\x00")
    with pytest.raises(ValueError, match="hash mismatch|rows"):
        validate_embedding_artifact(tmp_path / "emb")


def test_embedding_validation_structural_failures(tmp_path: Path) -> None:
    manifest = _write_embedding_artifact(tmp_path / "emb", ["a", "b"], [[[1.0, 0.0]] * 2])
    doc = manifest.to_dict()
    doc["schema_version"] = 999
    (tmp_path / "emb" / EMBEDDING_MANIFEST_FILENAME).write_text(json.dumps(doc))
    with pytest.raises(ValueError, match="version"):
        validate_embedding_artifact(tmp_path / "emb")
    doc["schema_version"] = 1
    doc["doc_ids"] = ["a", "a"]
    (tmp_path / "emb" / EMBEDDING_MANIFEST_FILENAME).write_text(json.dumps(doc))
    with pytest.raises(ValueError, match="unique"):
        validate_embedding_artifact(tmp_path / "emb")
    with pytest.raises(FileNotFoundError, match="not found"):
        load_embedding_manifest(tmp_path / "missing.json")


def test_capabilities_and_backend_resolution() -> None:
    caps = describe_capabilities()
    assert caps["faiss_available"] is False
    assert caps["faiss_gpu_count"] == 0
    assert caps["faiss_has_standard_gpu_resources"] is False
    assert caps["faiss_has_index_cpu_to_gpu"] is False
    # auto resolves to the best available here (numpy if present else python).
    backend = resolve_backend("auto")
    assert backend.name in ("numpy", "python")
    assert resolve_backend("python").name == "python"
    with pytest.raises(RuntimeError, match="faiss-gpu.*refusing"):
        resolve_backend("faiss-gpu")
    with pytest.raises(RuntimeError, match="faiss.*not installed"):
        resolve_backend("faiss-cpu")
    with pytest.raises(ValueError, match="unknown backend"):
        resolve_backend("bogus")
    if caps["numpy_available"]:
        assert resolve_backend("numpy").name == "numpy"
    else:
        with pytest.raises(RuntimeError, match="NumPy.*not installed"):
            resolve_backend("numpy")


def test_numpy_backend_matches_python_backend() -> None:
    numpy = pytest.importorskip("numpy")
    assert numpy is not None
    from xlm.data.semantic.backends import NumpyBackend

    rng_vectors = (numpy.random.RandomState(11).randn(60, 16)).astype(numpy.float32)
    rng_vectors /= numpy.linalg.norm(rng_vectors, axis=1, keepdims=True)
    ordinals = list(range(60))
    python = PythonBackend()
    python.build(rng_vectors.tolist(), ordinals)
    expected_ids, expected_scores = python.search(rng_vectors[:10].tolist(), ordinals[:10], 5)
    staged = NumpyBackend()
    staged.build(rng_vectors, ordinals)
    got_ids, got_scores = staged.search(rng_vectors[:10], ordinals[:10], 5)
    assert got_ids == expected_ids
    for expected_row, got_row in zip(expected_scores, got_scores, strict=True):
        assert got_row == pytest.approx(expected_row, abs=1e-6)
    # Tie cutoff: identical vectors cut inside the tie run must resolve by
    # ordinal exactly like the exhaustive Python backend.
    tied = numpy.tile(numpy.array([1.0] + [0.0] * 15, dtype=numpy.float32), (8, 1))
    tied_ordinals = list(range(100, 108))
    python.build(tied.tolist(), tied_ordinals)
    staged.build(tied, tied_ordinals)
    expected_tie, _ = python.search([tied[0].tolist()], [100], 5)
    got_tie, _ = staged.search(tied[:1], [100], 5)
    assert got_tie == expected_tie == [[101, 102, 103, 104, 105]]


def test_threshold_sweep_structure(tmp_path: Path) -> None:
    backend = PythonBackend()
    vectors = [[1.0, 0.0], [0.995, 0.0999], [0.0, 1.0], [0.99, 0.141], [0.5, 0.866]]
    out = tmp_path / "candidates.jsonl"
    generate_candidates(
        backend=backend,
        vectors=vectors,
        ordinals=[0, 1, 2, 3, 4],
        top_k=4,
        threshold=0.5,
        output_path=out,
    )
    analysis = summarize_thresholds(out, thresholds=[0.90, 0.95, 0.99])
    assert analysis["candidate_count"] > 0
    assert set(analysis["score_quantiles"]) == {"p50", "p90", "p99"}
    assert set(analysis["threshold_sweep"]) == {"0.90", "0.95", "0.99"}
    for _point, row in analysis["threshold_sweep"].items():
        assert row["candidate_pairs"] >= 0 and row["components"] >= 0


def test_semantic_cli_sidecar_only(tmp_path: Path) -> None:
    vectors = [[1.0, 0.0], [0.99, 0.141], [0.0, 1.0], [0.5, 0.866]]
    _write_embedding_artifact(tmp_path / "emb", ["d0", "d1", "d2", "d3"], [vectors])
    runner = CliRunner()
    valid = runner.invoke(data_app, ["embeddings-validate", "--dir", str(tmp_path / "emb")])
    assert valid.exit_code == 0, valid.output
    neighbors = runner.invoke(
        data_app,
        [
            "semantic-neighbors",
            "--embeddings",
            str(tmp_path / "emb"),
            "--output-dir",
            str(tmp_path / "sem"),
            "--backend",
            "python",
            "--top-k",
            "2",
            "--threshold",
            "0.9",
        ],
    )
    assert neighbors.exit_code == 0, neighbors.output
    assert "sidecar only" in neighbors.output
    report = json.loads((tmp_path / "sem" / "candidates-report.json").read_text(encoding="utf-8"))
    assert report["candidate_artifact"]["policy"] == SEMANTIC_POLICY_NONE
    assert report["experimental_clusters"] is None
    assert report["backend"]["backend"] == "python"
    experimental = runner.invoke(
        data_app,
        [
            "semantic-neighbors",
            "--embeddings",
            str(tmp_path / "emb"),
            "--output-dir",
            str(tmp_path / "semexp"),
            "--backend",
            "python",
            "--top-k",
            "2",
            "--threshold",
            "0.9",
            "--semantic-cluster-policy",
            SEMANTIC_POLICY_V0_EXPERIMENTAL,
        ],
    )
    assert experimental.exit_code == 0, experimental.output
    exp_report = json.loads(
        (tmp_path / "semexp" / "candidates-report.json").read_text(encoding="utf-8")
    )
    assert exp_report["experimental_clusters"]["policy"] == SEMANTIC_POLICY_V0_EXPERIMENTAL
    # Same candidates: the policy flag adds analysis, never changes pairs.
    assert (tmp_path / "semexp" / "candidates.jsonl").read_bytes() == (
        tmp_path / "sem" / "candidates.jsonl"
    ).read_bytes()
    # Pinned GPU fails loudly here; unknown policy is refused.
    gpu = runner.invoke(
        data_app,
        [
            "semantic-neighbors",
            "--embeddings",
            str(tmp_path / "emb"),
            "--output-dir",
            str(tmp_path / "semgpu"),
            "--backend",
            "faiss-gpu",
        ],
    )
    assert gpu.exit_code == 1
    bad_policy = runner.invoke(
        data_app,
        [
            "semantic-neighbors",
            "--embeddings",
            str(tmp_path / "emb"),
            "--output-dir",
            str(tmp_path / "sembad"),
            "--semantic-cluster-policy",
            "v9",
        ],
    )
    assert bad_policy.exit_code == 1


def test_semantic_off_by_default_for_dedup(tmp_path: Path) -> None:
    lines = []
    for i in range(20):
        text = f"unrelated semantic fixture prose number {i} with filler words"
        lines.append(
            json.dumps(
                {
                    "doc_id": f"sdoc-{i}",
                    "source_id": "s",
                    "source_revision": "r",
                    "source_file": "f.jsonl",
                    "source_row": i,
                    "raw_hash": "00" * 32,
                    "clean_hash": "11" * 32,
                    "text": text,
                    "utf8_byte_count": len(text.encode()),
                    "language": "en",
                    "language_confidence": 1.0,
                    "document_kind": "prose",
                    "source_metadata": {},
                    "parent_ids": [],
                    "license_reference": "cc-by-4.0",
                    "transform_log": [],
                    "quality_reasons": [],
                    "cluster_ids": {},
                    "split": "train",
                },
            )
        )
    selected = tmp_path / "sem_off.jsonl"
    selected.write_text("\n".join(lines) + "\n", encoding="utf-8")
    before, _, _, _, _ = run_sharded_dedup(
        input_path=selected,
        output_dir=tmp_path / "before",
        config=DedupConfig(),
        workers=1,
        input_shard_bytes=65536,
        output_shard_bytes=None,
        work_dir=tmp_path / "w1",
        max_input_bytes=2 * 1024**3,
    )
    # A semantic sidecar run in between cannot affect the lexical result.
    _write_embedding_artifact(
        tmp_path / "emb2",
        [f"sdoc-{i}" for i in range(20)],
        [[[1.0 if j == i % 4 else 0.0 for j in range(4)]] for i in range(20)],
    )
    runner = CliRunner()
    sidecar = runner.invoke(
        data_app,
        [
            "semantic-neighbors",
            "--embeddings",
            str(tmp_path / "emb2"),
            "--output-dir",
            str(tmp_path / "side"),
            "--backend",
            "python",
            "--top-k",
            "3",
            "--threshold",
            "0.5",
        ],
    )
    assert sidecar.exit_code == 0, sidecar.output
    after, _, _, _, _ = run_sharded_dedup(
        input_path=selected,
        output_dir=tmp_path / "after",
        config=DedupConfig(),
        workers=1,
        input_shard_bytes=65536,
        output_shard_bytes=None,
        work_dir=tmp_path / "w2",
        max_input_bytes=2 * 1024**3,
    )
    assert after.to_dict() == before.to_dict()


def test_embeddings_build_needs_numpy_and_validates_flags(tmp_path: Path) -> None:
    from xlm.data.semantic.backends import has_numpy

    runner = CliRunner()
    (tmp_path / "empty.jsonl").write_text("", encoding="utf-8")
    result = runner.invoke(
        data_app,
        [
            "embeddings-build",
            "--input",
            str(tmp_path / "empty.jsonl"),
            "--output-dir",
            str(tmp_path / "eb"),
        ],
    )
    if has_numpy():
        assert result.exit_code == 0, result.output
        assert (tmp_path / "eb" / EMBEDDING_MANIFEST_FILENAME).is_file()
    else:
        # NumPy is absent in the locked test env: the command must fail
        # clearly, never half-publish an artifact.
        assert result.exit_code == 1
        assert "NumPy" in result.output
        assert not (tmp_path / "eb" / EMBEDDING_MANIFEST_FILENAME).exists()
    bad_dtype = runner.invoke(
        data_app,
        [
            "embeddings-build",
            "--input",
            str(tmp_path / "empty.jsonl"),
            "--output-dir",
            str(tmp_path / "eb2"),
            "--dtype",
            "int8",
        ],
    )
    assert bad_dtype.exit_code == 1
