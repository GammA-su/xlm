# Evaluation policy and holdout isolation

**C05 matcher v4 (2026-10-02):** `c05-matcher-v4` keeps the v3 normal signatures
unchanged. Only for an item with zero normal signatures, it adds one exact
whole-item fallback over a label-free composite of every textual alternative (floor
4 tokens / 16 characters / 3 distinct, no sliding windows). This mainly protects
against row/item-level leakage, not partial spans. Protected preparation refuses
while any item is unsigned. `c05-matcher-v3` keeps its historical meaning.
[Report](docs/implementation/reports/C05-MATCHER-V4.md).

**C05 engine continuation (2026-10-02):** authored fixtures now exercise immutable
benchmark matching, known-group propagation, crash recovery and kept-membership
gates. They are not protected benchmark evidence. Real preparation must still
occur under a versioned protected isolation mechanism with a trusted content-free
receipt: `separate_principal_v1` (the separate operator identity/machine below) or,
since 2026-10-02, `detached_volume_v1` (same Windows account; benchmark material,
protected index and C05 scratch on a dedicated volume mounted only for preparation
and the C05 run). The latter is *detached-volume operational isolation* against
accidental/process-level contamination, not adversarial security against the same
user, and it does not satisfy the **Isolated final** evaluation requirement below. Raw indexes, pattern digests and detailed exclusion decisions
remain protected; exported kept membership is frozen against the complete source
manifest before any query. The new completion/token-shard attestations do not
replace the official evaluation `FinalExclusionReceipt` gate. C05 engineering is
accepted (2026-10-02); protected execution still awaits the protected benchmark
receipt and operator decisions, and no protected schema-3 receipt exists. See the current
[C05 ledger](docs/implementation/reports/C05-GLOBAL-CONTAMINATION-PLAN.md).

**Official-claim binding (2026-10-02):** `verify_benchmark_claim` now accepts only a
protected schema-3 `FinalExclusionReceipt`. Its `c05_binding` names the global C05
completion (kept membership, seals, policy, index); its `selection_binding` names the
signed final freeze of the exact quota-selected training membership, tokenizer,
exact counts, quota table, recipe and exposure plan; `output_membership_digest` is
the selected membership. The independently supplied `BenchmarkClaimBinding` must
match selection, freeze, tokenizer, quota and source-seal digests as well as the
earlier fields. Schema-1/2 receipts (global or legacy membership) and authored
(development) chains cannot enable official claims. No protected schema-3 receipt
exists yet.

**P35 research selection:** [xlm-science-v1](docs/implementation/reports/P35-SCIENTIFIC-CONTRACT.md)
specifies fixed-distribution held-out text CE as the same-tokenizer 50M primary
endpoint, text BPB for tokenizer comparisons, and independent training-pair
uncertainty. The four-task index below remains a secondary capability report;
its scoring, coverage and holdout rules are unchanged. New cadence and statistical
integration are specified in the [handoff](docs/implementation/handoffs/P35-OPUS-HANDOFF.md),
not implemented by this documentation milestone.

## Core scores

Base models, zero-shot conditional likelihood, no chat template or benchmark fine-tuning. Core tasks: BLiMP macro accuracy; ARC-Easy `acc_norm`; HellaSwag `acc_norm`; PIQA `acc`. Save both accuracy metrics for the multiple-choice tasks. Pin task IDs, prompt templates, dataset revisions and scorer implementation. Inspect `acc_norm` in that revision; the inspected harness uses answer-string character length, not token count. Add a regression fixture that would expose a normalization change.

The internal aggregate is:

`S = 100 * mean_over_four_tasks((accuracy - chance) / (1 - chance))`

Use chance 0.5 for BLiMP and PIQA, 0.25 for four-choice HellaSwag, and mean `1/n_choices` for the actual ARC split. Macro-average BLiMP subdatasets before giving BLiMP one quarter of the suite weight. Do not clip negative scores. Do not form a full suite score when a required task/subdataset is missing; report partial coverage and component results instead.

Diagnostics: frozen English validation NLL, same-tokenizer perplexity, same-text bits per byte, per-domain losses, answer likelihood margins, and learning curves by tokens/bytes/compute. A diagnostic improvement is not a benchmark victory. An initialized untrained model establishes scoring biases; it need not equal uniform-choice chance.

## Feedback tiers

| Task | Search | Confirmation | Final |
|---|---|---|---|
| ARC-Easy | official train | official validation | official test |
| HellaSwag | grouped part of train | disjoint grouped part of train | official validation |
| PIQA | grouped part of train | disjoint grouped part of train | official validation |
| BLiMP | about 20% of whole subdatasets | about 20% of whole subdatasets | remaining about 60% |

These are project policies. All benchmark splits remain excluded from pretraining/tokenizer fitting. Freeze exact IDs, stratification/grouping and seeds before search. Keep obvious duplicate goals/source contexts together. Freeze whole BLiMP subdataset allocations; do not claim that this proves complete linguistic independence.

The harness default tasks may select the intended final split. Therefore `xlm evaluate --suite search` must use explicit validated task variants, never unexamined defaults. Network acquisition of official final data must be performed by the operator-side service, not by the coding agent’s ordinary development command.

A limit such as 100 examples is a smoke evaluation, not the official benchmark. Random chance references and suite weights are recomputed/validated for the selected items when diagnostics use subsets. Final reporting distinguishes the sealed BLiMP subset from full BLiMP, which is partly development-exposed. Never copy reference scores from a different protocol as locally reproduced results.

## Isolation that is real, not a YAML flag

There are two operating modes:

**Development-only:** all locally available evaluation examples are treated as exposed. CI uses artificial benchmark-shaped fixtures. The system cannot issue an unexposed-final claim.

**Isolated final:** evaluation data and label-bearing manifests live under a different OS identity or machine, outside the agent’s mounts and permissions. The agent cannot read official benchmark caches, final task definitions containing labels, raw decontamination indexes, or detailed exclusion matches. A trusted operator creates split/exclusion receipts and freezes a final-evaluation request for specified checkpoint hashes. The evaluator runs without arbitrary candidate code execution privileges over the holdout store.

A same-user process, hidden directory, encrypted file whose key the agent can read, or `sealed: true` does not provide that isolation. Mark the environment nonsealed until its permissions/mount checks and operator attestation pass. Public benchmark data are intrinsically accessible elsewhere: document this limitation and do not portray operational isolation as a proof that no pretrained agent has ever encountered them.

Decontamination is done in the isolated preparation environment when it requires final examples. It returns opaque receipt IDs and kept corpus membership, plus bounded aggregate counts. Do not give an adaptive arbitrary-text membership oracle to the idea generator; finalize/cap submitted corpus batches and audit queries. Hashes of public benchmark strings alone are not confidentiality protection.

Under `c04-benchmark-risk-v2`, acquisition admission (including legacy `clean`)
never authorizes an uncontaminated or official benchmark claim. The
`xlm.operator.final.verify_receipt` result separates evaluation receipt integrity
(`valid`) from `official_benchmark_claims_allowed`. The latter stays false unless
`xlm.data.exclusion.receipt.verify_benchmark_claim` verifies a protected, trusted
receipt against independently supplied frozen training/evaluation lineage:
checkpoint, suite, input corpus, exact selected training membership (schema 3,
with selection/freeze/tokenizer/quota/seal identities), policy and exclusion index.
That index/policy must cover all benchmark splits of the suite. Development
receipts, receipt IDs alone, `none_declared`, mismatched pools and acquisition
decisions cannot satisfy the gate. Success is `screened_with_limitations`, never
proof of zero contamination. The CLI currently supplies no frozen-pool receipt,
so its integrity check does not enable official claims. The Mix-01 freeze bridge
(`final-receipt --c05-proof --freeze`, `claim-binding`, `claim-check`) is implemented
and exercised only on generated authored chains; admission does not claim that
exclusion has run.

For final evaluation of custom code, export a reviewed scoring bundle, run it in a sandbox with read-only model artifacts, denied network, controlled outputs and a trusted scorer/adapter boundary. Merely placing code in another process is not a security guarantee. Where robust isolation is unavailable, run as development-exposed and say so rather than claiming a sealed result.

## Statistical output

Use paired per-item differences on aligned item IDs and cluster-aware bootstrap where applicable. BLiMP uncertainty should include subdataset-level variation. Separate evaluation-sample uncertainty from between-training-seed variability; show both. Three paired seeds are an initial final-stage target, not a guarantee of precise seed-variance estimates. Missing or mismatched item IDs invalidate paired comparisons.

Freeze CI method/seed/repetitions, minimum effect, regression limits and checkpoint selection before confirmation. Confidence intervals after adaptive development-set selection are decision support, not an unbiased final test. Keep every tried model, failed run and tuning attempt in the ledger. Bootstrap code must pass known synthetic cases, including identical predictions, swapped winner, clustered duplicates and zero-valid-task cases.

A final claim says exactly which checkpoint, size, budget, task protocol and comparator set were evaluated. State-of-the-art language is disallowed unless supported by a separately maintained, dated and appropriately scoped survey.
