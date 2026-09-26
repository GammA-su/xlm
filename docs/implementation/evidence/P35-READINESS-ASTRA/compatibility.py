"""Read-only Git blob/environment and lazy public API observations."""

from __future__ import annotations

import ast
import hashlib
import importlib
import importlib.metadata
import json
import platform
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
BASE = "8ccb4bc764ae50861e7d6872f71a2efc545484a0"


def blob(ref: str, name: str) -> bytes:
    return subprocess.check_output(["git", "show", f"{ref}:{name}"], cwd=ROOT)


def main(output: Path) -> None:
    paths = [
        "pyproject.toml",
        "uv.lock",
        ".python-version",
        "src/xlm/comparison/promotion.py",
        "src/xlm/comparison/tracks.py",
        "src/xlm/comparison/bootstrap.py",
        "src/xlm/cli/compare_cmd.py",
        "src/xlm/comparison/recipes.py",
    ]
    checks = {}
    for name in paths:
        head, base, work = blob("HEAD", name), blob(BASE, name), (ROOT / name).read_bytes()
        checks[name] = {
            "head_equals_parent": head == base,
            "git_blob_sha256": hashlib.sha256(head).hexdigest(),
            "worktree_sha256": hashlib.sha256(work).hexdigest(),
            "worktree_equal_after_crlf_normalization": work.replace(b"\r\n", b"\n") == head,
        }
        assert head == base and work.replace(b"\r\n", b"\n") == head
    import xlm.evaluation as evaluation

    importlib.import_module("xlm.evaluation.cadence")
    previous = ast.parse(blob(BASE, "src/xlm/evaluation/__init__.py"))
    old_exports = next(
        ast.literal_eval(node.value)
        for node in previous.body
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets)
    )
    assert evaluation.__all__ == old_exports

    torch_after_pure_import = "torch" in sys.modules
    exports = {name: getattr(evaluation, name).__module__ for name in evaluation.__all__}
    assert not torch_after_pure_import
    assert set(evaluation.__all__) <= set(dir(evaluation))
    for name, module in exports.items():
        assert getattr(evaluation, name) is getattr(importlib.import_module(module), name)
    import torch

    report = {
        "python": sys.version,
        "executable": sys.executable,
        "platform": platform.platform(),
        "torch": torch.__version__,
        "cuda_runtime": torch.version.cuda,
        "gpu": torch.cuda.get_device_name(0),
        "packages": {
            name: importlib.metadata.version(name)
            for name in ("numpy", "lm_eval", "pytest", "ruff", "mypy", "pytest-xdist")
        },
        "git_checks": checks,
        "torch_loaded_by_pure_import": torch_after_pure_import,
        "public_exports": exports,
        "nvidia_smi": subprocess.check_output(["nvidia-smi"], text=True),
    }
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print("Git blob invariants and lazy public exports verified; environment captured.")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
