# Essential-Web evidence v2.2: protocol and acquisition-readiness review

2026-09-28. Starting/current implementation HEAD:
9e008987454fa8370438ec1f25a61a71196be5ec.
This is the task-specific AGENTS.md PXX report, not a new research milestone.
Decision: **READY TO IMPLEMENT EVIDENCE V2.2**; neither arm is ready to acquire.
No implementation, network, acquisition, corpus text, selection change or push.

## 1. Independent M history audit

Direct inputs: v2.1 protocol/freeze and bound parents; implementation report;
two v2.1 children; real external M complete/incomplete and T cost/selection
receipts; pre-fix and post-fix local Git source objects. Raw/canonical hashes
and the existing v2.1 descriptor graph match. The exact review calculations
and source bindings are in
[verification.json](../evidence/ESSENTIAL-WEB-EVIDENCE-V2.2/verification.json)
and [COMMANDS.md](../evidence/ESSENTIAL-WEB-EVIDENCE-V2.2/COMMANDS.md).

The old hop counter incremented before the refusal and parent handler.
The refused fourth transition did not produce a fourth follow. Distinguish
a handler invocation from an issued HTTP request:

- Old first file: 3 original footer ranges + 3 completed follows = 6.
- Old second file: 1 original failed request; refused follow = 0 issued.
- Old pass: **7**, not 8; complete pass: **48**; cumulative: **55**.
- First file: **12 >10**, a proven historical request-limit violation.
  The implementation report's phrase “4 proven redirect follows” is wrong;
  its first-file conclusion A remains correct.

| Crawl | Historical physical requests | Recorded range-body bytes |
|---|---:|---:|
| 2014-15 | 12 | 537,616 |
| 2015-32 | 7 | 264,322 |
| 2016-50 | 6 | 215,631 |
| 2018-05 | 6 | 264,763 |
| 2019-09 | 6 | 228,714 |
| 2021-04 | 6 | 221,283 |
| 2021-49 | 6 | 231,779 |
| 2024-26 | 6 | 227,340 |
| Total | **55** | **2,191,448** |

The full source paths, fixed windows, ETags/lengths and remaining per-file
budgets are recorded in the audit and normative protocol.

**Exact all-response-body historical totals cannot be established from these
receipts.** The table is the exact recorded range-body contribution. Redirect
bodies were metered at the transport layer but omitted from the persisted arm
receipt. Producer SafeRedirectHandler reads them using the transport-wide
byte allowance; there is no historical 4096-byte/request enforcement.
The failed request's response-body amount is not established either.
Recorded M bytes are a lower bound on all-body consumption, not a complete
reconciliation. Same for residual T bodies. Any additional attempts evidenced
later also carry forward. No guessed small bound or budget reset is accepted.

## 2. Decision and future request budgets

A footer-only deviation does not reveal new selected metadata or selector
outcomes. The eight M files and windows remain unchanged. A transparent
prospective resource amendment preserves scientific preregistration while
retaining the historical operational violation. Replacing M would spend new
planning effort and change sampling identities without fixing the machinery.
Abandoning the scientific lineage is not justified by this physical-only event.

Choose **v2.2** with M footer requests/file **10 ->16**.
The derivation is **12 consumed + 1 original request + up to 3 redirects**.
This is a meaningful complete revalidation-chain allowance, not merely two
extra requests to relabel an overrun. Under observed one-hop routing it allows
two nominal attempts within the four-request future reservation.

Unchanged: **80 footer requests/arm, 100 data requests/plan, 800 data
requests/arm, 880 combined/arm**, three redirects and two retry ceilings.
All M bytes/resources and all T caps are unchanged.

Minimal future M revalidation: one pinned four-byte header range/file, checking
PAR1, exact Content-Range/length and the adopted strong ETag. Revision/path and
all plan bindings are verified locally. No separate HEAD, full footer refetch,
new layout/window discovery or duplicated identity query is necessary.
The current transport needs a tested comparison against adopted identity;
this is a proposed implementation schedule, not a live compatibility result.

Nominal: 2 physical requests/file, 16/arm; cumulative footer **71/80**.
Absolute prospective limit: **4/file and 25/arm**, including all retries and
redirects. All-eight maximal three-hop chains need 32 and cannot be promised;
fail closed at the first binding limit. After nominal revalidation, footer
room is 2 requests on the first file, 7 on the second, 8 each elsewhere,
but only **9 arm-wide**. No file can borrow beyond the arm budget.

M data estimate: 8*85=680. Total **751 nominal**, or **760 with all 80
footer requests used**, fits 880. At the full 800 data cap, total is 871
nominal or exactly 880 with the full footer allowance. No arm increase needed.

The 85 figure is not a certified physical schedule: sampling.py counts leaf
ranges plus four controls, with no redirect multiplier. One redirect per
estimated request would exceed 100/file. Implementation must use the existing
validated resource/target-reuse transport where applicable and derive a
physical plan that fits, or refuse. Do not enlarge the 100 cap or conceal hops.

## 3. Arm T disk and readiness audit

Disk decision: **stage-gated acquisition/publication, with the same cumulative
16 MiB final and 512 MiB combined caps; no numerical increase**. Future human
labels do not exist at acquisition. Their unknown size does not itself prove
that acquisition must wait or that the cap must grow. A valid acquisition-stage
worst-case reservation is still mandatory. Later artifact writes/publication
must reserve against all earlier retained artifacts, refuse before overflow
and leave the review INCOMPLETE if substantive outputs cannot fit. Never shrink
labels, drop documents or claim success to fit storage.

A “package at the full 16 MiB cap plus other artifacts” stress case should
refuse; it is not a lower bound on necessary output size. No fresh cap per stage,
no uncounted copies, caches or staging. The <=8 MiB retained text cap stays.

The reported **102,098,902** high water and final **10,581,984** are unbound
model claims: the delivered child has no disk schedule or exact inputs to
reproduce them. The current disk helper also demonstrably understates some
scratch peaks and ignores intermediate final overflows. Therefore the present
implementation does not yet justify acquisition on disk grounds.

Independent readiness findings:

| Surface | Independent result |
|---|---|
| Frozen identity | VERIFIED: 118 unique locators exactly match selection, cost map and T child; original selection bytes/digest unchanged. |
| Range coverage | BLOCKED: all eight schedules begin at data_page_offset and omit dictionary prefixes; 17,539,154 required bytes omitted in total, with equal excess past chunk ends. Correct complete-chunk spans retain 47 ranges and 179,963,169 compressed bytes. |
| Carry-in | 54 requests / 2,231,492 recorded bytes reproduced. Residual 48*4096 and 6*4096 body bounds are unsupported and not charged/reserved. ready=true does not certify history. |
| Durability | BLOCKED: save/reload probe loses live requests/bytes/time: 1/123/7 becomes 0/0/0. Adoption replay alone is not durable execution accounting. |
| Requests | 47+16=63 is logical/nominal; +54=117 omits redirects. One redirect per logical request makes 180. Need a physical schedule and correct per-file histories/control allocation. |
| Transfer | Bound 213,517,601 fits. Data headroom **38,140,639**, not 35,909,147: footer history does not spend the data subcap. Combined headroom **52,686,363** is conditional on recorded history completeness and future controls. |
| Decompression / scan | Numerically fit: 311,068,163 sum / 41,297,124 max bytes; 38,400 sum / 8,448 max rows. Whole-chunk bound is not extra allowance for repeated decode. |
| Memory | Formula reservations **93,613,610..105,045,302 bytes =89.28..100.18 MiB**. Report conflated decimal MB and MiB and overstates RSS margin. No live allocator/supervisor proof. |
| Deadlines | 1800/600/30 caps exist; historical elapsed zeros are unmeasured and current child lacks durable deadline state. A guard receiving a number is not supervised execution. |
| Parent binding | Both child parents objects are empty; T references carry digest but no captured carry artifact was delivered alongside it. New children must bind fully resolved lineage/code/environment/identity evidence. |
| Overall | **BLOCKED for acquisition-readiness**, beyond absence of authorization. |

The dry T child must be preserved as historical evidence and regenerated after
repairs, not quietly edited into a success. The reported per-file remaining
budgets also do not consistently match its own JSON; derive all new summaries
from the regenerated machine-readable plan.

## 4. Lineage, freeze and files

The normative [v2.2 protocol](ESSENTIAL-WEB-EVIDENCE-V2.2-PROTOCOL.md) and
[new freeze](../evidence/ESSENTIAL-WEB-EVIDENCE-V2.2/freeze.json) bind v2.1,
all M history/observations, the first-file audit capture, complete M accounting
from this review, blocked M child, T child/cost/carry evidence, unchanged
selection, operational maps, revalidation schedule, rationale and remaining
unknowns. Captured v2.1 carry/audit results retain their original claims;
this review explicitly rejects insufficient readiness claims.

History: v2.0 T infeasible and defective M planning; v2.1 T amendment and
confirmed M overrun, acquisition blocked; v2.2 prospective M allowance and
accounting clarification. Old artifacts remain unchanged. A new ceiling does
not make twelve requests comply with the old ten-request ceiling.

Files created: this REVIEW, v2.2 PROTOCOL, evidence COMMANDS.md,
verification.json, v21_m_audit_capture.json, v21_t_carry_capture.json and
freeze.json. STATUS.md updated. No source/test/dependency edits.
Existing dirty selector-review documents preserved. No commit or push.

Protocol file SHA-256 and freeze canonical digest are published in STATUS.md
and the final response outside the freeze-bound artifacts to avoid a cycle.

## 5. Exact Muse handoff

> Implement only essential-web-evidence-v2.2 from
> docs/implementation/reports/ESSENTIAL-WEB-EVIDENCE-V2.2-PROTOCOL.md and
> docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V2.2/freeze.json.
> Read AGENTS.md, CONTRACTS.md, STATUS.md, the v2.0/v2.1 frozen protocols,
> v2.1 implementation report and this independent review first. Verify all
> freeze/parent/selection/child/code-evidence bindings before changing code.
> Keep older versions readable and behaviorally distinct; no global defaults.
> Preserve all eight M file/window identities, exact 118 T locators, scientific
> namespace essential-web-evidence-v2.0, revision, policy, seeds, strata,
> rubric, review IDs/order and M-before-T-unblinding rule.
>
> Change only M footer requests/file from 10 to 16 under v2.2. Keep footer
> arm 80, data 100/plan and 800/arm, combined requests 880; all other M
> and T caps remain unchanged. Preserve the historical 12>10 violation.
> Carry the reconstructed M 55 physical requests, exact recorded 2191448
> range-body bytes, all source receipts and any further evidenced usage.
> Exact all-response-body history is NOT established: missing redirect/error
> meters and timing must remain blocking unless authoritative local evidence
> supplies exact values or justified conservative bounds reserved against
> every relevant cap. Reject the unsupported historical 4096-byte/request
> estimates. Never reset counters or silently declare missing costs zero.
>
> Implement M's exact minimal revalidation: local hash/revision/path/plan
> checks then one bytes=0-3 GET/file, final 206/PAR1/exact Content-Range length
> and adopted strong ETag, all hosts/redirects validated. Subsequent data
> responses must preserve identity. Nominal 2 physical requests/file;
> maximum prospective 4/file and 25/arm including retries/redirects, within
> cumulative 16/80. Charge only actually issued original/follow requests;
> refused redirect targets do not count as followed. Refuse before caps,
> missing identity or accounting gaps. Do not refetch full footers or reseed.
> Derive actual M data physical schedules under unchanged 100/800/880,
> using existing validated target reuse as applicable, or keep blocked.
>
> Repair the reviewed T obligations without changing the reader or selection:
> include dictionary-first complete chunk extents; preserve all 47 corrected
> bounded ranges and verify exact coverage without adjacent-column overread;
> separate footer/data/combined budgets; replace nominal logical counts with
> charged physical requests; persist all live events, partial failures,
> retries, bytes, elapsed work and in-flight uncertainty across restart.
> Preserve event IDs, refuse conflicting replays, and atomically reserve work
> before issuance. Enforce body/range bounds at the transport, including
> redirect/error bodies. No extra HTTP/decoder stack or dependency defaults.
>
> Keep final disk 16777216, combined 536870912 and retained text 8388608.
> Correct cumulative phase inventories, scratch peaks, intermediate final
> occupancy, encoded text/artifact overhead and atomic old/new/tmp copies.
> Before acquisition bind its own complete conservative stage reservation.
> Later labels/adjudication get no fresh allowance: reserve before each
> write/publication and stop INCOMPLETE without semantic truncation if they
> do not fit. Unknown future labels alone need not preclude acquisition.
> Bind real allocator/process-base reservations and external RSS/deadline
> supervision or refuse; helpers accepting supplied measurements do not
> certify execution. Unknown historical durations remain explicit blockers.
>
> Use authored synthetic focused regression tests for v2.0/v2.1 stability,
> v2.2 16/80/100/800/880 boundaries, fourth refused versus followed redirect,
> revalidation identity/range/drift, unknown histories and unsupported bounds,
> dictionary coverage, actual request charging, crash/restart live-state
> conservation, replay conflicts, data/footer separation, disk cumulative
> phases/atomic copies and supervisor/deadline fail-stop. One pytest
> controller with explicit workers; single nodes -n 0. Set OMP/MKL/OPENBLAS/
> NUMEXPR thread counts to one and TOKENIZERS_PARALLELISM=false. Use
> uv run --offline --locked --no-sync --extra cpu --extra eval with pinned
> Python 3.12.13; record actual environment. No full/live/CUDA suite by default.
>
> Generate NEW offline v2.2 child plans or explicit blocked receipts, binding
> every parent, reconciliation, code/environment, length/ETag, range schedule,
> category counters, storage schedule and deadline state. Preserve old
> receipts/children. Keep executable=false and authorization=NONE.
> Update implementation report, requirement ledger and STATUS with exact
> commands/statuses and remaining evidence gaps. Do not claim readiness when
> history cannot be resolved offline. No network, acquisition, corpus text,
> selector/sample change, training/tokenizer/admission, push or campaign.
> Stop after implementation and focused verification; return regenerated
> child plans for offline review, then a separate acquisition authorization
> review. This prompt authorizes neither acquisition nor silent cap growth.

## 6. Evidence, commands, ledger and stop

Exact bounded audit command is in COMMANDS.md. Exit **0**.
It recomputed the input hashes and arithmetic, captured verify-v21-freeze,
audit-m-history and reconcile-carry-in (each exit 0), and ran three authored
synthetic implementation probes. Each probe reproduced a defect; the successful
audit exit means the defect was demonstrated, not that implementation passed.

Audit digest:
f1e570d6b9936c9fb3984d5bc364750f8e282077a4259c65ee278b07b2d5d5ff.
Environment: Windows 11 build 26200, Python 3.12.13, uv offline/locked/no-sync;
installed metadata torch 2.14.0+cpu, pyarrow 25.0.1. No dependency sync,
Torch/CUDA execution or new live dataset compatibility test.
Audit receipt bytes read: 2,209,727. Elapsed before output: 2.0569591999956174 s.
Endpoint current-process RSS 29,466,624; peak 32,063,488 bytes. These exclude
child processes and are not acquisition process-tree measurements.
Network requests zero; corpus-text bytes read zero. No synthetic or measured
timing is represented as dataset throughput.

| Requirement | Status |
|---|---|
| Prospective v2.2 protocol, exact caps/schedule, lineage, handoff | IMPLEMENTED as documentation |
| M known-event request reconstruction, recorded byte table | VERIFIED offline |
| M historical old-cap violation | VERIFIED; not retroactively cured |
| All-body historical M/T totals and elapsed-work reconciliation | BLOCKED; missing evidence |
| Selection/file/window identities and receipt integrity | VERIFIED |
| Range/durability/disk findings | VERIFIED by source/receipt checks and bounded synthetic probes |
| v2.2 product implementation / new child plans | NOT RUN; next Muse task |
| Actual acquisition readiness / authorization | BLOCKED / not granted |
| pytest fast/full, live, CUDA or campaign | NOT RUN |
| Text inspection, selector/sample change, training/admission, push | OUT OF SCOPE |

Freeze construction and final descriptor/parent/source-preservation validation
commands are also in COMMANDS.md; their exact exit statuses are recorded there
after execution. No skip is counted as a pass. Old source and frozen documents
remain untouched. Next action is the exact handoff above; the stop point is
protocol/freeze delivery, not execution.

