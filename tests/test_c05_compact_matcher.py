"""Compact exact C05 matcher: authored synthetic fixtures only, never benchmark text.

Every equivalence test instantiates both the historical ``StreamingMatcher`` and the
compact backend over the same frozen pattern set and requires identical results.
"""

from __future__ import annotations

import gc
import getpass
import itertools
import json
import random
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from typing import Any

import pytest

from test_c05_engine import PROMPT, document, execute, membership, setup_run, small_resources
from xlm.data.dedup.matchview import match_tokens
from xlm.data.evidence_v2 import canonical
from xlm.data.exclusion import compact
from xlm.data.exclusion.artifacts import authorize
from xlm.data.exclusion.capacity import FILE_SLACK_BYTES, admit_plan, storage_bounds
from xlm.data.exclusion.compact import CeilingExceeded, CompactExactMatcher, prepare
from xlm.data.exclusion.isolation import VolumeIdentity, checkout_root, init_protected_root
from xlm.data.exclusion.policy import C05Error, MatcherPolicy, MatcherPolicyV4, Resources
from xlm.data.exclusion.runner import file_sha, matcher_directory, resume_check
from xlm.data.exclusion.streaming import (
    Pattern,
    StreamingMatcher,
    item_patterns,
    patterns,
    render,
)

FULL = compact.FULL_MASK


def write_index(path: Path, entries: Iterable[Pattern]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        b"".join(
            canonical.canonical_bytes({"tokens": list(p.tokens), "provenance": list(p.provenance)})
            + b"\n"
            for p in entries
        )
    )
    return path


Build = Callable[..., CompactExactMatcher]


@pytest.fixture
def build(tmp_path: Path) -> Iterator[Build]:
    """Compile authored patterns into a temporary directory; closed at teardown."""
    opened: list[CompactExactMatcher] = []
    counter = itertools.count()

    def make(entries: Iterable[Pattern], **options: Any) -> CompactExactMatcher:
        root = tmp_path / f"case-{next(counter)}"
        index = write_index(root / "index.jsonl", entries)
        matcher = prepare(
            root / "scratch" / compact.MATCHER_DIR,
            index,
            index_sha256=file_sha(index),
            index_bytes=index.stat().st_size,
            max_record=1024 * 1024,
            max_records=options.pop("max_records", None),
            max_logical_nodes=options.pop("max_logical_nodes", None),
            **options,
        )
        opened.append(matcher)
        return matcher

    yield make
    for matcher in opened:
        matcher.close()


def historical(entries: list[Pattern]) -> StreamingMatcher:
    return StreamingMatcher(entries, max_patterns=10**9, max_nodes=10**9)


def p(text: str, ref: str = "ref") -> Pattern:
    return Pattern(tuple(text.split()), (ref,))


def assert_equivalent(
    old: StreamingMatcher, new: CompactExactMatcher, documents: Iterable[list[str]]
) -> int:
    hits = 0
    for tokens in documents:
        expected = old.match(tokens)
        assert new.match(tokens) == expected
        assert new.match(iter(tokens)) == expected  # Any iterable, as historically.
        hits += expected is not None
    assert new.manifest["counts"]["logical_trie_nodes"] == old.nodes
    return hits


CASES: dict[str, tuple[list[str], list[str]]] = {
    "start_middle_end": (
        ["quartz lantern glows softly tonight", "amber kettle hums"],
        [
            "quartz lantern glows softly tonight and more",
            "before quartz lantern glows softly tonight after",
            "words then amber kettle hums",
            "quartz lantern glows softly",
            "",
            "amber",
        ],
    ),
    "overlapping": (
        ["a b c d e f", "c d e f g h", "e f g"],
        ["x a b c d e f g h y", "c d e f g", "a b c d e f", "b c d e f g h"],
    ),
    "prefix_of_another": (
        ["red fox jumps", "red fox jumps over lazy hounds", "fox jumps over lazy"],
        ["red fox jumps over lazy hounds", "the red fox jumps", "fox jumps over lazy hounds"],
    ),
    "repeated_tokens": (
        ["la la la la la la", "la la ti", "ti ti ti ti ti ti ti"],
        ["la la la la la la la la", "la la la la la ti", "ti ti ti ti ti ti", "la ti ti la"],
    ),
    "short_patterns": (
        ["solo", "duo pair", "tri ple set", "four token long one"],
        ["solo", "a solo b", "duo", "duo pair", "tri ple", "tri ple set", "four token long"],
    ),
    "long_beyond_q": (
        [" ".join(f"w{i}" for i in range(40)), " ".join(f"w{i}" for i in range(3, 23))],
        [
            " ".join(f"w{i}" for i in range(45)),
            " ".join(f"w{i}" for i in range(3, 22)),
            " ".join(f"w{i}" for i in range(2, 30)),
        ],
    ),
    "shared_prefixes": (
        [" ".join(["shared"] * 10 + [f"tail{i}", "end"]) for i in range(60)],
        [" ".join(["shared"] * 12 + [f"tail{i}", "end"]) for i in range(0, 80, 7)]
        + [" ".join(["shared"] * 10 + ["tail3"])],
    ),
}


@pytest.mark.parametrize("case", sorted(CASES))
def test_authored_cases_match_historical_automaton(build: Build, case: str) -> None:
    pattern_texts, documents = CASES[case]
    entries = [p(text, f"ref{i}") for i, text in enumerate(pattern_texts)]
    hits = assert_equivalent(
        historical(entries),
        build(entries),
        [d.split() for d in documents] + [["unknown", *d.split(), "unknown"] for d in documents],
    )
    assert hits > 0


def test_unknown_tokens_never_equal_benchmark_tokens(build: Build) -> None:
    entries = [p("alpha beta gamma"), p("delta")]
    old, new = historical(entries), build(entries)
    assert new.vocabulary.get("") is None and min(new.vocabulary.values()) == 1
    for tokens in (["alphA", "beta", "gamma"], ["alpha", "beta", "gamma2"], ["deltas"], []):
        assert old.match(tokens) is None and new.match(tokens) is None
    assert new.match(["zz", "alpha", "beta", "gamma"]) == old.match(
        ["zz", "alpha", "beta", "gamma"]
    )


def test_normalization_and_rendered_benchmark_patterns(build: Build) -> None:
    rows = [
        render("arc_easy", {"question": PROMPT, "choices": ["yes", "no"]}),
        render(
            "hellaswag",
            {
                "ctx": "An artisan assembles a patterned cabinet.",
                "endings": [
                    "Secure the wooden bracket with three copper bolts beneath the upper shelf",
                    "yes",
                ],
                "activity_label": "Woodwork",
            },
        ),
    ]
    entries = [e for n, row in enumerate(rows) for e in patterns(row, f"r{n}", MatcherPolicy())]
    texts = [
        "An introduction. WHY do copper bridges expand during summer! Closing notes.",
        "Why, do copper-bridges expand during summer?",
        "woodwork: An artisan assembles a patterned cabinet. Secure the wooden bracket",
        "Prefix secure THE wooden bracket with three copper bolts beneath the upper shelf.",
        "yes no yes no",
    ]
    assert (
        assert_equivalent(historical(entries), build(entries), [match_tokens(t) for t in texts])
        >= 3
    )


def test_fallback_generated_patterns(build: Build) -> None:
    rows: list[tuple[str, dict[str, Any]]] = [
        ("piqa", {"goal": "Tie knot", "sol1": "Loop rope twice", "sol2": "Pull ends"}),
        ("arc_easy", {"question": "Ice melts?", "choices": ["Heat it", "Cool it"]}),
    ]
    entries = []
    for n, (task, row) in enumerate(rows):
        _, found, _, fallback = item_patterns(task, row, f"item{n}", MatcherPolicyV4())
        assert fallback and len(found) == 1
        entries.extend(found)
    old, new = historical(entries), build(entries)
    docs = [
        match_tokens("Notes: tie knot; loop rope twice. Pull ends!"),
        match_tokens("Ice melts? Heat it. Cool it."),
        match_tokens("tie knot loop rope twice"),
    ]
    assert assert_equivalent(old, new, docs) == 2


def test_duplicate_provenance_shares_one_physical_pattern(tmp_path: Path, build: Build) -> None:
    single = build([p("copper bridges expand during summer", "one")])
    repeated = build(
        [p("copper bridges expand during summer", f"ref{i}") for i in range(50)]
        + [p("copper bridges expand during summer", "one")]
    )
    assert repeated.manifest["counts"]["index_records"] == 51
    assert repeated.manifest["counts"]["unique_patterns"] == 1
    assert single.manifest["files"] == repeated.manifest["files"]
    old = historical([p("copper bridges expand during summer", f"ref{i}") for i in range(50)])
    assert old.provenance  # Provenance stays a historical-matcher concept only.
    doc = "why copper bridges expand during summer".split()
    assert repeated.match(doc) == single.match(doc) == old.match(doc)


@pytest.mark.parametrize("seed", range(12))
def test_randomized_equivalence_and_logical_node_count(build: Build, seed: int) -> None:
    rng = random.Random(seed)
    size = rng.choice([2, 3, 6, 40])
    alphabet = [f"t{i}" for i in range(size)]
    entries = []
    for i in range(rng.randint(1, 120)):
        length = rng.choice([1, 2, 3, 4, 5, 6, 7, 9, 13, 17, 25])
        entries.append(Pattern(tuple(rng.choice(alphabet) for _ in range(length)), (f"r{i}",)))
    entries += rng.sample(entries, len(entries) // 4)
    documents = []
    for _ in range(150):
        tokens = [rng.choice([*alphabet, "unk"]) for _ in range(rng.randint(0, 50))]
        if rng.random() < 0.6:
            at = rng.randint(0, len(tokens))
            tokens[at:at] = list(rng.choice(entries).tokens)
        documents.append(tokens)
    q = rng.choice(sorted(compact.SUPPORTED_Q))
    assert_equivalent(historical(entries), build(entries, q=q, max_bucket=10**6), documents)


def test_ties_beyond_sort_columns_are_ordered_and_deduplicated_exactly(build: Build) -> None:
    rng = random.Random(7)
    stem = [f"s{i}" for i in range(compact.SORT_COLUMNS + 2)]
    entries = [
        Pattern(tuple(stem + [rng.choice("abc") for _ in range(rng.randint(0, 6))]), (f"r{i}",))
        for i in range(80)
    ] + [Pattern(tuple(stem[: compact.SORT_COLUMNS]), ("short",))]
    old, new = historical(entries), build(entries)
    assert new.manifest["counts"]["unique_patterns"] == len({e.tokens for e in entries})
    docs = [list(e.tokens) + ["x"] for e in entries] + [stem + list("abcabc")]
    assert_equivalent(old, new, docs)


def test_forced_fingerprint_collision_is_resolved_by_exact_comparison(build: Build) -> None:
    entries = [p("alpha beta gamma"), p("delta epsilon zeta"), p("one two three four five six")]
    old = historical(entries)
    # Mask 0: every anchor and full-pattern fingerprint collides with every other.
    new = build(entries, hash_mask=0)
    assert new.manifest["anchor_statistics"]["anchor_keys"] == 2  # one per anchor length
    for tokens in (
        "alpha beta delta".split(),
        "delta epsilon gamma".split(),
        "one two three four five seven".split(),
        "zeta epsilon delta".split(),
    ):
        assert new.match(tokens) is None and old.match(tokens) is None
    for tokens in ("x alpha beta gamma".split(), "one two three four five six".split()):
        assert new.match(tokens) == old.match(tokens) is not None
    # Production refuses a test-masked artifact.
    with pytest.raises(C05Error, match="fingerprint"):
        CompactExactMatcher(
            new.directory,
            index_sha256=new.manifest["source_index"]["sha256"],
            index_bytes=new.manifest["source_index"]["bytes"],
        )


def test_large_buckets_use_bounded_batches_and_stay_exact(build: Build) -> None:
    rng = random.Random(3)
    alphabet = [f"v{i}" for i in range(30)]
    entries = [
        Pattern(tuple(rng.choice(alphabet) for _ in range(rng.randint(1, 12))), (f"r{i}",))
        for i in range(3000)
    ]
    old = historical(entries)
    new = build(entries, hash_mask=0x3, max_bucket=10**6)
    assert new.manifest["anchor_statistics"]["max_bucket"] > compact.CANDIDATE_BATCH // 100
    documents = [[rng.choice(alphabet) for _ in range(120)] for _ in range(25)]
    assert_equivalent(old, new, documents)


def test_pathological_common_anchor_bucket_fails_closed(tmp_path: Path, build: Build) -> None:
    # Two-letter alphabet: every length-10 sequence; only 16 distinct 4-grams exist.
    entries = [
        Pattern(tuple(seq), (f"r{n}",)) for n, seq in enumerate(itertools.product("ab", repeat=10))
    ]
    with pytest.raises(CeilingExceeded) as refused:
        build(entries, q=4, max_bucket=32)
    assert refused.value.ceiling == "anchor_bucket"
    details = refused.value.details
    assert details["max_bucket"] > 32 and details["unique_patterns"] == 1024
    assert all(isinstance(v, int | float) for v in details.values())  # content-free
    published = [d for d in tmp_path.rglob(compact.MATCHER_DIR) if d.is_dir()]
    assert published == []  # Never published; only an untrusted staging directory.
    bounded = build(entries, q=4)
    assert bounded.manifest["anchor_statistics"]["max_bucket"] <= compact.MAX_ANCHOR_BUCKET
    rng = random.Random(1)
    docs = [[rng.choice("ab") for _ in range(30)] for _ in range(20)]
    assert_equivalent(historical(entries), bounded, docs)


def compile_to(directory: Path, index: Path, **options: Any) -> CompactExactMatcher:
    return prepare(
        directory,
        index,
        index_sha256=file_sha(index),
        index_bytes=index.stat().st_size,
        max_record=1024 * 1024,
        max_records=options.pop("max_records", None),
        max_logical_nodes=options.pop("max_logical_nodes", None),
        **options,
    )


def fixture_entries(count: int = 400, seed: int = 11) -> list[Pattern]:
    rng = random.Random(seed)
    words = [f"lex{i}" for i in range(300)]
    return [
        Pattern(tuple(rng.choice(words) for _ in range(rng.randint(3, 14))), (f"item{i}:prompt",))
        for i in range(count)
    ]


def test_compile_is_deterministic_and_order_independent(tmp_path: Path) -> None:
    entries = fixture_entries()
    index = write_index(tmp_path / "index.jsonl", entries)
    first = compile_to(tmp_path / "a" / "matcher", index)
    second = compile_to(tmp_path / "b" / "matcher", index)
    first.close()
    second.close()
    for name in [*compact.FILES, compact.MANIFEST]:
        assert (tmp_path / "a/matcher" / name).read_bytes() == (
            tmp_path / "b/matcher" / name
        ).read_bytes()
    shuffled = list(entries) + entries[:30]
    random.Random(5).shuffle(shuffled)
    other = write_index(tmp_path / "shuffled.jsonl", shuffled)
    third = compile_to(tmp_path / "c" / "matcher", other)
    third.close()
    for name in compact.FILES:
        assert (tmp_path / "a/matcher" / name).read_bytes() == (
            tmp_path / "c/matcher" / name
        ).read_bytes()
    manifest = json.loads((tmp_path / "a/matcher/manifest.json").read_bytes())
    serialized = json.dumps(manifest)
    assert str(tmp_path) not in serialized and "lex1" not in serialized
    assert "item" not in serialized  # No provenance, tokens or paths.


@pytest.mark.parametrize("damage", ["array_byte", "manifest_count", "extra_file", "missing_file"])
def test_damaged_compiled_matcher_refuses_reuse(tmp_path: Path, damage: str) -> None:
    index = write_index(tmp_path / "index.jsonl", fixture_entries())
    directory = tmp_path / "scratch" / "matcher"
    compile_to(directory, index).close()
    if damage == "array_byte":
        target = directory / "pattern_tokens.bin"
        raw = bytearray(target.read_bytes())
        raw[len(raw) // 2] ^= 1
        target.write_bytes(bytes(raw))
    elif damage == "manifest_count":
        manifest = json.loads((directory / "manifest.json").read_bytes())
        manifest["counts"]["logical_trie_nodes"] -= 1
        (directory / "manifest.json").write_bytes(canonical.canonical_bytes(manifest))
        with pytest.raises(C05Error, match="manifest digest"):
            compile_to(directory, index)
        # Even a recomputed self-digest cannot hide a count the arrays contradict.
        manifest.pop("digest")
        manifest["digest"] = canonical.digest(manifest)
        (directory / "manifest.json").write_bytes(canonical.canonical_bytes(manifest))
    elif damage == "extra_file":
        (directory / "notes.txt").write_bytes(b"x")
    else:
        (directory / "anchor_keys.bin").unlink()
    with pytest.raises(C05Error, match="compiled matcher refused"):
        compile_to(directory, index)
    assert directory.is_dir()  # Refused, never silently rebuilt or deleted.


def test_wrong_source_index_refuses_reuse(tmp_path: Path) -> None:
    index = write_index(tmp_path / "index.jsonl", fixture_entries())
    directory = tmp_path / "scratch" / "matcher"
    compile_to(directory, index).close()
    write_index(index, fixture_entries(seed=12))
    with pytest.raises(C05Error, match="source index digest"):
        compile_to(directory, index)
    with pytest.raises(C05Error, match="changed during matcher compilation"):
        prepare(
            tmp_path / "other" / "matcher",
            index,
            index_sha256="0" * 64,
            index_bytes=index.stat().st_size,
            max_record=1024 * 1024,
            max_records=None,
            max_logical_nodes=None,
        )
    assert not (tmp_path / "other" / "matcher").exists()


def test_interrupted_compile_is_never_trusted_and_rebuilds(tmp_path: Path) -> None:
    index = write_index(tmp_path / "index.jsonl", fixture_entries())
    directory = tmp_path / "scratch" / "matcher"
    staging = tmp_path / "scratch" / compact.STAGING_DIR

    def crash() -> None:
        raise RuntimeError("authored interruption before publication")

    with pytest.raises(RuntimeError, match="before publication"):
        compile_to(directory, index, before_publish=crash)
    assert staging.is_dir() and (staging / compact.MANIFEST).is_file()
    assert not directory.exists()
    with compile_to(directory, index) as matcher:  # Staging discarded, rebuilt cleanly.
        assert matcher.manifest["counts"]["unique_patterns"] > 0
    assert not staging.exists()
    # A published directory missing its manifest is incomplete: refused.
    (directory / compact.MANIFEST).unlink()
    with pytest.raises(C05Error, match="incomplete"):
        compile_to(directory, index)
    # Unknown entries inside staging are not deleted blindly.
    for entry in directory.iterdir():
        entry.unlink()
    directory.rmdir()
    staging.mkdir()
    (staging / "foreign.txt").write_bytes(b"x")
    with pytest.raises(C05Error, match="unexpected entry"):
        compile_to(tmp_path / "scratch" / "matcher", index)
    assert (staging / "foreign.txt").exists()


def test_closed_matcher_releases_its_files(tmp_path: Path) -> None:
    index = write_index(tmp_path / "index.jsonl", fixture_entries())
    directory = tmp_path / "scratch" / "matcher"
    matcher = compile_to(directory, index)
    assert matcher.match(["nothing"]) is None
    matcher.close()
    matcher.close()  # Idempotent.
    moved = directory.with_name("moved")
    directory.rename(moved)  # Windows refuses this while a mapping is open.
    for entry in moved.iterdir():
        entry.unlink()
    moved.rmdir()


def test_logical_node_and_record_ceilings_match_historical_semantics(tmp_path: Path) -> None:
    entries = fixture_entries(120) + fixture_entries(120)[:10]  # 10 duplicate records
    nodes = historical(entries).nodes
    records = len(entries)
    index = write_index(tmp_path / "index.jsonl", entries)
    with pytest.raises(CeilingExceeded) as refused:
        compile_to(tmp_path / "n1" / "matcher", index, max_logical_nodes=nodes - 1)
    assert refused.value.ceiling == "automaton_nodes"
    with pytest.raises(C05Error, match="node ceiling"):
        StreamingMatcher(entries, max_patterns=records, max_nodes=nodes - 1)
    with pytest.raises(CeilingExceeded) as refused:
        compile_to(tmp_path / "r1" / "matcher", index, max_records=records - 1)
    assert refused.value.ceiling == "benchmark_patterns"
    with pytest.raises(C05Error, match="pattern limit"):
        StreamingMatcher(entries, max_patterns=records - 1, max_nodes=nodes)
    StreamingMatcher(entries, max_patterns=records, max_nodes=nodes)
    exact = compile_to(
        tmp_path / "ok" / "matcher", index, max_records=records, max_logical_nodes=nodes
    )
    exact.close()
    assert exact.manifest["counts"]["logical_trie_nodes"] == nodes
    assert exact.manifest["counts"]["index_records"] == records
    # Reuse re-checks the stored counts against the current ceilings.
    with pytest.raises(C05Error, match="automaton node ceiling"):
        compile_to(tmp_path / "ok" / "matcher", index, max_logical_nodes=nodes - 1)


def test_ram_and_scratch_ceilings_refuse_compilation(tmp_path: Path) -> None:
    from xlm.data.exclusion.matcher_audit import Monitor

    index = write_index(tmp_path / "index.jsonl", fixture_entries())
    for field, ceiling in (("ram_bytes", 1), ("scratch_bytes", 1)):
        scratch = tmp_path / field
        scratch.mkdir()
        monitor = Monitor(Resources(**{field: ceiling}), scratch)
        if field == "scratch_bytes":
            monitor.peak_scratch = 0
            (scratch / "occupied").write_bytes(b"xx")
        with pytest.raises(CeilingExceeded) as refused:
            compile_to(scratch / "matcher", index, check=monitor)
        assert refused.value.ceiling == field
        assert not (scratch / "matcher").exists()


def test_compiled_bytes_stay_within_the_derived_storage_bound(tmp_path: Path) -> None:
    entries = fixture_entries(800)
    index = write_index(tmp_path / "index.jsonl", entries)
    with compile_to(tmp_path / "s" / "matcher", index) as matcher:
        compiled = sum(f.stat().st_size for f in (tmp_path / "s" / "matcher").iterdir())
        assert compiled <= matcher.manifest["compiled_bytes"] + compact.MANIFEST_BYTES
    tight = index.stat().st_size
    assert compiled <= compact.compiled_bound(tight, len(entries), 0)
    plan, _, _ = setup_run(tmp_path / "plan", [document("a", "An authored row.")])
    bounds = storage_bounds(plan.resources, plan.storage)
    assert bounds["compiled_matcher"] == compact.compiled_bound(
        plan.resources.benchmark_bytes, plan.resources.benchmark_patterns, FILE_SLACK_BYTES
    )
    short = small_resources(scratch_bytes=sum(bounds.values()) - 1)
    with pytest.raises(C05Error, match="no automatic widening"):
        admit_plan(short, plan.storage)


def test_resource_contract_and_benchmark_patterns_meaning_are_unchanged(tmp_path: Path) -> None:
    # Signed resource decisions restate this exact field set; nothing was added.
    assert set(Resources.model_fields) == {
        *("ram_bytes", "scratch_bytes", "index_bytes", "journal_bytes", "output_bytes"),
        *("decision_bytes", "free_bytes", "document_bytes", "document_tokens", "records"),
        *("attempted_records", "files", "comparisons", "bytes_read", "oversized_buckets"),
        *("benchmark_bytes", "benchmark_patterns", "automaton_nodes", "review_candidates"),
        *("workers", "stage_seconds", "overall_seconds"),
    }
    defaults = Resources()
    assert (defaults.benchmark_patterns, defaults.automaton_nodes) == (2_000_000, 8_000_000)
    # benchmark_patterns still counts emitted index records, duplicates included.
    entries = [p("copper bridges expand during summer", f"r{i}") for i in range(3)]
    index = write_index(tmp_path / "index.jsonl", entries)
    with pytest.raises(CeilingExceeded, match="benchmark_patterns"):
        compile_to(tmp_path / "a" / "matcher", index, max_records=2)
    compile_to(tmp_path / "b" / "matcher", index, max_records=3).close()


def test_structure_has_no_per_pattern_python_objects(tmp_path: Path) -> None:
    deltas = []
    for count in (500, 5000):
        index = write_index(tmp_path / f"{count}.jsonl", fixture_entries(count, seed=3))
        gc.collect()
        before = len(gc.get_objects())
        matcher = compile_to(tmp_path / str(count) / "matcher", index)
        gc.collect()
        deltas.append(len(gc.get_objects()) - before)
        for array in (matcher._tokens, matcher._offsets, matcher._keys, matcher._patterns):
            assert not array.flags.owndata  # A view of the read-only mapping.
        assert matcher.manifest["counts"]["logical_trie_nodes"] > count
        matcher.close()
    # Ten times the patterns (and logical nodes) adds no tracked Python containers.
    assert abs(deltas[1] - deltas[0]) < 50


# --- runner integration -------------------------------------------------------


def implementation(monkeypatch: pytest.MonkeyPatch) -> None:
    from xlm.data.exclusion import identity

    monkeypatch.setattr(
        identity,
        "implementation_identity",
        lambda: {"code_commit": "3" * 40, "code_identity": "4" * 64, "dependency_sha256": "5" * 64},
    )


def check_resume(plan: Any, index: Path, receipt: dict[str, Any]) -> dict[str, Any]:
    return resume_check(
        plan,
        authorize(plan, "fixture", b"authored-local-test-key-not-a-protected-issuer"),
        index=index,
        benchmark=receipt,
        trusted={"fixture": b"authored-local-test-key-not-a-protected-issuer"},
    )


def test_run_resume_and_resume_check_verify_the_compiled_matcher(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    implementation(monkeypatch)
    docs = [document("a", PROMPT), document("b", "A quiet lighthouse keeper's notebook.")]
    plan, index, receipt = setup_run(tmp_path / "run", docs)
    work = Path(plan.scratch_root) / plan.identity()

    def crash(event: str) -> None:
        if event == "matcher_staged":
            raise RuntimeError("authored crash during matcher compilation")

    with pytest.raises(RuntimeError, match="matcher compilation"):
        execute(plan, index, receipt, checkpoint=crash)
    assert (work / compact.STAGING_DIR).is_dir() and not (work / "matcher").exists()
    report = check_resume(plan, index, receipt)
    assert report["compiled_matcher"].startswith("absent")
    assert report["incomplete_matcher_staging"] is True

    def stop(event: str) -> None:
        if event == "row":
            raise RuntimeError("authored interruption after compile")

    with pytest.raises(RuntimeError, match="after compile"):
        execute(plan, index, receipt, checkpoint=stop)
    assert not (work / compact.STAGING_DIR).exists()
    assert check_resume(plan, index, receipt)["compiled_matcher"] == "verified"
    result = execute(plan, index, receipt)["payload"]
    assert (result["excluded"], result["kept"]) == (1, 1)
    clean, clean_index, clean_receipt = setup_run(tmp_path / "clean", docs)
    execute(clean, clean_index, clean_receipt)
    assert membership(plan) == membership(clean)
    assert "compiled_matcher" in result["storage"]["bounds"]


def test_runner_refuses_a_corrupted_compiled_matcher_before_scanning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    implementation(monkeypatch)
    plan, index, receipt = setup_run(tmp_path, [document("a", PROMPT)])
    work = Path(plan.scratch_root) / plan.identity()

    def stop(event: str) -> None:
        if event == "row":
            raise RuntimeError("authored interruption")

    with pytest.raises(RuntimeError):
        execute(plan, index, receipt, checkpoint=stop)
    target = work / "matcher" / "anchor_keys.bin"
    raw = bytearray(target.read_bytes())
    raw[0] ^= 0xFF
    target.write_bytes(bytes(raw))
    scanned: list[str] = []
    with pytest.raises(C05Error, match="compiled matcher refused"):
        execute(plan, index, receipt, checkpoint=scanned.append)
    assert scanned == []  # No corpus row was read.
    with pytest.raises(C05Error, match="compiled matcher refused"):
        check_resume(plan, index, receipt)


def test_compiled_matcher_location_guard(tmp_path: Path) -> None:
    from xlm.data.exclusion.artifacts import PlanIsolation
    from xlm.data.exclusion.isolation import inspect_protected_root

    plan, _, _ = setup_run(tmp_path, [document("a", "An authored row.")])
    work = Path(plan.scratch_root) / plan.identity()
    assert matcher_directory(plan, work) == work / "matcher"  # authored temp path
    with pytest.raises(C05Error, match="inside the plan's C05 scratch"):
        matcher_directory(plan, tmp_path / "elsewhere" / "job")
    inside = plan.model_copy(
        update={"mode": "protected", "scratch_root": str(checkout_root() / "c05-scratch")}
    )
    with pytest.raises(C05Error, match="repository checkout"):
        matcher_directory(inside, Path(inside.scratch_root) / "job")
    root = tmp_path / "C05-Protected"
    init_protected_root(root, "c05-fixture-root")
    protected = VolumeIdentity(device="fixture-protected")
    detached = plan.model_copy(
        update={
            "isolation": PlanIsolation(
                protected_root=inspect_protected_root(root, lambda _: protected),
                index_relpath="index.jsonl",
            )
        }
    )
    assert matcher_directory(detached, work, lambda _: protected) == work / "matcher"
    with pytest.raises(C05Error, match="protected volume"):
        matcher_directory(detached, work, lambda _: VolumeIdentity(device="ordinary-g"))


def test_detached_volume_run_keeps_the_compiled_matcher_on_protected_scratch(
    tmp_path: Path,
) -> None:
    from test_c05_detached_volume import (
        PROTECTED_DEVICE,
        Layout,
        detached_spec,
        protected_plan,
    )
    from test_c05_detached_volume import execute as detached_execute
    from test_c05_detached_volume import prepare as detached_prepare
    from xlm.data.exclusion.identity import implementation_identity

    actual = implementation_identity()
    layout = Layout(tmp_path)
    spec, pins = detached_spec(layout, "protected", getpass.getuser())
    envelope = detached_prepare(layout, spec, actual)
    plan = protected_plan(layout, envelope, pins, actual)
    detached_execute(layout, plan, envelope, actual)
    compiled = layout.scratch / plan.identity() / "matcher"
    assert (compiled / compact.MANIFEST).is_file()
    assert layout.inspector(compiled) == PROTECTED_DEVICE
    assert compiled.resolve().is_relative_to(layout.scratch.resolve())
    assert not any(p.name in compact.FILES for p in layout.gvol.rglob("*.bin"))


# --- content-free operator audit ----------------------------------------------


def audit_cli(arguments: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, Any]:
    from xlm.data.exclusion.operator import main

    code = main(["benchmark-matcher-audit-local", *arguments])
    return code, json.loads(capsys.readouterr().out)


def test_operator_matcher_audit_is_content_free(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "xvol" / "C05-Protected"
    init_protected_root(root, "c05-fixture-root")
    entries = fixture_entries(300)
    index = write_index(root / "prepared" / "index.jsonl", entries)
    resources = tmp_path / "resources.json"
    canonical.write_canonical_json(resources, small_resources().model_dump(mode="json"))
    scratch = tmp_path / "xvol" / "C05-Scratch" / "matcher-audit"
    arguments = ["--index", str(index), "--resources", str(resources), "--scratch", str(scratch)]
    code, report = audit_cli([*arguments, "--self-check", "50"], capsys)
    assert code == 0 and report["all_fit"] is True and report["content_free"] is True
    assert report["index_records"] == len(entries)
    assert report["unique_token_patterns"] == len({e.tokens for e in entries})
    assert report["logical_trie_nodes"] == historical(entries).nodes
    assert report["self_check"]["passed"] is True
    assert report["self_check"]["patterns_checked"] == 50
    for key in ("p50_bucket", "p95_bucket", "p99_bucket", "p999_bucket", "max_bucket"):
        assert isinstance(report["anchor"][key], int)
    assert report["compiled_matcher_bytes"] <= report["compiled_matcher_bound_bytes"]
    assert report["fits"]["automaton_nodes"] and report["fits"]["ram_bytes"]
    text = json.dumps(report)
    assert not any(word in text for word in ("lex1", "item1", "prompt"))
    assert (scratch / "matcher" / compact.MANIFEST).is_file()
    # Reuse of a non-empty audit scratch refuses; scratch inside the root refuses.
    code, refused = audit_cli(arguments, capsys)
    assert code == 1 and refused == {"refused": True, "error_type": "C05Error"}
    inside = [*arguments[:4], "--scratch", str(root / "scratch")]
    assert audit_cli(inside, capsys)[0] == 1
    code, streaming = audit_cli(
        [*arguments[:4], "--scratch", str(tmp_path / "xvol" / "s2"), "--backend", "streaming"],
        capsys,
    )
    assert code == 0 and streaming["logical_trie_nodes"] == report["logical_trie_nodes"]


def test_operator_audit_reports_ceilings_without_refusing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    entries = fixture_entries(200)
    index = write_index(tmp_path / "authored" / "index.jsonl", entries)
    nodes = historical(entries).nodes
    resources = tmp_path / "resources.json"
    canonical.write_canonical_json(
        resources,
        small_resources(automaton_nodes=nodes - 1, benchmark_patterns=10).model_dump(mode="json"),
    )
    arguments = ["--index", str(index), "--resources", str(resources), "--mode", "authored"]
    code, report = audit_cli([*arguments, "--scratch", str(tmp_path / "s")], capsys)
    assert code == 2 and report["all_fit"] is False
    assert report["fits"]["automaton_nodes"] is False
    assert report["fits"]["benchmark_patterns"] is False
    assert report["logical_trie_nodes"] == nodes
    protected = ["--index", str(index), "--resources", str(resources)]
    code, refused = audit_cli([*protected, "--scratch", str(tmp_path / "p")], capsys)
    assert code == 1  # Protected mode requires a marked protected root.


def test_operator_audit_ram_refusal_discards_partial_compile(tmp_path: Path) -> None:
    from xlm.data.exclusion.matcher_audit import Monitor, audit

    class Late(Monitor):
        """Breaches the RAM ceiling only once compilation is under way."""

        calls = 0

        def __call__(self) -> None:
            Late.calls += 1
            if Late.calls > 3:
                raise CeilingExceeded("ram_bytes", {"peak_rss_bytes": self.peak_rss})

    index = write_index(tmp_path / "index.jsonl", fixture_entries(3000))
    report = audit(index, small_resources(), tmp_path / "s", mode="authored", monitor_factory=Late)
    assert report["refused"] is True and report["ceiling"] == "ram_bytes"
    assert Late.calls > 3
    assert not any((tmp_path / "s").iterdir())  # The partial staging copy was removed.
