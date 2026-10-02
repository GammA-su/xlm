"""Content-free bridge from verified C05 completion to the official claim gate.

Schema 2 binds the global C05 completion (kept membership). Schema 3 additionally
binds the signed final freeze: exact selected training membership, tokenizer, exact
counts, frozen quotas, recipe and exposure plan. Official claims require schema 3.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from xlm.data.evidence_v2 import canonical
from xlm.data.exclusion.artifacts import ExecutionPlan, verify_benchmark
from xlm.data.exclusion.gates import MembershipGate
from xlm.data.exclusion.policy import C05Error
from xlm.data.exclusion.receipt import (
    BenchmarkClaimBinding,
    FinalExclusionReceipt,
    sign_receipt,
)
from xlm.data.exclusion.runner import verify_completion


def selection_binding(freeze: Mapping[str, Any]) -> dict[str, Any]:
    body = freeze["payload"]
    return {
        "selection_digest": body["selection_digest"],
        "selected_membership_sha256": body["selected_membership_sha256"],
        "freeze_digest": freeze["digest"],
        "tokenizer_fingerprint": body["tokenizer"]["fingerprint"],
        "tokenizer_files_digest": body["tokenizer"]["files_digest"],
        "counts_digest": body["counts_digest"],
        "quota_sha256": body["quota_sha256"],
        "requirements_digest": body["requirements_digest"],
        "recipe_identity": body["recipe_identity"],
        "exposure_plan_digest": body["exposure_plan_digest"],
        "selected_valid_targets": body["valid_targets"],
    }


def final_receipt(
    directory: Path,
    plan: ExecutionPlan,
    benchmark: Mapping[str, Any],
    pins: Mapping[str, Any],
    trusted: Mapping[str, bytes],
    issuer: str,
    key: bytes,
    *,
    freeze: Path | None = None,
    gate: MembershipGate | None = None,
) -> FinalExclusionReceipt:
    if trusted.get(issuer) != key:
        raise C05Error("final receipt signer is not trusted")
    verify_benchmark(benchmark, trusted, pins, plan.policy, mode=plan.mode)
    if benchmark["digest"] != plan.benchmark_receipt_digest:
        raise C05Error("final receipt benchmark differs from plan")
    envelope = verify_completion(directory, plan, trusted)
    body = envelope["payload"]
    bindings = {
        name: body[name]
        for name in (
            "plan_digest",
            "benchmark_receipt_digest",
            "source_seals",
            "input_manifest_digest",
            "membership_sha256",
            "policy_digest",
            "index_sha256",
            "review_decisions",
        )
    }
    bindings["completion_digest"] = envelope["digest"]
    selection: dict[str, Any] | None = None
    output_membership = body["membership_sha256"]
    if freeze is not None:
        from xlm.data.exclusion.freeze import verify_freeze

        if gate is None or (gate.plan_digest, gate.receipt_digest) != (
            plan.identity(),
            envelope["digest"],
        ):
            raise C05Error("freeze verification requires this completion's membership gate")
        selection = selection_binding(verify_freeze(freeze, gate))
        output_membership = selection["selected_membership_sha256"]
    receipt = FinalExclusionReceipt(
        receipt_id="c05-" + str(envelope["digest"]),
        schema_version="2" if selection is None else "3",
        issued_at=datetime.now(UTC).isoformat(),
        issuer_id=issuer,
        mode="protected" if plan.mode == "protected" else "development",
        corpus_input_digest=plan.input_manifest_digest,
        exclusion_policy_identity=plan.policy.identity(),
        exclusion_index_identity=plan.index_sha256,
        output_membership_digest=output_membership,
        documents_considered=body["documents"],
        documents_dropped=body["excluded"] + body["duplicates"],
        aggregate_counts={
            "kept": body["kept"],
            "excluded": body["excluded"],
            "duplicates": body["duplicates"],
        },
        notes=[
            "Exact compiled-signature containment and known-group propagation; "
            "screened with limitations.",
            "c05_binding.membership_sha256 is the complete v2 kept-membership file SHA-256, "
            "not the legacy doc-ID digest.",
            *(
                [
                    "output_membership_digest is the exact quota-selected training membership "
                    "bound by the signed final freeze."
                ]
                if selection is not None
                else []
            ),
            *(
                [
                    "Benchmark isolation: detached_volume_v1 (detached-volume operational "
                    "isolation; same OS principal permitted; accidental/process-level "
                    "protection, not adversarial security)."
                ]
                if benchmark["payload"]["isolation"].get("mechanism") == "detached_volume_v1"
                else []
            ),
        ],
        c05_binding=bindings,
        selection_binding=selection,
    )
    return sign_receipt(receipt, key)


def claim_binding(
    freeze: Mapping[str, Any],
    plan: ExecutionPlan,
    *,
    checkpoint_hash: str,
    suite_fingerprint: str,
) -> BenchmarkClaimBinding:
    """Expected identities from the verified freeze, supplied independently of a receipt."""
    body = freeze["payload"]
    if body["plan_digest"] != plan.identity():
        raise C05Error("freeze belongs to a different C05 plan")
    return BenchmarkClaimBinding(
        checkpoint_hash=checkpoint_hash,
        suite_fingerprint=suite_fingerprint,
        corpus_input_digest=plan.input_manifest_digest,
        output_membership_digest=body["selected_membership_sha256"],
        exclusion_policy_identity=plan.policy.identity(),
        exclusion_index_identity=plan.index_sha256,
        selection_digest=body["selection_digest"],
        freeze_digest=str(freeze["digest"]),
        tokenizer_fingerprint=body["tokenizer"]["fingerprint"],
        quota_sha256=body["quota_sha256"],
        source_seals_digest=canonical.digest(plan.source_seals),
    )
