"""Bounded external sort of fixed-width NumPy records (no pickle, no Python tuples).

Records are buffered up to ``run_records``; a full buffer is sorted with
``np.lexsort`` and spilled to a run file (charged to the working-index ledger,
fsync not required: runs are rebuilt after any crash). ``merged`` yields globally
sorted chunks. With one run nothing is spilled. Otherwise a single k-way merge
pass reads each run in blocks: every record not greater than the smallest block
tail among runs that still have unread data is safe to emit, so each step emits
at least one whole block and RAM stays bounded by ``k`` blocks.

Sort keys are unsigned integer fields compared lexicographically in the given
order; ties keep no particular order, so callers include a unique field in keys.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt

from xlm.data.exclusion.factstore import IndexLedger
from xlm.data.exclusion.policy import C05Error


def _order(records: npt.NDArray[Any], keys: Sequence[str]) -> npt.NDArray[np.intp]:
    # np.lexsort treats the last key as primary.
    order: npt.NDArray[np.intp] = np.lexsort([records[name] for name in reversed(keys)])
    return order


def _not_greater(records: npt.NDArray[Any], bound: Any, keys: Sequence[str]) -> int:
    """Count of the leading records <= ``bound`` (records are sorted by ``keys``)."""
    less = np.zeros(records.shape[0], dtype=bool)
    equal = np.ones(records.shape[0], dtype=bool)
    for name in keys:
        column, value = records[name], bound[name]
        less |= equal & (column < value)
        equal &= column == value
    return int(np.count_nonzero(less | equal))


class ExternalSorter:
    """Sort an unbounded stream of records in bounded memory."""

    def __init__(
        self,
        dtype: np.dtype[Any],
        keys: Sequence[str],
        *,
        run_records: int,
        directory: Path,
        ledger: IndexLedger,
        check: Callable[[], None],
        block_records: int = 1 << 18,
        max_runs: int = 16,
    ) -> None:
        if run_records < 1 or block_records < 1:
            raise C05Error("external sort buffers must hold at least one record")
        self.dtype, self.keys = dtype, tuple(keys)
        self.run_records, self.block_records = run_records, block_records
        self.directory, self.ledger, self.check = directory, ledger, check
        self.max_runs = max_runs
        self.buffer: list[npt.NDArray[Any]] = []
        self.buffered = 0
        self.runs: list[Path] = []
        self.total_runs = 0
        self.records = 0
        self.spilled_bytes = 0

    def add(self, records: npt.NDArray[Any]) -> None:
        if records.dtype != self.dtype:
            raise C05Error("external sort record type mismatch")
        start = 0
        while start < records.shape[0]:
            take = min(records.shape[0] - start, self.run_records - self.buffered)
            self.buffer.append(records[start : start + take])
            self.buffered += take
            self.records += take
            start += take
            if self.buffered == self.run_records:
                self._spill()

    def _sorted_buffer(self) -> npt.NDArray[Any]:
        data = np.concatenate(self.buffer) if self.buffer else np.zeros(0, self.dtype)
        self.buffer, self.buffered = [], 0
        result: npt.NDArray[Any] = data[_order(data, self.keys)]
        return result

    def _spill(self) -> None:
        self.check()
        if len(self.runs) >= self.max_runs:
            raise C05Error("external sort run ceiling; the RAM ceiling is too small")
        data = self._sorted_buffer()
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / f"run-{len(self.runs):05d}.bin"
        raw = data.tobytes()
        self.ledger.charge(len(raw))
        with path.open("xb") as stream:
            stream.write(raw)
        self.spilled_bytes += len(raw)
        self.runs.append(path)
        self.total_runs += 1

    def merged(self, chunk_records: int = 1 << 20) -> Iterator[npt.NDArray[Any]]:
        """Yield the sorted records in chunks; spill files are removed afterwards."""
        if not self.runs:
            data = self._sorted_buffer()
            for start in range(0, data.shape[0], chunk_records):
                self.check()
                yield data[start : start + chunk_records]
            return
        if self.buffered:
            self._spill()
        try:
            yield from self._merge()
        finally:
            self.cleanup()

    def _merge(self) -> Iterator[npt.NDArray[Any]]:
        sources = [np.memmap(path, dtype=self.dtype, mode="r") for path in self.runs]
        cursors = [0] * len(sources)
        block = self.block_records
        try:
            while True:
                self.check()
                heads = [
                    source[cursor : cursor + block]
                    for source, cursor in zip(sources, cursors, strict=True)
                ]
                live = [i for i, head in enumerate(heads) if head.shape[0]]
                if not live:
                    return
                bounding = [i for i in live if cursors[i] + heads[i].shape[0] < sources[i].shape[0]]
                taken: list[npt.NDArray[Any]] = []
                if bounding:
                    tails = np.concatenate([heads[i][-1:] for i in bounding])
                    bound = tails[_order(tails, self.keys)[0]]
                    for i in live:
                        count = _not_greater(heads[i], bound, self.keys)
                        if count:
                            taken.append(np.array(heads[i][:count]))
                            cursors[i] += count
                else:
                    for i in live:
                        taken.append(np.array(heads[i]))
                        cursors[i] += heads[i].shape[0]
                data = np.concatenate(taken)
                yield data[_order(data, self.keys)]
        finally:
            del sources

    def cleanup(self) -> None:
        for path in self.runs:
            if path.exists():
                size = path.stat().st_size
                os.remove(path)
                self.ledger.release(size)
        self.runs = []
