"""Independent, read-only conditional log-likelihood scoring complying with C06, C08, C09, C11.

Complying with P06 Requirements & Amendments:
- Amendment 1: Explicit boundary tokenization policies (JOINT_PREFIX_MATCH_V1, SEPARATE_V1).
- Amendment 2: Independent read-only scoring through public model interface with eval mode
  and no-grad execution; state and caller preservation.
- Amendment 3: Deterministic rolling window & context-limit policies with zero target drops.
- Amendment 4: Strict UTF-8 bits-per-byte (BPB) and diagnostic accounting without EOS confusion.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

import torch
import torch.nn.functional as F

from xlm.core.contracts import CanonicalDocument, InferenceInput
from xlm.data.normalization import canonical_normalize
from xlm.models.base import BaseModel
from xlm.tokenizers.base import BaseTokenizer


class BoundaryPolicy(StrEnum):
    """Explicit, versioned boundary tokenization conventions for context and continuation."""

    JOINT_PREFIX_MATCH_V1 = "joint_prefix_match_v1"
    SEPARATE_V1 = "separate_v1"


class WindowTruncationPolicy(StrEnum):
    """Policy when sequence length exceeds model context length."""

    ROLLING = "rolling"
    ERROR = "error"


class CrossBoundaryMergeError(ValueError):
    """Raised when a cross-boundary merge occurs under strict joint prefix match."""


class ContinuationTooLongError(ValueError):
    """Raised when sequence exceeds context length and policy is ERROR."""


class ZeroScoredTokensError(ValueError):
    """Raised when no valid targets exist to score."""


@dataclass(frozen=True)
class TokenPair:
    """Pre-processed token pair separating text preparation from numerical scoring."""

    context_tokens: list[int]
    continuation_tokens: list[int]
    full_tokens: list[int]
    scored_slice: tuple[int, int]  # Half-open target index range [start, end)
    continuation_byte_spans: list[tuple[int, int]]
    total_continuation_bytes: int
    boundary_policy: BoundaryPolicy
    diagnostics: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ScoringRequest:
    """Request to score a continuation conditioned on a context."""

    context: str
    continuation: str
    item_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ScoringResult:
    """Numerical result of conditional log-likelihood scoring."""

    item_id: str | None
    log_likelihood: float
    is_greedy: bool
    token_count: int
    byte_count: int
    token_log_probs: list[float]
    tokens: list[int]
    targets: list[int]
    byte_spans: list[tuple[int, int]]
    diagnostics: dict[str, Any] = field(default_factory=dict)

    @property
    def nll_sum(self) -> float:
        """Negative log-likelihood sum."""
        return -self.log_likelihood

    @property
    def perplexity(self) -> float:
        """Per-token perplexity."""
        if self.token_count == 0:
            return float("nan")
        return math.exp(min(self.nll_sum / self.token_count, 100.0))

    @property
    def bits_per_byte(self) -> float:
        """Bits per byte on continuation UTF-8 bytes."""
        if self.byte_count == 0:
            return float("nan")
        return self.nll_sum / (math.log(2.0) * self.byte_count)


@dataclass(frozen=True)
class DocumentScoringResult:
    """Comprehensive scoring result for a canonical document complying with C11."""

    doc_id: str
    text_token_nll_sum: float
    text_token_count: int
    scored_canonical_utf8_bytes: int
    text_bpb: float
    text_ppl: float
    eos_inclusive_nll_sum: float
    eos_inclusive_token_count: int
    text_and_eos_bpb: float
    windows_evaluated: int
    diagnostics: dict[str, Any] = field(default_factory=dict)


def prepare_token_pair(
    tokenizer: BaseTokenizer,
    context: str,
    continuation: str,
    boundary_policy: BoundaryPolicy = BoundaryPolicy.JOINT_PREFIX_MATCH_V1,
) -> TokenPair:
    """Prepare token sequences and target boundaries adhering to Amendment 1.

    Separates string-level tokenization and boundary resolution from numerical scoring.
    """
    clean_cont = canonical_normalize(continuation)
    cont_utf8_bytes = len(clean_cont.encode("utf-8"))

    if context == "":
        # Empty prompt uses declared BOS context
        bos_id = tokenizer.bos_token_id
        c_tokens = [bos_id]
        y_tokens, y_offsets = tokenizer.encode_with_offsets(clean_cont, add_special_tokens=False)
        full_tokens = c_tokens + y_tokens
        scored_slice = (1, len(full_tokens))
        return TokenPair(
            context_tokens=c_tokens,
            continuation_tokens=y_tokens,
            full_tokens=full_tokens,
            scored_slice=scored_slice,
            continuation_byte_spans=y_offsets,
            total_continuation_bytes=cont_utf8_bytes,
            boundary_policy=boundary_policy,
            diagnostics={"empty_context_bos": True},
        )

    clean_ctx = canonical_normalize(context)

    if boundary_policy == BoundaryPolicy.SEPARATE_V1:
        c_tokens = tokenizer.encode(clean_ctx, add_special_tokens=False)
        y_tokens, y_offsets = tokenizer.encode_with_offsets(clean_cont, add_special_tokens=False)
        full_tokens = c_tokens + y_tokens
        scored_slice = (len(c_tokens), len(full_tokens))
        return TokenPair(
            context_tokens=c_tokens,
            continuation_tokens=y_tokens,
            full_tokens=full_tokens,
            scored_slice=scored_slice,
            continuation_byte_spans=y_offsets,
            total_continuation_bytes=cont_utf8_bytes,
            boundary_policy=boundary_policy,
            diagnostics={"mode": "separate"},
        )

    if boundary_policy == BoundaryPolicy.JOINT_PREFIX_MATCH_V1:
        combined_text = clean_ctx + clean_cont
        full_tokens, full_offsets = tokenizer.encode_with_offsets(
            combined_text, add_special_tokens=False
        )
        c_tokens = tokenizer.encode(clean_ctx, add_special_tokens=False)
        ctx_len = len(c_tokens)

        # Verify prefix stability
        if len(full_tokens) >= ctx_len and full_tokens[:ctx_len] == c_tokens:
            y_tokens = full_tokens[ctx_len:]
            scored_slice = (ctx_len, len(full_tokens))
            raw_spans = full_offsets[ctx_len:]
            ctx_bytes = len(clean_ctx.encode("utf-8"))
            # Shift byte spans relative to continuation start
            cont_spans = [(max(0, s - ctx_bytes), max(0, e - ctx_bytes)) for s, e in raw_spans]
            return TokenPair(
                context_tokens=c_tokens,
                continuation_tokens=y_tokens,
                full_tokens=full_tokens,
                scored_slice=scored_slice,
                continuation_byte_spans=cont_spans,
                total_continuation_bytes=cont_utf8_bytes,
                boundary_policy=boundary_policy,
                diagnostics={"mode": "joint_prefix_matched"},
            )

        # Cross-boundary merge detected
        err_msg = (
            f"Cross-boundary merge detected between context '{context!r}' and "
            f"continuation '{continuation!r}'. Context tokens {c_tokens} do not prefix-match "
            f"joint tokens {full_tokens[:ctx_len]}."
        )
        raise CrossBoundaryMergeError(err_msg)

    raise ValueError(f"Unknown boundary policy: {boundary_policy}")


class ConditionalLikelihoodScorer:
    """Evaluates conditional log-likelihood and text-level diagnostics.

    Adheres strictly to Amendments 1, 2, 3, 4:
    - Pure read-only inference execution using model(InferenceInput(...)).
    - Automatic eval mode switching and caller state restoration.
    - Deterministic rolling window evaluation with zero target drops.
    - True UTF-8 bits per byte (BPB) calculation without structural token pollution.
    """

    def __init__(
        self,
        model: BaseModel,
        tokenizer: BaseTokenizer,
        device: str = "cpu",
        precision: str = "fp32",
        boundary_policy: BoundaryPolicy = BoundaryPolicy.JOINT_PREFIX_MATCH_V1,
        window_policy: WindowTruncationPolicy = WindowTruncationPolicy.ROLLING,
        max_context_length: int | None = None,
        stride: int | None = None,
    ) -> None:
        self.model = model
        self.tokenizer = tokenizer
        self.device = device
        self.precision = precision
        self.boundary_policy = boundary_policy
        self.window_policy = window_policy

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

        self.max_context_length = max_context_length or caps_max or 512
        self.stride = stride or max(1, self.max_context_length // 2)

    def _get_comp_dtype(self) -> torch.dtype:
        if self.precision == "fp64":
            return torch.float64
        if self.precision in ("bf16", "bfloat16"):
            return torch.bfloat16
        if self.precision in ("fp16", "float16"):
            return torch.float16
        return torch.float32

    def score_token_sequence(
        self,
        tokens: list[int],
        target_mask: list[bool],
        position_ids: list[int] | None = None,
    ) -> tuple[list[float], list[bool]]:
        """Score target tokens in a single window with exact target shifting.

        Input: tokens of length L.
        Model forward: input_ids = tokens[:-1] (length L - 1).
        Targets: tokens[1:] (length L - 1).
        Loss mask: target_mask[1:] (length L - 1).
        Returns: per-target log probabilities and is_greedy flags for masked positions.
        """
        seq_len = len(tokens)
        if seq_len <= 1:
            return [], []

        comp_dtype = self._get_comp_dtype()
        was_training = self.model.training
        self.model.eval()

        try:
            with torch.no_grad():
                input_ids = torch.tensor([tokens[:-1]], dtype=torch.long, device=self.device)
                pos_tensor = None
                if position_ids is not None:
                    pos_tensor = torch.tensor(
                        [position_ids[:-1]], dtype=torch.long, device=self.device
                    )

                inf_input = InferenceInput(
                    input_ids=input_ids,
                    position_ids=pos_tensor,
                )
                lm_out = self.model(inf_input)
                logits = lm_out.logits.to(comp_dtype)  # [1, L-1, V]

                # Compute stable log probabilities
                log_probs = F.log_softmax(logits, dim=-1)  # [1, L-1, V]

                targets = tokens[1:]
                masks = target_mask[1:]

                scored_log_probs: list[float] = []
                is_greedy_list: list[bool] = []

                for i, (tgt, m) in enumerate(zip(targets, masks, strict=True)):
                    if m:
                        lp = float(log_probs[0, i, tgt].item())
                        # Greedy argmax check with lowest index tie breaking
                        pred_token = int(torch.argmax(logits[0, i], dim=-1).item())
                        is_greedy = pred_token == tgt
                        scored_log_probs.append(lp)
                        is_greedy_list.append(is_greedy)

                return scored_log_probs, is_greedy_list
        finally:
            self.model.train(was_training)

    def score_continuation(
        self,
        context: str,
        continuation: str,
        item_id: str | None = None,
        boundary_policy: BoundaryPolicy | None = None,
    ) -> ScoringResult:
        """Score all tokens in a continuation conditioned on context."""
        policy = boundary_policy or self.boundary_policy
        token_pair = prepare_token_pair(
            self.tokenizer, context, continuation, boundary_policy=policy
        )

        full_tokens = token_pair.full_tokens
        total_len = len(full_tokens)
        start_tgt, end_tgt = token_pair.scored_slice

        if start_tgt >= end_tgt:
            raise ZeroScoredTokensError("Zero continuation targets present to score.")

        # Check context length
        if total_len <= self.max_context_length:
            target_mask = [False] * total_len
            for i in range(start_tgt, end_tgt):
                target_mask[i] = True

            log_probs, is_greedy = self.score_token_sequence(full_tokens, target_mask)
            total_ll = sum(log_probs)
            all_greedy = all(is_greedy) if is_greedy else False
            target_tokens = full_tokens[start_tgt:end_tgt]

            return ScoringResult(
                item_id=item_id,
                log_likelihood=total_ll,
                is_greedy=all_greedy,
                token_count=len(target_tokens),
                byte_count=token_pair.total_continuation_bytes,
                token_log_probs=log_probs,
                tokens=full_tokens,
                targets=target_tokens,
                byte_spans=token_pair.continuation_byte_spans,
                diagnostics=token_pair.diagnostics,
            )

        # Exceeds context length: apply WindowTruncationPolicy
        if self.window_policy == WindowTruncationPolicy.ERROR:
            raise ContinuationTooLongError(
                f"Sequence length {total_len} exceeds max context length {self.max_context_length} "
                "under policy WindowTruncationPolicy.ERROR."
            )

        # Rolling window policy: stride across continuation
        return self._score_continuation_rolling(token_pair, item_id)

    def _score_continuation_rolling(
        self,
        token_pair: TokenPair,
        item_id: str | None,
    ) -> ScoringResult:
        """Evaluate a long sequence using deterministic rolling windows."""
        full_tokens = token_pair.full_tokens
        total_len = len(full_tokens)
        start_tgt, end_tgt = token_pair.scored_slice
        w = self.max_context_length

        all_scored_log_probs: list[float] = []
        all_is_greedy: list[bool] = []
        target_tokens = full_tokens[start_tgt:end_tgt]

        # Targets to score have absolute indices in [start_tgt, end_tgt)
        scored_targets_set: set[int] = set()

        curr_start = 0
        while curr_start < total_len - 1:
            curr_end = min(curr_start + w, total_len)
            window_tokens = full_tokens[curr_start:curr_end]
            win_len = len(window_tokens)

            # Determine target ownership in this window
            win_mask = [False] * win_len
            for i in range(1, win_len):
                abs_idx = curr_start + i
                if start_tgt <= abs_idx < end_tgt and abs_idx not in scored_targets_set:
                    # Token is owned by this window if curr_start moves past it or end reached
                    win_mask[i] = True
                    scored_targets_set.add(abs_idx)

            if any(win_mask):
                pos_ids = list(range(curr_start, curr_start + win_len))
                lps, greedy_flags = self.score_token_sequence(
                    window_tokens, win_mask, position_ids=pos_ids
                )
                all_scored_log_probs.extend(lps)
                all_is_greedy.extend(greedy_flags)

            if curr_end >= total_len:
                break
            curr_start += self.stride

        scored_n = len(scored_targets_set)
        target_n = len(target_tokens)
        assert scored_n == target_n, (
            f"Rolling window target mismatch: scored {scored_n} of {target_n}"
        )

        total_ll = sum(all_scored_log_probs)
        all_greedy = all(all_is_greedy) if all_is_greedy else False

        diag = dict(token_pair.diagnostics)
        diag["rolling_windows_used"] = True

        return ScoringResult(
            item_id=item_id,
            log_likelihood=total_ll,
            is_greedy=all_greedy,
            token_count=len(target_tokens),
            byte_count=token_pair.total_continuation_bytes,
            token_log_probs=all_scored_log_probs,
            tokens=full_tokens,
            targets=target_tokens,
            byte_spans=token_pair.continuation_byte_spans,
            diagnostics=diag,
        )

    def score_document(
        self,
        document: CanonicalDocument | str,
        doc_id: str = "doc_eval",
    ) -> DocumentScoringResult:
        """Score an entire canonical document using rolling windows and exact C11 BPB."""
        if isinstance(document, CanonicalDocument):
            text = document.text
            did = document.doc_id
        else:
            text = document
            did = doc_id

        clean_text = canonical_normalize(text)
        canonical_bytes = clean_text.encode("utf-8")
        num_canonical_bytes = len(canonical_bytes)

        if num_canonical_bytes == 0:
            return DocumentScoringResult(
                doc_id=did,
                text_token_nll_sum=0.0,
                text_token_count=0,
                scored_canonical_utf8_bytes=0,
                text_bpb=float("nan"),
                text_ppl=float("nan"),
                eos_inclusive_nll_sum=0.0,
                eos_inclusive_token_count=0,
                text_and_eos_bpb=float("nan"),
                windows_evaluated=0,
                diagnostics={"empty_document": True},
            )

        # Content tokens without special framing
        content_ids, offsets = self.tokenizer.encode_with_offsets(
            clean_text, add_special_tokens=False
        )
        num_text_tokens = len(content_ids)
        if num_text_tokens == 0:
            return DocumentScoringResult(
                doc_id=did,
                text_token_nll_sum=0.0,
                text_token_count=0,
                scored_canonical_utf8_bytes=num_canonical_bytes,
                text_bpb=float("nan"),
                text_ppl=float("nan"),
                eos_inclusive_nll_sum=0.0,
                eos_inclusive_token_count=0,
                text_and_eos_bpb=float("nan"),
                windows_evaluated=0,
                diagnostics={"empty_content_tokens": True},
            )

        # Frame with BOS at start and EOS at end
        bos_id = self.tokenizer.bos_token_id
        eos_id = self.tokenizer.eos_token_id
        full_tokens = [bos_id] + content_ids + [eos_id]
        total_tokens = len(full_tokens)
        # BOS at 0 (unscored context)
        # Text targets at indices 1 .. num_text_tokens
        # EOS target at index num_text_tokens + 1

        w = self.max_context_length
        stride = self.stride

        scored_targets_nll: dict[int, float] = {}
        windows_count = 0

        curr_start = 0
        while curr_start < total_tokens - 1:
            curr_end = min(curr_start + w, total_tokens)
            window_tokens = full_tokens[curr_start:curr_end]
            win_len = len(window_tokens)

            # Assign targets deterministically:
            # Token at absolute index t is scored in the window where it has max causal context
            win_mask = [False] * win_len
            for i in range(1, win_len):
                abs_t = curr_start + i
                if abs_t not in scored_targets_nll:
                    # If this is the last window that can contain abs_t or reached end
                    is_last_window_for_token = (curr_start + stride + 1 > abs_t) or (
                        curr_end >= total_tokens
                    )
                    if is_last_window_for_token:
                        win_mask[i] = True

            if any(win_mask):
                windows_count += 1
                pos_ids = list(range(curr_start, curr_start + win_len))
                lps, _ = self.score_token_sequence(window_tokens, win_mask, position_ids=pos_ids)
                lp_idx = 0
                for i in range(1, win_len):
                    if win_mask[i]:
                        abs_t = curr_start + i
                        scored_targets_nll[abs_t] = -lps[lp_idx]
                        lp_idx += 1

            if curr_end >= total_tokens:
                break
            curr_start += stride

        # Separate text tokens from EOS
        text_nll_sum = sum(
            scored_targets_nll[t] for t in range(1, num_text_tokens + 1) if t in scored_targets_nll
        )
        eos_nll = scored_targets_nll.get(num_text_tokens + 1, 0.0)

        ln2 = math.log(2.0)
        text_bpb = (
            text_nll_sum / (ln2 * num_canonical_bytes) if num_canonical_bytes > 0 else float("nan")
        )
        text_ppl = math.exp(text_nll_sum / num_text_tokens) if num_text_tokens > 0 else float("nan")

        eos_inclusive_nll = text_nll_sum + eos_nll
        eos_inclusive_tokens = num_text_tokens + 1
        text_and_eos_bpb = (
            eos_inclusive_nll / (ln2 * num_canonical_bytes)
            if num_canonical_bytes > 0
            else float("nan")
        )

        return DocumentScoringResult(
            doc_id=did,
            text_token_nll_sum=text_nll_sum,
            text_token_count=num_text_tokens,
            scored_canonical_utf8_bytes=num_canonical_bytes,
            text_bpb=text_bpb,
            text_ppl=text_ppl,
            eos_inclusive_nll_sum=eos_inclusive_nll,
            eos_inclusive_token_count=eos_inclusive_tokens,
            text_and_eos_bpb=text_and_eos_bpb,
            windows_evaluated=windows_count,
            diagnostics={
                "eos_nll": eos_nll,
                "first_token_conditioned_on_bos": True,
                # Coverage evidence: the sums above silently skip unscored targets.
                "scored_text_targets": sum(
                    1 for t in range(1, num_text_tokens + 1) if t in scored_targets_nll
                ),
                "eos_target_scored": (num_text_tokens + 1) in scored_targets_nll,
            },
        )

    def score_batch(
        self,
        requests: list[ScoringRequest],
        boundary_policy: BoundaryPolicy | None = None,
    ) -> list[ScoringResult]:
        """Score multiple requests with exact single-item parity using padded forward pass."""
        if not requests:
            return []

        # If any request exceeds context length, fallback to single item evaluation
        # to ensure correct rolling window handling
        policy = boundary_policy or self.boundary_policy
        pairs: list[TokenPair] = [
            prepare_token_pair(
                self.tokenizer, req.context, req.continuation, boundary_policy=policy
            )
            for req in requests
        ]

        if any(len(p.full_tokens) > self.max_context_length for p in pairs):
            return [
                self.score_continuation(
                    req.context, req.continuation, item_id=req.item_id, boundary_policy=policy
                )
                for req in requests
            ]

        # Short batch: evaluate in a single batched forward pass with right padding
        max_len = max(len(p.full_tokens) for p in pairs)
        if max_len <= 1:
            raise ZeroScoredTokensError("All sequences have length <= 1")

        batch_size = len(pairs)
        pad_id = self.tokenizer.pad_token_id

        input_ids = torch.full(
            (batch_size, max_len - 1), pad_id, dtype=torch.long, device=self.device
        )
        pos_ids = torch.zeros((batch_size, max_len - 1), dtype=torch.long, device=self.device)
        attn_mask = torch.zeros((batch_size, max_len - 1), dtype=torch.bool, device=self.device)

        for b, p in enumerate(pairs):
            seq_len = len(p.full_tokens)
            if seq_len > 1:
                input_ids[b, : seq_len - 1] = torch.tensor(
                    p.full_tokens[:-1], dtype=torch.long, device=self.device
                )
                pos_ids[b, : seq_len - 1] = torch.arange(
                    seq_len - 1, dtype=torch.long, device=self.device
                )
                attn_mask[b, : seq_len - 1] = True

        comp_dtype = self._get_comp_dtype()
        was_training = self.model.training
        self.model.eval()

        try:
            with torch.no_grad():
                inf_input = InferenceInput(
                    input_ids=input_ids,
                    attention_mask=attn_mask,
                    position_ids=pos_ids,
                )
                lm_out = self.model(inf_input)
                logits = lm_out.logits.to(comp_dtype)  # [B, max_len - 1, V]
                log_probs = F.log_softmax(logits, dim=-1)

                results: list[ScoringResult] = []
                for b, (req, p) in enumerate(zip(requests, pairs, strict=True)):
                    start_tgt, end_tgt = p.scored_slice
                    lps: list[float] = []
                    is_greedy_list: list[bool] = []
                    targets = p.full_tokens[start_tgt:end_tgt]

                    for abs_tgt_idx in range(start_tgt, end_tgt):
                        pos_in_batch = (
                            abs_tgt_idx - 1
                        )  # logit at pos_in_batch predicts target at abs_tgt_idx
                        tgt = p.full_tokens[abs_tgt_idx]
                        lp = float(log_probs[b, pos_in_batch, tgt].item())
                        pred = int(torch.argmax(logits[b, pos_in_batch], dim=-1).item())
                        lps.append(lp)
                        is_greedy_list.append(pred == tgt)

                    results.append(
                        ScoringResult(
                            item_id=req.item_id,
                            log_likelihood=sum(lps),
                            is_greedy=all(is_greedy_list) if is_greedy_list else False,
                            token_count=len(targets),
                            byte_count=p.total_continuation_bytes,
                            token_log_probs=lps,
                            tokens=p.full_tokens,
                            targets=targets,
                            byte_spans=p.continuation_byte_spans,
                            diagnostics=p.diagnostics,
                        )
                    )
                return results
        finally:
            self.model.train(was_training)
