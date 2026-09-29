"""Read-only actual Phase-P review; writes only review artifacts on F:.

Never invokes the acquisition engine. Reads SQLite immutable/read-only and
Parquet structural metadata only; never statistics, KV metadata or row values.
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
import platform
import shutil
import socket
import sqlite3
import subprocess
import tempfile
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

import psutil
import pyarrow as pa
import pyarrow.parquet as pq

OUT = Path(__file__).resolve().parent
REPO = OUT.parents[3]
ROOT = Path("G:/Project/xlm-evidence-v4.1/essential-web")
EVIDENCE = REPO / "docs/implementation/evidence"
MAX_ROOT = 268435456
MAX_BODY = 4194304
EXPECTED = {
    "protocol_version": "essential-web-evidence-v4.1",
    "protocol_sha256": "3d667a263b92696a2d7a266f896e460b10a81ae38097f788c8b885ce768079cf",
    "freeze_digest": "285015604d30e1699b5f63fa4f25ec9c747c778be2f372bb87119adc0e455f80",
    "plan_digest": "762cef78051011d518c4918a41aa87cf81fbb3224bf438af8cc1c499cfca7711",
    "selection_digest": "975ba3dee4af0598e665ea05c69bbe49336c3e3a35fca777a863073190b78474",
}
MEMBERSHIP = "304bd0761125b48720a4de9c82bb6b3bf47100b8a15fc0953d548a8a1dab38bd"
REVISION = "ce4eccc7e9604667b6d7f32cb6274b8b41f3113d"


def deny_network(*args: Any, **kwargs: Any) -> None:
    raise RuntimeError("network is forbidden in this review")


socket.create_connection = deny_network
socket.getaddrinfo = deny_network


def canonical(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode()


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def self_digest(obj: dict[str, Any]) -> str:
    return sha(canonical({k: v for k, v in obj.items() if k != "digest"}))


def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = dict(pairs)
    assert len(result) == len(pairs), "duplicate JSON key"
    return result


def load(path: Path) -> dict[str, Any]:
    assert path.stat().st_size <= MAX_BODY
    return json.loads(path.read_bytes(), object_pairs_hook=unique)


def sealed(path: Path) -> dict[str, Any]:
    obj = load(path)
    assert self_digest(obj) == obj["digest"], path.name
    return obj


def write(name: str, obj: Any) -> None:
    """Atomic writes restricted to the F: review directory."""
    dest = OUT / name
    assert dest.parent == OUT and OUT.drive == "F:"
    fd, tmp = tempfile.mkstemp(prefix=".review-", dir=OUT)
    with os.fdopen(fd, "wb") as stream:
        stream.write(canonical(obj) + b"\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(tmp, dest)


def inventory() -> dict[str, Any]:
    entries: dict[str, Any] = {}
    paths = [ROOT, *sorted(ROOT.rglob("*"))]
    assert len(paths) <= 1000
    assert sum(p.stat().st_size for p in paths if p.is_file()) <= MAX_ROOT
    for path in paths:
        assert not path.is_symlink(), "unexpected evidence symlink"
        st = path.stat()
        item: dict[str, Any] = {"size": st.st_size, "mtime_ns": st.st_mtime_ns,
                                "mode": st.st_mode}
        if path.is_file():
            h = hashlib.sha256()
            with path.open("rb") as stream:
                while block := stream.read(65536):
                    h.update(block)
            item["sha256"] = h.hexdigest()
        entries[path.relative_to(ROOT).as_posix()] = item
    return entries


def body(outputs: dict[str, Any], op_id: str) -> bytes:
    row = outputs[op_id]
    assert row["retained_file"] == f"payload/{op_id}.bin"
    raw = (ROOT / row["retained_file"]).read_bytes()
    assert len(raw) == row["bytes"] <= MAX_BODY and sha(raw) == row["sha256"]
    return raw


def chunk(col: Any) -> dict[str, Any]:
    data = int(col.data_page_offset)
    dictionary = col.dictionary_page_offset
    assert data >= 4
    assert dictionary is None or 4 <= int(dictionary) <= data
    start = data if dictionary is None else int(dictionary)
    size = int(col.total_compressed_size)
    assert size > 0
    return {"path": str(col.path_in_schema), "physical_type": str(col.physical_type),
            "compression": str(col.compression), "start": start,
            "end_exclusive": start + size, "compressed_bytes": size,
            "uncompressed_bytes": int(col.total_uncompressed_size)}


def metadata(footer: bytes, trailer: bytes) -> Any:
    assert len(trailer) == 8 and trailer[4:] == b"PAR1"
    assert int.from_bytes(trailer[:4], "little") == len(footer)
    assert 0 < len(footer) <= MAX_BODY
    return pq.ParquetFile(pa.BufferReader(b"PAR1" + footer + trailer), pre_buffer=False,
                         thrift_string_size_limit=33554432,
                         thrift_container_size_limit=33554432).metadata


def dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


def allocated_bytes(names: set[str]) -> int:
    """Windows FILE_STANDARD_INFO.AllocationSize, using attribute-only handles."""
    from ctypes import wintypes

    class StandardInfo(ctypes.Structure):
        _fields_ = [("allocation", ctypes.c_longlong), ("eof", ctypes.c_longlong),
                    ("links", wintypes.DWORD), ("delete_pending", ctypes.c_ubyte),
                    ("directory", ctypes.c_ubyte)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                       wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    query = kernel.GetFileInformationByHandleEx
    query.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    query.restype = wintypes.BOOL
    close = kernel.CloseHandle
    close.argtypes = [wintypes.HANDLE]
    close.restype = wintypes.BOOL
    total = 0
    for name in names:
        handle = create(str(ROOT / name), 0x80, 7, None, 3, 0, None)
        if handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            info = StandardInfo()
            if not query(handle, 1, ctypes.byref(info), ctypes.sizeof(info)):
                raise ctypes.WinError(ctypes.get_last_error())
            assert info.eof == (ROOT / name).stat().st_size
            total += info.allocation
        finally:
            close(handle)
    return total


def main() -> None:
    started = time.perf_counter()
    assert ROOT.exists()
    assert not list(ROOT.glob("state.sqlite-*")), "SQLite journal/WAL needs separate review"
    before = inventory()
    if (OUT / "root-before.json").exists():
        assert load(OUT / "root-before.json") == before, "evidence changed since first snapshot"
    else:
        write("root-before.json", before)
    plan = sealed(EVIDENCE / "ESSENTIAL-WEB-EVIDENCE-V4.1/phase_p_plan.json")
    freeze = sealed(EVIDENCE / "ESSENTIAL-WEB-EVIDENCE-V4.1/freeze.json")
    parent = sealed(EVIDENCE / "ESSENTIAL-WEB-EVIDENCE-V4.0/phase_p_plan.json")
    assert plan["digest"] == EXPECTED["plan_digest"]
    assert freeze["digest"] == EXPECTED["freeze_digest"]
    assert sha((REPO / freeze["protocol"]["path"]).read_bytes()) == EXPECTED["protocol_sha256"]
    shared = lambda p: {k: v for k, v in p.items() if k not in
                         ("digest", "execution_root", "network", "protocol_version")}
    assert shared(plan) == shared(parent) and sha(canonical(shared(plan))) == MEMBERSHIP
    assert plan["source"]["revision"] == REVISION
    assert plan["selection_digest"] == EXPECTED["selection_digest"]
    assert plan["execution_root"] == str(ROOT)
    docs = {n: sealed(ROOT / n) for n in ("phase_p_receipt.json", "artifact_manifest.json",
                                         "m_phase_p_layout.json", "t_phase_p_layout.json")}
    receipt, manifest = docs["phase_p_receipt.json"], docs["artifact_manifest.json"]
    common_keys = {"kind", "protocol_version", "protocol_sha256", "freeze_digest", "plan_digest",
                   "scientific_namespace", "selection_digest", "source", "synthetic", "digest"}
    assert set(manifest) == common_keys | {"status", "receipt_digest", "artifacts"}
    assert set(receipt) == common_keys | {"status", "run_status", "stop_reason", "created_utc",
        "finalized_utc", "policy_digest", "source_files", "operations", "totals", "request_receipt",
        "layouts", "limits", "phase_d"}
    for name in ("m_phase_p_layout.json", "t_phase_p_layout.json"):
        assert set(docs[name]) == common_keys | {"files"}
    for name, doc in docs.items():
        for key, expected in EXPECTED.items():
            assert doc[key] == expected, (name, key)
        assert doc["source"] == plan["source"]
        assert doc["scientific_namespace"] == plan["scientific_namespace"]
        assert doc["synthetic"] is False
    assert receipt["status"] == receipt["run_status"] == manifest["status"] == "COMPLETE"
    assert receipt["stop_reason"] is None and receipt["phase_d"] == "NOT PERFORMED"
    assert receipt["limits"] == plan["limits"] and receipt["policy_digest"] == plan["policy_digest"]
    assert manifest["receipt_digest"] == receipt["digest"]
    actual_files = {n for n, entry in before.items() if "sha256" in entry}
    assert actual_files == set(manifest["artifacts"]) | {"state.sqlite", "artifact_manifest.json"}
    for name, binding in manifest["artifacts"].items():
        assert before[name]["sha256"] == binding["sha256"]
        assert before[name]["size"] == binding["bytes"]
    for name, binding in receipt["layouts"].items():
        assert binding["digest"] == docs[name]["digest"]
        assert binding["bytes"] == before[name]["size"]
        assert binding["sha256"] == before[name]["sha256"]

    conn = sqlite3.connect((ROOT / "state.sqlite").as_uri() + "?mode=ro&immutable=1", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        assert {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")} == {
            "run", "operations", "attempts", "outputs", "sqlite_sequence"}
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert not conn.execute("PRAGMA foreign_key_check").fetchall()
        runs = [dict(r) for r in conn.execute("SELECT * FROM run")]
        ops = [dict(r) for r in conn.execute("SELECT * FROM operations ORDER BY seq")]
        attempts = [dict(r) for r in conn.execute("SELECT * FROM attempts ORDER BY attempt_id")]
        outputs = {r["op_id"]: dict(r) for r in conn.execute("SELECT * FROM outputs")}
    finally:
        conn.close()
    assert len(runs) == 1
    run = runs[0]
    assert run["status"] == "COMPLETE" and run["stop_reason"] is None and run["synthetic"] == 0
    for key in ("protocol_version", "plan_digest", "selection_digest"):
        assert run[key] == EXPECTED[key]
    assert run["source_revision"] == REVISION
    assert run["created_utc"] == receipt["created_utc"] == "2026-09-29T19:40:03.449281Z"
    assert run["updated_utc"] == "2026-09-29T19:40:45.432227Z"
    assert dt(receipt["finalized_utc"]) >= dt(run["updated_utc"])
    assert len(ops) == len(outputs) == len(plan["operations"]) == 40
    assert len(attempts) == 80 and [a["attempt_id"] for a in attempts] == list(range(1, 81))
    exported = b"".join(canonical(a) + b"\n" for a in attempts)
    assert exported == (ROOT / "request_receipt.jsonl").read_bytes()
    assert receipt["request_receipt"] == {"path": "request_receipt.jsonl", "lines": 80,
                                           "bytes": len(exported), "sha256": sha(exported)}
    files = {(f["arm"], f["ordinal"]): f for f in plan["files"]}
    canonical_url = lambda f: ("https://huggingface.co/datasets/" + plan["source"]["repository"]
                              + "/resolve/" + REVISION + "/" + f["file"])
    assert receipt["source_files"] == [
        {k: f[k] for k in ("arm", "ordinal", "file", "remote_length", "strong_etag")}
        | {"canonical_url": canonical_url(f)} for f in plan["files"]]
    expected_temp: set[str] = set()
    durations: list[float] = []
    for op, planned, exported_op in zip(ops, plan["operations"], receipt["operations"], strict=True):
        f = files[(planned["arm"], planned["ordinal"])]
        for key in ("op_id", "seq", "arm", "ordinal", "kind"):
            assert op[key] == planned[key] == exported_op[key]
        assert op["file"] == f["file"] and op["status"] == exported_op["status"] == "COMPLETE"
        rng = planned["range"]
        if rng is None:
            trailer = body(outputs, f"T-{f['ordinal']:02d}-trailer")
            assert trailer[4:] == b"PAR1"
            length = int.from_bytes(trailer[:4], "little")
            assert 0 < length <= MAX_BODY
            rng = [f["remote_length"] - 8 - length, f["remote_length"] - 9]
            assert rng[0] >= f["bindings"]["data_span_half_open"][1]
        assert rng == [op["range_start"], op["range_end"]] == exported_op["range"]
        raw = body(outputs, op["op_id"])
        assert len(raw) == rng[1] - rng[0] + 1
        if planned["kind"] == "HEAD_0_3":
            assert raw == b"PAR1"
        elif planned["kind"] in ("M_FOOTER_AND_TRAILER", "T_TRAILER"):
            assert raw[-4:] == b"PAR1"
        chain = [a for a in attempts if a["op_id"] == op["op_id"]]
        assert len(chain) == 2
        assert [(a["try_number"], a["hop"], a["outcome"], a["http_status"]) for a in chain] == [
            (1, 0, "REDIRECT", 302), (1, 1, "SUCCESS", 206)]
        assert chain[0]["url_host"] == "huggingface.co"
        assert "https://" + chain[0]["url_host"] + chain[0]["url_path"] == canonical_url(f)
        assert chain[0]["url_query_sha256"] is None
        assert chain[1]["url_host"] in plan["network"]["signed_target_hosts"]
        assert chain[1]["url_query_sha256"] is not None
        assert outputs[op["op_id"]]["attempt_id"] == chain[1]["attempt_id"]
        assert exported_op["output"] == outputs[op["op_id"]]
        for a in chain:
            assert a["url_scheme"] == "https" and a["url_host"] in plan["network"]["hosts"]
            assert a["arm"] == op["arm"]
            assert a["error"] == ("" if a["outcome"] == "REDIRECT" else None)
            assert [a["range_start"], a["range_end"]] == rng
            assert a["temp_path"] == f"tmp/{op['op_id']}.a{a['attempt_id']}.part"
            assert dt(run["created_utc"]) <= dt(a["started_utc"]) <= dt(a["completed_utc"]) <= dt(run["updated_utc"])
            durations.append((dt(a["completed_utc"]) - dt(a["started_utc"])).total_seconds())
            assert 0 <= a["response_bytes"] <= MAX_BODY
            if a["outcome"] == "REDIRECT":
                expected_temp.add(a["temp_path"])
                binding = before[a["temp_path"]]
                assert binding["size"] == a["response_bytes"]
                assert binding["sha256"] == a["response_sha256"]
            else:
                assert a["temp_path"] not in before
                assert a["response_bytes"] == len(raw) and a["response_sha256"] == sha(raw)
    assert {n for n in actual_files if n.startswith("tmp/")} == expected_temp
    assert {n for n in actual_files if n.startswith("payload/")} == {o["retained_file"] for o in outputs.values()}
    totals: dict[str, Any] = {}
    for arm in ("M", "T"):
        aa = [a for a in attempts if a["arm"] == arm]
        oo = [o for o in ops if o["arm"] == arm]
        totals[arm] = {"logical_operations": len(oo), "logical_complete": len(oo),
                       "physical_attempts": len(aa), "response_body_bytes": sum(a["response_bytes"] for a in aa),
                       "attempts_by_outcome": dict(Counter(a["outcome"] for a in aa)),
                       "retained_payload_bytes": sum(outputs[o["op_id"]]["bytes"] for o in oo)}
        assert len(aa) <= 200 and totals[arm]["response_body_bytes"] <= 67108864
    totals["all"] = {k: totals["M"][k] + totals["T"][k] for k in totals["M"] if k != "attempts_by_outcome"}
    assert totals == receipt["totals"]
    assert (totals["M"]["response_body_bytes"], totals["T"]["response_body_bytes"]) == (1415952, 1496825)
    assert (totals["M"]["retained_payload_bytes"], totals["T"]["retained_payload_bytes"]) == (1398416, 1470541)

    selection_path = Path("F:/Project/xlm-evidence-v2/essential-web/text_selection_manifest.json")
    selection = sealed(selection_path)
    assert selection["digest"] == EXPECTED["selection_digest"]
    assert sha(selection_path.read_bytes()) == "8424f9668fef6952a558ead0dae27a705545a7f4a4d302713707d3ec6bb1af27"
    units = [u for c in selection["cells"] for u in ([c] if "identities" in c else c["crawls"])]
    locators = [i for u in units for i in u["identities"]]
    assert len(locators) == len({tuple(i) for i in locators}) == 118
    prior = sealed(EVIDENCE / "ESSENTIAL-WEB-EVIDENCE-V3.0/freeze.json")
    dry_files: dict[str, list[Any]] = {"M": [], "T": []}
    layout_summary: dict[str, list[Any]] = {"M": [], "T": []}
    for arm, name in (("M", "m_phase_p_layout.json"), ("T", "t_phase_p_layout.json")):
        assert len(docs[name]["files"]) == 8
        assert [f["ordinal"] for f in docs[name]["files"]] == list(range(8))
        for entry in docs[name]["files"]:
            assert set(entry) == {"arm", "ordinal", "file", "remote_length", "strong_etag",
                "canonical_url", "payloads", "bindings", "layout"} | (
                    {"validated_footer_length", "footer_range"} if arm == "T" else set())
            f = files[(arm, entry["ordinal"])]
            for key in ("arm", "ordinal", "file", "remote_length", "strong_etag"):
                assert entry[key] == f[key]
            assert entry["canonical_url"] == canonical_url(f)
            ids = [o["op_id"] for o in ops if o["arm"] == arm and o["ordinal"] == f["ordinal"]]
            assert entry["payloads"] == {i: {k: outputs[i][k] for k in ("bytes", "retained_file", "sha256")} for i in ids}
            op_id = f"{arm}-{f['ordinal']:02d}-footer"
            footer_op = next(o for o in ops if o["op_id"] == op_id)
            footer_start = footer_op["range_start"]
            footer_body = body(outputs, op_id)
            if arm == "M":
                footer, trailer = footer_body[:-8], footer_body[-8:]
            else:
                footer, trailer = footer_body, body(outputs, f"T-{f['ordinal']:02d}-trailer")
            meta = metadata(footer, trailer)
            assert footer_start == f["remote_length"] - 8 - len(footer)
            first = 0
            groups = []
            for gi in range(meta.num_row_groups):
                group = meta.row_group(gi)
                columns = [chunk(group.column(ci)) for ci in range(group.num_columns)]
                assert all(c["end_exclusive"] <= footer_start for c in columns)
                groups.append((gi, first, group.num_rows, group, columns))
                first += group.num_rows
            assert first == meta.num_rows
            common = {"num_rows": meta.num_rows, "num_row_groups": meta.num_row_groups,
                      "footer_length": len(footer), "footer_range": [footer_start, footer_op["range_end"]]}
            base = {k: f[k] for k in ("arm", "ordinal", "file", "remote_length", "strong_etag")}
            base["canonical_url"] = canonical_url(f)
            if arm == "M":
                b = f["bindings"]
                assert entry["bindings"] == b
                ws, we = b["window"]
                assert we - ws == 512 and b["projection"] == ["eai_taxonomy", "quality_signals"]
                hits = [g for g in groups if g[1] <= ws and we <= g[1] + g[2]]
                assert len(hits) == 1
                gi, first, rows, group, columns = hits[0]
                chosen = sorted([c for c in columns if c["path"].split(".")[0] in b["projection"]],
                                key=lambda c: (c["start"], c["path"]))
                assert len(chosen) == b["data_chunk_count"] == 81
                assert sum(c["compressed_bytes"] for c in chosen) == b["data_payload_bytes"]
                assert sum(c["uncompressed_bytes"] for c in chosen) == b["data_uncompressed_bytes"]
                assert all(a["end_exclusive"] == z["start"] for a, z in zip(chosen, chosen[1:]))
                computed = common | {"window": [ws, we], "window_row_group": gi,
                    "window_row_group_first_row": first, "window_row_group_rows": rows,
                    "projected_chunks": chosen,
                    "projected_span_half_open": [chosen[0]["start"], chosen[-1]["end_exclusive"]]}
                assert computed == entry["layout"]
                start, end = chosen[0]["start"], chosen[-1]["end_exclusive"]
                assert end - start == b["data_payload_bytes"] <= MAX_BODY
                ranges = [{"start": start, "end_exclusive": end, "bytes": end-start}]
                dry = base | {"window": [ws, we], "row_group": gi, "row_group_first_row": first,
                    "projection": b["projection"], "projected_chunk_ranges": [
                        {k: c[k] for k in ("path", "start", "end_exclusive", "compressed_bytes")} for c in chosen],
                    "request_ranges": ranges, "coalescing": "only exact adjacent selected chunks; zero gap bytes"}
                summary = {"ordinal": f["ordinal"], "file": f["file"], "chunk_count": len(chosen),
                    "row_group": gi, "row_group_first_row": first, "row_group_rows": rows,
                    "window": [ws, we], "phase_d_bytes": end-start, "request_ranges": ranges}
            else:
                b = f["bindings"]
                span_start, span_end = b["data_span_half_open"]
                assert entry["bindings"] == {"text_column": b["text_column"], "span_start": span_start,
                    "span_end": span_end, "data_range_count": b["data_range_count"],
                    "data_payload_bytes": b["data_payload_bytes"]}
                assert entry["validated_footer_length"] == len(footer) and entry["footer_range"] == common["footer_range"]
                text = [(g, c) for g in groups for c in g[4] if c["path"] == "text"]
                hits = [(g, c) for g, c in text if (c["start"], c["end_exclusive"]) == (span_start, span_end)]
                assert len(hits) == 1
                (gi, first, rows, group, _), chosen = hits[0]
                chosen = chosen | {"row_group": gi, "first_row": first, "num_rows": rows}
                assert chosen["compressed_bytes"] == b["data_payload_bytes"]
                computed = common | {"text_chunks": len(text), "selected_text_chunk": chosen,
                                     "frozen_data_range_count": b["data_range_count"]}
                assert computed == entry["layout"]
                col = next(group.column(ci) for ci in range(group.num_columns)
                           if group.column(ci).path_in_schema == "text")
                ranges = [{"start": start, "end_exclusive": min(start+MAX_BODY, span_end),
                           "bytes": min(MAX_BODY, span_end-start)} for start in range(span_start, span_end, MAX_BODY)]
                prior_file = next(p for p in prior["T_prospective_files"] if p["file"] == f["file"])
                assert [{"start": r["start"], "end": r["end_exclusive"], "bytes": r["bytes"]} for r in ranges] == prior_file["data_ranges_half_open"]
                assert len(ranges) == b["data_range_count"]
                selected = sorted([i for i in locators if i[2] == f["file"]], key=lambda i: i[3])
                assert all(i[0] == plan["source"]["repository"] and i[1] == REVISION and first <= i[3] < first+rows for i in selected)
                assert len(selected) == prior_file["retained_rows"]
                dry = base | {"text_column": "text", "row_group": gi, "row_group_first_row": first,
                    "row_group_rows": rows, "dictionary_page_offset": col.dictionary_page_offset,
                    "data_page_offset": col.data_page_offset, "chunk_span_half_open": [span_start, span_end],
                    "request_ranges": ranges, "locators": [
                        {"identity": i, "row_in_group": i[3]-first} for i in selected],
                    "coalescing": "none across the 4 MiB request ceiling; adjacent pieces reconstruct one dictionary-inclusive chunk"}
                summary = {"ordinal": f["ordinal"], "file": f["file"], "row_group": gi,
                    "dictionary_page_offset": col.dictionary_page_offset, "data_page_offset": col.data_page_offset,
                    "span_half_open": [span_start, span_end], "range_count": len(ranges),
                    "phase_d_bytes": span_end-span_start, "locator_count": len(selected)}
            dry_files[arm].append(dry)
            layout_summary[arm].append(summary)

    assert sum(f["locator_count"] for f in layout_summary["T"]) == 118
    assert sum(f["range_count"] for f in layout_summary["T"]) == 47
    assert sum(f["phase_d_bytes"] for f in layout_summary["T"]) == 179963169
    dry_totals = {}
    for arm in ("M", "T"):
        n = sum(len(f["request_ranges"]) for f in dry_files[arm])
        size = sum(r["bytes"] for f in dry_files[arm] for r in f["request_ranges"])
        dry_totals[arm] = {"logical_operations": n, "exact_success_payload_bytes": size,
            "physical_attempts_if_one_redirect_no_retry": 2*n,
            "scenario_max_physical_attempts_if_3_tries_3_redirects": 12*n,
            "scenario_max_body_bytes_if_every_attempt_returns_4MiB": 12*n*MAX_BODY}
    parents = {name: {"digest": doc["digest"], "sha256": before[name]["sha256"],
                     "bytes": before[name]["size"]} for name, doc in docs.items()}
    dry_plan = {"kind": "essential_web_v41_phase_d_structural_dry_plan", "status": "DRY_NOT_AUTHORIZED",
        "source": plan["source"], "selection_digest": EXPECTED["selection_digest"],
        "phase_p_plan_digest": EXPECTED["plan_digest"], "membership_and_ranges_digest": MEMBERSHIP,
        "phase_p_root": str(ROOT), "parents": parents,
        "state_sqlite": {"sha256": before["state.sqlite"]["sha256"], "bytes": before["state.sqlite"]["size"]},
        "range_convention": "half-open [start,end_exclusive); HTTP Range is start..end_exclusive-1",
        "request_order": "M then T; frozen ordinal then ascending start; all requests start at frozen canonical URL",
        "files": dry_files, "totals": dry_totals,
        "network": plan["network"], "request_body_ceiling_proposal": MAX_BODY,
        "scientific_membership": "unchanged; M same 4096 rows/projection, T same 118 locators",
        "restart_semantics_proposal": [
            "new separately authorized Phase-D root; never mutate or reuse Phase-P or historical roots",
            "bind this dry-plan digest and exact Phase-P parents before first request",
            "durable attempt before request; count every redirect/error/partial/retry body cumulatively",
            "promote only identity-verified complete exact range; hash/size verify complete outputs and skip on restart",
            "retain interrupted evidence; charge interrupted tries; new attempt ID on retry; no scientific duplication",
            "inconsistent state, identity mismatch or exhausted cap stops without fallback or reselection"],
        "authorization_gaps": [
            "Phase D is not implemented or authorized by this package",
            "new protocol must freeze root, arm/request/disk/memory/document/output caps and parsing/output scope",
            "T exact successful payload alone exceeds Phase-P 64 MiB arm cap; Phase-P caps cannot be silently reused",
            "redirect/error/retry bytes are unknown; one-redirect counts are scenarios, not guarantees",
            "scenario maxima are conservative arithmetic, not authorized operational caps",
            "Phase-P payloads may be reused read-only only after parent hashes reverify; no footer refetch in this plan"]}
    dry_plan["digest"] = self_digest(dry_plan)
    after = inventory()
    assert after == before, "G: evidence changed during review"
    root_bytes = sum(e["size"] for e in before.values() if "sha256" in e)
    size_on_disk = allocated_bytes(actual_files)
    assert root_bytes <= MAX_ROOT and size_on_disk <= MAX_ROOT
    result = {"verdict": "PHASE-P RESULT REVIEW PASSED - READY FOR PHASE-D AUTHORIZATION REVIEW",
        "reviewed_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO).decode().strip(),
        "environment": {"os": platform.platform(), "python": platform.python_version(), "pyarrow": pa.__version__},
        "run": run, "bindings": EXPECTED, "membership_and_ranges_digest": MEMBERSHIP,
        "totals": totals, "parents": parents, "observed_hosts": dict(Counter(a["url_host"] for a in attempts)),
        "redirect_body_bytes": {arm: sum(a["response_bytes"] for a in attempts if a["arm"] == arm and a["outcome"] == "REDIRECT") for arm in ("M", "T")},
        "max_response_bytes": max(a["response_bytes"] for a in attempts), "max_redirects": 1,
        "extra_retries": 0, "in_progress": 0, "max_recorded_attempt_elapsed_seconds": max(durations),
        "run_elapsed_seconds": (dt(run["updated_utc"])-dt(run["created_utc"])).total_seconds(),
        "root_file_count": len(actual_files), "root_file_bytes": root_bytes,
        "root_file_allocated_bytes_FILE_STANDARD_INFO": size_on_disk,
        "current_G_free_bytes": shutil.disk_usage(ROOT).free,
        "G_evidence_before_after_equal": True,
        "layout_summary": layout_summary, "phase_d_dry_plan_digest": dry_plan["digest"],
        "phase_d_dry_totals": dry_totals,
        "identity_evidence_qualification": "206/ranges/body/hash/PAR1 directly checked; Content-Range, ETag, coding, port and Location validation inferred from recorded SUCCESS/REDIRECT under reviewed code. Raw response headers are not persisted by the frozen schema.",
        "limitations": ["no network or Phase-P rerun; no Phase D; no row/text/statistics/KV inspection",
                        "current root size/free space are measured, not historical high-water/minimum telemetry",
                        "UTC attempt durations are recorded observations, not independent socket timing",
                        "no live execution code attestation or malicious-local-operator claim"],
        "review_elapsed_seconds": round(time.perf_counter()-started, 6),
        "review_peak_working_set_bytes": psutil.Process().memory_info().peak_wset}
    write("root-after.json", after)
    write("phase_d_dry_plan.json", dry_plan)
    write("review-result.json", result)
    print(json.dumps({k: result[k] for k in ("verdict", "totals", "observed_hosts", "redirect_body_bytes",
        "max_response_bytes", "max_recorded_attempt_elapsed_seconds", "root_file_bytes",
        "root_file_allocated_bytes_FILE_STANDARD_INFO", "phase_d_dry_plan_digest", "phase_d_dry_totals",
        "review_elapsed_seconds", "review_peak_working_set_bytes")}, indent=2))


if __name__ == "__main__":
    main()
