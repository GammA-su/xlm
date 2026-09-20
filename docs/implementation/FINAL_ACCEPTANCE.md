# P23 independent final acceptance — 2026-09-19

**Audit completed; production acceptance BLOCKED.** The tested offline components
and bounded CUDA execution are usable within the limits below. Passing fixture
tests does not establish live corpus compatibility, safe execution of a frozen
research campaign, or protected final deployment. This report supersedes broader
readiness interpretations of the historical P00–P22 reports.

The audit read START_HERE, AGENTS_TEMPLATE, AGENTS, ARCHITECTURE, CONTRACTS,
EVALUATION_POLICY, ACCEPTANCE_MATRIX, STATUS, and every P00–P22 report. Existing
work was preserved, including pre-existing untracked implementation files and the
modified recipes README. No campaign, upload, publication, final benchmark access,
or contract amendment was performed. Python/dependency pins were retained.

## D01 remediation addendum — 2026-09-19

Stage 1 D01 is **IMPLEMENTED / VERIFIED for bounded offline declared-request
consistency**: 99 focused checks and 92 caller checks passed; format/lint/types
passed. See [reports/P23-D01.md](reports/P23-D01.md) for the complete defect/code/
test/result map, preserved failures, exact commands and compatibility limits.
Schema-v1 originals remain checksum-only. Authentic executed provenance was
deferred to D06 at this Stage 1 boundary; see the Stage 2 addendum below.
Overall production acceptance remains **BLOCKED**.
The final complete offline rerun remains scheduled after all approved stages.
The historical audit evidence below is preserved as historical evidence.

## D06 remediation addendum — 2026-09-20

Stage 2 D06 is **IMPLEMENTED / VERIFIED for bounded Windows CPU offline
execution**. The existing queue/direct paths launch captured code in fresh
processes with verified execution envelopes and actual installed runtime identity.
Checkpoint provenance, separate evaluator provenance, compatible continuation,
crash recovery and operational guards have maintained adversarial coverage.
All 262 focused tests and format/lint/type checks passed. Before evidence and
intermediate failures remain preserved; see [P23-D06](reports/P23-D06.md).
Legacy originals retain unresolved execution provenance. CUDA/Linux/operator
validation is NOT RUN for this stage. Other defects and platform acceptance
remain BLOCKED. At the Stage 2 boundary, D03's [plan and reproduction](evidence/P23-D03/PLAN.md)
awaited separate approval. The subsequently approved core work is recorded below.

## D03 core remediation addendum — 2026-09-20

D03 core is **IMPLEMENTED / VERIFIED for the bounded Windows CPU baseline pilot**:
406 passing test executions / 400 distinct cases, zero skips, and passing
format/lint/type checks. Product and executed-test source integrity is verified;
the documented unrelated test-only changes were rerun separately.
The current [code/test/result map](reports/P23-D03.md) and [operator runbook](D03_BASELINE_PILOT.md)
distinguish core training, full-size planning limits, deferred cross-tokenizer
comparison, and unverified external operations. D03 cross-tokenizer matched
document/byte execution is **OPEN / DEFERRED**, not VERIFIED. D02 and later repairs
remain unimplemented and require separate approval. Platform acceptance remains
BLOCKED; the complete offline platform rerun is still scheduled after all stages.

## Actual verification

The environment is Windows 11 build 26200, AMD64, uv 0.11.6, Python 3.12.13.
CPU/evaluation checks use torch 2.14.0+cpu and lm-eval 0.4.13. A separate CUDA
environment uses torch 2.14.0+cu126 on an NVIDIA RTX 4090 (23.99 GiB, sm_89).
CPU and CUDA extras are mutually exclusive; evaluation is optional. All install
and execution commands use uv. No dependency version was changed for a test.

All local evidence is beneath `data/audit/p23/`, ignored by Git. Durable command,
resource, source-hash, and result summaries are also in `evidence/P23/` beside this
report. Exact argv, exit status, environment, elapsed time, and sampled process-tree
RSS are in `verified-evidence/results.json`. RSS is sampled at 200 ms and sums
process RSS (shared pages may be counted more than once); it is not GPU VRAM or
a proven worst-case memory bound.
The full test command used 589.063 s wall time and a sampled peak 996,024,320 bytes RSS; the demo used 6.375 s and 371,548,160 bytes. Selected retained logical sizes are 1,082,427,467 bytes for the final CPU environment, 4,490,611,308 bytes for the CUDA environment, 62,592,102 bytes for final test evidence/fixtures, and 26,227,192 bytes for the CUDA profile directory. These count shared/hardlinked files and are not physical allocated space or complete job disk consumption; see `evidence/P23/resources.json` and the P23 report for exclusions.

| Check | Actual result | Evidence |
|---|---|---|
| Original copied-tree prerequisite suite | 783 passed, 1 skipped, 12 deselected; exit 0, 508.79 s | `evidence/baseline.log`, `baseline.xml` |
| First new prepare regressions against original code | 5 failed; exit 1 | `evidence/regressions_before.log` |
| Prepare fixes and regression suite | 19 passed; exit 0 | `evidence/prepare_after.log` |
| Early runtime regression suite | 27 passed; exit 0 | `evidence/runtime_after.log` |
| Broad targeted iteration | 133 passed, 1 failed; exit 1; real shard manifest serialization then fixed | `evidence/targeted.log` |
| Strengthened prepare/train/resume/evaluate/export/reload workflow | 1 passed; exit 0, 36.07 s | `evidence/workflow_after.log` |
| Fresh offline CPU + eval installation | exit 0, 48.015 s; new environment in copied repository using the existing uv cache | `final-evidence/sync.log`, `results.json` |
| First clean audit iteration | collection failed, exit 2: audit help enumerator used the wrong Typer group type; fixed to actual `TyperGroup` | `final-evidence/tests.log` |
| Final full offline suite and quality checks | 884 passed, 1 skipped, 12 deselected; pytest exit 0 (586.04 s). Final root lint/format, mypy (187 source files), doctor, demo and wheel build exit 0 | `verified-evidence/` |
| Real CUDA tests | 9 passed, 1 skipped, 13 deselected; exit 0, 74.80 s | `evidence/cuda.log`, `cuda.xml` |
| Real CUDA tiny profile | exit 0; 2,179,392 unique parameters, ~2,723 targets/s, ~0.10 GiB peak reserved | `cuda-profile/profile.json` |
| FineWiki metadata acquisition, initial/recheck | exit 0 each; 21,702 payload bytes each, zero corpus records | `live/result.json`, `live/recheck_result.json` |

The full-run orchestrator exited 1 because its copied help test had a formatting-only mismatch. The delivered root has that formatting fixed: separate final lint/format checks both exited 0. `inspection.json` confirms identical Python ASTs; product source matches the tested copy byte-for-byte. No test failure was hidden. The CPU compile skip reports missing `cl`; the CUDA compile skip reports missing Triton.

The original and final suites exclude `network`, `cuda`, and `operator` markers.
Authored operator-service unit tests still run offline; these do not establish OS
separation. The CUDA compile test is **SKIPPED**, not passed: the installed wheel
has no usable Triton toolchain. CUDA profiling used two measured tiny dry steps,
BF16 with FP32 master weights, SDPA, microbatch 1, accumulation 2, and a 120-second
worker limit. It is a diagnostic, not sustained training throughput. P14's older
50M/150M/300M measurements remain historical evidence; those sizes were not rerun
in P23. No production-loader throughput or billion-token cost has been measured here.

The clean install is specifically **cache-backed offline installation**, not
download-free installation from an empty dependency cache. The separate copied
repository has no original runtime data. HF offline flags, uv offline mode,
single-thread CPU settings, an isolated XLM_HOME, and a 30-minute per-command
timeout are recorded by `scripts/audit_p23.py`. It does not launch external tests.

## Fixed defects and regression evidence

| Defect observed | Implementation changed | Regression evidence |
|---|---|---|
| Prepare considered existing/corrupt output reusable; ignored directory input changes and copy policy; failed check-only stages reported success | `prepare/{config,planner,runner,integrity}.py`: content identities, full stage policy/code/lock identity, duplicate-key rejection, forward invalidation, failure propagation, streamed local copy | `test_final_acceptance.py` prepare cases; `test_prepare.py` |
| Missing training inputs silently selected synthetic data; dry-run did not validate inputs | `training/inputs.py`, `cli/train_cmd.py`: explicit verified shard or explicitly authored bounded token list; unsupported data fields refused; direct smoke budgets bounded | missing-data/dry-run regressions; strengthened `test_offline_workflow.py` |
| Resume replaced real input/objective/optimizer settings with defaults | checksummed checkpoint `runtime.json`; reconstruct input identity, objective, optimizer, schedule, precision, and execution settings | interrupted CLI run/resume is bitwise equal to uninterrupted 33-target run, including label smoothing and nondefault Adam betas; real-shard 257-target continuation |
| Checksum-verified optimizer/RNG files could execute pickle code | `training/checkpoint.py`: restricted `weights_only=True` deserialization | `test_checkpoint.py` hostile RNG payload with recomputed hashes is refused |
| General inference bypassed checkpoint/export integrity; explicit missing tokenizer fell back to bytes | `models/serialization.py`, `tokenizers/loading.py`, eval/export/generation commands: hash-first loading, device placement, explicit tokenizer resolution | export corruption regression; missing-tokenizer regression; CLI export/reload parity |
| Eval identity used checkpoint directory name | hash actual weight bytes in native and harness paths | CLI evaluation suite fixtures and request tests |
| Official harness task copies could use default final splits and uncontrolled remote loading | remote/default tasks fail closed before model/data load or evidence-cache reuse; only explicit local JSON fixtures supported; developer CLI final execution refused | `test_final_acceptance.py` remote-task refusal; `test_eval_suites.py`, `test_harness_adapter.py`, `test_operator.py` |
| Between-seed spread paired argument order | pair spread inputs by recorded `(init_seed, data_seed)`; reject absent, duplicate, or mismatched pairs; primary pair selection remains D08 | seed-pair adversarial regressions; `test_comparison.py` |
| Queue hardcoded factories/optimizer settings and ignored initialization seed; cancellation/time exits lacked checkpoints | registered factories, complete optimizer config, explicit seed/input, wall-time guard, interruption checkpoints, exact-target completion check | `test_queue.py` and full offline suite; immutable execution still unresolved below |
| Acquisition ignored row selections; request journal stayed at zero; disk reservation occurred after publication | selected-record modes fail explicitly; primary request attempts reserved persistently; reserve final capacity before publication | `test_final_acceptance.py` request restart budget; acquisition fixture suites; live recheck reports one primary attempt |
| Shard verification materialized entire binary/index into memory | streaming digests and file-size checks in `data/tokens.py` | shard corruption/mmap/stream suites |
| Broad `artifacts/` ignore also hid Python artifact implementation | anchored `/artifacts/`; audit workspace ignored explicitly | wheel contents inspected; artifact modules now included in lint/format checks |

Module paths above are relative to `src/xlm/`; test paths are relative to `tests/`.
The doctor output also retains a stale `evaluation_harness` label (`prompt 15 pending`); installed local harness behavior is established by the tests, and official remote execution remains blocked. Doctor capability labels are not production acceptance.

The new runner and help enumerator are audit code; their intermediate failures are
retained rather than removed from the evidence history.

## Requirement → file → test → result

`VERIFIED` below always means the stated scope. `FAILED` means an inspected
implementation contradicts the requirement, even when existing tests pass.
`BLOCKED` means a required dependency, adapter, or admission is unavailable.
`IMPLEMENTED` is not verification; `NOT RUN` is not a pass.

| ID | Implementation (`src/xlm/`) | Tests (`tests/`) | Actual audit result |
|---|---|---|---|
| A01 | `cli/`, `core/paths.py`; root lock/config | `test_cli.py`, `test_cli_inventory.py`, `test_doctor.py`, `test_imports.py` | VERIFIED: cached offline CPU+eval install, lazy-import fixtures, 87 registered help surfaces; base-only fresh install not independently rerun |
| A02 | `config/`, `prepare/config.py` | `test_config.py`, `test_recipes.py`, `test_final_acceptance.py` | VERIFIED: typed composition, duplicate/unknown keys, cycles, overrides and draft gates; execution coupling limited below |
| A03 | `artifacts/`, artifact CLI | `test_artifact_identity.py`, `test_artifacts.py`, `test_ledger.py` and caller regressions | VERIFIED D01 declared-request consistency on Windows offline; [stage evidence](reports/P23-D01.md). Legacy checksum-only; authentic execution separately verified in D06/A28. |
| A04 | `data/`, `tokenizers/` | `test_data_adapters.py`, `test_tokenizers.py`, `test_tokenizer_regime.py` | VERIFIED authored UTF-8 local fixtures, normalization/provenance and train-only fitting |
| A05 | `models/` | `test_models.py`, `test_attention_and_masks.py` | VERIFIED exact preset counts, tied deployment parameters, causal/mask behavior on CPU fixtures |
| A06 | `objectives/`, `training/trainer.py` | `test_objectives.py`, `test_trainer.py` | VERIFIED uneven-mask global target normalization |
| A07 | `optimizers/`, `schedules/` | `test_optimizers.py`, `test_schedules.py`, `test_continuation.py` | VERIFIED unique groups and real state continuation |
| A08 | `training/`, `data/sampling/` | `test_trainer.py`, `test_mixture_stream.py`, `test_final_acceptance.py` | VERIFIED exact nonmultiple target counters on fixture streams; CLI 33 and 257 target cases |
| A09 | `training/checkpoint.py`, `cli/train_cmd.py` | `test_checkpoint.py`, `test_continuation_p05.py`, `test_trainer_mixture.py`, `test_final_acceptance.py` | VERIFIED restricted loading and deterministic fixture continuation; legacy CLI checkpoints without runtime identity BLOCKED |
| A10 | `evaluation/likelihood.py` | `test_likelihood.py`, `test_evaluation_fixtures.py` | VERIFIED Unicode/boundary/whitespace/long-target and byte denominator fixtures |
| A11 | `cli/demo_cmd.py`, prepare/train/eval/export | `test_cli_train_demo.py`, `test_offline_workflow.py` | VERIFIED bounded local vertical slices; prepared shard really consumed and export reloaded |
| A12 | `data/sources/` | `test_source_discovery.py`, `test_source_admission.py`, `test_data_catalog.py` | IMPLEMENTED catalog/discovery; authored schema fixtures VERIFIED, all twenty live schemas NOT RUN |
| A13 | `data/sources/policy.py`, views | `test_mix01_views.py`, `test_source_admission.py` | VERIFIED denied direct FineWeb and absent-source blocking, no fallback |
| A14 | `data/acquisition/`, `data/sources/transport.py` | acquisition plan/fetcher/verifier tests, request-budget regression | FAILED complete cumulative bounds: redirects, restarted byte reservations, cache integrity, and corpus record caps remain incomplete (D02) |
| A15 | `data/sources/`, acquisition | live plan/receipt and discovery artifacts | BLOCKED corpus adapter pilot; VERIFIED only pinned live metadata transfer; zero admitted rows |
| A16 | `data/cleaning/` | `test_cleaning_*.py` | VERIFIED authored cleaning/idempotence/math/code/HTML/rejection fixtures |
| A17 | `data/dedup/` | `test_dedup_engine.py`, `test_dedup_lineage.py` | VERIFIED bounded synthetic exact/near clusters, late joins and lineage |
| A18 | `data/` pool/splits | `test_pool_splits.py`, `test_pool_freeze_regime.py` | VERIFIED group disjointness and tokenizer leak gates on authored data |
| A19 | `data/exclusion/`, `operator/final.py` | `test_exclusion_benchmark.py`, `test_exclusion_receipt.py`, `test_operator.py` | VERIFIED development/receipt fixtures; actual protected-service deployment NOT RUN |
| A20 | `data/pools/`, `tokenizers/` | `test_pool_freeze_regime.py`, `test_tokenizer_regime.py`, `test_final_acceptance.py` | VERIFIED fixture freeze/reuse/invalidation; production tokenizer fit NOT RUN |
| A21 | `data/tokens.py` | `test_token_shards.py`, `test_mixture_stream.py` | VERIFIED dtype/offsets/byte spans/checksum/mmap fixtures; streaming verifier implemented |
| A22 | `data/sampling/`, `training/inputs.py`, shared construction | mixture and configurable training/resolution/public workflow tests | VERIFIED D03 core: executed two-source YAML, exact token quotas, scheduling caps, real isolation/target traces and continuation. Cross-tokenizer matched document/byte execution OPEN / DEFERRED. |
| A23 | `data/pools/views.py`, `data/sources/` | `test_mix01_views.py` | VERIFIED M0–M5 and explicit no-IFM definitions/gates; real mixture BLOCKED on source admission |
| A24 | `models/backends.py`, `training/trainer.py` | `test_cuda.py`, `test_cuda_execution.py` | VERIFIED 9 real GPU tests; compile test SKIPPED |
| A25 | `training/profile.py`, `training/profile_worker.py` | real tiny CUDA profile | VERIFIED tiny diagnostic only; per-size production reprofile NOT RUN; historical P14 separate |
| A26 | `evaluation/harness*.py` | `test_harness_adapter.py`, `test_final_acceptance.py` | VERIFIED installed harness/native parity on local authored JSON; official remote/reference evaluation BLOCKED (D04) |
| A27 | `evaluation/suites.py`, harness runner, operator | `test_eval_suites.py`, `test_operator.py` | FAILED complete partial-score claim: limited runs can compute a complete index from only returned samples (D05); protected developer execution now refused |
| A28 | existing snapshot/plan/queue, frozen bootstrap/worker, checkpoint and evaluation context | `test_frozen_execution.py`, `test_frozen_recovery.py`, queue/plan/checkpoint/CLI regressions | VERIFIED D06 bounded Windows CPU scope; [code/test/result map](reports/P23-D06.md). External platform/operator validation NOT RUN. |
| A29 | experiment plans/campaign/queue | queue, plans, configurable workflow/resolution tests | VERIFIED public C13 smoke planning, production refusals and frozen execution; production BLOCKED by missing public measured-profile lookup integration, D02 and unmeasured external scale |
| A30 | `comparison/tracks.py` | `test_comparison.py` | VERIFIED authored allowed-difference/refusal cases; real causal comparisons NOT RUN |
| A31 | `comparison/bootstrap.py` | `test_comparison.py`, `test_final_acceptance.py` | FAILED broader statistical claim (D08): spread pairs are fixed but primary pair selection and BLiMP cluster resampling remain incorrect; real multi-seed analysis NOT RUN |
| A32 | `comparison/{promotion,recipes}.py` | `test_comparison.py` | VERIFIED draft promotion/ablation gates; no trials launched |
| A33 | `training/components.py`, existing registries/plugins/direct/queue/resume | research and configurable public workflow/resolution tests | VERIFIED shared typed construction, supported baseline and meaningful authored architecture/objective/optimizer/tokenizer invocation and complete continuation; unsupported combinations refused, not exhaustive support |
| A34 | `reports/`, report CLI | `test_reports.py`, `test_dashboard.py`, `test_offline_workflow.py` | VERIFIED authored report escaping/missing-data and real fixture-run report; browser visual review NOT RUN |
| A35 | `models/serialization.py`, export/generation | `test_export.py`, `test_generation.py`, workflow | VERIFIED native restricted reload/integrity and fresh-process fixture parity; HF runtime parity NOT RUN; tied storage duplication remains D07 |
| A36 | `operator/` | `test_operator.py` | NOT RUN actual independent identities/OS isolation; fixture refusal logic VERIFIED |
| A37 | `operator/release.py` | `test_operator.py` | VERIFIED synthetic rights/provenance refusal logic; real release BLOCKED; no rights certification |
| A38 | runbooks, prepare recipe | `test_offline_workflow.py`, `test_recipes.py`, CLI inventory | VERIFIED tested Windows fixture path; Linux execution NOT RUN; full production chain BLOCKED |
| A39 | this report, CLI trace, audit runner | independent regressions, full rerun, external evidence | VERIFIED audit delivery and evidence separation; does not turn FAILED requirements into passes |

Every registered command is traced to its callback and relevant test scope in
[CLI_AUDIT.md](CLI_AUDIT.md). Help-only command coverage is labeled there.

## Exact coupling and supported combinations

The authored offline recipe executes local import, cleaning, deduplication,
grouped splitting, tokenizer fitting, shard writing, and mixture-plan generation.
P23 changed the integration test to train that actual prepared shard with its
actual BPE vocabulary. It resumes an intermediate checkpoint to 257 targets,
compares weights with uninterrupted continuation, performs native likelihood,
exports and reloads in a fresh process, and renders a report. A separate CLI
regression cooperatively interrupts at 16 committed targets and resumes to 33.
This tests the interruption boundary, not an external power-loss event.

The integration test also computes a paired bootstrap from actual predictions
on authored multiple-choice items, using the same checkpoint as both arms: its
zero difference is a no-op diagnostic. Its CLI four-task comparison substep uses
explicitly authored comparison-format fixtures, **not** benchmark observations
from that training run. No four-task benchmark score is claimed.

D03 now resolves the declared mixture, source shards, tokenizer, token exposure
quotas and packing into actual direct/queued/resumed inputs. Public authored
two-source runs consume 25 alpha and 8 zeta targets under a 33-target budget;
actual document/offset/byte traces and committed cursors agree. Caps end genuine
source visits and alter scheduling without artificial EOS or document boundaries.
Tests prove isolation and exact final suffix restoration. Matched-document/byte
execution across tokenizers remains **OPEN / DEFERRED**.

The shared typed construction path supports tested baseline transformer, CE,
AdamW, constant/warmup-cosine and byte/fitted-BPE combinations. Meaningful authored
architecture, trainable stochastic auxiliary, optimizer and tokenizer fixtures
execute through public direct/queue/fresh-process resume, including captured
plugin A after live plugin B changes. Exact model/objective/optimizer/schedule,
Python/NumPy/Torch RNG and committed data-state equality is required. This is
not exhaustive support for arbitrary plugin combinations; incompatible capabilities
fail explicitly. Default exhaustion is an error; repetition must be declared.

The public `experiment plan --smoke` uses the existing C13 queue. Direct/queued
smoke limits remain under five million unique parameters, at most 200,000 targets
and 600 training seconds; production guards are retained. Full-size public
planning still lacks measured profile lookup integration and external evidence.
50M/150M/300M checks inspected meta shapes only and proved smoke refusal. See
[D03's operator pilot prerequisites and exact commands](D03_BASELINE_PILOT.md).
Legacy checkpoints lacking authentic reconstruction metadata remain refused.

CPU eager/SDPA fixture combinations and the separately measured CUDA BF16/FP16
tests are supported within their tested scopes. CUDA compilation, HF export
runtime parity, arbitrary external plugins, production mixing, final deployment,
and full official benchmarks are not covered by a successful production test.

## Live evidence and authorization

The user explicitly authorized a previously reviewed bounded source pilot
(16 MiB maximum transferred, 100 corpus records maximum) and local CUDA smoke
tests. No other network/data access was authorized or performed by this audit.
The discovery/fetch implementations restrict hosts, request timeouts and payload
sizes; their incomplete production accounting is disclosed as D02.

* SimpleStories discovery: immutable revision
  `e63b8adc3b1a1bdc7cac5b500d150b71346b0628`, 2 requests, 3,736 response bytes.
  Its corpus parquet is 431,432,698 bytes. No whole-file download was attempted;
  selected-row extraction is unsupported. Corpus pilot: BLOCKED.
* FineWiki discovery: immutable revision
  `8bd13e72e6a002407649b3e898535f42ceb1aeb9`, 2 requests, 46,692 response bytes.
  `language_subsets.csv` was fetched twice, each 21,702 bytes, with SHA-256
  `d4392c4e4d4d2e2c4341a5db8de7403f51aaa9ca5a2c457b2f1b12fd03f54fc4`.
  This is metadata, not a language-model corpus. The initial request counter was
  incorrectly zero; the fixed recheck records one primary attempt. Redirects are
  still not included in that metric. No independent provider checksum was available.

Total observed response/file payload across these operations: **93,832 bytes**,
zero corpus records, zero source admissions. HTTP headers/redirect bodies are not
included in the payload metric. Host policy was the existing transport allowlist;
the selected endpoint was `huggingface.co` with pinned repository revisions.
The second fetch was limited to 1 MiB, 3 primary attempts, one retry, one worker,
15-second request timeout, 60-second overall deadline and 2 MiB each scratch/final
capacity. No corpus-level live compatibility claim follows from these transfers.

Official benchmark/reference-checkpoint runs and real protected evaluation are
**NOT RUN**. Developer requests cannot execute finals. No sealed examples were
read, cached, or used for decisions in P23. No research result was produced.

## Open defects and production gates

| ID | Finding and consequence | Required next action |
|---|---|---|
| D01 | Historical defect: matching ID/config reused without comparing new payload/provenance; incomplete destinations were replaced. | IMPLEMENTED / VERIFIED bounded offline in [P23-D01](reports/P23-D01.md): staged bytes, separate production/content identities, explicit conflicts, portable paths, legacy checksum-only compatibility. D06 execution evidence is separately scoped below. |
| D02 | Fetch redirects are unmetered; capacity reservations restart from zero while journals retain cumulative bytes; cache reuse checks size without independent content validation; `max_records` is not a corpus-processing gate. Prepare subprocess capture and aggregate job budgets also lack a complete memory/disk proof. | Shared persistent accounting across redirects/retries/cache/partial/final storage; bounded record extraction; failure-path tests before production admission. |
| D03 | Core mixture/components/caps/target accounting/continuation IMPLEMENTED / VERIFIED for the bounded Windows CPU baseline pilot; [code/test/result map](reports/P23-D03.md). Cross-tokenizer matched-document/byte execution remains OPEN / DEFERRED. | Operator pilot follows [actual CLI runbook](D03_BASELINE_PILOT.md) after prerequisite input review. Complete deferred tokenizer comparison in its separately approved research phase; do not infer full platform or production-size readiness. |
| D04 | The original harness wrapper pinned a revision/name while inheriting upstream default split/loading behavior. The guarded path now refuses remote tasks. | Implement bounded explicit split/ID-scoped acquisition, enforce grouped membership before loading/evaluation, then authorize separate official development checks. Preserve frozen scoring/tier rules. |
| D05 | Harness runner notes a `limit` but computes its index from task evidence built only from returned samples. Missing official population coverage is not necessarily represented in denominators. | Regress all-four-task limited runs; withhold full-suite index and preserve explicit partial coverage without changing score definitions. |
| D06 | Historical defect: live imported execution despite a snapshot label; insufficient plan/job binding and placeholder checkpoint provenance. | IMPLEMENTED / VERIFIED in [P23-D06](reports/P23-D06.md): captured fresh workers, recomputed envelopes, actual environment identity, authentic checkpoint/evaluator context and compatible recovery. 262 focused offline checks passed. CUDA/Linux/production validation NOT RUN; no persistent campaign authorized. |
| D07 | Export safetensors layout clones tied tensors for storage; fresh-process numerical parity does not establish the C08 single-copy storage requirement. | Store/reconstruct aliases explicitly and test unique deployed vs stored parameters without changing architecture counts. |
| D08 | Multi-seed spread uses recorded pairs after this patch, but the headline within-run interval and initial eligibility still use the first supplied files before pairing. BLiMP bootstrap groups repeated cluster draws back under their original ID, collapsing draw multiplicity in macro averaging. Existing tests do not expose these cases. | Pair before selecting the primary interval and validate each pair; preserve cluster-draw multiplicity; add analytically checkable bootstrap and shuffled-CLI regression fixtures without changing the frozen estimand. |

Other NOT RUN limits: Linux execution; actual process-separated protected service;
production tokenizer/source fit; production CPU RSS/loader throughput; full external
reference parity; research training/ablation campaigns. Frozen research/evaluation
contracts remain unchanged. Fixing a guard or documenting a blocker is not a
contract amendment.

## Reproduce and continue

From a separate copy containing source, tests, scripts, fixtures, recipes,
manifests, prompts, docs, root Markdown and the three environment files:

```powershell
uv sync --offline --locked --extra cpu --extra eval
uv run --offline --locked --extra cpu --extra eval python scripts/audit_p23.py --repo D:\Project\xlm\data\audit\p23\final-repo --evidence D:\Project\xlm\data\audit\p23\new-evidence
```

Use a new evidence directory: pytest owns its `--basetemp` subtree. This offline
runner never performs the separate live/CUDA/operator checks. The exact individual
commands and environment are in `evidence/P23/results.json`; external commands
and results are in [reports/P23.md](reports/P23.md).

There is no Prompt 24 in this sequence. D01 and D06 remediation are implemented
and verified within their documented offline scopes. D03 core is implemented and
offline verified in [P23-D03](reports/P23-D03.md). D03 matched
tokenizer comparison remains deferred. D02 is the next separate approval gate.
Later stages each require their own
focused plan, reproduction and approval. Preserve frozen contracts and keep all
real acquisition, official evaluations, full-size profiles and research campaigns
for operator execution. The complete offline acceptance rerun is scheduled after
the separately approved remediation stages, not at this D06 boundary.
