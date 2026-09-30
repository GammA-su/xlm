# Essential-Web v4.1 M analysis — exact commands and exit statuses

2026-09-30. Windows 11 build 26200; CPython 3.12.13; uv, offline, locked, no
sync. Working directory `F:\Project\xlm-data-ultrax`, branch
`data/mix01-ultrax-6b`, starting HEAD
`c69a184ee86606d3a4960b689d3958617c9c09f3`. No network. Real evidence, not
fixtures, for the run and verify commands; authored synthetic fixtures for
every pytest command.

Common prefix (`$U`):

```
uv run --offline --locked --no-sync --extra cpu --extra eval
```

Test-run environment: `OMP_NUM_THREADS=1 MKL_NUM_THREADS=1
OPENBLAS_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1 TOKENIZERS_PARALLELISM=false
PYTHONUTF8=1`.

## Real run (seals the package)

```
$U python scripts/essential_web_m_analysis.py run \
  --preparation docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V4.1-PHASE-D-RESULT-REVIEW/m-analysis-preparation.json \
  --phase-d-root G:/Project/xlm-evidence-v4.1/essential-web-phase-d \
  --development-dir F:/Project/xlm-selector-sweeps/essential-web-v2 \
  --work-dir F:/Project/xlm-selector-sweeps/essential-web-v4.1-m-analysis \
  --output-dir docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V4.1-M-ANALYSIS
```

Exit **0** ([log](logs/run.log)). Whole-process wall 4.937 s; evaluator
`run_sweep` wall 2.703 s; one end-of-run RSS sample 98,328,576 bytes (an
endpoint sample, not a measured peak). Sealed repository outputs 1,284,049
bytes; derived input outside Git 13,838,037 bytes; together far below the
frozen 67,108,864-byte output cap.

## Seal verification (recomputes everything from the bound inputs)

Same arguments with `verify` in place of `run`. Exit **0**, twice: once
directly after the run ([log](logs/verify.log)) and once after all quality
commands. Every recomputed file is byte-identical to the sealed file and the
recomputed seal digest equals the sealed digest. The seal binds the command
string, so `verify` must be called with these exact argument spellings.

## Evidence-root preservation

A stat inventory (relative path, size, mtime in ns) of all files under both
G: roots and the development directory was taken before the run and after
the verify; the two logs are identical ([before](logs/roots-before.log),
[after](logs/roots-after.log)): Phase P 86 files / 3,241,704 bytes, Phase D
119 files / 206,319,966 bytes, development 9 files / 864,832 bytes. The
analysis opened exactly three Phase-D files, read-only:
`m_selected_metadata.jsonl`, `m_acquisition_manifest.json`,
`phase_d_receipt.json`. No file under `sealed/` and no `t_*` file was opened.

## Tests (authored synthetic fixtures only)

| Command | Exit | Result |
|---|---:|---|
| `$U python -m pytest tests/test_essential_web_m_analysis.py -n 0 -q -p no:cacheprovider` | 0 | 29 passed, 21.25 s ([log](logs/pytest-m-analysis.log)) |
| `$U python -m pytest tests/test_essential_web_selector_sweep.py -n 0 -q -p no:cacheprovider` | 0 | 60 passed, 66.16 s ([log](logs/pytest-frozen-evaluator.log)) |
| `$U python -m pytest tests/test_evidence_v21.py::test_selection_identity_constants tests/test_evidence_v21.py::test_scientific_namespace_stays_v2_0 tests/test_evidence_v22.py::test_v22_namespace_and_selection_unchanged tests/test_evidence_v41.py::test_scientific_identity_constants_are_unchanged tests/test_evidence_v41_phase_d.py::test_scientific_identity_is_preserved -n 0 -q -p no:cacheprovider` | 0 | 5 passed, 0.42 s ([log](logs/pytest-science-identity.log)) |

`-p no:cacheprovider` only avoids a pytest cache-directory permission warning
on this machine; it changes no test selection. NOT RUN: the fast and full
offline selections, CUDA tests, network-authorized tests. A focused pass is
not a full-suite pass.

## Lint, format, types

Files: `src/xlm/data/evidence_v2/m_input_adapter.py`,
`src/xlm/data/evidence_v2/m_analysis.py`,
`scripts/essential_web_m_analysis.py`,
`tests/test_essential_web_m_analysis.py` (mypy: the first three).

| Command | Exit | Result |
|---|---:|---|
| `$U ruff check <files>` | 0 | All checks passed ([log](logs/ruff-check.log)) |
| `$U ruff format --check <files>` | 0 | 4 files already formatted ([log](logs/ruff-format.log)) |
| `$U mypy --strict <3 files>` | 1 | Did not start: Windows application control blocks the compiled mypy module ([log](logs/mypy-compiled.log)) |
| `$U python docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V4.1-PHASE-D-D01-D02-REPAIR/mypy_interpreted.py --strict <3 files>` | 0 | Success: no issues found in 3 source files; same locked mypy from its pure-Python sources ([log](logs/mypy-interpreted.log)) |

An earlier `ruff format --check` exited 1 (three files needed reformatting);
they were formatted before the real run, so the sealed code hashes are those
of the formatted files. A scratch trial run into a temporary directory
preceded the real run and was discarded; it is not part of the package.
