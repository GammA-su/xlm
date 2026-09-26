"""P35 M5: the pilot planner binds and verifies its frozen document order (AUTHORED).

Only the pure planner pieces are exercised here (schema, unresolved fields,
binding merge and the order preflight). The full ``plan_science_pilot`` path
needs torch; see ``tests/test_p35_m5_runtime.py`` (local certification). No real
Mix-01 order manifest exists or is invented: the checked-in draft keeps every
order pin null.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from p35_m5_support import default_documents, orders, write_sources
from xlm.data.ordering import write_order_manifest
from xlm.experiments.science_pilot import (
    BindingDocumentOrder,
    Findings,
    PilotBindings,
    PilotDocumentOrder,
    PilotPlanError,
    SciencePilotConfig,
    apply_bindings,
    check_document_order,
    unresolved_fields,
)

ROOT = Path(__file__).resolve().parents[1]
DRAFT = ROOT / "recipes/experiments/draft_science_v1_pilot_32m.yaml"
ORDER_ENTRIES = [
    "science_pilot.document_order.manifest",
    "science_pilot.document_order.order_manifest_id",
    "science_pilot.document_order.canonical_membership_id",
]


def _draft() -> dict[str, Any]:
    return json.loads(DRAFT.read_text(encoding="utf-8"))


def _codes(findings: Findings) -> list[str]:
    return [b.code for b in findings.blockers]


def test_checked_in_draft_leaves_the_order_unresolved() -> None:
    raw = _draft()
    pilot = SciencePilotConfig.model_validate(raw["science_pilot"])
    assert isinstance(pilot.document_order, PilotDocumentOrder)
    assert pilot.document_order.role == "fixed_single_order_not_an_independent_replicate"
    assert pilot.document_order.manifest is None  # no real Mix-01 order is invented
    missing = unresolved_fields(raw, pilot)
    assert [m for m in missing if "document_order" in m] == ORDER_ENTRIES
    findings = Findings()
    check_document_order(raw, pilot, findings)
    assert _codes(findings) == ["document_order_unresolved"]


def test_the_w_pilot_cannot_fall_back_to_shard_native_order() -> None:
    raw = _draft()
    raw["science_pilot"]["document_order"] = "shard_native_within_source_order"
    pilot = SciencePilotConfig.model_validate(raw["science_pilot"])
    assert any("pinned M5 order manifest" in m for m in unresolved_fields(raw, pilot))
    findings = Findings()
    check_document_order(raw, pilot, findings)
    assert _codes(findings) == ["document_order_unbound"]
    # Authored fixtures (M3 toy flows) keep the shard-native order.
    raw["science_pilot"]["contract"] = "authored_fixture"
    pilot = SciencePilotConfig.model_validate(raw["science_pilot"])
    findings = Findings()
    check_document_order(raw, pilot, findings)
    assert findings.blockers == []
    assert findings.preflight["document_order"]["policy"] == "shard_native_offset_order_v1"


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("order_manifest_id", "latest"),
        ("order_manifest_id", "ABC"),
        ("canonical_membership_id", "todo"),
        ("manifest", "relative/order.json"),
        ("manifest", "/abs/order-*.json"),
    ],
)
def test_order_binding_refuses_unpinned_identities(tmp_path: Path, key: str, value: str) -> None:
    # NOTE (local cert): the manifest fixture must be absolute on this platform;
    # the cloud `/abs/...` literal is not absolute on Windows (`Path.is_absolute`).
    good = {
        "manifest": str(tmp_path / "orders" / "a.json"),
        "order_manifest_id": "a" * 64,
        "canonical_membership_id": "b" * 64,
    }
    BindingDocumentOrder.model_validate(good)
    with pytest.raises(ValueError):
        BindingDocumentOrder.model_validate({**good, key: value})


def _pilot_with(raw: dict[str, Any], pins: dict[str, Any]) -> SciencePilotConfig:
    raw["science_pilot"]["document_order"] = {**raw["science_pilot"]["document_order"], **pins}
    return SciencePilotConfig.model_validate(raw["science_pilot"])


def _bound(tmp_path: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    readers = write_sources(tmp_path / "shards")
    order_a, _ = orders(readers)
    path = write_order_manifest(order_a, tmp_path / "orders" / "a.json")
    pins = {
        "manifest": str(path),
        "order_manifest_id": order_a["order_manifest_id"],
        "canonical_membership_id": order_a["canonical_membership_id"],
    }
    raw = _draft()
    raw["data"] = {
        "sources": {s: str(r.directory) for s, r in readers.items()},
        "document_order": dict(pins),
    }
    return raw, pins, order_a


def test_bound_order_verifies_against_the_pilot_shards(tmp_path: Path) -> None:
    raw, pins, order_a = _bound(tmp_path)
    pilot = _pilot_with(raw, pins)
    assert [m for m in unresolved_fields(raw, pilot) if "document_order" in m] == []
    findings = Findings()
    check_document_order(raw, pilot, findings)
    assert findings.blockers == []
    section = findings.preflight["document_order"]
    assert section["status"] == "VERIFIED"
    assert section["order_manifest_id"] == order_a["order_manifest_id"]
    assert section["independent_order_replicate"] is False


def test_order_for_another_membership_blocks(tmp_path: Path) -> None:
    raw, pins, _ = _bound(tmp_path)
    # Drop one document: a different membership over the same sources.
    docs = {s: default_documents(s) for s in ("alpha", "beta")}
    docs["alpha"].pop()
    fewer = write_sources(tmp_path / "fewer", docs)
    raw["data"]["sources"] = {s: str(r.directory) for s, r in fewer.items()}
    pilot = _pilot_with(raw, pins)
    findings = Findings()
    check_document_order(raw, pilot, findings)
    assert _codes(findings) == ["order_membership_mismatch"]


def test_pins_must_match_and_tampered_manifests_block(tmp_path: Path) -> None:
    raw, pins, order_a = _bound(tmp_path)
    wrong = {**pins, "canonical_membership_id": "c" * 64}
    raw_wrong = copy.deepcopy(raw)
    raw_wrong["data"]["document_order"] = dict(wrong)
    findings = Findings()
    check_document_order(raw_wrong, _pilot_with(copy.deepcopy(raw_wrong), wrong), findings)
    assert _codes(findings) == ["order_membership_pin_mismatch"]
    findings = Findings()
    check_document_order(raw_wrong, _pilot_with(copy.deepcopy(raw_wrong), pins), findings)
    assert "document_order_pin_mismatch" in _codes(findings)
    tampered = json.loads(Path(pins["manifest"]).read_text(encoding="utf-8"))
    ids = tampered["sources"]["alpha"]["ordered_doc_ids"]
    ids[0], ids[1] = ids[1], ids[0]
    Path(pins["manifest"]).write_text(json.dumps(tampered), encoding="utf-8")
    findings = Findings()
    check_document_order(raw, _pilot_with(copy.deepcopy(raw), pins), findings)
    assert _codes(findings) == ["order_manifest_tampered"]


def test_unpinned_reference_blocks(tmp_path: Path) -> None:
    raw, pins, _ = _bound(tmp_path)
    latest = {**pins, "order_manifest_id": "latest"}
    raw["data"]["document_order"] = latest
    findings = Findings()
    check_document_order(raw, _pilot_with(copy.deepcopy(raw), latest), findings)
    assert _codes(findings) == ["order_unpinned"]


def _bindings(tmp_path: Path, order: dict[str, Any] | None) -> PilotBindings:
    payload: dict[str, Any] = {
        "version": "xlm-science-pilot-bindings-v1",
        "data": {
            "sources": {"a": str(tmp_path)},
            "exposure_plan": str(tmp_path / "e.json"),
            "pool_artifact": None,
        },
        "tokenizer": {
            "artifact": str(tmp_path),
            "artifact_digest": "0" * 64,
            "fit_input_hash": "f",
        },
        "evaluation": {
            "quick_lm": None,
            "full_lm": None,
            "search_benchmark": None,
            "scoring": {"forward_precision": "fp32", "logprob_dtype": "fp64", "rolling_stride": 4},
            "group_assignments": None,
        },
        "profile_artifact": "profile_1",
        "storage_roots": {
            k: str(tmp_path) for k in ("data_root", "checkpoint_root", "temp_root", "output_root")
        }
        | {"evaluation_input_roots": [str(tmp_path)]},
        "capacity": {
            "checkpoint_size_source": {"kind": "measured_profile", "path": None},
            "evaluation_evidence_bytes": 0,
            "cache_bytes": 0,
            "safety_margin_bytes": 0,
        },
    }
    if order is not None:
        payload["document_order"] = order
    return PilotBindings.model_validate(payload)


def test_bindings_supply_the_order_into_data_and_pilot_pins(tmp_path: Path) -> None:
    raw = _draft()
    pilot = SciencePilotConfig.model_validate(raw["science_pilot"])
    order = {
        "manifest": str(tmp_path / "a.json"),
        "order_manifest_id": "a" * 64,
        "canonical_membership_id": "b" * 64,
    }
    merged = apply_bindings(raw, pilot, _bindings(tmp_path, order))
    assert merged["data"]["document_order"] == order
    section = merged["science_pilot"]["document_order"]
    assert section["role"] == "fixed_single_order_not_an_independent_replicate"
    assert {k: section[k] for k in order} == order
    # Without an order binding the §W draft stays unresolved (never defaulted).
    merged = apply_bindings(raw, pilot, _bindings(tmp_path, None))
    assert "document_order" not in merged["data"]
    rebound = SciencePilotConfig.model_validate(merged["science_pilot"])
    assert [m for m in unresolved_fields(merged, rebound) if "document_order" in m] == ORDER_ENTRIES
    # A draft that pre-binds the order, or declares the shard order, refuses bindings.
    prebound = copy.deepcopy(raw)
    prebound["science_pilot"]["document_order"]["manifest"] = "/abs/x.json"
    with pytest.raises(PilotPlanError, match="pre-binds"):
        apply_bindings(
            prebound,
            SciencePilotConfig.model_validate(prebound["science_pilot"]),
            _bindings(tmp_path, order),
        )
    native = copy.deepcopy(raw)
    native["science_pilot"]["document_order"] = "shard_native_within_source_order"
    with pytest.raises(PilotPlanError, match="shard-native"):
        apply_bindings(
            native,
            SciencePilotConfig.model_validate(native["science_pilot"]),
            _bindings(tmp_path, order),
        )


def test_order_manifest_must_lie_inside_the_data_root(tmp_path: Path) -> None:
    raw, pins, _ = _bound(tmp_path)
    raw["science_pilot"]["storage_roots"]["data_root"] = str((tmp_path / "shards").resolve())
    findings = Findings()
    check_document_order(raw, _pilot_with(copy.deepcopy(raw), pins), findings)
    assert _codes(findings) == ["document_order_outside_root"]  # orders/ is outside shards/
    raw["science_pilot"]["storage_roots"]["data_root"] = str(tmp_path.resolve())
    findings = Findings()
    check_document_order(raw, _pilot_with(copy.deepcopy(raw), pins), findings)
    assert findings.blockers == []
