# Requires: operator-run only, offline (planning math, no network).
"""Mix-01 production-acquisition planning helper: inventories and headroom.

Subcommands (all offline, deterministic, UTF-8 no-BOM LF output):

- ``freeze``: turn an operator-reviewed remote file list into an IMMUTABLE
  inventory artifact. Files are ordered by the repository hash chain
  ``SHA-256(seed | repository | revision | file)`` (same construction as
  ``xlm.data.acquisition.sampling._det_index``); the artifact carries per
  file sizes where known plus a single inventory digest. Never "first N
  remote files" in provider order.
- ``estimate``: turn per-source CALIBRATION measurements plus the 6B quota
  file into first-pass byte ranges (low/base/high) and proposed initial
  file counts. Estimated usable tokens are NEVER presented as exact XLM
  tokens: every assumption (bytes/token range, survival, safety margin) is
  echoed in the output. Rerun after calibration to get actual numbers.
- ``sufficiency``: compare acquired canonical bytes against an estimate
  document and report per-source SUFFICIENT / TOP_UP / UNKNOWN plus the
  deficit in files-equivalent. Never edits plans or budgets.

Nothing here downloads, probes, admits, tokenizes, trains, or authorizes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from pathlib import Path
from typing import Any

INVENTORY_VERSION = 1
ESTIMATE_VERSION = 1
SUFFICIENCY_VERSION = 1
SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def _fail(message: str) -> int:
    print(f"mix01_inventory: error: {message}", file=sys.stderr)
    return 1


def _det_key(seed: int, repository: str, revision: str, filename: str) -> str:
    return hashlib.sha256(
        "|".join([str(seed), repository, revision, filename]).encode("utf-8")
    ).hexdigest()


def _checked_sha(value: str, what: str) -> str:
    text = (value or "").strip()
    if not SHA_RE.fullmatch(text):
        raise ValueError(f"{what} must be an exact 40-hex commit SHA, got {value!r}")
    return text


def _atomic_write_json(path: Path, payload: Any) -> None:
    # Deterministic bytes: UTF-8 without BOM, LF newlines (explicit
    # newline="\n": Path.write_text would translate to CRLF on Windows).
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    tmp.replace(path)


def _read_lines(path: Path) -> list[str]:
    try:
        return [
            line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
        ]
    except OSError as exc:
        raise ValueError(f"cannot read file list '{path}': {exc}") from exc


def dupe_str(dupes: list[str]) -> str:
    return ", ".join(dupes[:10]) + ("..." if len(dupes) > 10 else "")


def cmd_freeze(args: argparse.Namespace) -> int:
    try:
        revision = _checked_sha(args.revision, "revision")
        names = _read_lines(args.files)
    except ValueError as exc:
        return _fail(str(exc))
    if not names:
        return _fail("file list is empty; refusing to freeze an empty inventory")
    if len(set(names)) != len(names):
        dupes = sorted({n for n in names if names.count(n) > 1})
        return _fail(f"duplicate file names in inventory input: {dupe_str(dupes)}")
    sizes: dict[str, int] = {}
    if args.sizes is not None:
        try:
            raw_sizes = json.loads(args.sizes.read_text(encoding="utf-8"))
        except Exception as exc:
            return _fail(f"cannot read sizes JSON: {exc}")
        if not isinstance(raw_sizes, dict):
            return _fail("sizes JSON must map file name to byte size")
        for name, size in raw_sizes.items():
            if name not in set(names):
                return _fail(f"sizes entry for unknown file: {name!r}")
            if isinstance(size, bool) or not isinstance(size, int) or size < 0:
                return _fail(f"size for {name!r} must be a non-negative integer")
            sizes[str(name)] = size
    entries = [
        {
            "file": name,
            "size_bytes": sizes.get(name),
            "order_key": _det_key(args.seed, args.repo, revision, name),
        }
        for name in names
    ]
    entries.sort(key=lambda e: (str(e["order_key"]), str(e["file"])))
    digest_input = (
        f"v{INVENTORY_VERSION}|{args.source}|{args.repo}|{revision}|{args.seed}"
        f"|{len(entries)}\n"
        + "".join(
            f"{e['order_key']} {e['size_bytes'] if e['size_bytes'] is not None else -1}"
            f" {e['file']}\n"
            for e in entries
        )
    )
    known = [int(str(e["size_bytes"])) for e in entries if e["size_bytes"] is not None]
    payload = {
        "inventory_version": INVENTORY_VERSION,
        "source_id": args.source,
        "repository": args.repo,
        "revision": revision,
        "seed": args.seed,
        "selection": "SHA-256(seed|repository|revision|file) ascending, filename tiebreak",
        "files": entries,
        "file_count": len(entries),
        "known_size_bytes": sum(known) if len(known) == len(entries) else None,
        "inventory_digest": hashlib.sha256(digest_input.encode("utf-8")).hexdigest(),
    }
    _atomic_write_json(args.output, payload)
    print(f"source: {args.source} files: {len(entries)} digest: {payload['inventory_digest']}")
    return 0


def _load_quotas(path: Path) -> dict[str, Any]:
    import yaml

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(f"cannot read quotas '{path}': {exc}") from exc
    if not isinstance(data, dict) or "first_pass_headroom_quotas" not in data:
        raise ValueError("quotas file must carry first_pass_headroom_quotas")
    return data


def _positive_number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise ValueError(f"calibration field '{name}' must be a positive number")
    return float(value)


def cmd_estimate(args: argparse.Namespace) -> int:
    try:
        quotas = _load_quotas(args.quotas)
        calibration = json.loads(args.calibration.read_text(encoding="utf-8"))
    except Exception as exc:
        return _fail(str(exc))
    if not isinstance(calibration, dict):
        return _fail("calibration JSON must be an object with a 'sources' mapping")
    headroom: dict[str, int] = quotas["first_pass_headroom_quotas"]
    final: dict[str, int] = quotas.get("final_quotas", {})
    cal_sources = calibration.get("sources", {})
    if not isinstance(cal_sources, dict):
        return _fail("calibration JSON must be an object with a 'sources' mapping")
    for source_id in cal_sources:
        if source_id not in headroom:
            return _fail(f"calibration source '{source_id}' is not a Mix-01 quota component")
    if not args.cpt_low < args.cpt < args.cpt_high:
        return _fail("need cpt_low < chars_per_token < cpt_high")
    if args.safety < 1:
        return _fail("safety margin must be >= 1")
    assumptions = {
        "bytes_per_token_base": args.cpt,
        "bytes_per_token_low": args.cpt_low,
        "bytes_per_token_high": args.cpt_high,
        "safety_margin": args.safety,
        "note": (
            "bytes/token is an ASSUMPTION for English prose (~1 byte/char), "
            "not a measurement; the frozen tokenizer decides exact XLM tokens. "
            "Ranges are uncertainty, never precision."
        ),
    }
    table: dict[str, Any] = {}
    for source_id in sorted(headroom):
        target = int(headroom[source_id])
        entry: dict[str, Any] = {
            "final_exact_token_quota": int(final.get(source_id, 0)),
            "first_pass_usable_token_target": target,
        }
        cal = cal_sources.get(source_id)
        if cal is None:
            entry["status"] = "NEEDS_CALIBRATION"
            entry["formula"] = (
                "required_transferred_bytes = target_tokens * bytes_per_token "
                "/ (canonical_bytes / transferred_bytes) / extra_survival * safety; "
                "proposed_files = ceil(required_base / avg_file_bytes)"
            )
            table[source_id] = entry
            continue
        try:
            sampled = _positive_number(cal.get("records_sampled"), "records_sampled")
            accepted = float(cal.get("accepted_records", 0))
            transferred = _positive_number(cal.get("transferred_bytes"), "transferred_bytes")
            canonical = _positive_number(cal.get("canonical_bytes"), "canonical_bytes")
            if not 0 <= accepted <= sampled:
                raise ValueError("accepted_records must lie in [0, records_sampled]")
            survival = float(cal.get("extra_survival", 1.0))
            if not 0 < survival <= 1:
                raise ValueError("extra_survival must lie in (0, 1]")
            avg_raw = cal.get("avg_file_bytes")
            avg_file = _positive_number(avg_raw, "avg_file_bytes") if avg_raw is not None else None
        except (ValueError, TypeError) as exc:
            return _fail(f"source '{source_id}': {exc}")
        acceptance = accepted / sampled
        if acceptance <= 0:
            table[source_id] = {
                **entry,
                "status": "BLOCKED",
                "reason": "zero acceptance in calibration; cannot size without usable material",
            }
            continue
        yield_bpc = canonical / transferred
        required_canonical = target * args.cpt
        required_base = required_canonical / yield_bpc / survival * args.safety
        required_low = target * args.cpt_low / yield_bpc / survival * args.safety
        required_high = target * args.cpt_high / yield_bpc / survival * args.safety
        files_base: int | None = None
        if avg_file is not None:
            files_base = int(math.ceil(required_base / avg_file))
        table[source_id] = {
            **entry,
            "status": "ESTIMATED",
            "measured": {
                "records_sampled": sampled,
                "accepted_records": accepted,
                "acceptance_rate": acceptance,
                "transferred_bytes": transferred,
                "canonical_bytes": canonical,
                "canonical_bytes_per_transferred_byte": yield_bpc,
                "extra_survival": survival,
            },
            "required_canonical_bytes_base": required_canonical,
            "required_transferred_bytes": {
                "low": int(required_low),
                "base": int(required_base),
                "high": int(required_high),
            },
            "proposed_initial_files_base": files_base,
            "top_up": (
                "take the NEXT files in inventory order after the initial prefix; "
                "mint a new plan (same seed/revision); never edit a spent plan."
            ),
        }
    payload = {
        "estimate_version": ESTIMATE_VERSION,
        "quotas_file": str(args.quotas),
        "calibration_file": str(args.calibration),
        "assumptions": assumptions,
        "sources": table,
    }
    _atomic_write_json(args.output, payload)
    estimated = sum(1 for v in table.values() if v.get("status") == "ESTIMATED")
    print(f"sources: {len(table)} estimated: {estimated} output: {args.output}")
    return 0


def cmd_sufficiency(args: argparse.Namespace) -> int:
    try:
        estimate = json.loads(args.estimate.read_text(encoding="utf-8"))
        acquired = json.loads(args.acquired.read_text(encoding="utf-8"))
    except Exception as exc:
        return _fail(str(exc))
    table = estimate.get("sources", {})
    got = acquired.get("sources", {})
    if not isinstance(table, dict) or not isinstance(got, dict):
        return _fail("estimate and acquired files must carry 'sources' mappings")
    report: dict[str, Any] = {}
    for source_id in sorted(table):
        have = got.get(source_id)
        need = table[source_id].get("required_canonical_bytes_base")
        if have is None:
            report[source_id] = {"status": "UNKNOWN", "reason": "no acquired bytes reported"}
            continue
        try:
            have_bytes = float(have.get("canonical_bytes", 0))
        except (TypeError, ValueError):
            return _fail(f"acquired bytes for '{source_id}' are not a number")
        if need is None:
            report[source_id] = {
                "status": "UNKNOWN",
                "reason": "no estimate (needs calibration or blocked)",
                "acquired_canonical_bytes": have_bytes,
            }
            continue
        if have_bytes >= float(need):
            report[source_id] = {
                "status": "SUFFICIENT",
                "acquired_canonical_bytes": have_bytes,
                "required_canonical_bytes_base": float(need),
            }
            continue
        deficit = float(need) - have_bytes
        report[source_id] = {
            "status": "TOP_UP",
            "acquired_canonical_bytes": have_bytes,
            "required_canonical_bytes_base": float(need),
            "deficit_canonical_bytes": deficit,
            "top_up": (
                "mint a new plan over the NEXT inventory files; same seed/revision; "
                "never edit the spent plan or replenish its budget"
            ),
        }
    payload = {
        "sufficiency_version": SUFFICIENCY_VERSION,
        "estimate_file": str(args.estimate),
        "acquired_file": str(args.acquired),
        "sources": report,
    }
    _atomic_write_json(args.output, payload)
    counts: dict[str, int] = {}
    for v in report.values():
        counts[v["status"]] = counts.get(v["status"], 0) + 1
    print(f"status: {counts} output: {args.output}")
    return 0


def _strict_int(value: Any, name: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"'{name}' must be an integer >= {minimum}")
    return value


def cmd_record(args: argparse.Namespace) -> int:
    """Append one unit's calibration measurements without manual JSON editing."""
    if args.combine_sources is not None:
        if float(args.survival) != 1.0:
            return _fail("survival comes from the combined entries, not --extra-survival")
        return cmd_record_combine(args)
    try:
        sampled = _strict_int(args.sampled, "records-sampled", 1)
        accepted = _strict_int(args.accepted, "accepted")
        rejected = _strict_int(args.rejected, "rejected")
        if accepted + rejected != sampled:
            raise ValueError(
                f"accepted ({accepted}) + rejected ({rejected}) != "
                f"records-sampled ({sampled}); use the adapt summary counts verbatim"
            )
        transferred = _strict_int(args.transferred, "transferred-bytes", 1)
        canonical = _strict_int(args.canonical, "canonical-bytes", 1)
        survival = float(args.survival)
        if not 0 < survival <= 1:
            raise ValueError("extra-survival must lie in (0, 1]")
    except (ValueError, TypeError) as exc:
        return _fail(str(exc))
    if args.avg_file is not None:
        try:
            avg_file: int | None = _strict_int(args.avg_file, "avg-file-bytes", 1)
        except ValueError as exc:
            return _fail(str(exc))
    else:
        avg_file = None
    if args.calibration.is_file():
        try:
            payload = json.loads(args.calibration.read_text(encoding="utf-8"))
        except Exception as exc:
            return _fail(f"cannot read calibration file: {exc}")
        if not isinstance(payload, dict) or not isinstance(payload.get("sources"), dict):
            return _fail("calibration file must carry a 'sources' mapping")
    else:
        payload = {"sources": {}}
    entry: dict[str, Any] = {
        "records_sampled": sampled,
        "accepted_records": accepted,
        "rejected_records": rejected,
        "transferred_bytes": transferred,
        "canonical_bytes": canonical,
        "extra_survival": survival,
    }
    if avg_file is not None:
        entry["avg_file_bytes"] = avg_file
    if args.source in payload["sources"] and not args.replace:
        if args.adopt and payload["sources"][args.source] == entry:
            print(f"source: {args.source} existing identical entry reused")
            return 0
        return _fail(
            f"source '{args.source}' already recorded; re-run with --replace "
            "to overwrite explicitly"
        )
    payload["sources"][args.source] = entry
    try:
        _atomic_write_json(args.calibration, payload)
    except OSError as exc:
        return _fail(str(exc))
    action = "replaced" if args.replace else "recorded"
    print(f"source: {args.source} {action} accepted: {accepted}/{sampled}")
    return 0


def cmd_record_combine(args: argparse.Namespace) -> int:
    """Sum existing view entries (e.g. IFM general+planning) into one quota entry."""
    if not args.calibration.is_file():
        return _fail("calibration file absent; record the views first")
    try:
        payload = json.loads(args.calibration.read_text(encoding="utf-8"))
        entries = payload.get("sources", {})
        parts = [name.strip() for name in str(args.combine_sources).split(",") if name.strip()]
        if len(parts) < 2:
            return _fail("--combine-sources needs at least two entries")
        if args.source in entries and not args.replace:
            return _fail(f"source '{args.source}' already recorded; re-run with --replace")
        summed = {
            "records_sampled": 0,
            "accepted_records": 0,
            "rejected_records": 0,
            "transferred_bytes": 0,
            "canonical_bytes": 0,
        }
        survivals = set()
        for name in parts:
            entry = entries.get(name)
            if not isinstance(entry, dict):
                return _fail(f"combined entry '{name}' is absent or malformed")
            for key in summed:
                value = entry.get(key)
                if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                    return _fail(f"combined entry '{name}' has bad '{key}'")
                summed[key] += value
            survivals.add(float(entry.get("extra_survival", 1.0)))
        if len(survivals) != 1:
            return _fail("combined entries disagree on extra_survival; refusing to mix")
        if any(entries[n].get("avg_file_bytes") is not None for n in parts):
            return _fail("combined entries carry avg_file_bytes; pass it explicitly instead")
    except (ValueError, TypeError, KeyError) as exc:
        return _fail(str(exc))
    except OSError as exc:
        return _fail(str(exc))
    combined = argparse.Namespace(
        calibration=args.calibration,
        source=args.source,
        sampled=summed["records_sampled"],
        accepted=summed["accepted_records"],
        rejected=summed["rejected_records"],
        transferred=summed["transferred_bytes"],
        canonical=summed["canonical_bytes"],
        avg_file=args.avg_file,
        survival=next(iter(survivals)),
        replace=args.replace,
        combine_sources=None,
    )
    return cmd_record(combined)


def cmd_canonical_bytes(args: argparse.Namespace) -> int:
    """Print the summed utf8_byte_count over a canonical documents.jsonl."""
    total = 0
    try:
        with args.input.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except Exception as exc:
                    print(
                        f"mix01_inventory: error: line {line_number} is not JSON: {exc}",
                        file=sys.stderr,
                    )
                    return 1
                count = record.get("utf8_byte_count", 0)
                if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                    print(
                        f"mix01_inventory: error: line {line_number} has a bad utf8_byte_count",
                        file=sys.stderr,
                    )
                    return 1
                total += count
    except OSError as exc:
        print(f"mix01_inventory: error: cannot read input: {exc}", file=sys.stderr)
        return 1
    print(total)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Mix-01 acquisition planning helper (offline).")
    sub = parser.add_subparsers(dest="command", required=True)
    freeze = sub.add_parser("freeze", help="Freeze a deterministic file inventory.")
    freeze.add_argument("--source", required=True)
    freeze.add_argument("--repo", required=True)
    freeze.add_argument("--revision", required=True)
    freeze.add_argument("--seed", type=int, default=20260918)
    freeze.add_argument("--files", type=Path, required=True)
    freeze.add_argument("--sizes", type=Path, default=None)
    freeze.add_argument("--output", type=Path, required=True)
    freeze.set_defaults(func=cmd_freeze)
    estimate = sub.add_parser("estimate", help="Size first-pass bytes from calibration.")
    estimate.add_argument("--quotas", type=Path, required=True)
    estimate.add_argument("--calibration", type=Path, required=True)
    estimate.add_argument("--output", type=Path, required=True)
    estimate.add_argument("--chars-per-token", type=float, default=4.0, dest="cpt")
    estimate.add_argument("--cpt-low", type=float, default=3.0)
    estimate.add_argument("--cpt-high", type=float, default=5.0)
    estimate.add_argument("--safety", type=float, default=1.15)
    estimate.set_defaults(func=cmd_estimate)
    sufficiency = sub.add_parser("sufficiency", help="Compare acquired bytes to an estimate.")
    sufficiency.add_argument("--estimate", type=Path, required=True)
    sufficiency.add_argument("--acquired", type=Path, required=True)
    sufficiency.add_argument("--output", type=Path, required=True)
    sufficiency.set_defaults(func=cmd_sufficiency)
    record = sub.add_parser("record", help="Append one unit's calibration measurements.")
    record.add_argument("--calibration", type=Path, required=True)
    record.add_argument("--source", required=True)
    record.add_argument("--records-sampled", type=int, required=True, dest="sampled")
    record.add_argument("--accepted", type=int, required=True)
    record.add_argument("--rejected", type=int, required=True)
    record.add_argument("--transferred-bytes", type=int, required=True, dest="transferred")
    record.add_argument("--canonical-bytes", type=int, required=True, dest="canonical")
    record.add_argument("--avg-file-bytes", type=int, default=None, dest="avg_file")
    record.add_argument("--extra-survival", type=float, default=1.0, dest="survival")
    record.add_argument("--replace", action="store_true")
    record.add_argument(
        "--adopt",
        action="store_true",
        help="Reuse an identical existing entry instead of failing on it.",
    )
    canonical = sub.add_parser("canonical-bytes", help="Sum utf8_byte_count over documents.jsonl.")
    canonical.add_argument("--input", type=Path, required=True)
    canonical.set_defaults(func=cmd_canonical_bytes)
    record.add_argument(
        "--combine-sources",
        default=None,
        help="Comma-separated existing entries to sum into --source (IFM views).",
    )
    record.set_defaults(func=cmd_record)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except OSError as exc:
        return _fail(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
