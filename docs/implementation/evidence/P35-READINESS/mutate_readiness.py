# ruff: noqa: E501  (mutation anchors must match source lines verbatim)
"""P35 pilot-readiness mutation runner (scratch; mutant source is never committed).

For each mutant: fresh `git archive HEAD` export, exact single-anchor edit,
py_compile, `xlm` must import from the export, then the listed pytest nodes run
with JUnit XML. KILLED only if pytest exits 1, every listed node FAILED on an
assertion (failure element, not error), and nothing errored in collection.
An unmutated control export must pass the union of all nodes first.
"""

from __future__ import annotations

import hashlib
import json
import os
import py_compile
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
PY = sys.executable
T = "tests/"
R = T + "test_p35_readiness_recoverability.py::"
P = T + "test_p35_readiness_planner.py::"
Y = T + "test_p35_readiness_payload.py::"
M = T + "test_p35_readiness_m4.py::"

MUTANTS = [
    {
        "id": 1,
        "name": "search@0 failure still allows update 1 (barrier ignores the search tier)",
        "file": "src/xlm/evaluation/recoverability.py",
        "old": "        if record.event.threshold != 0 or record.event_id not in required:\n",
        "new": "        if record.event.threshold != 0 or record.event_id not in required or (\n"
        "            record.event.tier is EventTier.SEARCH_BENCHMARK\n        ):\n",
        "nodes": [
            R + "test_any_failed_c0_event_closes_the_barrier[search_benchmark@0]",
            R + "test_partial_interrupted_or_unscored_c0_events_also_block[partial]",
            R + "test_uncrossed_c0_events_block_so_skipping_the_boundary_cannot_open_it",
        ],
    },
    {
        "id": 2,
        "name": "quick@1M has no recovery checkpoint (quick tier never gets one)",
        "file": "src/xlm/evaluation/recoverability.py",
        "old": "        sorted({e.threshold for e in required_events(plan, policy) if e.threshold not in covered})",
        "new": "        sorted({e.threshold for e in required_events(plan, policy) if e.threshold not in covered and e.tier is not EventTier.QUICK_LM})",
        "nodes": [
            R + "test_pilot_recovery_checkpoints_are_exactly_1m_and_4m",
            R + "test_pilot_table_with_the_v1_policy_has_a_route_for_every_required_event",
            P + "test_draft_review_table_has_a_route_for_every_required_event",
        ],
    },
    {
        "id": 3,
        "name": "unresolved evaluation-recovery checkpoint is retired",
        "file": "src/xlm/artifacts/retention.py",
        "old": "        if events:\n",
        "new": "        if events and candidate.role != EVALUATION_RECOVERY:\n",
        "nodes": [
            R + "test_recovery_checkpoint_is_pinned_while_its_evaluation_is_unresolved",
        ],
    },
    {
        "id": 4,
        "name": "capacity omits evaluation-recovery checkpoints",
        "file": "src/xlm/experiments/science_pilot.py",
        "old": "        retained = (pinned + recovery + evaluation_recovery + KEEP_RECOVERY) * size\n",
        "new": "        retained = (pinned + recovery + KEEP_RECOVERY) * size\n",
        "nodes": [
            P + "test_capacity_counts_worst_case_recovery_states_and_transients",
            P + "test_insufficient_capacity_blocks_and_the_measured_size_is_mandatory",
        ],
    },
    {
        "id": 5,
        "name": "payload digest includes microbatch boundaries (B8/B16 differ)",
        "file": "src/xlm/data/sampling/update_payload.py",
        "old": "    return _build(sequences, _packing_mode([mb.metadata for mb in microbatches]), arrays, strings)",
        "new": '    return _build(sequences, f"{_packing_mode([mb.metadata for mb in microbatches])}|{[len(mb.input_ids) for mb in microbatches]}", arrays, strings)',
        "nodes": [
            Y + "test_b8_b16_b32_partitions_of_one_global_update_have_one_digest",
            Y + "test_producer_encoding_and_synchronous_batches_have_one_digest",
            Y + "test_grouping_invariant_chains_across_updates",
        ],
    },
    {
        "id": 6,
        "name": "payload digest ignores labels",
        "file": "src/xlm/data/sampling/update_payload.py",
        "old": "        for name in INT_FIELDS:\n            if name not in self.arrays:\n                continue\n",
        "new": '        for name in INT_FIELDS:\n            if name not in self.arrays or name == "labels":\n                continue\n',
        "nodes": [
            Y + "test_one_changed_model_or_provenance_value_changes_the_digest[labels]",
        ],
    },
    {
        "id": 7,
        "name": "payload digest ignores the loss/attention masks and positions",
        "file": "src/xlm/data/sampling/update_payload.py",
        "old": "        for name in INT_FIELDS:\n            if name not in self.arrays:\n                continue\n",
        "new": '        for name in INT_FIELDS:\n            if name not in self.arrays or name in ("loss_mask", "attention_mask", "position_ids"):\n                continue\n',
        "nodes": [
            Y + "test_one_changed_model_or_provenance_value_changes_the_digest[position_ids]",
            Y + "test_one_changed_model_or_provenance_value_changes_the_digest[attention_mask]",
            Y + "test_a_moved_loss_mask_bit_with_the_same_count_changes_the_digest",
        ],
    },
    {
        "id": 8,
        "name": "resume resets the payload chain",
        "file": "src/xlm/data/sampling/update_payload.py",
        "old": '        chain = cls()\n        chain.rows = [list(r) for r in payload["rows"]]\n        return chain\n',
        "new": "        chain = cls()\n        return chain\n",
        "nodes": [
            Y + "test_resume_restores_the_chain_and_continues_exactly",
        ],
    },
    {
        "id": 9,
        "name": "microbatch_grouping_v2 ignores a payload-chain mismatch",
        "file": "src/xlm/comparison/science_tracks.py",
        "old": '    required=frozenset({"update_payload_receipt", "update_payload_chain_digest"}),\n',
        "new": "    required=frozenset(),\n",
        "nodes": [
            M + "test_a_payload_chain_mismatch_is_ineligible_on_v2",
            M + "test_a_run_without_the_receipt_is_ineligible_on_v2",
        ],
    },
    {
        "id": 10,
        "name": "required event without a route still yields a resolvable plan",
        "file": "src/xlm/experiments/science_pilot.py",
        "old": "    for event in unrecoverable_required(rows):\n",
        "new": "    for event in unrecoverable_required(rows)[:0]:\n",
        "nodes": [
            P + "test_missing_policy_blocks_the_research_pilot_with_every_lost_event",
            P
            + "test_authored_fixtures_also_fail_closed_on_a_missing_route[recovery_checkpoints-lost1]",
        ],
    },
]


def export(dest: Path) -> None:
    archive = dest.parent / f"{dest.name}.tar"
    subprocess.run(["git", "-C", str(REPO), "archive", "-o", str(archive), "HEAD"], check=True)
    dest.mkdir()
    with tarfile.open(archive) as tar:
        tar.extractall(dest, filter="data")
    archive.unlink()


def run_nodes(root: Path, nodes: list[str], tag: str) -> dict:
    env = {
        **os.environ,
        "PYTHONPATH": f"{root / 'src'}{os.pathsep}{root / 'tests'}",
        "OMP_NUM_THREADS": "1",
        "MKL_NUM_THREADS": "1",
        "OPENBLAS_NUM_THREADS": "1",
        "NUMEXPR_NUM_THREADS": "1",
        "TOKENIZERS_PARALLELISM": "false",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    where = subprocess.run(
        [PY, "-c", "import xlm, sys; print(xlm.__file__)"],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    junit = root / f"junit-{tag}.xml"
    proc = subprocess.run(
        [
            PY,
            "-m",
            "pytest",
            *nodes,
            "-n",
            "0",
            "-p",
            "no:cacheprovider",
            "-q",
            f"--junitxml={junit}",
        ],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
    )
    cases = {}
    if junit.exists():
        for case in ET.parse(junit).getroot().iter("testcase"):
            if case.find("failure") is not None:
                state, message = "failed", case.find("failure").get("message", "")[:300]
            elif case.find("error") is not None:
                state, message = "error", case.find("error").get("message", "")[:300]
            elif case.find("skipped") is not None:
                state, message = "skipped", ""
            else:
                state, message = "passed", ""
            cases[f"{case.get('classname')}::{case.get('name')}"] = {
                "state": state,
                "message": message,
            }
    return {
        "exit": proc.returncode,
        "xlm_from": where,
        "cases": cases,
        "tail": proc.stdout.strip().splitlines()[-1:],
    }


def main(out: Path) -> None:
    began = time.monotonic()
    head = subprocess.run(
        ["git", "-C", str(REPO), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    runner_sha = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    work = Path(tempfile.mkdtemp(prefix="p35_readiness_mut_"))
    results = {"tested_head": head, "runner_sha256": runner_sha, "mutants": []}
    control_root = work / "control"
    export(control_root)
    union = sorted({n for m in MUTANTS for n in m["nodes"]})
    control = run_nodes(control_root, union, "control")
    results["control"] = {
        "exit": control["exit"],
        "nodes": len(union),
        "passed": sum(c["state"] == "passed" for c in control["cases"].values()),
        "xlm_from": control["xlm_from"],
    }
    assert control["exit"] == 0 and results["control"]["passed"] == len(union), control
    for mutant in MUTANTS:
        root = work / f"m{mutant['id']}"
        export(root)
        target = root / mutant["file"]
        text = target.read_text(encoding="utf-8")
        count = text.count(mutant["old"])
        assert count == 1, (mutant["id"], count)
        target.write_text(text.replace(mutant["old"], mutant["new"]), encoding="utf-8")
        py_compile.compile(str(target), doraise=True)
        outcome = run_nodes(root, mutant["nodes"], f"m{mutant['id']}")
        states = [c["state"] for c in outcome["cases"].values()]
        killed = (
            outcome["exit"] == 1
            and len(states) == len(mutant["nodes"])
            and all(s == "failed" for s in states)
            and str(root / "src") in outcome["xlm_from"]
        )
        results["mutants"].append(
            {
                "id": mutant["id"],
                "name": mutant["name"],
                "file": mutant["file"],
                "edit": {"old": mutant["old"], "new": mutant["new"]},
                "nodes": mutant["nodes"],
                "exit": outcome["exit"],
                "xlm_from_export": str(root / "src") in outcome["xlm_from"],
                "cases": outcome["cases"],
                "result": "KILLED" if killed else "SURVIVED",
            }
        )
        print(mutant["id"], "KILLED" if killed else "SURVIVED", outcome["exit"], states, flush=True)
        shutil.rmtree(root)
    shutil.rmtree(work)
    results["seconds"] = round(time.monotonic() - began, 1)
    results["killed"] = sum(m["result"] == "KILLED" for m in results["mutants"])
    out.write_text(json.dumps(results, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("killed", results["killed"], "/", len(MUTANTS), "in", results["seconds"], "s")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
