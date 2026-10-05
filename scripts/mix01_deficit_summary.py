"""Read-only, content-free Mix-01 per-allocation yield summary (deficit remediation input).

Reads only metadata artifacts that already exist and prints ONE JSON object to stdout:

* the original (pre-cleaning) input manifest: acquired files/documents/bytes per
  allocation, per-source plan cursors and sufficiency seals;
* the cleaned input manifest: cleaned documents/bytes per allocation (its lineage must
  name the original manifest's digest);
* the C05 completion named by the downstream proof: kept / excluded / duplicate
  documents and kept-train bytes per allocation;
* the exact counts envelope (``counts.json`` only, never ``counts.jsonl``): kept-train
  documents and exact valid targets per allocation;
* a deficit report or a ``selection.json``: frozen quota, eligibility and deficit;
* optionally, frozen inventories (remaining files and known source bytes per source and,
  for Common Pile, per upstream component);
* optionally (``--decisions``), the private C05 decision ledger, streamed twice, to
  aggregate WHY documents were removed: duplicate survivors by allocation, excluded
  family sizes and their component spread, and whether new files keep adding new
  duplicate groups/families (saturation).

It never prints document text, document ids, group ids, file names, paths, benchmark
material or per-record hashes. The only digests printed are artifact identities.
Signatures are not verified here (no key is needed); compare the printed artifact
identities with the reviewed pins. It writes nothing.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

from xlm.data.evidence_v2 import canonical

COUNT_FIELDS = ("documents", "canonical_bytes", "file_bytes")
# Planner-managed inventories keep their last two ranks for bounded benchmarks.
RESERVED_TAIL_RANKS = 2
MAX_METADATA_BYTES = 512 * 1024**2


def load(path: Path) -> Any:
    if path.stat().st_size > MAX_METADATA_BYTES:
        raise SystemExit("metadata file exceeds the 512 MiB ceiling")
    return json.loads(path.read_bytes())


def file_sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(8 * 1024**2):
            digest.update(block)
    return digest.hexdigest()


def key_of(component: str, view: str, upstream: str | None) -> str:
    return str(canonical.canonical_bytes([component, view, upstream]).decode())


def ratio(numerator: float, denominator: float) -> float | None:
    return None if not denominator else round(numerator / denominator, 6)


def per_allocation(manifest: Mapping[str, Any]) -> dict[str, dict[str, int]]:
    totals: dict[str, dict[str, int]] = defaultdict(
        lambda: dict.fromkeys(("files", *COUNT_FIELDS), 0)
    )
    for item in manifest["files"]:
        bucket = totals[key_of(item["component"], item["view"], item.get("upstream_component"))]
        bucket["files"] += 1
        for name in COUNT_FIELDS:
            bucket[name] += int(item[name])
    return dict(totals)


def upstream_of(name: str) -> str:
    return name.replace("\\", "/").split("/", 1)[0]


def inventory_summary(
    inventory: Mapping[str, Any], acquired: set[str], *, reserved_tail: int, by_upstream: bool
) -> dict[str, Any]:
    entries = inventory["files"]
    names = [e["file"] for e in entries]
    position = {n: i for i, n in enumerate(names)}
    taken = sorted(position[n] for n in acquired if n in position)
    reserved = set(range(max(0, len(names) - reserved_tail), len(names)))

    def bucket(indexes: list[int]) -> dict[str, Any]:
        sizes = [entries[i].get("size_bytes") for i in indexes]
        known = [int(s) for s in sizes if s is not None]
        return {
            "files": len(indexes),
            "known_size_files": len(known),
            "known_size_bytes": sum(known),
        }

    remaining = [i for i in range(len(names)) if i not in set(taken)]
    eligible = [i for i in remaining if i not in reserved]
    out: dict[str, Any] = {
        "inventory_digest": inventory.get("inventory_digest"),
        "inventory_files": len(names),
        "acquired": bucket(taken),
        "acquired_files_not_in_inventory": len(acquired) - len(taken),
        "acquired_is_contiguous_prefix": taken == list(range(len(taken))),
        "remaining": bucket(remaining),
        "remaining_excluding_reserved_tail": bucket(eligible),
        "reserved_tail_ranks": reserved_tail,
    }
    if by_upstream:
        groups: dict[str, dict[str, list[int]]] = defaultdict(
            lambda: {"taken": [], "rest": [], "eligible": []}
        )
        for i in taken:
            groups[upstream_of(names[i])]["taken"].append(i)
        for i in remaining:
            groups[upstream_of(names[i])]["rest"].append(i)
        for i in eligible:
            groups[upstream_of(names[i])]["eligible"].append(i)
        out["by_upstream"] = {
            name: {
                "acquired": bucket(g["taken"]),
                "remaining": bucket(g["rest"]),
                "remaining_excluding_reserved_tail": bucket(g["eligible"]),
            }
            for name, g in sorted(groups.items())
        }
    return out


def ledger_rows(path: Path) -> Iterator[dict[str, Any]]:
    with path.open("rb") as stream:
        for raw in stream:
            yield json.loads(raw)


def ledger_summary(
    path: Path, file_rank: Mapping[str, tuple[str, int]], focus: set[str]
) -> dict[str, Any]:
    """Two streaming passes; aggregates only (no id leaves this function)."""
    decided: dict[str, Counter[str]] = defaultdict(Counter)
    removed_bytes: dict[str, Counter[str]] = defaultdict(Counter)
    duplicate_groups: set[tuple[str, str]] = set()  # (allocation, group) of duplicate members
    excluded_families: dict[str, Counter[str]] = defaultdict(Counter)
    excluded_family_bytes: Counter[str] = Counter()
    first_seen: dict[str, dict[str, Any]] = {}
    rows_by_file: dict[str, Counter[int]] = defaultdict(Counter)
    for row in ledger_rows(path):
        allocation = key_of(row["component"], row["view"], row["upstream_component"])
        decision = row["decision"]
        decided[allocation][decision] += 1
        removed_bytes[allocation][decision] += int(row["bytes"])
        if decision == "duplicate":
            duplicate_groups.add((allocation, row["duplicate_group"]))
        elif decision == "excluded":
            excluded_families[row["lineage_group"]][row["component"]] += 1
            excluded_family_bytes[row["lineage_group"]] += int(row["bytes"])
        if allocation in focus:
            rank = file_rank.get(row["file"], ("", -1))[1]
            rows_by_file[allocation][rank] += 1
            seen = first_seen.setdefault(allocation, {"groups": {}, "families": {}})
            for name, value in (
                ("groups", row["duplicate_group"]),
                ("families", row["lineage_group"]),
            ):
                current = seen[name].get(value)
                if current is None or rank < current:
                    seen[name][value] = rank
    wanted = {group for _, group in duplicate_groups}
    survivor_allocation: dict[str, str] = {}
    for row in ledger_rows(path):
        if row["decision"] == "kept" and row["duplicate_group"] in wanted:
            survivor_allocation[row["duplicate_group"]] = key_of(
                row["component"], row["view"], row["upstream_component"]
            )
    survivors: dict[str, Counter[str]] = defaultdict(Counter)
    for allocation, group in sorted(duplicate_groups):
        survivor = survivor_allocation.get(group)
        survivors[allocation][
            "no kept survivor (group excluded)"
            if survivor is None
            else "same allocation"
            if survivor == allocation
            else "other allocation: " + survivor
        ] += 1
    families = sorted(
        excluded_families.items(),
        key=lambda kv: (-sum(kv[1].values()), -excluded_family_bytes[kv[0]]),
    )
    largest: list[dict[str, Any]] = [
        {
            "documents": sum(counts.values()),
            "bytes": excluded_family_bytes[family],
            "documents_by_component": dict(sorted(counts.items())),
        }
        for family, counts in families[:5]
    ]
    saturation: dict[str, Any] = {}
    for allocation, seen in first_seen.items():
        ranks = sorted(rows_by_file[allocation])
        if len(ranks) < 2:
            continue
        cut = ranks[len(ranks) * 3 // 4]
        last_rows = sum(n for r, n in rows_by_file[allocation].items() if r >= cut)
        total_rows = sum(rows_by_file[allocation].values())
        entry: dict[str, Any] = {
            "files": len(ranks),
            "rows": total_rows,
            "last_quartile_rows": last_rows,
        }
        for name in ("groups", "families"):
            values = list(seen[name].values())
            new_late = sum(1 for r in values if r >= cut)
            entry[f"distinct_{name}"] = len(values)
            entry[f"distinct_{name}_per_row"] = ratio(len(values), total_rows)
            entry[f"new_{name}_in_last_quartile_files_per_row"] = ratio(new_late, last_rows)
        saturation[allocation] = entry
    return {
        "decisions_sha256": file_sha(path),
        "decisions_by_allocation": {k: dict(sorted(v.items())) for k, v in sorted(decided.items())},
        "bytes_by_decision": {k: dict(sorted(v.items())) for k, v in sorted(removed_bytes.items())},
        "duplicate_groups_by_survivor": {
            k: dict(sorted(v.items())) for k, v in sorted(survivors.items())
        },
        "excluded_families": len(excluded_families),
        "largest_excluded_families": largest,
        "excluded_documents_in_largest_family": sum(largest[0]["documents_by_component"].values())
        if largest
        else 0,
        "acquisition_order_saturation": saturation,
        "saturation_note": "new duplicate groups/families per row in the last quarter of each "
        "allocation's files (frozen inventory order) versus distinct ones per row overall; a "
        "much smaller late rate means more files of that source add little new material",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--original-manifest", type=Path, required=True)
    parser.add_argument("--cleaned-manifest", type=Path, required=True)
    parser.add_argument("--proof", type=Path, required=True, help="downstream C05 proof spec")
    parser.add_argument("--counts", type=Path, required=True, help="exact counts directory")
    report = parser.add_mutually_exclusive_group(required=True)
    report.add_argument("--deficit-report", type=Path)
    report.add_argument("--selection", type=Path, help="selection.json (no deficit case)")
    parser.add_argument("--inventory-dir", type=Path, help="<source_key>.inventory.json files")
    parser.add_argument(
        "--inventory",
        action="append",
        default=[],
        metavar="SOURCE_KEY=PATH",
        help="explicit inventory for a source key (repeatable; overrides --inventory-dir)",
    )
    parser.add_argument("--decisions", type=Path, help="optional private C05 decisions.jsonl")
    args = parser.parse_args()

    original = load(args.original_manifest)
    cleaned = load(args.cleaned_manifest)
    proof = load(args.proof)
    completion_path = Path(proof["completion"]) / "completion.json"
    completion = load(completion_path)
    counts = load(args.counts / "counts.json")
    report_path = args.deficit_report or args.selection
    report_body = load(report_path)
    report_body = report_body.get("payload", report_body)
    plan = load(Path(proof["plan"]))

    acquired = per_allocation(original)
    clean = per_allocation(cleaned)
    c05: Mapping[str, Mapping[str, int]] = completion["payload"]["allocations"]
    exact: Mapping[str, Mapping[str, int]] = counts["payload"]["allocations"]
    quota: Mapping[str, Mapping[str, Any]] = report_body["allocations"]
    keys = sorted(set(acquired) | set(clean) | set(c05) | set(exact) | set(quota))

    allocations: dict[str, Any] = {}
    for key in keys:
        a = acquired.get(key, dict.fromkeys(("files", *COUNT_FIELDS), 0))
        c = clean.get(key, dict.fromkeys(("files", *COUNT_FIELDS), 0))
        k = c05.get(key, {"kept": 0, "excluded": 0, "duplicate": 0, "train_bytes": 0})
        e = exact.get(key, {"documents": 0, "valid_targets": 0})
        q = quota.get(key, {})
        targets = int(e["valid_targets"])
        removed = int(k["excluded"]) + int(k["duplicate"])
        entry = {
            "acquired": a,
            "cleaned": c,
            "c05": {
                "kept": k["kept"],
                "kept_train_documents": e["documents"],
                "kept_non_train": int(k["kept"]) - int(e["documents"]),
                "excluded": k["excluded"],
                "duplicate": k["duplicate"],
                "kept_train_bytes": k["train_bytes"],
            },
            "exact_valid_targets": targets,
            "quota": q.get("quota"),
            "eligible_valid_targets": q.get("eligible_valid_targets"),
            "deficit": q.get("deficit"),
            "status": q.get("status"),
            "ratios": {
                "cleaning_document_survival": ratio(c["documents"], a["documents"]),
                "cleaning_byte_survival": ratio(c["canonical_bytes"], a["canonical_bytes"]),
                "c05_document_survival": ratio(int(k["kept"]), c["documents"]),
                "c05_removed_documents_share_excluded": ratio(int(k["excluded"]), removed),
                "c05_train_byte_survival": ratio(int(k["train_bytes"]), c["canonical_bytes"]),
                "targets_per_acquired_canonical_byte": ratio(targets, a["canonical_bytes"]),
                "targets_per_cleaned_canonical_byte": ratio(targets, c["canonical_bytes"]),
                "targets_per_c05_kept_train_byte": ratio(targets, int(k["train_bytes"])),
                "kept_train_bytes_per_target": ratio(int(k["train_bytes"]), targets),
                "quota_over_exact_targets": ratio(q.get("quota") or 0, targets),
            },
        }
        allocations[key] = entry

    sources: dict[str, Any] = {}
    explicit = dict(item.split("=", 1) for item in args.inventory)
    acquired_files: dict[str, set[str]] = defaultdict(set)
    for item in original["files"]:
        acquired_files[item["source_key"]].add(item["source_file"])
    for source in original.get("sources", []):
        name = source["source_key"]
        sufficiency = source.get("sufficiency") or {}
        selections = [p.get("selection") or {} for p in source.get("plans", [])]
        entry = {
            "acquired_source_files": len(acquired_files.get(name, ())),
            "plans": len(selections),
            "next_cursor": sufficiency.get("next_cursor"),
            "sufficiency_status": sufficiency.get("status"),
            "first_pass_token_method": sufficiency.get("token_method"),
            "inventory": None,
        }
        path = (
            Path(explicit[name])
            if name in explicit
            else (args.inventory_dir / f"{name}.inventory.json" if args.inventory_dir else None)
        )
        if path is not None and path.is_file():
            entry["inventory"] = inventory_summary(
                load(path),
                acquired_files.get(name, set()),
                reserved_tail=0 if name == "ew-fast" else RESERVED_TAIL_RANKS,
                by_upstream=name == "common_pile",
            )
        sources[name] = entry

    body: dict[str, Any] = {
        "kind": "mix01_deficit_metadata_summary_v1",
        "content_free": True,
        "signatures_verified": False,
        "artifacts": {
            "original_manifest_digest": original.get("digest"),
            "original_manifest_self_digest_ok": original.get("digest")
            == canonical.self_digest(original),
            "cleaned_manifest_digest": cleaned.get("digest"),
            "cleaned_manifest_self_digest_ok": cleaned.get("digest")
            == canonical.self_digest(cleaned),
            "cleaned_lineage_names_original": (cleaned.get("lineage") or {}).get(
                "original_input_manifest_digest"
            )
            == original.get("digest"),
            "plan_digest_in_proof": proof.get("plan_digest"),
            "completion_digest": completion.get("digest"),
            "completion_digest_matches_proof": completion.get("digest")
            == proof.get("completion_digest"),
            "completion_plan_digest": completion["payload"].get("plan_digest"),
            "completion_input_manifest_digest": completion["payload"].get("input_manifest_digest"),
            "plan_input_manifest_digest": plan.get("input_manifest_digest"),
            "counts_digest": counts.get("digest"),
            "counts_completion_digest": counts["payload"].get("completion_digest"),
            "counts_tokenizer_fingerprint": (counts["payload"].get("tokenizer") or {}).get(
                "fingerprint"
            ),
            "report_kind": report_body.get("kind"),
            "report_sha256": file_sha(report_path),
            "report_counts_digest": report_body.get("counts_digest"),
            "quota_sha256": report_body.get("quota_sha256"),
        },
        "totals": {
            "acquired": original.get("totals")
            or {k: sum(a[k] for a in acquired.values()) for k in COUNT_FIELDS},
            "cleaned": cleaned.get("totals"),
            "c05": {
                k: completion["payload"].get(k)
                for k in ("documents", "kept", "excluded", "duplicates")
            },
            "exact_valid_targets": sum(int(v["valid_targets"]) for v in exact.values()),
            "kept_train_documents": counts["payload"].get("documents"),
            "deficit": sum(int(v.get("deficit") or 0) for v in quota.values()),
            "dedup_stats": completion["payload"].get("dedup_stats"),
        },
        "allocations": allocations,
        "sources": sources,
    }
    if args.decisions is not None:
        rank: dict[str, tuple[str, int]] = {}
        orders: dict[str, dict[str, int]] = {}
        for name, path in [
            (
                s,
                Path(explicit[s])
                if s in explicit
                else (args.inventory_dir or Path()) / f"{s}.inventory.json",
            )
            for s in acquired_files
        ]:
            if path.is_file():
                orders[name] = {e["file"]: i for i, e in enumerate(load(path)["files"])}
        original_source_file = {f["path"]: f["source_file"] for f in original["files"]}
        for item in cleaned["files"]:
            origin = (item.get("cleaned_from") or {}).get("path", item["path"])
            source_file = original_source_file.get(origin, item.get("source_file"))
            order = orders.get(item["source_key"], {})
            rank[item["path"]] = (item["source_key"], order.get(source_file, -1))
        focus = {k for k, v in allocations.items() if (v["deficit"] or 0) > 0}
        body["ledger"] = ledger_summary(args.decisions, rank, focus)
    json.dump(body, sys.stdout, sort_keys=True, indent=1)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
