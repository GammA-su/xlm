"""C05 contamination policy v2 (c05-production-v3): trigger floors, compile filter, lineage
scope and versioned contracts. Authored values only; no protected material or network.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from xlm.core.contracts import CanonicalDocument
from xlm.data.dedup.lineage import lineage_keys_v3, split_only_keys_v1
from xlm.data.dedup.matchview import match_tokens
from xlm.data.evidence_v2 import canonical
from xlm.data.exclusion import fitscan
from xlm.data.exclusion.artifacts import CONTRACT_V2, CONTRACT_V3, output_contract
from xlm.data.exclusion.compact import CompactExactMatcher, compile_index, prepare
from xlm.data.exclusion.policy import (
    C05Error,
    Informativeness,
    MatcherPolicy,
    MatcherPolicyV4,
    ProductionPolicy,
    ProductionPolicyV3,
    TriggerPolicy,
    production_policy,
    scoped,
)
from xlm.data.exclusion.runner import check_trigger_coverage

TRIGGER = TriggerPolicy(reviewed_items_without_active_trigger=821)


def toks(text: str) -> tuple[str, ...]:
    return tuple(text.split(" "))


# -- the frozen trigger contract ----------------------------------------------------------------


def test_trigger_floors_are_exactly_the_reviewed_policy() -> None:
    eight = Informativeness(tokens=8, characters=40, distinct=5)
    assert TRIGGER.prompt == TRIGGER.item_fallback == eight
    assert TRIGGER.answer == TRIGGER.combined == eight == MatcherPolicyV4().answer
    assert TRIGGER.sentence == MatcherPolicyV4().sentence
    assert TRIGGER.sentence == Informativeness(tokens=3, characters=12, distinct=3)
    assert TRIGGER.version == "c05-trigger-floors-v1"
    # Frozen digest of the production value (821 reviewed uncovered items).
    assert TRIGGER.identity() == canonical.digest(TRIGGER.model_dump(mode="json"))
    assert TriggerPolicy(reviewed_items_without_active_trigger=820).identity() != (
        TRIGGER.identity()
    )


def test_the_reviewed_count_has_no_default() -> None:
    with pytest.raises(ValueError):
        TriggerPolicy()  # type: ignore[call-arg]
    with pytest.raises(ValueError):
        ProductionPolicyV3()  # type: ignore[call-arg]


@pytest.mark.parametrize(
    ("kind", "tokens", "active"),
    [
        # prompt: 4-7 tokens alone never trigger; 8 / 40 / 5 does.
        ("prompt", "then the man begins", False),
        ("prompt", "alpha bravo charlie delta echo foxtrot golfing", False),  # 7 tokens
        ("prompt", "alpha bravo charlie delta echo foxtrot golfing hotel", True),  # 8
        ("prompt", "abcd efgh ijkl mnop qrst uvwx yzab cdef", False),  # 39 characters
        ("prompt", "abcd efgh ijkl mnop qrst uvwx yzab cdefg", True),  # 40 characters
        ("prompt", "alpha bravo charlie delta alpha bravo charlie delta", False),  # 4 distinct
        ("prompt", "alpha bravo charlie delta alpha bravo charlie echoes", True),  # 5 distinct
        ("prompt", "alpha bravo charlie delta a1 b2 c3 x9 alpha bravo", False),  # 1-letter
        # answer / combined keep the frozen 8 / 40 / 5 floor.
        ("answer", "alpha bravo charlie delta echo foxtrot golfing", False),
        ("answer", "alpha bravo charlie delta echo foxtrot golfing hotel", True),
        ("combined", "abcd efgh ijkl mnop qrst uvwx yzab cdef", False),
        ("combined", "abcd efgh ijkl mnop qrst uvwx yzab cdefg", True),
        # sentence keeps 3 / 12 / 3 (short BLiMP protection).
        ("sentence", "those owls sing", True),
        ("sentence", "owls sing", False),  # 2 tokens
        ("sentence", "abc de fghi", False),  # 11 characters
        ("sentence", "abc de fghij", True),  # 12 characters
        ("sentence", "abc abc defgh x", False),  # 2 distinct two-letter tokens
        # item fallback now needs 8 / 40 / 5 (was 4 / 16 / 3).
        ("item_fallback", "fasten a loose wooden shelf yes no", False),
        ("item_fallback", "alpha bravo charlie delta echo foxtrot golfing hotel", True),
    ],
)
def test_trigger_floor_boundaries(kind: str, tokens: str, active: bool) -> None:
    assert TRIGGER.active(toks(tokens), [kind]) is active


def test_any_provenance_kind_may_activate_a_pattern() -> None:
    short = toks("those clever owls sing")
    assert TRIGGER.active(short, ["prompt"]) is False
    assert TRIGGER.active(short, ["prompt", "sentence"]) is True
    with pytest.raises(C05Error):
        TRIGGER.active(short, ["label"])


def test_normalization_is_the_frozen_deterministic_match_view() -> None:
    text = "Then,  the MAN—begins! ＡＢＣ"
    first = match_tokens(text)
    assert first == ["then", "the", "man", "begins", "abc"]
    assert match_tokens(text) == first
    assert TRIGGER.active(tuple(first[:4]), ["prompt"]) is False


# -- compile-time filter ------------------------------------------------------------------------

LONG_PROMPT = "which property of a material describes how easily heat flows"
SHARED = "those clever owls sing"
LINES = [
    ("r1", "prompt", "then the man begins"),  # 4-token fragment: inactive
    ("r2", "prompt", LONG_PROMPT),  # active
    ("r3", "prompt", SHARED),  # inactive as a prompt ...
    ("r4", "sentence", SHARED),  # ... but the same tokens are an active sentence
    ("r5", "item_fallback", "fasten a loose wooden shelf yes no"),  # inactive fallback
    ("r6", "answer", "to paint the wooden shutters with a wide brush"),  # active
]


def index_file(tmp_path: Path) -> tuple[Path, str, int]:
    path = tmp_path / "index.jsonl"
    path.write_bytes(
        b"".join(
            canonical.canonical_bytes({"provenance": [f"{ref}:{kind}"], "tokens": list(toks(t))})
            + b"\n"
            for ref, kind, t in LINES
        )
    )
    raw = path.read_bytes()
    return path, hashlib.sha256(raw).hexdigest(), len(raw)


def compiled(tmp_path: Path, name: str, trigger: TriggerPolicy | None) -> tuple[Path, Any]:
    index, sha, size = index_file(tmp_path)
    directory = tmp_path / name
    directory.mkdir()
    manifest = compile_index(
        index,
        directory,
        index_sha256=sha,
        index_bytes=size,
        max_record=1 << 20,
        max_records=None,
        max_logical_nodes=None,
        trigger=trigger,
    )
    return directory, manifest


def test_compile_filter_keeps_only_active_patterns_and_counts_items(tmp_path: Path) -> None:
    trigger = TriggerPolicy(reviewed_items_without_active_trigger=2)
    directory, manifest = compiled(tmp_path, "filtered", trigger)
    assert manifest["trigger"] == {
        "policy_digest": trigger.identity(),
        "index_records": 6,
        "active_records": 4,
        "unique_patterns": 5,
        "active_patterns": 3,
        "benchmark_item_refs": 6,
        "active_items": 4,  # r1 and r5 are the only uncovered items
    }
    _, sha, size = index_file(tmp_path)
    with CompactExactMatcher(
        directory, index_sha256=sha, index_bytes=size, trigger=trigger.identity()
    ) as matcher:
        assert matcher.manifest["counts"]["unique_patterns"] == 3
        filler = ["zz"] * 5
        assert matcher.match([*filler, *toks("then the man begins"), *filler]) is None
        assert matcher.match([*filler, *toks(SHARED), *filler]) is not None
        assert matcher.match([*filler, *toks(LONG_PROMPT), *filler]) is not None
        assert matcher.match(list(toks("fasten a loose wooden shelf yes no"))) is None


def test_trigger_binding_is_verified_on_every_open(tmp_path: Path) -> None:
    trigger = TriggerPolicy(reviewed_items_without_active_trigger=2)
    filtered, _ = compiled(tmp_path, "filtered", trigger)
    legacy, manifest = compiled(tmp_path, "legacy", None)
    assert "trigger" not in manifest
    _, sha, size = index_file(tmp_path)
    for directory, expected in (
        (filtered, None),
        (filtered, TriggerPolicy(reviewed_items_without_active_trigger=3).identity()),
        (legacy, trigger.identity()),
    ):
        with pytest.raises(C05Error, match="trigger policy binding"):
            CompactExactMatcher(directory, index_sha256=sha, index_bytes=size, trigger=expected)
    with CompactExactMatcher(legacy, index_sha256=sha, index_bytes=size) as matcher:
        assert matcher.match(list(toks("then the man begins"))) is not None
    # prepare() reuses a published compile only under the same trigger binding.
    index, _, _ = index_file(tmp_path)
    with pytest.raises(C05Error):
        prepare(
            filtered,
            index,
            index_sha256=sha,
            index_bytes=size,
            max_record=1 << 20,
            max_records=None,
            max_logical_nodes=None,
        )


def coverage_plan(policy: ProductionPolicy) -> Any:
    return SimpleNamespace(policy=policy)


def test_run_refuses_unless_the_reviewed_coverage_matches() -> None:
    trigger = TriggerPolicy(reviewed_items_without_active_trigger=2)
    v3 = ProductionPolicyV3(trigger=trigger)
    section = {"policy_digest": trigger.identity(), "index_records": 6, "active_items": 4}
    receipt = {"items": 6, "patterns": 6}
    matcher = SimpleNamespace(manifest={"trigger": section})
    check_trigger_coverage(coverage_plan(v3), receipt, matcher)  # type: ignore[arg-type]
    for bad_receipt, bad_section, reason in (
        ({"items": 7, "patterns": 6}, section, "reviewed count"),
        ({"items": 6, "patterns": 7}, section, "every protected index record"),
        (receipt, {**section, "policy_digest": "0" * 64}, "trigger binding"),
        (receipt, None, "trigger binding"),
    ):
        stub = SimpleNamespace(manifest={"trigger": bad_section} if bad_section else {})
        with pytest.raises(C05Error, match=reason):
            check_trigger_coverage(coverage_plan(v3), bad_receipt, stub)  # type: ignore[arg-type]
    with pytest.raises(C05Error, match="unfiltered plan"):
        check_trigger_coverage(coverage_plan(ProductionPolicy()), receipt, matcher)  # type: ignore[arg-type]


# -- versioned policy / plan / membership contracts ---------------------------------------------


def test_policy_versions_are_explicit_and_digest_stable() -> None:
    legacy = ProductionPolicy()
    assert type(production_policy(legacy.model_dump(mode="json"))) is ProductionPolicy
    v3 = ProductionPolicyV3(trigger=TRIGGER)
    parsed = production_policy(v3.model_dump(mode="json"))
    assert type(parsed) is ProductionPolicyV3 and parsed.identity() == v3.identity()
    assert scoped(v3) and not scoped(legacy)
    assert output_contract(v3) == CONTRACT_V3 and output_contract(legacy) == CONTRACT_V2
    assert v3.lineage == "known-lineage-v3"  # the split-leakage family
    assert v3.exclusion_lineage == "query-seed-derivation-family-v1"
    with pytest.raises(ValueError):
        ProductionPolicyV3(trigger=TRIGGER, matcher=MatcherPolicy())  # type: ignore[arg-type]
    with pytest.raises(ValueError):  # a v3 value never validates as v2
        ProductionPolicy.model_validate(v3.model_dump(mode="json"))


def membership_row(**groups: str) -> bytes:
    row = {
        "doc_id": "d",
        "source_id": "s",
        "component": "c",
        "view": "v",
        "file": "f",
        "row": 1,
        "content": "0" * 64,
        "bytes": 1,
        "duplicate_group": "d",
        "decision": "kept",
        "split": "train",
        "quick": False,
        "upstream_component": None,
        **groups,
    }
    return canonical.canonical_bytes(row) + b"\n"


@pytest.mark.parametrize(
    ("contract", "groups"),
    [
        ("c05_membership_v2", {"split_group": "d", "exclusion_group": "d"}),
        ("c05_membership_v3", {"lineage_group": "d"}),
        ("c05_membership_v3", {"split_group": "d", "lineage_group": "d"}),
    ],
)
def test_membership_rows_must_match_their_contract(contract: str, groups: dict[str, str]) -> None:
    fitscan.init_worker(fitscan.MembershipTables({}, (), 0, 0, 1 << 20, "t", contract))
    with pytest.raises(C05Error, match="membership record schema"):
        fitscan.parse_membership_chunk(membership_row(**groups))


@pytest.mark.parametrize(
    ("contract", "groups"),
    [
        ("c05_membership_v2", {"lineage_group": "d"}),
        ("c05_membership_v3", {"split_group": "d", "exclusion_group": "d"}),
    ],
)
def test_membership_schema_passes_for_its_own_contract(
    contract: str, groups: dict[str, str]
) -> None:
    fitscan.init_worker(fitscan.MembershipTables({}, (), 0, 0, 1 << 20, "t", contract))
    # The schema check passes; the row then fails only on its (absent) plan file.
    with pytest.raises(C05Error, match="outside its frozen plan file"):
        fitscan.parse_membership_chunk(membership_row(**groups))


# -- lineage scope ------------------------------------------------------------------------------


def document(source: str, metadata: dict[str, Any]) -> CanonicalDocument:
    return CanonicalDocument(
        "doc-1", source, "rev", "generated", 1, "a", "a", "text", 4, "en", 1.0, "text",
        metadata, [], "authored", [], [], {}, "train",
    )  # fmt: skip


@pytest.mark.parametrize(
    ("source", "metadata", "expected"),
    [
        (
            "synth",
            {
                "query_seed_url": "https://en.wikipedia.org/wiki/A",
                "additional_seed_url": "https://en.wikipedia.org/wiki/B",
            },
            {"url://en.wikipedia.org/wiki/B"},
        ),  # fmt: skip
        (
            "synth",
            {
                "query_seed_url": "https://en.wikipedia.org/wiki/A",
                "additional_seed_url": "http://www.en.wikipedia.org/wiki/A/",
            },
            set(),
        ),  # fmt: skip
        ("synth", {"query_seed_url": "https://en.wikipedia.org/wiki/A"}, set()),
        ("ultrax", {"additional_seed_url": "https://en.wikipedia.org/wiki/B"}, set()),
    ],
)
def test_split_only_keys_are_exactly_the_additional_seed_contribution(
    source: str, metadata: dict[str, Any], expected: set[str]
) -> None:
    doc = document(source, metadata)
    assert split_only_keys_v1(doc) == frozenset(expected)
    assert split_only_keys_v1(doc) <= set(lineage_keys_v3(doc))


def test_production_trigger_digest_is_frozen() -> None:
    # c05-trigger-floors-v1 with the production review of 821 uncovered items.
    assert TRIGGER.identity() == (
        "946cec19cacbc890d97eb2d5ea42471fd00fb41d27afcdbefd21916184b3c7cc"
    )


def test_policy_value_derivation_changes_only_the_v2_fields(tmp_path: Path) -> None:
    from scripts.c05_policy_v2_value import main

    base = ProductionPolicy(matcher=MatcherPolicyV4(), seed=7).model_dump(mode="json")
    (tmp_path / "base.json").write_bytes(canonical.canonical_bytes(base))
    out, matcher = tmp_path / "policy-value.json", tmp_path / "matcher.json"
    args = ["--base", str(tmp_path / "base.json"), "--reviewed-items-without-active-trigger", "3"]
    assert main([*args, "--output", str(out), "--matcher-output", str(matcher)]) == 0
    derived = canonical.loads_bytes_strict(out.read_bytes())
    changed = {k for k in set(base) | set(derived) if base.get(k) != derived.get(k)}
    assert changed == {"version", "stage_order", "trigger", "exclusion_lineage"}
    assert canonical.loads_bytes_strict(matcher.read_bytes()) == base["matcher"]
    with pytest.raises(SystemExit):  # write-once
        main([*args, "--output", str(out), "--matcher-output", str(tmp_path / "m2.json")])
    legacy = tmp_path / "v3-matcher.json"
    legacy.write_bytes(canonical.canonical_bytes(ProductionPolicy().model_dump(mode="json")))
    with pytest.raises(SystemExit):  # the base must use the c05-matcher-v4 generation
        main(
            [
                *("--base", str(legacy), "--reviewed-items-without-active-trigger", "3"),
                *("--output", str(tmp_path / "x.json")),
                *("--matcher-output", str(tmp_path / "y.json")),
            ]
        )


def test_material_spec_isolation_helper_replaces_only_isolation(tmp_path: Path) -> None:
    from scripts.c05_material_spec_isolation import main

    files = [
        {
            "task": "piqa",
            "repository": "ybisk/piqa",
            "revision": "0" * 40,
            "config": "c",
            "split": "s",
            "path": "piqa.jsonl",
            "bytes": 10,
            "sha256": "1" * 64,
            "items": 1,
        }
    ]
    isolation = {
        "mode": "authored",
        "operator_principal": "fixture-operator",
        "denied_agent_principal": "fixture-agent",
        "attestation_sha256": "2" * 64,
        "access_controls_verified": True,
    }
    spec = {
        "files": files,
        "publisher_inventory_sha256": "3" * 64,
        "all_published_configs_splits_reviewed": True,
        "isolation": isolation,
    }
    (tmp_path / "spec.json").write_bytes(canonical.canonical_bytes(spec))
    proposal = {"proposal_only": True, "isolation": {**isolation, "attestation_sha256": "4" * 64}}
    (tmp_path / "describe.json").write_bytes(canonical.canonical_bytes(proposal))
    args = ["--spec", str(tmp_path / "spec.json"), "--describe", str(tmp_path / "describe.json")]
    assert main([*args, "--output", str(tmp_path / "new.json")]) == 0
    written = canonical.loads_bytes_strict((tmp_path / "new.json").read_bytes())
    assert written == {**spec, "isolation": proposal["isolation"]}
    with pytest.raises(SystemExit):
        main([*args, "--output", str(tmp_path / "new.json")])
    with pytest.raises(SystemExit):
        main([*args, "--output", str(tmp_path / "spec.json")])
