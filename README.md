# XLM — Complete Implementation Prompt Pack

**Prepared:** 18 September 2026. **Deliverable:** an implementation plan and executable acceptance requirements for a coding agent, not an already implemented XLM application. The recipes specify the configuration language to build; remote sources remain unadmitted until verified locally.

## What to do

Place this directory at `docs/implementation-pack/` inside the intended XLM repository. Give your coding agent `START_HERE.md`, then execute `prompts/00_*.md` through `prompts/23_*.md` in order. A fresh agent session can resume by reading the same documents and the implementation ledger. Keep the same repository throughout. Prompts are deliberately milestone-sized; do not ask the agent to write the whole system in one unverified patch.

Every prompt inherits `AGENTS_TEMPLATE.md`, `ARCHITECTURE.md`, `CONTRACTS.md`, and `EVALUATION_POLICY.md`. Ask the agent to read those files, not rely on its memory of previous conversations. For existing repositories, preserve working code and adapt incrementally rather than overwriting the repository.

The user’s requirements are: from-scratch English causal language models at 50M/150M/300M; BLiMP, ARC-Easy, HellaSwag, PIQA; data-mixture experiments followed by frozen-regime research on original architectures, losses, optimizers and tokenizers; staged promotion; one RTX 4090-class 24 GB GPU; uv; no FineWeb/FineWeb-Edu default or silent fallback. The supplied initial mixture is a hypothesis, not a demonstrated optimum.

## Included

- 24 ordered implementation prompts, each with deliverables and tests.
- Research contracts for data, model plugins, objective normalization, token budgets, checkpointing, evaluation and fair comparisons.
- A twenty-candidate discovery catalog, plus the proposed mixture and six mixture experiments.
- Three reference model presets and example experiment/campaign recipes.
- A prompt for adding later research ideas without changing the evaluator.
- Hardware, data-admission, operational safety, dashboard, export, and release requirements.
- A final acceptance matrix distinguishing implemented, verified, blocked, and untested functionality.

## Completion levels

1. **Offline vertical slice (00–06):** local fixture → tokenizer → tiny model → train → resume → score → generate.
2. **Real data system (07–13):** source discovery → admitted bounded acquisition → cleaning → deduplication/decontamination → frozen pool → tokenized mixture → pilot.
3. **Research platform (14–18):** CUDA profiling, official evaluator, experiments, statistical comparisons, promotion, innovation interfaces.
4. **Operational completion (19–23):** dashboard, model export, isolation/release audits, campaign setup, independent end-to-end verification.

Passing level 4 means the platform is ready to run research. It does NOT mean that 1B/3B/6B-token campaigns have been run, that a new method is novel, or that the models achieve state of the art.

## Precedence

Current explicit user constraints > this pack’s contracts > its individual prompts > examples > historical design documents. Preserve scientifically important controls from earlier documents. Replace the earlier FineWeb-centric candidate register with this catalog; never copy an obsolete data default back into the implementation. Explain any necessary contract migration in an architecture decision record.

## Scope boundaries

Core delivery is a robust local single-GPU system with CPU tests. Distributed training, online teacher calls, automatic paid APIs, custom CUDA kernels, instruction tuning, retrieval, and a hosted multiuser service are not required for v1. Provide honest capability flags rather than pretending they are implemented. Native generation and export are required; a base checkpoint is not automatically an instruction-following chatbot.

This pack does not launch training, contact paid services, download corpora, or modify your repository by itself.
