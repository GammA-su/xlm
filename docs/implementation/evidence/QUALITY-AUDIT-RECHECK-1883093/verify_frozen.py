from pathlib import Path
import hashlib
import json
import subprocess

root=Path('F:/Project/xlm-quality-audit')
prefixes=['docs/implementation/reports/QUALITY-AUDIT-INDEPENDENT-96F38F3.md','docs/implementation/evidence/QUALITY-AUDIT-INDEPENDENT-96F38F3']
def git(*args):
    return subprocess.check_output(['git',*args],cwd=root)
paths=git('ls-tree','-r','--name-only','824b23e','--',*prefixes).decode().splitlines()
rows=[]
for path in paths:
    old=git('show','824b23e:'+path)
    current=git('show','HEAD:'+path)
    disk=(root/path).read_bytes()
    rows.append({'path':path,'git_blob_identical':old==current,'raw_disk_identical':old==disk,
                 'disk_sha256':hashlib.sha256(disk).hexdigest(),
                 'old_blob_sha256':hashlib.sha256(old).hexdigest()})
assert all(r['git_blob_identical'] for r in rows)
assert all(r['raw_disk_identical'] for r in rows)
Path('F:/qa188-review/frozen-hashes.json').write_text(json.dumps(rows,indent=1),encoding='utf-8')
print(json.dumps({'files':len(rows),'all_git_blobs_identical':True,'all_raw_worktree_bytes_identical':True}))
