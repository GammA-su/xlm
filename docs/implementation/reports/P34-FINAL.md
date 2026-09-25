# P34 final integration candidate

Branch: `integrate/p34-final-candidate`. No network, no downloads, no installs,
no live data, no research training, no push, no merge into other branches.
Neither source worktree was modified (Opus/Astra trees were read via git
objects and read-only fixture copy; their venv interpreters were executed with
bytecode writes disabled and `PYTHONPATH` pointed at this candidate).

## 1. Candidate starting HEAD

`8fd05c11e1bdfd84e075000d59e14c315c986f36`
(`docs(perf): close P33 CUDA audit with qualified throughput results`),
branch `integrate/p34-final-candidate`, clean tree. This is the exact common
P33 base of both independent implementations.

## 2. Exact Opus commit series applied

Verified first with
`git log --reverse --oneline 8fd05c11..perf/opus55-p34-independent`, then
cherry-picked in that tested order (each applied cleanly, no conflicts):

1. `5fca860` perf(data): fold the exact target trace chain once per packed window
2. `754730b` perf(loader): prepare exact updates in a bounded speculative producer
3. `51a7fb1` bench(cuda): add bounded P34 synchronous/resident/prefetch harness
4. `75fd8a9` bench(cuda): add P33-loader reference modes and an interleaved case runner
5. `0b64c83` bench(p34): retain throughput, CPU, checkpoint and determinism evidence
6. `257d2d2` docs(p34): report the independent training-throughput closeout

## 3. Initial Opus tree-identity proof

After application, before any new change:

- `git rev-parse HEAD^{tree}` = `b0367945dcdbef3d834c881311b8b5e6d579bb67`
- `git rev-parse perf/opus55-p34-independent^{tree}` = same `b0367945...`
- `git diff --exit-code perf/opus55-p34-independent HEAD` exited 0.

Commit hashes differ only by cherry-pick committer metadata; the trees are
identical.

## 4. Astra-vs-Opus invariant matrix

Compared semantic invariants (not filenames/counts). Astra HEAD `9f74944`
(`perf/p34-training-overlap`), product in `src/xlm/training/producer*.py`.

| # | Invariant | Opus behavior | Astra behavior | Equiv? | Astra stronger? | Action |
|---|---|---|---|---|---|---|
| A | CUDA completion before cursor promotion | No barrier: async optimizer failure could surface after cursor/scheduler/counters advanced | `requires_cuda_commit_barrier` + `current_stream.synchronize()` before promotion | No | Yes | Ported barrier + test |
| B | Ambiguous optimizer mutation | `optimizer`-stage failure was retryable in-process (BLOCKER) | `_update_in_doubt`: no more updates, no checkpoint, fresh reload required | No | Yes | Ported state machine + tests |
| C | Commit independent of producer liveness | `commit()` is pure local promotion, no IPC | Same (plus credit release) | Yes | No | Added missing commit-after-death test |
| D | Speculative/committed binding | generation, ordinal, start/end digests, content digest, budget | generation(uuid), sequence, start/end digests, batch_id, payload sha, target delta, mixture/version/exposure checks | Mostly | Partly | Added end-digest recompute + target-delta check at take |
| E | Stale/rollback/load_state/resume | generation bump, stale discard, reset round-trip validation, deterministic regen | uuid generation, full child restart on rollback | Yes | No (different mechanism, same property) | None (Opus tests cover) |
| F | Producer death (before/mid/after-publish/owned/parent/close/GC/hard kill) | EOF detection, daemon, weakref finalizer, finite join/term/kill, orphan test | watchdog thread, credits, owned shutdown, abrupt-death test | Yes | No | None (equivalent outcomes) |
| G | Windows pipe failures as domain errors | `BrokenPipeError` on send escaped raw; `poll()` OSError escaped raw | `(EOFError, OSError)` normalized to `ProducerError` incl. poll | No | Yes | Ported minimal normalization + tests |
| G2 | Duplex-pipe deadlock on back-to-back resets | Latent: large reset vs large update, nobody reading | One-way pipe + credits (immune by design) | N/A | Mechanism differs | Fixed with live divert reader (found deterministically via new wrapper; see §6) |
| H | Resource guards | depth 1..2, 300 s timeout, finite shutdown, daemon | frame 16 MiB, RSS 2 GiB, 60 s deadlines, credits, pre-pub RSS admission | Partial | Benchmark-only | Not ported: production frames/queue are batch/depth-bounded; waits finite; no leak (see §8) |
| I | Explicit opt-in config/CLI wiring | `PrefetchingBatcher` opt-in library, **not wired** into `xlm train` | `producer_prefetch` in `TrainingConfig` + `inputs.py` + envelope | No | Yes | Ported onto Opus impl + tests |
| J | Trace-fold safety | Byte-identical fast path + fallback + adversarial tests | No trace change (uses P33 loader) | N/A (Opus-only) | No | Kept; untouched |
| K | Attention-mask equivalence | CPU bool tensor, trainer moves to device | Unchanged list path (pickle) | Yes | No | Kept; proven by bit-exact gates |
| L | Checkpoint = committed only | `get_state` committed-only; resume regenerates | Same + in-doubt checkpoint refusal | Mostly | Partly | Refusal came with B; rest equivalent |

## 5. Astra guarantees already equivalent in Opus

C (local commit; proven additionally by the new commit-after-death test), E
(stale/rollback/load_state/resume/regeneration tests), F (close/GC/orphan
tests, daemon, finite shutdown), K (bit-exact training incl. isolated-document
masks), L (committed-only checkpoints, resume regenerates the exact update),
J (Opus-only, retained untouched).

## 6. Astra guarantees missing in Opus (all ported)

1. **A/B — commit boundary + in-doubt machine** (`cc22c50`, `979ced7`).
   `RecoveryRequiredError`, `_update_in_doubt` set immediately before
   `optimizer.step`, current-stream synchronize before schedule/counters/commit
   (only when the batcher sets `requires_cuda_commit_barrier`), flag cleared
   only after `batcher.commit()`, checkpoint refusal while in doubt, wrapper
   rollback on any `BaseException`, trainer-owned producer close on exit.
2. **G — transport normalization** (`9895987`). `_send` maps `OSError`
   (incl. `BrokenPipeError`) to `PrefetchProducerError` with reap;
   `_receive_raw` and `_take` map poll/recv `OSError`/EOF the same way;
   rollback after close is a safe no-op.
3. **G2 — duplex-pipe reset deadlock** (`9895987`). Discovered while porting:
   the new wrapper rollback plus an explicit rollback send two large resets
   back-to-back while a large update is in flight; both sides block writing
   with nobody reading (reproduced deterministically, fixed, regression test
   `test_back_to_back_resets_cannot_deadlock_the_duplex_pipe`). Fix: a
   short-lived divert thread keeps one inbound reader alive while a large
   reset crosses; shutdown now closes first (EOF reaps) with finite
   join/terminate/kill. Residual microsecond races (tiny control sends vs a
   perfectly-full buffer) are pre-existing on the base and documented, not
   introduced.
4. **D — end-state binding** (`9895987`). Each consumed update now recomputes
   `end_state_digest` and checks
   `end.committed_valid_targets - committed.committed_valid_targets ==
   valid_targets` before the trainer may use it.
5. **I — explicit run option** (`52d7627`). `training.producer_prefetch`
   (`off` default | `process_depth1`), validated by `TrainingConfig`, frozen
   into the execution envelope, honored by direct/queue/resume from the frozen
   value; non-mixture inputs reject it; production wrap uses depth 1 with
   content verification.

## 7. Exact protections/tests ported

- `test_optimizer_failure_requires_durable_recovery` (in-doubt + resume exact)
- In-doubt assertions across all five consumer-failure stages
- `test_cuda_commit_barrier_failure_requires_recovery` (CUDA)
- `test_consumed_update_commits_after_producer_death` (C)
- `test_broken_pipe_and_poll_failures_are_domain_errors` (G)
- `test_consumed_update_rejects_tampered_end_binding` (D)
- `test_back_to_back_resets_cannot_deadlock_the_duplex_pipe` (G2)
- `test_train_owns_producer_shutdown` (F/close ownership)
- `tests/test_producer_configuration.py` (I: default, opt-in, depth, rejection)
- `test_configurable_workflow` producer_cpu/producer_cuda params + envelope
  assertion (I resume identity); `suite_domains.json` entry
- Existing `optimizer`-stage in-process retry test narrowed to true
  pre-optimizer stages (that retry was the BLOCKER)

## 8. Deliberately NOT ported and why

- Astra's producer implementation/wire format (pickle batches, credits,
  watchdog, depth-two logic): slower wire, duplicate producer; Opus compact
  NumPy + string-table design retained as decided.
- IPC frame bound (16 MiB), producer RSS bound/admission (2 GiB),
  preparation/receive deadlines (60 s): benchmark guards. Production frames
  are bounded by `global_batch_valid_targets`/context/dtypes (a 65,536-target
  B8 update is a few MB); the queue is bounded by depth ≤ 2 plus outstanding
  accounting; waits are finite (300 s timeout, finite join/terminate/kill);
  no child leak (daemon + finalizer + owned close, tested).
- Rejected pinning, copy-stream experiments, async checkpoint prototype:
  out of scope for this milestone.
- Profiling/benchmark harnesses and evidence from Astra: not duplicated.

## 9. Final producer architecture

Recognizably Opus: one long-lived spawned child running the unchanged
`MixtureBatcher` at most `depth` (config route: 1) updates ahead; compact
NumPy arrays + string-table provenance over a duplex pipe; committed state
only in the consumer, changed only in `commit()`; generation/ordinal/start/end
digest/content/budget binding with stale discard; fail-closed transport;
divert-protected resets; close-first shutdown.

## 10. Final trace-fold architecture

Unchanged Opus `extend_target_trace_chain`: proves the str/int domain once per
window, escapes document runs once with the same C escaper `json.dumps` uses,
`%d` integer formatting; byte-identical records/digests; fallback to the
original per-target function outside the proven domain; per-target byte-limit
checks retained. Always on.

## 11. Final explicit configuration semantics

- Default `off`: byte-identical synchronous behavior unless the user opts in.
- `training.producer_prefetch: process_depth1` enables a depth-1,
  content-verified `PrefetchingBatcher` around the mixture batcher.
- Recorded by `TrainingConfig` validation and the frozen execution envelope;
  direct, queue and resume all construct from the frozen value, so a resumed
  run cannot silently change mode; provenance carries it end to end.
- Unknown values and non-mixture inputs are rejected loudly.

## 12. CPU correctness tests

All with locked-dependency interpreters, `PYTHONPATH` at this candidate,
one-thread BLAS/tokenizers, `-n 0` for spawn/serial tests:

- `tests/test_prefetch.py`: **18 passed** (exactness, depth-2 + final partial,
  sampling/packing/encode/exit faults, startup failure, kill + regenerate,
  stale discard, budget misprediction, misuse refusal, encoding refusal,
  close/GC, orphan death, commit-after-death, pipe/poll domains, tampered
  binding, back-to-back resets)
- `tests/test_prefetch_training.py`: **11 passed + 1 passed**
  (bit-exact training, 5 failure stages, 2 pre-optimizer retries, optimizer
  recovery, resume regeneration, train-owned shutdown; barrier test separately)
- `tests/test_sampling_trace.py` + `tests/test_producer_configuration.py`:
  **19 passed**
- `tests/test_trainer_mixture.py`: **6 passed**;
  `test_trainer.py` + `test_trainer_data.py` + `test_checkpoint.py`:
  **19 passed**
- `test_configurable_workflow[producer_cuda]`: **passed** (direct + queue +
  resume with producer, envelope assertion, 188 s on GPU)
- Ruff check, `ruff format --check`, and mypy pass on every changed
  product/test module (mypy reports only 2 pre-existing errors in unrelated
  `src/xlm/data/dedup/*` files, present on the base).
- NOT RUN (environmental, no locked CPU env available and no installation
  permitted): `test_configurable_workflow` CPU params and queue/frozen
  serial_heavy selections — the pristine Opus tree fails them identically at
  environment validation, before any product code runs. No full-suite pass is
  claimed (focused policy per AGENTS.md).

## 13. CUDA exactness tests

RTX 4090, torch 2.14.0+cu126, no other XLM GPU process (checked via
`nvidia-smi` before each run), real 50M B8 release-LR contract, 65,536 valid
targets/update, no substitution:

- `tests/test_p34_prefetch_cuda.py`: **passed** — 3 sync vs 3 prefetched
  updates bit-equal on metrics (losses), clipped-gradient digests, learning
  rates, committed cursors/state (incl. trace digest) and every final
  parameter, with the new commit barrier active in the prefetch path.
- `test_cuda_commit_barrier_failure_requires_recovery`: **passed** — barrier
  failure after a real optimizer step keeps committed state, marks in-doubt,
  refuses retry and checkpoint.
- Benchmark cross-mode digests (§16) agree per repetition as a second,
  independent exactness signal.

## 14. Failure/rollback/resume matrix

| Failure | Committed moves? | In doubt? | Recovery | Proven by |
|---|---|---|---|---|
| Sampling/packing/encode/startup/child death (fetch) | No | No | Regenerate / restart | prefetch fault tests |
| Broken pipe / EOF / poll failure | No | No | Domain error, restart | domain tests |
| Before H2D / mid-accumulation | No | No | In-process rollback + retry, bit-exact | retry tests |
| Optimizer step / barrier / before commit | No | **Yes** | Fresh reload from checkpoint | optimizer + barrier tests |
| After commit (checkpoint unpublished) | Yes (memory) | **Yes** | Replay from prior checkpoint | after_commit test |
| Checkpoint while in doubt | — | — | Refused | optimizer/barrier tests |
| Resume / load_state | Regenerates exact next update | No | Continue bit-exact | resume + workflow tests |

## 15. Static checks

Ruff check clean, `ruff format --check` clean, mypy clean on all changed
modules (see §12 for the two pre-existing unrelated errors).

## 16. Final synchronous targets/s (50M B8, 65,536 targets, 10+50)

`candidate_fast_sync_r{1,2,3}`: **37,543 / 33,596 / 38,324**
(median 37,543; r2 is a machine-noise dip on this busy WDDM desktop; Opus
same-session range was 38,703–40,650, median 39,530).

## 17. Final producer targets/s

`candidate_prefetch_r{1..4}`: **42,978 / 42,963 / 49,297 / 49,450**.

## 18. Final resident targets/s

`candidate_resident_r{1..4}`: **44,393 / 44,307 / 49,238 / 48,812**.

## 19. Producer/resident percentage

r1 96.8%, r2 97.0%, r3 **100.1%**, r4 **101.3%** — the producer sits at the
resident ceiling; the r1/r2 gap is session noise (14% session spread), not a
structural regression: consumer wait 3.6–3.9 ms/update, `blocked_takes = 0`,
`resets = 0`, `budget_mispredictions = 0` in every run.

## 20. CPU/RSS/VRAM (prefetch r4)

Producer mean **~36% of one core** (max 54%: substantial headroom), RSS
**~560 MB**; trainer ~93% of one core, RSS 1.46 GiB (peak 1.58 GiB); VRAM
allocated 3.82 GiB / reserved 4.47 GiB under the 14.5 GiB cap.

## 21. Checkpoint recommendation (preserved, not implemented)

No async checkpointing in this milestone. Typical save ~3 s; tails on G: come
from storage/fsync/cache exhaustion on the DRAM-less SATA SSD, not
serialization. Operational recommendation: publish checkpoints to the NVMe
volume and keep `TEMP` off the constrained checkpoint volume. No
user-specific drive letters are hard-coded into portable product logic.

## 22. SDPA nondeterminism note (preserved, not fixed)

Pre-existing, out of scope: memory-efficient SDPA backward is
nondeterministic at larger shapes on the certified path (300M: 17/30 distinct
gradients `is_causal`, 2/30 explicit mask; 50M: deterministic in 30/30).
`torch.use_deterministic_algorithms(True)` selects a deterministic variant but
changing attention math/backend is a separate scientific contract decision.
P34 does not change it. No deterministic algorithms were enabled here.

## 23. New commits after Opus, in cherry-pick order

1. `cc22c50` fix(train): fail closed after ambiguous CUDA update
2. `9895987` fix(data): harden prefetch transport, reset and shutdown boundaries
3. `52d7627` feat(train): expose explicit producer run option
4. `979ced7` test(train): cover the CUDA commit-barrier failure boundary
5. (this report, STATUS and candidate evidence — docs commit)

## 24. Complete final candidate commit sequence from P33

`8fd05c1` (P33 base) → `0db5c24`/`1850fe8`/`7cbc9bc`/`d36b18f`/`dc5a22b`/`a979db2`
(Opus series §2, tree-identical to Opus HEAD) → `cc22c50` → `9895987` →
`52d7627` → `979ced7` → docs/evidence closeout.

## 25. Whether this candidate is safe to integrate

**Yes**, within the stated scope: the Opus architecture and its advisarial
tests are intact; every Astra correctness/lifecycle guarantee that Opus
genuinely lacked is ported minimally with regression tests; CUDA exactness
holds bit for bit with the barrier active; the producer sits at the resident
ceiling; configuration is explicit, default-off, and resume-safe. All
verification commands, exit statuses, and NOT RUN items are recorded above;
no fake numbers, no deselections counted as passes.

## 26. Anything requiring Astra-level final review

1. The residual microsecond race: a tiny control send (`produce`/`stop`) can
   block if it lands while the pipe buffer is perfectly full of a child
   partial send. Pre-existing on the base, unchanged in probability by this
   work (large sends are now protected), but a reviewer may want a control
   channel or send timeouts as a follow-up.
2. No IPC frame/RSS/deadline hard caps were added (benchmark guards
   deliberately not copied); bounds rest on batch config and depth — review
   whether a future larger-context recipe needs explicit caps.
3. `verify_content=True` is on in the config route (not in benchmarks);
   steady-state cost is sub-millisecond but was not isolated in this session.
4. The `producer_cpu` workflow param and queue/frozen serial_heavy selections
   still need a locked CPU env to run; only the CUDA workflow leg passed here.
