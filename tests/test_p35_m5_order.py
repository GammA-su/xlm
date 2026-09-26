"""P35 M5: canonical membership identity and independent order manifests (AUTHORED data).

Every shard here is written from authored text with the byte tokenizer. Nothing
is downloaded, prepared from a live source or trained.
"""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from p35_m5_support import (
    ORDER_SEED_A,
    ORDER_SEED_B,
    TEXTS,
    default_documents,
    make_doc,
    orders,
    write_source,
    write_sources,
)
from xlm.artifacts.manifest import identity_digest
from xlm.data.ordering import (
    MembershipError,
    OrderedSourceIndex,
    OrderManifestError,
    build_membership,
    build_order_manifest,
    check_against_membership,
    derive_order,
    header_with_id,
    independence_problems,
    load_order_manifest,
    require_independent_orders,
    sequence_digest,
    verify_order_header,
    verify_order_manifest,
    write_order_manifest,
)
from xlm.data.ordering.manifest import order_header
from xlm.data.tokens import TokenShardReader

REPO = Path(__file__).resolve().parents[1]


def _membership_id(readers: dict[str, TokenShardReader]) -> str:
    return build_membership(readers).membership_id


def _docs() -> dict[str, list[Any]]:
    return {s: default_documents(s) for s in TEXTS}


# ------------------------------------------------------------------- membership


def test_same_documents_in_another_physical_order_have_equal_membership(tmp_path: Path) -> None:
    native = write_sources(tmp_path / "native")
    reversed_ = write_sources(tmp_path / "reversed", reverse=True)
    for source in TEXTS:
        # The physical artifacts differ (order-dependent checksums) ...
        assert native[source].manifest.checksum_sha256 != reversed_[source].manifest.checksum_sha256
    # ... but the canonical membership does not.
    assert _membership_id(native) == _membership_id(reversed_)
    a, b = build_membership(native), build_membership(reversed_)
    assert a.summary() == b.summary()


@pytest.mark.parametrize(
    "mutation",
    ["missing", "extra", "content", "same_length_content", "lineage", "source"],
)
def test_membership_changes_with_any_document_change(tmp_path: Path, mutation: str) -> None:
    base = _membership_id(write_sources(tmp_path / "base"))
    docs = _docs()
    if mutation == "missing":
        docs["alpha"].pop(2)
    elif mutation == "extra":
        docs["beta"].append(make_doc("beta_extra", "y = 1\n", "beta"))
    elif mutation == "content":
        docs["alpha"][1] = make_doc("alpha_1", "Completely different text. ", "alpha")
    elif mutation == "same_length_content":
        # Same doc id, same byte/token count: only the content differs.
        text = TEXTS["alpha"][0]
        docs["alpha"][0] = make_doc("alpha_0", text.replace("lunar", "LUNAR"), "alpha")
    elif mutation == "lineage":
        docs["beta"][0] = make_doc("beta_0", TEXTS["beta"][0], "beta", lineage="other_cluster")
    elif mutation == "source":
        moved = docs["alpha"].pop(3)
        docs["beta"].append(make_doc(moved.doc_id, moved.text, "beta"))
    changed = _membership_id(write_sources(tmp_path / "changed", docs))
    assert changed != base


def test_membership_is_not_a_count_or_byte_summary(tmp_path: Path) -> None:
    docs = _docs()
    text = TEXTS["alpha"][0]
    docs["alpha"][0] = make_doc("alpha_0", text.replace("lunar", "LUNAR"), "alpha")
    a = build_membership(write_sources(tmp_path / "a"))
    b = build_membership(write_sources(tmp_path / "b", docs))
    for source in TEXTS:
        sa, sb = a.sources[source].summary(), b.sources[source].summary()
        assert (sa["document_count"], sa["token_count"], sa["byte_count"]) == (
            sb["document_count"],
            sb["token_count"],
            sb["byte_count"],
        )
    assert a.membership_id != b.membership_id


def test_split_firewall_refuses_heldout_documents(tmp_path: Path) -> None:
    docs = _docs()
    docs["beta"][1] = make_doc("beta_1", TEXTS["beta"][1], "beta", split="diagnostic_val")
    readers = write_sources(tmp_path, docs)
    with pytest.raises(MembershipError) as info:
        build_membership(readers)
    assert info.value.code == "membership_split_violation"
    # The stream index refuses a non-train shard too, whatever order is supplied.
    with pytest.raises(OrderManifestError, match="non-train"):
        OrderedSourceIndex(readers["beta"], [d.doc_id for d in docs["beta"]])


def test_changed_split_cannot_masquerade_as_the_same_membership(tmp_path: Path) -> None:
    good = write_sources(tmp_path / "good")
    order_a, _ = orders(good)
    docs = _docs()
    docs["alpha"][4] = make_doc("alpha_4", TEXTS["alpha"][4], "alpha", split="audit")
    leaky = write_sources(tmp_path / "leaky", docs)
    with pytest.raises(MembershipError):
        build_membership(leaky)
    # A hand-made order that claims a held-out id for training is refused as well.
    forged = copy.deepcopy(order_a)
    forged["sources"]["alpha"]["ordered_doc_ids"][0] = "heldout_doc"
    with pytest.raises(OrderManifestError):
        verify_order_manifest(forged)


def test_duplicate_doc_ids_are_refused(tmp_path: Path) -> None:
    docs = _docs()
    docs["alpha"].append(make_doc("alpha_0", "duplicate id, other text. ", "alpha"))
    with pytest.raises(MembershipError) as info:
        build_membership(write_sources(tmp_path, docs))
    assert info.value.code == "membership_duplicate_document"


def test_membership_is_bound_to_verified_shard_bytes(tmp_path: Path) -> None:
    readers = write_sources(tmp_path)
    path = readers["alpha"].directory / "tokens.bin"
    raw = bytearray(path.read_bytes())
    raw[5] ^= 0x01
    path.write_bytes(bytes(raw))
    with pytest.raises(ValueError, match="checksum mismatch"):
        build_membership(readers)


# ------------------------------------------------------------------------ order


def test_same_seed_reproduces_the_identical_order_and_hash(tmp_path: Path) -> None:
    membership = build_membership(write_sources(tmp_path))
    first = build_order_manifest(membership, order_seed=ORDER_SEED_A)
    second = build_order_manifest(membership, order_seed=ORDER_SEED_A)
    assert first == second
    assert verify_order_manifest(first) == first["order_manifest_id"]


def test_same_seed_orders_are_independent_of_physical_shard_order(tmp_path: Path) -> None:
    native = build_order_manifest(
        build_membership(write_sources(tmp_path / "n")), order_seed=ORDER_SEED_A
    )
    reverse = build_order_manifest(
        build_membership(write_sources(tmp_path / "r", reverse=True)), order_seed=ORDER_SEED_A
    )
    for source in TEXTS:
        assert (
            native["sources"][source]["ordered_doc_ids"]
            == reverse["sources"][source]["ordered_doc_ids"]
        )
    # The artifact identity is part of the manifest, so the ids still differ.
    assert native["order_manifest_id"] != reverse["order_manifest_id"]


def test_different_seeds_give_genuinely_different_orders(tmp_path: Path) -> None:
    readers = write_sources(tmp_path)
    order_a, order_b = orders(readers)
    assert order_a["canonical_membership_id"] == order_b["canonical_membership_id"]
    assert order_a["order_manifest_id"] != order_b["order_manifest_id"]
    for source in TEXTS:
        seq_a = order_a["sources"][source]["ordered_doc_ids"]
        seq_b = order_b["sources"][source]["ordered_doc_ids"]
        assert sorted(seq_a) == sorted(seq_b) and seq_a != seq_b
        assert (
            order_a["sources"][source]["ordered_doc_ids_digest"]
            != order_b["sources"][source]["ordered_doc_ids_digest"]
        )
    assert independence_problems(order_a, order_b) == []
    assert independence_problems(header_with_id(order_a), header_with_id(order_b)) == []
    require_independent_orders(order_a, order_b)


def test_order_is_a_seeded_permutation_not_the_physical_order(tmp_path: Path) -> None:
    readers = write_sources(tmp_path)
    order_a, _ = orders(readers)
    native = [d.doc_id for d in default_documents("alpha")]
    assert order_a["sources"]["alpha"]["ordered_doc_ids"] != native


def test_same_sequence_under_a_different_seed_label_is_not_independent(tmp_path: Path) -> None:
    order_a, _ = orders(write_sources(tmp_path))
    relabelled = copy.deepcopy(order_a)
    relabelled["derivation"]["order_seed"] = ORDER_SEED_B
    relabelled["order_manifest_id"] = identity_digest(order_header(relabelled))
    # The full manifest is refused: its sequence is not its seed's derivation.
    with pytest.raises(OrderManifestError, match="not the declared seeded derivation"):
        verify_order_manifest(relabelled)
    # Its header verifies by digest, but the pair is not independent evidence.
    header = header_with_id(relabelled)
    assert verify_order_header(header) == relabelled["order_manifest_id"]
    problems = independence_problems(header_with_id(order_a), header)
    assert any("identical document sequence" in p for p in problems)
    with pytest.raises(OrderManifestError) as info:
        require_independent_orders(header_with_id(order_a), header)
    assert info.value.code == "orders_not_independent"


def test_tiny_membership_seed_collision_is_rejected(tmp_path: Path) -> None:
    docs = {
        "alpha": [make_doc("alpha_0", "one. ", "alpha"), make_doc("alpha_1", "two. ", "alpha")],
        "beta": [make_doc("beta_0", "x = 1\n", "beta"), make_doc("beta_1", "y = 2\n", "beta")],
    }
    membership = build_membership(write_sources(tmp_path, docs))
    by_sequence: dict[tuple[tuple[str, ...], ...], int] = {}
    collision = None
    for seed in range(64):
        manifest = build_order_manifest(membership, order_seed=seed)
        key = tuple(tuple(manifest["sources"][s]["ordered_doc_ids"]) for s in sorted(docs))
        if key in by_sequence:
            collision = (by_sequence[key], seed)
            break
        by_sequence[key] = seed
    assert collision is not None, "two docs per source: a collision within 64 seeds is certain"
    first = build_order_manifest(membership, order_seed=collision[0])
    second = build_order_manifest(membership, order_seed=collision[1])
    assert first["order_manifest_id"] != second["order_manifest_id"]
    problems = independence_problems(first, second)
    assert any("identical document sequence" in p for p in problems)


def test_orders_over_different_memberships_are_not_independent_orders(tmp_path: Path) -> None:
    docs = _docs()
    text = TEXTS["alpha"][0]
    docs["alpha"][0] = make_doc("alpha_0", text.replace("lunar", "LUNAR"), "alpha")
    a = build_order_manifest(build_membership(write_sources(tmp_path / "a")), order_seed=1)
    b = build_order_manifest(build_membership(write_sources(tmp_path / "b", docs)), order_seed=2)
    # Same counts per source; only one document's content differs.
    for source in TEXTS:
        assert (
            a["canonical_membership"]["sources"][source]["document_count"]
            == b["canonical_membership"]["sources"][source]["document_count"]
        )
    problems = independence_problems(a, b)
    assert any("different canonical memberships" in p for p in problems)


def test_manifest_hash_is_independent_of_json_key_order(tmp_path: Path) -> None:
    order_a, _ = orders(write_sources(tmp_path))

    def reverse_keys(value: Any) -> Any:
        if isinstance(value, dict):
            return {k: reverse_keys(value[k]) for k in reversed(list(value))}
        if isinstance(value, list):
            return [reverse_keys(v) for v in value]
        return value

    shuffled = reverse_keys(order_a)
    assert list(shuffled) != list(order_a)
    assert verify_order_manifest(shuffled) == order_a["order_manifest_id"]
    path = write_order_manifest(order_a, tmp_path / "order_a.json")
    loaded = load_order_manifest(path, expected_id=order_a["order_manifest_id"])
    assert loaded == order_a
    with pytest.raises(FileExistsError):
        write_order_manifest(order_a, path)


@pytest.mark.parametrize("tamper", ["swap", "swap_redigest", "summary", "seed", "id"])
def test_tampered_order_manifests_are_refused(tmp_path: Path, tamper: str) -> None:
    order_a, _ = orders(write_sources(tmp_path))
    forged = copy.deepcopy(order_a)
    sequence = forged["sources"]["alpha"]["ordered_doc_ids"]
    if tamper in ("swap", "swap_redigest"):
        sequence[0], sequence[1] = sequence[1], sequence[0]
        if tamper == "swap_redigest":
            forged["sources"]["alpha"]["ordered_doc_ids_digest"] = sequence_digest(sequence)
            forged["order_manifest_id"] = identity_digest(order_header(forged))
    elif tamper == "summary":
        forged["canonical_membership"]["sources"]["alpha"]["document_count"] += 1
    elif tamper == "seed":
        forged["derivation"]["order_seed"] += 1
    elif tamper == "id":
        forged["order_manifest_id"] = "0" * 64
    with pytest.raises(OrderManifestError):
        verify_order_manifest(forged)
    path = tmp_path / f"forged_{tamper}.json"
    path.write_text(json.dumps(forged), encoding="utf-8")
    with pytest.raises(OrderManifestError):
        load_order_manifest(path)
    # A genuine manifest pinned to another id is refused as well.
    genuine = write_order_manifest(order_a, tmp_path / f"genuine_{tamper}.json")
    with pytest.raises(OrderManifestError) as info:
        load_order_manifest(genuine, expected_id="f" * 64)
    assert info.value.code == "order_manifest_pin_mismatch"


def test_derivation_is_independent_of_python_hash_seed_and_process(tmp_path: Path) -> None:
    ids = [f"doc_{i}" for i in range(50)]
    expected = derive_order(ids, order_seed=7, membership_id="m" * 64, source_id="s")
    script = (
        "import json,sys\n"
        "from xlm.data.ordering import derive_order\n"
        "ids=[f'doc_{i}' for i in range(50)]\n"
        "print(json.dumps(derive_order(ids,order_seed=7,membership_id='m'*64,source_id='s')))\n"
    )
    for hash_seed in ("0", "12345"):
        env = {**os.environ, "PYTHONHASHSEED": hash_seed}
        out = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            env=env,
            check=True,
            timeout=120,
        )
        assert json.loads(out.stdout) == expected
    # Input enumeration order is irrelevant.
    assert derive_order(
        list(reversed(ids)), order_seed=7, membership_id="m" * 64, source_id="s"
    ) == (expected)


def test_manifest_must_order_exactly_the_bound_membership(tmp_path: Path) -> None:
    readers = write_sources(tmp_path / "a")
    order_a, _ = orders(readers)
    check_against_membership(order_a, build_membership(readers))
    docs = _docs()
    docs["beta"].pop()
    other = build_membership(write_sources(tmp_path / "b", docs))
    with pytest.raises(OrderManifestError) as info:
        check_against_membership(order_a, other)
    assert info.value.code == "order_membership_mismatch"


# ------------------------------------------------------------ document integrity


def test_whole_documents_keep_tokens_spans_and_metadata(tmp_path: Path) -> None:
    readers = write_sources(tmp_path)
    order_a, _ = orders(readers)
    for source, reader in readers.items():
        before = (
            reader.manifest.checksum_sha256,
            reader.manifest.offsets_checksum_sha256,
            (reader.directory / "tokens.bin").read_bytes(),
        )
        ordered = order_a["sources"][source]["ordered_doc_ids"]
        index = OrderedSourceIndex(reader, ordered)
        physical = {r["doc_id"]: r for r in reader.read_document_offsets()}
        fetch = reader.read_tokens_mmap
        concatenated: list[int] = []
        position = 0
        for doc_id in ordered:
            original = physical[doc_id]
            count = original["token_count"]
            tokens = index.read(fetch, position, count)
            assert tokens == reader.read_tokens(original["token_start"], count)
            record = index.document_at(position)
            assert record["doc_id"] == doc_id
            assert record["token_start"] == position
            assert record["physical_token_start"] == original["token_start"]
            unchanged = {k: v for k, v in record.items() if k not in ("token_start",)}
            unchanged.pop("physical_token_start")
            assert unchanged == {k: v for k, v in original.items() if k != "token_start"}
            # Every position inside the document maps to the same record.
            assert index.document_at(position + count - 1)["doc_id"] == doc_id
            concatenated += tokens
            position += count
        # The ordered stream is exactly the documents, whole, in manifest order.
        assert index.read(fetch, 0, index.total_tokens) == concatenated
        assert sorted(concatenated) == sorted(reader.read_tokens())
        after = (
            reader.manifest.checksum_sha256,
            reader.manifest.offsets_checksum_sha256,
            (reader.directory / "tokens.bin").read_bytes(),
        )
        assert before == after


def test_ordered_index_refuses_a_non_permutation(tmp_path: Path) -> None:
    reader = write_source(tmp_path / "alpha", "alpha", default_documents("alpha"))
    ids = [d.doc_id for d in default_documents("alpha")]
    for bad in (ids[:-1], [*ids, "alpha_x"], [*ids[:-1], ids[0]], [*ids[:-1], "ghost"]):
        with pytest.raises(OrderManifestError):
            OrderedSourceIndex(reader, bad)
