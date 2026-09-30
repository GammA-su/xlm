"""Bounded, offline recertification command recorder; no live Phase-D command."""
from pathlib import Path
import json
import os
import subprocess
import time

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]
U = ['uv', 'run', '--offline', '--locked', '--no-sync', '--extra', 'cpu', '--extra', 'eval']
ENV = dict(os.environ, OMP_NUM_THREADS='1', MKL_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1', NUMEXPR_NUM_THREADS='1', TOKENIZERS_PARALLELISM='false', PYTHONDONTWRITEBYTECODE='1')
TESTS = [f'tests/test_evidence_v4_{s}.py' for s in ('plan', 'transport', 'engine', 'e2e', 'wire')] + ['tests/test_evidence_v41.py', 'tests/test_evidence_v41_phase_d.py']
FILES = sorted(p.as_posix() for p in Path('src/xlm/data/evidence_v4').glob('*.py')) + ['scripts/evidence_v4.py', 'scripts/evidence_v41.py', 'scripts/evidence_v41_phase_d.py', 'tests/evidence_v4_support.py', 'tests/evidence_v41_phase_d_support.py'] + TESTS
assert len(FILES) == 23 and all(Path(p).is_file() for p in FILES)
PYTEST = ['python', '-m', 'pytest', '-n', '0', '-q', '-p', 'no:cacheprovider']
REPAIR = 'docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V4.1-PHASE-D-D01-D02-REPAIR'
commands = [
 ('parent-before', ['python', str(HERE / 'parent_inventory.py'), 'before']),
 ('environment', ['python', '-c', 'import sys,platform,sqlite3,importlib.metadata as m,tomllib; from pathlib import Path; lock=tomllib.loads(Path("uv.lock").read_text()); p=next(p for p in lock["package"] if p["name"]=="mypy"); assert p["version"]==m.version("mypy")=="2.3.1"; print(sys.version); print(platform.platform()); print("SQLite",sqlite3.sqlite_version); print({n:m.version(n) for n in ["pyarrow","mypy","ruff","pytest"]}); print("locked mypy",p["version"]); print("Python pin",Path(".python-version").read_text())']),
 ('cli-verify', ['python', 'scripts/evidence_v41_phase_d.py', 'verify']),
 ('pytest-v4-v41-phase-d', PYTEST + TESTS),
 ('pytest-d01-d02', PYTEST + ['tests/test_evidence_v41_phase_d.py', '-k', 'test_d01 or test_d02']),
 ('pytest-science', PYTEST + ['tests/test_evidence_v3.py', 'tests/test_evidence_v2_core.py', 'tests/test_evidence_v2_text.py']),
 ('astra-probes', PYTEST + [str(HERE / 'test_astra_probes.py')]),
 ('ruff-check', ['ruff', 'check'] + FILES),
 ('ruff-format', ['ruff', 'format', '--check'] + FILES),
 ('mypy-version', ['python', REPAIR + '/mypy_interpreted.py', '--version']),
 ('mypy-strict', ['python', REPAIR + '/mypy_interpreted.py', '--strict', '--no-incremental'] + FILES),
 ('check-real-parent', ['python', 'docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V4.1-PHASE-D-IMPLEMENTATION/check_real_parent.py']),
 ('cli-status', ['python', 'scripts/evidence_v41_phase_d.py', 'phase-d-status']),
 ('parent-after', ['python', str(HERE / 'parent_inventory.py'), 'after']),
 ('parent-compare', ['python', str(HERE / 'parent_inventory.py'), 'compare', 'before', 'after']),
]
records = []
for name, args in commands:
    start = time.monotonic()
    result = subprocess.run(U + args, cwd=REPO, env=ENV, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=300)
    (HERE / (name + '.log')).write_bytes(result.stdout)
    records.append(dict(name=name, argv=U + args, exit=result.returncode, seconds=round(time.monotonic()-start, 3)))
    (HERE / 'commands.json').write_text(json.dumps(records, indent=2) + '\n', encoding='utf-8')
    print(name, 'exit', result.returncode, result.stdout.decode('utf-8', errors='replace')[-1400:], flush=True)
raise SystemExit(int(any(r['exit'] for r in records)))
