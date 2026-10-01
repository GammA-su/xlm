"""Frozen local-processing growth limits and write-before-charge enforcement."""

from __future__ import annotations

import json
import os
import shutil
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

MIB = 1024**2


class GrowthLimitError(RuntimeError):
    """A local output would exceed its authorized storage envelope."""


class ProcessingGrowth(BaseModel):
    """Final output shares one pool; only actually coexisting temporaries add space."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    version: int = Field(default=1, ge=1, le=1)
    output_bytes: int = Field(gt=0)
    source_max_bytes: int = Field(default=0, ge=0)
    metadata_bytes: int = Field(default=MIB, gt=0, le=MIB)
    progress_bytes: int = Field(default=4096, gt=0, le=4096)
    state_bytes: int = Field(default=MIB, gt=0, le=MIB)
    run_metadata_bytes: int = Field(default=8 * MIB, gt=0, le=8 * MIB)
    event_bytes: int = Field(default=MIB, gt=0, le=MIB)

    @property
    def processing_peak(self) -> int:
        # Final files plus an exclusive-publication hard-link temporary (count
        # both paths logically), and old/new progress snapshots.
        return self.output_bytes + self.metadata_bytes + 2 * self.progress_bytes

    @property
    def state_peak(self) -> int:
        return 2 * self.state_bytes

    @property
    def run_peak(self) -> int:
        return 2 * self.run_metadata_bytes + self.event_bytes

    def for_source(self, verified_bytes: int) -> ProcessingGrowth:
        """Use unused source allowance within the frozen durable sum."""
        if verified_bytes < 0 or (self.source_max_bytes and verified_bytes > self.source_max_bytes):
            raise GrowthLimitError("verified source exceeds its frozen byte bound")
        return self.model_copy(
            update={
                "output_bytes": self.output_bytes + max(0, self.source_max_bytes - verified_bytes),
                "source_max_bytes": 0,
            }
        )


class OutputBudget:
    """One worker's aggregate final-output pool, including buffered document bytes.

    Precharges are conservative after an I/O error; that worker must stop.
    Progress replacement has its separate frozen two-snapshot reservation.
    """

    def __init__(
        self, limit: int, *, disk_path: Path | None = None, min_free_bytes: int = 0
    ) -> None:
        if limit < 1:
            raise ValueError("output budget must be positive")
        self.limit = limit
        self.used = 0
        self.disk_path, self.min_free_bytes = disk_path, min_free_bytes

    def charge(self, amount: int) -> None:
        if amount < 0 or self.used + amount > self.limit:
            raise GrowthLimitError("aggregate processing output exceeds its byte ceiling")
        if self.disk_path is not None:
            path = self.disk_path
            while not path.exists():
                path = path.parent
            if shutil.disk_usage(path).free - amount < self.min_free_bytes:
                raise GrowthLimitError("processing write would consume physical free-space reserve")
        self.used += amount

    def write(self, path: Path, data: bytes, ceiling: int) -> None:
        if len(data) > ceiling:
            raise GrowthLimitError(f"{path.name}: output exceeds its byte ceiling")
        self.charge(len(data))
        with path.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())


def bounded_json(value: Mapping[str, Any], ceiling: int) -> bytes:
    data = (json.dumps(dict(value), indent=2, sort_keys=True) + "\n").encode("utf-8")
    if len(data) > ceiling:
        raise GrowthLimitError("metadata exceeds its byte ceiling")
    return data


def check_result(result: Mapping[str, Any], limits: Mapping[str, Any], source_bytes: int) -> None:
    """Shared benchmark/production final check, complementing pre-write enforcement."""
    growth = ProcessingGrowth.model_validate(limits["processing_growth"]).for_source(source_bytes)
    output = int(result["processing_output_bytes"])
    if (
        int(result["canonical_bytes"]) > int(limits["max_canonical_bytes_per_file"])
        or output > growth.output_bytes
        or source_bytes + output > int(limits["max_durable_bytes_per_file"])
    ):
        raise GrowthLimitError("completed unit exceeds its canonical/durable output ceiling")
