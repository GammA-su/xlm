# Essential-Web evidence v4.0 — minimal Phase-P execution protocol

Frozen 2026-09-29 on `data/mix01-ultrax-6b`, parent commit
`60ed59c5078114dd54297b8923c45495014f7874`. Protocol version
`essential-web-evidence-v4.0`. This document is normative for the v4.0
Phase-P execution contract only. It does not change the scientific sample.

Bound artifacts (directory
[`docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V4.0/`](../evidence/ESSENTIAL-WEB-EVIDENCE-V4.0/)):

| Artifact | Canonical self-digest |
|---|---|
| `phase_p_plan.json` (the only source of executable operations) | `16b93183b485ced924466028b84a8b4cfd84a65f596dc95c80879e32464f35d3` |
| `scientific_adoption.json` | `158c3fc9fed030d0290aa50eded70f7d94b162ab6186ae38aca2ce6b1ebdffd3` |
| adopted scientific identity object (`H(scientific_identity)`) | `080caebb962c86562b530ddfa6a130118a49a6d207b044e83ce6a90a6d9691c6` |
| `freeze.json` / `verification.json` | bind this document's SHA-256 (not stated here: circular) |

Canonical form, identical to v2.0/v3.0: `C(x)` = UTF-8 of
`json.dumps(x, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
allow_nan=False)`; `H(x)` = SHA-256 hex of `C(x)`; an artifact's self-digest is
`H` of the object without its top-level `digest`. The builder
`build_v4_freeze.py` in the same directory reproduces every artifact offline.

## 1. Lineage and why v4 exists

| Version | Status |
|---|---|
| v2.x | `CLOSED_NON_EXECUTABLE`, `HISTORICALLY_UNCERTIFIABLE` (transport accounting of the historical runs cannot be certified; closure digest `ceaa2fbc6c0f638d333b3e08fd1591d2619bfe645c75ce7028c0e2bae96dda9b`). |
| v3.0 | Scientifically valid frozen **planning** lineage (freeze `aa977972883af209afffebae221683658b2637281ae8e72741150750534fd834`, protocol SHA-256 `c191aa49a7e35349e27eb077c7105ead1a15a7edbbb28ef0bc78f157b219fd79`). Its **execution implementation is abandoned before any real acquisition**: implementation `3dd5ebce0edb7d8e676966c9195b73fb5a1978c9`, children `12d85158206378c0d6833d95df9b9d8fa5789761`, independent authorization review 3 `60ed59c5078114dd54297b8923c45495014f7874` = **PHASE-P AUTHORIZATION BLOCKED** (R3-01..R3-08). v3 never passed authorization, never performed real epoch genesis, made zero real network requests, inspected no selected M outcome and no selected T document text. Its root `G:/Project/xlm-evidence-v3/essential-web` is not used by v4. |
| v4.0 | New prospective SIMPLE execution lineage. Adopts the exact v2.0 scientific membership (as frozen and carried by v3.0) and simplifies only the operational acquisition mechanism. |

Why v3 execution was abandoned: v3 attempted to *certify* an execution
framework — typed authorization/review/approval objects, exclusive genesis,
durable 30/600/1800-second active-time accounting, process-tree RSS
enforcement, disk sub-caps, future-Phase-D reservations and derived readiness
certification. Three independent reviews found each of those guarantees
difficult to make correct (deadline gaps at EOF/final write, sampled RSS
breaches after work completed, shallow immutability of trusted objects,
socket-step deadlines). None of those guarantees is part of the scientific
sample; they were operational controls. Continuing to perfect them delays the
experiment without improving scientific validity. v3 remains in the repository
unchanged as historical engineering evidence; nothing in v4 edits, deletes or
reinterprets a v3 artifact.

Why the scientific sample remains valid: selection happened entirely in the
v2.0 lineage from metadata/footer observations and a seeded policy, before any
selected outcome was seen. v3 and v4 changed only how bytes would be fetched
later. No v3 or v4 activity observed selected M outcomes or T text, so there is
no inspection that could have biased membership, and the membership below is
adopted byte-for-byte rather than regenerated.

## 2. Adopted scientific identity (unchanged)

Adopted from the committed v3.0 freeze (commit
`52569c525a6613faab096d17b60167b4aaa0f214`) and the v3.0 CHILD
`scientific_identity_adoption.json` (commit `12d8515…`), which are equal:

- scientific namespace `essential-web-evidence-v2.0`;
- selection digest `975ba3dee4af0598e665ea05c69bbe49336c3e3a35fca777a863073190b78474`
  (file SHA-256 `8424f9668fef6952a558ead0dae27a705545a7f4a4d302713707d3ec6bb1af27`, 23,807 bytes);
- source `EssentialAI/essential-web-v1.0` at revision `ce4eccc7e9604667b6d7f32cb6274b8b41f3113d`;
- selector policy digest `f4357f61f434d5105d823266153dac6372e122f12767187631e8796b4899dd07`;
- **M**: the same 8 files and deterministic 512-row windows, metadata seed
  20260927, projection `eai_taxonomy`, `quality_signals`, 4,096 rows total;
- **T**: the same 118 locators in the same 8 development files, the same
  strata, censuses, precedence, rubric, blinding and review-order construction
  (review-order seed 20260928), two reviewers plus adjudication.

No reselection, no replacement file or window, no new locator, no
inspection-driven adaptation. The 29 audited scientific code/dependency
bindings recorded by v3.0 still match HEAD (see `scientific_adoption.json`).

## 3. What v4 simplifies (guarantees intentionally NOT made)

The following are **outside** the v4 execution contract. v4 makes no claim
about them:

- cryptographic reviewer identity; typed reviewer-authorization, review or
  operator-approval objects; capability objects;
- exactly-once genesis; a multi-phase generalized state machine;
- process-tree RSS enforcement or continuous memory sampling;
- durable 30/600/1800-second active-time accounting (wall-clock timestamps
  are recorded; active time is not certified);
- future-Phase-D request, byte or time reservations;
- a general-purpose disk-reservation framework or disk sub-caps;
- readiness certification or a generic acquisition abstraction;
- bounded DNS resolution time (name resolution relies on the OS resolver);
- protection against a local user editing Python code or the SQLite file
  while the process is stopped (inconsistency detected on restart is STOP,
  but the store is not tamper-proof).

The operator authorizes execution by running the reviewed exact CLI command
with the exact plan digest. External review remains a documented process, not
a security protocol encoded in the downloader.

## 4. What v4 DOES guarantee

1. **Closed operation set.** The fetcher executes only the operations of the
   committed `phase_p_plan.json`, loaded from its fixed repository path and
   accepted only if its canonical self-digest equals the digest compiled into
   the implementation. No CLI option or supported API parameter accepts a URL,
   file, range, ETag, operation kind, plan override or output path.
2. **Exact ranges.** Every request carries exactly the frozen inclusive range,
   or, for the eight T footer operations only, the range mechanically derived
   from the validated trailer (section 6).
3. **Exact remote identity** for every successful response (section 7).
4. **Bounded network behaviour**: exact host allowlist, HTTPS/443, ≤3 redirect
   transitions per logical request, ≤2 additional tries per operation, 120-second
   per-attempt timeout, per-response and per-arm byte caps, per-arm attempt cap.
5. **Durable accounting**: every physical HTTP attempt is committed to SQLite
   before the request is issued; received body bytes are recorded durably
   during streaming and at completion; nothing is deleted.
6. **Restart without omission or silent duplication** (section 9).
7. **Isolation**: no M projected data chunk and no T text page can be requested
   (section 10).
8. **Hash-bound outputs**: COMPLETE only when all 40 operations are complete
   and verified (section 11).

## 5. Operational limits (non-scientific guardrails)

These numbers only stop runaway behaviour. They are not part of scientific
membership or outcome interpretation, reserve nothing for Phase D, and are
never raised silently. Hitting any of them is STOP.

| Limit | Value |
|---|---|
| physical HTTP attempts per arm (lifetime of the root, all tries/redirect hops/restarts) | 200 |
| returned response-body bytes per arm (all statuses) | 67,108,864 (64 MiB) |
| response body per physical response | 4,194,304 (4 MiB) |
| redirect transitions per logical request | ≤ 3 (a 4th redirect response is STOP) |
| tries per logical operation | 1 + ≤ 2 additional (retry delays 1 s, 2 s) |
| per physical attempt timeout | 120 s (socket timeout on each blocking step, recomputed from one absolute deadline; streaming aborts once 120 s have elapsed) |
| retained execution-root size | ≤ 268,435,456 bytes (256 MiB), measured over every file under the root |
| free space on the root's drive before a live invocation starts | ≥ 1,073,741,824 bytes (1 GiB) |
| durable byte-count update interval while streaming | ≤ 1,048,576 bytes |

Cap enforcement is pre-emptive and conservative: a physical attempt starts
only if (a) the arm has fewer than 200 recorded attempts, (b) the arm's
recorded body bytes plus 4,194,305 (one full response plus one overflow
detection byte) do not exceed 64 MiB, and (c) the measured root size plus
4,194,305 does not exceed 256 MiB. A response is read to at most 4,194,305
bytes; receiving the 4,194,305th byte is a per-response cap STOP. Hence no
recorded total can exceed its cap. For comparison only, v3's dry arithmetic
was 16 logical / ~24 observed-style physical / ≤ 40 cold for M; v4 does not use
those values as caps.

## 6. Exact Phase-P operations

40 logical operations in fixed order (`seq` 0–39): M files 0–7, then T files
0–7. Operation IDs are `M-NN-head`, `M-NN-footer`, `T-NN-head`, `T-NN-trailer`,
`T-NN-footer`. All output names derive from these IDs.

**M (16 logical operations)** — per file `HEAD_0_3` = bytes 0–3 (must be
exactly `PAR1`) and `M_FOOTER_AND_TRAILER` = the frozen range `[N-8-L, N-1]`
with frozen footer length L (the complete footer plus the 8-byte trailer):

| File | N | footer range | L |
|---|---|---|---|
| `data/crawl=CC-MAIN-2014-15/train-01860-of-02772.parquet` | 290684578 | 290481302–290684577 | 203268 |
| `data/crawl=CC-MAIN-2015-32/train-01682-of-01920.parquet` | 316724715 | 316525925–316724714 | 198782 |
| `data/crawl=CC-MAIN-2016-50/train-02156-of-03132.parquet` | 207246017 | 207095918–207246016 | 150091 |
| `data/crawl=CC-MAIN-2018-05/train-03378-of-03429.parquet` | 285464583 | 285265352–285464582 | 199223 |
| `data/crawl=CC-MAIN-2019-09/train-00153-of-02577.parquet` | 247450105 | 247286923–247450104 | 163174 |
| `data/crawl=CC-MAIN-2021-04/train-00179-of-03315.parquet` | 246957884 | 246802133–246957883 | 155743 |
| `data/crawl=CC-MAIN-2021-49/train-00408-of-02895.parquet` | 247477157 | 247310910–247477156 | 166239 |
| `data/crawl=CC-MAIN-2024-26/train-01127-of-03168.parquet` | 246493238 | 246331430–246493237 | 161800 |

M Phase-P payload if every operation succeeds first time: 1,398,416 bytes.
After the footer response is identity-verified, its trailer must state exactly
L, and the footer must parse (metadata only) and reproduce the frozen window
bindings: the 512-row window lies inside exactly one row group; that row
group's projected leaf chunks (`eai_taxonomy.*`, `quality_signals.*`) number
exactly `data_chunk_count` (81) with exactly the frozen compressed and
uncompressed byte sums; chunks do not overlap and end before the footer. The
reconstructed projected-chunk offsets are frozen into `m_phase_p_layout.json`.
No selected metadata row payload and no Phase-D projected chunk is fetched.

**T (24 logical operations)** — per file `HEAD_0_3` = bytes 0–3 (`PAR1`);
`T_TRAILER` = bytes N-8..N-1 (must end in `PAR1`); `T_FOOTER_FROM_TRAILER` =
bytes N-8-L..N-9, where L is the little-endian uint32 in trailer bytes 0–3.
This is the only dynamic range. It is valid only if 1 ≤ L ≤ 4,194,304,
N-8-L ≥ 4 and N-8-L ≥ the end of the frozen selected text-chunk span
(`data_span_half_open[1]`). Otherwise STOP. The derived range is committed to
the operation row in the same transaction that completes the trailer. No
document-selection decision depends on L. Frozen T files, lengths, strong
ETags and text-chunk spans are listed in `phase_p_plan.json`.

After the footer response is identity-verified it must parse (metadata only),
contain exactly one `text` column chunk whose dictionary-inclusive byte span
equals the frozen `data_span_half_open`, with compressed size equal to the
frozen `data_payload_bytes`, ending before the footer. The validated L, footer
range and that chunk's structural metadata (row group, rows, offsets, sizes,
codec) are frozen into `t_phase_p_layout.json` for a later, separately gated
Phase D. Column statistics and key/value metadata are never read, logged or
rendered. No text page is fetched and no document text is decoded.

## 7. Network policy and remote identity

- Method GET; headers `Range: bytes=<start>-<end>`, `Accept-Encoding: identity`
  and a fixed User-Agent. No credentials or tokens are sent.
- Scheme `https` only, port 443 only, exact hosts `huggingface.co` and
  `cas-bridge.xethub.hf.co`. Refused: `http`, userinfo, fragments, localhost,
  IP literals, any other host or subdomain (no suffix/wildcard matching), any
  scheme downgrade. URLs are parsed structurally (`urllib.parse.urlsplit`).
- Each logical request starts at the canonical resource
  `https://huggingface.co/datasets/EssentialAI/essential-web-v1.0/resolve/<revision>/<file>`.
  The revision is identity only in the **path**; a revision in a query string
  is not path identity.
- Redirects (301/302/303/307/308) are never followed by the HTTP library. The
  engine reads the actual `Location`, resolves it against the current URL,
  and validates the destination: either the signed-target host
  `cas-bridge.xethub.hf.co` (opaque path; identity then proven by the response
  checks below) or the exact canonical resource path on `huggingface.co`
  without a query. Anything else is STOP. Caller-supplied redirect history is
  not an input.
- A successful response must be: status 206; `Content-Range` exactly
  `bytes <start>-<end>/<N>` for the requested start/end and frozen total length
  N; a strong ETag (not `W/`) byte-identical to the frozen ETag; content coding
  absent or `identity`; `Content-Length`, when present, equal to end-start+1;
  a body of exactly end-start+1 bytes; `PAR1` at bytes 0–3 for `HEAD_0_3`
  (the body must equal `PAR1`) and at the last 4 bytes for
  `M_FOOTER_AND_TRAILER` and `T_TRAILER`.
- Retryable (consumes one of the 3 tries): transport errors including
  timeouts, and HTTP 429/500/502/503/504. Everything else that is not a valid
  206 or an allowed redirect is STOP (for example 200, 403, 404, 416, a
  redirect without `Location`, an identity mismatch). An identity mismatch
  never triggers another file, a refreshed ETag or a reselection.

## 8. Durable state

One SQLite database `state.sqlite` (stdlib `sqlite3`, `synchronous=FULL`,
exclusive locking so a second process refuses) holds:

- `run`: version, plan digest, scientific-selection digest, source revision,
  created UTC, status (`RUNNING` / `STOPPED` / `COMPLETE`), stop reason;
- `operations`: stable operation ID, seq, arm, source file ordinal and path,
  operation kind, exact range (T footer range NULL until derived), status
  (`PENDING` / `COMPLETE`);
- `attempts`: attempt ID, operation ID, try number, hop number (= redirect
  count before this request), requested URL identity (scheme/host/path and the
  SHA-256 of any query — signed query strings are not stored verbatim),
  requested start/end, started/completed UTC, HTTP status, response bytes,
  outcome, error text, temp-file path, response SHA-256 when complete;
- `outputs`: operation ID, retained file, bytes, SHA-256.

Order for every physical attempt: INSERT the attempt row and COMMIT; create
the deterministic temp file `tmp/<op_id>.a<attempt_id>.part`; issue the
request; stream the body to the temp file, flushing and fsyncing it before each
durable byte-count update (at most every 1 MiB) and at the end; record final
bytes and SHA-256. For a verified success, one transaction records the attempt
`SUCCESS`, inserts the `outputs` row and marks the operation `COMPLETE` (plus
the derived T footer range when the operation is a T trailer); then the temp
file is atomically renamed to `payload/<op_id>.bin`. Non-promoted temp files
are retained, never deleted.

## 9. Crash and restart policy

On every start the engine verifies the run row (version, plan digest,
selection digest, revision) and that the operation rows equal the plan
(including any derived T footer range recomputed from the retained trailer).
Then:

- `COMPLETE` operation: verify the retained payload's size and SHA-256 and
  skip it. If the payload is missing but its recorded temp file exists with the
  recorded hash (crash between commit and rename), finish the rename. Any other
  mismatch is STOP.
- attempt left `IN_PROGRESS`: set its received bytes to the actual size of its
  temp file (0 if the file was never created), mark it `INTERRUPTED`, and never
  treat it as complete. The operation's next try is a **new** physical attempt
  with a new ID. Interrupted tries count toward the 3-try limit and all caps.
- inconsistent database/file state is STOP for review, for example: temp file
  smaller than the durably recorded byte count, recorded bytes with no temp
  file, a payload file without a `COMPLETE` operation, a `COMPLETE` operation
  without an output row, operation rows differing from the plan.

Duplicate HTTP attempts are acceptable and remain visible; they do not change
the scientific sample. A `STOPPED` root refuses further `phase-p` invocations;
continuing requires review and, where a cap or rule must change, a protocol
amendment. A `COMPLETE` root re-verifies and makes no request.

## 10. Isolation

- M requests are exactly the frozen `[0,3]` and frozen footer ranges; the
  footer parse additionally proves every projected chunk ends before the
  footer start, so no Phase-D projected chunk byte was requested.
- T requests are `[0,3]`, `[N-8, N-1]` and the derived footer range, which by
  section 6 starts at or after the frozen text-chunk end, is at most 4 MiB, and
  is proven by the parsed metadata to be the footer. No text page range can be
  materialized.
- Operation rows are compared with the plan on every start; an injected or
  altered range or operation is STOP.

## 11. Outputs, completion and stop conditions

Under the root `G:\Project\xlm-evidence-v4\essential-web` (fresh, separate
from the v3 root; never created by the freeze or implementation tasks; the
live invocation creates it when absent and refuses a pre-existing non-empty
root that has no matching `state.sqlite`):

- `state.sqlite`, `payload/<op_id>.bin`, `tmp/*.part`;
- `request_receipt.jsonl` — canonical export of every physical attempt row;
- `m_phase_p_layout.json` — per M file: window, row group, projected-chunk
  offsets/sizes/codecs; payload hashes (only when COMPLETE);
- `t_phase_p_layout.json` — per T file: validated L, footer range, selected
  text-chunk structural metadata; payload hashes (only when COMPLETE);
- `phase_p_receipt.json` — status, protocol SHA-256, plan digest, adopted
  selection digest, source revision, source identities, every operation with
  its range/status/output hash, per-arm totals (logical operations, physical
  attempts by outcome, returned body bytes) equal to the attempt table, the
  receipt JSONL hash, layout digests, stop reason if any;
- `artifact_manifest.json` — bytes/SHA-256 of every retained payload, temp
  body and export above.

Phase P is **COMPLETE** only if every one of the 40 operations of the 16 frozen
files is complete and verified and both layouts reproduce their bindings.
Otherwise it is **INCOMPLETE**: no partial scientific conclusion, no
replacement source, no resampling.

STOP conditions (receipts preserved, run `STOPPED`): any cap in section 5,
retries exhausted, identity mismatch, disallowed or 4th redirect, non-retryable
status, invalid trailer/footer length, footer/layout binding mismatch,
inconsistent state, plan-digest mismatch.

## 12. Implementation contract

Package `src/xlm/data/evidence_v4/` (small modules) and CLI
`scripts/evidence_v4.py` with exactly `verify`, `show-plan`, `phase-p-status`,
`phase-p`. `phase-p` requires `--confirm-plan-digest
16b93183b485ced924466028b84a8b4cfd84a65f596dc95c80879e32464f35d3`, uses only
the frozen root and the live HTTPS transport, and has no URL, range, file,
ETag, plan, force or root option. This is protection against running a
different plan, not an authorization framework. An offline library entry point
exists for authored synthetic fixtures only; it refuses the real repository or
revision and the live transport. The implementation task must not run
`phase-p` against real remote data.

## 13. Phase D

Phase D remains separately gated: it requires a new freeze binding the
COMPLETE Phase-P receipt and layouts, and its own review. Nothing in v4.0
authorizes Phase D, training, tokenizer work or production admission.

## 14. Narrow review questions

1. Is the scientific membership unchanged? 2. Can the CLI fetch an arbitrary
URL/file/range? 3. Are exact frozen ranges and mechanically derived T footer
ranges enforced? 4. Are 206/Content-Range/ETag/length/path checks correct?
5. Are retries/redirects bounded? 6. Are every physical attempt and returned
byte counts durably recorded? 7. Does restart/resume avoid omission and silent
duplication? 8. Can Phase P accidentally fetch M data chunks or T text pages?
9. Are outputs hash-bound and complete? 10. Are operational caps enforced?
