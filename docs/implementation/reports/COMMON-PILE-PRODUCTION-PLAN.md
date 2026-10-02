# Common Pile production plan handoff (2026-10-02)

**Plan 1 is ready for operator authorization. It is NOT authorized and has NOT run.**
No external network, live-source acquisition, C05, tokenizer, training or push
occurred. Authored offline tests include loopback HTTP fixtures; they are not
live-source certification. The operator's existing decisions were verified
before any mutation. The unrelated UltraX worktree was untouched.

Starting HEAD: `04b46812428f36f3bb13a9711b6dbf0d01447037`, clean
`F:\Project\xlm-common-pile`, branch `feat/common-pile-allowlist`.
The final commit contains this report; its exact SHA is supplied in the chat
handoff and resolves with `git rev-parse HEAD`. No prior commit was rewritten.

Evidence: [COMMON-PILE-PRODUCTION-PLAN](../evidence/COMMON-PILE-PRODUCTION-PLAN/).
`commands.json` and `validation.json` record exact commands, exits, wrapper
wall times and sampled process-tree RSS. `verification.json` replays real
saved evidence without corpus text. `integrity-and-selection.json` contains
cross-binding checks and unrounded arithmetic. `plan-verification.json` verifies
stored plan reconstruction, policy reproduction, resume classes and the absence
of authorization/run state. `changed-files.txt` is the exact commit manifest.

## Integrity and admission

Repository `common-pile/comma_v0.1_training_dataset`, revision
`5afc546db324e7f39f297ba757c9a60547151e7c`; source `common_pile`,
view/logical component `common_pile_prose`. Exactly the six Balanced components;
384 frozen files, 64/component, 8,131,797,849 compressed B. Inventory re-derived
from the frozen discovery listing and allowlist. No immutable decision changed.

| Artifact | Result | Verified identity |
|---|---|---|
| allowlist | PASS | `b2bb7c0dc532a6263ca17b78816186fa194c12c09c74614baaeb29c76cdaab04` |
| inventory | PASS | `c0984aa33fb3598a9e724b9518df8cd8a0f2af52b5708ec211fb7fd693df7637` |
| CAL01 | PASS | `f7d74b4a2e2614e10a8231653ef36831aecb7546955feef73d6df89f9b4be289` |
| CAL02 | PASS | `5d57e4ea82e509e28faf69aa1e7c9ee95da38bbb26e4a7367a0dea0894071b67` |
| calibration | PASS | `c8a32cced2bae62c21b4e4396d46f0803f11745117e52d468adf3912dde0541d` |
| component_split | PASS | `d64b2b6ee53c7dd22d6b0ef193f09f4fb86a8fa512a6920daf40f048baa28e77` |
| reviewed_bounds | PASS | `0f1467e65b1c0b4b79ed4bd2aeb4d0399b2995fb41c4df6472b73390b46a966c` |
| measurement SHA-256 | PASS | `a99e994e3f1d43b5f50bc659ac7b1371a55cd05282e95f48c3a933d3a9bc7a42` |
| shared calibration SHA-256 | PASS | `73a3c5ab3f3f297437d1baaae90996cd1464950c576bc07d93aa2d9be89b764a` |
| shared headroom estimate SHA-256 | PASS | `f887246171bea265d02b1cb69e9a8f5342a046c38c76ae4f76682c088aadaa6d` |
| metadata file SHA-256 | PASS | `bf2a0b51dfbcaf06531ac279f5a605d41371573a18cf5eb3e11e459d2bd74438` |
| published bridge | PASS | `12ff9163353bf6e56979dc66b66307c12e9d57dd92e38c348b4b7927c70838f3` |
| admission canonical digest | PASS | `1f3c9f6e47ff6d2306c86efe0f11aacef5bea0bac1f0a916ca7e835072e6811f` |

Measurement canonical digest:
`ca146fa15f79ac80625df5f222f78e369325699ec2b8e86decf42ff8f571363a`.
Both shared default files are now correctly materialized by the operator;
the estimate is byte-identical to its successful versioned predecessor.
Every historical shared calibration entry matches the preserved backup,
whose SHA is `e6f7d3c1f7c5651edb89861bf2e65f1ed6de520582f9bf9ae30a8cac2550c552`.
No shared data file was rewritten by this continuation.

CAL01/CAL02 replay: 1,810 rows, 1,809 accepted, one recorded PressBooks empty-text
rejection; exact saved row identities/adapter digests reproduced. Gzip streams
were not retained: offline verification does not recheck their CRC or original
line bytes. Eight prefix files and four complete small files remain biased
samples, not full-component certification. Metadata and bridge were rebuilt and
compared to actual stored publication, not assumed from the earlier preview.

All four review files match the supplied SHA-256s:

| Review | SHA-256 |
|---|---|
| attribution | `aa313beeb2b568dd5104278cb406c3a31d835ebe6168d3ba89a5f92f8c4129ef` |
| benchmark_risk | `3a047ee431402d013111e5278c9e6b7ebf921f3fac86a2075dd9573f07813192` |
| external_evidence | `94c7fa013a49f257cfcd7f59bc324bfdba64330351bf7bfb898c97b04630e205` |
| source_rights | `c2b12ec4ef37d2024d22a8e76616c45cb5b246f84871ca732bc39ae925f93f38` |

Admission PASS: `c04-benchmark-risk-v3`, `suspect_with_mitigation`, operator
GammA, research-pretraining license approval and approved provenance. The
reconstructed decision matches every stored field except the intentionally
fresh reconstruction timestamp; the original stored timestamp is preserved.
The plan now binds the full stored decision digest, including its review hashes,
as well as bridge and probe fingerprint. The license basis remains
`component_allowlist_review`; repository and row licenses were not invented.
C05 remains mandatory before tokenizer/training/benchmark claims.

Probe fingerprint: `72ddc7186f38e59d54c599a3ed309e15dd1aae2b9141bb69ef2957c7f6eb0d39`.
Adapter v2 SHA: `89ae45abf7a2fd8e533a5d50ef6e37ae99e4ef5c701c365ad854cdb36157a05a`.
Frozen v1 and columns identities remain unchanged, as does evidence matrix
`3066a5098d585dbadbc8042427101e28c52684c6fb471533df1818dd1e9dbbbd`.

| Historical admission | Result |
|---|---|
| finepdfs | PASS; identical to prior handoff |
| finewiki | PASS; identical to prior handoff |
| ifm_general | PASS; identical to prior handoff |
| ifm_planning | PASS; identical to prior handoff |
| simple_stories | PASS; identical to prior handoff |
| synth | PASS; identical to prior handoff |
| ultrax | PASS; identical to prior handoff |
| wiki_rewrite | PASS; identical to prior handoff |

The optional txt360 ablation remains outside this task. The baseline's existing
11/11 readiness is not permission to execute these production plans.

## Recorded Strategy C selection

All six targets are satisfiable under the recorded 1.15 planning margin.
Quotas remain exactly 300M final / 330M first-pass tokens / 1.32B canonical B.
Selections are independent component prefixes, with no repeats, excluded files,
cross-component substitution or benchmark-reserved ranks. Global bounding ranks
[0,382) printed by the existing CLI are not a contiguous acquisition prefix;
the authoritative next positions are the six component cursors below.

| Component | Files | Compressed B | Projected canonical B | Required canonical B | Headroom B | Next cursor |
|---|---:|---:|---:|---:|---:|---:|
| libretexts | 46 | 87,207,314 | 257,155,526.529 | 220,000,000 | 37,155,526.529 | 266 |
| news | 64 | 98,602,096 | 247,415,757.402 | 215,144,136 | 32,271,621.402 | 381 |
| oercommons | 64 | 17,149,251 | 51,258,376.136 | 44,572,500 | 6,685,876.136 | 377 |
| pressbooks | 28 | 88,695,527 | 254,009,903.983 | 220,000,000 | 34,009,903.983 | 185 |
| project_gutenberg | 3 | 359,530,917 | 949,409,949.801 | 614,178,200 | 335,231,749.801 | 16 |
| public_domain_review | 63 | 2,876,489 | 7,020,939.183 | 6,105,164 | 915,775.183 | 382 |

| Component | First selected file | Last selected file | Projected /4 tokens | Headroom after 1.15 target B |
|---|---|---|---:|---:|
| libretexts | `libretexts/libretexts.chunk.39.jsonl.gz` | `libretexts/libretexts.chunk.49.jsonl.gz` | 64,288,881.632 | 4,155,526.529 |
| news | `news/news.chunk.29.jsonl.gz` | `news/news.chunk.32.jsonl.gz` | 61,853,939.350 | 1.002 |
| oercommons | `oercommons/oercommons.chunk.49.jsonl.gz` | `oercommons/oercommons.chunk.18.jsonl.gz` | 12,814,594.034 | 1.136 |
| pressbooks | `pressbooks/pressbooks.chunk.18.jsonl.gz` | `pressbooks/pressbooks.chunk.60.jsonl.gz` | 63,502,475.996 | 1,009,903.983 |
| project_gutenberg | `project_gutenberg/project_gutenberg.chunk.43.jsonl.gz` | `project_gutenberg/project_gutenberg.chunk.52.jsonl.gz` | 237,352,487.450 | 243,105,019.801 |
| public_domain_review | `public_domain_review/public_domain_review.chunk.26.jsonl.gz` | `public_domain_review/public_domain_review.chunk.07.jsonl.gz` | 1,755,234.796 | 0.583 |

Total: **268 files**, **654,061,594 B** expected single transfer,
**1,766,270,453 B** projected canonical (floored total), **190,919** projected
rows (rounded up), **441,567,613** /4 token equivalents. These are not exact XLM
tokens or guaranteed post-C05 yield. News/OER/PDR consume all eligible inventory;
their approximately one-byte residual beyond 15% is rounding, not extra safety.
Raw acquisition is about 53.752% Gutenberg; the recorded first-pass allocation
is 46.529%, enforced later by component down-selection.

## Deterministic policy

Policy freeze was executed because it is deterministic modeled acquisition
configuration after verified operator bounds/split/admission. It takes no
operator decision parameter and does not authorize a production plan.

| Candidate | Disposition | Transfer B | Requests estimate | Modeled wall seconds |
|---|---|---:|---:|---:|
| whole_file_local | selected, feasible | 654,061,594 | 536 | 3,313.884 |
| small_source_direct | inapplicable: whole eligible source exceeds 2 GiB and differs from C's selected population | not modeled | not modeled | not measured |

No Parquet mode participates. Prefix-observed transfer is 197,380.906 B/s;
adaptation is the explicitly disclosed fallback 5,814 rows/s. One stream and
one processor, maximum two in-flight files. Neither production throughput nor
worst-case runtime was measured. Scratch model uses the reviewed two-envelope
3,828,713,572 B ceiling; durable model uses 545,786,978,944 B of aggregate worst-case
allowances, not expected compressed-plus-canonical usage.

Policy digest: **`2d14a2c7e2036f94262d3ae16f28635a6afb59501cd9af5914ef2e76ee68436b`**.
Stored at `G:\XLM\plans\common_pile\transport-policy.json`.

## Production plan and limits

Sequence **1**, `G:\XLM\plans\common_pile\p01\plan.json`.
PLAN DIGEST: **`c38eb2be01a28579ffa77a319c0713e5fef2df0f4da9ac3ed3b3089ab04edaa7`**.
Acquisition behavioral hash: `e405d6f6573137c11fcac0913aac358ffbc1942720063ae938fbab7a9f0a8d0e`.

The plan binds source/revision, allowlist through inventory and component-policy
bindings, exact inventory digest, calibration digest/file hash, recorded split,
reviewed bounds, full admission identity, bridge/probe identity, policy identity,
all 268 exact paths, per-component cursors and target bytes. Reconstructing the
AcquisitionPlan reproduces its behavioral hash exactly. Repository snapshots
of plan and policy contain no corpus text.

| Limit | Value |
|---|---:|
| Explicit production selected-file limit | 268 (bounded implementation maximum 384) |
| Expected transfer / maximum transfer | 654,061,594 / 49,085,594,940 B |
| Expected requests / maximum requests | 536 / 4,288 |
| Maximum requests per file / retries | 16 / 5 |
| Request timeout / file deadline / plan deadline | 30 / 5,700 / 34,200 s |
| Maximum compressed file | 122,103,470 B |
| Maximum decoded file / expansion ratio | 854,724,290 B / 7x |
| Maximum rows per file / aggregate | 5,612 / 1,504,016 |
| Maximum upstream line and canonical record | 524,288 B |
| Maximum canonical file / aggregate | 644,874,996 / 172,826,498,928 B |
| Rejection ledger per file | 67,108,864 B |
| Shared processing output pool per file | 1,789,099,396 B |
| Durable file / aggregate output ceiling | 1,911,202,866 / 545,786,978,944 B |
| Scratch ceiling / physical free-space reserve | 3,828,713,572 / 34,359,738,368 B |
| Downloader / processor maxima | 1 / 1 |

Processing metadata/state each 1,048,576 B, progress 4,096 B, run metadata
8,388,608 B, event 1,048,576 B; parser bound 33,554,432 B. Reviewed bounds override
the older generic size-anchor diagnostic still present in plan metadata. No
accepted byte/record/row/growth/deadline bound was widened. Files and memory
buffers are bounded; production peak RAM and full-shard performance remain
unmeasured. Sampled free space at verification: G 695,036,133,376 B;
C 845,439,610,880 B. Runtime checks free space again.

## Resume and stop

`resume-check --plan 1` exited 0:
fresh_download **268**, local_complete_reuse **0**, local_processing_retry **0**,
resumable_partial **0**, sealed_skip **0**. Worst-case network bytes reported by
the conservative classifier: **32,723,729,960 B** (268 times the file ceiling);
this differs from the exact inventory expected transfer and the retry-inclusive
plan budget. Prefix samples were never promoted to complete production files.
Only `plan.json` exists in p01: no authorization or acquisition execution record.

Exact authorization/run/verify/resume/sufficiency/repair/top-up/seal commands and
network environment transitions are in [operator-commands.md](../evidence/COMMON-PILE-PRODUCTION-PLAN/operator-commands.md).
No such authorization or production action was executed here.

## Failure and recovery

| Failure | Required response |
|---|---|
| File exceeds frozen file ceiling; inventory identity disagreement | Stop, inspect source/validator and listing evidence; renew affected evidence/inventory/review if identity changed. Never silently change the immutable inventory. |
| Gzip expansion, line/record, row, canonical, ledger, durable or processing-growth ceiling | Deterministic failure: do not retry unchanged. Obtain new evidence and an explicit reviewed-bound amendment before a separately reviewed repair plan. |
| Scratch or free-space refusal | Stop and restore adequate space within the existing policy; resume only after checking retained state. New capacity ceilings require review/repair. |
| Request timeout or interruption | Only bounded transport retries (5 maximum) within request/time/byte budgets. On failure inspect spent cost, then operator-directed resume if still valid. No retry-until-green. |
| File or plan deadline exhausted | Stop; a retry cannot reset spent deadline accounting. Review a new bounded execution/repair path; never edit a receipt or widen time automatically. |
| Missing/null/non-string text | Fatal adapter field violation; stop and investigate real compatibility. Adapter changes require evidence renewal and current admission before repair. |
| Blank string text | Recorded CommonPileEmptyTextError rejection; continue within ledger/row/output bounds. Component canonical sufficiency can still fail. |
| Corrupt/truncated gzip, CRC, UTF-8 or JSON violation | Stop; compare verified source identity and hashes. A retained corrupt/invalid file is not fixed by repeating decode; investigate/reacquire only with explicit approval and renewed evidence when required. |
| Changed ETag/length/revision/local SHA | Refuse reuse/resume; investigate identity drift and renew affected evidence/admission. No silent overwrite or continuation. |
| Component quota deficit | Top-up only after all units seal and only from that component's remaining eligible cursor. If exhausted (notably news/OER/PDR), stop for a new operator decision; no repetition/substitution/renormalization. |

Bounds artifacts are write-once. This milestone does not invent an overwrite
or bounds-amendment CLI; a future bound failure requires a supported, explicitly
reviewed amendment before `plan-repair`. Decompression always restarts at byte
zero of a verified complete compressed file; only compressed download resumes
at a verified byte checkpoint. C05 lineage for Gutenberg remains unresolved work
for the later C05 stage, without fabricated book identifiers.

## Implementation and validation

Product changes: component-specific selected-file expectations (including
repair retained-download accounting); policy workloads from recorded C selection
and reviewed storage envelopes; explicit 1/1 component concurrency; exact full
admission decision binding. The first real plan attempt exposed the model's
256-file limit and exited 1 before a plan was written. The new explicit
`selected_file_limit` is behavioral-hash bound, production-only above 256 and
capped at 384. The driver sets 268 for this reviewed population. Default 256,
pilot limits, historical behavioral hashes, all operator bounds and selections
remain unchanged. This is proposed production configuration pending operator
authorization, not runtime automatic widening.

Python 3.12.13, Windows-11-10.0.26200-SP0, existing uv.lock and
CPU/eval extras. Commands use `uv run --offline --locked --no-sync`.
HF_HUB_OFFLINE/HF_DATASETS_OFFLINE/UV_OFFLINE/TRANSFORMERS_OFFLINE remained 1.
One xdist controller at a time, 16 workers/worksteal/no restarts. Test worker
OMP/MKL/OPENBLAS/NUMEXPR threads=1; TOKENIZERS_PARALLELISM=false. Production
settings and CPU/CUDA installation policy remain unchanged.

* 587 related synthetic tests passed (exit 0), 25.688 s wrapper,
  sampled process-tree peak RSS 3,680,899,072 B.
* 81 focused planning/run/repair/component tests passed (exit 0), 29.313 s,
  peak RSS 539,156,480 B; after admission identity binding, 40 focused tests
  passed (exit 0), 8.344 s, peak RSS 333,545,472 B.
* After the file-count change: 178 relevant acquisition/plan/runner/repair/CLI
  tests passed (exit 0), 26.485 s wrapper, peak RSS 3,135,344,640 B.
  These selections overlap and must not be added as unique tests.
* The first expanded selection had 175 passes / 3 failures. One new test used
  selected-record mode with whole-file inputs; fixed. Two Windows temporary
  journal names exceeded the practical path limit. A same-directory diagnostic
  created 207/208-character paths and reproduced FileNotFoundError on 291/292
  characters. The identical test selection then passed with a fresh short
  `--basetemp C:/XLM-scratch/cpcheck01`. No failure was called a flake or omitted.
* Initial component feedback had 23 passes / 2 failures: a nonexistent CLI
  reservation argument and an authored scratch envelope too small for two slots.
  The driver now uses the existing planner reservation constant; the fixture
  explicitly budgets two envelopes. The corrected 25-node selection passed.
* Initial strict-type checks exposed the audit helper's dynamic-import annotation
  and two typing issues in a newly touched existing test module; fixed without
  suppressions. The initial read-only decision comparison mistakenly compared
  a newly generated timestamp; all non-timestamp fields were then checked exactly.
* Final Ruff, format and strict mypy over all 10 touched Python files, and diff checks are recorded in validation.json.
  Related tests include policy, calibration/evidence, admission, split/bounds,
  planning/repair/top-up, gzip, resume, sufficiency/seal, CLI and inventory.
* Full repository acceptance, complementary serial selection, CUDA, live-source
  tests and production throughput/memory tests were NOT RUN. No skip counts as PASS.

## Requirement ledger and continuation

| Requirement | Status |
|---|---|
| All operator inputs and cross-bindings | VERIFIED / PASS |
| Eight historical admissions and prior calibration entries | VERIFIED / PASS, unchanged |
| Recorded C capacity, exact selection, cursors | VERIFIED / PASS |
| Policy and component-aware plan fixes | IMPLEMENTED / VERIFIED |
| Deterministic policy freeze | DONE, not execution authorization |
| Production p01 / reconstruction / resume-check | CREATED / VERIFIED |
| Plan authorization / acquisition / post-run seal | NOT RUN: operator only |
| C05 / tokenizer / training / CUDA / push | OUT OF SCOPE / NOT RUN |

Next: operator reviews this report and p01, then personally executes only the
appropriate commands from operator-commands.md. Return the actual run receipts,
resume state and sufficiency result for offline verification. Do not infer a
future authorization from this handoff.

Continuation prompt:

> Continue in F:\Project\xlm-common-pile on feat/common-pile-allowlist from the
> production-planning commit. Read COMMON-PILE-PRODUCTION-PLAN.md and its evidence.
> Verify operator authorization and any run receipts offline against p01 digest
> c38eb2be01a28579ffa77a319c0713e5fef2df0f4da9ac3ed3b3089ab04edaa7. Preserve all identities and decisions.
> Investigate failures or component deficits using actual receipts; no network,
> retries, changed bounds, repair/top-up authorization, C05/tokenizer/training or
> push without the corresponding new operator instruction.

COMMON PILE PRODUCTION PLAN READY FOR OPERATOR AUTHORIZATION
