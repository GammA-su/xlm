"""Aggressively normalized match view used only for duplicate and exclusion detection.

Contract C03 permits a separate normalized view for duplicate and exclusion
detection, distinct from the canonical model text. Nothing in this module is ever
written back into a :class:`CanonicalDocument`'s ``text``: canonical text keeps its
case, punctuation, mathematical notation and code indentation.

The view is versioned. Changing it changes duplicate decisions, so the version is
part of every dedup and exclusion artifact identity.
"""

from __future__ import annotations

import re
import unicodedata

MATCH_VIEW_VERSION = "1"

# Characters kept in the match view: letters, digits and single spaces. Punctuation
# is dropped so that quoting and light reformatting do not defeat duplicate matching.
_NON_ALNUM = re.compile(r"[^\w\s]", flags=re.UNICODE)
_WHITESPACE = re.compile(r"\s+", flags=re.UNICODE)


def match_normalize(text: str) -> str:
    """Return the normalized matching view of ``text``.

    Applies NFKC folding, case folding, punctuation removal and whitespace
    collapsing. This is deliberately lossy and is unsuitable for training text.
    """
    if not text:
        return ""
    folded = unicodedata.normalize("NFKC", text).casefold()
    stripped = _NON_ALNUM.sub(" ", folded)
    return _WHITESPACE.sub(" ", stripped).strip()


def match_tokens(text: str) -> list[str]:
    """Tokenize the match view into whitespace-delimited tokens."""
    normalized = match_normalize(text)
    return normalized.split(" ") if normalized else []
