# IFM requirement split + Common Pile audit (offline, no acquisition)

Base: `5b6df0eba8629e65f6c904ed751b52dce89e19d7` on `data/mix01-ultrax-6b`.
Worktree: `F:\Project\xlm-ifm-audit`, branch `prep/ifm-split-audit`.
Primary checkout untouched. No network, acquisition, admission, or
authorization. Operator-artifact reads (`G:\XLM`) are read-only.

## A. IFM requirement-split mechanism

Status before this task: MISSING. `evaluate_policy` refused every multi-view
source (`mix01_source.py`: "needs an explicit per-view requirement decision")
and no command recorded the decision. `record --combine-sources` only sums
calibration entries (already used: combined `ifm_behaviors_general_planning`
2601 rows exists in `G:\XLM\calib\calibration.json`); it does not split the
component requirement.

Implemented (smallest generic mechanism; no operator choice recorded):

1. Exact command (new, generic, offline):
   - `python scripts/mix01_source.py requirement split-record --component <id> --view-tokens general=165000000,planning=165000000 --operator <name> --rationale <text> [--data-root G:\XLM] [--requirement-split <path>]`
   - `python scripts/mix01_source.py requirement split-show --component <id> [--data-root G:\XLM]`
2. Artifact schema (`mix01_view_requirement_split` v1, self-digested):
   `component_id`, bound `quotas_sha256`/`estimate_sha256`, component
   first-pass/final tokens, `bytes_per_token_base` (4), `safety_margin`,
   `views: {view: {first_pass_tokens, final_tokens, required_canonical_bytes}}`,
   `operator`, `rationale`, `digest`. Default home:
   `<data-root>/calib/requirement_splits/<component>.json` (write-once:
   identical re-record reuses, divergent refuses).
3. Accepted units: FIRST-PASS TOKENS per view (positive integers). Canonical
   bytes follow at exactly 4/token; final tokens follow the quota headroom
   ratio exactly (integer-exact, else refused).
4. Digested/frozen: canonical digest over the body; `check_view_split`
   re-derives from the same quotas+estimate and compares.
5. Consumption: `requirement_of` resolves per-view requirements from the
   split for multi-view source-keys (else keeps the old refusal);
   `policy model`, `policy freeze`, and `plan` consume it unchanged through
   `requirement_of`/`evaluate_policy`; `sufficiency`/`seal` operate per plan
   record, which now carries `requirement.view_id`/`split_digest` and
   `inputs.requirement_split` only when split-bound (historical records
   verify unchanged).
6. Independence: the two views keep independent inventories, plans, and seals
   (per source-key roots `ifm_general` / `ifm_planning`). The component is
   complete when both seals are SUFFICIENT and their first-pass tokens sum to
   the component quota.
7. 50/50 effect: exactly general 165,000,000 + planning 165,000,000
   first-pass (final 150,000,000 + 150,000,000; canonical 660,000,000 B each).
   Verified in `tests/test_ifm_requirement_split.py`.

## B. IFM inventory filters

Pinned source `IFM/Pretrain-Behaviors` @
`3345e13d7f3f6d0ecb5fdd67b37aed289f3191f5` (registry, probes, listings agree).
Confirmed against the operator's already-frozen listings (read-only):

- general: path-prefix `general/`, extension `.parquet`, no globs —
  632 files, 12,698,074,147,764 declared bytes.
- planning: path-prefix `planning/`, extension `.parquet`, no globs —
  834 files, 1,671,906,970,539 declared bytes.

Future re-list commands (do not execute):

- `python scripts/mix01_inventory.py list-hf --source ifm_behaviors --view general --repo IFM/Pretrain-Behaviors --revision 3345e13d7f3f6d0ecb5fdd67b37aed289f3191f5 --path-prefix general/ --extension .parquet --output G:\XLM\inventories\ifm_general.listing.json`
- `python scripts/mix01_inventory.py list-hf --source ifm_behaviors --view planning --repo IFM/Pretrain-Behaviors --revision 3345e13d7f3f6d0ecb5fdd67b37aed289f3191f5 --path-prefix planning/ --extension .parquet --output G:\XLM\inventories\ifm_planning.listing.json`

## C. IFM current evidence (both views)

- Bridge: dry-run digests `1330da81…` (general, 698/698) and `fb1a2b96…`
  (planning, 1903/1903) reproduce recorded documents; apache-2.0.
- Calibration: per-view entries plus combined `ifm_behaviors_general_planning`
  (2601 rows) recorded in operator `calibration.json`; estimate ESTIMATED
  (combined transfer base 655,314,194 B).
- Adapters: independent `ifm_general`/`ifm_planning` (different schemas).
- Admission: decisions recorded for both views; metadata probes `partial` at
  the pinned revision.
- Modeled transport: previously refused solely for the missing split; now
  unblocked pending the operator's split decision.
- Inventory: frozen listings + inventories exist for both views.
- Readiness: everything except the requirement split is done. Next step is
  the operator's `requirement split-record` (decision, not code).

## D. Common Pile blocker (unchanged, still blocked)

- Component `common_pile_prose` (5%, final 300M / first-pass 330M), source
  `common-pile/comma_v0.1_training_dataset` @ `5afc546d…1e7c`.
- Intended upstream components: top-level source directories of file paths
  (~31 per adapter/registry notes); exact current list not enumerable offline
  (no listing, no inventory).
- Allowlist: none exists; registry leaves allowlist/weights/licensing as
  unresolved policy decisions.
- License/provenance: repository license NULL; per-component licenses not
  recorded; only news, libretexts, public_domain_review rows (9 real rows)
  ever certified — 28 components unknown.
- Cannot be unblocked from existing evidence: no probe, no calibration,
  no admission possible.
- Later metadata-only network research WILL be required: tree listing at the
  pinned revision to enumerate top-level components, plus per-component
  license/terms research from dataset documentation.
- Smallest next operator action: none executable now beyond review — first
  record the component allowlist as a policy decision, then per-component
  license/provenance review, then probe. Common Pile keeps its 5%; nothing
  renormalized, nothing substituted.

## Next operator commands (IFM, after SYNTH/simple sources at will)

- `uv run --offline --locked --extra cpu --extra eval python scripts/mix01_source.py requirement split-record --component ifm_behaviors_general_planning --view-tokens general=165000000,planning=165000000 --operator <name> --rationale <text> --data-root G:\XLM`
- `uv run --offline --locked --extra cpu --extra eval python scripts/mix01_source.py requirement split-show --component ifm_behaviors_general_planning --data-root G:\XLM`
- `uv run --offline --locked --extra cpu --extra eval python scripts/mix01_source.py policy freeze --source-key ifm_general --data-root G:\XLM`
- `uv run --offline --locked --extra cpu --extra eval python scripts/mix01_source.py policy freeze --source-key ifm_planning --data-root G:\XLM`
- `uv run --offline --locked --extra cpu --extra eval python scripts/mix01_source.py plan --source-key ifm_general --data-root G:\XLM`
- `uv run --offline --locked --extra cpu --extra eval python scripts/mix01_source.py plan --source-key ifm_planning --data-root G:\XLM`

## Checks

- `tests/test_ifm_requirement_split.py -n 0`: 8 passed (authored fixtures).
- Related `test_source_plan/test_source_run/test_mix01_inventory/test_cli_inventory -n 0`: 164 passed.
- `ruff check`, `ruff format --check`, `mypy --strict` (source_plan), `git diff --check`: exit 0.
