"""Read-only, independent stdlib identity checks; writes only this review's evidence."""
from __future__ import annotations

import ast
import hashlib
import json
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path

import psutil

REPO = Path(__file__).resolve().parents[4]
OUT = Path(__file__).resolve().parent
started = time.perf_counter()


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def digest(obj: dict) -> str:
    return sha(json.dumps({k: v for k, v in obj.items() if k != "digest"},
                          sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False, allow_nan=False).encode())


def read(rel: str) -> dict:
    return json.loads((REPO / rel).read_bytes())


base = "docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-"
v4 = base + "V4.0/"
protocol = REPO / "docs/implementation/reports/ESSENTIAL-WEB-EVIDENCE-V4.0-PROTOCOL.md"
freeze, plan, adoption = [read(v4 + n + ".json") for n in
                          ("freeze", "phase_p_plan", "scientific_adoption")]
expected = {
    "protocol_sha256": "4c7f11b15adb7d0601977fef61ab04d62fae0e9e64091fa22dbd99e1caf65727",
    "freeze_digest": "747b82a2df7702db2b111ea62c2f27f3de3d22a37428472fdc0550af90767f57",
    "plan_digest": "16b93183b485ced924466028b84a8b4cfd84a65f596dc95c80879e32464f35d3",
}
actual = {"protocol_sha256": sha(protocol.read_bytes()), "freeze_digest": digest(freeze),
          "plan_digest": digest(plan)}
assert actual == expected
assert digest(adoption) == adoption["digest"]
v3 = read(base + "V3.0/freeze.json")
child = read(base + "V3.0-CHILD/scientific_identity_adoption.json")
assert adoption["scientific_identity"] == v3["scientific_identity"] == child["payload"]["scientific_identity"]
bindings = {}
for rel, entry in adoption["scientific_code_baseline"].items():
    blob = subprocess.check_output(["git", "show", "HEAD:" + rel], cwd=REPO)
    assert sha(blob) == entry["head_blob_sha256"], rel
    assert sha((REPO / rel).read_bytes()) in (entry["head_blob_sha256"], entry["frozen_sha256"]), rel
    bindings[rel] = sha((REPO / rel).read_bytes())
selection_path = Path("F:/Project/xlm-evidence-v2/essential-web/text_selection_manifest.json")
raw = selection_path.read_bytes()  # frozen locator manifest only; no document text
selection = json.loads(raw)
binding = adoption["T"]["selection_binding"]
assert sha(raw) == binding["sha256"] and len(raw) == binding["bytes"]
assert digest(selection) == binding["digest"]
units = [u for cell in selection["cells"] for u in
         ([cell] if "identities" in cell else cell["crawls"])]
locators = [tuple(i) for unit in units for i in unit["identities"]]
assert len(locators) == len(set(locators)) == 118
assert sorted({i[2] for i in locators}) == sorted(f["file"] for f in plan["files"] if f["arm"] == "T")
assert selection["text_selection_seed"] == 20260927
m = [f for f in plan["files"] if f["arm"] == "M"]
assert [dict(file=f["file"], window=f["bindings"]["window"]) for f in m] == v3["scientific_identity"]["M_windows"]
assert all(f["bindings"]["data_chunk_count"] == 81 for f in m)
assert all(f["bindings"]["projection"] == ["eai_taxonomy", "quality_signals"] for f in m)
m_rows = sum(f["bindings"]["window"][1] - f["bindings"]["window"][0] for f in m)
assert m_rows == 4096
m_footer_bytes = sum(o["range"][1] - o["range"][0] + 1 for o in plan["operations"] if o["kind"] == "M_FOOTER_AND_TRAILER")
source_paths = [*sorted((REPO / "src/xlm/data/evidence_v4").glob("*.py")), REPO / "scripts/evidence_v4.py"]
imports = {}
for path in source_paths:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imports[str(path.relative_to(REPO))] = sorted({
        n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)
    } | {alias.name for n in ast.walk(tree) if isinstance(n, ast.Import) for alias in n.names})
from xlm.data.evidence_v4 import phase_p  # noqa: E402
xlm_modules = sorted(k for k in sys.modules if k.startswith("xlm"))
assert not any(k.startswith(("xlm.data.acquisition", "xlm.data.evidence_v3", "xlm.data.sources")) for k in xlm_modules)
root = phase_p.live_root()
assert str(root) == "G:\\Project\\xlm-evidence-v4\\essential-web" and not root.exists()
report = {
    **actual, "implementation_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip(),
    "branch": subprocess.check_output(["git", "branch", "--show-current"], cwd=REPO, text=True).strip(),
    "environment": {"python": sys.version, "platform": platform.platform(),
                    "uv": subprocess.check_output(["uv", "--version"], text=True).strip()},
    "scientific_namespace": adoption["scientific_identity"]["scientific_namespace"],
    "source_revision": plan["source"]["revision"], "selection_digest": digest(selection),
    "policy_digest": selection["policy_digest"],
    "M_files": len(m), "M_rows": m_rows, "M_footer_and_trailer_bytes": m_footer_bytes,
    "M_all_structural_bytes_including_heads": m_footer_bytes + 32,
    "T_unique_locators": len(set(locators)), "T_files": len({i[2] for i in locators}),
    "science_code_bindings": bindings, "direct_imports": imports, "runtime_xlm_modules": xlm_modules,
    "source_hashes": {str(p.relative_to(REPO)): sha(p.read_bytes()) for p in source_paths},
    "root": str(root), "root_exists": root.exists(), "G_free_bytes": shutil.disk_usage("G:/").free,
    "elapsed_seconds": time.perf_counter() - started,
    "peak_working_set_bytes": psutil.Process().memory_info().peak_wset,
    "live_requests": 0, "corpus_text_bytes_inspected": 0,
}
(OUT / "independent-verification.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
print(json.dumps({k: v for k, v in report.items() if k not in ("science_code_bindings", "direct_imports", "source_hashes")}, indent=2))
