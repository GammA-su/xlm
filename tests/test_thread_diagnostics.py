"""The timeout diagnostic stays active and owns its thread/descriptor lifetime."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import traceback
from pathlib import Path

import pytest

from thread_diagnostics import ThreadDump


def test_timeout_reports_python_frames_and_joins(tmp_path: Path) -> None:
    path = tmp_path / "trace.log"
    with path.open("wb") as output:
        timer = ThreadDump(0, output.fileno())
        timer.start()
        timer.thread.join(10)
        assert not timer.thread.is_alive()
        timer.close()
    assert "test_timeout_reports_python_frames_and_joins" in path.read_text()
    assert "Python thread snapshots" in path.read_text()
    with pytest.raises(OSError):
        os.fstat(timer.descriptor)


def test_cancelled_timer_is_joined_without_dump(tmp_path: Path) -> None:
    with (tmp_path / "trace.log").open("wb") as output:
        timer = ThreadDump(60, output.fileno())
        timer.start()
        timer.close()
        assert not timer.thread.is_alive()
        assert output.tell() == 0


def test_descriptor_survives_original_stream_closure(tmp_path: Path) -> None:
    path = tmp_path / "trace.log"
    with path.open("wb") as output:
        timer = ThreadDump(0, output.fileno())
    timer.start()
    timer.thread.join(10)
    assert not timer.thread.is_alive()
    timer.close()
    assert "Python thread snapshots" in path.read_text()


def test_close_waits_for_in_progress_dump(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()

    def format_stack(*args: object, **kwargs: object) -> list[str]:
        entered.set()
        assert release.wait(10)
        return ["authored frame\n"]

    monkeypatch.setattr(traceback, "format_stack", format_stack)
    with (tmp_path / "trace.log").open("wb") as output:
        timer = ThreadDump(0, output.fileno())
        timer.start()
        assert entered.wait(10)

        def close() -> None:
            timer.close()
            finished.set()

        closer = threading.Thread(target=close)
        closer.start()
        try:
            assert not finished.is_set()
            os.fstat(timer.descriptor)
        finally:
            release.set()
            closer.join(10)
        assert finished.is_set() and not timer.thread.is_alive()


def test_dump_failure_propagates_after_join(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken_stack(*args: object, **kwargs: object) -> list[str]:
        raise ValueError("authored diagnostic failure")

    monkeypatch.setattr(traceback, "format_stack", broken_stack)
    with (tmp_path / "trace.log").open("wb") as output:
        timer = ThreadDump(0, output.fileno())
        timer.start()
        timer.thread.join(10)
        assert not timer.thread.is_alive()
        with pytest.raises(RuntimeError, match="timeout diagnostic failed") as exc:
            timer.close()
        assert isinstance(exc.value.__cause__, ValueError)
        with pytest.raises(OSError):
            os.fstat(timer.descriptor)


@pytest.mark.parametrize("fail", [False, True])
def test_pytest_timeout_preserves_result_and_teardown(tmp_path: Path, fail: bool) -> None:
    # A private authored pytest session exercises plugin installation, timeout,
    # session-fixture teardown, and an actual assertion failure without xdist.
    (tmp_path / "conftest.py").write_text(
        "import pytest\nfrom thread_diagnostics import install_safe_timeout\n"
        "@pytest.hookimpl(trylast=True)\n"
        "def pytest_configure(config): install_safe_timeout(config)\n"
        "@pytest.fixture(scope='session', autouse=True)\n"
        "def verify():\n"
        "    yield\n"
        "    from pathlib import Path\n"
        "    Path(__file__).with_name('verified').write_text('full teardown')\n"
    )
    (tmp_path / "test_authored.py").write_text(
        "def test_busy_frames():\n"
        "    import time, faulthandler\n"
        "    from pathlib import Path\n"
        "    assert faulthandler.is_enabled()\n"
        "    end = time.monotonic() + 0.1\n"
        "    while time.monotonic() < end:\n"
        "        Path('root/child').relative_to(Path('root'))\n"
        f"    assert {not fail!r}\n"
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(tmp_path / "test_authored.py"),
            "-n",
            "0",
            "-q",
            "-s",
            "-o",
            "faulthandler_timeout=0.001",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == int(fail), result.stdout + result.stderr
    if sys.platform == "win32" and sys.version_info[:3] == (3, 12, 13):
        assert "Python thread snapshots" in result.stderr
    assert (tmp_path / "verified").read_text() == "full teardown"
