"""Frozen local LM validation inventories and native text CE/BPB scoring (P35 §I/§J).

Primary held-out quality is **text cross-entropy in nats per scored text
token**: BOS is unscored context, the structural EOS target and padding are
excluded. Per domain, the sufficient statistics are summed NLL, scored text
tokens and scored canonical UTF-8 bytes; every ratio is formed from sums, never
as a mean of per-document means. Domains are combined with fixed equal weights
(``equal_domain_v1``) over the frozen required domain set. A missing or empty
domain fails the evaluation; nothing renormalizes around it.

The inventory is an immutable local manifest pinned by content identity: every
documents file is verified by size and SHA-256, membership by declared
document ids, and byte coverage by the declared canonical byte total. Nothing
is downloaded and no directory is enumerated.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from xlm.artifacts.manifest import identity_digest
from xlm.data.normalization import canonical_normalize
from xlm.evaluation.cadence import EventTier
from xlm.evaluation.outcome import (
    EvaluationContext,
    EvaluationOutcome,
    InvalidMetricError,
    MissingDomainError,
)
from xlm.evaluation.receipts import AttemptOutcome

MANIFEST_VERSION = "xlm-lm-validation-v1"
SCORING_POLICY_VERSION = "xlm-lm-scoring-v1"
EVALUATOR_VERSION = "xlm-native-lm-validation-v1"
NORMALIZATION = "canonical_nfc_newline_v1"
DOMAIN_WEIGHTING = "equal_domain_v1"
PRIMARY_METRIC = "equal_domain_text_ce_nats_per_token"
SPLITS = ("diagnostic_val", "lm_confirmation")
SUBSETS = ("full", "quick")
SCOPE_KINDS = ("authored_fixture", "frozen_validation_inventory")
FORWARD_PRECISIONS = ("fp32", "bf16_autocast")
LOGPROB_DTYPES = ("fp32", "fp64")
MAX_DOCUMENTS_FILE_BYTES = 256 * 1024**2
MAX_DOCUMENTS = 1_000_000
_LN2 = math.log(2.0)


class ValidationManifestError(ValueError):
    """A validation manifest is malformed, unpinned or disagrees with its files."""


@dataclass(frozen=True)
class ValidationDomain:
    """One required domain: grouped source aliases and its frozen documents file."""

    domain_id: str
    source_ids: tuple[str, ...]
    documents_file: str
    content_sha256: str
    content_bytes: int
    document_ids: tuple[str, ...]
    text_utf8_bytes: int

    def identity(self) -> dict[str, Any]:
        return {
            "domain_id": self.domain_id,
            "source_ids": sorted(self.source_ids),
            "content_sha256": self.content_sha256,
            "content_bytes": self.content_bytes,
            "document_ids": sorted(self.document_ids),
            "text_utf8_bytes": self.text_utf8_bytes,
        }


@dataclass(frozen=True)
class ValidationManifest:
    """Declared, immutable LM validation inventory."""

    split: str
    subset: str
    scope_kind: str
    tokenizer_fingerprint: str
    domains: tuple[ValidationDomain, ...]
    nested_in: str | None = None
    normalization: str = NORMALIZATION
    domain_weighting: str = DOMAIN_WEIGHTING
    manifest_version: str = MANIFEST_VERSION
    notes: tuple[str, ...] = ()
    source_path: Path | None = field(default=None, compare=False)

    def __post_init__(self) -> None:
        if self.manifest_version != MANIFEST_VERSION:
            raise ValidationManifestError(f"unsupported manifest version '{self.manifest_version}'")
        for name, value, allowed in (
            ("split", self.split, SPLITS),
            ("subset", self.subset, SUBSETS),
            ("scope_kind", self.scope_kind, SCOPE_KINDS),
            ("normalization", self.normalization, (NORMALIZATION,)),
            ("domain_weighting", self.domain_weighting, (DOMAIN_WEIGHTING,)),
        ):
            if value not in allowed:
                raise ValidationManifestError(f"{name} '{value}' is not one of {list(allowed)}")
        if (self.subset == "quick") != (self.nested_in is not None):
            raise ValidationManifestError("a quick subset (and only it) names its nesting manifest")
        if not self.tokenizer_fingerprint:
            raise ValidationManifestError("the manifest must bind a tokenizer fingerprint")
        if not self.domains:
            raise ValidationManifestError("a validation manifest requires at least one domain")
        seen_domains: set[str] = set()
        seen_sources: set[str] = set()
        seen_docs: set[str] = set()
        for domain in self.domains:
            if domain.domain_id in seen_domains:
                raise ValidationManifestError(f"duplicate domain '{domain.domain_id}'")
            seen_domains.add(domain.domain_id)
            if not domain.source_ids:
                raise ValidationManifestError(f"domain '{domain.domain_id}' names no source")
            overlap = seen_sources.intersection(domain.source_ids)
            if overlap:
                raise ValidationManifestError(f"sources {sorted(overlap)} appear in two domains")
            seen_sources.update(domain.source_ids)
            if not domain.document_ids:
                raise ValidationManifestError(
                    f"domain '{domain.domain_id}' declares no documents; an empty required "
                    "domain cannot be scored and is never silently dropped"
                )
            docs = set(domain.document_ids)
            if len(docs) != len(domain.document_ids) or docs & seen_docs:
                raise ValidationManifestError(
                    f"domain '{domain.domain_id}' repeats a document id within or across domains"
                )
            seen_docs.update(docs)
            if len(domain.content_sha256) != 64 or domain.content_bytes < 0:
                raise ValidationManifestError(f"domain '{domain.domain_id}' has an invalid digest")
            if domain.text_utf8_bytes <= 0:
                raise ValidationManifestError(f"domain '{domain.domain_id}' declares no text bytes")
        if len(seen_docs) > MAX_DOCUMENTS:
            raise ValidationManifestError(f"manifest exceeds {MAX_DOCUMENTS} documents")

    @property
    def domain_ids(self) -> tuple[str, ...]:
        return tuple(d.domain_id for d in self.domains)

    def document_ids(self) -> set[str]:
        return {doc for d in self.domains for doc in d.document_ids}

    def identity(self) -> dict[str, Any]:
        return {
            "manifest_version": self.manifest_version,
            "split": self.split,
            "subset": self.subset,
            "scope_kind": self.scope_kind,
            "tokenizer_fingerprint": self.tokenizer_fingerprint,
            "normalization": self.normalization,
            "domain_weighting": self.domain_weighting,
            "nested_in": self.nested_in,
            "domains": [d.identity() for d in sorted(self.domains, key=lambda d: d.domain_id)],
        }

    def manifest_id(self) -> str:
        """Content identity; independent of file location and note text."""
        return identity_digest(self.identity())

    def to_dict(self) -> dict[str, Any]:
        return {
            "manifest_version": self.manifest_version,
            "manifest_id": self.manifest_id(),
            "split": self.split,
            "subset": self.subset,
            "scope_kind": self.scope_kind,
            "tokenizer_fingerprint": self.tokenizer_fingerprint,
            "normalization": self.normalization,
            "domain_weighting": self.domain_weighting,
            "nested_in": self.nested_in,
            "notes": list(self.notes),
            "domains": [
                {
                    "domain_id": d.domain_id,
                    "source_ids": list(d.source_ids),
                    "documents_file": d.documents_file,
                    "content_sha256": d.content_sha256,
                    "content_bytes": d.content_bytes,
                    "document_ids": list(d.document_ids),
                    "text_utf8_bytes": d.text_utf8_bytes,
                }
                for d in self.domains
            ],
        }

    @classmethod
    def from_dict(
        cls, payload: Mapping[str, Any], source_path: Path | None = None
    ) -> ValidationManifest:
        try:
            domains = tuple(
                ValidationDomain(
                    domain_id=str(entry["domain_id"]),
                    source_ids=tuple(str(s) for s in entry["source_ids"]),
                    documents_file=str(entry["documents_file"]),
                    content_sha256=str(entry["content_sha256"]),
                    content_bytes=int(entry["content_bytes"]),
                    document_ids=tuple(str(d) for d in entry["document_ids"]),
                    text_utf8_bytes=int(entry["text_utf8_bytes"]),
                )
                for entry in payload["domains"]
            )
            manifest = cls(
                split=str(payload["split"]),
                subset=str(payload["subset"]),
                scope_kind=str(payload["scope_kind"]),
                tokenizer_fingerprint=str(payload["tokenizer_fingerprint"]),
                domains=domains,
                nested_in=payload.get("nested_in"),
                normalization=str(payload["normalization"]),
                domain_weighting=str(payload["domain_weighting"]),
                manifest_version=str(payload["manifest_version"]),
                notes=tuple(str(n) for n in payload.get("notes", ())),
                source_path=source_path,
            )
        except (KeyError, TypeError) as exc:
            raise ValidationManifestError(f"malformed validation manifest: {exc}") from exc
        declared = payload.get("manifest_id")
        if declared is not None and declared != manifest.manifest_id():
            raise ValidationManifestError("manifest_id does not match the declared content")
        return manifest


def load_validation_manifest(path: Path | str) -> ValidationManifest:
    manifest_path = Path(path)
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise ValidationManifestError(f"validation manifest not found: {manifest_path}")
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValidationManifestError(f"{manifest_path}: manifest must be an object")
    return ValidationManifest.from_dict(payload, source_path=manifest_path)


def save_validation_manifest(manifest: ValidationManifest, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(json.dumps(manifest.to_dict(), indent=2, sort_keys=True).encode("utf-8"))
    tmp.replace(path)
    return path


def _read_documents(path: Path) -> list[dict[str, str]]:
    if path.is_symlink() or not path.is_file():
        raise ValidationManifestError(f"documents file is not a regular file: {path}")
    if path.stat().st_size > MAX_DOCUMENTS_FILE_BYTES:
        raise ValidationManifestError(f"documents file exceeds its byte bound: {path}")
    records: list[dict[str, str]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
        if not line.strip():
            continue
        record = json.loads(line)
        if not isinstance(record, dict) or set(record) - {"doc_id", "text", "source_id"}:
            raise ValidationManifestError(f"{path}:{number + 1}: unexpected record shape")
        if not isinstance(record.get("doc_id"), str) or not isinstance(record.get("text"), str):
            raise ValidationManifestError(f"{path}:{number + 1}: doc_id and text must be strings")
        records.append(record)
    return records


def build_validation_manifest(
    *,
    split: str,
    subset: str,
    scope_kind: str,
    tokenizer_fingerprint: str,
    domains: Sequence[Mapping[str, Any]],
    base_dir: Path,
    nested_in: str | None = None,
    notes: Sequence[str] = (),
) -> ValidationManifest:
    """Derive digests, membership and byte totals from prepared local files."""
    built: list[ValidationDomain] = []
    for entry in domains:
        relative = str(entry["documents_file"])
        path = (base_dir / relative).resolve()
        records = _read_documents(path)
        data = path.read_bytes()
        built.append(
            ValidationDomain(
                domain_id=str(entry["domain_id"]),
                source_ids=tuple(str(s) for s in entry["source_ids"]),
                documents_file=relative,
                content_sha256=hashlib.sha256(data).hexdigest(),
                content_bytes=len(data),
                document_ids=tuple(r["doc_id"] for r in records),
                text_utf8_bytes=sum(len(canonical_normalize(r["text"]).encode()) for r in records),
            )
        )
    return ValidationManifest(
        split=split,
        subset=subset,
        scope_kind=scope_kind,
        tokenizer_fingerprint=tokenizer_fingerprint,
        domains=tuple(built),
        nested_in=nested_in,
        notes=tuple(notes),
    )


@dataclass(frozen=True)
class VerifiedDomain:
    domain_id: str
    source_ids: tuple[str, ...]
    documents: tuple[tuple[str, str], ...]
    text_utf8_bytes: int


@dataclass(frozen=True)
class ValidationInventory:
    """A manifest whose files, membership and byte coverage were verified."""

    manifest: ValidationManifest
    manifest_id: str
    domains: tuple[VerifiedDomain, ...]

    @property
    def domain_ids(self) -> tuple[str, ...]:
        return tuple(d.domain_id for d in self.domains)

    def summary(self) -> dict[str, Any]:
        return {
            "manifest_id": self.manifest_id,
            "split": self.manifest.split,
            "subset": self.manifest.subset,
            "scope_kind": self.manifest.scope_kind,
            "nested_in": self.manifest.nested_in,
            "domains": {
                d.domain_id: {
                    "source_ids": list(d.source_ids),
                    "documents": len(d.documents),
                    "text_utf8_bytes": d.text_utf8_bytes,
                }
                for d in self.domains
            },
        }


def verify_validation_manifest(
    manifest: ValidationManifest,
    *,
    tokenizer_fingerprint: str,
    expected_manifest_id: str,
    base_dir: Path | None = None,
) -> ValidationInventory:
    """Verify pinned identity, tokenizer, file bytes, membership and byte coverage."""
    manifest_id = manifest.manifest_id()
    if manifest_id != expected_manifest_id:
        raise ValidationManifestError(
            f"validation manifest identity {manifest_id} differs from the pinned "
            f"{expected_manifest_id}; an unpinned or 'latest' inventory is never accepted"
        )
    if manifest.tokenizer_fingerprint != tokenizer_fingerprint:
        raise ValidationManifestError(
            "validation manifest was built for a different tokenizer than the evaluated model"
        )
    root = base_dir or (manifest.source_path.parent if manifest.source_path else None)
    if root is None:
        raise ValidationManifestError("a manifest without a location needs an explicit base_dir")
    verified: list[VerifiedDomain] = []
    for domain in manifest.domains:
        path = (root / domain.documents_file).resolve()
        records = _read_documents(path)
        data = path.read_bytes()
        if len(data) != domain.content_bytes or hashlib.sha256(data).hexdigest() != (
            domain.content_sha256
        ):
            raise ValidationManifestError(
                f"domain '{domain.domain_id}': documents file bytes differ from the manifest"
            )
        present = [r["doc_id"] for r in records]
        if len(set(present)) != len(present) or set(present) != set(domain.document_ids):
            raise ValidationManifestError(
                f"domain '{domain.domain_id}': document membership differs from the manifest"
            )
        texts: list[tuple[str, str]] = []
        total = 0
        for record in records:
            source = record.get("source_id")
            if source is not None and source not in domain.source_ids:
                raise ValidationManifestError(
                    f"document '{record['doc_id']}' names source '{source}' outside its domain"
                )
            clean = canonical_normalize(record["text"])
            if not clean:
                raise ValidationManifestError(
                    f"document '{record['doc_id']}' is empty after normalization"
                )
            total += len(clean.encode("utf-8"))
            texts.append((record["doc_id"], record["text"]))
        if total != domain.text_utf8_bytes:
            raise ValidationManifestError(
                f"domain '{domain.domain_id}': canonical text is {total} bytes, "
                f"manifest declares {domain.text_utf8_bytes}"
            )
        texts.sort()
        verified.append(
            VerifiedDomain(domain.domain_id, tuple(domain.source_ids), tuple(texts), total)
        )
    return ValidationInventory(manifest, manifest_id, tuple(verified))


def load_pinned_inventory(
    path: Path | str, *, manifest_id: str, tokenizer_fingerprint: str
) -> ValidationInventory:
    return verify_validation_manifest(
        load_validation_manifest(path),
        tokenizer_fingerprint=tokenizer_fingerprint,
        expected_manifest_id=manifest_id,
    )


@dataclass(frozen=True)
class LMScoringPolicy:
    """Frozen evaluation-time inference and scoring rules."""

    context_length: int
    rolling_stride: int
    forward_precision: str = "fp32"
    logprob_dtype: str = "fp64"

    def __post_init__(self) -> None:
        if self.forward_precision not in FORWARD_PRECISIONS:
            raise ValueError(f"forward_precision must be one of {list(FORWARD_PRECISIONS)}")
        if self.logprob_dtype not in LOGPROB_DTYPES:
            raise ValueError(f"logprob_dtype must be one of {list(LOGPROB_DTYPES)}")
        if self.context_length < 2:
            raise ValueError("scoring context must hold at least two tokens")
        if not 1 <= self.rolling_stride <= self.context_length - 1:
            # A stride of the full context would leave window-boundary targets
            # unscored; the native document scorer does not detect that itself.
            raise ValueError("rolling_stride must lie in [1, context_length - 1]")

    def identity(self) -> dict[str, Any]:
        return {
            "version": SCORING_POLICY_VERSION,
            "forward_precision": self.forward_precision,
            "logprob_dtype": self.logprob_dtype,
            "context_length": self.context_length,
            "rolling_stride": self.rolling_stride,
            "window": "rolling_each_target_once_max_context_v1",
            "position_ids": "absolute_document_positions_v1",
            "bos": "bos_unscored_context_v1",
            "eos": "structural_eos_excluded_reported_separately_v1",
            "padding": "none_one_document_per_forward_v1",
            "normalization": NORMALIZATION,
            "byte_unit": "canonical_utf8_bytes",
        }

    def digest(self) -> str:
        return identity_digest(self.identity())


@dataclass
class DomainStatistics:
    """Sufficient statistics of one domain; ratios are only formed from these sums."""

    documents: int = 0
    text_nll_nats: float = 0.0
    text_tokens: int = 0
    text_utf8_bytes: int = 0
    eos_nll_nats: float = 0.0
    eos_targets: int = 0

    def add(self, document: Mapping[str, Any]) -> None:
        self.documents += 1
        self.text_nll_nats += float(document["text_nll_nats"])
        self.text_tokens += int(document["text_tokens"])
        self.text_utf8_bytes += int(document["text_utf8_bytes"])
        self.eos_nll_nats += float(document["eos_nll_nats"])
        self.eos_targets += int(document["eos_targets"])

    def metrics(self) -> dict[str, Any]:
        ce = self.text_nll_nats / self.text_tokens
        eos_ce = (self.text_nll_nats + self.eos_nll_nats) / (self.text_tokens + self.eos_targets)
        return {
            "documents": self.documents,
            "text_nll_nats": self.text_nll_nats,
            "text_tokens": self.text_tokens,
            "text_utf8_bytes": self.text_utf8_bytes,
            "eos_nll_nats": self.eos_nll_nats,
            "eos_targets": self.eos_targets,
            "text_ce_nats_per_token": ce,
            "text_bpb": self.text_nll_nats / (_LN2 * self.text_utf8_bytes),
            "text_token_perplexity": math.exp(ce),
            "eos_inclusive_ce_nats_per_token_diagnostic": eos_ce,
        }


def aggregate_domain_metrics(
    domains: Mapping[str, DomainStatistics], required: Sequence[str]
) -> dict[str, Any]:
    """Combine per-domain sufficient statistics with fixed equal domain weights.

    Missing, extra or empty domains raise: the weights are frozen over the
    required set and are never renormalized around an absent domain.
    """
    missing = [d for d in required if d not in domains]
    if missing:
        raise MissingDomainError(f"required validation domains produced no result: {missing}")
    extra = sorted(set(domains) - set(required))
    if extra:
        raise MissingDomainError(f"unexpected validation domains {extra} outside the frozen set")
    empty = [d for d in required if domains[d].text_tokens <= 0 or domains[d].text_utf8_bytes <= 0]
    if empty:
        raise MissingDomainError(f"required validation domains scored nothing: {empty}")
    per_domain = {d: domains[d].metrics() for d in required}
    for name, values in per_domain.items():
        if values["text_nll_nats"] < 0 or not math.isfinite(values["text_nll_nats"]):
            raise InvalidMetricError(f"domain '{name}' text NLL is invalid")
    weight = 1.0 / len(required)
    total_nll = sum(domains[d].text_nll_nats for d in required)
    total_tokens = sum(domains[d].text_tokens for d in required)
    total_bytes = sum(domains[d].text_utf8_bytes for d in required)
    total_eos_nll = sum(domains[d].eos_nll_nats for d in required)
    total_eos = sum(domains[d].eos_targets for d in required)
    primary = sum(per_domain[d]["text_ce_nats_per_token"] for d in required) * weight
    return {
        "primary_metric": PRIMARY_METRIC,
        PRIMARY_METRIC: primary,
        "equal_domain_text_bpb": sum(per_domain[d]["text_bpb"] for d in required) * weight,
        "micro_text_ce_nats_per_token": total_nll / total_tokens,
        "micro_text_bpb": total_nll / (_LN2 * total_bytes),
        "micro_eos_inclusive_ce_nats_per_token_diagnostic": (total_nll + total_eos_nll)
        / (total_tokens + total_eos),
        "domain_weighting": DOMAIN_WEIGHTING,
        "domain_weights": {d: weight for d in required},
        "domains": per_domain,
        "units": {
            "ce": "nats per scored text token (BOS/EOS/padding excluded)",
            "bpb": "bits per canonical UTF-8 text byte",
        },
    }


def _module_source_digest() -> str:
    from xlm.evaluation import likelihood

    digest = hashlib.sha256()
    for path in (Path(__file__), Path(likelihood.__file__)):
        digest.update(path.name.encode() + b"\0" + path.read_bytes())
    return digest.hexdigest()


class LMValidationEvaluator:
    """Native text CE/BPB over one verified inventory, for one event tier."""

    def __init__(
        self,
        tier: EventTier,
        inventory: ValidationInventory,
        tokenizer: Any,
        policy: LMScoringPolicy,
    ) -> None:
        if tier is EventTier.SEARCH_BENCHMARK:
            raise ValueError("LM validation is not a benchmark tier")
        expected_split = (
            "lm_confirmation" if tier is EventTier.ENDPOINT_CONFIRMATION else ("diagnostic_val")
        )
        if inventory.manifest.split != expected_split:
            raise ValidationManifestError(
                f"{tier.value} requires a '{expected_split}' inventory, "
                f"got '{inventory.manifest.split}'"
            )
        if tier is EventTier.QUICK_LM and inventory.manifest.subset != "quick":
            raise ValidationManifestError("quick_lm requires the nested quick subset")
        if tier is not EventTier.QUICK_LM and inventory.manifest.subset != "full":
            raise ValidationManifestError(f"{tier.value} requires a full validation inventory")
        if tokenizer.fingerprint != inventory.manifest.tokenizer_fingerprint:
            raise ValidationManifestError("tokenizer differs from the inventory's tokenizer")
        self.tier: EventTier = tier
        self.inventory = inventory
        self.tokenizer = tokenizer
        self.policy = policy
        self._source_digest = _module_source_digest()

    def identity(self) -> dict[str, Any]:
        return {
            "implementation": "xlm.evaluation.lm_validation.LMValidationEvaluator",
            "version": EVALUATOR_VERSION,
            "source_digest": self._source_digest,
            "tokenizer": self.tokenizer.fingerprint,
            "inputs": self.inventory.summary(),
            "scoring_policy": self.policy.identity(),
            "domain_weighting": DOMAIN_WEIGHTING,
            "primary_metric": PRIMARY_METRIC,
        }

    def _score(self, scorer: Any, doc_id: str, text: str, device: str) -> dict[str, Any]:
        import torch

        autocast: Any = contextlib.nullcontext()
        if self.policy.forward_precision == "bf16_autocast":
            if device != "cuda":
                raise InvalidMetricError("bf16_autocast scoring requires a CUDA device")
            autocast = torch.autocast(device_type="cuda", dtype=torch.bfloat16)
        with autocast:
            result = scorer.score_document(text, doc_id=doc_id)
        tokens = result.text_token_count
        scored = result.diagnostics.get("scored_text_targets")
        if tokens <= 0 or scored != tokens or not result.diagnostics.get("eos_target_scored"):
            raise InvalidMetricError(
                f"document '{doc_id}' was not scored completely ({scored}/{tokens} text targets)"
            )
        expected_bytes = len(canonical_normalize(text).encode("utf-8"))
        if result.scored_canonical_utf8_bytes != expected_bytes:
            raise InvalidMetricError(f"document '{doc_id}' byte accounting disagrees")
        eos_nll = float(result.diagnostics["eos_nll"])
        for value in (result.text_token_nll_sum, eos_nll):
            if not math.isfinite(value) or value < 0:
                raise InvalidMetricError(f"document '{doc_id}' produced an invalid NLL {value!r}")
        return {
            "text_nll_nats": float(result.text_token_nll_sum),
            "text_tokens": tokens,
            "text_utf8_bytes": expected_bytes,
            "eos_nll_nats": eos_nll,
            "eos_targets": 1,
        }

    def evaluate(self, model: Any, *, device: str, context: EvaluationContext) -> EvaluationOutcome:
        from xlm.evaluation.likelihood import ConditionalLikelihoodScorer, WindowTruncationPolicy

        model_context = getattr(getattr(model, "config", None), "context_length", None)
        if model_context is not None and model_context != self.policy.context_length:
            raise InvalidMetricError("scoring policy context differs from the model context")
        scorer = ConditionalLikelihoodScorer(
            model=model,
            tokenizer=self.tokenizer,
            device=device,
            precision=self.policy.logprob_dtype,
            window_policy=WindowTruncationPolicy.ROLLING,
            max_context_length=self.policy.context_length,
            stride=self.policy.rolling_stride,
        )
        scoring_key = identity_digest(
            {"policy": self.policy.identity(), "tokenizer": self.tokenizer.fingerprint}
        )
        statistics: dict[str, DomainStatistics] = {}
        reused = 0
        for domain in self.inventory.domains:
            stats = DomainStatistics()
            for doc_id, text in domain.documents:
                key = (
                    scoring_key,
                    hashlib.sha256(canonical_normalize(text).encode("utf-8")).hexdigest(),
                )
                document = context.document_cache.get(key)
                if document is None:
                    document = self._score(scorer, doc_id, text, device)
                    context.document_cache[key] = document
                else:
                    reused += 1
                stats.add(document)
            if stats.text_utf8_bytes != domain.text_utf8_bytes:
                raise InvalidMetricError(f"domain '{domain.domain_id}' byte coverage is incomplete")
            statistics[domain.domain_id] = stats
        metrics = aggregate_domain_metrics(statistics, self.inventory.domain_ids)
        coverage = {
            "kind": "lm_validation",
            "manifest_id": self.inventory.manifest_id,
            "complete": True,
            "required_domains": list(self.inventory.domain_ids),
            "documents_scored": sum(s.documents for s in statistics.values()),
            "documents_declared": sum(len(d.documents) for d in self.inventory.domains),
            "text_utf8_bytes_scored": sum(s.text_utf8_bytes for s in statistics.values()),
            "text_utf8_bytes_declared": sum(d.text_utf8_bytes for d in self.inventory.domains),
            "documents_reused_within_boundary": reused,
        }
        if coverage["documents_scored"] != coverage["documents_declared"]:
            raise InvalidMetricError("not every declared validation document was scored")
        return EvaluationOutcome(AttemptOutcome.COMPLETE, metrics, coverage)


class CheckpointIdentityError(InvalidMetricError):
    """The checkpoint or evaluator is not the one an evaluation event requires."""


def score_checkpoint_validation(
    checkpoint: Path,
    *,
    manifest_path: Path,
    manifest_id: str,
    tier: EventTier,
    rolling_stride: int,
    forward_precision: str = "fp32",
    logprob_dtype: str = "fp64",
    device: str = "cpu",
    tokenizer_path: str | None = None,
    tokenizer: Any = None,
    expected_state_digest: str | None = None,
    expected_evaluator_digest: str | None = None,
    runtime: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Separate-process path: score an immutable checkpoint and bind what was loaded.

    P35 M3 (all optional, off by default): ``expected_state_digest`` and
    ``expected_evaluator_digest`` refuse, before anything is scored, a
    checkpoint whose loaded weights or an evaluator whose tokenizer, inventory
    or scoring policy differ from what an event requires. ``tokenizer`` passes
    the run's loaded tokenizer instead of a path, and ``runtime`` scores under
    the run's scoped scientific runtime (same kernel restrictions as training).
    """
    from xlm.artifacts.store import ArtifactStore, compute_file_sha256
    from xlm.core.paths import ArtifactPaths
    from xlm.evaluation.state_digest import model_state_digest
    from xlm.models.serialization import load_model_for_inference
    from xlm.tokenizers.loading import checkpoint_weights_hash, load_inference_tokenizer
    from xlm.training.science import ScientificRuntime

    if tokenizer is not None and tokenizer_path is not None:
        raise ValueError("pass a tokenizer object or a tokenizer path, not both")
    checkpoint = checkpoint.resolve()
    ArtifactStore(ArtifactPaths(root=checkpoint.parent.parent)).verify_artifact(checkpoint)
    weights_sha256 = checkpoint_weights_hash(checkpoint)
    model = load_model_for_inference(checkpoint, device=device)
    if checkpoint_weights_hash(checkpoint) != weights_sha256:
        raise InvalidMetricError("checkpoint weights changed while they were being loaded")
    state_digest = model_state_digest(model)
    if expected_state_digest is not None and state_digest != expected_state_digest:
        raise CheckpointIdentityError(
            f"checkpoint {checkpoint.name} holds model state {state_digest[:16]}…, not the "
            f"state {expected_state_digest[:16]}… the event was crossed at"
        )
    if tokenizer is None:
        tokenizer = load_inference_tokenizer(checkpoint, tokenizer_path)
    inventory = load_pinned_inventory(
        manifest_path, manifest_id=manifest_id, tokenizer_fingerprint=tokenizer.fingerprint
    )
    policy = LMScoringPolicy(
        context_length=int(model.config.context_length),
        rolling_stride=rolling_stride,
        forward_precision=forward_precision,
        logprob_dtype=logprob_dtype,
    )
    evaluator = LMValidationEvaluator(tier, inventory, tokenizer, policy)
    if (
        expected_evaluator_digest is not None
        and identity_digest(evaluator.identity()) != expected_evaluator_digest
    ):
        raise CheckpointIdentityError(
            "evaluator identity (tokenizer, inventory, scoring policy or scorer source) "
            "differs from the one the event was planned with"
        )
    meta = json.loads((checkpoint / "checkpoint_meta.json").read_text(encoding="utf-8"))
    with ScientificRuntime(runtime, device).scope():
        outcome = evaluator.evaluate(
            model,
            device=device,
            context=EvaluationContext(
                event_id=f"checkpoint:{checkpoint.name}",
                actual_committed_targets=int(meta["committed_valid_targets"]),
                model_state_digest=state_digest,
                provenance={"route": "checkpoint_separate_process_v1"},
            ),
        )
    if model_state_digest(model) != state_digest:
        raise InvalidMetricError("the scored checkpoint model changed during scoring")
    return {
        "model_state": {
            "kind": "checkpoint_v1",
            "checkpoint_id": checkpoint.name,
            "checkpoint_manifest_sha256": compute_file_sha256(checkpoint / "manifest.json"),
            "weights_sha256": weights_sha256,
            "state_digest": state_digest,
        },
        "checkpoint_meta": {
            key: meta.get(key) for key in ("run_id", "plan_id", "step", "committed_valid_targets")
        },
        "evaluator": evaluator.identity(),
        "status": outcome.status.value,
        "metrics": outcome.metrics,
        "coverage": outcome.coverage,
    }
