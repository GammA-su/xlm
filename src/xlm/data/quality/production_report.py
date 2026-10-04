"""Deterministic, content-free Phase-C artifacts and the production receipt.

Every artifact is a pure function of the committed per-file units (manifest order),
the production binding and the frozen policy. It is byte-identical for every worker
count and resume pattern, and ``clean-production-verify`` re-derives it. Building the
artifacts also re-proves the approved dry run's exact global, per-component and
per-rule accounting (:func:`~xlm.data.quality.production_approval.compare_accounting`).
A mismatch is fatal: no artifact or receipt is written.

No artifact holds document text or metadata values. The dropped-membership rows hold
only a locator (input path, row, byte offset, line bytes), the canonical text byte
count, the SHA-256 of the ``doc_id`` and of the raw row bytes, and the fired rule ids.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from xlm.data.evidence_v2 import canonical
from xlm.data.quality.cleaning import CleanStats
from xlm.data.quality.cleaning_policy import CompiledPolicy
from xlm.data.quality.cleaning_report import combinations, rule_impacts, summarize
from xlm.data.quality.production_approval import (
    ApprovedDryRun,
    accounting_from_stats,
    compare_accounting,
    outcome_counts,
)
from xlm.data.quality.receipt import check_envelope
from xlm.data.quality.scan import QualityError

PHASE = "C_PRODUCTION_CLEANING"
DROPPED_MEMBERSHIP = "dropped-membership.jsonl"
CLEANED_INVENTORY = "cleaned-inventory.json"
ARTIFACTS = (
    "cleaning-production-summary.md",
    "cleaning-production.json",
    "cleaning-by-component.json",
    "cleaning-by-rule.json",
    DROPPED_MEMBERSHIP,
    CLEANED_INVENTORY,
)
RECEIPT_KIND = "xlm_quality_cleaning_production_receipt"
RECEIPT_SCHEMA = 1
SEMANTICS = (
    "DROP-only. KEEP rows are the original input JSONL line bytes, unchanged and in input "
    "order; DROP rows are omitted. No transformation, normalization, reserialization, "
    "truncation or repair of any kept row."
)
EMPTY_OUTPUT_POLICY = (
    "one output file per input file; an input whose rows are all DROP yields a zero-byte "
    "output file"
)
MEMBERSHIP_KEYS = frozenset(
    {
        "ordinal",
        "input_path",
        "component",
        "row",
        "offset",
        "line_bytes",
        "canonical_bytes",
        "doc_id_sha256",
        "row_sha256",
        "decision",
        "rules",
    }
)
RECEIPT_KEYS = frozenset(
    {
        "kind",
        "schema_version",
        "status",
        "phase",
        "run_phase",
        "input_corpus_modified",
        "cleaned_corpus_written",
        "actions_executed",
        "binding",
        "binding_digest",
        "input_manifest",
        "cleaning_policy",
        "approved_dry_run",
        "implementation",
        "output_root",
        "source_files",
        "output_files",
        "artifacts",
        "dropped_membership_sha256",
        "cleaned_inventory_sha256",
        "accounting",
        "dry_run_accounting_match",
        "result_digest",
        "envelope",
        "execution",
        "digest",
    }
)
EXECUTION_INTS = (
    "files_cleaned",
    "files_resumed",
    "files_adopted",
    "cleaned_documents",
    "cleaned_file_bytes",
    "workers",
    "peak_tasks_in_flight",
    "peak_process_tree_rss_bytes",
    "supervisor_samples",
    "corpus_output_bytes",
    "state_bytes_before_receipt",
)
EXECUTION_FLOATS = ("wall_seconds", "clean_seconds", "file_mb_per_s", "documents_per_s")
EXECUTION_NOTE = "execution facts are operational and excluded from result_digest"


def _json(obj: Any) -> bytes:
    return (
        json.dumps(obj, sort_keys=True, indent=1, ensure_ascii=False, allow_nan=False) + "\n"
    ).encode("utf-8")


def _sha(value: Any) -> bool:
    return type(value) is str and len(value) == 64 and set(value) <= set("0123456789abcdef")


def header(binding: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "phase": PHASE,
        "content_free": True,
        "completion": (
            "NOT a completion signal: valid only together with a "
            "cleaning-production-receipt.json that validates (`clean-production-verify`)"
        ),
        "binding_digest": binding["digest"],
        "input_manifest_digest": binding["input_manifest"]["digest"],
        "cleaning_policy": dict(binding["cleaning_policy"]),
        "approved_dry_run": {
            "receipt_digest": binding["approved_dry_run"]["receipt_digest"],
            "result_digest": binding["approved_dry_run"]["result_digest"],
        },
        "semantics": SEMANTICS,
    }


def _counts() -> dict[str, dict[str, int]]:
    return {
        "input": {"documents": 0, "canonical_bytes": 0, "file_bytes": 0},
        "keep": {"documents": 0, "canonical_bytes": 0},
        "drop": {"documents": 0, "canonical_bytes": 0, "line_bytes": 0},
        "output": {"documents": 0, "canonical_bytes": 0, "file_bytes": 0},
    }


def _add(target: dict[str, dict[str, int]], entry: Mapping[str, Any]) -> None:
    for group in target:
        for key in target[group]:
            target[group][key] += int(entry[group][key])


def inventory_entry(unit: Mapping[str, Any]) -> dict[str, Any]:
    item, output, acc = unit["file"], unit["output"], unit["accounting"]
    return {
        "ordinal": item["ordinal"],
        "logical_id": item["path"],
        "source_key": item["source_key"],
        "component": item["component"],
        "view": item["view"],
        "upstream_component": item["upstream_component"],
        "input": {
            "path": item["path"],
            "documents_sha256": item["documents_sha256"],
            "file_bytes": item["file_bytes"],
            "canonical_bytes": item["canonical_bytes"],
            "documents": item["documents"],
        },
        "output": {
            "path": output["path"],
            "sha256": output["sha256"],
            "file_bytes": output["file_bytes"],
            "canonical_bytes": output["canonical_bytes"],
            "documents": output["documents"],
        },
        "dropped": {
            "documents": acc["dropped_documents"],
            "canonical_bytes": acc["dropped_canonical_bytes"],
            "line_bytes": acc["dropped_line_bytes"],
        },
    }


def _unit_counts(unit: Mapping[str, Any]) -> dict[str, dict[str, int]]:
    item, output, acc = unit["file"], unit["output"], unit["accounting"]
    return {
        "input": {
            "documents": item["documents"],
            "canonical_bytes": item["canonical_bytes"],
            "file_bytes": item["file_bytes"],
        },
        "keep": {
            "documents": acc["kept_documents"],
            "canonical_bytes": acc["kept_canonical_bytes"],
        },
        "drop": {
            "documents": acc["dropped_documents"],
            "canonical_bytes": acc["dropped_canonical_bytes"],
            "line_bytes": acc["dropped_line_bytes"],
        },
        "output": {
            "documents": output["documents"],
            "canonical_bytes": output["canonical_bytes"],
            "file_bytes": output["file_bytes"],
        },
    }


def build_production_artifacts(
    binding: Mapping[str, Any],
    units: Iterable[Mapping[str, Any]],
    policy: CompiledPolicy,
    approved: ApprovedDryRun,
) -> tuple[dict[str, bytes], str, dict[str, Any]]:
    """All artifacts, the worker-independent result digest and the exact accounting.

    Refuses (fatal) unless the re-evaluated decisions reproduce the approved dry run's
    global, component and rule accounting exactly.
    """
    ruleset = policy.params.ruleset
    global_stats = CleanStats(ruleset)
    by_component: dict[str, CleanStats] = {}
    totals = _counts()
    component_counts: dict[str, dict[str, dict[str, int]]] = {}
    inventory: list[dict[str, Any]] = []
    membership: list[bytes] = []
    outputs: list[dict[str, Any]] = []
    for unit in units:
        stats = CleanStats.from_json(unit["statistics"], ruleset)
        component = unit["file"]["component"]
        global_stats.merge(stats)
        by_component.setdefault(component, CleanStats(ruleset)).merge(stats)
        counts = _unit_counts(unit)
        _add(totals, counts)
        _add(component_counts.setdefault(component, _counts()), counts)
        inventory.append(inventory_entry(unit))
        outputs.append(
            {
                "path": unit["output"]["path"],
                "sha256": unit["output"]["sha256"],
                "file_bytes": unit["output"]["file_bytes"],
                "documents": unit["output"]["documents"],
            }
        )
        membership += [canonical.canonical_bytes(row) + b"\n" for row in unit["dropped"]]
    if not inventory:
        raise QualityError("no production units")
    global_stats.check()
    observed = accounting_from_stats(global_stats, by_component, policy)
    compared = compare_accounting(observed, approved.accounting)
    outcome = outcome_counts(global_stats)
    if (
        outcome["review_documents"] != 0
        or outcome["drop_documents"] != totals["drop"]["documents"]
        or outcome["drop_canonical_bytes"] != totals["drop"]["canonical_bytes"]
        or outcome["keep_documents"] != totals["keep"]["documents"]
        or outcome["keep_canonical_bytes"] != totals["keep"]["canonical_bytes"]
        or len(membership) != totals["drop"]["documents"]
    ):
        raise QualityError("production accounting is internally inconsistent")
    head = header(binding)
    match = {
        "status": "IDENTICAL",
        "compared": compared,
        "per_file": (
            "every input file's complete decision statistics equal the approved dry-run unit "
            "(checked before its output was published)"
        ),
    }
    artifacts: dict[str, bytes] = {}
    artifacts[DROPPED_MEMBERSHIP] = b"".join(membership)
    components = {name: component_counts[name] for name in sorted(component_counts)}
    artifacts[CLEANED_INVENTORY] = _json(
        {
            "kind": "xlm_cleaned_corpus_inventory_v1",
            **head,
            "mapping": dict(binding["mapping"]),
            "empty_output_policy": EMPTY_OUTPUT_POLICY,
            "files": inventory,
            "components": components,
            "totals": totals,
        }
    )
    artifacts["cleaning-production.json"] = _json(
        {
            "kind": "xlm_quality_cleaning_production_v1",
            **head,
            "global": totals,
            "components": components,
            "dry_run_accounting_match": match,
            "guardrails": observed["guardrails"],
            "global_decision_summary": summarize(global_stats),
            "dropped_membership_rows": len(membership),
        }
    )
    artifacts["cleaning-by-component.json"] = _json(
        {
            "kind": "xlm_quality_cleaning_production_by_component_v1",
            **head,
            "components": {
                name: {"counts": components[name], "summary": summarize(by_component[name])}
                for name in sorted(by_component)
            },
        }
    )
    global_impacts = rule_impacts(global_stats)
    artifacts["cleaning-by-rule.json"] = _json(
        {
            "kind": "xlm_quality_cleaning_production_by_rule_v1",
            **head,
            "rules": {
                name: {
                    **global_impacts[name],
                    "components": {
                        comp: rule_impacts(stats)[name]
                        for comp, stats in sorted(by_component.items())
                    },
                }
                for name in ruleset.ids
            },
            "drop_rule_combinations": [
                row for row in combinations(global_stats) if row["outcome"] == "DROP"
            ],
        }
    )
    artifacts["cleaning-production-summary.md"] = summary_markdown(
        head, totals, components, global_stats, len(membership)
    )
    result = canonical.digest(
        {name: hashlib.sha256(data).hexdigest() for name, data in sorted(artifacts.items())}
    )
    accounting = {"global": totals, "components": components}
    return artifacts, result, {"accounting": accounting, "outputs": outputs, "match": match}


def summary_markdown(
    head: Mapping[str, Any],
    totals: Mapping[str, Any],
    components: Mapping[str, Any],
    global_stats: CleanStats,
    drops: int,
) -> bytes:
    def row(scope: str, c: Mapping[str, Any]) -> str:
        return (
            f"| {scope} | {c['input']['documents']:,} | {c['keep']['documents']:,} | "
            f"{c['keep']['canonical_bytes']:,} | {c['drop']['documents']:,} | "
            f"{c['drop']['canonical_bytes']:,} | {c['output']['file_bytes']:,} |"
        )

    lines = [
        "# XLM Phase-C production cleaning (content-free)",
        "",
        f"Policy `{head['cleaning_policy']['version']}` "
        f"(`{head['cleaning_policy']['digest'][:16]}`), approved dry run result "
        f"`{head['approved_dry_run']['result_digest'][:16]}`, input manifest "
        f"`{head['input_manifest_digest'][:16]}`.",
        "",
        SEMANTICS,
        "",
        "Decisions reproduce the approved dry run exactly (per file before publication; "
        "global, per component and per rule before the receipt).",
        "",
        "## Documents and canonical UTF-8 bytes",
        "",
        "| scope | input docs | KEEP docs | KEEP bytes | DROP docs | DROP bytes "
        "| output file bytes |",
        "|---|---:|---:|---:|---:|---:|---:|",
        row("global", totals),
        *(row(name, components[name]) for name in sorted(components)),
        "",
        "## DROP rules (global marginal documents / canonical bytes)",
        "",
        "| rule | docs | bytes |",
        "|---|---:|---:|",
    ]
    a = global_stats.arrays
    for n, name in enumerate(global_stats.ruleset.ids):
        lines.append(f"| {name} | {int(a['rule'][n, 0]):,} | {int(a['rule'][n, 1]):,} |")
    lines += [
        "",
        f"Dropped-membership rows: {drops:,} (locators and digests only).",
        "",
        "The input corpus was not modified. Every existing C05 dedup, contamination, "
        "lineage, split and membership artifact is stale for the cleaned corpus: build "
        "the cleaned manifest (`clean-production-manifest`) after `clean-production-verify`, "
        "audit it independently and rerun C05 from scratch.",
        "",
    ]
    return "\n".join(lines).encode("utf-8")


def drop_counts(stats: CleanStats) -> dict[str, list[int]]:
    a = stats.arrays
    return {
        name: [int(a["rule"][n, 0]), int(a["rule"][n, 1])]
        for n, name in enumerate(stats.ruleset.ids)
    }


# -- receipt ----------------------------------------------------------------------------------


def build_receipt(
    *,
    binding: Mapping[str, Any],
    manifest_path: Path,
    policy_path: Path,
    approved: ApprovedDryRun,
    implementation: Mapping[str, str],
    output_root: Path,
    sources: list[dict[str, Any]],
    built: Mapping[str, Any],
    artifacts: Mapping[str, bytes],
    result_digest: str,
    envelope: Mapping[str, Any],
    execution: Mapping[str, Any],
) -> bytes:
    entries = {
        name: {
            "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "records": data.count(b"\n"),
        }
        for name, data in sorted(artifacts.items())
    }
    body: dict[str, Any] = {
        "kind": RECEIPT_KIND,
        "schema_version": RECEIPT_SCHEMA,
        "status": "COMPLETE",
        "phase": "COMPLETE",
        "run_phase": PHASE,
        "input_corpus_modified": False,
        "cleaned_corpus_written": True,
        "actions_executed": ["DROP"],
        "binding": dict(binding),
        "binding_digest": binding["digest"],
        "input_manifest": {
            "path": manifest_path.resolve().as_posix(),
            "digest": binding["input_manifest"]["digest"],
            "file_sha256": binding["input_manifest"]["file_sha256"],
        },
        "cleaning_policy": {
            "path": policy_path.resolve().as_posix(),
            **binding["cleaning_policy"],
        },
        "approved_dry_run": {
            "path": approved.path.resolve().as_posix(),
            **binding["approved_dry_run"],
        },
        "implementation": {
            "code_commit": implementation["code_commit"],
            "code_identity": implementation["code_identity"],
            "dependency_sha256": implementation["dependency_sha256"],
        },
        "output_root": binding["output_root"],
        "source_files": sources,
        "output_files": list(built["outputs"]),
        "artifacts": entries,
        "dropped_membership_sha256": entries[DROPPED_MEMBERSHIP]["sha256"],
        "cleaned_inventory_sha256": entries[CLEANED_INVENTORY]["sha256"],
        "accounting": dict(built["accounting"]),
        "dry_run_accounting_match": dict(built["match"]),
        "result_digest": result_digest,
        "envelope": dict(envelope),
        "execution": dict(execution),
    }
    body["digest"] = canonical.digest(body)
    return canonical.canonical_bytes(body)


def _require(condition: bool, what: str) -> None:
    if not condition:
        raise QualityError(f"production receipt invalid: {what}")


def validate_receipt(receipt: Any) -> dict[str, Any]:
    """Exact schema, self-digest, bindings, artifact hashes and accounting identities."""
    _require(isinstance(receipt, dict) and set(receipt) == RECEIPT_KEYS, "field set")
    _require(receipt["kind"] == RECEIPT_KIND, "kind")
    _require(receipt["schema_version"] == RECEIPT_SCHEMA, "schema version")
    _require(receipt["status"] == "COMPLETE" and receipt["phase"] == "COMPLETE", "status")
    _require(receipt["run_phase"] == PHASE, "run phase")
    _require(receipt["input_corpus_modified"] is False, "input_corpus_modified")
    _require(receipt["cleaned_corpus_written"] is True, "cleaned_corpus_written")
    _require(receipt["actions_executed"] == ["DROP"], "actions_executed")
    _require(receipt["digest"] == canonical.self_digest(receipt), "self-digest")
    binding = receipt["binding"]
    _require(isinstance(binding, dict), "binding")
    _require(binding.get("digest") == canonical.self_digest(binding), "binding digest")
    _require(receipt["binding_digest"] == binding["digest"], "binding digest reference")
    try:
        _require(
            receipt["input_manifest"]["digest"] == binding["input_manifest"]["digest"]
            and receipt["input_manifest"]["file_sha256"]
            == binding["input_manifest"]["file_sha256"],
            "input manifest binding",
        )
        policy = {k: v for k, v in receipt["cleaning_policy"].items() if k != "path"}
        _require(policy == binding["cleaning_policy"], "cleaning policy binding")
        dry = {k: v for k, v in receipt["approved_dry_run"].items() if k != "path"}
        _require(dry == binding["approved_dry_run"], "approved dry-run binding")
        _require(
            receipt["implementation"]["code_identity"]
            == binding["implementation"]["code_identity"],
            "implementation identity",
        )
        _require(receipt["output_root"] == binding["output_root"], "output root")
        sources, outputs = receipt["source_files"], receipt["output_files"]
        files = binding["input_manifest"]["files"]
        _require(isinstance(sources, list) and len(sources) == files, "source count")
        _require(isinstance(outputs, list) and len(outputs) == files, "output count")
        for entry in [*sources, *outputs]:
            _require(
                _sha(entry.get("documents_sha256", entry.get("sha256"))), "file SHA-256 schema"
            )
        artifacts = receipt["artifacts"]
        _require(isinstance(artifacts, dict) and set(artifacts) == set(ARTIFACTS), "artifact set")
        for entry in artifacts.values():
            _require(
                set(entry) == {"bytes", "sha256", "records"} and _sha(entry["sha256"]),
                "artifact schema",
            )
        expected = canonical.digest({name: artifacts[name]["sha256"] for name in sorted(artifacts)})
        _require(receipt["result_digest"] == expected, "result digest")
        _require(
            receipt["dropped_membership_sha256"] == artifacts[DROPPED_MEMBERSHIP]["sha256"]
            and receipt["cleaned_inventory_sha256"] == artifacts[CLEANED_INVENTORY]["sha256"],
            "membership/inventory digests",
        )
        totals = receipt["accounting"]["global"]
        _require(
            totals["keep"]["documents"] + totals["drop"]["documents"]
            == totals["input"]["documents"]
            == binding["input_manifest"]["documents"]
            and totals["keep"]["canonical_bytes"] + totals["drop"]["canonical_bytes"]
            == totals["input"]["canonical_bytes"]
            == binding["input_manifest"]["canonical_bytes"]
            and totals["output"]["documents"] == totals["keep"]["documents"]
            and totals["output"]["file_bytes"]
            == totals["input"]["file_bytes"] - totals["drop"]["line_bytes"]
            and totals["drop"]["documents"] == artifacts[DROPPED_MEMBERSHIP]["records"]
            and sum(o["file_bytes"] for o in outputs) == totals["output"]["file_bytes"]
            and sum(o["documents"] for o in outputs) == totals["output"]["documents"],
            "accounting identities",
        )
        _require(receipt["dry_run_accounting_match"]["status"] == "IDENTICAL", "dry-run match")
        check_envelope(receipt["envelope"])
        execution = receipt["execution"]
        _require(
            set(execution) == {*EXECUTION_INTS, *EXECUTION_FLOATS, "free_space", "note"},
            "execution schema",
        )
        for name in EXECUTION_INTS:
            _require(type(execution[name]) is int and execution[name] >= 0, f"execution {name}")
        for name in EXECUTION_FLOATS:
            _require(type(execution[name]) is float and execution[name] >= 0, f"execution {name}")
        _require(execution["note"] == EXECUTION_NOTE, "execution note")
        envelope = receipt["envelope"]
        _require(
            execution["files_cleaned"] + execution["files_resumed"] == files, "files accounted"
        )
        _require(
            execution["peak_process_tree_rss_bytes"] <= envelope["max_rss_bytes"]
            and execution["wall_seconds"] <= envelope["deadline_seconds"]
            and execution["corpus_output_bytes"] <= envelope["max_output_bytes"]
            and execution["corpus_output_bytes"] == totals["output"]["file_bytes"]
            and all(
                v["min_observed_free_bytes"] >= v["reserve_bytes"] for v in execution["free_space"]
            ),
            "execution facts exceed the envelope",
        )
    except (KeyError, TypeError, AttributeError):
        raise QualityError("production receipt invalid: schema") from None
    validated: dict[str, Any] = receipt
    return validated


def membership_rows(raw: bytes) -> list[dict[str, Any]]:
    """Strictly parsed dropped-membership rows (exact content-free schema)."""
    rows: list[dict[str, Any]] = []
    for line in raw.splitlines():
        try:
            row = canonical.loads_bytes_strict(line)
        except ValueError:
            raise QualityError("dropped membership is not strict JSON lines") from None
        if not isinstance(row, dict) or set(row) != MEMBERSHIP_KEYS:
            raise QualityError("dropped membership row schema")
        if canonical.canonical_bytes(row) != line or row["decision"] != "DROP":
            raise QualityError("dropped membership row schema")
        for name in ("ordinal", "row", "offset", "line_bytes", "canonical_bytes"):
            if type(row[name]) is not int or row[name] < 0:
                raise QualityError("dropped membership row schema")
        if not (_sha(row["doc_id_sha256"]) and _sha(row["row_sha256"])):
            raise QualityError("dropped membership row schema")
        if not isinstance(row["rules"], list) or not row["rules"]:
            raise QualityError("dropped membership row schema")
        rows.append(row)
    keys = [(r["ordinal"], r["row"]) for r in rows]
    if keys != sorted(keys) or len(set(keys)) != len(keys):
        raise QualityError("dropped membership is not in deterministic order")
    return rows
