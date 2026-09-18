# Prompt 23 — Independent end-to-end audit and completion report

Read the shared agent instructions, architecture, contracts, evaluation policy and current status before editing. Implement this milestone in the existing repository; run its tests and update the ledger.


Review the implementation as a skeptical maintainer, not as the author defending its design. Read ACCEPTANCE_MATRIX.md and every milestone report. Trace each advertised CLI command to working code and a relevant test. Search for silent defaults, fake success paths, unimplemented production branches, stale configs, unused plugins, hard-coded source fields, hidden dataset substitutions, mismatched counters and test skips portrayed as passes.

Run a clean offline install and full tests with uv, lint/type checks, CLI help tests and documented demo. Use a separate work directory. Exercise import→clean→dedup→split→tokenize→mixture→train→interrupt→resume→likelihood→comparison→report→export→reload. Corrupt shards/checkpoints/configs and verify failures are contained. Test network-failure/budget fixtures, nonmultiple token budgets, uneven masks, Unicode offsets, long answers, seed pairing, no-op plugins, cache invalidation, source depletion, late duplicate clusters and protected-access denial.

Run bounded CUDA profile/tests on actual hardware only when available and authorized. Validate at least one permitted real-source pilot separately from mocks. Official search benchmarks/reference-checkpoint checks require their own allowed data. Protected final deployment tests require the separate operator environment. Anything unavailable must be reported as NOT RUN/BLOCKED, never waved away as complete external validation.

Inspect the exact remaining coupling: add a no-op architecture/objective/optimizer/tokenizer through its plugin seam without changing the trainer or scorer; change mixture and budget through YAML; show immutable cache reuse and correct invalidation. Confirm the parameter counts, trained targets and saved code match the plan actually executed.

Fix discovered correctness bugs and add regression tests. Do not loosen acceptance thresholds or remove failing tests without an explicit reviewed reason. Produce `docs/implementation/FINAL_ACCEPTANCE.md` with requirement→file→test→actual result mapping, resources consumed, supported/unsupported combinations, open defects, live verification status and commands for the next authorized user action.

Completion language must distinguish: implemented software; offline-verified workflow; live-source verification; real CUDA verification; isolated final evaluation; and research results not yet produced. No state-of-the-art, novel-mechanism or billion-token training claim follows from implementing this platform. The final deliverable is a runnable, configurable, tested research system with clear operational limits—not benchmark victories invented during coding.


Finish with actual commands/results, artifact paths, blockers and the next prompt. Never label mocks, skipped tests or unrun research campaigns as verified.
