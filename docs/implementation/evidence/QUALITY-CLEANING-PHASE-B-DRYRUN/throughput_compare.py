"""Authored-data throughput: Phase-A audit vs Phase-B dry run, 1 worker, same corpus.

Usage: ``python throughput_compare.py <new scratch dir> <corpus MiB>`` from the
repository root. Uses the benchmark's authored corpus generator only.
"""

import json
import shutil
import sys
import time
from pathlib import Path

from xlm.data.quality.bench import build_corpus
from xlm.data.quality.cleaning_policy import freeze_policy
from xlm.data.quality.cleaning_runner import run_dry_run
from xlm.data.quality.runner import Limits, run_audit

scratch = Path(sys.argv[1])
shutil.rmtree(scratch, ignore_errors=True)
scratch.mkdir(parents=True)
manifest = build_corpus(scratch / "corpus", int(sys.argv[2]) * 1024**2)
limits = Limits(1, 8 * 1024**3, 0, 4 * 1024**3, 64 * 1024**2, 3600.0)
started = time.monotonic()
audit = run_audit(manifest, scratch / "audit", limits=limits, progress_interval=None)
audit_wall = time.monotonic() - started
template = Path("recipes/quality/cleaning_policy_v1.yaml")
freeze_policy(template, scratch / "audit", scratch / "frozen.yaml")
started = time.monotonic()
dry = run_dry_run(
    manifest, scratch / "dry", scratch / "frozen.yaml", limits=limits, progress_interval=None
)
dry_wall = time.monotonic() - started
summary = {
    "audit_wall": round(audit_wall, 2),
    "audit_scan_MBps": audit["scan"]["file_mb_per_s"],
    "dry_wall": round(dry_wall, 2),
    "dry_scan_MBps": dry["scan"]["file_mb_per_s"],
    "docs": dry["scan"]["scanned_documents"],
    "bytes": dry["scan"]["scanned_file_bytes"],
    "status": dry["policy_status"],
}
print(json.dumps(summary))
