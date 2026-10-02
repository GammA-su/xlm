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
| `common_pile` | `common_pile` / `common_pile_prose` | `common_pile` v2 | all six components; two pinned files each |

Common Pile has a frozen Balanced allowlist and production inventory, adapter
v2 and `.jsonl.gz` transport. Production still refuses until component
calibration, reviewed bounds, explicit A/B/C allocation, live metadata,
license/provenance review and admission pass. The six components are
libretexts, news, oercommons, pressbooks, project_gutenberg and
public_domain_review. Do not re-record or replace the existing allowlist.
Its digest is `b2bb7c0dc532a6263ca17b78816186fa194c12c09c74614baaeb29c76cdaab04`;
inventory digest is `c0984aa33fb3598a9e724b9518df8cd8a0f2af52b5708ec211fb7fd693df7637`.

The operator has completed the twelve samples and pinned metadata probe.
**Do not refetch them.** The [real-calibration handoff](../implementation/reports/COMMON-PILE-BALANCED-PRODUCTION-READINESS.md)
contains measured tables and the A/B/C comparison. Calibration digest:
`c8a32cced2bae62c21b4e4396d46f0803f11745117e52d468adf3912dde0541d`.
One PressBooks row was rejected as empty; small OER/PDR samples reached verified EOF.

The existing shared `calibration.json` could not be atomically replaced by the
agent (Windows access denied). Successful versioned artifacts preserve the
other sources: `G:\XLM\calib\calibration.common-pile-post.json` and
`G:\XLM\calib\headroom_estimate.common-pile-post.json`. The driver still uses
the default filenames; the operator must materialize them using the exact
[next commands](../implementation/evidence/COMMON-PILE-POST-CALIBRATION/operator-next-commands.md).
No ACL changes or hidden fallback are appropriate.

For estimates of the shared calibration, explicitly pass
`--auxiliary-source ifm_general --auxiliary-source ifm_planning`: those existing
view measurements are disclosed separately while the combined IFM quota entry
drives the mixture estimate. Unknown entries still refuse by default. Component
measurements disclose rounded inventory-weighted counts, not raw sample counts.

The build/show/evidence steps below have succeeded offline; the record/estimate
default paths remain the operator materialization step. `--adopt` allows an
identical measurement to be reused but never replaces a changed one:

```powershell
uv run --offline --locked --no-sync --extra cpu --extra eval python scripts/component_calibration.py build --source-key common_pile --data-root G:\XLM --receipt G:\XLM\calib\common_pile_cal01\sample-receipt.json --receipt G:\XLM\calib\common_pile_cal02\sample-receipt.json
uv run --offline --locked --no-sync --extra cpu --extra eval python scripts/component_calibration.py show --source-key common_pile --data-root G:\XLM
uv run --offline --locked --no-sync --extra cpu --extra eval python scripts/mix01_inventory.py record --source common_pile_prose --calibration G:\XLM\calib\calibration.json --measurement G:\XLM\calib\common_pile_prose\measurement.json --adopt
uv run --offline --locked --no-sync --extra cpu --extra eval python scripts/mix01_inventory.py estimate --quotas recipes/mixtures/mix01_quotas_6b.yaml --calibration G:\XLM\calib\calibration.json --output G:\XLM\calib\headroom_estimate.json --auxiliary-source ifm_general --auxiliary-source ifm_planning
uv run --offline --locked --no-sync --extra cpu --extra eval python scripts/mix01_source.py evidence show --source-key common_pile --data-root G:\XLM --sample-dir G:\XLM\calib\common_pile_cal01 --sample-dir G:\XLM\calib\common_pile_cal02
```

Then review the measured component rates, bounds and mixture alternatives.
`component-bounds preview|record|show` and `component-split preview|record|show`
take `--source-key common_pile --data-root G:\XLM`. Preview/record also need
`--input <reviewed-json> --operator <name> --rationale <text>`.
The bounds JSON contains every field of `component_policy.BOUND_FIELDS` and a
`processing_growth` object; values must come from reviewed evidence.
For B/C, split JSON maps each of the six names to positive integer
`final_tokens` and `first_pass_tokens`. Totals must be exactly 300M/330M.
For A, the explicit input is `{"strategy":"hash_prefix"}`; it supplies no
component-share guarantee. Software selects no option for the operator.
These records go beside `component-calibration.json`, are write-once, and are
bound into plans. Calibration alone does not admit or authorize production.

Common Pile preserves `declared_repository_license=null`. Its C04 basis is
the exact component allowlist and committed evidence matrix. The operator
must still record `approve_research_pretraining` or `reject`, plus provenance
`approved` or `rejected`, through the existing review workflow. Later admission,
policy freeze, planning and digest authorization remain separate operator steps.

Historical allowlist commands (already completed for this Balanced selection):

    uv run --offline --locked --extra cpu --extra eval python scripts/mix01_source.py allowlist preview --source-key common_pile --data-root G:\XLM --include <c1,c2,...> [--accept-flagged <flagged,...>] --operator <name> --rationale "<text>"
    uv run --offline --locked --extra cpu --extra eval python scripts/mix01_source.py allowlist record  --source-key common_pile --data-root G:\XLM --include <c1,c2,...> [--accept-flagged <flagged,...>] --operator <name> --rationale "<text>"
    uv run --offline --locked --extra cpu --extra eval python scripts/mix01_source.py allowlist show    --source-key common_pile --data-root G:\XLM
    uv run --offline --locked --extra cpu --extra eval python scripts/mix01_inventory.py freeze --source common_pile --repo common-pile/comma_v0.1_training_dataset --revision 5afc546db324e7f39f297ba757c9a60547151e7c --seed 20260918 --listing G:\XLM\inventories\common_pile.discovery.listing.json --allowlist G:\XLM\calib\component_allowlists\common_pile.json --output G:\XLM\inventories\common_pile.inventory.json

The allowlist is write-once (`G:\XLM\calib\component_allowlists\common_pile.json`):
it names exact top-level components of the frozen discovery listing, embeds
the committed evidence matrix entry of every component, and refuses a
flagged component (license class outside the open classes, or content fit
outside FIT/PARTIAL_FIT) unless `--accept-flagged` names it again. The
filtered inventory keeps the discovery hash order and seed and its digest
binds the allowlist digest; `plan`, `plan-supersede`, `plan-repair`,
`authorize` and `benchmark plan` refuse any inventory that does not bind it,
so an excluded component can never enter a later top-up.

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
limit or a changed admission (otherwise: resume). It keeps N's `next_cursor`, binds N's digest and
accounting, the failed receipts, the changed limits and the SHA-256 of every
retained verified source (in the plan hash), prints `PLAN DIGEST` and stops.
After `authorize` and `run --plan N+1 --offline`, retained sources are
processed with no transfer. Plan N can no longer run; its sealed units stay
valid; `sufficiency` and `seal` bind each rank once, through the plan that
sealed it (`repair_of`, `repaired_ranks`).

**Admission repair.** A unit can also fail because the adapter contract was
wrong, not a limit. The fix is then a versioned adapter in its own module,
registered in `xlm.data.adapters.registry`. `mix01_adapters.py` is frozen:
every bridge and the Essential-Web campaign bind its bytes. Each adapter's
bridge binds `adapter_code_identity(adapter_id)`. That identity covers the
frozen adapter and column modules plus a versioned adapter's own module, so
only that adapter's `evidence verify`, `plan`, `authorize` and `run` refuse
("adapter code changed"). Renew in this order: `evidence publish`, `review
record`, `admit`. Plan N then still binds the old admission and its `run`
refuses ("admission changed"). `plan-repair --plan N` accepts a changed
admission as the cause, even with unchanged limits. It records
`repair.changed_admission` (present only then), keeps the cursor, and binds
retained sources (zero transfer). The seal's `repair_of` repeats
`changed_admission`. An unauthorized plan under a stale admission is
superseded instead (`changed_sections: ["inputs"]`). First use:
[IFM General empty-text recovery](../implementation/reports/IFM-GENERAL-EMPTY-TEXT-RECOVERY.md).

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
