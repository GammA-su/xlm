"""Verified captured-process entry point; delegates to existing execution paths."""

from __future__ import annotations

import os
import random
from pathlib import Path
from typing import Any

from xlm.experiments.execution import (
    execution_context,
    observed_origins,
    validate_envelope,
    write_json,
)


def validate_request_options(request: dict[str, Any]) -> None:
    """The launch descriptor cannot override already frozen computation."""
    if request["action"] not in ("train", "resume"):
        return
    config = request["envelope"]["config"]
    training = config["training"]
    options = request["options"]
    expected = {
        "device": training["device"],
        "max_targets": training["budget"]["max_valid_targets"],
        "budget": training["budget"]["max_valid_targets"],
        "max_seconds": training["budget"]["max_train_seconds"],
        "precision": training["precision"],
        "attention_backend": config["model"]["attention_backend"],
        "compile_model": training["compile"],
        "activation_checkpointing": training["activation_checkpointing"],
    }
    for key, value in expected.items():
        if options.get(key) is not None and options[key] != value:
            raise ValueError(f"worker option '{key}' differs from frozen execution")
    if options.get("dry_run"):
        raise ValueError("a frozen training worker cannot be a dry run")


def run_worker(request: dict[str, Any]) -> int:
    import psutil

    write_json(
        Path(request["work"]) / "worker_identity.json",
        {"pid": os.getpid(), "create_time": psutil.Process().create_time()},
    )
    envelope = request["envelope"]
    expected_purpose = "evaluation" if request["action"] == "evaluate" else "training"
    if envelope["purpose"] != expected_purpose:
        raise ValueError("worker action/execution purpose mismatch")
    snapshot_dir = Path(request["snapshot_dir"])
    snapshot = validate_envelope(
        envelope, snapshot_dir, site=Path(request["runtime_locations"]["site_packages"])
    )
    validate_request_options(request)
    import torch

    from xlm.training.trainer import Trainer

    torch.set_num_threads(envelope["runtime_policy"]["torch_threads"])
    if "deterministic_algorithms" in envelope["runtime_policy"]:
        # Historical frozen policy, applied process-wide exactly as before.
        # Science-v1 runtime flags are owned and restored by the trainer scope.
        torch.use_deterministic_algorithms(envelope["runtime_policy"]["deterministic_algorithms"])
    seed = envelope["config"].get("training", {}).get("init_seed", 0)
    random.seed(seed)
    torch.manual_seed(seed)
    context = execution_context(envelope, snapshot_dir)
    context["observations"]["site_packages"] = request["runtime_locations"]["site_packages"]
    context["plan_hash"] = (
        request["plan_hash"] if "plan_hash" in request else request["plan"]["plan_hash"]
    )
    context["observations"]["train_step_origin"] = str(
        Path(Trainer.train_step.__code__.co_filename).resolve()
    )
    work = Path(request["work"])
    context["observations"]["work_dir"] = str(work)
    context["observations"]["max_owned_disk_bytes"] = request.get(
        "max_owned_disk_bytes", 2 * 1024**3
    )
    action = request["action"]
    result: dict[str, Any]
    if action == "queue":
        from xlm.experiments.plans import ExecutablePlan
        from xlm.experiments.queue import QueueJob, execute_plan_run

        plan = ExecutablePlan.from_dict(request["plan"])
        plan.validate_identity()
        if plan.execution_envelope != envelope:
            raise ValueError("worker plan/envelope mismatch")
        result = execute_plan_run(
            plan,
            QueueJob(**request["job"]),
            lambda: (work / "cancel.requested").exists(),
            execution=context,
        )
    elif action == "train":
        from xlm.cli.train_cmd import _train_in_process

        _train_in_process(Path(request["plan_path"]), execution=context, **request["options"])
        result = {"state": "SUCCEEDED"}
    elif action == "resume":
        from xlm.cli.train_cmd import _resume_in_process

        _resume_in_process(Path(request["checkpoint"]), execution=context, **request["options"])
        result = {"state": "SUCCEEDED"}
    elif action == "evaluate":
        from xlm.cli.eval_cmd import _evaluate_in_process

        options = dict(request["options"])
        frozen_options = {
            k: v for k, v in options.items() if k not in ("output_dir", "output_json")
        }
        if frozen_options != envelope["config"]:
            raise ValueError("evaluation options differ from frozen execution")
        for key in (
            "checkpoint",
            "include_path",
            "output_dir",
            "pins_path",
            "final_authorization_file",
            "inputs",
            "acquisition_store_root",
        ):
            if options.get(key) is not None:
                options[key] = Path(options[key])
        _evaluate_in_process(**options, execution=context)
        result = {"state": "SUCCEEDED"}
    else:
        raise ValueError("unknown frozen worker action")
    context["observations"]["module_origins"] = observed_origins(snapshot_dir, snapshot)
    result.update(
        execution_hash=envelope["execution_hash"], worker_pid=os.getpid(), execution=context
    )
    write_json(work / "result.json", result)
    return 0
