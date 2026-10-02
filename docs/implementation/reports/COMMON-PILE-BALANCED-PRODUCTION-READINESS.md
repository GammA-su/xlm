# Common Pile Balanced calibration-authorization handoff (2026-10-02)

The offline implementation is ready for the operator's bounded calibration
authorization. No calibration fetch, metadata probe, admission, production
policy/plan/authorization/run, C05, tokenizer, training or push ran in this
session. Nothing under the operator's real-data roots was modified.

Starting HEAD: `2cde18d8016551115b212f87ecec8a35b3d289d1`, clean worktree
`F:\Project\xlm-common-pile`, branch `feat/common-pile-allowlist`.
The final commit contains this report; resolve it with `git rev-parse HEAD`
after the commit. The final chat handoff supplies its exact SHA. The prior WIP
commit is preserved. Unrelated edits in `F:\Project\xlm-data-ultrax` were untouched.

Evidence directory: [COMMON-PILE-BALANCED-PRODUCTION-READINESS](../evidence/COMMON-PILE-BALANCED-PRODUCTION-READINESS/).
Evidence artifacts contain no real corpus text. `changed-files.txt` lists every file
changed in this continuation; `validation-results.json` contains exact expanded
commands, statuses, environment and sampled resource measurements.

## Verified identities and real evidence

| Identity | Value |
|---|---|
| Repository | `common-pile/comma_v0.1_training_dataset` |
| Revision | `5afc546db324e7f39f297ba757c9a60547151e7c` |
| Allowlist | `b2bb7c0dc532a6263ca17b78816186fa194c12c09c74614baaeb29c76cdaab04` |
| Production inventory | `c0984aa33fb3598a9e724b9518df8cd8a0f2af52b5708ec211fb7fd693df7637` |
| Seed / volume | 20260918; 384 files, 64/component, 8,131,797,849 compressed B |
| Cert02 receipt | `f52a0622a138575835307f0b430482f92debdf7d0aeb7e780db663f53cdacfe1` |
| Evidence matrix SHA-256 | `3066a5098d585dbadbc8042427101e28c52684c6fb471533df1818dd1e9dbbbd` |
| Adapter v2 module SHA-256 | `89ae45abf7a2fd8e533a5d50ef6e37ae99e4ef5c701c365ad854cdb36157a05a` |
| Frozen v1 module SHA-256 | `3651ff2af4fcb46c207404e052caa59e1ec42f2ac5a429e5dec752f7e83a7ef6` |
| Frozen columns module SHA-256 | `3e594f755eea797ac0d29d819c28b4ccf8e387cfc7889470dfc58e8657e58db1` |

The immutable components remain **libretexts, news, oercommons, pressbooks,
project_gutenberg, public_domain_review**. The production inventory re-derived
exactly from the existing allowlist and discovery listing (exit 0).

`audit_local.py` replayed the previously saved 9 + 48 real rows, read-only:
libretexts/news/public_domain_review 3 each; oercommons/pressbooks/Gutenberg
16 each. All 57 have exactly a string `text`; all accepted rows serialize
identically under v1/v2. Cert02 document digests reproduced exactly. This is
offline replay of previously acquired real evidence, not a new live-source
test and not full-corpus certification. Corpus rows stayed at their original
D:/G: locations; only file hashes and counts entered `local-audit.json`.
Gutenberg's previously observed roughly 67–129 KiB segments still need
lineage-aware C05 handling; no book IDs were invented.

## WIP audit and implementation

Initially completed: immutable allowlist/inventory, sampled schema evidence,
v2 adapter, generic whole-file gzip transport and Windows path-prefix fix.
Initially incomplete/untested: component calibration, evidence translation,
measurement validation, source registration, bounds, component quota policy,
null-license admission basis and their integration tests.

The exact five original Ruff failures were all E501:

| Original location | Width | Fix |
|---|---:|---|
| `verify_operator_artifacts.py:10` | 118 | split documented command across two lines |
| `scripts/component_calibration.py:102` | 101 | adjacent f-string segments |
| `scripts/component_calibration.py:106` | 102 | adjacent f-string segments |
| `component_calibration.py:90` | 104 | split refusal message |
| `component_calibration.py:211` | 101 | split proposed-bound rule string |

No broad lint suppression was added. Format initially already passed for
the 19 WIP Python files. All changed Python files since `3473324` are now
covered by Ruff, formatter and strict mypy.

Calibration now checks allowlist/inventory integrity, exact component coverage,
source/revision/file identities, strong validators, positive useful samples,
accepted/rejected/row accounting, decoded and canonical bytes, receipt totals
and deterministic reconstruction. Embedded text-free receipts make a stored
calibration reproducible. Inventory weighting preserves each component's
density; a Gutenberg outlier cannot become the other five components' rate.
Actual transferred bytes and decoded bytes are separate from compressed bytes
fed through the last row. `measurement()` discloses rounded weighted estimates;
`mix01_inventory record` verifies the embedded calibration, current allowlist,
current inventory and stored calibration instead of trusting a digest-shaped
string. Legacy measurement paths retain their historical keys and behavior.

Evidence translation rejects missing/excluded/duplicate components or files,
duplicate row identities, foreign revisions, changed rows/receipts and stale
document digests. It re-runs the current adapter before creating facts and
retains `declared_license=None`. The bridge binds the current adapter code.

`common_pile` is registered as source `common_pile`, view/logical component
`common_pile_prose`, adapter `common_pile`, component calibration kind.
Only its mutable catalog revision was pinned; no snapshot was edited.
`layout_of` loads `G:\XLM\calib\common_pile_prose\component-calibration.json`.
Policy modeling uses observed prefix-transfer duration/bytes, the explicitly
disclosed fallback adaptation rate of 5,814 rows/s, and only
`whole_file_local` / `small_source_direct`. These are estimates, not measured
production throughput. No Parquet model is offered for Common Pile.

The reviewed-bounds artifact binds source/view/revision, allowlist, production
inventory and calibration. It explicitly covers file/decoded/ratio/row/line,
canonical/durable/ledger, processing growth, scratch and deadline ceilings.
It validates internal resource consistency and covers the largest inventory
file. It is passed into planning and benchmarks; no global bounds dictionary
is mutated. **No real reviewed bounds were recorded.**

The write-once component split supports preview/record/show. B/C record exact
six-component final and first-pass quotas, totaling 300M and 330M. A requires
the explicit `{"strategy":"hash_prefix"}` choice. B/C select independent
component prefixes, keep cursors across top-ups, and use per-component sealed
receipt totals for sufficiency. Repair preserves the split and cursors;
changed split/bounds alter plan identity. Excluded files cannot enter.
**No allocation decision was recorded.**

C04 now permits only the pinned Common Pile source to present
`component_allowlist_review` with a null declared repository license, exact
Balanced allowlist and current evidence-matrix hash. The operator still
chooses approve/reject for research pretraining and approved/rejected
provenance, with the existing four reviews and C05 mitigation obligation.
The exception also requires the exact `common_pile_prose` view.
No synthetic repository/row license is created; unrelated null-license
sources stay blocked. Tests exercise the approval path, rejection, absent
basis and evidence-matrix/revision drift.

## Transport and resume audit

`mix01-source-raw-artifact-jsonl-gz-v1` retains the verified compressed source.
Suffix identification is exact; mixed-format plans, row ranges and row-group
parallelism refuse. Download retains existing allowlists, requests/retries,
bytes, validators and SHA-256 verification. Decoder enforces gzip members,
CRC/truncation, strict UTF-8, bounded lines/rows/expansion and JSON objects,
duplicate-key refusal and finite numbers. This audit removed an unintended
1 MiB expansion-ratio exemption and added refusal of numeric overflow
(`1e999`). Canonical outputs, ledgers, processing growth, scratch/durable
storage and the run deadline remain bounded; incomplete units do not publish.

Compressed downloads may resume from their verified byte checkpoint.
Decompression always restarts at byte zero of the verified complete file.
Failed adaptation retains that compressed file for a local retry, without
an unnecessary download. The Windows `plain_resolve` fix is preserved:
extended `\\?\` and UNC prefixes are normalized before containment checks.
The related runner/reservation tests pass; no unsupported flake claim is made.

Prefix sampling now refuses before following a redirect beyond its request
budget and never consumes a sentinel body byte beyond the authorized range.
Existing sample destinations refuse before refetch. Output and aggregate
decoded-byte ceilings are explicit. It makes no automatic retries; after a
failure the operator stops and reviews spent transfer before another attempt.

## A/B/C mixture analysis — estimates, no decision

`strategy-analysis.json` recomputes from the frozen inventory and **saved
publisher Comma-token counts**, not measured XLM token capacity. Calibration
must replace these proxies. Canonical proxy = publisher tokens × 4.
The two existing benchmark-reserved ranks reduce eligible PressBooks/PDR
volume slightly. The final exact tokenizer and C05 may reduce capacity again.

| Component | Available token proxy | Available canonical proxy B | A final / first-pass proxy tokens | B final / first-pass tokens | C candidate final / first-pass tokens |
|---|---:|---:|---:|---:|---:|
| libretexts | 93,000,000 | 372,000,000 | 1,563,956 / 1,720,352 | 50,000,000 / 55,000,000 | 50,000,000 / 55,000,000 |
| news | 64,000,000 | 256,000,000 | 3,295,953 / 3,625,548 | 50,000,000 / 55,000,000 | 50,000,000 / 55,000,000 |
| oercommons | 12,000,000 | 48,000,000 | 463,855 / 510,240 | 50,000,000 / 55,000,000 | 9,486,165 / 10,434,782 |
| pressbooks | 140,000,000 | 560,000,000 | 2,362,628 / 2,598,890 | 50,000,000 / 55,000,000 | 50,000,000 / 55,000,000 |
| project_gutenberg | 5,700,000,000 | 22,800,000,000 | 292,229,909 / 321,452,901 | 50,000,000 / 55,000,000 | 139,188,056 / 153,106,861 |
| public_domain_review | 1,700,000 | 6,800,000 | 83,699 / 92,069 | 50,000,000 / 55,000,000 | 1,325,779 / 1,458,357 |

Required canonical bytes per component are exactly four times the first-pass
column; all values are also explicit in the JSON artifact. B requires 220M B
each. C requires respectively 220M, 220M, 41,739,128, 220M, 612,427,444 and
5,833,428 B; total 1,320,000,000 B.

**A:** the current mean-file hash prefix selects 25 files (6 Gutenberg),
approximately 2,188,142,186 canonical B / 547.036M token proxy, overshooting
the 330M target through whole-file granularity. Gutenberg is 94.560% of the
available compressed inventory, 97.204% of selected compressed bytes and
97.410% of selected canonical bytes. The A token table assumes later uniform
down-selection preserves those shares; it is not an exact-token guarantee.
Strength: simplest selection, few large files. Weakness: little effective
prose diversity despite the allowlist. Complexity: one cursor.

**B:** 50M final / 55M first-pass per component, Gutenberg 16.667%.
OER Commons and PDR are volume-limited under the saved estimates; news is
close to its 63.25M requirement including 1.15 safety. No repetitions or
renormalization are assumed. Strength: equal component representation.
Weakness: estimated infeasibility. Complexity: per-component accounting and
later exact-token quotas.

**C:** equal opportunity for the five complementary core components, capped
by each one's eligible capacity / the existing 1.15 safety factor; Gutenberg
fills the residual. Thus first-pass core share = min(55M, eligible capacity /
1.15); final shares divide by 1.1, with deterministic integer residue assigned
to Gutenberg. This is a capacity rule, not arbitrary percentages. Gutenberg
is approximately 46.396%. Strength: preserves substantial instructional/news
prose and uses all safely estimated tiny-component capacity. Weakness: depends
on biased volume estimates and cannot guarantee post-C05 capacity. Complexity:
six cursors/quotas and final-token down-selection. The operator must choose
A, B or C **after calibration**; none is selected here.

## Exact calibration targets and ceilings

All targets are the first two files of their component in the frozen hash
order. Chunk size is **131,072 B**, maximum **128 requests/file including
redirects**, per-file configured body cap **4,194,304 B**, timeout 30 s and
deadline 600 s/file. Small files may end before the row target: then the
complete gzip stream must verify and the receipt reports the actual row count.
No row count or sampled volume is invented.

| Exact inventory path | Row target | Effective maximum transferred B |
|---|---:|---:|
| `libretexts/libretexts.chunk.39.jsonl.gz` | 256 | 1,806,742 |
| `libretexts/libretexts.chunk.40.jsonl.gz` | 256 | 1,939,463 |
| `news/news.chunk.29.jsonl.gz` | 256 | 1,543,613 |
| `news/news.chunk.44.jsonl.gz` | 256 | 1,527,147 |
| `oercommons/oercommons.chunk.49.jsonl.gz` | 256 | 336,135 |
| `oercommons/oercommons.chunk.24.jsonl.gz` | 256 | 294,589 |
| `pressbooks/pressbooks.chunk.18.jsonl.gz` | 256 | 2,995,813 |
| `pressbooks/pressbooks.chunk.26.jsonl.gz` | 256 | 3,206,053 |
| `project_gutenberg/project_gutenberg.chunk.43.jsonl.gz` | 32 | 4,194,304 |
| `project_gutenberg/project_gutenberg.chunk.27.jsonl.gz` | 32 | 4,194,304 |
| `public_domain_review/public_domain_review.chunk.26.jsonl.gz` | 256 | 69,642 |
| `public_domain_review/public_domain_review.chunk.04.jsonl.gz` | 256 | 61,343 |

Destinations: the ten non-Gutenberg samples go to
`G:\XLM\calib\common_pile_cal01`; the two Gutenberg samples go to
`G:\XLM\calib\common_pile_cal02`. Requested maximum: 2,624 rows.
Inventory-constrained maximum sample body transfer: **22,169,148 B**;
configured command caps sum to 48 MiB. Maximum sample requests: **1,536**.
Metadata probe: **8,388,608 B**, 20 requests, 10 s/request, 60 s overall,
no automatic retries; pinned through the catalog, with a persistent probe ID.
Combined body-byte ceiling: **30,557,756 B**; request ceiling **1,556**.
HTTP headers/TLS traffic are not body-byte accounting. Only exact pinned
source targets and the existing Hugging Face host/redirect allowlist apply.

Each sampling command caps saved row bytes and aggregate decoded samples at
128 MiB; gzip expansion remains 15× and lines 1 MiB. The sampler keeps bounded
samples in memory and writes no dataset cache or complete large shard. The
per-command worst-case transient sample and serialized buffers can coexist;
reserve 1 GiB RAM and 512 MiB disk for this bounded operator task, plus the
metadata probe's existing bounded journal/store allowance. No performance or
peak-memory measurement of the future network acquisition is claimed.

**Copy/paste the complete reviewed PowerShell block from
[operator-calibration.ps1](../evidence/COMMON-PILE-BALANCED-PRODUCTION-READINESS/operator-calibration.ps1).**
It contains all twelve literal paths, both sampler commands, exact environment
transitions, error checks and this metadata command:

```powershell
uv run --offline --locked --no-sync --extra cpu --extra eval python -m xlm.cli.main data probe --source common_pile --view common_pile_prose --catalog manifests/datasets.catalog.yaml --live --budget-mib 8 --probe-id common-pile-balanced-cal01 --publish --json
```

The `.ps1` is an artifact for review, not something the coding agent executed.
This machine blocks dot-sourcing scripts by execution policy; the prepared
block sets the established paths explicitly and can be pasted into PowerShell.
No execution-policy bypass was used. The final `finally` restores both
`HF_HUB_OFFLINE=1` and `HF_DATASETS_OFFLINE=1`.

## Gate ledger at this handoff

| Requirement | Status | Evidence / next action |
|---|---|---|
| Frozen Balanced allowlist | VERIFIED / PASS | re-derived against listing and matrix |
| Production inventory | VERIFIED / PASS | exact 384 files and expected digest |
| Six-component sampled schema | VERIFIED / PASS | read-only replay of 57 prior real rows |
| Adapter v2 and gzip transport | IMPLEMENTED, VERIFIED | focused and related offline tests |
| Calibration/evidence/measurement code | IMPLEMENTED, VERIFIED | authored six-component tests |
| Registration/catalog/layout/mode filtering | IMPLEMENTED, VERIFIED | CLI and policy tests |
| Reviewed-bounds and split mechanisms | IMPLEMENTED, VERIFIED | identities, constraints, top-ups |
| C04 component-license basis | IMPLEMENTED, VERIFIED | narrow null-license path and explicit review |
| Real component calibration | BLOCKED / NOT RUN | operator authorization and sampling |
| Real reviewed bounds | BLOCKED / NOT RECORDED | review calibration first |
| Operator A/B/C choice | BLOCKED / NOT RECORDED | compare calibrated capacities |
| Live metadata probe | BLOCKED / NOT RUN | absent in the actual existing store |
| License/provenance operator review | BLOCKED / NOT RECORDED | explicit decisions after evidence |
| Admission | BLOCKED / NOT DONE | no current Common Pile bridge/decision |
| Production policy/plan | BLOCKED / NOT CREATED | prerequisites refuse before writing |
| C05/tokenizer/training | OUT OF SCOPE / NOT RUN | later operator stages |
| Push | NOT RUN | not authorized |

`evidence show` exits 1: six-component bridge inputs need the new
`--sample-dir` receipts and saved rows. This does not negate the 57-row
historical schema replay; the three earlier components lack the required
calibration file/volume receipts.

`plan` exits 1 with exactly:

> component calibration missing: G:\XLM\calib\common_pile_prose\component-calibration.json; authorize the twelve bounded prefix samples, then run scripts/component_calibration.py build

The refusal logs and exact commands are committed. No production plan was
created. Later gates verify bounds, split, certification, live metadata and
current admission/adapter identity; an absent input never invokes a fallback.
They rebuild and compare the published bridge before accepting the current
review. Both authorization and execution compare the current bounds/split
against the plan, so a post-authorization artifact change refuses before work.

## Validation, resources and historical identities

Windows 11, Python **3.12.13**, existing `uv.lock`, CPU + eval extras,
`uv run --offline --locked --no-sync`. Existing `pyproject.toml`, `uv.lock`
and `.python-version` were preserved; CPU/CUDA installation policy remains
in the Windows/Linux runbooks with mutually exclusive extras. CUDA was not run.
Test-run OMP/MKL/OPENBLAS/NUMEXPR threads = 1; tokenizer parallelism disabled.
One xdist controller, 16 workers, worksteal, no worker restarts.

* Initial focused calibration/evidence: 22 passed (exit 0).
* Focused integrated regressions after exact blocker-test updates: 126 passed (exit 0).
* First wider run: 604 passed, 2 failed (exit 1), retained in `related-tests.log`.
  Two IFM tests assumed only IFM had registry overrides. They now explicitly
  verify both IFM and Common Pile v2 while checking all other frozen identities.
  These failures were investigated and fixed, not labeled pre-existing.
* Final authored related selection: **579 passed**, no skips, exit 0,
  25.735 s wrapper wall time, sampled process-tree peak RSS **3,852,455,936 B**.
  The complementary serial selection contained zero nodes (579 deselected,
  exit 5). It is **NOT RUN**, not a pass. This is not a whole-repository audit.
* Additional explicit hash-prefix-choice regression: 1 passed, exit 0 (0.61 s).
* After the final gate audit: **80 focused tests passed**, exit 0 (5.95 s
  pytest; 6.547 s wrapper), sampled process-tree peak RSS **334,778,368 B**.
  This includes the hash-choice regression and five new gate regressions;
  counts overlap the earlier selection and must not be added as unique tests.
  Bounds/split drift refuses at authorization and run; readiness verifies the
  current bridge before admission. Wrong-view null-license evidence refuses.
  `final-gate-checks.json` and `gate-*.log` retain the exact five-module command,
  resource measurements and repeated successful static/diff checks.
* Ruff, format check, strict mypy over all 34 Python files changed since
  `3473324`: exit 0. Diff check: exit 0. No suppressed static failures.
  Staging exposed CRLF endings and trailing whitespace in generated logs;
  these were normalized to UTF-8/LF without trailing spaces, generators updated, and the static
  and staged-diff checks repeated successfully. No evidence values changed.
* The original broader selection also replayed existing FinePDFs/SimpleStories
  local certification tests. No acquisition occurred. The final authored-only
  selection keeps those separate from synthetic unit/integration evidence.
* `audit_local.py`: exit 0. Actual stored admission gates for **UltraX,
  FinePDFs, SYNTH, Wiki Rewrite, FineWiki, IFM General, IFM Planning and
  SimpleStories all PASS**, reproducing the stored bridge evidence and adapter
  identities. Exact historical digests are in `local-audit.json`.

No unresolved/pre-existing test failure is claimed. Disk/performance limits of
future production remain unmeasured; real prefix samples cannot certify
worst-case full-shard sizes. Bounds remain an explicit operator review.

## Next operator steps and continuation

1. Review this report, the preview JSON and `operator-calibration.ps1`; authorize
   only the bounded twelve samples and metadata probe if acceptable.
2. Personally paste/run that PowerShell block. On failure stop; do not retry
   until green or silently raise a limit. Preserve the receipt and spent-cost evidence.
3. **STOP after the metadata probe and offline environment restoration.**
   Return the two sample receipts and probe outcome for offline review. Do not
   admit, select A/B/C, record bounds, freeze policy, plan/authorize production,
   run C05, fit a tokenizer or train as part of that block.
4. The subsequent offline build/show/measurement/estimate commands and exact
   preview/record/show interfaces are in the updated
   [source-acquisition runbook](../../runbooks/mix01-source-acquisition.md).
   Bound values and operator allocation percentages are deliberately unfilled.

Continuation prompt:

> Continue on F:\Project\xlm-common-pile, feat/common-pile-allowlist, from the
> newest commit. Read COMMON-PILE-BALANCED-PRODUCTION-READINESS.md and its
> validation/local-audit/authorization-preview artifacts. The operator has run
> the prepared Common Pile cal01/cal02 samples and pinned metadata probe.
> Verify their receipts and saved rows offline; build the six-component
> calibration and measurement, re-evaluate A/B/C capacity, and prepare concrete
> reviewed bounds and the operator allocation decision. Preserve the immutable
> allowlist/inventory and all historical source identities. No further network,
> admission, production policy/plan authorization, production run, C05,
> tokenizer, training or push without the corresponding operator instruction.

COMMON PILE BALANCED SOURCE READY FOR CALIBRATION AUTHORIZATION

---

## Historical WIP handoff (superseded by the continuation above)

Worktree `F:\Project\xlm-common-pile`, branch `feat/common-pile-allowlist`, on
top of `3473324`. The session was stopped early because the operator ran out
of quota. This report is the handoff to the next agent. No production run, no
admission, no plan or authorization, no C05, tokenizer or training, no push.

## Done

1. **Operator artifacts verified (Task 1)** with
   `evidence/COMMON-PILE-BALANCED-PRODUCTION-READINESS/verify_operator_artifacts.py`
   (output `operator-artifacts.json`).
   - Allowlist digest `b2bb7c0dc532a6263ca17b78816186fa194c12c09c74614baaeb29c76cdaab04`
     (operator GammA): exactly libretexts, news, oercommons, pressbooks,
     project_gutenberg and public_domain_review; no accepted flags; the
     evidence-matrix sha256 matches the committed matrix.
   - Production inventory digest
     `c0984aa33fb3598a9e724b9518df8cd8a0f2af52b5708ec211fb7fd693df7637`:
     384 files (64 per component), 8,131,797,849 B. It re-derives exactly from
     listing `a32b9b12…5038` plus the allowlist, with seed 20260918 and
     revision `5afc546d…1e7c`.
2. **Real-row certification (Task 2, authorized, network)** with
   `scripts/jsonl_gz_sample.py`, receipt `certification-cert02.receipt.json`,
   digest `f52a0622…cfe1`. The corpus rows are stored only at
   `G:\XLM\calib\common_pile_cert02\real-records.jsonl`.
   - The first 16 rows of `oercommons/oercommons.chunk.49`,
     `pressbooks/pressbooks.chunk.18` and
     `project_gutenberg/project_gutenberg.chunk.43`, read by sequential
     128 KiB `Range` requests from byte 0. No whole file was read.
   - Totals: 16 requests (8 ranges + 8 redirect hops) and 1,048,576 B
     transferred: 131,072 B, 131,072 B and 786,432 B. One extra metadata API
     call confirmed HEAD = `5afc546d…1e7c`.
   - All 48 rows are exactly `{"text": str}`, and the adapter accepted 48/48.
     Decompression amplification: 3.18, 3.14 and 2.70.
   - Gutenberg rows are 67–129 KB of text (median about 128 KB): books appear
     as fixed-size segments, which is relevant to C05 lineage.
   - With the earlier 9 rows (news, libretexts, public_domain_review), all six
     Balanced components show the same `{"text"}` schema in real samples.
3. **Adapter contract v2 (Task 3):** `src/xlm/data/adapters/common_pile_adapters.py`,
   registered in `registry.py` (the frozen `mix01_adapters.py` is untouched).
   - A blank string `text` is a recorded `CommonPileEmptyTextError`.
   - Absent, null or non-string `text` stays a fatal `MissingFieldError`.
   - Valid text is verbatim and byte-identical to v1.
   - Provenance: the upstream component, source file, source row and revision.
   - The adapter code identity changes, so evidence must be renewed (tested).
4. **`.jsonl.gz` production transport (Task 4):**
   - `jsonl_gz.py`: a bounded, fail-closed decoder. It handles multiple gzip
     members, CRC and truncation, trailing garbage, absolute and relative
     expansion bounds, line and row bounds, strict UTF-8, single JSON objects,
     no duplicate keys and no NaN.
   - `source_formats.py`: format by exact suffix; representation
     `verified_source_jsonl_gz` and contract
     `mix01-source-raw-artifact-jsonl-gz-v1`; scratch name `.jsonl.gz.part`.
     Parquet keeps every historical name and byte.
   - `source_local.py`: whole-file serial decode with the same serialization,
     adapter, ledger and summary as Parquet. A row range or row-group
     parallelism is refused.
   - `source_run.py` and `source_benchmark.py`: format-aware identity,
     durable reuse, scratch names, receipts and seal.
   - `source_plan.check_format_modes`: refuses mixed formats; `.jsonl.gz` is
     allowed only in `whole_file_local` or `small_source_direct`, without
     row-group parallelism.
   - **Resume semantics:** download resume is byte-level on the compressed
     file (Range/If-Range from a verified checkpoint, unchanged). Decompression
     never resumes mid-stream; it always runs from byte 0 of the
     SHA-256-verified local file, and a failed decode keeps the download for a
     local retry.
   - Unchanged frozen EW files: `source_parquet.py`, `essential_web_local.py`,
     `columns.py` and the adapter files.
5. **Pre-existing flake fixed.** The intermittent runner-test failure
   `reservation path escapes its budget root` came from Windows non-strict
   `Path.resolve()` keeping a `\\?\` prefix for a vanishing
   `*.state.json.tmp`. The fix, `source_reservations.plain_resolve`, took the
   result from 4/6 failing on base to 6/6 passing.
6. **Calibration tooling (Task 6, code only, untested):**
   - `component_calibration.py`: per-component rates, inventory-weighted
     combined estimate, proposed per-file bounds (max over components x1.35),
     planner layout and measurement file.
   - `scripts/component_calibration.py`: the build/show CLI.
   - `mix01_inventory.py record --measurement` accepts the
     `component_weighted_prefix_samples` basis.
7. **Evidence translation (Task 8, code only, untested):**
   `src/xlm/data/sources/common_pile_evidence.py` turns prefix-sample receipts
   and saved rows into `CertifiedFacts` bound to the allowlist, inventory and
   receipts. It establishes no license.

Tests (focused, `uv run --offline --locked`):
- `tests/test_jsonl_gz.py`: 36 passed.
- `tests/test_source_jsonl_gz_run.py`: 11 passed, run with `-n 8` alongside
  `test_source_repair.py` and `test_source_file_bounds.py`; 6 repeated runs at
  31 passed each.
- Parquet regressions (`test_source_run`, `test_source_repair`,
  `test_source_file_bounds`, `test_ifm_empty_text_recovery`,
  `test_source_rowgroups`, `test_mix01_source_cli`): 104 passed, plus the
  flake, now fixed.

Not run: mypy, a full ruff run over the final state, the related suite, and
any tests for items 6–7.

## NOT done / remaining (handoff)

1. **Tests for the untested modules:** `component_calibration.py`,
   `scripts/component_calibration.py`, `common_pile_evidence.py`, and the
   `mix01_inventory` component basis. Then ruff, format and
   `mypy --strict` on every changed file, and the related suite (allowlist,
   inventory, source_* and mix01_source_cli).
2. **Task 5, source registration (not started in code):**
   - Pin the catalog revision: in `manifests/datasets.catalog.yaml`, the
     `common_pile` entry has `"revision": null`; set it to
     `5afc546db324e7f39f297ba757c9a60547151e7c`. Never edit `snapshots/`.
   - In `scripts/mix01_source.py`, add
     `SourceSpec("common_pile", "common_pile_prose", "common_pile", "common_pile_prose", None)`
     with a component calibration kind and remove the `BLOCKED` entry. Update
     the tests that assert the block:
     `test_component_allowlist::test_common_pile_stays_blocked_for_every_production_command`
     and the `status --source-key common_pile` assertion in
     `test_mix01_source_cli.py`.
   - `layout_of` for the component kind: load and verify
     `<data-root>\calib\common_pile_prose\component-calibration.json` and use
     `component_calibration.layout_of`. Perf comes from `observed_transfer`;
     the adapt rate is `FALLBACK_ADAPT_ROWS_PER_SECOND`.
   - `evaluate_policy`: keep only `planner.JSONL_GZ_MODES` models.
   - `cmd_plan`: refuse until `planner.SOURCE_FILE_BOUNDS[("common_pile", "common_pile_prose")]`
     holds the reviewed bounds from the calibration.
   - `build_bridge`: for the component kind, use
     `common_pile_evidence.translate_component_samples`, with a new
     `--sample-dir` option (repeatable; each holds `sample-receipt.json` and
     `real-records.jsonl`).
3. **Calibration (Task 6) needs operator authorization (network); NOT run.**
   Proposed plan:
   - Sample libretexts, news and public_domain_review (no receipts exist for
     them; the 9-row cert01 has no file identities).
   - Take larger samples for all six components (for example 256 rows of the
     first 2 inventory files of each small component, and 32 rows of 2
     Gutenberg files).
   - Then run `scripts/component_calibration.py build`, then
     `mix01_inventory.py record --source common_pile_prose --measurement …`,
     then `estimate`.
   - Example (do not run without authorization):
     `uv run --offline --locked --extra cpu --extra eval python scripts/jsonl_gz_sample.py --source-key common_pile --data-root G:\XLM --label common-pile-cal01 --rows 256 --authorized-components libretexts,news,public_domain_review,oercommons,pressbooks --target <first 2 inventory files per component> --chunk-bytes 131072 --output-dir G:\XLM\calib\common_pile_cal01`
     plus a separate `--rows 32` run for project_gutenberg.
4. **Task 7, dominance audit (not re-run):** the previous estimate stands.
   Under the current hash-ordered prefix, the first Balanced plan is about 97%
   Gutenberg by bytes (25 files: 6 Gutenberg; libretexts, news, oercommons,
   pressbooks and pdr together about 3%).
   - A per-view split, analogous to the IFM requirement split (an
     instructional core taken whole plus Gutenberg for the remainder), should
     be designed and offered. It was NOT designed in code and NOT recorded.
     Compare A (hash order), B (equal share) and C (prose-balanced); the
     operator chooses.
   - Option B is volume-limited: the core components hold only about 1.24 GB
     estimated canonical in total.
5. **Task 8, evidence/admission is blocked by policy, not only by code:**
   - The repository declares no license, so `ce.build_bridge` and the C04 gate
     (`policy.evaluate_license_review`) refuse.
   - Admission needs a C04 contract amendment defining a per-component license
     basis (the allowlist digest) — an operator decision. Do not write a
     synthetic `declared_license`.
   - It also needs a real `xlm data probe --live` metadata probe in the store.
6. **Task 9:** after items 2–5: `policy freeze`, then `plan`, STOP at PLAN
   DIGEST. Docs to update: the runbook Common Pile section, `CONTRACTS.md`
   (the jsonl.gz raw-artifact contract `mix01-source-raw-artifact-jsonl-gz-v1`
   is in code only; add the amendment text), and `STATUS.md`.

Final state: **COMMON PILE BALANCED SOURCE NEEDS FURTHER WORK**
