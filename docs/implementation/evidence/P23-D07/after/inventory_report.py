"""Report the D07 tensor inventory at each level required by the brief.

Logical state names, unique deployed parameters, serialized tensor elements and
payload bytes, and container/metadata overhead are reported SEPARATELY so a
storage-layout change can never be confused with a change to the model.
"""

from __future__ import annotations

import json
import struct
import sys
from pathlib import Path
from typing import Any

import torch

from xlm.config.schemas import TransformerBaselineConfig
from xlm.export.loader import load_exported_model, read_manifest
from xlm.export.writer import export_model
from xlm.models.transformer import TransformerBaseline
from xlm.tokenizers.byte import ByteTokenizer

WORK = Path(sys.argv[1]).resolve()
WORK.mkdir(parents=True, exist_ok=True)


def layout(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    (n,) = struct.unpack("<Q", raw[:8])
    header = json.loads(raw[8 : 8 + n].decode("utf-8"))
    tensors = {k: v for k, v in header.items() if k != "__metadata__"}
    payload = sum(int(v["data_offsets"][1]) - int(v["data_offsets"][0]) for v in tensors.values())
    return {
        "serialized_tensor_entries": len(tensors),
        "serialized_elements": sum(
            int(torch.Size(v["shape"]).numel()) for v in tensors.values()
        ),
        "serialized_payload_bytes": payload,
        "container_header_bytes": 8 + n,
        "file_bytes": len(raw),
        "names": sorted(tensors),
    }


config = TransformerBaselineConfig(
    architecture="transformer_baseline", vocab_size=260, num_layers=2, hidden_size=64,
    num_attention_heads=4, intermediate_size=128, context_length=64,
    attention_backend="eager", tie_embeddings=True,
)
model = TransformerBaseline(config, seed=11)
tokenizer = ByteTokenizer()
out = WORK / "bundle"
manifest = export_model(model, tokenizer, out, "d07_after")
counts = model.count_parameters()
measured = layout(out / "model.safetensors")

ids = torch.tensor([[1, 2, 3, 4, 5]], dtype=torch.long)
with torch.no_grad():
    before_logits = model(ids).logits
loaded, loaded_tok, loaded_manifest = load_exported_model(out, device="cpu")
with torch.no_grad():
    after_logits = loaded(ids).logits

report = {
    "model_level": {
        "logical_state_names": len(model.state_dict()),
        "unique_deployed_parameters": int(counts.unique_deployed),
        "total_instantiated_including_aliases": int(counts.total_instantiated),
        "tied_parameters": int(counts.tied_parameters),
        "tied_aliases": [list(a) for a in counts.tied_aliases],
    },
    "serialized_level": measured,
    "manifest_level": {
        "export_format_version": manifest.export_format_version,
        "alias_schema_version": manifest.alias_schema_version,
        "storage_layout": manifest.storage_layout,
        "alias_map": manifest.alias_map,
        "parameters_deployed": manifest.parameters_deployed,
        "serialized_tensor_entries": manifest.serialized_tensor_entries,
        "serialized_payload_bytes": manifest.serialized_payload_bytes,
    },
    "cross_checks": {
        "payload_bytes_equals_unique_deployed_x4":
            measured["serialized_payload_bytes"] == counts.unique_deployed * 4,
        "serialized_elements_equals_unique_deployed":
            measured["serialized_elements"] == counts.unique_deployed,
        "alias_carries_no_payload": "lm_head.weight" not in measured["names"],
        "file_bytes_equals_header_plus_payload":
            measured["file_bytes"]
            == measured["container_header_bytes"] + measured["serialized_payload_bytes"],
    },
    "behaviour": {
        "logits_max_abs_diff": float((before_logits - after_logits).abs().max()),
        "parameter_identity_restored": loaded.lm_head.weight is loaded.embed_tokens.weight,
        "deployed_parameters_unchanged":
            int(loaded.count_parameters().unique_deployed) == int(counts.unique_deployed),
        "tokenizer_fingerprint_unchanged": loaded_tok.fingerprint == tokenizer.fingerprint,
        "loaded_storage_layout": loaded_manifest.storage_layout,
    },
}
print(json.dumps(report, indent=2))
Path(__file__).with_name("inventory_after.json").write_text(
    json.dumps(report, indent=2), encoding="utf-8"
)
