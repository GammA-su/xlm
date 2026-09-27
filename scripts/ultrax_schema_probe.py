# Requires: operator-run only, with network explicitly enabled for this call.
"""Bounded UltraX-Ultra-FineWeb schema probe (operator-run, network).

Resolves the actual accessible repository between the documented aliases
``openbmb/UltraX-Preview`` and ``openbmb/UltraX`` (never silently chosen),
pins the exact commit SHA, verifies the ``UltraX-Ultra-FineWeb`` config,
records the feature/schema declaration, streams only a very small bounded
sample (no full parquet download) and emits a machine-readable probe
receipt.

Network is OFF by default in this repository (``HF_HUB_OFFLINE=1``). The
operator enables it for exactly this call (see the runbook) and disables it
immediately afterward. All bounds are explicit: ``--max-rows``,
``--timeout-seconds`` and the streaming iterator. Nothing here acquires the
corpus, prepares data, trains a tokenizer or launches a pilot.

Provenance rule: once the exact repository SHA is resolved, EVERY
subsequent dataset operation (config listing, feature description,
streaming sample) is bound to that exact SHA, so the receipt can never
claim revision A while reading rows from a later HEAD. UltraX is a normal
parquet-backed dataset; no remote Python code is required and no
``trust_remote_code`` argument is passed (datasets 5.x removed it).
"""

from __future__ import annotations

import argparse
import collections
import datetime
import hashlib
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

PROBE_VERSION = 1
EXPECTED_FIELDS = ("uid", "raw_content", "cleaned_content", "processed_functions", "source")
EXPECTED_CONFIG = "UltraX-Ultra-FineWeb"
ALIAS_REPOS = ("openbmb/UltraX-Preview", "openbmb/UltraX")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def _fail(message: str) -> int:
    print(f"ultrax_schema_probe: error: {message}", file=sys.stderr)
    return 1


def _resolve_aliases(
    explicit_repo: str | None, *, timeout_seconds: float
) -> tuple[str, dict[str, dict[str, Any]]]:
    """Check both documented aliases; return the selected repo and per-alias outcomes."""
    try:
        from huggingface_hub import HfApi
    except ImportError as exc:
        raise RuntimeError(
            "huggingface_hub is required for the operator probe "
            "(sync the eval extra first); refusing to guess without it"
        ) from exc

    api = HfApi()
    deadline = time.monotonic() + timeout_seconds
    outcomes: dict[str, dict[str, Any]] = {}
    for repo in ALIAS_REPOS:
        try:
            info = api.dataset_info(repo, timeout=15.0)
            sha = getattr(info, "sha", None)
            outcomes[repo] = {
                "accessible": True,
                "sha": sha,
                "license": getattr(info, "license", None)
                or (getattr(info, "cardData", None) or {}).get("license"),
            }
        except Exception as exc:  # noqa: BLE001 - recorded, not raised
            outcomes[repo] = {"accessible": False, "error": f"{type(exc).__name__}: {exc}"}
        if time.monotonic() > deadline:
            raise TimeoutError("alias resolution exceeded --timeout-seconds")
    if explicit_repo is not None:
        outcome = outcomes.get(explicit_repo)
        if outcome is None or not outcome.get("accessible"):
            raise ValueError(
                f"explicit --repo '{explicit_repo}' is not accessible "
                f"(outcomes: {json.dumps(outcomes, sort_keys=True)})"
            )
        return explicit_repo, outcomes
    accessible = [repo for repo, outcome in outcomes.items() if outcome.get("accessible")]
    if len(accessible) != 1:
        raise ValueError(
            "refusing to silently choose between 'openbmb/UltraX-Preview' and "
            f"'openbmb/UltraX' (accessible: {accessible}); "
            "re-run with an explicit --repo"
        )
    return accessible[0], outcomes


def _verify_config(repo: str, config: str, *, revision: str) -> list[str]:
    """List configs at the exact pinned revision (never HEAD)."""
    try:
        from datasets import get_dataset_config_names
    except ImportError as exc:
        raise RuntimeError(
            "datasets is required for the operator probe "
            "(sync the eval extra first); refusing to guess without it"
        ) from exc
    # No trust_remote_code: removed in datasets 5.x; UltraX needs no remote code.
    names = list(get_dataset_config_names(repo, revision=revision))
    if config not in names:
        raise ValueError(
            f"config '{config}' not found in repository '{repo}'; "
            f"observed configs: {sorted(names)[:20]}"
        )
    return sorted(names)


def _describe_features(repo: str, config: str, *, revision: str) -> dict[str, str]:
    """Describe features at the exact pinned revision (never HEAD)."""
    try:
        from datasets import load_dataset_builder
    except ImportError as exc:
        raise RuntimeError("datasets is required for the operator probe") from exc
    # No trust_remote_code: removed in datasets 5.x; UltraX needs no remote code.
    builder = load_dataset_builder(repo, config, revision=revision)
    features = builder.info.features or {}
    return {name: str(features[name])[:200] for name in features}


def _sample_rows(
    repo: str,
    config: str,
    split: str,
    max_rows: int,
    timeout_seconds: float,
    *,
    revision: str,
) -> tuple[list[dict[str, Any]], float]:
    """Stream a bounded sample at the exact pinned revision (never HEAD)."""
    try:
        from datasets import load_dataset
    except ImportError as exc:
        raise RuntimeError("datasets is required for the operator probe") from exc
    started = time.monotonic()
    # No trust_remote_code: removed in datasets 5.x; UltraX needs no remote code.
    dataset = load_dataset(repo, config, split=split, streaming=True, revision=revision)
    rows: list[dict[str, Any]] = []
    for row in dataset:
        rows.append({k: row.get(k) for k in row})
        if len(rows) >= max_rows:
            break
        if time.monotonic() - started > timeout_seconds:
            raise TimeoutError("bounded sample exceeded --timeout-seconds")
    return rows, time.monotonic() - started


def _uid_format(uids: list[str]) -> dict[str, Any]:
    lengths = sorted({len(u) for u in uids})
    ascii_only = all(u.isascii() for u in uids)
    charset = "".join(sorted({c for u in uids for c in u}))[:200]
    digest = hashlib.sha256("|".join(sorted(uids)).encode("utf-8")).hexdigest()
    return {
        "count": len(uids),
        "unique": len(set(uids)),
        "min_length": min((len(u) for u in uids), default=0),
        "max_length": max((len(u) for u in uids), default=0),
        "distinct_lengths": lengths[:20],
        "ascii_only": ascii_only,
        "charset_sample": charset,
        "sorted_sample_sha256": digest,
    }


def cert_source_file(repository: str, revision_sha: str, config: str, split: str) -> str:
    """Deterministic truthful virtual stream locator for certification rows.

    Hugging Face streaming does not expose a proven physical parquet filename
    per returned row in this probe, so no parquet filename is fabricated.
    The ``hf-stream://`` URI binds repository, exact revision, config and
    split; the row index travels separately in ``_cert_source_row`` and the
    revision is duplicated in ``_cert_revision`` for equality checks.
    """
    for name, value in (
        ("repository", repository),
        ("config", config),
        ("split", split),
    ):
        if not isinstance(value, str) or not value.strip() or any(ch in value for ch in " \t\n\r@"):
            raise ValueError(f"cert locator {name} must be a non-empty token without @/space")
    if not SHA_RE.fullmatch(revision_sha):
        raise ValueError("cert locator revision must be an exact 40-hex commit SHA")
    return f"hf-stream://{repository}@{revision_sha}/{config}/{split}"


def build_receipt(
    *,
    repository: str,
    revision_sha: str,
    config: str,
    split: str,
    configs_observed: list[str],
    features: dict[str, str],
    rows: list[dict[str, Any]],
    declared_license: Any,
    alias_outcomes: dict[str, dict[str, Any]],
    elapsed_seconds: float,
    max_rows: int,
    timeout_seconds: float,
) -> dict[str, Any]:
    field_names = sorted(features)
    schema_match = sorted(field_names) == sorted(EXPECTED_FIELDS)
    uids = [r.get("uid") for r in rows if isinstance(r.get("uid"), str)]
    sources = collections.Counter(
        r.get("source") if isinstance(r.get("source"), str) else "<non-string>" for r in rows
    )
    cleaned_lengths: list[int] = []
    raw_lengths: list[int] = []
    empty_cleaned = 0
    for row in rows:
        cleaned = row.get("cleaned_content")
        raw = row.get("raw_content")
        if isinstance(cleaned, str):
            cleaned_lengths.append(len(cleaned))
            if not cleaned.strip():
                empty_cleaned += 1
        if isinstance(raw, str):
            raw_lengths.append(len(raw))
    functions = collections.Counter(
        r.get("processed_functions")
        if isinstance(r.get("processed_functions"), str)
        else "<non-string>"
        for r in rows
    )

    def _stats(values: list[int]) -> dict[str, Any]:
        if not values:
            return {"count": 0}
        ordered = sorted(values)
        return {
            "count": len(values),
            "min": ordered[0],
            "max": ordered[-1],
            "mean": sum(values) / len(values),
        }

    return {
        "probe_version": PROBE_VERSION,
        "probe": "ultrax-schema-probe-v1",
        "repository": repository,
        "tested_aliases": alias_outcomes,
        "revision_sha": revision_sha,
        "config": config,
        "split": split,
        "expected_config": EXPECTED_CONFIG,
        "config_verified": config == EXPECTED_CONFIG and config in configs_observed,
        "configs_observed": configs_observed,
        "field_names": field_names,
        "field_types": features,
        "expected_fields": list(EXPECTED_FIELDS),
        "schema_match": schema_match,
        "rows_sampled": len(rows),
        "max_rows": max_rows,
        "uid_format": _uid_format(uids),
        "source_values": dict(sorted(sources.items())),
        "cleaned_content_empty_count": empty_cleaned,
        "cleaned_content_empty_rate": (empty_cleaned / len(rows)) if rows else None,
        "cleaned_content_lengths": _stats(cleaned_lengths),
        "raw_content_lengths": _stats(raw_lengths),
        "processed_functions": dict(sorted(functions.items())),
        "processed_functions_examples": sorted(functions)[:10],
        "declared_license": declared_license,
        "license_caveat": (
            "UltraX is derived from source corpora and users must check "
            "applicable source-dataset licenses."
        ),
        "budgets": {
            "max_rows": max_rows,
            "timeout_seconds": timeout_seconds,
            "elapsed_seconds": elapsed_seconds,
            "full_download": False,
        },
        "observed_at": datetime.datetime.now(datetime.UTC).isoformat(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bounded UltraX schema probe (operator-run).")
    parser.add_argument(
        "--repo", default=None, help="Explicit repository; omit with --probe-aliases."
    )
    parser.add_argument(
        "--probe-aliases",
        action="store_true",
        help="Check both documented aliases before selecting exactly one.",
    )
    parser.add_argument("--config", default=EXPECTED_CONFIG)
    parser.add_argument("--split", default="train")
    parser.add_argument("--max-rows", type=int, default=30)
    parser.add_argument("--timeout-seconds", type=float, default=300.0)
    parser.add_argument("--output", type=Path, default=Path("ultrax_probe_receipt.json"))
    parser.add_argument(
        "--save-sample",
        type=Path,
        default=None,
        help="Optional bounded JSONL path for the sampled rows (adapter-cert input).",
    )
    args = parser.parse_args(argv)

    if not 1 <= args.max_rows <= 100:
        return _fail("--max-rows must be in [1, 100]; no full parquet download")
    if not 1 <= args.timeout_seconds <= 1800:
        return _fail("--timeout-seconds must be in [1, 1800]")
    if args.config != EXPECTED_CONFIG:
        return _fail(f"--config must be '{EXPECTED_CONFIG}' for Mix-01")
    if args.repo is None and not args.probe_aliases:
        return _fail(
            "pass --probe-aliases or an explicit --repo; aliases are never silently chosen"
        )

    started = time.monotonic()
    try:
        repository, alias_outcomes = _resolve_aliases(
            args.repo, timeout_seconds=min(args.timeout_seconds, 120.0)
        )
    except Exception as exc:  # noqa: BLE001 - operator-facing refusal
        return _fail(str(exc))
    revision_sha = (alias_outcomes[repository].get("sha") or "").strip()
    if not SHA_RE.fullmatch(revision_sha):
        return _fail(
            f"repository '{repository}' did not resolve to an exact 40-hex commit SHA "
            f"(got {revision_sha!r}); refusing main/latest/unpinned"
        )
    try:
        configs_observed = _verify_config(repository, args.config, revision=revision_sha)
        features = _describe_features(repository, args.config, revision=revision_sha)
        rows, sample_seconds = _sample_rows(
            repository,
            args.config,
            args.split,
            args.max_rows,
            args.timeout_seconds,
            revision=revision_sha,
        )
        cert_locator = cert_source_file(repository, revision_sha, args.config, args.split)
    except Exception as exc:  # noqa: BLE001 - operator-facing refusal
        return _fail(str(exc))

    receipt = build_receipt(
        repository=repository,
        revision_sha=revision_sha,
        config=args.config,
        split=args.split,
        configs_observed=configs_observed,
        features=features,
        rows=rows,
        declared_license=alias_outcomes[repository].get("license"),
        alias_outcomes=alias_outcomes,
        elapsed_seconds=time.monotonic() - started,
        max_rows=args.max_rows,
        timeout_seconds=args.timeout_seconds,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    tmp = args.output.with_name(args.output.name + ".tmp")
    tmp.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(args.output)
    if args.save_sample is not None:
        # Deterministic bytes: UTF-8 WITHOUT BOM, LF newlines (Python-owned;
        # never a PowerShell rewrite). The live-certification reader refuses
        # BOM input fail-closed, so this writer is the evidence authority.
        args.save_sample.parent.mkdir(parents=True, exist_ok=True)
        with args.save_sample.open("w", encoding="utf-8", newline="\n") as handle:
            for index, row in enumerate(rows):
                handle.write(
                    json.dumps(
                        {
                            **row,
                            "_cert_source_file": cert_locator,
                            "_cert_source_row": index,
                            "_cert_revision": revision_sha,
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
    print(f"repository: {repository}")
    print(f"revision_sha: {revision_sha}")
    print(f"config_verified: {receipt['config_verified']}")
    print(f"schema_match: {receipt['schema_match']} fields={receipt['field_names']}")
    print(f"rows_sampled: {receipt['rows_sampled']}")
    print(f"receipt: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
