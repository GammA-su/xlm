# Essential-Web evidence v2.1: bounded transport amendment

Version **essential-web-evidence-v2.1**, frozen 2026-09-28.
Verdict: **READY TO IMPLEMENT EVIDENCE V2.1**.
Scope: protocol amendment and implementation handoff only.
Arm M acquisition and Arm T text acquisition remain **NOT AUTHORIZED**.

This normative amendment inherits every scientific and execution requirement of
[essential-web-evidence-v2.0](ESSENTIAL-WEB-EVIDENCE-V2-PROTOCOL.md), except the
explicit Arm-T transfer ceiling changes below. The old document and receipts
remain unchanged. This amendment is bound by
[freeze.json](../evidence/ESSENTIAL-WEB-EVIDENCE-V2.1/freeze.json).
The parent freeze canonical digest is
**fe2157799a86fe777e45c220716563f8a73245a12593c40909222555bf1b8248**.
A disagreement outside these explicit changes is a STOP, not an implicit override.

## 1. Decision and rationale

Adopt option 1, a narrow physical transfer amendment. Eight frozen development
files each require a complete text column chunk with the current reader.
The independently reproduced cost is 213,517,601 bytes of future transfer
allowance (203.63 MiB), including the existing 4 MiB framing allowance per
chunk. The maximum is 27,886,815 bytes (26.59 MiB). All eight exceed the
v2.0 14 MiB data/file and 16 MiB total/file ceilings; the aggregate exceeds
128 MiB before planning is added. Whole-chunk refusal under v2.0 was correct.
Do not tighten that estimator or introduce a second decoder stack.

The 32 MiB/file and 256 MiB/arm ceilings are the next binary capacities above
the frozen 16/128 MiB limits, with the same eight-file scale and an explicit
footer reserve. They provide material margins without fitting a minimum plus
epsilon. They are bounded allowances, not permission to exhaust every retry
or predictions of actual transfer. No further automatic growth is allowed.

Physical cost is not a semantic outcome. The record shows footer-only cost
discovery before text review; this task inspected only local numeric receipts
and locator metadata. Receipt integrity is not independent proof of all past
operator behavior. On that documented exposure history, changing a transport
ceiling preserves scientific selection preregistration: no observed text
quality, labels, strata, policy output, or review score determines membership.
The amendment and its timing must be disclosed in subsequent reports. It does
not make development text confirmatory or remove clustered sampling limitations.

Option 2 could reduce future costs by preregistering fewer independently chosen
clusters/windows or another review design before its own cost/content exposure.
Reducing rows inside these same chunks would often save no transfer and would
lose census/review coverage. Cost-based replacement or dropping expensive rows
would condition selection on a new variable. Any redesigned sample needs a
separate protocol, selection freeze and scientific justification; none is made.

Option 3 remains valid if an operator declines the bounded cost or execution
gates cannot be met. Keep v2.0 physically blocked forever in its own lineage.
The amendment justifies proceeding to implementation, not acquiring data.

## 2. Exact changes and shared accounting

Binary bytes, inclusive ceilings:

| Arm T scope | Footer bytes | Data bytes | Total response-body bytes |
|---|---:|---:|---:|
| Per frozen file | 2,097,152 (2 MiB) | 31,457,280 (30 MiB) | 33,554,432 (32 MiB) |
| Entire eight-file arm | 16,777,216 (16 MiB) | 251,658,240 (240 MiB) | 268,435,456 (256 MiB) |

Footer ceilings are unchanged. The v2.0 data/file value was 14,680,064,
total/file 16,777,216, and total/arm 134,217,728; its implied aggregate
data allocation was 117,440,512. These transfer ceilings alone change.
The explicit aggregate data subcap is the sum of the eight file subcaps,
not an extra allowance. Unused footer allowance cannot be borrowed by data.

Every response-body byte belongs to exactly one stage category and the same
per-file and arm totals: planning, revalidation, data, redirect/error bodies,
failed/partial responses, retries and resumed work. A repeated attempt consumes
new budget. All network attempts, HEADs and redirect transitions count toward
the unchanged request ceilings. Empty bodies consume requests but zero body
bytes; unknown body bytes or requests are not zero. Count returned body bytes,
not TLS/header wire overhead; do not call these caps total link-layer traffic.

Maintain one durable ledger per arm across stages, processes, attempts and the
v2.0-to-v2.1 adoption. Do not reset it on version change, restart, failure or
adoption. Charge past events once, with explicit attempt identities and no
double-counting of duplicate reports. The two arms have separate ceilings:
no transfer credit may be moved between M and T. Never overwrite old receipts.

The latest T pass consumed 1,994,765 bytes and 48 requests. The earlier
text_cost_evidence.incomplete.json adds 236,727 bytes and 6 requests:
known T planning carry-in is **2,231,492 bytes / 54 requests**, including
473,454 bytes / 12 recorded requests for 2014-15. These are recorded amounts,
not certification of unmetered redirect/error bodies or other missing history.
Reconcile all history before execution, or fail closed. No new acquisition
allowance is granted for information that is missing.

The latest future-transfer aggregate excludes planning; use data upper plus
all consumed/planned footer and retry work when comparing totals. At the
observed map plus known prior planning, the total is 215,749,093 bytes and
headroom is 52,686,363 bytes. Even reserving the full 16 MiB footer subcap,
the map totals 230,294,817 bytes, leaving 38,140,639 bytes. Worst file data
headroom is 3,570,465 bytes (11.35% of its data cap; 12.80% above its bound).
With a full 2 MiB footer reserve, the same absolute headroom remains within
32 MiB. This does not guarantee another full 4 MiB retry at every file.

## 3. Unchanged scientific identity

Keep exactly the original 118 locators, cells, strata, censuses, precedence,
ownership, aliases/shortfall rules, seeds, frozen eight M files, development
files, original windows, source revision, selector policies and evaluator,
rubric, two-reviewer procedure, adjudication, M-before-T sealing, review
membership and development/confirmation labels. No selector or sample
regeneration is required or authorized.

In particular, the protocol version in a new receipt is
essential-web-evidence-v2.1, but the scientific hash namespace remains
**essential-web-evidence-v2.0** for metadata-file ranking, text selection,
review IDs and review ordering. This explicitly freezes the protocol_version
operand of v2.0 section 9 as well as the literal strings in sections 3 and 6.
Do not globally replace version strings and thereby change ranks, IDs or
review order. Selection seed 20260927, metadata seed 20260927, review-order
seed 20260928, K generation/commitment rules and ew2- IDs remain unchanged.
No new K or review package is generated in this task.

Selection artifact, adopted unchanged:
G:/Project/xlm-evidence-v2/essential-web/text_selection_manifest.json

- Canonical self-digest:
  **975ba3dee4af0598e665ea05c69bbe49336c3e3a35fca777a863073190b78474**.
- File bytes: 23,807; SHA-256:
  **8424f9668fef6952a558ead0dae27a705545a7f4a4d302713707d3ec6bb1af27**.

Adopt the whole existing manifest by reference, retaining its v2.0 version,
freeze digest and strata/order. Do not rewrite it with a new version field.
The audit verifies 118 distinct source locators and exact equality to the cost
map's sorted per-file wanted rows. It does not reselect from corpus metadata
or certify 118 distinct underlying articles.

## 4. Unchanged physical limits and evidence boundary

| Limit | Evidence and remaining obligation |
|---|---|
| Response body and range buffer | <=4,194,304 bytes each; split complete chunks into bounded ranges. Whole chunk is the decoding unit, not one HTTP response. |
| Decompression | <=67,108,864/file and <=536,870,912/arm. Text-chunk map max 41,297,124; sum 311,068,163. Residual headroom 25,811,740/file at maximum and 225,802,749/arm. Include parsing/expansion, repeated work and discarded text; these residuals are not measured costs. |
| Scanning | <=16,384/file and <=131,072/arm; max 8,448 and sum 38,400. Headroom 7,936 at maximum and 92,672/arm. Batch 256, no hidden rescan. |
| Requests | <=100/file and <=800/arm across all stages. Legacy 6/file is arithmetic, not a valid physical upper bound. Recompute from actual bounded ranges, HEAD/revalidation, redirects, retries and carried history before execution. No cap increase. |
| Parser / process memory | Parser <=33,554,432; supervised process-tree RSS <=268,435,456. Map workspace max 74,851,556 leaves 193,583,900 against RSS numerically, but is only a reservation formula, not measured RSS or a complete allocator proof. |
| Compression ratio | <=15 and existing <=16,777,216-byte exemption unchanged. Map text ratios are below 1.75; this does not waive allocation checks. |
| Retention | <=65,536 UTF-8 bytes/full document; <=8,388,608 retained text bytes. 118*65,536=7,733,248. No truncation, cleanup or backfill. This is distinct from physical transfer. |
| Disk | Scratch <=520,093,696, final <=16,777,216, combined <=536,870,912 at every instant. Include existing adopted artifacts, caches, partials, expansion, logs, temporary/atomic copies and review artifacts. No high-water or complete schedule is established by these receipts. |
| Runtime | <=1,800 seconds/arm, <=600/file, <=30/request, earliest deadline. Receipts' elapsed_seconds=0 is not a duration measurement. Prior planning and future work must be accounted; no automatic time reset. |
| Retries / concurrency / hosts | Two retries with 1/2-second delays, <=3 redirect transitions/logical request, one file/worker, exact v2.0 HTTPS/443 host allowlist and immutable identity validation. Unchanged. |

The complete map proves transfer-envelope fit under the amendment; it does
not certify every unchanged execution limit. A useful request-count diagnostic:
raw chunk lengths require at least 47 total ranges at 4 MiB (5 for 2016-50,
6 for each other file), before control requests/redirects. A hypothetical
contiguous schedule covering the full transfer allowances uses 55 ranges
(6 for 2016-50, 7 for each other file).
Three attempts times four requests per attempt would consume 660 plus known
54 planning requests = 714, before extra controls; first-file maximum would
be 84+12=96. This is a capacity illustration only: no enforced range schedule,
control budget or all-retry transfer fit is certified. Retry maxima are
ceilings, not guaranteed completed retries. Stop when any ceiling binds.

A one-file staged decoder may fit disk/memory, but network byte totals do not
bound peak disk occupancy, Arrow allocation multiplicity, or runtime.
Before any acquisition, require actual offline-verified range/decoder
reservations, persisted counters and supervised enforcement under these same
limits. Refuse if that cannot be done. Do not change a non-transfer cap to
turn this review into an execution success.

## 5. Explicit parent and adoption semantics

The new freeze binds raw bytes/length and canonical digest for each parent:

1. v2.0 freeze and its bound normative document/inventory.
2. M footer_evidence.json, digest
   **2ecf3eb9df01a41f7f9defa20633841211396f654350df80e460f5c2b5740052**.
   Adopt existing pinned-source footer/window/cost observations, including
   eight 512-row windows. Preserve original producer, version, COMPLETE
   planning status and caveats. No network rerun for version naming.
3. T text_cost_evidence_attempt2.incomplete.json, digest
   **ed713a0a6fe22cdd396f758182b841b0208f0fe92795b3784f8d71bb6bbf665b**.
   Bind as v2.0 INCOMPLETE physical-cost motivation, never as completed text
   acquisition or a v2.1 success. Its numeric chunk evidence may inform new
   offline plans; corrected derived checks belong in new child artifacts.
4. The exact selection manifest above.
5. Earlier M/T incomplete planning receipts as accounting-history parents.

Adoption grants evidentiary reuse, not an exemption from historical ceilings,
authorization, decoder safety, unchanged remote identity, or missing meters.
Revalidation later requires separate authorization and remaining budget.

M's COMPLETE pass records 48 requests / 1,922,640 bytes and eight
future_plan_feasible=true flags. Its projected transfers actually range
582,200..1,657,177 bytes (0.555..1.580 MiB), not 0.56..0.58 MiB throughout.
These are planner estimates; do not promote them to independently certified
whole-chunk/resource bounds. The earlier M incomplete attempt records four
requests / 268,808 bytes but predates the redirect accounting fix.
Its first-file three successful logical footer ranges plus the three
redirects described in the implementation report imply six physical requests;
with six in the COMPLETE pass this would be twelve for the same file, above
the unchanged ten/file planning cap. The old receipt lacks per-range details.
Historical resource compliance is therefore **BLOCKED**, pending authoritative
reconciliation; if the implied overrun is confirmed it cannot be cured under
this T-only amendment. Do not reset M, silently waive its cap or rerun planning.
The observations remain reusable with this disclosed limitation.

New v2.1 child receipts must distinguish adopted_observation,
historical_incomplete_cost_map, and budget_history roles, bind their
parents and selection separately from the new protocol/freeze, and preserve
the original statuses. No arbitrary version mixing or implicit relabeling.
Every new execution plan needs its own code/environment hashes, exact
range/resource schedule, durable carry-in ledger and authorization binding.
This freeze is not such a plan and does not designate an acquisition root.

## 6. Implementation handoff and stop

Use the exact Muse prompt in the accompanying
[amendment review](ESSENTIAL-WEB-EVIDENCE-V2.1-REVIEW.md).
Implement only the versioned cap/lineage/accounting behavior and necessary
focused verification, using the existing reader and acquisition infrastructure.
No product implementation is performed by this review.

Before acquisition, resolve or explicitly retain the inherited accounting,
range scheduling, memory, disk and deadline blocks. Produce a concrete
implementation report and hash-bound offline plans first. Future execution
still requires separate user authorization. No network, acquisition, corpus
text inspection, selector/sample changes, tokenizer, training, admission,
labeling, push or full research campaign is authorized here.

History remains: **v2.0 -> physical transfer refusal**;
**v2.1 -> bounded transport amendment, implementation pending**.

