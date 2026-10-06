# C06 after C05 production-v3: fresh tokenizer chain (2026-10-06)

Branch `fix/c06-c05-v3-transition`, worktree `F:\Project\xlm-c06-policy-v2`, from
`fb07db13028f9a9113520a3c3d539471ee74cb32`. That is the exact commit of the sealed real
C05 production-v3 run.

This change is code, tests and documentation only. It did NOT:

* fit the real tokenizer or tokenize the corpus;
* touch `G:/XLM/tokfit`, any C05 output, X: or any data.

## 1. Result

| item | value |
|---|---|
| C06 v3 support before this branch | parsers were already contract-aware; the production fast fit, kept index and their verifiers were never exercised on a `c05_membership_v3` completion (the v3 flow test fits a fixture tokenizer through the SQLite gate) |
| C06 v3 support after | VERIFIED on authored v3 and v2 completions through the production fast path (12 new tests) |
| semantics changed | none: C05, fit policy, sample rule, kept-index format, count and select unchanged |
| code changes | 2 small additive ones (section 3) |
| fit policy | `recipes/tokenizer/mix01_fit_shares_v1.yaml` reused unchanged, digest `9db3872b486c20a6659b996b6d7f9ffad335d64fb69c4410b5fa72f83637667c` (now pinned by a test) |
| fresh output root | `G:/XLM/tokfit/mix01-fit-shares-v1-policy-v2` |
| C06 order | fit and kept index in ONE pass and ONE atomic publication; then verify fit, then verify index |
| real train-kept count | unknown until the operator's step 6; printed as `assigned_splits.train` |

## 2. Compatibility assessment

Read paths:

* `fitscan.parse_membership_chunk` selects its schema from the plan's `output_contract`
  (`MEMBERSHIP_SCHEMAS`):
  * v3 rows must have exactly `split_group` and `exclusion_group`;
  * v2 rows must have exactly `lineage_group`;
  * anything else refuses.
* The fast fit (`fitfast.membership_tables`) and count (`countfast`) pass
  `view.plan.output_contract`.
* The group fields are only type-checked (strings). They are never stored, compared or
  used. C06 uses only `doc_id`, `content`, `bytes`, `split`, `decision`, file, row and
  allocation.
* The SQLite gate (`gates.MembershipGate`) imports only contract-neutral columns.

Why `split_group` cannot be read as `exclusion_group`: no C06/downstream module names
either field. The new test checks the source text of `fitfast`, `keptindex`,
`tokenizer_fit`, `gates`, `selection`, `countfast`, `selectfast`, `freeze` and
`transport`. It also proves that swapping or copying the two values changes no parsed
column.

Binding:

* The fit manifest carries `binding_of(view)`: mode, plan digest, completion digest,
  input manifest digest, `kept_membership_sha256` and source seals. Its `fit_path` is
  `c06-fast-v1`.
* `tokenizer/c05-binding.json` binds the plan digest, completion digest and tokenizer
  fingerprint.
* The kept index binds the plan, completion, membership SHA/bytes, kept count and the
  plan file table.
* `verify-tokenizer-fit`, `verify-kept-index`, `count-tokens` and `select` refuse any
  mismatch, so old artifacts are refused for the new chain (tested).

Gaps found:

1. Nothing let the operator pin WHICH C05 chain `fit-tokenizer` consumes. Passing the
   historical clean-v1 proof by mistake would fit a valid, but wrong, tokenizer into the
   new output root.
2. The kept index's train-kept document count was not printed anywhere. The completion
   has only per-allocation `kept` and `train_bytes`.

## 3. Changes

| file | change |
|---|---|
| `src/xlm/data/exclusion/control.py` | `--expect-c05-plan-digest` and `--expect-c05-completion-digest` on `fit-tokenizer`, `fit-tokenizer-reference`, `verify-tokenizer-fit` and `verify-kept-index` (see below) |
| `src/xlm/data/exclusion/fitfast.py` | `verify_kept_index` reports more content-free fields (see below) |
| `tests/test_c06_policy_v2.py` | new: 12 tests (section 6) |
| `docs/runbooks/c05-global-preparation.md` | operator sequence "C06 after the policy-v2 proof"; pin refusal listed under `--plan-only` |

The pins (`control.py`):

* They are optional. Without them the behaviour is unchanged.
* `expect_c05_chain` compares them to the proof's claimed digests before any membership
  or corpus read. Every C06 path then verifies those digests against the plan file and
  the completion.

The new `verify_kept_index` fields (`fitfast.py`), all from the verified snapshot:

* `plan_digest` and `completion_digest`;
* `assigned_splits` (kept rows by C05-assigned split);
* `train_canonical_bytes`.

The code identity changes. This does not affect the sealed C05 proof: downstream
consumers verify the plan digest, the signed completion and the proof, never the C05
plan's code identity (`runner.verify_completion`).

## 4. Order, roots and the train-kept count

Order (the existing contract, unchanged; this is option B):

1. `fit-tokenizer` streams the signed `membership.jsonl` once (hash = completion).
2. One source pass hashes every plan file and locates and parses every kept row. During
   that pass it fills both the BPE spool (selected rows) and the kept-index rows (every
   kept row).
3. BPE runs in a child process.
4. `tokenizer/`, the sample, the manifests and `kept-index/` publish in ONE atomic rename.
5. `verify-tokenizer-fit` and `verify-kept-index --membership` re-derive everything from
   authenticated membership.

What the kept index contains:

* every kept row: 14,927,848 expected = completion `kept`;
* `assigned_split` 0/1/2 = train/diagnostic_val/audit, as C05 assigned it.

The kept-train set is the rows with `assigned_split == train`. It is bound to completion
`df9834ab…` through the index manifest (`completion_digest`, `membership_sha256`, `kept`).
The index is not a train-only file, and changing that would be a new format; the fit
sample and count-tokens already restrict to train.

Expected real train-kept count:

* derivation: the number of `membership.jsonl` rows with `"split":"train"`, which equals
  `assigned_splits.train` from step 6 and must equal count-tokens' `documents`;
* it is NOT taken from the audit projection, which split over exclusion families while
  the real run splits over the broader split families;
* bounds known now: it is at most 14,927,848 kept rows, minus the diagnostic_val and
  audit rows (frozen per-allocation byte budgets);
* the historical count-tokens run 1 (STATUS, 2026-10-05; old chain) counted 12,613,085
  documents; it is NOT a prediction.

Fresh roots: see the runbook table. All are new paths. The fit and the index are
write-once (a fit into an existing directory refuses, tested on a historical fit dir).

## 5. Operator commands

Use the runbook section "C06 after the policy-v2 proof", steps 1-8 plus the prepared
count/select. Each command is one PowerShell line, run individually from
`F:\Project\xlm-c06-policy-v2`:

1. verify the worktree;
2. verify the new C05 proof;
3. `fit-tokenizer --plan-only` with pins;
4. the fit with pins and the reviewed digest;
5. `verify-tokenizer-fit`;
6. `verify-kept-index --membership`, which prints the train-kept count;
7. print the fingerprint (`c05-binding.json`);
8. print the manifest digests, sample identity and measured resources.

Expected resources (projection, not measured here):

* C06 fit: about 5.5-6 min and about 4-4.5 GiB peak process-tree RSS. Basis: the
  operator-measured clean-v1 fit (333.8 s, ~4.2 GiB) with the same stages, corpus bytes,
  sample size and BPE settings, on a slightly larger membership (14.93M kept rows).
* Verify fit: one membership pass (minutes); `--sources` adds one source pass.
* Verify kept index: one membership pass.
* Deadline 1200 s, RSS ceiling 24 GiB.

## 6. Tests (authored only; no real corpus, proof or tokenizer)

Environment:

* Windows 11 Pro 10.0.26200;
* Python 3.12.13 from `.python-version`;
* `uv sync --offline --locked --extra cpu --extra eval` (created `.venv` from cache);
* one thread per worker (`OMP/MKL/OPENBLAS/NUMEXPR_NUM_THREADS=1`,
  `TOKENIZERS_PARALLELISM=false`).

| command | exit | result |
|---|---|---|
| `uv run --offline --locked --no-sync --extra cpu --extra eval python -m pytest tests/test_c06_policy_v2.py -n 0 -q -p no:cacheprovider --basetemp=C:/t/c06v2-new2` | 0 | **12 passed** (31.6 s) |
| mutation: `expect_c05_chain(args)` disabled, `...::test_cli_pins_refuse_the_old_chain_before_any_read -n 0` | 1 | failed as required (the old proof planned); restored |
| related selection (below) | 1 | **281 passed, 1 failed** (153 s) |
| the failed `tests/test_c06_fast_hardening.py::test_a1_stubborn_descendants_are_killed_and_reaped -n 0` | 0 | passed (the known load-sensitive kill-timing test; see the C05 v2 report section 9) |
| `ruff check` and `ruff format --check` on the 3 changed Python files | 0 | clean |
| `mypy --strict src/xlm/data/exclusion/control.py src/xlm/data/exclusion/fitfast.py` | 0 | no issues |
| `git diff --check` | 0 | clean |

The related selection ran in one xdist controller:

* modules `tests/test_c06_policy_v2.py`, `test_c06_fast.py`, `test_c06_fast_hardening.py`,
  `test_c06_tokenizer_fit.py`, `test_tokenizer_fit_stream.py`, `test_c05_policy_v2_flow.py`,
  `test_c05_policy_v2.py` and `test_c05_cleaned_rerun.py`;
* `-n 16 --dist=worksteal --max-worker-restart=0`;
* `-m "not serial and not serial_exclusive and not performance and not network and not cuda"`.

NOT run: the full offline acceptance suite, the serial selection, count/select fast
suites beyond what the selected modules exercise, and any real data.

Fixtures (`tests/test_c06_policy_v2.py`): two authored C05 runs over the same generated
corpus through the real operator flow (`c05_policy_support.build_run`):

* the old chain: `c05-production-v2`, `c05_membership_v2`;
* the new chain: `c05-production-v3`, `c05_membership_v3`.

Both runs have diagnostic_val and audit partitions, plus excluded and duplicate
decisions. The production fast fit runs on each.

| requirement | test |
|---|---|
| `c05_membership_v3` accepted; legacy `c05_membership_v2` still readable; cross-contract and unknown contracts refused | `test_membership_parser_accepts_only_the_plans_contract`, `test_chains_carry_their_membership_contracts`, `test_legacy_v2_chain_still_fits_and_verifies` |
| `split_group` never interpreted as `exclusion_group` | `test_split_group_and_exclusion_group_never_drive_c06` |
| new completion digest binding (manifest, `c05-binding.json`, kept index, resource plan) | `test_new_fit_is_bound_to_the_new_chain` |
| only kept, only train; diagnostic/audit, excluded and duplicates absent; exact sample | `test_new_fit_sample_is_exactly_kept_train`: equals an independent oracle (rank = sha256 of canonical [tag, seed, allocation, doc_id, content]) built from identity fields only |
| kept index = C05 kept membership, train count and bytes reconcile with the completion; sources re-verified | `test_new_kept_index_is_c05_kept_membership` |
| deterministic sample, component shares, spool and tokenizer (inline vs 2 spawned workers: identical sample, kept index and tokenizer files) | `test_new_fit_verifies_and_is_deterministic` |
| frozen policy unchanged; deterministic 512 MiB component apportionment | `test_production_fit_policy_is_reused_unchanged` |
| old proof refused for the new fit and kept index; new proof refuses the stale fit and index | `test_cross_chain_fits_and_indexes_refuse` |
| pins refuse the old chain before any read or write (plan-only and fit); verify commands honour pins | `test_cli_pins_refuse_the_old_chain_before_any_read` |
| write-once (an existing historical fit is untouched); stale tokenizer refused by count-tokens; new tokenizer counts the new chain | `test_published_fits_are_write_once_and_stale_tokenizers_refused` |

The deterministic-512-MiB claim is proven at production scale only for the budget
apportionment. The sample determinism itself is proven on the authored scale (44 KiB),
where the same code path runs.

## 7. Downstream consequence

After the fit, the new tokenizer almost certainly has a fingerprint other than
`8ef1a2dd…`, so:

* `counts/mix01-clean-v1` and `selection/mix01-clean-v1*` cannot be reused; they are
  refused by binding;
* fresh `count-tokens` and `select` are required. Their commands are prepared in the
  runbook and were NOT run.

Mix-01 is NOT declared sufficient:

* the projection that met every frozen quota used the OLD tokenizer;
* its thinnest margins were PDR +149,595 and OER +524,374 valid targets;
* only the fresh exact count and select decide.

## 8. Requirement ledger

| requirement | status |
|---|---|
| worktree `F:\Project\xlm-c06-policy-v2`, branch `fix/c06-c05-v3-transition` from `fb07db1`; source worktree untouched | IMPLEMENTED |
| 3 audit of C06/kept index for v3 (`c05_membership_v3`, `exclusion_group`, `split_group`, proof/completion binding) | VERIFIED (authored): supported; gaps listed in section 2 |
| smallest correct change; C05 semantics unchanged | IMPLEMENTED (two additive operator-facing changes) |
| 4 tokenizer science preserved (policy file and digest unchanged) | VERIFIED |
| 4 fit bound to plan `c15b…`, completion `df98…`, new proof; refuses the old chain | IMPLEMENTED (binding + pins), VERIFIED (authored); production NOT RUN |
| 5 fresh output namespace; old artifacts never overwritten | IMPLEMENTED (documented roots, write-once tested) |
| 6 kept index order (existing contract: built and published with the fit) and count report | IMPLEMENTED; real count NOT RUN (operator step 6) |
| 7 progress with rate/elapsed/ETA/RSS; fast implementation reused | VERIFIED (existing `[C06]` progress; no path change) |
| 8 downstream count/select commands | PREPARED, NOT RUN |
| 9 Mix-01 not declared solved | DOCUMENTED |
| 10 tests | VERIFIED: 12 new passed; related 281 passed + 1 known load-sensitive (passes serially) |
| 11 no real C06/tokenization/data/X: operations | VERIFIED (none performed) |
| full offline acceptance, serial selection | NOT RUN |

## 9. Limitations

* The expected runtime and RAM are projections from the operator-measured clean-v1 fit.
  The real numbers are recorded in `tokenizer_fit_resource_plan.json` (`measured`).
* The pins are optional. The runbook commands always pass them; a hand-typed command
  without them falls back to the unchanged binding checks.
* `count-tokens` and `select` take no pins. They are bound to the chain through the
  tokenizer's `c05-binding.json` and the proof.
