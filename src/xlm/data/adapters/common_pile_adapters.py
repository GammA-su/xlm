"""Common Pile (``comma_v0.1_training_dataset``) adapter, row contract v2.

The pinned consolidated shards hold text-only rows: real certified rows of
news, libretexts and public_domain_review (9) and of oercommons, pressbooks and
project_gutenberg (48, certification ``common-pile-cert02``) are exactly
``{"text": <string>}``. No component, language, id or license field survives
consolidation, so provenance is the repository path and row, never a guessed
field: the canonical document carries the source file, the zero-based row,
the pinned revision and the top-level component of the path.

The v1 adapter in the frozen :mod:`xlm.data.adapters.mix01_adapters` fails the
whole file on a blank string ``text``. Exactly one branch changes here,
consistent with the IFM v2 contract and the shared content policy of
:func:`~xlm.data.adapters.mix01_adapters._require_recordable_content_text`:

- ``text`` is a string that is empty or whitespace-only: a recorded
  :class:`CommonPileEmptyTextError` (stable code ``CommonPileEmptyTextError``);
  it is row content quality, not a schema fault.

Everything else is v1, unchanged and delegated, so an accepted row's document
is byte-identical to v1's: ``text`` absent, null or not a string is a fatal
:class:`~xlm.data.adapters.mix01_adapters.MissingFieldError` (schema faults; no
real row was ever observed that way, so no other policy is supported), and
valid text is preserved verbatim. A componentless or unsafe source path is
refused fail-closed by the v1 component derivation.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from xlm.core.contracts import CanonicalDocument
from xlm.data.adapters import mix01_adapters as v1
from xlm.data.adapters.mix01_adapters import RecordRejectedError


class CommonPileEmptyTextError(RecordRejectedError):
    """A Common Pile row whose string ``text`` is empty or whitespace-only."""


class CommonPileAdapter(v1.CommonPileAdapter):
    """Common Pile prose rows under row contract v2 (see the module docstring)."""

    def adapt(
        self,
        record: Mapping[str, Any],
        *,
        source_file: str,
        source_row: int,
        source_revision: str,
    ) -> CanonicalDocument:
        text = record.get("text")
        if isinstance(text, str) and not text.strip():
            # The path is still checked first, so a bad locator is never hidden.
            component = self.upstream_component(source_file)
            kind = "empty" if not text else "whitespace-only"
            raise CommonPileEmptyTextError(
                f"adapter '{self.ADAPTER_ID}' records a {component} row whose 'text' is {kind} "
                f"({len(text)} characters); it has no training content."
            )
        return super().adapt(
            record, source_file=source_file, source_row=source_row, source_revision=source_revision
        )


ADAPTERS_BY_ID = {CommonPileAdapter.ADAPTER_ID: CommonPileAdapter}
