"""Evidence-v2.0 core mechanisms: authored synthetic fixtures only.

No network (sockets blocked), no X: reads, no real acquisition. Covers
canonical JSON behavior, frozen constants, offline inventory
reconstruction/ranking, window-v2 identities, and shared arm budgets.
"""

from __future__ import annotations

import hashlib
import json
import socket
from pathlib import Path
from typing import Any

import pytest

from xlm.data.evidence_v2 import budgets, canonical, frozen, inventory, windows

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V2"


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_: Any, **__: Any) -> None:
        raise AssertionError("network access attempted in an offline test")

    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)


# --------------------------------------------------------------------------
# Canonical construction.
# --------------------------------------------------------------------------


def test_canonical_bytes_form() -> None:
    raw = canonical.canonical_bytes({"b": [1, 2], "a": "x"})
    assert raw == b'{"a":"x","b":[1,2]}'
    assert canonical.digest({"a": 1}) == hashlib.sha256(b'{"a":1}').hexdigest()


def test_canonical_rejects_nonfinite() -> None:
    with pytest.raises(canonical.CanonicalError):
        canonical.canonical_bytes({"a": float("nan")})
    with pytest.raises(canonical.CanonicalError):
        canonical.loads_strict('{"a": Infinity}')
    with pytest.raises(canonical.CanonicalError):
        canonical.loads_strict('{"a": 1, "a": 2}')
    with pytest.raises(canonical.CanonicalError):
        canonical.loads_bytes_strict(b"\xef\xbb\xbf{}")
    with pytest.raises(canonical.CanonicalError):
        canonical.loads_bytes_strict(b"\xff\xfe{}")


def test_canonical_rejects_unknown_fields() -> None:
    with pytest.raises(canonical.CanonicalError, match="unknown fields"):
        canonical.check_known_fields({"a": 1, "zzz": 2}, frozenset({"a"}), what="t")
    canonical.check_known_fields({"a": 1}, frozenset({"a"}), what="t")


def test_self_digest_excludes_only_digest() -> None:
    body = {"a": 1, "digest": "old"}
    assert canonical.self_digest(body) == canonical.digest({"a": 1})


def test_selection_keys_are_strict() -> None:
    assert canonical.check_sequence_of_strings(["a", "b"], what="k") == ("a", "b")
    with pytest.raises(canonical.CanonicalError):
        canonical.check_sequence_of_strings(["a", 1], what="k")
    assert canonical.check_exact_int(3, what="n") == 3
    with pytest.raises(canonical.CanonicalError):
        canonical.check_exact_int(True, what="n")
    with pytest.raises(canonical.CanonicalError):
        canonical.check_ordered_paths(["b", "a"], what="p")


def test_atomic_write_roundtrip(tmp_path: Path) -> None:
    target = tmp_path / "sub" / "obj.json"
    binding = canonical.write_canonical_json(target, {"a": [1]})
    assert target.read_bytes() == b'{"a":[1]}'
    canonical.verify_binding(target, binding, what="t")
    with pytest.raises(canonical.CanonicalError, match="drift"):
        canonical.verify_binding(target, {"bytes": 1, "sha256": "0" * 64}, what="t")


# --------------------------------------------------------------------------
# Frozen bindings.
# --------------------------------------------------------------------------


def test_frozen_values_match_protocol() -> None:
    assert frozen.PROTOCOL_VERSION == "essential-web-evidence-v2.0"
    assert frozen.METADATA_SEED == 20260927
    assert frozen.REVIEW_ORDER_SEED == 20260928
    assert frozen.POLICY_DIGEST == (
        "f4357f61f434d5105d823266153dac6372e122f12767187631e8796b4899dd07"
    )
    assert frozen.FREEZE_DIGEST == (
        "fe2157799a86fe777e45c220716563f8a73245a12593c40909222555bf1b8248"
    )
    assert frozen.COMPLETE_INVENTORY_DIGEST == (
        "d2b3eac5dca002336a97654564a13c93e8e4696e6a1624a6fdf3c012fd0927d5"
    )
    assert len(frozen.STRATA) == 8
    assert [s[0] for s in frozen.STRATA] == [
        "2014-15",
        "2015-32",
        "2016-50",
        "2018-05",
        "2019-09",
        "2021-04",
        "2021-49",
        "2024-26",
    ]
    assert frozen.ARM_M_LIMITS["requests_total"] == 880
    assert frozen.ARM_M_LIMITS["pilot_requests_per_plan"] == 100
    assert frozen.ARM_T_LIMITS["selected_rows_max"] == 118
    assert frozen.ARM_T_LIMITS["document_bytes_max"] == 65536
    assert [name for name, _, _ in frozen.TEXT_STRATA] == [
        "B_science_census",
        "D_only_science_census",
        "B_practical_survivor",
        "B_practical_loss",
        "D_only_practical",
        "B_prose_survivor",
        "B_prose_loss",
        "D_only_prose",
    ]


# --------------------------------------------------------------------------
# Freeze + inventory verification against the committed frozen files.
# --------------------------------------------------------------------------


def test_freeze_binding_verifies() -> None:
    freeze = json.loads((EVIDENCE / "freeze.json").read_bytes())
    root_files = {
        a["relative_path"]: (ROOT / a["relative_path"]).read_bytes() for a in freeze["artifacts"]
    }
    inventory.verify_freeze_binding(freeze, root_files)


def test_freeze_binding_refuses_drift() -> None:
    freeze = json.loads((EVIDENCE / "freeze.json").read_bytes())
    root_files = {
        a["relative_path"]: (ROOT / a["relative_path"]).read_bytes() for a in freeze["artifacts"]
    }
    tampered = dict(freeze)
    tampered["digest"] = "0" * 64
    with pytest.raises(inventory.InventoryError):
        inventory.verify_freeze_binding(tampered, root_files)
    short = {k: v for k, v in root_files.items()}
    first = next(iter(short))
    short[first] = short[first] + b" "
    with pytest.raises(inventory.InventoryError):
        inventory.verify_freeze_binding(freeze, short)


def test_inventory_freeze_reconstructs_offline() -> None:
    freeze = json.loads((EVIDENCE / "inventory-freeze.json").read_bytes())
    result = inventory.verify_inventory_freeze(freeze)
    assert result["eligible_total"] == 23200
    assert result["winners"] == [
        "data/crawl=CC-MAIN-2014-15/train-01860-of-02772.parquet",
        "data/crawl=CC-MAIN-2015-32/train-01682-of-01920.parquet",
        "data/crawl=CC-MAIN-2016-50/train-02156-of-03132.parquet",
        "data/crawl=CC-MAIN-2018-05/train-03378-of-03429.parquet",
        "data/crawl=CC-MAIN-2019-09/train-00153-of-02577.parquet",
        "data/crawl=CC-MAIN-2021-04/train-00179-of-03315.parquet",
        "data/crawl=CC-MAIN-2021-49/train-00408-of-02895.parquet",
        "data/crawl=CC-MAIN-2024-26/train-01127-of-03168.parquet",
    ]


def test_ranking_tie_breaks_by_path_bytes() -> None:
    tied = ["data/c/b.parquet", "data/c/a.parquet"]
    ranks = {p: inventory.rank_digest("crawl=CC-MAIN-2014-15", p) for p in tied}
    winner, _ = inventory.select_winner(tied, "crawl=CC-MAIN-2014-15")
    expected = min(tied, key=lambda p: (ranks[p], p.encode("utf-8")))
    assert winner == expected


def test_inventory_refuses_mismatch() -> None:
    freeze = json.loads((EVIDENCE / "inventory-freeze.json").read_bytes())
    bad = json.loads(json.dumps(freeze))
    bad["strata"][0]["selected_file"] = bad["strata"][0]["excluded_file"]
    with pytest.raises(inventory.InventoryError):
        inventory.verify_inventory_freeze(bad)
    bad2 = json.loads(json.dumps(freeze))
    bad2["strata"][3] = dict(bad2["strata"][3], file_count=9999)
    with pytest.raises(inventory.InventoryError):
        inventory.verify_inventory_freeze(bad2)


def test_unavailable_winner_is_stop_not_rerank() -> None:
    # The mechanism offers no next-rank API: callers only get the winner.
    assert not hasattr(inventory, "next_rank")
    assert not hasattr(inventory, "rerank")


# --------------------------------------------------------------------------
# Window-v2 identities.
# --------------------------------------------------------------------------


def test_window_index_matches_literal_protocol_formula() -> None:
    parts = ["essential_web", "selector_recon", frozen.REVISION, "f.parquet", "window-v2", "g"]
    literal = (
        int(hashlib.sha256("|".join(["20260927", *parts]).encode("utf-8")).hexdigest(), 16) % 97
    )
    assert windows.index(parts, 97) == literal


def test_window_group_and_start_construction() -> None:
    slot = windows.choose_group("f.parquet", 5)
    assert 0 <= slot < 5
    with pytest.raises(windows.WindowError):
        windows.choose_group("f.parquet", 0)
    assert windows.start_domain(1000) == 1000
    assert windows.start_domain(20000) == 16384
    start = windows.choose_start("f.parquet", 2, 1000, 512)
    assert 0 <= start <= 1000 - 512
    with pytest.raises(windows.WindowError, match="not the frozen 512"):
        windows.choose_start("f.parquet", 2, 400, 400)


def test_freeze_window_records_absolute_and_relative() -> None:
    frozen_window = windows.freeze_window(
        "f.parquet", [0, 2, 4], [5000, 9000, 300], [0, 5000, 14000]
    )
    assert frozen_window["keep"] == 512
    slot = frozen_window["eligible_group_slot"]
    assert frozen_window["group_index"] == [0, 2, 4][slot]
    absolute = frozen_window["absolute_window"]
    assert absolute[1] - absolute[0] == 512
    assert absolute[0] == frozen_window["group_start"] + frozen_window["start_in_group"]
    assert frozen_window["expected_scan_rows"] == min(
        frozen_window["group_rows"],
        ((frozen_window["start_in_group"] + 512 + 255) // 256) * 256,
    )


def test_freeze_window_short_group_refuses() -> None:
    with pytest.raises(windows.WindowError):
        windows.freeze_window("f.parquet", [0], [100], [0])


def test_window_request_pins_frozen_fields() -> None:
    request = windows.sampling_request("data/crawl=CC-MAIN-2014-15/train-01860-of-02772.parquet")
    assert request["source_id"] == "essential_web"
    assert request["view_id"] == "selector_recon"
    assert request["revision"] == frozen.REVISION
    assert request["seed"] == 20260927
    assert request["projected_fields"] == ["eai_taxonomy", "quality_signals"]
    assert request["physical_layout"].startswith("UNRESOLVED")
    windows.check_projection(request["projected_fields"])
    with pytest.raises(windows.WindowError):
        windows.check_projection(["eai_taxonomy"])
    dry = windows.dry_arm_plan([f"f{i}.parquet" for i in range(8)])
    assert dry["executable"] is False
    assert len(dry["plans"]) == 8
    with pytest.raises(windows.WindowError):
        windows.dry_arm_plan(["only.parquet"])
    with pytest.raises(windows.WindowError):
        windows.check_plan_shape({"plans": [{"block_records": 4096, "target_records": 4096}]})


# --------------------------------------------------------------------------
# Shared arm budgets.
# --------------------------------------------------------------------------


def test_arm_m_ceilings_exact() -> None:
    ledger = budgets.new_arm_m()
    assert ledger.ceilings["requests_total"] == 880
    assert ledger.ceilings["data_requests_total"] == 800
    assert ledger.ceilings["response_body_bytes_total"] == 268435456
    assert ledger.ceilings["disk_combined_bytes"] == 536870912


def test_footer_and_execution_share_one_counter() -> None:
    ledger = budgets.new_arm_m()
    for _ in range(10):
        ledger.charge_file_request("f.parquet", kind="footer")
    with pytest.raises(budgets.BudgetRefusal):
        ledger.charge_file_request("f.parquet", kind="footer")
    # Execution continues on the SAME ledger. The refused 11th footer
    # attempt already consumed the shared counter (failures are charged).
    for _ in range(70):
        ledger.charge_request(kind="data")
    assert ledger.requests == 81
    # No reset API exists.
    assert not hasattr(ledger, "reset")


def test_retries_redirects_failures_charged() -> None:
    ledger = budgets.new_arm_t()
    ledger.charge_request(kind="data", retried=True, redirected_hops=3)
    ledger.charge_request(kind="data", failed=True)
    assert ledger.requests == 2
    assert ledger.retries == 1
    assert ledger.redirects == 3
    assert ledger.failed_requests == 1
    with pytest.raises(budgets.BudgetRefusal):
        ledger.charge_request(kind="data", redirected_hops=4)


def test_resume_does_not_reset_budget() -> None:
    ledger = budgets.new_arm_m()
    ledger.charge_request(kind="footer")
    snapshot = ledger.snapshot()
    assert snapshot["requests"] == 1
    # Resume = keep using the same object; a fresh object starts empty,
    # so callers must thread the original through.
    assert budgets.ArmLedger is type(ledger)


def test_transfer_and_decompressed_bounds() -> None:
    ledger = budgets.new_arm_m()
    ledger.charge_transfer("f.parquet", 4194304, kind="footer")
    with pytest.raises(budgets.BudgetRefusal):
        ledger.charge_transfer("f.parquet", 1, kind="footer")
    ledger.charge_decompressed("f.parquet", 67108864, kind="data")
    with pytest.raises(budgets.BudgetRefusal):
        ledger.charge_decompressed("f.parquet", 1, kind="data")


def test_disk_counts_partials_and_cache() -> None:
    ledger = budgets.new_arm_t()
    ledger.reserve_disk(scratch=1000, final=500, kind="partial")
    ledger.reserve_disk(scratch=64, kind="cache")
    assert ledger.snapshot()["disk_scratch"] == 1064
    ledger.release_disk(scratch=1064, final=500)
    with pytest.raises(budgets.BudgetRefusal):
        ledger.reserve_disk(final=16777217, kind="oversize-final")


def test_time_and_memory_refusal_paths() -> None:
    ledger = budgets.new_arm_t()
    ledger.charge_time(1799.0, kind="stage")
    with pytest.raises(budgets.BudgetRefusal):
        ledger.charge_time(2.0, kind="stage")
    with pytest.raises(budgets.BudgetRefusal):
        ledger.check_memory(268435457, kind="supervisor")
    with pytest.raises(budgets.BudgetRefusal, match="no safe decoder upper bound"):
        ledger.preflight_decode(None, kind="decode", cap_bytes=67108864)
    with pytest.raises(budgets.BudgetRefusal, match="exceeds cap"):
        ledger.preflight_decode(67108865, kind="decode", cap_bytes=67108864)


def test_pilot_ceiling_not_loosened() -> None:
    assert frozen.PILOT_REQUEST_CEILING == 100
    assert frozen.ARM_M_LIMITS["pilot_requests_per_plan"] == 100
    assert frozen.ARM_M_LIMITS["data_requests_per_plan"] == 100
