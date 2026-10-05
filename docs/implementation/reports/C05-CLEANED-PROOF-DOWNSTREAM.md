# Post-C05 cleaned-proof downstream compatibility (2026-10-05)

Branch `fix/cleaned-proof-downstream`, from `85e21f3`. Authored fixtures only. No
access to G: or X:. No real tokenizer fitting, counting, selection, tokenization or
training.

## Problem

`quotas.frozen_requirements` read `manifest["sources"]` from the C05 input manifest.
The production cleaned manifest (`eda4f994…`) has no `sources` by design. Every
consumer that needs quota lineage therefore refused a cleaned proof:

* `quota-report`;
* `fit-tokenizer` (including `--plan-only`) and `fit-tokenizer-reference`;
* `verify-tokenizer-fit` and `verify-kept-index --membership/--sources`;
* `select`.

## Design

There is one verifier: `cleaned.requirements_manifest(plan, manifest, proof_paths)`.
It is read-only and fails closed. It returns the "requirements manifest", which
supplies source and quota provenance only:

| C05 plan | returns |
|---|---|
| no `input_admission` (original manifest, e.g. p0002) | the manifest itself, unchanged; a cleaned-kind manifest refuses |
| `input_admission` (cleaned) | the ORIGINAL manifest, after all checks below |

Checks for a cleaned plan, in order:

1. The manifest's self-digest equals `plan.input_manifest_digest`, and its kind is a
   cleaned kind.
2. The admission is `admission.json` beside the proof's plan, where the runbook writes
   it. The location is only a lookup: the record is trusted only after it is bound by
   digests, never because of its name.
3. `verify_admission`, the plan-time verifier, reruns unchanged. It re-derives the
   whole record from its recorded evidence:
   * the operator pins;
   * the cleaned manifest bytes, re-derived from the VERIFIED production state;
   * the production receipt and verification record;
   * the independent post-clean audit and its artifacts;
   * the saved `report`;
   * the original manifest, by lineage digest and raw SHA-256.

   The proof's manifest path must equal the admitted manifest path.
4. `record.digest == plan.input_admission.admission_digest`.
5. `record.original_manifest.digest == plan.input_admission.original_manifest_digest`.
6. `binding(record) == plan.input_admission`. This covers all eight bound digests:
   production receipt and result, verification, audit receipt and result, and the
   cleaned-manifest file SHA-256.
7. `record.mode == plan.mode`.
8. The original manifest is re-read and re-bound: digest and raw SHA equal the record,
   and each file descends from the cleaned file in the same position.
9. The plan's `files` and `source_seals` re-derive exactly from (cleaned manifest,
   original manifest).

`frozen_requirements(manifest, quotas, ifm, *, provenance=None)` behaves as follows:

* Allocations still come from `manifest["files"]`, the C05 membership manifest.
* `sources`, adapter bindings, the quota SHA, the IFM split and the Common Pile split
  come from `provenance`.
* Without provenance, or with provenance equal to the manifest, it runs exactly the
  historical code path. A cleaned manifest then refuses with a `C05Error`; before this
  change it failed with a `KeyError`.
* A distinct provenance must:
  * be self-digested;
  * be the cleaned manifest's lineage original;
  * carry identical (component, view, upstream) labels in the same order.

  So the allocations, and the whole requirements dict, are identical to the
  original-manifest requirements.

Wiring:

* `MembershipGate`, from `open_gate`, and `fitfast.StreamedC05`, from `open_streamed`,
  carry the proof's (plan, manifest) paths. They expose `requirements_manifest()`.
  It is lazy and memoized per gate, so gate-only consumers never read the lineage:
  `count-tokens`, `tokenize-selection`, `freeze` and training shard verification.
* `quotas.view_requirements(view, quotas, ifm)` is used by `quota-report` and `select`.
* `tokenizer_fit._requirements` now takes the provenance. All seven call sites pass the
  view's provenance, or for `plan_from_proof` / `plan_from_proof_fast` the verified
  lineage built from the proof spec.
* `cleaned.plan_inputs` is refactored into `_load_original` + `_inputs`, with identical
  behavior, so that plan time and downstream share one derivation.

Not changed:

* `ExecutionPlan` and `InputAdmission`, so the plan identity and the `046381…` digest
  stay as they are;
* the completion schema;
* the proof (`ProofSpec`) schema;
* membership;
* the admission record;
* the C05 engine.

Downstream never re-checks the C05 plan's code identity, so the real proof stays
valid without regeneration. No new operator decision or CLI flag was added.

## Files

* `src/xlm/data/exclusion/cleaned.py`: `ADMISSION_FILE`, `requirements_manifest`,
  `_load_original` and `_inputs`.
* `src/xlm/data/exclusion/quotas.py`: the `provenance` parameter and
  `view_requirements`.
* `src/xlm/data/exclusion/gates.py`: `C05View.requirements_manifest`, plus
  `MembershipGate(proof_paths=…)` and `.requirements_manifest()`.
* `src/xlm/data/exclusion/transport.py`: `open_gate` passes the proof paths.
* `src/xlm/data/exclusion/fitfast.py`: `StreamedC05.proof_paths` and
  `.requirements_manifest()`, `plan_from_proof_fast`, and three call sites.
* `src/xlm/data/exclusion/tokenizer_fit.py`: `_requirements`, `plan_from_proof`, fit and
  verify.
* `src/xlm/data/exclusion/selection.py`: `select` uses `view_requirements`.
* `src/xlm/data/exclusion/control.py`: `quota-report` uses `view_requirements`.
* `tests/c05_cleaned_support.py`: `clean_chain` is split out of `make_chain`, with
  unchanged behavior.
* `tests/test_c05_cleaned_downstream.py` (new, 13 tests).
* Docs: the runbook section "Post-C05 consumers over the cleaned proof", this report,
  and STATUS.

## Tests

The fixture runs the real chain on authored data:

1. The generated Mix-01-shaped corpus (`c05_synthetic_flow.prepare`: all 11 components,
   IFM views, Common Pile upstreams, adapter bindings).
2. One planted row that the cleaner drops (hard NUL rule), within the 2 % component
   guardrail.
3. The real cleaning chain: Phase-A audit, v1/v2 freeze, dry run, production, verify,
   cleaned manifest, post-clean audit and report.
4. `admit-cleaned` into the plan root, fresh decisions, `plan --admission`, authorize,
   run, verify, and the `proof` CLI, all in authored mode.
5. A historical C05 over the original manifest in its own roots, as the p0002 analogue.

| requirement | test |
|---|---|
| cleaned proof + valid admission accepted; quotas exactly equal the original-manifest requirements (same dict, same digest) | `test_cleaned_proof_requirements_equal_original_manifest_requirements` |
| `fit-tokenizer --plan-only`, fast fit, `fit-tokenizer-reference`, `verify-tokenizer-fit` (both), `verify-kept-index --membership`, `count-tokens`, `select` (requirements digest = original), `tokenize-selection`, `freeze`, all through the operator CLI | `test_cleaned_proof_c06_fit_verify_count_select_tokenize_freeze` |
| ordinary proof unchanged | `test_original_manifest_proof_behavior_unchanged`, plus the unchanged existing C05/C06/selection suites |
| missing admission refused (library and CLI; nothing written) | `test_missing_admission_refused` |
| changed admission refused (re-signed record and broken self-digest) | `test_changed_admission_refused` |
| admission digest mismatch (another genuinely valid record; plan binding another digest) | `test_admission_digest_mismatch_refused` |
| original-manifest digest, other bound digests and re-derived plan inputs mismatched | `test_original_manifest_digest_mismatch_refused` |
| original manifest raw bytes changed (same semantic digest) or deleted | `test_original_manifest_raw_bytes_changed_refused` |
| wrong cleaned manifest (same bytes at another path; another digest; foreign provenance) | `test_wrong_cleaned_manifest_refused` |
| historical p0002-analogue proof cannot stand in | `test_historical_proof_cannot_stand_in_for_the_cleaned_proof` |
| cleaned files/counts never replaced by the original's | `test_cleaned_files_and_counts_are_never_replaced_by_the_original` |
| plan/completion/proof need no regeneration (schemas pinned, plan consumed in place) | `test_plan_completion_and_proof_need_no_regeneration` |
| lineage is lazy for gate-only consumers | `test_consumers_without_quota_lineage_never_read_the_admission` |

Commands were run on Windows 11, CPython 3.12.13, from worktree
`F:\Project\xlm-post-c05-clean-v1`, after `uv sync --offline --locked --extra cpu
--extra eval`. Test runs set `OMP/MKL/OPENBLAS/NUMEXPR_NUM_THREADS=1` and
`TOKENIZERS_PARALLELISM=false`. Prefix for every command below:
`uv run --offline --locked --no-sync --extra cpu --extra eval`.

| command | result |
|---|---|
| `python -m pytest tests/test_c05_cleaned_downstream.py -n 0 -q --basetemp=C:/t7 -p no:cacheprovider` | 13 passed (26 s), exit 0 |
| `python -m pytest tests/test_c05_cleaned_downstream.py tests/test_c05_cleaned_rerun.py tests/test_c06_tokenizer_fit.py tests/test_c06_fast.py tests/test_c06_fast_hardening.py tests/test_c05_selection.py tests/test_c05_acceptance_audit.py tests/test_c05_control.py tests/test_c05_detached_volume.py tests/test_mix01_quotas_6b.py -n 16 --dist=worksteal --max-worker-restart=0 -q --basetemp=C:/t8 -p no:cacheprovider` | 289 passed, **1 failed**, exit 1. The failure is `test_c06_fast_hardening.py::test_a1_stubborn_descendants_are_killed_and_reaped`: no grandchild PID file was written under 16-worker load (a process-pool deadline test that does not touch requirement code). |
| the same node alone, `-n 0 --basetemp=C:/t9` | 1 passed, exit 0 |
| `python -m pytest tests/test_c05_engine.py tests/test_parallel_tokens.py -n 16 --dist=worksteal …` | no tests ran: the suite requires `--dist=loadgroup` (its own guard) |
| the same with `--dist=loadgroup --basetemp=C:/t10` | 58 passed, exit 0 |
| `ruff check` / `ruff format --check` on `src/xlm/data/exclusion` and the two test files | clean |
| `mypy --strict src/xlm/data/exclusion tests/test_c05_cleaned_downstream.py tests/c05_cleaned_support.py` | no issues (45 files) |
| `git diff --check` | clean |

NOT RUN:

* the full offline suite and the serial selection: this is not the release gate;
* the quality suites: `src/xlm/data/quality` was not changed;
* `quota-report` through the CLI over a cleaned proof: the command requires a
  protected gate, and authored proofs are refused there by design. Its requirement
  step (`view_requirements` on a cleaned gate) is covered, and `quota_report` itself
  is unchanged;
* a protected-mode cleaned chain;
* any real G:/X: artifact. In particular, re-verifying the real admission downstream
  has not been exercised.

## Real C05 proof

The proof `G:/XLM/c05-clean-v1/clean-v1-p0001.proof.json` should remain valid
unchanged. No plan, completion, proof, membership or admission field or digest
changed, and downstream does not re-check the C05 code identity. This is by
construction and from authored tests. It was not checked against the real files.
The first real `fit-tokenizer --plan-only` is the live check. It needs everything
the admission recorded to still be at its recorded path, unchanged:

* `G:/XLM/c05-clean-v1/admission.json`;
* the cleaned manifest;
* the original manifest;
* `G:/XLM/quality/clean-production-v1`;
* `G:/XLM/quality/audit-clean-v1`;
* the saved audit report;
* the cleaned corpus root.

## Limitations

* Each command that needs lineage re-verifies it once per gate or view. The fast fit
  verifies it at plan time and again at fit time. The cost is metadata only: stats of
  the cleaned outputs, plus hashes of the small audit and cleaning artifacts. It was
  not measured on real data, and it runs inside the fit's deadline.
* The admission is located only as `admission.json` beside the plan. A record kept
  elsewhere refuses. Copying it byte-identically beside the plan is accepted, because
  only its digests are trusted.

## Next

From this commit's checkout, with `X:` detached, run the runbook section
[Post-C05 consumers over the cleaned proof](../runbooks/c05-global-preparation.md#post-c05-consumers-over-the-cleaned-proof-clean-v1-p0001),
starting with `fit-tokenizer … --plan-only`.
