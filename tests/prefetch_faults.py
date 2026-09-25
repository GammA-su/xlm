"""Picklable failure injection for the spawned prefetch producer (tests only).

``functools.partial(faulty_batcher, stage=..., at=..., marker=...)`` is passed as
``ProducerSpec.factory``. Each fault fires once per marker file, so a restarted
producer resumes normally and must regenerate the interrupted update exactly.
"""

from __future__ import annotations

import dataclasses
import os
from typing import Any

import xlm.data.sampling.prefetch as prefetch
from xlm.data.sampling.prefetch import ProducerSpec
from xlm.data.sampling.stream import MixtureBatcher


def _claim(marker: str) -> bool:
    try:
        os.close(os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
    except FileExistsError:
        return False
    return True


def _fire(stage: str, marker: str) -> None:
    if not _claim(marker):
        return
    if stage == "exit":
        os._exit(17)
    raise RuntimeError(f"injected {stage} failure")


def faulty_batcher(spec: ProducerSpec, *, stage: str, at: int, marker: str) -> MixtureBatcher:
    """Build the real batcher, failing once at ``stage`` during production ``at``."""
    if stage == "startup":
        _fire(stage, marker)
    batcher = dataclasses.replace(spec, factory=None).build()
    produced = 0
    next_step = batcher.next_step_microbatches
    select = batcher.scheduler.select_source
    pack = batcher._pack_window
    encode = prefetch._encode
    windows = {"sampling": 0, "packing": 0}

    def counted_windows(function: Any, target: str) -> Any:
        def wrapped(*args: Any, **kwargs: Any) -> Any:
            windows[target] += 1
            # Fail on the second window, after speculative progress already exists.
            if produced == at and stage == target and windows[target] == 2:
                _fire(stage, marker)
            return function(*args, **kwargs)

        return wrapped

    def production(remaining_budget: int | None = None) -> Any:
        nonlocal produced
        windows.update(sampling=0, packing=0)
        if produced == at and stage == "exit":
            _fire(stage, marker)
        try:
            return next_step(remaining_budget)
        finally:
            produced += 1

    def encoding(*args: Any, **kwargs: Any) -> Any:
        if produced - 1 == at and stage == "encode":
            _fire(stage, marker)
        return encode(*args, **kwargs)

    batcher.scheduler.select_source = counted_windows(select, "sampling")  # type: ignore[method-assign]
    batcher._pack_window = counted_windows(pack, "packing")  # type: ignore[method-assign]
    batcher.next_step_microbatches = production  # type: ignore[method-assign]
    prefetch._encode = encoding  # type: ignore[assignment]  # this child process only
    return batcher
