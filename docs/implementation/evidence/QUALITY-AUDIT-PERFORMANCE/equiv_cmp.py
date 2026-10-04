import sys
from pathlib import Path
S = Path(sys.argv[1]); a, b = sys.argv[2], sys.argv[3]
ok = True
for case in sorted(p.name for p in (S / "eq" / a).iterdir()):
    da, db = S / "eq" / a / case, S / "eq" / b / case
    ba, bb = (da / "binding_digest").read_text(), (db / "binding_digest").read_text()
    for f in sorted(p.name for p in da.iterdir() if p.name != "binding_digest"):
        x, y = (da / f).read_bytes(), (db / f).read_bytes()
        same_raw = x == y
        norm = x.replace(ba.encode(), b"<BINDING>") == y.replace(bb.encode(), b"<BINDING>")
        ok &= norm
        print(f"{case:8s} {f:34s} raw={'same' if same_raw else 'diff'} normalized={'IDENTICAL' if norm else 'DIFFERENT'} binding_occurrences={x.count(ba.encode())}")
print("ALL IDENTICAL (binding digest normalized)" if ok else "DIFFERENCES FOUND")
