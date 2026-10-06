"""Final Mix-01 freeze over the signed selected-training-membership.

Each logical component gets exactly one token shard containing exactly its selected
records; every offset re-proves C05 content, exact count and selected valid targets.
The frozen recipe uses the unchanged quota shares, and the exposure plan must consume
each shard exactly once (no repetition, no unconsumed remainder). The freeze also
emits the bounded training-data block; nothing here trains a model.
"""

from __future__ import annotations

from collections.abc import Mapping
from fractions import Fraction
from pathlib import Path
from typing import Any

from xlm.data.acquisition.source_run import write_once
from xlm.data.evidence_v2 import canonical
from xlm.data.exclusion.artifacts import signed, verify_signed
from xlm.data.exclusion.gates import MembershipGate
from xlm.data.exclusion.inputs import read_metadata
from xlm.data.exclusion.policy import C05Error
from xlm.data.exclusion.selection import (
    COUNT_RULE,
    SelectionGate,
    binding_of,
    check_binding,
    iter_plan_documents,
    tokenizer_identity,
)

FINAL_MIXTURE_ID = "Mix-01-final"
# Authored chains rehearse the identical code but can never be a Mix-01 plan.
REHEARSAL_MIXTURE_ID = "authored-rehearsal-final"
FREEZE_KIND = "c05_mix01_freeze_v1"


def tokenize_selection(
    gate: MembershipGate,
    selection_dir: Path,
    tokenizer_dir: Path,
    output_root: Path,
    *,
    batch_size: int = 1,
) -> dict[str, dict[str, Any]]:
    """Write one shard per component from the C05 inputs, filtered to the selection."""
    from xlm.data.tokens import TokenShardWriter

    selection = SelectionGate(gate, selection_dir)
    from xlm.data.exclusion.transport import protected_guard

    protected_guard(gate.plan, (selection_dir, tokenizer_dir, output_root))
    tokenizer, identity = tokenizer_identity(tokenizer_dir, gate)
    if identity != selection.body["tokenizer"]:
        raise C05Error("tokenization tokenizer differs from the exact-count tokenizer")
    manifests: dict[str, dict[str, Any]] = {}
    for component in sorted(selection.body["components"]):
        documents = (
            doc
            for _, doc in iter_plan_documents(gate, components={component})
            if selection.selected(doc.doc_id)
        )
        manifest = TokenShardWriter(
            output_root / component,
            component,
            component,
            tokenizer,
            pool_hash=selection.digest,
            max_output_bytes=gate.plan.resources.output_bytes,
            batch_size=batch_size,
            c05_gate=gate,
            selection=selection,
        ).write_documents(documents, COUNT_RULE["add_special_tokens"])
        manifests[component] = manifest.to_dict()
    return manifests


def final_recipe(mode: str, components: Mapping[str, Mapping[str, int]], total: int) -> Any:
    """Unchanged quota shares; a share that cannot apportion exactly refuses."""
    from xlm.data.sampling import MixtureRecipe

    entries = []
    for component in sorted(components):
        quota = components[component]["quota"]
        weight = quota / total
        if Fraction(str(weight)) * total != quota:
            raise C05Error("frozen quota share is not exactly representable; no renormalization")
        entries.append({"source_id": component, "weight": weight, "shard_id": component})
    return MixtureRecipe.model_validate(
        {
            "mixture_id": FINAL_MIXTURE_ID if mode == "protected" else REHEARSAL_MIXTURE_ID,
            "components": entries,
        }
    )


def exposure_plan(
    recipe: Any,
    gate: MembershipGate,
    shards: Mapping[str, Mapping[str, Any]],
    components: Mapping[str, Mapping[str, int]],
    total: int,
    block_size: int,
) -> dict[str, Any]:
    from xlm.data.sampling import SourceAvailability, compile_exposure_plan, validate_mixture
    from xlm.data.tokens import TokenShardReader

    availability = {}
    for component, record in shards.items():
        reader = TokenShardReader(Path(record["path"]))
        counters = reader.counters
        availability[component] = SourceAvailability(
            source_id=component,
            shard_id=reader.manifest.shard_id,
            valid_targets=int(counters["valid_targets"]),
            content_tokens=int(counters["content_tokens"]),
            eos_tokens=int(counters["eos_tokens"]),
            canonical_bytes=int(counters["canonical_bytes"]),
            num_documents=reader.manifest.num_documents,
            token_dtype=reader.manifest.token_dtype,
        )
    plan = compile_exposure_plan(
        recipe,
        validate_mixture(recipe, availability),
        total,
        block_size,
        c05_gate=gate,
        c05_shards={c: Path(r["path"]) for c, r in shards.items()},
        c05_rehearsal=gate.mode == "authored",
    )
    for component, projection in plan.projections.items():
        if (
            projection.planned_targets != components[component]["quota"]
            or projection.unique_targets_available != projection.planned_targets
            or projection.repeated_targets
        ):
            raise C05Error("exposure plan does not consume each selected shard exactly once")
    return plan.to_dict()


def freeze(
    gate: MembershipGate,
    proof: Path,
    selection_dir: Path,
    shards_root: Path,
    tokenizer_dir: Path,
    output: Path,
    issuer: str,
    key: bytes,
    *,
    block_size: int = 8192,
    training_input_policy: str | None = None,
) -> dict[str, Any]:
    from xlm.data.input_policy import (
        TrainingInputPolicy,
        admit_sources,
        check_frozen_policy,
        policy_fields,
    )

    fields = policy_fields(training_input_policy)
    if fields:
        admit_sources(
            {p.name: p for p in shards_root.iterdir() if not p.name.startswith(".")},
            TrainingInputPolicy(),
        )
    selection = SelectionGate(gate, selection_dir)
    tokenizer, identity = tokenizer_identity(tokenizer_dir, gate)
    if fields:
        import hashlib

        from xlm.data.input_policy import validate_headers
        from xlm.data.tokens import token_byte_lengths

        validate_headers(
            {p.name: p for p in shards_root.iterdir() if not p.name.startswith(".")},
            TrainingInputPolicy(),
            hashlib.sha256(token_byte_lengths(tokenizer)).hexdigest(),
        )
    if identity != selection.body["tokenizer"]:
        raise C05Error("freeze tokenizer differs from the exact-count tokenizer")
    components: dict[str, dict[str, int]] = selection.body["components"]
    present = sorted(p.name for p in shards_root.iterdir() if not p.name.startswith("."))
    if present != sorted(components):
        raise C05Error("freeze shard set differs from the selected components")
    gate.db.execute("DELETE FROM seen")
    shards: dict[str, dict[str, Any]] = {}
    for component in sorted(components):
        directory = (shards_root / component).resolve()
        shards[component] = {
            **selection.verify_component_shard(directory, component, reset_seen=False),
            "path": str(directory),
        }
    total = int(selection.body["valid_target_quota"])
    recipe = final_recipe(gate.mode, components, total)
    exposure = exposure_plan(recipe, gate, shards, components, total, block_size)
    envelope = signed(
        {
            **fields,
            "kind": FREEZE_KIND,
            **binding_of(gate),
            "selection_path": str(selection_dir.resolve()),
            "selection_digest": selection.digest,
            "selected_membership_sha256": selection.body["selected_membership_sha256"],
            "counts_digest": selection.body["counts_digest"],
            "tokenizer": identity,
            "quota_sha256": selection.body["quota_sha256"],
            "requirements_digest": selection.body["requirements_digest"],
            "recipe": recipe.model_dump(mode="json"),
            "recipe_identity": recipe.identity(),
            "exposure_plan": exposure,
            "exposure_plan_digest": canonical.digest(exposure),
            "block_size": block_size,
            "shards": shards,
            "valid_targets": total,
        },
        issuer,
        key,
    )
    check_frozen_policy(envelope["payload"])
    output.mkdir(parents=True, exist_ok=True)
    write_once(output / "freeze.json", envelope)
    write_once(
        output / "training-data.json",
        {
            **fields,
            "mixture": recipe.model_dump(mode="json"),
            "sources": {c: r["path"] for c, r in shards.items()},
            "exposure_plan": exposure,
            "c05_proof": str(proof.resolve()),
            "c05_freeze": str((output / "freeze.json").resolve()),
        },
    )
    return envelope


def verify_freeze(path: Path, gate: MembershipGate) -> dict[str, Any]:
    """Re-verify signature, selection and every frozen shard's actual bytes."""
    from xlm.data.sampling import MixtureRecipe

    envelope = read_metadata(path, digested=False)
    body = verify_signed(envelope, gate.trusted)
    check_binding(body, gate, FREEZE_KIND)
    from xlm.data.input_policy import check_frozen_policy

    check_frozen_policy(body)
    selection = SelectionGate(gate, Path(body["selection_path"]))
    if (
        selection.digest != body["selection_digest"]
        or selection.body["tokenizer"] != body["tokenizer"]
        or selection.body["selected_membership_sha256"] != body["selected_membership_sha256"]
        or selection.body["counts_digest"] != body["counts_digest"]
    ):
        raise C05Error("freeze selection changed")
    components = selection.body["components"]
    if set(body["shards"]) != set(components):
        raise C05Error("freeze shard set differs from the selection")
    gate.db.execute("DELETE FROM seen")
    for component in sorted(components):
        record = body["shards"][component]
        actual = selection.verify_component_shard(Path(record["path"]), component, reset_seen=False)
        if {**actual, "path": record["path"]} != record:
            raise C05Error("frozen shard bytes changed")
    recipe = MixtureRecipe.model_validate(body["recipe"])
    total = int(selection.body["valid_target_quota"])
    if recipe.identity() != body["recipe_identity"] or recipe.model_dump(
        mode="json"
    ) != final_recipe(gate.mode, components, total).model_dump(mode="json"):
        raise C05Error("freeze recipe differs from frozen quota shares")
    exposure = exposure_plan(recipe, gate, body["shards"], components, total, body["block_size"])
    if exposure != body["exposure_plan"]:
        raise C05Error("freeze exposure plan differs from the frozen shards")
    return envelope


def verify_training_freeze(
    data: dict[str, Any], sources: Mapping[str, Path], *, production: bool
) -> dict[str, Any]:
    """Training input check: declared mixture/sources/exposure equal the signed freeze."""
    from xlm.data.exclusion.transport import open_gate
    from xlm.data.sampling import MixtureRecipe

    proof, frozen = data.get("c05_proof"), data.get("c05_freeze")
    if not isinstance(proof, str) or not isinstance(frozen, str):
        raise C05Error("final training requires explicit C05 proof and freeze paths")
    from xlm.data.input_policy import check_frozen_policy, policy_from_binding

    policy = policy_from_binding(data.get("training_input_policy"))
    if policy is not None:
        from xlm.data.exclusion.freezefast import verify_freeze_fast

        envelope = verify_freeze_fast(Path(frozen), Path(proof), allow_authored=not production)
    else:
        with open_gate(
            Path(proof), allow_authored=not production, consumes=(frozen, *sources.values())
        ) as gate:
            if gate is None:
                raise C05Error("C05 proof absent")
            envelope = verify_freeze(Path(frozen), gate)
    body = envelope["payload"]
    check_frozen_policy(body)
    if data.get("training_input_policy") != body.get("training_input_policy"):
        raise C05Error("training input policy differs from signed freeze")
    if production and body["mode"] != "protected":
        raise C05Error("authored rehearsal cannot satisfy Mix-01 training")
    if MixtureRecipe.model_validate(data["mixture"]).identity() != body["recipe_identity"]:
        raise C05Error("training mixture differs from the signed freeze")
    if data.get("exposure_plan") != body["exposure_plan"]:
        raise C05Error("training exposure plan differs from the signed freeze")
    actual = {name: str(Path(path).resolve()) for name, path in sources.items()}
    if actual != {c: r["path"] for c, r in body["shards"].items()}:
        raise C05Error("training shards differ from the signed freeze")
    binding = {
        "mode": body["mode"],
        "plan_digest": body["plan_digest"],
        "completion_digest": body["completion_digest"],
        "selection_digest": body["selection_digest"],
        "selected_membership_sha256": body["selected_membership_sha256"],
        "freeze_digest": envelope["digest"],
    }
    if data.get("c05_binding", binding) != binding:
        raise C05Error("training C05 binding changed")
    data["c05_binding"] = binding
    return binding
