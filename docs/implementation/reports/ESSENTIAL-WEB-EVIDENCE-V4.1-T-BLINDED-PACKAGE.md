# Essential-Web v4.1 T blinded review package

**T BLINDED PACKAGE SEALED — READY FOR REVIEWER LABELING**

2026-09-30, branch `data/mix01-ultrax-6b`, starting HEAD
`65edfd0a9e3b33b2b98cc9423223e31506db59a5`. The frozen 118-locator Arm-T
selection was materialized, for the first time, as a custodian master ledger
and two blinded reviewer packages in an access-restricted directory outside
Git and outside both G: evidence roots. The sealed M result was verified
first and is bound as the parent.

T package digest:
`18c95b95699922db325626fd8776c2317231c51666f9bcc42898f5811dabc58d`

No human or model labeling, unblinding, selector decision, reselection,
M change, network, acquisition, G: mutation or push. Selected text was
processed in memory and written only to the external root; none was printed
or placed in Git.

[Evidence directory](../evidence/ESSENTIAL-WEB-EVIDENCE-V4.1-T-BLINDED-PACKAGE/),
[package manifest](../evidence/ESSENTIAL-WEB-EVIDENCE-V4.1-T-BLINDED-PACKAGE/package_manifest.json),
[leakage audit](../evidence/ESSENTIAL-WEB-EVIDENCE-V4.1-T-BLINDED-PACKAGE/leakage_audit.json),
[exact commands](../evidence/ESSENTIAL-WEB-EVIDENCE-V4.1-T-BLINDED-PACKAGE/COMMANDS.md).

## Parents verified

| Binding | Verified value |
|---|---|
| M seal digest | `afc972ccf570a5587bf7bd3b6b8d1d728a09f2230c49f1b0a19c1c74cda0b9bc`, recomputed by the committed verifier before and after this work (exit 0 both times) |
| M result commit | `65edfd0a9e3b33b2b98cc9423223e31506db59a5`; working `m_seal.json` (SHA-256 `eaaac163c56976aa473e5f68703dd4abcbf06dde13b76d7dd0867d86f4d6aa23`) and `artifact_manifest.json` are byte-identical to that commit |
| Phase-D receipt | SHA-256 `7e8c37e183d546cb43c5cc3deae1c8b6267969500e8af025ceeba67c149320e3`, the receipt bound inside the M seal; run and Arm T both COMPLETE |
| T acquisition manifest | SHA-256 `322d338d46d31deb0d0ef7548322402f325d59206c34700ed4d9ca9ae827e72e`, digest `1cd02d514a6e15d4cabf9ba1ed76ad8cff328913635a4b8b76edb11193def8e3`; equals the receipt's bound output |
| T selected documents | 720,801 bytes, SHA-256 `83491f1539713d6ecf5a0d19cfd39a5e52a82fa0e12a45acc82906afc1183b73` |
| T sealed provenance | 65,766 bytes, SHA-256 `8d786ba8d4887c9b15085adde598edc708d265a7ef43b7a46c04047afb4117c1` |
| T preparation manifest | digest `6daa94e40463a634153de91b8123ffbf503157816d908cd575da603f949ef4b9` |
| Frozen selection manifest | SHA-256 `8424f9668fef6952a558ead0dae27a705545a7f4a4d302713707d3ec6bb1af27`, digest `975ba3dee4af0598e665ea05c69bbe49336c3e3a35fca777a863073190b78474` |
| Scientific namespace / source revision | `essential-web-evidence-v2.0` / `ce4eccc7e9604667b6d7f32cb6274b8b41f3113d` |
| Selector policy digest | `f4357f61f434d5105d823266153dac6372e122f12767187631e8796b4899dd07` |

The chain is: M seal → Phase-D receipt → T acquisition manifest → documents
and provenance. Each of the 118 stored records was also checked against the
sealed provenance and against its committed entry binding from the Phase-D
result review. Every reviewable text re-encodes to its recorded byte count
and SHA-256. The 118 locators equal the frozen selection exactly: none
duplicated, none missing, none unexpected.

## Blinding material: first materialization

No earlier key, review ID or reviewer order exists. The search covered the
repository (code, tests, all evidence directories, for `ew2-` IDs and key
commitments) and these evidence locations, by file name and directory:
`F:\Project\xlm-evidence-v2`, `F:\Project\xlm-selector-sweeps`,
`G:\Project\xlm-evidence-v4`, `G:\Project\xlm-evidence-v4.1` (including
`essential-web-phase-d\sealed`), and `F:\XLM-Review` (absent before this
work). Nothing outside those locations was inspected. This agrees with the
Phase-D result review, which recorded that none existed.

This is therefore the **first materialization, not a reselection**. One
32-byte key was generated with the existing `blinding.generate_secret` and
written once. Its SHA-256 commitment is
`884214cfdd3b9db3ec21503acb3bda80f50b5ddc18fac502d5d9b4611e2e01e9`.
Review IDs and orders use the unchanged frozen construction in
`src/xlm/data/evidence_v2/blinding.py`:

- ID: `"ew2-"` + HMAC-SHA256(K, C([`essential-web-evidence-v2.0`,
  `review-id`, locator])), full digest.
- Order for reviewer r: ascending
  H([`essential-web-evidence-v2.0`, `review-order`, 20260928, r, review ID]),
  tie by review ID.

Two earlier invocations were refused while reading the sources, before any
key existed, so no key was ever discarded and no order was ever rerolled.
From now on the builder reuses the stored key and refuses if recomputed IDs
or orders differ from the stored ones.

## External package

Root: `F:\XLM-Review\essential-web-v4.1-t` (722 files, 4,852,481 bytes;
the frozen final-artifact cap is 16,777,216). No frozen document names an
exact destination for v4.1; the Phase-D result review requires "a separate
access-controlled external directory" outside Git and both G: roots. The ACL
grants full control to the operator account, SYSTEM and Administrators only,
with inheritance from the parent removed.

| Area | Contents |
|---|---|
| `custodian/` | `sealed/custodian_key.bin` (K), `master_ledger.json`, `sealed_mapping.json`, both ordered ID lists, per-reviewer file manifests, `materialization.json`, the leakage audit and the package manifest |
| `reviewer-1/`, `reviewer-2/` | `package.json`, `order.json`, `texts/NNNN.txt`, `view/NNNN.html`, `forms/NNNN.json`, `rubric.md`, `rubric.json`, `README.txt` |

### Master ledger

118 entries: **117 reviewable, 1 unreviewable, 0 missing or invalid**. Each
entry binds the frozen locator, source file, absolute row, row group, row
within group, the group's first row, the acquisition operations and chunk
span, the stored record's line number and SHA-256, the acquisition status,
the text SHA-256 and UTF-8 byte count where text exists, the review ID and
the position in each reviewer's order. Retained reviewable text is 671,351
bytes; the largest reviewable document is 38,893 bytes.

Selection strata and ranks are **not** in the ledger. It binds the selection
manifest by hash; the custodian joins strata only after adjudication is
sealed, as the protocol requires.

### Oversized entry

The one `unreviewable_full_document_due_to_size` entry (109,974 bytes) is in
the ledger with its review ID, `text_sha256` null, both order positions null
and `dimension_status: not_reviewed`. It is in neither reviewer package: no
text, no excerpt, no form, and its review ID appears in no reviewer file.
The source holds no text for it, and the builder refuses an oversized entry
that carries any. Later analysis must report reviewable 117 and
unreviewable 1 separately.

### Reviewer packages

| | reviewer-1 | reviewer-2 |
|---|---|---|
| Reviewable items | 117 | 117 |
| Duplicate IDs | 0 | 0 |
| Order commitment | `3e7b52dd85342ad1380273c46843262f5971399e2e18feff8e31a71c266ab378` | `d1c901f1ff7d48e271bf19800098aa184b91beb2415cef7f85132dd9f29fa876` |
| `package.json` SHA-256 | `6123d546f76707404b717c3723b2da459dff15dbb4e7b192f88f7aadfaef8d63` | `b57b28ae6ce7ebabc62b301d35f938463e03064e69beb134fe5b16b2d6d6c0ba` |
| File-tree digest | `e7a3aecf32e48097d5d601f80141905ed2f3d56ba57e830a27dd14be5fac4761` | `4018dc07199d5074980e1dbcd714300d05fdfb7601c159fc48165e93ff66eb96` |
| Files / bytes | 356 / 2,268,558 | 356 / 2,268,558 |

The order commitment is the canonical digest of the ordered review-ID list.
Both packages hold the same 117 IDs in different orders, each equal to the
recomputed frozen order. Every text hash equals the ledger's.

`package.json` is the output of the existing `blinding.build_package` helper
with its sealed entries removed. Each item is `review_id`, `text`,
`rubric_version`, `reviewer` and `labels: null`. `texts/` holds the exact
UTF-8 bytes; `view/` holds the same text HTML-escaped inside a page with a
`default-src 'none'` content-security policy, no links, scripts or remote
assets.

### Rubric and forms

Rubric version `essential-web-evidence-v2.0-rubric-1`, all **18 dimensions**.
`rubric.md` is protocol section 8 byte for byte (8,358 bytes, SHA-256
`2afa915de92597e66f214a5dea89f76af04387773cabd62c87639eb2aa57b56d`);
`rubric.json` is the existing `rubric.py` data unchanged (SHA-256
`7fb5d7b2d81d0511bc53ae1ca765f2ccbd27a8fdc2f2d330fe6ebfda10a69d42`). The
builder refuses if any dimension title or level in the code differs from the
section-8 table. Bound sources: `rubric.py` SHA-256
`47bbcbb3da52d61683977c8d32af545036605764566a0ba9e572ba80eacd045b`, protocol
SHA-256 `c32dff47dc6faeb3733a391a79744ba7a887e26ade157c399e806f0f47a99618`.

Each of the 234 forms carries exactly the frozen form fields. All 18
dimension values, confidence, expertise, rationale and submission time are
null; nothing is pre-filled. The adjudication contract (independent third
adjudicator, no majority rule) is unchanged and nothing for it was
materialized.

## Blinding and leakage audit

The builder's audit scanned all 712 reviewer files and would have refused to
write on any hit. **Structural hits: 0.** A second, separately written scan
of the files on disk also found 0.

- **Field names.** Every JSON key at every depth is checked against an
  allow-list per file type and a deny-list (the frozen prohibited fields plus
  hashes, row identity, repository, revision, condition, rank, score, key and
  mapping names). No key outside the allow-lists exists.
- **Exact values.** K (raw, hex, base64), repository, revision, the eight
  source paths and basenames, crawl IDs, all 118 locators, the selection
  ownership names, the selection, policy, M-seal and preparation digests,
  every text, record and locator hash, and the oversized entry's review ID:
  absent from every reviewer file, including inside document text.
- **Patterns.** `A-normal` through `D-strict`, B-versus-D wording, ownership
  labels, `CC-MAIN-…`, `train-NNNNN-of-NNNNN`, local drive paths: absent
  everywhere, including inside document text.
- **Generic vocabulary** (selector, locator, stratum, crawl, provenance,
  policy, strict, census, hypothesis, taxonomy, sha256, hmac, custodian and
  others): absent outside document text. One reviewer's files never name the
  other reviewer.

Limitations, stated plainly:

1. **Reviewers must not have this repository or any custodian report.**
   `t-entry-bindings.json`, committed by the Phase-D result review, lists each
   text's SHA-256 beside its source line and a hash of its locator. A
   reviewer who can read it could map a document to its source file, row and
   crawl. It does not reveal selection ownership, which needs the selection
   manifest held outside Git.
2. Document text is unaltered, so dates, names or ordinary words such as
   "policy" inside a document remain (the second scan counts such words in
   text; none is a custodian value).
3. `package.json` shows the key commitment and the protocol version string,
   as the protocol and the existing helper specify.
4. Both reviewer directories and the custodian directory sit under one
   restricted root on this machine. Separation between the two reviewers is
   achieved at distribution: each receives only their own directory.
5. `README.txt` is newly written handling guidance drawn from protocol
   sections 8 and 9 and the form validator. It is not frozen wording; the
   rubric itself is verbatim.

## Commitments and Git-safe artifacts

| Commitment | Value |
|---|---|
| Package digest | `18c95b95699922db325626fd8776c2317231c51666f9bcc42898f5811dabc58d` |
| Key commitment (SHA-256 of K) | `884214cfdd3b9db3ec21503acb3bda80f50b5ddc18fac502d5d9b4611e2e01e9` |
| Master ledger | 145,102 bytes, SHA-256 `72eadb834b523e26844caceb976dc77276f6e3fcf666edcf40c5cc1faa0aeb3a` |
| Sealed mapping | 32,827 bytes, SHA-256 `ad7e3306f223fa37b9a38570655cde99cc46339b66200ccdea3433f8a453687e` |
| Custodian tree digest | `915c782f61c5b9577aa88aa5d50c16c24a76cacddcfea613064face6d72fc2a3` |
| Build code digest | `b8f9a31a5ae049c89e9daf3a3a3d3c9b77f98a36846f8212b2f15d80d44e38e0` (seven files, listed with hashes in the manifest) |
| Git artifact manifest digest | `78eaad3b1c533cf1d894effcd9ff75777c37072f922f1442288aa76790d96848` |

In Git: `package_manifest.json`, `reviewer1_commitment.json`,
`reviewer2_commitment.json`, `custodian_commitment.json`,
`rubric_binding.json`, `m_seal_parent.json`, `leakage_audit.json`,
`artifact_manifest.json`, `COMMANDS.md`, `independent_scan.py` and logs.
They hold hashes, counts and rules only. The builder refuses to write them
if they contain K, any review ID, or any document line of 32 characters or
more. No ordered ID list, no locator-to-ID mapping and no text is in Git.

`verify` recomputes the whole package from the bound inputs and the stored
key and matched all 722 external files and all eight bindings byte for byte.
The manifest binds the environment and code hashes, so `verify` must run
with the committed code on this environment.

## Implementation and tests

- `src/xlm/data/evidence_v2/t_package.py`: parent verification, ledger,
  reviewer files, audit, validation, write and verify.
- `scripts/essential_web_t_package.py`: `materialize` and `verify`; refuses a
  package root inside the repository or the evidence roots.
- `tests/test_essential_web_t_package.py`: 39 tests on authored fixtures.

The tests cover: 118 → 117 + 1; duplicate, missing, unexpected, foreign and
reordered locators; text-hash mismatch; an oversized entry carrying text;
source-hash and manifest drift; M-seal mismatch and a modified M result; a
receipt not bound by the M seal; reuse of an existing key, IDs and orders
with key generation forbidden; refusal when stored IDs or orders differ;
determinism given the key; frozen ID and order construction; absence of the
oversized entry from reviewer files; unaltered texts; forbidden fields and
values; eight injected leaks; escaped views; blank 18-dimension forms;
verbatim rubric; text and order changes moving the commitments; text, key and
ID absence from the Git artifacts; write, verify and tamper refusal.

## Requirement ledger

| Requirement | Status |
|---|---|
| M seal reproduces and is unmodified | VERIFIED (real evidence, before and after) |
| T acquisition source chain and 118-locator membership | VERIFIED (real evidence) |
| Search for prior K / IDs / orders | VERIFIED none exist in the listed locations; first materialization |
| Frozen ID and order construction, seed 20260928, `ew2-` namespace | IMPLEMENTED (existing helper reused), VERIFIED (real run and synthetic tests) |
| Master ledger, 118 = 117 + 1, no locator dropped | IMPLEMENTED, VERIFIED (real run) |
| Reviewer packages: 117 each, frozen order, hashes match, no forbidden fields | IMPLEMENTED, VERIFIED (real run, builder audit and independent scan) |
| Oversized entry kept in ledger, absent from reviewer material | IMPLEMENTED, VERIFIED |
| Rubric verbatim, 18 dimensions, blank forms | IMPLEMENTED, VERIFIED |
| Git artifacts free of text, K, IDs and mapping | IMPLEMENTED, VERIFIED |
| Package verify by full recomputation | VERIFIED (exit 0) |
| Both G: roots and the selection directory unchanged | VERIFIED (stat inventories identical before and after) |
| Local ACL on the external root | IMPLEMENTED, VERIFIED on this machine |
| Refusal paths and reuse | VERIFIED (synthetic tests only; a mock proves logic, not live data) |
| Ruff check and format | VERIFIED |
| Strict mypy | VERIFIED with the interpreted runner; compiled mypy BLOCKED by application control |
| Fast and full offline selections, CUDA, network tests | NOT RUN |
| Exact-text alias consolidation | OUT OF SCOPE here: 117 distinct text hashes, and the builder refuses if aliases appear |
| Stratum and rank join | NOT RUN, deliberately deferred until adjudication is sealed |
| Reviewer labels, disagreement report, adjudication, unblinding, selector decision | NOT RUN / next stages |
| Measured process peak memory | NOT MEASURED |

Measured resources: materialization 2.121 s wall for the whole process (one
run); external root 4,852,481 bytes; the eight Git binding files total
19,690 bytes.

## Exact next human-review action

The custodian gives each of two independent human reviewers, who have
suitable domain expertise and no access to this repository or to each other's
work, a copy of only their own directory:

- reviewer 1: `F:\XLM-Review\essential-web-v4.1-t\reviewer-1\`
- reviewer 2: `F:\XLM-Review\essential-web-v4.1-t\reviewer-2\`

Each reviewer reads `README.txt` and `rubric.md`, reviews the 117 items in
the order in `order.json`, fills in one copy of each `forms/NNNN.json`, and
returns the completed forms to the custodian without discussion. The
custodian seals both first-pass submissions and their digests before any
comparison. No model label substitutes for either reviewer. The custodian
directory, the key and the mapping stay where they are. Unblinding and the
stratum join wait until adjudication is sealed.

**T BLINDED PACKAGE SEALED — READY FOR REVIEWER LABELING**
