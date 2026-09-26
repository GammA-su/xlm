"""P35 pilot readiness: one bounded AUTHORED/SYNTHETIC flow (not the pilot; no training).

Run from the repository root:

    uv run --offline --locked --no-sync --extra cuda --extra eval python \
        docs/implementation/evidence/P35-READINESS/synthetic_flow.py <output.json>

Part A (recoverability, pure planner/ledger/retention decisions):
  C=0 barrier with an injected search failure and a retry; a small quick
  threshold without a milestone gets an evaluation-recovery checkpoint at its
  natural first crossing; an injected evaluation failure keeps that state
  pinned; a (simulated) exact rescore completes the event and releases it.
Part B (fairness receipt): real authored token shards and the real
  MixtureBatcher; two consecutive global updates partitioned 8/16/32 sequences
  per microbatch and through the P34 producer encoder give one digest and one
  chain. Part C measures digest overhead on synthetic pilot-shaped arrays.

Bounds: no targets are trained; < 10 MB written; < 60 s. Torch is not needed.
The Trainer/queue execution of Part A is covered by
``tests/test_p35_readiness_runtime.py`` (local CUDA certification).
"""

from __future__ import annotations

import json
import statistics
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "tests"))

import numpy as np  # noqa: E402

from xlm.artifacts.retention import RetentionCandidate, decide_retention  # noqa: E402
from xlm.data.sampling import MixtureBatcher  # noqa: E402
from xlm.data.sampling.prefetch import _encode  # noqa: E402
from xlm.data.sampling.update_payload import (  # noqa: E402
    UpdatePayloadChain,
    canonical_from_microbatches,
    canonical_from_prepared,
)
from xlm.evaluation.cadence import AUTHORED_FIXTURE, build_plan  # noqa: E402
from xlm.evaluation.receipts import EvaluationLedger  # noqa: E402
from xlm.evaluation.recoverability import (  # noqa: E402
    RecoverabilityPolicy,
    barrier_blockers,
    checkpoint_plan_with_recovery,
    live_retry_candidates,
    recoverability_table,
    required_incomplete,
    unrecoverable_required,
)
from xlm.tokenizers.byte import ByteTokenizer  # noqa: E402

POLICY = RecoverabilityPolicy.from_config(
    {
        "version": "xlm-evaluation-recoverability-v1",
        "required_events": "all_planned_events_v1",
        "initial_barrier": "required_initial_evaluation_barrier_v1",
        "recovery_checkpoints": "evaluation_recovery_checkpoints_v1",
        "endpoint": "endpoint_live_retry_then_fail_stop_v1",
    }
)
BUDGET, BATCH = 4096, 256


def attempt(ledger: EvaluationLedger, event_id: str, number: int, status: str) -> None:
    record = ledger.records[event_id]
    ledger.adopt_attempt(
        record,
        {
            "number": number,
            "status": status,
            "computation_identity": f"comp-{event_id}",
            "outcome_artifact": f"SYNTHETIC-{event_id}-a{number}",
            "model_state_digest": record.due["model_state_digest"],  # type: ignore[index]
            "actual_committed_targets": record.due["actual_committed_targets"],  # type: ignore[index]
            "failure": None if status == "complete" else {"type": "AuthoredFailure"},
        },
    )


def part_a() -> dict[str, Any]:
    plan = build_plan(
        AUTHORED_FIXTURE,
        BUDGET,
        confirmation_registered=False,
        fixture_thresholds={"quick_lm": [0, 1000, BUDGET], "search_benchmark": [0, BUDGET]},
    )
    checkpoints = checkpoint_plan_with_recovery(
        {"cadence": AUTHORED_FIXTURE, "fixture_milestones": [0, BUDGET], "fixture_recovery": []},
        BUDGET,
        evaluation_plan=plan,
        policy=POLICY,
    )
    ledger = EvaluationLedger(plan, {"quick_lm": "q" * 64, "search_benchmark": "s" * 64})
    trace: list[str] = []

    def cross(event_id: str, committed: int) -> None:
        ledger.mark_due(
            plan.event(event_id),
            committed=committed,
            step=committed // BATCH,
            digest=f"state-{committed}".ljust(64, "0"),
            computation_identity=f"comp-{event_id}",
        )

    for event_id in ("quick_lm@0", "search_benchmark@0"):
        cross(event_id, 0)
    attempt(ledger, "quick_lm@0", 1, "complete")
    attempt(ledger, "search_benchmark@0", 1, "failed")  # injected failure
    blocked = barrier_blockers(ledger, POLICY)
    trace.append(f"C=0 barrier after the injected search failure: {blocked}")
    retry = live_retry_candidates(ledger, 0, POLICY)
    attempt(ledger, "search_benchmark@0", 2, "complete")  # retry at C=0
    opened = barrier_blockers(ledger, POLICY)
    trace.append(f"retry {retry} at C=0 -> barrier {opened or 'OPEN'}: update 1 may begin")

    # Natural first crossing of quick_lm@1000 at batch 256: update 4, C = 1024.
    due_ckpt = [e.event_id for e in checkpoints.due(1024, handled=["checkpoint@0"])]
    cross("quick_lm@1000", 1024)
    attempt(ledger, "quick_lm@1000", 1, "failed")  # injected live failure
    states = [
        RetentionCandidate(
            "t0",
            "milestone",
            0,
            0,
            1,
            ledger.records["quick_lm@0"].due["model_state_digest"],
            "m" * 64,
        ),  # type: ignore[index]  # noqa: E501
        RetentionCandidate(
            "t1000",
            "evaluation_recovery",
            1024,
            4,
            1,
            ledger.records["quick_lm@1000"].due["model_state_digest"],
            "m" * 64,
        ),  # type: ignore[index]  # noqa: E501
        RetentionCandidate("t4096", "milestone", BUDGET, 16, 1, "final".ljust(64, "0"), "m" * 64),
    ]

    def dependencies() -> dict[str, list[str]]:
        deps: dict[str, list[str]] = {}
        for record in ledger.ordered():
            if record.due is not None and record.status.value != "complete":
                deps.setdefault(record.due["model_state_digest"], []).append(record.event_id)
        return deps

    pinned = decide_retention(states, protected={}, evaluation_dependencies=dependencies())
    attempt(ledger, "quick_lm@1000", 2, "complete")  # exact rescore from t1000 (simulated)
    released = decide_retention(states, protected={}, evaluation_dependencies=dependencies())
    for event_id in ("quick_lm@4096", "search_benchmark@4096"):
        cross(event_id, BUDGET)
        attempt(ledger, event_id, 1, "complete")
    return {
        "evaluation_recovery_checkpoints": [
            e.event_id for e in checkpoints.events if e.role.value == "evaluation_recovery"
        ],
        "checkpoint_due_at_first_crossing_c1024": due_ckpt,
        "barrier": {"after_failure": blocked, "retried": retry, "after_retry": opened},
        "t1000_while_failed": pinned.keep.get("t1000"),
        "t1000_after_rescore": released.retire.get("t1000"),
        "attempts_quick_lm@1000": [
            (a["number"], a["status"]) for a in ledger.records["quick_lm@1000"].attempts
        ],
        "canonical_quick_lm@1000": ledger.records["quick_lm@1000"].canonical,
        "required_incomplete_at_budget": required_incomplete(ledger, POLICY),
        "unrecoverable_required": unrecoverable_required(
            recoverability_table(plan, checkpoints, POLICY)
        ),
        "trace": trace,
    }


def part_b(workdir: Path) -> dict[str, Any]:
    from p35_m5_support import recipe, write_sources

    readers = write_sources(workdir / "shards")
    tokenizer = ByteTokenizer()
    digests: dict[str, list[str]] = {}
    heads: dict[str, str] = {}
    partitions: dict[str, list[int]] = {}
    for label, group in (("B8", 8), ("B16", 16), ("B32", 32)):
        stream = MixtureBatcher(
            recipe(),
            dict(readers),
            context_length=16,
            global_batch_valid_targets=16 * 40,
            microbatch_sequences=group,
            pad_token_id=tokenizer.pad_token_id,
            bos_token_id=tokenizer.bos_token_id,
            eos_token_id=tokenizer.eos_token_id,
        )
        chain = UpdatePayloadChain()
        digests[label] = []
        before = 0
        for step in (1, 2):
            batches = stream.next_step_microbatches()
            canonical = canonical_from_microbatches(batches)
            if group == 8:
                produced = canonical_from_prepared(_encode(batches, 0, 0, None, "s", None, 0.0))
                digests["P34_producer_B8"] = digests.get("P34_producer_B8", []) + [
                    produced.digest()
                ]
            partitions.setdefault(label, []).append(len(batches))
            chain.stage(
                step=step,
                committed_before=before,
                valid_targets=canonical.valid_targets,
                payload=canonical.digest(),
            )
            chain.commit()
            stream.commit()
            before += canonical.valid_targets
            digests[label].append(canonical.digest())
        heads[label] = chain.head
    return {
        "microbatches_per_update": partitions,
        "digests": digests,
        "chain_heads": heads,
        "all_digests_equal": len({tuple(v) for v in digests.values()}) == 1,
        "all_heads_equal": len(set(heads.values())) == 1,
    }


def part_c() -> dict[str, Any]:
    """Digest cost on SYNTHETIC pilot-shaped updates (128 x 512; B8 = 16 microbatches)."""
    rng = np.random.default_rng(35)
    n, t = 128, 512
    ints = {k: rng.integers(0, 32768, size=(n, t)) for k in ("input_ids", "labels")}
    mask = np.ones((n, t), dtype=np.int64)
    positions = np.tile(np.arange(t), (n, 1))
    table = tuple(f"doc_{i}" for i in range(4096))
    codes = rng.integers(0, len(table), size=(n, t)).astype(np.int32)
    from types import SimpleNamespace

    prepared = SimpleNamespace(
        rows=tuple([8] * 16),
        metadata=tuple({"packing_mode": "causal_stream"} for _ in range(16)),
        input_ids=ints["input_ids"],
        labels=ints["labels"],
        loss_mask=mask,
        position_ids=positions,
        segment_ids=np.zeros((n, t), dtype=np.int64),
        input_attention_mask=np.ones((n, t), dtype=bool),
        provenance=SimpleNamespace(
            strings=table,
            attribution=codes % 12,
            doc_ids=codes,
            lineage_ids=codes,
            byte_spans=np.stack([positions, positions + 1], axis=-1),
            token_offsets=positions,
        ),
    )
    producer = []
    for _ in range(20):
        began = time.perf_counter()
        canonical_from_prepared(prepared).digest()
        producer.append(time.perf_counter() - began)
    lists = [
        SimpleNamespace(
            input_ids=ints["input_ids"][i : i + 8].tolist(),
            labels=ints["labels"][i : i + 8].tolist(),
            loss_mask=mask[i : i + 8].tolist(),
            position_ids=positions[i : i + 8].tolist(),
            segment_ids=[[0] * t] * 8,
            source_attribution=[[table[c % 12] for c in row] for row in codes[i : i + 8].tolist()],
            metadata={
                "packing_mode": "causal_stream",
                "input_attention_mask": [[1] * t] * 8,
                "target_doc_ids": [[table[c] for c in row] for row in codes[i : i + 8].tolist()],
                "target_lineage_ids": [
                    [table[c] for c in row] for row in codes[i : i + 8].tolist()
                ],
                "target_byte_spans": [[(p, p + 1) for p in range(t)]] * 8,
                "target_token_offsets": positions[i : i + 8].tolist(),
            },
        )
        for i in range(0, n, 8)
    ]
    synchronous = []
    for _ in range(5):
        began = time.perf_counter()
        canonical_from_microbatches(lists).digest()
        synchronous.append(time.perf_counter() - began)
    update_seconds = 65_536 / 45_679  # P34 B8 planning rate (§Q), not a new measurement
    producer_ms = statistics.median(producer) * 1000
    sync_ms = statistics.median(synchronous) * 1000
    return {
        "shape": [n, t],
        "producer_path_median_ms": round(producer_ms, 3),
        "synchronous_list_path_median_ms": round(sync_ms, 3),
        "repetitions": {"producer": len(producer), "synchronous": len(synchronous)},
        "planning_update_seconds_at_p34_b8_rate": round(update_seconds, 4),
        "producer_path_percent_of_planning_update": round(
            100 * producer_ms / 1000 / update_seconds, 3
        ),
        "synchronous_path_percent_of_planning_update": round(
            100 * sync_ms / 1000 / update_seconds, 3
        ),
        "note": "CPU-only measurement; SYNTHETIC arrays; no GPU; no device sync involved",
    }


def main(output: Path) -> None:
    began = time.perf_counter()
    with tempfile.TemporaryDirectory() as scratch:
        result = {
            "label": "SYNTHETIC / AUTHORED - not the pilot, no training",
            "part_a_recoverability": part_a(),
            "part_b_fairness": part_b(Path(scratch)),
            "part_c_overhead": part_c(),
        }
    result["wall_seconds"] = round(time.perf_counter() - began, 3)
    if sys.platform == "win32":
        import psutil

        result["peak_rss_mib"] = round(psutil.Process().memory_info().peak_wset / 1024**2, 1)
        result["peak_rss_measurement"] = "Windows peak working set (psutil peak_wset)"
    else:
        import resource

        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        result["peak_rss_mib"] = round(peak / (1024**2 if sys.platform == "darwin" else 1024), 1)
        result["peak_rss_measurement"] = "resource.getrusage ru_maxrss"
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({k: result[k] for k in ("wall_seconds", "peak_rss_mib")}))


if __name__ == "__main__":
    main(Path(sys.argv[1]))
