"""Read-only replay and deterministic planning arithmetic; outputs contain no corpus text."""

from __future__ import annotations

import hashlib
import importlib
import json
import shutil
import sys
from pathlib import Path
from typing import Any

OUT = Path(__file__).resolve().parent
PRIOR = OUT.parent / "COMMON-PILE-POST-CALIBRATION"
sys.path.insert(0, str(PRIOR))
v: Any = importlib.import_module("verify_calibration")

from xlm.data.acquisition import component_policy as cp  # noqa: E402
from xlm.data.evidence_v2 import canonical  # noqa: E402


def main() -> None:
    captured: dict[str, Any] = {}
    original_write = v.write
    v.write = lambda path, value: captured.update(replay=value)
    v.main()
    cli = v.driver()
    args = cli.build_parser().parse_args(
        [
            "policy",
            "model",
            "--source-key",
            "common_pile",
            "--data-root",
            "G:/XLM",
            "--scratch-root",
            "C:/XLM-scratch",
        ]
    )
    spec, target = cli.spec_of("common_pile"), cli.store()
    pin = cli.pin_of(spec)
    component = cli.component_ready(args, spec)
    expected = {
        "calibration": "c8a32cced2bae62c21b4e4396d46f0803f11745117e52d468adf3912dde0541d",
        "component_split": "d64b2b6ee53c7dd22d6b0ef193f09f4fb86a8fa512a6920daf40f048baa28e77",
        "reviewed_bounds": "0f1467e65b1c0b4b79ed4bd2aeb4d0399b2995fb41c4df6472b73390b46a966c",
    }
    for key, digest in expected.items():
        assert component[key]["digest"] == digest, key
        if key != "calibration":
            assert component[key]["operator"] == "GammA"
    cal = component["calibration"]
    identities = {}
    paths = {
        "measurement": (
            "common_pile_prose/measurement.json",
            "a99e994e3f1d43b5f50bc659ac7b1371a55cd05282e95f48c3a933d3a9bc7a42",
        ),
        "shared_calibration": (
            "calibration.json",
            "73a3c5ab3f3f297437d1baaae90996cd1464950c576bc07d93aa2d9be89b764a",
        ),
        "backup": (
            "calibration.pre-common-pile.json",
            "e6f7d3c1f7c5651edb89861bf2e65f1ed6de520582f9bf9ae30a8cac2550c552",
        ),
        "estimate": (
            "headroom_estimate.json",
            "f887246171bea265d02b1cb69e9a8f5342a046c38c76ae4f76682c088aadaa6d",
        ),
    }
    for name, (relative, sha) in paths.items():
        path = v.DATA / "calib" / relative
        assert hashlib.sha256(v.read(path)).hexdigest() == sha, name
        identities[name] = {"status": "PASS", "path": str(path), "sha256": sha}
    assert v.read(v.DATA / "calib/headroom_estimate.json") == v.read(
        v.DATA / "calib/headroom_estimate.common-pile-post.json"
    )
    measurement = json.loads(v.read(v.DATA / "calib/common_pile_prose/measurement.json"))
    assert measurement == v.cc.measurement(cal)
    assert canonical.digest(measurement) == (
        "ca146fa15f79ac80625df5f222f78e369325699ec2b8e86decf42ff8f571363a"
    )
    shared = json.loads(v.read(v.DATA / "calib/calibration.json"))["sources"]
    backup = json.loads(v.read(v.DATA / "calib/calibration.pre-common-pile.json"))["sources"]
    assert all(shared[key] == value for key, value in backup.items())
    receipt = v.ce.verify_current(
        target, pin, rebuild=lambda: cli.build_bridge(args, spec, target)[1:]
    )
    assert receipt["digest"] == ("12ff9163353bf6e56979dc66b66307c12e9d57dd92e38c348b4b7927c70838f3")
    reviews, hashes = cli.review.read_reviews(v.DATA / "reviews/common_pile_balanced_cal01")
    assert hashes == {
        "attribution": "aa313beeb2b568dd5104278cb406c3a31d835ebe6168d3ba89a5f92f8c4129ef",
        "benchmark_risk": "3a047ee431402d013111e5278c9e6b7ebf921f3fac86a2075dd9573f07813192",
        "external_evidence": "94c7fa013a49f257cfcd7f59bc324bfdba64330351bf7bfb898c97b04630e205",
        "source_rights": "c2b12ec4ef37d2024d22a8e76616c45cb5b246f84871ca732bc39ae925f93f38",
    }
    evidence = cli.load_probe_evidence(pin.source_id, pin.view_id, target)
    rebuilt = cli.review.build_decision(pin, evidence, receipt, reviews, hashes)
    actual = cli.load_admission_decision(pin.source_id, pin.view_id, target)
    left, right = rebuilt.model_dump(mode="json"), actual.model_dump(mode="json")
    assert {k: x for k, x in left.items() if k != "decision_timestamp"} == {
        k: x for k, x in right.items() if k != "decision_timestamp"
    }
    historical = {}
    prior_gates = json.loads(v.read(PRIOR / "gate-status.json"))["historical"]
    for name, entry in prior_gates.items():
        identity = cli.current_admission(cli.spec_of(name), target)
        assert identity == entry["identity"], name
        historical[name] = {"status": "PASS", "identity": identity}
    inventory = cal["inventory_snapshot"]
    selected, cursors = cp.select_files(
        component,
        inventory,
        acquired={},
        cursors={},
        safety=1.15,
        eligible=len(inventory["files"]) - cli.planner.BENCHMARK_RESERVED_POSITIONS,
    )
    files = [e["file"] for e in selected]
    projected = cp.expected_selection(cal, files, files)
    table = {}
    for name, entry in projected["components"].items():
        names = [f for f in files if f.split("/")[0] == name]
        required = component["component_split"]["components"][name]["required_canonical_bytes"]
        assert entry["canonical_bytes"] >= required * 1.15, name
        table[name] = entry | {
            "first_file": names[0],
            "last_file": names[-1],
            "required_bytes": required,
            "projected_tokens": entry["canonical_bytes"] / 4,
            "headroom_bytes": entry["canonical_bytes"] - required,
            "headroom_after_safety_bytes": entry["canonical_bytes"] - required * 1.15,
            "next_cursor": cursors[name],
        }
    report = cli.evaluate_policy(args, spec)
    result = {
        "status": "PASS",
        "network": False,
        "component_digests": expected,
        "identities": identities,
        "historical": historical,
        "reviews_sha256": hashes,
        "published_bridge_digest": receipt["digest"],
        "admission": right,
        "admission_canonical_digest": canonical.digest(right),
        "selected_files": selected,
        "components": table,
        "expected": projected,
        "policy_model": report,
    }
    # No writes occur until all integrity, historical and capacity assertions pass.
    original_write(OUT / "verification.json", captured["replay"])
    original_write(OUT / "integrity-and-selection.json", result)
    roots = cli.roots_of(args)
    if roots.sequences():
        assert roots.sequences() == [1]
        plan = cli.runner.load_plan(roots, 1)
        minted = cli.planner.minted_from_record(plan)
        assert minted.plan_hash == plan["acquisition_plan"]["plan_hash"]
        assert minted.selected_file_limit == len(files) == 268
        assert plan["selection"]["files"] == selected
        assert plan["selection"]["component_cursors"] == cursors
        assert plan["expected"] == projected
        assert plan["inputs"]["admission"] == cli.current_admission(spec, target)
        assert plan["source"] == pin.as_dict()
        assert plan["inventory"]["digest"] == inventory["inventory_digest"]
        for key in ("component_split", "reviewed_bounds"):
            assert plan["inputs"][key] == component[key]
        frozen = cli.runner.read_json(roots.plans / "transport-policy.json")
        cli.policy.check_frozen(frozen, pin.source_id)
        assert frozen["report"] == report
        assert plan["inputs"]["transport_policy"]["digest"] == frozen["digest"]
        for key in ("calibration", "component_split", "reviewed_bounds"):
            assert frozen["inputs"][key] == component[key]["digest"]
        assert sorted(p.name for p in roots.plan_dir(1).iterdir()) == ["plan.json"]
        resume = cli.runner.resume_state(roots, plan)
        restart = cli.runner.classify(roots, plan, resume, "p01")
        assert restart["counts"]["fresh_download"] == 268
        assert sum(restart["counts"].values()) == 268
        original_write(OUT / "plan.json", plan)
        original_write(OUT / "transport-policy.json", frozen)
        original_write(
            OUT / "plan-verification.json",
            {
                "status": "PASS",
                "plan_digest": plan["digest"],
                "policy_digest": frozen["digest"],
                "acquisition_plan_hash": minted.plan_hash,
                "plan_path": str(roots.plan_dir(1) / "plan.json"),
                "restart": restart,
                "authorized": False,
                "run": False,
                "free_disk_bytes": {
                    p: shutil.disk_usage(p).free for p in ("G:/XLM", "C:/XLM-scratch")
                },
            },
        )
    print("PASS: all operator inputs, eight historical admissions and six component capacities")
    print(json.dumps({"expected": projected, "policy": report["candidates"]}))


if __name__ == "__main__":
    main()
