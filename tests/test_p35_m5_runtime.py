"""P35 M5 runtime integration: Trainer checkpoint/resume and planning with a frozen order.

These tests need torch (and the pilot planning test the CUDA-extra environment,
like the M3 pilot tests). They build tiny authored models on authored shards;
nothing is a research run. In an environment without torch they are skipped,
which is NOT RUN, never a pass (P35-M5 report §local certification).
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

from p35_m5_support import CONTEXT, GLOBAL, batcher, orders, write_sources
from xlm.data.ordering import build_membership, build_order_manifest, write_order_manifest
from xlm.tokenizers.byte import ByteTokenizer

torch = pytest.importorskip("torch")

from xlm.artifacts.ledger import RunLedger  # noqa: E402
from xlm.artifacts.store import ArtifactStore  # noqa: E402
from xlm.config.schemas import (  # noqa: E402
    AdamWConfig,
    CrossEntropyObjectiveConfig,
    TransformerBaselineConfig,
    WarmupCosineScheduleConfig,
)
from xlm.core.paths import ArtifactPaths  # noqa: E402
from xlm.models.transformer import TransformerBaseline  # noqa: E402
from xlm.objectives.cross_entropy import CrossEntropyObjective  # noqa: E402
from xlm.optimizers.adamw import create_adamw_optimizer  # noqa: E402
from xlm.schedules.cosine import WarmupCosineSchedule  # noqa: E402
from xlm.training.checkpoint import CheckpointManager, IncompatibleCheckpointError  # noqa: E402
from xlm.training.trainer import Trainer  # noqa: E402

HORIZON = 4 * GLOBAL
PLAN = "m5_plan"


def _parts(root: Path, readers: dict[str, Any], order: Any, *, seed: int = 1234) -> dict[str, Any]:
    tokenizer = ByteTokenizer()
    paths = ArtifactPaths(root=root)
    store = ArtifactStore(paths)
    manager = CheckpointManager(
        artifact_store=store, run_ledger=RunLedger(paths.ledger / "ledger.sqlite"), paths=paths
    )
    torch.manual_seed(seed)
    model = TransformerBaseline(
        TransformerBaselineConfig(
            architecture="transformer_baseline",
            vocab_size=tokenizer.vocab_size,
            num_layers=1,
            hidden_size=16,
            num_attention_heads=2,
            intermediate_size=32,
            context_length=CONTEXT,
            attention_backend="eager",
            tie_embeddings=True,
        ),
        seed=seed,
    )
    objective = CrossEntropyObjective(CrossEntropyObjectiveConfig())
    optimizer, manifest = create_adamw_optimizer(AdamWConfig(lr=1e-3), model=model)
    schedule = WarmupCosineSchedule(
        WarmupCosineScheduleConfig(
            warmup_valid_targets=GLOBAL, horizon_valid_targets=HORIZON, min_lr_ratio=0.1
        ),
        base_lr=1e-3,
    )
    return {
        "manager": manager,
        "model": model,
        "objective": objective,
        "optimizer": optimizer,
        "manifest": manifest,
        "schedule": schedule,
        "batcher": batcher(readers, copy.deepcopy(order), emit_tensors=True),
    }


def _trainer(parts: dict[str, Any], run_id: str, budget: int, **kwargs: Any) -> Trainer:
    return Trainer(
        model=parts["model"],
        objective=parts["objective"],
        optimizer=parts["optimizer"],
        optimizer_manifest=parts["manifest"],
        schedule=parts["schedule"],
        batcher=parts["batcher"],
        checkpoint_manager=parts["manager"],
        run_id=run_id,
        plan_id=PLAN,
        device="cpu",
        precision="fp32",
        max_valid_targets=budget,
        **kwargs,
    )


def _save_half(tmp_path: Path, readers: dict[str, Any], order: Any) -> Path:
    parts = _parts(tmp_path / "store", readers, order)
    trainer = _trainer(parts, "m5_half", 2 * GLOBAL)
    trainer.train()
    return Path(
        parts["manager"].save_checkpoint(
            checkpoint_id="m5_half_ckpt",
            run_id="m5_half",
            step=trainer.step,
            committed_valid_targets=trainer.committed_valid_targets,
            processed_valid_targets=trainer.processed_valid_targets,
            plan_id=PLAN,
            model=parts["model"],
            objective=parts["objective"],
            optimizer=parts["optimizer"],
            optimizer_manifest=parts["manifest"],
            schedule=parts["schedule"],
            batcher=parts["batcher"],
        )
    )


def _load(parts: dict[str, Any], checkpoint: Path, *, is_fork: bool = False) -> Any:
    return parts["manager"].load_checkpoint(
        checkpoint,
        model=parts["model"],
        objective=parts["objective"],
        optimizer=parts["optimizer"],
        optimizer_manifest=parts["manifest"],
        schedule=parts["schedule"],
        batcher=parts["batcher"],
        expected_plan_id=PLAN,
        is_fork=is_fork,
    )


def test_same_order_trainer_resume_is_exact(tmp_path: Path) -> None:
    readers = write_sources(tmp_path / "shards")
    order_a, _ = orders(readers)
    full = _parts(tmp_path / "full", readers, order_a)
    _trainer(full, "m5_full", HORIZON).train()
    checkpoint = _save_half(tmp_path, readers, order_a)
    resumed = _parts(tmp_path / "store", readers, order_a, seed=999)
    meta = _load(resumed, checkpoint)
    trainer = _trainer(
        resumed,
        "m5_resumed",
        HORIZON,
        step=meta.step,
        committed_valid_targets=meta.committed_valid_targets,
        processed_valid_targets=meta.processed_valid_targets,
    )
    trainer.train()
    for name, value in full["model"].state_dict().items():
        assert torch.equal(value, resumed["model"].state_dict()[name]), name
    assert resumed["batcher"].get_state() == full["batcher"].get_state()
    assert (
        resumed["batcher"].get_state()["document_order"]["order_manifest_id"]
        == (order_a["order_manifest_id"])
    )


@pytest.mark.parametrize("target", ["order_b", "shard_native", "order_b_fork"])
def test_cross_order_resume_is_refused_before_any_state_restore(
    tmp_path: Path, target: str
) -> None:
    readers = write_sources(tmp_path / "shards")
    order_a, order_b = orders(readers)
    checkpoint = _save_half(tmp_path, readers, order_a)
    other = None if target == "shard_native" else order_b
    parts = _parts(tmp_path / "store", readers, other, seed=999)
    before = {k: v.clone() for k, v in parts["model"].state_dict().items()}
    with pytest.raises(IncompatibleCheckpointError, match="document order|new experiment"):
        _load(parts, checkpoint, is_fork=target.endswith("fork"))
    after = parts["model"].state_dict()
    assert all(torch.equal(before[k], after[k]) for k in before)  # weights untouched
    assert parts["optimizer"].state_dict()["state"] == {}  # optimizer untouched
    assert parts["batcher"].get_state()["committed_valid_targets"] == 0


def test_new_experiment_under_order_b_has_its_own_identity(tmp_path: Path) -> None:
    readers = write_sources(tmp_path / "shards")
    order_a, order_b = orders(readers)
    fresh = _parts(tmp_path / "fresh_b", readers, order_b)
    _trainer(fresh, "m5_b", 2 * GLOBAL).train()
    state_b = fresh["batcher"].get_state()
    assert state_b["document_order"]["order_manifest_id"] == order_b["order_manifest_id"]
    assert (
        state_b["document_order"]["canonical_membership_id"] == (order_a["canonical_membership_id"])
    )


def test_process_producer_trains_on_the_same_frozen_order(tmp_path: Path) -> None:
    from xlm.training.inputs import MixtureInput, build_training_batcher

    readers = write_sources(tmp_path / "shards")
    order_a, _ = orders(readers)
    sync = batcher(readers, order_a, emit_tensors=True)
    source = MixtureInput(sync.recipe, dict(readers), copy.deepcopy(order_a))
    tokenizer = ByteTokenizer()
    training = {
        "producer_prefetch": "process_depth1",
        "context_length": CONTEXT,
        "global_batch_valid_targets": GLOBAL,
        "microbatch_sequences": 2,
    }
    producer = build_training_batcher(source, {}, training, tokenizer)
    try:
        for _ in range(5):
            expected = sync.next_step_microbatches()
            actual = producer.next_step_microbatches()
            for a, b in zip(expected, actual, strict=True):
                assert torch.equal(a.input_ids, b.input_ids)
                assert torch.equal(a.labels, b.labels)
            sync.commit()
            producer.commit()
            assert producer.get_state() == sync.get_state()
    finally:
        producer.close()


needs_cuda_env = pytest.mark.skipif(
    not torch.cuda.is_available(), reason="pilot planning follows the M3 CUDA-environment tests"
)


@needs_cuda_env
def test_pilot_plan_binds_and_verifies_the_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json

    from p35_m3_support import (
        ROOT,
        authored_bindings,
        authored_pilot_draft,
        build_inputs,
        fixture_size_checkpoint,
    )
    from xlm.data.tokens import TokenShardReader
    from xlm.experiments.science_pilot import plan_science_pilot

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("XLM_HOME", str(home))
    budget = 4096
    inputs = build_inputs(tmp_path / "inputs", budget=budget)
    size = fixture_size_checkpoint(home, inputs)
    draft = authored_pilot_draft(
        inputs, budget=budget, milestones=[0, 2048, budget], recovery=[1024, 3072],
        quick=[0, 2048], full=[budget],
    )  # fmt: skip
    draft["science_pilot"]["document_order"] = {
        "policy": "m5-independent-document-order-v1",
        "role": "fixed_single_order_not_an_independent_replicate",
        "manifest": None,
        "order_manifest_id": None,
        "canonical_membership_id": None,
    }
    readers = {s: TokenShardReader(Path(p)) for s, p in inputs["sources"].items()}
    membership = build_membership(readers)
    out = tmp_path / "out"
    out.mkdir()
    reviews = {}
    for label, seed in (("a", 35_051), ("b", 35_052)):
        order = build_order_manifest(membership, order_seed=seed)
        path = write_order_manifest(order, inputs["data_root"] / f"order_{label}.json")
        bindings = authored_bindings(inputs, home=home, output_root=out, size_source=str(size))
        bindings["document_order"] = {
            "manifest": str(path),
            "order_manifest_id": order["order_manifest_id"],
            "canonical_membership_id": order["canonical_membership_id"],
        }
        draft_path = tmp_path / f"draft_{label}.yaml"
        draft_path.write_text(json.dumps(draft), encoding="utf-8")
        bindings_path = tmp_path / f"bindings_{label}.json"
        bindings_path.write_text(json.dumps(bindings), encoding="utf-8")
        result = plan_science_pilot(
            draft_path,
            workspace_root=ROOT,
            bindings_path=bindings_path,
            snapshot_dir=out / f"snapshot_{label}",
            output_path=out / f"plan_{label}.json",
            review_path=out / f"review_{label}.json",
            artifact_home=home,
        )
        assert result.review["preflight"]["document_order"]["status"] == "VERIFIED"
        reviews[label] = result.review
    data_a = reviews["a"]["preflight"]["resolution"]["bindings"]["data"]
    data_b = reviews["b"]["preflight"]["resolution"]["bindings"]["data"]
    assert data_a != data_b  # the execution identity binds the order
    # An order over another membership blocks planning before anything is frozen.
    bindings = authored_bindings(inputs, home=home, output_root=out, size_source=str(size))
    foreign = build_order_manifest(
        build_membership(write_sources(tmp_path / "foreign")), order_seed=1
    )
    path = write_order_manifest(foreign, inputs["data_root"] / "order_foreign.json")
    bindings["document_order"] = {
        "manifest": str(path),
        "order_manifest_id": foreign["order_manifest_id"],
        "canonical_membership_id": foreign["canonical_membership_id"],
    }
    bindings_path = tmp_path / "bindings_foreign.json"
    bindings_path.write_text(json.dumps(bindings), encoding="utf-8")
    blocked = plan_science_pilot(
        tmp_path / "draft_a.yaml",
        workspace_root=ROOT,
        bindings_path=bindings_path,
        artifact_home=home,
    )
    assert blocked.plan is None
    codes = {b["code"] for b in blocked.review["blockers"]}
    assert codes & {"order_membership_mismatch", "order_shard_mismatch"}
