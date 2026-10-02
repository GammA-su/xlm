"""Content-free bridge from verified C05 completion to the official claim gate."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from xlm.data.exclusion.artifacts import ExecutionPlan, verify_benchmark
from xlm.data.exclusion.policy import C05Error
from xlm.data.exclusion.receipt import FinalExclusionReceipt, sign_receipt
from xlm.data.exclusion.runner import verify_completion


def final_receipt(
    directory: Path,
    plan: ExecutionPlan,
    benchmark: Mapping[str, Any],
    pins: Mapping[str, Any],
    trusted: Mapping[str, bytes],
    issuer: str,
    key: bytes,
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
    receipt = FinalExclusionReceipt(
        receipt_id="c05-" + str(envelope["digest"]),
        schema_version="2",
        issued_at=datetime.now(UTC).isoformat(),
        issuer_id=issuer,
        mode="protected" if plan.mode == "protected" else "development",
        corpus_input_digest=plan.input_manifest_digest,
        exclusion_policy_identity=plan.policy.identity(),
        exclusion_index_identity=plan.index_sha256,
        output_membership_digest=body["membership_sha256"],
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
            "Membership digest is the complete v2 membership file SHA-256, "
            "not the legacy doc-ID digest.",
        ],
        c05_binding=bindings,
    )
    return sign_receipt(receipt, key)
