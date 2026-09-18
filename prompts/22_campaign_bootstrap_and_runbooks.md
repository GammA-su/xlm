# Prompt 22 — Complete user workflows and production campaign preparation

Read the shared agent instructions, architecture, contracts, evaluation policy and current status before editing. Implement this milestone in the existing repository; run its tests and update the ledger.


Create a coherent high-level workflow on top of the tested services. Implement `xlm prepare --config ... --plan-only` and an explicitly authorized execution mode for discovery/admission checks → fetch → clean → dedup/exclude → pool freeze → tokenizer → token shards → mixture plan. The dry plan explains missing approvals and exact downstream invalidations. Repeated execution reuses verified artifacts and resumes incomplete stages; it never replaces data or repeats costly work silently.

Copy/adapt the supplied recipes into validated repository configs. Include tiny offline demo; baseline 50m/150m/300m; M0–M5 mixture search; the separately named no-IFM variant; fixed-recipe architecture comparison; objective/optimizer comparisons; matched-byte and matched-compute tokenizer experiments; multiple-seed confirmation; data×architecture and two-idea ablations. Every draft has explicit unresolved references where real artifacts are not yet admitted; a draft must not masquerade as executable.

Create resource-aware campaign plans with baseline calibration before candidate claims, 50M screens, shortlist validation at 150M, freeze after data selection, then innovation screening and 300M final confirmation. Preserve model-init/data seed separation, exact schedules, paired comparisons and protected final gates. Initial budgets are 128M/512M/1B at 50M, 1B/3B at 150M and 6B at 300M, not mandatory automatic runs. Generate a plan showing total cost from actual profile measurements where available; otherwise say measurement required.

Write practical uv-first Linux and Windows runbooks: install, doctor, offline demo, source probe/admission, small live pilot, prepare, profile, train/resume, evaluate search, compare, promote, queue, report, export and operator final evaluation. All snippets use the implemented flags and appropriate CPU/CUDA extra. Document disk locations, cleanup by reachability with dry-run, safe cancellation, common failures and artifact portability.

Acceptance: execute the documented offline workflow from a clean temporary repository environment; validate all recipe schemas; validate draft-to-plan blocking; run a toy multi-stage prepare/train/evaluate/compare/export path; restart preparation without duplicate acquisition; verify model size/mixture/loss/tokenizer/budget changes use the intended interfaces. Prepare but do not launch large campaigns. Report exactly which live datasets, GPU sizes and final-evaluation deployment remain unverified.


Finish with actual commands/results, artifact paths, blockers and the next prompt. Never label mocks, skipped tests or unrun research campaigns as verified.
