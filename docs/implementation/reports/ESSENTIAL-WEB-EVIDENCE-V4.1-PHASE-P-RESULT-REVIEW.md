# Essential-Web v4.1 post-live Phase-P result review

**PHASE-P RESULT REVIEW PASSED — READY FOR PHASE-D AUTHORIZATION REVIEW**

2026-09-29. Read-only review of actual completed evidence at
`G:\Project\xlm-evidence-v4.1\essential-web`. Reviewed implementation HEAD
`91be1a1c5909a942561c2e37445cf543d9731372`, branch `data/mix01-ultrax-6b`.
Production code remains identical to v4.1 implementation `9c420c0`; the later
commit is test-only. The passed host-amendment review was not reopened.

No network, acquisition, Phase-P rerun, Phase D, corpus document text, scientific
reselection, implementation change, G: mutation or push. Review artifacts and
the structural dry plan are written only under the repository on F:.

[Commands and evidence](../evidence/ESSENTIAL-WEB-EVIDENCE-V4.1-PHASE-P-RESULT-REVIEW/COMMANDS.md),
[machine-readable result](../evidence/ESSENTIAL-WEB-EVIDENCE-V4.1-PHASE-P-RESULT-REVIEW/review-result.json),
[exact Phase-D dry plan](../evidence/ESSENTIAL-WEB-EVIDENCE-V4.1-PHASE-P-RESULT-REVIEW/phase_d_dry_plan.json).

## Run identity, completion and accounting

SQLite was opened with `mode=ro&immutable=1`, without the acquisition engine or
state writer. No journal/WAL sidecar exists. SQLite integrity and foreign-key
checks pass. The single run row is non-synthetic **COMPLETE**, stop reason null:
created `2026-09-29T19:40:03.449281Z`, updated
`2026-09-29T19:40:45.432227Z` (41.982946 seconds by recorded UTC timestamps).

Independent stdlib canonical hashes match these requested bindings. All four
output documents bind the exact v4.1 profile/source/scientific namespace.
SQLite stores version/plan/selection/revision; the documents also bind protocol
and freeze hashes. No alternate version or scientific source appears.

| Binding | Verified value |
|---|---|
| Protocol SHA-256 | `3d667a263b92696a2d7a266f896e460b10a81ae38097f788c8b885ce768079cf` |
| Freeze digest | `285015604d30e1699b5f63fa4f25ec9c747c778be2f372bb87119adc0e455f80` |
| Plan digest | `762cef78051011d518c4918a41aa87cf81fbb3224bf438af8cc1c499cfca7711` |
| Membership/ranges digest | `304bd0761125b48720a4de9c82bb6b3bf47100b8a15fc0953d548a8a1dab38bd` |
| Selection digest | `975ba3dee4af0598e665ea05c69bbe49336c3e3a35fca777a863073190b78474` |
| Source revision | `ce4eccc7e9604667b6d7f32cb6274b8b41f3113d` |

Every logical operation equals its frozen operation. M has exactly two per file:
0–3 and the frozen footer/trailer. T has exactly three per file: 0–3, N−8..N−1
and N−8−L..N−9, independently derived from each retained trailer. Each operation
has one SUCCESS and one retained output. There are no extra operations, missing
sources, failed/retried attempts or IN_PROGRESS rows. Attempts have contiguous
IDs 1–80; the canonical JSONL export equals the complete SQLite attempt table.

| Arm | COMPLETE operations | Physical attempts | REDIRECT / SUCCESS | Response-body bytes | Retained payload bytes |
|---|---:|---:|---:|---:|---:|
| M | 16/16 | 32 | 16 / 16 | 1,415,952 | 1,398,416 |
| T | 24/24 | 48 | 24 / 24 | 1,496,825 | 1,470,541 |
| All | 40/40 | 80 | 40 / 40 | 2,912,777 | 2,868,957 |

The 43,820-byte difference is exactly the retained origin redirect bodies:
M 17,536 and T 26,284. All 40 redirect temp files and 40 promoted payloads have
the recorded sizes/SHA-256. No attempt body is omitted or silently discarded.

## Live identity and evidence qualification

Every observed chain is **huggingface.co (302) → us.aws.cdn.hf.co (206)**.
The origin path equals the frozen repository/revision/file resource; each
successful target is an exact frozen signed-target host. There is one redirect
and no extra retry per operation. No fallback or replacement is recorded.

HTTP 206, requested ranges, source bindings, payload lengths/hashes and prescribed
PAR1 are directly reproducible from SQLite, receipts and retained bytes.
The frozen schema does **not** retain original Content-Range, ETag,
Content-Encoding, Content-Length or Location headers, or the URL port. Their
validation is evidenced by SUCCESS/REDIRECT outcomes under the already-reviewed
production path: SUCCESS is recorded only after exact range/total/strong-ETag,
coding/length, URL-policy and structure checks. Their raw wire values cannot be
independently replayed from this root. This is the existing v4.1 receipt evidence
model, not a new claim of packet capture or authenticated execution attestation.
There is no actual result contradicting the passed implementation review.

## M structural result and exact Phase-D ranges

An independent metadata-only PyArrow traversal reproduces every exported M
layout. All windows remain 512 rows inside exactly one row group, totaling
4,096 rows. Projection remains `eai_taxonomy`, `quality_signals`. Each file has
**81 projected chunks**, all exactly adjacent, with the frozen compressed and
uncompressed totals. No selected chunk overlaps the acquired footer.

Ranges below are **half-open [start,end)**; HTTP end is `end−1`. M-00..07 are
the frozen plan ordinals, with full source paths/ETags/lengths in the dry plan.

| File | Row group | Frozen row window | Exact coalesced byte range | Bytes |
|---|---:|---|---|---:|
| M-00 | 9 | [90023,90535) | [289898947,290481147) | 582,200 |
| M-01 | 9 | [92096,92608) | [315229381,316525770) | 1,296,389 |
| M-02 | 6 | [67173,67685) | [205515226,207095760) | 1,580,534 |
| M-03 | 1 | [11728,12240) | [60529942,62163812) | 1,633,870 |
| M-04 | 2 | [26634,27146) | [95348280,96991689) | 1,643,409 |
| M-05 | 4 | [46415,46927) | [156189931,157839897) | 1,649,966 |
| M-06 | 5 | [58279,58791) | [192193472,193850649) | 1,657,177 |
| M-07 | 1 | [19154,19666) | [62433757,64082742) | 1,648,985 |

**648 chunks; eight proposed logical requests; 11,692,530 exact successful body
bytes.** Coalescing adds zero bytes and no column content; every request is under
4 MiB. The dry plan also enumerates all 648 individual leaf-column ranges, so
the union equality is reviewable. No M data was fetched or decoded here.

## T structural result and exact Phase-D ranges

Retained L/trailer/footer boundaries and every exported T layout reproduce.
Each frozen selected span is exactly one `text` chunk with a valid dictionary
offset preceding its data-page offset. The original locator-only manifest
reproduces its digest and 118 unique locators. Each locator maps to the expected
file and row group; the dry plan records its row offset within that group.

| File | Row group | Dictionary-inclusive span [start,end) | Ranges | Bytes | Locators |
|---|---:|---|---:|---:|---:|
| T-00 | 5 | [156637786,179150784) | 6 | 22,512,998 | 16 |
| T-01 | 8 | [259990771,283182502) | 6 | 23,191,731 | 14 |
| T-02 | 5 | [145763654,165808388) | 5 | 20,044,734 | 11 |
| T-03 | 6 | [190807685,213037997) | 6 | 22,230,312 | 11 |
| T-04 | 6 | [197584400,221221447) | 6 | 23,637,047 | 14 |
| T-05 | 4 | [129999706,152903349) | 6 | 22,903,643 | 13 |
| T-06 | 1 | [32748062,56440573) | 6 | 23,692,511 | 18 |
| T-07 | 1 | [31508248,53258441) | 6 | 21,750,193 | 21 |

**47 dictionary-inclusive requests; 179,963,169 exact compressed bytes.** Each
span is split from its start into 4,194,304-byte pieces plus its final remainder.
All 47 exact endpoints equal the prior frozen planning ranges; no structural
change or range expansion is needed. Do not merge across the proposed 4 MiB
ceiling. No selected document, page contents, statistics or KV metadata was
inspected. These requests would acquire the existing selected row-group chunks;
future decoding/output must retain only the frozen 118 selected rows.

## Isolation, limits and integrity

All successful Phase-P payloads are heads, trailers or parseable footers.
Independent traversal checks that every column's structural chunk ends before
the acquired footer, and no requested successful range includes M projected
data or T text pages. JSON documents have the expected structural fields;
there are no extra root files/logs or extra SQLite tables. No document text was
rendered. Raw footers may contain permitted statistics, which were not accessed.
Redirect bodies were hashed as opaque bytes, never decoded or copied to reports.

| Operational requirement | Observed |
|---|---|
| ≤200 attempts/arm | M 32; T 48 |
| ≤67,108,864 response bytes/arm | M 1,415,952; T 1,496,825 |
| ≤4,194,304 bytes/response | Maximum 216,880 |
| ≤3 redirects/logical request | Maximum 1 |
| ≤2 extra tries | 0 |
| 120-second physical-attempt deadline | Maximum recorded UTC duration 1.183811 s; retained COMPLETE outcomes under reviewed deadline implementation |
| ≤268,435,456 retained-root bytes | 86 files, 3,241,704 file bytes; 3,399,872 allocated file bytes (Windows FILE_STANDARD_INFO) |
| ≥1 GiB free before execution | Reviewed engine precheck applies; current free bytes 999,665,057,792; historical minimum not separately logged |

Root byte accounting includes SQLite, manifest, exports and all temporary bodies.
Allocated bytes exclude filesystem directory/MFT overhead. Before/after inventories
match for file hashes, sizes, mtimes and directory metadata. No G: file was opened
for writing. The current size/free-space measurements are not peak/minimum telemetry.

All four JSON self-digests, manifest file bindings, layout payload references,
SQLite outputs and request-export hashes reproduce. The manifest binds the
COMPLETE receipt. The manifest deliberately excludes itself and SQLite under the
frozen format; this review additionally hashes both in its root inventory/dry-plan
parents. No v4.0 output is imported as a v4.1 output.

| Phase-P parent | Canonical digest |
|---|---|
| Receipt | `ebd5704be8a871034ef8048c0641e9d735d017f458003b4efc50ab124c8bd965` |
| Manifest | `1bb6bad6e3690758c005e260c272eac3c0adcb6d040fde960a502d0ced90d7fe` |
| M layout | `2c9f2f79448e1bb1d59b57f1ddec276dcd05bc79dddaea4014c9942a340d340a` |
| T layout | `364bda46c766e8f9fdebd5732408743420cbe5c6f0b00fbd6a10d09a8bd53274` |

## Phase-D planning package and remaining authorization work

Dry-plan digest:
**`ee82125d1b0d2b6d0a6c420144910db954659d5b757283d39123ddc3d0080356`**.
Status **DRY_NOT_AUTHORIZED**. The 124,373-byte package binds the Phase-P receipt,
manifest, layouts and SQLite hash; exact source identities; all request endpoints;
648 M column chunks and 118 T locator mappings; unchanged membership; host policy;
and proposed durable, cumulative restart semantics. It copies no retained payload.

M+T: **55 logical requests, 191,655,699 exact successful payload bytes**. If each
request again has one redirect and no retry, there would be 110 physical attempts
(M 16/T 94). This is a scenario, not a measured future result. Redirect/error/partial
bodies make total returned bytes unknown in advance. Under a proposed three-try,
three-redirect policy, conservative maxima are M 96/T 564 physical attempts;
at 4 MiB per physical body, M 402,653,184/T 2,365,587,456 bytes. These arithmetic
bounds are **not authorized caps** and do not justify silently changing a cap.

The new Phase-D protocol must freeze its own root, request/arm/memory/scratch/final
disk/document/output limits, parsing/output scope and stop/restart policy.
T's successful payload alone exceeds the Phase-P 64 MiB arm limit. Phase-P caps
cannot be reused unchanged or broadened implicitly. Proposed restart behavior:
verify parent and completed-range hashes; skip valid completed ranges; preserve and
charge every partial/redirect/error/retry body; use new attempt IDs for interrupted
work; stop on mismatch or exhausted cap. No scientific membership changes.

## Requirement ledger and execution record

| Question | Status | Conclusion |
|---|---|---|
| Exact frozen plan executed | VERIFIED | Run, operations, sources, document bindings and derived ranges reconcile |
| Every operation scientifically complete once | VERIFIED | 40 COMPLETE operations, one SUCCESS/output each |
| Physical attempts accounted | VERIFIED | 80 rows/IDs, exact JSONL equality, all bodies accounted |
| Remote identity | VERIFIED within frozen receipt model | Direct retained-byte checks plus recorded validations; raw headers not persisted |
| M/T layouts valid | VERIFIED | Independent structural metadata traversal equals exports |
| Scientific isolation | VERIFIED | Only structural ranges acquired; no text/statistics access in review |
| Output hash/receipt consistency | VERIFIED | Full root/manifest/SQLite/layout reconciliation |
| v4.1 operational limits | VERIFIED within recorded evidence | Measured counts/bytes and recorded outcomes; temporal limits qualified above |
| Exact Phase-D physical plan | IMPLEMENTED, VERIFIED as dry artifact | Digest-bound ranges and locator mappings; no authorization |
| Blocking Phase-P defect | None | No BLOCKED result requirement |
| Phase-P rerun, new network, full/focused pytest suite | NOT RUN | Read-only result review; no implementation change |
| Phase D, text decoding, GPU/training | OUT OF SCOPE / NOT RUN | Separate future authorization |

Environment: Windows 11 build 26200 AMD64, CPython 3.12.13, PyArrow 25.0.1,
uv 0.12.19, existing locked CPU/eval environment. Final read-only review script
exit 0, measured 0.291761 s inside main, peak process working set 58,224,640 bytes.
These are review costs, not live acquisition performance. No dependency/pin change.

One initial review-script assertion incorrectly expected NULL error fields for
redirects. The production schema uses empty strings for successful redirect
outcomes and NULL for SUCCESS; the assertion was corrected to require those exact
values. That run exited 1; both subsequent runs exited 0. The final run added
explicit output-schema checks and true allocated-file-size measurement. Logs and
exact commands retain all results; no production fix or assertion suppression.

Only the new report/evidence package is staged for the review commit. The STATUS
notice is updated locally while preserving the pre-existing dirty notice and all
other user files; prior untracked reviews remain outside this commit. No push.

Exact next operator prompt:

> Prepare the separately gated Phase-D protocol and authorization package from
> dry-plan digest ee82125d1b0d2b6d0a6c420144910db954659d5b757283d39123ddc3d0080356.
> Bind its exact COMPLETE Phase-P parents, eight M requests and 47 T requests;
> preserve all scientific identities. Freeze explicit transfer/request/resource/
> output limits and restart semantics, then perform the Phase-D authorization
> review. No acquisition or document-text inspection until separately authorized.

**PHASE-P RESULT REVIEW PASSED — READY FOR PHASE-D AUTHORIZATION REVIEW**
