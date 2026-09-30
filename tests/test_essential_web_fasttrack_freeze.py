"""Essential-Web fast-track selector freeze (offline).

Synthetic parents are authored here to exercise the builder's checks; the
committed evidence files in Git (sealed M summary, Arm-T package manifest,
policy spec, the freeze itself) are read as repository artifacts. No test
opens Arm-T review material or the network (sockets are blocked).
"""

from __future__ import annotations

import copy
import importlib.util
import json
import socket
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

from xlm.data.evidence_v2 import canonical, m_analysis
from xlm.data.evidence_v2 import fasttrack_freeze as ff

REPO_ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = REPO_ROOT / "docs" / "implementation" / "evidence"
M_DIR = EVIDENCE / "ESSENTIAL-WEB-EVIDENCE-V4.1-M-ANALYSIS"
T_MANIFEST = EVIDENCE / "ESSENTIAL-WEB-EVIDENCE-V4.1-T-BLINDED-PACKAGE" / "package_manifest.json"
FREEZE_PATH = EVIDENCE / "ESSENTIAL-WEB-SELECTOR-FASTTRACK-FREEZE" / "freeze.json"
EVALUATOR_PATH = REPO_ROOT / "scripts" / "essential_web_selector_sweep.py"
POLICY_PATH = REPO_ROOT / "recipes" / "selectors" / "essential_web_selector_sweep_v1.yaml"

# Development B-normal and comparison counts, authored from the frozen report.
DEV = {
    "A-normal": (19, 108, 377, 40, 3552),
    "A-strict": (12, 62, 334, 28, 3660),
    "B-normal": (29, 108, 371, 36, 3552),
    "B-strict": (20, 62, 329, 25, 3660),
    "C-normal": (29, 41, 120, 354, 3552),
    "C-strict": (20, 22, 102, 292, 3660),
    "D-normal": (54, 279, 719, 110, 2934),
    "D-strict": (20, 62, 329, 25, 3660),
}
M = {
    "A-normal": (17, 117, 374, 50, 3538),
    "A-strict": (7, 68, 344, 38, 3639),
    "B-normal": (24, 117, 372, 45, 3538),
    "B-strict": (11, 68, 342, 36, 3639),
    "C-normal": (24, 46, 127, 361, 3538),
    "C-strict": (11, 30, 111, 305, 3639),
    "D-normal": (56, 257, 717, 118, 2948),
    "D-strict": (11, 68, 342, 36, 3639),
}
KEYS = ("science", "practical", "prose", "unassigned", "rejected")


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_: Any, **__: Any) -> None:
        raise AssertionError("network access attempted in an offline test")

    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)


def _summary(table: dict[str, tuple[int, ...]]) -> dict[str, Any]:
    finals = ("essential_science", "essential_practical", "essential_prose")
    return {
        "combos": {
            combo: {
                "conservation_ok": True,
                "final": dict(zip((*finals, "unassigned", "rejected"), row, strict=True)),
            }
            for combo, row in table.items()
        },
        "identity_invariants": {"b_strict_d_strict_agreement": 4096},
        "input_records": 4096,
        "multi_final_violations": 0,
    }


def _spec() -> dict[str, Any]:
    spec = yaml.safe_load(POLICY_PATH.read_text(encoding="utf-8"))
    assert isinstance(spec, dict)
    return spec


def _synthetic_parents(tmp_path: Path) -> dict[str, Any]:
    """Authored sealed-M directory, development directory and T manifest."""
    m_dir = tmp_path / "m"
    dev_dir = tmp_path / "dev"
    (m_dir / "m_sweep").mkdir(parents=True)
    dev_dir.mkdir()
    dev_summary = m_analysis.dumps(_summary(DEV))
    dev_manifest = m_analysis.dumps({"kind": "authored_development_manifest"})
    (dev_dir / "summary.json").write_bytes(dev_summary)
    (dev_dir / "sweep_manifest.json").write_bytes(dev_manifest)
    files = {"m_sweep/summary.json": m_analysis.dumps(_summary(M))}
    manifest = m_analysis.artifact_manifest(files)
    evaluator_raw = EVALUATOR_PATH.read_bytes()
    policy_raw = POLICY_PATH.read_bytes()
    seal: dict[str, Any] = {
        "kind": m_analysis.SEAL_KIND,
        "status": "SEALED",
        "arm": "M",
        "scientific_namespace": "authored-namespace",
        "source_revision": "ab" * 20,
        "source": {"repository": "authored/repo", "records": {"sha256": "11" * 32}},
        "adapted_input": {"records": 4096, "sha256": "22" * 32},
        "evaluator": {
            "path": "scripts/essential_web_selector_sweep.py",
            "bytes": len(evaluator_raw),
            "sha256": m_analysis.sha256_bytes(evaluator_raw),
            "tool_version": "2",
            "report_schema_version": 2,
            "policy": {
                "bytes": len(policy_raw),
                "sha256": m_analysis.sha256_bytes(policy_raw),
                "digest": ff.POLICY_DIGEST,
            },
        },
        "development": {
            "manifest": {
                "bytes": len(dev_manifest),
                "sha256": m_analysis.sha256_bytes(dev_manifest),
                "digest": "33" * 32,
            },
            "binding": {"records": 4096},
            "artifacts": {
                "summary.json": {
                    "bytes": len(dev_summary),
                    "sha256": m_analysis.sha256_bytes(dev_summary),
                }
            },
        },
        "results": dict(manifest["artifacts"]),
        "artifact_manifest_digest": manifest["digest"],
        "selector_decision": "NOT MADE",
        "policy_ranking": "NOT PERFORMED",
        "other_arm_material_bound": False,
        "post_seal_rule": "M analysis must not change in response to later semantic review",
    }
    seal["seal_digest"] = canonical.digest(seal)
    for name, raw in files.items():
        (m_dir / name).write_bytes(raw)
    (m_dir / m_analysis.ARTIFACT_MANIFEST_NAME).write_bytes(m_analysis.dumps(manifest))
    (m_dir / m_analysis.SEAL_NAME).write_bytes(m_analysis.dumps(seal))
    t_manifest = json.loads(T_MANIFEST.read_text(encoding="utf-8"))
    t_manifest["m_seal_parent"]["seal_digest"] = seal["seal_digest"]
    t_dir = tmp_path / "t-commitments"
    t_dir.mkdir()
    t_path = t_dir / ff.T_MANIFEST_NAME
    t_path.write_bytes(m_analysis.dumps(t_manifest))
    return {
        "m_dir": m_dir,
        "dev_dir": dev_dir,
        "t_path": t_path,
        "seal_digest": seal["seal_digest"],
    }


def _build(parents: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    digest = parents["seal_digest"]
    seal, m_summary, m_binding = ff.load_m_evidence(parents["m_dir"], expected_seal_digest=digest)
    development_summary, development_binding = ff.load_development(parents["dev_dir"], seal)
    spec, evaluator_binding = ff.bind_evaluator(EVALUATOR_PATH, POLICY_PATH, seal)
    arguments: dict[str, Any] = {
        "seal": seal,
        "m_binding": m_binding,
        "m_summary": m_summary,
        "development_summary": development_summary,
        "development_binding": development_binding,
        "spec": spec,
        "evaluator_binding": evaluator_binding,
        "t_arm": ff.t_arm_record(parents["t_path"], expected_seal_digest=digest),
        "mixture": {"changed_by_this_freeze": False},
        "code": {},
        "parent_commit": "cd" * 20,
        "decision_date": "2026-09-30",
        "commands": ["authored"],
        "environment": {"network": "none"},
    }
    arguments.update(overrides)
    return ff.build_freeze(**arguments)


# --------------------------------------------------------------------------
# Selector identity: exact frozen B-normal, extracted and never rewritten.
# --------------------------------------------------------------------------


def test_policy_digest_matches_the_frozen_evaluator() -> None:
    module_spec = importlib.util.spec_from_file_location("ff_test_sweep", EVALUATOR_PATH)
    assert module_spec is not None and module_spec.loader is not None
    evaluator = importlib.util.module_from_spec(module_spec)
    sys.modules["ff_test_sweep"] = evaluator
    module_spec.loader.exec_module(evaluator)
    spec, digest = evaluator.load_policy_spec(POLICY_PATH)
    assert digest == ff.POLICY_DIGEST == canonical.digest(spec)


def test_selector_semantics_is_the_frozen_b_normal_definition() -> None:
    spec = _spec()
    semantics = ff.selector_semantics(spec)
    assert semantics["policy"] == "B" and semantics["tier"] == "normal"
    assert semantics["gate_name"] == "GN"
    assert semantics["gate"] == {
        "english_min": 0.8,
        "artifacts": ["No Artifacts"],
        "missing": ["No missing content", "Missing Images or Figures"],
    }
    assert semantics["component_rules"] == {
        "science": {"any": ["S5", "S61"]},
        "practical": {"all": ["P"]},
        "prose": {"all": ["R"]},
    }
    assert semantics["precedence"] == ["science", "practical", "prose"]
    assert semantics["predicates"] == spec["predicates"]
    assert semantics["label_universes"]["doctype_union"] == spec["doctype_union"]
    assert semantics["validity"]["fdc_syntax"] == spec["fdc_syntax"]
    assert ff.selector_semantics(_spec()) == semantics


@pytest.mark.parametrize(
    "mutate",
    [
        lambda s: s["policies"]["B"].__setitem__("normal", "GD"),
        lambda s: s["policies"]["B"].__setitem__("science", {"any": ["S5"]}),
        lambda s: s["policies"]["B"].__setitem__("practical", {"all": ["F6", "P"]}),
        lambda s: s.__setitem__("precedence", ["practical", "science", "prose"]),
    ],
)
def test_selector_semantics_refuses_a_changed_policy(mutate: Any) -> None:
    spec = _spec()
    mutate(spec)
    with pytest.raises(ff.FreezeError):
        ff.selector_semantics(spec)


def test_semantics_digest_moves_with_any_threshold() -> None:
    spec = _spec()
    before = canonical.digest(ff.selector_semantics(spec))
    spec["gates"]["GN"]["english_min"] = 0.81
    assert canonical.digest(ff.selector_semantics(spec)) != before


# --------------------------------------------------------------------------
# Counts: recomputed from the summaries, conservation enforced.
# --------------------------------------------------------------------------


def test_condition_counts_conserve_and_refuse() -> None:
    counts = ff.condition_counts(_summary(M), what="M")
    assert counts["B-normal"] == dict(zip(KEYS, M["B-normal"], strict=True))
    assert all(sum(row.values()) == 4096 for row in counts.values())
    short = _summary(M)
    short["combos"]["B-normal"]["final"]["rejected"] -= 1
    with pytest.raises(ff.FreezeError, match="conserve"):
        ff.condition_counts(short, what="M")
    missing = _summary(M)
    del missing["combos"]["D-strict"]
    with pytest.raises(ff.FreezeError, match="eight frozen conditions"):
        ff.condition_counts(missing, what="M")
    overlap = _summary(M)
    overlap["multi_final_violations"] = 1
    with pytest.raises(ff.FreezeError, match="more than one final"):
        ff.condition_counts(overlap, what="M")
    flagged = _summary(M)
    flagged["combos"]["A-normal"]["conservation_ok"] = False
    with pytest.raises(ff.FreezeError, match="conserve"):
        ff.condition_counts(flagged, what="M")


def test_committed_sealed_m_summary_reproduces_b_normal() -> None:
    seal, summary, binding = ff.load_m_evidence(M_DIR)
    assert seal["seal_digest"] == ff.M_SEAL_DIGEST == binding["seal_digest"]
    counts = ff.condition_counts(summary, what="M")
    assert {combo: tuple(row[key] for key in KEYS) for combo, row in counts.items()} == M
    assert counts["B-normal"] == {
        "science": 24,
        "practical": 117,
        "prose": 372,
        "unassigned": 45,
        "rejected": 3538,
    }


# --------------------------------------------------------------------------
# Arm T: NOT RUN, from the committed manifest only.
# --------------------------------------------------------------------------


def test_t_arm_is_recorded_as_not_run_from_the_committed_manifest() -> None:
    record = ff.t_arm_record(T_MANIFEST)
    assert record["status"] == "NOT_RUN_NO_TWO_INDEPENDENT_HUMAN_REVIEWERS"
    assert record["semantic_review"] == "NOT RUN"
    assert (record["acquired_locators"], record["reviewable"]) == (118, 117)
    assert record["unreviewable_oversized"] == 1
    assert record["human_labels_collected"] == 0
    assert record["adjudication"] == record["unblinding"] == "NOT RUN"
    assert record["used_for_selector_decision"] is False
    assert record["text_inspected_for_this_freeze"] is False
    assert set(record["substitutes_used"].values()) == {False}
    package = record["blinded_package"]
    assert package["package_digest"] == ff.T_PACKAGE_DIGEST
    assert package["preserved_for_future_research"] is True
    text = json.dumps(record).lower()
    for word in ("passed", "failed", "negative"):
        assert word not in text


@pytest.mark.parametrize(
    ("mutate", "match"),
    [
        (lambda m: m.__setitem__("human_labels_assigned", 1), "labels or unblinding"),
        (lambda m: m.__setitem__("unblinded", True), "labels or unblinding"),
        (lambda m: m.__setitem__("package_digest", "00" * 32), "package digest"),
        (lambda m: m.__setitem__("status", "OPEN"), "SEALED"),
        (lambda m: m.__setitem__("selector_decision", "B-normal"), "selector decision"),
        (lambda m: m["counts"].__setitem__("reviewable", 118), "118 = 117"),
        (lambda m: m["m_seal_parent"].__setitem__("seal_digest", "00" * 32), "sealed M"),
    ],
)
def test_t_arm_record_refuses_a_changed_package(tmp_path: Path, mutate: Any, match: str) -> None:
    manifest = json.loads(T_MANIFEST.read_text(encoding="utf-8"))
    mutate(manifest)
    path = tmp_path / ff.T_MANIFEST_NAME
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ff.FreezeError, match=match):
        ff.t_arm_record(path)


@pytest.mark.parametrize(
    "relative",
    [
        "reviewer-1/package.json",
        "reviewer-1/package_manifest.json",
        "custodian/package_manifest.json",
        "custodian/master_ledger.json",
        "sealed/t_selected_documents.jsonl",
        "XLM-Review/essential-web-v4.1-t/package_manifest.json",
        "texts/0001.txt",
    ],
)
def test_review_material_paths_are_refused_before_any_read(relative: str) -> None:
    with pytest.raises(ff.FreezeError, match="refusing Arm-T review material"):
        ff.t_arm_record(Path("Z:/does-not-exist") / relative)


# --------------------------------------------------------------------------
# Freeze: deterministic, bound to every parent, tamper-evident.
# --------------------------------------------------------------------------


def test_freeze_build_is_deterministic_and_states_the_decision(tmp_path: Path) -> None:
    parents = _synthetic_parents(tmp_path)
    freeze = _build(parents)
    assert freeze == _build(parents)
    assert ff.freeze_digest_of(freeze) == freeze["freeze_digest"]
    selector = freeze["production_selector"]
    assert selector["condition"] == "B-normal" and selector["id"] == ff.SELECTOR_ID
    assert selector["policy_digest"] == ff.POLICY_DIGEST
    assert selector["tuning_after_m"] == "none"
    assert selector["admitted_components"] == [
        "essential_science",
        "essential_practical",
        "essential_prose",
    ]
    assert selector["non_admitted_finals"] == ["unassigned", "rejected"]
    decision = freeze["decision"]
    assert decision["selector"] == "B-normal"
    assert decision["t_validated"] is False
    assert decision["semantic_quality_claims"] == "none"
    assert decision["prior_state"]["m_seal_selector_decision"] == "NOT MADE"
    assert freeze["t_arm"]["status"] == ff.T_ARM_STATUS
    assert freeze["m_evidence"]["modified_by_this_freeze"] is False
    assert freeze["counts"]["development"]["B-normal"]["science"] == 29
    assert freeze["counts"]["m"]["B-normal"]["science"] == 24


def test_rationale_numbers_are_recomputed_from_the_counts(tmp_path: Path) -> None:
    rationale = {
        item["id"]: item["facts"] for item in _build(_synthetic_parents(tmp_path))["rationale"]
    }
    assert list(rationale) == [
        "replication",
        "coverage_versus_c",
        "science_extension_versus_a",
        "narrower_than_d",
        "strict_not_selected",
    ]
    replication = rationale["replication"]
    assert replication["practical"]["delta_rows"] == 9
    assert replication["rejected"]["delta_rows"] == -14
    assert replication["largest_absolute_share_move_percentage_points"] == 0.3418
    assert rationale["coverage_versus_c"]["m"] == {
        "b_practical": 117,
        "c_practical": 46,
        "b_prose": 372,
        "c_prose": 127,
        "b_science": 24,
        "c_science": 24,
    }
    assert rationale["science_extension_versus_a"]["m"]["added_science_rows"] == 7
    assert rationale["science_extension_versus_a"]["development"]["added_science_rows"] == 10
    narrower = rationale["narrower_than_d"]
    assert narrower["artifact_labels_admitted_only_by_d_normal_gate"] == ["Irrelevant Content"]
    assert narrower["m"] == {
        "b_selected_rows": 513,
        "d_selected_rows": 1030,
        "rows_b_rejects_that_d_passes": 590,
    }
    assert rationale["strict_not_selected"]["b_strict_science"]["m"] == 11
    assert rationale["strict_not_selected"]["b_strict_science"]["development"] == 20


def test_freeze_refuses_when_strict_tiers_differ(tmp_path: Path) -> None:
    parents = _synthetic_parents(tmp_path)
    table = dict(M)
    table["D-strict"] = (12, 68, 342, 36, 3638)
    with pytest.raises(ff.FreezeError, match="B-strict and D-strict"):
        _build(parents, m_summary=_summary(table))


def test_freeze_digest_moves_with_every_bound_parent(tmp_path: Path) -> None:
    parents = _synthetic_parents(tmp_path)
    base = _build(parents)
    t_arm = copy.deepcopy(base["t_arm"])
    t_arm["blinded_package"]["package_digest"] = "00" * 32
    spec = _spec()
    spec["gates"]["GN"]["english_min"] = 0.81
    table = dict(M)
    table["B-normal"] = (25, 116, 372, 45, 3538)
    variants = [
        _build(parents, t_arm=t_arm),
        _build(parents, spec=spec),
        _build(parents, m_summary=_summary(table)),
        _build(parents, parent_commit="ef" * 20),
        _build(parents, mixture={"changed_by_this_freeze": True}),
    ]
    digests = {base["freeze_digest"], *(variant["freeze_digest"] for variant in variants)}
    assert len(digests) == len(variants) + 1


def test_parent_tampering_is_refused(tmp_path: Path) -> None:
    parents = _synthetic_parents(tmp_path)
    digest = parents["seal_digest"]
    with pytest.raises(ff.FreezeError, match="expected sealed M result"):
        ff.load_m_evidence(parents["m_dir"])
    seal, _, _ = ff.load_m_evidence(parents["m_dir"], expected_seal_digest=digest)
    with pytest.raises(ff.FreezeError, match="policy spec digest"):
        ff.bind_evaluator(EVALUATOR_PATH, POLICY_PATH, seal, expected_policy_digest="00" * 32)
    changed = tmp_path / "changed_evaluator.py"
    changed.write_bytes(EVALUATOR_PATH.read_bytes() + b"\n# edited\n")
    with pytest.raises(ff.FreezeError, match="evaluator file differs"):
        ff.bind_evaluator(changed, POLICY_PATH, seal)
    summary_path = parents["dev_dir"] / "summary.json"
    summary_path.write_bytes(summary_path.read_bytes() + b" ")
    with pytest.raises(ff.FreezeError, match="development summary differs"):
        ff.load_development(parents["dev_dir"], seal)
    m_summary = parents["m_dir"] / "m_sweep" / "summary.json"
    m_summary.write_bytes(m_summary.read_bytes() + b" ")
    with pytest.raises(m_analysis.AnalysisError, match="changed after sealing"):
        ff.load_m_evidence(parents["m_dir"], expected_seal_digest=digest)


def test_load_freeze_detects_edits(tmp_path: Path) -> None:
    freeze = _build(_synthetic_parents(tmp_path))
    path = tmp_path / ff.FREEZE_NAME
    path.write_bytes(m_analysis.dumps(freeze))
    assert ff.load_freeze(path)["freeze_digest"] == freeze["freeze_digest"]
    freeze["decision"]["t_validated"] = True
    path.write_bytes(m_analysis.dumps(freeze))
    with pytest.raises(ff.FreezeError, match="freeze digest mismatch"):
        ff.load_freeze(path)


# --------------------------------------------------------------------------
# The committed freeze.
# --------------------------------------------------------------------------


def test_committed_freeze_binds_the_real_parents() -> None:
    freeze = ff.load_freeze(FREEZE_PATH)
    assert freeze["version"] == ff.FREEZE_VERSION and freeze["status"] == "FROZEN"
    assert freeze["m_evidence"]["seal_digest"] == ff.M_SEAL_DIGEST
    assert freeze["m_evidence"]["result_commit"] == ff.M_RESULT_COMMIT
    assert freeze["production_selector"]["policy_digest"] == ff.POLICY_DIGEST
    assert freeze["production_selector"]["semantics"] == ff.selector_semantics(_spec())
    assert freeze["t_arm"] == ff.t_arm_record(T_MANIFEST)
    assert freeze["parent_commit"] == ff.T_PACKAGE_COMMIT
    counts = freeze["counts"]
    assert {c: tuple(r[k] for k in KEYS) for c, r in counts["development"].items()} == DEV
    assert {c: tuple(r[k] for k in KEYS) for c, r in counts["m"].items()} == M
    seal, summary, binding = ff.load_m_evidence(M_DIR)
    assert freeze["m_evidence"]["summary"] == binding["summary"]
    assert counts["m"] == ff.condition_counts(summary, what="M")
    assert freeze["evaluator"]["sha256"] == seal["evaluator"]["sha256"]
    assert freeze["mixture"]["changed_by_this_freeze"] is False
    assert freeze["mixture"]["ultrax_ultrafineweb_weight"] == 0.2


def test_committed_freeze_holds_no_review_material() -> None:
    raw = FREEZE_PATH.read_text(encoding="utf-8")
    assert "ew2-" not in raw

    def keys(value: Any) -> set[str]:
        if isinstance(value, dict):
            return set(value) | {k for v in value.values() for k in keys(v)}
        if isinstance(value, list):
            return {k for v in value for k in keys(v)}
        return set()

    forbidden = {
        "text",
        "labels",
        "review_id",
        "review_ids",
        "locator",
        "mapping",
        "rationale_text",
    }
    assert not keys(json.loads(raw)) & forbidden
