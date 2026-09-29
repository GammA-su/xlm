"""Preserve existing status bytes, add this review notice, and verify no code drift."""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

OUT = Path(__file__).resolve().parent
REPO = OUT.parents[3]
status = REPO / "docs/implementation/STATUS.md"
original = status.read_bytes()
notice = (
    "> **ESSENTIAL-WEB EVIDENCE V4.0 NARROW PHASE-P REVIEW (2026-09-29, offline):\r\n"
    "> BLOCKED for both M and T.** Frozen identities/scientific membership match.\r\n"
    "> B01: production HTTP partial-read bytes are lost from accounting. B02:\r\n"
    "> buffered physical reads can exceed the v4 120-second attempt deadline.\r\n"
    "> 157 v4 + 63 science tests passed; four independent diagnostic assertions\r\n"
    "> failed across those two mechanisms. Ruff/format/mypy passed. No implementation\r\n"
    "> change, network, corpus text, real root, Phase D or push.\r\n"
    "> [Review and next repair prompt](reports/ESSENTIAL-WEB-EVIDENCE-V4.0-REVIEW.md),\r\n"
    "> [commands/evidence](evidence/ESSENTIAL-WEB-EVIDENCE-V4.0-REVIEW/COMMANDS.md).\r\n"
    "> Next: request B01/B02 repair and focused offline recertification; do not run Phase P.\r\n\r\n"
).encode("utf-8")
assert not original.startswith(notice), "closeout already applied"
status.write_bytes(notice + original)
assert status.read_bytes()[len(notice):] == original
verified = json.loads((OUT / "independent-verification.json").read_text(encoding="utf-8"))
for rel, expected in verified["source_hashes"].items():
    assert hashlib.sha256((REPO / rel).read_bytes()).hexdigest() == expected, rel
root = Path(verified["root"])
assert not root.exists()
changes = subprocess.check_output([
    "git", "diff", "--name-only", "--", "src", "scripts", "tests",
    "pyproject.toml", "uv.lock", ".python-version",
], cwd=REPO, text=True)
assert not changes
summary = {
    "status_original_bytes_preserved": True,
    "status_original_sha256": hashlib.sha256(original).hexdigest(),
    "notice_bytes": len(notice),
    "source_hashes_unchanged": True,
    "tracked_implementation_or_dependency_changes": changes.splitlines(),
    "root_exists": root.exists(),
    "HEAD": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip(),
    "review_commit": None,
}
(OUT / "closeout.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
print(json.dumps(summary, indent=2))
