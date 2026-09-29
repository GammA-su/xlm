"""Authored synthetic fixtures for the v4.1 Phase-D tests (offline only).

Everything here is SYNTHETIC. Small Parquet files are authored with PyArrow;
a synthetic COMPLETE Phase-P parent root is produced by running the reviewed
v4 Phase-P engine offline over them (v4.1 profile, scripted transport); the
synthetic Phase-D plan is then derived independently from the authored files'
own metadata and that parent's exports (this module never calls the Phase-D
plan derivation it tests).

T file 0 holds a >4 MiB dictionary-inclusive text chunk (two range pieces);
T file 1 holds an oversized selected document and a null selected value.
Every unselected T row carries :data:`SENTINEL` so tests can prove no
unselected text is exported, logged or rendered.
"""

from __future__ import annotations

import hashlib
import io
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from evidence_v4_support import (
    SYNTHETIC_REVISION,
    Call,
    FakeClock,
    Fixture,
    SyntheticTransport,
    _m_bytes,
    _m_entry,
    _t_entry,
    plan_dict,
)
from xlm.data.evidence_v2 import canonical
from xlm.data.evidence_v4 import frozen, phase_d, phase_p, v41
from xlm.data.evidence_v4 import phase_d_plan as pd
from xlm.data.evidence_v4 import transport as tp

SENTINEL = "UNSELECTED-SENTINEL"
PIECE = 4194304
BIG_ROWS = 2700  # rows of T file 0's selected row group (~4.6 MB of incompressible text)
T0_SELECTED = (3, 700, 2699)
T1_SELECTED = (2, 5, 9, 39)
T1_OVERSIZE_ROW = 5
T1_NULL_ROW = 9


def _noise(seed: str, size: int) -> str:
    out = bytearray()
    counter = 0
    while len(out) < size:
        out += hashlib.sha256(f"{seed}-{counter}".encode()).hexdigest().encode()
        counter += 1
    return out[:size].decode()


def selected_text(ordinal: int, row: int) -> str | None:
    """The authored oracle for selected rows (group-relative)."""
    if ordinal == 1 and row == T1_NULL_ROW:
        return None
    if ordinal == 1 and row == T1_OVERSIZE_ROW:
        return "oversized selected document " + "z" * 70000
    body = f"selected document {ordinal}/{row} héllo — ✓ "
    return body + (_noise(f"sel{ordinal}-{row}", 1600) if ordinal == 0 else "short")


def _group(ordinal: int, group: int, rows: int, selected: tuple[int, ...]) -> pa.Table:
    texts: list[str | None] = []
    for r in range(rows):
        if group == 1 and r in selected:
            texts.append(selected_text(ordinal, r))
        else:
            size = 1600 if (ordinal == 0 and group == 1) else 40
            texts.append(
                f"{SENTINEL} {ordinal}/{group}/{r} " + _noise(f"u{ordinal}{group}{r}", size)
            )
    return pa.table(
        {
            "id": [f"t{ordinal}-{group}-{r:05d}" for r in range(rows)],
            "text": pa.array(texts, type=pa.string()),
        }
    )


def _t_bytes(ordinal: int) -> bytes:
    sizes = (20, BIG_ROWS, 10) if ordinal == 0 else (5, 40, 5)
    selected = T0_SELECTED if ordinal == 0 else T1_SELECTED
    sink = io.BytesIO()
    tables = [_group(ordinal, g, n, selected) for g, n in enumerate(sizes)]
    with pq.ParquetWriter(sink, tables[0].schema, compression="snappy") as writer:
        for table in tables:
            writer.write_table(table, row_group_size=len(table))
    return sink.getvalue()


def _binding(raw: bytes) -> dict[str, Any]:
    return {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def _pieces(start: int, end: int) -> list[tuple[int, int]]:
    out = []
    while start < end:
        out.append((start, min(start + PIECE, end)))
        start = out[-1][1]
    return out


def phase_d_plan_dict(fx: Fixture, parent: Path) -> dict[str, Any]:
    """Author the synthetic Phase-D plan from the parent's exports and the files' metadata."""
    m_layout = json.loads((parent / "m_phase_p_layout.json").read_bytes())
    t_layout = json.loads((parent / "t_phase_p_layout.json").read_bytes())
    files: list[dict[str, Any]] = []
    operations: list[dict[str, Any]] = []
    for entry in m_layout["files"]:
        lay = entry["layout"]
        i = entry["ordinal"]
        span = lay["projected_span_half_open"]
        files.append(
            {
                "arm": "M",
                "ordinal": i,
                "file": entry["file"],
                "remote_length": entry["remote_length"],
                "strong_etag": entry["strong_etag"],
                "window": lay["window"],
                "row_group": lay["window_row_group"],
                "row_group_first_row": lay["window_row_group_first_row"],
                "row_group_rows": lay["window_row_group_rows"],
                "projection": list(frozen.PROJECTION),
                "projected_chunks": [
                    {k: c[k] for k in ("path", "start", "end_exclusive", "compressed_bytes")}
                    for c in lay["projected_chunks"]
                ],
                "range_half_open": span,
                "uncompressed_bytes": entry["bindings"]["data_uncompressed_bytes"],
                "phase_p_payloads": {"footer": f"payload/M-{i:02d}-footer.bin"},
                "footer_start": lay["footer_range"][0],
            }
        )
        operations.append(
            {
                "op_id": f"M-{i:02d}-d00",
                "arm": "M",
                "ordinal": i,
                "piece": 0,
                "kind": "M_PROJECTED_RANGE",
                "range": [span[0], span[1] - 1],
            }
        )
    for entry in t_layout["files"]:
        i = entry["ordinal"]
        chunk = entry["layout"]["selected_text_chunk"]
        group = pq.ParquetFile(io.BytesIO(fx.data[entry["file"]])).metadata.row_group(
            chunk["row_group"]
        )
        (text,) = [
            group.column(c)
            for c in range(group.num_columns)
            if group.column(c).path_in_schema == "text"
        ]
        selected = T0_SELECTED if i == 0 else T1_SELECTED
        span = [chunk["start"], chunk["end_exclusive"]]
        files.append(
            {
                "arm": "T",
                "ordinal": i,
                "file": entry["file"],
                "remote_length": entry["remote_length"],
                "strong_etag": entry["strong_etag"],
                "text_column": "text",
                "row_group": chunk["row_group"],
                "row_group_first_row": chunk["first_row"],
                "row_group_rows": chunk["num_rows"],
                "chunk_span_half_open": span,
                "dictionary_page_offset": int(text.dictionary_page_offset),
                "data_page_offset": int(text.data_page_offset),
                "compressed_bytes": span[1] - span[0],
                "uncompressed_bytes": chunk["uncompressed_bytes"],
                "phase_p_payloads": {
                    "footer": f"payload/T-{i:02d}-footer.bin",
                    "trailer": f"payload/T-{i:02d}-trailer.bin",
                },
                "footer_start": entry["footer_range"][0],
                "locators": [
                    {
                        "identity": [
                            frozen.SYNTHETIC_REPOSITORY,
                            SYNTHETIC_REVISION,
                            entry["file"],
                            chunk["first_row"] + r,
                        ],
                        "row_in_group": r,
                    }
                    for r in selected
                ],
            }
        )
        for piece, (start, end) in enumerate(_pieces(*span)):
            operations.append(
                {
                    "op_id": f"T-{i:02d}-d{piece:02d}",
                    "arm": "T",
                    "ordinal": i,
                    "piece": piece,
                    "kind": "T_TEXT_CHUNK_RANGE",
                    "range": [start, end - 1],
                }
            )
    for seq, op in enumerate(operations):
        op["seq"] = seq
    artifacts: dict[str, Any] = {}
    for name in pd.PARENT_DOCS:
        raw = (parent / name).read_bytes()
        artifacts[name] = {**_binding(raw), "digest": json.loads(raw)["digest"]}
    for name in pd.PARENT_FILES:
        artifacts[name] = _binding((parent / name).read_bytes())
    for rel in sorted(p.relative_to(parent).as_posix() for p in (parent / "payload").iterdir()):
        if rel.endswith(("-footer.bin", "-trailer.bin")):
            artifacts[rel] = _binding((parent / rel).read_bytes())
    dry = {"kind": "synthetic_dry_plan", "ranges": [o["range"] for o in operations]}
    dry_raw = canonical.canonical_bytes(dry)
    synthetic_doc = _binding(b"synthetic review document")
    body: dict[str, Any] = {
        "kind": "essential_web_v41_phase_d_plan",
        "protocol_version": pd.PROTOCOL_VERSION,
        "plan_schema_version": 1,
        "synthetic": True,
        "phase": "D",
        "scientific_namespace": frozen.SCIENTIFIC_NAMESPACE,
        "selection_digest": frozen.SELECTION_DIGEST,
        "policy_digest": frozen.POLICY_DIGEST,
        "source": {
            "host": "huggingface.co",
            "repository_type": "datasets",
            "repository": frozen.SYNTHETIC_REPOSITORY,
            "revision": SYNTHETIC_REVISION,
        },
        "execution_root": pd.SYNTHETIC_ROOT,
        "parents": {
            "dry_plan": {
                "path": "SYNTHETIC",
                **_binding(dry_raw),
                "digest": canonical.digest(dry),
            },
            "result_review": {
                "commit": "1" * 40,
                "report": {"path": "SYNTHETIC", **synthetic_doc},
                "review_result": {"path": "SYNTHETIC", **synthetic_doc},
                "verdict": "SYNTHETIC",
            },
            "phase_p": {
                "root": pd.SYNTHETIC_ROOT,
                "protocol_version": v41.PROTOCOL_VERSION,
                "protocol_sha256": v41.PROTOCOL_SHA256,
                "freeze_digest": v41.FREEZE_DIGEST,
                "plan_digest": fx.plan.digest,
                "membership_and_ranges_digest": v41.membership_and_ranges_digest(fx.plan_obj),
                "run_status": "COMPLETE",
                "artifacts": artifacts,
            },
        },
        "arms": ["M", "T"],
        "files": files,
        "operations": operations,
        "limits": pd.LIMITS,
        "network": v41.NETWORK,
    }
    body["digest"] = canonical.self_digest(body)
    return body


@dataclass
class DFixture:
    directory: Path
    fx: Fixture  # the authored files and the synthetic Phase-P plan
    parent: Path  # the synthetic COMPLETE Phase-P root (read-only for Phase D)
    plan_obj: dict[str, Any]
    plan: pd.PhaseDPlan


def build_dfixture(directory: Path) -> DFixture:
    directory.mkdir(parents=True, exist_ok=True)
    data: dict[str, bytes] = {}
    entries: list[dict[str, Any]] = []
    for i in range(2):
        name = f"data/crawl=SYN-M/train-{i:05d}.parquet"
        data[name] = _m_bytes(120, i)
        entries.append(_m_entry(i, name, data[name]))
    for i in range(2):
        name = f"data/crawl=SYN-T/train-{i:05d}.parquet"
        data[name] = _t_bytes(i)
        entries.append(_t_entry(i, name, data[name]))
    plan_obj = plan_dict(entries, data, v41.PROFILE)
    fx = Fixture(
        directory,
        data,
        plan_obj,
        frozen.validate_plan(plan_obj, synthetic=True, profile=v41.PROFILE),
    )
    parent = directory / "phase-p"
    result = phase_p.run_offline(
        parent,
        plan=fx.plan,
        transport=SyntheticTransport(fx),
        sleep=lambda _: None,
        clock=FakeClock(),
    )
    if result.status != "COMPLETE":
        raise AssertionError(f"synthetic Phase-P parent did not complete: {result}")
    obj = phase_d_plan_dict(fx, parent)
    return DFixture(directory, fx, parent, obj, pd.validate_plan(obj, synthetic=True))


def reseal(obj: dict[str, Any]) -> dict[str, Any]:
    body = {k: v for k, v in obj.items() if k != "digest"}
    body["digest"] = canonical.self_digest(body)
    return body


def at_op(dfx: DFixture, op_id: str, *, host: str | None = None) -> Callable[[Call], bool]:
    """Match calls of one Phase-D operation (by file and exact range)."""
    op = dfx.plan.fetch.operation(op_id)

    def match(call: Call) -> bool:
        if host is not None and call.host != host:
            return False
        file = SyntheticTransport(dfx.fx).file_of(call)
        return file == op.source_file.file and (call.start, call.end) == op.range

    return match


def run_d(
    root: Path,
    dfx: DFixture,
    transport: tp.Transport,
    clock: FakeClock | None = None,
    *,
    parent: Path | None = None,
    plan: pd.PhaseDPlan | None = None,
) -> phase_d.Result:
    return phase_d.run_offline(
        root,
        plan=plan or dfx.plan,
        parent=parent or dfx.parent,
        transport=transport,
        sleep=lambda _: None,
        clock=clock or FakeClock(),
    )


def tree(path: Path) -> dict[str, tuple[int, int, str]]:
    """Content inventory of a directory: size, mtime and SHA-256 of every file."""
    return {
        p.relative_to(path).as_posix(): (
            p.stat().st_size,
            p.stat().st_mtime_ns,
            hashlib.sha256(p.read_bytes()).hexdigest(),
        )
        for p in sorted(path.rglob("*"))
        if p.is_file()
    }
