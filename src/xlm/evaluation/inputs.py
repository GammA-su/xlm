"""Versioned evaluation-input manifests for explicitly selected local benchmark data (D04).

The evaluator never downloads anything. An operator prepares a *selected
evaluation artifact* outside this process, writes a manifest describing exactly
what was selected, and this module verifies that artifact before any task
executes.

A manifest declares, per task variant:

* the source dataset repository, immutable revision, configuration and the
  **official split the records were selected from**;
* the explicit selected example IDs and the record field carrying them;
* the required grouping/subdataset membership (BLiMP subdatasets);
* the local artifact path with its content digest and size;
* the record-schema/adapter version and the task-definition source;
* the feedback tier and the exposure classification.

Verification is deliberately unforgiving. A local filename proves nothing: the
declared identity, the declared membership and the file's actual contents must
all agree, and the declared split must be the one the evaluation policy assigns
to that tier for that task. Nothing here consults the network, loads a provider
cache, or executes code named by a manifest.

The manifest also carries an optional acquisition-receipt reference. Populating
it is D02's job; this module only records and re-states it.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

from xlm.evaluation.suites import (
    FinalAuthorization,
    FinalAuthorizationRequiredError,
    SuiteTier,
    resolve_suite,
)

EVAL_INPUT_MANIFEST_VERSION = "1"

#: Scope kinds a manifest may declare. The label is reported verbatim next to
#: every score so a frozen subset is never presented as the full benchmark.
SCOPE_KINDS = (
    "authored_fixture",
    "frozen_development_subset",
    "full_official_split",
)

#: Exposure classes from the evaluation policy. ``isolated_final`` cannot be
#: declared by a developer manifest; the operator service owns that path.
EXPOSURE_CLASSES = (
    "authored_fixture",
    "development_exposed",
    "isolated_final",
)

LOGICAL_TASKS = ("arc_easy", "hellaswag", "piqa", "blimp")

#: Only these task-config keys may be rewritten when binding a verified local
#: artifact to a pinned installed task definition. Everything else - prompts,
#: preprocessing, choices, delimiters, metrics, filters, few-shot policy - is
#: preserved exactly as the installed harness defines it.
LOADER_ONLY_KEYS = frozenset(
    {
        "dataset_path",
        "dataset_name",
        "dataset_kwargs",
        "training_split",
        "validation_split",
        "test_split",
        "fewshot_split",
    }
)


class EvaluationInputError(RuntimeError):
    """Base error for evaluation-input manifests."""


class ManifestSchemaError(EvaluationInputError):
    """Raised when a manifest is structurally invalid."""


class InputVerificationError(EvaluationInputError):
    """Raised when a declared artifact fails verification."""


class MembershipError(InputVerificationError):
    """Raised when declared membership disagrees with the artifact contents."""


class TierViolationError(InputVerificationError):
    """Raised when a manifest requests a split its tier may not touch."""


def _require(payload: Mapping[str, Any], key: str, where: str) -> Any:
    if key not in payload:
        raise ManifestSchemaError(f"{where}: required key '{key}' is missing")
    return payload[key]


def _require_str(payload: Mapping[str, Any], key: str, where: str) -> str:
    value = _require(payload, key, where)
    if not isinstance(value, str) or not value:
        raise ManifestSchemaError(f"{where}: '{key}' must be a non-empty string")
    return value


def _optional_str(payload: Mapping[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ManifestSchemaError(f"'{key}' must be a non-empty string or null")
    return value


@dataclass(frozen=True)
class TaskInputSelection:
    """One task variant's explicitly selected, locally verified evaluation artifact."""

    task: str
    leaf_task: str
    source_repository: str
    source_revision: str
    source_split: str
    record_schema_version: str
    adapter_version: str
    item_id_field: str
    item_ids: tuple[str, ...]
    data_file: str
    content_sha256: str
    content_bytes: int
    subdataset: str | None = None
    source_config: str | None = None
    source_population_size: int | None = None
    #: Record field carrying the gold label, when the task has one. BLiMP has
    #: none: its target is the positional convention "sentence_good first".
    #: Declaring it lets prompt content and label content be hashed separately,
    #: so a relabelling invalidates accuracies without pretending the rendered
    #: prompts changed too.
    label_field: str | None = None
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.task not in LOGICAL_TASKS:
            raise ManifestSchemaError(
                f"selection task '{self.task}' is not one of {list(LOGICAL_TASKS)}"
            )
        if self.task == "blimp" and not self.subdataset:
            raise ManifestSchemaError("a BLiMP selection must declare its subdataset")
        if self.task != "blimp" and self.subdataset:
            raise ManifestSchemaError(f"task '{self.task}' must not declare a BLiMP subdataset")
        if not self.item_ids:
            raise ManifestSchemaError(
                f"selection '{self.namespace}' declares no item ids; an empty "
                "expected population cannot be verified"
            )
        if len(set(self.item_ids)) != len(self.item_ids):
            duplicates = sorted({i for i in self.item_ids if self.item_ids.count(i) > 1})
            raise ManifestSchemaError(
                f"selection '{self.namespace}' declares duplicate item ids: {duplicates}"
            )
        if len(self.content_sha256) != 64:
            raise ManifestSchemaError(
                f"selection '{self.namespace}' content_sha256 must be a sha256 hex digest"
            )
        if self.content_bytes < 0:
            raise ManifestSchemaError(f"selection '{self.namespace}' content_bytes is negative")
        if self.source_population_size is not None and self.source_population_size < len(
            self.item_ids
        ):
            raise ManifestSchemaError(
                f"selection '{self.namespace}' selects {len(self.item_ids)} items from a "
                f"declared source population of {self.source_population_size}"
            )

    @property
    def namespace(self) -> str:
        """Stable namespace for this selection's item identities.

        BLiMP item ids are only unique within a subdataset, and two tasks can
        both number their items from zero, so every id is qualified before it
        reaches coverage accounting.
        """
        return f"{self.task}/{self.subdataset}" if self.subdataset else self.task

    @property
    def bound_task_name(self) -> str:
        """Harness task name used for the locally bound run.

        Deliberately distinct from the official leaf name: a result row for
        ``xlmdev_arc_easy`` can never be mistaken for an official ``arc_easy``
        benchmark run in a log, a cache or a report.
        """
        return f"xlmdev_{self.leaf_task}"

    def namespaced_ids(self) -> tuple[str, ...]:
        return tuple(f"{self.namespace}#{item}" for item in self.item_ids)

    def identity(self) -> dict[str, Any]:
        """Identity components that must invalidate a bound result when changed."""
        return {
            "task": self.task,
            "leaf_task": self.leaf_task,
            "subdataset": self.subdataset,
            "repository": self.source_repository,
            "revision": self.source_revision,
            "config": self.source_config,
            "split": self.source_split,
            "record_schema_version": self.record_schema_version,
            "adapter_version": self.adapter_version,
            "item_id_field": self.item_id_field,
            "item_ids": list(self.item_ids),
            "content_sha256": self.content_sha256,
            "content_bytes": self.content_bytes,
            "label_field": self.label_field,
        }

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["item_ids"] = list(self.item_ids)
        payload["notes"] = list(self.notes)
        payload["namespace"] = self.namespace
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any], where: str) -> TaskInputSelection:
        raw_ids = _require(payload, "item_ids", where)
        if not isinstance(raw_ids, list) or not all(isinstance(i, (str, int)) for i in raw_ids):
            raise ManifestSchemaError(f"{where}: 'item_ids' must be a list of strings or integers")
        population = payload.get("source_population_size")
        if population is not None and not isinstance(population, int):
            raise ManifestSchemaError(f"{where}: 'source_population_size' must be an integer")
        notes = payload.get("notes") or []
        if not isinstance(notes, list) or not all(isinstance(n, str) for n in notes):
            raise ManifestSchemaError(f"{where}: 'notes' must be a list of strings")
        return cls(
            task=_require_str(payload, "task", where),
            leaf_task=_require_str(payload, "leaf_task", where),
            source_repository=_require_str(payload, "source_repository", where),
            source_revision=_require_str(payload, "source_revision", where),
            source_split=_require_str(payload, "source_split", where),
            record_schema_version=_require_str(payload, "record_schema_version", where),
            adapter_version=_require_str(payload, "adapter_version", where),
            item_id_field=_require_str(payload, "item_id_field", where),
            item_ids=tuple(str(i) for i in raw_ids),
            data_file=_require_str(payload, "data_file", where),
            content_sha256=_require_str(payload, "content_sha256", where),
            content_bytes=int(_require(payload, "content_bytes", where)),
            subdataset=_optional_str(payload, "subdataset"),
            source_config=_optional_str(payload, "source_config"),
            source_population_size=population,
            label_field=_optional_str(payload, "label_field"),
            notes=tuple(notes),
        )


@dataclass(frozen=True)
class EvaluationInputManifest:
    """A frozen description of exactly which local evaluation inputs may be used."""

    manifest_version: str
    scope_label: str
    scope_kind: str
    exposure_class: str
    tier: SuiteTier
    harness_version: str
    task_definition_source: str
    selections: tuple[TaskInputSelection, ...]
    required_blimp_subdatasets: tuple[str, ...] = ()
    acquisition_receipts: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    source_path: Path | None = field(default=None, compare=False)

    def __post_init__(self) -> None:
        if self.manifest_version != EVAL_INPUT_MANIFEST_VERSION:
            raise ManifestSchemaError(
                f"unsupported manifest_version '{self.manifest_version}'; "
                f"this build reads version {EVAL_INPUT_MANIFEST_VERSION}"
            )
        if self.scope_kind not in SCOPE_KINDS:
            raise ManifestSchemaError(
                f"scope_kind '{self.scope_kind}' is not one of {list(SCOPE_KINDS)}"
            )
        if self.exposure_class not in EXPOSURE_CLASSES:
            raise ManifestSchemaError(
                f"exposure_class '{self.exposure_class}' is not one of {list(EXPOSURE_CLASSES)}"
            )
        if not self.selections:
            raise ManifestSchemaError("a manifest must declare at least one selection")
        if self.task_definition_source != "pinned_installed_harness":
            raise ManifestSchemaError(
                "task_definition_source must be 'pinned_installed_harness': XLM never "
                "loads task definitions supplied by an input manifest"
            )
        seen: set[str] = set()
        for selection in self.selections:
            if selection.namespace in seen:
                raise ManifestSchemaError(f"duplicate selection namespace '{selection.namespace}'")
            seen.add(selection.namespace)
        declared_subdatasets = {s.subdataset for s in self.selections if s.subdataset}
        missing = sorted(set(self.required_blimp_subdatasets) - declared_subdatasets)
        if missing:
            raise ManifestSchemaError(
                f"required BLiMP subdatasets are not selected by this manifest: {missing}"
            )
        if self.scope_kind == "authored_fixture" and self.exposure_class != "authored_fixture":
            raise ManifestSchemaError(
                "an authored_fixture scope must declare exposure_class 'authored_fixture'"
            )

    @property
    def declared_tasks(self) -> tuple[str, ...]:
        return tuple(sorted({s.task for s in self.selections}))

    @property
    def is_authored_fixture(self) -> bool:
        return self.scope_kind == "authored_fixture"

    def selections_for(self, task: str) -> tuple[TaskInputSelection, ...]:
        return tuple(s for s in self.selections if s.task == task)

    def expected_ids(self) -> dict[str, tuple[str, ...]]:
        """Namespaced expected item ids, declared before anything is executed."""
        return {s.namespace: s.namespaced_ids() for s in self.selections}

    def manifest_id(self) -> str:
        """Content identity of the declared scope, independent of file location."""
        payload = {
            "manifest_version": self.manifest_version,
            "scope_label": self.scope_label,
            "scope_kind": self.scope_kind,
            "exposure_class": self.exposure_class,
            "tier": str(self.tier),
            "harness_version": self.harness_version,
            "task_definition_source": self.task_definition_source,
            "required_blimp_subdatasets": sorted(self.required_blimp_subdatasets),
            "selections": [
                s.identity() for s in sorted(self.selections, key=lambda s: s.namespace)
            ],
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "manifest_version": self.manifest_version,
            "manifest_id": self.manifest_id(),
            "scope_label": self.scope_label,
            "scope_kind": self.scope_kind,
            "exposure_class": self.exposure_class,
            "tier": str(self.tier),
            "harness_version": self.harness_version,
            "task_definition_source": self.task_definition_source,
            "required_blimp_subdatasets": list(self.required_blimp_subdatasets),
            "acquisition_receipts": list(self.acquisition_receipts),
            "notes": list(self.notes),
            "selections": [s.to_dict() for s in self.selections],
        }

    @classmethod
    def from_dict(
        cls, payload: Mapping[str, Any], source_path: Path | None = None
    ) -> EvaluationInputManifest:
        where = str(source_path) if source_path else "<manifest>"
        if not isinstance(payload, Mapping):
            raise ManifestSchemaError(f"{where}: manifest must be a mapping")
        raw_selections = _require(payload, "selections", where)
        if not isinstance(raw_selections, list) or not raw_selections:
            raise ManifestSchemaError(f"{where}: 'selections' must be a non-empty list")
        selections = tuple(
            TaskInputSelection.from_dict(entry, f"{where} selections[{i}]")
            for i, entry in enumerate(raw_selections)
        )
        tier_value = _require_str(payload, "tier", where)
        try:
            tier = SuiteTier(tier_value)
        except ValueError as exc:
            raise ManifestSchemaError(f"{where}: unknown tier '{tier_value}'") from exc
        required = payload.get("required_blimp_subdatasets") or []
        receipts = payload.get("acquisition_receipts") or []
        notes = payload.get("notes") or []
        for name, value in (
            ("required_blimp_subdatasets", required),
            ("acquisition_receipts", receipts),
            ("notes", notes),
        ):
            if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                raise ManifestSchemaError(f"{where}: '{name}' must be a list of strings")
        return cls(
            manifest_version=_require_str(payload, "manifest_version", where),
            scope_label=_require_str(payload, "scope_label", where),
            scope_kind=_require_str(payload, "scope_kind", where),
            exposure_class=_require_str(payload, "exposure_class", where),
            tier=tier,
            harness_version=_require_str(payload, "harness_version", where),
            task_definition_source=_require_str(payload, "task_definition_source", where),
            selections=selections,
            required_blimp_subdatasets=tuple(required),
            acquisition_receipts=tuple(receipts),
            notes=tuple(notes),
            source_path=source_path,
        )


def load_evaluation_inputs(path: Path | str) -> EvaluationInputManifest:
    """Read a manifest from YAML or JSON. Data only; nothing is executed."""
    manifest_path = Path(path)
    if not manifest_path.is_file():
        raise EvaluationInputError(f"evaluation input manifest not found: {manifest_path}")
    raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ManifestSchemaError(f"{manifest_path}: manifest must be a mapping")
    return EvaluationInputManifest.from_dict(raw, source_path=manifest_path)


def save_evaluation_inputs(manifest: EvaluationInputManifest, path: Path) -> Path:
    """Write a manifest atomically, with platform-independent bytes.

    Newlines are written as ``\\n`` explicitly. A manifest records byte digests
    of its artifacts, so a platform that rewrote line endings would invalidate
    every one of them.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    payload = yaml.safe_dump(manifest.to_dict(), sort_keys=False, allow_unicode=True)
    tmp.write_bytes(payload.replace("\r\n", "\n").encode("utf-8"))
    tmp.replace(path)
    return path


# --------------------------------------------------------------------- verification


@dataclass(frozen=True)
class VerifiedSelection:
    """A selection whose declared identity and membership matched its artifact."""

    selection: TaskInputSelection
    resolved_path: Path
    bound_split: str
    record_count: int
    #: Digest of everything the prompts are rendered from - the record contents
    #: with the declared label field removed. Model likelihoods depend on this.
    prompt_digest: str = ""
    #: Digest of the gold labels alone. Accuracies and the aggregate depend on
    #: this; the rendered prompts do not.
    label_digest: str = ""

    @property
    def namespace(self) -> str:
        return self.selection.namespace


@dataclass(frozen=True)
class VerifiedInputs:
    """The result of verifying a manifest: the only inputs execution may touch."""

    manifest: EvaluationInputManifest
    manifest_id: str
    selections: tuple[VerifiedSelection, ...]
    policy_splits: Mapping[str, str]

    @property
    def scope_label(self) -> str:
        return self.manifest.scope_label

    def by_bound_task(self) -> dict[str, VerifiedSelection]:
        return {v.selection.bound_task_name: v for v in self.selections}

    def summary(self) -> dict[str, Any]:
        return {
            "manifest_id": self.manifest_id,
            "scope_label": self.manifest.scope_label,
            "scope_kind": self.manifest.scope_kind,
            "exposure_class": self.manifest.exposure_class,
            "tier": str(self.manifest.tier),
            "tasks": list(self.manifest.declared_tasks),
            "required_blimp_subdatasets": list(self.manifest.required_blimp_subdatasets),
            "selections": [
                {
                    "namespace": v.namespace,
                    "bound_task": v.selection.bound_task_name,
                    "leaf_task": v.selection.leaf_task,
                    "declared_items": len(v.selection.item_ids),
                    "records_in_artifact": v.record_count,
                    "source": f"{v.selection.source_repository}@{v.selection.source_revision}",
                    "source_split": v.selection.source_split,
                    "content_sha256": v.selection.content_sha256,
                    "path": str(v.resolved_path),
                }
                for v in self.selections
            ],
            "acquisition_receipts": list(self.manifest.acquisition_receipts),
        }


def policy_splits_for_tier(tier: SuiteTier) -> dict[str, str]:
    """The official split each task may use at this tier, from the frozen suite.

    Derived from :func:`resolve_suite` rather than restated, so the split
    firewall cannot drift away from the evaluation policy it enforces.
    """
    authorization = (
        FinalAuthorization(True, "policy-introspection", "xlm", "policy")
        if tier is SuiteTier.FINAL
        else None
    )
    variants = resolve_suite(
        tier,
        {},
        blimp_subdatasets=["policy_probe_a", "policy_probe_b", "policy_probe_c"],
        final_authorization=authorization,
    )
    return {variant.lm_eval_task: variant.split for variant in variants}


def _read_records(path: Path) -> list[Mapping[str, Any]]:
    """Read a selected evaluation artifact: a JSON array or JSON Lines."""
    text = path.read_text(encoding="utf-8")
    stripped = text.lstrip()
    if stripped.startswith("["):
        payload = json.loads(text)
        if not isinstance(payload, list):
            raise InputVerificationError(f"{path}: JSON payload must be an array of records")
        records = payload
    else:
        records = [json.loads(line) for line in text.splitlines() if line.strip()]
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            raise InputVerificationError(f"{path}: record {index} is not an object")
    return records


def _declared_ids_in(
    records: Sequence[Mapping[str, Any]], selection: TaskInputSelection, path: Path
) -> list[str]:
    field_name = selection.item_id_field
    ids: list[str] = []
    for index, record in enumerate(records):
        if field_name not in record:
            raise MembershipError(
                f"{path}: record {index} has no declared id field '{field_name}'. A selected "
                "evaluation artifact must carry a stable source identifier for every item; "
                "positional order is not an identity."
            )
        value = record[field_name]
        if not isinstance(value, (str, int)):
            raise MembershipError(
                f"{path}: record {index} id field '{field_name}' is {type(value).__name__}, "
                "expected a string or integer"
            )
        ids.append(str(value))
    return ids


def _split_content_digests(
    records: Sequence[Mapping[str, Any]],
    selection: TaskInputSelection,
    present_ids: Sequence[str],
) -> tuple[str, str]:
    """Hash prompt-bearing content and gold labels separately.

    Keeping these apart is what lets a relabelling invalidate accuracies and the
    aggregate while leaving the raw-likelihood identity intact: the model saw
    exactly the same rendered prompts either way. Records are sorted by declared
    id first, so a reordered artifact produces identical digests.
    """
    label_field = selection.label_field
    ordered = sorted(zip(present_ids, records, strict=True), key=lambda pair: pair[0])
    prompt_payload = [
        (item_id, {k: v for k, v in sorted(record.items()) if k != label_field})
        for item_id, record in ordered
    ]
    if label_field is None:
        label_payload: list[Any] = ["positional_target_convention"]
    else:
        label_payload = [(item_id, record.get(label_field)) for item_id, record in ordered]
    prompt = json.dumps(prompt_payload, sort_keys=True, separators=(",", ":"), default=str)
    label = json.dumps(label_payload, sort_keys=True, separators=(",", ":"), default=str)
    return (
        hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        hashlib.sha256(label.encode("utf-8")).hexdigest(),
    )


def verify_evaluation_inputs(
    manifest: EvaluationInputManifest,
    *,
    base_dir: Path | None = None,
    tier: SuiteTier | None = None,
    harness_version: str | None = None,
    final_authorization: FinalAuthorization | None = None,
) -> VerifiedInputs:
    """Verify every declared artifact before any task is built or executed.

    Checks, in order and all of them fatal:

    1. tier admissibility (a developer manifest can never resolve ``final``);
    2. the split firewall, from the frozen suite definition;
    3. artifact existence, size and content digest;
    4. exact membership: declared ids == ids present, no duplicates, no extras;
    5. required BLiMP subdataset cover;
    6. pinned harness agreement.

    No network access, no provider cache, no code from the manifest.
    """
    effective_tier = tier or manifest.tier
    if tier is not None and tier is not manifest.tier:
        raise TierViolationError(
            f"manifest declares tier '{manifest.tier}' but the run requested '{tier}'"
        )
    if effective_tier is SuiteTier.FINAL and (
        final_authorization is None or not final_authorization.operator_authorized
    ):
        raise FinalAuthorizationRequiredError(
            "final-tier evaluation inputs refused: the ordinary developer command must "
            "never resolve a final split. Request it and let the operator-side service "
            "execute it."
        )
    if manifest.exposure_class == "isolated_final" and effective_tier is not SuiteTier.FINAL:
        raise TierViolationError(
            f"exposure_class 'isolated_final' cannot be used by a '{effective_tier}' developer run"
        )
    if harness_version is not None and manifest.harness_version != harness_version:
        raise InputVerificationError(
            f"manifest was built against harness {manifest.harness_version} but "
            f"{harness_version} is installed; rebuild the manifest with evidence."
        )

    policy = policy_splits_for_tier(effective_tier)
    root = base_dir or (manifest.source_path.parent if manifest.source_path else Path.cwd())

    verified: list[VerifiedSelection] = []
    for selection in manifest.selections:
        expected_split = policy.get(selection.task)
        if expected_split is None:
            raise TierViolationError(
                f"task '{selection.task}' has no policy split at tier '{effective_tier}'"
            )
        if selection.source_split != expected_split:
            raise TierViolationError(
                f"selection '{selection.namespace}' declares source split "
                f"'{selection.source_split}', but the evaluation policy assigns "
                f"'{expected_split}' to task '{selection.task}' at tier '{effective_tier}'. "
                "A self-declared label does not establish approved split membership."
            )

        candidate = Path(selection.data_file)
        resolved = candidate if candidate.is_absolute() else (root / candidate)
        resolved = resolved.resolve()
        if resolved.is_symlink() or not resolved.is_file():
            raise InputVerificationError(
                f"selection '{selection.namespace}': artifact is not a regular file: {resolved}"
            )
        actual_bytes = resolved.stat().st_size
        if actual_bytes != selection.content_bytes:
            raise InputVerificationError(
                f"selection '{selection.namespace}': artifact is {actual_bytes} bytes, "
                f"manifest declares {selection.content_bytes}"
            )
        actual_digest = hashlib.sha256(resolved.read_bytes()).hexdigest()
        if actual_digest != selection.content_sha256:
            raise InputVerificationError(
                f"selection '{selection.namespace}': artifact digest {actual_digest} does not "
                f"match the declared {selection.content_sha256}. Same-length corruption and "
                "silent substitution are both rejected here."
            )

        records = _read_records(resolved)
        present = _declared_ids_in(records, selection, resolved)
        if selection.label_field is not None:
            absent = [
                index for index, record in enumerate(records) if selection.label_field not in record
            ]
            if absent:
                raise MembershipError(
                    f"selection '{selection.namespace}': declared label field "
                    f"'{selection.label_field}' is missing from record(s) {absent[:5]}"
                )
        declared = list(selection.item_ids)
        duplicates = sorted({i for i in present if present.count(i) > 1})
        if duplicates:
            raise MembershipError(
                f"selection '{selection.namespace}': artifact contains duplicate item ids "
                f"{duplicates}"
            )
        missing = sorted(set(declared) - set(present))
        unexpected = sorted(set(present) - set(declared))
        if missing or unexpected:
            raise MembershipError(
                f"selection '{selection.namespace}': declared membership does not match the "
                f"artifact. Missing {missing}; unexpected {unexpected}. A larger unselected "
                "source must not be presented as the selected evaluation artifact."
            )
        prompt_digest, label_digest = _split_content_digests(records, selection, present)
        verified.append(
            VerifiedSelection(
                selection=selection,
                resolved_path=resolved,
                bound_split=selection.source_split,
                record_count=len(records),
                prompt_digest=prompt_digest,
                label_digest=label_digest,
            )
        )

    observed_subdatasets = {v.selection.subdataset for v in verified if v.selection.subdataset}
    missing_required = sorted(set(manifest.required_blimp_subdatasets) - observed_subdatasets)
    if missing_required:
        raise MembershipError(f"required BLiMP subdatasets were not verified: {missing_required}")

    return VerifiedInputs(
        manifest=manifest,
        manifest_id=manifest.manifest_id(),
        selections=tuple(verified),
        policy_splits=policy,
    )


# ------------------------------------------------------------------------ construction


def build_evaluation_inputs(
    *,
    scope_label: str,
    scope_kind: str,
    exposure_class: str,
    tier: SuiteTier,
    harness_version: str,
    entries: Iterable[Mapping[str, Any]],
    base_dir: Path,
    required_blimp_subdatasets: Sequence[str] = (),
    acquisition_receipts: Sequence[str] = (),
    notes: Sequence[str] = (),
) -> EvaluationInputManifest:
    """Build a manifest from prepared artifacts, deriving digests and membership.

    Each entry names an existing artifact and its declared provenance; the
    selected item ids and the content identity are read from the artifact
    itself rather than retyped. This is the one construction path -
    ``scripts/build_eval_inputs.py`` is a thin wrapper over it.
    """
    selections: list[TaskInputSelection] = []
    for index, entry in enumerate(entries):
        where = f"entry[{index}]"
        data_file = _require_str(entry, "data_file", where)
        candidate = Path(data_file)
        resolved = candidate if candidate.is_absolute() else (base_dir / candidate)
        resolved = resolved.resolve()
        if not resolved.is_file():
            raise EvaluationInputError(f"{where}: artifact not found: {resolved}")
        id_field = _require_str(entry, "item_id_field", where)
        records = _read_records(resolved)
        if not records:
            raise EvaluationInputError(f"{where}: artifact {resolved} contains no records")
        ids: list[str] = []
        for position, record in enumerate(records):
            if id_field not in record:
                raise EvaluationInputError(
                    f"{where}: record {position} in {resolved} has no id field '{id_field}'"
                )
            ids.append(str(record[id_field]))
        selections.append(
            TaskInputSelection(
                task=_require_str(entry, "task", where),
                leaf_task=_require_str(entry, "leaf_task", where),
                source_repository=_require_str(entry, "source_repository", where),
                source_revision=_require_str(entry, "source_revision", where),
                source_split=_require_str(entry, "source_split", where),
                record_schema_version=_require_str(entry, "record_schema_version", where),
                adapter_version=_require_str(entry, "adapter_version", where),
                item_id_field=id_field,
                item_ids=tuple(ids),
                data_file=data_file,
                content_sha256=hashlib.sha256(resolved.read_bytes()).hexdigest(),
                content_bytes=resolved.stat().st_size,
                subdataset=_optional_str(entry, "subdataset"),
                source_config=_optional_str(entry, "source_config"),
                source_population_size=entry.get("source_population_size"),
                label_field=_optional_str(entry, "label_field"),
                notes=tuple(entry.get("notes") or ()),
            )
        )
    return EvaluationInputManifest(
        manifest_version=EVAL_INPUT_MANIFEST_VERSION,
        scope_label=scope_label,
        scope_kind=scope_kind,
        exposure_class=exposure_class,
        tier=tier,
        harness_version=harness_version,
        task_definition_source="pinned_installed_harness",
        selections=tuple(selections),
        required_blimp_subdatasets=tuple(required_blimp_subdatasets),
        acquisition_receipts=tuple(acquisition_receipts),
        notes=tuple(notes),
    )
