# Essential-Web evidence v4.1 — Phase-D acquisition protocol

Frozen 2026-09-29 on `data/mix01-ultrax-6b`, parent commit
`ed8efcf3c85c265828a88579a264f08a2048640f` (the passed Phase-P result review).
Protocol version `essential-web-evidence-v4.1-phase-d`. This document governs
one bounded acquisition: the exact compressed ranges needed to recover the
already-frozen M metadata windows and T selected documents. It changes no
scientific membership. It does not authorize a live run. Live execution
requires a separate narrow Phase-D authorization review.

Bound artifacts (directory
[`docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V4.1-PHASE-D/`](../evidence/ESSENTIAL-WEB-EVIDENCE-V4.1-PHASE-D/)):

| Artifact | Canonical self-digest |
|---|---|
| `phase_d_plan.json` (the only source of Phase-D operations) | `23a26ffc87d27e4c8fa5de86d7ec446c260bfd65442dc5b1dfd2651cd62222b7` |
| `phase_p_parent_binding.json` | `de27e2a56f83970a91f9f3224b3bad7cefc977018612522679838ebd15b61559` |
| `scientific_adoption.json` | `4a2de146e53b4a21f1596f1f3518d0a12f5fec2b5760bfab20fd3eabcdea2cba` |
| reviewed dry plan `phase_d_dry_plan.json` (parent, committed at `ed8efcf`) | `ee82125d1b0d2b6d0a6c420144910db954659d5b757283d39123ddc3d0080356` |
| `freeze.json` / `verification.json` | bind this document's SHA-256 (not stated here: circular) |

Canonical form `C(x)` and digest `H(x)` are identical to v2.0–v4.1. The builder
`build_phase_d_freeze.py` in the same directory reproduces every artifact
offline. It reads the committed dry plan, the v4.1 plan, the v4.0 adoption and,
read-only, the COMPLETE Phase-P root and the frozen selection manifest.

## 1. Parent: COMPLETE v4.1 Phase P

Phase P ran once at `G:\Project\xlm-evidence-v4.1\essential-web` and is
**COMPLETE**: 40/40 operations, 80 physical attempts, every chain
`huggingface.co` (302) → `us.aws.cdn.hf.co` (206). The independent read-only
result review (`ed8efcf`) verdict is **PHASE-P RESULT REVIEW PASSED — READY
FOR PHASE-D AUTHORIZATION REVIEW**. Phase D binds that exact parent:

| Parent | Binding |
|---|---|
| Phase-P plan / protocol / freeze | `762cef78…7711` / `3d667a26…79cf` / `28501560…5f80` |
| Membership-and-ranges digest | `304bd0761125b48720a4de9c82bb6b3bf47100b8a15fc0953d548a8a1dab38bd` |
| `phase_p_receipt.json` | digest `ebd5704be8a871034ef8048c0641e9d735d017f458003b4efc50ab124c8bd965` |
| `artifact_manifest.json` | digest `1bb6bad6e3690758c005e260c272eac3c0adcb6d040fde960a502d0ced90d7fe` |
| `m_phase_p_layout.json` | digest `2c9f2f79448e1bb1d59b57f1ddec276dcd05bc79dddaea4014c9942a340d340a` |
| `t_phase_p_layout.json` | digest `364bda46c766e8f9fdebd5732408743420cbe5c6f0b00fbd6a10d09a8bd53274` |
| `state.sqlite`, `request_receipt.jsonl` | bytes + SHA-256 (hashed as bytes; never opened by Phase D) |
| 8 M footers, 8 T footers, 8 T trailers | bytes + SHA-256, equal to the Phase-P manifest |
| Result review | commit `ed8efcf`, report and `review-result.json` bytes + SHA-256 |

The plan's `parents` object records all 30 artifacts. Before any Phase-D
state change and on every invocation, the program re-reads every bound
artifact from the parent root. It requires exact bytes and SHA-256, the four
JSON self-digests, a COMPLETE receipt bound by the manifest, and M/T layouts
equal to the plan's windows, chunks, spans and footer starts. Any difference
is a refusal before the Phase-D root is created or changed. Footers are
re-verified again when decoding reads them. A change during a run is STOP.

The Phase-P root is **read-only**. Phase D never creates, writes, renames or
deletes anything there, never opens its SQLite file and never reruns Phase P.

## 2. Scientific identity (unchanged, adopted by reference)

Phase D adopts the v4.0 scientific adoption (`158c3fc9…ffd3`, identity digest
`080caebb962c86562b530ddfa6a130118a49a6d207b044e83ce6a90a6d9691c6`) unchanged:

- namespace `essential-web-evidence-v2.0`; selection digest
  `975ba3dee4af0598e665ea05c69bbe49336c3e3a35fca777a863073190b78474`; source
  `EssentialAI/essential-web-v1.0` at revision
  `ce4eccc7e9604667b6d7f32cb6274b8b41f3113d`; selector policy
  `f4357f61f434d5105d823266153dac6372e122f12767187631e8796b4899dd07`;
- **M**: the same 8 files, the same deterministic 512-row windows
  (4,096 rows), projection exactly `eai_taxonomy`, `quality_signals`;
- **T**: the same 118 locators in the same 8 files, with the same strata,
  censuses, precedence, rubric, blinding and review order.

The builder verified that the plan's 118 locators equal, as a set, the
identities of the frozen selection manifest (SHA-256 `8424f966…af27`). No
stratum or other selection category is copied into any Phase-D artifact.
No replacement, no reselection, no new or widened range, and no membership
change based on decoded content. Phase D makes no selector decision, human
review or scientific score.

## 3. Exact M plan (8 operations)

One operation per file: the union of that file's 81 exactly adjacent
projected leaf chunks, which the dry plan, Phase-P layout and footer metadata
all reproduce. Coalescing adds zero bytes. Every range lies inside its row
group and ends before the Phase-P footer.

| Op | File | Row group | Frozen window | Range [start,end) | Bytes |
|---|---|---:|---|---|---:|
| M-00-d00 | `data/crawl=CC-MAIN-2014-15/train-01860-of-02772.parquet` | 9 | [90023,90535) | [289898947,290481147) | 582,200 |
| M-01-d00 | `data/crawl=CC-MAIN-2015-32/train-01682-of-01920.parquet` | 9 | [92096,92608) | [315229381,316525770) | 1,296,389 |
| M-02-d00 | `data/crawl=CC-MAIN-2016-50/train-02156-of-03132.parquet` | 6 | [67173,67685) | [205515226,207095760) | 1,580,534 |
| M-03-d00 | `data/crawl=CC-MAIN-2018-05/train-03378-of-03429.parquet` | 1 | [11728,12240) | [60529942,62163812) | 1,633,870 |
| M-04-d00 | `data/crawl=CC-MAIN-2019-09/train-00153-of-02577.parquet` | 2 | [26634,27146) | [95348280,96991689) | 1,643,409 |
| M-05-d00 | `data/crawl=CC-MAIN-2021-04/train-00179-of-03315.parquet` | 4 | [46415,46927) | [156189931,157839897) | 1,649,966 |
| M-06-d00 | `data/crawl=CC-MAIN-2021-49/train-00408-of-02895.parquet` | 5 | [58279,58791) | [192193472,193850649) | 1,657,177 |
| M-07-d00 | `data/crawl=CC-MAIN-2024-26/train-01127-of-03168.parquet` | 1 | [19154,19666) | [62433757,64082742) | 1,648,985 |

**8 logical operations; 11,692,530 exact successful payload bytes.** The HTTP
Range is `bytes=<start>-<end-1>`.

## 4. Exact T plan (47 operations)

Per file: the frozen row group's dictionary-inclusive `text` chunk
(`dictionary_page_offset` = span start < `data_page_offset`), split from its
start into 4,194,304-byte pieces plus the final remainder. Operation
`T-NN-dKK` is piece KK. Pieces are exactly adjacent and reconstruct the chunk.
Nothing is coalesced across the 4 MiB ceiling or across a gap. Every endpoint
equals the dry plan.

| File | Path | Row group | Span [start,end) | Data page | Pieces | Bytes | Uncompressed | Locators |
|---|---|---:|---|---:|---:|---:|---:|---:|
| T-00 | `data/crawl=CC-MAIN-2014-15/train-01787-of-02772.parquet` | 5 | [156637786,179150784) | 158729170 | 6 | 22,512,998 | 38,450,828 | 16 |
| T-01 | `data/crawl=CC-MAIN-2015-32/train-01486-of-01920.parquet` | 8 | [259990771,283182502) | 262393506 | 6 | 23,191,731 | 40,088,762 | 14 |
| T-02 | `data/crawl=CC-MAIN-2016-50/train-00454-of-03132.parquet` | 5 | [145763654,165808388) | 147633836 | 5 | 20,044,734 | 34,378,348 | 11 |
| T-03 | `data/crawl=CC-MAIN-2018-05/train-01826-of-03429.parquet` | 6 | [190807685,213037997) | 192929439 | 6 | 22,230,312 | 38,296,749 | 11 |
| T-04 | `data/crawl=CC-MAIN-2019-09/train-01825-of-02577.parquet` | 6 | [197584400,221221447) | 199754111 | 6 | 23,637,047 | 41,297,124 | 14 |
| T-05 | `data/crawl=CC-MAIN-2021-04/train-00297-of-03315.parquet` | 4 | [129999706,152903349) | 132222075 | 6 | 22,903,643 | 39,415,672 | 13 |
| T-06 | `data/crawl=CC-MAIN-2021-49/train-02221-of-02895.parquet` | 1 | [32748062,56440573) | 35268448 | 6 | 23,692,511 | 41,244,759 | 18 |
| T-07 | `data/crawl=CC-MAIN-2024-26/train-02037-of-03168.parquet` | 1 | [31508248,53258441) | 33648881 | 6 | 21,750,193 | 37,895,921 | 21 |

**47 logical operations; 179,963,169 exact successful compressed bytes; 118
locators.** M+T: 55 operations, 191,655,699 bytes. The piece counts equal the
v4.1 plan's frozen `data_range_count` values. The full per-piece endpoint
list is in `phase_d_plan.json` and printed by `show-plan`.

## 5. Plan grammar (fixed; no caller input)

`phase_d_plan.json` is loaded only from its fixed repository path. It is
accepted only when its canonical bytes and self-digest equal the compiled
digest. The validator re-derives every operation from the file bindings
(M: the chunk union; T: the 4 MiB split of the span) and refuses any
operation list that differs by one field. That rules out injection, removal,
reordering, widening or kind changes. It also refuses non-adjacent chunks, a
projection other than the frozen two columns, a window outside its row group
or not 512 rows, a non-dictionary-inclusive span, locators that are not
`[repository, revision, file, first_row + row_in_group]` and strictly
ascending, any Phase-P payload name other than the derived one, limits or
network different from the frozen values, a dry-plan digest other than
`ee82125d…0356`, a parent other than the COMPLETE v4.1 root, and any real
shape other than 8/47 operations, 11,692,530/179,963,169 bytes, 4,096 rows
and 118 locators.

## 6. Operational limits (non-scientific guardrails)

They stop runaway behaviour only. They are never raised automatically, and
hitting one is STOP with receipts preserved and ranges unchanged. They leave
room for retries and error traffic but do not guarantee that every
theoretical retry can complete.

| Limit | M | T |
|---|---:|---:|
| physical HTTP attempts per arm (root lifetime: all tries, hops, restarts) | 64 | 320 |
| returned response-body bytes per arm (every status, partial and redirect body) | 67,108,864 | 536,870,912 |

| Limit (both arms) | Value |
|---|---|
| body per physical response | ≤ 4,194,304 bytes (reading the 4,194,305th is CAP_EXCEEDED → STOP) |
| redirect transitions per logical request | ≤ 3 |
| additional tries per logical operation | ≤ 2 (delays 1 s, 2 s; retryable: transport errors, timeouts, 429/500/502/503/504) |
| physical-attempt timeout | 120 s absolute, same B02 semantics as v4.1 |
| retained execution-root size (every file: payloads, temp bodies, SQLite, exports, outputs) | ≤ 1,073,741,824 bytes |
| free space on the root's drive before a run | ≥ 2,147,483,648 bytes |
| durable byte-count interval while streaming | ≤ 1,048,576 bytes |
| M decoded metadata record / M bundle | ≤ 1,048,576 bytes / ≤ 67,108,864 bytes |
| T selected document (UTF-8) / retained text / T bundle | ≤ 65,536 / ≤ 8,388,608 / ≤ 67,108,864 bytes |

Enforcement is pre-emptive, as in v4.0 section 5. An attempt starts only if
the arm has fewer attempts than its cap, its recorded body bytes plus
4,194,305 fit the arm cap, and the measured root size plus 4,194,305 fits the
root cap. Before writing decoded outputs, the root size plus the output bytes
must fit the root cap, or the arm is INCOMPLETE. Decoding memory is not
enforced by the process (v4 section 3). Expected peak is one file at a time:
at most one ~23 MB T chunk plus its ≤ 41,297,124-byte uncompressed text, or
one ≤ 1.7 MB M range plus ≤ 1,704,825 uncompressed bytes, plus PyArrow
overhead. There is no scratch area outside the root.

## 7. Transport (reused v4.1, unchanged)

Each logical request starts at the frozen canonical
`https://huggingface.co/datasets/EssentialAI/essential-web-v1.0/resolve/<revision>/<file>`.
It follows only the exact 19-host v4.1 policy (exact string equality, HTTPS,
port 443, no userinfo, fragment, localhost or IP literal) with ≤ 3 redirect
transitions. A successful response must be 206, with `Content-Range` exactly
the requested start–end and the frozen total length, a strong ETag
byte-identical to the frozen ETag, identity coding, and `Content-Length` (when
present) and body exactly the range length. B01 (every received body byte
persisted before a framing failure) and B02 (one absolute 120-second deadline
arming every blocking socket step) apply unchanged. A mismatch never
refreshes an ETag, replaces a file or widens a range.

## 8. Durable state and restart

One `state.sqlite` per root, in the v4 schema (`run`, `operations`,
`attempts`, `outputs`) plus `decoded_outputs` (name, arm, records, bytes,
SHA-256). The run row binds this protocol version, the plan digest, the
selection digest and the revision.

- Every physical attempt row is committed before its request. Its body
  streams to `tmp/<op_id>.a<attempt_id>.part` with durable byte counts. A
  verified success atomically records SUCCESS, the output row and COMPLETE,
  then the temp file is renamed to `payload/<op_id>.bin`.
- Restart: stored operations must equal the plan. A COMPLETE operation's
  payload is size/hash verified and skipped. An IN_PROGRESS attempt gets its
  actual temp size, becomes INTERRUPTED (counted against tries and caps) and
  is followed by a **new** attempt ID. No silent completion and no invisible
  duplicate: every attempt stays visible.
- Decoding runs after acquisition and is deterministic. After a crash during
  decoding, a restart makes no request and decodes again from the verified
  payloads. Outputs are written atomically and recorded in `decoded_outputs`.
- A STOPPED root refuses further runs until reviewed. A COMPLETE root
  re-verifies every exported artifact and makes no request.

## 9. Decoding and isolation

The decoder sees only identity-verified retained bytes. A sparse virtual file
of the frozen remote length holds just the acquired segments and refuses any
read outside them. PyArrow opens it with the pre-parsed Phase-P footer
metadata (no footer read) and `pre_buffer=False` (no read coalescing), and
never requests column statistics.

- **M**: read only the projected columns of the frozen row group. The
  footer's projected leaves must equal the 81 frozen chunks. Retain exactly
  the frozen window rows (512 per file). Each output line is canonical JSON
  `{"_xlm_acquisition": {repository, revision, source_file, row, row_group,
  row_in_group, m_ordinal}, "eai_taxonomy": …, "quality_signals": …}`.
  Non-canonical values (for example a non-finite float) or a record over
  1 MiB make the arm INCOMPLETE. They are never coerced.
- **T**: decode the dictionary-inclusive `text` chunk of the frozen row group
  in 256-row batches up to the batch holding the greatest selected row. Read
  values at selected row indices only, as raw bytes, and decode them as
  strict UTF-8. Unselected rows in the same pages are decompressed because
  that is mechanically necessary. They are never retained, exported, logged,
  rendered, counted by content or used for any decision; the manifest only
  counts how many there were.

The retained `payload/*.bin` files are the raw compressed acquisition bytes.
Because a chunk also holds other rows, they necessarily contain unselected
rows in compressed Parquet form. They are custodian acquisition evidence,
hash-bound in the manifest, and never decoded outside the engine or rendered.

## 10. T retention, blinding and sealed provenance

Inherited unchanged from the frozen v2.0 protocol section 7, reusing
`sparse.classify_retained`. Each of the 118 locators receives exactly one
status:

- `full_text_available`: UTF-8 length ≤ 65,536 bytes. The complete text,
  its length and its SHA-256 are retained.
- `unreviewable_full_document_due_to_size`: length > 65,536. No text or
  excerpt is retained; the full length is recorded.
- `unreviewable_missing_or_invalid_text`: null, empty, non-string or invalid
  UTF-8. Never coerced.

No truncation and no normalization. Retained unique text is ≤ 8,388,608
bytes (118 × 65,536 = 7,733,248 fits). An oversized document is therefore not
a stop: it is a frozen terminal status, and the T acquisition can be COMPLETE
without claiming 118 reviewable texts. The count per status is reported.

Blinding (frozen v2.0 section 9, unchanged): Phase D generates no custodian
secret K, review ID, key commitment or reviewer order. No stratum, census,
policy label or other selection category appears in any Phase-D output. No
Phase-D file is a reviewer package. Locator-bearing T material is
custodian-only under `sealed/`: `t_selected_documents.jsonl` (locator,
ordinal, row group and row, status, length, SHA-256, text) and
`t_provenance.json` (locator → file, row group, chunk span, source
operations, status, hash). The later blinded package is built from these by
the existing mechanism, with a fresh K. The M-report-before-T-unblinding rule
is unchanged. Phase D does not unblind anything.

## 11. Outputs and completion

Under `G:\Project\xlm-evidence-v4.1\essential-web-phase-d`:

- `state.sqlite`, `payload/<op_id>.bin`, `tmp/*.part`;
- `request_receipt.jsonl`: every attempt row, canonical;
- `m_selected_metadata.jsonl` (4,096 lines when complete) and
  `m_acquisition_manifest.json` (per file: window, row group, operation and
  payload hash, record count and hash; totals);
- `sealed/t_selected_documents.jsonl` (118 lines when complete),
  `sealed/t_provenance.json`, `t_acquisition_manifest.json` (per file:
  operations and payload hashes, status counts, decoded and unselected row
  counts; totals; sealed-output hashes; no locators and no text);
- `phase_d_receipt.json`: status, run status, stop reason, per-arm status and
  reason, every operation with range/status/output hash, per-arm totals equal
  to the attempt table (logical operations, attempts by outcome, returned body
  bytes, retained payload bytes), request-receipt hash, output hashes, limits;
- `artifact_manifest.json`: bytes/SHA-256 of every payload, temp body, export
  and output, bound to the receipt digest.

Every document binds this protocol version and SHA-256, the freeze and plan
digests, the dry-plan digest, the Phase-P parent (plan, parents digest,
receipt, manifest and layout digests), the scientific namespace, selection
and policy digests, and the source revision.

**M COMPLETE** only if all 8 operations completed and the decoded bundle has
exactly the 4,096 frozen rows, 512 per file, none missing, duplicated or
reordered. **T COMPLETE** only if all 47 operations completed and all 118
locators were recovered exactly once, each with one terminal status, within
the retained-text limits. **Overall COMPLETE** only if both arms are COMPLETE.
Otherwise the run is STOPPED/INCOMPLETE. A completed arm's outputs are still
written and marked with that arm's status. Nothing is resampled or replaced.

STOP conditions: any cap, retries exhausted, identity or policy violation,
non-retryable status, inconsistent state, parent drift, decode failure,
duplicate/missing row or locator, retention-limit violation.

## 12. Implementation contract

Reuse `src/xlm/data/evidence_v4/`; do not clone it. The v4 engine's transport,
redirect, identity, retry, B01/B02, attempt accounting and restart
reconciliation are used by subclassing. Only the Phase-D caps, parent
verification, decoding and exports are new. The only permitted changes to
reviewed code are default-preserving hooks: an operation-ID pattern for the
derived temp/payload names, and a store class. Add `phase_d_plan.py` (frozen
constants and grammar), `phase_d_decode.py`, `phase_d.py` and
`scripts/evidence_v41_phase_d.py` with exactly `verify`, `show-plan`,
`phase-d-status` and `phase-d --confirm-plan-digest <digest>`. There is no
URL, file, range, ETag, host, root, locator, plan or force option. Tests are
synthetic and offline only. The implementation task must not run `phase-d`,
must not create the Phase-D root and must not touch the Phase-P root.

## 13. Future command (NOT authorized by this freeze)

After a narrow Phase-D authorization review passes, and only then, the
operator runs from the repository:

    uv run --offline --locked --extra cpu --extra eval python scripts/evidence_v41_phase_d.py phase-d --confirm-plan-digest 23a26ffc87d27e4c8fa5de86d7ec446c260bfd65442dc5b1dfd2651cd62222b7

`--offline` prevents dependency downloads. The program itself uses the
network, only to the 19 frozen hosts. It creates the fresh Phase-D root and
reads the Phase-P root read-only.

## 14. Known limitations

- Remote identity evidence follows the v4.1 receipt model: raw headers are
  validated but not persisted.
- DNS resolution time is not bounded (unchanged from v4.0).
- Process memory is not enforced (section 6 gives the expected bound).
- PyArrow may pad reads for files written by very old parquet-mr versions.
  Any such read outside the acquired bytes is refused, so the arm stops as
  INCOMPLETE rather than widening a range.
- Redirect, error and retry bytes are unknown in advance. The caps bound them
  but do not guarantee completion.

## 15. Narrow review questions

1. Is scientific membership unchanged: 4,096 M rows, 118 T locators, the
   same projection, windows and files?
2. Do the 8 M and 47 T ranges equal the reviewed dry plan exactly, with
   nothing added, widened or coalesced across gaps?
3. Does Phase D refuse a different Phase-P parent and never write to it?
4. Can the CLI or API fetch an arbitrary URL, file, range, host or locator?
5. Are the v4.1 host policy, redirect bound, identity checks, B01 and B02
   unchanged?
6. Are the M/T caps, the 4 MiB response cap, the 1 GiB root cap and the
   2 GiB free-space floor enforced without auto-raising?
7. Does restart avoid omission and silent duplication?
8. Is unselected text never retained, exported or logged?
9. Are the 65,536/8,388,608 retention rules applied without truncation?
10. Is no selection category, secret or review ID produced, and is
    locator-bearing material sealed?
11. Is COMPLETE only claimed when both arms satisfy their exact conditions?
