"""Order-independent union-find clustering with frozen survivor selection.

Contract C05 requires deterministic survivor selection and retention of every source
alias of the surviving document. Both the cluster identity and the survivor choice
here are functions of cluster *content* only -- never of insertion order, partition
count or worker count -- so the same corpus yields the same clusters however the work
was sharded.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

SURVIVOR_RULE_VERSION = "1"
CLUSTER_ID_VERSION = "1"


class UnionFind:
    """Union-find over string keys with union-by-size and path compression."""

    def __init__(self) -> None:
        self._parent: dict[str, str] = {}
        self._size: dict[str, int] = {}

    def add(self, key: str) -> None:
        if key not in self._parent:
            self._parent[key] = key
            self._size[key] = 1

    def find(self, key: str) -> str:
        self.add(key)
        root = key
        while self._parent[root] != root:
            root = self._parent[root]
        # Path compression: flatten the walked chain onto the root.
        while self._parent[key] != root:
            self._parent[key], key = root, self._parent[key]
        return root

    def union(self, left: str, right: str) -> None:
        left_root, right_root = self.find(left), self.find(right)
        if left_root == right_root:
            return
        # Union by size, with a deterministic tie-break so the internal forest shape
        # does not depend on the order edges happened to arrive.
        if self._size[left_root] < self._size[right_root] or (
            self._size[left_root] == self._size[right_root] and right_root < left_root
        ):
            left_root, right_root = right_root, left_root
        self._parent[right_root] = left_root
        self._size[left_root] += self._size[right_root]

    def groups(self) -> dict[str, list[str]]:
        """Return root -> sorted members for every known key."""
        grouped: dict[str, list[str]] = {}
        for key in self._parent:
            grouped.setdefault(self.find(key), []).append(key)
        for members in grouped.values():
            members.sort()
        return grouped


@dataclass(frozen=True)
class DocumentFacts:
    """The minimal per-document facts clustering needs, kept small on purpose.

    Holding facts rather than whole documents is what lets clustering run over a
    corpus far larger than RAM.
    """

    doc_id: str
    source_id: str
    clean_hash: str
    utf8_byte_count: int
    lineage_key: str
    lineage_rule: str


@dataclass
class DuplicateCluster:
    """A resolved duplicate cluster with its survivor and retained aliases."""

    cluster_id: str
    member_doc_ids: list[str]
    survivor_doc_id: str
    dropped_doc_ids: list[str]
    source_aliases: list[str]
    match_kinds: list[str]
    size: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "cluster_id": self.cluster_id,
            "member_doc_ids": self.member_doc_ids,
            "survivor_doc_id": self.survivor_doc_id,
            "dropped_doc_ids": self.dropped_doc_ids,
            "source_aliases": self.source_aliases,
            "match_kinds": self.match_kinds,
            "size": self.size,
            "metadata": self.metadata,
        }


def compute_cluster_id(member_doc_ids: Iterable[str]) -> str:
    """Derive a cluster ID from its sorted membership.

    Because the ID is a digest of the sorted member set, two runs that discover the
    same cluster in different orders produce the same ID, and a cluster that gains a
    member gets a visibly different ID rather than silently mutating in place.
    """
    members = sorted(set(member_doc_ids))
    if not members:
        raise ValueError("cannot compute a cluster ID for an empty member set")
    payload = f"v{CLUSTER_ID_VERSION}:" + "|".join(members)
    return "dup_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def select_survivor(members: Iterable[str], facts: Mapping[str, DocumentFacts]) -> str:
    """Select the surviving document for a cluster under the frozen rule.

    Frozen rule v1, applied in order:

    1. Largest canonical UTF-8 byte count -- prefer the most complete rendering.
    2. Lexicographically smallest ``source_id`` -- a stable, content-free tie-break.
    3. Lexicographically smallest ``doc_id``.

    No step consults arrival order, so the result is identical across worker counts.
    """
    member_list = sorted(set(members))
    if not member_list:
        raise ValueError("cannot select a survivor from an empty cluster")
    missing = [m for m in member_list if m not in facts]
    if missing:
        raise KeyError(f"missing document facts for cluster members: {missing}")

    return min(
        member_list,
        key=lambda doc_id: (
            -facts[doc_id].utf8_byte_count,
            facts[doc_id].source_id,
            doc_id,
        ),
    )


def build_clusters(
    union_find: UnionFind,
    facts: Mapping[str, DocumentFacts],
    match_kinds: Mapping[str, set[str]] | None = None,
) -> list[DuplicateCluster]:
    """Materialize duplicate clusters, sorted by cluster ID for stable output."""
    clusters: list[DuplicateCluster] = []
    for members in union_find.groups().values():
        if len(members) < 2:
            continue
        cluster_id = compute_cluster_id(members)
        survivor = select_survivor(members, facts)
        kinds: set[str] = set()
        if match_kinds:
            for member in members:
                kinds |= match_kinds.get(member, set())
        clusters.append(
            DuplicateCluster(
                cluster_id=cluster_id,
                member_doc_ids=members,
                survivor_doc_id=survivor,
                dropped_doc_ids=[m for m in members if m != survivor],
                # Every source that contributed a member is retained on the survivor,
                # so provenance is not lost when duplicates are dropped (C05).
                source_aliases=sorted({facts[m].source_id for m in members}),
                match_kinds=sorted(kinds),
                size=len(members),
            )
        )
    clusters.sort(key=lambda c: c.cluster_id)
    return clusters
