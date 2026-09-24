"""Owned Python timeout diagnostics for the pinned Windows CPython runtime.

CPython 3.12.13's native dump_traceback_later can dereference a cleared code
pointer while walking another running thread. Python frame references avoid
that asynchronous native walk. Fatal-signal faulthandler remains enabled.
"""

from __future__ import annotations

import os
import sys
import threading
import traceback
from collections.abc import Generator
from types import ModuleType

import pytest


class ThreadDump:
    """One cancellable timer; its thread is joined before its descriptor closes."""

    def __init__(self, timeout: float, descriptor: int, *, exit_on_timeout: bool = False):
        self.timeout = timeout
        self.descriptor = os.dup(descriptor)
        self.exit_on_timeout = exit_on_timeout
        self.stop = threading.Event()
        self.error: Exception | None = None
        self.thread = threading.Thread(target=self._run, name="pytest-thread-dump")

    def start(self) -> None:
        self.thread.start()

    def _run(self) -> None:
        if self.stop.wait(self.timeout):
            return
        try:
            self._dump()
        except Exception as exc:
            self.error = exc  # Propagate at join; a broken diagnostic is not ignored.

    def _dump(self) -> None:
        lines = [f"Timeout ({self.timeout:g}s): Python thread snapshots\n"]
        frames = sys._current_frames()
        try:
            for ident, frame in sorted(frames.items())[:100]:
                lines.append(f"Thread 0x{ident:x} (oldest frame first):\n")
                lines.extend(traceback.format_stack(frame, limit=50))
        finally:
            frames.clear()
        payload = "".join(lines).encode("utf-8", errors="backslashreplace")[: 1024**2]
        # os.write may return a short count even for a regular duplicated fd.
        while payload:
            written = os.write(self.descriptor, payload)
            if written <= 0:
                raise OSError("timeout diagnostic write made no progress")
            payload = payload[written:]
        if self.exit_on_timeout:
            os._exit(1)

    def close(self) -> None:
        self.stop.set()
        self.thread.join()
        os.close(self.descriptor)
        if self.error is not None:
            raise RuntimeError("timeout diagnostic failed") from self.error


class SafeTimeoutPlugin:
    def __init__(self, original: ModuleType):
        self.original = original
        self.active: ThreadDump | None = None

    @pytest.hookimpl(wrapper=True, trylast=True)
    def pytest_runtest_protocol(self, item: pytest.Item) -> Generator[None, object, object]:
        timeout = float(item.config.getini("faulthandler_timeout") or 0)
        if timeout <= 0:
            return (yield)
        descriptor = item.config.stash[self.original.fault_handler_stderr_fd_key]
        self.active = ThreadDump(
            timeout,
            descriptor,
            exit_on_timeout=item.config.getini("faulthandler_exit_on_timeout"),
        )
        self.active.start()
        try:
            return (yield)
        finally:
            self._cancel()

    def _cancel(self) -> None:
        active, self.active = self.active, None
        if active is not None:
            active.close()

    @pytest.hookimpl(tryfirst=True)
    def pytest_enter_pdb(self) -> None:
        self._cancel()

    @pytest.hookimpl(tryfirst=True)
    def pytest_exception_interact(self) -> None:
        self._cancel()

    def pytest_unconfigure(self, config: pytest.Config) -> None:
        self._cancel()
        self.original.pytest_unconfigure(config)


def install_safe_timeout(config: pytest.Config) -> None:
    """Replace only the affected diagnostic plugin, after its fatal handler setup."""
    if sys.platform != "win32" or sys.version_info[:3] != (3, 12, 13):
        return
    original = config.pluginmanager.get_plugin("faulthandler")
    if original is None:
        return  # Respect explicit -p no:faulthandler.
    # pytest_configure below runs trylast: the original owns a duplicated fd
    # and an enabled fatal handler. Preserve both and delegate final cleanup.
    config.pluginmanager.unregister(original)
    config.pluginmanager.register(SafeTimeoutPlugin(original), "xlm-safe-timeout")
