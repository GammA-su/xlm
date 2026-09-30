"""Offline hashes for this readiness package; no source text or network access."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parent
    repo = root.parents[3]
    # PowerShell 5.1 output redirection is UTF-16. Keep review logs plain UTF-8.
    for path in (root / "logs").glob("*.log"):
        raw = path.read_bytes()
        encoding = "utf-16" if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8"
        normalized = "\n".join(line.rstrip() for line in raw.decode(encoding).splitlines()) + "\n"
        path.write_bytes(normalized.encode("utf-8"))
    paths = sorted(
        p
        for p in root.rglob("*")
        if p.is_file() and p.name != "artifact-manifest.json" and "__pycache__" not in p.parts
    )
    names = subprocess.check_output(
        ["git", "diff-tree", "--no-commit-id", "--name-only", "-r", "57cb42f"],
        cwd=repo,
        text=True,
    ).splitlines()
    names += [
        "pyproject.toml",
        "uv.lock",
        ".python-version",
        "recipes/mixtures/mix01.yaml",
        "recipes/mixtures/mix01_quotas_6b.yaml",
        "recipes/mixtures/mix01_views.yaml",
        "recipes/selectors/essential_web_selector_sweep_v1.yaml",
        "scripts/essential_web_selector_sweep.py",
    ]

    def descriptor(path: Path) -> dict[str, str | int]:
        raw = path.read_bytes()
        return {
            "path": path.relative_to(repo).as_posix(),
            "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
        }

    result = {
        "kind": "essential_web_readiness_artifact_manifest",
        "code_commit": "57cb42f",
        "artifacts": [descriptor(p) for p in paths],
        "code_and_frozen_inputs": [descriptor(repo / name) for name in sorted(names)],
        "self_hash": "excluded to avoid self-reference",
    }
    temporary = root / "artifact-manifest.json.tmp"
    temporary.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    temporary.replace(root / "artifact-manifest.json")
    print(f"sealed {len(paths)} artifacts and {len(names)} code/frozen inputs")


if __name__ == "__main__":
    main()
