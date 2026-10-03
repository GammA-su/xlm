# C06 C05-bound tokenizer fit

**Date:** 2026-10-03
**Branch:** `feat/c06-c05-tokenizer-fit` (from `2ae48a71f3e7368a989bfca8ef95c09b53cecf18`)
**Status:** IMPLEMENTED and VERIFIED on authored fixtures. The real production fit
is NOT RUN; it requires operator authorization.

History: an earlier pass on this branch (commit `e0608ac`) stopped because the
repository had no frozen production share values. The operator then decided the
policy recorded below. The separate plan-numbering fix is commit `9ee7409`.

Nothing on the real system was touched. No real proof, corpus, C05 output or `X:`
was opened, no tokenizer was fitted on real data, and count-tokens, select,
tokenization and training were not run.

## 1. Frozen policy

`recipes/tokenizer/mix01_fit_shares_v1.yaml` (data-only, `mode: production`).

| Identity | Value |
|---|---|
| Policy digest (canonical parsed content, EOL-independent) | `9db3872b486c20a6659b996b6d7f9ffad335d64fb69c4410b5fa72f83637667c` |
| Policy file SHA-256 (LF bytes as committed) | `d18d0c2cdf6898d1d097ed1e7322dbb11bc59e4e6dcd709944734572de382f04` |
| Pinned quota table | `recipes/mixtures/mix01_quotas_6b.yaml`, SHA-256 `9b16db765c4dbb3fc3a366b1c2a8acc88e3ca8bc81f344d0a082ee00d8c78fdf` |

Policy values: target 536,870,912 canonical bytes, ByteLevel BPE, vocabulary 32,768,
special tokens `<pad> <bos> <eos> <unk>`, seed 20,260,919, `max_document_bytes`
1,048,576. All eleven component weights are integer `1`:
`essential_science`, `essential_practical`, `essential_prose`,
`ultrax_ultrafineweb`, `finepdfs_en`, `synth_en_explanations`,
`nemotron_wiki_rewrite`, `finewiki_en`, `ifm_behaviors_general_planning`,
`common_pile_prose`, `simple_stories`.

Rules are single-valued literals. Changing one makes the policy unparseable:

| Rule | Value |
|---|---|
| eligibility | `c05_kept_train_exact_content` |
| order | `sha256_tag_seed_allocation_docid_content_then_docid` |
| crossing | `include_whole_document` |
| oversized | `skip_for_fit_only` |
| shortfall | `refuse_without_redistribution` |
| rounding | `largest_remainder_then_key` |

The rationale in the file states the operator's reasons:
- The fit distribution is intentionally not the training distribution.
- The tokenizer is shared and fixed across Mix-01 mixture experiments.
- Natural bytes are rejected because acquisition headroom would set the vocabulary.
- M0 weights are rejected because they would favour the M0 mixture.
- Equal weights are an explicit decision, not a fallback.

**Parser (`load_fit_policy`)** refuses any of the following:
- unknown fields or duplicate YAML keys;
- a component set other than the eleven;
- a non-integer weight (including bool, float or string) or a weight ≤ 0;
- in production mode, any other target, vocabulary, seed or document cap;
- different special tokens;
- a changed rule;
- a missing or altered internal-split identity.

`mode: development` exists only for authored fixtures. A protected C05 gate refuses
it.

## 2. Byte-budget derivation (exact integers)

1. Component budgets are a largest-remainder apportionment of 536,870,912 by the
   integer weights, with ties broken by component ID. 536,870,912 = 11 × 48,806,446 + 6,
   so `common_pile_prose`, `essential_practical`, `essential_prose`,
   `essential_science`, `finepdfs_en` and `finewiki_en` get **48,806,447** each. The
   other five get **48,806,446**. The shares are recorded exactly as `1/11`.
2. Each component budget is then apportioned over that component's allocations by
   the frozen `final_tokens` quotas returned by `quotas.frozen_requirements`, with
   ties broken by allocation key. These are the same requirements used by
   `select`/`quota-report`.
   - **IFM** is split by the `--ifm-split` file. `frozen_requirements` accepts that
     file only if its digest equals the split bound by the sealed
     `ifm_general`/`ifm_planning` sources. With the documented 50/50 split, the views
     get 24,403,223 bytes each.
   - **Common Pile** is split by the `component_split` bound by the sealed
     `common_pile` source.
   - The fit manifest records `quota_sha256`, `ifm_split_digest`,
     `common_pile_split_digest` and `requirements_digest`.
   - Every other component has exactly one allocation. A non-composite component
     with several allocations refuses.
3. The quotas only divide a composite component's own budget. Changing M0 quotas
   leaves every top-level budget unchanged (tested). Natural availability is never
   an input to the budgets.

## 3. Eligibility, cap, crossing and shortfall

- **Eligibility.**
  - Pass 1 re-reads the hash-verified plan files (`iter_plan_documents`).
  - A record absent from kept membership is excluded or a duplicate, and is counted
    but never offered.
  - A kept record must match the full canonical-record digest and allocation
    (`_kept_train`), or the fit refuses.
  - Kept `diagnostic_val`/`audit` records are counted and never offered.
  - Per allocation, kept / non-kept / kept-train bytes must equal the signed
    completion's `allocations`, and the document total must match. A changed,
    unknown or injected record therefore refuses. Pass 2 re-verifies through
    `MembershipGate.verify`.
- **1 MiB cap.**
  - A record with `utf8_byte_count > 1,048,576` is skipped for fitting only. It is
    not modified, and it stays in C05 membership and remains eligible for training
    selection.
  - Exactly 1,048,576 bytes is eligible.
  - `oversized_candidates_skipped` and `oversized_candidate_bytes` are recorded per
    allocation, per component and globally. These are counts only, with no IDs.
- **Crossing.**
  - Records are taken in rank order until the requested bytes are reached. The
    crossing document is included whole and nothing is truncated.
  - The manifest records `requested_bytes`, `selected_bytes` and `overshoot_bytes`
    per allocation, per component and globally. Overshoot per allocation is less
    than the crossing document's size, so it is ≤ 1 MiB.
  - The rank is `sha256(canonical(["c06-tokenizer-fit-v1", seed, allocation, doc_id, content]))`.
    The tag keeps the fit order independent of the C05 quota-selection order, which
    uses the same seed.
- **Shortfall.**
  - If any allocation has eligible bytes below its requested budget, the fit refuses
    before any text is spooled.
  - It writes a content-free deficit report (`--deficit-report`, exit 2) with
    requested, available eligible, deficit, eligible documents and oversized skips
    per allocation.
  - There is no redistribution, renormalization, target reduction, repetition or
    cap removal, and nothing is published.

## 4. Bounded architecture

| Structure | Bound |
|---|---|
| C05 kept-membership lookup | Existing gate SQLite on the proof's scratch (≤ `Resources.index_bytes`), on disk |
| Pass-1 state | Per allocation: a heap holding exactly the shortest rank-ordered prefix that reaches the budget. Later-ranked entries are dropped once the budget is met. Hard ceiling of 4,000,000 retained entries in total; above it the fit refuses. Planning figure 512 B per entry, about 2 GB worst case. A typical 512 MiB sample holds far fewer entries. |
| Pass-2 lookup | The same bounded selected map, keyed by doc_id |
| BPE spool | Length-framed selected text only, in `--scratch/c06-fit-<uuid>/` (bound: sample bound plus 8 B per document) |
| Documents in memory | One at a time per pass. Tested: at most 3 alive, and exactly two sequential passes |
| Concatenated corpus | None |

The resource plan (`--plan-only`) is deterministic and content-free. It covers:
- input files, documents and bytes, and the two-pass read bound;
- the sample bound: target plus allocations × 1 MiB;
- the BPE peak estimate, reusing the P11 planning formula, now factored as
  `estimated_fit_peak_memory_bytes`;
- the spool, lookup and output ceilings.

The real fit refuses unless `--resource-plan-digest` equals the reviewed plan's
digest. It also refuses if the scratch volume's free space is below the spool
bound. Measured stage times, peak RSS (Windows `peak_wset`) and spool bytes are
appended to `tokenizer_fit_resource_plan.json` after the run.

## 5. The complete sample reaches BPE

- The bridge calls `ByteLevelBPETokenizer.train_from_documents` with these
  settings:
  - `max_train_docs = selected documents` and `max_train_bytes = selected canonical bytes`,
    never the historical 100,000 / 500 MiB defaults;
  - `require_complete=True` (new: exceeding a bound raises `FitSampleBoundError`
    instead of silently truncating);
  - `spool_dir` set to the job scratch;
  - `c05_gate` set to the open gate, so the fit itself is gated.
- After the fit, the bridge requires three things:
  - the documents and bytes fed equal the frozen sample;
  - the tokenizer's `training_input_hash` equals the bridge's own hash of exactly
    the selected records in feed order;
  - verification recomputes that hash independently.
- Scaled evidence:
  - With the defaults patched down to 2 documents / 100 bytes, a default caller is
    capped (control). The bridge still feeds every selected record.
  - A logical 150,000-document / 600 MiB bound passes through uncapped.
  - Bounds of n−1 documents or b−1 bytes raise. Nothing of hundreds of MiB was
    allocated.

## 6. Output and C05 binding

The output is one write-once directory, staged as `<output>.partial-<uuid>` and
renamed into place last:

```
<output>/tokenizer/tokenizer.json
<output>/tokenizer/tokenizer_manifest.json
<output>/tokenizer/c05-binding.json        {plan_digest, completion_digest, tokenizer_fingerprint}
<output>/tokenizer_fit_manifest.json        signed (trusted --issuer), content-free
<output>/tokenizer_fit_sample.jsonl         doc_id, C05 content digest, allocation, bytes, rank
<output>/tokenizer_fit_resource_plan.json   accepted plan + measured values
```

- Downstream commands take `--tokenizer <output>/tokenizer`. `tokenizer_identity()`
  is unchanged and accepts the directory.
- `c05-binding.json` is written only after the gated fit, in the same staging
  directory.
- The signed manifest binds:
  - the policy, its digest and file SHA, and the C05 plan, completion, input-manifest,
    kept-membership and source-seal bindings;
  - the split digests;
  - per-allocation and per-component declared/achieved shares, as exact fractions;
  - bytes and documents;
  - the sample SHA;
  - the BPE bounds and fed counts;
  - the tokenizer identity;
  - the resource-plan digest and the producing code identity.
- `verify-tokenizer-fit` re-checks:
  - signature, binding, policy, splits, budgets and resource plan;
  - the sample file, row by row, against kept-train membership, the cap and the
    crossing rule;
  - the tokenizer files.
- A deficit, a C05 mismatch, an interrupt (exit 130), a fit failure or a
  verification failure removes the staging directory and the job scratch, so no
  complete output exists.

## 7. Progress

Output is on stderr with the `[C06]` label, as fixed stage names and numbers only.
The stages are:
- `PROOF VERIFY`;
- `SAMPLE INDEX`: documents/total, %, GiB input, eligible, oversized, retained, docs/s, MiB/s, rolling ETA;
- `SAMPLE SELECT`;
- `SAMPLE VERIFY`;
- `TOKENIZER FIT`: fed documents/total and bytes;
- `TOKENIZER FIT: MERGES`: no percentage, plus a fixed note that internal BPE merge
  progress is not observable, plus an elapsed/RSS heartbeat every 30 s;
- `TOKENIZER SAVE`, `VERIFY`, `COMPLETE`.

Tests confirm the output contains no IDs, digests, text or paths.

## 8. Tests and checks

Environment: Windows 11, Python 3.12.13 via `uv run --offline --locked --extra cpu --extra eval`,
with `OMP/MKL/OPENBLAS/NUMEXPR_NUM_THREADS=1` and `TOKENIZERS_PARALLELISM=false`.
All fixtures are authored; nothing ran live.

| Command | Exit | Result |
|---|---|---|
| `pytest tests/test_c06_tokenizer_fit.py -n 8 --dist=worksteal --max-worker-restart=0` | 0 | 63 passed |
| `pytest tests/test_c06_tokenizer_fit.py tests/test_c05_selection.py tests/test_c05_detached_volume.py tests/test_c05_control.py tests/test_c05_progress.py tests/test_tokenizers.py tests/test_tokenizer_fit_stream.py tests/test_tokenizer_regime.py tests/test_pool_freeze_regime.py tests/test_cli_pool_freeze.py -n 16 --dist=worksteal --max-worker-restart=0` | 0 | 235 passed (includes the plan-numbering regression) |
| `ruff format --check` / `ruff check` (8 changed files) | 0 / 0 | clean |
| `mypy --strict` (6 changed source modules) | 0 | no issues |
| `git diff --check` | 0 | clean |

The C06 module covers each item the operator requested:
- eligibility (excluded, duplicate, diagnostic_val, audit never offered; only kept
  train offered);
- content, unknown, second-pass and changed-byte mismatches, and a C05 proof mismatch;
- the mounted protected volume, with the fit refusing before any policy, plan or
  corpus access, plus root and scratch path refusals;
- determinism and seed binding;
- policy identity and 24 fail-closed parser cases plus duplicate keys;
- exact equal weights, no fallback, M0 independence, natural-availability
  independence, and the IFM and Common Pile splits;
- the cap boundary and the skipped-never-fed rule;
- shortfall refusal and the cap-induced shortfall, without redistribution;
- crossing and overshoot accounting;
- complete-sample consumption at scaled and logical sizes;
- binding and `tokenizer_identity`;
- changed tokenizer files refusing, and production certification not being weakened;
- content-free progress, bounded retention and the hard ceiling;
- interrupt, failure and verification-failure cleanup;
- operator CLI plan → fit → verify → `count-tokens` → `select` (EXACT).

**NOT RUN:**
- the remaining C05 suites (engine, compact, scan, preparation);
- the full acceptance selection;
- Torch-dependent workflows that call `train_from_documents` with explicit caps
  (signature unchanged);
- any real or live work.

Micro-measurement (authored, not a benchmark): the pass-1 per-record path (parse,
content digest, rank) runs at about 18,700 docs/s or 68.5 MiB/s single-threaded on
3.8 KB records. Disk I/O, file hashing and SQLite lookups are excluded.

## 9. Open limitations

- Both passes are single-process. Real throughput, the gate import time for 12.6M
  rows (`PROOF VERIFY` has no inner progress) and BPE peak memory are **not
  measured**. The P11 3.5× BPE figure is a planning estimate and may understate
  Hugging Face BPE word-count memory. The measured peak is recorded after the run.
- The policy pins the quota-table SHA of the LF bytes. If the sealed sources bind
  different bytes, for example a CRLF checkout, `--plan-only` refuses before any
  corpus read.
- Sample membership is independent of input order. The BPE feed order is the
  frozen C05 plan file order, and the training-input hash binds it.
- Merge heartbeats depend on the backend releasing the GIL. Without heartbeats the
  process is not hung.
- If real documents were so small that the sample exceeded 4,000,000 entries, the
  fit refuses, and that needs an operator decision.

## 10. Operator commands (after approval; not executed here)

See [the runbook](../../runbooks/c05-global-preparation.md#tokenizer-fit-c06).
