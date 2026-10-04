# C05 cleaned-corpus rerun readiness (`clean-v1`)

Date: 2026-10-04. Worktree `F:\Project\xlm-c05-clean-v1`, branch `feat/c05-clean-v1`,
starting commit `79d788c5b3f4a7e3e39dfd290d3b78cd88754293` (Phase-C cleaner). Code
commit `b8a2a6a32f63ad216dc4d213dfaa00df5dc1f87c`; this report is in the following
docs commit.

Scope:
* Offline, authored synthetic fixtures only.
* No access to G:, X:, protected benchmark material or real operator keys.
* No real C05 run, no signature or authorization of a real decision, no push.
* Historical C05 artifacts (p0001/p0002, membership, proof, plans) untouched.

## 1. Outcome

| question | answer |
|---|---|
| Compact parallel engine (2ae48a7) | **Already present.** `2ae48a7` is an ancestor of `79d788c` (`git merge-base` = `2ae48a7`). Nothing ported or cherry-picked. Since then, `src/xlm/data/exclusion` only gained C06 work; the compact engine files are unchanged. |
| Could C05 consume the cleaned manifest? | **No.** In protected mode, `plan` rebuilt the manifest from source seals (`verify_input_manifest`); that rebuild can only reproduce `c05_global_input_manifest`. `make_plan` also needs `sources` (source id/revision/seal) and a per-file `source_file`. The cleaned manifest has neither, and the scan checks every row's `source_id`/`source_revision` against the plan file. |
| Fix | Smallest admission layer: `admit-cleaned` plus `plan --admission` (section 2). The engine, policy semantics and membership/completion schemas are unchanged. |
| Protected benchmark preparation | **Must be rebuilt** (section 4). |
| Readiness | Ready for operator preflight. Post-C05 consumers still blocked (section 8). |

## 2. Cleaned-manifest admission design (`xlm.data.exclusion.cleaned`)

The cleaned manifest (`G:/XLM/quality/clean-production-v1/cleaned-input-manifest.json`,
kind `xlm_cleaned_input_manifest`, status `CANDIDATE_REQUIRES_INDEPENDENT_AUDIT_AND_C05`,
semantic digest `eda4f994…1389`) is **never rewritten**. The admission command reads
evidence only and writes one write-once record. It requires, in order:

1. **Manifest.**
   * Strict canonical JSON whose bytes equal their canonical form.
   * Self-digest equal to the operator pin `--expect-manifest-digest`.
   * Cleaned kind, `version: 1`, the candidate status, `training_permitted: false`.
   * An exact file-entry key set.
   * Totals, components and `empty_files` recomputed from the files.
2. **Original manifest**, matched by the lineage digest, raw SHA-256 and kind:
   * exactly one original file per cleaned file, in the same position;
   * `cleaned_from` equal to that original file's identity, and the same source
     key/component/view/upstream;
   * a `source_file` on every file, and `sources` with id/revision/seal for every
     source.

   It supplies the source identities and seals the scan checks.
3. **Production cleaning (verified).**
   * The receipt (`load_production_receipt`) and the `VERIFIED` verification record
     (`load_verification`), with the result, receipt and verification digests each
     pinned by the operator.
   * Verified files, documents, canonical bytes and file bytes equal to the manifest
     totals.
   * The manifest is **re-derived from the verified state** (`derive_cleaned_manifest`)
     and its bytes must be identical.
4. **Independent post-clean audit**, verified by `verify_historical_audit`, i.e. from
   its own recorded identities and on-disk artifacts. This works even though the audit
   ran at another commit, and current constants are never substituted. It requires:
   * the receipt's and binding's manifest digest and raw SHA-256, equal to this
     manifest's;
   * kind and totals equal (files, documents, canonical bytes, file bytes);
   * no C05 kept overlay;
   * the same data root;
   * `source_files` equal to the manifest's per-file identities;
   * the result digest pinned by the operator.
5. **Audit `report` output** (the saved stdout of `python -m xlm.data.quality report`):
   * exactly `{artifacts, result_digest, sources_rehashed, verified}`;
   * `verified` and `sources_rehashed` both `true`;
   * the same result digest and artifact count.

   UTF-8 and BOM-marked UTF-16 are both accepted (Windows PowerShell 5.1 redirection).

The record (`c05_cleaned_input_admission_v1`, status `ADMITTED_FOR_FRESH_C05_PLANNING`)
states `c05: NOT RUN …`. It is permission to **plan**, not a C05 result.

`plan`:
* A cleaned manifest requires `--admission`. `plan` **re-derives** the record from its
  recorded evidence paths and pins and refuses unless the result is identical. Changed
  evidence refuses.
* An original manifest refuses `--admission`.
* The admission mode must equal the plan mode (protected <-> production kinds).
* Plan files take the cleaned path, SHA-256 and counts, plus the original
  `source_id`/`source_revision`/`source_file`. Seals come from the original sources.
* `ExecutionPlan.input_admission` binds the admission, original manifest, production
  receipt/result, verification and audit receipt/result digests, and the cleaned
  manifest's raw SHA-256 into the **plan digest**. When absent, it is dropped from the
  digest body, so every historical plan digest is unchanged. A test reloads a plan body
  in the pre-change wire format to the same digest.
* **Fresh-generation guard.** It refuses a plan root holding a `pNNNN.json` bound to
  another manifest. It also refuses a scratch or output root holding a plan-digest entry
  that is not a plan of this plan root. The guard runs before and inside the allocation
  lock, so historical `G:/XLM/c05`, `X:/C05-Scratch` and the p0002 output can never be
  reused. Plans, work directories and completions are keyed by plan digest, so a cleaned
  plan cannot address historical state anyway.

Old decisions and proofs:
* Signed decisions bound to `11724d92…` fail `verify_decision` (`stale operator
  decision`).
* A historical p0002 proof fails `MembershipGate` with the cleaned manifest (`current
  corpus manifest differs from C05`).
* Its completion fails verification under the cleaned plan (`completion plan/mode
  mismatch`).
* The reverse also holds: a cleaned proof refuses the original manifest.

Other operator additions:
* `<purpose> carry-forward` writes only the reviewed VALUE of an old signed decision,
  after verifying its signature, as plain data for a fresh `record`/`freeze`.
* `proof` writes the downstream proof specification of a verified completion. It checks
  the plan's own manifest and the protected-volume guard before writing, then opens the
  gate.
* `derive_cleaned_manifest` is a behavior-preserving split of `build_cleaned_manifest`:
  same checks, same order, same bytes (quality suites pass unchanged).

## 3. Policy and resources: unchanged

No policy or resource semantics changed. The fresh decisions restate the historical
reviewed values (`carry-forward`), bound to `eda4f994…` with evidence digest = admission
digest:

* **Policy:** `c05-production-v2`, `disk-minhash-v2`, `near_threshold 0.8`, 128
  permutations, 32 bands, `longest-source-doc-v1`, `known-lineage-v3`, Gutenberg
  `known_groups_only`, `c05-matcher-v4`, `fuzzy_auto_exclusion false`, `review.enabled
  false`, seed 20260919, `group-hash-v2`.
* **Resources:** workers 16, RAM 48 GiB, compact index 64 GiB, scratch 352 GiB, stage
  24 h, overall 72 h.

`test_reviewed_production_values_admit_the_cleaned_corpus_shape` builds a plan with
2,035 files, 15,087,207 documents and 103,993,099,986 bytes under these values. The
storage worst case and the record, file and byte ceilings all admit, so nothing is
structurally incompatible. The operator's other carried fields (records,
`benchmark_patterns`, `automaton_nodes`, …) stay as reviewed. The cleaned corpus is
smaller than the historical one on every axis.

## 4. Protected benchmark preparation: rebuild required

`create_plan` requires the receipt's `(code_identity, dependency_sha256)` to equal the
running checkout's. `code_identity` is a digest of every `src/**/*.py`. The historical
receipt was built at the compact-engine checkout. `79d788c` already added the quality
modules, and this branch adds `cleaned.py`, so the identity differs. Protected
`build-local` itself also refuses a supplied identity that differs from the running
code. Reusing the old receipt would require weakening that check, which was not done.

Tests:
* `test_stale_preparation_code_identity_refused_fresh_accepted`: a receipt built under
  another identity refuses with `preparation code/dependencies stale` and writes no
  plan; the same plan with a fresh-identity receipt is accepted.

Operator action:
* Rebuild from the existing protected material, after mounting the VHDX, into
  `X:/C05-Protected/prepared-clean-v1` (runbook step 5). Its index depends only on the
  material and matcher policy.
* If the new receipt's `index_sha256` equals the historical one, the carried-forward
  `benchmark_patterns`/`automaton_nodes`/`benchmark_bytes` still apply. Otherwise rerun
  `benchmark-matcher-audit-local` before signing resources.

## 5. Fresh roots and attempt naming

| role | path |
|---|---|
| plan root | `G:/XLM/c05-clean-v1/` (admission, decisions, trust copy, receipt export, `p0001.json`, authorization, proof) |
| C05 output | `G:/C05-output-clean-v1/` |
| C05 scratch | `X:/C05-Scratch-clean-v1/` |
| protected preparation | `X:/C05-Protected/prepared-clean-v1/` |

* The attempt is **`clean-v1-p0001`**: plan `p0001.json` in the fresh plan root.
* `next_plan_sequence` numbers plans per plan root (`pNNNN.json`), so a new generation
  starts at p0001.
* Calling it p0003 would wrongly suggest a continuation of the historical numbering; the
  CLI does not need it.

## 6. Exact workflow

The exact PowerShell sequence is in the
[runbook](../../runbooks/c05-global-preparation.md#cleaned-corpus-c05-rerun-clean-v1-2026-10-04-operator-sequence):

0. clean tree
1. pins
2. saved audit report
3. `admit-cleaned`
4. `carry-forward` + fresh `record`/`freeze`
5. protected `describe` + `build-local` + `benchmark-receipt verify`
6. `plan`
7. `authorize`
8. `status`
9. `run`
10. `resume-check`/`resume`
11. `verify`
12. detach `X:` + `proof`

The agent stopped before every signature and authorization.

## 7. Tests and checks

Environment:
* Windows 11, Python 3.12.13, uv 0.12.19, locked offline environment
  (`uv sync --offline --locked --extra cpu --extra eval`).
* `OMP/MKL/OPENBLAS/NUMEXPR_NUM_THREADS=1`, `TOKENIZERS_PARALLELISM=false`, short
  `--basetemp` under `C:/t`.
* All fixtures are authored; none are live data.

Evidence:
[`evidence/C05-CLEANED-RERUN-READINESS/`](../evidence/C05-CLEANED-RERUN-READINESS/COMMANDS.md).

| run | result |
|---|---|
| Baseline at `79d788c`: C05 + fast MinHash, non-serial, n16 | 345 passed, exit 0 |
| New `tests/test_c05_cleaned_rerun.py`, `-n 0` | **31 passed**, exit 0 (48.5 s) |
| C05 + MinHash (fast/arrow) + C06 (fit/fast/hardening) + Opus equivalence, non-serial, n16 | **586 passed**, exit 0 (final run) |
| Same selection, earlier run (before the `proof` verb) | 1 failed, 584 passed. See below. |
| C05 serial selection (`-m serial -n 0`) | 1 passed, exit 0 |
| Quality suites (production, cleaning, v2, performance, audit, detectors, hardening, final repairs, acceptance fixes), n16, `not serial_exclusive` | 532 passed, 7 skipped (POSIX `/proc`), exit 0 |
| Quality serial (`-m serial_exclusive -n 0`) | 1 passed, exit 0 |
| `ruff format --check src tests scripts` | 805 files formatted, exit 0 |
| `ruff check src tests scripts` | all checks passed, exit 0 |
| `mypy --strict src/xlm/data/exclusion src/xlm/data/dedup/minhash.py src/xlm/data/quality` | no issues in 72 files, exit 0 |
| `git diff --cached --check` (code commit) | exit 0 |

The earlier failure was `tests/test_c06_fast_hardening.py::test_a1_stubborn_descendants_are_killed_and_reaped`:
* `grandchild.pid` was not written before the deadline under 16-worker load.
* It passed 3/3 in isolation (`-n 0`) and in the final selection run.
* The test covers `supervisor`/`fitfast`, which this change does not touch.
* It is recorded as a load-sensitive failure, not a pass.

Requirement-to-test map (`tests/test_c05_cleaned_rerun.py`; chain fixture in
`tests/c05_cleaned_support.py`):

* **The chain fixture** runs the real cleaner chain on authored text:
  original manifest with sources -> audit -> v1/v2 freeze -> dry run -> production ->
  verify (`--compare-sources`) -> cleaned manifest -> post-clean audit -> `report`.
  It yields 731 -> 726 documents in 10 files.
* **Admission path (production kinds):** `test_production_kind_cleaned_manifest_admitted_through_admission_path`
  (`xlm_cleaned_input_manifest` -> protected-mode admission; refuses an authored plan).
* **Totals and semantic digest binding:**
  * `test_cleaned_manifest_semantic_digest_and_totals_bound_exactly`;
  * `test_manifest_not_matching_verified_cleaning_refused`;
  * `test_admission_cli_is_write_once_and_rederivable`.
* **Production-verification mismatch:** `test_production_verification_mismatch_refused`
  (three pins, a self-consistent record with wrong totals, a missing record).
* **Audit receipt mismatch:** `test_audit_receipt_mismatch_refused` (the pre-clean audit
  of the original manifest; result pin).
* **Audit source rehash false:** `test_audit_report_must_verify_with_source_rehash` (5
  cases). Also `test_audit_report_saved_by_windows_powershell_is_accepted`.
* **Evidence drift after admission:** `test_admission_refuses_once_its_evidence_changes`.
* **Admission required / original refuses it:**
  `test_cleaned_manifest_needs_admission_and_original_refuses_it`.
* **Old 11724… decision refused, fresh eda4… accepted:**
  * `test_old_manifest_decision_refused_and_fresh_decision_accepted` (literal digests);
  * `test_historical_decisions_refused_carry_forward_values_only` (CLI; fresh plan
    starts at p0001).
* **Old p0002 completion/proof refused:** `test_historical_p0002_completion_and_proof_refused`.
* **Historical roots never overwritten:** `test_historical_roots_refused_and_never_modified`
  (a tree digest of the historical plans, scratch and output is unchanged after every
  cleaned plan/run).
* **Protected preparation identity:** `test_stale_preparation_code_identity_refused_fresh_accepted`.
* **Admission is not completion:** `test_admission_is_not_a_c05_result` (`verify`
  refuses, `status` is `not_started`, `resume-check` refuses with no state).
* **Exact dedup, near dedup (MinHash), benchmark contamination, known-lineage
  propagation, grouping, splits, membership:**
  `test_fresh_run_dedup_contamination_lineage_splits_membership`.
  * Cross-file exact pair: one survivor.
  * Near pair: the longer survives.
  * Contaminated document and its URL-lineage family member: both excluded.
  * Clean URL family: kept, one split.
  * Membership equals the kept decisions in id order.
  * Cleaner-dropped rows never reach C05.
* **1/2/4/8/16 worker equivalence:** `test_worker_counts_give_identical_membership[2|4|8|16]`
  (membership and private decisions byte-identical to 1 worker).
* **Interruption/resume:** `test_interruption_then_resume_gives_identical_result` at
  `file_committed`, `grouped` and `before_publication`, then `resume-check`, `resume`,
  `verify`.
* **Final verification and proof:**
  * `test_final_verification_and_proof_over_cleaned_manifest` (`verify`, the `proof`
    verb, the gate, original-manifest refusal, tampered membership);
  * `test_proof_refused_while_protected_guard_refuses_writes_nothing`.
* **Reviewed resources:** `test_reviewed_production_values_admit_the_cleaned_corpus_shape`.

Changed existing test:
* `test_historical_isolation_and_plan_digests_keep_their_contract` also pops the new
  optional field when it rebuilds the historical digest.
* It now additionally asserts that a plan body in the pre-change wire format reloads to
  the same digest.

## 8. Requirement ledger

| requirement | status |
|---|---|
| Compact parallel engine on this branch | VERIFIED (already present; ancestor) |
| Cleaned manifest admitted without rewriting; semantic digest `eda4f994…` bound | IMPLEMENTED, VERIFIED (authored) |
| Admission requires completed and verified production cleaning, manifest re-derived from it | IMPLEMENTED, VERIFIED (authored) |
| Admission requires the independent audit of this manifest, source rehash, matching digest and totals | IMPLEMENTED, VERIFIED (authored) |
| Admission is not C05 completion | IMPLEMENTED, VERIFIED |
| Old `11724…` decisions and signatures refused; fresh `eda4…` decisions accepted; values carried forward only | IMPLEMENTED, VERIFIED |
| Historical p0002 completion/proof refused for the cleaned manifest | VERIFIED |
| Historical C05 roots never overwritten (fresh-generation guard) | IMPLEMENTED, VERIFIED |
| Protected preparation: stale identity refused, fresh accepted; rebuild required | VERIFIED |
| Exact/near dedup, contamination, lineage, splits, membership, proof on the cleaned chain | VERIFIED (authored) |
| 1/2/4/8/16 worker equivalence; interruption/resume at 3 points | VERIFIED (authored) |
| Policy/resource semantics unchanged | VERIFIED (no policy code change; the reviewed values admit the cleaned shape) |
| Real admission against `G:` evidence | NOT RUN (operator) |
| Protected preparation rebuild, plan, authorization, run, verify, proof | NOT RUN (operator) |
| Downstream quota report / C06 fit / selection on a cleaned proof | BLOCKED (below) |

## 9. Remaining blockers

1. **Operator inputs (expected).** These require the operator, not engineering:
   * the saved audit `report` JSON, if it was not kept;
   * the trust config path;
   * the paths of the three p0002 decisions and the v4 matcher-policy file;
   * a fresh `protected-root describe` and material spec (new roots and checkout);
   * a protected `build-local` under the final commit;
   * all signatures and the authorization.
2. **Post-C05 consumers (engineering, outside C05).** `quotas.frozen_requirements`, used
   by `quota-report`, C06 `fit-tokenizer` and `select`, reads `manifest["sources"]` and
   the sources' adapter bindings. The cleaned manifest has no `sources`, so these
   commands refuse on a cleaned proof. They need the same lineage join through the
   plan's `input_admission` to the original manifest. This does not block the C05 run
   or its proof.
3. **Not measured.** The production-scale runtime on the cleaned corpus. The compact
   engine report's projection (about 1.5 h scan, 16 workers) remains a projection.

## 10. Next command (operator)

Runbook step 0, then step 3 (`admit-cleaned`), from the final commit of
`feat/c05-clean-v1`.
