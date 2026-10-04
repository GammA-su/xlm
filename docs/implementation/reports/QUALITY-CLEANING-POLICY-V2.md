# Cleaning policy v2: human-reviewed successor of v1 (content-free)

Date: 2026-10-04. `recipes/quality/cleaning_policy_v2.yaml` succeeds
`cleaning_policy_v1.yaml`. v1 is preserved unchanged as historical evidence: its
template, its operator-frozen file `cleaning_policy_v1.frozen.yaml` (digest
`1a49d436...`, audit-v3), its semantics, its tests and its evidence.

The operator ran the v1 Phase-B dry run and manually reviewed the 119 materialized
examples. This report records only the resulting rule changes. No document text,
excerpt, identifier or locator from that review is recorded here or in any policy
file.

## Rule changes after review

| v1 | v2 | reason (content-free) |
|---|---|---|
| `hard.full_html` -> DROP | **KEEP** (rule removed; complete HTML syntax alone is never a DROP condition) | systematic false positives: reviewed examples were useful programming / Q&A / tutorial documents with literal complete HTML examples |
| default class group: severe signals >= 2 -> DROP | default threshold **2 -> 3** (>= 3 DROP, <= 2 KEEP) | the default repetition threshold was too aggressive |
| structured class group: exactly 2 -> REVIEW | exactly-two REVIEW -> **KEEP** (>= 3 DROP, <= 2 KEEP) | no REVIEW outcome in v2 |
| `finepdfs_en`: >= 2 OCR and 0 severe -> REVIEW | OCR-only REVIEW -> **KEEP** (>= 2 OCR and >= 1 severe stays DROP) | no REVIEW outcome in v2 |
| `replacement_chars >= 8` / `mojibake_hits >= 16` -> REVIEW | encoding REVIEW thresholds -> **DROP** | the reviewed high-tail samples showed real visible corruption |

## Unchanged from v1

- `nul >= 1` and `noncharacters >= 1` -> DROP. Whole-document dropping is preferred
  over introducing a production transformation.
- The combined encoding DROP (both signals) and the encoding-plus-forbidden-C0/C1
  DROP.
- All six severe-repetition cuts and the three `finepdfs_en` OCR cuts, per component.
  They are frozen from the SAME authenticated Phase-A audit-v3 conservative candidate
  and must equal the frozen v1 cuts exactly; the freeze refuses otherwise.
- The guardrails and the KEEP protections: URLs, generic/light markup, code examples,
  math/tables, ordinary boilerplate, page numbers, headers, legitimate Unicode,
  non-ASCII, BOM, bidi, zero-width, ZWJ/ZWNJ. These hold unless another explicit v2
  DROP rule fires.
- No TRANSFORM. The run is a dry run only.

v2 has no REVIEW outcome; its outcomes are KEEP and DROP.

## Provenance

- The v2 template's pinned `policy.human_review` section records:
  - `successor_of: cleaning_policy_v1`;
  - the v1 rule-set digest;
  - the number of reviewed examples (119);
  - the five changes above.
- `clean-freeze-policy` for v2 requires `--predecessor` (the frozen v1 file). It
  refuses unless:
  - the authenticated Phase-A receipt is the predecessor's;
  - the copied cuts equal the predecessor's.
- The frozen v2 file records the predecessor's version, policy digest, file SHA-256,
  Phase-A receipt digest and thresholds digest.

Tests, commands and evidence:
[evidence](../evidence/QUALITY-CLEANING-POLICY-V2/COMMANDS.md);
[runbook](../../runbooks/quality-cleaning-dry-run.md#policy-v2-human-reviewed).
