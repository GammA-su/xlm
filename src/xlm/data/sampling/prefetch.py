"""Bounded speculative update production in one long-lived child process (P34).

The child runs the unchanged :class:`MixtureBatcher`; this module only moves its
outputs across a pipe and binds them to committed state. Contract:

* Committed state lives only in the consumer and changes only in ``commit``.
  The child's batcher state is speculative: it runs at most ``depth`` updates
  ahead and is replaced wholesale by ``reset`` from committed state.
* Every prepared update carries the digest of the state it started from and the
  state it ends in. The consumer uses an update only when its start digest is
  the committed digest, so an update is never skipped, duplicated or stale.
* ``rollback``, ``load_state`` and ``restart`` start a new generation. Messages
  from older generations are discarded unread. Regeneration from the same
  committed state is deterministic, because the batcher is.
* Tensors travel as compact NumPy arrays. Per-target provenance travels as
  string-table codes and is materialized exactly on request, never implicitly
  on the trainer's thread. Values that cannot be encoded losslessly fail closed.

Windows ``spawn`` requirements: this module has no import-time side effects,
the child entry point is a module-level function, and every argument is plain
picklable data. The child imports neither torch nor CUDA.
"""

from __future__ import annotations

import collections
import copy
import hashlib
import logging
import math
import multiprocessing
import time
import weakref
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from xlm.core.contracts import TrainingBatch
from xlm.data.sampling.mixture import MixtureRecipe
from xlm.data.sampling.prefetch_transport import BoundedConnection, send_message
from xlm.data.sampling.scheduler import RepeatBudgetExceededError, SourceExhaustedError
from xlm.data.sampling.stream import MixtureBatcher, MixtureStreamError
from xlm.data.tokens import TokenShardReader

logger = logging.getLogger(__name__)
# multiprocessing.connection.Connection, or PipeConnection on Windows.
_Connection = Any

PREFETCH_PROTOCOL_VERSION = "p34-prefetch-v1"
_TRANSPORTED_ERRORS: dict[str, type[Exception]] = {
    "SourceExhaustedError": SourceExhaustedError,
    "RepeatBudgetExceededError": RepeatBudgetExceededError,
    "MixtureStreamError": MixtureStreamError,
}
_LIGHT_METADATA = (
    "packing_mode",
    "valid_targets",
    "valid_target_count",
    "sequence_count",
    "is_partial",
)


class PrefetchError(RuntimeError):
    """Base class for prefetch transport and protocol failures."""


class PrefetchProducerError(PrefetchError):
    """The producer process failed, exited or timed out."""


class PrefetchProtocolError(PrefetchError):
    """A prepared update is not bound to the consumer's committed state."""


class PrefetchEncodingError(PrefetchError):
    """A batch value cannot be carried losslessly by the compact encoding."""


def state_digest(state: dict[str, Any]) -> str:
    """Canonical identity of one batcher state, as checkpoints serialize it."""
    from xlm.artifacts.manifest import identity_digest

    return identity_digest(state)


@dataclass(frozen=True)
class ProducerSpec:
    """Plain, picklable reconstruction recipe for the producer's batcher."""

    recipe: dict[str, Any]
    shards: dict[str, str]
    manifests: dict[str, dict[str, Any]]
    context_length: int
    global_batch_valid_targets: int
    microbatch_sequences: int | None
    pad_token_id: int
    bos_token_id: int
    eos_token_id: int
    exposure_plan: dict[str, Any] | None = None
    max_open_shards: int = 0
    # P35 M5: the frozen order manifest; the child verifies and indexes the same order.
    document_order: dict[str, Any] | None = None
    # A module-level callable, pickled by reference. Tests inject failures here.
    factory: Callable[[ProducerSpec], MixtureBatcher] | None = None
    # Counters authenticate the v2 byte table; snapshot them across process startup.
    counters: dict[str, dict[str, Any]] | None = None

    @classmethod
    def from_batcher(cls, batcher: MixtureBatcher) -> ProducerSpec:
        return cls(
            recipe=batcher.recipe.model_dump(mode="json"),
            shards={s: str(r.directory) for s, r in batcher.readers.items()},
            manifests={s: r.manifest.to_dict() for s, r in batcher.readers.items()},
            context_length=batcher.context_length,
            global_batch_valid_targets=batcher.global_batch_valid_targets,
            microbatch_sequences=batcher.microbatch_sequences,
            pad_token_id=batcher.pad_token_id,
            bos_token_id=batcher.bos_token_id,
            eos_token_id=batcher.eos_token_id,
            exposure_plan=copy.deepcopy(batcher.exposure_plan),
            max_open_shards=batcher.max_open_shards,
            document_order=copy.deepcopy(batcher.document_order),
            counters={s: r.counters for s, r in batcher.readers.items()},
        )

    def build(self) -> MixtureBatcher:
        """Reconstruct the batcher from verified, unchanged shard artifacts."""
        if self.factory is not None:
            return self.factory(self)
        readers = {}
        for source_id, directory in self.shards.items():
            reader = TokenShardReader(Path(directory))
            if reader.manifest.to_dict() != self.manifests[source_id]:
                raise PrefetchProducerError(f"shard manifest for {source_id} changed")
            if self.counters is not None and reader.counters != self.counters[source_id]:
                raise PrefetchProducerError("shard counters changed before producer startup")
            reader.verify_integrity()
            readers[source_id] = reader
        return MixtureBatcher(
            MixtureRecipe.model_validate(self.recipe),
            readers,
            context_length=self.context_length,
            global_batch_valid_targets=self.global_batch_valid_targets,
            microbatch_sequences=self.microbatch_sequences,
            pad_token_id=self.pad_token_id,
            bos_token_id=self.bos_token_id,
            eos_token_id=self.eos_token_id,
            emit_tensors=False,
            exposure_plan=copy.deepcopy(self.exposure_plan),
            max_open_shards=self.max_open_shards,
            document_order=copy.deepcopy(self.document_order),
        )


@dataclass(frozen=True)
class CompactProvenance:
    """Per-target provenance as codes into one string table; lossless."""

    strings: tuple[str, ...]
    attribution: np.ndarray  # int32 [W, T]
    doc_ids: np.ndarray  # int32 [W, T]
    lineage_ids: np.ndarray  # int32 [W, T]
    byte_spans: np.ndarray  # int64 [W, T, 2]
    token_offsets: np.ndarray  # int64 [W, T]

    def materialize(self, start: int, stop: int) -> dict[str, Any]:
        """Exactly the lists ``MixtureBatcher`` places in one microbatch."""
        table = self.strings

        def strings(codes: np.ndarray) -> list[list[str]]:
            return [[table[i] for i in row] for row in codes[start:stop].tolist()]

        return {
            "source_attribution": strings(self.attribution),
            "target_doc_ids": strings(self.doc_ids),
            "target_lineage_ids": strings(self.lineage_ids),
            "target_byte_spans": [
                list(map(tuple, row)) for row in self.byte_spans[start:stop].tolist()
            ],
            "target_token_offsets": self.token_offsets[start:stop].tolist(),
        }


@dataclass(frozen=True)
class PreparedUpdate:
    """One exact optimizer update, bound to its start and end batcher states."""

    generation: int
    ordinal: int
    remaining_budget: int | None
    start_state_digest: str
    end_state: dict[str, Any] | None
    end_state_digest: str | None
    rows: tuple[int, ...]
    metadata: tuple[dict[str, Any], ...]
    input_ids: np.ndarray
    labels: np.ndarray
    loss_mask: np.ndarray
    position_ids: np.ndarray
    segment_ids: np.ndarray
    input_attention_mask: np.ndarray
    provenance: CompactProvenance
    content_digest: str
    produce_seconds: float
    valid_targets: int

    @property
    def empty(self) -> bool:
        return not self.rows

    def compute_content_digest(self) -> str:
        return _content_digest(
            self.rows,
            self.metadata,
            (
                self.input_ids,
                self.labels,
                self.loss_mask,
                self.position_ids,
                self.segment_ids,
                self.input_attention_mask,
                self.provenance.attribution,
                self.provenance.doc_ids,
                self.provenance.lineage_ids,
                self.provenance.byte_spans,
                self.provenance.token_offsets,
            ),
            self.provenance.strings,
        )

    def microbatch_provenance(self, index: int) -> dict[str, Any]:
        """Exact per-target provenance lists for one returned microbatch."""
        start = sum(self.rows[:index])
        return self.provenance.materialize(start, start + self.rows[index])

    def to_microbatches(self) -> list[TrainingBatch]:
        """Zero-copy CPU tensors in the dtypes the synchronous batcher emits.

        Segment IDs and the attention mask are tensors rather than nested lists;
        per-target provenance stays compact (see ``microbatch_provenance``).
        """
        import torch

        batches = []
        start = 0
        for size, light in zip(self.rows, self.metadata, strict=True):
            stop = start + size
            batches.append(
                TrainingBatch(
                    input_ids=torch.from_numpy(self.input_ids[start:stop]),
                    labels=torch.from_numpy(self.labels[start:stop]),
                    loss_mask=torch.from_numpy(self.loss_mask[start:stop]),
                    position_ids=torch.from_numpy(self.position_ids[start:stop]),
                    segment_ids=torch.from_numpy(self.segment_ids[start:stop]),
                    source_attribution=None,
                    metadata={
                        **light,
                        "input_attention_mask": torch.from_numpy(
                            self.input_attention_mask[start:stop]
                        ),
                    },
                )
            )
            start = stop
        return batches


def _content_digest(
    rows: tuple[int, ...],
    metadata: tuple[dict[str, Any], ...],
    arrays: tuple[np.ndarray, ...],
    strings: tuple[str, ...],
) -> str:
    from xlm.artifacts.manifest import identity_digest

    digest = hashlib.sha256()
    digest.update(identity_digest({"rows": list(rows), "metadata": list(metadata)}).encode())
    digest.update(identity_digest(list(strings)).encode())
    for array in arrays:
        digest.update(f"{array.dtype.str}{array.shape}".encode())
        digest.update(np.ascontiguousarray(array).data)
    return digest.hexdigest()


def _encode(
    batches: list[TrainingBatch],
    generation: int,
    ordinal: int,
    remaining_budget: int | None,
    start_digest: str,
    end_state: dict[str, Any] | None,
    started: float,
) -> PreparedUpdate:
    """Pack list-mode batches into arrays; refuse anything not exactly representable."""
    table: dict[str, int] = {}

    def codes(rows: list[list[str]]) -> list[list[int]]:
        return [[table.setdefault(value, len(table)) for value in row] for row in rows]

    def exact(rows: list[Any], dtype: type, name: str) -> np.ndarray:
        flat = [value for row in rows for value in (row if name != "spans" else _flat(row))]
        if set(map(type, flat)) - {int}:
            raise PrefetchEncodingError(f"{name} contains non-integer values")
        return np.array(rows, dtype=dtype)

    def listed(value: Any, name: str) -> list[Any]:
        if type(value) is not list:
            raise PrefetchEncodingError(f"{name} must be list-mode batcher output")
        return value

    inputs: list[Any] = []
    labels: list[Any] = []
    masks: list[Any] = []
    positions: list[Any] = []
    segments: list[Any] = []
    attention: list[Any] = []
    attribution: list[Any] = []
    docs: list[Any] = []
    lineages: list[Any] = []
    spans: list[Any] = []
    offsets: list[Any] = []
    metadata = []
    for batch in batches:
        inputs += listed(batch.input_ids, "input_ids")
        labels += listed(batch.labels, "labels")
        masks += listed(batch.loss_mask, "loss_mask")
        positions += listed(batch.position_ids, "position_ids")
        segments += listed(batch.segment_ids, "segment_ids")
        attention += listed(batch.metadata["input_attention_mask"], "input_attention_mask")
        attribution += listed(batch.source_attribution, "source_attribution")
        docs += batch.metadata["target_doc_ids"]
        lineages += batch.metadata["target_lineage_ids"]
        spans += batch.metadata["target_byte_spans"]
        offsets += batch.metadata["target_token_offsets"]
        metadata.append({key: batch.metadata[key] for key in _LIGHT_METADATA})
    for columns, name in ((attribution, "attribution"), (docs, "doc_ids"), (lineages, "lineage")):
        if set(map(type, (value for row in columns for value in row))) - {str}:
            raise PrefetchEncodingError(f"{name} contains non-string values")
    if any(len(span) != 2 for row in spans for span in row):
        raise PrefetchEncodingError("byte spans must be coordinate pairs")
    attribution_codes = np.array(codes(attribution), dtype=np.int32)
    doc_codes = np.array(codes(docs), dtype=np.int32)
    lineage_codes = np.array(codes(lineages), dtype=np.int32)
    provenance = CompactProvenance(
        strings=tuple(table),
        attribution=attribution_codes,
        doc_ids=doc_codes,
        lineage_ids=lineage_codes,
        byte_spans=exact(spans, np.int64, "spans"),
        token_offsets=exact(offsets, np.int64, "token_offsets"),
    )
    arrays = {
        "input_ids": exact(inputs, np.int64, "input_ids"),
        "labels": exact(labels, np.int64, "labels"),
        "loss_mask": exact(masks, np.int64, "loss_mask"),
        "position_ids": exact(positions, np.int64, "position_ids"),
        "segment_ids": exact(segments, np.int64, "segment_ids"),
        # The trainer builds this with torch.tensor(..., dtype=bool): nonzero is True.
        "input_attention_mask": exact(attention, np.int64, "attention").astype(bool),
    }
    rows = tuple(len(batch.input_ids) for batch in batches)
    metadata_tuple = tuple(metadata)
    content = _content_digest(
        rows,
        metadata_tuple,
        (
            *arrays.values(),
            provenance.attribution,
            provenance.doc_ids,
            provenance.lineage_ids,
            provenance.byte_spans,
            provenance.token_offsets,
        ),
        provenance.strings,
    )
    return PreparedUpdate(
        generation=generation,
        ordinal=ordinal,
        remaining_budget=remaining_budget,
        start_state_digest=start_digest,
        end_state=end_state,
        end_state_digest=None if end_state is None else state_digest(end_state),
        rows=rows,
        metadata=metadata_tuple,
        provenance=provenance,
        content_digest=content,
        produce_seconds=time.perf_counter() - started,
        valid_targets=sum(int(m["valid_target_count"]) for m in metadata),
        **arrays,
    )


def _flat(row: list[tuple[int, int]]) -> list[int]:
    return [value for span in row for value in span]


def _error_message(generation: int, exc: BaseException) -> tuple[Any, ...]:
    # Batcher errors name sources and positions, never corpus text. Other types
    # travel by name only, as in the tokenization worker pool.
    name = type(exc).__name__
    detail = str(exc) if name in _TRANSPORTED_ERRORS or isinstance(exc, PrefetchError) else ""
    return ("error", generation, name, detail)


def _producer_main(conn: _Connection, spec: ProducerSpec, initial_state: dict[str, Any]) -> None:
    """Child entry point: serve ``reset`` / ``produce`` / ``stop`` until told or orphaned."""
    started = time.perf_counter()
    try:
        batcher = spec.build()
        batcher.load_state(initial_state)
        state = batcher.get_state()
        send_message(
            conn,
            (
                "ready",
                PREFETCH_PROTOCOL_VERSION,
                state_digest(state),
                time.perf_counter() - started,
            ),
        )
    except BaseException as exc:
        send_message(conn, _error_message(0, exc))
        return
    generation, ordinal, poisoned = 0, 0, False
    try:
        while True:
            try:
                message = conn.recv()
            except (EOFError, OSError):
                return  # The consumer is gone; never outlive it.
            kind = message[0]
            if kind == "stop":
                return
            if kind == "reset":
                _, generation, state = message
                ordinal = 0
                try:
                    batcher.load_state(state)
                    poisoned = False
                    send_message(conn, ("reset_ok", generation, state_digest(batcher.get_state())))
                except BaseException as exc:
                    poisoned = True
                    send_message(conn, _error_message(generation, exc))
                continue
            if kind != "produce":
                raise PrefetchProtocolError(f"unknown producer request {kind!r}")
            _, requested, budget = message
            if requested != generation or poisoned:
                continue  # Stale speculation; a reset for the new generation follows.
            begin = time.perf_counter()
            try:
                start = state_digest(batcher.get_state())
                batches = batcher.next_step_microbatches(remaining_budget=budget)
                if batches:
                    batcher.commit()  # Speculative: this is the child's copy only.
                    end_state: dict[str, Any] | None = batcher.get_state()
                else:
                    batcher.rollback()
                    end_state = None
                update = _encode(batches, generation, ordinal, budget, start, end_state, begin)
            except BaseException as exc:
                poisoned = True
                send_message(conn, _error_message(generation, exc))
                continue
            send_message(conn, ("update", generation, update))
            ordinal += 1
    finally:
        batcher.close()
        conn.close()


def _shutdown(process: Any, conn: _Connection) -> None:
    # Close first: EOF on the child's next receive (or a failed send) always
    # reaps it, while a farewell message could block forever on a full pipe.
    try:
        conn.close()
    except (OSError, ValueError):
        pass
    process.join(timeout=0.25)
    if process.is_alive():
        process.terminate()
        process.join(timeout=5)
    if process.is_alive():
        process.kill()
        process.join(timeout=5)
    conn.join_reader()


class PrefetchingBatcher:
    """``BatcherProtocol`` implementation fed by one speculative producer process.

    ``depth`` bounds prepared-but-unconsumed updates beyond the one being trained.
    """

    close_on_trainer_exit = True
    requires_cuda_commit_barrier = True

    def __init__(
        self,
        spec: ProducerSpec,
        initial_state: dict[str, Any],
        *,
        depth: int = 1,
        timeout_seconds: float = 300.0,
        verify_content: bool = False,
    ) -> None:
        if not 1 <= depth <= 2:
            raise ValueError("prefetch depth must be 1 or 2")
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.spec = spec
        self.depth = depth
        self.timeout_seconds = timeout_seconds
        self.verify_content = verify_content
        self._committed = copy.deepcopy(initial_state)
        self._committed_digest = state_digest(self._committed)
        self._generation = 0
        self._ordinal = 0
        self._ready: collections.deque[PreparedUpdate] = collections.deque()
        self._outstanding = 0
        self._pending: PreparedUpdate | None = None
        self._needs_reset = False
        self._reset_ack: str | None = None
        self._stats: dict[str, float] = collections.defaultdict(float)
        self._process: Any = None
        self._conn: _Connection | None = None
        self._finalizer: Any = None
        self._start_process()

    # ------------------------------------------------------------- lifecycle

    def _start_process(self) -> None:
        context = multiprocessing.get_context("spawn")
        parent, child = context.Pipe(duplex=True)
        begin = time.perf_counter()
        process = context.Process(
            target=_producer_main,
            args=(child, self.spec, self._committed),
            name="xlm-prefetch-producer",
            daemon=True,
        )
        try:
            process.start()
            child.close()
            transport = BoundedConnection(parent, self.depth + 2, self.timeout_seconds)
        except BaseException as exc:
            # Ownership must exist even if the reader cannot start: there is no
            # batcher finalizer yet to reap the already spawned child.
            parent.close()
            child.close()
            if process.pid is not None:
                if process.is_alive():
                    process.terminate()
                process.join(timeout=5)
                if process.is_alive():
                    process.kill()
                    process.join(timeout=5)
            if isinstance(exc, Exception):
                raise PrefetchProducerError("producer transport startup failed") from exc
            raise
        self._process, self._conn = process, transport
        self._finalizer = weakref.finalize(self, _shutdown, process, transport)
        message = self._receive_raw()
        if message[0] == "error":
            self.close()
            self._raise_remote(message)
        _, version, digest, child_seconds = message
        if version != PREFETCH_PROTOCOL_VERSION or digest != self._committed_digest:
            self.close()
            raise PrefetchProtocolError("producer did not start from the committed state")
        self._stats["startup_seconds"] += time.perf_counter() - begin
        self._stats["child_build_seconds"] += child_seconds
        self._stats["starts"] += 1
        logger.info(
            "prefetch producer started",
            extra={"pid": process.pid, "startup_seconds": time.perf_counter() - begin},
        )

    def close(self) -> None:
        """Stop the producer and release its pipe; idempotent."""
        if self._finalizer is not None:
            self._finalizer()
        self._ready.clear()
        self._outstanding = 0

    def restart(self) -> None:
        """Replace the producer process, resuming from committed state only."""
        self.close()
        self._pending = None
        self._needs_reset = False
        self._generation = 0
        self._ordinal = 0
        self._stats["restarts"] += 1
        self._start_process()

    def __enter__(self) -> PrefetchingBatcher:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    @property
    def producer_pid(self) -> int | None:
        return None if self._process is None else self._process.pid

    def stats(self) -> dict[str, float]:
        return dict(self._stats)

    # ------------------------------------------------------------- transport

    def _receive_raw(self) -> tuple[Any, ...]:
        assert self._conn is not None and self._process is not None
        self._conn.timeout_seconds = self.timeout_seconds
        deadline = time.monotonic() + self.timeout_seconds
        try:
            while not self._conn.poll(0.05):
                if not self._process.is_alive():
                    exitcode = self._process.exitcode
                    self.close()
                    raise PrefetchProducerError(f"producer exited with code {exitcode}")
                if time.monotonic() >= deadline:
                    self.close()
                    raise PrefetchProducerError("producer exceeded its response timeout")
        except OSError as exc:
            # Windows PeekNamedPipe can fail during poll, before recv_bytes.
            self.close()
            raise PrefetchProducerError("producer pipe poll failed") from exc
        try:
            message: tuple[Any, ...] = self._conn.recv()
        except (EOFError, OSError) as exc:
            self.close()
            raise PrefetchProducerError("producer pipe closed") from exc
        return message

    def _raise_remote(self, message: tuple[Any, ...]) -> None:
        _, _, name, detail = message
        error = _TRANSPORTED_ERRORS.get(name)
        if error is not None:
            raise error(detail)
        raise PrefetchProducerError(f"producer failed: {name}{': ' + detail if detail else ''}")

    def _send(self, message: tuple[Any, ...]) -> None:
        if self._conn is None or self._finalizer is None or not self._finalizer.alive:
            raise PrefetchProducerError("producer is closed; call restart()")
        try:
            self._conn.timeout_seconds = self.timeout_seconds
            self._conn.send(message)
        except OSError as exc:
            # A dead child surfaces here as BrokenPipeError, not as a valid reply.
            self._needs_reset = True
            self._outstanding = 0
            self._pending = None
            self.close()
            raise PrefetchProducerError("producer pipe closed during send") from exc

    def _pump(self, until: Callable[[], bool]) -> None:
        """Receive until ``until()``; stale generations are discarded unread."""
        deadline = time.monotonic() + self.timeout_seconds
        while not until():
            if time.monotonic() >= deadline:
                self.close()
                raise PrefetchProducerError("producer exceeded its protocol timeout")
            message = self._receive_raw()
            if message[1] != self._generation:
                self._stats["discarded_stale"] += 1
                continue
            if message[0] == "error":
                self._needs_reset = True
                self._outstanding = 0
                self._raise_remote(message)
            if message[0] == "update":
                self._ready.append(message[2])
            elif message[0] == "reset_ok":
                self._reset_ack = message[2]

    def _check_end_binding(self, update: PreparedUpdate) -> None:
        """Recompute the child's end-state claims before the trainer may use them."""
        if update.end_state is None or update.end_state_digest is None:
            self._needs_reset = True
            raise PrefetchProtocolError("prepared update carries no ending committed state")
        if state_digest(update.end_state) != update.end_state_digest:
            self._needs_reset = True
            raise PrefetchProtocolError("prepared update ending state digest differs")
        advanced = (
            update.end_state["committed_valid_targets"] - self._committed["committed_valid_targets"]
        )
        if advanced != update.valid_targets:
            self._needs_reset = True
            raise PrefetchProtocolError("prepared update target count differs")

    def _reset(self, state: dict[str, Any]) -> None:
        self._generation += 1
        self._ordinal = 0
        self._ready.clear()
        self._outstanding = 0
        self._needs_reset = False
        self._stats["resets"] += 1
        self._reset_ack = None
        # The lifetime reader drains partial child sends during every control
        # write, including tiny produce requests. No reset-specific reader race.
        self._send(("reset", self._generation, state))
        # Finish this handshake before another reset can accumulate acknowledgments
        # in the bounded inbox. Rollback is an exceptional, non-hot path.
        self._pump(lambda: self._reset_ack is not None)
        if self._reset_ack != state_digest(state):
            self._needs_reset = True
            raise PrefetchProtocolError("producer reset state differs")

    def _request(self, budget: int | None) -> None:
        self._send(("produce", self._generation, budget))
        self._outstanding += 1
        self._stats["requests"] += 1

    # ---------------------------------------------------------- BatcherProtocol

    def next_step_microbatches(self, remaining_budget: int | None = None) -> list[TrainingBatch]:
        if self._pending is not None:
            raise PrefetchProtocolError("previous update was neither committed nor rolled back")
        if self._needs_reset:
            self._reset(self._committed)
        update = self._take(remaining_budget)
        if update.remaining_budget != remaining_budget:
            # Speculation assumed a different budget; regenerate synchronously.
            self._stats["budget_mispredictions"] += 1
            self._reset(self._committed)
            update = self._take(remaining_budget)
        if (
            update.generation != self._generation
            or update.ordinal != self._ordinal
            or update.remaining_budget != remaining_budget
        ):
            self._needs_reset = True
            raise PrefetchProtocolError("prepared update generation/ordinal/budget differs")
        if update.start_state_digest != self._committed_digest:
            self._needs_reset = True
            raise PrefetchProtocolError("prepared update does not start at the committed state")
        if self.verify_content:
            began_verification = time.perf_counter()
            content_matches = update.compute_content_digest() == update.content_digest
            self._stats["verify_content_seconds"] += time.perf_counter() - began_verification
            if not content_matches:
                self._needs_reset = True
                raise PrefetchProtocolError("prepared update content digest mismatch")
        if update.empty:
            if update.valid_targets != 0 or update.end_state is not None:
                self._needs_reset = True
                raise PrefetchProtocolError("empty update carries target progress")
            self._needs_reset = True  # The child rolled back; resynchronize explicitly.
            return []
        self._check_end_binding(update)
        limit = self.spec.global_batch_valid_targets
        if remaining_budget is not None:
            limit = min(limit, remaining_budget)
        if not 0 < update.valid_targets <= limit:
            self._needs_reset = True
            raise PrefetchProtocolError("prepared update exceeds requested target budget")
        self._pending = update
        self._ordinal += 1
        if remaining_budget is None or remaining_budget - update.valid_targets > 0:
            if self._outstanding < self.depth:
                ahead = (
                    None if remaining_budget is None else remaining_budget - update.valid_targets
                )
                self._request(ahead)
        return update.to_microbatches()

    def _take(self, budget: int | None) -> PreparedUpdate:
        if self._outstanding == 0:
            self._request(budget)
        begin = time.perf_counter()
        assert self._conn is not None
        # Blocked means nothing prepared had even reached the pipe yet.
        try:
            blocked = not self._ready and not self._conn.poll(0)
        except OSError as exc:
            self.close()
            raise PrefetchProducerError("producer pipe poll failed") from exc
        self._pump(lambda: bool(self._ready))
        waited = time.perf_counter() - begin
        self._stats["consumer_wait_seconds"] += waited
        self._stats["blocked_takes"] += blocked
        self._stats["takes"] += 1
        self._outstanding -= 1
        update = self._ready.popleft()
        self._stats["last_produce_seconds"] = update.produce_seconds
        self._stats["produce_seconds"] += update.produce_seconds
        return update

    @property
    def pending_update(self) -> PreparedUpdate | None:
        """The update handed to the trainer and not yet committed or rolled back."""
        return self._pending

    def commit(self) -> None:
        """Promote the consumed update's end state; the only committed-state change."""
        if self._pending is None:
            raise PrefetchProtocolError("commit without a consumed update")
        assert self._pending.end_state is not None and self._pending.end_state_digest is not None
        self._committed = self._pending.end_state
        self._committed_digest = self._pending.end_state_digest
        self._pending = None
        self._stats["commits"] += 1

    def rollback(self) -> None:
        """Discard the consumed update and all speculation; regenerate from committed."""
        budget = None if self._pending is None else self._pending.remaining_budget
        had_pending = self._pending is not None
        self._pending = None
        self._stats["rollbacks"] += 1
        if self._conn is None or self._finalizer is None or not self._finalizer.alive:
            return  # Already closed: no speculation remains to invalidate.
        self._reset(self._committed)
        if had_pending:
            self._request(budget)  # Same committed state, same budget: same update.

    def get_state(self) -> dict[str, Any]:
        """Committed state only; speculative producer progress is never exposed."""
        return copy.deepcopy(self._committed)

    def load_state(self, state_dict: dict[str, Any]) -> None:
        """Validate in the producer's batcher first; adopt only on success."""
        if self._pending is not None:
            raise PrefetchProtocolError("load_state with an unfinished update")
        candidate = copy.deepcopy(state_dict)
        self._reset(candidate)
        digest = state_digest(candidate)
        if self._reset_ack != digest:
            raise PrefetchProtocolError("producer state differs from the loaded state")
        self._committed = candidate
        self._committed_digest = digest
