"""Benchmark fixture scorer, character-normalized metrics, and cryptographic cache identity.

Complying with Contract C11, C12 and Amendments 5, 6:
- Accurate character normalization (acc_norm) using answer string length.
- Distinct raw and normalized margins and deterministic tie breaking.
- Robust raw likelihood caching and aggregate evaluation receipts.
- Inter-choice and inter-request state isolation.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from xlm.core.contracts import EvaluationReceipt
from xlm.core.paths import ArtifactPaths
from xlm.data.normalization import compute_sha256
from xlm.evaluation.fixtures import BenchmarkFixtureDataset, MinimalPairItem, MultipleChoiceItem
from xlm.evaluation.likelihood import BoundaryPolicy, ConditionalLikelihoodScorer, ScoringResult

SCORER_VERSION = "xlm_likelihood_v1"
TEMPLATE_VERSION = "v1_verbatim"


@dataclass(frozen=True)
class MultipleChoiceItemResult:
    """Evaluation result for a single multiple-choice item."""

    item_id: str
    gold_index: int
    predicted_raw: int
    predicted_norm: int
    is_correct_raw: bool
    is_correct_norm: bool
    choice_log_likelihoods: list[float]
    choice_normalized_scores: list[float]
    raw_margin: float
    norm_margin: float


@dataclass(frozen=True)
class MinimalPairItemResult:
    """Evaluation result for a single minimal-pair item."""

    item_id: str
    good_log_likelihood: float
    bad_log_likelihood: float
    is_correct: bool
    margin: float


class RawLikelihoodCache:
    """Content-addressed raw likelihood cache complying with Amendment 5.

    Identifies:
    - Canonical context text, continuation text, and boundary policy.
    - Model/checkpoint hash and configuration.
    - Tokenizer fingerprint.
    - Precision mode, max context length, and scorer version.
    """

    def __init__(self, cache_dir: Path | None = None) -> None:
        self.cache_dir = cache_dir or (ArtifactPaths.from_env().evaluation / "raw_cache")
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def compute_key(
        self,
        context: str,
        continuation: str,
        boundary_policy: BoundaryPolicy,
        model_hash: str,
        tokenizer_hash: str,
        precision_mode: str,
        max_context: int,
        execution_fingerprint: str | None = None,
    ) -> str:
        payload = (
            f"ctx={context!r}:cont={continuation!r}:bp={boundary_policy.value}:"
            f"model={model_hash}:tok={tokenizer_hash}:prec={precision_mode}:"
            f"ctx_len={max_context}:scorer={SCORER_VERSION}:execution={execution_fingerprint}"
        )
        return compute_sha256(payload)

    def get(self, key: str) -> ScoringResult | None:
        path = self.cache_dir / f"{key}.json"
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return ScoringResult(
                item_id=data.get("item_id"),
                log_likelihood=float(data["log_likelihood"]),
                is_greedy=bool(data["is_greedy"]),
                token_count=int(data["token_count"]),
                byte_count=int(data["byte_count"]),
                token_log_probs=[float(x) for x in data["token_log_probs"]],
                tokens=[int(x) for x in data["tokens"]],
                targets=[int(x) for x in data["targets"]],
                byte_spans=[(int(s), int(e)) for s, e in data["byte_spans"]],
                diagnostics=dict(data.get("diagnostics", {})),
            )
        except (json.JSONDecodeError, KeyError, ValueError):
            # Corrupted cache file: reject cleanly
            return None

    def put(self, key: str, result: ScoringResult) -> None:
        path = self.cache_dir / f"{key}.json"
        tmp_path = self.cache_dir / f"{key}.tmp.{datetime.now(UTC).timestamp()}"
        payload = {
            "item_id": result.item_id,
            "log_likelihood": result.log_likelihood,
            "is_greedy": result.is_greedy,
            "token_count": result.token_count,
            "byte_count": result.byte_count,
            "token_log_probs": result.token_log_probs,
            "tokens": result.tokens,
            "targets": result.targets,
            "byte_spans": result.byte_spans,
            "diagnostics": result.diagnostics,
        }
        tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp_path.replace(path)


class BenchmarkFixtureScorer:
    """Evaluates synthetic offline benchmark fixtures without cross-example state leakage."""

    def __init__(
        self,
        scorer: ConditionalLikelihoodScorer,
        model_hash: str = "model_hash_v1",
        tokenizer_hash: str | None = None,
        cache: RawLikelihoodCache | None = None,
        use_cache: bool = True,
        execution_fingerprint: str | None = None,
    ) -> None:
        self.scorer = scorer
        self.model_hash = model_hash
        self.tokenizer_hash = tokenizer_hash or scorer.tokenizer.fingerprint
        self.cache = cache or RawLikelihoodCache()
        self.use_cache = use_cache
        self.execution_fingerprint = execution_fingerprint

    def _score_cached(self, context: str, continuation: str, item_id: str | None) -> ScoringResult:
        if self.use_cache:
            key = self.cache.compute_key(
                context=context,
                continuation=continuation,
                boundary_policy=self.scorer.boundary_policy,
                model_hash=self.model_hash,
                tokenizer_hash=self.tokenizer_hash,
                precision_mode=self.scorer.precision,
                max_context=self.scorer.max_context_length,
                execution_fingerprint=self.execution_fingerprint,
            )
            hit = self.cache.get(key)
            if hit is not None:
                return hit

        res = self.scorer.score_continuation(context, continuation, item_id=item_id)
        if self.use_cache:
            self.cache.put(key, res)
        return res

    def evaluate_multiple_choice_item(self, item: MultipleChoiceItem) -> MultipleChoiceItemResult:
        """Evaluate a single multiple-choice item with character-normalized acc_norm."""
        raw_lls: list[float] = []
        norm_scores: list[float] = []

        for ch in item.choices:
            res = self._score_cached(item.context, ch, item.item_id)
            raw_lls.append(res.log_likelihood)
            # Amendment 6: Character normalization uses answer string length
            ch_len = len(ch)
            norm_scores.append(res.log_likelihood / ch_len)

        # Deterministic tie breaking: lowest index
        max_raw = max(raw_lls)
        pred_raw = raw_lls.index(max_raw)

        max_norm = max(norm_scores)
        pred_norm = norm_scores.index(max_norm)

        # Compute margins
        other_raws = [raw_lls[i] for i in range(len(raw_lls)) if i != pred_raw]
        raw_margin = max_raw - max(other_raws) if other_raws else 0.0

        other_norms = [norm_scores[i] for i in range(len(norm_scores)) if i != pred_norm]
        norm_margin = max_norm - max(other_norms) if other_norms else 0.0

        return MultipleChoiceItemResult(
            item_id=item.item_id,
            gold_index=item.gold_index,
            predicted_raw=pred_raw,
            predicted_norm=pred_norm,
            is_correct_raw=(pred_raw == item.gold_index),
            is_correct_norm=(pred_norm == item.gold_index),
            choice_log_likelihoods=raw_lls,
            choice_normalized_scores=norm_scores,
            raw_margin=raw_margin,
            norm_margin=norm_margin,
        )

    def evaluate_minimal_pair_item(self, item: MinimalPairItem) -> MinimalPairItemResult:
        """Evaluate a single minimal pair item conditioned on BOS."""
        # Good sentence likelihood from empty context (BOS)
        res_good = self._score_cached("", item.good_sentence, item.item_id)
        # Bad sentence likelihood from empty context (BOS)
        res_bad = self._score_cached("", item.bad_sentence, item.item_id)

        margin = res_good.log_likelihood - res_bad.log_likelihood
        is_correct = res_good.log_likelihood > res_bad.log_likelihood

        return MinimalPairItemResult(
            item_id=item.item_id,
            good_log_likelihood=res_good.log_likelihood,
            bad_log_likelihood=res_bad.log_likelihood,
            is_correct=is_correct,
            margin=margin,
        )

    def evaluate_dataset(
        self,
        dataset: BenchmarkFixtureDataset,
        receipt_id: str | None = None,
    ) -> tuple[EvaluationReceipt, list[Any]]:
        """Evaluate an entire fixture dataset and emit an EvaluationReceipt."""
        item_results: list[Any] = []
        metrics: dict[str, float] = {}

        if dataset.kind == "multiple_choice":
            mc_items = [it for it in dataset.items if isinstance(it, MultipleChoiceItem)]
            for mc_it in mc_items:
                mc_res = self.evaluate_multiple_choice_item(mc_it)
                item_results.append(mc_res)

            n_total = len(mc_items)
            n_raw_correct = sum(1 for r in item_results if r.is_correct_raw)
            n_norm_correct = sum(1 for r in item_results if r.is_correct_norm)
            avg_raw_margin = (
                sum(r.raw_margin for r in item_results) / n_total if n_total > 0 else 0.0
            )
            avg_norm_margin = (
                sum(r.norm_margin for r in item_results) / n_total if n_total > 0 else 0.0
            )

            metrics = {
                "acc": n_raw_correct / n_total if n_total > 0 else 0.0,
                "acc_norm": n_norm_correct / n_total if n_total > 0 else 0.0,
                "raw_margin_mean": avg_raw_margin,
                "norm_margin_mean": avg_norm_margin,
            }

        elif dataset.kind == "minimal_pair":
            pair_items = [it for it in dataset.items if isinstance(it, MinimalPairItem)]
            for pair_it in pair_items:
                pair_res = self.evaluate_minimal_pair_item(pair_it)
                item_results.append(pair_res)

            n_total = len(pair_items)
            n_correct = sum(1 for r in item_results if r.is_correct)
            avg_margin = sum(r.margin for r in item_results) / n_total if n_total > 0 else 0.0

            metrics = {
                "acc": n_correct / n_total if n_total > 0 else 0.0,
                "margin_mean": avg_margin,
            }
        else:
            raise ValueError(f"Unsupported dataset kind: {dataset.kind}")

        rec_id = (
            receipt_id
            or f"receipt_{dataset.dataset_id}_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}"
        )

        receipt = EvaluationReceipt(
            receipt_id=rec_id,
            checkpoint_hash=self.model_hash,
            tokenizer_hash=self.tokenizer_hash,
            dataset_revision="v1_synthetic",
            split_id="offline_fixture",
            task_source_and_scorer_version=f"{dataset.dataset_id}:{SCORER_VERSION}",
            prompt_template_version=TEMPLATE_VERSION,
            metric_normalization_policy="character_length"
            if dataset.kind == "multiple_choice"
            else "sentence_raw",
            context_truncation_policy=self.scorer.window_policy.value,
            precision_mode=self.scorer.precision,
            metrics=metrics,
            scored_items_count=len(item_results),
            holdout_mode="search",
            created_at=datetime.now(UTC).isoformat(),
        )

        return receipt, item_results
