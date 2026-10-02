# Current production-planning handoff (2026-10-02)

**The post-calibration handoff below is superseded by
[COMMON-PILE-PRODUCTION-PLAN.md](COMMON-PILE-PRODUCTION-PLAN.md).**
Operator Strategy C, reviewed bounds, publication, reviews and admission have
been verified. Policy is frozen and p01 is created, unauthorized and not run.
See that report and its exact operator commands for the current stop point.

---

# Common Pile real post-calibration handoff (2026-10-02)

The six-component real calibration is verified and built. **C is the technical recommendation; no operator allocation or bounds decision has been recorded.** This continuation was entirely offline. The operator performed the preceding live samples and metadata probe. No admission, production policy freeze, production plan, production authorization/run, C05, tokenizer, training, or push occurred.

Starting HEAD: `42fe42b25e3b51879a23d7b48c605740b5fcc671`, clean `F:\Project\xlm-common-pile`, branch `feat/common-pile-allowlist`. The final commit contains this report; the final chat supplies its exact SHA. Existing prior work and the unrelated UltraX worktree were preserved.

Evidence: [COMMON-PILE-POST-CALIBRATION](../evidence/COMMON-PILE-POST-CALIBRATION/). `verification.json` contains text-free offline replay results; `analysis.json` contains all calibrated arithmetic; `commands.json` and `validation.json` retain exact commands, exits, environment, durations and sampled process-tree RSS. `changed-files.txt` is the exact commit file manifest. All corpus text remains outside the repository.

## Verified real inputs and immutable identities

CAL01 digest `f7d74b4a2e2614e10a8231653ef36831aecb7546955feef73d6df89f9b4be289`: 10 files, 1,746 rows, 76 requests, 4,693,869 body B. CAL02 digest `5d57e4ea82e509e28faf69aa1e7c9ee95da38bbb26e4a7367a0dea0894071b67`: 2 files, 64 rows, 44 requests, 2,883,584 body B. Both digests match the operator values; saved row identities, lengths, per-row adapter outcomes/document hashes and per-file aggregate document hashes replay exactly. The twelve exact authorized targets, source/revision, allowlist/inventory bindings and recorded complete/prefix status verify.

OER Commons has two complete 83-row files; PDR has two complete 22-row files. These were successful EOFs, not short-sample failures. Eight other files are biased prefixes. PressBooks has one `CommonPileEmptyTextError`: 1,809 accepted / 1 rejected overall. No rejected text is included in artifacts.

The stored metadata probe manifest verifies: `common_pile/common_pile_prose`, Hugging Face, pinned revision, `partial`, `real_observed`, not gated, null declared license, two observed/declared files. Probe file SHA-256: `bf2a0b51dfbcaf06531ac279f5a605d41371573a18cf5eb3e11e459d2bd74438`. Probe cost: 127,456 B / 2 requests / 0.422 s. Combined operator live cost: **7,704,909 body B / 122 requests**. No request was repeated by this agent.

Offline limitation: compressed streams were not retained. We verified receipt integrity and saved adapter rows; we did not replay gzip CRCs or reproduce original upstream line hashes from reserialized saved JSON. Complete-gzip validation is the verified sampler receipt evidence.

Unchanged: repository `common-pile/comma_v0.1_training_dataset`; revision `5afc546db324e7f39f297ba757c9a60547151e7c`; allowlist `b2bb7c0dc532a6263ca17b78816186fa194c12c09c74614baaeb29c76cdaab04`; inventory `c0984aa33fb3598a9e724b9518df8cd8a0f2af52b5708ec211fb7fd693df7637` (384 files, 64/component, 8,131,797,849 compressed B). Exactly libretexts, news, oercommons, pressbooks, project_gutenberg, public_domain_review.

## Authoritative calibration and measurement

Calibration: `G:\XLM\calib\common_pile_prose\component-calibration.json`, self-digest **`c8a32cced2bae62c21b4e4396d46f0803f11745117e52d468adf3912dde0541d`**. Build and show exited 0. Every component and both receipts are embedded and verified by deterministic reconstruction.

Measurement: `G:\XLM\calib\common_pile_prose\measurement.json`; SHA-256 **`a99e994e3f1d43b5f50bc659ac7b1371a55cd05282e95f48c3a933d3a9bc7a42`**; canonical JSON digest **`ca146fa15f79ac80625df5f222f78e369325699ec2b8e86decf42ff8f571363a`**. It binds calibration, allowlist, inventory, revision and receipt identities.

The real `mix01_inventory record` initially failed to replace the existing shared `G:\XLM\calib\calibration.json` (WinError 5, exit 1). Its content was verified unchanged; no permissions were changed. Recording into **`G:\XLM\calib\calibration.common-pile-post.json` succeeded (exit 0)** and preserves every historical source entry. SHA-256: `73a3c5ab3f3f297437d1baaae90996cd1464950c576bc07d93aa2d9be89b764a`. The failed temporary `calibration.json.tmp` remains unpromoted; it is not an authoritative calibration.

Estimation first exposed existing IFM view entries outside the mixture quota keys. The CLI now supports explicit repeatable `--auxiliary-source`; unknown entries still refuse by default, quota components cannot be excluded, and auxiliary measurements remain disclosed. With `--auxiliary-source ifm_general --auxiliary-source ifm_planning`, the real estimate succeeds at `G:\XLM\calib\headroom_estimate.common-pile-post.json`. It projects 572,847,737 compressed B and 28 mean-sized initial files for Common Pile, using 1.15 safety.

The weighted measurement contains **357 rounded sample-equivalent rows**, with rounded acceptance 1.0. These are not the actual 1,810 rows / one rejection. The estimate now retains the component-weighted basis, calibration digest and this explicit rounding disclosure. Historical estimate shapes remain unchanged unless the new basis/auxiliary option is present.

**Shared default filenames still need operator materialization.** The existing driver reads `calibration.json` and `headroom_estimate.json`; no hidden versioned fallback was added. Exact idempotent record/estimate commands are in [operator-next-commands.md](../evidence/COMMON-PILE-POST-CALIBRATION/operator-next-commands.md).

### Measured six-component table

All byte quantities below are observed sample quantities. Decoded bytes include decoder look-ahead; compressed-to-last-row differs from total transfer. Files sampled = two for every component.

| Component | Rows | Accepted / rejected | Transferred B | Compressed through last row B | Decoded B | Canonical B | Max observed line B | Completeness |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| libretexts | 512 | 512 / 0 | 1,703,936 | 1,552,384 | 4,697,591 | 4,577,645 | 127,786 | 2 prefixes |
| news | 512 | 512 / 0 | 524,288 | 417,792 | 1,117,232 | 1,048,338 | 19,932 | 2 prefixes |
| oercommons | 166 | 166 / 0 | 630,724 | 630,724 | 1,912,452 | 1,885,207 | 131,030 | 2 complete shards |
| pressbooks | 512 | 511 / 1 | 1,703,936 | 1,646,592 | 4,827,169 | 4,715,578 | 131,132 | 2 prefixes |
| project_gutenberg | 64 | 64 / 0 | 2,883,584 | 2,785,280 | 7,562,134 | 7,355,063 | 140,115 | 2 prefixes |
| public_domain_review | 44 | 44 / 0 | 130,985 | 130,975 | 328,380 | 319,684 | 27,025 | 2 complete shards |

| Component | Canonical / transferred | Decoded / transferred | Canonical B / accepted row | Canonical / compressed through last row | Rejections |
|---|---:|---:|---:|---:|---|
| libretexts | 2.686512 | 2.756906 | 8940.713 | 2.948784 | none observed |
| news | 1.999546 | 2.130951 | 2047.535 | 2.509234 | none observed |
| oercommons | 2.988957 | 3.032154 | 11356.669 | 2.988957 | none observed |
| pressbooks | 2.767462 | 2.832952 | 9228.137 | 2.863841 | CommonPileEmptyTextError: 1 |
| project_gutenberg | 2.550667 | 2.622477 | 114922.859 | 2.640691 | none observed |
| public_domain_review | 2.440615 | 2.507005 | 7265.545 | 2.440802 | none observed |

## Projected component capacities and uncertainty

Rates are measured on the samples. Whole-inventory yields below are **projections**, rounded here to the nearest byte/token, not exact corpus capacities or XLM token counts. `/4` is the existing planning convention. Two reserved tail ranks remove one PressBooks file and one PDR file from eligible planning capacity.

| Component | Inventory compressed B | Projected canonical B | Projected /4 tokens | Eligible /4 tokens |
|---|---:|---:|---:|---:|
| libretexts | 122,167,467 | 360,245,464 | 90,061,366 | 90,061,366 |
| news | 98,602,096 | 247,415,757 | 61,853,939 | 61,853,939 |
| oercommons | 17,149,251 | 51,258,376 | 12,814,594 | 12,814,594 |
| pressbooks | 201,539,814 | 577,178,022 | 144,294,505 | 141,930,871 |
| project_gutenberg | 7,689,423,474 | 20,305,389,076 | 5,076,347,269 | 5,076,347,269 |
| public_domain_review | 2,915,747 | 7,116,760 | 1,779,190 | 1,755,235 |

Total projection: **21,548,603,455 canonical B / 5,387,150,864 token equivalents**. Prefixes can differ from later shard regions, and two complete small shards do not establish a full-component distribution. No unbiased sampling or statistical confidence interval is claimed. C05, lineage handling, filtering and the final tokenizer can reduce useful capacity. The calibrated margins are operational safeguards, not proof of post-C05 sufficiency.

## Calibrated A/B/C decision

**A, existing global hash-prefix mechanism:** the mean-yield planner asks for 28 files; applying each selected file's own component rate gives the following actual-prefix projection. The first cumulative prefix that crosses the 1.15-adjusted target is already 18 files. This is reported separately; the implemented legacy A selection was not silently changed.

| Component | Selected files | Compressed B | Projected canonical B | Projected /4 tokens |
|---|---:|---:|---:|---:|
| libretexts | 2 | 3,746,205 | 11,046,749 | 2,761,687 |
| news | 7 | 10,759,505 | 26,998,119 | 6,749,530 |
| oercommons | 5 | 1,450,402 | 4,335,189 | 1,083,797 |
| pressbooks | 3 | 9,480,901 | 27,151,795 | 6,787,949 |
| project_gutenberg | 6 | 718,849,342 | 1,898,258,774 | 474,564,693 |
| public_domain_review | 5 | 261,767 | 638,921 | 159,730 |

A totals **1,968,429,547 projected canonical B / 492,107,387 token equivalents**. Gutenberg is **96.435%**, so the former approximately 97% dominance remains approximately true. The separate 18-file crossing projects 1,628,862,438 canonical B and 96.951% Gutenberg. Whole-file overshoot and eventual uniform down-selection do not create an exact component-token guarantee.

**B, equal shares:** 50M final / 55M first-pass tokens each, 220M canonical B each. The safety requirement is 63.25M projected token equivalents per component. B is **infeasible** under the current safety policy, not renormalized.

| Component | First-pass target | Eligible projected tokens | Margin vs 55M | Margin vs 63.25M | Safety status |
|---|---:|---:|---:|---:|---|
| libretexts | 55,000,000 | 90,061,366 | 35,061,366 | 26,811,366 | sufficient |
| news | 55,000,000 | 61,853,939 | 6,853,939 | -1,396,061 | insufficient |
| oercommons | 55,000,000 | 12,814,594 | -42,185,406 | -50,435,406 | insufficient |
| pressbooks | 55,000,000 | 141,930,871 | 86,930,871 | 78,680,871 | sufficient |
| project_gutenberg | 55,000,000 | 5,076,347,269 | 5,021,347,269 | 5,013,097,269 | sufficient |
| public_domain_review | 55,000,000 | 1,755,235 | -53,244,765 | -61,494,765 | insufficient |

**C, recomputed capacity-capped core:** each non-Gutenberg component receives `min(55M, floor(eligible projected tokens / 1.15))` first-pass tokens. The explicit 15% acquisition margin matches the planner; it is not statistical confidence. Final core quotas use integer `first_pass * 10 // 11`; all integer residue and the remaining quota go to Gutenberg. No repetition is assumed.

| Component | Final tokens | First-pass tokens | Canonical requirement B | Projected capacity minus 1.15 target (tokens) |
|---|---:|---:|---:|---:|
| libretexts | 50,000,000 | 55,000,000 | 220,000,000 | 26,811,366.014 |
| news | 48,896,394 | 53,786,034 | 215,144,136 | 0.250 |
| oercommons | 10,130,113 | 11,143,125 | 44,572,500 | 0.284 |
| pressbooks | 50,000,000 | 55,000,000 | 220,000,000 | 78,680,870.596 |
| project_gutenberg | 139,585,956 | 153,544,550 | 614,178,200 | 4,899,771,036.441 |
| public_domain_review | 1,387,537 | 1,526,291 | 6,105,164 | 0.146 |

C totals exactly **300,000,000 final / 330,000,000 first-pass / 1,320,000,000 canonical B**. Gutenberg's first-pass allocation is **46.528652%**. The small residual margins for news/OER/PDR are integer rounding beyond the already-applied 15%, not additional safety. Selecting calibrated per-component prefixes succeeds as a read-only arithmetic exercise; it does not create an acquisition plan.

**Technical recommendation: C.** It is feasible under the stated calibrated projection, preserves each complementary core's equal opportunity, limits Gutenberg domination, keeps deterministic quotas/cursors and requires no repetition. A is dominated by Gutenberg; B lacks projected safety capacity. This recommendation is not an operator decision. Exact A/B/C preview commands and all integer input files are in the operator command document; every preview exited 0, and none was recorded.

C's full-file selection is 268 files, projecting 1,766,270,453 canonical B and **53.752% Gutenberg in the acquired raw pool**. That differs from the allocation because of safety headroom and indivisible files. Later per-component exact-token down-selection must enforce the chosen final quotas; raw acquisition is not the final mixture.

## Bounds preview and derivation

Preview digest **`15e186e4628973f6d4a42e2cce1008ac66bdca479b25d37df941d383a9b7ed41`**, with `operator=PREVIEW_ONLY`. The actual record digest will change with the operator identity/rationale. The complete input is `bounds.input.json`; `bounds-derivation.json` explains its calculations. These are proposed fail-closed operating ceilings, not proven full-shard maxima.

| Field | Proposed value |
|---|---:|
| `file_deadline_seconds` | 5,700 |
| `max_canonical_bytes_per_file` | 644,874,996 |
| `max_decoded_bytes_per_file` | 854,724,290 |
| `max_decompression_ratio` | 7 |
| `max_durable_bytes_per_file` | 1,911,202,866 |
| `max_file_bytes` | 122,103,470 |
| `max_ledger_bytes` | 67,108,864 |
| `max_record_bytes` | 524,288 |
| `max_rows_per_file` | 5,612 |
| `plan_deadline_seconds` | 34,200 |
| `scratch_cap_bytes` | 3,828,713,572 |

`max_line_bytes` is the same 524,288 B enforced through `max_record_bytes`; there is no separate unbound line override. Processing growth: output pool **1,789,099,396 B**, source allowance **122,103,470 B**, metadata/state each 1 MiB, progress 4 KiB, run metadata 8 MiB, event 1 MiB.

* File size is the exact maximum over the frozen inventory.
* Largest observed decoded/compressed rate is 3.064679535. Double it and round up to 7x; apply this to the exact largest compressed file for decoded capacity.
* Rows and canonical bytes use 2x the largest component-specific full-file projection. This absorbs observed variation without presenting prefixes as worst-case evidence.
* Largest observed line is 140,115 B. Double it, then round up to a power of two: 524,288 B.
* Output pool is twice the decoded ceiling, plus 2,048 B per bounded row for metadata, 64 MiB for the rejection ledger, and 1 MiB summary. These are explicit operational reserves within a shared enforced pool, not measured serialization maxima.
* Durable covers compressed source plus the output pool. Scratch covers two complete source/output/atomic metadata/state envelopes; the existing 32 GiB physical free-space reserve remains.
* Slowest observed prefix throughput is 87,258.890 B/s. File deadline is 4x largest-file transfer time, rounded to 300 s (600 s floor). Plan deadline uses the larger projected A/C compressed selection, 4x the same observed time, rounded to 900 s (3,600 s floor). These are generous time budgets, not measured production runtime predictions.

Prefix evidence cannot prove the maximum record, compression ratio, rows or runtime of an unobserved shard. Acceptance of these operating ceilings is an operator risk decision. Any exceedance must stop and require reviewed new bounds; no automatic increase or additional network is authorized. No extra live request is necessary to prepare this decision; successful full-shard processing remains unverified.

## Evidence, review basis and production gates

`evidence show` exited 0. Candidate bridge digest **`12ff9163353bf6e56979dc66b66307c12e9d57dd92e38c348b4b7927c70838f3`**; probe fingerprint **`72ddc7186f38e59d54c599a3ed309e15dd1aae2b9141bb69ef2957c7f6eb0d39`**. `bridge-preview.json` and `review-facts-preview.json` contain the current technical evidence and the same facts used by `review show`, without publishing or recording reviews.

Adapter identities remain: v2 `89ae45abf7a2fd8e533a5d50ef6e37ae99e4ef5c701c365ad854cdb36157a05a`; frozen v1 `3651ff2af4fcb46c207404e052caa59e1ec42f2ac5a429e5dec752f7e83a7ef6`; columns `3e594f755eea797ac0d29d819c28b4ccf8e387cfc7889470dfc58e8657e58db1`.

Review basis: null repository license; `component_allowlist_review`; exact six components, immutable allowlist/inventory/revision, CAL01/CAL02 receipts, saved-row hashes, current adapter, stored metadata, and evidence matrix `3066a5098d585dbadbc8042427101e28c52684c6fb471533df1818dd1e9dbbbd`. No row-level licenses were invented. Benchmark contamination remains unknown; `suspect_with_mitigation` requires later C05.

`review show` currently exits 1: `the latest probe evidence of this view is not a bridge publication`. Publication was left to the operator. The prepared operator sequence is `evidence publish` then `review show`, followed only by the operator's own review decisions and admission.

The **read-only shared prerequisite checker**, without invoking either forbidden policy-freeze or plan command, refuses exactly:

> reviewed source bounds missing: G:\XLM\calib\common_pile_prose\reviewed-bounds.json; review calibration then use component-bounds record

After that, allocation, current published bridge and bound admission are still required. The driver's default estimate also needs operator materialization as described above. No absent artifact triggers a fallback.

| Requirement | State |
|---|---|
| CAL01/CAL02, metadata, frozen identities | VERIFIED |
| Six-component calibration and versioned measurement/estimate | IMPLEMENTED / VERIFIED |
| Shared default measurement/estimate update | BLOCKED: atomic replacement denied; operator command prepared |
| A/B/C and bounds previews | VERIFIED; NOT RECORDED |
| Evidence bridge preparation | VERIFIED; NOT PUBLISHED |
| License/provenance review and admission | NOT RECORDED / NOT DONE |
| Policy freeze / production plan / authorization / run | NOT RUN; prerequisites BLOCKED |
| C05 / tokenizer / training / CUDA / network / push | OUT OF SCOPE / NOT RUN |

## Validation and changes

Python 3.12.13, Windows 11, existing locked CPU/eval environment. All execution used `uv run --offline --locked --no-sync`; HF offline flags remained enabled for this continuation. Tests use authored synthetic fixtures. Real verification was separate read-only replay of operator-created artifacts. One xdist controller with 16 workers/worksteal/no restarts; OMP/MKL/OPENBLAS/NUMEXPR each one, tokenizer parallelism disabled.

* Related selection: **592 passed**, exit 0; 27.469 s wrapper, sampled process-tree peak RSS 3,979,165,696 B. Includes calibration/measurement, evidence/allowlist/split/bounds, gzip decoder/runner, source plan/run/repair, admission, source CLI, inventory, production ingest, IFM and SYNTH regressions.
* After the final weighted-count disclosure change: **53 focused tests passed**, exit 0; 3.813 s wrapper, peak RSS 113,975,296 B. These overlap the related selection; do not add them as unique tests.
* Eight actual historical source admission identities reverified: UltraX, FinePDFs, SYNTH, Wiki Rewrite, FineWiki, IFM General, IFM Planning, SimpleStories all PASS (`gate-status.json`).
* Ruff/format/strict mypy passed for all eight touched Python files, with `MYPYPATH=src` for the source-layout package. Final staged diff check passed. No blanket suppressions.
* Full repository acceptance, serial selection, CUDA, new live-source tests and production performance tests were NOT RUN. No skip is counted as a pass.
* Investigated failures: shared Windows atomic replacement (preserved, versioned output used); estimate rejection of auxiliary IFM entries (explicitly supported/tested); initial audit import/type/lint errors (fixed). No unresolved test failure or unsupported pre-existing failure claim.

Product changes are limited to explicit auxiliary calibration disclosure, preserving the weighted-count basis in estimates, and correcting stale calibration CLI guidance. Added offline verification/analysis/gate scripts and synthetic arithmetic/refusal tests. No adapter, immutable source decision, frozen inventory, prior review, quota, dependency lock or snapshot was altered.

## Exact operator stop and continuation

Review [operator-next-commands.md](../evidence/COMMON-PILE-POST-CALIBRATION/operator-next-commands.md). It contains exact commands to materialize the shared measurement/estimate, preview all alternatives and bounds, record only the operator's chosen allocation/bounds, publish/show evidence, and perform the operator's own review/admission. No network command is included.

**Agent STOP: ready for the operator's mixture/bounds/license decisions.** Operator STOP: after those decisions/admission, return receipts for offline verification. No policy freeze, production plan or production authorization/run is included.

Continuation prompt:

> Continue in F:\Project\xlm-common-pile on feat/common-pile-allowlist from the newest post-calibration commit. Read the current readiness report and COMMON-PILE-POST-CALIBRATION evidence. The operator has materialized default calibration/estimate files, selected and recorded allocation/bounds, and published/reviewed/admitted the bridge. Verify these artifacts and their exact bindings offline, including current adapter and CAL01/CAL02 identities. Confirm no historical source changed. Prepare the next policy/plan handoff only within the operator's new instructions; do not freeze policy, create/authorize/run production, use network, run C05/tokenizer/training, or push without explicit authorization.

COMMON PILE CALIBRATION READY FOR OPERATOR MIXTURE DECISION

---
## Previous calibration-authorization handoff (superseded by the real calibration above)

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
