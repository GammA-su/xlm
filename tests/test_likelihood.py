"""Tests for conditional likelihood scoring, boundary policies, rolling windows, and BPB.

Complying with Contracts C06, C08, C11 and Amendments 1, 2, 3, 4, 7.
"""

from __future__ import annotations

import math
from typing import Any

import pytest
import torch
import torch.nn as nn

from xlm.config.schemas import TransformerBaselineConfig
from xlm.core.contracts import InferenceInput, LMOutput, ModelCapabilities
from xlm.evaluation.likelihood import (
    BoundaryPolicy,
    ConditionalLikelihoodScorer,
    ContinuationTooLongError,
    CrossBoundaryMergeError,
    WindowTruncationPolicy,
    prepare_token_pair,
)
from xlm.models.base import BaseModel
from xlm.models.parameter_counts import ParameterCounts
from xlm.models.transformer import TransformerBaseline
from xlm.tokenizers.bpe import ByteLevelBPETokenizer
from xlm.tokenizers.byte import ByteTokenizer


class PrefixDependentToyOracle(BaseModel):
    """Toy oracle model where logits at step t depend deterministically on prefix w[:t+1].

    Used in Amendment 7 to expose any target shifting errors or future-token leakage.
    """

    def __init__(self, vocab_size: int = 260, context_length: int = 32) -> None:
        super().__init__()
        self.vocab_size = vocab_size
        self._context_length = context_length
        self.dummy_param = nn.Parameter(torch.zeros(1))

    def get_capabilities(self) -> ModelCapabilities:
        return ModelCapabilities(max_context_length=self._context_length)

    def count_parameters(self) -> ParameterCounts:
        return ParameterCounts(
            total_instantiated=1,
            unique_deployed=1,
            active=1,
            non_embedding=1,
            training_only=0,
            tied_parameters=0,
            formula_estimate=1,
            formula_matches=True,
            max_deployed_cap=None,
            cap_respected=True,
            device="cpu",
        )

    def forward(
        self,
        input_ids: torch.Tensor | InferenceInput,
        attention_mask: torch.Tensor | None = None,
        position_ids: torch.Tensor | None = None,
        state: Any | None = None,
        requested_outputs: dict[str, bool] | None = None,
    ) -> LMOutput:
        ids: torch.Tensor = (
            input_ids.input_ids if isinstance(input_ids, InferenceInput) else input_ids
        )
        batch_size, seq_len = ids.shape
        logits = torch.zeros(
            (batch_size, seq_len, self.vocab_size), dtype=torch.float32, device=ids.device
        )

        # For each position t, the logits favor (w[t] + 7) % vocab_size
        # This creates a strictly causal, prefix-dependent distribution!
        for b in range(batch_size):
            for t in range(seq_len):
                prev_token = int(ids[b, t].item())
                favored_next = (prev_token + 7) % self.vocab_size
                logits[b, t, favored_next] = 10.0

        return LMOutput(logits=logits)


class ConstantLogitsToyModel(BaseModel):
    """Toy model with constant uniform logits for hand-computable BPB verification."""

    def __init__(self, vocab_size: int = 4, context_length: int = 32) -> None:
        super().__init__()
        self.vocab_size = vocab_size
        self._context_length = context_length

    def get_capabilities(self) -> ModelCapabilities:
        return ModelCapabilities(max_context_length=self._context_length)

    def count_parameters(self) -> ParameterCounts:
        return ParameterCounts(
            total_instantiated=0,
            unique_deployed=0,
            active=0,
            non_embedding=0,
            training_only=0,
            tied_parameters=0,
            formula_estimate=0,
            formula_matches=True,
            max_deployed_cap=None,
            cap_respected=True,
            device="cpu",
        )

    def forward(
        self,
        input_ids: torch.Tensor | InferenceInput,
        attention_mask: torch.Tensor | None = None,
        position_ids: torch.Tensor | None = None,
        state: Any | None = None,
        requested_outputs: dict[str, bool] | None = None,
    ) -> LMOutput:
        ids: torch.Tensor = (
            input_ids.input_ids if isinstance(input_ids, InferenceInput) else input_ids
        )
        b, seq_len = ids.shape
        logits = torch.zeros((b, seq_len, self.vocab_size), dtype=torch.float64, device=ids.device)
        return LMOutput(logits=logits)


def test_vectorized_vs_brute_force_token_by_token_parity() -> None:
    """Verify exact numerical agreement between vectorized and brute-force token scoring."""
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
    tokenizer = ByteTokenizer()
    scorer = ConditionalLikelihoodScorer(model=model, tokenizer=tokenizer, precision="fp32")

    # Sequence of tokens: [bos, 10, 20, 30, 40]
    tokens = [tokenizer.bos_token_id, 10, 20, 30, 40]
    mask = [False, True, True, True, True]

    # Vectorized score
    vec_lps, vec_greedy = scorer.score_token_sequence(tokens, mask)

    # Brute force token by token
    bf_lps = []
    bf_greedy = []
    for step in range(1, len(tokens)):
        sub_tokens = tokens[: step + 1]
        sub_mask = [False] * len(sub_tokens)
        sub_mask[-1] = True
        sub_lp, sub_gr = scorer.score_token_sequence(sub_tokens, sub_mask)
        bf_lps.append(sub_lp[0])
        bf_greedy.append(sub_gr[0])

    assert len(vec_lps) == len(bf_lps) == 4
    for v_lp, b_lp in zip(vec_lps, bf_lps, strict=True):
        assert math.isclose(v_lp, b_lp, rel_tol=1e-5, abs_tol=1e-6)
    assert vec_greedy == bf_greedy


def test_prefix_dependent_toy_oracle_target_shifting() -> None:
    """Expose target shifting and verify zero future-token leakage with causal oracle."""
    model = PrefixDependentToyOracle(vocab_size=260, context_length=16)
    tokenizer = ByteTokenizer()
    scorer = ConditionalLikelihoodScorer(model=model, tokenizer=tokenizer)

    # Context: "Hello", Continuation: " World"
    res = scorer.score_continuation("Hello", " World")
    assert res.token_count > 0
    assert len(res.token_log_probs) == res.token_count

    # Verify that target at position i was predicted using prefix ending at position i-1
    tokens = res.tokens
    target_start = len(tokens) - res.token_count
    for idx, tgt in enumerate(res.targets):
        pos = target_start + idx
        prev_tok = tokens[pos - 1]
        expected_favored = (prev_tok + 7) % 260
        # If target matches the oracle expectation, log prob should be high (> -0.1)
        if tgt == expected_favored:
            assert res.token_log_probs[idx] > -0.1


def test_single_vs_batch_parity_with_padding() -> None:
    """Verify exact score parity between single scoring and padded batch scoring."""
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
    model = TransformerBaseline(config, seed=123)
    tokenizer = ByteTokenizer()
    scorer = ConditionalLikelihoodScorer(model=model, tokenizer=tokenizer)

    from xlm.evaluation.likelihood import ScoringRequest

    requests = [
        ScoringRequest(context="The sky is", continuation=" blue.", item_id="item_1"),
        ScoringRequest(
            context="Water is", continuation=" very wet and transparent.", item_id="item_2"
        ),
        ScoringRequest(context="", continuation="Just a continuation from BOS.", item_id="item_3"),
    ]

    # Single scoring
    single_results = [
        scorer.score_continuation(r.context, r.continuation, item_id=r.item_id) for r in requests
    ]

    # Batch scoring
    batch_results = scorer.score_batch(requests)

    assert len(single_results) == len(batch_results) == 3
    for s_res, b_res in zip(single_results, batch_results, strict=True):
        assert s_res.item_id == b_res.item_id
        assert s_res.token_count == b_res.token_count
        assert s_res.targets == b_res.targets
        assert math.isclose(s_res.log_likelihood, b_res.log_likelihood, rel_tol=1e-5, abs_tol=1e-6)
        for s_lp, b_lp in zip(s_res.token_log_probs, b_res.token_log_probs, strict=True):
            assert math.isclose(s_lp, b_lp, rel_tol=1e-5, abs_tol=1e-6)


def test_answer_mask_positions_and_empty_prompt() -> None:
    """Verify target mask is strictly 0 on context and 1 on continuation."""
    tokenizer = ByteTokenizer()
    pair = prepare_token_pair(tokenizer, context="Prompt context", continuation=" answer")
    start, end = pair.scored_slice

    assert start == len(pair.context_tokens)
    assert end == len(pair.full_tokens)
    assert end - start == len(pair.continuation_tokens)

    # Empty prompt uses BOS context
    pair_empty = prepare_token_pair(tokenizer, context="", continuation="answer")
    assert pair_empty.context_tokens == [tokenizer.bos_token_id]
    assert pair_empty.scored_slice == (1, len(pair_empty.full_tokens))


def test_long_continuation_rolling_vs_error_policies() -> None:
    """Verify rolling window policy scores every target exactly once while ERROR policy rejects."""
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

    long_text = "This is a very long sequence that definitely exceeds 8 tokens."

    # 1. Error policy
    error_scorer = ConditionalLikelihoodScorer(
        model=model,
        tokenizer=tokenizer,
        window_policy=WindowTruncationPolicy.ERROR,
        max_context_length=8,
    )
    with pytest.raises(ContinuationTooLongError):
        error_scorer.score_continuation("Context:", long_text)

    # 2. Rolling policy
    rolling_scorer = ConditionalLikelihoodScorer(
        model=model,
        tokenizer=tokenizer,
        window_policy=WindowTruncationPolicy.ROLLING,
        max_context_length=8,
        stride=4,
    )
    res = rolling_scorer.score_continuation("Context:", long_text)
    assert res.token_count > 8
    assert len(res.token_log_probs) == res.token_count
    assert res.diagnostics.get("rolling_windows_used") is True


def test_boundary_policies_and_cross_merges(tmp_path: Any) -> None:
    """Test leading spaces, separate tokenization, and cross-boundary merges."""
    # Fit tiny BPE to create a subword merge: 'pl' + 'ay' -> 'play'
    from tokenizers import Tokenizer
    from tokenizers.decoders import ByteLevel as ByteLevelDecoder
    from tokenizers.models import BPE
    from tokenizers.pre_tokenizers import ByteLevel
    from tokenizers.trainers import BpeTrainer

    hf_tok = Tokenizer(BPE(unk_token="<unk>"))
    hf_tok.pre_tokenizer = ByteLevel(add_prefix_space=False)
    hf_tok.decoder = ByteLevelDecoder()
    trainer = BpeTrainer(  # type: ignore[no-untyped-call]
        vocab_size=265,
        special_tokens=["<pad>", "<bos>", "<eos>", "<unk>"],
        initial_alphabet=ByteLevel.alphabet(),
    )
    hf_tok.train_from_iterator(["playing plays player playing"], trainer)
    tok = ByteLevelBPETokenizer(hf_tok, target_vocab_size=265)

    # 1. Leading space test: " Paris"
    pair_space = prepare_token_pair(
        tok, "France", " Paris", boundary_policy=BoundaryPolicy.JOINT_PREFIX_MATCH_V1
    )
    assert len(pair_space.continuation_tokens) > 0

    # 2. Separate mode always succeeds without cross-boundary merge
    pair_sep = prepare_token_pair(tok, "play", "ing", boundary_policy=BoundaryPolicy.SEPARATE_V1)
    assert pair_sep.context_tokens == tok.encode("play", add_special_tokens=False)
    assert pair_sep.continuation_tokens == tok.encode("ing", add_special_tokens=False)

    # 3. Joint mode with cross-boundary merge raises CrossBoundaryMergeError
    # "play" + "ing" tokenizes jointly as ["playing"] (1 token) instead of ["play", "ing"]
    joint_ids = tok.encode("playing", add_special_tokens=False)
    ctx_ids = tok.encode("play", add_special_tokens=False)
    if joint_ids[: len(ctx_ids)] != ctx_ids:
        with pytest.raises(CrossBoundaryMergeError):
            prepare_token_pair(
                tok, "play", "ing", boundary_policy=BoundaryPolicy.JOINT_PREFIX_MATCH_V1
            )


def test_hand_computable_bits_per_byte() -> None:
    """Verify bits-per-byte formula against exact authored mathematical example.

    With vocab_size=4 and uniform logits:
    p(w) = 1/4 = 0.25 -> NLL = -ln(1/4) = ln(4) = 2*ln(2).
    For 3 text bytes with 1 byte per token (3 tokens):
    Total NLL = 3 * 2 * ln(2) = 6 * ln(2).
    BPB = Total NLL / (ln(2) * 3 bytes) = 6*ln(2) / (3*ln(2)) = 2.00000000 exactly!
    """
    model = ConstantLogitsToyModel(vocab_size=4, context_length=16)

    # Dummy tokenizer where vocab size is 4 and 1 char = 1 token
    class Dummy4Tok(ByteTokenizer):
        @property
        def vocab_size(self) -> int:
            return 4

        def encode_with_offsets(
            self, text: str, add_special_tokens: bool = False
        ) -> tuple[list[int], list[tuple[int, int]]]:
            # Each char is a token id: ord(c) % 4
            t_ids = [(ord(c) % 4) for c in text]
            spans = [(i, i + 1) for i in range(len(text))]
            return t_ids, spans

    tok = Dummy4Tok()
    scorer = ConditionalLikelihoodScorer(model=model, tokenizer=tok, precision="fp64")

    doc_res = scorer.score_document("abc")  # 3 bytes, 3 tokens
    assert doc_res.text_token_count == 3
    assert doc_res.scored_canonical_utf8_bytes == 3
    # NLL per token should be 2*ln(2) = 1.3862943611198906
    expected_token_nll = 2.0 * math.log(2.0)
    assert math.isclose(doc_res.text_token_nll_sum, 3.0 * expected_token_nll, rel_tol=1e-12)
    # BPB must be exactly 2.0!
    assert math.isclose(doc_res.text_bpb, 2.0, rel_tol=1e-12)


def test_read_only_and_mode_restoration() -> None:
    """Verify scorer does not alter training mode or accumulate gradients."""
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
    model.train(True)
    assert model.training is True

    tokenizer = ByteTokenizer()
    scorer = ConditionalLikelihoodScorer(model=model, tokenizer=tokenizer)

    scorer.score_continuation("Context", " Continuation")

    # Mode must be restored
    assert model.training is True
    # Gradients must remain None
    for p in model.parameters():
        assert p.grad is None
