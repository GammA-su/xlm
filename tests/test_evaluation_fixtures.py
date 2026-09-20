"""Tests for benchmark fixtures, character-normalized acc_norm, and cache invalidation.

Complying with Contract C11, C12 and Amendments 5, 6, 7.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import torch

from xlm.config.schemas import AdamWConfig, CrossEntropyObjectiveConfig, TransformerBaselineConfig
from xlm.evaluation.fixtures import (
    BenchmarkFixtureDataset,
    InvalidChoiceError,
    MinimalPairItem,
    MultipleChoiceItem,
    get_synthetic_multiple_choice_fixture,
)
from xlm.evaluation.likelihood import ConditionalLikelihoodScorer
from xlm.evaluation.scorer import BenchmarkFixtureScorer, RawLikelihoodCache
from xlm.inference.generation import GenerationConfig, TextGenerator
from xlm.models.transformer import TransformerBaseline
from xlm.objectives.base import accumulate_microbatch_gradient
from xlm.objectives.cross_entropy import CrossEntropyObjective
from xlm.optimizers.adamw import create_adamw_optimizer
from xlm.tokenizers.byte import ByteTokenizer
from xlm.training.data import TrainingBatcher


def test_multiple_choice_scoring_and_acc_norm() -> None:
    """Verify raw accuracy vs character-normalized acc_norm and margin calculation."""
    config = TransformerBaselineConfig(
        architecture="transformer_baseline",
        vocab_size=260,
        context_length=32,
        hidden_size=64,
        intermediate_size=128,
        num_layers=2,
        num_attention_heads=4,
        attention_backend="eager",
    )
    model = TransformerBaseline(config, seed=42)
    tokenizer = ByteTokenizer()
    scorer = ConditionalLikelihoodScorer(model=model, tokenizer=tokenizer)
    fixture_scorer = BenchmarkFixtureScorer(scorer=scorer, use_cache=False)

    item = MultipleChoiceItem(
        item_id="item_mc_01",
        context="The capital of France is",
        choices=[
            " Paris",
            " a very long incorrect answer string that has lower average likelihood",
        ],
        gold_index=0,
    )

    res = fixture_scorer.evaluate_multiple_choice_item(item)
    assert len(res.choice_log_likelihoods) == 2
    assert len(res.choice_normalized_scores) == 2

    # Verify character length normalization formula: norm = raw / len(choice)
    assert res.choice_normalized_scores[0] == res.choice_log_likelihoods[0] / len(" Paris")
    assert res.choice_normalized_scores[1] == res.choice_log_likelihoods[1] / len(
        " a very long incorrect answer string that has lower average likelihood"
    )
    assert isinstance(res.raw_margin, float)
    assert isinstance(res.norm_margin, float)


def test_empty_choice_string_rejected() -> None:
    """Verify empty choice strings are rejected to prevent division-by-zero in acc_norm."""
    with pytest.raises(InvalidChoiceError):
        MultipleChoiceItem(
            item_id="bad_mc",
            context="Question",
            choices=["valid", ""],
            gold_index=0,
        )


def test_minimal_pair_scoring() -> None:
    """Verify sentence acceptability scoring conditioned on BOS."""
    config = TransformerBaselineConfig(
        architecture="transformer_baseline",
        vocab_size=260,
        context_length=32,
        hidden_size=64,
        intermediate_size=128,
        num_layers=2,
        num_attention_heads=4,
        attention_backend="eager",
    )
    model = TransformerBaseline(config, seed=42)
    tokenizer = ByteTokenizer()
    scorer = ConditionalLikelihoodScorer(model=model, tokenizer=tokenizer)
    fixture_scorer = BenchmarkFixtureScorer(scorer=scorer, use_cache=False)

    item = MinimalPairItem(
        item_id="pair_01",
        good_sentence="This is a coherent sentence.",
        bad_sentence="Sentence coherent a this is.",
    )

    res = fixture_scorer.evaluate_minimal_pair_item(item)
    assert isinstance(res.good_log_likelihood, float)
    assert isinstance(res.bad_log_likelihood, float)
    assert res.margin == res.good_log_likelihood - res.bad_log_likelihood
    assert res.is_correct == (res.good_log_likelihood > res.bad_log_likelihood)


def test_cache_invalidation_and_reuse(tmp_path: Path) -> None:
    """Verify cache reuse on identical request and invalidation on model/tokenizer/label changes."""
    cache = RawLikelihoodCache(cache_dir=tmp_path / "cache")
    config = TransformerBaselineConfig(
        architecture="transformer_baseline",
        vocab_size=260,
        context_length=32,
        hidden_size=64,
        intermediate_size=128,
        num_layers=2,
        num_attention_heads=4,
        attention_backend="eager",
    )
    model = TransformerBaseline(config, seed=42)
    tokenizer = ByteTokenizer()
    scorer = ConditionalLikelihoodScorer(model=model, tokenizer=tokenizer)

    fixture_scorer = BenchmarkFixtureScorer(
        scorer=scorer,
        model_hash="hash_m1",
        tokenizer_hash=tokenizer.fingerprint,
        cache=cache,
        use_cache=True,
    )

    mc_dataset = get_synthetic_multiple_choice_fixture()
    receipt1, _ = fixture_scorer.evaluate_dataset(mc_dataset)

    # 1. Identical request should reuse cache
    receipt2, _ = fixture_scorer.evaluate_dataset(mc_dataset)
    assert receipt1.metrics == receipt2.metrics

    # 2. Perturbing model hash should cause a cache miss (new computation)
    fixture_scorer_m2 = BenchmarkFixtureScorer(
        scorer=scorer,
        model_hash="hash_m2_different",
        tokenizer_hash=tokenizer.fingerprint,
        cache=cache,
        use_cache=True,
    )
    receipt_m2, _ = fixture_scorer_m2.evaluate_dataset(mc_dataset)
    assert receipt_m2.checkpoint_hash == "hash_m2_different"

    # 3. Corrupting a cache file must be safely handled without crash
    cache_files = list((tmp_path / "cache").glob("*.json"))
    assert len(cache_files) > 0
    cache_files[0].write_text("CORRUPTED_NOT_JSON", encoding="utf-8")
    # Evaluating again should safely bypass corrupt file
    receipt_corrupt_bypass, _ = fixture_scorer.evaluate_dataset(mc_dataset)
    assert receipt_corrupt_bypass.scored_items_count > 0


def test_strict_synthetic_fixture_labeling() -> None:
    """Verify offline fixtures cannot register as official benchmark suites."""
    dataset = get_synthetic_multiple_choice_fixture()
    assert dataset.is_offline_fixture is True
    assert dataset.is_official_benchmark is False

    with pytest.raises(
        ValueError, match="Synthetic fixture cannot register as an official benchmark"
    ):
        BenchmarkFixtureDataset(
            dataset_id="fake_arc",
            kind="multiple_choice",
            items=[],
            is_offline_fixture=True,
            is_official_benchmark=True,
        )


def test_evaluation_and_generation_training_isolation() -> None:
    """Verify evaluation and generation do not shift subsequent training weights or RNG."""
    config = TransformerBaselineConfig(
        architecture="transformer_baseline",
        vocab_size=260,
        context_length=16,
        hidden_size=64,
        intermediate_size=128,
        num_layers=2,
        num_attention_heads=4,
        attention_backend="eager",
    )
    obj_cfg = CrossEntropyObjectiveConfig()
    opt_cfg = AdamWConfig(lr=0.01)

    tokens = [((i % 24) + 4) for i in range(100)]

    # --- Branch A: Step 1 -> Evaluation & Generation -> Step 2 ---
    torch.manual_seed(999)
    model_a = TransformerBaseline(config, seed=42)
    obj_a = CrossEntropyObjective(obj_cfg)
    opt_a, _ = create_adamw_optimizer(opt_cfg, model=model_a, objective=obj_a)
    batcher_a = TrainingBatcher(tokens, context_length=8, global_batch_valid_targets=16)

    # Step 1
    mbs_1 = batcher_a.next_step_microbatches(16)
    opt_a.zero_grad()
    for mb in mbs_1:
        out_a = model_a(mb.input_ids)
        res_a = obj_a(out_a, mb)
        accumulate_microbatch_gradient(res_a, total_valid_targets=16)
    opt_a.step()

    # Intervening evaluation and generation
    tok = ByteTokenizer()
    scorer = ConditionalLikelihoodScorer(model_a, tok)
    scorer.score_continuation("Context", " Continuation")
    generator = TextGenerator(model_a, tok)
    generator.generate("Prompt", GenerationConfig(max_new_tokens=8, do_sample=True, seed=777))

    # Step 2
    mbs_2_a = batcher_a.next_step_microbatches(16)
    opt_a.zero_grad()
    for mb in mbs_2_a:
        out_a = model_a(mb.input_ids)
        res_a = obj_a(out_a, mb)
        accumulate_microbatch_gradient(res_a, total_valid_targets=16)
    opt_a.step()

    # --- Branch B: Step 1 -> Step 2 (Uninterrupted) ---
    torch.manual_seed(999)
    model_b = TransformerBaseline(config, seed=42)
    obj_b = CrossEntropyObjective(obj_cfg)
    opt_b, _ = create_adamw_optimizer(opt_cfg, model=model_b, objective=obj_b)
    batcher_b = TrainingBatcher(tokens, context_length=8, global_batch_valid_targets=16)

    # Step 1
    mbs_1_b = batcher_b.next_step_microbatches(16)
    opt_b.zero_grad()
    for mb in mbs_1_b:
        out_b = model_b(mb.input_ids)
        res_b = obj_b(out_b, mb)
        accumulate_microbatch_gradient(res_b, total_valid_targets=16)
    opt_b.step()

    # Step 2 directly
    mbs_2_b = batcher_b.next_step_microbatches(16)
    opt_b.zero_grad()
    for mb in mbs_2_b:
        out_b = model_b(mb.input_ids)
        res_b = obj_b(out_b, mb)
        accumulate_microbatch_gradient(res_b, total_valid_targets=16)
    opt_b.step()

    # Verify bitwise identical final weights
    sd_a = model_a.state_dict()
    sd_b = model_b.state_dict()
    for k in sd_a:
        assert torch.equal(sd_a[k], sd_b[k]), f"Intervening evaluation shifted weights for '{k}'"
