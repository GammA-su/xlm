# Essential-Web recovery scope fix — 2026-09-30

**READY TO RUN ESSENTIAL-WEB BATCH 1**

Batch 1 can use its existing plan and authorization. The Batch-0 recovery is
an exact unit/content exception, not a campaign-wide execution restriction.
No acquisition, external network, redownload, selector, mixture, quota, progress
UI change or push occurred here. Real Batch 1 remains unexecuted.

## Authoritative state

The read-only audit loaded the frozen campaign, both batch records, authorized
plans and operator authorization records; verified receipt and artifact hashes;
and inspected raw, canonical, private staging and scratch paths. It did not
derive state from stdout or process any source rows.

| | Batch 0 | Batch 1 |
|---|---:|---:|
| Planned files | 32 | 32 |
| Sealed units | **32** | **0** |
| Sealed rows | **2,604,815** | **0** |
| Retained source files | 32 | **0** |
| Canonical files | 320 | **0** |
| Private staging files | 0 | **0** |
| Scratch files | 0 | **0; batch scratch directory absent** |
| Performance records | 2 | **0** |
| Event log | present | **absent** |
| Existing authorization | valid | **valid, preserved** |

The failed Batch-1 invocation made **zero network requests, downloads or
processing calls**: the old `cmd_run` raises immediately after reading the
batch record, before `check_admission`, scratch creation, `execute_batch` or
`run_pipeline`. None of that earlier code sends a request. The authoritative
store agrees: only the five Prepare/authorization files exist for Batch 1;
there is no execution artifact or scratch directory. This establishes the
provided failed invocation, not a machine-wide packet-capture claim about
unrelated programs.

The before/after snapshots verify **398 operator files**, totaling
**10,782,777,731 bytes**, are byte-, size- and mtime-identical: all 393 Batch-0
raw/sidecar/canonical/plan/event/performance/authorization files plus the five
Batch-1 plan/authorization files. All 32 Batch-0 receipt/artifact hashes also
validate under the corrected code. The original recovery JSON and digest
`51c09f0b15e6b19ebeb9854c6d65ec1994b473467b555723f0924606574c24db`
remain unchanged.

Exact existing estimated-token accounting after Batch 0 is science
40,167,642.5, practical 97,768,710.5, prose 274,137,810.5. The console rounds
these to the whole-token values supplied in the task; no accounting rule changed.

## Cause and correction

`load_campaign` loads the historical recovery into `Campaign.recovery`.
Previously, `cmd_run` treated its mere presence as requiring `batch == 0`.
`execute_batch` repeated the global restriction. Other call sites also lacked
the full scope: `unit_job` matched only the filename, `seal_unit` tagged every
receipt with the recovery digest, and `verify_unit` required recovery lineage
on unrelated future receipts. Fixing only the first guard would leave later
failures and incorrect lineage.

The common lookup now checks all of:

- campaign identity and **batch 0**;
- inventory rank derived from frozen membership: **26 / f00026**;
- exact file `data/crawl=CC-MAIN-2016-50/train-02164-of-03132.parquet`;
- verified retained SHA-256
  `06dfd85a171f7a1a155fa34493b9f61b3de0ff16b46f0765d29c2631fbf02ad1`;
- source path, repository and revision in the verified identity.

Only this match receives **11,494,172 bytes**. Execution additionally checks
its original batch digest and recovery authorization. A missing or wrong
retained source at that exact scope still refuses. Missing scope arguments,
another unit/path/hash or another batch cannot receive the exception.

All unrelated units use the ordinary **8,388,608-byte (8 MiB)** production
bound and their own batch authorization. Their receipts carry no Batch-0
recovery lineage and do not require its approval. Another oversized future
record still fails closed and needs its own evidence/recovery decision.

Historical Batch-0 receipt protections remain enforced only within their
batch; f00026's receipt must retain its exact digest and effective-bound
lineage. Completed units continue to be skipped. Progress/ETA and permanent
event behavior are preserved; their source files are byte-identical to 5c775cb.

## Identity and authorization

| Identity | Preserved value |
|---|---|
| Campaign | `8e42ba31b0ef9fcbae1079cebf88d2449b88a9b37ec52b68c7a408eb2546bb8c` |
| Batch-1 membership | `fc4a6becbcdd052e3639c03bf67192613ccdb982c821ef728adb04f829e7b139` |
| Batch-1 plan | `8c6b2b1be916ce0113701bf36823cb0d28f638c190c60e2e408b2b94d1e9ad33` |
| Batch-1 authorization | `281042ed09275662d44d67d13b4d731dbd23f5cb0f8064cdbcce15d61f164b25` |

No new Prepare or operator authorization is necessary. No authorized resource
limit, membership or acquisition behavior expands. The original campaign's
three transport-code files did not change in this fix. The driver and recovery
dispatcher do belong to the historical recovery's broader code freeze, so
simply editing them would trip that guard.

An additive [code-compatibility record](../evidence/ESSENTIAL-WEB-RECOVERY-SCOPE/code-compatibility.json)
binds this specific scope repair without rewriting the historical amendment.
Digest `94ab7cdbebea1dc20af2c875d9774d4d5655adf0743cca12379c7ffa4c47fab6`.
It validates its own digest, campaign, original recovery digest, old and new
code maps, and permits differences **only** in the driver and recovery
dispatcher. Any further code drift still refuses. It neither grants resource
authorization nor enables the record exception for another unit. Normal
campaign source/science/benchmark/code checks and C04 admission remain enforced.
The actual stored Batch-1 C04 admission was checked offline and passes.

Offline Batch-1 dry planning now reports **32 scheduled, zero already-sealed
scheduled, recovery digest null**. `network_units=32` describes future work,
not requests made by this dry command. The gate reports **RUN**.

## Evidence, checks and limits

[Before](../evidence/ESSENTIAL-WEB-RECOVERY-SCOPE/before.json),
[after](../evidence/ESSENTIAL-WEB-RECOVERY-SCOPE/after.json),
[dry Batch 1](../evidence/ESSENTIAL-WEB-RECOVERY-SCOPE/batch1-resume.json), and
[exact commands/results](../evidence/ESSENTIAL-WEB-RECOVERY-SCOPE/COMMANDS.md).

Windows 11, Python 3.12.13, the existing uv-locked CPU/eval dependencies.
`pyproject.toml`, `uv.lock`, `.python-version` and the CPU/CUDA installation
policy are unchanged. Initial hash audit: **23.609 s**; after audit:
**23.218 s**. No raw/canonical output was generated; the audit reads bounded
hash chunks, plus small JSON receipts. Peak RSS and physical I/O totals were
not measured. Synthetic test scratch remains separate from the operator store.

Authored fixtures cover exact f00026 matching; wrong unit/path/hash;
Batch 1/2 ordinary bounds; exact 8 MiB and one byte over; scoped approval;
interrupted Batch-1 resume and idempotency; normal future receipts without
recovery lineage; completed Batch-0 immutability; and refusal of unfrozen
code drift. Existing progress/ETA tests are included. Transport regressions
use loopback HTTP only, not live providers.

The requested related selection passed **744 tests, zero skipped, in 288.16 s**
(exit 0). Scoped ruff, format and strict mypy checks all passed (exit 0).
This is a focused regression selection, not the repository's full acceptance.

| Requirement | Status |
|---|---|
| Authoritative Batch-0/1 state and zero-work failed invocation | VERIFIED |
| Exact recovery scope across dispatch, bounds, authorization and receipts | IMPLEMENTED, VERIFIED |
| All Batch-0 artifacts and Batch-1 authorization unchanged | VERIFIED |
| Batch-1 existing plan, authorization and C04 admission valid | VERIFIED |
| Dashboard/ETA preserved | VERIFIED; source unchanged and regressions included |
| Future oversized records remain bounded | VERIFIED with authored fixtures |
| Requested related regressions, ruff/format/strict mypy | VERIFIED: 744 passed; all three static checks exit 0 |
| Real Batch-1 execution, external network, full acceptance, CUDA | NOT RUN |
| New recovery policies, selector/mixture/quota changes | OUT OF SCOPE |

## Next operator command

From `F:/Project/xlm-data-ultrax`:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -Command '. .\scripts\operator_storage.ps1; .\scripts\operator_essential_web_fast.ps1 -Batch 1 -Stage Run'
```

This uses the **existing Batch-1 authorization**. Do not supply the Batch-0
recovery digest or rerun Prepare. This future operator command performs the
32 previously authorized downloads; it was **not executed in this task**.
