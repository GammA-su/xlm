"""Proposed-plugin scaffold generator (P18, A33).

Generates, per category: typed config, registry wiring, parameter/state/
resource accounting stubs, serialization hooks, a causal/no-future test
template, a tiny-overfit test template, a disabled-mode test and a comparison
recipe. Generated scaffolds are disabled by default and fail explicitly when
loaded or instantiated; they are never registered as functioning mechanisms.
"""

from __future__ import annotations

from pathlib import Path

from xlm.research.loader import PLUGIN_CATEGORIES

SCAFFOLD_VERSION = "1"


class ScaffoldError(ValueError):
    """Raised for unsupported categories or unsafe output targets."""


_CATEGORY_IMPORTS = {
    "architecture": "from xlm.models.base import BaseModel",
    "objective": "from xlm.objectives.base import BaseObjective, ObjectiveCapabilities",
    "optimizer": "import torch",
    "tokenizer": "from xlm.tokenizers.base import BaseTokenizer",
}

_CATEGORY_CLASS_SKETCH = {
    "architecture": '''class {cls}(BaseModel):
    """PROPOSED scaffold for {name}: not implemented, must fail explicitly."""

    def __init__(self, config: {cfg}) -> None:
        raise NotImplementedError(
            "proposed scaffold '{name}': implement the mechanism before instantiating"
        )''',
    "objective": '''class {cls}(BaseObjective):
    """PROPOSED scaffold for {name}: not implemented, must fail explicitly."""

    @property
    def capabilities(self) -> ObjectiveCapabilities:
        raise NotImplementedError(
            "proposed scaffold '{name}': declare capabilities when implementing"
        )

    def forward(self, model_output: Any, batch: Any) -> Any:
        raise NotImplementedError(
            "proposed scaffold '{name}': implement the mechanism before instantiating"
        )''',
    "optimizer": '''class {cls}(torch.optim.Optimizer):
    """PROPOSED scaffold for {name}: not implemented, must fail explicitly."""

    def __init__(self, params: Any, lr: float = 0.001) -> None:
        raise NotImplementedError(
            "proposed scaffold '{name}': implement the mechanism before instantiating"
        )''',
    "tokenizer": '''class {cls}(BaseTokenizer):
    """PROPOSED scaffold for {name}: not implemented, must fail explicitly."""

    def __init__(self) -> None:
        raise NotImplementedError(
            "proposed scaffold '{name}': implement the mechanism before instantiating"
        )''',
}


def _class_name(plugin_name: str, suffix: str) -> str:
    parts = [p for p in plugin_name.replace("-", "_").split("_") if p]
    return "".join(p[:1].upper() + p[1:] for p in parts) + suffix


def _config_class_name(plugin_name: str) -> str:
    return _class_name(plugin_name, "Config")


def scaffold_files(plugin_name: str, category: str) -> dict[str, str]:
    """Render every scaffold file for a category. Pure function, easy to test."""
    if category not in PLUGIN_CATEGORIES:
        raise ScaffoldError(
            f"unknown plugin category '{category}'; expected one of {PLUGIN_CATEGORIES}"
        )
    cls = _class_name(plugin_name, "Plugin")
    cfg = _config_class_name(plugin_name)

    manifest = f"""# PROPOSED scaffold for '{plugin_name}' ({category}).
# Disabled by default: loading this plugin must fail explicitly until the
# mechanism is implemented. It is not a functioning mechanism.
manifest_version: "1"
plugin_id: {plugin_name}
version: "0-proposed"
category: {category}
enabled: false
entry_module: {plugin_name}_plugin.py
register_function: register
files:
  - {plugin_name}_plugin.py
capabilities: {{}}
parameter_accounting:
  parameters: unimplemented
  state: unimplemented
  extra_compute_per_step: unimplemented
novelty: proposed scaffold, no novelty claim
"""

    module = f'''"""PROPOSED scaffold for '{plugin_name}' ({category}). Not implemented."""

from __future__ import annotations

from typing import Any

from xlm.config.schemas import StrictConfigModel
from xlm.core.registry import Registry
{_CATEGORY_IMPORTS[category]}


class {cfg}(StrictConfigModel):
    """Typed config for the proposed '{plugin_name}' mechanism."""

    type: str = "{plugin_name}"
    version: str = "0-proposed"


{_CATEGORY_CLASS_SKETCH[category].format(cls=cls, cfg=cfg, name=plugin_name)}


def accounting() -> dict[str, Any]:
    """Parameter/state/resource accounting. Unimplemented by definition."""
    raise NotImplementedError(
        "proposed scaffold '{plugin_name}': no accounting exists before implementation"
    )


def serialize_state(component: Any) -> dict[str, Any]:
    """Serialization hook. Unimplemented by definition."""
    raise NotImplementedError(
        "proposed scaffold '{plugin_name}': no state exists before implementation"
    )


def register(registry: Registry[Any], capabilities: Any) -> Any:
    """Registry wiring. The loader refuses disabled plugins before reaching here."""
    raise NotImplementedError(
        "proposed scaffold '{plugin_name}' is disabled: implement the mechanism first"
    )
'''

    causal_test = f'''"""Causality template for '{plugin_name}': no future information may leak."""

from __future__ import annotations

import pytest


def test_{plugin_name}_causal_no_future_leak() -> None:
    """Fill in: changing future inputs must not change earlier outputs."""
    pytest.skip("proposed scaffold '{plugin_name}': implement the causality test")
'''

    overfit_test = f'''"""Tiny-overfit template for '{plugin_name}'."""

from __future__ import annotations

import pytest


def test_{plugin_name}_tiny_overfit() -> None:
    """Fill in: the mechanism must overfit a tiny fixed dataset when enabled."""
    pytest.skip("proposed scaffold '{plugin_name}': implement the overfit test")
'''

    disabled_test = f'''"""Disabled-mode test for '{plugin_name}'."""

from __future__ import annotations

from pathlib import Path

import pytest

from xlm.research.loader import PluginError, load_plugin


def test_{plugin_name}_disabled_refuses_to_load(tmp_path: Path) -> None:
    """A proposed scaffold fails explicitly instead of registering."""
    with pytest.raises(PluginError, match="disabled"):
        load_plugin(Path(__file__).parent)
'''

    comparison_recipe = f"""# Comparison recipe skeleton for '{plugin_name}'.
# The candidate differs from the baseline ONLY in the declared research variable.
schema_version: 1
kind: comparison_recipe
id: compare_{plugin_name}_vs_baseline
baseline: <baseline run id>
candidate: <candidate run id with '{plugin_name}' enabled>
track: <architecture|objective|optimizer|tokenizer|data-mixture>
allowed_differences: [{plugin_name}]
notes:
  - Fill in the frozen comparison contract before running.
  - A no-op control is not evidence of novelty.
"""

    base = plugin_name
    return {
        "plugin.yaml": manifest,
        f"{base}_plugin.py": module,
        f"test_{base}_causal.py": causal_test,
        f"test_{base}_overfit.py": overfit_test,
        f"test_{base}_disabled.py": disabled_test,
        f"comparison_{base}.yaml": comparison_recipe,
    }


def write_scaffold(plugin_name: str, category: str, output_dir: Path | str) -> list[Path]:
    """Write a disabled-by-default scaffold. Refuses clobbering and escapes."""
    if not plugin_name or "/" in plugin_name or "\\" in plugin_name or plugin_name in (".", ".."):
        raise ScaffoldError(f"unsafe plugin name '{plugin_name}'")
    out = Path(output_dir) / plugin_name
    if out.exists():
        raise ScaffoldError(f"scaffold target '{out}' already exists; refusing to clobber")
    out.mkdir(parents=True, exist_ok=False)
    written: list[Path] = []
    for filename, content in scaffold_files(plugin_name, category).items():
        target = out / filename
        target.write_text(content, encoding="utf-8")
        written.append(target)
    return sorted(written)
