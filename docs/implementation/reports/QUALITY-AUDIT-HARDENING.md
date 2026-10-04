# Global quality audit Phase A: hardening against I01–I14

Date: 2026-10-04. Branch `feat/global-quality-audit`, worktree
`F:/Project/xlm-quality-audit`. Starting HEAD `824b23e` (the independent audit
`626d569` of implementation `96f38f3`, plus a docs commit). This hardening is one
commit on top of `824b23e`.

**Verdict: all fourteen findings are repaired, and every one of the 21 independent
counterexamples now passes UNCHANGED. Ready for independent re-audit. The real audit
was NOT run.** All evidence uses authored offline fixtures. There was:

- no network access;
- no access to the real `G:` corpus or to `X:`;
- no real C05, tokenizer fit or training;
- no cleaning or transformation;
- no push.

The historical audit files
`docs/implementation/reports/QUALITY-AUDIT-INDEPENDENT-96F38F3.md` and
`docs/implementation/evidence/QUALITY-AUDIT-INDEPENDENT-96F38F3/` are byte-identical
to `824b23e` (`git diff --exit-code` exit 0).

**C05 disclosure.** The existing overlay tests use the `c05_flow` fixture, which
runs the AUTHORED synthetic C05 pipeline (`scripts/c05_synthetic_flow`) on
generated text with an authored key. It is an offline fixture, not a real or
protected C05 run. Astra's two overlay probes consume Astra's retained public
authored fixture at `F:/qa-tmp-96f38f3/quality-c050/root` and never regenerate it.

## Fixes

| ID | Repair | Pinned by |
|---|---|---|
| I01 | Source identity is cryptographic, never mtime (stat is no longer recorded or compared). (1) The bytes measured are the bytes hashed while being read (SHA, size and rows must match before the final task exists). (2) A **bracketing** full re-hash (`scan.verify_source`, SHA-256 + size + rows) starts only after the file's last measurement returns and must match before its unit commits; it runs in two background threads overlapped with the CPU-bound scan. (3) Resume re-hashes every committed source before reuse. (4) `report` re-hashes every source. (5) `materialize-review` re-hashes every selected source. | `test_i01_*`, `test_resume_rehashes_committed_files_and_ignores_mtime` |
| I02 | New `outputs.OutputTree`. The binding is the ownership marker, and a non-empty output without it refuses. Unknown files refuse and are never deleted; only exact job-owned staging names (`fNNNNN.unit.zz.tmp`, `<artifact>.tmp`) that are regular files are removed. Every path component from the root to each write target is `lstat`-checked for symlinks, junctions and reparse points, and the resolved parent must equal the expected location. All inputs are protected before any directory is created: manifest, proof, plan, trust, completion, C05 scratch and protected root, and every corpus file directory. | `test_i02_*` |
| I03 | `materialize-review` follows a fixed order of trust: strict COMPLETE receipt → exact current binding (code, policy, manifest, overlay) → artifacts re-derived from units → review-manifest SHA, bytes and record count from the receipt → exact row schema → each file is in the frozen manifest → full re-hash of every selected source, proving each `(offset, row)` locator → each row's `row_sha256` and `doc_id_sha256`. Output is streamed under `--max-output-mib`. It runs under its own supervisor (deadline, RSS, free space). The destination must be new and unaliased, and outside the repository, data root, audit output, inputs and protected roots. | `test_materialize_*`, `test_i03_*`, `test_i02_aliased_review_destination_refuses` |
| I04 | `runner.Guard` is one supervisor from CLI dispatch (`started` is taken in `main` before argument handling) through prepare/overlay, resume re-hash, scan, aggregation, artifact writes and receipt publication. It enforces the absolute deadline, the whole process-tree RSS and the free-space reserve. Every write is precharged against the output ceiling, and the binding write cannot exceed it. The in-process (`workers=1`) pool checks after every task. Publication passes `Guard.gate` (no failure and a deadline margin); a failure recorded during publication withdraws the receipt. `report` and `materialize-review` are supervised. Manifest, binding, receipt, unit and review reads are bounded, and unit decompression is bounded. | `test_i04_*`, Astra deadline/cap probes |
| I05 | Language numerics (`language_confidence` and each score) are dense 1001-bin histograms plus out-of-range, non-numeric and absent slots, with no label cap. Categorical maps are exact and order independent, with a hard limit decided by the final union. Artifacts publish a deterministic top 64 by (docs desc, value asc) plus an exact remainder. | `test_i05_*`, `test_population_merge_order_independence_with_language` |
| I06 | `detectors._runs` now does one vectorized pass over adjacent code-point differences (`maximal_runs`), O(n), with outputs identical to the old regex oracle on 333 adversarial texts. The class cache is bounded at 65,536 entries; the per-code-point regex cache was removed. | `test_i06_*` |
| I07 | No source-derived free string reaches any output. The `language` field and LID labels pass only if they look like language codes; split and document kind are allowlisted; provenance is mapped by the SHA-256 of the exact adapter literal to 7 categories (an AST test covers every adapter literal); everything else becomes `<unrecognized>`. Unknown language-like metadata keys are counted, never named. Review rows carry `doc_id_sha256` and `row_sha256`, never the raw `doc_id`. Canaries in text, doc_id, language, label, provenance, kind, key name, value and score are absent from every output byte, including decompressed units. | `test_i07_*` |
| I08 | Only `<!DOCTYPE html>` or HTML document elements make `markup_full_html`. A non-HTML doctype or an XML declaration gives `markup_xml` + `has_other_doctype`. Structure is judged outside fenced code examples (``` / ~~~); tags inside fences count as `fenced_tags` with the `markup_fenced_example` flag. | `test_i08_markup_semantics` |
| I09 | Ratio bins sit on exact float edges `k/1000` (`bisect`/`searchsorted`), so `value >= k/1000` is exactly bin ≥ k. Candidate rules carry an explicit `comparator`, `cut`, `cut_bin` and `quantile_bin`: `>= lo(c)` for the high tail (never the clean bin 0) and `< hi(c)` for the low tail (never the exact-1.0 bin). Every published rule's impact equals the number of documents that independently satisfy the printed comparator. | `test_i09_*` |
| I10 | Receipt schema 2, self-digested, with an exact field set, kind, version, `status` COMPLETE, `phase` COMPLETE and `audit_phase`. It contains the binding and its digest, manifest identity (and path), detector and implementation identity, overlay, source identities, artifact bytes/SHA/records and the result digest. The effective envelope covers workers, queue, RSS, reserve, output, document ceiling, deadline, chunk, verify threads, pending commits, supervisor interval, publication margin and review limit. The receipt also records every producer envelope. Aggregates explicitly say they are not a completion signal. | `test_i10_*` |
| I11 | The overlay keeps each kept row's doc_id BLAKE2b-128, C05 content digest (first 16 bytes, the same `canonical.digest` of the canonical row) and bytes, in dense per-file arrays filled in one pass (O(kept rows); no per-file masks). The membership schema, allocation fields versus the plan file, and per-allocation kept/train-byte/non-kept totals are reconciled with the signed completion. The scan worker refuses a mismatched doc_id, bytes or content. | `test_i11_*` |
| I12 | `boilerplate_lines` = distinct matching short lines; the new `boilerplate_phrase_hits` = every category match. | `test_i12_*` |
| I13 | Rows are parsed with `canonical.loads_bytes_strict` (strict UTF-8, duplicate keys, NaN/±Infinity refused), then the exact CanonicalDocument field set and types are checked. Oversized rows, manifests and units refuse. | `test_i13_*` |
| I14 | The key is renamed to `presence_ratio_ge_0_001`, with `comparator: ">="` and `cut: 0.001`. A boundary fixture at exactly 1/1000 is counted, and one at 1/1001 is not. | `test_i14_*` |

Also done:

- review ranks are keyed by seed + manifest digest (`review_key_sha256` in the binding);
- candidates remain PROPOSAL_ONLY with every `action` null;
- there is still no Phase-B executor;
- the detector policy is versioned `v2` because metric and flag semantics changed.

## Astra's 21 counterexamples (file unchanged, all PASS)

Command and JUnit evidence: [astra-probes-unchanged.xml](../evidence/QUALITY-AUDIT-HARDENING/astra-probes-unchanged.xml).
The run had **49 passed, 0 failed**, against Astra's 28/21. No probe needed an
equivalent rewrite. Each repaired mechanism is additionally pinned by
message-matching hardening probes, so no probe passes for an incidental reason:

| Astra probe | Now refused by | Pinned by |
|---|---|---|
| resume_rehashes_current_source | resume re-hash | `test_resume_rehashes_committed_files_and_ignores_mtime` |
| report_refuses_same_stat_mutation | report re-hash | `test_i01_report_rehashes_every_source` |
| source_mutation_after_hash_before_commit | bracketing re-hash | `test_i01_fresh_scan_rehashes_after_measurement` |
| deadline_covers_aggregation | Guard checks after aggregation plus the gate | `test_i04_publication_gate_withholds_complete` |
| one_worker_deadline_checked_after_last_task | inline post-task check | (Astra probe; deadline message) |
| binding_write_respects_output_cap | precharged writes | (Astra probe) |
| input_manifest_cannot_be_overwritten | input protection | `test_i02_inputs_inside_output_refuse_without_writing` |
| nested_units_junction_cannot_delete_source | ownership marker plus alias check | `test_i02_owned_units_junction_is_refused_before_cleanup` |
| materialize_checks_review_manifest_hash | artifact re-derivation plus receipt hash | `test_materialize_refuses_any_review_manifest_change` |
| materialize_rehashes_source | selected-source re-hash | `test_materialize_rehashes_selected_sources` |
| forged_receipt_cannot_read_unaudited_file | strict schema plus re-derivation | `test_i03_forged_but_well_formed_receipt_refuses`, `test_i10_*` |
| source_metadata_is_not_exported_as_snippets | vocabulary mapping | `test_i07_*` |
| candidate_saturated_ratio_matches_advertised_cut | exact comparator | `test_i09_*` |
| receipt_resource_envelope_is_recorded | envelope | `test_i10_receipt_self_digest_and_envelope` |
| report_refuses_noncomplete_receipt | strict schema | `test_i10_receipt_schema_is_strict` |
| language_many_scores_remain_bounded_and_mergeable | dense numeric bins | `test_i05_*` |
| policy_xml_code_not_strong_html | doctype name and fences | `test_i08_markup_semantics` |
| noncanonical_json_refuses[duplicate_key, nonfinite] | strict parser | `test_i13_noncanonical_rows_refuse` |
| signed_overlay_row_identity_mismatch_refuses | kept-row identity | `test_i11_signed_wrong_doc_id_refuses` |
| boilerplate_lines_counts_lines | distinct-line count | `test_i12_*` |

## Tests and static checks

All runs used `OMP/MKL/OPENBLAS/NUMEXPR=1`, `TOKENIZERS_PARALLELISM=false` and
`PYTHONDONTWRITEBYTECODE=1`, with short `--basetemp` roots under `F:/qrep-*`.

| Selection | Result |
|---|---|
| Astra probes, unchanged (`-n 0`) | 49 passed, exit 0 |
| Quality detectors + audit + hardening, parallel selection (`-n 8 --dist=worksteal`, `-m "not serial_exclusive"`) | 142 passed, exit 0 |
| Serial selection (`-n 0 -m serial_exclusive`: linear-run timing) | 1 passed, exit 0 |
| `ruff format --check` (19 files) | exit 0 |
| `ruff check` (19 files) | exit 0 |
| `mypy --strict` (18 source files) | exit 0 |
| `git diff --check` (new files intent-added) | exit 0 |

The worker-determinism check (workers 1/2/4/8 with 4 KiB chunks, byte-identical
artifacts) is in the 142, together with:

- resume and mutation adversaries;
- output/junction safety;
- overlay identity;
- materialization security;
- receipt semantics;
- privacy canaries;
- resource/deadline enforcement.

The full repository suite was **NOT RUN**: only the quality package and its tests
changed, and this is not a release gate.

## Performance (authored; not real-corpus measurements)

Evidence: [authored-benchmark.json](../evidence/QUALITY-AUDIT-HARDENING/authored-benchmark.json),
[measurements.json](../evidence/QUALITY-AUDIT-HARDENING/measurements.json),
[kernel-profile.txt](../evidence/QUALITY-AUDIT-HARDENING/kernel-profile.txt).

The benchmark corpus was 268,480,026 bytes, 47,015 documents and 8 files.
Machine: Windows 11, 16 logical and 8 physical cores. All four runs produced the
single result digest `c1f0bb77…`.

| workers | MB/s | docs/s | peak tree RSS | before hardening |
|---:|---:|---:|---:|---:|
| 1 | 6.0 | 1,052 | 0.16 GiB | 6.6 MB/s |
| 2 | 10.2 | 1,790 | 0.41 GiB | (not run) |
| 4 | 18.8 | 3,292 | 0.74 GiB | 20.2 MB/s |
| 8 | 30.1 | 5,279 | 1.33 GiB | 32.9 MB/s |

The additional safety costs about 8%: strict parsing, two per-row SHA-256s, kept
identity checks and the bracketing re-hash.

**Linear run detector, on Astra's adversary** (N distinct code points, each
repeated 8 times):

| UTF-8 size | Before | After |
|---:|---:|---:|
| 192 KB | 4.596 s | 0.0019 s |
| 768 KB | — | 0.0082 s |

From 24 KB to 768 KB the cost doubles with each doubling of the input.

**Profile** of one 1,500-document chunk (5.69 MB):

- `analyze` is about 77% of the time;
- strict parsing and schema checking about 8%;
- runs about 6%;
- kept-identity verification has no measurable cost (ratio 0.99).

**Overlay staging** runs at 79k rows/s with strict parsing, about 12.7 s per million
kept rows. The single pass indexes each row's own file array, which is linear.

## Real-audit projection (PROJECTION, not a measurement)

For 104,506,534,003 file bytes and 15,097,174 documents:

| Scenario | Rate assumption | Scan |
|---|---:|---:|
| Optimistic | 30.1 MB/s (authored, 8 workers) | 0.96 h |
| Likely | 15–25 MB/s (between Astra's short independent run and the authored rate) | 1.2–1.9 h |
| Conservative | 6 MB/s (adverse mix) | 4.8 h |

Add the following:

- **Overlay staging:** at most about 3.2 min for ≤ 15.1 M kept rows, plus hashing
  `membership.jsonl`.
- **Aggregation:** about 0.5 min. This was measured at 22 s for 2,035 units before
  hardening and not re-measured.
- **Bracketing re-hash:** it reads every byte a second time (about 209 GB read in
  total). It runs concurrently in two threads. With the scan CPU-bound at about
  30 MB/s, the assumed (unmeasured) 0.5 GB/s `G:` read rate should not bind.
- **`report`:** its full re-hash is a separate full read, about 3.5 min at the
  assumed 0.5 GB/s plus derivation.
- **Resume:** it re-hashes the committed files.

The planning allowance is about 1.5–2.5 h for the audit. The 12 h deadline covers
the conservative case.

## Operator commands (after an independent re-audit passes)

```powershell
uv run --offline --locked --no-sync --extra cpu --extra eval python -m xlm.data.quality audit --manifest <"manifest" path inside G:/XLM/c05/p0002.proof.json> --output G:/XLM/quality/audit-v2 --c05-proof G:/XLM/c05/p0002.proof.json --workers 8 --max-rss-gib 12 --free-reserve-gib 8 --max-output-gib 4 --max-document-mib 64 --deadline-hours 12
uv run --offline --locked --no-sync --extra cpu --extra eval python -m xlm.data.quality report --manifest <same manifest> --output G:/XLM/quality/audit-v2 --c05-proof G:/XLM/c05/p0002.proof.json --workers 4 --max-rss-gib 8 --deadline-hours 6
```

Prerequisites: `X:` detached, and the trust-key variables of the proof set as for
C06. The output must be new: an old v1 directory is owned by a different binding
and refuses. The expected artifacts under `G:/XLM/quality/audit-v2/` are:

- `quality-audit.json`
- `quality-by-component.json`
- `quality-histograms.json`
- `quality-intersections.json`
- `quality-language.json`
- `review-manifest.jsonl`
- `candidate-policy-{conservative,moderate,aggressive}.yaml`
- `quality-summary.md`
- `quality-audit-receipt.json` (schema 2)
- `audit-binding.json`
- `units/f00000.unit.zz` to `units/f02034.unit.zz`

## Remaining limitations

- **Hashing races.** Re-hashing cannot detect a mutation that is made and fully
  reverted between the two hashes, or a change after the final check. `report`
  re-hashes later. Path checks versus writes are not atomic against a concurrent
  same-user adversary. This is operational protection, not a security boundary.
- **No signature.** The receipt is self-digested but not signed. Trust rests on the
  operator-supplied manifest, the current code identity, re-derivation and re-hashing.
- **Supervisor reach.** With `workers=1`, a single in-process document cannot be
  interrupted mid-analysis; the check runs after each task. With more workers, the
  workers are killed on failure.
- **Language codes.** These are structurally bounded (code-shaped values), not a
  semantic allowlist; a short code-shaped source value is published as a category.
- **Fences.** Only fenced ``` / ~~~ examples are recognized; indented code and
  `<pre>` examples are not.
- **Detector heuristics.** These are unchanged except for I06/I08/I12: shell, CSV
  and bibliography class misses, the context-insensitive newsletter phrase, and
  Roman-numeral page-number false positives remain review subjects.
- **Overlay memory.** Overlay identity arrays add about 0.6 GiB parent RSS for 15.1 M
  rows. This is estimated, not measured on real data.
- **Not re-measured after hardening:** aggregation at 2,035 units and peak RSS at
  real scale.
- **Leftover scratch.** Authored scratch directories remain for the operator to
  remove; deletion was not attempted after a safety refusal:
  - `F:/qrep-probes`, `F:/qrep-own`, `F:/qrep-hard`, `F:/qrep-serial`,
    `F:/qrep-astra`, `F:/qrep-fast`;
  - `F:/qrep-bench-256`, an incomplete benchmark root from a failed first attempt;
  - `F:/qrep-bench-256b`.

## Requirement ledger

| Requirement | Status |
|---|---|
| I01–I14 repairs | IMPLEMENTED, VERIFIED (authored fixtures) |
| Astra's 21 counterexamples pass unchanged | VERIFIED |
| Phase A remains read-only measurement; no Phase-B executor; candidates PROPOSAL_ONLY | VERIFIED |
| Worker determinism 1/2/4/8 | VERIFIED (fixtures and 256 MiB authored benchmark) |
| Linear run detector with scaling evidence | VERIFIED (authored) |
| File-grouped overlay | IMPLEMENTED; staging rate measured (authored) |
| Real-corpus audit, report, review | NOT RUN (operator, after re-audit) |
| Full repository suite | NOT RUN |
| Cleaning, new C05, tokenizer, training | OUT OF SCOPE |

Next prompt: **independent re-audit of the hardening commit on
`feat/global-quality-audit`, using the unchanged probes at
`docs/implementation/evidence/QUALITY-AUDIT-INDEPENDENT-96F38F3/test_independent.py`
plus fresh adversaries. Do not run the real audit before it passes.**
