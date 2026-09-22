# Dense row-group/block sampling for selected-record acquisition

Status: **PLANNING ONLY — IMPLEMENTED / VERIFIED (offline fixtures)** on
`feat/rowgroup-sampling`. No fetch-semantics change, no transport change, no
adapter/policy change. No live network was used here. Any FinePDF numbers
below are OPERATOR-RUN real evidence, not reproduced in this worktree.

## 0. Why this exists (real evidence)

FinePDFs-Edu, two shards, `max_workers=2`:

- sparse `[0,1)` per file: retained 2, wall 129.125 s, transfer 17,125,280 B
- dense `[0,100)` per file: retained 200, wall 156.187 s, transfer 17,125,286 B

100x more retained documents for ~21% more wall time at identical
transfer/decompression/request cost (same 2000 scanned records). Remote
selected-record cost is strongly row-group oriented: decoding a group costs
about one group whether 1 or 100 rows are kept. Sparse isolated-row selection
pays full group decodes per kept row and is extremely inefficient.

This planner gives operators a deterministic way to build dense,
row-group-aligned selections. It only plans; acquisition semantics are
unchanged.

## 1. Operator workflow (no wrapper required)

Candidate files, then sampling plan, inspect, plan, authorize, fetch:

```powershell
# 1. Sampling plan: discover footers (bounded, read-only) and derive ranges.
#    Remote discovery (default): bounded footer range reads, no payloads.
uv run --offline --locked --extra cpu --group dev python -m xlm.cli.main data sample-blocks `
  --source finepdfs_edu --view eng_Latn `
  --catalog manifests/datasets.catalog.yaml `
  --revision 9cfabe2127faca99b3d5c4dc6d1fcb397399ebde `
  --files data/eng_Latn/train/000_00083.parquet,data/eng_Latn/train/000_00092.parquet `
  --seed 7 --mode rowgroup `
  --target-records 200 --max-overshoot-records 2000 `
  --output plans/finepdfs_blocks.json

#    Offline/local variant (authored fixtures, CI): read local footers.
#    Add: --local-dir <dir-holding-the-candidate-files>

# 2. Inspect the generated ranges and evidence report.
#    plans/finepdfs_blocks.json              -> {"file": [start, stop), ...}
#    plans/finepdfs_blocks.evidence.json    -> blocks, estimates, warnings, bias note

# 3. Build the acquisition plan from the sampled ranges.
uv run --offline --locked --extra cpu --group dev python -m xlm.cli.main data plan `
  --source finepdfs_edu --view eng_Latn `
  --catalog manifests/datasets.catalog.yaml `
  --files data/eng_Latn/train/000_00083.parquet,data/eng_Latn/train/000_00092.parquet `
  --mode selected_records `
  --row-ranges plans/finepdfs_blocks.json `
  --seed 7 --max-records 25000 `
  --output plans/finepdfs_dense.json

# 4. Explicit authorization (pilot shown; production uses admission + auth hash).
#    Pass --pilot-approved only as an explicit operator act.
uv run --offline --locked --extra cpu --group dev python -m xlm.cli.main data fetch `
  --plan plans/finepdfs_dense.json --pilot-approved
```

Pass the same `--seed` to `data plan` so the sampling seed is bound into
the plan's `sampling_frame` (and therefore the selection identity).

Dense FinePDF block selection without fetching it is step 1 alone: it
writes only `plans/finepdfs_blocks.json` + the evidence file and performs
no acquisition (remote mode performs bounded footer range reads only).

## 2. Sampling algorithm (`src/xlm/data/acquisition/sampling.py`)

- Inputs (pure logical): source/view/revision, candidate files (sorted
  internally, so input order is irrelevant), seed, mode, block target,
  overshoot bound, per-file block cap, record/byte/parser bounds.
- File visit order: sorted files rotated by
  `sha256(seed|source|view|revision|mode|"order")`. Never file-list order,
  never row group zero by default.
- Start group per file: `sha256(seed|...|file|mode|"start") mod usable-groups`.
  SHA-256 index (not `random` module): stable across processes and versions.
- `rowgroup` mode: one whole group per touch. `contiguous` mode: the minimal
  whole-group-aligned run from the start group covering `--block-records`.
- `diversify-files-first`: round robin — every selected file gets its first
  block before any file gets a second (extensions grow the same interval
  forward while groups stay consecutive, capped by `--max-blocks-per-file`).
- Greedy fill with overshoot gate: after the first (always taken) block,
  further blocks are added only if they fit `--max-records` /
  `--max-bytes` hard caps and overshoot `planned + rows - target` stays
  within `--max-overshoot-records`. Stops with a documented shortfall rather
  than fragmenting groups.
- One contiguous group-aligned interval per file (matches the single
  `[start, stop)` per-file shape `row_ranges` requires).

## 3. Determinism contract

Selection is a pure function of source/view/revision, candidate files,
seed, policy, and targets/bounds. It never depends on timing, worker count,
attempt number, local paths, or network response ordering. Changing workers
or renewing the attempt later cannot change logical selection (the
attempt-invariant selection hash already guarantees byte-identical outputs
for identical ranges).

## 4. Budget behavior

- Parser/decompression bounds apply per group at discovery with the same
  numeric diagnostic style as acquisition
  (`compared total_byte_size=... against max_parser_bytes=...; num_rows=...`).
  Unusable groups are skipped with their diagnostic recorded; if no group is
  usable anywhere, planning is refused and no files are written.
- Cumulative `--max-records` / `--max-bytes` (uncompressed) are hard caps:
  never knowingly exceeded; a first block violating them is refused.
- Target overshoot is soft and explicit: requested/planned/overshoot are all
  reported (e.g. target 950 against 1000-row groups plans 1000, overshoot 50).
- Unknowns stay unknown: `estimated_transfer_bytes` is always null (range
  transfer depends on projection/framing, never fabricated); token estimates
  appear only via an operator-supplied `--tokens-per-record` factor.

## 5. Dense sampling vs whole-file rule of thumb

- Use dense row-group/block sampling for screening: many shards, one (or few)
  aligned blocks each, diversity before depth.
- Use `whole_file` acquisition when a large fraction of a shard is needed:
  if the plan would cover most groups of a shard, downloading it once is
  usually cheaper than many range reads plus per-group decode overhead.
- Whole-file policy is unchanged in this commit.
- Bias warning (always emitted): block sampling is NOT uniform record
  sampling; screened statistics must not be read as unbiased corpus estimates.
