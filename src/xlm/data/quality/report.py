"""Deterministic Phase-A artifacts from committed units (no source text, no decisions).

Every artifact is a pure function of the units (in manifest order), the audit binding
and the frozen detector policy, so it is byte-identical for every worker count and
re-derivable by ``report``. Candidate threshold bands are a mechanical tail census of
each component's own distribution, labelled ``PROPOSAL_ONLY``; none is executable.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

import numpy as np
import yaml

from xlm.data.evidence_v2 import canonical
from xlm.data.quality.aggregate import (
    ABSENT,
    NBINS,
    NON_NUMERIC,
    NUMERIC_FIELDS,
    OUT_OF_RANGE,
    AggregateError,
    Population,
)
from xlm.data.quality.policy import (
    BOOL_INTERSECTIONS,
    CANDIDATE_BANDS,
    CANDIDATE_RULE,
    CLASS_ORDER,
    COMPONENT_SOURCE_TYPE,
    DETECTOR_SCOPE,
    FLAGS,
    JOINT_PAIRS,
    LANGUAGE_LABEL_KEYS,
    LANGUAGE_SCORE_KEYS,
    METRIC_INDEX,
    METRICS,
    POLICY_VERSION,
    PUBLISHED_CATEGORY_VALUES,
    QUANTILES,
    SIZE_FLAGS,
    STATUS_PROPOSAL,
    MetricSpec,
    bin_bounds,
    coarse_index,
    policy_identity,
)
from xlm.data.quality.review import assign_roles, merge_samples

ARTIFACTS = (
    "quality-audit.json",
    "quality-by-component.json",
    "quality-histograms.json",
    "quality-intersections.json",
    "quality-language.json",
    "review-manifest.jsonl",
    "candidate-policy-conservative.yaml",
    "candidate-policy-moderate.yaml",
    "candidate-policy-aggressive.yaml",
    "quality-summary.md",
)
RUN_BUCKETS = (8, 16, 32, 64, 128, 256)
RUN_METRICS = ("max_char_run", "max_punct_run", "max_space_run", "max_alnum_run", "max_other_run")
PUBLICATION_RATIO_STEP = 5  # 1000 fine bins -> 200 published bins of width 0.005
ACTION_OPTIONS = {
    "A": ["DROP"],
    "C": ["DROP", "TRANSFORM"],
    "D": ["DROP", "TRANSFORM"],
    "E": ["DROP"],
    "F": ["DROP", "TRANSFORM"],
    "G": ["DROP"],
    "H": ["DROP", "TRANSFORM"],
    "I": ["DROP", "TRANSFORM"],
    "J": ["DROP"],
}
SCOPE_BY_DIMENSION = {
    "A": "prose_specific",
    "C": "web_specific",
    "D": "web_specific",
    "E": "universal",
    "F": "universal",
    "G": "universal",
    "H": "universal",
    "I": "pdf_ocr_specific",
    "J": "prose_specific",
}
DEFAULT_SCOPE_TYPES = {
    "universal": None,
    "web_specific": {"web", "mixed_public_domain"},
    "pdf_ocr_specific": {"pdf", "mixed_public_domain"},
    "prose_specific": None,
}
STALENESS = (
    "Any production DROP or TRANSFORM changes the corpus. Cleaning can CREATE new exact "
    "and near duplicates (e.g. '<p>Hello</p>' and '<div>Hello</div>' both become "
    "'Hello'). Every existing C05 dedup membership, contamination proof, lineage group, "
    "split and completion then becomes stale. Required order: cleaned canonical corpus "
    "-> NEW input manifest/seals -> rerun global exact+near dedup -> rerun benchmark "
    "contamination -> rerun lineage grouping and splits -> NEW C05 completion/proof -> "
    "tokenizer fit."
)


CANDIDATE_BANNER = (
    "# PROPOSAL_ONLY - NOT EXECUTABLE. Mechanical tail census for threshold review.\n"
    "# No action has been decided; every rule's `action` is null. Phase A executes nothing.\n"
)


class _PlainDumper(yaml.SafeDumper):
    """Safe YAML without anchors/aliases, so every rule reads on its own."""

    def ignore_aliases(self, data: Any) -> bool:
        return True


def _json(obj: Any) -> bytes:
    return (
        json.dumps(obj, sort_keys=True, indent=1, ensure_ascii=False, allow_nan=False) + "\n"
    ).encode("utf-8")


def _pct(part: int, whole: int) -> float:
    return round(100.0 * part / whole, 6) if whole else 0.0


def impact(docs: int, nbytes: int, pop: Population) -> dict[str, Any]:
    return {
        "docs": int(docs),
        "bytes": int(nbytes),
        "docs_pct": _pct(int(docs), pop.docs),
        "bytes_pct": _pct(int(nbytes), pop.bytes),
    }


def pair_impact(pair: np.ndarray, pop: Population) -> dict[str, Any]:
    return impact(int(pair[0]), int(pair[1]), pop)


def _number(value: float | int) -> float | int:
    number = float(value)
    return int(number) if number.is_integer() else number


# -- quantiles and tails -------------------------------------------------------------------------


def quantile_bin(row: np.ndarray, applicable: int, num: int, den: int) -> int | None:
    if applicable <= 0:
        return None
    rank = -(-num * applicable // den)
    return int(np.searchsorted(np.cumsum(row), rank, side="left"))


def metric_summary(pop: Population, spec: MetricSpec) -> dict[str, Any]:
    n = METRIC_INDEX[spec.name]
    applicable = int(pop.hist_docs[n].sum())
    quantiles: dict[str, Any] = {}
    for label, num, den in QUANTILES:
        b = quantile_bin(pop.hist_docs[n], applicable, num, den)
        quantiles[label] = None if b is None else [_number(x) for x in bin_bounds(spec, b)]
    mean = None
    if spec.kind == "count" and applicable:
        mean = round(int(pop.sums[n]) / applicable, 6)
    return {
        "applicable": applicable,
        "not_applicable": int(pop.na[n]),
        "not_applicable_bytes": int(pop.na_bytes[n]),
        "quantiles_bin_lo_hi": quantiles,
        "max": None if np.isnan(pop.maxima[n]) else _number(pop.maxima[n]),
        "min": None if np.isnan(pop.minima[n]) else _number(pop.minima[n]),
        "mean": mean,
    }


def tail(pop: Population, spec: MetricSpec, num: int, den: int) -> dict[str, Any] | None:
    """The tail census rule (``policy.CANDIDATE_RULE``) with an EXACT comparator.

    High-is-suspicious: cut bin ``c`` = the bin holding the q-quantile, never the clean
    bin 0; the rule is ``value >= lo(c)`` and its impact is exactly the documents in
    bins >= c. Low-is-suspicious: ``c`` = the bin holding the (1-q)-quantile, never the
    clean exact-1.0 ratio bin; the rule is ``value < hi(c)`` and its impact is exactly
    the documents in bins <= c. Bins are half-open on the same floats that are printed,
    so the comparator, the printed cut and the impact agree for every value.
    """
    n = METRIC_INDEX[spec.name]
    row = pop.hist_docs[n]
    applicable = int(row.sum())
    if applicable == 0 or spec.direction == "none":
        return None
    if spec.direction == "high":
        b = quantile_bin(row, applicable, num, den)
        assert b is not None
        c = max(b, 1)
        selected = slice(c, NBINS)
        cut = bin_bounds(spec, c)[0]
        comparator = ">="
    else:
        b = quantile_bin(row, applicable, den - num, den)
        assert b is not None
        c = min(b, NBINS - 2) if spec.kind == "ratio" else b
        selected = slice(0, c + 1)
        cut = bin_bounds(spec, c)[1]
        comparator = "<"
    docs = int(pop.hist_docs[n, selected].sum())
    nbytes = int(pop.hist_bytes[n, selected].sum())
    return {
        "comparator": comparator,
        "cut": _number(cut),
        "quantile_bin": b,
        "cut_bin": c,
        "impact": impact(docs, nbytes, pop),
    }


# -- summaries ----------------------------------------------------------------------------------


def summarize(pop: Population) -> dict[str, Any]:
    flags = {f: pair_impact(pop.flags[n], pop) for n, f in enumerate(FLAGS)}
    presence: dict[str, Any] = {}
    run_buckets: dict[str, Any] = {}
    for n, spec in enumerate(METRICS):
        if spec.kind != "count" or spec.dimension not in {"C", "D", "E", "F", "H", "J"}:
            continue
        presence[spec.name] = impact(
            int(pop.hist_docs[n, 1:].sum()), int(pop.hist_bytes[n, 1:].sum()), pop
        )
        if spec.name in RUN_METRICS:
            buckets = {}
            for threshold in RUN_BUCKETS:
                first = _first_bin_at_least(spec, threshold)
                buckets[f">={threshold}"] = impact(
                    int(pop.hist_docs[n, first:].sum()), int(pop.hist_bytes[n, first:].sum()), pop
                )
            run_buckets[spec.name] = buckets
    # Ratio bin >= 1 is EXACTLY value >= 0.001 (exact float edges), hence ``ge``.
    ratio_presence = {
        name: {
            "comparator": ">=",
            "cut": 0.001,
            **impact(
                int(pop.hist_docs[METRIC_INDEX[name], 1:].sum()),
                int(pop.hist_bytes[METRIC_INDEX[name], 1:].sum()),
                pop,
            ),
        }
        for name in ("page_number_line_ratio", "hyphen_break_ratio", "single_char_line_ratio")
    }
    return {
        "documents": pop.docs,
        "canonical_bytes": pop.bytes,
        "jsonl_line_bytes": pop.line_bytes,
        "size_buckets": {f: flags[f] for f in SIZE_FLAGS},
        "flags": {f: v for f, v in flags.items() if f not in SIZE_FLAGS},
        "presence_count_ge_1": presence,
        "presence_ratio_ge_0_001": ratio_presence,
        "character_run_buckets": run_buckets,
        "classes": {c: pair_impact(pop.classes[n], pop) for n, c in enumerate(CLASS_ORDER)},
        "bool_intersections": {
            name: pair_impact(pop.bools[n], pop)
            for n, (name, _, _) in enumerate(BOOL_INTERSECTIONS)
        },
        "metrics": {spec.name: metric_summary(pop, spec) for spec in METRICS},
    }


def _first_bin_at_least(spec: MetricSpec, threshold: int) -> int:
    for b in range(NBINS):
        if bin_bounds(spec, b)[0] >= threshold:
            return b
    return NBINS


def histograms(pop: Population) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for n, spec in enumerate(METRICS):
        rows: dict[int, list[int]] = {}
        for b in np.flatnonzero(pop.hist_docs[n]).tolist():
            key = b // PUBLICATION_RATIO_STEP if spec.kind == "ratio" and b < 1000 else b
            if spec.kind == "ratio" and b >= 1000:
                key = 1000 // PUBLICATION_RATIO_STEP
            entry = rows.setdefault(key, [0, 0])
            entry[0] += int(pop.hist_docs[n, b])
            entry[1] += int(pop.hist_bytes[n, b])
        cells = []
        for key in sorted(rows):
            if spec.kind == "ratio":
                lo = key * PUBLICATION_RATIO_STEP / 1000
                hi = min(1.0, (key + 1) * PUBLICATION_RATIO_STEP / 1000)
                if key * PUBLICATION_RATIO_STEP >= 1000:
                    lo = hi = 1.0
            else:
                lo, hi = bin_bounds(spec, key)
            cells.append([_number(lo), _number(hi), rows[key][0], rows[key][1]])
        out[spec.name] = {"bins_lo_hi_docs_bytes": cells, "not_applicable": int(pop.na[n])}
    return out


def intersections(pop: Population) -> dict[str, Any]:
    joints: dict[str, Any] = {}
    for n, (name, a, b) in enumerate(JOINT_PAIRS):
        sa, sb = METRICS[METRIC_INDEX[a]], METRICS[METRIC_INDEX[b]]
        cells = []
        grid = pop.joints[n]
        for i, j in zip(*np.nonzero(grid[:, :, 0]), strict=True):
            cells.append(
                {
                    a: _axis(sa, int(i), grid.shape[0]),
                    b: _axis(sb, int(j), grid.shape[1]),
                    **impact(int(grid[i, j, 0]), int(grid[i, j, 1]), pop),
                }
            )
        joints[name] = {"x": a, "y": b, "cells": cells}
    return {
        "bool": {
            name: {"left": left, "right": right, **pair_impact(pop.bools[n], pop)}
            for n, (name, left, right) in enumerate(BOOL_INTERSECTIONS)
        },
        "joint_coarse": joints,
    }


def _axis(spec: MetricSpec, index: int, size: int) -> Any:
    if index == size - 1:
        return "not_applicable"
    edges = spec.edges
    lo = None if index == 0 else edges[index - 1]
    hi = None if index >= len(edges) else edges[index]
    return [lo, hi]


def _top(table: Mapping[str, list[int]]) -> dict[str, Any]:
    """Deterministic top values by (documents desc, value asc) plus an exact remainder."""
    ordered = sorted(table.items(), key=lambda kv: (-kv[1][0], kv[0]))
    shown = ordered[:PUBLISHED_CATEGORY_VALUES]
    rest = ordered[PUBLISHED_CATEGORY_VALUES:]
    return {
        "values": {value: {"docs": e[0], "bytes": e[1]} for value, e in shown},
        "other": {
            "distinct": len(rest),
            "docs": sum(e[0] for _, e in rest),
            "bytes": sum(e[1] for _, e in rest),
        },
    }


def _numeric(array: np.ndarray, docs: int) -> dict[str, Any]:
    spec = METRICS[METRIC_INDEX["alpha_ratio"]]  # any ratio spec: exact 1000-bin bounds
    bins = array[:NBINS]
    in_range = int(bins.sum())
    quantiles = {}
    for label, num, den in QUANTILES:
        b = quantile_bin(bins, in_range, num, den)
        quantiles[label] = None if b is None else [_number(x) for x in bin_bounds(spec, b)]
    return {
        "present": docs - int(array[ABSENT]),
        "in_range": in_range,
        "out_of_range": int(array[OUT_OF_RANGE]),
        "non_numeric": int(array[NON_NUMERIC]),
        "absent": int(array[ABSENT]),
        "exactly_1_0": int(bins[NBINS - 1]),
        "below_0_5": {"comparator": "<", "cut": 0.5, "docs": int(bins[:500].sum())},
        "quantiles_bin_lo_hi": quantiles,
        "bins_nonzero": [[int(b), int(bins[b])] for b in np.flatnonzero(bins)],
    }


def language(pop: Population) -> dict[str, Any]:
    stats = pop.language
    docs = pop.docs
    numeric = {name: _numeric(stats.numeric[name], docs) for name in NUMERIC_FIELDS}
    fractions = {}
    for key in LANGUAGE_SCORE_KEYS:
        fractions[key] = round(numeric[key]["present"] / docs, 6) if docs else 0.0
    for key in LANGUAGE_LABEL_KEYS:
        present = sum(e[0] for e in stats.categories[key].values())
        fractions[key] = round(present / docs, 6) if docs else 0.0
    best = max(fractions.values(), default=0.0)
    if best >= 0.99:
        assessment = "row-level language evidence on (nearly) every document"
    elif best > 0:
        assessment = "row-level language evidence on part of the documents"
    else:
        assessment = "inherited only: no row-level language evidence in canonical metadata"
    confidence = numeric.pop("language_confidence")
    return {
        "documents": docs,
        "vocabulary_note": (
            "Values are bounded categories only: language-code-shaped values, the split and "
            "document-kind allowlists, adapter provenance categories (exact-literal SHA-256 "
            "match); anything else is <unrecognized>. No source string is copied."
        ),
        "language_field": _top(stats.categories["language"]),
        "language_confidence": confidence,
        "language_confidence_constant_1_0": docs > 0 and confidence["exactly_1_0"] == docs,
        "row_level_evidence_fraction": fractions,
        "scores": numeric,
        "labels": {key: _top(stats.categories[key]) for key in LANGUAGE_LABEL_KEYS},
        "provenance": _top(stats.categories["provenance"]),
        "documents_with_unrecognized_language_like_keys": stats.unrecognized_language_keys,
        "document_kind": _top(stats.categories["document_kind"]),
        "split_field": _top(stats.categories["split"]),
        "assessment": assessment,
    }


# -- candidate policies -------------------------------------------------------------------------


def _scope(spec: MetricSpec, component: str) -> tuple[str, bool]:
    scope = SCOPE_BY_DIMENSION[spec.dimension]
    types = DEFAULT_SCOPE_TYPES[scope]
    applies = types is None or COMPONENT_SOURCE_TYPE.get(component) in types
    return scope, applies


def candidate_policy(
    band: str, num: int, den: int, scopes: Mapping[str, Population], bindings: Mapping[str, Any]
) -> dict[str, Any]:
    components: dict[str, Any] = {}
    for scope_name in sorted(scopes):
        if not scope_name.startswith("component:"):
            continue
        component = scope_name.split(":", 1)[1]
        pop = scopes[scope_name]
        rules = []
        for flag in ("empty", "whitespace_only"):
            n = FLAGS.index(flag)
            rules.append(
                {
                    "id": f"{component}.{flag}",
                    "detector": flag,
                    "dimension": "B",
                    "definition": "flag (no threshold)",
                    "scope_proposal": "universal",
                    "applies_by_default": True,
                    "action": None,
                    "action_options": ["DROP"],
                    "estimated_impact": pair_impact(pop.flags[n], pop),
                }
            )
        for spec in METRICS:
            if not spec.candidate:
                continue
            result = tail(pop, spec, num, den)
            if result is None:
                continue
            scope, applies = _scope(spec, component)
            rules.append(
                {
                    "id": f"{component}.{spec.name}",
                    "detector": spec.name,
                    "dimension": spec.dimension,
                    "definition": f"value {result['comparator']} {result['cut']!r}",
                    "comparator": result["comparator"],
                    "cut": result["cut"],
                    "cut_bin": result["cut_bin"],
                    "quantile_bin": result["quantile_bin"],
                    "scope_proposal": scope,
                    "applies_by_default": applies,
                    "action": None,
                    "action_options": list(ACTION_OPTIONS[spec.dimension]),
                    "estimated_impact": result["impact"],
                }
            )
        components[component] = {
            "source_type_prior": COMPONENT_SOURCE_TYPE.get(component, "unknown"),
            "documents": pop.docs,
            "canonical_bytes": pop.bytes,
            "rules": rules,
        }
    return {
        "kind": "xlm_quality_candidate_policy_v2",
        "status": STATUS_PROPOSAL,
        "executable": False,
        "band": band,
        "derivation": (
            f"Mechanical tail census at the component's {num}/{den} quantile, NOT a reviewed "
            f"threshold. {CANDIDATE_RULE} Each rule's `estimated_impact` is exactly the "
            f"documents satisfying `value <comparator> cut`. Impacts are MARGINAL per rule; "
            f"the union of rules is not derivable from marginal histograms and needs a "
            f"Phase-B dry run."
        ),
        "action_schema": {
            "KEEP": "retain unchanged",
            "DROP": "remove the whole document",
            "TRANSFORM": (
                "only a high-confidence, semantically safe, reviewed edit; never lowercase, "
                "ASCII-fold, strip punctuation/indentation/Markdown/math, collapse arbitrary "
                "whitespace, truncate, rewrite, paraphrase, summarize or LLM-rewrite"
            ),
            "action": "null until an operator records a reviewed decision",
        },
        "actions_executed_in_phase_a": [],
        "detector_scope_proposal": DETECTOR_SCOPE,
        "staleness": STALENESS,
        "bindings": dict(bindings),
        "components": components,
    }


def near_bins(scopes: Mapping[str, Population]) -> dict[str, dict[str, int | None]]:
    """Coarse bin holding each component's moderate-band cut (review near-threshold)."""
    _, num, den = next(b for b in CANDIDATE_BANDS if b[0] == "moderate")
    out: dict[str, dict[str, int | None]] = {}
    for scope_name, pop in scopes.items():
        if not scope_name.startswith("component:"):
            continue
        component = scope_name.split(":", 1)[1]
        entries: dict[str, int | None] = {}
        for spec in METRICS:
            if not spec.review:
                continue
            result = tail(pop, spec, num, den)
            # The coarse bin holding the cut BIN (its lower edge), for either direction.
            entries[spec.name] = (
                None
                if result is None
                else coarse_index(spec, float(bin_bounds(spec, result["cut_bin"])[0]))
            )
        out[component] = entries
    return out


# -- assembly -------------------------------------------------------------------------------------


def scope_names(file: Mapping[str, Any]) -> list[str]:
    upstream = file["upstream_component"] or "-"
    return [
        "global",
        f"component:{file['component']}",
        f"allocation:{file['component']}|{file['view']}|{upstream}",
        f"source:{file['source_key']}",
    ]


def build_artifacts(
    binding: Mapping[str, Any], units: Iterable[Mapping[str, Any]]
) -> tuple[dict[str, bytes], str]:
    """All artifacts (name -> bytes) and the worker-independent result digest."""
    overlay = binding["overlay"] is not None
    names = ("all", "c05_kept", "c05_removed") if overlay else ("all",)
    scopes: dict[str, dict[str, Population]] = {}
    samples: dict[str, dict[str, list[Any]]] = {}
    for unit in units:
        parts = {k: Population.from_json(v) for k, v in unit["populations"].items()}
        if overlay:
            if not set(parts) <= {"c05_kept", "c05_removed"}:
                raise AggregateError("overlay unit population names")
        elif set(parts) - {"all"}:
            raise AggregateError("unit population names")
        for scope in scope_names(unit["file"]):
            target = scopes.setdefault(scope, {n: Population() for n in names})
            for name, pop in parts.items():
                target[name].merge(pop)
                if overlay:
                    target["all"].merge(pop)
        merge_samples(samples.setdefault(unit["file"]["component"], {}), unit["review"])
    ordered = sorted(scopes)
    bindings = {
        "audit_binding_digest": binding["digest"],
        "input_manifest_digest": binding["input_manifest"]["digest"],
        "detector_policy": binding["detector_policy"],
    }
    header = {
        "phase": "A_READ_ONLY_AUDIT",
        "completion": (
            "NOT a completion signal: valid only together with a quality-audit-receipt.json "
            "that passes strict validation (`report` re-derives and verifies it)"
        ),
        "mode": binding["input_manifest"]["mode"],
        "populations": list(names),
        "population_meaning": {
            "all": "every document named by the input manifest",
            "c05_kept": "documents in the authenticated C05 kept membership (overlay only)",
            "c05_removed": "documents C05 excluded or deduplicated (overlay only)",
        },
        "bindings": bindings,
        "content_free": True,
        "corpus_modified": False,
    }
    artifacts: dict[str, bytes] = {}
    artifacts["quality-audit.json"] = _json(
        {
            "kind": "xlm_quality_audit_v2",
            **header,
            "input_manifest": binding["input_manifest"],
            "overlay": binding["overlay"],
            "global": {n: summarize(scopes["global"][n]) for n in names},
            "metric_definitions": {
                m.name: {
                    "dimension": m.dimension,
                    "kind": m.kind,
                    "suspicious_tail": m.direction,
                    "description": m.description,
                }
                for m in METRICS
            },
            "heuristics_note": (
                "Every detector is a bounded heuristic measurement. Repetition (G) uses "
                "whitespace word units, not a tokenizer. Quantiles are histogram bins "
                "[lo, hi): exact below 16 for counts, width 0.001 for ratios."
            ),
            "staleness": STALENESS,
        }
    )
    artifacts["quality-by-component.json"] = _json(
        {
            "kind": "xlm_quality_by_component_v1",
            **header,
            "scopes": {s: {n: summarize(scopes[s][n]) for n in names} for s in ordered},
        }
    )
    published = [s for s in ordered if s == "global" or s.startswith("component:")]
    artifacts["quality-histograms.json"] = _json(
        {
            "kind": "xlm_quality_histograms_v1",
            **header,
            "resolution": "count bins unchanged; ratio bins published at width 0.005",
            "scopes": {s: {n: histograms(scopes[s][n]) for n in names} for s in published},
        }
    )
    artifacts["quality-intersections.json"] = _json(
        {
            "kind": "xlm_quality_intersections_v1",
            **header,
            "scopes": {s: {n: intersections(scopes[s][n]) for n in names} for s in published},
        }
    )
    artifacts["quality-language.json"] = _json(
        {
            "kind": "xlm_quality_language_v1",
            **header,
            "note": (
                "Existing evidence only; no language classifier was run. A component whose "
                "assessment is 'inherited only' has no row-level verification."
            ),
            "scopes": {s: {n: language(scopes[s][n]) for n in names} for s in ordered},
        }
    )
    component_scopes = {s: scopes[s]["all"] for s in ordered if s.startswith("component:")}
    rows = assign_roles(samples, near_bins(component_scopes))
    artifacts["review-manifest.jsonl"] = b"".join(
        json.dumps(r, sort_keys=True, ensure_ascii=False).encode("utf-8") + b"\n" for r in rows
    )
    for band, num, den in CANDIDATE_BANDS:
        body = yaml.dump(
            candidate_policy(band, num, den, component_scopes, bindings),
            Dumper=_PlainDumper,
            sort_keys=True,
            allow_unicode=True,
            width=100,
        )
        artifacts[f"candidate-policy-{band}.yaml"] = (CANDIDATE_BANNER + body).encode("utf-8")
    artifacts["quality-summary.md"] = summary_markdown(scopes, names, binding, len(rows))
    result = canonical.digest(
        {name: hashlib.sha256(data).hexdigest() for name, data in sorted(artifacts.items())}
    )
    return artifacts, result


def summary_markdown(
    scopes: Mapping[str, Mapping[str, Population]],
    names: Sequence[str],
    binding: Mapping[str, Any],
    review_rows: int,
) -> bytes:
    lines = [
        "# XLM Phase-A quality audit summary (content-free)",
        "",
        f"Detector policy `{POLICY_VERSION}` (`{policy_identity()[:16]}`), input manifest "
        f"`{binding['input_manifest']['digest'][:16]}` ({binding['input_manifest']['mode']}).",
        "All numbers are measurements. No document was modified, dropped or transformed.",
        "Candidate bands in `candidate-policy-*.yaml` are PROPOSAL_ONLY tail censuses.",
        f"Review manifest rows (locators only): {review_rows}.",
        "",
    ]
    indicators = (
        ("empty+ws", ("empty", "whitespace_only")),
        ("<64 ch", ("chars_lt64",)),
        (">1 MiB", ("bytes_gt1MiB",)),
        ("full HTML", ("markup_full_html",)),
        ("any markup", ("markup_any",)),
        ("boilerplate", ("boilerplate_any",)),
        ("URL", ("has_url",)),
        ("control", ("control_any",)),
    )
    for name in names:
        lines += [
            f"## Population `{name}` - presence indicators (% docs / % bytes)",
            "",
            "| scope | docs | GB | " + " | ".join(label for label, _ in indicators) + " |",
            "|---|---:|---:|" + "---:|" * len(indicators),
        ]
        for scope in sorted(scopes):
            if scope != "global" and not scope.startswith("component:"):
                continue
            pop = scopes[scope][name]
            cells = []
            for _, flags in indicators:
                docs = sum(int(pop.flags[FLAGS.index(f), 0]) for f in flags)
                nbytes = sum(int(pop.flags[FLAGS.index(f), 1]) for f in flags)
                cells.append(f"{_pct(docs, pop.docs):.3f} / {_pct(nbytes, pop.bytes):.3f}")
            lines.append(
                f"| {scope} | {pop.docs:,} | {pop.bytes / 1e9:.3f} | " + " | ".join(cells) + " |"
            )
        lines.append("")
    lines += [
        "## Language evidence (population `all`)",
        "",
        "| scope | assessment | confidence constant 1.0 |",
        "|---|---|---|",
    ]
    for scope in sorted(scopes):
        if scope.startswith("component:"):
            summary = language(scopes[scope]["all"])
            lines.append(
                f"| {scope} | {summary['assessment']} | "
                f"{summary['language_confidence_constant_1_0']} |"
            )
    lines += ["", "## Staleness", "", STALENESS, ""]
    return "\n".join(lines).encode("utf-8")
