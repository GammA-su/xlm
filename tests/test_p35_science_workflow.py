"""P35 M1/M2 public workflow: science-v1 runs through direct, queue and resume.

The real CLI, captured snapshots and frozen workers execute a bounded authored
two-source mixture on CUDA with the process producer. Direct, queued and
resumed executions must resolve one scientific identity, one training-RNG
receipt and one committed LR history. Strict mode must also agree bitwise.
The M2 test adds an authored evaluation cadence to the same frozen path.
"""

from __future__ import annotations

import copy
import json
import math
from pathlib import Path
from typing import Any

import pytest
import torch
import yaml

from p35_eval_support import write_inventory
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


# --------------------------------------------------------------------- M2 cadence


def attempt_records(home: Path) -> dict[str, dict[str, Any]]:
    """Every published evaluation attempt record under one XLM home, by artifact id."""
    return {
        path.parent.name: json.loads(path.read_text(encoding="utf-8"))
        for path in home.rglob("attempt.json")
        if path.parent.parent.name == "evaluations"
    }


def outcomes(home: Path) -> dict[str, dict[str, Any]]:
    """Outcome receipts by event id; each event must have exactly one attempt here."""
    found: dict[str, dict[str, Any]] = {}
    for record in attempt_records(home).values():
        if record["record"] == "outcome":
            event_id = record["event"]["event_id"]
            assert event_id not in found, f"{event_id} scored twice in {home}"
            found[event_id] = record
    return found


def ledger_events(checkpoint: Path) -> dict[str, dict[str, Any]]:
    ledger = json.loads((checkpoint / "science.json").read_text())["evaluation"]
    return {event["event_id"]: event for event in ledger["events"]}


def assert_training_state_equal(left: Path, right: Path) -> None:
    for name in ("model.pt", "objective.pt", "optimizer.pt", "rng_state.pt"):
        assert_state_equal(
            torch.load(left / name, weights_only=True, map_location="cpu"),
            torch.load(right / name, weights_only=True, map_location="cpu"),
        )
    for name in ("schedule.json", "data_state.json"):
        assert json.loads((left / name).read_text()) == json.loads((right / name).read_text())


@pytest.mark.cuda
@pytest.mark.serial
def test_science_v1_cadence_through_direct_queue_and_resume(tmp_path: Path) -> None:
    """An authored cadence consumed by the frozen CLI, queue and resume path on CUDA.

    Updates are 16/16/1 targets (C = 16, 32, 33), exactly as without a cadence.
    Thresholds 8 and 24 fall inside updates and must fire at the next natural
    boundary; 33 is the exact-budget endpoint. Checkpoints land at C = 16 and 32
    between recording a crossing and scoring it, so the C=32 checkpoint owes
    ``quick_lm@24`` while ``quick_lm@8`` is already complete there.
    """
    if not torch.cuda.is_available():
        pytest.skip("CUDA hardware required")
    from test_frozen_execution import authored_tree

    tree = authored_tree(tmp_path / "tree")
    evidence = tmp_path / "commands"
    data = prepare_inputs(tmp_path, tree, evidence)
    vocab_size = data.pop("vocab_size")
    tokenizer = ByteLevelBPETokenizer.load(Path(data["tokenizer_artifact"]))
    # Documents longer than the 8-token context: rolling windows rebuild RoPE
    # caches on the scored module, which must never be the trained one.
    full_docs = {
        "prose": [("p1", "the quick brown fox jumps over the lazy dog"), ("p2", "A A A z z")],
        "science": [("s1", "E = mc^2 holds; é and € are non-ASCII"), ("s2", "zeta alpha")],
    }
    quick_docs = {"prose": [full_docs["prose"][0]], "science": [full_docs["science"][0]]}
    full = write_inventory(tmp_path / "lm" / "full", full_docs, tokenizer)
    quick = write_inventory(
        tmp_path / "lm" / "quick", quick_docs, tokenizer, subset="quick", nested_in=full[1]
    )
    schedule: dict[str, Any] = {
        "type": "warmup_cosine",
        "warmup_valid_targets": 20,
        "horizon_valid_targets": 64,
    }
    science_evaluation = {
        "version": "xlm-eval-cadence-v1",
        "cadence": "authored_fixture",
        "fixture_thresholds": {"quick_lm": [0, 8, 24], "full_lm": [0, 33]},
        "confirmation_registered": False,
        "quick_lm": {"manifest": str(quick[0]), "manifest_id": quick[1]},
        "full_lm": {"manifest": str(full[0]), "manifest_id": full[1]},
        "search_benchmark": None,
        "endpoint_confirmation": None,
        "scoring": {"forward_precision": "fp32", "logprob_dtype": "fp64", "rolling_stride": 4},
    }
    plain: dict[str, Any] = {
        "id": "authored_p35_cadence",
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
                "attention_policy": "strict_deterministic_v1",
                "matmul_tf32": "disabled",
                "bf16_reduced_precision_reduction": "allowed",
            },
        },
    }
    config = copy.deepcopy(plain)
    config["evaluation"] = {"suite": "search", "science": science_evaluation}
    draft, plain_draft = tree / "cadence.yaml", tree / "plain.yaml"
    draft.write_text(yaml.safe_dump(config), encoding="utf-8")
    plain_draft.write_text(yaml.safe_dump(plain), encoding="utf-8")

    # Legacy stays legacy: the public CLI refuses a cadence without science-v1.
    legacy = copy.deepcopy(config)
    for key in ("science_version", "lr_policy", "training_seed", "runtime"):
        legacy["training"].pop(key)
    legacy_draft = tree / "legacy.yaml"
    legacy_draft.write_text(yaml.safe_dump(legacy), encoding="utf-8")
    with pytest.raises(AssertionError, match="requires training.science_version"):
        cli(tree, tmp_path / "legacy-home", evidence, "train", str(legacy_draft),
            "--device", "cuda")  # fmt: skip
    assert not attempt_records(tmp_path / "legacy-home")

    # Direct and queued with the cadence, and direct with identical inputs without it.
    direct_home, queue_home = tmp_path / "direct-home", tmp_path / "queue-home"
    plain_home = tmp_path / "plain-home"
    cli(tree, direct_home, evidence, "train", str(draft), "--device", "cuda")
    cli(tree, plain_home, evidence, "train", str(plain_draft), "--device", "cuda")
    snapshot, plan = tmp_path / "snapshot", tmp_path / "plan.json"
    cli(tree, queue_home, evidence, "experiment", "plan", str(draft), "--smoke",
        "--snapshot-dir", str(snapshot), "--output", str(plan))  # fmt: skip
    cli(tree, queue_home, evidence, "experiment", "submit", str(plan),
        "--snapshot-dir", str(snapshot), "--device", "cuda")  # fmt: skip
    cli(tree, queue_home, evidence, "queue", "run", "--once", "--device", "cuda")
    direct, queued = checkpoints(direct_home), checkpoints(queue_home)
    bare = checkpoints(plain_home)
    assert set(direct) == set(queued) == set(bare) == {16, 32, 33}

    expected_due = {  # event -> (actual committed targets, optimizer step)
        "quick_lm@0": (0, 0),
        "full_lm@0": (0, 0),
        "quick_lm@8": (16, 1),
        "quick_lm@24": (32, 2),
        "full_lm@33": (33, 3),
    }
    expected_schedule = WarmupCosineSchedule(WarmupCosineScheduleConfig(**schedule), base_lr=0.01)
    for name, found in (("direct", direct), ("queued", queued)):
        # The C=16 checkpoint owes quick_lm@8 (recorded, not yet scored); by the
        # C=32 checkpoint it is complete and quick_lm@24 is owed instead.
        at16, at32 = ledger_events(found[16]), ledger_events(found[32])
        assert at16["quick_lm@8"]["status"] == "due" and at16["quick_lm@8"]["attempts"] == []
        assert at16["quick_lm@24"]["status"] == "planned"
        assert {at16[e]["status"] for e in ("quick_lm@0", "full_lm@0")} == {"complete"}
        assert at32["quick_lm@8"]["status"] == "complete"
        assert at32["quick_lm@24"]["status"] == "due" and at32["quick_lm@24"]["attempts"] == []
        final = json.loads((found[33] / "science.json").read_text())
        assert final["evaluation"]["completeness"]["complete"] is True, name
        events = {e["event_id"]: e for e in final["evaluation"]["events"]}
        assert {
            e: (r["due"]["actual_committed_targets"], r["due"]["step"]) for e, r in events.items()
        } == expected_due, name
        # Natural boundaries only: the update sizes are those of a cadence-free run.
        rows = final["lr_receipts"]["rows"]
        assert [row[1:4] for row in rows] == [[0, 16, 16], [16, 16, 32], [32, 1, 33]], name
        for row in rows:
            assert row[4] == [expected_schedule.get_lr(row[3])] * 2

    direct_out, queued_out = outcomes(direct_home), outcomes(queue_home)
    assert len(attempt_records(direct_home)) == len(attempt_records(queue_home)) == 10
    final_events = ledger_events(direct[33])
    for event_id, (committed, _) in expected_due.items():
        receipt = direct_out[event_id]
        assert receipt["status"] == "complete" and receipt["attempt"] == 1
        assert receipt["event"]["planned_threshold"] == int(event_id.split("@")[1])
        assert receipt["actual_committed_targets"] == committed
        owed_digest = final_events[event_id]["due"]["model_state_digest"]
        assert receipt["model_state"]["state_digest"] == owed_digest
        assert final_events[event_id]["canonical_attempt"] is not None
        assert math.isfinite(receipt["metrics"]["equal_domain_text_ce_nats_per_token"])
        assert receipt["guard"]["changed"] == []
        assert {"model_tensors", "model_attributes", "module_modes", "gradients", "optimizer",
                "schedule", "counters", "data_cursor", "science_receipts",
                "runtime_flags"} <= set(receipt["guard"]["verified"])  # fmt: skip
        assert receipt["computation"]["scientific_runtime"]["attention_policy"] == (
            "strict_deterministic_v1"
        )
        # The queue resolves the same scientific evaluation for identical inputs.
        other = queued_out[event_id]
        for key in ("event", "actual_committed_targets", "step", "model_state",
                    "computation_identity", "evaluator", "metrics", "coverage"):  # fmt: skip
            assert other[key] == receipt[key], (event_id, key)
    assert direct_out["full_lm@33"]["event"]["is_endpoint"] is True

    # Evaluation never changed training: bitwise identical to the cadence-free run.
    for comparison in (queued[33], bare[33]):
        assert_training_state_equal(direct[33], comparison)
    bare_science = json.loads((bare[33] / "science.json").read_text())
    assert "evaluation" not in bare_science and not attempt_records(plain_home)

    # Fresh-process resume from C=32 into a store that never saw quick_lm@24
    # scored: the owed event is scored first, at the recorded state; completed
    # events are adopted from the checkpoint and never rescored.
    resumed_home = tmp_path / "resume-home"
    cli(tree, resumed_home, evidence, "resume", str(direct[32]), "--device", "cuda")
    resumed = checkpoints(resumed_home)
    assert set(resumed) == {33}
    resumed_out = outcomes(resumed_home)
    assert set(resumed_out) == {"quick_lm@24", "full_lm@33"}
    owed = ledger_events(direct[32])["quick_lm@24"]["due"]
    assert resumed_out["quick_lm@24"]["actual_committed_targets"] == 32
    assert resumed_out["quick_lm@24"]["model_state"]["state_digest"] == owed["model_state_digest"]
    for event_id, receipt in resumed_out.items():
        assert receipt["metrics"] == direct_out[event_id]["metrics"], event_id
    resumed_events = ledger_events(resumed[33])
    for event_id in ("quick_lm@0", "full_lm@0", "quick_lm@8"):
        saved = ledger_events(direct[32])[event_id]
        assert resumed_events[event_id]["canonical_attempt"] == saved["canonical_attempt"]
        assert resumed_events[event_id]["attempts"] == saved["attempts"]
    assert all(e["status"] == "complete" for e in resumed_events.values())
    assert_training_state_equal(direct[33], resumed[33])

    # Resume at the exact budget in the original store: nothing is owed, nothing
    # is rescored and no checkpoint is published.
    before = attempt_records(direct_home)
    cli(tree, direct_home, evidence, "resume", str(direct[33]), "--device", "cuda")
    assert attempt_records(direct_home) == before
    assert checkpoints(direct_home) == direct

    # One scientific identity across direct, queued and resumed executions.
    finals = {"direct": direct[33], "queued": queued[33], "resumed": resumed[33]}
    envelopes = {
        k: json.loads((v / "execution.json").read_text())["envelope"] for k, v in finals.items()
    }
    reference = envelopes["direct"]
    assert reference["runtime_policy"] == SCIENCE_RUNTIME_POLICY
    assert reference["config"]["evaluation"]["science"] == science_evaluation
    for name, envelope in envelopes.items():
        for key in ("config", "bindings", "runtime_policy", "code_hash", "dependency_hash"):
            assert envelope[key] == reference[key], (name, key)
    assert envelopes["resumed"]["execution_hash"] == reference["execution_hash"]
    sciences = {k: json.loads((v / "science.json").read_text()) for k, v in finals.items()}
    for name, state in {**sciences, "no_cadence": bare_science}.items():
        assert state["policy"] == sciences["direct"]["policy"], name
        assert state["train_start_rng"] == sciences["direct"]["train_start_rng"], name
        assert state["lr_receipts"] == sciences["direct"]["lr_receipts"], name
    assert sciences["direct"]["train_start_rng"]["training_seed"] == 10001
    for name in ("queued", "resumed"):
        assert sciences[name]["evaluation"]["plan"] == sciences["direct"]["evaluation"]["plan"]
        assert (
            sciences[name]["evaluation"]["evaluator_digests"]
            == sciences["direct"]["evaluation"]["evaluator_digests"]
        )
