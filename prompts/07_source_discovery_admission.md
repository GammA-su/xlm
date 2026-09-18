# Prompt 07 — Dataset discovery and source-admission system

Read the shared agent instructions, architecture, contracts, evaluation policy and current status before editing. Implement this milestone in the existing repository; run its tests and update the ledger.


Implement a source catalog and read-only discovery adapter for Hugging Face Hub and explicit HTTPS/local manifests. Read the provided twenty-candidate catalog. Treat names/subset hints as discovery inputs, not verified schema. No FineWeb/FineWeb-Edu source is enabled or used as fallback.

Implement `xlm data sources`, `xlm data probe --source ID`, `xlm data audit` and an explicit admission record workflow. Resolve repository existence, immutable revision, file inventory, configurations/splits, actual Arrow/record schemas, size metadata, auth/gating requirements and model-card/license references. Inspect representative rows only within a small configured public metadata/sample budget. Redact tokens and sensitive fields. Metadata or schema changes must produce a new probe fingerprint.

A source record separately tracks availability, schema verification, tested view/adapter, extraction/normalization policy, license/provenance review, lineage findings and operator approval. The software records evidence and policy status; it must not purport to certify legal rights. Unknown/gated/unavailable sources remain disabled and explain why. Do not use a guessed `text` column or automatically concatenate arbitrary values. For varying subsets, create separate schema fingerprints/adapters.

Discover license/subset differences within broad source families rather than copying only the repository’s top-level tag. Preserve original source provenance and derivative relationships where available. The default strict commercial/research policy choice is explicit; a permissive repository tag cannot erase unknown upstream restrictions. Disable benchmark-containing blends pending a component audit.

This prompt permits bounded public metadata discovery when network access is available: a configurable total ceiling of 32 MiB, limited requests/time, no gated downloads or terms acceptance. Normal tests remain offline with mocked transports. If network access fails, report live discovery NOT RUN/BLOCKED while finishing and testing the implementation. The production catalog stays unadmitted.

Acceptance: probes distinguish missing, private/gated, schema-mismatched and working sources; revisions are real and immutable; an unknown license is not auto-approved; nested field inspection works; missing `text` is handled explicitly; denial policy catches a FineWeb fallback; secret redaction and request caps are tested. Verify at least one public provider response when allowed and clearly distinguish that from complete admission.


Finish with actual commands/results, artifact paths, blockers and the next prompt. Never label mocks, skipped tests or unrun research campaigns as verified.
