# Prompt 16 — Experiment plans, bounded local queue and mixture search

Read the shared agent instructions, architecture, contracts, evaluation policy and current status before editing. Implement this milestone in the existing repository; run its tests and update the ledger.


Implement immutable experiment planning and a robust local queue over the completed trainer/evaluator. A run plan resolves all preset/config/artifact references, actual code snapshot, dependency hash, seeds, size/counts, data exposure, compute/disk limits, evaluation tier/cadence and comparison track. Capture tracked changes plus allowlisted untracked plugin files without secrets; execute the captured code, not a mutable working tree that can change after enqueueing.

Implement `xlm experiment plan`, `xlm experiment submit`, `xlm queue run`, `xlm queue status`, `xlm queue cancel` and `xlm campaign plan`. Planning is read-only and displays total trial count, tokens by size, estimated cost range, storage and approval blockers. Production execution requires a matching authorization, not a default unlimited flag. Prevent simultaneous GPU jobs on the same device. CPU preparation can have its own bounded worker pool.

Support explicit experiment lists, grid sweeps and deterministic random/coarse-simplex mixture proposals. Emit complete configs before execution. No opaque optimizer chooses thousands of trials in the background. Support successive-budget rounds using 50M at 128M/512M/1B targets, 150M at 1B/3B, 300M at 6B, but keep them as plans until authorized. Preserve schedule-horizon rules: continuation runs use a shared declared horizon; standalone-budget runs restart with matched horizon-specific schedules. Never compare the two as identical.

Track FAILED, INTERRUPTED, CANCELLED, BLOCKED and SUCCEEDED states, retries with limits, checkpoint lineage, heartbeat and idempotent recovery. No duplicate run is launched after a process crash unless an explicit retry policy permits it. Failed experiments remain in the ledger. Evaluation/checkpoint cadence is token-based and predeclared. Queue code must not include ad hoc architecture or dataset special cases.

Acceptance: execute a multi-run toy campaign sequentially; only one GPU lease can be held; cancellation and crash recovery; immutable queued code/config; deduplication of identical plans; explicit source/seed changes alter IDs; unauthorized large run blocked; over-budget sweep refused; retries preserve failure evidence; horizon extension requires a fork. Show a six-mixture campaign plan without actually running its billion-token budget.


Finish with actual commands/results, artifact paths, blockers and the next prompt. Never label mocks, skipped tests or unrun research campaigns as verified.
