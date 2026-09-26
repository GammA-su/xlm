# ruff: noqa: E501  (mutation anchors must match source lines verbatim)
"""P35 microbatch evidence hardening: adversary/mutation runner (scratch mutants, never committed).

Each mutant re-opens one Astra adversary by reverting exactly one hardening
defense. For each: a fresh ``git archive HEAD`` export, an exact single-anchor
edit, ``py_compile``, ``xlm`` must import from the export, then the listed
pytest nodes run with JUnit XML. KILLED only if pytest exits 1, every listed
node FAILED (a failure element, never an error), and every failure message is a
scientific assertion (``AssertionError``, a pytest-rewritten ``assert``, ``DID NOT RAISE`` or a
``pytest.raises`` message mismatch), never an import/syntax/type/name error. An
unmutated control export must pass the union of all nodes first.
"""

from __future__ import annotations

import hashlib
import json
import os
import py_compile
import re
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
H = "tests/test_p35_hardening_payload.py::"
R = "tests/test_p35_hardening_runtime.py::"
Y = "tests/test_p35_readiness_payload.py::"
#: pytest reports a rewritten plain ``assert`` as "assert ..." (no class prefix).
SCIENTIFIC_FAILURE = re.compile(
    r"^(AssertionError|assert |Failed: DID NOT RAISE)|Regex pattern did not match"
)

MUTANTS = [
    {
        "id": 1,
        "name": "detached consumed input tensor replacement accepted (row-count-only binding)",
        "file": "src/xlm/data/sampling/update_payload.py",
        "old": "        bind_consumed(pending, microbatches)\n        return canonical_from_prepared(pending)\n",
        "new": "        if sum(int(len(mb.input_ids)) for mb in microbatches) != sum(pending.rows):\n"
        '            raise PayloadReceiptError("microbatches are not the pending prepared update")\n'
        "        return canonical_from_prepared(pending)\n",
        "nodes": [
            R + "test_a_detached_replacement_consumed_tensor_is_refused[input_ids]",
            R + "test_a_detached_replacement_consumed_tensor_is_refused[labels]",
            R + "test_provenance_correct_but_tensor_replaced_is_refused",
            R + "test_tensor_correct_but_provenance_row_shifted_is_refused",
            H + "test_one_changed_consumed_value_is_refused[input_ids]",
            H + "test_one_changed_consumed_value_is_refused[labels]",
            H + "test_provenance_shifted_after_sealing_is_refused",
        ],
    },
    {
        "id": 2,
        "name": "LR/payload chain ahead of committed data accepted (no history-vs-data check)",
        "file": "src/xlm/training/science.py",
        "old": '    from xlm.data.sampling.update_payload import PayloadReceiptError, verify_chain\n\n    raw = saved.get("update_payloads")\n',
        "new": '    from xlm.data.sampling.update_payload import PayloadReceiptError, verify_chain\n\n    if True:\n        return\n    raw = saved.get("update_payloads")\n',
        "nodes": [
            R + "test_inconsistent_history_is_refused_before_any_restore[history_ahead_A7]",
            R + "test_inconsistent_history_is_refused_before_any_restore[data_ahead]",
            R + "test_inconsistent_history_is_refused_before_any_restore[meta_committed]",
            H + "test_history_and_committed_state_must_agree[chain_and_lr_ahead_A7]",
            H + "test_history_and_committed_state_must_agree[data_ahead]",
        ],
    },
    {
        "id": 3,
        "name": "payload receipt commit failure still allows checkpoint (in-doubt cleared first)",
        "file": "src/xlm/training/trainer.py",
        "old": "            try:\n                self.science.commit_receipted_update(receipt)\n",
        "new": "            self._update_in_doubt = False\n            try:\n                self.science.commit_receipted_update(receipt)\n",
        "nodes": [
            R + "test_a_payload_commit_failure_after_data_commit_poisons_the_boundary",
            R + "test_training_never_succeeds_after_a_receipt_commit_failure",
        ],
    },
    {
        "id": 4,
        "name": "evaluator mutates the payload chain without guard detection",
        "file": "src/xlm/training/evaluation.py",
        "old": '    if payloads is not None:\n        fingerprint["update_payload_receipt"] = identity_digest(payloads.guard_state())\n',
        "new": "",
        "nodes": [
            R + "test_evaluator_mutation_of_receipt_state_is_caught[chain_head]",
            R + "test_evaluator_mutation_of_receipt_state_is_caught[add_row]",
            R + "test_evaluator_mutation_of_receipt_state_is_caught[staged_receipt]",
            R + "test_an_untouched_receipt_passes_and_is_a_verified_guard_component",
        ],
    },
    {
        "id": 5,
        "name": "duplicate compact provenance aliases silently alter equality",
        "file": "src/xlm/data/sampling/update_payload.py",
        "old": "    provenance = update.provenance\n    check_compact_table(provenance)\n",
        "new": "    provenance = update.provenance\n",
        "nodes": [
            H + "test_a_duplicate_alias_is_refused_precisely",
            H + "test_an_out_of_table_code_is_refused[-1]",
        ],
    },
    {
        "id": 6,
        "name": "changed-policy fork silently drops or continues required receipt history",
        "file": "src/xlm/training/checkpoint.py",
        "old": '        required = science is not None and science.update_payloads is not None\n        saved = saved_science.get("update_payloads") if saved_science is not None else None\n',
        "new": '        if not same_policy:\n            return\n        required = science is not None and science.update_payloads is not None\n        saved = saved_science.get("update_payloads") if saved_science is not None else None\n',
        "nodes": [
            R + "test_fork_receipt_semantics_refuse_before_restore[on_to_off_changed_policy]",
            R + "test_fork_receipt_semantics_refuse_before_restore[off_to_on_changed_policy]",
            R + "test_fork_receipt_semantics_refuse_before_restore[on_to_on_changed_policy]",
        ],
    },
    {
        "id": 7,
        "name": "payload row duplicated after resume (stage accepts a replayed update)",
        "file": "src/xlm/data/sampling/update_payload.py",
        "old": "        if (step, committed_before) != self._expected():\n",
        "new": "        if False:\n",
        "nodes": [
            R + "test_resume_cannot_duplicate_or_skip_receipt_rows",
            Y + "test_replayed_or_skipped_updates_are_never_appended[1-0]",
        ],
    },
    {
        "id": 8,
        "name": "chain final partial C off by one accepted",
        "file": "src/xlm/training/science.py",
        "old": "    if endpoint > data:\n",
        "new": "    if endpoint > data + 1:\n",
        "nodes": [
            H + "test_history_and_committed_state_must_agree[partial_off_by_one]",
            R + "test_a_consistent_checkpoint_and_a_final_partial_update_validate_exactly",
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
        [PY, "-c", "import xlm; print(xlm.__file__)"],
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
                state, message = "failed", case.find("failure").get("message", "")[:400]
            elif case.find("error") is not None:
                state, message = "error", case.find("error").get("message", "")[:400]
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
    work = Path(tempfile.mkdtemp(prefix="p35_hardening_mut_"))
    results: dict = {"tested_head": head, "runner_sha256": runner_sha, "mutants": []}
    control_root = work / "control"
    export(control_root)
    union = sorted({n for m in MUTANTS for n in m["nodes"]})
    control = run_nodes(control_root, union, "control")
    results["control"] = {
        "exit": control["exit"],
        "nodes": len(union),
        "passed": sum(c["state"] == "passed" for c in control["cases"].values()),
        "xlm_from": control["xlm_from"],
        "tail": control["tail"],
    }
    print("control", results["control"], flush=True)
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
        cases = outcome["cases"]
        killed = (
            outcome["exit"] == 1
            and len(cases) == len(mutant["nodes"])
            and all(c["state"] == "failed" for c in cases.values())
            and all(SCIENTIFIC_FAILURE.search(c["message"]) for c in cases.values())
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
                "cases": cases,
                "result": "KILLED" if killed else "SURVIVED",
            }
        )
        print(
            mutant["id"],
            "KILLED" if killed else "SURVIVED",
            outcome["exit"],
            [c["state"] for c in cases.values()],
            flush=True,
        )
        shutil.rmtree(root)
    shutil.rmtree(work)
    results["seconds"] = round(time.monotonic() - began, 1)
    results["killed"] = sum(m["result"] == "KILLED" for m in results["mutants"])
    out.write_text(json.dumps(results, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("killed", results["killed"], "/", len(MUTANTS), "in", results["seconds"], "s")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
