"""Per-document lazy text features shared across cleaning stages.

Multiple filters scan the same text for overlapping characterizations
(word splits, line splits, character-class counts). Recomputing them in
every stage repeats O(text) passes over data that has not changed.

:class:`TextFeatures` is a single-entry lazy cache: each derived value is
computed at most once per distinct text object and shared by every stage
that observes that exact object. Invalidation is explicit and automatic:
normalization, HTML extraction, and boilerplate removal create a *new*
``str`` object whenever they change the text, and the cache keys on object
identity (``is``) while holding a strong reference to the key, so a
mutated text always misses and recomputes. A text that passes through
unchanged keeps the same object and hits.

Correctness contract (P27B-G):

- Every cached value is exactly the value the reference stage computes
  directly (same callables, same order-independent integer counts, same
  floating-point inputs). Sharing never changes a decision, a metric, or
  a reason string.
- Consumers must treat returned lists as read-only. Stages only measure
  (``len``, iteration, ``Counter`` copies) and never mutate them.
- One instance serves one document at a time in a single worker. It is
  not shared between processes or threads. Call :meth:`release` (or simply
  rebind on the next document) so a large document's word list does not
  outlive its processing.
"""

from __future__ import annotations


class CharStats:
    """Integer character-class counts for one text version.

    All counts are plain integers accumulated in a single pass; any ratio
    derived from them is therefore bit-identical to the same ratio derived
    from separately accumulated passes over the same characters.
    """

    __slots__ = ("alpha", "latin", "alnum", "non_ws", "symbols", "digits")

    def __init__(
        self,
        alpha: int = 0,
        latin: int = 0,
        alnum: int = 0,
        non_ws: int = 0,
        symbols: int = 0,
        digits: int = 0,
    ) -> None:
        self.alpha = alpha
        self.latin = latin
        self.alnum = alnum
        self.non_ws = non_ws
        self.symbols = symbols
        self.digits = digits


def compute_char_stats(text: str) -> CharStats:
    """Count character classes in one pass (exact reference definitions).

    - ``alpha``: ``c.isalpha()`` (language filter Latin/total accounting).
    - ``latin``: ``c.isascii() and c.isalpha()`` (same predicate, one pass).
    - ``non_ws``: ``not c.isspace()`` (noise filter denominator).
    - ``symbols``: non-whitespace and ``not c.isalnum()`` (noise numerator;
      digits are alphanumeric, so they never count as symbols).
    - ``digits``: non-whitespace and ``c.isdigit()`` (noise digit ratio).
    - ``alnum``: ``c.isalnum()`` over all characters, including whitespace
      positions evaluated the same way (length-filter density accounting
      evaluates every character; whitespace simply never satisfies it).
    """
    alpha = latin = alnum = non_ws = symbols = digits = 0
    for char in text:
        if char.isalpha():
            alpha += 1
            if char.isascii():
                latin += 1
        if char.isalnum():
            alnum += 1
        if not char.isspace():
            non_ws += 1
            if char.isdigit():
                digits += 1
            elif not char.isalnum():
                symbols += 1
    return CharStats(
        alpha=alpha, latin=latin, alnum=alnum, non_ws=non_ws, symbols=symbols, digits=digits
    )


class TextFeatures:
    """Single-entry lazy cache of derived values for one text version."""

    __slots__ = ("_text", "_words", "_lines", "_chars")

    def __init__(self) -> None:
        self._text: str | None = None
        self._words: list[str] | None = None
        self._lines: list[str] | None = None
        self._chars: CharStats | None = None

    def _bind(self, text: str) -> None:
        """Bind to ``text``, dropping values derived from any older version."""
        if text is not self._text:
            self._text = text
            self._words = None
            self._lines = None
            self._chars = None

    def words(self, text: str) -> list[str]:
        """Share ``text.split()`` across stages (read-only)."""
        self._bind(text)
        if self._words is None:
            assert self._text is not None
            self._words = self._text.split()
        return self._words

    def lines(self, text: str) -> list[str]:
        """Share ``text.splitlines()`` across stages (read-only)."""
        self._bind(text)
        if self._lines is None:
            assert self._text is not None
            self._lines = self._text.splitlines()
        return self._lines

    def chars(self, text: str) -> CharStats:
        """Share one-pass :func:`compute_char_stats` across stages."""
        self._bind(text)
        if self._chars is None:
            assert self._text is not None
            self._chars = compute_char_stats(self._text)
        return self._chars

    def release(self) -> None:
        """Drop all cached values (and the bound text) after a document."""
        self._text = None
        self._words = None
        self._lines = None
        self._chars = None
