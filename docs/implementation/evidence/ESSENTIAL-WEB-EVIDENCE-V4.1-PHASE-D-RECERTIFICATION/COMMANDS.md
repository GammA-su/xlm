# Final narrow recertification commands

CWD `F:\Project\xlm-data-ultrax`, 2026-09-30; HEAD `33339663a2e9e15316c119193c81f38ff2d47652`.
All acquisition/decoding tests use authored synthetic fixtures. The real parent
checker and structural footer checks are read-only. No live Phase-D command.

The exact argument arrays, exit codes and elapsed subprocess seconds for every
prescribed command are recorded in [commands.json](commands.json).
[run_review.py](run_review.py) lists the full 23-file static-check scope and
the exact seven-file focused selection, without shell glob/brace ambiguity.
Every recorded command exited 0; the runner itself exited 0.

```powershell
uv run --offline --locked --no-sync --extra cpu --extra eval python docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V4.1-PHASE-D-RECERTIFICATION/run_review.py
```

The runner sets OMP_NUM_THREADS, MKL_NUM_THREADS, OPENBLAS_NUM_THREADS and
NUMEXPR_NUM_THREADS to 1, TOKENIZERS_PARALLELISM=false and
PYTHONDONTWRITEBYTECODE=1 for its children. The additional probe command used
those same environment settings:

```powershell
uv run --offline --locked --no-sync --extra cpu --extra eval python -m pytest -n 0 -q -p no:cacheprovider docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V4.1-PHASE-D-RECERTIFICATION/test_final_gate.py
```

Exit 0: 14 passed in 10.82 s. Output captured with
`2>&1 | Tee-Object -FilePath .../independent-final-gate.log; exit $LASTEXITCODE`.
These probes include loops over every bound artifact of the synthetic fixture
for removal and same-size mutation; no copies of the real parent are mutated.

```powershell
uv run --offline --locked --no-sync --extra cpu --extra eval python docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V4.1-PHASE-D-RECERTIFICATION/final_audit.py
git diff --check
git diff --name-only HEAD -- src scripts tests pyproject.toml uv.lock .python-version
git diff --cached --name-only
git rev-parse HEAD
git status --short
```

Final audit exit 0; rerun after the documentation-only update to verify the
original STATUS byte suffix remains intact and the final real-parent inventory
still equals the starting inventory. No tests repeated for the documentation edit.
Git checks exit 0; production/dependency diff and index empty, HEAD unchanged.
Final output is in `final-audit.log` and `git-final.log`.

Read-only discovery included `Get-Content`, `rg`, `git show`, `git diff`,
`git status` and `uv --version`. One combined discovery command exited 1 because
PowerShell passed the nonexistent literal `docs/INSTALL*` to rg; no validation
depended on that documentation glob. No test command failed. No dependency
installation, compiled-mypy retry, full acceptance, CUDA or live run.
