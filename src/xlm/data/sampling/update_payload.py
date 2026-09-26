"""Canonical global-update payload receipt (``global_update_payload_digest_v1``).

The fairness question for accumulation grouping (B8/B16/B32) is whether two
runs gave the optimizer the *same global update*, however it was split into
microbatches. This module canonicalizes one optimizer update's payload before
grouping can affect its identity:

* every per-sequence field the batcher hands the model or objective is
  flattened across microbatches, in the batcher's own global sequence order,
  into one ``[N, T]`` (``[N, T, 2]`` for byte spans) array. Microbatch
  boundaries, per-microbatch counts and container types never enter the
  digest; sequences are never sorted;
* integer fields are hashed as little-endian int64 with their shape; the
  attention mask as its boolean meaning (the trainer casts it to ``bool``);
* string fields (source, document, lineage) are encoded against a table in
  first-occurrence order over the flattened global field, so the encoding is
  a function of the global ordered payload only;
* fields that the batcher does not provide are *absent*, and the header lists
  exactly which fields are bound. Nothing is invented.

Bound fields, where represented (``MixtureBatcher`` and the P34 producer bind
all of them): input ids, labels, loss/valid-target mask, position ids, segment
ids, input attention mask, packing mode, per-position source attribution,
per-target document id, lineage id, canonical byte span and ordered-stream
token offset. The header binds the sequence count, context length and the
number of valid targets.

Hashing runs on the CPU representation that already exists before device
transfer (Python lists, CPU tensors or the producer's NumPy arrays); a device
tensor is refused rather than synchronized.

The receipt chain (``xlm-update-payload-chain-v1``) folds, per committed
update, ``(step, committed_before, valid_targets, payload_digest)`` onto the
previous chain digest from a run-independent genesis, so runs that differ only
in grouping have identical chains. Updates are *staged* before compute and
*committed* only after the optimizer update and data cursor commit; a failed
or in-doubt update is discarded and never enters the chain.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from xlm.artifacts.manifest import canonical_json, identity_digest

PAYLOAD_VERSION = "global_update_payload_digest_v1"
CHAIN_VERSION = "xlm-update-payload-chain-v1"
CHAIN_COLUMNS = ("step", "committed_before", "valid_targets", "payload_digest", "chain_digest")
CHAIN_GENESIS = identity_digest({"version": CHAIN_VERSION, "genesis": PAYLOAD_VERSION})
MAX_CHAIN_ROWS = 1_000_000
#: Integer fields in hash order; ``attention_mask`` is hashed as its boolean meaning.
INT_FIELDS = (
    "input_ids",
    "labels",
    "loss_mask",
    "position_ids",
    "segment_ids",
    "attention_mask",
    "byte_spans",
    "token_offsets",
)
STRING_FIELDS = ("source_attribution", "doc_ids", "lineage_ids")
_LE_INT64 = np.dtype("<i8")


class PayloadReceiptError(ValueError):
    """An update payload cannot be canonicalized, or a chain is inconsistent."""


def _int_array(value: Any, name: str) -> np.ndarray:
    if hasattr(value, "detach") and hasattr(value, "device"):  # a torch tensor
        if getattr(value.device, "type", "cpu") != "cpu":
            raise PayloadReceiptError(
                f"{name} is on {value.device}; the payload receipt hashes the CPU "
                "representation before transfer and never synchronizes a device"
            )
        value = value.detach().numpy()
    array = np.asarray(value)
    if array.dtype.kind not in "iub":
        raise PayloadReceiptError(f"{name} is not an integer/boolean field ({array.dtype})")
    return array.astype(_LE_INT64, copy=False)


def _string_codes(rows: Sequence[Any], name: str) -> tuple[list[str], np.ndarray]:
    """First-occurrence codes; a per-sequence string (not a per-position list) is one column."""
    per_sequence = [isinstance(row, str) for row in rows]
    if any(per_sequence) and not all(per_sequence):
        raise PayloadReceiptError(f"{name} mixes per-sequence and per-position values")
    if rows and all(per_sequence):
        rows = [[row] for row in rows]
    table: dict[str, int] = {}
    codes: list[list[int]] = []
    for row in rows:
        encoded: list[int] = []
        for value in row:
            if type(value) is not str:
                raise PayloadReceiptError(f"{name} contains a non-string value")
            encoded.append(table.setdefault(value, len(table)))
        codes.append(encoded)
    return list(table), np.asarray(codes, dtype=_LE_INT64)


def _table_codes(codes: np.ndarray, strings: Sequence[str]) -> tuple[list[str], np.ndarray]:
    """Re-encode codes into a shared table as first-occurrence codes of this field alone."""
    flat = np.asarray(codes).reshape(-1)
    if flat.size == 0:
        return [], np.zeros(np.asarray(codes).shape, dtype=_LE_INT64)
    unique, first, inverse = np.unique(flat, return_index=True, return_inverse=True)
    order = np.argsort(first, kind="stable")
    rank = np.empty(len(order), dtype=_LE_INT64)
    rank[order] = np.arange(len(order), dtype=_LE_INT64)
    table = [strings[int(u)] for u in unique[order]]
    return table, rank[inverse].reshape(np.asarray(codes).shape)


@dataclass(frozen=True)
class CanonicalUpdate:
    """One global update's payload in canonical, grouping-independent form."""

    sequences: int
    context: int
    packing_mode: str | None
    arrays: Mapping[str, np.ndarray]
    strings: Mapping[str, tuple[list[str], np.ndarray]]

    @property
    def valid_targets(self) -> int:
        return int(np.count_nonzero(self.arrays["loss_mask"]))

    def header(self) -> dict[str, Any]:
        return {
            "version": PAYLOAD_VERSION,
            "sequences": self.sequences,
            "context": self.context,
            "packing_mode": self.packing_mode,
            "valid_targets": self.valid_targets,
            "fields": [f for f in INT_FIELDS if f in self.arrays]
            + [f for f in STRING_FIELDS if f in self.strings],
        }

    def digest(self) -> str:
        digest = hashlib.sha256(PAYLOAD_VERSION.encode() + b"\0")
        digest.update(canonical_json(self.header()))
        for name in INT_FIELDS:
            if name not in self.arrays:
                continue
            array = np.ascontiguousarray(self.arrays[name], dtype=_LE_INT64)
            digest.update(
                canonical_json({"field": name, "dtype": "<i8", "shape": list(array.shape)})
            )
            digest.update(array.tobytes(order="C"))
        for name in STRING_FIELDS:
            if name not in self.strings:
                continue
            table, codes = self.strings[name]
            codes = np.ascontiguousarray(codes, dtype=_LE_INT64)
            digest.update(
                canonical_json(
                    {"field": name, "table": table, "dtype": "<i8", "shape": list(codes.shape)}
                )
            )
            digest.update(codes.tobytes(order="C"))
        return digest.hexdigest()


def _build(
    sequences: int,
    packing_mode: str | None,
    arrays: dict[str, np.ndarray],
    strings: dict[str, tuple[list[str], np.ndarray]],
) -> CanonicalUpdate:
    for required in ("input_ids", "labels", "loss_mask"):
        if required not in arrays:
            raise PayloadReceiptError(f"an update payload needs {required}")
    shape = arrays["input_ids"].shape
    if len(shape) != 2 or shape[0] != sequences:
        raise PayloadReceiptError(f"input_ids must be [sequences, context], got {shape}")
    for name, array in arrays.items():
        expected = (*shape, 2) if name == "byte_spans" else shape
        if array.shape != expected:
            raise PayloadReceiptError(f"{name} has shape {array.shape}, expected {expected}")
    for name, (_, codes) in strings.items():
        # Per position ([N, T]) or, when a batcher attributes whole sequences, [N, 1].
        if codes.shape not in (shape, (shape[0], 1)):
            raise PayloadReceiptError(f"{name} has shape {codes.shape}, expected {shape}")
    if "attention_mask" in arrays:
        arrays["attention_mask"] = (arrays["attention_mask"] != 0).astype(_LE_INT64)
    return CanonicalUpdate(int(shape[0]), int(shape[1]), packing_mode, arrays, strings)


def _packing_mode(metadatas: Sequence[Mapping[str, Any]]) -> str | None:
    modes = {m.get("packing_mode") for m in metadatas}
    if len(modes) != 1:
        raise PayloadReceiptError(
            f"microbatches disagree on packing mode: {sorted(map(str, modes))}"
        )
    mode = modes.pop()
    return None if mode is None else str(mode)


def canonical_from_microbatches(microbatches: Sequence[Any]) -> CanonicalUpdate:
    """Canonicalize ``TrainingBatch`` microbatches (list mode or CPU tensors)."""
    if not microbatches:
        raise PayloadReceiptError("an update needs at least one microbatch")
    getters: dict[str, Any] = {
        "input_ids": lambda mb: mb.input_ids,
        "labels": lambda mb: mb.labels,
        "loss_mask": lambda mb: mb.loss_mask,
        "position_ids": lambda mb: mb.position_ids,
        "segment_ids": lambda mb: mb.segment_ids,
        "attention_mask": lambda mb: mb.metadata.get("input_attention_mask"),
        "byte_spans": lambda mb: mb.metadata.get("target_byte_spans"),
        "token_offsets": lambda mb: mb.metadata.get("target_token_offsets"),
    }
    string_getters: dict[str, Any] = {
        "source_attribution": lambda mb: mb.source_attribution,
        "doc_ids": lambda mb: mb.metadata.get("target_doc_ids"),
        "lineage_ids": lambda mb: mb.metadata.get("target_lineage_ids"),
    }
    arrays: dict[str, np.ndarray] = {}
    for name, get in getters.items():
        values = [get(mb) for mb in microbatches]
        present = [v is not None for v in values]
        if not any(present):
            continue
        if not all(present):
            raise PayloadReceiptError(f"{name} is present in only some microbatches")
        arrays[name] = np.concatenate([_int_array(v, name) for v in values], axis=0)
    strings: dict[str, tuple[list[str], np.ndarray]] = {}
    for name, get in string_getters.items():
        values = [get(mb) for mb in microbatches]
        present = [v is not None for v in values]
        if not any(present):
            continue
        if not all(present):
            raise PayloadReceiptError(f"{name} is present in only some microbatches")
        strings[name] = _string_codes([row for v in values for row in v], name)
    sequences = int(arrays["input_ids"].shape[0]) if "input_ids" in arrays else 0
    return _build(sequences, _packing_mode([mb.metadata for mb in microbatches]), arrays, strings)


def canonical_from_prepared(update: Any) -> CanonicalUpdate:
    """Canonicalize a P34 ``PreparedUpdate`` (the producer's NumPy arrays and provenance)."""
    provenance = update.provenance
    arrays = {
        "input_ids": _int_array(update.input_ids, "input_ids"),
        "labels": _int_array(update.labels, "labels"),
        "loss_mask": _int_array(update.loss_mask, "loss_mask"),
        "position_ids": _int_array(update.position_ids, "position_ids"),
        "segment_ids": _int_array(update.segment_ids, "segment_ids"),
        "attention_mask": _int_array(update.input_attention_mask, "attention_mask"),
        "byte_spans": _int_array(provenance.byte_spans, "byte_spans"),
        "token_offsets": _int_array(provenance.token_offsets, "token_offsets"),
    }
    strings = {
        "source_attribution": _table_codes(provenance.attribution, provenance.strings),
        "doc_ids": _table_codes(provenance.doc_ids, provenance.strings),
        "lineage_ids": _table_codes(provenance.lineage_ids, provenance.strings),
    }
    if sum(update.rows) != arrays["input_ids"].shape[0]:
        raise PayloadReceiptError("prepared update row counts do not cover its arrays")
    return _build(sum(update.rows), _packing_mode(update.metadata), arrays, strings)


def canonical_update(batcher: Any, microbatches: Sequence[Any]) -> CanonicalUpdate:
    """The payload the trainer is about to use, from the authoritative CPU representation.

    A P34 producer hands the trainer zero-copy views of its prepared update,
    whose provenance stays on ``pending_update``; the synchronous batcher puts
    provenance in each microbatch. Both canonicalize to the same payload.
    """
    pending = getattr(batcher, "pending_update", None)
    if pending is not None:
        if sum(int(len(mb.input_ids)) for mb in microbatches) != sum(pending.rows):
            raise PayloadReceiptError("microbatches are not the pending prepared update")
        return canonical_from_prepared(pending)
    return canonical_from_microbatches(microbatches)


def chain_digest(previous: str, step: int, committed_before: int, valid: int, payload: str) -> str:
    return identity_digest(
        {
            "version": CHAIN_VERSION,
            "previous": previous,
            "step": step,
            "committed_before": committed_before,
            "valid_targets": valid,
            "payload_digest": payload,
        }
    )


class UpdatePayloadChain:
    """Committed per-update payload receipts; staged before compute, committed after it."""

    def __init__(self) -> None:
        self.rows: list[list[Any]] = []
        self._staged: list[Any] | None = None

    @property
    def head(self) -> str:
        return str(self.rows[-1][4]) if self.rows else CHAIN_GENESIS

    def _expected(self) -> tuple[int, int]:
        if not self.rows:
            return 1, 0
        step, before, valid = (int(v) for v in self.rows[-1][:3])
        return step + 1, before + valid

    def stage(self, *, step: int, committed_before: int, valid_targets: int, payload: str) -> None:
        """Hold one update's receipt until its optimizer update and data cursor commit."""
        if self._staged is not None:
            raise PayloadReceiptError("a staged update receipt was neither committed nor discarded")
        if (step, committed_before) != self._expected():
            raise PayloadReceiptError(
                f"update {step} at C={committed_before} does not follow the committed chain "
                f"(expected {self._expected()}); a replayed or skipped update is never appended"
            )
        if valid_targets <= 0:
            raise PayloadReceiptError("only updates with valid targets are receipted")
        self._staged = [step, committed_before, valid_targets, payload]

    def discard(self) -> None:
        """A failed, skipped or in-doubt update never becomes committed evidence."""
        self._staged = None

    def commit(self) -> list[Any]:
        if self._staged is None:
            raise PayloadReceiptError("no staged update receipt to commit")
        if len(self.rows) >= MAX_CHAIN_ROWS:
            raise PayloadReceiptError("update payload chain exceeds its bound")
        step, before, valid, payload = self._staged
        row = [step, before, valid, payload, chain_digest(self.head, step, before, valid, payload)]
        self.rows.append(row)
        self._staged = None
        return row

    def to_dict(self) -> dict[str, Any]:
        if self._staged is not None:
            raise PayloadReceiptError("cannot serialize a chain with an uncommitted staged update")
        return {
            "version": PAYLOAD_VERSION,
            "chain_version": CHAIN_VERSION,
            "genesis": CHAIN_GENESIS,
            "columns": list(CHAIN_COLUMNS),
            "rows": [list(r) for r in self.rows],
            "head": self.head,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> UpdatePayloadChain:
        """Restore a saved chain after re-deriving every link (tampering is refused)."""
        verify_chain(payload)
        chain = cls()
        chain.rows = [list(r) for r in payload["rows"]]
        return chain


def verify_chain(payload: Mapping[str, Any]) -> str:
    """Recompute a saved chain from its genesis; return its head or raise."""
    if (
        payload.get("version") != PAYLOAD_VERSION
        or payload.get("chain_version") != CHAIN_VERSION
        or payload.get("genesis") != CHAIN_GENESIS
        or payload.get("columns") != list(CHAIN_COLUMNS)
    ):
        raise PayloadReceiptError("unknown update payload chain layout or version")
    rows = payload.get("rows")
    if not isinstance(rows, list) or len(rows) > MAX_CHAIN_ROWS:
        raise PayloadReceiptError("update payload chain rows are missing or unbounded")
    head, step, before = CHAIN_GENESIS, 1, 0
    for row in rows:
        if not isinstance(row, list) or len(row) != len(CHAIN_COLUMNS):
            raise PayloadReceiptError("malformed update payload chain row")
        if int(row[0]) != step or int(row[1]) != before or int(row[2]) <= 0:
            raise PayloadReceiptError(f"update payload chain is not contiguous at step {row[0]}")
        head = chain_digest(head, int(row[0]), int(row[1]), int(row[2]), str(row[3]))
        if row[4] != head:
            raise PayloadReceiptError(f"update payload chain link {row[0]} does not verify")
        step, before = step + 1, before + int(row[2])
    if payload.get("head") != head:
        raise PayloadReceiptError("update payload chain head does not verify")
    return head
