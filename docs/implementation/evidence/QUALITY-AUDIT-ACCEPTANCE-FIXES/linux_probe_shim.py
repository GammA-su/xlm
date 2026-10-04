"""Pytest plugin: run the FROZEN Windows probe file unchanged on Linux.

Used with ``-p linux_probe_shim`` from a scratch working directory. It changes no
probe; it supplies the two Windows-only environment pieces the probes assume:

1. ``junction(link, target)`` (PowerShell ``New-Item -ItemType Junction``) becomes a
   directory symbolic link, the POSIX alias the output tree must refuse the same way.
2. The authored C05 fixture the probes read from the literal path
   ``F:/qa-tmp-96f38f3/quality-c050/root`` (relative on POSIX, so below the scratch
   working directory) is rebuilt with the repository's AUTHORED synthetic C05 flow
   (``scripts/c05_synthetic_flow.py``; the same flow as the ``c05_flow`` test
   fixture). No real or protected C05 material is touched.

This is an emulation; it is not a native Windows run.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

FIXTURE = Path("F:/qa-tmp-96f38f3/quality-c050/root")


def _build_fixture() -> None:
    root = Path.cwd() / FIXTURE
    if (root / "proof.json").exists():
        return
    repo = Path(__file__).resolve().parents[4]
    sys.path.insert(0, str(repo))
    from scripts.c05_authored_pilot import KEY
    from scripts.c05_synthetic_flow import KEY_ENV, decide_and_plan, prepare, run_c05

    previous = os.environ.get(KEY_ENV)
    os.environ[KEY_ENV] = KEY
    here = Path.cwd()
    os.chdir(repo)  # the flow reads repository-relative manifests
    try:
        paths = prepare(root)
        result = run_c05(paths, decide_and_plan(paths))
        spec = Path(result["proof"])
        if spec.resolve() != (root / "proof.json").resolve():
            (root / "proof.json").write_bytes(spec.read_bytes())
    finally:
        os.chdir(here)
        if previous is None:
            os.environ.pop(KEY_ENV, None)
        else:
            os.environ[KEY_ENV] = previous


def _symlink_junction(link: Path, target: Path) -> None:
    os.symlink(target, link, target_is_directory=True)


def pytest_sessionstart(session: pytest.Session) -> None:
    _build_fixture()


def pytest_collection_modifyitems(
    session: pytest.Session, config: pytest.Config, items: list[pytest.Item]
) -> None:
    for item in items:
        module = getattr(item, "module", None)
        if module is not None and hasattr(module, "junction"):
            module.junction = _symlink_junction
