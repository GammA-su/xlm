# Exact local commands and exit statuses

Working directory for every command below:
`G:\Project\xlm-p35-readiness-astra`. Shell: Windows PowerShell, no login profile.
`R` below denotes `& docs/implementation/evidence/P35-READINESS-ASTRA/env.ps1`.
That checked-in wrapper executes **uv run --offline --locked --no-sync --extra cuda
--extra eval**, using the existing
`G:\Project\xlm-p35-m3\.venv-p35-m3` environment, target-worktree src/tests on
PYTHONPATH, bytecode disabled, offline Hugging Face flags, one OMP/MKL/OpenBLAS/
NumExpr thread, TOKENIZERS_PARALLELISM=false, strict cuBLAS workspace and target-local
TEMP/TMP/uv cache. No installation or sync occurred. All training/data were authored.

Each pytest command additionally used
`--junitxml=docs/implementation/evidence/P35-READINESS-ASTRA/<name>.xml` and
`*> docs/implementation/evidence/P35-READINESS-ASTRA/<name>.log`.
The artifact name is the first column below. Exit statuses are native statuses,
not inferred from a skip. XML contains per-node outcomes/timing.
At closeout, PowerShell UTF-16 diagnostics were converted to UTF-8 and trailing
whitespace in diagnostic lines/XML traceback text was removed for readable Git
diffs. Test outcomes, assertions, timings and numeric evidence were not changed.

| Artifact | Exit | Result |
|---|---:|---|
| readiness | 0 | 112 passed, all 17 readiness runtime nodes executed |
| affected | 0 | 232 passed |
| comparison | 1 | 252 passed, 1 documented P17 CRLF golden-byte failure |
| serial | 0 | 1 passed, 24 deselected; initial CUDA toy flow |
| workflow | 0 | 3 passed on repaired product source |
| defects-before | 1 | 4 intended regressions fail on starting product code |
| adversarial-first | 1 | 27 passed; one authored test used wrong exception superclass |
| repair-regressions | 0 | 87 passed after repairs and precise exception correction |

```powershell
nvidia-smi
R python -m pytest tests/test_p35_readiness_runtime.py tests/test_p35_readiness_recoverability.py tests/test_p35_readiness_planner.py tests/test_p35_readiness_payload.py tests/test_p35_readiness_m4.py -n 0 -rA
R python -m pytest tests/test_p35_science_pilot.py tests/test_p35_checkpoint_cadence.py tests/test_p35_checkpoint_retention.py tests/test_p35_checkpoint_rescore.py tests/test_p35_eval_training.py tests/test_p35_eval_cadence.py tests/test_p35_science.py tests/test_checkpoint.py tests/test_p34_commit_boundary.py tests/test_prefetch.py tests/test_prefetch_training.py tests/test_trainer_mixture.py tests/test_p35_m5_runtime.py -m 'not serial' -n 8 --dist=worksteal --max-worker-restart=0
R python -m pytest tests/test_p35_m4_stats.py tests/test_p35_m4_manifest.py tests/test_p35_m4_eligibility.py tests/test_p35_m4_promotion.py tests/test_p35_m4_evidence.py tests/test_p35_m5_evidence.py tests/test_comparison.py -n 0
R python -m pytest tests/test_p35_m3_toy_flow.py tests/test_trainer_mixture.py tests/test_prefetch.py -m serial -n 0
R python -m pytest tests/test_p35_science_workflow.py -n 0
R python -m pytest tests/test_p35_astra_boundaries.py -n 0
R python -m pytest tests/test_p35_astra_boundaries.py docs/implementation/evidence/P35-READINESS-ASTRA/test_adversarial.py -n 0 -rA
R python -m pytest tests/test_p35_astra_boundaries.py tests/test_p35_wall_allowance.py tests/test_p35_readiness_planner.py tests/test_p35_science_pilot.py docs/implementation/evidence/P35-READINESS-ASTRA/test_adversarial.py -n 0 -rA
```

The serial selection was repeated after repairs with artifact name
`serial-after-repair`; its final result is in the report appendix and summary.json.
There was one xdist controller only, with eight workers. Heavy GPU selections were
sequential. The fresh-process CPU receipt script overlapped only the planner portion
of the focused group. No full repository acceptance/audit was substituted for these
requested and directly affected selections.

Other exact commands:

```powershell
R python docs/implementation/evidence/P35-READINESS/synthetic_flow.py .astra-scratch/synthetic_flow_local.json
R python docs/implementation/evidence/P35-READINESS/synthetic_flow.py docs/implementation/evidence/P35-READINESS-ASTRA/synthetic-flow.json
R python docs/implementation/evidence/P35-READINESS-ASTRA/compatibility.py docs/implementation/evidence/P35-READINESS-ASTRA/environment.json
R python docs/implementation/evidence/P35-READINESS-ASTRA/receipt_resume.py .astra-scratch/receipt-resume docs/implementation/evidence/P35-READINESS-ASTRA/receipt-resume.json
nvidia-smi --query-gpu=name,driver_version,memory.used,memory.free,utilization.gpu --format=csv
R python docs/implementation/evidence/P35-READINESS-ASTRA/receipt_spot.py .astra-scratch/receipt-spot docs/implementation/evidence/P35-READINESS-ASTRA/receipt-spot.json
R python docs/implementation/evidence/P35-READINESS-ASTRA/summarize.py
```

Synthetic flow first exits 1 (`resource` unavailable); this was repeated once only
to preserve complete native stderr after correcting the PowerShell logging wrapper.
The portability repair then exits 0. Compatibility, fresh-process resume and receipt
spot each exit 0. The spot writes 98,304 runtime bytes plus its small JSON/log; 163,840
targets including warmup, 6.781 seconds in the experiment body. Fresh-process resume
trains 106 targets total, each subprocess has a 60-second timeout. Neither is a pilot.

Static scope and exact commands (all Python changed since certified M5 plus new
review tests/scripts; mypy uses changed source modules):

```powershell
$astraFiles = @(git diff --name-only 8ccb4bc764ae50861e7d6872f71a2efc545484a0 -- '*.py') + @('tests/test_p35_astra_boundaries.py') + @(Get-ChildItem docs/implementation/evidence/P35-READINESS-ASTRA -Filter '*.py' | ForEach-Object FullName)
R python -m ruff check @astraFiles
R python -m ruff format --check @astraFiles
$astraFiles = @(git diff --name-only 8ccb4bc764ae50861e7d6872f71a2efc545484a0 -- 'src/*.py')
R python -m mypy --follow-imports=silent @astraFiles
git -c core.whitespace=cr-at-eol diff --check
git diff 8ccb4bc764ae50861e7d6872f71a2efc545484a0 HEAD -- pyproject.toml uv.lock .python-version
```

Initial ruff checks reported formatting/import issues in the new evidence scripts;
formatting was corrected. `ruff format` also normalized mixed working-tree endings
in edited files to their existing local style. queue.py is CRLF in the Git index
itself, so the whitespace check explicitly recognizes CR at end of line; no trailing
spaces are ignored. The P17 failed test was neither changed nor suppressed.
Compatibility checks compare raw Git blobs, worktree bytes and CRLF normalization;
they also verify the unchanged public export list/order and object identities.

No network, live-data, OCR, downloads, dependency installation, full 32M pilot,
microbatch/mixture study, research campaign, push or merge was run. No margins selected.
