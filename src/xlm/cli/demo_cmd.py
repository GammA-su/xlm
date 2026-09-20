"""Bounded offline demo command verifying full vertical slice complying with P00-P06.

Complying with XLM Contracts and P06 Amendment 8:
- Self-contained offline vertical slice:
  synthetic data -> tokenization -> shard -> training ->
  checkpoint -> resume -> evaluate -> generate -> report.
- Declares 200-target budget and horizon from the start.
- Natural interruption at 100 targets (step 2), then resume to 200 targets.
- Offline diagnostic fixture evaluation (multiple-choice acc/acc_norm, minimal-pair, document BPB).
- Autoregressive text generation (greedy and nucleus sampled).
- Structured summary report.
"""

from __future__ import annotations

import importlib.util
import tempfile
from pathlib import Path

import typer


def demo_command() -> None:
    """Run an end-to-end bounded demo of tokenization, training, checkpointing, and resume."""
    if importlib.util.find_spec("torch") is None:
        typer.echo(
            "Error: PyTorch is required for demo. Run with uv run --extra cpu/cuda xlm demo",
            err=True,
        )
        raise typer.Exit(code=1)

    from xlm.artifacts.ledger import RunLedger
    from xlm.artifacts.store import ArtifactStore
    from xlm.config.schemas import (
        AdamWConfig,
        CrossEntropyObjectiveConfig,
        TransformerBaselineConfig,
        WarmupCosineScheduleConfig,
    )
    from xlm.core.contracts import CanonicalDocument
    from xlm.core.paths import ArtifactPaths
    from xlm.data.tokens import TokenShardReader, TokenShardWriter
    from xlm.evaluation.fixtures import (
        get_synthetic_minimal_pair_fixture,
        get_synthetic_multiple_choice_fixture,
    )
    from xlm.evaluation.likelihood import ConditionalLikelihoodScorer
    from xlm.evaluation.scorer import BenchmarkFixtureScorer
    from xlm.inference.generation import GenerationConfig, TextGenerator
    from xlm.models.serialization import load_model_for_inference
    from xlm.models.transformer import TransformerBaseline
    from xlm.objectives.cross_entropy import CrossEntropyObjective
    from xlm.optimizers.adamw import create_adamw_optimizer
    from xlm.schedules.cosine import WarmupCosineSchedule
    from xlm.tokenizers.byte import ByteTokenizer
    from xlm.training.checkpoint import CheckpointManager
    from xlm.training.data import TrainingBatcher
    from xlm.training.trainer import Trainer

    typer.echo("=" * 60)
    typer.echo("XLM Milestone Demo: Complete Offline Vertical Slice (P00-P06)")
    typer.echo("=" * 60)

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        tmp_root = Path(tmpdir)
        paths = ArtifactPaths(root=tmp_root / "artifacts")
        store = ArtifactStore(paths)
        ledger = RunLedger(paths.ledger / "ledger.sqlite")
        checkpoint_manager = CheckpointManager(artifact_store=store, run_ledger=ledger, paths=paths)

        # 1. Author synthetic documents
        typer.echo("1. Preparing synthetic canonical documents...")
        text_corpus = (
            "The quick brown fox jumps over the lazy dog. "
            "Causal language modeling requires exact prediction target shifting. "
            "Every token in the sequence is predicted exactly once "
            "with zero gaps and zero duplicates. "
        ) * 5
        docs = [
            CanonicalDocument(
                doc_id="doc_demo_01",
                source_id="demo_synthetic",
                source_revision="1",
                source_file="demo.txt",
                source_row=1,
                raw_hash="hash_raw_demo",
                clean_hash="hash_clean_demo",
                text=text_corpus,
                utf8_byte_count=len(text_corpus.encode("utf-8")),
                language="en",
                language_confidence=1.0,
                document_kind="text",
                source_metadata={},
                parent_ids=[],
                license_reference="CC-0",
                transform_log=[],
                quality_reasons=[],
                cluster_ids={},
                split="train",
            )
        ]

        # 2. Tokenize into immutable token shard
        typer.echo("2. Writing token shard using ByteTokenizer...")
        tok = ByteTokenizer()
        shard_dir = paths.token_shards / "demo_shard_01"
        writer = TokenShardWriter(
            output_dir=shard_dir,
            shard_id="demo_shard_01",
            source_id="demo_synthetic",
            tokenizer=tok,
        )
        manifest = writer.write_documents(docs)
        typer.echo(f"   Wrote {manifest.num_tokens} tokens into shard '{manifest.shard_id}'")

        # 3. Model & optimizer setup
        typer.echo("3. Initializing tiny TransformerBaseline model and components...")
        config = TransformerBaselineConfig(
            architecture="transformer_baseline",
            vocab_size=tok.vocab_size,
            num_layers=2,
            hidden_size=32,
            num_attention_heads=2,
            intermediate_size=64,
            context_length=32,
            attention_backend="eager",
            tie_embeddings=True,
        )
        model = TransformerBaseline(config, seed=42)
        obj = CrossEntropyObjective(CrossEntropyObjectiveConfig())
        opt_config = AdamWConfig(lr=0.01, weight_decay=0.01)
        opt, opt_manifest = create_adamw_optimizer(opt_config, model=model, objective=obj)
        sched_config = WarmupCosineScheduleConfig(
            warmup_valid_targets=20,
            horizon_valid_targets=200,
            min_lr_ratio=0.1,
        )
        sched = WarmupCosineSchedule(sched_config, base_lr=0.01)

        reader = TokenShardReader(shard_dir)
        batcher = TrainingBatcher(
            data_source=reader,
            context_length=32,
            global_batch_valid_targets=50,
            exhaustion_policy="repeat_bounded",
            max_document_exposures=100,
        )

        # 4. First leg: train to 100 targets (2 updates of 50 targets)
        typer.echo("4. Executing Part 1: Training to 100 targets (2 updates x 50 targets)...")
        trainer_1 = Trainer(
            model=model,
            objective=obj,
            optimizer=opt,
            optimizer_manifest=opt_manifest,
            schedule=sched,
            batcher=batcher,
            checkpoint_manager=checkpoint_manager,
            # Each completed demo phase publishes its own immutable final checkpoint.
            run_id="demo_run_p05_part1",
            plan_id="demo_plan_200",
            device="cpu",
            precision="fp32",
            gradient_clip_norm=1.0,
            max_valid_targets=100,
        )
        summary_1 = trainer_1.train()
        typer.echo(
            f"   Part 1 completed: {summary_1.committed_valid_targets} targets, "
            f"steps={summary_1.total_steps}"
        )
        l_start = summary_1.metrics_history[0].loss
        l_end = summary_1.metrics_history[-1].loss
        typer.echo(f"   Step 1 loss: {l_start:.4f} -> Step 2 loss: {l_end:.4f}")

        # Save checkpoint at natural boundary
        chk_dir = checkpoint_manager.save_checkpoint(
            checkpoint_id="demo_chk_step_2_100",
            run_id="demo_run_p05_part1",
            step=trainer_1.step,
            committed_valid_targets=trainer_1.committed_valid_targets,
            processed_valid_targets=trainer_1.processed_valid_targets,
            plan_id="demo_plan_200",
            model=model,
            objective=obj,
            optimizer=opt,
            optimizer_manifest=opt_manifest,
            schedule=sched,
            batcher=batcher,
        )
        typer.echo(f"   Published checkpoint at: {chk_dir.name}")

        # 5. Second leg: fresh reconstruction and resume to 200 targets
        typer.echo(
            "5. Executing Part 2: Reconstructing fresh components and resuming to 200 targets..."
        )
        fresh_model = TransformerBaseline(config, seed=999)
        fresh_obj = CrossEntropyObjective(CrossEntropyObjectiveConfig())
        fresh_opt, fresh_manifest = create_adamw_optimizer(
            opt_config, model=fresh_model, objective=fresh_obj
        )
        fresh_sched = WarmupCosineSchedule(sched_config, base_lr=0.01)
        fresh_batcher = TrainingBatcher(
            data_source=reader,
            context_length=32,
            global_batch_valid_targets=50,
            exhaustion_policy="repeat_bounded",
            max_document_exposures=100,
        )

        chk_meta = checkpoint_manager.load_checkpoint(
            chk_dir,
            model=fresh_model,
            objective=fresh_obj,
            optimizer=fresh_opt,
            optimizer_manifest=fresh_manifest,
            schedule=fresh_sched,
            batcher=fresh_batcher,
            expected_plan_id="demo_plan_200",
        )
        typer.echo(
            f"   Loaded checkpoint: step={chk_meta.step}, "
            f"committed_targets={chk_meta.committed_valid_targets}"
        )

        trainer_2 = Trainer(
            model=fresh_model,
            objective=fresh_obj,
            optimizer=fresh_opt,
            optimizer_manifest=fresh_manifest,
            schedule=fresh_sched,
            batcher=fresh_batcher,
            checkpoint_manager=checkpoint_manager,
            run_id="demo_run_p05",
            plan_id="demo_plan_200",
            device="cpu",
            precision="fp32",
            gradient_clip_norm=1.0,
            max_valid_targets=200,
            parent_checkpoint_id=chk_meta.checkpoint_id,
            parent_plan_id=chk_meta.plan_id,
            step=chk_meta.step,
            committed_valid_targets=chk_meta.committed_valid_targets,
            processed_valid_targets=chk_meta.processed_valid_targets,
        )
        summary_2 = trainer_2.train()
        typer.echo(
            f"   Part 2 completed: {summary_2.committed_valid_targets} targets, "
            f"steps={summary_2.total_steps}"
        )
        typer.echo(f"   Final loss: {summary_2.final_loss:.4f}")

        # Save final checkpoint at 200 targets
        chk_final_dir = checkpoint_manager.save_checkpoint(
            checkpoint_id="demo_chk_step_4_200",
            run_id="demo_run_p05",
            step=trainer_2.step,
            committed_valid_targets=trainer_2.committed_valid_targets,
            processed_valid_targets=trainer_2.processed_valid_targets,
            plan_id="demo_plan_200",
            model=fresh_model,
            objective=fresh_obj,
            optimizer=fresh_opt,
            optimizer_manifest=fresh_manifest,
            schedule=fresh_sched,
            batcher=fresh_batcher,
            parent_checkpoint_id=chk_meta.checkpoint_id,
            parent_plan_id=chk_meta.plan_id,
        )
        typer.echo(f"   Published final checkpoint at: {chk_final_dir.name}")

        # 6. Part 3: Evaluate diagnostic fixtures and document metrics
        typer.echo(
            "6. Executing Part 3: Evaluating offline diagnostic fixtures & document metrics..."
        )
        eval_model = load_model_for_inference(chk_final_dir, device="cpu")
        scorer = ConditionalLikelihoodScorer(model=eval_model, tokenizer=tok, device="cpu")
        fixture_scorer = BenchmarkFixtureScorer(
            scorer=scorer,
            model_hash="demo_chk_step_4_200",
            tokenizer_hash=tok.fingerprint,
        )

        mc_dataset = get_synthetic_multiple_choice_fixture()
        mc_receipt, _ = fixture_scorer.evaluate_dataset(mc_dataset)
        mc_acc = mc_receipt.metrics["acc"]
        mc_norm = mc_receipt.metrics["acc_norm"]
        typer.echo(f"   Multiple Choice Acc: {mc_acc:.2%}, Acc_Norm: {mc_norm:.2%}")

        pair_dataset = get_synthetic_minimal_pair_fixture()
        pair_receipt, _ = fixture_scorer.evaluate_dataset(pair_dataset)
        pair_acc = pair_receipt.metrics["acc"]
        pair_margin = pair_receipt.metrics["margin_mean"]
        typer.echo(f"   Minimal Pair Acc: {pair_acc:.2%}, Margin: {pair_margin:.4f}")

        doc_eval = scorer.score_document(docs[0])
        typer.echo(
            f"   Synthetic Document Text BPB: {doc_eval.text_bpb:.4f}, "
            f"Perplexity: {doc_eval.text_ppl:.2f}"
        )

        # 7. Part 4: Autoregressive text generation
        typer.echo("7. Executing Part 4: Autoregressive text generation...")
        generator = TextGenerator(model=eval_model, tokenizer=tok, device="cpu")

        test_prompt = "The quick brown"
        greedy_res = generator.generate(
            test_prompt, GenerationConfig(max_new_tokens=16, do_sample=False)
        )
        typer.echo(
            f"   Greedy Completion: {greedy_res.full_text!r} (finish: {greedy_res.finish_reason})"
        )

        sample_res = generator.generate(
            test_prompt,
            GenerationConfig(max_new_tokens=16, do_sample=True, temperature=0.8, top_k=5, seed=42),
        )
        typer.echo(
            f"   Sampled Completion: {sample_res.full_text!r} (finish: {sample_res.finish_reason})"
        )

        # 8. Part 5: Structured summary report
        typer.echo("8. Executing Part 5: Emitting structured vertical slice summary report...")
        typer.echo("-" * 60)
        typer.echo("Summary of Milestone Verification (P00 - P06):")
        typer.echo(f"  - Training Targets Committed : {summary_2.committed_valid_targets} / 200")
        typer.echo(f"  - Total Optimizer Steps      : {summary_2.total_steps}")
        typer.echo(
            f"  - Step 1 Loss -> Step 4 Loss : {summary_1.metrics_history[0].loss:.4f} -> "
            f"{summary_2.final_loss:.4f}"
        )
        typer.echo(f"  - Offline MC Accuracy        : {mc_acc:.2%} (norm: {mc_norm:.2%})")
        typer.echo(f"  - Minimal Pair Accuracy      : {pair_acc:.2%}")
        typer.echo(f"  - Document Text BPB          : {doc_eval.text_bpb:.4f}")
        typer.echo(f"  - Generated Tokens (Greedy)  : {len(greedy_res.generated_token_ids)}")
        typer.echo(f"  - Generated Tokens (Sampled) : {len(sample_res.generated_token_ids)}")
        typer.echo("-" * 60)

        # Validate complete vertical slice properties
        assert summary_2.committed_valid_targets == 200
        assert summary_2.total_steps == 4
        assert summary_2.termination_reason == "completed"
        assert doc_eval.text_bpb > 0.0
        assert len(greedy_res.generated_token_ids) > 0
        assert len(sample_res.generated_token_ids) > 0

    typer.echo("=" * 60)
    typer.echo("Demo Successfully Completed: 200 Valid Targets Reached!")
    typer.echo("Complete Offline Vertical Slice Successfully Completed (P00-P06)!")
    typer.echo("=" * 60)
