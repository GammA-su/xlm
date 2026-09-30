# Essential-Web selector fast-track freeze — exact commands and exit statuses

2026-09-30. Windows 11 build 26200; CPython 3.12.13; uv 0.12.19, offline,
locked, no sync. Working directory `F:\Project\xlm-data-ultrax`, branch
`data/mix01-ultrax-6b`, starting HEAD
`e596f19cde59d6f9b922ebc4069cf79b231c27c0`. No network. Real evidence for the
verify and build commands; authored synthetic fixtures plus committed
repository artifacts for pytest.

Common prefix (`$U`):

```
uv run --offline --locked --no-sync --extra cpu --extra eval
```

Test-run environment: `OMP_NUM_THREADS=1 MKL_NUM_THREADS=1
OPENBLAS_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1 TOKENIZERS_PARALLELISM=false
PYTHONUTF8=1`.

## M seal verification (parent; recomputes everything)

```
$U python scripts/essential_web_m_analysis.py verify \
  --preparation docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V4.1-PHASE-D-RESULT-REVIEW/m-analysis-preparation.json \
  --phase-d-root G:/Project/xlm-evidence-v4.1/essential-web-phase-d \
  --development-dir F:/Project/xlm-selector-sweeps/essential-web-v2 \
  --work-dir F:/Project/xlm-selector-sweeps/essential-web-v4.1-m-analysis \
  --output-dir docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V4.1-M-ANALYSIS
```

Exit **0**, twice: before any work and after the freeze was built
([log](logs/m-seal-verify.log)). Seal digest
`afc972ccf570a5587bf7bd3b6b8d1d728a09f2230c49f1b0a19c1c74cda0b9bc`.

## Freeze build

```
$U python scripts/essential_web_fasttrack_freeze.py build \
  --m-evidence-dir docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V4.1-M-ANALYSIS \
  --development-dir F:/Project/xlm-selector-sweeps/essential-web-v2 \
  --t-package-manifest docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V4.1-T-BLINDED-PACKAGE/package_manifest.json \
  --output-dir docs/implementation/evidence/ESSENTIAL-WEB-SELECTOR-FASTTRACK-FREEZE \
  --parent-commit e596f19cde59d6f9b922ebc4069cf79b231c27c0 --decision-date 2026-09-30
```

Exit **0** ([log](logs/build.log)). Freeze digest
`c6f32a65f083c99b64245e25151f2cc73275093e1013d68b625c6d6f63d10a0c`. The freeze
binds this command string, so `verify` must use these argument spellings.

## Freeze verification

Same arguments with `verify` in place of `build`, without `--parent-commit`
and `--decision-date`. Exit **0**, twice ([log](logs/verify.log)): the
recomputed file is byte-identical to `freeze.json`.

## Arm-T preservation (stat only)

A recursive file count and byte total of
`F:\XLM-Review\essential-web-v4.1-t`: 722 files, 4,852,481 bytes
([log](logs/t-root-stat.log)), equal to the sealed record. No file there was
opened. `scripts/essential_web_t_package.py verify` was NOT RUN: it reads
the review texts.

## Tests

| Command | Exit | Result |
|---|---:|---|
| `$U python -m pytest tests/test_essential_web_fasttrack_freeze.py -n 0 -q -p no:cacheprovider` | 0 | 32 passed ([log](logs/pytest-fasttrack-freeze.log)) |
| `$U python -m pytest tests/test_essential_web_selector_sweep.py -n 0 -q -p no:cacheprovider` | 0 | 60 passed, 67.55 s ([log](logs/pytest-frozen-evaluator.log)) |
| `$U python -m pytest tests/test_essential_web_m_analysis.py -n 0 -q -p no:cacheprovider` | 0 | 29 passed, 21.77 s ([log](logs/pytest-m-analysis.log)) |
| `$U python -m pytest tests/test_evidence_v21.py::test_selection_identity_constants tests/test_evidence_v21.py::test_scientific_namespace_stays_v2_0 tests/test_evidence_v22.py::test_v22_namespace_and_selection_unchanged tests/test_evidence_v41.py::test_scientific_identity_constants_are_unchanged tests/test_evidence_v41_phase_d.py::test_scientific_identity_is_preserved -n 0 -q -p no:cacheprovider` | 0 | 5 passed ([log](logs/pytest-science-identity.log)) |

`-p no:cacheprovider` only avoids a pytest cache-directory permission warning
on this machine. NOT RUN: the fast and full offline selections, CUDA tests,
network-authorized tests. A focused pass is not a full-suite pass.

## Lint, format, types

Files: `src/xlm/data/evidence_v2/fasttrack_freeze.py`,
`scripts/essential_web_fasttrack_freeze.py`,
`tests/test_essential_web_fasttrack_freeze.py` (mypy: the first two).

| Command | Exit | Result |
|---|---:|---|
| `$U ruff check <files>` | 0 | All checks passed ([log](logs/ruff-check.log)) |
| `$U ruff format --check <files>` | 0 | 3 files already formatted ([log](logs/ruff-format.log)) |
| `$U python docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V4.1-PHASE-D-D01-D02-REPAIR/mypy_interpreted.py --strict <2 files>` | 0 | Success: no issues found in 2 source files ([log](logs/mypy-interpreted.log)) |

Compiled `mypy` was not attempted: Windows application control blocks it on
this machine. The three files were formatted once before the real build, so
the code hashes in the freeze are those of the formatted files. A scratch
trial build into a temporary directory preceded the real build and was
discarded.
