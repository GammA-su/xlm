# Dry analysis preparation — custodian only

The result review passed. These manifests contain bindings and instructions,
not document text, human labels, policy results or a reviewer package.

- [M manifest](m-analysis-preparation.json): 4,096 rows, eight exact windows,
  frozen policy/evaluator hashes and eight verified development result artifacts.
- [T manifest](t-analysis-preparation.json): 118 selected entries, 117 reviewable
  full texts and one oversized nonreviewable entry, source hashes, frozen
  blinding/rubric/order rules and explicit materialization constraints.
- [T per-entry bindings](t-entry-bindings.json): source-line, locator hash,
  status, length, text hash and record hash only. Custodian data; do not expose
  it as a reviewer ordering or mapping.
- [Code bindings](analysis-code-bindings.json): exact working-file hashes of
  the evaluator, policy, blinding, rubric and scientific constants.

M analysis has not run. The existing sweep CLI cannot directly consume Phase-D
output: its provenance uses `row_index`, while Phase D uses `row`, and its input
receipt schema is different. Build a bounded derived-input adapter, validate
all source identities and hashes, reuse the unchanged evaluator kernels and
preserve metadata verbatim. Do not fabricate old bundle/execution receipts.
Use the section-5 comparison tables with separate development/M denominators
and matched-crawl differences. Seal M results before T unblinding.

All nine corrected development files were located at the explicit F: path
in the M manifest and verified against their historical frozen identities.
The historical X: raw bundle is absent; the dry package does not claim it was
revalidated or silently substitute another raw dataset.

T materialization is a separate step outside Git and outside both G: roots.
Preserve any previously sealed matching K, review IDs and ordered manifests;
otherwise generate one fresh 32-byte K in a restricted custodian location.
Never reroll the frozen order or disclose K/source mappings. Use the existing
`ew2-` full HMAC IDs, scientific namespace `essential-web-evidence-v2.0`,
review-order seed 20260928 and separate reviewer-1/reviewer-2 ordering.
The rubric remains `essential-web-evidence-v2.0-rubric-1`.

The existing `build_package` helper requires nonempty text. Supply the 117
reviewable entries to that helper; preserve the oversized entry's opaque ID
and terminal status in the 118-entry master ledger with no text/excerpt.
Do not misrepresent it as missing acquisition or remove it from denominators.
Its scientific dimensions will be `not_reviewed`, not substantive judgments.

Reviewer material may expose opaque IDs, unchanged full texts where available,
the rubric and blank forms only. Exclude selection category/ownership, B-vs-D,
source/crawl/row information, hypothesis condition and custodian provenance.
Render escaped offline text with no active links, scripts or remote assets.
No IDs, K, reviewer orders, reviewer packages or labels were produced in this task.

Next: use the exact operator prompt in the result-review report. This preparation
does not authorize automatic human labeling or a final selector decision.
