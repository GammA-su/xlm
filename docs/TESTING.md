# Test gates

Use a previously synchronized, locked Python 3.12 CPU/evaluation environment.
`--offline --locked --no-sync` prevents these commands from installing packages.
An absent dependency is not a passed capability check. See the CPU/CUDA installation
policy in the [Windows](runbooks/windows.md) and [Linux](runbooks/linux.md)
runbooks; environment provisioning is separate from running tests.
If the existing environment is outside this checkout, set
`UV_PROJECT_ENVIRONMENT` to its absolute directory before invoking uv. The exact
environment used for this worktree is recorded in the P30 report.

For PowerShell, set these **test-process** limits first:

```powershell
$env:OMP_NUM_THREADS='1'
$env:MKL_NUM_THREADS='1'
$env:OPENBLAS_NUM_THREADS='1'
$env:NUMEXPR_NUM_THREADS='1'
$env:TOKENIZERS_PARALLELISM='false'
$env:PYTHONDONTWRITEBYTECODE='1'
$env:PYTHONPATH=(Join-Path $PWD 'src')
$env:XLM_HOME=(Join-Path $PWD 'artifacts/test-home')
```

Use one xdist controller at a time. Coordinate with other sessions on the host.
Workers have private fallback `XLM_HOME` directories; individual tests use private
temporary directories. Never set global pytest `addopts` to enable workers: nested
pytest/subprocess tests must not inherit parallel execution.

## Routine development and integration

During a repair, run the failing nodes and directly related regressions first:

```powershell
uv run --offline --locked --no-sync python -m pytest tests/test_token_publication.py -n 0 -q --durations=10
```

Tier A is the complement of the explicit exclusions, so new unmarked correctness
tests automatically enter it. `slow` is informational, not an exclusion.
Run this safe fast selection for routine development (worker recommendation and
measured host conditions are in the P30 report):

Twelve workers was fastest in the local 4/8/12/16 single-trial sweep. Use four
when memory is constrained; remeasure on other hosts instead of choosing from
logical CPU count alone.

```powershell
uv run --offline --locked --no-sync python -m pytest -q --strict-markers -m "not serial and not performance and not scale and not cuda and not network and not operator and not optional_dependency and not environment_setup" -n 12 --dist=worksteal --max-worker-restart=0 --durations=30
```

For cross-cutting integration, run A and both core commands below. The exclusive
selection is complementary, mandatory, and small; it retains timing-sensitive
checks. Domain groups use one xdist controller and private per-test homes. The
exact-node resource audit is `tests/suite_domains.json`; unknown serial tests
default to core/exclusive, never disappear from a gate.

```powershell
uv run --offline --locked --no-sync python -m pytest -q --strict-markers -m "serial_core and not serial_exclusive" -n 4 --dist=loadgroup --max-worker-restart=0 --durations=30
uv run --offline --locked --no-sync python -m pytest -q --strict-markers -m "serial_core and serial_exclusive" -n 0 --durations=30
```

Tests are grouped by audited resource ownership and fixture prerequisites, not by
filename alone. Process-pool tests share a group to bound fan-out. Recovery tests
kill only their owned processes; local servers bind ephemeral loopback ports.
Frozen admission, custom-worker, and provenance groups have independent mutable
state. `--dist=worksteal` is refused for B; it remains the recommendation for A.
Exclusive or unaudited optional tests are refused under xdist. Use `-n 0` or audit
their isolation before adding them to the parallel catalog.

The runtime fixture cache applies only to explicitly listed queue fixture captures.
It stores immutable bytes from a real inventory, supplies fresh dictionaries, and
checks a fresh inventory at session teardown. Lock/project/Python-pin, producer,
interpreter/platform and extras inputs are bound to that inventory. Changed inputs
and explicit alternate sites use the original function. Environment mutation,
compiler/tool and frozen-worker checks bypass reuse. Real CLI subprocesses and
worker verification always inventory actual installed bytes. Do not install or
modify dependencies during a test session. Append `--fresh-runtime` to disable
automatic queue fixture reuse; cache contract tests still exercise their subject.

On the pinned Windows CPython 3.12.13 build, pytest timeout diagnostics use an
owned Python thread instead of `faulthandler.dump_traceback_later`. The native
timer reproducibly crashes while inspecting changing frames; see the
[P32 crash diagnosis](implementation/reports/P32-HEAVY-CRASH.md). The configured
`faulthandler_timeout` still emits bounded thread snapshots, including during
fixture teardown. Fatal-crash handling remains enabled. Runtime verification
still rehashes the full installed inventory; this change adds no identity cache.
The diagnostic thread is cancelled and joined before its descriptor closes.
Python snapshots require the GIL, so retain an external process watchdog for
native hangs. Other interpreter versions use pytest's standard implementation.

## Final offline correctness acceptance

All six commands are required: **A, core, exclusive core above, plus heavy,
correctness-scale, and installed optional below**. Core and heavy together retain
every original B test. Heavy means composed system evidence (multi-source workflows,
multi-job campaigns, full preparation restart/rebuild, and the settings/resume
matrix); it is not a waiver for slow tests. Smaller core/API oracles complement
these unchanged originals. Record each exit status; any failure,
collection error, missing group, or worker crash fails aggregate acceptance.
Report skips separately. Required unavailable capabilities block their certification.

The historical P30B gate recorded a Windows checkpoint rename failure in the
grouped queue campaign and a journal replacement failure in A. Those records
remain preserved. The latest P32 acceptance evidence is in the
[heavy-crash closeout](implementation/reports/P32-HEAVY-CRASH.md), following the
acquisition recovery corrections. A passing focused rerun alone never clears a
failed full-gate record.

```powershell
uv run --offline --locked --no-sync python -m pytest -q --strict-markers -m serial_heavy -n 4 --dist=loadgroup --max-worker-restart=0 --durations=30
uv run --offline --locked --no-sync python -m pytest -q --strict-markers -m "scale and not network and not cuda and not operator and not environment_setup" -n 0 --durations=30
uv run --offline --locked --no-sync python -m pytest -q --strict-markers -m "optional_dependency and not network and not cuda and not operator and not environment_setup" -n 4 --dist=loadgroup --max-worker-restart=0 --durations=30
```

Installed evaluation APIs retain module-fixture locality; independent real CLI
campaigns run in separate groups. Each worker has a private Hugging Face cache,
with offline flags set before optional imports. No installed backend is omitted.

For lower memory, replace grouped B with the original complete serial command and
run optional with `-n 0`; A can use the previously measured four-worker option:

```powershell
uv run --offline --locked --no-sync python -m pytest -q --strict-markers -m "serial and not performance and not scale and not cuda and not network and not operator and not optional_dependency and not environment_setup" -n 0 --durations=30
uv run --offline --locked --no-sync python -m pytest -q --strict-markers -m "optional_dependency and not network and not cuda and not operator and not environment_setup" -n 0 --durations=30
```

Tier C contains separate `scale` and `performance` markers. Scale retains original
large equivalence and resource-bound assertions and belongs in final correctness.
Timing matrices are explicit performance evidence. Five added small dedup oracles
reuse the original equivalence assertions; the large versions retain their counts.

## Performance acceptance

Run on an otherwise idle host, without xdist, and keep the printed tables:

```powershell
uv run --offline --locked --no-sync python -m pytest -s --strict-markers -m "performance or scale" -n 0 --durations=30
```

For timing matrices only, replace the expression with `performance`. For bounded
profiling, select named nodes and label the result partial. Do not assert wall-time
thresholds. The dedup 100k fallback alone has historically taken over 30 minutes
across its endpoints; inspect the available backend before budgeting a full run.

## Explicit capabilities and diagnostics

Tier D comprises `optional_dependency`, `cuda`, `network`, `operator`, and
`environment_setup`. The last marker identifies the relocated-environment test
that deliberately executes offline `uv sync`; **do not run it under a no-install
instruction**. None of these capabilities is implicitly enabled by the fast gate.
Network and operator tests require authorization; CUDA requires matching hardware.

```powershell
# Only with authorization to construct an offline environment:
uv run --offline --locked --no-sync python -m pytest -m environment_setup -n 0 -vv
# Only with the intended CUDA environment/hardware:
uv run --offline --locked --no-sync python -m pytest -m cuda -n 0 -vv
```

`pytest-timeout` is not in this tested environment; no dependency or process-kill
plugin was added. To locate slow work, use explicit nodes or a gate expression:

```powershell
uv run --offline --locked --no-sync python -m pytest tests/test_frozen_execution.py -m "not environment_setup" -n 0 -vv -x --durations=30 --durations-min=.1 -o faulthandler_timeout=120
uv run --offline --locked --no-sync python -m pytest --collect-only -q -n 0 --strict-markers
```

Faulthandler prints stacks; it does not impose a deadline or terminate a child.
Distinguish an active slow test from a hang using the node name, stacks and process
activity. Preserve the first failure; do not retry until green.

The optional `scripts/pytest_evidence.py` plugin records node IDs, phase outcomes,
fixture cache hits, wall time and process-tree resources sampled every 0.5 seconds.
CPU is an observed lower bound (short-lived or ending processes can be missed);
both peak simultaneous and unique observed process counts are recorded. Add `scripts` to
`PYTHONPATH`, then append `-p pytest_evidence --evidence-json PATH` to any command.
It does not change selection, assertions, scheduling or timeouts. RSS sums processes
(including shared pages) and can miss short-lived peaks. Direct pytest needs no wrapper.
Plugin wall time excludes pytest startup before plugin configuration and final
teardown; use pytest's reported elapsed time for comparisons of complete invocations.

See [P30](implementation/reports/P30.md) for measured results, the original-node
coverage ledger, remaining failures, and cherry-pick guidance.
See [P30B](implementation/reports/P30B.md) for serial/optional profiles, domain
repeatability, all-original-node preservation and final acceptance results.
