# Batch-1 Windows publication fix: offline execution evidence

- **Date:** 2026-09-30.
- **Repository:** `F:/Project/xlm-data-ultrax`, branch `data/mix01-ultrax-6b`,
  starting commit `97acd086a250fe257029fc527660d41a80a80e40`.
- **Environment:**
  - Windows 11 build 26200; C:, F: and G: are NTFS.
  - Python 3.12.13, filelock 4.0.0, PyArrow 25.0.1.
  - The existing uv-locked CPU/eval environment.
- **Not done:** no dependency installation, external network, production run,
  redownload or push.
- **Test transport:** authored loopback HTTP fixtures only.

All commands ran from the repository root.

## Authoritative state, before any code edit

```bash
uv run --offline --locked --no-sync --extra cpu --extra eval python scripts/essential_web_batch1_audit.py --output docs/implementation/evidence/ESSENTIAL-WEB-BATCH1-WINDOWS/before.json
```

Exit **0**, 32.6 s. The audit reports:
- 10/32 sealed (31.25%), 848,755 rows;
- 1 READY_FROM_DURABLE_RAW (f00035);
- 5 RESUMABLE_PARTIAL, all prefix hashes verified;
- 3 MUST_DOWNLOAD;
- 13 UNTOUCHED.

It also records the staging forensic listing, the text-free progress snapshots
and the event log.

Observed but not decoded:
- the event log: failed f00035 PermissionError at +37 s, then 8
  TransferCancelledError events at +37 to +40 s;
- `performance-00.json`;
- scratch state files;
- the Batch-1 raw directory: 11 sources with sidecars and no temporaries.

## Offline reproduction (temporary directories only)

```bash
uv run --offline --locked --no-sync python docs/implementation/evidence/ESSENTIAL-WEB-BATCH1-WINDOWS/replace-probe.py <scratchpad> G:/XLM/temp > docs/implementation/evidence/ESSENTIAL-WEB-BATCH1-WINDOWS/replace-probe.json
uv run --offline --locked --no-sync python docs/implementation/evidence/ESSENTIAL-WEB-BATCH1-WINDOWS/replace-storm-probe.py <scratchpad> G:/XLM/temp > docs/implementation/evidence/ESSENTIAL-WEB-BATCH1-WINDOWS/replace-storm-probe.json
```

Both exit **0**. Each probe creates and removes its own `race-probe*`
directory, and `G:/XLM/temp` was left as it was found.

- **Replace onto a target that a reader holds:** `PermissionError` errno 13 /
  WinError 5, with the `.tmp` left behind. This happens both for a Python-open
  reader and for a `FILE_SHARE_DELETE` reader. The replace succeeds after the
  reader closes.
- **Storm against a `read_bytes()` loop:** 1,893 of 3,000 replaces failed on C:
  and 1,706 of 3,000 on G:, all WinError 5.
- **Storm against a `stat()` loop:** 0 of 3,000 failed on either volume.
- The Defender real-time state was read with `Get-MpComputerStatus`; it is
  enabled. No antivirus evidence was found.

## Fix, code freeze and dry restart

```powershell
uv run --offline --locked --no-sync --extra cpu --extra eval python scripts/essential_web_windows_freeze.py
uv run --offline --locked --no-sync --extra cpu --extra eval python scripts/essential_web_fast.py --data-root G:/XLM --scratch-root C:/XLM-scratch resume-check --batch 1 --output docs/implementation/evidence/ESSENTIAL-WEB-BATCH1-WINDOWS/batch1-resume.json
uv run --offline --locked --no-sync --extra cpu --extra eval python scripts/essential_web_fast.py --data-root G:/XLM --scratch-root C:/XLM-scratch gate --batch 1
```

All three exit **0**.
- **Freeze digest:**
  `1a63736c47c9bc516b406fa81465b7784e96b6fce467c452db2591f8d558c0d9`.
  Its `previous_code` equals the scope-fix record's `code`.
- **Resume check** (4.9 s):
  - 32 total, 10 sealed, 22 scheduled, `already_sealed_scheduled=0`,
    `recovery_digest=null`;
  - restart classes: sealed_skip 10, local_complete_reuse 1, partial_resume 5,
    fresh_download 16;
  - 872,415,232 resumable verified bytes; 1,269,159,626 known network bytes;
    13 files of unknown length;
  - 4,331,467,791 bytes charged against the 25,769,803,776-byte ceiling.
- **Gate:** `RUN`.

`network_units=21` describes future work. This dry command made no request.

Stored Batch-1 C04 admission, exit **0**, output
`Batch-1 current stored C04 admission: valid (offline)`:

```powershell
$env:XLM_HOME='G:\XLM\xlm-home'; $env:HF_HUB_OFFLINE='1'; $env:HF_DATASETS_OFFLINE='1'
@'
import sys
from pathlib import Path
sys.path.insert(0, 'scripts')
import essential_web_fast as driver
from xlm.data.acquisition.plan import load_acquisition_plan
plan = load_acquisition_plan(Path('G:/XLM/plans/ew-fast/b0001/batch.plan.json'))
driver.check_admission(plan)
print('Batch-1 current stored C04 admission: valid (offline)')
'@ | uv run --offline --locked --no-sync --extra cpu --extra eval python -
```

## Immutability after the fix

```bash
uv run --offline --locked --no-sync --extra cpu --extra eval python scripts/essential_web_batch1_audit.py --output docs/implementation/evidence/ESSENTIAL-WEB-BATCH1-WINDOWS/after.json --compare docs/implementation/evidence/ESSENTIAL-WEB-BATCH1-WINDOWS/before.json
```

Exit **0**, 19.4 s. `changed_since_compare=[]` and
`all_operator_artifacts_unchanged=true` hold for 555 files and 15,931,411,643
bytes, compared by SHA-256, size and mtime. They cover:
- Batch-0 and Batch-1 plan directories, including the authorization, plan,
  events and performance records;
- all canonical units and receipts;
- raw sources and sidecars;
- Batch-1 scratch partials and states;
- the failed staging directory.

The unit classes are identical to the before audit.

## Tests and static checks

Focused feedback on the new module (exit **0**, 18 passed in 9.89 s, 0 skipped):

```powershell
uv run --offline --locked --no-sync --extra cpu --extra eval python -m pytest tests/test_essential_web_windows_publication.py -n 0 -q -p no:cacheprovider --basetemp .bw1 --tb=short -k "not committed_windows_fix"
```

The first run found the worker-side `winerror` missing. Pickling drops it when
`filename2` is set. `failure_detail` now recovers only the `[WinError N]` number
from the remote traceback. No assertion was weakened.

Requested regression selection, run by one serial pytest controller with one
thread per numerical library:

```powershell
$env:OMP_NUM_THREADS='1'; $env:MKL_NUM_THREADS='1'; $env:OPENBLAS_NUM_THREADS='1'
$env:NUMEXPR_NUM_THREADS='1'; $env:TOKENIZERS_PARALLELISM='false'
$checks='essential_web_windows_publication essential_web_recovery_scope essential_web_recovery essential_web_fast essential_web_calibration essential_web_bulk essential_web_readiness essential_web_production_selector essential_web_fasttrack_freeze essential_web_selector_sweep essential_web_bootstrap essential_web_live_certification source_admission mix01_inventory mix01_quotas_6b mix01_views acquisition_plan acquisition_verifier acquisition_bounds acquisition_fetcher acquisition_leases hf_range_transport production_ingest rowgroup_sampling adapt_rejections exclusion_receipt exclusion_benchmark parquet_window_nested parquet_window_sampling selected_record_concurrency calibration_adopt'.Split(' ')
$testPaths=@($checks | ForEach-Object { "tests/test_$_.py" })
uv run --offline --locked --no-sync --extra cpu --extra eval python -m pytest @testPaths -n 0 -q -p no:cacheprovider --basetemp .bw2 --tb=short -rs *> docs/implementation/evidence/ESSENTIAL-WEB-BATCH1-WINDOWS/regressions.log
```

Exit **0**: **763 passed in 298.29 s**, with no test skipped (`-rs` listed
none). This is the previous 744-test selection plus the 19 new Windows cases,
including the committed-record binding test. See `regressions.log`. It is a
focused regression selection, not full repository acceptance.

Static checks on the eight changed or new Python files, each exit **0**:

```powershell
$files=@('scripts/essential_web_fast.py','scripts/essential_web_batch1_audit.py','scripts/essential_web_windows_freeze.py','src/xlm/data/sources/essential_web_local.py','src/xlm/data/sources/essential_web_monitor.py','src/xlm/data/sources/essential_web_progress.py','src/xlm/data/sources/essential_web_recovery.py','tests/test_essential_web_windows_publication.py')
uv run --offline --locked --no-sync --extra cpu --extra eval ruff check @files
uv run --offline --locked --no-sync --extra cpu --extra eval ruff format --check @files
uv run --offline --locked --no-sync --extra cpu --extra eval mypy --strict @files
git diff --check
```

Ruff reported all checks passed and eight files already formatted. Strict mypy
found no issues in the eight files; it also printed the existing unused
`lm_eval` override note.
- The first static pass found one unused test import and one `int(Any | None)`.
- The second was fixed by requiring a bound length for a verified prefix, which
  the transport always has.

## Not run

- **Not run:** a real Batch-1 resume, external provider checks, full
  repository acceptance, CUDA and the research campaign.
- **Out of scope:** selector, mixture and quota changes.
- **No retry added:** no bounded retry exists to test.
- **Resource use:** audit hashing reads files in bounded chunks. Peak RSS and
  physical disk I/O were not measured.
