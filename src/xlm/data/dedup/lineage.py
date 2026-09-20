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
from urllib.parse import urlsplit, urlunsplit

from xlm.core.contracts import CanonicalDocument

LINEAGE_RULE_VERSION = "1"

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
