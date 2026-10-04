import hashlib, json, sys, time, types
from pathlib import Path
import xlm.data.exclusion.fitfast as FF
from xlm.data.exclusion.selection import allocation_key
from xlm.data.quality import overlay as OV
S = Path(sys.argv[1]); N = int(sys.argv[2]); d = S / "ovl"; d.mkdir(exist_ok=True)
files = [types.SimpleNamespace(path=f"canonical/c{i}/v/documents.jsonl", documents=N // 10 + 5, component=f"comp{i}", view="v", upstream_component=None, source_id=f"src{i}") for i in range(10)]
rows = []
for n in range(N):
    f = files[n % 10]
    rows.append({"bytes": 600 + n % 50, "component": f.component, "content": hashlib.sha256(str(n).encode()).hexdigest(), "decision": "kept",
                 "doc_id": f"{n:064d}", "duplicate_group": f"{n:064d}", "file": f.path, "lineage_group": f"{n:064d}", "quick": False,
                 "row": n // 10 + 1, "source_id": f.source_id, "split": "train", "upstream_component": None, "view": "v"})
from xlm.data.exclusion.fitscan import MEMBERSHIP_KEYS
assert set(rows[0]) == MEMBERSHIP_KEYS, set(rows[0]) ^ MEMBERSHIP_KEYS
raw = b"".join(json.dumps(r, sort_keys=True, separators=(",", ":")).encode() + b"\n" for r in rows)
(d / "membership.jsonl").write_bytes(raw)
alloc = {}
for f in files:
    k = allocation_key(f.component, f.view, f.upstream_component)
    kept = sum(1 for r in rows if r["file"] == f.path)
    alloc[k] = {"kept": kept, "train_bytes": sum(r["bytes"] for r in rows if r["file"] == f.path), "excluded": f.documents - kept, "duplicate": 0}
completion = {"membership_bytes": len(raw), "kept": N, "membership_sha256": hashlib.sha256(raw).hexdigest(), "allocations": alloc}
plan = types.SimpleNamespace(input_manifest_digest="m", files=files, resources=types.SimpleNamespace(document_bytes=64 * 2**20), scratch_root="/nonexistent", isolation=None)
view = types.SimpleNamespace(plan=plan, completion=completion, directory=d, mode="authored", plan_digest="p", receipt_digest="r")
FF.open_streamed = lambda *a, **k: view
proof = d / "proof.json"; proof.write_text("{}")
t = time.perf_counter()
ov = OV.load_overlay(proof, manifest_digest="m", documents={f.path: f.documents for f in files}, allow_authored=True, consumes=[])
dt = time.perf_counter() - t
print(f"rows {N} bytes {len(raw)/1e6:.0f}MB: {dt:.2f}s = {dt/N*1e6:.1f} us/row, {len(raw)/1e6/dt:.1f} MB/s")
import numpy as np
tag = sys.argv[3]
np.savez(S / f"ovl_{tag}.npz", **{f"k{i}": ov.kept[f.path] for i, f in enumerate(files)}, **{f"i{i}": ov.identity[f.path].view(np.uint8) for i, f in enumerate(files)})
json.dump(ov.binding, open(S / f"ovl_{tag}.json", "w"), sort_keys=True)
