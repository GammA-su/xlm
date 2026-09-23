# P30 test-suite evidence

All measured inputs were authored offline fixtures. No live compatibility or GPU
timings are represented. See [the report](../../reports/P30.md) for interpretation,
commands, failed acceptance, and the distinction between routine and final gates.

- `inventory.json`: original/current counts, markers, added IDs, no missing IDs,
  and unchanged original assertion expressions in edited test functions.
- `test-ledger.tsv`: every current node, original membership, tier, reason and each
  run's result. `NOT RUN` means that run did not execute that node.
- `modules.tsv`: all original/current test-module counts and tier distribution.
- `runs.json`: commands, exit codes, timing, RSS, selection agreement and hashes.
- `acceptance.json`: every required node accounted for, original leg exit statuses,
  latest applicable outcomes and unresolved failures. The initial failed serial
  run remains failed even though its crash-cleanup case was subsequently repaired.
- `source-audit.json`: pre-change AST call/constant audit; indirect runtime/process
  work was additionally traced manually. It is not a complete dynamic call graph.
- `top30-*.md`: measured long tails, gate and execution constraint. The final table
  also includes the bounded performance sample. Setup and call are distinct phases.
- `environment.json`: measured host and interpreter context.
- `raw/`: gzip copies of collected inventories, full phase results and logs.
  `raw/manifest.json` binds compressed and original bytes with SHA-256.

JSON is UTF-8 (some initial shell-written metadata has a BOM). PowerShell logs
are UTF-16 with a BOM. Raw paths identify this worktree, its owned pytest temporary
fixtures, and the reused installed environment. No other session's result files
were read. Faulthandler's `Timeout` stack dumps are diagnostics, not killed tests.

From the repository root, independently verify the recorded node union with the
existing environment and no dependency installation:

```powershell
@'
import gzip
import json
from pathlib import Path
root = Path('docs/implementation/evidence/P30/raw')
def nodes(name):
    payload = gzip.decompress((root / name).read_bytes())
    return {item['nodeid'] for item in json.loads(payload)['collected']}
before = nodes('before-inventory.json.gz')
after = nodes('completion-inventory.json.gz')
assert len(before) == 1667 and len(after) == 1673
assert before <= after
print('No missing originals; added:', *sorted(after - before), sep='\n')
'@ | uv run --offline --locked --no-sync python -
```

The worker sweep used the same 1,519-node selection in all four trials. The plugin
checks identical worker collections and the reduction checks complete reported
node coverage. RSS is sampled process-tree RSS, not a guaranteed peak or GPU use.
`pytest_elapsed_seconds` is pytest's complete reported elapsed time; plugin
`wall_seconds` excludes startup before configuration and final teardown.

The final aggregate is **BLOCKED**, not passed: two preparation/reuse regressions
remain and the CPU compile parity capability was skipped because `cl` is absent.
The full timing-only matrix and hardware/network/environment-construction gates
were not run. No retry-until-green, xfail, skipped failure, reduced large fixture,
or removed original assertion was used to obtain the fast-gate timing.
