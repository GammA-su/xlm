# P34 (independent, Opus) — closing the 50M end-to-end / resident gap

Branch `perf/opus55-p34-independent`, worktree `G:\Project\xlm-opus55-p34`,
starting HEAD `8fd05c11e1bdfd84e075000d59e14c315c986f36` (certified P33 base),
initially clean. Environment `.venv-opus55-p34` reused without synchronization:
Python 3.12.13, torch 2.14.0+cu126, CUDA 12.6, NumPy 2.5.3, RTX 4090
(25,756,696,576 bytes), driver 596.49, shared Windows desktop (~8.3 GiB device
memory before allocation). No network, downloads, installs, live data, research
training, push or merge. The other P34 worktree was not inspected.

## Result

**50M B8 end-to-end: 32,189 → 50,588 targets/s (median of 3, +57.2%), 99.6% of
the same-session resident ceiling (50,775) and 98.0% of P33's 51,606.** Every
scientific quantity is unchanged: the full 60/61-update runs of all four loader
modes end in one identical parameter digest, and an actual-50M CUDA test is bit
exact on gradients, LRs, metrics, parameters and cursors.

Two independent, retained changes:

1. **Exact trace-chain fold** (product, always on): the C07 per-target
   SHA-256 chain is byte-identical but built without per-target dicts,
   validation or `json.dumps`. Loader CPU per update 779 → 390 ms; synchronous
   end-to-end 32,189 → 39,530 targets/s (+22.8%).
2. **Bounded speculative producer** (opt-in library, `PrefetchingBatcher`):
   one spawned process runs the unchanged `MixtureBatcher` one update ahead and
   ships compact arrays over a pipe; committed state stays in the trainer.
   Zero blocked updates in 150 measured; consumer cost 3.0–3.5 ms/update.

Stop condition reached: **resident compute is the practical ceiling** at 50M.
Remaining gains need launch/kernel work or a changed B8 contract.

## Reproduced P33 baseline and decomposition

Commands via `scripts/p34_env.ps1` (offline, locked, no-sync, this venv, one
thread per BLAS/OMP, `TOKENIZERS_PARALLELISM=false`). The frozen fixture was
regenerated from its seed and reproduces `evidence/p33/fixture.json` byte for
byte. `--loader p33` executes `stream.py` pinned at `8fd05c1` for references.

| 50M B8, 10 warmup + 50 measured, 65,536 targets/update | Runs (targets/s) | Median | Median update |
|---|---|---:|---:|
| P33 loader, synchronous (P33 reference path) | 31,386 · 32,268 · 32,190 | **32,189** | 2,047 ms |
| Exact trace fold, synchronous | 38,703 · 39,530 · 40,650 | 39,530 | 1,666 ms |
| **Prefetch producer, depth 1** | 49,327 · 51,065 · 50,588 | **50,588** | 1,287 ms |
| P33 loader inside the producer | 50,337 · 50,926 | 50,632 | 1,292 ms |
| Advancing resident (ceiling) | 49,896 · 50,775 · 50,792 | 50,775 | 1,289 ms |

P33 reported 32,152 / 51,606; the reproduction matches the former and today's
resident ceiling is 1.6% lower. One P33-loader run observed a foreign XLM
process mid-run, is kept as `final_p33_sync_r1_invalid.json` and was repeated.

Instrumented update (one extra update, CUDA event spans include launch gaps):

| Stage ms | P33 sync | Exact sync | Prefetch | Resident |
|---|---:|---:|---:|---:|
| Loader / consumer take (CPU wall) | 671.7 | 332.8 | **3.0** | 0.0 |
| H2D `Tensor.to` (68 calls) | 4.8 | 4.7 | 4.6 | — |
| Forward / objective / backward | 446 / 55 / 665 | 449 / 51 / 725 | 439 / 51 / 655 | 443 / 50 / 669 |
| Finite / clip / optimizer | 10.5 / 6.4 / 6.1 | 14.5 / 8.5 / 5.5 | 11.8 / 4.8 / 5.4 | 10.7 / 6.4 / 5.5 |
| Whole update wall | 1,884 | 1,612 | **1,183** | 1,194 |

### Where the ~800 ms of preparation went (CPU only, quiet host)

`scripts/p34_cpu_probes.py decompose` (medians of 8 steady updates; coarse
per-window wrappers, so stage sums exceed the uninstrumented total):

| Stage per 65,536-target update | P33 loader | Current |
|---|---:|---:|
| **Uninstrumented `next_step_microbatches` + commit** | **779 ms** | **390 ms** |
| Per-target trace chain (exact replay) | 529 | fold: 136–149 |
| … of which `json.dumps` alone | 279 | — |
| … of which SHA-256 + hexdigest alone (irreducible) | 63 | 63 |
| … type validation / dict / wrapper remainder | ~187 | — |
| Packing + per-target provenance lists (`_pack_window`) | 77 | 111* |
| Window selection (incl. mmap read 20, index lookup 24–26) | 48 | 50 |
| Microbatch tensors + metadata lists | 21 | 23 |
| Trainer attention mask from lists | 4 | 4 |
| Commit (state deep copy) | 1.3 | 1.5 |
| Mixture scheduling / exposure accounting | 0.4 / 0.9 | 0.4 / 0.9 |

*Identical code; the spread reflects allocation/GC placement, not a change.
Mask and position creation are inside packing; cursor arithmetic is negligible.

**Eliminated outright:** ~390 ms of JSON construction and re-validation. The
chain itself is sequential (each record embeds the previous hex digest), so
incremental hash objects, larger updates or prefix-state reuse cannot apply:
the first 64-byte SHA block already contains the previous digest. What remains
is per-document string escaping (once per run), `%d` integer formatting
(`int.__repr__`, the same bytes `json` writes), and one SHA-256 per target.
Values outside the proven `str`/`int` domain take the original function, so
malformed input still fails identically; the 8 MiB record limit is checked per
target. Verified: 20 real updates equal to HEAD's `stream.py` in every tensor,
metadata list and state field (10 epoch wraps); adversarial strings (quotes,
backslashes, `%`, NUL, astral, U+2028); fallback types; truncation of
`last_step_trace` at 256; byte-coverage flag.

## Architectures considered

| Design | Idea | Measured | Decision |
|---|---|---|---|
| C: eliminate in-process | Exact trace fold only | +22.8% e2e; loader 390 ms | **Retained** (product) |
| A: full-batch producer | Child builds `TrainingBatch` lists; Queue/Pipe pickles | 4.07 MB, 18.4 ms consumer receive, 35 ms round trip | Rejected: per-target Python lists dominate IPC |
| A′: compact producer | Child runs unchanged batcher; ships NumPy arrays + coded provenance over Pipe | 1.2 ms receive (tensors), 3.0–3.5 ms/update actual | **Retained** (opt-in) |
| Shared-memory slot | Child writes arrays into a trainer-owned slot | 0.24 ms receive | Rejected: saves ~1–3 ms/update (≤0.25%) for slot lifetime/overwrite hazards |
| B: descriptors | Child sends window descriptors; trainer re-reads mmap and packs | Not built | Rejected: would duplicate packing semantics on the trainer thread to save ~1 ms |
| torch.multiprocessing tensors | Shared CPU tensors via Queue | 33 ms round trip | Rejected: provenance lists still pickled |
| Thread producer | GIL-sharing prefetch | Not re-run | P33 measured no gain; trainer thread is ~91% busy |
| Two producers | Ordered parallel preparation | Not run | One producer prepares 2.9× (idle) / 2.5× (beside the trainer) faster than the GPU consumes |

The producer and the trace fold are complementary. The P33 loader inside the
producer reaches the same throughput, but needs ~990 ms of a ~1,290 ms update
(67% of a core vs 35%) and already blocked 3–8 times per 50 updates. The fold
turns a marginal overlap into a 2.9× margin and is the entire win for any
synchronous path.

## Producer architecture (`src/xlm/data/sampling/prefetch.py`)

* One daemon child from the `spawn` context; the module has no import-time
  effects; the entry point is module-level; `ProducerSpec` is plain data
  (recipe dump, shard paths plus manifests re-verified in the child, batcher
  settings). The child imports neither torch nor CUDA; startup 2.4–2.6 s,
  reported separately and amortized.
* Depth 1 by default (maximum 2). Depth 2 was not needed: zero blocked takes.
* Transport: pickle-5 over one duplex `Pipe`. The `PreparedUpdate` carries
  `int64` input/label/mask/position/segment arrays, a bool attention mask,
  light metadata, provenance codes and the end state (5.1 MB pickled).
  Tensors are zero-copy `torch.from_numpy` views; H2D stays pageable and
  unchanged. Per-target provenance is materialized exactly on request
  (24 ms/update if eagerly rebuilt, which the trainer never needs).
* The only trainer change: a prepared CPU bool attention-mask tensor is moved
  with `.to(device, dtype=bool)`; list inputs keep `torch.tensor(...)`.

### Speculative / committed contract

| State | Owner | Changes |
|---|---|---|
| Committed (`get_state`, checkpoints) | consumer | only in `commit()` |
| Speculative | child batcher | per prepared update; replaced by `reset` |
| Generation | consumer | rollback, load_state, restart, budget misprediction, error |

Each update binds: start-state digest (cursors, scheduler, epochs, carry,
trace digest, counters), end state and its digest, rows and light metadata,
content digest of every array and string table, remaining budget, generation
and ordinal. It is used only if its start digest equals the committed digest.
Stale generations are discarded unread. A speculative budget is
`remaining − valid_targets`; a mismatch regenerates synchronously. A second
fetch without commit/rollback raises `PrefetchProtocolError`.

### Failure matrix (all verified against an uninterrupted run)

| Failure point | Test | Outcome |
|---|---|---|
| Producer startup | `test_producer_startup_failure_is_reported` | `PrefetchProducerError`; next start succeeds |
| During sampling / packing | `test_producer_failures_never_skip_or_duplicate[sampling/packing]` | Committed state unchanged; regenerated sequence identical |
| Before IPC send (encoding) | `…[encode]` | Same |
| After IPC send (producer dies) | `…[exit]` + `test_killed_producer_regenerates_identical_update` | `restart()` regenerates the identical update (content digest, end state) |
| Consumer before use / stale batch | `test_rollback_discards_stale_speculation_and_replays` | Stale generation discarded; replay identical |
| Before H2D; device failure mid-accumulation; optimizer | `test_consumer_failure_recovers_from_checkpoint_exactly`, `test_pre_optimizer_failure_retries_in_process_exactly` | In-process rollback or checkpoint resume; bit-exact params/metrics/state |
| Before / immediately after cursor commit | `…recovers_from_checkpoint_exactly[before_commit/after_commit]` | Resume from last checkpoint; bit exact |
| Protocol misuse, invalid resumed state | `test_protocol_misuse_and_invalid_state_fail_closed` | Refused; committed state and producer intact |
| Non-integer provenance | `test_lossless_encoding_refuses_non_integer_provenance` | `PrefetchEncodingError` (fail closed) |
| Windows lifecycle | `test_close_and_garbage_collection_leave_no_producer`, `test_producer_exits_when_consumer_process_dies` | No orphan after close, GC or hard consumer death |

"CUDA failure" is exercised as a device-side exception on CPU; the CUDA
exactness gate covers real CUDA execution. **Resume:** checkpoints store
committed state only; after reload the first produced update equals the
update prepared before the crash (content digest and end state), and training
continues bit exactly (`test_resume_regenerates_the_discarded_prefetched_update`).

## CPU-only ceiling, CPU, RSS, VRAM, telemetry

`p34_cpu_probes.py ceiling`: one producer with an idle consumer sustains
**2.21 updates/s = 144,640 targets/s** (443 ms/update; P33 loader 1.15
updates/s). Beside the GPU trainer it measures ~505 ms/update.

| 50M B8 | Trainer CPU (1 core=100) | Producer CPU | System CPU | Trainer / producer RSS | Allocated / reserved |
|---|---:|---:|---:|---:|---:|
| P33 sync | 91.5–92.6 | — | 20–26% | 1.48 GiB | 3.82 / 4.47 GiB |
| Prefetch | 90.7–91.7 | 34–36 | 28–34% | 1.47 / 0.55 GiB | 3.82 / 4.47 GiB |
| Resident | 89.4–89.7 | — | 24–30% | 2.32 GiB | 3.94 / 4.60 GiB |

Uncontended sampled telemetry (mean): P33 sync 25–29% GPU util, 146 W;
exact sync 32–37%, 162 W; prefetch 40–43%, 185–187 W, 23.6% memory util;
resident 40%, 184 W; SM clock ~2,683 MHz throughout. Samples alias the
update phases and are not SM occupancy. No affinity was set: 16 logical
threads, ~2 busy cores, and prefetch already equals resident; producer native
threads stay at one.

H2D / pinning / streams: pageable H2D is 4.6 ms of a 1,183 ms update (0.4%)
and prefetch equals resident, so there is nothing left to overlap. Pinned
buffers, nonblocking copies and a copy stream were **not pursued**; if they
ever matter, trainer-owned preallocated pinned buffers (never producer-created
pinned memory on Windows) are the right location.

## Checkpoint tails (`scripts/p34_checkpoints.py`, real 50M model + AdamW)

Seven saves (598,835,373 published bytes each) on G:, a Crucial BX500 2 TB
SATA SSD (DRAM-less, SLC write cache):

| Save | Regime | Pause s | Serialize model/opt | Publication (fsync) | Disk MiB written |
|---|---|---:|---:|---:|---:|
| 0 | back-to-back | 3.52 | 0.34 / 0.60 | 2.48 (1.58) | 886 |
| 1 | back-to-back | 3.03 | 0.32 / 0.59 | 2.01 (1.27) | 716 |
| 2 | back-to-back | **25.28** | 0.31 / 1.80 | 22.97 (**22.22**) | 1,090 |
| 3 | back-to-back | **14.30** | 1.34 / 2.67 | 10.17 (**9.40**) | 1,069 |
| 4–6 | 60 s apart | 3.00 · 3.57 · 3.02 | ~0.29 / ~0.59 | ~2.0–2.6 | 716–1,010 |

D2H ~0.1 s, optimizer snapshot 0.02 s, SHA-256 0.32 s CPU, post-save
verification ~0.5 s (outside the pause) are stable. **Tails are storage
tails:** raw sequential writes with a 64 MiB fsync ran at 45 MiB/s in one
probe and recovered from 45 to ~375 MiB/s mid-way through the next — the SLC
cache exhaustion cliff. Back-to-back saves exhaust it; spaced saves do not.
P33's 62 s pause followed gigabytes of diagnostic snapshot writes. Every save
also writes the payload twice (torch.save into a temporary directory, then a
hashed, fsynced copy into the store); the P33/P34 wrappers place `TEMP` on
G:, so the temporary copy lands on the same slow volume (716–1,090 MiB
written per 571 MiB payload).

**Async checkpointing: not implemented; not recommended yet.** At the 50M
recipe cadence (16M targets ≈ 5.3 min) saves are spaced far apart; the
typical 3.0 s pause is ~1% of runtime (63 saves ≈ 3.2 min per 1B targets).
First, operationally: publish checkpoints to the NVMe volume and keep `TEMP`
off the checkpoint volume; second, serialize once, directly into store
staging, with a streaming hash (halves bytes written, no durability change).
If asynchrony is still wanted, design A: an immutable CPU snapshot of model,
optimizer, scheduler, RNG, committed batcher state, counters and provenance
taken on the trainer thread at the optimizer boundary (~0.15 s), one
background publisher, at most one in flight; when the next checkpoint is due
while one is publishing, **block** (never silently drop; coalescing only if a
research policy says so); a crash loses only the in-flight save.

## 150M / 300M smokes (after freezing 50M; 10 warmup + 20 measured)

| Model / B | P33 sync | Exact sync | Prefetch | Resident |
|---|---:|---:|---:|---:|
| 150M / 16 | 30,203 | 37,417 · 34,096 · 37,461 | 46,431 (1 run) | not run |
| 300M / 8 | 18,282 | 21,665 · 21,192 · 19,352 | 20,706 · 21,326 · 21,432 | 21,702 · 21,917 |

150M B16 gains +54% (single prefetch run). At 300M the exact synchronous
loader is already within ~3% of resident and the producer's extra benefit is
inside run-to-run spread (±5%). The synchronous loader costs less at 300M than
its CPU time implies; the partial overlap was not isolated (open question).
Producer 478–578 ms/update, 18–30% of a core. Allocations are unchanged by
the loader mode (150M 12.17/13.63 GiB, 300M 11.85/13.06 GiB).

## Planning estimates (raw 1B-target hours, synthetic steady state)

`1e9 / targets_per_second / 3600`; excludes setup, acquisition, evaluation,
failures; not a convergence time.

| Model | P33 sync | Exact sync | Prefetch |
|---|---:|---:|---:|
| 50M B8 | 8.63 | 7.03 | **5.49** |
| 150M B16 | 9.20 | 7.42 | 5.98 (one run) |
| 300M B8 | 15.19 | 13.11 | 13.03 |

50M checkpoint allowance at the recipe cadence: +3.2 min typical, +26.5 min
if every save hit the observed 25 s tail. Larger-model pauses were not measured.

## Scientific exactness

* Data: every run of every mode ends at the same committed trace digest and
  target count; the producer runs the unchanged batcher; CPU training through
  the real Trainer is bit exact for prefetch, rollback, recovery and resume.
* 50M CUDA: `test_actual_50m_prefetched_updates_match_synchronous_loader`
  compares three release-LR 65,536-target updates (first LR 0.001): metrics,
  every clipped-gradient digest, LRs, committed state, every parameter — exact.
  All 12 full 50M runs end in one digest per run length (61 updates with the
  instrumented update, 60 without), across all four modes.
* **GPU nondeterminism (pre-existing, not P34):** the certified path's
  memory-efficient SDPA backward is nondeterministic. torch warns in a real
  150M/300M update that "Memory Efficient attention defaults to a
  non-deterministic algorithm" (the only flagged op). An isolated probe at
  300M shapes produced 2 distinct gradients in 30 repeats with an explicit
  mask and 17 with `is_causal=True`. One of three identical 300M prefetch runs
  and the 150M cross-mode runs diverged at ~2e-5 relative gradient norm while
  their data streams were identical. P33 saw the same ("differs at measured
  update 7"). Bitwise run-to-run replay is therefore not guaranteed on this
  path at any loader setting; `torch.use_deterministic_algorithms(True)`
  selects a deterministic variant. Changing it is a separate contract
  decision (and throughput measurement); P34 does not change it.

## Tests and checks

| Selection | Result | Exit |
|---|---|---:|
| Focused CPU gate: new prefetch/training/trace tests + trainer, sampling, packing, token-map, checkpoint, optimizer, objective, attention, P33 regressions (`-m "not cuda" -k "not compile" -n 8 --dist=worksteal --max-worker-restart=0`) | 201 passed, 24 deselected | 0 |
| Bounded CUDA gate (`tests/test_p34_prefetch_cuda.py tests/test_p33_gradients.py tests/test_p33_resident.py tests/test_cuda_execution.py -m cuda -k "not compile" -n 0`) | 20 passed, 21 deselected | 0 |
| Ruff, format, mypy (`--follow-imports=silent`) on all changed source, scripts and tests | Pass | 0 |

No skips in either selection; deselections are not passes. The full CPU
six-leg gate, compile cases, network/live tests and official evaluation were
**not run** (focused scope).

## Commits (review in order)

1. `5fca860` perf(data): fold the exact target trace chain once per packed window
2. `754730b` perf(loader): prepare exact updates in a bounded speculative producer
3. `51a7fb1` bench(cuda): add bounded P34 synchronous/resident/prefetch harness
4. `75fd8a9` bench(cuda): add P33-loader reference modes and an interleaved case runner
5. `0b64c83` bench(p34): retain throughput, CPU, checkpoint and determinism evidence
6. docs(p34): this report, the user doc and STATUS (branch HEAD)

## Rejected or not pursued

| Idea | Expected | Measured / reason | Risk |
|---|---|---|---|
| Full `TrainingBatch` over Queue/Pipe | hide 800 ms | 16–18 ms trainer-thread receive; 4 MB | Low, wasteful |
| torch shared tensors via Queue | cheaper tensors | 33 ms round trip; lists dominate | Windows handle lifetime |
| Shared-memory slot | −1 to −3 ms/update | ≤0.25%; below noise | Slot overwrite / lifetime |
| Descriptor + trainer materialization | −1 ms IPC | Not built | Duplicated packing semantics |
| Eager provenance rebuild on consumer | API parity | 24 ms/update on the launch thread | None; kept lazy instead |
| Depth 2, two producers | hide stalls | 0 blocked takes; 2.5–2.9× producer headroom | Memory, ordering |
| Thread producer | hide loader | P33: no gain; GIL vs 91%-busy trainer | GIL |
| Pinned / nonblocking / copy stream | overlap H2D | H2D 0.4%; prefetch = resident | Windows pinned lifecycle |
| Hash prefix/incremental reuse | fewer SHA blocks | Impossible: first block holds previous digest | — |
| Async checkpoint publication | hide 3 s | ~1% at cadence; tails are storage | Durability/ownership |

## Cross-review points against Astra

* Which loader work was **eliminated** vs hidden? (Fold: −390 ms exact.)
* Does the producer ship per-target Python lists? (Here: codes; 3 ms/update.)
* How is speculative work bound to committed state (digest chain,
  generations), and is a stale update provably unusable?
* Budget misprediction and final partial update handling.
* Failure matrix coverage, especially after-commit and resume regeneration.
* Windows lifecycle: GC, hard consumer death, `spawn` import safety.
* Contention discipline: were foreign XLM processes detected during runs?
* Same fixture, same run shape; compare end-to-end/resident ratios, not raw
  numbers, since resident drifted 1.6% between sessions.
* Checkpoint-tail root cause and whether async publication was justified.

## Integration and readiness

**Integrate:** yes. The trace fold is a small, exact, always-on product change
(+22.8% synchronous). The producer is additive and opt-in; it touches the
trainer only for an equivalent attention-mask path.

**50M research readiness:** the training system is ready for a bounded 50M
pilot on the synchronous path. Before a campaign: (1) wire `PrefetchingBatcher`
behind an explicit, recorded run option (not a hidden default); (2) move
checkpoints (and `TEMP`) to the NVMe volume or accept 3 s typical / 10–60 s
tail pauses; (3) decide whether bitwise run-to-run replay is required (then
measure deterministic attention); (4) validate cold-storage loader latency on
real shards — this fixture is small and page-cached.

Next command: `git log --reverse --oneline 8fd05c11e1bdfd84e075000d59e14c315c986f36..HEAD`
in this worktree, then compare with Astra's P34 using the points above.
