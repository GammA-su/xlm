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
- ``measure``: derive one unit's calibration measurements from its
  artifacts (plan, fetch journal, adaptation summary, canonical documents),
  cross-check every binding, and write them as a deterministic JSON result
  file. ``record --measurement`` consumes that file, so operator drivers
  pass paths only and never scrape numbers from human-readable stdout.

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
MEASUREMENT_VERSION = 1
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
MAX_JOURNAL_BYTES = 8 * 1024**2
MAX_SUMMARY_BYTES = 1024**2
MEASURED_FIELDS = (
    "records_sampled",
    "accepted_records",
    "rejected_records",
    "transferred_bytes",
    "canonical_bytes",
)
#: Window-decode disclosure carried from measurement into the calibration entry.
WINDOW_TRANSFER_BASIS = "calibration_window_retained_share"
WINDOW_FIELDS = ("transfer_basis", "raw_transferred_bytes", "records_scanned")
#: Component-weighted prefix-sample disclosure (``component_calibration.measurement``).
COMPONENT_TRANSFER_BASIS = "component_weighted_prefix_samples"
COMPONENT_FIELDS = ("transfer_basis", "component_calibration_digest")


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
    listing_path = getattr(args, "listing", None)
    files_path = getattr(args, "files", None)
    allowlist_path = getattr(args, "allowlist", None)
    if listing_path is not None and files_path is not None:
        return _fail("pass either --files or --listing, not both")
    if allowlist_path is not None and listing_path is None:
        return _fail("--allowlist filters a --listing; pass the listing it was decided against")
    if listing_path is not None:
        try:
            from xlm.data.sources import hf_inventory as _hfi
        except Exception as exc:
            return _fail(f"cannot load generic listing module: {exc}")
        try:
            receipt = _hfi.read_listing(listing_path)
            names, sizes = _hfi.listing_to_freeze_inputs(receipt)
            # Bind freeze identity to the listing identity; never mix revisions.
            if str(args.source) != str(receipt["source_id"]):
                return _fail("freeze --source differs from the listing source_id")
            if str(args.repo) != str(receipt["repository"]):
                return _fail("freeze --repo differs from the listing repository")
            try:
                revision = _checked_sha(args.revision, "revision")
            except ValueError as exc:
                return _fail(str(exc))
            if revision != str(receipt["resolved_revision"]):
                return _fail("freeze --revision differs from the listing resolved revision")
            if getattr(args, "sizes", None) is not None:
                return _fail("--listing already carries sizes; do not also pass --sizes")
            binding: dict[str, Any] | None = None
            if allowlist_path is not None:
                # Exact top-level component membership, decided by the operator
                # against this very listing; never a filename pattern.
                from xlm.data.acquisition import component_allowlist as _allow

                record = _allow.read_json(allowlist_path)
                names, sizes = _allow.filtered_freeze_inputs(record, receipt)
                binding = _allow.inventory_binding(record)
        except ValueError as exc:
            return _fail(str(exc))
        if not names:
            return _fail("listing holds no file; refusing to freeze an empty inventory")
        payload = freeze_inventory(
            args.source, args.repo, revision, args.seed, names, sizes, allowlist=binding
        )
        _atomic_write_json(args.output, payload)
        bound = f" allowlist: {binding['digest']}" if binding is not None else ""
        print(
            f"source: {args.source} files: {len(names)} "
            f"digest: {payload['inventory_digest']} from-listing: {receipt['digest']}{bound}"
        )
        return 0
    try:
        revision = _checked_sha(args.revision, "revision")
        if files_path is None:
            return _fail("freeze needs --files or --listing")
        names = _read_lines(files_path)
    except ValueError as exc:
        return _fail(str(exc))
    if not names:
        return _fail("file list is empty; refusing to freeze an empty inventory")
    if len(set(names)) != len(names):
        dupes = sorted({n for n in names if names.count(n) > 1})
        return _fail(f"duplicate file names in inventory input: {dupe_str(dupes)}")
    sizes = {}
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
    payload = freeze_inventory(args.source, args.repo, revision, args.seed, names, sizes)
    _atomic_write_json(args.output, payload)
    print(f"source: {args.source} files: {len(names)} digest: {payload['inventory_digest']}")
    return 0


def freeze_inventory(
    source: str,
    repository: str,
    revision: str,
    seed: int,
    names: list[str],
    sizes: dict[str, int],
    allowlist: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Existing production inventory format, also usable for offline adoption.

    ``allowlist`` is the component-allowlist binding of a filtered inventory;
    its digest enters the inventory digest. Without one the format and digest
    are exactly the historical ones.
    """
    _checked_sha(revision, "revision")
    if not names or len(set(names)) != len(names):
        raise ValueError("inventory requires nonempty distinct paths")
    if any(name not in names or type(size) is not int or size < 0 for name, size in sizes.items()):
        raise ValueError("invalid inventory size or unknown path")
    entries = [
        {
            "file": name,
            "size_bytes": sizes.get(name),
            "order_key": _det_key(seed, repository, revision, name),
        }
        for name in names
    ]
    entries.sort(key=lambda e: (str(e["order_key"]), str(e["file"])))
    bound = f"|allowlist:{allowlist['digest']}" if allowlist is not None else ""
    digest_input = (
        f"v{INVENTORY_VERSION}|{source}|{repository}|{revision}|{seed}{bound}"
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
        "source_id": source,
        "repository": repository,
        "revision": revision,
        "seed": seed,
        "selection": "SHA-256(seed|repository|revision|file) ascending, filename tiebreak",
        "files": entries,
        "file_count": len(entries),
        "known_size_bytes": sum(known) if len(known) == len(entries) else None,
        "inventory_digest": hashlib.sha256(digest_input.encode("utf-8")).hexdigest(),
    }
    if allowlist is not None:
        payload["component_allowlist"] = allowlist
    return payload


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
    auxiliary = set(getattr(args, "auxiliary_source", []))
    if auxiliary - set(cal_sources):
        return _fail("an auxiliary calibration source is absent")
    if auxiliary & set(headroom):
        return _fail("a quota component cannot be treated as an auxiliary calibration source")
    for source_id in cal_sources:
        if source_id not in headroom and source_id not in auxiliary:
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
        if cal.get("transfer_basis") == COMPONENT_TRANSFER_BASIS:
            table[source_id]["calibration_basis"] = COMPONENT_TRANSFER_BASIS
            table[source_id]["component_calibration_digest"] = cal["component_calibration_digest"]
            table[source_id]["measured"]["count_basis"] = (
                "rounded inventory-weighted sample equivalents, not raw observed counts; "
                "actual component acceptance/rejections remain in the bound calibration"
            )
    payload = {
        "estimate_version": ESTIMATE_VERSION,
        "quotas_file": str(args.quotas),
        "calibration_file": str(args.calibration),
        "assumptions": assumptions,
        "sources": table,
    }
    if auxiliary:
        payload["auxiliary_calibration_sources"] = {
            name: cal_sources[name] for name in sorted(auxiliary)
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


def _load_measurement(path: Path) -> dict[str, Any]:
    """Read a ``measure`` result file; every recorded value comes from it."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(f"cannot read measurement '{path}': {exc}") from exc
    if not isinstance(payload, dict) or payload.get("measurement_version") != MEASUREMENT_VERSION:
        raise ValueError(f"measurement '{path}' is not a version {MEASUREMENT_VERSION} result")
    for key in MEASURED_FIELDS:
        _strict_int(payload.get(key), key)
    survival = payload.get("extra_survival")
    if isinstance(survival, bool) or not isinstance(survival, (int, float)):
        raise ValueError("measurement 'extra_survival' must be a number")
    if payload.get("transfer_basis") == COMPONENT_TRANSFER_BASIS:
        # A component-weighted estimate of a multi-component source: its counts are
        # rounded reweightings of real prefix samples, bound to their calibration.
        digest = payload.get("component_calibration_digest")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("component measurement does not name its calibration digest")
        _strict_int(payload.get("avg_file_bytes"), "avg_file_bytes", 1)
        from xlm.data.acquisition import component_calibration as cc

        record = payload.get("component_calibration")
        if not isinstance(record, dict):
            raise ValueError("component measurement needs its verifiable calibration")
        cc.check_calibration(
            record, allowlist=record["allowlist"], inventory=record["inventory_snapshot"]
        )
        if payload != cc.measurement(record):
            raise ValueError("component measurement does not reproduce its calibration")
        return payload
    if "transfer_basis" in payload:
        if payload["transfer_basis"] != WINDOW_TRANSFER_BASIS:
            raise ValueError(f"measurement transfer_basis {payload['transfer_basis']!r} unknown")
        raw = _strict_int(payload.get("raw_transferred_bytes"), "raw_transferred_bytes", 1)
        scanned = _strict_int(payload.get("records_scanned"), "records_scanned", 1)
        if -(-raw * payload["records_sampled"] // scanned) != payload["transferred_bytes"]:
            raise ValueError("measurement transferred_bytes is not the retained-row share")
    return payload


def cmd_record(args: argparse.Namespace) -> int:
    """Append one unit's calibration measurements without manual JSON editing."""
    explicit = [args.sampled, args.accepted, args.rejected, args.transferred, args.canonical]
    if args.combine_sources is not None:
        if args.survival is not None or args.measurement is not None:
            return _fail("survival comes from the combined entries, not --extra-survival")
        return cmd_record_combine(args)
    if args.measurement is not None:
        if any(value is not None for value in explicit) or args.survival is not None:
            return _fail("--measurement supplies every value; do not also pass explicit counts")
        try:
            measured = _load_measurement(args.measurement)
        except ValueError as exc:
            return _fail(str(exc))
        args.sampled = measured["records_sampled"]
        args.accepted = measured["accepted_records"]
        args.rejected = measured["rejected_records"]
        args.transferred = measured["transferred_bytes"]
        args.canonical = measured["canonical_bytes"]
        args.survival = measured["extra_survival"]
        if measured.get("transfer_basis") == COMPONENT_TRANSFER_BASIS:
            from xlm.data.acquisition import component_calibration as cc

            record = measured["component_calibration"]
            if args.source != record["component_id"]:
                return _fail("component measurement belongs to another logical component")
            root = args.calibration.parent.parent
            try:
                current_allow = json.loads(
                    (root / "calib/component_allowlists" / f"{record['source_id']}.json").read_text(
                        encoding="utf-8"
                    )
                )
                current_inventory = json.loads(
                    (root / "inventories" / f"{record['source_id']}.inventory.json").read_text(
                        encoding="utf-8"
                    )
                )
                cc.check_calibration(record, allowlist=current_allow, inventory=current_inventory)
                stored_calibration = json.loads(
                    (
                        root / "calib" / record["component_id"] / "component-calibration.json"
                    ).read_text(encoding="utf-8")
                )
                if stored_calibration != record:
                    raise ValueError("measurement names a stale component calibration")
            except (OSError, ValueError, KeyError) as exc:
                return _fail(f"component measurement current identity: {exc}")
            disclosure = {key: measured[key] for key in COMPONENT_FIELDS}
            disclosure["component_calibration"] = record
            if args.avg_file is None:
                args.avg_file = measured["avg_file_bytes"]
        else:
            disclosure = {key: measured[key] for key in WINDOW_FIELDS if key in measured}
    elif any(value is None for value in explicit):
        return _fail(
            "pass --measurement, or all of --records-sampled/--accepted/--rejected/"
            "--transferred-bytes/--canonical-bytes"
        )
    else:
        disclosure = {}
    if args.survival is None:
        args.survival = 1.0
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
    # Legacy entries keep their exact keys; window entries disclose their basis.
    entry.update(disclosure)
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
        if any("transfer_basis" in entries[n] for n in parts):
            return _fail("combined entries carry a calibration-window transfer basis; refusing")
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
        adopt=args.adopt,
        measurement=None,
        combine_sources=None,
    )
    return cmd_record(combined)


def scan_canonical(path: Path, *, require_text: bool) -> tuple[int, int, str]:
    """Return (documents, summed utf8_byte_count, file sha256); strict per line.

    Every non-blank line must be a JSON object with an integer
    ``utf8_byte_count``; where ``text`` is present (always, with
    ``require_text``) the count must equal its UTF-8 length, matching the
    canonical document contract. Any deviation raises ValueError.
    """
    digest = hashlib.sha256()
    documents = total = 0
    try:
        with path.open("rb") as handle:
            for line_number, raw in enumerate(handle, start=1):
                digest.update(raw)
                if not raw.strip():
                    continue
                try:
                    record = json.loads(raw.decode("utf-8"))
                except Exception as exc:
                    raise ValueError(f"line {line_number} is not JSON: {exc}") from exc
                if not isinstance(record, dict):
                    raise ValueError(f"line {line_number} is not a JSON object")
                count = record.get("utf8_byte_count")
                if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                    raise ValueError(f"line {line_number} has a bad utf8_byte_count")
                text = record.get("text")
                if text is None and require_text:
                    raise ValueError(f"line {line_number} has no text")
                if text is not None:
                    if not isinstance(text, str):
                        raise ValueError(f"line {line_number} text is not a string")
                    if len(text.encode("utf-8")) != count:
                        raise ValueError(
                            f"line {line_number} utf8_byte_count {count} differs from "
                            f"its text length {len(text.encode('utf-8'))}"
                        )
                documents += 1
                total += count
    except OSError as exc:
        raise ValueError(f"cannot read '{path}': {exc}") from exc
    return documents, total, digest.hexdigest()


def cmd_canonical_bytes(args: argparse.Namespace) -> int:
    """Print the summed utf8_byte_count over a canonical documents.jsonl (display only)."""
    try:
        _, total, _ = scan_canonical(args.input, require_text=False)
    except ValueError as exc:
        return _fail(str(exc))
    print(total)
    return 0


def _read_bounded_json(path: Path, limit: int, what: str) -> Any:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise ValueError(f"{what} '{path}' is missing: {exc}") from exc
    if size > limit:
        raise ValueError(f"{what} '{path}' exceeds {limit} bytes")
    try:
        return json.loads(path.read_bytes().decode("utf-8"))
    except Exception as exc:
        raise ValueError(f"{what} '{path}' is unreadable: {exc}") from exc


def _expect(actual: Any, expected: Any, what: str) -> None:
    if actual != expected:
        raise ValueError(f"{what} is {actual!r}, expected {expected!r}")


def measure_unit(
    *,
    plan_path: Path,
    scratch_dir: Path,
    canonical_dir: Path,
    source: str,
    view: str,
    revision: str,
    allow_empty: bool = False,
) -> dict[str, Any]:
    """Derive calibration measurements from one unit's artifacts, fail-closed.

    Binds plan -> fetch journal -> adaptation summary -> canonical documents
    by plan id/hash, source and revision; requires a COMPLETED fetch; checks
    the documents digest/count against the summary and every per-document
    byte count against its text. Reads only; writes nothing.
    """
    from xlm.data.acquisition.plan import load_acquisition_plan
    from xlm.data.acquisition.progress import AcquisitionState

    try:
        plan = load_acquisition_plan(plan_path)
    except Exception as exc:
        raise ValueError(f"plan '{plan_path}' failed validation: {exc}") from exc
    plan_hash = plan.compute_behavioral_hash()
    _expect(plan.plan_hash, plan_hash, "stored plan_hash (recomputed hash differs)")
    _expect(plan.source_id, source, "plan source_id")
    _expect(plan.view_id, view, "plan view_id")
    _expect(plan.revision, revision, "plan revision")

    journal_path = scratch_dir / "journals" / f"{plan.plan_id}.progress.json"
    try:
        size = journal_path.stat().st_size
    except OSError as exc:
        raise ValueError(f"fetch journal '{journal_path}' is missing: {exc}") from exc
    if size > MAX_JOURNAL_BYTES:
        raise ValueError(f"fetch journal '{journal_path}' exceeds {MAX_JOURNAL_BYTES} bytes")
    try:
        state = AcquisitionState.model_validate_json(journal_path.read_bytes())
    except Exception as exc:
        raise ValueError(f"fetch journal '{journal_path}' is invalid: {exc}") from exc
    _expect(state.plan_id, plan.plan_id, "journal plan_id")
    _expect(state.plan_hash, plan_hash, "journal plan_hash")
    _expect(state.status, "COMPLETED", "journal status")
    transferred = _strict_int(state.transferred_bytes, "journal transferred_bytes", 1)
    window_fields: dict[str, Any] = {}
    if plan.parquet_window is not None:
        # A window run transfers projected-chunk PREFIXES for every decoded
        # row (records_scanned), not only the retained ones, so the raw
        # journal transfer is not a production yield input. Record it
        # verbatim and size with the retained-row share (buffer/dictionary
        # overhead only inflates raw, keeping the share conservative).
        scanned = _strict_int(
            state.accounting.consumed.get("records_scanned"), "journal records_scanned", 1
        )
        retained = _strict_int(state.records_acquired, "journal records_acquired", 1)
        if retained > scanned:
            raise ValueError(
                f"journal records_acquired ({retained}) exceeds records_scanned ({scanned})"
            )
        window_fields = {
            "transfer_basis": WINDOW_TRANSFER_BASIS,
            "raw_transferred_bytes": transferred,
            "records_scanned": scanned,
            "transfer_note": (
                "CALIBRATION-ONLY window decode: raw_transferred_bytes covers projected "
                "prefixes of records_scanned decoded rows; transferred_bytes = "
                "ceil(raw_transferred_bytes * records_sampled / records_scanned) assumes "
                "uniform bytes per decoded row; not production-fetch throughput."
            ),
        }
        transferred = -(-transferred * retained // scanned)

    summary = _read_bounded_json(
        canonical_dir / "adaptation_summary.json", MAX_SUMMARY_BYTES, "adaptation summary"
    )
    if not isinstance(summary, dict):
        raise ValueError("adaptation summary is not a JSON object")
    _expect(summary.get("adaptation_summary_version"), 1, "adaptation_summary_version")
    _expect(summary.get("plan_id"), plan.plan_id, "adaptation summary plan_id")
    _expect(summary.get("plan_hash"), plan_hash, "adaptation summary plan_hash")
    _expect(summary.get("source_id"), source, "adaptation summary source_id")
    _expect(summary.get("source_revision"), revision, "adaptation summary source_revision")
    if "manifest" in summary or "shards" in summary:
        raise ValueError("sharded canonical output is not a calibration output")
    total_input = _strict_int(summary.get("total_input_records"), "total_input_records", 1)
    accepted = _strict_int(summary.get("accepted_records"), "accepted_records")
    rejected = _strict_int(summary.get("rejected_records"), "rejected_records")
    if accepted + rejected != total_input:
        raise ValueError(
            f"adaptation summary accepted ({accepted}) + rejected ({rejected}) != "
            f"total_input_records ({total_input})"
        )
    _expect(total_input, state.records_acquired, "adaptation total_input_records vs fetched")
    documents = summary.get("documents")
    if not isinstance(documents, dict):
        raise ValueError("adaptation summary lacks its documents binding")
    _expect(documents.get("file"), "documents.jsonl", "adaptation summary documents.file")
    _expect(documents.get("count"), accepted, "adaptation summary documents.count")

    count, canonical, digest = scan_canonical(canonical_dir / "documents.jsonl", require_text=True)
    _expect(digest, documents.get("sha256"), "documents.jsonl sha256")
    _expect(count, accepted, "documents.jsonl document count")
    _strict_int(canonical, "canonical_bytes", 0 if allow_empty else 1)
    return {
        "measurement_version": MEASUREMENT_VERSION,
        "source_id": source,
        "view_id": view,
        "revision": revision,
        "plan_id": plan.plan_id,
        "plan_hash": plan_hash,
        "fetch_status": state.status,
        "records_sampled": total_input,
        "accepted_records": accepted,
        "rejected_records": rejected,
        "transferred_bytes": transferred,
        "canonical_bytes": canonical,
        "canonical_documents": count,
        "documents_sha256": digest,
        "extra_survival": 1.0,
        **window_fields,
    }


def cmd_measure(args: argparse.Namespace) -> int:
    """Write the unit's measurement result file (idempotent; never overwrites a divergent one)."""
    try:
        payload = measure_unit(
            plan_path=args.plan,
            scratch_dir=args.scratch_dir,
            canonical_dir=args.canonical_dir,
            source=args.source,
            view=args.view,
            revision=args.revision,
        )
    except ValueError as exc:
        return _fail(f"measurement refused: {exc}")
    rendered = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
    if args.output.exists():
        try:
            existing = args.output.read_bytes()
        except OSError as exc:
            return _fail(f"cannot read existing measurement: {exc}")
        if existing != rendered:
            return _fail(
                f"existing measurement '{args.output}' differs from the artifacts; "
                "refusing to overwrite it"
            )
        action = "existing identical measurement reused"
    else:
        _atomic_write_json(args.output, payload)
        action = "measurement written"
    print(
        f"{action}: {args.output} accepted {payload['accepted_records']}/"
        f"{payload['records_sampled']} transferred {payload['transferred_bytes']} "
        f"canonical {payload['canonical_bytes']}"
    )
    return 0


def cmd_list_hf(args: argparse.Namespace) -> int:
    """NETWORK: bounded metadata-only Hub tree listing at the pinned revision."""
    import os

    if os.environ.get("HF_HUB_OFFLINE") == "1" or os.environ.get("HF_DATASETS_OFFLINE") == "1":
        return _fail("live listing refused while HF_*_OFFLINE=1; unset only for the operator run")
    try:
        from xlm.data.sources import hf_inventory as _hfi
    except Exception as exc:
        return _fail(f"cannot load generic listing module: {exc}")
    try:
        filt = _hfi.normalize_filter(
            path_prefix=str(args.path_prefix or ""),
            extensions=tuple(args.extension or ()),
            include_globs=tuple(args.include_glob or ()),
            exclude_globs=tuple(args.exclude_glob or ()),
        )
        limits = _hfi.ListingLimits(
            max_pages=int(args.max_pages),
            max_items=int(args.max_items),
            max_requests=int(args.max_requests),
            max_metadata_bytes=int(args.max_metadata_bytes),
            max_retries=int(args.max_retries),
            per_request_timeout_seconds=float(args.timeout),
            total_deadline_seconds=float(args.deadline),
        )
        _hfi.check_limits(limits)
        fetcher = _hfi.HfTreeFetcher(
            repository=str(args.repo),
            revision=str(args.revision),
            path_prefix=filt.path_prefix,
            recursive=True,
            timeout_seconds=float(args.timeout),
        )
        receipt = _hfi.collect_listing(
            repository=str(args.repo),
            requested_revision=str(args.revision),
            source_id=str(args.source),
            view_id=str(args.view),
            filt=filt,
            limits=limits,
            fetcher=fetcher,
        )
        _hfi.write_listing(args.output, receipt)
    except ValueError as exc:
        return _fail(str(exc))
    except OSError as exc:
        return _fail(str(exc))
    total = receipt["total_declared_bytes"]
    print(
        f"listing: {receipt['repository']}@{receipt['resolved_revision']} "
        f"files: {receipt['item_count']} bytes: {total} "
        f"pages: {receipt['page_count']} requests: {receipt['request_count']} "
        f"digest: {receipt['digest']}"
    )
    return 0


def cmd_verify_listing(args: argparse.Namespace) -> int:
    """Offline verification of a listing receipt; never uses the network."""
    try:
        from xlm.data.sources import hf_inventory as _hfi
    except Exception as exc:
        return _fail(f"cannot load generic listing module: {exc}")
    try:
        receipt = _hfi.read_listing(args.listing)
    except ValueError as exc:
        return _fail(str(exc))
    print(
        f"listing VERIFIED: {receipt['repository']}@{receipt['resolved_revision']} "
        f"source: {receipt['source_id']} view: {receipt['view_id']} "
        f"files: {receipt['item_count']} bytes: {receipt['total_declared_bytes']} "
        f"file-list: {receipt['file_list_digest']} digest: {receipt['digest']}"
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Mix-01 acquisition planning helper (offline).")
    sub = parser.add_subparsers(dest="command", required=True)
    freeze = sub.add_parser("freeze", help="Freeze a deterministic file inventory.")
    freeze.add_argument("--source", required=True)
    freeze.add_argument("--repo", required=True)
    freeze.add_argument("--revision", required=True)
    freeze.add_argument("--seed", type=int, default=20260918)
    freeze.add_argument("--files", type=Path, required=False, default=None)
    freeze.add_argument("--sizes", type=Path, default=None)
    freeze.add_argument(
        "--listing",
        type=Path,
        default=None,
        help="Frozen listing receipt (from list-hf); replaces --files/--sizes.",
    )
    freeze.add_argument(
        "--allowlist",
        type=Path,
        default=None,
        help="Operator component allowlist: keep only its included top-level components "
        "of --listing and bind its digest into the inventory digest.",
    )
    freeze.add_argument("--output", type=Path, required=True)
    freeze.set_defaults(func=cmd_freeze)
    list_hf = sub.add_parser(
        "list-hf",
        help="NETWORK: bounded metadata-only Hugging Face tree listing at a pinned revision.",
    )
    list_hf.add_argument("--source", required=True)
    list_hf.add_argument("--view", required=True)
    list_hf.add_argument("--repo", required=True)
    list_hf.add_argument("--revision", required=True)
    list_hf.add_argument("--path-prefix", default="", help="Relative prefix, e.g. data/eng_Latn/.")
    list_hf.add_argument(
        "--extension",
        action="append",
        default=None,
        dest="extension",
        help="Allowlist one extension (repeatable), e.g. --extension .parquet.",
    )
    list_hf.add_argument(
        "--include-glob", action="append", default=None, help="Required glob (repeatable)."
    )
    list_hf.add_argument(
        "--exclude-glob", action="append", default=None, help="Exclusion glob (repeatable)."
    )
    list_hf.add_argument("--output", type=Path, required=True)
    list_hf.add_argument("--max-pages", type=int, default=128)
    list_hf.add_argument("--max-items", type=int, default=50000)
    list_hf.add_argument("--max-requests", type=int, default=256)
    list_hf.add_argument("--max-metadata-bytes", type=int, default=16 * 1024**2)
    list_hf.add_argument("--max-retries", type=int, default=3)
    list_hf.add_argument("--timeout", type=float, default=15.0)
    list_hf.add_argument("--deadline", type=float, default=300.0)
    list_hf.set_defaults(func=cmd_list_hf)
    verify = sub.add_parser("verify-listing", help="Offline verification of a listing receipt.")
    verify.add_argument("--listing", type=Path, required=True)
    verify.set_defaults(func=cmd_verify_listing)
    estimate = sub.add_parser("estimate", help="Size first-pass bytes from calibration.")
    estimate.add_argument("--quotas", type=Path, required=True)
    estimate.add_argument("--calibration", type=Path, required=True)
    estimate.add_argument("--output", type=Path, required=True)
    estimate.add_argument("--chars-per-token", type=float, default=4.0, dest="cpt")
    estimate.add_argument("--cpt-low", type=float, default=3.0)
    estimate.add_argument("--cpt-high", type=float, default=5.0)
    estimate.add_argument("--safety", type=float, default=1.15)
    estimate.add_argument(
        "--auxiliary-source",
        action="append",
        default=[],
        help="Explicit non-quota calibration entry to disclose separately (repeatable).",
    )
    estimate.set_defaults(func=cmd_estimate)
    sufficiency = sub.add_parser("sufficiency", help="Compare acquired bytes to an estimate.")
    sufficiency.add_argument("--estimate", type=Path, required=True)
    sufficiency.add_argument("--acquired", type=Path, required=True)
    sufficiency.add_argument("--output", type=Path, required=True)
    sufficiency.set_defaults(func=cmd_sufficiency)
    record = sub.add_parser("record", help="Append one unit's calibration measurements.")
    record.add_argument("--calibration", type=Path, required=True)
    record.add_argument("--source", required=True)
    record.add_argument("--records-sampled", type=int, default=None, dest="sampled")
    record.add_argument("--accepted", type=int, default=None)
    record.add_argument("--rejected", type=int, default=None)
    record.add_argument("--transferred-bytes", type=int, default=None, dest="transferred")
    record.add_argument("--canonical-bytes", type=int, default=None, dest="canonical")
    record.add_argument("--avg-file-bytes", type=int, default=None, dest="avg_file")
    record.add_argument(
        "--extra-survival", type=float, default=None, dest="survival", help="Default 1.0."
    )
    record.add_argument(
        "--measurement",
        type=Path,
        default=None,
        help="Record every value from a 'measure' result file (no explicit counts).",
    )
    record.add_argument("--replace", action="store_true")
    record.add_argument(
        "--adopt",
        action="store_true",
        help="Reuse an identical existing entry instead of failing on it.",
    )
    canonical = sub.add_parser("canonical-bytes", help="Sum utf8_byte_count over documents.jsonl.")
    canonical.add_argument("--input", type=Path, required=True)
    canonical.set_defaults(func=cmd_canonical_bytes)
    measure = sub.add_parser("measure", help="Write one unit's calibration measurement file.")
    measure.add_argument("--plan", type=Path, required=True)
    measure.add_argument("--scratch-dir", type=Path, required=True)
    measure.add_argument("--canonical-dir", type=Path, required=True)
    measure.add_argument("--source", required=True)
    measure.add_argument("--view", required=True)
    measure.add_argument("--revision", required=True)
    measure.add_argument("--output", type=Path, required=True)
    measure.set_defaults(func=cmd_measure)
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
