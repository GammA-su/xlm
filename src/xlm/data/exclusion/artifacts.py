"""Strict content-free C05 artifacts, trust verification and execution plans."""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import Field

from xlm.data.evidence_v2 import canonical
from xlm.data.exclusion.capacity import StorageGeometry, admit_plan
from xlm.data.exclusion.inputs import read_metadata
from xlm.data.exclusion.isolation import (
    AnyIsolation,
    DetachedVolumeIsolation,
    Isolation,
    ProtectedRoot,
    VolumeInspector,
    verify_protected_root,
    verify_separation,
)
from xlm.data.exclusion.policy import (
    C05Error,
    FrozenModel,
    ProductionPolicy,
    Resources,
    require_engine_acceptance,
)

Sha = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Revision = Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]


class PlanIsolation(FrozenModel):
    """Detached-volume binding of a plan: protected root and its immutable index.

    Absent (``None``) for historical ``separate_principal_v1``/authored plans, whose
    plan digests are therefore unchanged.
    """

    mechanism: Literal["detached_volume_v1"] = "detached_volume_v1"
    protected_root: ProtectedRoot
    index_relpath: str = Field(min_length=1)

    def index_path(self) -> Path:
        root = Path(self.protected_root.path)
        relative = Path(self.index_relpath)
        if relative.is_absolute() or not (root / relative).resolve().is_relative_to(root.resolve()):
            raise C05Error("protected index path escapes the protected root")
        return root / relative


class MaterialFile(FrozenModel):
    task: Literal["arc_easy", "blimp", "hellaswag", "piqa"]
    repository: str
    revision: Revision
    config: str = Field(min_length=1)
    split: str = Field(min_length=1)
    path: str = Field(min_length=1)
    sha256: Sha
    bytes: int = Field(gt=0)
    items: int = Field(gt=0)
    format: Literal["jsonl", "parquet"] = "jsonl"


class BenchmarkReceipt(FrozenModel):
    kind: Literal["c05_benchmark_preparation_v2"] = "c05_benchmark_preparation_v2"
    files: tuple[MaterialFile, ...]
    publisher_inventory_sha256: Sha
    all_published_configs_splits_reviewed: Literal[True]
    renderer: Literal["task-render-v3"] = "task-render-v3"
    normalization: Literal["match-view-v1"] = "match-view-v1"
    policy_digest: Sha
    fuzzy_policy_digest: Sha = Field(default_factory=lambda: ProductionPolicy().review.identity())
    automatic_disposition: Literal["AUTOMATIC_EXACT_EXCLUSION"] = "AUTOMATIC_EXACT_EXCLUSION"
    index_sha256: Sha
    index_bytes: int = Field(gt=0)
    items: int = Field(gt=0)
    duplicate_items: int = Field(ge=0)
    variants: int = Field(gt=0)
    patterns: int = Field(gt=0)
    items_without_patterns: int = Field(ge=0)
    index_version: Literal["protected-pattern-jsonl-v2"] = "protected-pattern-jsonl-v2"
    code_commit: Revision
    code_identity: Sha
    dependency_sha256: Sha
    lm_eval: Literal["0.4.13"] = "0.4.13"
    # Versioned: no ``mechanism`` = separate_principal_v1; else detached_volume_v1.
    isolation: AnyIsolation
    issuer: str = Field(min_length=1)


class InputFile(FrozenModel):
    path: str
    source_key: str
    source_id: str
    source_revision: str
    component: str
    view: str
    source_file: str
    documents_sha256: Sha
    file_bytes: int = Field(ge=0)
    canonical_bytes: int = Field(ge=0)
    documents: int = Field(ge=0)
    upstream_component: str | None = None


class ExecutionPlan(FrozenModel):
    kind: Literal["c05_execution_plan_v2"] = "c05_execution_plan_v2"
    sequence: int = Field(ge=1)
    mode: Literal["protected", "authored"]
    input_manifest_digest: Sha
    source_seals: dict[str, Sha]
    files: tuple[InputFile, ...]
    benchmark_receipt_digest: Sha
    index_sha256: Sha
    policy: ProductionPolicy
    resources: Resources
    storage: StorageGeometry
    data_root: str
    scratch_root: str
    output_root: str
    code_commit: Revision
    code_identity: Sha
    dependency_sha256: Sha
    output_contract: Literal["c05_membership_v2"] = "c05_membership_v2"
    review_decisions: dict[str, Sha] = Field(default_factory=dict)
    authorization_contract: Literal["signed-plan-digest-v2"] = "signed-plan-digest-v2"
    isolation: PlanIsolation | None = None

    def identity(self) -> str:
        self.policy.identity()
        # Deterministic worst-case storage must fit the reviewed ceilings.
        admit_plan(self.resources, self.storage)
        roots = [Path(x).resolve() for x in (self.data_root, self.scratch_root, self.output_root)]
        for i, root in enumerate(roots):
            for other in roots[i + 1 :]:
                if root.is_relative_to(other) or other.is_relative_to(root):
                    raise C05Error("C05 roots overlap")
        if not self.files or len(self.files) > self.resources.files:
            raise C05Error("input file ceiling")
        if sum(f.documents for f in self.files) > self.resources.records:
            raise C05Error("input record ceiling")
        if sum(f.file_bytes for f in self.files) > self.resources.bytes_read:
            raise C05Error("input byte ceiling")
        if len({f.path for f in self.files}) != len(self.files):
            raise C05Error("duplicate input path")
        for f in self.files:
            path = Path(f.path)
            if path.is_absolute() or not (roots[0] / path).resolve().is_relative_to(roots[0]):
                raise C05Error("input path escapes root")
        body = self.model_dump(mode="json")
        if self.isolation is None:
            del body["isolation"]  # Historical plan digests stay unchanged.
        else:
            self.isolation.index_path()
            protected = self.isolation.protected_root.path
            for role, root in zip(("data root", "scratch", "C05 output"), roots, strict=True):
                if root.is_relative_to(Path(protected).resolve()) or Path(
                    protected
                ).resolve().is_relative_to(root):
                    raise C05Error(f"protected benchmark root overlaps {role}")
        return canonical.digest(body)


def signed(body: Mapping[str, Any], issuer: str, key: bytes) -> dict[str, Any]:
    if not key or not issuer:
        raise C05Error("signing identity missing")
    payload = dict(body)
    payload["issuer"] = issuer
    identity = canonical.digest(payload)
    return {
        "payload": payload,
        "digest": identity,
        "signature": hmac.new(key, identity.encode(), hashlib.sha256).hexdigest(),
    }


def verify_signed(envelope: Mapping[str, Any], trusted: Mapping[str, bytes]) -> dict[str, Any]:
    if set(envelope) != {"payload", "digest", "signature"}:
        raise C05Error("signed artifact schema")
    body = envelope["payload"]
    if not isinstance(body, dict):
        raise C05Error("signed artifact payload")
    key = trusted.get(str(body.get("issuer", "")))
    identity = canonical.digest(body)
    if key is None or envelope["digest"] != identity or not isinstance(envelope["signature"], str):
        raise C05Error("untrusted or stale signed artifact")
    expected = hmac.new(key, identity.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, envelope["signature"]):
        raise C05Error("signature mismatch")
    return dict(body)


def verify_benchmark(
    envelope: Mapping[str, Any],
    trusted: Mapping[str, bytes],
    pins: Mapping[str, Any],
    policy: ProductionPolicy,
    *,
    mode: str = "protected",
) -> BenchmarkReceipt:
    receipt = BenchmarkReceipt.model_validate(verify_signed(envelope, trusted))
    if receipt.isolation.mode != mode or receipt.policy_digest != policy.matcher.identity():
        raise C05Error("benchmark mode or matcher policy mismatch")
    if receipt.fuzzy_policy_digest != policy.review.identity():
        raise C05Error("benchmark fuzzy review policy mismatch")
    if mode == "protected" and receipt.items_without_patterns:
        raise C05Error("benchmark items without frozen signatures require policy review")
    # separate_principal_v1 keeps its historical contract; detached_volume_v1 permits
    # one principal only with its validated root/device separation (model checks).
    if (
        mode == "protected"
        and isinstance(receipt.isolation, Isolation)
        and receipt.isolation.operator_principal.casefold()
        == receipt.isolation.denied_agent_principal.casefold()
    ):
        raise C05Error("same-principal isolation is not protected")
    if {f.task for f in receipt.files} != set(pins):
        raise C05Error("benchmark task coverage incomplete")
    if (
        receipt.items != sum(f.items for f in receipt.files)
        or receipt.duplicate_items > receipt.items
    ):
        raise C05Error("benchmark item accounting mismatch")
    if len({f.path for f in receipt.files}) != len(receipt.files):
        raise C05Error("duplicate benchmark file")
    for f in receipt.files:
        pin = pins[f.task]
        if (f.repository, f.revision) != (pin["repository"], pin["revision"]):
            raise C05Error("stale benchmark revision/repository")
    return receipt


def authorize(plan: ExecutionPlan, issuer: str, key: bytes) -> dict[str, Any]:
    return signed(
        {"kind": "c05_authorization_v2", "plan_digest": plan.identity(), "mode": plan.mode},
        issuer,
        key,
    )


def make_plan(
    manifest: dict[str, Any],
    envelope: dict[str, Any],
    trusted: Mapping[str, bytes],
    pins: Mapping[str, Any],
    policy: ProductionPolicy,
    resources: Resources,
    *,
    sequence: int,
    storage: StorageGeometry,
    scratch: Path,
    output: Path,
    code_commit: str,
    code_identity: str,
    dependency_sha256: str,
    mode: Literal["protected", "authored"] = "protected",
    index: Path | None = None,
    inspector: VolumeInspector | None = None,
) -> ExecutionPlan:
    require_engine_acceptance(mode)
    if manifest.get("digest") != canonical.self_digest(manifest):
        raise C05Error("input manifest digest mismatch")
    receipt = verify_benchmark(envelope, trusted, pins, policy, mode=mode)
    sources = {s["source_key"]: s for s in manifest["sources"]}
    files = tuple(
        InputFile(
            **{
                k: f[k]
                for k in InputFile.model_fields
                if k in f and k not in {"source_id", "source_revision"}
            },
            source_id=sources[f["source_key"]]["source"]["source_id"],
            source_revision=sources[f["source_key"]]["source"]["revision"],
        )
        for f in manifest["files"]
    )
    plan = ExecutionPlan(
        sequence=sequence,
        mode=mode,
        input_manifest_digest=manifest["digest"],
        source_seals={k: s["seal_digest"] for k, s in sources.items()},
        files=files,
        benchmark_receipt_digest=str(envelope["digest"]),
        index_sha256=receipt.index_sha256,
        policy=policy,
        resources=resources,
        storage=storage,
        data_root=manifest["data_root"],
        scratch_root=str(scratch.resolve()),
        output_root=str(output.resolve()),
        code_commit=code_commit,
        code_identity=code_identity,
        dependency_sha256=dependency_sha256,
        isolation=detached_binding(
            receipt, index, manifest["data_root"], scratch, output, inspector
        ),
    )
    plan.identity()
    return plan


def detached_binding(
    receipt: BenchmarkReceipt,
    index: Path | None,
    data_root: str,
    scratch: Path,
    output: Path,
    inspector: VolumeInspector | None = None,
) -> PlanIsolation | None:
    """Bind and live-verify a detached-volume receipt's root, index and plan roots."""
    if not isinstance(receipt.isolation, DetachedVolumeIsolation):
        return None
    if index is None:
        raise C05Error("detached-volume plans must name the protected index on its volume")
    protected = verify_protected_root(receipt.isolation.protected_root, inspector)
    root = Path(protected.path).resolve()
    if not index.resolve().is_relative_to(root):
        raise C05Error("protected index must stay inside the protected benchmark root")
    verify_separation(
        protected,
        (("data_root", data_root), ("c05_scratch", scratch), ("c05_output", output)),
        inspector,
    )
    return PlanIsolation(
        protected_root=protected,
        index_relpath=index.resolve().relative_to(root).as_posix(),
    )


def load_envelope(path: Path) -> dict[str, Any]:
    return read_metadata(path, digested=False)
