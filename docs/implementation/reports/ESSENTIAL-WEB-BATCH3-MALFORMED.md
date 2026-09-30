# Essential-Web Batch 3: f00110 malformed stop

2026-09-30. Offline diagnosis and repair of the automatic runner's Batch-3 stop.
Exact commands, exit codes and the store read-only proof are in
[COMMANDS.md](../evidence/ESSENTIAL-WEB-BATCH3-MALFORMED/COMMANDS.md).

**Verdict: READY TO RESUME AUTOMATED ESSENTIAL-WEB ACQUISITION**, through
one reviewed Batch-3 resume with the fast operator driver, then a new
`PrepareAuto`. The earlier envelope `21cbad8c…d9e` is bound to the previous code,
so it is refused.

## What happened

f00110 is not an abnormal file. 42 of its 78,689 rows are malformed (0.053%),
inside the frozen 1% budget and typical of the 123 sealed files (0.048% pooled).
Three of those rows fall in the first 283 rows (row indices 202, 242 and 282).
The worker applied the per-row rule to every prefix of the file, so 3/283 =
1.06% stopped the whole file at row 282. That rule was written for the
2,048-row calibration windows. The fast campaign made each whole file one pass,
and on a file of about 79,000 rows the rule stops on any early cluster, whatever
the file's own fraction.

## Authoritative Batch-3 state (verified offline)

`resume-check --batch 3` on the store, same result before and after the change:

| | |
|---|---|
| Sealed (skip) | 27 / 32 (84.4%) |
| Local reuse | f00110 (retained on the durable volume), f00123 (complete scratch copy) |
| Partial resume | f00122, f00125, f00126: 603,979,776 verified bytes kept |
| Fresh download | 0 |
| Remaining network bytes | 213,131,186 (the three partial tails) |
| Charged / ceiling | 8,476,924,052 / 25,769,803,776 B |

Runner journal: batch 3 started `CLEAN_NOT_STARTED`, exited 1, `fatal_stop`
`HUMAN_REVIEW_REQUIRED`. Batch journal root failure: f00110
`MalformedLimitError` at `malformed.py:23`. f00122, f00125 and f00126 were
cancelled by that failure.

## f00110 identity

| | |
|---|---|
| Inventory rank | 110 (Batch 3, key `f00110`) |
| Source path | `data/crawl=CC-MAIN-2024-26/train-00549-of-03168.parquet` |
| Repository / revision | `EssentialAI/essential-web-v1.0` @ `ce4eccc7e9604667b6d7f32cb6274b8b41f3113d` |
| Local file | `G:\XLM\acq-raw\ew-fast\source\data\crawl=CC-MAIN-2024-26\train-00549-of-03168.parquet` |
| Length | 245,401,594 B |
| SHA-256 | `968bedb4644070246d01fbd7424b164f88e81384947808edb84a1d91d88bd4ea` (rehashed; equals identity record and linked ETag, independently verified) |
| ETag (xet) | `ade654c66d8fb3424f7b5a8205107d79f10b5f98001ada8c8ba3db863180c937` |
| Rows / row groups | 78,689 / 8 |

## Offline replay (exact production row path)

[`replay_unit.py`](../evidence/ESSENTIAL-WEB-BATCH3-MALFORMED/replay_unit.py)
streams the retained file with the unit job's own limits through
`selected_payloads` and the three production `essential_web_bnormal` adapters.
Unlike the worker, it records where the frozen counter first raises and then
keeps counting. It outputs no text.

| Outcome | Rows |
|---|---:|
| Accepted science | 852 |
| Accepted practical | 2,569 |
| Accepted prose | 7,342 |
| Unassigned | 801 |
| Rejected at the gate | 67,083 |
| Malformed (validity stage, final `rejected`) | 42 |
| **Total** | **78,689** |

Malformed fraction 0.0534%. **First abort: row index 282** (283 rows seen, 3
malformed), identical in all three views.
[`f00110-replay-before.json`](../evidence/ESSENTIAL-WEB-BATCH3-MALFORMED/f00110-replay-before.json)
lists each malformed row with its index, reason codes, selector stage/final,
field path, value type, UTF-8 size and a bounded label summary.

### Grouped reasons

| Reason (evaluator codes) | Values | Rows | % of f00110 | Gate on the row's valid fields | Frozen semantics |
|---|---|---:|---:|---|---|
| `invalid_fdc_syntax` + `unknown_label:k` | FDC `-1`, knowledge `Abstain` | 19 | 0.0241 | rejects all 19 | yes: validity, fail-closed, counted malformed |
| `unknown_label:k` | `Metacognitive` 11, `Abstain` 7 | 18 | 0.0229 | rejects 11, passes 7 | yes, same |
| `invalid_fdc_syntax` | `641.563.2` 2, `812.54812.54`, `363.3220976.7`, `92` | 5 | 0.0064 | rejects 4, passes 1 | yes, same |

All values are strings (2–13 bytes). None is a missing field, a non-string
label, bad UTF-8, a renderer failure or container corruption. Every one is
already a documented class: the production calibration recorded
`unknown_label:k` and `invalid_fdc_syntax` as "evaluator validity failures that
fail closed by design" and kept the threshold
([calibration report](ESSENTIAL-WEB-PRODUCTION-CALIBRATION.md#malformed-rows)).
Every such row is `rejected` under B-normal whatever the budget does. For 34 of
the 42, the gate would reject the row on its valid fields anyway. For the other
8, the component would depend on the unknown label, so the frozen policy
correctly refuses to guess.

### The 123 sealed units

[`sealed-malformed-profile.json`](../evidence/ESSENTIAL-WEB-BATCH3-MALFORMED/sealed-malformed-profile.json)
(ledgers, row numbers and reason codes only): 4,965 malformed of 10,253,689 rows
(0.048%); per-file maximum 0.089%. The same reason families recur, plus
`unknown_label:m`, `:a`, `:t`, eight `missing_label:d` and four renderer
`essential_web_unusable_record`. In several sealed files one malformed row fell
within the first 100 rows, so one more nearby would have stopped them. The stop
is a recurring operational hazard of the prefix rule, not a property of f00110.

## Classification and decision

- **Not corruption and not schema drift**: 0.053% of rows, known value shapes,
  valid container, correct row accounting.
- **Not an adapter bug**: the adapter and evaluator do exactly what their frozen
  contract says.
- **Valid source rows rejected by B-normal (category C)**: `Metacognitive` is a
  real Bloom knowledge level outside the frozen universe; `Abstain` and FDC
  `-1` are classifier abstentions; the garbled codes are invalid classifier
  output. The frozen policy *intentionally* fails these closed as validity
  failures and records them as malformed. **This is kept.** Reclassifying them
  as ordinary policy rejections would change `mix01_adapters.py` and therefore
  the frozen adapter code identity (`adapter_code_sha256`) that the campaign,
  its 123 receipts and the calibration seal bind. The rejection codes of new
  ledgers would also differ from sealed ones. The evidence does not need it:
  the rows use about 5% of the 1% budget.
- **The defect is where the 1% budget was judged.** The budget is per file pass
  ([readiness](ESSENTIAL-WEB-PRODUCTION-READINESS.md#malformed-rows): "thresholds
  apply independently to each file pass"), but the worker judged it on every
  prefix.

**Versioned amendment `essential-web-malformed-whole-pass-v1`** (local worker
only): the frozen `MalformedCounter` still observes every row and must fire,
and its stop stands only once malformed rows exceed 1% of the whole pass. That
is the earliest row from which the pass's final fraction can no longer be ≤1%.
Consequences, all tested:

- every stop is one the frozen rule also makes (never stricter), and the
  decision at the end of the pass is exactly the frozen rule's;
- a genuinely >1% file still stops, as soon as the outcome is certain
  (11 of 1,000 rows stops at the 11th);
- corruption or schema drift (every row bad) still stops within the first 1%
  of the file (row 787 of 78,689);
- a pass under 100 rows behaves exactly as before (third malformed row);
- the threshold is not raised: still 1%, still per file, still counted
  malformed.

`malformed.py`, the adapters, the evaluator, the policy spec and the rejection
ledger format are byte-identical. `xlm data adapt` keeps the frozen per-row rule
(it is not the fast campaign's path).

## Scientific invariants

Unchanged, verified: source revision, B-normal thresholds and taxonomy
universes, science/practical/prose precedence, mixture quotas, selector
freeze/policy/evaluator digests, adapter code identity (the campaign loads
only if `sealer.code_identity()` equals the frozen hashes, and it loads; the
envelope comparison shows `adapter` and `selector` equal). Every row rejected
before is still rejected. Documents and ledgers are byte-identical to the
frozen adapters' oracle (test) and deterministic across runs.

## f00110 before and after (production worker, offline, private output)

[`complete_unit_offline.py`](../evidence/ESSENTIAL-WEB-BATCH3-MALFORMED/complete_unit_offline.py)
calls `essential_web_local.adapt_source_file` with the job the executor builds
for this unit, into a scratch directory outside the store, and then deletes it.
Nothing is sealed or published.

| | Before (HEAD `22517b2`) | After |
|---|---|---|
| Outcome | `MalformedLimitError` after 0.28 s | completed, 36.8 s |
| Malformed | stop at 3 / 283 | 42 / 78,689 (0.053%) |

| Component | Documents | Canonical bytes | Estimated tokens (4 B/token) |
|---|---:|---:|---:|
| essential_science | 852 | 6,174,756 | 1.54M |
| essential_practical | 2,569 | 13,778,940 | 3.44M |
| essential_prose | 7,342 | 34,339,602 | 8.58M |

Counts equal the replay. Selected-record stream SHA-256
`da142896…6e83`. Token figures use the campaign's 4 B/token estimate; they are
not tokenizer counts.

## f00123

`data/crawl=CC-MAIN-2015-32/train-00773-of-01920.parquet`, complete scratch
copy `C:\XLM-scratch\ew-fast\b0003\f00123.parquet.part`, SHA-256
`36bbae09…c8eb` (rehashed), 75,741 rows. 31 malformed (0.041%): `unknown_label:k`
18, `invalid_fdc_syntax` 7, `unknown_label:m` 6 (`Abstain`). **No stop under
either rule.** The production worker completed it offline: science 303 /
2,431,364 B, practical 1,265 / 8,780,602 B, prose 5,323 / 27,806,583 B.
**Ready for local processing**; the resumed run seals it.

## Restart

The classification is unchanged by the fix (`resume-check-after.json`):
**sealed_skip 27, local_reuse 2 (f00110, f00123), partial_resume 3 (f00122,
f00125, f00126), fresh_download 0**, 603,979,776 verified bytes kept. The 27
seals remain valid: their outputs do not depend on the malformed decision, and
the amendment never stops a file the old rule let through.

f00110 is not completed before the restart. Sealing belongs to the executor.
Offline `Resume` refuses this batch because three sources still need their
partial tails and f00123 is a scratch copy, not a retained source. The normal
`Run` reuses f00110 and f00123 without any transfer and fetches only the three
tails (213,131,186 B) under the existing Batch-3 authorization.

## Authorization and envelope

| | Changed? |
|---|---|
| Campaign digest `8e42ba31…bb8c` | no |
| Batch-3 membership `bb7fd547…4480`, plan, authorization `f25da873…f56c` | no (child re-derived identically) |
| Recovery amendment | no |
| Running code | yes: `essential_web_local.py`, `essential_web_recovery.py` |
| Auto envelope `21cbad8c…d9e` | **invalid**: differs at `code.running_sha256` (those two files) and `code.compatibility.records` |

The campaign loads because an additive, digest-sealed code-compatibility record
[`code-compatibility.json`](../evidence/ESSENTIAL-WEB-BATCH3-MALFORMED/code-compatibility.json)
(`essential_web_malformed_whole_pass_v1`, digest `9c9b618b…7f18`) continues
exactly from the Windows-fix record and changes only those two files. `RunAuto`
refuses the old envelope before it does anything (tested). A new `PrepareAuto`
binds the new code; the batch children do not change.

The runner still refuses Batch 3 by design: its failure mark matches the
unchanged batch state, and its instruction is "review it, then resume with the
fast operator driver". That reviewed resume is the one manual step.

## Operator commands

```powershell
. .\scripts\operator_storage.ps1
.\scripts\operator_essential_web_fast.ps1 -Stage ResumeCheck -Batch 3   # offline: 27 / 2 / 3 / 0
.\scripts\operator_essential_web_fast.ps1 -Stage Run -Batch 3           # reviewed resume; already authorized
.\scripts\operator_essential_web_campaign.ps1 -Stage Status             # expect batch 4 CLEAN_NOT_STARTED
.\scripts\operator_essential_web_campaign.ps1 -Stage PrepareAuto -MaxBatches 20
.\scripts\operator_essential_web_campaign.ps1 -Stage RunAuto -Authorize <new AUTO AUTHORIZATION DIGEST>
```

Do not pass `-Authorize 21cbad8ca0de96148b094c14753187145ba0558481a17b4b9ab6a2bd4527ad9e`;
it is refused.

## Tests

New [`tests/test_essential_web_malformed_whole_pass.py`](../../../tests/test_essential_web_malformed_whole_pass.py)
(16, authored fixtures, no network):

- each f00110 value shape stays `EssentialWebMalformedRowError` with its frozen
  reason code and selector `rejected` / `validity`;
- a gate rejection never uses the budget, and a renderer-level missing field
  still counts;
- the exact f00110 condition: the real 42 row positions stop the frozen rule at
  row 282 and pass the amendment;
- above 1% still aborts; every-row-bad aborts at row 787; a tiny pass stops at
  its third bad row;
- property check over 1,500 random patterns: every amended stop is a frozen
  stop, and for passes of 100 or more rows it stops exactly when the whole pass
  exceeds 1%;
- the worker completes a dense prefix and writes documents byte-identical to
  the frozen adapters, with ledger rows and codes unchanged; 4 of 300 still
  aborts;
- the Batch-3 restart plan: two local reuses, three partial resumes, zero fresh
  downloads;
- the runner stops for human review on the malformed stop, starts nothing
  again, and after the reviewed driver resume (no source transferred again)
  continues to the next batch;
- an envelope bound to the previous code is refused at exactly the three
  identity paths; a new envelope has identical children and runs;
- the chain accepts the amendment only with its own two files and in order, and
  the committed record binds the running code.

`test_committed_windows_fix_binds_the_running_code` now checks that the Windows
record is the new record's predecessor, since it is no longer the last link.

Related selection: 802 passed (parallel) + 3 serial, 0 skipped; ruff, format
and strict mypy pass. Fast and full repository selections and CUDA: NOT RUN.

## Requirement ledger

| Requirement | Status |
|---|---|
| Authoritative Batch-3 state from receipts, journal and store | VERIFIED |
| f00110 identity and rehash | VERIFIED |
| Full offline replay, text-free per-row malformed detail | VERIFIED |
| Grouped reasons with gate outcome | VERIFIED |
| Root cause (prefix evaluation of a per-pass budget) | VERIFIED |
| Whole-pass amendment in the local worker | IMPLEMENTED, VERIFIED (fixtures and real file) |
| Frozen classification, selector, adapters, quotas unchanged | VERIFIED |
| f00110 completes with the production worker offline | VERIFIED (private output, not sealed) |
| f00123 has no blocker | VERIFIED |
| Restart 27 / 2 / 3 / 0 | VERIFIED |
| Code-compatibility record; campaign loads | IMPLEMENTED, VERIFIED |
| Old envelope refused; new envelope required | VERIFIED (tests, read-only real comparison) |
| Operator store untouched | VERIFIED (1,601 files, 0 differences) |
| Batch-3 resume, new `PrepareAuto`, `RunAuto` | NOT RUN (operator; network) |
| Reclassifying unknown labels as policy rejections | OUT OF SCOPE (frozen contract kept) |
| Fast/full repository selections, CUDA | NOT RUN |
| C05, tokenization, training | NOT RUN |

## Limitations

- The amendment covers the fast campaign's local worker. The same prefix rule
  still governs `xlm data adapt --adapter essential_web_bnormal`, which the
  campaign does not use.
- A file that ends above 1% is now refused at the row where its malformed count
  crosses 1% of the whole file, not at the first dense prefix. At worst that is
  the whole file (about 35 s of local processing measured here) before the
  refusal. The refusal, its human-review stop and the absence of any
  publication are unchanged.
- Batch-3 wall time will include one extra local pass over f00110 and f00123
  (about 35 s each, measured).

## Files changed

- `src/xlm/data/sources/essential_web_local.py`: `observe_malformed`,
  `MALFORMED_POLICY`
- `src/xlm/data/sources/essential_web_recovery.py`: third compatibility record
- `tests/test_essential_web_malformed_whole_pass.py` (new)
- `tests/test_essential_web_windows_publication.py`: chain-link assertion
- `docs/implementation/evidence/ESSENTIAL-WEB-BATCH3-MALFORMED/` (record,
  scripts, JSON evidence, logs)
- this report, `docs/implementation/STATUS.md`, `docs/runbooks/windows.md`
