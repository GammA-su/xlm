"""Does Astra's rewritten path_digest change the value my cache identity uses?"""
import importlib.util, sys
from pathlib import Path

sys.path.insert(0, str(Path("src").resolve()))
from xlm.prepare.integrity import path_digest as mine

# Load Astra's version with its new dependency on artifacts.manifest helpers.
import xlm.artifacts.manifest as am
astra_manifest = Path(".d0405/work/xcheck/manifest_astra.py")
spec = importlib.util.spec_from_file_location("astra_manifest", astra_manifest)
mod = importlib.util.module_from_spec(spec); sys.modules["astra_manifest"] = mod
spec.loader.exec_module(mod)
for name in ("bounded_children", "ensure_plain_path"):
    setattr(am, name, getattr(mod, name))

spec = importlib.util.spec_from_file_location("astra_integrity", ".d0405/work/xcheck/integrity_astra.py")
ai = importlib.util.module_from_spec(spec); spec.loader.exec_module(ai)

for target in ("fixtures/eval/tasks", "fixtures/eval/inputs/dev_fixture_v1"):
    p = Path(target)
    a, b = mine(p), ai.path_digest(p)
    print(f"{target}\n  baseline={a[:32]}\n  astra   ={b[:32]}\n  IDENTICAL={a == b}")
