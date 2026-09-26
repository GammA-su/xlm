"""M5 order evidence for science-v1 comparisons (P35 §H/§R; M4 slot consumer).

Two artifacts, both about **independent within-source document-order evidence**
(never "data-order robust" in any broader sense):

1. The **order declaration** placed in a comparison manifest's existing
   ``order_robustness.m5_order_evidence`` slot (kind
   ``m5_independent_order_manifests_v1``). It is preregistered and names the
   verifiable headers of >= 2 independent order manifests over one canonical
   membership, plus the tuple -> order allocation. Allocation policy
   ``m5_alternating_roster_allocation_v1`` assigns roster tuple ``i`` (roster
   order) to order ``i mod k``: for the five-pair 50M confirmation C0/C2/C4 -> A
   and C1/C3 -> B; three-tuple 150M/300M blocks cover both orders (A/B/A).
2. The **baseline order-evidence bundle** (``xlm-m5-order-evidence-bundle-v1``):
   post-run provenance binding the declaration, the comparison manifest hash,
   every run's actual order/membership receipts and evidence digest, and the
   initialization counts per order and scale stage. It carries no statistics;
   M4 stays responsible for quality statistics.

M4's promotion rule consumes (1) together with the runs' extracted receipts;
nothing is inferred from labels.
"""

from __future__ import annotations

import itertools
from collections.abc import Mapping, Sequence
from typing import Any

from xlm.artifacts.manifest import identity_digest
from xlm.comparison.science_evidence import (
    EvidenceError,
    evidence_fields,
    verify_evidence_record,
)
from xlm.comparison.science_manifest import (
    MEMBERSHIP_SENTINEL,
    ORDER_SENTINEL,
    arm_ids,
    replicate_identity,
)
from xlm.data.ordering import (
    OrderManifestError,
    header_with_id,
    independence_problems,
    verify_order_header,
)

ORDER_EVIDENCE_KIND = "m5_independent_order_manifests_v1"
ALLOCATION_POLICY = "m5_alternating_roster_allocation_v1"
BUNDLE_VERSION = "xlm-m5-order-evidence-bundle-v1"
TERMINOLOGY = "independent within-source document-order evidence"
SCOPE_NOTE = (
    "Robustness only to the declared M5 intervention (whole-document permutation within "
    "each source over identical canonical train membership). Not a claim of robustness to "
    "all data orders, stochastic sampling or IID resampling."
)
DECLARATION_KEYS = frozenset(
    {
        "kind",
        "canonical_membership_id",
        "order_manifests",
        "order_manifest_ids",
        "allocation_policy",
        "allocation",
    }
)


class OrderEvidenceError(ValueError):
    """An order declaration or bundle cannot establish independent-order evidence."""


def alternating_allocation(tuple_ids: Sequence[str], order_ids: Sequence[str]) -> dict[str, str]:
    """Tuple ``i`` (roster order) -> order ``i mod k``."""
    if len(order_ids) < 2:
        raise OrderEvidenceError("an allocation needs at least two order manifests")
    return {t: order_ids[i % len(order_ids)] for i, t in enumerate(tuple_ids)}


def build_order_declaration(
    manifests: Sequence[Mapping[str, Any]], tuple_ids: Sequence[str]
) -> dict[str, Any]:
    """The preregistered slot value for independent orders ``manifests`` (A, B, ...)."""
    headers = [header_with_id(m) for m in manifests]
    for first, second in itertools.combinations(manifests, 2):
        problems = independence_problems(first, second)
        if problems:
            raise OrderEvidenceError("; ".join(problems))
    ids = [str(h["order_manifest_id"]) for h in headers]
    return {
        "kind": ORDER_EVIDENCE_KIND,
        "canonical_membership_id": headers[0]["canonical_membership_id"],
        "order_manifests": headers,
        "order_manifest_ids": ids,
        "allocation_policy": ALLOCATION_POLICY,
        "allocation": alternating_allocation(tuple_ids, ids),
    }


def declaration_problems(declaration: Any, roster: Any) -> list[str]:
    """Why a declaration does not establish independent orders for this roster."""
    if not isinstance(declaration, Mapping) or set(declaration) != DECLARATION_KEYS:
        return [f"M5 order evidence must be exactly {sorted(DECLARATION_KEYS)}"]
    problems: list[str] = []
    if declaration["kind"] != ORDER_EVIDENCE_KIND:
        problems.append(f"kind must be {ORDER_EVIDENCE_KIND}")
    if declaration["allocation_policy"] != ALLOCATION_POLICY:
        problems.append(f"allocation_policy must be {ALLOCATION_POLICY}")
    headers = declaration["order_manifests"]
    ids = declaration["order_manifest_ids"]
    if not isinstance(headers, list) or not isinstance(ids, list) or len(headers) < 2:
        return [*problems, "at least two order manifest headers are required"]
    verified: list[str] = []
    for i, header in enumerate(headers):
        try:
            verified.append(verify_order_header(header))
        except (OrderManifestError, TypeError, KeyError) as exc:
            problems.append(f"order manifest {i} does not verify: {exc}")
    if problems:
        return problems
    if ids != verified or len(set(ids)) != len(ids):
        problems.append("order_manifest_ids must be the distinct verified header identities")
    membership = declaration["canonical_membership_id"]
    for i, header in enumerate(headers):
        if header["canonical_membership_id"] != membership:
            problems.append(
                f"order manifest {i} orders membership {header['canonical_membership_id']}, "
                f"not the declared {membership}"
            )
    for (i, a), (j, b) in itertools.combinations(enumerate(headers), 2):
        for problem in independence_problems(a, b):
            problems.append(f"orders {i}/{j}: {problem}")
    if not isinstance(roster, list) or not roster:
        return [*problems, "the manifest roster is empty"]
    tuple_ids = [str(e.get("tuple_id")) for e in roster if isinstance(e, Mapping)]
    allocation = declaration["allocation"]
    if not isinstance(allocation, Mapping) or set(allocation) != set(tuple_ids):
        return [*problems, "allocation must assign every roster tuple exactly once"]
    if len(tuple_ids) < len(ids):
        problems.append("fewer roster tuples than order manifests: some order is unused")
    elif dict(allocation) != alternating_allocation(tuple_ids, ids):
        problems.append(
            f"allocation does not follow {ALLOCATION_POLICY} (tuple i -> order i mod "
            f"{len(ids)} in roster order; five 50M tuples: C0/C2/C4 -> A, C1/C3 -> B)"
        )
    for entry in roster:
        if isinstance(entry, Mapping) and entry.get("order_manifest_id") != allocation.get(
            entry.get("tuple_id")
        ):
            problems.append(
                f"roster tuple '{entry.get('tuple_id')}' carries order "
                f"{entry.get('order_manifest_id')}, the declaration allocates "
                f"{allocation.get(entry.get('tuple_id'))}"
            )
    return problems


def run_binding_problems(
    declaration: Mapping[str, Any],
    order_ids: Mapping[str, str],
    membership_ids: Mapping[str, str] | None,
) -> list[str]:
    """Actual per-tuple receipts against the declaration (complete pairs only)."""
    problems: list[str] = []
    allocation = declaration["allocation"]
    for tuple_id, order_id in sorted(order_ids.items()):
        if order_id == ORDER_SENTINEL:
            problems.append(f"tuple '{tuple_id}' ran under the pre-M5 shard-native order")
        elif allocation.get(tuple_id) != order_id:
            problems.append(
                f"tuple '{tuple_id}' ran under order {order_id}, allocated "
                f"{allocation.get(tuple_id)}"
            )
    if membership_ids is None:
        problems.append("run canonical membership receipts were not supplied")
    else:
        for tuple_id, membership in sorted(membership_ids.items()):
            if membership != declaration["canonical_membership_id"]:
                problems.append(
                    f"tuple '{tuple_id}' trained on membership {membership}, not the declared "
                    f"{declaration['canonical_membership_id']}"
                )
    if len(set(order_ids.values())) < 2:
        problems.append("the paired runs span fewer than two independent order manifests")
    return problems


# --------------------------------------------------------------------- bundle


def initialization_counts(
    runs: Sequence[Mapping[str, Any]], order_ids: Sequence[str]
) -> dict[str, Any]:
    """Unique scientific replicate identities per order; reruns never add.

    A run row needs ``order_manifest_id``, ``replicate_identity``, ``init_seed`` and
    ``arm_id``. Retries/duplicates of one identity count once.
    """
    by_order: dict[str, Any] = {}
    for order_id in order_ids:
        rows = [r for r in runs if r["order_manifest_id"] == order_id]
        identities = {r["replicate_identity"] for r in rows}
        by_arm: dict[str, int] = {}
        for arm in sorted({str(r["arm_id"]) for r in rows}):
            by_arm[arm] = len({r["replicate_identity"] for r in rows if r["arm_id"] == arm})
        by_order[order_id] = {
            "unique_replicate_identities": len(identities),
            "unique_init_seeds": len({r["init_seed"] for r in rows}),
            "unique_replicate_identities_by_arm": by_arm,
            "run_attempts": len(rows),
            "repeat_attempts_not_counted": len(rows) - sum(by_arm.values()),
        }
    return by_order


def build_order_bundle(
    manifest: Mapping[str, Any],
    runs: Sequence[tuple[str, str, Mapping[str, Any]]],
) -> dict[str, Any]:
    """Baseline order-evidence bundle from a comparison manifest and run evidence.

    ``runs`` are ``(label, arm_id, evidence_record)`` for every completed attempt.
    """
    slot = manifest.get("order_robustness") or {}
    declaration = slot.get("m5_order_evidence") if isinstance(slot, Mapping) else None
    problems = declaration_problems(declaration, manifest.get("replicate_roster"))
    if problems:
        raise OrderEvidenceError("; ".join(problems))
    assert isinstance(declaration, Mapping)
    roster = {
        replicate_identity(
            e["init_seed"], e["training_seed"], e["data_seed"], e["order_manifest_id"]
        ): e
        for e in manifest["replicate_roster"]
    }
    rows: list[dict[str, Any]] = []
    for label, arm_id, evidence in runs:
        try:
            verify_evidence_record(evidence)
        except EvidenceError as exc:
            raise OrderEvidenceError(f"run '{label}': {exc}") from exc
        fields = evidence_fields(evidence)
        rep = evidence["replicate"]
        identity = replicate_identity(
            rep["init_seed"], rep["training_seed"], rep["data_seed"], rep["order_manifest_id"]
        )
        entry = roster.get(identity)
        rows.append(
            {
                "label": label,
                "arm_id": arm_id,
                "tuple_id": entry["tuple_id"] if entry is not None else None,
                "replicate_identity": identity,
                "init_seed": rep["init_seed"],
                "order_manifest_id": rep["order_manifest_id"],
                "canonical_membership_id": fields.get("canonical_membership_id"),
                "within_source_order_policy": fields.get("within_source_order_policy"),
                "evidence_digest": evidence["evidence_digest"],
                "at_budget": bool(evidence["endpoint"]["at_budget"]),
            }
        )
    rows.sort(key=lambda r: (str(r["tuple_id"]), r["arm_id"], r["label"]))
    return seal_bundle(
        {
            "bundle_version": BUNDLE_VERSION,
            "kind": ORDER_EVIDENCE_KIND,
            "terminology": TERMINOLOGY,
            "scope": SCOPE_NOTE,
            "canonical_membership_id": declaration["canonical_membership_id"],
            "order_manifest_ids": list(declaration["order_manifest_ids"]),
            "declaration": dict(declaration),
            "roster": [dict(e) for e in manifest["replicate_roster"]],
            "arms": arm_ids(manifest),
            "comparison": {
                "comparison_id": manifest.get("comparison_id"),
                "manifest_hash": identity_digest(dict(manifest)),
                "scale_stage": manifest.get("scale_stage"),
                "study_stage": manifest.get("study_stage"),
            },
            "derivation": {
                str(h["order_manifest_id"]): dict(h["derivation"])
                for h in declaration["order_manifests"]
            },
            "runs": rows,
        }
    )


def _computed(bundle: Mapping[str, Any]) -> dict[str, Any]:
    declaration = bundle["declaration"]
    ids = list(declaration["order_manifest_ids"])
    runs = bundle["runs"]
    counted = [r for r in runs if r["tuple_id"] is not None]
    counts = initialization_counts(counted, ids)
    complete: dict[str, list[str]] = {oid: [] for oid in ids}
    for entry in bundle["roster"]:
        tuple_id = entry["tuple_id"]
        at_budget = {r["arm_id"] for r in counted if r["tuple_id"] == tuple_id and r["at_budget"]}
        if set(bundle["arms"]) <= at_budget:
            complete[declaration["allocation"][tuple_id]].append(tuple_id)
    return {
        "initialization_counts": {
            "by_order": counts,
            "by_scale_stage": {str(bundle["comparison"]["scale_stage"]): counts},
        },
        "complete_pairs_by_order": complete,
    }


def seal_bundle(payload: Mapping[str, Any]) -> dict[str, Any]:
    bundle = {**payload, **_computed(payload)}
    bundle["bundle_hash"] = identity_digest(bundle)
    return bundle


def bundle_problems(bundle: Any, *, require_complete: bool = True) -> list[str]:
    """Why a bundle does not establish independent within-source order evidence."""
    if not isinstance(bundle, Mapping) or bundle.get("bundle_version") != BUNDLE_VERSION:
        return [f"not a {BUNDLE_VERSION} bundle"]
    payload = {k: v for k, v in bundle.items() if k != "bundle_hash"}
    if bundle.get("bundle_hash") != identity_digest(payload):
        return ["bundle hash does not verify (altered)"]
    problems = declaration_problems(bundle["declaration"], bundle["roster"])
    if problems:
        return problems
    if bundle["canonical_membership_id"] != bundle["declaration"]["canonical_membership_id"] or (
        bundle["order_manifest_ids"] != bundle["declaration"]["order_manifest_ids"]
    ):
        return ["bundle identities differ from its declaration"]
    recomputed = _computed(payload)
    for key, value in recomputed.items():
        if bundle[key] != value:
            problems.append(f"{key} does not recompute from the bound runs")
    declaration = bundle["declaration"]
    for row in bundle["runs"]:
        if row["tuple_id"] is None:
            problems.append(f"run '{row['label']}' is not a registered roster replicate")
            continue
        if row["order_manifest_id"] != declaration["allocation"][row["tuple_id"]]:
            problems.append(f"run '{row['label']}' ran under an order not allocated to its tuple")
        if row["canonical_membership_id"] in (None, MEMBERSHIP_SENTINEL) or (
            row["canonical_membership_id"] != declaration["canonical_membership_id"]
        ):
            problems.append(f"run '{row['label']}' did not train on the declared membership")
    used = {r["order_manifest_id"] for r in bundle["runs"] if r["tuple_id"] is not None}
    if len(used) < 2:
        problems.append("runs span fewer than two independent order manifests")
    if require_complete:
        for oid, tuples in recomputed["complete_pairs_by_order"].items():
            allocated = [t for t, o in declaration["allocation"].items() if o == oid]
            if sorted(tuples) != sorted(allocated):
                problems.append(
                    f"order {oid}: complete pairs {sorted(tuples)} of allocated {sorted(allocated)}"
                )
    return problems
