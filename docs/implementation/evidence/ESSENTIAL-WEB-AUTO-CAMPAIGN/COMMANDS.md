# Exact commands and results — Essential-Web automatic campaign runner

2026-09-30; checkout `F:\Project\xlm-data-ultrax`, branch `data/mix01-ultrax-6b`.
Starting HEAD `5afdd05d1cf326f9232e1616084a3a6625316e0c`.

Environment: Windows 11 Pro 10.0.26200, Windows PowerShell 5.1, Python
3.12.13 from the existing locked CPU/eval environment (`uv run --offline
--locked --no-sync`). `pyproject.toml`, `uv.lock` and `.python-version` are
unchanged; no dependency was added. Operator roots from
`scripts/operator_storage.ps1`: durable `G:\XLM`, scratch `C:\XLM-scratch`.

**No network request, no batch execution, no Prepare/authorize of Batch 3, no
envelope written to the operator store, no GPU work, no push.**

Evidence classes:

- **Real local state, read-only**: the fast campaign (`8e42ba31…bb8c`), its 96
  sealed receipts, the batch records and performance records of batches 0–2,
  the frozen inventory and free space on `G:` and `C:`.
- **Authored fixtures**: every test (authored Parquet rows served by the
  loopback endpoint of `tests/test_essential_web_fast.py`). They prove the
  runner's logic, not live endpoint behaviour.
- **Not run**: `prepare-auto` without `--dry` on the real store (it would write
  the envelope file), `run-auto`, and every network stage.

## Real-store dry validation

`no_network.py` (this directory) replaces `socket.connect`, `connect_ex` and
`create_connection` with a function that counts and raises, then calls
`scripts/essential_web_campaign.py`. The script below snapshots every file under
`G:\XLM\plans\ew-fast`, `G:\XLM\canonical\ew-fast`, `G:\XLM\acq-raw\ew-fast` and
`C:\XLM-scratch\ew-fast` (path, size, mtime) before and after.

```powershell
. .\scripts\operator_storage.ps1
$U = @('run','--offline','--locked','--no-sync','--extra','cpu','--extra','eval')
$G = 'docs/implementation/evidence/ESSENTIAL-WEB-AUTO-CAMPAIGN/no_network.py'
uv @U python $G status --json                                   # -> dry-status.json
uv @U python $G status                                          # -> dry-status.txt
uv @U python $G prepare-auto --max-batches 20 --operator $env:USERNAME --dry --json   # -> dry-prepare.json
uv @U python $G prepare-auto --max-batches 20 --operator $env:USERNAME --dry         # -> dry-prepare.txt
```

| Command | Exit | Seconds | Network attempts |
|---|---:|---:|---:|
| `status --json` | 0 | 0.69 | 0 |
| `status` | 0 | 0.67 | 0 |
| `prepare-auto --dry --json` | 0 | 2.68 | 0 |
| `prepare-auto --dry` | 0 | 2.67 | 0 |
| `prepare-auto --dry --json` (repeat) | 0 | — | 0 |

Store snapshot: **1,196 files before, 1,196 after, 0 differences.** The repeat
produced the identical envelope (`21cbad8ca0de96148b094c14753187145ba0558481a17b4b9ab6a2bd4527ad9e`);
the surrounding report differed only in the live `C:` free-byte reading.

The envelope digest binds the running code, so it is valid for exactly the
committed runner and campaign code; `PrepareAuto` by operator `gamma` with the
default options reproduces it.

Also run (offline, read-only):

```powershell
.\scripts\operator_essential_web_campaign.ps1 -Stage Status    # exit 0
uv @U python scripts/essential_web_fast.py show --batch 3      # exit 0 -> batch3-show.txt
```

`batch3-show.txt` is the unchanged fast driver's own listing of Batch 3:
membership `bb7fd547…4480`, inventory ranks 96–127, the same as the envelope's
first child.

An earlier dry attempt pointed at the no-network wrapper through `TEMP`, which
`operator_storage.ps1` moves to `G:\XLM\temp`; Python could not open the file
and nothing ran. Its snapshot then reported 110 differing directory entries.
The snapshot at that time also listed directories; no process was writing
(newest store file 20:56:36, the end of Batch 2), and a repeat with a 2 s pause
showed 0 differences. The final script compares files only.

## Tests

New module, serial:

```powershell
uv @U python -m pytest tests/test_essential_web_campaign_runner.py -n 0 -q -p no:cacheprovider --basetemp .bt-auto
```

Exit **0**, **26 passed**, 0 skipped. The eight restart, interrupt and failure
tests passed three consecutive repeats (`-k "ctrl_c or execution_failures or
oversized or nonzero or runs_from"`: 8 passed each time).

Related regression selection on the final code (`regressions.log`): the
Essential-Web runner, fast, recovery, recovery-scope, Windows-publication,
calibration, bulk, readiness, selector, fast-track, sweep, bootstrap and live
certification suites, plus source admission, Mix-01 inventory, quotas and views,
acquisition plan, verifier, bounds, fetcher and leases, HF range transport,
production ingest, row-group sampling, adapt rejections, exclusion receipt and
benchmark, Parquet window, selected-record concurrency and calibration adoption.

```powershell
uv @U python -m pytest <selection> -n 8 --dist=worksteal --max-worker-restart=0 -m "not serial" -q -p no:cacheprovider --basetemp .ba
uv @U python -m pytest <selection> -n 0 -m serial -q -p no:cacheprovider --basetemp .bb
```

- Parallel: exit **0**, **786 passed**, 57.39 s.
- Serial: exit **0**, **3 passed**.
- **0 skipped.** The PowerShell parse test ran.

An earlier run of the same selection, with the longer basetemp `.bt-auto-x`,
failed two `test_acquisition_bounds` fetch tests with `[Errno 2]`. That is the
known Windows path-length failure: the journal path was about 270 characters.
Both passed with a short basetemp (`.b2`, 15.9 s) and inside the final parallel
run.

Thread settings for every test run: `OMP/MKL/OPENBLAS/NUMEXPR_NUM_THREADS=1`,
`TOKENIZERS_PARALLELISM=false`. The fast and full repository selections and
CUDA tests were **not run**; this is a focused selection, not a full-suite pass.

## Static checks

```powershell
$F = @('scripts/essential_web_campaign.py','src/xlm/data/sources/essential_web_campaign_runner.py','tests/test_essential_web_campaign_runner.py','tests/test_essential_web_fast.py','docs/implementation/evidence/ESSENTIAL-WEB-AUTO-CAMPAIGN/no_network.py')
uv @U ruff check @F; uv @U ruff format --check @F; uv @U mypy --strict @F
```

Ruff: all checks passed. Format: 5 files already formatted. Strict mypy
(compiled; it loaded normally this time): no issues in 5 files, plus the note
about the unused `lm_eval` override.

## Unchanged production code

`git diff` is empty for `scripts/essential_web_fast.py`,
`scripts/operator_essential_web_fast.ps1`, the fast campaign JSON, and the fast,
local, monitor, progress and recovery modules. It is also empty for
`source_parquet.py` and the adapters. The recovery code-compatibility chain,
which hashes those files, therefore still loads the real campaign (`status`
above).
