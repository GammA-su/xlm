"""Authored receipt continuity across genuinely fresh Python processes (106 targets)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from p35_eval_support import build_trainer, reload, run_all
from xlm.data.sampling.update_payload import UpdatePayloadChain
from xlm.evaluation.cadence import AUTHORED_FIXTURE, build_checkpoint_plan


def child(phase: str, root: Path, output: Path) -> None:
    trainer = build_trainer(
        root,
        budget=53,
        dropout=0.2,
        checkpoint_plan=build_checkpoint_plan(
            AUTHORED_FIXTURE, 53, fixture_milestones=[0, 16, 53], fixture_recovery=[]
        ),
    )
    trainer.science.update_payloads = UpdatePayloadChain()
    if phase == "resume":
        reload(trainer, root / "checkpoints/m2_run_ckpt-t16-a001")
    if phase == "first":
        assert trainer.train_step() is not None
    else:
        run_all(trainer)
    output.write_text(
        json.dumps(
            {
                "phase": phase,
                "pid": __import__("os").getpid(),
                "committed": trainer.committed_valid_targets,
                "step": trainer.step,
                "lr": trainer.science.lr_receipts,
                "chain": trainer.science.update_payloads.to_dict(),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def main(root: Path, output: Path) -> None:
    root.mkdir(parents=True, exist_ok=False)
    outputs = {}
    for phase in ("uninterrupted", "first", "resume"):
        target = root / ("a" if phase == "uninterrupted" else "b")
        artifact = root / f"{phase}.json"
        result = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                "child",
                phase,
                str(target),
                str(artifact),
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        outputs[phase] = json.loads(artifact.read_text(encoding="utf-8"))
    a, b = outputs["uninterrupted"], outputs["resume"]
    assert a["chain"] == b["chain"] and a["lr"] == b["lr"]
    assert [r[2] for r in a["chain"]["rows"]] == [16, 16, 16, 5]
    assert len({v["pid"] for v in outputs.values()}) == 3
    report = {
        "scope": "authored CPU science-v1; dropout 0.2; three fresh processes",
        "total_targets": 106,
        "equal_rows_head_and_lr": True,
        "runs": outputs,
    }
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print("Fresh-process resume: identical payload rows/head and LR; final partial 5 targets.")


if __name__ == "__main__":
    if sys.argv[1] == "child":
        child(sys.argv[2], Path(sys.argv[3]), Path(sys.argv[4]))
    else:
        main(Path(sys.argv[1]), Path(sys.argv[2]))
