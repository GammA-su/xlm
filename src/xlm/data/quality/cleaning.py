"""Phase-B dry-run kernel: ONE detector pass plus policy evaluation per document.

Workers parse and verify canonical rows exactly as the Phase-A scan does, run the
same :func:`~xlm.data.quality.detectors.analyze` once, evaluate the frozen cleaning
policy on its numbers (:func:`evaluate`) and return content-free, exactly mergeable
integer statistics (:class:`CleanStats`) plus bottom-k review candidates (locators,
identity digests and numbers only; never text). Nothing here writes anything.

Decision semantics (policy v1; all thresholds frozen):

- hard corruption DROP: ``markup_full_html`` flag, ``nul >= 1``, ``noncharacters >= 1``;
- severe repetition: count the six frozen conservative signals; default class group
  (prose_like / other / markup_like / empty) DROPs at >= 2; structured group
  (math_table_like / code_like) DROPs at >= 3 and is REVIEW at exactly 2;
- OCR (``finepdfs_en`` only): >= 2 OCR signals DROP with >= 1 severe signal, else REVIEW;
- encoding: ``replacement_chars >= 8`` or ``mojibake_hits >= 16`` is REVIEW; both, or
  either together with any C0/C1 control, is DROP.

Outcome precedence: DROP if any DROP rule fires, otherwise REVIEW if any REVIEW rule
fires, otherwise KEEP. Every fired rule is recorded, so marginal impacts, exclusive
impacts, pairwise overlaps and exact rule combinations are all derivable.
"""

from __future__ import annotations

import hashlib
import heapq
import os
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import numpy as np
import numpy.typing as npt

from xlm.data.quality.aggregate import CLASS_INDEX, AggregateError
from xlm.data.quality.cleaning_policy import (
    DROP,
    KEEP,
    OCR_SIGNALS,
    OUTCOMES,
    REP_SIGNALS,
    REVIEW,
    RULESET_V1,
    ComponentRules,
    RuleParams,
    RuleSet,
    Stratum,
)
from xlm.data.quality.detectors import analyze
from xlm.data.quality.overlay import IDENTITY
from xlm.data.quality.policy import CLASS_ORDER, FLAG_INDEX, METRIC_INDEX
from xlm.data.quality.review import doc_rank
from xlm.data.quality.scan import ChunkTask, QualityError, _verify_kept, parse_row

N_CLASSES = len(CLASS_ORDER)
N_REP = len(REP_SIGNALS)
N_OCR = len(OCR_SIGNALS)
GROUPS = ("default", "structured")
FULL_HTML = FLAG_INDEX["markup_full_html"]
CODE_EXAMPLE = FLAG_INDEX["markup_code_example"]
M = METRIC_INDEX
# Numbers recorded in a review row (all content-free measurements).
ROW_METRICS = (
    "utf8_bytes",
    "chars",
    *REP_SIGNALS,
    *OCR_SIGNALS,
    "replacement_chars",
    "mojibake_hits",
    "c0_controls",
    "c1_controls",
    "nul",
    "noncharacters",
)
COMPRESSION_BIT = 1 << REP_SIGNALS.index("compression_ratio")
RUN_BITS = (1 << REP_SIGNALS.index("max_char_run")) | (
    1 << REP_SIGNALS.index("repeated_char_ratio")
)
ENCODING_PRIORITY = ("essential_science", "essential_prose", "ultrax_ultrafineweb")
SAMPLING_SEED_SEPARATOR = "\0"


# -- per-document decision ------------------------------------------------------------------


@dataclass(frozen=True)
class Decision:
    outcome: int
    rules: int  # bit n = params.ruleset.ids[n] fired
    severe: int  # bit k = REP_SIGNALS[k] fired
    severe_count: int
    severe_na: int  # bit k = REP_SIGNALS[k] not applicable (value None)
    ocr: int  # bit k = OCR_SIGNALS[k] fired (OCR-scoped components only)
    ocr_count: int
    ocr_scope: bool
    structured: bool


def _fires(threshold: Any, value: float | int | None) -> bool:
    if value is None:
        return False
    return bool(value >= threshold.cut) if threshold.at_least else bool(value < threshold.cut)


def evaluate(
    values: Sequence[float | int | None],
    flags: Sequence[int] | frozenset[int],
    doc_class: str,
    rules: ComponentRules,
    params: RuleParams,
) -> Decision:
    """The frozen v1 decision for one document's Phase-A measurements."""
    structured = doc_class in params.structured_classes
    severe = severe_na = 0
    for k, threshold in enumerate(rules.repetition):
        if threshold is None:
            continue
        value = values[threshold.index]
        if value is None:
            severe_na |= 1 << k
        elif _fires(threshold, value):
            severe |= 1 << k
    severe_count = severe.bit_count()
    ocr = 0
    ocr_scope = rules.ocr is not None
    if rules.ocr is not None:
        for k, threshold in enumerate(rules.ocr):
            if threshold is not None and _fires(threshold, values[threshold.index]):
                ocr |= 1 << k
    ocr_count = ocr.bit_count()

    # Rule bits of THIS policy version. The v1 parameters reproduce v1 exactly; v2 has no
    # full-HTML rule (``full_html_drop`` false), no structured or OCR REVIEW, and DROP
    # encoding rules.
    bit = params.ruleset.bit
    fired = 0
    if params.full_html_drop and FULL_HTML in flags:
        fired |= bit["hard.full_html"]
    if int(values[M["nul"]] or 0) >= params.nul_at_least:
        fired |= bit["hard.nul"]
    if int(values[M["noncharacters"]] or 0) >= params.noncharacters_at_least:
        fired |= bit["hard.noncharacters"]
    if structured:
        if severe_count >= params.structured_drop_at_least:
            fired |= bit["rep.severe_structured"]
        elif (
            params.structured_review_equals is not None
            and severe_count == params.structured_review_equals
        ):
            fired |= bit["rep.structured_two_signals"]
    elif severe_count >= params.default_drop_at_least:
        fired |= bit["rep.severe_default"]
    if ocr_scope and ocr_count >= params.ocr_at_least:
        if severe_count >= params.ocr_drop_severe_at_least:
            fired |= bit["ocr.finepdfs_with_repetition"]
        elif params.ocr_review:
            fired |= bit["ocr.finepdfs_review"]
    replacement = int(values[M["replacement_chars"]] or 0) >= params.replacement_at_least
    mojibake = int(values[M["mojibake_hits"]] or 0) >= params.mojibake_at_least
    if replacement:
        fired |= bit[params.replacement_rule]
    if mojibake:
        fired |= bit[params.mojibake_rule]
    if replacement and mojibake:
        fired |= bit["enc.replacement_and_mojibake"]
    if (replacement or mojibake) and any(
        int(values[M[name]] or 0) >= 1 for name in params.forbidden_controls
    ):
        fired |= bit["enc.with_forbidden_controls"]

    if fired & params.ruleset.drop_mask:
        outcome = DROP
    elif fired & params.ruleset.review_mask:
        outcome = REVIEW
    else:
        outcome = KEEP
    return Decision(
        outcome, fired, severe, severe_count, severe_na, ocr, ocr_count, ocr_scope, structured
    )


def rule_names(mask: int, ruleset: RuleSet = RULESET_V1) -> list[str]:
    return [name for n, name in enumerate(ruleset.ids) if mask >> n & 1]


def signal_names(mask: int, names: Sequence[str]) -> list[str]:
    return [name for n, name in enumerate(names) if mask >> n & 1]


# -- review strata --------------------------------------------------------------------------


def expand_strata(params: RuleParams, components: Sequence[str]) -> list[Stratum]:
    """The ordered strata with ``coverage.<component>.<outcome>`` expanded over the
    version's outcomes."""
    out: list[Stratum] = []
    for stratum in params.strata:
        if stratum.name == "coverage.<component>.<outcome>":
            out += [
                Stratum(f"coverage.{c}.{o}", stratum.quota)
                for c in sorted(components)
                for o in params.outcomes
            ]
        else:
            out.append(stratum)
    return out


def quota_of(params: RuleParams) -> dict[str, int]:
    """Quota by stratum name (``coverage.*`` share one quota)."""
    quotas = {s.name: s.quota for s in params.strata}
    return quotas


def strata_of(
    decision: Decision,
    component: str,
    flags: Sequence[int] | frozenset[int],
    values: Sequence[float | int | None],
    params: RuleParams,
) -> list[str]:
    """Every review stratum of the policy version this document belongs to.

    Candidate memberships are computed for every known definition and kept only when
    the version lists that stratum (``coverage.*`` always applies).
    """
    ruleset = params.ruleset
    out = [_coverage_name(component, decision.outcome)]
    fired = decision.rules
    rep = bool(fired & ruleset.rep_mask)
    if component == "finewiki_en":
        if rep:
            out.append("priority.finewiki_en.severe_repetition")
        if decision.severe & COMPRESSION_BIT:
            out.append("priority.finewiki_en.compression")
    if fired & ruleset.bit["ocr.finepdfs_with_repetition"]:
        out.append("priority.finepdfs_en.ocr_repetition")
    if component == "common_pile_prose" and rep and decision.severe & RUN_BITS:
        out.append("priority.common_pile_prose.char_runs")
    if component == "ultrax_ultrafineweb" and rep:
        out.append("priority.ultrax_ultrafineweb.severe_tail")
    if fired & ruleset.enc_mask and component in ENCODING_PRIORITY:
        out.append(ENCODING_STRATA[component])
    for n, (_, action) in enumerate(ruleset.rules):
        if fired >> n & 1 and (action == DROP or decision.outcome == REVIEW):
            out.append(ruleset.strata[n])
    keep = decision.outcome == KEEP
    if keep and decision.structured and decision.severe_count == 1:
        out.append("protected.structured_one_signal")
    if CODE_EXAMPLE in flags and FULL_HTML not in flags:
        out.append("protected.code_example_markup")
    if keep and FULL_HTML in flags:
        out.append("protected.full_html_keep")
    if keep and not decision.structured and decision.severe_count == 1:
        out.append("control.default_one_signal")
    if keep and decision.severe_count == 2:
        out.append(
            "control.structured_two_signals"
            if decision.structured
            else "control.default_two_signals"
        )
    if keep:
        replacement = int(values[M["replacement_chars"]] or 0)
        mojibake = int(values[M["mojibake_hits"]] or 0)
        half_r, half_m = params.replacement_at_least // 2, params.mojibake_at_least // 2
        if (
            half_r <= replacement < params.replacement_at_least
            or half_m <= mojibake < params.mojibake_at_least
        ):
            out.append("control.encoding_near")
        if decision.ocr_scope and decision.ocr_count == 1:
            out.append("control.finepdfs_ocr_one")
        if (
            decision.ocr_scope
            and decision.ocr_count >= params.ocr_at_least
            and decision.severe_count == 0
        ):
            out.append("control.finepdfs_ocr_only")
    allowed = params.stratum_names
    return [name for name in out if name in allowed or name.startswith("coverage.")]


ENCODING_STRATA = {c: f"priority.encoding.{c}" for c in ENCODING_PRIORITY}


@lru_cache(maxsize=1024)
def _coverage_name(component: str, outcome: int) -> str:
    return f"coverage.{component}.{OUTCOMES[outcome]}"


def sampling_key(params: RuleParams, manifest_digest: str) -> bytes:
    """32-byte BLAKE2b key binding review ranks to the policy seed and the manifest."""
    return hashlib.sha256(
        f"{params.seed}{SAMPLING_SEED_SEPARATOR}{manifest_digest}".encode()
    ).digest()


_SALT: dict[tuple[bytes, str], int] = {}


def stratum_salt(key: bytes, name: str) -> int:
    cached = _SALT.get((key, name))
    if cached is None:
        cached = int.from_bytes(
            hashlib.blake2b(name.encode("utf-8"), digest_size=8, key=key).digest(), "big"
        )
        if len(_SALT) < 4096:
            _SALT[(key, name)] = cached
    return cached


def _entry_order(entry: Mapping[str, Any]) -> tuple[int, str, int]:
    return (int(entry["rank"]), str(entry["path"]), int(entry["row"]))


def merge_candidates(
    target: dict[str, list[Any]], incoming: Mapping[str, list[Any]], quotas: Mapping[str, int]
) -> None:
    """Bottom-quota per stratum (associative and order independent)."""
    for name, entries in incoming.items():
        combined = target.get(name, []) + list(entries)
        combined.sort(key=_entry_order)
        target[name] = combined[: _quota(quotas, name)]


def _quota(quotas: Mapping[str, int], name: str) -> int:
    if name.startswith("coverage."):
        return quotas["coverage.<component>.<outcome>"]
    return quotas[name]


# -- mergeable statistics -------------------------------------------------------------------


def _shapes(n_rules: int) -> dict[str, tuple[int, ...]]:
    return {
        "outcome": (3, 2),
        "class_outcome": (N_CLASSES, 3, 2),
        "rule": (n_rules, 2),
        "rule_exclusive": (n_rules, 2),
        "rule_pair": (n_rules, n_rules, 2),
        "rule_class": (n_rules, N_CLASSES, 2),
        "severe": (2, N_REP + 1, 3, 2),
        "signal": (2, N_REP, 2),
        "signal_na": (N_REP, 2),
        "ocr": (N_OCR + 1, 3, 2),
        "ocr_signal": (N_OCR, 2),
    }


class CleanStats:
    """Exact integer counters (documents, canonical UTF-8 bytes) of one population."""

    def __init__(self, ruleset: RuleSet = RULESET_V1) -> None:
        self.ruleset = ruleset
        self.shapes = _shapes(len(ruleset.ids))
        self.docs = 0
        self.bytes = 0
        self.line_bytes = 0
        self.max_text_bytes = 0
        self.arrays = {name: np.zeros(shape, dtype=np.int64) for name, shape in self.shapes.items()}
        self.combos: dict[int, list[int]] = {}  # rule mask -> [docs, bytes]
        self.strata: dict[str, list[int]] = {}  # review stratum -> [docs, bytes]

    def add_batch(
        self,
        *,
        sizes: npt.NDArray[np.int64],
        line_bytes: int,
        outcome: npt.NDArray[np.int64],
        classes: npt.NDArray[np.int64],
        rules: npt.NDArray[np.int64],
        severe: npt.NDArray[np.int64],
        severe_count: npt.NDArray[np.int64],
        severe_na: npt.NDArray[np.int64],
        structured: npt.NDArray[np.int64],
        ocr: npt.NDArray[np.int64],
        ocr_count: npt.NDArray[np.int64],
        ocr_scope: npt.NDArray[np.bool_],
        strata: Sequence[Sequence[str]],
    ) -> None:
        n = int(sizes.shape[0])
        if n == 0:
            return
        a = self.arrays
        self.docs += n
        self.bytes += int(sizes.sum())
        self.line_bytes += line_bytes
        self.max_text_bytes = max(self.max_text_bytes, int(sizes.max()))
        weights = np.stack([np.ones(n, dtype=np.int64), sizes], axis=1)  # (n, 2)

        def add(target: npt.NDArray[np.int64], index: tuple[Any, ...]) -> None:
            np.add.at(target, (*index, 0), weights[:, 0])
            np.add.at(target, (*index, 1), weights[:, 1])

        add(a["outcome"], (outcome,))
        add(a["class_outcome"], (classes, outcome))
        add(a["severe"], (structured, severe_count, outcome))
        n_rules = len(self.ruleset.ids)
        bits = ((rules[:, None] >> np.arange(n_rules)) & 1).astype(np.int64)  # (n, R)
        a["rule"] += bits.T @ weights
        a["rule_pair"] += _pairs(bits, bits, weights)
        one_hot = np.zeros((n, N_CLASSES), dtype=np.int64)
        one_hot[np.arange(n), classes] = 1
        a["rule_class"] += _pairs(bits, one_hot, weights)
        drop_mask, review_mask = self.ruleset.drop_mask, self.ruleset.review_mask
        drop_bits = bits * np.array([(1 << r) & drop_mask != 0 for r in range(n_rules)])
        review_bits = bits * np.array([(1 << r) & review_mask != 0 for r in range(n_rules)])
        only_drop = drop_bits * (drop_bits.sum(axis=1) == 1)[:, None]
        only_review = review_bits * ((review_bits.sum(axis=1) == 1) & (outcome == REVIEW))[:, None]
        a["rule_exclusive"] += (only_drop + only_review).T @ weights
        sig = ((severe[:, None] >> np.arange(N_REP)) & 1).astype(np.int64)
        for group in (0, 1):
            members = structured == group
            a["signal"][group] += sig[members].T @ weights[members]
        na = ((severe_na[:, None] >> np.arange(N_REP)) & 1).astype(np.int64)
        a["signal_na"] += na.T @ weights
        if ocr_scope.any():
            scoped = np.flatnonzero(ocr_scope)
            add(a["ocr"], (ocr_count[scoped], outcome[scoped]))
            ocr_bits = ((ocr[scoped, None] >> np.arange(N_OCR)) & 1).astype(np.int64)
            a["ocr_signal"] += ocr_bits.T @ weights[scoped]
        masks, inverse = np.unique(rules, return_inverse=True)
        docs = np.bincount(inverse, minlength=masks.shape[0])
        nbytes = np.zeros(masks.shape[0], dtype=np.int64)
        np.add.at(nbytes, inverse, sizes)
        for mask, d, b in zip(masks.tolist(), docs.tolist(), nbytes.tolist(), strict=True):
            entry = self.combos.setdefault(int(mask), [0, 0])
            entry[0] += int(d)
            entry[1] += int(b)
        for i, names in enumerate(strata):
            size = int(sizes[i])
            for name in names:
                entry = self.strata.setdefault(name, [0, 0])
                entry[0] += 1
                entry[1] += size

    def merge(self, other: CleanStats) -> None:
        self.docs += other.docs
        self.bytes += other.bytes
        self.line_bytes += other.line_bytes
        self.max_text_bytes = max(self.max_text_bytes, other.max_text_bytes)
        if other.ruleset != self.ruleset:
            raise AggregateError("cleaning statistics of different policy versions")
        for name in self.shapes:
            self.arrays[name] += other.arrays[name]
        for mask, (d, b) in other.combos.items():
            entry = self.combos.setdefault(mask, [0, 0])
            entry[0] += d
            entry[1] += b
        for name, (d, b) in other.strata.items():
            entry = self.strata.setdefault(name, [0, 0])
            entry[0] += d
            entry[1] += b

    def to_json(self) -> dict[str, Any]:
        return {
            "docs": self.docs,
            "bytes": self.bytes,
            "line_bytes": self.line_bytes,
            "max_text_bytes": self.max_text_bytes,
            "arrays": {name: self.arrays[name].tolist() for name in sorted(self.shapes)},
            "combos": {str(k): list(v) for k, v in sorted(self.combos.items())},
            "strata": {k: list(v) for k, v in sorted(self.strata.items())},
        }

    @classmethod
    def from_json(cls, data: Mapping[str, Any], ruleset: RuleSet = RULESET_V1) -> CleanStats:
        stats = cls(ruleset)
        if not isinstance(data, Mapping) or set(data) != {
            "docs",
            "bytes",
            "line_bytes",
            "max_text_bytes",
            "arrays",
            "combos",
            "strata",
        }:
            raise AggregateError("cleaning statistics schema")
        for name in ("docs", "bytes", "line_bytes", "max_text_bytes"):
            value = data[name]
            if type(value) is not int or value < 0:
                raise AggregateError("cleaning statistics schema")
            setattr(stats, name, value)
        arrays = data["arrays"]
        if not isinstance(arrays, Mapping) or set(arrays) != set(stats.shapes):
            raise AggregateError("cleaning statistics schema")
        for name, shape in stats.shapes.items():
            array = np.asarray(arrays[name])
            if array.shape != shape or array.dtype.kind != "i" or (array < 0).any():
                raise AggregateError("cleaning statistics schema")
            stats.arrays[name] = array.astype(np.int64)
        for field, table, limit in (
            ("combos", stats.combos, 1 << len(ruleset.ids)),
            ("strata", None, 0),
        ):
            raw = data[field]
            if not isinstance(raw, Mapping):
                raise AggregateError("cleaning statistics schema")
            for key, value in raw.items():
                if (
                    not isinstance(value, list)
                    or len(value) != 2
                    or any(type(x) is not int or x < 0 for x in value)
                ):
                    raise AggregateError("cleaning statistics schema")
                if table is not None:
                    if not key.isdigit() or not 0 <= int(key) < limit:
                        raise AggregateError("cleaning statistics schema")
                    table[int(key)] = list(value)
                else:
                    if type(key) is not str or len(key) > 256:
                        raise AggregateError("cleaning statistics schema")
                    stats.strata[key] = list(value)
        stats.check()
        return stats

    def check(self) -> None:
        """Internal exactness: every table partitions the same documents and bytes."""
        a = self.arrays
        totals = [self.docs, self.bytes]
        if a["outcome"].sum(axis=0).tolist() != totals:
            raise AggregateError("cleaning statistics are inconsistent (outcomes)")
        if a["class_outcome"].sum(axis=(0, 1)).tolist() != totals:
            raise AggregateError("cleaning statistics are inconsistent (classes)")
        if a["severe"].sum(axis=(0, 1, 2)).tolist() != totals:
            raise AggregateError("cleaning statistics are inconsistent (signal counts)")
        combo = [sum(v[0] for v in self.combos.values()), sum(v[1] for v in self.combos.values())]
        if combo != totals:
            raise AggregateError("cleaning statistics are inconsistent (rule combinations)")
        for outcome, mask in ((DROP, self.ruleset.drop_mask),):
            union = [sum(v[i] for k, v in self.combos.items() if k & mask) for i in (0, 1)]
            if union != a["outcome"][outcome].tolist():
                raise AggregateError("cleaning statistics are inconsistent (DROP union)")


def _pairs(
    left: npt.NDArray[np.int64], right: npt.NDArray[np.int64], weights: npt.NDArray[np.int64]
) -> npt.NDArray[np.int64]:
    """``out[r, s, w] = sum_n left[n, r] * right[n, s] * weights[n, w]`` (exact integers)."""
    docs = left.T @ right
    nbytes = (left * weights[:, 1:2]).T @ right
    return np.stack([docs, nbytes], axis=2)


# -- chunk kernel ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CleanTask:
    chunk: ChunkTask
    component: str
    rules: ComponentRules
    params: RuleParams
    sampling_key: bytes
    # Production cleaning: no review sampling; every DROP row's chunk-local byte span
    # and content-free identity are returned instead (``CleanChunkResult.drops``).
    record_drops: bool = False


# (start, stop, row, file offset, doc_id SHA-256, row SHA-256, rule mask, canonical bytes)
DropSpan = tuple[int, int, int, int, str, str, int, int]


@dataclass
class CleanChunkResult:
    ordinal: int
    last: bool
    rows: int
    canonical_bytes: int
    populations: dict[str, CleanStats]
    review: dict[str, list[Any]]
    max_line_bytes: int = 0
    drops: list[DropSpan] | None = None
    # Operational only (never in a unit, artifact or receipt).
    nbytes: int = 0
    pid: int = 0
    started: float = 0.0
    finished: float = 0.0
    cpu_seconds: float = 0.0


def process_clean_chunk(task: CleanTask) -> CleanChunkResult:
    started, cpu = time.monotonic(), time.process_time()
    result = measure_clean_chunk(task)
    result.nbytes = len(task.chunk.data)
    result.pid = os.getpid()
    result.cpu_seconds = time.process_time() - cpu
    result.started, result.finished = started, time.monotonic()
    return result


def _plain(value: float | int | None) -> float | int | None:
    if value is None:
        return None
    number = float(value)
    return int(number) if number.is_integer() else number


def measure_clean_chunk(task: CleanTask) -> CleanChunkResult:
    """Parse, verify, measure (one detector pass) and decide every row of one chunk."""
    chunk = task.chunk
    params = task.params
    data = chunk.data
    kept = None if chunk.kept is None else np.frombuffer(chunk.kept, dtype=np.bool_)
    identity = None if chunk.identity is None else np.frombuffer(chunk.identity, dtype=IDENTITY)
    columns: dict[str, list[Any]] = {
        name: []
        for name in (
            "sizes",
            "lines",
            "outcome",
            "classes",
            "rules",
            "severe",
            "severe_count",
            "severe_na",
            "structured",
            "ocr",
            "ocr_count",
            "ocr_scope",
            "names",
            "strata",
        )
    }
    sampler = _Sampler(task)
    drops: list[DropSpan] | None = [] if task.record_drops else None
    count = 0
    pos, row, end_of_data = 0, chunk.first_row, len(data)
    while pos < end_of_data:
        newline = data.find(b"\n", pos)
        stop = end_of_data if newline < 0 else newline + 1
        if stop - pos > chunk.line_ceiling:
            raise QualityError("canonical row exceeds the document ceiling")
        body = data[pos:newline] if newline >= 0 else data[pos:stop]
        row_digest = hashlib.sha256(body).digest()
        document = parse_row(body)
        text, declared, doc_id = document["text"], document["utf8_byte_count"], document["doc_id"]
        try:
            nbytes = len(text.encode("utf-8"))
        except UnicodeEncodeError:
            raise QualityError("canonical text is not valid Unicode scalar values") from None
        if nbytes != declared:
            raise QualityError("canonical utf8_byte_count disagrees with its text")
        index = row - chunk.first_row
        if kept is None:
            membership: bool | None = None
            name = "all"
        else:
            if index >= kept.shape[0]:
                raise QualityError("C05 overlay row count disagrees with the chunk")
            membership = bool(kept[index])
            name = "c05_kept" if membership else "c05_removed"
            if membership:
                assert identity is not None
                _verify_kept(identity[index], doc_id, nbytes, row_digest, document)
        result = analyze(text, nbytes)  # the ONE detector pass
        flags = frozenset(result.flags)
        decision = evaluate(result.values, flags, result.doc_class, task.rules, params)
        strata = strata_of(decision, task.component, flags, result.values, params)
        columns["sizes"].append(nbytes)
        columns["lines"].append(stop - pos)
        columns["outcome"].append(decision.outcome)
        columns["classes"].append(CLASS_INDEX[result.doc_class])
        columns["rules"].append(decision.rules)
        columns["severe"].append(decision.severe)
        columns["severe_count"].append(decision.severe_count)
        columns["severe_na"].append(decision.severe_na)
        columns["structured"].append(int(decision.structured))
        columns["ocr"].append(decision.ocr)
        columns["ocr_count"].append(decision.ocr_count)
        columns["ocr_scope"].append(decision.ocr_scope)
        columns["names"].append(name)
        columns["strata"].append(strata)
        if drops is not None:
            if decision.outcome == DROP:
                drops.append(
                    (
                        pos,
                        stop,
                        row,
                        chunk.base_offset + pos,
                        hashlib.sha256(doc_id.encode("utf-8")).hexdigest(),
                        row_digest.hex(),
                        decision.rules,
                        nbytes,
                    )
                )
            count += 1
            row += 1
            pos = stop
            continue
        sampler.offer(
            count,
            row,
            strata,
            _recorder(
                row,
                chunk.base_offset + pos,
                doc_id,
                row_digest,
                membership,
                result.doc_class,
                decision,
                result.values,
                strata,
                params.ruleset,
            ),
        )
        count += 1
        row += 1
        pos = stop
    if kept is not None and kept.shape[0] != count:
        raise QualityError("C05 overlay row count disagrees with the chunk")
    arrays = {
        k: np.array(v, dtype=np.int64)
        for k, v in columns.items()
        if k not in ("names", "strata", "ocr_scope")
    }
    scope = np.array(columns["ocr_scope"], dtype=np.bool_)
    name_array = np.array(columns["names"])
    populations: dict[str, CleanStats] = {}
    for pop_name in sorted(set(columns["names"])):
        members = np.flatnonzero(name_array == pop_name)
        stats = populations[pop_name] = CleanStats(params.ruleset)
        stats.add_batch(
            sizes=arrays["sizes"][members],
            line_bytes=int(arrays["lines"][members].sum()),
            outcome=arrays["outcome"][members],
            classes=arrays["classes"][members],
            rules=arrays["rules"][members],
            severe=arrays["severe"][members],
            severe_count=arrays["severe_count"][members],
            severe_na=arrays["severe_na"][members],
            structured=arrays["structured"][members],
            ocr=arrays["ocr"][members],
            ocr_count=arrays["ocr_count"][members],
            ocr_scope=scope[members],
            strata=[columns["strata"][i] for i in members.tolist()],
        )
    review = sampler.candidates()
    return CleanChunkResult(
        ordinal=chunk.ordinal,
        last=chunk.last,
        rows=count,
        canonical_bytes=int(arrays["sizes"].sum()) if count else 0,
        populations=populations,
        review=review,
        max_line_bytes=int(arrays["lines"].max()) if count else 0,
        drops=drops,
    )


def _recorder(
    row: int,
    offset: int,
    doc_id: str,
    row_digest: bytes,
    kept: bool | None,
    doc_class: str,
    decision: Decision,
    values: Sequence[float | int | None],
    strata: Sequence[str],
    ruleset: RuleSet,
) -> Callable[[], dict[str, Any]]:
    """A deferred review record (built only if the document enters some stratum)."""

    def make() -> dict[str, Any]:
        return {
            "row": row,
            "offset": offset,
            "doc_id_sha256": hashlib.sha256(doc_id.encode("utf-8")).hexdigest(),
            "row_sha256": row_digest.hex(),
            "doc_class": doc_class,
            "outcome": OUTCOMES[decision.outcome],
            "rules": rule_names(decision.rules, ruleset),
            "severe_repetition_signals": signal_names(decision.severe, REP_SIGNALS),
            "severe_repetition_signal_count": decision.severe_count,
            "ocr_signals": signal_names(decision.ocr, OCR_SIGNALS),
            "ocr_signal_count": decision.ocr_count if decision.ocr_scope else None,
            "strata": sorted(strata),
            "kept": kept,
            "values": {m: _plain(values[M[m]]) for m in ROW_METRICS},
        }

    return make


class _Sampler:
    """Streaming bottom-quota per stratum within one chunk.

    Each stratum keeps a bounded max-heap of ``(rank, index)``; a document's record is
    built only when it enters a heap and dropped once no heap holds it, so memory is
    bounded by the quotas, never by the number of rows in the chunk. Within a chunk
    the path is fixed and rows increase with ``index``, so ``(rank, index)`` orders
    exactly as the global ``(rank, path, row)``.
    """

    def __init__(self, task: CleanTask) -> None:
        self.key = task.sampling_key
        self.path = task.chunk.path
        self.component = task.component
        self.quotas = quota_of(task.params)
        self.heaps: dict[str, list[tuple[int, int]]] = {}
        self.held: dict[int, int] = {}
        self.records: dict[int, dict[str, Any]] = {}

    def _release(self, index: int) -> None:
        self.held[index] -= 1
        if not self.held[index]:
            del self.held[index]
            del self.records[index]

    def offer(
        self,
        index: int,
        row: int,
        strata: Sequence[str],
        make: Callable[[], dict[str, Any]],
    ) -> None:
        base = doc_rank(self.key, self.path, row)
        taken = 0
        for name in strata:
            item = (-(base ^ stratum_salt(self.key, name)), -index)
            heap = self.heaps.setdefault(name, [])
            if len(heap) < _quota(self.quotas, name):
                heapq.heappush(heap, item)
                taken += 1
            elif item > heap[0]:
                _, evicted = heapq.heapreplace(heap, item)
                self._release(-evicted)
                taken += 1
        if taken:
            self.held[index] = taken
            self.records[index] = make()

    def candidates(self) -> dict[str, list[Any]]:
        out: dict[str, list[Any]] = {}
        for name, heap in self.heaps.items():
            entries = []
            for negative_rank, negative_index in sorted(heap, reverse=True):
                record = self.records[-negative_index]
                entries.append(
                    {
                        "rank": -negative_rank,
                        "path": self.path,
                        "component": self.component,
                        **record,
                    }
                )
            out[name] = entries
        return out
