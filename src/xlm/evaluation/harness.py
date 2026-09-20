"""Pinned lm-evaluation-harness integration surface (C11, A26).

The harness is an optional extra: this module imports without it and raises a
clear, actionable error when the harness path is requested. The integration is
pinned to one supported harness release; a different installed version is
reported rather than run silently. Backend registration goes through the
harness's public ``register_model`` registry and never edits installed code.

Cache identity includes the harness version, checkpoint/tokenizer hashes, task
and template versions, dataset revision/split, metric normalization policy,
context/truncation policy and precision. Harness response caching is disabled
for suite runs; XLM's own evidence store compares the full identity instead.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from xlm.evaluation.suites import TaskVariant

SUPPORTED_LM_EVAL_VERSION = "0.4.13"
HARNESS_CONTRACT_VERSION = "1"

# Suite runs never consult the harness's own response cache: stale third-party
# caches must not override XLM identity checks.
SUITE_USE_CACHE = False


class HarnessUnavailableError(RuntimeError):
    """Raised when harness functionality is requested without the eval extra."""


class HarnessVersionMismatchError(RuntimeError):
    """Raised when the installed harness differs from the pinned supported release."""


def harness_version() -> str | None:
    """Return the installed harness version, or None when it is absent."""
    try:
        import lm_eval  # noqa: PLC0415
    except ImportError:
        return None
    return str(getattr(lm_eval, "__version__", "unknown"))


def require_harness() -> Any:
    """Return the harness module, refusing version drift loudly."""
    try:
        import lm_eval  # noqa: PLC0415
    except ImportError as exc:
        raise HarnessUnavailableError(
            "lm-evaluation-harness is not installed. Run "
            "`uv sync --locked --extra cpu --extra eval` (or --extra cuda) before "
            "`xlm evaluate --suite search|confirmation`."
        ) from exc

    installed = str(getattr(lm_eval, "__version__", "unknown"))
    if installed != SUPPORTED_LM_EVAL_VERSION:
        raise HarnessVersionMismatchError(
            f"installed lm-eval {installed} does not match the pinned supported "
            f"version {SUPPORTED_LM_EVAL_VERSION}. XLM does not run silently against "
            "an unpinned harness; update the pin with evaluation evidence."
        )
    return lm_eval


def register_xlm_backend() -> type:
    """Register the XLM backend with the harness registry and return the class."""
    require_harness()
    from xlm.evaluation.harness_lm import XlmHarnessLM  # noqa: PLC0415

    return XlmHarnessLM


def create_harness_model(
    model: Any,
    tokenizer: Any,
    device: str = "cpu",
    precision: str = "fp32",
    boundary_policy: Any = None,
    max_context_length: int | None = None,
    max_gen_tokens: int = 256,
) -> Any:
    """Build a registered XLM harness backend instance."""
    harness_lm = register_xlm_backend()
    from xlm.evaluation.likelihood import BoundaryPolicy  # noqa: PLC0415

    return harness_lm(
        model=model,
        tokenizer=tokenizer,
        device=device,
        precision=precision,
        boundary_policy=boundary_policy or BoundaryPolicy.JOINT_PREFIX_MATCH_V1,
        max_context_length=max_context_length,
        max_gen_tokens=max_gen_tokens,
    )


@dataclass(frozen=True)
class HarnessIdentity:
    """Full cache identity for one evaluation run (C11)."""

    harness_version: str
    contract_version: str
    checkpoint_hash: str
    tokenizer_hash: str
    task_variant_ids: tuple[str, ...]
    dataset_revisions: tuple[str, ...]
    split_ids: tuple[str, ...]
    prompt_template_version: str
    metric_normalization_policy: str
    context_truncation_policy: str
    precision: str
    limit: int | None = None
    extra: tuple[tuple[str, str], ...] = ()

    def fingerprint(self) -> str:
        payload = json.dumps(
            {
                "harness": self.harness_version,
                "contract": self.contract_version,
                "checkpoint": self.checkpoint_hash,
                "tokenizer": self.tokenizer_hash,
                "variants": list(self.task_variant_ids),
                "revisions": list(self.dataset_revisions),
                "splits": list(self.split_ids),
                "template": self.prompt_template_version,
                "normalization": self.metric_normalization_policy,
                "truncation": self.context_truncation_policy,
                "precision": self.precision,
                "limit": self.limit,
                "extra": sorted(self.extra),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["fingerprint"] = self.fingerprint()
        return payload


def build_harness_identity(
    checkpoint_hash: str,
    tokenizer_hash: str,
    variants: list[TaskVariant],
    *,
    precision: str = "fp32",
    limit: int | None = None,
    prompt_template_version: str = "harness_task_native",
    context_truncation_policy: str = "rolling",
    extra: dict[str, str] | None = None,
) -> HarnessIdentity:
    """Build the identity that a cached result must match exactly."""
    return HarnessIdentity(
        harness_version=SUPPORTED_LM_EVAL_VERSION,
        contract_version=HARNESS_CONTRACT_VERSION,
        checkpoint_hash=checkpoint_hash,
        tokenizer_hash=tokenizer_hash,
        task_variant_ids=tuple(sorted(v.variant_id for v in variants)),
        dataset_revisions=tuple(sorted({v.dataset_revision or "unpinned" for v in variants})),
        split_ids=tuple(sorted({v.split for v in variants})),
        prompt_template_version=prompt_template_version,
        metric_normalization_policy="character_length",
        context_truncation_policy=context_truncation_policy,
        precision=precision,
        limit=limit,
        extra=tuple(sorted((extra or {}).items())),
    )


def _absolutize_local_paths(cfg: dict[str, Any], base_dir: Path) -> None:
    """Resolve local data_files relative to the source YAML's directory."""
    kwargs = cfg.get("dataset_kwargs")
    if not isinstance(kwargs, dict):
        return
    data_files = kwargs.get("data_files")
    if isinstance(data_files, dict):
        kwargs["data_files"] = {
            split: (
                str((base_dir / path).resolve())
                if isinstance(path, str) and not Path(path).is_absolute()
                else path
            )
            for split, path in data_files.items()
        }
    elif isinstance(data_files, str) and not Path(data_files).is_absolute():
        kwargs["data_files"] = str((base_dir / data_files).resolve())


def materialize_pinned_tasks(
    variants: list[TaskVariant],
    out_dir: Path,
    source_manager: Any,
) -> tuple[list[str], dict[str, TaskVariant]]:
    """Copy each variant's harness YAML into a private dir, adding its revision pin.

    The official task config (prompt, choices, preprocessing, metrics) is copied
    verbatim from the pinned installed harness; the only edits are the wrapper
    task name and the immutable ``dataset_kwargs.revision``. This avoids both
    reimplementing named tasks and mutating installed third-party code.

    Returns the task names to request and a name-to-variant mapping so results
    (including expanded BLiMP subdatasets) map back to their suite variant.
    """
    import yaml  # noqa: PLC0415

    out_dir.mkdir(parents=True, exist_ok=True)
    index = getattr(source_manager, "task_index", {})
    names: list[str] = []
    mapping: dict[str, TaskVariant] = {}

    for variant in variants:
        leaves = (
            list(variant.blimp_subdatasets)
            if variant.lm_eval_task == "blimp" and variant.blimp_subdatasets
            else [variant.lm_eval_task]
        )
        for leaf in leaves:
            entry = index.get(leaf)
            if entry is None or getattr(entry, "yaml_path", None) is None:
                raise HarnessUnavailableError(
                    f"cannot locate the pinned harness YAML for task '{leaf}'; "
                    "the installed harness may be incomplete."
                )
            source_path = Path(entry.yaml_path)
            cfg = yaml.safe_load(source_path.read_text(encoding="utf-8"))
            if not isinstance(cfg, dict):
                raise HarnessUnavailableError(f"task YAML '{source_path}' is not a mapping")
            _absolutize_local_paths(cfg, source_path.parent)
            # The old wrapper preserved official default final splits and let
            # datasets fetch entire configs. A revision pin alone cannot enforce
            # tier membership, transfer caps, or operator isolation.
            if cfg.get("dataset_path") != "json" or variant.dataset_revision is not None:
                raise HarnessUnavailableError(
                    "official/remote task execution is BLOCKED: requires a bounded, "
                    "split-scoped local dataset and frozen grouped membership; "
                    "automatic harness downloads are disabled by the P23 audit"
                )
            local_files = (cfg.get("dataset_kwargs") or {}).get("data_files", {})
            if (
                not isinstance(local_files, dict)
                or not local_files
                or any(
                    not isinstance(value, str) or not Path(value).is_file()
                    for value in local_files.values()
                )
            ):
                raise HarnessUnavailableError(
                    "fixture tasks require explicit existing local data files"
                )
            if variant.tier.value == "final":
                raise HarnessUnavailableError(
                    "final execution requires the separate operator service"
                )
            if variant.split not in local_files:
                raise HarnessUnavailableError(
                    "declared evaluation split is absent from local data files"
                )
            cfg["test_split"] = variant.split
            cfg["validation_split"] = variant.split
            cfg["training_split"] = None

            keep_leaf_name = variant.lm_eval_task == "blimp" or variant.dataset_revision is None
            wrapper_name = leaf if keep_leaf_name else variant.variant_id
            cfg["task"] = wrapper_name
            if variant.dataset_revision:
                kwargs = dict(cfg.get("dataset_kwargs") or {})
                kwargs["revision"] = variant.dataset_revision
                cfg["dataset_kwargs"] = kwargs

            wrapper_path = out_dir / f"{wrapper_name}.yaml"
            wrapper_path.write_text(
                yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True), encoding="utf-8"
            )
            names.append(wrapper_name)
            mapping[wrapper_name] = variant
    return names, mapping


def resolve_official_task_config(leaf: str, source_manager: Any) -> tuple[dict[str, Any], Path]:
    """Fully resolve one installed task definition, honouring its inheritance.

    The pinned harness expresses task definitions with ``include:`` (every BLiMP
    subdataset inherits ``_template_yaml``) and ``!function`` references
    (HellaSwag's ``process_docs`` lives beside its YAML). Reading the YAML text
    and copying it elsewhere silently loses both. This uses the harness's own
    loader, so what comes back is the definition the harness would really run:
    inherited keys merged, ``!function`` resolved to the actual callable.

    Nothing installed is modified, and no task code named by a manifest is run.
    """
    require_harness()
    from lm_eval.tasks._yaml_loader import load_yaml  # noqa: PLC0415

    index = getattr(source_manager, "task_index", {})
    entry = index.get(leaf)
    if entry is None or getattr(entry, "yaml_path", None) is None:
        raise HarnessUnavailableError(
            f"cannot locate the pinned harness YAML for task '{leaf}'; "
            "the installed harness may be incomplete."
        )
    source_path = Path(entry.yaml_path)
    config = load_yaml(source_path, resolve_func=True)
    if not isinstance(config, dict):
        raise HarnessUnavailableError(f"task YAML '{source_path}' is not a mapping")
    return dict(config), source_path


def bind_verified_inputs_to_tasks(
    verified: Any,
    source_manager: Any,
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    """Bind verified local artifacts to the pinned installed task definitions (D04).

    For each verified selection the official definition is resolved and then
    *only* loader keys are rewritten: the dataset becomes the verified local
    JSON artifact and exactly one split is declared. Prompts, ``process_docs``,
    choices, targets, delimiters, metrics, filters, decontamination settings and
    the zero-shot policy stay exactly as the installed harness defines them, and
    the function asserts that before returning.

    Returns the task specs to execute, a bound-name to verified-selection map,
    and the task-definition identities that must invalidate a cached result.

    :raises HarnessUnavailableError: when a definition cannot be bound safely.
        An unsupported case fails loudly instead of being approximated by a
        simplified local copy of the task.
    """
    from xlm.evaluation.inputs import LOADER_ONLY_KEYS  # noqa: PLC0415

    structural = {"tag", "group", "task_list", "include"}
    specs: list[dict[str, Any]] = []
    mapping: dict[str, Any] = {}
    identities: dict[str, Any] = {}

    for item in verified.selections:
        selection = item.selection
        config, source_path = resolve_official_task_config(selection.leaf_task, source_manager)

        output_type = config.get("output_type")
        if output_type != "multiple_choice":
            raise HarnessUnavailableError(
                f"task '{selection.leaf_task}' has output_type '{output_type}'. XLM binds "
                "verified local inputs only to multiple-choice conditional-likelihood "
                "tasks; other output types are unsupported, not approximated."
            )
        if int(config.get("num_fewshot", 0) or 0) != 0:
            raise HarnessUnavailableError(
                f"task '{selection.leaf_task}' declares num_fewshot="
                f"{config.get('num_fewshot')}; the frozen protocol is zero-shot."
            )

        identities[selection.namespace] = {
            "leaf_task": selection.leaf_task,
            "definition_source": str(source_path),
            "definition_sha256": hashlib.sha256(source_path.read_bytes()).hexdigest(),
            "definition_version": str((config.get("metadata") or {}).get("version", "unknown")),
            # The behavioural keys are recorded verbatim as well as by file
            # digest, so an inherited change reached through ``include:`` is
            # visible in the identity even though it lives in another file.
            "doc_to_text": repr(config.get("doc_to_text")),
            "doc_to_choice": repr(config.get("doc_to_choice")),
            "doc_to_target": repr(config.get("doc_to_target")),
            "target_delimiter": repr(config.get("target_delimiter")),
            "process_docs": getattr(config.get("process_docs"), "__qualname__", None),
            "filter_list": repr(config.get("filter_list")),
            "metric_list": repr(config.get("metric_list")),
            "output_type": output_type,
            "num_fewshot": config.get("num_fewshot", 0),
        }

        bound = dict(config)
        for key in structural:
            bound.pop(key, None)

        split = selection.source_split
        # Every loader key is assigned explicitly. An upstream default split, a
        # remote dataset path or a provider cache can therefore never be
        # selected by omission.
        bound["dataset_path"] = "json"
        bound["dataset_name"] = None
        bound["dataset_kwargs"] = {"data_files": {split: str(item.resolved_path)}}
        bound["training_split"] = None
        bound["validation_split"] = None
        bound["fewshot_split"] = None
        bound["test_split"] = split
        bound["task"] = selection.bound_task_name

        behavioural = set(config) - LOADER_ONLY_KEYS - structural - {"task", "metadata"}
        bound_behavioural = set(bound) - LOADER_ONLY_KEYS - {"task", "metadata"}
        if bound_behavioural != behavioural:
            raise HarnessUnavailableError(
                f"binding '{selection.leaf_task}' would change non-loader configuration "
                f"{sorted(bound_behavioural.symmetric_difference(behavioural))}; refusing "
                "rather than running an altered task definition."
            )
        for key in behavioural:
            if bound[key] is not config[key]:
                raise HarnessUnavailableError(
                    f"binding '{selection.leaf_task}' altered behavioural key '{key}'"
                )

        specs.append(bound)
        mapping[selection.bound_task_name] = item

    return specs, mapping, identities


#: Manager over the installed task index only. That index is part of the pinned
#: installed harness and cannot change while the process runs, so it is scanned
#: once. Managers over a local include path are never cached: those directories
#: are written during a run, and a stale index would silently resolve the wrong
#: task definition.
_DEFAULT_MANAGER: Any = None


def _task_manager_cached(include_path_key: str, include_defaults: bool) -> Any:
    from lm_eval.tasks import TaskManager  # noqa: PLC0415

    global _DEFAULT_MANAGER
    if not include_path_key and include_defaults:
        if _DEFAULT_MANAGER is None:
            _DEFAULT_MANAGER = TaskManager(include_path=None, include_defaults=True)
        return _DEFAULT_MANAGER
    return TaskManager(
        include_path=include_path_key or None,
        include_defaults=include_defaults,
    )


def build_task_manager(include_path: Path | None, include_defaults: bool = True) -> Any:
    """Create (or reuse) a harness TaskManager.

    Scanning the installed task index is expensive; within one process the same
    include path returns the same manager. Materialized wrapper directories are
    self-contained, so they are built with ``include_defaults=False``.
    """
    key = str(Path(include_path).resolve()) if include_path is not None else ""
    return _task_manager_cached(key, include_defaults)
