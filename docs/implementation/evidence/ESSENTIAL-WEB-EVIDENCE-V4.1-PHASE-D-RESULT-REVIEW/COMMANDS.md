# Post-live result-review command record

Date 2026-09-30; CWD `F:\Project\xlm-data-ultrax`; HEAD
`4ec64b856c2f0c41aa8e7480c276d6d5e57841e8`. Both G: evidence roots read-only.
Prefix `U` below expands exactly to:

```powershell
uv run --offline --locked --no-sync --extra cpu --extra eval
```

`E` below is the repository-relative directory
`docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V4.1-PHASE-D-RESULT-REVIEW`.
These are notation substitutions, not additional shell commands.

| Exact command after substitutions | Exit | Evidence |
|---|---:|---|
| `U python E/inspect_evidence.py` | 0 | `schema-inventory.log`, both `*-before.json` |
| `U python E/review_result.py` (initial) | 1 | `review-run-1.log`; mistaken assumption that REDIRECT error is null |
| Same (second) | 1 | `review-run-2.log`; mistaken assumption that SUCCESS error is empty string |
| Same (corrected schema) | 0 | `review-run-3.log`; reconciled real evidence |
| Same (added file/field allowlists and per-file bindings) | 0 | final audit evidence |
| Same (final formatted code, rejecting transport object and strict zip) | 0 | `review-run-final.log`, `review-result.json`, inventories, bindings |
| `U python E/prepare_analysis.py` | 0 | `prepare-analysis.log`; dry preparation |
| Same, after final review binding changed | 0 | `prepare-analysis-final.log`; final manifests |
| `U python -m pytest -n 0 -q -p no:cacheprovider E/test_review_helpers.py` | 0 | `pytest-review-helpers.log`: 9 passed, 0.11 s |
| Same after final review-script changes | 0 | `pytest-review-helpers-final.log`: 9 passed, 0.17 s |
| `U ruff check E/*.py` (final) | 0 | `ruff-final.log`: clean, four review Python files |
| `U ruff format --check E/*.py` (final) | 0 | `ruff-format-final.log`: four already formatted |

The actual PowerShell capture was
`2>&1 | Tee-Object -FilePath <log>; exit $LASTEXITCODE`; exit values above
refer to the uv process. `PYTHONDONTWRITEBYTECODE=1` was set for final runs.
For both pytest commands, OMP_NUM_THREADS, MKL_NUM_THREADS,
OPENBLAS_NUM_THREADS and NUMEXPR_NUM_THREADS were 1 and
TOKENIZERS_PARALLELISM=false. No xdist workers, skips, xfails or test failures.

Initial review-only ruff checks exited 1 for formatting/imports/one unused import,
strict-zip and exception-chaining style. Applied `ruff check --fix --select I,F401`,
`ruff format`, exact literal wrapping with AST equality, explicit `strict=True`,
`raise ... from None`, and `ruff check --fix --select UP034`; final checks pass.
No production file or rule was changed. Review schema corrections did not change
any evidence or loosen the exact outcome-specific expected values.

Read-only discovery used Get-Content, rg, directory listings and git status/log.
One guessed `ESSENTIAL-WEB-EVIDENCE-V2.0-PROTOCOL.md` path was absent; the actual
`ESSENTIAL-WEB-EVIDENCE-V2-PROTOCOL.md` was located and read. Historical X: and G:
development paths were absent; the F: corrected sweep was located and each byte
hash checked against the frozen manifest, with the relocation explicit.

Final preservation checks: both roots' inventories equal the initial inventories;
pre-existing STATUS bytes remain an exact suffix of the updated file; production
and dependency diff empty; index empty; HEAD unchanged; `git diff --check` exit 0.
Evidence: `final-preservation.json`, `git-final.log`.

No live command, network, acquisition, G: write, policy evaluation, K/ID generation,
human label, final selector decision or push. Full acceptance, production regression
suites, CUDA and mypy were not run for this review-only change.
