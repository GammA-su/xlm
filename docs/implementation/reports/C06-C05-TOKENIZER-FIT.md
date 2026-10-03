# C06 C05-bound tokenizer fit — share policy decision required

**Date:** 2026-10-03
**Branch:** `feat/c06-c05-tokenizer-fit` (from `2ae48a71f3e7368a989bfca8ef95c09b53cecf18`)
**Status:** BLOCKED — `TOKENIZER FIT SHARES REQUIRE OPERATOR DECISION`

The production bridge (C05 p0002 proof → deterministic fit sample → 32,768
ByteLevel BPE → `c05-binding.json`) was **not implemented**. The task requires a
stop before any fit-share choice when the repository does not define the
production shares unambiguously. It does not. No real file was opened, no corpus
was read, no tokenizer was fitted, and nothing touched `X:` or `G:`.

A separate, independent fix landed: C05 plan-sequence allocation (see the last
section).

## 1. Is an unambiguous production tokenizer-fit share policy frozen?

**No.** What exists:

| Source | What it defines | Production fit shares? |
|---|---|---|
| `CONTRACTS.md` C06 | "Freeze a **balanced** tokenizer-training sample from the admitted training pool **before mixture search**; never retrain per mixture." | Mechanism only; "balanced" is unspecified |
| `prompts/11_pool_freeze_tokenizer_regime.md`, `reports/P11.md` §3.3 | Balance by **declared raw-byte shares** across views | Mechanism only |
| `src/xlm/data/pools/views.py` `SourceView.declared_raw_byte_share` | Per-view share field, default `0.0` | Field exists; values only in `fixtures/pools/views.yaml` (0.7/0.3, authored) |
| `recipes/mixtures/mix01_views.yaml` (`mix01_views_v2`) | Mix-01 component registry | **No `declared_raw_byte_share`** on any view |
| `recipes/mixtures/mix01.yaml` | M0 weights, unit `valid_target_tokens`, status `draft_unvalidated` | Mixture weights, not fit shares |
| `recipes/mixtures/mix01_quotas_6b.yaml` | Final valid-target quotas (weight × 6B); says "freeze the tokenizer fit input" | Mentions the step and gives no shares |
| `build_tokenizer_fit_manifest` | Silently falls back to **equal shares** when declared shares sum to 0 | That is a fallback in the code, not a frozen decision. The production bridge must not reach it |

The seed (`TokenizerFitConfig.seed = 20260919`), target (512 MiB), vocabulary
(32,768) and special tokens are frozen. The share vector is not.

## 2. Options (all data-only, all reproducible from signed metadata)

Allocation granularity: the 11 Mix-01 components. Within IFM, the
`general`/`planning` views are split by the frozen IFM split. Within Common
Pile, the upstream components are split by the frozen Common Pile component
split. These match the existing `(component, view, upstream)` allocation key.

**Option A: natural kept-train byte shares.**
`share(a) = train_bytes(a) / Σ train_bytes`, where the inputs are the per-allocation
values already in the signed p0002 completion payload (`allocations[*].train_bytes`).
- Pro: mixture-independent, so it satisfies C06 "before mixture search". It is a
  uniform byte sample of the actual screened training pool. It needs no extra
  pass and is bound by the completion digest.
- Con: it reflects first-pass acquisition **headroom**, not intended exposure.
  For example, the documented Essential practical/prose oversupply is preserved
  in the seal, so oversupplied sources dominate the merges. Shares vary with any
  future top-up/re-C05, but that already produces a new proof and a new tokenizer.

**Option B: frozen Mix-01 allocation quotas as byte shares.**
`share(a) = final_valid_targets_quota(a) / 6,000,000,000`, taken from
`mix01_quotas_6b.yaml` plus the IFM and Common Pile splits (the same table
`frozen_requirements` already verifies against the sealed sources).
- Pro: the tokenizer sees approximately the anchor training distribution. Every
  number is already frozen and SHA-bound.
- Con: this uses the **final Mix-01 weights**, which the task names as a
  must-not-use-silently case. It couples the shared tokenizer to M0 (`draft_unvalidated`),
  so M1–M5 treatments (more PDFs, more synth, …) run on a tokenizer tilted toward
  the anchor, and C06 asks for freezing before mixture search. Units also differ:
  token quotas are reused as byte shares, and that conversion must be stated
  explicitly.

**Option C: equal component shares, quota-proportional inside a component.**
1/11 per component, split inside IFM/Common Pile by their frozen per-view or
per-upstream quotas.
- Pro: neutral across mixture treatments and simple to state.
- Con: over-represents narrow sources. For example `simple_stories` gets 9.1% of
  the fit against a 2% M0 weight, which skews merges toward children's-story
  vocabulary. Equal shares are also named in the task as a must-not-use-silently
  case.

The decision should also confirm the existing P11 defaults below. Without an
explicit choice, the bridge would **reuse** them and record them in the manifest:
- whole-document crossing: once an allocation's budget is reached, the crossing
  document is included whole (overshoot is recorded);
- an allocation that cannot meet its budget is recorded as a shortfall and
  **not** redistributed;
- `max_document_bytes = None` (no per-document cap). Once 512 MiB is split
  across every allocation, a single very long PDF could consume a small
  allocation's budget. A cap such as 1 MiB is a legitimate extra choice.

Record the decision as a data-only file, for example `recipes/tokenizer/mix01_fit_shares_v1.yaml`,
with `share_policy_id`, the option, the per-allocation shares (or the rule for A),
the crossing/cap/shortfall rules, and the operator and date. The fit manifest binds
its SHA-256. The bridge would refuse without the file, and never default.

## 3. Bridge design that is ready to implement after the decision

The design does not depend on which option is chosen. Each option only supplies
the share vector.

1. `open_gate(proof, consumes=[scratch, output, shares, quotas, ifm_split])`
   applies the detached-volume guard **before** any corpus byte is read. It also
   refuses scratch/output inside the protected root or the C05 scratch. Gate
   import goes into a SQLite lookup on disk, never a Python dict.
2. **Sample index (pass 1).** This pass uses `iter_plan_documents` (one sequential
   pass that verifies file hashes and counts). For each record it checks `gate.lookup`
   and the exact `canonical.digest(doc.to_dict())`. Kept records whose content or
   allocation differs refuse. Excluded and duplicate records are absent from kept
   membership and are skipped, as in `count_tokens`. `diagnostic_val`/`audit`
   records are kept non-train and are skipped. A document with an unknown ID refuses.
   The pass inserts `(allocation, rank=sha256(seed, allocation, doc_id, content), id, content, bytes)`
   into a scratch SQLite `WITHOUT ROWID` table. Memory stays bounded at roughly
   (row count × ~200 B) on disk, not in RAM.
3. **Sample select.** For each allocation, in `ORDER BY rank, id`, take whole documents
   until the byte budget is reached. The result is a bounded `fit_ids` table, roughly
   10⁵–10⁶ rows. The order is independent of file and record arrival order.
4. **Materialize/verify (pass 2).** A second sequential pass re-verifies each
   selected record through `MembershipGate.verify` (exact content, kept, train,
   no repeat). It writes length-framed canonical text to a scratch spool and
   refuses if any selected ID is missing at the end.
5. **Fit.** `ByteLevelBPETokenizer.train_from_documents` receives explicit
   `max_train_docs = selected_count`, `max_train_bytes = selected_bytes`, and
   `is_production_baseline=True` with the protected gate. The current 100k-document
   and 500 MiB defaults would silently truncate a 512 MiB sample, so the bridge
   sets these limits explicitly. The bridge then checks that `training_input_hash`
   equals the hash recomputed over the complete frozen sample.
6. **Publish atomically.** Stage `tokenizer/{tokenizer.json, tokenizer_manifest.json, c05-binding.json}`,
   `tokenizer_fit_manifest.json` (bindings, shares declared/achieved, selected-ID
   file SHA, no text) and `tokenizer_fit_resource_plan.json`. Run `tokenizer_identity`
   against the open gate, then rename the stage into place. On any error or
   interrupt, scratch is removed and no completed directory exists.
7. Content-free progress (`PROOF VERIFY … COMPLETE`). The `tokenizers` BPE trainer
   has no progress callback, so the TOKENIZER FIT stage prints a start line
   explaining that merge progress is not observable.

The command would be `python -m xlm.data.exclusion.operator fit-tokenizer`, routed
through `control.main` alongside `count-tokens`/`select`, with the interface
proposed in the task plus `--fit-shares <decision file>`.

## 4. Separate fix: C05 plan-sequence allocation

`create_plan` enumerated `plan_root.glob("p[0-9]*.json")`. That pattern also matches
`p0001.authorization.json`, and the code then raised on `int("0001.authorization")`.
`next_plan_sequence` now counts only full-name matches of `p(\d{4,}).json`. Commit
`9ee7409`. The historical checkout `F:\Project\xlm-c05-parallel` was not touched.

## 5. Commands and results

Environment: Windows 11, Python via `uv run --offline --locked`. Thread variables
were set to 1 and `TOKENIZERS_PARALLELISM=false`.

| Command | Exit | Result |
|---|---|---|
| `pytest tests/test_c05_control.py::test_plan_sequence_counts_only_exact_execution_plan_names -n 0` | 0 | 1 passed |
| `pytest tests/test_c05_control.py -n 8 --dist=worksteal --max-worker-restart=0` | 0 | 16 passed |
| `ruff format --check` / `ruff check` (control.py, test_c05_control.py) | 0 / 0 | clean |
| `mypy --strict src/xlm/data/exclusion/control.py` | 0 | no issues |
| `git diff --check` | 0 | clean |
| Old-glob reproduction (scratch script) | — | `invalid literal for int() … '0001.authorization'` |

NOT RUN: the other C05 suites, tokenizer and P11 regime tests (no code in those
paths changed), the full acceptance selection, and any real or live work.
