"""Versioned protected benchmark-isolation mechanisms for C05.

``separate_principal_v1`` (historical; receipts carry no ``mechanism`` field): the
benchmark is prepared and screened by an operator OS identity that differs from the
agent identity, whose access is denied by operator-verified OS controls.

``detached_volume_v1``: the operator and agent may be the same OS account. Raw
benchmark material, the protected index and C05 scratch (which holds detailed match
facts and, with review enabled, raw signature tokens) live on a dedicated,
separately mounted volume that is attached only for protected preparation and the
C05 run and detached before any tokenizer or training work. Its claim is
*detached-volume operational isolation* against accidental, process-level
contamination. It is NOT adversarial security: the same OS user can deliberately
mount, copy or read the volume, and nothing here denies the agent OS access.

The scientific invariant both mechanisms serve: benchmark examples, labels and
detailed signatures never become tokenizer or model-training membership. C05
membership proof remains the primary control; the mount guard is defense in depth.
"""

from __future__ import annotations

import getpass
import hashlib
import os
import re
import secrets
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Annotated, Any, Final, Literal

from pydantic import Discriminator, Field, Tag, model_validator

from xlm.data.evidence_v2 import canonical
from xlm.data.exclusion.policy import C05Error, FrozenModel

Sha = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]

SEPARATE_PRINCIPAL: Final = "separate_principal_v1"
DETACHED_VOLUME: Final = "detached_volume_v1"
MARKER_NAME: Final = "C05-PROTECTED-ROOT.json"
MARKER_BYTES: Final = 4096
LOGICAL_ID: Final = r"^[a-z0-9][a-z0-9._-]{2,63}$"
ISOLATION_CLAIM: Final = "detached-volume operational isolation"
THREAT_MODEL: Final = (
    "accidental/process-level benchmark contamination only; not adversarial security "
    "against the same OS user deliberately circumventing the boundary"
)
ACCESS_POLICY: Final = (
    "mount only for protected preparation and the C05 run; detach before tokenizer "
    "fitting, tokenization or model training (v1)"
)
# Roles whose filesystem device must differ from the protected volume.
OFF_VOLUME_ROLES: Final = frozenset({"data_root", "training_data", "c05_output"})
Role = Literal["repository", "data_root", "training_data", "c05_scratch", "c05_output"]


class Isolation(FrozenModel):
    """Historical ``separate_principal_v1`` isolation; field set unchanged."""

    mode: Literal["protected", "authored"]
    operator_principal: str = Field(min_length=1)
    denied_agent_principal: str = Field(min_length=1)
    attestation_sha256: Sha
    access_controls_verified: Literal[True]

    @property
    def mechanism(self) -> str:
        return SEPARATE_PRINCIPAL


class VolumeIdentity(FrozenModel):
    """Filesystem device identity (Windows: volume serial number via ``os.stat``)."""

    scheme: Literal["os-stat-st-dev-v1"] = "os-stat-st-dev-v1"
    device: str = Field(pattern=r"^[0-9A-Za-z._:-]{1,128}$")


class ProtectedRoot(FrozenModel):
    """Content-free identity of the dedicated protected benchmark root."""

    logical_id: str = Field(pattern=LOGICAL_ID)
    path: str = Field(min_length=1)
    marker_sha256: Sha
    volume: VolumeIdentity


class RootIdentity(FrozenModel):
    role: Role
    path: str = Field(min_length=1)
    volume: VolumeIdentity


def overlaps(first: str | Path, second: str | Path) -> bool:
    a, b = Path(first).resolve(), Path(second).resolve()
    return a.is_relative_to(b) or b.is_relative_to(a)


def check_role(
    protected: ProtectedRoot, role: str, path: str | Path, volume: VolumeIdentity
) -> None:
    """One root's separation from the protected benchmark root and volume."""
    if overlaps(path, protected.path):
        raise C05Error(f"protected benchmark root overlaps {role}")
    if role in OFF_VOLUME_ROLES and volume == protected.volume:
        raise C05Error(f"{role} shares the protected benchmark filesystem device")
    if role == "c05_scratch" and volume != protected.volume:
        raise C05Error("C05 scratch (detailed matches) must stay on the protected volume")


class DetachedVolumeIsolation(FrozenModel):
    """``detached_volume_v1``: same-principal detached-volume operational isolation."""

    mechanism: Literal["detached_volume_v1"]
    mode: Literal["protected", "authored"]
    isolation: Literal["detached-volume operational isolation"] = ISOLATION_CLAIM
    threat_model: Literal[
        "accidental/process-level benchmark contamination only; not adversarial security "
        "against the same OS user deliberately circumventing the boundary"
    ] = THREAT_MODEL
    access_policy: Literal[
        "mount only for protected preparation and the C05 run; detach before tokenizer "
        "fitting, tokenization or model training (v1)"
    ] = ACCESS_POLICY
    operator_principal: str = Field(min_length=1)
    # The agent may be the same OS account; no OS access denial is asserted.
    agent_principal: str = Field(min_length=1)
    agent_os_access_denial: Literal["not_asserted"] = "not_asserted"
    protected_root: ProtectedRoot
    separated_roots: tuple[RootIdentity, ...] = Field(min_length=2)
    attestation_sha256: Sha

    @model_validator(mode="after")
    def _separated(self) -> DetachedVolumeIsolation:
        if not Path(self.protected_root.path).is_absolute():
            raise ValueError("protected benchmark root must be an absolute path")
        roles = {r.role for r in self.separated_roots}
        if not {"repository", "data_root"} <= roles:
            raise ValueError("detached-volume isolation must declare repository and data roots")
        for root in self.separated_roots:
            check_role(self.protected_root, root.role, root.path, root.volume)
        return self


def _mechanism(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("mechanism", SEPARATE_PRINCIPAL))
    return str(getattr(value, "mechanism", SEPARATE_PRINCIPAL))


# A historical receipt has no ``mechanism`` key and keeps its v1 meaning; a receipt
# naming ``detached_volume_v1`` must satisfy the detached-volume contract.
AnyIsolation = Annotated[
    Annotated[Isolation, Tag(SEPARATE_PRINCIPAL)]
    | Annotated[DetachedVolumeIsolation, Tag(DETACHED_VOLUME)],
    Discriminator(_mechanism),
]

VolumeInspector = Callable[[Path], VolumeIdentity]


def os_volume(path: Path) -> VolumeIdentity:
    """Device of ``path`` (or its nearest existing ancestor); unknown refuses."""
    probe = path.resolve()
    while not probe.exists():
        if probe.parent == probe:
            raise C05Error("no existing ancestor to establish a filesystem device")
        probe = probe.parent
    device = os.stat(probe).st_dev
    if not device:
        raise C05Error("filesystem device identity unavailable; separation not established")
    return VolumeIdentity(device=str(device))


def init_protected_root(path: Path, logical_id: str) -> dict[str, Any]:
    """Write the write-once, content-free root marker (a random identity nonce)."""
    if not path.is_absolute():
        raise C05Error("protected benchmark root must be an absolute path")
    if not re.fullmatch(LOGICAL_ID, logical_id):
        raise C05Error("protected root logical id must match " + LOGICAL_ID)
    path.mkdir(parents=True, exist_ok=True)
    marker = {
        "kind": "c05_protected_root_v1",
        "logical_id": logical_id,
        "root_nonce": secrets.token_hex(16),
    }
    with (path / MARKER_NAME).open("xb") as stream:
        stream.write(canonical.canonical_bytes(marker))
    return marker


def inspect_protected_root(path: Path, inspector: VolumeInspector | None = None) -> ProtectedRoot:
    """Live identity: marker digest and filesystem device of a mounted root."""
    marker_path = path / MARKER_NAME
    if not marker_path.is_file():
        raise C05Error("protected benchmark root is not mounted or has no root marker")
    raw = marker_path.read_bytes()
    if len(raw) > MARKER_BYTES:
        raise C05Error("protected root marker size ceiling")
    marker = canonical.loads_bytes_strict(raw)
    if not isinstance(marker, dict) or marker.get("kind") != "c05_protected_root_v1":
        raise C05Error("protected root marker schema")
    return ProtectedRoot(
        logical_id=str(marker.get("logical_id")),
        path=str(path.resolve()),
        marker_sha256=hashlib.sha256(raw).hexdigest(),
        volume=(inspector or os_volume)(path),
    )


def verify_protected_root(
    expected: ProtectedRoot, inspector: VolumeInspector | None = None
) -> ProtectedRoot:
    live = inspect_protected_root(Path(expected.path), inspector)
    if live != expected:
        raise C05Error("protected benchmark root or volume identity differs from the receipt")
    return live


def verify_separation(
    protected: ProtectedRoot,
    roots: Iterable[tuple[Role, str | Path]],
    inspector: VolumeInspector | None = None,
) -> list[RootIdentity]:
    """Live device/overlap checks of actual roots against the protected root."""
    measure = inspector or os_volume
    result = []
    for role, path in roots:
        volume = measure(Path(path))
        check_role(protected, role, path, volume)
        result.append(RootIdentity(role=role, path=str(Path(path).resolve()), volume=volume))
    return result


def checkout_root() -> Path:
    """Repository checkout of the running code; never inside the protected root."""
    return Path(__file__).resolve().parents[4]


def require_operator(isolation: Isolation | DetachedVolumeIsolation) -> None:
    """The running OS principal must be the recorded operator.

    ``separate_principal_v1`` additionally requires a distinct denied agent identity;
    ``detached_volume_v1`` permits the same principal by contract.
    """
    if (
        isinstance(isolation, Isolation)
        and isolation.operator_principal.casefold() == isolation.denied_agent_principal.casefold()
    ):
        raise C05Error("separate_principal_v1 requires a separate operator identity")
    if getpass.getuser().casefold() != isolation.operator_principal.casefold():
        raise C05Error("protected principal differs from the recorded operator")
