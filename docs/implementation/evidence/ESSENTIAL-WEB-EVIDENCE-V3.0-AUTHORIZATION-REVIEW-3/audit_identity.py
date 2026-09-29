"""Independent Git/blob, canonical identity and metadata-only audit. No network."""
from __future__ import annotations

import ast
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

import psutil

from xlm.data.evidence_v3 import envidentity

REPO = Path(__file__).resolve().parents[4]
OUT = Path(__file__).resolve().parent
FREEZE = "52569c525a6613faab096d17b60167b4aaa0f214"
A = "3dd5ebce0edb7d8e676966c9195b73fb5a1978c9"
B = "12d85158206378c0d6833d95df9b9d8fa5789761"
BASE = "docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V3.0"
CHILD = BASE + "-CHILD"


def git(*args: str) -> bytes:
    p = subprocess.run(["git", *args], cwd=REPO, capture_output=True, timeout=30, check=True)
    return p.stdout


def blob(commit: str, path: str) -> bytes:
    return git("cat-file", "blob", f"{commit}:{path}")


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def digest(value: object) -> str:
    return sha(json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True,
                          separators=(",", ":")).encode("utf-8"))


def selfdigest(value: dict) -> str:
    return digest({k: v for k, v in value.items() if k != "digest"})


def main() -> None:
    began = time.perf_counter()
    assert git("rev-parse", "HEAD").decode().strip() == B
    assert git("rev-parse", B + "^").decode().strip() == A
    freeze = json.loads(blob(B, BASE + "/freeze.json"))
    protocol_path = "docs/implementation/reports/ESSENTIAL-WEB-EVIDENCE-V3.0-PROTOCOL.md"
    protocol_sha = sha(blob(B, protocol_path))
    assert protocol_sha == "c191aa49a7e35349e27eb077c7105ead1a15a7edbbb28ef0bc78f157b219fd79"
    assert selfdigest(freeze) == freeze["digest"] == "aa977972883af209afffebae221683658b2637281ae8e72741150750534fd834"
    cap_digest = digest(freeze["arm_caps"])
    assert cap_digest == "e2485cd438524124c22074d59c48a5ee7dc699f9c9a9ea3f9c11deeef54e0f7b"
    frozen_paths = git("ls-tree", "-r", "--name-only", FREEZE, "--", BASE).decode().splitlines()
    frozen_paths += [protocol_path, "docs/implementation/reports/ESSENTIAL-WEB-EVIDENCE-V3.0-REVIEW.md"]
    for path in frozen_paths:
        assert blob(FREEZE, path) == blob(B, path), path
        # Historical metadata includes mixed newline blobs. This check is only
        # checkout equivalence; normative identity above uses exact Git bytes.
        assert (REPO / path).read_bytes().replace(b"\r\n", b"\n") == blob(B, path).replace(b"\r\n", b"\n"), path
    closure = json.loads(blob(B, BASE + "/lineage_closure.json"))
    assert selfdigest(closure) == closure["digest"]
    child_paths = git("ls-tree", "-r", "--name-only", B, "--", CHILD).decode().splitlines()
    children = {}
    for path in child_paths:
        raw = blob(B, path)
        value = json.loads(raw)
        assert selfdigest(value) == value["digest"], path
        assert (REPO / path).read_bytes() == raw, path
        assert value["authorization"] == "NONE" and value["executable"] is False
        children[Path(path).name] = {"bytes": len(raw), "sha256": sha(raw), "canonical_digest": value["digest"]}
    manifest = json.loads(blob(B, CHILD + "/artifact_manifest.json"))
    for name, desc in manifest["payload"]["artifacts"].items():
        for key in ("bytes", "sha256", "canonical_digest"):
            assert desc[key] == children[name][key], (name, key)
    assert len(children) == 14 and sum(c["bytes"] for c in children.values()) == 113954
    expected = {
        "artifact_manifest.json": ("3a524225312641ad1c7f65247709ea66b576afadebe8c13e28d938096f0f3d0b", "88a014e31d8cac0dbcc962d9298442068bd6048af16a9e797233a1f8a6159a37"),
        "arm_m_phase_p_dry.json": ("b4af65eced13ac9e7cca0ef99830f3f9307f85e77da090dd6406c0e3c0766905", "0ac4f51b09aa9bd2008237aeb824023d92c3d2a83cbc68a9b4b5f37b6286f580"),
        "arm_t_phase_p_dry.json": ("9c1f17017d0d493ed5ebfa13a4e9db478ac5a0b781c0c96dfe9ddd0195e623a0", "9f623e08edfb4e30b87d9d177b4c685ec9ab8628810b80fabbdcad6ff1ec912a"),
    }
    for name, (canon, file_sha) in expected.items():
        assert children[name]["canonical_digest"] == canon and children[name]["sha256"] == file_sha
    code = manifest["producer"]["code_hashes"]
    assert manifest["producer"]["implementation_commit"] == A
    for path, expected_sha in code.items():
        assert sha(blob(A, path)) == expected_sha and blob(A, path) == blob(B, path), path
        local = (REPO / path).read_bytes()
        assert local == blob(B, path) or local == blob(B, path).replace(b"\n", b"\r\n"), path
    configs = {}
    for path in (".python-version", "pyproject.toml", "uv.lock"):
        raw = blob(B, path)
        assert raw == blob(A, path)
        local = (REPO / path).read_bytes()
        assert local == raw or local == raw.replace(b"\n", b"\r\n")
        configs[path] = {"sha256_committed_blob": sha(raw), "git_object_id": git("rev-parse", f"{B}:{path}").decode().strip(),
                         "worktree_sha256": sha(local), "representation": "exact" if local == raw else "crlf"}
    science = freeze["scientific_identity"]
    assert len(science["M_windows"]) == 8
    assert all(w["window"][1] - w["window"][0] == 512 for w in science["M_windows"])
    assert science["selection_total"] == 118 and science["metadata_and_text_seed"] == 20260927
    assert science["selection_digest"] == "975ba3dee4af0598e665ea05c69bbe49336c3e3a35fca777a863073190b78474"
    assert science["revision"] == "ce4eccc7e9604667b6d7f32cb6274b8b41f3113d"
    assert science["policy_digest"] == "f4357f61f434d5105d823266153dac6372e122f12767187631e8796b4899dd07"
    assert science["scientific_namespace"] == "essential-web-evidence-v2.0"
    science_diff = git("diff", "--name-only", FREEZE, B, "--", "src/xlm/data/evidence_v2", "src/xlm/data/evidence_v21",
                       "src/xlm/data/evidence_v22", "recipes/selectors", "docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V2*").decode()
    assert not science_diff
    m = freeze["M_prospective_files"]
    t = freeze["T_prospective_files"]
    payload = sum(f["phase_P_payload_bytes"] for f in m)
    arithmetic = {"M_P_logical": sum(f["P_logical"] for f in m), "M_P_observed": sum(f["P_nominal_physical"] for f in m),
                  "M_P_cold": sum(f["P_cold_chain_max_no_retry"] for f in m), "M_D_identity": sum(f["D_footer_cold_chain_max"] for f in m),
                  "M_payload": payload, "M_footer_margin": 33554432-payload, "M_min_file_margin": min(4194304-f["phase_P_payload_bytes"] for f in m),
                  "T_P_logical": sum(f["P_logical"] for f in t), "T_P_observed": sum(f["P_nominal_physical"] for f in t),
                  "T_P_cold": sum(f["P_cold_chain_max_no_retry"] for f in t), "T_D_identity": sum(f["D_footer_cold_chain_max"] for f in t),
                  "T_D_data": sum(f["D_data_physical_no_retry"] for f in t), "T_P_ceiling": 800-32-47,
                  "D_identity_byte_reservation_per_file": 4*65537, "D_identity_byte_reservation_arm": 8*4*65537}
    imports = {}
    public = {}
    for path in code:
        if path.endswith(".py"):
            tree = ast.parse(blob(B, path).decode("utf-8"))
            imports[path] = sorted({node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)} |
                                   {a.name for node in ast.walk(tree) if isinstance(node, ast.Import) for a in node.names})
            public[path] = [n.name for n in tree.body if isinstance(n, (ast.ClassDef, ast.FunctionDef)) and not n.name.startswith("_")]
    assert not any("acquisition" in item for group in imports.values() for item in group)
    root = Path("G:/Project/xlm-evidence-v3/essential-web")
    root_metadata = {"G_exists": Path("G:/").is_dir(), "execution_root_exists": os.path.lexists(root), "ancestors": []}
    assert root_metadata["G_exists"] and not root_metadata["execution_root_exists"]
    for path in [root, *root.parents]:
        if os.path.lexists(path):
            st = os.lstat(path)
            root_metadata["ancestors"].append({"path": str(path), "device": st.st_dev, "file_id": st.st_ino,
                                                "attributes": getattr(st, "st_file_attributes", None), "reparse_tag": getattr(st, "st_reparse_tag", None)})
    out = {"HEAD": B, "branch": git("branch", "--show-current").decode().strip(), "A": A, "freeze_commit": FREEZE,
           "protocol_sha256": protocol_sha, "freeze_digest": freeze["digest"], "cap_map_digest": cap_digest,
           "frozen_paths_unchanged": frozen_paths, "scientific_code_diff": science_diff, "science": science,
           "external_original_selection": "NOT READ: committed frozen descriptor identity verified; no external original opened",
           "closure": closure, "configs": configs, "children": children, "code_bound_files": len(code),
           "runtime": envidentity.runtime_environment(REPO, config_identity={p: v["sha256_committed_blob"] for p,v in configs.items()}),
           "arithmetic": arithmetic, "imports": imports, "public_surface": public, "root_metadata": root_metadata,
           "audit_wall_seconds": time.perf_counter()-began, "audit_process_memory": psutil.Process().memory_info()._asdict()}
    (OUT / "identity.json").write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"result": "VERIFIED", "children": len(children), "bytes": sum(c["bytes"] for c in children.values()),
                      "arithmetic": arithmetic, "runtime": out["runtime"], "root_metadata": root_metadata}, indent=2))


if __name__ == "__main__":
    main()
