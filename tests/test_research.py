"""Acceptance tests for P18: idea cards, plugin seams, capabilities and scaffolds.

All research content is authored synthetic fixture material. No benchmark
labels, no live runs, no novelty claims.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from xlm.core.registry import Registry
from xlm.research.capabilities import (
    ExecutionContext,
    PluginCapabilities,
    check_combination,
)
from xlm.research.ideas import (
    CostRecord,
    IdeaCard,
    IdeaStatus,
    IdeaValidationError,
    PriorArtEntry,
    blank_card,
    load_card,
    save_card,
    validate_card,
    validate_card_file,
)
from xlm.research.loader import PluginError, load_manifest, load_plugin
from xlm.research.scaffold import ScaffoldError, write_scaffold

REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGINS_ROOT = REPO_ROOT / "src" / "xlm" / "plugins"


def fresh_registries() -> dict[str, Registry[Any]]:
    """Isolated registries so plugin loading never touches global singletons."""
    return {
        "architecture": Registry("architecture"),
        "objective": Registry("objective"),
        "optimizer": Registry("optimizer"),
        "tokenizer": Registry("tokenizer"),
    }


def complete_card() -> IdeaCard:
    return IdeaCard(
        idea_id="demo-idea",
        title="Gated residual scaling",
        category="architecture",
        status=IdeaStatus.PROPOSED,
        bottleneck="deep signal attenuation",
        mechanism="scale residuals by a learned scalar gate per block",
        original_element="the per-block scalar gate placement after attention",
        prior_art=[
            PriorArtEntry(
                description="Highway networks use gated transforms",
                sources=["Srivastava et al., 2015"],
                search_terms=["gated residual", "highway transformer"],
                date="2026-09-01",
            )
        ],
        prior_art_status="one close family found; placement differs",
        predictions=["deeper blocks keep gradient norm within 2x"],
        falsification_experiment="remove the gate; if the depth curve is unchanged, kill it",
        allowed_information="past tokens only; no future labels",
        costs=CostRecord(parameters="+0.01%", compute="+0.5%"),
        baselines=["baseline_50m_mix01"],
        ablations=["gate removed", "gate shared across blocks"],
        tuning_allowance="same 3-trial budget as baseline",
        kill_rules=["no depth-curve change by 128M targets"],
        promotion_rules=["+1 suite point with 2 seeds"],
    )


# ------------------------------------------------------------------ idea cards


def test_blank_card_fails_on_missing_fields() -> None:
    problems = validate_card(blank_card("empty"))
    assert any("falsification_experiment" in p for p in problems)
    assert any("allowed_information" in p for p in problems)
    assert any("baselines" in p for p in problems)
    assert len(problems) > 5


def test_complete_card_validates() -> None:
    assert validate_card(complete_card()) == []


def test_novelty_certainty_phrasing_is_refused() -> None:
    card = complete_card().model_copy(
        update={"original_element": "the first ever gated residual, nobody has ever tried it"}
    )
    problems = validate_card(card)
    assert any("novelty certainty" in p for p in problems)


def test_card_statuses_round_trip_and_category_is_strict(tmp_path: Path) -> None:
    card = complete_card().model_copy(update={"status": IdeaStatus.IMPLEMENTED})
    path = save_card(card, tmp_path / "idea.yaml")
    assert load_card(path).status == IdeaStatus.IMPLEMENTED
    with pytest.raises(Exception, match="category"):
        IdeaCard.model_validate({**complete_card().model_dump(), "category": "alchemy"})
    with pytest.raises(IdeaValidationError, match="not found"):
        load_card(tmp_path / "missing.yaml")


# ------------------------------------------------------- four no-op plugins


def test_all_four_noop_plugins_load_into_isolated_registries() -> None:
    registries = fresh_registries()
    results = {}
    for plugin_id in ("noop_architecture", "noop_objective", "noop_optimizer", "noop_tokenizer"):
        result = load_plugin(PLUGINS_ROOT / plugin_id, registries)
        results[plugin_id] = result
        assert "nonnovel" in result.novelty
    assert results["noop_architecture"].registered_key == "noop_architecture:1"
    assert results["noop_objective"].registered_key == "noop_objective:1"
    assert results["noop_optimizer"].registered_key == "noop_optimizer:1"
    assert results["noop_tokenizer"].registered_key == "noop_tokenizer:1"
    # Nothing registered outside the four expected keys.
    for kind, registry in registries.items():
        assert [e.key for e in registry.list_entries()] == [f"noop_{kind}:1"]


def test_plugin_loading_touches_no_trainer_or_scorer_files() -> None:
    registries = fresh_registries()
    for plugin_id in ("noop_architecture", "noop_objective", "noop_optimizer", "noop_tokenizer"):
        result = load_plugin(PLUGINS_ROOT / plugin_id, registries)
        for filename in result.files:
            assert "trainer" not in filename and "scorer" not in filename
            assert filename.endswith("_plugin.py")


def test_architecture_noop_matches_baseline_exactly() -> None:
    pytest.importorskip("torch")
    import torch

    from xlm.config.schemas import TransformerBaselineConfig
    from xlm.models.transformer import TransformerBaseline
    from xlm.plugins.noop_architecture.noop_architecture_plugin import create_noop_architecture

    config = TransformerBaselineConfig(
        architecture="transformer_baseline",
        vocab_size=64,
        num_layers=2,
        hidden_size=32,
        num_attention_heads=2,
        intermediate_size=64,
        context_length=16,
        attention_backend="eager",
    )
    reference = TransformerBaseline(config, seed=11)
    plugin_model = create_noop_architecture(config, seed=11)
    plugin_model.load_state_dict(reference.state_dict())
    plugin_model.eval()
    reference.eval()
    ids = torch.randint(4, 64, (2, 16))
    with torch.no_grad():
        assert torch.equal(plugin_model(ids).logits, reference(ids).logits)
    assert (
        plugin_model.count_parameters().unique_deployed
        == reference.count_parameters().unique_deployed
    )


def test_objective_noop_matches_baseline_loss() -> None:
    pytest.importorskip("torch")
    import torch

    from xlm.config.schemas import TransformerBaselineConfig
    from xlm.core.contracts import TrainingBatch
    from xlm.models.transformer import TransformerBaseline
    from xlm.objectives.cross_entropy import CrossEntropyObjective
    from xlm.objectives.noop import NoOpObjective

    config = TransformerBaselineConfig(
        architecture="transformer_baseline",
        vocab_size=64,
        num_layers=2,
        hidden_size=32,
        num_attention_heads=2,
        intermediate_size=64,
        context_length=16,
        attention_backend="eager",
    )
    model = TransformerBaseline(config, seed=3)
    model.eval()
    ids = torch.randint(4, 64, (2, 16))
    with torch.no_grad():
        out = model(ids)
    batch = TrainingBatch(
        input_ids=ids,
        labels=torch.roll(ids, -1, 1),
        loss_mask=torch.ones_like(ids),
        position_ids=None,
    )
    base = CrossEntropyObjective()(out, batch)
    noop = NoOpObjective()(out, batch)
    assert noop.loss.item() == pytest.approx(base.loss.item())
    assert noop.unscaled_loss_sum == pytest.approx(base.unscaled_loss_sum)


def test_optimizer_noop_matches_baseline_updates() -> None:
    pytest.importorskip("torch")
    import torch

    from xlm.plugins.noop_optimizer.noop_optimizer_plugin import NoOpAdamW

    torch.manual_seed(9)
    params_a = [torch.nn.Parameter(torch.randn(8, 8))]
    params_b = [torch.nn.Parameter(params_a[0].detach().clone())]
    opt_plugin = NoOpAdamW(params_a, lr=0.01, weight_decay=0.0)
    opt_base = torch.optim.AdamW(params_b, lr=0.01, weight_decay=0.0)
    loss_a = (params_a[0] ** 2).sum()
    loss_b = (params_b[0] ** 2).sum()
    opt_plugin.zero_grad()
    opt_base.zero_grad()
    loss_a.backward()  # type: ignore[no-untyped-call]
    loss_b.backward()  # type: ignore[no-untyped-call]
    opt_plugin.step()
    opt_base.step()
    assert torch.equal(params_a[0], params_b[0])
    assert set(opt_plugin.state_dict()) == set(opt_base.state_dict())


def test_tokenizer_noop_matches_baseline_deterministically() -> None:
    from xlm.evaluation.likelihood import ConditionalLikelihoodScorer
    from xlm.plugins.noop_tokenizer.noop_tokenizer_plugin import NoOpByteTokenizer
    from xlm.tokenizers.byte import ByteTokenizer

    base = ByteTokenizer()
    plugin = NoOpByteTokenizer()
    assert plugin.fingerprint == base.fingerprint
    text = "Hello world! Deterministic scoring check 123."
    assert plugin.encode(text) == base.encode(text)
    assert plugin.decode(plugin.encode(text)) == base.decode(base.encode(text))

    scorer_base = ConditionalLikelihoodScorer(model=_tiny_eval_model(), tokenizer=base)
    scorer_plugin = ConditionalLikelihoodScorer(model=scorer_base.model, tokenizer=plugin)
    first = scorer_base.score_continuation("The sky is", " blue")
    second = scorer_plugin.score_continuation("The sky is", " blue")
    assert first.log_likelihood == pytest.approx(second.log_likelihood)


def _tiny_eval_model() -> Any:
    pytest.importorskip("torch")
    from xlm.config.schemas import TransformerBaselineConfig
    from xlm.models.transformer import TransformerBaseline

    config = TransformerBaselineConfig(
        architecture="transformer_baseline",
        vocab_size=260,
        num_layers=1,
        hidden_size=32,
        num_attention_heads=2,
        intermediate_size=64,
        context_length=64,
        attention_backend="eager",
    )
    return TransformerBaseline(config, seed=2)


# ------------------------------------------------------- capability checks


def test_baseline_combination_passes_and_unsupported_ones_fail() -> None:

    ctx = ExecutionContext()
    ok = check_combination(
        model_caps=_caps("architecture"),
        objective_caps=_caps("objective"),
        optimizer_caps=_caps("optimizer"),
        tokenizer_caps=_caps("tokenizer"),
        context=ctx,
        total_parameters=100,
    )
    assert ok == []

    hidden = check_combination(
        objective_caps=_caps("objective", requires_hidden_states=True), context=ctx
    )
    assert any("hidden states" in v for v in hidden)

    closure = check_combination(
        optimizer_caps=_caps("optimizer", requires_closure=True), context=ctx
    )
    assert any("closures" in v for v in closure)

    nondeterministic = check_combination(
        tokenizer_caps=_caps("tokenizer", deterministic_scoring=False), context=ctx
    )
    assert any("deterministic" in v for v in nondeterministic)

    over_cap = check_combination(
        model_caps=_caps("architecture"),
        context=ExecutionContext(parameter_cap=10),
        total_parameters=11,
    )
    assert any("exceed the cap" in v for v in over_cap)


def _caps(category: str, **overrides: Any) -> PluginCapabilities:
    from xlm.research.capabilities import PluginCapabilities

    return PluginCapabilities(category=category, **overrides)


def test_objective_owned_state_survives_resume(tmp_path: Path) -> None:
    """Auxiliary objective parameters round-trip through checkpoints and continue."""
    pytest.importorskip("torch")
    from xlm.artifacts.ledger import RunLedger
    from xlm.artifacts.store import ArtifactStore
    from xlm.config.schemas import (
        AdamWConfig,
        TransformerBaselineConfig,
        WarmupCosineScheduleConfig,
    )
    from xlm.core.paths import ArtifactPaths
    from xlm.models.transformer import TransformerBaseline
    from xlm.objectives.auxiliary_fixture import AuxiliaryLearningObjective
    from xlm.optimizers.adamw import create_adamw_optimizer
    from xlm.schedules.cosine import WarmupCosineSchedule
    from xlm.training.checkpoint import CheckpointManager
    from xlm.training.data import TrainingBatcher
    from xlm.training.trainer import Trainer

    def build(home: Path, seed: int) -> tuple[Trainer, TrainingBatcher, CheckpointManager]:
        paths = ArtifactPaths(root=home)
        manager = CheckpointManager(
            artifact_store=ArtifactStore(paths),
            run_ledger=RunLedger(paths.ledger / "ledger.sqlite"),
            paths=paths,
        )
        config = TransformerBaselineConfig(
            architecture="transformer_baseline",
            vocab_size=32,
            num_layers=2,
            hidden_size=32,
            num_attention_heads=2,
            intermediate_size=64,
            context_length=8,
            attention_backend="eager",
        )
        model = TransformerBaseline(config, seed=seed)
        objective = AuxiliaryLearningObjective()
        optimizer, manifest = create_adamw_optimizer(
            AdamWConfig(lr=0.01, weight_decay=0.0), model=model, objective=objective
        )
        schedule = WarmupCosineSchedule(
            WarmupCosineScheduleConfig(
                warmup_valid_targets=16, horizon_valid_targets=64, min_lr_ratio=0.1
            ),
            base_lr=0.01,
        )
        batcher = TrainingBatcher(
            data_source=[((i % 28) + 4) for i in range(200)],
            context_length=8,
            global_batch_valid_targets=16,
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
            run_id="aux_run",
            plan_id="aux_plan",
            device="cpu",
            max_valid_targets=64,
        )
        return trainer, batcher, manager

    trainer, _, manager = build(tmp_path / "a", seed=5)
    for _ in range(2):
        assert trainer.train_step() is not None
    ckpt = trainer._save_checkpoint("aux_ckpt")

    resumed, resumed_batcher, resumed_manager = build(tmp_path / "b", seed=5)
    resumed_manager.load_checkpoint(
        ckpt,
        model=resumed.model,
        objective=resumed.objective,
        optimizer=resumed.optimizer,
        optimizer_manifest=resumed.optimizer_manifest,
        schedule=resumed.schedule,
        batcher=resumed_batcher,
        expected_plan_id="aux_plan",
        device="cpu",
    )
    resumed_aux = getattr(resumed.objective, "aux_param")  # noqa: B009
    trainer_aux = getattr(trainer.objective, "aux_param")  # noqa: B009
    assert resumed_aux.item() == pytest.approx(trainer_aux.item())
    resumed.step = trainer.step
    resumed.committed_valid_targets = trainer.committed_valid_targets
    resumed.processed_valid_targets = trainer.processed_valid_targets

    # An uninterrupted run's third step is the oracle for the resumed third step.
    reference, _, _ = build(tmp_path / "c", seed=5)
    reference_losses = []
    for _ in range(3):
        metrics = reference.train_step()
        assert metrics is not None
        reference_losses.append(metrics.loss)
    continued = resumed.train_step()
    assert continued is not None
    assert continued.loss == pytest.approx(reference_losses[2])


# ------------------------------------------------- scaffolds and guards


def test_scaffold_covers_all_categories_with_required_files(tmp_path: Path) -> None:
    for category in ("architecture", "objective", "optimizer", "tokenizer"):
        written = write_scaffold(f"demo_{category}", category, tmp_path / category)
        names = sorted(p.name for p in written)
        assert names == sorted(
            [
                "plugin.yaml",
                f"demo_{category}_plugin.py",
                f"test_demo_{category}_causal.py",
                f"test_demo_{category}_overfit.py",
                f"test_demo_{category}_disabled.py",
                f"comparison_demo_{category}.yaml",
            ]
        )
        manifest = load_manifest(tmp_path / category / f"demo_{category}")
        assert manifest.enabled is False
    with pytest.raises(ScaffoldError, match="unknown plugin category"):
        write_scaffold("demo_x", "alchemy", tmp_path)
    with pytest.raises(ScaffoldError, match="refusing to clobber"):
        write_scaffold("demo_architecture", "architecture", tmp_path / "architecture")
    with pytest.raises(ScaffoldError, match="unsafe plugin name"):
        write_scaffold("../escape", "objective", tmp_path)


def test_proposed_scaffold_cannot_pretend_to_run(tmp_path: Path) -> None:
    import importlib.util

    written = write_scaffold("proposed_opt", "optimizer", tmp_path)
    plugin_dir = tmp_path / "proposed_opt"
    assert {p.name for p in written} >= {"plugin.yaml", "proposed_opt_plugin.py"}
    with pytest.raises(PluginError, match="disabled"):
        load_plugin(plugin_dir, fresh_registries())
    # Even imported directly, the scaffold produces no outputs: instantiation,
    # accounting and serialization all fail explicitly, so no hidden teacher
    # data can be consumed and no token order can be altered.
    spec = importlib.util.spec_from_file_location(
        "proposed_opt_plugin", str(plugin_dir / "proposed_opt_plugin.py")
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with pytest.raises(NotImplementedError, match="implement the mechanism"):
        module.ProposedOptPlugin(object())
    with pytest.raises(NotImplementedError, match="no accounting"):
        module.accounting()
    with pytest.raises(NotImplementedError, match="no state"):
        module.serialize_state(object())
    # The disabled mechanism registers nothing and consumes no teacher data:
    # every registry stays empty and no module side effects execute.
    registries = fresh_registries()
    try:
        load_plugin(plugin_dir, registries)
    except PluginError:
        pass
    assert all(not r.list_entries() for r in registries.values())


def test_protected_evaluator_change_is_detected(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "sneaky_arch"
    plugin_dir.mkdir()
    (plugin_dir / "plugin.yaml").write_text(
        "manifest_version: '1'\n"
        "plugin_id: sneaky_arch\n"
        "version: '1'\n"
        "category: architecture\n"
        "enabled: true\n"
        "entry_module: mod.py\n"
        "files:\n"
        "  - mod.py\n"
        "  - src/xlm/evaluation/scorer.py\n",
        encoding="utf-8",
    )
    (plugin_dir / "mod.py").write_text("x = 1\n", encoding="utf-8")
    with pytest.raises(PluginError, match="protected surface"):
        load_plugin(plugin_dir, fresh_registries())


def test_undeclared_files_and_path_escapes_are_refused(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "sloppy"
    plugin_dir.mkdir()
    (plugin_dir / "plugin.yaml").write_text(
        "manifest_version: '1'\nplugin_id: sloppy\nversion: '1'\n"
        "category: objective\nenabled: true\nentry_module: mod.py\nfiles:\n  - mod.py\n",
        encoding="utf-8",
    )
    (plugin_dir / "mod.py").write_text("x = 1\n", encoding="utf-8")
    (plugin_dir / "extra_hidden.py").write_text("y = 2\n", encoding="utf-8")
    with pytest.raises(PluginError, match="undeclared files"):
        load_plugin(plugin_dir, fresh_registries())

    escape_dir = tmp_path / "escapee"
    escape_dir.mkdir()
    (escape_dir / "plugin.yaml").write_text(
        "manifest_version: '1'\nplugin_id: escapee\nversion: '1'\n"
        "category: objective\nenabled: true\nentry_module: mod.py\nfiles:\n  - ../outside.py\n",
        encoding="utf-8",
    )
    (escape_dir / "mod.py").write_text("x = 1\n", encoding="utf-8")
    with pytest.raises(PluginError, match="escapes the plugin directory"):
        load_plugin(escape_dir, fresh_registries())


def test_plugin_changes_alter_code_fingerprints(tmp_path: Path) -> None:
    from xlm.experiments.snapshot import capture_snapshot

    tree = tmp_path / "ws"
    (tree / "src" / "xlm" / "plugins").mkdir(parents=True, exist_ok=True)
    (tree / "src" / "xlm" / "plugins" / "base.py").write_text("x = 1\n", encoding="utf-8")
    (tree / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    (tree / "uv.lock").write_text("lock\n", encoding="utf-8")
    before = capture_snapshot(tree, tmp_path / "snap_a")
    (tree / "src" / "xlm" / "plugins" / "new_mechanism.py").write_text("y = 2\n", encoding="utf-8")
    after = capture_snapshot(tree, tmp_path / "snap_b")
    assert before.code_hash != after.code_hash


def test_discover_plugins_lists_manifest_dirs(tmp_path: Path) -> None:
    from xlm.research.loader import discover_plugins

    assert discover_plugins(tmp_path / "absent") == []
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    (tmp_path / "b" / "plugin.yaml").write_text("plugin_id: b\n", encoding="utf-8")
    assert [p.name for p in discover_plugins(tmp_path)] == ["b"]


# ------------------------------------------------------------------------ CLI


def _invoke(args: list[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Any:
    from typer.testing import CliRunner

    from xlm.cli.main import app

    monkeypatch.chdir(REPO_ROOT)
    return CliRunner().invoke(app, args)


def test_cli_idea_new_and_validate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    card_path = tmp_path / "idea.yaml"
    created = _invoke(
        [
            "research",
            "idea",
            "new",
            "--id",
            "demo",
            "--title",
            "Demo",
            "--category",
            "objective",
            "--output",
            str(card_path),
        ],
        monkeypatch,
        tmp_path,
    )
    assert created.exit_code == 0, created.output
    assert card_path.is_file()
    invalid = _invoke(["research", "idea", "validate", str(card_path)], monkeypatch, tmp_path)
    assert invalid.exit_code == 1
    assert "falsification_experiment" in invalid.output

    filled = complete_card()
    from xlm.research.ideas import save_card

    save_card(filled, card_path)
    valid = _invoke(["research", "idea", "validate", str(card_path)], monkeypatch, tmp_path)
    assert valid.exit_code == 0, valid.output
    assert "valid" in valid.output.lower()


def test_cli_scaffold_and_check_plugin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    scaffolded = _invoke(
        [
            "research",
            "scaffold",
            "--category",
            "optimizer",
            "--name",
            "demo_opt",
            "--output-dir",
            str(tmp_path / "scaffolds"),
        ],
        monkeypatch,
        tmp_path,
    )
    assert scaffolded.exit_code == 0, scaffolded.output
    assert "DISABLED" in scaffolded.output
    refused = _invoke(
        ["research", "check-plugin", str(tmp_path / "scaffolds" / "demo_opt")],
        monkeypatch,
        tmp_path,
    )
    assert refused.exit_code == 1
    assert "disabled" in refused.output.lower()

    checked = _invoke(
        ["research", "check-plugin", "src/xlm/plugins/noop_objective", "--json"],
        monkeypatch,
        tmp_path,
    )
    assert checked.exit_code == 0, checked.output
    import json as _json

    verdict = _json.loads(checked.output)
    assert verdict["registered_key"] == "noop_objective:1"
    assert "nonnovel" in verdict["novelty"]


def test_cli_scaffold_all_categories(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for category in ("architecture", "objective", "optimizer", "tokenizer"):
        result = _invoke(
            [
                "research",
                "scaffold",
                "--category",
                category,
                "--name",
                f"demo_{category}",
                "--output-dir",
                str(tmp_path / "all"),
            ],
            monkeypatch,
            tmp_path,
        )
        assert result.exit_code == 0, result.output
    assert len(list((tmp_path / "all").iterdir())) == 4


def test_idea_card_with_missing_falsification_fails_explicitly(tmp_path: Path) -> None:
    card = complete_card().model_copy(update={"falsification_experiment": "  "})
    problems = validate_card(card)
    assert problems == ["missing required field 'falsification_experiment'"]
    card_path = tmp_path / "card.yaml"
    save_card(complete_card(), card_path)
    assert validate_card_file(card_path) == []
    assert "not found" in validate_card_file(tmp_path / "absent.yaml")[0]
