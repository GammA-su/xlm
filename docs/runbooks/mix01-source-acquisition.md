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
authorization remain valid. Active usage is 2,771,022,236 B, leaving
2,771,750,500 B, below the planner's 10,721,899,142 B processing allowance.
b3 needs larger scratch capacity and bounded processing-output accounting;
the generic scheduler repair remains pending. Do not retry b2 or run the range
half. See the [archive/capacity report](../implementation/reports/FINEPDFS-SCRATCH-ARCHIVE.md).

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
cursor) or `INCOMPLETE` (resume, never top up). `seal` writes the write-once
first-pass seal of the source.

## Files

`G:\XLM\plans\<key>\` (plans, authorizations, policy, benchmarks, performance
receipts, events), `G:\XLM\acq-raw\<key>\source\` (verified upstream Parquet +
identity sidecars), `G:\XLM\canonical\<key>\pNN\fRRRRR\` (documents, zstd
rejection ledger, summary, receipt), `C:\XLM-scratch\<key>\` (partials only).
