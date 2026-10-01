"""OFFLINE: the runtime's footer-only checks on the four real IFM footers, against plan limits.

Each kept footer (from ``footer_metadata_fetch.py``) is presented at its exact
offset in a virtual file of the declared size whose body is never read (only
the footer is), then judged by the same ``discover_layout_local`` +
``layout_record`` calls that ``source_local.check_layout`` makes (the leading
magic check needs the payload and is skipped). Further bounds that footer
facts can decide are checked against each plan's limits. No network, no text.

    uv run --offline --locked --extra cpu --extra eval python \
        docs/implementation/evidence/IFM-PRODUCTION-BOUND-RECOVERY/preflight_footers.py \
        --footers <keep-dir> --data-root G:/XLM
"""

from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path
from typing import Any

import pyarrow as pa

from xlm.data.acquisition.sampling import discover_layout_local
from xlm.data.adapters.columns import columns_for
from xlm.data.sources.essential_web_bulk import layout_record


#: Arrow reads the last 64 KiB speculatively; that window before a smaller
#: footer is served as zeros (never fetched, never parsed). Anything earlier is
#: payload and refused.
SPECULATIVE_BYTES = 64 * 1024


class FooterOnly(io.RawIOBase):
    """A read-only file of ``size`` bytes holding ``footer`` at its end; the body is never read."""

    def __init__(self, footer: bytes, size: int) -> None:
        pad = min(max(0, SPECULATIVE_BYTES - len(footer)), size - len(footer))
        self.footer, self.size, self.position = bytes(pad) + footer, size, 0
        self.start = size - len(self.footer)

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self.position

    def seek(self, offset: int, whence: int = 0) -> int:
        base = {0: 0, 1: self.position, 2: self.size}[whence]
        self.position = base + offset
        return self.position

    def readinto(self, buffer: Any) -> int:
        if self.position < self.start:
            raise OSError("payload bytes were requested; this preflight reads footers only")
        chunk = self.footer[self.position - self.start : self.position - self.start + len(buffer)]
        buffer[: len(chunk)] = chunk
        self.position += len(chunk)
        return len(chunk)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--footers", required=True)
    parser.add_argument("--data-root", required=True)
    parser.add_argument("--summary", default=str(Path(__file__).with_name("footers.json")))
    options = parser.parse_args()
    summary = {
        (f["view"], f["rank"]): f
        for f in json.loads(Path(options.summary).read_text(encoding="utf-8"))["files"]
    }
    report: dict[str, Any] = {}
    for view in ("general", "planning"):
        for sequence in (1, 2):
            path = (
                Path(options.data_root) / "plans" / f"ifm_{view}" / f"p{sequence:02d}" / "plan.json"
            )
            record = json.loads(path.read_bytes())
            limits = record["limits"]
            for entry in record["selection"]["files"]:
                rank, name = int(entry["rank"]), str(entry["file"])
                facts = summary[(view, rank)]
                assert facts["file"] == name
                footer = (Path(options.footers) / f"{view}-f{rank:05d}.footer.bin").read_bytes()
                stream = pa.PythonFile(FooterOnly(footer, facts["file_bytes"]), mode="r")
                layout = layout_record(
                    discover_layout_local(
                        stream,
                        name=name,
                        max_parser_bytes=int(limits["max_parser_bytes"]),
                        max_decompression_ratio=float(limits["max_decompression_ratio"]),
                    ),
                    columns_for(f"ifm_{view}", view),
                    float(limits["max_decompression_ratio"]),
                )
                refused = [g["index"] for g in layout["groups"] if g["refusal"] is not None]
                projected = sum(g["projected_uncompressed_bytes"] for g in layout["groups"])
                text = facts["per_column"]["text"]["uncompressed"]
                checks = {
                    "file_bytes <= max_file_bytes": facts["file_bytes"] <= limits["max_file_bytes"],
                    "footer_bytes <= max_parser_bytes": facts["footer_bytes"]
                    <= limits["max_parser_bytes"],
                    "no row group refused (check_layout)": not refused,
                    "rows <= max_rows_per_file (check_layout)": layout["rows"]
                    <= limits["max_rows_per_file"],
                    "projected uncompressed <= max_decoded_bytes_per_file": projected
                    <= limits["max_decoded_bytes_per_file"],
                    "text uncompressed <= max_canonical_bytes_per_file": text
                    <= limits["max_canonical_bytes_per_file"],
                    "file + text <= max_durable_bytes_per_file": facts["file_bytes"] + text
                    <= limits["max_durable_bytes_per_file"],
                }
                report[f"ifm_{view}/p{sequence:02d}/f{rank:05d}"] = {
                    "plan_digest": record["digest"],
                    "file": name,
                    "rows": layout["rows"],
                    "row_groups": len(layout["groups"]),
                    "refused_groups": refused,
                    "projected_uncompressed_bytes": projected,
                    "checks": checks,
                    "passes": all(checks.values()),
                }
    json.dump(report, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
