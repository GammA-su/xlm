"""Small read-only metadata/provenance sample; never a full corpus scan."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import yaml

from xlm.data.acquisition.source_run import write_once
from xlm.data.evidence_v2 import canonical
from xlm.data.exclusion.inputs import contained, read_metadata


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--quotas", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest = read_metadata(args.manifest)
    root = Path(manifest["data_root"])
    if args.output.resolve().is_relative_to(root.resolve()):
        raise ValueError("facts output must be outside the immutable data root")
    quotas = yaml.safe_load(args.quotas.read_text(encoding="utf-8"))
    headroom: dict[str, Any] = {}
    for component, counts in manifest["components"].items():
        have = counts["canonical_bytes"]
        need = 4 * quotas["first_pass_headroom_quotas"][component]
        final = 4 * quotas["final_quotas"][component]
        headroom[component] = {
            **counts,
            "first_pass_target_bytes": need,
            "estimated_final_target_bytes": final,
            "loss_fraction_to_first_pass_target": (have - need) / have,
            "loss_fraction_to_estimated_final_target": (have - final) / have,
        }
    samples = []
    seen: set[str] = set()
    for entry in manifest["files"]:
        key = entry["source_key"]
        if key in seen:
            continue
        seen.add(key)
        path = contained(root, entry["path"])
        with path.open("rb") as stream:
            raw = stream.readline(2 * 1024**2 + 1)
        if len(raw) > 2 * 1024**2:
            raise ValueError("provenance sample exceeds 2 MiB; not widening the sample")
        doc = canonical.loads_bytes_strict(raw)
        samples.append(
            {
                "source_key": key,
                "file": entry["path"],
                "bytes_read": len(raw),
                "source_revision": doc["source_revision"],
                "source_metadata_keys": sorted(doc["source_metadata"]),
                "has_parent_ids": bool(doc["parent_ids"]),
                "cluster_keys": sorted(doc["cluster_ids"]),
                "has_document_id": bool(doc["doc_id"]),
                "source_row": doc["source_row"],
                "source_file": doc["source_file"],
            }
        )
    result = {
        "input_manifest_digest": manifest["digest"],
        "headroom": headroom,
        "provenance_samples": samples,
        "sample_limit": "first row of first file per source key; at most 10 rows, 2 MiB each",
        "sample_bytes_read": sum(s["bytes_read"] for s in samples),
        "sample_bias": "prefix-only; no full corpus compatibility or hash certification",
        "token_basis": "four canonical UTF-8 bytes per token; exact tokenizer counts unknown",
        "sources": [
            {"source_key": s["source_key"], "seal_digest": s["seal_digest"], "source": s["source"]}
            for s in manifest["sources"]
        ],
    }
    write_once(args.output, result)
    print(f"Saved metadata facts; {len(samples)} prefix rows, {result['sample_bytes_read']} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
