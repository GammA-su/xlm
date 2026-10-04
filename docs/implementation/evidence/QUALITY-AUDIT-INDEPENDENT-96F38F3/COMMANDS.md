# Independent audit execution record — 96f38f3

2026-10-04, `F:/Project/xlm-quality-audit`, PowerShell without profiles unless
otherwise noted. Exact target HEAD/branch initially matched; status empty.
All completed test runs used authored offline fixtures. No live-source or CUDA
tests. No implementation edits, commits, pushes, downloads or installations.

Environment: CPython 3.12.13, Windows 11 10.0.26200, uv 0.12.19,
NumPy 2.5.3, psutil 7.2.2. Imported `xlm` from this target worktree.
CPU/eval environment was already installed and locked. All test commands set:

```powershell
$env:OMP_NUM_THREADS='1'
$env:MKL_NUM_THREADS='1'
$env:OPENBLAS_NUM_THREADS='1'
$env:NUMEXPR_NUM_THREADS='1'
$env:TOKENIZERS_PARALLELISM='false'
```

No xdist controller was used (`-n 0`). The first independent probe run and the
non-overlay baseline ran concurrently as separate serial pytest processes; neither
contained process-memory measurement probes. The subsequent child-memory/deadline
checks and benchmark ran separately. Nested audit workers are product workers,
not nested pytest controllers.

## Identity and final preservation

```powershell
git status --short
git branch --show-current
git rev-parse HEAD
git log -3 --oneline
git diff --exit-code HEAD -- src tests scripts pyproject.toml uv.lock .python-version
git diff --check
```

Identity commands exit 0. Initial status empty; branch `feat/global-quality-audit`;
HEAD `96f38f311cccaa8a157fb8b3f4f4bb5ed0601d50`; log `96f38f3`, `40ce62a`, `03242cf`.
Implementation/dependency preservation and diff check exit 0. Final documentation
changes are intentional and listed in the report; no claim of final clean worktree.

## Initial interrupted command — scope deviation, not a completed result

```powershell
uv run --offline --locked --no-sync --extra cpu --extra eval python -m pytest tests/test_quality_detectors.py tests/test_quality_audit.py -n 0 --basetemp F:/qa-tmp-96f38f3 -q
```

Interrupted after observing that `c05_flow` invokes an authored C05 pipeline.
Tool-reported exit **1** after Ctrl-C, no completed pytest summary. The fixture had
already run and the first overlay test had started. This violated the explicit
no-C05 instruction. No G:, X:, protected volume or production corpus was involved.
No tests from this interrupted command are added to the completed totals.
The original milestone's blanket no-C05 claim should likewise not be inferred
from a suite that contains this fixture.

## Completed existing selection

```powershell
uv run --offline --locked --no-sync --extra cpu --extra eval python -m pytest tests/test_quality_detectors.py tests/test_quality_audit.py -k 'not overlay' -n 0 --basetemp F:/qa96-baseline -q -p no:cacheprovider
```

Exit **0**: **77 passed, 4 deselected in 16.26s**. Includes existing workers
1/2/4/8 determinism, resume, integrity, report, materialization, CLI, conservation
and detector tests. Four tests invoking `c05_flow` excluded. This is not a complete
repository acceptance run. Output was captured in the tool transcript; this file
records the result, not an invented copy of a full pytest log.

## Independent probes: original 37

```powershell
uv run --offline --locked --no-sync --extra cpu --extra eval python -m pytest docs/implementation/evidence/QUALITY-AUDIT-INDEPENDENT-96F38F3/test_independent.py -n 0 --basetemp F:/qa96-probes -q --tb=short -p no:cacheprovider
```

Exit **1**: **17 failed, 20 passed in 17.01s**. At execution the script contained
37 cases; it subsequently gained 12 additional cases below. The original 17
failures (no xfail, no repair, no retry):

```text
test_resume_rehashes_current_source
test_report_refuses_same_stat_mutation
test_source_mutation_after_hash_before_commit
test_deadline_covers_aggregation
test_one_worker_deadline_checked_after_last_task
test_binding_write_respects_output_cap
test_input_manifest_cannot_be_overwritten
test_nested_units_junction_cannot_delete_source
test_materialize_checks_review_manifest_hash
test_materialize_rehashes_source
test_forged_receipt_cannot_read_unaudited_file
test_source_metadata_is_not_exported_as_snippets
test_candidate_saturated_ratio_matches_advertised_cut
test_receipt_resource_envelope_is_recorded
test_report_refuses_noncomplete_receipt
test_language_many_scores_remain_bounded_and_mergeable
test_policy_xml_code_not_strong_html
```

Detailed traceback output is in the tool transcript. First 37 authored result
directories remain at `F:/qa96-probes`. The nested-junction test modifies/deletes
only an authored sentinel, never production data.

## Eight additional probes

```powershell
uv run --offline --locked --no-sync --extra cpu --extra eval python -m pytest docs/implementation/evidence/QUALITY-AUDIT-INDEPENDENT-96F38F3/test_independent.py -k 'forced_reverse or blocked_workers or worker_crash or child_rss or midstream or public_overlay or review_traversal or huge_review' -n 0 --basetemp F:/qa96-extra -q --tb=short -p no:cacheprovider --junitxml=docs/implementation/evidence/QUALITY-AUDIT-INDEPENDENT-96F38F3/extra.xml
```

Exit **0**: **8 passed, 37 deselected in 7.05s**. Machine-readable evidence:
[extra.xml](extra.xml). Process-tree RSS, reverse completion, crashed/blocked
workers, source mutation during scan, public-overlay/no-overlay conservation,
stale proof refusal, traversal and 256 MiB + 1 review-manifest refusal.

Public-overlay test consumes retained **authored** public fixture at
`F:/qa-tmp-96f38f3/quality-c050/root`. It does not invoke its producer. Do not run
C05 to regenerate this fixture under the current authorization. Future independent
reproduction should supply equivalent pre-authored public fixtures explicitly.

## Four final semantic probes

```powershell
uv run --offline --locked --no-sync --extra cpu --extra eval python -m pytest docs/implementation/evidence/QUALITY-AUDIT-INDEPENDENT-96F38F3/test_independent.py -k 'noncanonical or signed_overlay or boilerplate_lines_counts' -n 0 --basetemp F:/qa96-final-probes -q --tb=short -p no:cacheprovider --junitxml=docs/implementation/evidence/QUALITY-AUDIT-INDEPENDENT-96F38F3/final-probes.xml
```

Exit **1**: **4 failed, 45 deselected in 2.98s**. Machine-readable evidence:
[final-probes.xml](final-probes.xml). Two accepted noncanonical JSON cases, wrong
doc ID in a correctly signed authored public membership, and four boilerplate
hits misreported as four lines. Re-signing the authored public completion is
fixture construction only, not C05 matcher/exclusion execution; the original
public files are preserved.

Completed unique totals: **105 passed / 21 failed / no skipped**. Independent
cases: **28 passed / 21 failed = 49**. Four existing baseline overlay cases were
excluded; equivalent coverage is explicitly limited as stated in the report.
The incremental deselections do not add missing nodes to these totals.

## Static checks

Exact commands and individual exit codes are retained in:
[ruff-format.log](ruff-format.log), [ruff-check.log](ruff-check.log),
[mypy-strict.log](mypy-strict.log). All exit **0**; format/check/mypy cover 15 files:

```powershell
uv run --offline --locked --no-sync ruff format --check src/xlm/data/quality tests/test_quality_audit.py tests/test_quality_detectors.py tests/quality_fixtures.py scripts/quality_audit_benchmark.py
uv run --offline --locked --no-sync ruff check src/xlm/data/quality tests/test_quality_audit.py tests/test_quality_detectors.py tests/quality_fixtures.py scripts/quality_audit_benchmark.py
uv run --offline --locked --no-sync mypy --strict src/xlm/data/quality tests/test_quality_audit.py tests/test_quality_detectors.py tests/quality_fixtures.py scripts/quality_audit_benchmark.py
git diff --check
```

They first ran as a combined shell command (exit 0), then were captured separately
to record exact individual exits. No source changed between checks. Mypy reported
only an unused `lm_eval` override note. Audit-only probe scripts are outside this
static selection and are not presented as production modules.

## Detector/performance experiment

```powershell
uv run --offline --locked --no-sync --extra cpu --extra eval python docs/implementation/evidence/QUALITY-AUDIT-INDEPENDENT-96F38F3/measure.py
```

Exit **0**. 64 authored cases, 3 scaling trials at each of 4 lengths (largest
192,000 UTF-8 bytes), cProfile detector sample, and an 8-file/33,604,664-byte
authored audit with workers 1/8. `F:/qa96-benchmark` retains source, manifest and
both completed audit outputs. Script refuses an existing root; do not remove
evidence merely to rerun. No benchmark numbers or source-file hashes are fabricated.

```powershell
uv run --offline --locked --no-sync --extra cpu --extra eval python -c "import cProfile,pstats,io; from pathlib import Path; from xlm.data.quality.scan import load_manifest,file_tasks,process_chunk; m=load_manifest(Path('F:/qa96-benchmark/manifest.json')); task=next(file_tasks(m.data_root,m.files[0],None,1024**2)); p=cProfile.Profile(); p.enable(); result=process_chunk(task); p.disable(); s=io.StringIO(); pstats.Stats(p,stream=s).strip_dirs().sort_stats('cumulative').print_stats(35); Path('docs/implementation/evidence/QUALITY-AUDIT-INDEPENDENT-96F38F3/kernel-profile.txt').write_text(s.getvalue(),encoding='utf-8'); print(s.getvalue())"
```

Exit **0**. 723 authored documents, one already-frozen source chunk, no writes
except profile evidence. First attempt used `pstats.Stats(p,s)` and failed with
TypeError after measuring (exit 1, no saved profile); corrected keyword
`stream=s` produced the retained profile. A separate brace-expanded `rg` command
failed PowerShell parsing (exit 1); searching the package directory corrected it.
The first attachment read from the initial workspace also printed local PowerShell
profile execution-policy errors; subsequent commands used `login=false`.

Final scratch inventory command (exit 0):

```powershell
uv run --offline --locked --no-sync python -c "import json; from pathlib import Path; roots=['F:/qa96-benchmark','F:/qa96-baseline','F:/qa96-probes','F:/qa96-extra','F:/qa96-final-probes']; data={root:sum(f.stat().st_size for f in Path(root).rglob('*') if f.is_file()) for root in roots}; Path('docs/implementation/evidence/QUALITY-AUDIT-INDEPENDENT-96F38F3/scratch-logical-bytes.json').write_text(json.dumps(data,indent=1),encoding='utf-8'); print(json.dumps(data,sort_keys=True))"
```

The five new scratch directories total **372,536,321 logical file bytes**, including
the 256 MiB + 1 oversized-review test file. This is a final logical inventory,
not peak allocated disk blocks; aliases can double-count retained authored files.
It excludes the original interrupted fixture directory, environment/cache and
repository evidence. No scratch was removed. A preceding inventory one-liner
using nested `exec` quoting failed with SyntaxError (exit 1); the dictionary
comprehension above is the corrected command. Final separately captured
`git diff --check` and implementation/dependency `git diff --exit-code` both exit 0.

## Not run and next action

No full repository suite, CUDA, live source, real review, real corpus, protected
deployment, 64 MiB pathological allocation, power-loss durability, production
overlay timing, 2,035-unit independent aggregation-scale rerun, or raw disk test.
No random-completion test separate from forced reverse ordering. Static-only
claims and injected/mock identity changes are distinguished in the report.

Next: review [the independent report](../../reports/QUALITY-AUDIT-INDEPENDENT-96F38F3.md)
and authorize a separate repair task for I01–I14. The real audit stays blocked.
