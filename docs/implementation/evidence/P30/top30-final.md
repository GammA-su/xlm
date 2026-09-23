# Top 30 measured phases (final)

Maximum observed phase per node; runs are identified. Setup and call are distinct. Tier/serial columns describe final classification, including the initial diagnostic's subsequently reclassified cases.

| Seconds | Run | Phase | Tier | xdist safe | Repeated cost / role | Node |
|---:|---|---|---|---|---|---|
| 236.72 | final-serial | call | B | no: serial | captured execution / runtime inventory (where invoked); correctness | `tests/test_offline_workflow.py::test_offline_workflow_from_clean_environment` |
| 216.92 | final-serial | call | B | no: serial | captured execution / runtime inventory (where invoked); correctness | `tests/test_configurable_workflow.py::test_public_two_source_prepare_direct_queue_and_resume[False]` |
| 169.15 | final-serial | call | B | no: serial | captured execution / runtime inventory (where invoked); correctness | `tests/test_queue.py::test_toy_campaign_runs_sequentially_to_success` |
| 154.06 | final-serial | call | B | no: serial | captured execution / runtime inventory (where invoked); correctness | `tests/test_configurable_workflow.py::test_public_two_source_prepare_direct_queue_and_resume[True]` |
| 145.05 | final-serial | call | B | no: serial | captured execution / runtime inventory (where invoked); correctness | `tests/test_cli_train_demo.py::test_cli_train_and_resume_subprocess` |
| 126.45 | final-serial | call | B | no: serial | captured execution / runtime inventory (where invoked); correctness | `tests/test_final_acceptance.py::test_cli_resume_reconstructs_real_settings_and_exact_nonmultiple_budget` |
| 116.83 | performance-bounded | call | C | no: serial | original generated timing matrix; explicit evidence | `tests/test_dedup_throughput.py::test_benchmark_10k_workers` |
| 112.77 | final-serial | call | B | no: serial | captured execution / runtime inventory (where invoked); correctness | `tests/test_frozen_execution.py::test_public_queue_and_diagnostic_receipt_keep_separate_provenance` |
| 108.83 | final-serial | call | B | no: serial | captured execution / runtime inventory (where invoked); correctness | `tests/test_cli_eval_gen.py::test_cli_evaluate_and_generate_subprocess` |
| 86.84 | final-crash-cleanup | call | B | no: serial | isolated correctness fixture / execution | `tests/test_frozen_recovery.py::test_real_crash_preserves_committed_boundary_on_retry` |
| 75.95 | final-optional | call | D | explicit gate | CLI startup and artifact construction; correctness | `tests/test_eval_declared_inputs.py::test_cli_declared_run_under_a_limit_withholds_the_index` |
| 75.41 | final-optional | call | D | explicit gate | isolated correctness fixture / execution | `tests/test_harness_adapter.py::test_evidence_cache_reuses_and_recomputes_on_identity_change` |
| 74.10 | final-optional | call | D | explicit gate | CLI startup and artifact construction; correctness | `tests/test_eval_declared_inputs.py::test_cli_declared_run_publishes_a_labelled_index` |
| 70.49 | final-serial | call | B | no: serial | captured execution / runtime inventory (where invoked); correctness | `tests/test_queue.py::test_cancel_stops_a_queued_job_and_a_running_job` |
| 67.60 | final-serial | call | B | no: serial | captured execution / runtime inventory (where invoked); correctness | `tests/test_prepare.py::test_full_toy_run_reuses_on_repeat` |
| 56.12 | final-serial | call | B | no: serial | captured execution / runtime inventory (where invoked); correctness | `tests/test_configurable_training.py::test_public_cli_executes_registered_objective` |
| 51.79 | final-serial | call | B | no: serial | captured execution / runtime inventory (where invoked); correctness | `tests/test_queue.py::test_changed_live_tree_preserves_frozen_run` |
| 51.43 | final-serial | call | B | no: serial | captured execution / runtime inventory (where invoked); correctness | `tests/test_queue.py::test_crash_recovery_requeues_once_when_retries_remain` |
| 50.11 | final-fast-w16 | call | A | yes, A | CLI startup and artifact construction; correctness | `tests/test_artifact_identity.py::test_actual_public_cli_current_and_legacy_artifacts` |
| 49.98 | final-serial | call | B | no: serial | captured execution / runtime inventory (where invoked); correctness | `tests/test_queue.py::test_failed_jobs_keep_evidence_and_stay_terminal` |
| 49.72 | final-optional | call | D | explicit gate | CLI startup and artifact construction; correctness | `tests/test_eval_declared_inputs.py::test_cli_refuses_to_mix_declared_inputs_with_task_overrides` |
| 48.83 | final-serial | call | B | no: serial | captured execution / runtime inventory (where invoked); correctness | `tests/test_frozen_execution.py::test_intact_a_executes_after_live_b_and_import_override` |
| 48.74 | final-serial | call | B | no: serial | captured execution / runtime inventory (where invoked); correctness | `tests/test_frozen_execution.py::test_real_frozen_worker_trains_and_publishes` |
| 46.97 | final-serial | call | B | no: serial | captured execution / runtime inventory (where invoked); correctness | `tests/test_frozen_execution.py::test_real_worker_output_bound_and_terminal_failure` |
| 43.37 | final-fast-w16 | call | A | yes, A | isolated correctness fixture / execution | `tests/test_acquisition_bounds.py::test_public_plan_fetch_status_verify_prepare` |
| 41.16 | final-scale | call | C | no: serial | original large generated fixture; correctness/resource bound | `tests/test_dedup_throughput.py::test_memory_bound` |
| 40.86 | final-serial | call | B | no: serial | captured execution / runtime inventory (where invoked); correctness | `tests/test_prepare.py::test_partial_outputs_resume_without_redo` |
| 39.36 | final-serial | call | B | no: serial | captured execution / runtime inventory (where invoked); correctness | `tests/test_queue.py::test_submit_refuses_blocked_plans_and_missing_snapshots` |
| 39.14 | final-serial | call | B | no: serial | captured execution / runtime inventory (where invoked); correctness | `tests/test_offline_workflow.py::test_prepare_restart_reuses_verified_acquisition` |
| 28.67 | final-fast-w16 | call | A | yes, A | isolated correctness fixture / execution | `tests/test_mix01_views.py::test_data_adapt_refusals` |
