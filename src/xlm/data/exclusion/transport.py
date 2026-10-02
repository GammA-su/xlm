"""Explicit content-free C05 proof transport for existing downstream commands."""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from xlm.data.exclusion.artifacts import ExecutionPlan, Sha
from xlm.data.exclusion.control import key_from_env, trust_from_file
from xlm.data.exclusion.gates import PRODUCTION_COMPONENTS, MembershipGate
from xlm.data.exclusion.inputs import read_metadata
from xlm.data.exclusion.policy import C05Error, FrozenModel


class ProofSpec(FrozenModel):
    plan: str
    manifest: str
    completion: str
    trust: str
    scratch: str
    plan_digest: Sha
    completion_digest: Sha
    signer: str | None = None
    signer_key_env: str | None = None


def protected_guard(plan: ExecutionPlan, consumes: Iterable[Path | str]) -> None:
    """Defense in depth for detached_volume_v1 plans; C05 membership stays primary.

    Downstream (tokenizer fitting/counting, tokenization, mixture, training) refuses
    while the plan's protected benchmark root is accessible at its bound path, and
    refuses any input/output path inside that root or the C05 scratch, which holds
    detailed matches. Only the bound path is probed: the same volume mounted at
    another drive letter or folder is not detected (operational isolation, not
    adversarial security).
    """
    if plan.isolation is None:
        return
    from xlm.data.exclusion.isolation import overlaps

    root = plan.isolation.protected_root.path
    if Path(root).exists():
        raise C05Error(
            "protected benchmark volume is mounted; detach it before tokenizer or training work"
        )
    for item in consumes:
        for blocked, name in (
            (root, "protected benchmark root"),
            (plan.scratch_root, "C05 protected scratch"),
        ):
            if overlaps(item, blocked):
                raise C05Error(f"{name} path cannot be tokenizer/training material")


def guard_proof(path: Path | None, consumes: Iterable[Path | str] = ()) -> None:
    """Cheap pre-read guard: the proof's plan only, before any corpus byte is read."""
    if path is None:
        return
    spec = ProofSpec.model_validate(read_metadata(path, digested=False))
    plan = ExecutionPlan.model_validate(read_metadata(Path(spec.plan), digested=False))
    if plan.identity() != spec.plan_digest:
        raise C05Error("C05 proof plan changed")
    protected_guard(
        plan,
        (path, spec.plan, spec.manifest, spec.completion, spec.trust, spec.scratch, *consumes),
    )


@contextmanager
def open_gate(
    path: Path | None,
    *,
    allow_authored: bool = False,
    consumes: Iterable[Path | str] = (),
) -> Iterator[MembershipGate | None]:
    """Open the verified kept-membership lookup named by a proof specification.

    Protected plans only, unless the caller explicitly runs an authored chain whose
    every artifact then carries ``mode="authored"`` and fails protected consumers.
    ``consumes`` names the caller's corpus/tokenizer/shard paths for the
    detached-volume guard.
    """
    if path is None:
        yield None
        return
    spec = ProofSpec.model_validate(read_metadata(path, digested=False))
    plan = ExecutionPlan.model_validate(read_metadata(Path(spec.plan), digested=False))
    if plan.identity() != spec.plan_digest:
        raise C05Error("C05 proof plan changed")
    protected_guard(
        plan,
        (path, spec.plan, spec.manifest, spec.completion, spec.trust, spec.scratch, *consumes),
    )
    if plan.mode == "authored" and not allow_authored:
        raise C05Error("development evidence cannot satisfy protected C05")
    trust = trust_from_file(Path(spec.trust))
    signer = None
    if spec.signer is not None or spec.signer_key_env is not None:
        if spec.signer is None or spec.signer_key_env is None:
            raise C05Error("incomplete downstream signing identity")
        signer = (spec.signer, key_from_env(spec.signer_key_env))
    scratch_root = Path(spec.scratch).resolve()
    for immutable in (Path(plan.data_root).resolve(), Path(spec.completion).resolve()):
        if scratch_root.is_relative_to(immutable):
            raise C05Error("downstream scratch overlaps immutable input")
    lookup = scratch_root / ("membership-" + uuid.uuid4().hex + ".sqlite")
    gate = MembershipGate(
        Path(spec.completion),
        plan,
        read_metadata(Path(spec.manifest)),
        trust,
        lookup,
        authored=plan.mode == "authored",
        signer=signer,
    )
    try:
        if gate.receipt_digest != spec.completion_digest:
            raise C05Error("C05 completion changed")
        yield gate
    finally:
        gate.close()
        # This context owns only this newly created lookup, never another job's files.
        lookup.unlink(missing_ok=True)


def verify_training_shards(
    data: dict[str, Any], shards: Mapping[str, Path], *, production: bool = False
) -> dict[str, str] | None:
    required = production or any(s in PRODUCTION_COMPONENTS for s in shards)
    reference = data.get("c05_proof")
    if reference is None and not required:
        return None
    if not isinstance(reference, str):
        raise C05Error("production training requires explicit C05 proof transport")
    with open_gate(Path(reference), consumes=shards.values()) as gate:
        if gate is None:
            raise C05Error("C05 proof absent")
        gate.db.execute("DELETE FROM seen")
        for directory in shards.values():
            gate.verify_token_shard(directory, reset_seen=False)
        binding = {"plan_digest": gate.plan_digest, "completion_digest": gate.receipt_digest}
        if data.get("c05_binding", binding) != binding:
            raise C05Error("training C05 binding changed")
        data["c05_binding"] = binding
        return binding
