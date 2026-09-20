"""Pool freeze manifests and late bridge-duplicate invalidation.

Contract C05: if new sources later merge formerly separate groups, refreeze the
affected pool and invalidate comparisons; never quietly reuse now-leaking splits.

A :class:`PoolFreeze` binds a pool's identity to the dedup configuration, the split
policy, and a digest of the actual membership. :func:`detect_bridge_duplicates`
compares a frozen pool against a newer dedup result and reports the groups that a new
snapshot has bridged. A bridge that spans two different splits is a leak, and the
frozen pool must be superseded by a new lineage rather than edited in place.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from xlm.data.dedup.engine import DedupResult
from xlm.data.pools.splits import SplitAssignment

POOL_FREEZE_VERSION = "1"


class PoolInvalidatedError(RuntimeError):
    """Raised when a frozen pool is used despite a known invalidating bridge."""


@dataclass
class PoolFreeze:
    """An immutable record of a frozen corpus pool."""

    pool_id: str
    created_at: str
    dedup_identity: str
    split_policy_identity: str
    membership_digest: str
    document_count: int
    achieved_bytes: dict[str, int]
    achieved_documents: dict[str, int]
    quick_val_bytes: int
    quick_val_document_count: int
    group_count: int
    split_of_doc: dict[str, str]
    group_of_doc: dict[str, str]
    superseded_by: str | None = None
    supersedes: str | None = None
    invalidation_reason: str | None = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def is_active(self) -> bool:
        """A pool stays usable until something supersedes it."""
        return self.superseded_by is None

    def require_active(self) -> None:
        """Raise if this pool has been superseded."""
        if not self.is_active:
            raise PoolInvalidatedError(
                f"Pool '{self.pool_id}' was invalidated ({self.invalidation_reason}) and "
                f"superseded by '{self.superseded_by}'. Use the successor pool; a "
                "superseded split may leak across the train/validation boundary."
            )

    def save(self, path: Path) -> Path:
        """Write the freeze manifest atomically."""
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_text(json.dumps(self.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
        temp.replace(path)
        return path

    @staticmethod
    def load(path: Path) -> PoolFreeze:
        """Load a freeze manifest."""
        data = json.loads(path.read_text(encoding="utf-8"))
        return PoolFreeze(**data)


def freeze_pool(
    assignment: SplitAssignment,
    dedup_result: DedupResult,
    pool_label: str = "pool",
    supersedes: str | None = None,
) -> PoolFreeze:
    """Create a pool freeze bound to a specific dedup run and split assignment.

    The pool ID is derived from the behavioral inputs, so an identical corpus,
    dedup configuration and split policy reproduce the same ID, while any behavioral
    change produces a visibly different one.
    """
    identity_payload = (
        f"v{POOL_FREEZE_VERSION}:{dedup_result.config_identity}:"
        f"{assignment.policy_identity}:{assignment.membership_digest()}"
    )
    pool_id = f"{pool_label}_" + hashlib.sha256(identity_payload.encode("utf-8")).hexdigest()[:20]

    return PoolFreeze(
        pool_id=pool_id,
        created_at=datetime.now(UTC).isoformat(),
        dedup_identity=dedup_result.config_identity,
        split_policy_identity=assignment.policy_identity,
        membership_digest=assignment.membership_digest(),
        document_count=len(assignment.assignments),
        achieved_bytes=dict(assignment.achieved_bytes),
        achieved_documents=dict(assignment.achieved_documents),
        quick_val_bytes=assignment.quick_val_bytes,
        quick_val_document_count=len(assignment.quick_val_doc_ids),
        group_count=assignment.group_count,
        split_of_doc=dict(assignment.assignments),
        group_of_doc=dict(assignment.group_of_doc),
        supersedes=supersedes,
    )


@dataclass
class BridgeFinding:
    """One newly discovered duplicate relation that bridges frozen split groups."""

    cluster_id: str
    bridged_doc_ids: list[str]
    previous_groups: list[str]
    affected_splits: list[str]
    crosses_split_boundary: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class BridgeReport:
    """The outcome of checking a frozen pool against a newer dedup result."""

    pool_id: str
    findings: list[BridgeFinding]

    @property
    def invalidating_findings(self) -> list[BridgeFinding]:
        """Only bridges that actually cross a split boundary invalidate a pool."""
        return [f for f in self.findings if f.crosses_split_boundary]

    @property
    def requires_refreeze(self) -> bool:
        return bool(self.invalidating_findings)

    def to_dict(self) -> dict[str, Any]:
        return {
            "pool_id": self.pool_id,
            "findings": [f.to_dict() for f in self.findings],
            "requires_refreeze": self.requires_refreeze,
            "invalidating_count": len(self.invalidating_findings),
        }


def detect_bridge_duplicates(freeze: PoolFreeze, new_dedup: DedupResult) -> BridgeReport:
    """Find duplicate clusters in ``new_dedup`` that bridge formerly separate groups.

    Only documents already present in the frozen pool are considered: a cluster made
    entirely of new documents extends the corpus but does not bridge anything that
    was frozen.
    """
    findings: list[BridgeFinding] = []

    for cluster in new_dedup.clusters:
        known = [d for d in cluster.member_doc_ids if d in freeze.split_of_doc]
        if len(known) < 2:
            continue

        previous_groups = sorted({freeze.group_of_doc[d] for d in known})
        if len(previous_groups) < 2:
            continue  # already in one group; nothing was bridged

        affected_splits = sorted({freeze.split_of_doc[d] for d in known})
        findings.append(
            BridgeFinding(
                cluster_id=cluster.cluster_id,
                bridged_doc_ids=sorted(known),
                previous_groups=previous_groups,
                affected_splits=affected_splits,
                crosses_split_boundary=len(affected_splits) > 1,
            )
        )

    findings.sort(key=lambda f: f.cluster_id)
    return BridgeReport(pool_id=freeze.pool_id, findings=findings)


def supersede_pool(
    freeze: PoolFreeze,
    successor: PoolFreeze,
    reason: str,
) -> tuple[PoolFreeze, PoolFreeze]:
    """Mark ``freeze`` superseded by ``successor``, creating a new lineage.

    The old freeze is not edited to hide the problem: it keeps its membership and
    gains an explicit invalidation reason and a pointer to its successor, so any
    comparison that used it can still be identified and re-run.
    """
    if successor.pool_id == freeze.pool_id:
        raise ValueError("a pool cannot supersede itself; the successor must be a new freeze")

    invalidated = PoolFreeze(**{**freeze.to_dict()})
    invalidated.superseded_by = successor.pool_id
    invalidation_note = f"invalidated: {reason}"
    invalidated.invalidation_reason = reason
    if invalidation_note not in invalidated.notes:
        invalidated.notes.append(invalidation_note)

    linked_successor = PoolFreeze(**{**successor.to_dict()})
    linked_successor.supersedes = freeze.pool_id

    return invalidated, linked_successor
