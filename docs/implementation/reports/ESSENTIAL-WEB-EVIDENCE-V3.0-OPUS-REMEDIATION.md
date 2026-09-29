# Essential-Web evidence v3.0: Opus architectural remediation of the Phase-P boundary

2026-09-29. Checkout `F:\Project\xlm-data-ultrax`, branch `data/mix01-ultrax-6b`.
Starting HEAD `37c9fcc06d094ba3a7a9f52efde998a7970db63c` (first remediation), after the
second independent review verdict **PHASE-P AUTHORIZATION BLOCKED** (M and T).
Implementation commit **A** `3dd5ebce0edb7d8e676966c9195b73fb5a1978c9`; derived
children + tests-typing + reports commit **B** (see §9). Frozen protocol commit
`52569c525a6613faab096d17b60167b4aaa0f214`. No push.

Scope boundary held: no network, no acquisition, no corpus text, no real genesis,
no G: execution root (still absent), no Phase D, no training/tokenizer/admission,
no scientific-sample, protocol, freeze or cap change, no operator approval created.
Synthetic epochs used only disposable temporary roots.

## 1. Architecture review of 37c9fcc (before editing)

Every public/callable path that could fetch bytes, execute a Phase-P operation,
publish genesis, modify accounting or mark a phase complete:

| Path in 37c9fcc | Bypass |
|---|---|
| `executor.PhasePExecutor(..., validated_auth=<mapping>, transport_client=...)` | caller-built "validated" dict accepted; caller operations supplied `file/url/range/etag/kind`; caller `redirects` list trusted |
| `executor.FakeTransport.fetch(url)` | no Range, no deadline; whole body materialized before metering |
| `epoch.publish_genesis_record(root, body, claim={"claim": path})` | fabricated claim mapping accepted; loader checked only digest/epoch/root string |
| `authz.validate_phase_p_authorization(..., expected_*=caller values)` | expectations supplied by caller; `list("MT") == ["M","T"]`; env keys only presence-checked |
| `authz.validate_operator_approval` | `"phase-P" in statement` substring semantics |
| `envidentity.git_blob_sha` | `git hash-object <working file>` called "blob identity" (CRLF drift) |
| `ledger.V3Ledger` (public, snapshot + journal) | reload lost later records; pending reservations reloaded without bytes/elapsed |
| `guards.PhysicalDiskInventory.write_file/atomic_replace` | `Path.is_symlink` only: junctions escaped; two write paths with different peak logic |
| `guards.FailClosedSupervisor.release_child(pid, terminated=True)` | boolean release of a live child |
| `guards.ActiveRuntime` | in-memory; budget queried for `"__ready__"`, not the operation's file |
| `readiness` | mechanism "self-tests" of isolated helpers; true even with guards disabled |

All nine concrete exploits were **executed** against an extracted 37c9fcc tree
(offline, temp dirs): [repro script](../evidence/ESSENTIAL-WEB-EVIDENCE-V3.0-OPUS-REMEDIATION/repro_astra_exploits_37c9fcc.py),
[output](../evidence/ESSENTIAL-WEB-EVIDENCE-V3.0-OPUS-REMEDIATION/repro_astra_exploits_37c9fcc.out.txt):
negative free-text approval accepted; `arms="MT"` + invented environment accepted;
working-tree hash ≠ committed blob; fabricated claim mapping published genesis;
fabricated validation mapping + arbitrary `16-19` "footer" executed; `fetch(url)`
without range/deadline; **real NTFS junction write escaped the root**; boolean
release of a live child; ledger reload `live=2 persisted=1`.

## 2. Redesign decision: one trusted boundary

`authz.py`, `epoch.py`, `guards.py`, `ledger.py` and the old executor/transport
were **deleted**, not patched. The supported API is four functions in
`executor.py`, taking only file paths (plus a synthetic harness for disposable
roots):

```
check_authorization / perform_genesis / execute_phase_p / inspect_epoch
  (repo_root, root, authorization_path, approval_path, review_path[, harness][, max_operations])
```

Every call re-derives and re-validates everything from bytes and the actual
runtime:

```
derive_expectations (actual repo, committed children, actual runtime)   -> minted Expectations
validate_authorization (canonical bytes, exact schema)                    -> minted ValidatedPhasePAuthorization
  + typed review decision bound to the authorization REQUEST digest
validate_operator_approval (decision == "APPROVE_PHASE_P")               -> minted ValidatedOperatorApproval
genesis.verify_root / publish / load_epoch_start (exact schema, full bindings)
journal replay (hash chain + durable head) -> EpochState reducer -> crash conservation -> reconcile
supervisor thread start (fail closed) -> SESSION_OPEN
for each plan operation (M then T, frozen order):
  TIME hold (durable) -> ATTEMPT_RESERVE (P ceilings keep future-D) -> ATTEMPT_ISSUED
  -> single-hop TransportRequest(exact Range, absolute deadline) -> ATTEMPT_HEADERS
  -> per chunk: checkpoint (memory/deadline) -> ATTEMPT_BODY (durable) -> expose
  -> redirect? policy-check actual Location, new durable reservation
  -> identity (strong ETag, exact Content-Range/total, exact length, structural path, magic)
  -> settle (never refunds) -> journalled write (old+new peak, reconcile) + OP completion
seal: bounded metadata parse + frozen-binding comparison -> result.json -> P_COMPLETE_SEALED
```

Modules: `trust` (minting registry), `authorization`, `envidentity`, `plan`,
`genesis`, `journal`, `fsroot`, `memory`, `transport`, `netpolicy`, `footer`,
`executor`, `harness`, `synthetic`, `readiness`, `dry`, `schedules`, `frozen_v3`.
Callers cannot pass validation results, operations, URLs, ranges, labels,
budgets, clocks or transports in REAL mode. A synthetic harness may substitute
only root, authored synthetic plans (which must name the synthetic source,
never the real repository/revision), the offline `FakeTransport` (exact type),
a clock and a memory reader, and is refused at or under
`G:/Project/xlm-evidence-v3`.

## 3. Frozen contract and scientific identity (unchanged)

`verify-v3` (exit 0): protocol SHA-256 `c191aa49a7e35349e27eb077c7105ead1a15a7edbbb28ef0bc78f157b219fd79`;
freeze `aa977972883af209afffebae221683658b2637281ae8e72741150750534fd834`;
cap map `e2485cd438524124c22074d59c48a5ee7dc699f9c9a9ea3f9c11deeef54e0f7b`;
epoch `essential-web-evidence-v3.0:essential-web:epoch-0001` NOT_STARTED /
authorization NONE; selection `975ba3dee4af0598e665ea05c69bbe49336c3e3a35fca777a863073190b78474`;
namespace `essential-web-evidence-v2.0`; revision
`ce4eccc7e9604667b6d7f32cb6274b8b41f3113d`; policy
`f4357f61f434d5105d823266153dac6372e122f12767187631e8796b4899dd07`; M 8 files /
seed 20260927 / projection `[eai_taxonomy, quality_signals]` / eight 512-row
windows; T 118 locators; M 688/720, T 95/127; execution root absent.
`git diff 37c9fcc -- <protocol, review, freeze, epoch_definition, V3.0 evidence>` is empty.

## 4. Findings: reproduction, root cause, change, closing tests, limitation

Test files: `tests/test_evidence_v3{,_authorization,_journal,_execution,_containment,_e2e,_children}.py`
(abbreviated A/J/X/C/E/CH below).

1. **Authorization object forgeable.** Reproduced (arms "MT", invented env).
   Root cause: validator took caller expectations and returned a dict.
   Change: minted `Expectations` derived from the actual repo/runtime; strict
   canonical bytes; exact key set; canonical-byte equality (bool≠1, 2.0≠2);
   `arms` exact array `["M","T"]`; returns minted `ValidatedPhasePAuthorization`
   (no constructor, registry-checked, immutable, uncopyable). Tests: A
   `test_arms_must_be_exact_ordered_array` (6), `test_invented_*_resealed_refuses`,
   `test_any_binding_mismatch_refuses` (18), `test_resource_cap_bool_or_float_refuses`,
   `test_unknown_and_missing_fields_refuse`, `test_trusted_classes_cannot_be_constructed`,
   `test_minted_objects_are_immutable_and_uncopyable`,
   `test_executor_public_api_accepts_no_validation_objects`. Limitation: Python
   has no memory-safe private state; deliberate use of underscore internals
   (`trust._mint`) is outside the supported API.
2. **Code/env identity.** Reproduced (hash-object ≠ committed blob). Change:
   one width — SHA-256 of `git cat-file blob <commit>:<path>`; working-tree
   representation recorded separately (`exact`/`crlf`, anything else refuses);
   environment (15 exact keys: Python version/implementation/venv class, uv,
   committed `.python-version`/`pyproject.toml`/`uv.lock` SHA-256, pyarrow,
   psutil, torch version + build read from `torch/version.py` without import,
   OS system/release/version, architecture) compared field-for-field with the
   actual runtime; REAL mode also requires every loaded `xlm.*` module to be
   bound code. Tests: A `test_committed_blob_identity_uses_git_objects`,
   `test_code_hash_width_40_refuses`, `test_runtime_environment_exact_keys_without_importing_torch`,
   `test_actual_child_code_map_passes_actual_real_validator` (clean subprocess:
   committed child map + actual runtime pass every REAL binding; the only
   refusal is the deliberate "review must be committed" rule), CH
   `test_child_code_identity_is_committed_blob_sha256`.
3. **Operator approval semantics.** Reproduced. Change: separate strict artifact;
   `decision` must equal `APPROVE_PHASE_P`; binds the authorization digest;
   `notes` inert. Tests: A `test_only_exact_positive_decision_approves` (7),
   `test_notes_have_zero_effect`, `test_empty_or_wrong_digest_approval_refuses`,
   `test_approval_fields_strict` (7), `test_approval_unknown_field_refuses`.
   Added: typed review decision bound to the authorization *request* digest —
   A `test_review_decision_must_bind_this_request`, `test_review_decision_fields_strict` (6).
4. **Genesis.** Reproduced (fabricated claim). Change: `genesis.publish`
   accepts only minted objects; `O_EXCL` claim binds epoch, root path and
   physical identity (volume serial + directory file ID), auth and approval;
   30-key exact `epoch_start.json`; loader checks every binding against the
   freshly validated objects and the physical control files. Tests: J
   `test_eight_concurrent_genesis_attempts_one_winner`,
   `test_second_genesis_refused_even_after_restart`, `test_dirty_root_refused`,
   `test_crash_between_claim_and_epoch_start_blocks`,
   `test_claim_and_epoch_bound_to_physical_directory`,
   `test_resealed_incomplete_or_altered_epoch_start_refused` (17), A
   `test_fabricated_mappings_cannot_enter_genesis`. Limitation: a crash between
   claim and epoch_start leaves the epoch BLOCKED for review (by design).
5. **Ledger.** Reproduced (`live=2 persisted=1`). Change: no snapshot; the
   hash-chained, fsynced journal is the only state; chain seeded by the
   epoch_start digest; durable head detects truncation; strict reducer re-run
   on every load; open work conserved (`CRASH_ATTEMPT` = full reservation,
   `CRASH_TIME` = full open hold); torn tail BLOCKS. Tests: J
   `test_repeated_load_update_reload_cycles_are_exact`,
   `test_crash_mid_body_conserves_reservation_and_time`,
   `test_crash_state_never_becomes_less_conservative`,
   `test_journal_tail_truncation_refused`, `test_journal_torn_tail_blocks`,
   `test_journal_corruption_and_reordering_refused`,
   `test_journal_from_another_epoch_start_refused`,
   `test_every_attempt_keeps_footer_category_and_plan_identity`. Limitation: an
   adversary with write access who rewrites journal AND head consistently is
   not detectable without an external anchor.
6. **Plan-bound executor.** Reproduced (16-19 "footer"). Change: operations come
   only from the validated plan (REAL: committed child == recomputation from
   `freeze.json`); the reducer re-checks file/op/range/order per attempt. Tests:
   X `test_exact_plan_operations_only`, `test_arbitrary_16_19_range_plan_refused_even_when_resealed`,
   `test_caller_cannot_relabel_data_as_footer`, `test_ledger_reducer_refuses_off_plan_attempt`,
   `test_swapped_plan_not_bound_by_authorization_refused`, `test_transport_request_cannot_be_forged`;
   A `test_only_offline_fake_transport_accepted_in_harness`,
   `test_live_transport_refuses_requests_not_issued_by_executor`,
   `test_synthetic_plan_cannot_name_the_real_source`.
7. **Range-aware transport.** Reproduced (`fetch(url)`). Change: immutable sealed
   `TransportRequest(url, host, range, absolute deadline, timeout, read bound)`;
   single hop; `read_chunk(max)`; the private live transport opens only
   executor-issued, single-use requests, resolves DNS itself and refuses
   non-global addresses. Test: X `test_request_spec_carries_exact_range_and_deadline`.
8. **Redirects from actual responses.** Reproduced (caller list). Change: only
   actual 3xx `Location` values; https, exact host, 443, no userinfo/IP/
   localhost/relative; signed-target host or the exact canonical path; ≤3
   transitions; each hop a new durable reservation; refused bodies charged.
   Tests: X `test_actual_redirect_to_bad_destination_refused_and_charged` (13),
   `test_fourth_redirect_refused`, `test_three_redirects_allowed`.
9. **Streaming body accounting.** Change: per 64 KiB chunk, checkpoint then
   durable `ATTEMPT_BODY` then expose; reservation = max(payload, 64 KiB)+1;
   uncertain outcomes charge the full reservation. Tests: X
   `test_success_and_redirect_bodies_counted_exactly`, `test_invalid_status_body_counted`,
   `test_partial_short_body_counted_and_refused`,
   `test_exception_after_bytes_keeps_reservation_then_retries`,
   `test_uncertain_open_failure_retains_reservation`,
   `test_oversized_redirect_body_cannot_disappear`,
   `test_transport_overdelivery_is_charged_and_refused`, `test_full_200_response_is_stop`,
   `test_retryable_503_backs_off_then_exhausts`.
10. **Exact remote identity.** Change: strong ETag syntax + frozen equality,
    exact `bytes s-e/N`, N = frozen length, exact body length, identity coding,
    structural path (query never satisfies identity), magic. Tests: X
    `test_identity_mismatch_is_stop_with_bytes_kept` (9), `test_total_length_mismatch_is_stop`,
    `test_long_206_body_refused`, `test_wrong_magic_is_stop`,
    `test_weak_expected_etag_cannot_be_planned`, `test_structural_resource_identity`.
11. **Process lifecycle.** Reproduced (boolean release). Change: `register(pid)`
    → opaque handle; `reap` succeeds only when the registry observes the exact
    process (pid + creation time) gone; no `release`, no exit proof; registered
    PIDs measured with their own descendants. Tests: C
    `test_registry_has_no_boolean_or_caller_proof_release`,
    `test_live_child_cannot_be_released_and_reaps_after_exit`,
    `test_sampler_includes_descendants`, `test_registered_external_non_descendant_is_measured`
    (real detached orphan; found that the venv launcher's child must be included).
12. **Supervision during work.** Change: executor-owned monitor thread (100 ms
    real / 10 ms synthetic) latches breach or measurement failure; body and
    parser loops checkpoint; start fails closed. Tests: X
    `test_transient_overcap_during_body_cancels`, `test_memory_measurement_failure_aborts`,
    `test_memory_unavailable_at_start_refuses_before_network`, `test_parser_input_is_bounded`;
    C `test_supervisor_latches_breach_and_refuses_bad_readings`. Limitation:
    sampling cannot prove an infinitesimal transient stayed under 256 MiB;
    bounded buffers + 4 MiB parser input + Thrift limits complement it.
13. **Windows junctions.** Reproduced (real junction escape). Change: strict
    lowercase relative grammar; `st_file_attributes`/`st_reparse_tag` checks on
    every ancestor and component; root (volume, file-ID) re-verified each
    operation; post-mutation re-check; physical scan refuses any reparse point.
    Tests (real `mklink /J`, never skipped on Windows): C
    `test_real_junction_child_escape_refused`, `test_ancestor_junction_refused_at_genesis`,
    `test_junction_planted_after_genesis_refuses_before_network`,
    `test_junction_planted_mid_run_stops_without_escape`,
    `test_relative_path_grammar_refuses` (18), `test_root_replaced_by_other_directory_refused`.
    `test_symlink_escape_refused` is SKIPPED here (no symlink privilege) and is
    not counted as junction evidence. Limitation: a concurrent local adversary
    could race a component swap between check and use; the post-check detects
    but cannot undo external bytes.
14. **One write API.** Change: `_write` = journalled reserve (old+new peak,
    subcaps and combined, both arms for control) → temp `O_EXCL` → write-through
    rename → size check → full reconcile (unknown file STOP) → commit; control
    allowances reserved at genesis. Tests: C `test_unknown_physical_file_stops`,
    `test_unknown_file_mid_run_stops_and_keeps_accounting`,
    `test_inventory_survives_reload_and_later_mutation`,
    `test_actual_bytes_exceeding_reservation_refused`,
    `test_disk_reservation_refuses_over_subcap_and_combined`,
    `test_replacement_counts_old_plus_new_peak`, `test_control_files_count_against_both_arms`,
    `test_real_epoch_reserves_control_allowances_at_genesis`. Limitation:
    occupancy uses logical sizes (not cluster allocation); directories count 0.
15. **Durable runtime.** Change: every step opens a durable hold (request ≤30 s,
    clipped by the file's and arm's remaining time); elapsed charged at each
    boundary to the holding file; crash charges the full open hold; pause only
    at a quiescent `SESSION_CLOSE`. Tests: X `test_31s_open_request_times_out_and_is_charged`,
    `test_elapsed_survives_reload`, `test_exhausted_file_budget_never_reaches_transport`,
    `test_staging_and_parse_are_inside_active_time`, `test_pause_cannot_bypass_open_request`.
    Limitation: time before the session opens (interpreter start, imports) is not
    charged; a non-interruptible parse that overruns its hold and then crashes
    is charged only the hold.
16. **Future-D reservation.** Change: plan ceilings = frozen caps − retained D
    allocation, enforced by the reducer at every reservation. Tests: X
    `test_m_48th_p_request_permitted_while_d_reserve_retained`,
    `test_m_49th_p_request_refused_to_preserve_d_reserve`,
    `test_m_per_file_ceiling_12_retains_4_for_d`, `test_t_reserved_control_ceiling`.
17. **Readiness.** Change: static identity checks + 11 integrated adversarial
    scenarios through the public executor; statuses `VERIFIED_STATIC`,
    `VERIFIED_SYNTHETIC_INTEGRATION`, `UNVERIFIED_LIVE`; any mismatch → BLOCKED and
    the builder refuses to publish. Observed during development: code identity
    was BLOCKED until commit A existed. Test: CH `test_readiness_is_derived_and_not_authorized`.
18. **Child path reproducibility.** Change: descriptors embed the normative
    repository-relative path; producer binds commit A. Tests: CH
    `test_regeneration_is_exact_for_relative_and_absolute_output`,
    `test_children_embed_no_builder_or_checkout_paths`,
    `test_manifest_binds_every_child_by_repository_path`. Absolute `G:` strings
    remaining in children are frozen `freeze.json` provenance and the frozen root.
19. **Phase-P operations.** M: per file GET 0-3, GET `[footer_start, N-1]`
    (trailer length must equal the frozen start). T: GET 0-3, GET N-8..N-1,
    then N-8-L..N-9 only after L passes range/body/parser/footer-budget checks.
    Seal compares M window row group, 81 projected chunks and compressed/
    uncompressed sums; T exactly one `text` chunk equal to the frozen span.
20. **Phase D** is not executable (no API, reducer refuses D states).
21. **Adversarial tests** are listed per finding above; §6 has totals.

## 5. Recomputed Phase-P arithmetic

M: direct logical 16; observed-style physical 24; cold no-retry 40; D identity
reserve 32; combined 72 ≤ 80; **P ceiling 48 arm / 12 per file**; spare 8;
payload 1,398,416; footer remaining before redirect/error/retry 32,156,016;
minimum per-file footer headroom 3,991,024. Byte ceilings keep the D identity
body reservation (4 × 65,537 per file): P footer bytes 31,457,248 arm,
3,932,156 per file (implementation reservation policy, not a cap change).
T: direct logical 24; observed physical 32; cold 48; D identity 32; combined
controls 80; T has no frozen footer-request cap, so P keeps D identity + the
47 frozen data ranges inside 100/file and 800/arm: **P ceiling 721 arm**,
per file `[90, 90, 91, 90, 90, 90, 90, 90]`; footer bytes 14,680,032 arm /
1,835,004 per file; transfer 52,820,671 arm. T footer headroom is not
fabricated before L is observed.

## 6. Evidence runs (exact commands; offline; locked environment)

Environment: Windows 11 Pro 10.0.26200 AMD64, CPython 3.12.13 project venv, uv
0.12.19, pyarrow 25.0.1, psutil 7.2.2, torch 2.14.0+cpu. Thread env:
`OMP/MKL/OPENBLAS/NUMEXPR_NUM_THREADS=1 TOKENIZERS_PARALLELISM=false`.

| Command | Result |
|---|---|
| `uv run --offline --locked --no-sync --extra cpu --extra eval python -m pytest tests/test_evidence_v3.py tests/test_evidence_v3_authorization.py tests/test_evidence_v3_journal.py tests/test_evidence_v3_execution.py tests/test_evidence_v3_containment.py tests/test_evidence_v3_e2e.py tests/test_evidence_v3_children.py -n 0 -p no:cacheprovider -q -rs` | 225 collected (15/68/32/58/38/9/5): **224 passed, 1 skipped** (`test_symlink_escape_refused`: no symlink privilege; not junction evidence), exit 0, 305.8 s |
| same runner: `tests/test_evidence_v22.py tests/test_evidence_v21.py tests/test_evidence_v2_core.py tests/test_evidence_v2_footer.py tests/test_evidence_v2_text.py tests/test_prepare_bounds.py -n 0` | 180 passed, exit 0 |
| same runner: `tests/test_acquisition_bounds.py -n 0` | 33 passed, **2 failed** (pre-existing, outside scope; see §10) |
| `ruff check` / `ruff format --check` on `src/xlm/data/evidence_v3 scripts/evidence_v3.py tests/evidence_v3_support.py tests/test_evidence_v3*.py` + repro script | clean |
| `mypy src/xlm/data/evidence_v3 scripts/evidence_v3.py tests/evidence_v3_support.py tests/test_evidence_v3*.py` | no issues (28 files) |
| `python scripts/evidence_v3.py verify-v3` | exit 0 (§3) |
| `python scripts/evidence_v3.py build-v3-children --out-dir <scratch> --implementation-commit 3dd5ebc…` ×2 | exit 0; byte-identical |

Integrated end-to-end (E `test_integrated_phase_p_across_real_process_death`):
authorize → approve → genesis → 3 operations in-process → **new process killed
with `os._exit(137)` after 1 journalled body byte** → new process with the
real psutil sampler conserves (`CRASH_ATTEMPT` full reservation, `CRASH_TIME`
30 s), resumes, finishes and seals M and T. Verified: journalled requests equal
physical requests logged by all three processes; bodies = Σ settled charges;
runtime ≥ 1 s per request + 30 s crash hold; session-1 staged bytes unchanged;
physical files ⊆ inventory; each plan op completed exactly once in order; one
GENESIS; epoch_start unchanged; all reserved ranges in the plan; sealed results
bind the authorization; a sealed epoch re-run issues nothing. Adversarial
variants (E, 8): bad redirect, 5 MB body, 31 s timeouts, wrong ETag, wrong
range, memory breach during the body, unknown file mid-run, journal fsync
failure — each fails closed with conserved accounting, the arm
`P_INCOMPLETE`, T never started, and no automatic rerun issues any request.

## 7. CHILD artifacts (regenerated, commit A bound)

`docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V3.0-CHILD/`, 14 files,
**113,954 bytes**; authorization NONE, executable false; readiness verdict
READY_FOR_PHASE_P_AUTHORIZATION_REVIEW (13 verified mechanisms, 2 `UNVERIFIED_LIVE`).

| Artifact | canonical digest | file SHA-256 |
|---|---|---|
| artifact_manifest.json | `3a524225312641ad1c7f65247709ea66b576afadebe8c13e28d938096f0f3d0b` | `88a014e31d8cac0dbcc962d9298442068bd6048af16a9e797233a1f8a6159a37` |
| arm_m_phase_p_dry.json | `b4af65eced13ac9e7cca0ef99830f3f9307f85e77da090dd6406c0e3c0766905` | `0ac4f51b09aa9bd2008237aeb824023d92c3d2a83cbc68a9b4b5f37b6286f580` |
| arm_t_phase_p_dry.json | `9c1f17017d0d493ed5ebfa13a4e9db478ac5a0b781c0c96dfe9ddd0195e623a0` | `9f623e08edfb4e30b87d9d177b4c685ec9ab8628810b80fabbdcad6ff1ec912a` |
| readiness_review.json | `5bdbecc656fee68521ccf9a3737cdfa5cd15493c0ca2c32f2f3c6de02c1006e2` | `d9bcd1b2ffaf675fdd57b29f9fd93139912cfb98b44a964b2fdfd247ab45a00e` |

## 8. Requirement ledger

| Requirement | Status |
|---|---|
| Unforgeable authorization / approval / review types | IMPLEMENTED, VERIFIED (synthetic + REAL-binding subprocess) |
| Committed-blob code identity; actual-runtime env comparison | IMPLEMENTED, VERIFIED |
| Exclusive, physically bound genesis; full-binding loader | IMPLEMENTED, VERIFIED |
| Authoritative journal; restart continuation; crash conservation | IMPLEMENTED, VERIFIED (incl. real process death) |
| Plan-bound executor; no caller ops/URLs/ranges/labels | IMPLEMENTED, VERIFIED |
| Range/deadline transport; actual redirects; streaming accounting; identity | IMPLEMENTED, VERIFIED (synthetic); live transport NOT RUN |
| Registry lifecycle; during-work supervision | IMPLEMENTED, VERIFIED |
| Junction-safe containment; single write API | IMPLEMENTED, VERIFIED (real junctions); symlink case NOT RUN (privilege) |
| Durable runtime and deadlines | IMPLEMENTED, VERIFIED |
| Future-D cap reservation (M 48/49; T ceilings) | IMPLEMENTED, VERIFIED |
| Derived readiness; path-independent children | IMPLEMENTED, VERIFIED |
| Live network / real source identity / real footers | NOT RUN (UNVERIFIED_LIVE) |
| Real genesis, operator approval, Phase D, push | OUT OF SCOPE |
| `docs/implementation/STATUS.md` update | NOT RUN — file carries unrelated pre-existing user edits that must stay unstaged |

## 9. Files changed

Commit A: `src/xlm/data/evidence_v3/` (new `authorization`, `footer`, `fsroot`,
`genesis`, `harness`, `journal`, `memory`, `netpolicy`, `plan`, `synthetic`,
`trust`; rewritten `__init__`, `dry`, `envidentity`, `executor`, `readiness`,
`transport`; extended `frozen_v3`, `schedules`; deleted `authz`, `epoch`,
`guards`, `ledger`), `scripts/evidence_v3.py`, `tests/evidence_v3_support.py`,
`tests/test_evidence_v3*.py` (old `test_evidence_v3_remediation.py` deleted).
Commit B: regenerated CHILD tree, test typing fixes, this report, the
reproduction evidence directory, and the IMPLEMENTATION.md pointer.

## 10. Remaining limitations and open items for the reviewer

- **Authenticity is procedural, not cryptographic.** Authorization, approval and
  review are integrity-bound JSON; an operator who fabricates and commits a
  review decision can pass validation. The code guarantees that the executed
  epoch binds a specific committed, auditable review decision for the exact
  authorization request, the exact committed code and the actual runtime.
  Signed commits or an external anchor would be needed for identity proof.
- **Live behaviour is unverified**: HF ETag/Content-Range/redirect shapes, cas-bridge
  signed-target behaviour, TLS/DNS, and the footer-binding definitions
  (compressed/uncompressed sums as `total_*_size`) are assumptions checked only
  on synthetic fixtures; a mismatch fails closed (STOP) after metered requests.
- **Interpretations for review**: T P ceiling (721 arm, 100−(4+n) per file)
  derived from shared caps rather than an 80-control cap; D identity body
  reservation 4 × 65,537 bytes/file; no D time reservation (P and D share 1800 s);
  control files counted in scratch for both arms; T raw footer bytes (which may
  contain column statistics) are retained opaque in the counted root for the
  future D reader but never decoded, logged or rendered.
- In-process private-name access, concurrent local filesystem adversaries, torn
  journal tails (BLOCK), and sampling granularity are stated limits (§4).
- `tests/test_acquisition_bounds.py::{test_public_plan_fetch_status_verify_prepare,
  test_production_fetch_with_verified_admission}` fail with a Windows temp-file
  `ENOENT` in the acquisition progress journal; no module they import is changed
  here (nothing outside `evidence_v3` imports it). Recorded, not fixed.

## 11. Decision rule and verdict

"Can a malicious or buggy caller using public/supported APIs execute a physical
Phase-P operation with any unreviewed authorization, unapproved operator state,
wrong root, wrong epoch, wrong file, wrong range, wrong budget, or without
durable accounting?" Unapproved operator state, wrong root, wrong epoch, wrong
file, wrong range, wrong budget, missing durable accounting: **no** — each is
refused by construction and demonstrated by the adversarial suites above.
Unreviewed authorization: the code cannot distinguish a genuine reviewer from an
operator impersonating one (§10); it can only bind a committed, request-specific
review decision. That residual is inherent to unsigned artifacts and is surfaced
for the reviewer to accept or to require signing.

**READY TO REPEAT PHASE-P AUTHORIZATION REVIEW** (implementation), with the
authenticity limit above explicitly handed to the independent reviewer.

## 12. Next action

Independent Phase-P authorization review of commit B (children bound to A):
re-run the exploit reproduction against 37c9fcc and the adversarial suites
against HEAD, then — only if approved — the reviewer commits a typed
`essential-web-evidence-v3-phase-p-review-decision` for the exact authorization
request, and the operator runs `scripts/evidence_v3.py check-auth` before any
`phase-p-genesis`.
