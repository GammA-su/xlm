"""Working trainer engine with exact budgets, microbatch accumulation, and safe resume.

Complying with XLM Contracts C08, C09, C10 and P05 Amendments 1, 4, 5, 6, 7 & 8:
- Normalized by actual total valid targets N_global across microbatches.
- Global gradient clipping over unique parameters after accumulation.
- Non-finite loss/gradient guard raising NonFiniteGradientError.
- Exact token budget completion with masked final update.
- Replayable data state committed at update boundaries.
- Cooperative interruption saving cleanly at optimizer boundaries.
- Structured metrics reporting with CE vs objective separation and throughput.
"""

from __future__ import annotations

import contextlib
import math
import signal
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from xlm.config.science import LEGACY_LR_POLICY, ScientificPolicy
from xlm.core.contracts import RunStatus, TrainingBatch
from xlm.models.backends import maybe_compile, validate_precision
from xlm.models.base import BaseModel
from xlm.objectives.base import BaseObjective, accumulate_microbatch_gradient
from xlm.optimizers import (
    ParameterGroupManifest,
    clip_global_gradient_norm,
)
from xlm.schedules.base import BaseSchedule
from xlm.training.checkpoint import CheckpointManager
from xlm.training.data import BatcherProtocol
from xlm.training.science import (
    ScientificRuntime,
    ScientificState,
    validate_group_lr_semantics,
)

try:
    import torch
    import torch.nn as nn
    import torch.optim as optim
except ImportError:
    torch = None  # type: ignore[assignment]
    nn = None  # type: ignore[assignment]
    optim = None  # type: ignore[assignment]

if TYPE_CHECKING:
    from xlm.training.evaluation import EvaluationController
    from xlm.training.milestones import CheckpointController


class TrainerError(Exception):
    """Base exception for training errors."""


class NonFiniteGradientError(TrainerError):
    """Raised when non-finite (NaN or Inf) loss or gradients are detected."""


class RecoveryRequiredError(TrainerError):
    """An interrupted optimizer boundary must resume in a fresh trainer."""


@dataclass(frozen=True)
class TrainingStepMetrics:
    """Structured metrics emitted for each completed optimizer update.

    ``learning_rate`` keeps its historical meaning under every policy: the
    schedule evaluated at the post-update committed count (under the legacy
    policy, the LR the *next* update will see). ``lr_used`` is what
    ``optimizer.step`` actually read, per parameter group. ``lr_next`` is the
    LR already installed for the next update, or ``None`` when the policy
    derives it from that update's actual valid targets.
    """

    step: int
    loss: float
    model_ce_loss: float
    auxiliary_loss: float
    valid_targets: int
    committed_valid_targets: int
    processed_valid_targets: int
    grad_norm: float
    learning_rate: float
    step_time_seconds: float
    lr_policy: str = LEGACY_LR_POLICY
    lr_used: tuple[float, ...] = ()
    lr_schedule_counter: int | None = None
    lr_next: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TrainingSummary:
    """Final summary of a training run."""

    run_id: str
    plan_id: str
    total_steps: int
    committed_valid_targets: int
    processed_valid_targets: int
    total_training_time_seconds: float
    setup_time_seconds: float
    termination_reason: str  # 'completed' | 'interrupted' | 'resource_limit_reached' | 'failed'
    final_loss: float | None
    metrics_history: list[TrainingStepMetrics]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class Trainer:
    """Production training engine for local CPU and CUDA execution."""

    def __init__(
        self,
        model: BaseModel,
        objective: BaseObjective,
        optimizer: optim.Optimizer,
        optimizer_manifest: ParameterGroupManifest,
        schedule: BaseSchedule,
        batcher: BatcherProtocol,
        checkpoint_manager: CheckpointManager,
        run_id: str,
        plan_id: str,
        device: str = "cpu",
        precision: str = "fp32",
        gradient_clip_norm: float = 1.0,
        max_valid_targets: int = 100,
        max_train_seconds: float | None = None,
        checkpoint_every_valid_targets: int | None = None,
        parent_checkpoint_id: str | None = None,
        parent_plan_id: str | None = None,
        step: int = 0,
        committed_valid_targets: int = 0,
        processed_valid_targets: int = 0,
        activation_checkpointing: bool = False,
        compile_model: bool = False,
        compile_mode: str = "default",
        max_scaler_skips: int = 50,
        science: ScientificState | None = None,
        evaluation: EvaluationController | None = None,
        checkpoints: CheckpointController | None = None,
    ) -> None:
        self.model = model
        self.objective = objective
        self.optimizer = optimizer
        self.optimizer_manifest = optimizer_manifest
        self.schedule = schedule
        self.batcher = batcher
        self.checkpoint_manager = checkpoint_manager
        self.run_id = run_id
        self.plan_id = plan_id
        self.device = device
        self.gradient_clip_norm = gradient_clip_norm
        self.max_valid_targets = max_valid_targets
        self.max_train_seconds = max_train_seconds
        self.checkpoint_every_valid_targets = checkpoint_every_valid_targets
        self.parent_checkpoint_id = parent_checkpoint_id
        self.parent_plan_id = parent_plan_id

        # Precision is validated loudly: unknown modes and device combinations
        # that cannot execute the mode raise instead of degrading silently.
        self.precision = validate_precision(precision, device)
        self.activation_checkpointing = activation_checkpointing
        self.compile_model = compile_model
        self.compile_mode = compile_mode

        # The checkpoint-compatible model is always the uncompiled module:
        # compiled state_dicts carry `_orig_mod.` prefixes that would break
        # resume into an uncompiled model. Execution uses the compiled path.
        self.exec_model = model
        self.compile_report: dict[str, Any] = {"compiled": False, "mode": None}
        if activation_checkpointing:
            if hasattr(model, "activation_checkpointing"):
                setattr(model, "activation_checkpointing", True)  # noqa: B010
            else:
                raise TrainerError(
                    "Activation checkpointing requested but the model does not "
                    "support it. Enable it explicitly on a supporting model."
                )
        if compile_model:
            self.exec_model, self.compile_report = maybe_compile(model, True, compile_mode)

        self.step = step
        self.committed_valid_targets = committed_valid_targets
        self.processed_valid_targets = processed_valid_targets
        self.next_checkpoint_target = (
            (committed_valid_targets + checkpoint_every_valid_targets)
            if checkpoint_every_valid_targets is not None
            else None
        )

        self.total_training_time_seconds = 0.0
        self.setup_time_seconds = 0.0
        self.termination_reason = "completed"
        self._stop_requested = False
        self.max_scaler_skips = max_scaler_skips
        self._consecutive_scaler_skips = 0
        self._last_step_skipped = False
        self._update_in_doubt = False
        self._evaluation_compromised = False
        self.checkpoint_manager.boundary_guard = self._require_committed_boundary

        # Precision scaler setup
        self.scaler: Any = None
        if (
            self.precision in ("fp16", "fp16_scaler")
            and self.device == "cuda"
            and torch is not None
        ):
            self.scaler = torch.amp.GradScaler("cuda")

        # Scientific policy: legacy unless a science-v1 state is supplied.
        self.science = science or ScientificState(ScientificPolicy.legacy())
        self.runtime = ScientificRuntime(self.science.policy.runtime, self.device)
        if self.science.policy.is_science:
            validate_group_lr_semantics(
                optimizer,
                optimizer_manifest.groups,
                getattr(schedule, "base_lr", None),
            )
            self.runtime.check_environment()
            model_config = getattr(model, "config", None)
            backend = str(getattr(model_config, "attention_backend", "unknown"))
            hidden = int(getattr(model_config, "hidden_size", 0))
            heads = int(getattr(model_config, "num_attention_heads", 0))
            if heads <= 0 or hidden % heads:
                raise TrainerError("science-v1 requires a model config declaring attention shape")
            self.science.record_runtime(
                self.runtime.attention_receipt(
                    attention_backend=backend,
                    num_heads=heads,
                    head_dim=hidden // heads,
                    precision=self.precision,
                )
            )

        # Science-v1 evaluation events (P35 M2). Legacy runs never get a controller.
        self.evaluation = evaluation
        if evaluation is not None:
            if not self.science.policy.is_science:
                raise TrainerError("an evaluation cadence requires the xlm-science-v1 policy")
            evaluation.bind_trainer(self)
        # Science-v1 absolute checkpoint events (P35 M3). They replace the relative
        # cadence; both at once would publish checkpoints nobody planned.
        self.checkpoints = checkpoints
        if checkpoints is not None:
            if not self.science.policy.is_science:
                raise TrainerError("a checkpoint cadence requires the xlm-science-v1 policy")
            if checkpoint_every_valid_targets is not None:
                raise TrainerError(
                    "a science checkpoint cadence replaces checkpoint_every_valid_targets; "
                    "pass None for the relative cadence"
                )
            checkpoints.bind_trainer(self)

    def _get_autocast_context(self) -> Any:
        """Return precision context manager."""
        if torch is None:
            return contextlib.nullcontext()

        if self.precision == "bf16_fp32_master" and self.device == "cuda":
            return torch.autocast(device_type="cuda", dtype=torch.bfloat16)
        if self.precision in ("fp16", "fp16_scaler") and self.device == "cuda":
            return torch.autocast(device_type="cuda", dtype=torch.float16)
        return contextlib.nullcontext()

    def _get_unique_trainable_parameters(self) -> list[nn.Parameter]:
        """Collect deduplicated unique trainable parameters across model and objective."""
        unique_params: list[nn.Parameter] = []
        seen_ids: set[int] = set()

        for p in self.model.parameters():
            if p.requires_grad and id(p) not in seen_ids:
                seen_ids.add(id(p))
                unique_params.append(p)

        if self.objective is not None:
            for p in self.objective.parameters():
                if p.requires_grad and id(p) not in seen_ids:
                    seen_ids.add(id(p))
                    unique_params.append(p)

        return unique_params

    def _save_checkpoint(self, checkpoint_id: str) -> Path:
        """Save a checkpoint at the current state."""
        self._require_committed_boundary()
        return self.checkpoint_manager.save_checkpoint(
            checkpoint_id=checkpoint_id,
            run_id=self.run_id,
            step=self.step,
            committed_valid_targets=self.committed_valid_targets,
            processed_valid_targets=self.processed_valid_targets,
            plan_id=self.plan_id,
            model=self.model,
            objective=self.objective,
            optimizer=self.optimizer,
            optimizer_manifest=self.optimizer_manifest,
            schedule=self.schedule,
            batcher=self.batcher,
            parent_checkpoint_id=self.parent_checkpoint_id,
            parent_plan_id=self.parent_plan_id,
            scaler=self.scaler,
            precision=self.precision,
            science=self.science,
        )

    def _require_committed_boundary(self) -> None:
        if self._update_in_doubt or self._evaluation_compromised:
            raise RecoveryRequiredError("Resume a complete checkpoint in a fresh trainer")

    def execution_report(self) -> dict[str, Any]:
        """Report the exact execution modes this trainer runs under.

        Precision, backend, checkpointing and compile settings are recorded so a
        run can prove what it executed -- modes are never implied by flags that
        were silently ignored.
        """
        backend = getattr(getattr(self.model, "config", None), "attention_backend", "unknown")
        return {
            "device": self.device,
            "precision": self.precision,
            "attention_backend": backend,
            "activation_checkpointing": bool(
                getattr(self.model, "activation_checkpointing", False)
            ),
            "compile": dict(self.compile_report),
            "scaler_active": self.scaler is not None,
            "scientific_policy": self.science.policy.identity(),
            "runtime_receipt": (
                self.science.runtime_receipts[-1] if self.science.runtime_receipts else None
            ),
        }

    def _skip_scaler_step(self, reason: str) -> None:
        """Skip one overflowed update under the loss-scaler protocol.

        Rolls the batcher back so no data is consumed, clears partial gradients,
        and backs the scaler off. A bounded number of consecutive skips is
        tolerated; persistent overflow fails the run loudly (C10) instead of
        burning the budget on skipped updates forever.
        """
        self.batcher.rollback()
        self.optimizer.zero_grad()
        if self.scaler is not None:
            self.scaler.update()
        self._consecutive_scaler_skips += 1
        self._last_step_skipped = True
        if self._consecutive_scaler_skips > self.max_scaler_skips:
            raise NonFiniteGradientError(
                f"Persistent scaler overflow ({self._consecutive_scaler_skips} consecutive "
                f"skipped updates): {reason}"
            )

    @property
    def _has_boundary_events(self) -> bool:
        return self.evaluation is not None or self.checkpoints is not None

    def save_terminal_checkpoint(self, reason: str) -> Path:
        """Final/interrupted/cancelled/time-limit state.

        Legacy runs keep their historical ``{run_id}_{reason}`` ids. With a
        science checkpoint cadence the state is published through the event
        ledger, and a boundary that already has a published checkpoint (for
        example the exact-budget endpoint milestone) is reused, never duplicated.
        """
        if self.checkpoints is not None:
            return self.checkpoints.save_terminal(self, reason)
        return self._save_checkpoint(f"{self.run_id}_{reason}")

    def recover_failed_evaluations(self) -> list[dict[str, Any]] | None:
        """At the exact budget: rescore past unresolved events from exact retained checkpoints.

        Requires both science-v1 cadences. Afterwards retention runs again, so a
        checkpoint kept only for an event that is now complete is released
        under the normal policy instead of being retained indefinitely.
        """
        if self.evaluation is None or self.checkpoints is None:
            return None
        report = self.evaluation.recover_from_retained(self, self.checkpoints)
        if any(entry.get("status") == "complete" for entry in report):
            last = self.checkpoints.ledger.last_good()
            if last is not None:
                self.checkpoints.apply_retention(self, after=last.artifact_id)
        self.checkpoints.ledger.note({"rescore_pass": report})
        return report

    def train_step(self) -> TrainingStepMetrics | None:
        """Rollback failed preparation; never reuse a partially updated optimizer."""
        self._require_committed_boundary()
        if self._has_boundary_events:
            # Initial-state events and events restored as due by a resume.
            self._evaluate_boundary()
        try:
            metrics = self._train_step()
        except BaseException:
            try:
                self.batcher.rollback()
            except Exception:
                # Preserve the primary failure (especially optimizer ambiguity).
                # A failed producer rollback already closes/poisons its transport.
                pass
            raise
        if metrics is not None and self._has_boundary_events:
            self._evaluate_boundary()
        return metrics

    def _evaluate_boundary(self) -> None:
        """Science-v1 events of the committed state, in the fixed boundary order.

        Evaluation crossings are recorded first, then the boundary's checkpoint
        is published (so it owes those evaluations), then evaluations run.
        Never mid-update; each phase is a no-op when already handled.
        """
        assert self._has_boundary_events
        self._require_committed_boundary()
        if self.evaluation is not None:
            self.evaluation.record_crossings(self)
        if self.checkpoints is not None:
            self.checkpoints.at_boundary(self)
        if self.evaluation is not None:
            self.evaluation.run_pending(self)

    def _train_step(self) -> TrainingStepMetrics | None:
        """Execute one update inside the run's scientific runtime scope (legacy: none)."""
        with self.runtime.scope():
            return self._train_update()

    def _train_update(self) -> TrainingStepMetrics | None:
        """Execute exactly one complete optimizer update across microbatches."""
        remaining_budget = self.max_valid_targets - self.committed_valid_targets
        if remaining_budget <= 0:
            return None
        self._last_step_skipped = False

        step_start = time.monotonic()

        # 1. Fetch microbatches with exact remaining budget cap
        microbatches = self.batcher.next_step_microbatches(remaining_budget=remaining_budget)
        if not microbatches:
            return None

        # 2. Count total valid targets across all microbatches in this update
        n_global = sum(
            int(mb.loss_mask.sum().item()) if hasattr(mb.loss_mask, "sum") else sum(mb.loss_mask)
            for mb in microbatches
        )

        if n_global <= 0:
            # Wholly empty update: skip step, decay, and schedule advance
            self.batcher.commit()
            return None

        # Endpoint policy: derive f(C+N) from the actual valid N before any
        # compute. It is installed immediately before optimizer mutation, so
        # every pre-mutation failure leaves group LRs untouched.
        committed_before = self.committed_valid_targets
        endpoint_lr: float | None = None
        if self.science.policy.endpoint_lr:
            endpoint_lr = self.schedule.get_lr(committed_before + n_global)

        # 3. Accumulate gradients across microbatches
        accumulated_loss = 0.0
        accumulated_ce_loss = 0.0
        accumulated_aux_loss = 0.0

        self.model.train()
        self.objective.train()
        self.optimizer.zero_grad()

        autocast_cm = self._get_autocast_context()

        for mb in microbatches:
            mb_valid = (
                int(mb.loss_mask.sum().item())
                if hasattr(mb.loss_mask, "sum")
                else sum(mb.loss_mask)
            )
            if mb_valid == 0:
                continue

            # Move batch inputs to device
            input_ids = (
                mb.input_ids.to(self.device) if hasattr(mb.input_ids, "to") else mb.input_ids
            )
            labels = mb.labels.to(self.device) if hasattr(mb.labels, "to") else mb.labels
            loss_mask = (
                mb.loss_mask.to(self.device) if hasattr(mb.loss_mask, "to") else mb.loss_mask
            )
            pos_ids = (
                mb.position_ids.to(self.device)
                if mb.position_ids is not None and hasattr(mb.position_ids, "to")
                else mb.position_ids
            )

            mb_device = TrainingBatch(
                input_ids=input_ids,
                labels=labels,
                loss_mask=loss_mask,
                position_ids=pos_ids,
                segment_ids=mb.segment_ids,
                source_attribution=mb.source_attribution,
                metadata=mb.metadata,
            )

            attention_mask = mb_device.loss_mask
            if "input_attention_mask" in mb.metadata:
                prepared = mb.metadata["input_attention_mask"]
                # A prefetched update carries a CPU bool tensor; lists keep the old path.
                attention_mask = (
                    prepared.to(device=self.device, dtype=torch.bool)
                    if isinstance(prepared, torch.Tensor)
                    else torch.tensor(prepared, dtype=torch.bool, device=self.device)
                )
            if mb.metadata.get("packing_mode") == "isolated_document":
                from xlm.models.masks import build_isolated_document_mask

                segments = torch.as_tensor(mb.segment_ids, dtype=torch.long, device=self.device)
                attention_mask = build_isolated_document_mask(segments, attention_mask)
            with autocast_cm:
                model_out = self.exec_model(
                    input_ids=mb_device.input_ids,
                    attention_mask=attention_mask,
                    position_ids=mb_device.position_ids,
                )
                loss_res = self.objective(model_out, mb_device)

            loss_val = (
                float(loss_res.loss.item())
                if hasattr(loss_res.loss, "item")
                else float(loss_res.loss)
            )
            if math.isnan(loss_val) or math.isinf(loss_val):
                if self.scaler is not None:
                    # Loss-scaler protocol: skip the update without consuming data
                    # and let the scaler back off, instead of failing the run.
                    self._skip_scaler_step(f"Non-finite loss '{loss_val}' at step {self.step}")
                    return None
                self.batcher.rollback()
                raise NonFiniteGradientError(
                    f"Non-finite loss '{loss_val}' encountered at step {self.step}"
                )

            # Weight by actual valid target share in this microbatch
            share = float(mb_valid) / float(n_global)
            accumulated_loss += loss_val * share
            accumulated_ce_loss += loss_res.unscaled_loss_sum / float(n_global)
            accumulated_aux_loss += getattr(loss_res, "auxiliary_loss", 0.0) * share

            # Backward accumulation
            if self.scaler is not None:
                scale = float(loss_res.valid_target_denominator) / float(n_global)
                self.scaler.scale(loss_res.loss * scale).backward()
            else:
                accumulate_microbatch_gradient(loss_res, total_valid_targets=n_global)

        # 4. Check for non-finite gradients
        if self.scaler is not None:
            self.scaler.unscale_(self.optimizer)

        unique_params = self._get_unique_trainable_parameters()
        from xlm.optimizers.clipping import gradients_are_finite

        if not gradients_are_finite(unique_params):
            if self.scaler is not None:
                self._skip_scaler_step(f"Non-finite gradient at step {self.step}")
                return None
            self.batcher.rollback()
            raise NonFiniteGradientError(
                f"Non-finite gradient encountered in parameter at step {self.step}"
            )

        # 5. Global gradient clipping over unique parameters
        grad_norm = clip_global_gradient_norm(unique_params, max_norm=self.gradient_clip_norm)

        # 6. Optimizer step & zero grad
        if endpoint_lr is not None:
            for group in self.optimizer.param_groups:
                group["lr"] = endpoint_lr * group.get("lr_multiplier", 1.0)
        lr_used = tuple(float(group["lr"]) for group in self.optimizer.param_groups)
        self._update_in_doubt = True
        if self.scaler is not None:
            self.scaler.step(self.optimizer)
            self.scaler.update()
        else:
            self.optimizer.step()

        self.optimizer.zero_grad()

        # 7. Advance schedule and counters
        if torch is not None and torch.device(self.device).type == "cuda":
            # Surface asynchronous optimizer failures before promoting the cursor.
            # The producer may continue speculative preparation during this wait.
            torch.cuda.current_stream(self.device).synchronize()
        new_committed = self.committed_valid_targets + n_global
        lr_next: float | None
        if endpoint_lr is None:
            lr = self.schedule.apply_lr_to_optimizer(self.optimizer, counter_value=new_committed)
            lr_next = lr
        else:
            # The next LR depends on the next update's actual N; only the
            # historical post-update schedule value is reported here.
            lr = self.schedule.get_lr(new_committed)
            lr_next = None
        receipt = (
            [self.step + 1, committed_before, n_global, new_committed, list(lr_used)]
            if self.science.policy.is_science
            else None
        )

        self.committed_valid_targets = new_committed
        self.processed_valid_targets += sum(
            mb.metadata.get("valid_target_count", 0) for mb in microbatches
        )
        self.step += 1

        # 8. Commit data batcher state for this completed update
        self.batcher.commit()
        self._update_in_doubt = False
        self._consecutive_scaler_skips = 0
        if receipt is not None:
            # Only a committed update publishes an LR receipt.
            self.science.record_lr(receipt)
        if self.evaluation is not None:
            # First crossing at this committed C; recorded before any periodic
            # checkpoint so that checkpoint knows the state still owes events.
            self.evaluation.record_crossings(self)

        step_sec = time.monotonic() - step_start
        self.total_training_time_seconds += step_sec

        metrics = TrainingStepMetrics(
            step=self.step,
            loss=accumulated_loss,
            model_ce_loss=accumulated_ce_loss,
            auxiliary_loss=accumulated_aux_loss,
            valid_targets=n_global,
            committed_valid_targets=self.committed_valid_targets,
            processed_valid_targets=self.processed_valid_targets,
            grad_norm=grad_norm,
            learning_rate=lr,
            step_time_seconds=step_sec,
            lr_policy=self.science.policy.lr_policy,
            lr_used=lr_used,
            lr_schedule_counter=new_committed if endpoint_lr is not None else None,
            lr_next=lr_next,
        )

        # 9. Checkpointing: science-v1 absolute events (first crossing, one
        # checkpoint per boundary), else the historical relative cadence.
        if self.checkpoints is not None:
            self.checkpoints.at_boundary(self)
        elif (
            self.next_checkpoint_target is not None
            and self.checkpoint_every_valid_targets is not None
        ):
            if self.committed_valid_targets >= self.next_checkpoint_target:
                chk_id = f"{self.run_id}_step_{self.step}_ckpt"
                self._save_checkpoint(chk_id)
                self.next_checkpoint_target += self.checkpoint_every_valid_targets

        return metrics

    def train(self) -> TrainingSummary:
        """Run training until budget is satisfied, time runs out, or stop is requested."""
        self._require_committed_boundary()
        setup_start = time.monotonic()
        # Move model to device
        self.model.to(self.device)
        if self.objective is not None:
            self.objective.to(self.device)
        self.setup_time_seconds = time.monotonic() - setup_start

        # Frozen callers supply verified identity; domain diagnostics remain unresolved.
        from xlm.artifacts.manifest import identity_digest

        execution = self.checkpoint_manager.execution
        plan_hash = (
            execution["plan_hash"]
            if execution
            else identity_digest({"unresolved_plan_label": self.plan_id, "scope": "domain-api"})
        )
        auth_token = (
            identity_digest(
                {
                    "policy": "direct-smoke",
                    "execution_hash": execution["envelope"]["execution_hash"],
                }
            )
            if execution
            else "unresolved-domain-api"
        )
        # Register run in ledger as RUNNING
        try:
            self.checkpoint_manager.ledger.register_run(
                run_id=self.run_id,
                experiment_id=self.plan_id,
                plan_hash=plan_hash,
            )
            self.checkpoint_manager.ledger.transition_run(
                self.run_id, from_state=RunStatus.DRAFT, to_state=RunStatus.PLANNED
            )
            self.checkpoint_manager.ledger.authorize_run(
                self.run_id,
                plan_hash=plan_hash,
                auth_token=auth_token,
            )
            self.checkpoint_manager.ledger.transition_run(
                self.run_id, from_state=RunStatus.AUTHORIZED, to_state=RunStatus.RUNNING
            )
        except Exception:
            # If run was already registered, transition to RUNNING directly
            try:
                run_rec = self.checkpoint_manager.ledger.get_run(self.run_id)
                if run_rec and run_rec["status"] != RunStatus.RUNNING.value:
                    from_st = RunStatus(run_rec["status"])
                    self.checkpoint_manager.ledger.transition_run(
                        self.run_id, from_state=from_st, to_state=RunStatus.RUNNING
                    )
            except Exception:
                pass

        # Setup cooperative interruption handler
        def _interrupt_handler(signum: int, frame: Any) -> None:
            self._stop_requested = True

        prev_sigint = signal.signal(signal.SIGINT, _interrupt_handler)
        prev_sigterm = None
        if hasattr(signal, "SIGTERM"):
            prev_sigterm = signal.signal(signal.SIGTERM, _interrupt_handler)

        metrics_history: list[TrainingStepMetrics] = []
        final_loss: float | None = None
        run_start = time.monotonic()

        try:
            if self._has_boundary_events:
                # A resume at the exact budget still owes its endpoint events.
                self._evaluate_boundary()
            while self.committed_valid_targets < self.max_valid_targets:
                if (
                    execution
                    and (Path(execution["observations"]["work_dir"]) / "cancel.requested").is_file()
                ):
                    self._stop_requested = True
                if self._stop_requested:
                    self.termination_reason = "interrupted"
                    break

                if self.max_train_seconds is not None:
                    if (time.monotonic() - run_start) >= self.max_train_seconds:
                        self.termination_reason = "resource_limit_reached"
                        break

                step_metrics = self.train_step()
                if step_metrics is None:
                    if self._last_step_skipped:
                        # Scaler overflow: data unconsumed, scaler backed off.
                        # The wall-time limit still bounds a pathological run.
                        continue
                    break

                metrics_history.append(step_metrics)
                final_loss = step_metrics.loss

            if self.committed_valid_targets >= self.max_valid_targets:
                self.termination_reason = "completed"
                self.recover_failed_evaluations()

        except Exception as exc:
            self.termination_reason = "failed"
            try:
                self.checkpoint_manager.ledger.transition_run(
                    self.run_id, from_state=RunStatus.RUNNING, to_state=RunStatus.FAILED
                )
            except Exception:
                pass
            raise exc

        finally:
            # Restore signals
            signal.signal(signal.SIGINT, prev_sigint)
            if prev_sigterm is not None and hasattr(signal, "SIGTERM"):
                signal.signal(signal.SIGTERM, prev_sigterm)
            if getattr(self.batcher, "close_on_trainer_exit", False):
                self.batcher.close()  # type: ignore[attr-defined]

        # Final checkpoint and ledger state update
        if self.termination_reason == "completed":
            self.save_terminal_checkpoint("final")
            try:
                self.checkpoint_manager.ledger.transition_run(
                    self.run_id, from_state=RunStatus.RUNNING, to_state=RunStatus.SUCCEEDED
                )
            except Exception:
                pass
        elif self.termination_reason == "interrupted":
            self.save_terminal_checkpoint("interrupted")
            try:
                self.checkpoint_manager.ledger.transition_run(
                    self.run_id, from_state=RunStatus.RUNNING, to_state=RunStatus.INTERRUPTED
                )
            except Exception:
                pass

        return TrainingSummary(
            run_id=self.run_id,
            plan_id=self.plan_id,
            total_steps=self.step,
            committed_valid_targets=self.committed_valid_targets,
            processed_valid_targets=self.processed_valid_targets,
            total_training_time_seconds=self.total_training_time_seconds,
            setup_time_seconds=self.setup_time_seconds,
            termination_reason=self.termination_reason,
            final_loss=final_loss,
            metrics_history=metrics_history,
        )
