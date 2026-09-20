"""Unit tests for domain contracts, protocols, and data structures (Contract C01-C12)."""

import pytest

from xlm.core.contracts import (
    CanonicalDocument,
    ComparisonContract,
    EvaluationReceipt,
    InferenceInput,
    LMOutput,
    LossResult,
    ModelCapabilities,
    RunPlan,
    RunStatus,
    TokenShardManifest,
    TrainingBatch,
    validate_run_transition,
)


def test_canonical_document_validation() -> None:
    """Verify CanonicalDocument validates byte counts and splits strictly."""
    text = "Hello XLM world!"
    valid_bytes = len(text.encode("utf-8"))

    doc = CanonicalDocument(
        doc_id="doc_001",
        source_id="test_source",
        source_revision="rev1",
        source_file="file1.txt",
        source_row=0,
        raw_hash="a" * 64,
        clean_hash="b" * 64,
        text=text,
        utf8_byte_count=valid_bytes,
        language="en",
        language_confidence=0.99,
        document_kind="web",
        source_metadata={"url": "https://example.com"},
        parent_ids=[],
        license_reference="open_access",
        transform_log=[],
        quality_reasons=[],
        cluster_ids={},
        split="train",
    )
    assert doc.doc_id == "doc_001"
    assert doc.to_dict()["utf8_byte_count"] == valid_bytes

    # Mismatched byte count
    with pytest.raises(ValueError, match="utf8_byte_count mismatch"):
        CanonicalDocument(
            doc_id="doc_bad_bytes",
            source_id="test_source",
            source_revision="rev1",
            source_file="file1.txt",
            source_row=0,
            raw_hash="a" * 64,
            clean_hash="b" * 64,
            text=text,
            utf8_byte_count=valid_bytes + 10,
            language="en",
            language_confidence=0.99,
            document_kind="web",
            source_metadata={},
            parent_ids=[],
            license_reference="open_access",
            transform_log=[],
            quality_reasons=[],
            cluster_ids={},
            split="train",
        )

    # Invalid split
    with pytest.raises(ValueError, match="Invalid split"):
        CanonicalDocument(
            doc_id="doc_bad_split",
            source_id="test_source",
            source_revision="rev1",
            source_file="file1.txt",
            source_row=0,
            raw_hash="a" * 64,
            clean_hash="b" * 64,
            text=text,
            utf8_byte_count=valid_bytes,
            language="en",
            language_confidence=0.99,
            document_kind="web",
            source_metadata={},
            parent_ids=[],
            license_reference="open_access",
            transform_log=[],
            quality_reasons=[],
            cluster_ids={},
            split="invalid_split",
        )


def test_token_shard_manifest_validation() -> None:
    """Verify TokenShardManifest validates dtype, endianness, and byte coverage."""
    manifest = TokenShardManifest(
        shard_id="shard_001",
        source_id="source_a",
        num_tokens=1000,
        num_documents=10,
        token_dtype="uint16",
        endianness="little",
        tokenizer_hash="t" * 64,
        pool_hash="p" * 64,
        checksum_sha256="c" * 64,
        offsets_checksum_sha256="o" * 64,
        byte_coverage_ratio=0.995,
    )
    assert manifest.num_tokens == 1000

    # Invalid token_dtype
    with pytest.raises(ValueError, match="Invalid token_dtype"):
        TokenShardManifest(
            shard_id="shard_bad",
            source_id="source_a",
            num_tokens=1000,
            num_documents=10,
            token_dtype="float32",
            endianness="little",
            tokenizer_hash="t" * 64,
            pool_hash="p" * 64,
            checksum_sha256="c" * 64,
            offsets_checksum_sha256="o" * 64,
            byte_coverage_ratio=0.995,
        )


def test_inference_input_and_training_batch() -> None:
    """Verify inference view completely excludes training labels, loss masks, and metadata."""
    batch = TrainingBatch(
        input_ids=[1, 2, 3],
        labels=[2, 3, 4],
        loss_mask=[1, 1, 1],
        position_ids=[0, 1, 2],
        segment_ids=None,
        source_attribution=["src_a", "src_a", "src_a"],
        metadata={"token_quota": 3},
    )
    inf_view = batch.to_inference_view()
    assert isinstance(inf_view, InferenceInput)
    assert inf_view.input_ids == [1, 2, 3]
    assert inf_view.position_ids == [0, 1, 2]
    # Verify labels and metadata are not exposed on InferenceInput
    assert not hasattr(inf_view, "labels")
    assert not hasattr(inf_view, "source_attribution")
    assert not hasattr(inf_view, "metadata")


def test_lm_output_and_loss_result() -> None:
    """Verify LMOutput excludes target-dependent loss and LossResult tracks accumulation."""
    out = LMOutput(logits=[[0.1, 0.9]], auxiliary_outputs={"hidden_dim": 512})
    assert out.logits == [[0.1, 0.9]]
    assert not hasattr(out, "loss")  # Contract C08: LMOutput excludes target loss

    res = LossResult(
        loss_protocol="token_additive",
        loss=2.5,
        unscaled_loss_sum=250.0,
        valid_target_denominator=100,
        diagnostics={"perplexity": 12.18},
    )
    assert res.loss_protocol == "token_additive"
    assert res.valid_target_denominator == 100

    with pytest.raises(ValueError, match="Invalid loss_protocol"):
        LossResult(
            loss_protocol="unsupported_loss",
            loss=1.0,
            unscaled_loss_sum=1.0,
            valid_target_denominator=1,
        )


def test_run_status_state_machine() -> None:
    """Verify legal and illegal state transitions in RunStatus."""
    # Legal
    validate_run_transition(RunStatus.DRAFT, RunStatus.PLANNED)
    validate_run_transition(RunStatus.PLANNED, RunStatus.AUTHORIZED)
    validate_run_transition(RunStatus.AUTHORIZED, RunStatus.RUNNING)
    validate_run_transition(RunStatus.RUNNING, RunStatus.SUCCEEDED)
    validate_run_transition(RunStatus.RUNNING, RunStatus.FAILED)
    validate_run_transition(RunStatus.INTERRUPTED, RunStatus.RUNNING)

    # Illegal
    with pytest.raises(ValueError, match="Illegal run state transition"):
        validate_run_transition(RunStatus.DRAFT, RunStatus.RUNNING)
    with pytest.raises(ValueError, match="Illegal run state transition"):
        validate_run_transition(RunStatus.SUCCEEDED, RunStatus.RUNNING)


def test_model_capabilities_and_run_plan() -> None:
    """Verify ModelCapabilities flags and RunPlan records."""
    caps = ModelCapabilities(supports_kv_cache=True, max_context_length=1024)
    assert caps.supports_kv_cache is True
    assert caps.max_context_length == 1024

    plan = RunPlan(
        plan_id="p1",
        experiment_id="exp1",
        resolved_config_hash="h1" * 16,
        code_hash="c1" * 16,
        dependency_hash="d1" * 16,
        seed_policy={"init": 1},
        resource_caps={"max_proc": 1},
        created_at="2026-09-18T00:00:00Z",
    )
    assert plan.plan_id == "p1"
    assert plan.status == RunStatus.PLANNED


def test_evaluation_receipt_and_comparison_contract() -> None:
    """Verify EvaluationReceipt records C11 identity and ComparisonContract validates tracks."""
    receipt = EvaluationReceipt(
        receipt_id="rec_001",
        checkpoint_hash="ckpt_" + "0" * 59,
        tokenizer_hash="tok_" + "0" * 60,
        dataset_revision="rev_1",
        split_id="val",
        task_source_and_scorer_version="harness_v0.4.0",
        prompt_template_version="template_v1",
        metric_normalization_policy="char_length",
        context_truncation_policy="left_truncate",
        precision_mode="bf16",
        metrics={"acc": 0.72, "acc_norm": 0.75},
        scored_items_count=500,
        holdout_mode="search",
        created_at="2026-09-18T12:00:00Z",
    )
    assert receipt.holdout_mode == "search"
    assert receipt.to_dict()["metrics"]["acc"] == 0.72

    contract = ComparisonContract(
        contract_id="comp_001",
        track="architecture",
        baseline_run_id="run_base",
        candidate_run_id="run_cand",
        allowed_differences=("model.architecture",),
        materiality_threshold=1.0,
        frozen_fields_verified=True,
    )
    assert contract.track == "architecture"

    with pytest.raises(ValueError, match="Invalid comparison track"):
        ComparisonContract(
            contract_id="comp_bad",
            track="invalid_track",
            baseline_run_id="run_base",
            candidate_run_id="run_cand",
            allowed_differences=(),
            materiality_threshold=1.0,
            frozen_fields_verified=True,
        )
