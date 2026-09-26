"""P35 M5: order identity in M4 evidence, the robustness slot, allocation and bundle.

All values are SYNTHETIC/AUTHORED. Checkpoints are authored science-v1-shaped
artifacts published through the real ``ArtifactStore`` (weights are a labelled
placeholder); no model was trained and no statistic is a real result.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

from p35_m4_support import (
    C_TUPLES,
    make_evidence,
    microbatch_manifest,
    microbatch_runs,
    mixture_pair_runs,
    superiority_manifest,
)
from p35_m5_support import h, synthetic_membership, synthetic_orders
from test_p35_m4_evidence import _extract, publish_run
from xlm.artifacts.manifest import identity_digest
from xlm.comparison import science_evidence
from xlm.comparison.science_compare import compare_science
from xlm.comparison.science_evidence import (
    EvidenceError,
    evidence_fields,
    verify_evidence_record,
)
from xlm.comparison.science_manifest import MEMBERSHIP_SENTINEL, ORDER_SENTINEL
from xlm.comparison.science_order import (
    ALLOCATION_POLICY,
    OrderEvidenceError,
    build_order_bundle,
    build_order_declaration,
    bundle_problems,
    declaration_problems,
    seal_bundle,
)
from xlm.comparison.science_promotion import order_robustness
from xlm.comparison.science_tracks import MICROBATCH_TRACK, MIXTURE_TRACK
from xlm.data.ordering import ORDER_POLICY, build_order_manifest

CTRL = {"C0": 3.00, "C1": 3.10, "C2": 3.20, "C3": 3.05, "C4": 3.15}
WIN = {"C0": 2.98, "C1": 3.07, "C2": 3.19, "C3": 3.02, "C4": 3.12}
SEEDS = (401, 20001, 20261001)


def _order_receipts(order: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    config = {
        "manifest": "/abs/orders/order.json",
        "order_manifest_id": order["order_manifest_id"],
        "canonical_membership_id": order["canonical_membership_id"],
    }
    state = {
        "order_manifest_id": order["order_manifest_id"],
        "canonical_membership_id": order["canonical_membership_id"],
        "within_source_order_policy": ORDER_POLICY,
    }
    return config, state


def _m5_manifest(
    *, scale: str = "50m", tuples: list[Any] | None = None, promote: bool = True
) -> dict[str, Any]:
    doc = superiority_manifest(scale=scale, tuples=tuples)
    declaration = build_order_declaration(
        synthetic_orders(), [e["tuple_id"] for e in doc["replicate_roster"]]
    )
    for entry in doc["replicate_roster"]:
        entry["order_manifest_id"] = declaration["allocation"][entry["tuple_id"]]
    doc["order_robustness"] = {"required": True, "m5_order_evidence": declaration}
    doc["scale_promotion"].update(
        intent=promote,
        resource_plan_ref="SYNTHETIC RP" if promote else None,
        ablation_refs=["SYNTHETIC AB"] if promote else [],
    )
    return doc


def _candidate(record: dict[str, Any]) -> dict[str, Any]:
    (candidate,) = record["candidates"]
    return candidate


# ------------------------------------------------------------- M4 extraction


def test_extractor_reads_the_real_order_identity(tmp_path: Path) -> None:
    order_a, _ = synthetic_orders()
    config, state = _order_receipts(order_a)
    evidence = _extract(
        publish_run(tmp_path, "run-a", SEEDS, config_order=config, state_order=state)
    )
    fields = evidence["fields"]
    assert evidence["evidence_version"] == "xlm-science-run-evidence-v2"
    assert fields["order_manifest_id"] == order_a["order_manifest_id"] != ORDER_SENTINEL
    assert evidence["replicate"]["order_manifest_id"] == order_a["order_manifest_id"]
    assert fields["canonical_membership_id"] == order_a["canonical_membership_id"]
    assert fields["within_source_order_policy"] == ORDER_POLICY
    verify_evidence_record(evidence)


def test_pre_m5_checkpoint_keeps_the_sentinel(tmp_path: Path) -> None:
    fields = _extract(publish_run(tmp_path, "run-legacy", SEEDS))["fields"]
    assert fields["order_manifest_id"] == ORDER_SENTINEL
    assert fields["canonical_membership_id"] == MEMBERSHIP_SENTINEL
    assert fields["within_source_order_policy"] == "shard_native_offset_order_v1"


@pytest.mark.parametrize("case", ["config_only", "state_only", "different_id", "policy"])
def test_inconsistent_order_receipts_are_refused_not_sentinelled(tmp_path: Path, case: str) -> None:
    order_a, order_b = synthetic_orders()
    config, state = _order_receipts(order_a)
    kwargs: dict[str, Any] = {"config_order": config, "state_order": state}
    if case == "config_only":
        kwargs["state_order"] = None
    elif case == "state_only":
        kwargs["config_order"] = None
    elif case == "different_id":
        kwargs["state_order"] = _order_receipts(order_b)[1]
    elif case == "policy":
        kwargs["state_order"] = {
            **state,
            "within_source_order_policy": "shard_native_offset_order_v1",
        }
    with pytest.raises(EvidenceError, match="order"):
        _extract(publish_run(tmp_path, f"run-{case}", SEEDS, **kwargs))


def test_v1_records_stay_verifiable_and_read_as_pre_m5() -> None:
    record = make_evidence("run-v1", SEEDS, primary=3.0)
    record["evidence_version"] = "xlm-science-run-evidence-v1"
    del record["fields"]["canonical_membership_id"]
    record["evidence_digest"] = identity_digest(
        {k: v for k, v in record.items() if k != "evidence_digest"}
    )
    verify_evidence_record(record)
    assert evidence_fields(record)["canonical_membership_id"] == MEMBERSHIP_SENTINEL
    assert "canonical_membership_id" not in record["fields"]  # never rewritten
    forged = copy.deepcopy(record)
    forged["fields"]["order_manifest_id"] = h("m5")
    forged["evidence_digest"] = identity_digest(
        {k: v for k, v in forged.items() if k != "evidence_digest"}
    )
    with pytest.raises(EvidenceError, match="v1"):
        verify_evidence_record(forged)


def test_m5_policy_constant_mirrors_the_data_layer() -> None:
    assert science_evidence.M5_ORDER_POLICY == ORDER_POLICY


def test_membership_is_must_match_on_both_certified_tracks() -> None:
    for track in (MICROBATCH_TRACK, MIXTURE_TRACK):
        assert track.classify("canonical_membership_id").value == "MUST_MATCH"
        assert track.classify("order_manifest_id").value == "MUST_MATCH"
        assert track.classify("within_source_order_policy").value == "MUST_MATCH"


# -------------------------------------------------------------- declaration


def test_c0_c2_c4_on_a_and_c1_c3_on_b_validates() -> None:
    doc = _m5_manifest()
    declaration = doc["order_robustness"]["m5_order_evidence"]
    a, b = declaration["order_manifest_ids"]
    assert declaration["allocation_policy"] == ALLOCATION_POLICY
    assert declaration["allocation"] == {"C0": a, "C1": b, "C2": a, "C3": b, "C4": a}
    assert declaration_problems(declaration, doc["replicate_roster"]) == []


def test_three_tuple_scale_block_covers_both_orders() -> None:
    tuples = [
        ("S0", 1001, 30001, 20262001),
        ("S1", 1003, 30002, 20262002),
        ("S2", 1005, 30003, 20262003),
    ]
    doc = _m5_manifest(scale="150m", tuples=tuples, promote=False)
    declaration = doc["order_robustness"]["m5_order_evidence"]
    a, b = declaration["order_manifest_ids"]
    assert declaration["allocation"] == {"S0": a, "S1": b, "S2": a}
    assert declaration_problems(declaration, doc["replicate_roster"]) == []


def test_same_order_twice_is_refused() -> None:
    order_a, _ = synthetic_orders()
    with pytest.raises(OrderEvidenceError, match="same manifest"):
        build_order_declaration([order_a, order_a], ["C0", "C1"])
    doc = _m5_manifest()
    declaration = doc["order_robustness"]["m5_order_evidence"]
    declaration["order_manifests"][1] = declaration["order_manifests"][0]
    declaration["order_manifest_ids"][1] = declaration["order_manifest_ids"][0]
    assert declaration_problems(declaration, doc["replicate_roster"])


def test_orders_over_different_memberships_are_refused() -> None:
    order_a, _ = synthetic_orders()
    other = build_order_manifest(synthetic_membership({"alpha": 41, "beta": 30}), order_seed=7)
    with pytest.raises(OrderEvidenceError, match="different canonical memberships"):
        build_order_declaration([order_a, other], [t[0] for t in C_TUPLES])
    doc = _m5_manifest()
    declaration = doc["order_robustness"]["m5_order_evidence"]
    from xlm.data.ordering import header_with_id

    declaration["order_manifests"][1] = header_with_id(other)
    declaration["order_manifest_ids"][1] = other["order_manifest_id"]
    problems = declaration_problems(declaration, doc["replicate_roster"])
    assert any("membership" in p for p in problems)


def test_all_five_pairs_on_order_a_is_not_robust() -> None:
    doc = _m5_manifest()
    declaration = doc["order_robustness"]["m5_order_evidence"]
    a = declaration["order_manifest_ids"][0]
    for entry in doc["replicate_roster"]:
        entry["order_manifest_id"] = a
    declaration["allocation"] = {t: a for t in declaration["allocation"]}
    assert any(
        ALLOCATION_POLICY in p for p in declaration_problems(declaration, doc["replicate_roster"])
    )
    status, reason = order_robustness(
        doc,
        {t: a for t in declaration["allocation"]},
        {t: declaration["canonical_membership_id"] for t in declaration["allocation"]},
    )
    assert status == "BLOCKED"
    # Even with a (forged) declaration that validates, runs all on A are refused.
    valid = _m5_manifest()
    good = valid["order_robustness"]["m5_order_evidence"]
    membership = {t: good["canonical_membership_id"] for t in good["allocation"]}
    status, reason = order_robustness(
        valid, {t: good["order_manifest_ids"][0] for t in good["allocation"]}, membership
    )
    assert status == "BLOCKED"


def test_roster_must_carry_the_allocated_orders() -> None:
    doc = _m5_manifest()
    declaration = doc["order_robustness"]["m5_order_evidence"]
    a, b = declaration["order_manifest_ids"]
    doc["replicate_roster"][1]["order_manifest_id"] = a  # C1 should be B
    problems = declaration_problems(declaration, doc["replicate_roster"])
    assert any("C1" in p for p in problems)


def test_a_malformed_or_tampered_header_is_refused() -> None:
    doc = _m5_manifest()
    declaration = doc["order_robustness"]["m5_order_evidence"]
    declaration["order_manifests"][0]["derivation"]["order_seed"] += 1
    assert any(
        "does not verify" in p for p in declaration_problems(declaration, doc["replicate_roster"])
    )


# -------------------------------------------------------- M4 comparison flow


def test_valid_order_evidence_satisfies_the_m4_slot() -> None:
    doc = _m5_manifest()
    record = compare_science(doc, mixture_pair_runs(doc, CTRL, WIN))
    promotion = _candidate(record)["promotion"]
    assert promotion["requirements"]["order_robustness"] == "SATISFIED"
    assert promotion["state"] == "PROMOTE_TO_150M"


def test_runs_on_another_membership_block_robustness() -> None:
    doc = _m5_manifest()
    runs = mixture_pair_runs(doc, CTRL, WIN)
    for entry in runs:
        assert entry.evidence is not None
        fields = entry.evidence["fields"]
        fields["canonical_membership_id"] = h("other-membership")
        entry.evidence["evidence_digest"] = identity_digest(
            {k: v for k, v in entry.evidence.items() if k != "evidence_digest"}
        )
    promotion = _candidate(compare_science(doc, runs))["promotion"]
    assert promotion["requirements"]["order_robustness"] == "BLOCKED"
    assert "membership" in promotion["blockers"][0]["reason"]


def test_order_robustness_needs_membership_receipts() -> None:
    doc = _m5_manifest()
    declaration = doc["order_robustness"]["m5_order_evidence"]
    status, reason = order_robustness(doc, dict(declaration["allocation"]))
    assert status == "BLOCKED" and "membership" in reason
    membership = {t: declaration["canonical_membership_id"] for t in declaration["allocation"]}
    status, _ = order_robustness(doc, dict(declaration["allocation"]), membership)
    assert status == "SATISFIED"


def test_microbatch_pair_must_share_its_order() -> None:
    doc = microbatch_manifest(stage="screen")
    order_a, order_b = synthetic_orders()
    declaration = build_order_declaration(
        [order_a, order_b], [e["tuple_id"] for e in doc["replicate_roster"]]
    )
    for entry in doc["replicate_roster"]:
        entry["order_manifest_id"] = declaration["allocation"][entry["tuple_id"]]
    doc["order_robustness"] = {"required": False, "m5_order_evidence": declaration}
    values = {"b8": {"E0": 3.0}, "b16": {"E0": 3.0}}
    runs = microbatch_runs(doc, values)
    # Every run carries order A and its membership (the roster's order) ...
    for entry in runs:
        assert entry.evidence is not None
        assert entry.evidence["fields"]["order_manifest_id"] == order_a["order_manifest_id"]
    # ... re-run the B16 arm under order B: it is not a registered replicate of E0.
    swapped = []
    for entry in runs:
        evidence = copy.deepcopy(entry.evidence)
        assert evidence is not None
        if entry.arm_id == "b16":
            for part in (evidence["fields"], evidence["replicate"]):
                part["order_manifest_id"] = order_b["order_manifest_id"]
            evidence["evidence_digest"] = identity_digest(
                {k: v for k, v in evidence.items() if k != "evidence_digest"}
            )
        swapped.append(type(entry)(**{**entry.__dict__, "evidence": evidence}))
    candidates = compare_science(doc, swapped)["candidates"]
    b16 = next(c for c in candidates if c["arm_id"] == "b16")
    assert b16["eligible"] is False


def test_mixture_comparison_keeps_the_order_policy_fixed() -> None:
    doc = superiority_manifest(stage="screen", tuples=[("E0", 101, 10001, 20260918)])
    runs = mixture_pair_runs(
        doc,
        {"E0": 3.0},
        {"E0": 2.9},
        candidate_overrides={"within_source_order_policy": ORDER_POLICY},
    )
    candidate = _candidate(compare_science(doc, runs))
    assert candidate["eligible"] is False
    assert any(v["field"] == "within_source_order_policy" for v in candidate["field_violations"])


# ------------------------------------------------------------------- bundle


def _bundle_runs(doc: dict[str, Any], *, retry: bool = False) -> list[tuple[str, str, Any]]:
    entries = mixture_pair_runs(doc, CTRL, WIN)
    runs = [(e.label, e.arm_id, e.evidence) for e in entries]
    if retry:
        # A same-seed rerun of C0 (both arms): a repeat, never a new replicate.
        for label, arm, evidence in list(runs):
            if label.endswith("-C0"):
                again = copy.deepcopy(evidence)
                again["fields"]["run_id"] += "-retry"
                again["evidence_digest"] = identity_digest(
                    {k: v for k, v in again.items() if k != "evidence_digest"}
                )
                runs.append((label + "-retry", arm, again))
    return runs


def test_bundle_reports_unique_initialization_counts_per_order() -> None:
    doc = _m5_manifest()
    bundle = build_order_bundle(doc, _bundle_runs(doc))
    declaration = doc["order_robustness"]["m5_order_evidence"]
    a, b = declaration["order_manifest_ids"]
    counts = bundle["initialization_counts"]["by_order"]
    assert counts[a]["unique_replicate_identities"] == 3
    assert counts[b]["unique_replicate_identities"] == 2
    assert counts[a]["unique_replicate_identities_by_arm"] == {"m0": 3, "m1": 3}
    assert bundle["initialization_counts"]["by_scale_stage"]["50m"] == counts
    assert bundle["complete_pairs_by_order"] == {a: ["C0", "C2", "C4"], b: ["C1", "C3"]}
    assert bundle["canonical_membership_id"] == declaration["canonical_membership_id"]
    assert bundle["order_manifest_ids"] == [a, b]
    assert (
        bundle["declaration"]["canonical_membership_id"] == declaration["canonical_membership_id"]
    )
    assert bundle["comparison"]["manifest_hash"] == identity_digest(doc)
    assert set(bundle["derivation"]) == {a, b}
    assert "independent within-source document-order evidence" == bundle["terminology"]
    assert bundle_problems(bundle) == []


def test_same_seed_retries_do_not_inflate_initialization_counts() -> None:
    doc = _m5_manifest()
    bundle = build_order_bundle(doc, _bundle_runs(doc, retry=True))
    a, b = doc["order_robustness"]["m5_order_evidence"]["order_manifest_ids"]
    counts = bundle["initialization_counts"]["by_order"]
    assert counts[a]["unique_replicate_identities"] == 3
    assert counts[a]["unique_init_seeds"] == 3
    assert counts[a]["run_attempts"] == 8  # 3 tuples x 2 arms + one C0 retry per arm
    assert counts[a]["repeat_attempts_not_counted"] == 2
    assert counts[b]["unique_replicate_identities"] == 2
    assert bundle_problems(bundle) == []


def test_tampered_or_invalid_bundles_are_refused() -> None:
    doc = _m5_manifest()
    bundle = build_order_bundle(doc, _bundle_runs(doc))
    a = doc["order_robustness"]["m5_order_evidence"]["order_manifest_ids"][0]
    inflated = copy.deepcopy(bundle)
    inflated["initialization_counts"]["by_order"][a]["unique_replicate_identities"] = 5
    assert bundle_problems(inflated) == ["bundle hash does not verify (altered)"]
    resealed = seal_bundle({k: v for k, v in inflated.items() if k != "bundle_hash"})
    assert resealed["initialization_counts"]["by_order"][a]["unique_replicate_identities"] == 3
    forged = {k: v for k, v in inflated.items() if k != "bundle_hash"}
    forged["bundle_hash"] = identity_digest(forged)
    assert any("recompute" in p for p in bundle_problems(forged))
    # Missing C3 pairs: incomplete for the order-B stratum.
    partial = build_order_bundle(doc, [r for r in _bundle_runs(doc) if not r[0].endswith("-C3")])
    assert any("complete pairs" in p for p in bundle_problems(partial))
    assert bundle_problems(partial, require_complete=False) == []


def test_bundle_refuses_runs_on_another_membership() -> None:
    doc = _m5_manifest()
    runs = _bundle_runs(doc)
    label, arm, evidence = runs[0]
    evidence = copy.deepcopy(evidence)
    evidence["fields"]["canonical_membership_id"] = h("other")
    evidence["evidence_digest"] = identity_digest(
        {k: v for k, v in evidence.items() if k != "evidence_digest"}
    )
    bundle = build_order_bundle(doc, [(label, arm, evidence), *runs[1:]])
    assert any("declared membership" in p for p in bundle_problems(bundle))


def test_bundle_requires_a_valid_declaration() -> None:
    doc = superiority_manifest()
    with pytest.raises(OrderEvidenceError):
        build_order_bundle(doc, [])
