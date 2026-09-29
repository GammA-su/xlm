"""Preserve user edits, prepend review status, and bind review evidence."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]


def main() -> None:
    baseline = json.loads((HERE / "baseline.json").read_text())
    assert subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip() == baseline["head"]
    for name, digest in baseline["prior_files_sha256"].items():
        if name != "docs/implementation/STATUS.md":
            assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == digest, name
    assert not Path("G:/Project/xlm-evidence-v4/essential-web").exists()
    assert not subprocess.check_output(["git", "diff", "--", "src", "tests", "scripts", "pyproject.toml", "uv.lock", ".python-version"], cwd=ROOT)
    staged = subprocess.check_output(["git", "diff", "--cached", "--name-only"], cwd=ROOT, text=True).splitlines()
    assert all(name.startswith("docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V4.0-FINAL-RECERTIFICATION/") or name == "docs/implementation/reports/ESSENTIAL-WEB-EVIDENCE-V4.0-FINAL-RECERTIFICATION.md" for name in staged)
    status = ROOT / "docs/implementation/STATUS.md"
    notice = (
        "> **ESSENTIAL-WEB EVIDENCE V4.0 FINAL B01/B02 RECERTIFICATION (2026-09-29, offline):\n"
        "> V4 NARROW PHASE-P REVIEW PASSED. M: PASS. T: PASS.** Repair reviewed:\n"
        "> `a9f89c0d13ef01e9ab387104c4acd4da948d2366`. Frozen identities unchanged.\n"
        "> Independent real-parser probes: B01 12/12/12 physical/temp/SQLite bytes;\n"
        "> B02 PAR1 at 60 s, TIMEOUT at 120 s, all bytes retained; 119/120/121 boundaries pass.\n"
        "> Focused Windows Python 3.12.13 suite: 180 passed, no failures/skips.\n"
        "> No network, corpus text, real-root creation, Phase D or implementation changes.\n"
        "> [Final review and exact next operator command](reports/ESSENTIAL-WEB-EVIDENCE-V4.0-FINAL-RECERTIFICATION.md),\n"
        "> [commands/evidence](evidence/ESSENTIAL-WEB-EVIDENCE-V4.0-FINAL-RECERTIFICATION/COMMANDS.md).\n"
        "> Next: operator may run the reviewed digest-confirmed Phase-P command; not executed here.\n"
        "> This supersedes the historical B01/B02 blocking notice below.\n\n"
    ).replace("\n", "\r\n").encode()
    temporary = status.with_name("STATUS.md.final-review.tmp")
    if temporary.exists():  # recover this review's preserved intended bytes after WinError 5
        intended = temporary.read_bytes()
        assert intended.startswith(notice)
        previous = intended[len(notice):]
        assert hashlib.sha256(previous).hexdigest() == baseline["prior_files_sha256"]["docs/implementation/STATUS.md"]
        current = status.read_bytes()
        assert current.replace(b"\r\n", b"\n") == intended.replace(b"\r\n", b"\n")
        # Atomic replacement was refused twice by Windows. The preserved temporary
        # remains available until the exact-byte in-place documentation write verifies.
        with status.open("r+b") as handle:
            handle.write(intended)
            handle.truncate()
            handle.flush()
            os.fsync(handle.fileno())
        assert status.read_bytes() == intended
        temporary.unlink()
    else:
        current = status.read_bytes()
        assert current.startswith(notice)
        previous = current[len(notice):]
        assert hashlib.sha256(previous).hexdigest() == baseline["prior_files_sha256"]["docs/implementation/STATUS.md"]
    assert status.read_bytes()[len(notice):] == previous
    result = dict(starting_head=baseline["head"], prior_files_unchanged_except_status_prefix=True, status_previous_bytes_preserved=len(previous), status_prefix_bytes=len(notice), real_root_absent=True, implementation_diff_empty=True, index_contains_only_review_paths=True)
    (HERE / "preservation.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    files = [p for p in HERE.iterdir() if p.is_file() and p.name != "artifact_manifest.json"]
    files.append(ROOT / "docs/implementation/reports/ESSENTIAL-WEB-EVIDENCE-V4.0-FINAL-RECERTIFICATION.md")
    manifest = {p.relative_to(ROOT).as_posix(): dict(bytes=p.stat().st_size, sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in sorted(files)}
    (HERE / "artifact_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
