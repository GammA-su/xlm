"""Read-only source/provenance audit and final inventory; writes only review evidence."""
import base64
import csv
import hashlib
import importlib.metadata
import json
from pathlib import Path
import subprocess

from parent_inventory import inventory

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]
REPAIR = HERE.with_name('ESSENTIAL-WEB-EVIDENCE-V4.1-PHASE-D-D01-D02-REPAIR')


def git(*args):
    return subprocess.check_output(['git', *args], cwd=REPO)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


head = git('rev-parse', 'HEAD').decode().strip()
assert head == '33339663a2e9e15316c119193c81f38ff2d47652'
assert not git('diff', '--name-only', 'HEAD', '--', 'src', 'scripts', 'tests', 'pyproject.toml', 'uv.lock', '.python-version')
assert not git('diff', '--cached', '--name-only')
frozen_paths = ['docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V4.1-PHASE-D', 'docs/implementation/reports/ESSENTIAL-WEB-EVIDENCE-V4.1-PHASE-D-PROTOCOL.md']
assert not git('diff', '--name-only', 'd586938', head, '--', *frozen_paths)
paths = git('ls-files', 'src/xlm/data/evidence_v4', 'scripts/evidence_v41_phase_d.py', *frozen_paths).decode().splitlines()
bindings = {}
for name in paths:
    raw = (REPO / name).read_bytes()
    assert raw == git('show', f'{head}:{name}'), name
    bindings[name] = sha(raw)
assert (HERE / 'test_astra_probes.py').read_bytes() == (REPAIR / 'test_astra_probes.py').read_bytes()
assert (HERE / 'test_astra_probes.py').read_bytes() == git('show', f'{head}:{REPAIR.relative_to(REPO).as_posix()}/test_astra_probes.py')
distribution = importlib.metadata.distribution('mypy')
assert distribution.version == '2.3.1'
checked = []
for row in csv.reader(distribution.read_text('RECORD').splitlines()):
    name, recorded, size = row
    if name.startswith('mypy/') and name.endswith('.py'):
        raw = distribution.locate_file(name).read_bytes()
        actual = 'sha256=' + base64.urlsafe_b64encode(hashlib.sha256(raw).digest()).rstrip(b'=').decode()
        assert actual == recorded and len(raw) == int(size), name
        checked.append(name)
assert checked
before = json.loads((HERE / 'parent-before.json').read_bytes())
after = inventory()
assert before == after and not after['phase_d_root_exists']
(HERE / 'parent-final.json').write_text(json.dumps(after, indent=1) + '\n', encoding='utf-8')
old_status = (HERE / 'status-before.bin').read_bytes()
assert (REPO / 'docs/implementation/STATUS.md').read_bytes().endswith(old_status)
result = dict(head=head, branch=git('branch', '--show-current').decode().strip(), production_unchanged=True, freeze_unchanged=True, index_empty=True, astra_probe_copy_exact=True, mypy_version=distribution.version, mypy_source_RECORD_files_verified=len(checked), parent_inventory_identical=True, phase_d_root_exists=False, status_original_bytes_preserved=True, source_sha256=bindings)
(HERE / 'final-audit.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
print(json.dumps({k:v for k,v in result.items() if k != 'source_sha256'}, indent=2))
