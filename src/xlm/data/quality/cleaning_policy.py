"""Cleaning policy v1 (Phase B): frozen rules, frozen thresholds, strict loading.

``recipes/quality/cleaning_policy_v1.yaml`` is the versioned rule template. Its
``policy`` section is pinned by :data:`POLICY_SECTION_DIGEST`: any edit refuses, so a
rule can only change through a reviewed code change (a new policy version). The
template carries no thresholds.

:func:`freeze_policy` copies, once, each component's CONSERVATIVE Phase-A candidate
cut (comparator, cut and cut bin, verbatim) for the six severe-repetition signals and,
for the OCR-scoped components only, the three OCR signals. It reads only a Phase-A
output whose receipt validates strictly and whose ``candidate-policy-conservative.yaml``
matches the receipt's SHA-256 and size, and writes a self-digested ``FROZEN`` policy
carrying full provenance (receipt, result, binding, manifest, detector policy, code and
candidate-file digests). After the freeze nothing reads the candidate YAML again.

:func:`load_frozen` turns a frozen file into a :class:`CompiledPolicy`: plain tuples
that a worker evaluates (``xlm.data.quality.cleaning``). Configuration stays data-only:
the YAML parameterizes fixed, code-defined rule semantics and is never executed.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from xlm.data.evidence_v2 import canonical
from xlm.data.quality.policy import (
    CANDIDATE_BANDS,
    CLASS_ORDER,
    METRIC_INDEX,
    METRICS,
    POLICY_VERSION,
)
from xlm.data.quality.receipt import check_envelope
from xlm.data.quality.scan import QualityError, read_bounded

POLICY_KIND = "xlm_quality_cleaning_policy"
POLICY_V1 = "cleaning_policy_v1"
POLICY_V2 = "cleaning_policy_v2"
TEMPLATE_STATUS = "RULES_FROZEN_THRESHOLDS_PENDING"
FROZEN_STATUS = "FROZEN"
# canonical.digest of the template's ``policy`` section (the reviewed v1 rule set).
POLICY_SECTION_DIGEST = "d1eb1990448ea76fbffbba9d44ff016d9dada68caabebe92ed7f61627cfe77e2"
# canonical.digest of the v2 template's ``policy`` section (the human-reviewed successor).
POLICY_SECTION_DIGEST_V2 = "cb4c52853969046f49bebfa72dc82e2ad922f0ae9e5781128a5f886caefdc0c4"
SECTION_DIGESTS = {POLICY_V1: POLICY_SECTION_DIGEST, POLICY_V2: POLICY_SECTION_DIGEST_V2}
TEMPLATE_KEYS = frozenset({"kind", "version", "status", "policy", "thresholds", "provenance"})
FROZEN_KEYS = TEMPLATE_KEYS | {"template_sha256", "digest"}
MAX_POLICY_BYTES = 4 * 1024**2
CANDIDATE_ARTIFACT = "candidate-policy-conservative.yaml"
CANDIDATE_KIND = "xlm_quality_candidate_policy_v2"
CANDIDATE_BAND = "conservative"
FROZEN_BANNER = (
    "# XLM cleaning policy v1 - FROZEN. Written by `clean-freeze-policy`; do not edit.\n"
    "# Thresholds are copied verbatim from the verified Phase-A conservative candidate\n"
    "# policy named in `provenance`. Self-digested: any edit refuses. DRY-RUN ONLY.\n"
)
FROZEN_BANNER_V2 = (
    "# XLM cleaning policy v2 (human-reviewed successor of v1) - FROZEN. Written by\n"
    "# `clean-freeze-policy`; do not edit. Thresholds are copied verbatim from the verified\n"
    "# Phase-A conservative candidate policy and equal the v1 frozen cuts (`provenance`).\n"
    "# Self-digested: any edit refuses. DRY-RUN ONLY.\n"
)
BANNERS = {POLICY_V1: FROZEN_BANNER, POLICY_V2: FROZEN_BANNER_V2}

OUTCOMES = ("KEEP", "DROP", "REVIEW")
KEEP, DROP, REVIEW = 0, 1, 2
# Fixed rule order (bit positions in a document's rule mask).
RULES: tuple[tuple[str, int], ...] = (
    ("hard.full_html", DROP),
    ("hard.nul", DROP),
    ("hard.noncharacters", DROP),
    ("rep.severe_default", DROP),
    ("rep.severe_structured", DROP),
    ("rep.structured_two_signals", REVIEW),
    ("ocr.finepdfs_with_repetition", DROP),
    ("ocr.finepdfs_review", REVIEW),
    ("enc.replacement_review", REVIEW),
    ("enc.mojibake_review", REVIEW),
    ("enc.replacement_and_mojibake", DROP),
    ("enc.with_forbidden_controls", DROP),
)
# v2 (human-reviewed): no full-HTML rule, no REVIEW rule; encoding thresholds DROP.
RULES_V2: tuple[tuple[str, int], ...] = (
    ("hard.nul", DROP),
    ("hard.noncharacters", DROP),
    ("rep.severe_default", DROP),
    ("rep.severe_structured", DROP),
    ("ocr.finepdfs_with_repetition", DROP),
    ("enc.replacement_chars", DROP),
    ("enc.mojibake_hits", DROP),
    ("enc.replacement_and_mojibake", DROP),
    ("enc.with_forbidden_controls", DROP),
)


@dataclass(frozen=True)
class RuleSet:
    """One policy version's rules in fixed bit order, with derived masks."""

    version: str
    rules: tuple[tuple[str, int], ...]
    ids: tuple[str, ...]
    bit: Mapping[str, int]
    drop_mask: int
    review_mask: int
    rep_mask: int
    enc_mask: int
    strata: tuple[str, ...]  # review stratum of each rule (``drop.<id>`` / ``review.<id>``)


def make_ruleset(version: str, rules: tuple[tuple[str, int], ...]) -> RuleSet:
    ids = tuple(name for name, _ in rules)
    bit = {name: 1 << n for n, name in enumerate(ids)}
    return RuleSet(
        version=version,
        rules=rules,
        ids=ids,
        bit=bit,
        drop_mask=sum(bit[name] for name, action in rules if action == DROP),
        review_mask=sum(bit[name] for name, action in rules if action == REVIEW),
        rep_mask=sum(bit[name] for name in ids if name.startswith("rep.")),
        enc_mask=sum(bit[name] for name in ids if name.startswith("enc.")),
        strata=tuple(
            f"drop.{name}" if action == DROP else f"review.{name}" for name, action in rules
        ),
    )


RULESET_V1 = make_ruleset(POLICY_V1, RULES)
RULESET_V2 = make_ruleset(POLICY_V2, RULES_V2)
RULESETS = {POLICY_V1: RULESET_V1, POLICY_V2: RULESET_V2}
# v1 module-level names (unchanged; the historical v1 API).
RULE_IDS = RULESET_V1.ids
RULE_BIT = dict(RULESET_V1.bit)
DROP_MASK = RULESET_V1.drop_mask
REVIEW_MASK = RULESET_V1.review_mask
REP_SIGNALS = (
    "compression_ratio",
    "ngram10_excess_ratio",
    "dup_line_byte_ratio",
    "dup_paragraph_byte_ratio",
    "repeated_char_ratio",
    "max_char_run",
)
OCR_SIGNALS = ("page_number_line_ratio", "repeated_header_ratio", "single_char_line_ratio")
THRESHOLD_KEYS = frozenset(
    {"comparator", "cut", "cut_bin", "quantile_bin", "phase_a_rule_id", "phase_a_impact"}
)
IMPACT_KEYS = frozenset({"docs", "bytes", "docs_pct", "bytes_pct"})
COMPONENT_KEYS = frozenset({"repetition", "ocr"})
PROVENANCE_KEYS = frozenset({"phase_a", "candidate_policy", "copy_rule", "frozen_by"})
PROVENANCE_KEYS_V2 = PROVENANCE_KEYS | {"predecessor"}
PREDECESSOR_KEYS = frozenset(
    {"version", "policy_digest", "file_sha256", "phase_a_receipt_digest", "thresholds_digest"}
)
PHASE_A_KEYS = frozenset(
    {
        "receipt_digest",
        "result_digest",
        "binding_digest",
        "input_manifest_digest",
        "input_manifest_file_sha256",
        "input_manifest_mode",
        "detector_policy",
        "code_commit",
        "code_identity",
        "dependency_sha256",
        "c05_overlay",
        "operational_envelope",
    }
)
CANDIDATE_KEYS = frozenset({"artifact", "kind", "band", "quantile", "status", "sha256", "bytes"})
COPY_RULE = (
    "per component and signal: comparator, cut, cut_bin and quantile_bin copied verbatim from "
    "the Phase-A conservative candidate rule `<component>.<signal>`; a signal with no Phase-A "
    "rule (no applicable documents) is null and never fires"
)
FROZEN_BY = "python -m xlm.data.quality clean-freeze-policy"


class PolicyError(QualityError):
    """Content-free refusal of a cleaning policy file."""


def _require(condition: bool, what: str) -> None:
    if not condition:
        raise PolicyError(f"cleaning policy refused: {what}")


def _sha(value: Any) -> bool:
    return type(value) is str and len(value) == 64 and set(value) <= set("0123456789abcdef")


class _StrictLoader(yaml.SafeLoader):
    """Safe YAML that refuses duplicate mapping keys (PyYAML keeps the last silently)."""


def _mapping(loader: _StrictLoader, node: yaml.MappingNode, deep: bool = False) -> Any:
    keys: set[Any] = set()
    for key_node, _ in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in keys:
            raise PolicyError("cleaning policy refused: duplicate YAML key")
        keys.add(key)
    return loader.construct_mapping(node, deep=deep)


_StrictLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


class _PlainDumper(yaml.SafeDumper):
    def ignore_aliases(self, data: Any) -> bool:
        return True


def parse_yaml(raw: bytes) -> Any:
    try:
        return yaml.load(raw.decode("utf-8"), Loader=_StrictLoader)  # noqa: S506 - safe subclass
    except (UnicodeDecodeError, yaml.YAMLError):
        raise PolicyError("cleaning policy refused: not strict UTF-8 YAML") from None


def dump_yaml(body: Mapping[str, Any]) -> bytes:
    text = yaml.dump(dict(body), Dumper=_PlainDumper, sort_keys=True, allow_unicode=True, width=100)
    return text.encode("utf-8")


def read_policy(path: Path) -> tuple[dict[str, Any], bytes]:
    raw = read_bounded(Path(path), MAX_POLICY_BYTES, "cleaning policy")
    body = parse_yaml(raw)
    _require(isinstance(body, dict), "not a mapping")
    return body, raw


# -- rule parameters (derived from the pinned policy section) ----------------------------


@dataclass(frozen=True)
class Stratum:
    name: str
    quota: int


@dataclass(frozen=True)
class RuleParams:
    """Every number and name the evaluator uses, read from the digest-pinned section."""

    structured_classes: frozenset[str]
    nul_at_least: int
    noncharacters_at_least: int
    default_drop_at_least: int
    structured_drop_at_least: int
    structured_review_equals: int | None
    ocr_components: frozenset[str]
    ocr_at_least: int
    ocr_drop_severe_at_least: int
    replacement_at_least: int
    mojibake_at_least: int
    forbidden_controls: tuple[str, ...]
    guardrails: tuple[tuple[str, int], ...]
    max_rows: int
    seed: str
    strata: tuple[Stratum, ...]
    # Version semantics (v1 values reproduce v1 exactly).
    version: str
    ruleset: RuleSet
    full_html_drop: bool
    ocr_review: bool
    replacement_rule: str
    mojibake_rule: str
    outcomes: tuple[str, ...]

    @property
    def stratum_names(self) -> frozenset[str]:
        return frozenset(s.name for s in self.strata)


def rule_params(policy: Mapping[str, Any], version: str = POLICY_V1) -> RuleParams:
    try:
        section = canonical.digest(policy)
    except canonical.CanonicalError:
        raise PolicyError("cleaning policy refused: policy section is not plain data") from None
    if version == POLICY_V2:
        _require(
            section == POLICY_SECTION_DIGEST_V2,
            "policy section is not the human-reviewed v2 rule set",
        )
        return _rule_params_v2(policy)
    _require(version == POLICY_V1, "unknown policy version")
    _require(section == POLICY_SECTION_DIGEST, "policy section is not the reviewed v1 rule set")
    groups = policy["class_groups"]
    _require(
        sorted([*groups["default"], *groups["structured"]]) == sorted(CLASS_ORDER),
        "class groups must partition the interpretation classes",
    )
    rep = policy["severe_repetition"]["rules"]
    ocr = policy["finepdfs_ocr"]
    enc = policy["encoding"]
    rails = policy["guardrails"]
    sampling = policy["review_sampling"]
    _require(
        tuple(policy["severe_repetition"]["signals"]) == REP_SIGNALS
        and tuple(ocr["signals"]) == OCR_SIGNALS,
        "signal lists",
    )
    names = [*policy["hard_corruption"], *rep, *ocr["rules"], *enc["rules"]]
    _require(sorted(names) == sorted(RULE_IDS), "rule identifiers")
    return RuleParams(
        structured_classes=frozenset(groups["structured"]),
        nul_at_least=int(policy["hard_corruption"]["hard.nul"]["at_least"]),
        noncharacters_at_least=int(policy["hard_corruption"]["hard.noncharacters"]["at_least"]),
        default_drop_at_least=int(rep["rep.severe_default"]["signal_count_at_least"]),
        structured_drop_at_least=int(rep["rep.severe_structured"]["signal_count_at_least"]),
        structured_review_equals=int(rep["rep.structured_two_signals"]["signal_count_equals"]),
        ocr_components=frozenset(ocr["components"]),
        ocr_at_least=int(ocr["rules"]["ocr.finepdfs_with_repetition"]["ocr_signal_count_at_least"]),
        ocr_drop_severe_at_least=int(
            ocr["rules"]["ocr.finepdfs_with_repetition"]["severe_repetition_signal_count_at_least"]
        ),
        replacement_at_least=int(enc["rules"]["enc.replacement_review"]["at_least"]),
        mojibake_at_least=int(enc["rules"]["enc.mojibake_review"]["at_least"]),
        forbidden_controls=tuple(enc["forbidden_controls"]),
        guardrails=tuple(
            (key, int(rails[key]))
            for key in (
                "component_drop_docs_pct_gt",
                "global_drop_docs_pct_gt",
                "component_drop_bytes_pct_gt",
                "global_drop_bytes_pct_gt",
            )
        ),
        max_rows=int(sampling["max_rows"]),
        seed=str(sampling["seed"]),
        strata=tuple(Stratum(str(s["name"]), int(s["quota"])) for s in sampling["strata"]),
        version=POLICY_V1,
        ruleset=RULESET_V1,
        full_html_drop=True,
        ocr_review=True,
        replacement_rule="enc.replacement_review",
        mojibake_rule="enc.mojibake_review",
        outcomes=tuple(policy["outcomes"]),
    )


def _guardrails(rails: Mapping[str, Any]) -> tuple[tuple[str, int], ...]:
    return tuple(
        (key, int(rails[key]))
        for key in (
            "component_drop_docs_pct_gt",
            "global_drop_docs_pct_gt",
            "component_drop_bytes_pct_gt",
            "global_drop_bytes_pct_gt",
        )
    )


def _rule_params_v2(policy: Mapping[str, Any]) -> RuleParams:
    """v2 semantics from its digest-pinned section (no full-HTML rule, no REVIEW)."""
    groups = policy["class_groups"]
    _require(
        sorted([*groups["default"], *groups["structured"]]) == sorted(CLASS_ORDER),
        "class groups must partition the interpretation classes",
    )
    _require(policy["outcomes"] == ["KEEP", "DROP"], "v2 outcomes")
    _require(policy["human_review"]["successor_of"] == POLICY_V1, "v2 predecessor version")
    _require(
        policy["human_review"]["predecessor_policy_section_digest"] == POLICY_SECTION_DIGEST,
        "v2 predecessor rule set",
    )
    rep = policy["severe_repetition"]["rules"]
    ocr = policy["finepdfs_ocr"]
    enc = policy["encoding"]
    sampling = policy["review_sampling"]
    _require(
        tuple(policy["severe_repetition"]["signals"]) == REP_SIGNALS
        and tuple(ocr["signals"]) == OCR_SIGNALS,
        "signal lists",
    )
    names = [*policy["hard_corruption"], *rep, *ocr["rules"], *enc["rules"]]
    _require(sorted(names) == sorted(RULESET_V2.ids), "rule identifiers")
    _require(
        all(
            rule["action"] == "DROP"
            for group in (policy["hard_corruption"], rep, ocr["rules"], enc["rules"])
            for rule in group.values()
        ),
        "v2 rules are DROP only",
    )
    return RuleParams(
        structured_classes=frozenset(groups["structured"]),
        nul_at_least=int(policy["hard_corruption"]["hard.nul"]["at_least"]),
        noncharacters_at_least=int(policy["hard_corruption"]["hard.noncharacters"]["at_least"]),
        default_drop_at_least=int(rep["rep.severe_default"]["signal_count_at_least"]),
        structured_drop_at_least=int(rep["rep.severe_structured"]["signal_count_at_least"]),
        structured_review_equals=None,
        ocr_components=frozenset(ocr["components"]),
        ocr_at_least=int(ocr["rules"]["ocr.finepdfs_with_repetition"]["ocr_signal_count_at_least"]),
        ocr_drop_severe_at_least=int(
            ocr["rules"]["ocr.finepdfs_with_repetition"]["severe_repetition_signal_count_at_least"]
        ),
        replacement_at_least=int(enc["rules"]["enc.replacement_chars"]["at_least"]),
        mojibake_at_least=int(enc["rules"]["enc.mojibake_hits"]["at_least"]),
        forbidden_controls=tuple(enc["forbidden_controls"]),
        guardrails=_guardrails(policy["guardrails"]),
        max_rows=int(sampling["max_rows"]),
        seed=str(sampling["seed"]),
        strata=tuple(Stratum(str(s["name"]), int(s["quota"])) for s in sampling["strata"]),
        version=POLICY_V2,
        ruleset=RULESET_V2,
        full_html_drop=False,
        ocr_review=False,
        replacement_rule="enc.replacement_chars",
        mojibake_rule="enc.mojibake_hits",
        outcomes=tuple(policy["outcomes"]),
    )


# -- thresholds -----------------------------------------------------------------------------


@dataclass(frozen=True)
class Threshold:
    """``value >= cut`` (``at_least``) or ``value < cut``; ``index`` is the metric slot."""

    metric: str
    index: int
    at_least: bool
    cut: float | int


@dataclass(frozen=True)
class ComponentRules:
    """Per-component frozen signals (``None`` = no Phase-A rule; never fires)."""

    component: str
    repetition: tuple[Threshold | None, ...]
    ocr: tuple[Threshold | None, ...] | None


@dataclass(frozen=True)
class CompiledPolicy:
    version: str
    digest: str
    file_sha256: str
    params: RuleParams
    components: Mapping[str, ComponentRules]
    provenance: Mapping[str, Any]

    def rules_for(self, component: str) -> ComponentRules:
        rules = self.components.get(component)
        if rules is None:
            raise PolicyError(
                "cleaning policy refused: a manifest component has no frozen thresholds"
            )
        return rules


def _expected_comparator(metric: str) -> str:
    direction = METRICS[METRIC_INDEX[metric]].direction
    _require(direction in ("high", "low"), "signal metric has no suspicious direction")
    return ">=" if direction == "high" else "<"


def _check_threshold(metric: str, entry: Any, component: str) -> Threshold | None:
    if entry is None:
        return None
    _require(isinstance(entry, dict) and set(entry) == THRESHOLD_KEYS, "threshold schema")
    spec = METRICS[METRIC_INDEX[metric]]
    _require(entry["comparator"] == _expected_comparator(metric), "threshold comparator")
    cut = entry["cut"]
    if spec.kind == "count":
        _require(type(cut) is int and cut >= 1, "count threshold cut")
    else:
        _require(type(cut) in (int, float) and 0.0 < float(cut) <= 1.0, "ratio threshold cut")
    for name in ("cut_bin", "quantile_bin"):
        _require(type(entry[name]) is int and 0 <= entry[name] <= 1000, "threshold bins")
    _require(entry["phase_a_rule_id"] == f"{component}.{metric}", "threshold Phase-A rule id")
    impact = entry["phase_a_impact"]
    _require(isinstance(impact, dict) and set(impact) == IMPACT_KEYS, "threshold impact schema")
    for name in ("docs", "bytes"):
        _require(type(impact[name]) is int and impact[name] >= 0, "threshold impact")
    for name in ("docs_pct", "bytes_pct"):
        _require(type(impact[name]) in (int, float), "threshold impact")
    return Threshold(metric, METRIC_INDEX[metric], entry["comparator"] == ">=", cut)


def _check_provenance(provenance: Any, version: str = POLICY_V1) -> None:
    keys = PROVENANCE_KEYS_V2 if version == POLICY_V2 else PROVENANCE_KEYS
    _require(isinstance(provenance, dict) and set(provenance) == keys, "provenance schema")
    if version == POLICY_V2:
        predecessor = provenance["predecessor"]
        _require(
            isinstance(predecessor, dict)
            and set(predecessor) == PREDECESSOR_KEYS
            and predecessor["version"] == POLICY_V1
            and all(_sha(predecessor[k]) for k in PREDECESSOR_KEYS - {"version"}),
            "v2 predecessor provenance",
        )
        _require(
            predecessor["phase_a_receipt_digest"] == provenance["phase_a"]["receipt_digest"],
            "v2 must be frozen from the same Phase-A audit as its predecessor",
        )
    phase_a = provenance["phase_a"]
    _require(isinstance(phase_a, dict) and set(phase_a) == PHASE_A_KEYS, "Phase-A provenance")
    for name in (
        "receipt_digest",
        "result_digest",
        "binding_digest",
        "input_manifest_digest",
        "input_manifest_file_sha256",
        "code_identity",
        "dependency_sha256",
    ):
        _require(_sha(phase_a[name]), f"Phase-A provenance {name}")
    _require(type(phase_a["code_commit"]) is str, "Phase-A provenance code commit")
    _require(phase_a["input_manifest_mode"] in ("production", "authored"), "Phase-A mode")
    _require(type(phase_a["c05_overlay"]) is bool, "Phase-A overlay flag")
    try:  # the HISTORICAL envelope: typed and ranged, never equal to current constants
        check_envelope(phase_a["operational_envelope"])
    except QualityError:
        raise PolicyError("cleaning policy refused: Phase-A operational envelope") from None
    detector = phase_a["detector_policy"]
    _require(
        isinstance(detector, dict)
        and set(detector) == {"version", "digest"}
        and detector["version"] == POLICY_VERSION
        and _sha(detector["digest"]),
        "Phase-A detector policy",
    )
    candidate = provenance["candidate_policy"]
    _require(
        isinstance(candidate, dict)
        and set(candidate) == CANDIDATE_KEYS
        and candidate["artifact"] == CANDIDATE_ARTIFACT
        and candidate["kind"] == CANDIDATE_KIND
        and candidate["band"] == CANDIDATE_BAND
        and candidate["quantile"] == _band_quantile()
        and candidate["status"] == "PROPOSAL_ONLY"
        and _sha(candidate["sha256"])
        and type(candidate["bytes"]) is int,
        "candidate policy provenance",
    )
    _require(provenance["copy_rule"] == COPY_RULE, "copy rule")
    _require(provenance["frozen_by"] == FROZEN_BY, "frozen-by")


def _band_quantile() -> list[int]:
    _, num, den = next(b for b in CANDIDATE_BANDS if b[0] == CANDIDATE_BAND)
    return [num, den]


def _check_common(body: Mapping[str, Any]) -> RuleParams:
    _require(body.get("kind") == POLICY_KIND, "kind")
    version = body.get("version")
    _require(version in SECTION_DIGESTS, "version")
    policy = body.get("policy")
    if not isinstance(policy, dict):
        raise PolicyError("cleaning policy refused: policy section")
    _require(policy.get("detector_policy_version") == POLICY_VERSION, "detector policy version")
    return rule_params(policy, str(version))


def validate_template(body: Mapping[str, Any]) -> RuleParams:
    _require(set(body) == TEMPLATE_KEYS, "template field set")
    _require(body["status"] == TEMPLATE_STATUS, "template status")
    _require(body["thresholds"] is None and body["provenance"] is None, "template thresholds")
    return _check_common(body)


def validate_frozen(body: Mapping[str, Any], file_sha256: str) -> CompiledPolicy:
    """Strict schema, self-digest, pinned rule section and threshold semantics."""
    _require(set(body) == FROZEN_KEYS, "frozen field set")
    _require(body["status"] == FROZEN_STATUS, "policy is not FROZEN (run clean-freeze-policy)")
    _require(body["digest"] == canonical.self_digest(body), "self-digest")
    _require(_sha(body["template_sha256"]), "template SHA-256")
    params = _check_common(body)
    _check_provenance(body["provenance"], params.version)
    thresholds = body["thresholds"]
    if not isinstance(thresholds, dict) or not thresholds:
        raise PolicyError("cleaning policy refused: thresholds")
    if params.version == POLICY_V2:
        _require(
            canonical.digest(thresholds) == body["provenance"]["predecessor"]["thresholds_digest"],
            "v2 thresholds differ from the predecessor's frozen cuts",
        )
    components: dict[str, ComponentRules] = {}
    for component, entry in thresholds.items():
        _require(type(component) is str and bool(component), "threshold component")
        _require(isinstance(entry, dict) and set(entry) == COMPONENT_KEYS, "component thresholds")
        repetition = entry["repetition"]
        _require(
            isinstance(repetition, dict) and sorted(repetition) == sorted(REP_SIGNALS),
            "repetition thresholds",
        )
        ocr = entry["ocr"]
        if component in params.ocr_components:
            _require(
                isinstance(ocr, dict) and sorted(ocr) == sorted(OCR_SIGNALS),
                "OCR thresholds of an OCR-scoped component",
            )
            ocr_rules: tuple[Threshold | None, ...] | None = tuple(
                _check_threshold(m, ocr[m], component) for m in OCR_SIGNALS
            )
        else:
            _require(ocr is None, "OCR thresholds outside the OCR scope")
            ocr_rules = None
        components[component] = ComponentRules(
            component,
            tuple(_check_threshold(m, repetition[m], component) for m in REP_SIGNALS),
            ocr_rules,
        )
    return CompiledPolicy(
        version=str(body["version"]),
        digest=str(body["digest"]),
        file_sha256=file_sha256,
        params=params,
        components=components,
        provenance=body["provenance"],
    )


def load_frozen(path: Path) -> CompiledPolicy:
    body, raw = read_policy(path)
    return validate_frozen(body, hashlib.sha256(raw).hexdigest())


# -- freezing --------------------------------------------------------------------------------


def _copy(rules: Mapping[str, Any], component: str, metric: str) -> dict[str, Any] | None:
    rule = rules.get(f"{component}.{metric}")
    if rule is None:
        return None
    _require(rule.get("detector") == metric and rule.get("action") is None, "candidate rule")
    impact = rule.get("estimated_impact")
    _require(isinstance(impact, dict), "candidate impact")
    entry = {
        "comparator": rule.get("comparator"),
        "cut": rule.get("cut"),
        "cut_bin": rule.get("cut_bin"),
        "quantile_bin": rule.get("quantile_bin"),
        "phase_a_rule_id": rule["id"],
        "phase_a_impact": {k: impact.get(k) for k in sorted(IMPACT_KEYS)},
    }
    _check_threshold(metric, entry, component)
    return entry


def _predecessor(path: Path | None, version: str) -> dict[str, Any] | None:
    """v2 requires its FROZEN v1 predecessor (same audit, identical cuts); v1 takes none."""
    if version == POLICY_V1:
        _require(path is None, "a v1 policy has no predecessor")
        return None
    _require(path is not None, "a v2 freeze requires --predecessor (the frozen v1 policy)")
    assert path is not None
    body, raw = read_policy(path)
    compiled = validate_frozen(body, hashlib.sha256(raw).hexdigest())
    _require(compiled.version == POLICY_V1, "the predecessor must be a FROZEN v1 policy")
    return {
        "version": POLICY_V1,
        "policy_digest": compiled.digest,
        "file_sha256": compiled.file_sha256,
        "phase_a_receipt_digest": body["provenance"]["phase_a"]["receipt_digest"],
        "thresholds_digest": canonical.digest(body["thresholds"]),
    }


def freeze_policy(
    template: Path, audit_output: Path, destination: Path, predecessor: Path | None = None
) -> dict[str, Any]:
    """Copy the Phase-A conservative cuts into a new FROZEN policy file (read-only input).

    v2 additionally requires ``predecessor`` (the frozen v1 policy): the Phase-A audit
    must be the predecessor's (same receipt digest) and the copied cuts must equal the
    predecessor's exactly; the predecessor identity is recorded in the provenance.

    The Phase-A output may come from an earlier accepted implementation (e.g. 32 MiB
    scan chunks). It is verified by :func:`phase_a_history.verify_historical_audit`
    against its OWN recorded identities and artifacts. Its operational envelope is
    preserved as provenance, never compared with current implementation constants.
    """
    from xlm.data.quality.phase_a_history import verify_candidate, verify_historical_audit

    template_body, template_raw = read_policy(template)
    params = validate_template(template_body)
    prior = _predecessor(predecessor, params.version)
    destination = Path(destination)
    _require(not destination.exists() and not destination.is_symlink(), "destination exists")
    _require(destination.absolute().parent.is_dir(), "destination directory does not exist")
    audit_output = Path(audit_output)
    _require(
        not Path(os.path.abspath(destination)).is_relative_to(audit_output.resolve()),
        "destination inside the Phase-A audit output",
    )
    audit = verify_historical_audit(audit_output)
    receipt = audit.receipt
    detector = receipt["detector_policy"]
    entry = receipt["artifacts"][CANDIDATE_ARTIFACT]
    candidate = parse_yaml(audit.artifacts[CANDIDATE_ARTIFACT])  # the hash-verified bytes
    rules = verify_candidate(candidate, audit, kind=CANDIDATE_KIND, band=CANDIDATE_BAND)
    thresholds: dict[str, Any] = {}
    for component in sorted(rules):
        thresholds[component] = {
            "repetition": {m: _copy(rules[component], component, m) for m in REP_SIGNALS},
            "ocr": (
                {m: _copy(rules[component], component, m) for m in OCR_SIGNALS}
                if component in params.ocr_components
                else None
            ),
        }
    _require(bool(thresholds), "candidate policy has no components")
    if prior is not None:
        _require(
            prior["phase_a_receipt_digest"] == receipt["digest"],
            "the predecessor was frozen from a different Phase-A audit",
        )
        _require(
            prior["thresholds_digest"] == canonical.digest(thresholds),
            "copied cuts differ from the predecessor's frozen cuts",
        )
    binding = receipt["binding"]
    body: dict[str, Any] = {
        "kind": POLICY_KIND,
        "version": params.version,
        "status": FROZEN_STATUS,
        "policy": template_body["policy"],
        "thresholds": thresholds,
        "provenance": {
            "phase_a": {
                "receipt_digest": receipt["digest"],
                "result_digest": receipt["result_digest"],
                "binding_digest": receipt["binding_digest"],
                "input_manifest_digest": receipt["input_manifest"]["digest"],
                "input_manifest_file_sha256": receipt["input_manifest"]["file_sha256"],
                "input_manifest_mode": binding["input_manifest"]["mode"],
                "detector_policy": dict(detector),
                "code_commit": receipt["implementation"]["code_commit"],
                "code_identity": receipt["implementation"]["code_identity"],
                "dependency_sha256": receipt["implementation"]["dependency_sha256"],
                "c05_overlay": receipt["overlay"] is not None,
                "operational_envelope": dict(receipt["envelope"]),
            },
            "candidate_policy": {
                "artifact": CANDIDATE_ARTIFACT,
                "kind": CANDIDATE_KIND,
                "band": CANDIDATE_BAND,
                "quantile": _band_quantile(),
                "status": "PROPOSAL_ONLY",
                "sha256": entry["sha256"],
                "bytes": entry["bytes"],
            },
            "copy_rule": COPY_RULE,
            "frozen_by": FROZEN_BY,
        },
        "template_sha256": hashlib.sha256(template_raw).hexdigest(),
    }
    if prior is not None:
        body["provenance"]["predecessor"] = prior
    body["digest"] = canonical.self_digest(body)
    payload = BANNERS[params.version].encode("utf-8") + dump_yaml(body)
    reloaded = parse_yaml(payload)
    _require(reloaded == body, "frozen policy does not round-trip through YAML")
    compiled = validate_frozen(reloaded, hashlib.sha256(payload).hexdigest())
    canonical.write_atomic(destination, payload)
    return {
        "frozen": True,
        "destination": str(destination),
        "policy_digest": compiled.digest,
        "file_sha256": compiled.file_sha256,
        "components": sorted(thresholds),
        "phase_a_receipt_digest": receipt["digest"],
        "candidate_policy_sha256": entry["sha256"],
        "version": params.version,
    }


def threshold_table(compiled: CompiledPolicy) -> list[dict[str, Any]]:
    """Content-free table of every frozen threshold (for reports)."""
    rows: list[dict[str, Any]] = []
    for component in sorted(compiled.components):
        rules = compiled.components[component]
        entries: Sequence[tuple[str, Threshold | None]] = [
            *zip(REP_SIGNALS, rules.repetition, strict=True),
            *(zip(OCR_SIGNALS, rules.ocr, strict=True) if rules.ocr is not None else ()),
        ]
        for metric, threshold in entries:
            rows.append(
                {
                    "component": component,
                    "signal": metric,
                    "family": "ocr" if metric in OCR_SIGNALS else "severe_repetition",
                    "comparator": None
                    if threshold is None
                    else (">=" if threshold.at_least else "<"),
                    "cut": None if threshold is None else threshold.cut,
                }
            )
    return rows
