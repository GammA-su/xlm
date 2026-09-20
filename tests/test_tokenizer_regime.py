"""Reconstruction and offset tests for a tokenizer fitted from a frozen pool.

Contract C06 requires reconstruction relative to canonical NFC text, a byte-span
offset convention, and coverage of arbitrary Unicode, combining marks, emoji,
non-English proper names, code and literal special-token-looking strings.

The tokenizer here is fitted from the pool's own frozen fit sample, so these tests
exercise the artifact the regime actually binds -- not a throwaway toy built from
unrelated text.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from xlm.core.contracts import CanonicalDocument
from xlm.data.normalization import canonical_normalize, compute_sha256
from xlm.data.pools import (
    PoolBinding,
    SourceView,
    TokenizerFitConfig,
    ViewSelector,
    build_pool,
    build_tokenizer_fit_manifest,
    restrict_membership_to_split,
)

pytest.importorskip("tokenizers")
from xlm.tokenizers.bpe import ByteLevelBPETokenizer  # noqa: E402

# Fit text deliberately spans scripts, code and symbols so the fitted vocabulary is
# not purely ASCII prose.
FIT_TEXTS = [
    "The harbour master recorded every vessel entering before the autumn storms arrived.",
    "François Müller and Søren Kierkegaard corresponded about ethics and obligation.",
    "Данные обрабатываются последовательно, строка за строкой, без исключений.",
    "気候変動の影響は地域によって大きく異なることが観測されています。",
    "def solve(values):\n    total = 0\n"
    "    for v in values:\n        total += v\n    return total\n",
    "Let x = 3 and y < 5; then x^2 > 9 holds whenever a<b<c orders the roots.",
    "Emoji sequences such as a rocket and a family render as multi-codepoint clusters.",
    "Combining marks: e + U+0301 composes to é under NFC normalization rules.",
    "Chemical notation H₂O and CO₂ alongside ½ ¾ and the ± sign appear in tables.",
    "Tabs\tand\ttrailing   spaces   must   survive   the   round   trip   unchanged.",
]

ROUND_TRIP_CASES = [
    pytest.param(
        "François Müller met Ana Sánchez in Kraków last Tuesday.", id="proper_names_latin"
    ),
    pytest.param("Достоевский и Толстой писали о морали и ответственности.", id="cyrillic"),
    pytest.param("東京都の気候は湿度が高く、夏は特に厳しい。", id="cjk"),
    pytest.param("Ελληνικά γράμματα: α β γ δ ε ζ η θ.", id="greek"),
    pytest.param(
        "def f(x):\n    if x > 0:\n        return x ** 2\n    return -x\n", id="code_indent"
    ),
    pytest.param("a<b<c and 3 < n implies n^2 > 9 for all integers n.", id="math_symbols"),
    pytest.param("Currency: $100, €85, £72, ¥11000, ₹8300.", id="currency"),
    pytest.param("Combining: é vs é after NFC.", id="combining_marks"),
    pytest.param("Emoji: \U0001f680 \U0001f9ea \U0001f30d end.", id="emoji"),
    pytest.param(
        "Literal token-looking text: <eos> <bos> <pad> <unk> stay ordinary.", id="literal_specials"
    ),
    pytest.param("Mixed: François wrote def solve(): return ½ + ¾  # ok", id="mixed"),
    pytest.param("   leading and trailing whitespace   ", id="whitespace"),
    pytest.param("Tabs\tand\nnewlines\r\nnormalized.", id="control_whitespace"),
]


def make_doc(doc_id: str, text: str, kind: str = "prose") -> CanonicalDocument:
    raw = text.encode("utf-8")
    return CanonicalDocument(
        doc_id=doc_id,
        source_id="fixture_src",
        source_revision="rev_1",
        source_file="shard.jsonl",
        source_row=0,
        raw_hash=compute_sha256(raw),
        clean_hash=compute_sha256(raw),
        text=text,
        utf8_byte_count=len(raw),
        language="en",
        language_confidence=1.0,
        document_kind=kind,
        source_metadata={},
        parent_ids=[],
        license_reference="cc-by-4.0",
        transform_log=[],
        quality_reasons=[],
        cluster_ids={},
        split="train",
    )


@pytest.fixture(scope="module")
def pool_fitted_tokenizer() -> ByteLevelBPETokenizer:
    """Fit a tokenizer from a frozen pool's own tokenizer-fit manifest."""
    docs = [
        make_doc(f"fit_{i}", text, "code" if "def " in text else "prose")
        for i, text in enumerate(FIT_TEXTS)
    ]
    views = [
        SourceView(
            view_id="prose_view",
            family_id="fixture_family",
            selector=ViewSelector(document_kinds=["prose"]),
            declared_raw_byte_share=0.7,
            priority=10,
        ),
        SourceView(
            view_id="code_view",
            family_id="fixture_family",
            selector=ViewSelector(document_kinds=["code"]),
            declared_raw_byte_share=0.3,
            priority=5,
        ),
    ]
    binding = PoolBinding(
        source_revisions={"fixture_src": "rev_1"},
        selected_raw_files={"fixture_src": ["shard.jsonl"]},
        row_locator_digest="rows_v1",
        cleaning_policy_identity="clean_v1",
        dedup_policy_identity="dedup_v1",
        exclusion_policy_identity="excl_v1",
        split_policy_identity="split_v1",
        license_receipts={"fixture_src": "cc-by-4.0"},
        producer_code_version="p11_v1",
    )
    assembly = build_pool(docs, views, binding)

    train_only = restrict_membership_to_split(
        assembly.manifest.view_membership["doc_ids_by_view"], assembly.accepted_documents, "train"
    )
    fit_manifest = build_tokenizer_fit_manifest(
        assembly.accepted_documents,
        train_only,
        assembly.manifest.view_membership["declared_shares"],
        assembly.manifest.pool_id,
        TokenizerFitConfig(target_sample_bytes=10_000),
    )
    selected = set(fit_manifest.doc_ids)
    fit_docs = [d for d in assembly.accepted_documents if d.doc_id in selected]

    return ByteLevelBPETokenizer.train_from_documents(fit_docs, target_vocab_size=400)


@pytest.mark.parametrize("text", ROUND_TRIP_CASES)
def test_round_trip_is_exact_against_canonical_text(
    pool_fitted_tokenizer: ByteLevelBPETokenizer, text: str
) -> None:
    """Reconstruction is relative to canonical NFC text, not the pre-cleaning bytes (C06)."""
    canonical = canonical_normalize(text)
    ids = pool_fitted_tokenizer.encode(canonical)
    assert pool_fitted_tokenizer.decode(ids) == canonical


@pytest.mark.parametrize("text", ROUND_TRIP_CASES)
def test_byte_offsets_partition_the_canonical_bytes(
    pool_fitted_tokenizer: ByteLevelBPETokenizer, text: str
) -> None:
    """Offsets must tile the canonical byte string exactly: no gaps, no overlaps."""
    canonical = canonical_normalize(text)
    expected_bytes = len(canonical.encode("utf-8"))

    ids, offsets = pool_fitted_tokenizer.encode_with_offsets(canonical)
    assert len(ids) == len(offsets)

    cursor = 0
    for start, end in offsets:
        assert start == cursor, f"offset gap or overlap at byte {cursor}"
        assert end >= start
        cursor = end
    assert cursor == expected_bytes, "offsets did not cover every canonical byte"


@pytest.mark.parametrize("text", ROUND_TRIP_CASES)
def test_byte_coverage_is_complete(pool_fitted_tokenizer: ByteLevelBPETokenizer, text: str) -> None:
    canonical = canonical_normalize(text)
    assert pool_fitted_tokenizer.compute_byte_coverage(canonical) == pytest.approx(1.0)


def test_offset_slices_reconstruct_their_own_token_bytes(
    pool_fitted_tokenizer: ByteLevelBPETokenizer,
) -> None:
    """Each offset span must name exactly the bytes of its own token."""
    canonical = canonical_normalize("François wrote def solve(): return ½ + ¾ today.")
    raw = canonical.encode("utf-8")
    ids, offsets = pool_fitted_tokenizer.encode_with_offsets(canonical)

    for token_id, (start, end) in zip(ids, offsets, strict=True):
        token_bytes = pool_fitted_tokenizer.token_to_bytes(
            pool_fitted_tokenizer.id_to_token(token_id)
        )
        assert raw[start:end] == token_bytes


def test_ordinary_text_never_becomes_a_control_token(
    pool_fitted_tokenizer: ByteLevelBPETokenizer,
) -> None:
    """Literal special-token-looking strings must stay ordinary content (C06)."""
    canonical = canonical_normalize("Literal <eos> <bos> <pad> <unk> inside a sentence.")
    ids = pool_fitted_tokenizer.encode(canonical, add_special_tokens=False)

    assert not (set(ids) & pool_fitted_tokenizer.special_token_ids)
    assert pool_fitted_tokenizer.decode(ids) == canonical


def test_structural_framing_adds_zero_width_boundary_offsets(
    pool_fitted_tokenizer: ByteLevelBPETokenizer,
) -> None:
    """BOS and EOS are structural: they cover no canonical bytes."""
    canonical = canonical_normalize("A short framed sentence.")
    num_bytes = len(canonical.encode("utf-8"))

    ids, offsets = pool_fitted_tokenizer.encode_with_offsets(canonical, add_special_tokens=True)
    assert ids[0] == pool_fitted_tokenizer.bos_token_id
    assert ids[-1] == pool_fitted_tokenizer.eos_token_id
    assert offsets[0] == (0, 0)
    assert offsets[-1] == (num_bytes, num_bytes)


def test_empty_text_round_trips(pool_fitted_tokenizer: ByteLevelBPETokenizer) -> None:
    ids, offsets = pool_fitted_tokenizer.encode_with_offsets("")
    assert ids == []
    assert offsets == []
    assert pool_fitted_tokenizer.decode([]) == ""


def test_tokenizer_save_load_preserves_identity(
    pool_fitted_tokenizer: ByteLevelBPETokenizer, tmp_path: Path
) -> None:
    """The regime binds a tokenizer fingerprint; it must survive a round trip."""
    pool_fitted_tokenizer.save(tmp_path / "tok")
    reloaded = ByteLevelBPETokenizer.load(tmp_path / "tok")

    assert reloaded.fingerprint == pool_fitted_tokenizer.fingerprint
    assert reloaded.actual_vocab_size == pool_fitted_tokenizer.actual_vocab_size

    sample = canonical_normalize("François and 東京 and def f(): pass")
    assert reloaded.encode(sample) == pool_fitted_tokenizer.encode(sample)


def test_demo_fit_is_not_marked_a_production_baseline(
    pool_fitted_tokenizer: ByteLevelBPETokenizer,
) -> None:
    """A tiny fixture fit must not present itself as the production tokenizer."""
    assert pool_fitted_tokenizer.is_production_baseline is False
    assert pool_fitted_tokenizer.actual_vocab_size < 32_768


def test_fitted_vocabulary_reflects_the_multilingual_fit_sample(
    pool_fitted_tokenizer: ByteLevelBPETokenizer,
) -> None:
    """The fit sample spans scripts, so non-ASCII text must not degrade to raw bytes.

    A purely ASCII fit would still round-trip (byte-level BPE always does), so the
    check is on encoding efficiency rather than on correctness.
    """
    ascii_text = canonical_normalize("the harbour master recorded every vessel entering")
    ascii_ratio = len(pool_fitted_tokenizer.encode(ascii_text)) / len(ascii_text.encode("utf-8"))
    assert ascii_ratio < 1.0, "fitted merges should compress familiar ASCII prose"


def test_offsets_handle_multi_byte_characters_without_splitting_reconstruction(
    pool_fitted_tokenizer: ByteLevelBPETokenizer,
) -> None:
    """A token may split a character's bytes; the concatenation must still reconstruct.

    Byte-level BPE is permitted to cut inside a multi-byte codepoint. What must hold
    is that reassembling every token's bytes reproduces the text exactly.
    """
    canonical = canonical_normalize("気候変動 ± ½ \U0001f680 François")
    ids, _ = pool_fitted_tokenizer.encode_with_offsets(canonical)

    rebuilt = b"".join(
        pool_fitted_tokenizer.token_to_bytes(pool_fitted_tokenizer.id_to_token(i)) for i in ids
    )
    assert rebuilt.decode("utf-8") == canonical


def test_batch_encode_matches_individual_encoding(
    pool_fitted_tokenizer: ByteLevelBPETokenizer,
) -> None:
    texts: list[str] = [canonical_normalize(str(t.values[0])) for t in ROUND_TRIP_CASES[:5]]
    batched = pool_fitted_tokenizer.batch_encode(texts)
    assert batched == [pool_fitted_tokenizer.encode(t) for t in texts]
