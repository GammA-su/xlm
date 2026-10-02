# C05 matcher v4: exact whole-item fallback and lossless dedup (2026-10-02)

Starting HEAD `2f929220834aa9edb36901e09f7fee5ffc8b8836` on `feat/c05-global-preparation`.
Authored synthetic fixtures only. No network, no real benchmark material, no access
to `X:\C05-Protected`, no real C05 preparation or scan, no push.

## Why v4 exists

The operator's content-free audit of the real protected inventory (76 files, 153,182
items) found 813 items with zero frozen v3 signatures (ARC-Easy 1, BLiMP 362, PIQA
450, HellaSwag 0). `verify_benchmark()` refuses a protected receipt with
`items_without_patterns > 0`. The operator measured that a label-free whole-item
composite clears 4 tokens / 16 characters / 3 distinct for all 813. These real
measurements were **not** reproduced or tested by the agent.

## Contract

`c05-matcher-v3` (`MatcherPolicy`) is unchanged: identity
`cc696e54…3bb0`, `ProductionPolicy()` identity `3a67baa3…bd4f` (pinned in tests).
v3 never consults a fallback.

`c05-matcher-v4` (`MatcherPolicyV4`) has every v3 field with the same defaults, plus:

| field | value |
| --- | --- |
| `version` | `c05-matcher-v4` |
| `renderer` | `task-render-v3` (normal renderer unchanged) |
| `fallback_renderer` | `item-composite-v1` |
| `fallback_mode` | `exact-whole-item-only` |
| `fallback` | `{tokens: 4, characters: 16, distinct: 3}` |

Policies parse through `matcher_policy()` (discriminated on `version`, no implicit
version). `ProductionPolicy.matcher` accepts either version; its default stays v3.

Per item: the v3 normal signatures are computed as before. If there is at least one,
v4 emits exactly those (no fallback). If there are none, v4 builds the composite,
match-view-v1 normalizes it and checks the floor (`tokens-with-two-letters-v1` for
distinct). If it passes, v4 emits **one** exact pattern over all composite tokens
(no sliding windows, even past `span_tokens`), with provenance `<ref>:item_fallback`.
If it fails, the item stays unsigned. Frozen background phrases suppress the fallback
just as they suppress normal signatures.

### item-composite-v1 (publisher field order, one-space join, no separator token)

* ARC-Easy: `question` + every `choices.text`.
* BLiMP: `sentence_good` + `sentence_bad`.
* PIQA: `goal` + `sol1` + `sol2`.
* HellaSwag: `ctx` if present, else `ctx_a` + `ctx_b`; then every `endings` entry.
  `activity_label` and the lm-eval preprocessed variants stay with the normal
  renderer only.

It never reads `answerKey`, `label` or any score. Tests use a row mapping that fails on
any label access, and vary the label values. Limitation: the composite protects mainly
against row/item-level leakage. Normal signatures give stronger partial-span detection.

## Lossless dedup and `benchmark_patterns`

Equivalence relation: two emissions of one item are equal when their
`(normalized tokens, provenance)` pair is equal. Removing duplicates is lossless for
these reasons. The index holds one line per distinct `(digest(tokens), provenance)`,
and SQLite already dropped such duplicates (`INSERT OR IGNORE` / `ON CONFLICT DO
NOTHING`). Every provenance embeds `digest([material entry, row number])`, which is
unique per item because material paths are unique. Same tokens with different
provenance, or from different items, are kept.

As a result, emitted patterns equal the final `index.jsonl` line count, and `build`
now asserts this (`protected index/emission count invariant`). `benchmark_patterns` now
bounds those emitted records, which are exactly what `StreamingMatcher` consumes.
Before, it bounded raw duplicate emissions. Raw computation keeps independent hard
bounds: `max_item_variants` × `spans_per_variant` per item (plus at most one fallback),
`records`, `stage_seconds` and `ram_bytes`.

Authored HellaSwag-style duplication fixture (`ctx` ≈ `ctx_a`+`ctx_b`, `[title]`
variant, activity label): 52 variants, **114 raw candidates → 47 emitted**. The v3
index bytes equal an independent reference that applies the old raw-emission
semantics. The existing golden index from the pre-parallel HEAD `6863bc0` is still
reproduced exactly.

## Protected refusal

In protected mode, `build` refuses with `protected benchmark items without frozen
signatures` after all material is processed and before `index.jsonl`, the receipt or
the export are written. `PREPARATION-INCOMPLETE` remains. Authored mode still counts
unsigned items in the receipt.

## Audit command

`benchmark-audit-local --spec --material-root --policy --resources [--workers 1..16]
[--progress-interval S | --no-progress]`. It uses the same bounded workers and
file-identity checks as `build-local`, writes nothing and needs no key. Stdout is one
JSON object with totals, per-task counts, and unsigned counts by task/config/split.
Counts include files, items, normal/fallback-signed, unsigned, raw candidates, emitted
after dedup and deduplicated emissions. It also reports `benchmark_patterns_ceiling`
and whether it is met. The command exits 0 when every item is signed and 2 otherwise.
`benchmark_patterns` is reported, not enforced, so the operator can size it.
Time/RAM/record ceilings are enforced. Progress (stderr) adds `fallback items N`.

## Evidence

| requirement | status |
| --- | --- |
| v3 identity/behavior historical | VERIFIED (pinned identities, golden index) |
| v4 fallback only on unsigned items, one exact pattern, 4/16/3 floor | VERIFIED (authored) |
| label independence (ARC/PIQA/HellaSwag/BLiMP) | VERIFIED (guarded mapping) |
| lossless dedup; v3 index bytes unchanged | VERIFIED (authored) |
| v4 workers 1/2/4/16 byte-identical index + receipt | VERIFIED (authored) |
| protected refusal before receipt/export | VERIFIED (authored, separate-principal) |
| audit content-free, writes nothing, correct counts | VERIFIED (authored) |
| real inventory: 813 → 0 unsigned under v4 | NOT RUN (operator measurement only) |

Commands (exit 0 unless noted):

* `uv run --offline --locked --extra cpu --extra eval python -m pytest tests/test_c05_matcher_v4.py -n 0`: 24 passed.
* All C05 tests: `pytest tests/test_c05_*.py -n 16 --dist=worksteal --max-worker-restart=0` gave 210 passed, 1 failed (exit 1). The failure is
  `test_deadline_is_enforced_while_workers_run`: under 16-way load, spawning workers
  takes longer than its 2 s deadline. The same test passed serially (`-n 0`), along
  with the whole parallel file (21 passed). It is load-sensitive timing, not a weakened assertion.
* `ruff format --check src tests`, `ruff check src tests`,
  `mypy --strict src/xlm/data/exclusion tests/test_c05_matcher_v4.py`, `git diff --check`: clean.

The full repository suite was not run (not the acceptance gate).

## Operator next steps (not run by the agent)

1. Freeze a v4 matcher policy JSON (`{"version": "c05-matcher-v4"}` plus restated
   fields) and audit: `… operator benchmark-audit-local --spec X:/C05-Protected/material-spec.json --material-root X:/C05-Protected/material --policy <v4-policy.json> --resources <resources.json> --workers 16`.
   It must report `items_without_patterns: 0`.
2. Set `benchmark_patterns` from `totals.emitted_patterns_after_lossless_dedup` (with a
   reviewed margin). Check `automaton_nodes`. Sign a new resource and production-policy
   decision that binds the v4 matcher.
3. Rerun `build-local` with the v4 policy into a fresh destination.
