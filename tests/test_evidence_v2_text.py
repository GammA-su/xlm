"""Evidence-v2.0 text-arm mechanisms: authored synthetic fixtures only.

No network (sockets blocked), no X: reads, no real text. Covers Arm-T
locator selection (census, ranking, precedence, shortfalls), sparse
retention, blinding/review packages, the frozen rubric, receipts, and
the offline CLI surface.
"""

from __future__ import annotations

import importlib.util
import json
import socket
import sys
from pathlib import Path
from typing import Any

import pytest

from xlm.data.evidence_v2 import blinding, canonical, frozen, receipts, rubric, sparse, text_select

ROOT = Path(__file__).resolve().parents[1]
CRAWLS = ["crawl=CC-MAIN-2014-15", "crawl=CC-MAIN-2021-04"]


def _cli() -> Any:
    path = ROOT / "scripts" / "evidence_v2.py"
    spec = importlib.util.spec_from_file_location("evidence_v2_cli", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["evidence_v2_cli"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_: Any, **__: Any) -> None:
        raise AssertionError("network access attempted in an offline test")

    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)


def _row(
    file: str,
    row: int,
    crawl: str,
    b_normal: str,
    d_normal: str | None = None,
    b_strict: str | None = None,
) -> dict[str, Any]:
    final = {f"{p}-{t}": "rejected" for p in "ABCD" for t in ("normal", "strict")}
    final["B-normal"] = b_normal
    final["D-normal"] = d_normal if d_normal is not None else b_normal
    final["B-strict"] = b_strict if b_strict is not None else b_normal
    return {
        "locator": [frozen.REPOSITORY, frozen.REVISION, file, row],
        "crawl": crawl,
        "final": final,
    }


def _science_world() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for i in range(29):
        rows.append(_row("dev-a.parquet", 1000 + i, CRAWLS[i % 2], "essential_science"))
    for i in range(25):
        rows.append(
            _row(
                "dev-b.parquet",
                2000 + i,
                CRAWLS[i % 2],
                "rejected",
                d_normal="essential_science",
            )
        )
    return rows


# --------------------------------------------------------------------------
# Census and integrity.
# --------------------------------------------------------------------------


def test_census_exact_29_25() -> None:
    selection = text_select.select_text(_science_world(), CRAWLS)
    census = {c["stratum"]: c for c in selection["cells"] if "requested" in c}
    assert census["B_science_census"]["selected"] == 29
    assert census["D_only_science_census"]["selected"] == 25
    assert census["B_science_census"]["shortfall"] == 0
    assert selection["total"] == 54


def test_census_mismatch_stops() -> None:
    rows = _science_world()[:-1]
    with pytest.raises(text_select.SelectionError, match="not exactly 25"):
        text_select.select_text(rows, CRAWLS)


def test_d_only_with_b_switch_stops() -> None:
    rows = _science_world()
    rows.append(
        _row("dev-c.parquet", 1, CRAWLS[0], "essential_prose", d_normal="essential_science")
    )
    with pytest.raises(text_select.SelectionError, match="not B-rejected"):
        text_select.select_text(rows, CRAWLS)


def test_unknown_crawl_stops() -> None:
    rows = _science_world()
    with pytest.raises(text_select.SelectionError, match="outside the frozen order"):
        text_select.select_text(rows, ["crawl=CC-MAIN-2099-99"])


# --------------------------------------------------------------------------
# Ranked strata: precedence, conflicts, shortfalls, determinism.
# --------------------------------------------------------------------------


def _ranked_world() -> list[dict[str, Any]]:
    rows = _science_world()
    # B practical survivors and losses in both crawls.
    for crawl in CRAWLS:
        rows.append(
            _row("dev-p.parquet", 10, crawl, "essential_practical", b_strict="essential_practical")
        )
        rows.append(_row("dev-p.parquet", 11, crawl, "essential_practical", b_strict="rejected"))
        rows.append(_row("dev-q.parquet", 12, crawl, "rejected", d_normal="essential_practical"))
        rows.append(_row("dev-q.parquet", 13, crawl, "rejected", d_normal="essential_practical"))
        rows.append(_row("dev-r.parquet", 14, crawl, "essential_prose", b_strict="essential_prose"))
        rows.append(_row("dev-r.parquet", 15, crawl, "essential_prose", b_strict="rejected"))
    # A single D-only prose candidate in the first crawl only: the second
    # crawl cell must record a shortfall, never borrow across crawls.
    rows.append(_row("dev-s.parquet", 16, CRAWLS[0], "rejected", d_normal="essential_prose"))
    return rows


def test_ranked_cells_counts_and_shortfalls() -> None:
    selection = text_select.select_text(_ranked_world(), CRAWLS)
    cells = {c["stratum"]: c for c in selection["cells"] if "crawls" in c}
    assert cells["B_practical_survivor"]["crawls"][0]["selected"] == 1
    assert cells["D_only_practical"]["crawls"][0]["selected"] == 2
    # crawl-1 has a single D-only prose candidate: shortfall 1, no borrowing.
    d_prose = cells["D_only_prose"]
    assert d_prose["crawls"][0]["selected"] == 1
    assert d_prose["crawls"][0]["shortfall"] == 1
    assert d_prose["crawls"][1]["selected"] == 0
    assert d_prose["crawls"][1]["shortfall"] == 2
    assert selection["total"] <= 118


def test_precedence_ownership_and_conflicts() -> None:
    rows = _science_world()
    # Corrupt duplicate locator: (dev-a.parquet, 1000) is already owned by
    # the B-science census, but a contradictory copy claims B-practical
    # survivor eligibility. The later cell must record a conflict and must
    # not reselect the owned locator.
    rows.append(
        _row(
            "dev-a.parquet", 1000, CRAWLS[0], "essential_practical", b_strict="essential_practical"
        )
    )
    selection = text_select.select_text(rows, CRAWLS)
    cells = {c["stratum"]: c for c in selection["cells"] if "crawls" in c}
    survivor = cells["B_practical_survivor"]["crawls"][0]
    assert survivor["eligible"] == 1
    assert survivor["conflicts"] == 1
    assert survivor["selected"] == 0
    assert survivor["shortfall"] == 1
    assert survivor["identities"] == []
    keys = [
        (i[2], i[3])
        for c in selection["cells"]
        for cvs in ([c] if "identities" in c else c["crawls"])
        for i in cvs["identities"]
    ]
    assert len(set(keys)) == len(keys)
    assert selection["total"] == 54


def test_ranking_deterministic_and_stratum_sensitive() -> None:
    loc = [frozen.REPOSITORY, frozen.REVISION, "f.parquet", 7]
    first = text_select.rank_digest("B_practical_survivor", CRAWLS[0], loc)
    assert first == text_select.rank_digest("B_practical_survivor", CRAWLS[0], loc)
    assert first != text_select.rank_digest("B_practical_loss", CRAWLS[0], loc)
    assert first != text_select.rank_digest("B_practical_survivor", CRAWLS[1], loc)


def test_selection_deterministic_across_runs() -> None:
    first = text_select.select_text(_ranked_world(), CRAWLS)
    second = text_select.select_text(_ranked_world(), CRAWLS)
    assert canonical.digest(first) == canonical.digest(second)


def test_manifest_seal_roundtrip() -> None:
    selection = text_select.select_text(_science_world(), CRAWLS)
    manifest = text_select.build_manifest(
        selection, evaluator_hash="ab" * 32, command="select-text", exit_status=0
    )
    sealed = text_select.seal(manifest)
    assert sealed["digest"] == canonical.self_digest(manifest)
    assert sealed["freeze_digest"] == frozen.FREEZE_DIGEST
    assert sealed["policy_digest"] == frozen.POLICY_DIGEST
    assert sealed["total_selected"] == 54


def test_policy_spec_loads_frozen_digest() -> None:
    evaluator, code_hash = text_select.load_evaluator(ROOT / "scripts")
    assert len(code_hash) == 64
    _, digest = text_select.load_policy_spec(evaluator, ROOT / "recipes")
    assert digest == frozen.POLICY_DIGEST


# --------------------------------------------------------------------------
# Sparse retention.
# --------------------------------------------------------------------------


def test_sparse_grouping_and_batches() -> None:
    plan = sparse.group_wanted_rows([7, 3, 300], [(0, 256), (256, 512)])
    assert plan == {0: [3, 7], 1: [300]}
    with pytest.raises(sparse.SparseError):
        sparse.group_wanted_rows([999], [(0, 256)])
    with pytest.raises(sparse.SparseError):
        sparse.group_wanted_rows([], [(0, 256)])
    assert sparse.decode_batches(256, 300) == [(256, 512)]
    assert sparse.decode_batches(0, 7) == [(0, 256)]


def test_sparse_retains_only_wanted() -> None:
    decoded = [(4, "d"), (5, "e"), (6, "f")]
    kept, gaps = sparse.retain_selected(decoded, frozenset({5}))
    assert kept == [(5, "e")]
    assert gaps == 2
    with pytest.raises(sparse.SparseError):
        sparse.retain_selected([(5, "a"), (5, "b")], frozenset({5}))


def test_sparse_statuses_no_truncation() -> None:
    loc = ["r", "v", "f.parquet", 1]
    ok = sparse.classify_retained("x" * 65536, locator=loc)
    assert ok["status"] == "full_text_available" and ok["utf8_bytes"] == 65536
    big = sparse.classify_retained("x" * 65537, locator=loc)
    assert big["status"] == "unreviewable_full_document_due_to_size"
    assert big["excerpt"] is None and big["utf8_bytes"] == 65537
    assert sparse.classify_retained("", locator=loc)["status"] == (
        "unreviewable_missing_or_invalid_text"
    )
    assert sparse.classify_retained(None, locator=loc)["status"] == (
        "unreviewable_missing_or_invalid_text"
    )
    assert sparse.classify_retained(42, locator=loc)["status"] == (
        "unreviewable_missing_or_invalid_text"
    )
    partial = sparse.classify_retained("x", locator=loc, reason="timeout")
    assert partial["status"] == "acquisition_incomplete"


def test_sparse_retained_budget_counts_unique_once() -> None:
    items = [
        {"status": "full_text_available", "sha256": "a" * 64, "utf8_bytes": 10},
        {"status": "full_text_available", "sha256": "a" * 64, "utf8_bytes": 10},
        {"status": "unreviewable_full_document_due_to_size", "utf8_bytes": 10**9},
    ]
    assert sparse.check_retained_budget(items) == 10
    heavy = [
        {"status": "full_text_available", "sha256": f"{i:064d}", "utf8_bytes": 65536}
        for i in range(129)
    ]
    with pytest.raises(sparse.SparseError):
        sparse.check_retained_budget(heavy)


def test_sparse_alias_consolidation_keeps_membership() -> None:
    items = [
        {
            "status": "full_text_available",
            "sha256": "d" * 64,
            "locator": ["r", "v", "b.parquet", 2],
            "stratum": "D_only_prose",
            "crawl": CRAWLS[1],
        },
        {
            "status": "full_text_available",
            "sha256": "d" * 64,
            "locator": ["r", "v", "a.parquet", 9],
            "stratum": "B_prose_survivor",
            "crawl": CRAWLS[0],
        },
    ]
    rank_s = {name: i for i, (name, _, _) in enumerate(frozen.TEXT_STRATA)}
    rank_c = {CRAWLS[0]: 0, CRAWLS[1]: 1}
    out = sparse.consolidate_aliases(items, rank_s, rank_c)
    assert len(out) == 1
    assert out[0]["representative"] == ["r", "v", "a.parquet", 9]
    assert len(out[0]["aliases"]) == 2


# --------------------------------------------------------------------------
# Blinding.
# --------------------------------------------------------------------------


def test_secret_and_commitment() -> None:
    secret = blinding.generate_secret()
    assert len(secret) == 32
    assert blinding.key_commitment(secret) == blinding.key_commitment(bytes(secret))
    with pytest.raises(blinding.BlindingError):
        blinding.key_commitment(b"short")


def test_review_ids_deterministic_opaque() -> None:
    secret = b"k" * 32
    loc = [frozen.REPOSITORY, frozen.REVISION, "f.parquet", 3]
    first = blinding.review_id(secret, loc)
    assert first == blinding.review_id(secret, loc)
    assert first.startswith("ew2-") and len(first) == 4 + 64
    assert first != blinding.review_id(b"j" * 32, loc)
    assert first != blinding.review_id(secret, [frozen.REPOSITORY, frozen.REVISION, "f.parquet", 4])


def test_reviewer_orders_differ_deterministically() -> None:
    secret = b"k" * 32
    ids = [
        blinding.review_id(secret, [frozen.REPOSITORY, frozen.REVISION, "f.parquet", i])
        for i in range(9)
    ]
    order1 = blinding.order_ids(ids, "reviewer-1")
    order2 = blinding.order_ids(ids, "reviewer-2")
    assert sorted(order1) == sorted(order2) == sorted(ids)
    assert order1 == blinding.order_ids(ids, "reviewer-1")
    assert order1 != order2


def test_package_has_no_source_leaks() -> None:
    secret = b"k" * 32
    items = [
        {
            "locator": [frozen.REPOSITORY, frozen.REVISION, "f.parquet", i],
            "stratum": "B_science_census",
            "rank": "0" * 64,
            "text": f"document {i} text",
        }
        for i in range(3)
    ]
    package = blinding.build_package(items, secret, "reviewer-1", rubric.RUBRIC_VERSION)
    assert set(package["order"]) == {f["review_id"] for f in package["forms"]}
    for form in package["forms"]:
        assert set(form) == {"review_id", "text", "rubric_version", "reviewer", "labels"}
        assert not (set(form) & frozen.FORBIDDEN_PACKAGE_FIELDS)
    public, sealed = blinding.split_sealed(package)
    assert "sealed_entries" not in public
    assert len(sealed) == 3
    assert sealed[0]["locator"][2] == "f.parquet"


def test_package_collision_stops() -> None:
    secret = b"k" * 32
    items = [
        {
            "locator": [frozen.REPOSITORY, frozen.REVISION, "f.parquet", 1],
            "stratum": "s",
            "rank": "r",
            "text": "t",
        },
        {
            "locator": [frozen.REPOSITORY, frozen.REVISION, "f.parquet", 1],
            "stratum": "s",
            "rank": "r",
            "text": "t",
        },
    ]
    with pytest.raises(blinding.BlindingError, match="collision"):
        blinding.build_package(items, secret, "reviewer-1", rubric.RUBRIC_VERSION)


# --------------------------------------------------------------------------
# Rubric.
# --------------------------------------------------------------------------


def test_rubric_dimensions_exact() -> None:
    assert len(rubric.DIMENSIONS) == 18
    assert [d["n"] for d in rubric.DIMENSIONS] == list(range(1, 19))
    assert rubric.DIM_BY_NAME["overall_disposition"]["levels"] == (
        "acceptable_as_is",
        "requires_separately_specified_repair",
        "reject",
    )
    assert "english_fluency" in rubric.NOT_APPLICABLE_DIMS
    assert "english_predominance" not in rubric.NOT_APPLICABLE_DIMS
    assert len(rubric.FRAGMENTATION_DEFECT_TYPES) == 7


def _valid_form() -> dict[str, Any]:
    dims = {d["name"]: d["levels"][0] for d in rubric.DIMENSIONS}
    return {
        "review_id": "ew2-" + "0" * 64,
        "reviewer": "reviewer-1",
        "rubric_version": rubric.RUBRIC_VERSION,
        "submitted_utc": "2026-09-27T00:00:00Z",
        "expertise": "general",
        "confidence": "sufficient",
        "confidence_reason": "",
        "dimensions": dims,
        "dimension_notes": {},
        "disposition_rationale": "serves its purpose",
        "known_essential_gaps": False,
        "known_material_error": False,
    }


def test_rubric_form_validation() -> None:
    assert rubric.validate_form(_valid_form()) == []
    bad = _valid_form()
    bad["dimensions"] = dict(bad["dimensions"], english_fluency="uncertain")
    assert any("uncertain requires" in e for e in rubric.validate_form(bad))
    bad2 = _valid_form()
    bad2["dimensions"] = dict(bad2["dimensions"], english_predominance="not_applicable")
    assert any("not authorized" in e for e in rubric.validate_form(bad2))
    ok_na = _valid_form()
    ok_na["dimensions"] = dict(ok_na["dimensions"], english_fluency="not_applicable")
    assert rubric.validate_form(ok_na) == []
    partial = _valid_form()
    partial["dimensions"] = dict(partial["dimensions"], english_fluency="not_reviewed")
    assert any("all 18" in e for e in rubric.validate_form(partial))
    blank = rubric.blank_dimensions(False)
    assert set(blank) == set(rubric.DIM_BY_NAME)
    assert all(v == "not_reviewed" for v in blank.values())
    gap = _valid_form()
    gap["known_material_error"] = True
    assert any("acceptable_as_is" in e for e in rubric.validate_form(gap))
    missing = _valid_form()
    missing["disposition_rationale"] = ""
    assert any("rationale" in e for e in rubric.validate_form(missing))
    extra = _valid_form()
    extra["policy"] = "B"
    assert any("unknown form fields" in e for e in rubric.validate_form(extra))
    assert set(rubric.disagreement_schema()) == {
        "review_id",
        "dimension",
        "label_a",
        "label_b",
        "uncertain_either",
    }
    assert set(rubric.adjudication_schema()) == {
        "review_id",
        "dimension",
        "blind_label",
        "rationales_seen",
        "final_label",
        "final_rationale",
        "resolved",
    }


# --------------------------------------------------------------------------
# Receipts.
# --------------------------------------------------------------------------


def test_provenance_binds_frozen_identities() -> None:
    code = [ROOT / "src/xlm/data/evidence_v2/canonical.py"]
    prov = receipts.provenance(
        root=ROOT,
        code_paths=code,
        command="select-text",
        exit_status=0,
        caps={"requests": 800},
        parents={"freeze": frozen.FREEZE_DIGEST},
        stage="selection",
    )
    assert prov["freeze_digest"] == frozen.FREEZE_DIGEST
    assert prov["policy_digest"] == frozen.POLICY_DIGEST
    assert prov["environment"]["python_version_file"] == "3.12.13"
    assert len(prov["environment"]["uv_lock_sha256"]) == 64
    sealed = receipts.seal({"a": 1, **{"provenance": prov}})
    assert sealed["digest"] == canonical.self_digest(
        {k: v for k, v in sealed.items() if k != "digest"}
    )


def test_publish_refuses_overwrite_and_aggregate_needs_all(tmp_path: Path) -> None:
    target = tmp_path / "manifest.json"
    receipts.publish_manifest(target, receipts.seal({"a": 1}))
    with pytest.raises(receipts.ReceiptError, match="refusing to overwrite"):
        receipts.publish_manifest(target, receipts.seal({"a": 1}))
    receipts.check_aggregate({"a": {"bytes": 1, "sha256": "x"}}, ["a"], what="t")
    with pytest.raises(receipts.ReceiptError, match="missing artifacts"):
        receipts.check_aggregate({}, ["a"], what="t")


# --------------------------------------------------------------------------
# Offline CLI surface.
# --------------------------------------------------------------------------


def test_cli_verify_freeze_and_inventory(capsys: pytest.CaptureFixture[str]) -> None:
    cli = _cli()
    assert cli.main(["verify-freeze"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["freeze_digest"] == frozen.FREEZE_DIGEST
    assert out["eligible_total"] == 23200
    assert cli.main(["expand-inventory"]) == 0
    winners = json.loads(capsys.readouterr().out)
    assert winners[0].endswith("train-01860-of-02772.parquet")


def test_cli_dry_m_plan(tmp_path: Path) -> None:
    cli = _cli()
    target = tmp_path / "dry-m.json"
    assert cli.main(["dry-m-plan", "--out", str(target)]) == 0
    body = json.loads(target.read_bytes())
    assert body["plan"]["executable"] is False
    assert len(body["plan"]["plans"]) == 8
    assert body["provenance"]["freeze_digest"] == frozen.FREEZE_DIGEST
    assert body["digest"] == canonical.self_digest({k: v for k, v in body.items() if k != "digest"})


def test_cli_dry_t_plan(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cli = _cli()
    selection = text_select.select_text(_science_world(), CRAWLS)
    manifest = text_select.seal(
        text_select.build_manifest(selection, evaluator_hash="ab" * 32, command="t", exit_status=0)
    )
    path = tmp_path / "selection.json"
    receipts.publish_manifest(path, manifest)
    assert cli.main(["dry-t-plan", "--selection-manifest", str(path)]) == 0
    body = json.loads(capsys.readouterr().out)
    assert body["executable"] is False
    assert body["selection_digest"] == manifest["digest"]
    assert body["total_selected"] == 54


def test_cli_live_planning_refuses_without_authorization(
    capsys: pytest.CaptureFixture[str],
) -> None:
    cli = _cli()
    assert cli.main(["plan-footers", "--no-dry-run"]) == 2
    assert "STOP" in capsys.readouterr().err
    assert cli.main(["plan-text-costs"]) == 2
    assert "STOP" in capsys.readouterr().err
    assert cli.main(["plan-footers"]) == 0
