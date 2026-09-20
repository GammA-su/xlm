# P23 D04/D05 — preserved before-evidence

Captured 2026-09-20 from branch `fix/d04-d05` at baseline commit
`0a8ff6592b3a893d0bd70f99649fdfcdf7fde310`, in an isolated workspace
(`D:\Project\xlm-d0405`) with its own venv, `XLM_HOME` and work dirs. No
network, no official benchmark data. All inputs are authored synthetic.

| File | What it is |
|---|---|
| `author_fixtures.py` | Writes the authored benchmark-SHAPED tasks and the tiny CPU checkpoint |
| `repro_d04_d05.py` | Bounded adversarial reproduction against real production entry points |
| `repro_results.json` | Observed before-state, one record per case |
| `repro_run.log` | Full stdout/stderr of the reproduction run |
| `d05_cli_limit2.log` | Public `xlm evaluate` CLI attempt (frozen-worker route) |
| `probe_keys.py` / `.out` | Pinned-harness metric-key and sample-shape probe |

## Exact commands

```powershell
$env:UV_PROJECT_ENVIRONMENT = 'D:\Project\xlm-d0405\.venv-d0405'
$env:PYTHONPATH   = ''
$env:XLM_HOME     = 'D:\Project\xlm-d0405\.d0405\home'
$env:HF_HUB_OFFLINE = '1'; $env:HF_DATASETS_OFFLINE = '1'
uv sync --offline --locked --extra cpu --extra eval                      # exit 0
uv run --offline --locked --no-sync --extra cpu --extra eval `
    python docs/implementation/evidence/P23-D04-D05/before/author_fixtures.py .d0405/work/repro
uv run --offline --locked --no-sync --extra cpu --extra eval `
    xlm evaluate .d0405/work/repro/model_ckpt --suite search `
      --tasks arc_easy,hellaswag,piqa,blimp `
      --include-path .d0405/work/repro/tasks --limit 2 `
      --output-dir .d0405/work/repro/out-limit2                          # exit 1
.\.venv-d0405\Scripts\python.exe `
    docs/implementation/evidence/P23-D04-D05/before/repro_d04_d05.py .d0405/work/repro  # exit 0
```

Imports verified to resolve to this tree:
`xlm -> D:\Project\xlm-d0405\src\xlm\__init__.py`, `lm_eval 0.4.13`,
`torch 2.14.0+cpu`, CPython 3.12.13.

## Observed before-state

| Case | Observed |
|---|---|
| D04-1 | The ordinary `search` suite resolved from `manifests/eval_dataset_pins.yaml` is refused wholesale by `run_harness_suite` (`HarnessRunError`, "official/final evaluation is BLOCKED"), because every pin is resolved and carries a revision. There is no route to an explicitly selected, verified local development input. |
| D04-2 | `materialize_pinned_tasks` refuses any task whose installed YAML is not already `dataset_path: json`. The official `arc_easy` prompt/choice/metric definition therefore cannot be combined with a verified local split-scoped snapshot at all. Only hand-written fixture YAMLs run — i.e. approximate home-made task definitions. |
| D05-0 | **New defect.** The BLiMP branch of `run_harness_suite` reads `metrics.get("acc")`, but the pinned harness emits `"acc,none"`. Every BLiMP subdataset raises `HarnessRunError: task '…' lacks metric 'acc'`. BLiMP has never executed through this runner. |
| D05-1 | Four tasks present, `--limit 2` against 6 authored items per task: `index.complete = True`, **index = −12.5 published as the four-task index**. Coverage reported as `scored 2 / total_items 2`. |
| D05-2 | Declared BLiMP scope of two subdatasets, one executed: `index.complete = True`, index = −29.17, macro-averaged over a single subdataset. |
| D05-3 | `harness_runner` passes `total_items=len(items)`: the expected population is defined as the returned population by construction. |
| D05-4 | `ItemEvidence.item_id` is the harness positional `doc_id` (`"0"`, `"1"`), not the declared id carried in the doc (`syn_arc_0`, …). Membership can only be counted, never checked. |

The CLI log additionally shows the frozen-execution worker (D06) correctly
running from a code snapshot, and the harness warning that authored fixture
YAMLs named `arc_easy`/`hellaswag`/`piqa`/`blimp` override the installed tasks.
