"""Pytest fixtures and configuration for offline test suite."""

from __future__ import annotations

import os
import socket
from collections.abc import Generator
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest

if TYPE_CHECKING:
    from runtime_fixture import RuntimeSeed
    from xlm.experiments.plans import ExecutablePlan


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--fresh-runtime", action="store_true", help="Disable queue fixture runtime reuse"
    )


@pytest.fixture(scope="session")
def runtime_seed() -> Generator[RuntimeSeed, None, None]:
    from runtime_fixture import RuntimeSeed

    lock = Path(__file__).resolve().parents[1] / "uv.lock"
    seed = RuntimeSeed.capture(lock)
    yield seed
    seed.verify_unchanged(lock)


@pytest.fixture(autouse=True)
def queue_runtime_prerequisite(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> Generator[None, None, None]:
    from runtime_fixture import RUNTIME_FIXTURE_NODES

    nodeid = request.node.nodeid.rsplit("@p30b-", 1)[0]
    if nodeid not in RUNTIME_FIXTURE_NODES or request.config.getoption("--fresh-runtime"):
        yield
        return
    import test_queue

    seed = cast("RuntimeSeed", request.getfixturevalue("runtime_seed"))
    hits = 0

    def freeze(plan: ExecutablePlan, snapshot: Path, extras: list[str]) -> ExecutablePlan:
        nonlocal hits
        result, used = seed.freeze(plan, snapshot, extras)
        hits += int(used)
        return result

    monkeypatch.setattr(test_queue, "freeze_execution", freeze)
    yield
    request.node.user_properties.extend(
        [("runtime_fixture_cache_hits", hits), ("runtime_fixture_key", seed.key)]
    )


@pytest.fixture(scope="session")
def installed_eval_runtime() -> None:
    """Import optional harness packages only when their tests actually execute."""
    pytest.importorskip("lm_eval")
    pytest.importorskip("datasets")


def pytest_configure(config: pytest.Config) -> None:
    """Give each xdist worker its own XLM_HOME before anything resolves paths.

    Tests that set XLM_HOME per test (the majority) are unaffected; this only
    changes the fallback home shared by tests that rely on the environment.
    Without it, concurrent workers share one home directory and its ledgers.
    """
    worker = getattr(config, "workerinput", {}).get("workerid", None)
    if worker is not None:
        base = os.environ.get("XLM_HOME")
        if base:
            os.environ["XLM_HOME"] = str(Path(base) / f"worker-{worker}")


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
