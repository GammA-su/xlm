"""Component-aware calibration of a multi-component ``.jsonl.gz`` source (offline).

A consolidated source such as Common Pile mixes corpora whose files differ by
three orders of magnitude in size and whose rows differ from short news items
to 128 KB book segments, so one calibration file cannot describe it. This
module turns bounded prefix samples (:mod:`~xlm.data.acquisition.jsonl_gz_sample`
receipts) of every allowlisted component into one self-digested calibration
record bound to the component allowlist and the production inventory.

Per component (measured on its samples): rows, accepted fraction, canonical
bytes per row (rejections included), compressed bytes per row (at the row's
end in the stream), decompression amplification, and the row text-size
distribution. Per component (estimated from those rates and the exact
inventory file sizes): rows and canonical bytes per file, mean and largest.

Combined, because the planner takes a hash-ordered prefix of the whole
inventory, every per-file and per-transferred-byte figure is weighted by the
inventory: mean rows and canonical bytes per file over all inventory files,
and canonical bytes per transferred byte weighted by component inventory
bytes. Per-file ceilings are proposed as ``ceil(1.35 x)`` the largest estimate
over all components, never the mean, so a dense small component cannot fail
against a mean. Every derived number is labeled an estimate.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

from xlm.data.acquisition import component_allowlist as allow
from xlm.data.acquisition.transport_policy import SourceLayout
from xlm.data.evidence_v2 import canonical

CALIBRATION_KIND = "mix01_component_calibration"
CALIBRATION_VERSION = 1
SAMPLE_KIND = "jsonl_gz_prefix_sample"
BOUND_MARGIN = 1.35
BYTES_PER_ESTIMATED_TOKEN = 4
#: Disclosure basis a ``mix01_inventory.py record --measurement`` entry carries.
TRANSFER_BASIS = "component_weighted_prefix_samples"


class CalibrationError(ValueError):
    """Calibration inputs are missing, foreign or inconsistent; nothing is written."""


def _verified(receipt: Mapping[str, Any]) -> None:
    body = dict(receipt)
    if body.pop("digest", None) != canonical.digest(body):
        raise CalibrationError("prefix sample receipt digest does not verify")
    if receipt.get("kind") != SAMPLE_KIND or receipt.get("version") != 1:
        raise CalibrationError("not a version-1 prefix sample receipt")


def _quantile(values: Sequence[int], q: float) -> int:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, math.floor(q * len(ordered)))]


def build_calibration(
    receipts: Sequence[Mapping[str, Any]],
    *,
    allowlist: Mapping[str, Any],
    inventory: Mapping[str, Any],
) -> dict[str, Any]:
    """The self-digested calibration record of every allowlisted component."""
    if not receipts:
        raise CalibrationError("calibration needs at least one prefix sample receipt")
    binding = inventory.get("component_allowlist") or {}
    if binding.get("digest") != allowlist.get("digest"):
        raise CalibrationError("inventory does not bind this component allowlist")
    included = list(allowlist["included"])
    sizes = {str(e["file"]): e["size_bytes"] for e in inventory["files"]}
    if any(size is None for size in sizes.values()):
        raise CalibrationError("component calibration needs every inventory file size")
    samples: dict[str, list[dict[str, Any]]] = {c: [] for c in included}
    used: list[dict[str, str]] = []
    seen: set[str] = set()
    for receipt in receipts:
        _verified(receipt)
        bindings = receipt.get("bindings") or {}
        if (
            receipt["repository"] != allowlist["repository"]
            or receipt["revision"] != allowlist["revision"]
            or bindings.get("component_allowlist_digest") != allowlist["digest"]
            or bindings.get("production_inventory_digest") != inventory["inventory_digest"]
        ):
            raise CalibrationError(
                f"receipt {receipt['digest'][:12]} is bound to another allowlist, inventory or revision"
            )
        used.append({"label": str(receipt["label"]), "digest": str(receipt["digest"])})
        for entry in receipt["files"]:
            name = str(entry["file"])
            if name in seen:
                raise CalibrationError(f"'{name}' is sampled by more than one receipt")
            seen.add(name)
            if sizes.get(name) != entry["identity"]["total_bytes"]:
                raise CalibrationError(f"'{name}' is not a file of the production inventory")
            component = allow.component_of(name)
            if component not in samples:
                raise CalibrationError(f"'{name}' belongs to an excluded component")
            if entry["schema"]["key_sets"] != [["text"]]:
                raise CalibrationError(f"'{name}' rows are not text-only")
            samples[component].append(dict(entry))
    missing = [c for c, entries in samples.items() if not entries]
    if missing:
        raise CalibrationError(f"components without a calibration sample: {missing}")

    components: dict[str, Any] = {}
    files_total = len(sizes)
    for component, entries in sorted(samples.items()):
        rows = sum(int(e["rows"]) for e in entries)
        accepted = sum(int(e["adapter"]["accepted"]) for e in entries)
        canonical_bytes = sum(int(e["adapter"]["canonical_bytes"]) for e in entries)
        details = [d for e in entries for d in e["rows_detail"]]
        # Compressed stream bytes up to each file's last sampled row end.
        compressed = sum(int(e["rows_detail"][-1]["compressed_bytes_at_row_end"]) for e in entries)
        decoded = sum(int(d["line_bytes"]) + 1 for d in details)
        texts = [int(d["text_utf8_bytes"]) for d in details if d["text_utf8_bytes"] is not None]
        file_sizes = [int(s) for n, s in sizes.items() if allow.component_of(n) == component]
        per_row_compressed = compressed / rows
        canonical_per_row = canonical_bytes / rows
        components[component] = {
            "measured": {
                "files_sampled": sorted(str(e["file"]) for e in entries),
                "rows": rows,
                "accepted": accepted,
                "rejected": rows - accepted,
                "canonical_bytes": canonical_bytes,
                "compressed_bytes_to_last_row": compressed,
                "decoded_line_bytes": decoded,
                "accepted_fraction": accepted / rows,
                "canonical_bytes_per_row": canonical_per_row,
                "compressed_bytes_per_row": per_row_compressed,
                "canonical_bytes_per_compressed_byte": canonical_bytes / compressed,
                "decompression_amplification": decoded / compressed,
                "text_bytes": {
                    "min": min(texts),
                    "p50": _quantile(texts, 0.5),
                    "p90": _quantile(texts, 0.9),
                    "max": max(texts),
                },
            },
            "inventory": {
                "files": len(file_sizes),
                "bytes": sum(file_sizes),
                "mean_file_bytes": sum(file_sizes) / len(file_sizes),
                "max_file_bytes": max(file_sizes),
            },
            "estimated": {
                "rows_per_mean_file": sum(file_sizes) / len(file_sizes) / per_row_compressed,
                "rows_per_largest_file": max(file_sizes) / per_row_compressed,
                "canonical_bytes_per_largest_file": max(file_sizes) * canonical_bytes / compressed,
                "canonical_bytes_total": sum(file_sizes) * canonical_bytes / compressed,
            },
        }
    inventory_bytes = sum(int(s) for s in sizes.values())
    canonical_total = sum(c["estimated"]["canonical_bytes_total"] for c in components.values())
    rows_total = sum(
        c["inventory"]["bytes"] / c["measured"]["compressed_bytes_per_row"]
        for c in components.values()
    )
    proposed_rows = math.ceil(
        BOUND_MARGIN * max(c["estimated"]["rows_per_largest_file"] for c in components.values())
    )
    proposed_canonical = math.ceil(
        BOUND_MARGIN
        * max(c["estimated"]["canonical_bytes_per_largest_file"] for c in components.values())
    )
    amplification = max(c["measured"]["decompression_amplification"] for c in components.values())
    body: dict[str, Any] = {
        "kind": CALIBRATION_KIND,
        "version": CALIBRATION_VERSION,
        "source_id": str(allowlist["source_id"]),
        "component_id": str(allowlist["component_id"]),
        "repository": str(allowlist["repository"]),
        "revision": str(allowlist["revision"]),
        "component_allowlist_digest": str(allowlist["digest"]),
        "production_inventory_digest": str(inventory["inventory_digest"]),
        "receipts": used,
        "observed_transfer": {
            "requests": sum(
                int(e["transfer"]["requests"]) for entries in samples.values() for e in entries
            ),
            "seconds": sum(
                float(e["transfer"]["seconds"]) for entries in samples.values() for e in entries
            ),
            "transferred_bytes": sum(
                int(e["transfer"]["transferred_bytes"])
                for entries in samples.values()
                for e in entries
            ),
        },
        "components": components,
        "combined": {
            "basis": "estimate: per-component sample rates x exact inventory file sizes",
            "inventory_files": files_total,
            "inventory_bytes": inventory_bytes,
            "estimated_canonical_bytes": canonical_total,
            "estimated_rows": rows_total,
            "mean_file_bytes": inventory_bytes / files_total,
            "rows_per_file": rows_total / files_total,
            "canonical_bytes_per_row": canonical_total / rows_total,
            "canonical_bytes_per_file": canonical_total / files_total,
            "canonical_bytes_per_transferred_byte": canonical_total / inventory_bytes,
            "max_decompression_amplification": amplification,
            "estimated_tokens": canonical_total / BYTES_PER_ESTIMATED_TOKEN,
        },
        "proposed_file_bounds": {
            "rule": f"ceil({BOUND_MARGIN} x the largest per-component estimate of the largest file)",
            "max_rows_per_file": proposed_rows,
            "max_canonical_bytes_per_file": proposed_canonical,
        },
        "token_method": "canonical UTF-8 bytes / 4 (estimate, never exact XLM tokens)",
    }
    body["digest"] = canonical.digest(body)
    return body


def check_calibration(
    record: Mapping[str, Any], *, allowlist: Mapping[str, Any], inventory: Mapping[str, Any]
) -> dict[str, Any]:
    """Verify a stored calibration's digest and its allowlist/inventory binding."""
    body = dict(record)
    if body.pop("digest", None) != canonical.digest(body):
        raise CalibrationError("component calibration digest does not verify")
    if record.get("kind") != CALIBRATION_KIND or record.get("version") != CALIBRATION_VERSION:
        raise CalibrationError("not a version-1 component calibration")
    if (
        record["component_allowlist_digest"] != allowlist["digest"]
        or record["production_inventory_digest"] != inventory["inventory_digest"]
        or sorted(record["components"]) != sorted(allowlist["included"])
    ):
        raise CalibrationError("component calibration belongs to another allowlist or inventory")
    return dict(record)


def layout_of(record: Mapping[str, Any], *, record_sha256: str) -> SourceLayout:
    """The planner's whole-file layout: the inventory mean file, with measured density.

    A ``.jsonl.gz`` file is one sequential unit: it has no row groups or ranges,
    so the group fields describe the whole mean file and only whole-file modes
    apply (``source_plan.JSONL_GZ_MODES``).
    """
    combined = record["combined"]
    mean_file = max(1, round(combined["mean_file_bytes"]))
    rows = max(1, round(combined["rows_per_file"]))
    return SourceLayout(
        source_id=str(record["source_id"]),
        file_bytes=mean_file,
        group_rows=rows,
        group_bytes=mean_file,
        projected_group_bytes=mean_file,
        range_requests_per_group=1.0,
        metadata_requests_per_file=1,
        canonical_bytes_per_row=float(combined["canonical_bytes_per_row"]),
        range_record_bytes_per_row=float(combined["canonical_bytes_per_row"]),
        metadata_bytes_per_file=0,
        source_files=int(combined["inventory_files"]),
        evidence={"component_calibration": record_sha256},
        rows_per_file_measured=rows,
    )


def measurement(record: Mapping[str, Any]) -> dict[str, Any]:
    """A ``mix01_inventory.py record --measurement`` file of the combined estimate.

    The per-component samples are reweighted so each component's compressed
    bytes are proportional to its inventory bytes (the hash-ordered prefix the
    planner takes); counts are rounded to integers and disclosed as such.
    """
    components = record["components"]
    inventory_bytes = float(record["combined"]["inventory_bytes"])
    scale = sum(c["measured"]["compressed_bytes_to_last_row"] for c in components.values())
    sampled = accepted = canonical_bytes = transferred = 0
    for entry in components.values():
        share = entry["inventory"]["bytes"] / inventory_bytes
        factor = share * scale / entry["measured"]["compressed_bytes_to_last_row"]
        rows = round(entry["measured"]["rows"] * factor)
        kept = round(entry["measured"]["accepted"] * factor)
        sampled += rows
        accepted += min(kept, rows)
        canonical_bytes += round(entry["measured"]["canonical_bytes"] * factor)
        transferred += round(entry["measured"]["compressed_bytes_to_last_row"] * factor)
    return {
        "measurement_version": 1,
        "records_sampled": max(1, sampled),
        "accepted_records": accepted,
        "rejected_records": max(1, sampled) - accepted,
        "transferred_bytes": max(1, transferred),
        "canonical_bytes": max(1, canonical_bytes),
        "extra_survival": 1.0,
        "transfer_basis": TRANSFER_BASIS,
        "component_calibration_digest": str(record["digest"]),
        "avg_file_bytes": round(record["combined"]["mean_file_bytes"]),
    }
