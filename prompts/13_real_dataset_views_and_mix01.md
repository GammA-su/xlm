# Prompt 13 — Real dataset adapters, initial mixture and six controlled treatments

Read the shared agent instructions, architecture, contracts, evaluation policy and current status before editing. Implement this milestone in the existing repository; run its tests and update the ledger.


Use the twenty-candidate catalog and mixture recipes provided with this pack. Implement dataset-specific **views** for the initial mixture through verified generic transports and explicit adapters. Do not assume conceptual category names are upstream fields. Each view has a schema-probe report, real revision, precise selector/render policy, source lineage, adapter tests and admission status.

Initial XLM-Mix-01 valid-target shares: Essential-Web 25% (10% science/explanations, 10% practical procedures, 5% varied prose); Nemotron-CC-v2.1 organic 20% (15% High-Quality, 5% Medium-High-Quality); English FinePDFs-Edu 15%; English SYNTH 15%; Nemotron Wiki-Rewrite 8%; English FineWiki 5%; IFM Pretrain-Behaviors general/planning 5%; selected Common Pile prose 5%; SimpleStories 2%.

Verify exact actual names/configurations/files/metadata. For Essential-Web use real taxonomy/quality fields and document fallback behavior for missing metadata; missing fields cannot silently pass. For Nemotron select organic categories explicitly, not broad repository sampling. For SYNTH preserve required context and render necessary context/question/final explanation; reasoning traces are a separate treatment. For Wiki-Rewrite select only that specialized component. FinePDFs uses publisher-extracted text. IFM subsets may have different schemas and require independent adapters. Treat all percentages as a testable hypothesis, not proven best weights.

Unknown, inaccessible, unapproved or insufficient sources block that mixture. The separate `mix01_no_ifm` treatment explicitly moves the IFM 5% to Essential practical prose; it is not an automatic runtime fallback. Give it a different ID and hash. No FineWeb fallback under any circumstance. Alternative datasets from the catalog can be probed and tested with bounded replacement recipes; do not download all twenty.

Implement M0–M5 from the pack: anchor, less SYNTH, more SYNTH, more PDF, TxT360 web replacement, more practical Essential text. Keep all other settings fixed and produce a structured diff. The TxT360 variant cannot run until its specific web view is verified/admitted.

Acceptance: offline schema fixtures per initial source adapter; mixture weights sum exactly to one; organic/synthetic category separation; English and required-context filters; rejection of missing source or empty selector; visible new treatment ID for the no-IFM alternative. Run a small real pilot only for already approved sources within an explicitly authorized aggregate limit. Report source-by-source READY/BLOCKED/NOT LIVE-VERIFIED status; never report a complete live mixture merely because tests use source-shaped fixtures.


Finish with actual commands/results, artifact paths, blockers and the next prompt. Never label mocks, skipped tests or unrun research campaigns as verified.
