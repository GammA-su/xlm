# Prompt 21 — Protected final evaluation, authorization and release auditing

Read the shared agent instructions, architecture, contracts, evaluation policy and current status before editing. Implement this milestone in the existing repository; run its tests and update the ledger.


Complete the operator-side evaluation/exclusion workflow defined in EVALUATION_POLICY.md. The ordinary coding workspace is development-only by default. Implement request/receipt schemas, checkpoint/manifest binding, authorization/revocation, access logs and release validation without placing real final data or keys in the repository.

Document and test a practical separate-OS-account or separate-machine deployment with a protected data/cache directory outside agent mounts. Check actual access restrictions where possible. Same-user hidden paths or a YAML sealed flag must fail a sealed-mode readiness check. Public availability and pretrained-agent familiarity remain documented limitations, not problems solved by encrypting a file.

An operator prepares exclusion receipts for fixed corpus submissions and final evaluation for preselected checkpoint hashes. Bound queries and returns to avoid exposing an adaptive final-set membership oracle. The final evaluator runs a reviewed scoring bundle with no network, controlled read-only inputs, bounded resources and restricted outputs. Arbitrary candidate code with unrestricted access to final labels is not a secure scorer merely because it runs in a subprocess. Establish a trusted scorer/model adapter boundary and document remaining sandbox limitations.

Implement `xlm final request`, operator-side `xlm final execute`, `xlm final verify-receipt` and `xlm release audit`. The development process cannot execute a protected request without operator credentials/environment. A receipt binds weights, data/scorer/protocol, model code, context/precision, approved aggregate outputs and exposure classification. Keep detailed final predictions private; ordinary reports import only approved summaries after selection is closed. Bind decontamination receipts similarly.

Release audit verifies data-use/admission records, source lineage, code/lock/config hashes, reproducibility instructions, benchmark exposure labels, parameter and compute disclosures, attribution files, export integrity and absence of secrets/final labels. Unknown rights/provenance are BLOCKED or explicitly unresolved, never silently certified.

Acceptance: two-identity/mount integration test when available; otherwise synthetic protocol/permission tests plus operator setup NOT RUN. Unauthorized requests, altered hashes, replayed/unapproved requests and forbidden output fields fail. Verify no real final examples were fetched during development. Security limitations must be explicit. This milestone can implement the mechanism without falsely certifying a protected deployment that the current environment cannot provide.


Finish with actual commands/results, artifact paths, blockers and the next prompt. Never label mocks, skipped tests or unrun research campaigns as verified.
