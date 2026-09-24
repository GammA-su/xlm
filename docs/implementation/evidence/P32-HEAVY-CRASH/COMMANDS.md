# P32 heavy-crash commands and environment

Every command runs in `G:\Project\xlm-p32-heavy-crash`. Initial branch/HEAD/status/
worktree checks exited 0: clean `fix/p32-heavy-worker-crash`, base `fa4ff50`.
The parent recovery worktree and original committed evidence were read-only.

```powershell
git branch --show-current
git rev-parse HEAD
git status --short
git worktree list
```

The existing parent `.venv` was copied with
`robocopy /E /COPY:DAT /DCOPY:DAT /R:0 /W:0` into this checkout (46,951 files,
1.008 GiB reported, zero failures). Only the copied `_editable_impl_xlm.pth`
was adjusted. No install, dependency resolution or lock change occurred.

`scripts/p32_heavy.ps1` guards the branch and supplies:

```powershell
uv run --offline --locked --no-sync python <arguments>
```

The wrapper sets worktree-local environment/cache/home/TEMP paths, offline HF/uv
flags, `PYTHONDONTWRITEBYTECODE=1`, `PYTHONFAULTHANDLER=1`, OMP/MKL/OPENBLAS/
NUMEXPR threads=1 and `TOKENIZERS_PARALLELISM=false`. Python is 3.12.13, with the
same locked CPU/evaluation environment as P32 recovery. No external socket is
authorized; ordinary offline fixtures enforce this. Authored localhost fixtures
and bounded toy acceptance jobs are separate from live acquisition/research.

## Original native evidence

Read Application events 1000/1001 for 2026-09-24 18:15–18:30. Copied only the
matching `python.exe.36048.dmp` from the existing Windows CrashDumps directory
into ignored artifacts. No debugger installation, registry change, symbol
download or crash-tool configuration was performed. `inspect_dump.py.gz`
preserves the exact local binary-reader source used to inspect minidump streams
and the matching PE export/unwind tables. Its run command was:

```powershell
& scripts/p32_heavy.ps1 artifacts/p32-heavy-crash/inspect_dump.py
```

The minidump's SHA-256 is in results.json; process-memory bytes stay uncommitted.
Extracted original logs are additional copies; the committed failed P32 gate
under `P32-RECOVERY` is unchanged. Stack-address scans are not presented as a
symbolized debugger unwind.

## Controlled before-fix experiments

```powershell
& scripts/p32_heavy.ps1 scripts/p32_heavy_runs.py before
& scripts/p32_heavy.ps1 artifacts/p32-heavy-crash/reproduce_watchdog.py
```

The first command declares one exact-node serial run and one exact-node run
under four-worker loadgroup. The group has only that node, so this also covers
the entire domain. Both use `-X dev`, fatal handling and the original 120-second
native timeout. Each passes once. A separate parent checkout run is unnecessary:
both begin on unchanged base source before the repair is loaded.

The second command declares exactly three stdlib-only children with a 15 s busy
path loop, 1 ms repeated native dumps and 25 s child timeout. Every child dies
with 3221225477 (`0xc0000005`); the controller exits 0 because this experiment
records the crashes rather than masking their exit codes. Its exact source and
all three logs/results are preserved. This accelerated race reproducer is not
a claim about failure frequency at normal timeout intervals.

## Control, repair and focused tests

```powershell
& scripts/p32_heavy.ps1 scripts/p32_heavy_probe.py control
& scripts/p32_heavy.ps1 scripts/p32_heavy_probe.py safe
& scripts/p32_heavy.ps1 -m pytest tests/test_thread_diagnostics.py -n 0 -q -rs --basetemp artifacts/p32-heavy-crash/pytest-diagnostic-unit
& scripts/p32_heavy.ps1 -m pytest tests/test_thread_diagnostics.py tests/test_runtime_fixture.py -n 0 -q -rs --basetemp artifacts/p32-heavy-crash/pytest-focused -p pytest_evidence --evidence-json artifacts/p32-heavy-crash/focused.json
& scripts/p32_heavy.ps1 -m pytest tests/test_thread_diagnostics.py -n 0 -q --basetemp artifacts/p32-heavy-crash/pytest-final-unit
```

Control and safe modes each declare three 15 s trials with 25 s child limits;
all six children exit 0. Safe mode emits 135/137/133 dumps. Initial unit selection:
6 passed / 1.86 s; focused selection after error-propagation regression added:
15 passed / 58.93 s; final cancellation cleanup unit selection: 7 passed / 1.87 s.
All pytest exits 0. The focused set retains actual runtime-inventory equivalence
and same-size/mtime-preserving synthetic mutation tests.

## Predeclared stress and release sequence

```powershell
& scripts/p32_heavy.ps1 scripts/p32_heavy_runs.py stress
& scripts/p32_heavy.ps1 scripts/p32_heavy_runs.py heavy
& scripts/p32_heavy.ps1 scripts/p32_heavy_runs.py gate
```

Stress declares five exact-node/group repetitions, each with a fresh root,
four workers, `--dist=loadgroup --max-worker-restart=0`. Heavy requires all five
successful results. The full gate requires a green heavy leg plus focused/probe
evidence. No full gate runs during diagnosis. These validation modes use normal
Python mode with `PYTHONFAULTHANDLER=1`; dev mode was used for diagnosis/probes.

Every selection has `-q -rs --strict-markers --durations=20`,
`-o faulthandler_timeout=120`, private `--basetemp`, and the pytest evidence
plugin. Each run has an external 1,800 s / 24 GiB sampled process-tree watchdog.
One xdist controller runs at a time. Exact argv, exits, wall time, sampled RSS,
node phases and collections are in the raw evidence. Missing or inconsistent
worker collections fail validation; no existing output is overwritten.

The full gate reuses the unchanged P32 six selectors and worker counts:

| Leg | Selection | Workers / scheduler |
|---|---|---|
| Tier A | `not serial and not performance and not scale and not cuda and not network and not operator and not optional_dependency and not environment_setup` | 16 / worksteal |
| Core | `serial_core and not serial_exclusive` | 4 / loadgroup |
| Exclusive | `serial_core and serial_exclusive` | 0 |
| Heavy | `serial_heavy` | 4 / loadgroup |
| Scale | `scale and not network and not cuda and not operator and not environment_setup` | 0 |
| Optional | `optional_dependency and not network and not cuda and not operator and not environment_setup` | 4 / loadgroup |

## Static checks and packaging

```powershell
$changed = @(git diff --name-only fa4ff50 -- '*.py') + @(git ls-files --others --exclude-standard -- '*.py')
$changed = @($changed | Sort-Object -Unique)
& scripts/p32_heavy.ps1 -m ruff format --check @changed
& scripts/p32_heavy.ps1 -m ruff check @changed
& scripts/p32_heavy.ps1 -m mypy @changed --follow-imports=silent --cache-dir artifacts/p32-heavy-crash/mypy-cache
& scripts/p32_heavy.ps1 scripts/p32_heavy_evidence.py
git diff --check
```

Development checks caught type/import-access and formatting issues;
these were corrected before final static validation. Original native failures
and before-fix passes remain in the evidence. Packaging validates gate-node
coverage/disjointness and reports actual acceptance status; a successful
packaging command does not itself turn a failed gate into a pass.

Final results: all five stress runs exit 0; standalone heavy exits 0 with seven
passes. The full gate runs once and all six legs exit 0: 1,787 passed / two
capability skips / zero failures across 1,789 distinct nodes. Normalized node
coverage is complete and disjoint. All six changed Python modules pass final
Ruff format/check and mypy (each exit 0). Evidence packaging exits 0 and records
`full_gate_passed: true`. `storage-final.json.gz` records end-of-run free space,
not a transient peak disk measurement.
