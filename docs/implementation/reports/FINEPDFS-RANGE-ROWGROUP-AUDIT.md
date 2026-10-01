# FinePDFs range row-group audit (b3 matched range benchmark)

Date: 2026-10-01. Branch `data/mix01-ultrax-6b` at `c36c797`. Offline,
read-only: no network, no payload download, no code change, no commit, no
tokenizer, no C05, no training.

Verdict: **FINEPDFS RANGE PATH NOT COMPARABLE.** Under the current range-path
safety rule, the range transport cannot reach 29% of the rows and 54% of the
projected bytes of the same file that the whole-file b3 benchmark processed.
A matched range benchmark would compare different populations. Neither the
4,000-row plan nor any 20,000-row variant the current contracts allow is fair.

## Inputs

| Item | Value |
| --- | --- |
| File | `data/eng_Latn/train/000_00083.parquet`, `HuggingFaceFW/finepdfs-edu` @ `9cfabe2127faca99b3d5c4dc6d1fcb397399ebde` |
| Local copy | `C:\XLM-scratch\finepdfs\bench-b2\f00000.parquet.part`, 2,771,021,138 B; `Get-FileHash` sha256 `4eeb58bc769a194f472100ed4acd1d960e35c669caecd96fd4ce6425fabca38d` (equals the recorded shard digest) |
| Read | Parquet footer metadata only (`discover_layout_local`); no record batch decoded, no text printed |
| Operator evidence | `G:\XLM\plans\finepdfs\benchmarks\b3\range-rows{,.evidence}.json` (remote discovery) |

Local discovery with the same seed reproduces the operator's remote result
exactly: `[174000,178000)`, groups `[174,178)`, 4,000 rows, the same three
warnings. Local and remote footers agree.

## 1–2. The refused group and the binding limit

The first refused group after 177 is **group 178** (rows `[178000,179000)`, 1,000 rows).
Groups 179 and 180 are also refused; 181 is usable again.

| Quantity (group 178) | Bytes |
| --- | --- |
| `total_byte_size` (all-column logical/uncompressed) | **51,723,873** |
| all-column compressed / uncompressed | 24,409,755 / 51,723,873 |
| projected (9 adapter columns) compressed / uncompressed | 24,091,206 / 51,187,223 |
| `text` compressed / uncompressed (ratio) | 24,070,838 / 51,159,218 (2.125) |
| largest per-column ratio (`file_path`) | 3.580 |

- Function: `xlm.data.acquisition.sampling._refusal_for_group`, first branch.
- Condition: `total_byte_size > max_parser_bytes`.
- Values compared: `51,723,873 > 33,554,432` (1.54× the bound).
- Not binding: decompression ratio (max 3.58 against 15.0); record bound (the
  sampler never sees it); transfer/request/scan ceilings (rowgroup mode
  checks none); metadata budget (8,388,608 B caps footer discovery only).
- The sampler rule matches `records.check_row_group`, which both range
  readers (`_parquet_selection_exact`, `_parquet_selection_projected`) call
  before decoding. A range plan that covered group 178 would fail at fetch time
  with `RecordLimitError`. The sampler is correct to refuse.
- A projection-aware version of the same rule would still refuse group 178,
  because its projected uncompressed size is 51,187,223 B.

## 3. Bounds that sample-blocks received

`sample-blocks` received `max_parser_bytes=33,554,432` and
`max_decompression_ratio=15.0` as **generic CLI defaults** (`--max-parser-bytes`,
`--max-decompression-ratio`). They are not the FinePDFs source policy. They
equal the FinePDFs policy values only numerically. `max_record_bytes` is not a
sampler input. `metadata_budgets.max_bytes=8,388,608` is the remote footer-discovery
budget and has nothing to do with the record bound.

A related trap: `xlm data plan` without `--limits` gives
`AcquisitionLimits.max_record_bytes = 1 MiB`, not the FinePDFs 32 MiB, and the
CLI has no flag to change it. Any future FinePDFs range plan must use `--limits`.

## 4. Traversal semantics (option A)

`plan_sample_blocks` with one file picks a seeded starting usable group
(`_det_index(... "start")`). `extend_run` then extends only to `index + 1`. At
the first unusable group it marks the file truncated and stops for good.

This contiguity is **required for row-range identity**. It is not an accident.
`AcquisitionPlan.row_ranges` is `dict[str, tuple[int, int]]`: exactly one
interval per file, and it is part of the plan hash. A run that skipped groups
178–180 could not be written as a plan. It would need a plan-schema change
(several intervals per file) and multi-interval runtime support, and so a new
digest.

## 5. Whole-file structure (footer only)

| Statistic | Value |
| --- | --- |
| Row groups | 221 (220 × 1,000 rows, 1 × 407) |
| Usable / refused | 157 / 64 |
| Refusal reasons | 64 × parser byte bound (`total_byte_size`); 0 × ratio; 0 × other |
| Usable / refused rows | 156,407 / 64,000 (refused share **29.0%**) |
| Projected compressed bytes: usable / refused | 1,245,575,171 / 1,476,464,698 (refused share **54.2%**) |
| Refused `total_byte_size` range | 33,703,688 – 91,969,014 |
| Usable runs | 25 runs of 1–9 groups; longest 9,000 rows (`[0,9)` and `[153,162)`) |
| Usable groups after 178 | 31 |
| ≥20 usable groups overall | yes (157) |
| Contiguous 20k usable region | **none** |

The refused groups are not one pathological group. They come in a periodic
pattern: runs of 1–4 heavy groups after every 4–9 usable groups (for example
9–12, 20–22, …, 178–180, 218–219). Full per-group table:
[row-groups.json](../evidence/FINEPDFS-RANGE-ROWGROUP-AUDIT/row-groups.json);
[summary.json](../evidence/FINEPDFS-RANGE-ROWGROUP-AUDIT/summary.json).

## 6. Population bias and sample size

Projected compressed bytes per row (range transfer per row):

| Population | Groups | B/row | CV across groups |
| --- | --- | --- | --- |
| Whole file (what b3 whole-file processed) | 221 | 12,350 | 0.618 |
| Usable under range rule | 157 | 7,964 | 0.348 |
| Refused | 64 | 23,070 | 0.199 |
| Operator block 174–177 (4k) | 4 | 6,205 | 0.163 |
| Production model (2,529,955,258 B / 442,437 rows) | — | 5,718 | single calibration group |

- The range-reachable population is lighter by construction (−36% B/row). It
  systematically excludes the long-document groups, including the rows that
  drove the 32 MiB record bound.
- The 4k block understates per-row transfer of the whole file by about 50%.
  It is 4 contiguous, autocorrelated groups, giving roughly 12–16 range
  requests from which to extrapolate a latency for about 1,337. **4k is
  insufficient.**
- For ±10% (95%) on mean bytes/row under independent group draws: about 47
  usable-only groups, or about 147 groups for the whole population. For ±5%:
  about 186 or about 587. Contiguous groups are positively correlated, so these
  are lower bounds. The local footer gives no per-request latency variance, so
  the latency sample size is **not derived** here.
- The production range estimate (`transport-policy-modeled.json`, finepdfs_edu)
  takes per-row transfer from one light calibration group. It is about 2.2×
  under the file mean. It also assumes every group is range-reachable, which
  this audit refutes. That estimate is not a valid target for a matched
  benchmark.

## 7. Options

| Option | Result |
| --- | --- |
| A — accept 4,000 contiguous rows | Runs, but biased (−50% B/row), too few requests, wrong population. Rejected. |
| B — skip unusable groups, accumulate 20 | Cannot be expressed as one interval per file. Needs a plan-schema change and a new digest. Still samples only the light population. Rejected. |
| C — another contiguous 20k run | No such run exists (max 9,000). Impossible. |
| D — window mode | At most one window per file, so at most 1,000 rows. It is the streamed buffer reader, not the modeled projected range path. Rejected. |
| E — more files or a raised bound | Other files break the matched-file design. Raising 32 MiB is forbidden (Task 8). |

None is fair. The comparison fails on population, not on sample size.

## 8. Identity

No selection behavior was changed, so no plan, sampling or benchmark identity
changed. A range path that could be compared would need a **new range safety
contract**: a streaming, projection-aware per-group check like
`check_window_group` (per-request bytes already split to ≤ `max_parser_bytes`,
projected ratio, decoded bytes charged per batch, per-record 32 MiB) in place of
the whole-group `total_byte_size` rule. That change needs an independent memory
argument, because both range readers currently materialize the whole group
with `list(iter_batches(...))`, and its own reviewed digest. A multi-interval
`row_ranges` would also need a new plan schema. Neither is authorized here.

## 9. Side finding (not fixed)

`ChosenBlock.compressed_bytes` sums `total_byte_size`, which is the logical
uncompressed size. The b3 range evidence reports `compressed_bytes=47,223,137`
for groups 174–177; their actual column-compressed sum is 25,485,067.
`transport_policy.layout_from_calibration` reads that field as the group's
all-column bytes. Fixing the label changes sampling evidence and calibration
inputs, so it needs its own change.

Update: fixed forward and versioned in
[FINEPDFS-WHOLE-FILE-POLICY](FINEPDFS-WHOLE-FILE-POLICY.md) (sampling report
v2; v1 evidence is read unchanged; measured whole-file sizing supersedes it
for FinePDFs).

## 10. CPU note (Task 11, not implemented)

Whole-file processing is file-parallel only:
`process_workers = min(process_workers, len(files))` (`source_benchmark.py:115`,
`source_plan.py:282`). `selected_payloads` walks row groups in sequence in one
process. b3 therefore used one process for 220,407 rows (117.36 s measured,
about 1,878 rows/s). Intra-file row-group parallelism is technically feasible:
groups are independent and readable by index from the local file. It would need
deterministic in-order reassembly of the canonical output, a shared or
partitioned per-file decoded-byte budget, and per-worker memory sized to the
largest group (91,969,014 B logical). Defer until transport policy is frozen.

## Commands run (all exit 0)

```
Get-FileHash -Algorithm SHA256 C:\XLM-scratch\finepdfs\bench-b2\f00000.parquet.part
uv run --offline --locked --extra cpu python docs\implementation\evidence\FINEPDFS-RANGE-ROWGROUP-AUDIT\audit.py C:\XLM-scratch\finepdfs\bench-b2\f00000.parquet.part
uv run --offline --locked --extra cpu ruff check docs\implementation\evidence\FINEPDFS-RANGE-ROWGROUP-AUDIT\audit.py
uv run --offline --locked --extra cpu ruff format --check docs\implementation\evidence\FINEPDFS-RANGE-ROWGROUP-AUDIT\audit.py
```

The audit script regenerated byte-identical evidence on a second run. Tests and
mypy were NOT RUN, because no source code changed. Variability statistics came
from a scratch script over the same footer metadata.

## Next

No live range acquisition. Decide FinePDFs transport policy from what is
already established: `whole_file_local` is the only executable and comparable
mode (`EXECUTABLE_MODES` already excludes `range_selected`). Any later range
reconsideration starts with a reviewed range-v2 safety contract, not a benchmark.
