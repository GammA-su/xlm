# Global C05 allocation and resource continuation (2026-10-02, after Astra)

**Both handed-off engineering boundaries are implemented and pass authored and
generated tests: hard aggregate storage admission, and the exact final allocation
chain up to a schema-3 official-claim receipt. Protected execution is still closed in
code.** This session tried to clear `ENGINEERING_BLOCKERS` (and so open
`require_engine_acceptance`); the session's permission policy refused that edit.
The list and the guard are unchanged, and their text now describes work that this
continuation implemented. Opening the protected path is an explicit maintainer
decision, not something this report claims. No executable protected plan exists.

### Recovery

Start: `F:\Project\xlm-c05-global`, branch `feat/c05-global-preparation`, clean, HEAD
`42c133ba317582deec75351bee08472eab9d172c` ("feat: extend C05 control plane and
downstream proof transport"). It is in `feat/c05-global-preparation` and
`rescue/c05-post-astra-shutdown`, both also on `origin`. Ancestry: 42c133b →
a99b084 → 3c8deaf → 737708e. No uncommitted or later post-Astra work existed. No
reset, rebase, squash, clean or push was performed. The pre-change focused C05
selection passed 97 tests (1 pilot deselected), exit 0, 23 s.

Commit 42c133b did not update this report; the section below about it was written
by inspecting its code and `control-plane/` evidence. That evidence is preserved
unchanged, including its failures: `control-feedback` exit 1 (review-queue test,
fixed before the commit), and `related-serial` exit 1 with three failures caused by
long Windows pytest temp paths (`FileNotFoundError` under deep `pytest-246` paths).
Generated pilots (control plane, authored mode): 1,105 docs 3.87 s; 10,105 docs
44.37 s, 75,714,560 B SQLite, 86,745,088 B sampled RSS; 50,105 docs 319.10 s,
375,427,072 B, 88,014,848 B; 100,105 docs 697.07 s, 751,505,408 B, 89,874,432 B; each
kept all clean rows and excluded 105. These are generated-pilot measurements, not
production performance.

Verified from code and tests at 42c133b: write-once signed lineage/resource/policy
decisions bound to the input-manifest digest (`control.py`); sequenced plan
allocation under a file lock; plan-digest-bound authorization; `run`, `resume`,
`status`, `resume-check` (read-only), `verify`; a `HEURISTIC_REVIEW_ONLY` bounded
token-overlap review queue that never excludes (`review.py`); matcher/renderer v3
and known-lineage v3 policy identities; cumulative signed retry/byte reservations;
UTF-8 byte-count verification; downstream proof specs (`transport.py`) for tokenizer
fit, exact counting, tokenization, parallel tokenization, mixture planning and
training input; a schema-2 `FinalExclusionReceipt` bridge; and a quota report that
never turns missing counts into sufficiency.

### Blocker A — hard aggregate storage (`capacity.py`)

Every file a run can create has a plan-fixed upper bound:

| Component | Bound | Kind |
|---|---|---|
| `facts.sqlite` (signatures, facts, band postings, union/group state, review queue) | `index_bytes` rounded to 4 KiB pages | HARD (`max_page_count`, page size pinned at 4,096) |
| `facts.sqlite-journal` | `H + pages × (4104 + 2H)` with measured header `H` | DERIVED (DELETE mode journals each original page once, plus one header and at most one header of alignment per sync) |
| `decisions.jsonl` (private) | `decision_bytes` (new field) | HARD (counted before write) |
| staged/published `membership.jsonl` | `output_bytes`, counted once (same-directory rename) | HARD |
| `completion.json` + `.tmp`, `state.json` + `.tmp` | 2 × 4 MiB, 2 × 256 KiB | HARD (checked before write) |
| lock, benchmark index, per-file allocation slack | 4 KiB, `benchmark_bytes`, 10 × 2 MiB | reserved / fixed input |
| other processes' disk use, process-tree RSS | — | MONITORED (sampled every 250 ms / 50 ms) |

C05 has no source or membership staging beyond the above: inputs are read in place,
and membership staging is the `.partial` directory. `plan` measures the scratch
volume's journal geometry (`StorageGeometry`, bound into the plan) and
`ExecutionPlan.identity()` refuses when `journal_bytes` is below the derived journal
bound or `scratch_bytes` is below the worst-case sum. Nothing widens a ceiling; a
larger ceiling is a new plan digest needing new authorization. Before `run`/`resume`
creates any file, the geometry is re-measured (drift refuses), unaccounted entries
refuse (WAL/SHM/foreign files), and each volume must hold `Σ(bound − present)` plus
`free_bytes`. Present bytes (a hot journal, partial staging) count as allocated.
The same physical-reserve check is sampled during the run, so another process
consuming space refuses before this job could exhaust the volume. Signed state keeps
`admissions`, `peak_aggregate_sampled` and `peak_journal_sampled` as maxima across
attempts; completion reports them. Spent time, rows, comparisons and bytes read are
still reserved before work and never reset.

Measured geometry: SQLite 3.53.1, 512 B journal header, 4,104 B record, on both C:
and G:. With the proposed defaults (now internally consistent: 128 GiB database,
161 GiB journal, 16 GiB decisions, 32 GiB output, 352 GiB scratch) the worst case is
363,223,060,992 B, of which 172,067,127,808 B is the conservative journal bound.
The old defaults (32 GiB journal, 192 GiB scratch) fail this admission. Proposed
values are not an operator decision. Limits: the journal bound is conservative
(about 1.25× the database); the per-file slack assumes clusters of at most 2 MiB;
free-space consumption by other processes is sampled; Windows directory-entry power-
loss durability is not certified. Every run now measures geometry with fsyncs; the
focused selection went from 23 s to about 45 s.

### Blocker B — exact allocation and selected-training-membership

`selection.py`, `freeze.py`, `bridge.py` and schema 3 of `FinalExclusionReceipt`
implement the chain: C05 kept membership → tokenizer identity → exact counts →
frozen quotas → per-allocation selection → selected-training-membership → signed
selected pool → final freeze → training data block → training-input verifier →
schema-3 claim receipt.

* **Tokenizer identity:** fingerprint plus SHA-256 of every artifact file. A fitted
  BPE must carry `c05-binding.json` naming this plan/completion (required in
  protected mode; a mismatching binding refuses in any mode). Vocabulary must equal
  the quota table's `tokenizer_vocab_size`.
* **Exact counts** (`count-tokens`): re-reads the plan's exact input files (SHA-256,
  size and row count must match), counts only kept `train` records whose full
  canonical record digest and allocation match the completion, under
  `c05-valid-targets-v1`, the same rule the token shard writer uses. Repeats refuse;
  every kept training record must be counted. The output is sorted by doc ID,
  byte-capped, SHA-256-bound and signed.
* **Frozen quotas:** `frozen_requirements` re-derives 17 allocations from the quota
  file bound by every sealed source, the IFM view split and the Common Pile
  component split. It now reads the total from the table's own
  `final_valid_targets`; the real table still says 6,000,000,000 and its SHA-256 is
  bound by the sealed sources. Quotas and membership are unchanged.
* **Selection** (`select`, policy `c05-quota-selection-v1`, seed 20260919): per
  allocation, eligible records are kept `train` records with positive valid targets,
  ordered by `sha256(seed, allocation, doc_id, content)` and then doc ID (an
  index-ordered SQLite scan, no in-memory sort). Whole records are taken until the
  quota; the crossing record keeps exactly the remaining valid targets as a token
  prefix. Each allocation therefore lands exactly on its quota (`EXACT`) or is a
  `DEFICIT`. Any deficit publishes nothing, writes a content-free deficit report and
  exits 2: no substitution, renormalization, repetition or top-up.
  Every count row is re-checked against C05 membership, so even a trusted-signer
  artifact naming an excluded, uncovered or reallocated record refuses.
* **Selected-training-membership:** `selected.jsonl` (doc ID, content digest,
  allocation, counted and selected valid targets) plus a signed `selection.json`
  (`c05_selected_pool_v1`) binding mode, plan, completion, input manifest, kept and
  selected membership SHA-256s, source seals, counts digest, tokenizer, count rule,
  quota/IFM/Common Pile/requirements digests, policy and per-allocation and
  per-component totals.
* **IFM / Common Pile:** general and planning, and the six Common Pile upstreams,
  are separate allocations with their frozen final quotas. The component shard holds
  exactly their sum and the exposure plan consumes it once, so the internal
  allocation consumed equals the frozen one exactly.
* **Final freeze** (`tokenize-selection`, `freeze`): one shard per logical component,
  written from the C05 inputs filtered to the selection. Each offset carries the
  content digest, completion, selection digest and counted/selected valid targets;
  the writer recounts and refuses drift. Records are labelled with the mixture
  component; the canonical source stays in `c05_canonical_source_id`. The freeze
  verifies every shard's bytes and offsets, refuses missing, extra or swapped
  shards, builds the recipe from unchanged quota shares (refusing shares that don't
  apportion exactly), and compiles an exposure plan that must use exactly each shard
  (no repetition, no remainder). It signs `c05_mix01_freeze_v1` and writes the
  bounded training-data block.
* **Training input:** `resolve_training_input` verifies a `c05_freeze` against the
  proof (signature, selection, every shard's bytes, recipe, exposure plan, source
  paths). A `Mix-01*` mixture without a freeze now refuses: kept membership alone is
  insufficient. The existing 2 GiB bounded input contract is unchanged; the real 6B
  payload (~12 GB uint16) cannot pass it, and no unbounded path was added.
* **Mode:** authored chains run the identical code. Gates and the exposure planner
  accept authored artifacts only through an explicit rehearsal flag, never under a
  `Mix-01*` id. Artifacts record the mode, and Mix-01 training and official claims
  refuse authored ones.
* **Top-up:** any new or changed record changes file identity, so the input
  manifest, plan and completion change too. Counting refuses changed inputs, the old
  completion does not verify for the new plan, and the old selection and freeze do
  not verify against the new gate. Renewed global C05 is required.

### Downstream proof transport audit

| Path | Proof checked against actual bytes |
|---|---|
| Tokenizer fit (`tokenizer train --c05-proof`) | each fit record through the membership gate; `c05-binding.json` published (protected proofs only) |
| Exact count (`count-tokens`) | input file hashes and counts; full-record digest per kept record |
| Tokenization (`tokenize-selection`, `TokenShardWriter`) | gate per record; selection and recount per record; signed attestation |
| Parallel tokenization / assembly | per-lane gate and selection (`--c05-selection`); assembled shard re-verified and re-attested |
| Mixture / exposure plan | every shard verified, no repeats across shards; binding in plan ID |
| Final freeze | every component shard's bytes, offsets, counts and selection; recipe; exposure |
| Training data / input resolution | `verify_training_freeze` re-verifies the freeze and every shard |
| Frozen execution (`resolve_execution_config`) | exposure plan recompiled through the gate (rehearsal only for non-Mix-01 authored freezes) |
| Official claim | schema-3 receipt plus independently derived claim binding |

Remaining gaps: `tokenizer train --c05-proof` and `quota-report` still accept only
protected proofs, so the generated rehearsal fits its tokenizer fixture through the
same library function. `tokenizer count-exact` remains a totals-only report and
`count-tokens` is the per-record allocation input.

### Official receipt bridge

`final-receipt --c05-proof --freeze` issues schema 3: `c05_binding` (global
completion, kept membership, seals, policy, index, review decisions) plus
`selection_binding` (selection, selected membership, freeze, tokenizer fingerprint
and files, counts, quota, requirements, recipe, exposure plan, selected valid
targets), with `output_membership_digest` set to the selected membership.
`verify_benchmark_claim` requires schema 3 and compares the selection, freeze,
tokenizer, quota and source-seal digests in an independently derived
`BenchmarkClaimBinding` (`claim-binding`). Tests refuse each of: the same global C05
with a changed selection; a changed tokenizer, quota, selected record or source
seal; v1/v2 (global) receipts; and development receipts. A changed token count is
refused at tokenization (recount drift) and yields a different selection digest.
A top-up refuses until a renewed C05. Legacy receipt tests were updated so their
acceptance cases use schema 3 and they assert that legacy and global receipts are
refused.

### Production-faithful synthetic completion

`scripts/c05_synthetic_flow.py` (generated corpus; synthetic environment trust key;
authored mode) ran: input manifest → three signed decisions → policy freeze → plan
→ authorize → run interrupted inside grouping → `resume-check` → `resume` → `verify`
→ proof spec → tokenizer fixture (BPE, vocabulary 320, fitted through the gate on
kept train records) → `count-tokens` → `select` → `tokenize-selection` → `freeze` →
`resolve_training_input` → `final-receipt` (schema 3) → `claim-binding` →
`claim-check`. All steps after the tokenizer go through the operator CLI. Result:
383 documents, 349 kept, 17 excluded, 17 duplicates (exactly the planted oracle); 2
storage admissions; 17 allocations, all `EXACT`, 10,000 selected valid targets over
34 records (17 truncated crossing records); the receipt is schema 3 / development;
the official claim check refuses (exit 1) as required for an authored chain.
See `allocation-resources/synthetic-flow.*`. This is not real-data evidence.

### Validation (this continuation)

Environment: Python 3.12.13, Windows-11-10.0.26200, existing CPU/eval environment
(`UV_PROJECT_ENVIRONMENT=F:/Project/xlm-common-pile/.venv`, `PYTHONPATH` = this
checkout's `src`), `uv run --offline --locked --no-sync`, HF/Transformers/uv offline,
one BLAS thread per worker, tokenizer parallelism off. No install or lock change.
Exact arguments, exits, wall time and sampled process-tree RSS are in
`allocation-resources/*.json`, with LF-normalized logs. Selections overlap; do not add
their counts together.

| Run | Result |
|---|---|
| Baseline focused C05 selection before changes | 97 passed, 1 deselected, exit 0 |
| New `test_c05_capacity.py` (aggregate admission, insufficient disk, mid-run exhaustion and resume, journal growth, hot-journal crash, WAL refusal, publication-crash overlap, hard page cap and no widening, readiness categories) | 9 passed |
| New `test_c05_selection.py` (end-to-end oracle, determinism/digest stability, IFM/Common Pile exactness, excluded/reallocated/uncovered refusal, count drift, tokenizer binding, pool/freeze tampering, missing/swapped shards, training freeze, deficit, top-up, schema-3 claims, global receipt refusal, parallel transport, frozen-execution dry run) | 15 passed |
| `related-parallel` (Astra's 32 files + new suites + operator + Essential-Web bulk; `not serial and not optional_dependency`, 16 workers) | 567 passed, exit 0, 37.4 s |
| `related-serial` complement (`serial or optional_dependency`, `-n 0`, `--basetemp=C:/t/c05s`) | 19 passed, 568 deselected, exit 0, 623 s; includes Astra's three long-path failures, which pass with the short temp root |
| `focused-final` (10 files, after the last test edits) | 225 passed, exit 0 |
| `authored-pilot` (`test_bounded_authored_engine_pilot`) | 1 passed; 300 kept / 20 duplicates / 110 excluded, 11.8 s (Astra: 8.1 s) |
| `synthetic-flow` | exit 0, 8.4 s, summary in `synthetic-flow-summary.json` |
| Ruff format `--check`, Ruff check, strict mypy (27 source files + scripts via imports) | exit 0 / 0 / 0 |

Retained failures: `mypy-strict-01` (exit 1, eight typing findings, fixed without
suppressions) and `ruff-check-01` (exit 1, import order in a late test, fixed).
During development: one fixture test needed an updated regex because admission now
refuses an orphan staging entry before work starts (same refusal, earlier). Three
legacy claim tests and one downstream test were changed to the stricter contract,
and they now assert that v1/v2 receipts and kept-only Mix-01 training are refused.
A first rehearsal planted no duplicates (an indexing slip in the generator; fixed).
The full repository acceptance selection was NOT RUN. Real C05, live benchmarks and
CUDA were NOT RUN.

### Real metadata-only preflight

`preparation verify`: exit 0, manifest `11724d92…84152` unchanged (2,035 files'
metadata/sizes). `preparation preflight`: exit 2 (audit with new
`blocker_categories`). `operator plan-readiness`: exit 2. Its engineering blockers
are the unchanged `ENGINEERING_BLOCKERS` strings. Operator decisions missing:
Gutenberg lineage, reviewed resources. Evidence missing: protected benchmark
preparation receipt (`G:/XLM/c05` does not exist). Engineering checks: real quota
table → 17 allocations, 6,000,000,000 targets, quota `9b16db76…`, IFM split
`e001aff4…`, Common Pile split `d64b2b6e…`; proposed resources admit at
363,223,060,992 B. The probe directory was removed afterwards.

| Requirement | Status |
|---|---|
| Recovery of 42c133b, history preserved | VERIFIED |
| Hard aggregate storage admission, crash/journal/publication accounting | IMPLEMENTED / VERIFIED authored scope |
| Exact counts, deterministic quota selection, selected membership, signed pool | IMPLEMENTED / VERIFIED generated scope |
| IFM and Common Pile frozen internal allocations | IMPLEMENTED / VERIFIED generated scope |
| Final freeze, training-input verifier, parallel transport | IMPLEMENTED / VERIFIED generated scope |
| Schema-3 official claim bridge | IMPLEMENTED / VERIFIED synthetic trust |
| Protected execution gate (`ENGINEERING_BLOCKERS`) | BLOCKED: unchanged; needs maintainer decision |
| Protected benchmark receipt, Gutenberg decision, resource decision | BLOCKED: operator evidence/decisions |
| Bounded training path for the real 6B payload | OUT OF SCOPE / NOT IMPLEMENTED (2 GiB contract kept) |
| Real C05 scan, production tokenizer, final 6B freeze, training, network, push | NOT RUN |

Next command after maintainer and operator inputs: the runbook's readiness check,
then the decision `record`/`freeze` commands, `plan --mode protected`, `authorize`,
`run`, `verify` and the allocation chain.

# Global C05 engine continuation (2026-10-02)

**The engine has substantial authored validation, but production acceptance is
incomplete. No executable protected C05 plan exists.** Protected plan construction
and execution refuse in code; a benchmark receipt alone does not remove that
refusal. This continuation must not be reported as engine-ready or authorized.

Starting HEAD: `3c8deaf83e73767473f1c9df9a6278917c112f11`, clean branch
`feat/c05-global-preparation`, `F:\Project\xlm-c05-global`. The final commit is
reported in the chat handoff; it contains this report without rewriting history.
This continuation's exact commit manifest is
[engine-v2/changed-files.txt](../evidence/C05-GLOBAL-CONTAMINATION-PLAN/engine-v2/changed-files.txt).

All work remained offline. No full C05 scan, real benchmark material/index access,
source-pool mutation, final mixture freeze, production tokenizer/model training,
CUDA, upload or push occurred. Tiny tokenizer/trainer operations inside authored
regression tests are not production training. Acquisitions in tests use loopback
fixtures. The unrelated worktrees were not edited.

## What changed and what the checks establish

`streaming.py` implements a token Aho-Corasick automaton bounded by pattern/node
ceilings, with resource callbacks during construction and failure-link traversal.
Its provenance map is immutable and retains duplicate benchmark references. Every
scan uses the same frozen index; corpus repetition never removes signatures.
Authored tests detect 1/1, 4/4 and 100/100 repeated prompts, with normalized case,
punctuation, token boundaries, embedded prompts and reordered options.

The legacy development matcher also no longer suppresses spans using corpus
frequency or materializes the entire input iterator. Its version advances to 2;
historical frequency settings remain identity fields but cannot suppress evidence.
An independently frozen background-phrase list now supplies the common-phrase
negative control. That interface remains a development report API; the new runner
uses the immutable streaming matcher.

Draft matcher policy, explicitly serialized and digest-bound: prompt minimum
4 tokens / 16 characters / 3 distinct tokens; BLiMP sentence minimum 3 / 12 / 3;
answer and combined-render minimum 8 / 40 / 5. Long variants use 13-token spans,
stride 6, at most 32 spans/variant, 64 variants/item and 65,536 B/variant. All ARC
choices, both PIQA solutions, both BLiMP sentences and HellaSwag context/endings
participate without selecting a gold answer. These thresholds passed authored
controls; they are not measured population false-positive rates or operator-frozen
production choices. Items lacking any signature block protected receipt admission.

The automatic guarantee is exact normalized token-boundary containment of an
actually compiled signature, followed by propagation over known groups. It is not
universal full-example, paraphrase, semantic or unseen-lineage coverage. Truncation,
informativeness floors and a reviewed background policy are explicit limitations.
Fuzzy automatic deletion is forbidden. A bounded fuzzy review queue remains
unimplemented; its resource field is a proposed ceiling, not proof of a detector.

`disk.py` stores document facts, signatures, band postings, all aliases and union-find
parents in SQLite. MinHash retains 5-word shingles, 128 permutations, 32 bands,
seed 20260919 and estimated-Jaccard threshold 0.8; candidate and bucket limits are
64 and 256. Skipped oversized bands and candidate-cap documents are counted.
Shingle hashing is reduced in chunks of 2,048 to bound the existing vectorized
permutations-by-shingles temporary. Cross-source exact and near matching, threshold
boundaries and survivor ordering have authored checks. Survivors are longest text,
then smallest source ID, then document ID. Near matching remains a capped heuristic.

Every document is benchmark-screened before survivor publication. Duplicate and
known-lineage unions then propagate any direct hit to the whole known group,
including a would-be survivor. Group-safe diagnostic, nested quick and audit
assignment follows exclusion; it does not depend on mixture weights. Known lineage
v2 uses every explicit parent and recognized metadata relation, plus SYNTH
`query_seed_url` and `additional_seed_url`. URL keys may bridge sources. No URL or
parent means no invented relationship. Gutenberg rows receive no fabricated book
ID or shard-wide grouping. Whole-book policy refuses rows without real book IDs;
the alternative known-group treatment still requires operator review.

## Artifacts, recovery and downstream checks

`artifacts.py` defines strict extra-field-rejecting preparation, file, policy,
resource and plan schemas. HMAC-SHA256 envelopes bind canonical payload digests to
explicit trusted issuers. Authored and protected modes are distinct. The protected
builder checks the separate operator principal and declared isolation attestation,
and actual local source/dependency identities. This is operator attestation, not
automatic proof that OS access controls were correctly configured.

The content-free preparation receipt binds repository/revision/config/split/file
hashes and sizes, item/duplicate/render/pattern counts, empty-signature count,
publisher inventory digest and all-published-coverage review, policy/index identity,
code/dependencies, harness version, isolation and issuer. No counts/configurations
were invented for real benchmarks. The local builder currently supports reviewed
JSONL material; unknown publisher formats/conversion provenance still need review.
It never invokes dataset remote code, acquires payloads or accepts licenses.
Machine-readable schemas are in
[artifact-schemas.json](../evidence/C05-GLOBAL-CONTAMINATION-PLAN/engine-v2/artifact-schemas.json).

The runner scans one bounded JSONL record at a time, verifies complete file hashes
and exact row/text-byte counters, and commits each file transaction with a signed
fact/posting/lineage digest. Resume rehashes reusable source files and verifies those
signed facts. Grouping has a signed digest and an all-or-nothing transaction.
The plan lock, benchmark index identity, code/dependency identity and signed journal
must agree. Interrupted files restart; completed files reuse verified facts.
Row/comparison reservations are persisted before work in blocks of up to 1,024;
unused credits are conservatively spent after a crash. Stage and overall deadlines
survive restart. Tests use both raised interruptions and actual `os._exit(29)` at
row, file-commit, grouping and pre-publication boundaries.

Private scratch retains `facts.sqlite` and `decisions.jsonl`; detailed match keys
and exclusion decisions stay private. Publication exports **only kept rows** in
sorted `membership.jsonl` and a signed `completion.json` with aggregate counts,
input/plan/index-receipt/policy bindings and membership SHA-256. Component, view and
Common Pile upstream allocation identifiers survive. Files are fsynced before a
directory rename publishes completion. Windows directory-entry persistence after
power loss is not certified. A crash before publication has no completed artifact.

`MembershipGate` validates the trusted completion, exact current input-manifest
digest and membership bytes, then uses a bounded disk lookup. Whole canonical
record identity and train membership must match; excluded, changed, renamed,
new/top-up and repeated rows fail. Tokenizer-fit selection, BPE fitting and
TokenShardWriter use this gate; baseline first-pass source IDs cannot silently
take the development route. Exact token counting also requires a protected gate.
Token shards carry signed attestations binding current C05 receipt, shard manifest
and counters, plus per-offset original-record hashes. Exposure and matched-plan
compilation refuse unverified baseline/Mix-01 availability. Authored trust fixtures
exercise acceptance/refusal; they are not real protected receipts.

The complete operator CLI, parallel tokenization proof transport, official final
receipt bridge and final component/IFM/Common Pile quota workflow remain incomplete.
The low-level gates do not certify those missing paths. Top-ups still require a
new reviewed acquisition and global re-screening; changed group bridges invalidate
affected membership/splits. No substitution, repetition, renormalization or quota
amendment was performed.

## Resources, evidence and current refusal

`storage.py` now creates large indexes on empty tables and maintains them during
insertion. Read queries must have index-backed order: a query plan requiring a
temporary B-tree or automatic index is refused. TEMP is memory-only as defense in
depth, automatic indexes and mmap are disabled, and page caches are 8 MiB. This
avoids reliance on hidden SQLite external sort files without changing a process-
global temporary-directory setting. Database page ceilings, rollback journals,
private ledger, staged/final publication overlap and the benchmark index contribute
to resource checks. Maximum-size and full-population resource certification is
still incomplete; the protected path remains disabled.

Draft defaults are 24 GiB process-tree RAM, 192 GiB aggregate scratch, 128 GiB
database, 32 GiB journal, 32 GiB output and 32 GiB free-space reserve; 64 MiB record,
2M normalized tokens/record, 16M unique rows, 32M attempted-row reservations, 4,096
files, 1B comparisons, 2 GiB benchmark index, 2M patterns and 8M automaton nodes.
One worker, 86,400 s/stage and 259,200 s overall. These are proposed configuration,
not frozen real-plan limits or guaranteed feasibility. RSS is sampled at 50 ms,
disk at 250 ms and explicit boundaries; sampled peaks can miss short allocations.

The authored 430-document pilot has 300 independent clean rows, 20 duplicate
aliases, 100 direct hits and 10 linked derivatives. Expected outcome: 300 kept,
20 duplicates, 110 excluded. Its first measurement was 70.101 s; replacing costly
per-operation process-tree enumeration with 50 ms sampling reduced this to 9.129 s,
87,384,064 B sampled runner RSS and 3,590,221 B then-accounted scratch. Both passed;
the later index-order/privacy/aggregate-accounting changes passed the final pilot
in 8.132 s (12.015 s command wrapper), 87,134,208 B sampled runner RSS,
3,664,461 B sampled aggregate scratch and 100,120 B exported kept membership.
The wrapper sampled 111,525,888 B process-tree RSS. This is authored evidence, not live-source or
104.5 GB performance certification.

The source inventory reverified unchanged, exit 0: digest
`11724d92c011dd01e8e8c3ab944ac2adeef76aa8ff4abc921bf882eb0ac84152`.
All ten seals, eleven components and 2,035 file sizes remain bound. No 104.5 GB
content rehash was performed. The four benchmark pins and source quotas remain
unchanged. Only the proposed content-free receipt path
`G:/XLM/c05/benchmark-preparation.receipt.json` was checked for existence (absent);
real material availability elsewhere remains unknown. No protected cache was opened.

Evidence under [engine-v2](../evidence/C05-GLOBAL-CONTAMINATION-PLAN/engine-v2/)
contains exact argument arrays, exits, environment, logs, wall times and sampled
process-tree RSS. Validation selections overlap and must not be added as unique
tests. The broad related selection passed 404 tests; its complementary serial
selection passed 4 (405 deselected), including installed-harness and process/CLI
regressions. Subsequent focused regressions cover publication/privacy/storage
changes: the final focused selection passed 94 tests (one separately run pilot
deselected), exit 0, 11.453 s wrapper and 188,481,536 B sampled process-tree RSS.
Strict mypy, Ruff check and format passed over all 20 touched Python files.
Exact commands, totals and static results are in `validation-summary.json`.
Complete repository acceptance, real C05, live benchmarks and CUDA were NOT RUN.

Failures are retained: an initial test selection named nonexistent
`test_operator_final.py` and ran no tests (exit 5); the corrected list initially
used the wrong optional marker and was refused before execution (exit 2).
The correct marker is `optional_dependency`; the affected tests then ran serially,
not omitted. Initial Ruff formatting findings and strict-mypy reader/writer variable
reuse and fixture-inference errors were fixed without suppressions. One editing
command omitted the shared-environment setting and uv created an ignored local
`.venv`; no dependency sync/install occurred. All validation reused the existing
CPU/eval environment. No failure was called a flake or converted to a pass.
The final diff check also exposed CRLF as trailing whitespace under this repository's
Git settings; touched text files were normalized to LF without changing JSON values
or canonical artifact digests. The failed check is retained, followed by the clean
check; no whitespace rule was disabled.

| Requirement | Current status |
|---|---|
| Frozen input/seal/size reproduction | VERIFIED metadata-only, unchanged |
| Immutable task-aware matcher and repeated-copy repair | IMPLEMENTED / VERIFIED authored scope |
| Disk facts/union-find, capped near dedup, alias/survivor ordering | IMPLEMENTED / VERIFIED authored scope |
| SYNTH/all-parent lineage and hit propagation | IMPLEMENTED / VERIFIED authored scope |
| Gutenberg whole-book completeness | BLOCKED: genuine lineage absent |
| File journals, hard process interruption, stale-state refusal | IMPLEMENTED / VERIFIED authored scope |
| Private decisions and atomic kept-membership publication | IMPLEMENTED / VERIFIED authored scope |
| Protected material schemas and local JSONL builder | IMPLEMENTED / VERIFIED authored scope |
| Real complete benchmark material/receipt | BLOCKED: not supplied |
| Low-level tokenizer/token/count/exposure gates | IMPLEMENTED / VERIFIED authored scope |
| Complete CLI/parallel/final-receipt/quota integration | BLOCKED: engineering incomplete |
| Bounded fuzzy candidate review | NOT IMPLEMENTED; no automatic fuzzy deletion |
| Maximum-record/index and population-scale resource certification | NOT RUN / BLOCKED |
| Protected executable plan/authorization/run | BLOCKED in code; sequence/path/digest absent |
| Production tokenizer/final freeze/training/network/push | OUT OF SCOPE / NOT RUN |

The next step is engineering completion plus isolated content-free preparation,
not authorization of a placeholder plan. See the updated runbook for exact schema,
local inspection and verification commands. There is no valid future C05 run
command or plan digest to authorize at this stop point.

Continuation: preserve the current implementation and evidence; finish production
resource certification, bounded fuzzy review, operator plan/authorize/run/resume,
downstream proof transport, final-receipt and quota integration. Keep the protected
refusal until acceptance establishes those paths. Obtain only a content-free
benchmark inventory/receipt from the isolated operator environment. No full C05,
network, source mutation, production tokenizer/training or push is authorized.

## Historical preparation audit (retained below)

The following is the prior committed audit. Its code-gap descriptions are
historical; the continuation above is the current implementation state.

# Global C05 preparation audit (2026-10-02)

**C05 is partially implemented. No executable global plan exists yet.**
The complete first-pass input inventory is now reproducible offline, but protected
benchmark preparation, a production matcher/runner, and downstream membership
enforcement remain blockers. This report is an audit and implementation design,
not execution authorization. No full C05 run, tokenizer, final mixture freeze,
training, external network, upload or push occurred. Source pools were not modified.

## Worktree and binding evidence

The requested Common Pile worktree was clean on `feat/common-pile-allowlist`, HEAD
`737708e31268e8d63ee886619c764e83dd5475cc`, matching the expected pushed commit.
The starting shell was in `F:\Project\xlm-data-ultrax`, HEAD `8271505`, whose
STATUS/runbook edits and untracked operator evidence were preserved. Inspection
used `git status --short`, `git log -5 --oneline`, `git branch -vv`, and
`git worktree list` in both checkouts. The Common Pile commit includes both the
UltraX HEAD and IFM bounds branch as ancestors (`git merge-base --is-ancestor`,
both exit 0). No merge, reset or history rewrite was necessary.

Created `F:\Project\xlm-c05-global`, branch `feat/c05-global-preparation`, using:

```powershell
git worktree add -b feat/c05-global-preparation F:/Project/xlm-c05-global 737708e31268e8d63ee886619c764e83dd5475cc
```

This isolates the global continuation from operator acquisition work. It does not
declare a permanent integration branch. No other worktree was edited. The final
commit SHA is supplied in the chat handoff; `git rev-parse HEAD` resolves it.

[Evidence directory](../evidence/C05-GLOBAL-CONTAMINATION-PLAN/) contains the
manifest, preparation refusal, source facts, matcher characterization, command
arguments, exit statuses, wall times, sampled RSS and logs. All digests below are
stored artifacts, not sample hashes.

* Input manifest: `input-manifest.json`, digest
  `11724d92c011dd01e8e8c3ab944ac2adeef76aa8ff4abc921bf882eb0ac84152`.
* Preparation audit: `preparation-audit.json`, digest
  `76ca1070bdfc7060b01b2fffa71994836d8d6423577a48ada9476e94a8ad3bc0`.
* **C05 execution PLAN DIGEST: absent (`null`). Sequence/path: not allocated.**
  Neither digest above can authorize execution.

## What C05 means here and what exists

Binding sources: `CONTRACTS.md` C02/C03/C05/C06/C07 and C04 benchmark-risk v2/v3;
`EVALUATION_POLICY.md`; historical `reports/P10.md`, P11/P12 pool/tokenizer work,
P21 operator facilities, and P28 sharded deduplication. P10 explicitly certified
development-mode A19 only. A receipt interface is not a completed production scan.

C05 includes global exact/near deduplication before final splits, retained aliases,
known lineage grouping, diagnostic/quick/audit partitions independent of mixture
weights, and exclusion of **every benchmark split**, including development train
splits, before tokenizer fitting or gradient training. Source admission and first-
pass seals establish none of those later results. An official benchmark claim
additionally requires trusted protected exclusion evidence bound to evaluation
checkpoint/suite and exact training membership. No universal zero-contamination
claim is permitted.

| Surface | Existing behavior and limits |
|---|---|
| `data/dedup/matchview.py` | Match view v1: NFKC, Unicode casefold, replace non-word/non-whitespace characters with spaces, collapse whitespace. Underscores survive `\w`. Never writes this lossy view to model text. |
| `data/dedup/engine.py`, `minhash.py`, `index.py` | Exact matching plus deterministic BLAKE2b word shingles, MinHash/LSH; partitioned disk index. Defaults: 5-word shingles, 128 permutations, 32 bands, seed 20260919, estimated Jaccard >=0.8, 64 candidate comparisons/document, bucket cap 256. Oversized buckets are counted/skipped, not guaranteed recall. |
| `data/dedup/sharded.py` | Deterministic signature shards and bounded worker candidate verification; rereads input for survivor output. Failure aborts publication. Not an integrated resumable global C05 exclusion runner; document facts, union-find and other global structures still need scale/RAM certification. |
| `data/dedup/clusters.py` | Stable cluster IDs; survivor is longest canonical byte length, then smallest source ID, then doc ID. Source aliases retained. Lineage is not automatically duplication. |
| `data/dedup/lineage.py` | Explicit lineage, synthetic seed, conversation/book/document/page IDs, canonical URL, then parent/singleton. Non-URL metadata keys are source-namespaced. Uses first available lineage key and first sorted parent fallback. Does not infer missing book IDs or arbitrary semantic ancestry. |
| `data/pools/splits.py`, `freeze.py`, `builder.py` | Group-safe deterministic splits and leak checks; defaults 50 MiB diagnostic, 5 MiB nested quick, 5 MiB audit, seed 20260919. Late group merging invalidates old freeze. These functions have not been applied globally here. |
| `data/exclusion/benchmark.py` | Development exact full-example hash and informative spans. `scan` materializes all input documents; suppression and matching iterate corpus x indexed spans. No fuzzy/semantic benchmark matcher, global journal or bounded production runner. |
| `data/exclusion/receipt.py` | Opaque `FinalExclusionReceipt`, HMAC issuer trust, input/kept-membership/policy/index binding, protected/development separation. Existing membership hash is over sorted doc IDs; it does not alone bind the newly inventoried file content/seals. New production integration must bind both. |
| `operator/final.py`, evaluation suite/harness code | Official-claim gate verifies protected receipt and independently supplied checkpoint/suite/pool bindings. Existing CLI does not supply a production Mix-01 pool receipt. `prepare_exclusion_receipt` records counts; it is not a matcher. |
| `data/pools/tokenizer_fit.py`, `cli/tokenizer_cmd.py` | Checks/selects train split; **no C05 receipt or kept-membership gate** for raw first-pass input. A `split=train` row is not proof of exclusion. |
| `data/pools/manifest.py`, `cli/data_cmd.py`, `cli/mixture_cmd.py` | Policy strings and token-shard availability are not protected C05 verification. Some paths permit `none_declared`; final Mix-01 screening enforcement is missing. |
| New `data/exclusion/inputs.py` / `preparation.py` | Bounded read-only first-pass metadata inventory; exact baseline coverage; source seal, receipt, plan/accounting and size drift refusal. `inventory`, `verify`, `preflight`; no authorization/run command. Preflight exits 2 and writes a non-executable blocker audit. |

Existing CLI includes `xlm data dedup`, split/pool/tokenizer operations and operator
final-receipt facilities, but no complete production `c05 authorize/run/resume`
workflow. The new entry point is `python -m xlm.data.exclusion.preparation`.
It does not pretend to implement those missing verbs.

## Benchmark identities and isolation

`manifests/eval_dataset_pins.yaml` SHA-256:
`f230cea66b4bc24a484cff89d6780991e5ceeae89aa120ad4034ec51af1150dd`.
These are metadata pins already present in the repository, not new live evidence.
Harness dependency is locked to `lm-eval==0.4.13`.

| Task | Dataset | Immutable revision | Required configs / field mapping to validate |
|---|---|---|---|
| ARC-Easy | `allenai/ai2_arc` | `210d026faf9955653af8916fad021475a3f00453` | `ARC-Easy`; question and every choice text, not gold label alone |
| BLiMP | `nyu-mll/blimp` | `877fba0801ffb7cbd8c39c1ff314a46f053f6036` | Every publisher subdataset; both sentence variants |
| HellaSwag | `Rowan/hellaswag` | `218ec52e09a7e7462a5400043bb9a69a41d06b76` | Publisher config; context/ctx_a/ctx_b and every ending; reconcile harness preprocessing |
| PIQA | `baber/piqa` | `142f6d7367fd9877f0fb3b5734ea6a545f54cdd1` | Publisher config; goal and both solutions |

All publisher splits are required. Evaluation policy uses ARC train/validation/test,
HellaSwag and PIQA grouped train plus final validation, and whole BLiMP config
partitions. This does not license omission of any other published benchmark split.
Exact material config/split lists, item counts, duplicate item IDs, file SHA-256s,
render/signature counts and index identity are **NOT VERIFIED**; no values are
invented. Pin availability is not material availability.

The operator must inventory local material in the isolated preparation environment
first. Additional network acquisition is **unknown**, not automatically necessary.
No protected raw index, official examples, labels, benchmark caches or label-bearing
final definitions were opened. The agent-accessible G:\XLM artifact directory
metadata and repository contain no supplied verified protected preparation receipt.
This is not an assertion that no such receipt exists on another machine/account.

EVALUATION_POLICY requires final-data preparation under a different OS identity or
machine, outside the agent's permissions. The result returned here must be a trusted
content-free inventory/receipt with exact revision/config/split/count/file identities,
normalization/render version, signature policy/index identity, code/dependency
identity and isolation attestation. Do not return detailed matches or signatures:
hashes of public benchmark strings are not confidentiality protection. Source
manifest membership must be frozen before queries; no adaptive membership oracle.

The [runbook](../../runbooks/c05-global-preparation.md) supplies bounded future
metadata-only listing commands. Payload acquisition cannot yet be honestly supplied
as a reviewed executable command: exact files/formats/bytes and isolated destination
remain unresolved. No unrestricted `load_dataset` or `snapshot_download` substitute
is authorized. Remote code and automatic license acceptance remain prohibited.

## First-pass inventory and identities

Every sealed baseline pool is included: 10 source seals, 12 leaf views, 11 logical
components, **2,035 canonical JSONL files**. IFM general/planning share one logical
component; Common Pile's six upstream components remain separately identifiable.
Optional txt360 is explicitly excluded.

| Source seal | Exact stored digest |
|---|---|
| Essential-Web (all three views) | `a77c78f7636695ef0ab241c176e07cc9bcfd1815907a13f55dc2f6f7c28c7516` |
| UltraX | `efdb99dbf1bc8380985925fcbbe654536d02c5b0c87162eecc9e92e858302f1f` |
| FinePDFs | `9c2e38c36d2a6528027416347d20850ebd1f08cd036cf7aac1e98c6509fa3b3b` |
| SYNTH | `b020a96fcb14699fd56dfec72a917916f216eb50fd8b3d86bb53077daba6178a` |
| Wiki Rewrite | `08895109ddb498d6cc018b884f121905969435d13e969df5345278120f98f42e` |
| FineWiki | `18321fb27a5ac535190232ccb79ca1387469aa16cf1c45381d4d2f1666de1bf0` |
| IFM General | `7498428c0934be0b660e67154a1c1120ad2be06fd95d74ea958e426e804f3b81` |
| IFM Planning | `8ee097ba90403280ccfdeb578e64a80c7551777775ddf8a53b932e296c5dcc09` |
| Common Pile | `bd63336113131532c1470ff25d1e38c29bd3498a215deff9dcf54154d13491dd` |
| SimpleStories | `069fd0c7960a750ae24cc630921aa9086db0e44c6e56cf1d034afc346331d56d` |

**Prompt correction:** the supplied FinePDFs digest omitted its last `b` (63 hex
characters). The table uses the self-verified stored 64-character digest; no source
artifact was changed to fit the prompt. The other supplied seal identities match.

The manifest binds each canonical path/size/SHA-256, document and text-byte count,
source file/component/view, receipt, seal, source revision/adapter ID, plans,
recomputed accounting/sufficiency, and recorded adapter/admission inputs. Essential
campaign, batch, authorization and per-view membership bindings are preserved.
Document IDs and row locators are **transitively bound** by each complete document
file hash, not enumerated into a new document list. The source seals were self-
checked, their metadata cross-bindings reconstructed and all canonical file sizes
checked. We did not rehash 104.5 GB or revalidate every raw source payload. The future
authorized scan must hash bytes as it reads and refuse a same-size content change.
The preparation manifest explicitly records this limit; verification cannot silently
upgrade to content verification.

| Logical component | Documents | Canonical text B | Loss to first-pass byte target | Loss to final /4 estimate |
|---|---:|---:|---:|---:|
| essential_science | 315,173 | 2,692,218,118 | 1.940% | 10.854% |
| essential_practical | 1,278,649 | 6,962,804,542 | 62.084% | 65.531% |
| essential_prose | 4,239,644 | 20,071,278,364 | 93.423% | 94.021% |
| ultrax_ultrafineweb | 2,746,681 | 10,826,457,142 | 51.231% | 55.664% |
| finepdfs_en | 444,748 | 5,633,801,338 | 29.710% | 36.100% |
| synth_en_explanations | 1,751,337 | 4,771,301,422 | 17.004% | 24.549% |
| nemotron_wiki_rewrite | 1,000,000 | 3,635,905,169 | 41.913% | 47.193% |
| finewiki_en | 864,344 | 5,416,221,771 | 75.629% | 77.844% |
| ifm_behaviors_general_planning | 1,355,028 | 18,970,441,585 | 93.042% | 93.674% |
| common_pile_prose | 194,842 | 1,757,435,959 | 24.891% | 31.719% |
| simple_stories | 906,728 | 1,121,786,829 | 52.932% | 57.211% |
| **Total** | **15,097,174** | **81,859,652,239** | component-specific | not exact tokens |

Physical JSONL bytes to read once: **104,506,534,003**. The 15.1M count is before
global deduplication: not 15.1M unique independent works. Common Pile has 194,864
source rows and 194,842 accepted documents; these are different counters. Its seal
binds the expected p01 digest and 268 units. All six canonical requirements pass.

Headroom arithmetic is `(available - target) / available`, with first-pass and final
targets multiplied by 4. It is a sensitivity analysis, **not a contamination-rate
forecast**. Deduplication, filtering, split reservation and exact tokenizer ratios
all consume headroom. Essential Science is most sensitive by this measure. Aggregate
IFM/Common Pile headroom cannot replace their frozen internal allocations. News,
OER and PDR already consumed all eligible inventory; a later deficit can block the
mixture even when Gutenberg has surplus. No quota or source inventory was changed.

## Matching, false positives, SYNTH and Gutenberg

Current exclusion defaults: `span_length=13`, `min_span_characters=40`,
`max_spans_per_example=32`, `max_corpus_span_frequency=3`, both full and span
matching enabled. Full matching hashes the **entire document** against prompt plus
all answers in original order, so embedding or option permutation can defeat that
path. Span segments include prompt, prompt plus each answer and individual answers.
Stride is half-span; shorter-than-13-token segments can qualify by character count.
There is no entropy or unique-token floor. Only the first matching span/example is
reported. Duplicate full hashes overwrite an example entry; reported example counts
are not an authoritative benchmark item count. `scan` also mutates its index by
removing common spans, so partitioning/resume could change decisions.

`matcher-coverage.json` is an actual tiny authored-fixture run:

| Case | Observed |
|---|---|
| Exact full rendering | 1/1 excluded |
| Uppercase/punctuation prompt embedded in context | 1/1 excluded |
| Reordered choices with a long informative prompt | 1/1 excluded via prompt |
| Four prompt copies assigned different source IDs | **0/4 excluded**; repeated span suppressed |
| Short prompt embedded without answers | **0/1 excluded** |
| Paraphrase with no exact span | 0/1; no paraphrase guarantee exists |
| Five generic negatives: short answers, choice labels, ordinary phrases/fact | 0/5 false positives |
| Two SYNTH-shaped rows sharing `query_seed_url` | **not grouped** by lineage v1 |

0/5 is only an authored control result, not a measured corpus false-positive rate.
The two missed exact/prompt cases are coverage failures, not passed production
requirements. No weak fuzzy match deleted any real data. Existing corpus MinHash
is a near-duplicate heuristic, not a benchmark paraphrase detector, and its caps can
miss cross-source matches. A semantic GPU model would add tuning/compute/false-
positive risk; none was introduced.

The next matcher must be a **versioned extension of the existing exclusion surface**:
compile protected, task-aware exact full/prompt/answer variants and robust token-
boundary spans; preserve duplicate benchmark provenance; use a streaming index,
not a corpus x signature loop; never suppress repeated benchmark strings just
because training repeats them. Common boilerplate suppression must come from a
frozen, independently justified background policy, not contaminated corpus frequency.
Short BLiMP/PIQA prompts need explicit task-specific informativeness rules: applying
13 words universally misses them, while indexing `yes` or choice labels overdeletes.
Exact containment/option permutation, minimum lengths, distinct-token or entropy
thresholds and negative controls must be frozen and tested before a production plan.
No new thresholds are silently chosen by this audit.

Bounded fuzzy candidates may be recorded for operator review with matched fraction,
length/rarity thresholds, candidate caps and aggregate review limits. They must not
automatically exclude large amounts of data on weak similarity. No fuzzy kernel or
its false-positive calibration has been implemented/certified here; it remains a
separate explicitly bounded requirement, not an exact-exclusion guarantee.

SYNTH canonical text contains Context/query-seed text, Question/query, and Answer/
synthetic answer. Synthetic reasoning is excluded by the acquisition adapter; all
actually retained canonical text must be screened. Metadata retains `synth_id` and
optional seed URLs, but not a proven universal shared-seed lineage ID. Lineage v1
does not recognize `query_seed_url`/`additional_seed_url`. A versioned extension
should use available seed URLs and parent/duplicate relations without treating
each synth ID as an independent seed or exposing benchmark matches to the agent.

The bounded real provenance inspection read ten first rows, at most 2 MiB/row,
141,066 B per attempt (two identical reads after the wrapper-name correction),
and saved only metadata keys/locators/presence flags. Gutenberg's
sample has doc ID, source revision/file/row and `upstream_component`, but no parent,
cluster or book ID. Adapter v2 accepts text-only upstream rows. A row is a segment,
not proof of an independent book. The safe proposed exclusion unit is a canonical
row plus every **known** duplicate/derivative group member across sources. Unknown
book-wide propagation remains unprovable. Do not fabricate book IDs, infer books
from shard boundaries, or discard a whole multi-GB source file automatically. If
the required scientific policy demands whole-book exclusion, Gutenberg remains
blocked until real lineage is obtained or the operator explicitly reviews a
different source treatment. No such decision was made here.

## Output, downstream gates and recovery design (not implemented globally)

Source pools remain immutable. A production exclusion artifact should bind the
global input manifest; benchmark material/index and rendering policy; matcher,
dedup, lineage and split versions; code/dependencies; resource plan and authorization.
Within the protected environment, keep a deterministic ledger keyed by source seal,
file SHA, doc ID/row and exclusion reason/evidence identity. Propagate matches to
known groups and retain all aliases. Export only kept membership, opaque receipt,
content-free per-source/component counts and bounded aggregate reasons. Detailed
benchmark matches stay protected, even when evidence is hashed. No corpus text is
needed in the exported manifest or journal.

Tokenizer input must be training membership **minus exclusions**, verified against
the exact seal/content and protected receipt. Final exact-token down-selection must
use the same screened membership and unchanged component quotas. A receipt-ID string,
arbitrary `c05=true`, train split flag or a stale policy digest cannot pass. The
current tokenizer/final-mixture APIs do not enforce this end to end: they must not
be used for Mix-01 until that integration is implemented and tested. This audit
does not claim that the new preflight has repaired those APIs.

Required runner design: journal by immutable input-file identity and stage under a
plan lock; stream/hash each file; bounded record parsing; persist completed shards
with counts/digests; commit progress only after output fsync/atomic rename. Resume
verifies plan/index/source/code identity and every reusable shard, discards only
private incomplete staging under a validated output root, and restarts an interrupted
file deterministically. Exclusion keys are unique and final ledgers sorted, so replay
cannot duplicate drops. Track scratch plus partial/index/journal/temporary/publication
overlap against one aggregate disk cap; preserve spent deadline and record counts.
After every required file/group stage succeeds, verify aggregate membership/counts
and atomically publish a completion manifest. A crash before publication must leave
no apparently complete receipt. Lock/replay corruption, stale inputs and budget
exhaustion fail closed. Existing sharded dedup crash-abort behavior does not establish
this cross-stage resume contract; these C05 tests remain NOT RUN/unimplemented.

After exclusion and tokenizer freeze, count exact tokens per logical component and
its frozen IFM/Common Pile allocation. A deficit uses the remaining eligible cursor
of that component only, in a new reviewed acquisition plan/authorization. Re-screen
new material globally with existing groups; new group bridges invalidate affected
splits/membership and require a new pool/receipt lineage. Never reuse old C05 proof
for a changed corpus. No repeats, cross-component substitution, renormalization or
inventory reset. If a component is exhausted, stop for an operator decision. No
top-up or new source was acquired here.

## Resource design and measurements

CPU/disk deterministic processing is the recommendation. GPU is not used. One worker
for a bounded implementation pilot; consider four signature workers only after
measuring memory/I/O. This is distinct from 16-worker test execution.

One source read is 104.51 GB on disk; the text payload is 81.86 GB. Dedup survivor
output and protected screening may require additional reads. A pure I/O calculation
at hypothetical 100/500 MB/s is 1,045/209 seconds **per read**; neither is measured
throughput or a wall-time promise. NFKC/tokenization/hash generation and 128 MinHash
permutations may dominate CPU. The existing all-documents benchmark scan cannot
fit this corpus plus Python object overhead into the stated 72 GB RAM.

15,097,174 x 128 x 8 is **15,459,506,176 B** of raw uint64 signatures alone.
32 band postings/document means **483,109,568 postings** before keys, facts, union-
find, spill overhead and duplicates. Index storage cannot be certified from the
signature array alone. Proposed review targets: 24 GiB process-tree RAM, 192 GiB
aggregate scratch/index (including publication overlap), 32 GiB final metadata/
membership, 32 GiB free-space reserve. These are **unmeasured design targets, not
frozen plan limits**. They must be amended before execution if a measured bounded
pilot shows them insufficient. Final per-record, candidate, output, document and
elapsed-time ceilings remain to be made concrete with the actual benchmark index.
No compressed duplicate corpus or model weights are budgeted. No actual full C05
peak RAM, temporary/index footprint or throughput was measured.

Sampled available space: C 845,422,497,792 B, G 692,376,616,960 B. Runtime must check
again. Metadata inventory: 3.516 s, sampled process-tree RSS 106,254,336 B; this is
not a corpus scan benchmark. Authored matcher characterization: 0.703 s, 90,984,448 B.
RSS is sampled every 50 ms and can miss short peaks; wrapper wall includes startup.

## Validation and requirement ledger

Python 3.12.13, Windows-11-10.0.26200-SP0. Existing Common Pile CPU/eval environment
reused through `UV_PROJECT_ENVIRONMENT`; `PYTHONPATH` selects this worktree's `src`.
All commands use `uv run --offline --locked --no-sync`; no dependency sync/install
or lock change. `pyproject.toml`, `uv.lock`, `.python-version`, and the existing
mutually exclusive CPU/CUDA installation policy are preserved. HF Hub/datasets,
Transformers and uv offline flags were 1. Worker OMP/MKL/OpenBLAS/NumExpr threads
were 1; tokenizer parallelism false. These are test settings, not training changes.

* New focused input tests: 17 passed, exit 0, 4.843 s, 156,307,456 B sampled RSS.
* Related selection: 237 passed, exit 0, 13.219 s, 2,075,328,512 B sampled RSS;
  includes new Essential integration and exclusion/dedup/lineage/split/pool/tokenizer/
  suite/operator regressions. Authored acquisitions use loopback HTTP only.
* Complementary related serial/optional selection: 1 passed, 237 deselected,
  exit 0, 1.844 s, 114,925,568 B. No selected test was skipped.
  Focused and related selections overlap and must not be added as unique tests.
* First parallel selection exited 2 before running tests: the installed-harness
  optional test requires `-n 0` or audited loadgroup. It was moved to the explicit
  serial selection, not omitted. No retry-until-green was used.
* Initial authored characterization incorrectly assigned to a frozen dataclass;
  changed to `dataclasses.replace`. The failed command is preserved. An initial
  source-facts wrapper name collided with its output JSON name; the facts were
  retained, and a distinct command-evidence name records the successful invocation.
* Initial metadata prototype interpreted Essential authorization fields as self-
  digests; corrected to their existing declared authorization identities. No stored
  authorization was modified. Initial Ruff line-length findings were formatted/fixed.
* The first staged diff check exposed CRLF/trailing whitespace in captured Windows
  console evidence. Evidence text was normalized to LF with trailing whitespace
  removed; JSON values and artifact canonical digests are unchanged. The command
  recorder now does this explicitly and records the original stdout SHA-256 for
  subsequent commands. The failed check remains recorded, not recast as a pass.
* Final Ruff format/check and strict mypy over all seven touched Python files passed,
  exit 0; final cached mypy 1.032 s / 115,974,144 B sampled RSS (earlier check:
  28.000 s / 745,115,648 B). `git diff --check` is recorded
  separately in evidence. Complete repository acceptance, full-corpus
  C05, production resume/crash tests, live benchmarks, CUDA and performance tests
  were NOT RUN. Existing acquisition resume tests are not C05 resume certification.

| Requirement | Status |
|---|---|
| C05 contract/code/CLI/history map | VERIFIED audit |
| Eleven baseline components, ten seals, paths/revisions/metadata/counts | IMPLEMENTED / VERIFIED metadata-only |
| Corpus hashes / every doc ID reread | NOT RUN; bound transitively, mandatory scan-time check |
| Immutable source stores, optional txt360 exclusion | VERIFIED for preparation |
| Benchmark immutable revisions | VERIFIED repository metadata |
| Exact benchmark material/config/split/count/signature identities | BLOCKED: isolated operator preparation evidence missing |
| Development matcher and receipt interface | IMPLEMENTED previously / VERIFIED authored scope |
| Production exact/prompt coverage and scalable matching | BLOCKED: demonstrated coverage/performance gaps |
| Fuzzy detection and population false-positive estimate | NOT IMPLEMENTED / NOT RUN; no claim |
| Global lineage propagation incl. SYNTH; Gutenberg whole-book lineage | BLOCKED; known keys insufficient, no invented IDs |
| Resume/crash-safe global runner and atomic exclusion publication | NOT IMPLEMENTED / NOT RUN |
| Tokenizer/final-mixture C05 membership enforcement | NOT IMPLEMENTED end to end; blocked |
| Resource sizing and component-headroom sensitivity | VERIFIED arithmetic; production measurements NOT RUN |
| Executable plan, sequence, authorization/run/post-run commands | BLOCKED; no plan digest, no authorization |
| Tokenizer, final 6B freeze, training, CUDA, push | OUT OF SCOPE / NOT RUN |

## Exact stop and continuation

Stop is **verified metadata manifest plus a non-executable preflight refusal**,
not “ready for authorization.” There is no honest `c05 run` or post-run verification
command to give yet. Use the runbook's offline `verify` command (exit 0) or `preflight`
(expected exit 2) to reproduce this state. The new implementation is complete only
for preparation inventory/refusal; the full requested production preparation is
not complete. The missing runner and gates are engineering work, not cured merely
by authorizing a run.

Next prompt:

> Continue in F:\Project\xlm-c05-global on feat/c05-global-preparation. Read this
> report and evidence; verify the input manifest offline. Preserve all source seals,
> quotas and benchmark pins. Complete the existing C05 exclusion surface with a
> versioned streaming matcher, known-lineage propagation, bounded resumable runner,
> protected publication and tokenizer/final-mixture membership gates. Keep authored
> tests separate from live evidence. Obtain only content-free benchmark preparation
> metadata from the isolated operator environment; do not open final examples or
> raw signatures in the agent workspace. Freeze an executable plan only once exact
> protected material coverage and resource bounds verify. No full C05 run, external
> network, tokenizer, training, source mutation or push is authorized.

C05 GLOBAL PLAN NEEDS FURTHER WORK
