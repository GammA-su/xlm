# Remaining Mix-01 calibration runbook (operator-run, pilot-capped)

Worktree `G:\Project\xlm-data-ultrax` (code only) at Track A closeout;
all calibration data/cache/artifacts under `X:\XLM` (931 GiB free,
verified present). This runbook covers the 10 remaining acquisition
units. Common Pile is documented in §6 with NO fetch commands until its
license resolves. No production admission is granted, no bulk acquisition,
no tokenizer, no training, no pilot. The USER runs every network command;
the agent ran none.

Baseline (confirmed, not redesigned): UltraX calibration succeeded —
inventory digest `cb42e273…900634f`, file
`data/UltraX-Ultra-FineWeb/UltraX-Ultra-FineWeb-en-part-0039-of-0104.parquet`
rows `[90950,91950)`, 1000 records, transferred 3,326,258 B, selected
output 4,673,212 B, accepted 994 / rejected 6 (six `remove_all()` rows),
canonical 3,845,430 B, plan hash
`f13b93a5…ac02d`. Yield ≈ 1.156 canonical B per transferred B
(compressed parquet), acceptance 0.994. The workflow below repeats this
shape per unit at ~1000 records.

## 1. Per-unit parameters (from the actual repository)

`--seed` is 20260918 and `--target-records` 1000 for every unit;
`--mode rowgroup`, `--attempt 1`, pilot-default limits + `--pilot-approved`
(no production limits anywhere). File paths are repo-observed real paths
(live-cert tests + registry notes); the operator confirms each is still
listed at the pinned revision during `probe`, otherwise passes `-Files`
explicitly (never invented). One file per unit; backups named where known.

| Unit | source / view @ revision | Adapter (config) / spec | File (backup) | Live-cert prerequisite |
|---|---|---|---|---|
| essential_science | essential_web / essential_science @ ce4eccc7…3113d | essential_web (essential_science) / `essential_web:essential_science` | data/v1/train/00001.parquet (00002, 00003) | certified (shares raw fetch, §4) |
| essential_practical | essential_web / essential_practical @ same | essential_web (essential_practical) / `essential_web:essential_practical` | same file | certified (shares raw fetch, §4) |
| essential_prose | essential_web / essential_prose @ same | essential_web (essential_prose) / `essential_web:essential_prose` | same file | certified (shares raw fetch, §4) |
| synth_en_explanations | synth / default @ 0d6813a2…437 | synth_en / `synth_en` | synth_001.parquet (of 500) | certified |
| nemotron_wiki_rewrite | nemotron_specialized / Nemotron-Pretraining-Wiki-Rewrite @ 9ed3718b…4320f | wiki_rewrite / `wiki_rewrite` | Nemotron-Pretraining-Wiki-Rewrite/part_000003.parquet | certified |
| simple_stories | simple_stories / default @ e63b8adc…406b0628 | simple_stories / `simple_stories` | data/train-00003-of-00007.parquet | certified |
| finepdfs_en | finepdfs_edu / eng_Latn @ 9cfabe21…ebde | finepdfs_en / `finepdfs_en` | data/eng_Latn/train/000_00083.parquet (000_00084) | PROVISIONAL — authored fixtures only; calibration rows are live-test material, formal cert after operator review |
| finewiki_en | finewiki / en @ 8bd13e72…1aeb9 | finewiki_en / `finewiki_en` | data/enwiki/000_00013.parquet | PROVISIONAL — same as finepdfs |
| ifm_general | ifm_behaviors / general @ 3345e13d…191f5 | ifm_general / `ifm_general` | general/general_full.chunk0-bdbff8a5c6-00315.parquet | PROVISIONAL — same as finepdfs |
| ifm_planning | ifm_behaviors / planning @ same | ifm_planning / `ifm_planning` | planning/planning.chunk0-160f3594ed-00416.parquet | PROVISIONAL — same as finepdfs |

Discovery mechanism for every unit: `xlm data probe --live` (counts +
fingerprint at the pinned revision) → operator confirms the proven path
above in the Hub file listing at that SHA → `sample-blocks` footer
discovery (bounded range reads, `--revision` pinned) derives the exact
`[start,stop)` row ranges. No blind first-N selection.

## 2. Driver script (preferred, fail-closed)

`scripts/operator_calibrate_remaining.ps1` runs one unit end-to-end
(Probe → SampleBlocks → Plan (+hash display) → Fetch → Status → Verify →
Adapt → Summary → Record), echoing each full command, stopping on any
non-zero exit, network ON only inside the three bounded live calls,
`--offline --locked --no-sync` throughout, one root per unit
(`X:\XLM\calib\<unit>\{scratch,raw,canonical}`, plan/rows/evidence/logs
alongside), pilot caps only, never `admit`/production/tokenizer/train.
Native execution is exit-code-only (`Invoke-NativeCapture`: file-redirected
streams, so harmless native stderr can never abort a run; §7b).

Restart-safe adoption (`scripts/calibration_adopt.py`, offline; exit 0 =
run, 2 = reuse, 1 = fail closed) is checked before every mutating stage,
reusing only existing repository semantics: probe evidence loads via
`load_probe_evidence` and must match source/view/revision/repository,
real-observed type and discovery outcome (same-payload republication would
conflict, so compatible evidence prints "existing compatible probe
evidence reused" and the live call is skipped); row ranges and plans must
match parameters byte-for-byte with recomputed hashes (plan reruns are
otherwise same-hash no-ops by CLI design); adapted outputs must bind the
current plan hash (adapt refuses overwrite by CLI design); verified
publications must verify and bind current outputs (re-verify runs with
`--no-publish` instead of republishing, since receipts embed fresh
timestamps); a COMPLETED fetch journal bound to this plan and unit roots
whose outputs still hash to their journaled digests is adopted WITHOUT
calling fetch (absent/unfinished journals run fetch, which resumes
natively). Anything incompatible, corrupt, or incomplete (including only
one of rows/report or of documents/summary) fails closed — never deleted,
overwritten, or bypassed with a fresh identity. Record first runs
`mix01_inventory.py measure`, which derives every value from the artifacts
(plan → COMPLETED journal → adaptation summary → canonical documents,
cross-checked by plan id/hash, source, revision, document digest/count and
per-document byte counts) into `<unit>\record_inputs.json`, then
`record --measurement <that file> --adopt` (identical re-records are
no-ops, including the convergent essential triple — keep `-Files`
identical across its three runs; divergent ones fail).

Machine-value contract: native stdout/stderr are human-only (displayed
and logged, never returned or parsed). Machine values travel only through
helper exit codes and deterministic JSON result files (§7b follow-up 3).

`powershell -NoProfile -ExecutionPolicy Bypass -File G:\Project\xlm-data-ultrax\scripts\operator_calibrate_remaining.ps1 -Unit simple_stories -Stage All`
`powershell -NoProfile -ExecutionPolicy Bypass -File G:\Project\xlm-data-ultrax\scripts\operator_calibrate_remaining.ps1 -Unit finepdfs_en -Stage All`
`powershell -NoProfile -ExecutionPolicy Bypass -File G:\Project\xlm-data-ultrax\scripts\operator_calibrate_remaining.ps1 -Unit finewiki_en -Stage All`
`powershell -NoProfile -ExecutionPolicy Bypass -File G:\Project\xlm-data-ultrax\scripts\operator_calibrate_remaining.ps1 -Unit synth_en_explanations -Stage All`
`powershell -NoProfile -ExecutionPolicy Bypass -File G:\Project\xlm-data-ultrax\scripts\operator_calibrate_remaining.ps1 -Unit nemotron_wiki_rewrite -Stage All`
`powershell -NoProfile -ExecutionPolicy Bypass -File G:\Project\xlm-data-ultrax\scripts\operator_calibrate_remaining.ps1 -Unit essential_science -Stage All`
`powershell -NoProfile -ExecutionPolicy Bypass -File G:\Project\xlm-data-ultrax\scripts\operator_calibrate_remaining.ps1 -Unit essential_practical -Stage All`
`powershell -NoProfile -ExecutionPolicy Bypass -File G:\Project\xlm-data-ultrax\scripts\operator_calibrate_remaining.ps1 -Unit essential_prose -Stage All`
`powershell -NoProfile -ExecutionPolicy Bypass -File G:\Project\xlm-data-ultrax\scripts\operator_calibrate_remaining.ps1 -Unit ifm_general -Stage All`
`powershell -NoProfile -ExecutionPolicy Bypass -File G:\Project\xlm-data-ultrax\scripts\operator_calibrate_remaining.ps1 -Unit ifm_planning -Stage All`

Resume/debug per stage (same plan → journal resume; never a fresh plan to
replenish budgets):

`powershell -NoProfile -ExecutionPolicy Bypass -File G:\Project\xlm-data-ultrax\scripts\operator_calibrate_remaining.ps1 -Unit <unit> -Stage Fetch`

`-Files "a.parquet,b.parquet"` overrides the proven default when probe
review requires it. `-DataRoot` overrides `X:\XLM` (re-measure first).

## 3. Worked xlm-command expansion (simple_stories; others substitute §1 tokens)

`Set-Location G:\Project\xlm-data-ultrax`
`$env:XLM_HOME="X:\XLM\xlm-home"; $env:HF_HOME="X:\XLM\hf-cache"; $env:HF_DATASETS_CACHE="X:\XLM\hf-cache\datasets"`
`$env:HF_HUB_OFFLINE="1"; $env:HF_DATASETS_OFFLINE="1"; $env:TRANSFORMERS_OFFLINE="1"; $env:UV_OFFLINE="1"`
`uv sync --offline --locked --extra cpu --extra eval`
`$env:HF_HUB_OFFLINE="0"; $env:HF_DATASETS_OFFLINE="0"`
`uv run --offline --locked --no-sync --extra cpu --extra eval xlm data probe --catalog manifests/datasets.catalog.yaml --source simple_stories --view default --live --budget-mib 16 --probe-id cal01 --json`
`uv run --offline --locked --no-sync --extra cpu --extra eval xlm data sample-blocks --source simple_stories --view default --revision e63b8adc3b1a1bdc7cac5b500d150b71346b0628 --files data/train-00003-of-00007.parquet --seed 20260918 --mode rowgroup --target-records 1000 --output X:\XLM\calib\simple_stories\rows.json --report X:\XLM\calib\simple_stories\rows.evidence.json`
`$env:HF_HUB_OFFLINE="1"; $env:HF_DATASETS_OFFLINE="1"`
`uv run --offline --locked --no-sync --extra cpu --extra eval xlm data plan --source simple_stories --view default --catalog manifests/datasets.catalog.yaml --files data/train-00003-of-00007.parquet --mode selected_records --row-ranges X:\XLM\calib\simple_stories\rows.json --adapter-spec simple_stories --seed 20260918 --attempt 1 --pilot-approved --output X:\XLM\calib\simple_stories\plan.json`
`uv run --offline --locked --no-sync --extra cpu --extra eval python scripts/calibration_adopt.py plan-identity --plan X:\XLM\calib\simple_stories\plan.json`
`$env:HF_HUB_OFFLINE="0"; $env:HF_DATASETS_OFFLINE="0"`
`uv run --offline --locked --no-sync --extra cpu --extra eval xlm data fetch --plan X:\XLM\calib\simple_stories\plan.json --output-dir X:\XLM\calib\simple_stories\raw --scratch-dir X:\XLM\calib\simple_stories\scratch --pilot-approved`
`uv run --offline --locked --no-sync --extra cpu --extra eval xlm data status --plan X:\XLM\calib\simple_stories\plan.json --scratch-dir X:\XLM\calib\simple_stories\scratch`
`uv run --offline --locked --no-sync --extra cpu --extra eval xlm data fetch --plan X:\XLM\calib\simple_stories\plan.json --output-dir X:\XLM\calib\simple_stories\raw --scratch-dir X:\XLM\calib\simple_stories\scratch --pilot-approved`
`uv run --offline --locked --no-sync --extra cpu --extra eval xlm data verify --plan X:\XLM\calib\simple_stories\plan.json --output-dir X:\XLM\calib\simple_stories\raw --scratch-dir X:\XLM\calib\simple_stories\scratch --json`
`$env:HF_HUB_OFFLINE="1"; $env:HF_DATASETS_OFFLINE="1"`
`uv run --offline --locked --no-sync --extra cpu --extra eval xlm data adapt --plan X:\XLM\calib\simple_stories\plan.json --adapter simple_stories --input X:\XLM\calib\simple_stories\raw\selected_records.jsonl --output-dir X:\XLM\calib\simple_stories\canonical --on-reject record`
`Get-Content X:\XLM\calib\simple_stories\canonical\adaptation_summary.json`
`uv run --offline --locked --no-sync --extra cpu --extra eval python scripts/mix01_inventory.py measure --plan X:\XLM\calib\simple_stories\plan.json --scratch-dir X:\XLM\calib\simple_stories\scratch --canonical-dir X:\XLM\calib\simple_stories\canonical --source simple_stories --view default --revision e63b8adc3b1a1bdc7cac5b500d150b71346b0628 --output X:\XLM\calib\simple_stories\record_inputs.json`
`uv run --offline --locked --no-sync --extra cpu --extra eval python scripts/mix01_inventory.py record --calibration X:\XLM\calib\calibration.json --source simple_stories --measurement X:\XLM\calib\simple_stories\record_inputs.json --adopt`

(The explicit `--records-sampled/--accepted/--rejected/--transferred-bytes
/--canonical-bytes` form still exists for reference entries such as §5;
never type those numbers from console output.)

Essential adapts add `--adapter-config <slice>`; all other units adapt
without it. STOPs: after each plan hash (no authorization given here),
after each admit review (admit commands are production-gated and NOT part
of calibration), before any wave failing the space re-measure.

## 4. Shared-fetch and combine notes (no semantic change)

- Essential: the three slices stamp identical raw rows (slice selection is
  adapt-time metadata, no taxonomy filtering in the adapter), so one fetch
  may feed three adapts; the driver runs independent per-unit chains
  (~MB-scale duplication) so each chain mirrors its production per-view
  plan. The Record stage writes the same measured numbers under all three
  slice keys (documented provenance, not triple transfer).
- IFM: general/planning record under view-qualified keys, then combine
  (sums; survival must agree; avg_file_bytes must be passed explicitly):

`uv run --offline --locked --no-sync --extra cpu --extra eval python scripts/mix01_inventory.py record --calibration X:\XLM\calib\calibration.json --source ifm_behaviors_general_planning --records-sampled 1 --accepted 1 --rejected 0 --transferred-bytes 1 --canonical-bytes 1 --combine-sources ifm_general,ifm_planning`

(counts are placeholders overridden by the sum; `--replace` only for an
explicit re-record). Then:

`uv run --offline --locked --no-sync --extra cpu --extra eval python scripts/mix01_inventory.py estimate --quotas recipes/mixtures/mix01_quotas_6b.yaml --calibration X:\XLM\calib\calibration.json --output X:\XLM\calib\headroom_estimate.json`

## 5. UltraX calibration.json reference entry (confirmed baseline)

`uv run --offline --locked --no-sync --extra cpu --extra eval python scripts/mix01_inventory.py record --calibration X:\XLM\calib\calibration.json --source ultrax_ultrafineweb --records-sampled 1000 --accepted 994 --rejected 6 --transferred-bytes 3326258 --canonical-bytes 3845430`

## 6. Common Pile — NO FETCH until license resolution (separate)

Component `common_pile_prose` (source `common_pile`,
`common-pile/comma_v0.1_training_dataset` @ 5afc546d…151e7c): published
license metadata is NULL/unresolved and the 31-component allowlist is an
open policy decision, so license review is BLOCKED and no calibration
fetch is authorized. Required order: (1) resolve per-component license +
allowlist as a policy decision (recorded, not in this runbook); (2) live
adapter test on real rows of admitted components; (3) `data probe --live`;
(4) `data admit`; only then (5) the §2-style calibration chain. No
commands for steps 2–5 are given until step 1 lands.

## 7. Forbidden in this phase

Production-scale fetch or limits, `data admit` approvals, tokenizer
train/select/freeze, `data tokenize`/`freeze`, `mixture plan
--budget-targets 6000000000`, `prepare --authorize`, `experiment
plan/submit`, `train`, `resume`, pushes. Calibration fetch always carries
`--pilot-approved` under default pilot caps.

## 7b. Driver wrapper repair (canary follow-up, no semantic change)

The first `simple_stories` canary aborted inside the DRIVER after the probe
had already persisted valid evidence: `& uv ... 2>&1` merges native stderr
into the PowerShell stream, and under `$ErrorActionPreference='Stop'` the
mere presence of informational stderr raised `NativeCommandError` despite
exit code 0. `Invoke-NativeCapture` now redirects stdout/stderr to files
via `Start-Process` (same construction as `operator_pilot.ps1`) and the
driver fails SOLELY on nonzero exit codes; `$ErrorActionPreference` stays
`Stop` for cmdlets. All direct `& uv` calls (steps, python helpers, Env
sync, record) route through it; each step keeps a combined `.log` plus
`.stdout.txt`/`.stderr.txt` sidecars. Regression:
`tests/test_operator_driver.py` + `tests/files/calibrate_driver_native.ps1`
(exit0+stderr→pass, exit0+both→pass, nonzero→fail-stop, real `xlm --help`
and bad-command paths); the old pattern was verified to throw
`NativeCommandError` on the same stub. The interrupted probe's evidence
(`X:\XLM\xlm-home\probe_evidence\probe_simple_stories_default`) is valid
(correct source/view/revision `e63b8adc…`, `real_observed`, `_COMPLETED`)
and is REUSED, not deleted; `X:\XLM\calib\simple_stories\` holds only logs.
Follow-up: rerunning the probe against that evidence hit the (correct)
store conflict, so every mutating stage now adopts instead of republishing
(see §2 adoption paragraph and `scripts/calibration_adopt.py` with its
8-case regression suite `tests/test_calibration_adopt.py`). Rerunning
`-Unit simple_stories -Stage All` reuses the probe evidence and runs only
the stages with no compatible output yet.

Follow-up 2 (argv preservation): the repaired run then failed displaying
the plan identity — `Start-Process -ArgumentList <array>` does not quote
elements, so the `python -c "..."` payload split and Python saw only
`from`. Fixed two ways: (a) argv is now joined into ONE pre-quoted command
line (`ConvertTo-NativeArgument`, CommandLineToArgvW rules — spaces,
semicolons, quotes, backslashes, Unicode and spaced paths verified by
round-trip); (b) `python -c` is ELIMINATED from the driver entirely — plan
identity via `calibration_adopt.py plan-identity` (which also fixes a
latent `load_acquisition_plan(str)` vs `Path` defect) and canonical bytes
via `mix01_inventory.py canonical-bytes`. The existing SimpleStories plan
(`plan_simple_stories_default_huggingface_4a55bdec…`, hash
`a2d45d5d…9624f`) validates through the new path unchanged and is adopted
on rerun, which then proceeds to the unexecuted Fetch.

Follow-up 3 (machine output must not depend on human output): the next
SimpleStories run completed fetch (COMPLETED, 2,424,514 B, 1000 records),
verify (published `raw_simple_stories_default_4a55bdec…`) and adapt
(1000/0), then aborted in Record with `could not parse canonical byte
count` although `canonical-bytes` visibly printed `1269186`. Root cause,
proven under Windows PowerShell 5.1.26100 with a synthetic command
printing exactly that integer (and byte-identical to the real
`canonical-bytes-…stdout.txt`, `31 32 36 39 31 38 36 0D 0A`): Python's
text-mode stdout on Windows writes `\r\n`; the driver split on `` `n ``
only, leaving `"1269186\r"`, and .NET's `$` (no Multiline) matches only at
end-of-string or before a final `\n`, never before `\r`, so `'^\d+$'`
rejected every line and the filter yielded `$null`. The helper and the
canonical data were correct. Rather than patch one parse site, every
subprocess-stdout consumer was audited and removed:

| Site | Class | Before | Now |
|---|---|---|---|
| Invoke-Step / Invoke-Uv return value | — | returned raw stdout | return nothing (human-only; logged) |
| Invoke-Adopt | MACHINE (exit code) | exit code | unchanged |
| plan-identity | HUMAN | displayed, discarded | displayed only (identity is in the measurement) |
| Record: `data status --json` | MACHINE | `Substring(IndexOf("{"))` + ConvertFrom-Json; no COMPLETED check; crashed on a missing journal | removed; `measure` reads the journal file, requires COMPLETED + plan binding |
| Record: adaptation summary | MACHINE | ConvertFrom-Json of the file, unchecked | `measure` validates plan id/hash/source/revision/counts |
| Record: `canonical-bytes` | MACHINE | last `^\d+$` line of stdout (the bug) | `measure` sums strictly (count must equal UTF-8 length of `text`; digest/count must match the summary) |
| Record: `record` values | MACHINE | five numbers interpolated from the above | `record --measurement <file>` |
| Fetch rerun | — | re-ran fetch (journal IN_PROGRESS→COMPLETED, perf sidecar overwritten, network flag on) | `calibration_adopt.py fetch` adopts a bound COMPLETED journal; no fetch call |
| Probe/SampleBlocks/Plan/Verify/Adapt/Status/Summary/Env stdout | HUMAN | discarded/displayed | unchanged |

Further defects found by the audit and fixed: `Invoke-NativeCapture`
rejected empty argv items (a Mandatory `[string[]]` refuses `""` at bind
time; now `[AllowEmptyString()]`) and now fails on a missing exit code;
`calibration_adopt.py` ran (and would silently overwrite) when only one of
rows/report existed, reused adapted outputs without checking the documents
digest, and reused a publication while the raw dir held unbound extra
files — all now refuse; record-mode `data adapt` leaked an empty
`adaptation_summary.json.<uuid>.tmp` beside every published triple
(`StagedAdaptation.finish` opened a writer for the summary and then staged
the summary in a second temp). The existing orphan
`X:\XLM\calib\simple_stories\canonical\adaptation_summary.json.644f936de856436a971fa7e1523db724.tmp`
(0 bytes) is harmless and was left untouched; the operator may delete it.
Argv quoting (`ConvertTo-NativeArgument`) was reviewed and kept: .NET
Framework (PS 5.1) has no `ProcessStartInfo.ArgumentList`, and the MSVC
rules are implemented correctly; a 23-item digest round trip (empty, `"`,
`'`, `;`, trailing/double-trailing backslash, backslash-before-quote,
spaced paths, Unicode/emoji, newline, tab, `--key=value`, a Python code
payload) passes byte-exact through PowerShell → Start-Process → uv →
Python.

Read-only real-state audit (no mutation; before/after snapshots equal):
plan `plan_simple_stories_default_huggingface_4a55bdec3254c7738c30`, hash
`a2d45d5d220a2d18209abdd047cd99c91e9fea766b0ebaa8529bbe2cc8a9624f`,
revision `e63b8adc3b1a1bdc7cac5b500d150b71346b0628`, journal COMPLETED,
transferred 2,424,514 B, 1000 records, adaptation 1000 accepted / 0
rejected, canonical 1,269,186 B over 1000 documents (digest `a54d9b3d…`).
All six adoption checks (probe, sample-blocks, plan, fetch, verify, adapt)
return REUSE. `measure` against the real artifacts (output to a temp file)
and `record --measurement` into a temporary COPY of `calibration.json`
produced exactly: records_sampled 1000, accepted 1000, rejected 0,
transferred 2424514, canonical 1269186, extra_survival 1.0, with the
UltraX entry unchanged; a second record was an identical no-op.

Next `-Unit simple_stories -Stage All`: Env (offline `uv sync`) runs;
Probe, SampleBlocks, Plan (+identity display), Fetch, Verify (re-confirm
with `--no-publish`), Adapt are REUSED; Status and Summary display; Record
executes `measure` (writes `record_inputs.json`) and `record` (adds
`simple_stories` to `calibration.json`). No download, no republication,
no canonical overwrite, no plan/rows/probe regeneration.

Requirement ledger (follow-up 3):

| Requirement | Status |
|---|---|
| Root cause reproduced + explained (PS 5.1, synthetic) | VERIFIED |
| No driver machine value from stdout | IMPLEMENTED, VERIFIED (static audit + stage harness) |
| Record values derived from artifacts, cross-checked | IMPLEMENTED, VERIFIED (synthetic + real read-only) |
| Completed fetch adopted without a fetch call | IMPLEMENTED, VERIFIED (synthetic + real read-only decision) |
| Synthetic Stage All (all stages but Env) + restart no-op | VERIFIED (`tests/test_operator_driver_stages.py`) |
| Fail-closed mutations (13 driver-level, 16 measure-level) | VERIFIED |
| Argv round trip incl. empty/newline | VERIFIED (after the AllowEmptyString fix) |
| Env stage inside the synthetic harness | NOT RUN (would mutate the shared venv; covered by operator runs) |
| Real Record into `X:\XLM\calib\calibration.json` | NOT RUN (operator action) |
| Delete the stray real 0-byte tmp | OUT OF SCOPE (no real-data mutation) |

## 8. Verdict

**READY FOR REMAINING CALIBRATION.** Ten units executable via §2 (or §3
expansion); Common Pile excluded pending license; IFM combined before
estimate (§4); headroom numbers via `estimate` only after calibration
lands. Bulk acquisition remains unauthorized.
