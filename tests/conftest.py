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
def installed_eval_runtime(
    tmp_path_factory: pytest.TempPathFactory,
) -> Generator[None, None, None]:
    """Import optional harness packages only when their tests actually execute."""
    cache = tmp_path_factory.mktemp("installed-eval-cache")
    with pytest.MonkeyPatch.context() as patch:
        for name, path in {
            "HF_HOME": cache,
            "HF_HUB_CACHE": cache / "hub",
            "HF_DATASETS_CACHE": cache / "datasets",
            "HF_MODULES_CACHE": cache / "modules",
        }.items():
            patch.setenv(name, str(path))
        for name in ("HF_HUB_OFFLINE", "HF_DATASETS_OFFLINE", "TRANSFORMERS_OFFLINE"):
            patch.setenv(name, "1")
        pytest.importorskip("lm_eval")
        pytest.importorskip("datasets")
        yield


def pytest_itemcollected(item: pytest.Item) -> None:
    """Classify before marker selection; unaudited serial cases stay exclusive."""
    from suite_domains import DOMAINS

    excluded = {"performance", "scale", "cuda", "network", "operator", "environment_setup"}
    markers = {mark.name for mark in item.iter_markers()}
    if markers & excluded or not markers & {"serial", "optional_dependency"}:
        return
    domain = DOMAINS.get(item.nodeid)
    if domain is None:
        if "serial" in markers and "optional_dependency" not in markers:
            item.add_marker(pytest.mark.serial_core)
        item.add_marker(pytest.mark.serial_exclusive)
        return
    if domain.tier == "serial_core":
        item.add_marker(pytest.mark.serial_core)
    elif domain.tier == "serial_heavy":
        item.add_marker(pytest.mark.serial_heavy)
    if domain.exclusive:
        item.add_marker(pytest.mark.serial_exclusive)
    else:
        item.add_marker(pytest.mark.xdist_group(name=domain.group))


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(session: pytest.Session, items: list[pytest.Item]) -> None:
    """Fail closed for unsafe scheduling; never silently deselect a required node."""
    worker = hasattr(session.config, "workerinput")
    if not worker and not session.config.getoption("numprocesses", default=0):
        return
    grouped = (
        session.config.getoption("loadgroup", default=False)
        if worker
        else session.config.getoption("dist") == "loadgroup"
    )
    for item in items:
        if item.get_closest_marker("serial_exclusive"):
            message = f"{item.nodeid} requires -n 0 (exclusive or unaudited)"
            session.shouldfail = message
            raise pytest.UsageError(message)
        constrained = any(
            item.get_closest_marker(name) for name in ("serial", "optional_dependency")
        )
        if constrained and (not item.get_closest_marker("xdist_group") or not grouped):
            message = f"{item.nodeid} requires audited --dist=loadgroup or -n 0"
            session.shouldfail = message
            raise pytest.UsageError(message)


@pytest.fixture(autouse=True)
def private_domain_home(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    if any(
        request.node.get_closest_marker(name)
        for name in ("serial_core", "serial_heavy", "optional_dependency")
    ):
        path = cast(Path, request.getfixturevalue("tmp_path"))
        monkeypatch.setenv("XLM_HOME", str(path / "fallback-home"))


@pytest.hookimpl(trylast=True)
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
    from thread_diagnostics import install_safe_timeout

    install_safe_timeout(config)


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
