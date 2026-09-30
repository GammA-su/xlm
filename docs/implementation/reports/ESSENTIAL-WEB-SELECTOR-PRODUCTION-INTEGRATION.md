# Essential-Web frozen selector: production integration

**ESSENTIAL-WEB SELECTOR FROZEN — READY FOR PRODUCTION ACQUISITION REVIEW**

2026-09-30, branch `data/mix01-ultrax-6b`, on freeze commit
`9586778ef5ac594900efb4b9bf78daaa0ee5c6ce`. The production Essential-Web
admission path now uses the exact frozen B-normal selector (freeze digest
`c6f32a65f083c99b64245e25151f2cc73275093e1013d68b625c6d6f63d10a0c`). It
reproduces B-normal row for row on both 4,096-row replicates. Bulk
acquisition is **not** ready: admission, inventory and the acquisition plan
are open (see the readiness vector).

The selector is a metadata-only fast-track decision. The Arm-T semantic
review was not run; nothing here claims the admitted documents are good.

No network, acquisition, Arm-T access, labels, weight or quota change, or
push.

[Evidence directory](../evidence/ESSENTIAL-WEB-SELECTOR-PRODUCTION-INTEGRATION/),
[development reproduction](../evidence/ESSENTIAL-WEB-SELECTOR-PRODUCTION-INTEGRATION/development_reproduction.json),
[M reproduction](../evidence/ESSENTIAL-WEB-SELECTOR-PRODUCTION-INTEGRATION/m_reproduction.json),
[dry plan](../evidence/ESSENTIAL-WEB-SELECTOR-PRODUCTION-INTEGRATION/production_selection_dry_plan.json),
[readiness](../evidence/ESSENTIAL-WEB-SELECTOR-PRODUCTION-INTEGRATION/readiness.json),
[exact commands](../evidence/ESSENTIAL-WEB-SELECTOR-PRODUCTION-INTEGRATION/COMMANDS.md),
[freeze report](ESSENTIAL-WEB-SELECTOR-FASTTRACK-FREEZE.md).

## What was there before

The production adapter `essential_web` takes a component name and stamps it
on every row. It selects nothing: the three Essential passes would have
produced the same rows three times. No production selector existed.

## Design

The smallest change that gives exact B-normal admission on the existing
`xlm data adapt` path:

- **`src/xlm/data/adapters/essential_web_selector.py`** holds no selector
  logic. It loads the frozen evaluator file and the policy spec by path,
  refuses both unless their bytes hash to the frozen identities (evaluator
  `5a63e785…`, policy file `c27a0aae…`, canonical policy digest
  `f4357f61…`), executes the exact bytes it hashed, and delegates each row
  to the evaluator's own `validate_row` and `evaluate_policy("B",
  "normal")`. A changed evaluator, a tuned threshold or even a
  comment-only edit of the spec makes it refuse to load.
- **`EssentialWebSelectedAdapter`** (adapter id `essential_web_bnormal`) is
  constructed with one component, as before. It renders with the certified
  `EssentialWebAdapter`, unchanged, and then admits the row only if the
  frozen selector's single final component equals its own component.
  Admitted documents also record `essential_web_selector: B-normal`, the
  policy digest and the freeze digest.
- Rows not admitted are policy drops with three distinct rejection codes:
  `EssentialWebSelectorRejectedError` (validity or gate),
  `EssentialWebSelectorUnassignedError` and
  `EssentialWebSelectorOtherComponentError`. With `--on-reject record` the
  adaptation summary therefore counts each outcome. Reasons carry the
  evaluator's reason codes only, never text or label values.
- The three Essential views in `recipes/mixtures/mix01_views.yaml` now bind
  `essential_web_bnormal` and record the selector identity. The column
  contract and the calibration driver's three Essential units point at the
  same adapter.

Precedence needs no extra code. Each row has one frozen final component, so
the three passes are disjoint and follow science, practical, prose.

Two behaviours to know:

1. **Malformed rows stay fatal.** Rendering runs first, so a row the
   certified adapter refuses (no `id`, empty `text`, non-numeric English
   score, empty FDC code) aborts the adaptation even if the selector would
   have rejected it. This keeps the existing rule that a schema fault is
   never hidden behind a recordable rejection. It also means a single
   malformed row stops a bulk pass.
2. **The legacy `essential_web` adapter still exists** for the certification
   tests. The registry no longer binds it, and the readiness check refuses
   an admission decision that names it.

## Offline verification

### Real evidence (metadata only, no text)

Every row went through the three production adapters' admission. The five
final counts were compared with the frozen sweep artifacts, overall and for
each of the eight crawls.

| Replicate | Science | Practical | Prose | Unassigned | Rejected | Sum | Per-crawl match | Overlap |
|---|---:|---:|---:|---:|---:|---:|---|---:|
| Development, 4,096 rows | 29 | 108 | 371 | 36 | 3552 | 4096 | 8 of 8 | 0 |
| M (sealed), 4,096 rows | 24 | 117 | 372 | 45 | 3538 | 4096 | 8 of 8 | 0 |

Both match exactly. No row is admitted by more than one component.

The development bundle is re-evaluated row by row here for the first time
since the sweep. The M analysis recorded it as unavailable; it is present at
`G:\XLM\recon\essential_web` and its digests equal the frozen development
binding (bundle `ac1c13b6…`, payload `42e07c35…`). The M seal is unchanged
and still verifies.

These replicates carry no `text`, so they exercise admission, not document
rendering. Rendering plus admission together ran on the three real
certification rows (`adapter-cert-essential-web01`): one is admitted as
prose, two are rejected at the gate.

### Authored fixtures

57 tests in `tests/test_essential_web_production_selector.py`:

- 41 edge fixtures with exact expected outcomes: S5 and S61 science, the
  61x allowlist boundary (611 and 619 excluded), FDC `005.4` as 0xx, every
  precedence pair, both practical branches, English at 0.8 and 0.7999,
  `Irrelevant Content` rejected, `Missing Images or Figures` admitted, both
  rejected correctness labels, an excluded document type, invalid FDC
  syntax, unknown labels and wrong types;
- a 28,080-row grid on which the adapters equal the frozen evaluator and
  never overlap;
- loader refusal on a changed evaluator, a tuned policy, a comment-only
  policy edit and a missing file;
- rendering identity with the certified adapter, fatal-first ordering,
  rejection codes, and messages free of text;
- `xlm data adapt` run three times over the same six raw rows, giving a
  disjoint partition and per-code rejection counts;
- registry binding and unchanged Mix-01 weights;
- reproduction, dry-plan and readiness logic, including refusals.

## Mix-01 configuration impact

| Item | Change |
|---|---|
| Weights (`mix01.yaml`) | none; UltraX stays 20%, Essential 10% / 10% / 5% |
| Quotas (`mix01_quotas_6b.yaml`) | none |
| Registry id | unchanged (`mix01_views_v2`) |
| Essential views | `adapter_id` → `essential_web_bnormal`; selector identity and the six taxonomy paths recorded |
| Other eight components and `txt360_web` | untouched |
| Pool view selectors | unchanged: they still match `mix01_component` |

## Dry production-selection plan

Nothing was run. Stages, in order, all `NOT RUN`: probe, inventory,
calibrate, plan, admit, fetch and verify, adapt. One raw fetch feeds three
adapt passes, one per component, each with `--adapter essential_web_bnormal
--adapter-config <component> --on-reject record`.

Observed admission (descriptive counts over clustered windows, not rates
with uncertainty):

| Component | Weight | Final quota (tokens) | Development | M | Both replicates |
|---|---:|---:|---:|---:|---:|
| essential_science | 0.10 | 600,000,000 | 29 / 4096 | 24 / 4096 | 53 / 8192 = 0.65% |
| essential_practical | 0.10 | 600,000,000 | 108 / 4096 | 117 / 4096 | 225 / 8192 = 2.75% |
| essential_prose | 0.05 | 300,000,000 | 371 / 4096 | 372 / 4096 | 743 / 8192 = 9.07% |

Science is the sparse slice, yet its quota equals the practical quota and is
twice the prose quota. Science will therefore bound how many rows must be
scanned: about 155 scanned rows per admitted science row on these counts.

Not measured, and not zero: tokens per admitted row, transferred bytes per
scanned row with the text column, and the rows and files needed per
component. No byte or file estimate is given.

## Readiness for bulk acquisition

| Check | Result | Basis |
|---|---|---|
| `selector_frozen` | **true** | committed freeze, digest equals the code constant |
| `selector_integration_ok` | **true** | registry binds the adapter and identity; both reproductions match |
| `source_revision_ok` | **true** | registry, freeze, evaluator pin and evidence all at `ce4eccc7…3113d` |
| `production_admission_ok` | **false** | see blockers 1 and 2 |
| `inventory_ready` | **false** | see blocker 3 |
| `acquisition_plan_ready` | **false** | see blockers 4 and 5 |

The operator store was read at `G:\XLM` (read-only). Existing limits: pilot
caps of 256 MiB transferred, 25,000 records, 2 GiB output, 100 requests and
100,000 scanned records; anything larger requires recorded production
admission. `data adapt` defaults to a 64 MiB input cap.

### Remaining blockers

1. **No usable probe evidence.** The only stored Essential probe
   (`essential_science`) ended `budget_exhausted` with no revision, schema
   or fingerprint. `essential_practical` and `essential_prose` have none.
2. **No admission decision** for any Essential view. Each needs license and
   benchmark review and an operator `data admit` naming adapter
   `essential_web_bnormal`.
3. **No frozen production inventory** for `essential_web` in
   `G:\XLM\inventories`.
4. **No calibration under the frozen selector.** `calibration.json` has no
   Essential entry. The driver's 1,000-record default would yield about six
   science rows, and its default file path
   (`data/v1/train/00001.parquet`) does not match the layout seen in the
   evidence (`data/crawl=…/train-…-of-….parquet`). The calibration sample
   must be resized and its files chosen from the inventory.
5. **Acquisition cost is unknown and may be large.** The fetcher downloads
   the text column for every scanned row, and under B-normal about 87% of
   rows are rejected and a further 1% unassigned. Whether to accept that
   cost or to add a metadata-first pass that fetches text only for admitted
   rows is a decision for the acquisition review. No such pass exists on the
   production path today.
6. **One malformed row aborts a pass** (design point 1). The review should
   confirm this is acceptable at bulk scale.

## Requirement ledger

| Requirement | Status |
|---|---|
| Production admission uses exact B-normal, no rewritten logic | IMPLEMENTED, VERIFIED (hash-bound loader; grid equality with the evaluator) |
| B-normal reproduced on development, row level and per crawl | VERIFIED (real metadata evidence) |
| B-normal reproduced on sealed M: 24 / 117 / 372 / 45 / 3538, sum 4096 | VERIFIED (real metadata evidence) |
| No overlap after precedence | VERIFIED (both replicates, grid, CLI partition) |
| Authored edge fixtures | VERIFIED (41 cases) |
| Rendering plus admission on real rows | VERIFIED on 3 certification rows only |
| Rendering on the replicates | NOT RUN (replicates hold no text) |
| Registry, column contract, calibration driver bound to the adapter | IMPLEMENTED, VERIFIED (tests; driver tests pass) |
| Mix-01 weights and quotas unchanged | VERIFIED |
| M seal and freeze still verify | VERIFIED (exit 0 after integration) |
| No Arm-T input consulted | VERIFIED by construction (path refusal; metadata-only inputs) |
| Dry production-selection plan | IMPLEMENTED (nothing run) |
| Readiness vector | IMPLEMENTED, VERIFIED against the real operator store (read-only) |
| Refusal paths | VERIFIED (synthetic tests only; a mock proves logic, not live data) |
| Ruff check and format | VERIFIED |
| Strict mypy | VERIFIED with the interpreted runner; compiled mypy BLOCKED by application control |
| Live adapter run on bulk rows, probe, admission, inventory, calibration, fetch | NOT RUN |
| Fast and full offline selections, CUDA, network tests | NOT RUN |
| Measured process peak memory | NOT MEASURED |

Measured resources: each 4,096-row reproduction ran in a few seconds
including interpreter start; outputs total under 40 KB in Git.

## Exact next operator action

Hold a production-acquisition review for Essential-Web with this report and
the dry plan. It must decide blockers 4 to 6, then authorize, in order: a
live probe for the three views, the inventory freeze, and a resized
calibration through `essential_web_bnormal`. First command after the review
(network, operator-run, not executed here):

`uv run --locked --extra cpu xlm data probe --catalog manifests/datasets.catalog.yaml --source essential_web --view essential_science --live --budget-mib <MIB> --probe-id <ID> --json`

The earlier probe failed at its body limit, so the budget must be set by the
review, not reused.

**ESSENTIAL-WEB SELECTOR FROZEN — READY FOR PRODUCTION ACQUISITION REVIEW**
