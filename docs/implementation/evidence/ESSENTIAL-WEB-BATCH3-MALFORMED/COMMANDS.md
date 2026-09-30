# Exact commands and results — Essential-Web Batch-3 malformed stop (f00110)

2026-09-30; checkout `F:\Project\xlm-data-ultrax`, branch `data/mix01-ultrax-6b`.
Starting HEAD `22517b269ea694e8bdb9552e2fe6ac63e7731d7e`.

Environment: Windows 11 Pro 10.0.26200, Windows PowerShell 5.1, Python 3.12.13
from the existing locked CPU/eval environment (`uv run --offline --locked
--no-sync`). `pyproject.toml`, `uv.lock` and `.python-version` are unchanged.
Operator roots from `scripts/operator_storage.ps1`: durable `G:\XLM`, scratch
`C:\XLM-scratch`.

**No network request, no download, no batch run, no seal, no publication, no
write to the operator store, no push.** Every store command ran through
`no_network.py` (this directory), which makes any socket connection raise and
turns the exit code into 99. Every one reported `NETWORK ATTEMPTS: 0`.

Evidence classes:

- **Real local data, read-only**: the Batch-3 journal, batch record and plan,
  the 123 sealed receipts and ledgers, the retained f00110 source, the complete
  f00123 scratch copy and the prepared auto envelope.
- **Authored fixtures**: every test.

## Store read-only proof

A PowerShell snapshot of every file under `G:\XLM\plans\ew-fast`,
`G:\XLM\canonical\ew-fast`, `G:\XLM\acq-raw\ew-fast` and `C:\XLM-scratch\ew-fast`
(path, size, mtime) was taken before the first command and compared after the
last one: **1,601 files, 0 differences**. The private worker outputs went to a
scratch directory outside the store and were deleted by the script.

## Commands (after `. .\scripts\operator_storage.ps1`)

```powershell
$U = @('run','--offline','--locked','--no-sync','--extra','cpu','--extra','eval')
$E = 'docs/implementation/evidence/ESSENTIAL-WEB-BATCH3-MALFORMED'
$W = '<session scratch outside the store>'
uv @U python "$E/no_network.py" essential_web_fast resume-check --batch 3 --output "$E/resume-check-before.json"
uv @U python "$E/no_network.py" "$E/replay_unit.py" --batch 3 --rank 110 --output "$E/f00110-replay-before.json"
uv @U python "$E/no_network.py" "$E/sealed_malformed_profile.py" --output "$E/sealed-malformed-profile.json"
# worker at the starting HEAD, from a temporary detached worktree of 22517b2:
uv @U python "<worktree>/$E/no_network.py" "<worktree>/$E/complete_unit_offline.py" --batch 3 --rank 110 --work $W --output "$E/f00110-worker-before.json"
# after the change:
uv @U python "$E/mint_compatibility.py"
uv @U python "$E/no_network.py" essential_web_fast resume-check --batch 3 --output "$E/resume-check-after.json"
uv @U python "$E/no_network.py" "$E/complete_unit_offline.py" --batch 3 --rank 110 --work $W --output "$E/f00110-worker-after.json"
uv @U python "$E/no_network.py" "$E/replay_unit.py" --batch 3 --rank 123 --output "$E/f00123-replay.json"
uv @U python "$E/no_network.py" "$E/complete_unit_offline.py" --batch 3 --rank 123 --work $W --output "$E/f00123-worker-after.json"
uv @U python "$E/no_network.py" "$E/envelope_impact.py" --envelope 21cbad8ca0de96148b094c14753187145ba0558481a17b4b9ab6a2bd4527ad9e --output "$E/envelope-impact.json"
uv @U python "$E/no_network.py" essential_web_campaign status
uv @U python "$E/no_network.py" essential_web_fast gate --batch 3
```

| Command | Exit | Result |
|---|---:|---|
| `resume-check` before | 0 | 27/32 sealed; reuse f00110, f00123; resume f00122, f00125, f00126; 0 fresh |
| `replay_unit` f00110 | 0 | 78,689 rows, 42 malformed, frozen rule stops at row 282 |
| `sealed_malformed_profile` | 0 | 123 units, 4,965 / 10,253,689 malformed, max file 0.089% |
| worker at HEAD, f00110 | 2 | `MalformedLimitError` after 0.28 s |
| `mint_compatibility` | 0 | record `9c9b618b…7f18` |
| `resume-check` after | 0 | identical restart classification; campaign loads through the chain |
| worker after, f00110 | 0 | completed, 36.8 s, 42 malformed |
| `replay_unit` f00123 | 0 | 75,741 rows, 31 malformed, no stop under either rule |
| worker after, f00123 | 0 | completed, 34.0 s, 31 malformed |
| `envelope_impact` | 0 | old envelope differs only in running code and compatibility records |
| campaign `status` | 0 | next batch 3 `HUMAN_REVIEW_REQUIRED` (runner's failure mark) |
| `gate --batch 3` | 0 | `RUN` |

The evidence scripts were then edited for strict typing and formatting only and
all store commands after the change were run again: every JSON output was
identical apart from timing fields. The HEAD-worktree run used the script before
that edit (it differs only in how it imports the driver).

## Tests

```powershell
$env:OMP_NUM_THREADS='1'; $env:MKL_NUM_THREADS='1'; $env:OPENBLAS_NUM_THREADS='1'
$env:NUMEXPR_NUM_THREADS='1'; $env:TOKENIZERS_PARALLELISM='false'
uv @U python -m pytest tests/test_essential_web_malformed_whole_pass.py -n 0 -q -p no:cacheprovider --basetemp .bmw
uv @U python -m pytest <selection> -n 8 --dist=worksteal --max-worker-restart=0 -m "not serial" -q -p no:cacheprovider --basetemp .bm1
uv @U python -m pytest <selection> -n 0 -m serial -q -p no:cacheprovider --basetemp .bm2
```

- New module: exit **0**, **16 passed**.
- Related selection (`regressions.log`: the new module plus the 32 files of the
  automatic-campaign selection): parallel exit **0**, **802 passed**, 51 s;
  serial exit **0**, **3 passed**. **0 skipped.**
- The fast and full repository selections and CUDA tests were **not run**; this
  is a focused selection, not a full-suite pass.

## Static checks

```powershell
uv @U ruff check <10 files>; uv @U ruff format --check <10 files>; uv @U mypy --strict <10 files>
```

The two source files, the new test module, the updated Windows test and the six
evidence scripts: ruff all checks passed; format 10 files already formatted;
strict mypy (compiled, loaded normally) no issues in 10 source files, plus the
note about the unused `lm_eval` override.
