# C05 contamination policy v2 (`c05-production-v3`): production implementation (2026-10-06)

Branch `fix/c05-contamination-policy-v2`, from `f883210` (the committed production
counterfactual tool). Code, tests and documentation only. Nothing was run on X: or on
production C05; no key was used; no recipe, quota, split, cleaning or acquired data
changed.

## 1. Result

| item | value |
|---|---|
| matcher | protected index generated UNCHANGED by `c05-matcher-v4` (`c053e711…`); `c05-trigger-floors-v1` decides which patterns exclude |
| trigger floors | prompt 8/40/5, item fallback 8/40/5; sentence 3/12/3, answer 8/40/5, combined 8/40/5 unchanged; rule: a pattern is active if ANY of its provenance kinds meets that kind's floor |
| trigger digest (reviewed 821 uncovered items) | `946cec19cacbc890d97eb2d5ea42471fd00fb41d27afcdbefd21916184b3c7cc` |
| production policy `c05-production-v3` digest (clean-v1 values + v2 fields) | `572c0a1eea8a31358ee43573cb89c69825b998bf4d4bb6786cc726e50285f0bb` |
| contamination exclusion family | `query-seed-derivation-family-v1` (versioned field of the policy, bound by its digest) |
| split-leakage family | `known-lineage-v3`, unchanged; separate field, file and membership column |
| benchmark index/receipt | rebuild REQUIRED (code identity binding), with the UNCHANGED generation policy; the index must be byte-identical to clean-v1's `5c9f777d…` |
| recall gate | new read-only `trigger-recall-local`; expected 821 uncovered, 152,361/153,182 whole items, 66,472/67,000 BLiMP single sentences |
| expected C05 decisions | exactly the audited `prompt8 x query_seed_family`: kept 14,927,848, excluded 38,364, duplicates 120,995 |
| expected runtime | protected rebuild ~20 min, recall gate ~10 min, C05 ~2 h 05 min (clean-v1 took about 2 h 01 min) |

## 2. Production evidence, independently re-verified

Read from the operator's content-free outputs `C:/XLM-scratch/c05-policy-audit.json` and
`c05-policy-supply.json` (produced by `f883210`):

* every reproduction invariant is true:
  * 682,086 rescanned first hits;
  * 1,750,340 rebuilt lineage documents.
* Direct-hit documents:

  | matcher | direct-hit documents |
  |---|---:|
  | current | 682,086 |
  | prompt8 | 19,427 |
  | floor8_pair | 6,677 |

* Whole-item injected copies detected:

  | matcher | detected / items |
  |---|---|
  | current | 153,182 / 153,182 |
  | prompt8 | 152,361 / 153,182 |
  | floor8_pair | 149,640 / 153,182 |

* BLiMP single sentence: current 66,472 and prompt8 66,472 (floor8_pair 29,771).
* Worst pattern: prompt, HellaSwag `ctx_b` (`published_subfield_fragment`), 4 tokens,
  16 characters, 1 item, in 425,170 documents (411,080 first hits), 17/17 allocations.
* Index-level items without a standalone signature under prompt8:

  | task | items |
  |---|---:|
  | ARC | 1 |
  | BLiMP | 362 |
  | PIQA | 458 |
  | **total** | **821** |

  This equals the whole-item misses exactly.
* `prompt8 x query_seed_family` (old tokenizer) meets all 17 frozen quotas:
  * remaining deficit 0;
  * SYNTH 932,782,701 of 900,000,000;
  * essential_science 629,681,786 of 600,000,000;
  * LibreTexts 58,002,762, OER 10,654,487, PressBooks 54,387,520, Gutenberg 225,986,050,
    PDR 1,537,132.

No reason to reject prompt8 was found. Its known cost, 821 tiny items without any
standalone signature, is made explicit. The policy states the number, the run
recomputes it and refuses on any difference, and the completion records it.
floor8_pair loses 36,701 BLiMP single-sentence protections and 2,721 more whole items,
so it is not preferred.

## 3. Index / receipt decision (not a guess)

* `plan` refuses a benchmark receipt whose code identity differs from the running code
  (`control.py`, `preparation code/dependencies stale`). Every `src/` change therefore
  needs a fresh protected preparation, exactly as clean-v1 did. Reusing the clean-v1
  receipt is impossible by contract.
* A rebuild with NEW generation floors (a "v5" index) is refused too:
  * 821 items would have no signature;
  * protected `build` and `verify_benchmark` require `items_without_patterns == 0`.
* Therefore the rebuild keeps the receipt-bound generation `c05-matcher-v4` unchanged,
  and the expected index is byte-identical to clean-v1's. The new floors act only at
  matcher compilation, through the explicitly versioned trigger policy.
* For normal signatures this equals a rebuild under the new floors:
  * floors are monotone;
  * a whole variant of at most 13 tokens is its own pattern;
  * a 13-token window that meets a floor implies its variant does.

  Only the v4 whole-item fallback differs: the measured 821 items stay uncovered either
  way.
* The compile is the audited candidate. Activity is decided per DISTINCT pattern, over
  all its index records. The authored recall test proves equality with the audit's
  `prompt8` detection.

## 4. Trust and binding changes

| artifact | change |
|---|---|
| `ProductionPolicyV3` (`c05-production-v3`) | `matcher` = generation policy (must be v4, must equal `receipt.policy_digest`); `trigger` (required, no default, reviewed uncovered count); `exclusion_lineage`; `lineage` = split family; new `stage_order`. v2 policies validate and dump unchanged (version-discriminated union); a v3 value never validates as v2 |
| `ExecutionPlan` | `policy` is the version union; `output_contract` must be `c05_membership_v3` for v3 (checked in `identity()`). The clean-v1 plan digest is unchanged (`046381…` re-verified with this code) |
| compiled matcher manifest | `trigger` section (policy digest, record/pattern/item counts); every open requires the exact binding; an unfiltered compile can never serve v3, nor a filtered one v2 |
| run | before any corpus row: compiled trigger binding, `index_records == receipt.patterns`, `receipt.items - active_items == reviewed` (else refuse) |
| fact units | `c05-facts-v3`: extra `lineage_scope` section (1 byte per lineage key: split-only) included in the attested facts digest; v2 units unchanged |
| group seal | format `c05-facts-v3`; files `excl.u32`, `excluded.u8`, `fam_excluded.u8` replace `fam_hit.u8`; group digest rows add the exclusion family and decision |
| membership / decision rows | `c05_membership_v3`: `split_group` and `exclusion_group` (no `lineage_group`); downstream parsers accept only the plan's contract |
| completion | `c05_completion_v3`; adds `output_contract`, `trigger` (digest, counts, uncovered), `exclusion_lineage`, `split_lineage`, `exclusion_families`; verification checks them against the plan |
| reference (SQLite) engine | refuses v3 plans (oracle for v2 only) |
| downstream (C06 fit, count, select) | unchanged binding: completion digest. A tokenizer, counts or deficit report of clean-v1 is refused for the v2 proof (tested) |

## 5. Exclusion vs split families (`grouping.py`)

1. One external lineage sort produces two edge sets:
   * every key (split family);
   * every key except split-only keys (exclusion family).
2. Split-only = keys that only a SYNTH row's `additional_seed_url` contributes
   (`split_only_keys_v1`). The same URL also given as `query_seed_url` is not
   split-only.
3. Exclusion family = components over duplicates (exact + near), existing parents and
   non-split-only keys.
4. A hit excludes its exclusion family. `additional_seed_url` never merges and never
   propagates. A→B→C chains cannot carry a hit.
5. Split family = the unchanged known-lineage-v3 closure. Diagnostic/audit greedy
   allocation (same order and budgets) considers only split families with NO excluded
   member.

## 6. Tests (authored only)

Each command was run from the worktree with one thread per worker:

| command | result |
|---|---|
| `uv run --offline --locked --no-sync --extra cpu --extra eval python -m pytest tests/test_c05_policy_v2.py -n 0 -q -p no:cacheprovider` | **39 passed** |
| `... tests/test_c05_policy_v2_flow.py -n 0 ...` | **11 passed** |
| `... tests/test_c05_policy_candidates.py tests/test_c05_policy_counterfactual.py -n 0 ...` (audit tool unchanged in behaviour) | 25 + 24 passed (earlier in this session) |
| final related selection (`tests/test_c05_*.py`, C06 fast/fit, count-tokens, minhash, select fast), `-n 16 --dist=worksteal`, `-m "not serial_exclusive and not performance and not network and not cuda"` | **816 passed, 1 failed** (exit 1) |
| the failed `test_a1_stubborn_descendants_are_killed_and_reaped`, `-n 0`, on v2 and on base `f883210` | passed on both (known load-sensitive kill-timing test) |
| `ruff check`, `ruff format --check` | clean |
| `mypy --strict` on all 19 changed source files and the scripts | no issues |
| `git diff --check` | clean |

See section 9 for the base-vs-v2 comparison of the load-sensitive files.

`test_c05_policy_v2.py` covers:

* frozen floors and the pinned production trigger digest;
* boundaries:
  * prompt/answer/combined 7/8 tokens, 39/40 characters, 4/5 distinct;
  * sentence 2/3 tokens, 11/12 characters, 2/3 distinct;
  * item fallback 7 vs 8;
* any-kind activation;
* deterministic NFKC/casefold normalization;
* the compile filter, active pattern/item counts and trigger binding on open/reuse;
* the run coverage refusals;
* policy versioning;
* the membership contract per version;
* split-only keys;
* the two operator helper scripts.

`test_c05_policy_v2_flow.py` uses two real authored C05 runs on the same corpus
(legacy v2 policy; v3), each through plan, authorize, interrupted run, resume,
verify, proof, C06 fit, count and select:

* legacy reproduces the giant transitive SYNTH family, matching the oracle;
* v3 per-allocation decisions equal the independent naive oracle's
  `prompt8 x query_seed_family`;
* v3 rows and completion carry the new contracts;
* the same query seed is excluded together (seed 10, true long ARC copy);
* the 4-token fragment no longer excludes (seed 5);
* the A→B→C chain does not propagate (seeds 9, 11);
* split families stay transitive, and none with an excluded member is diagnostic/audit;
* short BLiMP sentence/pair hits and cross-source duplicate propagation are kept;
* the legacy tokenizer and legacy counts are refused for the v3 proof;
* a wrong reviewed coverage refuses before any fact unit;
* the trigger-recall gate equals the audited prompt8 detection for every task/split/form
  and blocks on a wrong expectation;
* the operator `proof` accepts the v3 completion.

## 7. Bounded benchmark (authored 100k documents, 16 workers, 5700X3D)

`scripts/c05_compact_benchmark.py generate --docs 100000 --provenance-kind combined`,
then `run` once per policy:

| policy | wall | SCAN | scan docs/s | matcher ENCODE | peak RSS |
|---|---:|---:|---:|---:|---:|
| c05-production-v2 | 46.1 s | 36.75 s | 2,721 | 2.8 s | 1.96 GB |
| c05-production-v3 | 49.8 s | 37.4 s | 2,676 | 5.0 s | 1.97 GB |

* Same decisions on this corpus: kept 91,977, excluded 159.
* Scan throughput is unchanged within noise.
* The per-record floor check was optimized afterwards: compile alone went 3.34 s →
  4.51 s versus 18.7 s profiled before. Projected cost on the 9.47M-record index: about
  +1 min.
* Grouping adds one masked edge set and one union-find, under 0.2 s here.

## 8. Expected production runtime

* Basis: the clean-v1 artifact times.
  * Decisions 10:40, receipt 11:00, so the protected preparation took ≤20 min.
  * Authorization 11:12, completion 13:13, so the C05 run took about 2 h 01 min.
* v3 adds about 1 min of compile and seconds of grouping, so about 2 h 05 min.
* The recall gate (compile + 306k injected forms) is estimated at about 10 min.
* The proof takes minutes.

## 9. Load-sensitive tests

Early `-n 16` runs in this session showed `resume-check` refusals in the authored flow
fixture. Those runs overlapped with my own mypy, benchmark and pytest jobs. With nothing
else running, the five affected files (`test_c05_forensics_fast`, `test_c06_fast`,
`test_c06_fast_hardening`, `test_c05_cleaned_rerun`, `test_select_fast`) passed
**242/242 on base `f883210`** (temporary detached worktree, since removed) **and 242/242
on v2**, at `-n 16`. [Evidence](../evidence/C05-POLICY-V2/load-comparison.txt).

## 10. Downstream invalidation

Once policy-v2 C05 is adopted, these become historical:

* clean-v1 plan `046381…`, completion `225b33…`, proof `clean-v1-p0001.proof.json`;
* tokenizer `8ef1a2dd…` (`G:/XLM/tokfit/mix01-fit-shares-v1-clean-v1`) and its kept
  index;
* counts `G:/XLM/counts/mix01-clean-v1`;
* deficit report `G:/XLM/selection/mix01-clean-v1.deficit.json`;
* the audit's token projection (old tokenizer).

They are refused when combined with the v2 chain.

Reusable unchanged: the cleaned corpus, the cleaning/admission chain (`84e401ba…`), the
acquired sources, the quotas and splits, and the protected material.

Fresh chain (documented, not executed):

```text
C05 policy-v2 -> C06 fit -> kept index -> count-tokens -> select -> exact 6B quotas
```

## 11. Requirement ledger

| requirement | status |
|---|---|
| worktree from `f883210`, branch `fix/c05-contamination-policy-v2` | IMPLEMENTED |
| 1 production evidence re-verified from committed artifacts | VERIFIED |
| 2 versioned matcher policy (generation + `c05-trigger-floors-v1`), digest frozen | IMPLEMENTED, VERIFIED (authored); production NOT RUN |
| 3 query-seed exclusion family; separate split family in code, schema, docs | IMPLEMENTED, VERIFIED (authored oracle) |
| 4 reproduce `prompt8 x query_seed_family` | VERIFIED on authored data; production expected values documented; NOT RUN |
| 5 mixture/quotas/splits/cleaning/data unchanged | VERIFIED (no such file touched) |
| 6 index/receipt decision with refusal of mixed bindings | IMPLEMENTED (rebuild with unchanged generation; trigger binding) |
| 7 fresh roots | IMPLEMENTED (documented) |
| 8 plan/proof binding; old proof historical; downstream refuses mixing | IMPLEMENTED, VERIFIED (authored) |
| 9 matcher, lineage and end-to-end tests; legacy readable | IMPLEMENTED, VERIFIED |
| 10 recall acceptance (ARC, BLiMP, HellaSwag, PIQA; BLiMP single) | VERIFIED (authored); production gate NOT RUN (X:) |
| 11 operator commands | IMPLEMENTED (runbook) |
| 12 no material throughput regression | VERIFIED (bounded authored benchmark) |
| 13 downstream plan | DOCUMENTED, NOT RUN |
| 14 checks | ruff, mypy --strict, git diff --check clean; focused related suites 816 passed + 1 known load-flaky (passes serially); serial_exclusive and full acceptance NOT RUN |

## 12. Limitations

* Per-allocation TRAIN counts can differ slightly from the audit projection, which split
  over exclusion families. The fresh count/select is the authority. The thinnest
  margins are PDR (+149,595), OER (+524,374) and news (lower bound +1.6M).
* `c05_component_forensics` and the audit tool parse v2 ledgers only. They refuse a v3
  ledger by schema.
* The trigger digest includes the reviewed count. A different count is a different,
  re-reviewable policy.
