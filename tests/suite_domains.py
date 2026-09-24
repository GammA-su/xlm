"""Data-only, exact-node resource-domain audit for bounded pytest scheduling."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType


@dataclass(frozen=True)
class Domain:
    tier: str
    group: str
    exclusive: bool
    reason: str


def _load() -> Mapping[str, Domain]:
    document = json.loads(Path(__file__).with_suffix(".json").read_text(encoding="utf-8"))
    if set(document) != {"schema_version", "nodes"} or document["schema_version"] != 1:
        raise ValueError("unsupported test domain audit")
    result = {}
    for nodeid, row in document["nodes"].items():
        if (
            not isinstance(nodeid, str)
            or set(row) != {"tier", "group", "exclusive", "reason"}
            or row["tier"] not in {"serial_core", "serial_heavy", "optional"}
            or not isinstance(row["group"], str)
            or re.fullmatch(r"p30b-[a-zA-Z0-9_-]+", row["group"]) is None
            or type(row["exclusive"]) is not bool
            or not isinstance(row["reason"], str)
            or not row["reason"]
        ):
            raise ValueError(f"invalid test domain audit entry: {nodeid}")
        result[nodeid] = Domain(**row)
    return MappingProxyType(result)


DOMAINS = _load()
