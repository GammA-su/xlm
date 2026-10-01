# FinePDFs whole-file transport policy and measured sizing

Date: 2026-10-01. Branch `data/mix01-ultrax-6b`, base `c36c797`. Offline only.
No acquisition, payload download, network, tokenizer, C05, training or push.
Nothing was written to the operator store (`G:\XLM`). The real-data rehearsal
ran against a scratch copy of the calibration and the b3 receipt.

Verdict: **FINEPDFS POLICY DIGEST NEEDS OPERATOR REVIEW.** The code can now
freeze the correct decision. The operator must run the two offline commands
below on `G:\XLM` and confirm the printed digests match the rehearsal. The
production inventory then needs its own tool milestone (see "Inventory").

## 1. Contract before this change

`scripts/mix01_source.py policy freeze --basis measured` read **both**
`--whole-receipt` and `--range-receipt`, checked both digests, and built a
`measured_model` for each. `transport_policy.freeze` only required non-empty
inputs. There was no way to represent an alternative that is structurally
non-comparable. Freezing a measured FinePDFs policy would have needed a range
receipt that does not and should not exist. Sizing (`rows_per_file`,
`canonical_bytes_per_row`, file bytes) came only from the 1,000-row
calibration, for both the policy workloads and `source_plan.build_plan`. The
policy record did not bind view, repository or revision. There is no separate
policy authorization: the policy is write-once, and the operator authorizes
the **plan** digest that binds it.

## 2. What changed (generic, no source-name special cases)

| Area | Change |
| --- | --- |
| `transport_policy` | `mix01-transport-policy-v2` records, used only when a measured freeze binds `subject` (source/view/repository/revision), `sizing` and `dispositions`. `mode_disposition(mode, status ∈ {infeasible, non_comparable}, contract, evidence digest + sha256, summary)`. A disposed mode cannot be the selected mode or carry a timing. The v2 reason says the selection is the only measured eligible mode and *not a speed comparison*. `check_frozen` accepts v1 and v2; `check_subject` refuses a policy for another view or revision. `SourceLayout.rows_per_file_measured` (optional) is omitted from records when unset, so v1 report shapes are unchanged. |
| `range_reach` (new) | `reach_audit` builds a footer-only, self-digested `range-v1-reach-v1` record of one verified file. Status is `comparable`, `non_comparable` or `infeasible` under the exact `records.check_row_group` rule. `check_reach` binds the record to the view and the measured benchmark file; `disposition_of` refuses `comparable` (a matched benchmark is then required). |
| `source_plan` | `sizing_from_receipt` builds a self-digested `mix01-whole-file-sizing-v1` measurement from a completed, digest-verified, whole-file benchmark receipt of this exact view. `check_sizing` and `sized_layout` apply it. `build_plan` sizes from a policy-bound measurement, never from calibration, and records `inputs.sizing`. Plans under policies without sizing are unchanged. |
| `sampling` | Block reports are `sampling_plan_version 2`: `compressed_bytes` is the column-chunk compressed sum and `total_byte_size` is explicit. Window reports stay version 1. |
| `mix01_source.py` | `benchmark range-reach --local-file --expected-sha256` (offline, streaming hash, write-once `range-reach.json`). `policy model/freeze --basis measured --whole-receipt R` with exactly one of `--range-receipt` / `--range-reach`. A measured freeze binds subject, sizing and dispositions. `plan` prints its sizing basis. |

## 3. Decision and identity (real-data rehearsal)

Selected mode: **`whole_file_local`**. Basis: the measured, executable,
population-complete fresh b3 whole-file receipt, plus structural evidence that
`range_selected` v1 cannot reach the same population. Frozen reason:
"whole_file_local is the only measured eligible mode (…); range_selected
non_comparable under range-v1-reach-v1; no timing exists for the excluded
modes, so this is not a speed comparison".

| Identity | Value |
| --- | --- |
| Whole-file receipt `performance-01.json` | digest `16b0cc14e13187f06e3d5c26a1dc0fd58e3f47fd5986d5cfd47ad32a8978ad5c`, file sha256 `7d932b867687e449603871a8a5c3bbdbfc6379fb37c52f430a01b2abbefb3960` |
| b3 benchmark / plan hash | `aa539f07…1e5f35` / `55d9a94f…eabeab` |
| Source shard sha256 | `4eeb58bc769a194f472100ed4acd1d960e35c669caecd96fd4ce6425fabca38d` (bound in the reach record) |
| Range reach record | digest **`27ceafe9c32e7a175d852260129204a04dcfff603c2dc81174e4ee81db215780`**, file sha256 `2a82194c64bfafd995374d6ab5fe52e9de21ff93fa739f1da8747eb544755361` |
| Sizing measurement | digest **`4709fb8460c1cca6bc54624ce9e8ad3069fea7434c8a98c9c2eb856f835b2a58`** |
| Transport policy (v2) | digest **`f3a524116f38ac9b49e55d5a33e81ecbc503074f21af9f3fe5263b02a6e7fc73`** |

Expected artifacts:
[expected-range-reach.json](../evidence/FINEPDFS-WHOLE-FILE-POLICY/expected-range-reach.json),
[expected-transport-policy.json](../evidence/FINEPDFS-WHOLE-FILE-POLICY/expected-transport-policy.json).
These were derived from the real shard and receipt, but they are rehearsal
outputs: nothing is frozen until the operator writes them on `G:\XLM`.

Range-v1 disposition: `non_comparable`. 64 of 221 groups are refused (64,000
of 220,407 rows; 1,476,464,698 of 2,722,039,869 projected compressed bytes).
The longest reachable run is 9,000 rows. The old modeled FinePDFs range
estimate (about 2.53 GB, 1,337 requests, 442,437 rows in
`MIX01-HIGH-THROUGHPUT/transport-policy-modeled.json`) is **invalidated**:
the reach record's `invalidates` field and
[a status note](../evidence/MIX01-HIGH-THROUGHPUT/FINEPDFS-RANGE-ESTIMATE-OBSOLETE.md)
mark it, and the v2 policy carries no range candidate. That historical file is
not rewritten.

## 4. Full-file yield and sizing precedence

| Quantity | 1,000-row calibration (group 174) | b3 whole file (measured) |
| --- | --- | --- |
| Rows | 1,000 | 220,407 |
| Accepted / rejected | 1,000 / 0 | 144,125 / 76,282 (0.653904 accepted) |
| Canonical bytes | 10,293,010 | 1,784,575,330 |
| Canonical B/row | 10,293.0 | 8,096.7 |
| Rows per file | 260,417 (estimated from mislabeled logical bytes) | 220,407 (counted) |
| Canonical / raw byte | — | 0.644014 |
| Estimated tokens (canonical / 4) | — | 446,143,832 per file |

Precedence (`mix01-whole-file-sizing-v1`): the whole-file measurement
supersedes the calibration for **rows per file, canonical bytes per row
(acceptance included) and file bytes**. The calibration stays bound for its
schema/adapter evidence (`inputs.calibration` sha256s are unchanged) and for
range-model fields. Exact tokens are known only after tokenizer freeze.

First-pass target 990,000,000 estimated tokens (3.96e9 canonical bytes; ×1.15
margin = 4.554e9):

- About **2.22 benchmark-file equivalents** (990M / 446.14M). This is
  approximate: other inventory files differ in size and yield.
- Measured sizing plans **3 files**: ceil(4.554e9 / 1.7846e9 = 2.55). That is
  5,353,725,990 expected canonical bytes and about 1,338,431,497 estimated
  tokens if the files resemble b3.
- The calibration would have planned **2 files** (2.68e9 per file
  estimated). Two b3-like files yield about 3.57e9, short of the 3.96e9
  requirement.
- Shortfall is still handled by the deterministic top-up (`sufficiency`
  counts sealed canonical bytes, not estimates). A file far larger than b3
  fails closed on the per-file ceilings: `max_rows_per_file` 440,814;
  `max_file_bytes` 5,542,772,736; `max_canonical_bytes_per_file` about
  3.57e9.
- Size-aware sizing (inventory `size_bytes` × 0.644 canonical/raw) is
  recommended for the inventory milestone.

## 5. compressed_bytes defect

Blast radius:

- `ChosenBlock.compressed_bytes` fed only `transport_policy.layout_from_calibration`
  (`group_bytes`, which drives `whole_bytes_per_row` and then `rows_per_file`).
- All six stored rowgroup calibrations (ultrax, finepdfs_en, finewiki_en,
  ifm_general, ifm_planning, simple_stories) are v1, with
  `compressed_bytes == uncompressed_bytes`.
- Only UltraX has a frozen policy, and it is sealed.

Fix and containment:

- The sampler now emits v2 (true compressed bytes plus explicit
  `total_byte_size`).
- `layout_from_calibration` reads v1 unchanged (historical layouts
  reproduce), reads v2 as compressed, and refuses unknown versions.
- For FinePDFs, the false number no longer drives the plan: measured sizing
  supersedes `rows_per_file`, and `whole_bytes_per_row` is derived from the
  measurement.
- Correcting the label alone would not have helped FinePDFs: with true
  compressed bytes (5,872,818 for group 174), rows per file would read
  471,840. Both readings of one light group are wrong; only the whole-file
  count is right.
- Still open: v1 calibrations of other sources still size **modeled**
  policies. Their measured freezes (benchmark first) now supersede that.

## 6. Historical identities

- UltraX `transport-policy.json` (v1, measured,
  `28c90174…6bd4`) verifies under the new `check_frozen`.
- UltraX `p01/plan.json` (`454395fb…45fb`) reproduces its acquisition plan
  hash.
- Rebuilding p01 from its frozen inputs gives `60945c41…601e` with **both**
  HEAD `c36c797` code (exported with `git archive`) and this change. The drift
  from the stored digest is the earlier processing-growth commit
  (`processing_growth`, scratch/output caps), not this milestone.
- The UltraX policy has no sizing, so its plans are sized exactly as before.

## 7. Planner enforcement

- `EXECUTABLE_MODES = LOCAL_MODES` (whole_file_local, row_group_local,
  small_source_direct). `range_selected` is never selectable (refusal "no
  production planner executes this mode").
- `build_plan` refuses any non-local frozen mode.
- v2 additionally refuses a disposed selected mode and a policy for another
  view or revision.
- No range execution was added.

## 8. CPU / intra-file parallelism (not implemented)

Changing whole-file processing from one process per file to row-group workers:

- **Transport policy:** no change. The v2 policy binds the receipt's measured
  model (`process_workers 1`), and selection does not depend on it (one
  eligible mode).
- **Adapter:** no change if the same adapter code path runs.
- **Canonical output:** unchanged only if documents and rejections are written
  in the same row order (byte-identical). This must be proven on the local
  shard against the offline b3 receipt (144,125 documents, 1,784,575,330
  canonical bytes, per-unit digests).
- **Production plan:** unchanged only if it is a run-time worker option within
  the existing limits. Per-worker decode memory or a new limits key
  (row-group workers, processing growth) changes the plan digest.
- **Executable/compatibility hash:** none is bound by Mix-01 source plans or
  receipts. Only Essential-Web uses compatibility records.

Recommendation: implement and prove byte identity **before generating the
production plan digest**, because the likely limits changes enter the plan.
It must land before production acquisition at the latest. Afterwards it is
safe only if byte-identical and bound-neutral.

## 9. Inventory and production commands

There is no repository tool that lists a pinned Hub directory completely. The
FinePDFs probe observed 2 top-level files, and
`essential_web_recon.HubTreeLister` is hardwired to Essential-Web. **Next
milestone:** a generic, allowlisted, paginated, byte- and request-bounded
lister (repository, revision, path prefix `data/eng_Latn/train/`) that writes
the candidate list and an LFS size map for `mix01_inventory.py freeze --sizes`.
Size-aware sizing should come with it. Production planning is blocked on it.

Operator, now (offline, `G:\XLM`, writes two write-once files):

```powershell
. .\scripts\operator_storage.ps1
uv run --offline --locked --extra cpu --extra eval python scripts/mix01_source.py benchmark range-reach --source-key finepdfs --label b3 --local-file C:\XLM-scratch\finepdfs\bench-b2\f00000.parquet.part --expected-sha256 4eeb58bc769a194f472100ed4acd1d960e35c669caecd96fd4ce6425fabca38d
# expect: RANGE REACH DIGEST 27ceafe9c32e7a175d852260129204a04dcfff603c2dc81174e4ee81db215780
uv run --offline --locked --extra cpu --extra eval python scripts/mix01_source.py policy model --source-key finepdfs --basis measured --whole-receipt G:\XLM\plans\finepdfs\benchmarks\b3\performance-01.json --range-reach G:\XLM\plans\finepdfs\benchmarks\b3\range-reach.json
uv run --offline --locked --extra cpu --extra eval python scripts/mix01_source.py policy freeze --source-key finepdfs --basis measured --whole-receipt G:\XLM\plans\finepdfs\benchmarks\b3\performance-01.json --range-reach G:\XLM\plans\finepdfs\benchmarks\b3\range-reach.json
# expect: TRANSPORT POLICY whole_file_local (measured) digest f3a524116f38ac9b49e55d5a33e81ecbc503074f21af9f3fe5263b02a6e7fc73
# STOP: review the policy digest; a different digest means an input differs.
```

After the inventory-lister milestone produces the list (network, separately
authorized):

```powershell
uv run --offline --locked --extra cpu --extra eval python scripts/mix01_inventory.py freeze --source finepdfs_edu --repo HuggingFaceFW/finepdfs-edu --revision 9cfabe2127faca99b3d5c4dc6d1fcb397399ebde --seed 20260918 --files G:\XLM\inventories\finepdfs_candidates.txt --sizes G:\XLM\inventories\finepdfs_sizes.json --output G:\XLM\inventories\finepdfs.inventory.json
# verify: an identical second freeze to scratch must be byte-identical
uv run --offline --locked --extra cpu --extra eval python scripts/mix01_inventory.py freeze --source finepdfs_edu --repo HuggingFaceFW/finepdfs-edu --revision 9cfabe2127faca99b3d5c4dc6d1fcb397399ebde --seed 20260918 --files G:\XLM\inventories\finepdfs_candidates.txt --sizes G:\XLM\inventories\finepdfs_sizes.json --output C:\XLM-scratch\finepdfs-verify\finepdfs.inventory.json
uv run --offline --locked --extra cpu --extra eval python scripts/mix01_source.py plan --source-key finepdfs
# prints "sizing basis whole-file measurement 4709fb84…" and PLAN DIGEST
# STOP - USER MUST REVIEW PLAN DIGEST BEFORE AUTHORIZATION
```

`plan` re-verifies the inventory (order keys, digest, source/repository/revision)
and the policy subject and sizing. Its estimates come from the measurement.
Do not authorize or run the plan from this report.

## Requirement ledger

| Requirement | Status |
| --- | --- |
| Measured whole-file + structural range disposition in policy | IMPLEMENTED, VERIFIED (unit, CLI, real-data rehearsal) |
| No fabricated range receipt or timing | VERIFIED (disposed mode cannot carry a timing) |
| Policy binds source/view/revision/receipt/reach evidence/mode/disposition | VERIFIED |
| Historical UltraX policy/plan identities | VERIFIED (v1 verifies; p01 hash reproduces; rebuild identical to HEAD code) |
| Full-file sizing precedence over calibration | IMPLEMENTED, VERIFIED |
| Calibration preserved (not deleted or rewritten) | VERIFIED |
| compressed_bytes semantics (forward fix, versioned) | IMPLEMENTED, VERIFIED |
| Old FinePDFs range model invalidated | IMPLEMENTED (note + reach `invalidates`) |
| Planner cannot select range_selected | VERIFIED |
| Operator policy freeze on G:\XLM | NOT RUN (operator action) |
| FinePDFs inventory | BLOCKED (needs lister milestone) |
| Production plan digest | BLOCKED (inventory) |
| Intra-file CPU parallelism | OUT OF SCOPE (assessed only) |

## Tests and checks (exit 0 unless noted)

```
uv run --offline --locked --extra cpu --extra eval python -m pytest tests/test_source_plan.py tests/test_mix01_source_cli.py tests/test_range_reach.py tests/test_parquet_window_sampling.py tests/test_rowgroup_sampling.py tests/test_source_growth_integration.py tests/test_source_run.py tests/test_source_record_bound.py tests/test_source_archive.py tests/test_mix01_admission.py tests/test_certified_evidence.py tests/test_essential_web_bulk.py tests/test_essential_web_recon.py tests/test_operator_driver_window.py tests/test_calibration_adopt.py tests/test_parquet_window_nested.py -n 12 --dist=worksteal --max-worker-restart=0 -m "not serial" --basetemp C:\pt-xlm3 -p no:cacheprovider
  -> 255 passed (same selection with -m serial: 0 selected, exit 5)
uv run --offline --locked --extra cpu ruff check <changed files>; ruff format --check <changed files>
uv run --offline --locked --extra cpu mypy --strict src/xlm/data/acquisition/{sampling,transport_policy,source_plan,range_reach}.py scripts/mix01_source.py  -> no issues
git diff --check -> clean
```

Two existing assertions changed deliberately: the rowgroup report version
moved from 1 to 2 (`test_report_bias_and_no_paths`,
`test_legacy_reports_carry_no_window_keys`). Both now also assert the new
fields and that window reports stay at version 1. This is a full focused
selection, not a full-suite pass: the full offline acceptance suite was NOT
RUN. Test files are outside the repository mypy gate; they have pre-existing
errors on untouched lines.
