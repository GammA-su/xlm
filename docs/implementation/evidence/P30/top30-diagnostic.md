# Top 30 measured phases (diagnostic)

Maximum observed phase per node; runs are identified. Setup and call are distinct. Tier/serial columns describe final classification, including the initial diagnostic's subsequently reclassified cases.

| Seconds | Run | Phase | Tier | xdist safe | Repeated cost / role | Node |
|---:|---|---|---|---|---|---|
| 192.75 | fast-w4 | call | B | no: serial | captured execution / runtime inventory (where invoked); correctness | `tests/test_final_acceptance.py::test_cli_resume_reconstructs_real_settings_and_exact_nonmultiple_budget` |
| 96.90 | fast-w4 | call | B | no: serial | captured execution / runtime inventory (where invoked); correctness | `tests/test_prepare.py::test_full_toy_run_reuses_on_repeat` |
| 79.87 | fast-w4 | call | B | no: serial | captured execution / runtime inventory (where invoked); correctness | `tests/test_configurable_training.py::test_public_cli_executes_registered_objective` |
| 59.39 | fast-w4 | call | B | no: serial | captured execution / runtime inventory (where invoked); correctness | `tests/test_queue.py::test_submit_refuses_blocked_plans_and_missing_snapshots` |
| 52.66 | fast-w4 | call | B | no: serial | captured execution / runtime inventory (where invoked); correctness | `tests/test_prepare.py::test_partial_outputs_resume_without_redo` |
| 50.39 | fast-w4 | call | B | no: serial | captured execution / runtime inventory (where invoked); correctness | `tests/test_frozen_execution.py::test_changed_tokenizer_and_real_shard_are_rejected` |
| 43.49 | fast-w4 | call | B | no: serial | captured execution / runtime inventory (where invoked); correctness | `tests/test_frozen_execution.py::test_authority_and_recomputed_envelope_fields_are_checked` |
| 36.55 | fast-w4 | call | B | no: serial | captured execution / runtime inventory (where invoked); correctness | `tests/test_frozen_execution.py::test_snapshot_damage_rejects_before_worker[tamper]` |
| 33.46 | fast-w4 | call | B | no: serial | captured execution / runtime inventory (where invoked); correctness | `tests/test_frozen_execution.py::test_independent_persisted_bindings_refuse_before_worker` |
| 31.90 | fast-w4 | call | B | no: serial | captured execution / runtime inventory (where invoked); correctness | `tests/test_queue.py::test_crash_without_retries_fails_loudly` |
| 29.71 | fast-w4 | call | B | no: serial | captured execution / runtime inventory (where invoked); correctness | `tests/test_queue.py::test_status_lists_jobs_with_attempts` |
| 29.25 | fast-w4 | call | B | no: serial | captured execution / runtime inventory (where invoked); correctness | `tests/test_frozen_execution.py::test_snapshot_damage_rejects_before_worker[missing]` |
| 28.22 | fast-w4 | call | B | no: serial | captured execution / runtime inventory (where invoked); correctness | `tests/test_queue.py::test_cpu_environment_refuses_cuda_job_without_execution` |
| 28.22 | fast-w4 | call | B | no: serial | captured execution / runtime inventory (where invoked); correctness | `tests/test_queue.py::test_submit_allows_explicit_duplicates` |
| 27.77 | fast-w4 | call | B | no: serial | captured execution / runtime inventory (where invoked); correctness | `tests/test_queue.py::test_submit_deduplicates_identical_plans` |
| 27.21 | fast-w4 | call | B | no: serial | captured execution / runtime inventory (where invoked); correctness | `tests/test_reports.py::test_cli_runs_list_empty_and_populated` |
| 25.45 | fast-w4 | call | B | no: serial | captured execution / runtime inventory (where invoked); correctness | `tests/test_frozen_execution.py::test_snapshot_damage_rejects_before_worker[extra]` |
| 22.98 | fast-w4 | call | A | yes, A | isolated correctness fixture / execution | `tests/test_acquisition_bounds.py::test_public_plan_fetch_status_verify_prepare` |
| 20.81 | fast-w4 | call | A | yes, A | isolated correctness fixture / execution | `tests/test_acquisition_bounds.py::test_nested_acquisition_consumes_parent_preparation_budget` |
| 20.50 | fast-w4 | call | A | yes, A | CLI startup and artifact construction; correctness | `tests/test_artifact_identity.py::test_actual_public_cli_current_and_legacy_artifacts` |
| 14.60 | fast-w4 | call | A | yes, A | captured execution / runtime inventory (where invoked); correctness | `tests/test_frozen_execution.py::test_snapshot_reuse_conflicts_and_paths` |
| 13.57 | fast-w4 | call | A | yes, A | isolated correctness fixture / execution | `tests/test_rowgroup_sampling.py::test_sampled_ranges_acquire_deterministically` |
| 13.07 | fast-w4 | call | B | no: serial | captured execution / runtime inventory (where invoked); correctness | `tests/test_prepare.py::test_failed_stage_stops_with_reason_and_state` |
| 10.53 | fast-w4 | call | A | yes, A | isolated correctness fixture / execution | `tests/test_mix01_views.py::test_data_adapt_refusals` |
| 8.91 | fast-w4 | call | A | yes, A | isolated correctness fixture / execution | `tests/test_recipes.py::test_readme_example_commands_are_implemented` |
| 8.67 | fast-w4 | call | A | yes, A | isolated correctness fixture / execution | `tests/test_acquisition_bounds.py::test_production_fetch_with_verified_admission` |
| 6.91 | fast-w4 | call | A | yes, A | CLI startup and artifact construction; correctness | `tests/test_models.py::test_cli_model_inspect_subprocess[300m]` |
| 6.67 | fast-w4 | call | A | yes, A | CLI startup and artifact construction; correctness | `tests/test_models.py::test_cli_model_inspect_subprocess[150m]` |
| 6.61 | fast-w4 | call | A | yes, A | CLI startup and artifact construction; correctness | `tests/test_acquisition_plan.py::test_cli_attempt_renewal_offline` |
| 6.51 | fast-w4 | call | A | yes, A | CLI startup and artifact construction; correctness | `tests/test_models.py::test_cli_model_inspect_subprocess[tiny]` |
