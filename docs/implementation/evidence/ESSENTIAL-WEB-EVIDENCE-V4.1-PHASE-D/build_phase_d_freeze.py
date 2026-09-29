"""Reproduce the Essential-Web evidence-v4.1 Phase-D acquisition freeze (offline).

This is reproduction tooling for the Phase-D protocol/freeze commit, not the
acquirer. No network access and no corpus text. It reads, read-only:

- the committed reviewed dry plan (``phase_d_dry_plan.json``, digest
  ``ee82125d...0356``) and its blob at the result-review commit;
- the committed v4.1 Phase-P plan, freeze and v4.0 scientific adoption;
- the COMPLETE v4.1 Phase-P root (JSON exports, SQLite file bytes, footer and
  trailer payloads) — files are only ``read_bytes()``/``stat()``; SQLite is
  hashed as bytes, never opened; footers are parsed metadata-only (no
  statistics, no key/value metadata, no pages);
- the frozen v2.0 text-selection manifest (locator identities only).

It derives ``phase_d_plan.json`` from the dry plan WITHOUT inventing ranges:
every range is copied from the dry plan and independently re-derived from the
Phase-P layouts/footers and the v4.1 plan; any difference is a failure.

Usage:  uv run --offline --locked --no-sync --extra cpu --extra eval python <this file> plan|freeze
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO / "src"))

from xlm.data.evidence_v4 import layout  # noqa: E402  (metadata-only footer parser)

OUT_REL = "docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V4.1-PHASE-D"
OUT = REPO / OUT_REL
PROTOCOL_REL = "docs/implementation/reports/ESSENTIAL-WEB-EVIDENCE-V4.1-PHASE-D-PROTOCOL.md"

PROTOCOL_VERSION = "essential-web-evidence-v4.1-phase-d"
FROZEN_DATE = "2026-09-29"
EXECUTION_ROOT = "G:\\Project\\xlm-evidence-v4.1\\essential-web-phase-d"

PHASE_P_ROOT_TEXT = "G:\\Project\\xlm-evidence-v4.1\\essential-web"
PHASE_P_ROOT = Path(PHASE_P_ROOT_TEXT)
V40_ROOT_TEXT = "G:\\Project\\xlm-evidence-v4\\essential-web"
V3_ROOT_TEXT = "G:\\Project\\xlm-evidence-v3\\essential-web"

REVIEW_COMMIT = "ed8efcf3c85c265828a88579a264f08a2048640f"
REVIEW_DIR = "docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V4.1-PHASE-P-RESULT-REVIEW"
DRY_REL = f"{REVIEW_DIR}/phase_d_dry_plan.json"
DRY_DIGEST = "ee82125d1b0d2b6d0a6c420144910db954659d5b757283d39123ddc3d0080356"
REVIEW_RESULT_REL = f"{REVIEW_DIR}/review-result.json"
REVIEW_REPORT_REL = (
    "docs/implementation/reports/ESSENTIAL-WEB-EVIDENCE-V4.1-PHASE-P-RESULT-REVIEW.md"
)
REVIEW_VERDICT = "PHASE-P RESULT REVIEW PASSED - READY FOR PHASE-D AUTHORIZATION REVIEW"

V41_DIR = "docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V4.1"
V41_PLAN_REL = f"{V41_DIR}/phase_p_plan.json"
V41_FREEZE_REL = f"{V41_DIR}/freeze.json"
V41_PROTOCOL_REL = "docs/implementation/reports/ESSENTIAL-WEB-EVIDENCE-V4.1-PROTOCOL.md"
V41_PROTOCOL_VERSION = "essential-web-evidence-v4.1"
V41_PROTOCOL_SHA256 = "3d667a263b92696a2d7a266f896e460b10a81ae38097f788c8b885ce768079cf"
V41_FREEZE_DIGEST = "285015604d30e1699b5f63fa4f25ec9c747c778be2f372bb87119adc0e455f80"
V41_PLAN_DIGEST = "762cef78051011d518c4918a41aa87cf81fbb3224bf438af8cc1c499cfca7711"
MEMBERSHIP_DIGEST = "304bd0761125b48720a4de9c82bb6b3bf47100b8a15fc0953d548a8a1dab38bd"
V40_ADOPTION_REL = "docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V4.0/scientific_adoption.json"
V40_ADOPTION_DIGEST = "158c3fc9fed030d0290aa50eded70f7d94b162ab6186ae38aca2ce6b1ebdffd3"
V40_IDENTITY_DIGEST = "080caebb962c86562b530ddfa6a130118a49a6d207b044e83ce6a90a6d9691c6"

SCIENTIFIC_NAMESPACE = "essential-web-evidence-v2.0"
SELECTION_DIGEST = "975ba3dee4af0598e665ea05c69bbe49336c3e3a35fca777a863073190b78474"
SELECTION_SHA256 = "8424f9668fef6952a558ead0dae27a705545a7f4a4d302713707d3ec6bb1af27"
SELECTION_PATH = Path("F:/Project/xlm-evidence-v2/essential-web/text_selection_manifest.json")
POLICY_DIGEST = "f4357f61f434d5105d823266153dac6372e122f12767187631e8796b4899dd07"
REPOSITORY = "EssentialAI/essential-web-v1.0"
REVISION = "ce4eccc7e9604667b6d7f32cb6274b8b41f3113d"
PROJECTION = ["eai_taxonomy", "quality_signals"]

PHASE_P_RECEIPT_DIGEST = "ebd5704be8a871034ef8048c0641e9d735d017f458003b4efc50ab124c8bd965"
PHASE_P_MANIFEST_DIGEST = "1bb6bad6e3690758c005e260c272eac3c0adcb6d040fde960a502d0ced90d7fe"
PHASE_P_M_LAYOUT_DIGEST = "2c9f2f79448e1bb1d59b57f1ddec276dcd05bc79dddaea4014c9942a340d340a"
PHASE_P_T_LAYOUT_DIGEST = "364bda46c766e8f9fdebd5732408743420cbe5c6f0b00fbd6a10d09a8bd53274"

PIECE = 4194304
EXPECTED = {"M_ops": 8, "M_bytes": 11692530, "T_ops": 47, "T_bytes": 179963169, "locators": 118}

M_KIND = "M_PROJECTED_RANGE"
T_KIND = "T_TEXT_CHUNK_RANGE"

LIMITS: dict[str, Any] = {
    "physical_attempts_per_arm_max": {"M": 64, "T": 320},
    "response_body_bytes_per_arm_max": {"M": 67108864, "T": 536870912},
    "response_body_bytes_per_response_max": 4194304,
    "overflow_detection_bytes": 1,
    "redirect_transitions_per_logical_request_max": 3,
    "additional_attempts_per_operation_max": 2,
    "retry_delays_seconds": [1, 2],
    "retryable_http_statuses": [429, 500, 502, 503, 504],
    "physical_attempt_timeout_seconds": 120,
    "execution_root_bytes_max": 1073741824,
    "free_disk_bytes_min": 2147483648,
    "byte_count_flush_interval_bytes": 1048576,
    "parquet_thrift_limit_bytes": 33554432,
    "t_range_piece_bytes": PIECE,
    "decode_batch_rows": 256,
    "m_metadata_record_bytes_max": 1048576,
    "m_selected_metadata_bytes_max": 67108864,
    "t_document_utf8_bytes_max": 65536,
    "t_retained_text_bytes_max": 8388608,
    "t_selected_documents_bytes_max": 67108864,
}

T_STATUSES = [
    "full_text_available",
    "unreviewable_full_document_due_to_size",
    "unreviewable_missing_or_invalid_text",
    "acquisition_incomplete",
]

COMMAND = (
    "uv run --offline --locked --extra cpu --extra eval python scripts/evidence_v41_phase_d.py "
    "phase-d --confirm-plan-digest {digest}"
)


def cbytes(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def H(value: Any) -> str:
    return hashlib.sha256(cbytes(value)).hexdigest()


def self_digest(body: dict[str, Any]) -> str:
    return H({k: v for k, v in body.items() if k != "digest"})


def seal(body: dict[str, Any]) -> dict[str, Any]:
    body = {k: v for k, v in body.items() if k != "digest"}
    body["digest"] = self_digest(body)
    return body


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def fail(message: str) -> None:
    raise SystemExit(f"build_phase_d_freeze: FAILED: {message}")


def require(condition: bool, message: str) -> None:
    if not condition:
        fail(message)


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = dict(pairs)
    require(len(result) == len(pairs), "duplicate JSON key")
    return result


def load_json(raw: bytes) -> Any:
    return json.loads(raw, object_pairs_hook=_unique)


def load_sealed(path: Path, digest: str | None) -> tuple[bytes, dict[str, Any]]:
    raw = path.read_bytes()
    obj = load_json(raw)
    require(isinstance(obj, dict) and self_digest(obj) == obj.get("digest"), f"{path}: digest")
    if digest is not None:
        require(obj["digest"] == digest, f"{path}: digest {obj['digest']} != {digest}")
    return raw, obj


def git_blob(commit: str, rel: str) -> bytes:
    return subprocess.run(
        ["git", "show", f"{commit}:{rel}"], cwd=REPO, check=True, capture_output=True
    ).stdout


def binding(raw: bytes) -> dict[str, Any]:
    return {"bytes": len(raw), "sha256": sha(raw)}


def write(name: str, body: dict[str, Any]) -> dict[str, Any]:
    raw = cbytes(body)
    (OUT / name).write_bytes(raw)
    return {"path": f"{OUT_REL}/{name}", **binding(raw), "digest": body["digest"]}


def root_inventory(root: Path) -> list[list[Any]]:
    """Stat-only inventory (names, sizes, mtimes) proving the parent is not mutated."""
    rows: list[list[Any]] = []
    for path in sorted(root.rglob("*")):
        st = path.stat()
        rows.append([path.relative_to(root).as_posix(), path.is_dir(), st.st_size, st.st_mtime_ns])
    return rows


# -- parents ---------------------------------------------------------------


def phase_p_parent(dry: dict[str, Any], checks: dict[str, Any]) -> dict[str, Any]:
    require(PHASE_P_ROOT.is_dir(), f"Phase-P root {PHASE_P_ROOT_TEXT} is missing")
    artifacts: dict[str, dict[str, Any]] = {}
    docs: dict[str, dict[str, Any]] = {}
    for name, digest in (
        ("phase_p_receipt.json", PHASE_P_RECEIPT_DIGEST),
        ("artifact_manifest.json", PHASE_P_MANIFEST_DIGEST),
        ("m_phase_p_layout.json", PHASE_P_M_LAYOUT_DIGEST),
        ("t_phase_p_layout.json", PHASE_P_T_LAYOUT_DIGEST),
    ):
        raw, obj = load_sealed(PHASE_P_ROOT / name, digest)
        require(dry["parents"][name] == {**binding(raw), "digest": digest}, f"dry parent {name}")
        artifacts[name] = {**binding(raw), "digest": digest}
        docs[name] = obj
    sqlite_raw = (PHASE_P_ROOT / "state.sqlite").read_bytes()  # bytes only; never opened
    require(dry["state_sqlite"] == binding(sqlite_raw), "state.sqlite differs from the dry plan")
    artifacts["state.sqlite"] = binding(sqlite_raw)
    requests_raw = (PHASE_P_ROOT / "request_receipt.jsonl").read_bytes()
    receipt, manifest = docs["phase_p_receipt.json"], docs["artifact_manifest.json"]
    require(
        receipt["request_receipt"]["sha256"] == sha(requests_raw)
        and manifest["artifacts"]["request_receipt.jsonl"] == binding(requests_raw),
        "request receipt binding",
    )
    artifacts["request_receipt.jsonl"] = binding(requests_raw)
    for doc in docs.values():
        require(
            (doc["plan_digest"], doc["protocol_version"], doc["synthetic"])
            == (V41_PLAN_DIGEST, V41_PROTOCOL_VERSION, False),
            "Phase-P export binds a different plan/version",
        )
        require(
            (doc["protocol_sha256"], doc["freeze_digest"], doc["selection_digest"])
            == (V41_PROTOCOL_SHA256, V41_FREEZE_DIGEST, SELECTION_DIGEST),
            "Phase-P export binds a different protocol/freeze/selection",
        )
    require(
        (receipt["status"], receipt["run_status"], receipt["stop_reason"])
        == ("COMPLETE", "COMPLETE", None),
        "Phase-P receipt is not COMPLETE",
    )
    require(
        manifest["status"] == "COMPLETE" and manifest["receipt_digest"] == receipt["digest"],
        "Phase-P manifest does not bind the COMPLETE receipt",
    )
    for name in ("m_phase_p_layout.json", "t_phase_p_layout.json"):
        require(receipt["layouts"][name]["digest"] == artifacts[name]["digest"], f"{name} ref")
        require(
            manifest["artifacts"][name] == binding((PHASE_P_ROOT / name).read_bytes()),
            f"{name} manifest binding",
        )
    names = [f"payload/M-{i:02d}-footer.bin" for i in range(8)]
    names += [f"payload/T-{i:02d}-{kind}.bin" for i in range(8) for kind in ("trailer", "footer")]
    for rel in names:
        raw = (PHASE_P_ROOT / rel).read_bytes()
        require(manifest["artifacts"][rel] == binding(raw), f"{rel} differs from the manifest")
        artifacts[rel] = binding(raw)
    checks["phase_p_parent"] = {
        "receipt_status": receipt["status"],
        "run_status": receipt["run_status"],
        "operations_complete": sum(1 for o in receipt["operations"] if o["status"] == "COMPLETE"),
        "operations": len(receipt["operations"]),
        "totals_all": receipt["totals"]["all"],
        "artifacts_verified": len(artifacts),
        "exports_bind_v41_plan_protocol_freeze_selection": True,
    }
    return {
        "root": PHASE_P_ROOT_TEXT,
        "protocol_version": V41_PROTOCOL_VERSION,
        "protocol_sha256": V41_PROTOCOL_SHA256,
        "freeze_digest": V41_FREEZE_DIGEST,
        "plan_digest": V41_PLAN_DIGEST,
        "membership_and_ranges_digest": MEMBERSHIP_DIGEST,
        "run_status": "COMPLETE",
        "artifacts": dict(sorted(artifacts.items())),
    }


# -- files and operations ------------------------------------------------------


def split(start: int, end: int) -> list[list[int]]:
    """Half-open [start, end) into 4 MiB pieces from the start plus the remainder."""
    pieces: list[list[int]] = []
    cursor = start
    while cursor < end:
        stop = min(cursor + PIECE, end)
        pieces.append([cursor, stop])
        cursor = stop
    return pieces


def footer_metadata(rel: str, trailer_rel: str | None) -> Any:
    raw = (PHASE_P_ROOT / rel).read_bytes()
    if trailer_rel is None:
        return layout._metadata(raw[:-8], raw[-8:])
    return layout._metadata(raw, (PHASE_P_ROOT / trailer_rel).read_bytes())


def m_file(
    dry_file: dict[str, Any], v41_file: dict[str, Any], m_layout: dict[str, Any]
) -> dict[str, Any]:
    i = dry_file["ordinal"]
    b = v41_file["bindings"]
    lay = m_layout["layout"]
    require(
        (dry_file["file"], dry_file["remote_length"], dry_file["strong_etag"])
        == (v41_file["file"], v41_file["remote_length"], v41_file["strong_etag"])
        == (m_layout["file"], m_layout["remote_length"], m_layout["strong_etag"]),
        f"M-{i:02d}: file identity differs between dry plan, v4.1 plan and Phase-P layout",
    )
    require(
        dry_file["window"] == b["window"] == lay["window"]
        and dry_file["projection"] == b["projection"] == PROJECTION,
        f"M-{i:02d}: window/projection",
    )
    require(b["window"][1] - b["window"][0] == 512, f"M-{i:02d}: window is not 512 rows")
    chunks = [
        {k: c[k] for k in ("path", "start", "end_exclusive", "compressed_bytes")}
        for c in lay["projected_chunks"]
    ]
    require(chunks == dry_file["projected_chunk_ranges"], f"M-{i:02d}: chunks differ")
    require(len(chunks) == b["data_chunk_count"], f"M-{i:02d}: chunk count")
    cursor = chunks[0]["start"]
    for c in chunks:
        require(c["start"] == cursor, f"M-{i:02d}: chunks are not exactly adjacent")
        require(c["end_exclusive"] - c["start"] == c["compressed_bytes"] > 0, f"M-{i:02d}: size")
        cursor = c["end_exclusive"]
    span = [chunks[0]["start"], cursor]
    require(span == lay["projected_span_half_open"], f"M-{i:02d}: projected span")
    require(span[1] - span[0] == b["data_payload_bytes"], f"M-{i:02d}: payload bytes")
    require(
        dry_file["request_ranges"]
        == [{"start": span[0], "end_exclusive": span[1], "bytes": span[1] - span[0]}],
        f"M-{i:02d}: dry request range differs from the chunk union",
    )
    require(span[1] - span[0] <= PIECE, f"M-{i:02d}: range exceeds 4 MiB")
    footer_start = lay["footer_range"][0]
    require(span[1] <= footer_start, f"M-{i:02d}: range reaches the footer")
    rg, first, rows = (
        lay["window_row_group"],
        lay["window_row_group_first_row"],
        lay["window_row_group_rows"],
    )
    require(
        (dry_file["row_group"], dry_file["row_group_first_row"]) == (rg, first),
        f"M-{i:02d}: row group",
    )
    require(first <= b["window"][0] and b["window"][1] <= first + rows, f"M-{i:02d}: window rg")
    footer_rel = f"payload/M-{i:02d}-footer.bin"
    meta = footer_metadata(footer_rel, None)
    group = meta.row_group(rg)
    require(int(group.num_rows) == rows, f"M-{i:02d}: footer row-group rows")
    leaves = []
    for c in range(int(group.num_columns)):
        column = group.column(c)
        if str(column.path_in_schema).split(".")[0] not in PROJECTION:
            continue
        start = int(column.data_page_offset)
        if column.dictionary_page_offset is not None and int(column.dictionary_page_offset) > 0:
            start = min(start, int(column.dictionary_page_offset))
        size = int(column.total_compressed_size)
        leaves.append(
            {
                "path": str(column.path_in_schema),
                "start": start,
                "end_exclusive": start + size,
                "compressed_bytes": size,
            }
        )
    leaves.sort(key=lambda c: (c["start"], c["path"]))
    require(leaves == chunks, f"M-{i:02d}: footer metadata leaves differ from the chunks")
    return {
        "arm": "M",
        "ordinal": i,
        "file": dry_file["file"],
        "remote_length": dry_file["remote_length"],
        "strong_etag": dry_file["strong_etag"],
        "window": b["window"],
        "row_group": rg,
        "row_group_first_row": first,
        "row_group_rows": rows,
        "projection": PROJECTION,
        "projected_chunks": chunks,
        "range_half_open": span,
        "uncompressed_bytes": b["data_uncompressed_bytes"],
        "phase_p_payloads": {"footer": footer_rel},
        "footer_start": footer_start,
    }


def t_file(
    dry_file: dict[str, Any],
    v41_file: dict[str, Any],
    t_layout: dict[str, Any],
    selected: set[tuple[Any, ...]],
) -> dict[str, Any]:
    i = dry_file["ordinal"]
    b = v41_file["bindings"]
    chunk = t_layout["layout"]["selected_text_chunk"]
    require(
        (dry_file["file"], dry_file["remote_length"], dry_file["strong_etag"])
        == (v41_file["file"], v41_file["remote_length"], v41_file["strong_etag"])
        == (t_layout["file"], t_layout["remote_length"], t_layout["strong_etag"]),
        f"T-{i:02d}: file identity",
    )
    span = b["data_span_half_open"]
    require(
        dry_file["chunk_span_half_open"] == span == [chunk["start"], chunk["end_exclusive"]],
        f"T-{i:02d}: span",
    )
    require(chunk["compressed_bytes"] == span[1] - span[0] == b["data_payload_bytes"], "T bytes")
    rg, first, rows = chunk["row_group"], chunk["first_row"], chunk["num_rows"]
    require(
        (dry_file["row_group"], dry_file["row_group_first_row"], dry_file["row_group_rows"])
        == (rg, first, rows),
        f"T-{i:02d}: row group",
    )
    pieces = split(*span)
    require(
        [[r["start"], r["end_exclusive"]] for r in dry_file["request_ranges"]] == pieces
        and all(r["bytes"] == r["end_exclusive"] - r["start"] for r in dry_file["request_ranges"]),
        f"T-{i:02d}: dry request ranges differ from the 4 MiB split of the span",
    )
    require(len(pieces) == b["data_range_count"], f"T-{i:02d}: range count vs v4.1 plan")
    footer_rel, trailer_rel = f"payload/T-{i:02d}-footer.bin", f"payload/T-{i:02d}-trailer.bin"
    meta = footer_metadata(footer_rel, trailer_rel)
    group = meta.row_group(rg)
    text = [
        group.column(c)
        for c in range(int(group.num_columns))
        if str(group.column(c).path_in_schema) == "text"
    ]
    require(len(text) == 1 and int(group.num_rows) == rows, f"T-{i:02d}: text leaf")
    dictionary, data = int(text[0].dictionary_page_offset or 0), int(text[0].data_page_offset)
    require(
        (dictionary, data) == (dry_file["dictionary_page_offset"], dry_file["data_page_offset"])
        and span[0] == dictionary < data < span[1],
        f"T-{i:02d}: chunk is not dictionary-inclusive at the frozen offsets",
    )
    require(int(text[0].total_compressed_size) == span[1] - span[0], f"T-{i:02d}: chunk size")
    footer_start = t_layout["footer_range"][0]
    require(span[1] <= footer_start, f"T-{i:02d}: span reaches the footer")
    locators = []
    for loc in dry_file["locators"]:
        identity = loc["identity"]
        require(
            identity[:3] == [REPOSITORY, REVISION, dry_file["file"]]
            and identity[3] == first + loc["row_in_group"]
            and 0 <= loc["row_in_group"] < rows,
            f"T-{i:02d}: locator identity/row",
        )
        require(tuple(identity) in selected, f"T-{i:02d}: locator not in the selection manifest")
        locators.append({"identity": identity, "row_in_group": loc["row_in_group"]})
    rows_in = [loc["row_in_group"] for loc in locators]
    require(rows_in == sorted(set(rows_in)), f"T-{i:02d}: locators must be unique and ascending")
    return {
        "arm": "T",
        "ordinal": i,
        "file": dry_file["file"],
        "remote_length": dry_file["remote_length"],
        "strong_etag": dry_file["strong_etag"],
        "text_column": "text",
        "row_group": rg,
        "row_group_first_row": first,
        "row_group_rows": rows,
        "chunk_span_half_open": span,
        "dictionary_page_offset": dictionary,
        "data_page_offset": data,
        "compressed_bytes": span[1] - span[0],
        "uncompressed_bytes": chunk["uncompressed_bytes"],
        "phase_p_payloads": {"footer": footer_rel, "trailer": trailer_rel},
        "footer_start": footer_start,
        "locators": locators,
    }


def operations(files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ops: list[dict[str, Any]] = []
    for f in files:
        if f["arm"] == "M":
            spans = [f["range_half_open"]]
            kind = M_KIND
        else:
            spans = split(*f["chunk_span_half_open"])
            kind = T_KIND
        for piece, (start, end) in enumerate(spans):
            ops.append(
                {
                    "op_id": f"{f['arm']}-{f['ordinal']:02d}-d{piece:02d}",
                    "seq": len(ops),
                    "arm": f["arm"],
                    "ordinal": f["ordinal"],
                    "piece": piece,
                    "kind": kind,
                    "range": [start, end - 1],
                }
            )
    return ops


def selection_locators(checks: dict[str, Any]) -> set[tuple[Any, ...]]:
    """Locator identities of the frozen v2.0 selection manifest (no strata exported)."""
    raw = SELECTION_PATH.read_bytes()
    require(sha(raw) == SELECTION_SHA256, "selection manifest SHA-256 drift")
    selection = load_json(raw)
    require(self_digest(selection) == selection["digest"] == SELECTION_DIGEST, "selection digest")
    units = [u for c in selection["cells"] for u in ([c] if "identities" in c else c["crawls"])]
    locators = [tuple(i) for u in units for i in u["identities"]]
    require(len(locators) == len(set(locators)) == EXPECTED["locators"], "selection locators")
    checks["selection_manifest"] = {
        "sha256": SELECTION_SHA256,
        "digest": SELECTION_DIGEST,
        "unique_locators": len(locators),
        "strata_exported": False,
    }
    return set(locators)


# -- main ----------------------------------------------------------------------


def build_plan(checks: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    dry_raw, dry = load_sealed(REPO / DRY_REL, DRY_DIGEST)
    require(git_blob(REVIEW_COMMIT, DRY_REL) == dry_raw, "dry plan differs from the review commit")
    require(dry["status"] == "DRY_NOT_AUTHORIZED", "dry plan status")
    require(
        (dry["phase_p_plan_digest"], dry["selection_digest"], dry["membership_and_ranges_digest"])
        == (V41_PLAN_DIGEST, SELECTION_DIGEST, MEMBERSHIP_DIGEST),
        "dry plan parents",
    )
    require(dry["phase_p_root"] == PHASE_P_ROOT_TEXT, "dry plan Phase-P root")
    require(
        dry["source"]
        == {
            "host": "huggingface.co",
            "repository_type": "datasets",
            "repository": REPOSITORY,
            "revision": REVISION,
        },
        "dry plan source",
    )
    _, v41_plan = load_sealed(REPO / V41_PLAN_REL, V41_PLAN_DIGEST)
    _, v41_freeze = load_sealed(REPO / V41_FREEZE_REL, V41_FREEZE_DIGEST)
    require(
        sha((REPO / V41_PROTOCOL_REL).read_bytes()) == V41_PROTOCOL_SHA256, "v4.1 protocol drift"
    )
    require(dry["network"] == v41_plan["network"] == v41_freeze["network"], "network differs")
    review_raw = (REPO / REVIEW_RESULT_REL).read_bytes()
    report_raw = (REPO / REVIEW_REPORT_REL).read_bytes()
    require(git_blob(REVIEW_COMMIT, REVIEW_RESULT_REL) == review_raw, "review result drift")
    require(git_blob(REVIEW_COMMIT, REVIEW_REPORT_REL) == report_raw, "review report drift")
    review = load_json(review_raw)
    require(review["verdict"] == REVIEW_VERDICT, "review verdict")
    require(review["phase_d_dry_plan_digest"] == DRY_DIGEST, "review binds another dry plan")
    before = root_inventory(PHASE_P_ROOT)
    phase_p = phase_p_parent(dry, checks)
    selected = selection_locators(checks)
    m_layout = load_json((PHASE_P_ROOT / "m_phase_p_layout.json").read_bytes())
    t_layout = load_json((PHASE_P_ROOT / "t_phase_p_layout.json").read_bytes())
    v41_files = {(f["arm"], f["ordinal"]): f for f in v41_plan["files"]}
    files = [
        m_file(d, v41_files[("M", d["ordinal"])], m_layout["files"][d["ordinal"]])
        for d in dry["files"]["M"]
    ]
    files += [
        t_file(d, v41_files[("T", d["ordinal"])], t_layout["files"][d["ordinal"]], selected)
        for d in dry["files"]["T"]
    ]
    require([f["ordinal"] for f in files] == [*range(8), *range(8)], "file ordinals")
    ops = operations(files)
    m_ops = [o for o in ops if o["arm"] == "M"]
    t_ops = [o for o in ops if o["arm"] == "T"]
    totals = {
        "M_ops": len(m_ops),
        "M_bytes": sum(o["range"][1] - o["range"][0] + 1 for o in m_ops),
        "T_ops": len(t_ops),
        "T_bytes": sum(o["range"][1] - o["range"][0] + 1 for o in t_ops),
        "locators": sum(len(f["locators"]) for f in files if f["arm"] == "T"),
    }
    require(totals == EXPECTED, f"totals {totals} != {EXPECTED}")
    require(
        (totals["M_bytes"], totals["T_bytes"])
        == (dry["totals"]["M"]["exact_success_payload_bytes"], dry["totals"]["T"]["exact_success_payload_bytes"])
        and (totals["M_ops"], totals["T_ops"])
        == (dry["totals"]["M"]["logical_operations"], dry["totals"]["T"]["logical_operations"]),
        "totals differ from the dry plan",
    )
    all_locators = {tuple(loc["identity"]) for f in files if f["arm"] == "T" for loc in f["locators"]}
    require(all_locators == selected, "dry-plan locators differ from the selection manifest")
    require(root_inventory(PHASE_P_ROOT) == before, "Phase-P root changed during the build")
    checks["phase_p_root_unchanged"] = {"entries": len(before), "before_equals_after": True}
    checks["dry_plan"] = {
        "digest": DRY_DIGEST,
        "sha256": sha(dry_raw),
        "equals_blob_at_review_commit": REVIEW_COMMIT,
        "status": dry["status"],
    }
    checks["ranges"] = {
        "M_ranges_equal_dry_plan_and_chunk_union": True,
        "M_chunks_equal_phase_p_layout_and_footer_metadata": True,
        "T_ranges_equal_dry_plan_and_4MiB_split": True,
        "T_range_counts_equal_v41_plan_data_range_count": True,
        "T_dictionary_inclusive_from_footer_metadata": True,
        "locators_equal_selection_manifest": True,
        **totals,
    }
    parents = {
        "dry_plan": {"path": DRY_REL, **binding(dry_raw), "digest": DRY_DIGEST},
        "result_review": {
            "commit": REVIEW_COMMIT,
            "report": {"path": REVIEW_REPORT_REL, **binding(report_raw)},
            "review_result": {"path": REVIEW_RESULT_REL, **binding(review_raw)},
            "verdict": REVIEW_VERDICT,
        },
        "phase_p": phase_p,
    }
    plan = seal(
        {
            "kind": "essential_web_v41_phase_d_plan",
            "protocol_version": PROTOCOL_VERSION,
            "plan_schema_version": 1,
            "synthetic": False,
            "phase": "D",
            "scientific_namespace": SCIENTIFIC_NAMESPACE,
            "selection_digest": SELECTION_DIGEST,
            "policy_digest": POLICY_DIGEST,
            "source": dry["source"],
            "execution_root": EXECUTION_ROOT,
            "parents": parents,
            "arms": ["M", "T"],
            "files": files,
            "operations": ops,
            "limits": LIMITS,
            "network": v41_plan["network"],
        }
    )
    return plan, dry, totals


def build_parent_binding(plan: dict[str, Any], checks: dict[str, Any]) -> dict[str, Any]:
    return seal(
        {
            "kind": "essential_web_v41_phase_d_phase_p_parent_binding",
            "protocol_version": PROTOCOL_VERSION,
            "phase_d_plan_digest": plan["digest"],
            "parents": plan["parents"],
            "verified": checks["phase_p_parent"],
            "rule": (
                "Phase D refuses before any request when any bound Phase-P artifact, footer or "
                "trailer payload differs in bytes or SHA-256, or the plan/layouts disagree"
            ),
            "phase_p_root_access": "read-only; never created, written, renamed or deleted",
        }
    )


def build_adoption(plan: dict[str, Any]) -> dict[str, Any]:
    adoption_raw, adoption = load_sealed(REPO / V40_ADOPTION_REL, V40_ADOPTION_DIGEST)
    require(adoption["scientific_identity_digest"] == V40_IDENTITY_DIGEST, "identity digest")
    t_files = [f for f in plan["files"] if f["arm"] == "T"]
    m_files = [f for f in plan["files"] if f["arm"] == "M"]
    require(
        [[w["file"], w["window"]] for w in adoption["scientific_identity"]["M_windows"]]
        == [[f["file"], f["window"]] for f in m_files],
        "M windows differ from the adopted identity",
    )
    require(
        [d["file"] for d in adoption["T"]["development_files"]] == [f["file"] for f in t_files],
        "T files differ from the adopted identity",
    )
    return seal(
        {
            "kind": "essential_web_v41_phase_d_scientific_adoption",
            "protocol_version": PROTOCOL_VERSION,
            "adoption": "exact by reference; Phase D acquires the frozen membership, never selects",
            "adopted": {
                "v4_0_scientific_adoption": {
                    "path": V40_ADOPTION_REL,
                    **binding(adoption_raw),
                    "digest": V40_ADOPTION_DIGEST,
                },
                "scientific_identity_digest": V40_IDENTITY_DIGEST,
                "v4_1_phase_p_plan_digest": V41_PLAN_DIGEST,
                "membership_and_ranges_digest": MEMBERSHIP_DIGEST,
            },
            "scientific_namespace": SCIENTIFIC_NAMESPACE,
            "selection_digest": SELECTION_DIGEST,
            "selection_manifest_sha256": SELECTION_SHA256,
            "source": {"repository": REPOSITORY, "revision": REVISION},
            "policy_digest": POLICY_DIGEST,
            "M": {
                "files": len(m_files),
                "rows_per_file": 512,
                "rows_total": 4096,
                "projection": PROJECTION,
                "windows": [{"file": f["file"], "window": f["window"]} for f in m_files],
                "selector_decision_during_acquisition": False,
            },
            "T": {
                "files": len(t_files),
                "locators": sum(len(f["locators"]) for f in t_files),
                "locators_per_file": [len(f["locators"]) for f in t_files],
                "retention": {
                    "document_utf8_bytes_max": 65536,
                    "retained_unique_text_bytes_max": 8388608,
                    "statuses": T_STATUSES,
                    "oversized_document": (
                        "status unreviewable_full_document_due_to_size; no text or excerpt "
                        "retained; full UTF-8 length recorded"
                    ),
                    "missing_or_invalid": (
                        "null, empty, non-string or invalid UTF-8 text is "
                        "unreviewable_missing_or_invalid_text; never coerced"
                    ),
                    "truncation": "forbidden",
                    "normalization": "none",
                    "source": "ESSENTIAL-WEB-EVIDENCE-V2-PROTOCOL.md section 7 (frozen v2.0)",
                },
                "unselected_rows": (
                    "may be decoded programmatically inside a required chunk; never retained, "
                    "exported, logged, rendered or used for any decision"
                ),
            },
            "blinding": {
                "custodian_secret_generated": False,
                "review_ids_generated": False,
                "reviewer_orders_generated": False,
                "selection_category_in_any_phase_d_output": False,
                "phase_d_outputs_are_reviewer_packages": False,
                "sealed_outputs": "locator-bearing text and provenance are custodian-only (sealed/)",
                "rules": "unchanged frozen v2.0 section 9; M-report-before-T-unblinding unchanged",
            },
            "prohibitions": [
                "no reselection",
                "no replacement file, window, locator or source",
                "no new or broadened range",
                "no membership change based on decoded content",
                "no truncation or normalization of retained text",
                "no human review or scientific scoring in Phase D",
            ],
        }
    )


def environment() -> dict[str, Any]:
    return {
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "os": os.name,
    }


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode not in ("plan", "freeze"):
        fail("usage: build_phase_d_freeze.py plan|freeze")
    OUT.mkdir(parents=True, exist_ok=True)
    checks: dict[str, Any] = {}
    plan, _, totals = build_plan(checks)
    plan_ref = write("phase_d_plan.json", plan)
    parent = build_parent_binding(plan, checks)
    parent_ref = write("phase_p_parent_binding.json", parent)
    adoption = build_adoption(plan)
    adoption_ref = write("scientific_adoption.json", adoption)
    print(f"phase-d plan digest           {plan['digest']}")
    print(f"phase-p parent binding digest {parent['digest']}")
    print(f"scientific adoption digest    {adoption['digest']}")
    if mode == "plan":
        return 0
    protocol_raw = (REPO / PROTOCOL_REL).read_bytes()
    protocol_text = protocol_raw.decode("utf-8")
    for value in (plan["digest"], parent["digest"], adoption["digest"], DRY_DIGEST):
        require(value in protocol_text, f"protocol does not state {value}")
    command = COMMAND.format(digest=plan["digest"])
    require(command in protocol_text, "protocol does not state the exact future command")
    for forbidden in (PHASE_P_ROOT_TEXT, V40_ROOT_TEXT, V3_ROOT_TEXT):
        require(EXECUTION_ROOT != forbidden, "Phase-D root reuses an earlier root")
    require(not Path(EXECUTION_ROOT).exists(), "the Phase-D execution root already exists")
    freeze = seal(
        {
            "kind": "essential_web_v41_phase_d_freeze",
            "protocol_version": PROTOCOL_VERSION,
            "frozen_date": FROZEN_DATE,
            "status": "FROZEN_READY_TO_IMPLEMENT",
            "protocol": {"path": PROTOCOL_REL, **binding(protocol_raw)},
            "phase_d_plan": plan_ref,
            "phase_p_parent_binding": parent_ref,
            "scientific_adoption": adoption_ref,
            "dry_plan": plan["parents"]["dry_plan"],
            "result_review_commit": REVIEW_COMMIT,
            "execution_root": EXECUTION_ROOT,
            "phase_p_root": PHASE_P_ROOT_TEXT,
            "operational_limits": LIMITS,
            "network": plan["network"],
            "operation_counts": {"M": totals["M_ops"], "T": totals["T_ops"]},
            "exact_success_payload_bytes": {
                "M": totals["M_bytes"],
                "T": totals["T_bytes"],
                "all": totals["M_bytes"] + totals["T_bytes"],
            },
            "selected": {"M_rows": 4096, "T_locators": totals["locators"]},
            "future_command": command,
            "current_network_requests": 0,
            "current_corpus_text_bytes_read": 0,
            "phase_d_execution": "NOT AUTHORIZED by this freeze; requires narrow review",
        }
    )
    freeze_ref = write("freeze.json", freeze)
    verification = seal(
        {
            "kind": "essential_web_v41_phase_d_freeze_verification",
            "protocol_version": PROTOCOL_VERSION,
            "command": (
                "uv run --offline --locked --no-sync --extra cpu --extra eval python "
                f"{OUT_REL}/build_phase_d_freeze.py freeze"
            ),
            "protocol_sha256": freeze["protocol"]["sha256"],
            "freeze_digest": freeze["digest"],
            "phase_d_plan_digest": plan["digest"],
            "dry_plan_digest": DRY_DIGEST,
            "checks": checks,
            "environment": environment(),
            "network_requests": 0,
            "corpus_text_bytes_read": 0,
            "phase_p_root_writes": 0,
            "tests": "NOT RUN in the freeze commit; the implementation commit carries tests",
        }
    )
    write("verification.json", verification)
    print(f"protocol sha256               {freeze['protocol']['sha256']}")
    print(f"freeze digest                 {freeze_ref['digest']}")
    print(f"verification digest           {verification['digest']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
