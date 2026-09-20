"""Summarize completed authored public runs without inventing benchmark evidence."""
from __future__ import annotations
import json
from pathlib import Path
import torch

HERE = Path(__file__).resolve().parent
results = []
for fixture in sorted((HERE / "focused-tmp").glob("test_public_two_source_prepare*")):
    if not fixture.is_dir():
        continue
    final = []
    for meta_path in (fixture / "direct-home").rglob("checkpoint_meta.json"):
        meta = json.loads(meta_path.read_text())
        if meta["committed_valid_targets"] == 33 and (meta_path.parent / "_COMPLETED").exists():
            final.append(meta_path.parent)
    assert len(final) == 1, (fixture, final)
    checkpoint = final[0]
    data = json.loads((checkpoint / "data_state.json").read_text())
    objective = torch.load(checkpoint / "objective.pt", weights_only=True)
    optimizer = torch.load(checkpoint / "optimizer.pt", weights_only=True)
    execution = json.loads((checkpoint / "execution.json").read_text())
    results.append({
        "fixture": fixture.name, "checkpoint": str(checkpoint),
        "source_counters": data["scheduler"]["counters"],
        "committed_stream_cursors": data["cursors"],
        "committed_stream_carry": data["carry_token"],
        "targets": data["committed_valid_targets"], "trace_digest": data["trace_digest"],
        "auxiliary_parameter": float(objective["aux_param"]) if "aux_param" in objective else None,
        "optimizer_saved_keys": sorted(optimizer),
        "components": execution["envelope"]["bindings"]["components"],
        "tokenizer_identity": execution["envelope"]["bindings"]["tokenizer"],
        "worker_pid": execution["observations"]["worker_pid"],
        "snapshot_dir": execution["observations"]["snapshot_dir"],
        "plugin_origins": {k: v for k, v in execution["observations"]["module_origins"].items()
                           if k.startswith("xlm_plugin_")},
    })
assert len(results) == 2
(HERE / "public_observations.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
print(json.dumps(results, indent=2))
