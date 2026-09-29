# Essential-Web evidence v3.0: Phase-P remediation record

2026-09-29. Checkout `F:\Project\xlm-data-ultrax`, branch
`data/mix01-ultrax-6b`, HEAD `7269832bf44d3c7556e991ea96cf5ad7c2748b4c`.
Frozen protocol commit `52569c525a6613faab096d17b60167b4aaa0f214`.

Independent verdict before this task: **PHASE-P AUTHORIZATION BLOCKED**
(scientific identity and dry-plan arithmetic passed; implementation
enforcement did not). The protocol/freeze were NOT redesigned: protocol
SHA-256 `c191aa49a7e35349e27eb077c7105ead1a15a7edbbb28ef0bc78f157b219fd79`,
freeze digest `aa977972883af209afffebae221683658b2637281ae8e72741150750534fd834`,
epoch `essential-web-evidence-v3.0:essential-web:epoch-0001`, namespace
`essential-web-evidence-v2.0`, selection
`975ba3dee4af0598e665ea05c69bbe49336c3e3a35fca777a863073190b78474`,
revision `ce4eccc7e9604667b6d7f32cb6274b8b41f3113d`, all caps and the
`G:/Project/xlm-evidence-v3/essential-web` root preserved exactly.

Each finding below records: finding, reproduced, root cause, fix,
regression test, resulting evidence. No contradiction in the normative
protocol/freeze was discovered, so no frozen artifact was edited.

## 1. Strict Phase-P authorization schema

- Finding: the validator accepted artifacts missing `phase`, with
  `reviewer_decision = BLOCKED`, or with wrong epoch/root bindings when the
  supplied digest matched.
- Reproduced: yes, by construction review of `verify_authorization` (only
  digest + optional phase-marker checked).
- Root cause: permissive schema — no required-field set, no unknown-field
  rejection, no binding checks beyond digest equality.
- Fix: new `src/xlm/data/evidence_v3/authz.py` with the exact 26-field
  schema (`kind`, `schema_version`, `protocol_sha256`, `freeze_digest`,
  `freeze_commit`, `implementation_commit`, `child_manifest_digest`,
  `epoch_id`, `execution_root`, `phase`, `arms`, `m_phase_p_plan_digest`,
  `t_phase_p_plan_digest`, `scientific_namespace`, `selection_digest`,
  `source_revision`, `resource_caps`, `resource_caps_digest`, `code_hashes`,
  `environment_identity`, `reviewer_decision`, `review_artifact_digest`,
  `operator_approval`, `authorization_scope`, `root_precondition`,
  `digest`); kind `essential-web-evidence-v3-phase-p-authorization`,
  schema 1, `phase = "P"`, `reviewer_decision = "APPROVED"` only,
  `authorization_scope = "phase-P-only"`,
  `root_precondition = "absent-or-pristine-empty"`, exact frozen bindings,
  caps equality plus cap-map digest, plan/commit/code/review bindings, and
  stale-review refusal. Self-digest excludes only `digest`. New CLI
  `check-auth` exposes the check offline.
- Regression tests: `test_auth_missing_phase_refuses`,
  `test_auth_blocked_decision_refuses`, `test_auth_wrong_root_refuses`,
  `test_auth_wrong_epoch_refuses`, `test_auth_wrong_implementation_refuses`,
  `test_auth_unknown_field_refuses`, `test_auth_wrong_plan_digest_refuses`,
  `test_auth_stale_review_refuses`, `test_auth_boolean_refuses`,
  `test_auth_missing_operator_approval_refuses`,
  `test_auth_valid_strict_schema_passes`.
- Evidence: `authz.py`; cap-map digest `e2485cd4…54e0f7b` independently
  recomputed from `freeze.json` arm_caps (`test_cap_digest_independently_reproduced`).

## 2. Operator approval distinct from reviewer approval

- Finding: reviewer approval could stand in for operator approval.
- Reproduced: yes — no operator-approval field existed at all.
- Root cause: single-approval model.
- Fix: `operator_approval` is a required embedded artifact
  (`essential-web-evidence-v3-operator-approval`, schema 1) binding
  `authorization_core_digest` (digest of the auth body minus `digest` and
  `operator_approval`, avoiding circularity), with operator identity, UTC
  timestamp, and an explicit phase-P statement. No `--yes`/`--force`/
  boolean/env bypass exists in the CLI (asserted by
  `test_no_cli_boolean_bypass_flags`). This task created no real approval.
- Regression tests: approval binding covered by the authorization suite
  above plus `test_auth_missing_operator_approval_refuses`.
- Evidence: `authz.validate_operator_approval`; genesis binds
  `operator_approval_digest`.

## 3. Exactly-once exclusive epoch genesis

- Finding: existence-check + replace is insufficient under concurrency.
- Reproduced: yes — two sequential publishers could interleave between
  check and write.
- Root cause: check-then-act without an atomic claim.
- Fix: `epoch.claim_genesis_exclusive` uses `O_CREAT|O_EXCL` on
  `genesis.claim` (Windows-safe): one winner, all others refuse; second
  genesis after restart refuses; no overwrite or replacement epoch. The
  strict `build_genesis_record` binds authorization digest, operator
  approval digest, protocol/freeze, implementation commit, child manifest,
  plan digests, execution root, initial inventory, initial zero ledgers,
  code/environment identity, resource caps, supervision identity, UTC start,
  and monotonic baseline. `publish_genesis_record` requires the claim.
  Real genesis never executed; synthetic temp-root probes only.
- Regression tests: `test_genesis_concurrent_exactly_once` (8 threads, one
  winner), `test_genesis_second_refuses_after_restart`,
  `test_genesis_crash_safe_no_partial`, `test_genesis_binds_auth_and_approval`,
  `test_genesis_binds_zero_ledgers_and_clocks`.
- Evidence: `epoch.py` claim/record functions; CLI synthetic probe
  succeeded and was cleaned.

## 4. Durable ledger: reserve before I/O

- Finding: save-zero → apply-one left the file unchanged; reload returned
  zero; pending data-category work reloaded as footer with zero elapsed.
- Reproduced: yes — `apply` mutated memory only; recovery hard-coded
  `kind="footer"`.
- Root cause: no write-through journal; category defaulted on recovery.
- Fix: `ledger.py` rewritten around an fsynced append-only journal
  (`journal.jsonl` + `snapshot.json`) with chained line digests. Event
  states RESERVED/ISSUED/RESPONSE_STARTED/COMPLETE/FAILED/REFUSED/
  CRASH_RESERVED; `reserve_event` charges requests and fsyncs RESERVED
  before any transport may issue; every metered delta fsyncs; category,
  file, phase, logical/physical attempt, redirect/retry counts, bytes, and
  elapsed persist per line and are never inferred. Reload replays the
  journal (source of truth), conserves open work as CRASH_RESERVED, and
  refuses truncation, corruption, epoch mismatch, or rollback behind the
  snapshot. `apply` fsyncs COMPLETE before returning.
- Regression tests: `test_ledger_apply_persists_before_return`,
  `test_ledger_reload_preserves_category_and_elapsed`,
  `test_ledger_crash_pending_conserves`,
  `test_ledger_conflicting_replay_refuses`,
  `test_ledger_truncated_journal_refuses`,
  `test_ledger_corrupt_journal_refuses`, `test_ledger_v2_import_refuses`.
- Evidence: `ledger.py` journal implementation.

## 5. Integrated Phase-P executor

- Finding: no execution path connected authorization, genesis, transport,
  ledger, memory, disk, runtime, identity verification, and PhaseGate.
- Reproduced: yes — only disconnected helpers existed.
- Root cause: executor never built.
- Fix: new `src/xlm/data/evidence_v3/executor.py` (`PhasePExecutor`) is the
  only supported Phase-P path. It gates on valid epoch record, strict
  authorization + operator approval, `P_AUTHORIZED` gate, root inventory,
  live ledgers, memory/disk/runtime checks, then runs reserve → supervise →
  meter → validate → settle → stage per operation. Never executed live;
  offline tests use `FakeTransport` on synthetic roots.
- Regression tests: `test_executor_refuses_without_epoch/auth/approval`,
  `test_executor_cannot_bypass_ledger/guards`,
  `test_executor_no_t_text_in_p`, `test_executor_no_m_data_in_p`,
  `test_executor_identity_mismatch_stops`.
- Evidence: `executor.py`.

## 6. Strict transport policy

- Finding: host validation accepted HTTP loopback, IP literals,
  raw.githubusercontent.com, arbitrary `*.hf.co`, and HTTPS port 444.
- Reproduced: yes by probe against the old check.
- Root cause: suffix/prefix matching instead of exact allowlist.
- Fix: new `src/xlm/data/evidence_v3/transport.py`: HTTPS only, port 443
  only, exact hosts `huggingface.co` / `cas-bridge.xethub.hf.co`, no IP
  literals, no localhost/loopback, no userinfo, every redirect hop
  revalidated, at most 3 transitions per logical attempt.
- Regression tests: eight policy tests from `*_refuses` through
  `test_transport_exact_allowed_hosts_pass` and hop revalidation.
- Evidence: `transport.py`; `check_frozen_hosts_match_protocol`.

## 7. Response/error body accounting

- Finding: invalid-status rejection charged zero body bytes.
- Reproduced: yes — metering happened only after successful parse.
- Fix: executor meters `len(body)` immediately after fetch
  (`mark_response_started` deltas), including redirect-hop bodies,
  before identity validation; failures call `fail_event` with observed
  bytes; failed/partial reads conserve via `ExecutorError ... from exc`.
  Uncertain reads retain a zero-delta conservation record.
- Regression tests: `test_body_invalid_status_counted` (503 + 100 B),
  `test_body_redirect_counted` (4 + 50 B, 2 requests),
  `test_body_partial_counted`, `test_body_failed_retry_counted` (20 B).
- Evidence: `executor._run_one` + `ledger.mark_response_started`.

## 8. Exact remote identity

- Finding: no integrated identity gate existed.
- Reproduced: n/a (missing path, see 5).
- Root cause: same as 5.
- Fix: `verify_remote_identity` enforces HTTP 206, exact Content-Range,
  exact frozen length, strong ETag, immutable revision/path containment,
  and PAR1 where required. Any mismatch is STOP: gate marked INCOMPLETE,
  no reselection, no replacement discovery, no continuation to D. M
  file/window identity frozen; T Phase P accepts footer/control only.
- Regression tests: `test_executor_identity_mismatch_stops` (ETag case;
  gate left INCOMPLETE).
- Evidence: `executor.verify_remote_identity`.

## 9. Memory enforcement, not just measurement

- Finding: `FailClosedSupervisor` was a callable helper; caller-supplied
  `terminated=True` counted as proof.
- Reproduced: yes — executor never consulted the supervisor.
- Root cause: no authoritative lifecycle, no enforcement wrapper.
- Fix: `OwnedProcessRegistry` with nonce-bound `ExitProof` minted only by
  `reap` after a liveness probe reports exit; booleans and forged proofs
  refuse. Supervisor accepts a registry for required PIDs and
  `enforce_around` wraps work with pre/post plus periodic monitor hooks;
  the executor checks memory before, during (per operation), and after
  each operation against the 256 MiB cap. Parser/allocation reservations
  qualified via `check_parser_reservation` + `allocator_reservation`.
- Regression tests: `test_memory_parent_child_and_dedup`,
  `test_memory_missing_owned_pid_refuses`,
  `test_memory_unreadable_refuses`, `test_memory_cap_exceed_refuses`,
  `test_memory_no_boolean_bypass`,
  `test_memory_registry_reap_release_cycle`.
- Evidence: `guards.OwnedProcessRegistry`, `enforce_around`.

## 10. Disk containment and accounting

- Finding: (A) 1-byte reservation + 256-byte write reconciled fits=true;
  (B) replace under-counted old+temp peak (8 vs 16); (C) `../outside.bin`
  escaped the root.
- Reproduced: all three, yes.
- Root cause: no reservation-vs-actual check, logical-only peak, no path
  containment.
- Fix: `_resolve_inside` rejects `..`, absolute/external paths,
  symlink/reparse and ancestor redirection, and alternate volumes.
  `write_file` refuses when actual bytes exceed the reservation and marks
  the object incomplete; `reconcile` re-checks actuals vs reservations.
  `atomic_replace` accounts old+temp simultaneously into
  `peak_physical`, tracked from real filesystem walks. Inventory persists
  (`save_state`/`load_state`); unknown files STOP; release follows verified
  deletion; imports are allowlisted, hash-verified, disk-counted.
- Regression tests: `test_disk_reserve1_write256_refuses` (incl. external
  bypass via reconcile), `test_disk_old_temp_peak_counted` (peak ≥ 16),
  `test_disk_dotdot_escape_refuses`, `test_disk_absolute_escape_refuses`,
  `test_disk_symlink_escape_refuses` (skipped only where the volume
  forbids symlinks), `test_disk_unknown_file_refuses`,
  `test_disk_release_after_deletion_only`,
  `test_disk_import_allowlisted_hash_counted`.
- Evidence: `guards.PhysicalDiskInventory`.

## 11. Runtime accounting

- Finding: 31 s open segment still reported 30 s budget; `end_segment`
  refusal cleared the anchor with elapsed left at 0; pause succeeded
  mid-segment.
- Reproduced: yes.
- Root cause: budget ignored open-segment elapsed; anchor cleared before
  charging; pause lacked a sealed-state requirement.
- Fix: `request_budget` includes open-segment elapsed and returns zero
  once the per-request allowance is consumed; `end_segment` charges first
  and keeps the anchor on refusal with prior elapsed retained;
  open-segment state (`open_file`, anchor) persists and
  `conserve_restart_open_segment` charges it at restart; `seal_pause`
  requires explicit `sealed_quiescent=True` and refuses with any open
  segment; earliest request/file/arm deadline wins with immediate
  zero/refusal when over.
- Regression tests: `test_runtime_open31s_zero_budget_and_retained`,
  `test_runtime_crash_preserves_conservative_elapsed`,
  `test_runtime_pause_during_open_refuses`,
  `test_runtime_sealed_pause_works`, `test_runtime_earliest_wins`.
- Evidence: `guards.ActiveRuntime`.

## 12. Derived child readiness

- Finding: readiness booleans were assigned directly by the builder.
- Reproduced: yes — literal `True` map in `build-v3-dry-plans`.
- Root cause: no derivation.
- Fix: new `src/xlm/data/evidence_v3/readiness.py`;
  `derive_arm_readiness` evaluates one evidence check per item
  (scientific/lineage/epoch/adoption/schedules/requests/bytes/
  decompression/scan/memory/disk/runtime, the last three via live
  mechanism self-tests) and returns BLOCKED with the failed list unless
  every check passes. `authorization` stays `NONE`. The builder fails
  closed when any arm is not ready.
- Regression tests: `test_readiness_derived_false_when_broken`,
  `test_readiness_no_hardcoded_true` (source assertion).
- Evidence: `readiness.py`; regenerated `readiness_review.json` carries
  per-item evidence strings.

## 13. Code/environment byte identity

- Finding: `.python-version`, `pyproject.toml`, `uv.lock` differed between
  checkout and Git blobs only by line endings; environment bound only
  versions + lock hash.
- Reproduced: yes (CRLF checkout vs LF blobs).
- Root cause: working-tree bytes used as identity.
- Fix: canonical identity is Git blob bytes via `git hash-object`
  (`envidentity.code_blob_hashes`); working-tree SHA-256 plus
  `core.autocrlf` recorded separately as checkout representation, never
  silently normalized. Environment identity now binds Python version,
  executable class, uv version, the three blob hashes, pyarrow/psutil/
  torch versions, CPU/CUDA build, OS/build, and architecture, with a
  documented non-claim beyond what is bound. Tool resolution falls back
  to well-known install locations when PATH lacks `git`/`uv`.
- Regression tests: `test_env_identity_git_blob_method`,
  `test_no_cli_boolean_bypass_flags`, `test_frozen_identities_unchanged`.
- Evidence: `envidentity.py`; regenerated producer blocks carry
  `code_identity_method: git-blob-bytes-authoritative`.

## 14. Phase-P dry-plan arithmetic (preserved + corrected detail)

- Finding: none in arithmetic itself; Astra supplied the P-capacity
  correction to publish.
- Reproduced: n/a — recomputation confirms the frozen numbers.
- Root cause: n/a.
- Fix: no numeric change. New `schedules.m_phase_p_accounting` /
  `t_phase_p_accounting` publish the corrected detail: M direct P logical
  16, observed-style physical 24, cold P no-retry bound 40, future D
  identity allocation 32, total controls 72 ≤ 80; with 32 reserved for D,
  P arm capacity is 48, so cold P = 40 leaves only 8 additional P requests
  while preserving the D allocation. M Phase-P payload 1,398,416 B;
  footer remaining before redirect/error/retry bodies 32,156,016 B;
  minimum per-file footer headroom 3,991,024 B. T direct P logical 24,
  observed-style physical 32, cold P no-retry bound 48, future D identity
  allocation 32, combined controls 80; T footer consumption depends on
  observed L during P — no headroom fabricated before measurement.
- Regression tests: `test_m_phase_p_accounting_exact`,
  `test_t_phase_p_accounting_exact`.
- Evidence: `schedules.py` accounting functions; §17–18 of the final
  response.

## 15. G: execution root

- Finding: review observed G: accessible (~999.7 GB free) with
  `G:\Project` and the execution root absent — acceptable pre-genesis.
- Reproduced: confirmed exactly (G:/ present, 999.7 GB free;
  `G:/Project` and `G:/Project/xlm-evidence-v3/essential-web` absent).
- Root cause: n/a.
- Fix: none needed; root NOT created in this task. Future genesis must
  verify the intended G: volume, exact absent/pristine root, ancestor
  containment, no reparse/symlink escape, and initial inventory. No F:
  substitution exists anywhere in code or artifacts.
- Regression tests: `test_no_real_g_write`.
- Evidence: this section; `verify-v3` reports `execution_root_absent`.

## 16. Test ledger

- Remediation file `tests/test_evidence_v3_remediation.py`: 71 tests,
  all passing (1 environment-conditional symlink skip). Original
  `tests/test_evidence_v3.py` extended to 41 tests (new sealed-pause and
  module-list cases), all passing. v2 evidence regressions
  (`test_evidence_v22`, `test_evidence_v21`, core/footer/text) and
  `test_prepare_bounds` all passing (see final response §20).
- Full focused command set, ruff check, ruff format check, and scoped
  mypy all green. The two pre-existing genesis mechanism tests plus the
  CLI synthetic genesis probe ran only against disposable temp roots;
  probe artifacts were deleted. No `epoch_start.json` in the repo, no G:
  writes, no network.
