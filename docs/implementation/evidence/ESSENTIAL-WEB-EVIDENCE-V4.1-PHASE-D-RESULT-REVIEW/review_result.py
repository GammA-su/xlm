"""Bounded offline post-live review. No engine run, decoding, labeling or G: writes."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import socket
import sqlite3
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import psutil
import yaml
from inspect_evidence import D, P, inventory

from xlm.data.evidence_v4 import phase_d
from xlm.data.evidence_v4 import phase_d_plan as pd

OUT = Path(__file__).resolve().parent
REPO = OUT.parents[3]
SCIENCE = REPO / "docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V2"
EXPECTED = {
    "protocol_sha256": "bb2bca6f571c9e02b02536bc518cdc1b61fdf0f56b0c6970eaa257c8d198769f",
    "freeze_digest": "9c4612fe860ef49c26ff09b25f8fdaf00f0a7015d7eab4fd7cfe28a0e1675228",
    "plan_digest": "23a26ffc87d27e4c8fa5de86d7ec446c260bfd65442dc5b1dfd2651cd62222b7",
    "selection_digest": "975ba3dee4af0598e665ea05c69bbe49336c3e3a35fca777a863073190b78474",
    "policy_digest": "f4357f61f434d5105d823266153dac6372e122f12767187631e8796b4899dd07",
}
START = time.monotonic()


class ReviewError(ValueError):
    pass


def check(value: Any, message: str) -> None:
    if not value:
        raise ReviewError(message)


def refuse(*_: Any, **__: Any) -> Any:
    raise ReviewError("network forbidden")


socket.create_connection = refuse
socket.getaddrinfo = refuse
socket.socket.connect = refuse
socket.socket.connect_ex = refuse


class NoTransport:
    def open(self, *_: Any, **__: Any) -> Any:
        raise ReviewError("acquisition forbidden")


def audit(event: str, args: tuple[Any, ...]) -> None:
    if event == "open" and isinstance(args[0], (str, bytes, os.PathLike)):
        path = os.fsdecode(args[0])
        mode, flags = args[1:3]
        if path.upper().startswith("G:") and (
            (mode and any(c in mode for c in "wax+"))
            or flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC)
        ):
            raise ReviewError("G: write refused")
    if event in ("os.remove", "os.rename", "os.mkdir", "os.rmdir", "os.chmod", "os.utime"):
        if any(isinstance(a, str) and a.upper().startswith("G:") for a in args):
            raise ReviewError("G: mutation refused")


sys.addaudithook(audit)


def canonical(obj: Any) -> bytes:
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def digest(obj: dict[str, Any]) -> str:
    return sha(canonical({k: v for k, v in obj.items() if k != "digest"}))


def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    obj = dict(pairs)
    check(len(obj) == len(pairs), "duplicate JSON key")
    return obj


def loads(raw: bytes) -> Any:
    def invalid(_: str) -> Any:
        raise ReviewError("non-finite JSON number")

    return json.loads(raw, object_pairs_hook=unique, parse_constant=invalid)


def load(path: Path) -> Any:
    check(path.stat().st_size <= 16777216, "JSON input byte cap")
    return loads(path.read_bytes())


def sealed(path: Path) -> dict[str, Any]:
    obj = load(path)
    check(digest(obj) == obj["digest"], "JSON self-digest mismatch: " + path.name)
    return obj


def binding(path: Path) -> dict[str, Any]:
    with path.open("rb") as stream:
        return {
            "bytes": path.stat().st_size,
            "sha256": hashlib.file_digest(stream, "sha256").hexdigest(),
        }


def write(name: str, obj: Any) -> None:
    check(Path(name).name == name, "review output name")
    raw = canonical(obj) + b"\n"
    check(len(raw) <= 2097152, "review output cap")
    check(
        sum(p.stat().st_size for p in OUT.rglob("*") if p.is_file()) + len(raw) <= 16777216,
        "review directory cap",
    )
    temp = OUT / (name + ".tmp")
    with temp.open("wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, OUT / name)


def lines(path: Path, maximum: int) -> list[tuple[bytes, dict[str, Any]]]:
    check(path.stat().st_size <= 67108864, "JSONL byte cap")
    result = []
    with path.open("rb") as stream:
        for raw in stream:
            check(len(result) < maximum and len(raw) <= 1048576, "JSONL record cap")
            obj = loads(raw)
            check(canonical(obj) + b"\n" == raw, "JSONL canonical bytes")
            result.append((raw, obj))
    return result


def main() -> None:
    before = {}
    for name, root in [("parent", P), ("phase-d", D)]:
        before[name] = inventory(root)
        check(
            before[name] == load(OUT / f"{name}-before.json"),
            "root changed since initial inventory",
        )
    check(not list(D.glob("state.sqlite-*")), "SQLite auxiliaries require separate reconciliation")
    plan = pd.load_committed_plan()
    plan_doc = sealed(REPO / pd.PLAN_PATH)
    freeze = sealed(REPO / pd.FREEZE_PATH)
    check(
        plan.digest == EXPECTED["plan_digest"] and freeze["digest"] == EXPECTED["freeze_digest"],
        "frozen plan/freeze",
    )
    check(
        sha((REPO / pd.PROTOCOL_PATH).read_bytes()) == EXPECTED["protocol_sha256"], "protocol bytes"
    )
    phase_d.verify_repository()
    engine = phase_d._PhaseDEngine(
        plan, D, P, NoTransport(), sleep=lambda _: None, clock=lambda: 0.0
    )  # no store opened, no run
    engine._verify_parents()
    parent_bindings = {}
    for item in plan.parents.artifacts:
        got = binding(P / item.rel)
        check(
            got == {"bytes": item.bytes, "sha256": item.sha256}, "independent parent byte binding"
        )
        parent_bindings[item.rel] = got
    docs = {
        name: sealed(D / name)
        for name in [
            phase_d.RECEIPT,
            phase_d.MANIFEST,
            phase_d.M_MANIFEST,
            phase_d.T_MANIFEST,
            phase_d.T_PROVENANCE,
        ]
    }
    receipt, manifest, mm, tm, provenance = (
        docs[n]
        for n in [
            phase_d.RECEIPT,
            phase_d.MANIFEST,
            phase_d.M_MANIFEST,
            phase_d.T_MANIFEST,
            phase_d.T_PROVENANCE,
        ]
    )
    common_fields = {
        "digest",
        "dry_plan_digest",
        "freeze_digest",
        "kind",
        "phase_p_parent",
        "plan_digest",
        "policy_digest",
        "protocol_sha256",
        "protocol_version",
        "scientific_namespace",
        "selection_digest",
        "source",
        "synthetic",
    }
    fields = {
        phase_d.RECEIPT: {
            "arms",
            "created_utc",
            "finalized_utc",
            "human_review",
            "limits",
            "operations",
            "outputs",
            "request_receipt",
            "run_status",
            "scientific_scoring",
            "source_files",
            "status",
            "stop_reason",
            "totals",
        },
        phase_d.MANIFEST: {"status", "receipt_digest", "artifacts"},
        phase_d.M_MANIFEST: {
            "files",
            "locator_field",
            "output",
            "projection",
            "records",
            "selector_decision",
            "status",
        },
        phase_d.T_MANIFEST: {
            "blinding",
            "files",
            "limits",
            "retained_text_bytes",
            "retained_unique_text_bytes",
            "sealed_outputs",
            "selected_locators",
            "status",
            "status_counts",
            "unselected_rows",
        },
        phase_d.T_PROVENANCE: {"access", "locators"},
    }
    for name, obj in docs.items():
        check(set(obj) == common_fields | fields[name], "unexpected export field: " + name)
    for obj in docs.values():
        for key, expected in EXPECTED.items():
            check(obj[key] == expected, "output identity " + key)
        check(obj["source"] == plan_doc["source"] and obj["synthetic"] is False, "source/synthetic")
        check(obj["scientific_namespace"] == "essential-web-evidence-v2.0", "scientific namespace")
        check(obj["dry_plan_digest"] == plan.parents.dry_plan_digest, "dry-plan binding")
        check(obj["phase_p_parent"] == receipt["phase_p_parent"], "parent cross-binding")
    expected_parent = dict(
        root=plan.parents.phase_p_root,
        plan_digest=plan.parents.phase_p_plan_digest,
        parents_digest=plan.parents.digest,
        receipt_digest=plan.parents.artifact("phase_p_receipt.json").digest,
        manifest_digest=plan.parents.artifact("artifact_manifest.json").digest,
        m_layout_digest=plan.parents.artifact("m_phase_p_layout.json").digest,
        t_layout_digest=plan.parents.artifact("t_phase_p_layout.json").digest,
    )
    check(receipt["phase_p_parent"] == expected_parent, "parent frozen identity")
    check(
        (receipt["status"], receipt["run_status"], receipt["stop_reason"], manifest["status"])
        == ("COMPLETE", "COMPLETE", None, "COMPLETE"),
        "COMPLETE receipts",
    )
    check(manifest["receipt_digest"] == receipt["digest"], "manifest receipt digest")
    check(receipt["limits"] == pd.LIMITS, "limit drift")
    files = {
        name: {"bytes": row["bytes"], "sha256": row["sha256"]}
        for name, row in before["phase-d"].items()
        if "sha256" in row
    }
    check(
        set(files) == set(manifest["artifacts"]) | {"state.sqlite", phase_d.MANIFEST},
        "unknown or missing retained file/log",
    )
    for name, entry in manifest["artifacts"].items():
        check(files.get(name) == entry, "artifact byte/hash mismatch: " + name)
    check(
        {phase_d.REQUESTS, phase_d.RECEIPT, *phase_d.ARM_OUTPUTS["M"], *phase_d.ARM_OUTPUTS["T"]}
        <= set(manifest["artifacts"]),
        "required exports",
    )
    with sqlite3.connect((D / "state.sqlite").as_uri() + "?mode=ro&immutable=1", uri=True) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        check(db.execute("PRAGMA integrity_check").fetchone()[0] == "ok", "SQLite integrity")
        check(not db.execute("PRAGMA foreign_key_check").fetchall(), "SQLite foreign keys")
        tables = {
            n: [dict(r) for r in db.execute("SELECT * FROM " + n)]
            for n in ["run", "operations", "attempts", "outputs", "decoded_outputs"]
        }
    check(len(tables["run"]) == 1, "run row count")
    run = tables["run"][0]
    check(
        run["status"] == "COMPLETE" and run["stop_reason"] is None and run["synthetic"] == 0,
        "run status",
    )
    for key in ["plan_digest", "selection_digest"]:
        check(run[key] == EXPECTED[key], "SQLite identity")
    check(
        run["source_revision"] == plan.fetch.source.revision
        and run["protocol_version"] == pd.PROTOCOL_VERSION,
        "run source/version",
    )
    ops = sorted(tables["operations"], key=lambda r: r["seq"])
    attempts = sorted(tables["attempts"], key=lambda r: r["attempt_id"])
    outputs = {r["op_id"]: r for r in tables["outputs"]}
    check(len(ops) == len(outputs) == 55 and len(attempts) == 110, "operation/attempt counts")
    check([r["attempt_id"] for r in attempts] == list(range(1, 111)), "attempt sequence")
    expected_files = (
        set(phase_d.EXPORTS)
        | {"state.sqlite"}
        | {r["retained_file"] for r in outputs.values()}
        | {a["temp_path"] for a in attempts if a["outcome"] != "SUCCESS"}
    )
    check(set(files) == expected_files, "extra exported/logged file")
    check(
        [row for _, row in lines(D / phase_d.REQUESTS, 400)] == attempts,
        "SQLite/request receipt equality",
    )
    check(
        receipt["request_receipt"]
        == {"path": phase_d.REQUESTS, "lines": 110, **files[phase_d.REQUESTS]},
        "request receipt binding",
    )
    r_ops = {r["op_id"]: r for r in receipt["operations"]}
    check(len(r_ops) == 55, "receipt operation uniqueness")
    hosts = Counter()
    elapsed = []
    for op, row in zip(plan.fetch.operations, ops, strict=True):
        expected = dict(
            op_id=op.op_id,
            seq=op.seq,
            arm=op.arm,
            ordinal=op.ordinal,
            file=op.source_file.file,
            kind=op.kind,
            range_start=op.range[0],
            range_end=op.range[1],
            status="COMPLETE",
        )
        check(row == expected, "operation differs from exact frozen range")
        output = outputs[op.op_id]
        r_op = r_ops[op.op_id]
        check(
            r_op
            == dict(
                op_id=op.op_id,
                seq=op.seq,
                arm=op.arm,
                ordinal=op.ordinal,
                kind=op.kind,
                range=list(op.range),
                status="COMPLETE",
                output=output,
            ),
            "operation export differs from SQLite",
        )
        chain = [a for a in attempts if a["op_id"] == op.op_id]
        check(
            len(chain) == 2 and [a["outcome"] for a in chain] == ["REDIRECT", "SUCCESS"],
            "attempt chain",
        )
        check(
            [a["hop"] for a in chain] == [0, 1] and all(a["try_number"] == 1 for a in chain),
            "unexpected retry/hop",
        )
        check([a["http_status"] for a in chain] == [302, 206], "HTTP status")
        check(
            [a["url_host"] for a in chain] == ["huggingface.co", "us.aws.cdn.hf.co"], "host chain"
        )
        check(
            chain[0]["url_path"]
            == urlsplit(plan.fetch.source.canonical_url(op.source_file.file)).path,
            "origin path",
        )
        for a in chain:
            check(
                a["arm"] == op.arm and (a["range_start"], a["range_end"]) == op.range,
                "attempt range",
            )
            check(
                a["url_scheme"] == "https"
                and a["error"] == ("" if a["outcome"] == "REDIRECT" else None),
                "attempt scheme/error",
            )
            check(0 <= a["response_bytes"] <= 4194304, "response body cap")
            check(a["temp_path"] == f"tmp/{op.op_id}.a{a['attempt_id']}.part", "temporary name")
            duration = (
                datetime.fromisoformat(a["completed_utc"])
                - datetime.fromisoformat(a["started_utc"])
            ).total_seconds()
            check(0 <= duration <= 120, "recorded physical duration")
            elapsed.append(duration)
            hosts[a["url_host"]] += 1
            retained = output["retained_file"] if a["outcome"] == "SUCCESS" else a["temp_path"]
            check(
                files[retained] == {"bytes": a["response_bytes"], "sha256": a["response_sha256"]},
                "attempt body hash/count",
            )
        check(
            output["attempt_id"] == chain[1]["attempt_id"]
            and output["retained_file"] == f"payload/{op.op_id}.bin",
            "success output binding",
        )
        check(
            output["bytes"] == op.range[1] - op.range[0] + 1
            and files[output["retained_file"]] == {k: output[k] for k in ["bytes", "sha256"]},
            "payload binding",
        )
    totals = {}
    for arm, n in [("M", 8), ("T", 47)]:
        aa = [a for a in attempts if a["arm"] == arm]
        oo = [outputs[o.op_id] for o in plan.fetch.operations if o.arm == arm]
        totals[arm] = dict(
            logical_operations=n,
            logical_complete=n,
            physical_attempts=len(aa),
            attempts_by_outcome=dict(Counter(a["outcome"] for a in aa)),
            response_body_bytes=sum(a["response_bytes"] for a in aa),
            retained_payload_bytes=sum(o["bytes"] for o in oo),
        )
        check(
            len(aa) <= pd.ATTEMPTS_PER_ARM_MAX[arm]
            and totals[arm]["response_body_bytes"] <= pd.BODY_BYTES_PER_ARM_MAX[arm],
            "arm limits",
        )
        check(
            receipt["arms"][arm]["status"] == "COMPLETE" and receipt["arms"][arm]["reason"] is None,
            "arm status",
        )
    totals["all"] = {
        k: totals["M"][k] + totals["T"][k] for k in totals["M"] if k != "attempts_by_outcome"
    }
    check(totals == receipt["totals"], "independent totals differ")
    decoded = {r["name"]: r for r in tables["decoded_outputs"]}
    check(
        set(decoded) == set(phase_d.ARM_OUTPUTS["M"] + phase_d.ARM_OUTPUTS["T"]),
        "decoded output set",
    )
    check(
        {n: {k: r[k] for k in ["arm", "bytes", "sha256"]} for n, r in decoded.items()}
        == receipt["outputs"],
        "decoded/receipt binding",
    )
    for name, row in decoded.items():
        check(files[name] == {k: row[k] for k in ["bytes", "sha256"]}, "decoded bytes/hash")

    mlines = lines(D / phase_d.M_OUTPUT, 4096)
    expected_rows = [(f, row) for f in plan.m_files for row in range(*f.window)]
    check(len(mlines) == len(expected_rows) == 4096, "M row count")
    for (_, record), (f, index) in zip(mlines, expected_rows, strict=True):
        check(
            set(record) == {"_xlm_acquisition", "eai_taxonomy", "quality_signals"}, "M projection"
        )
        check(
            record["_xlm_acquisition"]
            == dict(
                repository=plan.fetch.source.repository,
                revision=plan.fetch.source.revision,
                source_file=f.file,
                row=index,
                row_group=f.row_group,
                row_in_group=index - f.row_group_first_row,
                m_ordinal=f.ordinal,
            ),
            "M exact frozen row identity",
        )
    check(
        mm["records"] == decoded[phase_d.M_OUTPUT]["records"] == 4096
        and mm["projection"] == ["eai_taxonomy", "quality_signals"],
        "M manifest count/projection",
    )
    check(
        mm["status"] == tm["status"] == "COMPLETE"
        and mm["locator_field"] == "_xlm_acquisition"
        and mm["selector_decision"] == "NONE (acquisition only)",
        "M/T manifest status and purpose",
    )
    check(
        receipt["arms"]["M"]
        == dict(
            status="COMPLETE",
            reason=None,
            logical_operations=8,
            logical_complete=8,
            summary={"files": 8, "records": 4096},
        ),
        "M arm summary",
    )
    check(mm["output"] == {phase_d.M_OUTPUT: files[phase_d.M_OUTPUT]}, "M output binding")
    for f, entry in zip(plan.m_files, mm["files"], strict=True):
        subset = b"".join(
            raw
            for (raw, _), (file, _) in zip(mlines, expected_rows, strict=True)
            if file.ordinal == f.ordinal
        )
        check(
            entry["ordinal"] == f.ordinal
            and entry["file"] == f.file
            and entry["window"] == list(f.window),
            "M manifest window",
        )
        check(
            entry["records"] == 512 and entry["records_sha256"] == sha(subset),
            "M per-file records/hash",
        )
        check(
            entry["row_group"] == f.row_group
            and entry["row_group_first_row"] == f.row_group_first_row
            and entry["row_group_rows"] == f.row_group_rows,
            "M group",
        )
        check(entry["phase_p_footer"] == f.footer_payload, "M footer reference")
        check(
            entry["operation"]["op_id"] == plan.ops_of("M", f.ordinal)[0].op_id,
            "M operation reference",
        )
        op = plan.ops_of("M", f.ordinal)[0]
        output = outputs[op.op_id]
        check(
            entry["operation"]
            == dict(
                op_id=op.op_id,
                range=list(op.range),
                retained_file=output["retained_file"],
                bytes=output["bytes"],
                sha256=output["sha256"],
            ),
            "M payload reference",
        )
        check(
            entry["projected_chunks"] == len(f.chunks) == 81
            and entry["decoded_row_group_rows"] == f.row_group_rows,
            "M decoded shape",
        )
        check(
            set(entry)
            == {
                "decoded_row_group_rows",
                "decoder_reads",
                "file",
                "operation",
                "ordinal",
                "phase_p_footer",
                "projected_chunks",
                "records",
                "records_sha256",
                "row_group",
                "row_group_first_row",
                "row_group_rows",
                "window",
            },
            "M per-file fields",
        )

    tlines = lines(D / phase_d.T_DOCUMENTS, 118)
    documents = [r for _, r in tlines]
    wanted = [(f, loc) for f in plan.t_files for loc in f.locators]
    check(len(documents) == len(wanted) == 118, "T locator count")
    statuses = Counter(
        {
            s: 0
            for s in (
                "full_text_available",
                "unreviewable_full_document_due_to_size",
                "unreviewable_missing_or_invalid_text",
            )
        }
    )
    text_bytes = 0
    unique_text: dict[str, int] = {}
    oversized = []
    max_text = 0
    compact_entries = []
    for i, (doc, (f, loc), prov) in enumerate(
        zip(documents, wanted, provenance["locators"], strict=True)
    ):
        check(
            set(doc)
            == {
                "locator",
                "t_ordinal",
                "row_group",
                "row_in_group",
                "status",
                "utf8_bytes",
                "sha256",
                "text",
            },
            "T field allowlist",
        )
        check(
            doc["locator"] == list(loc.identity)
            and (doc["t_ordinal"], doc["row_group"], doc["row_in_group"])
            == (f.ordinal, f.row_group, loc.row_in_group),
            "T exact selected identity",
        )
        check(doc["status"] in statuses, "T terminal status")
        statuses[doc["status"]] += 1
        if doc["status"] == "full_text_available":
            check(isinstance(doc["text"], str) and bool(doc["text"]), "reviewable text type")
            raw = doc["text"].encode("utf-8", errors="strict")
            check(
                len(raw) == doc["utf8_bytes"] <= 65536 and sha(raw) == doc["sha256"],
                "full text length/hash",
            )
            text_bytes += len(raw)
            max_text = max(max_text, len(raw))
            unique_text[sha(raw)] = len(raw)
        else:
            check(
                doc["text"] is None and doc["sha256"] is None,
                "nonreviewable retains text or text hash",
            )
            if doc["status"] == "unreviewable_full_document_due_to_size":
                check(doc["utf8_bytes"] > 65536, "oversized document length")
                oversized.append(
                    {
                        "source_line": i + 1,
                        "locator_sha256": sha(canonical(doc["locator"])),
                        "utf8_bytes": doc["utf8_bytes"],
                        "text_retained": False,
                    }
                )
        expected_prov = dict(
            locator=list(loc.identity),
            t_ordinal=f.ordinal,
            file=f.file,
            row_group=f.row_group,
            row_group_first_row=f.row_group_first_row,
            row_in_group=loc.row_in_group,
            chunk_span_half_open=list(f.span),
            operations=[o.op_id for o in plan.ops_of("T", f.ordinal)],
            status=doc["status"],
            utf8_bytes=doc["utf8_bytes"],
            sha256=doc["sha256"],
        )
        check(prov == expected_prov, "sealed provenance mismatch")
        compact_entries.append(
            {
                "source_line": i + 1,
                "locator_sha256": sha(canonical(doc["locator"])),
                "status": doc["status"],
                "utf8_bytes": doc["utf8_bytes"],
                "text_sha256": doc["sha256"],
                "record_sha256": sha(tlines[i][0]),
            }
        )
    check(
        statuses
        == {
            "full_text_available": 117,
            "unreviewable_full_document_due_to_size": 1,
            "unreviewable_missing_or_invalid_text": 0,
        },
        "reported status distribution differs",
    )
    check(
        text_bytes == tm["retained_text_bytes"] <= 8388608
        and sum(unique_text.values()) == tm["retained_unique_text_bytes"],
        "retained text budget",
    )
    check(
        tm["status_counts"] == dict(statuses) and tm["selected_locators"] == 118,
        "T manifest totals",
    )
    check(
        receipt["arms"]["T"]
        == dict(
            status="COMPLETE",
            reason=None,
            logical_operations=47,
            logical_complete=47,
            summary={"selected_locators": 118, "status_counts": dict(statuses)},
        ),
        "T arm summary",
    )
    check(
        tm["limits"] == {"document_utf8_bytes_max": 65536, "retained_text_bytes_max": 8388608},
        "T limits",
    )
    check(
        set(tm["sealed_outputs"]) == {phase_d.T_DOCUMENTS, phase_d.T_PROVENANCE},
        "T sealed output set",
    )
    check(
        decoded[phase_d.T_DOCUMENTS]["records"] == decoded[phase_d.T_PROVENANCE]["records"] == 118,
        "T decoded row counts",
    )
    check(
        decoded[phase_d.M_MANIFEST]["records"] == decoded[phase_d.T_MANIFEST]["records"] == 1,
        "manifest decoded counts",
    )
    for name, entry in tm["sealed_outputs"].items():
        check(entry == files[name], "sealed output hash")
    for f, entry in zip(plan.t_files, tm["files"], strict=True):
        check(
            entry["file"] == f.file
            and entry["ordinal"] == f.ordinal
            and entry["selected_locators"] == len(f.locators),
            "T per-file identity",
        )
        check(
            entry["chunk_span_half_open"] == list(f.span) and entry["row_group"] == f.row_group,
            "T per-file span",
        )
        check(
            entry["status_counts"]
            == dict(
                Counter(
                    {
                        **{s: 0 for s in statuses},
                        **Counter(d["status"] for d in documents if d["t_ordinal"] == f.ordinal),
                    }
                )
            ),
            "T per-file statuses",
        )
        check(
            entry["unselected_rows_decoded_not_retained"]
            == entry["decoded_rows"] - len(f.locators),
            "T unselected count",
        )
        check(
            [r["op_id"] for r in entry["operations"]]
            == [o.op_id for o in plan.ops_of("T", f.ordinal)],
            "T operation references",
        )
        check(
            entry["operations"]
            == [
                dict(
                    op_id=o.op_id,
                    range=list(o.range),
                    retained_file=outputs[o.op_id]["retained_file"],
                    bytes=outputs[o.op_id]["bytes"],
                    sha256=outputs[o.op_id]["sha256"],
                )
                for o in plan.ops_of("T", f.ordinal)
            ],
            "T payload references",
        )
        check(
            (
                entry["phase_p_footer"],
                entry["phase_p_trailer"],
                entry["row_group_rows"],
                entry["dictionary_page_offset"],
                entry["data_page_offset"],
            )
            == (
                f.footer_payload,
                f.trailer_payload,
                f.row_group_rows,
                f.dictionary_page_offset,
                f.data_page_offset,
            ),
            "T file metadata",
        )
        check(
            set(entry)
            == {
                "chunk_span_half_open",
                "data_page_offset",
                "decoded_rows",
                "decoder_reads",
                "dictionary_page_offset",
                "file",
                "operations",
                "ordinal",
                "phase_p_footer",
                "phase_p_trailer",
                "row_group",
                "row_group_rows",
                "selected_locators",
                "status_counts",
                "unselected_rows_decoded_not_retained",
            },
            "T per-file fields",
        )

    selection_path = Path("F:/Project/xlm-evidence-v2/essential-web/text_selection_manifest.json")
    selection = sealed(selection_path)
    check(
        selection["digest"] == EXPECTED["selection_digest"]
        and binding(selection_path)["sha256"]
        == "8424f9668fef6952a558ead0dae27a705545a7f4a4d302713707d3ec6bb1af27",
        "scientific selection bytes",
    )
    identities = [
        tuple(loc)
        for cell in selection["cells"]
        for child in cell.get("crawls", [cell])
        for loc in child["identities"]
    ]
    check(
        len(identities) == len(set(identities)) == 118
        and set(identities) == {loc.identity for _, loc in wanted},
        "selection/census membership",
    )
    policy_path = REPO / "recipes/selectors/essential_web_selector_sweep_v1.yaml"
    policy = yaml.safe_load(policy_path.read_bytes())
    check(sha(canonical(policy)) == EXPECTED["policy_digest"], "policy canonical digest")
    check(
        not {f.file for f in plan.m_files} & {f.file for f in plan.t_files},
        "M/development file overlap",
    )
    check(
        receipt["human_review"] == receipt["scientific_scoring"] == "NOT PERFORMED",
        "unexpected review/scoring",
    )
    root_bytes = sum(entry["bytes"] for entry in files.values())
    check(root_bytes <= 1073741824, "whole-root cap")
    for name, root in [("parent", P), ("phase-d", D)]:
        after = inventory(root)
        check(after == before[name], "root changed during review")
        write(f"{name}-after.json", after)
    result = dict(
        status="PASS",
        phase_d_status="COMPLETE",
        run_status="COMPLETE",
        stop_reason=None,
        head=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip(),
        identities=EXPECTED,
        source_revision=plan.fetch.source.revision,
        totals=totals,
        request_hosts=dict(hosts),
        redirects_retained_bytes=totals["all"]["response_body_bytes"]
        - totals["all"]["retained_payload_bytes"],
        M={
            "records": len(mlines),
            "per_file": [512] * 8,
            "output": files[phase_d.M_OUTPUT],
            "exact_rows_and_projection": True,
        },
        T={
            "locators": len(documents),
            "statuses": dict(statuses),
            "retained_text_bytes": text_bytes,
            "unique_text_bytes": sum(unique_text.values()),
            "unique_full_texts": len(unique_text),
            "maximum_retained_document_bytes": max_text,
            "oversized": oversized,
            "output": files[phase_d.T_DOCUMENTS],
        },
        parent_artifacts_verified=len(parent_bindings),
        root_bytes=root_bytes,
        root_cap=1073741824,
        root_files=len(files),
        roots_unchanged=True,
        manifest_artifacts=len(manifest["artifacts"]),
        hashes_verified=True,
        sqlite_integrity="ok",
        sqlite_open="mode=ro&immutable=1; query_only=ON",
        unexpected_files_or_logs=[],
        network_requests=0,
        full_redecode=False,
        live_duration_from_timestamps_seconds=(
            datetime.fromisoformat(run["updated_utc"]) - datetime.fromisoformat(run["created_utc"])
        ).total_seconds(),
        maximum_recorded_attempt_seconds=max(elapsed),
        review_wall_seconds=round(time.monotonic() - START, 3),
        review_peak_working_set_bytes=psutil.Process().memory_info().peak_wset,
        environment={
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "sqlite": sqlite3.sqlite_version,
        },
        scientific_membership_unchanged=True,
        human_labels_assigned=0,
        final_selector_decision="NOT MADE",
        bound_exports={
            n: files[n] for n in sorted(files) if not n.startswith(("payload/", "tmp/"))
        },
    )
    write("review-result.json", result)
    write(
        "t-entry-bindings.json",
        {
            "access": "CUSTODIAN PREPARATION ONLY; not a blinded reviewer package; no text",
            "source": str(D / phase_d.T_DOCUMENTS),
            "source_binding": files[phase_d.T_DOCUMENTS],
            "entries": compact_entries,
        },
    )
    write("parent-bindings.json", parent_bindings)
    print(json.dumps({k: v for k, v in result.items() if k not in ("bound_exports",)}, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # Never dump input rows, document text, URL queries, or exception locals.
        print(
            "REVIEW FAILED:",
            type(exc).__name__,
            str(exc)
            if isinstance(exc, ReviewError)
            else "see failing source location without corpus contents",
            file=sys.stderr,
        )
        raise SystemExit(1) from None
