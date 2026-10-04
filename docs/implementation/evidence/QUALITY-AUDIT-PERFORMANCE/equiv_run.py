"""Run the audit (whatever xlm is importable) on the shared inputs; copy artifacts out."""
import json, os, shutil, sys
from pathlib import Path
from xlm.data.quality.runner import Limits, run_audit
from xlm.data.quality.report import ARTIFACTS
def lim(w):
    return Limits(workers=w, max_rss_bytes=12 * 2**30, free_reserve_bytes=0, max_output_bytes=4 * 2**30, line_ceiling=64 * 2**20, deadline_seconds=3600.0)
if __name__ == "__main__":
    S, tag, w = Path(sys.argv[1]), sys.argv[2], int(sys.argv[3])
    import xlm; print("xlm from", xlm.__file__, file=sys.stderr)
    c05 = json.load(open(S / "inp/c05.json"))
    os.environ[c05["key_env"]] = c05["key"]
    cases = {
        "bench": dict(manifest=S / "c256/manifest.json"),
        "fixture": dict(manifest=S / "inp/fixture/manifest.json"),
        "overlay": dict(manifest=Path(c05["manifest"]), proof=Path(c05["proof"]), allow_authored_proof=True),
    }
    only = sys.argv[4].split(",") if len(sys.argv) > 4 else list(cases)
    for name in only:
        kw = cases[name]
        out = S / f"eq/{tag}/{name}/out"
        shutil.rmtree(out.parent, ignore_errors=True); out.parent.mkdir(parents=True)
        r = run_audit(kw.pop("manifest"), out, limits=lim(w), progress_interval=None, **kw)
        binding = json.loads((out / "audit-binding.json").read_bytes())
        for a in ARTIFACTS:
            shutil.copy(out / a, out.parent / a)
        (out.parent / "binding_digest").write_text(binding["digest"])
        shutil.rmtree(out)
        print(name, r["result_digest"], r["scan"]["file_mb_per_s"])
