"""Offline, text-free replay of one Batch-3 unit through the production row path.

Resolves the unit from the authoritative batch record and resume state,
rehashes its local source file against the identity record, then streams it
with the exact production iterator (``selected_payloads`` with the unit job's
limits) through the three production ``essential_web_bnormal`` adapters.

Unlike the production worker it does not stop at the malformed allowance: it
records the row at which ``MalformedCounter`` first raises and keeps counting,
so the whole file is measured. Output holds counts, reason codes, field paths,
value types and bounded label summaries only; document text is never read
into the report, printed or written.

Usage (after dot-sourcing ``scripts/operator_storage.ps1``)::

    no_network.py replay_unit.py --batch 3 --rank 110 --output <json>
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
import time
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "src"))

fast_driver: Any = importlib.import_module("essential_web_fast")

from xlm.data.acquisition.source_parquet import file_sha256, selected_payloads  # noqa: E402
from xlm.data.adapters.columns import columns_for  # noqa: E402
from xlm.data.adapters.essential_web_selector import FrozenEssentialWebSelector  # noqa: E402
from xlm.data.adapters.malformed import MalformedCounter, MalformedLimitError  # noqa: E402
from xlm.data.adapters.mix01_adapters import (  # noqa: E402
    ADAPTERS_BY_ID,
    EssentialWebMalformedRowError,
    RecordRejectedError,
)
from xlm.data.sources import essential_web_bulk as bulk  # noqa: E402
from xlm.data.sources import essential_web_local as local  # noqa: E402

LABEL_SUMMARY_CHARS = 48
BYTES_PER_TOKEN = 4.0


def summarize(value: Any, *, is_label: bool) -> dict[str, Any]:
    """Type, encoded size and (for taxonomy labels/codes only) a bounded repr."""
    out: dict[str, Any] = {"type": type(value).__name__}
    if isinstance(value, str):
        out["utf8_bytes"] = len(value.encode("utf-8", "surrogatepass"))
        if is_label:
            shown = value[:LABEL_SUMMARY_CHARS]
            out["summary"] = shown + ("..." if len(value) > len(shown) else "")
    elif isinstance(value, (int, float, bool)) or value is None:
        out["summary"] = repr(value)
    elif isinstance(value, dict):
        out["keys"] = sorted(str(k) for k in value)[:16]
    elif isinstance(value, list):
        out["length"] = len(value)
    return out


def diagnose(
    record: dict[str, Any],
    evaluator: Any,
    spec: Mapping[str, Any],
    error: EssentialWebMalformedRowError,
) -> dict[str, Any]:
    """Text-free cause of one malformed row."""
    fields, reasons, unknowns = evaluator.validate_row(record, spec)
    detail: dict[str, Any] = {"message": str(error).split(":", 1)[0]}
    cause = error.__cause__
    if cause is not None:
        # Adapter MissingFieldError/UnicodeError messages name fields, never values.
        detail["stage"] = "renderer"
        detail["cause"] = type(cause).__name__
        detail["cause_message"] = str(cause)[:240]
        return detail
    detail["stage"] = "validity"
    detail["reasons"] = list(reasons)
    paths: dict[str, Any] = {}
    for reason in reasons:
        field = reason.rsplit(":", 1)[-1] if ":" in reason else None
        if reason.endswith("fdc") or "fdc" in reason:
            field = "f"
        elif "english" in reason:
            field = "e"
        if field is None or field not in evaluator._FIELD_PATHS:
            continue
        path = evaluator._FIELD_PATHS[field]
        status, value = evaluator.get_path(record, path)
        paths[path] = {"status": status, **summarize(value, is_label=field != "e")}
    detail["fields"] = paths
    # What the frozen gate says about the validated remainder (never invented).
    gate_fields = ("e", "a", "m", "t", "d")
    if all(name in fields for name in gate_fields):
        gate_reasons = evaluator.gate_gn(fields, spec)
        detail["gate_on_valid_fields"] = gate_reasons or "pass"
    else:
        detail["gate_on_valid_fields"] = "undetermined: a gate field is itself invalid"
    detail["unknown_fields"] = sorted(unknowns)
    return detail


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch", type=int, required=True)
    parser.add_argument("--rank", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)

    data_root = Path(os.environ["XLM_DATA_ROOT"])
    scratch_root = Path(os.environ["XLM_SCRATCH_ROOT"])
    campaign = fast_driver.load_campaign(
        fast_driver.REPO / fast_driver.FAST_DIR / "campaign.json", data_root, scratch_root
    )
    record = fast_driver.batch_record(campaign, args.batch)
    plan = fast_driver.load_acquisition_plan(campaign.batch_dir(args.batch) / "batch.plan.json")
    if plan.plan_hash != record["plan_hash"] or plan.selected_files != record["files"]:
        raise SystemExit("authorized plan differs from batch")
    resume = fast_driver.recovery.resume_state(campaign, record)
    pending = [p for p in resume["remaining"] if int(p["rank"]) == args.rank]
    if len(pending) != 1:
        raise SystemExit(f"rank {args.rank} is not an unsealed unit of batch {args.batch}")
    name = str(pending[0]["file"])
    key = f"f{args.rank:05d}"
    durable = campaign.raw_path(name)
    retained = fast_driver.load_durable_source(durable)
    scratch_dir = campaign.scratch(f"b{args.batch:04d}")
    if retained is not None:
        path, identity, location = durable, retained, "durable"
    else:
        state = json.loads((scratch_dir / f"{key}.state.json").read_text(encoding="utf-8"))
        if not state.get("complete"):
            raise SystemExit(f"{key} has no complete local copy")
        path, identity, location = scratch_dir / f"{key}.parquet.part", state, "scratch"
    started = time.monotonic()
    digest, length = file_sha256(path)
    if (digest, length) != (identity["sha256"], identity["length"]):
        raise SystemExit(f"{key}: local file does not rehash to its identity")
    hash_seconds = time.monotonic() - started

    job = fast_driver.unit_job(
        campaign, plan, name, Path("unused"), batch=args.batch, rank=args.rank, source=retained
    )
    limits = job["limits"]
    layout = local.check_layout(path, name, limits)
    views = list(job["views"])
    adapters = {view: ADAPTERS_BY_ID[local.ADAPTER_ID](view) for view in views}
    selector = FrozenEssentialWebSelector.load()
    evaluator, spec = selector._evaluator, selector._spec
    counters = {view: MalformedCounter() for view in views}
    first_abort: dict[str, Any] = {}
    finals: Counter[str] = Counter()
    stages: Counter[str] = Counter()
    codes: dict[str, Counter[str]] = {view: Counter() for view in views}
    docs: Counter[str] = Counter()
    canonical: Counter[str] = Counter()
    malformed_rows: list[dict[str, Any]] = []
    rows = 0
    started = time.monotonic()
    for row_index, payload in selected_payloads(
        path,
        source_file=name,
        locator={
            "source_id": job["source_id"],
            "repository": job["repository"],
            "revision": job["revision"],
            "selection_hash": job["selection_hash"],
        },
        etag=str(identity["etag"]),
        columns=columns_for(local.ADAPTER_ID, bulk.PLAN_VIEW),
        max_record_bytes=int(limits["max_record_bytes"]),
        max_parser_bytes=int(limits["max_parser_bytes"]),
        max_decoded_bytes=int(limits["max_decoded_bytes_per_file"]),
        row_range=(0, int(layout["rows"])),
        counters={},
    ):
        rows += 1
        row = json.loads(payload)
        decision = selector.decide(row)
        finals[decision.final] += 1
        stages[decision.stage] += 1
        malformed_error: EssentialWebMalformedRowError | None = None
        for view in views:
            try:
                document = adapters[view].adapt(
                    row, source_file=name, source_row=row_index, source_revision=plan.revision
                )
            except RecordRejectedError as exc:
                bad = isinstance(exc, EssentialWebMalformedRowError)
                if isinstance(exc, EssentialWebMalformedRowError):
                    malformed_error = exc
                codes[view][type(exc).__name__] += 1
                observed = bad
            else:
                docs[view] += 1
                canonical[view] += document.utf8_byte_count
                observed = False
            counter = counters[view]
            try:
                counter.observe(observed)
            except MalformedLimitError:
                if view not in first_abort:
                    first_abort[view] = {
                        "row_index": row_index,
                        "rows_observed": counter.rows,
                        "malformed_observed": counter.malformed,
                    }
                # Keep measuring the whole file: continue without the abort.
                counters[view] = _Tally(counter.rows, counter.malformed)
        if malformed_error is not None:
            entry = {
                "row_index": row_index,
                "selector_final": decision.final,
                "selector_stage": decision.stage,
                **diagnose(row, evaluator, spec, malformed_error),
            }
            malformed_rows.append(entry)
    seconds = time.monotonic() - started
    if rows != int(layout["rows"]):
        raise SystemExit("decoded rows differ from the footer row count")

    grouped: Counter[str] = Counter()
    for entry in malformed_rows:
        grouped[
            " + ".join(entry["reasons"])
            if entry["stage"] == "validity"
            else f"renderer:{entry['cause']}"
        ] += 1
    per_reason: Counter[str] = Counter()
    for entry in malformed_rows:
        for reason in entry.get("reasons", [f"renderer:{entry.get('cause')}"]):
            per_reason[reason] += 1
    unknown_values: Counter[str] = Counter()
    for entry in malformed_rows:
        for path_name, info in entry.get("fields", {}).items():
            if "summary" in info:
                unknown_values[f"{path_name} = {info['summary']!r}"] += 1
    gate_outcomes: Counter[str] = Counter(
        "pass"
        if e.get("gate_on_valid_fields") == "pass"
        else (
            "gate_rejects:" + ",".join(e["gate_on_valid_fields"])
            if isinstance(e.get("gate_on_valid_fields"), list)
            else str(e.get("gate_on_valid_fields"))
        )
        for e in malformed_rows
    )
    malformed = len(malformed_rows)
    report = {
        "batch": args.batch,
        "rank": args.rank,
        "key": key,
        "inventory_rank": args.rank,
        "source_file": name,
        "repository": plan.repository,
        "revision": plan.revision,
        "local_path": str(path),
        "local_location": location,
        "sha256": digest,
        "length": length,
        "etag": identity["etag"],
        "sha256_verified": True,
        "hash_seconds": round(hash_seconds, 3),
        "footer_rows": int(layout["rows"]),
        "row_groups": len(layout["groups"]),
        "rows": rows,
        "selector_finals": dict(sorted(finals.items())),
        "selector_stages": dict(sorted(stages.items())),
        "malformed": malformed,
        "malformed_fraction": malformed / rows if rows else 0.0,
        "first_abort": first_abort,
        "grouped_reasons": dict(grouped.most_common()),
        "per_reason": dict(per_reason.most_common()),
        "offending_values": dict(unknown_values.most_common()),
        "gate_on_valid_fields": dict(gate_outcomes.most_common()),
        "views": {
            view: {
                "documents": docs[view],
                "canonical_bytes": canonical[view],
                "estimated_tokens_4_bytes": canonical[view] / BYTES_PER_TOKEN,
                "rejection_codes": dict(sorted(codes[view].items())),
            }
            for view in views
        },
        "replay_seconds": round(seconds, 3),
        "malformed_rows": malformed_rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temp = args.output.with_suffix(".tmp")
    temp.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temp, args.output)
    brief = {k: v for k, v in report.items() if k != "malformed_rows"}
    print(json.dumps(brief, indent=2, sort_keys=True))
    return 0


class _Tally(MalformedCounter):
    """Post-abort counter: keeps counting, never raises again."""

    def __init__(self, rows: int, malformed: int) -> None:
        super().__init__(rows, malformed)

    def observe(self, malformed: bool) -> None:
        self.rows += 1
        self.malformed += int(malformed)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
