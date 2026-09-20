"""Per-item evaluation evidence and identity-checked result storage (C11, A26).

Exposed development examples keep their per-item likelihoods, choice margins and
identifiers alongside model/tokenizer/evaluator context. Truncation, omissions,
errors and limits are recorded explicitly; a score computed over an easy subset
that happened to fit is never silently presented as the task score.

The evidence cache is keyed by the full :class:`~xlm.evaluation.harness.HarnessIdentity`
fingerprint. A stale or foreign cache (including the harness's own response cache,
which suite runs disable) cannot override the identity comparison.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from xlm.evaluation.suites import SuiteIndex, TaskScore

EVIDENCE_VERSION = "1"


@dataclass(frozen=True)
class ItemEvidence:
    """One exposed evaluation item, with its choice structure preserved."""

    task: str
    item_id: str
    gold: int
    predicted: int
    predicted_normalized: int
    choice_log_likelihoods: list[float]
    choice_normalized_scores: list[float]
    raw_margin: float
    normalized_margin: float
    is_correct: bool
    is_correct_normalized: bool
    truncated: bool = False
    omitted_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TaskEvidence:
    """Aggregate evidence for one task/variant."""

    task: str
    variant_id: str
    metric_name: str
    acc: float
    acc_norm: float
    chance: float
    scored_items: int
    total_items: int
    omitted_items: int = 0
    error_items: int = 0
    truncated_items: int = 0
    subdataset_scores: dict[str, float] = field(default_factory=dict)
    items: list[ItemEvidence] = field(default_factory=list)

    def score(self) -> TaskScore:
        primary = self.acc_norm if self.metric_name == "acc_norm" else self.acc
        return TaskScore(
            task=self.task,
            metric_name=self.metric_name,
            value=primary,
            chance=self.chance,
            scored_items=self.scored_items,
            total_items=self.total_items,
            omissions=self.omitted_items,
            errors=self.error_items,
            truncated_items=self.truncated_items,
            subdataset_scores=dict(self.subdataset_scores),
        )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["items"] = [i.to_dict() for i in self.items]
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TaskEvidence:
        items = [ItemEvidence(**item) for item in data.get("items", [])]
        payload = {k: v for k, v in data.items() if k != "items"}
        return cls(**payload, items=items)


@dataclass
class EvaluationEvidence:
    """Complete identity-checked evidence for one suite run."""

    evidence_version: str
    identity_fingerprint: str
    identity: dict[str, Any]
    tasks: dict[str, TaskEvidence]
    index: SuiteIndex
    limit: int | None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_version": self.evidence_version,
            "identity_fingerprint": self.identity_fingerprint,
            "identity": self.identity,
            "tasks": {k: v.to_dict() for k, v in sorted(self.tasks.items())},
            "index": self.index.to_dict(),
            "limit": self.limit,
            "notes": self.notes,
        }

    def save(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(self.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(path)
        return path

    @classmethod
    def load(cls, path: Path) -> EvaluationEvidence:
        data = json.loads(path.read_text(encoding="utf-8"))
        tasks = {
            name: TaskEvidence.from_dict(payload) for name, payload in data.get("tasks", {}).items()
        }
        index_payload = data["index"]
        index = SuiteIndex(
            complete=bool(index_payload["complete"]),
            index=index_payload["index"],
            components=dict(index_payload["components"]),
            missing=list(index_payload["missing"]),
            chance_references=dict(index_payload["chance_references"]),
        )
        return cls(
            evidence_version=str(data["evidence_version"]),
            identity_fingerprint=str(data["identity_fingerprint"]),
            identity=dict(data["identity"]),
            tasks=tasks,
            index=index,
            limit=data.get("limit"),
            notes=list(data.get("notes", [])),
        )


class EvidenceCache:
    """Identity-checked evidence store; a mismatch forces recomputation."""

    def __init__(self, cache_dir: Path) -> None:
        self.cache_dir = cache_dir

    def path_for(self, identity_fingerprint: str) -> Path:
        return self.cache_dir / f"evidence_{identity_fingerprint}.json"

    def load_if_match(
        self, identity_fingerprint: str, expected_identity: dict[str, Any]
    ) -> EvaluationEvidence | None:
        """Return cached evidence only when the recorded identity matches exactly."""
        path = self.path_for(identity_fingerprint)
        if not path.is_file():
            return None
        try:
            evidence = EvaluationEvidence.load(path)
        except (json.JSONDecodeError, KeyError, ValueError, TypeError):
            return None
        if evidence.identity_fingerprint != identity_fingerprint:
            return None
        if evidence.identity != expected_identity:
            return None
        return evidence

    def store(self, evidence: EvaluationEvidence) -> Path:
        return evidence.save(self.path_for(evidence.identity_fingerprint))


def _sample_choice_scores(sample: dict[str, Any]) -> tuple[list[float], int, int]:
    """Extract raw choice log-likelihoods and gold index from a harness sample."""
    scores = sample.get("filtered_resps") or sample.get("resps")
    if not isinstance(scores, list):
        raise ValueError(f"sample for '{sample.get('doc_id')}' carries no responses")
    raw: list[float] = []
    for entry in scores:
        if isinstance(entry, (list, tuple)) and entry:
            raw.append(float(entry[0]))
        elif isinstance(entry, (int, float)):
            raw.append(float(entry))
        else:
            raise ValueError(f"unrecognized response entry: {entry!r}")
    target = sample.get("target")
    if isinstance(target, int):
        gold = target
    elif isinstance(target, str) and target.isdigit():
        gold = int(target)
    else:
        raise ValueError(f"sample target {target!r} is not an index")
    return raw, gold, len(raw)


def items_from_harness_samples(task: str, samples: list[dict[str, Any]]) -> list[ItemEvidence]:
    """Convert harness per-item samples into evidence rows.

    Ties break on the lowest index (first ``max`` occurrence), matching the
    harness's ``np.argmax`` behavior and the native scorer's Amendment 6 rule.
    Normalization divides by the choice's character length.
    """
    items: list[ItemEvidence] = []
    for sample in samples:
        raw, gold, n_choices = _sample_choice_scores(sample)
        doc_choices = sample.get("doc", {}).get("choices", [])
        lengths = [float(len(str(c))) for c in doc_choices]
        if len(lengths) != n_choices:
            lengths = [1.0] * n_choices
        norm = [raw[i] / lengths[i] for i in range(n_choices)]

        best_raw = max(raw)
        pred = raw.index(best_raw)
        best_norm = max(norm)
        pred_norm = norm.index(best_norm)

        others_raw = [v for i, v in enumerate(raw) if i != pred]
        others_norm = [v for i, v in enumerate(norm) if i != pred_norm]
        doc_id = sample.get("doc_id")
        items.append(
            ItemEvidence(
                task=task,
                item_id=str(sample.get("id", doc_id)),
                gold=gold,
                predicted=pred,
                predicted_normalized=pred_norm,
                choice_log_likelihoods=raw,
                choice_normalized_scores=norm,
                raw_margin=(best_raw - max(others_raw)) if others_raw else 0.0,
                normalized_margin=(best_norm - max(others_norm)) if others_norm else 0.0,
                is_correct=(pred == gold),
                is_correct_normalized=(pred_norm == gold),
                truncated=bool(sample.get("truncated", False)),
            )
        )
    return items


def items_from_native_results(
    task: str,
    item_results: list[Any],
) -> list[ItemEvidence]:
    """Convert native ``MultipleChoiceItemResult`` objects into evidence rows."""
    evidence: list[ItemEvidence] = []
    for result in item_results:
        evidence.append(
            ItemEvidence(
                task=task,
                item_id=result.item_id,
                gold=result.gold_index,
                predicted=result.predicted_raw,
                predicted_normalized=result.predicted_norm,
                choice_log_likelihoods=list(result.choice_log_likelihoods),
                choice_normalized_scores=list(result.choice_normalized_scores),
                raw_margin=result.raw_margin,
                normalized_margin=result.norm_margin,
                is_correct=result.is_correct_raw,
                is_correct_normalized=result.is_correct_norm,
            )
        )
    return evidence


def task_evidence_from_items(
    task: str,
    variant_id: str,
    metric_name: str,
    chance: float,
    items: list[ItemEvidence],
    total_items: int | None = None,
) -> TaskEvidence:
    """Aggregate per-item evidence into task evidence with coverage accounting."""
    scored = [i for i in items if i.omitted_reason is None]
    if not scored:
        return TaskEvidence(
            task=task,
            variant_id=variant_id,
            metric_name=metric_name,
            acc=0.0,
            acc_norm=0.0,
            chance=chance,
            scored_items=0,
            total_items=total_items if total_items is not None else len(items),
            omitted_items=len(items) - len(scored),
        )
    acc = sum(1 for i in scored if i.is_correct) / len(scored)
    acc_norm = sum(1 for i in scored if i.is_correct_normalized) / len(scored)
    return TaskEvidence(
        task=task,
        variant_id=variant_id,
        metric_name=metric_name,
        acc=acc,
        acc_norm=acc_norm,
        chance=chance,
        scored_items=len(scored),
        total_items=total_items if total_items is not None else len(items),
        omitted_items=len(items) - len(scored),
        error_items=sum(1 for i in scored if i.truncated),
        truncated_items=sum(1 for i in scored if i.truncated),
        items=items,
    )
