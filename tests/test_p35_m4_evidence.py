"""P35 M4: evidence from authored M1–M3-shaped checkpoints, CLI, §U reports, legacy.

The checkpoints here are AUTHORED immutable fixtures published through the real
``ArtifactStore`` with M1 ``science.json`` / M2 attempt / M3 checkpoint-ledger
shapes. No model was trained; ``model.pt`` is a labelled placeholder payload.
This proves the reader's logic, not compatibility with a real trainer run
(which needs torch and is NOT RUN in this environment).
"""

from __future__ import annotations

import ast
import csv
import hashlib
import io
import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from p35_m4_support import h
from xlm.artifacts.manifest import canonical_json, identity_digest
from xlm.artifacts.store import ArtifactStore
from xlm.comparison import science_evidence
from xlm.comparison.science_compare import (
    ComparisonError,
    RunEntry,
    compare_science,
    verify_comparison_record,
)
from xlm.comparison.science_evidence import (
    EvidenceError,
    attempt_artifact_id,
    extract_run_evidence,
)
from xlm.comparison.science_manifest import ORDER_SENTINEL
from xlm.comparison.science_tracks import get_track
from xlm.core.paths import ArtifactPaths

REPO = Path(__file__).resolve().parents[1]
BUDGET = 64
BATCH = 16
PRIMARY = "equal_domain_text_ce_nats_per_token"
TUPLES = [("E0", 101, 10001, 20260918), ("E1", 211, 10002, 20260919), ("E2", 307, 10003, 20260920)]
COUNTS = {"prose": 40, "web": 24}


def _config(
    seeds: tuple[int, int, int], microbatch: int, *, science: bool = True
) -> dict[str, Any]:
    training: dict[str, Any] = {
        "device": "cuda",
        "precision": "bf16_fp32_master",
        "context_length": 8,
        "global_batch_valid_targets": BATCH,
        "microbatch_sequences": microbatch,
        "gradient_clip_norm": 1.0,
        "budget": {"max_valid_targets": BUDGET, "max_train_seconds": 600.0},
        "schedule": {
            "type": "warmup_cosine",
            "counter": "committed_valid_targets",
            "horizon_valid_targets": 1_000_000_000,
            "warmup_valid_targets": 10_000_000,
            "min_lr_ratio": 0.1,
        },
        "activation_checkpointing": False,
        "producer_prefetch": "process_depth1",
        "compile": False,
        "init_seed": seeds[0],
        "data_seed": seeds[2],
        "checkpoint_every_valid_targets": 16_000_000,
    }
    if science:
        training.update(
            science_version="xlm-science-v1",
            lr_policy="target_endpoint_before_update_v1",
            training_seed=seeds[1],
            runtime={
                "attention_policy": "statistical_efficient_v1",
                "matmul_tf32": "disabled",
                "bf16_reduced_precision_reduction": "allowed",
            },
        )
    return {
        "schema_version": 1,
        "kind": "experiment_draft",
        "track": "baseline",
        "model": {
            "architecture": "reference_decoder",
            "num_layers": 1,
            "hidden_size": 16,
            "num_attention_heads": 2,
            "intermediate_size": 32,
            "vocab_size": 260,
            "context_length": 8,
            "attention_backend": "sdpa",
        },
        "data": {
            "mixture": {
                "mixture_id": "synthetic-two-source",
                "components": [
                    {"source_id": "web", "weight": 0.375},
                    {"source_id": "prose", "weight": 0.625},
                ],
                "exhaustion": {"repeat": False, "max_epochs": 1},
                "packing": {"mode": "causal_stream", "cross_document_attention": True},
            }
        },
        "objective": {"type": "cross_entropy", "version": "1"},
        "optimizer": {
            "type": "adamw",
            "lr": 0.001,
            "betas": [0.9, 0.95],
            "eps": 1e-8,
            "weight_decay": 0.1,
        },
        "training": training,
    }


def _publish(
    store: ArtifactStore,
    artifact_id: str,
    kind: str,
    files: dict[str, bytes],
    metadata: dict[str, Any],
    code: str,
    dep: str,
    config_hash: str,
) -> Path:
    return store.publish_artifact(
        artifact_id=artifact_id,
        kind=kind,
        files=files,
        producer_code_hash=code,
        dependency_hash=dep,
        resolved_config_hash=config_hash,
        metadata=metadata,
    )


def publish_run(
    root: Path,
    run_id: str,
    seeds: tuple[int, int, int],
    *,
    microbatch: int = 8,
    primary: float = 3.0,
    attempts: tuple[str, ...] = ("complete",),
    science: bool = True,
    lr_rows: list[list[Any]] | None = None,
    coverage_complete: bool = True,
    canonical_rule: str = "first_complete_attempt_v1",
    tamper_receipt: bool = False,
    config_order: dict[str, Any] | None = None,
    state_order: dict[str, Any] | None = None,
    update_payloads: dict[str, Any] | None = None,
    declare_receipt: str | None | bool = True,
) -> Path:
    """Publish one AUTHORED science-v1-shaped endpoint checkpoint and its eval attempts.

    ``config_order``/``state_order`` (P35 M5) are the pinned ``data.document_order``
    and the committed data state's ``document_order`` receipt; ``None`` is pre-M5.
    A published ``update_payloads`` chain is declared in the envelope as a real
    frozen run declares it; ``declare_receipt`` overrides the declaration
    (``None``/a version string) for mismatch fixtures.
    """
    store = ArtifactStore(ArtifactPaths(root=root))
    config = _config(seeds, microbatch, science=science)
    declared = (
        ("global_update_payload_digest_v1" if update_payloads is not None else None)
        if declare_receipt is True
        else declare_receipt
    )
    if declared is not None:
        config["training"]["update_payload_receipt"] = declared
    if config_order is not None:
        config["data"]["document_order"] = dict(config_order)
    code, dep = h("code"), h("lock")
    bindings = {
        "data": {"mixture": h("shards")},
        "tokenizer": {"fingerprint": h("tokenizer")},
        "components": {
            "model": {"key": "reference_decoder", "serializer": "1"},
            "objective": {"key": "cross_entropy", "serializer": "1"},
            "optimizer": {"key": "adamw", "serializer": "1"},
            "schedule": {"key": "warmup_cosine", "serializer": "1"},
        },
    }
    payload = {
        "version": 1,
        "purpose": "training",
        "config": config,
        "bindings": bindings,
        "code_hash": code,
        "dependency_hash": dep,
        "environment": {"policy": "installed-files-v1", "python": "3.12.13", "digest": h("env")},
        "runtime_policy": {"torch_threads": 1, "scientific_runtime": "trainer_scoped_v1"},
    }
    envelope = {**payload, "execution_hash": identity_digest(payload)}
    plan_hash = h(f"plan-{run_id}")
    execution = {"envelope": envelope, "plan_hash": plan_hash}
    rows = lr_rows or [
        [i + 1, i * BATCH, BATCH, (i + 1) * BATCH, [1e-6]] for i in range(BUDGET // BATCH)
    ]
    plan_digest = h(f"eval-plan-{BUDGET}")
    run_key = identity_digest({"run_id": run_id, "plan_id": plan_hash, "plan": plan_digest})
    events = []
    for tier, threshold in (("quick_lm", 16), ("quick_lm", 64), ("full_lm", 64)):
        event_id = f"{tier}@{threshold}"
        computation = h(f"computation-{run_id}-{event_id}")
        value = primary + (0.5 if threshold == 16 else 0.0)
        for number, outcome in enumerate(attempts if tier == "full_lm" else ("complete",), 1):
            base = {
                "receipt_version": "xlm-eval-receipt-v1",
                "run": {"run_id": run_id, "plan_id": plan_hash, "run_key": run_key},
                "event": {"event_id": event_id, "tier": tier, "planned_threshold": threshold},
                "attempt": number,
                "actual_committed_targets": threshold,
                "step": threshold // BATCH,
                "model_state": {
                    "kind": "in_memory_replica_v1",
                    "state_digest": h(f"m-{threshold}"),
                },
                "computation_identity": computation if outcome != "foreign" else h("other"),
                "evaluator": {"tier": tier, "inventory": h(f"inventory-{tier}")},
                "canonical_rule": canonical_rule,
                "retry_policy": "retry_on_restart_v1",
            }
            started = {**base, "record": "started"}
            complete = outcome in ("complete", "foreign")
            record = {
                **base,
                "record": "outcome",
                "status": "complete" if complete else "failed",
                "metrics": {PRIMARY: value, "domains": {"web": {"text_ce_nats_per_token": value}}}
                if complete
                else None,
                "coverage": {
                    "kind": "lm_validation",
                    "complete": coverage_complete,
                    "documents_scored": 3 if coverage_complete else 2,
                    "documents_declared": 3,
                }
                if complete
                else None,
                "failure": None if complete else {"type": "RuntimeError", "message": "SYNTHETIC"},
            }
            record["receipt_identity"] = identity_digest(record)
            if tamper_receipt and complete:
                record["metrics"] = {PRIMARY: value - 1.0}
            for phase, content in (("started", started), ("outcome", record)):
                _publish(
                    store,
                    attempt_artifact_id(run_key, event_id, number, phase),
                    "evaluations",
                    {"attempt.json": canonical_json(content)},
                    {
                        "receipt_version": "xlm-eval-receipt-v1",
                        "event_id": event_id,
                        "attempt": number,
                        "phase": phase,
                        "run_key": run_key,
                    },
                    code,
                    dep,
                    computation,
                )
        events.append(
            {
                "event_id": event_id,
                "tier": tier,
                "planned_threshold": threshold,
                "is_endpoint": threshold == BUDGET,
                "status": "due",
                "due": {
                    "actual_committed_targets": threshold,
                    "step": threshold // BATCH,
                    "model_state_digest": h(f"m-{threshold}"),
                    "computation_identity": computation,
                },
                "attempts": [],
                "canonical_attempt": None,
            }
        )
    evaluation = {
        "version": 1,
        "plan": {"cadence": "authored_fixture"},
        "plan_digest": plan_digest,
        "evaluator_digests": {"full_lm": h("full-ev"), "quick_lm": h("quick-ev")},
        "canonical_rule": canonical_rule,
        "retry_policy": "retry_on_restart_v1",
        "max_attempts_per_event": 3,
        "events": events,
        "completeness": {},
    }
    endpoint_id = f"{run_id}_ckpt-t{BUDGET}-a001"

    def record(artifact: str, digest: str, events_: list[str], committed: int) -> dict[str, Any]:
        identity = {"model_state_digest": digest, "data_cursor_digest": h(f"cursor-{committed}")}
        return {
            "artifact_id": artifact,
            "attempt": 1,
            "events": events_,
            "actual_committed_targets": committed,
            "status": "published",
            "identity": identity,
            "identity_digest": identity_digest(identity),
        }

    checkpoints = {
        "version": 1,
        "plan": {"cadence": "authored_fixture"},
        "plan_digest": h("ckpt-plan"),
        "retention_policy": "latest_two_recovery_plus_pinned_v1",
        "rescore_policy": "retained_exact_checkpoint_v1",
        "protected_references": {},
        "events": [],
        "records": [
            record(f"{run_id}_ckpt-t0-a001", h(f"init-{seeds[0]}"), ["checkpoint@0"], 0),
            record(endpoint_id, h(f"final-{run_id}"), [f"checkpoint@{BUDGET}"], BUDGET),
        ],
    }
    science_json = {
        "version": 1,
        "policy": {"science_version": "xlm-science-v1"},
        "train_start_rng": {"training_seed": seeds[1]},
        "lr_receipts": {
            "columns": ["step", "committed_before", "valid_targets", "schedule_counter", "lr_used"],
            "rows": rows,
        },
        "runtime_receipts": [
            {
                "attention_policy": "statistical_efficient_v1",
                "device": "cuda",
                "device_name": "SYNTHETIC GPU",
                "torch": "2.14.0+cu126",
                "observed_ops": ["aten::_efficient_attention_forward"],
            }
        ],
        "evaluation": evaluation,
        "checkpoints": checkpoints,
    }
    if update_payloads is not None:  # pilot readiness: authored payload receipt chain
        science_json["update_payloads"] = update_payloads
    data_state = {
        "version": 1,
        "mixture_identity": h("mixture"),
        "exposure_identity": h("exposure"),
        "committed_valid_targets": BUDGET,
        "trace_digest": h(f"trace-{seeds[2]}"),
        "scheduler": {
            "counters": {
                s: {
                    "source_id": s,
                    "valid_targets": n,
                    "repeated_targets": 0,
                    "content_targets": n - 2,
                    "eos_targets": 2,
                    "documents_visited": 3,
                    "canonical_bytes": 4 * n,
                    "repeated_bytes": 0,
                    "epoch": 0,
                    "cursor": n,
                }
                for s, n in COUNTS.items()
            }
        },
    }
    if state_order is not None:
        data_state["document_order"] = dict(state_order)
    meta = {
        "checkpoint_id": endpoint_id,
        "run_id": run_id,
        "step": BUDGET // BATCH,
        "committed_valid_targets": BUDGET,
        "processed_valid_targets": BUDGET,
        "plan_id": plan_hash,
        "model_config": config["model"],
        "precision": "bf16_fp32_master",
    }
    files = {
        "execution.json": json.dumps(execution, sort_keys=True).encode(),
        "checkpoint_meta.json": json.dumps(meta).encode(),
        "science.json": json.dumps(science_json).encode(),
        "data_state.json": json.dumps(data_state).encode(),
        "model.pt": b"AUTHORED PLACEHOLDER WEIGHTS - NOT A TRAINED MODEL",
    }
    return _publish(
        store,
        endpoint_id,
        "checkpoints",
        files,
        {**meta, "execution_provenance_hash": identity_digest(execution)},
        code,
        dep,
        plan_hash,
    )


def _count(config: Any) -> int:
    return 7_000  # AUTHORED fixture count; the product CLI uses the meta-device count


def _extract(path: Path) -> dict[str, Any]:
    return extract_run_evidence(path, parameter_counter=_count, counter_label="authored_fixture")


def test_evidence_is_derived_from_receipts(tmp_path: Path) -> None:
    ckpt = publish_run(tmp_path / "store", "run-b8-E0", (101, 10001, 20260918), microbatch=8)
    evidence = _extract(ckpt)
    fields = evidence["fields"]
    assert evidence["replicate"] == {
        "init_seed": 101,
        "training_seed": 10001,
        "data_seed": 20260918,
        "order_manifest_id": ORDER_SENTINEL,
    }
    assert fields["microbatch_sequences"] == 8 and fields["global_batch_valid_targets"] == BATCH
    assert fields["tokenizer_identity"] == {"fingerprint": h("tokenizer")}
    assert fields["update_boundaries_digest"] == identity_digest(
        [[0, 16], [16, 16], [32, 16], [48, 16]]
    )
    assert fields["per_source_exposure"]["prose"]["valid_targets"] == 40
    assert fields["initial_model_state_digest"] == h("init-101")
    assert fields["final_model_state_digest"] == h("final-run-b8-E0")
    assert fields["runtime_device_name"] == ["SYNTHETIC GPU"]
    assert fields["mixture_components"][0]["source_id"] == "prose"
    assert fields["model_parameter_count"] == 7_000
    assert evidence["source"]["parameter_count_source"] == "authored_fixture"
    assert fields["execution_hash"] and fields["code_hash"] == h("code")
    endpoint = evidence["evaluations"]["full_lm@64"]
    assert endpoint["complete"] and endpoint["metrics"][PRIMARY] == 3.0
    assert evidence["endpoint"]["at_budget"]


def test_canonical_attempt_is_first_complete_in_lineage(tmp_path: Path) -> None:
    ckpt = publish_run(tmp_path / "s", "r", (1, 2, 3), attempts=("failed", "foreign", "complete"))
    event = _extract(ckpt)["evaluations"]["full_lm@64"]
    assert [(a["number"], a["status"], a["in_lineage"]) for a in event["attempts"]] == [
        (1, "failed", True),
        (2, "complete", False),
        (3, "complete", True),
    ]
    assert event["canonical_attempt"].endswith("_a003_outcome") and event["complete"]


def test_failed_only_attempts_leave_the_event_incomplete(tmp_path: Path) -> None:
    ckpt = publish_run(tmp_path / "s", "r", (1, 2, 3), attempts=("failed",))
    event = _extract(ckpt)["evaluations"]["full_lm@64"]
    assert not event["complete"] and event["metrics"] is None


def test_incomplete_coverage_or_altered_receipt_is_not_complete(tmp_path: Path) -> None:
    partial = _extract(publish_run(tmp_path / "a", "r", (1, 2, 3), coverage_complete=False))
    event = partial["evaluations"]["full_lm@64"]
    assert event["complete"] is False and event["coverage_complete"] is False
    assert "canonical coverage is not complete" in event["reasons"]
    tampered = _extract(publish_run(tmp_path / "b", "r", (1, 2, 3), tamper_receipt=True))
    event = tampered["evaluations"]["full_lm@64"]
    assert event["complete"] is False
    assert "canonical receipt identity does not verify" in event["reasons"]


def test_legacy_and_unverifiable_checkpoints_are_refused(tmp_path: Path) -> None:
    with pytest.raises(EvidenceError, match="legacy"):
        _extract(publish_run(tmp_path / "legacy", "r", (1, 2, 3), science=False))
    ckpt = publish_run(tmp_path / "t", "r", (1, 2, 3))
    (ckpt / "science.json").write_bytes(b'{"version": 1}')
    with pytest.raises(EvidenceError, match="does not verify"):
        _extract(ckpt)
    with pytest.raises(EvidenceError, match="canonical rule"):
        _extract(publish_run(tmp_path / "rule", "r", (1, 2, 3), canonical_rule="latest_complete"))
    gap = [[1, 0, 16, 16, [1e-6]], [2, 32, 16, 48, [1e-6]]]
    with pytest.raises(EvidenceError, match="contiguous"):
        _extract(publish_run(tmp_path / "gap", "r", (1, 2, 3), lr_rows=gap))


def test_uncounted_parameters_stay_unknown(tmp_path: Path) -> None:
    def broken(config: Any) -> int:
        raise ImportError("torch unavailable")

    evidence = extract_run_evidence(
        publish_run(tmp_path / "s", "r", (1, 2, 3)), parameter_counter=broken
    )
    assert evidence["fields"]["model_parameter_count"] is None
    assert evidence["source"]["parameter_count_source"] == "count_failed: ImportError"


def _manifest(budget: int = BUDGET) -> dict[str, Any]:
    track = get_track("microbatch_grouping_v1")
    assert track is not None
    return {
        "science_comparison_version": "xlm-science-comparison-v1",
        "comparison_id": "authored-microbatch-toy",
        "title": "AUTHORED toy <script>alert(1)</script> | pipe",
        "description": "SYNTHETIC fixture; no results.",
        "status": "frozen",
        "preregistered_at": "2026-09-26T00:00:00Z",
        "parent_baseline": {"id": "toy", "version": "0"},
        "track": "microbatch_grouping_v1",
        "scale_stage": "50m",
        "study_stage": "screen",
        "question": "noninferiority",
        "control_arm": {"arm_id": "b8", "label": "B8", "intervention": {"microbatch_sequences": 8}},
        "candidate_arms": [
            {
                "arm_id": "b16",
                "label": "B16 <b>|x|</b>",
                "intervention": {"microbatch_sequences": 16},
            }
        ],
        "replicate_roster": [
            {
                "tuple_id": t,
                "role": "exploratory",
                "init_seed": i,
                "training_seed": r,
                "data_seed": d,
                "order_manifest_id": ORDER_SENTINEL,
            }
            for t, i, r, d in TUPLES
        ],
        "intended_differences": ["microbatch_sequences"],
        "required_invariants": track.must_match(),
        "training_budget_targets": budget,
        "fixed_values": {"global_batch_valid_targets": BATCH},
        "schedule": {
            "lr_policy": "target_endpoint_before_update_v1",
            "base_lr": 0.001,
            "warmup_targets": 10_000_000,
            "horizon_targets": 1_000_000_000,
            "min_lr_ratio": 0.1,
        },
        "primary_endpoint": {"tier": "full_lm", "planned_threshold": budget},
        "primary_metric": {"name": PRIMARY, "direction": "lower_is_better"},
        "secondary_metrics": [
            {
                "name": "domain_web_text_ce_nats_per_token",
                "direction": "lower_is_better",
                "role": "descriptive",
                "source": {
                    "tier": "search_benchmark",
                    "planned_threshold": budget,
                    "path": ["tasks", "blimp", "value"],
                },
                "regression_margin": None,
            }
        ],
        "curve_metric": {
            "name": PRIMARY,
            "direction": "lower_is_better",
            "tier": "quick_lm",
            "start_target": 16,
            "end_target": budget,
        },
        "practical_margin": None,
        "noninferiority_margin": 0.05,
        "margin_rationale": "SYNTHETIC",
        "efficiency": {
            "name": "successful_targets_per_second",
            "direction": "higher_is_better",
            "minimum_relative_gain": 0.05,
        },
        "confirmatory_pairs": None,
        "multiplicity": {"family_id": "toy", "family_size": 1, "guardrail_family_size": 0},
        "ci_level": 0.95,
        "stopping_rule": "fixed_n_all_pairs_no_early_success_v1",
        "promotion_rule_version": "xlm-p35-promotion-v1",
        "final_checkpoint_requirement": "exact_budget_endpoint_v1",
        "failure_policy": "failed_or_missing_pair_blocks_confirmation_v1",
        "order_robustness": {"required": False, "m5_order_evidence": None},
        "scale_promotion": {
            "intent": False,
            "prerequisite": None,
            "resource_plan_ref": None,
            "ablation_refs": [],
        },
    }


def _roster(root: Path) -> dict[str, Any]:
    runs = []
    for t, i, r, d in TUPLES:
        for arm, size, value in (("b8", 8, 3.0), ("b16", 16, 3.01)):
            ckpt = publish_run(
                root / f"{arm}-{t}",
                f"run-{arm}-{t}",
                (i, r, d),
                microbatch=size,
                primary=value + 0.001 * i / 100,
            )
            runs.append(
                {
                    "label": f"{arm}-{t}",
                    "arm_id": arm,
                    "status": "completed",
                    "failure": None,
                    "checkpoint": str(ckpt),
                    "synthetic": True,
                    "measurements": {
                        "successful_targets_per_second": {
                            "value": 45_000.0 if arm == "b8" else 50_000.0,
                            "source": "SYNTHETIC declared",
                        }
                    },
                }
            )
    runs.append(
        {
            "label": "b16-E0-first-attempt <i>",
            "arm_id": "b16",
            "status": "failed",
            "failure": "SYNTHETIC: killed runner",
            "checkpoint": None,
            "synthetic": True,
        }
    )
    return {"roster_version": "xlm-science-comparison-runs-v1", "runs": runs}


def _cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Any, Path, Path]:
    from xlm.cli.main import app

    monkeypatch.setattr(science_evidence, "meta_parameter_counter", _count)
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(_manifest()), encoding="utf-8")
    roster_path = tmp_path / "runs.json"
    roster_path.write_text(json.dumps(_roster(tmp_path / "stores")), encoding="utf-8")
    out = tmp_path / "report"
    result = CliRunner().invoke(
        app,
        [
            "experiment",
            "compare",
            "--comparison",
            str(manifest_path),
            "--runs",
            str(roster_path),
            "--output",
            str(out),
        ],
    )
    return result, out, manifest_path


def test_cli_compare_and_report_roundtrip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from xlm.cli.main import app

    result, out, _ = _cli(tmp_path, monkeypatch)
    assert result.exit_code == 0, result.output
    record = json.loads((out / "comparison.json").read_text(encoding="utf-8"))
    verify_comparison_record(record)
    (candidate,) = record["candidates"]
    assert candidate["eligible"] and candidate["pairing"]["n_complete"] == 3
    assert candidate["promotion"]["state"] == "SCREEN_ONLY"
    failed = next(r for r in record["runs"] if r["label"].startswith("b16-E0-first"))
    assert failed["state"] == "failed"
    markdown = (out / "report.md").read_text(encoding="utf-8")
    rows = list(csv.DictReader(io.StringIO((out / "summary.csv").read_text(encoding="utf-8"))))
    assert rows[0]["eligibility"] == "ELIGIBLE" and rows[0]["synthetic"] == "SYNTHETIC"
    assert rows[0]["failures"].startswith("1: b16-E0-first-attempt")
    assert "SYNTHETIC EVIDENCE" in markdown and "b16-E0-first-attempt" in markdown
    # External text is escaped: no raw HTML, no table-breaking pipes.
    assert "<script>" not in markdown and "<b>" not in markdown and "<i>" not in markdown
    assert "&lt;script&gt;" in markdown or "\\&lt;script" in markdown or "lt;script" in markdown
    title_line = next(line for line in markdown.splitlines() if line.startswith("- Title:"))
    assert "\\|" in title_line
    before = {p.name: p.read_bytes() for p in out.iterdir()}
    again = CliRunner().invoke(
        app,
        [
            "experiment",
            "report",
            "--record",
            str(out / "comparison.json"),
            "--output",
            str(tmp_path / "rerendered"),
        ],
    )
    assert again.exit_code == 0, again.output
    after = {p.name: p.read_bytes() for p in (tmp_path / "rerendered").iterdir()}
    assert after == before, "report re-renders the frozen record byte-identically"


def test_cli_report_refuses_altered_or_legacy_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from xlm.cli.main import app

    result, out, _ = _cli(tmp_path, monkeypatch)
    record = json.loads((out / "comparison.json").read_text(encoding="utf-8"))
    record["candidates"][0]["promotion"]["state"] = "PROMOTE_TO_150M"
    altered = tmp_path / "altered.json"
    altered.write_text(json.dumps(record), encoding="utf-8")
    refused = CliRunner().invoke(
        app, ["experiment", "report", "--record", str(altered), "--output", str(tmp_path / "x")]
    )
    assert refused.exit_code == 1 and "altered" in refused.output
    legacy = tmp_path / "legacy.json"
    legacy.write_text(
        json.dumps(
            {
                "comparison_version": 1,
                "bootstrap_version": "2",
                "primary_selection_policy": "numeric_lexicographic_min_v1",
                "eligibility": {"eligible": True},
                "index_difference": 2.0,
            }
        ),
        encoding="utf-8",
    )
    refused = CliRunner().invoke(
        app, ["experiment", "report", "--record", str(legacy), "--output", str(tmp_path / "y")]
    )
    assert refused.exit_code == 1 and "legacy P17" in refused.output


def test_cli_ineligible_comparison_writes_field_diff_and_exits_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from xlm.cli.main import app

    monkeypatch.setattr(science_evidence, "meta_parameter_counter", _count)
    manifest = _manifest()
    roster = _roster(tmp_path / "stores")
    # Replace one B16 run by a run with different data order (another data seed's trace).
    bad_run = publish_run(
        tmp_path / "bad2", "run-b16-E1-reordered", (211, 10002, 20260919), microbatch=16
    )
    data = json.loads((bad_run / "data_state.json").read_text(encoding="utf-8"))
    data["trace_digest"] = h("different-order")
    # Re-publish as a new, verified artifact with the altered committed trace.
    files = {
        p.name: p.read_bytes()
        for p in bad_run.iterdir()
        if p.name not in ("manifest.json", "_COMPLETED")
    }
    files["data_state.json"] = json.dumps(data).encode()
    store = ArtifactStore(ArtifactPaths(root=tmp_path / "bad3"))
    manifest_obj = ArtifactStore(ArtifactPaths(root=tmp_path / "bad2")).load_manifest(bad_run)
    reordered = store.publish_artifact(
        artifact_id=bad_run.name,
        kind="checkpoints",
        files=files,
        producer_code_hash=manifest_obj.producer_code_hash,
        dependency_hash=manifest_obj.dependency_hash,
        resolved_config_hash=manifest_obj.resolved_config_hash,
        metadata=manifest_obj.metadata,
    )
    for sub in ("evaluations",):
        import shutil

        shutil.copytree(tmp_path / "bad2" / sub, tmp_path / "bad3" / sub)
    for entry in roster["runs"]:
        if entry["label"] == "b16-E1":
            entry["checkpoint"] = str(reordered)
    (tmp_path / "m.json").write_text(json.dumps(manifest), encoding="utf-8")
    (tmp_path / "r.json").write_text(json.dumps(roster), encoding="utf-8")
    result = CliRunner().invoke(
        app,
        [
            "experiment",
            "compare",
            "--comparison",
            str(tmp_path / "m.json"),
            "--runs",
            str(tmp_path / "r.json"),
            "--output",
            str(tmp_path / "out"),
        ],
    )
    assert result.exit_code == 1
    record = json.loads((tmp_path / "out" / "comparison.json").read_text(encoding="utf-8"))
    (candidate,) = record["candidates"]
    assert not candidate["eligible"] and candidate["statistics"] is None
    violation = next(v for v in candidate["field_violations"] if v["field"] == "data_trace_digest")
    assert violation["control"] == h("trace-20260919") and violation["candidate"] == h(
        "different-order"
    )
    markdown = (tmp_path / "out" / "report.md").read_text(encoding="utf-8")
    assert "INELIGIBLE field diff" in markdown and "data\\_trace\\_digest" in markdown
    rows = list(csv.DictReader(io.StringIO((tmp_path / "out" / "summary.csv").read_text("utf-8"))))
    assert rows[0]["eligibility"].startswith("INELIGIBLE: field diff data_trace_digest")
    assert rows[0]["paired_delta"] == "INELIGIBLE (no effect estimate)"
    assert rows[0]["primary_control"] != "0" and "0.0" not in rows[0]["seed_sd"]


def test_invalid_manifest_report_shows_problems_and_not_run(tmp_path: Path) -> None:
    from xlm.reports.science import to_csv, to_markdown

    manifest = _manifest()
    del manifest["multiplicity"]
    record = compare_science(manifest, [RunEntry("x", "b8", "failed", "SYNTHETIC", None)])
    assert record["summary"]["overall"] == "INELIGIBLE"
    markdown = to_markdown(record)
    assert "INELIGIBLE: manifest problems" in markdown and "multiplicity" in markdown
    rows = list(csv.DictReader(io.StringIO(to_csv(record))))
    assert rows[0]["decision"] == "INELIGIBLE" and rows[0]["completeness"] == "NOT RUN"
    assert rows[0]["primary_control"] == "n/a"


def test_legacy_p17_records_are_not_upgraded() -> None:
    legacy = {"comparison_version": 1, "bootstrap_version": "2", "suite": {}, "eligibility": {}}
    with pytest.raises(ComparisonError, match="legacy P17"):
        verify_comparison_record(legacy)


def test_legacy_p17_modules_are_byte_identical_to_certified_m3() -> None:
    golden = {
        "src/xlm/comparison/promotion.py": (
            "04e961acb3dfe643742fa0777b7e9a98838139d8d34dbd08dda7689713aa8842"
        ),
        "src/xlm/comparison/tracks.py": (
            "07d9b7a158a4171d68bbf30fb6b9249eb1aade156f37e076f91b3c474354d019"
        ),
        "src/xlm/comparison/bootstrap.py": (
            "546033327a9b00d6030481e6702346f4c1c9e0d29995c2d0990b82ba1d3ced92"
        ),
        "src/xlm/cli/compare_cmd.py": (
            "f30254bd29d6015ff43cafd0893c26186db77a36c0d54f21af00f6458b55618e"
        ),
        "src/xlm/comparison/recipes.py": (
            "36498a181e648314cea23742eb8e79748e352a6f7afb63db572deca3d5ad15ef"
        ),
    }
    for rel, digest in golden.items():
        assert hashlib.sha256((REPO / rel).read_bytes()).hexdigest() == digest, rel


def test_legacy_p17_promotion_behavior_unchanged() -> None:
    from xlm.comparison.promotion import PromotionEvidence, PromotionGates, evaluate_promotion

    gates = PromotionGates()
    assert gates.to_dict() == {
        "gate_version": "1",
        "min_suite_delta": 1.0,
        "min_compute_saving": 0.10,
        "min_seeds": 2,
        "require_ci_excludes_zero": True,
        "max_task_regression": 0.5,
        "ci_condition": "search_stage_decision_support_only",
    }
    evidence = PromotionEvidence(
        baseline_run_id="a",
        candidate_run_id="b",
        track="architecture",
        suite_index_delta=1.5,
        index_ci_lo=0.2,
        index_ci_hi=2.0,
        task_deltas={},
        compute_saving_fraction=None,
        n_seeds_baseline=2,
        n_seeds_candidate=2,
    )
    assert evaluate_promotion(evidence, gates, eligible=True).promote is True
    one_seed = PromotionEvidence(
        **{**evidence.to_dict(), "n_seeds_baseline": 1, "scale_hypothesis_exceptions": ()}
    )
    assert evaluate_promotion(one_seed, gates, eligible=True).promote is False


def _constant(path: Path, name: str) -> Any:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            getattr(t, "id", None) == name for t in node.targets
        ):
            return ast.literal_eval(node.value)
    raise AssertionError(f"{name} not found in {path}")


def test_m4_reader_mirrors_the_frozen_m1_m2_m3_constants() -> None:
    src = REPO / "src" / "xlm"
    receipts = src / "evaluation" / "receipts.py"
    assert _constant(receipts, "RECEIPT_VERSION") == science_evidence.M2_RECEIPT_VERSION
    assert _constant(receipts, "CANONICAL_RULE") == science_evidence.M2_CANONICAL_RULE
    assert _constant(receipts, "LEDGER_VERSION") == science_evidence.M2_LEDGER_VERSION
    assert _constant(receipts, "MAX_ATTEMPT_NUMBER") == science_evidence.M2_MAX_ATTEMPT_NUMBER
    assert _constant(src / "evaluation" / "lm_validation.py", "PRIMARY_METRIC") == PRIMARY
    assert (
        list(_constant(src / "training" / "science.py", "LR_RECEIPT_COLUMNS"))
        == science_evidence.M1_LR_COLUMNS
    )
    assert (
        _constant(src / "training" / "science.py", "SCIENCE_STATE_VERSION")
        == science_evidence.M1_SCIENCE_STATE_VERSION
    )
    assert (
        _constant(src / "training" / "milestones.py", "CHECKPOINT_LEDGER_VERSION")
        == science_evidence.M3_CHECKPOINT_LEDGER_VERSION
    )


def test_partial_confirmation_row_is_labelled_provisional() -> None:
    from p35_m4_support import mixture_pair_runs, superiority_manifest
    from xlm.reports.science import summary_rows

    manifest = superiority_manifest()
    control = {"C0": 3.0, "C1": 3.1, "C2": 3.2, "C3": 3.05, "C4": 3.15}
    candidate = {"C0": 2.98, "C1": 3.07, "C2": 3.19}
    (row,) = summary_rows(
        compare_science(manifest, mixture_pair_runs(manifest, control, candidate))
    )
    assert row["seed_ci"].startswith("PROVISIONAL 3/5: [")
    assert row["paired_delta"].startswith("PROVISIONAL 3/5: ")
    assert row["completeness"] == "INCOMPLETE 3/5 (missing: C3, C4)"
    assert row["decision"] == "INCOMPLETE" and row["promotion_state"] == "PROVISIONAL"
