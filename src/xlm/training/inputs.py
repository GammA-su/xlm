"""Explicit local training inputs; missing artifacts never select fixture data."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from xlm.core.paths import ArtifactPaths
from xlm.data.input_limits import (
    AGGREGATE_INPUT_FILES,
    MAX_AGGREGATE_FROZEN_INPUT_BYTES,
    MAX_FROZEN_SHARD_INPUT_BYTES,
    MAX_SHARD_JSON_BYTES,
    SHARD_INPUT_FILES,
)
from xlm.data.tokens import TokenShardReader


@dataclass
class MixtureInput:
    recipe: Any
    readers: dict[str, TokenShardReader]
    #: P35 M5: the verified frozen within-source order manifest, if one is bound.
    document_order: dict[str, Any] | None = None


def _shard(path: Path) -> TokenShardReader:
    from xlm.artifacts.manifest import ensure_plain_path

    total = 0
    for name in SHARD_INPUT_FILES:
        item = path / name
        ensure_plain_path(item)
        if item.is_file():
            total += item.stat().st_size
            if total > MAX_FROZEN_SHARD_INPUT_BYTES or (
                name.endswith(".json") and item.stat().st_size > MAX_SHARD_JSON_BYTES
            ):
                raise ValueError("frozen shard input exceeds byte limit")
    reader = TokenShardReader(path)
    reader.verify_integrity()
    return reader


def _validate_training_index(reader: TokenShardReader) -> None:
    """Validate bounded document metadata without materializing a corpus."""
    cursor = count = 0
    with (reader.directory / "offsets.jsonl").open("rb") as stream:
        while raw := stream.readline(8 * 1024**2 + 1):
            if len(raw) > 8 * 1024**2:
                raise ValueError("document index entry exceeds 8 MiB")
            record = json.loads(raw)
            if record.get("source_id") != reader.manifest.source_id:
                raise ValueError("document source differs from shard source")
            if record.get("split") != "train":
                raise ValueError("training requires explicitly train-split documents")
            tokens = record.get("token_count")
            if type(tokens) is not int or tokens < 1 or record.get("token_start") != cursor:
                raise ValueError("document token index must be contiguous and nonempty")
            spans = record.get("token_byte_spans")
            if spans is not None and (
                len(spans) != tokens
                or any(
                    len(span) != 2
                    or any(type(n) is not int for n in span)
                    or not 0 <= span[0] <= span[1] <= record["byte_count"]
                    for span in spans
                )
            ):
                raise ValueError("invalid per-token canonical byte spans")
            cursor += tokens
            count += 1
    if cursor != reader.manifest.num_tokens or count != reader.manifest.num_documents:
        raise ValueError("document index coverage differs from shard manifest")


def normalize_training_data(data: dict[str, Any], training: dict[str, Any]) -> None:
    """Resolve composed presets into the existing MixtureRecipe; no ignored policies."""
    from xlm.data.sampling import MixtureComponent, MixtureRecipe, PackingPolicy
    from xlm.data.sources.mix01 import MixturePreset, validate_preset_weights_exact

    if data.get("document_order") is not None and training.get("science_version") is None:
        # P35 M5 order manifests are a science-v1 capability; legacy runs keep shard order.
        raise ValueError("data.document_order requires training.science_version")
    details = data.get("mixture_details")
    if data.get("mixture_preset") and not details:
        raise ValueError("mixture_preset must be composed with its declared mixture_details")
    raw = data.get("mixture")
    if details:
        if raw:
            raise ValueError("choose mixture or composed mixture_details, not both")
        preset = MixturePreset.model_validate(details)
        validate_preset_weights_exact(preset)
        if preset.scheduler != "token_deficit_v1":
            raise ValueError("unsupported mixture scheduler")
        if preset.weight_unit != "valid_target_tokens":
            raise ValueError("only valid_target_tokens mixture weights are executable")
        if preset.source_seed != training["data_seed"]:
            raise ValueError("mixture source_seed differs from training data_seed")
        if preset.exhaustion_policy not in ("error", "repeat_bounded"):
            raise ValueError("unsupported mixture exhaustion policy")
        for field in ("pool_artifact", "tokenizer_artifact"):
            if getattr(preset, field) and getattr(preset, field) != data.get(field):
                raise ValueError(f"mixture preset {field} differs from execution binding")
        raw = {
            "mixture_id": preset.id,
            "components": [{"source_id": s, "weight": w} for s, w in preset.weights.items()],
            "exhaustion": {
                "repeat": preset.exhaustion_policy == "repeat_bounded",
                "max_epochs": preset.max_document_exposures,
            },
        }
    packing = data.get("packing_policy")
    if isinstance(packing, str):
        packing = {"mode": {"causal_stream_eos": "causal_stream"}.get(packing, packing)}
    if packing is not None:
        packing = dict(packing)
        if "cross_document_attention" in data:
            if (
                "cross_document_attention" in packing
                and packing["cross_document_attention"] != data["cross_document_attention"]
            ):
                raise ValueError("conflicting packing cross_document_attention declarations")
            packing["cross_document_attention"] = data["cross_document_attention"]
        packing = PackingPolicy.model_validate(packing).model_dump(mode="json")
    elif "cross_document_attention" in data:
        packing = PackingPolicy(
            cross_document_attention=data["cross_document_attention"]
        ).model_dump(mode="json")
    if raw:
        raw = dict(raw)
        if packing is not None:
            if (
                "packing" in raw
                and PackingPolicy.model_validate(raw["packing"]).model_dump(mode="json") != packing
            ):
                raise ValueError("conflicting mixture and data packing policies")
            raw["packing"] = packing
        for field, expected in (
            ("data_seed", training["data_seed"]),
            ("model_seed", training["init_seed"]),
        ):
            if field in raw and raw[field] != expected:
                raise ValueError(f"mixture {field} differs from training seed")
            raw[field] = expected
        recipe = MixtureRecipe.model_validate(raw)
        data["mixture"] = recipe.model_dump(mode="json")
        for field in ("exhaustion_policy", "max_document_exposures"):
            if field in data:
                expected = (
                    ("repeat_bounded" if recipe.exhaustion.repeat else "error")
                    if field == "exhaustion_policy"
                    else recipe.exhaustion.max_epochs
                )
                if data[field] != expected:
                    raise ValueError(f"conflicting mixture {field}")
                data.pop(field)
        for field in (
            "mixture_details",
            "mixture_preset",
            "packing_policy",
            "cross_document_attention",
        ):
            data.pop(field, None)
    elif packing is not None:
        if "synthetic_tokens" in data:
            raise ValueError("document packing requires a shard with document metadata")
        # A single real shard can use the same packing/accounting path.
        reader = _shard(_source_path(data["pool_artifact"], ArtifactPaths.from_env()))
        data["mixture"] = MixtureRecipe(
            mixture_id="single_source",
            components=[MixtureComponent(source_id=reader.manifest.source_id, weight=1.0)],
            packing=PackingPolicy.model_validate(packing),
            data_seed=training["data_seed"],
            model_seed=training["init_seed"],
        ).model_dump(mode="json")
        data["sources"] = {reader.manifest.source_id: data.pop("pool_artifact")}
        data["mixture"]["exhaustion"] = {
            "repeat": data.get("exhaustion_policy", "error") == "repeat_bounded",
            "max_epochs": data.get("max_document_exposures", 1),
        }
        for field in ("packing_policy", "cross_document_attention"):
            data.pop(field, None)
        normalize_training_data(data, training)
    else:
        data.setdefault("exhaustion_policy", "error")
        data.setdefault("max_document_exposures", 1)


def _source_path(value: str, paths: ArtifactPaths) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError("source binding must be an explicit shard path/identifier")
    path = Path(value)
    return path if path.is_absolute() else paths.token_shards / path


def resolve_training_input(data: dict[str, Any], paths: ArtifactPaths) -> tuple[Any, str]:
    """Resolve a bounded authored token list or a verified single token shard."""
    unsupported = set(data) - {
        "synthetic_tokens",
        "pool_artifact",
        "tokenizer_artifact",
        "exhaustion_policy",
        "max_document_exposures",
        "mixture",
        "sources",
        "exposure_plan",
        "tokenizer",
        "document_order",
    }
    if unsupported:
        raise ValueError(f"unsupported training data fields: {sorted(unsupported)}; no fallback")
    if data.get("mixture"):
        from xlm.artifacts.manifest import identity_digest, validate_component
        from xlm.artifacts.store import compute_file_sha256
        from xlm.data.sampling import MixtureRecipe

        if "synthetic_tokens" in data:
            raise ValueError(
                "mixtures require real authored/prepared shards, not implicit token lists"
            )
        recipe = MixtureRecipe.model_validate(data["mixture"])
        bindings = data.get("sources", {})
        if not isinstance(bindings, dict) or len(recipe.components) > 64:
            raise ValueError("mixture source bindings must be a mapping with at most 64 sources")
        if set(bindings) - {c.source_id for c in recipe.components}:
            raise ValueError("unused source bindings are unsupported")
        readers = {}
        identities = {}
        input_bytes = 0
        for component in recipe.components:
            validate_component(component.source_id)
            reference = bindings.get(component.source_id) or component.shard_id
            if reference is None and data.get("pool_artifact"):
                reference = str(_source_path(data["pool_artifact"], paths) / component.source_id)
            if not reference:
                raise ValueError(f"missing shard for {component.source_id}; no substitution")
            path = _source_path(reference, paths)
            if (
                data.get("pool_artifact")
                and path.resolve()
                != (_source_path(data["pool_artifact"], paths) / component.source_id).resolve()
            ):
                raise ValueError("explicit source path differs from declared pool shard root")
            reader = _shard(path)
            _validate_training_index(reader)
            input_bytes += sum(
                (path / name).stat().st_size
                for name in AGGREGATE_INPUT_FILES
                if (path / name).is_file()
            )
            if input_bytes > MAX_AGGREGATE_FROZEN_INPUT_BYTES:
                raise ValueError("aggregate frozen shard inputs exceed 2 GiB")
            if reader.manifest.source_id != component.source_id:
                raise ValueError("shard source identity differs from declared mixture source")
            if component.shard_id and component.shard_id not in (
                reader.manifest.shard_id,
                reference,
            ):
                raise ValueError("declared shard_id differs from actual shard")
            readers[component.source_id] = reader
            identities[component.source_id] = {
                "manifest": asdict(reader.manifest),
                "counters": compute_file_sha256(path / "shard_counters.json")
                if (path / "shard_counters.json").is_file()
                else None,
            }
            bindings[component.source_id] = str(path.resolve())
        data["sources"] = bindings
        if data.get("pool_artifact") and len({r.manifest.pool_hash for r in readers.values()}) != 1:
            raise ValueError("source shard pool identities differ within declared pool")
        exposure = data.get("exposure_plan")
        if exposure is not None:
            from xlm.experiments.execution import read_json

            if isinstance(exposure, str):
                exposure = read_json(Path(exposure))
                data["exposure_plan"] = exposure
            if not isinstance(exposure, dict) or "basis" in exposure:
                raise ValueError(
                    "matched document/byte execution is deferred; "
                    "only token exposure plans are supported"
                )
            if (
                exposure.get("mixture_identity") != recipe.identity()
                or exposure.get("data_seed") != recipe.data_seed
            ):
                raise ValueError("exposure plan differs from the declared mixture/seed")
        identity: dict[str, Any] = {
            "sources": identities,
            "mixture": recipe.model_dump(mode="json"),
            "exposure": exposure,
        }
        order_manifest = None
        if data.get("document_order") is not None:
            from xlm.data.ordering import resolve_document_order

            # Verified against these exact shards: pins, derivation and membership.
            data["document_order"], order_manifest = resolve_document_order(
                data["document_order"], readers
            )
            # Only ordered inputs add the key, so shard-native identities keep their bytes.
            identity["document_order"] = {
                k: data["document_order"][k]
                for k in ("order_manifest_id", "canonical_membership_id")
            }
        return MixtureInput(recipe, readers, order_manifest), identity_digest(identity)
    if data.get("sources") or data.get("exposure_plan") or data.get("document_order"):
        raise ValueError("sources/exposure_plan/document_order require an explicit mixture")
    tokens = data.get("synthetic_tokens")
    shard = data.get("pool_artifact")
    if tokens is not None:
        if shard:
            raise ValueError("choose one explicit synthetic_tokens list or pool_artifact")
        if not isinstance(tokens, list) or not 2 <= len(tokens) <= 200_000:
            raise ValueError("synthetic_tokens must contain 2..200000 authored token IDs")
        if any(type(token) is not int or token < 0 for token in tokens):
            raise ValueError("synthetic_tokens must contain nonnegative integer IDs")
        return tokens, hashlib.sha256(json.dumps(tokens).encode()).hexdigest()
    if not isinstance(shard, str) or not shard:
        raise ValueError(
            "explicit training data is required; missing data never selects synthetic tokens"
        )
    path = _source_path(shard, paths)
    reader = _shard(path)
    data["pool_artifact"] = str(path.resolve())
    return reader, hashlib.sha256(
        json.dumps(asdict(reader.manifest), sort_keys=True).encode()
    ).hexdigest()


def resolve_training_tokenizer(
    data: dict[str, Any], catalog: dict[str, Any]
) -> tuple[Any, dict[str, Any]]:
    from xlm.experiments.execution import bounded_asset_hash
    from xlm.tokenizers.byte import ByteTokenizer
    from xlm.training.components import selected_entry

    reference = data.get("tokenizer_artifact")
    selection = data.get("tokenizer")
    tokenizer: Any
    identity: dict[str, Any] = {}
    if selection is not None:
        entry = selected_entry(catalog, "tokenizer", selection)
        config = entry.config_schema.model_validate(selection)
        data["tokenizer"] = config.model_dump(mode="json")
        if entry.factory is None:
            raise ValueError("selected tokenizer has no registered reconstruction factory")
        if entry.capabilities.get("loads_artifact") and not reference:
            raise ValueError("selected tokenizer requires an already fitted artifact")
        tokenizer = None if entry.capabilities.get("loads_artifact") else entry.factory(config)
        if reference:
            if reference == "byte":
                raise ValueError("custom tokenizer requires an explicit serialized artifact")
            location = Path(reference)
            if not location.is_absolute():
                location = ArtifactPaths.from_env().tokenizers / location
            identity["artifact_hash"] = bounded_asset_hash(location)
            tokenizer = (
                entry.factory(config, artifact=location)
                if entry.capabilities.get("loads_artifact")
                else type(tokenizer).load(location)
            )
            data["tokenizer_artifact"] = str(location.resolve())
        identity["component"] = {"key": entry.key, "serializer": entry.serializer_version}
    elif reference == "byte":
        tokenizer = ByteTokenizer()
        return tokenizer, {"type": "byte_fixture", "fingerprint": tokenizer.fingerprint}
    elif reference:
        from xlm.tokenizers.loading import load_inference_tokenizer

        location = Path(reference)
        if not location.is_absolute():
            location = ArtifactPaths.from_env().tokenizers / location
        identity["artifact_hash"] = bounded_asset_hash(location)
        tokenizer = load_inference_tokenizer(Path("."), str(location))
        data["tokenizer_artifact"] = str(location.resolve())
    elif "synthetic_tokens" in data:
        return None, {"type": "authored-token-ids", "text_tokenizer": None}
    else:
        raise ValueError("frozen shard execution requires its explicit tokenizer artifact")
    identity.update(fingerprint=tokenizer.fingerprint, type=type(tokenizer).__name__)
    return tokenizer, identity


def build_training_batcher(
    source: Any, data: dict[str, Any], training: dict[str, Any], tokenizer: Any
) -> Any:
    from xlm.data.sampling import MixtureBatcher
    from xlm.training.data import TrainingBatcher

    mode = training.get("producer_prefetch", "off")
    if mode not in ("off", "process_depth1"):
        raise ValueError("Unknown producer_prefetch mode")
    kwargs = {
        k: training[k]
        for k in ("context_length", "global_batch_valid_targets", "microbatch_sequences")
    }
    if tokenizer is not None:
        kwargs.update(
            pad_token_id=tokenizer.pad_token_id,
            bos_token_id=tokenizer.bos_token_id,
            eos_token_id=tokenizer.eos_token_id,
        )
    if isinstance(source, MixtureInput):
        batcher = MixtureBatcher(
            source.recipe,
            source.readers,
            emit_tensors=True,
            exposure_plan=data.get("exposure_plan"),
            document_order=source.document_order,
            **kwargs,
        )
        if mode == "process_depth1":
            from xlm.data.sampling.prefetch import PrefetchingBatcher, ProducerSpec

            producer = PrefetchingBatcher(
                ProducerSpec.from_batcher(batcher),
                batcher.get_state(),
                verify_content=True,
            )
            batcher.close()  # The child rebuilt its own readers; release the parent's maps.
            return producer
        return batcher
    if mode != "off":
        raise ValueError("Process producer requires an explicit mixture/shard packing input")
    return TrainingBatcher(
        source,
        exhaustion_policy=data.get("exhaustion_policy", "error"),
        max_document_exposures=data.get("max_document_exposures", 1),
        **kwargs,
    )
