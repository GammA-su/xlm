"""Protected final evaluation: requests, receipts and bounded execution (P21).

The development process can build a request but can never execute one: execution
demands operator credentials, a ready sealed root, a non-revoked authorization
bound to the exact request hash, a reviewed scorer bundle, and quota headroom.
Outputs are aggregates only -- per-item predictions never enter a receipt, by
schema construction. Every operator action appends to a hash-chained access log.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from xlm.data.exclusion.receipt import (
    BenchmarkClaimBinding,
    FinalExclusionReceipt,
    ReceiptValidationError,
    verify_benchmark_claim,
)

FINAL_PROTOCOL_VERSION = "1"

# Per-item prediction keys are forbidden in receipts. Anything shaped like
# scored text, labels or choices is dropped at the boundary, loudly.
FORBIDDEN_RECEIPT_KEYS = frozenset(
    {
        "predictions",
        "per_item",
        "items",
        "item_scores",
        "choices",
        "texts",
        "labels",
        "gold",
        "targets",
        "logits",
        "logprobs",
    }
)


class FinalEvaluationError(RuntimeError):
    """Raised when a final request, authorization or receipt is invalid."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _canonical_hash(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class FinalEvaluationRequest:
    """A frozen, bounded final-evaluation request built on the dev side."""

    protocol_version: str
    request_id: str
    checkpoint_hash: str
    suite_fingerprint: str
    task_variants: tuple[str, ...]
    max_items: int
    max_requests: int
    requester: str
    created_at: str
    expires_at: str
    nonce: str

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["task_variants"] = list(self.task_variants)
        return payload

    @property
    def request_hash(self) -> str:
        return _canonical_hash(self.to_dict())

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> FinalEvaluationRequest:
        try:
            return cls(
                protocol_version=str(data["protocol_version"]),
                request_id=str(data["request_id"]),
                checkpoint_hash=str(data["checkpoint_hash"]),
                suite_fingerprint=str(data["suite_fingerprint"]),
                task_variants=tuple(str(t) for t in data["task_variants"]),
                max_items=int(data["max_items"]),
                max_requests=int(data["max_requests"]),
                requester=str(data.get("requester", "")),
                created_at=str(data.get("created_at", "")),
                expires_at=str(data.get("expires_at", "")),
                nonce=str(data.get("nonce", "")),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise FinalEvaluationError(f"malformed final request: {exc}") from exc


def build_final_request(
    checkpoint_hash: str,
    suite_fingerprint: str,
    task_variants: list[str],
    max_items: int,
    max_requests: int,
    requester: str = "developer",
    ttl_seconds: int = 7 * 24 * 3600,
    nonce: str | None = None,
) -> FinalEvaluationRequest:
    """Freeze a bounded request. Unbounded requests are refused, not defaulted."""
    if max_items <= 0 or max_requests <= 0:
        raise FinalEvaluationError("final requests must declare positive item/request bounds")
    if not task_variants:
        raise FinalEvaluationError("final requests must name at least one task variant")
    import time as _time  # noqa: PLC0415 (local import keeps module import light)
    from datetime import timedelta  # noqa: PLC0415

    created = datetime.now(UTC)
    stamp = (
        nonce
        if nonce is not None
        else hashlib.sha256(
            f"{checkpoint_hash}:{suite_fingerprint}:{created.isoformat()}:{_time.time_ns()}".encode()
        ).hexdigest()[:16]
    )
    return FinalEvaluationRequest(
        protocol_version=FINAL_PROTOCOL_VERSION,
        request_id=f"finalreq_{stamp[:12]}",
        checkpoint_hash=checkpoint_hash,
        suite_fingerprint=suite_fingerprint,
        task_variants=tuple(task_variants),
        max_items=max_items,
        max_requests=max_requests,
        requester=requester,
        created_at=created.isoformat(),
        expires_at=(created + timedelta(seconds=ttl_seconds)).isoformat(),
        nonce=stamp,
    )


@dataclass
class OperatorAuthorization:
    """An operator credential binding one request hash to one operator."""

    operator_id: str
    request_hash: str
    ticket: str
    revoked: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def check_operator_environment(credential_path: Path | str | None) -> str:
    """Require operator credentials; the dev process has none by default."""
    if credential_path is None:
        raise FinalEvaluationError(
            "no operator credentials supplied; the development process cannot "
            "execute a protected request"
        )
    path = Path(credential_path)
    if not path.is_file():
        raise FinalEvaluationError(f"operator credential file not found: {path}")
    content = path.read_text(encoding="utf-8").strip()
    if not content:
        raise FinalEvaluationError("operator credential file is empty")
    return content


@dataclass
class AccessLog:
    """Append-only, hash-chained operator action log inside the sealed root."""

    log_path: Path

    def append(self, action: str, details: Mapping[str, Any]) -> dict[str, Any]:
        previous = "genesis"
        if self.log_path.is_file():
            for line in self.log_path.read_text(encoding="utf-8").splitlines():
                try:
                    previous = json.loads(line).get("entry_hash", previous)
                except json.JSONDecodeError:
                    continue
        entry = {
            "action": action,
            "at": _now(),
            "details": dict(details),
            "previous_hash": previous,
        }
        entry["entry_hash"] = _canonical_hash(entry)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, sort_keys=True) + "\n")
        return entry

    def entries(self) -> list[dict[str, Any]]:
        if not self.log_path.is_file():
            return []
        out = []
        for line in self.log_path.read_text(encoding="utf-8").splitlines():
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                out.append(payload)
        return out


def _store_path(sealed_root: Path, name: str) -> Path:
    return sealed_root / name


def load_json_list(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    return [d for d in data] if isinstance(data, list) else []


def save_json_list(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(rows, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


@dataclass(frozen=True)
class FinalEvaluationReceipt:
    """Aggregates-only proof of a protected evaluation. No per-item fields exist."""

    protocol_version: str
    receipt_id: str
    request_hash: str
    checkpoint_hash: str
    suite_fingerprint: str
    task_aggregates: dict[str, dict[str, float]]
    items_scored: int
    scorer_bundle_hash: str
    model_code_hash: str
    context_policy: str
    precision: str
    exposure_classification: str
    executed_at: str
    executed_by: str

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["task_aggregates"] = {k: dict(v) for k, v in self.task_aggregates.items()}
        return payload

    @property
    def receipt_hash(self) -> str:
        return _canonical_hash(self.to_dict())


def sanitize_aggregates(raw: Mapping[str, Any]) -> dict[str, dict[str, float]]:
    """Keep means and counts; drop anything shaped like per-item output, loudly."""
    dropped = sorted(set(raw) & FORBIDDEN_RECEIPT_KEYS)
    if dropped:
        raise FinalEvaluationError(
            f"receipt outputs must be aggregates only; forbidden keys present: {dropped}"
        )
    aggregates: dict[str, dict[str, float]] = {}
    for task, metrics in raw.items():
        if not isinstance(metrics, Mapping):
            raise FinalEvaluationError(f"task '{task}' aggregates must be a mapping")
        clean: dict[str, float] = {}
        for name, value in metrics.items():
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise FinalEvaluationError(
                    f"task '{task}' metric '{name}' is not a scalar aggregate"
                )
            clean[str(name)] = float(value)
        aggregates[str(task)] = clean
    return aggregates


def execute_final_request(
    request: FinalEvaluationRequest,
    authorization: OperatorAuthorization,
    sealed_root: Path | str,
    scorer_bundle_path: Path | str,
    reviewed_bundles_path: Path | str,
    quota_path: Path | str,
    aggregate_supplier: Any,
    operator_id: str,
    model_code_hash: str,
    context_policy: str,
    precision: str,
) -> FinalEvaluationReceipt:
    """Execute one protected request under every bound. Returns aggregates only.

    `aggregate_supplier` is called as ``supplier(request) -> (aggregates, items)``
    and MUST return pre-aggregated task means; per-item material is accepted by
    the signature but never recorded. Arbitrary candidate code is not a secure
    scorer merely because it runs here: the scorer bundle hash must match the
    reviewed registry, and sandbox limits (no network, read-only inputs, bounded
    items, aggregates-only outputs) are documented constraints, not proofs.
    """
    root = Path(sealed_root)
    if request.protocol_version != FINAL_PROTOCOL_VERSION:
        raise FinalEvaluationError(
            f"protocol '{request.protocol_version}' unsupported here "
            f"(supports '{FINAL_PROTOCOL_VERSION}')"
        )
    if not authorization.operator_id or authorization.revoked:
        raise FinalEvaluationError("authorization missing, empty operator, or revoked")
    if authorization.request_hash != request.request_hash:
        raise FinalEvaluationError("authorization does not match this exact request hash")
    if authorization.operator_id != operator_id:
        raise FinalEvaluationError("executing operator does not match the authorization")

    consumed_path = _store_path(root, "consumed_requests.json")
    consumed = load_json_list(consumed_path)
    if any(row.get("request_hash") == request.request_hash for row in consumed):
        raise FinalEvaluationError("request already executed; replays are refused")
    try:
        expires = datetime.fromisoformat(request.expires_at)
    except ValueError as exc:
        raise FinalEvaluationError(f"request expiry unreadable: {exc}") from exc
    if datetime.now(UTC) > expires:
        raise FinalEvaluationError("request expired")

    # Reviewed-bundle boundary: only pinned scorer code may run.
    bundle_path = Path(scorer_bundle_path)
    if not bundle_path.is_file():
        raise FinalEvaluationError(f"scorer bundle not found: {bundle_path}")
    bundle_hash = hashlib.sha256(bundle_path.read_bytes()).hexdigest()
    reviewed: dict[str, str] = {}
    reviewed_path = Path(reviewed_bundles_path)
    if reviewed_path.is_file():
        try:
            reviewed = json.loads(reviewed_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            reviewed = {}
    if bundle_hash not in set(reviewed.values()):
        raise FinalEvaluationError(
            "scorer bundle hash is not in the reviewed registry; arbitrary candidate "
            "code is refused even inside the operator path"
        )

    # Quota: bounded items per request and per checkpoint, tracked durably.
    quota_file = Path(quota_path)
    quota = load_json_list(quota_file)
    used_requests = sum(1 for r in quota if r.get("checkpoint") == request.checkpoint_hash)
    if request.max_items <= 0 or request.max_requests <= 0:
        raise FinalEvaluationError("request bounds must stay positive at execution")
    if used_requests + 1 > request.max_requests:
        raise FinalEvaluationError("checkpoint request quota exhausted")
    quota_file.parent.mkdir(parents=True, exist_ok=True)

    # The supplier returns pre-aggregated task means plus a declared item count.
    # Per-item material is never accepted, counted, or recorded: the signature
    # takes aggregates only, so a scorer cannot smuggle predictions inside.
    supplied = aggregate_supplier(request)
    if not isinstance(supplied, tuple) or len(supplied) != 2:
        raise FinalEvaluationError("aggregate supplier must return (aggregates, items_scored)")
    raw_aggregates, items_scored = supplied
    aggregates = sanitize_aggregates(raw_aggregates)
    if not isinstance(items_scored, int) or not 0 < items_scored <= request.max_items:
        raise FinalEvaluationError(
            f"declared scored items {items_scored!r} outside (0, {request.max_items}]"
        )

    receipt = FinalEvaluationReceipt(
        protocol_version=FINAL_PROTOCOL_VERSION,
        receipt_id=f"finalrcpt_{request.request_hash[:12]}",
        request_hash=request.request_hash,
        checkpoint_hash=request.checkpoint_hash,
        suite_fingerprint=request.suite_fingerprint,
        task_aggregates=aggregates,
        items_scored=items_scored,
        scorer_bundle_hash=bundle_hash,
        model_code_hash=model_code_hash,
        context_policy=context_policy,
        precision=precision,
        exposure_classification="final-protected",
        executed_at=_now(),
        executed_by=operator_id,
    )
    consumed.append(
        {
            "request_hash": request.request_hash,
            "receipt_id": receipt.receipt_id,
            "at": receipt.executed_at,
        }
    )
    save_json_list(consumed_path, consumed)
    quota.append(
        {"checkpoint": request.checkpoint_hash, "items": items_scored, "at": receipt.executed_at}
    )
    save_json_list(quota_file, quota)

    receipts_path = _store_path(root, "receipts.json")
    receipts = load_json_list(receipts_path)
    receipts.append(receipt.to_dict())
    save_json_list(receipts_path, receipts)

    AccessLog(_store_path(root, "access.log.jsonl")).append(
        "final_execute",
        {
            "request_hash": request.request_hash,
            "receipt_id": receipt.receipt_id,
            "operator": operator_id,
            "items": items_scored,
        },
    )
    return receipt


def verify_receipt(
    receipt: Mapping[str, Any],
    sealed_root: Path | str,
    *,
    exclusion_receipt: FinalExclusionReceipt | None = None,
    training_pool: BenchmarkClaimBinding | None = None,
    trusted_exclusion_issuers: dict[str, bytes] | None = None,
) -> dict[str, Any]:
    """Verify evaluation integrity and separately gate official benchmark claims.

    ``valid`` establishes evaluation receipt integrity only. C04 admission and
    final-protected exposure never establish training-corpus cleanliness.
    """
    root = Path(sealed_root)
    findings: list[str] = []
    ok = True

    required = (
        "receipt_id",
        "request_hash",
        "checkpoint_hash",
        "suite_fingerprint",
        "task_aggregates",
        "items_scored",
        "scorer_bundle_hash",
        "exposure_classification",
    )
    for key in required:
        if key not in receipt:
            ok = False
            findings.append(f"missing required field '{key}'")

    forbidden = sorted(set(receipt) & FORBIDDEN_RECEIPT_KEYS)
    if forbidden:
        ok = False
        findings.append(f"receipt carries forbidden per-item fields: {forbidden}")

    receipts = load_json_list(_store_path(root, "receipts.json"))
    stored = [r for r in receipts if r.get("receipt_id") == receipt.get("receipt_id")]
    if not stored:
        ok = False
        findings.append("receipt id unknown to the sealed registry")
    elif stored[0].get("request_hash") != receipt.get("request_hash"):
        ok = False
        findings.append("receipt request hash does not match the sealed record")
    elif _canonical_hash(stored[0]) != _canonical_hash(dict(receipt)):
        ok = False
        findings.append("presented receipt differs from the sealed record (altered)")

    consumed = load_json_list(_store_path(root, "consumed_requests.json"))
    if not any(row.get("request_hash") == receipt.get("request_hash") for row in consumed):
        ok = False
        findings.append("request hash was never consumed by a recorded execution")

    claim_findings: list[str] = []
    try:
        verify_benchmark_claim(exclusion_receipt, training_pool, trusted_exclusion_issuers or {})
        if training_pool is None:
            raise ReceiptValidationError("frozen training-pool binding is missing")
        if training_pool.checkpoint_hash != receipt.get(
            "checkpoint_hash"
        ) or training_pool.suite_fingerprint != receipt.get("suite_fingerprint"):
            raise ReceiptValidationError(
                "C05 frozen lineage differs from evaluation checkpoint/suite"
            )
    except ReceiptValidationError as exc:
        claim_findings.append(str(exc))
    claim_allowed = ok and not claim_findings
    return {
        "receipt_id": receipt.get("receipt_id"),
        "valid": ok,
        "findings": findings,
        "official_benchmark_claims_allowed": claim_allowed,
        "benchmark_contamination_status": (
            "screened_with_limitations" if claim_allowed else "possibly_contaminated"
        ),
        "benchmark_claim_findings": claim_findings,
        "zero_contamination_proven": False,
    }


@dataclass(frozen=True)
class DecontaminationReceipt:
    """Opaque proof of an exclusion pass: counts only, never matched text."""

    protocol_version: str
    receipt_id: str
    corpus_batch_hash: str
    method: str
    kept_documents: int
    excluded_documents: int
    operator_id: str
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def prepare_exclusion_receipt(
    corpus_batch_hash: str,
    method: str,
    kept_documents: int,
    excluded_documents: int,
    operator_id: str,
) -> DecontaminationReceipt:
    """Issue a decontamination receipt recording counts, not content."""
    if kept_documents < 0 or excluded_documents < 0:
        raise FinalEvaluationError("document counts cannot be negative")
    stamp = _canonical_hash(
        {
            "corpus": corpus_batch_hash,
            "method": method,
            "kept": kept_documents,
            "excluded": excluded_documents,
            "operator": operator_id,
            "at": _now(),
        }
    )
    return DecontaminationReceipt(
        protocol_version=FINAL_PROTOCOL_VERSION,
        receipt_id=f"decon_{stamp[:12]}",
        corpus_batch_hash=corpus_batch_hash,
        method=method,
        kept_documents=kept_documents,
        excluded_documents=excluded_documents,
        operator_id=operator_id,
        created_at=_now(),
    )
