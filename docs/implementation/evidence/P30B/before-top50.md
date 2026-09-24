# Original serial top 50

Measured phase sums; setup/call/teardown remain separate. Cost tags are source-based, overlapping categories, not measured percentage attribution.

| Total s | Setup s | Call s | Teardown s | Node |
|---:|---:|---:|---:|---|
| 230.474 | 0.002 | 230.471 | 0.001 | `tests/test_offline_workflow.py::test_offline_workflow_from_clean_environment` |
| 223.757 | 0.003 | 223.754 | 0.000 | `tests/test_configurable_workflow.py::test_public_two_source_prepare_direct_queue_and_resume[False]` |
| 182.859 | 0.003 | 182.856 | 0.001 | `tests/test_cli_eval_gen.py::test_cli_evaluate_and_generate_subprocess` |
| 172.847 | 0.003 | 172.843 | 0.001 | `tests/test_cli_train_demo.py::test_cli_train_and_resume_subprocess` |
| 171.022 | 0.002 | 171.020 | 0.000 | `tests/test_queue.py::test_toy_campaign_runs_sequentially_to_success` |
| 150.595 | 0.003 | 150.592 | 0.000 | `tests/test_configurable_workflow.py::test_public_two_source_prepare_direct_queue_and_resume[True]` |
| 129.256 | 0.002 | 129.253 | 0.001 | `tests/test_final_acceptance.py::test_cli_resume_reconstructs_real_settings_and_exact_nonmultiple_budget` |
| 119.631 | 0.003 | 119.627 | 0.000 | `tests/test_frozen_execution.py::test_public_queue_and_diagnostic_receipt_keep_separate_provenance` |
| 85.748 | 0.002 | 85.745 | 0.000 | `tests/test_frozen_recovery.py::test_real_crash_preserves_committed_boundary_on_retry` |
| 73.916 | 0.003 | 73.913 | 0.000 | `tests/test_queue.py::test_cancel_stops_a_queued_job_and_a_running_job` |
| 69.761 | 0.002 | 69.758 | 0.000 | `tests/test_prepare.py::test_full_toy_run_reuses_on_repeat` |
| 65.352 | 0.003 | 65.348 | 0.001 | `tests/test_configurable_training.py::test_public_cli_executes_registered_objective` |
| 57.233 | 0.003 | 57.230 | 0.000 | `tests/test_queue.py::test_changed_live_tree_preserves_frozen_run` |
| 55.831 | 0.003 | 55.828 | 0.001 | `tests/test_queue.py::test_crash_recovery_requeues_once_when_retries_remain` |
| 53.485 | 0.003 | 53.482 | 0.000 | `tests/test_queue.py::test_failed_jobs_keep_evidence_and_stay_terminal` |
| 52.936 | 0.002 | 52.934 | 0.000 | `tests/test_frozen_execution.py::test_intact_a_executes_after_live_b_and_import_override` |
| 50.672 | 0.003 | 50.669 | 0.000 | `tests/test_frozen_execution.py::test_real_frozen_worker_trains_and_publishes` |
| 49.424 | 0.002 | 49.421 | 0.001 | `tests/test_frozen_execution.py::test_real_worker_output_bound_and_terminal_failure` |
| 41.449 | 0.003 | 41.446 | 0.000 | `tests/test_offline_workflow.py::test_prepare_restart_reuses_verified_acquisition` |
| 39.570 | 0.003 | 39.566 | 0.001 | `tests/test_prepare.py::test_partial_outputs_resume_without_redo` |
| 38.891 | 0.002 | 38.888 | 0.001 | `tests/test_queue.py::test_submit_refuses_blocked_plans_and_missing_snapshots` |
| 27.186 | 0.003 | 27.183 | 0.000 | `tests/test_frozen_execution.py::test_actual_environment_mismatch_fails_in_real_worker` |
| 24.598 | 0.003 | 24.594 | 0.000 | `tests/test_frozen_execution.py::test_worker_rejects_cli_override_outside_frozen_envelope` |
| 24.138 | 19.652 | 4.486 | 0.000 | `tests/test_frozen_execution.py::test_frozen_seed_copies_are_private` |
| 21.977 | 0.002 | 21.974 | 0.001 | `tests/test_queue.py::test_crash_without_retries_fails_loudly` |
| 21.871 | 0.003 | 21.867 | 0.001 | `tests/test_queue.py::test_submit_allows_explicit_duplicates` |
| 21.268 | 0.002 | 21.265 | 0.000 | `tests/test_queue.py::test_submit_deduplicates_identical_plans` |
| 20.693 | 0.003 | 20.689 | 0.000 | `tests/test_queue.py::test_status_lists_jobs_with_attempts` |
| 20.674 | 0.003 | 20.671 | 0.000 | `tests/test_queue.py::test_cpu_environment_refuses_cuda_job_without_execution` |
| 19.883 | 0.002 | 19.880 | 0.001 | `tests/test_reports.py::test_cli_runs_list_empty_and_populated` |
| 19.636 | 0.003 | 19.632 | 0.000 | `tests/test_frozen_execution.py::test_changed_tokenizer_and_real_shard_are_rejected` |
| 10.686 | 0.002 | 10.683 | 0.001 | `tests/test_continuation_p05.py::test_cpu_fresh_process_continuation_parity` |
| 9.787 | 0.003 | 9.784 | 0.000 | `tests/test_prepare.py::test_failed_stage_stops_with_reason_and_state` |
| 8.192 | 0.000 | 8.191 | 0.000 | `tests/test_cli_eval_gen.py::test_cli_demo_complete_vertical_slice_subprocess` |
| 7.706 | 0.002 | 7.703 | 0.000 | `tests/test_frozen_execution.py::test_authority_and_recomputed_envelope_fields_are_checked` |
| 7.190 | 0.001 | 7.189 | 0.000 | `tests/test_cli_train_demo.py::test_cli_demo_subprocess` |
| 6.088 | 0.002 | 6.086 | 0.000 | `tests/test_frozen_execution.py::test_independent_persisted_bindings_refuse_before_worker` |
| 5.188 | 0.003 | 5.185 | 0.001 | `tests/test_cleaning_scheduler_v2.py::test_dynamic_and_static_shard_manifests_exact` |
| 4.451 | 0.003 | 4.448 | 0.000 | `tests/test_dedup_throughput.py::test_cli_dedup_manifest_input_and_errors` |
| 3.850 | 1.635 | 1.947 | 0.268 | `tests/test_acquisition_bounds.py::test_selected_private_attempt_resumes_in_new_process` |
| 3.698 | 0.002 | 3.695 | 0.001 | `tests/test_dedup_throughput.py::test_cli_dedup_legacy_and_sharded` |
| 3.501 | 0.003 | 3.498 | 0.000 | `tests/test_cleaning_throughput.py::test_workers_1_2_4_agree` |
| 3.038 | 0.002 | 3.035 | 0.000 | `tests/test_frozen_execution.py::test_snapshot_damage_rejects_before_worker[missing]` |
| 2.932 | 0.002 | 2.929 | 0.000 | `tests/test_frozen_execution.py::test_snapshot_damage_rejects_before_worker[extra]` |
| 2.911 | 0.003 | 2.908 | 0.000 | `tests/test_frozen_execution.py::test_snapshot_damage_rejects_before_worker[tamper]` |
| 2.376 | 0.003 | 2.373 | 0.000 | `tests/test_dedup_small_oracles.py::test_small_lexical_equivalence[workers-layouts]` |
| 2.327 | 0.002 | 2.324 | 0.000 | `tests/test_cleaning_throughput.py::test_cli_clean_manifest_input_and_errors` |
| 2.294 | 0.002 | 2.291 | 0.000 | `tests/test_acquisition_bounds.py::test_killed_download_resumes_in_a_fresh_process_without_new_allowance` |
| 2.101 | 0.004 | 2.096 | 0.000 | `tests/test_cleaning_throughput.py::test_max_docs_matches_reference` |
| 2.020 | 0.003 | 2.017 | 0.001 | `tests/test_parallel_tokens.py::test_worker_count_and_assembly_bytes_exact` |

## Module subtotals

| Module | Nodes | Setup s | Call s | Teardown s |
|---|---:|---:|---:|---:|
| `tests/test_queue.py` | 11 | 0.029 | 556.827 | 0.005 |
| `tests/test_configurable_workflow.py` | 2 | 0.005 | 374.346 | 0.001 |
| `tests/test_frozen_execution.py` | 13 | 19.683 | 371.207 | 0.005 |
| `tests/test_offline_workflow.py` | 2 | 0.005 | 271.916 | 0.001 |
| `tests/test_cli_eval_gen.py` | 2 | 0.003 | 191.047 | 0.001 |
| `tests/test_cli_train_demo.py` | 2 | 0.004 | 180.032 | 0.001 |
| `tests/test_final_acceptance.py` | 1 | 0.002 | 129.253 | 0.001 |
| `tests/test_prepare.py` | 3 | 0.008 | 119.108 | 0.002 |
| `tests/test_frozen_recovery.py` | 1 | 0.002 | 85.745 | 0.000 |
| `tests/test_configurable_training.py` | 1 | 0.003 | 65.348 | 0.001 |
| `tests/test_reports.py` | 1 | 0.002 | 19.880 | 0.001 |
| `tests/test_cleaning_throughput.py` | 6 | 0.017 | 11.426 | 0.003 |
| `tests/test_continuation_p05.py` | 1 | 0.002 | 10.683 | 0.001 |
| `tests/test_dedup_throughput.py` | 3 | 0.007 | 9.343 | 0.001 |
| `tests/test_prepare_bounds.py` | 8 | 0.022 | 7.137 | 0.004 |
| `tests/test_cleaning_scheduler_v2.py` | 1 | 0.003 | 5.185 | 0.001 |
| `tests/test_acquisition_bounds.py` | 3 | 1.646 | 5.034 | 0.269 |
| `tests/test_dedup_small_oracles.py` | 1 | 0.003 | 2.373 | 0.000 |
| `tests/test_parallel_tokens.py` | 1 | 0.003 | 2.017 | 0.001 |
| `tests/test_artifact_identity.py` | 2 | 0.006 | 1.255 | 0.001 |
| `tests/test_sharded_datasets.py` | 3 | 0.009 | 0.175 | 0.002 |
