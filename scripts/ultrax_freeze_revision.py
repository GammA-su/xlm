# Requires: operator-run only, offline (applies an already-probed receipt).
"""Freeze the probed UltraX revision into the Mix-01 source definition.

Reads a ``ultrax_schema_probe`` receipt, refuses placeholders (``main``,
``latest``, empty, non-40-hex SHAs), and atomically pins
``observed_revision`` (plus ``observed_license`` when the receipt declares
one) for ``ultrax_ultrafineweb`` in both the view registry and the dataset
catalog. Re-validates both files with the repository's strict loaders.
Nothing here touches the network.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

SHA_RE = re.compile(r"^[0-9a-f]{40}$")
REFUSED = {"", "main", "latest", "master", "head", "null", "none", "todo", "tbd"}


def _fail(message: str) -> int:
    print(f"ultrax_freeze_revision: error: {message}", file=sys.stderr)
    return 1


def _load_receipt(path: Path) -> dict[str, Any]:
    try:
        receipt = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(f"cannot read probe receipt '{path}': {exc}") from exc
    if not isinstance(receipt, dict):
        raise ValueError("probe receipt must be a JSON object")
    return receipt


def _checked_revision(receipt: dict[str, Any]) -> tuple[str, str, Any]:
    revision = str(receipt.get("revision_sha") or "").strip()
    repository = str(receipt.get("repository") or "").strip()
    config = str(receipt.get("config") or "").strip()
    if not revision or revision.casefold() in REFUSED or not SHA_RE.fullmatch(revision):
        raise ValueError(
            f"receipt revision {revision!r} is not an exact 40-hex commit SHA; "
            "refusing main/latest/unpinned"
        )
    if not receipt.get("config_verified"):
        raise ValueError("receipt config is not verified; refusing to freeze")
    if config != "UltraX-Ultra-FineWeb":
        raise ValueError(f"receipt config {config!r} is not UltraX-Ultra-FineWeb")
    return repository, revision, receipt.get("declared_license")


def _atomic_write_json_compatible_yaml(path: Path, payload: Any) -> None:
    import yaml

    text = yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    tmp.replace(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Freeze the probed UltraX revision (offline).")
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument(
        "--views",
        type=Path,
        default=Path("recipes/mixtures/mix01_views.yaml"),
    )
    parser.add_argument(
        "--catalog",
        type=Path,
        default=Path("manifests/datasets.catalog.yaml"),
    )
    args = parser.parse_args(argv)

    try:
        receipt = _load_receipt(args.receipt)
        repository, revision, declared_license = _checked_revision(receipt)
    except ValueError as exc:
        return _fail(str(exc))

    try:
        import yaml
    except ImportError as exc:
        return _fail(f"pyyaml is required: {exc}")

    try:
        views_data = yaml.safe_load(args.views.read_text(encoding="utf-8"))
        catalog_data = yaml.safe_load(args.catalog.read_text(encoding="utf-8"))
    except Exception as exc:
        return _fail(f"cannot read source definition: {exc}")

    try:
        views = views_data["views"]
        match = [v for v in views if v.get("component_id") == "ultrax_ultrafineweb"]
        if len(match) != 1:
            raise ValueError("registry must declare exactly one ultrax_ultrafineweb view")
        match[0]["repository"] = repository
        match[0]["observed_revision"] = revision
        if isinstance(declared_license, str) and declared_license.strip():
            match[0]["observed_license"] = declared_license.strip()
        entries = catalog_data["sources"]
        candidates = [e for e in entries if e.get("source_id") == "ultrax_ultrafineweb"]
        if len(candidates) != 1:
            raise ValueError("catalog must declare exactly one ultrax_ultrafineweb source")
        candidates[0]["repository"] = repository
        candidates[0]["revision"] = revision
    except (KeyError, TypeError, ValueError) as exc:
        return _fail(str(exc))

    _atomic_write_json_compatible_yaml(args.views, views_data)
    _atomic_write_json_compatible_yaml(args.catalog, catalog_data)

    try:
        from xlm.data.sources.catalog import load_catalog
        from xlm.data.sources.mix01 import load_mix01_views

        registry = load_mix01_views(args.views)
        catalog = load_catalog(args.catalog)
        pinned_view = registry.get("ultrax_ultrafineweb")
        pinned_entry = catalog.get_source("ultrax_ultrafineweb")
        assert pinned_view.observed_revision == revision
        assert pinned_entry is not None and pinned_entry.revision == revision
    except Exception as exc:
        return _fail(f"frozen source definition failed validation: {exc}")

    print(f"repository: {repository}")
    print(f"revision: {revision}")
    print(f"views: {args.views}")
    print(f"catalog: {args.catalog}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
