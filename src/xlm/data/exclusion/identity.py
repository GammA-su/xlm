"""Local source/dependency identity, independent of corpus and benchmark content."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

from xlm.data.evidence_v2.canonical import digest


def implementation_identity() -> dict[str, str]:
    root = Path(__file__).resolve().parents[4]
    files = sorted((root / "src").rglob("*.py"))
    code = digest(
        {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    )
    dependencies = digest(
        {
            name: hashlib.sha256((root / name).read_bytes()).hexdigest()
            for name in ("pyproject.toml", "uv.lock", ".python-version")
        }
    )
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    return {
        "code_commit": result.stdout.strip(),
        "code_identity": code,
        "dependency_sha256": dependencies,
    }
