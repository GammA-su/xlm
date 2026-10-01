"""Write the text-free FinePDFs row-group audit evidence (footer metadata only)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from xlm.data.acquisition.sampling import SamplingRequest, discover_layout_local, plan_sample_blocks

PATH = sys.argv[1]  # verified local copy of the shard (sha256 checked by the caller)
NAME = "data/eng_Latn/train/000_00083.parquet"
OUT = Path(__file__).resolve().parent
PARSER = 33_554_432
RATIO = 15.0
PROJ = (
    "text",
    "language",
    "extractor",
    "is_truncated",
    "token_count",
    "full_doc_lid",
    "full_doc_lid_score",
    "page_average_lid",
    "page_average_lid_score",
)

layout = discover_layout_local(
    PATH, name=NAME, max_parser_bytes=PARSER, max_decompression_ratio=RATIO
)
rows = []
for g in layout.groups:
    projected = [c for c in g.columns if c.path.split(".")[0] in PROJ]
    rows.append(
        {
            "index": g.index,
            "start_row": g.start_row,
            "num_rows": g.num_rows,
            "total_byte_size": g.total_byte_size,
            "columns_compressed": sum(c.compressed for c in g.columns),
            "projected_compressed": sum(c.compressed for c in projected),
            "projected_uncompressed": sum(c.uncompressed for c in projected),
            "max_column_ratio": round(
                max(c.uncompressed / max(1, c.compressed) for c in g.columns), 4
            ),
            "usable": g.usable,
            "refusal_kind": None
            if g.usable
            else ("parser_byte_bound" if "parser byte bound" in (g.refusal or "") else "other"),
        }
    )

runs: list[list[int]] = []
start = None
for g in layout.groups:
    if g.usable and start is None:
        start = g.index
    elif not g.usable and start is not None:
        runs.append([start, g.index])
        start = None
if start is not None:
    runs.append([start, len(layout.groups)])

reproduced = plan_sample_blocks(
    {NAME: layout},
    SamplingRequest(
        source_id="finepdfs_edu",
        view_id="eng_Latn",
        revision="9cfabe2127faca99b3d5c4dc6d1fcb397399ebde",
        files=(NAME,),
        seed=20260918,
        mode="rowgroup",
        target_records=20000,
    ),
).to_report()

usable = [r for r in rows if r["usable"]]
refused = [r for r in rows if not r["usable"]]
summary = {
    "kind": "finepdfs_range_rowgroup_audit",
    "basis": "footer metadata of the verified local shard; no record payload decoded or printed",
    "file": NAME,
    "local_copy_sha256": "4eeb58bc769a194f472100ed4acd1d960e35c669caecd96fd4ce6425fabca38d",
    "local_copy_bytes": 2771021138,
    "bounds": {"max_parser_bytes": PARSER, "max_decompression_ratio": RATIO},
    "refusal_rule": (
        "sampling._refusal_for_group == records.check_row_group: total_byte_size > max_parser_bytes"
    ),
    "row_groups": len(rows),
    "usable_groups": len(usable),
    "refused_groups": len(refused),
    "refused_by_kind": {
        "parser_byte_bound": sum(1 for r in refused if r["refusal_kind"] == "parser_byte_bound")
    },
    "usable_rows": sum(r["num_rows"] for r in usable),
    "refused_rows": sum(r["num_rows"] for r in refused),
    "projected_compressed_usable": sum(r["projected_compressed"] for r in usable),
    "projected_compressed_refused": sum(r["projected_compressed"] for r in refused),
    "usable_runs_start_stop_exclusive": runs,
    "longest_usable_run_rows": max(sum(rows[i]["num_rows"] for i in range(a, b)) for a, b in runs),
    "usable_groups_after_178": sum(1 for r in usable if r["index"] > 178),
    "reproduced_sample_blocks": {
        "seed": 20260918,
        "row_ranges": reproduced["row_ranges"],
        "planned_records": reproduced["planned_records"],
        "warnings": reproduced["warnings"],
    },
}
OUT.mkdir(parents=True, exist_ok=True)
# Bytes, not text mode: LF on every platform so reruns are byte-identical.
(OUT / "row-groups.json").write_bytes((json.dumps(rows, indent=1) + "\n").encode("utf-8"))
(OUT / "summary.json").write_bytes((json.dumps(summary, indent=2) + "\n").encode("utf-8"))
print(
    json.dumps(
        {k: v for k, v in summary.items() if k != "usable_runs_start_stop_exclusive"}, indent=1
    )
)
