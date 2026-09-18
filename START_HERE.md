# Bootstrap prompt — give this to the coding agent

You are implementing XLM in this repository. This is a research platform, not a collection of disconnected scripts. Read these files before editing:

- `docs/implementation-pack/AGENTS_TEMPLATE.md`
- `docs/implementation-pack/ARCHITECTURE.md`
- `docs/implementation-pack/CONTRACTS.md`
- `docs/implementation-pack/EVALUATION_POLICY.md`
- `docs/implementation-pack/ACCEPTANCE_MATRIX.md`
- the current implementation ledger, when present.

Inspect the repository and preserve existing work. Use uv exclusively for Python environments, dependency changes and execution. Implement the numbered prompts in `docs/implementation-pack/prompts/` sequentially. Start with prompt 00, or resume the first incomplete milestone supported by the ledger and repository evidence.

For this session, complete one milestone and its acceptance tests. Do not implement later milestones speculatively. Do not stop at an implementation plan: make the code changes, run the bounded tests, fix failures, and update `docs/implementation/STATUS.md` and a milestone report. Do not fabricate test results. A dependency, unavailable GPU or gated source may be BLOCKED or NOT RUN; that is not a successful test. Never weaken a test merely to make the report green.

Default implementation authorization covers offline tests and explicitly bounded local smoke runs. It does not authorize downloading whole corpora, accepting licenses, launching billion-token runs, paid API calls, using final benchmark labels, pushing to a remote repository, or deleting existing artifacts. Generate plans for operations requiring further authorization. Where credentials or approvals are missing, finish the independent local implementation and record the exact blocked action.

The architecture must let a user change data mixture, model family, size, loss, optimizer, tokenizer, context, token budget and seed through validated configuration. New mechanisms must be addable through documented plugins without editing the generic trainer or benchmark scorer. Unsupported combinations must fail clearly instead of silently reverting to the baseline.

At the end report: milestone; changed files; commands actually executed; results; remaining blockers; generated artifact paths; next numbered prompt. Do not claim the entire project is complete until prompt 23’s acceptance report supports that claim.
