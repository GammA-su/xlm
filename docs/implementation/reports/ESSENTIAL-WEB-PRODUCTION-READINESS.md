> **Current update, 2026-09-30: ADAPTER FIXED; RESUME THE EXISTING PROBE OFFLINE.**
> Operator admission and the 256-row probe fetch have already completed (operator
> context); do not repeat the historical admission/probe instructions below.
> Empty optional FDC labels caused all 108 renderer failures. All 256 now render,
> with frozen selector counts unchanged. Existing raw passes local verification.
> [Repair evidence, checks and exact resume command](ESSENTIAL-WEB-OPTIONAL-FDC-FIX.md).
> Use `resume-probe-adaptation.ps1` in the readiness evidence directory; it reuses
> raw/plan, runs no fetch, and produces measurement.json. Script execution and
> production canonical outputs remain NOT RUN in this repair task.

# Essential-Web production readiness — 2026-09-30

**READY FOR OPERATOR ADMISSION**

## Admission-contract correction (2026-09-30, offline)

The existing live schema probe passed: receipt
`G:\XLM\calib\essential-web-production\schema-probe\schema-probe-20260930T115739Z.receipt.json`,
`ACCESSIBLE`, `real_observed`, 8 requests, 238,373 response-body bytes. Its three
stored fingerprints were verified offline. The schema probe requirement is
satisfied; do not repeat the historical schema-probe command below.

`clean` was not contractually defined as absence of deliberate benchmark blends.
The old prepared decision was therefore ambiguous. Amendment
`c04-benchmark-risk-v2` requires `suspect_with_mitigation` for all three Essential
views, with explicit review hashes, the existing `xlm.data.exclusion` mechanism,
frozen-pool scope and a fail-closed benchmark-claim gate. Contamination remains
possible. No corpus-wide decontamination or zero-contamination proof exists.

The three decisions are resealed with the real fingerprints and unchanged
source/revision/B-normal/resource bindings. C04 verification passed in a temporary
store with simulated approval. Real operator admission is still **false**, and
the prepared decisions say `operator_approved=false`. C05 screening of the
eventually frozen Essential/Mix-01 canonical training pool remains outstanding;
it is required before tokenizer/gradient training and official benchmark claims.

Next operator command, from the repository root:

```powershell
. .\scripts\operator_storage.ps1
& .\docs\implementation\evidence\ESSENTIAL-WEB-ADMISSION-BOOTSTRAP\future-admit.ps1 -Operator $env:USERNAME
```

This offline command checks the prepared seal, current reviews and stored probe
identities before recording approval. It was not executed against the real store.
See [the correction report](ESSENTIAL-WEB-ADMISSION-CONTRACT-V2.md) for the exact
amendment, test outcomes, remaining C05 limitations and changed files. The earlier
readiness material below is historical and superseded by this correction.

## Admission bootstrap update (2026-09-30, offline)

Everything that can be closed offline is closed. Production admission itself is
**still false**, because it needs two things that cannot be produced offline: a
live schema probe record and the operator's approval. The live sequence
therefore starts with a small schema probe and an admission step, and the fetch
gate refuses the 330-request production probe until both exist.

Starting HEAD `9aab98a1afa8cec778710f15cd1adc5cbeb1a506`; `57cb42f` and `9aab98a`
are ancestors. The project pipeline made no network request. No probe,
calibration or acquisition ran; selector, mixture, inventory and calibration are
unchanged; nothing was pushed. Commands, exit statuses and the fixture/live
distinction are in
[COMMANDS.md](../evidence/ESSENTIAL-WEB-ADMISSION-BOOTSTRAP/COMMANDS.md).

### What C04 requires

C04 (CONTRACTS.md) requires: real repository, immutable revision,
subset/files/split, actual schema, tested adapter, source license/provenance
review and explicit operator approval. The gate that enforces it
(`AdmissionGate.evaluate`) needs:

| Requirement | Source | State |
|---|---|---|
| Probe evidence `accessible`, `real_observed`, with revision, verified schema and fingerprint | live probe | **missing**; stored record is `budget_exhausted` |
| Declared license known | probe | missing until the probe reads the card |
| Decision matches evidence source, view, provider, repository, revision, fingerprint | decision | prepared |
| Essential views: pinned revision, `essential_web_bnormal`, exact B-normal selector binding | decision | prepared |
| `license_review = approved` | review + operator | review written |
| `benchmark_risk = clean` | review + operator | review written |
| `operator_approved = true` | operator | pending |

C04 asks the probe for schema and source identity only. Yield and cost are not
admission fields; they belong to the production probe and calibration.

### Source, license and provenance review

[source-rights-review.json](../evidence/ESSENTIAL-WEB-ADMISSION-BOOTSTRAP/source-rights-review.json),
[attribution-plan.json](../evidence/ESSENTIAL-WEB-ADMISSION-BOOTSTRAP/attribution-plan.json) and
[external-source-evidence.json](../evidence/ESSENTIAL-WEB-ADMISSION-BOOTSTRAP/external-source-evidence.json).
This is an engineering and data-governance review, not legal advice.

- **Identity**: `EssentialAI/essential-web-v1.0` at
  `ce4eccc7e9604667b6d7f32cb6274b8b41f3113d`; public, ungated; no access terms
  were accepted.
- **License metadata**: `odc-by` in the dataset card front matter and in the
  local repository metadata snapshot. It covers Essential AI's contributions.
- **Attribution**: Essential-Web v1.0 under ODC-By with the arXiv:2506.14111
  citation; Common Crawl named as upstream, with its Terms of Use.
- **Underlying content**: the card says "We do not alter the license of any of
  the underlying data." Per-page rights are unknown and this review does not
  resolve them. No claim is made that every document is ODC-By.
- **Provenance**: Common Crawl → DCLM pool (89 snapshots, 2013-20 to 2022-49)
  plus 12 snapshots (2023-06 to 2024-38) → deduplication, quality filtering,
  taxonomy labeling → Essential-Web → frozen B-normal selector.
- **Privacy and third-party risk**: crawled text can hold personal, copyrighted
  or restricted material; the card documents no removal of personal information.
- **Use**: research pretraining corpus under `strict_research`. Redistribution,
  weight publication and commercial use are not covered.
- **Mitigation**: locators and upstream ids are kept so documents can be traced
  and removed; no ownership claim; removal means excluding locators and
  refreezing the pool (there is no automated takedown service).

The agent read the card, the Common Crawl terms and the arXiv abstract through
its WebFetch tool. That tool returns a model-made summary, so those
observations carry no byte hash. The live schema probe records the card's
SHA-256 at the pinned revision. The Common Crawl terms also contain a user
indemnification covering AI training; the operator should read the full terms.

### Benchmark contamination review

[benchmark-risk-review.json](../evidence/ESSENTIAL-WEB-ADMISSION-BOOTSTRAP/benchmark-risk-review.json).
Contamination with BLiMP, ARC-Easy, HellaSwag and PIQA is **possible**; nothing
shows the source was decontaminated, and no Essential-Web text has been scanned
against benchmark text. Zero contamination is not claimed.

The existing mechanism is C05 benchmark exclusion (`xlm.data.exclusion`:
full-example hash and informative-span matching, with receipts). The review
binds it as the mitigation and separates two permissions:

- acquire and pretrain: **yes**;
- claim uncontaminated benchmark results: **no**, until a C05 exclusion receipt
  exists over the frozen pool.

Two limits are recorded. The matcher holds its documents in memory and has not
been run or sized for the Mix-01 pool, and no command applies it during pool
freeze, so a pool frozen today would record `none_declared`.

**Resolved by `c04-benchmark-risk-v2`.** The original three-value field did not
define the narrow blend-only meaning. The versioned amendment above replaces
the Essential prepared `clean` decisions with `suspect_with_mitigation` and
binds the separate C05 receipt requirement. Neither risk state certifies zero
contamination.

### Bootstrap: the contradiction and the route taken

The shared production probe needs about 330 requests, so it is a production
plan and needs prior admission; admission needs accessible probe evidence. The
generic `data probe` cannot supply it: it never verifies a schema without card
metadata, and for this repository its metadata request exceeds the body limit.
This is a circular dependency in the tooling, not in C04 or C13.

A second blocker was found. The artifact store never replaces a destination,
and `probe_essential_web_essential_science` already holds the
`budget_exhausted` record. No new evidence for that view could be published at
all.

Route 1 (adopting Phase-P evidence) was **not** taken: neither C04 nor
`ProbeEvidenceRecord` has an adoption or equivalence mechanism. Route 3 (a
contract amendment) was not needed; the pilot ceiling stays at 100.

**Route 2 was implemented**: a dedicated schema and source-identity probe.

| Operation | Bytes | Establishes |
|---|---:|---|
| `README.md` at the pinned revision | ≤ 1 MiB | card SHA-256, license, commit header when served |
| file bytes 0–3 | 4 | Parquet header, remote length, ETag |
| last 8 bytes | 8 | footer length |
| footer | 203,268 | schema, row groups, codecs |

Four logical operations, about 8 physical requests with one redirect each; hard
cap **24 requests**, 8 MiB, 15 s per request, 120 s overall, one worker. The
probe decodes no row and never reads the text column.

It refuses on: altered plan, wrong repository, revision, adapter or selector
binding; a card served from another commit; a license other than `odc-by`;
changed file length, ETag or footer bytes; a schema digest other than the one
the selector was frozen on; missing `text`, `eai_taxonomy` or `quality_signals`;
a non-string `text`; a projection the production reader cannot decode; a first
row group shorter than 2,048 rows.

Expected values come from the retained Phase-P footer of the first calibration
file (`data/crawl=CC-MAIN-2014-15/train-01860-of-02772.parquet`). All eight
retained footers share one schema. These are planning evidence; the live probe
observes each value again.
[schema-probe-plan.json](../evidence/ESSENTIAL-WEB-ADMISSION-BOOTSTRAP/schema-probe-plan.json),
digest `0b887739d5bac0a321d5b312a9f4d7a955e1851baaec7f8dcfede5a5bdedd3b4`.

Not established by this probe, and left to the production probe: row decode
through the production reader, selector yield, retained bytes and transfer cost.
A row-level check under 100 requests would need a second, coalescing reader;
the certified reader issues at least one request per leaf (101 leaves).

**Supersession.** Probe evidence and decisions can now be published as
`<id>.attemptNN`. The loaders resolve the newest attempt, check that the record
names the requested source and view, and return nothing when the newest attempt
is damaged (they never fall back to older evidence). The `budget_exhausted`
record stays in the store untouched.

### Admission decisions

[admission-decisions.json](../evidence/ESSENTIAL-WEB-ADMISSION-BOOTSTRAP/admission-decisions.json)
holds one **prepared, not recorded** decision per view. Each binds repository,
revision, adapter and its code hash, the B-normal selector identity, component,
canonicalization path, resource contract and the SHA-256 of all four review
files. The fingerprint is empty until the live probe supplies it.

The gate was run offline on a replay of the retained real footer with an
authored card. It refuses that evidence as `synthetic_fixture`, as it should.
Relabeling the replay in memory shows every other criterion passes, so nothing
but live evidence and approval is missing. Nothing was written to `G:\XLM`.

The earlier refusal records in
`ESSENTIAL-WEB-PRODUCTION-READINESS/admission-decisions.json` are unchanged.

### Production probe and calibration

The shared probe keeps its frozen plan: 256 rows, 128 MiB, 330 requests, one
worker, 15 s requests, 600 s deadline, plan hash `04db5c76…52b04c`. It
classifies as **production** and is refused without stored admission or
without a matching authorization hash
([dry result](../evidence/ESSENTIAL-WEB-ADMISSION-BOOTSTRAP/production-probe-authorization-dry.json)).
An authored-store test shows that after admission the fetch gate resolves it
and the operator script's own `data plan` command reproduces the same hash.

Calibration is unchanged: 16,384 rows, 8 × 2,048, digest
`a6cab8cd58a127b77dd130249147f3541ac4575f3ea8369bf248879fd52e89b0`. All 26
calibration files hash-match the manifest sealed at `57cb42f`
([proof](../evidence/ESSENTIAL-WEB-ADMISSION-BOOTSTRAP/calibration-unchanged.json)).

### Next operator commands

Not executed. Run from the authoritative checkout.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass
Set-Location -LiteralPath 'F:\Project\xlm-data-ultrax'
. .\scripts\operator_storage.ps1
$B = 'docs/implementation/evidence/ESSENTIAL-WEB-ADMISSION-BOOTSTRAP'
$E = 'docs/implementation/evidence/ESSENTIAL-WEB-PRODUCTION-READINESS'
& "$B/future-schema-probe.ps1"                 # live, about 8 requests
# Read the four review files in $B, then approve as the operator:
& "$B/future-admit.ps1" -Operator '<your name>'
& "$E/future-probe.ps1"                        # live, bounded production probe
& "$E/future-calibration.ps1"                  # live, 16,384 rows
```

If the schema probe refuses, it writes a refusal receipt under
`G:\XLM\calib\essential-web-production\schema-probe` and publishes nothing.

### Readiness and requirement ledger

| Check | Value | Status |
|---|---|---|
| selector_frozen | true | VERIFIED |
| selector_integration_ok | true | VERIFIED |
| source_revision_ok | true | VERIFIED |
| admission reviews complete | true | IMPLEMENTED, VERIFIED by `check_reviews` |
| production_admission_ok | **false** | BLOCKED on live schema probe and operator approval |
| inventory_ready | true | VERIFIED |
| malformed_policy_ready | true | VERIFIED |
| transfer_strategy_ready | true | VERIFIED model |
| probe_plan_ready | true | IMPLEMENTED, VERIFIED offline; live NOT RUN |
| calibration_plan_ready | true | VERIFIED unchanged |
| acquisition_plan_ready | false | BLOCKED: admission, then measured science capacity |
| live schema probe / live probe / live calibration | false | NOT RUN |
| full and fast repository selections, CUDA | — | NOT RUN, OUT OF SCOPE |

Remaining live-only items: the schema probe record, the operator approval, and
the measured science retained bytes, tokens per input row and transfer per
input row.

Open limitations: the probe's live behavior is untested (ETag form, redirect
count and the commit header are assumptions checked fail-closed); two existing
acquisition tests fail under this machine's long `TEMP` path and pass with a
short one; the C05 matcher is not yet wired or sized for the Mix-01 pool.

---

## Previous state at commit 57cb42f (preserved)

The text below is the earlier report, unchanged. Its blockers A and B are addressed above; its verdict and readiness table describe that earlier state.

**ESSENTIAL-WEB PRODUCTION READINESS BLOCKED**

Offline hardening and the calibration freeze are implemented. Live execution is
not ready: C04 admission evidence is incomplete, the existing full-column window
reader exceeds the pilot request ceiling, and science retained-byte yield has not
been measured. These are explicit blockers, not successful production admission.
No network, live probe, calibration, bulk acquisition, T reviewer text access,
selector change, mixture change, dependency installation, or push occurred.

Starting HEAD: `29c3814a076122e6f86e98491dbf7986fe0062fa`, branch
`data/mix01-ultrax-6b`. Both `9586778ef5ac594900efb4b9bf78daaa0ee5c6ce`
and that integration commit passed `merge-base --is-ancestor` (exit 0).
The pre-existing STATUS edit and seven untracked review paths were preserved;
only this task's STATUS addition is staged. Exact commands, environment and
failures are in [COMMANDS.md](../evidence/ESSENTIAL-WEB-PRODUCTION-READINESS/COMMANDS.md).

## Admission and provenance

| View | Decision | Frozen final | Final / first-pass quota |
|---|---|---|---|
| essential_science | BLOCKED | essential_science | 600M exact / 660M estimated tokens |
| essential_practical | BLOCKED | essential_practical | 600M exact / 660M estimated tokens |
| essential_prose | BLOCKED | essential_prose | 300M exact / 330M estimated tokens |

[Decisions](../evidence/ESSENTIAL-WEB-PRODUCTION-READINESS/admission-decisions.json)
are deterministic refusal records, **not invented approved AdmissionDecision
artifacts**. No decisions were installed in the operator store. Each binds source,
revision, component semantics, B-normal identity, adapter code SHA-256,
canonicalization and resource contract. C04's actual gate now refuses foreign
source/view/provider/repository decisions, wrong Essential revision, legacy
adapter or wrong/missing B-normal selector binding. `data admit` records the
verified selector identity for the production adapter.

Local source evidence:

- `D:\Project\xlm-operator-pilot\adapter-cert-essential-web01\repository-meta.json`:
  repository `EssentialAI/essential-web-v1.0`, revision
  `ce4eccc7e9604667b6d7f32cb6274b8b41f3113d`, `license=odc-by`,
  `private=false`, `gated=false`. Its exact bytes/hash and metadata are bound in
  [source-provenance.json](../evidence/ESSENTIAL-WEB-PRODUCTION-READINESS/source-provenance.json).
- `recipes/mixtures/mix01_views.yaml`, P07/P13 and
  `MIX01-6B-ACQUISITION-PLAN.md` corroborate the observed tag and pending review.
  The local repository/evidence search found no archived upstream license terms,
  completed rights/attribution/provenance review or benchmark-risk review at this
  pin. A license tag is not that review. No rights were inferred or accepted.
- Real M Phase-P layout and eight footer payloads at
  `G:\Project\xlm-evidence-v4.1\essential-web` provide physical schema,
  immutable URLs, lengths and strong ETags. Layout SHA-256
  `3bcafccb81737694d731dad9cda4949b5e221c51ca9cde4f69102b087267f5e4`
  and every footer descriptor were checked before parsing. Only M footer bytes
  were opened; no document payload or T layout/reviewer text was used.
- `G:\XLM\xlm-home\probe_evidence\probe_essential_web_essential_science\probe_evidence.json`
  is still `budget_exhausted`; the other two production views have no probe.
  A schema-verified accessible `ProbeEvidenceRecord` and current fingerprint
  remain missing. Local footer evidence is not silently relabeled as a completed
  live production probe.

The skipped two-reviewer T arm is **not** an admission requirement here.

## Inventory adoption

The existing `ESSENTIAL-WEB-EVIDENCE-V2/inventory-freeze.json` was reverified via
the existing inventory verifier, including every original listing hash, eligible
path digest, source/revision and complete inventory digest. The adopted inventory
has **23,200 distinct paths in eight frozen crawl strata**. It is not a complete
inventory of all 101 source crawls. Eight development files remain excluded.

Ordering uses the existing planner's
`SHA256(seed|repository|revision|file)` with seed **20260930**, then filename.
The existing `mix01_inventory` format and freeze implementation are reused.
Crawl is unambiguously encoded in each exact path. Sizes/ETags/row counts are
known for eight footer-certified files; the other 23,192 sizes remain null.
The planner accepts unknown sizes; capacity estimates are correspondingly limited.

Inventory digest:
`4bbd5517e760971405d9aed56bba5b84877a6325d10f5a10a05b50f5a8d763d8`.
[Inventory](../evidence/ESSENTIAL-WEB-PRODUCTION-READINESS/production.inventory.json),
[adoption manifest](../evidence/ESSENTIAL-WEB-PRODUCTION-READINESS/inventory-adoption.json).
This is a frozen repository artifact, not an unreported write to `G:\XLM`.

## Physical transfer decision

The real retained footers show independently addressable scalar leaves beneath
`eai_taxonomy` and `quality_signals`. Text occupies its own column chunk. The
eight candidate group-0 layouts each have **10,000 rows and 101 projected leaves**.
None of these 808 projected chunks has an offset or column index. The existing
window-v2 reader can stop sequential decoding early, but cannot seek to admitted
rows. A simple two-pass implementation therefore pays for a text chunk whenever
any row in its group is admitted; row-level text transfer is unavailable.

Observed compressed bytes across these eight groups:

| Quantity | Bytes |
|---|---:|
| All adapter-projected chunks | 226,939,951 |
| Selector metadata chunks | 13,184,204 |
| Text chunks | 185,909,973 |
| Footer payloads | 1,398,384 |
| Projected uncompressed chunk sizes | 373,129,561 |
| Full-text-first model, including footer/read-ahead | **228,862,655** |
| Metadata-first model, including admitted text and other columns | **228,862,655** |
| Expected physical saving | **0%** |

The calculation uses confirmation admission `513/4096 = 12.5244%` and
`P(chunk needed) = 1 - (1 - 513/4096)^10000`, numerically 1 for every group.
It assumes independent admissions within groups; clustering is unmeasured.
Even science alone has about 58.6 expected admissions per 10,000 rows.
These are approximate whole-chunk costs, not measured live 2,048-row prefix costs,
network wire bytes or promises about the full source. Dictionary/buffer reads and
retries can alter actual prefix transfer. The second-pass overhead is omitted,
which favors metadata-first in this comparison.

**Keep full-text-first.** The recorded model does not reach the operational 25%
saving threshold. No sparse downloader was built. Inputs, chunk sizes/index flags,
assumptions and reproducible calculation are in
[physical-inputs.json](../evidence/ESSENTIAL-WEB-PRODUCTION-READINESS/physical-inputs.json)
and [transfer-strategy.json](../evidence/ESSENTIAL-WEB-PRODUCTION-READINESS/transfer-strategy.json).

## Malformed rows

`EssentialWebSelectedAdapter` retains the certified renderer and exact evaluator.
Isolated missing/unusable fields or Unicode text failures become
`EssentialWebMalformedRowError` with `essential_web_unusable_record`; selector
validity failures retain the evaluator's deterministic reason codes. Neither
reason includes rejected text. With `--on-reject record`, the existing atomic
rejection ledger records category `malformed` and the next row continues.

Per adapt pass: stop when malformed fraction is **>1% at 100 or more rows**;
before 100, stop on the third malformed row. Ordinary selector policy rejections
do not count as malformed. A single bad row followed by 99 good rows is allowed;
two bad rows among 100 stop. Failure prevents completed canonical publication.
Thresholds apply independently to each file pass; tiny partial passes may finish
before the 100-row fraction check, bounded by the early absolute rule.

Wrong revision, foreign path/selection, invalid container/decompression, missing
physical schema fields and impossible row accounting remain fatal. Oversized
records refused by the bounded decoder remain resource failures, not permission
to relax limits. Production selector semantics and all thresholds are unchanged.

## Frozen calibration and probe

Calibration digest:
`a6cab8cd58a127b77dd130249147f3541ac4575f3ea8369bf248879fd52e89b0`.

The exact eight paths, strong ETags, lengths and footer hashes are in
[calibration-plan.json](../evidence/ESSENTIAL-WEB-PRODUCTION-READINESS/calibration-plan.json).
Each contributes **group 0, half-open rows [0,2048)**: exactly **16,384 input rows**.
Files reuse the historical, hash-ranked M file identities to reuse real footers;
the new operational prefix windows are disjoint from all M confirmation windows
and the development files. This is not the 4,096-row confirmation sample.
There is no outcome-based replacement or adaptive start. Prefix sampling is
clustered and is not represented as an unbiased corpus sample.

Expected counts, not acceptance requirements: science **96**, practical **468**,
prose **1,488**, unassigned **180**, rejected **14,152**. Live execution must
record actual results, including zero retained science.

One shared probe uses the first file's rows **[0,256)**. One raw fetch feeds the
three adapters. It overlaps the first calibration window and is excluded from
calibration totals; the current fetcher does not share byte caches between those
two plans. The modeled full first group plus footer/read-ahead is 27,503,841
bytes, so **128 MiB** comfortably exceeds twice that modeled cost. The explicit
formula is `max(128 MiB, ceil(2 * modeled_group_bytes / MiB) * MiB)`.

**Newly confirmed execution blocker:** 101 leaves plus text buffer reads and
footer reads imply about **220 physical requests** with one redirect per open.
The dry probe has **330 requests**, one retry, 15-second request timeouts,
600-second deadline, one worker, 4 MiB window buffers, 32 MiB parser cap,
512 MiB decompressed, 1 GiB scratch and 2 GiB raw-output limits. All eight
calibration plans use the same bounded design. They exceed C13's 100-request
pilot ceiling, so the CLI correctly classifies them as **production**, requiring
prior admission and matching plan authorization. Raising only the byte budget
does not solve this. Generic `data probe --budget-mib 128` is unsupported (max 32),
and its current call has no Essential extractor contract. No such invalid command
is offered as a solution.

The existing source-probe/admission bootstrap must be closed using a bounded
schema probe or explicitly reviewed adoption of the existing real footer
evidence. The implementation does not waive C13 or fabricate that evidence.
Thus `probe_plan_ready=false`, despite a complete bounded dry probe specification.

The new offline measurement command binds frozen plans, complete journals, raw
hashes, source validators, row identities and canonical document summaries.
It records actual input/scanned rows, files/crawls, physical response-body bytes,
decompressed bytes, journal elapsed wall time including restart downtime, five
selector outcomes, malformed rows, retained documents/UTF-8 bytes/characters,
and all per-component cost ratios. Shared transfer is counted once. Zero-yield
ratios are null, never fabricated. The existing bytes-per-token assumption
**3 / 4 / 5** supplies estimated tokens, not exact training tokens. Raw body
transfer is preserved without prorating it down to admitted rows.
Process peak memory is explicitly unmeasured by the journal; runtime performance
evidence remains required before production sizing. Caps are not measurements.

## Science capacity and acquisition sequence

Science quota stays **600,000,000 exact final tokens**; first pass stays
**660,000,000 estimated tokens** (10% headroom). At confirmation density,
science admission is `24/4096 = 0.005859375`, or **170.6667 input rows per
admitted science row**. Retained bytes/tokens per science row are unknown.

Eight observed files contain 660,927 rows and 2,088,498,277 physical bytes:
mean 82,615.875 rows and 261,062,284.625 bytes/file. Extrapolating those means
over 23,200 inventory files gives 1.9167B rows and 6.0566 TB of full files.
This extrapolation is **not a capacity proof or an acquisition budget**. Under
that extrapolation, the headroom target needs at least 58.77 estimated tokens
per admitted science document. That size/retention has not been observed.

After calibration, compute:

```
estimated science tokens/input row = retained science UTF-8 bytes / 4 / input rows
required input rows = ceil(660000000 / measured yield)
required files = ceil(required input rows / observed mean file rows)
expected transfer = required input rows * actual shared transfer/input rows
```

The executable model also reports an estimated inventory deficit when positive
measurements imply more files than the adopted inventory contains. Unknowns stay
null. No quota is lowered or mixture renormalized. If capacity is insufficient,
acquisition remains blocked; other source crawls need separately frozen evidence.
[Science model](../evidence/ESSENTIAL-WEB-PRODUCTION-READINESS/science-capacity.json).

Dry sequence: admission resolution → adopted inventory → shared probe → frozen
calibration → science yield/capacity → existing `mix01_inventory estimate` and
bounded canonical pool acquisition in inventory order → later tokenizer freeze
and exact count → `sufficiency` and deterministic next-prefix top-ups only for
deficient components. Bulk byte/file budgets remain unset until measured.
[Full dry plan](../evidence/ESSENTIAL-WEB-PRODUCTION-READINESS/dry-acquisition-plan.json).

## Operator root and exact future commands

The authoritative operator root is set once in `recipes/operator/storage.json`:
`G:\XLM`. Dot-sourcing `scripts/operator_storage.ps1` derives `XLM_DATA_ROOT`,
`XLM_HOME=G:\XLM\xlm-home`, cache and temporary paths. The legacy driver resolves
its checkout from `$PSScriptRoot` and its data root from `XLM_DATA_ROOT` or an
explicit parameter. It refuses the obsolete Essential 1,000-row flow, and its
unverified `data/v1/train/00001.parquet` defaults were removed.

Future operator commands, **not executed and currently blocked**:

```powershell
# Start a process-local operator shell if scripts are disabled by the default policy:
powershell -NoProfile -ExecutionPolicy Bypass
Set-Location -LiteralPath 'F:\Project\xlm-data-ultrax'
. .\scripts\operator_storage.ps1
$E = 'docs/implementation/evidence/ESSENTIAL-WEB-PRODUCTION-READINESS'
# Read the blockers and admission decisions first. These scripts require their resolution.
& "$E/future-probe.ps1"
& "$E/future-calibration.ps1"
Get-Content "$env:XLM_DATA_ROOT/calib/essential-web-production/probe/measurement.json" -Raw
Get-Content "$env:XLM_DATA_ROOT/calib/essential-web-production/calibration/measurement.json" -Raw
```

The scripts contain every exact supported `data plan/fetch/verify/adapt/status`
argument and hash. They share one physical fetch across views, stop on every
nonzero native exit and restore offline settings after each fetch. Output roots
are derived from the central config; no generated command targets `X:\XLM`.
They do not install dependencies or grant source admission. Re-running a completed
adapt path still follows the CLI's refusal-on-existing-output behavior; the
general calibration adoption helper remains available for a separately reviewed
resume. No full campaign is an acceptance test.

**Exact next operator action:** inspect `admission-decisions.json` and supply the
missing revision-bound license/provenance/attribution and benchmark review.
Close the documented C04 schema-probe bootstrap before executing either live
script. Merely approving these dry plans cannot satisfy missing source evidence.

## Readiness and requirement ledger

| Check | Value | Status |
|---|---|---|
| selector_frozen | true | VERIFIED |
| selector_integration_ok | true | VERIFIED: both reproductions and adapter tests |
| source_revision_ok | true | VERIFIED |
| production_admission_ok | false | BLOCKED: evidence and reviews above |
| inventory_ready | true | VERIFIED: eight-crawl scope, unknown sizes disclosed |
| malformed_policy_ready | true | IMPLEMENTED, VERIFIED |
| transfer_strategy_ready | true | VERIFIED model; actual cost NOT RUN |
| probe_plan_ready | false | BLOCKED: C04/C13 bootstrap |
| calibration_plan_ready | true | VERIFIED dry 8 × 2,048 freeze; execution gated |
| acquisition_plan_ready | false | BLOCKED: admission and measured science capacity |
| live_probe_run | false | NOT RUN |
| live_calibration_run | false | NOT RUN |
| CUDA / full acceptance / bulk acquisition / tokenizer | — | OUT OF SCOPE, NOT RUN |

Offline checks: 301 passes and one measurement-helper import-shadowing failure
in the broad focused selection; repaired, then 90 readiness/selector checks pass.
The final repair selection passed 76 checks (33 readiness plus 43 driver/adoption).
The subsequent PowerShell root regression additionally verifies the actual setup;
parameter defaults were moved into the script body for Windows PowerShell 5.1.
Final narrow readiness/root verification: **35 passed**, exit 0, 10.05 s.
Ruff check/format and scoped strict interpreted mypy pass. Full/fast repository
acceptance selections were not run. Exact counts, commands and exit statuses,
including intermediate failures, are in COMMANDS.md. Authored fixtures dominate;
the separate selector reproductions use real stored metadata and certification
tests use three previously stored real rows. No mock is claimed as live compatibility.

Resource evidence: eight verified footer payloads total 1,398,384 bytes; dry
artifacts including inventory are about 4.9 MB. Offline process peak memory was
not measured. Live throughput, prefix transfer, wall time, malformed prevalence,
retention and science capacity remain unmeasured.

Files changed: admission/CLI binding, Essential adapter and rejection handling,
malformed counter, reusable inventory freeze/empty-yield measurement, offline
readiness/model/measurement scripts, central operator root and legacy driver,
focused tests, this evidence/report, STATUS, P13 and Windows runbook. The only
unrelated CLI line change removes an obsolete NumPy import type-ignore exposed
by strict mypy; runtime behavior is unchanged. Mix-01/UltraX configurations,
selector evaluator/spec and dependency lock files have no changes. Exact artifact
and code hashes are in `artifact-manifest.json`.

Local implementation commit: `57cb42f` (`feat: harden Essential-Web production
acquisition path`); the following documentation commit freezes this report and
the readiness package. Neither commit is pushed.

**ESSENTIAL-WEB PRODUCTION READINESS BLOCKED**
