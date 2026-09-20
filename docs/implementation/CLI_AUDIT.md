# P23 registered CLI trace

Generated from the actual Typer command tree on 2026-09-19. Every row has a parametrized help test in `tests/test_cli_inventory.py`. Help passing proves registration and argument rendering, not execution semantics. The final column names relevant execution or domain tests and explicitly limits that evidence; it does not assert every option combination is tested. Paths are repository-relative. Final acceptance and production blockers are in [FINAL_ACCEPTANCE.md](FINAL_ACCEPTANCE.md).

87 command/group help surfaces.

The registration locations below are historical P23 line references. D01/D06/D03
remediation keeps the public command tree and updates execution evidence. Current
bounded Windows CPU coverage includes public artifact import/inspect/verify
([D01](reports/P23-D01.md)), captured workers/recovery ([D06](reports/P23-D06.md)),
and two-source prepare/tokenize/mixture-plan/direct-train/queued-train/fresh-resume
with actual nondefault components ([D03](reports/P23-D03.md)). The D03 `--smoke`
planning option retains C13 and production guards; full-size planning, acquisition,
official evaluation and deferred matched-tokenizer exposure remain open.

| Command | Registered callback location | Relevant execution evidence / limits |
|---|---|---|
| `xlm` | `src/xlm/cli/main.py:86` | test_cli.py |
| `xlm artifact` | `registered group` | group help only |
| `xlm artifact inspect` | `src/xlm/cli/artifact_cmd.py:43` | test_artifacts.py, test_ledger.py (domain); help only for CLI verify/rebuild/cleanup |
| `xlm artifact rebuild-ledger` | `src/xlm/cli/artifact_cmd.py:99` | test_artifacts.py, test_ledger.py (domain); help only for CLI verify/rebuild/cleanup |
| `xlm artifact verify` | `src/xlm/cli/artifact_cmd.py:79` | test_artifacts.py, test_ledger.py (domain); help only for CLI verify/rebuild/cleanup |
| `xlm campaign` | `registered group` | group help only |
| `xlm campaign plan` | `src/xlm/cli/experiment_cmd.py:262` | test_experiment_plans.py, test_queue.py, test_offline_workflow.py; D06 frozen execution VERIFIED in bounded Windows CPU scope; production gates remain |
| `xlm compare` | `src/xlm/cli/compare_cmd.py:59` | test_comparison.py, test_offline_workflow.py (authored evidence) |
| `xlm config` | `registered group` | group help only |
| `xlm config diff` | `src/xlm/cli/config_cmd.py:133` | test_config.py, test_cli.py, test_recipes.py |
| `xlm config resolve` | `src/xlm/cli/config_cmd.py:96` | test_config.py, test_cli.py, test_recipes.py |
| `xlm config schema` | `src/xlm/cli/config_cmd.py:158` | test_config.py, test_cli.py, test_recipes.py |
| `xlm config validate` | `src/xlm/cli/config_cmd.py:28` | test_config.py, test_cli.py, test_recipes.py |
| `xlm dashboard` | `src/xlm/cli/report_cmd.py:189` | test_reports.py, test_dashboard.py, test_offline_workflow.py |
| `xlm data` | `registered group` | group help only |
| `xlm data admit` | `src/xlm/cli/data_cmd.py:550` | test_data_catalog.py, test_data_adapters.py, test_cli_pool_freeze.py, test_cleaning_pipeline.py, test_dedup_engine.py, test_source_discovery.py, test_acquisition_fetcher.py (fixtures) |
| `xlm data audit` | `src/xlm/cli/data_cmd.py:474` | test_data_catalog.py, test_data_adapters.py, test_cli_pool_freeze.py, test_cleaning_pipeline.py, test_dedup_engine.py, test_source_discovery.py, test_acquisition_fetcher.py (fixtures) |
| `xlm data clean` | `src/xlm/cli/data_cmd.py:964` | test_data_catalog.py, test_data_adapters.py, test_cli_pool_freeze.py, test_cleaning_pipeline.py, test_dedup_engine.py, test_source_discovery.py, test_acquisition_fetcher.py (fixtures) |
| `xlm data dedup` | `src/xlm/cli/data_cmd.py:1200` | test_data_catalog.py, test_data_adapters.py, test_cli_pool_freeze.py, test_cleaning_pipeline.py, test_dedup_engine.py, test_source_discovery.py, test_acquisition_fetcher.py (fixtures) |
| `xlm data fetch` | `src/xlm/cli/data_cmd.py:793` | test_data_catalog.py, test_data_adapters.py, test_cli_pool_freeze.py, test_cleaning_pipeline.py, test_dedup_engine.py, test_source_discovery.py, test_acquisition_fetcher.py (fixtures) |
| `xlm data freeze` | `src/xlm/cli/data_cmd.py:1561` | test_data_catalog.py, test_data_adapters.py, test_cli_pool_freeze.py, test_cleaning_pipeline.py, test_dedup_engine.py, test_source_discovery.py, test_acquisition_fetcher.py (fixtures) |
| `xlm data import-local` | `src/xlm/cli/data_cmd.py:104` | test_data_catalog.py, test_data_adapters.py, test_cli_pool_freeze.py, test_cleaning_pipeline.py, test_dedup_engine.py, test_source_discovery.py, test_acquisition_fetcher.py (fixtures) |
| `xlm data mix01-status` | `src/xlm/cli/data_cmd.py:1724` | test_data_catalog.py, test_data_adapters.py, test_cli_pool_freeze.py, test_cleaning_pipeline.py, test_dedup_engine.py, test_source_discovery.py, test_acquisition_fetcher.py (fixtures) |
| `xlm data plan` | `src/xlm/cli/data_cmd.py:627` | test_data_catalog.py, test_data_adapters.py, test_cli_pool_freeze.py, test_cleaning_pipeline.py, test_dedup_engine.py, test_source_discovery.py, test_acquisition_fetcher.py (fixtures) |
| `xlm data pool` | `registered group` | group help only |
| `xlm data pool build` | `src/xlm/cli/data_cmd.py:1420` | test_data_catalog.py, test_data_adapters.py, test_cli_pool_freeze.py, test_cleaning_pipeline.py, test_dedup_engine.py, test_source_discovery.py, test_acquisition_fetcher.py (fixtures) |
| `xlm data pool inspect` | `src/xlm/cli/data_cmd.py:1497` | test_data_catalog.py, test_data_adapters.py, test_cli_pool_freeze.py, test_cleaning_pipeline.py, test_dedup_engine.py, test_source_discovery.py, test_acquisition_fetcher.py (fixtures) |
| `xlm data pool verify` | `src/xlm/cli/data_cmd.py:1523` | test_data_catalog.py, test_data_adapters.py, test_cli_pool_freeze.py, test_cleaning_pipeline.py, test_dedup_engine.py, test_source_discovery.py, test_acquisition_fetcher.py (fixtures) |
| `xlm data probe` | `src/xlm/cli/data_cmd.py:344` | test_data_catalog.py, test_data_adapters.py, test_cli_pool_freeze.py, test_cleaning_pipeline.py, test_dedup_engine.py, test_source_discovery.py, test_acquisition_fetcher.py (fixtures) |
| `xlm data quality-report` | `src/xlm/cli/data_cmd.py:1124` | test_data_catalog.py, test_data_adapters.py, test_cli_pool_freeze.py, test_cleaning_pipeline.py, test_dedup_engine.py, test_source_discovery.py, test_acquisition_fetcher.py (fixtures) |
| `xlm data sources` | `src/xlm/cli/data_cmd.py:299` | test_data_catalog.py, test_data_adapters.py, test_cli_pool_freeze.py, test_cleaning_pipeline.py, test_dedup_engine.py, test_source_discovery.py, test_acquisition_fetcher.py (fixtures) |
| `xlm data split` | `src/xlm/cli/data_cmd.py:1278` | test_data_catalog.py, test_data_adapters.py, test_cli_pool_freeze.py, test_cleaning_pipeline.py, test_dedup_engine.py, test_source_discovery.py, test_acquisition_fetcher.py (fixtures) |
| `xlm data status` | `src/xlm/cli/data_cmd.py:855` | test_data_catalog.py, test_data_adapters.py, test_cli_pool_freeze.py, test_cleaning_pipeline.py, test_dedup_engine.py, test_source_discovery.py, test_acquisition_fetcher.py (fixtures) |
| `xlm data tokenize` | `src/xlm/cli/data_cmd.py:1794` | test_data_catalog.py, test_data_adapters.py, test_cli_pool_freeze.py, test_cleaning_pipeline.py, test_dedup_engine.py, test_source_discovery.py, test_acquisition_fetcher.py (fixtures) |
| `xlm data verify` | `src/xlm/cli/data_cmd.py:904` | test_data_catalog.py, test_data_adapters.py, test_cli_pool_freeze.py, test_cleaning_pipeline.py, test_dedup_engine.py, test_source_discovery.py, test_acquisition_fetcher.py (fixtures) |
| `xlm demo` | `src/xlm/cli/demo_cmd.py:23` | test_cli_train_demo.py; standalone demo.log |
| `xlm doctor` | `src/xlm/cli/main.py:101` | test_cli.py |
| `xlm evaluate` | `src/xlm/cli/eval_cmd.py:278` | test_cli_eval_gen.py, test_eval_suites.py, test_harness_adapter.py; official remote loader BLOCKED |
| `xlm experiment` | `registered group` | group help only |
| `xlm experiment authorize` | `src/xlm/cli/experiment_cmd.py:94` | test_experiment_plans.py, test_queue.py, test_offline_workflow.py; D06 frozen execution VERIFIED in bounded Windows CPU scope; production gates remain |
| `xlm experiment plan` | `src/xlm/cli/experiment_cmd.py:40` | test_experiment_plans.py, test_queue.py, test_offline_workflow.py; D06 frozen execution VERIFIED in bounded Windows CPU scope; production gates remain |
| `xlm experiment submit` | `src/xlm/cli/experiment_cmd.py:125` | test_experiment_plans.py, test_queue.py, test_offline_workflow.py; D06 frozen execution VERIFIED in bounded Windows CPU scope; production gates remain |
| `xlm export` | `src/xlm/cli/export_cmd.py:13` | test_export.py, test_offline_workflow.py |
| `xlm final` | `registered group` | group help only |
| `xlm final execute` | `src/xlm/cli/final_cmd.py:57` | test_operator.py; real OS isolation NOT RUN |
| `xlm final request` | `src/xlm/cli/final_cmd.py:22` | test_operator.py; real OS isolation NOT RUN |
| `xlm final verify-receipt` | `src/xlm/cli/final_cmd.py:144` | test_operator.py; real OS isolation NOT RUN |
| `xlm generate` | `src/xlm/cli/generate_cmd.py:13` | test_generation.py, test_cli_eval_gen.py, test_export.py |
| `xlm generate-session` | `src/xlm/cli/generate_cmd.py:139` | test_generation.py, test_cli_eval_gen.py, test_export.py |
| `xlm maintenance` | `src/xlm/cli/prepare_cmd.py:121` | test_prepare.py, test_final_acceptance.py, test_offline_workflow.py |
| `xlm mixture` | `registered group` | group help only |
| `xlm mixture inspect` | `src/xlm/cli/mixture_cmd.py:320` | test_mixture_planning.py, test_offline_workflow.py; D03 public execution evidence in test_configurable_workflow.py; matched tokenizer exposure OPEN / DEFERRED |
| `xlm mixture matched-plan` | `src/xlm/cli/mixture_cmd.py:183` | test_mixture_planning.py projections only; cross-tokenizer matched execution OPEN / DEFERRED, not VERIFIED |
| `xlm mixture plan` | `src/xlm/cli/mixture_cmd.py:121` | test_mixture_planning.py, test_offline_workflow.py; D03 public execution evidence in test_configurable_workflow.py; matched tokenizer exposure OPEN / DEFERRED |
| `xlm mixture preset-diff` | `src/xlm/cli/mixture_cmd.py:288` | test_mixture_planning.py, test_offline_workflow.py; D03 public execution evidence in test_configurable_workflow.py; matched tokenizer exposure OPEN / DEFERRED |
| `xlm mixture preset-validate` | `src/xlm/cli/mixture_cmd.py:252` | test_mixture_planning.py, test_offline_workflow.py; D03 public execution evidence in test_configurable_workflow.py; matched tokenizer exposure OPEN / DEFERRED |
| `xlm mixture preview` | `src/xlm/cli/mixture_cmd.py:353` | test_mixture_planning.py, test_offline_workflow.py; D03 public execution evidence in test_configurable_workflow.py; matched tokenizer exposure OPEN / DEFERRED |
| `xlm mixture validate` | `src/xlm/cli/mixture_cmd.py:88` | test_mixture_planning.py, test_offline_workflow.py; D03 public execution evidence in test_configurable_workflow.py; matched tokenizer exposure OPEN / DEFERRED |
| `xlm model` | `registered group` | group help only |
| `xlm model inspect` | `src/xlm/cli/model_cmd.py:47` | test_models.py, test_cli.py |
| `xlm prepare` | `src/xlm/cli/prepare_cmd.py:26` | test_prepare.py, test_final_acceptance.py, test_offline_workflow.py |
| `xlm profile` | `src/xlm/cli/profile_cmd.py:13` | test_cuda_execution.py, test_cuda.py; real CUDA profile.json |
| `xlm promote` | `src/xlm/cli/compare_cmd.py:261` | test_comparison.py, test_offline_workflow.py (authored evidence) |
| `xlm queue` | `registered group` | group help only |
| `xlm queue cancel` | `src/xlm/cli/experiment_cmd.py:246` | test_experiment_plans.py, test_queue.py, test_offline_workflow.py; D06 frozen execution VERIFIED in bounded Windows CPU scope; production gates remain |
| `xlm queue run` | `src/xlm/cli/experiment_cmd.py:197` | test_experiment_plans.py, test_queue.py, test_offline_workflow.py; D06 frozen execution VERIFIED in bounded Windows CPU scope; production gates remain |
| `xlm queue status` | `src/xlm/cli/experiment_cmd.py:223` | test_experiment_plans.py, test_queue.py, test_offline_workflow.py; D06 frozen execution VERIFIED in bounded Windows CPU scope; production gates remain |
| `xlm release` | `registered group` | group help only |
| `xlm release audit` | `src/xlm/cli/final_cmd.py:174` | test_operator.py; real OS isolation NOT RUN |
| `xlm report` | `src/xlm/cli/report_cmd.py:37` | test_reports.py, test_dashboard.py, test_offline_workflow.py |
| `xlm research` | `registered group` | group help only |
| `xlm research check-plugin` | `src/xlm/cli/research_cmd.py:83` | test_research.py |
| `xlm research idea` | `registered group` | group help only |
| `xlm research idea new` | `src/xlm/cli/research_cmd.py:16` | test_research.py |
| `xlm research idea validate` | `src/xlm/cli/research_cmd.py:47` | test_research.py |
| `xlm research scaffold` | `src/xlm/cli/research_cmd.py:63` | test_research.py |
| `xlm resume` | `src/xlm/cli/train_cmd.py:316` | test_cli_train_demo.py, test_final_acceptance.py, test_offline_workflow.py |
| `xlm run` | `registered group` | group help only |
| `xlm run inspect` | `src/xlm/cli/train_cmd.py:20` | test_cli_train_demo.py, test_final_acceptance.py, test_offline_workflow.py |
| `xlm runs` | `registered group` | group help only |
| `xlm runs list` | `src/xlm/cli/report_cmd.py:149` | test_reports.py, test_dashboard.py, test_offline_workflow.py |
| `xlm tokenizer` | `registered group` | group help only |
| `xlm tokenizer encode` | `src/xlm/cli/tokenizer_cmd.py:191` | test_tokenizers.py, test_tokenizer_regime.py, test_offline_workflow.py |
| `xlm tokenizer inspect` | `src/xlm/cli/tokenizer_cmd.py:165` | test_tokenizers.py, test_tokenizer_regime.py, test_offline_workflow.py |
| `xlm tokenizer train` | `src/xlm/cli/tokenizer_cmd.py:55` | test_tokenizers.py, test_tokenizer_regime.py, test_offline_workflow.py |
| `xlm tokenizer verify` | `src/xlm/cli/tokenizer_cmd.py:224` | test_tokenizers.py, test_tokenizer_regime.py, test_offline_workflow.py |
| `xlm train` | `src/xlm/cli/train_cmd.py:84` | test_cli_train_demo.py, test_final_acceptance.py, test_offline_workflow.py |

