# C05 cleaned-corpus rerun readiness: commands and results

`U` = `uv run --offline --locked --no-sync` from `F:\Project\xlm-c05-clean-v1`.
Environment: `uv sync --offline --locked --extra cpu --extra eval` (Python 3.12.13,
uv 0.12.19); `OMP_NUM_THREADS=MKL_NUM_THREADS=OPENBLAS_NUM_THREADS=NUMEXPR_NUM_THREADS=1`,
`TOKENIZERS_PARALLELISM=false`. All fixtures are authored; no live data, G:, X:, network or
protected material.

| # | command | exit / result | log |
|---|---|---|---|
| 1 | (base `79d788c`) `U python -m pytest tests/test_c05_*.py tests/test_minhash_fast_kernel.py -m "not serial" -n 16 --dist=worksteal --max-worker-restart=0 -q -p no:cacheprovider --basetemp=C:/t/c05base` | 0: 345 passed | `c05-baseline-79d788c.log` |
| 2 | `U python -m pytest tests/test_c05_cleaned_rerun.py -n 0 -q -p no:cacheprovider --basetemp=C:/t/cr3` | 0: 31 passed | `cleaned-rerun-n0.log` |
| 3 | `U python -m pytest tests/test_c05_*.py tests/test_minhash_fast_kernel.py tests/test_minhash_arrow_kernel.py tests/test_c06_tokenizer_fit.py tests/test_c06_fast.py tests/test_c06_fast_hardening.py tests/test_opus_review_equivalence.py -m "not serial" -n 16 --dist=worksteal --max-worker-restart=0 -q -p no:cacheprovider -rs --basetemp=C:/t/c05all3` | 0: 586 passed | `c05-c06-nonserial-n16.log` |
| 3a | same selection, earlier run (before the `proof` verb; log overwritten by 3) | 1: 584 passed, 1 failed (`test_c06_fast_hardening.py::test_a1_stubborn_descendants_are_killed_and_reaped`, `grandchild.pid` missing under load). Isolated `-n 0` reruns: 3/3 passed. The code path is untouched. | (output recorded in the report) |
| 4 | `U python -m pytest tests/test_c05_*.py -m serial -n 0 -q -p no:cacheprovider` | 0: 1 passed, 361 deselected | `c05-serial-n0.log` |
| 5 | `U python -m pytest tests/test_quality_{cleaning_production,cleaning,cleaning_v2,performance,audit,detectors,hardening,final_repairs,acceptance_fixes}.py -n 16 --dist=worksteal --max-worker-restart=0 -m "not serial_exclusive" -q -p no:cacheprovider -rs` | 0: 532 passed, 7 skipped (POSIX `/proc`) | `quality-suites-n16.log` |
| 6 | `U python -m pytest tests/test_quality_hardening.py -m serial_exclusive -n 0 -q -p no:cacheprovider` | 0: 1 passed | `quality-serial-n0.log` |
| 7 | `U ruff format --check src tests scripts` | 0: 805 files formatted | `ruff-format.log` |
| 8 | `U ruff check src tests scripts` | 0 | `ruff-check.log` |
| 9 | `U mypy --strict src/xlm/data/exclusion src/xlm/data/dedup/minhash.py src/xlm/data/quality` | 0: no issues in 72 files | `mypy.log` |
| 10 | `git diff --cached --check` (code commit `b8a2a6a`) | 0 | `diff-check.log` |

Not run:
* the full repository suite (outside this milestone; the selection above covers C05,
  C06, MinHash, equivalence and quality);
* any real-data, protected or production command.

Implementation identity at the code commit (content-free):
`{"code_commit": "b8a2a6a32f63ad216dc4d213dfaa00df5dc1f87c", "code_identity":
"e8c7f0fa9656cd058a75cfb004a9463af72ff0afbb52983c45cd7f4481fe21c3",
"dependency_sha256": "a57fc5d14b25cf6f9ae93877f9806770a57c390e55e9e64e007d41f987087dcd"}`.
The docs commit changes no `src/` file, so `code_identity` is the same at the branch head.
`code_commit` is the head the operator plans from.
