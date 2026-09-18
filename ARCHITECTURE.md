# XLM architecture

## Design objective

Make research changes cheap while keeping evidence traceable. Use a modular Python package, typed contracts, data-only YAML, immutable artifacts, explicit state machines, and one local job runner. Training must be usable without the dashboard, internet, a tracking account, or a proprietary model provider.

## Pipeline

```text
Source discovery and approval
  → pinned, bounded acquisition
  → source adapter → canonical document records
  → normalization and deterministic quality filters
  → duplicate/lineage clusters and evaluation exclusions
  → stable train / diagnostic-validation / text-audit split
  → immutable reusable cleaned pool
  → frozen tokenizer → immutable per-source token shards
  → mixture exposure plan → deterministic batches
  → model + objective + optimizer + schedule
  → training + safe checkpoint/resume
  → likelihood adapter → development evaluation
  → paired comparison → promotion specification
  → isolated final evaluation → research release
```

Data acquisition, cleaning and deduplication happen before a training run. Do not re-download or re-clean the same content when only weights or architecture change. A tokenizer change invalidates token shards, not the canonical text pool. A filter change invalidates downstream artifacts. A model change invalidates predictions, not corpus preparation. A dataset revision change creates a new corpus lineage, not a silent cache replacement.

## Package tree

```text
src/xlm/
  cli/             # Typer entrypoints; parsing and presentation only
  config/          # schemas, composition, validation, canonical hashes
  core/            # protocols, dataclasses, capabilities, identifiers
  artifacts/       # manifests, content addressing, lineage, locks, atomic IO
  data/
    sources/       # source discovery/admission and bounded transports
    adapters/      # raw schema → canonical records; explicit rendering
    transforms/    # normalization, extraction, filtering, annotations
    dedup/         # exact/near duplicates, lineage clusters
    exclusion/     # development matching + protected exclusion receipts
    pools/         # split/freeze and manifest query
    tokens/        # token shards, document offsets, byte spans
    sampling/      # exposure plans, deterministic quotas and packing
  tokenizers/      # baseline BPE, byte fixture, plugin capabilities
  models/          # reference Transformer and architecture registry
  objectives/      # CE and research objective plugins
  optimizers/      # optimizer registry, parameter groups, accounting
  training/        # loop, precision, gradient accumulation, schedules, resume
  evaluation/      # native likelihood, diagnostics, harness adapter, policies
  experiments/     # plans, resource caps, queue, immutable run snapshots
  analysis/        # paired statistics, curves, gates and comparison contracts
  research/        # idea cards, plugin scaffolding, prior-art records
  reporting/       # CSV/JSON/Markdown/HTML; optional dashboard
  inference/       # generation and native export
  security/        # final-evaluation authorization and release checks
```

Keep dependencies inward: CLI/dashboard → orchestration → public contracts and domain modules. Models do not import dataset providers, the CLI or benchmark tasks. The trainer does not know names of datasets or candidate architectures. Evaluation cannot depend on objective-specific training code. A research plugin receives only the information allowed by its track.

Use explicit registries such as `architecture`, `objective`, `optimizer`, `tokenizer`, `source_adapter`, `transform`, and `schedule`. Each entry declares an identifier/version, a strict configuration schema, capabilities and serializer version. Discover local plugins from an allowlisted package/entry-point group; do not fetch code at runtime.

## Artifacts and local storage

Use an environment-configurable `XLM_HOME` with sensible platform-local defaults. Paths must work on Windows and Linux; never hard-code a user’s drive or home directory. Store raw downloads, clean pools, token shards, runs and reports in separate directories. Store sealed evaluation outside this tree and outside the agent’s account.

Use Parquet for canonical document tables, compact binary arrays/memory maps for token IDs and offsets, JSON manifests, JSONL metric logs, and SQLite for the local run ledger. A successful artifact has a checksummed manifest and completion marker. Consumers reject partial artifacts. Filesystem outputs are authoritative; the SQLite catalog can be rebuilt. Use locking and transactions to prevent duplicate writers.

A logical artifact key includes input manifests, relevant resolved config, producer code digest, schema version, dependency/runtime fingerprint when it affects output, and seeds. The final manifest separately hashes output bytes. Cache reuse requires both valid inputs and verified checksums. Presentation changes need not invalidate a model; behavior changes must.

## Research ergonomics

The user must be able to compose one experiment from a model preset, mixture, objective, optimizer, tokenizer artifact, budget, precision profile, and evaluation policy. Overrides are typed and audited. Unknown keys are errors. Lists replace instead of invisibly concatenating. Cycles in configuration inheritance are rejected. Arrays of trial configurations are materialized before jobs start.

Use an initial single-GPU process lock. A CPU pipeline may run separately within its own resource cap. State changes are explicit: DRAFT → PLANNED → AUTHORIZED → RUNNING → SUCCEEDED / FAILED / INTERRUPTED / CANCELLED / BLOCKED. Promotion produces a draft experiment; it does not start expensive training.

## Minimal user interface

CLI is authoritative. Generate useful static HTML/Markdown reports first. An optional local read-only dashboard can show data inventories, jobs, learning curves, mixtures and comparisons. It must not become a second implementation of orchestration. Bind to loopback by default and never expose secrets or sealed results through convenience views.

## Not a framework for everything

Complete v1 means this local research workflow works end to end. Do not build Kubernetes, a cloud marketplace, a distributed parameter server, a general agent runtime, or a custom CUDA compiler before the system can overfit a tiny batch and resume correctly.
