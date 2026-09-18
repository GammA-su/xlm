# Prompt order

| Prompt | Milestone |
|---|---|
| 00 | [foundation](prompts/00_foundation.md) |
| 01 | [contracts config artifacts](prompts/01_contracts_config_artifacts.md) |
| 02 | [local data tokenizers](prompts/02_local_data_tokenizers.md) |
| 03 | [reference models](prompts/03_reference_models.md) |
| 04 | [losses optimizers schedules](prompts/04_losses_optimizers_schedules.md) |
| 05 | [training checkpoint resume](prompts/05_training_checkpoint_resume.md) |
| 06 | [native scoring and generation](prompts/06_native_scoring_and_generation.md) |
| 07 | [source discovery admission](prompts/07_source_discovery_admission.md) |
| 08 | [bounded acquisition](prompts/08_bounded_acquisition.md) |
| 09 | [cleaning quality pipeline](prompts/09_cleaning_quality_pipeline.md) |
| 10 | [dedup splits exclusions](prompts/10_dedup_splits_exclusions.md) |
| 11 | [pool freeze tokenizer regime](prompts/11_pool_freeze_tokenizer_regime.md) |
| 12 | [token shards mixture packing](prompts/12_token_shards_mixture_packing.md) |
| 13 | [real dataset views and mix01](prompts/13_real_dataset_views_and_mix01.md) |
| 14 | [cuda profile and performance](prompts/14_cuda_profile_and_performance.md) |
| 15 | [official evaluation harness](prompts/15_official_evaluation_harness.md) |
| 16 | [experiment plans and queue](prompts/16_experiment_plans_and_queue.md) |
| 17 | [statistics comparisons promotion](prompts/17_statistics_comparisons_promotion.md) |
| 18 | [research plugins and idea cards](prompts/18_research_plugins_and_idea_cards.md) |
| 19 | [reports and dashboard](prompts/19_reports_and_dashboard.md) |
| 20 | [export generation portability](prompts/20_export_generation_portability.md) |
| 21 | [isolation security release](prompts/21_isolation_security_release.md) |
| 22 | [campaign bootstrap and runbooks](prompts/22_campaign_bootstrap_and_runbooks.md) |
| 23 | [independent final acceptance](prompts/23_independent_final_acceptance.md) |

Execute sequentially in one repository. Start a fresh agent session whenever useful, but make it read the shared contracts and persistent status. The first seven prompts produce a real offline vertical slice before the heavy real-data and research orchestration work.
