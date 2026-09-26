# Requires: operator-run only, offline (deterministic file ordering).
"""Deterministic UltraX file ordering for acquisition selection (offline).

Orders candidate source files by the repository's deterministic hash chain
``SHA-256(seed | repository | revision | file)`` (the same construction as
``xlm.data.acquisition.sampling._det_index``): stable across processes,
Python versions and platforms, never mixing in timing, paths, worker counts
or network order. The operator takes a prefix of this order until enough
source material exists and later tops up with the next files in the same
order, without changing already admitted identities. Never "the first N
parquet files" in provider order.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path


def _det_key(seed: int, repository: str, revision: str, filename: str) -> str:
    return hashlib.sha256(
        "|".join([str(seed), repository, revision, filename]).encode("utf-8")
    ).hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Deterministic UltraX file ordering (offline).")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--seed", type=int, default=20260918)
    parser.add_argument("--files", type=Path, default=None)
    parser.add_argument("--file", action="append", default=[], dest="listed")
    parser.add_argument("--receipt", type=Path, default=None)
    parser.add_argument("--count", type=int, default=0)
    parser.add_argument("--output", type=Path, default=Path("ultrax_ordered_files.json"))
    args = parser.parse_args(argv)

    candidates: list[str] = list(args.listed)
    if args.files is not None:
        try:
            candidates += [
                line.strip()
                for line in args.files.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
        except OSError as exc:
            print(f"ultrax_order_files: error: {exc}", file=sys.stderr)
            return 1
    if args.receipt is not None:
        try:
            receipt = json.loads(args.receipt.read_text(encoding="utf-8"))
            observed = receipt.get("observed_files") or []
            candidates += [str(name) for name in observed if str(name).strip()]
        except Exception as exc:  # noqa: BLE001 - operator-facing refusal
            print(f"ultrax_order_files: error: cannot read receipt: {exc}", file=sys.stderr)
            return 1
    seen: list[str] = []
    for name in candidates:
        if name not in seen:
            seen.append(name)
    if not seen:
        print("ultrax_order_files: error: no candidate files supplied", file=sys.stderr)
        return 1
    if args.count < 0 or args.count > len(seen):
        print("ultrax_order_files: error: --count out of range", file=sys.stderr)
        return 1
    ordered = sorted(
        seen, key=lambda name: (_det_key(args.seed, args.repo, args.revision, name), name)
    )
    payload = {
        "repo": args.repo,
        "revision": args.revision,
        "seed": args.seed,
        "ordered_files": ordered,
        "selected_files": ordered[: args.count] if args.count else ordered,
        "count": args.count or len(ordered),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    tmp = args.output.with_name(args.output.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(args.output)
    for name in payload["selected_files"]:
        print(name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
