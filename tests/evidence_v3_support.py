"""Shared offline helpers for the Evidence-v3 Phase-P test suites.

Every epoch built here is SYNTHETIC: authored in-memory Parquet files, a
fake HTTPS-like server, a disposable temporary root and a controllable
clock. Runtime bindings in the authorization fixture are probed from the
actual interpreter, so they pass only where the real validator agrees.
"""

from __future__ import annotations

import socket
from pathlib import Path
from typing import Any

import pytest

from xlm.data.evidence_v2 import canonical
from xlm.data.evidence_v3 import executor, synthetic

REPO = Path(__file__).resolve().parents[1]


def block_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_: Any, **__: Any) -> None:
        raise AssertionError("network access attempted in an offline test")

    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)


def build(tmp_path: Path, **kwargs: Any) -> synthetic.SyntheticEpoch:
    return synthetic.build_epoch(REPO, tmp_path, **kwargs)


def genesis(ep: synthetic.SyntheticEpoch) -> dict[str, Any]:
    return executor.perform_genesis(**ep.paths(), harness=ep.harness())


def execute(ep: synthetic.SyntheticEpoch, **kwargs: Any) -> dict[str, Any]:
    return executor.execute_phase_p(**ep.paths(), harness=ep.harness(), **kwargs)


def inspect(ep: synthetic.SyntheticEpoch) -> dict[str, Any]:
    return executor.inspect_epoch(ep.root)


def load_json(path: Path) -> dict[str, Any]:
    value: dict[str, Any] = canonical.loads_bytes_strict(path.read_bytes())
    return value


def write_json(path: Path, body: dict[str, Any]) -> None:
    path.write_bytes(canonical.canonical_bytes(body))


def m_requests(ep: synthetic.SyntheticEpoch) -> int:
    return int(inspect(ep)["arms"]["M"]["requests"])
