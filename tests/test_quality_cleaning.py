"""Phase-B cleaning-policy dry run on authored fixtures (no real corpus, no network).

Covers: the pinned v1 rule set, the freeze from a verified Phase-A audit (provenance,
verbatim copy, refusals), 0/1/2/3 severe-signal decisions, structured-class
protection, the FinePDFs OCR rule, the encoding rule, full HTML vs code examples,
legitimate Unicode, exact union accounting against an independent oracle,
component-specific thresholds, guardrails, deterministic review selection, worker
determinism, no source mutation, no text leakage, receipt/binding validation,
interruption/resume, one detector pass per document, the C05 diagnostic overlay and
operator-only review materialization.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import zlib
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from cleaning_fixtures import DOCS, TEST_CUTS, cleaning_layout, override_thresholds
from quality_fixtures import CANARIES, build_corpus
from xlm.data.evidence_v2 import canonical
from xlm.data.quality import cleaning, cleaning_runner
from xlm.data.quality.cleaning import CleanStats, evaluate, strata_of
from xlm.data.quality.cleaning_policy import (
    DROP,
    KEEP,
    OCR_SIGNALS,
    OUTCOMES,
    POLICY_SECTION_DIGEST,
    REP_SIGNALS,
    REVIEW,
    RULE_IDS,
    ComponentRules,
    PolicyError,
    RuleParams,
    Threshold,
    dump_yaml,
    freeze_policy,
    load_frozen,
    parse_yaml,
    read_policy,
    validate_template,
)
from xlm.data.quality.cleaning_report import ARTIFACTS, REVIEW_MANIFEST, guardrails
from xlm.data.quality.cleaning_runner import (
    BINDING_FILE,
    RECEIPT_FILE,
    load_receipt,
    materialize_review,
    run_dry_run,
    verify_dry_run,
)
from xlm.data.quality.cli import main
from xlm.data.quality.detectors import analyze
from xlm.data.quality.phase_a_history import HistoricalAuditError
from xlm.data.quality.policy import METRIC_INDEX, METRICS
from xlm.data.quality.review import ReviewError, read_review_rows
from xlm.data.quality.runner import Limits, ReviewLimits, run_audit
from xlm.data.quality.scan import QualityError

REPO = Path(__file__).resolve().parents[1]
TEMPLATE = REPO / "recipes" / "quality" / "cleaning_policy_v1.yaml"
TEXT_CANARIES = (
    *CANARIES,
    "Survey 1000",
    "Buy cheap items",
    "cafÃ©",
    "lost � glyph",
    "مرحبا",
    "def read(path)",
    "Journal of Applied Things",
)


def limits(workers: int = 1, **changes: Any) -> Limits:
    values: dict[str, Any] = {
        "workers": workers,
        "max_rss_bytes": 8 * 1024**3,
        "free_reserve_bytes": 0,
        "max_output_bytes": 256 * 1024**2,
        "line_ceiling": 1024**2,
        "deadline_seconds": 600.0,
    }
    values.update(changes)
    return Limits(**values)


def corpus_hashes(root: Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def artifacts(output: Path) -> dict[str, bytes]:
    return {name: (output / name).read_bytes() for name in ARTIFACTS}


def dry(
    flow: dict[str, Path], output: Path, workers: int = 1, policy: Path | None = None, **kw: Any
) -> dict[str, Any]:
    return run_dry_run(
        flow["manifest"],
        output,
        policy or flow["policy"],
        limits=limits(workers),
        progress_interval=None,
        **kw,
    )


@pytest.fixture(scope="module")
def flow(tmp_path_factory: pytest.TempPathFactory) -> Iterator[dict[str, Path]]:
    """Authored corpus -> Phase-A audit -> FROZEN policy -> authored-cut variant."""
    root = tmp_path_factory.mktemp("cleaning")
    manifest = build_corpus(root / "corpus", cleaning_layout())
    audit = root / "audit"
    run_audit(manifest, audit, limits=limits(), progress_interval=None)
    frozen = root / "policies" / "frozen.yaml"
    frozen.parent.mkdir()
    freeze_policy(TEMPLATE, audit, frozen)
    policy = override_thresholds(frozen, root / "policies" / "test-cuts.yaml")
    yield {"root": root, "manifest": manifest, "audit": audit, "frozen": frozen, "policy": policy}


@pytest.fixture(scope="module")
def baseline(flow: dict[str, Path], tmp_path_factory: pytest.TempPathFactory) -> Path:
    output = tmp_path_factory.mktemp("baseline") / "dry"
    dry(flow, output)
    return output


# -- the frozen v1 rule set ---------------------------------------------------------------


def params() -> RuleParams:
    body, _ = read_policy(TEMPLATE)
    return validate_template(body)


def test_template_is_the_pinned_v1_rule_set() -> None:
    body, _ = read_policy(TEMPLATE)
    assert canonical.digest(body["policy"]) == POLICY_SECTION_DIGEST
    p = validate_template(body)
    assert p.structured_classes == {"code_like", "math_table_like"}
    assert (p.default_drop_at_least, p.structured_drop_at_least) == (2, 3)
    assert p.structured_review_equals == 2
    assert p.ocr_components == {"finepdfs_en"} and p.ocr_at_least == 2
    assert p.ocr_drop_severe_at_least == 1
    assert (p.replacement_at_least, p.mojibake_at_least) == (8, 16)
    assert p.forbidden_controls == ("c0_controls", "c1_controls")
    assert dict(p.guardrails) == {
        "component_drop_docs_pct_gt": 2,
        "global_drop_docs_pct_gt": 2,
        "component_drop_bytes_pct_gt": 10,
        "global_drop_bytes_pct_gt": 5,
    }
    assert p.max_rows == 150
    assert body["policy"]["transform"] == "DISABLED"
    assert body["policy"]["outcomes"] == ["KEEP", "DROP", "REVIEW"]


def test_any_rule_edit_or_duplicate_key_refuses(tmp_path: Path) -> None:
    raw = TEMPLATE.read_text(encoding="utf-8")
    edited = tmp_path / "edited.yaml"
    edited.write_text(raw.replace("signal_count_at_least: 2", "signal_count_at_least: 1"))
    with pytest.raises(PolicyError, match="reviewed v1 rule set"):
        validate_template(read_policy(edited)[0])
    duplicate = tmp_path / "duplicate.yaml"
    duplicate.write_text(raw + "kind: other\n")
    with pytest.raises(PolicyError, match="duplicate YAML key"):
        read_policy(duplicate)


# -- freezing --------------------------------------------------------------------------------


def test_freeze_copies_conservative_cuts_verbatim_with_provenance(flow: dict[str, Path]) -> None:
    import yaml

    candidate = yaml.safe_load((flow["audit"] / "candidate-policy-conservative.yaml").read_bytes())
    receipt = json.loads((flow["audit"] / "quality-audit-receipt.json").read_bytes())
    frozen = parse_yaml(flow["frozen"].read_bytes())
    assert frozen["status"] == "FROZEN"
    assert frozen["digest"] == canonical.self_digest(frozen)
    provenance = frozen["provenance"]
    assert provenance["phase_a"]["receipt_digest"] == receipt["digest"]
    assert provenance["phase_a"]["result_digest"] == receipt["result_digest"]
    assert provenance["phase_a"]["input_manifest_digest"] == receipt["input_manifest"]["digest"]
    entry = receipt["artifacts"]["candidate-policy-conservative.yaml"]
    assert provenance["candidate_policy"]["sha256"] == entry["sha256"]
    assert provenance["candidate_policy"]["quantile"] == [999, 1000]
    assert frozen["template_sha256"] == hashlib.sha256(TEMPLATE.read_bytes()).hexdigest()
    assert set(frozen["thresholds"]) == set(candidate["components"]) == set(DOCS)
    copied = 0
    for component, entry_rules in candidate["components"].items():
        rules = {r["id"]: r for r in entry_rules["rules"]}
        thresholds = frozen["thresholds"][component]
        assert (thresholds["ocr"] is None) == (component != "finepdfs_en")
        families = [("repetition", REP_SIGNALS), ("ocr", OCR_SIGNALS)]
        for family, signals in families:
            if thresholds[family] is None:
                continue
            for metric in signals:
                rule = rules.get(f"{component}.{metric}")
                value = thresholds[family][metric]
                if rule is None:
                    assert value is None
                    continue
                copied += 1
                for key in ("comparator", "cut", "cut_bin", "quantile_bin"):
                    assert value[key] == rule[key]
                assert value["phase_a_impact"] == rule["estimated_impact"]
    assert copied > 0
    compiled = load_frozen(flow["frozen"])
    assert compiled.provenance == provenance


def test_freeze_refuses_tampered_candidate_and_existing_destination(
    flow: dict[str, Path], tmp_path: Path
) -> None:
    audit = tmp_path / "audit"
    shutil.copytree(flow["audit"], audit)
    with pytest.raises(PolicyError, match="destination exists"):
        freeze_policy(TEMPLATE, audit, flow["frozen"])
    path = audit / "candidate-policy-conservative.yaml"
    path.write_bytes(path.read_bytes().replace(b"cut: ", b"cut:  ", 1))
    with pytest.raises(HistoricalAuditError, match="candidate-policy-conservative.yaml differs"):
        freeze_policy(TEMPLATE, audit, tmp_path / "frozen.yaml")
    assert not (tmp_path / "frozen.yaml").exists()
    (audit / "quality-audit-receipt.json").unlink()
    with pytest.raises(HistoricalAuditError, match="receipt is missing"):
        freeze_policy(TEMPLATE, audit, tmp_path / "frozen.yaml")


def test_dry_run_refuses_unfrozen_edited_or_foreign_policies(
    flow: dict[str, Path], tmp_path: Path
) -> None:
    with pytest.raises(PolicyError, match="field set|not FROZEN"):
        dry(flow, tmp_path / "a", policy=TEMPLATE)
    body = parse_yaml(flow["policy"].read_bytes())
    body["thresholds"]["finewiki_en"]["repetition"]["max_char_run"]["cut"] = 2
    edited = tmp_path / "edited.yaml"
    edited.write_bytes(dump_yaml(body))
    with pytest.raises(PolicyError, match="self-digest"):
        dry(flow, tmp_path / "b", policy=edited)
    body["provenance"]["phase_a"]["input_manifest_digest"] = "0" * 64
    body["digest"] = canonical.self_digest(body)
    foreign = tmp_path / "foreign.yaml"
    foreign.write_bytes(dump_yaml(body))
    with pytest.raises(PolicyError, match="different input manifest"):
        dry(flow, tmp_path / "c", policy=foreign)
    body = parse_yaml(flow["policy"].read_bytes())
    body["thresholds"]["finewiki_en"]["ocr"] = body["thresholds"]["finepdfs_en"]["ocr"]
    body["digest"] = canonical.self_digest(body)
    scoped = tmp_path / "scoped.yaml"
    scoped.write_bytes(dump_yaml(body))
    with pytest.raises(PolicyError, match="outside the OCR scope"):
        dry(flow, tmp_path / "d", policy=scoped)
    for name in ("a", "b", "c", "d"):
        assert not (tmp_path / name / RECEIPT_FILE).exists()


# -- decision semantics (pure) -------------------------------------------------------------


def rules_for(component: str = "finewiki_en", **cuts: tuple[str, float | int]) -> ComponentRules:
    chosen = {**TEST_CUTS, **cuts}

    def make(metric: str) -> Threshold:
        comparator, cut = chosen[metric]
        return Threshold(metric, METRIC_INDEX[metric], comparator == ">=", cut)

    ocr = tuple(make(m) for m in OCR_SIGNALS) if component == "finepdfs_en" else None
    return ComponentRules(component, tuple(make(m) for m in REP_SIGNALS), ocr)


def values(**set_values: float | int | None) -> list[float | int | None]:
    """Clean measurements (nothing fires) with chosen overrides."""
    out: list[float | int | None] = [0] * len(METRICS)
    for spec in METRICS:
        out[METRIC_INDEX[spec.name]] = 0.0 if spec.kind == "ratio" else 0
    out[METRIC_INDEX["compression_ratio"]] = 0.6
    for name, value in set_values.items():
        out[METRIC_INDEX[name]] = value
    return out


SIGNAL_HITS: dict[str, float | int] = {
    "compression_ratio": 0.1,
    "ngram10_excess_ratio": 0.9,
    "dup_line_byte_ratio": 0.9,
    "dup_paragraph_byte_ratio": 0.9,
    "repeated_char_ratio": 0.9,
    "max_char_run": 500,
}


def with_signals(count: int, **extra: float | int | None) -> list[float | int | None]:
    hits = dict(list(SIGNAL_HITS.items())[:count])
    return values(**hits, **extra)


@pytest.mark.parametrize(
    ("count", "default", "structured"),
    [(0, KEEP, KEEP), (1, KEEP, KEEP), (2, DROP, REVIEW), (3, DROP, DROP), (6, DROP, DROP)],
)
def test_severe_signal_counts_by_class_group(count: int, default: int, structured: int) -> None:
    p = params()
    for doc_class in ("prose_like", "other", "markup_like", "empty"):
        decision = evaluate(with_signals(count), frozenset(), doc_class, rules_for(), p)
        assert (decision.severe_count, decision.outcome) == (count, default), doc_class
    for doc_class in ("math_table_like", "code_like"):
        decision = evaluate(with_signals(count), frozenset(), doc_class, rules_for(), p)
        assert (decision.severe_count, decision.outcome) == (count, structured), doc_class


def test_single_signal_keep_is_review_eligible_and_structured_is_protected() -> None:
    p = params()
    v = with_signals(1)
    one = evaluate(v, frozenset(), "prose_like", rules_for(), p)
    assert one.outcome == KEEP and one.rules == 0
    assert "control.default_one_signal" in strata_of(one, "finewiki_en", frozenset(), v, p)
    v2 = with_signals(2)
    structured = evaluate(v2, frozenset(), "code_like", rules_for(), p)
    assert cleaning.rule_names(structured.rules) == ["rep.structured_two_signals"]
    strata = strata_of(structured, "finewiki_en", frozenset(), v2, p)
    assert "review.rep.structured_two_signals" in strata
    protected = evaluate(v, frozenset(), "math_table_like", rules_for(), p)
    assert "protected.structured_one_signal" in strata_of(protected, "x", frozenset(), v, p)


def test_comparators_are_exact_at_the_cut_and_not_applicable_never_fires() -> None:
    p = params()
    rules = rules_for()
    at = values(max_char_run=64, compression_ratio=0.17)
    below = values(max_char_run=63, compression_ratio=0.1699999)
    # ``>=`` fires exactly at the cut; ``<`` does not fire at the cut.
    run_bit = 1 << REP_SIGNALS.index("max_char_run")
    compression_bit = 1 << REP_SIGNALS.index("compression_ratio")
    assert evaluate(at, frozenset(), "prose_like", rules, p).severe == run_bit
    assert evaluate(below, frozenset(), "prose_like", rules, p).severe == compression_bit
    na = values(compression_ratio=None, ngram10_excess_ratio=None)
    decision = evaluate(na, frozenset(), "prose_like", rules, p)
    assert decision.severe_count == 0 and decision.severe_na == 0b11
    missing = ComponentRules("x", (None,) * len(REP_SIGNALS), None)
    assert evaluate(with_signals(6), frozenset(), "prose_like", missing, p).outcome == KEEP


def test_component_thresholds_differ_per_component() -> None:
    p = params()
    v = values(max_char_run=100, repeated_char_ratio=0.35)
    strict = rules_for("common_pile_prose")
    lenient = rules_for("finewiki_en", max_char_run=(">=", 200), repeated_char_ratio=(">=", 0.5))
    assert evaluate(v, frozenset(), "prose_like", strict, p).outcome == DROP
    assert evaluate(v, frozenset(), "prose_like", lenient, p).outcome == KEEP


def test_finepdfs_ocr_rule_is_component_scoped() -> None:
    p = params()
    pdf = rules_for("finepdfs_en")
    two = {"page_number_line_ratio": 0.5, "repeated_header_ratio": 0.5}
    review = evaluate(values(**two), frozenset(), "prose_like", pdf, p)
    assert (review.ocr_count, OUTCOMES[review.outcome]) == (2, "REVIEW")
    assert cleaning.rule_names(review.rules) == ["ocr.finepdfs_review"]
    drop = evaluate(values(**two, max_char_run=99), frozenset(), "prose_like", pdf, p)
    assert cleaning.rule_names(drop.rules) == ["ocr.finepdfs_with_repetition"]
    assert drop.outcome == DROP and drop.severe_count == 1
    one = evaluate(values(page_number_line_ratio=0.5), frozenset(), "prose_like", pdf, p)
    assert one.outcome == KEEP and one.ocr_count == 1
    for component in ("finewiki_en", "common_pile_prose", "essential_science"):
        other = evaluate(values(**two), frozenset(), "prose_like", rules_for(component), p)
        assert other.outcome == KEEP and not other.ocr_scope


@pytest.mark.parametrize(
    ("measurements", "outcome", "rules"),
    [
        ({"replacement_chars": 7}, KEEP, []),
        ({"replacement_chars": 8}, REVIEW, ["enc.replacement_review"]),
        ({"mojibake_hits": 15}, KEEP, []),
        ({"mojibake_hits": 16}, REVIEW, ["enc.mojibake_review"]),
        (
            {"replacement_chars": 8, "mojibake_hits": 16},
            DROP,
            ["enc.replacement_review", "enc.mojibake_review", "enc.replacement_and_mojibake"],
        ),
        (
            {"replacement_chars": 8, "c0_controls": 1},
            DROP,
            ["enc.replacement_review", "enc.with_forbidden_controls"],
        ),
        (
            {"mojibake_hits": 16, "c1_controls": 2},
            DROP,
            ["enc.mojibake_review", "enc.with_forbidden_controls"],
        ),
        ({"c0_controls": 3, "c1_controls": 3}, KEEP, []),
        ({"nul": 1}, DROP, ["hard.nul"]),
        ({"noncharacters": 1}, DROP, ["hard.noncharacters"]),
        (
            {"bom": 4, "bidi_controls": 3, "zero_width": 5, "zwj_zwnj": 9, "urls": 40},
            KEEP,
            [],
        ),
    ],
)
def test_encoding_and_hard_corruption_rules(
    measurements: dict[str, int], outcome: int, rules: list[str]
) -> None:
    decision = evaluate(values(**measurements), frozenset(), "prose_like", rules_for(), params())
    assert decision.outcome == outcome
    assert cleaning.rule_names(decision.rules) == rules


def analyze_decide(text: str, component: str = "common_pile_prose") -> cleaning.Decision:
    result = analyze(text, len(text.encode("utf-8")))
    return evaluate(
        result.values, frozenset(result.flags), result.doc_class, rules_for(component), params()
    )


def test_full_html_page_drops_but_code_example_markup_is_kept() -> None:
    page = analyze_decide(DOCS["ultrax_ultrafineweb"]["html_page"])
    assert cleaning.rule_names(page.rules) == ["hard.full_html"] and page.outcome == DROP
    example = DOCS["ultrax_ultrafineweb"]["code_example"]
    result = analyze(example, len(example.encode("utf-8")))
    decision = analyze_decide(example)
    assert decision.outcome == KEEP and decision.rules == 0
    p = params()
    strata = strata_of(decision, "ultrax_ultrafineweb", frozenset(result.flags), result.values, p)
    assert "protected.code_example_markup" in strata


@pytest.mark.parametrize(
    "name",
    [
        "accented_latin",
        "japanese",
        "arabic_hebrew",
        "emoji",
        "combining_marks",
        "math_unicode",
        "format_marks",
        "urls",
        "boilerplate",
        "light_markup",
        "page_numbers",
        "markdown_table",
        "latex",
    ],
)
def test_legitimate_unicode_markup_and_structure_are_kept(name: str) -> None:
    decision = analyze_decide(DOCS["common_pile_prose"][name])
    assert decision.outcome == KEEP and decision.rules == 0


def test_guardrails_are_strict_exact_integer_comparisons(flow: dict[str, Path]) -> None:
    compiled = load_frozen(flow["policy"])

    def stats(docs: int, drop_docs: int, nbytes: int, drop_bytes: int) -> CleanStats:
        s = CleanStats()
        s.docs, s.bytes = docs, nbytes
        s.arrays["outcome"][DROP] = [drop_docs, drop_bytes]
        s.arrays["outcome"][KEEP] = [docs - drop_docs, nbytes - drop_bytes]
        return s

    exact = stats(100, 2, 1000, 50)  # exactly 2 % docs and exactly 5 % bytes
    assert guardrails(exact, {"c": stats(100, 2, 1000, 100)}, compiled)["breaches"] == []
    rails = guardrails(stats(100, 3, 1000, 51), {"c": stats(100, 2, 1000, 101)}, compiled)
    assert rails["status"] == "POLICY_REQUIRES_REVIEW"
    assert {(b["scope"], b["measure"]) for b in rails["breaches"]} == {
        ("global", "drop_docs_pct"),
        ("global", "drop_bytes_pct"),
        ("component:c", "drop_bytes_pct"),
    }


# -- end to end ----------------------------------------------------------------------------


def oracle(flow: dict[str, Path]) -> dict[str, Any]:
    """Independent per-document recomputation of every outcome (docs, bytes)."""
    compiled = load_frozen(flow["policy"])
    manifest = json.loads(flow["manifest"].read_bytes())
    root = Path(manifest["data_root"])
    totals: dict[str, list[list[int]]] = {}
    combos: dict[str, dict[int, int]] = {}
    for entry in manifest["files"]:
        component = entry["component"]
        for line in (root / entry["path"]).read_bytes().splitlines():
            row = json.loads(line)
            text = row["text"]
            size = len(text.encode("utf-8"))
            result = analyze(text, size)
            decision = evaluate(
                result.values,
                frozenset(result.flags),
                result.doc_class,
                compiled.rules_for(component),
                compiled.params,
            )
            for scope in ("global", component):
                cell = totals.setdefault(scope, [[0, 0] for _ in OUTCOMES])
                cell[decision.outcome][0] += 1
                cell[decision.outcome][1] += size
                combo = combos.setdefault(scope, {})
                combo[decision.rules] = combo.get(decision.rules, 0) + 1
    return {"outcomes": totals, "combos": combos}


def test_exact_union_accounting_matches_an_independent_oracle(
    flow: dict[str, Path], baseline: Path
) -> None:
    expected = oracle(flow)
    report = json.loads((baseline / "cleaning-dry-run.json").read_bytes())
    manifest = json.loads(flow["manifest"].read_bytes())
    total_docs = sum(f["documents"] for f in manifest["files"])
    total_bytes = sum(f["canonical_bytes"] for f in manifest["files"])
    for scope, summary in [("global", report["global"]), *report["components"].items()]:
        got = [[summary["outcomes"][o]["docs"], summary["outcomes"][o]["bytes"]] for o in OUTCOMES]
        assert got == expected["outcomes"][scope], scope
        assert summary["drop_union"]["docs"] == got[DROP][0]
        assert summary["review_union"]["bytes"] == got[REVIEW][1]
    g = report["global"]
    assert sum(g["outcomes"][o]["docs"] for o in OUTCOMES) == total_docs == g["documents"]
    assert sum(g["outcomes"][o]["bytes"] for o in OUTCOMES) == total_bytes
    histogram = g["severe_repetition_signal_count"]
    assert set(histogram) == {str(n) for n in range(7)}
    assert sum(cell["docs"] for cell in histogram.values()) == total_docs
    intersections = json.loads((baseline / "cleaning-rule-intersections.json").read_bytes())
    combos = intersections["scopes"]["global"]["exact_rule_combinations"]
    by_mask = {sum((1 << RULE_IDS.index(r) for r in c["rules"]), 0): c["docs"] for c in combos}
    assert by_mask == expected["combos"]["global"]
    unions = intersections["scopes"]["global"]["unions"]
    assert unions["drop_union"]["docs"] == expected["outcomes"]["global"][DROP][0]
    assert unions["review_union"]["docs"] == expected["outcomes"]["global"][REVIEW][0]
    # Every rule fired somewhere, and marginal >= exclusive.
    by_rule = json.loads((baseline / "cleaning-by-rule.json").read_bytes())["rules"]
    for name, entry in by_rule.items():
        marginal, exclusive = entry["global"]["marginal"], entry["global"]["exclusive"]
        assert marginal["docs"] >= exclusive["docs"]
        assert marginal["docs"] >= 1, name
    classes = g["class_distribution"]
    assert sum(c["docs"] for c in classes["DROP"].values()) == g["drop_union"]["docs"]
    assert sum(c["docs"] for c in classes["REVIEW"].values()) == g["review_union"]["docs"]


def test_expected_decisions_and_guardrail_flag(baseline: Path) -> None:
    report = json.loads((baseline / "cleaning-dry-run.json").read_bytes())
    assert report["policy_status"] == "POLICY_REQUIRES_REVIEW"
    assert report["guardrails"]["breaches"]
    assert report["corpus_modified"] is False and report["cleaned_corpus_written"] is False
    components = report["components"]
    assert components["finewiki_en"]["rules"]["rep.severe_default"]["marginal"]["docs"] == 2
    pdf = components["finepdfs_en"]
    assert pdf["rules"]["ocr.finepdfs_with_repetition"]["marginal"]["docs"] == 1
    assert pdf["rules"]["ocr.finepdfs_review"]["marginal"]["docs"] == 2
    assert pdf["ocr_scope"]["documents_in_scope"]["docs"] == pdf["documents"]
    assert components["finewiki_en"]["ocr_scope"]["documents_in_scope"]["docs"] == 0
    science = components["essential_science"]["rules"]
    assert science["rep.structured_two_signals"]["marginal"]["docs"] == 2
    assert science["rep.severe_structured"]["marginal"]["docs"] == 1
    assert science["enc.with_forbidden_controls"]["marginal"]["docs"] == 2
    common = components["common_pile_prose"]
    assert common["outcomes"]["DROP"]["docs"] == 1  # only the character spam
    assert common["outcomes"]["REVIEW"]["docs"] == 0
    thresholds = {(r["component"], r["signal"]): r for r in report["frozen_thresholds"]}
    assert thresholds[("finewiki_en", "max_char_run")]["cut"] == 64
    assert ("finewiki_en", "page_number_line_ratio") not in thresholds


def test_review_manifest_is_bounded_deterministic_locators_only(
    flow: dict[str, Path], baseline: Path, tmp_path: Path
) -> None:
    rows = read_review_rows(baseline / REVIEW_MANIFEST)
    assert 0 < len(rows) <= 150
    keys = [(r["path"], r["row"]) for r in rows]
    assert len(set(keys)) == len(keys)
    for row in rows:
        cleaning_runner.check_review_row(row)
        assert row["selected_by"] in row["strata"]
    selected = {r["selected_by"] for r in rows}
    for stratum in (
        "priority.finewiki_en.severe_repetition",
        "priority.finewiki_en.compression",
        "priority.finepdfs_en.ocr_repetition",
        "priority.common_pile_prose.char_runs",
        "priority.ultrax_ultrafineweb.severe_tail",
        "priority.encoding.essential_science",
        "priority.encoding.ultrax_ultrafineweb",
        "drop.hard.full_html",
        "drop.hard.nul",
        "drop.hard.noncharacters",
        "review.ocr.finepdfs_review",
        "review.enc.mojibake_review",
        "protected.code_example_markup",
        "control.default_one_signal",
        "control.encoding_near",
        "control.finepdfs_ocr_one",
    ):
        assert any(stratum in r["strata"] for r in rows), stratum
    assert any(s.startswith("coverage.") for s in selected)
    covered = {(r["component"], r["outcome"]) for r in rows}
    assert {(c, "KEEP") for c in DOCS} <= covered
    report = json.loads((baseline / "cleaning-dry-run.json").read_bytes())
    for entry in report["review_manifest"]["strata"]:
        # Earlier strata may already hold members; a quota is a floor, never a cap.
        members = entry["members"]["docs"]
        assert min(entry["quota"], members) <= entry["selected_members"] <= members
    # Same inputs, fresh output: identical selection.
    again = tmp_path / "again"
    dry(flow, again)
    assert (again / REVIEW_MANIFEST).read_bytes() == (baseline / REVIEW_MANIFEST).read_bytes()


def test_review_selection_respects_the_row_cap(flow: dict[str, Path]) -> None:
    from dataclasses import replace

    from xlm.data.quality.cleaning_report import select_review

    compiled = load_frozen(flow["policy"])
    small = replace(compiled, params=replace(compiled.params, max_rows=5))
    candidates = {
        "drop.hard.nul": [
            {"rank": n, "path": "p", "row": n, "strata": ["drop.hard.nul"], **_row_stub()}
            for n in range(1, 9)
        ]
    }
    rows, report = select_review(candidates, small, ["c"])
    assert len(rows) == 5
    nul = next(e for e in report if e["stratum"] == "drop.hard.nul")
    assert nul["selected_members"] == 5 and nul["shortfall_reason"] == "max_rows"


def _row_stub() -> dict[str, Any]:
    return {
        "offset": 0,
        "doc_id_sha256": "0" * 64,
        "row_sha256": "0" * 64,
        "component": "c",
        "doc_class": "prose_like",
        "outcome": "DROP",
        "rules": ["hard.nul"],
        "severe_repetition_signals": [],
        "severe_repetition_signal_count": 0,
        "ocr_signals": [],
        "ocr_signal_count": None,
        "kept": None,
        "values": {},
    }


def test_worker_counts_give_byte_identical_artifacts(
    flow: dict[str, Path], baseline: Path, tmp_path: Path
) -> None:
    expected = artifacts(baseline)
    binding = (baseline / BINDING_FILE).read_bytes()
    for workers in (2, 4):
        output = tmp_path / f"w{workers}"
        result = dry(flow, output, workers=workers)
        assert result["scan"]["workers"] == workers
        assert artifacts(output) == expected
        assert (output / BINDING_FILE).read_bytes() == binding
        assert load_receipt(output)["result_digest"] == load_receipt(baseline)["result_digest"]


def test_dry_run_is_read_only_and_content_free(flow: dict[str, Path], tmp_path: Path) -> None:
    before = corpus_hashes(flow["root"])
    output = tmp_path / "out"
    log = tmp_path / "progress.log"
    run_dry_run(
        flow["manifest"],
        output,
        flow["policy"],
        limits=limits(2),
        progress_interval=0.2,
        progress_log=log,
    )
    assert corpus_hashes(flow["root"]) == before  # corpus, Phase-A audit and policies
    names = {p.relative_to(output).as_posix() for p in output.rglob("*") if p.is_file()}
    assert names == {*ARTIFACTS, BINDING_FILE, RECEIPT_FILE} | {
        f"units/f{n:05d}.unit.zz" for n in range(len(DOCS))
    }
    payloads = [p.read_bytes() for p in output.glob("*") if p.is_file()]
    payloads += [zlib.decompress(p.read_bytes()) for p in (output / "units").iterdir()]
    payloads.append(log.read_bytes())
    for payload in payloads:
        for canary in TEXT_CANARIES:
            assert canary.encode("utf-8") not in payload, canary
    lines = log.read_text(encoding="utf-8").splitlines()
    assert lines and all(line.startswith("[quality-clean]") for line in lines)
    assert any(" scan " in line or "complete" in line for line in lines)
    receipt = load_receipt(output)
    assert receipt["corpus_modified"] is False and receipt["cleaned_corpus_written"] is False
    assert receipt["actions_executed"] == []


def test_one_detector_pass_per_document(
    flow: dict[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[int] = []
    real = analyze

    def counting(text: str, nbytes: int) -> Any:
        calls.append(nbytes)
        return real(text, nbytes)

    monkeypatch.setattr(cleaning, "analyze", counting)
    dry(flow, tmp_path / "out")
    manifest = json.loads(flow["manifest"].read_bytes())
    assert len(calls) == sum(f["documents"] for f in manifest["files"])


def test_receipt_binding_and_report_verification(
    flow: dict[str, Path], baseline: Path, tmp_path: Path
) -> None:
    result = verify_dry_run(flow["manifest"], baseline, flow["policy"])
    assert result["verified"] and result["sources_rehashed"]
    receipt = load_receipt(baseline)
    binding = json.loads((baseline / BINDING_FILE).read_bytes())
    assert receipt["binding"] == binding
    assert binding["cleaning_policy"]["digest"] == load_frozen(flow["policy"]).digest
    assert receipt["policy_status"] == "POLICY_REQUIRES_REVIEW"
    # A tampered artifact refuses.
    output = tmp_path / "copy"
    shutil.copytree(baseline, output)
    path = output / "cleaning-dry-run-summary.md"
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(QualityError):
        verify_dry_run(flow["manifest"], output, flow["policy"])
    # A re-digested receipt with a forged status refuses.
    shutil.rmtree(output)
    shutil.copytree(baseline, output)
    body = json.loads((output / RECEIPT_FILE).read_bytes())
    body["policy_status"] = "POLICY_WITHIN_GUARDRAILS"
    body["digest"] = canonical.self_digest(body)
    (output / RECEIPT_FILE).write_bytes(canonical.canonical_bytes(body))
    with pytest.raises(QualityError, match="policy status"):
        verify_dry_run(flow["manifest"], output, flow["policy"])
    # A forged envelope (smaller RSS ceiling than measured) refuses at validation.
    body["policy_status"] = "POLICY_REQUIRES_REVIEW"
    body["envelope"]["max_rss_bytes"] = 1
    body["digest"] = canonical.self_digest(body)
    (output / RECEIPT_FILE).write_bytes(canonical.canonical_bytes(body))
    with pytest.raises(QualityError, match="receipt invalid"):
        load_receipt(output)
    # A different (re-frozen) policy no longer matches the binding.
    other = override_thresholds(
        flow["frozen"], tmp_path / "other.yaml", cuts={**TEST_CUTS, "max_char_run": (">=", 65)}
    )
    with pytest.raises(QualityError, match="binding"):
        verify_dry_run(flow["manifest"], baseline, other)


def test_interruption_then_resume_gives_identical_artifacts(
    flow: dict[str, Path], baseline: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "out"
    real = cleaning_runner.commit_unit
    committed: list[int] = []

    def interrupting(*args: Any, **kwargs: Any) -> None:
        if len(committed) == 2:
            raise KeyboardInterrupt
        real(*args, **kwargs)
        committed.append(1)

    monkeypatch.setattr(cleaning_runner, "commit_unit", interrupting)
    with pytest.raises(KeyboardInterrupt):
        dry(flow, output)
    assert not (output / RECEIPT_FILE).exists()
    assert len(list((output / "units").glob("*.unit.zz"))) == 2
    monkeypatch.setattr(cleaning_runner, "commit_unit", real)
    result = dry(flow, output, workers=2)
    assert (result["files_resumed"], result["files_scanned"]) == (2, len(DOCS) - 2)
    assert artifacts(output) == artifacts(baseline)
    verify_dry_run(flow["manifest"], output, flow["policy"])
    with pytest.raises(QualityError, match="already complete"):
        dry(flow, output)


def test_resume_refuses_a_changed_policy_or_mutated_source(
    flow: dict[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "out"
    real = cleaning_runner.commit_unit

    def once(*args: Any, **kwargs: Any) -> None:
        real(*args, **kwargs)
        raise KeyboardInterrupt

    monkeypatch.setattr(cleaning_runner, "commit_unit", once)
    with pytest.raises(KeyboardInterrupt):
        dry(flow, output)
    monkeypatch.setattr(cleaning_runner, "commit_unit", real)
    other = override_thresholds(
        flow["frozen"], tmp_path / "other.yaml", cuts={**TEST_CUTS, "max_char_run": (">=", 65)}
    )
    with pytest.raises(QualityError, match="different dry-run binding"):
        dry(flow, output, policy=other)
    # A source file mutated after its unit was committed refuses on resume.
    corpus = tmp_path / "corpus"
    manifest = build_corpus(corpus, cleaning_layout())
    audit = tmp_path / "audit"
    run_audit(manifest, audit, limits=limits(), progress_interval=None)
    frozen = tmp_path / "frozen.yaml"
    freeze_policy(TEMPLATE, audit, frozen)
    out2 = tmp_path / "out2"
    monkeypatch.setattr(cleaning_runner, "commit_unit", once)
    with pytest.raises(KeyboardInterrupt):
        run_dry_run(manifest, out2, frozen, limits=limits(), progress_interval=None)
    monkeypatch.setattr(cleaning_runner, "commit_unit", real)
    first = sorted((corpus / "data").rglob("documents.jsonl"))[0]
    first.write_bytes(first.read_bytes().replace(b"Rivers", b"Lakes!", 1))
    with pytest.raises(QualityError, match="differs from the frozen manifest"):
        run_dry_run(manifest, out2, frozen, limits=limits(), progress_interval=None)


def test_output_must_not_be_a_phase_a_audit_or_overlap_inputs(
    flow: dict[str, Path], tmp_path: Path
) -> None:
    with pytest.raises(QualityError, match="not owned"):
        dry(flow, flow["audit"])
    with pytest.raises(QualityError):
        dry(flow, flow["policy"].parent)


def test_cli_runs_and_refuses_content_free(
    flow: dict[str, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    common = [
        "--manifest",
        str(flow["manifest"]),
        "--policy",
        str(flow["policy"]),
        "--workers",
        "1",
        "--max-rss-gib",
        "4",
        "--free-reserve-gib",
        "0",
        "--max-output-gib",
        "1",
        "--deadline-hours",
        "1",
        "--no-progress",
    ]
    assert main(["clean-dry-run", "--output", str(tmp_path / "out"), *common]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["complete"] and result["cleaned_corpus_written"] is False
    assert result["policy_status"] == "POLICY_REQUIRES_REVIEW"
    assert main(["clean-dry-run", "--output", str(tmp_path / "out"), *common]) == 1
    assert json.loads(capsys.readouterr().out)["refused"] is True
    code = main(
        [
            "clean-report",
            "--manifest",
            str(flow["manifest"]),
            "--policy",
            str(flow["policy"]),
            "--output",
            str(tmp_path / "out"),
            "--workers",
            "1",
        ]
    )
    assert code == 0 and json.loads(capsys.readouterr().out)["verified"] is True
    code = main(
        [
            "clean-materialize-review",
            "--output",
            str(tmp_path / "out"),
            "--destination",
            str(tmp_path / "review"),
        ]
    )
    assert code == 1 and "operator-confirm" in capsys.readouterr().out
    assert not (tmp_path / "review").exists()


def test_materialize_review_copies_only_selected_verified_rows(
    flow: dict[str, Path], baseline: Path, tmp_path: Path
) -> None:
    if REPO.resolve() in tmp_path.resolve().parents:  # pragma: no cover - defensive
        pytest.skip("temporary directory inside the repository")
    with pytest.raises(ReviewError, match="inside the repository"):
        materialize_review(baseline, REPO / "never-review", limits=ReviewLimits())
    destination = tmp_path / "review"
    result = materialize_review(
        baseline,
        destination,
        limits=ReviewLimits(max_documents=150, max_chars=5000),
        strata=["drop.hard.full_html", "priority.finepdfs_en.ocr_repetition"],
    )
    rows = read_review_rows(baseline / REVIEW_MANIFEST)
    wanted = [
        r
        for r in rows
        if {"drop.hard.full_html", "priority.finepdfs_en.ocr_repetition"} & set(r["strata"])
    ]
    assert result["materialized"] == len(wanted) > 0
    records = [
        json.loads(line)
        for line in (destination / "review.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    texts = {f"{c}-{n}": t for c, docs in DOCS.items() for n, t in docs.items()}
    for record in records:
        assert record["excerpt"] == texts[record["doc_id"]][:5000]
    html_page = (destination / "review.html").read_text(encoding="utf-8")
    assert "<script>var x" not in html_page and "&lt;script&gt;" in html_page
    # Tampered review manifest refuses before any text is written.
    copy = tmp_path / "copy"
    shutil.copytree(baseline, copy)
    path = copy / REVIEW_MANIFEST
    path.write_bytes(path.read_bytes().replace(b'"row": ', b'"row":  ', 1))
    with pytest.raises((QualityError, ReviewError)):
        materialize_review(copy, tmp_path / "review2", limits=ReviewLimits())
    assert not (tmp_path / "review2").exists()


# -- C05 diagnostic overlay ---------------------------------------------------------------


@pytest.fixture(scope="module")
def c05_flow(tmp_path_factory: pytest.TempPathFactory) -> Iterator[dict[str, Any]]:
    from scripts.c05_authored_pilot import KEY
    from scripts.c05_synthetic_flow import KEY_ENV, decide_and_plan, prepare, run_c05

    environment = pytest.MonkeyPatch()
    environment.setenv(KEY_ENV, KEY)
    root = tmp_path_factory.mktemp("cleaning-c05") / "root"
    paths = prepare(root)
    result = run_c05(paths, decide_and_plan(paths))
    audit = root.parent / "audit"
    run_audit(root / "manifest.json", audit, limits=limits(), progress_interval=None)
    frozen = root.parent / "frozen.yaml"
    freeze_policy(TEMPLATE, audit, frozen)
    yield {
        "manifest": root / "manifest.json",
        "proof": Path(result["proof"]),
        "completion": result["completion"]["payload"],
        "policy": frozen,
    }
    environment.undo()


def test_c05_overlay_is_diagnostic_only(c05_flow: dict[str, Any], tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    run_dry_run(
        c05_flow["manifest"], plain, c05_flow["policy"], limits=limits(), progress_interval=None
    )
    overlay = tmp_path / "overlay"
    run_dry_run(
        c05_flow["manifest"],
        overlay,
        c05_flow["policy"],
        limits=limits(2),
        progress_interval=None,
        proof=c05_flow["proof"],
        allow_authored_proof=True,
    )
    a = json.loads((plain / "cleaning-dry-run.json").read_bytes())
    b = json.loads((overlay / "cleaning-dry-run.json").read_bytes())
    assert a["diagnostic_c05_overlay"] is None
    assert a["global"] == b["global"] and a["components"] == b["components"]
    diagnostic = b["diagnostic_c05_overlay"]["scopes"]["global"]
    completion = c05_flow["completion"]
    kept = sum(diagnostic["c05_kept"][o]["docs"] for o in OUTCOMES)
    removed = sum(diagnostic["c05_removed"][o]["docs"] for o in OUTCOMES)
    assert kept == completion["kept"]
    assert kept + removed == b["global"]["documents"]
    for o in OUTCOMES:
        assert (
            diagnostic["c05_kept"][o]["docs"] + diagnostic["c05_removed"][o]["docs"]
            == b["global"]["outcomes"][o]["docs"]
        )
    rows = read_review_rows(overlay / REVIEW_MANIFEST)
    assert {r["kept"] for r in rows} <= {True, False}


def test_streaming_sampler_equals_a_full_sort_bottom_quota(flow: dict[str, Path]) -> None:
    from xlm.data.quality.cleaning import (
        CleanTask,
        _quota,
        measure_clean_chunk,
        quota_of,
        sampling_key,
        stratum_salt,
    )
    from xlm.data.quality.review import doc_rank
    from xlm.data.quality.scan import file_tasks, load_manifest

    compiled = load_frozen(flow["policy"])
    manifest = load_manifest(flow["manifest"])
    key = sampling_key(compiled.params, manifest.digest)
    quotas = quota_of(compiled.params)
    for item in manifest.files:
        rules = compiled.rules_for(item.component)
        for chunk in file_tasks(manifest.data_root, item, None, 1024**2):
            got = measure_clean_chunk(CleanTask(chunk, item.component, rules, compiled.params, key))
            expected: dict[str, list[tuple[int, int]]] = {}
            for n, line in enumerate(chunk.data.splitlines()):
                text = json.loads(line)["text"]
                result = analyze(text, len(text.encode("utf-8")))
                decision = evaluate(
                    result.values, frozenset(result.flags), result.doc_class, rules, compiled.params
                )
                row = chunk.first_row + n
                names = strata_of(
                    decision,
                    item.component,
                    frozenset(result.flags),
                    result.values,
                    compiled.params,
                )
                for name in names:
                    rank = doc_rank(key, item.path, row) ^ stratum_salt(key, name)
                    expected.setdefault(name, []).append((rank, row))
            assert set(got.review) == set(expected)
            for name, ranked in expected.items():
                bottom = sorted(ranked)[: _quota(quotas, name)]
                assert [(e["rank"], e["row"]) for e in got.review[name]] == bottom, name


# -- historical Phase-A audits (freeze compatibility) -------------------------------------

HISTORICAL_CHUNK_BYTES = 32 * 1024**2  # the accepted pre-performance implementation


@pytest.fixture(scope="module")
def historical(tmp_path_factory: pytest.TempPathFactory) -> Iterator[dict[str, Path]]:
    """A COMPLETE Phase-A audit whose recorded envelope and binding carry the historical
    32 MiB scan chunk (the current implementation uses 8 MiB)."""
    from xlm.data.quality import runner, scan

    assert scan.CHUNK_BYTES != HISTORICAL_CHUNK_BYTES
    root = tmp_path_factory.mktemp("historical")
    manifest = build_corpus(root / "corpus", cleaning_layout())
    audit = root / "audit"
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(scan, "CHUNK_BYTES", HISTORICAL_CHUNK_BYTES)
        patch.setattr(runner, "CHUNK_BYTES", HISTORICAL_CHUNK_BYTES)
        run_audit(manifest, audit, limits=limits(2), progress_interval=None)
    yield {"root": root, "manifest": manifest, "audit": audit}


def _receipt_path(audit: Path) -> Path:
    return audit / "quality-audit-receipt.json"


def _read_receipt(audit: Path) -> dict[str, Any]:
    body: dict[str, Any] = json.loads(_receipt_path(audit).read_bytes())
    return body


def _seal(audit: Path, receipt: dict[str, Any]) -> None:
    """Re-digest a modified receipt (a forger with write access, no signing key)."""
    receipt["digest"] = canonical.self_digest(receipt)
    _receipt_path(audit).write_bytes(canonical.canonical_bytes(receipt))


def _replace_artifact(audit: Path, name: str, data: bytes) -> None:
    """Rewrite an artifact AND consistently reseal its receipt entry, the result digest,
    the recorded output bytes and the receipt digest, so only semantic checks remain."""
    receipt = _read_receipt(audit)
    old = (audit / name).stat().st_size
    (audit / name).write_bytes(data)
    receipt["artifacts"][name] = {
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "records": data.count(b"\n"),
    }
    table = receipt["artifacts"]
    receipt["result_digest"] = canonical.digest({n: table[n]["sha256"] for n in sorted(table)})
    receipt["execution"]["output_bytes_before_receipt"] += len(data) - old
    _seal(audit, receipt)


def _copy(historical: dict[str, Path], tmp_path: Path) -> Path:
    audit = tmp_path / "audit"
    shutil.copytree(historical["audit"], audit)
    return audit


def test_historical_audit_with_other_chunk_size_freezes(
    historical: dict[str, Path], tmp_path: Path
) -> None:
    from xlm.data.quality.receipt import load_receipt as load_phase_a_receipt
    from xlm.data.quality.report import ARTIFACTS as PHASE_A_ARTIFACTS
    from xlm.data.quality.scan import CHUNK_BYTES

    audit = historical["audit"]
    receipt = _read_receipt(audit)
    assert receipt["envelope"]["chunk_bytes"] == HISTORICAL_CHUNK_BYTES != CHUNK_BYTES
    assert receipt["binding"]["chunk_bytes"] == HISTORICAL_CHUNK_BYTES
    # The CURRENT-run verifier (audit / report / materialize-review) stays strict.
    with pytest.raises(QualityError, match="chunk_bytes differs from the implementation"):
        load_phase_a_receipt(audit, PHASE_A_ARTIFACTS, "quality-audit-receipt.json")
    before = corpus_hashes(historical["root"])
    frozen = tmp_path / "frozen.yaml"
    result = freeze_policy(TEMPLATE, audit, frozen)
    assert result["frozen"] is True
    assert corpus_hashes(historical["root"]) == before  # the historical audit is read-only
    compiled = load_frozen(frozen)
    phase_a = compiled.provenance["phase_a"]
    assert phase_a["receipt_digest"] == receipt["digest"]
    assert phase_a["binding_digest"] == receipt["binding_digest"]
    assert phase_a["code_identity"] == receipt["implementation"]["code_identity"]
    assert phase_a["operational_envelope"] == receipt["envelope"]  # preserved, not compared
    assert phase_a["operational_envelope"]["chunk_bytes"] == HISTORICAL_CHUNK_BYTES
    assert set(compiled.components) == set(DOCS)
    # The frozen policy drives a CURRENT (8 MiB) dry run and its full verification.
    output = tmp_path / "dry"
    run_dry_run(historical["manifest"], output, frozen, limits=limits(), progress_interval=None)
    assert verify_dry_run(historical["manifest"], output, frozen)["verified"] is True


def _mutate_receipt_manifest(audit: Path) -> None:
    receipt = _read_receipt(audit)
    receipt["input_manifest"]["digest"] = "0" * 64
    _seal(audit, receipt)


def _forge_binding(audit: Path, change: Any) -> None:
    """Consistently rewrite binding, binding file and receipt (all self-digests valid)."""
    receipt = _read_receipt(audit)
    binding = receipt["binding"]
    change(binding)
    binding["digest"] = canonical.self_digest(binding)
    (audit / "audit-binding.json").write_bytes(canonical.canonical_bytes(binding))
    receipt["binding_digest"] = binding["digest"]
    receipt["input_manifest"]["digest"] = binding["input_manifest"]["digest"]
    receipt["detector_policy"] = binding["detector_policy"]
    _seal(audit, receipt)


def _set_manifest_digest(binding: dict[str, Any]) -> None:
    binding["input_manifest"]["digest"] = "1" * 64


def _set_detector_digest(binding: dict[str, Any]) -> None:
    binding["detector_policy"]["digest"] = "2" * 64


def _status_incomplete(audit: Path) -> None:
    receipt = _read_receipt(audit)
    receipt["status"] = "INCOMPLETE"
    _seal(audit, receipt)


def _unsealed_edit(audit: Path) -> None:
    receipt = _read_receipt(audit)
    receipt["execution"]["wall_seconds"] = 0.5
    _receipt_path(audit).write_bytes(canonical.canonical_bytes(receipt))


def _inconsistent_envelope(audit: Path) -> None:
    receipt = _read_receipt(audit)
    receipt["envelope"]["chunk_bytes"] = 8 * 1024**2  # binding still records 32 MiB
    receipt["producer_envelopes"] = [receipt["envelope"]]
    _seal(audit, receipt)


def _missing_unit(audit: Path) -> None:
    next((audit / "units").iterdir()).unlink()


def _edit_bytes(name: str, old: bytes, new: bytes) -> Any:
    def apply(audit: Path) -> None:
        path = audit / name
        path.write_bytes(path.read_bytes().replace(old, new, 1))

    return apply


@pytest.mark.parametrize(
    ("tamper", "message"),
    [
        (
            _edit_bytes("candidate-policy-conservative.yaml", b"cut: ", b"cut:  "),
            "candidate-policy-conservative.yaml differs",
        ),
        (_edit_bytes("quality-audit.json", b'"documents"', b'"Documents"'), "quality-audit.json"),
        (_edit_bytes("audit-binding.json", b'"line_ceiling"', b'"Line_ceiling"'), "binding"),
        (_mutate_receipt_manifest, "input manifest differs"),
        (lambda a: _forge_binding(a, _set_manifest_digest), "bindings"),
        (lambda a: _forge_binding(a, _set_detector_digest), "differs from the current detectors"),
        (_status_incomplete, "not COMPLETE"),
        (lambda a: _receipt_path(a).unlink(), "receipt is missing"),
        (_unsealed_edit, "self-digest"),
        (lambda a: (a / "quality-audit.json").unlink(), "quality-audit.json is missing"),
        (lambda a: (a / "candidate-policy-conservative.yaml").unlink(), "is missing"),
        (_inconsistent_envelope, "envelope chunk vs binding"),
        (_missing_unit, "audit unit is missing"),
    ],
)
def test_historical_freeze_still_refuses_tampering(
    historical: dict[str, Path], tmp_path: Path, tamper: Any, message: str
) -> None:
    audit = _copy(historical, tmp_path)
    tamper(audit)
    destination = tmp_path / "frozen.yaml"
    with pytest.raises(QualityError, match=message):
        freeze_policy(TEMPLATE, audit, destination)
    assert not destination.exists()


def _candidate_edit(change: Any) -> Any:
    def apply(audit: Path) -> None:
        import yaml

        name = "candidate-policy-conservative.yaml"
        body = yaml.safe_load((audit / name).read_bytes())
        change(body)
        _replace_artifact(audit, name, dump_yaml(body))

    return apply


def _first_rules(body: dict[str, Any]) -> list[dict[str, Any]]:
    rules: list[dict[str, Any]] = body["components"]["finewiki_en"]["rules"]
    return rules


def _set_action(body: dict[str, Any]) -> None:
    _first_rules(body)[-1]["action"] = "DROP"


def _duplicate_rule(body: dict[str, Any]) -> None:
    _first_rules(body).append(dict(_first_rules(body)[-1]))


def _unknown_detector(body: dict[str, Any]) -> None:
    rule = _first_rules(body)[-1]
    rule["detector"], rule["id"] = "urls", "finewiki_en.urls"


def _drop_component(body: dict[str, Any]) -> None:
    del body["components"]["finewiki_en"]


def _extra_component(body: dict[str, Any]) -> None:
    body["components"]["invented_component"] = body["components"]["finewiki_en"]


def _executable(body: dict[str, Any]) -> None:
    body["executable"] = True


def _status_reviewed(body: dict[str, Any]) -> None:
    body["status"] = "REVIEWED"


def _string_cut(body: dict[str, Any]) -> None:
    for rule in _first_rules(body):
        if rule["detector"] == "dup_line_byte_ratio":
            rule["cut"] = "0.5"


def _wrong_comparator(body: dict[str, Any]) -> None:
    for rule in _first_rules(body):
        if rule["detector"] == "compression_ratio":
            rule["comparator"] = ">="


def _wrong_bindings(body: dict[str, Any]) -> None:
    body["bindings"]["input_manifest_digest"] = "3" * 64


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (_set_action, "action is not null"),
        (_duplicate_rule, "duplicate candidate rule"),
        (_unknown_detector, "unexpected detector"),
        (_drop_component, "component set differs"),
        (_extra_component, "component set differs"),
        (_executable, "executable"),
        (_status_reviewed, "status"),
        (_string_cut, "threshold cut"),
        (_wrong_comparator, "threshold comparator"),
        (_wrong_bindings, "bindings differ"),
    ],
)
def test_historical_freeze_refuses_malformed_candidate_even_when_resealed(
    historical: dict[str, Path], tmp_path: Path, change: Any, message: str
) -> None:
    audit = _copy(historical, tmp_path)
    _candidate_edit(change)(audit)
    destination = tmp_path / "frozen.yaml"
    with pytest.raises(QualityError, match=message):
        freeze_policy(TEMPLATE, audit, destination)
    assert not destination.exists()


def test_resealed_candidate_without_semantic_change_still_freezes(
    historical: dict[str, Path], tmp_path: Path
) -> None:
    """Control for the reseal helper: a consistent reseal alone is not what refuses."""
    audit = _copy(historical, tmp_path)
    _candidate_edit(lambda body: None)(audit)
    assert freeze_policy(TEMPLATE, audit, tmp_path / "frozen.yaml")["frozen"] is True
