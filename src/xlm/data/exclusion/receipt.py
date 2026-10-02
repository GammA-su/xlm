"""Operator-side final exclusion receipts.

EVALUATION_POLICY: decontamination against protected final data happens in an
isolated preparation environment under a different OS identity. Its ordinary output
is an opaque receipt, aggregate counts and corpus keep/drop decisions -- never
benchmark labels, raw final hashes or matching snippets.

This module implements the *coding-workspace side* of that boundary: it can create a
receipt only in development mode over synthetic protected fixtures, and it can verify
a receipt that a trusted operator issued. It cannot read protected examples, and a
receipt it verifies carries no benchmark content to leak.

Real protected ingestion and evaluation are completed in prompt 21. Nothing here
constitutes an isolation guarantee; ``verify_receipt`` under ``protected`` policy
checks issuer trust and integrity, which is not the same as an OS-enforced boundary.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

RECEIPT_SCHEMA_VERSION = "1"

# Fields that must never appear in a receipt. Their presence indicates the receipt
# was produced by something that had access to protected content and leaked it.
_FORBIDDEN_FIELDS = frozenset(
    {
        "labels",
        "gold",
        "gold_labels",
        "answers",
        "answer_key",
        "snippets",
        "matched_text",
        "examples",
        "raw_hashes",
        "example_hashes",
    }
)


class ReceiptValidationError(ValueError):
    """Raised when a final exclusion receipt is malformed, unsigned or untrusted."""


class ProtectedAccessError(RuntimeError):
    """Raised when the coding workspace attempts to read protected final data."""


@dataclass
class FinalExclusionReceipt:
    """An opaque receipt binding corpus inputs to a keep/drop decision set.

    The receipt records *which corpus documents to drop* and aggregate counts. It
    does not record what they matched, which benchmark example was involved, or any
    benchmark text or label.
    """

    receipt_id: str
    schema_version: str
    issued_at: str
    issuer_id: str
    mode: str
    corpus_input_digest: str
    exclusion_policy_identity: str
    exclusion_index_identity: str
    output_membership_digest: str
    documents_considered: int
    documents_dropped: int
    dropped_doc_ids: list[str] = field(default_factory=list)
    aggregate_counts: dict[str, int] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    signature: str | None = None
    c05_binding: dict[str, Any] | None = None

    def signing_payload(self) -> str:
        """Canonical, signature-excluding serialization used for signing."""
        body = {
            k: v
            for k, v in asdict(self).items()
            if k != "signature" and not (k == "c05_binding" and v is None)
        }
        return json.dumps(body, sort_keys=True, separators=(",", ":"))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def save(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_text(json.dumps(self.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
        temp.replace(path)
        return path

    @staticmethod
    def load(path: Path) -> FinalExclusionReceipt:
        data = json.loads(path.read_text(encoding="utf-8"))
        unexpected = set(data) - set(FinalExclusionReceipt.__dataclass_fields__)
        if unexpected:
            raise ReceiptValidationError(
                f"receipt contains unexpected fields: {sorted(unexpected)}"
            )
        return FinalExclusionReceipt(**data)


def compute_corpus_input_digest(doc_ids: list[str]) -> str:
    """Digest the corpus membership submitted for exclusion.

    A receipt is only meaningful for the exact corpus batch it was issued against,
    which is also what caps an adaptive membership oracle: the submitted batch is
    fixed before the receipt is requested.
    """
    payload = "|".join(sorted(doc_ids))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def compute_membership_digest(kept_doc_ids: list[str]) -> str:
    """Digest the post-exclusion corpus membership."""
    payload = "|".join(sorted(kept_doc_ids))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def sign_receipt(receipt: FinalExclusionReceipt, issuer_key: bytes) -> FinalExclusionReceipt:
    """Sign a receipt with the issuer's key.

    HMAC establishes that a receipt came from a holder of the issuer key and was not
    altered afterwards. It is an integrity and authenticity check, not a
    confidentiality mechanism and not operational isolation.
    """
    signed = FinalExclusionReceipt(**receipt.to_dict())
    signed.signature = hmac.new(
        issuer_key, receipt.signing_payload().encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return signed


def verify_receipt(
    receipt: FinalExclusionReceipt,
    trusted_issuers: dict[str, bytes],
    policy: str = "protected",
    expected_corpus_digest: str | None = None,
) -> None:
    """Validate a receipt, raising :class:`ReceiptValidationError` on any failure.

    Under the ``protected`` policy every check is mandatory. Under ``development``
    an unsigned receipt is tolerated, but the receipt is then explicitly not a
    final-exclusion claim.
    """
    if receipt.schema_version not in {RECEIPT_SCHEMA_VERSION, "2"}:
        raise ReceiptValidationError(
            f"unsupported receipt schema version '{receipt.schema_version}'; "
            f"expected '{RECEIPT_SCHEMA_VERSION}'"
        )

    for required in ("receipt_id", "issuer_id", "corpus_input_digest", "output_membership_digest"):
        if not getattr(receipt, required):
            raise ReceiptValidationError(f"receipt is malformed: '{required}' is empty")

    if receipt.schema_version == "2":
        binding = receipt.c05_binding
        required_bindings = {
            "plan_digest",
            "completion_digest",
            "benchmark_receipt_digest",
            "source_seals",
            "input_manifest_digest",
            "membership_sha256",
            "policy_digest",
            "index_sha256",
            "review_decisions",
        }
        if binding is None or set(binding) != required_bindings or receipt.dropped_doc_ids:
            raise ReceiptValidationError("v2 requires content-free completion binding")
        for name, expected in {
            "input_manifest_digest": receipt.corpus_input_digest,
            "membership_sha256": receipt.output_membership_digest,
            "policy_digest": receipt.exclusion_policy_identity,
            "index_sha256": receipt.exclusion_index_identity,
        }.items():
            if binding[name] != expected:
                raise ReceiptValidationError("v2 completion binding mismatch")
        if receipt.documents_dropped < 0 or receipt.documents_considered < 0:
            raise ReceiptValidationError("invalid v2 document counts")
    elif receipt.documents_dropped != len(receipt.dropped_doc_ids):
        raise ReceiptValidationError(
            f"receipt is inconsistent: documents_dropped={receipt.documents_dropped} "
            f"but {len(receipt.dropped_doc_ids)} dropped IDs listed"
        )
    if receipt.documents_dropped > receipt.documents_considered:
        raise ReceiptValidationError(
            "receipt is inconsistent: more documents dropped than considered"
        )

    leaked = _FORBIDDEN_FIELDS.intersection(receipt.aggregate_counts)
    if leaked:
        raise ReceiptValidationError(
            f"receipt carries forbidden protected content in aggregate_counts: {sorted(leaked)}"
        )

    if expected_corpus_digest is not None and receipt.corpus_input_digest != expected_corpus_digest:
        raise ReceiptValidationError(
            "receipt is not bound to this corpus batch: corpus_input_digest mismatch"
        )

    if policy == "development":
        return
    if policy != "protected":
        raise ReceiptValidationError(f"unknown receipt policy '{policy}'")

    if receipt.mode != "protected":
        raise ReceiptValidationError(
            f"receipt was issued in '{receipt.mode}' mode and cannot satisfy a "
            "protected-policy check; a development receipt is not a final-exclusion claim"
        )
    if not receipt.signature:
        raise ReceiptValidationError("receipt is unsigned and the policy is 'protected'")

    issuer_key = trusted_issuers.get(receipt.issuer_id)
    if issuer_key is None:
        raise ReceiptValidationError(
            f"issuer '{receipt.issuer_id}' is not in the trusted issuer set"
        )

    expected = hmac.new(
        issuer_key, receipt.signing_payload().encode("utf-8"), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected, receipt.signature):
        raise ReceiptValidationError("receipt signature does not verify; contents were altered")


@dataclass(frozen=True)
class BenchmarkClaimBinding:
    """Expected identities from frozen training/evaluation lineage, not admission.

    The policy and index must cover all benchmark splits in the named suite.
    A trusted operator supplies these expectations independently of the receipt.
    """

    checkpoint_hash: str
    suite_fingerprint: str
    corpus_input_digest: str
    output_membership_digest: str
    exclusion_policy_identity: str
    exclusion_index_identity: str


def verify_benchmark_claim(
    receipt: FinalExclusionReceipt | None,
    binding: BenchmarkClaimBinding | None,
    trusted_issuers: dict[str, bytes],
) -> None:
    """Require protected C05 evidence for the exact frozen training membership.

    Success establishes a screening receipt, never universal zero contamination.
    Acquisition approval, a development receipt and receipt IDs alone cannot pass.
    """
    if receipt is None or binding is None:
        raise ReceiptValidationError("official benchmark claims require a bound C05 receipt")
    for key, value in asdict(binding).items():
        if not value.strip() or value.lower() in {"none_declared", "unknown", "pending"}:
            raise ReceiptValidationError(f"frozen training-pool binding lacks {key}")
    verify_receipt(
        receipt,
        trusted_issuers,
        policy="protected",
        expected_corpus_digest=binding.corpus_input_digest,
    )
    for key in (
        "output_membership_digest",
        "exclusion_policy_identity",
        "exclusion_index_identity",
    ):
        if getattr(receipt, key) != getattr(binding, key):
            raise ReceiptValidationError(f"C05 receipt {key} differs from frozen training pool")


def issue_development_receipt(
    corpus_doc_ids: list[str],
    dropped_doc_ids: list[str],
    exclusion_policy_identity: str,
    exclusion_index_identity: str,
    issuer_id: str = "local_development",
    aggregate_counts: dict[str, int] | None = None,
) -> FinalExclusionReceipt:
    """Issue a development-mode receipt over synthetic protected fixtures.

    Explicitly marked ``mode="development"`` so it can never pass a protected-policy
    check. This is the only receipt the coding workspace may create.
    """
    dropped = sorted(set(dropped_doc_ids))
    kept = sorted(set(corpus_doc_ids) - set(dropped))
    receipt_seed = f"{compute_corpus_input_digest(corpus_doc_ids)}:{exclusion_policy_identity}"

    return FinalExclusionReceipt(
        receipt_id="rcpt_dev_" + hashlib.sha256(receipt_seed.encode("utf-8")).hexdigest()[:20],
        schema_version=RECEIPT_SCHEMA_VERSION,
        issued_at=datetime.now(UTC).isoformat(),
        issuer_id=issuer_id,
        mode="development",
        corpus_input_digest=compute_corpus_input_digest(corpus_doc_ids),
        exclusion_policy_identity=exclusion_policy_identity,
        exclusion_index_identity=exclusion_index_identity,
        output_membership_digest=compute_membership_digest(kept),
        documents_considered=len(set(corpus_doc_ids)),
        documents_dropped=len(dropped),
        dropped_doc_ids=dropped,
        aggregate_counts=aggregate_counts or {},
        notes=[
            "DEVELOPMENT MODE: issued over synthetic protected fixtures in the coding "
            "workspace. This is not a sealed-final exclusion claim.",
            "No protected final examples were read to produce this receipt.",
            "Real protected ingestion and final evaluation are completed in prompt 21.",
        ],
    )


def assert_no_protected_access(path: Path, protected_roots: list[Path]) -> None:
    """Refuse to read a path that lies under a declared protected root.

    A path check is a guard rail inside this process, not an OS security boundary;
    real isolation requires a separate OS identity (EVALUATION_POLICY).
    """
    resolved = path.resolve()
    for root in protected_roots:
        try:
            resolved.relative_to(root.resolve())
        except ValueError:
            continue
        raise ProtectedAccessError(
            f"refusing to read '{resolved}': it lies under protected root '{root}'. "
            "Protected final data is accessible only to the operator-side process."
        )
