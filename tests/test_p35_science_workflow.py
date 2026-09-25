"""P35 M1 public workflow: one science-v1 run through direct, queue and resume.

The real CLI, captured snapshots and frozen workers execute a bounded authored
two-source mixture on CUDA with the process producer. Direct, queued and
resumed executions must resolve one scientific identity, one training-RNG
receipt and one committed LR history. Strict mode must also agree bitwise.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import torch
import yaml

from test_configurable_workflow import assert_state_equal, checkpoints, cli, documents
from xlm.config.schemas import WarmupCosineScheduleConfig
from xlm.config.science import ENDPOINT_LR_POLICY, SCIENCE_RUNTIME_POLICY, SCIENCE_V1
from xlm.data.sampling import MixtureComponent, MixtureRecipe, PackingPolicy
from xlm.schedules.cosine import WarmupCosineSchedule
from xlm.tokenizers.bpe import ByteLevelBPETokenizer


def prepare_inputs(tmp_path: Path, tree: Path, evidence: Path) -> dict[str, Any]:
    """Authored documents -> tokenizer -> shards -> exposure plan (public CLI)."""
    docs = documents()
    documents_path = tmp_path / "documents.jsonl"
    documents_path.write_text(
        "".join(json.dumps(d.to_dict()) + "\n" for d in docs), encoding="utf-8"
    )
    tokenizer = ByteLevelBPETokenizer.train_from_documents(
        docs, target_vocab_size=260, max_train_docs=4, max_train_bytes=1024
    )
    tokenizer.save(tmp_path / "tokenizer")
    recipe = MixtureRecipe(
        mixture_id="two_sources",
        components=[
            MixtureComponent(source_id="alpha", weight=0.75),
            MixtureComponent(source_id="zeta", weight=0.25),
        ],
        packing=PackingPolicy(max_document_tokens=4),
        data_seed=42,
        model_seed=7,
    )
    recipe_path = tmp_path / "mixture.yaml"
    recipe_path.write_text(yaml.safe_dump(recipe.model_dump(mode="json")), encoding="utf-8")
    shards, exposure = tmp_path / "shards", tmp_path / "exposure.json"
    prepare = tree / "prepare.yaml"
    prepare.write_text(
        yaml.safe_dump(
            {
                "id": "authored_p35",
                "output_root": str(tmp_path / "preparation"),
                "stages": [
                    {
                        "stage_id": "tokenize",
                        "kind": "run",
                        "command": [
                            "data",
                            "tokenize",
                            "--input",
                            str(documents_path),
                            "--tokenizer",
                            str(tmp_path / "tokenizer"),
                            "--output-dir",
                            str(shards),
                            "--pool-id",
                            "authored_p35",
                            "--split",
                            "train",
                        ],
                        "outputs": [str(shards)],
                        "invalidates": ["exposure"],
                    },
                    {
                        "stage_id": "exposure",
                        "kind": "run",
                        "command": [
                            "mixture",
                            "plan",
                            "--recipe",
                            str(recipe_path),
                            "--shards",
                            str(shards),
                            "--budget-targets",
                            "33",
                            "--block-size",
                            "8",
                            "--output",
                            str(exposure),
                        ],
                        "outputs": [str(exposure)],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    cli(tree, tmp_path / "prepare-home", evidence, "prepare", "--config", str(prepare),
        "--authorize", "--timeout", "60")  # fmt: skip
    return {
        "mixture": recipe.model_dump(mode="json"),
        "sources": {s: str(shards / s) for s in ("alpha", "zeta")},
        "tokenizer_artifact": str(tmp_path / "tokenizer"),
        "tokenizer": {"type": "bpe", "target_vocab_size": 260},
        "exposure_plan": str(exposure),
        "vocab_size": tokenizer.vocab_size,
    }


@pytest.mark.cuda
@pytest.mark.serial
@pytest.mark.parametrize(
    "attention_policy", ["strict_deterministic_v1", "statistical_efficient_v1"]
)
def test_science_v1_direct_queue_and_resume_share_identity(
    tmp_path: Path, attention_policy: str
) -> None:
    if not torch.cuda.is_available():
        pytest.skip("CUDA hardware required")
    from test_frozen_execution import authored_tree

    tree = authored_tree(tmp_path / "tree")
    evidence = tmp_path / "commands"
    data = prepare_inputs(tmp_path, tree, evidence)
    vocab_size = data.pop("vocab_size")
    schedule: dict[str, Any] = {
        "type": "warmup_cosine",
        "warmup_valid_targets": 20,
        "horizon_valid_targets": 64,
    }
    config = {
        "id": "authored_p35_science",
        "model": {
            "architecture": "transformer_baseline",
            "vocab_size": vocab_size,
            "hidden_size": 16,
            "intermediate_size": 32,
            "num_layers": 1,
            "num_attention_heads": 2,
            "context_length": 8,
            "attention_backend": "sdpa",
            "dropout": 0.1,
        },
        "data": data,
        "objective": {"type": "cross_entropy"},
        "optimizer": {"type": "adamw", "lr": 0.01},
        "training": {
            "device": "cuda",
            "precision": "bf16_fp32_master",
            "producer_prefetch": "process_depth1",
            "context_length": 8,
            "init_seed": 7,
            "data_seed": 42,
            "global_batch_valid_targets": 16,
            "microbatch_sequences": 2,
            "checkpoint_every_valid_targets": 16,
            "budget": {"max_valid_targets": 33, "max_train_seconds": 60},
            "schedule": schedule,
            "science_version": SCIENCE_V1,
            "lr_policy": ENDPOINT_LR_POLICY,
            "training_seed": 10001,
            "runtime": {
                "attention_policy": attention_policy,
                "matmul_tf32": "disabled",
                "bf16_reduced_precision_reduction": "allowed",
            },
        },
    }
    draft = tree / "draft.yaml"
    draft.write_text(yaml.safe_dump(config), encoding="utf-8")
    direct_home, queue_home = tmp_path / "direct-home", tmp_path / "queue-home"
    cli(tree, direct_home, evidence, "train", str(draft), "--device", "cuda")
    snapshot, plan = tmp_path / "snapshot", tmp_path / "plan.json"
    cli(tree, queue_home, evidence, "experiment", "plan", str(draft), "--smoke",
        "--snapshot-dir", str(snapshot), "--output", str(plan))  # fmt: skip
    cli(tree, queue_home, evidence, "experiment", "submit", str(plan),
        "--snapshot-dir", str(snapshot), "--device", "cuda")  # fmt: skip
    cli(tree, queue_home, evidence, "queue", "run", "--once", "--device", "cuda")
    direct, queued = checkpoints(direct_home), checkpoints(queue_home)
    assert set(direct) == set(queued) == {16, 32, 33}
    resumed_home = tmp_path / "resume-home"
    cli(tree, resumed_home, evidence, "resume", str(direct[16]), "--device", "cuda")
    resumed = checkpoints(resumed_home)
    assert set(resumed) == {32, 33}
    finals = {"direct": direct[33], "queued": queued[33], "resumed": resumed[33]}

    envelopes = {
        k: json.loads((v / "execution.json").read_text())["envelope"] for k, v in finals.items()
    }
    reference = envelopes["direct"]
    assert reference["runtime_policy"] == SCIENCE_RUNTIME_POLICY
    assert reference["config"]["training"]["runtime"]["attention_policy"] == attention_policy
    for name, envelope in envelopes.items():
        # One resolved scientific behavior; resume reuses the checkpoint's envelope.
        for key in ("config", "bindings", "runtime_policy", "code_hash", "dependency_hash"):
            assert envelope[key] == reference[key], (name, key)
    assert envelopes["resumed"]["execution_hash"] == reference["execution_hash"]

    sciences = {k: json.loads((v / "science.json").read_text()) for k, v in finals.items()}
    expected_schedule = WarmupCosineSchedule(WarmupCosineScheduleConfig(**schedule), base_lr=0.01)
    rows = sciences["direct"]["lr_receipts"]["rows"]
    assert [row[1:4] for row in rows] == [[0, 16, 16], [16, 16, 32], [32, 1, 33]]
    for row in rows:
        assert row[4] == [expected_schedule.get_lr(row[3])] * 2
    for name, state in sciences.items():
        assert state["policy"] == sciences["direct"]["policy"], name
        assert state["train_start_rng"] == sciences["direct"]["train_start_rng"], name
        assert state["lr_receipts"] == sciences["direct"]["lr_receipts"], name
    assert sciences["direct"]["train_start_rng"]["training_seed"] == 10001
    assert "torch_cuda_all" in sciences["direct"]["train_start_rng"]["generators"]
    # The resumed attempt appends its own observation after the original one.
    assert len(sciences["resumed"]["runtime_receipts"]) == 2
    for state in sciences.values():
        receipt = state["runtime_receipts"][-1]
        assert receipt["attention_policy"] == attention_policy
        assert {"aten::_efficient_attention_forward", "aten::_efficient_attention_backward"} <= set(
            receipt["observed_ops"]
        )
        strict = attention_policy == "strict_deterministic_v1"
        assert receipt["flags"]["deterministic_algorithms"] is strict
        assert receipt["cublas_workspace_config"] == (":4096:8" if strict else None)

    if attention_policy == "strict_deterministic_v1":
        for other in (queued[33], resumed[33]):
            for name in ("model.pt", "objective.pt", "optimizer.pt", "rng_state.pt"):
                assert_state_equal(
                    torch.load(direct[33] / name, weights_only=True, map_location="cpu"),
                    torch.load(other / name, weights_only=True, map_location="cpu"),
                )
            for name in ("schedule.json", "data_state.json"):
                assert json.loads((direct[33] / name).read_text()) == json.loads(
                    (other / name).read_text()
                )
    else:
        # Statistical mode: identical data accounting; weights are not claimed equal.
        for other in (queued[33], resumed[33]):
            assert json.loads((direct[33] / "data_state.json").read_text()) == json.loads(
                (other / "data_state.json").read_text()
            )
