"""Single-copy tied-tensor export storage and alias reconstruction (D07, C08).

C08 requires that tied storage is "saved and counted once". These tests check
the **actual serialized payload** by parsing the safetensors container header,
not the manifest's claim about itself: a bundle that merely asserted
single-copy storage would still fail here.

Everything is a tiny deterministically initialized CPU model with authored
inputs. No downloads, no GPU, no research-sized work.
"""

from __future__ import annotations

import hashlib
import json
import struct
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("torch")

import torch  # noqa: E402

from xlm.artifacts.store import ArtifactConflictError, ArtifactStore  # noqa: E402
from xlm.config.schemas import TransformerBaselineConfig  # noqa: E402
from xlm.core.paths import ArtifactPaths  # noqa: E402
from xlm.export.loader import ExportLoadError, load_exported_model, read_manifest  # noqa: E402
from xlm.export.manifest import ExportError, IncompatibleExportError  # noqa: E402
from xlm.export.writer import (  # noqa: E402
    export_model,
    export_representation_identity,
    publish_export_bundle,
)
from xlm.models.aliases import (  # noqa: E402
    AliasError,
    TensorAlias,
    UnsupportedAliasError,
    resolve_tied_aliases,
    validate_alias_map,
)
from xlm.models.transformer import TransformerBaseline  # noqa: E402
from xlm.tokenizers.byte import ByteTokenizer  # noqa: E402

#: Exact equality is required for everything here: the repair must not change a
#: single stored value. A tolerance would hide precisely the kind of silent
#: rewrite this stage is meant to rule out.
EXACT = 0.0


def tiny_config(tie: bool = True) -> TransformerBaselineConfig:
    return TransformerBaselineConfig(
        architecture="transformer_baseline",
        vocab_size=260,
        num_layers=2,
        hidden_size=64,
        num_attention_heads=4,
        intermediate_size=128,
        context_length=64,
        attention_backend="eager",
        tie_embeddings=tie,
    )


def tiny_model(tie: bool = True, seed: int = 7) -> TransformerBaseline:
    return TransformerBaseline(tiny_config(tie), seed=seed)


def container_layout(weights_path: Path) -> dict[str, Any]:
    """Parse the real safetensors header: entries, byte ranges, overhead."""
    raw = weights_path.read_bytes()
    (header_len,) = struct.unpack("<Q", raw[:8])
    header = json.loads(raw[8 : 8 + header_len].decode("utf-8"))
    tensors = {
        name: {
            "dtype": meta["dtype"],
            "shape": tuple(meta["shape"]),
            "range": (int(meta["data_offsets"][0]), int(meta["data_offsets"][1])),
            "payload_bytes": int(meta["data_offsets"][1]) - int(meta["data_offsets"][0]),
        }
        for name, meta in header.items()
        if name != "__metadata__"
    }
    return {
        "tensors": tensors,
        "entries": len(tensors),
        "payload_bytes": sum(t["payload_bytes"] for t in tensors.values()),
        "distinct_ranges": len({t["range"] for t in tensors.values()}),
        "header_bytes": 8 + header_len,
        "file_bytes": len(raw),
    }


@pytest.fixture
def tied_bundle(tmp_path: Path) -> tuple[Path, TransformerBaseline, ByteTokenizer]:
    model = tiny_model(tie=True)
    assert model.lm_head.weight is model.embed_tokens.weight, "fixture is not tied"
    tokenizer = ByteTokenizer()
    out = tmp_path / "bundle"
    export_model(model, tokenizer, out, "d07_tied")
    return out, model, tokenizer


def rewrite_as_legacy_v1(bundle: Path, *, diverge: bool = False, wrong_shape: bool = False) -> Path:
    """Author a v1-format bundle from a v2 one: both payloads, no alias map.

    This is how the pre-D07 exporter wrote tied weights. It is constructed here
    rather than checked in so the legacy path is exercised against a bundle
    whose values match the current fixture.
    """
    from safetensors.torch import load_file as safetensors_load
    from safetensors.torch import save_file as safetensors_save

    weights = bundle / "model.safetensors"
    state = safetensors_load(str(weights))
    payload = state["embed_tokens.weight"]
    if wrong_shape:
        state["lm_head.weight"] = payload[:-1].clone()
    elif diverge:
        state["lm_head.weight"] = payload.clone() + 1.0
    else:
        state["lm_head.weight"] = payload.clone()
    safetensors_save(state, str(weights))

    manifest_path = bundle / "export_manifest.json"
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    data["export_format_version"] = "1"
    data["storage_layout"] = "duplicated_legacy"
    data["alias_map"] = {}
    data["tied_mapping"] = {"tied_lm_head": ["embed_tokens.weight", "lm_head.weight"]}
    for entry in data["files"]:
        if entry["path"] == "model.safetensors":
            entry["sha256"] = hashlib.sha256(weights.read_bytes()).hexdigest()
            entry["size_bytes"] = weights.stat().st_size
    manifest_path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    return bundle


def reseal(bundle: Path) -> None:
    """Refresh recorded file hashes after deliberately editing a bundle."""
    manifest_path = bundle / "export_manifest.json"
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    for entry in data["files"]:
        target = bundle / entry["path"]
        entry["sha256"] = hashlib.sha256(target.read_bytes()).hexdigest()
        entry["size_bytes"] = target.stat().st_size
    manifest_path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


# ------------------------------------------------------- single-copy storage


def test_a_tied_tensor_is_serialized_exactly_once(tied_bundle: Any) -> None:
    """The headline D07 requirement, measured from the container itself."""
    out, model, _ = tied_bundle
    layout = container_layout(out / "model.safetensors")
    manifest = read_manifest(out)

    # The alias name carries no payload at all.
    assert "embed_tokens.weight" in layout["tensors"]
    assert "lm_head.weight" not in layout["tensors"]

    # Logical names vs unique deployed parameters vs serialized entries.
    counts = model.count_parameters()
    logical_names = len(model.state_dict())
    assert logical_names == 21
    assert counts.unique_deployed == 98_880
    assert counts.total_instantiated == 115_520
    assert layout["entries"] == logical_names - 1 == 20

    # Serialized payload equals the unique deployed parameters exactly.
    assert layout["payload_bytes"] == counts.unique_deployed * 4
    assert layout["distinct_ranges"] == layout["entries"]

    # Deployed count is unchanged by the storage repair.
    assert manifest.parameters_deployed == counts.unique_deployed
    assert manifest.storage_layout == "single_copy"
    assert manifest.serialized_tensor_entries == layout["entries"]
    assert manifest.serialized_payload_bytes == layout["payload_bytes"]

    # Container overhead is reported separately and never counted as payload.
    assert layout["file_bytes"] == layout["header_bytes"] + layout["payload_bytes"]


def test_equal_valued_independent_parameters_stay_independent(tmp_path: Path) -> None:
    """Deduplication must follow declared identity, never value equality."""
    model = tiny_model(tie=False)
    assert model.lm_head.weight is not model.embed_tokens.weight
    with torch.no_grad():
        model.lm_head.weight.copy_(model.embed_tokens.weight)
    assert torch.equal(model.lm_head.weight, model.embed_tokens.weight)

    out = tmp_path / "independent"
    manifest = export_model(model, ByteTokenizer(), out, "d07_independent")
    layout = container_layout(out / "model.safetensors")

    assert manifest.alias_map == {}
    assert resolve_tied_aliases(model) == ()
    assert "lm_head.weight" in layout["tensors"]
    assert "embed_tokens.weight" in layout["tensors"]
    assert (
        layout["tensors"]["lm_head.weight"]["range"]
        != layout["tensors"]["embed_tokens.weight"]["range"]
    )
    assert layout["entries"] == 21
    assert layout["payload_bytes"] == model.count_parameters().unique_deployed * 4

    loaded, _, _ = load_exported_model(out, device="cpu")
    assert loaded.lm_head.weight is not loaded.embed_tokens.weight
    # Still independently trainable after the round trip.
    with torch.no_grad():
        loaded.lm_head.weight.add_(1.0)
    assert not torch.equal(loaded.lm_head.weight, loaded.embed_tokens.weight)


def test_canonical_name_and_alias_map_are_deterministic(tmp_path: Path) -> None:
    first = export_model(tiny_model(), ByteTokenizer(), tmp_path / "a", "d07_det_a")
    second = export_model(tiny_model(), ByteTokenizer(), tmp_path / "b", "d07_det_b")
    assert first.alias_map == second.alias_map == {"lm_head.weight": "embed_tokens.weight"}
    assert first.alias_schema_version == second.alias_schema_version == "1"
    # Identical inputs produce byte-identical weights and an identical layout.
    assert first.model_hash == second.model_hash
    assert container_layout(tmp_path / "a" / "model.safetensors") == container_layout(
        tmp_path / "b" / "model.safetensors"
    )


# --------------------------------------------------------- reconstruction


def test_fresh_process_reload_restores_parameter_identity_and_parity(
    tied_bundle: Any, tmp_path: Path
) -> None:
    """Identity, logits, likelihood, greedy text and tokenizer in a new process."""
    out, model, tokenizer = tied_bundle
    model.eval()
    probe = tmp_path / "probe.py"
    probe.write_text(
        "import json, torch\n"
        "from xlm.export.loader import load_exported_model\n"
        f"m, tok, man = load_exported_model(r'{out}', device='cpu')\n"
        "m.eval()\n"
        "torch.manual_seed(20260920)\n"
        "ids = torch.randint(4, 260, (1, 8))\n"
        "out = {'ids': ids.tolist()}\n"
        "with torch.no_grad():\n"
        "    out['logits'] = m(ids).logits.tolist()\n"
        "out['tied'] = m.lm_head.weight is m.embed_tokens.weight\n"
        "out['fingerprint'] = tok.fingerprint\n"
        "out['deployed'] = int(m.count_parameters().unique_deployed)\n"
        "out['storage_layout'] = man.storage_layout\n"
        "from xlm.evaluation.likelihood import ConditionalLikelihoodScorer\n"
        "s = ConditionalLikelihoodScorer(model=m, tokenizer=tok, device='cpu')\n"
        "out['ll'] = s.score_continuation('Hello', ' world').log_likelihood\n"
        "from xlm.inference.generation import GenerationConfig, TextGenerator\n"
        "g = TextGenerator(model=m, tokenizer=tok, device='cpu')\n"
        "out['text'] = g.generate('Hello world', GenerationConfig(max_new_tokens=8"
        ")).generated_text\n"
        "print('RESULT' + json.dumps(out))\n",
        encoding="utf-8",
    )
    completed = subprocess.run(
        [sys.executable, str(probe)],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert completed.returncode == 0, completed.stderr[-3000:]
    line = next(x for x in completed.stdout.splitlines() if x.startswith("RESULT"))
    got = json.loads(line[len("RESULT") :])

    assert got["tied"] is True
    assert got["fingerprint"] == tokenizer.fingerprint
    assert got["deployed"] == int(model.count_parameters().unique_deployed)
    assert got["storage_layout"] == "single_copy"

    ids = torch.tensor(got["ids"])
    with torch.no_grad():
        expected_logits = model(ids).logits
    assert (torch.tensor(got["logits"]) - expected_logits).abs().max().item() == EXACT

    from xlm.evaluation.likelihood import ConditionalLikelihoodScorer
    from xlm.inference.generation import GenerationConfig, TextGenerator

    scorer = ConditionalLikelihoodScorer(model=model, tokenizer=tokenizer, device="cpu")
    assert abs(got["ll"] - scorer.score_continuation("Hello", " world").log_likelihood) == EXACT
    generator = TextGenerator(model=model, tokenizer=tokenizer, device="cpu")
    assert (
        got["text"]
        == generator.generate("Hello world", GenerationConfig(max_new_tokens=8)).generated_text
    )


def test_gradients_accumulate_into_the_single_tied_parameter(tied_bundle: Any) -> None:
    """A restored tie must behave like one parameter in the backward pass."""
    out, _, _ = tied_bundle
    loaded, _, _ = load_exported_model(out, device="cpu")
    loaded.train()
    assert loaded.lm_head.weight is loaded.embed_tokens.weight

    ids = torch.randint(4, 260, (2, 6))
    logits = loaded(ids).logits
    logits.sum().backward()

    shared = loaded.embed_tokens.weight
    assert shared.grad is not None
    # One object, so one gradient buffer: the head's gradient IS the embedding's.
    assert loaded.lm_head.weight.grad is shared.grad
    assert torch.isfinite(shared.grad).all()
    # The tie contributes through both uses, so the gradient is not degenerate.
    assert shared.grad.abs().sum().item() > 0.0


# ----------------------------------------------------------------- refusals


def test_corrupted_payload_is_rejected_before_deserialization(tied_bundle: Any) -> None:
    out, _, _ = tied_bundle
    weights = out / "model.safetensors"
    raw = bytearray(weights.read_bytes())
    raw[-1] ^= 0xFF
    weights.write_bytes(bytes(raw))
    with pytest.raises(ExportLoadError, match="hash mismatch"):
        load_exported_model(out, device="cpu")


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        ({"lm_head.weight": "no_such_tensor"}, "carries no stored payload"),
        ({"lm_head.weight": "lm_head.weight"}, "targets itself"),
        ({"embed_tokens.weight": "lm_head.weight"}, "carries no stored payload"),
    ],
)
def test_invalid_alias_metadata_is_rejected(
    tied_bundle: Any, mutate: dict[str, str], message: str
) -> None:
    out, _, _ = tied_bundle
    manifest_path = out / "export_manifest.json"
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    data["alias_map"] = mutate
    manifest_path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    with pytest.raises(ExportLoadError, match=message):
        load_exported_model(out, device="cpu")


def test_alias_chains_and_cycles_are_rejected() -> None:
    with pytest.raises(AliasError, match="alias chains are not supported"):
        validate_alias_map({"a": "b", "b": "c"}, {"b", "c"})
    with pytest.raises(AliasError, match="targets itself"):
        validate_alias_map({"a": "a"}, {"a"})
    with pytest.raises(AliasError, match="carries no stored payload"):
        validate_alias_map({"a": "missing"}, {"b"})


def test_missing_and_extra_tensors_are_rejected(tied_bundle: Any) -> None:
    from safetensors.torch import load_file as safetensors_load
    from safetensors.torch import save_file as safetensors_save

    out, _, _ = tied_bundle
    weights = out / "model.safetensors"

    state = safetensors_load(str(weights))
    dropped = state.pop("norm.weight")
    safetensors_save(state, str(weights))
    reseal(out)
    with pytest.raises(ExportLoadError, match="do not fit the declared architecture"):
        load_exported_model(out, device="cpu")

    state["norm.weight"] = dropped
    state["an_unexpected_tensor"] = torch.zeros(3)
    safetensors_save(state, str(weights))
    reseal(out)
    with pytest.raises(ExportLoadError, match="do not fit the declared architecture"):
        load_exported_model(out, device="cpu")


def test_unsupported_versions_are_rejected(tied_bundle: Any) -> None:
    out, _, _ = tied_bundle
    manifest_path = out / "export_manifest.json"
    data = json.loads(manifest_path.read_text(encoding="utf-8"))

    data["export_format_version"] = "99"
    manifest_path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(IncompatibleExportError, match="Re-export with the matching"):
        load_exported_model(out, device="cpu")

    data["export_format_version"] = "2"
    data["alias_schema_version"] = "99"
    manifest_path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(IncompatibleExportError, match="alias schema"):
        load_exported_model(out, device="cpu")

    data["alias_schema_version"] = "1"
    data["storage_layout"] = "compressed_somehow"
    manifest_path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(IncompatibleExportError, match="unknown storage layout"):
        load_exported_model(out, device="cpu")


def test_a_v1_bundle_cannot_claim_single_copy(tied_bundle: Any) -> None:
    out, _, _ = tied_bundle
    manifest_path = out / "export_manifest.json"
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    data["export_format_version"] = "1"  # storage_layout stays "single_copy"
    manifest_path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(IncompatibleExportError, match="stores tied tensors twice"):
        load_exported_model(out, device="cpu")


# --------------------------------------------------------- legacy v1 loading


def test_legacy_v1_bundle_loads_and_is_labelled_duplicated(tied_bundle: Any) -> None:
    """Supported legacy loading, reported honestly rather than relabelled."""
    out, model, _ = tied_bundle
    rewrite_as_legacy_v1(out)

    manifest = read_manifest(out)
    assert manifest.export_format_version == "1"
    assert manifest.storage_layout == "duplicated_legacy"
    assert manifest.alias_map == {}

    layout = container_layout(out / "model.safetensors")
    assert layout["entries"] == 21  # both copies really are present
    assert layout["payload_bytes"] > model.count_parameters().unique_deployed * 4

    loaded, _, loaded_manifest = load_exported_model(out, device="cpu")
    assert loaded.lm_head.weight is loaded.embed_tokens.weight
    assert torch.equal(loaded.embed_tokens.weight, model.embed_tokens.weight)
    # The original manifest is not rewritten and never becomes "single_copy".
    assert loaded_manifest.storage_layout == "duplicated_legacy"
    on_disk = json.loads((out / "export_manifest.json").read_text(encoding="utf-8"))
    assert on_disk["export_format_version"] == "1"
    assert on_disk["storage_layout"] == "duplicated_legacy"


def test_conflicting_legacy_copies_are_refused(tied_bundle: Any) -> None:
    """Never resolved by whichever payload happened to load last."""
    out, _, _ = tied_bundle
    rewrite_as_legacy_v1(out, diverge=True)
    with pytest.raises(ExportLoadError, match="tied weights diverged"):
        load_exported_model(out, device="cpu")


def test_legacy_shape_mismatch_is_refused_before_bit_comparison(tied_bundle: Any) -> None:
    """Shape and dtype are checked; byte equality alone is not validation."""
    out, _, _ = tied_bundle
    rewrite_as_legacy_v1(out, wrong_shape=True)
    with pytest.raises(ExportLoadError, match="has shape"):
        load_exported_model(out, device="cpu")


# ---------------------------------------------------------- alias detection


def test_partial_and_transposed_views_are_refused(tmp_path: Path) -> None:
    """Shared storage that is not a complete same-tensor alias must fail loudly."""
    model = tiny_model(tie=False)
    whole = model.embed_tokens.weight

    with pytest.raises(UnsupportedAliasError, match="not a complete alias"):
        from xlm.models.aliases import assert_complete_alias

        assert_complete_alias("view", whole[:10], "whole", whole)

    with pytest.raises(UnsupportedAliasError):
        from xlm.models.aliases import assert_complete_alias

        assert_complete_alias("t", whole.t(), "whole", whole)


def test_undeclared_shared_storage_is_refused_by_the_writer(tmp_path: Path) -> None:
    """A view smuggled into the state dict is refused, never cloned."""
    from xlm.export.writer import build_single_copy_payload

    base = torch.zeros(4, 4)
    state = {"a.weight": base, "b.weight": base.view(4, 4)}
    with pytest.raises(ExportError, match="share storage but are not a declared tie"):
        build_single_copy_payload(state, ())


def test_alias_detection_rejects_a_declared_but_absent_alias() -> None:
    from xlm.export.writer import build_single_copy_payload

    state = {"a.weight": torch.zeros(2, 2)}
    with pytest.raises(ExportError, match="absent from the model state"):
        build_single_copy_payload(state, (TensorAlias(alias="b.weight", target="a.weight"),))


# ----------------------------------------------------- D01 artifact identity


def _store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ArtifactStore:
    monkeypatch.setenv("XLM_HOME", str(tmp_path / "home"))
    return ArtifactStore(ArtifactPaths.from_env())


def test_published_bundle_reuses_identical_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path, monkeypatch)
    model, tokenizer = tiny_model(), ByteTokenizer()
    first = export_model(
        model, tokenizer, tmp_path / "one", "d07_pub", publish=True, artifact_store=store
    )
    # A second identical export of the same model must verify and reuse, not
    # collide: v2 bundles are byte-deterministic.
    second = export_model(
        model, tokenizer, tmp_path / "two", "d07_pub", publish=True, artifact_store=store
    )
    assert first.model_hash == second.model_hash
    assert export_representation_identity(first) == export_representation_identity(second)
    published = store.paths.root / "exports" / "d07_pub"
    assert published.is_dir()
    store.verify_artifact(published)


def test_conflicting_content_under_one_export_id_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path, monkeypatch)
    tokenizer = ByteTokenizer()
    export_model(
        tiny_model(seed=1),
        tokenizer,
        tmp_path / "one",
        "d07_conflict",
        publish=True,
        artifact_store=store,
    )
    with pytest.raises(ArtifactConflictError):
        export_model(
            tiny_model(seed=2),
            tokenizer,
            tmp_path / "two",
            "d07_conflict",
            publish=True,
            artifact_store=store,
        )
    # The completed original is intact after the refused publication.
    store.verify_artifact(store.paths.root / "exports" / "d07_conflict")


def test_layout_change_is_a_different_representation_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A v1 layout must not pass as the same representation as a v2 one."""
    store = _store(tmp_path, monkeypatch)
    model, tokenizer = tiny_model(), ByteTokenizer()
    v2 = export_model(
        model, tokenizer, tmp_path / "v2", "d07_layout", publish=True, artifact_store=store
    )
    legacy_dir = tmp_path / "legacy"
    export_model(model, tokenizer, legacy_dir, "d07_layout")
    rewrite_as_legacy_v1(legacy_dir)
    legacy = read_manifest(legacy_dir)

    assert export_representation_identity(legacy) != export_representation_identity(v2)
    # Same trained weights, different serialized representation.
    assert legacy.parameters_deployed == v2.parameters_deployed
    with pytest.raises(ArtifactConflictError):
        publish_export_bundle(legacy_dir, legacy, artifact_store=store)
    store.verify_artifact(store.paths.root / "exports" / "d07_layout")


def test_a_copied_bundle_loads_without_the_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Publication must not make bundles depend on the store to load."""
    import shutil

    store = _store(tmp_path, monkeypatch)
    model, tokenizer = tiny_model(), ByteTokenizer()
    out = tmp_path / "portable"
    export_model(model, tokenizer, out, "d07_portable", publish=True, artifact_store=store)

    elsewhere = tmp_path / "elsewhere" / "copy"
    elsewhere.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(out, elsewhere)
    shutil.rmtree(store.paths.root)
    monkeypatch.delenv("XLM_HOME", raising=False)

    loaded, loaded_tok, manifest = load_exported_model(elsewhere, device="cpu")
    assert loaded.lm_head.weight is loaded.embed_tokens.weight
    assert loaded_tok.fingerprint == tokenizer.fingerprint
    assert manifest.storage_layout == "single_copy"


# ------------------------------------------------------------- public CLI


@pytest.mark.slow
def test_public_cli_export_and_load_round_trip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The installed `xlm export` command produces a single-copy bundle."""
    import os

    from xlm.models.serialization import save_model_to_directory

    model = tiny_model()
    checkpoint = tmp_path / "ckpt"
    save_model_to_directory(model, checkpoint)
    ByteTokenizer().save(checkpoint / "tokenizer")

    out = tmp_path / "cli_bundle"
    env = dict(os.environ)
    env["XLM_HOME"] = str(tmp_path / "home")
    env.pop("PYTHONPATH", None)
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "xlm.cli.main",
            "export",
            str(checkpoint),
            "--output-dir",
            str(out),
            "--export-id",
            "d07_cli",
            "--publish",
        ],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        env=env,
    )
    assert completed.returncode == 0, completed.stderr[-3000:]
    assert "single_copy" in completed.stdout
    assert "1 stored once" in completed.stdout
    assert "Published:" in completed.stdout

    layout = container_layout(out / "model.safetensors")
    assert "lm_head.weight" not in layout["tensors"]
    assert layout["payload_bytes"] == model.count_parameters().unique_deployed * 4

    loaded, _, manifest = load_exported_model(out, device="cpu")
    assert loaded.lm_head.weight is loaded.embed_tokens.weight
    assert manifest.storage_layout == "single_copy"
    published = tmp_path / "home" / "exports" / "d07_cli"
    assert published.is_dir(), sorted((tmp_path / "home").rglob("d07_cli"))
    # The published copy is verifiable on its own terms.
    ArtifactStore(ArtifactPaths(root=tmp_path / "home")).verify_artifact(published)
