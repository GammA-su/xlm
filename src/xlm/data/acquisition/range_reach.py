"""Structural reach of the v1 range path over one verified local Parquet file (offline).

``range_selected`` v1 decodes a row group only if ``records.check_row_group``
accepts it (all-column ``total_byte_size`` against ``max_parser_bytes``,
per-column decompression ratio); ``sampling._refusal_for_group`` is the same
rule. ``whole_file_local`` processes every row of the file. When any group
is refused, the range path cannot deliver the population the whole-file path
processes, so a range timing of the reachable groups is not a matched
comparison. Footer metadata only: no record payload is decoded.

Status under ``range-v1-reach-v1``:

- ``comparable``: every row group is reachable (a matched range benchmark is
  needed before range can be excluded);
- ``non_comparable``: some, not all, row groups are refused;
- ``infeasible``: no row group is reachable.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from xlm.data.acquisition.sampling import FileLayout
from xlm.data.acquisition.transport_policy import (
    SUBJECT_KEYS,
    PolicyError,
    TransportMode,
    mode_disposition,
)
from xlm.data.evidence_v2 import canonical

REACH_KIND = "mix01_range_reach_audit"
REACH_CONTRACT = "range-v1-reach-v1"
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


def _kind(refusal: str) -> str:
    if "parser byte bound" in refusal:
        return "parser_byte_bound"
    if "decompression ratio" in refusal:
        return "decompression_ratio"
    return "other"


def reach_audit(
    layout: FileLayout,
    *,
    subject: Mapping[str, str],
    file_sha256: str,
    file_bytes: int,
    max_parser_bytes: int,
    max_decompression_ratio: float,
    projected_fields: Sequence[str],
) -> dict[str, Any]:
    """Self-digested reach record of one file; never decodes or reports record text."""
    if not _HEX64.fullmatch(file_sha256) or file_bytes < 1 or not layout.groups:
        raise PolicyError("reach audit needs a verified file identity and its row groups")
    projected = set(projected_fields)
    groups = layout.groups
    refused = [group for group in groups if not group.usable]

    def projected_bytes(group: Any) -> int:
        return sum(c.compressed for c in group.columns if c.path.split(".")[0] in projected)

    runs: list[list[int]] = []
    start: int | None = None
    for group in groups:
        if group.usable and start is None:
            start = group.index
        elif not group.usable and start is not None:
            runs.append([start, group.index])
            start = None
    if start is not None:
        runs.append([start, len(groups)])
    total_rows = sum(group.num_rows for group in groups)
    refused_rows = sum(group.num_rows for group in refused)
    all_projected = sum(projected_bytes(group) for group in groups)
    refused_projected = sum(projected_bytes(group) for group in refused)
    reasons: dict[str, int] = {}
    for group in refused:
        key = _kind(str(group.refusal))
        reasons[key] = reasons.get(key, 0) + 1
    status = (
        "comparable"
        if not refused
        else "infeasible"
        if len(refused) == len(groups)
        else "non_comparable"
    )
    longest = max((sum(g.num_rows for g in groups[a:b]) for a, b in runs), default=0)
    body: dict[str, Any] = {
        "kind": REACH_KIND,
        "contract": REACH_CONTRACT,
        "rule": "a row group is range-reachable iff records.check_row_group accepts it "
        "(total_byte_size <= max_parser_bytes and every column within the ratio bound)",
        "subject": {key: subject[key] for key in SUBJECT_KEYS},
        "file": {"name": layout.name, "sha256": file_sha256, "bytes": file_bytes},
        "bounds": {
            "max_parser_bytes": max_parser_bytes,
            "max_decompression_ratio": max_decompression_ratio,
        },
        "projected_fields": sorted(projected),
        "row_groups": len(groups),
        "reachable_groups": len(groups) - len(refused),
        "refused_groups": [group.index for group in refused],
        "refusal_reasons": dict(sorted(reasons.items())),
        "rows": total_rows,
        "refused_rows": refused_rows,
        "projected_compressed_bytes": all_projected,
        "refused_projected_compressed_bytes": refused_projected,
        "reachable_runs": runs,
        "longest_reachable_run_rows": longest,
        "status": status,
        "invalidates": "any range_selected estimate for this file that assumes every row "
        "group is reachable or takes per-row transfer from reachable groups only",
    }
    body["digest"] = canonical.digest(body)
    return body


def check_reach(
    record: Mapping[str, Any], pin: Mapping[str, str], files: Sequence[str]
) -> Mapping[str, Any]:
    """Verify a reach record against this view and the measured whole-file benchmark."""
    body = dict(record)
    if body.pop("digest", None) != canonical.digest(body):
        raise PolicyError("range reach audit digest does not verify")
    if record.get("kind") != REACH_KIND or record.get("contract") != REACH_CONTRACT:
        raise PolicyError(f"not a {REACH_CONTRACT} range reach audit")
    if dict(record["subject"]) != {key: pin[key] for key in SUBJECT_KEYS}:
        raise PolicyError(
            "range reach audit belongs to another source view, repository or revision"
        )
    if record["file"]["name"] not in set(files):
        raise PolicyError(
            "range reach audit covers a file the whole-file benchmark did not measure"
        )
    return record


def disposition_of(record: Mapping[str, Any], evidence_sha256: str) -> dict[str, str]:
    """The ``range_selected`` disposition a checked reach record supports, if any."""
    status = str(record["status"])
    if status == "comparable":
        raise PolicyError(
            "every row group is range-reachable: a matched range benchmark is required, "
            "range_selected cannot be excluded structurally"
        )
    rows, refused = int(record["rows"]), int(record["refused_rows"])
    projected = int(record["projected_compressed_bytes"])
    summary = (
        f"{len(record['refused_groups'])} of {record['row_groups']} row groups of "
        f"{record['file']['name']} are refused by records.check_row_group "
        f"({refused}/{rows} rows, "
        f"{int(record['refused_projected_compressed_bytes'])}/{projected} projected "
        "compressed bytes); the range path cannot reach the whole-file population"
    )
    return mode_disposition(
        TransportMode.RANGE_SELECTED,
        status,
        contract=REACH_CONTRACT,
        evidence_digest=str(record["digest"]),
        evidence_sha256=evidence_sha256,
        summary=summary,
    )
