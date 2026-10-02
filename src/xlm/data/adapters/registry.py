"""Adapter registry of the Mix-01 source pipeline, and each adapter's code modules.

:mod:`xlm.data.adapters.mix01_adapters` and its ``ADAPTERS_BY_ID`` are frozen:
every certified bridge and the Essential-Web campaign bind that file's bytes.
A corrected adapter contract therefore lives in its own module and replaces
the frozen entry of its adapter id here. Production adaptation, bridge
certification and ``xlm data adapt`` all resolve adapters through this
registry; the frozen Essential-Web paths keep the frozen registry, whose
Essential-Web entries are the same classes.

:func:`adapter_code_modules` names the sources an adapter's behavior comes
from, which the bridge binds by SHA-256 as that adapter's code identity: the
frozen adapter and column-contract modules, plus the module of every ``xlm``
class in the registered class's hierarchy. A frozen class adds nothing, so
its identity is unchanged; a versioned class adds its own module, so its
adapter's evidence and admission must be renewed and a plan made under the
old identity cannot run.
"""

from __future__ import annotations

import sys
from types import ModuleType
from typing import Any

from xlm.data.adapters import columns, ifm_adapters, mix01_adapters

ADAPTERS_BY_ID: dict[str, Any] = {
    **mix01_adapters.ADAPTERS_BY_ID,
    **ifm_adapters.ADAPTERS_BY_ID,
}


def adapter_code_modules(adapter_id: str) -> tuple[ModuleType, ...]:
    """The modules whose source defines ``adapter_id``'s behavior, frozen ones first."""
    if adapter_id not in ADAPTERS_BY_ID:
        raise KeyError(f"adapter '{adapter_id}' is not registered")
    modules: list[ModuleType] = [mix01_adapters, columns]
    for cls in type.mro(ADAPTERS_BY_ID[adapter_id]):
        module = sys.modules[cls.__module__]
        if module.__name__.startswith("xlm.") and module not in modules:
            modules.append(module)
    return tuple(modules)
