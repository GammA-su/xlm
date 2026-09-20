"""Bounded adversarial reproduction of P23 defect D07: tied-weight export storage.

Runs the REAL public export path (xlm.export.writer.export_model) with a tiny
authored model, then inspects the actual safetensors payload layout by parsing
the container header. Nothing is mocked.

Three controls, as required:

A. Genuine tie      - two attributes refer to the same Parameter object.
B. Equal but free   - two independent Parameters holding identical values.
C. Numerical reload - the current bundle still reloads and reproduces outputs.

Storage duplication is measured from per-tensor `data_offsets` in the
safetensors header, NOT from total file size: container and metadata overhead
are reported separately so they cannot be mistaken for payload.
"""

from __future__ import annotations

import json
import struct
import sys
from pathlib import Path
from typing import Any

import torch

from xlm.config.schemas import TransformerBaselineConfig
from xlm.export.loader import load_exported_model
from xlm.export.writer import export_model
from xlm.models.transformer import TransformerBaseline
from xlm.tokenizers.byte import ByteTokenizer

WORK = Path(sys.argv[1]).resolve()
WORK.mkdir(parents=True, exist_ok=True)
RESULTS: list[dict[str, Any]] = []


def tiny_config(tie: bool) -> TransformerBaselineConfig:
    return TransformerBaselineConfig(
        architecture="transformer_baseline",
        vocab_size=260,
        num_layers=2,
        hidden_size=64,
        num_attention_heads=4,
        intermediate_size=128,
        context_length=64,
        attention_backend="eager",
        tie_embeddings=tie,
    )


def read_safetensors_layout(path: Path) -> dict[str, Any]:
    """Parse the real container header: names, dtypes, shapes, byte ranges."""
    raw = path.read_bytes()
    (header_len,) = struct.unpack("<Q", raw[:8])
    header = json.loads(raw[8 : 8 + header_len].decode("utf-8"))
    tensors: dict[str, Any] = {}
    payload_bytes = 0
    for name, meta in sorted(header.items()):
        if name == "__metadata__":
            continue
        start, end = meta["data_offsets"]
        size = end - start
        payload_bytes += size
        tensors[name] = {
            "dtype": meta["dtype"],
            "shape": meta["shape"],
            "offset_start": start,
            "offset_end": end,
            "payload_bytes": size,
        }
    # Distinct byte ranges actually occupied (aliases would share a range).
    distinct_ranges = {(t["offset_start"], t["offset_end"]) for t in tensors.values()}
    return {
        "file_bytes": len(raw),
        "header_bytes": 8 + header_len,
        "payload_bytes": payload_bytes,
        "tensor_entries": len(tensors),
        "distinct_payload_ranges": len(distinct_ranges),
        "tensors": tensors,
        "metadata": header.get("__metadata__"),
    }


def model_inventory(model: torch.nn.Module) -> dict[str, Any]:
    counts = model.count_parameters()
    state_keys = sorted(model.state_dict().keys())
    return {
        "state_dict_keys": len(state_keys),
        "unique_deployed_parameters": int(counts.unique_deployed),
        "total_instantiated": int(counts.total_instantiated),
        "tied_parameters": int(counts.tied_parameters),
        "tied_aliases": [list(a) for a in counts.tied_aliases],
    }


# ------------------------------------------------------------------ control A
model_a = TransformerBaseline(tiny_config(tie=True), seed=11)
assert model_a.lm_head.weight is model_a.embed_tokens.weight, "fixture is not actually tied"
tok = ByteTokenizer()
out_a = WORK / "export_tied"
manifest_a = export_model(model_a, tok, out_a, export_id="d07_repro_tied")
layout_a = read_safetensors_layout(out_a / "model.safetensors")
inv_a = model_inventory(model_a)

embed = layout_a["tensors"].get("embed_tokens.weight")
head = layout_a["tensors"].get("lm_head.weight")
duplicated = bool(embed and head and (embed["offset_start"], embed["offset_end"])
                  != (head["offset_start"], head["offset_end"]))
RESULTS.append({
    "case": "A-genuine-tie",
    "question": "is a genuinely tied payload stored once?",
    "model": inv_a,
    "manifest_tied_mapping": manifest_a.tied_mapping,
    "manifest_parameters_deployed": manifest_a.parameters_deployed,
    "container": {k: v for k, v in layout_a.items() if k != "tensors"},
    "embed_tokens.weight": embed,
    "lm_head.weight": head,
    "TIED_PAYLOAD_STORED_TWICE": duplicated,
    "duplicated_bytes": embed["payload_bytes"] if duplicated else 0,
})

# ------------------------------------------------------------------ control B
# Independent Parameters that happen to hold identical values must NOT be
# deduplicated by any repair: they are separately trainable.
model_b = TransformerBaseline(tiny_config(tie=False), seed=11)
assert model_b.lm_head.weight is not model_b.embed_tokens.weight
with torch.no_grad():
    model_b.lm_head.weight.copy_(model_b.embed_tokens.weight)
assert torch.equal(model_b.lm_head.weight, model_b.embed_tokens.weight)
out_b = WORK / "export_equal_independent"
manifest_b = export_model(model_b, tok, out_b, export_id="d07_repro_equal")
layout_b = read_safetensors_layout(out_b / "model.safetensors")
inv_b = model_inventory(model_b)
embed_b = layout_b["tensors"]["embed_tokens.weight"]
head_b = layout_b["tensors"]["lm_head.weight"]
RESULTS.append({
    "case": "B-equal-but-independent",
    "question": "do equal-valued independent parameters stay separate?",
    "model": inv_b,
    "manifest_tied_mapping": manifest_b.tied_mapping,
    "manifest_parameters_deployed": manifest_b.parameters_deployed,
    "container": {k: v for k, v in layout_b.items() if k != "tensors"},
    "separate_payload_ranges": (embed_b["offset_start"], embed_b["offset_end"])
                               != (head_b["offset_start"], head_b["offset_end"]),
    "values_identical": True,
})

# ------------------------------------------------------------------ control C
ids = torch.tensor([[1, 2, 3, 4, 5]], dtype=torch.long)
with torch.no_grad():
    logits_before = model_a(input_ids=ids).logits
loaded, loaded_tok, loaded_manifest = load_exported_model(out_a, device="cpu", dtype="float32")
with torch.no_grad():
    logits_after = loaded(input_ids=ids).logits
RESULTS.append({
    "case": "C-numerical-reload",
    "question": "does the current bundle reload and reproduce outputs?",
    "logits_max_abs_diff": float((logits_before - logits_after).abs().max()),
    "identity_restored_on_load": loaded.lm_head.weight is loaded.embed_tokens.weight,
    "tokenizer_fingerprint_matches": loaded_tok.fingerprint == tok.fingerprint,
    "note": "numerical parity does NOT establish single-copy storage (C08)",
})

print(json.dumps({"results": RESULTS}, indent=2))
Path(__file__).with_name("repro_results.json").write_text(
    json.dumps({"results": RESULTS}, indent=2), encoding="utf-8"
)
