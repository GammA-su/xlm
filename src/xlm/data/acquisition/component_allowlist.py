"""Write-once operator allowlists of a repository's top-level components (offline).

Some Mix-01 sources consolidate many independently licensed upstream corpora
in one repository, one top-level directory per corpus (Common Pile: 31). Such a
source has no single license, so production may only ever see the components
an operator explicitly allowed. This module freezes that decision:

- the component universe is derived from a verified, complete metadata listing
  (exact first path segment of every listed file; no filename guessing);
- every universe component carries the evidence-matrix entry it was decided
  on (content fit, license class, provenance confidence, evidence sources);
- an included component that the matrix flags (license class outside the open
  classes, or content fit outside FIT/PARTIAL_FIT) must be named again by the
  operator in ``accepted_flags``; a stale or extra acceptance is refused;
- the record is self-digested and binds the listing digest, so it can only
  filter the listing it was decided against.

The production inventory is the discovery listing filtered to the included
components, frozen with the same seed and revision; its digest binds this
record's digest (``mix01_inventory.py freeze --allowlist``). Planning verifies
that binding on every plan, so an excluded component cannot enter a later
top-up. Nothing here downloads, admits, plans or authorizes.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from xlm.data.evidence_v2 import canonical
from xlm.data.sources import hf_inventory

ALLOWLIST_KIND = "mix01_component_allowlist"
ALLOWLIST_VERSION = 1
MATRIX_KIND = "mix01_component_evidence_matrix"
MATRIX_VERSION = 1

#: License classes that never need a separate operator acceptance.
OPEN_LICENSE_CLASSES = frozenset(
    {
        "VERIFIED_PUBLIC_DOMAIN",
        "VERIFIED_OPEN_LICENSE",
        "PER_DOCUMENT_OPEN_LICENSE",
        "MIXED_BUT_FILTERED_TO_OPEN",
    }
)
LICENSE_CLASSES = OPEN_LICENSE_CLASSES | {
    "INSUFFICIENT_EVIDENCE",
    "EXCLUDE_PROVENANCE",
    "NEEDS_OPERATOR_REVIEW",
}
#: Content-fit classes that never need a separate operator acceptance.
FITTING_CLASSES = frozenset({"FIT", "PARTIAL_FIT"})
FIT_CLASSES = FITTING_CLASSES | {"EXCLUDE_CONTENT_FIT", "NEEDS_OPERATOR_REVIEW"}
CONFIDENCE = frozenset({"HIGH", "MEDIUM", "LOW"})
MATRIX_FIELDS = ("content_fit", "license_class", "provenance_confidence", "evidence")


class AllowlistError(ValueError):
    """An allowlist, its evidence or a bound inventory does not verify; nothing is written."""


def component_of(path: str) -> str:
    """The exact first segment of a repository-relative file path (fail closed)."""
    if not isinstance(path, str) or not path or "\\" in path or path.startswith("/"):
        raise AllowlistError(f"not a repository-relative file path: {path!r}")
    segments = path.split("/")
    if len(segments) < 2 or any(s in ("", ".", "..") for s in segments):
        raise AllowlistError(f"file path names no top-level component: {path!r}")
    return segments[0]


def listing_universe(listing: Mapping[str, Any]) -> dict[str, dict[str, int]]:
    """Files and declared bytes per top-level component of a verified complete listing."""
    try:
        hf_inventory.verify_listing(dict(listing))
    except hf_inventory.HfInventoryError as exc:
        raise AllowlistError(f"listing does not verify: {exc}") from exc
    if (
        listing.get("completion_status") != "complete"
        or listing.get("pagination_complete") is not True
    ):
        raise AllowlistError("listing is not complete; a component universe needs every file")
    if listing.get("unknown_size_count") != 0:
        raise AllowlistError("listing has files of unknown size")
    universe: dict[str, dict[str, int]] = {}
    for entry in listing["files"]:
        bucket = universe.setdefault(component_of(str(entry["path"])), {"files": 0, "bytes": 0})
        bucket["files"] += 1
        bucket["bytes"] += int(entry["size_bytes"])
    if not universe:
        raise AllowlistError("listing holds no component")
    return dict(sorted(universe.items()))


def check_matrix(matrix: Mapping[str, Any], repository: str, revision: str) -> None:
    """Validate an evidence matrix's shape and its binding to one repository revision."""
    if matrix.get("kind") != MATRIX_KIND or matrix.get("version") != MATRIX_VERSION:
        raise AllowlistError("not a version-1 component evidence matrix")
    if (matrix.get("repository"), matrix.get("revision")) != (repository, revision):
        raise AllowlistError("evidence matrix is for another repository or revision")
    sources = matrix.get("sources")
    components = matrix.get("components")
    if not isinstance(sources, dict) or not sources:
        raise AllowlistError("evidence matrix lists no evidence sources")
    if not isinstance(components, dict) or not components:
        raise AllowlistError("evidence matrix lists no components")
    for name, source in sources.items():
        if not isinstance(source, dict) or not str(source.get("url") or "").strip():
            raise AllowlistError(f"evidence source '{name}' has no url")
    for name, entry in components.items():
        if not isinstance(entry, dict) or any(f not in entry for f in MATRIX_FIELDS):
            raise AllowlistError(f"evidence for '{name}' lacks one of {MATRIX_FIELDS}")
        if entry["content_fit"] not in FIT_CLASSES:
            raise AllowlistError(f"'{name}' has unknown content fit {entry['content_fit']!r}")
        if entry["license_class"] not in LICENSE_CLASSES:
            raise AllowlistError(f"'{name}' has unknown license class {entry['license_class']!r}")
        if entry["provenance_confidence"] not in CONFIDENCE:
            raise AllowlistError(f"'{name}' has unknown provenance confidence")
        refs = entry["evidence"]
        if not isinstance(refs, list) or not refs or any(r not in sources for r in refs):
            raise AllowlistError(f"evidence for '{name}' cites no or unknown sources")


def flagged(entry: Mapping[str, Any]) -> bool:
    """Whether including this component needs an explicit operator acceptance."""
    return (
        entry["license_class"] not in OPEN_LICENSE_CLASSES
        or entry["content_fit"] not in FITTING_CLASSES
    )


def _names(values: Sequence[str], what: str) -> list[str]:
    cleaned = [str(v).strip() for v in values]
    if any(not v for v in cleaned) or len(set(cleaned)) != len(cleaned):
        raise AllowlistError(f"{what} must be distinct non-empty component names")
    return sorted(cleaned)


def build_allowlist(
    *,
    source_key: str,
    component_id: str,
    listing: Mapping[str, Any],
    matrix: Mapping[str, Any],
    matrix_sha256: str,
    included: Sequence[str],
    accepted_flags: Sequence[str],
    operator: str,
    rationale: str,
) -> dict[str, Any]:
    """Freeze an operator's exact component allowlist against one listing."""
    if not source_key.strip() or not component_id.strip():
        raise AllowlistError("a source key and a Mix-01 component id are required")
    if not operator.strip() or not rationale.strip():
        raise AllowlistError("a named operator and a written rationale are required")
    universe = listing_universe(listing)
    repository = str(listing["repository"])
    revision = str(listing["resolved_revision"])
    check_matrix(matrix, repository, revision)
    evidence: dict[str, Any] = dict(matrix["components"])
    if sorted(evidence) != sorted(universe):
        missing = sorted(set(universe) - set(evidence))
        unknown = sorted(set(evidence) - set(universe))
        raise AllowlistError(
            f"evidence matrix does not cover exactly the listed components "
            f"(missing {missing}, not listed {unknown})"
        )
    chosen = _names(included, "included components")
    if not chosen:
        raise AllowlistError("an allowlist includes at least one component")
    outside = [c for c in chosen if c not in universe]
    if outside:
        raise AllowlistError(f"components not in the listed universe: {outside}")
    accepted = _names(accepted_flags, "accepted flags")
    needed = [c for c in chosen if flagged(evidence[c])]
    if accepted != needed:
        raise AllowlistError(
            "accepted flags must name exactly the included components the evidence flags: "
            f"flagged {needed}, accepted {accepted}"
        )
    if not isinstance(matrix_sha256, str) or len(matrix_sha256) != 64:
        raise AllowlistError("the evidence matrix sha256 is required")
    cited = sorted({ref for entry in evidence.values() for ref in entry["evidence"]})
    body: dict[str, Any] = {
        "kind": ALLOWLIST_KIND,
        "version": ALLOWLIST_VERSION,
        "source_key": source_key.strip(),
        "component_id": component_id.strip(),
        "source_id": str(listing["source_id"]),
        "repository": repository,
        "revision": revision,
        "listing": {
            "digest": str(listing["digest"]),
            "file_list_digest": str(listing["file_list_digest"]),
            "item_count": int(listing["item_count"]),
            "total_declared_bytes": int(listing["total_declared_bytes"]),
        },
        "evidence_matrix": {"sha256": matrix_sha256, "audit": str(matrix.get("audit") or "")},
        "universe": universe,
        "included": chosen,
        "excluded": [c for c in universe if c not in chosen],
        "evidence": {c: {f: evidence[c][f] for f in MATRIX_FIELDS} for c in sorted(evidence)},
        "evidence_sources": {ref: dict(matrix["sources"][ref]) for ref in cited},
        "accepted_flags": accepted,
        "operator": operator.strip(),
        "rationale": rationale.strip(),
    }
    body["digest"] = canonical.digest(body)
    return body


def check_allowlist(record: Mapping[str, Any], listing: Mapping[str, Any]) -> dict[str, Any]:
    """Verify a stored allowlist and that it was decided against exactly this listing."""
    if record.get("kind") != ALLOWLIST_KIND or record.get("version") != ALLOWLIST_VERSION:
        raise AllowlistError("not a version-1 component allowlist")
    body = dict(record)
    if body.pop("digest", None) != canonical.digest(body):
        raise AllowlistError("component allowlist digest does not verify")
    universe = listing_universe(listing)
    bound = record["listing"]
    if (
        bound.get("digest") != listing["digest"]
        or bound.get("file_list_digest") != listing["file_list_digest"]
        or (record["repository"], record["revision"], record["source_id"])
        != (listing["repository"], listing["resolved_revision"], listing["source_id"])
    ):
        raise AllowlistError("component allowlist was decided against another listing")
    if record["universe"] != universe:
        raise AllowlistError("component allowlist universe differs from the listing")
    included, excluded = list(record["included"]), list(record["excluded"])
    if (
        not included
        or included != sorted(set(included))
        or sorted(included + excluded) != list(universe)
    ):
        raise AllowlistError("included and excluded components do not partition the universe")
    evidence = record["evidence"]
    if sorted(evidence) != list(universe):
        raise AllowlistError("component allowlist evidence does not cover the universe")
    if record["accepted_flags"] != [c for c in included if flagged(evidence[c])]:
        raise AllowlistError("accepted flags differ from the flagged included components")
    return dict(record)


def filtered_freeze_inputs(
    record: Mapping[str, Any], listing: Mapping[str, Any]
) -> tuple[list[str], dict[str, int]]:
    """Exactly the listed files of the included components, with their sizes."""
    check_allowlist(record, listing)
    allowed = set(record["included"])
    names: list[str] = []
    sizes: dict[str, int] = {}
    for entry in listing["files"]:
        path = str(entry["path"])
        if component_of(path) in allowed:
            names.append(path)
            sizes[path] = int(entry["size_bytes"])
    if not names:
        raise AllowlistError("the allowlist selects no listed file")
    return names, sizes


def inventory_binding(record: Mapping[str, Any]) -> dict[str, Any]:
    """The allowlist block a filtered inventory carries (and its digest binds)."""
    return {
        "digest": str(record["digest"]),
        "included": list(record["included"]),
        "excluded": list(record["excluded"]),
    }


def check_inventory_binding(
    record: Mapping[str, Any], listing: Mapping[str, Any], inventory: Mapping[str, Any]
) -> None:
    """Refuse an inventory that is not this allowlist's exact filtered listing."""
    _, sizes = filtered_freeze_inputs(record, listing)
    if inventory.get("component_allowlist") != inventory_binding(record):
        raise AllowlistError("inventory does not bind this component allowlist")
    if (inventory.get("repository"), inventory.get("revision"), inventory.get("source_id")) != (
        record["repository"],
        record["revision"],
        record["source_id"],
    ):
        raise AllowlistError("inventory belongs to another source, repository or revision")
    entries = inventory.get("files")
    if not isinstance(entries, list):
        raise AllowlistError("inventory has no file list")
    held = {str(e["file"]): e.get("size_bytes") for e in entries}
    if len(held) != len(entries) or held != sizes:
        raise AllowlistError(
            "inventory files differ from the allowlisted listing files (missing, extra or resized)"
        )
    allowed = set(record["included"])
    if any(component_of(name) not in allowed for name in held):
        raise AllowlistError("inventory holds a file of an excluded component")


def read_json(path: Path) -> dict[str, Any]:
    """A JSON object from disk; refuses anything else."""
    try:
        value = json.loads(path.read_bytes().decode("utf-8"))
    except (OSError, ValueError) as exc:
        raise AllowlistError(f"cannot read '{path}': {exc}") from exc
    if not isinstance(value, dict):
        raise AllowlistError(f"'{path}' is not a JSON object")
    return value


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_once(path: Path, record: Mapping[str, Any]) -> bool:
    """Atomically store a record; True if written, False if identical; divergent refused."""
    rendered = (json.dumps(record, indent=2, sort_keys=True) + "\n").encode("utf-8")
    if path.exists():
        if path.read_bytes() == rendered:
            return False
        raise AllowlistError(f"a different allowlist is already recorded at {path}; refusing")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(rendered)
    tmp.replace(path)
    return True
