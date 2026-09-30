"""OFFLINE: freeze the reviewed Batch-0 amendment without authorizing execution."""

from __future__ import annotations

import json
from pathlib import Path

import essential_web_fast as driver

from xlm.data.acquisition.source_parquet import file_sha256
from xlm.data.evidence_v2 import canonical
from xlm.data.sources import essential_web_recovery as recovery


def main() -> None:
    repo = Path(__file__).resolve().parents[1]
    config = driver.read_json(repo / driver.FAST_DIR / "campaign.json")
    evidence = repo / recovery.MANIFEST
    audit = driver.read_json(evidence.with_name("audit.json"))
    batch = driver.read_json(Path("G:/XLM/plans/ew-fast/b0000/batch.json"))
    driver.check_digest(batch, "original batch")
    if (
        audit["campaign"] != config["digest"]
        or batch["campaign"] != config["digest"]
        or (audit["sealed"], audit["total"]) != (31, 32)
        or len(audit["diagnostics"]) != 1
    ):
        raise RuntimeError("audit does not describe the narrow Batch-0 recovery")
    diagnosis = audit["diagnostics"][0]
    if diagnosis["file"] != batch["files"][26] or diagnosis["rows"] != diagnosis["footer_rows"]:
        raise RuntimeError("audit file/row coverage differs")
    manifest = driver.self_digest(
        {
            "kind": "essential_web_batch_recovery_v1",
            "campaign": config["digest"],
            "batch": 0,
            "batch_digest": batch["digest"],
            "original_authorization": batch["authorization_digest"],
            "file": diagnosis["file"],
            "source_sha256": diagnosis["source_sha256"],
            "old_max_record_bytes": config["limits"]["max_record_bytes"],
            "max_record_bytes": diagnosis["maximum_raw_bytes"],
            "basis": "exact maximum canonical raw record bytes over all rows of the hash-bound "
            "retained file; only this file receives the larger processing bound",
            "audit_sha256": file_sha256(evidence.with_name("audit.json"))[0],
            "sealed_receipts": {u["key"]: u["receipt"] for u in audit["verified_units"]},
            "original_code": config["transport_code_sha256"],
            "code": recovery.code_identity(repo),
            "science_changed": False,
            "original_plan_changed": False,
            "scope": "Batch 0 only; retained source required; no replacement of prior seals",
        }
    )
    if canonical.digest({k: v for k, v in manifest.items() if k != "digest"}) != manifest["digest"]:
        raise RuntimeError("amendment digest did not reproduce")
    driver.write_json(evidence, manifest)
    print(json.dumps({"recovery_digest": manifest["digest"], "authorized": False}, indent=2))


if __name__ == "__main__":
    main()
