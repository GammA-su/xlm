"""Pytest fixtures and configuration for offline test suite."""

from __future__ import annotations

import socket
from collections.abc import Generator
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def block_network(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> None:
    """Enforce strict offline isolation by blocking socket connections.

    Tests marked with @pytest.mark.network are permitted network access.
    """
    if "network" in request.keywords:
        return

    original_connect = socket.socket.connect

    def guarded_connect(self: socket.socket, address: tuple[str, int] | str) -> None:
        # Allow loopback/unix domain connections if needed
        if isinstance(address, tuple):
            host = address[0]
            if host in ("127.0.0.1", "localhost", "::1"):
                original_connect(self, address)
                return
        raise RuntimeError(
            f"Network access denied in offline tests! Attempted connection to: {address}"
        )

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)


@pytest.fixture
def isolated_xlm_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Generator[Path, None, None]:
    """Provide a clean, isolated temporary directory for XLM_HOME."""
    home_dir = tmp_path / "xlm_test_home"
    monkeypatch.setenv("XLM_HOME", str(home_dir))
    yield home_dir
