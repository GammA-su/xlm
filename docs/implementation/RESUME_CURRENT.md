# Resume — acquisition-performance measurement (2026-09-21)

## Recovery (do not reset)

- Repo actually used: `D:\Project\xlm-final-integration`
- Branch: `closeout/readiness`
- Starting HEAD discovered: `22c781bfb0a734839a55f3439c7376b56dbf628c` ("fix(ifm): planning adapter matches live text/token_count schema")
- `git status --short` at recovery:
  - `M src/xlm/data/acquisition/fetcher.py`
  - `M src/xlm/data/sources/transport.py`
  - `?? src/xlm/data/acquisition/perf.py`
- No committed perf work in log; prior lost-session work was UNCOMMITTED only — preserved, not reset.
- `D:/Project/xlm` (`fix/d02`) is NOT authoritative and was NOT edited.
- Previous `RESUME_CURRENT.md` (CLI-first, HEAD 5527b4a) was stale — that work is already committed (186506a..22c781b).
- No reset/clean/stash/checkout/rebase performed. No copy from `D:\Project\xlm`.

## Task status (measurement first, no optimization, no live network)

- [x] Phase 0 recovery inspection
- [x] Finish instrumentation (selection/parquet/metadata/decompressed/serialize/cache/per-file records)
- [x] Direct CLI `data performance` + `data performance-compare`
- [x] Offline tests (fake clocks, loopback only, 20 passed)
- [x] Runbook + decision tree + STATUS/report
- [ ] Focused verification (final ruff/mypy/related regressions) + scoped commit

## Owned modified files

- `src/xlm/data/acquisition/perf.py` (new: telemetry, sidecar, compare, sanitize, cache class, slowest stage/files, load helper)
- `src/xlm/data/acquisition/fetcher.py` (range bytes, decompressed delta, per-file scanned/retained, parquet split)
- `src/xlm/data/acquisition/selection.py` (JSONL/Parquet body/metadata/decode/serialize, scanned/retained, file_worker, cache)
- `src/xlm/data/acquisition/records.py` (metadata vs decode split, optional perf observer)
- `src/xlm/data/sources/transport.py` (redirect observer, preserved)
- `src/xlm/cli/data_cmd.py` (`data performance`, `data performance-compare`, `_print_perf_summary`)
- `src/xlm/data/acquisition/__init__.py` (perf exports)
- `tests/test_acquisition_performance.py` (new, 20 tests)
- `docs/implementation/P24-ACQUISITION-PERFORMANCE.md` (new runbook + decision tree)
- `docs/implementation/reports/P24.md` (new milestone report)
- `docs/implementation/STATUS.md` (P24 row)
- `docs/implementation/RESUME_CURRENT.md` (this file)

## Tests completed

- Focused (`-n 0`, thread env 1, `TOKENIZERS_PARALLELISM=false`):
  - `tests/test_acquisition_performance.py` — 20 passed.
  - `tests/test_acquisition_plan.py` — 21 passed.
  - `tests/test_acquisition_fetcher.py` — 10 passed.
  - `tests/test_acquisition_bounds.py` subset (transfer, redirect/retry, selected
    locators jsonl+parquet, ignored-parquet, row-group diag x2) — 8 passed.
- Quality: `ruff format --check src tests` exit 0 (289 files);
  `ruff check src tests` exit 0; `mypy src` exit 0 (202 files).
- Full platform audit NOT RUN by policy (final gate only). Remaining bounds tests
  (killed-download, nested, public-plan, etc.) NOT RUN here.
- No live network, no real acquisition, no GPU, no training.

## Evidence paths

- Fresh evidence reserved: `data/audit/perf/` (git-ignored). No historical logs overwritten.
- Sidecars at runtime: `scratch/performance/<plan_id>.perf.json` (attempt-owned, git-ignored).

## Commit outcome (environmental block, work preserved)

- Scoped commit BUILT and verified ("feat(acquisition): bounded performance
  telemetry, direct CLI, worker comparison, runbook"), parent `22c781b`,
  exactly the 12 scoped files above (`git show --stat` verified).
- Parked on backup ref `closeout/perf-measurement-backup` (resolve the exact
  hash at use time with `git rev-parse closeout/perf-measurement-backup`).
- `closeout/readiness` was deliberately NOT moved (still 22c781b); no reset,
  no checkout, no merge, no main change, no push.
- BLOCKED step: fast-forwarding `closeout/readiness` to the commit.
  `git add`/`git commit`/`git update-ref` fail with "unable to write new index
  file" / "couldn't set 'refs/heads/closeout/readiness'". Filesystem probes
  prove the dirs/files are creatable/openable, while renames over the live
  `index` and `refs/heads/closeout/readiness` fail — an external process holds
  open handles on this worktree's live git state (7 OpenCode processes were
  observed; no repo process was killed). Object-store, new-ref, and read paths
  all work. Alt-index path used: `GIT_INDEX_FILE=C:\Users\gamma\AppData\Local\Temp\opencode\xlm-perf-index`
  (removed after use).
- Operator finish (run when the holder releases, e.g. after closing watcher apps):
  ```powershell
  git log --oneline --decorate -2 closeout/perf-measurement-backup
  $new = git rev-parse closeout/perf-measurement-backup
  git update-ref refs/heads/closeout/readiness $new 22c781bfb0a734839a55f3439c7376b56dbf628c
  git log --oneline --decorate -3
  git branch -d closeout/perf-measurement-backup
  ```
  If the fast-forward still fails, keep working from the backup ref (it contains
  the full finished state) and do NOT reset readiness.

Do NOT push, modify main, merge cleaning branch, touch xlm/xlm-cleaning-fix/xlm-operator-code, run live network, real acquisition, GPU, or training.
