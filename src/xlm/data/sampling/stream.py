"""Multi-source mixture batcher implementing the trainer's data protocol.

Contract C07: resume must restore source deficits, document cursors, the partial
pack, RNG and the committed-versus-prefetched boundary. Prefetch may advance a
speculative cursor, but a checkpoint commits only consumed data.

This class exposes the same protocol the single-source ``TrainingBatcher`` does --
``next_step_microbatches`` / ``commit`` / ``rollback`` / ``get_state`` /
``load_state`` -- so the existing trainer drives a mixture without modification.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from xlm.core.contracts import TrainingBatch
from xlm.data.sampling.mixture import MixtureRecipe, SourceAvailability
from xlm.data.sampling.packing import CausalStreamPacker, PackedWindow, build_packer
from xlm.data.sampling.scheduler import (
    QuotaScheduler,
    ScheduleState,
    SourceExhaustedError,
)
from xlm.data.sampling.trace import extend_target_trace_chain
from xlm.data.token_cache import TokenMapCache
from xlm.data.tokens import TokenShardReader

STREAM_STATE_VERSION = "2"


class MixtureStreamError(RuntimeError):
    """Raised when the mixture stream cannot produce a valid batch."""


class MixtureBatcher:
    """Streams training batches from several source shards under a mixture recipe."""

    def __init__(
        self,
        recipe: MixtureRecipe,
        shards: Mapping[str, TokenShardReader | Path],
        context_length: int = 512,
        global_batch_valid_targets: int = 65_536,
        microbatch_sequences: int | None = None,
        pad_token_id: int = 0,
        bos_token_id: int = 1,
        eos_token_id: int = 2,
        emit_tensors: bool = False,
        exposure_plan: dict[str, Any] | None = None,
        max_open_shards: int = 0,
    ) -> None:
        if context_length <= 0:
            raise ValueError(f"context_length must be positive, got {context_length}")
        if global_batch_valid_targets <= 0:
            raise ValueError(
                f"global_batch_valid_targets must be positive, got {global_batch_valid_targets}"
            )

        self.recipe = recipe
        self.context_length = context_length
        self.global_batch_valid_targets = global_batch_valid_targets
        self.microbatch_sequences = microbatch_sequences
        self.pad_token_id = pad_token_id
        self.bos_token_id = bos_token_id
        self.eos_token_id = eos_token_id
        # Torch is an optional extra and this package stays importable without it.
        # Tensors are built lazily, only when a caller (the trainer) asks for them.
        self.emit_tensors = emit_tensors
        self.exposure_plan = copy.deepcopy(exposure_plan)
        if microbatch_sequences is not None and microbatch_sequences <= 0:
            raise ValueError("microbatch_sequences must be positive")
        self._document_cache: dict[str, tuple[dict[str, Any], int]] = {}
        self._previous_document: dict[str, dict[str, Any]] = {}
        self._window_positions: list[int] = []
        self._window_records: list[dict[str, Any]] = []

        self.readers: dict[str, TokenShardReader] = {}
        availability: dict[str, SourceAvailability] = {}

        for component in recipe.components:
            source = shards.get(component.source_id)
            if source is None:
                raise MixtureStreamError(
                    f"mixture '{recipe.mixture_id}' names source '{component.source_id}' "
                    "but no shard was supplied for it"
                )
            reader = source if isinstance(source, TokenShardReader) else TokenShardReader(source)
            self.readers[component.source_id] = reader

            counters = reader.counters
            availability[component.source_id] = SourceAvailability(
                source_id=component.source_id,
                shard_id=reader.manifest.shard_id,
                valid_targets=int(
                    counters.get("valid_targets", max(0, reader.manifest.num_tokens - 1))
                ),
                content_tokens=int(counters.get("content_tokens", reader.manifest.num_tokens)),
                eos_tokens=int(counters.get("eos_tokens", 0)),
                canonical_bytes=int(counters.get("canonical_bytes", 0)),
                num_documents=reader.manifest.num_documents,
                token_dtype=reader.manifest.token_dtype,
            )

        self.availability = availability
        self.scheduler = QuotaScheduler(recipe, availability)
        self.packer = build_packer(recipe.packing.mode, context_length, pad_token_id, bos_token_id)

        # Committed state is what a checkpoint records; uncommitted is speculative and
        # is discarded on rollback, so a prefetch can never skip committed examples.
        self._committed = self._initial_state()
        self._uncommitted = copy.deepcopy(self._committed)
        if max_open_shards < 0:
            raise ValueError("max_open_shards cannot be negative")
        self.max_open_shards = max_open_shards
        self._token_maps = TokenMapCache(max_open_shards) if max_open_shards else None

    def close(self) -> None:
        """Release mapped shard handles; call when this stream is no longer needed."""
        if self._token_maps is not None:
            self._token_maps.close()

    def __enter__(self) -> MixtureBatcher:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _initial_state(self) -> dict[str, Any]:
        from xlm.artifacts.manifest import identity_digest

        return {
            "version": STREAM_STATE_VERSION,
            "mixture_identity": self.recipe.identity(),
            "exposure_identity": identity_digest(self.exposure_plan),
            "cursors": {s: 0 for s in self.readers},
            "epochs": {s: 0 for s in self.readers},
            "carry_token": {s: None for s in self.readers},
            "partial_pack": None,
            "scheduler": self.scheduler.get_state(),
            "committed_valid_targets": 0,
            "trace_digest": "",
            "last_step_trace": [],
            "last_step_trace_truncated": False,
            "byte_coverage_complete": True,
        }

    # ------------------------------------------------------------------ protocol

    def get_state(self) -> dict[str, Any]:
        """Serialize committed state only -- never speculative prefetch progress."""
        return copy.deepcopy(self._committed)

    def load_state(self, state_dict: dict[str, Any]) -> None:
        """Restore committed state, refusing a state from a different mixture."""
        if state_dict.get("version") != STREAM_STATE_VERSION:
            raise MixtureStreamError(
                "mixture packing state version differs; use original captured implementation"
            )
        recorded = state_dict.get("mixture_identity")
        from xlm.artifacts.manifest import identity_digest

        if state_dict.get("exposure_identity") != identity_digest(self.exposure_plan):
            raise MixtureStreamError("refusing to resume: token exposure plan identity differs")
        if recorded and recorded != self.recipe.identity():
            raise MixtureStreamError(
                "refusing to resume: checkpoint was written under mixture identity "
                f"'{recorded}' but this stream is '{self.recipe.identity()}'. "
                "A changed mixture is a new data lineage, not a resumable run."
            )
        self._committed = copy.deepcopy(state_dict)
        self._uncommitted = copy.deepcopy(state_dict)
        self.scheduler.load_state(self._committed["scheduler"])
        self._document_cache.clear()
        self._previous_document.clear()

    def commit(self) -> None:
        """Promote speculative progress after a successful optimizer step."""
        self._uncommitted["scheduler"] = self.scheduler.get_state()
        self._committed = copy.deepcopy(self._uncommitted)

    def rollback(self) -> None:
        """Discard speculative progress, returning to the last committed cursor."""
        self._uncommitted = copy.deepcopy(self._committed)
        self.scheduler.load_state(self._committed["scheduler"])

    # -------------------------------------------------------------------- batching

    def _read(self, source_id: str, start: int, count: int) -> list[int]:
        """Read a bounded token window from one source shard."""
        reader = self.readers[source_id]
        if self._token_maps is not None:
            return self._token_maps.read(reader, start, count)
        return reader.read_tokens_mmap(start=start, count=count)

    def _document_at(self, source_id: str, position: int) -> dict[str, Any]:
        """Locate a real document with a bounded streaming index, never an eager corpus list."""
        cached = self._document_cache.get(source_id)
        previous = self._previous_document.get(source_id)
        if (
            previous is not None
            and previous["token_start"]
            <= position
            < previous["token_start"] + previous["token_count"]
        ):
            return previous
        offset = 0
        if cached is not None:
            record, after = cached
            begin = int(record["token_start"])
            if begin <= position < begin + int(record["token_count"]):
                return record
            if position >= begin + int(record["token_count"]):
                offset = after
        with (self.readers[source_id].directory / "offsets.jsonl").open("rb") as index:
            index.seek(offset)
            while raw := index.readline(8 * 1024**2 + 1):
                if len(raw) > 8 * 1024**2:
                    raise MixtureStreamError("document index entry exceeds 8 MiB")
                loaded = json.loads(raw)
                if not isinstance(loaded, dict):
                    raise MixtureStreamError("document index entry must be an object")
                record = dict(loaded)
                record["_xlm_index_after"] = index.tell()
                begin, count = int(record["token_start"]), int(record["token_count"])
                if begin <= position < begin + count:
                    if cached is not None:
                        self._previous_document[source_id] = cached[0]
                    self._document_cache[source_id] = (record, index.tell())
                    return record
                if begin > position:
                    break
        raise MixtureStreamError(f"document index does not cover token {position} in {source_id}")

    def _next_window(self, source_id: str) -> tuple[list[int], bool]:
        """Return the next T+1 window from a source, and whether it repeats text."""
        cursor = self._uncommitted["cursors"][source_id]
        carry = self._uncommitted["carry_token"][source_id]
        total = self.readers[source_id].manifest.num_tokens

        needed = self.context_length if carry is not None else self.context_length + 1
        available = total - cursor

        repeated = self._uncommitted["epochs"][source_id] > 0

        # A window needs at least two tokens to yield one target. With a carried
        # context token one more token suffices; without one, two are needed. The
        # carry belongs to the finished epoch, so a new epoch starts without it.
        needs_new_epoch = available < (1 if carry is not None else 2)
        if needs_new_epoch:
            self.scheduler.advance_epoch(source_id)
            self._uncommitted["epochs"][source_id] += 1
            self._uncommitted["cursors"][source_id] = 0
            cursor, carry, repeated = 0, None, True
            needed = self.context_length + 1
            available = total

        take = min(needed, available)
        if self.exposure_plan is not None:
            remaining = (
                self.exposure_plan["projections"][source_id]["planned_targets"]
                - self.scheduler.state.counters[source_id].valid_targets
            )
            take = min(take, min(remaining, self.exposure_plan["block_size"]) + (carry is None))
        cap = self.recipe.packing.max_document_tokens
        if cap is not None:
            document = self._document_at(source_id, cursor)
            # One scheduler visit ends at the cap or real document boundary.
            # The next visit competes again on observed target deficits.
            take = min(take, cap, document["token_start"] + document["token_count"] - cursor)
        chunk = self._read(source_id, cursor, take)
        window = ([carry] + chunk) if carry is not None else chunk
        self._window_positions = ([cursor - 1] if carry is not None else []) + list(
            range(cursor, cursor + take)
        )
        self._window_records = []
        position = self._window_positions[0]
        stop = self._window_positions[-1] + 1
        while position < stop:
            record = self._document_at(source_id, position)
            end = min(stop, int(record["token_start"]) + int(record["token_count"]))
            self._window_records.extend([record] * (end - position))
            position = end

        self._uncommitted["cursors"][source_id] = cursor + take
        # Overlap exactly the last context token, so the next window's first target is
        # the one this window could not reach -- no target dropped, none counted twice.
        self._uncommitted["carry_token"][source_id] = window[-1]
        return window, repeated

    def _pack_window(self, source_id: str, window: list[int]) -> PackedWindow:
        """Use real document identities to retain isolation across capped visits."""
        packed = CausalStreamPacker(self.context_length, self.pad_token_id, self.bos_token_id).pack(
            window, [source_id] * self.context_length
        )
        records = self._window_records
        usable = len(window) - 1
        packed.doc_ids = [str(record["doc_id"]) for record in records[1:]] + [""] * (
            self.context_length - usable
        )
        packed.byte_spans = []
        for record, position in zip(records[1:], self._window_positions[1:], strict=True):
            spans = record.get("token_byte_spans")
            local = position - record["token_start"]
            packed.byte_spans.append(tuple(spans[local]) if spans is not None else (-1, -1))
        packed.byte_spans += [(0, 0)] * (self.context_length - usable)
        packed.token_offsets = self._window_positions[1:] + [-1] * (self.context_length - usable)
        packed.lineage_ids = [str(record.get("lineage_id", "")) for record in records[1:]] + [
            ""
        ] * (self.context_length - usable)
        if self.recipe.packing.mode == "isolated_document":
            for i in range(usable):
                # A cap is not a document boundary. Continuation retains the real
                # document and position; only a genuine document change isolates.
                packed.segment_ids[i] = int(records[i]["token_start"])
                packed.position_ids[i] = self._window_positions[i] - records[i]["token_start"]
                if records[i]["token_start"] != records[i + 1]["token_start"]:
                    packed.loss_mask[i] = 0
            packed.segment_ids[usable:] = [-1] * (self.context_length - usable)
        return packed

    def next_step_microbatches(self, remaining_budget: int | None = None) -> list[TrainingBatch]:
        """Assemble the microbatches for one optimizer update."""
        limit = self.global_batch_valid_targets
        if remaining_budget is not None:
            limit = min(limit, remaining_budget)
        if limit <= 0:
            return []

        windows: list[Any] = []
        step_targets = 0
        self._uncommitted["last_step_trace"] = []
        self._uncommitted["last_step_trace_truncated"] = False

        while step_targets < limit:
            if (len(windows) + 1) * self.context_length > 1_048_576:
                raise MixtureStreamError(
                    "packed optimizer step exceeds 1048576 positions; "
                    "reduce the declared global batch or increase the document visit cap"
                )
            if self.exposure_plan is None:
                source_id = self.scheduler.select_source()
            else:
                candidates = [
                    s
                    for s in self.scheduler.source_ids
                    if self.scheduler.state.counters[s].valid_targets
                    < self.exposure_plan["projections"][s]["planned_targets"]
                ]
                if not candidates:
                    break
                source_id = max(
                    candidates,
                    key=lambda s: (
                        self.scheduler.deficit_of(s),
                        -self.scheduler.source_ids.index(s),
                    ),
                )
            try:
                window_ids, repeated = self._next_window(source_id)
            except SourceExhaustedError:
                if not windows:
                    raise
                break

            if len(window_ids) < 2:
                continue  # A cap-one initial context visit cannot yet yield a target.
            packed = self._pack_window(source_id, window_ids)

            valid = packed.valid_targets
            if valid <= 0:
                continue

            if step_targets + valid > limit:
                # Trim the final window so the run stops at exactly the allowed count.
                allowed = limit - step_targets
                trimmed = 0
                for index, flag in enumerate(packed.loss_mask):
                    if flag:
                        trimmed += 1
                        if trimmed > allowed:
                            packed.loss_mask[index] = 0
                        else:
                            last_kept = index
                self._uncommitted["cursors"][source_id] = self._window_positions[last_kept + 1] + 1
                self._uncommitted["carry_token"][source_id] = packed.labels[last_kept]
                # A speculative window can span more than two short documents.
                # Anchor index lookup at the last consumed record, not its suffix.
                kept_record = self._window_records[last_kept + 1]
                self._document_cache[source_id] = (kept_record, kept_record["_xlm_index_after"])
                self._previous_document.pop(source_id, None)
                valid = packed.valid_targets

            eos_targets = sum(
                1
                for label, flag in zip(packed.labels, packed.loss_mask, strict=True)
                if flag and label == self.eos_token_id
            )
            bos_excluded = sum(
                1
                for token, flag in zip(packed.labels, packed.loss_mask, strict=True)
                if not flag and token == self.bos_token_id
            )
            padding = sum(1 for flag in packed.loss_mask if not flag) - bos_excluded
            kept = [index for index, flag in enumerate(packed.loss_mask) if flag]
            epoch = self._uncommitted["epochs"][source_id]
            # One exact chain fold per window; see extend_target_trace_chain.
            self._uncommitted["trace_digest"] = extend_target_trace_chain(
                self._uncommitted["trace_digest"],
                source_id,
                epoch,
                packed.doc_ids,
                packed.lineage_ids,
                packed.token_offsets,
                packed.labels,
                packed.byte_spans,
                kept,
            )
            if any(packed.byte_spans[index][0] < 0 for index in kept):
                self._uncommitted["byte_coverage_complete"] = False
            recorded = self._uncommitted["last_step_trace"]
            room = max(0, 256 - len(recorded))
            recorded.extend(
                {
                    "source_id": source_id,
                    "doc_id": packed.doc_ids[index],
                    "lineage_id": packed.lineage_ids[index],
                    "token_offset": packed.token_offsets[index],
                    "label": packed.labels[index],
                    "byte_span": list(packed.byte_spans[index]),
                    "epoch": epoch,
                }
                for index in kept[:room]
            )
            if len(kept) > room:
                self._uncommitted["last_step_trace_truncated"] = True

            self.scheduler.record_exposure(
                source_id=source_id,
                valid_targets=valid,
                content_targets=valid - eos_targets,
                eos_targets=eos_targets,
                bos_excluded=bos_excluded,
                padding_excluded=max(0, padding),
                repeated=repeated,
                canonical_bytes=sum(
                    max(0, end - start)
                    for (start, end), flag in zip(packed.byte_spans, packed.loss_mask, strict=True)
                    if flag
                ),
                documents=len(
                    {
                        record["doc_id"]
                        for record, flag in zip(
                            self._window_records[1:], packed.loss_mask, strict=False
                        )
                        if flag and record["token_start"] >= self._window_positions[0]
                    }
                ),
            )
            windows.append(packed)
            step_targets += valid

        if not windows:
            return []

        self._uncommitted["committed_valid_targets"] += step_targets
        return self._to_microbatches(windows)

    def _to_microbatches(self, windows: list[Any]) -> list[TrainingBatch]:
        """Group packed windows into microbatches of the configured size."""
        size = self.microbatch_sequences or len(windows)
        batches: list[TrainingBatch] = []

        convert = self._tensor_factory() if self.emit_tensors else None

        for start in range(0, len(windows), size):
            chunk = windows[start : start + size]
            inputs = [w.input_ids for w in chunk]
            labels = [w.labels for w in chunk]
            masks = [w.loss_mask for w in chunk]
            positions = [w.position_ids for w in chunk]

            if convert is not None:
                inputs, labels, masks, positions = (
                    convert(inputs),
                    convert(labels),
                    convert(masks),
                    convert(positions),
                )

            valid = sum(w.valid_targets for w in chunk)
            batches.append(
                TrainingBatch(
                    input_ids=inputs,
                    labels=labels,
                    loss_mask=masks,
                    position_ids=positions,
                    segment_ids=[w.segment_ids for w in chunk],
                    source_attribution=[w.source_attribution for w in chunk],
                    metadata={
                        "packing_mode": self.packer.mode,
                        "valid_targets": valid,
                        # The trainer reads this key; keep both names in step.
                        "valid_target_count": valid,
                        "sequence_count": len(chunk),
                        "is_partial": any(w.is_partial for w in chunk),
                        "target_byte_spans": [w.byte_spans for w in chunk],
                        "target_doc_ids": [w.doc_ids for w in chunk],
                        "target_token_offsets": [w.token_offsets for w in chunk],
                        "target_lineage_ids": [w.lineage_ids for w in chunk],
                        "input_attention_mask": [
                            [int(token != self.pad_token_id) for token in w.input_ids]
                            for w in chunk
                        ],
                    },
                )
            )
        return batches

    @staticmethod
    def _tensor_factory() -> Any:
        """Return a list-of-lists to tensor converter, importing torch lazily."""
        import torch

        def convert(rows: list[list[int]]) -> Any:
            return torch.tensor(rows, dtype=torch.long)

        return convert

    # -------------------------------------------------------------------- reporting

    def share_report(self) -> dict[str, Any]:
        """Observed shares, drift and structural-token gap."""
        report = self.scheduler.share_report()
        return {
            **report.to_dict(),
            "structural_gap": report.structural_gap(),
            "byte_coverage_complete": self._uncommitted["byte_coverage_complete"],
        }

    @property
    def schedule_state(self) -> ScheduleState:
        return self.scheduler.state
