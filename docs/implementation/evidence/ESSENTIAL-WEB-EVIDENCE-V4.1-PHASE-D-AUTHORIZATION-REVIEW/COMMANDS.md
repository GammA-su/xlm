# Narrow Phase-D authorization review commands

CWD `F:\Project\xlm-data-ultrax`, 2026-09-29. No network or live Phase D.
HEAD `b1e1d10bbfcde78e708ba21409c7a0a765f9f5be`, branch `data/mix01-ultrax-6b`.
No changes to implementation, tests under `tests/`, dependencies or freezes.
Only new review artifacts and a prefix added to already-dirty STATUS.

PowerShell test environment (set before each pytest invocation):

```powershell
$env:OMP_NUM_THREADS='1'
$env:MKL_NUM_THREADS='1'
$env:OPENBLAS_NUM_THREADS='1'
$env:NUMEXPR_NUM_THREADS='1'
$env:TOKENIZERS_PARALLELISM='false'
```

Commands below were executed exactly as shown. Output was captured via
`2>&1 | Tee-Object -FilePath <log>; exit $LASTEXITCODE` for tests and the same
pipeline without `2>&1` for JSON checks. Exit values are the uv process exit,
not Tee-Object's success. These are focused selections, never full acceptance.

```powershell
git status --short
git rev-parse HEAD
git log --oneline -12
git branch --show-current
```

All exit 0. Initial dirty paths are preserved in `git-status-before.txt` and
`user-files-before.json`. `status-before.bin` preserves the exact existing
STATUS bytes for the prefix-preservation assertion.

```powershell
uv run --offline --locked --no-sync --extra cpu --extra eval python docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V4.1-PHASE-D-AUTHORIZATION-REVIEW/review_inventory.py before
uv run --offline --locked --no-sync --extra cpu --extra eval python scripts/evidence_v41_phase_d.py verify
uv run --offline --locked --no-sync --extra cpu --extra eval python docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V4.1-PHASE-D-IMPLEMENTATION/check_real_parent.py
```

Exit 0 each. Inventory before: 89 entries including root, 3,241,704 file bytes;
root absent for Phase D. Logs: `verify.log`, `check-real-parent.log`.

```powershell
uv run --offline --locked --no-sync --extra cpu --extra eval python -m pytest -n 0 -q -p no:cacheprovider tests/test_evidence_v4_plan.py tests/test_evidence_v4_transport.py tests/test_evidence_v4_engine.py tests/test_evidence_v4_e2e.py tests/test_evidence_v4_wire.py tests/test_evidence_v41.py tests/test_evidence_v41_phase_d.py
```

`focused-tests.log`: exit 0, **412 passed in 43.72s**. Same seven-file selection
as implementation COMMANDS.md; its shell brace expansion is written out here.

```powershell
uv run --offline --locked --no-sync --extra cpu --extra eval python -m pytest -n 0 -q -p no:cacheprovider docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V4.1-PHASE-D-AUTHORIZATION-REVIEW/test_review_probes.py
```

`independent-probes-initial.log`: exit 1, **19 passed, 2 failed in 2.67s**.
Initial binding probe mistakenly disallowed the committed dry-plan trailing
newline. Corrected only that reviewer requirement: executable plan bytes remain
strictly canonical; every digest/raw-byte hash still verified. The other failure
was the real parent-drift contract defect, not corrected or suppressed.

The cap measurement was also extended to count files before SQLite closes,
because close removes its journal and final-only size can hide an overrun.

```powershell
uv run --offline --locked --no-sync --extra cpu --extra eval python -m pytest -n 0 -q -p no:cacheprovider docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V4.1-PHASE-D-AUTHORIZATION-REVIEW/test_review_probes.py::test_independent_frozen_hashes_ranges_and_scientific_identity docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V4.1-PHASE-D-AUTHORIZATION-REVIEW/test_review_probes.py::test_root_cap_includes_exports_at_decode_boundary
```

`corrected-focused-probes.log`: exit 1, **1 passed, 1 failed in 2.29s**.
Binding check passes; root cap fails by 16,748 accounted bytes at the measured
pre-close export boundary. Synthetic occupancy simulation, no large real root.

Added structural-only live-footer verification and observation JSONs, then ran
the final review selection once with the existing in-process measurement helper:

```powershell
uv run --offline --locked --no-sync --extra cpu --extra eval python docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V4.1-PHASE-D-IMPLEMENTATION/measure_pytest.py -n 0 -q -p no:cacheprovider docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V4.1-PHASE-D-AUTHORIZATION-REVIEW/test_review_probes.py
```

`independent-probes-final.log`: exit 1, **20 passed, 2 failed in 3.83s**.
Measured runner wall 4.012 s; peak working set 139,292,672 bytes. Failures:
`test_root_cap_includes_exports_at_decode_boundary` and
`test_parent_manifest_drift_during_run_stops`. No skips or xfails.
The two failures remain intentionally visible as review evidence.

```powershell
uv run --offline --locked --no-sync --extra cpu --extra eval python -m pytest -n 0 -q -p no:cacheprovider tests/test_evidence_v3.py tests/test_evidence_v2_core.py tests/test_evidence_v2_text.py
```

`science-regressions.log`: exit 0, **69 passed in 1.40s**. This is the recorded
scientific regression selection, not a reopening of v3 architecture or guarantees.

Final evidence/documentation and preservation checks:

```powershell
uv run --offline --locked --no-sync --extra cpu --extra eval python docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V4.1-PHASE-D-AUTHORIZATION-REVIEW/finalize_review.py
uv run --offline --locked --no-sync --extra cpu --extra eval python docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V4.1-PHASE-D-AUTHORIZATION-REVIEW/review_inventory.py after
git diff --check
git diff --name-only HEAD -- src scripts tests pyproject.toml uv.lock .python-version
git diff --cached --name-only
git rev-parse HEAD
git status --short
```

Final exit 0 each. Initial standalone `git diff --check` returned 1 for CRLFs in the new
STATUS prefix. Only that new prefix was changed to LF; the original STATUS
suffix remained byte-identical. Finalizer/inventory/check were repeated after
that documentation-only correction; tests were not repeated.
`parent-after.json` equals `parent-before.json`; real Phase-D root
absent; every prior user file preserved (STATUS has its intact original suffix).
Production/dependency diff and index are empty. No commit: PASS-only commit
condition was not met. No push. Full acceptance, CUDA, live acquisition, real
column decoding and live performance measurements were NOT RUN. No lint/typecheck
rerun on unchanged implementation. Review outcome: both arms BLOCKED.
