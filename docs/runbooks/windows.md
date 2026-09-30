> **Essential-Web Batch-0 recovery (2026-09-30):** authoritative state is 31/32
> sealed (96.875%), with only `f00026` remaining and its source already retained.
> Do not run Prepare or download again. Review the
> [recovery report and exact authorization/resume command](../implementation/reports/ESSENTIAL-WEB-BATCH0-RECOVERY.md).
> `-Stage ResumeCheck` verifies hashes and dry-plans remaining work offline;
> `-Stage Resume -RecoveryAuthorize <reviewed recovery digest>` authorizes the
> amendment and refuses all download work. Existing seals and the original
> Batch-0 authorization stay intact. Earlier instructions below are historical.

> **Essential-Web existing probe resume (2026-09-30):** after the optional FDC
> renderer repair, run from the repository root:
> `powershell -NoProfile -ExecutionPolicy Bypass -File .\docs\implementation\evidence\ESSENTIAL-WEB-PRODUCTION-READINESS\resume-probe-adaptation.ps1`.
> This uses the existing verified 256-row raw probe and plan for local
> verification/adaptation/measurement. Do not rerun `future-probe.ps1`.
> Completed measurement is never overwritten; incompatible partial output is
> retained for review. [Evidence and limitations](../implementation/reports/ESSENTIAL-WEB-OPTIONAL-FDC-FIX.md).

> **Essential-Web fast transport (2026-09-30):** the range-reader campaign below is
> superseded and its driver refuses to fetch. After dot-sourcing
> `scripts/operator_storage.ps1` (which now also sets `XLM_SCRATCH_ROOT` from
> `scratch_root` in `recipes/operator/storage.json`), run the live benchmark first:
> `.\scripts\operator_essential_web_fast.ps1 -Stage Benchmark -Authorize <benchmark digest>`,
> then per batch `-Batch N -Stage Prepare` (offline) and
> `-Batch N -Stage Run -Authorize <digest>`. Digests, limits, resume and disk use are in the
> [fast bulk plan](../implementation/reports/ESSENTIAL-WEB-FAST-BULK-PLAN.md).

> **Essential-Web bulk acquisition (2026-09-30):** the calibration is sealed and
> the campaign is frozen. One batch at a time, from the repository root after
> dot-sourcing `scripts/operator_storage.ps1`:
> `.\scripts\operator_essential_web_bulk.ps1 -Batch 0 -Stage Prepare` (reads
> footers only), then
> `.\scripts\operator_essential_web_bulk.ps1 -Batch 0 -Stage Run -Authorize <digest>`.
> Stages, resume, status, stop and disk limits are in the
> [bulk acquisition plan](../implementation/reports/ESSENTIAL-WEB-BULK-ACQUISITION-PLAN.md).

# XLM runbook — Windows (PowerShell)

For the Mix-01 operator checkout, first dot-source `scripts/operator_storage.ps1`.
It reads `recipes/operator/storage.json` (operator data root `G:\XLM`) and derives
`XLM_HOME=G:\XLM\xlm-home`, cache and temporary paths. If the default PowerShell
execution policy disables scripts, start an operator shell with
`powershell -NoProfile -ExecutionPolicy Bypass`; this affects only that process.
The remaining-component calibration driver requires `XLM_DATA_ROOT` or an explicit
`-DataRoot` and resolves the checkout from its script location. Essential-Web uses
the frozen shared 16,384-row commands in the
[production-readiness report](../implementation/reports/ESSENTIAL-WEB-PRODUCTION-READINESS.md),
whose admission/probe blockers must be resolved before live execution. The legacy
1,000-row Essential driver path is disabled. Historical evidence paths describe
past runs and are not current operator defaults.

Use mutually exclusive `--extra cpu` or `--extra cuda`; add `--extra eval` for
local harness fixtures. Python is pinned to 3.12.13 in `.python-version`; use
`uv sync --locked` to preserve the tested dependency graph. `--offline` requires
an already populated uv package cache. CUDA requires a compatible NVIDIA driver;
CPU installs do not prove GPU behavior.

**P23 scope:** the Windows authored offline workflow and separate RTX 4090 smoke
tests were executed. Linux commands were reviewed but not executed here. Full
production campaigns are blocked; consult
[FINAL_ACCEPTANCE](../implementation/FINAL_ACCEPTANCE.md) for current limitations.
The snippets below describe command surfaces, not proof that every production
combination has run. No real corpus admission or protected final deployment was
verified in P23.

Direct `xlm train` now requires an existing token shard or an explicit bounded
`data.synthetic_tokens` fixture list; missing data fails even in dry-run. It is a
baseline smoke interface (under 5M parameters, at most 200,000 targets and 600 s).
`xlm resume` requires bound `runtime.json`, `execution.json`, and the original
verified code capture. See [frozen execution](../frozen-execution.md) for offline
runtime identity, fork rules, and limits. Legacy checkpoints without execution
evidence remain inspectable but cannot qualify for frozen continuation. Supply the matching
BPE tokenizer to evaluation/export; an explicit missing path never falls back to
bytes. Export bundles load their included tokenizer automatically.

Official remote `search`/`confirmation` task execution is BLOCKED until the loader
can enforce bounded explicit split membership. Authored local JSON harness tasks
remain available. Developer-side `final` execution is always refused; request-only
construction is separate from an authorized operator deployment. Multi-source
production YAML/plugin training is not yet connected through the complete CLI.

## 0. Conventions and disk locations

- Repository root in these examples: `D:\Project\xlm`. Adjust to your checkout.
- `XLM_HOME` roots all runtime data (raw, clean, shards, runs, ledger, eval,
  profiles). Isolate it per project:
  ```powershell
  $env:XLM_HOME = "D:\Project\xlm\data"
  ```
- Release and sealed material never lives under `XLM_HOME`. Sealed roots are
  operator-owned directories outside every agent mount (see §16).

## 1. Install

```powershell
# Base install (no torch): metadata, config, doctor, data-plane commands.
uv sync --locked
# CPU execution (training, eval, generation, profiling on CPU).
uv sync --locked --extra cpu
# CUDA execution (NVIDIA GPU). Mutually exclusive with --extra cpu.
uv sync --locked --extra cuda
# Evaluation harness (optional, composable with cpu or cuda).
uv sync --locked --extra cpu --extra eval
uv --version
```

## 2. Doctor

```powershell
uv run --locked --extra cpu xlm doctor
uv run --locked --extra cpu xlm doctor --json
```

`doctor` reports the torch wheel (CPU vs `cu126`), CUDA availability, device
capability, BF16 support, probed SDPA kernels and unsupported-configuration
explanations. It never installs drivers or toolkits.

## 3. Offline demo

```powershell
uv run --locked --extra cpu xlm demo
```

Bounded end-to-end slice (import → tokenizer → train → resume → evaluate →
generate) on synthetic data. No network, no admission, no authorization.

## 4. Config validate / resolve / diff

```powershell
uv run --locked xlm config validate recipes/experiments/baseline_50m.yaml
uv run --locked xlm config validate recipes/experiments/baseline_50m.yaml --mode executable
uv run --locked xlm config validate recipes/models/50m.yaml
uv run --locked xlm config validate recipes/mixtures/mix01.yaml
uv run --locked xlm config validate recipes/prepare/offline_toy.yaml
uv run --locked xlm config resolve recipes/experiments/baseline_50m.yaml --output resolved.yaml
uv run --locked xlm config diff recipes/experiments/baseline_50m.yaml recipes/experiments/baseline_150m.yaml
```

The second command fails on purpose: drafts carry null artifacts and must
never validate as executable.

## 5. Source probe and admission

```powershell
uv run --locked xlm data sources --catalog manifests/datasets.catalog.yaml
uv run --locked xlm data audit --catalog manifests/datasets.catalog.yaml
# Bounded metadata-only discovery (no data, no license acceptance):
uv run --locked xlm data probe --catalog manifests/datasets.catalog.yaml --source finewiki --live
```

Admission decisions are operator records (`xlm data admit --help`); candidates
stay unadmitted until then. FineWeb and FineWeb-Edu are denied always.

## 6. Small live pilot (bounded, operator-approved sources only)

```powershell
uv run --locked xlm data plan --source finewiki --files language_subsets.csv --pilot-approved --output data/scratch/plan.json
uv run --locked xlm data fetch --plan data/scratch/plan.json --output-dir data/raw/pilot --scratch-dir data/scratch/fetch
uv run --locked xlm data status --plan data/scratch/plan.json --scratch-dir data/scratch/fetch
uv run --locked xlm data verify --plan data/scratch/plan.json --output-dir data/raw/pilot
```

Pilot caps are explicit flags on `plan` (`--max-bytes`, `--max-records`,
`--max-output-disk`); `--pilot-approved` without operator approval is refused.

## 7. Prepare (staged data pipeline)

```powershell
# Dry plan first: shows ready/blocked/stale stages and invalidation chains.
uv run --locked xlm prepare --config recipes/prepare/offline_toy.yaml --plan-only
# Execute with explicit authorization; reruns reuse verified stages.
uv run --locked xlm prepare --config recipes/prepare/offline_toy.yaml --authorize
uv run --locked xlm prepare --config recipes/prepare/offline_toy.yaml --authorize --force
```

## 8. Profile (CUDA example; CPU works with --device cpu)

```powershell
uv run --locked --extra cuda xlm profile --config recipes/models/50m.yaml --device cuda --precision bf16_fp32_master --attention-backend sdpa --output-dir data/profiles/50m
```

Writes `profile.json` (measured tokens/sec, VRAM, checkpoint sizes) and
`profile_freeze.json` (frozen microbatch/accumulation/backend for matched runs).

## 9. Train and resume

```powershell
uv run --locked --extra cpu xlm train plan.json --device cpu --max-targets 3200
uv run --locked --extra cpu xlm resume <checkpoint-dir> --device cpu
uv run --locked --extra cpu xlm resume <checkpoint-dir> --fork --budget 6400 --device cpu
```

A non-fork resume whose budget outgrows the saved schedule horizon is refused;
extend budgets only with `--fork`. Precision, backend, compile and
activation-checkpointing flags are explicit (`--precision`, `--attention-backend`,
`--compile`, `--activation-checkpointing`).

## 10. Evaluate (search tier)

```powershell
uv run --locked --extra cpu --extra eval xlm evaluate <checkpoint-dir> --suite synthetic_mc --device cpu
uv run --locked --extra cpu --extra eval xlm evaluate <checkpoint-dir> --suite search --limit 10 --output-dir data/eval/smoke
```

Search/confirmation run the pinned harness on explicit variants. `--suite final`
without operator authorization exits 1; `--request-only` writes the frozen
request instead of running.

## 11. Compare and promote

```powershell
uv run --locked xlm compare --baseline base.json --candidate cand.json --plan-baseline plan_a.json --plan-candidate plan_b.json --track architecture --facts-baseline facts.json --facts-candidate facts.json --clusters clusters.json --n-bootstrap 1000 --output comparison.json
uv run --locked xlm promote --comparison comparison.json --plan plan_a.json --to-size 150m --output-draft promoted.json --output-decision decision.json
```

Ineligible tracks refuse before any winner is computed. Promotion writes drafts
only; it never launches jobs, grants authorization or schedules final suites.

## 12. Queue

```powershell
uv run --locked xlm experiment plan recipes/experiments/baseline_50m.yaml --output plan.json --snapshot-dir snaps/xx
uv run --locked xlm experiment authorize --plan-hash <hash> --max-targets 128000000 --approver op --ticket-id T-1 --output ticket.json
uv run --locked xlm experiment submit plan.json --snapshot-dir snaps/xx --device cpu --ticket ticket.json
uv run --locked xlm queue run --once
uv run --locked xlm queue status
uv run --locked xlm queue cancel <job-id>
uv run --locked xlm campaign plan recipes/campaigns/data_search.yaml
```

One GPU lease per device; stale holders are reclaimed with a record. Cancel is
safe at any point; crashed RUNNING jobs recover within their retry policy.

## 13. Report, runs and dashboard

```powershell
uv run --locked xlm report --run <job-id> --format md --output report.md
uv run --locked xlm report --campaign campaign.json --format html --output campaign.html
uv run --locked xlm report --data-inputs fragments.json --format json
uv run --locked xlm runs list
uv run --locked xlm dashboard --port 8765
```

The dashboard binds loopback only; any other bind needs `--deployment-config`
with declared authentication. Reports render missing values as `n/a`, never zero.

## 14. Export and generation

```powershell
uv run --locked --extra cpu xlm export <checkpoint-dir> --output-dir data/exports/xx --export-id xx
uv run --locked --extra cpu xlm generate <checkpoint-or-export-dir> --prompt "Hello" --max-new-tokens 32
uv run --locked --extra cpu xlm generate-session <checkpoint-or-export-dir> --prompts "Hi" --max-new-tokens 32 --output session.json
```

Exports hold safetensors weights plus hashes and provenance — never raw corpora,
credentials, run files, or optimizer state by default. Nothing is published
anywhere by these commands.

## 15. Research workflow

```powershell
uv run --locked xlm research idea new --id demo --title "Demo" --category objective --output idea.yaml
uv run --locked xlm research idea validate idea.yaml
uv run --locked xlm research scaffold --category optimizer --name demo_opt --output-dir scaffolds
uv run --locked xlm research check-plugin src/xlm/plugins/noop_objective
```

Follow `CHANGE_EXPERIMENT_TEMPLATE.md` for the idea→patch→smoke→matched
experiment→comparison chain.

## 16. Operator final evaluation and release

```powershell
uv run --locked xlm final request --checkpoint-hash <hash> --suite-fingerprint <fp> --task-variants xlm_arc_easy_final --max-items 100 --max-requests 1 --output final_request.json
# Operator side only (requires $env:XLM_OPERATOR_CREDENTIALS and a sealed root):
uv run --locked xlm final execute --request final_request.json --authorization auth.json --sealed-root <sealed> --scorer-bundle scorer.py --reviewed-bundles reviewed.json --output receipt.json
uv run --locked xlm final verify-receipt receipt.json --sealed-root <sealed>
uv run --locked xlm release audit <release-dir>
```

The development process cannot execute a protected request; receipts carry
aggregates only; unknown rights stay BLOCKED.

## 17. Cleanup by reachability (dry-run first)

```powershell
uv run --locked xlm maintenance
uv run --locked xlm maintenance --apply --max-bytes 1073741824
```

Default is a dry run listing scratch/tmp files under `XLM_HOME`. `--apply`
removes only those, bounded by `--max-bytes`; anything ambiguous is listed for
manual review, never deleted.

## 18. Safe cancellation

- Queue jobs: `xlm queue cancel <job-id>` (queued stops at once; running stops
  at the next optimizer boundary with a checkpoint).
- Training: Ctrl+C triggers cooperative interruption (checkpoint + INTERRUPTED).
- Prepare: Ctrl+C stops between stages; rerun resumes from the state file.

## 19. Common failures

| Symptom | Cause | Fix |
|---|---|---|
| `Executable ... rejects null` | Draft has unresolved artifacts | Resolve/admit artifacts first; drafts are not executable |
| `attention_backend 'profile_required'` | Unresolved backend | Pass `--attention-backend` or freeze an `xlm profile` result |
| `budget ... exceeds schedule horizon` on resume | Extended budget without fork | Re-run with `--fork` |
| `device 'cuda:0' is leased` | Another job holds the GPU | Wait, `queue status`, or cancel the holder |
| `plan carries approval blockers` on submit | Null artifacts / unmeasured cost | `experiment plan` lists them; resolve each |
| `smoke caps` on submit | No ticket for a large run | `experiment authorize` with matching hash + limits |
| `No silent fallback` on mixture errors | Missing/empty source | Fix the recipe; fallback is forbidden |
| `sealed mode not established` | Same-user or unattested root | Use a real separate account/machine + attestation |

## 20. Artifact portability

Exports (`model.safetensors`, `config.json`, `tokenizer/`, `export_manifest.json`)
are self-contained: verify with `xlm release audit`-style hash checks after
copying (every file hash is in the manifest). Reports are static files that stay
readable after moving with their directory.

## 21. What remains unverified

Live production datasets beyond bounded pilots; GPU sizes other than the
single RTX 4090 profiled; the two-identity sealed deployment; transformers-based
HF runtime parity; torch.compile parity (no codegen toolchain); full official
benchmark suites (only bounded smokes ran). See `docs/implementation/STATUS.md`.
