# Essential-Web evidence v2.2: prospective Arm-M transport amendment

Version **essential-web-evidence-v2.2**, frozen 2026-09-28.
Verdict: **READY TO IMPLEMENT EVIDENCE V2.2**.
This is a protocol decision, not implementation or acquisition authorization.
Both arms remain non-executable pending implementation, new offline child-plan
review and a separate acquisition authorization review.

Inherit [v2.1](ESSENTIAL-WEB-EVIDENCE-V2.1-PROTOCOL.md) and its v2.0 parent
without rewriting either. The v2.1 freeze is
bac82d6b9538f4005f7f0ffee6aa5c4f3a5394098c0fae8f63fc94c76832a7cd.
This document's exact bytes and the accounting audit are bound by
[the v2.2 freeze](../evidence/ESSENTIAL-WEB-EVIDENCE-V2.2/freeze.json).

## 1. Decision, scientific identity and historical compliance

Choose option A: a prospective versioned M request-cap amendment. No new M
replicate is selected and the evidence lineage is not abandoned. Only M's
footer-request/file ceiling changes numerically, from 10 to 16. The exact
future revalidation schedule and the existing cumulative disk rules are
specified below. No T ceiling, data-plan ceiling or scientific rule changes.

The earlier M first-file planning consumed twelve physical requests against
the then-applicable ten-request limit. That event **violated v2.0/v2.1**.
v2.2 does not pardon, relabel, delete, reset or retroactively certify it.
It prospectively permits charging inherited usage against a new allowance
before additional work. Historical violations stay visible in every child.

The stated and receipted exposure was footer/layout and transport behavior,
before acquisition of the new 4096 M metadata rows or observation of M
selector outputs. The files were already frozen and their windows deterministic.
Changing an execution resource ceiling under that exposure history preserves
scientific selection preregistration; it does not preserve a claim of perfect
historical operational compliance. A cost-only amendment must never alter
membership or be used to optimize against new scientific outcomes.

A new replicate would discard usable physical observations and spend another
planning budget while changing scientific identities; it would not repair the
accounting implementation. It is unnecessary for a disclosed footer-only
operational deviation. Abandonment is unwarranted without semantic leakage,
membership drift or irrecoverable scientific provenance. Unresolved operational
history still blocks execution even though the protocol amendment is justified.

Keep all eight M files and windows, the 118 T locators, revision, selector
policy/evaluator, seeds, strata, censuses, rubric, blinding, adjudication and
M-report-before-T-unblinding rule. Scientific hash namespaces remain
**essential-web-evidence-v2.0**, including review IDs/order. New receipt version
is v2.2; never regenerate the selection by replacing a version string.

Selection canonical digest:
975ba3dee4af0598e665ea05c69bbe49336c3e3a35fca777a863073190b78474.
Selection file SHA-256:
8424f9668fef6952a558ead0dae27a705545a7f4a4d302713707d3ec6bb1af27.
Original selection bytes: 23,807; membership: 118 unchanged source locators.

## 2. Reconstructed M history, with measurement limits

The old handler incremented its hop counter before checking the cap and before
calling the parent redirect handler. Its fourth invocation raised: the fourth
redirect target was **not requested**. Old activity was three successful
original ranges plus three followed redirects on 2014-15, and one original
failed request on 2015-32: **7 physical requests**, not 8. The later complete
pass has 24 original ranges plus 24 followed redirects: **48**.
These are distinct requests in distinct runs, not duplicate receipt references.

| Crawl / frozen M basename | Historical requests | Exact recorded range-body bytes | Requests left at 16 | Left after nominal 2-request revalidation |
|---|---:|---:|---:|---:|
| 2014-15 / train-01860-of-02772.parquet | 12 | 537,616 | 4 | 2 |
| 2015-32 / train-01682-of-01920.parquet | 7 | 264,322 | 9 | 7 |
| 2016-50 / train-02156-of-03132.parquet | 6 | 215,631 | 10 | 8 |
| 2018-05 / train-03378-of-03429.parquet | 6 | 264,763 | 10 | 8 |
| 2019-09 / train-00153-of-02577.parquet | 6 | 228,714 | 10 | 8 |
| 2021-04 / train-00179-of-03315.parquet | 6 | 221,283 | 10 | 8 |
| 2021-49 / train-00408-of-02895.parquet | 6 | 231,779 | 10 | 8 |
| 2024-26 / train-01127-of-03168.parquet | 6 | 227,340 | 10 | 8 |
| Arm | **55** | **2,191,448** | **25 under arm cap** | **9 under arm cap** |

The request total reconstructs the disclosed successful ranges, followed
redirects and failed request. Any independently evidenced additional attempts
must also be charged; the table is not a license to discard further history.

The byte column is exact for receipt-recorded returned range bodies, not a
certified all-response-body total. In the old producer, redirect bodies were
read through TransportBudget but not exported into these arm receipts.
TransportBudget's broad ceiling was not a 4096-byte redirect-body limit.
No exact historical redirect/error-body measurements or sufficiently tight
per-file bounds are present in the supplied artifacts. The failed second-file
request has zero *recorded range-body* bytes; do not infer its entire response
body was zero. Actual M all-body bytes/file and arm remain **unknown**.

All recorded bodies carry forward. Remaining M footer byte budgets calculated
from recorded bodies alone are 4,194,304 minus each row above and 31,362,984
arm-wide. These are upper limits on remaining allowance, not spendable certified
budgets until missing costs are reconciled. Unknown historical bytes, retries
or timing remain execution blockers. Only authoritative local evidence or a
mechanically justified upper bound charged/reserved against every relevant cap
can resolve them; an analyst's small estimate is insufficient. No unknown is zero.

## 3. Exact M request ceilings

| M request ceiling | v2.0 / v2.1 | v2.2 |
|---|---:|---:|
| Footer planning/revalidation per file, cumulative | 10 | **16** |
| Footer planning/revalidation whole arm, cumulative | 80 | **80 unchanged** |
| Data per file/plan | 100 | **100 unchanged** |
| Data whole arm | 800 | **800 unchanged** |
| Whole arm, footer plus data | 880 | **880 unchanged** |
| Redirect transitions per logical attempt | 3 | **3 unchanged** |
| Retries per request | 2 | **2 unchanged**, delays 1 and 2 seconds |

Sixteen is justified by **12 historical + 1 original revalidation request +
3 permitted redirect follows = 16**. It accommodates one maximal allowed
resolution chain at the bottleneck file, and at observed one-hop routing allows
two two-request attempts. Fifteen would exclude an otherwise permitted
three-redirect revalidation chain at that file; a larger cap is not evidenced.
This is a bounded execution allowance, not minimum-plus-epsilon fitting to
scientific outcomes. It does not promise that every retry can complete.

The 80 footer arm cap leaves 25 prospective requests. Nominal revalidation
needs 16, leaving 9 arm-wide. No evidence justifies raising 80; 880 remains
80+800. Individual file allowances are not additive entitlements beyond the
arm cap, and footer/data categories cannot borrow requests.

All M byte, row, memory, disk, decoder, timeout and concurrency ceilings stay
as v2.0/v2.1. In particular: footer 4,194,304/file and 33,554,432/arm;
data 29,360,128/file and 234,881,024/arm; total bodies 268,435,456/arm;
single body/range buffer 4,194,304; decompression 67,108,864/file and
536,870,912/arm; scan 16,384/file and 131,072/arm; parser 33,554,432;
process-tree RSS 268,435,456; scratch/final/combined disk
469,762,048 / 67,108,864 / 536,870,912; deadlines 1800/600/30 seconds;
512 rows/file, 4096/arm. The full unchanged maps are in the freeze.

## 4. Minimal future M revalidation

Perform all local checks first, using no request: frozen revision/path, parent
hashes, schema/chunk/window identities, exact projection, code/environment/plan
binding, accounted history and remaining reservations. Do not relist the
repository or discover new windows.

For each of the eight frozen files, in frozen crawl order, immediately before
its data stage, plan **one logical GET of bytes 0-3 inclusive** from its pinned
canonical resource URL, using the current allowlisted range transport. This
reads only Parquet file magic, not rows. Accept only final HTTP 206 with exact
Content-Range 0-3 / previously recorded length, four-byte PAR1 body, and the
same strong ETag as the adopted observation. Validate all redirect hosts and
resource identity. Missing/weak/different ETag, changed length, wrong range,
whole-file 200 or other mismatch is a STOP, not permission to refetch footers,
change identities or reselect. A separate HEAD and duplicate ETag request are
unnecessary: the same response supplies range identity, length and ETag.
The immutable revision/path and plan bindings are checked locally.

Every subsequent data response must match the same bound remote identity;
the preflight check cannot excuse later drift. This is a minimal revalidation
design requiring implementation and synthetic verification, not a claim that
the current footer wrapper already compares against the adopted ETag.

Per file: one logical request; **2 physical requests nominally** using the
observed one redirect, **1** if direct, **4** for one maximum legal chain.
Prospective revalidation is limited to **4 physical requests/file across
initial attempt, all retries and redirects**, and **25 physical requests/arm**
across all eight files. These are plan reservations within the 16/80 cumulative
ceilings. A failure or retry consumes this reservation; no new allowance appears
on resume. All eight three-redirect chains would need 32, which does not fit:
stop incomplete rather than promise that route. Never silently omit a file.
At most two nominal two-request attempts/file can complete; other combinations
stop at the earliest retry, per-file, arm, byte or time limit.

Reserve and durably charge each physical attempt before issuing it; a denied
redirect is not a followed request. Record its rejection separately. Runtime
must stop before exceeding a ceiling, including missing accounting or identity.
Successful nominal body payload is 4 bytes/file, 32/arm; all redirect/error/
failed bodies add to the same footer byte counters and must be bounded/metered.
One-time signed targets may be reused only through existing validated immutable
resource cache semantics, with no credential logging and no bypass of identity
checks. No arbitrary URL or additional HTTP/decoder stack is introduced.

## 5. M data capacity and implementation qualifications

Eight plans estimated at 85 data requests consume 680. Known history plus
nominal revalidation gives **55+16+680=751 <=880**, leaving 129.
Using the entire footer cap gives **80+680=760**, leaving 120.
At the data cap, **71+800=871**, or **80+800=880** exactly.
Thus neither 80 nor 880 needs an increase based on those budgets.

However, the recorded 85 is a range/control estimate: the source formula sums
projected-column buffered ranges and adds four metadata requests. It is not a
redirect-aware physical guarantee. Naively giving every estimated request
another redirect would exceed 100/file. Before execution, derive exact physical
requests using the existing permitted target reuse/range transport and actual
stage assignment, count every control/redirect/retry once, and refuse if it
cannot fit 100/plan, 800 data/arm and the remaining 880 total. Do not count the
same revalidation event in both categories or use the estimate as authorization.
No M data-request cap change is approved.

## 6. Arm T final disk: staged gates, unchanged cumulative caps

Choose the staged-publication interpretation, with explicit requirements:
future human label/adjudication volume alone need not prevent text acquisition.
It is not a guarantee of eventual complete review. **Do not raise the 16,777,216
final or 536,870,912 combined ceiling**, nor the 520,093,696 scratch,
8,388,608 retained text or 65,536 full-document ceiling.

Before acquisition, require a conservative schedule for all acquisition-stage
persistent artifacts, retained texts, manifests, provenance, bounded failure
receipts, logs, caches, partials and atomic temporary/old/new copies. Count
earlier retained files cumulatively and count actual representation overhead,
not just UTF-8 text length. Across stages, continue the same storage inventory
and counters: each stage does not receive a fresh 16 MiB. Retained text shared
between review packages is stored once; real extra copies must be counted.

Later, before accepting/writing/publishing forms, labels, adjudication or a
summary, reserve their actual encoded sizes plus temporary duplication against
the remaining shared ceilings. Reserve incrementally before each write, not
only after an oversized artifact is complete. Release a reservation only after
the accounted object is actually removed. Scratch staging does not exempt
persistent outputs from the final cap. No hidden spill or omitted artifacts.

If the complete scientific review cannot fit, refuse publication, preserve
bounded INCOMPLETE evidence and original reviews within the permitted storage,
and report the shortfall. Do not truncate substantive labels, alter the rubric,
drop documents, silently discard evidence, resample or claim a complete review.
A separately reviewed amendment may later be needed, but is not granted here.

The modeled 16 MiB package plus additional logs/parents necessarily exceeds a
16 MiB all-artifacts cap; that is an expected rejection case, not proof that
the minimum required publication cannot fit. Conversely, the reported
102,098,902-byte overall high water is not an acquisition certificate: no bound
disk schedule with those inputs is in the T child. The present disk helper
also fails independent intermediate-final and scratch-peak probes. Acquisition
still requires a corrected cumulative phase schedule and fail-closed tests.

## 7. T readiness and inherited implementation blockers

All T v2.1 numerical caps remain unchanged. The child retains 118 correct
locators and is DRY_NOT_AUTHORIZED with executable=false. It is **not otherwise
ready for authorization review**. Regenerate it after the following corrections,
without changing the reader or scientific selection:

- Its ranges start at data_page_offset, while dictionary_page_offset is earlier
  in all eight chunks. The existing schedules omit 17,539,154 bytes of required
  dictionary prefixes in aggregate and extend equally far past the proper
  chunk ends. The full chunk begins at the earliest required dictionary/data
  page; compressed length covers that complete chunk. Correcting the start
  retains 47 nominal <=4 MiB ranges and 179,963,169 compressed bytes.
- Sixty-three nominal logical future requests (47+16 controls) plus 54 carry-in
  gives 117, but that omits physical redirect follows. With one redirect per
  logical request it would be 180. Define actual controls and physical bounds;
  no request-cap enlargement. The exact M revalidation prescription does not
  automatically change T controls.
- Carry-in's 2,231,492 bytes / 54 requests are recorded history. Its 4096-byte
  per-request residual estimates are not enforced historical bounds. Gap
  entries are not reserved in readiness checks. Unknown bodies/attempt
  attribution/timing must block until reconciled or conservatively bounded
  from authoritative evidence and charged against all affected caps.
- Durable save/load currently preserves adopted entries, not later live
  charges: the audit reproduced requests/bytes/time changing from 1/123/7 to
  0/0/0 after reload. Make the actual event ledger durable before any network.
  Same-ID conflicting replay must refuse, and crash uncertainty must reserve
  attempted work rather than refund it.
- Do not subtract footer bytes from the data subcap. For the future bound,
  data headroom is **38,140,639**, not 35,909,147; combined headroom based on
  recorded bodies is **52,686,363**, before unknown/additional work. Keep
  nominal chunk bytes distinct from conservative transfer bounds.
- Chunk decompression sum 311,068,163/max 41,297,124 and scan sum 38,400/max
  8,448 numerically fit. These are not allowances for arbitrary repeated
  decoding. Account parser expansion and all repeated work.
- Memory reservations 93,613,610..105,045,302 bytes are **89.28..100.18 MiB**,
  not 93.6..105.0 MiB. They are formula reservations; full allocator/process
  base and accumulated retained output plus external supervision must be
  qualified. A function accepting a supplied RSS number is not a running
  process-tree supervisor.
- Bind disk/deadline schedules, measured or safely bounded historical durations,
  per-file elapsed work and remaining time. Zero receipt counters are not
  instant runs. Current helpers are not evidence of a supervised executor.
- Bind complete parent descriptors, source/code/environment, length/ETag and
  effective history in new children. Existing children have empty parents
  objects; a claimed hash without the resolved audit evidence is insufficient.

These are repairs to existing requirements, not new scientific permissions.
No implementation is performed in this review.

## 8. Freeze/adoption and authorization boundary

The v2.2 freeze binds the original v2.1 freeze and its parent graph, both M
historical receipts, existing M observations, the v2.1 first-file M audit capture,
this review's full-arm accounting reconciliation, the blocked M child, the dry
T child, captured T carry receipt (with rejected readiness claims preserved),
and the byte-identical selection. It includes exact old/new ceiling maps,
revalidation reservations, code evidence bindings and unresolved unknowns.
All adoptions retain original producer/version/status; none rewrites history.

History must read:
v2.0: T physically infeasible; defective M transport planning.
v2.1: T transfer amendment; confirmed M historical overrun; acquisition blocked.
v2.2: prospective bounded M request/accounting amendment; implementation pending
and execution still blocked on documented qualifications.

The required sequence is **this protocol -> Muse implementation -> regenerated
offline child plans and review -> separate acquisition authorization review**.
The [review's exact Muse prompt](ESSENTIAL-WEB-EVIDENCE-V2.2-REVIEW.md#5-exact-muse-handoff)
is the next action. No network, acquisition, corpus text inspection, scientific
selection change, tokenizer/training/admission, push or campaign is authorized.

