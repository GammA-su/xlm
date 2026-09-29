# Essential-Web evidence v4.0 — implementation commands and results

Date 2026-09-29. Branch `data/mix01-ultrax-6b`. Starting HEAD
`60ed59c5078114dd54297b8923c45495014f7874`; protocol/freeze commit
`2ede38f21d4b9d9ba45989eec812213ce9851bee`.

Environment: Windows 11 10.0.26200 (AMD64), CPython 3.12.13 (project `.venv`),
uv 0.12.19, PyArrow 25.0.1, torch 2.14.0+cpu installed (not imported by v4).
Test-run settings: `OMP_NUM_THREADS=MKL_NUM_THREADS=OPENBLAS_NUM_THREADS=NUMEXPR_NUM_THREADS=1`,
`TOKENIZERS_PARALLELISM=false`, `-p no:cacheprovider` (the repository
`.pytest_cache` directory is not writable in this checkout).

All tests are OFFLINE with authored SYNTHETIC fixtures and disposable temporary
roots. Sockets are monkeypatched to refuse in every engine/E2E test; the
crash subprocess disables `socket.create_connection`. No network, no live
acquisition, no corpus text, no real v4 root (`G:\Project` does not exist
after the run), no Phase D, no push.

| # | Command | Exit | Result / artifact |
|---|---|---|---|
| 1 | `uv run --offline --locked python docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V4.0/build_v4_freeze.py plan` then `... freeze` (commit 1, rerun byte-identically) | 0 | freeze artifacts; see commit `2ede38f` |
| 2 | `uv run --offline --locked python -m pytest -n 0 -q -p no:cacheprovider tests/test_evidence_v4_plan.py tests/test_evidence_v4_transport.py tests/test_evidence_v4_engine.py tests/test_evidence_v4_e2e.py --junitxml=.../v4-focused.xml` | 0 | 157 passed, 0 failed, 0 skipped — `v4-focused.txt`, `v4-focused.xml` |
| 3 | same selection via `pytest.main` with psutil | 0 | 14.86 s wall, peak working set 187,428,864 bytes — `v4-focused-resources.txt` |
| 4 | `uv run --offline --locked python -m pytest -n 0 -q -p no:cacheprovider` on 9 v3 identity/lineage nodes + `tests/test_evidence_v2_core.py tests/test_evidence_v2_text.py` | 0 | 63 passed — `science-regressions.txt` |
| 5 | `uv run --offline --locked ruff check <v4 src, cli, tests>` | 0 | `ruff-check.txt` |
| 6 | `uv run --offline --locked ruff format --check <same>` | 0 | `ruff-format.txt` |
| 7 | `uv run --offline --locked mypy <same 13 files>` (strict project config) | 0 | `mypy.txt` |
| 8 | `uv run --offline --locked python scripts/evidence_v4.py verify` | 0 | `cli-verify.json` |
| 9 | `uv run --offline --locked python scripts/evidence_v4.py show-plan` | 0 | `cli-show-plan.txt` (40 operations) |
| 10 | `uv run --offline --locked python scripts/evidence_v4.py phase-p-status` | 0 | `cli-phase-p-status.json` (NOT_STARTED, root absent) |
| 11 | `uv run --offline --locked python scripts/evidence_v4.py phase-p --confirm-plan-digest 000…000` | 1 | refused before any state; `cli-wrong-digest.txt` |
| 12 | regression-sensitivity check: `test_crash_before_temp_creation_is_reconciled_across_restarts` with the reconciliation fix temporarily removed | 1 (expected) | test fails without the fix; fix restored and the test passes |

NOT RUN: the live `phase-p` command (forbidden in this task); the full
offline acceptance suite (not an acceptance gate here); unrelated generic
acquisition suites (v4 imports none of that code — its only non-v4 import
is `xlm.data.evidence_v2.canonical`); CUDA tests (irrelevant).
