# FinePDFs bounded processing growth and b3 review (2026-10-01)

This milestone implements verified source-growth admission, bounded processing
reservations and pre-write enforcement, then prepares **only** the offline b3
benchmark plan. Authorization, adoption and execution require the next operator
step. b1 remains archived; b2 remains failed capacity history with its original
authorization. The [accounting](FINEPDFS-SCRATCH-ACCOUNTING.md) and
[archive](FINEPDFS-SCRATCH-ARCHIVE.md) reports remain historical evidence.

**FINEPDFS B3 READY FOR OPERATOR DIGEST REVIEW.** Plan creation and retained
artifact verification exited 0 in **98.312 s**. No b3 authorization, acquisition
plan, adoption, performance receipt or scratch workspace was created.

- BENCHMARK DIGEST: `aa539f079104c7c764266a285354ec0b79462d2a13390b7301e841876a5e1f35`.
- Plan hash: `55d9a94fc105fec2281fb5bb38475e7252234f8cd29aa0b138149db362eabeab`.
- Plan: `G:\XLM\plans\finepdfs\benchmarks\b3\benchmark.json`.
- Verification digest: `831824c4564e13f33939a45495ab7b3d63cf4467d188fd2c638f7c52c430a45b`.

## Byte-producing-path audit

Let `B=C:/XLM-scratch/finepdfs/bench-b3`, `U=B/staging/f00000`,
`W=U/<private-attempt>`, and `D=G:/XLM/plans/finepdfs/benchmarks/b3`.
`F=5,542,772,736`, `C=5,360,949,571`, `DUR=16,264,671,878`,
`M=S=1,048,576`, `P=4,096`, `L=536,870,912`, `J=8,388,608`.
For verified source length `s`, the final-output pool is `O(s)=DUR-s`.
For an unknown source it starts at `O(F)=10,721,899,142` and is rebalanced
only after transfer verification. Limits are byte counts, without alignment.
`DUR` remains the prior per-unit durable ceiling; the acquisition-level
output-cap operand becomes **21,828,424,326** bytes to include raw publication
overlap and run metadata. This intended accounting change is bound into the
new plan hash.

| Producer / final path | Temporary / coexistence | Maximum / enforcement |
| --- | --- | --- |
| Transfer: `B/f00000.parquet.part` | Stream writes the retained partial directly | `F`; verified complete reuse has zero new source growth; prefix recovery retains existing occupancy until safe truncation; existing transfer/length/drift checks remain |
| State: `B/f00000.state.json` | `f00000.state.json.tmp`, old and new coexist | `S` each, `2S` peak; bound JSON before writing; fixed temporary avoids accumulating crash leftovers |
| Local Parquet decode / selected records / adapter | In memory; no selected-record file, extraction tree, cache or OCR output | Existing decoded, parser, record and row limits; no disk growth attributed to this in-memory path |
| Documents: `W/documents.jsonl` | One private file and bounded writer buffer; no second disk copy | Canonical text `<=C`; **every serialized byte**, including JSON overhead, is charged before buffering/writing to the shared `O(s)` pool |
| Rejections / ledger: `W/adaptation_rejections.jsonl.zst` | Bounded uncompressed ledger and compression buffer in memory; one direct exclusive disk file | Uncompressed ledger `<=L`, checked before append; compressed file `<=min(L,O(s))`, charged to the same pool before write; no separate uncompressed disk ledger |
| Summary: `W/adaptation_summary.json` | One direct exclusive file; no temporary disk copy | `<=M`, also charged to `O(s)` before write |
| Worker progress: `U/progress.json` | `U/progress.tmp` replaces prior snapshot; `U/progress.lock` is empty | `P` each, `2P` peak; serialize/check before temporary write; lock behavior unchanged |
| Unit receipt / durable identity (production) | Receipt exclusive link publication briefly exposes final and temporary paths; identity uses atomic replace | Final receipt `<=M` and actual identity bytes are precharged to `O(s)`; additional receipt overlap `M` is reserved. Benchmark keeps this shared envelope even though it publishes no canonical unit receipt or raw identity on scratch |
| Raw promotion (production only) | Durable source `.tmp` and final raw path coexist during hard-link publication; sidecar final/temporary coexist | Copy stops before exceeding verified `s`; reserve `2s+2M` on the actual output volume, in addition to processing; no raw promotion in benchmark |
| Canonical unit publication (production only) | Rename private directory into final canonical unit | No duplicate directory contents; shared canonical/durable checks and bounded receipt precede rename |
| Benchmark reconciliation and cleanup | Streaming read of documents; no reconciliation output | Shared final result check precedes successful reconciliation; failures retain source and bounded private outputs; existing retry cleanup policy remains |
| Events: `D/events.jsonl` | Append; existing history remains | At most `1,048,576` **new** bytes per run, checked before append |
| Performance: `D/performance-NN.json` | Write-once temporary and final link coexist | `J` each, `2J` peak, checked before publication; events plus performance reserve `2J+M=17,825,792` on G: |
| Planning / authorization / adoption JSON | Write-once metadata temporary and final link | Generic JSON publication ceiling `J`; only `benchmark.json` is created on G: in this milestone; no authorization or adoption performed |

The previous gaps were documents/summary/serialized ledger without a common
disk ceiling, progress/state without a frozen output bound, and benchmark
final checks weaker than production. Those paths now use the bound above.
Ledger and summary are **inside**, not added a second time to, the final-output
pool. The document writer streams; it never loads the completed output to
count its bytes. The existing ledger remains memory-buffered under its explicit
512 MiB bound; this change is not a memory-usage optimization.

## Contract and admission

`ProcessingGrowth(version=1)` is data-only and frozen into both the benchmark
record and acquisition behavioral hash. Old records omit that hash operand and
still reconstruct their original identity. The selection hash is unchanged.
The contract derives `output_bytes=DUR-F`, `source_max_bytes=F`,
`metadata_bytes=min(1 MiB,max(1,output_bytes//16))`, state 1 MiB, progress 4 KiB,
run metadata 8 MiB and events 1 MiB. These small explicit ceilings are enforced
refusal limits, not estimates of what arbitrary metadata might produce.

Final serialized outputs share unused verified source headroom within the
**unchanged** durable ceiling: `s+O(s)=DUR`. This preserves small-source
adaptation where document metadata can exceed twice the canonical text. It
does not increase canonical text, record/parser or durable limits. The old 2x
planner multiplier is now an enforced aggregate ceiling, not an assumption
that JSON always expands by less than two.

`SourceReservations` verifies/classifies before scheduling, reserves source,
state and worker outputs before submission, and requires a matching reservation
in the worker job. After verified transfer it rebalances source/output space
before submitting processing. Independent budgets on the same volume sum
outstanding physical growth. Admission also counts every retained path and
private output from earlier attempts. No hard-link, sparse-file, compression
or deduplication exemption exists. Capacity refusal never submits a worker.

| Restart class | Source reservation |
| --- | --- |
| `sealed_skip` | No work/reservation; leftovers remain occupied |
| `local_complete_reuse` | Verify version, name, canonical URL, revision when recorded, strong validator, linked length/digest, exact length, SHA and prefix SHA; reserve actual source size, **zero** new source growth; recheck before reuse and refuse changed complete input without network fallback |
| `local_processing_retry` | Existing durable source and identity rehashed by preparation; no new scratch source copy; processing reservation still required |
| `resumable_partial` | Validate prefix hash, identity and bounded total; reserve only `total-verified_prefix` growth, **plus retain unverified-tail occupancy** until safely truncated |
| `fresh_download` | Reserve `F` new source bytes; invalid retained tails remain occupied until transport safely restarts in place |

Worker documents, compressed rejections, summary and reserved final metadata
share one pre-write budget. State/progress replacement and receipt publication
have explicit overlap allowances. Benchmark and production both use the same
worker and `check_result`, supplementing production's existing seal checks.
On overrun they fail closed; oversized output is not published as a unit.
Output sizing is conservative after an I/O failure. Historical source plans
without the new contract remain verifiable but unsealed work requires a new
plan; their old authorizations are not silently expanded.

`benchmark run --offline` now requires every input to verify as complete local
reuse before any download submission. Its complete-cache transfer path cannot
fall back to network. HF offline environment variables alone do not constrain
the underlying HTTP client. Donor adoption additionally uses the same complete
state verifier, then rehashes the linked bytes; the existing authorization gate
and archive resolution remain required.

## Exact b3 cap

The frozen retained allowance is `R=2,771,022,236` bytes, the complete active
b2 source plus its state. b1 is outside the active root and still occupies
physical disk. b3 authorizes a general fresh-capable transport envelope:

```text
new fresh source bound F                 5,542,772,736
final processing pool O(F)              10,721,899,142
receipt atomic overlap M                    1,048,576
two progress snapshots 2P                       8,192
two source-state snapshots 2S               2,097,152
one new unit slot                       16,267,825,798
retained b2 allowance R                  2,771,022,236
b3 active logical scratch cap           19,038,848,034
```

No rounding or unexplained multiplier is added. This is the smallest cap for
the chosen shared publication envelope plus the measured retained footprint.
It is not a claim that the benchmark will produce that many bytes. Serialized
ledger (up to 536,870,912), summary (up to 1,048,576), final metadata and documents
compete within the processing pool; none can make the aggregate exceed it.

With adopted `s=2,771,021,138`, new **source** growth is zero and
`O(s)=13,493,650,740`. Existing b3 source bytes still count. The total envelope
remains `R+s+O(s)+M+2P+2S=19,038,848,034`. If adoption/reuse is lost, fresh
download can use `F` while the processing pool returns to `O(F)`. Additional
unplanned leftovers may cause a safe refusal; a fixed cap cannot promise
unlimited abandoned retries. A missing or corrupt complete input in offline
mode refuses instead of downloading.

Physical free space after **all outstanding new growth** must remain at least
`34,359,738,368` bytes on each involved volume. Fresh C: requires that reserve
plus `16,267,825,798` new bytes above the current root. G: run metadata reserves
`17,825,792` new bytes plus the same physical reserve. If roots share a volume,
their growth requirements are summed. Archived b1 consumes physical space
through the filesystem's free-space value; it is never subtracted from that
check. No current free-space reading is a promise about a later run.

## Identity and real retained-artifact evidence

The metadata-only evidence is under
[`FINEPDFS-PROCESSING-GROWTH`](../evidence/FINEPDFS-PROCESSING-GROWTH).
`prepare_b3.py` refuses socket connect/DNS/bind, rehashes source bytes and archive
inventory, verifies the UltraX first-pass seal with content checks, invokes the
real CLI planner, then compares historical file hashes/sizes/mtimes. It prints
no corpus text. Its one-time precondition refuses an already-existing b3.

All **15** b1/b2 operator files have unchanged hashes, sizes and mtimes. The
five archived files reverify, the b2 state is unchanged, and the real source
reproduces `4eeb58bc769a194f472100ed4acd1d960e35c669caecd96fd4ce6425fabca38d`.
Active occupancy remains **2,771,022,236** bytes. The verified source class is
complete local reuse, with zero new source/network bytes required.
The UltraX seal re-verifies all **12** units with content checks and remains
`efdb99dbf1bc8380985925fcbbe654536d02c5b0c87162eecc9e92e858302f1f`;
all **15** UltraX operator JSON files remain unchanged. Its sealed artifacts
are read only. The post-check physical C: free space was **851,160,186,880**
bytes. No processing throughput, CPU or peak-memory measurement was taken.

b3 preserves `finepdfs_edu` / `finepdfs_en` / `eng_Latn`, repository
`HuggingFaceFW/finepdfs-edu`, revision
`9cfabe2127faca99b3d5c4dc6d1fcb397399ebde` and file
`data/eng_Latn/train/000_00083.parquet`. FinePDFs record/parser stay
33,554,432 bytes; UltraX generic record stays 8,388,608 bytes. Adapter/column
code, admission bridge, membership and scientific policies are unchanged.
Only scratch, processing-growth and associated output-cap operands change.

Shared Essential Web transfer/scheduler code gains optional hooks. Its exact
executable hashes are bound by **one additive** compatibility record continuing
the existing chain; old manifests, authorizations, reports and sealed outputs
are not edited. Tests check the running code, historical chain links and refusal
of an amendment that changes an unrelated adapter. This is not new Essential
Web execution or an alteration of its scientific/record limits.

## Validation ledger

Windows 11 build 26200; Python 3.12.13; existing locked CPU/eval uv environment,
`--offline --locked --no-sync --extra cpu --extra eval`. No installs, dependency
changes, CUDA policy changes or external network. Set `UV_OFFLINE`,
`HF_HUB_OFFLINE`, `HF_DATASETS_OFFLINE`, `TRANSFORMERS_OFFLINE` to `1`,
`OMP/MKL/OPENBLAS/NUMEXPR_NUM_THREADS=1`, `TOKENIZERS_PARALLELISM=false`.
Real planning additionally sets `XLM_DATA_ROOT=G:\XLM`,
`XLM_HOME=G:\XLM\xlm-home`, `XLM_SCRATCH_ROOT=C:\XLM-scratch`, `PYTHONUTF8=1`.

The focused selection covers new adversarial growth cases plus source-run,
planning, record, archive, acquisition identity, CLI, Essential Web transport /
Windows publication, UltraX and FinePDFs schema regressions. Authored fixtures
only; integration transfers use isolated loopback HTTP. They are not live
dataset tests or whole-file FinePDFs performance measurements.

```powershell
uv run --offline --locked --no-sync --extra cpu --extra eval python -m pytest tests/test_source_growth.py tests/test_source_growth_integration.py tests/test_source_run.py tests/test_source_plan.py tests/test_source_record_bound.py tests/test_source_archive.py tests/test_acquisition_plan.py tests/test_mix01_source_cli.py tests/test_essential_web_fast.py tests/test_essential_web_windows_publication.py tests/test_ultrax_ultrafineweb.py tests/test_finepdfs_live_schema.py -m 'not serial' -n 16 --dist=worksteal --max-worker-restart=0
uv run --offline --locked --no-sync --extra cpu --extra eval python -m pytest tests/test_source_record_bound.py tests/test_source_archive.py tests/test_source_growth_integration.py tests/test_mix01_source_cli.py -n 16 --dist=worksteal --max-worker-restart=0
uv run --offline --locked --no-sync --extra cpu --extra eval python docs/implementation/evidence/FINEPDFS-PROCESSING-GROWTH/prepare_b3.py
```

Development failures were corrected without weakening assertions: initial
source-output accounting needed shared durable headroom; the generic key-set
test needed the explicit new operand; frozen Essential checks needed an additive
compatibility link; legacy scratch tests required retaining the base API while
enforcing path containment in the new manager. A 190-case related selection
then passed (17.30 s). After adding offline mode and stronger donor checks,
191/192 passed (16.69 s); the donor error type had regressed. Restoring its
existing `RunError` contract yielded **34/34 affected cases passing** (6.64 s).
Thus 192 distinct focused cases have passing results, zero skipped. Pytest's
cache-directory permission warning is not a test failure. No full acceptance,
CUDA or live network selection was run.

Ruff check, Ruff format check and strict mypy pass on all 18 changed Python
files (including tests and the evidence script). Strict mypy initially found
implicit import errors; direct imports fixed them. Mypy was not blocked by
Windows application control. `git diff --check` passes. These are focused
checks, not full acceptance. No new throughput or peak RSS claim is made.

Exact static-check selection and commands (each final check exit 0):

```powershell
$changed = @(
  'scripts/mix01_source.py',
  'src/xlm/data/acquisition/plan.py',
  'src/xlm/data/acquisition/records.py',
  'src/xlm/data/acquisition/source_benchmark.py',
  'src/xlm/data/acquisition/source_dashboard.py',
  'src/xlm/data/acquisition/source_local.py',
  'src/xlm/data/acquisition/source_parquet.py',
  'src/xlm/data/acquisition/source_plan.py',
  'src/xlm/data/acquisition/source_run.py',
  'src/xlm/data/acquisition/source_growth.py',
  'src/xlm/data/acquisition/source_reservations.py',
  'src/xlm/data/sources/essential_web_local.py',
  'src/xlm/data/sources/essential_web_recovery.py',
  'tests/test_source_growth.py',
  'tests/test_source_growth_integration.py',
  'tests/test_source_record_bound.py',
  'tests/test_source_run.py',
  'docs/implementation/evidence/FINEPDFS-PROCESSING-GROWTH/prepare_b3.py'
)
uv run --offline --locked --no-sync --extra cpu --extra eval ruff check @changed
uv run --offline --locked --no-sync --extra cpu --extra eval ruff format --check @changed
uv run --offline --locked --no-sync --extra cpu --extra eval mypy --strict @changed
git diff --check
```

Ruff import fixes and formatting were applied during development; pre-format
checks reported line-length/import issues. Final checks above pass. Static
checks and tests had no installation step. The new key-set expectation is an
explicit contract addition; no safety assertion was removed or relaxed.

| Requirement | Status | Evidence / limit |
| --- | --- | --- |
| Byte-producing-path map / frozen growth contract | IMPLEMENTED / VERIFIED | Table, behavioral hash and deterministic planner tests |
| Source classes / zero-growth reuse | IMPLEMENTED / VERIFIED | State/hash checks, partial/fresh tests, same-volume and retained-output tests |
| Processing admission before workers | IMPLEMENTED / VERIFIED | Parent reservation, worker marker, refusal test, process-pool integration |
| Documents/rejection/ledger/summary/progress/state bounds | IMPLEMENTED / VERIFIED | Exact writer ceiling, individual/aggregate refusal, no oversized publication |
| Production / benchmark safety alignment | IMPLEMENTED / VERIFIED | Shared worker/result check; canonical, ledger, summary and compression failures |
| Donor/archive / record policies / restart compatibility | VERIFIED | Existing suites plus offline complete-reuse integration |
| b3 plan and real history checks | VERIFIED | Offline real CLI; exact identities above; no authorization/adoption/run |
| FinePDFs whole-file performance, actual output size, peak RSS | NOT RUN | Requires operator review/authorization and later execution |
| b2 retry, range, production, C05, tokenizer, training, network, push | OUT OF SCOPE | None executed |

## Operator boundary

The next action is to **review** `G:\XLM\plans\finepdfs\benchmarks\b3\benchmark.json`.
The live [runbook](../../runbooks/mix01-source-acquisition.md) gives the exact
future authorization/adoption/offline-run commands. This session stops at the
b3 benchmark digest. It does not authorize b3, adopt b2 into b3, execute b3 or
establish readiness for the range half.

The local commit includes the 18 Python files listed above, this report,
additive STATUS entry, live runbook and three JSON evidence files. Previous
reports and operator history remain untouched. The original 101 unrelated
STATUS lines and unrelated untracked work remain unstaged; no push. Review
the resulting local commit with `git show --stat HEAD`.
