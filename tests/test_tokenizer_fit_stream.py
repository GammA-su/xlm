"""Exact fit selection, framing, hashes and bounded document retention."""

from __future__ import annotations

import hashlib
import weakref
from dataclasses import replace
from pathlib import Path

import pytest

from test_performance_tokenization import document
from xlm.data.pools.tokenizer_fit import TokenizerFitConfig, build_tokenizer_fit_manifest
from xlm.tokenizers.bpe import ByteLevelBPETokenizer, _fit_text_stream


def test_fit_spool_exact_boundaries_hash_and_caps() -> None:
    docs = [document(text, i) for i, text in enumerate(["a\x00b\n", "\U0001f642\n\n", "last"])]
    for cap in (1, 2, 3):
        with _fit_text_stream(iter(docs), cap, 1000) as (texts, digest):
            assert list(texts) == [d.text for d in docs[:cap]]
            assert (
                digest
                == hashlib.sha256(
                    "\n".join(
                        f"{d.source_id}:{d.doc_id}:{d.clean_hash}" for d in docs[:cap]
                    ).encode()
                ).hexdigest()
            )
    with _fit_text_stream(iter(docs), 100, docs[0].utf8_byte_count) as (texts, _):
        assert list(texts) == [docs[0].text]


def test_split_validation_still_precedes_document_cap() -> None:
    docs = [document("a", 0), document("b", 1)]
    docs[1] = replace(docs[1], split="diagnostic_val")
    with pytest.raises(ValueError, match="not 'train'"):
        with _fit_text_stream(iter(docs), 1, 100):
            pytest.fail("must validate the first over-cap document as before")


def test_fit_does_not_retain_documents(tmp_path: Path) -> None:
    refs = []

    def documents():
        for i in range(100):
            assert sum(r() is not None for r in refs) <= 2
            doc = document(f"unique passage {i} " * 100, i)
            refs.append(weakref.ref(doc))
            yield doc

    first = ByteLevelBPETokenizer.train_from_documents(documents(), target_vocab_size=300)
    first.save(tmp_path / "first")
    second = ByteLevelBPETokenizer.train_from_documents(
        [document(f"unique passage {i} " * 100, i) for i in range(100)], target_vocab_size=300
    )
    second.save(tmp_path / "second")
    for name in ("tokenizer.json", "tokenizer_manifest.json"):
        assert (tmp_path / "first" / name).read_bytes() == (tmp_path / "second" / name).read_bytes()


def test_fit_manifest_projects_metadata_without_changing_membership() -> None:
    refs = []

    def documents():
        for i in range(100):
            assert sum(r() is not None for r in refs) <= 2
            doc = document("large text " * 100, i)
            refs.append(weakref.ref(doc))
            yield doc

    members = {"view": [str(i) for i in range(100)]}
    config = TokenizerFitConfig(target_sample_bytes=10000)
    actual = build_tokenizer_fit_manifest(documents(), members, {"view": 1.0}, "pool", config)
    expected = build_tokenizer_fit_manifest(
        [document("large text " * 100, i) for i in range(100)],
        members,
        {"view": 1.0},
        "pool",
        config,
    )
    assert actual.to_dict() == expected.to_dict()
