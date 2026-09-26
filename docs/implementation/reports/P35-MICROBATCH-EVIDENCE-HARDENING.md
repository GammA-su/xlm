# P35 micro-batch evidence hardening (not a new P35 milestone)

Branch `research/p35-microbatch-evidence-hardening`, created exactly from the
ASTRA-reviewed pilot-readiness commit `d7942ba7cfa1c15f8c9eefe8770c58b655208d84`
(`research/p35-pilot-readiness-certified`, tag `p35-pilot-readiness-certified`).
Worktree `D:\Project\xlm-p35-microbatch-hardening`. Contract:
[P35 scientific contract](P35-SCIENTIFIC-CONTRACT.md) §B/§E/§T. Findings closed:
ASTRA M1–M6 ([REVIEW.md](../evidence/P35-READINESS-ASTRA/REVIEW.md),
[pilot readiness](P35-PILOT-READINESS.md) "Required pre-study repairs").
Evidence: [P35-MICROBATCH-HARDENING/](../evidence/P35-MICROBATCH-HARDENING/).

Scope, as instructed. This pass implements only the six receipt/study blockers,
their bounds and the pilot non-regression. It prepared no data. It built no
Mix-01 order manifest. It ran no 32M pilot, no B8/B16/B32 or mixture study and
no research campaign. It chose no margins and pushed or merged nothing.

The pilot draft still has no `training.update_payload_receipt`. The C0 barrier,
1M/4M recovery cadence, M5 order semantics, the 2 GiB input cap and the P34
producer design are unchanged.

## 1. Verdict

**MICRO-BATCH EVIDENCE CERTIFIED — READY FOR FORMAL STUDY**, for the receipt
itself, on the local Windows/CUDA environment. What this covers:

- **All six ASTRA blockers are closed:** five fixed, and one (duplicate
  compact aliases) closed by refusing that representation.
- **Adversaries:** all eight were killed through scientific assertions
  (8/8 mutants).
- **Runtime:** every relevant Trainer, producer, checkpoint, evaluator, queue
  and workflow selection is green. The one exception is the documented,
  pre-existing P17 CRLF golden-byte check.
- **v1 unchanged:** `global_update_payload_digest_v1` is byte-identical to the
  starting commit.
- **Pilot unchanged:** receipt-disabled behavior is unchanged, and no receipt
  path executes.

This certifies that `microbatch_grouping_v2` evidence can now be trusted. It
does **not** make a study executable. A formal B8/B16/B32 study still needs:

- real Mix-01 inputs and order manifests;
- preregistered practical and non-inferiority margins, which remain unset;
- a measured 50M receipt cost;
- a frozen plan and the user's authorization.

The implementing agent also self-certified this pass (§11 risk 3). The 32M
pilot keeps the receipt disabled.

## 2. Environment

- **Machine.** Windows NT 10.0.26200, RTX 4090, driver 596.49. At start about
  9.2 GB was held by desktop applications at 4 % utilization
  ([nvidia-smi-before.txt](../evidence/P35-MICROBATCH-HARDENING/nvidia-smi-before.txt)).
- **Software.** Python 3.12.13, torch 2.14.0+cu126, CUDA runtime 12.6,
  NumPy 2.5.3, lm-eval 0.4.13, pytest 9.1.1, ruff 0.16.8, mypy 2.3.1.
- **Wrapper.** `R` = `& docs/implementation/evidence/P35-MICROBATCH-HARDENING/env.ps1`.
  It runs `uv run --offline --locked --no-sync --extra cuda --extra eval` on the
  existing locked environment `G:\Project\xlm-p35-m3\.venv-p35-m3`. `PYTHONPATH`
  is this worktree's `src` and `tests`. It sets one OMP/MKL/OpenBLAS/NumExpr
  thread, `TOKENIZERS_PARALLELISM=false`, offline Hugging Face and
  `CUBLAS_WORKSPACE_CONFIG=:4096:8`. TEMP goes to the ignored
  `.hardening-scratch/`.
- **No dependency change.** There was no install or sync, and no torch was
  installed. `pyproject.toml`, `uv.lock` and `.python-version` are unchanged
  (`git diff d7942ba HEAD` on the three files is empty).
- **Network.** One `git fetch origin --tags`, a read of the user's own
  repository, obtained the starting commit. The pre-existing checkout at
  `D:\Project\xlm-final-integration` has unrelated uncommitted P24 work, so
  this pass uses a separate worktree and left that checkout untouched.
- **Fixtures.** All data is authored or synthetic: generated token shards, the
  byte tokenizer, and 1-layer 16-wide models. Nothing here is live-data
  evidence.

## 3. Pre-change findings (code at `d7942ba`, confirmed before any edit)

| Astra | Location at `d7942ba` | Mechanism |
|---|---|---|
| M1 | `update_payload.canonical_update` | The producer path compared only `sum(len(mb.input_ids)) == sum(pending.rows)` and then hashed `pending`. A consumed tensor replaced after production was never compared with it. |
| M2 (A7) | `checkpoint.load_checkpoint` step 2b → `ScientificState._saved_update_payloads` | Chain links and chain-vs-LR rows were checked. Nothing compared the history with `checkpoint_meta` step/C or `data_state.committed_valid_targets`. The queue then set trainer counters from the metadata and the batcher from the data state. |
| M3 | `Trainer._train_update` step 8 | Order: `batcher.commit()`, then `_update_in_doubt = False`, then `record_lr`, then `payloads.commit()`. A chain-commit failure left an LR-only history on a checkpointable boundary. |
| M4 | `evaluation.training_state_fingerprint` | `science_receipts` digested the train-start RNG, LR receipts and the runtime-receipt count. The payload chain, its head and its staging were not included. |
| M5 | `update_payload.canonical_from_prepared` → `_table_codes` | Codes were re-ranked by first occurrence *of the code*. A table repeating a string gave equal decoded provenance two digests. A negative code wrapped silently in NumPy. |
| M6 | `checkpoint.load_checkpoint` | Every receipt check was gated on `saved_policy == current_policy`, and receipt presence is not part of the policy identity. A changed-policy fork skipped all receipt validation, so it could silently drop a parent chain or start a receipted child mid-lineage. |

## 4. Design and results per blocker

### 4.1 Blocker 1 — the consumed tensors are bound to the receipt

`update_payload.bind_consumed(pending, microbatches)` runs inside
`canonical_update` before the digest, before any compute and before device
transfer. It reads CPU values only and never synchronizes: a device tensor is
refused by `_int_array` before `.numpy()`. It requires:

- the same number of microbatches as `pending.rows`, and the same partition;
- each model/objective input equal to its slice of the prepared arrays, value
  for value (`np.array_equal`, not row counts). The inputs are `input_ids`,
  `labels`, `loss_mask`, `position_ids`, `segment_ids` and
  `metadata["input_attention_mask"]`, the last by its boolean meaning;
- the light metadata the trainer reads (packing mode, counts) equal;
- any provenance a microbatch carries equal to the prepared provenance of its
  rows. The stock producer's microbatches carry none;
- the prepared update equal to its producer seal:
  `compute_content_digest() == content_digest`. The seal binds rows, arrays,
  the string table and provenance as the child encoded them.

Only then is `pending` hashed, so the digest of the consumed input and the
digest of the prepared payload are the same by proof, not by assumption. The
synchronous path already hashes the microbatches handed to the trainer.

Results (runtime tests through the real Trainer and a real spawned
`process_depth1` producer with content verification):

| Case | Result |
|---|---|
| Direct path: receipt row digest equals the digest of the returned microbatches; every forward call received exactly those `input_ids` | VERIFIED |
| Producer path: 3 updates; chain identical to the direct run; each row equals `canonical_from_prepared(pending)`; forward inputs equal consumed tensors equal prepared slices | VERIFIED |
| Same shape, one input token changed in a detached replacement tensor | refused (`microbatch 0 input_ids differs`) before any forward; zero rows, no staged receipt, C = 0, weights, data and optimizer unchanged; the regenerated update then binds and matches the direct run |
| Same shape, one label changed | refused (`microbatch 0 labels differs`), same guarantees |
| Provenance correct, whole `labels` tensor replaced | refused (`microbatch 1 labels differs`) |
| Tensors correct, provenance row shifted on the pending update | refused (`producer seal`) |
| A carried provenance copy that disagrees with its prepared rows | refused (`target_doc_ids differs`) |
| Swapped, regrouped (B16 against a B8 pending update), dropped, or packing-mode-relabelled microbatches | refused |
| CUDA tensor handed to the receipt, with `torch.cuda.synchronize` and `Stream.synchronize` patched to fail | refused (`is on cuda`); no synchronization |

**Residual (documented, not claimed closed):** after binding, the zero-copy
buffer is shared by the pending update and the consumed tensors. An in-place
write by model or objective code *during* the update, to a later microbatch,
is outside this receipt's threat model. That code already has the same trust
as the training code itself.

### 4.2 Blocker 2 — history against committed data C (Astra A7)

`training/science.check_receipt_history` runs from
`CheckpointManager._check_receipt_lineage`. That is load step 2b, before any
model, objective, optimizer, schedule, scaler, data or RNG restore, and before
the M5 order check. It requires:

- the chain to verify from genesis;
- the LR receipts to be one contiguous history from (step 1, C 0), with
  `schedule_counter == C + N` for every row;
- the chain and LR rows to agree update by update;
- `len(rows) == checkpoint step`;
- the final history `C == checkpoint committed C == data_state.committed_valid_targets`.
  An integer is required; a bool or a missing value is refused.

Neither history is truncated or extended to fit. Save time adds an O(1) check
(`check_receipt_alignment`, lengths plus tails against step, trainer C and
batcher C), so an incoherent receipt set is never published.

| Case (authored, resealed checksummed checkpoint unless noted) | Result |
|---|---|
| A7: self-consistent LR + chain row one update beyond data C | refused before restore: `claims C=48, ahead of the committed data state C=32` |
| Data state C ahead of the history | refused: `committed work without its required receipts` |
| History behind the data (last row dropped from both) | refused, same |
| LR ahead of the chain | refused: `update by update` |
| Checkpoint metadata C +1, or step +1 | refused: `metadata records` / `checkpoint step 3` |
| Final partial update 16/16/8 | validates exactly; last row `[3, 32, 8]`; an at-budget resume adds nothing |
| Final partial C off by one (data and meta 39, history 40) | refused: `C=40, ahead of the committed data` |
| Astra's in-memory A7 construction (stage, commit and `record_lr` with no training) | `_save_checkpoint` refuses (`not coherent`); nothing published |

After each refusal the model weights, the batcher state and the empty
optimizer state are unchanged, and the chain is still empty.

### 4.3 Blocker 3 — atomic receipt authority, poisoned boundary

With the receipt declared, `Trainer._train_update` keeps the boundary in doubt
after `batcher.commit()` and calls `ScientificState.commit_receipted_update`.
That call first checks everything that can refuse: the LR bound, lock-step
lengths and tails, and a staged receipt for exactly this `(step, C, N)` via
`UpdatePayloadChain.prepare_commit`. Only then does it append the LR row and
commit the chain row. `stage` has already preflighted the row and byte
bounds.

A failure inside the commit is re-raised as `ScienceReceiptCommitError`, a
`RecoveryRequiredError`, and the boundary stays in doubt. Nothing is rolled
back. As an extra check, `to_checkpoint` refuses to serialize receipt sets of
unequal length. Receipt-disabled runs keep the historical ordering
byte-for-byte.

Fault injection *between the LR append and the chain commit* on update 2:

| Check | Result |
|---|---|
| Error type and cause | `ScienceReceiptCommitError` (is a `RecoveryRequiredError`); cause is the authored fault |
| State at the poisoned boundary | data and optimizer committed (trainer C = batcher C = 32); LR rows 2, chain rows 1; `_update_in_doubt` true |
| Next update | `train_step` raises `RecoveryRequiredError`; `optimizer.step` is not called again |
| Checkpoints after the poison | `_save_checkpoint`, `save_terminal_checkpoint("final")` and a direct `CheckpointManager.save_checkpoint` all refuse; the published set is still t0 and t16 |
| `trainer.train()` | raises; `termination_reason = failed`; run ledger `FAILED`; no t48 or final checkpoint |
| Real frozen queue worker (exported tree with the fault, science-v1 receipt plan, CUDA) | job `FAILED`, reason names `ScienceReceiptCommitError`, no `completion` classification, run record and ledger `FAILED`; the only checkpoint is C = 32 with 1 LR and 1 payload row |
| Recovery | the prior t16 checkpoint verifies and reloads in a fresh trainer, which trains to the budget with a chain identical to the uninterrupted run |

### 4.4 Blocker 4 — evaluator guard covers receipt state

When the receipt is declared, `training_state_fingerprint` adds the component
`update_payload_receipt`: the digest of `UpdatePayloadChain.guard_state()`.
That state holds the type, the payload and chain versions read from the
module at call time, the genesis, the columns, the rows, the head and the
staged receipt. The `science_receipts` digest also gains a declaration flag,
but only when a receipt is declared.

Receipt-free runs therefore keep the historical component set, which appears
in every outcome receipt's `guard.verified`. An evaluator that attaches a
chain to a receipt-free run still shows as a `science_receipts` change. M2
semantics are unchanged: a live change fails the attempt, publishes a FAILED
outcome, sets `_evaluation_compromised` and raises `RecoveryRequiredError`.

| Evaluator mutation at the C = 16 boundary | Result |
|---|---|
| chain head (last row's link) altered | caught: `changed` includes `update_payload_receipt`; attempt FAILED; checkpoint refused |
| a row added (staged and committed) | caught |
| the last row removed | caught |
| a receipt left staged | caught |
| declaration removed (`update_payloads = None`) | caught |
| module version field altered (`PAYLOAD_VERSION`) | caught |
| untouched receipt | `changed == []`; `update_payload_receipt` is in `verified` |
| receipt-free run | `update_payload_receipt` is not in `verified` (historical); an attached chain is caught as `science_receipts` |

### 4.5 Blocker 5 — compact string alias rule (Option B: refuse)

`check_compact_table` runs in `canonical_from_prepared`. It refuses a table
whose aliases repeat a string, a non-string alias, non-integer codes, and any
code outside `[0, len(table))`, which includes a negative code NumPy would
silently wrap. The error names alias positions and table size, never
provenance values.

- The stock `_encode` builds tables with `setdefault`, so they are unique. Its
  digests are unchanged (§4.7).
- Equal decoded values in the duplicate representation (Astra's construction)
  are refused: `repeats one string at aliases <first index> and <table size>`.
  The test asserts the exact indices.
- A semantically different decoded value gives a different digest and is
  accepted.

Option B was chosen over re-canonicalization because it keeps v1 for every
accepted input. It also makes the invalid representation explicit rather than
silently equal.

### 4.6 Blocker 6 — changed-scientific-policy fork semantics

`CheckpointManager._check_receipt_lineage` applies to ordinary resume and to
every fork. It is a no-op when neither side declares the receipt.

| Parent → child | Rule | Tested result |
|---|---|---|
| Receipt presence differs (either direction), same or changed policy | refused before restore; enabling or disabling the receipt is a new experiment from initialization | refused, state untouched (4 cases) |
| Pre-readiness parent (science.json without `update_payloads`) → receipted child | refused (`receipt-free or pre-readiness`) | refused |
| Pre-readiness parent → receipt-free child | historical behavior | fork accepted, trains |
| Legacy parent (no science.json) → receipted child | refused (presence) | refused, state untouched |
| Receipt version differs (`…_v2` resealed) | refused for resume and fork; a new version is a new experiment | refused, state untouched |
| Same policy, both receipted, fork | inherits and continues the chain (data lineage) after full A7 validation | rows `[1]` → `[1, 2, 3]` |
| Changed policy, both receipted, C > 0 | refused: the chain certifies one policy's lineage from initialization, and the fork does not adopt the parent's LR history (M1) | refused |
| Changed policy, both receipted, fork of the initial C = 0 state | allowed after validating the empty parent chain; the child begins its own chain at genesis; nothing is inherited | child row `[1, 0, 16]` |

M5 document-order fork semantics are untouched: the order check still runs at
step 2c, after this rule.

### 4.7 Receipt versioning decision

- **Digests.** `global_update_payload_digest_v1`, `xlm-update-payload-chain-v1`,
  the genesis and the row layout are unchanged. The hardening refuses inputs;
  it does not re-hash accepted ones. [v1_parity.py](../evidence/P35-MICROBATCH-HARDENING/v1_parity.py)
  ran on a clean `git archive d7942ba` export and on the hardened tree. Over 3
  authored updates at B8/B16/B32, on both the direct and producer paths, it
  gave identical digests and heads (`v1_parity_base.json` ==
  `v1_parity_head.json`). A test pins those base-computed values.
- **Newly refused inputs.** Duplicate aliases, out-of-table codes, malformed
  digests and oversized rows were never valid stock outputs. v1 stays v1.
- **Track.** `microbatch_grouping_v2`'s meaning (required receipt version and
  identical chain head) is unchanged, so no new track version was created.
- **Evidence.** Extraction now also refuses a chain whose presence or version
  disagrees with the frozen envelope's `training.update_payload_receipt`. This
  is ambiguity refusal, not a new field. The evidence version stays
  `xlm-science-run-evidence-v3`.

### 4.8 Bounds

| Constant | Value | Derivation |
|---|---|---|
| `MAX_CHAIN_ROWS` | 400,000 (was 1,000,000) | Largest planned run: 300M/6B = 91,554 updates at 65,536 targets per update. 50M/1B = 15,259; 150M/3B = 45,777; pilot = 489. A 1B-parameter model at 20 targets/parameter (20B targets = 305,176 updates) is headroom only, not a plan. |
| `MAX_CHAIN_ROW_BYTES` | 192 | The worst-case row is 173 bytes (step < 10⁶, C < 10¹⁵, N < 2³¹, two 64-hex digests); checked at `stage` and on load. |
| `MAX_CHAIN_BYTES` | 77,604,096 | 400,000 × 194 + 4 KiB |
| science.json with receipt | ≤ `MAX_SCIENCE_STATE_BYTES` (128 MiB, the existing read bound) | Checked at save for receipted runs, so no unloadable checkpoint is published. At the row bound, lock-step chain and LR rows (about 300 bytes/update worst case) plus 8 MiB for ledgers stay below it (tested). |

`stage` refuses at the bound before the update runs, so `commit` never fails
on it. Loading refuses more rows than the bound, bool or string counters,
non-hex digests and oversized rows. The previous 1,000,000-row bound was
inconsistent with the 128 MiB read bound: roughly 255 MB of chain plus LR rows
would have published an unloadable checkpoint.

### 4.9 Resume, duplicates and final partial updates

- Resume from t16 continues with rows `[1, 2, 3]`, identical to the
  uninterrupted chain.
- Staging a replay of update 1 after resume is refused (`does not follow`).
- A resumed trainer whose counters forgot the resume (step 0 with a restored
  1-row chain) is refused at its next update; no duplicate row is appended.
- A saved chain with a duplicated row is refused on load.
- The final partial update validates exactly (§4.2).

### 4.10 Pilot non-regression

The receipt-free test patches every hardening entry point to fail if called:
`canonical_update`, `bind_consumed`, `canonical_from_prepared`,
`canonical_from_microbatches`, `check_receipt_history`,
`commit_receipted_update` and `check_receipt_alignment`. Under those patches
it runs:

- a full science-v1 `train()` with an evaluation cadence and checkpoint
  events;
- an ordinary resume from t0 to the budget;
- a changed-policy fork, which keeps the M1 semantics (the LR history is not
  adopted);
- a real `process_depth1` producer run (the pilot route).

None raise, and no receipt path executes. The endpoint `science.json` has
exactly the historical key set. The guard component set is historical (§4.4).

The complete pilot-readiness selection is green (§6): the C0 barrier, 1M/4M
recovery checkpoints, endpoint fail-stop, queue FAILED classification, the
wall allowance, 9P capacity and the input-cap stat diagnostic.

## 5. Adversaries (scratch mutants; nothing mutated is committed)

[mutate_hardening.py](../evidence/P35-MICROBATCH-HARDENING/mutate_hardening.py)
re-opens each adversary by reverting exactly one defense. Each mutant runs in
a fresh `git archive HEAD` export with a single-anchor edit, after
`py_compile`, with `xlm` imported from the export. A mutant is KILLED only if
three things hold:

- pytest exits 1;
- every listed node FAILED (never errored);
- every failure message is a scientific assertion (`AssertionError`,
  `DID NOT RAISE`, or a `pytest.raises` message mismatch), never an import,
  syntax, type or name error.

An unmutated control export must pass all nodes first. Full records, including
the edits, per-node messages and the runner SHA-256, are in
[mutations.json](../evidence/P35-MICROBATCH-HARDENING/mutations.json).

Final round: tested HEAD `6b94a92`, runner SHA-256 prefix `af65ebb57f40d957`.
The control passed 27/27. **8/8 KILLED** in 246.2 s, each with pytest exit 1
and `xlm` imported from the export.

| # | Adversary (defense reverted) | Killing nodes | Observed failure |
|---|---|---|---|
| 1 | Detached consumed input tensor accepted (row-count-only binding restored) | 4 runtime (token, label, whole-tensor, provenance shift) + 3 pure | `DID NOT RAISE PayloadReceiptError` ×7 |
| 2 | LR/payload chain ahead of committed data accepted (history-vs-data check disabled) | 3 runtime (A7, data ahead, meta C) + 2 pure | `DID NOT RAISE IncompatibleCheckpointError` / `ScientificPolicyError` |
| 3 | Payload commit failure still allows checkpoint (in-doubt cleared before the receipts) | poisoned-boundary and `train()` tests | `assert (False)` on `_update_in_doubt`; `assert (32 == 32 and False)` |
| 4 | Evaluator mutates the chain undetected (guard component removed) | head, row, staged + untouched-component test | `DID NOT RAISE RecoveryRequiredError` ×3; `verified` lacks the component |
| 5 | Duplicate aliases silently alter equality (table check removed) | duplicate alias; code −1 | `DID NOT RAISE PayloadReceiptError` ×2 |
| 6 | Changed-policy fork silently drops or continues required receipt history (old policy gate restored) | on→off, off→on, on→on changed policy | `DID NOT RAISE IncompatibleCheckpointError` ×3 |
| 7 | Payload row duplicated after resume (stage contiguity removed) | resume/stale-counter test + readiness replay test | `DID NOT RAISE PayloadReceiptError` ×2 |
| 8 | Final partial C off by one accepted (tolerance of one target) | pure partial-off-by-one + runtime partial endpoint | `DID NOT RAISE`; `Regex pattern did not match` (a different refusal) |

**First round, kept, not hidden:** [mutations-first-round.json](../evidence/P35-MICROBATCH-HARDENING/mutations-first-round.json),
HEAD `e03c11a`, reported 7/8. Mutant 3 read "SURVIVED" although both of its
nodes failed on genuine assertions about the poisoned boundary. The failure
messages were `assert (False)` and `assert (32 == 32 and False)`. JUnit reports
a pytest-rewritten plain `assert` without an `AssertionError` prefix, which
the runner's message filter did not accept.

The classifier was widened to accept that form (commit `6b94a92`), and the
whole round was rerun once. No test, assertion or product code changed between
the two rounds.

## 6. Commands, exits and results

All pytest runs used `R python -m pytest …` with `-p no:cacheprovider` and
JUnit XML plus a log under the evidence directory. Parallel runs used one xdist
controller, `-n 8 --dist=worksteal --max-worker-restart=0 -m "not serial"`.
Serial and CUDA-heavy selections ran with `-n 0`, one at a time.

| Artifact | Selection | Exit | Result | Pytest s |
|---|---|---:|---|---:|
| (development) | `test_p35_readiness_payload/m4/runtime.py -n 0`, right after the product edits | 0 | 65 passed | 161.9 |
| `readiness-hardening` | `test_p35_hardening_payload`, `test_p35_hardening_runtime`, `test_p35_readiness_{runtime,recoverability,planner,payload,m4}`, `test_p35_astra_boundaries`; `-m "not serial" -n 8` | 0 | **213 passed**, 0 skipped (the CUDA device-tensor test executed) | 179.1 |
| `affected` | `test_p35_science_pilot`, `test_p35_checkpoint_{cadence,retention,rescore}`, `test_p35_eval_{training,cadence,cuda}`, `test_p35_science`, `test_checkpoint`, `test_p34_commit_boundary`, `test_p34_adversarial`, `test_prefetch`, `test_prefetch_training`, `test_trainer_mixture`, `test_trainer`, `test_trainer_data`, `test_p35_m5_runtime`, `test_p35_wall_allowance`; `-m "not serial" -n 8` | 0 | **278 passed**, 0 skipped | 419.2 |
| `comparison` | `test_p35_m4_{stats,manifest,eligibility,promotion,evidence}`, `test_p35_m5_{evidence,order,stream,pilot}`, `test_comparison`; `-n 0` | 1 | **305 passed, 1 failed**: `test_legacy_p17_modules_are_byte_identical_to_certified_m3` | 120.4 |
| `serial` | `test_p35_m3_toy_flow`, `test_trainer_mixture`, `test_prefetch`, `test_p35_hardening_runtime`, `test_p35_astra_boundaries`; `-m serial -n 0` | 0 | **4 passed**, 66 deselected. These are the M3 CUDA toy flow, this pass's real frozen queue worker, and Astra's two real-worker transports. | 389.8 |
| `workflow` | `test_p35_science_workflow.py -n 0` | 0 | **3 passed**: direct/queue/resume under both attention policies, plus the M2 cadence. It overlapped with the CPU-only mutation round. | 684.3 |
| `mutations` | `python mutate_hardening.py mutations.json` | 0 | control 27/27; 8/8 killed (§5) | 246 s wall |
| `v1_parity_{base,head}.json` | `python v1_parity.py` on a `git archive d7942ba` export and on the tree | 0 / 0 | identical digests and heads | — |
| `receipt-spot-producer.json` | `python receipt_spot_producer.py` | 0 | §7 | 24.3 s wall |

**The single failure** is the known Windows checkout artifact documented in
M4 §25.2, M5 §19.8 and the ASTRA closeout. The CRLF working-tree SHA
`31c22cce…` is compared against the LF golden `04e961ac…`. The five P17 Git
blobs at HEAD hash to the goldens (`04e961ac`, `07d9b7a1`, `54603332`,
`36498a18`, `f30254bd`), and `git diff d7942ba HEAD` on them is empty. It was
neither weakened nor skipped. **This is not an all-tests-green claim.**

**Test corrections during development.** Each fixed a defect in a new test,
never a product outcome, and none weakened an assertion:

- one pure test's consumed microbatches still carried list-mode provenance, so
  it now strips provenance, as the producer does, to reach the seal path, and
  it asserts both refusal routes;
- one runtime tamper test reloaded with a different checkpoint plan, which M3
  refused first; the milestones were aligned;
- the poisoned-boundary test replaced `at_boundary` (a no-op when nothing is
  due) with a direct `CheckpointManager.save_checkpoint` call.

**Not run:** the full offline acceptance gate. It is not required by the
AGENTS test policy for a focused pass, and no full suite is claimed. CPU-only
frozen legs remain BLOCKED by the pre-existing CUDA-wheel environment (M1–M3).

## 7. Performance (bounded; not a production number)

The product change adds work on the producer path (value comparison plus the
seal recompute), so one bounded receipt-enabled/disabled check was repeated
*on that path*:
[receipt_spot_producer.py](../evidence/P35-MICROBATCH-HARDENING/receipt_spot_producer.py)
→ [receipt-spot-producer.json](../evidence/P35-MICROBATCH-HARDENING/receipt-spot-producer.json).
It ran after all other GPU work, with about 9.1 GB held by the desktop at 6 %
utilization.

- **Design.** RTX 4090, real Trainer, 6,768-parameter authored model, fp32,
  eager, strict. Real spawned `process_depth1` producer with content
  verification. B8, context 64, 2,048 targets per update. ABBA blocks, each
  with 4 warmup updates excluded and 16 measured.
- **Bounds.** 163,840 targets (≤ 200k), 24.3 s wall (≤ 5 min), 714,721 bytes of
  output (≤ 1 GiB).

| Block | Receipt | Targets/s | Receipt CPU ms/update | of which binding ms | Stream syncs/update | Peak CUDA allocated |
|---|---|---:|---:|---:|---:|---:|
| A | off | 31,086.9 | 0 | 0 | 1.0 | 70,661,120 |
| B | on | 29,271.6 | 0.981 | 0.679 | 1.0 | 70,661,120 |
| B | on | 27,704.9 | 0.963 | 0.650 | 1.0 | 70,661,120 |
| A | off | 27,109.6 | 0 | 0 | 1.0 | 70,661,120 |

- **Receipt work.** Mean receipt CPU work is 0.972 ms/update on the producer
  path; binding is about 0.66 ms of it.
- **Comparison with ASTRA.** ASTRA measured 0.377 ms on the synchronous path,
  where hashing is unchanged, and the paths are not directly comparable.
- **Throughput.** Mean block throughput changed by −2.10 %. The disabled arm
  alone spans 13 % across its blocks, so no population overhead is estimated.
- **Memory and synchronization.** Zero GPU-allocation delta, and no added
  explicit synchronization. Process RSS was about 1.38 GB.
- **Scope.** None of this is a 50M, pilot or production number.

## 8. Static checks

| Command | Exit | Result |
|---|---|---|
| `R python -m ruff check` on the 6 changed source files, 3 test files and evidence scripts | 0 | All checks passed |
| `R python -m ruff format --check` on the same files | 0 | already formatted |
| `R python -m mypy --follow-imports=silent` on the 6 changed source files | 0 | no issues (only the existing unused `lm_eval` config note) |
| `git -c core.whitespace=cr-at-eol diff --check` | 0 | clean |
| `git diff d7942ba HEAD -- pyproject.toml uv.lock .python-version` | 0 | empty |

## 9. Astra findings: disposition

| Astra | Disposition | Evidence |
|---|---|---|
| M1 consumed-tensor binding | **FIXED** | §4.1; adversary 1 |
| M2 chain/LR vs data C (A7) | **FIXED** | §4.2; adversaries 2, 8 |
| M3 receipt commit failure | **FIXED** (poisoned boundary, atomic science commit, preflighted bounds) | §4.3; adversary 3; real queue worker |
| M4 evaluator guard | **FIXED** | §4.4; adversary 4 |
| M5 duplicate compact aliases | **Deliberately REFUSED representation** (Option B) | §4.5; adversary 5 |
| M6 changed-policy fork | **FIXED** (explicit lineage rule, refuse before restore) | §4.6; adversary 6 |
| F1 9P under repeated retirement errors | unchanged, FOLLOW-UP ONLY (not a study blocker) | readiness report |
| F2 absent/None masks, overflow, alternate providers | partly strengthened: counters must be `int`, digests hex, rows bounded. The evidence claim is still restricted to the stock input domain. | §4.8 |

## 10. Requirement ledger

| # | Acceptance criterion | Status | Evidence |
|---|---|---|---|
| 1 | Actual consumed tensors bound to the receipt | VERIFIED | §4.1; adversary 1 |
| 2 | LR/payload history cannot exceed or lag committed data | VERIFIED | §4.2; adversary 2 |
| 3 | Receipt commit failure fail-stops (recovery required) | VERIFIED | §4.3; adversary 3; real queue worker FAILED |
| 4 | Evaluation guard protects all receipt state | VERIFIED | §4.4; adversary 4 |
| 5 | Compact alias semantics explicit and safe | VERIFIED (refusal rule) | §4.5; adversary 5 |
| 6 | Changed-policy fork semantics explicit and safe | VERIFIED | §4.6; adversary 6 |
| 7 | Receipt growth bounded | VERIFIED | §4.8 |
| 8 | Resume cannot duplicate or skip rows | VERIFIED | §4.9; adversary 7 |
| 9 | Final partial updates validate exactly | VERIFIED | §4.2; adversary 8 |
| 10 | `microbatch_grouping_v2` fails closed on receipt ambiguity | VERIFIED | evidence declaration/version/presence refusals; A7 at evidence level; v2 track unchanged and green |
| 11 | Receipt-disabled pilot behavior unchanged | VERIFIED | §4.10; readiness selection green |
| 12 | High-risk adversaries killed | VERIFIED | 8/8 (§5) |
| 13 | Relevant runtime tests green | VERIFIED, with the one documented pre-existing P17 CRLF failure | §6 |
| 14 | No real B8/B16/B32 study | VERIFIED | none run |
| 15 | No real data or pilot | VERIFIED | authored fixtures only |
| — | v1 digest semantics preserved | VERIFIED | §4.7 parity against `d7942ba` |
| — | Bounded receipt cost check | VERIFIED (tiny diagnostic); 50M cost NOT RUN | §7 |
| — | Full offline acceptance gate | NOT RUN | not required for this focused pass |
| — | Practical/NI margins, real study inputs, authorization | OUT OF SCOPE / BLOCKED for the study | §11 |

## 11. Remaining risks

1. **In-place mutation during compute** (§4.1) of the shared zero-copy buffer
   by model or objective code is not detected. It would require training code
   that writes into its own inputs.
2. **Receipt cost at 50M/B8 is unmeasured.** The bounded spot (§7) is a tiny
   model. On the producer path, binding (the seal recompute plus the value
   comparison) is about two thirds of the 0.97 ms/update receipt work, and it
   scales with the update's bytes. Measure it at the real shape before
   freezing a study plan. All arms pay it equally.
3. **Self-certification.** This pass was implemented and locally certified by
   one agent. Unlike ASTRA, it is not an independent review. An independent
   adversarial read before the first formal study is prudent.
4. **Binding level is not versioned in records.** Receipts made by the
   pre-hardening code are indistinguishable by version. Only authored test
   fixtures exist. A formal study must run from this commit or later, and M4
   already requires equal `code_hash` across arms.
5. **The formal study is still not executable.** It needs real Mix-01 inputs,
   two real order manifests, preregistered practical and NI margins, a
   measured profile and cost, a frozen plan and the user's authorization.
6. **Evidence at 91k rows.** Evidence extraction reads `science.json` under a
   64 MiB bound. A 300M/6B receipted checkpoint (about 91,554 rows, about
   27 MB) fits, but the 400,000-row design maximum would not. If runs approach
   that size, raise the extractor bound deliberately.

## 12. Files and commits

- **Source:**
  - `data/sampling/update_payload.py`: binding, alias rule, bounds,
    `prepare_commit`, `guard_state`;
  - `training/science.py`: `commit_receipted_update`,
    `check_receipt_alignment`, `check_receipt_history`;
  - `training/trainer.py`: `ScienceReceiptCommitError` and the receipted
    commit order;
  - `training/checkpoint.py`: save-time coherence and byte bound,
    `_check_receipt_lineage`;
  - `training/evaluation.py`: guard component;
  - `comparison/science_evidence.py`: declaration match.
- **Tests:**
  - new: `test_p35_hardening_payload.py`, `test_p35_hardening_runtime.py`;
  - updated: `test_p35_m4_evidence.py::publish_run` declares a published
    chain, as real frozen runs do.
- **Evidence:** `env.ps1`, `v1_parity.py` plus its base and head JSON,
  `mutate_hardening.py`, `mutations.json`, `receipt_spot_producer.py`,
  `receipt-spot-producer.json`, JUnit XML and logs.
- **Docs:** this report, [science-v1](../../science-v1.md), [STATUS](../STATUS.md).
  `.gitignore` gains `.hardening-scratch/`.

Commits on `d7942ba` (`git log --reverse --oneline d7942ba..HEAD`):

1. `249d7a9` feat(science): bind consumed microbatches, refuse duplicate aliases, bound the payload chain
2. `871fce2` feat(science): coherent receipt authority, pre-restore history checks and fork semantics
3. `4448ef7` feat(science): evidence refuses a payload receipt that disagrees with its declaration
4. `a1a6672` test(p35): microbatch evidence hardening regressions and v1 digest parity
5. `e03c11a` test(p35): hardening adversary runner (fresh export per mutant, assertion kills only)
6. `6b94a92` test(p35): mutation classifier accepts pytest-rewritten assert messages; keep first round
7. docs(p35): this report, science-v1, STATUS and evidence (closing record)

Nothing was pushed or merged, and no tag was created.

## 13. Next step

Review and integrate this branch:
`git -C D:\Project\xlm-p35-microbatch-hardening log --reverse --oneline d7942ba..HEAD`.

The 32M pilot proceeds unchanged, with the receipt disabled, through its
existing binding, preflight and authorization path. When the user decides to
run the formal study, use this prompt:

**"Prepare the formal P35 B8/B16/B32 microbatch study plan on top of
research/p35-microbatch-evidence-hardening. Enable
training.update_payload_receipt in all three arms. Measure the receipt's cost
at the real 50M/B8 shape with a bounded profile. Record the user's
preregistered practical and non-inferiority margins in the comparison
manifest. Produce a frozen plan for user authorization. Do not launch any run."**

## 14. Independent Astra review (2026-09-26)

**Verdict: SAFE AFTER SPECIFIC FIXES.** This is an independent review of the
receipt implementation, not authorization to run the study. Starting clean
HEAD was `e7644a3e667db2892ece23b4ca8d737c83b800c8` on
`review/p35-microbatch-astra`, worktree `G:\Project\xlm-p35-microbatch-astra`.
The source diff from `d7942ba7cfa1c15f8c9eefe8770c58b655208d84` and all requested
prior reports were reviewed before tests or product edits. The independent
[PRETEST](../evidence/P35-MICROBATCH-HARDENING-ASTRA/PRETEST.md) recorded
**BLOCKED pending independent verification**. Sections 1-13 above remain the
implementing agent's historical account; this section records the later review.

Evidence is under
[`P35-MICROBATCH-HARDENING-ASTRA`](../evidence/P35-MICROBATCH-HARDENING-ASTRA/COMMANDS.md).
Its command ledger gives exact selections, environment, exit statuses and
failed first attempts. All data used here is authored. No network, install,
live data, 32M pilot, formal microbatch study, research campaign, push or merge.

### 14.1 Six blockers and independent attacks

| Area | Independent finding and evidence | Status |
|---|---|---|
| Consumed tensors | `bind_consumed` compares all six consumed array fields value-for-value, partition and light metadata, optional carried provenance, then verifies the producer seal. Same-shape token/label/mask/position changes, detached replacements and both provenance/tensor mismatches refuse before compute. Device tensors refuse without receipt-induced CUDA synchronization. | VERIFIED at the pre-compute binding boundary |
| LR / payload / data C | The loader validates full contiguous histories, every schedule counter, checkpoint step, metadata C and data C before mutable restore. Both directions of disagreement, partial endpoint, duplicate and intermediate-C attacks refuse without truncation. | VERIFIED |
| Receipt authority | A3 throws after LR append and data commit but before chain append. No successful boundary, next update or normal checkpoint; previous checkpoint reload remains usable. The real frozen queue worker reports failure. | VERIFIED fail-stop authority; **not** rollback of every in-memory field |
| Evaluator guard | Rows, head, staging, declaration/version and receipt presence are covered. A newly found unreadable-state escape was fixed; invalid staging, rows and whole receipt now require recovery. Receipt-disabled behavior retains its historical branch. | IMPLEMENTED / VERIFIED after fix |
| Compact aliases | Unique stock `_encode` output works; duplicate decoded strings, non-strings and invalid codes refuse. Changed decoded strings change the digest. The encoder's dictionary interning emits unique strings. | VERIFIED |
| Resume / forks | Same-version/same-policy on-to-on continues; on/off changes, version changes, legacy and pre-readiness evidence manufacture refuse. C>0 policy change refuses; C=0 re-origin starts at genesis. Ordinary resume and both fork-policy branches share lineage validation. | VERIFIED |

The scratch adversaries are separate from Opus's mutation runner, in
[`test_independent.py`](../evidence/P35-MICROBATCH-HARDENING-ASTRA/test_independent.py).
All eight attacks reached their intended behavior. There are no syntax/import
error kills and no claim that all eight were killed:

| Attack | Result |
|---|---|
| A1: mutate after bind, before forward | **Survives intentional Python injection.** The committed row retains the earlier digest while a forward spy observes the changed token. This explicitly demonstrates the trust boundary. |
| A2: matching LR/chain, wrong data C | Refused before restore, in both directions; metadata-step and partial-endpoint variants also refuse. |
| A3: joint receipt commit exception | `ScienceReceiptCommitError`, then `RecoveryRequiredError` for both next update and terminal checkpoint. Prior authoritative checkpoint resumes to completion. |
| A4: mutate an already-staged receipt during evaluation | Guard detects `update_payload_receipt`; evaluation poisons live training. |
| A5: identical decoded alias at two codes | Explicit duplicate-alias refusal; unique/changed/non-string/out-of-table controls reach their distinct intended outcomes. |
| A6: changed-policy fork from pre-readiness checkpoint | Refused before any restored state can manufacture a receipt history. |
| A7: duplicate final row and recompute chain links | Refused: recomputed hashes do not make duplicated update coordinates contiguous. |
| A8: valid final C, incorrect intermediate C | Refused: endpoint equality cannot replace update-by-update continuity. |

### 14.2 After-binding mutation assessment

For the current stock formal-study path, this is **FOLLOW-UP ONLY**, conditional
on freezing the inspected code/model/objective. It is not a universal guarantee
for every Python extension accepted by the engine. The stock Transformer reads
token IDs through its embedding; mask operations allocate new masks; RoPE reads
position indices and modifies its caches/activations, not input positions; CE
uses `torch.where` for safe labels and masked losses without writing labels or
loss masks. The no-op and auxiliary objective paths inspected do not modify the
batch inputs. Producer arrays are process-local received data, not a live
shared-memory writer from the child process.

The tiny CUDA route diagnostic clones forward inputs and confirms that the stock
forward leaves them unchanged on direct, producer and resumed executions. CE's
non-mutating behavior is source-reviewed; the forward observer is not presented
as an objective-write detector. A malicious/replaced model, objective or hook
can still mutate after binding, as A1 proves. Such an extension would invalidate
the equality claim and needs a separate mutation review or enforcement before
use. No redundant payload hashing or GPU synchronization was added here.

### 14.3 Three newly found defects and repairs

1. **Planned histories could not be fingerprinted/extracted.** The generic
   artifact JSON helper has a 100,000-node / 8 MiB contract. The evaluator
   hashed full LR and payload histories with it; all planned 1B/3B/6B metadata
   probes failed. The boundary digest also used it and exceeds the node bound
   for the 3B/6B update lists. Added bounded streaming canonical JSON hashing
   for declared receipt histories only, preserving the existing SHA-256 byte
   semantics, with caller row limits and a 128 MiB byte ceiling. Generic artifact
   limits and receipt-disabled branches are unchanged. The three planned-scale
   tests now fingerprint real trainer state and compute boundary digests.
2. **An unreadable evaluator mutation escaped fail-stop handling.** A staged
   receipt containing an unserializable object made verification raise before
   `_evaluation_compromised` was set. For a guard captured with a declared
   receipt, inability to fingerprint is now a failed verification, so the
   controller publishes failure and refuses continuation/checkpointing. The
   historical receipt-disabled exception behavior is retained.
3. **M4 extraction accepted a wrong LR schedule counter.** Checkpoint loading
   already rejected it. Extraction now invokes the same full receipt-history
   validator after the existing declaration/boundary checks. Keeping those
   earlier checks preserves established diagnostic messages.

Eight permanent regressions are in `tests/test_p35_receipt_history_regressions.py`.
The first M4 scratch probe had an incorrect fixture-helper call; that failed
before the attack and was corrected, then the actual acceptance defect was
reproduced. It is not counted as an attack kill. The initial two hardening
regression failures were changed refusal messages, not accepted corruption;
their validation order was repaired and five focused related cases passed.

### 14.4 Reader and serialization bounds

[`bounds-compatibility.json`](../evidence/P35-MICROBATCH-HARDENING-ASTRA/bounds-compatibility.json)
contains arithmetic, not invented training receipts. With global valid targets
65,536 and the stock two AdamW groups, reserving 24 serialized characters for
each finite nonnegative binary64 LR value:

| Planned campaign | Updates | Final partial | Chain row-list bytes | LR row-list upper bytes | Total with 4 KiB header + 8 MiB ancillary allowance |
|---|---:|---:|---:|---:|---:|
| 50M / 1B | 15,259 | 51,712 | 2,474,410 | 1,389,329 | 12,256,443 |
| 150M / 3B | 45,777 | 24,064 | 7,479,362 | 4,258,021 | 20,130,087 |
| 300M / 6B | **91,553** | 48,128 | 14,986,626 | 8,560,965 | 31,940,295 |

The historical 91,554 figure above was an arithmetic error. The active science
guide/source comment now use 91,553. The longest planned chain row is 162 bytes,
well below 192. All planned row histories fit the 64 MiB reader; the largest
leaves 43,557,177 bytes after rows and the 4 KiB header allowance. No reader
bound was raised.

**Qualification:** row counts alone do not prove that every possible complete
`science.json` fits 64 MiB. Auxiliary ledgers and optimizer-group count vary;
the 8 MiB allowance is not a code-enforced maximum. Before the study, the frozen
resource plan must budget the entire file within the reader limit. The review
does not certify arbitrarily expanded metadata. The 400k row ceiling is wider
internal storage, not a promise of extraction at every allowed storage size.
Its maximum chain allowance is 77,604,096 bytes. With two LR groups, the stated
numeric domain and the same ancillary allowance, the conservative combined
bound is 129,192,704 bytes, below 128 MiB (134,217,728). Other configurations
remain subject to the actual whole-file save check. This is mutual coherence
under the declared shape, not a universal full-file bound derived from rows.

Streaming history hashing removes the smaller, previously hidden metadata
ceilings. It remains O(history length); large-scale guard work and full campaign
metadata growth have not been benchmarked as a research run.

### 14.5 Real CUDA / producer / fresh process

[`cuda-routes.json`](../evidence/P35-MICROBATCH-HARDENING-ASTRA/cuda-routes.json)
records four distinct child processes: synchronous, `process_depth1`, first
checkpoint segment and resumed segment. All use the same authored payload.
The actual CPU microbatches' canonical payload/provenance digests equal the
committed receipt rows. Direct, producer and final resumed LR histories/chains
are identical. Updates are `[96, 96, 5]`; resume begins from C=96. Executed
target exposure across the three complete routes is 591 targets (the same
197-target stream repeated three times),
37.14 s, 1,360,753 scratch bytes. These are tiny CUDA diagnostics, not quality
measurements. The diagnostic's clone/equality observer itself synchronizes;
it is separate from the production receipt's CPU-only path.

### 14.6 Actual 50M-shape receipt cost

[`cost-50m.json`](../evidence/P35-MICROBATCH-HARDENING-ASTRA/cost-50m.json) and
[`cost_50m.py`](../evidence/P35-MICROBATCH-HARDENING-ASTRA/cost_50m.py) record one
bounded diagnostic on RTX 4090, Python 3.12.13, torch 2.14.0+cu126. Architecture
is the repository 50M preset: **49,883,648 unique deployed/trainable model
parameters**, no trainable objective auxiliaries, stock AdamW, BF16 activations
with FP32 master parameters, statistical efficient SDPA. B8, context 512,
global valid targets 65,536, actual producer route. The tokenizer/text is
authored byte-token data; this is not representative language-model training.

One complete warmup, one receipt-off update and one receipt-on update use fresh
identical initialization per block: **196,608 total targets**, **20.281 s**,
**3,685,549 scratch bytes**. No checkpoints/weights were published by this
measurement. The subprocess watchdog is 295 s, allocator fraction 0.5, and
output inventory remains below 1 GiB. No retry spent additional measurement
targets. Each update has sixteen eight-sequence microbatches and one short
one-sequence tail because valid-target masks determine the global boundary.
The 65,536-target authored stream is repeated across the three blocks; the
196,608 total is compute exposure, not unique corpus exposure. The model has
199,534,592 bytes of FP32 master parameters; stock AdamW's two FP32 moments
account analytically for 99,767,296 elements / 399,069,184 bytes, plus one step
scalar per parameter tensor. Objective state/auxiliaries are empty. These state
figures are analytic accounting, not a saved optimizer inventory; the measured
GPU peaks include the actual training allocations. A separate meta-device
inventory consumes no training targets.

| Metric | Receipt off | Receipt on |
|---|---:|---:|
| End-to-end update seconds | 2.078709 | 1.890417 |
| Targets/s | 31,527.26 | 34,667.48 |
| Canonicalization CPU ms, including bind | 0 | 7.3663 |
| Bind CPU ms, nested in canonicalization | 0 | 4.1820 |
| Payload digest CPU ms | 0 | 4.1978 |
| Stage + commit CPU ms | 0 | 0.0355 |
| Total measured receipt CPU ms, no double-counted bind | 0 | **11.5996** |
| Peak GPU allocated bytes | 3,738,034,688 | 3,738,034,688 |
| Peak GPU reserved bytes | 4,236,247,040 | 4,236,247,040 |
| Parent RSS bytes, sampled after update | 1,790,234,624 | 1,795,002,368 |
| Process-tree RSS bytes, sampled after update | 2,353,168,384 | 2,357,882,880 |
| Explicit stream synchronize calls | 1 | 1 |

Each block also has two measurement-only global synchronizations. This counts
explicit Python calls, not implicit driver synchronization. The observed
throughput delta is **+9.96%** for receipt-on, but the fixed-order single samples,
cold producer/optimizer startup and shared desktop make a causal speedup or
population overhead claim unjustified. The defensible result is bounded CPU
receipt work and unchanged observed GPU peaks/sync count. No optimization or
training-quality conclusion follows.

### 14.7 Formal v2 eligibility and pilot

`microbatch_grouping_v2` can support the stated claim for a frozen stock study:
initial weights, M5 membership/order, exact global payload chain, global batch
and boundaries, optimizer/LR, runtime/code/environment and evaluation are
MUST_MATCH; only `microbatch_sequences` is intentionally varied. Final weight
digest may differ. Missing/null payload evidence is unknown and INELIGIBLE;
ambiguous declaration or malformed chain is rejected during extraction. The
new counter check closes the independently demonstrated extraction gap.

M4 also retains historical pre-M5 sentinels. Equality of two such sentinels is
not proof of real M5 data. The formal comparison manifest must freeze actual
M5 membership/order identities, along with the required real data and margins.
These remain preregistration/input requirements, not outputs of this review.

Receipt-disabled pilot paths still execute no payload hashing/rows/receipt
checks, retain their historical checkpoint/science keys and guard behavior,
and keep the pilot plan identity. The pilot draft and all dependency files are
byte-identical in Git to the certified parent. C0 barrier, recovery/retention
at the 1M/4M crossings, endpoint fail-stop and queue classification are covered
by the authored focused regressions and real frozen-worker tests. No actual
32M pilot was run; synthetic threshold fixtures are not a pilot substitute.

### 14.8 Verification ledger and closeout

| Requirement | Status | Evidence / limit |
|---|---|---|
| Six blockers, stock scope | VERIFIED | Existing hardening/readiness tests plus independent A1-A8; A1 is an observed trust boundary |
| Three new source repairs | IMPLEMENTED / VERIFIED | 20 independent cases and 8 permanent regressions |
| Affected trainer/checkpoint/producer/evaluator/pilot | VERIFIED | 278 passed, exit 0 |
| Hardening/readiness | VERIFIED after focused repair | 211 passed + 2 diagnostic failures initially; 5 related repair cases passed; no blanket rerun claim |
| M4/M5/comparison | VERIFIED except recorded environment failure | 305 passed, one P17 CRLF raw-byte failure, exit 1 |
| Serial queue and M3 recovery | VERIFIED | 4 passed, 66 deselected, exit 0, 341.94 s |
| Science direct/queue/resume workflows | VERIFIED | 3 passed, exit 0, 577.58 s; both attention policies plus M2 cadence |
| CUDA route/resume equivalence | VERIFIED | Four processes, exact chains, final partial update |
| 50M receipt cost | VERIFIED diagnostic | Caps respected; one update per arm, no statistical performance claim |
| Ruff / format / scoped mypy | VERIFIED | Final exits 0; 17 formatted files, 6 typed source modules |
| Dependency / pilot draft identity | VERIFIED | Parent blob and normalized worktree hashes in bounds JSON |
| Whole repository acceptance / long-run campaign performance | NOT RUN | Requested focused selection only; streaming guard is O(history) |
| Arbitrary mutating Python extensions | OUT OF SCOPE | A1 survives; separate review/enforcement required for those extensions |
| Actual formal study launch | BLOCKED pending prerequisites | Real frozen M5 data/order, margins, manifest, resource plan, authorization |
| Network / install / live data / pilot / formal study / push / merge | OUT OF SCOPE | None performed |

P17 classification is supported by actual parent evidence: the Git blob for
`comparison/promotion.py` has SHA-256
`04e961acb3dfe643742fa0777b7e9a98838139d8d34dbd08dda7689713aa8842` at both parent
and starting HEAD; the CRLF worktree hash is
`31c22cce117e2cac30cfccba75b88c752aad99337f3b290fbed72a0a759645b4`. All five
protected files normalize exactly to their unchanged parent blobs. The failing
golden assertion was not weakened or excluded.

The next task is planning only: **"Prepare the formal P35 B8/B16/B32 comparison
on this reviewed branch. Freeze real M5 membership/order, stock code and all
matched contracts. Obtain and record the user's practical/NI margins; use the
recorded 50M diagnostic for the resource plan and budget the entire science
metadata file. Produce the comparison manifest and frozen plan for user
authorization. Do not launch."** No margins were chosen by this review.

Closeout: the final requested science workflow group passed all three cases
(exit 0); every executed CUDA case ran, with zero skips in the recorded
selections. The source/regression repair is local commit
`8e5f6c57520337531c7d2c1c581b03cfdb72b4c9`. The following documentation commit
contains this independent section, the active guide/status changes and bounded
evidence. PowerShell logs were decoded from UTF-16 to UTF-8 for review;
terminal trailing whitespace was trimmed from logs/JUnit after the staged
whitespace check flagged it. Failed first-run diagnostics remain present. At closeout the evidence
directory held 42 files / 249,625 bytes before this final ledger text. The
earlier scratch inventory was 73,983,984 bytes, not a peak-disk measurement.
`git diff --check` passed and dependency/pilot files have no diff from starting
HEAD. No push, merge, tag, data upload or weight publication occurred.
