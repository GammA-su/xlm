# Requires: operator-run, NETWORK (bounded Range requests to the pinned Hub resolve URL).
"""Bounded prefix sample of allowlisted ``.jsonl.gz`` source files (certification/calibration).

Each ``--target`` must be a file of the source's frozen production inventory
(which binds its component allowlist), and its top-level component must be
named in ``--authorized-components``: the command can never touch a file the
operator did not authorize. For each target exactly the first ``--rows`` rows
are fetched by sequential ``Range`` requests from byte 0 and decoded by the
production ``.jsonl.gz`` decoder; the stream is abandoned once they are
complete.

Outputs (write-once):

- ``<output-dir>/real-records.jsonl``: the sampled rows (CORPUS TEXT; the
  output directory must be outside the code checkout);
- ``<output-dir>/sample-receipt.json``: text-free accounting and adapter
  outcomes; ``--receipt-copy`` writes an identical copy for repository evidence.

    uv run --offline --locked --extra cpu --extra eval python scripts/jsonl_gz_sample.py \
        --source-key common_pile --data-root G:\\XLM --label cert02 --rows 16 \
        --authorized-components oercommons,pressbooks,project_gutenberg \
        --target oercommons/oercommons.chunk.49.jsonl.gz ... \
        --output-dir G:\\XLM\\calib\\common_pile_cert02
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from xlm.data.acquisition import component_allowlist as allow
from xlm.data.acquisition import jsonl_gz_sample as sampler
from xlm.data.acquisition import source_plan as planner
from xlm.data.acquisition.sampling import canonical_range_url
from xlm.data.sources import hf_inventory

REPO = Path(__file__).resolve().parents[1]
ADAPTERS = {"common_pile": "common_pile"}
MIB = 1024**2


def _fail(message: str) -> int:
    print(f"jsonl_gz_sample: refused: {message}", file=sys.stderr)
    return 1


def _write_once(path: Path, data: bytes) -> None:
    if path.exists():
        if path.read_bytes() != data:
            raise SystemExit(f"jsonl_gz_sample: refused: {path} exists with other content")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(data)
    temporary.replace(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-key", required=True)
    parser.add_argument("--data-root", required=True, type=Path)
    parser.add_argument("--label", required=True)
    parser.add_argument("--rows", type=int, required=True)
    parser.add_argument("--target", action="append", required=True)
    parser.add_argument("--authorized-components", required=True)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--receipt-copy", type=Path, default=None)
    parser.add_argument("--max-bytes-per-file", type=int, default=32 * MIB)
    parser.add_argument("--max-total-bytes", type=int, default=48 * MIB)
    parser.add_argument("--max-requests-per-file", type=int, default=48)
    parser.add_argument("--chunk-bytes", type=int, default=MIB)
    parser.add_argument("--max-line-bytes", type=int, default=64 * MIB)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--deadline", type=float, default=600.0)
    args = parser.parse_args(argv)

    if os.environ.get("HF_HUB_OFFLINE") == "1" or os.environ.get("HF_DATASETS_OFFLINE") == "1":
        return _fail("live sampling refused while HF_*_OFFLINE=1; unset only for this run")
    key = str(args.source_key)
    if key not in ADAPTERS:
        return _fail(f"'{key}' has no registered prefix-sample adapter")
    output = args.output_dir.resolve()
    if output.is_relative_to(REPO.resolve()):
        return _fail("--output-dir holds corpus text and must be outside the code checkout")
    authorized = {c.strip() for c in str(args.authorized_components).split(",") if c.strip()}
    root: Path = args.data_root
    try:
        listing = hf_inventory.read_listing(root / "inventories" / f"{key}.discovery.listing.json")
        record = allow.check_allowlist(
            allow.read_json(root / "calib" / "component_allowlists" / f"{key}.json"), listing
        )
        inventory_path = root / "inventories" / f"{key}.inventory.json"
        inventory = allow.read_json(inventory_path)
        allow.check_inventory_binding(record, listing, inventory)
        planner.check_inventory(
            inventory, str(record["source_id"]), str(record["repository"]), str(record["revision"])
        )
    except (allow.AllowlistError, hf_inventory.HfInventoryError, planner.PlanError) as exc:
        return _fail(str(exc))
    if not authorized <= set(record["included"]):
        return _fail(f"authorized components {sorted(authorized)} are not all allowlisted")
    sizes = {str(e["file"]): e["size_bytes"] for e in inventory["files"]}
    targets = list(dict.fromkeys(args.target))
    for target in targets:
        if target not in sizes or sizes[target] is None:
            return _fail(f"'{target}' is not a sized file of the production inventory")
        if allow.component_of(target) not in authorized:
            return _fail(f"'{target}' belongs to a component not authorized for this run")
    limits = sampler.SampleLimits(
        max_bytes=int(args.max_bytes_per_file),
        max_requests=int(args.max_requests_per_file),
        chunk_bytes=int(args.chunk_bytes),
        timeout_seconds=float(args.timeout),
        deadline_seconds=float(args.deadline),
        max_line_bytes=int(args.max_line_bytes),
    )
    revision = str(record["revision"])
    samples: list[sampler.PrefixSample] = []
    spent = 0
    for target in targets:
        remaining = int(args.max_total_bytes) - spent
        if remaining < limits.chunk_bytes:
            return _fail("the run's total byte ceiling is spent")
        bounded = sampler.SampleLimits(
            **{**limits.__dict__, "max_bytes": min(limits.max_bytes, remaining)}
        )
        url = canonical_range_url("huggingface", str(record["repository"]), revision, target)
        try:
            sample = sampler.sample_prefix(
                url,
                source_file=target,
                rows=int(args.rows),
                revision=revision,
                expected_total_bytes=int(sizes[target]),
                limits=bounded,
            )
        except sampler.PrefixSampleError as exc:
            return _fail(str(exc))
        spent += sample.transferred_bytes
        samples.append(sample)
        print(
            f"{target}: {len(sample.lines)} rows, {sample.requests} requests, "
            f"{sample.transferred_bytes:,} B transferred, whole file {sample.whole_file}"
        )
    receipt = sampler.sample_receipt(
        samples,
        label=str(args.label),
        source_id=str(record["source_id"]),
        repository=str(record["repository"]),
        revision=revision,
        adapter_id=ADAPTERS[key],
        bindings={
            "component_allowlist_digest": str(record["digest"]),
            "production_inventory_digest": str(inventory["inventory_digest"]),
            "discovery_listing_digest": str(listing["digest"]),
            "authorized_components": ",".join(sorted(authorized)),
        },
        limits=limits,
    )
    rows: list[bytes] = []
    for sample in samples:
        for index, line in enumerate(sample.lines):
            value: dict[str, Any] = {
                "_cert_source_file": sample.source_file,
                "_cert_source_row": index,
                "_cert_component": allow.component_of(sample.source_file),
                "_cert_revision": revision,
                **json.loads(line.decode("utf-8")),
            }
            rows.append(json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8"))
    _write_once(output / "real-records.jsonl", b"\n".join(rows) + b"\n")
    rendered = (json.dumps(receipt, indent=2, sort_keys=True) + "\n").encode("utf-8")
    _write_once(output / "sample-receipt.json", rendered)
    if args.receipt_copy is not None:
        _write_once(args.receipt_copy, rendered)
    totals = receipt["totals"]
    print(
        f"SAMPLE RECEIPT {receipt['digest']}: {totals['files']} files, {totals['rows']} rows, "
        f"{totals['requests']} requests, {totals['transferred_bytes']:,} B"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
