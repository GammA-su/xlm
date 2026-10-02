"""OFFLINE, read-only: the admission gate every Mix-01 source key passes today.

Calls the driver's own ``current_admission`` (the check ``plan``, ``authorize``
and ``run`` apply) for each source key and prints its admission identity or
the refusal, plus the adapter code identity the running checkout computes.
Writes nothing; needs XLM_DATA_ROOT / XLM_HOME like the driver.

    uv run --offline --locked --extra cpu --extra eval python \
        docs/implementation/evidence/IFM-GENERAL-EMPTY-TEXT-RECOVERY/admission_gates.py
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[4]
KEYS = (
    "ultrax",
    "finepdfs",
    "synth",
    "wiki_rewrite",
    "finewiki",
    "ifm_general",
    "ifm_planning",
    "simple_stories",
)


def main() -> None:
    spec = importlib.util.spec_from_file_location("mix01_source", REPO / "scripts/mix01_source.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("driver not found")
    driver = importlib.util.module_from_spec(spec)
    sys.modules["mix01_source"] = driver
    spec.loader.exec_module(driver)
    from xlm.data.sources import certified_evidence as ce

    target = driver.store()
    report: dict[str, Any] = {}
    for key in KEYS:
        source = driver.spec_of(key)
        pin = driver.pin_of(source)
        entry: dict[str, Any] = {"adapter_id": pin.adapter_id}
        receipt = ce.stored_bridge(target, pin.source_id, pin.view_id)
        entry["stored_bridge_digest"] = None if receipt is None else receipt["digest"]
        entry["stored_code_sha256"] = None if receipt is None else receipt["adapter"]["code_sha256"]
        try:
            entry["running_code_sha256"] = ce.adapter_code_identity(pin.adapter_id)
        except TypeError:  # pre-change signature: one identity for every adapter
            entry["running_code_sha256"] = ce.adapter_code_identity()  # type: ignore[call-arg]
        try:
            entry["admission"] = driver.current_admission(source, target)
        except Exception as exc:  # report the gate's refusal verbatim
            entry["refused"] = f"{type(exc).__name__}: {exc}"
        report[key] = entry
    json.dump(report, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
