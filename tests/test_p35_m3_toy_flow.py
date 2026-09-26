"""P35 M3 authored/synthetic toy flow (NOT the 32M pilot), on CUDA, hard-bounded.

Bounds: <= 200,000 training targets in total (16,384 in phase A; 65,536 in
phase B plus at most one replay of 65,536 after the simulated crash), <= 10
minutes, <= 2 GiB of generated outputs. Every input is generated text; the
tokenizer is a toy BPE; the model has a few thousand parameters.

Phase A (configured components, in process): science-v1 endpoint LR, explicit
training seed, process producer, absolute checkpoint milestones and rolling
recovery, evaluation cadence, retention, one authored evaluator fault at two
boundaries, continuation, end-of-run exact-checkpoint rescoring and release.

Phase B (public CLI and frozen queue): pilot plan from bindings -> validate ->
operator-fixture ticket -> EXECUTABLE -> submit -> queue run; the runner is
killed after a milestone checkpoint, stale-job recovery requeues it and the
resumed attempt receives only the remaining total wall allowance.
"""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest
import torch

from p35_m3_support import authored_bindings, authored_pilot_draft, build_inputs
from test_configurable_workflow import cli

MAX_TOTAL_TARGETS = 200_000
MAX_SECONDS = 600.0
MAX_OUTPUT_BYTES = 2 * 1024**3
PHASE_A_BUDGET = 16_384
PHASE_B_BUDGET = 65_536


class FailAt:
    """Authored fault injection: the real evaluator, failing once at named events."""

    def __init__(self, inner: Any, events: set[str]) -> None:
        self.inner = inner
        self.tier = inner.tier
        self.pending = set(events)
        self.failed: list[str] = []

    def identity(self) -> dict[str, Any]:
        return self.inner.identity()  # type: ignore[no-any-return]

    def evaluate(self, model: Any, *, device: str, context: Any) -> Any:
        if context.event_id in self.pending:
            self.pending.discard(context.event_id)
            self.failed.append(context.event_id)
            raise RuntimeError("authored fault injection: transient scorer failure")
        return self.inner.evaluate(model, device=device, context=context)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)


def tree_bytes(root: Path) -> int:
    return sum(p.stat().st_size for p in root.rglob("*") if p.is_file())


def committed_checkpoints(root: Path) -> dict[str, int]:
    found: dict[str, int] = {}
    for meta in root.glob("runs/*/artifacts/checkpoints/*/checkpoint_meta.json"):
        if (meta.parent / "_COMPLETED").is_file():
            found[meta.parent.name] = json.loads(meta.read_text())["committed_valid_targets"]
    return found


def kill_tree(pid: int) -> None:
    import psutil

    try:
        parent = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return
    for child in parent.children(recursive=True):
        try:
            child.kill()
        except psutil.NoSuchProcess:
            pass
    parent.kill()


def phase_a(tmp_path: Path, home: Path) -> dict[str, Any]:
    from xlm.artifacts.ledger import RunLedger
    from xlm.artifacts.store import ArtifactStore
    from xlm.core.paths import ArtifactPaths
    from xlm.evaluation.cadence import EventTier
    from xlm.experiments.execution import resolve_execution_config
    from xlm.experiments.science_pilot import (
        PilotBindings,
        SciencePilotConfig,
        apply_bindings,
    )
    from xlm.training.checkpoint import CheckpointManager
    from xlm.training.components import construct_training_components
    from xlm.training.trainer import Trainer

    inputs = build_inputs(tmp_path / "inputs_a", budget=PHASE_A_BUDGET, docs_per_source=200)
    draft = authored_pilot_draft(
        inputs,
        budget=PHASE_A_BUDGET,
        milestones=[0, 8192, PHASE_A_BUDGET],
        recovery=[2048, 4096, 6144, 10240, 12288, 14336],
        quick=[0, 4096, 8192, PHASE_A_BUDGET],
        full=[0, PHASE_A_BUDGET],
    )
    placeholder = tmp_path / "unused_size_source"
    bindings = PilotBindings.model_validate(
        authored_bindings(inputs, home=home, output_root=tmp_path, size_source=str(placeholder))
    )
    merged = apply_bindings(
        draft, SciencePilotConfig.model_validate(draft["science_pilot"]), bindings
    )
    resolved, _ = resolve_execution_config(copy.deepcopy(merged))
    components = construct_training_components(resolved, device="cuda")
    assert components.checkpoints is not None and components.evaluation is not None
    live = components.evaluation.evaluators[EventTier.QUICK_LM]
    faulty = FailAt(live, {"quick_lm@4096", "quick_lm@8192"})
    components.evaluation.evaluators[EventTier.QUICK_LM] = faulty
    paths = ArtifactPaths(root=home / "phase_a")
    trainer = Trainer(
        model=components.model,
        objective=components.objective,
        optimizer=components.optimizer,
        optimizer_manifest=components.optimizer_manifest,
        schedule=components.schedule,
        batcher=components.batcher,
        checkpoint_manager=CheckpointManager(
            artifact_store=ArtifactStore(paths),
            run_ledger=RunLedger(paths.ledger / "ledger.sqlite"),
            paths=paths,
        ),
        run_id="m3_toy_a",
        plan_id="m3_toy_a_plan",
        device="cuda",
        precision=resolved["training"]["precision"],
        max_valid_targets=PHASE_A_BUDGET,
        max_train_seconds=300,
        checkpoint_every_valid_targets=None,
        science=components.science,
        evaluation=components.evaluation,
        checkpoints=components.checkpoints,
    )
    started = time.monotonic()
    summary = trainer.train()
    seconds = time.monotonic() - started
    assert summary.termination_reason == "completed"
    assert trainer.committed_valid_targets == PHASE_A_BUDGET
    ledger = trainer.checkpoints.ledger  # type: ignore[union-attr]
    records = {r.artifact_id: r for r in ledger.records.values()}
    evaluation = trainer.evaluation
    histories = {
        e: [(h["number"], h["status"], h.get("route")) for h in evaluation._store.history(e)]
        for e in ("quick_lm@4096", "quick_lm@8192")
    }
    assert faulty.failed == ["quick_lm@4096", "quick_lm@8192"]
    for event_id, history in histories.items():
        assert history == [
            (1, "failed", None),
            (2, "complete", "retained_exact_checkpoint_rescore_v1"),
        ], event_id
    assert evaluation.completeness().complete
    # The recovery state held for the failed quick_lm@4096 was kept past the
    # latest-two rule, then released by the retention pass after its rescore.
    t4096 = records["m3_toy_a_ckpt-t4096-a001"]
    kept_reasons = [
        entry["decision"]["keep"].get("m3_toy_a_ckpt-t4096-a001") for entry in ledger.retention_log
    ]
    assert ["evaluation_dependency:quick_lm@4096"] in kept_reasons
    assert t4096.status == "retired" and t4096.retired is not None
    receipts = trainer.science.lr_receipts
    first = receipts[0]
    assert first[1] == 0 and first[2] == 256 and first[3] == 256
    expected_first = components.schedule.get_lr(256)
    assert all(abs(lr - expected_first) < 1e-15 for lr in first[4])
    assert trainer.science.train_start_rng["training_seed"] == 10001
    runtime = trainer.science.runtime_receipts[-1]
    assert runtime["attention_policy"] == "statistical_efficient_v1"
    assert set(runtime["expected_ops"]) <= set(runtime["observed_ops"])
    final = [r for r in ledger.published() if r.actual_committed_targets == PHASE_A_BUDGET]
    size_source = home / "phase_a" / "checkpoints" / final[0].artifact_id
    return {
        "seconds": seconds,
        "committed_targets": trainer.committed_valid_targets,
        "updates": len(receipts),
        "first_lr_receipt": first,
        "train_start_rng": trainer.science.train_start_rng,
        "runtime_observed_ops": runtime["observed_ops"],
        "checkpoints": [
            {
                "artifact_id": r.artifact_id,
                "events": r.events,
                "planned_thresholds": r.planned_thresholds,
                "actual_committed_targets": r.actual_committed_targets,
                "step": r.step,
                "role": r.role,
                "status": r.status,
                "retired_reason": (r.retired or {}).get("reason"),
            }
            for r in ledger.records.values()
        ],
        "retention_decisions": len(ledger.retention_log),
        "rescore_histories": histories,
        "evaluation_complete": evaluation.completeness().complete,
        "size_source": str(size_source),
        "inputs_vocab": inputs["vocab_size"],
    }


@pytest.mark.cuda
@pytest.mark.serial
def test_m3_authored_toy_flow(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    if not torch.cuda.is_available():
        pytest.skip("CUDA hardware required")
    from test_frozen_execution import authored_tree
    from xlm.artifacts.ledger import RunLedger
    from xlm.core.paths import ArtifactPaths
    from xlm.experiments.queue import ExperimentQueue

    flow_started = time.monotonic()
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("XLM_HOME", str(home))
    evidence: dict[str, Any] = {"label": "P35 M3 authored/synthetic toy flow (not the 32M pilot)"}
    evidence["phase_a"] = phase_a(tmp_path, home)

    # ------------------------------------------------------------- phase B
    tree = authored_tree(tmp_path / "tree")
    commands = tmp_path / "commands"
    inputs = build_inputs(tmp_path / "inputs_b", budget=PHASE_B_BUDGET, docs_per_source=200)
    draft = authored_pilot_draft(
        inputs,
        budget=PHASE_B_BUDGET,
        milestones=[0, 16_384, PHASE_B_BUDGET],
        recovery=[32_768, 49_152],
        quick=[0, 8192, 16_384, 32_768, 49_152, PHASE_B_BUDGET],
        full=[0, PHASE_B_BUDGET],
        total_wall_seconds=540,
    )
    out = tmp_path / "out"
    out.mkdir()
    bindings = authored_bindings(
        inputs, home=home, output_root=out, size_source=evidence["phase_a"]["size_source"]
    )
    bindings["storage_roots"]["data_root"] = str(tmp_path.resolve())
    draft_path = tree / "m3_toy_draft.yaml"
    draft_path.write_text(json.dumps(draft, indent=2), encoding="utf-8")
    bindings_path = out / "bindings.json"
    bindings_path.write_text(json.dumps(bindings, indent=2), encoding="utf-8")
    plan, review, snap = out / "plan.json", out / "review.json", out / "snapshot"
    planned = cli(tree, home, commands, "experiment", "plan", str(draft_path), "--bindings",
                  str(bindings_path), "--output", str(plan), "--review", str(review),
                  "--snapshot-dir", str(snap))  # fmt: skip
    plan_review = json.loads(review.read_text(encoding="utf-8"))
    assert plan_review["status"] == "RESOLVED", plan_review["blockers"]
    plan_hash = plan_review["plan"]["plan_hash"]
    frozen = json.loads(plan.read_text(encoding="utf-8"))
    assert "Science pilot:   RESOLVED" in planned.stdout
    validated = cli(tree, home, commands, "experiment", "validate", str(plan))
    assert "Science pilot:   RESOLVED" in validated.stdout
    ticket = out / "ticket.json"
    cli(tree, home, commands, "experiment", "authorize", "--plan-hash", plan_hash,
        "--max-targets", str(PHASE_B_BUDGET), "--max-seconds", str(frozen["budget_max_seconds"]),
        "--max-disk-gib", str(frozen["storage_estimate_gib"]), "--approver", "m3-toy-fixture",
        "--ticket-id", "M3-TOY", "--output", str(ticket))  # fmt: skip
    executable = cli(tree, home, commands, "experiment", "validate", str(plan), "--ticket",
                     str(ticket))  # fmt: skip
    assert "Science pilot:   EXECUTABLE" in executable.stdout
    cli(tree, home, commands, "experiment", "submit", str(plan), "--ticket", str(ticket),
        "--device", "cuda", "--snapshot-dir", str(snap), "--max-retries", "1")  # fmt: skip

    env = {
        **os.environ,
        "XLM_HOME": str(home),
        "PYTHONPATH": str(tree / "src"),
        "UV_OFFLINE": "1",
        "HF_HUB_OFFLINE": "1",
        "HF_DATASETS_OFFLINE": "1",
        "OMP_NUM_THREADS": "1",
        "MKL_NUM_THREADS": "1",
    }
    with (out / "runner-1.log").open("wb") as log:
        runner = subprocess.Popen(
            [sys.executable, "-m", "xlm.cli.main", "queue", "run", "--device", "cuda", "--once"],
            cwd=tree,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        killed_after: dict[str, int] | None = None
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline and runner.poll() is None:
            found = committed_checkpoints(home)
            if any(c >= 16_384 for c in found.values()):
                kill_tree(runner.pid)
                killed_after = found
                break
            time.sleep(0.05)
        runner.wait(timeout=30)
    assert killed_after is not None, "the runner finished before the simulated crash"

    paths = ArtifactPaths(root=home)
    queue = ExperimentQueue(
        RunLedger(paths.ledger / "ledger.sqlite"), paths, tree_root=tree, heartbeat_stale_seconds=1
    )
    time.sleep(2)
    [job_id] = queue.recover_stale_jobs()
    work = home / "runs" / job_id
    crashed = json.loads((work / "wall_allowance.json").read_text(encoding="utf-8"))
    assert crashed["attempts"][0]["outcome"] == "running"  # left by the killed runner
    resumed = queue.run_next("cuda")
    assert resumed["state"] == "SUCCEEDED", resumed
    allowance = json.loads((work / "wall_allowance.json").read_text(encoding="utf-8"))
    first, second = allowance["attempts"]
    assert first["outcome"] == "interrupted_runner_lost" and first["seconds"] > 0
    assert second["outcome"] == "succeeded"
    assert abs(second["remaining_at_start"] - (540 - first["seconds"])) < 1e-6  # no reset
    assert allowance["consumed_seconds"] <= 540 and allowance["status"] == "completed"
    process = json.loads((work / "worker-2" / "process.json").read_text(encoding="utf-8"))
    assert process["wall_seconds_limit"] == pytest.approx(second["remaining_at_start"])

    final_ckpts = committed_checkpoints(home)
    names = sorted(final_ckpts)
    final_name = [n for n, c in final_ckpts.items() if c == PHASE_B_BUDGET]
    assert final_name, names
    science = json.loads(
        (work / "artifacts" / "checkpoints" / final_name[0] / "science.json").read_text()
    )
    ledger = science["checkpoints"]
    by_event = {e["event_id"]: e for e in ledger["events"]}
    assert by_event["checkpoint@16384"]["due"]["actual_committed_targets"] == 16_384
    assert by_event["checkpoint@65536"]["due"]["actual_committed_targets"] == PHASE_B_BUDGET
    milestone_ids = [n for n in names if "-t16384-" in n]
    assert milestone_ids == [f"{job_id}_ckpt-t16384-a001"]  # never republished
    rows = science["lr_receipts"]["rows"]
    assert [r[2] for r in rows] == [256] * (PHASE_B_BUDGET // 256)
    # The endpoint checkpoint is published between recording the endpoint
    # crossings and scoring them, so it owes exactly its endpoint evaluations.
    owed = {e["event_id"] for e in science["evaluation"]["events"] if e["status"] != "complete"}
    assert owed == {f"quick_lm@{PHASE_B_BUDGET}", f"full_lm@{PHASE_B_BUDGET}"}
    run_record = json.loads((work / "run_record.json").read_text(encoding="utf-8"))
    assert run_record["status"] == "SUCCEEDED"
    assert run_record["result"]["resumed_checkpoint"]
    assert run_record["result"]["evaluation"]["complete"] is True
    from xlm.evaluation.rescore import load_run_evaluation_state

    durable = load_run_evaluation_state(work / "artifacts" / "checkpoints" / final_name[0])
    assert durable.ledger.completeness().complete  # reconciled from immutable attempts

    seconds = time.monotonic() - flow_started
    produced = tree_bytes(tmp_path)
    upper_targets = PHASE_A_BUDGET + 2 * PHASE_B_BUDGET
    assert upper_targets <= MAX_TOTAL_TARGETS
    assert seconds <= MAX_SECONDS, seconds
    assert produced <= MAX_OUTPUT_BYTES, produced
    evidence["phase_b"] = {
        "plan_hash": plan_hash,
        "review_status": plan_review["status"],
        "validate": "RESOLVED",
        "validate_with_ticket": "EXECUTABLE",
        "killed_after_checkpoints": killed_after,
        "resumed_from": run_record["result"]["resumed_checkpoint"],
        "wall_allowance": allowance,
        "published_checkpoints": final_ckpts,
        "checkpoint_events": {
            k: {"status": v["status"], "due": v["due"]} for k, v in by_event.items()
        },
        "retention_log_entries": len(ledger["retention_log"]),
        "endpoint_checkpoint_owes": sorted(owed),
        "evaluation_completeness_run_result": run_record["result"]["evaluation"],
        "evaluation_completeness_reconciled": durable.ledger.completeness().to_dict(),
        "updates": len(rows),
    }
    evidence["bounds"] = {
        "training_targets_upper_bound": upper_targets,
        "committed_targets": PHASE_A_BUDGET + PHASE_B_BUDGET,
        "wall_seconds": seconds,
        "produced_bytes": produced,
        "limits": {
            "targets": MAX_TOTAL_TARGETS,
            "seconds": MAX_SECONDS,
            "bytes": MAX_OUTPUT_BYTES,
        },
    }
    target = os.environ.get("XLM_M3_TOY_EVIDENCE")
    if target:
        Path(target).write_text(json.dumps(evidence, indent=2, sort_keys=True, default=str))
