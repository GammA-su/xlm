# Stage 3 proposal — D03 mixture and registered-component execution

Status: **AWAITING SEPARATE APPROVAL**. No D03 product implementation is included
in D06. Overall platform acceptance remains **BLOCKED**. This plan preserves
CONTRACTS.md, EVALUATION_POLICY.md, the existing Trainer, artifact store, queue,
component registries, mixture scheduler/batcher, and D01/D06 identity guards.

## Focused adversarial reproduction

`test_d03_before.py` contains two required-behavior assertions and a positive
control, using authored text/token IDs and a tiny CPU model only:

1. A real verified token shard contains one authored long document. With the
   existing `PackingPolicy(max_document_tokens=4)`, observe the actual source
   cursor movement during one `_next_window` visit. It must draw at most four
   new tokens. This isolates the documented ignored cap without inventing a new
   configuration field or changing the scoring/target definition.
2. The public `xlm train` command selects the already registered
   `noop_objective@1` in an eight-target authored plan. Execution must reach that
   registered factory. The current direct path instead constructs the fixed CE
   schema; registry presence alone cannot make the public path executable.
   This is a control/interface test, not a novelty claim or a benchmark.
3. The same uncapped authored shard produces the requested 16 valid targets,
   showing that the domain stream remains usable.

Executed on Windows CPU against the settled D06 source on 2026-09-20:
**2 failed, 1 passed**, pytest exit **1**, 52.17 seconds (52.92 seconds enclosing
command; sampled process-tree RSS 685,633,536 bytes). The cap case observed
17 tokens drawn despite a declared cap of 4. The public command reached the
frozen worker, then failed in `CrossEntropyObjectiveConfig` because it required
`cross_entropy` instead of the selected registered `noop_objective`. It published
no checkpoint. The positive control yielded exactly 16 valid authored targets.
The source hashes before/after match. This records defects, not D03 acceptance.

Exact command from `D:\Project\xlm` with the existing offline CPU/eval environment
and `PYTHONPATH=D:\Project\xlm\src`:

```powershell
uv run --offline --locked --extra cpu --extra eval python data/audit/p23-remediation/stage03/run_before.py
```

The retained runner records the full inner pytest command, environment flags,
source hashes, exit code and resource observations in `results.json`. Evidence:
`test_d03_before.py`, `before.log`, `before.xml`, and the two `observed.json` files.
These originals must be preserved during any subsequently approved repair.

No assertion is marked xfail and no fixture-only success is called production
execution. The failed public command is bounded to eight targets/ten training
seconds, with a 180-second outer subprocess limit and offline-only environment.

## Implementation stages within D03

1. **Resolve existing declared inputs into executable bindings.** Extend the
   existing training input resolver and versioned execution envelope to normalize
   the current `mixture_preset`/`mixture_details`, pool, tokenizer, exposure and
   packing declarations. Resolve actual per-source shards through existing
   manifests and `ArtifactPaths`, verify bytes, tokenizer/pool identities and
   admission requirements, and bind all sources and policies in D06. Keep single
   shards and authored token lists working. Missing or unsupported inputs remain
   explicit blockers; never silently substitute, drop a field, or renormalize.

2. **Share component reconstruction through existing registries.** Factor the
   repeated direct/queue/resume setup into one small typed construction seam in
   the existing training package. It creates the registered model, objective,
   optimizer, schedule, tokenizer, and existing single-source or `MixtureBatcher`
   input. It does not introduce a training loop. Use the existing reviewed plugin
   loader and file-scope/capability checks; capture selected plugin bytes and
   serializer versions. YAML stays data-only. A fresh frozen worker reconstructs
   exactly the selected components, including trainable auxiliary state.

3. **Enforce document/packing limits without target loss.** Apply the existing
   per-visit document cap at the shard/document cursor boundary, preserve real
   document/source/lineage IDs and byte spans, and carry the continuation context
   and remaining document offset in checkpoint state. Split visits according to
   the declared cap; do not truncate useful text. Preserve separate causal-stream
   and isolated-document semantics and exact BOS/EOS/padding accounting. Refuse
   unsupported policy combinations. If the existing contracts cannot express a
   required policy, present that specific conflict for review rather than adding
   an unvalidated field or silently redefining the policy.

4. **Restore the complete data/component state.** Reuse `CheckpointManager` to
   restore objective auxiliaries, optimizer and schedule state, data cursors,
   source deficits, exposure counters, RNG, and partial packing. Version genuinely
   changed state formats explicitly. A changed mixture, tokenizer, component,
   packing policy, or exposure plan requires a new envelope/fork and authority.
   Historical unresolved states are not upgraded by assertion.

5. **Make matched document/byte execution explicit.** Connect the existing
   exposure-plan machinery to actual document/span selection in the same batcher.
   Test two differently tokenized authored inputs against the same frozen text
   exposure, retaining comparable raw context and exact target/byte counters.
   A planning object alone is not executed equivalence. Unsupported tokenizer
   offset/capability combinations must remain explicitly unsupported; distinguish
   any such missing implementation from external validation awaiting the operator.

6. **Exercise the public chain and preserve authority.** Use authored local
   sources through YAML → prepare/plan → direct and queued training → checkpoint
   → fresh-process resume → native diagnostic evaluation/export. Keep production
   admission/profile/authorization prerequisites and direct smoke caps. Inspect
   production-size configurations with shape/meta checks only. No full numerical
   profile, campaign, live corpus acquisition, official benchmark, or remote push.

## Required acceptance evidence

- Two distinct authored sources produce observable source/target/document traces
  and the frozen token-share/drift accounting. Changing weights changes actual
  exposure; restoring a run restores its original mixture/data state.
- In each component category, an explicitly registered authored component is
  actually called through the public path and reconstructed in a fresh worker.
  Use observable behavior or component-specific state; no-op parity alone does
  not prove invocation. A trainable auxiliary must update and resume correctly.
- An uninterrupted run and interrupted/fresh-process continuation agree on
  computational state, next data IDs and masks, and exact nonmultiple final
  target budget. D06 provenance binds all selected sources/components.
- Oversized documents are split under the declared cap or explicitly rejected;
  no lost/duplicated targets. Exhausted sources, incompatible tokenizer hashes,
  unsupported fields, missing artifacts and changed policies refuse correctly.
- Matched document/byte execution on differently tokenized fixtures has real
  exposure evidence; unsupported cases remain explicitly identified.
- Existing preparation, mixture, packing, registry, trainer, checkpoint, CLI,
  authorization, D01 conflict, and D06 frozen-worker regressions continue to pass.
  Run uv offline with locked dependencies; preserve before failures and record
  exact commands, exits, source/import locations and resource limits.

Deliver an updated defect → implementation → maintained test → result map, user
commands, and distinct **IMPLEMENTED/VERIFIED offline** versus **NOT RUN — OPERATOR
ACTION REQUIRED** statuses. Full platform acceptance and the final full offline
rerun remain gated on the remaining separately approved stages.

**Approval requested:** implement D03 under this focused plan, with bounded
offline fixtures and tiny CPU runs only. D06 approval does not authorize it.
