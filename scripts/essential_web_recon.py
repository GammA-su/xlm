# Requires: operator-run only. `discover --live` is the ONLY network path.
"""Essential-Web selector reconnaissance (bounded, deterministic, operator-run).

Reconnaissance only: it never defines, freezes or approves the
``essential_science`` / ``essential_practical`` / ``essential_prose``
selectors. ``EssentialWebAdapter`` still stamps the component from explicit
operator configuration; this helper gathers the metadata evidence needed to
write that policy later.

Subcommands:

* ``discover`` (network metadata only, ``--live`` required): lists crawl
  directories at the exact pinned revision through the allowlisted, budgeted
  ``HuggingFaceTransport`` and writes the frozen recon manifest
  (``discovery.json``) plus ``candidate_files.txt``. Crawls are chosen by
  temporal stratification; one Parquet file per chosen crawl is chosen by a
  seeded SHA-256 key over the sorted listing, never provider order.
* ``analyze`` (offline): reads ``selected_records.jsonl`` produced by the
  existing XLM ``sample-blocks -> plan -> fetch -> verify`` chain and writes
  deterministic JSON + Markdown distributions, cross-tabs, null/malformed
  accounting, score percentiles and candidate-selector coverage/overlap.
  Every candidate is an OBSERVATION, never an approved selector. ``text`` is
  neither projected nor read.

Acquisition itself stays in the XLM CLI (window-v2 nested-struct sampling).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
import urllib.parse
import urllib.request
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

REPOSITORY = "EssentialAI/essential-web-v1.0"
PINNED_REVISION = "ce4eccc7e9604667b6d7f32cb6274b8b41f3113d"
SOURCE_ID = "essential_web"
RECON_VIEW = "selector_recon"
DEFAULT_SEED = 20260918
DEFAULT_STRATA = 8
DATA_ROOT = "data"
RECON_VERSION = "essential-recon-v1"
MANIFEST_KIND = "essential_web_selector_recon_manifest"
ANALYSIS_KIND = "essential_web_selector_recon_analysis"
CANDIDATE_STATUS = "OBSERVATION_ONLY_NOT_APPROVED"

_REVISION_RE = re.compile(r"^[0-9a-f]{40}$")
_CRAWL_RE = re.compile(r"^crawl=CC-MAIN-(\d{4})-(\d{2})$")
_PERCENTILES = (0, 1, 5, 10, 25, 50, 75, 90, 95, 99, 100)
_ENGLISH_THRESHOLDS = (0.5, 0.65, 0.8, 0.9, 0.95)
MAX_RECORDS = 200_000
MAX_RECORD_LINE_BYTES = 1024 * 1024


class ReconError(RuntimeError):
    """Fail-closed reconnaissance refusal."""


def recon_projection() -> tuple[str, ...]:
    """Certified Essential column contract minus ``text`` (metadata only).

    Derived from the adapter contract instead of guessed; the list-typed
    upstream column ``line_start_n_end_idx`` is not in that contract, so
    window-v2 never sees it.
    """
    from xlm.data.adapters.columns import columns_for

    fields = tuple(f for f in columns_for(SOURCE_ID, "essential_science") if f != "text")
    if len(fields) == len(columns_for(SOURCE_ID, "essential_science")):
        raise ReconError("certified Essential contract no longer lists 'text'; re-review")
    return fields


def require_revision(revision: str | None) -> str:
    if not revision or not _REVISION_RE.fullmatch(revision):
        raise ReconError("an exact 40-hex pinned revision is required; refusing branch names")
    return revision


def crawl_key(name: str) -> tuple[int, int]:
    """Sort key ``(year, week)`` for ``crawl=CC-MAIN-YYYY-WW``; malformed ids refuse."""
    match = _CRAWL_RE.fullmatch(name)
    if match is None:
        raise ReconError(f"malformed crawl directory id: {name!r}")
    year, week = int(match.group(1)), int(match.group(2))
    if not 2008 <= year <= 2100 or not 1 <= week <= 53:
        raise ReconError(f"crawl id out of range: {name!r}")
    return year, week


def _det_index(seed: int, parts: Sequence[str], size: int) -> int:
    if size <= 0:
        raise ReconError("deterministic choice over an empty set")
    digest = hashlib.sha256("|".join([str(seed), *parts]).encode("utf-8")).hexdigest()
    return int(digest, 16) % size


def select_crawls(
    crawls: Iterable[str], *, seed: int, revision: str, strata: int
) -> list[dict[str, Any]]:
    """Temporal stratification: sort by (year, week), split into ``strata``
    contiguous near-equal blocks, pick one crawl per block by seeded key."""
    require_revision(revision)
    names = list(crawls)
    if len(set(names)) != len(names):
        raise ReconError("duplicate crawl directories in listing")
    ordered = sorted(names, key=crawl_key)
    if strata < 1 or strata > len(ordered):
        raise ReconError(f"strata must be in [1, {len(ordered)}], got {strata}")
    chosen: list[dict[str, Any]] = []
    for index in range(strata):
        lo, hi = (index * len(ordered)) // strata, ((index + 1) * len(ordered)) // strata
        block = ordered[lo:hi]
        pick = _det_index(
            seed, (REPOSITORY, revision, RECON_VERSION, "stratum", str(index)), len(block)
        )
        chosen.append(
            {
                "stratum": index,
                "stratum_first": block[0],
                "stratum_last": block[-1],
                "stratum_size": len(block),
                "crawl": block[pick],
            }
        )
    return chosen


def choose_file(files: Iterable[str], *, crawl: str, seed: int, revision: str) -> str:
    """One Parquet file per crawl by seeded key over the sorted listing."""
    parquet = sorted(set(files))
    if not parquet:
        raise ReconError(f"no Parquet files listed directly under {crawl}")
    return parquet[
        _det_index(seed, (REPOSITORY, revision, RECON_VERSION, crawl, "file"), len(parquet))
    ]


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def manifest_digest(body: Mapping[str, Any]) -> str:
    return _sha256_text(_canonical({k: v for k, v in body.items() if k != "digest"}))


# Lister: path -> list of {"type": "file"|"directory", "path", "size", "oid"}.
Lister = Callable[[str], list[dict[str, Any]]]


def build_manifest(lister: Lister, *, revision: str, seed: int, strata: int) -> dict[str, Any]:
    require_revision(revision)
    top = lister(DATA_ROOT)
    crawl_dirs: list[str] = []
    ignored: list[str] = []
    for entry in top:
        path = str(entry.get("path", ""))
        parent, _, name = path.rpartition("/")
        if parent != DATA_ROOT:
            raise ReconError(f"listing entry outside {DATA_ROOT}/: {path!r}")
        if entry.get("type") == "directory":
            crawl_key(name)
            crawl_dirs.append(name)
        else:
            ignored.append(path)
    chosen = select_crawls(crawl_dirs, seed=seed, revision=revision, strata=strata)
    selections: list[dict[str, Any]] = []
    for item in chosen:
        crawl_path = f"{DATA_ROOT}/{item['crawl']}"
        entries = lister(crawl_path)
        subdirs = sorted(str(e["path"]) for e in entries if e.get("type") == "directory")
        if subdirs:
            raise ReconError(f"{crawl_path} holds subdirectories {subdirs[:5]}; layout unreviewed")
        files = {
            str(e["path"]): e
            for e in entries
            if e.get("type") == "file" and str(e.get("path", "")).endswith(".parquet")
        }
        for path in files:
            if path.rpartition("/")[0] != crawl_path:
                raise ReconError(f"listed file {path!r} is not under {crawl_path}")
        chosen_path = choose_file(files, crawl=item["crawl"], seed=seed, revision=revision)
        entry = files[chosen_path]
        lfs = entry.get("lfs") if isinstance(entry.get("lfs"), Mapping) else {}
        selections.append(
            {
                **item,
                "parquet_files_in_crawl": len(files),
                "listing_sha256": _sha256_text(_canonical(sorted(files))),
                "file": chosen_path,
                "size": entry.get("size"),
                "oid": entry.get("oid"),
                "lfs_sha256": lfs.get("oid") if isinstance(lfs, Mapping) else None,
            }
        )
    ordered = sorted(crawl_dirs, key=crawl_key)
    body: dict[str, Any] = {
        "kind": MANIFEST_KIND,
        "recon_version": RECON_VERSION,
        "status": "RECONNAISSANCE_ONLY",
        "repository": REPOSITORY,
        "revision": revision,
        "source_id": SOURCE_ID,
        "view_id": RECON_VIEW,
        "seed": seed,
        "strata": strata,
        "ordering": {
            "crawls": "sorted by (year, week) parsed from crawl=CC-MAIN-YYYY-WW",
            "strata": "contiguous blocks [floor(i*N/K), floor((i+1)*N/K))",
            "crawl_choice": "sha256('seed|repo|revision|essential-recon-v1|stratum|i') mod block",
            "file_choice": "sha256('seed|repo|revision|essential-recon-v1|crawl|file') mod sorted",
        },
        "crawl_dir_count": len(ordered),
        "crawl_dirs": ordered,
        "crawl_dirs_sha256": _sha256_text(_canonical(ordered)),
        "ignored_top_level_files": sorted(ignored),
        "projection": list(recon_projection()),
        "selections": selections,
        "files": [s["file"] for s in selections],
    }
    body["digest"] = manifest_digest(body)
    return body


def load_manifest(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ReconError(f"{path}: manifest must be a JSON object")
    if data.get("kind") != MANIFEST_KIND or data.get("digest") != manifest_digest(data):
        raise ReconError(f"{path}: not a recon manifest or digest mismatch")
    require_revision(data.get("revision"))
    return data


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    with temp.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(text)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)


class HubTreeLister:
    """Paginated, budgeted HF tree listing on the allowlisted transport."""

    def __init__(self, revision: str, max_requests: int, max_bytes: int) -> None:
        from xlm.data.sources.transport import HuggingFaceTransport, TransportBudget

        self.revision = require_revision(revision)
        self.budget = TransportBudget(
            max_bytes=max_bytes,
            max_requests=max_requests,
            deadline_seconds=600.0,
            per_request_timeout=30.0,
        )
        self.transport = HuggingFaceTransport(self.budget)

    def __call__(self, path: str) -> list[dict[str, Any]]:
        from xlm.data.sources.transport import validate_host

        quoted = urllib.parse.quote(path, safe="/")
        url: str | None = (
            f"https://huggingface.co/api/datasets/{REPOSITORY}/tree/"
            f"{self.revision}/{quoted}?expand=false"
        )
        entries: list[dict[str, Any]] = []
        while url is not None:
            validate_host(url)
            self.budget.record_request()
            request = urllib.request.Request(
                url, headers={"User-Agent": "xlm-data-discovery/1.0"}, method="GET"
            )
            with self.transport.opener.open(request, timeout=self.budget.per_request_timeout) as r:
                page = json.loads(self.budget.read_body(r, 4 * 1024 * 1024).decode("utf-8"))
                link = r.headers.get("Link", "")
            if not isinstance(page, list):
                raise ReconError(f"unexpected tree listing for {path}")
            entries.extend(e for e in page if isinstance(e, dict))
            match = re.search(r'<([^>]+)>;\s*rel="next"', link)
            url = match.group(1) if match else None
        return entries


# ---------------------------------------------------------------- analysis

MISSING, NULL, MALFORMED = "__missing__", "__null__", "__malformed__"

#: Selector-relevant paths (all under the execution-v2 two-field projection).
#: id/pid/metadata are NOT required: crawl/file/row identity comes from the
#: acquisition locator, never from optional upstream fields.
ACCOUNTED_PATHS = (
    "eai_taxonomy.free_decimal_correspondence.primary.code",
    "eai_taxonomy.free_decimal_correspondence.primary.labels.level_1",
    "eai_taxonomy.document_type_v2.primary.label",
    "eai_taxonomy.bloom_knowledge_domain.primary.label",
    "eai_taxonomy.bloom_cognitive_process.primary.label",
    "quality_signals.fasttext.english",
)

#: Candidate probes over OBSERVED fields. FDC codes are Dewey-compatible
#: decimals (real row: 746.92 -> Arts / Needlework). These are hypotheses to
#: measure, never selectors; refine with --candidates after seeing labels.
DEFAULT_CANDIDATES: dict[str, dict[str, Any]] = {
    "science.fdc_5xx": {
        "group": "science",
        "all": [
            {"path": "eai_taxonomy.free_decimal_correspondence.primary.code", "prefix_in": ["5"]}
        ],
    },
    "science.fdc_5xx_61x": {
        "group": "science",
        "all": [
            {
                "path": "eai_taxonomy.free_decimal_correspondence.primary.code",
                "prefix_in": ["5", "61"],
            }
        ],
    },
    "practical.fdc_6xx": {
        "group": "practical",
        "all": [
            {"path": "eai_taxonomy.free_decimal_correspondence.primary.code", "prefix_in": ["6"]}
        ],
    },
    "practical.bloom_procedural": {
        "group": "practical",
        "all": [
            {"path": "eai_taxonomy.bloom_knowledge_domain.primary.label", "in": ["Procedural"]}
        ],
    },
    "prose.fdc_8xx": {
        "group": "prose",
        "all": [
            {"path": "eai_taxonomy.free_decimal_correspondence.primary.code", "prefix_in": ["8"]}
        ],
    },
    "prose.fdc_7xx_8xx_9xx": {
        "group": "prose",
        "all": [
            {
                "path": "eai_taxonomy.free_decimal_correspondence.primary.code",
                "prefix_in": ["7", "8", "9"],
            }
        ],
    },
}

_CONDITION_KEYS = {"path", "in", "prefix_in", "gte", "lte"}


def get_path(record: Mapping[str, Any], path: str) -> tuple[str, Any]:
    """Return (status, value): ok / missing / null / malformed (non-mapping parent)."""
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


def _label(record: Mapping[str, Any], path: str) -> str:
    status, value = get_path(record, path)
    if status != "ok":
        return status
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return MALFORMED
    text = str(value).strip()
    return text if text else MALFORMED


def _number(record: Mapping[str, Any], path: str) -> float | None:
    status, value = get_path(record, path)
    if status != "ok" or isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def fdc_prefix(code: str, digits: int) -> str:
    if code in (MISSING, NULL, MALFORMED):
        return code
    head = code.split(".", 1)[0]
    if not head.isdigit():
        return MALFORMED
    return head.zfill(3)[:digits]


def percentiles(values: Sequence[float]) -> dict[str, float | None]:
    """Nearest-rank percentiles (deterministic, no interpolation)."""
    ordered = sorted(values)
    out: dict[str, float | None] = {}
    for p in _PERCENTILES:
        if not ordered:
            out[f"p{p}"] = None
            continue
        rank = max(1, math.ceil(p / 100 * len(ordered)))
        out[f"p{p}"] = round(ordered[rank - 1], 6)
    return out


def validate_candidates(candidates: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Data-only predicate grammar; anything else (incl. 'approved') refuses."""
    checked: dict[str, dict[str, Any]] = {}
    for name, spec in candidates.items():
        if not isinstance(spec, Mapping) or set(spec) - {"group", "all", "description"}:
            raise ReconError(f"candidate {name!r}: only group/all/description are allowed")
        if spec.get("group") not in ("science", "practical", "prose"):
            raise ReconError(f"candidate {name!r}: group must be science/practical/prose")
        conditions = spec.get("all")
        if not isinstance(conditions, list) or not conditions:
            raise ReconError(f"candidate {name!r}: 'all' must be a non-empty list")
        for condition in conditions:
            if not isinstance(condition, Mapping) or "path" not in condition:
                raise ReconError(f"candidate {name!r}: each condition needs a path")
            if set(condition) - _CONDITION_KEYS or len(condition) < 2:
                raise ReconError(f"candidate {name!r}: unsupported condition {sorted(condition)}")
        checked[str(name)] = dict(spec)
    return checked


def matches(record: Mapping[str, Any], spec: Mapping[str, Any]) -> bool:
    for condition in spec["all"]:
        path = condition["path"]
        if "gte" in condition or "lte" in condition:
            number = _number(record, path)
            if number is None:
                return False
            if "gte" in condition and number < float(condition["gte"]):
                return False
            if "lte" in condition and number > float(condition["lte"]):
                return False
        label = _label(record, path)
        if "in" in condition and label not in condition["in"]:
            return False
        if "prefix_in" in condition and (
            label in (MISSING, NULL, MALFORMED)
            or not any(label.startswith(p) for p in condition["prefix_in"])
        ):
            return False
    return True


def _share(count: int, total: int) -> float:
    return round(count / total, 6) if total else 0.0


def _dist(counter: Counter[str], total: int) -> list[dict[str, Any]]:
    return [
        {"value": k, "count": v, "share": _share(v, total)}
        for k, v in sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))
    ]


def _crosstab(pairs: Iterable[tuple[str, str]]) -> dict[str, dict[str, int]]:
    table: dict[str, Counter[str]] = {}
    for row, column in pairs:
        table.setdefault(row, Counter())[column] += 1
    return {row: dict(sorted(cols.items())) for row, cols in sorted(table.items())}


def read_records(path: Path, manifest: Mapping[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """Bounded read of ``selected_records.jsonl``; locator must match the manifest."""
    from xlm.data.acquisition.records import LOCATOR_FIELD

    allowed = {s["file"]: s["crawl"] for s in manifest["selections"]}
    rows: list[tuple[str, dict[str, Any]]] = []
    with path.open("rb") as stream:
        for number, raw in enumerate(stream, start=1):
            if len(raw) > MAX_RECORD_LINE_BYTES:
                raise ReconError(f"line {number} exceeds {MAX_RECORD_LINE_BYTES} bytes")
            if len(rows) >= MAX_RECORDS:
                raise ReconError(f"more than {MAX_RECORDS} records; recon is not acquisition")
            record = json.loads(raw)
            locator = record.pop(LOCATOR_FIELD, None)
            if not isinstance(locator, Mapping):
                raise ReconError(f"line {number}: missing acquisition locator")
            if locator.get("revision") != manifest["revision"] or (
                locator.get("repository") != manifest["repository"]
            ):
                raise ReconError(f"line {number}: record is not from the manifest revision")
            source = locator.get("source_file")
            if source not in allowed:
                raise ReconError(f"line {number}: source file {source!r} not in manifest")
            record.pop("text", None)  # never analyzed; recon plans do not project it
            rows.append((allowed[source], record))
    return rows


def analyze(
    rows: Sequence[tuple[str, Mapping[str, Any]]],
    manifest: Mapping[str, Any],
    candidates: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    specs = validate_candidates(DEFAULT_CANDIDATES if candidates is None else candidates)
    total = len(rows)
    per_crawl = Counter(crawl for crawl, _ in rows)
    accounting = {
        path: dict(
            sorted(
                Counter(s if (s := get_path(r, path)[0]) != "ok" else "ok" for _, r in rows).items()
            )
        )
        for path in ACCOUNTED_PATHS
    }
    classifier_keys = sorted(
        {
            k
            for _, r in rows
            if isinstance(r.get("eai_taxonomy"), Mapping)
            for k in r["eai_taxonomy"]
        }
    )
    classifiers: dict[str, Any] = {}
    for key in classifier_keys:
        field = "code" if key == "free_decimal_correspondence" else "label"
        path = f"eai_taxonomy.{key}.primary.{field}"
        classifiers[key] = {
            "path": path,
            "primary": _dist(Counter(_label(r, path) for _, r in rows), total),
        }
    fdc_path = "eai_taxonomy.free_decimal_correspondence.primary.code"
    codes = [_label(r, fdc_path) for _, r in rows]
    doc = [_label(r, "eai_taxonomy.document_type_v2.primary.label") for _, r in rows]
    bloom = [_label(r, "eai_taxonomy.bloom_knowledge_domain.primary.label") for _, r in rows]
    fdc1 = [fdc_prefix(c, 1) for c in codes]
    fdc = {f"digits_{d}": _dist(Counter(fdc_prefix(c, d) for c in codes), total) for d in (1, 2, 3)}
    fdc["level_1_label"] = _dist(
        Counter(
            _label(r, "eai_taxonomy.free_decimal_correspondence.primary.labels.level_1")
            for _, r in rows
        ),
        total,
    )
    scores: dict[str, Any] = {}
    for group in ("fasttext", "red_pajama_v2"):
        keys = sorted(
            {
                k
                for _, r in rows
                if isinstance(q := r.get("quality_signals"), Mapping)
                and isinstance(q.get(group), Mapping)
                for k in q[group]
            }
        )
        for key in keys:
            path = f"quality_signals.{group}.{key}"
            values = [v for _, r in rows if (v := _number(r, path)) is not None]
            scores[path] = {
                "n": len(values),
                "non_numeric_or_absent": total - len(values),
                "percentiles": percentiles(values),
            }
    english = [_number(r, "quality_signals.fasttext.english") for _, r in rows]
    observations = {
        "fasttext_english_at_or_above": {
            str(t): _share(sum(1 for v in english if v is not None and v >= t), total)
            for t in _ENGLISH_THRESHOLDS
        },
        "extraction_artifacts_primary": _dist(
            Counter(_label(r, "eai_taxonomy.extraction_artifacts.primary.label") for _, r in rows),
            total,
        ),
        "missing_content_primary": _dist(
            Counter(_label(r, "eai_taxonomy.missing_content.primary.label") for _, r in rows), total
        ),
        "note": "Observed shares only; no threshold is proposed or applied.",
    }
    hits = {
        name: {i for i, (_, r) in enumerate(rows) if matches(r, spec)}
        for name, spec in specs.items()
    }
    coverage = {
        name: {
            "group": specs[name]["group"],
            "status": CANDIDATE_STATUS,
            "definition": specs[name]["all"],
            "count": len(ids),
            "share": _share(len(ids), total),
            "per_crawl": {c: sum(1 for i in ids if rows[i][0] == c) for c in sorted(per_crawl)},
        }
        for name, ids in hits.items()
    }
    names = sorted(hits)
    overlaps = [
        {
            "a": a,
            "b": b,
            "intersection": len(hits[a] & hits[b]),
            "jaccard": _share(len(hits[a] & hits[b]), len(hits[a] | hits[b])),
        }
        for i, a in enumerate(names)
        for b in names[i + 1 :]
    ]
    groups = {
        g: set().union(*(hits[n] for n in names if specs[n]["group"] == g))
        for g in ("science", "practical", "prose")
    }
    union = set().union(*groups.values())
    group_overlap = {
        "union_share": {g: _share(len(ids), total) for g, ids in groups.items()},
        "pairwise_intersection": {
            f"{a}&{b}": len(groups[a] & groups[b])
            for a, b in (("science", "practical"), ("science", "prose"), ("practical", "prose"))
        },
        "unassigned_share": _share(total - len(union), total),
    }
    return {
        "kind": ANALYSIS_KIND,
        "status": "RECONNAISSANCE_ONLY",
        "approved_selectors": [],
        "partial_sample_warning": (
            "Window samples are clustered (one contiguous window per file); shares are "
            "diagnostic observations, not unbiased corpus estimates."
        ),
        "manifest_digest": manifest["digest"],
        "repository": manifest["repository"],
        "revision": manifest["revision"],
        "text_analyzed": False,
        "records": total,
        "records_per_crawl": dict(sorted(per_crawl.items())),
        "path_accounting": accounting,
        "classifiers": classifiers,
        "fdc": fdc,
        "crosstabs": {
            "fdc_digit1_x_document_type_v2": _crosstab(zip(fdc1, doc, strict=True)),
            "fdc_digit1_x_bloom_knowledge_domain": _crosstab(zip(fdc1, bloom, strict=True)),
            "document_type_v2_x_bloom_knowledge_domain": _crosstab(zip(doc, bloom, strict=True)),
        },
        "scores": scores,
        "quality_observations": observations,
        "candidates": coverage,
        "candidate_overlaps": overlaps,
        "group_overlap": group_overlap,
    }


def _esc(value: Any) -> str:
    """Escape data-derived text for Markdown/HTML rendering (untrusted labels)."""
    text = str(value).replace("\r", " ").replace("\n", " ").replace("`", "'")
    return text.replace("|", "\\|").replace("<", "&lt;").replace(">", "&gt;")


def render_markdown(result: Mapping[str, Any]) -> str:
    lines = [
        "# Essential-Web selector reconnaissance (OBSERVATIONS ONLY)",
        "",
        f"- revision `{result['revision']}`, manifest digest `{result['manifest_digest']}`",
        f"- records: {result['records']} (text analyzed: {result['text_analyzed']})",
        "- No candidate below is approved or final.",
        f"- {result['partial_sample_warning']}",
        "",
        "## Records per crawl",
        "",
        "| crawl | records |",
        "|---|---:|",
        *(f"| {_esc(c)} | {n} |" for c, n in result["records_per_crawl"].items()),
        "",
        "## Path accounting",
        "",
        "| path | counts |",
        "|---|---|",
        *(f"| `{p}` | {_esc(_canonical(c))} |" for p, c in result["path_accounting"].items()),
        "",
        "## FDC first digit",
        "",
        "| value | count | share |",
        "|---|---:|---:|",
        *(
            f"| {_esc(d['value'])} | {d['count']} | {d['share']} |"
            for d in result["fdc"]["digits_1"]
        ),
        "",
    ]
    for key, info in result["classifiers"].items():
        lines += [
            f"## {key} (`{info['path']}`)",
            "",
            "| value | count | share |",
            "|---|---:|---:|",
        ]
        lines += [
            f"| {_esc(d['value'])} | {d['count']} | {d['share']} |" for d in info["primary"][:25]
        ]
        lines.append("")
    english = result["scores"].get("quality_signals.fasttext.english", {})
    lines += [
        "## fastText English percentiles",
        "",
        f"`{_canonical(english.get('percentiles'))}`",
        "",
        "## Quality observations",
        "",
        f"`{_canonical(result['quality_observations']['fasttext_english_at_or_above'])}`",
        "",
        "## Candidate coverage (NOT APPROVED)",
        "",
        "| candidate | group | count | share |",
        "|---|---|---:|---:|",
    ]
    lines += [
        f"| {n} | {c['group']} | {c['count']} | {c['share']} |"
        for n, c in result["candidates"].items()
    ]
    lines += [
        "",
        "## Candidate overlaps",
        "",
        "| a | b | intersection | jaccard |",
        "|---|---|---:|---:|",
    ]
    lines += [
        f"| {o['a']} | {o['b']} | {o['intersection']} | {o['jaccard']} |"
        for o in result["candidate_overlaps"]
    ]
    lines += ["", f"Group overlap: `{_canonical(result['group_overlap'])}`", ""]
    return "\n".join(lines)


# ---------------------------------------------------------------- execution v2

EXECUTION_KIND = "essential_web_selector_recon_execution"
EXECUTION_VERSION = "essential-recon-execution-v2"
BUNDLE_KIND = "essential_web_selector_recon_bundle"
EXECUTION_PROJECTION = ("eai_taxonomy", "quality_signals")
UNIT_RECORDS = 512
EXECUTION_WINDOW = {
    "policy_version": 2,
    "max_window_scan_rows": 16384,
    "stream_buffer_bytes": 4 * 1024 * 1024,
    "batch_rows": 256,
}
MAX_PART_BYTES = 64 * 1024 * 1024
MAX_JOURNAL_BYTES = 4 * 1024 * 1024
MAX_FOOTER_BYTES = 1024 * 1024
NARROWING_RATIONALE = (
    "The discovery (v1) 5-field projection [eai_taxonomy, quality_signals, id, pid, "
    "metadata] needed ~104 estimated requests per file, above the per-plan pilot "
    "ceiling of 100, and footer planning refused. The 2-field selector-analysis "
    "projection [eai_taxonomy, quality_signals] passed at 85 requests per file on all "
    "8 files. The ceiling is per acquisition plan, so execution is split into 8 "
    "independent 512-row pilot plans (one per selected file), never one 8-file plan."
)
PROBE_LIMITATION = (
    "The generic 'xlm data probe' for selector_recon hit 'response exceeds its "
    "allocated body limit' (0 body bytes, 1 request): the repository API metadata "
    "response exceeds the probe's per-response ceiling. The production probe is "
    "unchanged. Plans bind the revision through an operator recon-only catalog "
    "pinned to the independently discovered exact revision; that is a research "
    "binding, NOT production evidence or admission."
)


def unit_paths(unit: str) -> dict[str, str]:
    """Paths relative to the recon root; footers use the observed split/NN layout."""
    return {
        "rows": f"split/{unit}/rows.json",
        "evidence": f"split/{unit}/rows.evidence.json",
        "plan": f"units/{unit}/plan.json",
        "raw": f"units/{unit}/raw",
        "scratch": f"units/{unit}/scratch",
    }


def _read_json(path: Path, limit: int) -> Any:
    if not path.is_file():
        raise ReconError(f"missing required artifact {path}")
    if path.stat().st_size > limit:
        raise ReconError(f"{path} exceeds {limit} bytes")
    return json.loads(path.read_bytes())


def check_footer(
    rows: Any, evidence: Any, selection: Mapping[str, Any], manifest: Mapping[str, Any]
) -> tuple[int, int]:
    """Adopt one footer unit only if it matches the execution identity exactly."""
    file = selection["file"]
    if not isinstance(rows, dict) or list(rows) != [file]:
        raise ReconError(f"rows.json must hold exactly [{file}], got {list(rows)[:3]}")
    bound = rows[file]
    if not (isinstance(bound, list) and len(bound) == 2 and all(type(v) is int for v in bound)):
        raise ReconError(f"rows.json range for {file} is malformed")
    start, stop = bound
    if not isinstance(evidence, dict):
        raise ReconError("rows.evidence.json must be an object")
    expected = {
        "source_id": SOURCE_ID,
        "view_id": RECON_VIEW,
        "revision": manifest["revision"],
        "seed": manifest["seed"],
        "mode": "window",
        "selected_files": [file],
        "projected_logical_fields": list(EXECUTION_PROJECTION),
        "planned_records": UNIT_RECORDS,
        "row_ranges": {file: [start, stop]},
    }
    for key, value in expected.items():
        if evidence.get(key) != value:
            raise ReconError(f"footer evidence {key}={evidence.get(key)!r}, expected {value!r}")
    if sorted(evidence.get("projected_fields") or []) != sorted(EXECUTION_PROJECTION):
        raise ReconError(f"footer projection {evidence.get('projected_fields')} is not 2-field")
    policy = evidence.get("window_policy") or {}
    for key, value in EXECUTION_WINDOW.items():
        if policy.get(key) != value:
            raise ReconError(f"footer window_policy.{key}={policy.get(key)!r}, expected {value}")
    windows = evidence.get("windows") or []
    if len(windows) != 1 or windows[0].get("file") != file:
        raise ReconError("footer evidence must hold exactly one window for its file")
    window = windows[0]
    if (window.get("start_row"), window.get("stop_row")) != (start, stop):
        raise ReconError("footer window rows disagree with rows.json")
    if stop - start != UNIT_RECORDS:
        raise ReconError(f"row range [{start},{stop}) is not {UNIT_RECORDS} rows")
    from xlm.data.acquisition.plan import PILOT_MAX_REQUESTS

    if int(window.get("estimated_requests", PILOT_MAX_REQUESTS + 1)) > PILOT_MAX_REQUESTS:
        raise ReconError(f"unit estimated requests exceed the pilot ceiling {PILOT_MAX_REQUESTS}")
    return start, stop


def build_execution(manifest: Mapping[str, Any], root: Path, expect_digest: str) -> dict[str, Any]:
    """Execution-v2 identity over the frozen discovery manifest (never rewritten)."""
    from xlm.data.acquisition.plan import PILOT_MAX_REQUESTS

    if manifest["digest"] != expect_digest:
        raise ReconError(f"discovery digest {manifest['digest']} != expected {expect_digest}")
    if manifest["revision"] != PINNED_REVISION or manifest["seed"] != DEFAULT_SEED:
        raise ReconError("discovery revision/seed differ from the pinned recon identity")
    selections = manifest["selections"]
    if [s["stratum"] for s in selections] != list(range(manifest["strata"])):
        raise ReconError("discovery selections are not one per stratum in order")
    if [s["file"] for s in selections] != manifest["files"]:
        raise ReconError("discovery files disagree with its selections")
    units: list[dict[str, Any]] = []
    for selection in selections:
        unit = f"{selection['stratum']:02d}"
        paths = unit_paths(unit)
        rows = _read_json(root / paths["rows"], MAX_FOOTER_BYTES)
        evidence = _read_json(root / paths["evidence"], 16 * MAX_FOOTER_BYTES)
        start, stop = check_footer(rows, evidence, selection, manifest)
        window = evidence["windows"][0]
        units.append(
            {
                "unit": unit,
                "stratum": selection["stratum"],
                "stratum_first": selection["stratum_first"],
                "stratum_last": selection["stratum_last"],
                "crawl": selection["crawl"],
                "file": selection["file"],
                "row_range": [start, stop],
                "records": UNIT_RECORDS,
                "row_group": window.get("row_group"),
                "expected_scan_rows": window.get("expected_scan_rows"),
                "estimated_requests": window.get("estimated_requests"),
                "estimated_transfer_upper_bytes": window.get("estimated_transfer_upper_bytes"),
                "rows_sha256": hashlib.sha256((root / paths["rows"]).read_bytes()).hexdigest(),
                "evidence_sha256": hashlib.sha256(
                    (root / paths["evidence"]).read_bytes()
                ).hexdigest(),
                "paths": paths,
            }
        )
    body: dict[str, Any] = {
        "kind": EXECUTION_KIND,
        "execution_version": EXECUTION_VERSION,
        "status": "RECONNAISSANCE_ONLY",
        "parent_discovery_digest": manifest["digest"],
        "parent_discovery_projection": list(manifest["projection"]),
        "repository": manifest["repository"],
        "revision": manifest["revision"],
        "source_id": SOURCE_ID,
        "view_id": RECON_VIEW,
        "seed": manifest["seed"],
        "strata": manifest["strata"],
        "projection": list(EXECUTION_PROJECTION),
        "records_per_unit": UNIT_RECORDS,
        "total_target_records": UNIT_RECORDS * len(units),
        "window_policy": dict(EXECUTION_WINDOW),
        "plan_shape": "one independent selected_records pilot plan per selected file",
        "pilot_max_requests_per_plan": PILOT_MAX_REQUESTS,
        "narrowing_rationale": NARROWING_RATIONALE,
        "probe_limitation": PROBE_LIMITATION,
        "units": units,
    }
    body["digest"] = manifest_digest(body)
    return body


def _write_or_adopt(path: Path, text: str) -> str:
    """Restart-safe: identical existing bytes are adopted; anything else refuses."""
    if path.exists():
        if path.read_bytes() == text.encode("utf-8"):
            return "adopted"
        raise ReconError(f"{path} exists with different content; refusing to overwrite")
    _atomic_write(path, text)
    return "written"


def load_execution(path: Path) -> dict[str, Any]:
    data = _read_json(path, 4 * MAX_FOOTER_BYTES)
    if (
        not isinstance(data, dict)
        or data.get("kind") != EXECUTION_KIND
        or data.get("execution_version") != EXECUTION_VERSION
        or data.get("digest") != manifest_digest(data)
    ):
        raise ReconError(f"{path}: not an execution-v2 manifest or digest mismatch")
    return data


def _check_plan(plan: Any, unit: Mapping[str, Any], execution: Mapping[str, Any]) -> None:
    from xlm.data.acquisition.plan import PILOT_MAX_REQUESTS

    file, (start, stop) = unit["file"], unit["row_range"]
    facts = {
        "source_id": (plan.source_id, SOURCE_ID),
        "view_id": (plan.view_id, RECON_VIEW),
        "repository": (plan.repository, execution["repository"]),
        "revision": (plan.revision, execution["revision"]),
        "mode": (plan.mode.value, "selected_records"),
        "selected_files": (list(plan.selected_files), [file]),
        "row_ranges": (
            {k: list(v) for k, v in (plan.row_ranges or {}).items()},
            {file: [start, stop]},
        ),
        "projected_fields": (list(plan.projected_fields or []), list(EXECUTION_PROJECTION)),
        "is_pilot": (plan.is_pilot, True),
    }
    for key, (actual, expected) in facts.items():
        if actual != expected:
            raise ReconError(f"unit {unit['unit']}: plan {key}={actual!r}, expected {expected!r}")
    window = plan.parquet_window
    if window is None or any(getattr(window, k) != v for k, v in EXECUTION_WINDOW.items()):
        raise ReconError(f"unit {unit['unit']}: plan window policy is not execution-v2")
    if plan.limits.max_records > UNIT_RECORDS or plan.limits.max_requests > PILOT_MAX_REQUESTS:
        raise ReconError(f"unit {unit['unit']}: plan limits exceed the unit/pilot bounds")
    if not plan.plan_hash or plan.plan_hash != plan.compute_behavioral_hash():
        raise ReconError(f"unit {unit['unit']}: plan hash missing or mismatched")


def _check_part(
    data: bytes, unit: Mapping[str, Any], execution: Mapping[str, Any], seen: set[tuple[str, int]]
) -> None:
    from xlm.data.acquisition.records import LOCATOR_FIELD

    lines = data.split(b"\n")
    if lines[-1] != b"":
        raise ReconError(f"unit {unit['unit']}: output does not end with a newline")
    lines = lines[:-1]
    if len(lines) != UNIT_RECORDS:
        raise ReconError(f"unit {unit['unit']}: {len(lines)} records, expected {UNIT_RECORDS}")
    start, stop = unit["row_range"]
    allowed = {*EXECUTION_PROJECTION, LOCATOR_FIELD}
    for number, line in enumerate(lines, start=1):
        try:
            record = json.loads(line)
        except ValueError as error:
            raise ReconError(f"unit {unit['unit']} line {number}: corrupt JSONL") from error
        if not isinstance(record, dict) or set(record) != allowed:
            keys = sorted(record) if isinstance(record, dict) else type(record).__name__
            raise ReconError(f"unit {unit['unit']} line {number}: fields {keys} != projection")
        loc = record[LOCATOR_FIELD]
        if not isinstance(loc, dict) or loc.get("source_file") != unit["file"]:
            raise ReconError(f"unit {unit['unit']} line {number}: wrong source file")
        if loc.get("revision") != execution["revision"] or (
            loc.get("repository") != execution["repository"]
        ):
            raise ReconError(f"unit {unit['unit']} line {number}: wrong revision/repository")
        row = loc.get("row_index")
        if type(row) is not int or not start <= row < stop:
            raise ReconError(f"unit {unit['unit']} line {number}: row {row!r} outside range")
        key = (unit["file"], row)
        if key in seen:
            raise ReconError(f"duplicate locator {key}")
        seen.add(key)


def combine(execution: Mapping[str, Any], root: Path) -> tuple[bytes, dict[str, Any]]:
    """Validate every unit, then concatenate parts byte-for-byte in stratum order."""
    from xlm.data.acquisition.plan import load_acquisition_plan
    from xlm.data.acquisition.progress import AcquisitionState

    units = execution["units"]
    if [u["stratum"] for u in units] != list(range(execution["strata"])):
        raise ReconError("execution units are not one per stratum in order")
    seen: set[tuple[str, int]] = set()
    hashes: set[str] = set()
    parts: list[bytes] = []
    receipts: list[dict[str, Any]] = []
    for unit in units:
        paths = unit["paths"]
        try:
            plan = load_acquisition_plan(root / paths["plan"])
        except (OSError, ValueError) as error:
            raise ReconError(f"unit {unit['unit']}: plan invalid: {error}") from error
        _check_plan(plan, unit, execution)
        if plan.plan_hash in hashes:
            raise ReconError(f"unit {unit['unit']}: duplicate plan identity")
        hashes.add(plan.plan_hash)
        journal = root / paths["scratch"] / "journals" / f"{plan.plan_id}.progress.json"
        if not journal.is_file() or journal.stat().st_size > MAX_JOURNAL_BYTES:
            raise ReconError(f"unit {unit['unit']}: fetch journal missing or oversized")
        state = AcquisitionState.model_validate_json(journal.read_bytes())
        if state.plan_id != plan.plan_id or state.plan_hash != plan.plan_hash:
            raise ReconError(f"unit {unit['unit']}: journal binds a different plan")
        if state.status != "COMPLETED" or set(state.file_progress) != {"selected_records.jsonl"}:
            raise ReconError(f"unit {unit['unit']}: fetch journal is not COMPLETED")
        progress = state.file_progress["selected_records.jsonl"]
        if state.records_acquired != UNIT_RECORDS or progress.status != "completed":
            raise ReconError(f"unit {unit['unit']}: {state.records_acquired} records acquired")
        output = root / paths["raw"] / "selected_records.jsonl"
        if not output.is_file() or output.stat().st_size > MAX_PART_BYTES:
            raise ReconError(f"unit {unit['unit']}: output missing or oversized")
        data = output.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if progress.content_sha256 != digest or progress.bytes_downloaded != len(data):
            raise ReconError(f"unit {unit['unit']}: output differs from its journal digest")
        _check_part(data, unit, execution, seen)
        parts.append(data)
        receipts.append(
            {
                "unit": unit["unit"],
                "stratum": unit["stratum"],
                "crawl": unit["crawl"],
                "file": unit["file"],
                "row_range": unit["row_range"],
                "plan_hash": plan.plan_hash,
                "records": UNIT_RECORDS,
                "bytes": len(data),
                "sha256": digest,
            }
        )
    combined = b"".join(parts)
    receipt: dict[str, Any] = {
        "kind": BUNDLE_KIND,
        "execution_digest": execution["digest"],
        "parent_discovery_digest": execution["parent_discovery_digest"],
        "revision": execution["revision"],
        "projection": list(EXECUTION_PROJECTION),
        "order": "stratum",
        "parts": receipts,
        "part_count": len(parts),
        "total_records": UNIT_RECORDS * len(parts),
        "combined_bytes": len(combined),
        "combined_sha256": hashlib.sha256(combined).hexdigest(),
    }
    receipt["digest"] = manifest_digest(receipt)
    return combined, receipt


# ---------------------------------------------------------------- CLI


def _discover(args: argparse.Namespace) -> int:
    if not args.live:
        raise ReconError(
            "discover lists the remote tree; pass --live explicitly (network OFF by default)"
        )
    revision = require_revision(args.revision)
    lister = HubTreeLister(revision, args.max_requests, args.max_bytes)
    manifest = build_manifest(lister, revision=revision, seed=args.seed, strata=args.strata)
    _atomic_write(
        args.out_dir / "discovery.json", json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    )
    _atomic_write(args.out_dir / "candidate_files.txt", "\n".join(manifest["files"]) + "\n")
    print(
        json.dumps(
            {
                "digest": manifest["digest"],
                "files": manifest["files"],
                "requests": lister.budget.requests_made,
            },
            indent=2,
        )
    )
    return 0


def _analyze(args: argparse.Namespace) -> int:
    manifest = load_manifest(args.manifest)
    candidates = None
    if args.candidates is not None:
        candidates = json.loads(args.candidates.read_text(encoding="utf-8"))
    result = analyze(read_records(args.records, manifest), manifest, candidates)
    _atomic_write(args.output_json, json.dumps(result, indent=2, sort_keys=True) + "\n")
    _atomic_write(args.output_md, render_markdown(result) + "\n")
    print(json.dumps({"records": result["records"], "json": str(args.output_json)}))
    return 0


def _execution(args: argparse.Namespace) -> int:
    manifest = load_manifest(args.discovery)
    execution = build_execution(manifest, args.root, args.expect_discovery_digest)
    text = json.dumps(execution, indent=2, sort_keys=True) + "\n"
    action = _write_or_adopt(args.output, text)
    print(
        json.dumps(
            {
                "execution": action,
                "digest": execution["digest"],
                "units": [u["unit"] for u in execution["units"]],
                "total_target_records": execution["total_target_records"],
            }
        )
    )
    return 0


def _combine(args: argparse.Namespace) -> int:
    execution = load_execution(args.execution)
    combined, receipt = combine(execution, args.root)
    if args.receipt.exists() and not args.output.exists():
        raise ReconError(f"{args.receipt} exists without its bundle; refusing")
    output = args.output
    if output.exists():
        if hashlib.sha256(output.read_bytes()).hexdigest() != receipt["combined_sha256"]:
            raise ReconError(f"{output} exists with different content; refusing to overwrite")
        action = "adopted"
    else:
        output.parent.mkdir(parents=True, exist_ok=True)
        temp = output.with_name(output.name + ".tmp")
        with temp.open("wb") as stream:
            stream.write(combined)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, output)
        action = "written"
    _write_or_adopt(args.receipt, json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    print(
        json.dumps(
            {
                "bundle": action,
                "records": receipt["total_records"],
                "sha256": receipt["combined_sha256"],
                "receipt": receipt["digest"],
            }
        )
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    sub = parser.add_subparsers(dest="command", required=True)
    d = sub.add_parser("discover", help="network metadata listing -> frozen recon manifest")
    d.add_argument("--revision", required=True)
    d.add_argument("--seed", type=int, default=DEFAULT_SEED)
    d.add_argument("--strata", type=int, default=DEFAULT_STRATA)
    d.add_argument("--out-dir", type=Path, required=True)
    d.add_argument("--live", action="store_true")
    d.add_argument("--max-requests", type=int, default=80)
    d.add_argument("--max-bytes", type=int, default=32 * 1024 * 1024)
    a = sub.add_parser("analyze", help="offline distributions over fetched metadata records")
    a.add_argument("--manifest", type=Path, required=True)
    a.add_argument("--records", type=Path, required=True)
    a.add_argument("--candidates", type=Path, default=None)
    a.add_argument("--output-json", type=Path, required=True)
    a.add_argument("--output-md", type=Path, required=True)
    e = sub.add_parser("execution", help="offline: bind discovery + footers -> execution-v2")
    e.add_argument("--discovery", type=Path, required=True)
    e.add_argument("--expect-discovery-digest", required=True)
    e.add_argument("--root", type=Path, required=True)
    e.add_argument("--output", type=Path, required=True)
    c = sub.add_parser("combine", help="offline: validate 8 fetched units -> one bundle")
    c.add_argument("--execution", type=Path, required=True)
    c.add_argument("--root", type=Path, required=True)
    c.add_argument("--output", type=Path, required=True)
    c.add_argument("--receipt", type=Path, required=True)
    sub.add_parser("projection", help="print the discovery-v1 projection (offline)")
    args = parser.parse_args(argv)
    try:
        if args.command == "discover":
            return _discover(args)
        if args.command == "analyze":
            return _analyze(args)
        if args.command == "execution":
            return _execution(args)
        if args.command == "combine":
            return _combine(args)
        print(",".join(recon_projection()))
        return 0
    except (ReconError, OSError, ValueError) as error:
        print(f"REFUSED: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
