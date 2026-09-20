"""Next-token cross-entropy objective and independent diagnostic computation.

Complying with XLM Contract C09, Acceptance Requirements A06/A07, and P04 Amendments.
"""

from __future__ import annotations

import math
from typing import Any

import torch
import torch.nn.functional as F

from xlm.config.schemas import CrossEntropyObjectiveConfig
from xlm.core.contracts import LMOutput, LossResult, TrainingBatch
from xlm.objectives.base import (
    BaseObjective,
    InvalidBatchError,
    ObjectiveCapabilities,
)


def compute_independent_diagnostic_ce(
    logits: torch.Tensor,
    labels: torch.Tensor,
    loss_mask: torch.Tensor,
) -> tuple[float, int]:
    """Compute independent next-token negative log-likelihood sum and valid token count.

    Complying with Contract C09:
    "Ordinary next-token CE is independently evaluated regardless of the objective
    being optimized."

    Handles ignored label sentinels safely before indexing and preserves float64 precision.
    """
    if logits.dim() != 3:
        raise ValueError(f"Expected 3D logits (batch, seq_len, vocab), got shape {logits.shape}")

    batch_size, seq_len, vocab_size = logits.shape
    if labels.shape != (batch_size, seq_len):
        raise ValueError(f"labels shape {labels.shape} != logits prefix {(batch_size, seq_len)}")
    if loss_mask.shape != (batch_size, seq_len):
        raise ValueError(f"loss_mask shape {loss_mask.shape} != labels shape {labels.shape}")

    mask_bool = loss_mask.bool()
    valid_count = int(mask_bool.sum().item())
    if valid_count == 0:
        return 0.0, 0

    # Handle ignored label sentinels before indexing to prevent unsafe gathered index errors
    safe_labels = torch.where(mask_bool, labels, torch.zeros_like(labels))

    # Compute unreduced log-softmax
    # Preserve float64 if input is float64; otherwise compute in float32 for numerical stability
    eval_dtype = torch.float64 if logits.dtype == torch.float64 else torch.float32
    eval_logits = logits.to(eval_dtype)

    log_probs = F.log_softmax(eval_logits, dim=-1)
    target_log_probs = log_probs.gather(dim=-1, index=safe_labels.unsqueeze(-1)).squeeze(-1)
    nll = -target_log_probs

    # Zero out masked positions and sum
    valid_nll = torch.where(mask_bool, nll, torch.zeros_like(nll))
    unscaled_nll_sum = float(valid_nll.sum().item())

    return unscaled_nll_sum, valid_count


class CrossEntropyObjective(BaseObjective):
    """Baseline next-token cross-entropy objective complying with Contract C09."""

    def __init__(self, config: CrossEntropyObjectiveConfig | None = None) -> None:
        super().__init__()
        self.config = config or CrossEntropyObjectiveConfig()
        self._capabilities = ObjectiveCapabilities(
            loss_protocol="token_additive",
            supports_microbatching=True,
            has_auxiliary_parameters=False,
            requires_hidden_states=False,
        )

    @property
    def capabilities(self) -> ObjectiveCapabilities:
        return self._capabilities

    def forward(
        self,
        model_output: LMOutput,
        batch: TrainingBatch,
        *,
        allow_empty: bool = False,
    ) -> LossResult:
        """Evaluate next-token cross-entropy on pre-shifted batch labels.

        Args:
            model_output: Model output containing logits (B, T, V).
            batch: Training batch containing already-shifted labels and loss_mask.
            allow_empty: If True, returns zero-loss result for zero-valid-target microbatch;
                         if False, raises InvalidBatchError.
        """
        logits: torch.Tensor = model_output.logits
        labels: torch.Tensor = batch.labels
        loss_mask: torch.Tensor = batch.loss_mask

        if logits.dim() != 3:
            raise InvalidBatchError(f"Expected 3D logits (batch, seq, vocab), got {logits.shape}")
        if labels.shape != logits.shape[:2]:
            raise InvalidBatchError(
                f"labels shape {labels.shape} does not match logits prefix {logits.shape[:2]}"
            )
        if loss_mask.shape != labels.shape:
            raise InvalidBatchError(
                f"loss_mask shape {loss_mask.shape} does not match labels shape {labels.shape}"
            )

        mask_bool = loss_mask.bool()
        valid_targets = int(mask_bool.sum().item())

        if valid_targets == 0:
            if not allow_empty:
                raise InvalidBatchError(
                    "An all-masked batch with zero valid targets cannot be evaluated: "
                    "explicit invalid/no-update condition."
                )
            zero_loss = torch.zeros((), dtype=logits.dtype, device=logits.device)
            return LossResult(
                loss_protocol="token_additive",
                loss=zero_loss,
                unscaled_loss_sum=0.0,
                valid_target_denominator=0,
                diagnostics={"valid_tokens": 0.0},
                auxiliary_loss=0.0,
            )

        # Handle ignored label sentinels before indexing
        safe_labels = torch.where(mask_bool, labels, torch.zeros_like(labels))

        # Check label range on valid positions
        if mask_bool.any():
            valid_labels = safe_labels[mask_bool]
            min_label = int(valid_labels.min().item())
            max_label = int(valid_labels.max().item())
            if min_label < 0 or max_label >= logits.size(-1):
                raise InvalidBatchError(
                    f"Label value out of vocabulary bounds [0, {logits.size(-1) - 1}]: "
                    f"min={min_label}, max={max_label}"
                )

        # Compute stable cross-entropy without label smoothing for baseline
        # Keep calculation in float64 if logits is float64; otherwise float32 for autograd stability
        comp_dtype = torch.float64 if logits.dtype == torch.float64 else torch.float32
        logits_comp = logits.to(comp_dtype)

        # Flat per-token cross entropy
        vocab_size = logits.size(-1)
        flat_logits = logits_comp.view(-1, vocab_size)
        flat_labels = safe_labels.view(-1)

        per_token_loss = F.cross_entropy(
            flat_logits,
            flat_labels,
            reduction="none",
            label_smoothing=self.config.label_smoothing,
        ).view_as(labels)

        valid_loss = torch.where(mask_bool, per_token_loss, torch.zeros_like(per_token_loss))
        unscaled_loss_sum_tensor = valid_loss.sum()
        unscaled_loss_sum_val = float(unscaled_loss_sum_tensor.item())

        # Standalone loss is normalized by the batch's valid target count
        # Retain original dtype for downstream autograd
        standalone_loss = (unscaled_loss_sum_tensor / float(valid_targets)).to(logits.dtype)

        ce_mean = unscaled_loss_sum_val / float(valid_targets)
        ppl = math.exp(min(ce_mean, 100.0))

        diagnostics = {
            "ce_loss": ce_mean,
            "perplexity": ppl,
            "valid_tokens": float(valid_targets),
            "unscaled_loss_sum": unscaled_loss_sum_val,
        }

        return LossResult(
            loss_protocol="token_additive",
            loss=standalone_loss,
            unscaled_loss_sum=unscaled_loss_sum_val,
            valid_target_denominator=valid_targets,
            diagnostics=diagnostics,
            auxiliary_loss=0.0,
        )


def create_cross_entropy_objective(
    config: CrossEntropyObjectiveConfig | dict[str, Any] | None = None,
) -> CrossEntropyObjective:
    """Factory creating CrossEntropyObjective from config."""
    if config is None:
        cfg = CrossEntropyObjectiveConfig()
    elif isinstance(config, dict):
        cfg = CrossEntropyObjectiveConfig(**config)
    else:
        cfg = config
    return CrossEntropyObjective(cfg)
