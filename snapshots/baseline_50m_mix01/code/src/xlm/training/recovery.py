"""Explicit out-of-memory recovery decisions complying with Contract C10.

An OOM during a production run restarts from a safe checkpoint. It cannot skip
data (the batcher cursor must equal the checkpoint's committed count) and it
cannot change precision, backend, microbatching or compile mode without an
explicit fork record. Anything else raises instead of quietly altering the
comparison contract.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


class OOMRecoveryError(RuntimeError):
    """Raised when an OOM recovery would skip data or silently change the contract."""


# Checkpoint-meta keys compared against the requested restart configuration.
CONTRACT_KEYS = (
    "precision",
    "attention_backend",
    "microbatch_sequences",
    "activation_checkpointing",
    "compile",
)


@dataclass(frozen=True)
class OOMRecoveryDecision:
    """A recorded, auditable decision for restarting after an OOM."""

    action: str  # 'restart_same_contract' | 'fork_new_contract'
    checkpoint_id: str
    committed_valid_targets: int
    changes: dict[str, list[Any]] = field(default_factory=dict)
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def verify_data_continuity(batcher_committed: int, checkpoint_committed: int) -> None:
    """Refuse any recovery that would skip or repeat committed data silently."""
    if batcher_committed != checkpoint_committed:
        raise OOMRecoveryError(
            f"Data cursor mismatch: batcher committed {batcher_committed} valid targets "
            f"but checkpoint committed {checkpoint_committed}. Recovery cannot skip "
            "or repeat data; restore the batcher to the checkpoint cursor first."
        )


def plan_oom_recovery(
    checkpoint_meta: dict[str, Any],
    requested: dict[str, Any],
    batcher_committed: int,
) -> OOMRecoveryDecision:
    """Decide how to restart after an OOM, recording any contract change.

    Identical execution settings restart under the same contract. Any change to
    precision, backend, microbatching, checkpointing or compile mode forces an
    explicit fork decision -- the comparison contract changes by record, never
    by accident inside a recovery path.
    """
    checkpoint_id = str(checkpoint_meta.get("checkpoint_id", "unknown"))
    checkpoint_committed = int(checkpoint_meta.get("committed_valid_targets", 0))
    verify_data_continuity(batcher_committed, checkpoint_committed)

    changes: dict[str, list[Any]] = {}
    for key in CONTRACT_KEYS:
        before = checkpoint_meta.get(key)
        after = requested.get(key, before)
        if after != before:
            changes[key] = [before, after]

    if not changes:
        return OOMRecoveryDecision(
            action="restart_same_contract",
            checkpoint_id=checkpoint_id,
            committed_valid_targets=checkpoint_committed,
            reason="Execution contract unchanged; restarting from the safe checkpoint.",
        )
    return OOMRecoveryDecision(
        action="fork_new_contract",
        checkpoint_id=checkpoint_id,
        committed_valid_targets=checkpoint_committed,
        changes=changes,
        reason=(
            "Recovery changes the execution contract; this is a fork with its own "
            "lineage, not a silent continuation."
        ),
    )
