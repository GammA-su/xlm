"""Read-only production prerequisite and historical admission checks; no plan/freeze calls."""

from __future__ import annotations

import os
from typing import Any

from verify_calibration import DATA, OUT, driver, write

from xlm.data.sources import certified_evidence as ce


def main() -> None:
    os.environ.update(HF_HUB_OFFLINE="1", HF_DATASETS_OFFLINE="1", XLM_HOME="G:/XLM/xlm-home")
    cli = driver()
    target = cli.store()
    historical = {}
    for name in (
        "ultrax",
        "finepdfs",
        "synth",
        "wiki_rewrite",
        "finewiki",
        "ifm_general",
        "ifm_planning",
        "simple_stories",
    ):
        historical[name] = {
            "status": "PASS",
            "identity": cli.current_admission(cli.spec_of(name), target),
        }
    args = cli.build_parser().parse_args(
        ["evidence", "show", "--source-key", "common_pile", "--data-root", str(DATA)]
    )
    spec = cli.spec_of("common_pile")
    states: dict[str, Any] = {}
    try:
        cli.component_ready(args, spec)
    except cli.DriverError as exc:
        states["policy_and_plan_prerequisite"] = {"status": "BLOCKED", "reason": str(exc)}
    else:
        raise ValueError("operator gates unexpectedly ready; investigate before continuing")
    try:
        ce.verify_current(target, cli.pin_of(spec))
    except ce.BridgeRefusal as exc:
        states["published_bridge"] = {"status": "NOT PUBLISHED", "reason": str(exc)}
    else:
        raise ValueError("a bridge was published during a show-only milestone")
    paths = {
        "reviewed_bounds": DATA / "calib/common_pile_prose/reviewed-bounds.json",
        "component_split": DATA / "calib/common_pile_prose/component-split.json",
        "transport_policy": DATA / "plans/common_pile/transport-policy.json",
    }
    for name, path in paths.items():
        if path.exists():
            raise ValueError(f"unexpected operator artifact: {path}")
        states[name] = {"status": "NOT RECORDED", "path": str(path)}
    write(
        OUT / "gate-status.json",
        {
            "network": False,
            "production_plan_called": False,
            "policy_freeze_called": False,
            "historical": historical,
            "common_pile": states,
        },
    )
    print("Eight historical admissions PASS; Common Pile blocked by unrecorded reviewed bounds")


if __name__ == "__main__":
    main()
