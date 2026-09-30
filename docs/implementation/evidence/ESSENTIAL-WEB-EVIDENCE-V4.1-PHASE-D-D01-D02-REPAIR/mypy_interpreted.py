"""Run the installed mypy from its bundled pure-Python sources (no compiled mypyc modules).

On 2026-09-30 Windows application control refused to load mypy's compiled
``.pyd`` modules on this machine ("DLL load failed while importing mypy").
Nothing is unblocked, installed or changed: inside this one process, imports
of the ``mypy`` package resolve to the ``.py`` sources shipped in the same
locked wheel instead of the extension modules. Arguments are mypy's own.
"""

from __future__ import annotations

import importlib.machinery as machinery
import importlib.util
import os
import sys
from typing import Any

SOURCE_ONLY = machinery.FileFinder.path_hook(
    (machinery.SourceFileLoader, machinery.SOURCE_SUFFIXES),
)


def hook(path: str) -> Any:
    parts = os.path.normcase(path).split(os.sep)
    if "site-packages" in parts and parts[parts.index("site-packages") + 1 :][:1] == ["mypy"]:
        return SOURCE_ONLY(path)
    raise ImportError


class TopLevel:
    """Resolve the top-level ``mypy`` package itself from its source ``__init__.py``."""

    @staticmethod
    def find_spec(name: str, path: Any = None, target: Any = None) -> Any:
        if name != "mypy":
            return None
        for entry in sys.path:
            init = os.path.join(entry, "mypy", "__init__.py")
            if os.path.isfile(init):
                return importlib.util.spec_from_file_location(
                    "mypy", init, submodule_search_locations=[os.path.join(entry, "mypy")]
                )
        return None


def main() -> int:
    sys.path_hooks.insert(0, hook)
    sys.path_importer_cache.clear()
    sys.meta_path.insert(0, TopLevel)  # type: ignore[arg-type]
    from mypy.main import main as mypy_main

    compiled = [
        name
        for name, module in sys.modules.items()
        if name.split(".")[0] == "mypy" and str(getattr(module, "__file__", "")).endswith(".pyd")
    ]
    print(f"interpreted mypy; compiled mypy modules loaded: {len(compiled)}", file=sys.stderr)
    try:
        mypy_main(args=sys.argv[1:], stdout=sys.stdout, stderr=sys.stderr)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else int(exc.code is not None)
    return 0


if __name__ == "__main__":
    sys.exit(main())
