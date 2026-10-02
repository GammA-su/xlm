"""Bounded offline replay of the operator's pinned samples; never emits corpus text."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import Any

from xlm.data.acquisition import component_calibration as cc
from xlm.data.adapters.common_pile_adapters import CommonPileAdapter
from xlm.data.adapters.mix01_adapters import RecordRejectedError
from xlm.data.adapters.rejections import serialize_document
from xlm.data.evidence_v2 import canonical
from xlm.data.sources import certified_evidence as ce
from xlm.data.sources import common_pile_evidence as cpe
from xlm.data.sources import common_pile_license as lic

ROOT = Path(__file__).resolve().parents[4]
OUT = Path(__file__).resolve().parent
DATA = Path("G:/XLM")
EXPECTED = {
    "common_pile_cal01": (
        "f7d74b4a2e2614e10a8231653ef36831aecb7546955feef73d6df89f9b4be289",
        {"files": 10, "rows": 1746, "requests": 76, "transferred_bytes": 4693869},
    ),
    "common_pile_cal02": (
        "5d57e4ea82e509e28faf69aa1e7c9ee95da38bbb26e4a7367a0dea0894071b67",
        {"files": 2, "rows": 64, "requests": 44, "transferred_bytes": 2883584},
    ),
}


def driver() -> Any:
    spec = importlib.util.spec_from_file_location("mix01_source", ROOT / "scripts/mix01_source.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def read(path: Path, cap: int = 128 * 1024**2) -> bytes:
    if path.stat().st_size > cap:
        raise ValueError(f"offline verification input exceeds {cap} B: {path}")
    return path.read_bytes()


def write(path: Path, value: Any) -> None:
    data = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")
    if len(data) > 16 * 1024**2:
        raise ValueError("text-free audit output exceeds 16 MiB")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(data)
    temporary.replace(path)


def main() -> None:
    os.environ.update(HF_HUB_OFFLINE="1", HF_DATASETS_OFFLINE="1", XLM_HOME="G:/XLM/xlm-home")
    cli = driver()
    args = cli.build_parser().parse_args(
        ["evidence", "show", "--source-key", "common_pile", "--data-root", str(DATA)]
    )
    pin = cli.pin_of(cli.spec_of("common_pile"))
    inventory, _, _ = cli.production_inventory(args, pin)
    allowlist = json.loads(read(DATA / "calib/component_allowlists/common_pile.json"))
    if allowlist["digest"] != "b2bb7c0dc532a6263ca17b78816186fa194c12c09c74614baaeb29c76cdaab04":
        raise ValueError("allowlist changed")
    if (
        inventory["inventory_digest"]
        != "c0984aa33fb3598a9e724b9518df8cd8a0f2af52b5708ec211fb7fd693df7637"
    ):
        raise ValueError("production inventory changed")
    preview = json.loads(
        read(
            OUT.parent
            / "COMMON-PILE-BALANCED-PRODUCTION-READINESS"
            / "calibration-authorization-preview.json"
        )
    )
    targets = {e["file"]: e for e in preview["targets"]}
    samples: list[tuple[bytes, bytes]] = []
    inputs: list[dict[str, Any]] = []
    reports: dict[str, Any] = {}
    seen: set[str] = set()
    adapter = CommonPileAdapter()
    for name, (digest, totals) in EXPECTED.items():
        directory = DATA / "calib" / name
        receipt_bytes = read(directory / "sample-receipt.json", 16 * 1024**2)
        rows_bytes = read(directory / "real-records.jsonl")
        receipt = json.loads(receipt_bytes)
        cc.check_sample_receipt(receipt)
        if receipt["digest"] != digest or receipt["totals"] != totals:
            raise ValueError(f"{name}: operator-reported digest or totals disagree")
        samples.append((receipt_bytes, rows_bytes))
        for filename, data in (
            ("sample-receipt.json", receipt_bytes),
            ("real-records.jsonl", rows_bytes),
        ):
            inputs.append(
                {
                    "path": str(directory / filename),
                    "bytes": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                }
            )
        saved = {}
        for line in rows_bytes.splitlines():
            row = json.loads(line)
            key = (row["_cert_source_file"], row["_cert_source_row"])
            if key in saved:
                raise ValueError("duplicate row identity")
            saved[key] = row
        files = []
        for entry in receipt["files"]:
            cc.check_sample(entry)
            filename = entry["file"]
            if filename not in targets or filename in seen:
                raise ValueError("extra or duplicate sample target")
            seen.add(filename)
            expected_rows = 32 if entry["component"] == "project_gutenberg" else 256
            if entry["rows_requested"] != expected_rows or entry["row_indices"] != [
                0,
                entry["rows"],
            ]:
                raise ValueError("requested rows or row indices differ")
            whole = entry["component"] in {"oercommons", "public_domain_review"}
            if entry["whole_file_read"] != whole:
                raise ValueError("sample completeness differs from operator result")
            if whole and entry["identity"]["total_bytes"] != entry["transfer"]["transferred_bytes"]:
                raise ValueError("whole-file sample lacks complete transfer")
            if (
                entry["url"]
                != f"https://huggingface.co/datasets/{pin.repository}/resolve/{pin.revision}/{filename}"
            ):
                raise ValueError("sample URL differs from pinned exact target")
            if entry["identity"]["linked"]["X-Repo-Commit"] != pin.revision:
                raise ValueError("linked source revision differs")
            for detail in entry["rows_detail"]:
                row = saved[(filename, detail["row"])]
                if len(row["text"].encode("utf-8")) != detail["text_utf8_bytes"]:
                    raise ValueError("saved text length differs from receipt")
                try:
                    doc = adapter.adapt(
                        {"text": row["text"]},
                        source_file=filename,
                        source_row=detail["row"],
                        source_revision=pin.revision,
                    )
                except RecordRejectedError as exc:
                    if detail["outcome"] != type(exc).__name__:
                        raise ValueError("saved row rejection differs") from exc
                else:
                    payload = serialize_document(doc).encode("utf-8") + b"\n"
                    if (
                        detail["outcome"] != "accepted"
                        or detail["doc_id"] != doc.doc_id
                        or detail["document_sha256"] != hashlib.sha256(payload).hexdigest()
                    ):
                        raise ValueError("saved document identity differs")
            files.append(
                {
                    k: entry[k]
                    for k in (
                        "file",
                        "rows",
                        "rows_requested",
                        "whole_file_read",
                        "transfer",
                        "adapter",
                    )
                }
            )
        reports[name] = {"digest": digest, "totals": totals, "files": files, "status": "VERIFIED"}
    if seen != set(targets):
        raise ValueError("sample targets do not exactly cover the twelve authorized files")
    facts = cpe.translate_component_samples(pin, samples, allowlist=allowlist, inventory=inventory)
    certification = ce.certify_adapter(pin, facts, ce.view_schema(pin, facts))
    target = cli.store()
    metadata = ce.generic_probe_record(target, pin.source_id, pin.view_id)
    if metadata is None:
        raise ValueError("stored pinned metadata probe missing")
    record, artifact_id, sha256 = metadata
    if (
        record.outcome.value,
        record.evidence_type.value,
        record.declared_license,
        record.is_gated,
        record.observed_files_count,
        record.total_files_count_declared,
    ) != ("partial", "real_observed", None, False, 2, 2):
        raise ValueError("metadata outcome differs from operator report")
    if (record.resource_metrics["bytes_transferred"], record.resource_metrics["requests_made"]) != (
        127456,
        2,
    ):
        raise ValueError("metadata cost differs from operator report")
    facts.probe["component_license_basis"] = lic.build_basis(allowlist)
    ce.translate_store_probe(pin, facts, *metadata)
    for item in inputs:
        if hashlib.sha256(read(Path(item["path"]))).hexdigest() != item["sha256"]:
            raise ValueError("input changed during offline verification")
    result = {
        "kind": "common-pile-post-calibration-verification-v1",
        "network": False,
        "source": pin.as_dict(),
        "allowlist_digest": allowlist["digest"],
        "inventory_digest": inventory["inventory_digest"],
        "inputs": inputs,
        "samples": reports,
        "adapter": certification,
        "metadata": {
            "artifact_id": artifact_id,
            "file_sha256": sha256,
            "status": "VERIFIED",
            "record": record.model_dump(mode="json"),
        },
        "limitations": [
            "Original compressed sample streams were not retained; offline replay verifies "
            "receipts and saved adapter rows, not gzip CRC anew.",
            "Original upstream line hashes remain receipt evidence; saved JSON serialization "
            "is not the upstream byte representation.",
        ],
    }
    result["digest"] = canonical.digest(result)
    write(OUT / "verification.json", result)
    print(f"VERIFIED {result['digest']}: 1810 rows, 12 files, pinned metadata; no network")


if __name__ == "__main__":
    main()
