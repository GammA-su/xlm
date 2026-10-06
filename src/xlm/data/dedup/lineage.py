"""Lineage grouping keys that keep known document families together.

Contract C05 requires known derivatives -- source document and page IDs, book
chapters, conversation turns, URL variants and synthetic examples sharing a seed --
to stay in one lineage group, so that a split cannot place two members of the same
family on opposite sides.

Lineage grouping is declarative: it uses the metadata the adapter actually recorded.
Where no family metadata exists, a document forms its own singleton group. That is an
honest "unknown", not a claim that the document has no relatives.
"""

from __future__ import annotations

import re
from dataclasses import replace
from urllib.parse import urlsplit, urlunsplit

from xlm.core.contracts import CanonicalDocument

LINEAGE_RULE_VERSION = "1"
LINEAGE_V2 = "known-lineage-v2"

# Metadata keys inspected, in priority order. The first present, non-empty key wins,
# so an explicit lineage_id always overrides a derived one.
_LINEAGE_KEYS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("explicit", ("lineage_id", "lineage_group")),
    ("synthetic_seed", ("synthetic_seed", "seed")),
    ("conversation", ("conversation_id", "thread_id", "dialogue_id")),
    ("book", ("book_id", "volume_id")),
    ("document", ("source_document_id", "document_id", "article_id", "page_id")),
    ("url", ("url", "source_url", "canonical_url")),
)

# Tracking and versioning query parameters that create spurious URL variants.
_VOLATILE_QUERY_PREFIXES = ("utm_", "ref", "fbclid", "gclid", "session", "sid")
_WWW_PREFIX = re.compile(r"^www\.")


def canonical_url(raw: str) -> str:
    """Reduce a URL to a variant-insensitive form.

    Scheme, ``www.`` prefix, fragment, trailing slash and tracking parameters are
    dropped so that http/https, with/without ``www`` and tracking-tagged variants of
    the same article collapse to one lineage key.
    """
    try:
        parts = urlsplit(raw.strip())
    except ValueError:
        return raw.strip().casefold()

    host = _WWW_PREFIX.sub("", parts.netloc.casefold())
    path = parts.path.rstrip("/") or "/"

    kept_params = []
    if parts.query:
        for item in parts.query.split("&"):
            if not item:
                continue
            name = item.split("=", 1)[0].casefold()
            if any(name.startswith(prefix) for prefix in _VOLATILE_QUERY_PREFIXES):
                continue
            kept_params.append(item)

    return urlunsplit(("", host, path, "&".join(sorted(kept_params)), ""))


def lineage_key(doc: CanonicalDocument) -> tuple[str, str]:
    """Return ``(rule_name, lineage_key)`` for ``doc``.

    A book chapter contributes only its book ID, and a conversation turn only its
    conversation ID, so every chapter or turn of the same work shares a group.
    """
    metadata = doc.source_metadata

    for rule_name, candidate_keys in _LINEAGE_KEYS:
        for key in candidate_keys:
            value = metadata.get(key)
            if value is None:
                continue
            text = str(value).strip()
            if not text:
                continue
            if rule_name == "url":
                return rule_name, f"url:{canonical_url(text)}"
            return rule_name, f"{rule_name}:{doc.source_id}:{text}"

    # Explicit parent links are the last structured signal before falling back.
    if doc.parent_ids:
        return "parent", f"parent:{doc.source_id}:{sorted(doc.parent_ids)[0]}"

    return "singleton", f"doc:{doc.source_id}:{doc.doc_id}"


def lineage_keys_v2(doc: CanonicalDocument) -> tuple[str, ...]:
    """All known links, including SYNTH seed URLs and every explicit parent.

    V1 remains available for historical freezes. No shard adjacency, synth_id or
    fabricated book identity participates. URL keys intentionally cross sources.
    """
    keys = {f"parent-doc:{doc.doc_id}"}
    keys.update(f"parent-doc:{p}" for p in doc.parent_ids if p)
    for rule, names in _LINEAGE_KEYS:
        for name in names:
            value = doc.source_metadata.get(name)
            if isinstance(value, bool) or not isinstance(value, (str, int)):
                continue
            text = str(value).strip()
            if text:
                keys.add(
                    f"url:{canonical_url(text)}"
                    if rule == "url"
                    else f"{rule}:{doc.source_id}:{text}"
                )
    if doc.source_id == "synth":
        for name in ("query_seed_url", "additional_seed_url"):
            value = doc.source_metadata.get(name)
            if isinstance(value, str) and value.strip():
                parsed = urlsplit(value)
                if parsed.scheme.lower() in ("http", "https") and parsed.netloc:
                    keys.add(f"url:{canonical_url(value)}")
    for name, value in doc.cluster_ids.items():
        if name in ("duplicate_cluster", "split_group") and value:
            keys.add(f"cluster:{name}:{value}")
    return tuple(sorted(keys))


def lineage_keys_v3(doc: CanonicalDocument) -> tuple[str, ...]:
    """Revision-bound metadata/unknown parents; canonical URLs still bridge sources.

    Actual cross-source parent document references are resolved by the disk engine.
    Missing parent labels must not accidentally bridge unrelated publishers.
    """
    namespace = f"{doc.source_id}:{doc.source_revision}"
    keys = {f"parent:{namespace}:{doc.doc_id}"}
    keys.update(f"parent:{namespace}:{p}" for p in doc.parent_ids if p)
    for key in lineage_keys_v2(doc):
        if key.startswith("url:"):
            keys.add(key)
        elif not key.startswith("parent-doc:"):
            keys.add(f"metadata:{namespace}:{key}")
    return tuple(sorted(keys))


ADDITIONAL_SEED_URL = "additional_seed_url"


def split_only_keys_v1(doc: CanonicalDocument) -> frozenset[str]:
    """known-lineage-v3 keys that ONLY a SYNTH row's ``additional_seed_url`` contributes.

    ``query-seed-derivation-family-v1`` keeps them for split-leakage grouping but never
    lets them merge contamination families. A key that another field also produces
    (e.g. the same URL as ``query_seed_url``) is not split-only.
    """
    if doc.source_id != "synth" or ADDITIONAL_SEED_URL not in doc.source_metadata:
        return frozenset()
    metadata = {k: v for k, v in doc.source_metadata.items() if k != ADDITIONAL_SEED_URL}
    reduced = lineage_keys_v3(replace(doc, source_metadata=metadata))
    return frozenset(lineage_keys_v3(doc)) - frozenset(reduced)
