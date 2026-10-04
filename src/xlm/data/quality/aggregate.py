"""Mergeable, exact, content-free population statistics.

A population (all documents, or the C05-kept / C05-removed documents of a file) is a
set of integer arrays: per-metric histograms (documents and canonical UTF-8 bytes per
bin), not-applicable counts, integer sums, extrema, flag / class / intersection
counts and threshold-free joint histograms, plus language-metadata statistics.

Every quantity is an integer (byte sums are accumulated in float64 only while they
are far below 2**53 and converted back exactly), so merging is associative and
commutative: worker count, chunking and merge order cannot change a result.

Language metadata never carries a source string into an artifact: numeric evidence
goes into dense fixed histograms, and every categorical value is first mapped onto a
bounded vocabulary (:mod:`policy`); anything else becomes ``<unrecognized>``.
Categorical maps are merged exactly without caps; a map larger than the hard limit
refuses, and the final union decides that, so the outcome is order independent.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np
import numpy.typing as npt

from xlm.data.quality.policy import (
    BOOL_INTERSECTIONS,
    CATEGORY_HARD_LIMIT,
    CLASS_ORDER,
    DOCUMENT_KIND_VALUES,
    FLAG_INDEX,
    FLAGS,
    JOINT_PAIRS,
    LANGUAGE_CODE_PATTERN,
    LANGUAGE_LABEL_KEYS,
    LANGUAGE_PROVENANCE_KEY,
    LANGUAGE_SCORE_KEYS,
    METRIC_INDEX,
    METRICS,
    PROVENANCE_CATEGORIES,
    RATIO_FINE_EDGES,
    SPLIT_VALUES,
    UNRECOGNIZED,
)

NBINS = 1001
N_METRICS = len(METRICS)
IS_RATIO = np.array([m.kind == "ratio" for m in METRICS])
IS_INTEGRAL = np.array([m.kind == "count" for m in METRICS])
CLASS_INDEX = {c: n for n, c in enumerate(CLASS_ORDER)}
EXACT_FLOAT_LIMIT = 2**52
RATIO_EDGES_ARRAY = np.asarray(RATIO_FINE_EDGES, dtype=np.float64)

# Numeric language histograms: bins 0..1000, then three tail slots.
OUT_OF_RANGE, NON_NUMERIC, ABSENT = NBINS, NBINS + 1, NBINS + 2
NUMERIC_SLOTS = NBINS + 3
NUMERIC_FIELDS = ("language_confidence", *LANGUAGE_SCORE_KEYS)
CATEGORY_FIELDS = ("language", "document_kind", "split", "provenance", *LANGUAGE_LABEL_KEYS)
KNOWN_LANGUAGE_KEYS = frozenset(
    (*LANGUAGE_SCORE_KEYS, *LANGUAGE_LABEL_KEYS, LANGUAGE_PROVENANCE_KEY)
)
_LANGUAGE_CODE = re.compile(LANGUAGE_CODE_PATTERN)
_SPLITS = frozenset(SPLIT_VALUES)
_KINDS = frozenset(DOCUMENT_KIND_VALUES)


class AggregateError(ValueError):
    """Content-free failure while building or merging statistics."""


def ratio_bins(column: npt.NDArray[np.float64]) -> npt.NDArray[np.int64]:
    """Vectorized :func:`policy.ratio_bin` on the exact float edges (no NaN input)."""
    index = np.searchsorted(RATIO_EDGES_ARRAY, column, side="right").astype(np.int64) - 1
    return np.clip(index, 0, NBINS - 1)


def bin_matrix(values: npt.NDArray[np.float64]) -> npt.NDArray[np.int64]:
    """Vectorized :func:`policy.metric_bin`; NaN (not applicable) becomes -1."""
    out = np.full(values.shape, -1, dtype=np.int64)
    present = ~np.isnan(values)
    ratio = values[:, IS_RATIO]
    sub = out[:, IS_RATIO]
    mask = ~np.isnan(ratio)
    sub[mask] = ratio_bins(ratio[mask])
    out[:, IS_RATIO] = sub
    counts = np.floor(values[:, ~IS_RATIO])
    count_out = out[:, ~IS_RATIO]
    cmask = ~np.isnan(counts)
    flat = np.maximum(counts[cmask], 0)
    mantissa, exponent = np.frexp(flat)
    octave = exponent.astype(np.int64) - 1
    sub_bin = np.floor((mantissa * 2 - 1) * 8).astype(np.int64)
    large = 16 + (octave - 4) * 8 + sub_bin
    count_out[cmask] = np.where(flat < 16, flat.astype(np.int64), large)
    out[:, ~IS_RATIO] = count_out
    if np.any(out[present] >= NBINS):
        raise AggregateError("metric value outside the histogram range")
    return out


def coarse_matrix(values: npt.NDArray[np.float64], metric: int) -> npt.NDArray[np.int64]:
    """``bisect_right`` on the metric's coarse edges; NaN becomes -1."""
    column = values[:, metric]
    edges = np.asarray(METRICS[metric].edges, dtype=np.float64)
    index = np.searchsorted(edges, column, side="right").astype(np.int64)
    index[np.isnan(column)] = -1
    return index


# -- bounded vocabularies --------------------------------------------------------------


def language_code(value: Any) -> str:
    if type(value) is str and len(value) <= 16 and _LANGUAGE_CODE.fullmatch(value):
        return value
    return UNRECOGNIZED


def provenance_category(value: Any) -> str:
    if type(value) is not str:
        return UNRECOGNIZED
    digest = hashlib.sha256(value.encode("utf-8", "surrogatepass")).hexdigest()
    return PROVENANCE_CATEGORIES.get(digest, UNRECOGNIZED)


def _numeric_slot(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return NON_NUMERIC
    number = float(value)
    if not 0.0 <= number <= 1.0:  # NaN fails this comparison as well
        return OUT_OF_RANGE
    return min(max(int(np.searchsorted(RATIO_EDGES_ARRAY, number, side="right")) - 1, 0), 1000)


class LanguageStats:
    """Existing language evidence only; no free source string is retained."""

    def __init__(self) -> None:
        self.numeric = {name: np.zeros(NUMERIC_SLOTS, dtype=np.int64) for name in NUMERIC_FIELDS}
        self.categories: dict[str, dict[str, list[int]]] = {f: {} for f in CATEGORY_FIELDS}
        self.unrecognized_language_keys = 0

    def _count(self, field: str, value: str, nbytes: int) -> None:
        table = self.categories[field]
        entry = table.get(value)
        if entry is None:
            if len(table) >= CATEGORY_HARD_LIMIT:
                raise AggregateError("categorical language vocabulary exceeds its hard limit")
            table[value] = [1, nbytes]
        else:
            entry[0] += 1
            entry[1] += nbytes

    def add(self, row: Mapping[str, Any], nbytes: int) -> None:
        self._count("language", language_code(row.get("language")), nbytes)
        split = row.get("split")
        self._count("split", split if split in _SPLITS else UNRECOGNIZED, nbytes)
        kind = row.get("document_kind")
        self._count("document_kind", kind if kind in _KINDS else UNRECOGNIZED, nbytes)
        self.numeric["language_confidence"][_numeric_slot(row.get("language_confidence"))] += 1
        metadata = row.get("source_metadata")
        if not isinstance(metadata, Mapping):
            metadata = {}
        for key in LANGUAGE_SCORE_KEYS:
            self.numeric[key][_numeric_slot(metadata[key]) if key in metadata else ABSENT] += 1
        for key in LANGUAGE_LABEL_KEYS:
            if key in metadata:
                self._count(key, language_code(metadata[key]), nbytes)
        if LANGUAGE_PROVENANCE_KEY in metadata:
            self._count(
                "provenance", provenance_category(metadata[LANGUAGE_PROVENANCE_KEY]), nbytes
            )
        for key in metadata:
            low = key.lower() if isinstance(key, str) else ""
            if ("lang" in low or "lid" in low) and key not in KNOWN_LANGUAGE_KEYS:
                # Only the count of such documents; key names are source-derived text.
                self.unrecognized_language_keys += 1
                break

    def merge(self, other: LanguageStats) -> None:
        for name in NUMERIC_FIELDS:
            self.numeric[name] += other.numeric[name]
        for field, table in other.categories.items():
            mine = self.categories[field]
            for value, (docs, nbytes) in table.items():
                entry = mine.get(value)
                if entry is None:
                    mine[value] = [docs, nbytes]
                else:
                    entry[0] += docs
                    entry[1] += nbytes
            if len(mine) > CATEGORY_HARD_LIMIT:
                raise AggregateError("categorical language vocabulary exceeds its hard limit")
        self.unrecognized_language_keys += other.unrecognized_language_keys

    def to_json(self) -> dict[str, Any]:
        return {
            "numeric": {
                name: [[int(b), int(array[b])] for b in np.flatnonzero(array)]
                for name, array in self.numeric.items()
            },
            "categories": {
                field: {value: list(entry) for value, entry in sorted(table.items())}
                for field, table in self.categories.items()
            },
            "unrecognized_language_keys": self.unrecognized_language_keys,
        }

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> LanguageStats:
        stats = cls()
        if set(data["numeric"]) != set(NUMERIC_FIELDS):
            raise AggregateError("unit language numeric fields differ from the policy")
        if set(data["categories"]) != set(CATEGORY_FIELDS):
            raise AggregateError("unit language categorical fields differ from the policy")
        for name, cells in data["numeric"].items():
            for slot, count in cells:
                if not 0 <= slot < NUMERIC_SLOTS:
                    raise AggregateError("unit language histogram slot out of range")
                stats.numeric[name][slot] = count
        for field, table in data["categories"].items():
            if len(table) > CATEGORY_HARD_LIMIT:
                raise AggregateError("categorical language vocabulary exceeds its hard limit")
            stats.categories[field] = {value: [int(e[0]), int(e[1])] for value, e in table.items()}
        stats.unrecognized_language_keys = int(data["unrecognized_language_keys"])
        return stats


class Population:
    """Statistics of one document population (see module docstring)."""

    def __init__(self) -> None:
        self.docs = 0
        self.bytes = 0
        self.line_bytes = 0
        self.hist_docs = np.zeros((N_METRICS, NBINS), dtype=np.int64)
        self.hist_bytes = np.zeros((N_METRICS, NBINS), dtype=np.int64)
        self.na = np.zeros(N_METRICS, dtype=np.int64)
        self.na_bytes = np.zeros(N_METRICS, dtype=np.int64)
        self.sums = np.zeros(N_METRICS, dtype=np.int64)
        self.maxima = np.full(N_METRICS, np.nan)
        self.minima = np.full(N_METRICS, np.nan)
        self.flags = np.zeros((len(FLAGS), 2), dtype=np.int64)
        self.classes = np.zeros((len(CLASS_ORDER), 2), dtype=np.int64)
        self.bools = np.zeros((len(BOOL_INTERSECTIONS), 2), dtype=np.int64)
        self.joints = [
            np.zeros(
                (
                    len(METRICS[METRIC_INDEX[a]].edges) + 2,
                    len(METRICS[METRIC_INDEX[b]].edges) + 2,
                    2,
                ),
                dtype=np.int64,
            )
            for _, a, b in JOINT_PAIRS
        ]
        self.language = LanguageStats()

    # -- accumulation ----------------------------------------------------------------

    def add_batch(
        self,
        values: npt.NDArray[np.float64],
        nbytes: npt.NDArray[np.int64],
        line_bytes: int,
        flag_bits: npt.NDArray[np.int64],
        classes: npt.NDArray[np.int64],
    ) -> None:
        """Add ``len(nbytes)`` documents (rows of ``values``; NaN = not applicable)."""
        rows = int(nbytes.shape[0])
        if rows == 0:
            return
        weights = nbytes.astype(np.float64)
        if float(weights.sum()) >= EXACT_FLOAT_LIMIT:
            raise AggregateError("batch byte total exceeds exact accumulation range")
        self.docs += rows
        self.bytes += int(nbytes.sum())
        self.line_bytes += line_bytes
        bins = bin_matrix(values)
        for metric in range(N_METRICS):
            column = bins[:, metric]
            present = column >= 0
            self.na[metric] += rows - int(present.sum())
            self.na_bytes[metric] += int(nbytes[~present].sum())
            if not present.any():
                continue
            selected = column[present]
            self.hist_docs[metric] += np.bincount(selected, minlength=NBINS)
            self.hist_bytes[metric] += np.rint(
                np.bincount(selected, weights=weights[present], minlength=NBINS)
            ).astype(np.int64)
            raw = values[present, metric]
            high, low = float(raw.max()), float(raw.min())
            if np.isnan(self.maxima[metric]) or high > self.maxima[metric]:
                self.maxima[metric] = high
            if np.isnan(self.minima[metric]) or low < self.minima[metric]:
                self.minima[metric] = low
            if IS_INTEGRAL[metric]:
                self.sums[metric] += int(np.rint(raw.sum()))
        for flag in range(len(FLAGS)):
            hit = ((flag_bits >> flag) & 1).astype(bool)
            if hit.any():
                self.flags[flag, 0] += int(hit.sum())
                self.flags[flag, 1] += int(nbytes[hit].sum())
        self.classes[:, 0] += np.bincount(classes, minlength=len(CLASS_ORDER))
        self.classes[:, 1] += np.rint(
            np.bincount(classes, weights=weights, minlength=len(CLASS_ORDER))
        ).astype(np.int64)
        for n, (_, left, right) in enumerate(BOOL_INTERSECTIONS):
            hit = _operand(left, values, flag_bits, classes) & _operand(
                right, values, flag_bits, classes
            )
            if hit.any():
                self.bools[n, 0] += int(hit.sum())
                self.bools[n, 1] += int(nbytes[hit].sum())
        for n, (_, a, b) in enumerate(JOINT_PAIRS):
            ia = coarse_matrix(values, METRIC_INDEX[a])
            ib = coarse_matrix(values, METRIC_INDEX[b])
            # Not applicable goes to the last row/column.
            ia[ia < 0] = self.joints[n].shape[0] - 1
            ib[ib < 0] = self.joints[n].shape[1] - 1
            cells = self.joints[n].shape[1]
            flat = ia * cells + ib
            size = self.joints[n].shape[0] * cells
            self.joints[n][:, :, 0] += np.bincount(flat, minlength=size).reshape(-1, cells)
            self.joints[n][:, :, 1] += (
                np.rint(np.bincount(flat, weights=weights, minlength=size))
                .astype(np.int64)
                .reshape(-1, cells)
            )

    def add_language(self, row: Mapping[str, Any], nbytes: int) -> None:
        """Language evidence already present in the canonical row (no classification)."""
        self.language.add(row, nbytes)

    # -- merging / serialization ---------------------------------------------------------

    def merge(self, other: Population) -> None:
        self.docs += other.docs
        self.bytes += other.bytes
        self.line_bytes += other.line_bytes
        self.hist_docs += other.hist_docs
        self.hist_bytes += other.hist_bytes
        self.na += other.na
        self.na_bytes += other.na_bytes
        self.sums += other.sums
        self.maxima = np.fmax(self.maxima, other.maxima)
        self.minima = np.fmin(self.minima, other.minima)
        self.flags += other.flags
        self.classes += other.classes
        self.bools += other.bools
        for mine, theirs in zip(self.joints, other.joints, strict=True):
            mine += theirs
        self.language.merge(other.language)

    def to_json(self) -> dict[str, Any]:
        metrics: dict[str, Any] = {}
        for n, spec in enumerate(METRICS):
            nonzero = np.flatnonzero(self.hist_docs[n])
            metrics[spec.name] = {
                "bins": [
                    [int(b), int(self.hist_docs[n, b]), int(self.hist_bytes[n, b])] for b in nonzero
                ],
                "not_applicable": int(self.na[n]),
                "not_applicable_bytes": int(self.na_bytes[n]),
                "sum": int(self.sums[n]) if IS_INTEGRAL[n] else None,
                "max": _number(self.maxima[n]),
                "min": _number(self.minima[n]),
            }
        return {
            "documents": self.docs,
            "canonical_bytes": self.bytes,
            "line_bytes": self.line_bytes,
            "metrics": metrics,
            "flags": {f: [int(x) for x in self.flags[n]] for n, f in enumerate(FLAGS)},
            "classes": {c: [int(x) for x in self.classes[n]] for n, c in enumerate(CLASS_ORDER)},
            "bool_intersections": {
                name: [int(x) for x in self.bools[n]]
                for n, (name, _, _) in enumerate(BOOL_INTERSECTIONS)
            },
            "joints": {
                name: [
                    [int(i), int(j), int(self.joints[n][i, j, 0]), int(self.joints[n][i, j, 1])]
                    for i, j in zip(*np.nonzero(self.joints[n][:, :, 0]), strict=True)
                ]
                for n, (name, _, _) in enumerate(JOINT_PAIRS)
            },
            "language": self.language.to_json(),
        }

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> Population:
        pop = cls()
        pop.docs = int(data["documents"])
        pop.bytes = int(data["canonical_bytes"])
        pop.line_bytes = int(data["line_bytes"])
        if set(data["metrics"]) != set(METRIC_INDEX):
            raise AggregateError("unit metric set differs from the detector policy")
        for name, entry in data["metrics"].items():
            n = METRIC_INDEX[name]
            for b, docs, nbytes in entry["bins"]:
                if not 0 <= b < NBINS:
                    raise AggregateError("unit histogram bin out of range")
                pop.hist_docs[n, b] = docs
                pop.hist_bytes[n, b] = nbytes
            pop.na[n] = entry["not_applicable"]
            pop.na_bytes[n] = entry["not_applicable_bytes"]
            pop.sums[n] = entry["sum"] or 0
            pop.maxima[n] = np.nan if entry["max"] is None else entry["max"]
            pop.minima[n] = np.nan if entry["min"] is None else entry["min"]
        if set(data["flags"]) != set(FLAG_INDEX):
            raise AggregateError("unit flag set differs from the detector policy")
        for name, (docs, nbytes) in data["flags"].items():
            pop.flags[FLAG_INDEX[name]] = (docs, nbytes)
        for name, (docs, nbytes) in data["classes"].items():
            pop.classes[CLASS_INDEX[name]] = (docs, nbytes)
        bools = {name: n for n, (name, _, _) in enumerate(BOOL_INTERSECTIONS)}
        for name, (docs, nbytes) in data["bool_intersections"].items():
            pop.bools[bools[name]] = (docs, nbytes)
        joints = {name: n for n, (name, _, _) in enumerate(JOINT_PAIRS)}
        for name, cells in data["joints"].items():
            grid = pop.joints[joints[name]]
            for i, j, docs, nbytes in cells:
                if not (0 <= i < grid.shape[0] and 0 <= j < grid.shape[1]):
                    raise AggregateError("unit joint cell out of range")
                grid[i, j] = (docs, nbytes)
        pop.language = LanguageStats.from_json(data["language"])
        return pop


def _number(value: float) -> float | int | None:
    if np.isnan(value):
        return None
    return int(value) if float(value).is_integer() else float(value)


def _operand(
    operand: str,
    values: npt.NDArray[np.float64],
    flag_bits: npt.NDArray[np.int64],
    classes: npt.NDArray[np.int64],
) -> npt.NDArray[np.bool_]:
    if operand.startswith("class:"):
        result: npt.NDArray[np.bool_] = classes == CLASS_INDEX[operand[6:]]
        return result
    if operand.startswith("any:"):
        column = values[:, METRIC_INDEX[operand[4:]]]
        return np.nan_to_num(column, nan=0.0) > 0
    return ((flag_bits >> FLAG_INDEX[operand]) & 1).astype(bool)


def merged(populations: Sequence[Population]) -> Population:
    total = Population()
    for pop in populations:
        total.merge(pop)
    return total
