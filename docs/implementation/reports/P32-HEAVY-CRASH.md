# P32 heavy-worker crash repair — 2026-09-24

**P32 is correctness-complete within the declared offline acceptance scope.**
The native crash is reproduced without XLM and identified in the retained
Windows minidump. A test-only diagnostic repair passes the five-run stress set,
the normal heavy leg, and one full six-leg gate: **1,787 passed / 2 capability
skips / 0 failed**. Acquisition product behavior and runtime mutation detection
are unchanged. No merge or push was performed.

Worktree: `G:\Project\xlm-p32-heavy-crash`; branch
`fix/p32-heavy-worker-crash`; starting HEAD
`fa4ff5009b0eb4a1c28b45576cc8e7dc9ab1b020`. Branch, HEAD, clean status and
worktree list were verified before edits. No other worktree was modified.
The existing environment was copied read-only into this worktree; its editable
source path alone was adjusted. No dependencies were installed or synchronized.

Evidence: [commands](../evidence/P32-HEAVY-CRASH/COMMANDS.md),
[tables](../evidence/P32-HEAVY-CRASH/TABLES.md),
[structured results](../evidence/P32-HEAVY-CRASH/results.json), and
[raw-evidence hashes](../evidence/P32-HEAVY-CRASH/sha256.json).

## 1. Original failure and retained evidence

Exact node:
`tests/test_queue.py::test_toy_campaign_runs_sequentially_to_success@p30b-queue-campaign`.
Group: `p30b-queue-campaign`; worker: `gw1`; process: 36048 (`0x8cd0`).
Setup passed in 13.4648 s; call passed in 96.4991 s. Session-fixture teardown
did not finish. Xdist emitted a failed crash report with phase `???`; this is
not a successful teardown or a fully passed node.

The final Python stack was:
`runtime_seed` → `RuntimeSeed.verify_unchanged` → `RuntimeSeed.capture` →
`installed_runtime` → `_hash_batch` → `Path.relative_to`.
The timeout dump stops immediately after printing the pathlib filename, then
the fatal exception handler prints the second traceback. Original compressed
logs and node/phase evidence remain unchanged in
`docs/implementation/evidence/P32-RECOVERY/`.

The OS retained `python.exe.36048.dmp`; a copy is stored only in this worktree's
ignored artifacts. Application events 1000/1001 at 18:23:13/18:23:16 identify
`python312.dll`, exception `0xc0000005`, offset `0xa626c`. No debugger or symbols
were downloaded. No installed cdb/WinDbg/procdump was found. A bounded local
binary reader inspected the minidump and the exact interpreter's PE exports,
exception table and instruction bytes. The raw dump is not committed because
it can contain process memory; its hash and extracted diagnostic facts are
retained instead.

## 2. Reproduction matrix and causal evidence

Experiments were declared before execution, with fresh logs and finite bounds:

| Experiment | Hypothesis / configuration | Observed result |
|---|---|---|
| A | Exact unchanged node, `-n 0`, Python dev mode, original 120 s timeout | 1 pass; 183.890 s runner |
| B/C | Exact unchanged node, four workers, `--dist=loadgroup`, no worker restart | 1 pass; 123.985 s runner |
| Domain membership | Entire `p30b-queue-campaign` group | Contains only this node; B also covers C, without a redundant rerun |
| D | Separate parent execution | NOT RUN: A/B already ran the unchanged base candidate; no other worktree needed |
| Native diagnostic probe | Three fixed trials; stdlib `Path.relative_to` loop, 1 ms repeated `dump_traceback_later`, maximum 15 s each | 3/3 native access violations, exit 3221225477 |
| Control | Three fixed 15 s trials; same path loop, fatal handler enabled, no native timer | 3/3 exit 0 |
| Replacement | Three fixed 15 s trials; same path loop, repeated owned Python timeout dumps | 3/3 exit 0; 135 / 137 / 133 dumps |

The accelerated probe imports no XLM module, starts no xdist worker, hashes no
file, creates no executor, and uses nonexistent authored paths: `relative_to`
only manipulates path values. The probe exposes the native diagnostic race;
it is not a measurement of its frequency at a 120-second timeout. A/B passes
are retained, not treated as proof that the intermittent original failure was
absent. No unchanged failing gate was retried to obtain success.

## 3–5. Root cause, Windows exception and classification

The faulting native thread is **`0x7e20`**, distinct from the Python test thread
**`0x14890`** whose stack was being printed. It has no Python stack in the fatal
dump. The exception context's instruction pointer is
`python312.dll + 0xa626c`. PE unwind ranges place it inside
`PyCode_Addr2Line`, range `[0xa6218, 0xa6271)`, at offset `+0x54`.
Instruction bytes `8b 41 44` decode as `mov eax, [rcx + 0x44]`; **RCX is zero**.
The recorded access is a read from **address `0x44`**, matching the code object's
first-line field in the installed CPython headers. Raw stack slots also contain
addresses in the native traceback-dumping region; these are reported as stack
slots, not a symbolized debugger unwind.

The native timeout watchdog walks frames asynchronously while the target
Python thread is changing/retiring them. Here it reached line-number lookup
with a null code-object pointer. Windows correctly raised a native access
violation. The Python `Path.relative_to` line was the **observed target frame**,
not evidence that pathlib itself dereferenced invalid memory. The three native
probes fault in this same traceback/line-number region (`0xa6243`, `0x28768c`),
with unfinished timeout dumps and sometimes nonsensical line numbers.

Classification: **native CPython diagnostic defect triggered by test
infrastructure**, on the pinned Windows CPython 3.12.13 build. This conclusion
uses the original native exception context and a reproducer without XLM;
it does not rely on the age of the source file. It is not an acquisition,
filesystem, runtime-hashing, executor-shutdown or xdist-group ownership defect.
This task does not patch CPython or claim other interpreter builds are affected.

## Runtime inventory and fixture lifetime audit

`runtime_seed` is session scoped, hence instantiated per xdist worker that
requests it. It is not group scoped. The queue fixture temporarily replaces
only fixture capture; the function-scoped monkeypatch is undone before session
teardown. Teardown still performs a full fresh `RuntimeSeed.capture` and
compares inputs, payload and key. It has not been replaced by cached metadata.

`installed_runtime` traverses with bounded `os.scandir` and closes each iterator.
`_hash_batch` submits at most 64 selected entries to eight local threads,
fully consumes `pool.map`, then the executor context joins all its workers.
Each hash owns its file stream and hashlib instance. `hashlib.update` on 64 KiB
chunks can release the GIL; no mmap is used. Path calculation and the shared
inventory dictionary are updated in the inventory caller, not concurrently by
hash workers. Missing files and path/byte violations raise ordinary exceptions.

In the original timeout snapshot, the eight hashing workers were waiting on
their work queues while inventory was between batches. This is inside the
executor context, not proof of shutdown racing the interpreter. The queue body
had completed all three bounded toy subprocess jobs. The dump does not provide
a complete historical child-process inventory; no unsupported claim about every
OS process is made. Crucially, the stdlib reproducer requires neither child
processes nor executor threads. A second fixture destroying the environment
is not required to explain this crash.

## 6. Small test-infrastructure repair

`tests/thread_diagnostics.py` replaces only pytest's timeout-dump plugin hooks
on Windows CPython **3.12.13**, after the original plugin enables fatal-crash
handling. It retains the configured timeout and fatal handler. An owned Python
thread waits on an Event, obtains strong frame references with
`sys._current_frames`, and formats bounded Python tracebacks. The timer is
cancelled and joined before its duplicated descriptor closes, including PDB,
exception interaction and plugin unconfiguration. Diagnostic errors propagate
at join. The explicit exit-on-timeout option retains its exit behavior.

`tests/conftest.py` installs the adapter after the original configure hook.
Seven tests verify actual timeout output, cancellation/join, descriptor ownership,
an in-progress dump, diagnostic-error propagation, and nested pytest success/
failure with session teardown and fatal handling preserved. No skip, xfail,
assertion weakening, sleep-based lifecycle fix, or catch-and-ignore of an access
violation was added.

Snapshots are bounded to 100 threads, 50 frames per thread and 1 MiB of output.
They are best-effort live Python snapshots, not an atomic stop-the-world dump.
Unlike the native watchdog, the Python diagnostic thread requires the GIL;
it cannot report promptly if native code holds the GIL indefinitely. External
process-tree watchdogs retain the independent 1,800 s / 24 GiB bounds. Fatal
`PYTHONFAULTHANDLER=1` remains enabled. Other runtimes keep pytest's own plugin.

No acquisition source, recovery schema, reservation accounting, runtime inventory,
scientific identity, dependency lock or suite scheduling domain is changed.

## 7–10. Commits and validation

Focused diagnostic plus existing runtime-fixture regressions: **15 passed**,
58.93 s. After the final cancellation-cleanup adjustment, all **7** diagnostic
tests passed again in 1.87 s. These include unchanged actual-inventory comparisons
and same-size/mtime-preserving authored file mutation detection.

Repair commit: **`95ee6b3`**, `fix(tests): avoid unsafe native timeout frame walks
on Windows` (three test-infrastructure files). The certification scripts and
evidence follow in a separate commit.

The predeclared five-run group stress set passed **5/5**, with complete setup,
call and teardown, no worker restart, and runner durations **123.219 / 121.937 /
124.281 / 124.688 / 123.687 s**. The normal 120-second replacement diagnostic
fired successfully during inventory; no assertion or fixture was disabled.

The normal heavy leg passed **7/7**, including completed queue-fixture teardown,
in **344.07 s** pytest / **344.828 s** external runner time, with **2.832 GiB**
peak sampled process-tree RSS and exit 0. The single full six-leg gate follows
that green result. Its actual counts, exits, wall/RSS and completion disposition
are in the linked evidence. Only one xdist
controller runs at a time; every output name is fresh, worker restart is disabled,
and failures stop escalation. No full gate is run during diagnosis.

Final Tier A: **1,639 passed / 2 capability skips**, 105.82 s pytest time,
106.734 s external runner. The skips remain missing `cl` for CPU Inductor
compilation and absent Windows privilege for creating a real symlink; neither
is counted as a pass. All seven new regressions are included in this selection.
Core passed **68 tests** in 368.00 s; exclusive passed **8 tests** in 29.73 s.
These are final-gate results, separate from the earlier focused evidence.
The final-gate heavy selection passed **7 tests** in **342.92 s**, including
the original queue node's setup, call and teardown. No worker restarted.
Scale passed **8 tests** in **95.30 s**.
Installed optional dependencies passed **57 tests** in **189.04 s**. All six
legs exited 0. Aggregate: **1,787 passed, 2 skipped, 0 failed**, covering
**1,789 distinct selected nodes**. Every selected node has an outcome; the
collector normalizes xdist group suffixes when checking disjoint coverage.
The final gate ran exactly once after the required standalone heavy pass.
Its peak sampled process-tree RSS was 6.713 GiB (Tier A). All six changed
Python modules pass Ruff formatting/checks and mypy. No test code changed
after repair commit `95ee6b3`; finalization changed reporting/tooling only.

## 11–12. Scope and integration

**P32 is now correctness-complete for the tested offline contract**, and the
corrected candidate is eligible for release integration on that basis. The two
unavailable capabilities remain uncertified; this is not a live-source, CUDA or
power-loss certification. Conflict risk with the acquisition product commits
is low: no acquisition file overlaps. Shared `tests/conftest.py` and status/user
documentation may need textual reconciliation if other branches changed them.
Existing P32 acquisition correctness and its substantial measured throughput
cost remain as reported; no new performance claim is made.

Next read-only review command: `git show --stat 95ee6b3`. Review the diagnostic
repair and this certification evidence together with the completed P32 recovery
candidate before any separately authorized integration.

No external network, installation, research training, push or merge occurred.
HTTP acceptance fixtures are authored localhost-only tests. Commands and
compressed evidence are provided alongside the final report.

Requirement ledger:

| Requirement | Status | Evidence / boundary |
|---|---|---|
| Preserve and identify original failure | VERIFIED | Original committed logs unchanged; native dump and OS-event analysis retained. |
| Controlled reproduction and classification | VERIFIED | Serial/group baseline controls; native 3/3 failures; control and replacement each 3/3 passes. |
| Repair diagnostic lifecycle | IMPLEMENTED / VERIFIED | Owned, cancelled/joined timer and descriptor; seven direct/nested-pytest regressions. |
| Preserve runtime mutation detection | VERIFIED | Runtime source/fixture unchanged; existing actual-inventory/mutation checks pass. |
| Five-run normal group stress | VERIFIED | Five complete passes; no worker restart. |
| Normal heavy selection | VERIFIED | Seven passes, including the original teardown. |
| One full six-leg acceptance | VERIFIED | 1,787 passed / 2 skipped / 0 failed; 1,789 distinct nodes; every leg exits 0. |
| Static checks | VERIFIED | Ruff format/check and mypy pass for six changed Python modules. |
| Real Windows symlink creation / CPU Inductor compiler | BLOCKED | Existing privilege/compiler capability skips, not passes. |
| CPython binary repair, other interpreter builds/platforms | OUT OF SCOPE / NOT RUN | Workaround is restricted to the observed pinned Windows runtime. |
| Acquisition optimization, dependency changes, live acquisition, research training | OUT OF SCOPE | No corresponding changes or runs. |
| Push / merge | NOT RUN | Explicitly prohibited. |
