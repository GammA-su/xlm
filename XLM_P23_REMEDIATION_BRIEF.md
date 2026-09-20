# XLM — P23 remediation brief

## Basis and limits

This brief responds to the supplied `FINAL_ACCEPTANCE.md` and `reports/P23.md`, dated 2026-09-19. It is a remediation instruction based on their documented findings, not an independent examination of the source code, test logs, or execution evidence. D01–D08 are the reports' defect identifiers. Inspect the actual implementations and reproduce each applicable defect before changing code. Additional defects may exist.

The acceptance report itself blocks production acceptance. Its successful offline tests do not override its failed requirements. Preserve the useful existing implementation and fix it incrementally; do not restart the project or create parallel trainers, artifact stores, evaluators, or configuration engines.

## Execution authority — applies to every stage

Implement code and run bounded offline tests with authored local data and a tiny model. Use `uv` exclusively for Python dependency management and execution. Preserve the user's repository changes and the selected locked dependencies; document any genuinely necessary dependency change.

The user will operate the real system. This brief does NOT authorize live source discovery, pilot downloads, actual corpus preparation, production tokenizer fitting, public checkpoint downloads, official benchmark campaigns, full-size numerical profiling, 50M/150M/300M training, paid calls, final-set access, remote publication, or pushes. Prior bounded authorizations mentioned in the reports are not blanket future authorization.

Use local fixture files and an explicitly permitted loopback test server. Keep external network access denied in normal tests. Dependency installation, when necessary, is separate from dataset or model acquisition.

Implement the actual operator workflows, not only their refusal paths. For external validation still requiring the user, deliver exact commands and required artifacts, then report `NOT RUN — OPERATOR ACTION REQUIRED`. Missing operator execution must not be confused with a missing implementation. Do not manufacture approvals on the user's behalf.

## Common remediation discipline

For each stage:

1. Inspect the relevant source, contracts, configuration, tests, and command call paths.
2. Write a bounded adversarial regression that exposes the documented defect in the current implementation; preserve the failing evidence when reproducible. If the report is stale, demonstrate the current behavior instead of fabricating a failure.
3. Make the smallest coherent repair in the existing system.
4. Verify the affected public CLI path, not only private helper functions.
5. Rerun relevant regressions and update a defect-to-code-to-test-to-result ledger.
6. Preserve failed, skipped, and not-run evidence. Do not change thresholds, metrics, split definitions, or test assertions merely to obtain green results.

Freeze benchmark definitions and comparison estimands. Historical outputs with unreliable identity, incomplete coverage, or defective statistics must not silently become trusted because the code was repaired. Introduce version/invalidation or explicit legacy status as appropriate; preserve originals.

## Stage 1 — D01: immutable artifact request equivalence

### Required repair

`ArtifactStore.publish_artifact` must not return an existing artifact merely because the requested ID/configuration hash matches. Validate the full relevant publication identity and compare the proposed payload manifest against the existing verified artifact.

Separate the recipe/production key from output content identity. Include the appropriate source/input lineage, producer code/dependency/serializer/schema identity, resolved behavioral configuration, file checksums, and sizes. Exclude cosmetic timestamps from deterministic identity.

For an identical verified request/output, reuse safely. For a conflicting output or relevant provenance under an existing immutable identity, reject explicitly or produce a distinct content-addressed artifact under the documented policy. Never overwrite the previous completed artifact or report it as the new payload.

Audit artifact-kind, artifact-ID, and manifest-path validation. Reuse existing containment, locking, staging, and publication mechanisms.

### Required tests

- Same ID/config, identical content and relevant provenance: idempotent reuse.
- Same ID/config, different same-length content: conflict, not success.
- Same bytes but different relevant input/producer provenance: correct identity distinction or explicit conflict.
- Corrupt existing payload and incomplete publication: rejected.
- Concurrent publishers: only the permitted completed outcome, with conflict evidence retained.
- Path escape attempts using supported Windows/POSIX forms: rejected.

## Stage 2 — D06: execute and record the actual frozen experiment

### Required repair

Do not validate one source tree and call already-imported implementation objects from another. Run each frozen job in a fresh controlled process that imports XLM and its allowlisted plugins from the immutable code snapshot or a verified build of that snapshot.

Bind the executable plan, authorization, code snapshot, dependency lock/environment, data and tokenizer artifacts, component versions, seeds, and runtime policy. Recompute and validate identities rather than trusting file labels. Control import paths so a user `PYTHONPATH`, editable checkout, or previously installed live package cannot silently override the selected snapshot. Record the origins and identities of the implementation actually executed.

Replace placeholders such as `P05_TRAINER_CHECKPOINT`, `TORCH_P05`, and generated label-like plan hashes with genuine, defined identities. This requires fixing execution as well as metadata: realistic-looking hashes over the wrong tree are still wrong.

Restore checkpoints only under the compatible declared plan. Changed behavior requires a new plan/fork and appropriate user authority. Do not relabel legacy checkpoints as proven reproducible without evidence.

### Required tests

- Enqueue code/config A, edit the working tree to B, execute: the job runs A.
- Alter the frozen snapshot: fail integrity checks before training.
- Inject a conflicting live package/import path: fail or remain bound to the correct snapshot, with observed import evidence.
- Modify plan/job/authorization/input identity independently: reject.
- Fresh-process resume preserves actual code/config/component/data identities and computational state.
- Snapshot/run identity reaches checkpoint and evaluation receipts without placeholder fields.

## Stage 3 — D03: make the configured research workflow executable

### Required repair

Connect the existing mixture loader, packing policies, component registries, and checkpoint reconstruction to one resolved execution path shared by direct training, queue execution, and resume. Preserve smoke caps and the separate authorized production path. Do not remove caps merely to make a configuration pass.

The direct smoke path currently supports only the narrow combination documented in the report. Complete generic registered architecture/objective/optimizer/tokenizer selection and multi-source data configuration. Each declared field must change the intended runtime behavior, remain explicitly non-executable draft information, or fail as unsupported. No ignored fields or implicit synthetic fallback.

Enforce `PackingPolicy.max_document_tokens` under the existing declared split/reject policy. Do not silently truncate useful text, change context, or drop targets. Preserve source/document/lineage identity and exact eligible-target accounting.

Do not label matched-byte plans as executed exposure equivalence. Demonstrate matched-document/byte execution with small differently tokenized authored inputs, or keep that capability explicitly unsupported until it is implemented.

### Required tests

- Two distinct local sources with a declared mixture pass through YAML -> prepare/plan -> CLI train -> checkpoint -> fresh-process resume -> evaluation/export.
- Verify actual source/target traces and mixture exposure within the frozen rounding/drift rules; do not rely only on configured percentages printed to logs.
- Change the mixture and show that execution changes; restoring a run restores its original mixture/data state.
- A registered test component in each category is actually selected and reconstructed through the public path. Use observable behavior or explicit instrumentation to detect ignored selections; no-op parity alone cannot prove invocation.
- A trainable auxiliary objective updates and resumes correctly.
- Oversized document cap, exhausted source, unsupported field, and missing artifact: correct explicit behavior.
- Nonmultiple final target budget: exact committed masks and next data IDs.
- Validate production-size configurations via shape/meta inspection without launching full-size numerical runs.

## Stage 4 — D02: bounded acquisition and preparation

### Required repair

Use persistent shared accounting across redirects, retries, restarts, metadata requests, partial files, caches, decompressed data, and final publication. A restart must not replenish the remaining transfer/request/storage allowance. Specify the transport layer actually measured; do not claim unimplemented network-overhead accounting.

Verify cache identity and content integrity, not just length. Make the corpus record cap a real processing/retention gate with defined semantics, rather than an unused plan field.

Implement the required bounded selected-record/row-group route through the existing transport and reader interfaces, or explicitly identify unsupported views. Never substitute a whole unbounded shard for a selected-row request. Preserve original row/file locators and distinguish a selected-record artifact from the original full-file object.

Bound prepare subprocess output capture and aggregate preparation resources. Do not accumulate unlimited stdout/stderr in memory or reset limits independently for each stage. Sampling process RSS is useful evidence, not a hard memory guarantee.

### Required tests

- Local HTTP redirects/retries count against one durable budget.
- Interrupt near a transfer limit and resume: no fresh allowance.
- Same-length corrupted cache file: rejected.
- Exceed record, decompression, output, scratch, and aggregate stage caps: stop without publishing incomplete completion.
- Concurrent workers reserve from one remaining budget.
- Large child-process output is bounded/spooled under a cap.
- Selected rows are the declared rows and retain correct locators, including fresh-process recovery where supported.
- All tests remain local/offline; no real source fetch is authorized by this brief.

## Stage 5 — D04/D05: implement scoped evaluation and prevent false full scores

### Required repair

Keep default remote/final task loading blocked. Complete an explicit operator-run path for acquiring or importing the permitted, pinned development assets and evaluating those exact assets locally with the frozen harness task/scoring definitions.

The evaluator need not download data itself. It must accept verified official-source assets supplied through the declared preparation workflow, rather than remain limited to synthetic JSON fixtures. Validate requested split/subdataset/item membership before evaluation. Never inherit upstream default final splits or allow an unverified third-party cache to bypass membership checks.

Bind expected evaluation scope independently of returned predictions. Record expected/scored/error/omitted/duplicate item coverage per task and required BLiMP subdataset.

An all-four-task limited run is still incomplete when it does not cover the declared scope. Withhold the full-suite index and full-suite promotion claims. Do not insert zeroes for missing tasks, silently shrink denominators, or redefine benchmark scores.

A deliberately frozen search subset can be complete relative to its own manifest, but must be labeled by that scope. It is not equivalent to a runtime `--limit` truncation or a full public benchmark score.

### Required tests

- All four tasks return limited subsets: partial coverage, no full-suite index.
- Missing or duplicate items/subdatasets cannot masquerade as complete coverage.
- One failed example cannot shrink the expected population silently.
- A complete intentionally frozen development scope computes its correctly labeled score.
- Protected/default/wrong-split requests are refused before data or cache access.
- Pinned installed task rendering/scoring is tested against authored fixtures without downloading official data.
- Data/tier/scorer/coverage changes invalidate the appropriate cached results.

Provide exact operator commands for the later official development validation. Do not run it now.

## Stage 6 — D08: repair paired comparisons and BLiMP bootstrap

### Required repair

Pair inputs by recorded `(init_seed, data_seed)` before selecting any primary comparison, checking eligibility, or computing intervals. Validate every included pair. Reject duplicate/missing/mismatched pair identities. The headline result must not change because CLI file arguments are reordered.

Preserve the frozen distinction between within-run item uncertainty and across-training-seed variation. If a primary pair is reported, its selection rule must be predeclared and deterministic, not argument order or observed score.

For BLiMP macro cluster resampling, preserve repeated cluster draws with their multiplicity. Repeated draws must not collapse back to one entry merely because they share an original subdataset ID. Preserve the established macro estimator rather than switching to item-weighted averaging.

### Required tests

- Shuffled baseline/candidate argument orders yield the same paired headline and intervals.
- Same-shaped/otherwise plausible but wrong seed pairs fail eligibility.
- Duplicate and missing seed pairs fail.
- Unequal cluster sizes expose macro-versus-micro weighting mistakes.
- A tiny enumerated bootstrap distribution agrees with analytically computed results.
- A resample with values `[0, 0, 1]` averages to `1/3`, not `1/2` after collapsing a repeated draw.
- Nonzero known paired effects and arm swapping are tested; identical checkpoint arms giving zero is insufficient by itself.

Invalidate or label affected legacy statistics; do not silently retain old claims after the repair.

## Stage 7 — D07: single-copy tied-weight export

### Required repair

Store each intended unique deployed tensor once and persist an explicit alias reconstruction map. Keep model parameter counts distinct from physical exported tensor storage.

Restore the declared tied `Parameter` identity after load. Reject inconsistent alias metadata or conflicting serialized values. Keep the serializer generic through existing architecture contracts, not hard-coded to one Transformer naming convention.

### Required tests

- Expected unique parameter/tensor storage without duplicated embedding/head payloads.
- Fresh-process load preserves logits, greedy output, and aliases.
- Missing/conflicting alias metadata fails clearly.
- Existing numerical parity remains intact.

## Stage 8 — one complete final acceptance rerun

Fix the stale doctor capability text. Re-run the entire audit against one frozen final tree so the top-level audit returns zero; retain the earlier nonzero historical evidence rather than rewriting it. Do not present a collection of later checks as a clean full-run result.

Use separate clean environments for base-only and CPU-plus-evaluation verification. Confirm torch is absent in the base environment and required numerical tests actually execute in the CPU environment. State the coverage of mypy, linting, skipped tests, and help-only versus functional CLI tests accurately.

The decisive offline workflow must use at least two authored sources and an explicit nondefault registered mechanism through the same public plan/queue/train/resume path intended for the operator. It must consume the prepared artifacts, not an implicit fallback. Include nonmultiple budgets, interruption, exact replay, evaluation coverage, comparison, export, and fresh reload.

Deliver:

- Updated `FINAL_ACCEPTANCE.md`, `STATUS.md`, and CLI coverage map.
- One D01–D08 -> source change -> regression -> command -> actual result mapping.
- Actual executed code/config/lock/input identities and retained evidence references.
- A clear list of remaining defects, unsupported combinations, and external validations not run.
- Exact operator commands for live source checks, real official development evaluations, per-size GPU profiling, and protected deployment checks, without executing them.

A successful offline remediation can establish implemented/offline-verified capability. It does not establish source rights, live dataset compatibility, full-size training quality, final-set isolation, benchmark wins, or scientific novelty.

## First instruction for the coding agent

Read the two P23 reports and shared contracts, inspect D01 in the existing code, and propose a focused repair plan with adversarial regressions. Wait for approval of that plan before implementing Stage 1. Work through later stages in separate reviewed patches. Keep actual operational runs under the user's control.
