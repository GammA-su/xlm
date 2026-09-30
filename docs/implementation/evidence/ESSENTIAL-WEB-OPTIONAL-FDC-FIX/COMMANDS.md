# Offline FDC repair commands — 2026-09-30

Run from `F:\Project\xlm-data-ultrax`, Windows / Python 3.12.13 / uv 0.12.19.
No dependencies or lock files changed. Shell setup:

```powershell
$U = @('run', '--offline', '--locked', '--no-sync', '--extra', 'cpu', '--extra', 'eval')
$env:HF_HUB_OFFLINE='1'; $env:HF_DATASETS_OFFLINE='1'
$env:OMP_NUM_THREADS='1'; $env:MKL_NUM_THREADS='1'
$env:OPENBLAS_NUM_THREADS='1'; $env:NUMEXPR_NUM_THREADS='1'
$env:TOKENIZERS_PARALLELISM='false'
$E = 'docs/implementation/evidence/ESSENTIAL-WEB-OPTIONAL-FDC-FIX'
```

The following were executed. Outputs are text-free except authored fixture values
in pytest failures. Captured outputs were normalized to UTF-8/LF afterward, with
trailing display whitespace removed; JSON values and test outcomes are unchanged.

```powershell
# Before the renderer edit: exit 0. Evidence file refuses replacement.
uv @U python "$E/replay.py" --raw G:/XLM/calib/essential-web-production/probe/probe-00/raw/selected_records.jsonl --output "$E/before.json"
# After the renderer edit: exit 0.
uv @U python "$E/replay.py" --raw G:/XLM/calib/essential-web-production/probe/probe-00/raw/selected_records.jsonl --output "$E/after.json"

# 14-file focused selection; exit 1: 372 passed, 2 Windows path-length failures.
uv @U python -m pytest tests/test_essential_web_production_selector.py tests/test_essential_web_readiness.py tests/test_mix01_views.py tests/test_data_adapters.py tests/test_adapt_rejections.py tests/test_calibration_adopt.py tests/test_acquisition_plan.py tests/test_acquisition_bounds.py tests/test_acquisition_verifier.py tests/test_acquisition_fetcher.py tests/test_essential_web_bootstrap.py tests/test_source_admission.py tests/test_exclusion_benchmark.py tests/test_exclusion_receipt.py -m 'not network and not serial' -n 16 --dist=worksteal --max-worker-restart=0 --basetemp=.bt-fdc -q
# Exact failing nodes with shorter paths, plus historical stored-row certification:
# exit 0, 11 passed (the 2 repaired-environment cases + 9 certification tests).
uv @U python -m pytest tests/test_acquisition_bounds.py::test_production_fetch_with_verified_admission tests/test_acquisition_bounds.py::test_public_plan_fetch_status_verify_prepare tests/test_essential_web_live_certification.py -n 0 --basetemp=.bf -q

# Formatting applied first: exit 0, three files formatted.
uv @U ruff format src/xlm/data/adapters/mix01_adapters.py tests/test_essential_web_production_selector.py "$E/replay.py"
# Each final check exit 0.
uv @U ruff check src/xlm/data/adapters/mix01_adapters.py tests/test_essential_web_production_selector.py "$E/replay.py"
uv @U ruff format --check src/xlm/data/adapters/mix01_adapters.py tests/test_essential_web_production_selector.py "$E/replay.py"
uv @U mypy --strict src/xlm/data/adapters/mix01_adapters.py tests/test_essential_web_production_selector.py "$E/replay.py"
```

Read-only verification used the real data CLI command with a Python network audit
guard. Exit 0; `verification.json` contains the full receipt. `--no-publish` avoids
creating/replacing any operator-store artifact. Receipt transfer counters describe
the previous acquisition, not new requests. Exact invocation:

```powershell
@'
import sys

def deny(event, args):
    if event in {'socket.connect', 'socket.getaddrinfo', 'socket.sendto'}:
        raise RuntimeError('offline verification denies network')

sys.addaudithook(deny)
from xlm.cli.data_cmd import app
app(['verify', '--plan', 'G:/XLM/calib/essential-web-production/probe/probe-00.plan.json', '--output-dir', 'G:/XLM/calib/essential-web-production/probe/probe-00/raw', '--scratch-dir', 'G:/XLM/calib/essential-web-production/probe/probe-00/scratch', '--no-publish', '--json'])
'@ | uv @U python -
```

PowerShell script parse check: exit 0, no errors (repeated after adding exact
plan/raw identity guards):

```powershell
$parseErrors = $null; $tokens = $null
[System.Management.Automation.Language.Parser]::ParseFile((Join-Path (Get-Location) 'docs/implementation/evidence/ESSENTIAL-WEB-PRODUCTION-READINESS/resume-probe-adaptation.ps1'), [ref]$tokens, [ref]$parseErrors) > $null
if ($parseErrors.Count -ne 0) { $parseErrors; exit 1 }
```

Tests are authored synthetic fixtures, except the explicitly separate stored-row
certification and 256-row replay. Acquisition tests exercise local loopback HTTP;
no external source requests were made. No new probe/fetch/calibration, full/fast
acceptance gate, CUDA, persistent GPU job or push. The resume script is prepared
and parsed, **not executed**; production `measurement.json` remains NOT RUN.

Existing `.bt-c04` and user edits/reviews were left alone. Automatic approval
review rejected the scratch cleanup command as blocked by policy; this task's
`.bf` and `.bt-fdc` remain untracked. No cleanup was executed. See the report for
measured replay memory/time and limitations.
