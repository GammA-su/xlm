"""Runtime side of ``xlm-science-v1``: training RNG, scoped runtime and receipts.

Legacy runs never reach these paths: their :class:`ScientificState` carries the
legacy policy, the runtime scope is a no-op, and nothing is written to
checkpoints. Science-v1 state is owned by one trainer/checkpoint pair and saved
as ``science.json`` beside the existing checkpoint files.
"""

from __future__ import annotations

import contextlib
import hashlib
import math
import os
import random
from collections.abc import Iterator, Mapping
from typing import TYPE_CHECKING, Any

from xlm.artifacts.manifest import canonical_json
from xlm.config.science import (
    ENDPOINT_LR_POLICY,
    STATISTICAL_ATTENTION,
    STRICT_ATTENTION,
    TRAINING_RNG_POLICY,
    ScientificPolicy,
    ScientificPolicyError,
)

if TYPE_CHECKING:
    from xlm.data.sampling.update_payload import UpdatePayloadChain
    from xlm.evaluation.receipts import EvaluationLedger
    from xlm.training.milestones import CheckpointLedger

SCIENCE_STATE_VERSION = 1
LR_RECEIPT_COLUMNS = (
    "step",
    "committed_before",
    "valid_targets",
    "schedule_counter",
    "lr_used",
)
MAX_LR_RECEIPTS = 1_000_000
MAX_RUNTIME_RECEIPTS = 1024
DETERMINISTIC_CUBLAS_WORKSPACES = (":4096:8", ":16:8")
EFFICIENT_ATTENTION_OPS = (
    "aten::_efficient_attention_forward",
    "aten::_efficient_attention_backward",
)
MATH_ATTENTION_OPS = ("aten::_scaled_dot_product_attention_math",)


class ScientificRuntimeError(RuntimeError):
    """A requested science-v1 runtime policy is unavailable or was not observed."""


def rng_state_digest(device: str) -> str:
    """SHA-256 over Python, NumPy, torch CPU and (for CUDA runs) all CUDA RNG states."""
    import numpy as np
    import torch

    digest = hashlib.sha256(b"xlm-training-rng-v1\0")
    version, internal, gauss = random.getstate()
    digest.update(canonical_json({"v": version, "s": list(internal), "g": gauss}))
    name, keys, position, has_gauss, cached = np.random.get_state()
    digest.update(canonical_json([name, int(position), int(has_gauss), float(cached)]))
    digest.update(np.asarray(keys, dtype=np.uint32).tobytes())
    digest.update(torch.get_rng_state().numpy().tobytes())
    if torch.device(device).type == "cuda":
        for state in torch.cuda.get_rng_state_all():
            digest.update(state.numpy().tobytes())
    return digest.hexdigest()


def reseed_training_rng(seed: int, device: str) -> dict[str, Any]:
    """Seed every training RNG after construction; return the train-start receipt."""
    import numpy as np
    import torch

    random.seed(seed)
    np.random.seed(seed % 2**32)
    torch.manual_seed(seed)  # also queues every CUDA generator
    if torch.device(device).type == "cuda":
        torch.cuda.manual_seed_all(seed)
    return {
        "policy": TRAINING_RNG_POLICY,
        "training_seed": seed,
        "generators": ["python", "numpy", "torch_cpu"]
        + (["torch_cuda_all"] if torch.device(device).type == "cuda" else []),
        "state_digest": rng_state_digest(device),
    }


class ScientificState:
    """Per-run science provenance shared by the trainer and its checkpoint manager.

    It only accumulates *committed* facts: LR receipts are appended after the
    data commit of a successful update, so a failed or in-doubt update never
    publishes one.
    """

    def __init__(self, policy: ScientificPolicy) -> None:
        self.policy = policy
        self.train_start_rng: dict[str, Any] | None = None
        self.lr_receipts: list[list[Any]] = []
        self.runtime_receipts: list[dict[str, Any]] = []
        # Science-v1 evaluation events (P35 M2); ``None`` when no cadence is declared,
        # which keeps M1 ``science.json`` files byte-identical.
        self.evaluation: EvaluationLedger | None = None
        # Science-v1 checkpoint events (P35 M3); ``None`` when no checkpoint cadence
        # is declared, which keeps M1/M2 ``science.json`` files byte-identical.
        self.checkpoints: CheckpointLedger | None = None
        # Pilot readiness: committed global-update payload receipt chain; ``None``
        # unless ``training.update_payload_receipt`` is declared (M1-M5 bytes unchanged).
        self.update_payloads: UpdatePayloadChain | None = None

    def record_lr(self, receipt: list[Any]) -> None:
        if len(self.lr_receipts) >= MAX_LR_RECEIPTS:
            raise ScientificRuntimeError("LR receipt log exceeds its bound")
        self.lr_receipts.append(receipt)

    def commit_receipted_update(self, receipt: list[Any]) -> None:
        """Commit one update's LR receipt and payload row as one science operation.

        Used only when the update payload receipt is declared. Everything that
        can refuse (bounds, the staged receipt matching this update, lock-step
        with the LR history) is checked before either history is appended. A
        failure in here leaves the trainer's boundary in doubt: the caller never
        treats the update as authoritative (see ``Trainer._train_update``).
        """
        chain = self.update_payloads
        if chain is None:
            raise ScientificRuntimeError("no update payload receipt is declared")
        step, before, valid = (int(v) for v in receipt[:3])
        if len(self.lr_receipts) >= MAX_LR_RECEIPTS:
            raise ScientificRuntimeError("LR receipt log exceeds its bound")
        if len(self.lr_receipts) != len(chain.rows) or (
            chain.rows and [int(v) for v in chain.rows[-1][:3]] != self.lr_receipts[-1][:3]
        ):
            raise ScientificRuntimeError(
                "LR receipts and the update payload chain are not in lock step"
            )
        chain.prepare_commit(step, before, valid)  # refuses before anything is appended
        self.lr_receipts.append(receipt)
        chain.commit()

    def check_receipt_alignment(self, *, step: int, committed: int, data_committed: Any) -> None:
        """Save-time check (declared receipt only, O(1)): histories, counters and data agree.

        The full row-by-row check runs when a checkpoint is loaded
        (:func:`check_receipt_history`); commits keep the histories in lock
        step, so the tails decide agreement here.
        """
        chain = self.update_payloads
        if chain is None:
            return
        lr = self.lr_receipts
        if not len(lr) == len(chain.rows) == step:
            raise ScientificRuntimeError(
                f"receipt history has {len(lr)} LR and {len(chain.rows)} payload rows for "
                f"step {step}"
            )
        endpoint = 0
        if lr:
            tail = [int(v) for v in lr[-1][:3]]
            if tail != [int(v) for v in chain.rows[-1][:3]]:
                raise ScientificRuntimeError("LR and payload receipt tails disagree")
            endpoint = tail[1] + tail[2]
        if not endpoint == committed == _data_committed(data_committed):
            raise ScientificRuntimeError(
                f"receipt history ends at C={endpoint}, the trainer committed {committed} and "
                f"the data state {data_committed}"
            )

    def record_runtime(self, receipt: dict[str, Any]) -> None:
        if len(self.runtime_receipts) >= MAX_RUNTIME_RECEIPTS:
            raise ScientificRuntimeError("runtime receipt log exceeds its bound")
        self.runtime_receipts.append(receipt)

    def to_checkpoint(self) -> dict[str, Any]:
        payload = {
            "version": SCIENCE_STATE_VERSION,
            "policy": self.policy.identity(),
            "train_start_rng": self.train_start_rng,
            "lr_receipts": {"columns": list(LR_RECEIPT_COLUMNS), "rows": self.lr_receipts},
            "runtime_receipts": self.runtime_receipts,
        }
        if self.evaluation is not None:
            payload["evaluation"] = self.evaluation.to_dict()
        if self.checkpoints is not None:
            payload["checkpoints"] = self.checkpoints.to_dict()
        if self.update_payloads is not None:
            if len(self.update_payloads.rows) != len(self.lr_receipts):
                # An incoherent receipt set is never serialized as scientific history.
                raise ScientificRuntimeError(
                    "LR receipts and the update payload chain disagree in length; the "
                    "boundary is not authoritative"
                )
            payload["update_payloads"] = self.update_payloads.to_dict()
        return payload

    def _saved_update_payloads(self, saved: Mapping[str, Any]) -> UpdatePayloadChain | None:
        """Parse and verify a saved update payload chain against this run's declaration."""
        from xlm.data.sampling.update_payload import PayloadReceiptError, UpdatePayloadChain

        raw = saved.get("update_payloads")
        if (raw is None) != (self.update_payloads is None):
            raise ScientificPolicyError(
                "update payload receipt presence differs between checkpoint and run; a chain "
                "cannot start mid-run or be dropped: resume refused"
            )
        if raw is None:
            return None
        try:
            chain = UpdatePayloadChain.from_dict(raw)
        except (PayloadReceiptError, KeyError, TypeError, ValueError) as exc:
            raise ScientificPolicyError(f"unreadable update payload chain: {exc}") from exc
        receipts = saved["lr_receipts"]["rows"]
        lr_rows = [[int(r[0]), int(r[1]), int(r[2])] for r in receipts]
        if [[int(r[0]), int(r[1]), int(r[2])] for r in chain.rows] != lr_rows:
            raise ScientificPolicyError(
                "update payload chain does not match the committed LR receipts update by update"
            )
        return chain

    def _saved_checkpoints(self, saved: Mapping[str, Any]) -> CheckpointLedger | None:
        """Parse and check a saved checkpoint-event ledger against this run's plan."""
        from xlm.evaluation.cadence import CadenceError
        from xlm.training.milestones import CheckpointLedger, CheckpointLedgerError

        raw = saved.get("checkpoints")
        if (raw is None) != (self.checkpoints is None):
            raise ScientificPolicyError(
                "checkpoint cadence presence differs between checkpoint and run; "
                "ordinary resume refused"
            )
        if raw is None or self.checkpoints is None:
            return None
        try:
            ledger = CheckpointLedger.from_dict(raw)
        except (CheckpointLedgerError, CadenceError, KeyError, TypeError, ValueError) as exc:
            raise ScientificPolicyError(f"unreadable checkpoint ledger: {exc}") from exc
        if ledger.plan.digest() != self.checkpoints.plan.digest():
            raise ScientificPolicyError("checkpoint plan changed; ordinary resume refused")
        for reference, reason in self.checkpoints.protected.items():
            ledger.protected.setdefault(reference, reason)
        return ledger

    def _saved_evaluation(self, saved: Mapping[str, Any]) -> EvaluationLedger | None:
        """Parse and check a saved evaluation ledger against this run's plan."""
        from xlm.evaluation.receipts import EvaluationLedger, EvaluationLedgerError

        raw = saved.get("evaluation")
        if (raw is None) != (self.evaluation is None):
            raise ScientificPolicyError(
                "evaluation cadence presence differs between checkpoint and run; "
                "ordinary resume refused"
            )
        if raw is None or self.evaluation is None:
            return None
        try:
            ledger = EvaluationLedger.from_dict(raw)
        except (EvaluationLedgerError, KeyError, TypeError, ValueError) as exc:
            raise ScientificPolicyError(f"unreadable evaluation ledger: {exc}") from exc
        if ledger.plan.digest() != self.evaluation.plan.digest():
            raise ScientificPolicyError("evaluation plan changed; ordinary resume refused")
        if ledger.evaluator_digests != self.evaluation.evaluator_digests:
            raise ScientificPolicyError(
                "evaluator or evaluation inputs changed since the run was planned; "
                "ordinary resume refused"
            )
        return ledger

    def restore(self, saved: Mapping[str, Any], *, fork: bool = False) -> None:
        """Adopt a checkpoint's committed history (same policy only; never reseeds).

        An explicit fork does not inherit the parent's evaluation events: the
        fork's plan is re-originated at its starting committed count instead.
        """
        if saved.get("version") != SCIENCE_STATE_VERSION:
            raise ScientificPolicyError("unsupported science checkpoint state version")
        if ScientificPolicy.from_identity(saved["policy"]) != self.policy:
            raise ScientificPolicyError("science checkpoint policy differs from this run")
        receipts = saved["lr_receipts"]
        if receipts.get("columns") != list(LR_RECEIPT_COLUMNS):
            raise ScientificPolicyError("unknown LR receipt layout")
        if self.lr_receipts:
            raise ScientificPolicyError("cannot restore into a run that already committed updates")
        ledger = None if fork else self._saved_evaluation(saved)
        checkpoint_ledger = None if fork else self._saved_checkpoints(saved)
        # The payload chain is data lineage: ordinary resume and forks both adopt it.
        payload_chain = self._saved_update_payloads(saved)
        self.train_start_rng = saved.get("train_start_rng")
        self.lr_receipts = [list(row) for row in receipts["rows"]]
        # Earlier attempts' observations first, then any made by this attempt
        # (the queue builds its trainer before restoring a retry checkpoint).
        self.runtime_receipts = [
            dict(r) for r in saved.get("runtime_receipts", [])
        ] + self.runtime_receipts
        if ledger is not None:
            self.evaluation = ledger
        if checkpoint_ledger is not None:
            self.checkpoints = checkpoint_ledger
        if payload_chain is not None:
            self.update_payloads = payload_chain

    def rebase_checkpoints(self, origin_committed_targets: int) -> None:
        """Start a forked run's fresh checkpoint plan at the fork's committed count."""
        if self.checkpoints is None:
            return
        from xlm.evaluation.cadence import CadenceError
        from xlm.training.milestones import CheckpointLedgerError, rebase_checkpoint_ledger

        try:
            self.checkpoints = rebase_checkpoint_ledger(self.checkpoints, origin_committed_targets)
        except (CadenceError, CheckpointLedgerError) as exc:
            raise ScientificPolicyError(f"fork checkpoint plan: {exc}") from exc

    def rebase_evaluation(self, origin_committed_targets: int) -> None:
        """Start a forked run's fresh evaluation plan at the fork's committed count."""
        if self.evaluation is None:
            return
        from xlm.evaluation.cadence import CadenceError, rebase_plan
        from xlm.evaluation.receipts import EvaluationLedger

        if not self.evaluation.untouched:
            raise ScientificPolicyError("a fork starts a fresh evaluation plan")
        try:
            plan = rebase_plan(self.evaluation.plan, origin_committed_targets)
        except CadenceError as exc:
            raise ScientificPolicyError(f"fork evaluation plan: {exc}") from exc
        self.evaluation = EvaluationLedger(plan, self.evaluation.evaluator_digests)


def _data_committed(value: Any) -> int:
    if type(value) is not int or value < 0:
        raise ScientificPolicyError(
            "the committed data state records no committed_valid_targets; a required update "
            "payload receipt cannot be bound to it"
        )
    return value


def check_receipt_history(
    saved: Mapping[str, Any], *, step: int, committed: int, data_committed: Any
) -> None:
    """Load-time check of a checkpoint carrying the update payload receipt (Astra A7).

    Runs before any model, optimizer, scaler, schedule or data state is restored.
    The payload chain must verify from its genesis; the LR receipts must be one
    contiguous committed-update history from ``(step 1, C 0)`` whose schedule
    counter is each update's endpoint ``C + N``; both must agree update by
    update; and the history's update count and final endpoint ``C`` must equal
    the checkpoint step, the checkpoint's committed count and the committed data
    state's count. History ahead of the data (claimed work the data never
    committed) and data ahead of the history (work without required receipts)
    are both refused; neither history is ever truncated or extended to fit.
    """
    from xlm.data.sampling.update_payload import PayloadReceiptError, verify_chain

    raw = saved.get("update_payloads")
    try:
        verify_chain(raw)  # type: ignore[arg-type]
    except (PayloadReceiptError, AttributeError, KeyError, TypeError, ValueError) as exc:
        raise ScientificPolicyError(f"unreadable update payload chain: {exc}") from exc
    receipts = saved.get("lr_receipts")
    if not isinstance(receipts, Mapping) or receipts.get("columns") != list(LR_RECEIPT_COLUMNS):
        raise ScientificPolicyError("unknown LR receipt layout")
    rows = receipts.get("rows")
    if not isinstance(rows, list) or len(rows) > MAX_LR_RECEIPTS:
        raise ScientificPolicyError("LR receipt rows are missing or unbounded")
    endpoint = 0
    for number, row in enumerate(rows, start=1):
        if (
            not isinstance(row, list)
            or len(row) != len(LR_RECEIPT_COLUMNS)
            or any(type(value) is not int for value in row[:4])
        ):
            raise ScientificPolicyError(f"malformed LR receipt at update {number}")
        if row[0] != number or row[1] != endpoint or row[2] <= 0 or row[3] != row[1] + row[2]:
            raise ScientificPolicyError(
                f"LR receipts are not one contiguous committed-update history at update {number}"
            )
        endpoint += row[2]
    if [list(r[:3]) for r in raw["rows"]] != [list(r[:3]) for r in rows]:  # type: ignore[index]
        raise ScientificPolicyError(
            "update payload chain does not match the committed LR receipts update by update"
        )
    data = _data_committed(data_committed)
    if endpoint > data:
        raise ScientificPolicyError(
            f"LR/payload receipt history claims C={endpoint}, ahead of the committed data "
            f"state C={data}: refused, never truncated"
        )
    if data > endpoint:
        raise ScientificPolicyError(
            f"committed data state C={data} exceeds the LR/payload receipt history "
            f"C={endpoint}: committed work without its required receipts is refused"
        )
    if committed != endpoint:
        raise ScientificPolicyError(
            f"checkpoint metadata records C={committed}, the receipt history and data state "
            f"C={endpoint}"
        )
    if step != len(rows):
        raise ScientificPolicyError(
            f"checkpoint step {step} differs from the {len(rows)} receipted committed updates"
        )


def validate_group_lr_semantics(
    optimizer: Any, manifest_groups: list[dict[str, Any]], base_lr: float | None
) -> list[float]:
    """Return per-group multipliers the endpoint policy can apply unambiguously.

    A group's intended rate is ``schedule_lr * lr_multiplier``. A group without
    an explicit multiplier must have been constructed at the schedule base LR;
    a distinct constructed LR has no representable endpoint meaning, so it is
    refused rather than collapsed onto the global schedule.
    """
    if base_lr is None or not math.isfinite(base_lr) or base_lr <= 0:
        raise ScientificPolicyError(f"{ENDPOINT_LR_POLICY} requires a schedule base_lr")
    if len(manifest_groups) != len(optimizer.param_groups):
        raise ScientificPolicyError("optimizer groups differ from their manifest")
    multipliers = []
    for index, (declared, group) in enumerate(
        zip(manifest_groups, optimizer.param_groups, strict=True)
    ):
        if "lr_multiplier" in group:
            multiplier = float(group["lr_multiplier"])
            if not math.isfinite(multiplier) or multiplier <= 0:
                raise ScientificPolicyError(f"group {index} lr_multiplier must be positive")
        else:
            constructed = declared.get("lr")
            if constructed is None or not math.isclose(
                float(constructed), base_lr, rel_tol=1e-12, abs_tol=0.0
            ):
                raise ScientificPolicyError(
                    f"group {index} was built with lr {constructed} != schedule base "
                    f"{base_lr} and declares no lr_multiplier; its endpoint rate is ambiguous"
                )
            multiplier = 1.0
        multipliers.append(multiplier)
    return multipliers


class ScientificRuntime:
    """Scoped science-v1 runtime flags; a no-op for legacy runs.

    Entering the scope sets deterministic enforcement, TF32 and BF16 reduction
    flags and restricts SDPA to the declared kernel; leaving restores every
    prior process value, even on failure.
    """

    def __init__(self, runtime: Mapping[str, str] | None, device: str) -> None:
        self.runtime = dict(runtime) if runtime is not None else None
        self.device = device

    @property
    def active(self) -> bool:
        return self.runtime is not None

    @property
    def strict(self) -> bool:
        return self.runtime is not None and self.runtime["attention_policy"] == STRICT_ATTENTION

    def sdpa_backends(self) -> list[Any]:
        from torch.nn.attention import SDPBackend

        if self.device == "cuda":
            return [SDPBackend.EFFICIENT_ATTENTION]
        if self.runtime is not None and self.runtime["attention_policy"] == STATISTICAL_ATTENTION:
            raise ScientificRuntimeError("statistical_efficient_v1 requires CUDA")
        return [SDPBackend.MATH]

    def check_environment(self) -> None:
        if self.strict and self.device == "cuda":
            workspace = os.environ.get("CUBLAS_WORKSPACE_CONFIG")
            if workspace not in DETERMINISTIC_CUBLAS_WORKSPACES:
                raise ScientificRuntimeError(
                    "strict_deterministic_v1 on CUDA requires CUBLAS_WORKSPACE_CONFIG in "
                    f"{DETERMINISTIC_CUBLAS_WORKSPACES} at process start, got {workspace!r}"
                )

    @contextlib.contextmanager
    def scope(self) -> Iterator[None]:
        if self.runtime is None:
            yield
            return
        import torch
        from torch.nn.attention import sdpa_kernel

        matmul = torch.backends.cuda.matmul
        cudnn = torch.backends.cudnn
        prior = (
            torch.are_deterministic_algorithms_enabled(),
            torch.is_deterministic_algorithms_warn_only_enabled(),
            cudnn.deterministic,
            cudnn.benchmark,
            matmul.allow_tf32,
            matmul.allow_bf16_reduced_precision_reduction,
        )
        requested = self.requested_flags()
        try:
            torch.use_deterministic_algorithms(requested["deterministic_algorithms"])
            cudnn.deterministic = requested["cudnn_deterministic"]
            cudnn.benchmark = False
            matmul.allow_tf32 = requested["matmul_allow_tf32"]
            matmul.allow_bf16_reduced_precision_reduction = requested[
                "bf16_reduced_precision_reduction"
            ]
            observed = self.observed_flags()
            if {k: observed[k] for k in requested} != requested:
                raise ScientificRuntimeError(
                    f"resolved runtime flags {observed} differ from requested {requested}"
                )
            with sdpa_kernel(self.sdpa_backends()):
                yield
        finally:
            torch.use_deterministic_algorithms(prior[0], warn_only=prior[1])
            cudnn.deterministic, cudnn.benchmark = prior[2], prior[3]
            matmul.allow_tf32 = prior[4]
            matmul.allow_bf16_reduced_precision_reduction = prior[5]

    def requested_flags(self) -> dict[str, bool]:
        assert self.runtime is not None
        return {
            "deterministic_algorithms": self.strict,
            "deterministic_warn_only": False,
            "cudnn_deterministic": self.strict,
            "matmul_allow_tf32": self.runtime["matmul_tf32"] == "enabled",
            "bf16_reduced_precision_reduction": (
                self.runtime["bf16_reduced_precision_reduction"] == "allowed"
            ),
        }

    @staticmethod
    def observed_flags() -> dict[str, Any]:
        import torch

        matmul = torch.backends.cuda.matmul
        return {
            "deterministic_algorithms": torch.are_deterministic_algorithms_enabled(),
            "deterministic_warn_only": torch.is_deterministic_algorithms_warn_only_enabled(),
            "cudnn_deterministic": torch.backends.cudnn.deterministic,
            "matmul_allow_tf32": matmul.allow_tf32,
            "bf16_reduced_precision_reduction": matmul.allow_bf16_reduced_precision_reduction,
            "float32_matmul_precision": torch.get_float32_matmul_precision(),
        }

    def attention_receipt(
        self, *, attention_backend: str, num_heads: int, head_dim: int, precision: str
    ) -> dict[str, Any]:
        """Probe the declared attention route under this scope and record what ran.

        The probe uses a private generator, so it consumes no training RNG. It
        observes the operator family for one small causal and one boolean-mask
        call; it is not a whole-model determinism or performance certificate.
        """
        assert self.runtime is not None
        import torch

        receipt: dict[str, Any] = {
            "attention_policy": self.runtime["attention_policy"],
            "requested_backend": attention_backend,
            "device": self.device,
            "torch": torch.__version__,
            "cublas_workspace_config": os.environ.get("CUBLAS_WORKSPACE_CONFIG"),
            "scope": "attention operator probe only; not a whole-model determinism certificate",
        }
        if self.device == "cuda":
            receipt["device_name"] = torch.cuda.get_device_name()
        if attention_backend == "eager":
            with self.scope():
                receipt["flags"] = self.observed_flags()
            receipt.update(sdpa_restriction=None, observed_ops=[], expected_ops=[])
            return receipt
        dtype = {
            "bf16_fp32_master": torch.bfloat16,
            "fp16": torch.float16,
            "fp16_scaler": torch.float16,
        }.get(precision, torch.float32)
        generator = torch.Generator(device=self.device).manual_seed(0)
        shape = (1, num_heads, 16, head_dim)
        q, k, v = (
            torch.randn(shape, generator=generator, device=self.device, dtype=dtype).requires_grad_(
                True
            )
            for _ in range(3)
        )
        mask = torch.ones(16, 16, dtype=torch.bool, device=self.device).tril()
        with self.scope():
            receipt["flags"] = self.observed_flags()
            receipt["sdpa_restriction"] = [b.name for b in self.sdpa_backends()]
            with torch.profiler.profile(activities=[torch.profiler.ProfilerActivity.CPU]) as prof:
                for kwargs in ({"is_causal": True}, {"attn_mask": mask}):
                    out = torch.nn.functional.scaled_dot_product_attention(q, k, v, **kwargs)
                    torch.autograd.backward(out.float().sum())
            if self.device == "cuda":
                torch.cuda.synchronize()
        observed = sorted(
            {
                e.key
                for e in prof.key_averages()
                if "attention" in e.key and e.key.startswith("aten")
            }
        )
        expected = list(EFFICIENT_ATTENTION_OPS if self.device == "cuda" else MATH_ATTENTION_OPS)
        receipt.update(
            observed_ops=observed,
            expected_ops=expected,
            probe={"shape": list(shape), "dtype": str(dtype).replace("torch.", "")},
        )
        missing = sorted(set(expected) - set(observed))
        if missing:
            raise ScientificRuntimeError(
                f"declared attention route not observed; missing {missing}, saw {observed}"
            )
        return receipt
