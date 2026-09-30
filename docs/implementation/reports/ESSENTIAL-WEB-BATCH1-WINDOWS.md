# Essential-Web Batch-1 Windows publication failure — 2026-09-30

**READY TO RESUME ESSENTIAL-WEB BATCH 1**

Batch 1 stopped at 10/32 on a live-progress race, not on a source, transport or
science fault. The fix coordinates the progress snapshot between the worker and
the monitor and reports the root failure apart from its cancellations. No
network, redownload, selector, mixture or quota change, and no push, occurred
here. The existing Batch-1 plan and authorization stay valid.

## Authoritative Batch-1 state

A read-only audit ([`before.json`](../evidence/ESSENTIAL-WEB-BATCH1-WINDOWS/before.json))
was taken before any code edit. It verifies receipts and artifact hashes, and
rehashes retained sources. It checks each scratch checkpoint against its fsynced
prefix hash and lists staging by name, size and mtime only. No row was decoded.

| | Units | Detail |
|---|---:|---|
| Sealed | **10 / 32 (31.25%)** | f00032, f00036–f00039, f00041–f00043, f00045, f00047; 848,755 rows |
| Failed local processing, ready from durable raw | 1 | f00035: durable raw and complete scratch copy both rehash to `64ebbf1a…4000` |
| Resumable partial | 5 | f00033, f00034, f00040, f00044, f00046: prefixes verify; 872,415,232 bytes kept |
| Must download (bound, 0 verified bytes) | 3 | f00048, f00049, f00050 |
| Untouched | 13 | f00051–f00063 |

No canonical unit exists without a receipt, and there are no stray promote
temporaries. The single performance record (`performance-00.json`) shows 10
sealed, 0 retries, 20 requests, and 8 download and 12 process workers.

## Root failure

- **Unit:** f00035, `data/crawl=CC-MAIN-2021-04/train-00049-of-03315.parquet`.
- **Exception:** `PermissionError`, errno 13, **WinError 5** (access denied).
- **Operation:** `os.replace(.staging/b0001/f00035.progress.tmp →
  .staging/b0001/f00035.progress.json)` on G:.
  - **Where it ran:** `adapt_source_file.progress()`,
    `essential_web_local.py:208` at `97acd08`.
  - **Process:** a process-pool worker (`process_unit` → `adapt_source_file` →
    `progress`).
  - **When:** during local processing, after row 36,403 of 78,477.
- **Destination:** it existed (the snapshot from 20:12:55.923).
- **Writer's handle:** the writer had closed its own `.tmp` handle before the
  replace.
- **The blocking handle:** the parent `Monitor.update` opens each unit's
  progress file once per second (`progress.read_bytes()`,
  `essential_web_monitor.py:130` at `97acd08`). On Windows,
  `MoveFileEx(REPLACE_EXISTING)` fails with WinError 5 while any other handle
  on the target is open.

**Not implicated:**
- the scratch download: f00035 completed at 20:12:30 and verified;
- the durable copy: promoted at 20:12:31–32;
- Parquet reads, canonical output, the ledger, zstd, hashing, the unit rename,
  unlink and the receipt.

**Forensic basis.** f00035 is the only unit whose staging still holds
`f00035.progress.tmp`, written at 20:12:56.922, next to the older
`progress.json` from 20:12:55.923. Its document writers stopped at
20:12:56.93–.95. The run started at about 20:12:19.4, and the `failed` event
came at +37 s. The event is keyed to f00035, so it came from that worker's
future, not from the monitor.

**What was not captured.** The event log stored only the exception type, and the
stderr text was not captured. So WinError 5 is reconstructed from this signature
and from the reproduction below, not read from the original message.

**Antivirus and indexing.** Microsoft Defender real-time protection is enabled,
so antivirus involvement is *possible* but has no evidence. The project's own
reader alone reproduces the failure at a high rate.

## Offline reproduction

The [probes](../evidence/ESSENTIAL-WEB-BATCH1-WINDOWS/replace-probe.json) ran on
NTFS C: and G:, in a temporary directory only (not operator data).

- **Replace while a reader holds the target:** `os.replace` raises
  `PermissionError` errno 13 / WinError 5, and the `.tmp` is left behind. This
  is the exact staging signature. It happens both with Python's normal open and
  with a `FILE_SHARE_DELETE` reader, so a share-mode change alone cannot fix it.
- **Replace after the reader closes:** it succeeds.
- **Replace storm with a `read_bytes()` loop running:**
  - 3,000 replaces failed 1,893 times on C: and 1,706 times on G:, always
    WinError 5.
  - The reader itself also raised `OSError` thousands of times.
- **Replace storm with a `stat()`/`is_file()` loop running:** 0 of 3,000
  replaces failed. Python 3.12 on this Windows build stats without holding a
  handle, so the monitor's size scans are harmless.

## Why it happened and the fix

The live-progress feature added in `5c775cb` made two processes share one
filename with no coordination. The worker replaced the file once per second, and
the monitor opened it once per second. A telemetry write therefore aborted
scientific processing. Batch 1 was the first long run with 12 processing
workers.

The change is in `essential_web_local.py` and `essential_web_monitor.py`:

- **Progress snapshot:** the worker's `os.replace` (`publish_progress`) and the
  monitor's open/read (`read_progress`) now each hold a per-unit OS file lock,
  `fNNNNN.progress.lock` (filelock, preserved, non-blocking). A replace
  therefore never meets an open reader. This closes the handle before the
  replace deterministically.
- **When the lock is taken:**
  - The worker skips that snapshot and retries it on the next row, with no
    sleep and no retry loop.
  - The monitor keeps showing the previous value.
- **What stays the same:** the replace is still atomic. Telemetry never changes
  documents, ledgers, summaries or receipts, and tests prove byte-identical
  outputs.
- **Same flaw in download state:** the monitor also read each stream's
  `state.json` while a download thread replaced it at every checkpoint. It no
  longer opens the state file of any stream started in this run.
  `ObservedScratch` records reservations, which happen before a stream starts,
  and the declared length from `shrink`. So the ETA keeps its length data.
  Unstarted units have no writer and are still read.
- **Other handles audited:** no other publication or cleanup race was found.
  - The unit directory rename and the scratch unlink run in the main thread,
    one after another with the monitor; 42 of 42 sealed units cleaned up.
  - The worker's `ParquetFile` is released before `process_unit` returns;
    tested by unlinking the source immediately.
  - Promote temporaries and sidecars have no concurrent reader.

**What did not change:**
- Retries: none were added, so no bounded retry needed testing.
- Concurrency: still 8 download and 12 process workers.
- C: scratch → G: durable architecture.
- Atomic write-once publication, SHA verification, restart semantics.
- A foreign unsynchronized handle still fails closed; this is tested.

## Cancellation cascade

`TransferCancelledError` is the expected cooperative stop.
- **How it is triggered:** only `fail()` sets `cancel`. Each stream raises the
  error at its next read.
- **The eight cancelled streams:** f00033, f00034, f00040, f00044, f00046,
  f00048, f00049 and f00050. They were cancelled 0–3 s after the root failure,
  and their checkpoints are all valid.
- **Processing already in flight:** it continued. Ten units were sealed between
  00:00:57 and 00:01:38, as designed.

The behavior is kept. Only the reporting changes. A cancellation after a root
failure is now a `cancelled` event, not a failure. The root carries structured,
code-authored facts:
- errno and WinError (the WinError is recovered from the remote traceback,
  because pickling drops it);
- own file basenames;
- the innermost project frame.

Exception text is still never logged.

On a fatal stop, the dashboard prints a summary block, and a permanent `fatal`
event records the same facts. For the original failure's state, the block would
read:

```text
ROOT FAILURE
  f00035 PermissionError errno=13 winerror=5
  at essential_web_local.py:135 in publish_progress (worker process)
  operation: replace f00035.progress.tmp -> f00035.progress.json
  access denied: the target was held open by another handle
CANCELLED
  8 in-flight units cancelled because of the root failure: f00048, f00033, ...
PRESERVED
  10 / 32 sealed (31.2%); 10 sealed in this run
RESTART
  22 remaining
  1 reusable locally (retained source or complete scratch)
  5 resumable (872,415,232 verified bytes kept)
  16 require a fresh download
```

The live ERRORS line now shows `failed units=… cancelled=…`. The other
progress and ETA lines are unchanged.

## Restart plan (dry, verified)

`resume-check --batch 1` ([batch1-resume.json](../evidence/ESSENTIAL-WEB-BATCH1-WINDOWS/batch1-resume.json))
uses the executor's own scheduling. The independent audit agrees unit for unit.

| Class | Count | Network |
|---|---:|---|
| sealed_skip | 10 | none |
| local_complete_reuse | 1 (f00035 durable raw) | none |
| partial_resume | 5 | Range/If-Range for 479,342,399 remaining bytes |
| fresh_download | 16 | 3 of known length (789,817,227 B) + 13 not yet known |

- **Network bytes:** 1,269,159,626 known. The worst case is 8,248,481,482 bytes
  (13 × the 512 MiB per-file bound).
- **Transfer ceiling:** 4,331,467,791 bytes are already charged against the
  25,769,803,776-byte ceiling.
- **Requests:** about 2 per remaining network file.
- **Scheduling:** no sealed unit is scheduled. Gate: RUN. Stored C04 admission:
  valid (offline).
- **Staging:** the restart's guarded staging reset removes the failed staging
  directory, which holds f00035's partial documents. Its forensic listing is
  preserved in `before.json`.

## Identity and authorization

| Identity | Value | Changed? |
|---|---|---|
| Campaign | `8e42ba31…46bb8c` | no |
| Batch-1 membership | `fc4a6bec…e7b139` | no |
| Batch-1 plan | `8c6b2b1b…9ad33` | no |
| Batch-1 authorization | `281042ed…164b25` | no |
| Batch-0 recovery | `51c09f0b…74c24db` | no |
| Scope-fix record | `94ab7cdb…47fab6` | no |
| Windows-fix record (new, additive) | `1a63736c47c9bc516b406fa81465b7784e96b6fce467c452db2591f8d558c0d9` | new |

**Why a new record is needed.** The campaign loads changed code only through
the recovery amendment's compatibility chain.

**What the chain now does.** `compatible_code` walks ordered, digest-bound
records:
- Each record must continue exactly from the previous one's code and change
  only its own files.
- The new record changes the driver, recovery dispatcher, local, monitor and
  progress modules.
- `source_parquet.py` and `columns.py` are unchanged.
- Nothing earlier is rewritten.

**What the record does not do.** It grants no resource, membership or
authorization change. Neither does anything else in this fix. So no new Prepare
and no operator approval is required.

## Evidence and checks

- **Environment:** Windows 11 (build 26200), Python 3.12.13, filelock 4.0.0,
  PyArrow 25.0.1, and the existing uv-locked CPU/eval environment.
  Dependencies are unchanged.
- **Commands:** exact commands and exit statuses are in
  [COMMANDS.md](../evidence/ESSENTIAL-WEB-BATCH1-WINDOWS/COMMANDS.md).
- **Immutability:** `after.json` compares 555 operator files
  (15,931,411,643 bytes) with `before.json`. All are unchanged in SHA-256, size
  and mtime. They cover Batch 0 and Batch 1: plans, events, canonical outputs,
  raw files and sidecars, scratch and staging.
- **New tests:** 19 new tests, 18 of them on real NTFS Windows semantics, none
  skipped. They cover:
  - the root case and the exact worker-side report;
  - a 2,000-snapshot cross-process stress run with 0 errors;
  - deferral with byte-identical outputs;
  - the monitor state-read rule;
  - cancellation versus root;
  - the end-to-end fail, resume and idempotent rerun;
  - the 22-unit restart classes, including corrupted checkpoints;
  - the compatibility chain.
- **Regression selection:** it spans the fast campaign and dashboard, recovery
  and scope, batch resume, acquisition, source_parquet and the selectors.
  **763 passed in 298.29 s, 0 skipped** (exit 0).
- **Static checks:** ruff, format and strict mypy on the eight changed Python
  files all exit 0. This is not full acceptance.

| Requirement | Status |
|---|---|
| Authoritative Batch-1 state, 10/32 | VERIFIED |
| Exact failing operation (WinError reconstructed, message not persisted) | VERIFIED by forensics and reproduction |
| Handle-coordinated publication, no sleeps or retries | IMPLEMENTED, VERIFIED |
| Requested regressions, ruff/format/strict mypy | VERIFIED: 763 passed, 0 skipped; all exit 0 |
| Download-state read race | IMPLEMENTED, VERIFIED |
| Root versus cancellation reporting, fatal summary | IMPLEMENTED, VERIFIED |
| Sealed units preserved and skipped | VERIFIED |
| Local, partial and fresh restart classes | VERIFIED (dry, offline) |
| Campaign and authorization identity preserved | VERIFIED |
| Real Batch-1 resume, live network, full acceptance, CUDA | NOT RUN |
| Selector, mixture and quota changes | OUT OF SCOPE |

## Next operator command

From `F:/Project/xlm-data-ultrax`:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -Command '. .\scripts\operator_storage.ps1; .\scripts\operator_essential_web_fast.ps1 -Batch 1 -Stage Run'
```

Use `-Stage Run`, not `Resume`, because 21 units need network. Do not pass
`-Authorize` or the recovery digest, and do not rerun Prepare. This was **not
executed** in this task.
