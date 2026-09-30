# Exact commands and results — calibration seal

2026-09-30; checkout `F:\Project\xlm-data-ultrax`, branch `data/mix01-ultrax-6b`.
Starting HEAD `b2e13840b92483a7483c157cf5dec536d2d44c8d`.

Environment: Windows 11 Pro 10.0.26200, Windows PowerShell 5.1.26100; Python
**3.12.13**, uv **0.12.19**, PyArrow **25.0.1**, pydantic **2.13.5**, torch
**2.14.0+cpu**. Existing locked CPU/eval environment, offline, no sync.
`pyproject.toml`, `uv.lock` and `.python-version` are unchanged. No network, no
fetch, no GPU work, no push.

Evidence class: the seal reads the **real live calibration** artifacts under
`G:\XLM\calib\essential-web-production\calibration` and the real operator store
`G:\XLM\xlm-home`, read-only. The tests use authored fixtures, plus read-only
checks of the committed seal files.

Setup for every command:

```powershell
. .\scripts\operator_storage.ps1
$U = @('run','--offline','--locked','--no-sync','--extra','cpu','--extra','eval')
$env:UV_OFFLINE='1'
$env:OMP_NUM_THREADS='1'; $env:MKL_NUM_THREADS='1'; $env:OPENBLAS_NUM_THREADS='1'
$env:NUMEXPR_NUM_THREADS='1'; $env:TOKENIZERS_PARALLELISM='false'
$C = 'docs/implementation/evidence/ESSENTIAL-WEB-PRODUCTION-CALIBRATION'
$E = 'docs/implementation/evidence/ESSENTIAL-WEB-PRODUCTION-READINESS'
```

## Before editing

```powershell
git status --short
git rev-parse HEAD
git log --oneline -15
git merge-base --is-ancestor c643f2ef0b7a7e5f4e53fddc6d65de0dfbb26d9a HEAD
git merge-base --is-ancestor 5fb37c96b4bf8bcd661d7d0e11a8dd11d801dde7 HEAD
git merge-base --is-ancestor b2e13840b92483a7483c157cf5dec536d2d44c8d HEAD
```

All exit 0. Initial dirty state: modified `docs/implementation/STATUS.md`;
untracked `.bf/`, `.bt-c04/`, `.bt-fdc/`, two review evidence directories and
five review reports. All preserved and excluded from the commits.

## Seal

```powershell
uv @U python scripts/essential_web_calibration_seal.py --root G:/XLM/calib/essential-web-production/calibration --freeze $E --xlm-home G:/XLM/xlm-home --work-dir G:/XLM/temp/ew-seal-work --output-dir $C
uv @U python scripts/essential_web_calibration_seal.py --root G:/XLM/calib/essential-web-production/calibration --freeze $E --xlm-home G:/XLM/xlm-home --work-dir G:/XLM/temp/ew-seal-work --output-dir $C --verify
```

Exit **0 / 0**. Output: `sealed:` then `verified:`
`149f3eb48e047e6545314dcb40cc01fe2b0e8853898a83a2060f0e8c9032e2a1`. About 15
seconds each. The command reruns `essential_web_measure.measure`, rehashes every
file, and re-adapts the eight raw files through the current adapter (24 runs)
into the work directory, which it removes afterwards. It writes only the three
JSON files in `$C`. Nothing under `G:\XLM\calib` or the store was modified.

Two earlier seal runs during development produced digests `d9f62255…d197` and
`7337057f…884d`. They differ only by fields added later (malformed reason codes,
largest raw record); the measured totals were identical each time.

Malformed reason strings were inspected locally as counts. They are evaluator
codes, not record text; the seal stores the codes and buckets anything that is
not code-shaped as `other`.

## Tests

```powershell
uv @U python -m pytest tests/test_essential_web_calibration.py -n 0 -q -p no:cacheprovider --basetemp G:\XLM\temp\ewb
```

Exit **0**, **8 passed**, no skips. Authored fixtures for the arithmetic; two
tests read the committed seal, yields and crawl files.

## Lint and strict types

```powershell
$S = @('src/xlm/data/sources/essential_web_calibration.py','scripts/essential_web_calibration_seal.py','tests/test_essential_web_calibration.py')
uv @U ruff check @S
uv @U ruff format --check @S
uv @U python docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V4.1-PHASE-D-D01-D02-REPAIR/mypy_interpreted.py --strict @S
```

Exits **0 / 0 / 0**. The interpreted mypy runner avoids the compiled-mypy
application-control failure on this machine.

## Resources

Inputs read: 149,979,195 bytes of raw records twice (working copy and store
copy), plus adaptation outputs. Outputs: three JSON files, about 62 KB.
Process peak memory was not measured.
