"""Arm T exact metadata-only locator selection (protocol section 6).

Reads the existing development metadata bundle READ-ONLY to derive the
frozen <=118 source-locator selection. Never reads upstream ``text``:
every input record is asserted text-free. Uses the frozen sweep
evaluator (imported by path, code-hash bound); census asserts of exactly
29 B-normal science and 25 D-normal-only science rows STOP otherwise.
Ranking is ``H([protocol, "text-select", seed, stratum, crawl,
repository, revision, file, row])`` ascending with
``(UTF8(file), row)`` tie-breaks, processed in normative stratum
precedence and section-3 crawl order, without replacement, borrowing,
or backfilling.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any

from xlm.data.evidence_v2 import canonical, frozen


class SelectionError(ValueError):
    """Any identity, census, ranking, or budget violation: STOP."""


def load_evaluator(scripts_dir: Path) -> tuple[Any, str]:
    """Import the frozen sweep evaluator by path; returns (module, sha256)."""
    path = scripts_dir / "essential_web_selector_sweep.py"
    raw = path.read_bytes()
    code_hash = hashlib.sha256(raw).hexdigest()
    spec = importlib.util.spec_from_file_location("evidence_v2_sweep_eval", path)
    if spec is None or spec.loader is None:
        raise SelectionError(f"cannot import evaluator from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["evidence_v2_sweep_eval"] = module
    spec.loader.exec_module(module)
    return module, code_hash


def load_policy_spec(evaluator: Any, recipes_dir: Path) -> tuple[Any, str]:
    """Load the frozen policy and refuse any digest drift."""
    spec, digest = evaluator.load_policy_spec(
        recipes_dir / "selectors" / "essential_web_selector_sweep_v1.yaml"
    )
    if digest != frozen.POLICY_DIGEST:
        raise SelectionError(f"policy digest drift: {digest}")
    return spec, digest


def iter_bundle_records(
    records_path: Path,
    bundle: Mapping[str, Any],
    execution: Mapping[str, Any],
    *,
    max_records: int = 5000,
    max_line_bytes: int = 1048576,
) -> Iterator[dict[str, Any]]:
    """Stream metadata records with frozen identity checks; text refused."""
    if bundle.get("digest") != frozen.DEV_BUNDLE_DIGEST:
        raise SelectionError("bundle digest is not the frozen development value")
    if execution.get("digest") != frozen.DEV_EXECUTION_DIGEST:
        raise SelectionError("execution digest is not the frozen development value")
    if bundle.get("combined_sha256") != frozen.DEV_COMBINED_SHA256:
        raise SelectionError("combined payload hash is not the frozen value")
    if bundle.get("total_records") != frozen.DEV_RECORDS:
        raise SelectionError("development record total is not 4096")
    if execution.get("projection") != ["eai_taxonomy", "quality_signals"]:
        raise SelectionError("development projection is not the frozen pair")
    if bundle.get("revision") != frozen.REVISION:
        raise SelectionError("bundle revision mismatch")
    files = {p["file"]: p for p in bundle["parts"]}
    payload_hash = hashlib.sha256()
    with records_path.open("rb") as stream:
        for line_number, raw in enumerate(stream, start=1):
            if len(raw) > max_line_bytes:
                raise SelectionError(f"line {line_number} exceeds the line cap")
            if line_number > max_records:
                raise SelectionError("more records than the input cap")
            payload_hash.update(raw)
            try:
                record = json.loads(raw)
            except ValueError as exc:
                raise SelectionError(f"line {line_number}: corrupt JSONL") from exc
            if not isinstance(record, dict):
                raise SelectionError(f"line {line_number}: record is not an object")
            if "text" in record:
                raise SelectionError(f"line {line_number}: text content refused")
            locator = record.get("_xlm_acquisition")
            if not isinstance(locator, Mapping):
                raise SelectionError(f"line {line_number}: missing acquisition locator")
            source_file = locator.get("source_file")
            if source_file not in files:
                raise SelectionError(f"line {line_number}: file not in bundle")
            part = files[source_file]
            row_index = locator.get("row_index")
            if (
                type(row_index) is not int
                or not part["row_range"][0] <= row_index < part["row_range"][1]
            ):
                raise SelectionError(f"line {line_number}: row outside its part range")
            if locator.get("revision") != frozen.REVISION:
                raise SelectionError(f"line {line_number}: locator revision mismatch")
            if locator.get("repository") != frozen.REPOSITORY:
                raise SelectionError(f"line {line_number}: locator repository mismatch")
            yield {
                "record": record,
                "source_file": str(source_file),
                "source_row": row_index,
                "crawl": str(part["crawl"]),
            }
    if payload_hash.hexdigest() != frozen.DEV_COMBINED_SHA256:
        raise SelectionError("recomputed payload hash mismatch")


def evaluate_rows(
    evaluator: Any, spec: Any, rows: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """Final component per policy/tier for each row; locators absolute."""
    evaluated: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for row in rows:
        key = (row["source_file"], row["source_row"])
        if key in seen:
            raise SelectionError(f"duplicate input locator: {key}")
        seen.add(key)
        fields, reasons, _ = evaluator.validate_row(row["record"], spec)
        if reasons:
            final = {f"{p}-{t}": "rejected" for p in "ABCD" for t in ("normal", "strict")}
        else:
            final = {
                f"{p}-{t}": evaluator.evaluate_policy(fields, p, t, spec)["final"]
                for p in "ABCD"
                for t in ("normal", "strict")
            }
        evaluated.append(
            {
                "locator": [
                    frozen.REPOSITORY,
                    frozen.REVISION,
                    row["source_file"],
                    row["source_row"],
                ],
                "crawl": row["crawl"],
                "final": final,
            }
        )
    return evaluated


def _final(row: Mapping[str, Any]) -> Mapping[str, str]:
    final = row["final"]
    if not isinstance(final, Mapping):
        raise SelectionError(f"row has no final-component mapping: {row.get('locator')}")
    return {str(k): str(v) for k, v in final.items()}


def _locator_of(row: Mapping[str, Any]) -> list[Any]:
    locator = row["locator"]
    if not isinstance(locator, list) or len(locator) != 4:
        raise SelectionError("row has no [repository, revision, file, row] locator")
    return locator


def _is_b(row: Mapping[str, Any], component: str) -> bool:
    return _final(row)["B-normal"] == component


def _is_d_only(row: Mapping[str, Any], component: str) -> bool:
    final = _final(row)
    if final["D-normal"] != component or final["B-normal"] == component:
        return False
    if final["B-normal"] != "rejected":
        raise SelectionError(
            f"D-only {component} row is B-assigned, not B-rejected: {row['locator']}"
        )
    return True


def _is_b_survivor(row: Mapping[str, Any], component: str) -> bool:
    final = _final(row)
    return final["B-normal"] == component and final["B-strict"] == component


def _is_b_loss(row: Mapping[str, Any], component: str) -> bool:
    final = _final(row)
    return final["B-normal"] == component and final["B-strict"] != component


def rank_digest(stratum: str, crawl: str, locator: Sequence[Any]) -> str:
    """Frozen non-census ranking hash for one locator."""
    return canonical.digest(
        [
            frozen.PROTOCOL_VERSION,
            frozen.TEXT_SELECT_RANK_PREFIX,
            frozen.TEXT_SELECTION_SEED,
            stratum,
            crawl,
            locator[0],
            locator[1],
            locator[2],
            locator[3],
        ]
    )


def _locator_order(locator: Sequence[Any]) -> tuple[bytes, int]:
    return (str(locator[2]).encode("utf-8"), int(locator[3]))


def _eligible(evaluated: Sequence[Mapping[str, Any]], stratum: str) -> list[Mapping[str, Any]]:
    if stratum == "B_science_census":
        return [r for r in evaluated if _is_b(r, "essential_science")]
    if stratum == "D_only_science_census":
        return [r for r in evaluated if _is_d_only(r, "essential_science")]
    if stratum == "B_practical_survivor":
        return [r for r in evaluated if _is_b_survivor(r, "essential_practical")]
    if stratum == "B_practical_loss":
        return [r for r in evaluated if _is_b_loss(r, "essential_practical")]
    if stratum == "D_only_practical":
        return [r for r in evaluated if _is_d_only(r, "essential_practical")]
    if stratum == "B_prose_survivor":
        return [r for r in evaluated if _is_b_survivor(r, "essential_prose")]
    if stratum == "B_prose_loss":
        return [r for r in evaluated if _is_b_loss(r, "essential_prose")]
    if stratum == "D_only_prose":
        return [r for r in evaluated if _is_d_only(r, "essential_prose")]
    raise SelectionError(f"unknown stratum: {stratum}")


def select_text(evaluated: Sequence[Mapping[str, Any]], crawls: Sequence[str]) -> dict[str, Any]:
    """Run the frozen selection; returns the manifest body (no digest yet)."""
    unknown = [c for c in crawls if c not in frozen.CRAWL_ORDER]
    if unknown:
        raise SelectionError(f"crawls outside the frozen order: {unknown}")
    order = [c for c in frozen.CRAWL_ORDER if c in crawls]
    owned: set[tuple[str, int]] = set()
    cells: list[dict[str, Any]] = []
    selected: list[Mapping[str, Any]] = []
    for stratum, kind, per_crawl in frozen.TEXT_STRATA:
        candidates = _eligible(evaluated, stratum)
        if kind == "census":
            expected = frozen.CENSUS_EXPECTED[stratum]
            if len(candidates) != expected:
                raise SelectionError(
                    f"{stratum}: census is {len(candidates)}, not exactly {expected}: STOP"
                )
            ordered = sorted(candidates, key=lambda r: _locator_order(_locator_of(r)))
            for row in ordered:
                locator = _locator_of(row)
                key = (str(locator[2]), int(locator[3]))
                if key in owned:
                    raise SelectionError(f"census overlap on already-owned {key}: STOP")
                owned.add(key)
                selected.append(row)
            cells.append(
                {
                    "stratum": stratum,
                    "requested": expected,
                    "eligible": len(candidates),
                    "conflicts": 0,
                    "selected": len(ordered),
                    "shortfall": 0,
                    "identities": [_locator_of(r) for r in ordered],
                }
            )
            continue
        assert per_crawl is not None
        per_crawl_cells: list[dict[str, Any]] = []

        def _key(row: Mapping[str, Any]) -> tuple[str, int]:
            locator = _locator_of(row)
            return (str(locator[2]), int(locator[3]))

        for crawl in order:
            in_crawl = [r for r in candidates if r["crawl"] == crawl]
            conflicts = sum(1 for r in in_crawl if _key(r) in owned)
            available = [r for r in in_crawl if _key(r) not in owned]
            ranked = sorted(
                available,
                key=lambda r: (
                    rank_digest(stratum, crawl, _locator_of(r)),
                    _locator_order(_locator_of(r)),
                ),
            )
            take = ranked[:per_crawl]
            for row in take:
                owned.add(_key(row))
                selected.append(row)
            per_crawl_cells.append(
                {
                    "crawl": crawl,
                    "requested": per_crawl,
                    "eligible": len(in_crawl),
                    "conflicts": conflicts,
                    "selected": len(take),
                    "shortfall": per_crawl - len(take),
                    "identities": [_locator_of(r) for r in take],
                }
            )
        cells.append({"stratum": stratum, "crawls": per_crawl_cells})
    if len(selected) > frozen.TEXT_SELECTION_MAX:
        raise SelectionError(f"selected {len(selected)} exceeds 118")
    keys = [_key(r) for r in selected]
    if len(set(keys)) != len(keys):
        raise SelectionError("repeated locator in selection")
    return {"cells": cells, "selected": selected, "total": len(selected)}


def build_manifest(
    selection: Mapping[str, Any],
    *,
    evaluator_hash: str,
    command: str,
    exit_status: int,
    budget_snapshot: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Locator-selection manifest body; digest added by ``seal`` below."""
    return {
        "kind": "essential_web_evidence_v2_text_selection",
        "protocol_version": frozen.PROTOCOL_VERSION,
        "freeze_digest": frozen.FREEZE_DIGEST,
        "policy_digest": frozen.POLICY_DIGEST,
        "repository": frozen.REPOSITORY,
        "revision": frozen.REVISION,
        "bundle_digest": frozen.DEV_BUNDLE_DIGEST,
        "execution_digest": frozen.DEV_EXECUTION_DIGEST,
        "combined_sha256": frozen.DEV_COMBINED_SHA256,
        "evaluator_code_sha256": evaluator_hash,
        "text_selection_seed": frozen.TEXT_SELECTION_SEED,
        "total_selected": selection["total"],
        "selection_max": frozen.TEXT_SELECTION_MAX,
        "cells": selection["cells"],
        "command": command,
        "exit_status": exit_status,
        "budget": dict(budget_snapshot) if budget_snapshot else {},
    }


def seal(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Attach the canonical digest; byte hashes bind the written file."""
    sealed = dict(manifest)
    sealed["digest"] = canonical.self_digest(manifest)
    return sealed
