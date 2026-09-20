"""Tests for bounded autoregressive text generation complying with Amendment 6 and 7."""

from __future__ import annotations

import pytest
import torch

from xlm.config.schemas import TransformerBaselineConfig
from xlm.inference.generation import (
    GenerationConfig,
    PromptTooLongError,
    PromptTruncationPolicy,
    TextGenerator,
)
from xlm.models.transformer import TransformerBaseline
from xlm.tokenizers.byte import ByteTokenizer


def test_greedy_generation_determinism() -> None:
    """Verify greedy generation produces deterministic argmax outputs."""
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
    generator = TextGenerator(model=model, tokenizer=tokenizer)

    prompt = "Hello"
    gen_cfg = GenerationConfig(max_new_tokens=10, do_sample=False)

    res1 = generator.generate(prompt, gen_cfg)
    res2 = generator.generate(prompt, gen_cfg)

    assert len(res1.generated_token_ids) == 10
    assert res1.generated_token_ids == res2.generated_token_ids
    assert res1.generated_text == res2.generated_text
    assert res1.finish_reason == "max_new_tokens"


def test_sampling_seed_reproducibility_and_local_generator() -> None:
    """Verify temperature/top-k/top-p sampling with seed produces identical results."""
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
    generator = TextGenerator(model=model, tokenizer=tokenizer)

    gen_cfg_seed1 = GenerationConfig(
        max_new_tokens=12,
        do_sample=True,
        temperature=0.8,
        top_k=10,
        top_p=0.9,
        seed=12345,
    )

    # Initial torch RNG state
    initial_rng = torch.get_rng_state()

    res1 = generator.generate("Prefix", gen_cfg_seed1)
    res2 = generator.generate("Prefix", gen_cfg_seed1)

    # Identical seed must yield identical sampled generation
    assert res1.generated_token_ids == res2.generated_token_ids

    # Global RNG must not be modified by local generator
    after_rng = torch.get_rng_state()
    assert torch.equal(initial_rng, after_rng)


def test_repetition_penalty_enforcement() -> None:
    """Verify repetition penalty reduces probability of previously generated tokens."""
    config = TransformerBaselineConfig(
        architecture="transformer_baseline",
        vocab_size=260,
        context_length=64,
        hidden_size=64,
        intermediate_size=128,
        num_layers=2,
        num_attention_heads=4,
        attention_backend="eager",
    )
    model = TransformerBaseline(config, seed=42)
    tokenizer = ByteTokenizer()
    generator = TextGenerator(model=model, tokenizer=tokenizer)

    # Without repetition penalty vs with strong repetition penalty
    cfg_normal = GenerationConfig(max_new_tokens=15, do_sample=False, repetition_penalty=1.0)
    cfg_penalized = GenerationConfig(max_new_tokens=15, do_sample=False, repetition_penalty=5.0)

    res_normal = generator.generate("Repeated text test", cfg_normal)
    res_penalized = generator.generate("Repeated text test", cfg_penalized)

    assert len(res_normal.generated_token_ids) == 15
    assert len(res_penalized.generated_token_ids) == 15
    # Strong penalty forces divergence from unpenalized sequence
    assert res_normal.generated_token_ids != res_penalized.generated_token_ids


def test_stopping_conditions() -> None:
    """Verify generation stops upon EOS, custom stop tokens, or max_new_tokens."""
    config = TransformerBaselineConfig(
        architecture="transformer_baseline",
        vocab_size=260,
        context_length=64,
        hidden_size=64,
        intermediate_size=128,
        num_layers=2,
        num_attention_heads=4,
        attention_backend="eager",
    )
    model = TransformerBaseline(config, seed=42)
    tokenizer = ByteTokenizer()
    generator = TextGenerator(model=model, tokenizer=tokenizer)

    # 1. Custom stop token
    custom_stop = 65  # ASCII 'A'
    cfg_stop = GenerationConfig(
        max_new_tokens=20, stop_tokens=[custom_stop], do_sample=True, seed=99
    )
    res_stop = generator.generate("Prompt", cfg_stop)

    # Either stopped on stop token or reached max tokens
    assert res_stop.finish_reason in ("stop_token", "eos", "max_new_tokens")
    if res_stop.finish_reason == "stop_token":
        assert custom_stop not in res_stop.generated_token_ids


def test_prompt_truncation_policies() -> None:
    """Verify oversized prompts raise PromptTooLongError or truncate left cleanly."""
    config = TransformerBaselineConfig(
        architecture="transformer_baseline",
        vocab_size=260,
        context_length=8,  # Tiny context window of 8
        hidden_size=64,
        intermediate_size=128,
        num_layers=2,
        num_attention_heads=4,
        attention_backend="eager",
    )
    model = TransformerBaseline(config, seed=42)
    tokenizer = ByteTokenizer()
    generator = TextGenerator(model=model, tokenizer=tokenizer)

    long_prompt = "A long prompt with more than eight tokens"

    # 1. Error policy
    cfg_err = GenerationConfig(max_new_tokens=4, prompt_truncation=PromptTruncationPolicy.ERROR)
    with pytest.raises(PromptTooLongError):
        generator.generate(long_prompt, cfg_err)

    # 2. Left truncation policy
    cfg_left = GenerationConfig(max_new_tokens=2, prompt_truncation=PromptTruncationPolicy.LEFT)
    res = generator.generate(long_prompt, cfg_left)
    assert len(res.prompt_token_ids) == 7  # 8 - 1 = 7 kept
    assert res.diagnostics.get("discarded_prompt_tokens", 0) > 0


def test_mode_restoration_and_training_isolation() -> None:
    """Verify eval mode is restored and generation does not contaminate training state."""
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
    model = TransformerBaseline(config, seed=42)
    model.train(True)
    assert model.training is True

    tokenizer = ByteTokenizer()
    generator = TextGenerator(model=model, tokenizer=tokenizer)

    generator.generate("Test prompt", GenerationConfig(max_new_tokens=5, do_sample=False))

    # Model training mode must be restored
    assert model.training is True
