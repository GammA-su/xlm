"""XLM lm-evaluation-harness backend, registered externally (C11, A26).

This module imports the harness and is therefore only importable when the `eval`
extra is installed. It defines the ``LM`` subclass and registers it under the
name ``xlm`` through the harness's public registry -- installed third-party code
is never edited. Native XLM scoring remains the oracle: ``loglikelihood`` and
``loglikelihood_rolling`` delegate to
:class:`xlm.evaluation.likelihood.ConditionalLikelihoodScorer` so a parity test
compares two consumers of the same arithmetic rather than two approximations.
"""

from __future__ import annotations

from typing import Any

import lm_eval  # noqa: F401  (version is checked by xlm.evaluation.harness)
import torch
from lm_eval.api.instance import Instance
from lm_eval.api.model import LM
from lm_eval.api.registry import register_model

from xlm.evaluation.likelihood import (
    BoundaryPolicy,
    ConditionalLikelihoodScorer,
    WindowTruncationPolicy,
)
from xlm.inference.generation import GenerationConfig, TextGenerator
from xlm.models.base import BaseModel
from xlm.tokenizers.base import BaseTokenizer


@register_model("xlm")
class XlmHarnessLM(LM):  # type: ignore[misc]  # LM is Any without lm-eval stubs
    """A harness backend that delegates every request to native XLM scoring."""

    def __init__(
        self,
        model: BaseModel,
        tokenizer: BaseTokenizer,
        device: str = "cpu",
        precision: str = "fp32",
        boundary_policy: BoundaryPolicy = BoundaryPolicy.JOINT_PREFIX_MATCH_V1,
        max_context_length: int | None = None,
        max_gen_tokens: int = 256,
    ) -> None:
        super().__init__()
        self._device = torch.device(device)
        self.model = model
        self.tokenizer = tokenizer
        self.precision = precision
        self.boundary_policy = boundary_policy
        self.max_gen_tokens = max_gen_tokens
        self._native = ConditionalLikelihoodScorer(
            model=model,
            tokenizer=tokenizer,
            device=device,
            precision=precision,
            boundary_policy=boundary_policy,
            window_policy=WindowTruncationPolicy.ROLLING,
            max_context_length=max_context_length,
        )
        self._generator = TextGenerator(model=model, tokenizer=tokenizer, device=device)

    # ------------------------------------------------ harness LM interface

    def loglikelihood(self, requests: list[Instance]) -> list[tuple[float, bool]]:
        """Score (context, continuation) pairs through the native scorer.

        Requests are pure functions of their arguments: results are invariant to
        ordering and no branch state survives between requests.
        """
        results: list[tuple[float, bool]] = []
        for instance in requests:
            context, continuation = instance.args
            scoring = self._native.score_continuation(
                context, continuation, item_id=instance.doc_id and str(instance.doc_id)
            )
            results.append((scoring.log_likelihood, scoring.is_greedy))
        return results

    def loglikelihood_rolling(self, requests: list[Instance]) -> list[Any]:
        """Score whole strings with rolling windows, using the text-BPB convention.

        Returns the native text-token log-likelihood (BOS is context, structural
        EOS is excluded); the EOS-inclusive variant has its own name in C11 and is
        not silently substituted here.
        """
        results: list[Any] = []
        for instance in requests:
            (text,) = instance.args
            document = self._native.score_document(text)
            results.append((-document.text_token_nll_sum,))
        return results

    def generate_until(self, requests: list[Instance]) -> list[str]:
        """Greedy generation with harness ``until`` stopping and token caps."""
        outputs: list[str] = []
        for instance in requests:
            context, gen_kwargs = instance.args
            kwargs = dict(gen_kwargs or {})
            max_new = int(kwargs.get("max_gen_toks", self.max_gen_tokens))
            max_new = max(1, min(max_new, self.max_gen_tokens))
            config = GenerationConfig(
                max_new_tokens=max_new,
                do_sample=False,
                temperature=1.0,
            )
            result = self._generator.generate(context, config)
            text = result.generated_text
            for stop in kwargs.get("until", []) or []:
                if stop and stop in text:
                    text = text.split(stop, 1)[0]
            outputs.append(text)
        return outputs

    # ------------------------------------------------------------ properties

    @property
    def eot_token_id(self) -> int:
        return int(self.tokenizer.eos_token_id)

    @property
    def prefix_token_id(self) -> int:
        return int(self.tokenizer.bos_token_id)

    @property
    def max_length(self) -> int:
        return int(self._native.max_context_length)

    @property
    def tokenizer_name(self) -> str:
        return f"xlm:{self.tokenizer.fingerprint}"

    def tok_encode(self, string: str, add_special_tokens: bool | None = None) -> list[int]:
        return self.tokenizer.encode(string, add_special_tokens=bool(add_special_tokens))

    def tok_decode(self, tokens: list[int]) -> str:
        return self.tokenizer.decode(tokens)
