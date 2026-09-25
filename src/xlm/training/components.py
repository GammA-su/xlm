"""Typed construction seam shared by direct, queued and resumed training."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from xlm.artifacts.manifest import identity_digest, validate_component
from xlm.core.registry import Registry, RegistryEntry

if TYPE_CHECKING:
    from torch.optim import Optimizer

    from xlm.models.base import BaseModel
    from xlm.objectives.base import BaseObjective
    from xlm.optimizers import ParameterGroupManifest
    from xlm.schedules.base import BaseSchedule
    from xlm.training.data import BatcherProtocol
    from xlm.training.science import ScientificState


def component_catalog(plugins: list[str] | None = None) -> dict[str, Registry[Any]]:
    """Load only explicitly selected, locally reviewed package plugins into local registries."""
    from xlm.config import schemas  # noqa: F401
    from xlm.research.loader import (
        default_registries,
        load_manifest,
        load_plugin,
        verify_file_scope,
    )

    catalog = {}
    for category, registry in default_registries().items():
        local: Registry[Any] = Registry(category)
        for entry in registry.list_entries():
            local.register(
                entry.identifier,
                entry.version,
                entry.config_schema,
                dict(entry.capabilities),
                entry.serializer_version,
                entry.factory,
            )
        catalog[category] = local
    if not isinstance(plugins or [], list) or len(plugins or []) > 16:
        raise ValueError("plugins must be a list of at most 16 reviewed package directory names")
    selected_plugins: set[tuple[str, str]] = set()
    for name in plugins or []:
        validate_component(name)
        directory = Path(__file__).resolve().parents[1] / "plugins" / name
        from xlm.experiments.execution import bounded_asset_hash

        bounded_asset_hash(directory)
        manifest = load_manifest(directory)
        plugin_key = (manifest.category, f"{manifest.plugin_id}:{manifest.version}")
        if plugin_key in selected_plugins:
            raise ValueError("duplicate selected plugin identity")
        selected_plugins.add(plugin_key)
        # Explicitly selected package plugins may supply a built-in control's
        # registration. Replace only in this run's local catalog, never globals.
        replacement: Registry[Any] = Registry(manifest.category)
        for entry in catalog[manifest.category].list_entries():
            if entry.key != plugin_key[1]:
                replacement.register(
                    entry.identifier,
                    entry.version,
                    entry.config_schema,
                    dict(entry.capabilities),
                    entry.serializer_version,
                    entry.factory,
                )
        catalog[manifest.category] = replacement
        verified = verify_file_scope(directory, manifest)
        if manifest.entry_module not in verified:
            raise ValueError("plugin entry module must be declared in its verified file scope")
        result = load_plugin(directory, catalog)
        if result.registered_key != f"{manifest.plugin_id}:{manifest.version}":
            raise ValueError("plugin registration differs from declared component identity")
        entry = catalog[manifest.category].get(manifest.plugin_id, manifest.version)
        entry.capabilities["plugin_capabilities"] = manifest.parsed_capabilities().to_dict()
    return catalog


def validate_component_selection(config: dict[str, Any], catalog: dict[str, Registry[Any]]) -> None:
    """Apply the existing capability checker before construction; fail unsupported seams."""
    from xlm.research.capabilities import PluginCapabilities, check_combination

    selections = {
        "model": ("architecture", config["model"]),
        "objective": ("objective", config["objective"]),
        "optimizer": ("optimizer", config["optimizer"]),
    }
    if config["data"].get("tokenizer"):
        selections["tokenizer"] = ("tokenizer", config["data"]["tokenizer"])
    caps = {}
    for key, (category, raw) in selections.items():
        entry = selected_entry(catalog, category, raw)
        fields = entry.capabilities.get("plugin_capabilities", entry.capabilities)
        caps[key + "_caps"] = PluginCapabilities.from_dict(
            {
                **{k: v for k, v in fields.items() if k in PluginCapabilities.__dataclass_fields__},
                "category": category,
            }
        )
    violations = check_combination(
        model_caps=caps["model_caps"],
        objective_caps=caps["objective_caps"],
        optimizer_caps=caps["optimizer_caps"],
        tokenizer_caps=caps.get("tokenizer_caps"),
    )
    if caps["model_caps"].has_recurrent_state:
        violations.append("this trainer does not implement recurrent-state continuation")
    if (
        caps["objective_caps"].loss_protocol != "token_additive"
        or not caps["objective_caps"].supports_microbatching
    ):
        violations.append("this trainer requires token-additive microbatch accumulation")
    if violations:
        raise ValueError("unsupported component combination: " + "; ".join(violations))


def selected_entry(
    catalog: dict[str, Registry[Any]], category: str, raw: dict[str, Any]
) -> RegistryEntry[Any]:
    selectors = {
        "architecture": ("architecture", "transformer_baseline"),
        "objective": ("type", "cross_entropy"),
        "optimizer": ("type", "adamw"),
        "schedule": ("type", "warmup_cosine"),
        "tokenizer": ("type", "byte_fixture"),
    }
    selector, default = selectors[category]
    version = (
        raw.get("architecture_version", "1")
        if category == "architecture"
        else raw.get("version", "1")
    )
    entry = catalog[category].get(str(raw.get(selector, default)), str(version))
    if entry.factory is None and category != "tokenizer":
        raise ValueError(f"selected {category} {entry.key} has no executable factory")
    return entry


@dataclass
class TrainingComponents:
    model: BaseModel
    objective: BaseObjective
    optimizer: Optimizer
    optimizer_manifest: ParameterGroupManifest
    schedule: BaseSchedule
    batcher: BatcherProtocol
    tokenizer: Any
    data_identity: str
    construction: dict[str, Any]
    science: ScientificState


def construct_training_components(
    config: dict[str, Any], *, device: str, fresh_training_rng: bool = True
) -> TrainingComponents:
    """Build registered components once; the existing Trainer owns all updates.

    ``init_seed`` seeds construction. Legacy runs then train on whatever global
    RNG state construction left behind (historical coupling). Science-v1 runs
    reseed every training generator from ``training_seed`` only after all
    components exist, unless ``fresh_training_rng`` is false because a
    checkpoint restore will supply the authoritative RNG state.
    """
    import random

    import torch

    from xlm.config.science import ScientificPolicy
    from xlm.core.paths import ArtifactPaths
    from xlm.training.inputs import (
        build_training_batcher,
        resolve_training_input,
        resolve_training_tokenizer,
    )
    from xlm.training.science import ScientificState, reseed_training_rng

    catalog = component_catalog(config.get("plugins"))
    validate_component_selection(config, catalog)
    training = config["training"]
    random.seed(training["init_seed"])
    torch.manual_seed(training["init_seed"])
    try:
        import numpy as np
    except ImportError:
        pass
    else:
        np.random.seed(training["init_seed"] % 2**32)
    entries = {
        category: selected_entry(catalog, category, raw)
        for category, raw in (
            ("architecture", config["model"]),
            ("objective", config["objective"]),
            ("optimizer", config["optimizer"]),
            ("schedule", training["schedule"]),
        )
    }

    def create(category: str, raw: dict[str, Any], **kwargs: Any) -> Any:
        entry = entries[category]
        assert entry.factory is not None
        return entry.factory(entry.config_schema.model_validate(raw), **kwargs)

    model = create("architecture", config["model"]).to(device)
    objective = create("objective", config["objective"]).to(device)
    optimizer, manifest = create("optimizer", config["optimizer"], model=model, objective=objective)
    if not hasattr(objective, "capabilities") or not objective.capabilities.supports_microbatching:
        raise ValueError("selected objective does not support the training accumulation protocol")
    if "lr" not in config["optimizer"]:
        raise ValueError("selected optimizer must declare an explicit schedule base learning rate")
    schedule = create("schedule", training["schedule"], base_lr=float(config["optimizer"]["lr"]))
    source, data_identity = resolve_training_input(config["data"], ArtifactPaths.from_env())
    tokenizer, _ = resolve_training_tokenizer(config["data"], catalog)
    batcher = build_training_batcher(source, config["data"], training, tokenizer)
    construction: dict[str, Any] = {
        category: {"key": entry.key, "serializer": entry.serializer_version}
        for category, entry in entries.items()
    }
    construction["identity"] = identity_digest(construction)
    science = ScientificState(ScientificPolicy.from_training(training))
    if science.policy.is_science and fresh_training_rng:
        assert science.policy.training_seed is not None
        science.train_start_rng = reseed_training_rng(science.policy.training_seed, device)
    return TrainingComponents(
        model,
        objective,
        optimizer,
        manifest,
        schedule,
        batcher,
        tokenizer,
        data_identity,
        construction,
        science,
    )


def inspect_model_shape(config: dict[str, Any]) -> int:
    """Count actual unique deployed parameters on meta without numerical allocation."""
    import torch

    catalog = component_catalog(config.get("plugins"))
    entry = selected_entry(catalog, "architecture", config["model"])
    assert entry.factory is not None
    with torch.device("meta"):
        model = entry.factory(entry.config_schema.model_validate(config["model"]))
    if any(parameter.device.type != "meta" for parameter in model.parameters()):
        raise ValueError("architecture does not honor meta shape inspection")
    return sum(parameter.numel() for parameter in model.parameters())
