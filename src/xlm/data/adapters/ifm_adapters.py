"""IFM Pretrain-Behaviors adapters, row contract v2: isolated empty ``text`` is recorded.

The v1 adapters in :mod:`xlm.data.adapters.mix01_adapters` fail the whole file
on any row whose ``text`` is a blank string. Their module is frozen (its bytes
are bound by every certified bridge and by the Essential-Web campaign), so the
corrected contract lives here and is registered by
:mod:`xlm.data.adapters.registry`.

Exactly one v1 branch changes:

- ``text`` is a string that is empty or whitespace-only: a recorded
  :class:`IfmEmptyTextError` (stable code ``IfmEmptyTextError``), raised only
  after ``token_count`` passes its v1 check, so a malformed row is never hidden
  behind a recordable rejection. Real evidence: retained General shard
  ``general_full.chunk0-bdbff8a5c6-00069`` holds exactly 2 such rows in
  329,409 (both exact-empty, both declaring ``token_count`` 0); footer
  statistics show the same ``token_count`` minimum of 0 in two row groups of
  the selected Planning file ``planning.chunk1-6d580bf230-00295``.

Everything else is v1, unchanged and delegated: ``text`` absent, null or not a
string, and a present ``token_count`` that is not a non-negative integer, stay
fatal :class:`MissingFieldError` (schema faults; null was never observed). An
accepted row is built by the v1 ``adapt`` itself, so its document is
byte-identical to v1's.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from xlm.core.contracts import CanonicalDocument
from xlm.data.adapters import mix01_adapters as v1
from xlm.data.adapters.mix01_adapters import RecordRejectedError


class IfmEmptyTextError(RecordRejectedError):
    """An IFM row whose string ``text`` is empty or whitespace-only (no training content)."""


def reject_empty_ifm_text(record: Mapping[str, Any], adapter_id: str) -> None:
    """Shared IFM v2 check, run before the v1 adapter; raises only for blank string text.

    The reason names the blank kind, the character count and the declared
    ``token_count``; it never carries row content.
    """
    text = record.get("text")
    if not isinstance(text, str) or text.strip():
        return
    token_count = v1._optional_upstream_token_count(record, adapter_id)
    kind = "empty" if not text else "whitespace-only"
    raise IfmEmptyTextError(
        f"adapter '{adapter_id}' records a row whose upstream 'text' is {kind} "
        f"({len(text)} characters, declared token_count {token_count}); "
        "it has no training content."
    )


class IfmGeneralAdapter(v1.IfmGeneralAdapter):
    """IFM ``general`` subset under row contract v2 (see the module docstring)."""

    def adapt(
        self,
        record: Mapping[str, Any],
        *,
        source_file: str,
        source_row: int,
        source_revision: str,
    ) -> CanonicalDocument:
        reject_empty_ifm_text(record, self.ADAPTER_ID)
        return super().adapt(
            record, source_file=source_file, source_row=source_row, source_revision=source_revision
        )


class IfmPlanningAdapter(v1.IfmPlanningAdapter):
    """IFM ``planning`` subset under row contract v2 (see the module docstring)."""

    def adapt(
        self,
        record: Mapping[str, Any],
        *,
        source_file: str,
        source_row: int,
        source_revision: str,
    ) -> CanonicalDocument:
        reject_empty_ifm_text(record, self.ADAPTER_ID)
        return super().adapt(
            record, source_file=source_file, source_row=source_row, source_revision=source_revision
        )


ADAPTERS_BY_ID = {
    IfmGeneralAdapter.ADAPTER_ID: IfmGeneralAdapter,
    IfmPlanningAdapter.ADAPTER_ID: IfmPlanningAdapter,
}
