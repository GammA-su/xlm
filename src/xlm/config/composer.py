"""Strict YAML loading, composition, preset expansion, and canonical configuration hashing."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

import yaml

# Allowlist of environment variables permitted for configuration interpolation
ALLOWED_ENV_VARS = {"XLM_HOME", "TMPDIR", "TEMP", "TMP"}


class UniqueSafeLoader(yaml.SafeLoader):
    """YAML loader that rejects duplicate keys and unsafe constructs."""

    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict[Any, Any]:
        mapping: dict[Any, Any] = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, (str, int, float, bool)):
                raise ValueError(f"Non-scalar YAML key '{key}' is forbidden")
            if key in mapping:
                raise ValueError(
                    f"Duplicate YAML key detected: '{key}' at line {node.start_mark.line + 1}"
                )
            mapping[key] = self.construct_object(value_node, deep=deep)
        return mapping


def load_yaml_str(content: str) -> dict[str, Any]:
    """Parse YAML content rejecting duplicate keys and non-mapping roots."""
    parsed = yaml.load(content, Loader=UniqueSafeLoader)
    if parsed is None:
        return {}
    if not isinstance(parsed, dict):
        raise ValueError(
            f"Configuration root must be a dictionary/mapping, got {type(parsed).__name__}"
        )
    return parsed


def load_yaml_file(path: Path) -> dict[str, Any]:
    """Load and parse YAML file from filesystem."""
    if not path.is_file():
        raise FileNotFoundError(f"Configuration file not found: {path}")
    content = path.read_text(encoding="utf-8")
    return load_yaml_str(content)


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Deep merge two dictionaries: mappings merge recursively; lists in override replace base."""
    merged = copy.deepcopy(base)
    for key, value in override.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            # Check for component type changes (e.g. changing architecture or optimizer type)
            base_type = merged[key].get("architecture") or merged[key].get("type")
            override_type = value.get("architecture") or value.get("type")
            if base_type and override_type and base_type != override_type:
                # Component type changed: do not retain old component fields
                merged[key] = copy.deepcopy(value)
            else:
                merged[key] = deep_merge(merged[key], value)
        else:
            # Lists and primitives replace entirely
            merged[key] = copy.deepcopy(value)
    return merged


def interpolate_env_vars(obj: Any) -> Any:
    """Interpolate environment variables for allowed variables, rejecting secret leakage."""
    if isinstance(obj, dict):
        return {k: interpolate_env_vars(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [interpolate_env_vars(v) for v in obj]
    if isinstance(obj, str):
        # Match ${VAR_NAME}
        matches = re.findall(r"\$\{([A-Za-z0-9_]+)\}", obj)
        for var in matches:
            if var not in ALLOWED_ENV_VARS:
                raise ValueError(
                    f"Environment variable '${{{var}}}' is not in the approved allowlist: "
                    f"{sorted(ALLOWED_ENV_VARS)}"
                )
            val = os.environ.get(var, "")
            obj = obj.replace(f"${{{var}}}", val)
        return obj
    return obj


def apply_cli_override(config: dict[str, Any], override_str: str) -> None:
    """Apply a typed key=value override to a configuration dictionary.

    Example override_str: 'training.budget.max_valid_targets=128000'
    """
    if "=" not in override_str:
        raise ValueError(
            f"Invalid override '{override_str}'. Must be formatted as 'path.to.key=value'"
        )

    path_str, raw_val = override_str.split("=", 1)
    keys = path_str.strip().split(".")
    if not keys or any(not k for k in keys):
        raise ValueError(f"Invalid override path: '{path_str}'")

    # Type inference for raw value
    parsed_val: Any = raw_val.strip()
    if parsed_val.lower() == "true":
        parsed_val = True
    elif parsed_val.lower() == "false":
        parsed_val = False
    elif parsed_val.lower() in ("null", "none"):
        parsed_val = None
    else:
        try:
            parsed_val = int(parsed_val)
        except ValueError:
            try:
                parsed_val = float(parsed_val)
            except ValueError:
                pass  # Keep as string

    curr = config
    for k in keys[:-1]:
        if k not in curr or not isinstance(curr[k], dict):
            curr[k] = {}
        curr = curr[k]
    curr[keys[-1]] = parsed_val


class ConfigComposer:
    """Orchestrates configuration inheritance, preset lookup, and validation."""

    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = workspace_root.resolve()
        self.models_dir = self.workspace_root / "recipes" / "models"
        self.mixtures_dir = self.workspace_root / "recipes" / "mixtures"

    def load_model_preset(self, preset_name: str) -> dict[str, Any]:
        """Load a model preset from recipes/models/<preset>.yaml."""
        preset_file = self.models_dir / f"{preset_name}.yaml"
        if not preset_file.exists():
            raise FileNotFoundError(f"Model preset '{preset_name}' not found at {preset_file}")
        data = load_yaml_file(preset_file)
        # Strip cosmetic preset envelope fields when inlined
        data.pop("schema_version", None)
        data.pop("kind", None)
        data.pop("id", None)
        return data

    def load_mixture_preset(self, mixture_name: str) -> dict[str, Any]:
        """Load a mixture preset from recipes/mixtures/<mixture>.yaml."""
        preset_file = self.mixtures_dir / f"{mixture_name}.yaml"
        if not preset_file.exists():
            raise FileNotFoundError(f"Mixture preset '{mixture_name}' not found at {preset_file}")
        data = load_yaml_file(preset_file)
        return data

    def compose(
        self,
        config_path: Path,
        overrides: list[str] | None = None,
        seen_paths: set[Path] | None = None,
        depth: int = 0,
    ) -> dict[str, Any]:
        """Recursively compose configuration honoring extends, presets, and overrides."""
        if depth > 10:
            raise ValueError(
                f"Exceeded maximum inheritance depth of 10 while resolving {config_path}"
            )

        resolved_path = config_path.resolve()
        if seen_paths is None:
            seen_paths = set()

        if resolved_path in seen_paths:
            raise ValueError(
                f"Cyclic inheritance detected: '{resolved_path}' has already been visited"
            )

        seen_paths.add(resolved_path)
        raw_config = load_yaml_file(resolved_path)

        # 1. Base definition from 'extends'
        base_config: dict[str, Any] = {}
        if "extends" in raw_config:
            parent_rel = raw_config.pop("extends")
            parent_path = (resolved_path.parent / parent_rel).resolve()
            base_config = self.compose(
                parent_path,
                overrides=None,
                seen_paths=copy.copy(seen_paths),
                depth=depth + 1,
            )

        # Merge raw_config over base_config
        merged = deep_merge(base_config, raw_config)

        # 2. Preset expansion
        # Model preset
        if isinstance(merged.get("model"), dict) and "preset" in merged["model"]:
            preset_name = merged["model"].pop("preset")
            preset_data = self.load_model_preset(preset_name)
            # Preset values merged with explicit model fields overriding preset
            merged["model"] = deep_merge(preset_data, merged["model"])

        # Mixture preset
        if isinstance(merged.get("data"), dict) and "mixture_preset" in merged["data"]:
            mixture_name = merged["data"].get("mixture_preset")
            if mixture_name:
                mixture_data = self.load_mixture_preset(mixture_name)
                # Keep mixture preset metadata or inline weights if needed
                merged["data"]["mixture_details"] = mixture_data

        # 3. Environment variable interpolation
        interpolated = interpolate_env_vars(merged)
        if not isinstance(interpolated, dict):
            raise TypeError("Composed configuration must be a mapping")
        result: dict[str, Any] = interpolated

        # 4. CLI overrides (applied only at root depth)
        if depth == 0 and overrides:
            for ov in overrides:
                apply_cli_override(result, ov)

        return result


def canonicalize_for_hash(obj: Any) -> Any:
    """Recursively strip non-behavioral fields and sort keys for stable hashing."""
    NON_BEHAVIORAL_KEYS = {
        "description",
        "notes",
        "name",
        "id",
        "status",
        "authorization",
        "plan_hash",
        "created_at",
        "updated_at",
    }
    if isinstance(obj, dict):
        return {
            k: canonicalize_for_hash(v)
            for k, v in sorted(obj.items())
            if k not in NON_BEHAVIORAL_KEYS
        }
    if isinstance(obj, list):
        return [canonicalize_for_hash(item) for item in obj]
    if isinstance(obj, float):
        # Format floats stably to 8 decimal places
        return round(obj, 8)
    return obj


def compute_plan_hash(config_dict: dict[str, Any]) -> str:
    """Compute deterministic SHA-256 canonical hash of behavioral configuration."""
    canonical = canonicalize_for_hash(config_dict)
    serialized = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def diff_configs(c1: dict[str, Any], c2: dict[str, Any], path: str = "") -> list[str]:
    """Compute list of human-readable differences between two configurations."""
    diffs: list[str] = []
    all_keys = sorted(set(c1.keys()) | set(c2.keys()))
    for k in all_keys:
        curr_path = f"{path}.{k}" if path else k
        if k not in c1:
            diffs.append(f"+ {curr_path}: {c2[k]}")
        elif k not in c2:
            diffs.append(f"- {curr_path}: {c1[k]}")
        elif isinstance(c1[k], dict) and isinstance(c2[k], dict):
            diffs.extend(diff_configs(c1[k], c2[k], curr_path))
        elif c1[k] != c2[k]:
            diffs.append(f"~ {curr_path}: {c1[k]} -> {c2[k]}")
    return diffs
