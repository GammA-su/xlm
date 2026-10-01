> **FINEWIKI P01 ROW-LIMIT RECOVERY (2026-10-01): FINEWIKI P01 RECOVERY READY
> FOR OPERATOR DIGEST REVIEW.** p01 `fd8c67df...` f00001 (`000_00011`, 417,809
> rows) failed the 162,540 row bound; f00000 (`000_00014`, 446,535 rows by one
> bounded footer read: 2 ranged requests, 2,900,003 B) was cancelled with
> 1,677,721,600 verified bytes. Cause: v1 calibration layout divides compressed
> file bytes by a dense group's logical bytes per row (estimate error, not a
> planner defect). An exact offline scan of 000_00011 also exceeds the record
> (13,566,858 B) and canonical (2,733,162,301 B) ceilings. FineWiki-only bounds
> (1.35x rule): rows 602,823, record 18 MiB, canonical 3,689,769,107; others
> unchanged. Generic fix: repair plans adopt the predecessor's verified scratch
> partial. **Repair p02 digest
> `319d6bf3dbb7ef085b637c27ea75a89d9d1a97f6b1337153cfebebb1844a305b`, NOT
> authorized**; resume-check: retry 1, resumable partial 1, 860,310,719 B
> network; cursor 2. 4 new + 94 related tests pass (-n 4); -n 16 flake noted.
> [Report](reports/FINEWIKI-P01-ROW-LIMIT-RECOVERY.md). Next: review digest,
> authorize, `run --plan 2` (online, resumes f00000).

> **FINEPDFS P01 RECORD-LIMIT RECOVERY (2026-10-01, offline): FINEPDFS P01
> RECOVERY READY FOR OPERATOR DIGEST REVIEW.** p01 unit f00000 (rank 0,
> `000_00022.parquet`, sha256 `b33dba5f...c3c2`, retained) failed on row 52,794
> (35,618,267 B > 32 MiB). A full offline scan shows it is the true maximum and
> the only row > 32 MiB; all 23 rows > 4 MiB are rolmOCR/truncated and
> adapter-rejected. New source bound `finepdfs-record-v2` = 48 MiB (1.41x);
> parser stays 32 MiB (whole-file: Thrift metadata only); generic/UltraX 8 MiB
> unchanged. Generic `plan-repair`: a new plan for exactly the unsealed ranks
> under changed limits, binding failed receipts and retained SHA-256, keeping
> cursor 3; repaired plans cannot run; an incomplete pass is never SUFFICIENT.
> Offline W4/serial adaptation at 48 MiB is byte-identical (152,614 docs,
> 488,030,588 est. tokens, 1.91 GB peak tree RSS). p01 `de220b01...` and its 2
> sealed units unchanged (rebuild reproduces it); UltraX seal unchanged.
> **Repair p02 digest `98209572a1939769dc3032562d2887bb8da4912b4936c1845f450a1bdb9799ad`,
> NOT authorized.** 29 focused + 427 related tests, ruff/format/strict mypy pass.
> No network, run, seal, C05, tokenizer, training or push.
> [Report and operator commands](reports/FINEPDFS-P01-RECORD-LIMIT-RECOVERY.md).
> Next: operator reviews the p02 digest; authorize; `run --plan 2 --offline`.

> **FINEPDFS INTRA-FILE PARALLEL (2026-10-01, offline): FINEPDFS PARALLEL
> PROCESSING READY FOR PRODUCTION PLANNING.** Generic row-group workers decode
> and adapt one verified local Parquet file. The coordinator replays them in
> file order through the unchanged bounds, so outputs and first errors equal
> serial. Owned shard `4eeb58bc...a38d`, 3 interleaved warm-cache repeats:
> serial 124.0 s, W2 63.5 s, W4 35.9 s (3.45x, 1.46 GB tree RSS), W8 25.0 s
> (4.96x). 3 files x W4 give 2.06x aggregate throughput over today. All 24
> runs reproduce `documents.jsonl` `f6d9bd01...0bd`, the ledger, summary and
> selected-record SHA-256. FinePDFs plans now bind `row_group_parallel` (W4,
> lookahead 4, 15 slots, 6 GiB); other plans and the frozen policy
> `f3a52411...` and sizing are unchanged. 324/324 related tests;
> ruff/format/strict mypy pass. No network, plan, C05, tokenizer, training or
> push. [Report](reports/FINEPDFS-INTRAFILE-PARALLEL.md). Next: build the
> FinePDFs production plan for review once its inventory exists; stop at the
> digest.
> **GENERIC HUB INVENTORY LISTER (2026-10-01, offline): READY FOR OPERATOR
> METADATA LISTING.** Bounded paginated `huggingface` tree lister
> (`src/xlm/data/sources/hf_inventory.py` v1) separates live metadata listing
> from freeze; `mix01_inventory.py` gains `list-hf` (network, future operator
> only), `verify-listing` (offline) and `freeze --listing`. FinePDFs filter is
> configuration (`data/eng_Latn/`, `.parquet`), never hard-coded. Ordering and
> v1 freeze/digest semantics unchanged. 40 new + 149 related cases pass;
> ruff/format/strict-mypy/diff-check pass. No live Hub/payload/plan/run/push.
> [Report](reports/HF-INVENTORY-LISTER.md).

> **FINEPDFS WHOLE-FILE POLICY (2026-10-01, offline): FINEPDFS POLICY DIGEST NEEDS
> OPERATOR REVIEW.** Measured freezes can now bind a receiptless `range_selected`
> disposition (`range-v1-reach-v1`, `non_comparable`: 64/221 groups refused) next
> to the real b3 whole-file receipt, as `mix01-transport-policy-v2` (not a speed
> comparison). Whole-file sizing (`mix01-whole-file-sizing-v1`: 220,407 rows,
> 65.39% accepted, 8,096.7 canonical B/row) supersedes the 1,000/1,000
> calibration: 3 files, not 2. Sampling block bytes are v2 (true compressed).
> UltraX identities unchanged. Rehearsed digests: reach `27ceafe9…`, policy
> `f3a52411…`. Inventory needs a lister milestone. No network or G: writes.
> [Report and operator commands](reports/FINEPDFS-WHOLE-FILE-POLICY.md).

> **FINEPDFS RANGE ROW-GROUP AUDIT (2026-10-01, offline): FINEPDFS RANGE PATH
> NOT COMPARABLE.** Group 178 is refused by `total_byte_size` 51,723,873 >
> `max_parser_bytes` 33,554,432 (generic sample-blocks default, same rule as
> range `check_row_group`). File-wide 64/221 groups refused (29% rows, 54%
> projected bytes); longest usable run 9,000 rows; no contiguous 20k region.
> One-interval `row_ranges` forbids skipping; range-reachable rows are 36%
> lighter, the 4k block 50% lighter, than what whole-file b3 processed.
> No code, digest, benchmark, network, or commit.
> [Audit](reports/FINEPDFS-RANGE-ROWGROUP-AUDIT.md).

> **FINEPDFS B3 PROCESSING GROWTH (2026-10-01, offline): FINEPDFS B3 READY
> FOR OPERATOR DIGEST REVIEW.** Verified-source growth and pre-worker processing
> reservations now share frozen, pre-write output/state/progress bounds. Fresh
> and local-reuse envelopes fit a derived 19,038,848,034 B scratch cap; existing
> bytes and hard-link paths count. b3 digest `aa539f07...e1f35`, plan hash
> `55d9a94f...abeab`; only `benchmark.json` exists, no authorization/adoption/run.
> Real offline checks preserve all 15 b1/b2 operator files, archived b1 inventory
> and b2 source/state; UltraX's 12-unit first-pass seal re-verifies unchanged.
> Essential's shared-code compatibility chain gains one additive exact-hash link.
> 192 distinct focused cases have passing results; final affected selection
> 34/34 pass. Ruff/format/strict mypy pass on 18 Python files. No full acceptance,
> whole-file performance, network, range, production, C05, tokenizer, training
> or push. [Report](reports/FINEPDFS-PROCESSING-GROWTH.md). Next: review
> `G:\XLM\plans\finepdfs\benchmarks\b3\benchmark.json`; stop at its digest.

> **FINEPDFS B1 ARCHIVE / B2 CAPACITY (2026-10-01, offline): FINEPDFS B2
> REQUIRES B3 FOR SCRATCH CAPACITY.** b1's five scratch files were moved to
> `C:\XLM-scratch-history\finepdfs\bench-b1`; hashes/sizes/mtimes verify, with
> durable archive receipt `e5ac48be...afb83`. Donor adoption resolves verified
> archives; historical receipts/events stay unchanged. b2 source and state
> reverify, complete reuse=1, network bytes=0. Active occupancy is now
> 2,771,022,236 B; remaining 2,771,750,500 B cannot cover the planner's
> 10,721,899,142 B processing allowance, before transient metadata. Task 4 STOP:
> generic scheduler repair and real b2 retry NOT RUN; b3 needs larger capacity
> and bounded output enforcement. b2 identity/authorization and all limits
> remain unchanged. 38 distinct focused cases passed; ruff/format/strict mypy
> pass. [Report](reports/FINEPDFS-SCRATCH-ARCHIVE.md). Next: review the local
> archive commit (`git show --stat HEAD`), then repair growth accounting and
> prepare b3 for review; no range benchmark, production run or push.

> **FINEPDFS B2 SCRATCH INVESTIGATION (2026-10-01, offline): FINEPDFS
> SCRATCH ACCOUNTING STILL BLOCKED.** Exact ScratchCapError reproduced.
> Scheduler reserves 5,542,772,736 B before complete-file reuse verification;
> adopted input is 2,771,021,138 B. Even corrected zero-growth source accounting
> cannot fit: the whole source scratch root is 5,678,988,129 B, already
> 136,215,393 B above the unchanged cap, including retained b1 output. Task 5's
> explicit stop condition applies. No code fix, cleanup, successful retry or
> commit; tests/ruff/format/mypy NOT RUN. Hash/state and authorization checks
> passed; local_complete_reuse=1, required network bytes=0. Existing b2
> authorization remains valid. Failed reproduction adds performance-01.json;
> prior history stays. [Report](reports/FINEPDFS-SCRATCH-ACCOUNTING.md).
> Next: review the report and resolve storage placement/processing capacity
> before resuming the narrow repair. No range benchmark or production run.

> **FINEPDFS WHOLE-FILE RECORD BOUND (2026-10-01, offline): FINEPDFS
> BENCHMARK REQUIRES NEW AUTHORIZATION.** Real b1 failed closed on a
> 9,064,396-byte row (generic `max_record_bytes` 8 MiB). An offline scan of the
> retained file (220,407 rows, sha256 `4eeb58bc…a38d`, repository-declared
> digest matches) found a 24,828,818-byte maximum. 3 rows exceed 8 MiB and 0
> exceed 24 MiB; every row above 4 MiB is a `rolmOCR` row. New source-specific,
> digest-bound bound for FinePDFs `eng_Latn`: 32 MiB, equal to the parser ceiling.
> A larger row still fails closed. Every other source keeps 8 MiB, and the
> UltraX p01 plan and seal are unchanged. b1 stays as history. Next: plan,
> authorize and adopt b2 (`benchmark adopt --donor b1`, offline, verified
> hard link, no redownload), then run b2. Tests: 12 new + 46 related passed;
> ruff exit 0; mypy NOT RUN (app control). Report:
> `reports/FINEPDFS-RECORD-BOUND.md`.

> **MIX-01 LIVE READINESS FROM CERTIFIED EVIDENCE (2026-10-01, offline):
> ULTRAX READY.** `mix01-status` read only the static registry flag
> `live_verified`, so admitted UltraX stayed NOT_LIVE_VERIFIED. Readiness now
> derives live verification for admitted components from the verified,
> real-observed bridge receipt. The receipt must bind the exact registry pin and
> the current adapter code, and the admission decision must bind it. Any gap
> fails closed. Real store: Essential ×3 + UltraX READY, **4/12**. No operator
> artifact changed. Tests: 13 + 315 related passed; ruff/format exit 0; mypy NOT
> RUN (app control). Report: `reports/MIX01-LIVE-READINESS.md`.

> **MIX-01 HIGH-THROUGHPUT SOURCE ACQUISITION + ULTRAX ADMISSION BRIDGE
> (2026-10-01, offline): READY FOR ULTRAX PERFORMANCE BENCHMARK.** UltraX needs
> no re-probe: a new offline bridge (`certified_evidence.py`, amendment
> `c04-certified-evidence-bridge-v1`) turns the existing real schema-probe
> receipt (`c67e9152…`), its 30 rows and the real 1,000-row calibration into
> accessible, schema-verified, fingerprinted C04 evidence; the adapter re-runs
> on every real row and reproduces the recorded documents digest `cd7c157a…`
> (dry run: receipt `b874f47e…c3ecf9`, fingerprint `ca72a0e8…41f8`; nothing
> published). The same command bridges FinePDFs, SYNTH, Wiki-Rewrite, FineWiki,
> both IFM views and SimpleStories from their real calibrations. Root cause of
> Essential-Web showing UNADMITTED: `audit`/`mix01-status` looked up view
> `default`; admission is per view. Fixed in reporting (real store: 3 Essential
> views admitted/READY); production gating was always per view. Non-Essential
> mitigated admission: `c04-benchmark-risk-v3` (Mix-01-pool C05 obligation,
> four reviews, provenance, resource contract); Essential v2 unchanged.
> New: versioned transport policy (`mix01-transport-policy-v1`, wall-time model,
> executable modes only, fastest reported), deterministic production planner
> (frozen-inventory prefix, hash-chained top-ups, benchmark-reserved tail), and
> a reusable whole-file engine (resumable streams, verification, retained
> upstream Parquet, pipelined worker processes, restart classes, root-failure
> report, dashboard, performance receipts, sufficiency, first-pass seal) with
> bounded benchmarks. UltraX modeled: whole-file 12.67 GB / 24 requests vs
> selected 3.60 GB / 3,208 requests (amplification 3.53, corrected from 2.37);
> preview plan 12 files, ranks [0, 12), transfer ceiling 19.0 GB. Operator
> review, admission, benchmark, plan authorization and acquisition NOT RUN.
> Tests: 52 new passed; 38-file related selection 867 passed + 1 basetemp-location
> failure that passes with the default temp root, 3 serial passed, 0 skipped;
> ruff/format pass; strict mypy NOT RUN (blocked by Windows application
> control); Essential seal `a77c78f7…` re-verifies, store unchanged.
> [Report and runbooks](reports/MIX01-HIGH-THROUGHPUT-ACQUISITION.md),
> [operator guide](../runbooks/mix01-source-acquisition.md). Next: run the
> UltraX runbook through `review show`, record the decision, `admit`, then the
> bounded benchmark and `plan`; stop at the PLAN DIGEST.

> **MIX-01 TRANSITION AFTER THE ESSENTIAL-WEB FIRST PASS (2026-10-01, offline):
> MIX-01 ACQUISITION TRANSITION STILL BLOCKED.** Essential-Web first pass
> verified from artifacts: 18 batches, 576 sealed files, 47,979,123 rows. Views:
> science 2,692,218,118 / practical 6,962,804,542 / prose 20,071,278,364
> canonical bytes = 673.1M / 1,740.7M / 5,017.8M estimated tokens, all SUFFICIENT.
> The runner stopped `FIRST_PASS_COMPLETE` after Batch 17; batches 18/19 never
> existed. C05, tokenizer and training NOT RUN. No native freeze fits a pre-C05
> component pool (`PoolFreeze`/`FrozenPoolManifest` need dedup + split
> identities), and the campaign's summaries carry no digest. A new additive
> write-once seal `G:\XLM\plans\ew-fast\first-pass-seal.json`, digest
> `a77c78f7636695ef0ab241c176e07cc9bcfd1815907a13f55dc2f6f7c28c7516`, binds
> source, revision, B-normal, adapter, campaign, inventory, quotas, recovery +
> compatibility chain, admissions, 18 batches, 576 receipts and per-view
> membership. Its build re-hashed 5,760 files (198.4 GB) in 353 s; only that
> file was added to the store. It records availability, not exposure: the
> oversupply is kept, no weight/quota/selector change, and C05, tokenizer,
> exact count, top-up and 6B freeze are pending. C05 is global (dedup survivor
> rule and corpus-dependent exclusion span suppression), so it is deferred
> until all of Mix-01 is available. Next source by readiness: UltraX
> (revision, inventory, calibration, estimate 5.25 GB, adapter certified on 30
> real rows). It and every other source are blocked: the generic `data probe
> --live` cannot produce accessible schema-verified evidence, and no production
> planner exists. License/provenance/benchmark reviews are pending. Common Pile
> stays BLOCKED (NULL license, no allowlist). Transport: UltraX and FinePDFs
> keep the bounded fetch; SYNTH needs measurement. Tests: 11 new; a related
> 30-file selection gave 702 + 3 serial passed and 2 known long-path failures,
> which pass with a short basetemp; 0 skipped; ruff, format and strict mypy
> pass. Full acceptance NOT RUN.
> [Report, ledger and runbook](reports/MIX01-ESSENTIAL-FIRST-PASS-TRANSITION.md).
> Next: implement the UltraX footer-schema admission probe and production
> planner (report §11, §15); no network until that lands.

> **ESSENTIAL-WEB BATCH-3 MALFORMED STOP (2026-09-30, offline): READY TO RESUME
> AUTOMATED ESSENTIAL-WEB ACQUISITION.** Batch 3 stopped at 27/32 on f00110
> (`data/crawl=CC-MAIN-2024-26/train-00549-of-03168.parquet`, rank 110, SHA-256
> `968bedb4…d4ea`, 78,689 rows) with `MalformedLimitError`. Offline replay: 42
> malformed rows (0.053%, inside the 1% budget; 123 sealed files pool to 0.048%).
> The per-pass budget was judged on every prefix, and 3 of the first 283 rows
> stopped the file at row 282. The rows are frozen validity failures
> (`unknown_label:k` `Abstain`/`Metacognitive`, `invalid_fdc_syntax` `-1` and
> garbled codes), all `rejected` under B-normal, and stay counted malformed.
> Amendment `essential-web-malformed-whole-pass-v1` (local worker only): the
> frozen counter must fire and malformed rows must exceed 1% of the whole file;
> it never stops what the old rule passed. Adapters, selector, `malformed.py`,
> quotas and output bytes are unchanged. f00110 now completes offline (852 /
> 2,569 / 7,342 docs); f00123 has no blocker. Restart: 27 sealed, 2 local, 3
> partial (603,979,776 B kept), 0 fresh. The campaign loads through a new
> compatibility record `9c9b618b…7f18`; Batch-3 authorization is unchanged; the
> old auto envelope `21cbad8c…d9e` is refused. Store untouched (1,601 files).
> Tests: 16 new; selection 802 + 3 serial passed, 0 skipped; ruff, format and
> strict mypy pass. Batch-3 resume and full acceptance NOT RUN; C05 NOT RUN.
> [Report](reports/ESSENTIAL-WEB-BATCH3-MALFORMED.md). Next: dot-source
> `scripts/operator_storage.ps1`, then
> `scripts/operator_essential_web_fast.ps1 -Stage Run -Batch 3`, then
> `scripts/operator_essential_web_campaign.ps1 -Stage PrepareAuto -MaxBatches 20`
> and `-Stage RunAuto -Authorize <new digest>`.

> **ESSENTIAL-WEB AUTOMATIC CAMPAIGN RUNNER (2026-09-30, offline): READY FOR
> AUTOMATED ESSENTIAL-WEB ACQUISITION.** Authoritative state: 3 complete batches,
> 96 sealed files, 7,956,430 rows, 32,970,294,061 B footprint. Science
> 118.9M/660M and practical 300.9M/660M estimated tokens are TOP_UP; prose
> 840.1M/330M is SUFFICIENT. Next is Batch 3, `CLEAN_NOT_STARTED` (membership
> `bb7fd547…4480`, batch authorization `f25da873…f56c`). A one-time bounded
> envelope (at most 20 batches; now 3–22) binds campaign, source, revision,
> selector, adapter, inventory, limits, running code and every child's exact
> authorization digest. Before each batch, `RunAuto` re-derives the child and
> compares it with the envelope. It then uses the unchanged
> plan/authorize/gate/C04/executor path. It stops when the first-pass targets
> are met, or on any human-review condition; it never approves a recovery,
> record bound or identity change. Real-store dry run: 0 network attempts,
> 1,196 files unchanged; the derivation reproduces the Batch 0–2 authorizations.
> Tests: 26 new; related selection 786 + 3 serial passed, 0 skipped; ruff,
> format and strict mypy pass. Batch 3, `RunAuto` and full acceptance NOT RUN;
> C05 NOT RUN; training not permitted.
> [Report, evidence and operator commands](reports/ESSENTIAL-WEB-AUTO-CAMPAIGN.md).
> Next: dot-source `scripts/operator_storage.ps1`, then
> `scripts/operator_essential_web_campaign.ps1 -Stage PrepareAuto -MaxBatches 20`
> and `-Stage RunAuto -Authorize <printed digest>`.

> **ESSENTIAL-WEB BATCH-1 WINDOWS PUBLICATION FIX (2026-09-30, offline): READY TO
> RESUME ESSENTIAL-WEB BATCH 1.** Authoritative state: 10/32 sealed (31.25%),
> 848,755 rows. Root failure: f00035 `PermissionError` (errno 13 / WinError 5)
> from the worker's live-progress `os.replace(.progress.tmp -> .progress.json)`
> while the parent monitor held the target open; reproduced offline on NTFS.
> The 8 `TransferCancelledError`s were the cooperative cascade. Fix: a per-unit
> lock coordinates replace and read (no sleeps or retries), the monitor no
> longer opens live download state, and the root is reported apart from
> cancellations with a restart plan. Restart: 10 sealed skipped, 1 local
> (f00035 durable raw), 5 Range resumes (872,415,232 bytes kept), 16 fresh.
> 555 operator files are hash/size/mtime-identical; campaign, Batch-1 plan
> and authorization unchanged; an additive code-compatibility record binds the
> fix. Real resume and full acceptance NOT RUN.
> [Report, evidence and operator command](reports/ESSENTIAL-WEB-BATCH1-WINDOWS.md).
> Next: dot-source `scripts/operator_storage.ps1`, then
> `scripts/operator_essential_web_fast.ps1 -Batch 1 -Stage Run`.

> **ESSENTIAL-WEB RECOVERY SCOPE FIX (2026-09-30, offline): READY TO RUN
> ESSENTIAL-WEB BATCH 1.** Batch 0 is complete: 32/32, 2,604,815 rows. Batch 1
> has zero raw/processed/sealed files, no scratch directory, and a valid existing
> plan/authorization; its failed attempt stopped before transport. The historical
> recovery now matches only Batch 0/f00026 plus its exact path/content identity.
> Other batches use their normal 8 MiB bound and authorization. No Prepare or
> reauthorization needed; campaign and original recovery digests unchanged.
> 398 operator artifacts are hash/size/mtime-identical; dashboard/ETA unchanged.
> Related regressions: 744 passed, zero skipped (288.16 s); scoped ruff,
> format and strict mypy pass. Full acceptance and real Batch 1 NOT RUN.
> [Scope fix, evidence and operator command](reports/ESSENTIAL-WEB-RECOVERY-SCOPE.md).
> Next: dot-source `scripts/operator_storage.ps1`, then
> `scripts/operator_essential_web_fast.ps1 -Batch 1 -Stage Run`.
> No external network, redownload, production execution or push in this task.

> **ESSENTIAL-WEB BATCH-0 RECOVERY (2026-09-30, offline): READY TO RESUME
> ESSENTIAL-WEB BATCH 0.** Authoritative receipts and hashes verify 31/32 sealed
> (96.875%), only `f00026` remaining; its full source is retained. Row 58,327 is
> structurally valid and B-normal practical: 11,494,172 encoded bytes exceed
> the old 8,388,608 bound. An additive, hash-bound amendment permits exactly
> 11,494,172 bytes for that file only. Original campaign, plan, authorization,
> science and all 31 seals stay intact. Dry resume schedules one unit and zero
> downloads; 341 sealed artifact sizes/mtimes are unchanged. Private offline
> replay passes all 69,697 remaining rows in 47.5 s, sampled RSS 358.6 MB;
> production remains 31/32. Focused regressions: 733 passed, zero skipped;
> ruff/format/strict mypy clean. Full acceptance and CUDA NOT RUN.
> Dashboard, durable events, smoothed overlap ETA
> and sealed campaign target progress added without dependencies. Exact checks,
> limitations and the new operator authorization digest are in the
> [recovery report](reports/ESSENTIAL-WEB-BATCH0-RECOVERY.md). No external network,
> redownload, production restart or push. Next: review the amendment and run the
> report's `-Stage Resume -RecoveryAuthorize <digest>` command (offline-only).

> **ESSENTIAL-WEB FAST TRANSPORT IDENTITY FIX (2026-09-30, offline): READY FOR
> HIGH-THROUGHPUT ESSENTIAL-WEB BATCH 0. NO NETWORK, NO REDOWNLOAD.** The live
> benchmark (operator) measured 22.7 / 26.0 / 142.7 MB/s at 1 / 4 / 8 streams,
> 11,363 rows/s at 12 processes, 0 retries, and real-byte parity IDENTICAL. It
> was recorded FAIL only because the code required a 64-hex strong ETag to equal
> the content SHA-256. Saved evidence: the storage ETag differs from the local
> SHA-256 in 14 of 14 files, while the repository's `X-Linked-ETag` equals it in
> 14 of 14. What the storage ETag hashes is NOT DETERMINED (`X-Xet-Hash` was not
> captured). New rule: the ETag is an opaque validator for resume and drift; the
> local SHA-256 is always recorded and must equal the plan's or the repository's
> declared SHA-256 when one exists; `X-Xet-Hash` is a separate field. No
> guarantee weakened: the removed check could only fail, the new one refuses
> real corruption. The saved report was re-accepted offline (PASS); its files
> were discarded by design, so no SHA-256 was recomputed. Campaign refrozen as
> `8e42ba31…bb8c` (science unchanged); gate for batch 0 is RUN. Modeled from the
> measured rates: 4.2 min per 32-file batch, 1.73 h for 25 batches (6.8 min and
> 2.85 h at the 4-stream rate); no whole batch has been measured. Focused tests
> 713 passed (33 in the module, 6 new); ruff/format/strict mypy clean; fast/full
> selections NOT RUN. No selector, mixture or quota change; no push.
> [Identity fix report](reports/ESSENTIAL-WEB-FAST-TRANSPORT-IDENTITY-FIX.md).
> Next: `. .\scripts\operator_storage.ps1; .\scripts\operator_essential_web_fast.ps1 -Batch 0 -Stage Prepare`

> **ESSENTIAL-WEB FAST TRANSPORT (2026-09-30, offline): READY FOR HIGH-THROUGHPUT
> ESSENTIAL-WEB BENCHMARK. NO BULK FETCH, NO LIVE REQUEST.** The range-reader
> campaign `644be917…0ce9` was stopped before any fetch and is kept as history;
> fast campaign `d7b1a503…5822` supersedes it with identical science (source,
> revision, inventory order, batch membership, B-normal selector, adapters,
> quotas, stop targets, C04/C05), checked on every load. Root cause of 3.71 h per
> batch: 30,280 range requests, 62% of the time is request latency. Prepare
> failed because 32 footers were read serially under one 60 s budget (2.7 s
> each); Prepare is now offline. Whole files cost 1.1205 times the projected
> bytes (eight real footers) for 2 requests per file instead of 946. Raw artifact
> is now the verified source Parquet (ETag, length, SHA-256), C04 amendment
> `essential-web-raw-artifact-v2`; no `selected_records.jsonl`, nothing deleted.
> Ledgers zstd level 9, 12.70x on the real calibration ledgers. Durable estimate
> 268.4 GB against 749.9 GB; scratch on `C:\XLM-scratch` capped at 64 GiB.
> Local processing reproduces the sealed calibration byte for byte on 16,384
> real rows and equals the certified range reader on authored files; 15,413
> rows/s at 12 processes. Batch stays 32 files; modeled 3 to 11 min by network
> rate, which is UNMEASURED. Focused tests 708 passed (28 new); ruff/format/
> strict mypy clean; fast/full selections NOT RUN. Live benchmark, real-byte
> parity and bulk fetch NOT RUN; the gate refuses batch 0 until the benchmark
> passes. No selector, mixture or quota change; no push.
> [Transport report](reports/ESSENTIAL-WEB-FAST-TRANSPORT.md),
> [bulk plan and commands](reports/ESSENTIAL-WEB-FAST-BULK-PLAN.md).
> Next: `. .\scripts\operator_storage.ps1; .\scripts\operator_essential_web_fast.ps1 -Stage Benchmark -Authorize 5f865608bb75a6272200c73d4a912d58182b7d991c65fff6ab5ed866aa308115`

> **ESSENTIAL-WEB BULK ACQUISITION (2026-09-30, offline): READY FOR FULL
> ESSENTIAL-WEB ACQUISITION. NO BULK FETCH RAN.** Campaign `644be917…0ce9`
> frozen from the sealed calibration. Batches of 32 whole files in frozen
> inventory order (digest `4bbd5517…63d8` verified); one slice per row-group
> index through the certified window reader; write-once ledger; restart skips
> completed work; duplicate rows, source drift and code drift refuse. Science
> is the bottleneck: 64,424,483 rows ≈ 780 files ≈ 25 batches (ceiling 33 as
> execution contingency, targets unchanged). Full-file model from eight real
> footers: 2,820 B/row, 181.7 GB and about 90 h at one worker, against 432.8 GB
> prefix-linear; all estimates. Stop at the first batch boundary where
> cumulative canonical bytes reach 2.64/2.64/1.32 GB (660M/660M/330M estimated
> tokens) via `mix01_inventory sufficiency`; exact sufficiency waits for the
> tokenizer. Practical/prose oversupply (2.8x/23.5x) is kept; quotas unchanged.
> Raw is retained (no contract permits deletion) in one copy: about 750 GB of
> the 1 TB volume, guarded by a 64 GiB reserve and an 850 GiB cap. C05 stays
> NOT RUN and is required on the frozen pool before tokenizer fit and training.
> Dry run on the real inventory: batch 0/1 deterministic and disjoint, zero
> calibration rows counted. Focused tests 505 passed (25 new); ruff/format/
> strict mypy clean; fast/full selections NOT RUN. Driver `Show` ran on the
> real root; `Layout`/`Run` NOT RUN. No selector, mixture or quota change; no push.
> [Plan and commands](reports/ESSENTIAL-WEB-BULK-ACQUISITION-PLAN.md).
> Next: `. .\scripts\operator_storage.ps1; .\scripts\operator_essential_web_bulk.ps1 -Batch 0 -Stage Prepare`,
> then `-Stage Run -Authorize <digest>`.

> **ESSENTIAL-WEB PRODUCTION CALIBRATION SEALED (2026-09-30, offline).**
> The live 16,384-row calibration (8 crawls, 8 files, 2,048 rows each) was
> recomputed from its executed artifacts and every recorded value reproduced:
> 110,070,226 transfer bytes, 94,613,662 decompressed, 301.28 s, 8 malformed;
> science/practical/prose/rejected/unassigned 101/434/1,495/14,214/140; 2,030
> documents, 10,462,526 canonical bytes. The current adapter reproduces all 24
> canonical outputs byte for byte. Seal `149f3eb4…e2a1` binds measurement,
> plans, raw, adaptation, selector, adapter code, admission attempt 2, freeze
> `a6cab8cd…89b0` and revision `ce4eccc…`. Science yields 10.244553 estimated
> tokens per input row (4 bytes/token assumed; not exact tokens) and is the
> bottleneck: 64,424,483 rows for 660M, against 22,754,957 (practical) and
> 2,740,950 (prose). Per-crawl science yield spans 5.08–18.79; clustered
> sample, no interval claimed. Malformed 0.0488%, within policy; dominant code
> `unknown_label:k` (6 of 8). Focused tests 8 passed; ruff/format/strict mypy
> clean; fast/full selections NOT RUN. No network, text in Git, selector or
> quota change, or push.
> [Report](reports/ESSENTIAL-WEB-PRODUCTION-CALIBRATION.md).
> Next: freeze the bulk acquisition campaign from this seal.

> **ESSENTIAL-WEB POST-FIX BINDINGS (2026-09-30, offline): BLOCKED BY ONE OPERATOR ACTION.**
> Adapter code hash changed `56ca4fb2…` → `3651ff2a…`. The enforced C04 gate still
> admits all three views (it does not persist a code hash), but the approved
> prepared package bound the old hash, so a superseding operator admission is
> required. Prepared decisions are resealed to the current hash; attempt 1 stays.
> Probe plan, authorization, fetch receipt and raw (`a1c2b807…a9f5`) remain valid.
> Offline resume ran: science 0, practical 5, prose 26, malformed 0; selector
> totals 223/26/5/2/0 unchanged; `measurement.json` written. Calibration digest
> `a6cab8cd…89b0` and its eight authorization hashes bind no adapter code and are
> unchanged. Focused tests 326 + 35 passed; ruff/format/strict mypy clean.
> No network, fetch, calibration or push.
> [Report and exact commands](reports/ESSENTIAL-WEB-POST-FIX-BINDINGS.md).
> Next: `. .\scripts\operator_storage.ps1; & .\docs\implementation\evidence\ESSENTIAL-WEB-ADMISSION-BOOTSTRAP\future-readmit-after-adapter-fix.ps1 -Operator $env:USERNAME`,
> then `future-calibration.ps1` (live, not run).

> **ESSENTIAL-WEB OPTIONAL FDC FIX (2026-09-30, offline): READY TO RESUME PROBE OFFLINE.**
> Reused the existing 256-row raw probe; no source fetch. Empty optional hierarchy
> labels caused all 108 malformed rows. Scoped renderer repair gives 256/256 base
> renders, prose 26/26 and practical 5/5, zero malformed in all three production
> passes. Frozen B-normal totals/order/thresholds and mixture are unchanged.
> Raw verifies against its existing plan/journal. Focused checks: 383 distinct
> passes across runs; initial two Windows path-length failures are retained in
> evidence and pass with shorter serial paths. Ruff/format/strict mypy pass.
> Resume script parsed, NOT EXECUTED; canonical outputs/measurement remain pending.
> [Report and exact commands](reports/ESSENTIAL-WEB-OPTIONAL-FDC-FIX.md).
> Next: `powershell -NoProfile -ExecutionPolicy Bypass -File .\docs\implementation\evidence\ESSENTIAL-WEB-PRODUCTION-READINESS\resume-probe-adaptation.ps1`.

> **ESSENTIAL-WEB ADMISSION CONTRACT V2 (2026-09-30, offline): READY FOR OPERATOR ADMISSION.**
> Existing real schema probe verified; no new probe. The previously ambiguous
> `clean` classification is replaced by `suspect_with_mitigation` for all three
> Essential views under `c04-benchmark-risk-v2`, with review hashes and a bound
> C05 obligation. All three resealed decisions pass the actual offline C04
> verifier in a temporary store; real operator approval remains pending.
> Official benchmark claims stay blocked until a protected exclusion receipt
> matches the frozen training pool. C05 pool integration/screening is NOT RUN.
> Focused contract checks pass after fixture migrations; an extra reports CLI
> test is blocked by Windows application-control policy on the PyTorch DLL.
> Ruff/format and strict mypy pass. No network, calibration, acquisition or push.
> [Correction report and exact checks](reports/ESSENTIAL-WEB-ADMISSION-CONTRACT-V2.md).
> Next: dot-source `scripts/operator_storage.ps1`, then run
> `docs/implementation/evidence/ESSENTIAL-WEB-ADMISSION-BOOTSTRAP/future-admit.ps1 -Operator $env:USERNAME`.

> **ESSENTIAL-WEB ADMISSION BOOTSTRAP (2026-09-30, offline): READY FOR LIVE
> ESSENTIAL-WEB PROBE AND CALIBRATION.** The C04 rights/provenance/attribution
> and benchmark-risk reviews are written and bound to revision `ce4eccc…`. A
> footer-only schema probe (4 operations, about 8 physical requests, cap 24)
> replaces the 330-request probe as the admission bootstrap; C13's 100-request
> pilot ceiling is unchanged. Probe evidence and decisions now supersede by
> attempt, because the store never replaces the stored `budget_exhausted`
> record. **Admission is still false**: it needs the live schema probe record
> and the operator's approval. Calibration freeze is byte-identical
> (`a6cab8cd…`). No project network request, probe, calibration, acquisition,
> selector or mixture change, or push.
> [Report and exact commands](reports/ESSENTIAL-WEB-PRODUCTION-READINESS.md).
> Next: `future-schema-probe.ps1`, then `future-admit.ps1 -Operator <name>`,
> then the frozen shared probe and calibration scripts.

> **ESSENTIAL-WEB PRODUCTION READINESS (2026-09-30, offline): BLOCKED.**
> B-normal remains frozen and reproduces both 4,096-row metadata replicates
> exactly, with no overlap. Production admission now binds the exact selector;
> isolated malformed rows are counted/quarantined and stop above 1% after 100
> rows (early bound: two). Adopted 23,200 revision-bound paths from eight crawls;
> froze 8 × 2,048 new calibration rows. Full-text-first stays: 228,862,655 modeled
> whole-group bytes either way, 0% expected metadata-first saving. Operator root
> is centrally configured as G:\XLM; stale Essential driver defaults are disabled.
> Admission remains false (license/provenance/benchmark review and accessible
> schema probe missing). The shared dry probe needs 330 requests, exceeding
> the C13 pilot ceiling; no admission/authorization bypass was introduced.
> Calibration design is frozen, but live probe/calibration and science capacity
> are NOT RUN. No network, T text access, selector/mixture change or push.
> [Report and exact commands](reports/ESSENTIAL-WEB-PRODUCTION-READINESS.md).
> Next: resolve the exact missing admission artifacts and C04 schema-probe
> bootstrap before executing the conditional live scripts.

> **ESSENTIAL-WEB FROZEN SELECTOR PRODUCTION INTEGRATION (2026-09-30):
> ESSENTIAL-WEB SELECTOR FROZEN — READY FOR PRODUCTION ACQUISITION REVIEW.**
> Production admission now uses exact frozen B-normal (freeze `c6f32a65…`).
> New adapter `essential_web_bnormal` renders with the certified adapter and
> admits a row only when the frozen evaluator's single final component
> equals the configured component; the evaluator and policy are loaded by
> path and refused unless they hash to the frozen identities. No selector
> logic was rewritten. The three Essential registry views, the column
> contract and the calibration driver bind it; weights, quotas and other
> components are unchanged. Real metadata evidence, row level and per crawl,
> through the production adapters: development 29/108/371/36/3552, sealed M
> 24/117/372/45/3538, each summing to 4096 with 0 overlaps. Rendering plus
> admission ran only on the 3 real certification rows (1 prose, 2 rejected).
> Readiness: selector_frozen, selector_integration_ok, source_revision_ok
> true; production_admission_ok, inventory_ready, acquisition_plan_ready
> FALSE (stored probe is budget_exhausted, no admission decisions, no
> Essential inventory or calibration; text-transfer cost at ~13% admission
> is unmeasured). Focused tests: selector 57, adapter/registry 123, driver 43, evaluator and freeze 126 passed; fast/full selections NOT RUN. The T
> semantic review remains NOT RUN. No network, acquisition, T access or push.
> [Integration report](reports/ESSENTIAL-WEB-SELECTOR-PRODUCTION-INTEGRATION.md).
> Next: Essential-Web production-acquisition review (probe budget,
> inventory, resized calibration, metadata-first decision).

> **ESSENTIAL-WEB SELECTOR FAST-TRACK FREEZE (2026-09-30): B-NORMAL FROZEN;
> T SEMANTIC REVIEW NOT RUN.** Amendment `essential-web-selector-fasttrack-v1`.
> Only one human is available, so the operator chose not to run the frozen
> two-reviewer Arm-T review. Arm-T status is
> `NOT_RUN_NO_TWO_INDEPENDENT_HUMAN_REVIEWERS`: 118 locators acquired (117
> reviewable, 1 oversized), blinded package sealed (`18c95b95…`) and preserved,
> 0 labels, no adjudication, no unblinding. It contributes no acceptance or
> rejection evidence; no model or single-reviewer substitute was used.
> B-normal (unchanged policy B, tier normal, policy digest `f4357f61…`,
> evaluator `5a63e785…`) is frozen as the production Essential-Web selector
> for Mix-01 by explicit operator decision on metadata evidence only. It is
> not a T-validated winner. M seal `afc972cc…` verified before and after.
> Counts recomputed from sealed artifacts (science/practical/prose/
> unassigned/rejected): development 29/108/371/36/3552, M 24/117/372/45/3538.
> Freeze digest
> `c6f32a65f083c99b64245e25151f2cc73275093e1013d68b625c6d6f63d10a0c`, verified
> by full recomputation. Focused tests: freeze 32, frozen evaluator 60, M
> analysis 29, identity 5 passed; fast/full selections NOT RUN. No network,
> acquisition, T text access, labels, reselection, weight change or push.
> [Freeze report](reports/ESSENTIAL-WEB-SELECTOR-FASTTRACK-FREEZE.md).
> Next: integrate the frozen selector into the production adapter.

> **ESSENTIAL-WEB V4.1 T BLINDED PACKAGE SEALED (2026-09-30).**
> The frozen 118-locator Arm-T selection is materialized as a custodian master
> ledger plus two blinded reviewer packages in the access-restricted external
> root `F:\XLM-Review\essential-web-v4.1-t` (722 files, 4,852,481 bytes),
> outside Git and both G: roots. M seal `afc972cc…` verified before and after
> and bound as parent. No prior K/IDs/orders existed: first materialization,
> not a reselection; frozen `ew2-` IDs, namespace v2.0, order seed 20260928.
> Ledger 118 = 117 reviewable + 1 oversized (no text, no excerpt, absent from
> reviewer material). Each reviewer: 117 items, frozen order, verbatim
> 18-dimension rubric, blank forms. Leakage audit over 712 reviewer files and
> an independent scan: 0 structural hits. Package digest
> `18c95b95699922db325626fd8776c2317231c51666f9bcc42898f5811dabc58d`, verified
> by full recomputation. Git holds hashes and counts only: no text, K, review
> ID or mapping. Focused tests 39, identity 5, blinding/rubric 7, M analysis
> 29 passed; fast/full selections NOT RUN. Both G: roots unchanged; no
> network, labels, unblinding, selector decision or push. Reviewers must not
> have repository access (committed entry bindings map text hashes to source).
> [T package report and next action](reports/ESSENTIAL-WEB-EVIDENCE-V4.1-T-BLINDED-PACKAGE.md).
> Next: two independent human reviewers label their own directory only.

> **ESSENTIAL-WEB V4.1 M SELECTOR EVIDENCE SEALED (2026-09-30).**
> The unchanged frozen evaluator and policy (A/B/C/D, normal/strict) ran on the
> verified 4,096-row Phase-D M replicate through a checked
> `derived_analysis_input` adapter (`row` -> `row_index`; metadata, order and
> provenance preserved; no legacy receipts invented). B-normal
> science/practical/prose/unassigned/rejected: 24/117/372/45/3538 (development
> 29/108/371/36/3552); B-strict 11/68/342/36/3639 (development
> 20/62/329/25/3660). Aggregate shares within 0.54 points of development;
> science is sparse and fell at strict; prose genre mix shifted. Descriptive
> only: no ranking, tuning, confidence intervals or selector decision.
> Seal `afc972ccf570a5587bf7bd3b6b8d1d728a09f2230c49f1b0a19c1c74cda0b9bc`,
> verified by full recomputation. Focused tests 29, frozen-evaluator
> regressions 60, identity tests 5 passed; fast/full selections NOT RUN.
> Both G: roots unchanged; no network, T access, human review or push.
> [M analysis report and exact next prompt](reports/ESSENTIAL-WEB-EVIDENCE-V4.1-M-ANALYSIS.md).
> Next: materialize the blinded T reviewer package externally; M must not change.

# Implementation status

> **ESSENTIAL-WEB EVIDENCE V4.1 HOST AMENDMENT (2026-09-29, offline): READY FOR
> NARROW V4.1 HOST-AMENDMENT REVIEW.** The first real v4.0 Phase-P run stopped
> safely on its first request (`M-00-head: host 'us.aws.cdn.hf.co' is not an
> exact allowlisted host`; M 1 attempt / 1098 redirect-body bytes /
> POLICY_REFUSED 1 / 0 complete / 0 retained; T 0 attempts). It is recorded as
> **STOPPED_POLICY_REFUSED**, not success. No scientific output was exposed.
> The v4.0 root `G:\Project\xlm-evidence-v4\essential-web` stays historical.
> v4.1 changes only the exact host set: 17 documented Hugging Face
> lfs/CDN hosts added as signed targets, 19 hosts total, exact equality,
> HTTPS/443, no wildcard. It also sets version `essential-web-evidence-v4.1`
> and a fresh root `G:\Project\xlm-evidence-v4.1\essential-web` (not
> created). Scientific identity, ranges, limits, B01/B02 and engine are
> unchanged; the engine is reused with an explicit profile/host policy.
> Protocol SHA-256
> `3d667a263b92696a2d7a266f896e460b10a81ae38097f788c8b885ce768079cf`; freeze
> `285015604d30e1699b5f63fa4f25ec9c747c778be2f372bb87119adc0e455f80`; new plan
> digest `762cef78051011d518c4918a41aa87cf81fbb3224bf438af8cc1c499cfca7711`;
> membership-and-ranges digest (v4.0 = v4.1)
> `304bd0761125b48720a4de9c82bb6b3bf47100b8a15fc0953d548a8a1dab38bd`.
> Focused v4+v4.1 tests: 337 passed (157 new); wire B01/B02: 23 passed; science
> regressions: 69 passed; ruff, format and strict mypy clean (CPython 3.12.3 Linux;
> the 3.12.13 Windows rerun has NOT been run). No live Phase P, no HF network, no
> corpus text, no push.
> [Protocol](reports/ESSENTIAL-WEB-EVIDENCE-V4.1-PROTOCOL.md),
> [implementation report / next command](reports/ESSENTIAL-WEB-EVIDENCE-V4.1-IMPLEMENTATION.md#6-next-command--do-not-run-in-this-task),
> [freeze](evidence/ESSENTIAL-WEB-EVIDENCE-V4.1/freeze.json).
> Next: narrow v4.1 host-amendment review; only then the operator runs
> `scripts/evidence_v41.py phase-p --confirm-plan-digest 762cef78…7711`.

> **ESSENTIAL-WEB EVIDENCE V2.2 (2026-09-28, offline): BLOCKED — NEITHER
> ARM READY FOR AUTHORIZATION REVIEW.** v2.2 freeze/parents/selection
> verified; v2.0 namespace pinned; only M 16/file changed. M audit: A
> (12>10 proven) + bytes unbound (256 MiB transport ceiling vacuous) →
> BLOCKED. T: dictionary-corrected 47-range schedule, carry reconciled,
> durable ledger repaired, supervisor/disk/deadline mechanisms built
> and tested — but historical bodies/timing unknown and final-disk
> conditional → BLOCKED. 9 DRY child artifacts published (no
> authorization). 163 focused + 67 sampling/bounds tests green;
> ruff/mypy clean. Details in
> [ESSENTIAL-WEB-EVIDENCE-V2.2-IMPLEMENTATION](reports/ESSENTIAL-WEB-EVIDENCE-V2.2-IMPLEMENTATION.md).
> Prior user/Astra docs preserved. No network/text/X: writes/push.

> **ESSENTIAL-WEB EVIDENCE V2.2 PROTOCOL / READINESS REVIEW (2026-09-28,
> offline): READY TO IMPLEMENT EVIDENCE V2.2; ACQUISITION STILL BLOCKED.**
> M known physical history reconstructs to 55 requests (old 7 + complete 48),
> including the proven first-file 12>10 violation. Recorded range bodies total
> 2,191,448 bytes; missing historical redirect/error-body totals remain unknown,
> not a certified zero or 4 KiB/request. Prospective amendment changes only M
> footer requests/file 10->16. Footer arm 80, data 100/plan and 800/arm, combined
> 880 unchanged. One four-byte identity revalidation/file: nominal 16 physical
> requests/arm, prospective maximum 4/file and 25/arm; 55+16+8*85=751 nominal.
> Historical violations remain recorded; all usage carries forward without reset.
> T keeps every cap and all 118 locators. Final disk uses cumulative guarded
> acquisition/publication stages, no cap increase. T child is not ready: all
> eight ranges omit dictionary prefixes; live ledger charges disappear on reload;
> unsupported gap bounds, disk helper defects and missing supervision/history
> remain blockers. Three bounded synthetic probes reproduced defects; no pytest
> suite or implementation change. Parent/arithmetic audit and final checks exit 0.
> Protocol SHA-256:
> `fd698793068458b31563418fdca29c97fa1efe1099b8776c7fd1407887f4f49d`.
> Freeze canonical digest:
> `b6602a445307d9638913c046b4cfebab3356e20559d89b3f2ab601aa924ebd0c`.
> [Normative protocol](reports/ESSENTIAL-WEB-EVIDENCE-V2.2-PROTOCOL.md),
> [review / exact Muse handoff](reports/ESSENTIAL-WEB-EVIDENCE-V2.2-REVIEW.md#5-exact-muse-handoff),
> [freeze](evidence/ESSENTIAL-WEB-EVIDENCE-V2.2/freeze.json),
> [commands](evidence/ESSENTIAL-WEB-EVIDENCE-V2.2/COMMANDS.md).
> Next: Muse offline implementation -> regenerated child-plan review -> separate
> acquisition authorization review. No network, acquisition, text, selection
> change, commit or push. Existing user edits and v2.0/v2.1 artifacts preserved.

> **ESSENTIAL-WEB EVIDENCE V2.1 (2026-09-28, offline): IMPLEMENTED BUT
> ARM-M ACCOUNTING BLOCKS ACQUISITION.** v2.1 freeze/parents/selection
> verified; v2.0 namespace pinned. Amended T transfer caps enforced
> with no-borrowing subcaps/totals. Durable carry-in reconciled
> (54 req / 2,231,492 B + bounded gaps; restarts replay idempotently).
> Exact 47-range schedule with remaining budgets (tightest file: 0
> re-attempts). Memory/disk/scratch fit; final-disk conditional on
> measured labeling. M audit: conclusion A — cumulative 12 > 10
> (proven 3+3 old, 3+3 complete, distinct runs), BLOCKED, blocked-plan
> receipt only. T dry child plan built, no authorization. 203 tests
> pass; ruff/mypy clean. Details in
> [ESSENTIAL-WEB-EVIDENCE-V2.1-IMPLEMENTATION](reports/ESSENTIAL-WEB-EVIDENCE-V2.1-IMPLEMENTATION.md).
> Prior user/Astra docs preserved. No network/text/X: writes/push.

> **ESSENTIAL-WEB EVIDENCE V2.1 AMENDMENT REVIEW (2026-09-28, offline):
> READY TO IMPLEMENT EVIDENCE V2.1; ACQUISITION NOT AUTHORIZED.**
> Independent receipt/hash audit reproduced the eight-file T cost map and
> exact 118-locator membership. New normative transfer-only amendment:
> footer/data/total = 2/30/32 MiB per T file and 16/240/256 MiB per T arm.
> All scientific identities, v2.0 hash namespaces, reader and non-transfer
> limits stay fixed; v2.0 remains historically blocked. M observations are
> adopted by hash; T's original INCOMPLETE map remains the motivation parent.
> Prior attempts carry forward: known T planning 2,231,492 bytes / 54 requests.
> The six-request estimate is not a physical upper bound; memory/disk/runtime
> readiness is unverified. Prior M redirect accounting implies a possible
> 12 requests against its unchanged 10/file footer cap: resource compliance
> remains BLOCKED pending reconciliation, with no waiver or budget reset.
> Protocol SHA-256:
> `1c437881148c3d6c1c42ca610e364055a0625732b07361f2e9271d0918fcdb4b`.
> New freeze digest:
> `bac82d6b9538f4005f7f0ffee6aa5c4f3a5394098c0fae8f63fc94c76832a7cd`.
> Parent verification, independent arithmetic audit and final binding checks
> exit 0. No product implementation or test suite run; no network/text reads.
> No commit or push. Existing selector-review edits preserved.
> [Normative protocol](reports/ESSENTIAL-WEB-EVIDENCE-V2.1-PROTOCOL.md),
> [review / exact next Muse prompt](reports/ESSENTIAL-WEB-EVIDENCE-V2.1-REVIEW.md#5-exact-muse-implementation-prompt),
> [freeze](evidence/ESSENTIAL-WEB-EVIDENCE-V2.1/freeze.json),
> [commands](evidence/ESSENTIAL-WEB-EVIDENCE-V2.1/COMMANDS.md).
> Next: give Muse the exact implementation prompt in review section 5;
> stop after offline implementation and focused verification. Both M and T
> acquisition still require separate authorization and resolved execution gates.

> **ESSENTIAL-WEB EVIDENCE V2 COST MAP (2026-09-27, offline): COLLECT
> ALL FILES' BOUNDS, STAY INCOMPLETE.** Diagnostic collection mode:
> footer-only T cost planning now continues past per-file future-
> acquisition infeasibility (§11 verdict stands — no tightening, caps
> frozen) to map every safely reachable frozen file, while integrity/
> safety failures still stop immediately with stopped_early. Extended
> incomplete receipt (schema v2): per-group units, exact formulas,
> per-limit file/arm fits, aggregate sums/maxima, refusal list; final
> status INCOMPLETE, never a success artifact. Chunk offsets plumbed
> from footers; page indexes explicitly unexposed. 109 tests pass;
> ruff/mypy clean; 67 sampling/bounds regressions green. Frozen
> identities unchanged. Details in
> [ESSENTIAL-WEB-EVIDENCE-V2-IMPLEMENTATION](reports/ESSENTIAL-WEB-EVIDENCE-V2-IMPLEMENTATION.md)
> §12. Prior user/Astra docs preserved.

> **ESSENTIAL-WEB EVIDENCE V2 ARM-T REFUSAL (2026-09-27, offline): WHOLE-
> CHUNK BOUND STANDS; NO SAFE TIGHTENING; REFUSAL KEPT.** Authorized run
> did Arm M 8/8, then refused T costs on 2014-15: transfer upper
> 26,707,302 = whole-chunk compressed text in wanted groups + 4 MiB
> framing per chunk (both caps exceeded). pyarrow exposes zero page
> APIs; the stack's minimum read is whole column chunks, so a
> page-subset bound would be unenforceable (second decoder stack
> forbidden). Dictionaries requisite per chunk; selected span covers
> ~93% of the window anyway. No text/X: reads. Observability only:
> failed-unit numbers now preserved in future incomplete receipts;
> user's receipt untouched. 103 tests pass; ruff/mypy clean. Freeze,
> policy, inventory, caps, 118-manifest unchanged. Details in
> [ESSENTIAL-WEB-EVIDENCE-V2-IMPLEMENTATION](reports/ESSENTIAL-WEB-EVIDENCE-V2-IMPLEMENTATION.md)
> §11. Prior user/Astra docs preserved.

> **ESSENTIAL-WEB EVIDENCE V2 REDIRECT FIX (2026-09-27, offline): COUNTING
> BUG, NOT A FOURTH REDIRECT; READY TO RETRY.** The user's "hop 4" stop
> (file 2, zero retries, 4 arm requests, file 1 complete) was the
> arm-scoped hop counter with no per-request reset — file 1's ranges
> consumed hops 1–3, file 2's first redirect became hop 4. A genuine
> 3-transition chain can never produce it. Fixed: chains reset per
> issued request (retries re-resolve independently), cap stays 3, plus
> credential-free redirect-chain diagnostics in refusals and per-redirect
> ledger charging. 101 focused tests pass (13 new mocked-HTTP chain
> tests); ruff/mypy clean. Freeze/policy/118-manifest unchanged; user's
> existing receipt untouched. Next: retry the exact footer command in
> [ESSENTIAL-WEB-EVIDENCE-V2-IMPLEMENTATION](reports/ESSENTIAL-WEB-EVIDENCE-V2-IMPLEMENTATION.md)
> §7. Prior user/Astra docs preserved.

> **ESSENTIAL-WEB EVIDENCE V2 FOOTER TRANSPORT (2026-09-27, offline):
> PREMATURE VERDICT CORRECTED; LIVE PLANNING IMPLEMENTED, NOT RUN.**
> The earlier READY claim was wrong: `plan-footers --no-dry-run
> --authorize-network` failed with an unimplemented-transport stub.
> This patch implements the real footer/cost planning on the existing
> XLM stack (range/transport/footer/window machinery + frozen
> ArmLedger/provenance): footer-only ranges, retry/redirect/host
> enforcement, drift refusal, deterministic 512-row windows with
> cross-check, future-plan feasibility, no-rerank INCOMPLETE receipts,
> atomic outputs, plus metadata-only Arm-T cost planning (never page
> data/text). Agent stayed offline (mock transports, sockets blocked).
> 88 focused tests pass; ruff/mypy clean. Frozen digests unchanged;
> 118-locator manifest digest recomputed
> `975ba3dee4…78474`. Prior user/Astra docs preserved. Details in
> [ESSENTIAL-WEB-EVIDENCE-V2-IMPLEMENTATION](reports/ESSENTIAL-WEB-EVIDENCE-V2-IMPLEMENTATION.md)
> §9. Next: separately authorized operator footer/cost run; live
> transport itself is implemented but unexecuted.

> **ESSENTIAL-WEB EVIDENCE V2 IMPLEMENTATION (2026-09-27, offline):
> MECHANISMS DONE; 118/118 LOCATORS SEALED; READY FOR BOUNDED
> FOOTER/COST PLANNING.** Implemented `essential-web-evidence-v2.0`
> mechanisms only (`src/xlm/data/evidence_v2/`, `scripts/evidence_v2.py`):
> canonical/digest layer, offline inventory/ranking, window-v2 identities,
> shared Arm-M/Arm-T budget ledgers, sparse exact-locator retention,
> HMAC blinding + rubric validation, provenance receipts. Freeze digest
> `fe215779…`, policy `f4357f61…`, 23,200-path inventory, and all eight
> file identities recomputed offline (match; no STOP). Real metadata-only
> Arm-T selection sealed to
> `G:\Project\xlm-evidence-v2\essential-web\text_selection_manifest.json`
> (census exactly 29/25, zero shortfalls/conflicts, total 118, digest
> `975ba3de…`). 54 focused synthetic tests pass; ruff/mypy clean. No
> network/fetch/text/X: writes/training/admission/push. Next: separately
> authorized footer/cost planning per
> [ESSENTIAL-WEB-EVIDENCE-V2-IMPLEMENTATION](reports/ESSENTIAL-WEB-EVIDENCE-V2-IMPLEMENTATION.md)
> §7. Prior user/Astra working-tree docs preserved.

> **ESSENTIAL-WEB EVIDENCE V2 PROTOCOL (2026-09-27, offline): READY TO
> IMPLEMENT EVIDENCE V2; NO ACQUISITION AUTHORIZED.** Frozen protocol
> `essential-web-evidence-v2.0`, metadata seed 20260927, review-order seed
> 20260928. All nine corrected v2 artifacts read and integrity checked;
> the request's 63-character manifest file hash is corrected explicitly
> to its measured 64-character value. Eight complete path inventories
> reconstruct to the original discovery hashes, allowing eight NEW file
> choices to be frozen before footer inspection (23,200 eligible paths).
> Unchanged selector digest/window-v2; eight separate 512-row plans;
> deterministic <=118 development-text locators, physical-work caps,
> 18-dimension blinded rubric, two independent reviewers and adjudication,
> qualitative decisions and exact Muse handoff specified. Full text field
> `text` established from offline pinned-source repository contracts.
> New windows/locator manifests/physical feasibility/acquisition/labels
> NOT RUN; no network, text inspection, X: writes, code/selector changes,
> training, admission or push. Tests NOT RUN (documentation only).
> Current protocol and next prompt:
> [ESSENTIAL-WEB-EVIDENCE-V2-PROTOCOL](reports/ESSENTIAL-WEB-EVIDENCE-V2-PROTOCOL.md).
> Earlier statuses below retain their historical context; corrected v2
> artifacts are now present and verified. No A/B/C/D selector is frozen.

> **ESSENTIAL-WEB SWEEP REPORTING FIX (2026-09-27, offline): FOUR
> DIAGNOSTIC DEFECTS REPAIRED; ASSIGNMENTS UNCHANGED; READY TO
> REGENERATE.** Patched exactly Astra's four real-run reporting
> findings in `scripts/essential_web_selector_sweep.py` (+ focused
> tests): primary-path FDC level labels with corrected level-1/finer
> positions and no non-primary fallback; bounded publisher metadata
> word-count distributions (never tokens; absent cells unavailable);
> within-component genre-share denominator made explicit per
> policy/tier/component; end-of-run RSS renamed
> `peak_rss_bytes` -> `endpoint_rss_bytes`. Policy YAML byte-identical,
> digest `f4357f61…07`, gates/predicates/precedence/assignments
> untouched; `TOOL_VERSION`/`REPORT_SCHEMA_VERSION` 2 (v1 outputs not
> semantically identical). 60 focused synthetic tests pass; ruff/mypy
> clean. Astra's review docs preserved. Old v1 sweep outputs
> (`G:\Project\xlm-selector-sweeps\essential-web-v1`) NOT touched; user
> decides on regeneration into a fresh dir per
> [ESSENTIAL-WEB-SELECTOR-SWEEP](reports/ESSENTIAL-WEB-SELECTOR-SWEEP.md)
> §9. No network/fetch/X: access/real execution/training/push.

> **REAL ESSENTIAL-WEB SWEEP REVIEW (2026-09-27, offline): NEED BOTH
> METADATA + TEXT EVIDENCE; NO SELECTOR FREEZE.** Audited all nine user
> outputs; eight non-manifest artifacts reproduce byte-for-byte from the
> existing read-only metadata bundle. Combined/eight-part hashes verify;
> 32,768 independent row-policy assignments match the frozen evaluator.
> Conservation, precedence, subsets and B/D identities reconcile; the
> malformed FDC row is quarantined. B-normal science is only 29 rows
> (0-10 per crawl); D adds 544 assignments whose Irrelevant Content quality
> is unreviewed. Found diagnostic defects: wrong FDC label paths/indexes,
> false word-count unavailability, genre-spread denominator, and endpoint
> RSS mislabeled as peak. No code repaired or policy changed. Proposed,
> not authorized: eight new metadata windows plus a 118-row blinded text
> review. No network/fetch/text inspection/X: writes/training/admission.
> See [scientific review](reports/ESSENTIAL-WEB-SELECTOR-SWEEP-REVIEW.md)
> for exact evidence, limits, ledger, and next planning prompt.

> **ESSENTIAL-WEB SELECTOR SWEEP (2026-09-27, offline): EXPERIMENT
> IMPLEMENTED, READY FOR USER OFFLINE SWEEP; NO FINAL APPROVAL.** Bounded
> streaming sweep for frozen policies A-D x normal/strict over the
> authorized 4096-row bundle (input-hash bound, fail-closed, no text).
> Frozen spec `recipes/selectors/essential_web_selector_sweep_v1.yaml`
> with canonical digest; 9 deterministic artifacts; sensitivity and
> temporal-flag diagnostics included. 39 focused synthetic tests pass;
> ruff/mypy clean. Real X: sweep NOT RUN (user-owned). No production
> selectors, adapter changes, admission, quota changes, or push. Next: the
> user command in
> [ESSENTIAL-WEB-SELECTOR-SWEEP](reports/ESSENTIAL-WEB-SELECTOR-SWEEP.md).

> **ESSENTIAL-WEB POLICY REVIEW (2026-09-27, offline): READY FOR OFFLINE
> SELECTOR SWEEP; NO FINAL POLICY APPROVAL.** Direct audit of the four
> authorized real reconnaissance artifacts reconciled aggregate counts,
> cross-tab margins, and bundle/execution receipt digests. One reported
> FDC code, `320.973/0207`, fails strict decimal syntax: path presence is
> not full code validity. Four documented candidate policies specify
> gates, science/practical/prose predicates, deterministic precedence,
> normal/stricter tiers, and per-crawl diagnostics. Recommend testing B
> (genre-based practical/prose; 5xx plus explicitly selected 61x science)
> against A/C/D. No selector executed or implemented; no text, network,
> fetch, X: writes, tokenizer, training, admission, quota change, or push.
> Tests NOT RUN (policy/documentation only). Next: the bounded offline
> sweep prompt in [ESSENTIAL-WEB-SELECTOR-POLICY-REVIEW](reports/ESSENTIAL-WEB-SELECTOR-POLICY-REVIEW.md).

> **MIX01-ULTRAX-6B (2026-09-26): IMPLEMENTED + FOCUSED-VERIFIED; READY FOR
> OPERATOR ULTRAX PROBE.** Branch `data/mix01-ultrax-6b` from `d7942ba`.
> Nemotron-CC organic 20% replaced by `ultrax_ultrafineweb` 20% (11 Mix-01
> components; preset `mix01` identity `d670bd3a…` → `6e1e4e9a…`, registry
> `mix01_views_v1` → `v2`); `nemotron_wiki_rewrite` kept. New
> `UltraXUltraFineWebAdapter` (cleaned-only, remove_all counted drop, no raw
> fallback), authored fixtures/certification, 6B/6.6B/32M quota file, bounded
> operator probe/freeze/order scripts, exact PowerShell runbook. Focused
> offline tests + ruff + scoped mypy green (no network, no real data). NO
> live acquisition/preparation/tokenizer/pilot/training by the agent. See
> [MIX01-ULTRAX-6B](reports/MIX01-ULTRAX-6B.md). Next: USER runs the tiny
> UltraX probe, freezes the exact SHA, and performs first-pass acquisition.
>
> **TRACK A REPAIR (2026-09-27): probe/cert path fixed, READY FOR OPERATOR
> RE-PROBE.** Real 30-row probe done (`openbmb/UltraX-Preview` @
> `a88527…d8df`, `apache-2.0`, frozen in views + catalog, formatting
> restored). Fixed: datasets-5.0.1 `trust_remote_code` removal (permanent),
> exact-SHA forwarding to all datasets calls, `hf-stream://` virtual cert
> locator, no-BOM writer, declared/streaming/observed schema fallback with
> honest evidence source, strengthened live-cert contract (UID rule
> unchanged: non-empty string). Operator evidence: clean 30-row sample
> present, receipt predates the schema-evidence format — re-probe required.
>
> **TRACK A CLOSED (2026-09-27).** Operator re-probe + live certification:
> ALL 5 PASSED, 0 failed, 0 skipped (`openbmb/UltraX-Preview` @
> `a88527…d8df`, `UltraX-Ultra-FineWeb`, schema_match true via
> `streaming_features`, five string fields, `apache-2.0`, 30 rows).
> Runbook probe `--output` fixed to
> `adapter-cert-ultrax01/probe_receipt.json` (no manual copy). Frozen SHA
> and adapter semantics untouched. See
> [MIX01-ULTRAX-6B §16](reports/MIX01-ULTRAX-6B.md).
>
> **6B PRODUCTION ACQUISITION PLAN (2026-09-27, planning only).** 6B exact /
> 6.6B headroom quotas stand; all 11 sources UNADMITTED (no recorded
> decisions; common_pile license blocked). Calibration tranche (bounded,
> pilot-capped, production code path), deterministic inventory + headroom
> helper (`scripts/mix01_inventory.py`, tested), per-source plan-hash flow
> with STOPs, ≤2-way fetch parallelism. Plan at
> [MIX01-6B-ACQUISITION-PLAN](reports/MIX01-6B-ACQUISITION-PLAN.md). Verdict:
> READY FOR CALIBRATION ACQUISITION (bulk NOT authorized). No live run.
>
> **REMAINING-UNIT CALIBRATION RUNBOOK (2026-09-27, planning only).**
> UltraX calibration confirmed the workflow (1000 records, 994 accepted,
> 6 remove_all, yield ≈ 1.156). Exact per-unit runbook for the other 10
> units (proven file paths, views, revisions, adapter specs) via new
> fail-closed driver `scripts/operator_calibrate_remaining.ps1`
> (`X:\XLM` data roots, pilot caps, network toggled per call, `--no-sync`);
> `record` (+`--combine-sources` for IFM) appends measurements to
> `calibration.json` without hand-editing. Common Pile excluded pending
> license. See
> [MIX01-CALIBRATION-REMAINING](reports/MIX01-CALIBRATION-REMAINING.md).
> Verdict: READY FOR REMAINING CALIBRATION. No live run.
>
> **CALIBRATION DRIVER MACHINE-OUTPUT REPAIR (2026-09-27, offline).**
> SimpleStories stopped in Record after fetch/verify/adapt succeeded:
> `canonical-bytes` printed `1269186\r\n` and the driver's `'^\d+$'` never
> matches before `\r` (proven under PS 5.1). The driver no longer parses
> any subprocess stdout: Record runs `mix01_inventory.py measure`
> (artifact-derived, cross-checked JSON `<unit>\record_inputs.json`) then
> `record --measurement`; a bound COMPLETED fetch is adopted without a
> fetch call; empty-argv and partial-output adoption gaps and an adapt
> temp-file leak fixed. Real SimpleStories state audited read-only (all six
> adoption checks REUSE; measured 1000/1000/0, 2,424,514 B → 1,269,186 B).
> Next: `powershell -NoProfile -ExecutionPolicy Bypass -File
> G:\Project\xlm-data-ultrax\scripts\operator_calibrate_remaining.ps1
> -Unit simple_stories -Stage All`. Verdict: READY FOR SIMPLESTORIES FINAL
> CANARY. See [MIX01-CALIBRATION-REMAINING §7b](reports/MIX01-CALIBRATION-REMAINING.md).
>
> **SYNTH LARGE-ROW-GROUP CALIBRATION (2026-09-27, offline): IMPLEMENTED +
> FOCUSED-VERIFIED; READY FOR SYNTH CALIBRATION RETRY.**
>
> - **Root cause.** Whole-group sampling compared each SYNTH shard's
>   single ~155k-row row group (`total_byte_size` ≈ 795 MB, all 14
>   columns) against the 32 MiB parser bound. It was not a driver bug.
> - **Fix.** New versioned `parquet_window` v1 (`sample-blocks --mode
>   window`, `plan --parquet-window-*`). It takes one deterministic
>   SHA-256 window per file inside one row group, streams only the
>   projected chunks in ≤4 MiB ranges, and stops decoding at the window
>   stop.
> - **Honest accounting.** Every decoded row is charged as scanned. The
>   real retry is predicted to take `synth_001` rows [8737,9737) and
>   decode 9,984 of them. Physical work is gated at the pilot ceilings for
>   window plans.
> - **Unchanged.** Legacy paths and hashes (pinned).
> - **Calibration yield.** It is recorded with the raw transfer disclosed
>   and sized by the retained-row share.
> - **Evidence.**
>   - New/related focused tests: 297 + 4 serial pass, 0 skipped. Full
>     suite NOT RUN.
>   - 14/14 mutations killed.
>   - Real-scale authored benchmark (797 MB group): ≤59.2 MB transferred,
>     ≤23 requests, ≤139 MB peak RSS.
> - **Next.** `powershell -NoProfile -ExecutionPolicy Bypass -File
>   G:\Project\xlm-data-ultrax\scripts\operator_calibrate_remaining.ps1
>   -Unit synth_en_explanations -Stage SampleBlocks`, review
>   `rows.evidence.json`, then `-Stage All`.
>
> See [SYNTH-LARGE-ROWGROUP-CALIBRATION](reports/SYNTH-LARGE-ROWGROUP-CALIBRATION.md).
>
> **PARQUET WINDOW-v2 NESTED STRUCTS (2026-09-27, offline): IMPLEMENTED +
> FOCUSED-VERIFIED; READY FOR WIKI WINDOW FOOTER RETRY.**
>
> - **Problem.** Wiki-Rewrite's projection includes the struct
>   `metadata`, which the flat window-v1 correctly refused.
> - **Fix.** A new shared resolver maps logical Arrow fields to every
>   physical Parquet leaf behind them. Structs are supported; lists and
>   maps are refused with a reason.
> - **Identity.** window-v2 is bound into the behavioral hash, the
>   evidence, the SHA-256 start and adoption. v1 is frozen, and the real
>   SYNTH plan hash `dd248136…8c33` is pinned.
> - **Driver.** Only the Wiki unit uses v2.
> - **Evidence.** 443 + 3 serial focused tests pass; 8/8 mutations
>   killed. Predicted real window: rows [7519,8519), 8,704 rows scanned.
> - **Next.** `powershell -NoProfile -ExecutionPolicy Bypass -File
>   G:\Project\xlm-data-ultrax\scripts\operator_calibrate_remaining.ps1
>   -Unit nemotron_wiki_rewrite -Stage SampleBlocks`
>
> See [PARQUET-WINDOW-NESTED-STRUCT](reports/PARQUET-WINDOW-NESTED-STRUCT.md).
>
> **ESSENTIAL-WEB SELECTOR RECON (2026-09-27): READY FOR ESSENTIAL LIVE
> RECON.** The adapter stamps the science/practical/prose component from
> configuration and never selects rows, so the three calibration units
> would share identical raw rows. Do not run them yet.
> `scripts/essential_web_recon.py` adds:
> - `discover`: bounded HF tree listing, 8 temporal crawl strata, a seeded
>   choice of one file per crawl, and a digest-frozen manifest;
> - `analyze`: offline distributions, cross-tabs, accounting, percentiles
>   and candidate overlap, with nothing approved.
>
> Acquisition reuses window-v2 sample-blocks → plan → fetch → verify with
> a metadata-only projection (`text` excluded), capped at 4,096 records.
> 14 new tests pass along with 22 nested-window regressions; ruff and
> mypy are clean. Selectors, the adapter, weights and quotas are
> unchanged. See [ESSENTIAL-WEB-SELECTOR-RECON](reports/ESSENTIAL-WEB-SELECTOR-RECON.md) §10.
>
> **ESSENTIAL RECON EXECUTION-V2 (2026-09-27): READY FOR ESSENTIAL 8-UNIT
> RECON FETCH.** Discovery succeeded (digest `c8d448fa…30a7`). The first
> design refused at the footer stage:
> - The 5-field projection needed about 104 requests per file, above the
>   pilot limit of 100 per plan.
> - The 2-field projection (`eai_taxonomy`, `quality_signals`) passed on
>   all 8 files at 85 requests each.
>
> New subcommands:
> - `execution` writes a separate execution-v2 manifest (`5065bcf6…`).
>   `discovery.json` is kept unchanged. The 8 footers are adopted only when
>   they match exactly, and the result is one independent 512-row plan per
>   file.
> - `combine` validates every unit and concatenates them, byte-preserved,
>   into 4,096 rows plus a receipt.
>
> The analyzer no longer needs id, pid or metadata. The generic probe hit
> its body limit; it is documented and unchanged, and plans bind the
> revision through a recon-only catalog. 59 focused tests pass; ruff and
> mypy are clean. See [ESSENTIAL-WEB-SELECTOR-RECON](reports/ESSENTIAL-WEB-SELECTOR-RECON.md) §10–§12.
> Surgical idempotent freeze; focused offline tests + ruff + scoped mypy
> green. No push. See [MIX01-ULTRAX-6B §15](reports/MIX01-ULTRAX-6B.md).
>
> Latest local review: **ASTRA INDEPENDENT REVIEW + LOCAL CUDA CERTIFICATION**
> (2026-09-26), **SAFE AFTER SPECIFIC FIXES**. Pilot-critical repairs implemented
> and verified; receipt-disabled pilot software is safe to integrate. Formal
> microbatch receipt remains BLOCKED on documented adversarial gaps. See the
> appended closeout below and [readiness report](reports/P35-PILOT-READINESS.md).
> No real pilot, data preparation, study, push or merge occurred.

> **P35 PILOT-READINESS HARDENING (2026-09-26; not Milestone 6): IMPLEMENTED;
> pure decisions VERIFIED; NEEDS LOCAL CERTIFICATION.** Branch
> `research/p35-pilot-readiness` from certified M5 `8ccb4bc` (tag
> `p35-m5-certified`). Linux cloud, Python 3.12.3, authorized base+dev scratch
> venv (no torch, no extras, no data). No acquisition, Mix-01, order manifests,
> training, pilot, B8/B16/B32 run, margins, push to certified branches or merge.
>
> - **Audit:** traced matrix. Pre-change, a clean failure could be permanently
>   lost for `search@0` (training starts anyway), `quick@1M`/`@4M` (no retained
>   state) and `search@32M` (no reachable retry: the CLI is capped at 200k, the
>   queue never re-runs, there is no benchmark checkpoint scorer). No path
>   stopped an evaluation-incomplete pilot.
> - **`xlm-evaluation-recoverability-v1`**, in the plan hash and the
>   checkpoint-plan identity:
>   - **C = 0 barrier:** bounded M2 retries at C = 0; no update 1 until
>     quick/full/search@0 are COMPLETE; fail-stop at C = 0.
>   - **`evaluation_recovery` checkpoints** at 1M (C = 1,048,576) and 4M
>     (C = 4,063,232) by first crossing, with no split update; pinned only while
>     unresolved.
>   - **Endpoint:** live retry, then `EVALUATION_INCOMPLETE` fail-stop.
>   - The §W draft declares the policy and stays DRAFT.
> - **Plan:** a recoverability table with a digest; the planner blocks any
>   required event with route `NONE`. **Capacity:** worst case 9P (was 7P).
>   **Input cap:** unchanged 2 GiB, plus a stat-only per-source diagnostic
>   before hashing.
> - **Fairness:** `global_update_payload_digest_v1` and the committed chain
>   `xlm-update-payload-chain-v1`, opt-in via `training.update_payload_receipt`.
>   - B8/B16/B32 and the P34 producer give an identical digest; each of 12
>     fields, a moved mask bit and the sequence order change it.
>   - Only committed updates are recorded; resume is exact.
>   - Tracks v3 and evidence v3; `microbatch_grouping_v2` requires the chain,
>     while v1 keeps its historical meaning. The B8/B16/B32 draft uses v2 and
>     its margins stay unset.
> - **Evidence:** 95 new pure tests pass. Focused regression (35 files): no
>   HEAD-only failure versus the base export. **Mutations: 10/10 killed.**
>   ruff clean. mypy: 5 torch-absent notices, identical on base. Synthetic flow
>   0.8 s.
> - **NOT RUN (torch):** `tests/test_p35_readiness_runtime.py` (17 nodes),
>   updated M3 pilot assertions, and the frozen/queue/toy flows that now inherit
>   the policy.
>
> See [P35 pilot readiness](reports/P35-PILOT-READINESS.md). Next: local CUDA
> certification (§16 commands), then bind real Mix-01 artifacts and read the
> `recoverability` and `input_bytes` review sections. DO NOT START M6 here.

> **P35 Milestone 5 LOCAL WINDOWS/CUDA CERTIFICATION CLOSEOUT (2026-09-26): M5
> CERTIFIED — SAFE TO INTEGRATE** as an opt-in. Worktree
> `G:\Project\xlm-p35-m5-local`, branch `review/p35-m5-local`, starting HEAD
> `dc41a32`, clean, exact certified parent `221c4e0` (locally certified M4).
> Windows build 26200, Python 3.12.13, torch 2.14.0+cu126, CUDA 12.6, RTX 4090
> (driver 596.49), NumPy 2.5.3, lm-eval 0.4.13, no XLM CUDA workload, sequential
> GPU work. No network/install/sync/live-data/Mix-01/real-orders/training/
> pilot/campaign/push/merge; no new study; no product-source change; dependency
> files unchanged.
>
> - **Primary M5 runtime:** 86/86 (51.4 s), all torch nodes executed. First run
>   81/86: one parametrized schema test used a POSIX-only `/abs/...` fixture
>   (not absolute on Windows); test-only fixture fix, product untouched.
> - **Resume:** same-order A→A exact (weights/cursor/trace/order receipt);
>   cross-order A→B, A→native, native→A (stream) and A→B-fork refused BEFORE any
>   model/optimizer/schedule/scaler/data restore. Forks cannot change order by
>   design — new experiment required.
> - **Producer:** real spawned `process_depth1` serves the frozen order
>   (input_ids+labels equal, states equal, 5 updates). **Pilot:** real authored
>   order bindings VERIFIED for A and B (identities differ, membership kept);
>   foreign/tampered/missing/unpinned/outside-root BLOCK; 32M draft stays
>   NONEXECUTABLE.
> - **M4 regression:** 226/227 (sole failure = proven pre-existing CRLF
>   artifact; P17 blobs untouched, 47/47 comparison green). **M1–M3 group:**
>   205/205 closing (one stale exact-dict assertion gained the designed
>   `document_order: VERIFIED` entry — test-only). **Serial:** 2 passed, 24
>   deselected. **Workflow:** 3/3.
> - **Real M5 evidence:** bounded frozen ordered toy (2048 targets, SUCCEEDED)
>   extracts as v2 with real order `a955f9a4…`/membership `a18d59ab…`/M5 policy,
>   all cross-checks OK. **Pre-M5:** exact sentinels preserved, v1 verifiable.
>   **Bundle/allocation:** 26/26 (C0/C2/C4→A, C1/C3→B; all-A, foreign
>   membership, relabelled-seed, retry-inflation all refused).
> - **Interleaving:** quotas/budget/boundaries identical across orders; traces
>   differ by design (same membership ≠ same token sequence).
> - **Static:** ruff check/format clean (28 files); scoped mypy 16 files clean
>   (cloud torch-absent ignores reassessed as justified with torch present);
>   dependency diff empty.
> - **Risks A–D** reviewed, no redesign: recomputation cost (future pilot
>   concern), 20M-doc bound (no problem), mixture-substitution ineligibility (by
>   design), v2 versioning (explicit everywhere).
>
> See [P35-M5 §19](reports/P35-M5.md#19-local-windowscuda-certification-closeout-2026-09-26-rtx-4090).
> Next: integrate M5; pin real tokenizer/shards/exposure; build two independent
> real order manifests; choose margins from policy/variance; obtain GPU
> authorization. DO NOT START another milestone here.

> **P35 Milestone 5 (2026-09-26): IMPLEMENTED; VERIFIED at the data/identity/
> evidence level; NEEDS LOCAL CERTIFICATION.** Branch
> `research/p35-m5-document-order` from certified M4 `221c4e0`. Linux cloud
> container, Python 3.12.3, base+dev `uv sync --locked` scratch venv (authorized;
> no torch, no extras, no data). No acquisition, data preparation, real order
> manifest, training, pilot, campaign or merge.
>
> - **Membership** `xlm-canonical-train-membership-v1`: order-independent,
>   train-only, content/lineage/source/split-bound identity computed from the
>   token shards.
> - **Order manifests** `m5-independent-document-order-v1`: seeded SHA-256-keyed
>   within-source permutation, header-digest id, re-derivation on verify;
>   non-independent pairs (same sequence, same seed, other membership) refused.
> - **Stream/producer**: whole-document ordered index over the unchanged payload
>   (no token duplication; ≈ id length + 6 bytes/document/order); quotas and
>   scheduler unchanged; the real P34 producer child serves the same order.
> - **Identity/resume**: pinned `data.document_order` in the envelope and data
>   identity; `document_order` in committed data state; cross-order resume and
>   forks refused before any state restore.
> - **M4**: evidence v2 reads the real order/membership ids from receipts
>   (pre-M5 keeps the sentinel); tracks v2 add MUST_MATCH `canonical_membership_id`;
>   the robustness slot verifies the declaration (C0/C2/C4 → A, C1/C3 → B) and run
>   binding. Statistics, margins and promotion rules unchanged. New order-evidence
>   bundle with unique initialization counts per order.
> - **Pilot**: the 32M draft requires a pinned order (fixed order, not an
>   independent replicate); planning blocks unbound/unpinned/foreign orders.
> - **Tests**: see the report's §13 table — 10/10 mutants killed; ruff/format
>   clean; mypy clean apart from 2 pre-existing torch-absent lines identical on
>   `221c4e0`. One serial error (`test_reports` torch check) is identical on
>   `221c4e0`.
> - **NOT RUN (torch/CUDA)**: Trainer resume A→A, `load_checkpoint` A→B refusal,
>   trainer-path producer, full pilot planning, updated M3 pilot assertions,
>   legacy torch regressions, frozen workflow.
>
> See [P35-M5](reports/P35-M5.md) and [science-v1](../science-v1.md). Next: run
> the P35-M5 §15 local certification commands, then the §18 prompt.

> **P35 Milestone 4 LOCAL CUDA CERTIFICATION CLOSEOUT (2026-09-26): M4 CERTIFIED
> — SAFE TO INTEGRATE** as an opt-in. Worktree `G:\Project\xlm-p35-m4-local`,
> branch `review/p35-m4-local`, starting HEAD `9e3239f`, clean, certified parent
> `9f57869` verified as ancestor. Windows 10 Pro (26200), Python 3.12.13, torch
> 2.14.0+cu126, RTX 4090 (driver 596.49), NumPy 2.5.3, lm-eval 0.4.13, no XLM
> CUDA process, sequential GPU work. No network/install/sync/live-data/pilot/
> campaign/push/merge; no M5; no product-code change; dependency files unchanged.
>
> - **M4 focused suite:** 179 passed, 1 failed (of 180). The failure is the P17
>   golden-SHA test on CRLF checkout bytes; all five git blobs match the golden
>   and all five working-tree files equal certified M3 — a proven pre-existing
>   Windows environment artifact, not an M4 defect.
> - **Legacy `test_comparison.py`:** 47 passed (cloud NOT RUN closed).
> - **Real frozen evidence:** no retained M3 toy bytes remained, so one bounded
>   authored-toy frozen flow was created with existing utilities (direct 4096 +
>   frozen 2048 targets, toy BPE/model, generated text only). Endpoint
>   `run_b66d98f3ccb11927_ckpt-t2048-a001` extracts via the real ArtifactStore:
>   `xlm-science-run-evidence-v1`, digest `ebc95509…`, science-v1, 6768 params
>   (real torch path), seeds 101/10001/20260918, order sentinel
>   `shard_native_no_order_manifest`, batch 256/microbatch 2, LR endpoint,
>   RTX 4090/torch 2.14.0+cu126, all evaluations COMPLETE. Cross-checks all OK;
>   repeat extraction identical; tampered copy refused. Single-real-run screen is
>   INCOMPLETE with no CI/winner.
> - **Legacy:** P17 blobs unchanged (5/5), 4 legacy-evidence nodes passed,
>   `test_comparison.py` 47 passed. **Spot checks:** 9/9 (A–E) passed.
> - **Static:** ruff check clean, format 17/17 clean, scoped mypy 10 files clean
>   (unscoped 7 errors are pre-existing in untouched files); dependency diff empty.
> - **Qualifications:** microbatch per-update mask/position digest absent (harden
>   before B8/B16/B32); throughput/VRAM operator-declared; driver/cuDNN not
>   separately recorded (same-machine pairing unblocked); margins intentionally
>   null (must be chosen before confirmation, never invented).
>
> See [P35-M4 §25](reports/P35-M4.md#25-local-cuda-certification-closeout-2026-09-26-windowsrtx-4090).
> Next: integrate M4, then the M5 prompt in P35-M4 §24. M5 may begin after
> integration. DO NOT START M5 here.

> **P35 Milestone 4 (2026-09-26): IMPLEMENTED and VERIFIED within the
> authored/synthetic scope; SAFE TO INTEGRATE as an opt-in.** Branch
> `research/p35-m4-comparisons` from certified M3 `9f57869`. No training,
> evaluation campaign, pilot, acquisition or data download.
>
> - **Environment.** The only network use was the user-authorized
>   `uv sync --locked` of base + dev wheels into a scratch venv (no torch or
>   extras). This is a Linux cloud container on Python 3.12.3, which differs
>   from the primary 3.12.13; it makes no CUDA claims.
> - **Manifest.** Versioned `xlm-science-comparison-v1` with 36 required keys,
>   field-level problems and `identity_digest` hash. Margins, family size and
>   roster are frozen inputs.
> - **Tracks and eligibility.** Three-way field classification; certified
>   `microbatch_grouping_v1` and `data_mixture_v1`. Any undeclared or unknown
>   difference is INELIGIBLE with a field diff and no effect estimate.
>   Cross-tokenizer CE is refused.
> - **Evidence.** Extracted from verified frozen science-v1 checkpoints (M1–M3
>   receipts, M2 `first_complete_attempt_v1`). Pairing uses explicit replicate
>   identity; same-seed reruns are repeats, not replicates.
> - **Statistics.** Paired Student-t (stdlib, verified against closed forms and
>   tables), Bonferroni from the manifest, oriented improvement plus raw deltas.
>   One pair gives no CI.
> - **Decisions.** `xlm-p35-decision-v1`: win/loss/ambiguous/NI against frozen
>   margins; nonsignificant ≠ NI.
> - **Promotion.** `xlm-p35-promotion-v1`: 5/3/3 fresh pairs; first 3 of 5 →
>   PROVISIONAL. Order robustness stays BLOCKED until M5 evidence exists.
> - **Reports and CLI.** §U JSON/Markdown/CSV; `xlm experiment compare` /
>   `xlm experiment report`.
> - **Tests.** 180 M4 tests pass (serial and `-n 4`).
>   - The 11 high-risk mutants are all KILLED.
>   - ruff, format and source mypy are clean.
>   - Related legacy tests: 31 passed. The one error is pre-existing (torch
>     missing), identical on `9f57869`.
> - **NOT RUN (torch absent):** `tests/test_comparison.py` (legacy P17, code
>   byte-identical) and evidence extraction from a real trainer checkpoint.
>
> See [P35-M4](reports/P35-M4.md), [science-v1](../science-v1.md#scientific-comparisons-p35-milestone-4)
> and [mutations.json](evidence/P35-M4/mutations.json). Next: integrate M4, run
> the two NOT RUN items on the CUDA environment, then the M5 prompt in P35-M4 §24.

> **P35 Milestone 3 final certification closeout (2026-09-26): M3 CERTIFIED —
> SAFE TO INTEGRATE** (opt-in, authored/synthetic scope). **M4 may begin.** The
> real 32M pilot stays **BLOCKED** on 17 real inputs and the user's
> authorization. No product code changed; two test-only commits (`e2541ec`,
> `bbffc16`) strengthen evidence.
>
> - **M1/M2 frozen workflow:** 3/3 passed on `f548d60` (556.8 s): direct,
>   queue and resume; LR, RNG and runtime identity; M2 cadence.
> - **Queue/frozen:** 35 selected, 10 passed, 5 failed, 20 errors.
>   - All 25 non-green nodes raise `installed torch accelerator does not match
>     selected extras` (CPU-extra frozen identity).
>   - They are identical on a clean certified-M2 `a3e5546` export: a
>     pre-existing environment limit, not M3.
>   - The CUDA-relevant M3 queue paths are exercised by the workflow, the
>     wall-allowance tests and toy phase B.
> - **Nine adversarial mutations, all KILLED** in isolated exports, each on its
>   intended assertion, after a green control:
>   1. resume cadence drift;
>   2. retirement before replacement authority;
>   3. deletion of the last good state;
>   4. ignored evaluation dependency;
>   5. rescore with same count/step but different weights;
>   6. fallback to the latest checkpoint;
>   7. peak-disk underestimate;
>   8. wall allowance reset on resume;
>   9. blocked or unauthorized pilot made EXECUTABLE.
>
>   One test weakness was found and fixed: the retention-order observer's
>   exception was swallowed by `apply_retention`.
> - **Final M3 group** (cadence, retention, rescore, pilot, wall): 73/73
>   passed. Ruff and format are clean; the mypy profile is unchanged.
> - **Toy flow** was not rerun. The post-run edit is proven behavior-neutral
>   by AST comparison.
> - **Open for the pilot:**
>   - The 2 GiB aggregate shard-input cap is a POTENTIAL BLOCKER pending real
>     sizes; M3 is not blocked by it.
>   - `quick_lm@1M/4M` have no exact checkpoint, which is a **PILOT POLICY
>     DECISION** for the user.
>   - Search-benchmark events are not checkpoint-rescorable and affect
>     completeness only on failure.
>
> See [P35-M3 §17](reports/P35-M3.md#17-final-certification-closeout-2026-09-26)
> and [certification.json](evidence/P35-M3/certification.json). Next: integrate
> M3, then the M4 prompt in P35-M3 §16.

> **P35 Milestone 3 (2026-09-26), superseded by the closeout above: IMPLEMENTED/VERIFIED within the
> authored/synthetic scope; safe to integrate as an opt-in. The real 32M pilot
> is BLOCKED** until the user supplies real artifacts. NOT RUN: the M1/M2 frozen
> workflow and queue/frozen serial regressions (session ended early); run them before integrating. Branch
> `research/p35-m3-pilot-retention` from certified M2 `a3e5546`.
>
> - **Checkpoint cadence.** Absolute committed-target checkpoint events share
>   the M2 first-crossing planner, with a fixed boundary order: crossing →
>   one checkpoint → evaluations. Pilot 0/8M/16M/32M land at C = 0,
>   8,060,928, 16,056,320 and 32,000,000 (488 full updates + 18,432). Planned
>   and actual counts are recorded. Resume never drifts or republishes, and
>   failed publications are recorded.
> - **Retention.** Bounded latest-two-recovery + pinned retention over
>   verified records. Milestones, references and evaluation-needed states are
>   protected, and a failed publication retires nothing.
> - **Rescoring.** FAILED M2 events are rescored only from the retained
>   checkpoint of exactly their state (digest-verified weights and evaluator,
>   device and runtime identity) as a new immutable attempt.
> - **Pilot planning.** Non-executable `draft_science_v1_pilot_32m`,
>   `xlm experiment plan --bindings` and the new `xlm experiment validate`
>   (DRAFT/BLOCKED/RESOLVED/EXECUTABLE), with measured peak-disk planning.
> - **Wall allowance.** The persisted 3,600 s total allowance gives a resumed
>   attempt only the remainder; expiry → INCOMPLETE.
> - **Toy flow.** One bounded authored toy flow on CUDA: 174.7 s, 13 MB,
>   ≤147,456 targets. It covered a failed eval → exact rescore, a killed
>   runner → resume with 500.7 s of 540 s, and plan → EXECUTABLE → queue.
>
> See [P35-M3](reports/P35-M3.md) and [science-v1](../science-v1.md).

> **P35 Milestone 2 certification closeout (2026-09-26): M2 CERTIFIED — SAFE TO
> INTEGRATE** (opt-in, authored/synthetic scope). No product change; one new test
> (`92e5eba`). A new CUDA frozen workflow runs an authored cadence through CLI
> train, experiment plan/submit + queue run, and CLI resume. Results:
>
> - Thresholds 8/24 fire at the natural boundaries C=16/32, and the endpoint at 33.
> - Update sizes stay 16/16/1 and the M1 LR receipts are unchanged.
> - Checkpoints owe events between crossing and scoring; an owed event is scored
>   after a fresh-store resume, and nothing is rescored at an at-budget resume.
> - Direct and queued receipts are identical, and the training state is bitwise
>   equal to a cadence-free run.
> - One envelope; legacy is refused.
> - Mutants M-A (crossing after checkpoint) and M-C (queue drops the controller)
>   are both killed.
>
> Missing regressions, `-n 0`, 717.5 s: 52 passed, 3 failed, 0 skipped:
>
> - M1 workflow: 2/2.
> - Harness adapter: 14/14.
> - Declared inputs: 36/39.
>
> The 3 failures are `xlm evaluate` CLI nodes that need the CPU-only frozen
> extra. They fail identically on a clean `517a9b8` export and pass with
> `--device cuda`: a pre-existing environment issue, not M2. The 34 cited
> guard/attempt/firewall nodes were rerun green. Real-inventory scorer
> throughput is unmeasured, and FAILED-event rescoring belongs to M3. See
> [P35-M2 §8](reports/P35-M2.md#8-certification-closeout-2026-09-26).
> Next: **P35 Milestone 3 only** (prompt in §8.5).

> **P35 Milestone 2 (2026-09-25): IMPLEMENTED/VERIFIED within the authored/synthetic
> scope; opt-in, safe to integrate once the listed NOT RUN regressions pass.**
> *(Superseded by the certification closeout above.)*
> Branch `research/p35-m2-eval-cadence` from `517a9b8`. Science-v1
> `evaluation.science` adds an absolute committed-target cadence (§K tables
> verbatim) with `quick_lm`, `full_lm`, `search_benchmark` and `endpoint_confirmation`
> events. First crossing is verified: the 1M event fires at C=1,048,576 with
> unchanged 65,536-target updates. Crossings are recorded before the periodic
> checkpoint; every attempt is an immutable artifact; the canonical receipt is the
> first complete attempt; failed, partial or missing events make the run
> evaluation-incomplete and never produce a score. Scoring uses a digest-verified
> replica under a state guard; runs with and without evaluation are bitwise
> identical on CPU and CUDA. Adds text-only CE, UTF-8 BPB, equal-domain
> aggregation, a pinned LM inventory format, and a BLiMP tier-partition firewall
> (a new gap closure). Tests: 89 new focused tests, 363-passed CPU regression
> (1 pre-existing cp932 failure, reproduced on `517a9b8`), 16 CUDA passed, 12/12
> mutants killed, and Ruff/format/mypy clean. NOT RUN: the science workflow test,
> harness regressions, a frozen CLI/queue cadence run, and 50M scorer/guard cost.
> See [P35-M2](reports/P35-M2.md). Next: run the NOT RUN regressions, then M3.

> **P35 Milestone 1 (2026-09-25): COMPLETE within the authored/synthetic scope;
> safe to integrate as an opt-in.** Branch `research/p35-m1-lr-rng-identity`
> from `991dd39`, three product/test commits plus docs. `xlm-science-v1`
> (explicit `science_version`, `lr_policy: target_endpoint_before_update_v1`,
> `training_seed`, `runtime` block) is opt-in. Schema-v1 configs and checkpoints
> resolve to the legacy policy. The historical config digest and a
> checkpoint produced by the unmodified code continue bit-identically.
> `optimizer.step` observed **0.0000065536 then 0.0000131072** at the 50M
> reference (legacy: 0.001 then 0.0000065536) on CPU and CUDA. Actual partial N
> is honored, and zero-valid work never steps. LR receipts are published only
> after the data commit, and every failure stage, including the CUDA barrier,
> leaves none. Training RNG is reseeded after construction and resume restores
> without reseeding. Attention/TF32/BF16-reduction policy is scoped per update
> and bound into the envelope. Direct, queue and resume share one identity on
> CUDA (strict mode also bitwise). Final gate from a clean export of `9f4c392`:
> **221 CPU + 17 CUDA + 5 serial passed**, plus 2 capability skips. There is
> one pre-existing cp932 README failure, and the CPU frozen legs are BLOCKED;
> both reproduce on the baseline. Ruff, format and mypy pass on 16 files.
> Strict-mode full-model cost, science-path throughput and CPU frozen
> workflows are NOT RUN. No real data, pilot, campaign, network, install, push
> or merge. See [P35-M1](reports/P35-M1.md) and [science-v1](../science-v1.md).
> Next prompt: **Implement P35 handoff Milestone 2 only (target-threshold
> evaluation cadence and state-preserving scoring) on top of M1; keep legacy
> behavior, use focused offline tests, and do not launch real-data training.**

> **P35 scientific contract (2026-09-25): IMPLEMENTED design / VERIFIED bounded
> audit**, on `research/p35-scientific-contract`, engineering base `febbf8b`.
> [Scientific contract](reports/P35-SCIENTIFIC-CONTRACT.md) covers A–Z;
> [Opus handoff](handoffs/P35-OPUS-HANDOFF.md) specifies five sequential steps.
> Decisions: controlled statistical reproducibility; paired B8/B16/B32 quality–cost
> study; versioned positive first-update warmup; one-pair screens, five-pair robust
> 50M confirmation, three-pair larger-scale confirmation; held-out CE primary;
> user-run 32M pilot. Code audit found the legacy first-step LR spike/next-LR log,
> within-shard order not shuffled by data_seed, unwired evaluation cadence, and
> old comparison statistics insufficient for the new seed-level decision rule.
> No product defaults/code/recipes/lockfile changed. Synthetic 14.891-s attention
> probe: strict deterministic efficient SDPA available at all three sizes, sampled
> forwards identical, all strict repeated backwards identical; busy-desktop
> operator timing is not a full-model speed certificate. Parameter counts verified;
> seven existing schedule tests passed. See [P35 record](reports/P35.md) for exact
> checks and limits. Real inputs/pilot and research studies NOT RUN; execution
> awaits handoff implementation and actual artifact/hash-bound user authorization.
> No network, install, acquisition, campaign, push or merge. Next prompt:
> **Implement P35 handoff milestone 1 only; preserve legacy LR/RNG behavior,
> run focused offline tests, and do not launch real-data training.**

> **P34 final adversarial review (2026-09-25): SAFE TO INTEGRATE the repaired
> series on `review/p34-astra-final`, within the frozen synthetic scope.** Starting
> `745e66e` was clean but had blockers: synchronous CUDA bypassed the completion
> barrier, the bound checkpoint manager could publish in-doubt state, pipe waits
> escaped deadlines, and four consumed-envelope claims were unchecked. Repairs
> retain the Opus exact trace fold and compact spawned producer; add bounded
> lifetime reading, frame admission and failure cleanup; and protect all CUDA
> optimizer/checkpoint boundaries. Final adversarial selection: 53 passed;
> earlier related CPU selection: 103 passed (overlapping counts). CUDA: three
> barrier/exactness nodes and public direct/queue/resume workflow passed. Ruff,
> format and mypy pass on all 10 changed Python files. Production-config B8:
> **45,679 targets/s**, resident **46,654**, ratio **97.91%**; synchronous **36,247**.
> Content verification remains on (3.06 ms/update). Eight lifecycle iterations
> leave zero children/threads and constant handle count. CPU-only frozen release
> legs remain NOT RUN because the installed CUDA wheel fails their unchanged
> environment check. No network, installations, live data, campaign, push or
> merge. See [P34 final review](reports/P34.md), which supersedes the earlier
> candidate verdict below. No further P34 engineering prompt is needed. Next:
> `git log --reverse --oneline 745e66e..HEAD` in this review worktree.

> **P34 final integration candidate (2026-09-25): COMPLETE within the synthetic
> diagnostic scope; branch `integrate/p34-final-candidate`.** Opus series
> applied cleanly onto the P33 base with proven tree identity, then five
> minimal Astra safety ports: fail-closed CUDA commit boundary with in-doubt
> optimizer state, transport domain normalization, duplex-pipe reset-deadlock
> repair (found deterministically during porting), end-state binding checks,
> and the explicit `training.producer_prefetch` run option (default off,
> frozen in the envelope, honored by direct/queue/resume). Actual-50M B8
> release-LR updates are bit exact with the barrier active; the producer sits
> at the resident ceiling (r3 100.1%, r4 101.3%; 3.6–3.9 ms consumer wait,
> zero blocked takes). CPU: prefetch 18 + training 12 + trace/config 19 +
> trainer suites 25 passed; CUDA: exactness + barrier + producer workflow
> passed. Ruff/format/mypy clean on changed modules. CPU workflow params and
> queue/frozen serial selections NOT RUN (no locked CPU env; pristine tree
> fails identically). See [P34-FINAL](reports/P34-FINAL.md). Next: review §26,
> then integrate.

> **P34 independent training-throughput challenger (Opus, 2026-09-25): COMPLETE
> within the synthetic diagnostic scope; branch `perf/opus55-p34-independent`.**
> 50M B8 end-to-end rises from 32,189 to **50,588 targets/s** (medians of three
> uncontended runs, +57.2%), 99.6% of the same-session resident ceiling (50,775),
> with every loader mode ending in one identical parameter digest. Two changes:
> an exact, always-on window-level fold of the C07 trace chain (loader
> 779 -> 390 ms/update; synchronous 39,530 targets/s, +22.8%), and an opt-in
> spawned `PrefetchingBatcher` that runs the unchanged batcher one update ahead,
> ships compact arrays and keeps committed state in the trainer (zero blocked
> updates; 3.0-3.5 ms consumer cost). Speculation is bound by start/end state
> digests and generations; producer, consumer, commit and resume failures all
> regenerate the exact sequence. Actual-50M CUDA updates are bit exact. Checkpoint
> tails (3.0 s typical, 14-25 s back-to-back) are SLC-cache exhaustion on the
> DRAM-less G: SSD, not serialization; async publication is not implemented.
> Memory-efficient SDPA backward is nondeterministic on the certified path
> (pre-existing). Gates: **201 CPU + 20 CUDA passed**, no skips; Ruff/format/mypy
> pass. Full six-leg gate, live data and research training not run. See
> [P34-OPUS](reports/P34-OPUS.md) and [P34-PREFETCH](P34-PREFETCH.md). Next:
> `git log --reverse --oneline 8fd05c11e1bdfd84e075000d59e14c315c986f36..HEAD`
> in `G:\Project\xlm-opus55-p34`, then cross-review against Astra's P34.

> **P33 bounded CUDA performance closeout (2026-09-24): COMPLETE within the
> synthetic diagnostic scope.** On RTX 4090 / torch 2.14.0+cu126, the retained
> product change consolidates CUDA gradient finite/norm transfers with exact
> clipping arithmetic and failure semantics. Three actual release-LR updates
> at the full 65,536-target budget match the frozen reference bit for bit;
> an actual-50M CUDA test also compares every clipped gradient digest and weight.
> Final focused gates: **64 CPU + 19 CUDA passed**, no skips; Ruff, format and
> mypy pass after one formatting-only correction. B8 end-to-end is 32,152 versus
> 32,044 targets/s (+0.335%, too small for a robust campaign claim); the smaller
> update diagnostic improves 3.63%, and resident B8 reaches 51,606 targets/s.
> B32 reaches 48,834 targets/s but **fails** the fixed release-LR parameter gate,
> as do B16 and native RMSNorm; these are not certified replacements. Original
> RMSNorm, microbatch/scientific defaults and durability remain unchanged.
> Bounded 150M B16 / 300M B8 smokes reach 29,826 / 18,714 targets/s. Shared
> desktop and a 14.5-GiB allocator cap qualify capacity results. Final 50M is
> **DATA-LIMITED with CPU launch overhead**; recommend P34 bounded producer-process
> overlap and checkpoint-tail design (observed 50M pause 3.6–62.4 s). No network,
> installation, live data, evaluation, research campaign, push or merge. No full
> CPU six-leg gate was run for this scoped CUDA task. See [P33](reports/P33.md)
> for negative gates, exact commands, raw evidence and planning limits. Next:
> `git log --reverse --oneline 03e6c4278a8a64301a1c416e37bda7623907e361..HEAD`
> in the P33 worktree, then review the P34 cursor/snapshot contract before coding.

> **P32 final performance closeout (2026-09-24): CORRECTNESS-COMPLETE within
> the declared offline scope; final six-leg gate PASSED.** Product `ce2bb32`
> carries closed-writer SHA/size/file-ID evidence into immediate publication,
> removing two redundant whole-payload reads while retaining full recovery
> verification, durable intent, atomic settlement, all fsyncs and journal/control
> authority checks. Exact acquisitions: 8/8; crash/restart matrices: 44/44 early,
> 44/44 mature, 20/20 selected, 4/4 parallel. Final gate ran once: **1,800 passed /
> two capability skips / zero failed**, preserving all 1,789 prior nodes and adding
> 13 publication regressions. Repeated G: median throughput improves 8.9% / 10.1% /
> 12.7% at 1/8/16 workers, but retained fsync stalls make the aggregate after-series
> slower; this is a CPU/happy-path improvement, not uniform wall-time recovery.
> The frozen 100k pipeline passes the exact comparator (126.885 s versus 109.834 s;
> unchanged downstream timing is not attributed to this change). No external
> network, installs, research training, push or merge. See
> [P32-PERF-CLOSEOUT](reports/P32-PERF-CLOSEOUT.md) for all observations, safety
> proof, limitations and final integration series. Next read-only command:
> `git show --stat ce2bb32`.

> **P32 heavy-worker crash closeout (2026-09-24): CORRECTNESS-COMPLETE
> within the declared offline scope; final gate PASSED.** Based on recovery
> candidate `fa4ff50`, repair `95ee6b3` replaces the unsafe native pytest timeout
> frame walker on pinned Windows CPython 3.12.13 with an owned, joined Python
> diagnostic thread. Native dump/OS events identify an invalid code-pointer
> read in `PyCode_Addr2Line`; a stdlib-only probe crashes 3/3 times without XLM,
> whereas control and replacement probes each pass 3/3. No acquisition,
> runtime-inventory, dependency or scientific behavior changes. Focused: 15
> passes plus 7 final diagnostic passes; original group stress 5/5; standalone
> heavy 7/7. One final six-leg gate: **1,787 passed / 2 capability skips /
> 0 failed**, 1,789 distinct selected nodes; all legs exit 0. Ruff/format/mypy
> pass. Missing compiler and symlink privilege remain uncertified. The corrected
> P32 candidate is eligible for release integration within this scope; existing
> acquisition throughput cost is unchanged. No external network, installs,
> research training, push or merge. See
> [P32-HEAVY-CRASH](reports/P32-HEAVY-CRASH.md) for native evidence and exact
> commands. Next: `git show --stat 95ee6b3`, then review the certification
> evidence before any separately authorized integration. Earlier entries are
> historical; the original failed gate remains preserved.

> **P32 recovery closeout (2026-09-24): IMPLEMENTED / VERIFIED (scoped);
> full acceptance FAILED, P32 completion and release integration BLOCKED.**
> On `fix/p32-recovery-closeout`, based on corrected candidate `26c1238`:
> durable publication intent reconciles whole and selected outputs exactly;
> owned journal/diagnostic replacements are retired under FileLock and their
> bytes are included in scratch admission, including the first journal write.
> Final crash matrices: 44/44 early, 44/44 mature, 20/20 selected, plus 4/4
> parallel selected publications; all eight successful acquisitions remain
> byte/accounting exact. Focused: 190 passed / one skip; after initialization
> correction, 85 passed / one skip. Final Tier A: 1,632 passed / two capability
> skips; core 68 passes; exclusive 8 passes. Heavy worker died with a Windows
> access violation during runtime-inventory fixture teardown after its test
> body passed; that is a gate failure, not a pass. Scale: 8 passes; optional:
> 57 passes. Six-leg total: **1,779 passed / 2 skipped / 1 failed**, covering
> 1,782 distinct nodes. All raw evidence is in
> [P32-RECOVERY](reports/P32-RECOVERY.md).
> G: 1/8/16-worker throughput changed from 143.581/227.321/226.137 to
> 91.263/125.916/29.326 MB/s; the final fsync outlier is retained. No further
> optimization, installs, external network, push or merge. Next: diagnose the
> preserved native worker crash using a focused reproducer before a new gate;
> keep the recovery fixes and explicit throughput tradeoff. Earlier entries
> below are historical.

> **Independent Opus 5.5 review (2026-09-24): CORRECTED / PARTIALLY VERIFIED;
> P32 completion BLOCKED.** Reviewed all six requested commits against green
> `9765a00`. Four separate corrections remove stale journal caching, reserve
> skipped-row scans before parsing, guard locator splicing against mutation,
> and establish durable empty-prefix ownership. Corrected product `f981464`:
> 227 focused passes; Tier A 1,600 passes / one missing-compiler skip; 36 final
> independent regressions pass. Whole-file crash matrices preserve accounting
> and prefixes but recover only 40/44 cases; selected matrix recovers 16/20.
> Publication deaths fail closed with valid files still present. Orphan atomic
> journal files also leave a gap in the complete scratch-cap claim. G: maximum
> observed durable acquisition 234.212 MB/s on larger files; selected Parquet
> 8.42–9.06 MB/s; frozen 100k pipeline 109.834 s with exact comparator success.
> Use corrected leases as the P32 implementation candidate, not as completed
> recovery work. Full six-leg acceptance NOT RUN because review did not pass.
> No network beyond authored localhost, installs, push or merge. See
> [OPUS55-REVIEW](reports/OPUS55-REVIEW.md) for verdicts, commits, evidence and
> the narrow next prompt. Earlier status entries below are historical.

> **Artifact↔ledger crash reconciliation: IMPLEMENTED / VERIFIED (bounded
> offline)** on `fix/artifact-ledger-reconciliation`. Strengthened
> `rebuild_from_filesystem` (full-verifier authority, idempotent no-op
> duplicates, incomplete/conflict reporting, count/byte/deadline bounds
> with honest truncation, phase timing) plus new `audit_ledger_references`
> (ok/healed/unusable verdicts, `unverifiable` marking, rows never
> deleted) with atomic check-and-write concurrency. Recovery caller:
> `artifact rebuild-ledger` with bounds + audit flags (no automatic
> full-store scans). Crash matrix A–H, concurrent convergence, real
> checkpoint crash-window recovery, and 1/100/1000 timings (0.03 s /
> 3.31 s / 27.84 s) all green: 18 new + 115 related tests passed;
> ruff/mypy clean. Sync substrate (durable publish + reconcile +
> idempotent record + restart discovery) now satisfies async-checkpoint
> prerequisites. See [reports/ARTIFACT-LEDGER-RECONCILE.md](reports/ARTIFACT-LEDGER-RECONCILE.md).
> Overall production acceptance remains BLOCKED; main is unchanged.

> **Checkpoint/artifact durability (synchronous baseline): IMPLEMENTED /
> VERIFIED (bounded offline)** on `fix/checkpoint-durability`. `publish_artifact`
> now orders flush+fsync per payload, staging/subdir syncs, durable manifest,
> durable `_COMPLETED` last, rename, parent sync — zero fsyncs before.
> Durability facts ride identity-neutral in `cosmetic_metadata`; Windows
> directory sync honestly reported unsupported (file fsync + NTFS rename +
> fail-closed verification instead). 15 new failure-injection/observability
> tests + 101 related tests passed; ruff/mypy clean. Cost ~5–6 ms per fsync
> on this box. See [reports/CHECKPOINT-DURABILITY.md](reports/CHECKPOINT-DURABILITY.md).
> Overall production acceptance remains BLOCKED; main is unchanged.

> **Performance integration 2026-09-23 (P28 + Astra P29/P29B/P29C):
> INTEGRATED / GATE-TESTED (bounded offline)** on
> `integrate/performance-20260923`. One P28 + 13 Astra + 7 P29C commits
> cherry-picked in order with zero conflicts; reconciliation adds only
> lint normalization, `--workers 6` + opt-in `--dedup-workers` harness
> support, and docs. Combined pipeline (adapt → shard → clean → P28
> dedup → split → tokenize → pack → loader): 10k in 28.7–53.6 s,
> 100k in 147.6–258.7 s depending on workers, every stage digest
> worker-invariant at both scales. Full gate: 1626 passed / 19 failed /
> 1 skipped (fast) + 9 passed (serial); 16 failures are xdist isolation
> flakes, 3 are pre-existing Astra-branch failures (proven identical
> without this integration). NumPy absent from the locked runtime (exact
> Python fallback; §12 operator action documented). Semantic lane stays
> opt-in and off by default. See [reports/PINTEGRATION.md](reports/PINTEGRATION.md).
> Overall production acceptance remains BLOCKED; main is unchanged.

> **P30B serial/final throughput (2026-09-24): IMPLEMENTED / VERIFIED (scoped);
> final acceptance BLOCKED.** On `perf/test-suite-throughput`, starting
> `343537d8dfd6b30bc6f724436feb1f7b14993163`: all 1,673 prior nodes retained,
> eight added core nodes, 1,681 collected. Original B: 66 pass / two fail,
> 2,454.37 s. Core now passes 69 nodes in 419.93 s (grouped + exclusive);
> installed optional passes all 57 in 190.44 s versus 523.03 s (63.6% less time).
> Test-only immutable queue identity reuse preserves fresh worker/CLI inventories.
> Audited four-worker domain grouping retains private mutable state; three risky
> repetitions passed 11/11 at 298.66 / 296.51 / 297.39 s. All 68 original B bodies
> and assertions are unchanged. Core + heavy still cover every original B node.
> Final six legs: 1,655 pass / four fail / one compiler skip, no missing required
> nodes, 1,172.83 s (19m32.83s). B totals 801.41 s, but the queue campaign stopped
> early on a Windows checkpoint rename PermissionError: **no validated full-final
> speedup or passing heavy gate is claimed**. A also encountered a Windows journal
> replacement PermissionError; two original token-preparation failures remain.
> B peak sampled RSS 1.035 -> 2.871 GiB; sampled CPU lower bound
> 2,322.266 -> 3,099.984 s (includes eight new nodes; failed-work caveat applies).
> A remains twelve workers; scale passes eight / 112.26 s. No product/durability
> code, original durability tests, dependency graph or P28/P29 algorithms changed.
> No network, installation, research campaign, push or merge. CUDA/live, hosted
> grouping and performance matrices NOT RUN. See [P30B report](reports/P30B.md),
> [evidence](evidence/P30B/README.md) and [direct commands](../TESTING.md).
> Next: investigate the journal and checkpoint rename failures (coordinate the
> checkpoint case with Muse), repair token preparation reuse/resume, run exact
> failing nodes with `-n 0`, then the six final legs on the integrated tree.

> **P30 test-suite iteration (2026-09-23): IMPLEMENTED / VERIFIED (scoped);
> final acceptance BLOCKED.** On `perf/test-suite-throughput`, starting
> `6bdca915005e2863e1c80b962dff6a053ab5b680`: all 1,667 original tests retained,
> six additions, 1,673-node tier ledger. Fast A (1,519 nodes) takes 81.71 s with
> 12 workers: 1,518 pass, one existing CPU compiler skip. Measured 4/8/12/16:
> 113.75 / 102.88 / 81.71 / 95.38 s. Immutable private-copy fixtures and lazy
> optional imports reduce repeated work; real worker verification remains intact.
> Original scale: eight passes / 111.62 s; installed optional: 57 / 535.97 s.
> Serial: 65 passes, three failures / 2,253.40 s; its Windows crash-termination
> race was repaired and the exact case passed in 89.83 s. Two unchanged token
> preparation/reuse publication failures remain. Latest required-node evidence:
> 1,649 pass, two fail, one skip; no missing required nodes. The four measured
> correctness legs total 49m42.70s: **no full-acceptance speedup is established**.
> Four bounded performance matrices passed / 139.93 s; full performance,
> CUDA/live/environment installation and hosted CI NOT RUN. No production code or
> dependency graph changed; no research campaign, network, installs, push or merge.
> See [P30 report](reports/P30.md), [node ledger](evidence/P30/test-ledger.tsv),
> [raw evidence](evidence/P30/README.md), and [direct commands](../TESTING.md).
> Next: repair the two `tests/test_prepare.py` regressions listed in the report,
> run those exact nodes with `-n 0`, then the four-leg final gate on the merged tree.

> **P29C exact cleaning (2026-09-23): IMPLEMENTED / VERIFIED (bounded offline)**
> on `perf/astra-cleaning-v2`, parent `5a43a81`. Authored one-worker cleaner:
> 10k 7.965 -> 5.918 s; 100k 70.399 -> 53.241 s (24.4% less time).
> Full frozen pipeline, unchanged worker settings: 160.558 -> 144.719 s.
> Explicit eight-worker cleaning: 111.688 s versus historical P29B 158.430 s;
> combines exact loop improvements with existing parallelism. Cleaner w6/w8:
> 21.271/20.256 s at 1,542.5/1,982.1 MiB tree RSS. Static default retained;
> bounded dynamic dispatch is opt-in for uneven work. Decisions, metrics,
> ordered payloads and token artifacts match; real code fingerprints change.
> Focused tests, scoped Ruff/mypy and full artifact comparisons passed.
> Token preparation is now dominant; educational-keyword scanning remains a
> cleaner hotspot hidden by the existing incomplete length timer. No P28 edits,
> network, installs, research training, push or merge. Full acceptance, 250k,
> live compatibility and CUDA NOT RUN; production acceptance remains BLOCKED.
> See [reports/P29C.md](reports/P29C.md), [measured tables](evidence/P29C/RESULTS.md)
> and [operator commands](../PERFORMANCE.md).

> **P29B token-path performance (2026-09-23): IMPLEMENTED / VERIFIED (bounded offline)**
> on `perf/astra-global-throughput`, parent `44c19b8`. Frozen 32,768-entry authored
> tokenizer; matched 100k pipeline 192.142 -> 158.430 s (17.5% less time), token
> stage 83.222 -> 51.950 s including assembly. Peak process-tree RSS rises from
> 369 MiB to 2.09 GiB with eight workers. Matched 10k is flat (20.250 -> 20.328 s).
> Adds bounded shard workers/native batches, exact packing/loader improvements,
> optional verified mmap cache, streaming fit preparation and fsynced manifest-last
> token publication. Focused offline tests and scoped Ruff/mypy passed. Cleaning
> is now the largest measured stage. No P28 edits, network, installs or training.
> Full acceptance, live compatibility and CUDA measurements NOT RUN; Windows
> directory/checkpoint power-loss durability remains unproven. Production
> acceptance remains BLOCKED. See [reports/P29B.md](reports/P29B.md), its exactness
> evidence and [operator commands](../PERFORMANCE.md).

> **P29 global throughput (2026-09-23): IMPLEMENTED / VERIFIED (bounded offline)**
> on `perf/astra-global-throughput`, parent `2a82dfd`. Exact ASCII counting,
> BPE byte-length reuse / IDs-only encoding, bounded token writes, and streaming
> split metadata. Authored 100k local pipeline: 228.795 → 173.354 s (24.2% less
> time); split RSS 718 → 294 MiB. Canonical/token artifacts and scientific metrics
> compare exactly. 229 focused regressions + 2 serial checks passed; scoped
> Ruff/mypy passed. No P28 implementation edits, network, installs or training.
> Production speedup, GPU and official evaluation NOT RUN; full suite NOT RUN.
> Audit also records the pre-existing checkpoint power-loss durability gap.
> See [reports/P29.md](reports/P29.md) and [performance commands](../PERFORMANCE.md).
> Production acceptance remains BLOCKED.

> **P28 exact/lexical dedup + FAISS semantic lane (2026-09-23): IMPLEMENTED /
> VERIFIED (bounded offline)** on `perf/p28-dedup-faiss` (parent `2a82dfd`).
> Exact uint64 vectorized MinHash (5×, fuzz-proven, NumPy-gated), shared
> match view, binary index framing, sharded spawn-worker engine with ordered
> assembly (5× at 8 workers: 10k 66→326/s locked, 313→962/s with kernel;
> 100k 65→338/s locked, 462→1185/s kernel), deterministic sharded survivor
> output, full telemetry. Semantic lane OFF by default: embedding artifact
> contract, test providers, python/numpy/faiss-cpu/faiss-gpu backends with
> capability detection (FAISS absent here — RTX 4090 present but
> undrivable), candidate sidecar + threshold analysis; 10k/100k×384 and
> 1M×384 vector benchmarks measured. 31 new tests + 223 focused
> regressions passed; ruff/mypy clean. Full suite NOT RUN (final gate
> only). See [reports/P28.md](reports/P28.md). Overall production
> acceptance remains BLOCKED; main is unchanged.

> **P27B cleaning throughput (2026-09-23): IMPLEMENTED / VERIFIED (bounded
> offline)** on `perf/p27b-cleaning-throughput` (parent `34122ad`).
> Exact micro-optimizations (shared lazy text features, fused char scans,
> compile-once matchers, PII hint gate, copy shortcut, buffered quarantine:
> mixed-5k wall 6.42 s → 5.14 s) plus a deterministic sharded cleaning
> engine (verified manifest/dir/file/Parquet input, legacy or sharded
> accepted/quarantine output, process-level shard parallelism, ordered
> assembly, quarantine caps in global order). Scaling: 10k mixed
> 964 → 3232 docs/s w1→w8; 100k mixed (243 MiB) 956 → 4235 docs/s;
> 128 MiB 62 → 216 docs/s with RSS growth ≤ 5.3 MiB. 28 new tests +
> 145 focused regressions passed; ruff/mypy clean. Dedup precompute
> correctly omitted (`clean_hash` is already free). Full suite NOT RUN
> (final gate only). See [reports/P27B.md](reports/P27B.md). Overall
> production acceptance remains BLOCKED; main is unchanged.

> **P27A production sharding (2026-09-23): IMPLEMENTED / VERIFIED (bounded
> offline)** on `perf/p27a-production-sharding` (parent `0675331`).
> Deterministic size-sharded datasets (`dataset-manifest.json` v1), streaming
> 64 MiB input budget, atomic per-shard publish with manifest-last ordering,
> manifest-dir adapt input, `--output-shard-bytes` adapt path, strict
> verifier, and `adopt` verified-prefix primitive (resume wiring deferred).
> 28 focused + 3 serial + 1 slow test passed; ruff/mypy clean on scoped
> sources. 128 MiB synthetic benchmark: 2 shards, 1.5 s, 2753 rec/s,
> RSS +2.2 MiB. Full suite NOT RUN (final gate only). See
> [reports/P27A.md](reports/P27A.md). Overall production acceptance remains
> BLOCKED; main is unchanged.

> **D02 + D04/D05 + D07 + D08 integrated (2026-09-20): IMPLEMENTED / INTEGRATED
> (bounded Windows CPU offline)** on `integrate/d07-d08`. Merges `fix/d08`
> (D02 closeout + D08 analysis repair) and `fix/d07` (single-copy export);
> shared xdist tooling reconciled identical. Full offline audit on the final
> tree: parallel (77 files, `-n 8`) 1029 passed / 2 pre-existing failures,
> serial (7 files) all green, serial-marked 3/3, base-only green, quality and
> demo green, authored chain green. Remaining: D06 inventory timing, recipe and
> report-plan failures, receipt-verification gap, deferred tokenizer work,
> operator-run validation. See `reports/P23-D02-D04-D05.md`, `reports/P23-D07.md`,
> `reports/P23-D08.md` and the consolidated integration report below.
> Overall production acceptance remains BLOCKED; main is unchanged.

> D01 remediation is **IMPLEMENTED / VERIFIED (bounded offline)** under Stage 1 approval.
> See [reports/P23-D01.md](reports/P23-D01.md) and [REMEDIATION.md](REMEDIATION.md).
> D06 is **IMPLEMENTED / VERIFIED (bounded Windows CPU offline)**.
> D03 core is **IMPLEMENTED / VERIFIED (bounded Windows CPU)** for the baseline
> pilot handoff; see [P23-D03](reports/P23-D03.md). Cross-tokenizer comparison
> remains OPEN / DEFERRED. D02, D04/D05, D07 and D08 are implemented and
> integrated (see above); other later repairs require separate approval.
> Overall production acceptance remains BLOCKED.

> **P23 audit (2026-09-19): production acceptance is BLOCKED.** Historical milestone statuses describe their original evidence. The updated requirement ledger and [FINAL_ACCEPTANCE.md](FINAL_ACCEPTANCE.md) supersede broader readiness claims. D01/A03 and D06/A28 are verified within their offline scopes; A14/A27/A31 and the other unresolved remediation gates remain open. Live corpus, official evaluation and real protected deployment are not verified. P23 audit delivery is complete, not a production or research-results certification.

## Environment and repository

- Base git revision: `20673e3c6a2f80ebaaf4e89705d2951397fff3d0`; P23 audited the existing working tree including untracked P01–P22 implementation. The command evidence records actual source hashes; no new commit was made.
- Python / uv / extra: Python 3.12.13 / uv 0.11.6 / `cpu` and `cuda` extras (mutually exclusive); optional `eval` extra (`lm-eval==0.4.13`, P15) installable alongside either
- OS / GPU / driver: Windows 11 (P23 build 26200) / NVIDIA GeForce RTX 4090 24GB / Driver 596.49 (CUDA 13.2 compatible)
- Active contracts version: XLM v1 (September 2026)

## Historical Catalog & Document Discrepancies

- The previous FineWeb-first data catalog is formally superseded. FineWeb and FineWeb-Edu are not defaults or fallbacks. Direct-source denylists apply, and no implicit fallback or renormalization is permitted.
- The active catalog is defined in `manifests/datasets.catalog.yaml` and `DATA_CATALOG.md` (20 candidate sources, requiring local admission audits).

## Milestones

| Milestone | Name | Status |
|---|---|---|
| P00 | [Repository foundation and uv environment](prompts/00_foundation.md) | VERIFIED |
| P01 | [Contracts, config and artifacts](prompts/01_contracts_config_artifacts.md) | VERIFIED |
| P02 | [Local data and tokenizers](prompts/02_local_data_tokenizers.md) | VERIFIED |
| P03 | [Reference models](prompts/03_reference_models.md) | VERIFIED |
| P04 | [Losses, optimizers and schedules](prompts/04_losses_optimizers_schedules.md) | VERIFIED |
| P05 | [Training loop, checkpoint and resume](prompts/05_training_checkpoint_resume.md) | VERIFIED |
| P06 | [Native scoring and generation](prompts/06_native_scoring_and_generation.md) | VERIFIED |
| P07 | [Source discovery and admission](prompts/07_source_discovery_admission.md) | VERIFIED |
| P08 | [Bounded acquisition](prompts/08_bounded_acquisition.md) | VERIFIED |
| P09 | [Cleaning and quality pipeline](prompts/09_cleaning_quality_pipeline.md) | VERIFIED |
| P10 | [Deduplication, splits and exclusions](prompts/10_dedup_splits_exclusions.md) | VERIFIED |
| P11 | [Pool freeze and tokenizer regime](prompts/11_pool_freeze_tokenizer_regime.md) | VERIFIED |
| P12 | [Token shards and mixture packing](prompts/12_token_shards_mixture_packing.md) | VERIFIED |
| P13 | [Real dataset views and mix01](prompts/13_real_dataset_views_and_mix01.md) | VERIFIED |
| P14 | [CUDA profile and performance](prompts/14_cuda_profile_and_performance.md) | VERIFIED |
| P15 | [Official evaluation harness adapter](prompts/15_official_evaluation_harness.md) | VERIFIED |
| P16 | [Experiment plans and queue](prompts/16_experiment_plans_and_queue.md) | VERIFIED |
| P17 | [Statistics, comparisons and promotion](prompts/17_statistics_comparisons_promotion.md) | VERIFIED |
| P18 | [Research plugins and idea cards](prompts/18_research_plugins_and_idea_cards.md) | VERIFIED |
| P19 | [Reports and dashboard](prompts/19_reports_and_dashboard.md) | VERIFIED |
| P20 | [Export, generation and portability](prompts/20_export_generation_portability.md) | VERIFIED |
| P21 | [Isolation, security and release](prompts/21_isolation_security_release.md) | VERIFIED |
| P22 | [Campaign bootstrap and runbooks](prompts/22_campaign_bootstrap_and_runbooks.md) | VERIFIED |
| P23 | [Independent final acceptance](prompts/23_independent_final_acceptance.md) | VERIFIED audit; production BLOCKED |
| P24 | Acquisition-performance measurement (closeout, no optimization) | IMPLEMENTED / VERIFIED (offline fixtures); live 1/2/4/8 NOT RUN |

## Acceptance Requirements Ledger

| ID | Requirement | Prompts | Status | Evidence Reference |
|---|---|---|---|---|
| A01 | Environment and CLI | 00 | VERIFIED (scoped) | `docs/implementation/FINAL_ACCEPTANCE.md` (A01); historical: `docs/implementation/reports/P00.md` |
| A02 | Configuration correctness | 01 | VERIFIED (scoped) | `docs/implementation/FINAL_ACCEPTANCE.md` (A02); historical: `docs/implementation/reports/P01.md` |
| A03 | Immutable artifacts | 01 | VERIFIED (D01 declared-request consistency, Windows offline) | [P23-D01 code/test/result map](reports/P23-D01.md); authentic execution separately verified in [D06/A28](reports/P23-D06.md) |
| A04 | Local canonical data | 02 | VERIFIED (scoped) | `docs/implementation/FINAL_ACCEPTANCE.md` (A04); historical: `docs/implementation/reports/P02.md` |
| A05 | Reference architecture | 03 | VERIFIED (scoped) | `docs/implementation/FINAL_ACCEPTANCE.md` (A05); historical: `docs/implementation/reports/P03.md` |
| A06 | Objective normalization | 04,05 | VERIFIED (scoped) | `docs/implementation/FINAL_ACCEPTANCE.md` (A06); historical: `docs/implementation/reports/P04.md`, `docs/implementation/reports/P05.md` |
| A07 | Optimizer and scheduler | 04,05 | VERIFIED (scoped) | `docs/implementation/FINAL_ACCEPTANCE.md` (A07); historical: `docs/implementation/reports/P04.md`, `docs/implementation/reports/P05.md` |
| A08 | Exact token budget | 05,12 | VERIFIED (scoped) | `docs/implementation/FINAL_ACCEPTANCE.md` (A08); historical: `docs/implementation/reports/P05.md`, `docs/implementation/reports/P12.md` |
| A09 | Safe resume | 05,12 | VERIFIED (scoped) | `docs/implementation/FINAL_ACCEPTANCE.md` (A09); historical: `docs/implementation/reports/P05.md`, `docs/implementation/reports/P12.md` (multi-source mixture resume verified) |
| A10 | Native scoring | 06 | VERIFIED (scoped) | `docs/implementation/FINAL_ACCEPTANCE.md` (A10); historical: `docs/implementation/reports/P06.md` |
| A11 | Offline vertical slice | 00–06 | VERIFIED (scoped) | `docs/implementation/FINAL_ACCEPTANCE.md` (A11); historical: `docs/implementation/reports/P06.md` |
| A12 | Twenty-source discovery | 07 | IMPLEMENTED; live NOT RUN | `docs/implementation/FINAL_ACCEPTANCE.md` (A12); historical: `docs/implementation/reports/P07.md` |
| A13 | No FineWeb substitution | 07,13 | VERIFIED (scoped) | `docs/implementation/FINAL_ACCEPTANCE.md` (A13); historical: `docs/implementation/reports/P07.md`, `docs/implementation/reports/P13.md` (P13 adds preset/view-level denial blocking) |
| A14 | Acquisition bounds | 08 | FAILED | `docs/implementation/FINAL_ACCEPTANCE.md` (A14); historical: `docs/implementation/reports/P08.md` |
| A15 | Source live verification | 07,08,13 | BLOCKED (corpus); metadata VERIFIED | `docs/implementation/FINAL_ACCEPTANCE.md` (A15); historical: `docs/implementation/reports/P07.md`, `docs/implementation/reports/P08.md`, `docs/implementation/reports/P13.md` (P13 metadata-only discovery: real revisions/configs for 9 of 10 mix01 repos; no adapter pilot, all views NOT LIVE-VERIFIED) |
| A16 | Normalization and cleaning | 09 | VERIFIED (scoped) | `docs/implementation/FINAL_ACCEPTANCE.md` (A16); historical: `docs/implementation/reports/P09.md` |
| A17 | Deduplication and lineage | 10 | VERIFIED (scoped) | `docs/implementation/FINAL_ACCEPTANCE.md` (A17); historical: `docs/implementation/reports/P10.md` |
| A18 | Corpus split integrity | 10,11 | VERIFIED (scoped) | `docs/implementation/FINAL_ACCEPTANCE.md` (A18); historical: `docs/implementation/reports/P10.md`, `docs/implementation/reports/P11.md` (P10 split integrity; P11 leak gate at the pool boundary and frozen quick subset) |
| A19 | Benchmark exclusion | 10,21 | VERIFIED (scoped) | `docs/implementation/FINAL_ACCEPTANCE.md` (A19); historical: `docs/implementation/reports/P10.md`, `docs/implementation/reports/P21.md` (development matching plus operator receipt path: opaque decontamination receipts, aggregates-only finals, bounded quotas; production deployment NOT RUN) |
| A20 | Pool/tokenizer freeze | 11 | VERIFIED (scoped) | `docs/implementation/FINAL_ACCEPTANCE.md` (A20); historical: `docs/implementation/reports/P11.md` (demo/pilot scope; production 32,768 fit planned but NOT RUN pending admitted sources in P13) |
| A21 | Token-shard format | 12 | VERIFIED (scoped) | `docs/implementation/FINAL_ACCEPTANCE.md` (A21); historical: `docs/implementation/reports/P12.md` |
| A22 | Mixture scheduling | 12 | VERIFIED D03 token-mixture core; matched tokenizer exposure OPEN / DEFERRED | [P23-D03](reports/P23-D03.md): actual public two-source inputs, quotas, caps, isolation, traces and fresh-process continuation; authored Windows CPU scope |
| A23 | Initial mixture and treatments | 13 | VERIFIED definitions; live BLOCKED | `docs/implementation/FINAL_ACCEPTANCE.md` (A23); historical: `docs/implementation/reports/P13.md` (definition/gating scope: M0–M5 + explicit no-IFM validated offline, admitted-source blocking enforced; live mixture BLOCKED, pilot NOT RUN) |
| A24 | CUDA correctness | 14 | VERIFIED (scoped) | `docs/implementation/FINAL_ACCEPTANCE.md` (A24); historical: `docs/implementation/reports/P14.md` (RTX 4090: CPU/CUDA numerics, SDPA/eager, BF16, checkpointing, accumulation, resume, FP16 scaler; compile parity NOT RUN — no codegen toolchain) |
| A25 | CUDA performance | 14 | VERIFIED tiny; per-size NOT RUN in P23 | `docs/implementation/FINAL_ACCEPTANCE.md` (A25); historical: `docs/implementation/reports/P14.md` (measured tokens/sec, VRAM, optimizer/checkpoint sizes for 50m/150m/300m; ETA intervals with uncertainty, no peak-FLOP promises) |
| A26 | Official harness parity | 15 | BLOCKED official; fixtures VERIFIED | `docs/implementation/FINAL_ACCEPTANCE.md` (A26); historical: `docs/implementation/reports/P15.md` (pinned lm-eval 0.4.13 via public registry; native parity on fixtures; one bounded live ARC-Easy train smoke; full official suites NOT RUN) |
| A27 | Evaluation tiering | 15,21 | FAILED | `docs/implementation/FINAL_ACCEPTANCE.md` (A27); historical: `docs/implementation/reports/P15.md`, `docs/implementation/reports/P21.md` (explicit search/confirmation/final variants, split firewall, partial labeling, final request frozen; isolated execution with replay/revocation/quota guards) |
| A28 | Queue and code freeze | 16 | VERIFIED D06 bounded Windows CPU | [P23-D06](reports/P23-D06.md); captured plugin mutation and frozen queue/recovery regressions pass again in [D03](reports/P23-D03.md); external validation NOT RUN |
| A29 | Campaign cost limits | 16,22 | VERIFIED smoke; production BLOCKED | `docs/implementation/FINAL_ACCEPTANCE.md` (A29); historical: `docs/implementation/reports/P16.md`, `docs/implementation/reports/P22.md` (smoke caps, hash-bound tickets, over-budget refusal, selection gates, fork-only horizons; staged 50M→300M campaign as plans; measured cost via profiles else unmeasured) |
| A30 | Comparison fairness | 17 | VERIFIED (scoped) | `docs/implementation/FINAL_ACCEPTANCE.md` (A30); historical: `docs/implementation/reports/P17.md` (six tracks with allowed-differences schemas; ineligible pairs with readable diffs; unknown fields fail closed) |
| A31 | Statistical analysis | 17 | FAILED (D08); fixture tests pass | `docs/implementation/FINAL_ACCEPTANCE.md` (A31); historical: `docs/implementation/reports/P17.md` (paired cluster bootstrap, deterministic seeds; seed/item uncertainty separated; interpolation-only target crossing; teacher/aux costs labeled) |
| A32 | Promotion and ablations | 17 | VERIFIED (scoped) | `docs/implementation/FINAL_ACCEPTANCE.md` (A32); historical: `docs/implementation/reports/P17.md` (frozen gates; from-scratch drafts with lineage; factorial/ablation matrices as plans; nothing launched or authorized) |
| A33 | Research extension seams | 18 | VERIFIED D03 supported typed construction | [P23-D03](reports/P23-D03.md): shared direct/queue/resume construction; baseline and meaningful authored architecture/objective/optimizer/tokenizer execution; unsupported combinations refused, not exhaustive plugin support |
| A34 | Reports and dashboard | 19 | VERIFIED (scoped) | `docs/implementation/FINAL_ACCEPTANCE.md` (A34); historical: `docs/implementation/reports/P19.md` (offline JSON/CSV/Markdown/HTML over authoritative data; escaped previews; missing as n/a; loopback read-only dashboard; UI screenshots NOT RUN headless) |
| A35 | Export and generation | 20 | VERIFIED (scoped) | `docs/implementation/FINAL_ACCEPTANCE.md` (A35); historical: `docs/implementation/reports/P20.md` (native safetensors export + hash-first loading; clean-process parity; corruption/version/plugin/secret refusals; replayable sessions; HF layout mapping with runtime parity NOT RUN) |
| A36 | Protected deployment | 21 | NOT RUN (deployment) | `docs/implementation/FINAL_ACCEPTANCE.md` (A36); historical: `docs/implementation/reports/P21.md` (sealed-readiness logic and all refusal paths verified; real two-identity deployment NOT RUN by design here) |
| A37 | Release audit | 21 | VERIFIED fixtures; release BLOCKED | `docs/implementation/FINAL_ACCEPTANCE.md` (A37); historical: `docs/implementation/reports/P21.md` (eleven-check audit over synthetic releases; unknown rights stay BLOCKED; no real release certified) |
| A38 | Complete runbooks | 22 | VERIFIED Windows fixture; Linux NOT RUN | `docs/implementation/FINAL_ACCEPTANCE.md` (A38); historical: `docs/implementation/reports/P22.md` (uv-first Windows/Linux runbooks with implemented flags; disk locations, reachability cleanup, cancellation, failures, portability, unverified inventory) |
| A39 | Independent audit | 23 | VERIFIED audit delivery | `docs/implementation/FINAL_ACCEPTANCE.md` (A39); historical:  |

## Evidence per milestone

### P00 — Foundation and uv environment
- Status: VERIFIED
- Files:
  - `.python-version`
  - `pyproject.toml`
  - `uv.lock`
  - `.gitignore`
  - `AGENTS.md`
  - `.github/workflows/ci.yml`
  - `src/xlm/__init__.py`
  - `src/xlm/core/__init__.py`
  - `src/xlm/core/paths.py`
  - `src/xlm/cli/__init__.py`
  - `src/xlm/cli/main.py`
  - `src/xlm/cli/doctor.py`
  - `tests/conftest.py`
  - `tests/test_cli.py`
  - `tests/test_doctor.py`
  - `tests/test_imports.py`
  - `tests/test_paths.py`
  - `tests/test_cuda.py`
- Test commands and exit statuses:
  - `uv sync --locked`: exit code 0 (clean base installation without torch verified)
  - `uv sync --locked --extra cpu --extra cuda`: exit code 1 (conflicting extras blocked as required)
  - `uv sync --locked --extra cpu`: exit code 0 (`torch==2.14.0+cpu` installed)
  - `uv run --locked --extra cpu ruff format --check`: exit code 0 (clean formatting)
  - `uv run --locked --extra cpu ruff check`: exit code 0 (all lint checks passed)
  - `uv run --locked --extra cpu mypy src tests`: exit code 0 (strict type check passed across 12 files)
  - `uv run --locked --extra cpu pytest -v -m "not cuda and not network and not operator"`: exit code 0 (17 passed, 1 deselected)
  - `uv run --locked --extra cpu xlm --help`: exit code 0
  - `uv run --locked --extra cpu xlm --version`: exit code 0 (`xlm 0.1.0`)
  - `uv run --locked --extra cpu xlm doctor`: exit code 0 (accurate CPU report)
  - `uv run --locked --extra cpu xlm doctor --json`: exit code 0 (strictly valid JSON on stdout)
  - Subprocess execution outside repo (`C:\Users\gamma`): exit code 0
  - `uv sync --locked --extra cuda`: exit code 0 (`torch==2.14.0+cu126` installed)
  - `uv run --locked --extra cuda xlm doctor`: exit code 0 (RTX 4090 detected, sm_89, 23.99 GB VRAM)
  - `uv run --locked --extra cuda pytest -v -m "cuda"`: exit code 0 (tensor forward/backward/synchronize passed)
  - `uv sync --locked --extra cpu`: exit code 0 (restored CPU extra)
- Evidence report: `docs/implementation/reports/P00.md`

### P01 — Contracts, config and artifacts
- Status: VERIFIED
- Files:
  - `pyproject.toml`
  - `src/xlm/core/contracts.py`
  - `src/xlm/core/registry.py`
  - `src/xlm/config/__init__.py`
  - `src/xlm/config/schemas.py`
  - `src/xlm/config/composer.py`
  - `src/xlm/artifacts/__init__.py`
  - `src/xlm/artifacts/manifest.py`
  - `src/xlm/artifacts/store.py`
  - `src/xlm/artifacts/ledger.py`
  - `src/xlm/cli/config_cmd.py`
  - `src/xlm/cli/artifact_cmd.py`
  - `src/xlm/cli/main.py`
  - `tests/test_contracts.py`
  - `tests/test_registry.py`
  - `tests/test_config.py`
  - `tests/test_artifacts.py`
  - `tests/test_ledger.py`
- Test commands and exit statuses:
  - `uv sync --locked`: exit code 0 (clean base installation without torch verified)
  - `uv run --locked pytest -q -m "not cuda and not network and not operator"`: exit code 0 (50 passed in base environment without torch)
  - `uv run --locked xlm config validate recipes/models/transformer_125m.yaml`: exit code 0
  - `uv run --locked xlm config resolve recipes/models/transformer_125m.yaml`: exit code 0
  - `uv run --locked xlm artifact --help`: exit code 0
  - `uv sync --locked --extra cpu`: exit code 0 (`torch==2.14.0+cpu` installed)
  - `uv run --locked --extra cpu ruff format --check`: exit code 0 (clean formatting across 63 files)
  - `uv run --locked --extra cpu ruff check`: exit code 0 (all lint checks passed)
  - `uv run --locked --extra cpu mypy src tests`: exit code 0 (strict type check passed across 26 files)
  - `uv run --locked --extra cpu pytest -v -m "not cuda and not network and not operator"`: exit code 0 (50 passed, 1 deselected)
  - `git diff uv.lock`: exit code 0 (0 lockfile diff)
- Evidence report: `docs/implementation/reports/P01.md`

### P02 — Local data and tokenizers
- Status: VERIFIED
- Files:
  - `pyproject.toml`
  - `uv.lock`
  - `src/xlm/data/__init__.py`
  - `src/xlm/data/normalization.py`
  - `src/xlm/data/canonical_io.py`
  - `src/xlm/data/tokens.py`
  - `src/xlm/data/adapters/__init__.py`
  - `src/xlm/data/adapters/text.py`
  - `src/xlm/data/adapters/jsonl.py`
  - `src/xlm/tokenizers/__init__.py`
  - `src/xlm/tokenizers/base.py`
  - `src/xlm/tokenizers/byte.py`
  - `src/xlm/tokenizers/bpe.py`
  - `src/xlm/config/schemas.py`
  - `src/xlm/cli/data_cmd.py`
  - `src/xlm/cli/tokenizer_cmd.py`
  - `src/xlm/cli/main.py`
  - `fixtures/sources/sample_local/manifest.yaml`
  - `fixtures/sources/sample_local/documents.jsonl`
  - `fixtures/sources/sample_local/sample_text.txt`
  - `tests/test_data_adapters.py`
  - `tests/test_token_shards.py`
  - `tests/test_tokenizers.py`
- Test commands and exit statuses:
  - `uv sync --locked`: exit code 0 (clean base installation with tokenizers and pyarrow, zero torch dependency)
  - `uv run --locked pytest -q -m "not cuda and not network and not operator"`: exit code 0 (67 passed in base environment without torch)
  - `uv sync --locked --extra cpu`: exit code 0
  - `uv run --locked --extra cpu ruff format --check`: exit code 0 (80 files clean)
  - `uv run --locked --extra cpu ruff check`: exit code 0 (all lint checks passed)
  - `uv run --locked --extra cpu mypy src tests`: exit code 0 (strict type check passed across 42 files)
  - `uv run --locked --extra cpu pytest -v -m "not cuda and not network and not operator"`: exit code 0 (67 passed, 1 deselected)
  - `uv run --locked --extra cpu xlm data import-local --manifest fixtures/sources/sample_local/manifest.yaml --publish`: exit code 0 (published `canonical_sample_local`)
  - `uv run --locked --extra cpu xlm artifact inspect canonical_sample_local`: exit code 0
  - `uv run --locked --extra cpu xlm artifact verify canonical_sample_local`: exit code 0
  - `uv run --locked --extra cpu xlm tokenizer train --data-path canonical_sample_local --vocab-size 350 --publish`: exit code 0 (published `tokenizer_bpe_350`)
  - `uv run --locked --extra cpu xlm tokenizer inspect tokenizer_bpe_350`: exit code 0
  - `uv run --locked --extra cpu xlm tokenizer encode tokenizer_bpe_350 "Hello world! François 🚀 <eos>"`: exit code 0 (0 control IDs emitted, exact round-trip)
  - `uv run --locked --extra cpu xlm tokenizer verify tokenizer_bpe_350`: exit code 0 (all 9 suites passed)
  - `git diff uv.lock`: exit code 0 (lockfile clean)
- Evidence report: `docs/implementation/reports/P02.md`

### P03 — Reference models
- Status: VERIFIED
- Files:
  - `recipes/models/tiny.yaml`
  - `src/xlm/config/schemas.py`
  - `src/xlm/models/__init__.py`
  - `src/xlm/models/base.py`
  - `src/xlm/models/rmsnorm.py`
  - `src/xlm/models/rope.py`
  - `src/xlm/models/masks.py`
  - `src/xlm/models/feedforward.py`
  - `src/xlm/models/attention.py`
  - `src/xlm/models/initialization.py`
  - `src/xlm/models/parameter_counts.py`
  - `src/xlm/models/transformer.py`
  - `src/xlm/models/serialization.py`
  - `src/xlm/cli/model_cmd.py`
  - `src/xlm/cli/main.py`
  - `tests/test_attention_and_masks.py`
  - `tests/test_models.py`
- Test commands and exit statuses:
  - `uv sync --locked`: exit code 0 (clean base installation without torch verified)
  - `python -c "import sys, xlm; assert 'torch' not in sys.modules"`: exit code 0 (clean import isolation in base environment)
  - `uv run --locked xlm --help`: exit code 0
  - `uv run --locked xlm model --help`: exit code 0
  - `uv sync --locked --extra cpu`: exit code 0 (`torch==2.14.0+cpu` installed)
  - `uv run --locked --extra cpu ruff format --check`: exit code 0 (95 files clean)
  - `uv run --locked --extra cpu ruff check`: exit code 0 (all lint checks passed)
  - `uv run --locked --extra cpu mypy src tests`: exit code 0 (strict type check passed across 56 source files)
  - `uv run --locked --extra cpu pytest -v -m "not cuda and not network and not operator"`: exit code 0 (94 passed, 1 deselected)
  - `uv run --locked --extra cpu xlm model inspect tiny`: exit code 0 (2,179,392 unique deployed parameters, exact match)
  - `uv run --locked --extra cpu xlm model inspect 50m`: exit code 0 (49,883,648 unique deployed parameters, exact match)
  - `uv run --locked --extra cpu xlm model inspect 150m`: exit code 0 (149,942,016 unique deployed parameters, exact match)
  - `uv run --locked --extra cpu xlm model inspect 300m`: exit code 0 (299,418,624 unique deployed parameters, exact match)
  - `uv run --locked --extra cpu xlm model inspect tiny --json`: exit code 0 (valid JSON output)
  - `git diff uv.lock`: exit code 0 (lockfile clean)
- Evidence report: `docs/implementation/reports/P03.md`

### P04 — Losses, optimizers and schedules
- Status: VERIFIED
- Files:
  - `src/xlm/objectives/__init__.py`
  - `src/xlm/objectives/base.py`
  - `src/xlm/objectives/cross_entropy.py`
  - `src/xlm/objectives/noop.py`
  - `src/xlm/objectives/auxiliary_fixture.py`
  - `src/xlm/optimizers/__init__.py`
  - `src/xlm/optimizers/base.py`
  - `src/xlm/optimizers/adamw.py`
  - `src/xlm/optimizers/clipping.py`
  - `src/xlm/schedules/__init__.py`
  - `src/xlm/schedules/base.py`
  - `src/xlm/schedules/cosine.py`
  - `src/xlm/schedules/constant.py`
  - `src/xlm/schedules/anchors.py`
  - `src/xlm/config/schemas.py`
  - `tests/test_objectives.py`
  - `tests/test_optimizers.py`
  - `tests/test_schedules.py`
  - `tests/test_continuation.py`
- Test commands and exit statuses:
  - `uv sync --locked`: exit code 0 (clean base installation without torch verified)
  - `uv run --locked python -c "..."`: exit code 0 (verified clean base import isolation & registry discovery without torch)
  - `uv run --locked xlm --help`: exit code 0
  - `uv run --locked pytest -q ...`: exit code 0 (59 passed in clean base environment)
  - `uv sync --locked --extra cpu`: exit code 0 (`torch==2.14.0+cpu` installed)
  - `uv run --locked --extra cpu ruff format --check`: exit code 0 (114 files cleanly formatted)
  - `uv run --locked --extra cpu ruff check`: exit code 0 (all lint checks passed)
  - `uv run --locked --extra cpu mypy src tests`: exit code 0 (strict type check passed across 74 source files)
  - `uv run --locked --extra cpu pytest -v -m "not cuda and not network and not operator"`: exit code 0 (117 passed, 1 deselected in 23.13s)
  - `git diff uv.lock`: exit code 0 (lockfile clean)
- Evidence report: `docs/implementation/reports/P04.md`

### P05 — Training loop, checkpoint and resume
- Status: VERIFIED
- Files:
  - `src/xlm/training/__init__.py`
  - `src/xlm/training/data.py`
  - `src/xlm/training/checkpoint.py`
  - `src/xlm/training/trainer.py`
  - `src/xlm/cli/train_cmd.py`
  - `src/xlm/cli/demo_cmd.py`
  - `src/xlm/cli/main.py`
  - `src/xlm/artifacts/ledger.py`
  - `tests/test_trainer_data.py`
  - `tests/test_checkpoint.py`
  - `tests/test_trainer.py`
  - `tests/test_continuation_p05.py`
  - `tests/test_cli_train_demo.py`
- Test commands and exit statuses:
  - `uv sync --locked`: exit code 0 (clean base installation without torch verified)
  - `uv run --locked python -c "import torch"`: exit code 1 (`ModuleNotFoundError` confirmed in base environment)
  - `uv run --locked python -m xlm.cli.main run inspect --help`: exit code 0 (metadata-only CLI operates without torch)
  - `uv run --locked python -m xlm.cli.main train --help`: exit code 0 (CLI help operates without torch)
  - `uv run --locked python -m xlm.cli.main train dummy.json`: exit code 1 (graceful error requiring torch extra)
  - `uv run --locked pytest -v tests/test_artifacts.py tests/test_config.py tests/test_contracts.py tests/test_ledger.py tests/test_paths.py tests/test_registry.py tests/test_tokenizers.py tests/test_token_shards.py`: exit code 0 (48 core tests passed in base environment)
  - `uv sync --locked --extra cpu`: exit code 0 (`torch==2.14.0+cpu` installed)
  - `uv run --locked --extra cpu ruff format --check`: exit code 0 (126 files cleanly formatted)
  - `uv run --locked --extra cpu ruff check`: exit code 0 (all lint checks passed)
  - `uv run --locked --extra cpu mypy src tests`: exit code 0 (strict type check passed across 85 source files)
  - `uv run --locked --extra cpu pytest -v -m "not cuda and not network and not operator"`: exit code 0 (137 passed, 1 deselected)
  - `uv run --locked --extra cpu python -m xlm.cli.main demo`: exit code 0 (200-target end-to-end vertical slice demo passed)
  - `git diff uv.lock`: exit code 0 (lockfile clean)
- Evidence report: `docs/implementation/reports/P05.md`

### P06 — Native scoring and generation
- Status: VERIFIED
- Files:
  - `src/xlm/evaluation/__init__.py`
  - `src/xlm/evaluation/likelihood.py`
  - `src/xlm/evaluation/fixtures.py`
  - `src/xlm/evaluation/scorer.py`
  - `src/xlm/evaluation/diagnostics.py`
  - `src/xlm/inference/__init__.py`
  - `src/xlm/inference/generation.py`
  - `src/xlm/models/base.py`
  - `src/xlm/models/transformer.py`
  - `src/xlm/models/serialization.py`
  - `src/xlm/cli/eval_cmd.py`
  - `src/xlm/cli/generate_cmd.py`
  - `src/xlm/cli/demo_cmd.py`
  - `src/xlm/cli/main.py`
  - `tests/test_likelihood.py`
  - `tests/test_generation.py`
  - `tests/test_evaluation_fixtures.py`
  - `tests/test_cli_eval_gen.py`
- Test commands and exit statuses:
  - `uv sync --locked`: exit code 0 (clean base installation without torch verified)
  - `uv run --locked python -c "import torch"`: exit code 1 (`ModuleNotFoundError` confirmed in base environment)
  - `uv run --locked python -m xlm.cli.main --help`: exit code 0 (CLI root operates without torch)
  - `uv run --locked python -m xlm.cli.main evaluate --help`: exit code 0 (evaluate CLI help operates without torch)
  - `uv run --locked python -m xlm.cli.main generate --help`: exit code 0 (generate CLI help operates without torch)
  - `uv run --locked python -m xlm.cli.main demo --help`: exit code 0 (demo CLI help operates without torch)
  - `uv run --locked pytest -v tests/test_artifacts.py tests/test_config.py tests/test_doctor.py tests/test_imports.py tests/test_ledger.py tests/test_paths.py tests/test_registry.py tests/test_tokenizers.py`: exit code 0 (46 core tests passed in base environment)
  - `uv sync --locked --extra cpu`: exit code 0 (`torch==2.14.0+cpu` installed)
  - `uv run --locked --extra cpu ruff format --check src tests`: exit code 0 (95 files cleanly formatted)
  - `uv run --locked --extra cpu ruff check src tests`: exit code 0 (all lint checks passed)
  - `uv run --locked --extra cpu mypy src tests`: exit code 0 (strict type check passed across 98 source files)
  - `uv run --locked --extra cpu pytest -v tests/test_likelihood.py tests/test_generation.py tests/test_evaluation_fixtures.py tests/test_cli_eval_gen.py`: exit code 0 (22 dedicated P06 tests passed)
  - `uv run --locked --extra cpu pytest -v -m "not cuda and not network and not operator"`: exit code 0 (159 passed, 1 deselected in 62.25s)
  - `uv run --locked --extra cpu python -m xlm.cli.main demo`: exit code 0 (complete offline vertical slice P00-P06 demo successfully completed)
  - `git diff uv.lock`: exit code 0 (lockfile clean)
- Evidence report: `docs/implementation/reports/P06.md`

### P07 — Source discovery and admission
- Status: VERIFIED
- Files:
  - `src/xlm/data/sources/__init__.py`
  - `src/xlm/data/sources/catalog.py`
  - `src/xlm/data/sources/policy.py`
  - `src/xlm/data/sources/transport.py`
  - `src/xlm/data/sources/schema.py`
  - `src/xlm/data/sources/prober.py`
  - `src/xlm/data/sources/admission.py`
  - `src/xlm/cli/data_cmd.py`
  - `src/xlm/cli/main.py`
  - `manifests/datasets.catalog.yaml`
  - `fixtures/sources/sample_nested/manifest.yaml`
  - `fixtures/sources/sample_nested/nested_sample.parquet`
  - `fixtures/sources/sample_nested/mismatched_sample.parquet`
  - `fixtures/sources/sample_nested/sample_records.jsonl`
  - `tests/test_data_catalog.py`
  - `tests/test_source_discovery.py`
  - `tests/test_source_admission.py`
- Test commands and exit statuses:
  - `uv run --locked python -c "import sys; import xlm.data.sources; print('torch in modules:', 'torch' in sys.modules)"`: exit code 0 (`torch in modules: False` confirmed, zero-PyTorch base environment isolation)
  - `uv run --locked ruff check src/ tests/`: exit code 0 (all lint checks passed)
  - `uv run --locked ruff format --check src/ tests/`: exit code 0 (105 files cleanly formatted)
  - `uv run --locked mypy src/ tests/`: exit code 0 (strict type check passed across 108 source files)
  - `uv run --locked pytest tests/test_data_catalog.py tests/test_source_discovery.py tests/test_source_admission.py -v`: exit code 0 (28 dedicated P07 tests passed in 1.35s)
  - `uv run --locked pytest -m "not network"`: exit code 0 (186 passed, 1 skipped, 1 deselected in 61.27s)
  - `uv run --locked pytest tests/test_source_admission.py -k test_live_public_metadata_probe_seam`: exit code 0 (live Hugging Face API discovery verified within budget)
  - `uv run --locked python -m xlm.cli.main data sources --catalog manifests/datasets.catalog.yaml`: exit code 0 (all 20 sources listed as Pending)
  - `uv run --locked python -m xlm.cli.main data audit --catalog manifests/datasets.catalog.yaml`: exit code 0 (audited: 20 unadmitted, 0 admitted, 0 pending review)
  - `uv run --locked python -m xlm.cli.main data probe --catalog manifests/datasets.catalog.yaml --source finewiki --live`: exit code 0 (live discovery verified, commit SHA pinned, 46.6 KiB transferred, unadmitted status preserved)
  - `git diff uv.lock`: exit code 0 (lockfile clean)
- Evidence report: `docs/implementation/reports/P07.md`

### P08 — Bounded acquisition
- Status: VERIFIED
- Files:
  - `src/xlm/data/acquisition/__init__.py`
  - `src/xlm/data/acquisition/plan.py`
  - `src/xlm/data/acquisition/receipt.py`
  - `src/xlm/data/acquisition/disk.py`
  - `src/xlm/data/acquisition/progress.py`
  - `src/xlm/data/acquisition/fetcher.py`
  - `src/xlm/data/acquisition/verifier.py`
  - `src/xlm/cli/data_cmd.py`
  - `tests/test_acquisition_plan.py`
  - `tests/test_acquisition_fetcher.py`
  - `tests/test_acquisition_verifier.py`
  - `tests/test_acquisition_live.py`
- Test commands and exit statuses:
  - `uv run --locked python -c "import sys; import xlm.data.acquisition; print('torch in modules:', 'torch' in sys.modules)"`: exit code 0 (`torch in modules: False` confirmed, zero-PyTorch base environment isolation)
  - `uv run --locked ruff check src/ tests/`: exit code 0 (all lint checks passed)
  - `uv run --locked ruff format --check src/ tests/`: exit code 0 (116 files cleanly formatted)
  - `uv run --locked mypy src/ tests/`: exit code 0 (strict type check passed across 119 source files)
  - `uv run --locked pytest tests/test_acquisition_plan.py tests/test_acquisition_fetcher.py tests/test_acquisition_verifier.py -v`: exit code 0 (21 dedicated P08 tests passed in 4.52s)
  - `uv run --locked pytest tests/test_acquisition_live.py -v`: exit code 0 (live Hugging Face Hub pilot acquisition seam verified in 0.84s)
  - `uv run --locked pytest -m "not network and not cuda and not operator"`: exit code 0 (207 passed, 3 deselected in 66.64s)
  - `uv run --locked python -m xlm.cli.main data plan --source finewiki --files language_subsets.csv --pilot-approved --output data/scratch/test_plan.json`: exit code 0 (CLI plan compilation verified)
  - `uv run --locked python -m xlm.cli.main data fetch --plan data/scratch/test_plan.json --output-dir data/raw/test_finewiki --scratch-dir data/scratch/test_fetch`: exit code 0 (resumable fetch completed)
  - `uv run --locked python -m xlm.cli.main data status --plan data/scratch/test_plan.json --scratch-dir data/scratch/test_fetch`: exit code 0 (progress journal inspected)
  - `uv run --locked python -m xlm.cli.main data verify --plan data/scratch/test_plan.json --output-dir data/raw/test_finewiki`: exit code 0 (receipt validated and raw_dataset artifact published to P01 store)
  - `git diff uv.lock`: exit code 0 (lockfile clean)
- Evidence report: `docs/implementation/reports/P08.md`
- Next milestone: P09 (`prompts/09_cleaning_quality_pipeline.md`) — completed, see below



### P09 — Cleaning and quality pipeline
- Status: VERIFIED
- Note: partially implemented in a previous session and carried as IN PROGRESS. This
  session audited the inherited work, repaired the defects that blocked acceptance,
  closed the unmet acceptance criteria, and restored a clean gate state. Full audit
  findings are in `docs/implementation/reports/P09.md` §2.
- Files (new in this milestone):
  - `src/xlm/data/cleaning/__init__.py`
  - `src/xlm/data/cleaning/base.py`
  - `src/xlm/data/cleaning/types.py`
  - `src/xlm/data/cleaning/normalization.py`
  - `src/xlm/data/cleaning/html.py`
  - `src/xlm/data/cleaning/boilerplate.py`
  - `src/xlm/data/cleaning/repetition.py`
  - `src/xlm/data/cleaning/language.py`
  - `src/xlm/data/cleaning/length_noise.py`
  - `src/xlm/data/cleaning/pii.py`
  - `src/xlm/data/cleaning/structured.py`
  - `src/xlm/data/cleaning/quarantine.py`
  - `src/xlm/data/cleaning/reporting.py`
  - `src/xlm/data/cleaning/pipeline.py`
  - `fixtures/cleaning/shards/shard_00.jsonl` … `shard_05.jsonl`
  - `tests/test_cleaning_normalization.py`
  - `tests/test_cleaning_html.py`
  - `tests/test_cleaning_filters.py`
  - `tests/test_cleaning_structured.py`
  - `tests/test_cleaning_pipeline.py`
  - `tests/test_cleaning_scale.py`
- Files (modified in this session):
  - `src/xlm/data/canonical_io.py` (streaming Parquet read/write, multi-shard `read_shards`)
  - `src/xlm/cli/data_cmd.py` (shard fan-in, streaming writes, repaired `publish_artifact` call)
  - `src/xlm/cli/artifact_cmd.py` (artifact lookup now enumerates present kinds)
  - `tests/test_artifacts.py` (artifact-lookup regression tests)
- Test commands and exit statuses:
  - `uv run --locked ruff check src/ tests/`: exit code 0 (all checks passed; 66 pre-existing errors cleared)
  - `uv run --locked ruff format --check src/ tests/`: exit code 0 (136 files formatted; 6 previously unformatted)
  - `uv run --locked mypy src/ tests/`: exit code 0 (139 source files; 12 pre-existing errors cleared)
  - `uv run --locked pytest tests/ -m "not network and not cuda and not operator"`: exit code 0 (245 passed, 3 deselected in 66.55s; previously 231 passed / 1 failed)
  - `uv run --locked pytest tests/test_cleaning_*.py -q`: exit code 0 (36 P09 tests passed in 1.15s)
  - `uv run --locked python -c "import sys, xlm.data.cleaning; ..."`: exit code 0 (`torch` not imported; verified in a fresh interpreter)
  - `uv run --locked python -m xlm.cli.main data clean --input fixtures/cleaning/shards --output-dir data/clean/p09_fixture --preset educational_prose --publish`: exit code 0 (60 → 48 docs, 80.0% doc yield / 36.9% byte yield, 12 rejected, `clean_dataset` artifact published)
  - `uv run --locked python -m xlm.cli.main artifact verify clean_educational_prose_e5737733dfd6`: exit code 0 (verified against 4 file checksums)
  - `uv run --locked python -m xlm.cli.main data quality-report --dir data/clean/p09_fixture`: exit code 0
  - Bounded-memory negative control: accumulating implementation reintroduced → 3 tests failed; streaming implementation restored → 11 passed
  - `uv.lock` / `pyproject.toml`: unchanged (no dependency added)
- Measured fixture yield (synthetic 60-document corpus, not a live-source estimate):
  60 docs / 19,740 bytes in; 48 docs / 7,278 bytes retained; rejections
  `excessive_repetition:line_frequency_40` ×6 and `detected_secret:huggingface_token` ×6.
  Planted credential appears zero times in any output or quarantine file.
- Limitations recorded: fixture-only evidence; no labeled audit sample, so no
  classification precision is claimed; PII detection is not a certification;
  educational-density exemption is a declared heuristic; bounded memory demonstrated
  over 6 shards, not at corpus scale.
- Evidence report: `docs/implementation/reports/P09.md`
- Next milestone: P10 (`prompts/10_dedup_splits_exclusions.md`)

### P10 — Deduplication, lineage-safe splits and contamination controls
- Status: VERIFIED (development-mode scope; protected-final execution remains P21)
- Files (new):
  - `src/xlm/data/dedup/__init__.py`
  - `src/xlm/data/dedup/matchview.py`
  - `src/xlm/data/dedup/minhash.py`
  - `src/xlm/data/dedup/index.py`
  - `src/xlm/data/dedup/lineage.py`
  - `src/xlm/data/dedup/clusters.py`
  - `src/xlm/data/dedup/engine.py`
  - `src/xlm/data/pools/__init__.py`
  - `src/xlm/data/pools/splits.py`
  - `src/xlm/data/pools/freeze.py`
  - `src/xlm/data/exclusion/__init__.py`
  - `src/xlm/data/exclusion/benchmark.py`
  - `src/xlm/data/exclusion/receipt.py`
  - `tests/test_dedup_engine.py`
  - `tests/test_dedup_lineage.py`
  - `tests/test_pool_splits.py`
  - `tests/test_exclusion_benchmark.py`
  - `tests/test_exclusion_receipt.py`
- Files (modified):
  - `src/xlm/cli/data_cmd.py` (added `xlm data dedup` and `xlm data split`)
- Test commands and exit statuses:
  - `uv run --locked ruff check src/ tests/`: exit code 0 (all checks passed)
  - `uv run --locked ruff format --check src/ tests/`: exit code 0 (154 files formatted)
  - `uv run --locked mypy src/ tests/`: exit code 0 (157 source files, strict)
  - `uv run --locked pytest tests/ -m "not network and not cuda and not operator"`: exit code 0 (317 passed, 3 deselected in 66.94s; 245 before this milestone)
  - `uv run --locked pytest tests/test_dedup_engine.py`: exit code 0 (20 passed)
  - `uv run --locked pytest tests/test_dedup_lineage.py`: exit code 0 (6 passed)
  - `uv run --locked pytest tests/test_pool_splits.py`: exit code 0 (14 passed)
  - `uv run --locked pytest tests/test_exclusion_benchmark.py`: exit code 0 (10 passed)
  - `uv run --locked pytest tests/test_exclusion_receipt.py`: exit code 0 (22 passed)
  - `uv run --locked python -c "import sys, xlm.data.dedup, xlm.data.pools, xlm.data.exclusion; ..."`: exit code 0 (`torch imported: False`)
  - `uv run --locked python -m xlm.cli.main data dedup --input data/clean/p09_fixture/documents.jsonl --output-dir data/dedup/p10_fixture`: exit code 0 (48 → 14 survivors, 8 clusters)
  - `uv run --locked python -m xlm.cli.main data split --input data/dedup/p10_fixture/documents.jsonl --dedup-report ... --output-dir data/splits/p10_fixture`: exit code 0 (pool `pool_f1f70540613de0453418` frozen)
  - Negative controls: group-safe splits disabled → 2 tests failed; survivor selection made arrival-dependent → 1 test failed after a coverage gap was closed. Both restored and green.
  - `uv.lock` / `pyproject.toml`: unchanged (no dependency added)
- Artifacts: `data/dedup/p10_fixture/` (`documents.jsonl`, `dedup_report.json`);
  `data/splits/p10_fixture/` (`documents.jsonl`, `split_assignment.json`, `pool_freeze.json`)
- Limitations recorded: no labeled duplicate audit sample, so no near-duplicate
  precision or recall is claimed; 64-bit shingle hashes are not collision-free and the
  risk is surfaced rather than denied; paraphrased benchmark material is provably not
  detected (asserted directly in a test); the receipt interface provides integrity and
  issuer authenticity, not OS isolation — the environment remains nonsealed; all
  benchmark examples are authored synthetic fixtures and no protected final data was
  accessed.
- Evidence report: `docs/implementation/reports/P10.md`
- Next milestone: P11 (`prompts/11_pool_freeze_tokenizer_regime.md`)

### P11 — Reusable corpus pools and frozen tokenizer regime
- Status: VERIFIED (demo/pilot scope; the production 32,768 tokenizer fit is planned but NOT RUN)
- Files (new):
  - `src/xlm/data/pools/views.py`
  - `src/xlm/data/pools/manifest.py`
  - `src/xlm/data/pools/builder.py`
  - `src/xlm/data/pools/tokenizer_fit.py`
  - `src/xlm/data/pools/regime.py`
  - `fixtures/pools/views.yaml`
  - `fixtures/pools/binding.yaml`
  - `tests/test_pool_views.py`
  - `tests/test_pool_freeze_regime.py`
  - `tests/test_tokenizer_regime.py`
  - `tests/test_cli_pool_freeze.py`
- Files (modified):
  - `src/xlm/data/pools/__init__.py` (exports)
  - `src/xlm/cli/data_cmd.py` (added `xlm data pool build|inspect|verify` and `xlm data freeze`)
- Test commands and exit statuses:
  - `uv run --locked ruff check src/ tests/`: exit code 0 (all checks passed)
  - `uv run --locked ruff format --check src/ tests/`: exit code 0 (163 files formatted)
  - `uv run --locked mypy src/ tests/`: exit code 0 (166 source files, strict)
  - `uv run --locked pytest tests/ -m "not network and not cuda and not operator"`: exit code 0 (430 passed, 3 deselected in 70.32s; 317 before this milestone)
  - `uv run --locked pytest tests/test_pool_views.py`: exit code 0 (14 passed)
  - `uv run --locked pytest tests/test_pool_freeze_regime.py`: exit code 0 (43 passed)
  - `uv run --locked pytest tests/test_tokenizer_regime.py`: exit code 0 (48 passed)
  - `uv run --locked pytest tests/test_cli_pool_freeze.py`: exit code 0 (8 passed)
  - `xlm data pool build --input data/splits/p10_fixture/documents.jsonl --views fixtures/pools/views.yaml --binding fixtures/pools/binding.yaml --split-assignment data/splits/p10_fixture/split_assignment.json --output-dir data/pools/p11_fixture --budget-tokens 200000`: exit code 0 (`pool_ad074f006d26f4c7cea1`, DEMO_PILOT, 14 docs)
  - `xlm data pool inspect --manifest data/pools/p11_fixture/pool_manifest.json`: exit code 0 (2 views over 1 distinct family)
  - `xlm data pool verify --manifest ... --input ...`: exit code 0 (membership and content digests re-derived offline)
  - `xlm data freeze ... --vocab-size 32768 --fit-sample-bytes 2000 --plan-only`: exit code 0 (resource plan emitted; no fit executed)
  - `xlm data freeze ... --vocab-size 512 --fit`: exit code 0 (468-token tokenizer, `regime_6a7d4beeb5f5ece341aa`, no mixture bound, quick subset 2 of 3 diagnostic docs)
  - Negative controls: pool identity stripped of its policy bindings → 3 tests failed; exclusive assignment ignored → 2 tests failed. Both restored and green.
  - `uv.lock` / `pyproject.toml`: unchanged (no dependency added)
- Artifacts: `data/pools/p11_fixture/` (`documents.jsonl`, `pool_manifest.json`);
  `data/regime/p11_plan/` (plan-only); `data/regime/p11_fixture/`
  (`tokenizer_fit_manifest.json`, `tokenizer_fit_resource_plan.json`, `tokenizer/`, `research_regime.json`)
- Limitations recorded: the production 32,768 BPE fit was NOT RUN — the pilot pool
  holds 1,400 distinct training bytes, the executed fit used vocab 512 (468 actual
  tokens) and reports `is_production_baseline = False`; `ESTIMATED_BYTES_PER_TOKEN`
  is a planning constant, so every sufficiency figure is an estimate until the
  tokenizer is frozen in P12; the resource plan's peak-memory figure is an estimate,
  not a benchmark; `PRODUCTION_MIN_TRAIN_BYTES` is a project policy threshold, not an
  external standard; overlap detection is structural and cannot detect semantically
  duplicated upstream text.
- Evidence report: `docs/implementation/reports/P11.md`
- Next milestone: P12 (`prompts/12_token_shards_mixture_packing.md`)

### P12 — Production token shards, mixture scheduling and packing
- Status: VERIFIED (fixture/pilot scale; no production-scale corpus run). Audited and
  completed in a follow-up session: matched-canonical-byte/document exposure plans
  (required by the prompt and C07) were missing and have been implemented.
- Files (new):
  - `src/xlm/data/sampling/__init__.py`
  - `src/xlm/data/sampling/mixture.py`
  - `src/xlm/data/sampling/plan.py` (exposure plans + matched-byte/document plans)
  - `src/xlm/data/sampling/scheduler.py`
  - `src/xlm/data/sampling/packing.py`
  - `src/xlm/data/sampling/stream.py`
  - `src/xlm/cli/mixture_cmd.py`
  - `fixtures/mixture/documents.jsonl`
  - `fixtures/mixture/mix01.yaml`
  - `tests/test_mixture_planning.py`
  - `tests/test_packing_scheduling.py`
  - `tests/test_mixture_stream.py`
  - `tests/test_trainer_mixture.py`
- Files (modified):
  - `src/xlm/data/tokens.py` (extended the existing shard format: byte spans, lineage IDs, valid-target counts, BOS/EOS markers, counters sidecar, mmap and streaming reads)
  - `src/xlm/training/data.py` (added `BatcherProtocol`)
  - `src/xlm/training/trainer.py`, `src/xlm/training/checkpoint.py` (typed against the protocol)
  - `src/xlm/cli/data_cmd.py` (added `xlm data tokenize`), `src/xlm/cli/main.py` (mixture app)
- Test commands and exit statuses:
  - `uv run --locked ruff check src/ tests/`: exit code 0 (all checks passed)
  - `uv run --locked ruff format --check src/ tests/`: exit code 0
  - `uv run --locked mypy src/ tests/`: exit code 0 (177 source files, strict)
  - `uv run --locked pytest tests/ -m "not network and not cuda and not operator"`: exit code 0 (512 passed, 3 deselected in 74.77s; 430 before this milestone, 506 at first pass)
  - `uv run --locked pytest tests/test_mixture_planning.py`: exit code 0 (31 passed)
  - `uv run --locked pytest tests/test_packing_scheduling.py`: exit code 0 (23 passed)
  - `uv run --locked pytest tests/test_mixture_stream.py`: exit code 0 (22 passed)
  - `uv run --locked pytest tests/test_trainer_mixture.py`: exit code 0 (6 passed; the real Trainer driving the mixture loader)
  - `xlm mixture matched-plan --recipe fixtures/mixture/mix01.yaml --shards data/shards/p12_mix --basis canonical_bytes --budget 50000 --output data/shards/p12_mix/matched_mix01.json`: exit code 0 (planned bytes pinned to the budget; derived token budget reported separately; repetition warned)
  - `uv run --locked python -c "import sys, xlm.data.sampling; ..."`: exit code 0 (`torch imported: False`)
  - `uv run --locked python -m xlm.cli.main demo`: exit code 0 (offline vertical slice re-run, 200 valid targets, loss 5.5695 -> 4.5018)
  - `xlm data tokenize --input fixtures/mixture/documents.jsonl --tokenizer data/regime/p11_fixture/tokenizer --output-dir data/shards/p12_mix`: exit code 0 (3 shards, 6,592 valid targets, uint16, coverage 1.0000)
  - `xlm mixture validate|plan|preview|inspect`: exit code 0 (plan `plan_013dc0cf44ed71560c65`; preview drift 0.00000 against a 0.02000 bound; repetition shortfall warned at plan time)
  - Negative controls: dropping the final target from every window → 5 tests failed; exhaustion silently repeating instead of raising → 2 tests failed. Both restored and green.
  - `uv.lock` / `pyproject.toml`: unchanged (no dependency added)
- Defects found and fixed during implementation: a `memoryview` slice that kept an
  exported pointer and blocked mmap close; an epoch-advance guard that missed the
  carried-context case and produced a one-token window at a source's end.
- Artifacts: `data/shards/p12_mix/` (3 shard directories + `plan_mix01.json`),
  `data/shards/p12_fixture/`
- Audit note: matched-canonical-byte/document exposure plans were required by the
  prompt and C07 but were deferred in the first pass; they are now implemented
  (`compile_matched_plan`, `xlm mixture matched-plan`) with planning and real-shard
  byte-vs-BPE invariance tests. See report §8a.
- Limitations recorded: fixture scale only, no throughput or peak-RSS measurement;
  share drift is granularity-bounded on short runs and the tests assert the
  granularity relationship rather than an unreachable steady-state bound; multiworker
  prefetch is not implemented, so the "worker-count" acceptance item is covered as
  partition/microbatch-count independence; matched plans are planning-side only and
  are not yet bound to a run plan; `PackingPolicy.max_document_tokens` is identity-bound
  but not separately enforced beyond the context-window cap.
- Evidence report: `docs/implementation/reports/P12.md`
- Next milestone: P13 (`prompts/13_real_dataset_views_and_mix01.md`)

### P13 — Real dataset views and mix01
- Status: VERIFIED (definition/gating scope; live corpus NOT RUN, mixture BLOCKED pending admission)
- Files (new):
  - `recipes/mixtures/mix01_views.yaml` (13-view registry with observed revisions/configs)
  - `src/xlm/data/sources/mix01.py` (registry, exact preset validation, treatment diffs, run gating, pilot envelope)
  - `src/xlm/data/adapters/mix01_adapters.py` (11 per-family adapters with fail-closed contracts)
  - `fixtures/mixture/views/` (11 schema fixtures + README, synthetic and labelled)
  - `tests/test_mix01_views.py` (32 tests)
- Files (modified):
  - `src/xlm/cli/mixture_cmd.py` (`preset-validate`, `preset-diff`)
  - `src/xlm/cli/data_cmd.py` (`mix01-status` with `--preset` gating)
- Test commands and exit statuses:
  - `uv run --locked ruff check src/ tests/`: exit code 0 (all checks passed)
  - `uv run --locked ruff format --check src/ tests/`: exit code 0 (177 files clean)
  - `uv run --locked mypy src/ tests/`: exit code 0 (180 source files, strict)
  - `uv run --locked pytest tests/ -m "not network and not cuda and not operator"`: exit code 0 (544 passed, 3 deselected in 75.45s; 512 before this milestone)
  - `uv run --locked pytest tests/test_mix01_views.py`: exit code 0 (32 passed)
  - `xlm mixture preset-validate --preset recipes/mixtures/mix01.yaml`: exit code 0 (exact sum 1, 12 views, identity `d670bd3a…`)
  - `xlm mixture preset-diff --base .../mix01.yaml --variant .../m4_txt360_web.yaml`: exit code 0 (−0.15/−0.05 Nemotron, +0.20 txt360_web, 10 unchanged)
  - `xlm data mix01-status`: exit code 0 (0/13 ready, all NOT LIVE-VERIFIED with reasons)
  - `xlm data mix01-status --preset recipes/mixtures/mix01.yaml`: exit code 1 (blocked by 12 components, no fallback — expected)
  - Negative controls: neutered no-fallback validator → 1 test failed; substring organic matching → 1 test failed. Both restored and green.
  - Bounded live metadata discovery (unauthenticated, allowlisted host only): 28 requests / ~2.7 MiB total; real revisions for 9 of 10 repos; Nemotron-CC confirmed GATED; exact config names recorded (`High-Quality`, `eng_Latn`, `web-high-medium`, `Nemotron-Pretraining-Wiki-Rewrite`, `general`+`planning`, `en`, `default`).
  - `uv.lock` / `pyproject.toml`: unchanged (no dependency added)
- Artifacts: `data/probe/p13_mix01_snapshot.json`, `data/probe/p13_mix01_configs.json`, `data/probe/p13_ifm_behaviors_configs.json`
- Limitations recorded: 0 of 13 views READY (no admission, no reviews, no live-tested adapter); Essential-Web revision unresolved; Nemotron-CC gated; Common Pile license unresolved; adapter field names expected-not-verified; pilot NOT RUN (no approved sources, no authorization envelope issued); M4 TxT360 gated on its view's verification/admission.
- Evidence report: `docs/implementation/reports/P13.md`
- Next milestone: P14 (`prompts/14_cuda_profile_and_performance.md`)

### P14 — Single-4090 execution and correctness-preserving performance
- Status: VERIFIED (real-GPU scope; compile/eager parity NOT RUN — no codegen toolchain)
- Files (new):
  - `src/xlm/models/backends.py` (backend policy, SDPA kernel probing, compile helpers/probe, precision validation)
  - `src/xlm/training/profile.py` (preflight, isolated calibration, resource plans, freeze)
  - `src/xlm/training/profile_worker.py` (per-size worker process)
  - `src/xlm/training/recovery.py` (OOM restart/fork decisions)
  - `src/xlm/cli/profile_cmd.py` (`xlm profile`)
  - `tests/test_cuda_execution.py` (12 CPU-safe + 9 CUDA tests)
- Files (modified):
  - `src/xlm/models/transformer.py` (opt-in activation checkpointing)
  - `src/xlm/training/trainer.py` (precision validation, checkpointing/compile wiring, scaler skip-and-backoff, execution report)
  - `src/xlm/training/checkpoint.py` (scaler persistence, precision metadata, fork-aware restore)
  - `src/xlm/cli/train_cmd.py` (precision/backend/compile/checkpointing options, FP16 resume)
  - `src/xlm/cli/doctor.py` (BF16, probed SDPA kernels, wheel/drive explanations, corrected capabilities)
  - `src/xlm/cli/main.py` (`profile` command)
  - `tests/test_doctor.py` (7-tuple inspection)
- Test commands and exit statuses:
  - `uv sync --locked --extra cuda`: exit code 0 (`torch==2.14.0+cu126`)
  - `uv run --locked --extra cuda pytest tests/test_cuda_execution.py tests/test_cuda.py -m cuda`: exit code 0 (9 passed, 1 skipped — CUDA compile parity skipped: no Triton in wheel)
  - `xlm profile --config recipes/models/50m.yaml ...`: exit code 0 (32,768 tok/s @ mb8, 3.90 GiB reserved, ckpt 0.56 GiB)
  - `xlm profile --config recipes/models/150m.yaml ...`: exit code 0 (13,430 tok/s @ mb8, 7.30 GiB reserved, ckpt 1.68 GiB)
  - `xlm profile --config recipes/models/300m.yaml ...`: exit code 0 (3,049 tok/s @ mb8, 11.38 GiB reserved, ckpt 3.35 GiB)
  - `xlm doctor` (cuda env): exit code 0 (4090 sm_89, BF16 yes, SDPA flash=no/mem_efficient=yes/math=yes)
  - `uv sync --locked --extra cpu`: exit code 0 (restored `2.14.0+cpu`)
  - `uv run --locked ruff check src/ tests/`: exit code 0 (all checks passed)
  - `uv run --locked ruff format --check src/ tests/`: exit code 0 (183 files clean)
  - `uv run --locked mypy src/ tests/`: exit code 0 (186 source files, strict)
  - `uv run --locked pytest tests/ -m "not network and not cuda and not operator"`: exit code 0 (556 passed, 1 skipped, 12 deselected; 544 before this milestone)
  - Negative controls: shared worker checkpoint IDs aliased another model's artifacts (found live, fixed with isolated XLM_HOME + unique IDs, measurements re-run); instant-fail FP16 overflow defeated loss scaling (replaced with bounded skip-and-backoff).
  - `uv.lock` / `pyproject.toml`: unchanged (no dependency added)
- Artifacts: `data/profiles/p14_50m/`, `data/profiles/p14_150m/`, `data/profiles/p14_300m/` (profile.json + profile_freeze.json each)
- Limitations recorded: compile/eager parity NOT RUN (no Triton in Windows CUDA wheel, no C++ compiler for CPU Inductor); no flash attention in this wheel build; no live OOM occurred (recovery unit-tested only); dry runs use synthetic tokens (loader wait ≈ 0, re-measure on production loader); ETA ranges are wide measurement intervals; matched-run freezes written but no matched runs executed.
- Evidence report: `docs/implementation/reports/P14.md`
- Next milestone: P15 (`prompts/15_official_evaluation_harness.md`)

### P15 — Pinned official evaluator and tiered benchmark protocols
- Status: VERIFIED (offline fixture evidence; one bounded live search smoke; full
  official search/confirmation NOT RUN, final operator-blocked by design)
- Files (new):
  - `src/xlm/evaluation/harness.py` (version pin, identity, pinned task materialization, registry)
  - `src/xlm/evaluation/harness_lm.py` (LM subclass registered via the public registry)
  - `src/xlm/evaluation/harness_runner.py` (bounded suite execution + evidence assembly)
  - `src/xlm/evaluation/suites.py` (tiers, variants, firewall, partitions, four-task index, final request)
  - `src/xlm/evaluation/evidence.py` (per-item records, identity-checked cache)
  - `src/xlm/evaluation/reference.py` (comparator registration, bounded download plan)
  - `fixtures/eval/tasks/` (authored synthetic fixture tasks + data)
  - `tests/test_eval_suites.py` (29), `tests/test_harness_adapter.py` (14)
- Files (modified):
  - `src/xlm/cli/eval_cmd.py` (`--suite search|confirmation|final`, tasks/include-path/limit/request-only/final-authorization)
  - `pyproject.toml`, `uv.lock` (`eval` extra: `lm-eval==0.4.13`; mypy overrides)
- Test commands and exit statuses:
  - `uv add --optional eval "lm-eval==0.4.13"`: exit code 0 (lock updated)
  - `uv sync --locked --extra cpu --extra eval`: exit code 0
  - `uv run --locked --extra cpu --extra eval pytest tests/test_eval_suites.py tests/test_harness_adapter.py`: exit code 0 (43 passed in 172s)
  - `uv run --locked --extra cpu --extra eval pytest tests/ -m "not network and not cuda and not operator"`: exit code 0 (599 passed, 1 skipped, 12 deselected in 259s; 556 before)
  - `uv run --locked --extra cpu --extra eval ruff check src/ tests/`: exit code 0
  - `uv run --locked --extra cpu --extra eval ruff format --check src/ tests/`: exit code 0 (191 files)
  - `uv run --locked --extra cpu --extra eval mypy src/ tests/`: exit code 0 (194 source files, strict)
  - `xlm evaluate … --suite search --tasks xlm_fixture_mc --include-path fixtures/eval/tasks --limit 3`: exit code 0 (acc 0.6667 / acc_norm 0.3333; index withheld with notes)
  - `xlm evaluate … --suite final --request-only`: exit code 0 (frozen request written)
  - `xlm evaluate … --suite final` without authorization: exit code 1 (split firewall)
  - Bounded live smoke (ARC-Easy train pinned revision, limit 10, scripted): exit code 0 (limited smoke acc 0.4000 / acc_norm 0.2000; index withheld)
  - Lazy import check: `lm_eval` not imported at CLI startup even when installed
  - Negative controls: evidence keyed by wrapper name hid `arc_easy` from the index (found in live smoke, fixed to logical task names, re-run); final suite without authorization now refused with a test.
- Artifacts: `manifests/eval_dataset_pins.yaml`; `fixtures/eval/tasks/`; `data/eval/p15_smoke/`, `data/eval/p15_live_smoke/`
- Limitations recorded: full official suites NOT RUN (only a 10-item labeled smoke); final execution is P21's operator path; no model downloads; parity evidence is fixture-exact plus the bounded live slice; BLiMP partition sizes are a policy target, not a proven balance.
- Evidence report: `docs/implementation/reports/P15.md`
- Next milestone: P16 (`prompts/16_experiment_plans_and_queue.md`)

### P16 — Experiment plans, bounded local queue and mixture search
- Status: VERIFIED (offline definition/gating scope; billion-token trials are plans only)
- Files (new):
  - `src/xlm/experiments/__init__.py`
  - `src/xlm/experiments/snapshot.py` (immutable captures, secret fail-closed, verify)
  - `src/xlm/experiments/authorization.py` (hash-bound tickets, C13 smoke caps)
  - `src/xlm/experiments/plans.py` (draft resolution, blockers, horizon tagging/forks)
  - `src/xlm/experiments/sweeps.py` (lists, grids, random/simplex proposals, caps)
  - `src/xlm/experiments/campaigns.py` (expansion, selections, budget gates)
  - `src/xlm/experiments/queue.py` (jobs/attempts tables, leases, runner, recovery)
  - `src/xlm/cli/experiment_cmd.py` (`experiment`, `queue`, `campaign` commands)
  - `tests/test_experiment_plans.py` (19), `tests/test_queue.py` (18)
- Files (modified):
  - `src/xlm/cli/main.py` (command registration)
  - `src/xlm/cli/train_cmd.py` (non-fork resume past schedule horizon is refused)
- Test commands and exit statuses:
  - `uv run --locked ruff check src/ tests/`: exit code 0 (all checks passed)
  - `uv run --locked ruff format --check src/ tests/`: exit code 0 (201 files clean)
  - `uv run --locked mypy src/ tests/`: exit code 0 (204 source files, strict)
  - `uv run --locked pytest tests/test_experiment_plans.py tests/test_queue.py`: exit code 0 (37 passed)
  - `uv run --locked pytest tests/ -m "not network and not cuda and not operator"`: exit code 0 (636 passed, 1 skipped, 12 deselected; 599 before)
  - `xlm campaign plan recipes/campaigns/data_search.yaml`: exit code 0 (54 trials, 6 mixtures, 48 blocked on selection, nothing executed)
  - `xlm experiment plan recipes/experiments/baseline_50m.yaml`: exit code 0 (blockers listed)
  - `xlm experiment submit` (blocked plan, then unauthorized large plan): exit code 1 both, with reasons
  - Negative controls: disabled snapshot verification → tamper test failed; disabled plan-hash dedup → no duplicate anyway (job-ID + ledger-PK backstops). Both restored.
  - `uv.lock` / `pyproject.toml`: unchanged (no dependency added)
- Artifacts: plan/snapshot outputs are command-scoped (tmp in tests); no persistent campaign artifacts
- Limitations recorded: billion-token trials are plans, never executions; eval cadence predeclared but queue-executes only checkpoints; lease logic tested without GPU hardware; secret patterns are heuristics; toy runs are synthetic-data CPU runs.
- Evidence report: `docs/implementation/reports/P16.md`
- Next milestone: P17 (`prompts/17_statistics_comparisons_promotion.md`)

### P17 — Fair comparisons, uncertainty and size-promotion gates
- Status: VERIFIED (synthetic-fixture scope; no live runs, no hardware claims)
- Files (new):
  - `src/xlm/comparison/__init__.py`
  - `src/xlm/comparison/tracks.py` (six track schemas, eligibility with readable diffs)
  - `src/xlm/comparison/bootstrap.py` (paired cluster bootstrap, seed spread, suite index)
  - `src/xlm/comparison/curves.py` (interpolation-only crossing, compute-to-target)
  - `src/xlm/comparison/promotion.py` (frozen gates, drafts, factorial/ablation matrices)
  - `src/xlm/cli/compare_cmd.py` (`xlm compare`, `xlm promote`)
  - `tests/test_comparison.py` (34)
- Files (modified):
  - `src/xlm/cli/main.py` (command registration)
- Test commands and exit statuses:
  - `uv run --locked ruff check src/ tests/`: exit code 0 (all checks passed)
  - `uv run --locked ruff format --check src/ tests/`: exit code 0 (208 files clean)
  - `uv run --locked mypy src/ tests/`: exit code 0 (211 source files, strict)
  - `uv run --locked pytest tests/test_comparison.py`: exit code 0 (34 passed)
  - `xlm compare … --track architecture …`: exit code 0 (eligible; index delta +41.667 [+16.667, +66.667], synthetic)
  - `xlm promote …` (1 seed/arm): exit code 1 (seed gate correctly refused)
  - `xlm promote … --to-size 150m` (2 seeds/arm): exit code 0 (schema-valid draft, not_authorized, no final suite)
  - Negative controls: unstratified resampling dropped a task mid-replicate (stratified within tasks); CLI refusal fixture initially miswired (corrected, asserts on `tokenizer_hash`).
  - `uv.lock` / `pyproject.toml`: unchanged (no dependency added)
- Artifacts: `data/compare/p17_demo/` (synthetic evidence, plans, comparison, decision, draft + lineage)
- Limitations recorded: all numbers synthetic; no live comparison ran; cluster maps are caller-supplied (BLiMP refuses without one); search-stage CIs are decision support only, with multiplicity notes.
- Evidence report: `docs/implementation/reports/P17.md`
- Next milestone: P18 (`prompts/18_research_plugins_and_idea_cards.md`)

### P18 — Research idea workflow and safe extensibility
- Status: VERIFIED (synthetic-fixture scope; no breakthrough claims, no live runs)
- Files (new):
  - `src/xlm/research/__init__.py`
  - `src/xlm/research/ideas.py` (versioned cards, statuses, validation incl. novelty-certainty refusal)
  - `src/xlm/research/capabilities.py` (declarations + plan-time combination checks)
  - `src/xlm/research/loader.py` (manifests, discovery, file-scope/protected-surface guards, registration)
  - `src/xlm/research/scaffold.py` (disabled-by-default per-category scaffolds)
  - `src/xlm/plugins/__init__.py` + `noop_architecture/`, `noop_objective/`, `noop_optimizer/`, `noop_tokenizer/` (working nonnovel controls)
  - `src/xlm/cli/research_cmd.py` (`research idea|validate|scaffold|check-plugin`)
  - `tests/test_research.py` (22)
- Files (modified):
  - `src/xlm/cli/main.py` (command registration)
- Test commands and exit statuses:
  - `uv run --locked ruff check src/ tests/`: exit code 0 (all checks passed)
  - `uv run --locked ruff format --check src/ tests/`: exit code 0 (220 files clean)
  - `uv run --locked mypy src/ tests/`: exit code 0 (223 source files, strict)
  - `uv run --locked pytest tests/test_research.py`: exit code 0 (22 passed)
  - `xlm research idea new/validate` (blank): exit 0 then exit 1 with missing fields
  - `xlm research scaffold --category architecture`: exit code 0 (6 files, disabled)
  - `xlm research check-plugin src/xlm/plugins/noop_tokenizer`: exit code 0 (registered, nonnovel)
  - Negative controls: weakened file-scope guard → protected test failed; dropped falsification requirement → missing-field test failed. Both restored.
  - `uv.lock` / `pyproject.toml`: unchanged (no dependency added)
- Artifacts: `data/research/p18_demo/` (blank card + disabled scaffold)
- Limitations recorded: no research claims; prior-art records are not searches; no provider integration; secret patterns are heuristics.
- Evidence report: `docs/implementation/reports/P18.md`
- Next milestone: P19 (`prompts/19_reports_and_dashboard.md`)

### P19 — Research reports and optional local dashboard
- Status: VERIFIED (offline scope; UI screenshot/manual checks NOT RUN headless)
- Files (new):
  - `src/xlm/reports/__init__.py`
  - `src/xlm/reports/collect.py` (run/campaign/dataset collectors, protected stripping)
  - `src/xlm/reports/render.py` (JSON/CSV/Markdown/self-contained HTML, escaping, curves)
  - `src/xlm/dashboard/__init__.py`
  - `src/xlm/dashboard/server.py` (loopback read-only stdlib server, bind refusal)
  - `src/xlm/cli/report_cmd.py` (`report`, `runs list`, `dashboard`)
  - `tests/test_reports.py` (18), `tests/test_dashboard.py` (5)
- Files (modified):
  - `src/xlm/cli/main.py` (command registration)
- Test commands and exit statuses:
  - `uv run --locked ruff check src/ tests/`: exit code 0 (all checks passed)
  - `uv run --locked ruff format --check src/ tests/`: exit code 0 (228 files clean)
  - `uv run --locked mypy src/ tests/`: exit code 0 (231 source files, strict)
  - `uv run --locked pytest tests/test_reports.py tests/test_dashboard.py`: exit code 0 (23 passed)
  - Toy campaign via P16 queue (2 CPU jobs): exit code 0 (both SUCCEEDED)
  - `xlm report` (md/html) from campaign records: exit code 0 (readable, gaps listed)
  - `xlm runs list`: exit code 0 (ledger states reproduced)
  - Negative controls: escaping disabled → injection test failed; missing-as-zero → n/a test failed. Both restored.
  - `uv.lock` / `pyproject.toml`: unchanged (no dependency added)
- Artifacts: `data/reports/p19_demo/` (ledger, run records, run + campaign reports)
- Limitations recorded: UI screenshots/manual checks NOT RUN (headless); no sealed store to audit stripping against; curves need recorded history; unmeasured compute stays missing.
- Evidence report: `docs/implementation/reports/P19.md`
- Next milestone: P20 (`prompts/20_export_generation_portability.md`)

### P20 — Model export, reproducible loading and inference
- Status: VERIFIED (offline scope; HF runtime parity NOT RUN — no transformers install)
- Files (new):
  - `src/xlm/export/__init__.py`
  - `src/xlm/export/manifest.py` (hashes, provenance, accounting, compat rules)
  - `src/xlm/export/writer.py` (safe weights, tokenizer, secrets refusal, head exclusion)
  - `src/xlm/export/loader.py` (hash-first verify, tied restore, dtype/device rules)
  - `src/xlm/export/hf.py` (baseline-only Llama-layout mapping with exactness proof)
  - `src/xlm/inference/session.py` (bounded replayable completion sessions)
  - `src/xlm/cli/export_cmd.py` (`xlm export`)
  - `tests/test_export.py` (19)
- Files (modified):
  - `src/xlm/models/serialization.py` (safetensors fallback for export bundles)
  - `src/xlm/cli/generate_cmd.py` (`generate-session` command)
  - `src/xlm/cli/main.py` (command registration)
  - `pyproject.toml`, `uv.lock` (`safetensors==0.8.0` pinned — required by acceptance)
- Test commands and exit statuses:
  - `uv add safetensors`: exit code 0 (`safetensors==0.8.0`)
  - `uv run --locked ruff check src/ tests/`: exit code 0 (all checks passed)
  - `uv run --locked ruff format --check src/ tests/`: exit code 0 (236 files clean)
  - `uv run --locked mypy src/ tests/`: exit code 0 (239 source files, strict)
  - `uv run --locked pytest tests/test_export.py`: exit code 0 (19 passed)
  - `xlm export …`: exit code 0 (98,880-param bundle, optimizer excluded)
  - `xlm generate-session …` / `xlm generate <bundle>`: exit code 0 (transcript saved; safetensors read directly)
  - Negative controls: hash check disabled → corruption test failed; tied check bypassed → exposed a missing diverged-tied test, added it, bypass then failed it. Both restored.
- Artifacts: `data/export/p20_demo_ckpt/`, `data/export/p20_demo_bundle/`, `data/export/p20_demo_session.json`
- Limitations recorded: HF runtime parity NOT RUN; manifest is the trust root (signing is P21); no cache exists (labeled); nothing published anywhere.
- Evidence report: `docs/implementation/reports/P20.md`
- Next milestone: P21 (`prompts/21_isolation_security_release.md`)

### P21 — Protected final evaluation, authorization and release auditing
- Status: VERIFIED (mechanism scope; two-identity deployment NOT RUN; one real cache finding remediated)
- Files (new):
  - `src/xlm/operator/__init__.py`
  - `src/xlm/operator/sealed.py` (fail-closed sealed-readiness checks)
  - `src/xlm/operator/final.py` (requests, receipts, auth/revocation, quotas, reviewed-bundle boundary, access log, decontamination receipts)
  - `src/xlm/operator/release.py` (eleven-check release audit, final-data cache scan, confirmed purge)
  - `src/xlm/cli/final_cmd.py` (`final request|execute|verify-receipt`, `release audit`)
  - `tests/test_operator.py` (22)
- Files (modified):
  - `src/xlm/cli/main.py` (command registration)
- Test commands and exit statuses:
  - `uv run --locked ruff check src/ tests/`: exit code 0 (all checks passed)
  - `uv run --locked ruff format --check src/ tests/`: exit code 0 (242 files clean)
  - `uv run --locked mypy src/ tests/`: exit code 0 (245 source files, strict)
  - `uv run --locked pytest tests/test_operator.py`: exit code 0 (22 passed)
  - `xlm final request …`: exit code 0 (frozen request + hash, nothing scored)
  - `xlm release audit data/release/p21_demo`: exit code 0 (RELEASE, 11/11 pass)
  - `verify_no_final_examples()` on real caches: exit 1→0 (found 2 ARC-Easy test files from the P15 smoke → purged with confirmation → clean)
  - Negative controls: sealed writability ignored → writable test failed; aggregates boundary weakened → forbidden-supplier test failed. Both restored.
  - `uv.lock` / `pyproject.toml`: unchanged (no dependency added)
- Artifacts: `data/operator/p21_demo_request.json`, `data/release/p21_demo/`
- Limitations recorded: no protected deployment here (NOT RUN); subprocess is not a security boundary (documented); cache absence is not a non-read proof; no keys or final data in the repo.
- Evidence report: `docs/implementation/reports/P21.md`
- Next milestone: P22 (`prompts/22_campaign_bootstrap_and_runbooks.md`)

### P22 — Complete user workflows and production campaign preparation
- Status: VERIFIED (offline scope; large campaigns prepared, never launched)
- Files (new):
  - `src/xlm/prepare/__init__.py`
  - `src/xlm/prepare/config.py` (strict stage/config schemas, --define overrides)
  - `src/xlm/prepare/planner.py` (dry planning, staleness, downstream invalidation)
  - `src/xlm/prepare/runner.py` (authorized execution, reuse/resume/force, state)
  - `src/xlm/cli/prepare_cmd.py` (`prepare`, `maintenance cleanup`)
  - `recipes/prepare/offline_toy.yaml`, `recipes/prepare/toy_mixture.yaml`
  - `recipes/experiments/tiny_demo.yaml`
  - `recipes/comparisons/` (5 track-checked comparison drafts)
  - `recipes/campaigns/confirm_multiseed.yaml`, `factorial_demo.yaml`, `staged_50m_150m_300m.yaml`
  - `src/xlm/comparison/recipes.py` (comparison_recipe validation)
  - `docs/runbooks/windows.md`, `docs/runbooks/linux.md`
  - `tests/test_prepare.py` (13), `tests/test_recipes.py` (9), `tests/test_offline_workflow.py` (4)
- Files (modified):
  - `src/xlm/cli/config_cmd.py` (comparison/campaign/prepare/registry kinds; clean error paths)
  - `src/xlm/cli/main.py` (command registration)
  - `recipes/README.md` (examples rewritten to the real CLI)
- Test commands and exit statuses:
  - `uv run --locked ruff check src/ tests/`: exit code 0 (all checks passed)
  - `uv run --locked ruff format --check src/ tests/`: exit code 0 (251 files clean)
  - `uv run --locked mypy src/ tests/`: exit code 0 (254 source files, strict)
  - `uv run --locked pytest tests/test_recipes.py tests/test_prepare.py tests/test_offline_workflow.py`: exit code 0 (27 passed: 9 + 14 + 4)
  - `xlm prepare --config recipes/prepare/offline_toy.yaml --plan-only`: exit code 0 (10 stages, nothing executed)
  - `xlm prepare … --authorize` (isolated home): exit code 0 (all stages succeeded); repeat: exit code 0 (all reused)
  - `xlm campaign plan recipes/campaigns/staged_50m_150m_300m.yaml`: exit code 0 (60 trials, nothing executed)
  - Negative controls: neutered executor → reuse test failed; ignored invalidations → stale test failed. Both restored.
  - `uv.lock` / `pyproject.toml`: unchanged (no dependency added)
- Artifacts: `data/prepare/p22_home/` (10-stage offline run + state)
- Limitations recorded: large campaigns are plans only; pilot/live transfers gated, not exercised; cost without measured profiles is unknown; Linux runbook mirrors executed Windows behavior.
- Evidence report: `docs/implementation/reports/P22.md`
- Historical next at P22 completion: P23 (now audited; remediation gates are in the P23 section).

### P23 — Independent acceptance audit

- Status: VERIFIED audit delivery; production acceptance BLOCKED.
- Report: [reports/P23.md](reports/P23.md).
- Requirement map, supported combinations, defects and next prompt: [FINAL_ACCEPTANCE.md](FINAL_ACCEPTANCE.md).
- Registered command trace: [CLI_AUDIT.md](CLI_AUDIT.md).
- Actual command/resource/source-hash evidence: `docs/implementation/evidence/P23/`.
- Offline baseline: 783 passed, 1 skipped, 12 deselected. Final offline suite: **884 passed, 1 skipped, 12 deselected**; exit 0. Final lint/format, mypy (187 modules), demo, doctor and wheel build passed. The P23 report preserves intermediate failures and the formatting-only correction.
- Separate real CUDA: 9 passed, 1 skipped; tiny profile measured. Live source: pinned metadata only, zero corpus rows.
- Next: remediate D01–D08 and rerun P23; do not launch a production campaign or protected final evaluation.

### P23 remediation Stage 1 — D01

- Implementation: IMPLEMENTED; bounded offline verification: VERIFIED.
- 99 focused checks and 92 caller checks passed; one caller test deselected, no skips.
- Formatting/lint passed; mypy passed for 187 source files. Actual imports are from
  `D:\Project\xlm\src`, with a fresh cache-backed offline uv CPU/evaluation environment.
- Before evidence preserved: 5 failures and 1 identical-reuse pass. Final evidence,
  compatibility limits and defect/code/test/result map: [P23-D01 report](reports/P23-D01.md).
- Legacy schema-v1 originals remain checksum-only; authentic executed provenance
  remains D06. No trainer, evaluator, queue or frozen contract was modified in D01.
- At Stage 1 completion, D06 awaited separate approval; its current status is below.
- Final complete offline rerun: NOT RUN, scheduled after all approved stages.
  Overall production acceptance remains BLOCKED; external validation remains operator work.

### P23 remediation Stage 2 — D06

- Implementation and bounded offline verification approved on 2026-09-20.
- Implementation: IMPLEMENTED; bounded Windows CPU offline verification: VERIFIED.
- 53 core, 27 caller, 83 related regression and 99 D01 checks passed (262 total;
  no skips). Format/lint passed; mypy passed for 194 source files. All final groups
  record unchanged source hashes and imports from the repaired checkout/captures.
- Captured execution, actual environment fingerprints, checkpoint/evaluation
  provenance and fresh-process recovery use the existing infrastructure.
- Original before evidence is preserved: 3 failed, 1 positive control passed.
- Detailed scope and results: [P23-D06 report](reports/P23-D06.md).
- At this Stage 2 boundary, [D03's plan/reproduction](evidence/P23-D03/PLAN.md)
  awaited approval (2 failed, 1 positive control passed). The later core approval
  and current scope are recorded below.
- Overall production acceptance remains BLOCKED; the complete offline rerun
  remains deferred.

### P23 remediation Stage 3 — D03 core

- Approved core scope: actual configurable inputs/components, source scheduling,
  packing/target accounting and complete checkpoint restoration through the
  existing direct/queue/resume paths. Implementation: IMPLEMENTED; bounded
  Windows CPU offline verification: VERIFIED.
- 406 test executions / 400 distinct cases passed, zero skips. Format/lint passed;
  mypy passed for 195 source files. Product and executed-test hashes verified;
  documented unrelated test-only edits were separately rerun in `after05`.
- Report and defect/code/test/result map: [P23-D03](reports/P23-D03.md).
- Operator prerequisites and actual uv CLI commands: [bounded baseline pilot](D03_BASELINE_PILOT.md).
- D03 matched-document/byte execution across tokenizers: OPEN / DEFERRED to the
  tokenizer-research phase, not VERIFIED.
- D02 remains unimplemented. Its [focused plan and offline reproduction](evidence/P23-D02/PLAN.md)
  are ready for separate approval: 4 failures / 1 positive control and 2 explicit
  refusal failures. No live acquisition or real-corpus preparation occurred.
- Full-size public planning still lacks measured profile lookup integration;
  smoke/production guards remain. External pilot/profile/evaluation work is NOT RUN.
- Final complete platform rerun: NOT RUN. Overall production acceptance: BLOCKED.

## ASTRA INDEPENDENT REVIEW + LOCAL CUDA CERTIFICATION

2026-09-26, `review/p35-readiness-astra`, starting HEAD
`176e351b37a84f323b8fe53c10fa2ea87e5251c2`, clean worktree. Windows/Python
3.12.13/torch 2.14.0+cu126/CUDA 12.6/RTX 4090/driver 596.49; existing locked
CUDA+eval environment, offline and no-sync. No new milestone or real-data work.

**SAFE AFTER SPECIFIC FIXES:** the pilot fixes are now implemented and verified.
Stat caps gate expensive order/hash resolution; worker/queue preserve
EVALUATION_INCOMPLETE and refuse false success; final wall completion rejects
success arriving after its allowance. The synthetic evidence script is portable
to Windows. Keep the pilot receipt disabled. Scientific recovery design and
bounded runtime paths are supported; full-size performance is NOT RUN.

- Primary readiness: **112 passed**, including all **17** formerly unrun Torch
  nodes, no skips. Affected M1–M5: **232 passed**. Comparison/evidence:
  **252 passed, 1 failed** (unchanged P17 CRLF golden-byte test; Git blobs proven
  unchanged). Public workflow: **3 passed** on repaired code. Serial CUDA toy:
  **1 passed** before repairs and **1 passed** after, 24 complementary deselections
  each. Focused repairs/additional adversaries: **87 passed**, no skips.
- Independent A1–A8: retry persistence, endpoint durability, PARTIAL retention,
  shifted provenance, attention distinction, LR alignment and queue fail-stop
  covered. **A7 survives**: valid LR/chain history can exceed checkpoint/data C
  on load. Other reproduced pre-study gaps: detached producer-consumer tensors,
  receipt commit failure, evaluator chain mutation, duplicate compact aliases.
  Changed-policy fork receipt validation remains source-reviewed, not certified.
- Actual process producer equals direct Torch payload; fresh-process resume with
  dropout and partial final update equals uninterrupted rows/head/LR. These stock
  path successes do not close the adversarial gaps. M4 v2 formal study BLOCKED.
- Independent nominal capacity: **5 scientific + 2 terminal + 2 transient = 9P**,
  plus separate bounded overhead. Repeated deletion faults require storage fail-stop.
- Tiny B8 CUDA receipt spot: **163,840 targets**, **6.781 s**, **96 KiB** runtime
  files; **0.3771 ms/update** receipt CPU, **−0.493%** observed throughput delta,
  **0 bytes** peak GPU delta, same one explicit stream sync/update. This is a
  **6,768-parameter authored diagnostic**, not 50M pilot performance qualification.
- Ruff check/format **39 files** clean; mypy **23 source files** clean; dependency
  trio unchanged. Full repository acceptance audit NOT RUN. CPU/CUDA install
  policy unchanged. No network, downloads, install, live data, margins, pilot,
  campaign, push or merge.

Evidence and exact commands:
[P35-READINESS-ASTRA](evidence/P35-READINESS-ASTRA/COMMANDS.md),
[pre-test review](evidence/P35-READINESS-ASTRA/PRETEST.md),
[findings](evidence/P35-READINESS-ASTRA/REVIEW.md), and
[report closeout](reports/P35-PILOT-READINESS.md#astra-independent-review--local-cuda-certification).
The historical cloud sections above are retained as history.

Next operator command: **`git show --stat HEAD`** to review the local certification
commit for integration, with pilot receipt still disabled. Real pilot launch remains
blocked on separately authorized real bindings, measured profile/checkpoint capacity,
frozen plan and matching ticket. Formal microbatch work first requires the documented
receipt repairs and recertification. No next milestone is started here.
