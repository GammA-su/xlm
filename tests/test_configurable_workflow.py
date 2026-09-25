"""Public D03 workflow: authored two-source CPU training and exact continuation."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import torch
import yaml

from xlm.core.contracts import CanonicalDocument
from xlm.data.normalization import compute_sha256
from xlm.data.sampling import MixtureComponent, MixtureRecipe, PackingPolicy
from xlm.data.tokens import TokenShardReader
from xlm.tokenizers.bpe import ByteLevelBPETokenizer

ROOT = Path(__file__).resolve().parents[1]


def documents() -> list[CanonicalDocument]:
    return [
        CanonicalDocument(
            doc_id=source + str(index),
            source_id=source,
            source_revision="authored-v1",
            source_file="authored.txt",
            source_row=index,
            raw_hash=compute_sha256(text),
            clean_hash=compute_sha256(text),
            text=text,
            utf8_byte_count=len(text),
            language="en",
            language_confidence=1.0,
            document_kind="prose",
            source_metadata={},
            parent_ids=[],
            license_reference="authored fixture",
            transform_log=[],
            quality_reasons=[],
            cluster_ids={"split_group": source + str(index)},
            split="train",
        )
        for source, text in (("alpha", "A " * 48), ("zeta", "z " * 48))
        for index in range(2)
    ]


def cli(root: Path, home: Path, evidence: Path, *args: str) -> subprocess.CompletedProcess[str]:
    evidence.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [sys.executable, "-m", "xlm.cli.main", *args],
        cwd=root,
        env={
            **os.environ,
            "XLM_HOME": str(home),
            "PYTHONPATH": str(root / "src"),
            "UV_OFFLINE": "1",
            "HF_HUB_OFFLINE": "1",
            "HF_DATASETS_OFFLINE": "1",
            "OMP_NUM_THREADS": "1",
            "MKL_NUM_THREADS": "1",
        },
        text=True,
        capture_output=True,
        timeout=240,
    )
    index = len(list(evidence.glob("*.json")))
    (evidence / f"{index:02}.json").write_text(
        json.dumps(
            {
                "argv": [sys.executable, "-m", "xlm.cli.main", *args],
                "cwd": str(root),
                "returncode": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
            }
        ),
        encoding="utf-8",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result


def checkpoints(home: Path) -> dict[int, Path]:
    result = {}
    for path in home.rglob("checkpoint_meta.json"):
        meta = json.loads(path.read_text())
        result[meta["committed_valid_targets"]] = path.parent
    return result


def assert_state_equal(left: object, right: object) -> None:
    if isinstance(left, torch.Tensor):
        assert isinstance(right, torch.Tensor) and torch.equal(left, right)
    elif isinstance(left, dict):
        assert isinstance(right, dict) and left.keys() == right.keys()
        for key in left:
            assert_state_equal(left[key], right[key])
    elif isinstance(left, (list, tuple)):
        assert isinstance(right, (list, tuple)) and len(left) == len(right)
        for a, b in zip(left, right, strict=True):
            assert_state_equal(a, b)
    else:
        assert left == right


def authored_component_plugins(tree: Path) -> list[str]:
    """Test-only registered variants, loaded from the actual captured package."""
    bodies = {
        "architecture": """from dataclasses import replace
from xlm.config.schemas import TransformerBaselineConfig
from xlm.models.transformer import TransformerBaseline
class Config(TransformerBaselineConfig):
    architecture: Literal["authored_architecture"] = "authored_architecture"
class Model(TransformerBaseline):
    def forward(self, *args, **kwargs):
        print("AUTHORED_ARCHITECTURE_FORWARD_EXECUTED", flush=True)
        output = super().forward(*args, **kwargs)
        return replace(output, logits=output.logits * 0.75)
def factory(config):
    print("AUTHORED_ARCHITECTURE_FACTORY_EXECUTED", flush=True)
    return Model(config)
""",
        "optimizer": """from xlm.config.schemas import AdamWConfig
from xlm.optimizers.adamw import create_adamw_optimizer
class Config(AdamWConfig):
    type: Literal["authored_optimizer"] = "authored_optimizer"
    eps: float = 1e-6
def factory(config, **kwargs):
    print("AUTHORED_OPTIMIZER_FACTORY_EXECUTED", flush=True)
    return create_adamw_optimizer(config, **kwargs)
""",
        "tokenizer": """from xlm.config.schemas import BPETokenizerConfig
from xlm.tokenizers.bpe import ByteLevelBPETokenizer
class Config(BPETokenizerConfig):
    type: Literal["authored_tokenizer"] = "authored_tokenizer"
def factory(config, *, artifact):
    print("AUTHORED_TOKENIZER_FACTORY_EXECUTED", flush=True)
    tokenizer = ByteLevelBPETokenizer.load(artifact)
    if tokenizer._target_vocab_size != config.target_vocab_size:
        raise ValueError("authored tokenizer configuration mismatch")
    return tokenizer
""",
    }
    names = []
    for category, body in bodies.items():
        name = "authored_" + category
        names.append(name)
        plugin = tree / "src/xlm/plugins" / name
        plugin.mkdir()
        (plugin / "plugin.yaml").write_text(
            yaml.safe_dump(
                {
                    "plugin_id": name,
                    "version": "1",
                    "category": category,
                    "enabled": True,
                    "entry_module": "entry.py",
                    "files": ["entry.py"],
                    "capabilities": {},
                }
            ),
            encoding="utf-8",
        )
        body += f'''\ndef register(registry, capabilities):
    caps = capabilities.to_dict()
    caps["loads_artifact"] = {category == "tokenizer"!r}
    return registry.register("{name}", "1", Config, caps, "1", factory)
'''
        (plugin / "entry.py").write_text("from typing import Literal\n" + body, encoding="utf-8")
    return names


@pytest.mark.parametrize(
    "auxiliary,producer,device",
    [
        pytest.param(False, False, "cpu", id="False"),
        pytest.param(True, False, "cpu", id="True"),
        pytest.param(False, True, "cpu", id="producer_cpu"),
        pytest.param(False, True, "cuda", id="producer_cuda", marks=pytest.mark.cuda),
    ],
)
@pytest.mark.serial
def test_public_two_source_prepare_direct_queue_and_resume(
    tmp_path: Path, auxiliary: bool, producer: bool, device: str
) -> None:
    if device == "cuda" and not torch.cuda.is_available():
        pytest.skip("CUDA hardware required")
    from test_frozen_execution import authored_tree

    tree = authored_tree(tmp_path / "tree")
    if auxiliary:
        plugin = tree / "src/xlm/plugins/authored_aux"
        plugin.mkdir()
        (plugin / "plugin.yaml").write_text(
            yaml.safe_dump(
                {
                    "plugin_id": "authored_aux",
                    "version": "1",
                    "category": "objective",
                    "enabled": True,
                    "entry_module": "entry.py",
                    "files": ["entry.py"],
                    "capabilities": {
                        "loss_protocol": "token_additive",
                        "supports_microbatching": True,
                        "has_auxiliary_parameters": True,
                        "requires_hidden_states": False,
                    },
                }
            ),
            encoding="utf-8",
        )
        (plugin / "entry.py").write_text(
            """from typing import Literal
from xlm.config.schemas import StrictConfigModel
from xlm.objectives.auxiliary_fixture import AuxiliaryLearningObjective
import random
import numpy as np
import torch
class Config(StrictConfigModel):
    type: Literal["authored_aux"] = "authored_aux"
    version: str = "1"
    init_val: float = 1.0
    target_val: float = 3.0
class Objective(AuxiliaryLearningObjective):
    def forward(self, *args, **kwargs):
        self.target_val = 3.0 + random.random() + float(np.random.random()) + float(torch.rand(()))
        return super().forward(*args, **kwargs)
def factory(config):
    print("AUTHORED_AUX_FACTORY_EXECUTED", flush=True)
    return Objective(config.init_val, config.target_val)
def register(registry, capabilities):
    return registry.register("authored_aux", "1", Config,
        capabilities.to_dict(), "1", factory)
""",
            encoding="utf-8",
        )
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
    shards = tmp_path / "shards"
    exposure = tmp_path / "exposure.json"
    prepare = tree / "prepare.yaml"
    prepare.write_text(
        yaml.safe_dump(
            {
                "id": "authored_d03",
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
                            "authored_d03",
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
    evidence = tmp_path / "commands"
    cli(
        tree,
        tmp_path / "prepare-home",
        evidence,
        "prepare",
        "--config",
        str(prepare),
        "--authorize",
        "--timeout",
        "60",
    )
    config = {
        "id": "authored_d03",
        "model": {
            "architecture": "transformer_baseline",
            "vocab_size": tokenizer.vocab_size,
            "hidden_size": 16,
            "intermediate_size": 32,
            "num_layers": 1,
            "num_attention_heads": 2,
            "context_length": 8,
            "attention_backend": "eager",
        },
        "data": {
            "mixture": recipe.model_dump(mode="json"),
            "sources": {s: str(shards / s) for s in ("alpha", "zeta")},
            "tokenizer_artifact": str(tmp_path / "tokenizer"),
            "tokenizer": {"type": "bpe", "target_vocab_size": 260},
            "exposure_plan": str(exposure),
        },
        "objective": {"type": "authored_aux" if auxiliary else "cross_entropy"},
        "optimizer": {"type": "adamw", "lr": 0.01},
        "training": {
            "device": device,
            "precision": "fp32",
            "producer_prefetch": "process_depth1" if producer else "off",
            "context_length": 8,
            "init_seed": 7,
            "data_seed": 42,
            "global_batch_valid_targets": 16,
            "microbatch_sequences": 2,
            "checkpoint_every_valid_targets": 16,
            "budget": {"max_valid_targets": 33, "max_train_seconds": 30},
            "schedule": {"type": "constant"},
        },
    }
    if auxiliary:
        config["plugins"] = ["authored_aux", *authored_component_plugins(tree)]
        config["model"]["architecture"] = "authored_architecture"
        config["optimizer"]["type"] = "authored_optimizer"
        config["data"]["tokenizer"]["type"] = "authored_tokenizer"
    draft = tree / "draft.yaml"
    draft.write_text(yaml.safe_dump(config), encoding="utf-8")
    direct_home, queue_home = tmp_path / "direct-home", tmp_path / "queue-home"
    cli(tree, direct_home, evidence, "train", str(draft), "--device", device)
    snapshot = tmp_path / "snapshot"
    plan = tmp_path / "plan.json"
    cli(
        tree,
        queue_home,
        evidence,
        "experiment",
        "plan",
        str(draft),
        "--smoke",
        "--snapshot-dir",
        str(snapshot),
        "--output",
        str(plan),
    )
    cli(
        tree,
        queue_home,
        evidence,
        "experiment",
        "submit",
        str(plan),
        "--snapshot-dir",
        str(snapshot),
        "--device",
        device,
    )
    if auxiliary:
        # Live B cannot execute. Submitted A and A's checkpoint must still run
        # their captured plugin, including its original schema and factory.
        (plugin / "entry.py").write_text('raise RuntimeError("LIVE_PLUGIN_B_EXECUTED")\n')
    cli(tree, queue_home, evidence, "queue", "run", "--once", "--device", device)
    direct, queued = checkpoints(direct_home), checkpoints(queue_home)
    assert set(direct) == {16, 32, 33}, direct
    assert set(queued) == {16, 32, 33}, queued
    resumed_home = tmp_path / "resume-home"
    cli(tree, resumed_home, evidence, "resume", str(direct[16]), "--device", device)
    resumed = checkpoints(resumed_home)
    assert set(resumed) == {32, 33}
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
    traces = []
    for target in (16, 32, 33):
        state = json.loads((direct[target] / "data_state.json").read_text())
        traces.extend(state["last_step_trace"])
        assert state["byte_coverage_complete"] and not state["last_step_trace_truncated"]
    assert len(traces) == 33 and {t["source_id"] for t in traces} == {"alpha", "zeta"}
    assert abs(sum(t["source_id"] == "alpha" for t in traces) - 33 * 0.75) <= 4
    for source in ("alpha", "zeta"):
        tokens = TokenShardReader(shards / source).read_tokens()
        rows = [t for t in traces if t["source_id"] == source]
        assert [t["token_offset"] for t in rows] == list(range(1, len(rows) + 1))
        assert all(t["label"] == tokens[t["token_offset"]] for t in rows)
        assert all(t["doc_id"] == source + "0" for t in rows)
        assert all(t["byte_span"][1] > t["byte_span"][0] for t in rows)
    execution = json.loads((direct[33] / "execution.json").read_text())
    assert execution["envelope"]["config"]["data"]["mixture"] == recipe.model_dump(mode="json")
    assert execution["envelope"]["config"]["training"]["producer_prefetch"] == (
        "process_depth1" if producer else "off"
    )
    if auxiliary:
        objective = torch.load(direct[33] / "objective.pt", weights_only=True)
        assert objective["aux_param"].item() > 1.0
        assert "AUTHORED_ARCHITECTURE_FORWARD_EXECUTED" in (evidence / "01.json").read_text()
        assert "AUTHORED_AUX_FACTORY_EXECUTED" in (evidence / "01.json").read_text()
        assert any("authored_aux" in key for key in execution["observations"]["module_origins"])
        for category in ("architecture", "optimizer", "tokenizer"):
            assert (
                f"AUTHORED_{category.upper()}_FACTORY_EXECUTED"
                in (evidence / "01.json").read_text()
            )
            assert any(
                "authored_" + category in key for key in execution["observations"]["module_origins"]
            )
    else:
        evaluated = cli(
            tree,
            direct_home,
            evidence,
            "evaluate",
            str(direct[33]),
            "--suite",
            "synthetic_mc",
            "--device",
            device,
            "--json",
        )
        receipt = json.loads(evaluated.stdout)[0]
        assert receipt["execution_provenance"]["training"]["envelope"] == execution["envelope"]
        assert receipt["execution_provenance"]["evaluator"]["purpose"] == "evaluation"
