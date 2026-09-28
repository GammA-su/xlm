# Essential-Web evidence-v2.0: offline implementation report

Date: 2026-09-27. Branch: `data/mix01-ultrax-6b`. Starting HEAD: `a23cd7e`
(dirty only with preserved prior user/Astra docs). This implements ONLY
`essential-web-evidence-v2.0` from the normative
[protocol](ESSENTIAL-WEB-EVIDENCE-V2-PROTOCOL.md), bound by
`../evidence/ESSENTIAL-WEB-EVIDENCE-V2/freeze.json`. No experiment was
designed here: seeds, files, windows, policies, strata, rubric, limits,
and the decision matrix are frozen and reproduced verbatim as data.

Agent work is offline only unless a command says otherwise: no live
footer inspection, fetch, source-text inspection, X: writes, tokenizer,
training, production admission, or push. The single authorized exception
is the read-only derivation of the Arm-T locator selection from the
already-existing metadata-only development bundle, sealed into the
designated G: root. Every input record was asserted text-free.

## 1. Frozen verification (all recomputed, exit 0)

- Freeze digest recomputed `fe2157799a86fe777e45c220716563f8a73245a12593c40909222555bf1b8248`
  (expected value, match); all four bound artifacts match bytes + SHA-256
  + descriptor digests.
- `inventory-freeze.json` self-digest `466ece976bfadfbd024c7fb66ff0076f1d86f36ce7d362331d85f1fee36c018f`
  (match); policy digest `f4357f61…07` recomputed from the repo YAML (match,
  YAML equality).
- All eight original listing hashes reproduce from sequential templates;
  eligible digests, ranking hashes, and winners reproduce; selected files
  are distinct and disjoint from development files; 23,200 eligible paths;
  complete inventory digest `d2b3eac5…0927d5` (match). No STOP condition.

Eight frozen new files (`data/crawl=CC-MAIN-*/train-*-of-*.parquet`):
2014-15/01860, 2015-32/01682, 2016-50/02156, 2018-05/03378, 2019-09/00153,
2021-04/00179, 2021-49/00408, 2024-26/01127.

## 2. What was implemented

New package `src/xlm/data/evidence_v2/` (typed, bounded, no dynamic
execution of data files):

- `canonical.py` — `C(x)`/`H(x)` per §2 (Python 3.12 `json.dumps`,
  `sort_keys`, compact separators, `ensure_ascii=False`,
  `allow_nan=False`, UTF-8, no BOM/newline); strict parsing rejects
  duplicate keys, non-finite numbers, BOM, bad UTF-8; unknown-field
  rejection; self-digests; file bindings; atomic writes.
- `frozen.py` — data-only frozen constants (versions, seeds 20260927 /
  20260928, digests, repo/revision, strata, exact Arm-M/Arm-T ceilings,
  hosts, retry rules, text-strata precedence, forbidden package fields).
- `inventory.py` — offline listing expansion, listing/eligible/rank
  verification, lowest-digest winner with UTF-8 tie-break, full
  freeze/inventory verification. No next-rank API: an unavailable winner
  is a STOP.
- `windows.py` — exact §4 index construction delegating to the existing
  tested `sampling._det_index` with the frozen string seed (asserted
  equal to the literal protocol formula); group/start choice;
  `K != 512` refusal with no rerank; absolute + group-relative freezing;
  pinned single-file request builder; dry arm plan with explicitly
  unresolved physicals (`executable: False`).
- `budgets.py` — shared `ArmLedger` per arm with the exact §4/§7
  ceilings: one request counter shared by footer+execution (retries,
  redirects, failures charged), per-file/arm transfer and decompressed
  bounds, scan bounds, disk scratch/final/combined with reserve/release
  (partials/caches included), deadlines, memory checks, decode
  preflight refusal. No reset API; resume reuses the ledger.
- `text_select.py` — read-only bundle verification against frozen
  digests (bundle/execution/combined/payload/projection/revision,
  text refused per record), frozen-evaluator import by path with
  code-hash binding, exact §6 census/ranking/precedence/ownership
  selection, per-cell requested/eligible/conflicts/selected/shortfall,
  `<=118` assertion, manifest + canonical digest.
- `sparse.py` — file/group decode planning, 256-row batches covering
  the greatest wanted row, membership filtering (gaps decoded but never
  emitted), the four acquisition statuses with the 65536-byte no-excerpt
  rule, unique-text budget, exact-text alias consolidation that never
  alters membership.
- `blinding.py` — 32-byte custodian secret, SHA-256 commitment,
  HMAC-SHA256 `ew2-` review IDs with collision STOP, deterministic
  per-reviewer orders from seed 20260928, packages with a leak assertion
  against the frozen forbidden set, sealed mapping split.
- `rubric.py` — all 18 dimensions transcribed with anchors; `uncertain`
  everywhere (reason required); `not_applicable` only on dimensions
  2/12/14/15/16; `not_reviewed` all-or-nothing; acceptable_as_is
  consistency rule; disagreement/adjudication schemas.
- `receipts.py` — provenance binding (protocol/freeze/policy digests,
  code hashes, Python 3.12.13, `.python-version`, `uv.lock` hash,
  command, exit, caps, parents), atomic no-overwrite publication,
  aggregate completeness check.
- `scripts/evidence_v2.py` — `verify-freeze`, `expand-inventory`,
  `dry-m-plan`, `select-text`, `dry-t-plan` (offline); `plan-footers`
  and `plan-text-costs` default to dry-run and otherwise refuse without
  the explicit `--authorize-network` flag (exit 2, nothing written).
  Live transport was not exercised.

Reuse (not reimplementation): `sampling._det_index` /
`plan_sample_windows` semantics for window identities,
`EssentialWebAdapter` `contract(text_field="text")` for the upstream
field, existing `TransportBudget`/journal/capacity and atomic-writer
machinery remain the live-transport substrate. New: shared arm ledger,
sparse membership layer, blinding, rubric, canonical/provenance layer.

## 3. Real Arm-T locator selection (authorized metadata-only step)

Command: `uv run --offline --locked --no-sync --extra cpu --extra eval
python scripts/evidence_v2.py select-text` (exit 0). Read
`X:\XLM\recon\essential_web/{raw/selected_records.jsonl,bundle.json,execution.json}`
read-only; all frozen identities verified inside the run; every record
asserted text-free; evaluator hash `5a63e785…f9c` equals the freeze
session's recorded sweep source hash (evaluator byte-identical).

Sealed: `G:\Project\xlm-evidence-v2\essential-web\text_selection_manifest.json`
(23,807 bytes, SHA-256 `8424f9668fef6952a558ead0dae27a705545a7f4a4d302713707d3ec6bb1af27`,
manifest digest `975ba3dee4af0598e665ea05c69bbe49336c3e3a35fca777a863073190b78474`).

Results: census exactly 29 B-normal science and 25 D-normal-only
science (STOP gates passed); all ranked cells filled with zero
shortfalls and zero conflicts; total **118/118**, unique.

| Stratum | Per-crawl eligible | Selected |
|---|---|---|
| B_science_census | 29 total | 29 |
| D_only_science_census | 25 total | 25 |
| B_practical_survivor | 6,2,4,7,14,7,15,7 | 1 × 8 |
| B_practical_loss | 4,6,2,4,3,13,5,9 | 1 × 8 |
| D_only_practical | 29,20,34,22,17,16,16,16 | 2 × 8 |
| B_prose_survivor | 20,31,38,51,44,49,57,39 | 1 × 8 |
| B_prose_loss | 6,1,5,5,7,6,6,6 | 1 × 8 |
| D_only_prose | 82,50,34,53,31,43,24,31 | 2 × 8 |

Tightest cell: B-prose-loss in 2015-32 had exactly 1 eligible row (filled,
no borrowing). A dry Arm-T cost plan over this manifest covers 8
development files with all group/physical costs explicitly unresolved
(dry plan digest `78162c13…6d9841`, stdout only, not sealed to G:).

## 4. Verification (synthetic/offline only)

- `tests/test_evidence_v2_core.py` + `tests/test_evidence_v2_text.py`:
  **54 passed** (`-n 0`, sockets blocked, thread env pinned). Covers
  file selection incl. ties/digest-mismatch/unavailable-winner,
  window-v2 identities incl. literal-formula equality and 512-refusal,
  shared budgets incl. retry/redirect/failure charging and no-reset,
  census 29/25 + mismatch/STOP paths, deterministic ranking +
  precedence/conflicts/shortfalls, sparse membership/gaps/statuses/
  aliases, blinding determinism/orders/leak-freedom/collisions, rubric
  exactness + malformed-form refusal, digests/drift/byte hashes,
  CLI offline surface + live refusal.
- `ruff check` clean, `ruff format --check` clean (14 files),
  scoped `mypy` clean (11 source files).
- Environment: Python 3.12.13, torch 2.14.0+cpu, CUDA unavailable
  (CPU-only build; CUDA tests not run, not claimed).
- Full acceptance/live/CUDA suites: NOT RUN (per task boundary).

## 5. Requirement ledger

| Requirement | Status |
|---|---|
| Freeze/protocol/inventory/policy/8-file verification | VERIFIED offline, digests match |
| Arm-M replicate mechanisms (inventory, ranking, windows, dry plans, budgets) | IMPLEMENTED + focused-tested |
| Arm-T locator selection 29/25 census, ≤118, sealed manifest | IMPLEMENTED; real manifest produced (118, digest above) |
| Sparse exact-locator retention layer | IMPLEMENTED + focused-tested |
| Shared budget enforcement M + T | IMPLEMENTED + focused-tested |
| Blinding/review-package mechanisms (no labeling) | IMPLEMENTED + focused-tested |
| Rubric 18 dimensions + validation | IMPLEMENTED + focused-tested |
| Canonical digest/provenance machinery | IMPLEMENTED + focused-tested |
| Footer/window/cost evidence, text acquisition, labeling | NOT RUN (not authorized) |
| Live transport, full acceptance, CUDA | NOT RUN / OUT OF SCOPE |
| Selector/policy/adapter changes | OUT OF SCOPE (none made) |

New source hashes (SHA-256): canonical `2a2f80d6…`, frozen
`80a19463…`, inventory `6731622d…`, windows `9fdfca91…`, budgets
`d1a30db3…`, text_select `42618125…`, sparse `1ded3fa3…`, blinding
`0916a5e2…`, rubric `47bbcbb3…`, receipts `2749a339…`,
`scripts/evidence_v2.py` `99b71f5b…`.

## 6. Physical costs remaining unknown

All of them: footer bytes/layouts, row-group maps, chunk lengths,
codecs, decoder workspaces, request/range counts, text sizes, reviewer
agreement. Caps are ceilings, not estimates; insufficient evidence
means refusal, never assumed zero.

## 7. Exact next operator commands (footer/cost planning ONLY)

These fetch explicitly authorized planning/footer evidence and never
corpus rows/text. Do NOT run until the USER separately enables network.

```powershell
$V=@("run","--offline","--locked","--no-sync","--extra","cpu","--extra","eval")
uv @V python scripts/evidence_v2.py verify-freeze
uv @V python scripts/evidence_v2.py dry-m-plan --out G:\Project\xlm-evidence-v2\essential-web\dry_arm_m_plan.json
# Separately authorized footer inspection for the 8 frozen files (≤80 requests, ≤32 MiB):
uv @V python scripts/evidence_v2.py plan-footers --no-dry-run --authorize-network --out G:\Project\xlm-evidence-v2\essential-web\footer_evidence.json
# Text file/group cost evidence for the sealed 118-locator selection (≤800 requests):
uv @V python scripts/evidence_v2.py plan-text-costs --authorize-network
```

Without `--authorize-network` both planning commands exit 2 and write
nothing. Live transport paths are implemented as gated stubs and were
NOT exercised; the authorized run must record actual requests, bytes,
rows, runtime, disk high water, and memory methodology.

## 8. Stop point

STOP after this report and commit. No footer inspection, acquisition,
labeling, or freeze was performed or authorized here. No X: writes, no
G: writes beyond the single authorized selection manifest, no push.
**READY FOR BOUNDED FOOTER/COST PLANNING** (pending separate user
network authorization), not ready to acquire.

## 9. Live footer/cost planning transport (patch, 2026-09-27, offline agent)

CORRECTION: the §8 verdict above was premature. `plan-footers
--no-dry-run --authorize-network` failed for the user with "live footer
transport is not implemented in this offline task" — footer planning
had only a dry-run emitter plus a refusing stub. This patch implements
the real planning transport; the agent itself stayed offline throughout
(synthetic fixtures + stub opener only, sockets blocked in tests).

Reused existing XLM machinery (no second HTTP/Parquet stack):
`canonical_range_url`, `validate_host`, `TransportBudget`,
`SafeRedirectHandler`, `discover_layout_over_ranges`,
`plan_sample_windows`, `ParquetWindowDecode`, projection resolution —
the same composition the `xlm data sample-blocks` footer path uses —
plus the frozen `ArmLedger` and evidence-v2 provenance. New code:
`src/xlm/data/evidence_v2/footer.py` (live + fake transports, retry,
per-file planning, arm aggregate, incomplete receipts) and
`src/xlm/data/evidence_v2/text_costs.py` (metadata-only Arm-T cost
planning over the same transport).

With `--no-dry-run --authorize-network`, `plan-footers` now: verifies
freeze + inventory first; refuses any non-frozen file; fetches only
header + Parquet footer ranges (4 MiB single-body cap, 30 s timeout,
≤2 retries with 1 s/2 s delays for timeouts/resets/429/502/503/504,
every attempt charged); enforces huggingface.co +
cas-bridge.xethub.hf.co HTTPS/443 with ≤3 redirect hops (all charged);
binds ETag + length per file and refuses drift; resolves the exact
metadata projection (text column presence refused); runs frozen
window-v2 eligibility per group plus `plan_sample_windows`; cross-checks
the deterministic 512-row window against the independent
`windows.freeze_window` identities; records per-group bytes/estimates/
refusals and future-plan feasibility (100 req/plan, 28 MiB data/file,
64 MiB decomp/file, 16384 scan/file, 256 MiB reservation — refusal when
no safe bound); stops the whole arm INCOMPLETE on the first bad file
with no rerank/reseed/substitute. Output `footer_evidence.json` is
atomic with canonical digest + file binding; failures write only a
separate `.incomplete.json` receipt, never a success artifact. All
planning counters live in the same `ArmLedger` later execution
continues (footer ≤80 requests / ≤32 MiB enforced as stage totals on
the shared counter; kind-level accounting added for this).

`plan-text-costs --no-dry-run --authorize-network` reuses the same
transport to read footer/chunk metadata for the text leaf in groups
holding the frozen 118 locators (offsets, compressed/uncompressed
lengths, group sizes only — never page data, never decode, never text),
computes conservative per-file cost uppers against the frozen T caps,
and seals cost evidence or stops INCOMPLETE.

Verification: 34 new tests (`tests/test_evidence_v2_footer.py`, 88
total evidence-v2 tests pass, `-n 0`, no network) cover frozen-file
gating, footer-only ranges (served bytes ≪ file, header + tail zone
only), deterministic windows, request/retry/redirect accounting,
per-file + arm request/byte refusals, body cap, host/redirect-host
refusals, revision pinning, length/ETag drift, schema/missing-field
refusals, 512-row and no-eligible-group refusals, future-plan and
workspace refusals, no-rerank stop, incomplete receipts without success
artifacts, ledger resume accumulation, text-column exclusion, and all T
cost behaviors. `ruff check` / `format --check` clean, scoped `mypy`
clean. Live transport itself was NOT run (no network in this task).

Frozen identities unchanged: freeze `fe215779…`, policy `f4357f61…`,
23200-path inventory, 8 winners; locator manifest digest recomputed
`975ba3dee4af0598e665ea05c69bbe49336c3e3a35fca777a863073190b78474`
(118 rows, unchanged).

## 10. Redirect accounting audit + fix (2026-09-27, offline agent)

The authorized run stopped at the second file with "redirect hop 4
exceeds the 3-hop ceiling" after zero retries and 4 arm requests, with
file 1 complete (3 requests). Audit verdict: implementation counting
bug (B), not a genuine fourth redirect (A).

Pre-patch semantics: `HopCountingRedirectHandler.hops` lived on the
transport (one per arm run) and incremented on every redirect_request
call — across all ranges, retry attempts, and files — with no reset.
File 1's ranges consumed hops 1–3; file 2's first range followed its
first redirect as hop 4 and refused. Any single chain with exactly 3
transitions could never produce "hop 4" by itself.

Fix (cap unchanged at 3, no protocol edit): the chain resets for every
issued request — `handler.reset_chain()` before each `opener.open`,
including retries, which re-resolve independently. Precise definitions
now documented in code: initial request count = top-level `open` calls;
redirect transition count = Location follows resolving one issued
request (the original is hop 0, never counted); retry count = repeat
opens (attempts, never hops); logical request count = issued requests;
physical HTTP attempt count = attempts + followed redirects, all
charged to the ArmLedger as footer requests. Retries therefore add
attempts but never hops; a post-retry chain is counted from zero.

Receipt diagnostics (future failures only; the user's existing receipt
is untouched): refusals now carry file, range, frozen resource URL,
retry attempt number, transition total, and a per-hop chain (status,
source/dest hosts, sanitized path, allowlist verdict) with query
strings, fragments, and userinfo stripped before recording — signed
credentials can never reach a receipt. `RangeEvidence` carries hops +
chain; per-file units log per-range hops; `ArmIncomplete` propagates
the chain into `redirect_diagnostics` in the incomplete receipt.

Verification: 13 new mocked-HTTP tests prove 0/1/2/3-redirect chains
pass, a 4th transition refuses before R4 is fetched, the original
request is not hop 1, timeout retries add no hops, post-retry chains
count from zero, chains reset per logical request (the two-sequential-
2-hop test fails on the pre-fix code), every attempt + redirect
charges the ledger, evil targets refuse before following, and
credentials are redacted from diagnostics and receipts. 101
evidence-v2 tests pass; ruff/format/mypy clean. Cap, freeze, policy,
and 118-manifest identities unchanged.

## 11. Arm-T cost refusal audit (2026-09-27, offline agent, no text)

The authorized run completed Arm M (8/8 feasible, 48 footer requests,
1,922,640 footer bytes, no retries/failures; 85-request 512-row future
plans) and then refused Arm-T cost planning on the first file,
`.../2014-15/train-01787-of-02772.parquet`: `transfer upper 26707302
exceeds data/file cap` (14,680,064 bytes; also above the 16,777,216
total/file cap). Cap unchanged. No text inspected; no X: reads.

Exact derivation: `transfer_upper = text_compressed_bytes +
chunk_count × 4,194,304`, where both terms cover only row groups
holding frozen wanted rows (whole text column chunks, the minimum unit
the stack can read, plus one 4 MiB range-framing buffer per chunk, the
same prefix+buffer convention as the audited window estimator).
Decompressed upper = whole-chunk uncompressed text (minimum decodable
unit); request upper = chunks + wanted groups + 4; scan upper =
256-row batches covering each group's greatest wanted row. The
decompressed/scan/request checks were not reached for this file.

Failing-file selection facts (from the sealed G: manifest, metadata
only): 16 locators, rows 55315–55792, span 478 of the 512-row window
[55285, 55797). Exact row groups, chunk sizes, and page structure are
not in the refusal receipt and are unknowable offline (no network/X:
access); the receipt now preserves the failed unit's numbers for
future authorized audits (see below).

Yes, this is whole-chunk conservative accounting: the code assumes
reading entire text column chunks because execution cannot do less.
pyarrow 25.0.1 exposes zero page/index/offset APIs on `ParquetFile`;
the stack's minimum read unit is whole column-chunk spans
(`_column_chunk_spans`, dictionary page included by construction), and
`plan.py` documents "no page skipping". Whether these files carry page
indexes is unknowable offline — and immaterial: even with perfect page
metadata, no decoder in the stack can execute a single-page read, so a
page-subset cost bound would be unenforceable at execution (a second
decoder stack is forbidden and uncertifiable here). Dropping the 4 MiB
framing slack would assume exact-range execution discipline that no
implemented T execution path provides. Dictionaries are requisite for
any decode (format fact) and shared per chunk (format fact); the
selected span covers ~93% of the window's row extent, so page-level
savings would be marginal regardless.

Therefore no tighter SAFE mechanically enforceable bound exists with
the current infrastructure: keep the refusal. The protocol's
"conservative whole projected-chunk bounds are acceptable even when
they force refusal" covers exactly this outcome.

Receipt improvement (observability only; refusal logic, caps, and
selection untouched): infeasible files now preserve their computed
cost evidence (`failed_unit`: wanted rows, groups, chunk sizes, uppers,
reasons — numbers only, never text) in `TextCostsIncomplete` and in
future `.incomplete.json` receipts, so the C/n decomposition above is
answerable without re-fetching footers. The user's existing receipt is
untouched. Tested: formula-exact uppers, numbers-only dumps, receipt
round-trip, no success artifact, footer-only ranges.

103 evidence-v2 tests pass (`-n 0`, sockets blocked); `ruff check` /
`format --check` clean; scoped `mypy` clean. Frozen identities (freeze,
policy, inventory, 8 files, 118-manifest, strata, 16 MiB cap)
unchanged. No G: writes in this task.
