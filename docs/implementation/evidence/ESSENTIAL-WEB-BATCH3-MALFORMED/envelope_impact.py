"""Read-only: what the running code does to the prepared auto envelope and Batch 3.

Compares the stored envelope's identity with the identity the running code
derives, re-derives the Batch-3 child the envelope bound, and reports the
runner's own classification of the next batch. Writes only ``--output``.
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "src"))

auto: Any = importlib.import_module("essential_web_campaign")

from xlm.data.sources import essential_web_campaign_runner as runner  # noqa: E402


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--envelope", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    ctx = auto.Context(
        auto.tool.REPO / auto.tool.FAST_DIR / "campaign.json",
        Path(os.environ["XLM_DATA_ROOT"]),
        Path(os.environ["XLM_SCRATCH_ROOT"]),
    )
    campaign = ctx.load()
    stored = auto.tool.read_json(auto.auto_dir(campaign) / "envelopes" / f"{args.envelope}.json")
    runner.check_envelope(stored)
    current = auto.identity(campaign)
    batch = 3
    derived = auto.children(campaign, [batch])[0]
    bound = runner.child_for(stored, batch)
    recorded = auto.recorded_child(campaign, batch)
    report = {
        "envelope": args.envelope,
        "campaign_digest": campaign.config["digest"],
        "identity_differences": runner.differences(stored["identity"], current),
        "running_code_changes": {
            name: {"envelope": stored["identity"]["code"]["running_sha256"].get(name), "now": value}
            for name, value in current["code"]["running_sha256"].items()
            if stored["identity"]["code"]["running_sha256"].get(name) != value
        },
        "compatibility_records_now": current["code"]["compatibility"]["records"],
        "adapter_code_unchanged": stored["identity"]["adapter"] == current["adapter"],
        "selector_unchanged": stored["identity"]["selector"] == current["selector"],
        "batch3_child_bound_equals_derived": bound == derived,
        "batch3_child_bound_equals_recorded": bound == recorded,
        "batch3_authorization_digest": recorded["authorization_digest"],
        "next_batch_classification": auto.classify(
            campaign, batch, auto.event_log(campaign).read()
        ),
    }
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
