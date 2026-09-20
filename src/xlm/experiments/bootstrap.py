"""Trusted stdlib-only bootstrap, invoked with -I -S -B from verified captured bytes.

No site initialization, .pth execution, user-site or live editable import. This is
an integrity boundary within a trusted local account, not an OS sandbox.
"""

from __future__ import annotations

import hashlib
import importlib.machinery
import json
import sys
from pathlib import Path
from typing import Any


def main() -> int:
    descriptor = Path(sys.argv[1])
    with descriptor.open("rb") as stream:
        raw = stream.read(8 * 1024**2 + 1)
    if len(raw) > 8 * 1024**2 or hashlib.sha256(raw).hexdigest() != sys.argv[2]:
        raise ValueError("worker launch descriptor identity mismatch")
    request = json.loads(raw)
    source = Path(request["snapshot_dir"]) / "code/src"
    site = Path(request["runtime_locations"]["site_packages"])
    sys.dont_write_bytecode = True

    # -B alone prevents writes but still permits reading existing .pyc files.
    # Compile verified source bytes, so excluded installer bytecode cannot override
    # the dependency fingerprint or bind an environment's old absolute paths.
    def source_code(loader: Any, fullname: str) -> Any:
        path = loader.get_filename(fullname)
        return loader.source_to_code(loader.get_data(path), path)

    def reject_sourceless(loader: Any, fullname: str) -> Any:
        raise ImportError(
            f"sourceless Python bytecode is not in the frozen runtime policy: {fullname}"
        )

    importlib.machinery.SourceFileLoader.get_code = source_code  # type: ignore[method-assign,assignment]
    importlib.machinery.SourcelessFileLoader.get_code = reject_sourceless  # type: ignore[method-assign,assignment]
    sys.path.insert(0, str(source.resolve()))
    sys.path.append(str(site.resolve()))
    from xlm.experiments.worker import run_worker

    return run_worker(request)


if __name__ == "__main__":
    raise SystemExit(main())
