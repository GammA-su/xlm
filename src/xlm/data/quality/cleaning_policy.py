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
    policy_identity,
)
from xlm.data.quality.scan import QualityError, read_bounded

POLICY_KIND = "xlm_quality_cleaning_policy"
POLICY_V1 = "cleaning_policy_v1"
TEMPLATE_STATUS = "RULES_FROZEN_THRESHOLDS_PENDING"
FROZEN_STATUS = "FROZEN"
# canonical.digest of the template's ``policy`` section (the reviewed v1 rule set).
POLICY_SECTION_DIGEST = "d1eb1990448ea76fbffbba9d44ff016d9dada68caabebe92ed7f61627cfe77e2"
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
RULE_IDS = tuple(name for name, _ in RULES)
RULE_BIT = {name: 1 << n for n, name in enumerate(RULE_IDS)}
DROP_MASK = sum(1 << n for n, (_, action) in enumerate(RULES) if action == DROP)
REVIEW_MASK = sum(1 << n for n, (_, action) in enumerate(RULES) if action == REVIEW)
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
    structured_review_equals: int
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


def rule_params(policy: Mapping[str, Any]) -> RuleParams:
    try:
        section = canonical.digest(policy)
    except canonical.CanonicalError:
        raise PolicyError("cleaning policy refused: policy section is not plain data") from None
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


def _check_provenance(provenance: Any) -> None:
    _require(
        isinstance(provenance, dict) and set(provenance) == PROVENANCE_KEYS, "provenance schema"
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
    _require(body.get("version") == POLICY_V1, "version")
    policy = body.get("policy")
    if not isinstance(policy, dict):
        raise PolicyError("cleaning policy refused: policy section")
    _require(policy.get("detector_policy_version") == POLICY_VERSION, "detector policy version")
    return rule_params(policy)


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
    _check_provenance(body["provenance"])
    thresholds = body["thresholds"]
    if not isinstance(thresholds, dict) or not thresholds:
        raise PolicyError("cleaning policy refused: thresholds")
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


def _candidate_rules(candidate: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    components = candidate.get("components")
    if not isinstance(components, dict):
        raise PolicyError("cleaning policy refused: candidate components")
    out: dict[str, dict[str, Any]] = {}
    for component, entry in components.items():
        _require(type(component) is str and isinstance(entry, dict), "candidate component")
        rules = entry.get("rules")
        _require(isinstance(rules, list), "candidate rules")
        found: dict[str, Any] = {}
        for rule in rules:
            _require(isinstance(rule, dict) and type(rule.get("id")) is str, "candidate rule")
            found[rule["id"]] = rule
        out[component] = found
    return out


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


def freeze_policy(template: Path, audit_output: Path, destination: Path) -> dict[str, Any]:
    """Copy the Phase-A conservative cuts into a new FROZEN policy file (read-only input)."""
    from xlm.data.quality.receipt import load_receipt
    from xlm.data.quality.report import ARTIFACTS
    from xlm.data.quality.runner import _read_binding
    from xlm.data.quality.scan import RECEIPT_FILE

    template_body, template_raw = read_policy(template)
    params = validate_template(template_body)
    destination = Path(destination)
    _require(not destination.exists() and not destination.is_symlink(), "destination exists")
    _require(destination.absolute().parent.is_dir(), "destination directory does not exist")
    audit_output = Path(audit_output)
    _require(
        not Path(os.path.abspath(destination)).is_relative_to(audit_output.resolve()),
        "destination inside the Phase-A audit output",
    )
    receipt = load_receipt(audit_output, ARTIFACTS, RECEIPT_FILE)
    if _read_binding(audit_output) != receipt["binding"]:
        raise PolicyError("cleaning policy refused: Phase-A receipt and binding differ")
    detector = receipt["detector_policy"]
    _require(
        detector == {"version": POLICY_VERSION, "digest": policy_identity()},
        "Phase-A detector policy differs from the current detectors",
    )
    entry = receipt["artifacts"][CANDIDATE_ARTIFACT]
    raw = read_bounded(audit_output / CANDIDATE_ARTIFACT, MAX_POLICY_BYTES, "candidate policy")
    _require(
        (len(raw), hashlib.sha256(raw).hexdigest()) == (entry["bytes"], entry["sha256"]),
        "candidate policy differs from the Phase-A receipt",
    )
    candidate = parse_yaml(raw)
    _require(isinstance(candidate, dict), "candidate policy")
    _require(
        candidate.get("kind") == CANDIDATE_KIND
        and candidate.get("band") == CANDIDATE_BAND
        and candidate.get("status") == "PROPOSAL_ONLY",
        "candidate policy kind/band",
    )
    _require(
        candidate.get("bindings")
        == {
            "audit_binding_digest": receipt["binding_digest"],
            "input_manifest_digest": receipt["input_manifest"]["digest"],
            "detector_policy": detector,
        },
        "candidate policy bindings differ from the Phase-A receipt",
    )
    rules = _candidate_rules(candidate)
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
    binding = receipt["binding"]
    body: dict[str, Any] = {
        "kind": POLICY_KIND,
        "version": POLICY_V1,
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
    body["digest"] = canonical.self_digest(body)
    payload = FROZEN_BANNER.encode("utf-8") + dump_yaml(body)
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
