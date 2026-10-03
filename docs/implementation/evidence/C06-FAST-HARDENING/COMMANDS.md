# C06 FAST hardening: commands and evidence

2026-10-03, working directory `F:/Project/xlm-c06-tokenizer`. All runs used
`uv run --offline --locked --extra cpu --extra eval` with
`OMP/MKL/OPENBLAS/NUMEXPR_NUM_THREADS=1` and `TOKENIZERS_PARALLELISM=false` (the BPE
child overrides it). Authored and generated inputs only. Report:
[C06-FAST-HARDENING](../../reports/C06-FAST-HARDENING.md).

## Tests

```bash
python -m pytest tests/test_c06_fast_hardening.py tests/test_c06_fast.py -n 4 --dist=worksteal --max-worker-restart=0 --basetemp=C:/t8hc
# exit 0: 122 passed (75 + 47)
T="tests/test_c06_fast.py tests/test_c06_fast_hardening.py tests/test_c06_tokenizer_fit.py tests/test_c05_selection.py tests/test_c05_detached_volume.py tests/test_c05_control.py tests/test_c05_progress.py tests/test_c05_engine.py tests/test_tokenizers.py tests/test_tokenizer_fit_stream.py tests/test_tokenizer_regime.py tests/test_pool_freeze_regime.py tests/test_cli_pool_freeze.py tests/test_configurable_workflow.py tests/test_p35_science_workflow.py"
python -m pytest $T -m "not serial and not network and not cuda" -n 8 --dist=worksteal --max-worker-restart=0 --basetemp=C:/t9reg
# exit 0: 407 passed
python -m pytest $T -m "serial and not network and not cuda" -n 0 --basetemp=C:/t9ser
# exit 0: 3 passed, 411 deselected
```

## Astra's independent probes

The probes were materialized from `../C06-FAST-INDEPENDENT/*.py.txt` into `C:/t9aud`
without modification.

```bash
PYTHONPATH="F:/Project/xlm-c06-tokenizer/tests;C:/t9aud" python -m pytest C:/t9aud/test_independent.py -p conftest -n 0 -rA --basetemp=C:/t9fin1
# exit 1: 43 passed, 2 failed (API-coupled probes; see report) -> independent_unmodified.log
PYTHONPATH="F:/Project/xlm-c06-tokenizer/tests;C:/t9aud" python -m pytest C:/t9aud/test_independent_adapted.py -p conftest -n 0 -rA -s --basetemp=C:/t9fin2
# exit 0: 2 passed -> independent_adapted.log (source: test_independent_adapted.py.txt)
python C:/t9aud/baseline_run.py C:/t9base/c060/root C:/t9base/independent_fit0/policy.yaml C:/t9aud/baseline_fit
# exit 0: git-exported bb886bd (C:/t7c06audit/baseline) fit
PYTHONPATH=tests python -c "from pathlib import Path; from test_c06_fast import assert_equivalent; assert_equivalent(Path('C:/t9aud/baseline_fit/fit'), Path('C:/t9base/independent_fit0/fit'))"
# exit 0: identical scientific artifacts
```

## Static

```bash
ruff format --check <13 changed .py files>   # exit 0
ruff check <13 changed .py files>            # exit 0
mypy --strict <7 changed/new src modules>    # exit 0
git diff --check                             # exit 0
```

## Performance (bounded, authored, OS-cached files)

```bash
PYTHONPATH=. python -m scripts.c06_fast_scaling --root <scratch>/hscale --output scaling_supervised.json
PYTHONPATH=. python -m scripts.c06_bpe_benchmark --root <scratch>/hbpe --output bpe_authenticated_512mib.json --sizes 512 --threads 16 8
PYTHONPATH=. python -m scripts.c06_fast_benchmark --root <scratch>/hmain --output benchmark.json
python ab_supervision.py <scratch>/ab3     # -> ab_supervision.jsonl
```

All exited 0. The generated data was deleted afterwards. In `benchmark.json`, the
64 MiB BPE and `projection` fields come from the older harness model; the
authenticated 512 MiB figures are in `bpe_authenticated_512mib.json`.
