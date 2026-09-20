"""Plugin discovery, verification and registration without touching core code (P18).

A plugin is a directory with a ``plugin.yaml`` manifest plus its declared
files. Loading verifies, in order: manifest schema, file scope (declared files
exist inside the plugin directory, nothing undeclared executes), protected
surfaces (evaluation code, sealed manifests, baseline settings, trainer and
scorer are never in scope), enabled flag (proposed scaffolds fail explicitly
instead of registering as functioning mechanisms), capabilities, and finally
registration into caller-supplied registries. Research plugins must not modify
evaluation code, sealed manifests or baseline settings; the file-scope diff
proves it for every load.
"""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import Field

from xlm.config.schemas import StrictConfigModel
from xlm.core.registry import Registry
from xlm.research.capabilities import PluginCapabilities

PLUGIN_MANIFEST_VERSION = "1"

PLUGIN_CATEGORIES = ("architecture", "objective", "optimizer", "tokenizer")

# Paths a plugin may never claim, add or modify. Evaluation code, sealed
# manifests, baseline reference settings, and the generic trainer/scorer that
# plugins are forbidden to edit all live under these prefixes.
PROTECTED_PREFIXES = (
    "src/xlm/evaluation/",
    "src/xlm/training/trainer.py",
    "src/xlm/training/checkpoint.py",
    "src/xlm/evaluation",  # without trailing slash, for exact-dir references
    "recipes/models/",
    "recipes/experiments/baseline_",
    "manifests/sealed",
    "manifests/datasets.catalog.yaml",
)


class PluginError(RuntimeError):
    """Raised when a plugin cannot be verified or registered safely."""


class PluginManifest(StrictConfigModel):
    """The plugin.yaml contract every plugin directory carries."""

    manifest_version: str = Field(default=PLUGIN_MANIFEST_VERSION)
    plugin_id: str = Field(min_length=1)
    version: str = Field(default="1")
    category: str = Field(min_length=1)
    enabled: bool = Field(default=False)
    entry_module: str = Field(min_length=1)
    register_function: str = Field(default="register")
    files: list[str] = Field(default_factory=list)
    capabilities: dict[str, Any] = Field(default_factory=dict)
    parameter_accounting: dict[str, Any] = Field(default_factory=dict)
    novelty: str = Field(default="")

    def parsed_capabilities(self) -> PluginCapabilities:
        try:
            return PluginCapabilities(category=self.category, **self.capabilities)
        except TypeError as exc:
            raise PluginError(
                f"plugin '{self.plugin_id}' declares invalid capabilities: {exc}"
            ) from exc


def load_manifest(plugin_dir: Path | str) -> PluginManifest:
    """Load and strictly validate a plugin manifest."""
    directory = Path(plugin_dir)
    manifest_path = directory / "plugin.yaml"
    if not manifest_path.is_file():
        raise PluginError(f"no plugin.yaml in '{directory}'")
    data = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise PluginError(f"plugin manifest at '{manifest_path}' must be a mapping")
    try:
        manifest = PluginManifest.model_validate(data)
    except Exception as exc:
        raise PluginError(f"plugin manifest failed validation: {exc}") from exc
    if manifest.category not in PLUGIN_CATEGORIES:
        raise PluginError(
            f"plugin '{manifest.plugin_id}' has unknown category '{manifest.category}'; "
            f"expected one of {PLUGIN_CATEGORIES}"
        )
    return manifest


def verify_file_scope(plugin_dir: Path | str, manifest: PluginManifest) -> list[str]:
    """Diff declared files against the directory; refuse escapes and protected paths.

    Returns the sorted list of verified relative paths. Every refusal names the
    offending entry and the rule it breaks.
    """
    directory = Path(plugin_dir)
    problems: list[str] = []
    verified: list[str] = []

    if not manifest.files:
        raise PluginError(f"plugin '{manifest.plugin_id}' declares no files")

    for entry in manifest.files:
        normalized = entry.replace("\\", "/").lstrip("/")
        if not normalized or normalized in (".", "./"):
            problems.append(f"entry '{entry}' is empty")
            continue
        if normalized.startswith("..") or "/../" in normalized or normalized == "..":
            problems.append(f"entry '{entry}' escapes the plugin directory")
            continue
        for prefix in PROTECTED_PREFIXES:
            if normalized == prefix.rstrip("/") or normalized.startswith(prefix):
                problems.append(
                    f"entry '{entry}' touches protected surface '{prefix}'; "
                    "plugins must not modify evaluation code, sealed manifests, "
                    "baseline settings, trainer or scorer"
                )
                break
        else:
            candidate = directory / normalized
            if not candidate.is_file():
                problems.append(f"declared file '{entry}' is missing from '{directory}'")
                continue
            verified.append(normalized)

    if problems:
        raise PluginError(
            f"plugin '{manifest.plugin_id}' file-scope check failed: " + "; ".join(problems)
        )

    actual = sorted(
        p.relative_to(directory).as_posix()
        for p in directory.rglob("*")
        if p.is_file() and p.name != "plugin.yaml" and "__pycache__" not in p.parts
    )
    undeclared = sorted(set(actual) - set(verified))
    if undeclared:
        raise PluginError(
            f"plugin '{manifest.plugin_id}' ships undeclared files: "
            + ", ".join(undeclared[:8])
            + ("..." if len(undeclared) > 8 else "")
            + "; declare every executed file or remove it"
        )
    return sorted(verified)


def check_enabled(manifest: PluginManifest) -> None:
    """Refuse disabled (proposed, unimplemented) plugins with an explicit error."""
    if not manifest.enabled:
        raise PluginError(
            f"plugin '{manifest.plugin_id}' is disabled (proposed scaffold, not an "
            "implemented mechanism); implement it before loading"
        )


def import_plugin_module(plugin_dir: Path | str, manifest: PluginManifest) -> Any:
    """Import the plugin entry module under an isolated module name."""
    module_path = Path(plugin_dir) / manifest.entry_module
    if not module_path.is_file():
        raise PluginError(
            f"plugin '{manifest.plugin_id}' entry module '{manifest.entry_module}' missing"
        )
    module_name = f"xlm_plugin_{manifest.plugin_id}_{manifest.version}".replace("-", "_")
    spec = importlib.util.spec_from_file_location(module_name, str(module_path))
    if spec is None or spec.loader is None:
        raise PluginError(f"cannot import plugin module '{module_path}'")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        del sys.modules[module_name]
        raise PluginError(f"plugin '{manifest.plugin_id}' failed to import: {exc}") from exc
    return module


def default_registries() -> dict[str, Registry[Any]]:
    """The global registries plugins register into (imported lazily)."""
    from xlm.core import registry as global_registry

    return {
        "architecture": global_registry.architectures,
        "objective": global_registry.objectives,
        "optimizer": global_registry.optimizers,
        "tokenizer": global_registry.tokenizers,
        "source_adapter": global_registry.source_adapters,
        "transform": global_registry.transforms,
        "schedule": global_registry.schedules,
    }


@dataclass
class PluginLoadResult:
    """Outcome of loading one plugin directory."""

    plugin_id: str
    version: str
    category: str
    registered_key: str
    files: list[str] = field(default_factory=list)
    novelty: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_plugin(
    plugin_dir: Path | str,
    registries: Mapping[str, Registry[Any]] | None = None,
) -> PluginLoadResult:
    """Verify and register one plugin. Core trainer/scorer code is untouched."""
    directory = Path(plugin_dir)
    manifest = load_manifest(directory)
    check_enabled(manifest)
    verified = verify_file_scope(directory, manifest)
    capabilities = manifest.parsed_capabilities()

    module = import_plugin_module(directory, manifest)
    register_fn = getattr(module, manifest.register_function, None)
    if not callable(register_fn):
        raise PluginError(
            f"plugin '{manifest.plugin_id}' has no callable '{manifest.register_function}'"
        )
    targets = registries if registries is not None else default_registries()
    if manifest.category not in targets:
        raise PluginError(f"no registry for category '{manifest.category}'")
    entry = register_fn(targets[manifest.category], capabilities)
    key = entry.key if hasattr(entry, "key") else f"{manifest.plugin_id}:{manifest.version}"
    return PluginLoadResult(
        plugin_id=manifest.plugin_id,
        version=manifest.version,
        category=manifest.category,
        registered_key=key,
        files=verified,
        novelty=manifest.novelty,
    )


def discover_plugins(plugins_root: Path | str) -> list[Path]:
    """List plugin directories (those carrying a plugin.yaml), sorted by name."""
    root = Path(plugins_root)
    if not root.is_dir():
        return []
    return sorted(p for p in root.iterdir() if p.is_dir() and (p / "plugin.yaml").is_file())
