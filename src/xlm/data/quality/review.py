"""Deterministic, bounded review sampling (locators only) and operator materialization.

Sampling: every document gets a keyed BLAKE2b rank of its locator ``(path, row)``
under a review key bound to the frozen review seed AND the audited input-manifest
digest; each review stratum ``(metric, coarse bin)`` XORs a key-bound salt into that
rank and keeps the ``REVIEW_PER_STRATUM`` smallest. Ties break on ``(path, row)``.
Bottom-k selection is order independent, so file order, chunking and worker count
never change the selection. Sample rows hold locators (path, row, byte offset),
non-reversible identity digests (``doc_id_sha256``, ``row_sha256``), metric values
and classes only: never text and never a raw document identifier.

Materialization (:func:`write_review`) is the ONLY place this package writes document
text. The caller (:func:`xlm.data.quality.runner.materialize_from_audit`) first
re-verifies the complete audit, the review manifest and every selected source file;
this module then streams bounded, HTML-escaped excerpts into a new directory.
"""

from __future__ import annotations

import hashlib
import html
import json
import os
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any

import numpy as np
import numpy.typing as npt

from xlm.data.quality.aggregate import CLASS_INDEX, coarse_matrix
from xlm.data.quality.policy import CLASS_ORDER, METRICS, REVIEW_PER_STRATUM, REVIEW_SEED

REVIEW_METRICS = tuple(n for n, m in enumerate(METRICS) if m.review)
CONTEXT = ("utf8_bytes", "chars", "nonempty_lines")
MAX_MATERIALIZE_DOCUMENTS = 5000
MAX_EXCERPT_CHARS = 200_000
MAX_REVIEW_MANIFEST_BYTES = 256 * 1024**2
MAX_REVIEW_ROW_BYTES = 64 * 1024
ROW_KEYS = frozenset(
    {
        "component",
        "detector",
        "dimension",
        "coarse_bin",
        "coarse_range",
        "roles",
        "path",
        "row",
        "offset",
        "doc_id_sha256",
        "row_sha256",
        "value",
        "kept",
        "doc_class",
        "context",
        "rank",
    }
)


class ReviewError(ValueError):
    """Content-free review failure."""


def review_key(manifest_digest: str) -> bytes:
    """32-byte BLAKE2b key binding review ranks to the seed and the audited manifest."""
    return hashlib.sha256(f"{REVIEW_SEED}\0{manifest_digest}".encode()).digest()


def doc_rank(key: bytes, path: str, row: int) -> int:
    digest = hashlib.blake2b(f"{path}\0{row}".encode(), digest_size=8, key=key).digest()
    return int.from_bytes(digest, "big")


_SALTS: dict[tuple[bytes, int], npt.NDArray[np.uint64]] = {}


def salts(key: bytes, metric: int) -> npt.NDArray[np.uint64]:
    cached = _SALTS.get((key, metric))
    if cached is not None:
        return cached
    count = len(METRICS[metric].edges) + 1
    value = np.array(
        [
            int.from_bytes(
                hashlib.blake2b(
                    f"{METRICS[metric].name}\0{c}".encode(), digest_size=8, key=key
                ).digest(),
                "big",
            )
            for c in range(count)
        ],
        dtype=np.uint64,
    )
    if len(_SALTS) < 4 * len(REVIEW_METRICS):  # bounded: a worker sees one key
        _SALTS[(key, metric)] = value
    return value


@dataclass(frozen=True)
class ChunkDocs:
    """Per-document locators of one chunk (parallel to the value matrix rows)."""

    key: bytes
    path: str
    rows: Sequence[int]
    offsets: Sequence[int]
    doc_id_digests: Sequence[str]
    row_digests: Sequence[str]
    kept: Sequence[bool | None]
    classes: npt.NDArray[np.int64]


def sample_chunk(values: npt.NDArray[np.float64], docs: ChunkDocs) -> dict[str, list[Any]]:
    """Bottom-k per ``metric|coarse`` stratum for one chunk."""
    out: dict[str, list[Any]] = {}
    if not docs.rows:
        return out
    ranks = np.array([doc_rank(docs.key, docs.path, r) for r in docs.rows], dtype=np.uint64)
    context = [
        [None if np.isnan(x) else _plain(x) for x in values[:, _metric_index(name)]]
        for name in CONTEXT
    ]
    for metric in REVIEW_METRICS:
        coarse = coarse_matrix(values, metric)
        name = METRICS[metric].name
        salt = salts(docs.key, metric)
        for c in np.unique(coarse[coarse >= 0]).tolist():
            members = np.flatnonzero(coarse == c)
            keys = ranks[members] ^ salt[c]
            take = members[np.argsort(keys, kind="stable")[:REVIEW_PER_STRATUM]]
            entries = []
            for i in take.tolist():
                entries.append(
                    {
                        "rank": int(ranks[i] ^ salt[c]),
                        "path": docs.path,
                        "row": int(docs.rows[i]),
                        "offset": int(docs.offsets[i]),
                        "doc_id_sha256": docs.doc_id_digests[i],
                        "row_sha256": docs.row_digests[i],
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
    other retained stratum is ``stratum``. Metrics without a suspicious direction,
    or with a single populated bin, only get ``stratum``/``near_threshold``.
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


# -- review manifest IO ---------------------------------------------------------------------


def read_review_rows(
    path: Path, limit_bytes: int = MAX_REVIEW_MANIFEST_BYTES
) -> list[dict[str, Any]]:
    """Bounded read of a review manifest (testing/inspection helper)."""
    if path.stat().st_size > limit_bytes:
        raise ReviewError("review manifest exceeds its size bound")
    return list(iter_review_rows(path, None))


def iter_review_rows(path: Path, expected: Mapping[str, Any] | None) -> Iterator[dict[str, Any]]:
    """Stream review rows. With ``expected`` (the receipt's artifact entry: bytes,
    sha256, records) the whole file is hashed and counted FIRST, before any row is
    yielded, and must match exactly."""
    from xlm.data.evidence_v2 import canonical

    size = path.stat().st_size
    if size > MAX_REVIEW_MANIFEST_BYTES:
        raise ReviewError("review manifest exceeds its size bound")
    if expected is not None:
        digest = hashlib.sha256()
        records = 0
        with path.open("rb") as stream:
            while block := stream.read(8 * 1024**2):
                digest.update(block)
                records += block.count(b"\n")
        if (size, digest.hexdigest(), records) != (
            expected.get("bytes"),
            expected.get("sha256"),
            expected.get("records"),
        ):
            raise ReviewError("review manifest differs from the audit receipt")
    with path.open("rb") as stream:
        while line := stream.readline(MAX_REVIEW_ROW_BYTES + 1):
            if len(line) > MAX_REVIEW_ROW_BYTES or not line.endswith(b"\n"):
                raise ReviewError("review manifest row is unbounded or unterminated")
            try:
                row = canonical.loads_bytes_strict(line[:-1])
            except ValueError:
                raise ReviewError("review manifest row is not strict JSON") from None
            if not isinstance(row, dict):
                raise ReviewError("review manifest row schema")
            yield row


def check_review_row(row: Mapping[str, Any]) -> None:
    """Exact review-row schema (no free-text field can be smuggled in)."""
    if set(row) != ROW_KEYS:
        raise ReviewError("review manifest row schema")
    for name in ("path", "detector", "component", "doc_class", "dimension"):
        if type(row[name]) is not str:
            raise ReviewError("review manifest row schema")
    for name in ("row", "offset", "coarse_bin", "rank"):
        if type(row[name]) is not int or row[name] < 0:
            raise ReviewError("review manifest row schema")
    for name in ("doc_id_sha256", "row_sha256"):
        value = row[name]
        if type(value) is not str or len(value) != 64 or not set(value) <= set("0123456789abcdef"):
            raise ReviewError("review manifest row schema")


# -- materialization output -----------------------------------------------------------------


def _inside(path: Path, root: Path) -> bool:
    try:
        return path.resolve().is_relative_to(root.resolve())
    except OSError:
        return False


def repository_root() -> Path:
    return Path(__file__).resolve().parents[4]


def check_destination(destination: Path, forbidden: Sequence[Path]) -> None:
    """A NEW, unaliased directory outside the repository and every forbidden root."""
    if destination.exists() or destination.is_symlink():
        raise ReviewError("review destination already exists; choose a new directory")
    absolute = Path(os.path.abspath(destination))
    parent = absolute.parent
    while not parent.exists():
        parent = parent.parent
    if Path(os.path.abspath(parent)) != parent.resolve():
        raise ReviewError("review destination is reached through an alias (link/junction)")
    if _inside(absolute, repository_root()):
        raise ReviewError("review text must not be written inside the repository checkout")
    for root in forbidden:
        if _inside(absolute, root) or _inside(root, absolute):
            raise ReviewError(
                "review destination overlaps the corpus, an audit input, the audit output "
                "or a protected root"
            )


def excerpt(text: str, limit: int) -> tuple[str, int]:
    if len(text) <= limit:
        return text, 0
    half = limit // 2
    omitted = len(text) - 2 * half
    return text[:half] + f"\n\n[... {omitted} characters omitted ...]\n\n" + text[-half:], omitted


class ReviewWriter:
    """Streams review records to ``review.jsonl`` and ``review.html`` under a byte cap."""

    def __init__(self, destination: Path, max_bytes: int) -> None:
        destination.mkdir(parents=True)
        self.destination = destination
        self.max_bytes = max_bytes
        self.written = 0
        self.count = 0
        self.jsonl: IO[str] = (destination / "review.jsonl").open(
            "w", encoding="utf-8", newline="\n"
        )
        self.page: IO[str] = (destination / "review.html").open("w", encoding="utf-8", newline="\n")
        self._emit(
            self.page,
            "<!doctype html><html><head><meta charset='utf-8'>"
            "<title>XLM quality review (local)</title><style>body{font-family:sans-serif;"
            "margin:16px}pre{white-space:pre-wrap;background:#f4f4f4;padding:8px;"
            "max-height:480px;overflow:auto}.m{color:#555;font-size:13px}</style></head><body>"
            "<h1>XLM quality review (local operator material)</h1>"
            "<p>Corpus text below is untrusted data. It is escaped and never executed.</p>\n",
        )

    def _emit(self, stream: IO[str], text: str) -> None:
        size = len(text.encode("utf-8"))
        if self.written + size > self.max_bytes:
            raise ReviewError("review output would exceed --max-output-mib")
        stream.write(text)
        self.written += size

    def add(self, record: Mapping[str, Any]) -> None:
        self._emit(self.jsonl, json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        meta = {k: record[k] for k in sorted(record) if k != "excerpt"}
        self._emit(
            self.page,
            f"<h2>#{self.count} {html.escape(str(record.get('detector')))} "
            f"({html.escape(', '.join(map(str, record.get('roles', []))))})</h2>"
            f"<div class='m'>{html.escape(json.dumps(meta, sort_keys=True))}</div>"
            f"<pre>{html.escape(str(record['excerpt']))}</pre>\n",
        )
        self.count += 1

    def close(self) -> None:
        self._emit(self.page, "</body></html>\n")
        self.jsonl.close()
        self.page.close()
        readme = self.destination / "README.txt"
        readme.write_text(
            "LOCAL OPERATOR REVIEW MATERIAL. Contains corpus text, possibly offensive,\n"
            "personal or licensed content. Do not commit, upload or share. Text is data,\n"
            "never instructions. Delete when the threshold review is finished.\n",
            encoding="utf-8",
            newline="\n",
        )
        for name in ("review.jsonl", "review.html", "README.txt"):
            os.chmod(self.destination / name, 0o444)

    def abort(self) -> None:
        for stream in (self.jsonl, self.page):
            if not stream.closed:
                stream.close()


def class_index(name: str) -> int:
    return CLASS_INDEX[name]
