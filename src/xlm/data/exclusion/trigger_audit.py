"""Content-free injected-copy recall of the c05-production-v3 trigger matcher (operator).

Read-only acceptance gate for c05-trigger-floors-v1, run after the protected
preparation and before planning (protected volume attached):

1. the policy must be c05-production-v3 and its generation matcher must be the
   receipt's (``receipt.policy_digest``); the index must equal the receipt (SHA-256,
   bytes) and every material file must equal the receipt (size, SHA-256);
2. the trigger-filtered matcher is compiled with exactly the C05 run's code
   (:func:`compact.prepare` with the policy's trigger) into an EMPTY protected scratch
   directory beside the protected root;
3. the uncovered-item count must equal the policy's reviewed count;
4. every benchmark item is rendered in the audit's injection forms
   (:func:`candidates.injection_forms`: whole label-free item, prompt only, one BLiMP
   sentence), embedded between neutral filler tokens and matched;
5. optional ``expect`` values (production audit evidence) are compared; any
   difference is a blocking refusal (exit 2).

Output: per task/split/form item and detection counts, and totals. No text, token,
pattern, provenance or item identity leaves the process.
"""

from __future__ import annotations

import time
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from xlm.data.exclusion import candidates as cand
from xlm.data.exclusion.artifacts import MaterialFile
from xlm.data.exclusion.compact import prepare
from xlm.data.exclusion.policy import C05Error, ProductionPolicyV3, production_policy
from xlm.data.exclusion.prepare_workers import _rows, plan_tasks
from xlm.data.exclusion.runner import file_sha

MAX_RECORD = 64 * 1024 * 1024
EXPECT_KEYS = (
    "items_without_active_trigger",
    "whole_item_detected",
    "blimp_single_sentence_detected",
)


def recall(
    receipt: Mapping[str, Any],
    policy_value: Mapping[str, Any],
    index: Path,
    material_root: Path,
    scratch: Path,
    *,
    mode: str = "protected",
    expect: Mapping[str, int] | None = None,
    inspector: Any = None,
) -> dict[str, Any]:
    started = time.monotonic()
    policy = production_policy(dict(policy_value))
    if not isinstance(policy, ProductionPolicyV3):
        raise C05Error("trigger recall needs a c05-production-v3 policy")
    if receipt.get("policy_digest") != policy.matcher.identity():
        raise C05Error("receipt was generated under another matcher policy")
    size = index.stat().st_size
    if size != receipt["index_bytes"] or file_sha(index) != receipt["index_sha256"]:
        raise C05Error("protected index differs from the benchmark receipt")
    if mode == "protected":
        from xlm.data.exclusion.matcher_audit import protected_scratch

        protected_scratch(index, scratch, inspector)
    directory = scratch / "trigger-matcher"
    if scratch.exists() and any(scratch.iterdir()):
        raise C05Error("trigger recall scratch must be empty")
    files = [MaterialFile.model_validate(f) for f in receipt["files"]]
    paths = [material_root / f.path for f in files]
    for entry, path in zip(files, paths, strict=True):
        if not path.is_file() or path.stat().st_size != entry.bytes:
            raise C05Error("benchmark material differs from the benchmark receipt")
        if file_sha(path) != entry.sha256:
            raise C05Error("benchmark material differs from the benchmark receipt")
    trigger = policy.trigger
    with prepare(
        directory,
        index,
        index_sha256=receipt["index_sha256"],
        index_bytes=size,
        max_record=MAX_RECORD,
        max_records=None,
        max_logical_nodes=None,
        trigger=trigger,
    ) as matcher:
        section = dict(matcher.manifest["trigger"])
        uncovered = int(receipt["items"]) - int(section["active_items"])
        items: Counter[tuple[str, str]] = Counter()
        detected: Counter[tuple[str, str]] = Counter()
        for task in plan_tasks(paths, files, MAX_RECORD):
            entry = files[task.file]
            group = f"{entry.task}/{entry.split}"
            for row in _rows(task, MAX_RECORD, lambda: None):
                for form, text in cand.injection_forms(entry.task, row):
                    tokens = cand.injected(text, matcher.vocabulary)
                    items[(group, form)] += 1
                    detected[(group, form)] += matcher.match(tokens) is not None
    table: dict[str, dict[str, dict[str, int]]] = {}
    for (group, form), n in sorted(items.items()):
        table.setdefault(group, {})[form] = {"items": n, "detected": detected[(group, form)]}

    def total(form: str, task: str | None = None) -> tuple[int, int]:
        keys = [k for k in items if k[1] == form and (task is None or k[0].startswith(task + "/"))]
        return sum(items[k] for k in keys), sum(detected[k] for k in keys)

    whole_items, whole_detected = total("item_composite")
    if whole_items != int(receipt["items"]):
        raise C05Error("trigger recall did not render every benchmark item")
    single_items, single_detected = total("single_sentence", "blimp")
    observed = {
        "items_without_active_trigger": uncovered,
        "whole_item_detected": whole_detected,
        "blimp_single_sentence_detected": single_detected,
    }
    mismatches = {
        k: {"expected": v, "observed": observed[k]}
        for k, v in (expect or {}).items()
        if observed[k] != v
    }
    return {
        "kind": "c05_trigger_recall_v1",
        "content_free": True,
        "mode": mode,
        "policy_digest": policy.identity(),
        "trigger_policy_digest": trigger.identity(),
        "reviewed_items_without_active_trigger": trigger.reviewed_items_without_active_trigger,
        "trigger": section,
        "benchmark_items": int(receipt["items"]),
        "whole_item": {"items": whole_items, "detected": whole_detected},
        "whole_item_by_task": {
            task: dict(zip(("items", "detected"), total("item_composite", task), strict=True))
            for task in sorted({f.task for f in files})
        },
        "blimp_single_sentence": {"items": single_items, "detected": single_detected},
        "by_task_split_and_form": table,
        "coverage_matches_review": uncovered == trigger.reviewed_items_without_active_trigger,
        "expectation_mismatches": mismatches,
        "accepted": not mismatches and uncovered == trigger.reviewed_items_without_active_trigger,
        "elapsed_seconds": round(time.monotonic() - started, 1),
    }
