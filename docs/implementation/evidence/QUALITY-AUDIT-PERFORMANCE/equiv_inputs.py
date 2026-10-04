"""Build shared authored inputs once: fixture corpus, C05 synthetic flow (overlay)."""
import json, os, sys
from pathlib import Path
sys.path.insert(0, "/home/user/xlm/tests"); sys.path.insert(0, "/home/user/xlm")
S = Path(sys.argv[1])
from quality_fixtures import build_corpus, standard_layout
build_corpus(S / "inp/fixture", standard_layout())
from scripts.c05_authored_pilot import KEY
from scripts.c05_synthetic_flow import KEY_ENV, decide_and_plan, prepare, run_c05
os.environ[KEY_ENV] = KEY
root = S / "inp/c05/root"
paths = prepare(root)
result = run_c05(paths, decide_and_plan(paths))
json.dump({"manifest": str(root / "manifest.json"), "proof": str(result["proof"]), "key_env": KEY_ENV, "key": KEY}, open(S / "inp/c05.json", "w"))
print("ok")
