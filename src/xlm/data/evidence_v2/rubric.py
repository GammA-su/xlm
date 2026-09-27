"""Frozen human review rubric: 18 categorical dimensions, exact (protocol §8).

Wording and anchors are transcribed verbatim from the normative protocol.
No numeric 1-5 transformation. ``uncertain`` is allowed on every judgment
and requires a short reason. ``not_applicable`` only where listed below.
Rows without available text take ``not_reviewed`` in all 18 fields.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

RUBRIC_VERSION = "essential-web-evidence-v2.0-rubric-1"
UNCERTAIN = "uncertain"
NOT_APPLICABLE = "not_applicable"
NOT_REVIEWED = "not_reviewed"

# Dimensions allowing `not_applicable`, per the protocol table.
NOT_APPLICABLE_DIMS = frozenset(
    {
        "english_fluency",
        "apparent_errors",
        "practical_actionability",
        "qa_answer",
        "prose_coherence",
    }
)

DIMENSIONS: tuple[dict[str, Any], ...] = (
    {
        "n": 1,
        "name": "english_predominance",
        "title": "English predominance",
        "levels": ("predominant", "not_predominant"),
        "anchors": {
            "predominant": ">50% of natural-language words appear English",
            "not_predominant": "<=50%. Exclude code, formulas, URLs, numbers and "
            "proper-name-only spans; if no assessable natural language, use uncertain. "
            "Estimate as human judgment, not fastText.",
        },
    },
    {
        "n": 2,
        "name": "english_fluency",
        "title": "English fluency",
        "levels": ("fluent", "impaired_but_readable", "unreadable"),
        "anchors": {
            "fluent": "English passages readable throughout, at most isolated local errors",
            "impaired_but_readable": "recurrent errors require rereading but central "
            "meaning recoverable",
            "unreadable": "central meaning cannot be reliably recovered. "
            "not_applicable only when no English passage exists. Judge linguistic "
            "fluency separately from missing-context/extraction labels.",
        },
    },
    {
        "n": 3,
        "name": "boilerplate_fraction",
        "title": "Boilerplate/irrelevant fraction",
        "levels": ("zero", "low", "moderate", "high", "dominant"),
        "anchors": {
            "zero": "0%",
            "low": "(0,10%]",
            "moderate": "(10,25%]",
            "high": "(25,50%]",
            "dominant": "(50,100%] of visible non-whitespace characters that is "
            "navigation, ads, templated clutter, duplication or unrelated inserted "
            "material. Disagreement with the topic/opinion is not irrelevance.",
        },
    },
    {
        "n": 4,
        "name": "boilerplate_severity",
        "title": "Boilerplate severity",
        "levels": ("none", "peripheral", "intrusive_separable", "obscuring"),
        "anchors": {
            "none": "absent",
            "peripheral": "present but main content reads continuously",
            "intrusive_separable": "interrupts reading, useful passage boundaries "
            "still identifiable",
            "obscuring": "useful content cannot be reliably separated. A low "
            "fraction can still be intrusive.",
        },
    },
    {
        "n": 5,
        "name": "standalone_completeness",
        "title": "Standalone completeness",
        "levels": ("complete", "minor_gaps", "incomplete"),
        "anchors": {
            "complete": "stated main purpose fulfilled with necessary textual context",
            "minor_gaps": "local omissions but central purpose still usable",
            "incomplete": "missing essential premise, result, answer, ending or "
            "instructions prevents use.",
        },
    },
    {
        "n": 6,
        "name": "figure_dependency",
        "title": "Missing image/figure dependency",
        "levels": ("none", "helpful_only", "essential_missing"),
        "anchors": {
            "none": "none required for central meaning",
            "helpful_only": "absent visual would aid but text suffices",
            "essential_missing": "central explanation/instruction cannot be interpreted "
            "without absent image/figure. Unknown existence/dependence => uncertain.",
        },
    },
    {
        "n": 7,
        "name": "external_link_dependency",
        "title": "External-link dependency",
        "levels": ("none", "optional_support", "essential_external"),
        "anchors": {
            "none": "no external material required",
            "optional_support": "citations/further reading optional to use text",
            "essential_external": "following a link is needed for central "
            "content/steps/answer. Do not follow links during review.",
        },
    },
    {
        "n": 8,
        "name": "actual_genre",
        "title": "Actual genre",
        "levels": (
            "academic",
            "explanatory",
            "technical_reference",
            "instruction",
            "faq",
            "support",
            "discussion_qa",
            "news",
            "personal_commentary",
            "creative",
            "nonfiction_narrative",
            "promotional",
            "listing_structured",
            "other",
            "mixed",
        ),
        "anchors": {
            "academic": "research report/scholarly argument",
            "explanatory": "topic exposition",
            "technical_reference": "lookup/specification/API reference",
            "instruction": "procedure/tutorial",
            "faq": "curated question-answer list",
            "support": "troubleshooting/support response",
            "discussion_qa": "forum question and replies",
            "news": "event reporting",
            "personal_commentary": "personal narrative/opinion/blog",
            "creative": "fiction/poetry/drama",
            "nonfiction_narrative": "connected factual narrative/essay",
            "promotional": "sales/advertisement",
            "listing_structured": "index/catalog/table without sustained exposition",
            "other": "requires explanation",
            "mixed": "no purpose dominates. Format alone does not determine genre.",
        },
    },
    {
        "n": 9,
        "name": "component_fit",
        "title": "Essential component fit",
        "levels": ("science", "practical", "prose", "none_mixed"),
        "anchors": {
            "science": "substantive scientific explanation, evidence or reasoning",
            "practical": "usable instruction, problem-solving or reference",
            "prose": "sustained coherent narrative/exposition",
            "none_mixed": "none fits, or multiple fit without a dominant purpose "
            "(required subreason `none` or `mixed`). Judge content, not upstream "
            "labels. Scientific step-by-step material with primarily instructional "
            "purpose can fit practical.",
        },
    },
    {
        "n": 10,
        "name": "promotion",
        "title": "Promotion/commercial",
        "levels": ("none", "incidental", "substantial", "dominant"),
        "anchors": {
            "none": "no promotional purpose",
            "incidental": "attribution/product mention supports independent substance",
            "substantial": "persuasion/sales mixed with independently useful substance",
            "dominant": "central purpose advertising, sales, conversion or affiliate "
            "promotion. Product-specific documentation is not automatically promotion.",
        },
    },
    {
        "n": 11,
        "name": "science_substance",
        "title": "Science substance",
        "levels": (
            "none",
            "mention_only",
            "substantive_explanation",
            "substantive_evidence_reasoning",
        ),
        "anchors": {
            "none": "no scientific explanatory claims",
            "mention_only": "terminology or assertions without meaningful explanation/evidence",
            "substantive_explanation": "meaningful mechanisms, concepts or scientific "
            "relationships explained",
            "substantive_evidence_reasoning": "methods/data, explicit inferential "
            "reasoning or evidence-based comparison is developed. This scale measures "
            "substance, not correctness; technical vocabulary alone earns no high label.",
        },
    },
    {
        "n": 12,
        "name": "apparent_errors",
        "title": "Apparent factual/technical errors",
        "levels": ("none_detected", "minor_local", "material"),
        "anchors": {
            "none_detected": "within declared expertise no apparent error found, not certification",
            "minor_local": "localized issue does not overturn the central explanation/action",
            "material": "error undermines central claim, mechanism, answer or action. "
            "not_applicable: no checkable factual/technical claims (e.g. pure fiction). "
            "Suspected error outside expertise => uncertain with suspicion noted.",
        },
    },
    {
        "n": 13,
        "name": "checkability",
        "title": "Checkability/verifiability",
        "levels": (
            "no_checkable_claims",
            "checkable_from_document",
            "external_check_identifiable",
            "insufficient_basis",
        ),
        "anchors": {
            "no_checkable_claims": "no factual/technical propositions",
            "checkable_from_document": "central claims can be checked against supplied "
            "derivation/data or explicit internal evidence",
            "external_check_identifiable": "meaningful external check has identifiable "
            "source, method or sufficiently precise claim",
            "insufficient_basis": "claims too vague or sources/methods too unspecified "
            "for meaningful checking. Checkability is not a truth judgment; no browsing.",
        },
    },
    {
        "n": 14,
        "name": "practical_actionability",
        "title": "Practical actionability",
        "levels": ("usable", "partially_usable", "unusable"),
        "anchors": {
            "usable": "sufficient steps, conditions or reference to perform stated task "
            "with expected prerequisite knowledge",
            "partially_usable": "some usable information but essential step/condition unresolved",
            "unusable": "stated task cannot be carried out from text. "
            "not_applicable: no instructional/reference/problem-solving purpose. "
            "State assumed prerequisites.",
        },
    },
    {
        "n": 15,
        "name": "qa_answer",
        "title": "Q&A answer",
        "levels": ("usable_answer", "partial_answer", "no_usable_answer"),
        "anchors": {
            "usable_answer": "at least one responsive answer adequately addresses "
            "the main question",
            "partial_answer": "responsive but essential issue unresolved",
            "no_usable_answer": "question only, irrelevant replies, or substantively "
            "unusable answer. Apply also to FAQs/support with a question-answer "
            "purpose. not_applicable: no question-answer purpose. For multi-question "
            "documents, judge the main purpose and note unanswered subordinate questions.",
        },
    },
    {
        "n": 16,
        "name": "prose_coherence",
        "title": "Prose coherence",
        "levels": ("coherent", "local_breaks", "incoherent"),
        "anchors": {
            "coherent": "sustained understandable narrative/exposition",
            "local_breaks": "localized discontinuities but overall progression recoverable",
            "incoherent": "central progression cannot be recovered. not_applicable: "
            "primarily reference/code/table/short Q&A with no sustained-prose purpose.",
        },
    },
    {
        "n": 17,
        "name": "fragmentation",
        "title": "Fragmentation/extraction",
        "levels": ("none_visible", "minor", "major_recoverable", "severe"),
        "anchors": {
            "none_visible": "no visible defect",
            "minor": "local duplication/formatting break, central meaning intact",
            "major_recoverable": "repeated fragmentation/interleaving but central "
            "passage recoverable",
            "severe": "missing/interleaved/garbled content prevents reliable recovery. "
            "Mark defect types separately: truncation, duplication, interleaving, "
            "encoding, markup, broken table/code, other. Multiple types allowed.",
        },
    },
    {
        "n": 18,
        "name": "overall_disposition",
        "title": "Overall disposition",
        "levels": (
            "acceptable_as_is",
            "requires_separately_specified_repair",
            "reject",
        ),
        "anchors": {
            "acceptable_as_is": "serves a science/practical/prose purpose as supplied, "
            "predominant readable English, essential content usable, no detected "
            "material error or destructive extraction/dependency",
            "requires_separately_specified_repair": "useful core exists but a specific "
            "bounded repair is necessary, describe it (no repair performed)",
            "reject": "no suitable core or central defect cannot be repaired from "
            "supplied text. uncertain: expertise/evidence/disagreement prevents "
            "disposition. No arithmetic sum and no forced acceptance from label averages.",
        },
    },
)

FRAGMENTATION_DEFECT_TYPES = frozenset(
    {
        "truncation",
        "duplication",
        "interleaving",
        "encoding",
        "markup",
        "broken table/code",
        "other",
    }
)

DIM_BY_NAME: dict[str, dict[str, Any]] = {d["name"]: d for d in DIMENSIONS}
FORM_FIELDS = frozenset(
    {
        "review_id",
        "reviewer",
        "rubric_version",
        "submitted_utc",
        "expertise",
        "confidence",
        "confidence_reason",
        "dimensions",
        "dimension_notes",
        "disposition_rationale",
        "known_essential_gaps",
        "known_material_error",
    }
)


class RubricError(ValueError):
    """Malformed or inconsistent review form: return for clarification."""


def blank_dimensions(available: bool) -> dict[str, Any]:
    """Blank dimension judgments: None when reviewable, not_reviewed when not."""
    if available:
        return {d["name"]: None for d in DIMENSIONS}
    return {d["name"]: NOT_REVIEWED for d in DIMENSIONS}


def validate_form(form: Mapping[str, Any]) -> list[str]:
    """Return clarification errors; empty means the form is well-formed.

    Never silently corrects. Checks: exact top-level fields, confidence
    rules, per-dimension levels (uncertain requires a reason,
    not_applicable only where listed, not_reviewed all-or-nothing), and
    the acceptable_as_is consistency rule.
    """
    errors: list[str] = []
    unknown = sorted(k for k in form if k not in FORM_FIELDS)
    if unknown:
        errors.append(f"unknown form fields: {unknown}")
    if form.get("rubric_version") != RUBRIC_VERSION:
        errors.append("rubric_version mismatch")
    confidence = form.get("confidence")
    if confidence not in ("sufficient", "limited"):
        errors.append("confidence must be sufficient or limited")
    elif confidence == "limited" and not form.get("confidence_reason"):
        errors.append("limited confidence requires a reason")
    dims = form.get("dimensions")
    if not isinstance(dims, dict) or sorted(dims) != sorted(DIM_BY_NAME):
        errors.append("dimensions must cover exactly the 18 frozen names")
        return errors
    notes = form.get("dimension_notes")
    if not isinstance(notes, dict):
        errors.append("dimension_notes must be a mapping")
        notes = {}
    reviewed_flags = [v != NOT_REVIEWED for v in dims.values()]
    if any(reviewed_flags) and not all(reviewed_flags):
        errors.append("not_reviewed must cover all 18 fields or none")
    for name, dim in DIM_BY_NAME.items():
        value = dims[name]
        allowed = set(dim["levels"]) | {UNCERTAIN}
        if name in NOT_APPLICABLE_DIMS:
            allowed.add(NOT_APPLICABLE)
        if value is None:
            errors.append(f"{name}: missing judgment")
            continue
        if value == NOT_REVIEWED:
            continue
        if value == NOT_APPLICABLE and name not in NOT_APPLICABLE_DIMS:
            errors.append(f"{name}: not_applicable is not authorized here")
            continue
        if value not in allowed:
            errors.append(f"{name}: {value!r} is not an allowed level")
            continue
        if value == UNCERTAIN and not (isinstance(notes, dict) and notes.get(name)):
            errors.append(f"{name}: uncertain requires a short reason")
    if dims.get("overall_disposition") == "acceptable_as_is":
        if form.get("known_essential_gaps") or form.get("known_material_error"):
            errors.append(
                "acceptable_as_is with known essential gaps or material error "
                "requires correction or uncertainty"
            )
    if not form.get("disposition_rationale"):
        errors.append("disposition rationale is required")
    return errors


def disagreement_schema() -> dict[str, Any]:
    """Field schema for one item-by-dimension disagreement record."""
    return {
        "review_id": "opaque ew2- id",
        "dimension": "one of the 18 frozen names",
        "label_a": "reviewer-1 label",
        "label_b": "reviewer-2 label",
        "uncertain_either": "bool",
    }


def adjudication_schema() -> dict[str, Any]:
    """Field schema for one adjudicated dimension."""
    return {
        "review_id": "opaque ew2- id",
        "dimension": "one of the 18 frozen names",
        "blind_label": "adjudicator unaided assessment",
        "rationales_seen": "two anonymized rationales (after blind record)",
        "final_label": "final categorical label",
        "final_rationale": "rationale with supporting text offsets",
        "resolved": "bool; unresolved stays uncertain, no majority rule",
    }
