"""Read-only whole-review inventories; hash bytes without interpreting corpus data."""
from __future__ import annotations

import hashlib
import json
import platform
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]
PARENT = Path(r"G:\Project\xlm-evidence-v4.1\essential-web")
DROOT = Path(r"G:\Project\xlm-evidence-v4.1\essential-web-phase-d")


def inventory(root: Path) -> dict:
    result = {}
    for path in [root, *sorted(root.rglob("*"))]:
        stat = path.stat()
        result[path.relative_to(root).as_posix()] = {
            "bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns,
            "sha256": hashlib.file_digest(path.open("rb"), "sha256").hexdigest()
            if path.is_file() else None,
        }
    return result


def main() -> None:
    mode = sys.argv[1]
    assert mode in ("before", "after")
    assert PARENT.is_dir() and not DROOT.exists()
    parent = inventory(PARENT)
    if mode == "before":
        status = subprocess.check_output(["git", "status", "--porcelain", "-uall"], cwd=REPO).decode()
        paths = [line[3:] for line in status.splitlines() if line[3:] != HERE.relative_to(REPO).as_posix()
                 and not line[3:].startswith(HERE.relative_to(REPO).as_posix() + "/")]
        saved = {p: (REPO / p).read_bytes().hex() for p in paths if (REPO / p).is_file()}
        # Keep only hashes except STATUS, whose exact pre-existing prefix will be checked.
        preserved = {p: {"bytes": len(bytes.fromhex(raw)),
                         "sha256": hashlib.sha256(bytes.fromhex(raw)).hexdigest()}
                     for p, raw in saved.items()}
        (HERE / "user-files-before.json").write_text(json.dumps(preserved, indent=2) + "\n")
        (HERE / "status-before.bin").write_bytes((REPO / "docs/implementation/STATUS.md").read_bytes())
        (HERE / "git-status-before.txt").write_text(status)
    else:
        assert parent == json.loads((HERE / "parent-before.json").read_text()), "parent mutation"
        preserved = json.loads((HERE / "user-files-before.json").read_text())
        for rel, binding in preserved.items():
            raw = (REPO / rel).read_bytes()
            if rel == "docs/implementation/STATUS.md":
                assert (HERE / "status-before.bin").read_bytes() in raw
            else:
                assert {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()} == binding, rel
    (HERE / f"parent-{mode}.json").write_text(json.dumps(parent, sort_keys=True, indent=2) + "\n")
    print(json.dumps({"mode": mode, "parent_entries": len(parent),
                      "parent_file_bytes": sum(x["bytes"] for x in parent.values() if x["sha256"]),
                      "parent_inventory_sha256": hashlib.sha256(json.dumps(parent, sort_keys=True).encode()).hexdigest(),
                      "phase_d_root_exists": DROOT.exists(), "python": sys.version,
                      "platform": platform.platform()}, indent=2))


if __name__ == "__main__":
    main()
