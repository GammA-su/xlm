"""P35 M5 bounded synthetic order flow: AUTHORED shards, no model, no training.

Two authored sources, eleven documents, two independent order seeds. Proves at
the data/evidence level: equal membership, different orders, identical whole-
document content, stream and producer following the frozen order, exact
same-order resume, cross-order refusal, real order ids in M4 evidence and a
validating order-evidence bundle. Every number is SYNTHETIC. Usage (repo root):

    uv run --offline --locked python docs/implementation/evidence/P35-M5/toy_order_flow.py OUT_DIR
"""

from __future__ import annotations

import copy
import json
import resource
import shutil
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "tests"))

from p35_m4_support import mixture_pair_runs, superiority_manifest  # noqa: E402
from p35_m5_support import (  # noqa: E402
    ORDER_SEED_A,
    ORDER_SEED_B,
    TEXTS,
    batcher,
    target_doc_sequence,
    write_sources,
)
from test_p35_m4_evidence import _extract, publish_run  # noqa: E402
from xlm.comparison.science_compare import compare_science  # noqa: E402
from xlm.comparison.science_order import (  # noqa: E402
    build_order_bundle,
    build_order_declaration,
    bundle_problems,
)
from xlm.data.ordering import (  # noqa: E402
    OrderedSourceIndex,
    build_membership,
    build_order_manifest,
    independence_problems,
    write_order_manifest,
)
from xlm.data.sampling import MixtureStreamError  # noqa: E402
from xlm.data.sampling.prefetch import ProducerSpec  # noqa: E402

CTRL = {"C0": 3.00, "C1": 3.10, "C2": 3.20, "C3": 3.05, "C4": 3.15}
WIN = {"C0": 2.98, "C1": 3.07, "C2": 3.19, "C3": 3.02, "C4": 3.12}


def _collapse(sequence: list[str]) -> list[str]:
    out: list[str] = []
    for item in sequence:
        if not out or out[-1] != item:
            out.append(item)
    return out


def _run(stream: Any, updates: int) -> list[Any]:
    batches: list[Any] = []
    for _ in range(updates):
        batches += stream.next_step_microbatches()
        stream.commit()
    return batches


def main(out: Path) -> dict[str, Any]:
    started = time.perf_counter()
    scratch = out / "_scratch"
    if scratch.exists():
        shutil.rmtree(scratch)
    readers = write_sources(scratch / "shards")
    membership = build_membership(readers)
    order_a = build_order_manifest(membership, order_seed=ORDER_SEED_A)
    order_b = build_order_manifest(membership, order_seed=ORDER_SEED_B)
    orders_dir = out / "orders"
    for path in orders_dir.glob("*.json"):
        path.unlink()
    path_a = write_order_manifest(order_a, orders_dir / "order_a.json")
    path_b = write_order_manifest(order_b, orders_dir / "order_b.json")

    # Whole-document content is identical under both orders.
    content_identical = True
    for source, reader in readers.items():
        physical = {r["doc_id"]: r for r in reader.read_document_offsets()}
        for order in (order_a, order_b):
            index = OrderedSourceIndex(reader, order["sources"][source]["ordered_doc_ids"])
            position = 0
            for doc_id in order["sources"][source]["ordered_doc_ids"]:
                count = physical[doc_id]["token_count"]
                tokens = index.read(reader.read_tokens_mmap, position, count)
                expected = reader.read_tokens(physical[doc_id]["token_start"], count)
                content_identical &= tokens == expected
                position += count

    streams: dict[str, Any] = {}
    for label, order in (("shard_native", None), ("order_a", order_a), ("order_b", order_b)):
        stream = batcher(readers, order)
        batches = _run(stream, 8)
        state = stream.get_state()
        streams[label] = {
            "first_documents": {
                s: _collapse([d for d in target_doc_sequence(batches) if d.startswith(f"{s}_")])
                for s in TEXTS
            },
            "trace_digest": state["trace_digest"],
            "document_order": state.get("document_order"),
            "committed_valid_targets": state["committed_valid_targets"],
        }

    reference = _run(batcher(readers, order_a), 8)
    first = batcher(readers, order_a)
    head = _run(first, 4)
    resumed = batcher(readers, copy.deepcopy(order_a))
    resumed.load_state(json.loads(json.dumps(first.get_state())))
    tail = _run(resumed, 4)
    exact_resume = [b.input_ids for b in head + tail] == [b.input_ids for b in reference]
    try:
        batcher(readers, order_b).load_state(first.get_state())
        cross_order = "ACCEPTED (defect)"
    except MixtureStreamError as exc:
        cross_order = f"REFUSED: {exc}"
    rebuilt = ProducerSpec.from_batcher(batcher(readers, order_a)).build()
    producer_same = [b.input_ids for b in _run(rebuilt, 8)] == [b.input_ids for b in reference]

    # M4 evidence: authored checkpoints whose receipts carry the real stream state.
    extracted = {}
    for label, order in (("order_a", order_a), ("order_b", order_b), ("pre_m5", None)):
        config_order = state_order = None
        if order is not None:
            state_order = batcher(readers, order).get_state()["document_order"]
            config_order = {
                "manifest": str((orders_dir / f"{label}.json").resolve()),
                "order_manifest_id": order["order_manifest_id"],
                "canonical_membership_id": order["canonical_membership_id"],
            }
        checkpoint = publish_run(
            scratch / "store" / label,
            f"toy-{label}",
            (401, 20001, 20261001),
            config_order=config_order,
            state_order=state_order,
        )
        fields = _extract(checkpoint)["fields"]
        extracted[label] = {
            k: fields[k]
            for k in ("order_manifest_id", "canonical_membership_id", "within_source_order_policy")
        }

    manifest = superiority_manifest()
    declaration = build_order_declaration(
        [order_a, order_b], [e["tuple_id"] for e in manifest["replicate_roster"]]
    )
    for entry in manifest["replicate_roster"]:
        entry["order_manifest_id"] = declaration["allocation"][entry["tuple_id"]]
    manifest["order_robustness"] = {"required": True, "m5_order_evidence": declaration}
    manifest["scale_promotion"].update(
        intent=True, resource_plan_ref="SYNTHETIC RP", ablation_refs=["SYNTHETIC AB"]
    )
    runs = mixture_pair_runs(manifest, CTRL, WIN)
    record = compare_science(manifest, runs)
    (candidate,) = record["candidates"]
    bundle_runs = [(r.label, r.arm_id, r.evidence) for r in runs]
    retry = copy.deepcopy(bundle_runs[0])
    bundle = build_order_bundle(manifest, [*bundle_runs, (retry[0] + "-retry", *retry[1:])])
    (out / "order_evidence_bundle.json").write_text(
        json.dumps(bundle, indent=1, sort_keys=True) + "\n", encoding="utf-8"
    )

    payload_bytes = sum(
        (r.directory / name).stat().st_size
        for r in readers.values()
        for name in ("tokens.bin", "offsets.jsonl")
    )
    token_bytes = sum((r.directory / "tokens.bin").stat().st_size for r in readers.values())
    summary = {
        "label": "P35 M5 SYNTHETIC toy order flow (authored shards; no model, no training)",
        "documents": {s: len(t) for s, t in TEXTS.items()},
        "canonical_membership_id": membership.membership_id,
        "membership_equal": order_a["canonical_membership_id"]
        == order_b["canonical_membership_id"],
        "order_a": {"seed": ORDER_SEED_A, "order_manifest_id": order_a["order_manifest_id"]},
        "order_b": {"seed": ORDER_SEED_B, "order_manifest_id": order_b["order_manifest_id"]},
        "ordered_doc_ids": {
            s: {
                "order_a": order_a["sources"][s]["ordered_doc_ids"],
                "order_b": order_b["sources"][s]["ordered_doc_ids"],
            }
            for s in TEXTS
        },
        "independence_problems": independence_problems(order_a, order_b),
        "whole_document_content_identical": content_identical,
        "streams": streams,
        "same_order_resume_exact": exact_resume,
        "cross_order_resume": cross_order,
        "producer_spec_rebuild_identical": producer_same,
        "m4_extracted": extracted,
        "m4_order_robustness": candidate["promotion"]["requirements"].get("order_robustness"),
        "m4_promotion_state": candidate["promotion"]["state"],
        "bundle_hash": bundle["bundle_hash"],
        "bundle_problems": bundle_problems(bundle),
        "initialization_counts": bundle["initialization_counts"]["by_order"],
        "overhead": {
            "token_payload_bytes": token_bytes,
            "token_plus_index_bytes": payload_bytes,
            "order_manifest_bytes": {
                "order_a": path_a.stat().st_size,
                "order_b": path_b.stat().st_size,
            },
            "duplicated_token_bytes": 0,
        },
        "resources": {
            "wall_seconds": round(time.perf_counter() - started, 3),
            "peak_rss_mib": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1),
        },
    }
    shutil.rmtree(scratch)
    (out / "summary.json").write_text(
        json.dumps(summary, indent=1, sort_keys=True) + "\n", encoding="utf-8"
    )
    return summary


if __name__ == "__main__":
    result = main(Path(sys.argv[1]))
    print(
        json.dumps(
            {
                k: result[k]
                for k in (
                    "membership_equal",
                    "same_order_resume_exact",
                    "m4_order_robustness",
                    "resources",
                )
            }
        )
    )
