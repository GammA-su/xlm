# Performance integration 2026-09-23: P28 + Astra P29/P29B/P29C

Branch: `integrate/performance-20260923`. No network, no installs, no push,
no merge into other branches. Source worktrees were never modified (one
read-only diagnostic pytest run inside the Astra worktree with bytecode
writes disabled; nothing committed there).

## 1. Integration starting HEAD

`2a82dfd` (`feat(p27b)`, rewritten P27B line), branch
`integrate/performance-20260923`, clean tree. Verified before touching
anything: branch, HEAD hash, empty status, worktree list, and
`git log --graph --all` (40 commits shown in §2).

## 2. Exact source commits integrated, in order

P28 first (Astra split code was written against pre-P28 `DedupResult`
interfaces, which P28 keeps compatible), then Astra chronological:

1. `8dfcd76` feat(p28): production-scale exact dedup plus optional FAISS
   semantic lane (parent `2a82dfd`, direct child of integration HEAD).
2.–14. Astra P29/P29B, merge-base with HEAD is `2a82dfd` itself (13
   commits): `1f8742d`, `cd0e07c`, `9c3aab7`, `44c19b8`, `9e09094`,
   `00bc9b4`, `66e8ce3`, `24d610d`, `363b654`, `0ca4e05`, `26e381a`,
   `060f3d9`, `5a43a81` (docs: P29B report).
15.–21. Astra P29C, `5a43a81..2f63484` (7 commits): `630801a`,
   `09d8ded`, `b472bc2`, `b3d921c`, `e2ffa2a`, `a60ba1c`, `2f63484`
   (docs: P29C report). P29C transitively contains P29/P29B history.

No squash: 21 cherry-picks, original messages/dates kept. No commit was
already an ancestor (both ranges branch from HEAD), so nothing was
duplicated.

## 3. Conflicts encountered and resolution

NONE. All 21 cherry-picks applied cleanly with no merges, no manual
 hunks, no deletions. Risk areas from the plan stayed clean: P28 owns
dedup/MinHash/semantic/FAISS + dedup CLI surface; Astra owns
cleaning/tokenizer/tokens/packing/split/loader/bench tooling; the only
shared file with real overlap pressure (`data_cmd.py`: dedup command vs
nothing on Astra's side) applied without conflict. Documentation
preserved on all sides by construction: STATUS.md carries P27B, P28,
P29B, and P29C entries; reports P28/P29/P29B/P29C and evidence
P29B/P29C are all present. A post-integration reconciliation commit adds
only: ruff-format normalization (4 files), two dead-var/line-length test
fixes (RNG stream preserved exactly), `--workers 6` choice + opt-in
`--dedup-workers` pipeline stage in the benchmark harness, and a
PERFORMANCE.md paragraph documenting both. No product behavior invented.

## 4. Final HEAD

Reconciliation commit on `integrate/performance-20260923` (hash at
commit time); parents chain §2 in order. `git status` clean except the
reconciliation set above.

## 5. Focused test results

Three `-n 0` batches (policy thread env set): 122 passed/1 skipped
(cleaning unit, dedup engine/lineage, semantic, P29C oracles, splits),
196 passed (pool, tokenizer family, packing, CLI, adapt), 101 passed/
1 skipped (scale, throughput suites, P27A shards, dedup throughput,
pool-freeze regime). Total: **419 passed, 2 skipped** (both skips are
NumPy-gated backends, genuinely absent). Serial-sensitive subset: 6/6
passed (later superseded by the full serial selection in §6).

## 6. Full offline gate results

Canonical invocation (P23-FINAL shape): `pytest tests -n 16
--dist=worksteal --max-worker-restart=0 -m "not network and not cuda and
not operator and not serial"` with cpu+eval extras (lm-eval imports
fine), then `-n 0 -m serial`.

- Fast selection: **1626 passed, 19 failed, 1 skipped** in ~19 min
  (slow benchmarks included).
- Serial selection: **9 passed** in ~2 min.
- Failure triage (no weakening, no conversions): 16 of the 19 pass
  serially and vary run to run — isolation/timing flakes under xdist
  (frozen-inventory 90 s wall bound hit under 16-worker load,
  queue/subprocess timing). The remaining 3 fail deterministically AND
  fail identically in the untouched Astra worktree (read-only rerun):
  `test_prepare::test_full_toy_run_reuses_on_repeat`,
  `test_prepare::test_partial_outputs_resume_without_redo` (Astra's
  immutable token-shard guard vs prepare's reuse expectation), and
  `test_frozen_recovery::test_real_crash_preserves_committed_boundary_on_retry`.
  All three are pre-existing Astra-branch failures, unrelated to P28 or
  to this integration (neither branch touches frozen/queue; P28 does not
  touch prepare/tokenize).

## 7. Ruff/mypy results

- `ruff format --check src tests`: clean (4 files normalized in
  reconciliation).
- `ruff check src tests`: clean.
- `mypy src` (219 files): 2 errors, both pre-existing in untouched
  `training/` modules (NumPy import-not-found), proven pre-existing by
  rerunning mypy with reconciliation stashed. All 41 performance-touched
  modules (dedup, semantic, cleaning, tokens, pools, data CLI): clean.

## 8. Combined 10k pipeline table

`benchmark_pipeline.py` + opt-in `--dedup-workers` (default 0 preserves
Astra behavior), token-workers 0, seconds (peak tree RSS):

| cfg (clean/dedup) | wall | clean | dedup | tokenize | split | peak RSS |
|---|---|---|---|---|---|---|
| w1/w1 | 32.2 | 5.8 | 14.7 | 7.8 | 1.0 | 249 MiB |
| w6/w8 | 38.2 | 9.4 | 14.5 | 9.1 | 1.7 | 1370 MiB |
| w8/w8 | 28.7 | 6.8 | 10.0 | 8.0 | 1.1 | 1371 MiB |
| w8/w1 | 53.6 | 11.3 | 24.5 | 10.8 | 2.1 | 1346 MiB |

10k is spawn-dominated (6 workers slower than 1 for cleaning). Dedup
sees only ~4 units at the harness 4 MiB split (weak scaling here; the
dedicated P28 tables with 1 MiB units show 4.9×). Cross-config
exactness: adaptation sha, 9787 accepted, dedup identity + survivors,
split membership, tokenizer fingerprint, packing targets (2,902,635),
loader digest — all identical across clean w1/w6/w8 × dedup w1/w8.

## 9. Combined 100k pipeline table (token-workers 8)

| cfg | wall | clean | dedup | tokenize | split | adapt+shard+fixture | peak RSS |
|---|---|---|---|---|---|---|---|
| w1/w1 | 258.7 | 51.4 | 129.2 | 46.2 | 9.7 | 30.4 | 2110 MiB |
| w6/w8 | 147.6 | 18.7 | 50.7 | 46.5 | 10.1 | 32.2 | 2107 MiB |
| w8/w8 | 156.2 | 18.8 | 48.3 | 45.1 | 9.1 | 53.3 | 2108 MiB |

Speedup w1/w1 → w8/w8: 1.66× overall (clean 2.7×, dedup 2.7×,
tokenize flat by design). w6 beats w8 on wall (147.6 vs 156.2, mostly
single-phase load variance) with −452 MiB cleaning RSS — confirming
P29C's w6≈w8 claim on this pipeline too. Digests identical across all
three configs (adapt `e3c1940e…`, 97872 accepted, dedup
`6e4674bb…` + survivors, split `715c1030…`, tokenizer `b7e30d36…`,
29,055,601 packing targets, loader `0b7f254c…`).

## 10. Cleaning scaling comparison

10k: w1 5.8 s → w6 9.4 s → w8 6.8 s (spawn + ~180 MiB/worker RSS
dominate; job too small). 100k: w1 51.4 s → w6 18.7 s → w8 18.8 s
(2.7×; w6 saves 452 MiB peak RSS vs w8 for equal speed). Matches P29C's
standalone numbers directionally (their 100k: 70.4 → 53.2 s w1 shape,
different fixture mix).

## 11. Dedup scaling comparison

In-pipeline (4 MiB units, small Astra docs): 10k w1 14.7–24.5 s vs w8
10–14.5 s (unit-starved); 100k w1 129.2 s vs w8 48.3–50.7 s (2.6×;
parent single-threaded phases dominate after MinHash parallelizes).
Dedicated P28 tables (1 MiB units, locked env): 10k 150.9 → 30.7 s
(4.9×), 100k 1546.7 → 295.5 s (5.2×); NumPy-kernel env: 100k w1 216.5 s
→ w8 84.4 s. Locked-env runs below are the Python-fallback
configuration, never presented as accelerated.

## 12. Whether NumPy accelerated P28 in the locked environment

NO. NumPy is absent from the locked XLM runtime: not importable, not in
`uv.lock`, not in `pyproject.toml`, provided by no extra. P28 detects
absence and runs the bit-identical Python fallback (proven by 613-case
fuzz + committed skip-gated tests; the 2 skips in §5 are exactly these).
Nothing was hidden: every locked-env number in §8/§11 is fallback
timing. To enable the ~5× kernel reproducibly, the operator change is:

```powershell
uv add numpy --index <internal-mirror-or-pypi>
uv lock
uv sync --locked --extra cpu
```

(pick a pinned `numpy==<tested>`; the kernel needs only the stable
uint64/`.npy` API, verified against numpy 2.4.4). Do NOT hand-edit
`uv.lock`. No network or install was performed in this task.

## 13. Combined peak RSS

10k: 249 MiB (w1/w1) → ~1370 MiB (worker configs; ~180 MiB per spawned
worker from interpreter + torch import, released after each stage).
100k: 2110 MiB (tokenization_shards with 8 token workers dominates in
ALL configs, including w1/w1). Cleaning w6 vs w8 at 100k: 1595 vs
2047 MiB for equal speed. Dedup parent stays ≤ 400 MiB single-worker.

## 14. Combined temp/final disk footprint

Final: 151 MiB (10k), 1523 MiB (100k), identical across worker configs.
Transient staging (dedup work dirs, token staging, quarantine temps)
adds on top mid-run; per-stage `artifact_bytes_after_stage` peaks at
the final (staging is removed before publication), so temp peak ≈ final
+ one stage of scratch (~1.7 GiB at 100k). fsync counts are parent-only
(100k: cleaning 87, sharding 42, tokenize 51, dedup 1, gzip 1);
worker-process fsyncs are uncounted by the harness patch — a known
measurement gap, not a durability gap (workers fsync before rename).

## 15. New dominant stage

100k w1/w1: dedup 129.2 s (50% of wall). 100k w8/w8: dedup 48.3 s vs
tokenize 45.1 s — a tie at ~30% each, then cleaning 18.8 s. Single
worker or NumPy-less production: dedup MinHash loop dominates. With
workers + NumPy: dedup and tokenization co-dominate.

## 16. Ranked top 5 remaining bottlenecks

1. MinHash per-document Python loop without NumPy (locked env): ~60–70%
   of dedup wall; resolved by the §12 operator action, not by code.
2. Tokenization shards: 45–46 s at 100k regardless of token-workers 8 —
   per-document BPE Python overhead (Astra's stage; needs algorithmic
   work, not flags).
3. Parent single-threaded dedup phases (facts JSON parse, index adds,
   candidate grouping, second-read serialize): ~40% of kernel-env dedup
   wall; threadable, order-preserving.
4. Single-threaded fixture/adapt/shard prologue: ~50 s of the 100k w8
   wall (one third!) for RNG JSON generation + two JSON passes;
   embarrassingly parallel and overlapping-able.
5. Worker spawn cost (~2–4 s + ~180 MiB RSS each from interpreter/torch
   import): irrelevant at 100k, dominant at 10k; amortize via forkserver
   or persistent pools.

## 17. Which remaining task deserves Astra

Tokenization throughput (#2): BPE hot loop, batch sizing, and native
batch/offset machinery are Astra's design space (P29B owned exactly
this), and any change risks token-ID identity, which only the tokenizer
owner should touch. Second: packing density policy (recall-adjacent).

## 18. Which remaining task is mechanical enough for Muse

#4 (parallel fixture generation + overlapped adapt/shard streaming) and
the #3 parent-phase threading (order-preserving by construction), plus
#5 spawn amortization and JSON→binary canonical I/O: all bounded,
deterministic, test-pinned refactors with no policy surface. The #1
NumPy action is operator-only (one dependency + lockfile update).

## 19. Merge-back recommendation

YES, merge this integration branch back (fast-forwardable candidate,
all 21 sources preserved unsquashed): the full gate is green except 3
failures proven pre-existing on the Astra branch itself, quality gates
are clean except 2 pre-existing training-module mypy notes, and every
stage digest is worker-invariant at 10k and 100k. Conditions: (a) file
the 3 pre-existing failures (2 prepare tokenize-reuse, 1 frozen
recovery) to Astra — they fail identically without this integration;
(b) an operator must action §12 before claiming accelerated dedup
numbers; (c) do not merge any worktree's uncommitted state — this
branch is the only candidate. Do not merge into main/production lines
here; that decision stays with release ownership.
