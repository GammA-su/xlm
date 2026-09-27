# Requires: operator-run only, offline (applies an already-probed receipt).
"""Freeze the probed UltraX revision into the Mix-01 source definition.

Reads a ``ultrax_schema_probe`` receipt, refuses placeholders (``main``,
``latest``, empty, non-40-hex SHAs), and surgically pins
``observed_revision`` (plus ``observed_license`` when the receipt declares
one) for ``ultrax_ultrafineweb`` in both the view registry and the dataset
catalog. The patch is line-level and format-preserving: comments, style
and every other byte stay untouched, so re-running with the same SHA is a
byte-identical no-op. A file that already pins a DIFFERENT SHA is refused,
never silently re-pinned. Both files are re-validated with the
repository's strict loaders. Nothing here touches the network.
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
    if not repository:
        raise ValueError("receipt repository is empty; refusing to freeze")
    return repository, revision, receipt.get("declared_license")


def _read_lines(path: Path) -> list[str]:
    raw = path.read_bytes()
    if raw[:3] == b"\xef\xbb\xbf":
        raise ValueError(f"source definition '{path}' carries a UTF-8 BOM; refusing to patch")
    return raw.decode("utf-8").split("\n")


def _write_lines(path: Path, lines: list[str]) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(("\n".join(lines)).encode("utf-8"))
    tmp.replace(path)


def _pin_scalar(
    lines: list[str], *, key: str, current_ok: tuple[str, ...], new_value: str, scope: str
) -> bool:
    """Pin one ``key: <scalar>`` line inside a pre-scoped line range.

    Returns True when the file was changed. ``null`` is pinned; the same
    value is a no-op (idempotent); any other value is refused, never
    silently re-pinned. Exactly one matching line must exist in scope.
    """
    hits = [i for i in scope if re.fullmatch(rf"\s*{re.escape(key)}:.*", lines[i])]
    if len(hits) != 1:
        raise ValueError(f"expected exactly one '{key}' line in {scope}, found {len(hits)}")
    (index,) = hits
    match = re.fullmatch(r"(\s*" + re.escape(key) + r":\s*)(.*?)(\s*)", lines[index])
    assert match is not None
    current = match.group(2)
    if current in current_ok:
        return False
    if current != "null":
        raise ValueError(f"already pins {key}={current!r}, refusing to re-pin to {new_value!r}")
    lines[index] = f"{match.group(1)}{new_value}{match.group(3)}"
    return True


def _ultrax_block_range(lines: list[str], anchor: str) -> range:
    """Line range of the ultrax_ultrafineweb block in the block-YAML registry."""
    starts = [i for i, line in enumerate(lines) if line.strip() == f"- {anchor}"]
    if len(starts) != 1:
        raise ValueError(f"expected exactly one '- {anchor}' line, found {len(starts)}")
    (start,) = starts
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if re.fullmatch(r"  - component_id:.*", lines[i]):
            end = i
            break
    return range(start, end)


def _pin_views(path: Path, repository: str, revision: str, declared_license: Any) -> bool:
    lines = _read_lines(path)
    scope = _ultrax_block_range(lines, "component_id: ultrax_ultrafineweb")
    changed = False
    repo_hits = [i for i in scope if re.fullmatch(r"\s*repository:.*", lines[i])]
    if len(repo_hits) != 1:
        raise ValueError("expected exactly one 'repository' line in the ultrax view")
    current_repo = lines[repo_hits[0]].split(":", 1)[1].strip()
    if current_repo != repository:
        raise ValueError(
            f"registry pins repository {current_repo!r}, receipt says {repository!r}; "
            "alias changes are refused"
        )
    changed |= _pin_scalar(
        lines, key="observed_revision", current_ok=(revision,), new_value=revision, scope=scope
    )
    if isinstance(declared_license, str) and declared_license.strip():
        changed |= _pin_scalar(
            lines,
            key="observed_license",
            current_ok=(declared_license.strip(),),
            new_value=declared_license.strip(),
            scope=scope,
        )
    if changed:
        _write_lines(path, lines)
    return changed


def _pin_catalog(path: Path, repository: str, revision: str) -> bool:
    # The catalog is JSON-style YAML: scope the ultrax entry between its
    # "source_id" marker and the next "candidate_number" marker.
    lines = _read_lines(path)
    markers = [i for i, line in enumerate(lines) if '"source_id": "ultrax_ultrafineweb"' in line]
    if len(markers) != 1:
        raise ValueError(f"expected exactly one ultrax catalog entry, found {len(markers)}")
    (marker,) = markers
    end = len(lines)
    for i in range(marker + 1, len(lines)):
        if '"candidate_number":' in lines[i]:
            end = i
            break
    scope = range(marker, end)
    repo_hits = [i for i in scope if '"repository":' in lines[i]]
    if len(repo_hits) != 1:
        raise ValueError("expected exactly one repository line in the ultrax catalog entry")
    current_repo = lines[repo_hits[0]].split(":", 1)[1].strip().rstrip(",").strip().strip('"')
    if current_repo != repository:
        raise ValueError(
            f"catalog pins repository {current_repo!r}, receipt says {repository!r}; "
            "alias changes are refused"
        )
    rev_hits = [i for i in scope if '"revision":' in lines[i]]
    if len(rev_hits) != 1:
        raise ValueError("expected exactly one revision line in the ultrax catalog entry")
    (index,) = rev_hits
    match = re.fullmatch(r'(.*?"revision":\s*)(.*?)(\s*,?\s*)', lines[index])
    assert match is not None
    current = match.group(2)
    if current == f'"{revision}"':
        return False
    if current != "null":
        raise ValueError(
            f"catalog already pins revision={current!r}, refusing to re-pin to {revision!r}"
        )
    lines[index] = f'{match.group(1)}"{revision}"{match.group(3)}'
    _write_lines(path, lines)
    return True


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
        changed_views = _pin_views(args.views, repository, revision, declared_license)
        changed_catalog = _pin_catalog(args.catalog, repository, revision)
    except (ValueError, OSError) as exc:
        return _fail(str(exc))

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
    print(f"views: {args.views} ({'pinned' if changed_views else 'already pinned'})")
    print(f"catalog: {args.catalog} ({'pinned' if changed_catalog else 'already pinned'})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
