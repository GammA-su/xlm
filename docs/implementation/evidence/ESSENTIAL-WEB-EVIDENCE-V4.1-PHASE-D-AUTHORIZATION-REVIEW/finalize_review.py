"""Write review-only summary and prepend STATUS without changing existing bytes."""
from __future__ import annotations

import hashlib
import json
import platform
import shutil
import subprocess
import sys
from pathlib import Path

import pyarrow

from review_inventory import DROOT, HERE, PARENT, REPO, inventory


def main() -> None:
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO).decode().strip()
    assert head == "b1e1d10bbfcde78e708ba21409c7a0a765f9f5be"
    subprocess.run(["git", "diff", "--exit-code", "HEAD", "--", "src", "scripts", "tests",
                    "pyproject.toml", "uv.lock", ".python-version"], cwd=REPO, check=True)
    assert not DROOT.exists()
    current = inventory(PARENT)
    assert current == json.loads((HERE / "parent-before.json").read_text())
    hashes = json.loads((HERE / "independent-bindings.json").read_text())
    plan = json.loads((REPO / "docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V4.1-PHASE-D/phase_d_plan.json").read_bytes())
    cap = json.loads((HERE / "cap-boundary-observation.json").read_text())
    drift = json.loads((HERE / "parent-drift-observation.json").read_text())
    result = {
        "verdict": "PHASE-D AUTHORIZATION REVIEW BLOCKED",
        "M_authorization": "BLOCKED", "T_authorization": "BLOCKED",
        "reviewed_head": head, "finishing_head": head,
        "freeze_commit": "d58693822ce06baddf1d62a21f69ecf0bb81f2bd",
        "phase_p_result_review_commit": "ed8efcf3c85c265828a88579a264f08a2048640f",
        "protocol_sha256": "bb2bca6f571c9e02b02536bc518cdc1b61fdf0f56b0c6970eaa257c8d198769f",
        "independently_recomputed_bindings": hashes,
        "phase_p_artifact_bindings": plan["parents"]["phase_p"]["artifacts"],
        "parent_root": str(PARENT), "parent_unchanged": True,
        "parent_inventory_entries_including_root": len(current),
        "parent_inventory_sha256": hashlib.sha256(json.dumps(current, sort_keys=True).encode()).hexdigest(),
        "parent_file_bytes": sum(v["bytes"] for v in current.values() if v["sha256"]),
        "parent_complete_operations": 40, "phase_d_root_exists": False,
        "scientific_identity": {key: plan[key] for key in
                                ("scientific_namespace", "selection_digest", "policy_digest", "source")},
        "M": {"operations": 8, "payload_bytes": 11692530, "frozen_rows": 4096},
        "T": {"operations": 47, "payload_bytes": 179963169, "frozen_locators": 118,
              "pieces_per_file": [6, 6, 5, 6, 6, 6, 6, 6]},
        "blockers": {"B01_parent_drift": drift, "B02_whole_root_cap": cap},
        "tests": {
            "prescribed_focused": {"passed": 412, "failed": 0, "exit": 0, "pytest_seconds": 43.72},
            "scientific_regressions": {"passed": 69, "failed": 0, "exit": 0, "pytest_seconds": 1.40},
            "independent_final": {"passed": 20, "failed": 2, "exit": 1, "pytest_seconds": 3.83,
                                  "measured_seconds": 4.012, "peak_working_set_bytes": 139292672},
            "skipped": 0, "xfail": 0,
        },
        "environment": {"python": sys.version, "pyarrow": pyarrow.__version__,
                        "platform": platform.platform(), "current_G_free_bytes": shutil.disk_usage(PARENT).free},
        "network_requests": 0, "live_phase_d": "NOT RUN", "real_column_decoding": "NOT RUN",
        "full_acceptance": "NOT RUN", "cuda": "NOT RUN", "live_performance": "NOT RUN",
        "phase_p_redesign": "OUT OF SCOPE", "implementation_changes": False,
        "review_commit": None, "commit_reason": "user made review commit conditional on PASS",
        "next_operator_action": "Offline repair of B01/B02 only, preserving frozen identity and Phase-P bytes; repeat narrow authorization review. Do not run live Phase D.",
    }
    (HERE / "review-result.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    notice = (
        "> **ESSENTIAL-WEB V4.1 PHASE-D AUTHORIZATION REVIEW BLOCKED (2026-09-29).**\r\n"
        "> Reviewed implementation `b1e1d10` and protocol/freeze `d586938`; M and T are\r\n"
        "> both BLOCKED. Frozen identities, exact 8 M / 47 T ranges, COMPLETE Phase-P\r\n"
        "> parent and read-only inventory verified. Prescribed tests: 412 passed;\r\n"
        "> scientific regressions: 69 passed; independent probes: 20 passed, 2 failed.\r\n"
        "> B01: parent-manifest drift during the run can still yield COMPLETE.\r\n"
        "> B02: export/store growth can exceed the frozen whole-root cap at a\r\n"
        "> synthetic accounting boundary. No implementation changes, network, live\r\n"
        "> Phase D, real Phase-D root, Phase-P mutation, scientific text review or push.\r\n"
        "> [Review and exact next repair prompt](reports/ESSENTIAL-WEB-EVIDENCE-V4.1-PHASE-D-AUTHORIZATION-REVIEW.md).\r\n"
        "> Next: offline repair of those two Phase-D contract failures, then repeat\r\n"
        "> the narrow authorization review. Do not run the live acquisition command.\r\n\r\n"
    ).encode("utf-8")
    original_notice = notice
    notice = notice.replace(b"\r\n", b"\n")
    status = REPO / "docs/implementation/STATUS.md"
    before = (HERE / "status-before.bin").read_bytes()
    actual = status.read_bytes()
    assert actual in (before, notice + before, original_notice + before), "STATUS changed independently; refuse to overwrite"
    if actual != notice + before:
        status.write_bytes(notice + before)
    print(json.dumps({"verdict": result["verdict"], "HEAD": head,
                      "parent_unchanged": True, "phase_d_root_exists": False,
                      "status_previous_bytes_preserved": True}, indent=2))


if __name__ == "__main__":
    main()
