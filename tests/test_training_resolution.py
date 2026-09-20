"""D03 input, capability and packing checks without numerical production work."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
import torch

from xlm.data.sampling import MixtureComponent, MixtureRecipe, PackingPolicy
from xlm.experiments.execution import resolve_execution_config
from xlm.training.components import construct_training_components, inspect_model_shape


def config(tmp_path: Path) -> dict:
    from test_configurable_training import authored_shard

    sources = {s: str(tmp_path / s) for s in ("alpha", "zeta")}
    for source, location in sources.items():
        authored_shard(Path(location), source)
    return {
        "model": {
            "architecture": "transformer_baseline",
            "vocab_size": 260,
            "hidden_size": 16,
            "intermediate_size": 32,
            "num_layers": 1,
            "num_attention_heads": 2,
            "context_length": 8,
            "attention_backend": "eager",
        },
        "data": {
            "sources": sources,
            "tokenizer_artifact": "byte",
            "mixture": {
                "mixture_id": "authored",
                "components": [
                    {"source_id": "alpha", "weight": 0.5},
                    {"source_id": "zeta", "weight": 0.5},
                ],
                "packing": {"max_document_tokens": 4},
            },
        },
        "objective": {"type": "cross_entropy"},
        "optimizer": {"type": "adamw", "lr": 0.01},
        "training": {
            "device": "cpu",
            "precision": "fp32",
            "context_length": 8,
            "global_batch_valid_targets": 8,
            "budget": {"max_valid_targets": 17, "max_train_seconds": 30},
            "schedule": {"type": "constant"},
        },
    }


@pytest.mark.parametrize(
    "change", ["missing", "source", "tokenizer", "exposure", "seed", "field", "packing"]
)
def test_declared_input_mismatches_are_refused(tmp_path: Path, change: str) -> None:
    raw = config(tmp_path)
    data = raw["data"]
    match = ""
    if change == "missing":
        data["sources"].pop("zeta")
        match = "missing shard"
    elif change == "source":
        data["sources"]["zeta"] = data["sources"]["alpha"]
        match = "source identity"
    elif change == "tokenizer":
        manifest = Path(data["sources"]["alpha"]) / "shard_manifest.json"
        value = json.loads(manifest.read_text())
        value["tokenizer_hash"] = "wrong"
        manifest.write_text(json.dumps(value))
        match = "tokenizer identity"
    elif change == "exposure":
        data["exposure_plan"] = {"basis": "matched_bytes"}
        match = "deferred"
    elif change == "seed":
        data["mixture"]["data_seed"] = 1
        match = "seed"
    elif change == "field":
        data["ignore_me"] = 1
        match = "unsupported training data fields"
    else:
        data["mixture"]["packing"]["cross_document_attention"] = False
        match = "causal_stream requires"
    with pytest.raises((ValueError, FileNotFoundError), match=match):
        resolve_execution_config(raw)


def test_token_exposure_projection_is_recomputed(tmp_path: Path) -> None:
    from xlm.data.sampling import compile_exposure_plan, validate_mixture

    resolved, _ = resolve_execution_config(config(tmp_path))
    components = construct_training_components(resolved, device="cpu")
    stream = components.batcher
    recipe = MixtureRecipe.model_validate(resolved["data"]["mixture"])
    exposure = compile_exposure_plan(
        recipe, validate_mixture(recipe, stream.availability), 17, block_size=4
    ).to_dict()
    resolved["data"]["exposure_plan"] = exposure
    valid, _ = resolve_execution_config(resolved)
    assert valid["data"]["exposure_plan"] == exposure
    resolved["training"]["budget"]["max_valid_targets"] = 18
    with pytest.raises(ValueError, match="recomputed token budget"):
        resolve_execution_config(resolved)


def test_exposure_quotas_and_block_size_change_actual_stream(tmp_path: Path) -> None:
    from xlm.data.sampling import compile_exposure_plan, validate_mixture

    resolved, _ = resolve_execution_config(config(tmp_path))
    original = construct_training_components(resolved, device="cpu").batcher
    recipe = MixtureRecipe.model_validate(resolved["data"]["mixture"])
    traces = []
    streams = []
    for block_size in (1, 8):
        plan = compile_exposure_plan(
            recipe, validate_mixture(recipe, original.availability), 17, block_size=block_size
        ).to_dict()
        assert sum(p["planned_targets"] for p in plan["projections"].values()) == 17
        selected = copy.deepcopy(resolved)
        selected["data"]["exposure_plan"] = plan
        stream = construct_training_components(selected, device="cpu").batcher
        trace = []
        for remaining in (8, 8, 1):
            batches = stream.next_step_microbatches(remaining)
            assert sum(b.metadata["valid_targets"] for b in batches) == remaining
            stream.commit()
            trace.extend(stream.get_state()["last_step_trace"])
        for source, projection in plan["projections"].items():
            rows = [t for t in trace if t["source_id"] == source]
            assert len(rows) == projection["planned_targets"]
            assert [t["token_offset"] for t in rows] == list(range(1, len(rows) + 1))
        traces.append([t["source_id"] for t in trace])
        streams.append(stream)
    assert traces[0] != traces[1]
    with pytest.raises(RuntimeError, match="exposure plan identity differs"):
        streams[1].load_state(streams[0].get_state())


def test_registered_unsupported_capabilities_fail_before_training(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from xlm.training.components import component_catalog

    catalog = component_catalog()
    catalog["optimizer"].get("adamw").capabilities["requires_closure"] = True
    monkeypatch.setattr("xlm.training.components.component_catalog", lambda plugins=None: catalog)
    with pytest.raises(ValueError, match="requires closures"):
        resolve_execution_config(config(tmp_path))


def test_explicit_existing_control_plugin_does_not_mutate_global_registry() -> None:
    from xlm.core.registry import objectives
    from xlm.training.components import component_catalog

    original = objectives.get("noop_objective")
    catalog = component_catalog(["noop_objective"])
    assert catalog["objective"].get("noop_objective").factory is not original.factory
    assert objectives.get("noop_objective") is original
    with pytest.raises(ValueError, match="duplicate selected plugin"):
        component_catalog(["noop_objective", "noop_objective"])


def test_worker_disk_sampling_tolerates_retired_staging_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from xlm.experiments.launcher import _observed_file_size

    file = tmp_path / "retired.json"
    file.write_bytes(b"abc")
    assert _observed_file_size(file) == 3
    original = Path.stat
    calls = 0

    def retires_between_checks(path, *args, **kwargs):
        nonlocal calls
        if path == file and kwargs.get("follow_symlinks", True):
            calls += 1
            if calls == 2:
                raise FileNotFoundError("authored atomic retirement")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", retires_between_checks)
    assert _observed_file_size(file) == 0

    def denied(path, *args, **kwargs):
        if path == file:
            raise PermissionError("authored access failure")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", denied)
    with pytest.raises(PermissionError, match="authored access failure"):
        _observed_file_size(file)


@pytest.mark.parametrize("preset", ["50m", "150m", "300m"])
def test_production_shapes_are_meta_only_and_smoke_refuses(preset: str) -> None:
    from xlm.config.composer import ConfigComposer
    from xlm.experiments.direct import _smoke_limits

    model = ConfigComposer(Path(__file__).resolve().parents[1]).load_model_preset(preset)
    raw = {"model": model, "training": {"budget": {"max_valid_targets": 1, "max_train_seconds": 1}}}
    assert inspect_model_shape(raw) >= 5_000_000
    with pytest.raises(ValueError, match="fewer than 5 million"):
        _smoke_limits(raw)


@pytest.mark.parametrize("cap", [None, 1, 4])
def test_real_document_boundaries_positions_and_model_isolation(
    tmp_path: Path, cap: int | None
) -> None:
    from test_trainer_mixture import make_doc
    from xlm.core.paths import ArtifactPaths
    from xlm.data.sampling import MixtureBatcher
    from xlm.data.tokens import TokenShardReader, TokenShardWriter
    from xlm.tokenizers.byte import ByteTokenizer
    from xlm.training.checkpoint import CheckpointManager
    from xlm.training.trainer import Trainer

    raw = config(tmp_path)
    shard = tmp_path / "isolated"
    TokenShardWriter(shard, "isolation", "alpha", ByteTokenizer()).write_documents(
        [make_doc("first", "AB", "alpha"), make_doc("second", "xy", "alpha")],
        add_special_tokens=True,
    )
    recipe = MixtureRecipe(
        mixture_id="isolation",
        components=[MixtureComponent(source_id="alpha", weight=1)],
        packing=PackingPolicy(
            mode="isolated_document", cross_document_attention=False, max_document_tokens=cap
        ),
    )
    stream = MixtureBatcher(
        recipe,
        {"alpha": TokenShardReader(shard)},
        context_length=8,
        global_batch_valid_targets=6,
        emit_tensors=True,
    )
    components = construct_training_components(resolve_execution_config(raw)[0], device="cpu")
    seen = []

    def observe(module, args, kwargs):
        seen.append({k: v.detach().clone() for k, v in kwargs.items()})

    components.model.register_forward_pre_hook(observe, with_kwargs=True)
    trainer = Trainer(
        model=components.model,
        objective=components.objective,
        optimizer=components.optimizer,
        optimizer_manifest=components.optimizer_manifest,
        schedule=components.schedule,
        batcher=stream,
        checkpoint_manager=CheckpointManager(paths=ArtifactPaths(root=tmp_path / "home")),
        run_id="isolation",
        plan_id="fixture",
        device="cpu",
        precision="fp32",
        max_valid_targets=6,
    )
    assert trainer.train_step().valid_targets == 6
    trace = stream.get_state()["last_step_trace"]
    assert [t["label"] for t in trace] == [
        ord("A") + 4,
        ord("B") + 4,
        2,
        ord("x") + 4,
        ord("y") + 4,
        2,
    ]
    assert [t["doc_id"] for t in trace] == ["first"] * 3 + ["second"] * 3
    assert all(call["attention_mask"].ndim == 4 for call in seen)
    if cap is None:
        call = seen[0]
        assert call["position_ids"][0, :7].tolist() == [0, 1, 2, 3, 0, 1, 2]
        assert not call["attention_mask"][0, 0, 5, :4].any()
        baseline = components.model(**call).logits.detach()
        altered = copy.deepcopy(call)
        altered["input_ids"][0, 1:3] = 200
        changed = components.model(**altered).logits.detach()
        assert torch.equal(baseline[:, 4:7], changed[:, 4:7])
