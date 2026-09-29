# Phase-D implementation commands and artifacts

2026-09-29; CWD `F:\Project\xlm-data-ultrax`; Windows 11 Pro 26200 AMD64;
CPython 3.12.13; PyArrow 25.0.1; the existing locked environment, used with
`--offline --locked --no-sync`. There was no dependency change, index access
or network use. Git Bash and PowerShell were both used.

Start: `git status --short`, `git rev-parse HEAD`, `git log --oneline -12`
all exited 0, at HEAD `ed8efcf3c85c265828a88579a264f08a2048640f`.
Pre-existing dirty or untracked user files were preserved and never staged:
`STATUS.md` and prior review directories/reports.

Test environment: `OMP_NUM_THREADS=MKL_NUM_THREADS=OPENBLAS_NUM_THREADS=NUMEXPR_NUM_THREADS=1`,
`TOKENIZERS_PARALLELISM=false`, `-n 0 -p no:cacheprovider`. Prefix `U` =
`uv run --offline --locked --no-sync --extra cpu --extra eval`.

| Command | Exit | Log / result |
|---|---:|---|
| `U python docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V4.1-PHASE-D/build_phase_d_freeze.py plan` | 0 | provisional digests (the grammar was then frozen) |
| `U python …/build_phase_d_freeze.py freeze` | 0 | protocol `bb2bca6f…`, freeze `9c4612fe…`, plan `23a26ffc…` |
| same, rerun before commit 1 | 0 | all five JSON artifacts byte-identical |
| `U python -m pytest -n 0 -q -p no:cacheprovider tests/test_evidence_v4_{plan,transport,engine,e2e,wire}.py tests/test_evidence_v41.py tests/test_evidence_v41_phase_d.py` | 0 | `pytest-v4-v41-phase-d.log`: 412 passed |
| `U python measure_pytest.py -n 0 -q -p no:cacheprovider tests/test_evidence_v41_phase_d.py` | 0 | `pytest-phase-d-measured.log`: 75 passed, 19.557 s, peak working set 159,285,248 bytes (in-process `GetProcessMemoryInfo`) |
| `U python -m pytest -n 0 -q -p no:cacheprovider tests/test_evidence_v3.py tests/test_evidence_v2_core.py tests/test_evidence_v2_text.py` | 0 | `pytest-science-regressions.log`: 69 passed |
| `U ruff check` / `ruff format --check` / `mypy --strict` on the 23 v4 files | 0 / 0 / 0 | clean |
| `U python scripts/evidence_v41_phase_d.py verify` | 0 | `cli-verify.log` |
| `U python scripts/evidence_v41_phase_d.py show-plan` | 0 | `cli-show-plan.log`: 55 operations |
| `U python scripts/evidence_v41_phase_d.py phase-d-status` | 0 | `cli-status.log`: `NOT_STARTED`, `exists: false` |
| `U python scripts/evidence_v41_phase_d.py phase-d --confirm-plan-digest 762cef78…7711` | 1 | `cli-wrong-digest.log`: refused |
| `U python scripts/evidence_v41_phase_d.py phase-d --confirm-plan-digest x --url https://evil.example/` | 2 | `cli-url-option.log`: unrecognized argument |
| `U python check_real_parent.py` | 0 | `check-real-parent.log`: real parent binds, unchanged, no Phase-D root |

## Failures observed and how they were resolved

- The first synthetic run had 2 failing tests, both test-authoring errors
  corrected before the freeze. One flipped a footer's final byte to `0x00`,
  but that byte already was the Thrift stop byte `0x00`, so it now XORs the
  byte. The other searched for `"text"` in a manifest that legitimately
  contains the status name `full_text_available`; it now checks for the
  absence of `text`/`locator(s)` keys.
- The first full regression run had 2 failures in
  `tests/test_evidence_v41.py`. Both asserted that the v4.1 Phase-P root is
  absent, which has been false since the reviewed live Phase-P run.
  Reproduced on pristine HEAD with `git worktree add --detach <scratch> ed8efcf`
  and HEAD's own `src` on `PYTHONPATH` (verified by import path): 2 failed,
  exit 1. The worktree was removed with `git worktree remove --force`. Both
  assertions were updated to state-independent checks (see the report,
  section 9).
- The first memory measurement read a launcher process (4,460,544 bytes) and
  was discarded as invalid. `measure_pytest.py` measures in-process. Its
  first run failed on a missing `ctypes` handle signature (exit 1), and the
  corrected run is the one recorded.

No test was skipped, retried to green or xfailed. Live Phase D, real decoding,
the full acceptance suite and the `probe_b01_b02.py` replay were NOT RUN.

## Files

- `check_real_parent.py`: read-only real-parent binding check (engine
  `_verify_parents`, dry-plan check, before/after inventory, sockets refused).
- `measure_pytest.py`: in-process pytest runner that reports the peak
  working set.
- Logs listed above.
