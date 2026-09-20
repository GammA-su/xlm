"""Expected evaluation population, coverage accounting and score eligibility (D05).

Coverage is decided by comparing what a frozen manifest *declared before the
run* against what execution actually returned. The expected population is never
derived from the returned predictions: a run that returns two items because a
``--limit`` truncated it, because an item failed, or because a BLiMP subdataset
never executed, is incomplete and says so.

Three coverage states exist:

``declared``
    An evaluation-input manifest fixed the expected population up front. The
    run is complete only when every declared item of every required task was
    scored and every required BLiMP subdataset appeared.

``undeclared``
    No manifest was supplied (the legacy authored-fixture route). Per-task
    metrics are still useful, but the expected population is unknown, so the
    full-suite index is withheld rather than guessed.

``legacy_unverified``
    Evidence written before this policy existed. It carries no population
    evidence and is never retroactively treated as complete.

Nothing here changes a score. Metric definitions, chance references,
denominators and the four-task weighting are untouched; coverage only decides
whether the declared-scope index may be *published*.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any

from xlm.evaluation.inputs import EvaluationInputManifest, VerifiedInputs
from xlm.evaluation.suites import REQUIRED_TASKS_FOR_INDEX

COVERAGE_POLICY_VERSION = "1"


class CoverageStatus(StrEnum):
    """How much is known about this run's expected population."""

    DECLARED = "declared"
    UNDECLARED = "undeclared"
    LEGACY_UNVERIFIED = "legacy_unverified"


@dataclass(frozen=True)
class ExpectedSelection:
    """The expected population of one task variant, fixed before execution."""

    namespace: str
    task: str
    subdataset: str | None
    bound_task_name: str
    item_ids: tuple[str, ...]

    @property
    def expected_items(self) -> int:
        return len(self.item_ids)


@dataclass(frozen=True)
class ExpectedPopulation:
    """Everything a run is required to cover, derived only from the manifest."""

    manifest_id: str
    scope_label: str
    scope_kind: str
    exposure_class: str
    tier: str
    selections: tuple[ExpectedSelection, ...]
    required_tasks: tuple[str, ...]
    required_blimp_subdatasets: tuple[str, ...]
    runtime_limit: int | None = None

    def by_bound_task(self) -> dict[str, ExpectedSelection]:
        return {s.bound_task_name: s for s in self.selections}

    def expected_items_for(self, task: str) -> int:
        return sum(s.expected_items for s in self.selections if s.task == task)

    def limit_truncates(self) -> list[str]:
        """Selections whose expected population exceeds an applied runtime limit."""
        if self.runtime_limit is None:
            return []
        return [s.namespace for s in self.selections if s.expected_items > self.runtime_limit]


def expected_population(
    verified: VerifiedInputs,
    *,
    runtime_limit: int | None = None,
    required_tasks: Sequence[str] = REQUIRED_TASKS_FOR_INDEX,
) -> ExpectedPopulation:
    """Freeze the expected population from verified inputs, before any execution."""
    manifest: EvaluationInputManifest = verified.manifest
    selections = tuple(
        ExpectedSelection(
            namespace=v.namespace,
            task=v.selection.task,
            subdataset=v.selection.subdataset,
            bound_task_name=v.selection.bound_task_name,
            item_ids=v.selection.namespaced_ids(),
        )
        for v in verified.selections
    )
    declared = {s.task for s in selections}
    return ExpectedPopulation(
        manifest_id=verified.manifest_id,
        scope_label=manifest.scope_label,
        scope_kind=manifest.scope_kind,
        exposure_class=manifest.exposure_class,
        tier=str(manifest.tier),
        selections=selections,
        # A manifest that does not declare all four tasks is a legitimate
        # narrower scope; it is required to cover exactly what it declared.
        required_tasks=tuple(t for t in required_tasks if t in declared),
        required_blimp_subdatasets=tuple(sorted(manifest.required_blimp_subdatasets)),
        runtime_limit=runtime_limit,
    )


@dataclass(frozen=True)
class TaskCoverage:
    """Reconciliation of one logical task's expected and observed populations."""

    task: str
    expected_items: int
    scored_items: int
    expected_item_ids: tuple[str, ...] = ()
    scored_item_ids: tuple[str, ...] = ()
    missing_item_ids: tuple[str, ...] = ()
    unexpected_item_ids: tuple[str, ...] = ()
    duplicate_item_ids: tuple[str, ...] = ()
    failed_item_ids: tuple[str, ...] = ()
    required_subdatasets: tuple[str, ...] = ()
    observed_subdatasets: tuple[str, ...] = ()
    missing_subdatasets: tuple[str, ...] = ()

    @property
    def complete(self) -> bool:
        return (
            not (
                self.missing_item_ids
                or self.unexpected_item_ids
                or self.duplicate_item_ids
                or self.failed_item_ids
                or self.missing_subdatasets
            )
            and self.scored_items == self.expected_items
        )

    def reasons(self) -> list[str]:
        problems: list[str] = []
        if self.missing_item_ids:
            problems.append(
                f"{len(self.missing_item_ids)} declared item(s) were never scored "
                f"(e.g. {list(self.missing_item_ids[:3])})"
            )
        if self.unexpected_item_ids:
            problems.append(
                f"{len(self.unexpected_item_ids)} scored item(s) were not declared "
                f"(e.g. {list(self.unexpected_item_ids[:3])})"
            )
        if self.duplicate_item_ids:
            problems.append(f"duplicate scored item ids: {list(self.duplicate_item_ids[:3])}")
        if self.failed_item_ids:
            problems.append(f"{len(self.failed_item_ids)} item(s) failed during execution")
        if self.missing_subdatasets:
            problems.append(f"missing required subdatasets: {list(self.missing_subdatasets)}")
        return problems

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        for key, value in list(payload.items()):
            if isinstance(value, tuple):
                payload[key] = list(value)
        payload["complete"] = self.complete
        payload["reasons"] = self.reasons()
        return payload


@dataclass(frozen=True)
class SuiteCoverage:
    """Whole-run coverage and the resulting eligibility of the declared-scope index."""

    status: CoverageStatus
    policy_version: str = COVERAGE_POLICY_VERSION
    scope_label: str = "undeclared"
    scope_kind: str = "undeclared"
    exposure_class: str = "unknown"
    manifest_id: str | None = None
    tasks: Mapping[str, TaskCoverage] = field(default_factory=dict)
    required_tasks: tuple[str, ...] = ()
    missing_tasks: tuple[str, ...] = ()
    runtime_limit: int | None = None
    interrupted: bool = False
    interruption_reason: str | None = None
    notes: tuple[str, ...] = ()

    @property
    def complete(self) -> bool:
        """True only when a declared scope was covered exactly and fully."""
        if self.status is not CoverageStatus.DECLARED:
            return False
        if self.interrupted or self.missing_tasks:
            return False
        if not self.required_tasks:
            return False
        return all(task in self.tasks and self.tasks[task].complete for task in self.required_tasks)

    @property
    def covers_full_suite(self) -> bool:
        """True when the declared scope is complete *and* spans all four tasks."""
        return self.complete and set(self.required_tasks) == set(REQUIRED_TASKS_FOR_INDEX)

    @property
    def is_authored_fixture(self) -> bool:
        return self.scope_kind == "authored_fixture"

    def eligibility_reasons(self) -> list[str]:
        """Why the declared-scope index is, or is not, publishable."""
        reasons: list[str] = []
        if self.status is CoverageStatus.UNDECLARED:
            reasons.append(
                "no evaluation-input manifest was supplied: the expected population is "
                "unknown, so the suite index is withheld rather than inferred from the "
                "results that happened to return"
            )
            return reasons
        if self.status is CoverageStatus.LEGACY_UNVERIFIED:
            reasons.append(
                "legacy evidence without population evidence: completeness cannot be "
                "established retroactively"
            )
            return reasons
        if self.interrupted:
            reasons.append(
                "execution was interrupted"
                + (f": {self.interruption_reason}" if self.interruption_reason else "")
            )
        if self.missing_tasks:
            reasons.append(f"declared tasks returned no results: {list(self.missing_tasks)}")
        for task in self.required_tasks:
            coverage = self.tasks.get(task)
            if coverage is None:
                continue
            for problem in coverage.reasons():
                reasons.append(f"{task}: {problem}")
        return reasons

    def scope_statement(self) -> str:
        """One line naming exactly what this result is, for reports and receipts."""
        if self.status is CoverageStatus.LEGACY_UNVERIFIED:
            return "legacy evidence; coverage unverified"
        if self.status is CoverageStatus.UNDECLARED:
            return "undeclared scope; coverage unverified"
        kind = {
            "authored_fixture": "authored synthetic fixture scope",
            "frozen_development_subset": "frozen development subset",
            "full_official_split": "full official split",
        }.get(self.scope_kind, self.scope_kind)
        state = "complete" if self.complete else "INCOMPLETE"
        return f"{kind} '{self.scope_label}' ({state})"

    def research_eligible(self) -> bool:
        """Whether this result may back a promotion or comparison claim.

        Authored fixture evidence proves the machinery works; it is never
        research evidence, however complete it is within its own scope.
        """
        return self.complete and not self.is_authored_fixture

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": str(self.status),
            "policy_version": self.policy_version,
            "scope_label": self.scope_label,
            "scope_kind": self.scope_kind,
            "exposure_class": self.exposure_class,
            "manifest_id": self.manifest_id,
            "complete": self.complete,
            "covers_full_suite": self.covers_full_suite,
            "research_eligible": self.research_eligible(),
            "scope_statement": self.scope_statement(),
            "required_tasks": list(self.required_tasks),
            "missing_tasks": list(self.missing_tasks),
            "runtime_limit": self.runtime_limit,
            "interrupted": self.interrupted,
            "interruption_reason": self.interruption_reason,
            "eligibility_reasons": self.eligibility_reasons(),
            "tasks": {name: cov.to_dict() for name, cov in sorted(self.tasks.items())},
            "notes": list(self.notes),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> SuiteCoverage:
        tasks = {}
        for name, entry in (payload.get("tasks") or {}).items():
            known = {
                key: entry[key]
                for key in (
                    "task",
                    "expected_items",
                    "scored_items",
                    "expected_item_ids",
                    "scored_item_ids",
                    "missing_item_ids",
                    "unexpected_item_ids",
                    "duplicate_item_ids",
                    "failed_item_ids",
                    "required_subdatasets",
                    "observed_subdatasets",
                    "missing_subdatasets",
                )
                if key in entry
            }
            for key, value in known.items():
                if isinstance(value, list):
                    known[key] = tuple(value)
            tasks[name] = TaskCoverage(**known)
        return cls(
            status=CoverageStatus(payload["status"]),
            policy_version=str(payload.get("policy_version", COVERAGE_POLICY_VERSION)),
            scope_label=str(payload.get("scope_label", "undeclared")),
            scope_kind=str(payload.get("scope_kind", "undeclared")),
            exposure_class=str(payload.get("exposure_class", "unknown")),
            manifest_id=payload.get("manifest_id"),
            tasks=tasks,
            required_tasks=tuple(payload.get("required_tasks") or ()),
            missing_tasks=tuple(payload.get("missing_tasks") or ()),
            runtime_limit=payload.get("runtime_limit"),
            interrupted=bool(payload.get("interrupted", False)),
            interruption_reason=payload.get("interruption_reason"),
            notes=tuple(payload.get("notes") or ()),
        )


@dataclass(frozen=True)
class ObservedItem:
    """One returned item, as coverage accounting sees it."""

    namespace: str
    item_id: str
    subdataset: str | None = None
    failed: bool = False

    @property
    def namespaced_id(self) -> str:
        return f"{self.namespace}#{self.item_id}"


def undeclared_coverage(
    *,
    observed: Mapping[str, Sequence[ObservedItem]] | None = None,
    runtime_limit: int | None = None,
    notes: Sequence[str] = (),
) -> SuiteCoverage:
    """Coverage for a run with no manifest: honest about what is not known.

    Scored counts are recorded because they are observed facts. Expected counts
    are left at zero rather than copied from the observations, so nothing
    downstream can mistake "two items came back" for "two items were expected".
    """
    tasks: dict[str, TaskCoverage] = {}
    for task, items in (observed or {}).items():
        scored = [i for i in items if not i.failed]
        tasks[task] = TaskCoverage(
            task=task,
            expected_items=0,
            scored_items=len(scored),
            scored_item_ids=tuple(sorted(i.namespaced_id for i in scored)),
            failed_item_ids=tuple(sorted(i.namespaced_id for i in items if i.failed)),
        )
    return SuiteCoverage(
        status=CoverageStatus.UNDECLARED,
        tasks=tasks,
        runtime_limit=runtime_limit,
        notes=tuple(notes),
    )


def legacy_coverage(notes: Sequence[str] = ()) -> SuiteCoverage:
    """Coverage for evidence written before population evidence was recorded."""
    return SuiteCoverage(
        status=CoverageStatus.LEGACY_UNVERIFIED,
        scope_label="legacy",
        scope_kind="legacy",
        notes=tuple(notes),
    )


def reconcile_coverage(
    expected: ExpectedPopulation,
    observed: Mapping[str, Iterable[ObservedItem]],
    *,
    interrupted: bool = False,
    interruption_reason: str | None = None,
    notes: Sequence[str] = (),
) -> SuiteCoverage:
    """Compare the frozen expected population against what execution returned.

    ``observed`` is keyed by logical task. Items are matched by their namespaced
    identity, so a BLiMP item cannot satisfy another subdataset's expectation
    and a positional index can never stand in for a declared id.
    """
    by_task_expected: dict[str, list[ExpectedSelection]] = {}
    for selection in expected.selections:
        by_task_expected.setdefault(selection.task, []).append(selection)

    tasks: dict[str, TaskCoverage] = {}
    missing_tasks: list[str] = []

    for task, selections in sorted(by_task_expected.items()):
        expected_ids: list[str] = []
        for selection in selections:
            expected_ids.extend(selection.item_ids)
        observed_items = list(observed.get(task, ()))
        if not observed_items:
            missing_tasks.append(task)

        scored = [i for i in observed_items if not i.failed]
        failed = [i for i in observed_items if i.failed]
        scored_ids = [i.namespaced_id for i in scored]
        duplicates = sorted({i for i in scored_ids if scored_ids.count(i) > 1})

        required_subdatasets = tuple(sorted({s.subdataset for s in selections if s.subdataset}))
        observed_subdatasets = tuple(sorted({i.subdataset for i in observed_items if i.subdataset}))
        missing_subdatasets = tuple(sorted(set(required_subdatasets) - set(observed_subdatasets)))

        tasks[task] = TaskCoverage(
            task=task,
            expected_items=len(expected_ids),
            scored_items=len(scored),
            expected_item_ids=tuple(expected_ids),
            # Canonical order. Arrival order carries no meaning - the harness
            # batches and sorts internally - and a coverage conclusion that
            # depended on it would not be reproducible.
            scored_item_ids=tuple(sorted(scored_ids)),
            missing_item_ids=tuple(sorted(set(expected_ids) - set(scored_ids))),
            unexpected_item_ids=tuple(sorted(set(scored_ids) - set(expected_ids))),
            duplicate_item_ids=tuple(duplicates),
            failed_item_ids=tuple(sorted(i.namespaced_id for i in failed)),
            required_subdatasets=required_subdatasets,
            observed_subdatasets=observed_subdatasets,
            missing_subdatasets=missing_subdatasets,
        )

    run_notes = list(notes)
    truncated = expected.limit_truncates()
    if truncated:
        run_notes.append(
            f"runtime limit {expected.runtime_limit} is smaller than the declared population "
            f"of {truncated}: this run cannot be complete for its declared scope."
        )

    return SuiteCoverage(
        status=CoverageStatus.DECLARED,
        scope_label=expected.scope_label,
        scope_kind=expected.scope_kind,
        exposure_class=expected.exposure_class,
        manifest_id=expected.manifest_id,
        tasks=tasks,
        required_tasks=tuple(expected.required_tasks),
        missing_tasks=tuple(sorted(missing_tasks)),
        runtime_limit=expected.runtime_limit,
        interrupted=interrupted,
        interruption_reason=interruption_reason,
        notes=tuple(run_notes),
    )
