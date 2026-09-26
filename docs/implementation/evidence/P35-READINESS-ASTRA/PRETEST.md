# ASTRA independent review — before tests and product edits

Starting HEAD: `176e351b37a84f323b8fe53c10fa2ea87e5251c2`, branch
`review/p35-readiness-astra`. Offline, authored fixtures only. Required contract,
handoff, M1–M5 reports, readiness report, science guide, AGENTS and implementation
STATUS read first. This is a source-derived assessment, **not runtime evidence**.
No certification tests or product edits preceded this document.

## Independent answers A–J

A. The intended policy is recoverable or fail-stop: initial/endpoint evaluations
have bounded same-state retries; interior LM events have exact checkpoints.
Durability is established before scoring. This does not prove every crash path.
B. Ordinary exceptions propagate out of execute_plan_run and fail the worker.
The outer queue trusts any successful worker result without independently checking
required completeness; it also loses the typed evaluation-incomplete classification.
C. Both train_step and the internal update path check the initial ledger; planned
but not due events are blockers. No first LR row should be committed on exhaustion.
D. Recovery checkpoints add the natural 1M/4M crossings. No update split is added.
Failure before publication stops; replay from older durable state may change the
lost lineage, which must never be represented as an evaluation of the old weights.
E. Search endpoint exhaustion raises RequiredEvaluationIncompleteError after the
endpoint checkpoint. FAILED is expected; structured completion is currently absent
across the subprocess boundary (SHOULD FIX BEFORE PILOT).
F. All non-COMPLETE due events, including PARTIAL, pin by model digest. Last-good
is independently protected. Retirement follows verified publication. Failed
retirement is logged and leaves extra bytes, so capacity is conditional on successful
retirement or a subsequent storage-cap fail-stop.
G. Five pre-endpoint scientific states can coexist (0,1M,4M,8M,16M), plus two
arbitrary rolling terminal states from interruptions: 7P. New serialization and
ArtifactStore staging contribute 2P: 9P. This is a nominal conservative bound,
not a universal bound under repeated deletion failures, orphan partials or explicit
protected fork references. Actual storage guards must fail closed in those cases.
H. The canonicalizer binds ordered complete sequences, tensor fields and provenance,
not just sampled examples. Grouping boundaries are removed. It proves input identity,
not equal stochastic operations, gradients or weights. Legal stock paths look sound;
arbitrary container equivalence and producer handoff deserve independent challenges.
I. Prepared-path canonicalization checks row count then hashes pending arrays,
not the actual returned microbatch values; detached/replaced tensors can therefore
evade the receipt. Missing/None conflation, duplicate compact string values,
post-data-commit receipt failures, historical chain/C consistency, and changed-policy
forks need adversarial checks. Evaluation's state guard omits the new payload chain.
These are potential pre-microbatch-study issues; receipt is disabled in the pilot.
Dropout is not bound by the payload and is not claimed to be.
J. v2 explicitly requires receipt version and head and compares both. v1 remains
historical. Evidence extraction's validation, rather than trusted supplied summaries,
is essential. Equal heads alone cannot establish the rest of the comparison contract.

## Event state machine (source-derived)

All retry maxima are three attempts per exact lineage, including interrupted attempts.
Exhaustion leaves failed/partial/interrupted status and complete=false, never COMPLETE.

| Event | First C / update | Checkpoint | Live retry | Exact rescore | Advance on failure |
|---|---:|---|---|---|---|
| quick@0 | 0 / 0 | pinned t0 | yes | LM available | no, initial barrier |
| full@0 | 0 / 0 | pinned t0 | yes | LM available | no, initial barrier |
| search@0 | 0 / 0 | pinned t0 | yes | no search rescore | no, initial barrier |
| quick@1M | 1,048,576 / 16 | evaluation_recovery | first attempt | LM | yes while exact state retained |
| quick@4M | 4,063,232 / 62 | evaluation_recovery | first attempt | LM | yes while exact state retained |
| quick@8M | 8,060,928 / 123 | pinned milestone | first attempt | LM | yes while exact state retained |
| quick@16M | 16,056,320 / 245 | pinned milestone | first attempt | LM | yes while exact state retained |
| quick@32M | 32,000,000 / 489 | pinned endpoint | yes | LM if attempts remain | no further training; fail completeness |
| full@32M | 32,000,000 / 489 | pinned endpoint | yes | LM if attempts remain | no further training; fail completeness |
| search@32M | 32,000,000 / 489 | pinned endpoint | yes | unavailable | no further training; fail completeness |

Interior failures do not immediately fail the job: exact state remains pinned,
and the endpoint rescore pass tries a remaining LM attempt. The endpoint pass
does not promise to consume all three attempts for an older event; unresolved
events fail-stop, allowing separately authorized exact rescore. State mutation
is stronger: RecoveryRequiredError, compromised Trainer, no checkpoint of corrupted
live state. Immutable started/outcome artifacts reconcile attempts after resume.

## Additional provisional findings and test obligations

* Input stat check occurs AFTER check_document_order opens readers and verifies
  the order; it also does not prevent subsequent resolution after a size blocker.
  SHOULD FIX BEFORE PILOT: move the stat gate ahead of these expensive operations.
* Queue success can race the final wall limit: AttemptClock.finish('succeeded')
  marks completed even when elapsed exceeds remaining. SHOULD FIX BEFORE PILOT
  if a bounded synthetic test confirms it. Retries otherwise share persisted time.
* Retry counter persistence, endpoint publication ordering, PARTIAL retention,
  shifted provenance, attention defaults, LR/chain alignment, chain/data alignment,
  and queue fail-stop need independent isolated A1–A8 checks.
* Capacity assertions require both nominal-count tests and distinction from
  storage failure fail-stop. No fabricated proof of universal 9P storage.
* All required local commands, runtime nodes, CUDA receipt measurement, input
  diagnostic and static checks are NOT RUN at this point.

Pre-test verdict: **SAFE AFTER SPECIFIC FIXES is a candidate, not certification**.
Resolve process-boundary classification, early input gating and wall-limit race;
verify locally; keep receipt opt-in unchanged pending measured cost and the
pre-study correctness findings. The real pilot remains blocked on its documented
real inputs, measured profile/capacity, plan binding and authorization.
