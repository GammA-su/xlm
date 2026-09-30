# Essential-Web selector fast-track freeze

**B-NORMAL FROZEN AS THE PRODUCTION ESSENTIAL-WEB SELECTOR FOR MIX-01 —
T SEMANTIC REVIEW NOT RUN**

2026-09-30, branch `data/mix01-ultrax-6b`, starting HEAD
`e596f19cde59d6f9b922ebc4069cf79b231c27c0`. Amendment
`essential-web-selector-fasttrack-v1`.

Freeze digest:
`c6f32a65f083c99b64245e25151f2cc73275093e1013d68b625c6d6f63d10a0c`

This is an explicit operator and scientific decision. It rests only on
metadata evidence: the sealed Arm-M result and the frozen development sweep.
It is **not** a text-validated result. The blinded Arm-T review was not run,
so no claim about the semantic quality of any policy is made here.

No network, acquisition, Arm-T text access, human or model labels,
unblinding, reselection, M change or push.

[Freeze](../evidence/ESSENTIAL-WEB-SELECTOR-FASTTRACK-FREEZE/freeze.json),
[exact commands](../evidence/ESSENTIAL-WEB-SELECTOR-FASTTRACK-FREEZE/COMMANDS.md),
[M analysis](ESSENTIAL-WEB-EVIDENCE-V4.1-M-ANALYSIS.md),
[T package](ESSENTIAL-WEB-EVIDENCE-V4.1-T-BLINDED-PACKAGE.md).

## Why an amendment

The frozen evidence protocol planned a blinded review of 118 Arm-T texts by
two independent human reviewers, with an independent adjudicator, before any
selector decision. One human is available. The operator chose not to run
that review and to return to data acquisition. A single reviewer, a model
or synthetic labels cannot stand in for the frozen design, so none was used.

This amendment therefore departs from the protocol in one respect: the
selector is decided without the T arm. Everything else is unchanged.

## Arm T status

`NOT_RUN_NO_TWO_INDEPENDENT_HUMAN_REVIEWERS`

| Fact | Value |
|---|---|
| Frozen T locators acquired | 118 of 118 |
| Reviewable / unreviewable (oversized) | 117 / 1 |
| Blinded package | materialized and sealed, digest `18c95b95699922db325626fd8776c2317231c51666f9bcc42898f5811dabc58d`, commit `e596f19` |
| Semantic labels collected | 0 |
| Adjudication | NOT RUN |
| Unblinding | NOT RUN |
| T evidence used to choose B-normal | none |
| Contribution to this decision | none: neither acceptance nor rejection evidence |

The arm is not run. It did not pass and it did not fail. The sealed package,
custodian key and mapping stay where they are for possible future research.
The builder read one Arm-T file: the Git-committed `package_manifest.json`,
which holds hashes and counts only. It refuses any reviewer, custodian or
`sealed/` path. A stat-only listing of the external root still shows 722
files and 4,852,481 bytes, as sealed; no file there was opened and the
package `verify` command was not run, because it reads the texts.

## Parents verified

| Binding | Verified value |
|---|---|
| M seal digest | `afc972ccf570a5587bf7bd3b6b8d1d728a09f2230c49f1b0a19c1c74cda0b9bc`, recomputed by the committed verifier before and after this work (exit 0 both times) |
| M result commit | `65edfd0a9e3b33b2b98cc9423223e31506db59a5`; no M file changed |
| M summary | `m_sweep/summary.json`, SHA-256 `75845b345792fa929b671c668e3688e94e24463703883f65fddb9c1cbfc5b069`, bound in the seal |
| Development sweep | manifest digest `e13c9c98efdf07fcc7c1375c4c8b62d918d77aa39c158171aa090c8695ac4087`; `summary.json` SHA-256 `0925c3708461feaa399a3f9c19e28d69cd338d7dd72d9e8837da115e4e9a60ed`, equal to the M seal's binding |
| Evaluator | `scripts/essential_web_selector_sweep.py`, SHA-256 `5a63e78560ea9e12b0ec03b5e7e63204b5c75554bee02192c12bb4ab0853bf9c`, tool version 2; unmodified |
| Policy spec | `recipes/selectors/essential_web_selector_sweep_v1.yaml`, SHA-256 `c27a0aae8f63a1d9f80fdeb693a14d877b68298d386a3fc8679bbab34fa2a088`, canonical digest `f4357f61f434d5105d823266153dac6372e122f12767187631e8796b4899dd07`; unmodified |
| Source | `EssentialAI/essential-web-v1.0` at `ce4eccc7e9604667b6d7f32cb6274b8b41f3113d` |
| Mix-01 preset and quotas | bound by hash; not edited |

## Frozen selector

Production selector: policy **B**, tier **normal**, of the unchanged
pre-registered spec. B-normal semantics digest
`56f86d728852b724f22b9d53b65f1566692efdc43a6e5e14d1bf02261249a5ac` (the
canonical digest of the definition below, extracted from the spec).

Nothing was rewritten or tuned. The freeze copies the definition out of the
spec and refuses to build if policy B no longer binds gate GN, if its
component rules differ, or if the precedence differs.

| Element | Frozen value |
|---|---|
| Validity | FDC code matches `[0-9]{3}(?:[.][0-9]+)?` (kept as a string); English score numeric and within 0 to 1; every label inside its known universe |
| Gate GN | English ≥ 0.8; artifacts `No Artifacts`; missing content `No missing content` or `Missing Images or Figures`; correctness `Highly Correct`, `Mostly Correct` or `Not Applicable/Indeterminate`; document type inside the 11-label union |
| Science | S5 or S61. S5: FDC first digit 5 with a science genre. S61: FDC prefix 610 or 612–618, genre Academic Writing or Knowledge Article, knowledge Conceptual, correctness Highly or Mostly Correct |
| Practical | P: an explicit instructional genre, or Procedural knowledge with a conditional genre |
| Prose | R: a prose genre with Factual or Conceptual knowledge |
| Precedence | science, then practical, then prose |
| Not admitted | `unassigned` (passes the gate, matches no component) and `rejected` |

The exact label lists are in `freeze.json` under
`production_selector.semantics`.

## Counts, recomputed from the sealed artifacts

Science / practical / prose / unassigned / rejected; every row sums to 4,096.

| Condition | Development | M (confirmation) |
|---|---|---|
| A-normal | 19 / 108 / 377 / 40 / 3552 | 17 / 117 / 374 / 50 / 3538 |
| **B-normal** | **29 / 108 / 371 / 36 / 3552** | **24 / 117 / 372 / 45 / 3538** |
| C-normal | 29 / 41 / 120 / 354 / 3552 | 24 / 46 / 127 / 361 / 3538 |
| D-normal | 54 / 279 / 719 / 110 / 2934 | 56 / 257 / 717 / 118 / 2948 |
| A-strict | 12 / 62 / 334 / 28 / 3660 | 7 / 68 / 344 / 38 / 3639 |
| B-strict | 20 / 62 / 329 / 25 / 3660 | 11 / 68 / 342 / 36 / 3639 |
| C-strict | 20 / 22 / 102 / 292 / 3660 | 11 / 30 / 111 / 305 / 3639 |
| D-strict | 20 / 62 / 329 / 25 / 3660 | 11 / 68 / 342 / 36 / 3639 |

Development numbers come from the hash-verified frozen sweep summary; M
numbers from the sealed M summary. Both equal the figures in the task
request.

## Rationale (descriptive, metadata only)

1. **B-normal replicated closely.** Between development and M its counts
   moved by −5 science, +9 practical, +1 prose, +9 unassigned and −14
   rejected rows. The largest share move is 0.34 percentage points.
2. **It keeps far more practical and prose rows than C-normal.** On M,
   practical 117 against 46 and prose 372 against 127 (development 108
   against 41 and 371 against 120). Science is identical in B and C.
3. **It includes the S61 science extension that A-normal lacks.** That adds
   7 science rows on M and 10 in development.
4. **It avoids D-normal's relaxed admission.** D-normal's gate also admits
   the artifact label `Irrelevant Content`. That passes 590 rows on M and
   618 in development that B-normal rejects, and roughly doubles the
   admitted rows (513 to 1,030 on M).
5. **Strict is not selected.** Strict science is sparse and moved more:
   B-strict science fell from 20 to 11, against 29 to 24 at normal. B-strict
   and D-strict are identical in both replicates.

These are counts of taxonomy metadata. They say which rows each policy
admits, not whether the admitted documents are good.

## Limitations

1. No semantic quality of any policy was assessed. B-normal is not a
   T-validated winner.
2. The condition was chosen with both replicates visible. M is a
   descriptive replication of the counts, not an independent test of the
   choice. The M seal itself recorded no decision and no ranking.
3. Both replicates are one contiguous 512-row window per crawl: clustered,
   not iid. No confidence interval, test or ranking statistic is reported.
4. Science stays sparse under B-normal: every crawl cell is below 20 rows
   in both replicates.
5. Whether `Irrelevant Content` rows are usable, and whether the S61 rows
   are good science, are exactly the questions the T review would have
   addressed. They remain open.

## Implementation and tests

- `src/xlm/data/evidence_v2/fasttrack_freeze.py`: parent verification,
  count recomputation, B-normal definition extraction, Arm-T status record,
  rationale and the canonical freeze.
- `scripts/essential_web_fasttrack_freeze.py`: `build` and `verify`; refuses
  to overwrite a freeze. `verify` recomputes the whole freeze from the bound
  inputs and compares it byte for byte.
- `tests/test_essential_web_fasttrack_freeze.py`: 32 tests.

The tests cover: policy digest equality with the evaluator; the extracted
B-normal definition and refusal on four policy mutations; count
conservation and refusals; the committed sealed M summary; the NOT RUN
record and seven refusals on a changed package; refusal of seven review
material paths before any read; determinism; digest movement with each
bound parent; parent tampering; and the committed freeze.

## Requirement ledger

| Requirement | Status |
|---|---|
| M seal reproduces exactly | VERIFIED (real evidence, before and after; exit 0) |
| M evidence unchanged | VERIFIED (Git status clean for the M and T evidence directories, evaluator and policy) |
| Development and M counts recomputed from sealed artifacts | VERIFIED (real artifacts) |
| Arm-T recorded as NOT RUN, no labels, no unblinding | IMPLEMENTED, VERIFIED (committed manifest only) |
| No Arm-T text or label used | VERIFIED by construction (path refusal, tests) |
| T package preserved | VERIFIED by stat only (722 files, 4,852,481 bytes); package `verify` NOT RUN |
| B-normal semantics exact and unchanged | VERIFIED (hashes; extraction refuses drift) |
| Freeze built and verified by full recomputation | VERIFIED (exit 0) |
| Refusal paths | VERIFIED (synthetic tests only; a mock proves logic, not live data) |
| Frozen-evaluator regressions (60), M analysis tests (29), identity tests (5) | VERIFIED (passed) |
| Ruff check and format | VERIFIED |
| Strict mypy | VERIFIED with the interpreted runner; compiled mypy BLOCKED by application control |
| Fast and full offline selections, CUDA, network tests | NOT RUN |
| Arm-T semantic review, adjudication, unblinding | NOT RUN (by operator decision) |
| Production integration | next commit |

Measured resources: freeze build under 1 s; `freeze.json` 19,139 bytes.

## Exact next action

Integrate the frozen selector into the production Essential-Web adapter and
verify it offline on both replicates (next commit on this branch). No
acquisition is authorized by this freeze.

**B-NORMAL FROZEN AS THE PRODUCTION ESSENTIAL-WEB SELECTOR FOR MIX-01 —
T SEMANTIC REVIEW NOT RUN**
