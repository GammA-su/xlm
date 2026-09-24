# P32 execution record

Working directory for every command below: `G:\Project\xlm-p32-recovery`.
Initial checks exited 0, found clean branch `fix/p32-recovery-closeout` and HEAD
`26c1238bb015494fa8646efe6ec3277bc3add0f2`:

```powershell
git branch --show-current
git rev-parse HEAD
git status --short
git worktree list
```

AGENTS.md, implementation status, the prior review, acquisition source/tests
and final test-selection policy were read before implementation. No other
worktree was modified. Existing parent `.venv` was copied into this worktree
using `robocopy /E /COPY:DAT /DCOPY:DAT /R:0 /W:0`; only the copied
`_editable_impl_xlm.pth` was changed to the new `src` path. No installation or
dependency synchronization occurred. `pyproject.toml`, `uv.lock`, and
`.python-version` remain unchanged.

## Environment wrapper

`scripts/p32.ps1` checks the branch and invokes:

```powershell
uv run --offline --locked --no-sync python <arguments>
```

It sets UV project environment/cache, XLM_HOME, TEMP/TMP, Python/Mypy search
paths into this worktree; disables Python downloads and dependency syncing;
sets HF offline flags; sets OMP/MKL/OPENBLAS/NUMEXPR threads to 1 and
TOKENIZERS_PARALLELISM=false. Optional-test fixtures supply private HF caches.
Existing CPython 3.12.13, NumPy 2.5.3, PyArrow 25.0.1, Torch 2.14.0+cpu,
tokenizers 0.23.2, pytest 9.1.1 and psutil 7.2.2 were used. Package versions
are independently recorded in results.json. Only authored localhost HTTP is
used; pytest's offline fixture rejects external sockets.

## Focused validation

```powershell
& scripts/p32.ps1 -m pytest tests/test_p32_publication.py -n 0 -q --basetemp artifacts/p32-recovery/pytest-publication
& scripts/p32.ps1 -m pytest tests/test_acquisition_leases.py tests/test_opus_review_prefix.py tests/test_selected_record_concurrency.py -n 0 -q --basetemp artifacts/p32-recovery/pytest-publication-initial
& scripts/p32.ps1 -m pytest tests/test_p32_orphans.py -n 0 -q -rs --basetemp artifacts/p32-recovery/pytest-orphans-final
```

Results: 20 passed / 3.93 s; 36 passed / 16.97 s; 12 passed, one Windows symlink
privilege skip / 14.80 s; each exit 0. An earlier orphan run had 10 passes and
the same skip before adding the explicit symlink/junction guard-logic cases.

Broad focused selection, exit 0, 190 passed / one skip / 231.21 s:

```powershell
& scripts/p32.ps1 -m pytest tests/test_p32_publication.py tests/test_p32_orphans.py tests/test_acquisition_fetcher.py tests/test_acquisition_bounds.py tests/test_acquisition_plan.py tests/test_acquisition_verifier.py tests/test_acquisition_leases.py tests/test_prepare.py tests/test_prepare_bounds.py tests/test_selected_record_concurrency.py tests/test_selected_record_encoding.py tests/test_hf_range_transport.py tests/test_opus_review_accounting.py tests/test_opus_review_prefix.py tests/test_opus_review_journal.py -n 0 -q -rs -m 'not performance and not scale and not network and not operator and not cuda and not environment_setup' --basetemp artifacts/p32-recovery/pytest-focused -p pytest_evidence --evidence-json artifacts/p32-recovery/focused.json
```

This run has one evidence-sampler thread warning caused by an authored
`psutil.Process` mock in a prepare-bounds test. Functional assertions pass;
its in-process RSS/CPU series is incomplete. Final gates additionally use an
external process-tree sampler, outside test mocks.

The 128-byte bootstrap regression was replayed against locally extracted
starting source using `artifacts/p32-recovery/reproduce_bootstrap.py`. Exact
pytest node: `tests/test_p32_orphans.py::test_initial_journal_bytes_are_bounded_before_first_write`.
It failed as expected, exit 1: actual control bytes 1,103 > 128. The helper only
prepends `artifacts/p32-recovery/baseline/src` to `sys.path` and invokes that node
with `-n 0 -q --basetemp artifacts/p32-recovery/pytest-bootstrap-before`.

After the bootstrap correction, this related selection exited 0 with
85 passed / one capability skip / 82.09 s:

```powershell
& scripts/p32.ps1 -m pytest tests/test_p32_orphans.py tests/test_p32_publication.py tests/test_acquisition_bounds.py tests/test_prepare_bounds.py -n 0 -q -rs -m 'not performance and not scale and not network and not operator and not cuda and not environment_setup' --basetemp artifacts/p32-recovery/pytest-bootstrap
```

## Real process death and restart

```powershell
& scripts/p32.ps1 scripts/review_opus_crashes.py --output artifacts/p32-recovery/crashes-early
& scripts/p32.ps1 scripts/review_opus_crashes.py --output artifacts/p32-recovery/crashes-mature --mature
& scripts/p32.ps1 scripts/review_opus_selected_crashes.py --output artifacts/p32-recovery/crashes-selected
& scripts/p32.ps1 scripts/review_opus_selected_crashes.py --parallel-publication --output artifacts/p32-recovery/crashes-parallel
```

All final controller exits 0; killed children exit 73; all 44/44/20/4 restarts
exit 0. Controllers retain the old phase hooks and add mandatory automatic
recovery and exact publication consumption assertions. Each child has a 60 s
subprocess timeout and 120 s plan deadline. Whole payloads are at most
2×16 MiB+17 bytes; transfer/temp/output budgets are 128 MiB, requests 20,
retry 0. Selected fixtures have 40 authored rows/file, explicit row ranges,
16 MiB resource caps, requests 100 and retries 0. Parallel adds one second
authored file and verifies forty final rows in deterministic order.

The initial complete matrices before the bootstrap correction are preserved
with `-before-bootstrap` suffixes. They were not deleted or relabeled as final.
Final runs use new roots and the final product source. The 16-death orphan and
competing-manager tests are real spawned processes, part of the focused and
exclusive selections, not mocked crash claims.

## Exact comparisons and regression measurements

```powershell
git archive --format=zip --output=artifacts/p32-recovery/baseline-src.zip 26c1238 src
Expand-Archive -LiteralPath artifacts/p32-recovery/baseline-src.zip -DestinationPath artifacts/p32-recovery/baseline
& scripts/p32.ps1 scripts/p32_measure.py prepare
& scripts/p32.ps1 scripts/p32_measure.py exact --label baseline
& scripts/p32.ps1 scripts/p32_measure.py exact --label current
& scripts/p32.ps1 scripts/p32_measure.py compare
& scripts/p32.ps1 scripts/p32_measure.py whole --label baseline
& scripts/p32.ps1 scripts/p32_measure.py whole --label current
```

All exits 0. Baseline/current commands run in separate interpreters, selecting
the corresponding source before imports. Exact comparison checks eight
authored acquisitions, receipts, consumed totals, occupancy and reservations.
The same literal authored receipt timestamp and fixed localhost repository
port remove incidental clock/port differences without omitting scientific fields.

The measurement method is reconstructed from local P31 object `1edacbd` using
the already reviewed read-constant / 1 GiB authored-admission adaptations.
Whole mode always runs 1/8/16 workers, 16×64 MiB, seed 31, 64 KiB reads.
Real fsync and existing locks are timed, never disabled. Every file SHA is
checked. Each point has a 4 GiB RSS / 900 s watchdog plus the bounded plan.
No other test/benchmark workload from this session runs concurrently with it.
No cold-cache or exclusive-machine claim is made.

After the bootstrap correction, current exact and three-worker measurements
were rerun once on final source with fresh destinations. Earlier current
results remain under `*-before-bootstrap`; no slow point was discarded or
retried until fast. The final sixteen-worker fsync outlier remains in the table.

## Final six-leg gate

```powershell
& scripts/p32.ps1 scripts/p32_gate.py tier-a
& scripts/p32.ps1 scripts/p32_gate.py remaining
& scripts/p32.ps1 scripts/p32_gate.py finish-unrun
```

The driver requires focused, bootstrap, orphan, exact and crash evidence first.
It then runs direct pytest sequentially with these unmodified policy selectors:

| Leg | Marker expression | Workers / distribution |
|---|---|---|
| A | `not serial and not performance and not scale and not cuda and not network and not operator and not optional_dependency and not environment_setup` | 16 / worksteal |
| Core | `serial_core and not serial_exclusive` | 4 / loadgroup |
| Exclusive | `serial_core and serial_exclusive` | 0 |
| Heavy | `serial_heavy` | 4 / loadgroup |
| Scale | `scale and not network and not cuda and not operator and not environment_setup` | 0 |
| Installed optional | `optional_dependency and not network and not cuda and not operator and not environment_setup` | 4 / loadgroup |

All include `-q --strict-markers -rs --durations=20 -o faulthandler_timeout=120`,
private `--basetemp`, and `-p pytest_evidence --evidence-json`. Xdist uses
`--max-worker-restart=0`; there is only one controller. Full argument arrays,
exit statuses, selected node IDs, outcomes, external wall/RSS measurements and
logs are retained. Each leg is bounded to 1,800 s / 24 GiB sampled tree RSS;
missing or mismatched worker collections fail certification.

The pre-bootstrap A pass and interrupted core attempt are preserved separately
under `gate-before-bootstrap`. That attempt is explicitly incomplete. Final
acceptance uses the new `gate` directory after the actual initialization fix.
It is not a retry of unchanged failing tests.

Final Tier A, core and exclusive exit 0. The heavy leg exits 1: a Windows
access violation kills `gw1` during `runtime_seed` teardown's installed-runtime
inventory. Its queue-campaign body passed, but the node is failed overall.
The runner stops on that failure. `finish-unrun` was added only to execute the
untouched scale/optional legs; it requires the preserved nonzero heavy exit,
refuses existing outputs, and does not rerun any completed selection. The
aggregate remains failed. No watchdog limit was reached. The fatal stack's
environment/runtime-fixture/conftest files are unchanged from the base, but
the native root cause is unresolved, not certified pre-existing.

Scale and optional exit 0 with 8 and 57 passes respectively. Final aggregate:
1,779 passed nodes / 2 capability skips / 1 failed node, 1,782 distinct selected
nodes, no missing outcomes or overlap. Collector exit 0 means evidence was
packaged successfully; `full_gate_passed` is explicitly **false**. All final
static checks exit 0 for fifteen changed Python files. `storage-final.json.gz`
records end-of-run free space; it is not a transient peak scratch measurement.

## Static checks and evidence packaging

```powershell
$changed = @(git diff --name-only 26c1238 -- '*.py') + @(git ls-files --others --exclude-standard -- '*.py')
$changed = @($changed | Sort-Object -Unique)
& scripts/p32.ps1 -m ruff check @changed
& scripts/p32.ps1 -m ruff format --check @changed
& scripts/p32.ps1 -m mypy @changed --follow-imports=silent --cache-dir artifacts/p32-recovery/mypy-cache
& scripts/p32.ps1 scripts/p32_evidence.py
git diff --check
```

All fifteen changed Python files passed Ruff, formatting and mypy before
acceptance. The later evidence/runner updates were checked again; a local
tuple annotation error found by mypy was corrected. Final static check logs
and exits are retained. The collector validates complete
matrix counts, absence of orphan temps, eight exact comparisons, gate node
coverage/disjointness, and writes tables plus compressed raw evidence. It
does not rerun tests or rewrite results to success. A body pass followed by
the worker crash counts only as a failed node in its summary (pytest's raw
heavy output says seven passed plus one failed; the table has six clean
passes plus one failed node). No external acquisition,
CUDA certification, model download, installation, research campaign, push or
merge was performed.
