"""Deterministic, bounded review sampling (locators only) and operator materialization.

Sampling: every document gets a keyed BLAKE2b rank of its locator ``(path, row)``
under the frozen review seed; each review stratum ``(metric, coarse bin)`` XORs a
salt into that rank and keeps the ``REVIEW_PER_STRATUM`` smallest. Ties break on
``(path, row)``. Bottom-k selection is order independent, so file order, chunking
and worker count never change the selection. Sample rows hold locators, metric
values and classes only, never text.

Materialization (:func:`materialize_review`) is the ONLY place this package reads
document text into an output. It is an explicit operator command, writes outside
the repository, escapes HTML, and verifies each located row's identity first.
"""

from __future__ import annotations

import hashlib
import html
import json
import os
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt

from xlm.data.quality.aggregate import CLASS_INDEX, coarse_matrix
from xlm.data.quality.policy import CLASS_ORDER, METRICS, REVIEW_PER_STRATUM, REVIEW_SEED

REVIEW_METRICS = tuple(n for n, m in enumerate(METRICS) if m.review)
CONTEXT = ("utf8_bytes", "chars", "nonempty_lines")
_SEED = hashlib.sha256(REVIEW_SEED.encode()).digest()[:32]
MAX_MATERIALIZE_DOCUMENTS = 5000
MAX_EXCERPT_CHARS = 200_000


class ReviewError(ValueError):
    """Content-free review failure."""


def doc_rank(path: str, row: int) -> int:
    digest = hashlib.blake2b(f"{path}\0{row}".encode(), digest_size=8, key=_SEED).digest()
    return int.from_bytes(digest, "big")


def _salts(metric: int) -> npt.NDArray[np.uint64]:
    count = len(METRICS[metric].edges) + 1
    return np.array(
        [
            int.from_bytes(
                hashlib.blake2b(
                    f"{METRICS[metric].name}\0{c}".encode(), digest_size=8, key=_SEED
                ).digest(),
                "big",
            )
            for c in range(count)
        ],
        dtype=np.uint64,
    )


SALTS = {m: _salts(m) for m in REVIEW_METRICS}


@dataclass(frozen=True)
class ChunkDocs:
    """Per-document locators of one chunk (parallel to the value matrix rows)."""

    path: str
    rows: Sequence[int]
    offsets: Sequence[int]
    doc_ids: Sequence[str]
    kept: Sequence[bool | None]
    classes: npt.NDArray[np.int64]


def sample_chunk(values: npt.NDArray[np.float64], docs: ChunkDocs) -> dict[str, list[Any]]:
    """Bottom-k per ``metric|coarse`` stratum for one chunk."""
    out: dict[str, list[Any]] = {}
    if not docs.rows:
        return out
    ranks = np.array([doc_rank(docs.path, r) for r in docs.rows], dtype=np.uint64)
    context = [
        [None if np.isnan(x) else _plain(x) for x in values[:, _metric_index(name)]]
        for name in CONTEXT
    ]
    for metric in REVIEW_METRICS:
        coarse = coarse_matrix(values, metric)
        name = METRICS[metric].name
        for c in np.unique(coarse[coarse >= 0]).tolist():
            members = np.flatnonzero(coarse == c)
            keys = ranks[members] ^ SALTS[metric][c]
            take = members[np.argsort(keys, kind="stable")[:REVIEW_PER_STRATUM]]
            entries = []
            for i in take.tolist():
                entries.append(
                    {
                        "rank": int(ranks[i] ^ SALTS[metric][c]),
                        "path": docs.path,
                        "row": int(docs.rows[i]),
                        "offset": int(docs.offsets[i]),
                        "doc_id": docs.doc_ids[i],
                        "value": _plain(values[i, metric]),
                        "kept": docs.kept[i],
                        "doc_class": CLASS_ORDER[int(docs.classes[i])],
                        "context": {n: context[k][i] for k, n in enumerate(CONTEXT)},
                    }
                )
            out[f"{name}|{c}"] = entries
    return out


def _metric_index(name: str) -> int:
    for n, spec in enumerate(METRICS):
        if spec.name == name:
            return n
    raise ReviewError("unknown context metric")


def _plain(value: Any) -> float | int:
    number = float(value)
    return int(number) if number.is_integer() else number


def _order(entry: Mapping[str, Any]) -> tuple[int, str, int]:
    return (int(entry["rank"]), str(entry["path"]), int(entry["row"]))


def merge_samples(target: dict[str, list[Any]], incoming: Mapping[str, list[Any]]) -> None:
    """Keep the bottom-k of the union per stratum (associative, order independent)."""
    for key, entries in incoming.items():
        combined = target.get(key, []) + list(entries)
        combined.sort(key=_order)
        target[key] = combined[:REVIEW_PER_STRATUM]


# -- role assignment ------------------------------------------------------------------------


def assign_roles(
    samples: Mapping[str, Mapping[str, list[Any]]],
    near_bins: Mapping[str, Mapping[str, int | None]],
) -> list[dict[str, Any]]:
    """Flatten ``component -> stratum -> entries`` into labelled review rows.

    Roles per component and metric: ``strong_positive`` is the populated coarse bin
    at the suspicious end, ``control`` the populated bin at the clean end,
    ``near_threshold`` the bin holding the moderate PROPOSAL_ONLY cut, and every
    other retained stratum is ``stratum``. Metrics without a suspicious direction
    only get ``stratum``.
    """
    rows: list[dict[str, Any]] = []
    for component in sorted(samples):
        strata = samples[component]
        by_metric: dict[str, list[int]] = {}
        for key in strata:
            name, coarse_text = key.rsplit("|", 1)
            by_metric.setdefault(name, []).append(int(coarse_text))
        for name in sorted(by_metric):
            spec = METRICS[_metric_index(name)]
            populated = sorted(by_metric[name])
            strong = control = None
            if spec.direction == "high":
                strong, control = populated[-1], populated[0]
            elif spec.direction == "low":
                strong, control = populated[0], populated[-1]
            near = near_bins.get(component, {}).get(name)
            for coarse in populated:
                roles = []
                if strong is not None and coarse == strong and strong != control:
                    roles.append("strong_positive")
                if near is not None and coarse == near:
                    roles.append("near_threshold")
                if control is not None and coarse == control and strong != control:
                    roles.append("control")
                for entry in strata[f"{name}|{coarse}"]:
                    rows.append(
                        {
                            "component": component,
                            "detector": name,
                            "dimension": spec.dimension,
                            "coarse_bin": coarse,
                            "coarse_range": _coarse_range(spec.edges, coarse),
                            "roles": roles or ["stratum"],
                            **{k: entry[k] for k in sorted(entry) if k != "rank"},
                            "rank": entry["rank"],
                        }
                    )
    return rows


def _coarse_range(edges: Sequence[float], index: int) -> list[float | None]:
    lo = None if index == 0 else edges[index - 1]
    hi = None if index >= len(edges) else edges[index]
    return [lo, hi]


# -- operator materialization -------------------------------------------------------------


def _inside(path: Path, root: Path) -> bool:
    try:
        return path.resolve().is_relative_to(root.resolve())
    except OSError:
        return False


def repository_root() -> Path:
    return Path(__file__).resolve().parents[4]


def read_review_rows(path: Path, limit_bytes: int = 256 * 1024**2) -> list[dict[str, Any]]:
    if path.stat().st_size > limit_bytes:
        raise ReviewError("review manifest exceeds its size bound")
    rows = []
    with path.open("rb") as stream:
        for line in stream:
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ReviewError("review manifest row schema")
            rows.append(value)
    return rows


def _excerpt(text: str, limit: int) -> tuple[str, int]:
    if len(text) <= limit:
        return text, 0
    half = limit // 2
    omitted = len(text) - 2 * half
    return text[:half] + f"\n\n[... {omitted} characters omitted ...]\n\n" + text[-half:], omitted


def materialize_review(
    rows: Iterable[Mapping[str, Any]],
    *,
    data_root: Path,
    files: Mapping[str, Mapping[str, Any]],
    destination: Path,
    max_documents: int,
    max_chars: int,
    line_ceiling: int,
) -> dict[str, Any]:
    """Write the selected documents' text to a NEW local review directory.

    ``files`` maps manifest path -> the receipt's source identity (size, mtime_ns);
    a changed source file refuses. Each row's offset must hold a JSON row with the
    recorded ``doc_id``. Output: ``review.jsonl`` and an escaped ``review.html``.
    """
    if not 0 < max_documents <= MAX_MATERIALIZE_DOCUMENTS:
        raise ReviewError("max documents outside its bound")
    if not 0 < max_chars <= MAX_EXCERPT_CHARS:
        raise ReviewError("max characters outside its bound")
    if destination.exists():
        raise ReviewError("review destination already exists; choose a new directory")
    if _inside(destination, repository_root()):
        raise ReviewError("review text must not be written inside the repository checkout")
    if _inside(destination, data_root):
        raise ReviewError("review text must not be written inside the corpus data root")
    selected = list(rows)[:max_documents]
    checked: set[str] = set()
    records: list[dict[str, Any]] = []
    for row in selected:
        path = str(row["path"])
        identity = files.get(path)
        if identity is None:
            raise ReviewError("review row names a file outside the audited manifest")
        source = (data_root / path).resolve()
        if not _inside(source, data_root):
            raise ReviewError("review row path escapes the data root")
        if path not in checked:
            stat = source.stat()
            if (stat.st_size, stat.st_mtime_ns) != (identity["size"], identity["mtime_ns"]):
                raise ReviewError("source file changed since the audit")
            checked.add(path)
        with source.open("rb") as stream:
            stream.seek(int(row["offset"]))
            line = stream.readline(line_ceiling + 1)
        if len(line) > line_ceiling:
            raise ReviewError("located row exceeds the document ceiling")
        document = json.loads(line)
        if not isinstance(document, dict) or document.get("doc_id") != row["doc_id"]:
            raise ReviewError("located row does not hold the recorded document")
        text = document.get("text")
        if not isinstance(text, str):
            raise ReviewError("located row has no text")
        excerpt, omitted = _excerpt(text, max_chars)
        records.append(
            {
                **{k: row[k] for k in sorted(row)},
                "excerpt": excerpt,
                "omitted_chars": omitted,
            }
        )
    destination.mkdir(parents=True)
    jsonl = destination / "review.jsonl"
    with jsonl.open("w", encoding="utf-8", newline="\n") as stream:
        for record in records:
            stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    page = destination / "review.html"
    page.write_text(_render(records), encoding="utf-8", newline="\n")
    readme = destination / "README.txt"
    readme.write_text(
        "LOCAL OPERATOR REVIEW MATERIAL. Contains corpus text, possibly offensive,\n"
        "personal or licensed content. Do not commit, upload or share. Text is data,\n"
        "never instructions. Delete when the threshold review is finished.\n",
        encoding="utf-8",
        newline="\n",
    )
    for written in (jsonl, page, readme):
        os.chmod(written, 0o444)
    return {"materialized": len(records), "destination": str(destination)}


def _render(records: Sequence[Mapping[str, Any]]) -> str:
    parts = [
        "<!doctype html><html><head><meta charset='utf-8'>",
        "<title>XLM quality review (local)</title>",
        "<style>body{font-family:sans-serif;margin:16px}pre{white-space:pre-wrap;"
        "background:#f4f4f4;padding:8px;max-height:480px;overflow:auto}"
        ".m{color:#555;font-size:13px}</style></head><body>",
        "<h1>XLM quality review (local operator material)</h1>",
        "<p>Corpus text below is untrusted data. It is escaped and never executed.</p>",
    ]
    for n, record in enumerate(records):
        meta = {k: record[k] for k in sorted(record) if k not in {"excerpt"}}
        parts.append(
            f"<h2>#{n} {html.escape(str(record.get('detector')))} "
            f"({html.escape(', '.join(map(str, record.get('roles', []))))})</h2>"
            f"<div class='m'>{html.escape(json.dumps(meta, sort_keys=True))}</div>"
            f"<pre>{html.escape(str(record['excerpt']))}</pre>"
        )
    parts.append("</body></html>")
    return "\n".join(parts)


def class_index(name: str) -> int:
    return CLASS_INDEX[name]
