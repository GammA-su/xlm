"""Frozen production Essential-Web selector: exact B-normal, never rewritten.

Freeze ``essential-web-selector-fasttrack-v1`` fixed policy ``B`` at tier
``normal`` of the pre-registered spec
``recipes/selectors/essential_web_selector_sweep_v1.yaml`` as the
production Essential-Web selector for Mix-01. This module does not
re-implement any gate, threshold, taxonomy rule or precedence. It loads
the frozen evaluator file ``scripts/essential_web_selector_sweep.py`` and
the policy spec by path, refuses both unless their bytes hash to the
frozen identities, and delegates every decision to the evaluator's own
``validate_row`` and ``evaluate_policy``.

The Arm-T semantic review was not run; this selector is a metadata-only
fast-track decision, not a text-validated one. Only the three Essential
components are admitted; ``unassigned`` and ``rejected`` rows are not.
"""

from __future__ import annotations

import hashlib
import importlib.util
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

SELECTOR_ID = "essential-web-b-normal"
POLICY = "B"
TIER = "normal"
CONDITION = "B-normal"
FREEZE_VERSION = "essential-web-selector-fasttrack-v1"
FREEZE_DIGEST = "c6f32a65f083c99b64245e25151f2cc73275093e1013d68b625c6d6f63d10a0c"
POLICY_DIGEST = "f4357f61f434d5105d823266153dac6372e122f12767187631e8796b4899dd07"
POLICY_FILE_SHA256 = "c27a0aae8f63a1d9f80fdeb693a14d877b68298d386a3fc8679bbab34fa2a088"
EVALUATOR_SHA256 = "5a63e78560ea9e12b0ec03b5e7e63204b5c75554bee02192c12bb4ab0853bf9c"
SOURCE_REVISION = "ce4eccc7e9604667b6d7f32cb6274b8b41f3113d"
EVALUATOR_RELATIVE_PATH = "scripts/essential_web_selector_sweep.py"
POLICY_RELATIVE_PATH = "recipes/selectors/essential_web_selector_sweep_v1.yaml"
ADMITTED_COMPONENTS = ("essential_science", "essential_practical", "essential_prose")
FINAL_COMPONENTS = (*ADMITTED_COMPONENTS, "unassigned", "rejected")
_MODULE_NAME = "xlm_frozen_essential_web_selector_evaluator"


class SelectorIdentityError(RuntimeError):
    """The evaluator or policy on disk is not the frozen one: refuse to select."""


@dataclass(frozen=True)
class SelectorDecision:
    """One row's frozen B-normal outcome.

    ``final`` is exactly one of :data:`FINAL_COMPONENTS`. ``stage`` names
    where the outcome was fixed (``validity``, ``gate`` or ``component``)
    and ``reasons`` holds the evaluator's own reason codes for a rejection.
    """

    final: str
    stage: str
    reasons: tuple[str, ...]

    @property
    def admitted(self) -> bool:
        return self.final in ADMITTED_COMPONENTS


def default_repository_root() -> Path:
    """The checkout that holds this module, the evaluator and the policy spec."""
    return Path(__file__).resolve().parents[4]


def _read_frozen(path: Path, expected_sha256: str, what: str) -> bytes:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise SelectorIdentityError(f"cannot read the frozen {what} '{path}': {exc}") from exc
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise SelectorIdentityError(
            f"{what} '{path}' differs from the frozen {what}; the production "
            "selector refuses to run on changed selector logic."
        )
    return raw


class FrozenEssentialWebSelector:
    """The frozen evaluator bound to policy B, tier normal."""

    def __init__(self, evaluator: Any, spec: Mapping[str, Any]) -> None:
        self._evaluator = evaluator
        self._spec = spec

    @classmethod
    def load(cls, repository_root: Path | None = None) -> FrozenEssentialWebSelector:
        """Load the evaluator and policy by path, refusing any identity drift."""
        root = repository_root if repository_root is not None else default_repository_root()
        evaluator_path = root / EVALUATOR_RELATIVE_PATH
        evaluator_raw = _read_frozen(evaluator_path, EVALUATOR_SHA256, "evaluator")
        policy_raw = _read_frozen(root / POLICY_RELATIVE_PATH, POLICY_FILE_SHA256, "policy spec")
        module_spec = importlib.util.spec_from_file_location(_MODULE_NAME, evaluator_path)
        if module_spec is None:
            raise SelectorIdentityError(f"cannot import the frozen evaluator '{evaluator_path}'")
        module = importlib.util.module_from_spec(module_spec)
        # Execute the exact bytes that were hashed, not a second read of the file.
        sys.modules[_MODULE_NAME] = module
        exec(compile(evaluator_raw, str(evaluator_path), "exec"), module.__dict__)
        spec = yaml.safe_load(policy_raw.decode("utf-8"))
        if not isinstance(spec, dict) or module.policy_digest_of(spec) != POLICY_DIGEST:
            raise SelectorIdentityError("policy spec digest differs from the frozen policy digest")
        if tuple(module.FINAL_COMPONENTS) != FINAL_COMPONENTS:
            raise SelectorIdentityError("evaluator final components differ from the frozen set")
        if module.PINNED_REVISION != SOURCE_REVISION:
            raise SelectorIdentityError("evaluator source revision differs from the frozen pin")
        if spec["policies"][POLICY][TIER] != "GN" or list(spec["precedence"]) != [
            "science",
            "practical",
            "prose",
        ]:
            raise SelectorIdentityError("policy B normal gate or precedence differs")
        return cls(module, spec)

    def decide(self, record: Mapping[str, Any]) -> SelectorDecision:
        """Frozen B-normal outcome for one upstream row (``text`` is never read)."""
        fields, reasons, _ = self._evaluator.validate_row(record, self._spec)
        if reasons:
            return SelectorDecision("rejected", "validity", tuple(reasons))
        result = self._evaluator.evaluate_policy(fields, POLICY, TIER, self._spec)
        if not result["gate_pass"]:
            return SelectorDecision("rejected", "gate", tuple(result["gate_reasons"]))
        final = str(result["final"])
        if final not in FINAL_COMPONENTS:
            raise SelectorIdentityError(f"evaluator returned an unknown final '{final}'")
        return SelectorDecision(final, "component", ())


def selector_identity() -> dict[str, str]:
    """Data-only identity of the frozen production selector."""
    return {
        "selector_id": SELECTOR_ID,
        "condition": CONDITION,
        "policy_digest": POLICY_DIGEST,
        "evaluator_sha256": EVALUATOR_SHA256,
        "freeze_version": FREEZE_VERSION,
        "freeze_digest": FREEZE_DIGEST,
    }
