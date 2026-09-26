"""Authored fixtures for P35 M5 document-order tests (SYNTHETIC, offline).

Two kinds of fixture:

- real token shards written from authored text with the byte tokenizer, used
  for membership, derivation, stream, producer and resume-identity tests;
- an authored in-memory membership (synthetic member digests), used where only
  order *headers* matter (M4 comparison/promotion tests).
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from xlm.core.contracts import CanonicalDocument
from xlm.data.normalization import compute_sha256
from xlm.data.ordering import (
    CanonicalMembership,
    SourceMembership,
    build_membership,
    build_order_manifest,
)
from xlm.data.ordering.membership import membership_digest
from xlm.data.sampling import MixtureBatcher, MixtureComponent, MixtureRecipe
from xlm.data.sampling.mixture import ExhaustionPolicy
from xlm.data.tokens import TokenShardReader, TokenShardWriter
from xlm.tokenizers.byte import ByteTokenizer

#: Authored documents: two sources, unequal lengths, distinct text per document.
TEXTS: dict[str, list[str]] = {
    "alpha": [
        "Tidal patterns shift with lunar declination. ",
        "Salt marsh grasses trap fine sediment on each flood tide and build the marsh. ",
        "Estuaries mix fresh and salt water. ",
        "Longshore drift moves sand along the beach face, grain by grain, for decades. ",
        "Spring tides follow full and new moons. ",
        "Barrier islands migrate landward during storms. ",
    ],
    "beta": [
        "def total(values):\n    return sum(values)\n",
        "for i in range(3):\n    print(i)\n",
        "class Box:\n    def __init__(self, v):\n        self.v = v\n",
        "x = [n * n for n in range(10) if n % 2]\n",
        "import math\nprint(math.sqrt(16))\n",
    ],
}
ORDER_SEED_A = 35_051
ORDER_SEED_B = 35_052
CONTEXT = 16
GLOBAL = 48


def h(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


def make_doc(
    doc_id: str,
    text: str,
    source_id: str,
    *,
    split: str = "train",
    lineage: str | None = None,
) -> CanonicalDocument:
    raw = text.encode("utf-8")
    return CanonicalDocument(
        doc_id=doc_id,
        source_id=source_id,
        source_revision="rev_1",
        source_file="authored.jsonl",
        source_row=0,
        raw_hash=compute_sha256(raw),
        clean_hash=compute_sha256(raw),
        text=text,
        utf8_byte_count=len(raw),
        language="en",
        language_confidence=1.0,
        document_kind="prose",
        source_metadata={},
        parent_ids=[],
        license_reference="cc-by-4.0",
        transform_log=[],
        quality_reasons=[],
        cluster_ids={"duplicate_cluster": lineage or f"lin_{doc_id}", "split_group": f"g_{doc_id}"},
        split=split,
    )


def write_source(
    directory: Path,
    source_id: str,
    documents: list[CanonicalDocument],
    *,
    shard_id: str | None = None,
) -> TokenShardReader:
    TokenShardWriter(
        directory, shard_id or f"shard_{source_id}", source_id, ByteTokenizer(), pool_hash="m5"
    ).write_documents(documents, add_special_tokens=True)
    return TokenShardReader(directory)


def default_documents(source_id: str) -> list[CanonicalDocument]:
    return [make_doc(f"{source_id}_{i}", t, source_id) for i, t in enumerate(TEXTS[source_id])]


def write_sources(
    root: Path,
    documents: dict[str, list[CanonicalDocument]] | None = None,
    *,
    reverse: bool = False,
) -> dict[str, TokenShardReader]:
    """One shard per source; ``reverse`` writes the same documents in reverse order."""
    docs = documents or {s: default_documents(s) for s in TEXTS}
    readers = {}
    for source_id, items in docs.items():
        ordered = list(reversed(items)) if reverse else list(items)
        readers[source_id] = write_source(root / source_id, source_id, ordered)
    return readers


def orders(readers: dict[str, TokenShardReader]) -> tuple[dict[str, Any], dict[str, Any]]:
    membership = build_membership(readers)
    return (
        build_order_manifest(membership, order_seed=ORDER_SEED_A),
        build_order_manifest(membership, order_seed=ORDER_SEED_B),
    )


def recipe(*, repeat: bool = True) -> MixtureRecipe:
    return MixtureRecipe(
        mixture_id="m5_authored",
        components=[
            MixtureComponent(source_id="alpha", weight=0.6),
            MixtureComponent(source_id="beta", weight=0.4),
        ],
        exhaustion=ExhaustionPolicy(repeat=repeat, max_epochs=16 if repeat else 1),
    )


def batcher(
    readers: dict[str, TokenShardReader],
    order: dict[str, Any] | None,
    *,
    repeat: bool = True,
    **kwargs: Any,
) -> MixtureBatcher:
    tokenizer = ByteTokenizer()
    return MixtureBatcher(
        recipe(repeat=repeat),
        dict(readers),
        context_length=kwargs.pop("context_length", CONTEXT),
        global_batch_valid_targets=kwargs.pop("global_batch_valid_targets", GLOBAL),
        microbatch_sequences=2,
        pad_token_id=tokenizer.pad_token_id,
        bos_token_id=tokenizer.bos_token_id,
        eos_token_id=tokenizer.eos_token_id,
        document_order=order,
        **kwargs,
    )


def target_doc_sequence(batches: list[Any]) -> list[str]:
    """Distinct consecutive doc ids of every kept target, in stream order."""
    sequence: list[str] = []
    for batch in batches:
        for row_docs, row_mask in zip(
            batch.metadata["target_doc_ids"], batch.loss_mask, strict=True
        ):
            for doc_id, flag in zip(row_docs, row_mask, strict=True):
                if flag and doc_id and (not sequence or sequence[-1] != doc_id):
                    sequence.append(doc_id)
    return sequence


def synthetic_membership(counts: dict[str, int] | None = None) -> CanonicalMembership:
    """An authored membership with SYNTHETIC member digests (headers-only tests)."""
    sources = {}
    for source_id, count in sorted((counts or {"alpha": 40, "beta": 30}).items()):
        doc_ids = tuple(f"{source_id}_{i:03d}" for i in range(count))
        digests = tuple(h(f"member:{source_id}:{d}") for d in doc_ids)
        sources[source_id] = SourceMembership(
            source_id=source_id,
            shard={
                "shard_id": f"shard_{source_id}",
                "source_id": source_id,
                "checksum_sha256": h(f"tokens:{source_id}"),
                "offsets_checksum_sha256": h(f"offsets:{source_id}"),
                "num_tokens": 100 * count,
                "num_documents": count,
                "token_dtype": "uint16",
                "tokenizer_hash": h("tokenizer"),
            },
            doc_ids=doc_ids,
            member_digests=digests,
            token_count=100 * count,
            byte_count=99 * count,
            membership_digest=membership_digest(list(zip(doc_ids, digests, strict=True))),
        )
    return CanonicalMembership(tokenizer_hash=h("tokenizer"), sources=sources)


def synthetic_orders(
    counts: dict[str, int] | None = None, seeds: tuple[int, int] = (ORDER_SEED_A, ORDER_SEED_B)
) -> tuple[dict[str, Any], dict[str, Any]]:
    membership = synthetic_membership(counts)
    return (
        build_order_manifest(membership, order_seed=seeds[0]),
        build_order_manifest(membership, order_seed=seeds[1]),
    )
