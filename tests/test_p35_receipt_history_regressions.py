"""Independent regressions for planned receipt histories and fail-stop guarding."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from test_p35_hardening_runtime import guarded_trainer, receipt_trainer, reseal
from test_p35_m4_evidence import publish_run
from xlm.artifacts.manifest import identity_digest
from xlm.comparison.science_evidence import (
    EvidenceError,
    _update_boundaries,
    extract_run_evidence,
)
from xlm.data.sampling.update_payload import UpdatePayloadChain, receipt_history_digest
from xlm.training.evaluation import training_state_fingerprint
from xlm.training.trainer import RecoveryRequiredError


@pytest.mark.parametrize("targets", [1_000_000_000, 3_000_000_000, 6_000_000_000])
def test_planned_metadata_histories_fit_guard_and_evidence(tmp_path: Path, targets: int) -> None:
    trainer = receipt_trainer(tmp_path)
    chain = trainer.science.update_payloads
    before = 0
    while before < targets:
        step = len(chain.rows) + 1
        valid = min(65536, targets - before)
        chain.stage(step=step, committed_before=before, valid_targets=valid, payload="a" * 64)
        chain.commit()
        trainer.science.lr_receipts.append([step, before, valid, before + valid, [1e-3, 1e-3]])
        before += valid
    # Synthetic metadata only: no token exposure or model-training claim.
    fingerprint = training_state_fingerprint(trainer)
    assert "update_payload_receipt" in fingerprint
    digest, count = _update_boundaries(trainer.science.lr_receipts, targets, receipted=True)
    assert len(digest) == 64 and count == (targets + 65535) // 65536
    chain.rows[-1][3] = "b" * 64
    assert (
        training_state_fingerprint(trainer)["update_payload_receipt"]
        != fingerprint["update_payload_receipt"]
    )


def test_small_history_digests_retain_existing_canonical_bytes() -> None:
    chain = UpdatePayloadChain()
    chain.stage(step=1, committed_before=0, valid_targets=5, payload="a" * 64)
    chain.commit()
    for value in (chain.guard_state(), {"unicode": "\u00e9", "rows": [[1, 0, 5, 5, [1e-3]]]}):
        assert receipt_history_digest(value) == identity_digest(value)
    rows = [[1, 0, 5, 5, [1e-3]]]
    assert _update_boundaries(rows, 5, receipted=True) == _update_boundaries(rows, 5)


@pytest.mark.parametrize("part", ["staged", "rows", "entire"])
def test_unreadable_receipt_during_evaluation_requires_recovery(tmp_path: Path, part: str) -> None:
    def mutate(trainer: Any, model: Any) -> None:
        if part == "staged":
            trainer.science.update_payloads._staged = [2, 16, 16, object()]
        elif part == "rows":
            trainer.science.update_payloads.rows[0][3] = object()
        else:
            trainer.science.update_payloads = object()

    trainer = guarded_trainer(tmp_path, mutate)
    with pytest.raises(RecoveryRequiredError):
        trainer.train_step()
    assert trainer._evaluation_compromised
    for operation in (trainer.train_step, lambda: trainer.save_terminal_checkpoint("final")):
        with pytest.raises(RecoveryRequiredError):
            operation()


def test_evidence_refuses_lr_schedule_counter_even_with_valid_payload_chain(tmp_path: Path) -> None:
    chain = UpdatePayloadChain()
    for step in range(1, 5):
        chain.stage(step=step, committed_before=(step - 1) * 16, valid_targets=16, payload="a" * 64)
        chain.commit()
    checkpoint = publish_run(
        tmp_path, "independent", (101, 10001, 20260918), update_payloads=chain.to_dict()
    )

    def damage(science: dict[str, Any]) -> dict[str, Any]:
        science["lr_receipts"]["rows"][1][3] += 1
        return science

    reseal(checkpoint, {"science.json": damage})
    with pytest.raises(EvidenceError, match="inconsistent receipt history"):
        extract_run_evidence(checkpoint, parameter_counter=lambda _: 6768)
