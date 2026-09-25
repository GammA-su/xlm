"""P35 M2: frozen LM validation inventories, text CE, UTF-8 BPB and domain aggregation.

Every expected value is computed independently of the scorer, from the closed
form of :class:`SuccessorModel` over a code-point tokenizer whose tokens are
deliberately not UTF-8 bytes.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from p35_eval_support import (
    CodepointTokenizer,
    SuccessorModel,
    hand_document,
    write_inventory,
)
from xlm.evaluation.cadence import EventTier
from xlm.evaluation.lm_validation import (
    PRIMARY_METRIC,
    DomainStatistics,
    LMScoringPolicy,
    LMValidationEvaluator,
    ValidationManifestError,
    aggregate_domain_metrics,
    load_pinned_inventory,
    load_validation_manifest,
)
from xlm.evaluation.outcome import EvaluationContext, InvalidMetricError, MissingDomainError
from xlm.tokenizers.byte import ByteTokenizer

LN2 = math.log(2.0)


def evaluate(directory: Path, domains: dict, *, context: int = 64, stride: int = 32) -> dict:
    tokenizer = CodepointTokenizer()
    path, manifest_id = write_inventory(directory, domains, tokenizer)
    inventory = load_pinned_inventory(
        path, manifest_id=manifest_id, tokenizer_fingerprint=tokenizer.fingerprint
    )
    evaluator = LMValidationEvaluator(
        EventTier.FULL_LM,
        inventory,
        tokenizer,
        LMScoringPolicy(context_length=context, rolling_stride=stride),
    )
    outcome = evaluator.evaluate(
        SuccessorModel(context),
        device="cpu",
        context=EvaluationContext("full_lm@0", 0, "digest", {}),
    )
    return {"metrics": outcome.metrics, "coverage": outcome.coverage}


def hand_domain(documents: list[tuple[str, str]]) -> dict[str, float]:
    tokenizer = CodepointTokenizer()
    docs = [hand_document(tokenizer, text) for _, text in documents]
    return {key: sum(d[key] for d in docs) for key in docs[0]}


def test_text_ce_bpb_and_eos_diagnostic_match_hand_computation(tmp_path: Path) -> None:
    # "abc": 'b' and 'c' follow their predecessor (hits); "zz" has none.
    documents = [("a", "abc"), ("z", "zz")]
    result = evaluate(tmp_path, {"prose": documents})["metrics"]
    domain = result["domains"]["prose"]
    hand = hand_domain(documents)
    assert domain["text_tokens"] == 5 and domain["text_utf8_bytes"] == 5
    assert math.isclose(domain["text_nll_nats"], hand["text_nll"], rel_tol=1e-12)
    assert math.isclose(domain["text_ce_nats_per_token"], hand["text_nll"] / 5, rel_tol=1e-12)
    assert math.isclose(domain["text_bpb"], hand["text_nll"] / (LN2 * 5), rel_tol=1e-12)
    assert math.isclose(
        domain["text_token_perplexity"], math.exp(hand["text_nll"] / 5), rel_tol=1e-12
    )
    # The structural EOS target is excluded from text CE and reported separately.
    assert math.isclose(domain["eos_nll_nats"], hand["eos_nll"], rel_tol=1e-12)
    eos_ce = (hand["text_nll"] + hand["eos_nll"]) / 7
    assert math.isclose(domain["eos_inclusive_ce_nats_per_token_diagnostic"], eos_ce, rel_tol=1e-12)
    assert not math.isclose(domain["text_ce_nats_per_token"], eos_ce, rel_tol=1e-6)


def test_pooled_ce_is_not_a_mean_of_document_means(tmp_path: Path) -> None:
    documents = [("a", "abc"), ("z", "zz")]
    metrics = evaluate(tmp_path, {"prose": documents})["metrics"]
    tokenizer = CodepointTokenizer()
    per_doc = [hand_document(tokenizer, text) for _, text in documents]
    mean_of_means = sum(d["text_nll"] / d["tokens"] for d in per_doc) / len(per_doc)
    pooled = sum(d["text_nll"] for d in per_doc) / sum(d["tokens"] for d in per_doc)
    ce = metrics["domains"]["prose"]["text_ce_nats_per_token"]
    assert math.isclose(ce, pooled, rel_tol=1e-12)
    assert abs(ce - mean_of_means) > 1e-3


@pytest.mark.parametrize(
    ("text", "code_points", "utf8_bytes"),
    [
        ("plain ascii", 11, 11),
        ("é", 1, 2),
        ("€ 5", 3, 5),
        ("😀x", 2, 5),
        ("naïve café 😀", 12, 17),
    ],
)
def test_bpb_uses_actual_utf8_bytes_not_characters_or_tokens(
    tmp_path: Path, text: str, code_points: int, utf8_bytes: int
) -> None:
    domain = evaluate(tmp_path, {"d": [("x", text)]})["metrics"]["domains"]["d"]
    hand = hand_domain([("x", text)])
    assert domain["text_tokens"] == code_points  # one token per code point
    assert domain["text_utf8_bytes"] == utf8_bytes == len(text.encode("utf-8"))
    assert math.isclose(domain["text_bpb"], hand["text_nll"] / (LN2 * utf8_bytes), rel_tol=1e-12)
    if code_points != utf8_bytes:
        # A token- or character-count denominator would give a different number.
        assert not math.isclose(
            domain["text_bpb"], hand["text_nll"] / (LN2 * code_points), rel_tol=1e-6
        )


def test_bytes_are_counted_after_canonical_normalization(tmp_path: Path) -> None:
    decomposed = "é"  # NFD: 3 UTF-8 bytes; canonical NFC "é" is 2
    domain = evaluate(tmp_path, {"d": [("x", decomposed)]})["metrics"]["domains"]["d"]
    assert len(decomposed.encode("utf-8")) == 3
    assert domain["text_utf8_bytes"] == 2 and domain["text_tokens"] == 1


def test_domains_keep_sufficient_statistics_and_equal_fixed_weights(tmp_path: Path) -> None:
    domains = {
        "large": [("l1", "abcdefghijklmnop"), ("l2", "qrstuvwxyz abcdef")],
        "small": [("s1", "zz")],
    }
    metrics = evaluate(tmp_path, domains)["metrics"]
    large, small = hand_domain(domains["large"]), hand_domain(domains["small"])
    ce_large = large["text_nll"] / large["tokens"]
    ce_small = small["text_nll"] / small["tokens"]
    assert metrics["domain_weights"] == {"large": 0.5, "small": 0.5}
    assert metrics["primary_metric"] == PRIMARY_METRIC
    assert math.isclose(metrics[PRIMARY_METRIC], (ce_large + ce_small) / 2, rel_tol=1e-12)
    micro = (large["text_nll"] + small["text_nll"]) / (large["tokens"] + small["tokens"])
    assert math.isclose(metrics["micro_text_ce_nats_per_token"], micro, rel_tol=1e-12)
    # Equal domain weights are not size weights.
    assert abs(metrics[PRIMARY_METRIC] - micro) > 1e-3
    bpb = (
        large["text_nll"] / (LN2 * large["bytes"]) + small["text_nll"] / (LN2 * small["bytes"])
    ) / 2
    assert math.isclose(metrics["equal_domain_text_bpb"], bpb, rel_tol=1e-12)


def test_missing_extra_or_empty_domains_fail_closed() -> None:
    filled = DomainStatistics(1, 3.0, 3, 3, 1.0, 1)
    with pytest.raises(MissingDomainError, match="produced no result"):
        aggregate_domain_metrics({"a": filled}, ["a", "b"])
    with pytest.raises(MissingDomainError, match="outside the frozen set"):
        aggregate_domain_metrics({"a": filled, "c": filled}, ["a"])
    with pytest.raises(MissingDomainError, match="scored nothing"):
        aggregate_domain_metrics({"a": filled, "b": DomainStatistics()}, ["a", "b"])


def test_rolling_windows_score_every_target_exactly_once(tmp_path: Path) -> None:
    long_text = "abcdefghijklmnopqrstuvwxyz" * 3  # 78 tokens > context 8
    windowed = evaluate(tmp_path / "w", {"d": [("x", long_text)]}, context=8, stride=3)
    single = evaluate(tmp_path / "s", {"d": [("x", long_text)]}, context=128, stride=64)
    a, b = windowed["metrics"]["domains"]["d"], single["metrics"]["domains"]["d"]
    assert a["text_tokens"] == b["text_tokens"] == 78
    # A first-order oracle is context-independent: complete coverage gives equal NLL.
    assert math.isclose(a["text_nll_nats"], b["text_nll_nats"], rel_tol=1e-12)
    assert windowed["coverage"]["complete"] is True
    with pytest.raises(ValueError, match="rolling_stride"):
        LMScoringPolicy(context_length=8, rolling_stride=8)  # would skip boundary targets


def test_manifest_identity_is_pinned_and_verified(tmp_path: Path) -> None:
    tokenizer = CodepointTokenizer()
    path, manifest_id = write_inventory(tmp_path, {"d": [("x", "abc"), ("y", "déf")]}, tokenizer)
    inventory = load_pinned_inventory(
        path, manifest_id=manifest_id, tokenizer_fingerprint=tokenizer.fingerprint
    )
    assert inventory.summary()["domains"]["d"] == {
        "source_ids": ["src_d"],
        "documents": 2,
        "text_utf8_bytes": 7,
    }
    with pytest.raises(ValidationManifestError, match="never accepted"):
        load_pinned_inventory(
            path, manifest_id="latest", tokenizer_fingerprint=tokenizer.fingerprint
        )
    with pytest.raises(ValidationManifestError, match="different tokenizer"):
        load_pinned_inventory(
            path, manifest_id=manifest_id, tokenizer_fingerprint=ByteTokenizer().fingerprint
        )
    documents = tmp_path / "d.jsonl"
    original = documents.read_bytes()
    documents.write_bytes(original.replace(b"abc", b"abd"))  # same length, new content
    with pytest.raises(ValidationManifestError, match="bytes differ"):
        load_pinned_inventory(
            path, manifest_id=manifest_id, tokenizer_fingerprint=tokenizer.fingerprint
        )
    documents.write_bytes(original)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["domains"][0]["document_ids"].append("ghost")
    payload.pop("manifest_id")
    path.write_text(json.dumps(payload), encoding="utf-8")
    tampered = load_validation_manifest(path)
    with pytest.raises(ValidationManifestError, match="membership differs"):
        load_pinned_inventory(
            path, manifest_id=tampered.manifest_id(), tokenizer_fingerprint=tokenizer.fingerprint
        )


def test_malformed_inventories_are_refused(tmp_path: Path) -> None:
    tokenizer = CodepointTokenizer()
    with pytest.raises(ValidationManifestError, match="declares no documents"):
        write_inventory(tmp_path / "empty", {"d": []}, tokenizer)
    with pytest.raises(ValidationManifestError, match="declares no text bytes"):
        write_inventory(tmp_path / "blank", {"d": [("x", "")]}, tokenizer)
    path, manifest_id = write_inventory(
        tmp_path / "mixed", {"d": [("x", "a"), ("y", "")]}, tokenizer
    )
    with pytest.raises(ValidationManifestError, match="empty after normalization"):
        load_pinned_inventory(
            path, manifest_id=manifest_id, tokenizer_fingerprint=tokenizer.fingerprint
        )
    with pytest.raises(ValidationManifestError, match="repeats a document id"):
        write_inventory(tmp_path / "dup", {"a": [("x", "a")], "b": [("x", "b")]}, tokenizer)
    with pytest.raises(ValidationManifestError, match="nesting manifest"):
        write_inventory(tmp_path / "nest", {"a": [("x", "a")]}, tokenizer, subset="quick")


def test_evaluator_refuses_mismatched_tiers_and_tokenizers(tmp_path: Path) -> None:
    tokenizer = CodepointTokenizer()
    path, manifest_id = write_inventory(tmp_path, {"d": [("x", "abc")]}, tokenizer)
    inventory = load_pinned_inventory(
        path, manifest_id=manifest_id, tokenizer_fingerprint=tokenizer.fingerprint
    )
    policy = LMScoringPolicy(context_length=64, rolling_stride=32)
    with pytest.raises(ValidationManifestError, match="nested quick subset"):
        LMValidationEvaluator(EventTier.QUICK_LM, inventory, tokenizer, policy)
    with pytest.raises(ValidationManifestError, match="lm_confirmation"):
        LMValidationEvaluator(EventTier.ENDPOINT_CONFIRMATION, inventory, tokenizer, policy)
    with pytest.raises(ValidationManifestError, match="tokenizer"):
        LMValidationEvaluator(EventTier.FULL_LM, inventory, ByteTokenizer(), policy)
    evaluator = LMValidationEvaluator(EventTier.FULL_LM, inventory, tokenizer, policy)
    with pytest.raises(InvalidMetricError, match="context"):
        evaluator.evaluate(
            SuccessorModel(32), device="cpu", context=EvaluationContext("e", 0, "d", {})
        )
