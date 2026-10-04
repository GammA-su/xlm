import json, shutil, sys, time
from pathlib import Path
from xlm.data.quality.runner import Limits, run_audit
if __name__ == "__main__":
    manifest, out, w = Path(sys.argv[1]), Path(sys.argv[2]), int(sys.argv[3])
    shutil.rmtree(out, ignore_errors=True)
    lim = Limits(workers=w, max_rss_bytes=12 * 2**30, free_reserve_bytes=0, max_output_bytes=4 * 2**30, line_ceiling=64 * 2**20, deadline_seconds=7200.0)
    t = time.monotonic(); r = run_audit(manifest, out, limits=lim, progress_interval=None); dt = time.monotonic() - t
    s = r["scan"]; a = r.get("activity", {})
    print(json.dumps({"workers": w, "scan_mb_s": s["file_mb_per_s"], "steady_mb_s": round(s["scanned_file_bytes"] / 1e6 / a["worker_span_seconds"], 2) if a else None, "wall_s": round(dt, 1), "rss_gib": round(s["peak_process_tree_rss_bytes"] / 2**30, 2), "peak_inflight": s["peak_tasks_in_flight"], "busy": a.get("worker_busy_fraction")}))
    shutil.rmtree(out, ignore_errors=True)
