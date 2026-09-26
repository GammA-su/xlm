"""P35 M4 bounded toy comparisons: SYNTHETIC authored evidence, no training.

Builds six scenarios from the authored test fixtures (tests/p35_m4_support.py),
runs the science-v1 comparison and writes each report (JSON/Markdown/CSV) plus
an index. Every value is SYNTHETIC; nothing here is a model, benchmark or timing
result. Usage (from the repository root):

    uv run --offline --locked python docs/implementation/evidence/P35-M4/toy_comparisons.py OUT_DIR
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "tests"))

from p35_m4_support import (  # noqa: E402
    E_TUPLES,
    microbatch_manifest,
    microbatch_runs,
    mixture_pair_runs,
    run,
    superiority_manifest,
)
from xlm.comparison.science_compare import compare_science, summary_state  # noqa: E402
from xlm.reports.science import write_science_report  # noqa: E402

CTRL = {"C0": 3.00, "C1": 3.10, "C2": 3.20, "C3": 3.05, "C4": 3.15}
WIN = {"C0": 2.98, "C1": 3.07, "C2": 3.19, "C3": 3.02, "C4": 3.12}


def scenarios() -> dict[str, dict]:
    out: dict[str, dict] = {}
    screen = superiority_manifest(stage="screen", tuples=E_TUPLES[:1])
    out["1_one_pair_screen"] = compare_science(
        screen, mixture_pair_runs(screen, {"E0": 3.40}, {"E0": 3.35})
    )
    replication = superiority_manifest(stage="replication", tuples=E_TUPLES[:2])
    out["2_two_pair_replication"] = compare_science(
        replication,
        mixture_pair_runs(replication, {"E0": 3.40, "E1": 3.46}, {"E0": 3.35, "E1": 3.42}),
    )
    confirmation = superiority_manifest()
    out["3_five_pair_50m_confirmation"] = compare_science(
        confirmation, mixture_pair_runs(confirmation, CTRL, WIN)
    )
    ambiguous = superiority_manifest(margin=0.02)
    out["4_ambiguous"] = compare_science(ambiguous, mixture_pair_runs(ambiguous, CTRL, WIN))
    ni = microbatch_manifest(stage="confirmation")
    ni["candidate_arms"] = ni["candidate_arms"][:1]
    ni["multiplicity"]["family_size"] = 1
    b16 = {k: v + 0.002 + 0.001 * ((i % 3) - 1) for i, (k, v) in enumerate(CTRL.items())}
    out["5_noninferiority_b16"] = compare_science(
        ni, microbatch_runs(ni, {"b8": CTRL, "b16": b16}, speeds={"b8": 45_000.0, "b16": 52_000.0})
    )
    incomplete = superiority_manifest()
    runs = mixture_pair_runs(incomplete, CTRL, {k: v for k, v in WIN.items() if k != "C4"})
    runs.append(run("m1-C4-failed", "m1", None, status="failed", failure="SYNTHETIC OOM"))
    out["6_incomplete_failed"] = compare_science(incomplete, runs)
    return out


def main(target: Path) -> None:
    index = {
        "label": "SYNTHETIC authored comparisons; no training, no real results",
        "scenarios": {},
    }
    for name, record in scenarios().items():
        write_science_report(record, target / name)
        (candidate,) = record["candidates"]
        index["scenarios"][name] = {
            "manifest_hash": record["manifest_hash"],
            "record_hash": record["record_hash"],
            "state": summary_state(record),
            "decision": (candidate.get("decision") or {}).get("result"),
            "n_complete": candidate["pairing"]["n_complete"],
            "n_required": candidate["pairing"]["n_required"],
            "ci_raw_delta": (candidate.get("statistics") or {}).get("ci_raw_delta"),
        }
    (target / "index.json").write_text(
        json.dumps(index, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main(Path(sys.argv[1]))
