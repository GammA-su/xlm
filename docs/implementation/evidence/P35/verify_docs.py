"""Bounded, offline documentation/evidence checks for P35; no CUDA execution."""

from __future__ import annotations

import json
import re
import string
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]


def main() -> None:
    report = ROOT / "docs/implementation/reports/P35-SCIENTIFIC-CONTRACT.md"
    handoff = ROOT / "docs/implementation/handoffs/P35-OPUS-HANDOFF.md"
    record = report.with_name("P35.md")
    contents = report.read_text(encoding="utf-8")
    if re.findall(r"^## ([A-Z])\. ", contents, re.MULTILINE) != list(string.ascii_uppercase):
        raise ValueError("scientific report must contain A-Z exactly once, in order")
    steps = re.findall(r"^## Milestone ([1-5]) ", handoff.read_text(encoding="utf-8"), re.MULTILINE)
    if steps != ["1", "2", "3", "4", "5"]:
        raise ValueError("handoff milestone sequence is incomplete")
    links = 0
    for document in (report, handoff, record):
        for destination in re.findall(
            r"\[[^\]]+\]\(([^)]+)\)", document.read_text(encoding="utf-8")
        ):
            if "://" in destination or destination.startswith("#"):
                raise ValueError(f"unexpected external link: {destination}")
            target = (document.parent / destination.split("#")[0]).resolve()
            if not target.is_relative_to(ROOT) or not target.is_file():
                raise ValueError(f"broken or out-of-workspace link: {destination}")
            links += 1
    evidence = json.loads(Path(__file__).with_name("probe.json").read_text(encoding="utf-8"))
    for size in ("50m", "150m", "300m"):
        preset = json.loads((ROOT / "recipes/models" / f"{size}.yaml").read_text())
        if evidence["counts"][size] != preset["expected_unique_parameters"]:
            raise ValueError(f"count evidence differs from recipe: {size}")
        if f"{evidence['counts'][size]:,}" not in contents:
            raise ValueError(f"count missing from report: {size}")
    cases = evidence["attention"]
    if len(cases) != 12 or any(case["repeats"] != 12 for case in cases):
        raise ValueError("attention evidence matrix incomplete")
    for case in cases:
        if case["deterministic"] and (
            case["distinct_forward"] != 1
            or case["distinct_backward"] != 1
            or not case["forward_equal_to_default"]
            or case["forward_max_abs_difference"] != 0.0
        ):
            raise ValueError("deterministic evidence differs from documented outcome")
    if divmod(32_000_000, 65_536) != (488, 18_432):
        raise ValueError("pilot update accounting error")
    if divmod(128_000_000, 65_536) != (1953, 8192):
        raise ValueError("screen update accounting error")
    print(
        f"PASS: A-Z; 5 handoff milestones; {links} local links; counts; 12 attention cases; budgets"
    )
    print("These are document consistency checks, not new-policy implementation tests.")


if __name__ == "__main__":
    main()
