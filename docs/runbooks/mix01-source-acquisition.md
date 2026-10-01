# Mix-01 source acquisition (non-Essential sources)

Operator guide for `scripts/mix01_source.py`: certified-evidence admission,
transport-policy selection, deterministic production plans and the
high-throughput whole-file engine. Essential-Web keeps its own frozen driver
(`scripts/essential_web_fast.py`); nothing here touches it.

Every subcommand is offline except `run` and `benchmark run`. Roots come from
`scripts/operator_storage.ps1` (`XLM_DATA_ROOT=G:\XLM`, `XLM_HOME=G:\XLM\xlm-home`,
`XLM_SCRATCH_ROOT=C:\XLM-scratch`). The whole-file transport is its own
host-allowlisted HTTPS client; the `HF_*_OFFLINE` variables do not gate it, but
runbooks still set them to `0` only around network steps so the state is explicit.

## Source keys

| Key | Source / view | Adapter | Calibration evidence |
|---|---|---|---|
| `ultrax` | `ultrax_ultrafineweb` / `UltraX-Ultra-FineWeb` | `ultrax_ultrafineweb` | schema-probe receipt + 30 rows, 1,000-row fetch |
| `finepdfs` | `finepdfs_edu` / `eng_Latn` | `finepdfs_en` | 1,000-row fetch |
| `synth` | `synth` / `default` | `synth_en` | 1,000-row window fetch |
| `wiki_rewrite` | `nemotron_specialized` / `Nemotron-Pretraining-Wiki-Rewrite` | `wiki_rewrite` | 1,000-row window fetch |
| `finewiki` | `finewiki` / `en` | `finewiki_en` | 1,000-row fetch |
| `ifm_general`, `ifm_planning` | `ifm_behaviors` / `general`, `planning` | `ifm_general`, `ifm_planning` | 698 / 1,903-row fetches |
| `simple_stories` | `simple_stories` / `default` | `simple_stories` | 1,000-row fetch |

`common_pile` is refused (license/provenance and component allowlist unresolved).
IFM policy/planning refuses until the operator records an explicit per-view
requirement split (the 50/50 split is an assumption, not a decision).

## 1. Admission evidence without a re-probe

`evidence show|publish|verify` re-derives C04 evidence from immutable real
artifacts: the catalog/registry pin, the certified rows (the adapter is re-run
and must reproduce any recorded documents digest), the schema with its named
basis, real file identities from fetch journals and the declared license from
the earlier real metadata probe. `publish` writes the evidence and a
self-digested bridge receipt as the next probe attempt; earlier attempts stay.
Inputs inside the code checkout (authored fixtures) are refused.

## 2. Review and admission

`review show` prints the known facts, the unknowns and the decision fields.
`review record` writes four write-once review files from the operator's explicit
decisions (`--license-decision approve_research_pretraining|reject`,
`--provenance-decision approved|rejected`, `--benchmark-risk
suspect_with_mitigation`, `--operator`, `--rationale`). `admit` binds their
SHA-256 and the bridge receipt into a `c04-benchmark-risk-v3` decision; the gate
re-evaluates it. The software makes no legal determination.

## 3. Transport policy (`mix01-transport-policy-v1`)

Modes: `whole_file_local` (whole verified upstream files, local processing,
upstream file retained), `row_group_local` (whole transfer, planned rows only),
`range_selected` (projected HTTP ranges, `xlm data fetch`), `small_source_direct`
(the whole small source in one batch).

Selection: among modes that fit the request, scratch and durable ceilings and
that a production planner can execute, choose the smallest modeled wall time;
within 10% prefer fewer requests. There is no fixed byte-amplification
threshold: extra bytes are charged at the modeled bandwidth and against the
disk ceilings. The fastest modeled mode is always reported, executable or not.

Wall-time model: network `max(bytes / aggregate rate, waves * per-file time)`
with one stream per file in waves of `streams`; local modes pipeline transfer
and processing with one process per file; the range path fetches, then adapts
in one process. Inputs are `modeled` (named Essential-Web measurements on the
same endpoints plus this source's calibration latency) or `measured` (bounded
benchmark receipts, both modes at the same concurrency). `policy freeze` writes
`G:\XLM\plans\<key>\transport-policy.json`; every plan binds its digest.

## 4. Benchmarks (bounded, before freezing a measured policy)

`benchmark plan|authorize|run` runs the whole-file mode on the
benchmark-reserved inventory tail (the last two ranks, never planned for
production) or on a named calibration file; outputs stay on scratch and are
removed after the receipt. The range mode is measured with a pilot-scope
`xlm data sample-blocks` / `plan --pilot-approved` / `fetch` / `adapt` of the
same files, normalized by `benchmark record-range`.

`benchmark adopt --label <target> --donor <donor>` verifies and hard-links a
complete pinned donor download. Existing source bytes need no second download,
but still count toward the scratch budget. The budget covers the entire source
scratch root, including donor files, state, leftovers and processing output;
it currently counts each hard-link path conservatively. The HF offline
variables alone do not prohibit the transport client's network requests.

Failed benchmark workspaces can be preserved outside the active scratch root
with `source_archive.archive_benchmark(roots, label, absolute_destination,
max_bytes=explicit_bound)`. This requires explicit operator authorization for
the move. It records a durable intent and verified archive receipt beside the
benchmark records, uses a same-volume rename, and retains every file. Adoption
resolves that receipt and verifies archived files; an archived benchmark cannot
run or receive adoption into its former workspace. A pending archive intent
requires finishing the same archival call before donor reuse. Do not leave
junctions or manually relocate a receipted archive.

FinePDFs b1 is now at `C:\XLM-scratch-history\finepdfs\bench-b1`, verified by
`G:\XLM\plans\finepdfs\benchmarks\b1\scratch-archive.json`. b2's source and
authorization remain valid; b2 stays failed capacity history. Active usage is
2,771,022,236 B. Do not retry b2 or run the range half.

New local-processing plans freeze `processing_growth` version 1 in their
benchmark and acquisition identity. The parent verifies source state/hash before
reserving new growth, then reserves all worker outputs before submission.
Serialized documents, compressed rejection ledger, summary and final metadata
share an enforced output pool; canonical text has its separate unchanged cap.
State/progress and atomic publication overlap are reserved and bounded before
write. Benchmark and production share worker and final result enforcement.
Historical unsealed plans without that contract need a new plan; sealed plans
and their identities remain verifiable.

FinePDFs **b3 is planned only**, awaiting operator digest review:

- Digest: `aa539f079104c7c764266a285354ec0b79462d2a13390b7301e841876a5e1f35`.
- Plan hash: `55d9a94fc105fec2281fb5bb38475e7252234f8cd29aa0b138149db362eabeab`.
- Scratch cap: **19,038,848,034 B** = retained b2 `2,771,022,236`
  + source maximum `5,542,772,736` + processing pool `10,721,899,142`
  + atomic metadata `1,048,576` + two progress snapshots `8,192`
  + two source states `2,097,152`. No rounding.
- Ledger up to `536,870,912` B, summary/receipt each up to `1,048,576` B,
  and serialized documents share the pool. With verified source length `s`,
  the output pool may use `16,264,671,878-s`; the combined ceiling is unchanged.
- Fresh and verified-reuse cases fit this same envelope. Existing hard-linked
  paths still count. Preserve **34,359,738,368 B** physical free space after all
  outstanding growth. G: reserves another **17,825,792 B** for run metadata.

See the complete [byte audit and validation report](../implementation/reports/FINEPDFS-PROCESSING-GROWTH.md).
Next command is review only:

```powershell
Get-Content -LiteralPath 'G:\XLM\plans\finepdfs\benchmarks\b3\benchmark.json'
```

Future commands, **only after operator review and explicit authorization**:

```powershell
$env:UV_OFFLINE='1'
$env:HF_HUB_OFFLINE='1'
$env:HF_DATASETS_OFFLINE='1'
$env:TRANSFORMERS_OFFLINE='1'
$env:XLM_DATA_ROOT='G:\XLM'
$env:XLM_HOME='G:\XLM\xlm-home'
$env:XLM_SCRATCH_ROOT='C:\XLM-scratch'
$env:PYTHONUTF8='1'
uv run --offline --locked --no-sync --extra cpu --extra eval python scripts/mix01_source.py benchmark authorize --source-key finepdfs --label b3 --digest aa539f079104c7c764266a285354ec0b79462d2a13390b7301e841876a5e1f35 --operator '<operator-name>'
uv run --offline --locked --no-sync --extra cpu --extra eval python scripts/mix01_source.py benchmark adopt --source-key finepdfs --label b3 --donor b2
uv run --offline --locked --no-sync --extra cpu --extra eval python scripts/mix01_source.py benchmark run --source-key finepdfs --label b3 --offline
```

Run b3 with **network OFF** after verified adoption. The benchmark's explicit
`--offline` refuses any input requiring network; it rechecks complete inputs
and prevents transfer fallback if reuse changes. Environment variables alone
are insufficient. None of these future commands was executed during b3 planning.

## 5. Plans, authorization, runs

`plan` writes the next write-once plan: the next contiguous ranks of the frozen
inventory, whole files, every resource ceiling derived by `mix01-source-planner-v1`,
and the bound admission, policy, quota, estimate and calibration digests. It
prints `PLAN DIGEST` and stops. `authorize --plan N --digest D --operator O`
records the operator's authorization of exactly that digest. `run --plan N`
(network) executes it: concurrent resumable streams to scratch, independent
SHA-256 verification, durable promotion, local adaptation in worker processes,
per-file receipts and atomic publication. A second `run` resumes; sealed units
are never redone.

Restart classes (`resume-check`): `sealed_skip`, `local_processing_retry`
(verified durable source, processing redone), `local_complete_reuse` (complete
verified scratch file), `resumable_partial` (verified prefix kept),
`fresh_download`. A failure prints ROOT FAILURE, CANCELLED BECAUSE OF ROOT
FAILURE, PRESERVED WORK and RESTART CLASSIFICATION and writes a failed
performance receipt; partial and complete downloads are kept.

`sufficiency` compares receipt-derived canonical bytes with the first-pass
requirement: `SUFFICIENT`, `TOP_UP` (run `plan` again: a new plan at the next
cursor) or `INCOMPLETE` (resume or repair, never top up). A pass with any
unsealed planned rank is `INCOMPLETE` even when its bytes suffice: no unit is
dropped silently. `seal` writes the write-once first-pass seal of the source.

### Repairing a unit that fails under its plan's own limits

An authorized plan is never edited. When a unit fails closed under one of the
plan's limits (for example `RecordLimitError`), a retry fails the same way.
`plan-repair --plan N` (offline) writes the next plan for exactly the unsealed
ranks of plan N, under the planner's current limits. It needs N's failed
performance receipt naming an unsealed unit and at least one changed per-unit
limit (otherwise: resume). It keeps N's `next_cursor`, binds N's digest and
accounting, the failed receipts, the changed limits and the SHA-256 of every
retained verified source (in the plan hash), prints `PLAN DIGEST` and stops.
After `authorize` and `run --plan N+1 --offline`, retained sources are
processed with no transfer. Plan N can no longer run; its sealed units stay
valid; `sufficiency` and `seal` bind each rank once, through the plan that
sealed it (`repair_of`, `repaired_ranks`).

Record bounds: generic and UltraX 8 MiB; FinePDFs `finepdfs-record-v2` 48 MiB
(whole-file plans only; the 32 MiB parser bound limits Thrift metadata, not a
row). First use: [FinePDFs p01 recovery](../implementation/reports/FINEPDFS-P01-RECORD-LIMIT-RECOVERY.md).

### Selected file sizes and superseding an unauthorized plan

Every selected file's exact frozen-inventory size fits its plan's
`max_file_bytes`. When the largest selected size exceeds the estimate-derived
ceiling (calibration file size x 2), it becomes the per-file sizing anchor:
`max_file_bytes` is that size rounded up to a whole MiB, rows and canonical
bytes scale with it, and every derived ceiling follows. The plan records this
as `file_size_anchor` (absent, and digests unchanged, when files fit).
`authorize` also refuses a stored plan whose own file bound excludes a known
selected size: such a plan can only fail closed.

A latest plan that was never authorized or run (only its `plan.json` exists,
with no units, staging or scratch) is superseded, not edited:
`plan-supersede --plan N` (offline) writes plan N+1 from N's cursor under
today's frozen inputs, binds N's digest and the changed limits, prints
`PLAN DIGEST` and stops. It refuses when nothing changed (authorize N
instead) and for repair plans. Plan N stays byte-identical and can no longer be
authorized or run; `sufficiency` lists `supersessions`, and the seal binds N as
`superseded_by` without units. An authorized or failed plan is repaired, never
superseded. First use: [IFM production bound recovery](../implementation/reports/IFM-PRODUCTION-BOUND-RECOVERY.md).

### Intra-file row-group parallelism

A view listed in `source_plan.SOURCE_ROW_GROUP_PARALLEL` gets a
`row_group_parallel` limit in its plan (part of the plan digest). Currently
only `finepdfs_edu/eng_Latn` is listed: 4 workers, lookahead 4, 15 slots,
6 GiB per file. Each file process then coordinates its own pool of row-group
workers. They read the same verified local Parquet file, write nothing, and
the coordinator replays their rows strictly in file order through the
unchanged bounds. Documents, ledger and summary are byte-identical to serial
processing, and so is the first error raised.

- `plan` derives `process_workers <= slots // (workers + 1)`. `run
  --process-workers` may lower it, but `process_workers x (workers + 1)` may
  never exceed the slots.
- The sampled process-tree memory ceiling per file is checked every 0.25 s;
  exceeding it fails the unit with `ProcessingMemoryError`.
- A worker crash fails the unit with `RowGroupError`. Nothing is published,
  and a rerun redoes the unit locally (`local_processing_retry`).
- Views without an entry, and their existing plans (UltraX p01), are unchanged.

Evidence: [FinePDFs intra-file report](../implementation/reports/FINEPDFS-INTRAFILE-PARALLEL.md).

## Files

`G:\XLM\plans\<key>\` (plans, authorizations, policy, benchmarks, performance
receipts, events), `G:\XLM\acq-raw\<key>\source\` (verified upstream Parquet +
identity sidecars), `G:\XLM\canonical\<key>\pNN\fRRRRR\` (documents, zstd
rejection ledger, summary, receipt), `C:\XLM-scratch\<key>\` (partials only).
