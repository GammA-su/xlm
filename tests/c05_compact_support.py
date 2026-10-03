"""Authored-only helpers comparing the compact C05 engine with the SQLite reference.

Every corpus here is synthetic (deterministic words, authored metadata); nothing
reads protected material, the network or production state.
"""

from __future__ import annotations

import hashlib
import random
import sqlite3
from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np

from test_c05_engine import KEY, PROMPT, TRUST, document, small_resources
from xlm.core.contracts import CanonicalDocument
from xlm.data.evidence_v2 import canonical
from xlm.data.exclusion import reference
from xlm.data.exclusion.artifacts import ExecutionPlan, InputFile, authorize, signed
from xlm.data.exclusion.capacity import probe_geometry
from xlm.data.exclusion.factstore import read_header
from xlm.data.exclusion.policy import MatcherPolicy, ProductionPolicy, Resources
from xlm.data.exclusion.runner import file_sha, run
from xlm.data.exclusion.streaming import patterns, render

#: Completion fields that are scientific (operational timings/peaks/plan ids excluded).
SCIENTIFIC = (
    "documents",
    "kept",
    "excluded",
    "duplicates",
    "components",
    "allocations",
    "dedup_stats",
    "membership_sha256",
    "membership_bytes",
    "review",
    "policy_digest",
    "index_sha256",
    "input_manifest_digest",
    "benchmark_receipt_digest",
    "source_seals",
    "review_decisions",
    "mode",
    "kind",
)


def write_index(root: Path) -> Path:
    index = root / "authored-index.jsonl"
    index.write_bytes(
        b"".join(
            canonical.canonical_bytes({"tokens": p.tokens, "provenance": p.provenance}) + b"\n"
            for p in patterns(
                render("arc_easy", {"question": PROMPT, "choices": ["yes", "no"]}),
                "authored",
                MatcherPolicy(),
            )
        )
    )
    return index


def setup_files(
    root: Path,
    files: list[list[CanonicalDocument]],
    *,
    resources: Resources | None = None,
    policy: ProductionPolicy | None = None,
    upstream: dict[int, str] | None = None,
) -> tuple[ExecutionPlan, Path, dict[str, Any]]:
    """One plan file per list (several documents each), authored receipt and index."""
    data = root / "data"
    data.mkdir(parents=True)
    entries = []
    for n, docs in enumerate(files):
        path = data / f"{n:03d}.jsonl"
        path.write_bytes(b"".join(canonical.canonical_bytes(d.to_dict()) + b"\n" for d in docs))
        source = docs[0].source_id if docs else "authored"
        revision = docs[0].source_revision if docs else "revision"
        entries.append(
            InputFile(
                path=path.name,
                source_key=source,
                source_id=source,
                source_revision=revision,
                component=f"component-{source}",
                view="view",
                source_file=f"{n:03d}.jsonl",
                documents_sha256=file_sha(path),
                file_bytes=path.stat().st_size,
                canonical_bytes=sum(d.utf8_byte_count for d in docs),
                documents=len(docs),
                upstream_component=(upstream or {}).get(n),
            )
        )
    index = write_index(root)
    receipt = signed(
        {
            "index_sha256": file_sha(index),
            "index_bytes": index.stat().st_size,
            "isolation": {"mode": "authored"},
        },
        "fixture",
        KEY,
    )
    plan = ExecutionPlan(
        sequence=1,
        mode="authored",
        input_manifest_digest="1" * 64,
        source_seals={e.source_key: "2" * 64 for e in entries},
        files=tuple(entries),
        benchmark_receipt_digest=receipt["digest"],
        index_sha256=file_sha(index),
        policy=policy or ProductionPolicy(diagnostic_bytes=0, quick_bytes=0, audit_bytes=0),
        resources=resources or small_resources(),
        storage=probe_geometry(root / "scratch"),
        data_root=str(data),
        scratch_root=str(root / "scratch"),
        output_root=str(root / "output"),
        code_commit="3" * 40,
        code_identity="4" * 64,
        dependency_sha256="5" * 64,
    )
    return plan, index, receipt


def execute_with(
    engine: str, plan: ExecutionPlan, index: Path, receipt: dict[str, Any], **kwargs: Any
) -> dict[str, Any]:
    runner = reference.run if engine == "reference" else run
    result: dict[str, Any] = runner(
        plan,
        authorize(plan, "fixture", KEY),
        index=index,
        benchmark=receipt,
        trusted=TRUST,
        issuer="fixture",
        key=KEY,
        current_code="4" * 64,
        current_dependencies="5" * 64,
        **kwargs,
    )
    return result


def outcome(plan: ExecutionPlan, engine: str) -> dict[str, Any]:
    """Every scientific artifact of one finished run, engine-independent where defined."""
    work = Path(plan.scratch_root) / plan.identity()
    final = Path(plan.output_root) / plan.identity()
    completion = canonical.loads_bytes_strict((final / "completion.json").read_bytes())["payload"]
    membership = (final / "membership.jsonl").read_bytes()
    result: dict[str, Any] = {
        "membership": membership,
        "membership_sha256": hashlib.sha256(membership).hexdigest(),
        "decisions": (work / "decisions.jsonl").read_bytes(),
        "completion": {k: completion[k] for k in SCIENTIFIC},
    }
    if engine == "reference":
        db = sqlite3.connect(work / "facts.sqlite")
        try:
            facts = {
                path: canonical.loads_strict(attestation)["payload"]["facts"]
                for path, attestation in db.execute("SELECT path, attestation FROM files")
            }
            seal = canonical.loads_strict(
                db.execute("SELECT value FROM seals WHERE key='groups'").fetchone()[0]
            )["payload"]
            result["signatures"] = {
                doc: bytes(sig) for doc, sig in db.execute("SELECT id, signature FROM docs")
            }
            result["exact"] = dict(db.execute("SELECT id, exact FROM docs"))
            result["hits"] = dict(db.execute("SELECT id, hit FROM docs"))
            result["bands"] = _grouped(db.execute("SELECT id, key FROM bands"))
            result["lineage"] = _grouped(db.execute("SELECT id, key FROM lineage"))
            result["parents"] = _grouped(db.execute("SELECT child, parent FROM parent_edges"))
            result["roots"] = {
                doc: (dup, fam, survivor)
                for doc, dup, fam, survivor in db.execute(
                    "SELECT id, duplicate, family, survivor FROM docs"
                )
            }
        finally:
            db.close()
        result["facts"] = facts
        result["group_digest"] = seal["groups"]
    else:
        result.update(compact_facts(plan))
        seal = canonical.loads_bytes_strict((work / "seal.json").read_bytes())["payload"]
        result["group_digest"] = seal["groups"]
    return result


def _grouped(rows: Any) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for doc, value in rows:
        out.setdefault(doc, []).append(value)
    return {doc: sorted(values) for doc, values in out.items()}


def compact_facts(plan: ExecutionPlan) -> dict[str, Any]:
    from xlm.data.dedup.minhash import MinHasher
    from xlm.data.exclusion.factstore import load_unit

    work = Path(plan.scratch_root) / plan.identity()
    hasher = MinHasher(plan.policy.minhash())
    facts: dict[str, str] = {}
    signatures: dict[str, bytes] = {}
    exact: dict[str, str] = {}
    hits: dict[str, str | None] = {}
    bands: dict[str, list[str]] = {}
    lineage: dict[str, list[str]] = {}
    parents: dict[str, list[str]] = {}
    for ordinal, item in enumerate(plan.files):
        path = work / "facts" / f"{ordinal:05d}.unit"
        envelope, _start = read_header(path)
        facts[item.path] = envelope["payload"]["facts"]
        unit = load_unit(path, TRUST, {}, review=plan.policy.review.enabled)
        blob, offsets = unit.ids()
        ids = [
            bytes(blob[int(offsets[r]) : int(offsets[r + 1])]).decode() for r in range(unit.rows)
        ]
        records = unit.records()
        hit_rows = {int(h["row"]): bytes(h["pattern"]).hex() for h in unit.hits()}
        sigs = unit.signatures()
        for row, doc in enumerate(ids):
            signatures[doc] = np.asarray(sigs[row], dtype="<u8").tobytes()
            exact[doc] = bytes(records["exact"][row]).hex()
            hits[doc] = hit_rows.get(row)
            keys = hasher.band_keys(np.asarray(sigs[row]).tolist())
            bands[doc] = sorted(keys)
        for name, target in (("lineage", lineage), ("parents", parents)):
            sblob, soff, counts, _dig = unit.strings(name)
            position = 0
            for row, doc in enumerate(ids):
                values = [
                    bytes(sblob[int(soff[k]) : int(soff[k + 1])]).decode()
                    for k in range(position, position + int(counts[row]))
                ]
                position += int(counts[row])
                if values:
                    target[doc] = sorted(values)
        unit.close()
    return {
        "facts": facts,
        "signatures": signatures,
        "exact": exact,
        "hits": hits,
        "bands": bands,
        "lineage": lineage,
        "parents": parents,
    }


def assert_equivalent(reference_out: dict[str, Any], compact_out: dict[str, Any]) -> None:
    for name in (
        "membership",
        "membership_sha256",
        "decisions",
        "completion",
        "facts",
        "group_digest",
        "signatures",
        "exact",
        "hits",
        "bands",
        "lineage",
        "parents",
    ):
        assert compact_out[name] == reference_out[name], name


# --- authored corpora ------------------------------------------------------------------

WORDS = tuple(
    "amber basalt cobalt dune ember fjord granite harbor iris juniper kelp lantern meadow "
    "nickel orchid pebble quartz ridge saffron tundra umber violet willow xenon yarrow "
    "zephyr anvil bramble cinder delta naïve façade straße 東京 ﬁligree Ångström".split()
)


def words(rng: random.Random, count: int) -> str:
    return " ".join(rng.choice(WORDS) + rng.choice(["", ",", ".", "!", ""]) for _ in range(count))


def mixed_corpus(seed: int, files: int = 6, per_file: int = 12) -> list[list[CanonicalDocument]]:
    """Exact/near duplicates across files, hits, URL lineage, parents, unicode ids."""
    rng = random.Random(seed)
    base = [words(rng, rng.choice([0, 3, 4, 5, 6, 40, 120])) for _ in range(16)]
    out: list[list[CanonicalDocument]] = []
    serial = 0
    for _ in range(files):
        source = rng.choice(["alpha", "beta", "gamma"])
        docs: list[CanonicalDocument] = []
        for _ in range(per_file):
            serial += 1
            kind = rng.random()
            if kind < 0.25:
                text = rng.choice(base)
            elif kind < 0.5:
                tokens = rng.choice(base).split(" ")
                if tokens and rng.random() < 0.7:
                    tokens[rng.randrange(len(tokens))] = rng.choice(WORDS)
                text = " ".join(tokens)
            elif kind < 0.6:
                text = f"Wrapper {serial}. {PROMPT} End."
            else:
                text = words(rng, rng.choice([1, 8, 50, 200]))
            metadata: dict[str, Any] = {}
            if rng.random() < 0.3:
                metadata["url"] = f"https://www.example.invalid/p/{rng.randrange(6)}?utm_x=1"
            if rng.random() < 0.1:
                metadata["book_id"] = f"book-{rng.randrange(3)}"
            identity = rng.choice(["doc", "Dok", "dóc", "文"]) + f"-{seed}-{serial:05d}"
            doc = document(identity, text, source=source, **metadata)
            if rng.random() < 0.15 and serial > 3:
                parents = [
                    rng.choice(["doc", "Dok", "dóc", "文"])
                    + f"-{seed}-{rng.randrange(1, serial):05d}",
                    "",
                    "missing-parent",
                ]
                doc = replace(doc, parent_ids=parents)
            docs.append(doc)
        out.append(docs)
    return out


def near_cluster_corpus(
    seed: int, clusters: int = 6, size: int = 9
) -> list[list[CanonicalDocument]]:
    """Clusters of near-identical documents: dense LSH buckets for cap/oversize logic."""
    rng = random.Random(seed)
    docs: list[CanonicalDocument] = []
    for c in range(clusters):
        tokens = [rng.choice(WORDS) for _ in range(rng.choice([30, 60, 90]))]
        for m in range(size):
            variant = list(tokens)
            for _ in range(rng.choice([0, 0, 1, 1, 2, 4])):
                variant[rng.randrange(len(variant))] = rng.choice(WORDS)
            identity = f"c{c:02d}-{rng.randrange(10**6):06d}-{m}"
            docs.append(document(identity, " ".join(variant), source="s1"))
    rng.shuffle(docs)
    split = max(1, len(docs) // 4)
    return [docs[i : i + split] for i in range(0, len(docs), split)]
