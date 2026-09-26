"""Authored tiny CUDA routes and genuinely fresh-process receipt continuation."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import torch

from p35_eval_support import build_trainer, reload, run_all
from test_prefetch import make_batcher, prefetcher, shards
from xlm.data.sampling.update_payload import UpdatePayloadChain, canonical_update
from xlm.evaluation.cadence import AUTHORED_FIXTURE, build_checkpoint_plan


def child(phase: str, root: Path, output: Path) -> None:
    torch.set_num_threads(1)
    readers = shards.__wrapped__(root / ("data_" + phase))
    trainer = build_trainer(
        root / "store",
        budget=197,
        global_batch=96,
        context=32,
        device="cuda",
        checkpoint_plan=build_checkpoint_plan(
            AUTHORED_FIXTURE, 197, fixture_milestones=[0, 96, 197], fixture_recovery=[]
        ),
    )
    trainer.batcher = make_batcher(readers) if phase == "sync" else prefetcher(readers)
    trainer.science.update_payloads = UpdatePayloadChain()
    consumed: list[str] = []
    take = trainer.batcher.next_step_microbatches

    def capture(**kwargs: Any) -> Any:
        batches = take(**kwargs)
        if batches:
            consumed.append(canonical_update(trainer.batcher, batches).digest())
        return batches

    trainer.batcher.next_step_microbatches = capture
    inputs_unchanged: list[bool] = []
    forward = trainer.exec_model

    def observe(**kwargs: Any) -> Any:
        before = {k: v.clone() for k, v in kwargs.items() if isinstance(v, torch.Tensor)}
        result = forward(**kwargs)
        inputs_unchanged.append(all(torch.equal(before[k], kwargs[k]) for k in before))
        return result

    trainer.exec_model = observe
    try:
        if phase == "resume":
            reload(trainer, root / "store/checkpoints/m2_run_ckpt-t96-a001")
        if phase == "first":
            assert trainer.train_step() is not None
        else:
            run_all(trainer)
        chain = trainer.science.update_payloads.to_dict()
        expected = (
            [r[3] for r in chain["rows"]][1:]
            if phase == "resume"
            else [r[3] for r in chain["rows"]]
        )
        assert consumed == expected
        assert inputs_unchanged and all(inputs_unchanged)
        report = {
            "phase": phase,
            "pid": os.getpid(),
            "device": trainer.device,
            "chain": chain,
            "lr": trainer.science.lr_receipts,
            "consumed_digests": consumed,
            "stock_forward_inputs_unchanged": True,
            "committed": trainer.committed_valid_targets,
            "peak_gpu_allocated": torch.cuda.max_memory_allocated(),
        }
        output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    finally:
        trainer.batcher.close()


def main(root: Path, output: Path) -> None:
    began = time.monotonic()
    root.mkdir(parents=True, exist_ok=False)
    results = {}
    for phase in ("sync", "producer", "first", "resume"):
        work = root / ("recovery" if phase in ("first", "resume") else phase)
        path = root / f"{phase}.json"
        completed = subprocess.run(
            [sys.executable, __file__, "child", phase, str(work), str(path)],
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr
        results[phase] = json.loads(path.read_text(encoding="utf-8"))
    assert len({r["pid"] for r in results.values()}) == 4
    for phase in ("producer", "resume"):
        assert results[phase]["chain"] == results["sync"]["chain"]
        assert results[phase]["lr"] == results["sync"]["lr"]
    assert [r[2] for r in results["sync"]["chain"]["rows"]] == [96, 96, 5]
    output.write_text(
        json.dumps(
            {
                "scope": "authored tiny CUDA; no quality or live-data claim",
                "total_targets": 591,
                "wall_seconds": time.monotonic() - began,
                "output_bytes": sum(p.stat().st_size for p in root.rglob("*") if p.is_file()),
                "equal_consumed_payloads_provenance_chain_and_resume": True,
                "runs": results,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print("CUDA synchronous/process_depth1 and fresh-process resume agree; partial update 5.")


if __name__ == "__main__":
    if sys.argv[1] == "child":
        child(sys.argv[2], Path(sys.argv[3]), Path(sys.argv[4]))
    else:
        main(Path(sys.argv[1]), Path(sys.argv[2]))
