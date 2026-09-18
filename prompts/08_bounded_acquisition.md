# Prompt 08 — Resumable bounded data acquisition

Read the shared agent instructions, architecture, contracts, evaluation policy and current status before editing. Implement this milestone in the existing repository; run its tests and update the ledger.


Implement data acquisition from admitted plans, not unrestricted downloads. Prefer explicit file/row-group selections and Hugging Face revision-pinned files; support JSONL, compressed JSONL and Parquet, and local files. Use publisher-provided extracted text for PDF source families. Do not implement default raw-PDF OCR, website crawling or dataset remote-code execution.

Create `xlm data plan`, `xlm data fetch`, `xlm data status` and `xlm data verify`. A plan specifies selected revision/files/rows, schema, selection seed/frame, candidate/approved source state, output artifact, limits and expected bytes where known. Fetch needs a matching authorization for large operations; a pilot can have explicitly approved small limits. Fail before an unbounded full shard/repository download when the provider cannot honor the plan.

Support bounded retries with backoff/jitter, rate limits, conditional requests, Range/ETag validation when supported, verified restart when not, atomic partial files and persistent progress. Count all bytes transferred, including retries and library caches; keep HF/HTTP caches inside the monitored root or account for them. Track decompressed output and temp disk as separate capacities. Stop before disk exhaustion with a recoverable state. Unknown remote size is guarded by streaming counters and hard caps.

For Parquet, use bounded metadata/row-group reading when feasible. A row-count `.take(N)` is not a network-byte guarantee; instrument actual I/O or choose a bounded acquisition mode that is measurable. Do not claim uniform corpus sampling from a few convenient shards. Freeze the sampled frame and show date/domain/file coverage and selection bias in the report.

Protect archive/path handling, redirects, credential scope and host allowlists. A remote record cannot direct the client to download another unapproved URL or execute code. Verify shard checksums after completion; never publish a manifest for an incomplete output. Make cache reuse observable.

Acceptance: local HTTP fixture simulates dropouts, ignored Range, changed ETags, 429s, oversized data, malicious redirects and checksum failure. Test disk/output ceilings, cache accounting, parallel-worker limits and interrupted resume. A pilot must be reproducible by selected file/row IDs. The fixture test is not a live-source verification; run a permitted small real fetch only after an admitted pilot plan exists.


Finish with actual commands/results, artifact paths, blockers and the next prompt. Never label mocks, skipped tests or unrun research campaigns as verified.
