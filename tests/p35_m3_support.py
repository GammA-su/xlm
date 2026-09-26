"""Authored toy science-pilot inputs for P35 M3 tests (no real data, no network).

Builds a two-source mixture from generated text, a small byte-level BPE
tokenizer, verified token shards, the frozen exposure plan, pinned LM
validation inventories and an ``authored_fixture`` pilot draft plus operator
bindings. None of it is a real corpus, tokenizer or pilot.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

from xlm.core.contracts import CanonicalDocument
from xlm.data.normalization import compute_sha256
from xlm.evaluation.lm_validation import build_validation_manifest, save_validation_manifest

ROOT = Path(__file__).resolve().parents[1]
SOURCES = {"alpha": 0.75, "zeta": 0.25}
WORDS = {
    "alpha": ["tide", "coast", "lunar", "harbor", "current", "reef", "swell", "estuary"],
    "zeta": ["solve", "value", "return", "list", "index", "loop", "sum", "branch"],
}


def document(doc_id: str, text: str, source_id: str) -> CanonicalDocument:
    raw = text.encode("utf-8")
    return CanonicalDocument(
        doc_id=doc_id,
        source_id=source_id,
        source_revision="authored_m3",
        source_file="authored.jsonl",
        source_row=0,
        raw_hash=compute_sha256(raw),
        clean_hash=compute_sha256(raw),
        text=text,
        utf8_byte_count=len(raw),
        language="en",
        language_confidence=1.0,
        document_kind="prose",
        source_metadata={},
        parent_ids=[],
        license_reference="authored-test-fixture",
        transform_log=[],
        quality_reasons=[],
        cluster_ids={},
        split="train",
    )


def generated_text(source: str, index: int, words: int) -> str:
    vocabulary = WORDS[source]
    return " ".join(vocabulary[(index * 7 + k * 3) % len(vocabulary)] for k in range(words)) + "."


def build_inputs(
    root: Path,
    *,
    budget: int,
    docs_per_source: int = 60,
    words: int = 60,
    context: int = 16,
    global_batch: int = 256,
    data_seed: int = 20260918,
    init_seed: int = 101,
    extra_training_docs: dict[str, list[tuple[str, str]]] | None = None,
) -> dict[str, Any]:
    """Author shards, tokenizer, exposure plan and inventories; return their bindings."""
    from xlm.data.sampling import (
        MixtureBatcher,
        MixtureRecipe,
        compile_exposure_plan,
        validate_mixture,
    )
    from xlm.data.tokens import TokenShardReader, TokenShardWriter
    from xlm.tokenizers.bpe import ByteLevelBPETokenizer
    from xlm.training.inputs import normalize_training_data

    data_root = root / "data"
    documents = {
        source: [
            document(f"{source}_{i}", generated_text(source, i, words), source)
            for i in range(docs_per_source)
        ]
        for source in SOURCES
    }
    for source, extra in (extra_training_docs or {}).items():
        documents[source] += [document(doc_id, text, source) for doc_id, text in extra]
    tokenizer = ByteLevelBPETokenizer.train_from_documents(
        [d for docs in documents.values() for d in docs],
        target_vocab_size=260,
        max_train_docs=16,
        max_train_bytes=4096,
    )
    tokenizer_dir = data_root / "tokenizer"
    tokenizer.save(tokenizer_dir)
    readers = {}
    for source, docs in documents.items():
        directory = data_root / "shards" / source
        writer = TokenShardWriter(
            directory, f"shard_{source}", source, tokenizer, pool_hash="authored_m3_pool"
        )
        writer.write_documents(docs, add_special_tokens=True)
        readers[source] = TokenShardReader(directory)
    data = {
        "mixture": {
            "mixture_id": "authored_two_source",
            "components": [{"source_id": s, "weight": w} for s, w in SOURCES.items()],
            "exhaustion": {"repeat": False, "max_epochs": 1},
        },
        "packing_policy": "causal_stream_eos",
        "cross_document_attention": True,
    }
    normalized = copy.deepcopy(data)
    normalize_training_data(normalized, {"data_seed": data_seed, "init_seed": init_seed})
    recipe = MixtureRecipe.model_validate(normalized["mixture"])
    batcher = MixtureBatcher(recipe, readers)
    try:
        exposure = compile_exposure_plan(
            recipe, validate_mixture(recipe, batcher.availability), budget, block_size=64
        ).to_dict()
    finally:
        batcher.close()
    exposure_path = data_root / "exposure.json"
    exposure_path.write_text(json.dumps(exposure, indent=2, sort_keys=True), encoding="utf-8")
    inventories = build_inventories(root / "eval", tokenizer)
    manifest = json.loads((tokenizer_dir / "tokenizer_manifest.json").read_text(encoding="utf-8"))
    return {
        "data": data,
        "sources": {s: str((data_root / "shards" / s).resolve()) for s in SOURCES},
        "exposure_plan": str(exposure_path.resolve()),
        "tokenizer_dir": tokenizer_dir.resolve(),
        "tokenizer": tokenizer,
        "fit_input_hash": manifest["training_input_hash"],
        "vocab_size": tokenizer.vocab_size,
        "inventories": inventories,
        "data_root": data_root.resolve(),
        "eval_root": (root / "eval").resolve(),
        "context": context,
        "global_batch": global_batch,
    }


def build_inventories(directory: Path, tokenizer: Any) -> dict[str, tuple[str, str]]:
    """Frozen-shaped full + nested quick inventories whose domains are the mixture sources."""
    full_docs = {
        "alpha": [("val_alpha_1", "the harbor current turns at dusk"), ("val_alpha_2", "reef")],
        "zeta": [("val_zeta_1", "return the sum of the list"), ("val_zeta_2", "loop index")],
    }
    quick_docs = {"alpha": [full_docs["alpha"][0]], "zeta": [full_docs["zeta"][0]]}

    def write(name: str, docs: dict[str, list[tuple[str, str]]], nested: str | None) -> Any:
        base = directory / name
        base.mkdir(parents=True, exist_ok=True)
        entries = []
        for domain, rows in docs.items():
            path = base / f"{domain}.jsonl"
            path.write_text(
                "".join(json.dumps({"doc_id": d, "text": t}) + "\n" for d, t in rows),
                encoding="utf-8",
            )
            entries.append(
                {"domain_id": domain, "source_ids": [domain], "documents_file": path.name}
            )
        manifest = build_validation_manifest(
            split="diagnostic_val",
            subset="quick" if nested else "full",
            scope_kind="authored_fixture",
            tokenizer_fingerprint=tokenizer.fingerprint,
            domains=entries,
            base_dir=base,
            nested_in=nested,
        )
        path = save_validation_manifest(manifest, base / "manifest.json")
        return str(path.resolve()), manifest.manifest_id()

    full = write("full", full_docs, None)
    quick = write("quick", quick_docs, full[1])
    return {"full": full, "quick": quick}


def tree_digest(path: Path) -> str:
    from xlm.prepare.integrity import path_digest

    return path_digest(path)


def toy_model(vocab_size: int, context: int) -> dict[str, Any]:
    return {
        "architecture": "transformer_baseline",
        "vocab_size": vocab_size,
        "num_layers": 1,
        "hidden_size": 16,
        "num_attention_heads": 2,
        "intermediate_size": 32,
        "context_length": context,
        "attention_backend": "sdpa",
        "dropout": 0.0,
    }


def authored_pilot_draft(
    inputs: dict[str, Any],
    *,
    budget: int,
    milestones: list[int],
    recovery: list[int],
    quick: list[int],
    full: list[int],
    total_wall_seconds: float = 600.0,
    training_seed: int = 10001,
) -> dict[str, Any]:
    """An ``authored_fixture`` pilot draft: same structure as the §W draft, toy scale."""
    from xlm.evaluation.cadence import update_arithmetic
    from xlm.training.components import inspect_model_shape

    base = json.loads(
        (ROOT / "recipes/experiments/draft_science_v1_pilot_32m.yaml").read_text(encoding="utf-8")
    )
    draft = copy.deepcopy(base)
    context, batch = inputs["context"], inputs["global_batch"]
    draft["id"] = "authored_m3_pilot"
    draft["model"] = toy_model(inputs["vocab_size"], context)
    draft["data"] = {
        **copy.deepcopy(inputs["data"]),
        "pool_artifact": None,
        "tokenizer_artifact": None,
        "tokenizer": {"type": "bpe", "target_vocab_size": 260},
        "exposure_plan": None,
    }
    training = draft["training"]
    training.update(
        context_length=context,
        global_batch_valid_targets=batch,
        microbatch_sequences=2,
        budget={"max_valid_targets": budget, "max_train_seconds": min(600, total_wall_seconds)},
        schedule={
            "type": "warmup_cosine",
            "counter": "committed_valid_targets",
            "horizon_valid_targets": budget,
            "warmup_valid_targets": batch,
            "min_lr_ratio": 0.1,
        },
        training_seed=training_seed,
        checkpoint_every_valid_targets=budget,
        checkpoint_cadence={
            "version": "xlm-checkpoint-cadence-v1",
            "cadence": "authored_fixture",
            "fixture_milestones": milestones,
            "fixture_recovery": recovery,
            "retention": "latest_two_recovery_plus_pinned_v1",
            "protected_references": [],
        },
    )
    draft["optimizer"]["lr"] = 0.01
    draft["resources"] = {
        "profile_artifact": None,
        "max_new_disk_gib": 1,
        "max_gpu_processes": 1,
        "total_wall_seconds": total_wall_seconds,
        "max_gpu_allocated_gib": 1,
        "max_process_tree_rss_gib": 8,
    }
    arithmetic = update_arithmetic(budget, batch)
    pilot = draft["science_pilot"]
    pilot["contract"] = "authored_fixture"
    # P35 M5: authored M3 flows keep the shard-native order (allowed for fixtures only).
    pilot["document_order"] = "shard_native_within_source_order"
    pilot["seed_tuple"] = "authored"
    pilot["expected"] = {
        **pilot["expected"],
        "parameters": inspect_model_shape({"model": draft["model"], "plugins": []}),
        "budget_valid_targets": budget,
        "global_batch_valid_targets": batch,
        "full_updates": arithmetic["full_updates"],
        "final_update_targets": arithmetic["final_update_targets"],
        "total_updates": arithmetic["total_updates"],
        "microbatch_sequences": 2,
        "context_length": context,
        "vocab_size": inputs["vocab_size"],
        "seeds": {"init_seed": 101, "training_seed": training_seed, "data_seed": 20260918},
        "lr": {
            "policy": "target_endpoint_before_update_v1",
            "base_lr": 0.01,
            "warmup_valid_targets": batch,
            "horizon_valid_targets": budget,
            "min_lr_ratio": 0.1,
        },
        "checkpoint_cadence": "authored_fixture",
        "evaluation_cadence": "authored_fixture",
        "mixture_preset": "authored_two_source",
        "mixture_components": sorted(SOURCES),
        "limits": {
            "total_wall_seconds": total_wall_seconds,
            "max_gpu_allocated_gib": 1,
            "max_process_tree_rss_gib": 8,
            "max_new_output_gib": 1,
            "max_gpu_processes": 1,
        },
    }
    pilot["evaluation"] = {
        "cadence": "authored_fixture",
        "fixture_thresholds": {"quick_lm": quick, "full_lm": full},
        "confirmation_registered": False,
        "quick_lm": None,
        "full_lm": None,
        "search_benchmark": None,
        "scoring": None,
    }
    pilot["cold_data"]["min_source_transitions"] = 2
    pilot["resume_verification"] = {"from_threshold": milestones[1], "fresh_process": True}
    return draft


def authored_bindings(
    inputs: dict[str, Any],
    *,
    home: Path,
    output_root: Path,
    size_source: str,
    safety_margin_bytes: int = 1024**2,
) -> dict[str, Any]:
    return {
        "version": "xlm-science-pilot-bindings-v1",
        "data": {
            "sources": dict(inputs["sources"]),
            "exposure_plan": inputs["exposure_plan"],
            "pool_artifact": None,
        },
        "tokenizer": {
            "artifact": str(inputs["tokenizer_dir"]),
            "artifact_digest": tree_digest(inputs["tokenizer_dir"]),
            "fit_input_hash": inputs["fit_input_hash"],
        },
        "evaluation": {
            "quick_lm": {
                "manifest": inputs["inventories"]["quick"][0],
                "manifest_id": inputs["inventories"]["quick"][1],
            },
            "full_lm": {
                "manifest": inputs["inventories"]["full"][0],
                "manifest_id": inputs["inventories"]["full"][1],
            },
            "search_benchmark": None,
            "scoring": {"forward_precision": "fp32", "logprob_dtype": "fp64", "rolling_stride": 4},
            "group_assignments": None,
        },
        "profile_artifact": "authored_m3_unmeasured_profile",
        "storage_roots": {
            "data_root": str(inputs["data_root"]),
            "checkpoint_root": str(home.resolve()),
            "temp_root": str(home.resolve()),
            "evaluation_input_roots": [str(inputs["eval_root"])],
            "output_root": str(output_root.resolve()),
        },
        "capacity": {
            "checkpoint_size_source": {"kind": "checkpoint_artifact", "path": size_source},
            "evaluation_evidence_bytes": 16 * 1024**2,
            "cache_bytes": 0,
            "safety_margin_bytes": safety_margin_bytes,
        },
    }


def fixture_size_checkpoint(
    directory: Path,
    inputs: dict[str, Any],
    *,
    payload_bytes: int = 4096,
    precision: str = "bf16_fp32_master",
) -> Path:
    """A verified store artifact standing in for a measured checkpoint (logic tests only)."""
    from xlm.artifacts.store import ArtifactStore
    from xlm.core.paths import ArtifactPaths

    model = toy_model(inputs["vocab_size"], inputs["context"])
    model.update(tie_embeddings=True)
    store = ArtifactStore(ArtifactPaths(root=directory))
    return store.publish_artifact(
        artifact_id="authored_size_source",
        kind="checkpoints",
        files={
            "model_config.json": json.dumps(model, sort_keys=True),
            "checkpoint_meta.json": json.dumps({"precision": precision}),
            "optimizer.pt": hashlib.sha256(b"x").hexdigest().encode() * (payload_bytes // 64),
        },
        producer_code_hash="authored-m3-test-fixture",
        dependency_hash="authored-m3-test-fixture",
        resolved_config_hash="authored-m3-test-fixture",
    )
