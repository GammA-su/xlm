# Requires: operator-run only, offline (experiment, no network).
"""Bounded offline experimental selector sweep for Essential-Web policies A-D.

Implements EXACTLY the frozen hypotheses in
``recipes/selectors/essential_web_selector_sweep_v1.yaml`` (authoritative
definitions live there; this file is the mechanical evaluator). Experiment
only: nothing here is a final selector, approval, or admission.

Pipeline: validate bundle/execution binding (self-digests, cross-digests,
projection, revision, combined input hash, 4096 records, 8 unique plan
hashes, stratum order) -> stream selected_records.jsonl with hard caps ->
validity gate, shared gates, component predicates, deterministic precedence
-> deterministic JSON artifacts plus a Markdown summary. Training text is
never read (popped and ignored) and never emitted.

Fail-closed throughout: any binding mismatch, cap breach, corrupt line,
duplicate locator, conservation mismatch, or final overlap exits nonzero
with no partial outputs left behind (artifacts are written atomically at
the end, only after every check passes).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
import time
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

TOOL_ID = "essential-web-selector-sweep"
TOOL_VERSION = "2"
# Report-output schema version, recorded in crosstabs.json and
# diagnostics.json. Bumped to 2 for the corrected reporting pass:
# primary-path FDC level labels, publisher metadata word-count
# distributions, within-component genre-share denominators, and the
# endpoint-RSS rename. Selector assignment logic is unchanged.
REPORT_SCHEMA_VERSION = 2
WORDS_PATH = "quality_signals.red_pajama_v2.rps_doc_word_count"
POLICIES = ("A", "B", "C", "D")
TIERS = ("normal", "strict")
FINAL_COMPONENTS = (
    "essential_science",
    "essential_practical",
    "essential_prose",
    "unassigned",
    "rejected",
)
PINNED_REVISION = "ce4eccc7e9604667b6d7f32cb6274b8b41f3113d"
EXPECTED_PROJECTION = ["eai_taxonomy", "quality_signals"]
N_FDC_TOP = 25


class SweepError(RuntimeError):
    """Any binding, cap, input, or accounting failure: exit nonzero."""


def _canonical(value: Any) -> str:
    # Same canonical scheme as the recon manifest digest: sorted keys,
    # compact separators, raw UTF-8. Shared byte-level contract.
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _manifest_digest(body: Mapping[str, Any]) -> str:
    return _sha256_text(_canonical({k: v for k, v in body.items() if k != "digest"}))


def policy_digest_of(spec: Mapping[str, Any]) -> str:
    """Canonical digest of a policy spec mapping (byte-stable)."""
    return _sha256_text(_canonical(spec))


def load_policy_spec(path: Path) -> tuple[dict[str, Any], str]:
    """Load the frozen spec and return (spec, canonical digest)."""
    import yaml

    try:
        spec = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise SweepError(f"cannot read policy spec '{path}': {exc}") from exc
    if not isinstance(spec, dict):
        raise SweepError("policy spec must be a YAML mapping")
    return spec, policy_digest_of(spec)


def default_policy_spec_path() -> Path:
    return (
        Path(__file__).resolve().parents[1]
        / "recipes"
        / "selectors"
        / "essential_web_selector_sweep_v1.yaml"
    )


# --------------------------------------------------------------------------
# Row access helpers (same tri-state path semantics as the recon analyzer:
# ok / missing / null / malformed non-mapping parent).
# --------------------------------------------------------------------------

MISSING = "missing"
NULL = "null"
MALFORMED = "malformed"


def get_path(record: Mapping[str, Any], path: str) -> tuple[str, Any]:
    """Return (status, value) walking dotted struct fields."""
    value: Any = record
    for part in path.split("."):
        if value is None:
            return NULL, None
        if not isinstance(value, Mapping):
            return MALFORMED, None
        if part not in value:
            return MISSING, None
        value = value[part]
    return ("ok", value) if value is not None else (NULL, None)


_FIELD_PATHS = {
    "f": "eai_taxonomy.free_decimal_correspondence.primary.code",
    "d": "eai_taxonomy.document_type_v2.primary.label",
    "k": "eai_taxonomy.bloom_knowledge_domain.primary.label",
    "a": "eai_taxonomy.extraction_artifacts.primary.label",
    "m": "eai_taxonomy.missing_content.primary.label",
    "t": "eai_taxonomy.technical_correctness.primary.label",
    "e": "quality_signals.fasttext.english",
}

_LABEL_FIELDS = ("d", "k", "a", "m", "t")


def _label_value(record: Mapping[str, Any], field: str) -> tuple[str, Any]:
    """Fetch a label field: (ok, stripped string) or (reason-kind, None).

    Labels must be non-empty strings. Booleans and numbers are wrong types;
    whitespace-only is empty. Unknown-vs-excluded is decided by the caller
    against the spec universes.
    """
    status, value = get_path(record, _FIELD_PATHS[field])
    if status != "ok":
        return status, None
    if not isinstance(value, str):
        return "bad_type", None
    text = value.strip()
    if not text:
        return "empty", None
    return "ok", text


def validate_row(
    record: Mapping[str, Any], spec: Mapping[str, Any]
) -> tuple[dict[str, Any], list[str], dict[str, str]]:
    """Validate one content row; return (validated fields, ALL reasons, unknowns).

    Fields holds only validated entries (partial when rejected, so rejected
    populations keep their measurable values). Reasons is empty iff the row
    may proceed to the shared gates. Unknowns maps classifier to the
    offending observed value for unknown_label cases. FDC codes stay
    strings, never floats.
    """
    reasons: list[str] = []
    fields: dict[str, Any] = {}
    unknowns: dict[str, str] = {}

    tax_status, tax_value = get_path(record, "eai_taxonomy")
    if tax_status != "ok" or not isinstance(tax_value, Mapping):
        reasons.append("missing_eai_taxonomy" if tax_status != "ok" else "malformed_eai_taxonomy")
    qual_status, qual_value = get_path(record, "quality_signals")
    if qual_status != "ok" or not isinstance(qual_value, Mapping):
        reasons.append(
            "missing_quality_signals" if qual_status != "ok" else "malformed_quality_signals"
        )

    status, fdc = get_path(record, _FIELD_PATHS["f"])
    if status == MALFORMED:
        reasons.append("malformed_fdc_parent")
    elif status != "ok":
        reasons.append("missing_fdc")
    elif not isinstance(fdc, str):
        reasons.append("bad_type_fdc")
    else:
        code = fdc.strip()
        if not code:
            reasons.append("empty_fdc")
        elif not re.fullmatch(spec["fdc_syntax"], code):
            reasons.append("invalid_fdc_syntax")
        else:
            fields["f"] = code

    status, english = get_path(record, _FIELD_PATHS["e"])
    if status == MALFORMED:
        reasons.append("malformed_english_parent")
    elif status != "ok":
        reasons.append("missing_english")
    elif isinstance(english, bool) or not isinstance(english, (int, float)):
        reasons.append("bad_type_english")
    else:
        score = float(english)
        if not math.isfinite(score):
            reasons.append("nonfinite_english")
        elif not spec["english_min"] <= score <= spec["english_max"]:
            reasons.append("english_out_of_range")
        else:
            fields["e"] = score

    for field in _LABEL_FIELDS:
        kind, value = _label_value(record, field)
        if kind != "ok":
            if kind == "bad_type":
                reasons.append(f"bad_type_label:{field}")
            elif kind == "empty":
                reasons.append(f"empty_label:{field}")
            elif kind == MALFORMED:
                reasons.append(f"malformed_label_parent:{field}")
            else:
                reasons.append(f"missing_label:{field}")
            continue
        assert isinstance(value, str)
        universes: dict[str, list[str]] = {
            "d": list(spec["doctype_union"]) + list(spec["doctype_known_excluded"]),
            "k": list(spec["knowledge_labels"]),
            "a": list(spec["artifacts_known"]),
            "m": list(spec["missing_known"]),
            "t": list(spec["correctness_allowed"]) + list(spec["correctness_rejected"]),
        }
        if value not in universes[field]:
            reasons.append(f"unknown_label:{field}")
            unknowns[field] = value
        else:
            fields[field] = value

    if not reasons:
        head = fields["f"].split(".", 1)[0]
        fields["prefix3"] = head
        fields["digit1"] = fields["f"][0]
        fields["digit2"] = fields["f"][:2]
    return fields, reasons, unknowns


# --------------------------------------------------------------------------
# Shared quality gates (conjunctive; empty reason list means pass).
# --------------------------------------------------------------------------


def _gate(
    features: Mapping[str, Any],
    gate: Mapping[str, Any],
    spec: Mapping[str, Any],
) -> list[str]:
    reasons: list[str] = []
    if not features["e"] >= gate["english_min"]:
        reasons.append("gate_english")
    if features["a"] not in gate["artifacts"]:
        reasons.append("gate_artifacts")
    if features["m"] not in gate["missing"]:
        reasons.append("gate_missing_content")
    if features["t"] not in spec["correctness_allowed"]:
        reasons.append("gate_correctness")
    if features["d"] not in spec["doctype_union"]:
        reasons.append("gate_doctype")
    return reasons


def gate_gn(features: Mapping[str, Any], spec: Mapping[str, Any]) -> list[str]:
    """Normal clean gate."""
    return _gate(features, spec["gates"]["GN"], spec)


def gate_gs(features: Mapping[str, Any], spec: Mapping[str, Any]) -> list[str]:
    """Strict gate."""
    return _gate(features, spec["gates"]["GS"], spec)


def gate_gd(features: Mapping[str, Any], spec: Mapping[str, Any]) -> list[str]:
    """Artifact-sensitivity normal gate."""
    return _gate(features, spec["gates"]["GD"], spec)


_REASON_FOR_COND = {
    "english": "gate_english",
    "artifacts": "gate_artifacts",
    "missing": "gate_missing_content",
    "correctness": "gate_correctness",
    "doctype": "gate_doctype",
}


def _gate_conditions(
    spec: Mapping[str, Any], gate_name: str, fields: Mapping[str, Any]
) -> dict[str, bool]:
    """Per-condition gate results (shared by attrition and diagnostics)."""
    gate = spec["gates"][gate_name]
    return {
        "english": bool(fields["e"] >= gate["english_min"]),
        "artifacts": bool(fields["a"] in gate["artifacts"]),
        "missing": bool(fields["m"] in gate["missing"]),
        "correctness": bool(fields["t"] in spec["correctness_allowed"]),
        "doctype": bool(fields["d"] in spec["doctype_union"]),
    }


_GATES = {"GN": gate_gn, "GS": gate_gs, "GD": gate_gd}


# --------------------------------------------------------------------------
# Component predicates (pure functions of validated features + frozen spec).
# --------------------------------------------------------------------------


def predicate_s5(features: Mapping[str, Any], spec: Mapping[str, Any]) -> bool:
    """F starts with 5 and D is a science genre."""
    code: str = features["f"]
    return code[0] == "5" and features["d"] in spec["predicates"]["s5_genres"]


def predicate_s61(features: Mapping[str, Any], spec: Mapping[str, Any]) -> bool:
    """Exact 61x allowlist prefix with genre, knowledge, and correctness."""
    pred = spec["predicates"]
    return (
        features["prefix3"] in pred["s61_prefixes"]
        and features["d"] in pred["s61_genres"]
        and features["k"] in pred["s61_knowledge"]
        and features["t"] in pred["s61_correctness"]
    )


def predicate_p(features: Mapping[str, Any], spec: Mapping[str, Any]) -> tuple[bool, str]:
    """Practical: explicit instructional genres, else Procedural-conditional.

    Returns (matched, branch) with branch in {"explicit", "conditional", ""};
    explicit wins when both hold so branch counts stay disjoint.
    """
    pred = spec["predicates"]
    if features["d"] in pred["practical_genres"]:
        return True, "explicit"
    if features["k"] in pred["practical_knowledge"] and (
        features["d"] in pred["practical_conditional_genres"]
    ):
        return True, "conditional"
    return False, ""


def predicate_r(features: Mapping[str, Any], spec: Mapping[str, Any]) -> bool:
    """Prose: narrative/expository genres with factual/conceptual knowledge."""
    pred = spec["predicates"]
    return features["d"] in pred["prose_genres"] and features["k"] in pred["prose_knowledge"]


def predicate_f6(features: Mapping[str, Any], spec: Mapping[str, Any]) -> bool:
    """F starts with 6 (spec argument accepted for a uniform signature)."""
    _ = spec
    code: str = features["f"]
    return code[0] == "6"


def predicate_f789(features: Mapping[str, Any], spec: Mapping[str, Any]) -> bool:
    """F starts with 7, 8, or 9."""
    _ = spec
    code: str = features["f"]
    return code[0] in ("7", "8", "9")


def _predicate_flag(name: str, flags: Mapping[str, Any]) -> bool:
    if name not in ("S5", "S61", "P", "R", "F6", "F789") or name not in flags:
        raise SweepError(f"unknown predicate '{name}' in policy spec")
    value = flags[name]
    if isinstance(value, tuple):
        value = value[0]
    return bool(value)


def _component_match(
    rule: Mapping[str, Any], flags: Mapping[str, Any], spec: Mapping[str, Any]
) -> bool:
    # Component rules carry an explicit combinator: {"any": [...]} for
    # alternative predicates (science), {"all": [...]} for conjunctions
    # (practical/prose). Anything else fails closed.
    _ = spec
    if not isinstance(rule, Mapping) or set(rule.keys()) not in ({"any"}, {"all"}):
        raise SweepError("component rule must be exactly {any: [...]} or {all: [...]}")
    if "any" in rule:
        names = rule["any"]
        if not isinstance(names, list) or not names:
            raise SweepError("component any-list must be non-empty")
        return any(_predicate_flag(name, flags) for name in names)
    names = rule["all"]
    if not isinstance(names, list) or not names:
        raise SweepError("component all-list must be non-empty")
    return all(_predicate_flag(name, flags) for name in names)


def evaluate_policy(
    features: Mapping[str, Any], policy_name: str, tier: str, spec: Mapping[str, Any]
) -> dict[str, Any]:
    """Evaluate one policy/tier on validated features.

    Returns gate outcome, per-predicate matches (branch for P), and the
    single deterministic final category (precedence science, practical,
    prose, unassigned; gate failure means rejected).
    """
    policy = spec["policies"][policy_name]
    gate_name = policy[tier]
    gate_reasons = _GATES[gate_name](features, spec)
    p_match, p_branch = predicate_p(features, spec)
    flags: dict[str, Any] = {
        "S5": predicate_s5(features, spec),
        "S61": predicate_s61(features, spec),
        "P": p_match,
        "P_branch": p_branch,
        "R": predicate_r(features, spec),
        "F6": predicate_f6(features, spec),
        "F789": predicate_f789(features, spec),
    }
    if gate_reasons:
        return {
            "gate": gate_name,
            "gate_pass": False,
            "gate_reasons": gate_reasons,
            "matches": flags,
            "final": "rejected",
        }
    if _component_match(policy["science"], flags, spec):
        final = "essential_science"
    elif _component_match(policy["practical"], flags, spec):
        final = "essential_practical"
    elif _component_match(policy["prose"], flags, spec):
        final = "essential_prose"
    else:
        final = "unassigned"
    return {
        "gate": gate_name,
        "gate_pass": True,
        "gate_reasons": [],
        "matches": flags,
        "final": final,
    }


# --------------------------------------------------------------------------
# Input binding (fail closed; shared by validate-input and run).
# --------------------------------------------------------------------------


def _read_json_capped(path: Path, cap_bytes: int, what: str) -> Any:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise SweepError(f"cannot stat {what} '{path}': {exc}") from exc
    if size > cap_bytes:
        raise SweepError(f"{what} '{path}' is {size} bytes, cap is {cap_bytes}")
    try:
        return json.loads(path.read_bytes().decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise SweepError(f"{what} '{path}' is not valid UTF-8 JSON: {exc}") from exc


def _check_digest(body: Mapping[str, Any], name: str) -> None:
    if not isinstance(body, Mapping):
        raise SweepError(f"{name} is not a JSON object")
    if "digest" not in body:
        raise SweepError(f"{name} carries no digest")
    if _manifest_digest(body) != body["digest"]:
        raise SweepError(f"{name} self-digest mismatch")


def load_binding(
    bundle_path: Path,
    execution_path: Path,
    expect: Mapping[str, Any],
) -> dict[str, Any]:
    """Load and cross-check bundle/execution; return binding facts.

    Verifies self-digests, execution linkage, projection, revision,
    eight 512-record parts in stratum order with unique plan hashes, and
    the expected record total. Never touches the network or the payload.
    """
    bundle = _read_json_capped(bundle_path, 4 * 1024 * 1024, "bundle")
    execution = _read_json_capped(execution_path, 4 * 1024 * 1024, "execution")
    _check_digest(bundle, "bundle")
    _check_digest(execution, "execution")
    if bundle.get("execution_digest") != execution.get("digest"):
        raise SweepError("bundle.execution_digest != execution.digest")
    for key in ("revision", "projection"):
        if bundle.get(key) != execution.get(key):
            raise SweepError(f"bundle/execution disagree on '{key}'")
    if list(bundle.get("projection", [])) != list(expect["projection"]):
        raise SweepError(f"bundle projection {bundle.get('projection')!r} is not supported")
    if bundle.get("revision") != expect["revision"]:
        raise SweepError(f"bundle revision {bundle.get('revision')!r} is not expected")
    if not re.fullmatch(r"[0-9a-f]{40}", str(bundle.get("revision") or "")):
        raise SweepError("bundle revision is not an exact 40-hex commit SHA")
    parts = bundle.get("parts")
    if not isinstance(parts, list) or len(parts) != 8:
        found = len(parts) if isinstance(parts, list) else type(parts).__name__
        raise SweepError(f"bundle must hold exactly 8 parts, found {found}")
    if [p.get("stratum") for p in parts] != list(range(8)):
        raise SweepError("bundle parts are not one per stratum in exact order")
    hashes = [p.get("plan_hash") for p in parts]
    if any(not isinstance(h, str) or not h for h in hashes) or len(set(hashes)) != 8:
        raise SweepError("bundle parts must carry eight unique plan hashes")
    files: dict[str, dict[str, Any]] = {}
    total = 0
    for part in parts:
        for key in ("crawl", "file", "records", "row_range"):
            if key not in part:
                raise SweepError(f"bundle part {part.get('unit')!r} lacks '{key}'")
        if part["records"] != 512:
            raise SweepError(
                f"bundle part {part.get('unit')!r} has {part['records']} records, not 512"
            )
        span = part["row_range"]
        if (
            not isinstance(span, list)
            or len(span) != 2
            or not all(isinstance(v, int) and not isinstance(v, bool) for v in span)
        ):
            raise SweepError(f"bundle part {part.get('unit')!r} has a bad row range")
        start, stop = span
        if stop - start != 512:
            raise SweepError(f"bundle part {part.get('unit')!r} spans {stop - start}, not 512")
        if part["file"] in files:
            raise SweepError(f"bundle file {part['file']!r} appears twice")
        files[part["file"]] = {
            "crawl": part["crawl"],
            "stratum": part["stratum"],
            "start": start,
            "stop": stop,
            "plan_hash": part["plan_hash"],
        }
        total += 512
    if expect["records"] > 0 and bundle.get("total_records") != expect["records"]:
        raise SweepError("bundle total_records does not match the expected count")
    if total != bundle.get("total_records"):
        raise SweepError("bundle parts do not sum to total_records")
    repository = execution.get("repository")
    if not isinstance(repository, str) or not repository:
        raise SweepError("execution carries no repository")
    return {
        "bundle": bundle,
        "execution": execution,
        "repository": repository,
        "files": files,
        "crawls": [parts[i]["crawl"] for i in range(8)],
        "total_records": total,
    }


COMBOS: tuple[tuple[str, str], ...] = tuple((p, t) for p in POLICIES for t in TIERS)
BASE_PREDS = ("S5", "S61", "P", "R", "F6", "F789")
GATE_CONDS = ("english", "artifacts", "missing", "correctness", "doctype")
_LEVEL1_BY_DIGIT = {
    "5": "Science and Natural history",
    "6": "Industrial arts, Technology, and Engineering",
    "7": "Arts",
    "8": "Literature",
    "9": "History and Geography",
}


def _english_band(score: float, bands: Sequence[Sequence[float]]) -> int:
    """Diagnostic band index for a score; bands are [lo, hi] with the top closed."""
    for index, (low, high) in enumerate(bands):
        if score < high or (index == len(bands) - 1 and score <= high):
            if score >= low:
                return index
    return len(bands) - 1


def _nearest_percentiles(values: Sequence[float]) -> dict[str, float | None]:
    """Nearest-rank percentiles (same rank rule as the recon analyzer)."""
    ordered = sorted(values)
    out: dict[str, float | None] = {}
    for point in (10, 25, 50, 75, 90):
        if not ordered:
            out[f"p{point}"] = None
            continue
        rank = max(1, math.ceil(point / 100 * len(ordered)))
        out[f"p{point}"] = round(ordered[rank - 1], 6)
    return out


def _english_summary(values: Sequence[float]) -> dict[str, Any]:
    dist = _nearest_percentiles(values)
    return {
        "count": len(values),
        "min": round(min(values), 6) if values else None,
        **dist,
        "max": round(max(values), 6) if values else None,
    }


def _words_summary(values: Sequence[float], missing: int) -> dict[str, Any]:
    """Bounded publisher-metadata word-count summary (words, never tokens).

    Nearest-rank percentiles, same rank rule as `_nearest_percentiles`;
    the sum runs over the sorted values so float summation order is
    canonical. Cells with no numeric values report `unavailable` rather
    than failing the sweep.
    """
    if not values:
        return {
            "status": "unavailable",
            "reason": f"no numeric {WORDS_PATH} values in this cell",
            "n": 0,
            "missing_or_nonnumeric": missing,
        }
    dist = _nearest_percentiles(values)
    ordered = sorted(values)
    return {
        "n": len(values),
        "missing_or_nonnumeric": missing,
        "min": round(ordered[0], 6),
        **dist,
        "max": round(ordered[-1], 6),
        "sum": round(math.fsum(ordered), 6),
        "unit": "metadata_words_not_tokens",
    }


def _top_counts(counter: Counter[str], top: int) -> dict[str, Any]:
    items = counter.most_common(top)
    return {
        "top": [{"value": value, "count": count} for value, count in items],
        "distinct": len(counter),
        "total": sum(counter.values()),
    }


class Sweep:
    """Streaming accumulator: bounded counters, no row storage except finals."""

    def __init__(self, spec: Mapping[str, Any], crawls: Sequence[str]) -> None:
        self.spec = spec
        self.crawls = list(crawls)
        self.scopes = ["all", *self.crawls]
        self.input_n: Counter[str] = Counter()
        self.validity: dict[str, Counter[str]] = {s: Counter() for s in self.scopes}
        self.invalid_n: dict[str, int] = dict.fromkeys(self.scopes, 0)
        self.gate_pass: dict[tuple[str, str, str], Counter[str]] = {}
        self.joint_pass: dict[tuple[str, str, str], Counter[str]] = {}
        self.pred_base: dict[str, Counter[str]] = {s: Counter() for s in self.scopes}
        self.comp_match: dict[tuple[str, str, str], Counter[str]] = {}
        self.final: dict[tuple[str, str, str], Counter[str]] = {}
        self.overlap_base: dict[str, Counter[str]] = {s: Counter() for s in self.scopes}
        self.pred_band: dict[str, Counter[str]] = {s: Counter() for s in self.scopes}
        self.transfer: dict[tuple[str, str, str], Counter[str]] = {}
        self.english: dict[tuple[str, str, str, str], list[float]] = {}
        self.english_input: dict[str, list[float]] = {s: [] for s in self.scopes}
        self.words: dict[tuple[str, str, str, str], list[float]] = {}
        self.words_missing: dict[tuple[str, str, str, str], int] = {}
        self.words_input: dict[str, list[float]] = {s: [] for s in self.scopes}
        self.words_missing_input: dict[str, int] = dict.fromkeys(self.scopes, 0)
        self.comp: dict[tuple[str, str, str, str], Counter[str]] = {}
        self.fdc: dict[tuple[str, str, str, str], Counter[str]] = {}
        self.full_codes: dict[tuple[str, str], Counter[str]] = {}
        self.science_detail: dict[tuple[str, str, str], Counter[str]] = {}
        self.practical_detail: dict[tuple[str, str, str], Counter[str]] = {}
        self.prose_detail: dict[tuple[str, str, str], Counter[str]] = {}
        self.sensitivity: dict[tuple[str, str], Counter[str]] = {}
        self.crosstab_fdk: dict[str, Counter[str]] = {s: Counter() for s in self.scopes}
        self.crosstab_dkc: dict[str, Counter[str]] = {s: Counter() for s in self.scopes}
        self.crosstab_61x: dict[str, Counter[str]] = {s: Counter() for s in self.scopes}
        self.crosstab_prod: dict[str, Counter[str]] = {s: Counter() for s in self.scopes}
        self.crosstab_qa: dict[str, Counter[str]] = {s: Counter() for s in self.scopes}
        self.crosstab_prose: dict[str, Counter[str]] = {s: Counter() for s in self.scopes}
        self.crosstab_levels: dict[str, Counter[str]] = {s: Counter() for s in self.scopes}
        self.level1_check: dict[str, Counter[str]] = {s: Counter() for s in self.scopes}
        self.unknown_values: dict[str, Counter[str]] = {}
        self.finals: dict[tuple[str, str], list[str]] = {c: [] for c in COMBOS}
        self.gatepass: dict[tuple[str, str], list[bool]] = {c: [] for c in COMBOS}
        self.rowinfo: list[tuple[str, str, int, dict[str, Any] | None]] = []
        self.waterfall: dict[tuple[str, str, str], Counter[str]] = {}
        self.multi_final = 0

    def process_valid(
        self,
        crawl: str,
        fields: Mapping[str, Any],
        optional: Mapping[str, Any],
    ) -> None:
        """Accumulate one validated row across all policies and tiers."""
        scopes = ("all", crawl)
        for scope in scopes:
            self.input_n[scope] += 1
        if "e" in fields:
            for scope in scopes:
                self.english_input[scope].append(fields["e"])
        for scope in scopes:
            if optional.get("words") is None:
                self.words_missing_input[scope] = self.words_missing_input.get(scope, 0) + 1
            else:
                self.words_input[scope].append(optional["words"])
        base = {
            "S5": predicate_s5(fields, self.spec),
            "S61": predicate_s61(fields, self.spec),
            "R": predicate_r(fields, self.spec),
            "F6": predicate_f6(fields, self.spec),
            "F789": predicate_f789(fields, self.spec),
        }
        p_match, p_branch = predicate_p(fields, self.spec)
        base["P"] = p_match
        band = _english_band(fields["e"], self.spec["english_bands"])
        for scope in scopes:
            for name in BASE_PREDS:
                if base[name]:
                    self.pred_base[scope][name] += 1
                    self.pred_band[scope][f"{name}|band{band}|{fields['a']}|{fields['m']}"] += 1
            matched = [n for n in BASE_PREDS if base[n]]
            for i, first in enumerate(matched):
                for second in matched[i + 1 :]:
                    self.overlap_base[scope][f"{first}&{second}"] += 1
            if len(matched) >= 3:
                self.overlap_base[scope]["triple_or_more"] += 1
        self._cross_tabs(crawl, fields, optional)
        for combo in COMBOS:
            policy_name, tier = combo
            policy_def: Mapping[str, Any] = self.spec["policies"][policy_name]
            result = evaluate_policy(fields, policy_name, tier, self.spec)
            final = result["final"]
            self.finals[combo].append(final)
            self.gatepass[combo].append(result["gate_pass"])
            stages = (
                ["valid"]
                + [
                    f"gate_{c}"
                    for c in ("english", "artifacts", "missing", "correctness", "doctype")
                ]
                + ["joint"]
            )
            for scope in scopes:
                self.record_waterfall(
                    combo, scope, stages, self._stage_pass(fields, policy_name, tier, result)
                )
                for cond in GATE_CONDS:
                    if self._cond_passed(cond, fields, policy_name, tier):
                        self._cell(self.gate_pass, combo, scope)[cond] += 1
                if result["gate_pass"]:
                    self._cell(self.joint_pass, combo, scope)["joint"] += 1
                comp_sets = {
                    "science": _component_match(
                        policy_def["science"], result["matches"], self.spec
                    ),
                    "practical": _component_match(
                        policy_def["practical"], result["matches"], self.spec
                    ),
                    "prose": _component_match(policy_def["prose"], result["matches"], self.spec),
                }
                for component, is_match in comp_sets.items():
                    if is_match:
                        self._cell(self.comp_match, combo, scope)[component] += 1
                hit = sorted(c for c, m in comp_sets.items() if m)
                if len(hit) >= 2:
                    for i, first in enumerate(hit):
                        for second in hit[i + 1 :]:
                            self._cell(self.transfer, combo, scope)[
                                f"{first}&{second}->{final}"
                            ] += 1
                self._cell(self.final, combo, scope)[final] += 1
                self._cell_english(combo, scope, final, fields)
                self._cell_words(combo, scope, final, optional.get("words"))
                self._cell_comp(combo, scope, final, fields)
                self._cell_fdc(combo, scope, final, fields)
                self._cell_detail(combo, scope, final, fields, base, p_branch)
        self._sensitivity(crawl, fields)

    @staticmethod
    def _cell(
        store: dict[tuple[str, str, str], Counter[str]], combo: tuple[str, str], scope: str
    ) -> Counter[str]:
        key = (combo[0], combo[1], scope)
        if key not in store:
            store[key] = Counter()
        return store[key]

    def _stage_pass(
        self, fields: Mapping[str, Any], policy_name: str, tier: str, result: Mapping[str, Any]
    ) -> dict[str, bool]:
        stages: dict[str, bool] = {"valid": True}
        for cond in GATE_CONDS:
            stages[f"gate_{cond}"] = self._cond_passed(cond, fields, policy_name, tier)
        stages["joint"] = bool(result["gate_pass"])
        return stages

    def _cond_passed(
        self, cond: str, fields: Mapping[str, Any], policy_name: str, tier: str
    ) -> bool:
        # Per-condition pass, independent of the joint outcome (attrition needs
        # each condition's marginal retention).
        gate_name: str = self.spec["policies"][policy_name][tier]
        return _gate_conditions(self.spec, gate_name, fields)[cond]

    def _cell_english(
        self, combo: tuple[str, str], scope: str, final: str, fields: Mapping[str, Any]
    ) -> None:
        key = (combo[0], combo[1], scope, final)
        self.english.setdefault(key, []).append(fields["e"])

    def _cell_words(
        self, combo: tuple[str, str], scope: str, final: str, words: float | None
    ) -> None:
        """Accumulate one optional publisher word count; missing stays counted."""
        key = (combo[0], combo[1], scope, final)
        if words is None:
            self.words_missing[key] = self.words_missing.get(key, 0) + 1
        else:
            self.words.setdefault(key, []).append(words)

    def _cell_comp(
        self, combo: tuple[str, str], scope: str, final: str, fields: Mapping[str, Any]
    ) -> None:
        for classifier, key in (("d", "D"), ("a", "A"), ("m", "M"), ("t", "T"), ("k", "K")):
            self.comp.setdefault((combo[0], combo[1], scope, final), Counter())[
                key + "=" + fields[classifier]
            ] += 1

    def _cell_fdc(
        self, combo: tuple[str, str], scope: str, final: str, fields: Mapping[str, Any]
    ) -> None:
        store = self.fdc.setdefault((combo[0], combo[1], scope, final), Counter())
        store["digit1=" + fields["digit1"]] += 1
        store["digit2=" + fields["digit2"]] += 1
        store["prefix3=" + fields["prefix3"]] += 1
        if final in ("essential_science", "essential_practical", "essential_prose"):
            self.full_codes.setdefault((combo[0], combo[1]), Counter())[fields["f"]] += 1

    def _cell_detail(
        self,
        combo: tuple[str, str],
        scope: str,
        final: str,
        fields: Mapping[str, Any],
        base: Mapping[str, Any],
        p_branch: str,
    ) -> None:
        if final == "essential_science":
            detail = self.science_detail.setdefault((combo[0], combo[1], scope), Counter())
            if base["S5"]:
                detail["s5"] += 1
            if base["S61"]:
                detail["s61"] += 1
        if final == "essential_practical":
            detail = self.practical_detail.setdefault((combo[0], combo[1], scope), Counter())
            detail["branch=" + (p_branch or "none")] += 1
            digit = fields["digit1"]
            detail["domain=" + ("0xx" if digit == "0" else "6xx" if digit == "6" else "other")] += 1
        if final == "essential_prose":
            detail = self.prose_detail.setdefault((combo[0], combo[1], scope), Counter())
            detail["genre=" + fields["d"]] += 1

    def _cross_tabs(
        self, crawl: str, fields: Mapping[str, Any], optional: Mapping[str, Any]
    ) -> None:
        cog = optional.get("cognitive") or "__missing__"
        levels = optional.get("levels") or ("__missing__", "__missing__", "__missing__")
        for scope in ("all", crawl):
            self.crosstab_fdk[scope][f"{fields['prefix3']}|{fields['d']}|{fields['k']}"] += 1
            self.crosstab_dkc[scope][f"{fields['d']}|{fields['k']}|{cog}"] += 1
            if fields["prefix3"] in self.spec["predicates"]["s61_prefixes"]:
                self.crosstab_61x[scope][
                    f"{fields['prefix3']}|{fields['d']}|{fields['k']}|{fields['t']}"
                ] += 1
            if fields["d"] in [
                "Product Page",
                *self.spec["predicates"]["practical_genres"],
                "Q&A Forum",
            ]:
                proc = "procedural" if fields["k"] == "Procedural" else "other"
                self.crosstab_prod[scope][f"{fields['d']}|{proc}|{fields['digit1']}xx"] += 1
            if fields["d"] == "Q&A Forum":
                proc = "procedural" if fields["k"] == "Procedural" else "other"
                self.crosstab_qa[scope][f"{proc}|{fields['t']}|{fields['m']}"] += 1
            if fields["d"] in self.spec["predicates"]["prose_genres"]:
                self.crosstab_prose[scope][
                    f"{fields['d']}|{fields['prefix3']}|{fields['k']}|{fields['a']}|{fields['m']}"
                ] += 1
            self.crosstab_levels[scope][f"{fields['prefix3']}|{levels[1]}|{levels[2]}"] += 1
            digit = fields["digit1"]
            if digit in _LEVEL1_BY_DIGIT:
                got = levels[0]
                if got == "__missing__":
                    self.level1_check[scope]["absent"] += 1
                elif got == _LEVEL1_BY_DIGIT[digit]:
                    self.level1_check[scope]["consistent"] += 1
                else:
                    self.level1_check[scope]["inconsistent"] += 1
            else:
                self.level1_check[scope]["unmapped"] += 1

    @staticmethod
    def extract_optional(record: Mapping[str, Any]) -> dict[str, Any]:
        """Opportunistic diagnostic fields; never validity-relevant.

        Bloom cognitive-process label and FDC level labels are read when
        present as non-empty strings, else recorded as None (rendered
        __missing__). FDC labels come from the projected primary path
        `eai_taxonomy.free_decimal_correspondence.primary.labels.*`
        only; sibling/non-primary `labels` mappings are never consulted.
        The publisher metadata word count at WORDS_PATH is read when it
        is a finite non-boolean number, else None. Absence here never
        fails validation; the required-field contract is unchanged.
        """

        def _opt_str(path: str) -> str | None:
            status, value = get_path(record, path)
            if status != "ok" or not isinstance(value, str) or not value.strip():
                return None
            return value.strip()

        def _opt_words() -> float | None:
            status, value = get_path(record, WORDS_PATH)
            if status != "ok" or isinstance(value, bool):
                return None
            if not isinstance(value, (int, float)) or not math.isfinite(value):
                return None
            return float(value)

        levels = tuple(
            _opt_str(f"eai_taxonomy.free_decimal_correspondence.primary.labels.{depth}")
            or "__missing__"
            for depth in ("level_1", "level_2", "level_3")
        )
        return {
            "cognitive": _opt_str("eai_taxonomy.bloom_cognitive_process.primary.label"),
            "levels": levels,
            "words": _opt_words(),
        }

    def record_waterfall(
        self, combo: tuple[str, str], scope: str, order: Sequence[str], passed: Mapping[str, bool]
    ) -> None:
        """Sequential attrition stages; stops counting at the first failure."""
        store = self._cell(self.waterfall, combo, scope)
        for stage in order:
            if not passed.get(stage, False):
                break
            store[stage] += 1

    def _sensitivity(self, crawl: str, fields: Mapping[str, Any]) -> None:
        for policy_name in POLICIES:
            base = dict(self.spec["gates"][self.spec["policies"][policy_name]["normal"]])
            variants: list[tuple[str, dict[str, Any]]] = []
            for threshold in self.spec["sensitivity"]["english_thresholds"]:
                gate = dict(base, english_min=threshold)
                variants.append((f"E>={threshold}", gate))
            for option in self.spec["sensitivity"]["artifact_options"]:
                variants.append(("A=" + "+".join(option), dict(base, artifacts=list(option))))
            for option in self.spec["sensitivity"]["missing_options"]:
                variants.append(("M=" + "+".join(option), dict(base, missing=list(option))))
            for scope in ("all", crawl):
                store = self.sensitivity.setdefault((policy_name, scope), Counter())
                for name, gate in variants:
                    if not _gate(fields, gate, self.spec):
                        store[name] += 1

    def process_invalid(
        self,
        crawl: str,
        fields: Mapping[str, Any],
        reasons: Sequence[str],
        unknowns: Mapping[str, str],
        words: float | None = None,
    ) -> None:
        """Accumulate a validity-rejected row (partial validated values kept)."""
        scopes = ("all", crawl)
        for scope in scopes:
            self.input_n[scope] += 1
            self.invalid_n[scope] += 1
            if "e" in fields:
                self.english_input[scope].append(fields["e"])
            if words is None:
                self.words_missing_input[scope] = self.words_missing_input.get(scope, 0) + 1
            else:
                self.words_input[scope].append(words)
            for reason in reasons:
                self.validity[scope][reason] += 1
            for classifier, value in unknowns.items():
                store = self.unknown_values.setdefault(classifier, Counter())
                if value not in store and len(store) >= 100:
                    store["+truncated_more"] += 1
                else:
                    store[value] += 1
            for combo in COMBOS:
                self._cell(self.final, combo, scope)["rejected"] += 1
                self._cell(self.waterfall, combo, scope)["input"] += 1
                if "e" in fields:
                    self.english.setdefault((combo[0], combo[1], scope, "rejected"), []).append(
                        fields["e"]
                    )
                self._cell_words(combo, scope, "rejected", words)
                for classifier, key in (("d", "D"), ("a", "A"), ("m", "M"), ("t", "T"), ("k", "K")):
                    if classifier in fields:
                        self.comp.setdefault((combo[0], combo[1], scope, "rejected"), Counter())[
                            key + "=" + fields[classifier]
                        ] += 1
                if "f" in fields:
                    store = self.fdc.setdefault((combo[0], combo[1], scope, "rejected"), Counter())
                    store["digit1=" + fields["digit1"]] += 1
                    store["digit2=" + fields["digit2"]] += 1
                    store["prefix3=" + fields["prefix3"]] += 1
        for combo in COMBOS:
            self.finals[combo].append("rejected")
            self.gatepass[combo].append(False)


# --------------------------------------------------------------------------
# Finalize: hard invariants (any failure refuses the whole sweep).
# --------------------------------------------------------------------------


def _check_conservation(sweep: Sweep, n_rows: int) -> None:
    if len(sweep.rowinfo) != n_rows:
        raise SweepError(f"{len(sweep.rowinfo)} rowinfo entries for {n_rows} rows")
    for combo in COMBOS:
        if len(sweep.finals[combo]) != n_rows:
            raise SweepError(f"{combo}: {len(sweep.finals[combo])} finals for {n_rows} rows")
        if len(sweep.gatepass[combo]) != n_rows:
            raise SweepError(f"{combo}: gate record length mismatch")
        for scope in sweep.scopes:
            total = sum(sweep.final[combo[0], combo[1], scope].values())
            if total != sweep.input_n[scope]:
                raise SweepError(
                    f"{combo}/{scope}: conservation {total} != input {sweep.input_n[scope]}"
                )


def _check_identities(sweep: Sweep, n_rows: int) -> dict[str, int]:
    """B-strict == D-strict, B-science == C-science, subset invariants.

    Cross-policy B/D relations report first: a gate-definition defect
    surfaces as the gate diagnostic (with offending locators) rather than
    as a downstream subset mismatch.
    """
    out: dict[str, int] = {}
    out.update(_check_bd_relations(sweep, n_rows))
    agree = sum(
        1
        for a, b in zip(sweep.finals[("B", "strict")], sweep.finals[("D", "strict")], strict=True)
        if a == b
    )
    out["b_strict_d_strict_agreement"] = agree
    if agree != n_rows:
        raise SweepError(f"B-strict vs D-strict differ on {n_rows - agree} rows")
    for policy_name in POLICIES:
        normal = sweep.finals[(policy_name, "normal")]
        strict = sweep.finals[(policy_name, "strict")]
        for component in ("essential_science", "essential_practical", "essential_prose"):
            bad = sum(
                1 for n, s in zip(normal, strict, strict=True) if s == component and n != component
            )
            out[f"{policy_name}_strict_subset_{component}"] = bad
            if bad:
                raise SweepError(f"{policy_name}: strict {component} is not a normal subset")
    pairs = (
        ("B", "C", "practical"),
        ("B", "C", "prose"),
    )
    for base, variant, kind in pairs:
        component = "essential_" + kind
        for tier in TIERS:
            base_f = sweep.finals[(base, tier)]
            var_f = sweep.finals[(variant, tier)]
            bad = sum(
                1 for b, v in zip(base_f, var_f, strict=True) if v == component and b != component
            )
            out[f"C_subset_B_{kind}_{tier}"] = bad
            if bad:
                raise SweepError(f"C {kind} {tier} is not a B subset")
    b_sci = sweep.finals[("B", "normal")]
    c_sci = sweep.finals[("C", "normal")]
    agree = sum(
        1
        for b, v in zip(b_sci, c_sci, strict=True)
        if (b == "essential_science") == (v == "essential_science")
    )
    out["b_c_science_agreement_normal"] = agree
    if agree != n_rows:
        raise SweepError("B-science vs C-science differ")
    return out


def _check_bd_relations(sweep: Sweep, n_rows: int) -> dict[str, int]:
    """Gate-set subset (GN pass ⊆ GD pass) and final-assignment preservation.

    B and D share predicates and precedence, so every B-normal gate-pass
    row must pass D-normal, and every B-assigned row must keep its
    component under D (GD-only rows may add D assignments). Violations
    raise with counts plus at most 20 bounded offender locators; no text.
    """
    _ = n_rows
    out: dict[str, int] = {}
    for tier in TIERS:
        b_gate = sweep.gatepass[("B", tier)]
        d_gate = sweep.gatepass[("D", tier)]
        offenders = [i for i, (b, d) in enumerate(zip(b_gate, d_gate, strict=True)) if b and not d]
        gn_pass = sum(1 for b in b_gate if b)
        gd_pass = sum(1 for d in d_gate if d)
        d_only = sum(1 for b, d in zip(b_gate, d_gate, strict=True) if d and not b)
        out[f"d_gate_superset_b_gate_{tier}"] = len(offenders)
        if offenders:
            raise SweepError(
                f"B-{tier} gate pass is not a subset of D-{tier} gate pass: "
                f"GN pass {gn_pass} GD pass {gd_pass} "
                f"|GN-GD| {len(offenders)} |GD-GN| {d_only}; "
                f"first {min(20, len(offenders))} offending locators: "
                + "; ".join(_format_gate_offender(sweep, i) for i in offenders[:20])
            )
    for tier in TIERS:
        b_final = sweep.finals[("B", tier)]
        d_final = sweep.finals[("D", tier)]
        for component in ("essential_science", "essential_practical", "essential_prose"):
            bad_idx = [
                i
                for i, (b, v) in enumerate(zip(b_final, d_final, strict=True))
                if b == component and v != component
            ]
            out[f"b_final_preserved_in_d_final_{component}_{tier}"] = len(bad_idx)
            if bad_idx:
                shown = "; ".join(
                    _format_final_offender(sweep, i, b_final[i], d_final[i]) for i in bad_idx[:20]
                )
                raise SweepError(
                    f"B-{tier} {component} is not preserved under D-{tier}: "
                    f"{len(bad_idx)} rows differ; first {min(20, len(bad_idx))}: {shown}"
                )
    return out


def _format_gate_offender(sweep: Sweep, index: int) -> str:
    """One bounded diagnostic line: locator, fields, GN/GD conditions, reasons."""
    crawl, source_file, row_index, fields = sweep.rowinfo[index]
    assert fields is not None
    spec = sweep.spec
    gn = _gate_conditions(spec, "GN", fields)
    gd = _gate_conditions(spec, "GD", fields)

    def _conds(conds: Mapping[str, bool]) -> str:
        order = ("english", "artifacts", "missing", "correctness", "doctype")
        return "".join("Y" if conds[c] else "n" for c in order)

    def _reasons(conds: Mapping[str, bool]) -> str:
        missing = sorted(_REASON_FOR_COND[c] for c, ok in conds.items() if not ok)
        return ",".join(missing) if missing else "-"

    return (
        f"row {crawl} {source_file} {row_index} "
        f"F={fields['f']} D={fields['d']} K={fields['k']} A={fields['a']} "
        f"M={fields['m']} T={fields['t']} E={fields['e']:.4f} "
        f"GN={_conds(gn)} GD={_conds(gd)} "
        f"GN_reasons=[{_reasons(gn)}] GD_reasons=[{_reasons(gd)}]"
    )


def _format_final_offender(sweep: Sweep, index: int, before: str, after: str) -> str:
    """One bounded diagnostic line: locator plus B vs D finals."""
    crawl, source_file, row_index, fields = sweep.rowinfo[index]
    detail = f"row {crawl} {source_file} {row_index} B={before} D={after}"
    if fields is not None:
        detail += f" F={fields['f']} D={fields['d']}"
    return detail


# --------------------------------------------------------------------------
# Deterministic output builders (sorted keys; no timestamps).
# --------------------------------------------------------------------------


def _sorted(counter: Counter[str]) -> dict[str, int]:
    return {key: counter[key] for key in sorted(counter)}


def _dumps(obj: Any) -> str:
    return json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _combo_key(policy_name: str, tier: str) -> str:
    return f"{policy_name}-{tier}"


def build_summary(
    sweep: Sweep, binding: Mapping[str, Any], identities: Mapping[str, int], n_rows: int
) -> dict[str, Any]:
    combos: dict[str, Any] = {}
    for combo in COMBOS:
        policy_name, tier = combo
        counts = sweep.final[combo[0], combo[1], "all"]
        finals = {component: counts.get(component, 0) for component in FINAL_COMPONENTS}
        combos[_combo_key(policy_name, tier)] = {
            "final": finals,
            "conservation_ok": sum(finals.values()) == n_rows,
        }
    return {
        "input_records": n_rows,
        "crawls": list(sweep.crawls),
        "combos": combos,
        "identity_invariants": dict(sorted(identities.items())),
        "multi_final_violations": sweep.multi_final,
    }


def build_per_crawl(sweep: Sweep) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for crawl in sweep.crawls:
        cell: dict[str, Any] = {"input": sweep.input_n[crawl]}
        cell["validity_reasons"] = _sorted(sweep.validity[crawl])
        cell["invalid"] = sweep.invalid_n[crawl]
        combos: dict[str, Any] = {}
        for combo in COMBOS:
            policy_name, tier = combo
            key = (combo[0], combo[1], crawl)
            counts = sweep.final[key]
            finals = {component: counts.get(component, 0) for component in FINAL_COMPONENTS}
            combos[_combo_key(policy_name, tier)] = {
                "final": finals,
                "retention": {
                    component: round(count / sweep.input_n[crawl], 6)
                    if sweep.input_n[crawl]
                    else 0.0
                    for component, count in finals.items()
                },
            }
        cell["combos"] = combos
        out[crawl] = cell
    return out


def build_attrition(sweep: Sweep) -> dict[str, Any]:
    stages = (
        ["input", "valid"]
        + [f"gate_{c}" for c in ("english", "artifacts", "missing", "correctness", "doctype")]
        + ["joint"]
    )
    out: dict[str, Any] = {"stages": list(stages), "note": "sequential; stops at first failure"}
    for combo in COMBOS:
        policy_name, tier = combo
        per_scope: dict[str, Any] = {}
        for scope in sweep.scopes:
            counts: dict[str, int] = {"input": sweep.input_n[scope]}
            store = sweep.waterfall.get((combo[0], combo[1], scope), Counter())
            for stage in stages[1:]:
                counts[stage] = store.get(stage, 0)
            per_scope[scope] = counts
        validity = {scope: _sorted(sweep.validity[scope]) for scope in sweep.scopes}
        out[_combo_key(policy_name, tier)] = {"waterfall": per_scope, "validity_reasons": validity}
    return out


def build_overlaps(sweep: Sweep) -> dict[str, Any]:
    base = {scope: _sorted(sweep.overlap_base[scope]) for scope in sweep.scopes}
    components: dict[str, Any] = {}
    for combo in COMBOS:
        policy_name, tier = combo
        per_scope: dict[str, Any] = {}
        for scope in sweep.scopes:
            key = (combo[0], combo[1], scope)
            per_scope[scope] = {
                "component_matches": {
                    component: sweep.comp_match.get(key, Counter()).get(component, 0)
                    for component in ("science", "practical", "prose")
                },
                "transfers": _sorted(sweep.transfer.get(key, Counter())),
            }
        components[_combo_key(policy_name, tier)] = per_scope
    transitions: dict[str, Any] = {}
    pairs = [("A", "B"), ("B", "C"), ("B", "D"), ("A", "C"), ("A", "D"), ("C", "D")]
    for first, second in pairs:
        for tier in TIERS:
            before = sweep.finals[(first, tier)]
            after = sweep.finals[(second, tier)]
            selected_before = {
                i for i, v in enumerate(before) if v != "unassigned" and v != "rejected"
            }
            selected_after = {
                i for i, v in enumerate(after) if v != "unassigned" and v != "rejected"
            }
            reassign = sum(1 for i in selected_before & selected_after if before[i] != after[i])
            transitions[f"{first}->{second}/{tier}"] = {
                "additions": len(selected_after - selected_before),
                "removals": len(selected_before - selected_after),
                "reassignments": reassign,
            }
    for policy_name in POLICIES:
        normal = sweep.finals[(policy_name, "normal")]
        strict = sweep.finals[(policy_name, "strict")]
        selected_normal = {i for i, v in enumerate(normal) if v != "unassigned" and v != "rejected"}
        selected_strict = {i for i, v in enumerate(strict) if v != "unassigned" and v != "rejected"}
        transitions[f"{policy_name}/normal->strict"] = {
            "additions": len(selected_strict - selected_normal),
            "removals": len(selected_normal - selected_strict),
            "reassignments": sum(
                1 for i in selected_normal & selected_strict if normal[i] != strict[i]
            ),
        }
    return {
        "predicate_overlaps": base,
        "component_overlaps": components,
        "transitions": transitions,
    }


def build_crosstabs(sweep: Sweep) -> dict[str, Any]:
    out: dict[str, Any] = {
        "predicate_x_band_x_artifact_x_missing": {
            s: _sorted(sweep.pred_band[s]) for s in sweep.scopes
        },
        "fdc3_x_doctype_x_knowledge": {s: _sorted(sweep.crosstab_fdk[s]) for s in sweep.scopes},
        "doctype_x_knowledge_x_cognitive": {
            s: _sorted(sweep.crosstab_dkc[s]) for s in sweep.scopes
        },
        "selected61x_prefix_x_doctype_x_knowledge_x_correctness": {
            s: _sorted(sweep.crosstab_61x[s]) for s in sweep.scopes
        },
        "genre_x_procedural_x_fdc_domain": {
            s: _sorted(sweep.crosstab_prod[s]) for s in sweep.scopes
        },
        "qa_x_procedural_x_correctness_x_missing": {
            s: _sorted(sweep.crosstab_qa[s]) for s in sweep.scopes
        },
        "prose_genre_x_prefix_x_knowledge_x_artifact_missing": {
            s: _sorted(sweep.crosstab_prose[s]) for s in sweep.scopes
        },
        "fdc_prefix_x_level_labels": {s: _sorted(sweep.crosstab_levels[s]) for s in sweep.scopes},
        "level1_consistency": {s: _sorted(sweep.level1_check[s]) for s in sweep.scopes},
    }
    english: dict[str, Any] = {"input": _english_summary(sweep.english_input["all"])}
    for combo in COMBOS:
        policy_name, tier = combo
        for final in FINAL_COMPONENTS:
            values = sweep.english.get((combo[0], combo[1], "all", final), [])
            english[f"{_combo_key(policy_name, tier)}/{final}"] = _english_summary(values)
    out["english_distributions"] = english
    compositions: dict[str, Any] = {}
    for combo in COMBOS:
        policy_name, tier = combo
        for final in FINAL_COMPONENTS:
            key = (combo[0], combo[1], "all", final)
            compositions[f"{_combo_key(policy_name, tier)}/{final}"] = {
                "labels": _sorted(sweep.comp.get(key, Counter())),
                "fdc": _sorted(sweep.fdc.get(key, Counter())),
            }
    out["compositions"] = compositions
    full_codes: dict[str, Any] = {}
    for combo in COMBOS:
        full_codes[_combo_key(*combo)] = _top_counts(
            sweep.full_codes.get(combo, Counter()), N_FDC_TOP
        )
    out["fdc_full_code_top"] = full_codes
    science: dict[str, Any] = {}
    for combo in COMBOS:
        policy_name, tier = combo
        detail = sweep.science_detail.get((combo[0], combo[1], "all"), Counter())
        chosen = sweep.final[combo[0], combo[1], "all"].get("essential_science", 0)
        science[_combo_key(policy_name, tier)] = {
            "s5_final": detail.get("s5", 0),
            "s61_final": detail.get("s61", 0),
            "science_final": chosen,
            "selected_61x_fraction": round(detail.get("s61", 0) / chosen, 6) if chosen else None,
        }
    out["science_balance"] = science
    practical: dict[str, Any] = {}
    for combo in COMBOS:
        policy_name, tier = combo
        detail = sweep.practical_detail.get((combo[0], combo[1], "all"), Counter())
        practical[_combo_key(policy_name, tier)] = {
            "explicit_branch": detail.get("branch=explicit", 0),
            "conditional_branch": detail.get("branch=conditional", 0),
            "domain": {name: detail.get("domain=" + name, 0) for name in ("0xx", "6xx", "other")},
        }
    out["practical_branches"] = practical
    prose: dict[str, Any] = {}
    for combo in COMBOS:
        policy_name, tier = combo
        detail = sweep.prose_detail.get((combo[0], combo[1], "all"), Counter())
        prose[_combo_key(policy_name, tier)] = {
            genre: detail.get("genre=" + genre, 0)
            for genre in sweep.spec["predicates"]["prose_genres"]
        }
    out["prose_genres"] = prose
    words: dict[str, Any] = {
        "field": WORDS_PATH,
        "unit_note": (
            "publisher metadata word count per row; not verified cleaned-text "
            "words and not XLM tokenizer tokens"
        ),
        "input": _words_summary(sweep.words_input["all"], sweep.words_missing_input.get("all", 0)),
        "input_by_crawl": {
            crawl: _words_summary(sweep.words_input[crawl], sweep.words_missing_input.get(crawl, 0))
            for crawl in sweep.crawls
        },
    }
    for combo in COMBOS:
        policy_name, tier = combo
        for final in FINAL_COMPONENTS:
            key = (combo[0], combo[1], "all", final)
            words[f"{_combo_key(policy_name, tier)}/{final}"] = _words_summary(
                sweep.words.get(key, []), sweep.words_missing.get(key, 0)
            )
    out["word_count_distributions"] = words
    out["report_schema_version"] = REPORT_SCHEMA_VERSION
    return out


def build_diagnostics(
    sweep: Sweep, anomaly_locs: Sequence[Sequence[str | int]], anomaly_total: int
) -> dict[str, Any]:
    sensitivity = {
        f"{policy}/{scope}": _sorted(counter)
        for (policy, scope), counter in sorted(sweep.sensitivity.items())
    }
    flags = sweep.spec["temporal_flags"]
    min_cell = int(flags["min_cell_count"])
    temporal: dict[str, Any] = {}
    for combo in COMBOS:
        policy_name, tier = combo
        for component in ("essential_science", "essential_practical", "essential_prose"):
            cells: list[dict[str, Any]] = []
            for crawl in sweep.crawls:
                final = sweep.final[combo[0], combo[1], crawl].get(component, 0)
                cells.append({"crawl": crawl, "final": final})
                if final == 0:
                    cells[-1]["flag"] = "zero_final_rows"
                elif final < min_cell:
                    cells[-1]["flag"] = "sparse_cell"
            qualified: list[tuple[str, int]] = [
                (str(c["crawl"]), int(c["final"])) for c in cells if int(c["final"]) >= min_cell
            ]
            fold_note: str | None = None
            if len(qualified) >= 2:
                inputs = {c: sweep.input_n[c] for c, _ in qualified}
                rets = [(c, f / inputs[c]) for c, f in qualified if inputs[c] > 0]
                if len(rets) >= 2:
                    spread = max(r for _, r in rets) / min(r for _, r in rets)
                    if spread >= float(flags["fold_threshold"]):
                        fold_note = f"retention spread {round(spread, 3)} >= 2-fold"
            temporal[f"{_combo_key(policy_name, tier)}/{component}"] = {
                "cells": cells,
                "retention_fold_note": fold_note,
            }
            if component == "essential_science":
                chosen = sweep.final[combo[0], combo[1], "all"].get("essential_science", 0)
                s61 = sweep.science_detail.get((combo[0], combo[1], "all"), Counter()).get("s61", 0)
                if (
                    chosen >= min_cell
                    and chosen > 0
                    and s61 / chosen > float(flags["s61_fraction"])
                ):
                    temporal[f"{_combo_key(policy_name, tier)}/{component}"]["s61_note"] = (
                        f"selected-61x fraction {round(s61 / chosen, 4)} > 0.5"
                    )
    gate_spreads: dict[str, Any] = {}
    for combo in COMBOS:
        policy_name, tier = combo
        stages = (
            ["valid"]
            + [f"gate_{c}" for c in ("english", "artifacts", "missing", "correctness", "doctype")]
            + ["joint"]
        )
        per_stage: dict[str, Any] = {}
        for index in range(1, len(stages)):
            rates = []
            for crawl in sweep.crawls:
                store = sweep.waterfall.get((combo[0], combo[1], crawl), Counter())
                prev = store.get(stages[index - 1], 0)
                cur = store.get(stages[index], 0)
                if prev > 0:
                    rates.append(cur / prev)
            spread = (max(rates) - min(rates)) if rates else 0.0
            per_stage[stages[index]] = {
                "max_min_pp_spread": round(spread * 100, 3),
                "flagged": spread * 100 >= float(flags["pp_threshold"]),
            }
        gate_spreads[_combo_key(policy_name, tier)] = per_stage
    genre_spreads: dict[str, Any] = {}
    for combo in COMBOS:
        policy_name, tier = combo
        for comp_final in ("essential_science", "essential_practical", "essential_prose"):
            per_crawl: dict[str, Counter[str]] = {}
            totals: dict[str, int] = {}
            for crawl in sweep.crawls:
                bucket = sweep.comp.get((combo[0], combo[1], crawl, comp_final), Counter())
                counts = Counter(
                    {key[2:]: count for key, count in bucket.items() if key.startswith("D=")}
                )
                per_crawl[crawl] = counts
                totals[crawl] = sum(counts.values())
            eligible = [c for c in sweep.crawls if totals[c] >= min_cell]
            flagged: dict[str, Any] = {}
            if len(eligible) >= 2:
                genres = sorted({genre for c in eligible for genre in per_crawl[c]})
                for genre in genres:
                    shares = {c: per_crawl[c].get(genre, 0) / totals[c] for c in eligible}
                    spread = max(shares.values()) - min(shares.values())
                    if spread * 100 >= float(flags["pp_threshold"]):
                        flagged[genre] = {
                            "spread_pp": round(spread * 100, 3),
                            "shares": {c: round(share, 6) for c, share in shares.items()},
                            "counts": {c: per_crawl[c].get(genre, 0) for c in eligible},
                            "denominators": {c: totals[c] for c in eligible},
                        }
            genre_spreads[f"{_combo_key(policy_name, tier)}/{comp_final}"] = {
                "denominator": "selected_rows_in_component_per_crawl",
                "min_component_rows": min_cell,
                "eligible_crawls": eligible,
                "flags": flagged,
            }
    for entry in temporal.values():
        entry["retention_denominator"] = "input_rows_per_crawl"
    return {
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "sensitivity_joint_pass_counts": sensitivity,
        "sensitivity_note": "diagnostic-only variants of one gate condition; never policies",
        "temporal_cells": temporal,
        "conditional_gate_spreads_pp": gate_spreads,
        "component_genre_share_spreads_pp": genre_spreads,
        "fdc_anomaly": {
            "count": anomaly_total,
            "locators": [list(loc) for loc in anomaly_locs],
            "locators_bounded_at": 50,
            "note": "invalid_fdc_syntax rows are quarantined from all predicates",
        },
        "unknown_values": {
            classifier: dict(sorted(counter.items()))
            for classifier, counter in sorted(sweep.unknown_values.items())
        },
    }


def render_summary(
    policy_digest: str,
    combined_sha256: str,
    summary: Mapping[str, Any],
    per_crawl: Mapping[str, Any],
    diagnostics: Mapping[str, Any],
) -> str:
    lines = [
        "# Essential-Web selector sweep summary (experiment, not approval)",
        "",
        f"policy digest: `{policy_digest}`",
        f"input records: {summary['input_records']}",
        f"combined input sha256: `{combined_sha256}`",
        "",
        "## Final assignments (aggregate)",
        "",
        "| policy-tier | science | practical | prose | unassigned | rejected |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for key in sorted(summary["combos"]):
        final = summary["combos"][key]["final"]
        lines.append(
            f"| {key} | {final.get('essential_science', 0)} | "
            f"{final.get('essential_practical', 0)} | {final.get('essential_prose', 0)} | "
            f"{final.get('unassigned', 0)} | {final.get('rejected', 0)} |"
        )
    lines += [
        "",
        "## Invariants",
        "",
        f"B-strict == D-strict agreement: "
        f"{summary['identity_invariants'].get('b_strict_d_strict_agreement', '?')}"
        f"/{summary['input_records']}",
        f"multi-final violations: {summary['multi_final_violations']}",
        "",
        "## Temporal flags fired",
        "",
    ]
    fired = [
        (key, cell.get("flag", ""))
        for key, value in diagnostics["temporal_cells"].items()
        for cell in value["cells"]
        if cell.get("flag")
    ]
    if not fired:
        lines.append("none")
    else:
        for key, flag in sorted(fired):
            lines.append(f"- {key}: {flag}")
    lines += [
        "",
        "Full tables: summary.json, per_crawl.json, attrition.json,",
        "overlaps.json, crosstabs.json, diagnostics.json.",
        "",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Streaming run engine (bounded input, caps, deadline; no text ever read).
# --------------------------------------------------------------------------


def _check_deadline(start: float, limit_seconds: float) -> None:
    if time.monotonic() - start > limit_seconds:
        raise SweepError(f"sweep exceeded the {limit_seconds:g}s runtime cap")


def _endpoint_rss_bytes() -> int | None:
    """One end-of-run RSS sample; not a measured process peak.

    No peak sampler (e.g. a polling max-RSS thread or platform-only
    peak_wset, which has no Linux equivalent) is used, so the value
    must never be labeled peak.
    """
    try:
        import psutil
    except ImportError:
        return None
    try:
        return int(psutil.Process(os.getpid()).memory_info().rss)
    except Exception:
        return None


def _locator(record: Mapping[str, Any], line_number: int) -> dict[str, Any]:
    from xlm.data.acquisition.records import LOCATOR_FIELD

    locator = record.get(LOCATOR_FIELD)
    if not isinstance(locator, Mapping):
        raise SweepError(f"line {line_number}: missing acquisition locator")
    return dict(locator)


def run_sweep(
    records_path: Path,
    binding: Mapping[str, Any],
    spec: Mapping[str, Any],
    caps: Mapping[str, Any],
) -> dict[str, Any]:
    """Stream the input bundle and evaluate every policy/tier.

    Never writes artifacts (the caller serializes and writes after all
    checks pass). Returns payloads plus runtime statistics; stats never
    enter artifacts, so identical inputs stay byte-identical.
    """
    start = time.monotonic()
    try:
        total_size = records_path.stat().st_size
    except OSError as exc:
        raise SweepError(f"cannot stat input '{records_path}': {exc}") from exc
    if total_size > caps["max_input_bytes"]:
        raise SweepError(f"input is {total_size} bytes, cap is {caps['max_input_bytes']}")
    sweep = Sweep(spec, binding["crawls"])
    seen: set[tuple[str, int]] = set()
    anomaly_locs: list[list[str | int]] = []
    anomaly_total = 0
    payload_hash = hashlib.sha256()
    n_rows = 0
    with records_path.open("rb") as stream:
        for line_number, raw in enumerate(stream, start=1):
            if len(raw) > caps["max_line_bytes"]:
                raise SweepError(f"line {line_number} exceeds the line cap")
            if n_rows >= caps["max_records"]:
                raise SweepError(f"more than {caps['max_records']} records; refusing")
            if n_rows % 512 == 0:
                _check_deadline(start, float(caps["max_runtime_seconds"]))
            try:
                record = json.loads(raw)
            except ValueError as exc:
                raise SweepError(f"line {line_number}: corrupt JSONL: {exc}") from exc
            if not isinstance(record, dict):
                raise SweepError(f"line {line_number}: record is not an object")
            payload_hash.update(raw)
            locator = _locator(record, line_number)
            source_file = locator.get("source_file")
            if source_file not in binding["files"]:
                raise SweepError(f"line {line_number}: file {source_file!r} not in bundle")
            part = binding["files"][source_file]
            if locator.get("revision") != binding["bundle"]["revision"] or (
                locator.get("repository") != binding["repository"]
            ):
                raise SweepError(f"line {line_number}: locator revision/repository mismatch")
            row_index = locator.get("row_index")
            if type(row_index) is not int or not part["start"] <= row_index < part["stop"]:
                raise SweepError(f"line {line_number}: row {row_index!r} outside its part range")
            key = (str(source_file), row_index)
            if key in seen:
                raise SweepError(f"duplicate locator {key}")
            seen.add(key)
            record.pop("text", None)
            fields, reasons, unknowns = validate_row(record, spec)
            sweep.rowinfo.append(
                (part["crawl"], str(source_file), row_index, dict(fields) if not reasons else None)
            )
            optional = Sweep.extract_optional(record)
            if reasons:
                if "invalid_fdc_syntax" in reasons:
                    anomaly_total += 1
                    if len(anomaly_locs) < 50:
                        anomaly_locs.append([str(source_file), row_index])
                sweep.process_invalid(
                    part["crawl"], fields, reasons, unknowns, optional.get("words")
                )
            else:
                sweep.process_valid(part["crawl"], fields, optional)
            n_rows += 1
    if binding["bundle"].get("combined_sha256") != payload_hash.hexdigest():
        raise SweepError("combined input hash does not match bundle.combined_sha256")
    if binding["bundle"].get("combined_bytes") != total_size:
        raise SweepError("input bytes do not match bundle.combined_bytes")
    if n_rows != binding["total_records"]:
        raise SweepError(f"read {n_rows} records, bundle declares {binding['total_records']}")
    _check_deadline(start, float(caps["max_runtime_seconds"]))
    _check_conservation(sweep, n_rows)
    identities = _check_identities(sweep, n_rows)
    _check_deadline(start, float(caps["max_runtime_seconds"]))
    summary = build_summary(sweep, binding, identities, n_rows)
    per_crawl = build_per_crawl(sweep)
    attrition = build_attrition(sweep)
    overlaps = build_overlaps(sweep)
    crosstabs = build_crosstabs(sweep)
    diagnostics = build_diagnostics(sweep, anomaly_locs, anomaly_total)
    _check_deadline(start, float(caps["max_runtime_seconds"]))
    payloads = {
        "summary.json": summary,
        "per_crawl.json": per_crawl,
        "attrition.json": attrition,
        "overlaps.json": overlaps,
        "crosstabs.json": crosstabs,
        "diagnostics.json": diagnostics,
    }
    elapsed = time.monotonic() - start
    return {
        "payloads": payloads,
        "summary": summary,
        "per_crawl": per_crawl,
        "diagnostics": diagnostics,
        "stats": {
            "records": n_rows,
            "input_bytes": total_size,
            "wall_seconds": round(elapsed, 3),
            "endpoint_rss_bytes": _endpoint_rss_bytes(),
            "scratch_bytes": 0,
        },
    }


def _fail(message: str) -> int:
    print(f"essential_web_selector_sweep: error: {message}", file=sys.stderr)
    return 1


def _expect_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--records", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--execution", type=Path, required=True)
    parser.add_argument("--expect-bundle-digest", required=True, help="Exact bundle.json digest.")
    parser.add_argument(
        "--expect-execution-digest", required=True, help="Exact execution.json digest."
    )
    parser.add_argument(
        "--expect-combined-sha256", required=True, help="Exact input payload SHA-256."
    )
    parser.add_argument("--expect-records", type=int, default=4096)
    parser.add_argument("--expect-revision", default=PINNED_REVISION)
    parser.add_argument(
        "--expect-policy-digest", default=None, help="Refuse unless the spec digests match."
    )
    parser.add_argument("--policy-spec", type=Path, default=None)


def _resolve_spec(args: argparse.Namespace) -> tuple[dict[str, Any], str]:
    spec_path = args.policy_spec or default_policy_spec_path()
    spec, digest = load_policy_spec(spec_path)
    if spec.get("policy_spec_version") != "essential-web-selector-sweep-v1":
        raise SweepError(f"unsupported policy spec version in '{spec_path}'")
    if args.expect_policy_digest is not None and args.expect_policy_digest != digest:
        raise SweepError("policy spec digest differs from --expect-policy-digest")
    return spec, digest


def _resolve_binding(args: argparse.Namespace) -> dict[str, Any]:
    bundle = _read_json_capped(args.bundle, 4 * 1024 * 1024, "bundle")
    execution = _read_json_capped(args.execution, 4 * 1024 * 1024, "execution")
    if not isinstance(bundle, Mapping):
        raise SweepError("bundle must be a JSON object")
    if not isinstance(execution, Mapping):
        raise SweepError("execution must be a JSON object")
    if bundle.get("digest") != args.expect_bundle_digest:
        raise SweepError("bundle digest differs from --expect-bundle-digest")
    if execution.get("digest") != args.expect_execution_digest:
        raise SweepError("execution digest differs from --expect-execution-digest")
    if bundle.get("combined_sha256") != args.expect_combined_sha256:
        raise SweepError("bundle combined hash differs from --expect-combined-sha256")
    return load_binding(
        args.bundle,
        args.execution,
        {
            "records": args.expect_records,
            "revision": args.expect_revision,
            "projection": EXPECTED_PROJECTION,
        },
    )


def cmd_validate_input(args: argparse.Namespace) -> int:
    try:
        spec, digest = _resolve_spec(args)
        binding = _resolve_binding(args)
    except SweepError as exc:
        return _fail(str(exc))
    print(
        _dumps(
            {
                "binding_ok": True,
                "policy_digest": digest,
                "revision": binding["bundle"]["revision"],
                "projection": binding["bundle"]["projection"],
                "records": binding["total_records"],
                "parts": len(binding["bundle"]["parts"]),
                "crawls": binding["crawls"],
                "plan_hashes": sorted({p["plan_hash"] for p in binding["bundle"]["parts"]}),
                "caps": spec["caps"],
            }
        ),
        end="",
    )
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    try:
        spec, digest = _resolve_spec(args)
        binding = _resolve_binding(args)
        result = run_sweep(args.records, binding, spec, spec["caps"])
    except SweepError as exc:
        return _fail(str(exc))
    texts = {name: _dumps(payload) for name, payload in result["payloads"].items()}
    texts["summary.md"] = render_summary(
        digest,
        binding["bundle"]["combined_sha256"],
        result["summary"],
        result["per_crawl"],
        result["diagnostics"],
    )
    texts["policy_spec.json"] = _dumps(spec)
    manifest = {
        "kind": "essential_web_selector_sweep_manifest",
        "tool": TOOL_ID,
        "tool_version": TOOL_VERSION,
        "policy_spec_version": spec["policy_spec_version"],
        "policy_digest": digest,
        "binding": {
            "bundle_digest": binding["bundle"]["digest"],
            "execution_digest": binding["execution"]["digest"],
            "combined_sha256": binding["bundle"]["combined_sha256"],
            "revision": binding["bundle"]["revision"],
            "records": binding["total_records"],
            "parts": len(binding["bundle"]["parts"]),
        },
        "caps": spec["caps"],
        "artifacts": {
            name: {
                "bytes": len(text.encode("utf-8")),
                "sha256": _sha256_text(text),
            }
            for name, text in sorted(texts.items())
        },
    }
    manifest["digest"] = _manifest_digest(manifest)
    texts["sweep_manifest.json"] = _dumps(manifest)
    # Single atomic batch: a failed invariant above never reaches this call,
    # so no partial sweep output can exist; a failed write leaves at most
    # complete files plus no manifest, and rerun refuses a non-empty dir.
    try:
        sizes = write_artifacts(args.output_dir, texts, spec["caps"])
    except SweepError as exc:
        return _fail(str(exc))
    stats = result["stats"]
    print(
        _dumps(
            {
                "manifest_digest": manifest["digest"],
                "policy_digest": digest,
                "records": stats["records"],
                "artifacts": sorted(texts) + ["sweep_manifest.json"],
                "output_bytes": sum(sizes.values()),
            }
        ),
        end="",
    )
    print(
        f"wall_seconds={stats['wall_seconds']} endpoint_rss_bytes={stats['endpoint_rss_bytes']} "
        f"input_bytes={stats['input_bytes']} scratch_bytes={stats['scratch_bytes']}",
    )
    return 0


def write_artifacts(
    output_dir: Path, artifacts: Mapping[str, str], caps: Mapping[str, Any]
) -> dict[str, int]:
    """Atomically write every artifact text; fail closed over the output cap."""
    total = sum(len(text.encode("utf-8")) for text in artifacts.values())
    if total > caps["max_output_bytes"]:
        raise SweepError(f"outputs total {total} bytes, cap is {caps['max_output_bytes']}")
    if output_dir.exists() and any((output_dir / name).exists() for name in artifacts):
        raise SweepError(f"output dir '{output_dir}' already holds sweep artifacts; clear it first")
    output_dir.mkdir(parents=True, exist_ok=True)
    for name, text in artifacts.items():
        path = output_dir / name
        temp = path.with_name(path.name + ".tmp")
        with temp.open("w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    return {name: len(text.encode("utf-8")) for name, text in artifacts.items()}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Bounded Essential-Web selector sweep (offline).")
    sub = parser.add_subparsers(dest="command", required=True)
    validate = sub.add_parser("validate-input", help="Check input bindings only.")
    _expect_args(validate)
    validate.set_defaults(func=cmd_validate_input)
    run = sub.add_parser("run", help="Validate, sweep, and write artifacts.")
    _expect_args(run)
    run.add_argument("--output-dir", type=Path, required=True)
    run.set_defaults(func=cmd_run)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except OSError as exc:
        return _fail(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
