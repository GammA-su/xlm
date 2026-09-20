"""Read-only evidence/source verification, apart from its own result record."""
import hashlib
import json
from pathlib import Path

ROOT = Path(r"D:\Project\xlm")
HERE = Path(__file__).resolve().parent

def sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()

archive = HERE / "after05"
manifest = json.loads((archive / "sha256.json").read_text())
for relative, expected in manifest.items():
    assert sha(archive / relative) == expected, relative
environment = json.loads((archive / "environment.json").read_text())
for relative, expected in environment["source_sha256"].items():
    assert sha(ROOT / relative) == expected, relative
for item in environment["preserved"]:
    assert sha(Path(item["Path"])) == item["Hash"].lower(), item["Path"]
summary = json.loads((archive / "summary.json").read_text())
for group in summary["groups"].values():
    assert group["source_unchanged"]
    assert all(command["exit_code"] == 0 for command in group["commands"])
proposal = HERE.parent / "P23-D03"
proposal_manifest = json.loads((proposal / "sha256.json").read_text(encoding="utf-8-sig"))
for item in proposal_manifest:
    assert sha(proposal / item["File"]) == item["SHA256"], item["File"]
result = {"exit_code": 0, "archive_files_verified": len(manifest),
          "current_source_files_verified": len(environment["source_sha256"]),
          "preserved_before_contract_pin_files_verified": len(environment["preserved"]),
          "d03_proposal_files_verified": len(proposal_manifest),
          "final_test_count": sum(int(g.get("junit", {}).get("tests", "0")) for g in summary["groups"].values()),
          "scope": "Windows CPU offline D06 only; full platform acceptance remains BLOCKED"}
(HERE / "verification.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
print(json.dumps(result, indent=2))
