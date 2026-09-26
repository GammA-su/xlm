# Implementation status

> **P35 Milestone 5 (2026-09-26): IMPLEMENTED; VERIFIED at the data/identity/
> evidence level; NEEDS LOCAL CERTIFICATION.** Branch
> `research/p35-m5-document-order` from certified M4 `221c4e0`. Linux cloud
> container, Python 3.12.3, base+dev `uv sync --locked` scratch venv (authorized;
> no torch, no extras, no data). No acquisition, data preparation, real order
> manifest, training, pilot, campaign or merge.
>
> - **Membership** `xlm-canonical-train-membership-v1`: order-independent,
>   train-only, content/lineage/source/split-bound identity computed from the
>   token shards.
> - **Order manifests** `m5-independent-document-order-v1`: seeded SHA-256-keyed
>   within-source permutation, header-digest id, re-derivation on verify;
>   non-independent pairs (same sequence, same seed, other membership) refused.
> - **Stream/producer**: whole-document ordered index over the unchanged payload
>   (no token duplication; ≈ id length + 6 bytes/document/order); quotas and
>   scheduler unchanged; the real P34 producer child serves the same order.
> - **Identity/resume**: pinned `data.document_order` in the envelope and data
>   identity; `document_order` in committed data state; cross-order resume and
>   forks refused before any state restore.
> - **M4**: evidence v2 reads the real order/membership ids from receipts
>   (pre-M5 keeps the sentinel); tracks v2 add MUST_MATCH `canonical_membership_id`;
>   the robustness slot verifies the declaration (C0/C2/C4 → A, C1/C3 → B) and run
>   binding. Statistics, margins and promotion rules unchanged. New order-evidence
>   bundle with unique initialization counts per order.
> - **Pilot**: the 32M draft requires a pinned order (fixed order, not an
>   independent replicate); planning blocks unbound/unpinned/foreign orders.
> - **Tests**: see the report's §13 table — 10/10 mutants killed; ruff/format
>   clean; mypy clean apart from 2 pre-existing torch-absent lines identical on
>   `221c4e0`. One serial error (`test_reports` torch check) is identical on
>   `221c4e0`.
> - **NOT RUN (torch/CUDA)**: Trainer resume A→A, `load_checkpoint` A→B refusal,
>   trainer-path producer, full pilot planning, updated M3 pilot assertions,
>   legacy torch regressions, frozen workflow.
>
> See [P35-M5](reports/P35-M5.md) and [science-v1](../science-v1.md). Next: run
> the P35-M5 §15 local certification commands, then the §18 prompt.

> **P35 Milestone 4 LOCAL CUDA CERTIFICATION CLOSEOUT (2026-09-26): M4 CERTIFIED
> — SAFE TO INTEGRATE** as an opt-in. Worktree `G:\Project\xlm-p35-m4-local`,
> branch `review/p35-m4-local`, starting HEAD `9e3239f`, clean, certified parent
> `9f57869` verified as ancestor. Windows 10 Pro (26200), Python 3.12.13, torch
> 2.14.0+cu126, RTX 4090 (driver 596.49), NumPy 2.5.3, lm-eval 0.4.13, no XLM
> CUDA process, sequential GPU work. No network/install/sync/live-data/pilot/
> campaign/push/merge; no M5; no product-code change; dependency files unchanged.
>
> - **M4 focused suite:** 179 passed, 1 failed (of 180). The failure is the P17
>   golden-SHA test on CRLF checkout bytes; all five git blobs match the golden
>   and all five working-tree files equal certified M3 — a proven pre-existing
>   Windows environment artifact, not an M4 defect.
> - **Legacy `test_comparison.py`:** 47 passed (cloud NOT RUN closed).
> - **Real frozen evidence:** no retained M3 toy bytes remained, so one bounded
>   authored-toy frozen flow was created with existing utilities (direct 4096 +
>   frozen 2048 targets, toy BPE/model, generated text only). Endpoint
>   `run_b66d98f3ccb11927_ckpt-t2048-a001` extracts via the real ArtifactStore:
>   `xlm-science-run-evidence-v1`, digest `ebc95509…`, science-v1, 6768 params
>   (real torch path), seeds 101/10001/20260918, order sentinel
>   `shard_native_no_order_manifest`, batch 256/microbatch 2, LR endpoint,
>   RTX 4090/torch 2.14.0+cu126, all evaluations COMPLETE. Cross-checks all OK;
>   repeat extraction identical; tampered copy refused. Single-real-run screen is
>   INCOMPLETE with no CI/winner.
> - **Legacy:** P17 blobs unchanged (5/5), 4 legacy-evidence nodes passed,
>   `test_comparison.py` 47 passed. **Spot checks:** 9/9 (A–E) passed.
> - **Static:** ruff check clean, format 17/17 clean, scoped mypy 10 files clean
>   (unscoped 7 errors are pre-existing in untouched files); dependency diff empty.
> - **Qualifications:** microbatch per-update mask/position digest absent (harden
>   before B8/B16/B32); throughput/VRAM operator-declared; driver/cuDNN not
>   separately recorded (same-machine pairing unblocked); margins intentionally
>   null (must be chosen before confirmation, never invented).
>
> See [P35-M4 §25](reports/P35-M4.md#25-local-cuda-certification-closeout-2026-09-26-windowsrtx-4090).
> Next: integrate M4, then the M5 prompt in P35-M4 §24. M5 may begin after
> integration. DO NOT START M5 here.

> **P35 Milestone 4 (2026-09-26): IMPLEMENTED and VERIFIED within the
> authored/synthetic scope; SAFE TO INTEGRATE as an opt-in.** Branch
> `research/p35-m4-comparisons` from certified M3 `9f57869`. No training,
> evaluation campaign, pilot, acquisition or data download.
>
> - **Environment.** The only network use was the user-authorized
>   `uv sync --locked` of base + dev wheels into a scratch venv (no torch or
>   extras). This is a Linux cloud container on Python 3.12.3, which differs
>   from the primary 3.12.13; it makes no CUDA claims.
> - **Manifest.** Versioned `xlm-science-comparison-v1` with 36 required keys,
>   field-level problems and `identity_digest` hash. Margins, family size and
>   roster are frozen inputs.
> - **Tracks and eligibility.** Three-way field classification; certified
>   `microbatch_grouping_v1` and `data_mixture_v1`. Any undeclared or unknown
>   difference is INELIGIBLE with a field diff and no effect estimate.
>   Cross-tokenizer CE is refused.
> - **Evidence.** Extracted from verified frozen science-v1 checkpoints (M1–M3
>   receipts, M2 `first_complete_attempt_v1`). Pairing uses explicit replicate
>   identity; same-seed reruns are repeats, not replicates.
> - **Statistics.** Paired Student-t (stdlib, verified against closed forms and
>   tables), Bonferroni from the manifest, oriented improvement plus raw deltas.
>   One pair gives no CI.
> - **Decisions.** `xlm-p35-decision-v1`: win/loss/ambiguous/NI against frozen
>   margins; nonsignificant ≠ NI.
> - **Promotion.** `xlm-p35-promotion-v1`: 5/3/3 fresh pairs; first 3 of 5 →
>   PROVISIONAL. Order robustness stays BLOCKED until M5 evidence exists.
> - **Reports and CLI.** §U JSON/Markdown/CSV; `xlm experiment compare` /
>   `xlm experiment report`.
> - **Tests.** 180 M4 tests pass (serial and `-n 4`).
>   - The 11 high-risk mutants are all KILLED.
>   - ruff, format and source mypy are clean.
>   - Related legacy tests: 31 passed. The one error is pre-existing (torch
>     missing), identical on `9f57869`.
> - **NOT RUN (torch absent):** `tests/test_comparison.py` (legacy P17, code
>   byte-identical) and evidence extraction from a real trainer checkpoint.
>
> See [P35-M4](reports/P35-M4.md), [science-v1](../science-v1.md#scientific-comparisons-p35-milestone-4)
> and [mutations.json](evidence/P35-M4/mutations.json). Next: integrate M4, run
> the two NOT RUN items on the CUDA environment, then the M5 prompt in P35-M4 §24.

> **P35 Milestone 3 final certification closeout (2026-09-26): M3 CERTIFIED —
> SAFE TO INTEGRATE** (opt-in, authored/synthetic scope). **M4 may begin.** The
> real 32M pilot stays **BLOCKED** on 17 real inputs and the user's
> authorization. No product code changed; two test-only commits (`e2541ec`,
> `bbffc16`) strengthen evidence.
>
> - **M1/M2 frozen workflow:** 3/3 passed on `f548d60` (556.8 s): direct,
>   queue and resume; LR, RNG and runtime identity; M2 cadence.
> - **Queue/frozen:** 35 selected, 10 passed, 5 failed, 20 errors.
>   - All 25 non-green nodes raise `installed torch accelerator does not match
>     selected extras` (CPU-extra frozen identity).
>   - They are identical on a clean certified-M2 `a3e5546` export: a
>     pre-existing environment limit, not M3.
>   - The CUDA-relevant M3 queue paths are exercised by the workflow, the
>     wall-allowance tests and toy phase B.
> - **Nine adversarial mutations, all KILLED** in isolated exports, each on its
>   intended assertion, after a green control:
>   1. resume cadence drift;
>   2. retirement before replacement authority;
>   3. deletion of the last good state;
>   4. ignored evaluation dependency;
>   5. rescore with same count/step but different weights;
>   6. fallback to the latest checkpoint;
>   7. peak-disk underestimate;
>   8. wall allowance reset on resume;
>   9. blocked or unauthorized pilot made EXECUTABLE.
>
>   One test weakness was found and fixed: the retention-order observer's
>   exception was swallowed by `apply_retention`.
> - **Final M3 group** (cadence, retention, rescore, pilot, wall): 73/73
>   passed. Ruff and format are clean; the mypy profile is unchanged.
> - **Toy flow** was not rerun. The post-run edit is proven behavior-neutral
>   by AST comparison.
> - **Open for the pilot:**
>   - The 2 GiB aggregate shard-input cap is a POTENTIAL BLOCKER pending real
>     sizes; M3 is not blocked by it.
>   - `quick_lm@1M/4M` have no exact checkpoint, which is a **PILOT POLICY
>     DECISION** for the user.
>   - Search-benchmark events are not checkpoint-rescorable and affect
>     completeness only on failure.
>
> See [P35-M3 §17](reports/P35-M3.md#17-final-certification-closeout-2026-09-26)
> and [certification.json](evidence/P35-M3/certification.json). Next: integrate
> M3, then the M4 prompt in P35-M3 §16.

> **P35 Milestone 3 (2026-09-26), superseded by the closeout above: IMPLEMENTED/VERIFIED within the
> authored/synthetic scope; safe to integrate as an opt-in. The real 32M pilot
> is BLOCKED** until the user supplies real artifacts. NOT RUN: the M1/M2 frozen
> workflow and queue/frozen serial regressions (session ended early); run them before integrating. Branch
> `research/p35-m3-pilot-retention` from certified M2 `a3e5546`.
>
> - **Checkpoint cadence.** Absolute committed-target checkpoint events share
>   the M2 first-crossing planner, with a fixed boundary order: crossing →
>   one checkpoint → evaluations. Pilot 0/8M/16M/32M land at C = 0,
>   8,060,928, 16,056,320 and 32,000,000 (488 full updates + 18,432). Planned
>   and actual counts are recorded. Resume never drifts or republishes, and
>   failed publications are recorded.
> - **Retention.** Bounded latest-two-recovery + pinned retention over
>   verified records. Milestones, references and evaluation-needed states are
>   protected, and a failed publication retires nothing.
> - **Rescoring.** FAILED M2 events are rescored only from the retained
>   checkpoint of exactly their state (digest-verified weights and evaluator,
>   device and runtime identity) as a new immutable attempt.
> - **Pilot planning.** Non-executable `draft_science_v1_pilot_32m`,
>   `xlm experiment plan --bindings` and the new `xlm experiment validate`
>   (DRAFT/BLOCKED/RESOLVED/EXECUTABLE), with measured peak-disk planning.
> - **Wall allowance.** The persisted 3,600 s total allowance gives a resumed
>   attempt only the remainder; expiry → INCOMPLETE.
> - **Toy flow.** One bounded authored toy flow on CUDA: 174.7 s, 13 MB,
>   ≤147,456 targets. It covered a failed eval → exact rescore, a killed
>   runner → resume with 500.7 s of 540 s, and plan → EXECUTABLE → queue.
>
> See [P35-M3](reports/P35-M3.md) and [science-v1](../science-v1.md).

> **P35 Milestone 2 certification closeout (2026-09-26): M2 CERTIFIED — SAFE TO
> INTEGRATE** (opt-in, authored/synthetic scope). No product change; one new test
> (`92e5eba`). A new CUDA frozen workflow runs an authored cadence through CLI
> train, experiment plan/submit + queue run, and CLI resume. Results:
>
> - Thresholds 8/24 fire at the natural boundaries C=16/32, and the endpoint at 33.
> - Update sizes stay 16/16/1 and the M1 LR receipts are unchanged.
> - Checkpoints owe events between crossing and scoring; an owed event is scored
>   after a fresh-store resume, and nothing is rescored at an at-budget resume.
> - Direct and queued receipts are identical, and the training state is bitwise
>   equal to a cadence-free run.
> - One envelope; legacy is refused.
> - Mutants M-A (crossing after checkpoint) and M-C (queue drops the controller)
>   are both killed.
>
> Missing regressions, `-n 0`, 717.5 s: 52 passed, 3 failed, 0 skipped:
>
> - M1 workflow: 2/2.
> - Harness adapter: 14/14.
> - Declared inputs: 36/39.
>
> The 3 failures are `xlm evaluate` CLI nodes that need the CPU-only frozen
> extra. They fail identically on a clean `517a9b8` export and pass with
> `--device cuda`: a pre-existing environment issue, not M2. The 34 cited
> guard/attempt/firewall nodes were rerun green. Real-inventory scorer
> throughput is unmeasured, and FAILED-event rescoring belongs to M3. See
> [P35-M2 §8](reports/P35-M2.md#8-certification-closeout-2026-09-26).
> Next: **P35 Milestone 3 only** (prompt in §8.5).

> **P35 Milestone 2 (2026-09-25): IMPLEMENTED/VERIFIED within the authored/synthetic
> scope; opt-in, safe to integrate once the listed NOT RUN regressions pass.**
> *(Superseded by the certification closeout above.)*
> Branch `research/p35-m2-eval-cadence` from `517a9b8`. Science-v1
> `evaluation.science` adds an absolute committed-target cadence (§K tables
> verbatim) with `quick_lm`, `full_lm`, `search_benchmark` and `endpoint_confirmation`
> events. First crossing is verified: the 1M event fires at C=1,048,576 with
> unchanged 65,536-target updates. Crossings are recorded before the periodic
> checkpoint; every attempt is an immutable artifact; the canonical receipt is the
> first complete attempt; failed, partial or missing events make the run
> evaluation-incomplete and never produce a score. Scoring uses a digest-verified
> replica under a state guard; runs with and without evaluation are bitwise
> identical on CPU and CUDA. Adds text-only CE, UTF-8 BPB, equal-domain
> aggregation, a pinned LM inventory format, and a BLiMP tier-partition firewall
> (a new gap closure). Tests: 89 new focused tests, 363-passed CPU regression
> (1 pre-existing cp932 failure, reproduced on `517a9b8`), 16 CUDA passed, 12/12
> mutants killed, and Ruff/format/mypy clean. NOT RUN: the science workflow test,
> harness regressions, a frozen CLI/queue cadence run, and 50M scorer/guard cost.
> See [P35-M2](reports/P35-M2.md). Next: run the NOT RUN regressions, then M3.

> **P35 Milestone 1 (2026-09-25): COMPLETE within the authored/synthetic scope;
> safe to integrate as an opt-in.** Branch `research/p35-m1-lr-rng-identity`
> from `991dd39`, three product/test commits plus docs. `xlm-science-v1`
> (explicit `science_version`, `lr_policy: target_endpoint_before_update_v1`,
> `training_seed`, `runtime` block) is opt-in. Schema-v1 configs and checkpoints
> resolve to the legacy policy. The historical config digest and a
> checkpoint produced by the unmodified code continue bit-identically.
> `optimizer.step` observed **0.0000065536 then 0.0000131072** at the 50M
> reference (legacy: 0.001 then 0.0000065536) on CPU and CUDA. Actual partial N
> is honored, and zero-valid work never steps. LR receipts are published only
> after the data commit, and every failure stage, including the CUDA barrier,
> leaves none. Training RNG is reseeded after construction and resume restores
> without reseeding. Attention/TF32/BF16-reduction policy is scoped per update
> and bound into the envelope. Direct, queue and resume share one identity on
> CUDA (strict mode also bitwise). Final gate from a clean export of `9f4c392`:
> **221 CPU + 17 CUDA + 5 serial passed**, plus 2 capability skips. There is
> one pre-existing cp932 README failure, and the CPU frozen legs are BLOCKED;
> both reproduce on the baseline. Ruff, format and mypy pass on 16 files.
> Strict-mode full-model cost, science-path throughput and CPU frozen
> workflows are NOT RUN. No real data, pilot, campaign, network, install, push
> or merge. See [P35-M1](reports/P35-M1.md) and [science-v1](../science-v1.md).
> Next prompt: **Implement P35 handoff Milestone 2 only (target-threshold
> evaluation cadence and state-preserving scoring) on top of M1; keep legacy
> behavior, use focused offline tests, and do not launch real-data training.**

> **P35 scientific contract (2026-09-25): IMPLEMENTED design / VERIFIED bounded
> audit**, on `research/p35-scientific-contract`, engineering base `febbf8b`.
> [Scientific contract](reports/P35-SCIENTIFIC-CONTRACT.md) covers A–Z;
> [Opus handoff](handoffs/P35-OPUS-HANDOFF.md) specifies five sequential steps.
> Decisions: controlled statistical reproducibility; paired B8/B16/B32 quality–cost
> study; versioned positive first-update warmup; one-pair screens, five-pair robust
> 50M confirmation, three-pair larger-scale confirmation; held-out CE primary;
> user-run 32M pilot. Code audit found the legacy first-step LR spike/next-LR log,
> within-shard order not shuffled by data_seed, unwired evaluation cadence, and
> old comparison statistics insufficient for the new seed-level decision rule.
> No product defaults/code/recipes/lockfile changed. Synthetic 14.891-s attention
> probe: strict deterministic efficient SDPA available at all three sizes, sampled
> forwards identical, all strict repeated backwards identical; busy-desktop
> operator timing is not a full-model speed certificate. Parameter counts verified;
> seven existing schedule tests passed. See [P35 record](reports/P35.md) for exact
> checks and limits. Real inputs/pilot and research studies NOT RUN; execution
> awaits handoff implementation and actual artifact/hash-bound user authorization.
> No network, install, acquisition, campaign, push or merge. Next prompt:
> **Implement P35 handoff milestone 1 only; preserve legacy LR/RNG behavior,
> run focused offline tests, and do not launch real-data training.**

> **P34 final adversarial review (2026-09-25): SAFE TO INTEGRATE the repaired
> series on `review/p34-astra-final`, within the frozen synthetic scope.** Starting
> `745e66e` was clean but had blockers: synchronous CUDA bypassed the completion
> barrier, the bound checkpoint manager could publish in-doubt state, pipe waits
> escaped deadlines, and four consumed-envelope claims were unchecked. Repairs
> retain the Opus exact trace fold and compact spawned producer; add bounded
> lifetime reading, frame admission and failure cleanup; and protect all CUDA
> optimizer/checkpoint boundaries. Final adversarial selection: 53 passed;
> earlier related CPU selection: 103 passed (overlapping counts). CUDA: three
> barrier/exactness nodes and public direct/queue/resume workflow passed. Ruff,
> format and mypy pass on all 10 changed Python files. Production-config B8:
> **45,679 targets/s**, resident **46,654**, ratio **97.91%**; synchronous **36,247**.
> Content verification remains on (3.06 ms/update). Eight lifecycle iterations
> leave zero children/threads and constant handle count. CPU-only frozen release
> legs remain NOT RUN because the installed CUDA wheel fails their unchanged
> environment check. No network, installations, live data, campaign, push or
> merge. See [P34 final review](reports/P34.md), which supersedes the earlier
> candidate verdict below. No further P34 engineering prompt is needed. Next:
> `git log --reverse --oneline 745e66e..HEAD` in this review worktree.

> **P34 final integration candidate (2026-09-25): COMPLETE within the synthetic
> diagnostic scope; branch `integrate/p34-final-candidate`.** Opus series
> applied cleanly onto the P33 base with proven tree identity, then five
> minimal Astra safety ports: fail-closed CUDA commit boundary with in-doubt
> optimizer state, transport domain normalization, duplex-pipe reset-deadlock
> repair (found deterministically during porting), end-state binding checks,
> and the explicit `training.producer_prefetch` run option (default off,
> frozen in the envelope, honored by direct/queue/resume). Actual-50M B8
> release-LR updates are bit exact with the barrier active; the producer sits
> at the resident ceiling (r3 100.1%, r4 101.3%; 3.6–3.9 ms consumer wait,
> zero blocked takes). CPU: prefetch 18 + training 12 + trace/config 19 +
> trainer suites 25 passed; CUDA: exactness + barrier + producer workflow
> passed. Ruff/format/mypy clean on changed modules. CPU workflow params and
> queue/frozen serial selections NOT RUN (no locked CPU env; pristine tree
> fails identically). See [P34-FINAL](reports/P34-FINAL.md). Next: review §26,
> then integrate.

> **P34 independent training-throughput challenger (Opus, 2026-09-25): COMPLETE
> within the synthetic diagnostic scope; branch `perf/opus55-p34-independent`.**
> 50M B8 end-to-end rises from 32,189 to **50,588 targets/s** (medians of three
> uncontended runs, +57.2%), 99.6% of the same-session resident ceiling (50,775),
> with every loader mode ending in one identical parameter digest. Two changes:
> an exact, always-on window-level fold of the C07 trace chain (loader
> 779 -> 390 ms/update; synchronous 39,530 targets/s, +22.8%), and an opt-in
> spawned `PrefetchingBatcher` that runs the unchanged batcher one update ahead,
> ships compact arrays and keeps committed state in the trainer (zero blocked
> updates; 3.0-3.5 ms consumer cost). Speculation is bound by start/end state
> digests and generations; producer, consumer, commit and resume failures all
> regenerate the exact sequence. Actual-50M CUDA updates are bit exact. Checkpoint
> tails (3.0 s typical, 14-25 s back-to-back) are SLC-cache exhaustion on the
> DRAM-less G: SSD, not serialization; async publication is not implemented.
> Memory-efficient SDPA backward is nondeterministic on the certified path
> (pre-existing). Gates: **201 CPU + 20 CUDA passed**, no skips; Ruff/format/mypy
> pass. Full six-leg gate, live data and research training not run. See
> [P34-OPUS](reports/P34-OPUS.md) and [P34-PREFETCH](P34-PREFETCH.md). Next:
> `git log --reverse --oneline 8fd05c11e1bdfd84e075000d59e14c315c986f36..HEAD`
> in `G:\Project\xlm-opus55-p34`, then cross-review against Astra's P34.

> **P33 bounded CUDA performance closeout (2026-09-24): COMPLETE within the
> synthetic diagnostic scope.** On RTX 4090 / torch 2.14.0+cu126, the retained
> product change consolidates CUDA gradient finite/norm transfers with exact
> clipping arithmetic and failure semantics. Three actual release-LR updates
> at the full 65,536-target budget match the frozen reference bit for bit;
> an actual-50M CUDA test also compares every clipped gradient digest and weight.
> Final focused gates: **64 CPU + 19 CUDA passed**, no skips; Ruff, format and
> mypy pass after one formatting-only correction. B8 end-to-end is 32,152 versus
> 32,044 targets/s (+0.335%, too small for a robust campaign claim); the smaller
> update diagnostic improves 3.63%, and resident B8 reaches 51,606 targets/s.
> B32 reaches 48,834 targets/s but **fails** the fixed release-LR parameter gate,
> as do B16 and native RMSNorm; these are not certified replacements. Original
> RMSNorm, microbatch/scientific defaults and durability remain unchanged.
> Bounded 150M B16 / 300M B8 smokes reach 29,826 / 18,714 targets/s. Shared
> desktop and a 14.5-GiB allocator cap qualify capacity results. Final 50M is
> **DATA-LIMITED with CPU launch overhead**; recommend P34 bounded producer-process
> overlap and checkpoint-tail design (observed 50M pause 3.6–62.4 s). No network,
> installation, live data, evaluation, research campaign, push or merge. No full
> CPU six-leg gate was run for this scoped CUDA task. See [P33](reports/P33.md)
> for negative gates, exact commands, raw evidence and planning limits. Next:
> `git log --reverse --oneline 03e6c4278a8a64301a1c416e37bda7623907e361..HEAD`
> in the P33 worktree, then review the P34 cursor/snapshot contract before coding.

> **P32 final performance closeout (2026-09-24): CORRECTNESS-COMPLETE within
> the declared offline scope; final six-leg gate PASSED.** Product `ce2bb32`
> carries closed-writer SHA/size/file-ID evidence into immediate publication,
> removing two redundant whole-payload reads while retaining full recovery
> verification, durable intent, atomic settlement, all fsyncs and journal/control
> authority checks. Exact acquisitions: 8/8; crash/restart matrices: 44/44 early,
> 44/44 mature, 20/20 selected, 4/4 parallel. Final gate ran once: **1,800 passed /
> two capability skips / zero failed**, preserving all 1,789 prior nodes and adding
> 13 publication regressions. Repeated G: median throughput improves 8.9% / 10.1% /
> 12.7% at 1/8/16 workers, but retained fsync stalls make the aggregate after-series
> slower; this is a CPU/happy-path improvement, not uniform wall-time recovery.
> The frozen 100k pipeline passes the exact comparator (126.885 s versus 109.834 s;
> unchanged downstream timing is not attributed to this change). No external
> network, installs, research training, push or merge. See
> [P32-PERF-CLOSEOUT](reports/P32-PERF-CLOSEOUT.md) for all observations, safety
> proof, limitations and final integration series. Next read-only command:
> `git show --stat ce2bb32`.

> **P32 heavy-worker crash closeout (2026-09-24): CORRECTNESS-COMPLETE
> within the declared offline scope; final gate PASSED.** Based on recovery
> candidate `fa4ff50`, repair `95ee6b3` replaces the unsafe native pytest timeout
> frame walker on pinned Windows CPython 3.12.13 with an owned, joined Python
> diagnostic thread. Native dump/OS events identify an invalid code-pointer
> read in `PyCode_Addr2Line`; a stdlib-only probe crashes 3/3 times without XLM,
> whereas control and replacement probes each pass 3/3. No acquisition,
> runtime-inventory, dependency or scientific behavior changes. Focused: 15
> passes plus 7 final diagnostic passes; original group stress 5/5; standalone
> heavy 7/7. One final six-leg gate: **1,787 passed / 2 capability skips /
> 0 failed**, 1,789 distinct selected nodes; all legs exit 0. Ruff/format/mypy
> pass. Missing compiler and symlink privilege remain uncertified. The corrected
> P32 candidate is eligible for release integration within this scope; existing
> acquisition throughput cost is unchanged. No external network, installs,
> research training, push or merge. See
> [P32-HEAVY-CRASH](reports/P32-HEAVY-CRASH.md) for native evidence and exact
> commands. Next: `git show --stat 95ee6b3`, then review the certification
> evidence before any separately authorized integration. Earlier entries are
> historical; the original failed gate remains preserved.

> **P32 recovery closeout (2026-09-24): IMPLEMENTED / VERIFIED (scoped);
> full acceptance FAILED, P32 completion and release integration BLOCKED.**
> On `fix/p32-recovery-closeout`, based on corrected candidate `26c1238`:
> durable publication intent reconciles whole and selected outputs exactly;
> owned journal/diagnostic replacements are retired under FileLock and their
> bytes are included in scratch admission, including the first journal write.
> Final crash matrices: 44/44 early, 44/44 mature, 20/20 selected, plus 4/4
> parallel selected publications; all eight successful acquisitions remain
> byte/accounting exact. Focused: 190 passed / one skip; after initialization
> correction, 85 passed / one skip. Final Tier A: 1,632 passed / two capability
> skips; core 68 passes; exclusive 8 passes. Heavy worker died with a Windows
> access violation during runtime-inventory fixture teardown after its test
> body passed; that is a gate failure, not a pass. Scale: 8 passes; optional:
> 57 passes. Six-leg total: **1,779 passed / 2 skipped / 1 failed**, covering
> 1,782 distinct nodes. All raw evidence is in
> [P32-RECOVERY](reports/P32-RECOVERY.md).
> G: 1/8/16-worker throughput changed from 143.581/227.321/226.137 to
> 91.263/125.916/29.326 MB/s; the final fsync outlier is retained. No further
> optimization, installs, external network, push or merge. Next: diagnose the
> preserved native worker crash using a focused reproducer before a new gate;
> keep the recovery fixes and explicit throughput tradeoff. Earlier entries
> below are historical.

> **Independent Opus 5.5 review (2026-09-24): CORRECTED / PARTIALLY VERIFIED;
> P32 completion BLOCKED.** Reviewed all six requested commits against green
> `9765a00`. Four separate corrections remove stale journal caching, reserve
> skipped-row scans before parsing, guard locator splicing against mutation,
> and establish durable empty-prefix ownership. Corrected product `f981464`:
> 227 focused passes; Tier A 1,600 passes / one missing-compiler skip; 36 final
> independent regressions pass. Whole-file crash matrices preserve accounting
> and prefixes but recover only 40/44 cases; selected matrix recovers 16/20.
> Publication deaths fail closed with valid files still present. Orphan atomic
> journal files also leave a gap in the complete scratch-cap claim. G: maximum
> observed durable acquisition 234.212 MB/s on larger files; selected Parquet
> 8.42–9.06 MB/s; frozen 100k pipeline 109.834 s with exact comparator success.
> Use corrected leases as the P32 implementation candidate, not as completed
> recovery work. Full six-leg acceptance NOT RUN because review did not pass.
> No network beyond authored localhost, installs, push or merge. See
> [OPUS55-REVIEW](reports/OPUS55-REVIEW.md) for verdicts, commits, evidence and
> the narrow next prompt. Earlier status entries below are historical.

> **Artifact↔ledger crash reconciliation: IMPLEMENTED / VERIFIED (bounded
> offline)** on `fix/artifact-ledger-reconciliation`. Strengthened
> `rebuild_from_filesystem` (full-verifier authority, idempotent no-op
> duplicates, incomplete/conflict reporting, count/byte/deadline bounds
> with honest truncation, phase timing) plus new `audit_ledger_references`
> (ok/healed/unusable verdicts, `unverifiable` marking, rows never
> deleted) with atomic check-and-write concurrency. Recovery caller:
> `artifact rebuild-ledger` with bounds + audit flags (no automatic
> full-store scans). Crash matrix A–H, concurrent convergence, real
> checkpoint crash-window recovery, and 1/100/1000 timings (0.03 s /
> 3.31 s / 27.84 s) all green: 18 new + 115 related tests passed;
> ruff/mypy clean. Sync substrate (durable publish + reconcile +
> idempotent record + restart discovery) now satisfies async-checkpoint
> prerequisites. See [reports/ARTIFACT-LEDGER-RECONCILE.md](reports/ARTIFACT-LEDGER-RECONCILE.md).
> Overall production acceptance remains BLOCKED; main is unchanged.

> **Checkpoint/artifact durability (synchronous baseline): IMPLEMENTED /
> VERIFIED (bounded offline)** on `fix/checkpoint-durability`. `publish_artifact`
> now orders flush+fsync per payload, staging/subdir syncs, durable manifest,
> durable `_COMPLETED` last, rename, parent sync — zero fsyncs before.
> Durability facts ride identity-neutral in `cosmetic_metadata`; Windows
> directory sync honestly reported unsupported (file fsync + NTFS rename +
> fail-closed verification instead). 15 new failure-injection/observability
> tests + 101 related tests passed; ruff/mypy clean. Cost ~5–6 ms per fsync
> on this box. See [reports/CHECKPOINT-DURABILITY.md](reports/CHECKPOINT-DURABILITY.md).
> Overall production acceptance remains BLOCKED; main is unchanged.

> **Performance integration 2026-09-23 (P28 + Astra P29/P29B/P29C):
> INTEGRATED / GATE-TESTED (bounded offline)** on
> `integrate/performance-20260923`. One P28 + 13 Astra + 7 P29C commits
> cherry-picked in order with zero conflicts; reconciliation adds only
> lint normalization, `--workers 6` + opt-in `--dedup-workers` harness
> support, and docs. Combined pipeline (adapt → shard → clean → P28
> dedup → split → tokenize → pack → loader): 10k in 28.7–53.6 s,
> 100k in 147.6–258.7 s depending on workers, every stage digest
> worker-invariant at both scales. Full gate: 1626 passed / 19 failed /
> 1 skipped (fast) + 9 passed (serial); 16 failures are xdist isolation
> flakes, 3 are pre-existing Astra-branch failures (proven identical
> without this integration). NumPy absent from the locked runtime (exact
> Python fallback; §12 operator action documented). Semantic lane stays
> opt-in and off by default. See [reports/PINTEGRATION.md](reports/PINTEGRATION.md).
> Overall production acceptance remains BLOCKED; main is unchanged.

> **P30B serial/final throughput (2026-09-24): IMPLEMENTED / VERIFIED (scoped);
> final acceptance BLOCKED.** On `perf/test-suite-throughput`, starting
> `343537d8dfd6b30bc6f724436feb1f7b14993163`: all 1,673 prior nodes retained,
> eight added core nodes, 1,681 collected. Original B: 66 pass / two fail,
> 2,454.37 s. Core now passes 69 nodes in 419.93 s (grouped + exclusive);
> installed optional passes all 57 in 190.44 s versus 523.03 s (63.6% less time).
> Test-only immutable queue identity reuse preserves fresh worker/CLI inventories.
> Audited four-worker domain grouping retains private mutable state; three risky
> repetitions passed 11/11 at 298.66 / 296.51 / 297.39 s. All 68 original B bodies
> and assertions are unchanged. Core + heavy still cover every original B node.
> Final six legs: 1,655 pass / four fail / one compiler skip, no missing required
> nodes, 1,172.83 s (19m32.83s). B totals 801.41 s, but the queue campaign stopped
> early on a Windows checkpoint rename PermissionError: **no validated full-final
> speedup or passing heavy gate is claimed**. A also encountered a Windows journal
> replacement PermissionError; two original token-preparation failures remain.
> B peak sampled RSS 1.035 -> 2.871 GiB; sampled CPU lower bound
> 2,322.266 -> 3,099.984 s (includes eight new nodes; failed-work caveat applies).
> A remains twelve workers; scale passes eight / 112.26 s. No product/durability
> code, original durability tests, dependency graph or P28/P29 algorithms changed.
> No network, installation, research campaign, push or merge. CUDA/live, hosted
> grouping and performance matrices NOT RUN. See [P30B report](reports/P30B.md),
> [evidence](evidence/P30B/README.md) and [direct commands](../TESTING.md).
> Next: investigate the journal and checkpoint rename failures (coordinate the
> checkpoint case with Muse), repair token preparation reuse/resume, run exact
> failing nodes with `-n 0`, then the six final legs on the integrated tree.

> **P30 test-suite iteration (2026-09-23): IMPLEMENTED / VERIFIED (scoped);
> final acceptance BLOCKED.** On `perf/test-suite-throughput`, starting
> `6bdca915005e2863e1c80b962dff6a053ab5b680`: all 1,667 original tests retained,
> six additions, 1,673-node tier ledger. Fast A (1,519 nodes) takes 81.71 s with
> 12 workers: 1,518 pass, one existing CPU compiler skip. Measured 4/8/12/16:
> 113.75 / 102.88 / 81.71 / 95.38 s. Immutable private-copy fixtures and lazy
> optional imports reduce repeated work; real worker verification remains intact.
> Original scale: eight passes / 111.62 s; installed optional: 57 / 535.97 s.
> Serial: 65 passes, three failures / 2,253.40 s; its Windows crash-termination
> race was repaired and the exact case passed in 89.83 s. Two unchanged token
> preparation/reuse publication failures remain. Latest required-node evidence:
> 1,649 pass, two fail, one skip; no missing required nodes. The four measured
> correctness legs total 49m42.70s: **no full-acceptance speedup is established**.
> Four bounded performance matrices passed / 139.93 s; full performance,
> CUDA/live/environment installation and hosted CI NOT RUN. No production code or
> dependency graph changed; no research campaign, network, installs, push or merge.
> See [P30 report](reports/P30.md), [node ledger](evidence/P30/test-ledger.tsv),
> [raw evidence](evidence/P30/README.md), and [direct commands](../TESTING.md).
> Next: repair the two `tests/test_prepare.py` regressions listed in the report,
> run those exact nodes with `-n 0`, then the four-leg final gate on the merged tree.

> **P29C exact cleaning (2026-09-23): IMPLEMENTED / VERIFIED (bounded offline)**
> on `perf/astra-cleaning-v2`, parent `5a43a81`. Authored one-worker cleaner:
> 10k 7.965 -> 5.918 s; 100k 70.399 -> 53.241 s (24.4% less time).
> Full frozen pipeline, unchanged worker settings: 160.558 -> 144.719 s.
> Explicit eight-worker cleaning: 111.688 s versus historical P29B 158.430 s;
> combines exact loop improvements with existing parallelism. Cleaner w6/w8:
> 21.271/20.256 s at 1,542.5/1,982.1 MiB tree RSS. Static default retained;
> bounded dynamic dispatch is opt-in for uneven work. Decisions, metrics,
> ordered payloads and token artifacts match; real code fingerprints change.
> Focused tests, scoped Ruff/mypy and full artifact comparisons passed.
> Token preparation is now dominant; educational-keyword scanning remains a
> cleaner hotspot hidden by the existing incomplete length timer. No P28 edits,
> network, installs, research training, push or merge. Full acceptance, 250k,
> live compatibility and CUDA NOT RUN; production acceptance remains BLOCKED.
> See [reports/P29C.md](reports/P29C.md), [measured tables](evidence/P29C/RESULTS.md)
> and [operator commands](../PERFORMANCE.md).

> **P29B token-path performance (2026-09-23): IMPLEMENTED / VERIFIED (bounded offline)**
> on `perf/astra-global-throughput`, parent `44c19b8`. Frozen 32,768-entry authored
> tokenizer; matched 100k pipeline 192.142 -> 158.430 s (17.5% less time), token
> stage 83.222 -> 51.950 s including assembly. Peak process-tree RSS rises from
> 369 MiB to 2.09 GiB with eight workers. Matched 10k is flat (20.250 -> 20.328 s).
> Adds bounded shard workers/native batches, exact packing/loader improvements,
> optional verified mmap cache, streaming fit preparation and fsynced manifest-last
> token publication. Focused offline tests and scoped Ruff/mypy passed. Cleaning
> is now the largest measured stage. No P28 edits, network, installs or training.
> Full acceptance, live compatibility and CUDA measurements NOT RUN; Windows
> directory/checkpoint power-loss durability remains unproven. Production
> acceptance remains BLOCKED. See [reports/P29B.md](reports/P29B.md), its exactness
> evidence and [operator commands](../PERFORMANCE.md).

> **P29 global throughput (2026-09-23): IMPLEMENTED / VERIFIED (bounded offline)**
> on `perf/astra-global-throughput`, parent `2a82dfd`. Exact ASCII counting,
> BPE byte-length reuse / IDs-only encoding, bounded token writes, and streaming
> split metadata. Authored 100k local pipeline: 228.795 → 173.354 s (24.2% less
> time); split RSS 718 → 294 MiB. Canonical/token artifacts and scientific metrics
> compare exactly. 229 focused regressions + 2 serial checks passed; scoped
> Ruff/mypy passed. No P28 implementation edits, network, installs or training.
> Production speedup, GPU and official evaluation NOT RUN; full suite NOT RUN.
> Audit also records the pre-existing checkpoint power-loss durability gap.
> See [reports/P29.md](reports/P29.md) and [performance commands](../PERFORMANCE.md).
> Production acceptance remains BLOCKED.

> **P28 exact/lexical dedup + FAISS semantic lane (2026-09-23): IMPLEMENTED /
> VERIFIED (bounded offline)** on `perf/p28-dedup-faiss` (parent `2a82dfd`).
> Exact uint64 vectorized MinHash (5×, fuzz-proven, NumPy-gated), shared
> match view, binary index framing, sharded spawn-worker engine with ordered
> assembly (5× at 8 workers: 10k 66→326/s locked, 313→962/s with kernel;
> 100k 65→338/s locked, 462→1185/s kernel), deterministic sharded survivor
> output, full telemetry. Semantic lane OFF by default: embedding artifact
> contract, test providers, python/numpy/faiss-cpu/faiss-gpu backends with
> capability detection (FAISS absent here — RTX 4090 present but
> undrivable), candidate sidecar + threshold analysis; 10k/100k×384 and
> 1M×384 vector benchmarks measured. 31 new tests + 223 focused
> regressions passed; ruff/mypy clean. Full suite NOT RUN (final gate
> only). See [reports/P28.md](reports/P28.md). Overall production
> acceptance remains BLOCKED; main is unchanged.

> **P27B cleaning throughput (2026-09-23): IMPLEMENTED / VERIFIED (bounded
> offline)** on `perf/p27b-cleaning-throughput` (parent `34122ad`).
> Exact micro-optimizations (shared lazy text features, fused char scans,
> compile-once matchers, PII hint gate, copy shortcut, buffered quarantine:
> mixed-5k wall 6.42 s → 5.14 s) plus a deterministic sharded cleaning
> engine (verified manifest/dir/file/Parquet input, legacy or sharded
> accepted/quarantine output, process-level shard parallelism, ordered
> assembly, quarantine caps in global order). Scaling: 10k mixed
> 964 → 3232 docs/s w1→w8; 100k mixed (243 MiB) 956 → 4235 docs/s;
> 128 MiB 62 → 216 docs/s with RSS growth ≤ 5.3 MiB. 28 new tests +
> 145 focused regressions passed; ruff/mypy clean. Dedup precompute
> correctly omitted (`clean_hash` is already free). Full suite NOT RUN
> (final gate only). See [reports/P27B.md](reports/P27B.md). Overall
> production acceptance remains BLOCKED; main is unchanged.

> **P27A production sharding (2026-09-23): IMPLEMENTED / VERIFIED (bounded
> offline)** on `perf/p27a-production-sharding` (parent `0675331`).
> Deterministic size-sharded datasets (`dataset-manifest.json` v1), streaming
> 64 MiB input budget, atomic per-shard publish with manifest-last ordering,
> manifest-dir adapt input, `--output-shard-bytes` adapt path, strict
> verifier, and `adopt` verified-prefix primitive (resume wiring deferred).
> 28 focused + 3 serial + 1 slow test passed; ruff/mypy clean on scoped
> sources. 128 MiB synthetic benchmark: 2 shards, 1.5 s, 2753 rec/s,
> RSS +2.2 MiB. Full suite NOT RUN (final gate only). See
> [reports/P27A.md](reports/P27A.md). Overall production acceptance remains
> BLOCKED; main is unchanged.

> **D02 + D04/D05 + D07 + D08 integrated (2026-09-20): IMPLEMENTED / INTEGRATED
> (bounded Windows CPU offline)** on `integrate/d07-d08`. Merges `fix/d08`
> (D02 closeout + D08 analysis repair) and `fix/d07` (single-copy export);
> shared xdist tooling reconciled identical. Full offline audit on the final
> tree: parallel (77 files, `-n 8`) 1029 passed / 2 pre-existing failures,
> serial (7 files) all green, serial-marked 3/3, base-only green, quality and
> demo green, authored chain green. Remaining: D06 inventory timing, recipe and
> report-plan failures, receipt-verification gap, deferred tokenizer work,
> operator-run validation. See `reports/P23-D02-D04-D05.md`, `reports/P23-D07.md`,
> `reports/P23-D08.md` and the consolidated integration report below.
> Overall production acceptance remains BLOCKED; main is unchanged.

> D01 remediation is **IMPLEMENTED / VERIFIED (bounded offline)** under Stage 1 approval.
> See [reports/P23-D01.md](reports/P23-D01.md) and [REMEDIATION.md](REMEDIATION.md).
> D06 is **IMPLEMENTED / VERIFIED (bounded Windows CPU offline)**.
> D03 core is **IMPLEMENTED / VERIFIED (bounded Windows CPU)** for the baseline
> pilot handoff; see [P23-D03](reports/P23-D03.md). Cross-tokenizer comparison
> remains OPEN / DEFERRED. D02, D04/D05, D07 and D08 are implemented and
> integrated (see above); other later repairs require separate approval.
> Overall production acceptance remains BLOCKED.

> **P23 audit (2026-09-19): production acceptance is BLOCKED.** Historical milestone statuses describe their original evidence. The updated requirement ledger and [FINAL_ACCEPTANCE.md](FINAL_ACCEPTANCE.md) supersede broader readiness claims. D01/A03 and D06/A28 are verified within their offline scopes; A14/A27/A31 and the other unresolved remediation gates remain open. Live corpus, official evaluation and real protected deployment are not verified. P23 audit delivery is complete, not a production or research-results certification.

## Environment and repository

- Base git revision: `20673e3c6a2f80ebaaf4e89705d2951397fff3d0`; P23 audited the existing working tree including untracked P01–P22 implementation. The command evidence records actual source hashes; no new commit was made.
- Python / uv / extra: Python 3.12.13 / uv 0.11.6 / `cpu` and `cuda` extras (mutually exclusive); optional `eval` extra (`lm-eval==0.4.13`, P15) installable alongside either
- OS / GPU / driver: Windows 11 (P23 build 26200) / NVIDIA GeForce RTX 4090 24GB / Driver 596.49 (CUDA 13.2 compatible)
- Active contracts version: XLM v1 (September 2026)

## Historical Catalog & Document Discrepancies

- The previous FineWeb-first data catalog is formally superseded. FineWeb and FineWeb-Edu are not defaults or fallbacks. Direct-source denylists apply, and no implicit fallback or renormalization is permitted.
- The active catalog is defined in `manifests/datasets.catalog.yaml` and `DATA_CATALOG.md` (20 candidate sources, requiring local admission audits).

## Milestones

| Milestone | Name | Status |
|---|---|---|
| P00 | [Repository foundation and uv environment](prompts/00_foundation.md) | VERIFIED |
| P01 | [Contracts, config and artifacts](prompts/01_contracts_config_artifacts.md) | VERIFIED |
| P02 | [Local data and tokenizers](prompts/02_local_data_tokenizers.md) | VERIFIED |
| P03 | [Reference models](prompts/03_reference_models.md) | VERIFIED |
| P04 | [Losses, optimizers and schedules](prompts/04_losses_optimizers_schedules.md) | VERIFIED |
| P05 | [Training loop, checkpoint and resume](prompts/05_training_checkpoint_resume.md) | VERIFIED |
| P06 | [Native scoring and generation](prompts/06_native_scoring_and_generation.md) | VERIFIED |
| P07 | [Source discovery and admission](prompts/07_source_discovery_admission.md) | VERIFIED |
| P08 | [Bounded acquisition](prompts/08_bounded_acquisition.md) | VERIFIED |
| P09 | [Cleaning and quality pipeline](prompts/09_cleaning_quality_pipeline.md) | VERIFIED |
| P10 | [Deduplication, splits and exclusions](prompts/10_dedup_splits_exclusions.md) | VERIFIED |
| P11 | [Pool freeze and tokenizer regime](prompts/11_pool_freeze_tokenizer_regime.md) | VERIFIED |
| P12 | [Token shards and mixture packing](prompts/12_token_shards_mixture_packing.md) | VERIFIED |
| P13 | [Real dataset views and mix01](prompts/13_real_dataset_views_and_mix01.md) | VERIFIED |
| P14 | [CUDA profile and performance](prompts/14_cuda_profile_and_performance.md) | VERIFIED |
| P15 | [Official evaluation harness adapter](prompts/15_official_evaluation_harness.md) | VERIFIED |
| P16 | [Experiment plans and queue](prompts/16_experiment_plans_and_queue.md) | VERIFIED |
| P17 | [Statistics, comparisons and promotion](prompts/17_statistics_comparisons_promotion.md) | VERIFIED |
| P18 | [Research plugins and idea cards](prompts/18_research_plugins_and_idea_cards.md) | VERIFIED |
| P19 | [Reports and dashboard](prompts/19_reports_and_dashboard.md) | VERIFIED |
| P20 | [Export, generation and portability](prompts/20_export_generation_portability.md) | VERIFIED |
| P21 | [Isolation, security and release](prompts/21_isolation_security_release.md) | VERIFIED |
| P22 | [Campaign bootstrap and runbooks](prompts/22_campaign_bootstrap_and_runbooks.md) | VERIFIED |
| P23 | [Independent final acceptance](prompts/23_independent_final_acceptance.md) | VERIFIED audit; production BLOCKED |
| P24 | Acquisition-performance measurement (closeout, no optimization) | IMPLEMENTED / VERIFIED (offline fixtures); live 1/2/4/8 NOT RUN |

## Acceptance Requirements Ledger

| ID | Requirement | Prompts | Status | Evidence Reference |
|---|---|---|---|---|
| A01 | Environment and CLI | 00 | VERIFIED (scoped) | `docs/implementation/FINAL_ACCEPTANCE.md` (A01); historical: `docs/implementation/reports/P00.md` |
| A02 | Configuration correctness | 01 | VERIFIED (scoped) | `docs/implementation/FINAL_ACCEPTANCE.md` (A02); historical: `docs/implementation/reports/P01.md` |
| A03 | Immutable artifacts | 01 | VERIFIED (D01 declared-request consistency, Windows offline) | [P23-D01 code/test/result map](reports/P23-D01.md); authentic execution separately verified in [D06/A28](reports/P23-D06.md) |
| A04 | Local canonical data | 02 | VERIFIED (scoped) | `docs/implementation/FINAL_ACCEPTANCE.md` (A04); historical: `docs/implementation/reports/P02.md` |
| A05 | Reference architecture | 03 | VERIFIED (scoped) | `docs/implementation/FINAL_ACCEPTANCE.md` (A05); historical: `docs/implementation/reports/P03.md` |
| A06 | Objective normalization | 04,05 | VERIFIED (scoped) | `docs/implementation/FINAL_ACCEPTANCE.md` (A06); historical: `docs/implementation/reports/P04.md`, `docs/implementation/reports/P05.md` |
| A07 | Optimizer and scheduler | 04,05 | VERIFIED (scoped) | `docs/implementation/FINAL_ACCEPTANCE.md` (A07); historical: `docs/implementation/reports/P04.md`, `docs/implementation/reports/P05.md` |
| A08 | Exact token budget | 05,12 | VERIFIED (scoped) | `docs/implementation/FINAL_ACCEPTANCE.md` (A08); historical: `docs/implementation/reports/P05.md`, `docs/implementation/reports/P12.md` |
| A09 | Safe resume | 05,12 | VERIFIED (scoped) | `docs/implementation/FINAL_ACCEPTANCE.md` (A09); historical: `docs/implementation/reports/P05.md`, `docs/implementation/reports/P12.md` (multi-source mixture resume verified) |
| A10 | Native scoring | 06 | VERIFIED (scoped) | `docs/implementation/FINAL_ACCEPTANCE.md` (A10); historical: `docs/implementation/reports/P06.md` |
| A11 | Offline vertical slice | 00–06 | VERIFIED (scoped) | `docs/implementation/FINAL_ACCEPTANCE.md` (A11); historical: `docs/implementation/reports/P06.md` |
| A12 | Twenty-source discovery | 07 | IMPLEMENTED; live NOT RUN | `docs/implementation/FINAL_ACCEPTANCE.md` (A12); historical: `docs/implementation/reports/P07.md` |
| A13 | No FineWeb substitution | 07,13 | VERIFIED (scoped) | `docs/implementation/FINAL_ACCEPTANCE.md` (A13); historical: `docs/implementation/reports/P07.md`, `docs/implementation/reports/P13.md` (P13 adds preset/view-level denial blocking) |
| A14 | Acquisition bounds | 08 | FAILED | `docs/implementation/FINAL_ACCEPTANCE.md` (A14); historical: `docs/implementation/reports/P08.md` |
| A15 | Source live verification | 07,08,13 | BLOCKED (corpus); metadata VERIFIED | `docs/implementation/FINAL_ACCEPTANCE.md` (A15); historical: `docs/implementation/reports/P07.md`, `docs/implementation/reports/P08.md`, `docs/implementation/reports/P13.md` (P13 metadata-only discovery: real revisions/configs for 9 of 10 mix01 repos; no adapter pilot, all views NOT LIVE-VERIFIED) |
| A16 | Normalization and cleaning | 09 | VERIFIED (scoped) | `docs/implementation/FINAL_ACCEPTANCE.md` (A16); historical: `docs/implementation/reports/P09.md` |
| A17 | Deduplication and lineage | 10 | VERIFIED (scoped) | `docs/implementation/FINAL_ACCEPTANCE.md` (A17); historical: `docs/implementation/reports/P10.md` |
| A18 | Corpus split integrity | 10,11 | VERIFIED (scoped) | `docs/implementation/FINAL_ACCEPTANCE.md` (A18); historical: `docs/implementation/reports/P10.md`, `docs/implementation/reports/P11.md` (P10 split integrity; P11 leak gate at the pool boundary and frozen quick subset) |
| A19 | Benchmark exclusion | 10,21 | VERIFIED (scoped) | `docs/implementation/FINAL_ACCEPTANCE.md` (A19); historical: `docs/implementation/reports/P10.md`, `docs/implementation/reports/P21.md` (development matching plus operator receipt path: opaque decontamination receipts, aggregates-only finals, bounded quotas; production deployment NOT RUN) |
| A20 | Pool/tokenizer freeze | 11 | VERIFIED (scoped) | `docs/implementation/FINAL_ACCEPTANCE.md` (A20); historical: `docs/implementation/reports/P11.md` (demo/pilot scope; production 32,768 fit planned but NOT RUN pending admitted sources in P13) |
| A21 | Token-shard format | 12 | VERIFIED (scoped) | `docs/implementation/FINAL_ACCEPTANCE.md` (A21); historical: `docs/implementation/reports/P12.md` |
| A22 | Mixture scheduling | 12 | VERIFIED D03 token-mixture core; matched tokenizer exposure OPEN / DEFERRED | [P23-D03](reports/P23-D03.md): actual public two-source inputs, quotas, caps, isolation, traces and fresh-process continuation; authored Windows CPU scope |
| A23 | Initial mixture and treatments | 13 | VERIFIED definitions; live BLOCKED | `docs/implementation/FINAL_ACCEPTANCE.md` (A23); historical: `docs/implementation/reports/P13.md` (definition/gating scope: M0–M5 + explicit no-IFM validated offline, admitted-source blocking enforced; live mixture BLOCKED, pilot NOT RUN) |
| A24 | CUDA correctness | 14 | VERIFIED (scoped) | `docs/implementation/FINAL_ACCEPTANCE.md` (A24); historical: `docs/implementation/reports/P14.md` (RTX 4090: CPU/CUDA numerics, SDPA/eager, BF16, checkpointing, accumulation, resume, FP16 scaler; compile parity NOT RUN — no codegen toolchain) |
| A25 | CUDA performance | 14 | VERIFIED tiny; per-size NOT RUN in P23 | `docs/implementation/FINAL_ACCEPTANCE.md` (A25); historical: `docs/implementation/reports/P14.md` (measured tokens/sec, VRAM, optimizer/checkpoint sizes for 50m/150m/300m; ETA intervals with uncertainty, no peak-FLOP promises) |
| A26 | Official harness parity | 15 | BLOCKED official; fixtures VERIFIED | `docs/implementation/FINAL_ACCEPTANCE.md` (A26); historical: `docs/implementation/reports/P15.md` (pinned lm-eval 0.4.13 via public registry; native parity on fixtures; one bounded live ARC-Easy train smoke; full official suites NOT RUN) |
| A27 | Evaluation tiering | 15,21 | FAILED | `docs/implementation/FINAL_ACCEPTANCE.md` (A27); historical: `docs/implementation/reports/P15.md`, `docs/implementation/reports/P21.md` (explicit search/confirmation/final variants, split firewall, partial labeling, final request frozen; isolated execution with replay/revocation/quota guards) |
| A28 | Queue and code freeze | 16 | VERIFIED D06 bounded Windows CPU | [P23-D06](reports/P23-D06.md); captured plugin mutation and frozen queue/recovery regressions pass again in [D03](reports/P23-D03.md); external validation NOT RUN |
| A29 | Campaign cost limits | 16,22 | VERIFIED smoke; production BLOCKED | `docs/implementation/FINAL_ACCEPTANCE.md` (A29); historical: `docs/implementation/reports/P16.md`, `docs/implementation/reports/P22.md` (smoke caps, hash-bound tickets, over-budget refusal, selection gates, fork-only horizons; staged 50M→300M campaign as plans; measured cost via profiles else unmeasured) |
| A30 | Comparison fairness | 17 | VERIFIED (scoped) | `docs/implementation/FINAL_ACCEPTANCE.md` (A30); historical: `docs/implementation/reports/P17.md` (six tracks with allowed-differences schemas; ineligible pairs with readable diffs; unknown fields fail closed) |
| A31 | Statistical analysis | 17 | FAILED (D08); fixture tests pass | `docs/implementation/FINAL_ACCEPTANCE.md` (A31); historical: `docs/implementation/reports/P17.md` (paired cluster bootstrap, deterministic seeds; seed/item uncertainty separated; interpolation-only target crossing; teacher/aux costs labeled) |
| A32 | Promotion and ablations | 17 | VERIFIED (scoped) | `docs/implementation/FINAL_ACCEPTANCE.md` (A32); historical: `docs/implementation/reports/P17.md` (frozen gates; from-scratch drafts with lineage; factorial/ablation matrices as plans; nothing launched or authorized) |
| A33 | Research extension seams | 18 | VERIFIED D03 supported typed construction | [P23-D03](reports/P23-D03.md): shared direct/queue/resume construction; baseline and meaningful authored architecture/objective/optimizer/tokenizer execution; unsupported combinations refused, not exhaustive plugin support |
| A34 | Reports and dashboard | 19 | VERIFIED (scoped) | `docs/implementation/FINAL_ACCEPTANCE.md` (A34); historical: `docs/implementation/reports/P19.md` (offline JSON/CSV/Markdown/HTML over authoritative data; escaped previews; missing as n/a; loopback read-only dashboard; UI screenshots NOT RUN headless) |
| A35 | Export and generation | 20 | VERIFIED (scoped) | `docs/implementation/FINAL_ACCEPTANCE.md` (A35); historical: `docs/implementation/reports/P20.md` (native safetensors export + hash-first loading; clean-process parity; corruption/version/plugin/secret refusals; replayable sessions; HF layout mapping with runtime parity NOT RUN) |
| A36 | Protected deployment | 21 | NOT RUN (deployment) | `docs/implementation/FINAL_ACCEPTANCE.md` (A36); historical: `docs/implementation/reports/P21.md` (sealed-readiness logic and all refusal paths verified; real two-identity deployment NOT RUN by design here) |
| A37 | Release audit | 21 | VERIFIED fixtures; release BLOCKED | `docs/implementation/FINAL_ACCEPTANCE.md` (A37); historical: `docs/implementation/reports/P21.md` (eleven-check audit over synthetic releases; unknown rights stay BLOCKED; no real release certified) |
| A38 | Complete runbooks | 22 | VERIFIED Windows fixture; Linux NOT RUN | `docs/implementation/FINAL_ACCEPTANCE.md` (A38); historical: `docs/implementation/reports/P22.md` (uv-first Windows/Linux runbooks with implemented flags; disk locations, reachability cleanup, cancellation, failures, portability, unverified inventory) |
| A39 | Independent audit | 23 | VERIFIED audit delivery | `docs/implementation/FINAL_ACCEPTANCE.md` (A39); historical:  |

## Evidence per milestone

### P00 — Foundation and uv environment
- Status: VERIFIED
- Files:
  - `.python-version`
  - `pyproject.toml`
  - `uv.lock`
  - `.gitignore`
  - `AGENTS.md`
  - `.github/workflows/ci.yml`
  - `src/xlm/__init__.py`
  - `src/xlm/core/__init__.py`
  - `src/xlm/core/paths.py`
  - `src/xlm/cli/__init__.py`
  - `src/xlm/cli/main.py`
  - `src/xlm/cli/doctor.py`
  - `tests/conftest.py`
  - `tests/test_cli.py`
  - `tests/test_doctor.py`
  - `tests/test_imports.py`
  - `tests/test_paths.py`
  - `tests/test_cuda.py`
- Test commands and exit statuses:
  - `uv sync --locked`: exit code 0 (clean base installation without torch verified)
  - `uv sync --locked --extra cpu --extra cuda`: exit code 1 (conflicting extras blocked as required)
  - `uv sync --locked --extra cpu`: exit code 0 (`torch==2.14.0+cpu` installed)
  - `uv run --locked --extra cpu ruff format --check`: exit code 0 (clean formatting)
  - `uv run --locked --extra cpu ruff check`: exit code 0 (all lint checks passed)
  - `uv run --locked --extra cpu mypy src tests`: exit code 0 (strict type check passed across 12 files)
  - `uv run --locked --extra cpu pytest -v -m "not cuda and not network and not operator"`: exit code 0 (17 passed, 1 deselected)
  - `uv run --locked --extra cpu xlm --help`: exit code 0
  - `uv run --locked --extra cpu xlm --version`: exit code 0 (`xlm 0.1.0`)
  - `uv run --locked --extra cpu xlm doctor`: exit code 0 (accurate CPU report)
  - `uv run --locked --extra cpu xlm doctor --json`: exit code 0 (strictly valid JSON on stdout)
  - Subprocess execution outside repo (`C:\Users\gamma`): exit code 0
  - `uv sync --locked --extra cuda`: exit code 0 (`torch==2.14.0+cu126` installed)
  - `uv run --locked --extra cuda xlm doctor`: exit code 0 (RTX 4090 detected, sm_89, 23.99 GB VRAM)
  - `uv run --locked --extra cuda pytest -v -m "cuda"`: exit code 0 (tensor forward/backward/synchronize passed)
  - `uv sync --locked --extra cpu`: exit code 0 (restored CPU extra)
- Evidence report: `docs/implementation/reports/P00.md`

### P01 — Contracts, config and artifacts
- Status: VERIFIED
- Files:
  - `pyproject.toml`
  - `src/xlm/core/contracts.py`
  - `src/xlm/core/registry.py`
  - `src/xlm/config/__init__.py`
  - `src/xlm/config/schemas.py`
  - `src/xlm/config/composer.py`
  - `src/xlm/artifacts/__init__.py`
  - `src/xlm/artifacts/manifest.py`
  - `src/xlm/artifacts/store.py`
  - `src/xlm/artifacts/ledger.py`
  - `src/xlm/cli/config_cmd.py`
  - `src/xlm/cli/artifact_cmd.py`
  - `src/xlm/cli/main.py`
  - `tests/test_contracts.py`
  - `tests/test_registry.py`
  - `tests/test_config.py`
  - `tests/test_artifacts.py`
  - `tests/test_ledger.py`
- Test commands and exit statuses:
  - `uv sync --locked`: exit code 0 (clean base installation without torch verified)
  - `uv run --locked pytest -q -m "not cuda and not network and not operator"`: exit code 0 (50 passed in base environment without torch)
  - `uv run --locked xlm config validate recipes/models/transformer_125m.yaml`: exit code 0
  - `uv run --locked xlm config resolve recipes/models/transformer_125m.yaml`: exit code 0
  - `uv run --locked xlm artifact --help`: exit code 0
  - `uv sync --locked --extra cpu`: exit code 0 (`torch==2.14.0+cpu` installed)
  - `uv run --locked --extra cpu ruff format --check`: exit code 0 (clean formatting across 63 files)
  - `uv run --locked --extra cpu ruff check`: exit code 0 (all lint checks passed)
  - `uv run --locked --extra cpu mypy src tests`: exit code 0 (strict type check passed across 26 files)
  - `uv run --locked --extra cpu pytest -v -m "not cuda and not network and not operator"`: exit code 0 (50 passed, 1 deselected)
  - `git diff uv.lock`: exit code 0 (0 lockfile diff)
- Evidence report: `docs/implementation/reports/P01.md`

### P02 — Local data and tokenizers
- Status: VERIFIED
- Files:
  - `pyproject.toml`
  - `uv.lock`
  - `src/xlm/data/__init__.py`
  - `src/xlm/data/normalization.py`
  - `src/xlm/data/canonical_io.py`
  - `src/xlm/data/tokens.py`
  - `src/xlm/data/adapters/__init__.py`
  - `src/xlm/data/adapters/text.py`
  - `src/xlm/data/adapters/jsonl.py`
  - `src/xlm/tokenizers/__init__.py`
  - `src/xlm/tokenizers/base.py`
  - `src/xlm/tokenizers/byte.py`
  - `src/xlm/tokenizers/bpe.py`
  - `src/xlm/config/schemas.py`
  - `src/xlm/cli/data_cmd.py`
  - `src/xlm/cli/tokenizer_cmd.py`
  - `src/xlm/cli/main.py`
  - `fixtures/sources/sample_local/manifest.yaml`
  - `fixtures/sources/sample_local/documents.jsonl`
  - `fixtures/sources/sample_local/sample_text.txt`
  - `tests/test_data_adapters.py`
  - `tests/test_token_shards.py`
  - `tests/test_tokenizers.py`
- Test commands and exit statuses:
  - `uv sync --locked`: exit code 0 (clean base installation with tokenizers and pyarrow, zero torch dependency)
  - `uv run --locked pytest -q -m "not cuda and not network and not operator"`: exit code 0 (67 passed in base environment without torch)
  - `uv sync --locked --extra cpu`: exit code 0
  - `uv run --locked --extra cpu ruff format --check`: exit code 0 (80 files clean)
  - `uv run --locked --extra cpu ruff check`: exit code 0 (all lint checks passed)
  - `uv run --locked --extra cpu mypy src tests`: exit code 0 (strict type check passed across 42 files)
  - `uv run --locked --extra cpu pytest -v -m "not cuda and not network and not operator"`: exit code 0 (67 passed, 1 deselected)
  - `uv run --locked --extra cpu xlm data import-local --manifest fixtures/sources/sample_local/manifest.yaml --publish`: exit code 0 (published `canonical_sample_local`)
  - `uv run --locked --extra cpu xlm artifact inspect canonical_sample_local`: exit code 0
  - `uv run --locked --extra cpu xlm artifact verify canonical_sample_local`: exit code 0
  - `uv run --locked --extra cpu xlm tokenizer train --data-path canonical_sample_local --vocab-size 350 --publish`: exit code 0 (published `tokenizer_bpe_350`)
  - `uv run --locked --extra cpu xlm tokenizer inspect tokenizer_bpe_350`: exit code 0
  - `uv run --locked --extra cpu xlm tokenizer encode tokenizer_bpe_350 "Hello world! François 🚀 <eos>"`: exit code 0 (0 control IDs emitted, exact round-trip)
  - `uv run --locked --extra cpu xlm tokenizer verify tokenizer_bpe_350`: exit code 0 (all 9 suites passed)
  - `git diff uv.lock`: exit code 0 (lockfile clean)
- Evidence report: `docs/implementation/reports/P02.md`

### P03 — Reference models
- Status: VERIFIED
- Files:
  - `recipes/models/tiny.yaml`
  - `src/xlm/config/schemas.py`
  - `src/xlm/models/__init__.py`
  - `src/xlm/models/base.py`
  - `src/xlm/models/rmsnorm.py`
  - `src/xlm/models/rope.py`
  - `src/xlm/models/masks.py`
  - `src/xlm/models/feedforward.py`
  - `src/xlm/models/attention.py`
  - `src/xlm/models/initialization.py`
  - `src/xlm/models/parameter_counts.py`
  - `src/xlm/models/transformer.py`
  - `src/xlm/models/serialization.py`
  - `src/xlm/cli/model_cmd.py`
  - `src/xlm/cli/main.py`
  - `tests/test_attention_and_masks.py`
  - `tests/test_models.py`
- Test commands and exit statuses:
  - `uv sync --locked`: exit code 0 (clean base installation without torch verified)
  - `python -c "import sys, xlm; assert 'torch' not in sys.modules"`: exit code 0 (clean import isolation in base environment)
  - `uv run --locked xlm --help`: exit code 0
  - `uv run --locked xlm model --help`: exit code 0
  - `uv sync --locked --extra cpu`: exit code 0 (`torch==2.14.0+cpu` installed)
  - `uv run --locked --extra cpu ruff format --check`: exit code 0 (95 files clean)
  - `uv run --locked --extra cpu ruff check`: exit code 0 (all lint checks passed)
  - `uv run --locked --extra cpu mypy src tests`: exit code 0 (strict type check passed across 56 source files)
  - `uv run --locked --extra cpu pytest -v -m "not cuda and not network and not operator"`: exit code 0 (94 passed, 1 deselected)
  - `uv run --locked --extra cpu xlm model inspect tiny`: exit code 0 (2,179,392 unique deployed parameters, exact match)
  - `uv run --locked --extra cpu xlm model inspect 50m`: exit code 0 (49,883,648 unique deployed parameters, exact match)
  - `uv run --locked --extra cpu xlm model inspect 150m`: exit code 0 (149,942,016 unique deployed parameters, exact match)
  - `uv run --locked --extra cpu xlm model inspect 300m`: exit code 0 (299,418,624 unique deployed parameters, exact match)
  - `uv run --locked --extra cpu xlm model inspect tiny --json`: exit code 0 (valid JSON output)
  - `git diff uv.lock`: exit code 0 (lockfile clean)
- Evidence report: `docs/implementation/reports/P03.md`

### P04 — Losses, optimizers and schedules
- Status: VERIFIED
- Files:
  - `src/xlm/objectives/__init__.py`
  - `src/xlm/objectives/base.py`
  - `src/xlm/objectives/cross_entropy.py`
  - `src/xlm/objectives/noop.py`
  - `src/xlm/objectives/auxiliary_fixture.py`
  - `src/xlm/optimizers/__init__.py`
  - `src/xlm/optimizers/base.py`
  - `src/xlm/optimizers/adamw.py`
  - `src/xlm/optimizers/clipping.py`
  - `src/xlm/schedules/__init__.py`
  - `src/xlm/schedules/base.py`
  - `src/xlm/schedules/cosine.py`
  - `src/xlm/schedules/constant.py`
  - `src/xlm/schedules/anchors.py`
  - `src/xlm/config/schemas.py`
  - `tests/test_objectives.py`
  - `tests/test_optimizers.py`
  - `tests/test_schedules.py`
  - `tests/test_continuation.py`
- Test commands and exit statuses:
  - `uv sync --locked`: exit code 0 (clean base installation without torch verified)
  - `uv run --locked python -c "..."`: exit code 0 (verified clean base import isolation & registry discovery without torch)
  - `uv run --locked xlm --help`: exit code 0
  - `uv run --locked pytest -q ...`: exit code 0 (59 passed in clean base environment)
  - `uv sync --locked --extra cpu`: exit code 0 (`torch==2.14.0+cpu` installed)
  - `uv run --locked --extra cpu ruff format --check`: exit code 0 (114 files cleanly formatted)
  - `uv run --locked --extra cpu ruff check`: exit code 0 (all lint checks passed)
  - `uv run --locked --extra cpu mypy src tests`: exit code 0 (strict type check passed across 74 source files)
  - `uv run --locked --extra cpu pytest -v -m "not cuda and not network and not operator"`: exit code 0 (117 passed, 1 deselected in 23.13s)
  - `git diff uv.lock`: exit code 0 (lockfile clean)
- Evidence report: `docs/implementation/reports/P04.md`

### P05 — Training loop, checkpoint and resume
- Status: VERIFIED
- Files:
  - `src/xlm/training/__init__.py`
  - `src/xlm/training/data.py`
  - `src/xlm/training/checkpoint.py`
  - `src/xlm/training/trainer.py`
  - `src/xlm/cli/train_cmd.py`
  - `src/xlm/cli/demo_cmd.py`
  - `src/xlm/cli/main.py`
  - `src/xlm/artifacts/ledger.py`
  - `tests/test_trainer_data.py`
  - `tests/test_checkpoint.py`
  - `tests/test_trainer.py`
  - `tests/test_continuation_p05.py`
  - `tests/test_cli_train_demo.py`
- Test commands and exit statuses:
  - `uv sync --locked`: exit code 0 (clean base installation without torch verified)
  - `uv run --locked python -c "import torch"`: exit code 1 (`ModuleNotFoundError` confirmed in base environment)
  - `uv run --locked python -m xlm.cli.main run inspect --help`: exit code 0 (metadata-only CLI operates without torch)
  - `uv run --locked python -m xlm.cli.main train --help`: exit code 0 (CLI help operates without torch)
  - `uv run --locked python -m xlm.cli.main train dummy.json`: exit code 1 (graceful error requiring torch extra)
  - `uv run --locked pytest -v tests/test_artifacts.py tests/test_config.py tests/test_contracts.py tests/test_ledger.py tests/test_paths.py tests/test_registry.py tests/test_tokenizers.py tests/test_token_shards.py`: exit code 0 (48 core tests passed in base environment)
  - `uv sync --locked --extra cpu`: exit code 0 (`torch==2.14.0+cpu` installed)
  - `uv run --locked --extra cpu ruff format --check`: exit code 0 (126 files cleanly formatted)
  - `uv run --locked --extra cpu ruff check`: exit code 0 (all lint checks passed)
  - `uv run --locked --extra cpu mypy src tests`: exit code 0 (strict type check passed across 85 source files)
  - `uv run --locked --extra cpu pytest -v -m "not cuda and not network and not operator"`: exit code 0 (137 passed, 1 deselected)
  - `uv run --locked --extra cpu python -m xlm.cli.main demo`: exit code 0 (200-target end-to-end vertical slice demo passed)
  - `git diff uv.lock`: exit code 0 (lockfile clean)
- Evidence report: `docs/implementation/reports/P05.md`

### P06 — Native scoring and generation
- Status: VERIFIED
- Files:
  - `src/xlm/evaluation/__init__.py`
  - `src/xlm/evaluation/likelihood.py`
  - `src/xlm/evaluation/fixtures.py`
  - `src/xlm/evaluation/scorer.py`
  - `src/xlm/evaluation/diagnostics.py`
  - `src/xlm/inference/__init__.py`
  - `src/xlm/inference/generation.py`
  - `src/xlm/models/base.py`
  - `src/xlm/models/transformer.py`
  - `src/xlm/models/serialization.py`
  - `src/xlm/cli/eval_cmd.py`
  - `src/xlm/cli/generate_cmd.py`
  - `src/xlm/cli/demo_cmd.py`
  - `src/xlm/cli/main.py`
  - `tests/test_likelihood.py`
  - `tests/test_generation.py`
  - `tests/test_evaluation_fixtures.py`
  - `tests/test_cli_eval_gen.py`
- Test commands and exit statuses:
  - `uv sync --locked`: exit code 0 (clean base installation without torch verified)
  - `uv run --locked python -c "import torch"`: exit code 1 (`ModuleNotFoundError` confirmed in base environment)
  - `uv run --locked python -m xlm.cli.main --help`: exit code 0 (CLI root operates without torch)
  - `uv run --locked python -m xlm.cli.main evaluate --help`: exit code 0 (evaluate CLI help operates without torch)
  - `uv run --locked python -m xlm.cli.main generate --help`: exit code 0 (generate CLI help operates without torch)
  - `uv run --locked python -m xlm.cli.main demo --help`: exit code 0 (demo CLI help operates without torch)
  - `uv run --locked pytest -v tests/test_artifacts.py tests/test_config.py tests/test_doctor.py tests/test_imports.py tests/test_ledger.py tests/test_paths.py tests/test_registry.py tests/test_tokenizers.py`: exit code 0 (46 core tests passed in base environment)
  - `uv sync --locked --extra cpu`: exit code 0 (`torch==2.14.0+cpu` installed)
  - `uv run --locked --extra cpu ruff format --check src tests`: exit code 0 (95 files cleanly formatted)
  - `uv run --locked --extra cpu ruff check src tests`: exit code 0 (all lint checks passed)
  - `uv run --locked --extra cpu mypy src tests`: exit code 0 (strict type check passed across 98 source files)
  - `uv run --locked --extra cpu pytest -v tests/test_likelihood.py tests/test_generation.py tests/test_evaluation_fixtures.py tests/test_cli_eval_gen.py`: exit code 0 (22 dedicated P06 tests passed)
  - `uv run --locked --extra cpu pytest -v -m "not cuda and not network and not operator"`: exit code 0 (159 passed, 1 deselected in 62.25s)
  - `uv run --locked --extra cpu python -m xlm.cli.main demo`: exit code 0 (complete offline vertical slice P00-P06 demo successfully completed)
  - `git diff uv.lock`: exit code 0 (lockfile clean)
- Evidence report: `docs/implementation/reports/P06.md`

### P07 — Source discovery and admission
- Status: VERIFIED
- Files:
  - `src/xlm/data/sources/__init__.py`
  - `src/xlm/data/sources/catalog.py`
  - `src/xlm/data/sources/policy.py`
  - `src/xlm/data/sources/transport.py`
  - `src/xlm/data/sources/schema.py`
  - `src/xlm/data/sources/prober.py`
  - `src/xlm/data/sources/admission.py`
  - `src/xlm/cli/data_cmd.py`
  - `src/xlm/cli/main.py`
  - `manifests/datasets.catalog.yaml`
  - `fixtures/sources/sample_nested/manifest.yaml`
  - `fixtures/sources/sample_nested/nested_sample.parquet`
  - `fixtures/sources/sample_nested/mismatched_sample.parquet`
  - `fixtures/sources/sample_nested/sample_records.jsonl`
  - `tests/test_data_catalog.py`
  - `tests/test_source_discovery.py`
  - `tests/test_source_admission.py`
- Test commands and exit statuses:
  - `uv run --locked python -c "import sys; import xlm.data.sources; print('torch in modules:', 'torch' in sys.modules)"`: exit code 0 (`torch in modules: False` confirmed, zero-PyTorch base environment isolation)
  - `uv run --locked ruff check src/ tests/`: exit code 0 (all lint checks passed)
  - `uv run --locked ruff format --check src/ tests/`: exit code 0 (105 files cleanly formatted)
  - `uv run --locked mypy src/ tests/`: exit code 0 (strict type check passed across 108 source files)
  - `uv run --locked pytest tests/test_data_catalog.py tests/test_source_discovery.py tests/test_source_admission.py -v`: exit code 0 (28 dedicated P07 tests passed in 1.35s)
  - `uv run --locked pytest -m "not network"`: exit code 0 (186 passed, 1 skipped, 1 deselected in 61.27s)
  - `uv run --locked pytest tests/test_source_admission.py -k test_live_public_metadata_probe_seam`: exit code 0 (live Hugging Face API discovery verified within budget)
  - `uv run --locked python -m xlm.cli.main data sources --catalog manifests/datasets.catalog.yaml`: exit code 0 (all 20 sources listed as Pending)
  - `uv run --locked python -m xlm.cli.main data audit --catalog manifests/datasets.catalog.yaml`: exit code 0 (audited: 20 unadmitted, 0 admitted, 0 pending review)
  - `uv run --locked python -m xlm.cli.main data probe --catalog manifests/datasets.catalog.yaml --source finewiki --live`: exit code 0 (live discovery verified, commit SHA pinned, 46.6 KiB transferred, unadmitted status preserved)
  - `git diff uv.lock`: exit code 0 (lockfile clean)
- Evidence report: `docs/implementation/reports/P07.md`

### P08 — Bounded acquisition
- Status: VERIFIED
- Files:
  - `src/xlm/data/acquisition/__init__.py`
  - `src/xlm/data/acquisition/plan.py`
  - `src/xlm/data/acquisition/receipt.py`
  - `src/xlm/data/acquisition/disk.py`
  - `src/xlm/data/acquisition/progress.py`
  - `src/xlm/data/acquisition/fetcher.py`
  - `src/xlm/data/acquisition/verifier.py`
  - `src/xlm/cli/data_cmd.py`
  - `tests/test_acquisition_plan.py`
  - `tests/test_acquisition_fetcher.py`
  - `tests/test_acquisition_verifier.py`
  - `tests/test_acquisition_live.py`
- Test commands and exit statuses:
  - `uv run --locked python -c "import sys; import xlm.data.acquisition; print('torch in modules:', 'torch' in sys.modules)"`: exit code 0 (`torch in modules: False` confirmed, zero-PyTorch base environment isolation)
  - `uv run --locked ruff check src/ tests/`: exit code 0 (all lint checks passed)
  - `uv run --locked ruff format --check src/ tests/`: exit code 0 (116 files cleanly formatted)
  - `uv run --locked mypy src/ tests/`: exit code 0 (strict type check passed across 119 source files)
  - `uv run --locked pytest tests/test_acquisition_plan.py tests/test_acquisition_fetcher.py tests/test_acquisition_verifier.py -v`: exit code 0 (21 dedicated P08 tests passed in 4.52s)
  - `uv run --locked pytest tests/test_acquisition_live.py -v`: exit code 0 (live Hugging Face Hub pilot acquisition seam verified in 0.84s)
  - `uv run --locked pytest -m "not network and not cuda and not operator"`: exit code 0 (207 passed, 3 deselected in 66.64s)
  - `uv run --locked python -m xlm.cli.main data plan --source finewiki --files language_subsets.csv --pilot-approved --output data/scratch/test_plan.json`: exit code 0 (CLI plan compilation verified)
  - `uv run --locked python -m xlm.cli.main data fetch --plan data/scratch/test_plan.json --output-dir data/raw/test_finewiki --scratch-dir data/scratch/test_fetch`: exit code 0 (resumable fetch completed)
  - `uv run --locked python -m xlm.cli.main data status --plan data/scratch/test_plan.json --scratch-dir data/scratch/test_fetch`: exit code 0 (progress journal inspected)
  - `uv run --locked python -m xlm.cli.main data verify --plan data/scratch/test_plan.json --output-dir data/raw/test_finewiki`: exit code 0 (receipt validated and raw_dataset artifact published to P01 store)
  - `git diff uv.lock`: exit code 0 (lockfile clean)
- Evidence report: `docs/implementation/reports/P08.md`
- Next milestone: P09 (`prompts/09_cleaning_quality_pipeline.md`) — completed, see below



### P09 — Cleaning and quality pipeline
- Status: VERIFIED
- Note: partially implemented in a previous session and carried as IN PROGRESS. This
  session audited the inherited work, repaired the defects that blocked acceptance,
  closed the unmet acceptance criteria, and restored a clean gate state. Full audit
  findings are in `docs/implementation/reports/P09.md` §2.
- Files (new in this milestone):
  - `src/xlm/data/cleaning/__init__.py`
  - `src/xlm/data/cleaning/base.py`
  - `src/xlm/data/cleaning/types.py`
  - `src/xlm/data/cleaning/normalization.py`
  - `src/xlm/data/cleaning/html.py`
  - `src/xlm/data/cleaning/boilerplate.py`
  - `src/xlm/data/cleaning/repetition.py`
  - `src/xlm/data/cleaning/language.py`
  - `src/xlm/data/cleaning/length_noise.py`
  - `src/xlm/data/cleaning/pii.py`
  - `src/xlm/data/cleaning/structured.py`
  - `src/xlm/data/cleaning/quarantine.py`
  - `src/xlm/data/cleaning/reporting.py`
  - `src/xlm/data/cleaning/pipeline.py`
  - `fixtures/cleaning/shards/shard_00.jsonl` … `shard_05.jsonl`
  - `tests/test_cleaning_normalization.py`
  - `tests/test_cleaning_html.py`
  - `tests/test_cleaning_filters.py`
  - `tests/test_cleaning_structured.py`
  - `tests/test_cleaning_pipeline.py`
  - `tests/test_cleaning_scale.py`
- Files (modified in this session):
  - `src/xlm/data/canonical_io.py` (streaming Parquet read/write, multi-shard `read_shards`)
  - `src/xlm/cli/data_cmd.py` (shard fan-in, streaming writes, repaired `publish_artifact` call)
  - `src/xlm/cli/artifact_cmd.py` (artifact lookup now enumerates present kinds)
  - `tests/test_artifacts.py` (artifact-lookup regression tests)
- Test commands and exit statuses:
  - `uv run --locked ruff check src/ tests/`: exit code 0 (all checks passed; 66 pre-existing errors cleared)
  - `uv run --locked ruff format --check src/ tests/`: exit code 0 (136 files formatted; 6 previously unformatted)
  - `uv run --locked mypy src/ tests/`: exit code 0 (139 source files; 12 pre-existing errors cleared)
  - `uv run --locked pytest tests/ -m "not network and not cuda and not operator"`: exit code 0 (245 passed, 3 deselected in 66.55s; previously 231 passed / 1 failed)
  - `uv run --locked pytest tests/test_cleaning_*.py -q`: exit code 0 (36 P09 tests passed in 1.15s)
  - `uv run --locked python -c "import sys, xlm.data.cleaning; ..."`: exit code 0 (`torch` not imported; verified in a fresh interpreter)
  - `uv run --locked python -m xlm.cli.main data clean --input fixtures/cleaning/shards --output-dir data/clean/p09_fixture --preset educational_prose --publish`: exit code 0 (60 → 48 docs, 80.0% doc yield / 36.9% byte yield, 12 rejected, `clean_dataset` artifact published)
  - `uv run --locked python -m xlm.cli.main artifact verify clean_educational_prose_e5737733dfd6`: exit code 0 (verified against 4 file checksums)
  - `uv run --locked python -m xlm.cli.main data quality-report --dir data/clean/p09_fixture`: exit code 0
  - Bounded-memory negative control: accumulating implementation reintroduced → 3 tests failed; streaming implementation restored → 11 passed
  - `uv.lock` / `pyproject.toml`: unchanged (no dependency added)
- Measured fixture yield (synthetic 60-document corpus, not a live-source estimate):
  60 docs / 19,740 bytes in; 48 docs / 7,278 bytes retained; rejections
  `excessive_repetition:line_frequency_40` ×6 and `detected_secret:huggingface_token` ×6.
  Planted credential appears zero times in any output or quarantine file.
- Limitations recorded: fixture-only evidence; no labeled audit sample, so no
  classification precision is claimed; PII detection is not a certification;
  educational-density exemption is a declared heuristic; bounded memory demonstrated
  over 6 shards, not at corpus scale.
- Evidence report: `docs/implementation/reports/P09.md`
- Next milestone: P10 (`prompts/10_dedup_splits_exclusions.md`)

### P10 — Deduplication, lineage-safe splits and contamination controls
- Status: VERIFIED (development-mode scope; protected-final execution remains P21)
- Files (new):
  - `src/xlm/data/dedup/__init__.py`
  - `src/xlm/data/dedup/matchview.py`
  - `src/xlm/data/dedup/minhash.py`
  - `src/xlm/data/dedup/index.py`
  - `src/xlm/data/dedup/lineage.py`
  - `src/xlm/data/dedup/clusters.py`
  - `src/xlm/data/dedup/engine.py`
  - `src/xlm/data/pools/__init__.py`
  - `src/xlm/data/pools/splits.py`
  - `src/xlm/data/pools/freeze.py`
  - `src/xlm/data/exclusion/__init__.py`
  - `src/xlm/data/exclusion/benchmark.py`
  - `src/xlm/data/exclusion/receipt.py`
  - `tests/test_dedup_engine.py`
  - `tests/test_dedup_lineage.py`
  - `tests/test_pool_splits.py`
  - `tests/test_exclusion_benchmark.py`
  - `tests/test_exclusion_receipt.py`
- Files (modified):
  - `src/xlm/cli/data_cmd.py` (added `xlm data dedup` and `xlm data split`)
- Test commands and exit statuses:
  - `uv run --locked ruff check src/ tests/`: exit code 0 (all checks passed)
  - `uv run --locked ruff format --check src/ tests/`: exit code 0 (154 files formatted)
  - `uv run --locked mypy src/ tests/`: exit code 0 (157 source files, strict)
  - `uv run --locked pytest tests/ -m "not network and not cuda and not operator"`: exit code 0 (317 passed, 3 deselected in 66.94s; 245 before this milestone)
  - `uv run --locked pytest tests/test_dedup_engine.py`: exit code 0 (20 passed)
  - `uv run --locked pytest tests/test_dedup_lineage.py`: exit code 0 (6 passed)
  - `uv run --locked pytest tests/test_pool_splits.py`: exit code 0 (14 passed)
  - `uv run --locked pytest tests/test_exclusion_benchmark.py`: exit code 0 (10 passed)
  - `uv run --locked pytest tests/test_exclusion_receipt.py`: exit code 0 (22 passed)
  - `uv run --locked python -c "import sys, xlm.data.dedup, xlm.data.pools, xlm.data.exclusion; ..."`: exit code 0 (`torch imported: False`)
  - `uv run --locked python -m xlm.cli.main data dedup --input data/clean/p09_fixture/documents.jsonl --output-dir data/dedup/p10_fixture`: exit code 0 (48 → 14 survivors, 8 clusters)
  - `uv run --locked python -m xlm.cli.main data split --input data/dedup/p10_fixture/documents.jsonl --dedup-report ... --output-dir data/splits/p10_fixture`: exit code 0 (pool `pool_f1f70540613de0453418` frozen)
  - Negative controls: group-safe splits disabled → 2 tests failed; survivor selection made arrival-dependent → 1 test failed after a coverage gap was closed. Both restored and green.
  - `uv.lock` / `pyproject.toml`: unchanged (no dependency added)
- Artifacts: `data/dedup/p10_fixture/` (`documents.jsonl`, `dedup_report.json`);
  `data/splits/p10_fixture/` (`documents.jsonl`, `split_assignment.json`, `pool_freeze.json`)
- Limitations recorded: no labeled duplicate audit sample, so no near-duplicate
  precision or recall is claimed; 64-bit shingle hashes are not collision-free and the
  risk is surfaced rather than denied; paraphrased benchmark material is provably not
  detected (asserted directly in a test); the receipt interface provides integrity and
  issuer authenticity, not OS isolation — the environment remains nonsealed; all
  benchmark examples are authored synthetic fixtures and no protected final data was
  accessed.
- Evidence report: `docs/implementation/reports/P10.md`
- Next milestone: P11 (`prompts/11_pool_freeze_tokenizer_regime.md`)

### P11 — Reusable corpus pools and frozen tokenizer regime
- Status: VERIFIED (demo/pilot scope; the production 32,768 tokenizer fit is planned but NOT RUN)
- Files (new):
  - `src/xlm/data/pools/views.py`
  - `src/xlm/data/pools/manifest.py`
  - `src/xlm/data/pools/builder.py`
  - `src/xlm/data/pools/tokenizer_fit.py`
  - `src/xlm/data/pools/regime.py`
  - `fixtures/pools/views.yaml`
  - `fixtures/pools/binding.yaml`
  - `tests/test_pool_views.py`
  - `tests/test_pool_freeze_regime.py`
  - `tests/test_tokenizer_regime.py`
  - `tests/test_cli_pool_freeze.py`
- Files (modified):
  - `src/xlm/data/pools/__init__.py` (exports)
  - `src/xlm/cli/data_cmd.py` (added `xlm data pool build|inspect|verify` and `xlm data freeze`)
- Test commands and exit statuses:
  - `uv run --locked ruff check src/ tests/`: exit code 0 (all checks passed)
  - `uv run --locked ruff format --check src/ tests/`: exit code 0 (163 files formatted)
  - `uv run --locked mypy src/ tests/`: exit code 0 (166 source files, strict)
  - `uv run --locked pytest tests/ -m "not network and not cuda and not operator"`: exit code 0 (430 passed, 3 deselected in 70.32s; 317 before this milestone)
  - `uv run --locked pytest tests/test_pool_views.py`: exit code 0 (14 passed)
  - `uv run --locked pytest tests/test_pool_freeze_regime.py`: exit code 0 (43 passed)
  - `uv run --locked pytest tests/test_tokenizer_regime.py`: exit code 0 (48 passed)
  - `uv run --locked pytest tests/test_cli_pool_freeze.py`: exit code 0 (8 passed)
  - `xlm data pool build --input data/splits/p10_fixture/documents.jsonl --views fixtures/pools/views.yaml --binding fixtures/pools/binding.yaml --split-assignment data/splits/p10_fixture/split_assignment.json --output-dir data/pools/p11_fixture --budget-tokens 200000`: exit code 0 (`pool_ad074f006d26f4c7cea1`, DEMO_PILOT, 14 docs)
  - `xlm data pool inspect --manifest data/pools/p11_fixture/pool_manifest.json`: exit code 0 (2 views over 1 distinct family)
  - `xlm data pool verify --manifest ... --input ...`: exit code 0 (membership and content digests re-derived offline)
  - `xlm data freeze ... --vocab-size 32768 --fit-sample-bytes 2000 --plan-only`: exit code 0 (resource plan emitted; no fit executed)
  - `xlm data freeze ... --vocab-size 512 --fit`: exit code 0 (468-token tokenizer, `regime_6a7d4beeb5f5ece341aa`, no mixture bound, quick subset 2 of 3 diagnostic docs)
  - Negative controls: pool identity stripped of its policy bindings → 3 tests failed; exclusive assignment ignored → 2 tests failed. Both restored and green.
  - `uv.lock` / `pyproject.toml`: unchanged (no dependency added)
- Artifacts: `data/pools/p11_fixture/` (`documents.jsonl`, `pool_manifest.json`);
  `data/regime/p11_plan/` (plan-only); `data/regime/p11_fixture/`
  (`tokenizer_fit_manifest.json`, `tokenizer_fit_resource_plan.json`, `tokenizer/`, `research_regime.json`)
- Limitations recorded: the production 32,768 BPE fit was NOT RUN — the pilot pool
  holds 1,400 distinct training bytes, the executed fit used vocab 512 (468 actual
  tokens) and reports `is_production_baseline = False`; `ESTIMATED_BYTES_PER_TOKEN`
  is a planning constant, so every sufficiency figure is an estimate until the
  tokenizer is frozen in P12; the resource plan's peak-memory figure is an estimate,
  not a benchmark; `PRODUCTION_MIN_TRAIN_BYTES` is a project policy threshold, not an
  external standard; overlap detection is structural and cannot detect semantically
  duplicated upstream text.
- Evidence report: `docs/implementation/reports/P11.md`
- Next milestone: P12 (`prompts/12_token_shards_mixture_packing.md`)

### P12 — Production token shards, mixture scheduling and packing
- Status: VERIFIED (fixture/pilot scale; no production-scale corpus run). Audited and
  completed in a follow-up session: matched-canonical-byte/document exposure plans
  (required by the prompt and C07) were missing and have been implemented.
- Files (new):
  - `src/xlm/data/sampling/__init__.py`
  - `src/xlm/data/sampling/mixture.py`
  - `src/xlm/data/sampling/plan.py` (exposure plans + matched-byte/document plans)
  - `src/xlm/data/sampling/scheduler.py`
  - `src/xlm/data/sampling/packing.py`
  - `src/xlm/data/sampling/stream.py`
  - `src/xlm/cli/mixture_cmd.py`
  - `fixtures/mixture/documents.jsonl`
  - `fixtures/mixture/mix01.yaml`
  - `tests/test_mixture_planning.py`
  - `tests/test_packing_scheduling.py`
  - `tests/test_mixture_stream.py`
  - `tests/test_trainer_mixture.py`
- Files (modified):
  - `src/xlm/data/tokens.py` (extended the existing shard format: byte spans, lineage IDs, valid-target counts, BOS/EOS markers, counters sidecar, mmap and streaming reads)
  - `src/xlm/training/data.py` (added `BatcherProtocol`)
  - `src/xlm/training/trainer.py`, `src/xlm/training/checkpoint.py` (typed against the protocol)
  - `src/xlm/cli/data_cmd.py` (added `xlm data tokenize`), `src/xlm/cli/main.py` (mixture app)
- Test commands and exit statuses:
  - `uv run --locked ruff check src/ tests/`: exit code 0 (all checks passed)
  - `uv run --locked ruff format --check src/ tests/`: exit code 0
  - `uv run --locked mypy src/ tests/`: exit code 0 (177 source files, strict)
  - `uv run --locked pytest tests/ -m "not network and not cuda and not operator"`: exit code 0 (512 passed, 3 deselected in 74.77s; 430 before this milestone, 506 at first pass)
  - `uv run --locked pytest tests/test_mixture_planning.py`: exit code 0 (31 passed)
  - `uv run --locked pytest tests/test_packing_scheduling.py`: exit code 0 (23 passed)
  - `uv run --locked pytest tests/test_mixture_stream.py`: exit code 0 (22 passed)
  - `uv run --locked pytest tests/test_trainer_mixture.py`: exit code 0 (6 passed; the real Trainer driving the mixture loader)
  - `xlm mixture matched-plan --recipe fixtures/mixture/mix01.yaml --shards data/shards/p12_mix --basis canonical_bytes --budget 50000 --output data/shards/p12_mix/matched_mix01.json`: exit code 0 (planned bytes pinned to the budget; derived token budget reported separately; repetition warned)
  - `uv run --locked python -c "import sys, xlm.data.sampling; ..."`: exit code 0 (`torch imported: False`)
  - `uv run --locked python -m xlm.cli.main demo`: exit code 0 (offline vertical slice re-run, 200 valid targets, loss 5.5695 -> 4.5018)
  - `xlm data tokenize --input fixtures/mixture/documents.jsonl --tokenizer data/regime/p11_fixture/tokenizer --output-dir data/shards/p12_mix`: exit code 0 (3 shards, 6,592 valid targets, uint16, coverage 1.0000)
  - `xlm mixture validate|plan|preview|inspect`: exit code 0 (plan `plan_013dc0cf44ed71560c65`; preview drift 0.00000 against a 0.02000 bound; repetition shortfall warned at plan time)
  - Negative controls: dropping the final target from every window → 5 tests failed; exhaustion silently repeating instead of raising → 2 tests failed. Both restored and green.
  - `uv.lock` / `pyproject.toml`: unchanged (no dependency added)
- Defects found and fixed during implementation: a `memoryview` slice that kept an
  exported pointer and blocked mmap close; an epoch-advance guard that missed the
  carried-context case and produced a one-token window at a source's end.
- Artifacts: `data/shards/p12_mix/` (3 shard directories + `plan_mix01.json`),
  `data/shards/p12_fixture/`
- Audit note: matched-canonical-byte/document exposure plans were required by the
  prompt and C07 but were deferred in the first pass; they are now implemented
  (`compile_matched_plan`, `xlm mixture matched-plan`) with planning and real-shard
  byte-vs-BPE invariance tests. See report §8a.
- Limitations recorded: fixture scale only, no throughput or peak-RSS measurement;
  share drift is granularity-bounded on short runs and the tests assert the
  granularity relationship rather than an unreachable steady-state bound; multiworker
  prefetch is not implemented, so the "worker-count" acceptance item is covered as
  partition/microbatch-count independence; matched plans are planning-side only and
  are not yet bound to a run plan; `PackingPolicy.max_document_tokens` is identity-bound
  but not separately enforced beyond the context-window cap.
- Evidence report: `docs/implementation/reports/P12.md`
- Next milestone: P13 (`prompts/13_real_dataset_views_and_mix01.md`)

### P13 — Real dataset views and mix01
- Status: VERIFIED (definition/gating scope; live corpus NOT RUN, mixture BLOCKED pending admission)
- Files (new):
  - `recipes/mixtures/mix01_views.yaml` (13-view registry with observed revisions/configs)
  - `src/xlm/data/sources/mix01.py` (registry, exact preset validation, treatment diffs, run gating, pilot envelope)
  - `src/xlm/data/adapters/mix01_adapters.py` (11 per-family adapters with fail-closed contracts)
  - `fixtures/mixture/views/` (11 schema fixtures + README, synthetic and labelled)
  - `tests/test_mix01_views.py` (32 tests)
- Files (modified):
  - `src/xlm/cli/mixture_cmd.py` (`preset-validate`, `preset-diff`)
  - `src/xlm/cli/data_cmd.py` (`mix01-status` with `--preset` gating)
- Test commands and exit statuses:
  - `uv run --locked ruff check src/ tests/`: exit code 0 (all checks passed)
  - `uv run --locked ruff format --check src/ tests/`: exit code 0 (177 files clean)
  - `uv run --locked mypy src/ tests/`: exit code 0 (180 source files, strict)
  - `uv run --locked pytest tests/ -m "not network and not cuda and not operator"`: exit code 0 (544 passed, 3 deselected in 75.45s; 512 before this milestone)
  - `uv run --locked pytest tests/test_mix01_views.py`: exit code 0 (32 passed)
  - `xlm mixture preset-validate --preset recipes/mixtures/mix01.yaml`: exit code 0 (exact sum 1, 12 views, identity `d670bd3a…`)
  - `xlm mixture preset-diff --base .../mix01.yaml --variant .../m4_txt360_web.yaml`: exit code 0 (−0.15/−0.05 Nemotron, +0.20 txt360_web, 10 unchanged)
  - `xlm data mix01-status`: exit code 0 (0/13 ready, all NOT LIVE-VERIFIED with reasons)
  - `xlm data mix01-status --preset recipes/mixtures/mix01.yaml`: exit code 1 (blocked by 12 components, no fallback — expected)
  - Negative controls: neutered no-fallback validator → 1 test failed; substring organic matching → 1 test failed. Both restored and green.
  - Bounded live metadata discovery (unauthenticated, allowlisted host only): 28 requests / ~2.7 MiB total; real revisions for 9 of 10 repos; Nemotron-CC confirmed GATED; exact config names recorded (`High-Quality`, `eng_Latn`, `web-high-medium`, `Nemotron-Pretraining-Wiki-Rewrite`, `general`+`planning`, `en`, `default`).
  - `uv.lock` / `pyproject.toml`: unchanged (no dependency added)
- Artifacts: `data/probe/p13_mix01_snapshot.json`, `data/probe/p13_mix01_configs.json`, `data/probe/p13_ifm_behaviors_configs.json`
- Limitations recorded: 0 of 13 views READY (no admission, no reviews, no live-tested adapter); Essential-Web revision unresolved; Nemotron-CC gated; Common Pile license unresolved; adapter field names expected-not-verified; pilot NOT RUN (no approved sources, no authorization envelope issued); M4 TxT360 gated on its view's verification/admission.
- Evidence report: `docs/implementation/reports/P13.md`
- Next milestone: P14 (`prompts/14_cuda_profile_and_performance.md`)

### P14 — Single-4090 execution and correctness-preserving performance
- Status: VERIFIED (real-GPU scope; compile/eager parity NOT RUN — no codegen toolchain)
- Files (new):
  - `src/xlm/models/backends.py` (backend policy, SDPA kernel probing, compile helpers/probe, precision validation)
  - `src/xlm/training/profile.py` (preflight, isolated calibration, resource plans, freeze)
  - `src/xlm/training/profile_worker.py` (per-size worker process)
  - `src/xlm/training/recovery.py` (OOM restart/fork decisions)
  - `src/xlm/cli/profile_cmd.py` (`xlm profile`)
  - `tests/test_cuda_execution.py` (12 CPU-safe + 9 CUDA tests)
- Files (modified):
  - `src/xlm/models/transformer.py` (opt-in activation checkpointing)
  - `src/xlm/training/trainer.py` (precision validation, checkpointing/compile wiring, scaler skip-and-backoff, execution report)
  - `src/xlm/training/checkpoint.py` (scaler persistence, precision metadata, fork-aware restore)
  - `src/xlm/cli/train_cmd.py` (precision/backend/compile/checkpointing options, FP16 resume)
  - `src/xlm/cli/doctor.py` (BF16, probed SDPA kernels, wheel/drive explanations, corrected capabilities)
  - `src/xlm/cli/main.py` (`profile` command)
  - `tests/test_doctor.py` (7-tuple inspection)
- Test commands and exit statuses:
  - `uv sync --locked --extra cuda`: exit code 0 (`torch==2.14.0+cu126`)
  - `uv run --locked --extra cuda pytest tests/test_cuda_execution.py tests/test_cuda.py -m cuda`: exit code 0 (9 passed, 1 skipped — CUDA compile parity skipped: no Triton in wheel)
  - `xlm profile --config recipes/models/50m.yaml ...`: exit code 0 (32,768 tok/s @ mb8, 3.90 GiB reserved, ckpt 0.56 GiB)
  - `xlm profile --config recipes/models/150m.yaml ...`: exit code 0 (13,430 tok/s @ mb8, 7.30 GiB reserved, ckpt 1.68 GiB)
  - `xlm profile --config recipes/models/300m.yaml ...`: exit code 0 (3,049 tok/s @ mb8, 11.38 GiB reserved, ckpt 3.35 GiB)
  - `xlm doctor` (cuda env): exit code 0 (4090 sm_89, BF16 yes, SDPA flash=no/mem_efficient=yes/math=yes)
  - `uv sync --locked --extra cpu`: exit code 0 (restored `2.14.0+cpu`)
  - `uv run --locked ruff check src/ tests/`: exit code 0 (all checks passed)
  - `uv run --locked ruff format --check src/ tests/`: exit code 0 (183 files clean)
  - `uv run --locked mypy src/ tests/`: exit code 0 (186 source files, strict)
  - `uv run --locked pytest tests/ -m "not network and not cuda and not operator"`: exit code 0 (556 passed, 1 skipped, 12 deselected; 544 before this milestone)
  - Negative controls: shared worker checkpoint IDs aliased another model's artifacts (found live, fixed with isolated XLM_HOME + unique IDs, measurements re-run); instant-fail FP16 overflow defeated loss scaling (replaced with bounded skip-and-backoff).
  - `uv.lock` / `pyproject.toml`: unchanged (no dependency added)
- Artifacts: `data/profiles/p14_50m/`, `data/profiles/p14_150m/`, `data/profiles/p14_300m/` (profile.json + profile_freeze.json each)
- Limitations recorded: compile/eager parity NOT RUN (no Triton in Windows CUDA wheel, no C++ compiler for CPU Inductor); no flash attention in this wheel build; no live OOM occurred (recovery unit-tested only); dry runs use synthetic tokens (loader wait ≈ 0, re-measure on production loader); ETA ranges are wide measurement intervals; matched-run freezes written but no matched runs executed.
- Evidence report: `docs/implementation/reports/P14.md`
- Next milestone: P15 (`prompts/15_official_evaluation_harness.md`)

### P15 — Pinned official evaluator and tiered benchmark protocols
- Status: VERIFIED (offline fixture evidence; one bounded live search smoke; full
  official search/confirmation NOT RUN, final operator-blocked by design)
- Files (new):
  - `src/xlm/evaluation/harness.py` (version pin, identity, pinned task materialization, registry)
  - `src/xlm/evaluation/harness_lm.py` (LM subclass registered via the public registry)
  - `src/xlm/evaluation/harness_runner.py` (bounded suite execution + evidence assembly)
  - `src/xlm/evaluation/suites.py` (tiers, variants, firewall, partitions, four-task index, final request)
  - `src/xlm/evaluation/evidence.py` (per-item records, identity-checked cache)
  - `src/xlm/evaluation/reference.py` (comparator registration, bounded download plan)
  - `fixtures/eval/tasks/` (authored synthetic fixture tasks + data)
  - `tests/test_eval_suites.py` (29), `tests/test_harness_adapter.py` (14)
- Files (modified):
  - `src/xlm/cli/eval_cmd.py` (`--suite search|confirmation|final`, tasks/include-path/limit/request-only/final-authorization)
  - `pyproject.toml`, `uv.lock` (`eval` extra: `lm-eval==0.4.13`; mypy overrides)
- Test commands and exit statuses:
  - `uv add --optional eval "lm-eval==0.4.13"`: exit code 0 (lock updated)
  - `uv sync --locked --extra cpu --extra eval`: exit code 0
  - `uv run --locked --extra cpu --extra eval pytest tests/test_eval_suites.py tests/test_harness_adapter.py`: exit code 0 (43 passed in 172s)
  - `uv run --locked --extra cpu --extra eval pytest tests/ -m "not network and not cuda and not operator"`: exit code 0 (599 passed, 1 skipped, 12 deselected in 259s; 556 before)
  - `uv run --locked --extra cpu --extra eval ruff check src/ tests/`: exit code 0
  - `uv run --locked --extra cpu --extra eval ruff format --check src/ tests/`: exit code 0 (191 files)
  - `uv run --locked --extra cpu --extra eval mypy src/ tests/`: exit code 0 (194 source files, strict)
  - `xlm evaluate … --suite search --tasks xlm_fixture_mc --include-path fixtures/eval/tasks --limit 3`: exit code 0 (acc 0.6667 / acc_norm 0.3333; index withheld with notes)
  - `xlm evaluate … --suite final --request-only`: exit code 0 (frozen request written)
  - `xlm evaluate … --suite final` without authorization: exit code 1 (split firewall)
  - Bounded live smoke (ARC-Easy train pinned revision, limit 10, scripted): exit code 0 (limited smoke acc 0.4000 / acc_norm 0.2000; index withheld)
  - Lazy import check: `lm_eval` not imported at CLI startup even when installed
  - Negative controls: evidence keyed by wrapper name hid `arc_easy` from the index (found in live smoke, fixed to logical task names, re-run); final suite without authorization now refused with a test.
- Artifacts: `manifests/eval_dataset_pins.yaml`; `fixtures/eval/tasks/`; `data/eval/p15_smoke/`, `data/eval/p15_live_smoke/`
- Limitations recorded: full official suites NOT RUN (only a 10-item labeled smoke); final execution is P21's operator path; no model downloads; parity evidence is fixture-exact plus the bounded live slice; BLiMP partition sizes are a policy target, not a proven balance.
- Evidence report: `docs/implementation/reports/P15.md`
- Next milestone: P16 (`prompts/16_experiment_plans_and_queue.md`)

### P16 — Experiment plans, bounded local queue and mixture search
- Status: VERIFIED (offline definition/gating scope; billion-token trials are plans only)
- Files (new):
  - `src/xlm/experiments/__init__.py`
  - `src/xlm/experiments/snapshot.py` (immutable captures, secret fail-closed, verify)
  - `src/xlm/experiments/authorization.py` (hash-bound tickets, C13 smoke caps)
  - `src/xlm/experiments/plans.py` (draft resolution, blockers, horizon tagging/forks)
  - `src/xlm/experiments/sweeps.py` (lists, grids, random/simplex proposals, caps)
  - `src/xlm/experiments/campaigns.py` (expansion, selections, budget gates)
  - `src/xlm/experiments/queue.py` (jobs/attempts tables, leases, runner, recovery)
  - `src/xlm/cli/experiment_cmd.py` (`experiment`, `queue`, `campaign` commands)
  - `tests/test_experiment_plans.py` (19), `tests/test_queue.py` (18)
- Files (modified):
  - `src/xlm/cli/main.py` (command registration)
  - `src/xlm/cli/train_cmd.py` (non-fork resume past schedule horizon is refused)
- Test commands and exit statuses:
  - `uv run --locked ruff check src/ tests/`: exit code 0 (all checks passed)
  - `uv run --locked ruff format --check src/ tests/`: exit code 0 (201 files clean)
  - `uv run --locked mypy src/ tests/`: exit code 0 (204 source files, strict)
  - `uv run --locked pytest tests/test_experiment_plans.py tests/test_queue.py`: exit code 0 (37 passed)
  - `uv run --locked pytest tests/ -m "not network and not cuda and not operator"`: exit code 0 (636 passed, 1 skipped, 12 deselected; 599 before)
  - `xlm campaign plan recipes/campaigns/data_search.yaml`: exit code 0 (54 trials, 6 mixtures, 48 blocked on selection, nothing executed)
  - `xlm experiment plan recipes/experiments/baseline_50m.yaml`: exit code 0 (blockers listed)
  - `xlm experiment submit` (blocked plan, then unauthorized large plan): exit code 1 both, with reasons
  - Negative controls: disabled snapshot verification → tamper test failed; disabled plan-hash dedup → no duplicate anyway (job-ID + ledger-PK backstops). Both restored.
  - `uv.lock` / `pyproject.toml`: unchanged (no dependency added)
- Artifacts: plan/snapshot outputs are command-scoped (tmp in tests); no persistent campaign artifacts
- Limitations recorded: billion-token trials are plans, never executions; eval cadence predeclared but queue-executes only checkpoints; lease logic tested without GPU hardware; secret patterns are heuristics; toy runs are synthetic-data CPU runs.
- Evidence report: `docs/implementation/reports/P16.md`
- Next milestone: P17 (`prompts/17_statistics_comparisons_promotion.md`)

### P17 — Fair comparisons, uncertainty and size-promotion gates
- Status: VERIFIED (synthetic-fixture scope; no live runs, no hardware claims)
- Files (new):
  - `src/xlm/comparison/__init__.py`
  - `src/xlm/comparison/tracks.py` (six track schemas, eligibility with readable diffs)
  - `src/xlm/comparison/bootstrap.py` (paired cluster bootstrap, seed spread, suite index)
  - `src/xlm/comparison/curves.py` (interpolation-only crossing, compute-to-target)
  - `src/xlm/comparison/promotion.py` (frozen gates, drafts, factorial/ablation matrices)
  - `src/xlm/cli/compare_cmd.py` (`xlm compare`, `xlm promote`)
  - `tests/test_comparison.py` (34)
- Files (modified):
  - `src/xlm/cli/main.py` (command registration)
- Test commands and exit statuses:
  - `uv run --locked ruff check src/ tests/`: exit code 0 (all checks passed)
  - `uv run --locked ruff format --check src/ tests/`: exit code 0 (208 files clean)
  - `uv run --locked mypy src/ tests/`: exit code 0 (211 source files, strict)
  - `uv run --locked pytest tests/test_comparison.py`: exit code 0 (34 passed)
  - `xlm compare … --track architecture …`: exit code 0 (eligible; index delta +41.667 [+16.667, +66.667], synthetic)
  - `xlm promote …` (1 seed/arm): exit code 1 (seed gate correctly refused)
  - `xlm promote … --to-size 150m` (2 seeds/arm): exit code 0 (schema-valid draft, not_authorized, no final suite)
  - Negative controls: unstratified resampling dropped a task mid-replicate (stratified within tasks); CLI refusal fixture initially miswired (corrected, asserts on `tokenizer_hash`).
  - `uv.lock` / `pyproject.toml`: unchanged (no dependency added)
- Artifacts: `data/compare/p17_demo/` (synthetic evidence, plans, comparison, decision, draft + lineage)
- Limitations recorded: all numbers synthetic; no live comparison ran; cluster maps are caller-supplied (BLiMP refuses without one); search-stage CIs are decision support only, with multiplicity notes.
- Evidence report: `docs/implementation/reports/P17.md`
- Next milestone: P18 (`prompts/18_research_plugins_and_idea_cards.md`)

### P18 — Research idea workflow and safe extensibility
- Status: VERIFIED (synthetic-fixture scope; no breakthrough claims, no live runs)
- Files (new):
  - `src/xlm/research/__init__.py`
  - `src/xlm/research/ideas.py` (versioned cards, statuses, validation incl. novelty-certainty refusal)
  - `src/xlm/research/capabilities.py` (declarations + plan-time combination checks)
  - `src/xlm/research/loader.py` (manifests, discovery, file-scope/protected-surface guards, registration)
  - `src/xlm/research/scaffold.py` (disabled-by-default per-category scaffolds)
  - `src/xlm/plugins/__init__.py` + `noop_architecture/`, `noop_objective/`, `noop_optimizer/`, `noop_tokenizer/` (working nonnovel controls)
  - `src/xlm/cli/research_cmd.py` (`research idea|validate|scaffold|check-plugin`)
  - `tests/test_research.py` (22)
- Files (modified):
  - `src/xlm/cli/main.py` (command registration)
- Test commands and exit statuses:
  - `uv run --locked ruff check src/ tests/`: exit code 0 (all checks passed)
  - `uv run --locked ruff format --check src/ tests/`: exit code 0 (220 files clean)
  - `uv run --locked mypy src/ tests/`: exit code 0 (223 source files, strict)
  - `uv run --locked pytest tests/test_research.py`: exit code 0 (22 passed)
  - `xlm research idea new/validate` (blank): exit 0 then exit 1 with missing fields
  - `xlm research scaffold --category architecture`: exit code 0 (6 files, disabled)
  - `xlm research check-plugin src/xlm/plugins/noop_tokenizer`: exit code 0 (registered, nonnovel)
  - Negative controls: weakened file-scope guard → protected test failed; dropped falsification requirement → missing-field test failed. Both restored.
  - `uv.lock` / `pyproject.toml`: unchanged (no dependency added)
- Artifacts: `data/research/p18_demo/` (blank card + disabled scaffold)
- Limitations recorded: no research claims; prior-art records are not searches; no provider integration; secret patterns are heuristics.
- Evidence report: `docs/implementation/reports/P18.md`
- Next milestone: P19 (`prompts/19_reports_and_dashboard.md`)

### P19 — Research reports and optional local dashboard
- Status: VERIFIED (offline scope; UI screenshot/manual checks NOT RUN headless)
- Files (new):
  - `src/xlm/reports/__init__.py`
  - `src/xlm/reports/collect.py` (run/campaign/dataset collectors, protected stripping)
  - `src/xlm/reports/render.py` (JSON/CSV/Markdown/self-contained HTML, escaping, curves)
  - `src/xlm/dashboard/__init__.py`
  - `src/xlm/dashboard/server.py` (loopback read-only stdlib server, bind refusal)
  - `src/xlm/cli/report_cmd.py` (`report`, `runs list`, `dashboard`)
  - `tests/test_reports.py` (18), `tests/test_dashboard.py` (5)
- Files (modified):
  - `src/xlm/cli/main.py` (command registration)
- Test commands and exit statuses:
  - `uv run --locked ruff check src/ tests/`: exit code 0 (all checks passed)
  - `uv run --locked ruff format --check src/ tests/`: exit code 0 (228 files clean)
  - `uv run --locked mypy src/ tests/`: exit code 0 (231 source files, strict)
  - `uv run --locked pytest tests/test_reports.py tests/test_dashboard.py`: exit code 0 (23 passed)
  - Toy campaign via P16 queue (2 CPU jobs): exit code 0 (both SUCCEEDED)
  - `xlm report` (md/html) from campaign records: exit code 0 (readable, gaps listed)
  - `xlm runs list`: exit code 0 (ledger states reproduced)
  - Negative controls: escaping disabled → injection test failed; missing-as-zero → n/a test failed. Both restored.
  - `uv.lock` / `pyproject.toml`: unchanged (no dependency added)
- Artifacts: `data/reports/p19_demo/` (ledger, run records, run + campaign reports)
- Limitations recorded: UI screenshots/manual checks NOT RUN (headless); no sealed store to audit stripping against; curves need recorded history; unmeasured compute stays missing.
- Evidence report: `docs/implementation/reports/P19.md`
- Next milestone: P20 (`prompts/20_export_generation_portability.md`)

### P20 — Model export, reproducible loading and inference
- Status: VERIFIED (offline scope; HF runtime parity NOT RUN — no transformers install)
- Files (new):
  - `src/xlm/export/__init__.py`
  - `src/xlm/export/manifest.py` (hashes, provenance, accounting, compat rules)
  - `src/xlm/export/writer.py` (safe weights, tokenizer, secrets refusal, head exclusion)
  - `src/xlm/export/loader.py` (hash-first verify, tied restore, dtype/device rules)
  - `src/xlm/export/hf.py` (baseline-only Llama-layout mapping with exactness proof)
  - `src/xlm/inference/session.py` (bounded replayable completion sessions)
  - `src/xlm/cli/export_cmd.py` (`xlm export`)
  - `tests/test_export.py` (19)
- Files (modified):
  - `src/xlm/models/serialization.py` (safetensors fallback for export bundles)
  - `src/xlm/cli/generate_cmd.py` (`generate-session` command)
  - `src/xlm/cli/main.py` (command registration)
  - `pyproject.toml`, `uv.lock` (`safetensors==0.8.0` pinned — required by acceptance)
- Test commands and exit statuses:
  - `uv add safetensors`: exit code 0 (`safetensors==0.8.0`)
  - `uv run --locked ruff check src/ tests/`: exit code 0 (all checks passed)
  - `uv run --locked ruff format --check src/ tests/`: exit code 0 (236 files clean)
  - `uv run --locked mypy src/ tests/`: exit code 0 (239 source files, strict)
  - `uv run --locked pytest tests/test_export.py`: exit code 0 (19 passed)
  - `xlm export …`: exit code 0 (98,880-param bundle, optimizer excluded)
  - `xlm generate-session …` / `xlm generate <bundle>`: exit code 0 (transcript saved; safetensors read directly)
  - Negative controls: hash check disabled → corruption test failed; tied check bypassed → exposed a missing diverged-tied test, added it, bypass then failed it. Both restored.
- Artifacts: `data/export/p20_demo_ckpt/`, `data/export/p20_demo_bundle/`, `data/export/p20_demo_session.json`
- Limitations recorded: HF runtime parity NOT RUN; manifest is the trust root (signing is P21); no cache exists (labeled); nothing published anywhere.
- Evidence report: `docs/implementation/reports/P20.md`
- Next milestone: P21 (`prompts/21_isolation_security_release.md`)

### P21 — Protected final evaluation, authorization and release auditing
- Status: VERIFIED (mechanism scope; two-identity deployment NOT RUN; one real cache finding remediated)
- Files (new):
  - `src/xlm/operator/__init__.py`
  - `src/xlm/operator/sealed.py` (fail-closed sealed-readiness checks)
  - `src/xlm/operator/final.py` (requests, receipts, auth/revocation, quotas, reviewed-bundle boundary, access log, decontamination receipts)
  - `src/xlm/operator/release.py` (eleven-check release audit, final-data cache scan, confirmed purge)
  - `src/xlm/cli/final_cmd.py` (`final request|execute|verify-receipt`, `release audit`)
  - `tests/test_operator.py` (22)
- Files (modified):
  - `src/xlm/cli/main.py` (command registration)
- Test commands and exit statuses:
  - `uv run --locked ruff check src/ tests/`: exit code 0 (all checks passed)
  - `uv run --locked ruff format --check src/ tests/`: exit code 0 (242 files clean)
  - `uv run --locked mypy src/ tests/`: exit code 0 (245 source files, strict)
  - `uv run --locked pytest tests/test_operator.py`: exit code 0 (22 passed)
  - `xlm final request …`: exit code 0 (frozen request + hash, nothing scored)
  - `xlm release audit data/release/p21_demo`: exit code 0 (RELEASE, 11/11 pass)
  - `verify_no_final_examples()` on real caches: exit 1→0 (found 2 ARC-Easy test files from the P15 smoke → purged with confirmation → clean)
  - Negative controls: sealed writability ignored → writable test failed; aggregates boundary weakened → forbidden-supplier test failed. Both restored.
  - `uv.lock` / `pyproject.toml`: unchanged (no dependency added)
- Artifacts: `data/operator/p21_demo_request.json`, `data/release/p21_demo/`
- Limitations recorded: no protected deployment here (NOT RUN); subprocess is not a security boundary (documented); cache absence is not a non-read proof; no keys or final data in the repo.
- Evidence report: `docs/implementation/reports/P21.md`
- Next milestone: P22 (`prompts/22_campaign_bootstrap_and_runbooks.md`)

### P22 — Complete user workflows and production campaign preparation
- Status: VERIFIED (offline scope; large campaigns prepared, never launched)
- Files (new):
  - `src/xlm/prepare/__init__.py`
  - `src/xlm/prepare/config.py` (strict stage/config schemas, --define overrides)
  - `src/xlm/prepare/planner.py` (dry planning, staleness, downstream invalidation)
  - `src/xlm/prepare/runner.py` (authorized execution, reuse/resume/force, state)
  - `src/xlm/cli/prepare_cmd.py` (`prepare`, `maintenance cleanup`)
  - `recipes/prepare/offline_toy.yaml`, `recipes/prepare/toy_mixture.yaml`
  - `recipes/experiments/tiny_demo.yaml`
  - `recipes/comparisons/` (5 track-checked comparison drafts)
  - `recipes/campaigns/confirm_multiseed.yaml`, `factorial_demo.yaml`, `staged_50m_150m_300m.yaml`
  - `src/xlm/comparison/recipes.py` (comparison_recipe validation)
  - `docs/runbooks/windows.md`, `docs/runbooks/linux.md`
  - `tests/test_prepare.py` (13), `tests/test_recipes.py` (9), `tests/test_offline_workflow.py` (4)
- Files (modified):
  - `src/xlm/cli/config_cmd.py` (comparison/campaign/prepare/registry kinds; clean error paths)
  - `src/xlm/cli/main.py` (command registration)
  - `recipes/README.md` (examples rewritten to the real CLI)
- Test commands and exit statuses:
  - `uv run --locked ruff check src/ tests/`: exit code 0 (all checks passed)
  - `uv run --locked ruff format --check src/ tests/`: exit code 0 (251 files clean)
  - `uv run --locked mypy src/ tests/`: exit code 0 (254 source files, strict)
  - `uv run --locked pytest tests/test_recipes.py tests/test_prepare.py tests/test_offline_workflow.py`: exit code 0 (27 passed: 9 + 14 + 4)
  - `xlm prepare --config recipes/prepare/offline_toy.yaml --plan-only`: exit code 0 (10 stages, nothing executed)
  - `xlm prepare … --authorize` (isolated home): exit code 0 (all stages succeeded); repeat: exit code 0 (all reused)
  - `xlm campaign plan recipes/campaigns/staged_50m_150m_300m.yaml`: exit code 0 (60 trials, nothing executed)
  - Negative controls: neutered executor → reuse test failed; ignored invalidations → stale test failed. Both restored.
  - `uv.lock` / `pyproject.toml`: unchanged (no dependency added)
- Artifacts: `data/prepare/p22_home/` (10-stage offline run + state)
- Limitations recorded: large campaigns are plans only; pilot/live transfers gated, not exercised; cost without measured profiles is unknown; Linux runbook mirrors executed Windows behavior.
- Evidence report: `docs/implementation/reports/P22.md`
- Historical next at P22 completion: P23 (now audited; remediation gates are in the P23 section).

### P23 — Independent acceptance audit

- Status: VERIFIED audit delivery; production acceptance BLOCKED.
- Report: [reports/P23.md](reports/P23.md).
- Requirement map, supported combinations, defects and next prompt: [FINAL_ACCEPTANCE.md](FINAL_ACCEPTANCE.md).
- Registered command trace: [CLI_AUDIT.md](CLI_AUDIT.md).
- Actual command/resource/source-hash evidence: `docs/implementation/evidence/P23/`.
- Offline baseline: 783 passed, 1 skipped, 12 deselected. Final offline suite: **884 passed, 1 skipped, 12 deselected**; exit 0. Final lint/format, mypy (187 modules), demo, doctor and wheel build passed. The P23 report preserves intermediate failures and the formatting-only correction.
- Separate real CUDA: 9 passed, 1 skipped; tiny profile measured. Live source: pinned metadata only, zero corpus rows.
- Next: remediate D01–D08 and rerun P23; do not launch a production campaign or protected final evaluation.

### P23 remediation Stage 1 — D01

- Implementation: IMPLEMENTED; bounded offline verification: VERIFIED.
- 99 focused checks and 92 caller checks passed; one caller test deselected, no skips.
- Formatting/lint passed; mypy passed for 187 source files. Actual imports are from
  `D:\Project\xlm\src`, with a fresh cache-backed offline uv CPU/evaluation environment.
- Before evidence preserved: 5 failures and 1 identical-reuse pass. Final evidence,
  compatibility limits and defect/code/test/result map: [P23-D01 report](reports/P23-D01.md).
- Legacy schema-v1 originals remain checksum-only; authentic executed provenance
  remains D06. No trainer, evaluator, queue or frozen contract was modified in D01.
- At Stage 1 completion, D06 awaited separate approval; its current status is below.
- Final complete offline rerun: NOT RUN, scheduled after all approved stages.
  Overall production acceptance remains BLOCKED; external validation remains operator work.

### P23 remediation Stage 2 — D06

- Implementation and bounded offline verification approved on 2026-09-20.
- Implementation: IMPLEMENTED; bounded Windows CPU offline verification: VERIFIED.
- 53 core, 27 caller, 83 related regression and 99 D01 checks passed (262 total;
  no skips). Format/lint passed; mypy passed for 194 source files. All final groups
  record unchanged source hashes and imports from the repaired checkout/captures.
- Captured execution, actual environment fingerprints, checkpoint/evaluation
  provenance and fresh-process recovery use the existing infrastructure.
- Original before evidence is preserved: 3 failed, 1 positive control passed.
- Detailed scope and results: [P23-D06 report](reports/P23-D06.md).
- At this Stage 2 boundary, [D03's plan/reproduction](evidence/P23-D03/PLAN.md)
  awaited approval (2 failed, 1 positive control passed). The later core approval
  and current scope are recorded below.
- Overall production acceptance remains BLOCKED; the complete offline rerun
  remains deferred.

### P23 remediation Stage 3 — D03 core

- Approved core scope: actual configurable inputs/components, source scheduling,
  packing/target accounting and complete checkpoint restoration through the
  existing direct/queue/resume paths. Implementation: IMPLEMENTED; bounded
  Windows CPU offline verification: VERIFIED.
- 406 test executions / 400 distinct cases passed, zero skips. Format/lint passed;
  mypy passed for 195 source files. Product and executed-test hashes verified;
  documented unrelated test-only edits were separately rerun in `after05`.
- Report and defect/code/test/result map: [P23-D03](reports/P23-D03.md).
- Operator prerequisites and actual uv CLI commands: [bounded baseline pilot](D03_BASELINE_PILOT.md).
- D03 matched-document/byte execution across tokenizers: OPEN / DEFERRED to the
  tokenizer-research phase, not VERIFIED.
- D02 remains unimplemented. Its [focused plan and offline reproduction](evidence/P23-D02/PLAN.md)
  are ready for separate approval: 4 failures / 1 positive control and 2 explicit
  refusal failures. No live acquisition or real-corpus preparation occurred.
- Full-size public planning still lacks measured profile lookup integration;
  smoke/production guards remain. External pilot/profile/evaluation work is NOT RUN.
- Final complete platform rerun: NOT RUN. Overall production acceptance: BLOCKED.
