# Mix-01 6B PRODUCTION ACQUISITION PLAN (operator planning; no live run)

Branch: `data/mix01-ultrax-6b` at `c5bae3e` (Track A closeout). Worktree
`G:\Project\xlm-data-ultrax`. This document PLANS ONLY. No bulk download,
no tokenizer, no training, no pilot was performed or authorized here. The
USER executes every live command in §10. Verdict in §12 does NOT authorize
bulk acquisition.

Target: 6,000,000,000 exact XLM valid tokens of prepared availability
across 11 Mix-01 components (weights/quotas frozen in
`recipes/mixtures/mix01.yaml` + `recipes/mixtures/mix01_quotas_6b.yaml`).
First-pass headroom ≈ 110% / 6.6B ESTIMATED usable tokens. Estimated tokens
are never exact XLM tokens: exact counts exist only after the 32,768-token
tokenizer is frozen (separate later task, §11).

Conventions: `<DATA>` = operator-chosen data drive root (re-measured §3);
`<HOME>` = `D:\Project\xlm-operator-ultrax` style operator home (holds
Track A probe evidence); plan/artifact paths below are exact in structure,
with file arguments bound to Phase D artifacts. Every command is one-line
PowerShell. Only real XLM CLI surface is used (`xlm data probe|audit|
admit|sample-blocks|plan|fetch|status|verify|adapt|performance|mix01-status`,
`xlm mixture preset-validate|preset-diff`); plan hashes are read from the
plan JSON the CLI writes. New planning helper: `scripts/mix01_inventory.py`
(`freeze|estimate|sufficiency`, offline, tested).

## 1. Source admission matrix (Phase A)

Policy (actual code): production fetch (`plan_requires_production_admission`,
i.e. beyond 256 MiB / 25,000 records / 2 GiB output or non-pilot) calls
`resolve_verified_production_admission`, which requires BOTH (a) in-store
real probe evidence (`ACCESSIBLE`, revision, adapter-verified schema,
fingerprint) from `xlm data probe --live`, and (b) a recorded `xlm data
admit` decision (operator-approved, license_review approved, benchmark
risk clean, revision+fingerprint match). No status below is invented: the
operator store starts empty, so every source is UNADMITTED until the
operator records evidence + decision. Legal approval is never automatic.

| # | Component (weight) | source_id / view | Repository @ pinned revision | Adapter (spec) | Lang/domain | License (published) | Adapter-cert | Admission | Live-probe requirement / gating reason |
|---|---|---|---|---|---|---|---|---|---|
| 1 | essential_science 10% | essential_web / essential_science | EssentialAI/essential-web-v1.0 @ ce4eccc7…3113d | essential_web (`essential_web:essential_science`) | en / science slice | odc-by | live-certified 3 real rows (offline sample) | UNADMITTED | `data probe --live` evidence + `data admit` (license+benchmark review pending) |
| 2 | essential_practical 10% | essential_web / essential_practical | same @ ce4eccc7…3113d | essential_web (`essential_web:essential_practical`) | en / practical slice | odc-by | live-certified 3 real rows | UNADMITTED | same as #1 (one probe covers all 3 slices; 3 admit decisions, one per view) |
| 3 | essential_prose 5% | essential_web / essential_prose | same @ ce4eccc7…3113d | essential_web (`essential_web:essential_prose`) | en / prose slice | odc-by | live-certified 3 real rows | UNADMITTED | same as #1 |
| 4 | ultrax_ultrafineweb 20% | ultrax_ultrafineweb / UltraX-Ultra-FineWeb | openbmb/UltraX-Preview @ a8852758…4d8df | ultrax_ultrafineweb | en / refined web (`cleaned_content`) | apache-2.0 + derived-corpora caveat | LIVE-CERTIFIED 5/5 on 30 real probe rows (Track A closed) | UNADMITTED | `data probe --live` evidence into store (schema-probe receipt is NOT store evidence) + `data admit`; registry `live_verified` flag flip to true at admission |
| 5 | finepdfs_en 15% | finepdfs_edu / eng_Latn | HuggingFaceFW/finepdfs-edu @ 9cfabe21…ebde | finepdfs_en | en-routed / educational PDF (Docling only) | odc-by | authored fixtures ONLY | UNADMITTED | live adapter test on real rows + probe + admit |
| 6 | synth_en_explanations 15% | synth / default | PleIAs/SYNTH @ 0d6813a2…437 | synth_en | en / QA with context | cc-by-4.0 | live-certified 3 real rows | UNADMITTED | probe + admit (500-file inventory; selection recorded per plan) |
| 7 | nemotron_wiki_rewrite 8% | nemotron_specialized / Nemotron-Pretraining-Wiki-Rewrite | nvidia/Nemotron-Pretraining-Specialized-v1 @ 9ed3718b…4320f | wiki_rewrite | en / wiki rewrite | cc-by-4.0 (row licenses preserved) | live-certified 3 real rows | UNADMITTED | probe + admit |
| 8 | finewiki_en 5% | finewiki / en | HuggingFaceFW/finewiki @ 8bd13e72…1aeb9 | finewiki_en | en / article prose | cc-by-sa-4.0 | authored fixtures ONLY | UNADMITTED | live adapter test + probe + admit |
| 9 | ifm_behaviors_general_planning 5% | ifm_behaviors / general + planning (TWO views) | IFM/Pretrain-Behaviors @ 3345e13d…191f5 | ifm_general / ifm_planning | en (view-level) | apache-2.0 | authored fixtures ONLY | UNADMITTED | live adapter tests (both views) + 2 probes + 2 admits; internal general/planning split 50/50 ASSUMED, subject to audit |
| 10 | common_pile_prose 5% | common_pile / common_pile_prose | common-pile/comma_v0.1_training_dataset @ 5afc546d…151e7c | common_pile | en (treatment intent) | NULL — UNRESOLVED | live-certified 9 rows (3 of 31 components only) | UNADMITTED, license review BLOCKED | resolve per-component license FIRST, then probe + admit; component allowlist is a separate policy decision |
| 11 | simple_stories 2% | simple_stories / default | SimpleStories/SimpleStories @ e63b8adc…406b0628 | simple_stories | en / narratives | mit | live-certified 6 real rows | UNADMITTED | probe + admit |

Outstanding-admission command shape (operator fills `--notes`, reviews,
then chooses `--decision approve` + `--license-review approved` ONLY on
passing review; example for UltraX, repeat per view with that view's
tested adapter):

`uv run --locked --extra cpu xlm data admit --source ultrax_ultrafineweb --view UltraX-Ultra-FineWeb --adapter ultrax_ultrafineweb --notes "<operator review notes>" --decision approve --license-review approved --benchmark-risk clean`

Pre-conditions per source (in order): `data probe --live` (store evidence)
→ adapter live test on real rows → license/provenance + benchmark review →
`data admit`. Common Pile additionally requires license resolution before
anything else. Nothing here approves anything.

## 2. Storage plan (Phase B, re-measured 2026-09-27)

Free GiB: C 68.8, D 95.0, E 78.5, F 19.6, G 78.7 (≈340 total; C lost ~3
since last reading — re-measure before every wave, §10 step 2). Do NOT
start bulk acquisition unless free space comfortably exceeds twice the
planned wave transfer plus outputs. Raw bytes ≠ token payload: compressed
parquet transfer, decompressed scratch, canonical JSONL, and (later)
token shards multiply the footprint; §6 sizes transfer bytes from
calibration, never from token counts.

Separate roots (no drive letters in product code; `<DATA>` chosen AFTER
re-measuring, never assumed to be the code worktree drive):

- `<DATA>\hf-cache` — HF hub + datasets caches (large HF cache OFF C:)
- `<DATA>\acq-scratch\<source>` — per-source fetch scratch + journals
- `<DATA>\acq-raw\<source>` — immutable raw fetch artifacts
- `<DATA>\canonical\<source>` — adapted canonical documents
- `<DATA>\tokfit` — tokenizer-fit data (later task)
- `<DATA>\frozen` — tokenized/frozen artifacts (later task)

Windows note: without symlink privilege, huggingface_hub duplicates
cached blobs — budget 2× for hub-cached bytes; the bounded fetcher itself
streams via range requests into scratch/output (no hub cache), while
probe/sample stages using `datasets` DO use the HF cache.

One-line configuration (execution order in §10):

`$env:XLM_HOME="<DATA>\xlm-home"`
`$env:HF_HOME="<DATA>\hf-cache"`
`$env:HF_DATASETS_CACHE="<DATA>\hf-cache\datasets"`
`$env:HF_HUB_OFFLINE="1"; $env:HF_DATASETS_OFFLINE="1"; $env:TRANSFORMERS_OFFLINE="1"; $env:UV_OFFLINE="1"`
`$env:TEMP="<DATA>\temp"; $env:TMP="<DATA>\temp"` (only if C: is tight)

## 3. Calibration tranche (Phase C, bounded, production system only)

Per acquisition unit (12 units: 11 components, IFM as general + planning):
`sample-blocks` (~1,000 records over 1–2 inventory-ordered files,
`--seed 20260918`, pinned `--revision`) → `data plan` (pilot caps,
`--pilot-approved`, `--adapter-spec`, `--row-ranges`) → `fetch` →
`status` → `verify` → `adapt --on-reject record`. Footprint ≈ a few MB per
unit, tens of MB total — orders below the "few GiB" ceiling. Never
`wget`/`curl`; never hand-rolled downloads.

Metrics → exact source command:

- raw bytes/record, transferred total: `xlm data fetch` stdout + `xlm data status --plan <plan> --scratch-dir <scratch>`
- records acquired: same status (`records_acquired`)
- rejection rate + counts by code: `adaptation_summary.json` (`rejected_records`, `rejection_counts_by_code`)
- canonical bytes/record: sum `utf8_byte_count` over `documents.jsonl` (one-liner §10)
- usable density: canonical ÷ transferred
- wall/throughput: `xlm data performance --plan <plan> --scratch-dir <scratch>`
- disk amplification: (transferred + canonical + status temp/output bytes) ÷ canonical
- dedup/cleaning survival: recorded assumption 1.0 in calibration JSON (refined later, never silently changed)
- UltraX extras from `selected_records.jsonl` (pre-adapt, carries both fields): cleaned/raw length ratio, `processed_functions` distribution, mojibake scan (replacement-char / non-printable rates) — read-only one-liners §10
- No exact XLM token inference: tokenizer unfrozen; densities stay in bytes/chars.

## 4. Deterministic file inventories (Phase D)

Per multi-file source: operator lists exact file names at the pinned
revision from probe/file-browser review into `<source>_candidates.txt`
(reviewed, never invented; UltraX: 104 parquet files currently
discovered) with sizes where available → freeze:

`uv run --offline --locked --extra cpu python scripts/mix01_inventory.py freeze --source <id> --repo <repo> --revision <40-hex-SHA> --seed 20260918 --files <source>_candidates.txt --sizes <source>_sizes.json --output <DATA>\inventories\<id>.inventory.json`

Artifact: full ordered list, per-file `size_bytes` (or null),
`order_key`, `file_count`, `known_size_bytes`, `inventory_digest`
(SHA-256 over the canonical ordered lines). Refuses empties, duplicates,
unpinned revisions, unknown size entries. UltraX ordering reuses the
Track A construction `SHA-256(seed|repository|revision|file)`; never
first-N in provider order. Top-up = next files in the SAME frozen order
under a NEW plan (same seed/revision); spent plans are never edited.

## 5. Headroom sizing (Phase E, formulas now, numbers after calibration)

Quota/headroom table (exact from `recipes/mixtures/mix01_quotas_6b.yaml`;
byte columns are TBD until calibration — no fabricated precision):

| Component | Final exact-token quota | First-pass usable-token target | Required transferred bytes (low/base/high) | Initial files (base) |
|---|---:|---:|---|---|
| essential_science | 600,000,000 | 660,000,000 | TBD | TBD |
| essential_practical | 600,000,000 | 660,000,000 | TBD | TBD |
| essential_prose | 300,000,000 | 330,000,000 | TBD | TBD |
| ultrax_ultrafineweb | 1,200,000,000 | 1,320,000,000 | TBD | TBD |
| finepdfs_en | 900,000,000 | 990,000,000 | TBD | TBD |
| synth_en_explanations | 900,000,000 | 990,000,000 | TBD | TBD |
| nemotron_wiki_rewrite | 480,000,000 | 528,000,000 | TBD | TBD |
| finewiki_en | 300,000,000 | 330,000,000 | TBD | TBD |
| ifm_behaviors_general_planning | 300,000,000 | 330,000,000 (165M+165M assumed) | TBD | TBD |
| common_pile_prose | 300,000,000 | 330,000,000 | TBD | TBD |
| simple_stories | 120,000,000 | 132,000,000 | TBD | TBD |

Formulas (implemented in `mix01_inventory.py estimate`, rerunnable):

`required_transferred = target_usable_tokens × bytes_per_token ÷ (canonical_bytes ÷ transferred_bytes) ÷ extra_survival × safety`, evaluated at bytes/token low/base/high (defaults 3.0/4.0/5.0 — an explicit English-prose ASSUMPTION echoed in every output, not a measurement); `proposed_files = ceil(required_base ÷ avg_file_bytes)`. Calibration JSON schema (operator-assembled from §3 outputs): `{sources: {id: {records_sampled, accepted_records, rejected_records, transferred_bytes, canonical_bytes, avg_file_bytes?, cleaned_raw_ratio?, extra_survival?}}}`; missing source → `NEEDS_CALIBRATION` with formula only; zero acceptance → `BLOCKED` with reason; unknown source → refusal. Sufficiency later via `sufficiency --estimate … --acquired …` (SUFFICIENT/TOP_UP/UNKNOWN + deficit; top-up = new plan over next inventory files).

## 6. Production plan hashes (Phase F)

Per unit, after inventory + admission: `sample-blocks` (pinned revision,
seed, target from §5) → `data plan --mode selected_records --row-ranges …
--adapter-spec <spec> --seed 20260918 --attempt 1` with production limits
(`--max-bytes/--max-records/--max-output-disk` from the estimate, past
pilot caps) → display hash → STOP (no authorization here) → operator
records `data admit` → fetch (gate re-verifies evidence+decision bound to
the exact revision). Show-hash command (reads the CLI-written plan JSON
through the real loader):

`uv run --offline --locked --extra cpu python scripts/calibration_adopt.py plan-identity --plan <plan>.json`

Journal identity: `<scratch>\journals\<plan_id>.progress.json`, bound to
the behavioral hash; resume = re-run the IDENTICAL fetch; never mint a
replacement plan to replenish spent budgets (fresh `--attempt` only after
an expired deadline, old attempt preserved).

## 7. Parallelism (Phase G, from the fetcher IO model)

One fetch process holds a per-plan file lock, runs ≤2 internal file
workers (`max_workers` default 2), journals per-plan scratch, and charges
shared request/byte budgets. Safe design: at most **2 concurrent source
fetches** (≤4 in-flight file streams), each with its own scratch/output
roots; never two processes on one plan (lock refuses). Adapt/canonicalize
offline anytime, serially if CPU-contended. No 11-way fetch. Re-measure
free space before each wave; halt the wave if free < 2× planned transfer.

## 8. Disk estimates

No byte totals are claimed before calibration (§5 TBD). Budgetary rules
only: wave transfer + 2× headroom must fit the emptiest involved drive;
canonical ≈ transferred × measured yield; scratch peak ≈ transfer +
canonical during fetch; token shards come LATER (tokenizer task) and are
not sized here. Re-measure per §10 step 2 every wave.

## 9. Acquisition-plan requirements (per unit checklist)

Pinned revision = registry SHA; deterministic inventory + seed recorded;
row ranges from `sample-blocks --report`; adapter-spec resolving certified
columns (essential needs `--adapter-config` at adapt); explicit byte/record/
request/disk limits; plan JSON + hash shown; admission recorded before
fetch; journal identity known; top-up path declared (next inventory files,
new plan). IFM = two units; common_pile blocked on license.

## 10. Exact operator runbook (one-line PowerShell, in order)

`<DATA>` chosen after step 2. Network OFF except marked calls (`HF_*_OFFLINE=0`
for that call only, `UV_OFFLINE=1` always). `<FILES>`/`<N>`/`<BYTES>` come
from Phase D/E artifacts named in steps 9–11/21.

`Set-Location G:\Project\xlm-data-ultrax`
`git rev-parse HEAD`
`git branch --show-current`
`Get-PSDrive -PSProvider FileSystem | Select-Object Name,@{n='FreeGiB';e={[math]::Round($_.Free/1GB,1)}}`
`$env:XLM_HOME="<DATA>\xlm-home"`
`$env:HF_HOME="<DATA>\hf-cache"`
`$env:HF_DATASETS_CACHE="<DATA>\hf-cache\datasets"`
`$env:HF_HUB_OFFLINE="1"; $env:HF_DATASETS_OFFLINE="1"; $env:TRANSFORMERS_OFFLINE="1"; $env:UV_OFFLINE="1"`
`uv sync --offline --locked --extra cpu --extra eval`
`uv run --offline --locked --extra cpu xlm data sources --catalog manifests/datasets.catalog.yaml`
`uv run --offline --locked --extra cpu xlm data audit --catalog manifests/datasets.catalog.yaml`
`uv run --offline --locked --extra cpu xlm mixture preset-validate --preset recipes/mixtures/mix01.yaml --views recipes/mixtures/mix01_views.yaml`
`$env:HF_HUB_OFFLINE="0"; $env:HF_DATASETS_OFFLINE="0"`
`uv run --locked --extra cpu xlm data probe --catalog manifests/datasets.catalog.yaml --source ultrax_ultrafineweb --view UltraX-Ultra-FineWeb --live --budget-mib 16 --probe-id cal01 --json`
`uv run --locked --extra cpu xlm data sample-blocks --source ultrax_ultrafineweb --view UltraX-Ultra-FineWeb --revision a88527587389fd4ab352e9ad1273f4c0a234d8df --files <FILE_A,FILE_B> --seed 20260918 --mode rowgroup --target-records 1000 --output <DATA>\calib\ultrax_rows.json`
`$env:HF_HUB_OFFLINE="1"; $env:HF_DATASETS_OFFLINE="1"`
`uv run --offline --locked --extra cpu xlm data plan --source ultrax_ultrafineweb --view UltraX-Ultra-FineWeb --catalog manifests/datasets.catalog.yaml --files <FILE_A,FILE_B> --mode selected_records --row-ranges <DATA>\calib\ultrax_rows.json --adapter-spec ultrax_ultrafineweb --seed 20260918 --pilot-approved --output <DATA>\calib\ultrax_plan.json`
`$env:HF_HUB_OFFLINE="0"; $env:HF_DATASETS_OFFLINE="0"`
`uv run --locked --extra cpu xlm data fetch --plan <DATA>\calib\ultrax_plan.json --output-dir <DATA>\calib\ultrax\raw --scratch-dir <DATA>\calib\ultrax\scratch --pilot-approved`
`uv run --locked --extra cpu xlm data status --plan <DATA>\calib\ultrax_plan.json --scratch-dir <DATA>\calib\ultrax\scratch`
`uv run --locked --extra cpu xlm data fetch --plan <DATA>\calib\ultrax_plan.json --output-dir <DATA>\calib\ultrax\raw --scratch-dir <DATA>\calib\ultrax\scratch --pilot-approved`
`uv run --locked --extra cpu xlm data verify --plan <DATA>\calib\ultrax_plan.json --output-dir <DATA>\calib\ultrax\raw --scratch-dir <DATA>\calib\ultrax\scratch --json`
`$env:HF_HUB_OFFLINE="1"; $env:HF_DATASETS_OFFLINE="1"`
`uv run --offline --locked --extra cpu xlm data adapt --plan <DATA>\calib\ultrax_plan.json --adapter ultrax_ultrafineweb --input <DATA>\calib\ultrax\raw\selected_records.jsonl --output-dir <DATA>\calib\ultrax\canonical --on-reject record`
`Get-Content <DATA>\calib\ultrax\canonical\adaptation_summary.json`
`uv run --offline --locked --extra cpu python scripts/mix01_inventory.py canonical-bytes --input <DATA>\calib\ultrax\canonical\documents.jsonl`
`uv run --offline --locked --extra cpu xlm data performance --plan <DATA>\calib\ultrax_plan.json --scratch-dir <DATA>\calib\ultrax\scratch`
`uv run --offline --locked --extra cpu python scripts/mix01_inventory.py freeze --source ultrax_ultrafineweb --repo openbmb/UltraX-Preview --revision a88527587389fd4ab352e9ad1273f4c0a234d8df --seed 20260918 --files <DATA>\inventories\ultrax_candidates.txt --output <DATA>\inventories\ultrax.inventory.json`
`uv run --offline --locked --extra cpu python scripts/mix01_inventory.py estimate --quotas recipes/mixtures/mix01_quotas_6b.yaml --calibration <DATA>\calib\calibration.json --output <DATA>\calib\headroom_estimate.json`
`uv run --offline --locked --extra cpu xlm data plan --source ultrax_ultrafineweb --view UltraX-Ultra-FineWeb --catalog manifests/datasets.catalog.yaml --files <ORDERED_PREFIX_CSV> --mode selected_records --row-ranges <DATA>\inventories\ultrax_rows.json --adapter-spec ultrax_ultrafineweb --seed 20260918 --max-bytes <BYTES> --max-records <RECORDS> --max-output-disk <BYTES> --output <DATA>\plans\ultrax_prod.json`
`uv run --offline --locked --extra cpu python scripts/calibration_adopt.py plan-identity --plan <DATA>\plans\ultrax_prod.json`
`uv run --offline --locked --extra cpu xlm data admit --source ultrax_ultrafineweb --view UltraX-Ultra-FineWeb --adapter ultrax_ultrafineweb --notes "<operator review notes>" --decision approve --license-review approved --benchmark-risk clean`
`$env:HF_HUB_OFFLINE="0"; $env:HF_DATASETS_OFFLINE="0"`
`uv run --locked --extra cpu xlm data fetch --plan <DATA>\plans\ultrax_prod.json --output-dir <DATA>\acq-raw\ultrax --scratch-dir <DATA>\acq-scratch\ultrax`
`uv run --locked --extra cpu xlm data status --plan <DATA>\plans\ultrax_prod.json --scratch-dir <DATA>\acq-scratch\ultrax`
`uv run --locked --extra cpu xlm data fetch --plan <DATA>\plans\ultrax_prod.json --output-dir <DATA>\acq-raw\ultrax --scratch-dir <DATA>\acq-scratch\ultrax`
`uv run --locked --extra cpu xlm data verify --plan <DATA>\plans\ultrax_prod.json --output-dir <DATA>\acq-raw\ultrax --scratch-dir <DATA>\acq-scratch\ultrax --json`
`$env:HF_HUB_OFFLINE="1"; $env:HF_DATASETS_OFFLINE="1"`
`uv run --offline --locked --extra cpu xlm data adapt --plan <DATA>\plans\ultrax_prod.json --adapter ultrax_ultrafineweb --input <DATA>\acq-raw\ultrax\selected_records.jsonl --output-dir <DATA>\canonical\ultrax --on-reject record`
`Get-Content <DATA>\canonical\ultrax\adaptation_summary.json`
`uv run --offline --locked --extra cpu python scripts/mix01_inventory.py sufficiency --estimate <DATA>\calib\headroom_estimate.json --acquired <DATA>\acquired.json --output <DATA>\sufficiency.json`
`Get-Content <DATA>\sufficiency.json`

Repeat the calibration block per unit (essential ×3 with `--adapter-config`,
finepdfs/synth/wiki/finewiki/ifm-general/ifm-planning/common/stories with
their view/adapter-spec from §1; common_pile only after license
resolution), then production plan→STOP→admit→fetch→verify→adapt→sufficiency
per unit, top-up via next inventory files under new plans only.

UltraX calibration extras (read-only over the fetched calibration sample):

`uv run --offline --locked --extra cpu python -c "import json; r=[json.loads(l) for l in open(r'<DATA>\calib\ultrax\raw\selected_records.jsonl',encoding='utf-8') if l.strip()]; c=[len(x.get('cleaned_content') or '') for x in r]; w=[len(x.get('raw_content') or '') for x in r]; print('cleaned/raw ratio:', round(sum(c)/max(1,sum(w)),4), 'empty:', sum(1 for x in r if not (x.get('cleaned_content') or '').strip()))"`
`uv run --offline --locked --extra cpu python -c "import json,collections; r=[json.loads(l) for l in open(r'<DATA>\calib\ultrax\raw\selected_records.jsonl',encoding='utf-8') if l.strip()]; print(dict(collections.Counter(str(x.get('processed_functions')) for x in r)))"`
`uv run --offline --locked --extra cpu python -c "import json; r=[json.loads(l) for l in open(r'<DATA>\calib\ultrax\raw\selected_records.jsonl',encoding='utf-8') if l.strip()]; t=''.join(x.get('cleaned_content') or '' for x in r); print('mojibake check: U+FFFD:', t.count('�'), 'non-printable share:', round(sum(1 for ch in t if not (ch.isprintable() or ch in '\n\t'))/max(1,len(t)),6))"`

STOP POINTS (fetch forbidden past each): after step 11 (plan hashes shown,
no authorization given); after admit review per source; before any wave
failing the step-2 space gate. FORBIDDEN in this phase: tokenizer train,
`data tokenize`/`freeze` for shards, `mixture plan --budget-targets
6000000000`, `prepare --authorize`, `experiment plan/submit`, `train`,
`resume`, production fetch without recorded admission.

## 11. Tokenizer boundary (STOP)

Nothing in §10 trains/selects/freezes a tokenizer, tokenizes shards,
counts exact XLM tokens, or binds the 32M pilot. After sufficiency shows
enough canonical material, the separate later task freezes the
representative fit corpus → 32,768-tokenizer → identity → exact counting →
deficit top-up → final 6B freeze. Do not perform those steps now.

## 12. Verdict

**READY FOR CALIBRATION ACQUISITION.** Calibration tranches (bounded,
pilot-capped, production code path) may proceed per §10 steps 1–24 once
the operator confirms `<DATA>` placement against a fresh step-2 reading.
Bulk acquisition is NOT authorized: it additionally requires per-source
admission (§1), inventory digests (§4), estimate numbers (§5), shown plan
hashes with recorded authorization (§6/§10 STOPs), and the space gate (§8).
