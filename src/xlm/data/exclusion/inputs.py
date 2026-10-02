"""Read-only, bounded inventory of sealed Mix-01 inputs for global C05 preparation.

This verifies metadata and file sizes, not corpus contents. Document identities
are transitively bound by acquisition's document-file SHA-256s. A future authorized
scanner must recompute those hashes while reading; this inventory is not a receipt
that C05 ran, a training pool, or permission to train.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from xlm.data.acquisition import source_run
from xlm.data.evidence_v2 import canonical
from xlm.data.sources import essential_web_pool_seal

SOURCE_KEYS = (
    "ew-fast",
    "ultrax",
    "finepdfs",
    "synth",
    "wiki_rewrite",
    "finewiki",
    "ifm_general",
    "ifm_planning",
    "common_pile",
    "simple_stories",
)
COMPONENTS = frozenset(
    {
        "essential_science",
        "essential_practical",
        "essential_prose",
        "ultrax_ultrafineweb",
        "finepdfs_en",
        "synth_en_explanations",
        "nemotron_wiki_rewrite",
        "finewiki_en",
        "ifm_behaviors_general_planning",
        "common_pile_prose",
        "simple_stories",
    }
)
MAX_METADATA_BYTES = 8 * 1024**2
MAX_UNITS = 10_000


class InputError(ValueError):
    """The sealed input cannot be reproduced from the local metadata."""


def read_metadata(path: Path, *, digested: bool = True) -> dict[str, Any]:
    """Read small strict JSON only; never follow a metadata path to corpus text."""
    with path.open("rb") as stream:
        raw = stream.read(MAX_METADATA_BYTES + 1)
    if len(raw) > MAX_METADATA_BYTES:
        raise InputError("metadata exceeds 8 MiB limit")
    value = canonical.loads_bytes_strict(raw)
    if not isinstance(value, dict):
        raise InputError("metadata must be an object")
    if digested and value.get("digest") != canonical.self_digest(value):
        raise InputError(f"metadata digest mismatch: {path.name}")
    return value


def contained(root: Path, relative: str) -> Path:
    """Resolve a data-only relative path, refusing traversal and external links."""
    target = (root / relative).resolve()
    if Path(relative).is_absolute() or not target.is_relative_to(root.resolve()):
        raise InputError("input path escapes data root")
    return target


def require_equal(actual: Any, expected: Any, what: str) -> None:
    if actual != expected:
        raise InputError(f"{what} differs from sealed metadata")


def file_entry(
    root: Path,
    directory: Path,
    facts: dict[str, Any],
    *,
    source_key: str,
    component: str,
    view: str,
    source_file: str,
    receipt: dict[str, Any],
) -> dict[str, Any]:
    path = contained(root, (directory / "documents.jsonl").relative_to(root).as_posix())
    require_equal(path.stat().st_size, facts["documents_file_bytes"], "canonical file size")
    return {
        "path": path.relative_to(root).as_posix(),
        "source_key": source_key,
        "component": component,
        "view": view,
        "upstream_component": source_file.split("/")[0] if source_key == "common_pile" else None,
        "source_file": source_file,
        "receipt_digest": receipt["digest"],
        "documents_sha256": facts["documents_sha256"],
        "file_bytes": facts["documents_file_bytes"],
        "canonical_bytes": facts["canonical_bytes"],
        "documents": facts["documents"],
        "document_identity_binding": (
            "all doc_id/source_revision/file/row fields in documents_sha256"
        ),
    }


def source_inputs(
    root: Path, scratch: Path, key: str
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Reproduce source sufficiency, accounting and sealed unit membership read-only."""
    seal_path = root / "plans" / key / "first-pass-seal.json"
    seal = read_metadata(seal_path)
    require_equal(seal["kind"], source_run.SEAL_KIND, "seal kind")
    require_equal(seal["stage"], "first_pass_canonical_availability", "seal stage")
    require_equal(seal["training_permitted"], False, "first-pass training prohibition")
    require_equal(seal["source_key"], key, "source key")
    if not 0 < len(seal["units"]) <= MAX_UNITS:
        raise InputError("sealed unit count exceeds inventory bounds")
    roots = source_run.Roots(root, scratch, key)
    status = source_run.sufficiency(roots)
    require_equal(status["status"], "SUFFICIENT", "sufficiency")
    require_equal(
        {k: v for k, v in status.items() if k != "digest"},
        seal["sufficiency"],
        "sufficiency reconstruction",
    )
    plans = {
        int(p["sequence"]): source_run.load_plan(roots, int(p["sequence"])) for p in seal["plans"]
    }
    for plan in seal["plans"]:
        require_equal(plans[int(plan["sequence"])]["digest"], plan["digest"], "plan digest")
    files: list[dict[str, Any]] = []
    receipts: list[dict[str, Any]] = []
    for unit in seal["units"]:
        sequence, rank = int(unit["sequence"]), int(unit["rank"])
        directory = roots.unit_dir(sequence, rank)
        receipt = read_metadata(directory / "receipt.json")
        require_equal(receipt["digest"], unit["receipt_digest"], "unit receipt")
        require_equal(receipt["source"], seal["source"], "source identity")
        require_equal(receipt["plan"]["digest"], plans[sequence]["digest"], "unit plan")
        require_equal(receipt["plan"]["sequence"], sequence, "unit sequence")
        for field in ("rank", "file", "documents", "canonical_bytes", "documents_sha256"):
            require_equal(receipt[field], unit[field], f"unit {field}")
        require_equal(receipt["raw"]["sha256"], unit["raw_sha256"], "raw identity")
        receipts.append(receipt)
        files.append(
            file_entry(
                root,
                directory,
                receipt,
                source_key=key,
                component=seal["source"]["component_id"],
                view=seal["source"]["view_id"],
                source_file=unit["file"],
                receipt=receipt,
            )
        )
    require_equal(source_run.membership_digest(receipts), seal["membership_digest"], "membership")
    require_equal(
        sum(f["canonical_bytes"] for f in files),
        status["acquired_canonical_bytes"],
        "source canonical total",
    )
    return {
        "source_key": key,
        "source": seal["source"],
        "seal_path": seal_path.relative_to(root).as_posix(),
        "seal_digest": seal["digest"],
        "seal_file_sha256": hashlib.sha256(seal_path.read_bytes()).hexdigest(),
        "membership_digest": seal["membership_digest"],
        "plans": seal["plans"],
        "accounting_digests": status["accounting"],
        "sufficiency": status,
        "adapter_binding": {str(s): p["inputs"] for s, p in plans.items()},
    }, files


def essential_inputs(root: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    seal_path = root / "plans/ew-fast/first-pass-seal.json"
    seal = read_metadata(seal_path)
    essential_web_pool_seal.check_seal(seal)
    if not 0 < len(seal["units"]) <= MAX_UNITS:
        raise InputError("sealed unit count exceeds inventory bounds")
    batches = {b["index"]: b for b in seal["batches"]}
    for index, batch in batches.items():
        directory = root / f"plans/ew-fast/b{index:04d}"
        record = read_metadata(directory / "batch.json")
        require_equal(record["digest"], batch["batch_record_digest"], "Essential batch")
        require_equal(record["campaign"], seal["campaign"]["digest"], "Essential campaign")
        for name, field in (
            ("authorization.json", "authorization_digest"),
            ("recovery-authorization.json", "recovery_authorization_digest"),
        ):
            if field in batch:
                auth = read_metadata(directory / name, digested=False)
                auth_field = "authorization_digest" if name == "authorization.json" else "digest"
                require_equal(auth[auth_field], batch[field], "Essential authorization")
    files: list[dict[str, Any]] = []
    for unit in seal["units"]:
        directory = root / f"canonical/ew-fast/b{unit['batch']:04d}/f{unit['rank']:05d}"
        receipt = read_metadata(directory / "receipt.json")
        require_equal(essential_web_pool_seal.unit_entry(receipt), unit, "Essential unit")
        require_equal(receipt["campaign"], seal["campaign"]["digest"], "Essential unit campaign")
        require_equal(
            receipt["plan"]["plan_hash"], batches[unit["batch"]]["plan_hash"], "Essential plan hash"
        )
        for view, facts in receipt["views"].items():
            files.append(
                file_entry(
                    root,
                    directory / view,
                    facts,
                    source_key="ew-fast",
                    component=view,
                    view=view,
                    source_file=unit["file"],
                    receipt=receipt,
                )
            )
    return {
        "source_key": "ew-fast",
        "source": seal["source"],
        "seal_path": seal_path.relative_to(root).as_posix(),
        "seal_digest": seal["digest"],
        "seal_file_sha256": hashlib.sha256(seal_path.read_bytes()).hexdigest(),
        "membership": seal["membership"],
        "campaign": seal["campaign"],
        "batches": seal["batches"],
        "sufficiency": seal["sufficiency"],
    }, files


def build_input_manifest(root: Path, scratch: Path) -> dict[str, Any]:
    """Inventory the complete fixed baseline. No wildcard source admission or writes."""
    root = root.resolve()
    sources: list[dict[str, Any]] = []
    files: list[dict[str, Any]] = []
    for key in SOURCE_KEYS:
        source, entries = (
            essential_inputs(root) if key == "ew-fast" else source_inputs(root, scratch, key)
        )
        sources.append(source)
        files.extend(entries)
    require_equal({f["component"] for f in files}, COMPONENTS, "eleven baseline components")
    if len({f["path"] for f in files}) != len(files):
        raise InputError("duplicate canonical file")
    if len(files) > MAX_UNITS:
        raise InputError("global file count exceeds bound")
    components = {
        c: {
            field: sum(int(f[field]) for f in files if f["component"] == c)
            for field in ("documents", "canonical_bytes", "file_bytes")
        }
        for c in sorted(COMPONENTS)
    }
    body: dict[str, Any] = {
        "kind": "c05_global_input_manifest",
        "version": 1,
        "data_root": root.as_posix(),
        "verification": (
            "metadata cross-bindings and canonical file sizes; content hashes NOT re-read"
        ),
        "sources": sources,
        "files": files,
        "components": components,
        "totals": {
            field: sum(int(f[field]) for f in files)
            for field in ("documents", "canonical_bytes", "file_bytes")
        },
        "excluded_optional_sources": ["txt360"],
        "training_permitted": False,
        "document_ids_materialized": False,
        "requires_scan_time_hash_verification": True,
    }
    body["digest"] = canonical.digest(body)
    return body


def verify_input_manifest(manifest: dict[str, Any], root: Path, scratch: Path) -> None:
    """Refuse source-seal/receipt/plan/size drift, without scanning corpus text."""
    require_equal(manifest.get("digest"), canonical.self_digest(manifest), "input manifest digest")
    require_equal(build_input_manifest(root, scratch), manifest, "current global inputs")
