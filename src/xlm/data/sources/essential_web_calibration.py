"""Exact yields and crawl dispersion of the measured Essential-Web calibration.

Pure arithmetic over measured counts: no network, no source text, no selector
or quota change. Estimated tokens use the project's assumed UTF-8 bytes per
token (3 / 4 / 5); they are never exact training-token counts.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from fractions import Fraction
from typing import Any

from xlm.data.adapters import essential_web_selector as selector

VIEWS: tuple[str, ...] = tuple(selector.ADMITTED_COMPONENTS)
#: More bytes per token means fewer tokens per byte, hence MORE rows to scan.
BYTES_PER_TOKEN: dict[str, int] = {"low": 3, "central": 4, "high": 5}
TOKEN_METHOD = "mix01_inventory: assumed UTF-8 bytes/token 3/4/5; estimated tokens only"
_OPTIONAL_UNIT_KEYS = (
    "requests",
    "elapsed_seconds",
    "decompressed_bytes",
    "raw_bytes",
    "max_raw_record_bytes",
    "performance",
)


class CalibrationError(ValueError):
    """Measured calibration inputs are missing, inconsistent or not positive."""


def _count(value: Any, name: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise CalibrationError(f"'{name}' must be an integer >= {minimum}")
    return value


def exact(value: Fraction) -> dict[str, Any]:
    """A ratio as its exact fraction plus the float used for display."""
    return {"fraction": f"{value.numerator}/{value.denominator}", "value": float(value)}


def rows_required(
    target_tokens: int, canonical_bytes: int, input_rows: int, bytes_per_token: int
) -> int:
    """Smallest row count whose linear yield reaches ``target_tokens`` estimated tokens."""
    if min(target_tokens, canonical_bytes, input_rows, bytes_per_token) <= 0:
        raise CalibrationError("positive target, yield and token sizing are required")
    needed = Fraction(target_tokens * bytes_per_token * input_rows, canonical_bytes)
    return -(-needed.numerator // needed.denominator)


def component_yields(measurement: Mapping[str, Any]) -> dict[str, Any]:
    """Exact per-component and shared per-row yields of one calibration measurement."""
    rows = _count(measurement["input_rows"], "input_rows", 1)
    transfer = _count(measurement["physical_response_body_bytes"], "transfer", 1)
    decompressed = _count(measurement["decompressed_bytes"], "decompressed", 1)
    malformed = _count(measurement["malformed_rows"], "malformed_rows")
    counts = measurement["selector_counts"]
    if sum(_count(counts[key], key) for key in selector.FINAL_COMPONENTS) != rows:
        raise CalibrationError("selector counts do not conserve the input rows")
    elapsed = Fraction(str(measurement["elapsed_wall_seconds_including_restart_downtime"]))
    components: dict[str, Any] = {}
    for view in VIEWS:
        cost = measurement["retention_and_cost"][view]
        documents = _count(cost["documents"], f"{view} documents")
        canonical = _count(cost["canonical_bytes"], f"{view} canonical_bytes")
        if documents != counts[view]:
            raise CalibrationError(f"{view}: retained documents differ from selector count")
        entry: dict[str, Any] = {
            "documents": documents,
            "canonical_bytes": canonical,
            "characters": _count(cost["characters"], f"{view} characters"),
            "documents_per_input_row": exact(Fraction(documents, rows)),
            "canonical_bytes_per_input_row": exact(Fraction(canonical, rows)),
            "estimated_tokens": {
                name: exact(Fraction(canonical, size)) for name, size in BYTES_PER_TOKEN.items()
            },
            "estimated_tokens_per_input_row": {
                name: exact(Fraction(canonical, size * rows))
                for name, size in BYTES_PER_TOKEN.items()
            },
        }
        if documents and canonical:
            entry.update(
                average_canonical_bytes_per_document=exact(Fraction(canonical, documents)),
                estimated_tokens_per_document=exact(
                    Fraction(canonical, BYTES_PER_TOKEN["central"] * documents)
                ),
                transfer_bytes_per_estimated_token=exact(
                    Fraction(transfer * BYTES_PER_TOKEN["central"], canonical)
                ),
                transfer_bytes_per_canonical_byte=exact(Fraction(transfer, canonical)),
                input_rows_per_retained_document=exact(Fraction(rows, documents)),
            )
        components[view] = entry
    return {
        "input_rows": rows,
        "token_method": TOKEN_METHOD,
        "bytes_per_token": dict(BYTES_PER_TOKEN),
        "components": components,
        "shared": {
            "physical_transfer_bytes_per_input_row": exact(Fraction(transfer, rows)),
            "decompressed_bytes_per_input_row": exact(Fraction(decompressed, rows)),
            "elapsed_seconds_per_input_row": exact(elapsed / rows),
            "malformed_rows": malformed,
            "malformed_rate": exact(Fraction(malformed, rows)),
            "transfer_note": "2,048-row prefix windows; NOT a full-file cost per row",
            "elapsed_note": "journal wall time, one worker, including restart downtime",
        },
    }


def component_requirements(
    measurement: Mapping[str, Any], quotas: Mapping[str, Any]
) -> dict[str, Any]:
    """Rows each component needs alone, the bottleneck, and the resulting pools.

    Linear extrapolation of the pooled calibration yield. Quotas are read, never
    changed; oversupply of a component does not alter its quota.
    """
    rows = _count(measurement["input_rows"], "input_rows", 1)
    central = BYTES_PER_TOKEN["central"]
    needs: dict[str, Any] = {}
    for view in VIEWS:
        canonical = _count(measurement["retention_and_cost"][view]["canonical_bytes"], view, 1)
        target = int(quotas["first_pass_headroom_quotas"][view])
        needs[view] = {
            "final_exact_token_quota": int(quotas["final_quotas"][view]),
            "first_pass_estimated_token_target": target,
            "required_input_rows": rows_required(target, canonical, rows, central),
        }
    bottleneck = max(VIEWS, key=lambda view: int(needs[view]["required_input_rows"]))
    scan = int(needs[bottleneck]["required_input_rows"])
    for view in VIEWS:
        canonical = int(measurement["retention_and_cost"][view]["canonical_bytes"])
        pool = Fraction(canonical * scan, central * rows)
        target = int(needs[view]["first_pass_estimated_token_target"])
        needs[view].update(
            expected_estimated_tokens_at_bottleneck_scan=float(pool),
            expected_canonical_bytes_at_bottleneck_scan=float(Fraction(canonical * scan, rows)),
            ratio_to_first_pass_target=float(pool / target),
        )
    return {
        "basis": "pooled calibration yield, central 4 bytes/token, linear in scanned rows",
        "components": needs,
        "bottleneck": bottleneck,
        "bottleneck_required_input_rows": scan,
        "quotas_changed": False,
    }


def _spread(values: Sequence[float]) -> dict[str, Any]:
    low, high = min(values), max(values)
    return {
        "min": low,
        "max": high,
        "unweighted_mean": sum(values) / len(values),
        "max_over_min": None if low == 0 else high / low,
    }


def crawl_dispersion(
    units: Sequence[Mapping[str, Any]], science_target_tokens: int
) -> dict[str, Any]:
    """Per-crawl yields and their spread, without any sampling-distribution claim.

    Each unit is one crawl, one file and one prefix window, so the units are
    clusters: no binomial or row-level interval is meaningful. The
    leave-one-crawl-out range is a sensitivity description only.
    """
    if len(units) < 2 or len({unit["crawl"] for unit in units}) != len(units):
        raise CalibrationError("dispersion needs at least two distinct crawls")
    central = BYTES_PER_TOKEN["central"]
    table: list[dict[str, Any]] = []
    for unit in units:
        rows = _count(unit["input_rows"], "input_rows", 1)
        entry: dict[str, Any] = {
            key: unit[key]
            for key in ("crawl", "file", "input_rows", "malformed_rows", "transferred_bytes")
        }
        entry.update({key: unit[key] for key in _OPTIONAL_UNIT_KEYS if key in unit})
        for view in VIEWS:
            documents = _count(unit[view]["documents"], f"{view} documents")
            canonical = _count(unit[view]["canonical_bytes"], f"{view} canonical_bytes")
            entry[view] = {
                "documents": documents,
                "canonical_bytes": canonical,
                "estimated_tokens": canonical / central,
                "estimated_tokens_per_input_row": canonical / central / rows,
                "documents_per_input_row": documents / rows,
            }
        table.append(entry)
    total_rows = sum(int(unit["input_rows"]) for unit in units)
    science = "essential_science"
    total_science = sum(int(unit[science]["canonical_bytes"]) for unit in units)
    leave_one_out: list[dict[str, Any]] = []
    for unit in units:
        rest_bytes = total_science - int(unit[science]["canonical_bytes"])
        rest_rows = total_rows - int(unit["input_rows"])
        leave_one_out.append(
            {
                "left_out_crawl": unit["crawl"],
                "required_input_rows": None
                if rest_bytes == 0
                else rows_required(science_target_tokens, rest_bytes, rest_rows, central),
            }
        )
    known = [int(e["required_input_rows"]) for e in leave_one_out if e["required_input_rows"]]
    pooled = rows_required(science_target_tokens, total_science, total_rows, central)
    summary: dict[str, Any] = {
        view: {
            "documents": _spread([float(e[view]["documents"]) for e in table]),
            "estimated_tokens_per_input_row": _spread(
                [float(e[view]["estimated_tokens_per_input_row"]) for e in table]
            ),
        }
        for view in VIEWS
    }
    summary["transferred_bytes"] = _spread([float(e["transferred_bytes"]) for e in table])
    summary["malformed_rows"] = _spread([float(e["malformed_rows"]) for e in table])
    return {
        "design": "one prefix window per crawl and file: clustered, not independent rows",
        "interval_statement": "no confidence interval is reported; spread is descriptive only",
        "crawls": len(table),
        "per_crawl": table,
        "spread": summary,
        "science_rows_required_pooled": pooled,
        "science_rows_required_leave_one_crawl_out": leave_one_out,
        "science_rows_required_leave_one_out_range": [min(known), max(known)] if known else None,
        "science_leave_one_out_max_over_pooled": max(known) / pooled if known else None,
    }
