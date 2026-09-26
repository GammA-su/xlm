"""P35 pilot readiness: microbatch_grouping_v2 requires the global-update payload chain.

Authored M1-M3-shaped checkpoints through the real ArtifactStore (SYNTHETIC,
placeholder weights); no training. ``microbatch_grouping_v1`` keeps its meaning.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from test_p35_m4_evidence import BATCH, BUDGET, TUPLES, _count, _manifest, publish_run
from xlm.comparison.science_compare import RunEntry, compare_science
from xlm.comparison.science_evidence import (
    EVIDENCE_VERSION,
    LEGACY_EVIDENCE_VERSIONS,
    EvidenceError,
    evidence_fields,
    extract_run_evidence,
    verify_evidence_record,
)
from xlm.comparison.science_manifest import validate_manifest
from xlm.comparison.science_tracks import (
    MICROBATCH_TRACK,
    MICROBATCH_TRACK_V2,
    MIXTURE_TRACK,
    SCIENCE_TRACKS,
    SCIENTIFIC_FIELDS,
    TRACK_TABLE_VERSION,
    FieldClass,
    get_track,
)
from xlm.data.sampling.update_payload import UpdatePayloadChain

ROOT = Path(__file__).resolve().parents[1]
PAYLOAD_FIELDS = ("update_payload_receipt", "update_payload_chain_digest")


def chain(tag: str) -> dict[str, Any]:
    """A committed chain over the authored LR rows (BUDGET // BATCH updates of BATCH)."""
    built = UpdatePayloadChain()
    for i in range(BUDGET // BATCH):
        built.stage(
            step=i + 1,
            committed_before=i * BATCH,
            valid_targets=BATCH,
            payload=f"{tag}-{i}".encode().hex().ljust(64, "0")[:64],
        )
        built.commit()
    return built.to_dict()


def entries(root: Path, payloads: dict[str, dict[str, Any] | None]) -> list[RunEntry]:
    runs = []
    for t, i, r, d in TUPLES:
        for arm, size in (("b8", 8), ("b16", 16)):
            payload = payloads.get(f"{arm}-{t}", payloads.get(arm))
            ckpt = publish_run(
                root / f"{arm}-{t}",
                f"run-{arm}-{t}",
                (i, r, d),
                microbatch=size,
                primary=3.0 + (0.01 if arm == "b16" else 0.0) + 0.001 * i / 100,
                update_payloads=copy.deepcopy(payload) if payload is not None else None,
            )
            runs.append(
                RunEntry(
                    label=f"{arm}-{t}",
                    arm_id=arm,
                    status="completed",
                    failure=None,
                    evidence=extract_run_evidence(ckpt, parameter_counter=_count),
                    synthetic=True,
                )
            )
    return runs


def v2_manifest() -> dict[str, Any]:
    manifest = _manifest()
    manifest["track"] = "microbatch_grouping_v2"
    manifest["required_invariants"] = MICROBATCH_TRACK_V2.must_match()
    return manifest


def candidate(record: dict[str, Any]) -> dict[str, Any]:
    [only] = record["candidates"]
    return only  # type: ignore[no-any-return]


def violation_fields(result: dict[str, Any]) -> set[str]:
    return {v["field"] for v in result["field_violations"]}


# ----------------------------------------------------------------- tracks


def test_track_tables_classify_the_new_fields() -> None:
    assert TRACK_TABLE_VERSION == "xlm-science-tracks-v3"
    for track in SCIENCE_TRACKS.values():
        assert set(track.table()) == set(SCIENTIFIC_FIELDS)
    for name in PAYLOAD_FIELDS:
        assert MICROBATCH_TRACK_V2.classify(name) is FieldClass.MUST_MATCH
        assert MICROBATCH_TRACK.classify(name) is FieldClass.RECORDED_MAY_DIFFER
        assert MIXTURE_TRACK.classify(name) is FieldClass.RECORDED_MAY_DIFFER
    assert MICROBATCH_TRACK_V2.varied == MICROBATCH_TRACK.varied == {"microbatch_sequences"}
    v1_must = set(MICROBATCH_TRACK.must_match())
    assert set(MICROBATCH_TRACK_V2.must_match()) == v1_must | set(PAYLOAD_FIELDS)
    for fixed in (
        "global_batch_valid_targets",
        "update_boundaries_digest",
        "order_manifest_id",
        "canonical_membership_id",
        "optimizer",
        "schedule",
        "evaluation_plan_digest",
    ):
        assert fixed in v1_must
    assert MICROBATCH_TRACK_V2.classify("final_model_state_digest") is (
        FieldClass.RECORDED_MAY_DIFFER
    )
    assert get_track("microbatch_grouping_v1") is MICROBATCH_TRACK


def test_the_b8_b16_b32_draft_uses_the_strengthened_track() -> None:
    path = ROOT / "recipes/science_comparisons/draft_science_v1_microbatch_b8_b16_b32.yaml"
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert doc["track"] == "microbatch_grouping_v2"
    assert set(PAYLOAD_FIELDS) <= set(doc["required_invariants"])
    assert doc["practical_margin"] is None and doc["noninferiority_margin"] is None
    fields = {p.field for p in validate_manifest(doc).problems}
    assert fields == {"status"}  # nonexecutable draft; margins left to the user


# --------------------------------------------------------------- evidence


def test_extraction_reads_and_verifies_the_chain(tmp_path: Path) -> None:
    saved = chain("same")
    evidence = extract_run_evidence(
        publish_run(tmp_path, "r", (1, 2, 3), update_payloads=saved), parameter_counter=_count
    )
    assert evidence["evidence_version"] == EVIDENCE_VERSION == "xlm-science-run-evidence-v3"
    assert evidence["fields"]["update_payload_receipt"] == "global_update_payload_digest_v1"
    assert evidence["fields"]["update_payload_chain_digest"] == saved["head"]
    verify_evidence_record(evidence)


def test_runs_without_the_receipt_extract_as_unknown(tmp_path: Path) -> None:
    evidence = extract_run_evidence(publish_run(tmp_path, "r", (1, 2, 3)), parameter_counter=_count)
    assert all(evidence["fields"][name] is None for name in PAYLOAD_FIELDS)


@pytest.mark.parametrize("damage", ["link", "rows_vs_lr"])
def test_an_inconsistent_chain_is_refused_never_treated_as_unknown(
    tmp_path: Path, damage: str
) -> None:
    saved = chain("x")
    if damage == "link":
        saved["rows"][1][3] = "f" * 64
    else:
        built = UpdatePayloadChain()
        for i in range(BUDGET // BATCH - 1):  # one committed update missing
            built.stage(step=i + 1, committed_before=i * BATCH, valid_targets=BATCH,
                        payload="a" * 64)  # fmt: skip
            built.commit()
        saved = built.to_dict()
    ckpt = publish_run(tmp_path, "r", (1, 2, 3), update_payloads=saved)
    with pytest.raises(EvidenceError, match="update payload chain"):
        extract_run_evidence(ckpt, parameter_counter=_count)


def test_pre_readiness_records_stay_verifiable_and_read_as_unknown() -> None:
    assert LEGACY_EVIDENCE_VERSIONS == (
        "xlm-science-run-evidence-v1",
        "xlm-science-run-evidence-v2",
    )
    v2 = {"evidence_version": "xlm-science-run-evidence-v2", "fields": {"a": 1}}
    from xlm.artifacts.manifest import identity_digest

    v2["evidence_digest"] = identity_digest(v2)
    verify_evidence_record(v2)
    fields = evidence_fields(v2)
    assert fields["update_payload_chain_digest"] is None and fields["a"] == 1
    assert "update_payload_chain_digest" not in v2["fields"]  # never rewritten
    forged = {"evidence_version": "xlm-science-run-evidence-v2",
              "fields": {"update_payload_chain_digest": "0" * 64}}  # fmt: skip
    forged["evidence_digest"] = identity_digest(forged)
    with pytest.raises(EvidenceError, match="cannot carry payload receipts"):
        verify_evidence_record(forged)


# --------------------------------------------------------------- eligibility


def test_identical_payload_chains_are_eligible_on_v2(tmp_path: Path) -> None:
    same = chain("same")
    record = compare_science(v2_manifest(), entries(tmp_path, {"b8": same, "b16": same}))
    result = candidate(record)
    assert result["eligible"], result["field_violations"]
    assert result["pairing"]["n_complete"] == len(TUPLES)
    for pair in result["pairing"]["pairs"]:
        diffs = {row["field"]: row for row in pair["field_diff"]}
        assert diffs["update_payload_chain_digest"]["status"] == "match"
        assert diffs["update_payload_chain_digest"]["classification"] == "MUST_MATCH"
        # Final weights differ between groupings; they are recorded, never compared.
        final = diffs["final_model_state_digest"]
        assert final["classification"] == "RECORDED_MAY_DIFFER"
        assert final["status"] == "recorded_difference"


def test_a_payload_chain_mismatch_is_ineligible_on_v2(tmp_path: Path) -> None:
    runs = entries(tmp_path, {"b8": chain("b8-data"), "b16": chain("b16-data")})
    result = candidate(compare_science(v2_manifest(), runs))
    assert not result["eligible"]
    assert "update_payload_chain_digest" in violation_fields(result)
    assert result["decision"]["result"] == "INELIGIBLE"


def test_a_run_without_the_receipt_is_ineligible_on_v2(tmp_path: Path) -> None:
    runs = entries(tmp_path, {"b8": chain("same"), "b16": None})
    result = candidate(compare_science(v2_manifest(), runs))
    assert not result["eligible"]
    assert {"update_payload_chain_digest", "update_payload_receipt"} <= violation_fields(result)


def test_historical_v1_keeps_its_meaning(tmp_path: Path) -> None:
    runs = entries(tmp_path, {"b8": chain("b8-data"), "b16": None})
    result = candidate(compare_science(_manifest(), runs))
    assert result["eligible"], result["field_violations"]  # v1 never required receipts
    assert "update_payload_chain_digest" not in violation_fields(result)
