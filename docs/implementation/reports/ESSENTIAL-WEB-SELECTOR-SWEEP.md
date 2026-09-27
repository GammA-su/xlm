# Essential-Web selector sweep (bounded offline experiment)

Date: 2026-09-27. Branch: `data/mix01-ultrax-6b`. Starting HEAD: `b817b11`
(dirty only with Astra's preserved review docs). Agent work is offline
only: no network, fetch, X: reads, corpus-text inspection, tokenizer,
training, production admission, quota changes, or push.

**EXPERIMENT ONLY. No policy here is final or approved.** This tool
implements exactly the frozen hypotheses in
`recipes/selectors/essential_web_selector_sweep_v1.yaml`, whose canonical
digest is:

```
f4357f61f434d5105d823266153dac6372e122f12767187631e8796b4899dd07
```

The sweep result binds this digest; any edit to the spec file changes it
(regression-tested). The authoritative policy prose is
[ESSENTIAL-WEB-SELECTOR-POLICY-REVIEW](ESSENTIAL-WEB-SELECTOR-POLICY-REVIEW.md);
nothing below reinterprets it.

## 1. Policy specification and versioning

- Spec file: `recipes/selectors/essential_web_selector_sweep_v1.yaml`
  (`policy_spec_version: essential-web-selector-sweep-v1`, schema 1).
- Digest: SHA-256 over canonical JSON of the parsed spec (sorted keys,
  compact separators, raw UTF-8 — the same scheme as recon manifests), so
  formatting/whitespace changes do not alter it but any semantic change
  does. Computed by `load_policy_spec` / `policy_digest_of`.
- Tool: `scripts/essential_web_selector_sweep.py` (`TOOL_VERSION 1`).
- Semantics (from the review, frozen verbatim): F/D/K/A/M/T/E field paths;
  FDC stays a string (`[0-9]{3}(?:[.][0-9]+)?`, anomaly `320.973/0207`
  quarantined as `invalid_fdc_syntax`); E numeric/finite/non-bool in
  [0,1]; K exactly Factual/Conceptual/Procedural; T common-allowed set
  with Partially Correct / Technically Flawed rejected; 11-label D union
  with documented known-excluded and unknown-label handling; gates
  GN (E>=0.80, No Artifacts, No-missing-or-Missing-Images),
  GS (E>=0.90, No Artifacts, No-missing-only),
  GD (E>=0.80, No-Artifacts-or-Irrelevant, No-missing-or-Missing-Images),
  all conjunctive; predicates S5 (F`5*` + 6 science genres), S61 (exact
  610/612–618 prefix + Academic/Knowledge + Conceptual + positive
  correctness), P (explicit instructional genres OR Procedural +
  Q&A/Knowledge/Personal-Blog), R (5 narrative genres + Factual/
  Conceptual), F6 (`6*`), F789 (`7/8/9*`); variants A (S5/P/R),
  B (S5|S61/P/R), C (S5|S61, F6∧P, F789∧R), D (GD normal, else B);
  precedence validity → science → practical → prose → unassigned, rejected
  on gate failure; B-strict == D-strict by construction (asserted).
- Small named pure predicates (`validate_row`, `gate_gn/gs/gd`,
  `predicate_s5/s61/p/r/f6/f789`, `evaluate_policy`); no expression
  engine. Scientific policy is auditable from code + spec.

## 2. Input binding (fail closed)

Real inputs (user-owned, read-only):
`X:\XLM\recon\essential_web\raw\selected_records.jsonl`,
`bundle.json`, `execution.json` (4096 records, 8×512 parts, revision
`ce4eccc7e9604667b6d7f32cb6274b8b41f3113d`, projection
`eai_taxonomy,quality_signals`). The tool verifies: bundle/execution
self-digests, `bundle.execution_digest == execution.digest`, projection
and revision equality against expectations (revision must be 40-hex),
bundle combined-SHA-256 recomputed over the streamed payload bytes,
combined byte count, record total (default exactly 4096), eight parts in
stratum order with 512-record spans and eight unique plan hashes, locator
(file/revision/repository/range/duplicate) checks per row. Any mismatch
exits nonzero with no outputs written.

## 3. Resource caps (frozen in the spec)

max_records 5000, max_input_bytes 32 MiB, max JSON line 1 MiB, max output
64 MiB total, scratch 64 MiB (no scratch files are written; reported 0),
max runtime 120 s (deadline checked every 512 rows and before output).
The real ~15 MiB bundle fits comfortably. Streaming read; bounded
in-memory counters only. Wall time, peak RSS (psutil), input/output
bytes go to STDOUT, never into artifacts, so identical inputs stay
byte-identical (regression-tested).

## 4. Report structure

Output dir holds exactly (deterministic sorted-key JSON + one Markdown;
`text` never read or emitted; locator lists bounded at 50 anomaly rows):
`sweep_manifest.json` (tool/policy/binding/caps digests, per-artifact
SHA-256), `policy_spec.json` (frozen spec copy), `summary.json` (global
finals, conservation, B==D/B==C-science/subset/gate-superset invariants),
`summary.md` (headline tables), `per_crawl.json` (denominators, validity,
gate/predicate/final counts, retention per crawl), `attrition.json`
(fixed-order waterfalls + validity reasons; reason sums may overlap),
`overlaps.json` (predicate pairs, component matches, precedence
transfers, all A/B/C/D × normal/strict transitions),
`crosstabs.json` (English distributions, label/FDC compositions, science
balance, practical branches/domains, prose genres, bounded cross-tabs;
word counts `unavailable` — no such field in the projection),
`diagnostics.json` (one-condition sensitivity for E 0.65/0.80/0.90/0.95,
artifact and missing-content options — diagnostic only, never policies;
temporal review flags with documented thresholds; anomaly/unknown-value
detail). Cross-tab cardinality is bounded by sparse exact cells plus
top-25 full FDC codes; totals are always present so nothing is lost.

## 5. Expected USER command

One validation, then one run (PowerShell, from `G:\Project\xlm-data-ultrax`):

```powershell
$R="X:\XLM\recon\essential_web"; $O="G:\Project\xlm-selector-sweeps\essential-web-v1"
$UV=@("run","--offline","--locked","--no-sync","--extra","cpu","--extra","eval")
$B="--expect-bundle-digest ac1c13b656d9dc3bc506726bfe3f1db925444efb899733052496be0425c58eb3"
$E="--expect-execution-digest 5065bcf6001ee38b63ca783f20a625af0256f2788ce9a0034edfba34c125bc23"
$C="--expect-combined-sha256 42e07c350d849c608ef202348971e3f7ea48aa80775d413a39dd3373628c58e1"
$V="--expect-revision ce4eccc7e9604667b6d7f32cb6274b8b41f3113d"
uv @UV python scripts/essential_web_selector_sweep.py validate-input --records "$R\raw\selected_records.jsonl" --bundle "$R\bundle.json" --execution "$R\execution.json" $B $E $C --expect-records 4096 $V
uv @UV python scripts/essential_web_selector_sweep.py run --records "$R\raw\selected_records.jsonl" --bundle "$R\bundle.json" --execution "$R\execution.json" $B $E $C --expect-records 4096 $V --output-dir $O
```

## 6. Interpretation limits

Eight contiguous 512-row windows are clustered diagnostics, not corpus
estimates; equal windows give equal-crawl averages, not corpus weighting;
row-independent confidence intervals are inappropriate; pre-gate
aggregates and derived ceilings are not post-selection results; scores
are uncalibrated (not English-purity probabilities); retaining N/A
correctness asserts nothing about correctness; word counts/tokens,
dedup, domain concentration and semantic precision are unmeasured here.
Sensitivity cells and temporal flags are diagnostics, never approvals.

## 7. Exact stop point

STOP after the output dir holds the nine artifacts and the manifest
digest is recorded. Send back `sweep_manifest.json` (with its digest),
`summary.json`, `summary.md`, `per_crawl.json`, `attrition.json`,
`overlaps.json`, `crosstabs.json`, `diagnostics.json`,
`policy_spec.json`, plus the stdout resource line. Do NOT proceed to
selector freezing, adapter changes, calibration, admission, or quotas.

## 8. Verification performed by the agent (offline, synthetic only)

39 focused tests pass (this file's suite): exact A/B/C/D × normal/strict
semantics, gates, predicates, precedence, overlaps/transfers, zero final
overlap, exact conservation, B==D and subset invariants, anomaly
quarantine (`320.973/0207` invalid, never 3xx), leading-zero `005.4` as
0xx strings, unknown/missing/bad-type/English rule refusals, text
independence, byte-identical reruns, spec-digest sensitivity, all input
binding refusals, duplicate-locator/foreign-row/corrupt-line refusals,
input/line/output/runtime caps, sensitivity monotonicity, temporal-flag
mechanics, distribution/composition shapes, CLI exit codes, socket-blocked
no-network fixture. `ruff check`, `ruff format --check`, and scoped
`mypy` are clean. No real X: data was read or executed.

Requirement ledger: experiment IMPLEMENTED + VERIFIED on synthetic
fixtures; real 4096-row execution NOT RUN (user-owned); production
selectors, adapter changes, admission, tokenizer, training OUT OF SCOPE.
A skip was never counted as a pass; no benchmark numbers were fabricated.
