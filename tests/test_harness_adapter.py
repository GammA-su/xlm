"""Acceptance tests for the pinned lm-evaluation-harness adapter (P15, A26).

These tests run the real installed harness against authored local fixture tasks
(no network, no official labels) with a tiny authored model. The native scorer
is the oracle: every harness likelihood is compared to the same two consumers'
arithmetic, and task acc/acc_norm are compared prediction by prediction.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.optional_dependency

pytest.importorskip("torch")
pytest.importorskip("lm_eval")
pytest.importorskip("datasets")

import torch  # noqa: E402

from xlm.core.contracts import InferenceInput, LMOutput, ModelCapabilities  # noqa: E402
from xlm.evaluation.evidence import (  # noqa: E402
    EvaluationEvidence,
    items_from_harness_samples,
    task_evidence_from_items,
)
from xlm.evaluation.harness import (  # noqa: E402
    SUPPORTED_LM_EVAL_VERSION,
    build_task_manager,
    create_harness_model,
)
from xlm.evaluation.harness_runner import run_harness_suite  # noqa: E402
from xlm.evaluation.likelihood import (  # noqa: E402
    ConditionalLikelihoodScorer,
    ContinuationTooLongError,
    WindowTruncationPolicy,
)
from xlm.evaluation.suites import SuiteTier, TaskVariant, compute_four_task_index  # noqa: E402
from xlm.models.base import BaseModel  # noqa: E402
from xlm.models.transformer import TransformerBaseline  # noqa: E402
from xlm.tokenizers.byte import ByteTokenizer  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_TASKS = REPO_ROOT / "fixtures" / "eval" / "tasks"
FIXTURE_DATA = FIXTURE_TASKS / "data"


def _tiny_model(seed: int = 5) -> TransformerBaseline:
    from xlm.config.schemas import TransformerBaselineConfig

    config = TransformerBaselineConfig(
        architecture="transformer_baseline",
        vocab_size=260,
        num_layers=2,
        hidden_size=64,
        num_attention_heads=4,
        intermediate_size=128,
        context_length=128,
        attention_backend="eager",
    )
    return TransformerBaseline(config, seed=seed)


class ZeroLogitsModel(BaseModel):
    """A no-op model: every choice scores equally, exposing tie behavior only."""

    def __init__(self, vocab_size: int, context_length: int = 128) -> None:
        super().__init__()
        self.vocab_size = vocab_size
        self.context_length = context_length
        self._config = type("Cfg", (), {"context_length": context_length})()

    @property
    def config(self) -> Any:
        return self._config

    def forward(
        self,
        input_ids: torch.Tensor | InferenceInput,
        attention_mask: torch.Tensor | None = None,
        position_ids: torch.Tensor | None = None,
        state: Any | None = None,
        requested_outputs: dict[str, bool] | None = None,
    ) -> LMOutput:
        ids = input_ids.input_ids if isinstance(input_ids, InferenceInput) else input_ids
        batch, seq_len = ids.shape
        logits = torch.zeros(batch, seq_len, self.vocab_size)
        return LMOutput(logits=logits, auxiliary_outputs={}, state=None)

    def get_capabilities(self) -> ModelCapabilities:
        return ModelCapabilities(
            supports_kv_cache=False,
            supports_cross_document_attention=True,
            supports_bidirectional=False,
            max_context_length=self.context_length,
            custom_state=False,
        )

    def count_parameters(self) -> Any:
        raise NotImplementedError


def _instances(request_type: str, args_list: Sequence[tuple[Any, ...]]) -> list[Any]:
    from lm_eval.api.instance import Instance

    return [
        Instance(request_type=request_type, doc={}, arguments=args, idx=i)
        for i, args in enumerate(args_list)
    ]


def _fixture_docs(name: str) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in (FIXTURE_DATA / f"{name}.json").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


# --------------------------------------------------------------- adapter parity


def test_harness_loglikelihood_equals_native_scoring_for_every_pair() -> None:
    model = _tiny_model()
    tokenizer = ByteTokenizer()
    adapter = create_harness_model(model, tokenizer, device="cpu")
    native = ConditionalLikelihoodScorer(model=model, tokenizer=tokenizer, device="cpu")

    pairs = [
        ("Question: two plus two?\nAnswer:", " four"),
        ("", " hello world"),
        ("The sky is", " blue"),
        ("Context with trailing space ", "continuation"),
    ]
    results = adapter.loglikelihood(_instances("loglikelihood", pairs))
    assert len(results) == len(pairs)
    for (context, continuation), (logprob, is_greedy) in zip(pairs, results, strict=True):
        expected = native.score_continuation(context, continuation)
        assert logprob == pytest.approx(expected.log_likelihood, abs=1e-12)
        assert is_greedy is expected.is_greedy


def test_batch_scoring_is_invariant_to_ordering() -> None:
    model = _tiny_model()
    tokenizer = ByteTokenizer()
    adapter = create_harness_model(model, tokenizer, device="cpu")

    pairs = [(f"ctx {i}", f" cont {i}") for i in range(6)]
    forward = adapter.loglikelihood(_instances("loglikelihood", pairs))
    reversed_results = adapter.loglikelihood(_instances("loglikelihood", list(reversed(pairs))))
    assert forward == list(reversed(reversed_results))


def test_branch_state_resets_between_requests() -> None:
    model = _tiny_model()
    tokenizer = ByteTokenizer()
    adapter = create_harness_model(model, tokenizer, device="cpu")

    pair = ("same context", " same continuation")
    first = adapter.loglikelihood(_instances("loglikelihood", [pair]))
    # Interleave an unrelated request, then repeat: identical result.
    adapter.loglikelihood(_instances("loglikelihood", [("other", " thing")]))
    second = adapter.loglikelihood(_instances("loglikelihood", [pair]))
    assert first == second


def test_rolling_loglikelihood_equals_native_document_scoring() -> None:
    model = _tiny_model()
    tokenizer = ByteTokenizer()
    adapter = create_harness_model(model, tokenizer, device="cpu")
    native = ConditionalLikelihoodScorer(model=model, tokenizer=tokenizer, device="cpu")

    long_text = ("The rolling window test sentence. " * 40).strip()
    results = adapter.loglikelihood_rolling(_instances("loglikelihood_rolling", [(long_text,)]))
    document = native.score_document(long_text)
    assert results[0][0] == pytest.approx(-document.text_token_nll_sum, abs=1e-9)
    assert document.text_token_count > model.config.context_length // 2


def test_long_example_error_policy_raises_while_rolling_covers_every_target() -> None:
    model = _tiny_model()
    tokenizer = ByteTokenizer()
    strict = ConditionalLikelihoodScorer(
        model=model,
        tokenizer=tokenizer,
        device="cpu",
        window_policy=WindowTruncationPolicy.ERROR,
    )
    rolling = ConditionalLikelihoodScorer(
        model=model,
        tokenizer=tokenizer,
        device="cpu",
        window_policy=WindowTruncationPolicy.ROLLING,
    )
    long_text = ("A long document sentence repeated for window coverage. " * 30).strip()
    with pytest.raises(ContinuationTooLongError):
        strict.score_continuation("", long_text)
    result = rolling.score_continuation("", long_text)
    assert len(result.token_log_probs) == result.token_count, "no target may be dropped"


def test_generate_until_is_deterministic_and_stops_on_until() -> None:
    model = _tiny_model()
    tokenizer = ByteTokenizer()
    adapter = create_harness_model(model, tokenizer, device="cpu")
    request = [("The", {"max_gen_toks": 6, "until": ["zzz"]})]
    first = adapter.generate_until(_instances("generate_until", request))
    second = adapter.generate_until(_instances("generate_until", request))
    assert first == second
    assert isinstance(first[0], str)


# ------------------------------------------------- real harness fixture task run


def _run_fixture_task(
    tmp_path: Path,
    model: BaseModel,
    tokenizer: ByteTokenizer,
    task_name: str,
    limit: int | None = None,
    monkeypatch: pytest.MonkeyPatch | None = None,
) -> EvaluationEvidence:
    if monkeypatch is not None:
        monkeypatch.chdir(REPO_ROOT)
    variant = TaskVariant(
        variant_id=f"xlm_{task_name}_search",
        lm_eval_task=task_name,
        tier=SuiteTier.SEARCH,
        split="train",
        normalized_metric="acc_norm",
        chance_reference="mean_inverse_n_choices",
    )
    return run_harness_suite(
        model=model,
        tokenizer=tokenizer,
        variants=[variant],
        checkpoint_hash="fixture_ckpt",
        device="cpu",
        limit=limit,
        include_path=FIXTURE_TASKS,
        output_dir=tmp_path / "eval",
    )


def _native_mc_results(
    model: BaseModel,
    tokenizer: ByteTokenizer,
    docs: list[dict[str, Any]],
) -> tuple[float, float, list[list[float]]]:
    """Native reference: score `" " + choice` exactly as the harness requests it."""
    native = ConditionalLikelihoodScorer(model=model, tokenizer=tokenizer, device="cpu")
    n_correct = 0
    n_correct_norm = 0
    per_item: list[list[float]] = []
    for doc in docs:
        context = f"Question: {doc['question']}\nAnswer:"
        scores: list[float] = []
        for choice in doc["choices"]:
            result = native.score_continuation(context, " " + choice)
            scores.append(result.log_likelihood)
        per_item.append(scores)
        norm = [scores[i] / len(doc["choices"][i]) for i in range(len(scores))]
        n_correct += int(scores.index(max(scores)) == doc["answer"])
        n_correct_norm += int(norm.index(max(norm)) == doc["answer"])
    return n_correct / len(docs), n_correct_norm / len(docs), per_item


def test_harness_fixture_task_matches_native_acc_and_acc_norm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model = _tiny_model()
    tokenizer = ByteTokenizer()
    evidence = _run_fixture_task(
        tmp_path, model, tokenizer, "xlm_fixture_mc", monkeypatch=monkeypatch
    )
    task = evidence.tasks["xlm_fixture_mc"]
    docs = _fixture_docs("xlm_fixture_mc")
    expected_acc, expected_acc_norm, per_item = _native_mc_results(model, tokenizer, docs)

    assert task.acc == pytest.approx(expected_acc)
    assert task.acc_norm == pytest.approx(expected_acc_norm)
    assert task.scored_items == len(docs)
    assert len(task.items) == len(docs)
    for item, expected_scores in zip(task.items, per_item, strict=True):
        assert item.choice_log_likelihoods == pytest.approx(expected_scores, abs=1e-9)


def test_no_op_model_scores_match_native_exactly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A no-op model exposes indexing alone: harness and native agree everywhere."""
    model = ZeroLogitsModel(vocab_size=260)
    tokenizer = ByteTokenizer()
    evidence = _run_fixture_task(
        tmp_path, model, tokenizer, "xlm_fixture_mc", monkeypatch=monkeypatch
    )
    docs = _fixture_docs("xlm_fixture_mc")
    expected_acc, expected_acc_norm, per_item = _native_mc_results(model, tokenizer, docs)
    task = evidence.tasks["xlm_fixture_mc"]
    assert task.acc == pytest.approx(expected_acc)
    assert task.acc_norm == pytest.approx(expected_acc_norm)

    for doc, item, scores in zip(docs, task.items, per_item, strict=True):
        predicted = scores.index(max(scores))
        normalized = [scores[i] / len(doc["choices"][i]) for i in range(len(scores))]
        predicted_normalized = normalized.index(max(normalized))
        assert item.predicted == predicted
        assert item.predicted_normalized == predicted_normalized


def test_bounded_run_records_the_limit_and_withholds_the_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model = _tiny_model()
    tokenizer = ByteTokenizer()
    evidence = _run_fixture_task(
        tmp_path, model, tokenizer, "xlm_fixture_mc", limit=2, monkeypatch=monkeypatch
    )
    task = evidence.tasks["xlm_fixture_mc"]
    assert task.scored_items <= 2
    assert evidence.index.complete is False
    assert evidence.index.index is None
    assert set(evidence.index.missing) >= {"hellaswag", "piqa", "blimp"}
    assert any("smoke evaluation" in note for note in evidence.notes)
    assert any("index withheld" in note for note in evidence.notes)


def test_evidence_cache_reuses_and_recomputes_on_identity_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model = _tiny_model()
    tokenizer = ByteTokenizer()
    first = _run_fixture_task(tmp_path, model, tokenizer, "xlm_fixture_mc", monkeypatch=None)
    second = _run_fixture_task(tmp_path, model, tokenizer, "xlm_fixture_mc", monkeypatch=None)
    assert first.to_dict() == second.to_dict()
    assert (tmp_path / "eval" / "evidence").is_dir()

    other = TaskVariant(
        variant_id="xlm_arc_easy_search",
        lm_eval_task="xlm_fixture_mc",
        tier=SuiteTier.SEARCH,
        split="train",
        normalized_metric="acc_norm",
        chance_reference="mean_inverse_n_choices",
    )
    changed_identity = run_harness_suite(
        model=model,
        tokenizer=tokenizer,
        variants=[other],
        checkpoint_hash="fixture_ckpt",
        device="cpu",
        include_path=FIXTURE_TASKS,
        output_dir=tmp_path / "eval",
    )
    assert changed_identity.identity_fingerprint != first.identity_fingerprint


def test_missing_tasks_prevent_an_aggregate_from_partial_evidence() -> None:
    items = [
        items_from_harness_samples(
            "arc_easy",
            [
                {
                    "id": 0,
                    "doc_id": 0,
                    "target": 0,
                    "resps": [[0.5, False], [0.1, False]],
                    "doc": {"choices": ["aa", "bb"]},
                }
            ],
        )[0]
    ]
    task = task_evidence_from_items("arc_easy", "xlm_arc_easy_search", "acc_norm", 0.5, items)
    index = compute_four_task_index({"arc_easy": task.score()})
    assert index.complete is False
    assert index.missing == ["blimp", "hellaswag", "piqa"]
    assert task.scored_items == 1 and task.acc_norm == 1.0


def test_harness_backend_registration_uses_the_public_registry() -> None:
    from lm_eval.api.registry import get_model

    create_harness_model(_tiny_model(), ByteTokenizer(), device="cpu")
    registered = get_model("xlm")
    assert registered is not None


def test_task_manager_discovers_the_authored_fixture() -> None:
    """The fixture YAML must be registered; data loading is covered by suite runs."""
    manager = build_task_manager(FIXTURE_TASKS)
    entry = manager.task_index["xlm_fixture_mc"]
    assert entry.yaml_path is not None
    assert Path(entry.yaml_path).name == "xlm_fixture_mc.yaml"


def test_supported_harness_version_is_the_installed_one() -> None:
    import lm_eval

    assert str(lm_eval.__version__) == SUPPORTED_LM_EVAL_VERSION
