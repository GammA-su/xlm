"""Certified facts of a multi-component ``.jsonl.gz`` source from its prefix samples.

The admission bridge (:mod:`xlm.data.sources.certified_evidence`) certifies an
adapter against real rows and binds real file identities. For Common Pile those
come from bounded prefix samples (:mod:`xlm.data.acquisition.jsonl_gz_sample`),
one or more files per allowlisted component, whose saved rows live outside the
code checkout. This module translates them into :class:`CertifiedFacts`:

- every sampled row, keyed by its file and zero-based row, as a row set per
  file whose documents digest the receipt recorded (so the bridge refuses rows
  that are not the ones the receipt's adapter run saw);
- every sampled file's identity (length, strong ETag) from its receipt;
- the observed schema of the real rows (basis: observed rows of a real fetch);
- the bindings: component allowlist digest, production inventory digest,
  receipt digests and the saved-rows SHA-256.

It establishes no license. The repository declares none, so the bridge still
refuses until a real metadata probe declares one or an amended contract
defines a per-component license basis. Nothing here downloads anything.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

from xlm.data.acquisition import component_allowlist as allow
from xlm.data.evidence_v2 import canonical
from xlm.data.sources import certified_evidence as ce

SAMPLE_CERTIFICATION = "jsonl-gz-prefix-sample-v1"


def _check_receipt(receipt: Mapping[str, Any]) -> None:
    body = dict(receipt)
    if body.pop("digest", None) != canonical.digest(body):
        raise ce.BridgeRefusal("prefix sample receipt digest does not verify")
    if receipt.get("kind") != "jsonl_gz_prefix_sample" or receipt.get("version") != 1:
        raise ce.BridgeRefusal("not a version-1 prefix sample receipt")


def translate_component_samples(
    pin: ce.SourcePin,
    samples: Sequence[tuple[bytes, bytes]],
    *,
    allowlist: Mapping[str, Any],
    inventory: Mapping[str, Any],
) -> ce.CertifiedFacts:
    """Facts of ``(receipt bytes, saved rows bytes)`` pairs covering every allowlisted component."""
    if (pin.repository, pin.revision, pin.component_id) != (
        allowlist["repository"],
        allowlist["revision"],
        allowlist["component_id"],
    ):
        raise ce.BridgeRefusal("component allowlist is not bound to this source pin")
    if (inventory.get("component_allowlist") or {}).get("digest") != allowlist["digest"]:
        raise ce.BridgeRefusal("production inventory does not bind the component allowlist")
    sizes = {str(e["file"]): e["size_bytes"] for e in inventory["files"]}
    facts = ce.CertifiedFacts(
        kind=SAMPLE_CERTIFICATION,
        schema={},
        schema_basis=ce.BASIS_OBSERVED,
        declared_license=None,
        license_caveat=(
            "the repository declares no license; per-component license basis is the operator "
            f"component allowlist {allowlist['digest']} (not a repository declaration)"
        ),
        split=None,
        observed_at=None,
    )
    covered: set[str] = set()
    receipts: list[dict[str, str]] = []
    for receipt_bytes, rows_bytes in samples:
        receipt = json.loads(receipt_bytes.decode("utf-8"))
        _check_receipt(receipt)
        bindings = receipt.get("bindings") or {}
        if (
            receipt["repository"] != pin.repository
            or receipt["revision"] != pin.revision
            or receipt["adapter_id"] != pin.adapter_id
            or bindings.get("component_allowlist_digest") != allowlist["digest"]
            or bindings.get("production_inventory_digest") != inventory["inventory_digest"]
        ):
            raise ce.BridgeRefusal(
                "prefix sample is bound to another source, allowlist or inventory"
            )
        saved: dict[tuple[str, int], dict[str, Any]] = {}
        for line in rows_bytes.splitlines():
            if not line.strip():
                continue
            row = json.loads(line.decode("utf-8"))
            key = (str(row.pop("_cert_source_file")), int(row.pop("_cert_source_row")))
            row.pop("_cert_component", None)
            if row.pop("_cert_revision", pin.revision) != pin.revision:
                raise ce.BridgeRefusal("saved sample row names another revision")
            saved[key] = row
        expected = sum(int(f["rows"]) for f in receipt["files"])
        if len(saved) != expected:
            raise ce.BridgeRefusal("saved sample rows differ from the receipt's row count")
        for entry in receipt["files"]:
            name = str(entry["file"])
            if sizes.get(name) != entry["identity"]["total_bytes"]:
                raise ce.BridgeRefusal(f"'{name}' is not a file of the production inventory")
            component = allow.component_of(name)
            if component not in allowlist["included"]:
                raise ce.BridgeRefusal(f"'{name}' belongs to an excluded component")
            covered.add(component)
            start = len(facts.rows)
            for index in range(int(entry["rows"])):
                if (name, index) not in saved:
                    raise ce.BridgeRefusal(f"saved rows lack {name} row {index}")
                facts.rows.append((name, index, saved[(name, index)]))
            label = f"sample:{name}"
            facts.row_sets[label] = (start, len(facts.rows))
            adapter = entry["adapter"]
            facts.recorded_documents[label] = (
                int(adapter["accepted"]),
                int(adapter["rejected"]),
                str(adapter["documents_sha256"]),
            )
            facts.observed_files.append(
                {
                    "path": name,
                    "length": int(entry["identity"]["total_bytes"]),
                    "etag": str(entry["identity"]["etag"]),
                    "evidence": "bounded prefix sample receipt (revision-pinned resolve)",
                    "receipt_digest": str(receipt["digest"]),
                }
            )
        receipts.append(
            {
                "label": str(receipt["label"]),
                "digest": str(receipt["digest"]),
                "receipt_sha256": hashlib.sha256(receipt_bytes).hexdigest(),
                "rows_sha256": hashlib.sha256(rows_bytes).hexdigest(),
            }
        )
    missing = sorted(set(allowlist["included"]) - covered)
    if missing:
        raise ce.BridgeRefusal(f"no real sample certifies allowlisted components {missing}")
    facts.schema = ce._observed_schema([row for _, _, row in facts.rows], ["text"])
    facts.inputs = {
        "component_allowlist": {"digest": str(allowlist["digest"])},
        "production_inventory": {"digest": str(inventory["inventory_digest"])},
        **{f"sample:{r['label']}": r for r in receipts},
    }
    facts.probe = {
        "kind": SAMPLE_CERTIFICATION,
        "components": sorted(covered),
        "receipts": [r["digest"] for r in receipts],
    }
    return facts
