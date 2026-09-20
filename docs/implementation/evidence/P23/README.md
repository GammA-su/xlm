# P23 evidence

Actual logs and machine-readable results, 2026-09-19. See ../../FINAL_ACCEPTANCE.md
and ../../reports/P23.md for scope and unresolved defects.

- `results.json`: full-suite command results, resource samples and copied-source hashes.
- `summary.json`: JUnit counts per module and final formatting/lint correction.
- `initial_clean_results.json` / `initial_clean_sync.log`: fresh cache-backed offline install; includes the first audit-collector failure, preserved.
- `inspection.json`: unchanged lock/contracts, wheel contents, final source comparison.
- `resources.json`: logical retained bytes, not physical allocated space or a disk cap.
- `cuda_profile.json`, `cuda.xml`, `cuda.log`: separately authorized real GPU evidence.
- `live_*.json`: metadata-only source pilot; zero corpus rows.
- `live_recheck.py`: exact separately authorized driver; it performs network I/O if invoked and is not part of offline test automation. Reusing its original work directory can return cached files; reauthorization is required for a changed plan.

The runner's aggregate exit was 1 because its copied help test was unformatted.
That formatting-only difference was fixed in the delivered root; final lint and
format checks both exited 0. The complete tests themselves exited 0 (884 passed,
1 skipped, 12 deselected). Logs have not been rewritten to hide earlier failures.
The `install_scope` in the raw runner result refers to the environment established
by the initial clean install; the final `sync` reused that environment.
