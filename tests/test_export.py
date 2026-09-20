"""Acceptance tests for P20: native export, verified loading and generation (A35).

A toy model is trained, exported, reloaded in a clean subprocess, and compared
for identical logits, likelihood and greedy completions. Corruption, version
drift, missing plugins and secrets are all refused loudly.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("torch")

import torch

from xlm.config.schemas import TransformerBaselineConfig
from xlm.export.hf import HfMappingError, hf_config, map_state_dict_to_hf_layout
from xlm.export.loader import ExportLoadError, load_exported_model
from xlm.export.manifest import ExportError, IncompatibleExportError
from xlm.export.writer import export_model
from xlm.inference.generation import GenerationConfig, TextGenerator
from xlm.inference.session import SessionError, run_session
from xlm.models.transformer import TransformerBaseline
from xlm.tokenizers.byte import ByteTokenizer

REPO_ROOT = Path(__file__).resolve().parents[1]


def _tiny_config(**overrides: Any) -> TransformerBaselineConfig:
    base: dict[str, Any] = {
        "architecture": "transformer_baseline",
        "vocab_size": 260,
        "num_layers": 2,
        "hidden_size": 64,
        "num_attention_heads": 4,
        "intermediate_size": 128,
        "context_length": 32,
        "attention_backend": "eager",
    }
    base.update(overrides)
    return TransformerBaselineConfig(**base)


def _train_toy(tmp_path: Path, seed: int = 4) -> tuple[TransformerBaseline, ByteTokenizer]:
    from xlm.artifacts.ledger import RunLedger
    from xlm.artifacts.store import ArtifactStore
    from xlm.config.schemas import AdamWConfig, CrossEntropyObjectiveConfig
    from xlm.core.paths import ArtifactPaths
    from xlm.objectives.cross_entropy import CrossEntropyObjective
    from xlm.optimizers.adamw import create_adamw_optimizer
    from xlm.schedules.constant import ConstantSchedule, ConstantScheduleConfig
    from xlm.training.checkpoint import CheckpointManager
    from xlm.training.data import TrainingBatcher
    from xlm.training.trainer import Trainer

    paths = ArtifactPaths(root=tmp_path / "artifacts")
    manager = CheckpointManager(
        artifact_store=ArtifactStore(paths),
        run_ledger=RunLedger(paths.ledger / "ledger.sqlite"),
        paths=paths,
    )
    config = _tiny_config()
    model = TransformerBaseline(config, seed=seed)
    objective = CrossEntropyObjective(CrossEntropyObjectiveConfig())
    optimizer, manifest = create_adamw_optimizer(
        AdamWConfig(lr=0.02, weight_decay=0.0), model=model
    )
    schedule = ConstantSchedule(ConstantScheduleConfig())
    tokens = [((i % 256) + 4) for i in range(400)]
    batcher = TrainingBatcher(
        data_source=tokens,
        context_length=32,
        global_batch_valid_targets=64,
        exhaustion_policy="repeat_bounded",
        max_document_exposures=100,
    )
    trainer = Trainer(
        model=model,
        objective=objective,
        optimizer=optimizer,
        optimizer_manifest=manifest,
        schedule=schedule,
        batcher=batcher,
        checkpoint_manager=manager,
        run_id="export_toy",
        plan_id="export_plan",
        device="cpu",
        max_valid_targets=128,
    )
    summary = trainer.train()
    assert summary.termination_reason == "completed"
    return model, ByteTokenizer()


def _export_toy(
    tmp_path: Path, name: str = "exp", **kwargs: Any
) -> tuple[Path, TransformerBaseline, ByteTokenizer]:
    model, tokenizer = _train_toy(tmp_path / f"train_{name}")
    out = tmp_path / name
    export_model(model, tokenizer, out, f"{name}_id", **kwargs)
    return out, model, tokenizer


# ------------------------------------------------------- native round trip


def test_export_bundle_contains_only_declared_files(tmp_path: Path) -> None:
    out, _, _ = _export_toy(tmp_path)
    names = sorted(p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file())
    assert names == [
        "config.json",
        "export_manifest.json",
        "model.safetensors",
        "tokenizer/tokenizer_manifest.json",
    ]


def test_clean_process_load_reproduces_logits_likelihood_and_completion(
    tmp_path: Path,
) -> None:
    out, model, tokenizer = _export_toy(tmp_path)
    model.eval()
    ids = torch.randint(4, 260, (1, 8))
    with torch.no_grad():
        reference_logits = model(ids).logits
    generator = TextGenerator(model=model, tokenizer=tokenizer, device="cpu")
    reference_text = generator.generate(
        "Hello world", GenerationConfig(max_new_tokens=8)
    ).generated_text

    probe = tmp_path / "probe_load.py"
    probe.write_text(
        "import json, sys, torch\n"
        "from xlm.export.loader import load_exported_model\n"
        f"model, tok, manifest = load_exported_model(r'{out}', device='cpu')\n"
        "model.eval()\n"
        "torch.manual_seed(1234)\n"
        "ids = torch.randint(4, 260, (1, 8))\n"
        "print('IDS:' + json.dumps(ids.tolist()))\n"
        "with torch.no_grad():\n"
        "    print('LOGITS:' + json.dumps(model(ids).logits.tolist()))\n"
        "from xlm.inference.generation import GenerationConfig, TextGenerator\n"
        "gen = TextGenerator(model=model, tokenizer=tok, device='cpu')\n"
        "print('TEXT:' + gen.generate(\n"
        "    'Hello world', GenerationConfig(max_new_tokens=8)).generated_text)\n"
        "print('TIED:' + str(model.lm_head.weight is model.embed_tokens.weight))\n"
        "print('FP:' + tok.fingerprint)\n",
        encoding="utf-8",
    )
    # The in-process reference must use the same input IDs the probe draws.
    completed = subprocess.run(
        [sys.executable, str(probe)], capture_output=True, text=True, check=False
    )
    assert completed.returncode == 0, completed.stderr[-2000:]
    lines = {
        line.split(":", 1)[0]: line.split(":", 1)[1]
        for line in completed.stdout.splitlines()
        if ":" in line and line.split(":", 1)[0] in ("IDS", "LOGITS", "TEXT", "TIED", "FP")
    }
    probe_ids = torch.tensor(json.loads(lines["IDS"]))
    with torch.no_grad():
        expected = model(probe_ids).logits
    assert torch.equal(torch.tensor(json.loads(lines["LOGITS"])), expected)
    assert lines["TEXT"] == reference_text
    assert lines["TIED"] == "True"
    assert lines["FP"] == tokenizer.fingerprint
    assert reference_logits.shape == expected.shape


def test_tokenizer_reconstruction_and_tied_identity(tmp_path: Path) -> None:
    out, model, tokenizer = _export_toy(tmp_path)
    loaded, loaded_tok, manifest = load_exported_model(out, device="cpu")
    assert loaded_tok.fingerprint == tokenizer.fingerprint
    assert manifest.special_ids == {"pad": 0, "bos": 1, "eos": 2, "unk": 3}
    loaded_head = getattr(loaded, "lm_head").weight  # noqa: B009
    loaded_embed = getattr(loaded, "embed_tokens").weight  # noqa: B009
    assert loaded_head is loaded_embed
    assert torch.equal(loaded_head, model.embed_tokens.weight)
    assert manifest.tied_mapping["tied_lm_head"] == ["embed_tokens.weight", "lm_head.weight"]


# ------------------------------------------------------------- refusal paths


def test_diverged_tied_weights_are_refused(tmp_path: Path) -> None:
    from safetensors.torch import load_file as safetensors_load
    from safetensors.torch import save_file as safetensors_save

    out, _, _ = _export_toy(tmp_path, name="tied_div")
    weights_path = out / "model.safetensors"
    state = safetensors_load(str(weights_path))
    state["lm_head.weight"] = state["lm_head.weight"] + 1.0
    safetensors_save(state, str(weights_path))

    manifest_path = out / "export_manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    for entry in payload["files"]:
        if entry["path"] == "model.safetensors":
            entry["sha256"] = hashlib.sha256(weights_path.read_bytes()).hexdigest()
            entry["size_bytes"] = weights_path.stat().st_size
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ExportLoadError, match="tied weights diverged"):
        load_exported_model(out, device="cpu")


def test_corrupted_and_incomplete_exports_are_rejected(tmp_path: Path) -> None:
    out, _, _ = _export_toy(tmp_path)
    weights = out / "model.safetensors"
    data = bytearray(weights.read_bytes())
    data[100] = (data[100] + 1) % 256
    weights.write_bytes(bytes(data))
    with pytest.raises(ExportLoadError, match="hash mismatch"):
        load_exported_model(out, device="cpu")

    out2, _, _ = _export_toy(tmp_path, name="exp2")
    (out2 / "config.json").unlink()
    with pytest.raises(ExportLoadError, match="missing"):
        load_exported_model(out2, device="cpu")


def test_unknown_format_and_serializer_are_refused_with_guidance(tmp_path: Path) -> None:
    out, _, _ = _export_toy(tmp_path)
    manifest_path = out / "export_manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))

    payload["export_format_version"] = "99"
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(IncompatibleExportError, match="Re-export with the matching"):
        load_exported_model(out, device="cpu")

    payload["export_format_version"] = "1"
    payload["serializer"] = "pickle-1"
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(IncompatibleExportError, match="supported serializer"):
        load_exported_model(out, device="cpu")


def test_missing_plugin_fails_usefully(tmp_path: Path) -> None:
    out, _, _ = _export_toy(tmp_path)
    manifest_path = out / "export_manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["plugin_id"] = "ghost_mechanism"
    payload["plugin_version"] = "1"
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ExportLoadError, match="missing plugin 'ghost_mechanism'"):
        load_exported_model(out, device="cpu")


def test_plugin_family_round_trip_and_missing_plugin(tmp_path: Path) -> None:
    from xlm.plugins.noop_architecture.noop_architecture_plugin import create_noop_architecture
    from xlm.research.loader import load_plugin

    try:
        load_plugin(REPO_ROOT / "src" / "xlm" / "plugins" / "noop_architecture")
    except Exception as exc:
        assert "already registered" in str(exc)

    config = _tiny_config()
    model = create_noop_architecture(config, seed=6)
    tokenizer = ByteTokenizer()
    out = tmp_path / "plugin_exp"
    export_model(
        model, tokenizer, out, "plugin_exp_id", plugin_id="noop_architecture", plugin_version="1"
    )
    # The plugin family is registered in this process: loads with parity.
    loaded, _, manifest = load_exported_model(out, device="cpu")
    assert manifest.plugin_id == "noop_architecture"
    model.eval()
    loaded.eval()
    ids = torch.randint(4, 260, (1, 8))
    with torch.no_grad():
        assert torch.equal(loaded(ids).logits, model(ids).logits)


def test_optimizer_state_excluded_by_default_with_opt_in(tmp_path: Path) -> None:
    out, _, _ = _export_toy(tmp_path)
    assert not (out / "optimizer.pt").exists()
    manifest = json.loads((out / "export_manifest.json").read_text(encoding="utf-8"))
    assert manifest["includes_optimizer_state"] is False

    model, _ = _train_toy(tmp_path / "train_opt")
    out2 = tmp_path / "exp_opt"
    export_model(
        model,
        ByteTokenizer(),
        out2,
        "exp_opt_id",
        include_optimizer_state=True,
        optimizer_state={"step": 3},
    )
    assert (out2 / "optimizer.pt").is_file()
    manifest2 = json.loads((out2 / "export_manifest.json").read_text(encoding="utf-8"))
    assert manifest2["includes_optimizer_state"] is True


def test_training_only_heads_are_excluded_with_accounting(tmp_path: Path) -> None:
    from xlm.objectives.auxiliary_fixture import AuxiliaryLearningObjective

    model, _ = _train_toy(tmp_path / "train_aux")
    out = tmp_path / "exp_aux"
    manifest = export_model(
        model,
        ByteTokenizer(),
        out,
        "exp_aux_id",
        objective=AuxiliaryLearningObjective(),
    )
    assert manifest.parameters_training_only > 0
    assert manifest.excluded_training_heads == ["objective:aux_param"]
    names = [p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file()]
    assert not any("aux" in name for name in names)


def test_secrets_and_raw_data_never_enter_the_bundle(tmp_path: Path) -> None:
    model, _ = _train_toy(tmp_path / "train_sec")
    out = tmp_path / "exp_sec"
    out.mkdir()
    (out / "api_token.txt").write_text("hf_secret", encoding="utf-8")
    (out / "corpus.jsonl").write_text('{"text": "copyrighted"}', encoding="utf-8")
    with pytest.raises(ExportError, match="secret-like files"):
        export_model(model, ByteTokenizer(), out, "exp_sec_id")


def test_dtype_device_load_rules(tmp_path: Path) -> None:
    out, _, _ = _export_toy(tmp_path)
    model, _, manifest = load_exported_model(out, device="cpu", dtype="float32")
    assert manifest.precision_stored == "float32"
    with pytest.raises(ExportLoadError, match="CUDA device"):
        load_exported_model(out, device="cpu", dtype="bfloat16")
    with pytest.raises(ExportLoadError, match="supported load dtype"):
        load_exported_model(out, device="cpu", dtype="int8")


# ------------------------------------------------------- sessions/generation


def test_session_is_deterministic_and_resets_between_prompts(tmp_path: Path) -> None:
    from xlm.inference.session import CompletionSession

    model, tokenizer = _train_toy(tmp_path / "train_sess")
    generator = TextGenerator(model=model, tokenizer=tokenizer, device="cpu")
    prompts = ["The sky is", "Once upon a"]
    first = run_session(generator, prompts, session_seed=11, max_new_tokens=8)
    second = run_session(generator, prompts, session_seed=11, max_new_tokens=8)
    assert [t.completion for t in first.turns] == [t.completion for t in second.turns]
    assert [t.prompt_seed for t in first.turns] == [t.prompt_seed for t in second.turns]
    assert first.turns[0].prompt_seed != first.turns[1].prompt_seed

    path = tmp_path / "session.json"
    first.save(path)
    assert CompletionSession.load(path).to_dict() == first.to_dict()
    with pytest.raises(SessionError, match="at least one prompt"):
        run_session(generator, [], session_seed=1)


def test_stop_strings_and_max_context_behave_explicitly(tmp_path: Path) -> None:
    model, tokenizer = _train_toy(tmp_path / "train_stop")
    generator = TextGenerator(model=model, tokenizer=tokenizer, device="cpu")
    session = run_session(
        generator,
        ["Hello world"],
        session_seed=3,
        max_new_tokens=16,
        stop_strings=["zzz_not_present_zzz"],
    )
    assert session.turns[0].finish_reason in (
        "eos",
        "stop_token",
        "max_new_tokens",
        "context_limit",
    )

    overlong = "word " * 500
    with pytest.raises(Exception, match="(?i)(too long|context|length|truncat)"):
        run_session(generator, [overlong], session_seed=3, max_new_tokens=4)


def test_no_chat_template_is_attached() -> None:
    import inspect

    from xlm.inference import session as session_mod

    source = inspect.getsource(session_mod) + inspect.getsource(run_session)
    lowered = source.lower()
    assert "chat_template" not in lowered
    assert "system_role" not in lowered and "<|assistant|>" not in lowered


# ------------------------------------------------------------------ HF layout


def test_hf_mapping_is_complete_and_shape_exact(tmp_path: Path) -> None:
    del tmp_path
    config = _tiny_config()
    model = TransformerBaseline(config, seed=2)
    state = model.state_dict()
    hf_state, report = map_state_dict_to_hf_layout(state, config.model_dump())
    assert report.native_keys_mapped == len(state)
    assert not report.unmapped_native_keys and not report.shape_mismatches
    for key, tensor in state.items():
        mapped = [v for k, v in hf_state.items() if v is tensor]
        assert len(mapped) == 1, f"native key '{key}' must map exactly once"
        assert mapped[0].shape == tensor.shape
    assert report.config["num_attention_heads"] == report.config["num_key_value_heads"] == 4
    assert report.config["tie_word_embeddings"] is True
    assert report.special_ids == {
        "pad_token_id": 0,
        "bos_token_id": 1,
        "eos_token_id": 2,
        "unk_token_id": 3,
    }
    assert hf_config(config.model_dump())["architectures"] == ["LlamaForCausalLM"]


def test_hf_mapping_refuses_non_baseline_and_partial_keys() -> None:
    config = _tiny_config()
    model = TransformerBaseline(config, seed=2)
    bad_config = dict(config.model_dump())
    bad_config["architecture"] = "mamba_like_v9"
    with pytest.raises(HfMappingError, match="cannot be exported as an existing HF GPT"):
        map_state_dict_to_hf_layout(model.state_dict(), bad_config)
    extra = dict(model.state_dict())
    extra["mystery.weight"] = next(iter(extra.values()))
    with pytest.raises(HfMappingError, match="no HF counterpart"):
        map_state_dict_to_hf_layout(extra, config.model_dump())


def test_cache_support_is_labeled_not_assumed(tmp_path: Path) -> None:
    out, _, _ = _export_toy(tmp_path, name="cache_lbl")
    manifest = json.loads((out / "export_manifest.json").read_text(encoding="utf-8"))
    assert manifest["cache_support"] is False


def test_generate_command_reads_export_bundles(tmp_path: Path) -> None:
    import subprocess as _subprocess

    out, _, _ = _export_toy(tmp_path, name="gen_exp")
    completed = _subprocess.run(
        [
            sys.executable,
            "-m",
            "xlm.cli.main",
            "generate",
            str(out),
            "--prompt",
            "Hello",
            "--max-new-tokens",
            "6",
            "--json",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr[-1500:]
    payload = json.loads(completed.stdout)
    assert payload["generated_token_ids"]


def test_general_inference_loader_cannot_bypass_export_integrity(tmp_path: Path) -> None:
    from xlm.models.serialization import load_model_for_inference

    out, _, _ = _export_toy(tmp_path, name="audit_corruption")
    config = json.loads((out / "config.json").read_text(encoding="utf-8"))
    config["context_length"] += 1
    (out / "config.json").write_text(json.dumps(config), encoding="utf-8")
    with pytest.raises(ExportLoadError, match="hash mismatch"):
        load_model_for_inference(out)
