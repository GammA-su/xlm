"""Independent injection probe: an M data-chunk and a T text-page operation row
inserted into a partial synthetic root must STOP before any request (offline)."""

from __future__ import annotations

import json
import socket
import sqlite3
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
sys.path[:0] = [str(REPO / "src"), str(REPO / "tests")]
socket.create_connection = None  # type: ignore[assignment]
socket.getaddrinfo = None  # type: ignore[assignment]

import evidence_v4_support as sup  # noqa: E402
from xlm.data.evidence_v4 import state  # noqa: E402

out = {}
for name, arm, kind, anchor in (
    ("M_data_chunk", "M", "M_DATA_CHUNK", "M-00-head"),
    ("T_text_page", "T", "T_TEXT_PAGE", "T-01-head"),
):
    work = Path(tempfile.mkdtemp(prefix=f"inject-{name}-"))
    fx = sup.build_fixture(work / "fixture")
    root = work / "root"
    crash = sup.Rule(sup.at(fx, "T-00-head"), lambda c: sup.SimulatedCrash("stop"))
    try:
        sup.run(root, fx, sup.SyntheticTransport(fx, rules=[crash]))
    except sup.SimulatedCrash:
        pass
    conn = sqlite3.connect(root / state.DB_NAME)
    conn.execute(
        "INSERT INTO operations VALUES (?, 10, ?, 1, ?, ?, 4, 4194307, 'PENDING')",
        (f"{arm}-01-data", arm, fx.op(anchor).source_file.file, kind),
    )
    conn.commit()
    conn.close()
    transport = sup.SyntheticTransport(fx)
    result = sup.run(root, fx, transport)
    out[name] = {
        "status": result.status,
        "run_status": result.run_status,
        "stop_reason": result.stop_reason,
        "requests_after_injection": len(transport.calls),
    }
print(json.dumps(out, indent=1))
