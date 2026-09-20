"""Bounded adversarial reproduction of P23 defects D04 and D05.

Runs the real production entry points (``resolve_suite``, ``run_harness_suite``,
``materialize_pinned_tasks``) against authored synthetic inputs and the pinned
installed harness. Nothing is mocked: the harness really scores the authored
items with a tiny authored model on CPU. No network, no official data.

Each case prints a JSON record with the observed behaviour so the before-state
is preserved verbatim.
"""

from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path
from typing import Any

WORK = Path(sys.argv[1]).resolve()
REPO = Path(__file__).resolve().parents[5]
TASKS = WORK / "tasks"
CKPT = WORK / "model_ckpt"

from xlm.evaluation.harness import materialize_pinned_tasks  # noqa: E402
from xlm.evaluation.harness_runner import run_harness_suite  # noqa: E402
from xlm.evaluation.suites import (  # noqa: E402
    SuiteTier,
    TaskVariant,
    load_dataset_pins,
    resolve_suite,
)
from xlm.models.serialization import load_model_for_inference  # noqa: E402
from xlm.tokenizers.byte import ByteTokenizer  # noqa: E402

RESULTS: list[dict[str, Any]] = []


def record(case: str, question: str, **payload: Any) -> None:
    RESULTS.append({"case": case, "question": question, **payload})


def mc_variant(task: str, tier: SuiteTier = SuiteTier.SEARCH) -> TaskVariant:
    return TaskVariant(
        variant_id=f"xlm_{task}_{tier.value}",
        lm_eval_task=task,
        tier=tier,
        split="train",
        normalized_metric="acc_norm",
        chance_reference="mean_inverse_n_choices",
        dataset_revision=None,
    )


def blimp_variant(subdatasets: tuple[str, ...], metric: str = "acc") -> TaskVariant:
    return TaskVariant(
        variant_id="xlm_blimp_search",
        lm_eval_task="blimp",
        tier=SuiteTier.SEARCH,
        split="train",
        normalized_metric=metric,
        chance_reference="fixed",
        chance_value=0.5,
        dataset_revision=None,
        blimp_subdatasets=subdatasets,
    )


# ---------------------------------------------------------------- D04 case 1
# The ordinary developer suite, resolved from the real pins file, is refused
# outright: there is no route at all to an explicitly selected, verified local
# development input under the frozen protocol.
pins = load_dataset_pins(REPO / "manifests" / "eval_dataset_pins.yaml")
variants = resolve_suite(SuiteTier.SEARCH, pins, blimp_subdatasets=["blimp_a", "blimp_b"])
model = load_model_for_inference(CKPT, device="cpu")
tokenizer = ByteTokenizer()
try:
    run_harness_suite(
        model=model,
        tokenizer=tokenizer,
        variants=variants,
        checkpoint_hash="syn_ckpt",
        device="cpu",
        include_path=TASKS,
        output_dir=WORK / "out_d04_1",
        use_evidence_cache=False,
    )
    record("D04-1", "default search suite from pinned revisions", outcome="RAN (unexpected)")
except Exception as exc:  # noqa: BLE001 - recording observed behaviour
    record(
        "D04-1",
        "default search suite from pinned revisions",
        outcome="REFUSED",
        error_type=type(exc).__name__,
        error=str(exc),
        variant_revisions={v.lm_eval_task: v.dataset_revision for v in variants},
    )

# ---------------------------------------------------------------- D04 case 2
# Even with a verified local snapshot on disk there is no way to combine the
# OFFICIAL task definition (prompt template, doc_to_choice, acc_norm) with that
# local split-scoped data: materialization refuses any task whose installed YAML
# is not already ``dataset_path: json``.
local_snapshot = WORK / "verified_local" / "arc_easy_search.json"
local_snapshot.parent.mkdir(parents=True, exist_ok=True)
local_snapshot.write_text(
    json.dumps([{"id": "syn_local_0", "question": "q", "choices": ["a", "b"], "answer": 0}]),
    encoding="utf-8",
)
try:
    from xlm.evaluation.harness import build_task_manager

    materialize_pinned_tasks(
        [mc_variant("arc_easy")],
        WORK / "out_d04_2" / "tasks",
        build_task_manager(None),
    )
    record("D04-2", "official arc_easy definition over verified local data", outcome="ALLOWED")
except Exception as exc:  # noqa: BLE001
    record(
        "D04-2",
        "official arc_easy definition over verified local data",
        outcome="REFUSED",
        error_type=type(exc).__name__,
        error=str(exc),
        local_snapshot=str(local_snapshot),
        note="no manifest/route exists to bind a verified local split to the official task",
    )

# ---------------------------------------------------------------- D05 case 0
# NEW DEFECT found while reproducing D05: the BLiMP branch of run_harness_suite
# reads metrics.get("acc"), but the pinned harness emits filter-suffixed keys
# ("acc,none"). Every BLiMP subdataset therefore raises. BLiMP has never been
# executable through this runner, so BLiMP macro coverage could not have been
# exercised end to end by any existing test.
try:
    run_harness_suite(
        model=model,
        tokenizer=tokenizer,
        variants=[blimp_variant(("blimp_syn_alpha",))],
        checkpoint_hash="syn_ckpt",
        device="cpu",
        include_path=TASKS,
        output_dir=WORK / "out_d05_0",
        use_evidence_cache=False,
    )
    record("D05-0", "BLiMP subdataset through run_harness_suite", outcome="RAN")
except Exception as exc:  # noqa: BLE001
    record(
        "D05-0",
        "BLiMP subdataset through run_harness_suite",
        outcome="RAISED",
        error_type=type(exc).__name__,
        error=str(exc),
        harness_metric_keys=["acc,none", "acc_norm,none", "acc_stderr,none", "acc_norm_stderr,none"],
        note="metrics.get(metric_name) misses the ',none' filter suffix that _metric_value handles",
    )

# The remaining D05 cases use metric key "acc,none" purely to step past D05-0
# and reach the coverage logic underneath. That override is a reproduction
# scaffold, not a proposed fix.
# ---------------------------------------------------------------- D05 case 1
# All four task names present, --limit 2 of 6 authored items per task: the run
# still reports a COMPLETE four-task index.
authored = [
    mc_variant("arc_easy"),
    mc_variant("hellaswag"),
    mc_variant("piqa"),
    blimp_variant(("blimp_syn_alpha", "blimp_syn_beta"), metric="acc,none"),
]
evidence = run_harness_suite(
    model=model,
    tokenizer=tokenizer,
    variants=authored,
    checkpoint_hash="syn_ckpt",
    device="cpu",
    limit=2,
    include_path=TASKS,
    output_dir=WORK / "out_d05_1",
    use_evidence_cache=False,
)
record(
    "D05-1",
    "four tasks, limit=2 of 6 authored items each",
    index_complete=evidence.index.complete,
    index_value=evidence.index.index,
    index_missing=evidence.index.missing,
    per_task={
        name: {"scored": t.scored_items, "total_items_reported": t.total_items}
        for name, t in sorted(evidence.tasks.items())
    },
    authored_items_per_mc_task=6,
    notes=evidence.notes,
)

# ---------------------------------------------------------------- D05 case 2
# The declared BLiMP scope has two subdatasets; only one is requested/returned.
# The suite still reports a complete index and a macro score over one member.
partial_blimp = [
    mc_variant("arc_easy"),
    mc_variant("hellaswag"),
    mc_variant("piqa"),
    blimp_variant(("blimp_syn_alpha",), metric="acc,none"),
]
evidence2 = run_harness_suite(
    model=model,
    tokenizer=tokenizer,
    variants=partial_blimp,
    checkpoint_hash="syn_ckpt",
    device="cpu",
    include_path=TASKS,
    output_dir=WORK / "out_d05_2",
    use_evidence_cache=False,
)
record(
    "D05-2",
    "declared BLiMP scope of 2 subdatasets, only 1 executed",
    index_complete=evidence2.index.complete,
    index_value=evidence2.index.index,
    index_missing=evidence2.index.missing,
    blimp_subdataset_scores=dict(evidence2.tasks["blimp"].subdataset_scores),
    declared_subdatasets=["blimp_syn_alpha", "blimp_syn_beta"],
    notes=evidence2.notes,
)

# ---------------------------------------------------------------- D05 case 3
# Expected population is never declared anywhere: TaskEvidence.total_items is
# assigned from the returned sample list itself.
record(
    "D05-3",
    "is the expected population independent of returned predictions?",
    harness_runner_line="task_evidence_from_items(..., total_items=len(items))",
    observed_total_items={n: t.total_items for n, t in sorted(evidence.tasks.items())},
    conclusion="expected population == returned population by construction",
)

# ---------------------------------------------------------------- D05 case 4
# Item identity is positional. The declared item id carried in the authored doc
# ("syn_alpha_0", ...) never reaches the evidence rows, so a manifest cannot
# check WHICH items were scored - only how many.
sample_items = evidence.tasks["arc_easy"].items
record(
    "D05-4",
    "does per-item evidence carry the declared item id?",
    evidence_item_ids=[i.item_id for i in sample_items],
    declared_item_ids_in_authored_data=["syn_arc_0", "syn_arc_1", "syn_arc_2"],
    conclusion="item_id is the harness positional doc_id; declared ids are dropped",
)

print(json.dumps({"results": RESULTS}, indent=2, sort_keys=False))
out = Path(__file__).with_name("repro_results.json")
out.write_text(json.dumps({"results": RESULTS}, indent=2), encoding="utf-8")
