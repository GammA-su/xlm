# Phase C: production global quality cleaner v1 (content-free)

Date: 2026-10-04. Branch `feat/quality-cleaner-v1`, based on `c9de734` (operator's frozen
`cleaning_policy_v2`).

What this adds:

- three commands: `clean-production`, `clean-production-verify` and
  `clean-production-manifest`;
- four modules: `xlm.data.quality.production`, `production_approval`,
  `production_paths` and `production_report`;
- two small hooks in existing code:
  - a `record_drops` mode of the existing Phase-B worker kernel;
  - two progress phases with an optional suffix.

It also registers the cleaned-manifest kinds in the input-manifest loader.

No real corpus, `G:`, `X:`, C05, tokenizer or training was touched. Everything below
was verified on authored fixtures. The production run, its verification and the
cleaned manifest are the operator's.

## Semantics

DROP-only. For each input row the frozen v2 policy decides KEEP or DROP:

- KEEP: the original line bytes are written unchanged and in order;
- DROP: the row is omitted.

The worker kernel is the Phase-B dry-run kernel (`measure_clean_chunk`), so the
decision code path is identical by construction. In production mode it skips review
sampling and returns each DROP row's chunk-local byte span plus content-free identity
(row, offset, doc_id SHA-256, row SHA-256, rule mask, canonical bytes). The parent
writes the very bytes it read and hashed, minus those spans, so no JSON is
reserialized. Evaluation and writing are one pass.

## Binding to the approved dry run

`production_approval.verify_approved_dry_run` re-verifies everything and trusts no
given number:

- the strict receipt, which must equal the directory binding;
- COMPLETE status and `POLICY_WITHIN_GUARDRAILS`;
- the result digest, which must equal `--approved-result-digest`;
- the policy (version v2, digest, file SHA-256, Phase-A and candidate provenance);
- the detector semantics;
- the manifest (digest, file SHA-256, kind, mode, totals), the data root and every
  source identity;
- every artifact hash;
- every unit, loaded against its manifest record and producer envelope;
- every artifact, re-derived from the units byte for byte.

It returns each file's merged dry-run statistics (overlay populations merged) and the
published global/component/rule accounting. Production refuses before a file's output
is published unless that file's complete statistics equal the dry run's. It refuses
before the receipt unless the global, component, by-component, rule and guardrail
accounting are identical. The dry run's code identity is recorded but not required to
equal the current code: the cleaner is new code, so decision equality is proven per
file instead.

## Requirement ledger

| Requirement | Status | Evidence |
|---|---|---|
| DROP-only; KEEP rows are raw input bytes, order kept, no transform/reserialization | IMPLEMENTED, VERIFIED (fixtures) | `test_keep_rows_are_original_bytes_in_order_and_drops_absent`, `test_no_transformations_of_unusual_text` (independent oracle; last row without newline preserved) |
| Required inputs `--manifest --policy --approved-dry-run --output-root --state-output` (plus `--approved-result-digest`) | IMPLEMENTED, VERIFIED | `test_cli_commands_and_progress` |
| Strict pre-write checks: frozen policy, v2, manifest, receipt, COMPLETE, within guardrails, result digest, policy/source binding, artifact hashes | IMPLEMENTED, VERIFIED | `test_stale_or_unapproved_dry_run_refuses`, `test_stale_policy_refuses`, `test_dry_run_of_another_manifest_refuses` |
| Independent re-evaluation of every row; exact match of global, component, rule-marginal and severe-histogram accounting; stronger per-file facts | IMPLEMENTED, VERIFIED | per file: `test_wrong_dry_run_accounting_is_fatal`; global: `test_global_accounting_mismatch_is_fatal`; dry run with C05 overlay: `test_overlay_dry_run_is_bound_without_the_proof` |
| Mismatch fatal, no completion receipt | IMPLEMENTED, VERIFIED | same two tests (no receipt, no staged receipt, no temporaries) |
| Deterministic collision-free mapping, no escape, stable across OS syntax, not drive-letter replacement | IMPLEMENTED, VERIFIED | `test_traversal_and_nonportable_paths_refuse` (13 cases), `test_mapping_is_one_to_one_and_stable` (case and NFC collisions, file/dir conflict) |
| Safety refusals: overlaps, junction/reparse, unrelated data, stale inputs, resource limits, disk reserve | IMPLEMENTED, VERIFIED | `test_overlap_and_destination_refusals` (7 overlap cases + pre-existing data never touched), `test_junction_and_reparse_protection` (real Windows junctions), `test_stale_source_refuses`, `test_resource_limits_refuse` |
| Never modify an input, never overwrite an existing output | IMPLEMENTED, VERIFIED | `reference` fixture (source hashes unchanged), `test_orphan_with_different_bytes_refuses`, the exclusive temporary create, publish refuses on an existing final |
| Per-file atomic publication (temp -> fsync/close -> verify -> rename) and complete unit records | IMPLEMENTED, VERIFIED | interruption tests below; unit schema checked on every load |
| Interruption at every publication point; resume; interrupted temporaries never count | IMPLEMENTED, VERIFIED | before rename (`test_interrupt_before_publication_then_resume`, planted garbage temporaries), after rename before unit (`test_interrupt_after_rename_before_unit_then_adopt`), after all units / between receipt staging and publication (`test_interrupt_after_units_and_before_receipt_publication`) |
| Source/policy/code/output mutation invalidates units | IMPLEMENTED, VERIFIED | `test_stale_source_refuses`, `test_resume_refuses_changed_or_missing_output`, `test_resume_refuses_a_different_binding` (code identity changed) |
| `clean-production-verify` (exact output set, SHA-256, strict rows, counts, mapping, dropped rows absent, totals, approved accounting; read-only) | IMPLEMENTED, VERIFIED | `test_verify_full_and_manifest_builder` (`--compare-sources --reevaluate`, corpus bytes unchanged), `test_verifier_catches_corruption`, `test_output_check_finds_dropped_rows_and_order`, `test_verifier_refuses_tampered_receipt` |
| Content-free deterministic `dropped-membership.jsonl` | IMPLEMENTED, VERIFIED | `test_receipt_and_artifacts`, `test_worker_and_rerun_determinism`, resume tests (byte-identical) |
| Cleaned inventory and cleaned manifest (verified-only, new digest) | IMPLEMENTED, VERIFIED | `test_verify_full_and_manifest_builder` (refuses before verification; reloads via `load_manifest`; digest differs) |
| Receipt binds manifest, policy, approved dry run, code, all sources, all outputs, membership, inventory, exact accounting | IMPLEMENTED, VERIFIED | `validate_receipt`; `test_receipt_and_artifacts` |
| Worker determinism 1/2/4 | VERIFIED | `test_worker_and_rerun_determinism` (corpus and all six artifacts byte-identical) |
| No document text in receipts/evidence | VERIFIED | `test_no_document_text_in_receipts_or_evidence` (all state files incl. decompressed units; canaries and every doc_id) |
| Progress every ~5 s with phase, files, docs, input/output GB, DROP docs, throughput, ETA, workers active, in flight, CPU, RSS, free disk | IMPLEMENTED, VERIFIED | `test_cli_commands_and_progress` |
| Real production cleaning, verification and cleaned manifest on `G:` | NOT RUN | operator only |
| C05 rerun, tokenizer, training | OUT OF SCOPE | next operator stage |
| Native-Linux run | NOT RUN | Windows only this session |

## Tests and checks (native Windows 11, CPython 3.12, locked `cpu`+`eval` environment)

Exact commands, exit statuses and logs: [evidence](../evidence/QUALITY-CLEANER-V1/COMMANDS.md).

- `tests/test_quality_cleaning_production.py`: 41 passed. All authored fixtures; real
  Windows junctions, no skip.
- Production plus every Phase-A/B quality suite under one xdist controller (8 workers,
  `-m "not serial_exclusive"`): 531 passed (including the 41 production tests), 7
  skipped (POSIX `/proc` fsync-path inspection), 1 failed. The failure,
  `test_quality_performance.py::test_status_discovers_a_running_audit_process_tree`,
  is pre-existing: it fails identically, run alone, on the unmodified base `c9de734` in
  a separate worktree. On native Windows a venv `python.exe` is a launcher, so the
  discovered interpreter PID differs from the launched PID. It is not a regression,
  and it is not a pass.
- Serial selection (`-m serial_exclusive`, `-n 0`): 1 passed.
- `ruff format --check`, `ruff check`, `mypy --strict` (package + cleaning tests),
  `git diff --check`: clean.

## Measured performance (authored data only)

`bench_production.py`: 805 MB synthetic corpus, 16 workers, on the operator-class
machine (16 logical CPUs). Other sessions may have shared the machine.

| step | wall s | file MB/s |
|---|---:|---:|
| Phase-A audit | 12.83 | 62.8 |
| Phase-B dry run | 12.25 | 65.8 |
| **Phase-C production** | 12.47 | **64.6** (clean phase 70.2) |
| verify `--compare-sources` | 3.41 | 236.5 (page cache warm) |

The synthetic corpus had no DROP under the genuine conservative cuts. The measured
path is the decision pass plus the full write, re-read and source re-hash.

Estimate for the real run: about 30 min for ~100 GB of input file bytes at the real
dry run's ~61.7 MB/s, plus 1-3 min of dry-run re-verification and aggregation. Up to
about 1 h if `G:` is IO-bound. Disk: the cleaned corpus is about the input file bytes
minus ~0.5 GB of dropped line bytes; the state is well under 1 GB.

## Open limitations

- `fsync_directory` is a documented no-op on Windows; rename durability rests on NTFS
  journaling. This is inherited from Phase A.
- The pre-existing Windows status-PID test failure above.
- The binding includes the absolute output root, so artifacts from runs into
  different roots differ in their binding-bound headers. Corpus bytes and membership
  rows do not differ.
