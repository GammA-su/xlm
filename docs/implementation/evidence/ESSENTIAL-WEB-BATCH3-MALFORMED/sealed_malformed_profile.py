"""Read-only malformed profile of every sealed fast-campaign unit (text-free).

For each receipt under ``<data root>/canonical/ew-fast`` it reads the science
view's compressed rejection ledger, keeps only ``rejection_category ==
"malformed"`` lines (source row + reason code; the ledger never holds text) and
reports the whole-file fraction and the worst running-prefix ratio that the
per-row ``MalformedCounter`` would see. Nothing is written except ``--output``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO / "src"))

from xlm.data.sources import essential_web_local as local  # noqa: E402

VIEW = "essential_science"


def prefix_profile(rows: int, bad_rows: list[int]) -> dict[str, Any]:
    """Worst running ratio at >=100 rows and the first row the rule would stop at."""
    worst, worst_at, stop_at = 0.0, None, None
    for count, row in enumerate(sorted(bad_rows), start=1):
        seen = row + 1
        if stop_at is None and ((seen < 100 and count > 2) or (seen >= 100 and count * 100 > seen)):
            stop_at = row
        ratio = count / max(seen, 100)
        if ratio > worst:
            worst, worst_at = ratio, row
    return {"worst_prefix_ratio": worst, "worst_prefix_row": worst_at, "stop_row": stop_at}


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    root = Path(os.environ["XLM_DATA_ROOT"]) / "canonical" / "ew-fast"
    units: list[dict[str, Any]] = []
    reasons: Counter[str] = Counter()
    for receipt_path in sorted(root.glob("b*/f*/receipt.json")):
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        summary = json.loads(
            (receipt_path.parent / VIEW / "adaptation_summary.json").read_text(encoding="utf-8")
        )
        ledger = local.read_ledger(
            receipt_path.parent / VIEW / local.LEDGER_FILENAME,
            int(summary["rejections"]["uncompressed_bytes"]),
        )
        bad_rows: list[int] = []
        for line in ledger.splitlines():
            if b'"rejection_category": "malformed"' not in line:
                continue
            entry = json.loads(line)
            bad_rows.append(int(entry["source_row"]))
            reasons[str(entry["reason"]).replace("essential_web_selector_value: ", "")] += 1
        if len(bad_rows) != int(receipt["malformed_rows"]):
            raise SystemExit(f"{receipt_path}: ledger and receipt malformed counts differ")
        rows = int(receipt["rows"])
        units.append(
            {
                "batch": int(receipt["batch"]),
                "rank": int(receipt["inventory_rank"]),
                "rows": rows,
                "malformed": len(bad_rows),
                "fraction": len(bad_rows) / rows,
                **prefix_profile(rows, bad_rows),
            }
        )
    total_rows = sum(u["rows"] for u in units)
    total_bad = sum(u["malformed"] for u in units)
    report = {
        "units": len(units),
        "rows": total_rows,
        "malformed": total_bad,
        "fraction": total_bad / total_rows,
        "max_file_fraction": max(u["fraction"] for u in units),
        "max_worst_prefix_ratio": max(u["worst_prefix_ratio"] for u in units),
        "units_over_half_percent_prefix": sum(u["worst_prefix_ratio"] > 0.005 for u in units),
        "reason_codes": dict(reasons.most_common()),
        "per_unit": units,
    }
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "per_unit"}, indent=2))
    top = sorted(units, key=lambda u: -u["worst_prefix_ratio"])[:8]
    for unit in top:
        print(json.dumps(unit))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
