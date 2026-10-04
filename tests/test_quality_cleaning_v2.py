"""Cleaning policy v2 (human-reviewed successor of v1) on authored fixtures only.

Proves the v2 decision semantics next to the unchanged v1 semantics, the pinned v2 rule
set, the v2 freeze (same authenticated Phase-A audit as the frozen v1 predecessor,
identical cuts, recorded predecessor, no corpus text), its refusals, and a v2 dry run
(exact accounting against an independent oracle, no REVIEW outcome, worker
determinism, verification and operator materialization). v1 artifacts, tests and
evidence are untouched.
"""

from __future__ import annotations

import hashlib
import json
import zlib
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from cleaning_fixtures import DOCS, TEST_CUTS, cleaning_layout, override_thresholds
from quality_fixtures import CANARIES, build_corpus, document
from xlm.data.evidence_v2 import canonical
from xlm.data.quality.cleaning import evaluate, rule_names, strata_of
from xlm.data.quality.cleaning_policy import (
    DROP,
    KEEP,
    OCR_SIGNALS,
    OUTCOMES,
    POLICY_SECTION_DIGEST,
    POLICY_SECTION_DIGEST_V2,
    REP_SIGNALS,
    REVIEW,
    RULESET_V1,
    RULESET_V2,
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
from xlm.data.quality.cleaning_report import ARTIFACTS, REVIEW_MANIFEST
from xlm.data.quality.cleaning_runner import (
    check_review_row,
    load_receipt,
    materialize_review,
    run_dry_run,
    verify_dry_run,
)
from xlm.data.quality.cli import main
from xlm.data.quality.detectors import analyze
from xlm.data.quality.policy import METRIC_INDEX, METRICS
from xlm.data.quality.review import read_review_rows
from xlm.data.quality.runner import Limits, ReviewLimits, run_audit

REPO = Path(__file__).resolve().parents[1]
TEMPLATE_V1 = REPO / "recipes" / "quality" / "cleaning_policy_v1.yaml"
TEMPLATE_V2 = REPO / "recipes" / "quality" / "cleaning_policy_v2.yaml"
FROZEN_V1 = REPO / "recipes" / "quality" / "cleaning_policy_v1.frozen.yaml"
# A programming tutorial that shows a COMPLETE HTML page as literal, unfenced text: the
# hardened detector classifies it as full HTML (v1 hard.full_html DROP, v2 KEEP).
HTML_TUTORIAL = (
    "How to make your first web page. Save the following as index.html and open it in "
    "a browser:\n\n<!DOCTYPE html>\n<html>\n<head><title>My first page</title></head>\n"
    "<body>\n<h1>Hello</h1>\n<p>This paragraph explains the body element.</p>\n</body>\n"
    "</html>\n\nThe head holds metadata such as the title, and the body holds visible "
    "content."
)


def limits(workers: int = 1) -> Limits:
    return Limits(workers, 8 * 1024**3, 0, 256 * 1024**2, 1024**2, 600.0)


def params(template: Path) -> RuleParams:
    body, _ = read_policy(template)
    return validate_template(body)


V1 = params(TEMPLATE_V1)
V2 = params(TEMPLATE_V2)


# -- pinned rule sets ----------------------------------------------------------------------


def test_v2_template_is_the_pinned_human_reviewed_rule_set() -> None:
    body, _ = read_policy(TEMPLATE_V2)
    assert body["version"] == "cleaning_policy_v2"
    assert canonical.digest(body["policy"]) == POLICY_SECTION_DIGEST_V2
    review = body["policy"]["human_review"]
    assert review["successor_of"] == "cleaning_policy_v1"
    assert review["predecessor_policy_section_digest"] == POLICY_SECTION_DIGEST
    assert review["reviewed_examples"] == 119
    assert body["policy"]["outcomes"] == ["KEEP", "DROP"]
    assert V2.version == "cleaning_policy_v2" and V2.ruleset is RULESET_V2
    assert (V2.default_drop_at_least, V2.structured_drop_at_least) == (3, 3)
    assert V2.structured_review_equals is None and V2.ocr_review is False
    assert V2.full_html_drop is False
    assert (V2.replacement_at_least, V2.mojibake_at_least) == (8, 16)
    assert RULESET_V2.review_mask == 0 and "hard.full_html" not in RULESET_V2.ids
    assert V2.guardrails == V1.guardrails and V2.max_rows == 150


def test_v1_template_and_frozen_file_are_unchanged() -> None:
    body, raw = read_policy(TEMPLATE_V1)
    assert canonical.digest(body["policy"]) == POLICY_SECTION_DIGEST
    assert V1.version == "cleaning_policy_v1" and V1.ruleset is RULESET_V1
    assert (V1.default_drop_at_least, V1.structured_review_equals) == (2, 2)
    assert V1.full_html_drop is True and V1.ocr_review is True
    frozen = load_frozen(FROZEN_V1)  # the operator's audit-v3 freeze (content-free)
    assert frozen.version == "cleaning_policy_v1"
    assert frozen.digest == "1a49d4360433742fcdf01c2daf75623fa5b451893642a351b412b49a2e9c11ba"
    assert frozen.params.ruleset is RULESET_V1


def test_any_v2_rule_edit_refuses(tmp_path: Path) -> None:
    raw = TEMPLATE_V2.read_text(encoding="utf-8")
    for old, new in (
        ("signal_count_at_least: 3", "signal_count_at_least: 2"),
        ("at_least: 16", "at_least: 15"),
        ("reviewed_examples: 119", "reviewed_examples: 120"),
    ):
        edited = tmp_path / "edited.yaml"
        edited.write_text(raw.replace(old, new, 1), encoding="utf-8")
        with pytest.raises(PolicyError, match="human-reviewed v2 rule set"):
            validate_template(read_policy(edited)[0])


# -- decision semantics --------------------------------------------------------------------


def rules_for(component: str = "finewiki_en") -> ComponentRules:
    def make(metric: str) -> Threshold:
        comparator, cut = TEST_CUTS[metric]
        return Threshold(metric, METRIC_INDEX[metric], comparator == ">=", cut)

    ocr = tuple(make(m) for m in OCR_SIGNALS) if component == "finepdfs_en" else None
    return ComponentRules(component, tuple(make(m) for m in REP_SIGNALS), ocr)


def values(**overrides: float | int | None) -> list[float | int | None]:
    out: list[float | int | None] = [0.0 if spec.kind == "ratio" else 0 for spec in METRICS]
    out[METRIC_INDEX["compression_ratio"]] = 0.6
    for name, value in overrides.items():
        out[METRIC_INDEX[name]] = value
    return out


HITS: dict[str, float | int] = {
    "compression_ratio": 0.1,
    "ngram10_excess_ratio": 0.9,
    "dup_line_byte_ratio": 0.9,
    "dup_paragraph_byte_ratio": 0.9,
    "repeated_char_ratio": 0.9,
    "max_char_run": 500,
}


def signals(count: int, **extra: float | int | None) -> list[float | int | None]:
    return values(**dict(list(HITS.items())[:count]), **extra)


def decide(
    p: RuleParams,
    v: list[float | int | None],
    doc_class: str = "prose_like",
    component: str = "finewiki_en",
    flags: frozenset[int] = frozenset(),
) -> tuple[int, list[str]]:
    decision = evaluate(v, flags, doc_class, rules_for(component), p)
    return decision.outcome, rule_names(decision.rules, p.ruleset)


@pytest.mark.parametrize(
    ("count", "v1_default", "v2_default", "v1_structured", "v2_structured"),
    [
        (0, KEEP, KEEP, KEEP, KEEP),
        (1, KEEP, KEEP, KEEP, KEEP),
        (2, DROP, KEEP, REVIEW, KEEP),
        (3, DROP, DROP, DROP, DROP),
        (6, DROP, DROP, DROP, DROP),
    ],
)
def test_severe_repetition_thresholds_v1_vs_v2(
    count: int, v1_default: int, v2_default: int, v1_structured: int, v2_structured: int
) -> None:
    for doc_class in ("prose_like", "other", "markup_like", "empty"):
        assert decide(V1, signals(count), doc_class)[0] == v1_default, doc_class
        assert decide(V2, signals(count), doc_class)[0] == v2_default, doc_class
    for doc_class in ("code_like", "math_table_like"):
        assert decide(V1, signals(count), doc_class)[0] == v1_structured, doc_class
        assert decide(V2, signals(count), doc_class)[0] == v2_structured, doc_class
    if count == 3:
        assert decide(V2, signals(3))[1] == ["rep.severe_default"]
        assert decide(V2, signals(3), "code_like")[1] == ["rep.severe_structured"]


def test_finepdfs_ocr_v1_vs_v2() -> None:
    two = {"page_number_line_ratio": 0.5, "repeated_header_ratio": 0.5}
    pdf = "finepdfs_en"
    assert decide(V1, values(**two), component=pdf) == (REVIEW, ["ocr.finepdfs_review"])
    assert decide(V2, values(**two), component=pdf) == (KEEP, [])
    with_one = values(**two, max_char_run=99)
    assert decide(V1, with_one, component=pdf) == (DROP, ["ocr.finepdfs_with_repetition"])
    assert decide(V2, with_one, component=pdf) == (DROP, ["ocr.finepdfs_with_repetition"])
    assert decide(V2, values(page_number_line_ratio=0.5), component=pdf) == (KEEP, [])
    assert decide(V2, values(**two), component="common_pile_prose") == (KEEP, [])


@pytest.mark.parametrize(
    ("measurements", "v1", "v2", "v2_rules"),
    [
        ({"replacement_chars": 7}, KEEP, KEEP, []),
        ({"replacement_chars": 8}, REVIEW, DROP, ["enc.replacement_chars"]),
        ({"mojibake_hits": 15}, KEEP, KEEP, []),
        ({"mojibake_hits": 16}, REVIEW, DROP, ["enc.mojibake_hits"]),
        (
            {"replacement_chars": 8, "mojibake_hits": 16},
            DROP,
            DROP,
            ["enc.replacement_chars", "enc.mojibake_hits", "enc.replacement_and_mojibake"],
        ),
        (
            {"replacement_chars": 9, "c0_controls": 1},
            DROP,
            DROP,
            ["enc.replacement_chars", "enc.with_forbidden_controls"],
        ),
        (
            {"mojibake_hits": 20, "c1_controls": 1},
            DROP,
            DROP,
            ["enc.mojibake_hits", "enc.with_forbidden_controls"],
        ),
        ({"c0_controls": 5, "c1_controls": 5}, KEEP, KEEP, []),
        ({"nul": 1}, DROP, DROP, ["hard.nul"]),
        ({"noncharacters": 1}, DROP, DROP, ["hard.noncharacters"]),
        (
            {"bom": 3, "bidi_controls": 2, "zero_width": 4, "zwj_zwnj": 6, "urls": 30},
            KEEP,
            KEEP,
            [],
        ),
    ],
)
def test_encoding_and_hard_rules_v1_vs_v2(
    measurements: dict[str, int], v1: int, v2: int, v2_rules: list[str]
) -> None:
    assert decide(V1, values(**measurements))[0] == v1
    assert decide(V2, values(**measurements)) == (v2, v2_rules)


def analyzed(text: str) -> tuple[list[float | int | None], frozenset[int], str]:
    result = analyze(text, len(text.encode("utf-8")))
    return result.values, frozenset(result.flags), result.doc_class


def test_literal_complete_html_is_kept_by_v2_and_dropped_by_v1() -> None:
    from xlm.data.quality.policy import FLAG_INDEX

    for text in (HTML_TUTORIAL, DOCS["ultrax_ultrafineweb"]["html_page"]):
        v, flags, doc_class = analyzed(text)
        assert FLAG_INDEX["markup_full_html"] in flags
        assert decide(V1, v, doc_class, "ultrax_ultrafineweb", flags) == (
            DROP,
            ["hard.full_html"],
        )
        assert decide(V2, v, doc_class, "ultrax_ultrafineweb", flags) == (KEEP, [])
        decision = evaluate(v, flags, doc_class, rules_for("ultrax_ultrafineweb"), V2)
        strata = strata_of(decision, "ultrax_ultrafineweb", flags, v, V2)
        assert "protected.full_html_keep" in strata
    # A fenced code example never was full HTML; it stays KEEP in both.
    v, flags, doc_class = analyzed(DOCS["ultrax_ultrafineweb"]["code_example"])
    assert decide(V1, v, doc_class, "x", flags)[0] == KEEP
    assert decide(V2, v, doc_class, "x", flags)[0] == KEEP


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
def test_keep_protections_hold_under_v2(name: str) -> None:
    v, flags, doc_class = analyzed(DOCS["common_pile_prose"][name])
    assert decide(V2, v, doc_class, "common_pile_prose", flags) == (KEEP, [])


def test_v2_never_produces_review() -> None:
    for count in range(7):
        for doc_class in ("prose_like", "other", "markup_like", "empty", "code_like"):
            for component in ("finepdfs_en", "finewiki_en"):
                for extra in (
                    {},
                    {"page_number_line_ratio": 0.5, "repeated_header_ratio": 0.5},
                    {"replacement_chars": 8},
                    {"mojibake_hits": 16},
                ):
                    outcome, _ = decide(V2, signals(count, **extra), doc_class, component)
                    assert outcome in (KEEP, DROP)


# -- freeze ---------------------------------------------------------------------------------


def v2_layout() -> dict[str, list[dict[str, Any]]]:
    layout = cleaning_layout()
    layout["ultrax_ultrafineweb/tutorial/a"] = [
        document("ultrax_ultrafineweb-html_tutorial", HTML_TUTORIAL, 1)
    ]
    return layout


@pytest.fixture(scope="module")
def flow(tmp_path_factory: pytest.TempPathFactory) -> Iterator[dict[str, Path]]:
    """Authored corpus -> Phase-A audit -> frozen v1 -> frozen v2 (predecessor v1)."""
    root = tmp_path_factory.mktemp("cleaning-v2")
    manifest = build_corpus(root / "corpus", v2_layout())
    audit = root / "audit"
    run_audit(manifest, audit, limits=limits(), progress_interval=None)
    policies = root / "policies"
    policies.mkdir()
    frozen_v1 = policies / "v1.frozen.yaml"
    freeze_policy(TEMPLATE_V1, audit, frozen_v1)
    frozen_v2 = policies / "v2.frozen.yaml"
    freeze_policy(TEMPLATE_V2, audit, frozen_v2, predecessor=frozen_v1)
    cuts_v1 = override_thresholds(frozen_v1, policies / "v1.cuts.yaml")
    cuts_v2 = with_predecessor_cuts(frozen_v2, cuts_v1, policies / "v2.cuts.yaml")
    yield {
        "root": root,
        "manifest": manifest,
        "audit": audit,
        "frozen_v1": frozen_v1,
        "frozen_v2": frozen_v2,
        "cuts_v1": cuts_v1,
        "cuts_v2": cuts_v2,
    }


def with_predecessor_cuts(frozen_v2: Path, cuts_v1: Path, destination: Path) -> Path:
    """Test-only v2 variant carrying the authored cuts of a v1 variant (re-digested, with
    the predecessor thresholds digest of that v1 variant)."""
    body = parse_yaml(frozen_v2.read_bytes())
    v1 = parse_yaml(cuts_v1.read_bytes())
    body["thresholds"] = v1["thresholds"]
    body["provenance"]["predecessor"]["thresholds_digest"] = canonical.digest(v1["thresholds"])
    body["digest"] = canonical.self_digest(body)
    destination.write_bytes(dump_yaml(body))
    return destination


def test_v2_freeze_uses_the_same_audit_and_identical_cuts(flow: dict[str, Path]) -> None:
    v1 = parse_yaml(flow["frozen_v1"].read_bytes())
    v2 = parse_yaml(flow["frozen_v2"].read_bytes())
    assert v2["version"] == "cleaning_policy_v2" and v2["status"] == "FROZEN"
    assert v2["thresholds"] == v1["thresholds"]  # every component cut preserved exactly
    assert v2["provenance"]["phase_a"] == v1["provenance"]["phase_a"]
    assert v2["provenance"]["candidate_policy"] == v1["provenance"]["candidate_policy"]
    compiled_v1 = load_frozen(flow["frozen_v1"])
    predecessor = v2["provenance"]["predecessor"]
    assert predecessor == {
        "version": "cleaning_policy_v1",
        "policy_digest": compiled_v1.digest,
        "file_sha256": hashlib.sha256(flow["frozen_v1"].read_bytes()).hexdigest(),
        "phase_a_receipt_digest": v1["provenance"]["phase_a"]["receipt_digest"],
        "thresholds_digest": canonical.digest(v1["thresholds"]),
    }
    assert v2["policy"]["human_review"]["successor_of"] == "cleaning_policy_v1"
    raw = flow["frozen_v2"].read_bytes()
    assert raw.startswith(b"# XLM cleaning policy v2 (human-reviewed successor of v1)")
    for canary in (*CANARIES, "My first page", "Survey 1000"):
        assert canary.encode("utf-8") not in raw
    assert load_frozen(flow["frozen_v2"]).params.ruleset is RULESET_V2


def test_v2_freeze_refusals(flow: dict[str, Path], tmp_path: Path) -> None:
    destination = tmp_path / "v2.yaml"
    with pytest.raises(PolicyError, match="requires --predecessor"):
        freeze_policy(TEMPLATE_V2, flow["audit"], destination)
    with pytest.raises(PolicyError, match="must be a FROZEN v1"):
        freeze_policy(TEMPLATE_V2, flow["audit"], destination, predecessor=flow["frozen_v2"])
    with pytest.raises(PolicyError, match="v1 policy has no predecessor"):
        freeze_policy(TEMPLATE_V1, flow["audit"], destination, predecessor=flow["frozen_v1"])
    # A v1 predecessor frozen from a DIFFERENT Phase-A audit refuses.
    other = build_corpus(tmp_path / "other", cleaning_layout(filler=13))
    run_audit(other, tmp_path / "other-audit", limits=limits(), progress_interval=None)
    foreign = tmp_path / "foreign-v1.yaml"
    freeze_policy(TEMPLATE_V1, tmp_path / "other-audit", foreign)
    with pytest.raises(PolicyError, match="different Phase-A audit"):
        freeze_policy(TEMPLATE_V2, flow["audit"], destination, predecessor=foreign)
    # A predecessor whose cuts were changed (and re-digested) refuses.
    changed = override_thresholds(flow["frozen_v1"], tmp_path / "changed-v1.yaml")
    with pytest.raises(PolicyError, match="differ from the predecessor"):
        freeze_policy(TEMPLATE_V2, flow["audit"], destination, predecessor=changed)
    assert not destination.exists()
    # A frozen v2 whose cuts drift from its recorded predecessor refuses at load.
    body = parse_yaml(flow["frozen_v2"].read_bytes())
    body["thresholds"]["finewiki_en"]["repetition"]["max_char_run"] = None
    body["digest"] = canonical.self_digest(body)
    drifted = tmp_path / "drifted.yaml"
    drifted.write_bytes(dump_yaml(body))
    with pytest.raises(PolicyError, match="differ from the predecessor's frozen cuts"):
        load_frozen(drifted)


def test_v2_freeze_cli(flow: dict[str, Path], tmp_path: Path, capsys: Any) -> None:
    destination = tmp_path / "cli-v2.yaml"
    code = main(
        [
            "clean-freeze-policy",
            "--template",
            str(TEMPLATE_V2),
            "--audit-output",
            str(flow["audit"]),
            "--predecessor",
            str(flow["frozen_v1"]),
            "--destination",
            str(destination),
        ]
    )
    result = json.loads(capsys.readouterr().out)
    assert code == 0 and result["frozen"] is True and result["version"] == "cleaning_policy_v2"
    assert destination.read_bytes() == flow["frozen_v2"].read_bytes()  # deterministic


# -- dry run --------------------------------------------------------------------------------


def oracle(flow: dict[str, Path], policy: Path) -> dict[str, Any]:
    compiled = load_frozen(policy)
    manifest = json.loads(flow["manifest"].read_bytes())
    root = Path(manifest["data_root"])
    outcomes: dict[str, list[list[int]]] = {}
    by_doc: dict[str, int] = {}
    for entry in manifest["files"]:
        component = entry["component"]
        for line in (root / entry["path"]).read_bytes().splitlines():
            row = json.loads(line)
            v, flags, doc_class = analyzed(row["text"])
            decision = evaluate(v, flags, doc_class, compiled.rules_for(component), compiled.params)
            size = len(row["text"].encode("utf-8"))
            by_doc[row["doc_id"]] = decision.outcome
            for scope in ("global", component):
                cell = outcomes.setdefault(scope, [[0, 0] for _ in OUTCOMES])
                cell[decision.outcome][0] += 1
                cell[decision.outcome][1] += size
    return {"outcomes": outcomes, "by_doc": by_doc}


@pytest.fixture(scope="module")
def dry_v2(flow: dict[str, Path], tmp_path_factory: pytest.TempPathFactory) -> Path:
    output = tmp_path_factory.mktemp("dry-v2") / "out"
    run_dry_run(flow["manifest"], output, flow["cuts_v2"], limits=limits(), progress_interval=None)
    return output


def test_v2_dry_run_exact_accounting_and_no_review(flow: dict[str, Path], dry_v2: Path) -> None:
    expected = oracle(flow, flow["cuts_v2"])
    report = json.loads((dry_v2 / "cleaning-dry-run.json").read_bytes())
    assert report["policy"]["version"] == "cleaning_policy_v2"
    for scope, summary in [("global", report["global"]), *report["components"].items()]:
        got = [[summary["outcomes"][o]["docs"], summary["outcomes"][o]["bytes"]] for o in OUTCOMES]
        assert got == expected["outcomes"][scope], scope
        assert summary["review_union"]["docs"] == 0
    by_rule = json.loads((dry_v2 / "cleaning-by-rule.json").read_bytes())["rules"]
    assert set(by_rule) == set(RULESET_V2.ids)
    assert all(entry["action"] == "DROP" for entry in by_rule.values())
    receipt = load_receipt(dry_v2)
    assert receipt["binding"]["cleaning_policy"]["version"] == "cleaning_policy_v2"
    assert receipt["cleaned_corpus_written"] is False


def test_v1_to_v2_decision_changes_on_the_authored_corpus(flow: dict[str, Path]) -> None:
    v1 = oracle(flow, flow["cuts_v1"])["by_doc"]
    v2 = oracle(flow, flow["cuts_v2"])["by_doc"]
    changed = {doc: (OUTCOMES[v1[doc]], OUTCOMES[v2[doc]]) for doc in v1 if v1[doc] != v2[doc]}
    assert changed == {
        "finewiki_en-generated_loop": ("DROP", "KEEP"),  # default exactly 2 signals
        "finepdfs_en-ocr_review": ("REVIEW", "KEEP"),  # OCR-only
        "finepdfs_en-single_chars": ("REVIEW", "KEEP"),  # OCR-only
        "essential_science-replacement_8": ("REVIEW", "DROP"),
        "essential_science-mojibake_16": ("REVIEW", "DROP"),
        "essential_science-code_two": ("REVIEW", "KEEP"),  # structured exactly 2
        "essential_science-table_two": ("REVIEW", "KEEP"),  # structured exactly 2
        "ultrax_ultrafineweb-html_page": ("DROP", "KEEP"),  # full HTML alone
        "ultrax_ultrafineweb-html_tutorial": ("DROP", "KEEP"),  # literal HTML tutorial
        "ultrax_ultrafineweb-mojibake_16": ("REVIEW", "DROP"),
    }
    assert REVIEW not in v2.values()


def test_v2_review_manifest_uses_v2_strata_only(dry_v2: Path) -> None:
    rows = read_review_rows(dry_v2 / REVIEW_MANIFEST)
    assert 0 < len(rows) <= 150
    for row in rows:
        check_review_row(row, RULESET_V2)
        assert row["outcome"] in ("KEEP", "DROP")
        assert not any(s.startswith("review.") for s in row["strata"])
    strata = {s for row in rows for s in row["strata"]}
    for name in (
        "protected.full_html_keep",
        "control.default_two_signals",
        "control.structured_two_signals",
        "control.finepdfs_ocr_only",
        "drop.enc.replacement_chars",
        "drop.enc.mojibake_hits",
        "drop.hard.nul",
    ):
        assert name in strata, name
    report = json.loads((dry_v2 / "cleaning-dry-run.json").read_bytes())
    names = [entry["stratum"] for entry in report["review_manifest"]["strata"]]
    assert not any(name.endswith(".REVIEW") for name in names)


def test_v2_dry_run_is_deterministic_verified_and_content_free(
    flow: dict[str, Path], dry_v2: Path, tmp_path: Path
) -> None:
    again = tmp_path / "w2"
    run_dry_run(flow["manifest"], again, flow["cuts_v2"], limits=limits(2), progress_interval=None)
    for name in ARTIFACTS:
        assert (again / name).read_bytes() == (dry_v2 / name).read_bytes(), name
    assert verify_dry_run(flow["manifest"], dry_v2, flow["cuts_v2"])["verified"] is True
    payloads = [(dry_v2 / name).read_bytes() for name in ARTIFACTS]
    payloads += [zlib.decompress(p.read_bytes()) for p in (dry_v2 / "units").iterdir()]
    for payload in payloads:
        for canary in (*CANARIES, "My first page", "lost � glyph"):
            assert canary.encode("utf-8") not in payload, canary
    destination = tmp_path / "review"
    result = materialize_review(
        dry_v2, destination, limits=ReviewLimits(), strata=["protected.full_html_keep"]
    )
    assert result["materialized"] >= 1
