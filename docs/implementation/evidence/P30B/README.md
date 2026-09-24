# P30B measurement evidence

The [report](../../reports/P30B.md) records the conclusions and exact gate commands.
This directory retains authored, offline test evidence; it contains no model weights
or real-source datasets.

- `before-top50.md`: original top 50 nodes and module phase subtotals.
- `before-serial-nodes.tsv`: every original B node's exact recorded setup/call/teardown durations.
- `original-serial-ledger.tsv`: every original B node, resource-domain audit and per-run outcomes.
- `original-body-checks.json`: AST test-body and assertion preservation for all 68 originals.
- `inventory.json`: complete final collection, including deselected capabilities.
- `runs.json`: exact pytest arguments, reported elapsed time, phase totals, outcomes and resources.
- `acceptance.json`: mandatory final selection coverage, failures, skips and missing legs/nodes.
- `environment.json`: interpreter, locked installed packages, hardware and test-only thread settings.
- `startup.json`: instrumented import/setup profiles; profiling adds overhead.
- `imports-unprofiled.json`: three fresh-process observations per import, without profiling.
- `optional-index.json`: bounded real installed task-index profile; no model execution or downloads.
- `before-worker-receipts.json`: real original worker elapsed/RSS receipts and rounded work-time output.
- `raw/` and `raw-manifest.json`: complete compressed JSON/log/exit records and measurement scripts,
  with SHA-256 and sizes for both original and compressed bytes. Failed development probes remain.

The optional pytest plugin samples the controller and its descendants every 0.5 s.
RSS sums resident pages across processes, including shared pages; brief peaks can be
missed. CPU is a sampled lower bound, not an OS job-accounting total. Unique and peak
simultaneous process counts are observed counts. uv and parent launcher overhead
outside the pytest process tree is excluded. Per-node times are active phase wall
sums, not scheduler queue time. Plugin wall excludes some pytest startup/teardown;
comparisons use pytest's reported elapsed time.

Resource-domain annotations are source audits. Cost categories can overlap and do
not claim disjoint measured percentages. Session fixtures are charged to the node
that triggers setup/teardown. Queue fixture construction appears in original test
call time; cache capture and fresh session verification appear in setup/teardown.
Profiler cumulative times overlap and must not be added as independent costs.

A skip is not a pass. The large original preparation tests remain required even
when they fail. Hosted CI, CUDA, external network/live acquisition, dependency
installation and performance matrices are separate, unexecuted P30B capabilities.
`acceptance.json` records the aggregate status and failure exit code. The evidence
builder itself exits successfully when it correctly records a blocked result;
that artifact-build exit is not the acceptance exit. Focused diagnostics do not
replace the six named final-leg outcomes.
