"""Mergeable, exact, content-free population statistics.

A population (all documents, or the C05-kept / C05-removed documents of a file) is a
set of integer arrays: per-metric histograms (documents and canonical UTF-8 bytes per
bin), not-applicable counts, integer sums, extrema, flag / class / intersection
counts and threshold-free joint histograms, plus bounded language-metadata counts.

Every quantity is an integer (byte sums are accumulated in float64 only while they
are far below 2**53 and converted back exactly), so merging is associative and
commutative: worker count, chunking and merge order cannot change a result.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np
import numpy.typing as npt

from xlm.data.quality.policy import (
    BOOL_INTERSECTIONS,
    CLASS_ORDER,
    FLAG_INDEX,
    FLAGS,
    JOINT_PAIRS,
    LANGUAGE_LABEL_KEYS,
    LANGUAGE_PROVENANCE_KEY,
    LANGUAGE_SCORE_KEYS,
    MAX_LABEL_CHARS,
    MAX_LABEL_VALUES,
    METRIC_INDEX,
    METRICS,
)

NBINS = 1001
N_METRICS = len(METRICS)
IS_RATIO = np.array([m.kind == "ratio" for m in METRICS])
IS_INTEGRAL = np.array([m.kind == "count" for m in METRICS])
CLASS_INDEX = {c: n for n, c in enumerate(CLASS_ORDER)}
EXACT_FLOAT_LIMIT = 2**52
OTHER_LABEL = "<other>"


class AggregateError(ValueError):
    """Content-free failure while building or merging statistics."""


def bin_matrix(values: npt.NDArray[np.float64]) -> npt.NDArray[np.int64]:
    """Vectorized :func:`policy.metric_bin`; NaN (not applicable) becomes -1."""
    out = np.full(values.shape, -1, dtype=np.int64)
    present = ~np.isnan(values)
    ratio = values[:, IS_RATIO]
    ratio_bins = np.clip(np.floor(ratio * 1000), 0, 1000)
    sub = out[:, IS_RATIO]
    mask = ~np.isnan(ratio)
    sub[mask] = ratio_bins[mask].astype(np.int64)
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


def _empty_language() -> dict[str, Any]:
    return {
        "language": {},
        "confidence_bins": {},
        "confidence_absent": 0,
        "scores": {k: {"present": 0, "out_of_range": 0, "bins": {}} for k in LANGUAGE_SCORE_KEYS},
        "labels": {k: {"present": 0, "values": {}} for k in LANGUAGE_LABEL_KEYS},
        "provenance": {},
        "other_language_keys": {},
        "document_kind": {},
        "split": {},
    }


def _bump(table: dict[str, Any], key: str, amount: int = 1) -> None:
    key = key[:MAX_LABEL_CHARS]
    if key not in table and len(table) >= MAX_LABEL_VALUES:
        key = OTHER_LABEL
    table[key] = table.get(key, 0) + amount


def _ratio_bin(value: float) -> str:
    return str(min(max(int(value * 1000), 0), 1000))


class Population:
    """Statistics of one document population (see module docstring)."""

    def __init__(self) -> None:
        self.docs = 0
        self.bytes = 0
        self.line_bytes = 0
        self.hist_docs = np.zeros((N_METRICS, NBINS), dtype=np.int64)
        self.hist_bytes = np.zeros((N_METRICS, NBINS), dtype=np.int64)
        self.na = np.zeros(N_METRICS, dtype=np.int64)
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
        self.language = _empty_language()

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
        lang = self.language
        value = row.get("language")
        bucket = lang["language"].setdefault(
            str(value)[:MAX_LABEL_CHARS]
            if len(lang["language"]) < MAX_LABEL_VALUES
            or str(value)[:MAX_LABEL_CHARS] in lang["language"]
            else OTHER_LABEL,
            [0, 0],
        )
        bucket[0] += 1
        bucket[1] += nbytes
        confidence = row.get("language_confidence")
        if isinstance(confidence, int | float) and not isinstance(confidence, bool):
            key = _ratio_bin(float(confidence))
            lang["confidence_bins"][key] = lang["confidence_bins"].get(key, 0) + 1
        else:
            lang["confidence_absent"] += 1
        _bump(lang["document_kind"], str(row.get("document_kind")))
        _bump(lang["split"], str(row.get("split")))
        metadata = row.get("source_metadata")
        if not isinstance(metadata, Mapping):
            return
        for key in LANGUAGE_SCORE_KEYS:
            if key in metadata:
                entry = lang["scores"][key]
                entry["present"] += 1
                score = metadata[key]
                if (
                    isinstance(score, int | float)
                    and not isinstance(score, bool)
                    and 0.0 <= float(score) <= 1.0
                ):
                    b = _ratio_bin(float(score))
                    entry["bins"][b] = entry["bins"].get(b, 0) + 1
                else:
                    entry["out_of_range"] += 1
        for key in LANGUAGE_LABEL_KEYS:
            if key in metadata:
                entry = lang["labels"][key]
                entry["present"] += 1
                _bump(entry["values"], str(metadata[key]))
        if LANGUAGE_PROVENANCE_KEY in metadata:
            _bump(lang["provenance"], str(metadata[LANGUAGE_PROVENANCE_KEY]))
        for key in metadata:
            low = str(key).lower()
            if ("lang" in low or "lid" in low) and key not in KNOWN_LANGUAGE_KEYS:
                _bump(lang["other_language_keys"], str(key))

    # -- merging / serialization ---------------------------------------------------------

    def merge(self, other: Population) -> None:
        self.docs += other.docs
        self.bytes += other.bytes
        self.line_bytes += other.line_bytes
        self.hist_docs += other.hist_docs
        self.hist_bytes += other.hist_bytes
        self.na += other.na
        self.sums += other.sums
        self.maxima = np.fmax(self.maxima, other.maxima)
        self.minima = np.fmin(self.minima, other.minima)
        self.flags += other.flags
        self.classes += other.classes
        self.bools += other.bools
        for mine, theirs in zip(self.joints, other.joints, strict=True):
            mine += theirs
        _merge_language(self.language, other.language)

    def to_json(self) -> dict[str, Any]:
        metrics: dict[str, Any] = {}
        for n, spec in enumerate(METRICS):
            nonzero = np.flatnonzero(self.hist_docs[n])
            metrics[spec.name] = {
                "bins": [
                    [int(b), int(self.hist_docs[n, b]), int(self.hist_bytes[n, b])] for b in nonzero
                ],
                "not_applicable": int(self.na[n]),
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
            "language": self.language,
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
                pop.hist_docs[n, b] = docs
                pop.hist_bytes[n, b] = nbytes
            pop.na[n] = entry["not_applicable"]
            pop.sums[n] = entry["sum"] or 0
            pop.maxima[n] = np.nan if entry["max"] is None else entry["max"]
            pop.minima[n] = np.nan if entry["min"] is None else entry["min"]
        for name, (docs, nbytes) in data["flags"].items():
            pop.flags[FLAG_INDEX[name]] = (docs, nbytes)
        for name, (docs, nbytes) in data["classes"].items():
            pop.classes[CLASS_INDEX[name]] = (docs, nbytes)
        bools = {name: n for n, (name, _, _) in enumerate(BOOL_INTERSECTIONS)}
        for name, (docs, nbytes) in data["bool_intersections"].items():
            pop.bools[bools[name]] = (docs, nbytes)
        joints = {name: n for n, (name, _, _) in enumerate(JOINT_PAIRS)}
        for name, cells in data["joints"].items():
            for i, j, docs, nbytes in cells:
                pop.joints[joints[name]][i, j] = (docs, nbytes)
        pop.language = _copy_language(data["language"])
        return pop


KNOWN_LANGUAGE_KEYS = frozenset(
    (*LANGUAGE_SCORE_KEYS, *LANGUAGE_LABEL_KEYS, LANGUAGE_PROVENANCE_KEY)
)


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


def _merge_counts(mine: dict[str, Any], theirs: Mapping[str, Any]) -> None:
    for key, amount in theirs.items():
        if key not in mine and len(mine) >= MAX_LABEL_VALUES:
            key = OTHER_LABEL
        if isinstance(amount, list):
            entry = mine.setdefault(key, [0, 0])
            entry[0] += amount[0]
            entry[1] += amount[1]
        else:
            mine[key] = mine.get(key, 0) + amount


def _merge_language(mine: dict[str, Any], theirs: Mapping[str, Any]) -> None:
    for name in ("language", "confidence_bins", "provenance", "other_language_keys"):
        _merge_counts(mine[name], theirs[name])
    for name in ("document_kind", "split"):
        _merge_counts(mine[name], theirs[name])
    mine["confidence_absent"] += theirs["confidence_absent"]
    for key, entry in theirs["scores"].items():
        target = mine["scores"][key]
        target["present"] += entry["present"]
        target["out_of_range"] += entry["out_of_range"]
        _merge_counts(target["bins"], entry["bins"])
    for key, entry in theirs["labels"].items():
        target = mine["labels"][key]
        target["present"] += entry["present"]
        _merge_counts(target["values"], entry["values"])


def _copy_language(data: Mapping[str, Any]) -> dict[str, Any]:
    fresh = _empty_language()
    _merge_language(fresh, data)
    return fresh


def merged(populations: Sequence[Population]) -> Population:
    total = Population()
    for pop in populations:
        total.merge(pop)
    return total
