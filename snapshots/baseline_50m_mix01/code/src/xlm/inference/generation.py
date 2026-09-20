"""Bounded autoregressive text generation complying with Contracts C08, C11 and Amendment 6.

Key Design & Invariants:
- Base-model text completion (not an instruction-following chatbot).
- Independent read-only execution: sets model.eval(), executes under torch.no_grad(),
  and restores caller's prior training state in finally:.
- Local device-compatible RNG generator: preserves caller and global RNG state.
- Strictly never divides by zero on greedy paths.
- Repetition penalty, temperature scaling, top-k filtering, and nucleus top-p sampling.
- Full context and bounded output limits without silent token omission.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

import torch
import torch.nn.functional as F

from xlm.core.contracts import InferenceInput
from xlm.data.normalization import canonical_normalize
from xlm.models.base import BaseModel
from xlm.tokenizers.base import BaseTokenizer


class PromptTruncationPolicy(StrEnum):
    """Policy when prompt exceeds model context length."""

    ERROR = "error"
    LEFT = "left"


class PromptTooLongError(ValueError):
    """Raised when prompt exceeds model context length and policy is ERROR."""


@dataclass(frozen=True)
class GenerationConfig:
    """Strict configuration for bounded autoregressive generation."""

    max_new_tokens: int = 64
    temperature: float = 1.0
    do_sample: bool = False
    top_k: int = 0
    top_p: float = 1.0
    repetition_penalty: float = 1.0
    stop_tokens: list[int] | None = None
    seed: int | None = None
    prompt_truncation: PromptTruncationPolicy = PromptTruncationPolicy.ERROR

    def __post_init__(self) -> None:
        if self.max_new_tokens <= 0:
            raise ValueError(f"max_new_tokens must be > 0, got {self.max_new_tokens}")
        if self.max_new_tokens > 512:
            raise ValueError(f"max_new_tokens exceeds bound of 512: got {self.max_new_tokens}")
        if self.temperature < 0.0:
            raise ValueError(f"temperature cannot be negative, got {self.temperature}")
        if self.top_k < 0:
            raise ValueError(f"top_k cannot be negative, got {self.top_k}")
        if not (0.0 < self.top_p <= 1.0):
            raise ValueError(f"top_p must be in (0.0, 1.0], got {self.top_p}")
        if self.repetition_penalty < 1.0:
            raise ValueError(f"repetition_penalty must be >= 1.0, got {self.repetition_penalty}")


@dataclass(frozen=True)
class GenerationResult:
    """Complete result of an autoregressive generation pass."""

    prompt: str
    generated_text: str
    full_text: str
    prompt_token_ids: list[int]
    generated_token_ids: list[int]
    finish_reason: str  # 'eos' | 'stop_token' | 'max_new_tokens' | 'context_limit'
    diagnostics: dict[str, Any] = field(default_factory=dict)


class TextGenerator:
    """Stateless autoregressive text completion generator."""

    def __init__(
        self,
        model: BaseModel,
        tokenizer: BaseTokenizer,
        device: str = "cpu",
    ) -> None:
        self.model = model
        self.tokenizer = tokenizer
        self.device = device

        caps_max = None
        if hasattr(model, "get_capabilities"):
            try:
                caps_max = getattr(model.get_capabilities(), "max_context_length", None)
            except Exception:
                pass
        if caps_max is None:
            caps = getattr(model, "capabilities", None)
            caps_max = getattr(caps, "max_context_length", None)
        if caps_max is None and hasattr(model, "config"):
            caps_max = getattr(model.config, "context_length", None)

        self.max_context_length = caps_max or 512

    def generate(
        self,
        prompt: str | list[int],
        config: GenerationConfig | None = None,
    ) -> GenerationResult:
        """Generate text continuation for a prompt complying with Amendment 6."""
        cfg = config or GenerationConfig()
        diagnostics: dict[str, Any] = {}

        # 1. Prepare prompt tokens
        if isinstance(prompt, str):
            prompt_str = prompt
            if prompt == "":
                prompt_tokens = [self.tokenizer.bos_token_id]
                diagnostics["empty_prompt_bos"] = True
            else:
                prompt_tokens = self.tokenizer.encode(
                    canonical_normalize(prompt), add_special_tokens=False
                )
        else:
            prompt_tokens = list(prompt)
            prompt_str = self.tokenizer.decode(prompt_tokens)

        # 2. Check prompt length against model context window
        p_len = len(prompt_tokens)
        if p_len >= self.max_context_length:
            if cfg.prompt_truncation == PromptTruncationPolicy.ERROR:
                raise PromptTooLongError(
                    f"Prompt length {p_len} reaches or exceeds max context length "
                    f"{self.max_context_length} under PromptTruncationPolicy.ERROR."
                )
            # Left truncation: keep the rightmost tokens so there is at least 1 slot to generate
            keep_len = self.max_context_length - 1
            discarded = p_len - keep_len
            prompt_tokens = prompt_tokens[-keep_len:]
            diagnostics["discarded_prompt_tokens"] = discarded

        # 3. Setup local random generator
        local_gen: torch.Generator | None = None
        if cfg.seed is not None:
            # torch.Generator supports CPU device string or torch.device
            dev_type = "cuda" if "cuda" in self.device else "cpu"
            local_gen = torch.Generator(device=dev_type)
            local_gen.manual_seed(cfg.seed)

        # 4. Mode management: eval mode, no-grad, restore caller state in finally
        was_training = self.model.training
        self.model.eval()

        generated_tokens: list[int] = []
        current_tokens = list(prompt_tokens)
        finish_reason = "max_new_tokens"

        stop_token_set = set(cfg.stop_tokens or [])
        stop_token_set.add(self.tokenizer.eos_token_id)

        try:
            with torch.no_grad():
                for _ in range(cfg.max_new_tokens):
                    cur_len = len(current_tokens)
                    if cur_len >= self.max_context_length:
                        finish_reason = "context_limit"
                        break

                    # Forward current sequence through model
                    input_tensor = torch.tensor(
                        [current_tokens], dtype=torch.long, device=self.device
                    )
                    inf_input = InferenceInput(input_ids=input_tensor)
                    lm_out = self.model(inf_input)

                    # Next-token logits at last position
                    next_logits = lm_out.logits[0, -1, :].clone().to(torch.float32)

                    # Apply repetition penalty
                    if cfg.repetition_penalty != 1.0 and current_tokens:
                        seen_tokens = set(current_tokens)
                        for tok in seen_tokens:
                            if next_logits[tok] > 0:
                                next_logits[tok] /= cfg.repetition_penalty
                            else:
                                next_logits[tok] *= cfg.repetition_penalty

                    # Decoding selection
                    if cfg.do_sample and cfg.temperature > 0.0:
                        # Temperature scaling
                        scaled_logits = next_logits / cfg.temperature

                        # Top-k filtering
                        if cfg.top_k > 0:
                            v_size = scaled_logits.size(-1)
                            k = min(cfg.top_k, v_size)
                            topk_vals, _ = torch.topk(scaled_logits, k=k)
                            cutoff = topk_vals[-1]
                            scaled_logits = torch.where(
                                scaled_logits < cutoff,
                                torch.tensor(float("-inf"), device=self.device),
                                scaled_logits,
                            )

                        # Top-p (nucleus) filtering
                        if cfg.top_p < 1.0:
                            sorted_logits, sorted_indices = torch.sort(
                                scaled_logits, descending=True
                            )
                            sorted_probs = F.softmax(sorted_logits, dim=-1)
                            cumulative_probs = torch.cumsum(sorted_probs, dim=-1)

                            # Shift right by 1 so the token that crosses the threshold is retained
                            mask = (cumulative_probs - sorted_probs) >= cfg.top_p
                            sorted_logits[mask] = float("-inf")
                            # Restore original indexing
                            scaled_logits.scatter_(-1, sorted_indices, sorted_logits)

                        probs = F.softmax(scaled_logits, dim=-1)
                        # Guarantee nonempty distribution
                        if torch.isnan(probs).any() or probs.sum() <= 0:
                            probs = F.softmax(next_logits, dim=-1)

                        if local_gen is not None:
                            next_tok = int(
                                torch.multinomial(probs, num_samples=1, generator=local_gen).item()
                            )
                        else:
                            next_tok = int(torch.multinomial(probs, num_samples=1).item())
                    else:
                        # Greedy argmax (never divides by zero!)
                        next_tok = int(torch.argmax(next_logits, dim=-1).item())

                    # Check stop conditions
                    if next_tok in stop_token_set:
                        if next_tok == self.tokenizer.eos_token_id:
                            finish_reason = "eos"
                        else:
                            finish_reason = "stop_token"
                        break

                    generated_tokens.append(next_tok)
                    current_tokens.append(next_tok)

        finally:
            self.model.train(was_training)

        # 5. Decode generated text
        try:
            gen_text = self.tokenizer.decode(generated_tokens)
        except Exception:
            # Fallback for partial/malformed UTF-8 sequences
            raw_bytes = bytearray()
            for tid in generated_tokens:
                t_str = self.tokenizer.id_to_token(tid)
                if hasattr(self.tokenizer, "token_to_bytes"):
                    raw_bytes.extend(self.tokenizer.token_to_bytes(t_str))
            gen_text = raw_bytes.decode("utf-8", errors="replace")

        full_text = prompt_str + gen_text

        return GenerationResult(
            prompt=prompt_str,
            generated_text=gen_text,
            full_text=full_text,
            prompt_token_ids=prompt_tokens,
            generated_token_ids=generated_tokens,
            finish_reason=finish_reason,
            diagnostics=diagnostics,
        )
