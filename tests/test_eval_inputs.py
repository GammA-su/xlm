"""Evaluation-input manifests: schema, verification and the split firewall (D04).

Every input here is authored. No official benchmark record, no network, no
provider cache. The negative cases each introduce exactly one deviation from a
known-good manifest so the assertion names the reason it fails.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

import pytest
import yaml

from xlm.evaluation.inputs import (
    EVAL_INPUT_MANIFEST_VERSION,
    EvaluationInputManifest,
    InputVerificationError,
    ManifestSchemaError,
    MembershipError,
    TierViolationError,
    build_evaluation_inputs,
    load_evaluation_inputs,
    policy_splits_for_tier,
    save_evaluation_inputs,
    verify_evaluation_inputs,
)
from xlm.evaluation.suites import (
    FinalAuthorization,
    FinalAuthorizationRequiredError,
    SuiteTier,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_INPUTS = REPO_ROOT / "fixtures" / "eval" / "inputs"
COMPLETE_MANIFEST = FIXTURE_INPUTS / "dev_fixture_v1" / "manifest.yaml"
SUBSET_MANIFEST = FIXTURE_INPUTS / "dev_fixture_subset" / "manifest.yaml"


@pytest.fixture
def scratch_manifest(tmp_path: Path) -> Path:
    """A private, writable copy of the complete authored fixture scope."""
    target = tmp_path / "scope"
    shutil.copytree(COMPLETE_MANIFEST.parent, target)
    return target / "manifest.yaml"


def _write_json(path: Path, records: Any) -> None:
    """Write an artifact as LF bytes, matching how the fixtures were authored.

    A manifest records byte digests, so a test that let the OS choose the
    newline would change the size and digest of every file it touched and fail
    for the wrong reason.
    """
    path.write_bytes((json.dumps(records, indent=2) + "\n").encode("utf-8"))


def _edit(manifest_path: Path, mutate: Any) -> Path:
    payload = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    mutate(payload)
    manifest_path.write_bytes(yaml.safe_dump(payload, sort_keys=False).encode("utf-8"))
    return manifest_path


def _selection(payload: dict[str, Any], namespace: str) -> dict[str, Any]:
    for entry in payload["selections"]:
        if entry.get("namespace") == namespace or entry["task"] == namespace:
            return entry
    raise AssertionError(f"no selection {namespace!r}")


# ------------------------------------------------------------------ policy splits


def test_policy_splits_come_from_the_frozen_suite_definition() -> None:
    """The firewall reads the suite policy rather than restating it."""
    search = policy_splits_for_tier(SuiteTier.SEARCH)
    confirmation = policy_splits_for_tier(SuiteTier.CONFIRMATION)
    final = policy_splits_for_tier(SuiteTier.FINAL)

    assert search == {
        "arc_easy": "train",
        "hellaswag": "train",
        "piqa": "train",
        "blimp": "train",
    }
    # Confirmation moves ARC to official validation; the grouped tasks stay on
    # a disjoint subdivision of train.
    assert confirmation["arc_easy"] == "validation"
    assert confirmation["hellaswag"] == "train"
    assert confirmation["piqa"] == "train"
    # The final splits are the ones a developer tier must never touch.
    assert final == {
        "arc_easy": "test",
        "hellaswag": "validation",
        "piqa": "validation",
        "blimp": "train",
    }


# ------------------------------------------------------------------ happy path


def test_authored_fixture_manifest_verifies_and_reports_its_scope() -> None:
    manifest = load_evaluation_inputs(COMPLETE_MANIFEST)
    assert manifest.manifest_version == EVAL_INPUT_MANIFEST_VERSION
    assert manifest.scope_kind == "authored_fixture"
    assert manifest.exposure_class == "authored_fixture"
    assert manifest.declared_tasks == ("arc_easy", "blimp", "hellaswag", "piqa")
    assert manifest.required_blimp_subdatasets == (
        "blimp_adjunct_island",
        "blimp_anaphor_gender_agreement",
    )

    verified = verify_evaluation_inputs(manifest, tier=SuiteTier.SEARCH)
    assert len(verified.selections) == 5
    # Expected ids are declared, namespaced, and independent of any run.
    expected = manifest.expected_ids()
    assert expected["arc_easy"] == tuple(f"arc_easy#syn_arc_{i:04d}" for i in range(4))
    assert all(ns.startswith("blimp/") for ns in expected if ns.startswith("blimp"))
    # Bound task names cannot be mistaken for an official benchmark run.
    assert {v.selection.bound_task_name for v in verified.selections} == {
        "xlmdev_arc_easy",
        "xlmdev_hellaswag",
        "xlmdev_piqa",
        "xlmdev_blimp_adjunct_island",
        "xlmdev_blimp_anaphor_gender_agreement",
    }


def test_manifest_id_is_content_identity_not_file_location(tmp_path: Path) -> None:
    manifest = load_evaluation_inputs(COMPLETE_MANIFEST)
    elsewhere = tmp_path / "copied.yaml"
    save_evaluation_inputs(manifest, elsewhere)
    assert load_evaluation_inputs(elsewhere).manifest_id() == manifest.manifest_id()


def test_manifest_id_changes_with_membership(scratch_manifest: Path) -> None:
    before = load_evaluation_inputs(scratch_manifest).manifest_id()

    def drop_one(payload: dict[str, Any]) -> None:
        _selection(payload, "arc_easy")["item_ids"].pop()

    _edit(scratch_manifest, drop_one)
    assert load_evaluation_inputs(scratch_manifest).manifest_id() != before


def test_prompt_and_label_digests_are_separate(scratch_manifest: Path) -> None:
    """A relabelling must not look like the model seeing different text."""
    verified = verify_evaluation_inputs(
        load_evaluation_inputs(scratch_manifest), tier=SuiteTier.SEARCH
    )
    arc = next(v for v in verified.selections if v.selection.task == "arc_easy")

    data_path = scratch_manifest.parent / "arc_easy.json"
    records = json.loads(data_path.read_text(encoding="utf-8"))
    records[0]["answerKey"] = "B" if records[0]["answerKey"] == "A" else "A"
    _write_json(data_path, records)

    def refresh(payload: dict[str, Any]) -> None:
        entry = _selection(payload, "arc_easy")
        entry["content_sha256"] = hashlib.sha256(data_path.read_bytes()).hexdigest()
        entry["content_bytes"] = data_path.stat().st_size

    _edit(scratch_manifest, refresh)
    relabelled = verify_evaluation_inputs(
        load_evaluation_inputs(scratch_manifest), tier=SuiteTier.SEARCH
    )
    arc_after = next(v for v in relabelled.selections if v.selection.task == "arc_easy")

    assert arc_after.prompt_digest == arc.prompt_digest
    assert arc_after.label_digest != arc.label_digest


def test_digests_are_stable_under_record_reordering(scratch_manifest: Path) -> None:
    verified = verify_evaluation_inputs(
        load_evaluation_inputs(scratch_manifest), tier=SuiteTier.SEARCH
    )
    arc = next(v for v in verified.selections if v.selection.task == "arc_easy")

    data_path = scratch_manifest.parent / "arc_easy.json"
    records = json.loads(data_path.read_text(encoding="utf-8"))
    records.reverse()
    _write_json(data_path, records)

    def refresh(payload: dict[str, Any]) -> None:
        entry = _selection(payload, "arc_easy")
        entry["content_sha256"] = hashlib.sha256(data_path.read_bytes()).hexdigest()
        entry["content_bytes"] = data_path.stat().st_size
        entry["item_ids"] = list(reversed(entry["item_ids"]))

    _edit(scratch_manifest, refresh)
    reordered = verify_evaluation_inputs(
        load_evaluation_inputs(scratch_manifest), tier=SuiteTier.SEARCH
    )
    arc_after = next(v for v in reordered.selections if v.selection.task == "arc_easy")
    assert arc_after.prompt_digest == arc.prompt_digest
    assert arc_after.label_digest == arc.label_digest


# ------------------------------------------------------------------ refusals


def test_wrong_split_for_the_tier_is_refused(scratch_manifest: Path) -> None:
    """A self-declared label does not establish approved split membership."""

    def wrong_split(payload: dict[str, Any]) -> None:
        _selection(payload, "arc_easy")["source_split"] = "test"

    _edit(scratch_manifest, wrong_split)
    with pytest.raises(TierViolationError, match="evaluation policy assigns 'train'"):
        verify_evaluation_inputs(load_evaluation_inputs(scratch_manifest), tier=SuiteTier.SEARCH)


def test_hellaswag_final_split_is_refused_at_search(scratch_manifest: Path) -> None:
    """HellaSwag's final split is official *validation*, not test."""

    def wrong_split(payload: dict[str, Any]) -> None:
        _selection(payload, "hellaswag")["source_split"] = "validation"

    _edit(scratch_manifest, wrong_split)
    with pytest.raises(TierViolationError):
        verify_evaluation_inputs(load_evaluation_inputs(scratch_manifest), tier=SuiteTier.SEARCH)


def test_final_tier_requires_operator_authorization(scratch_manifest: Path) -> None:
    def make_final(payload: dict[str, Any]) -> None:
        payload["tier"] = "final"
        for entry in payload["selections"]:
            entry["source_split"] = {
                "arc_easy": "test",
                "hellaswag": "validation",
                "piqa": "validation",
                "blimp": "train",
            }[entry["task"]]

    _edit(scratch_manifest, make_final)
    manifest = load_evaluation_inputs(scratch_manifest)
    with pytest.raises(FinalAuthorizationRequiredError):
        verify_evaluation_inputs(manifest, tier=SuiteTier.FINAL)
    # An unauthorized object is not authorization either.
    with pytest.raises(FinalAuthorizationRequiredError):
        verify_evaluation_inputs(
            manifest,
            tier=SuiteTier.FINAL,
            final_authorization=FinalAuthorization(False, "nope"),
        )


def test_requested_tier_must_match_the_manifest(scratch_manifest: Path) -> None:
    with pytest.raises(TierViolationError, match="declares tier 'search'"):
        verify_evaluation_inputs(
            load_evaluation_inputs(scratch_manifest), tier=SuiteTier.CONFIRMATION
        )


def test_isolated_final_exposure_is_refused_for_a_developer_run(
    scratch_manifest: Path,
) -> None:
    def claim_isolation(payload: dict[str, Any]) -> None:
        # scope_kind moves too: an authored fixture is separately pinned to the
        # authored_fixture exposure class, which is checked first.
        payload["scope_kind"] = "frozen_development_subset"
        payload["exposure_class"] = "isolated_final"

    _edit(scratch_manifest, claim_isolation)
    with pytest.raises(TierViolationError, match="isolated_final"):
        verify_evaluation_inputs(load_evaluation_inputs(scratch_manifest), tier=SuiteTier.SEARCH)


def test_same_length_corruption_is_rejected(scratch_manifest: Path) -> None:
    """Byte-for-byte length equality is not integrity."""
    data_path = scratch_manifest.parent / "piqa.json"
    original = data_path.read_bytes().decode("utf-8")
    corrupted = original.replace("use a dry cloth", "use a dry cloXh", 1)
    assert len(corrupted) == len(original)
    data_path.write_bytes(corrupted.encode("utf-8"))

    with pytest.raises(InputVerificationError, match="does not match the declared"):
        verify_evaluation_inputs(load_evaluation_inputs(scratch_manifest), tier=SuiteTier.SEARCH)


def test_size_mismatch_is_rejected(scratch_manifest: Path) -> None:
    def wrong_size(payload: dict[str, Any]) -> None:
        _selection(payload, "piqa")["content_bytes"] += 1

    _edit(scratch_manifest, wrong_size)
    with pytest.raises(InputVerificationError, match="bytes"):
        verify_evaluation_inputs(load_evaluation_inputs(scratch_manifest), tier=SuiteTier.SEARCH)


def test_an_unselected_larger_source_is_not_the_selected_artifact(
    scratch_manifest: Path,
) -> None:
    """Extra rows in the file are unexpected members, not a bigger selection."""
    data_path = scratch_manifest.parent / "arc_easy.json"
    records = json.loads(data_path.read_text(encoding="utf-8"))
    records.append(
        {
            "id": "syn_arc_9999",
            "question": "An extra fixture row nobody selected?",
            "choices": {"text": ["yes", "no"], "label": ["A", "B"]},
            "answerKey": "A",
        }
    )
    _write_json(data_path, records)

    def refresh(payload: dict[str, Any]) -> None:
        entry = _selection(payload, "arc_easy")
        entry["content_sha256"] = hashlib.sha256(data_path.read_bytes()).hexdigest()
        entry["content_bytes"] = data_path.stat().st_size

    _edit(scratch_manifest, refresh)
    with pytest.raises(MembershipError, match="unexpected \\['syn_arc_9999'\\]"):
        verify_evaluation_inputs(load_evaluation_inputs(scratch_manifest), tier=SuiteTier.SEARCH)


def test_missing_declared_item_is_rejected(scratch_manifest: Path) -> None:
    data_path = scratch_manifest.parent / "arc_easy.json"
    records = json.loads(data_path.read_text(encoding="utf-8"))
    removed = records.pop()["id"]
    _write_json(data_path, records)

    def refresh(payload: dict[str, Any]) -> None:
        entry = _selection(payload, "arc_easy")
        entry["content_sha256"] = hashlib.sha256(data_path.read_bytes()).hexdigest()
        entry["content_bytes"] = data_path.stat().st_size

    _edit(scratch_manifest, refresh)
    with pytest.raises(MembershipError, match=f"Missing \\['{removed}'\\]"):
        verify_evaluation_inputs(load_evaluation_inputs(scratch_manifest), tier=SuiteTier.SEARCH)


def test_duplicate_records_in_the_artifact_are_rejected(scratch_manifest: Path) -> None:
    data_path = scratch_manifest.parent / "arc_easy.json"
    records = json.loads(data_path.read_text(encoding="utf-8"))
    records.append(dict(records[0]))
    _write_json(data_path, records)

    def refresh(payload: dict[str, Any]) -> None:
        entry = _selection(payload, "arc_easy")
        entry["content_sha256"] = hashlib.sha256(data_path.read_bytes()).hexdigest()
        entry["content_bytes"] = data_path.stat().st_size

    _edit(scratch_manifest, refresh)
    with pytest.raises(MembershipError, match="duplicate item ids"):
        verify_evaluation_inputs(load_evaluation_inputs(scratch_manifest), tier=SuiteTier.SEARCH)


def test_duplicate_declared_ids_are_rejected_by_the_schema(scratch_manifest: Path) -> None:
    def duplicate(payload: dict[str, Any]) -> None:
        entry = _selection(payload, "arc_easy")
        entry["item_ids"].append(entry["item_ids"][0])

    _edit(scratch_manifest, duplicate)
    with pytest.raises(ManifestSchemaError, match="duplicate item ids"):
        load_evaluation_inputs(scratch_manifest)


def test_missing_declared_id_field_is_rejected(scratch_manifest: Path) -> None:
    def wrong_field(payload: dict[str, Any]) -> None:
        _selection(payload, "arc_easy")["item_id_field"] = "not_a_field"

    _edit(scratch_manifest, wrong_field)
    with pytest.raises(MembershipError, match="no declared id field"):
        verify_evaluation_inputs(load_evaluation_inputs(scratch_manifest), tier=SuiteTier.SEARCH)


def test_missing_declared_label_field_is_rejected(scratch_manifest: Path) -> None:
    def wrong_label(payload: dict[str, Any]) -> None:
        _selection(payload, "arc_easy")["label_field"] = "not_a_label"

    _edit(scratch_manifest, wrong_label)
    with pytest.raises(MembershipError, match="declared label field"):
        verify_evaluation_inputs(load_evaluation_inputs(scratch_manifest), tier=SuiteTier.SEARCH)


def test_absent_artifact_is_rejected(scratch_manifest: Path) -> None:
    (scratch_manifest.parent / "piqa.json").unlink()
    with pytest.raises(InputVerificationError, match="not a regular file"):
        verify_evaluation_inputs(load_evaluation_inputs(scratch_manifest), tier=SuiteTier.SEARCH)


def test_harness_version_drift_is_rejected(scratch_manifest: Path) -> None:
    with pytest.raises(InputVerificationError, match="rebuild the manifest"):
        verify_evaluation_inputs(
            load_evaluation_inputs(scratch_manifest),
            tier=SuiteTier.SEARCH,
            harness_version="0.0.1-not-installed",
        )


def test_required_subdataset_must_be_selected(scratch_manifest: Path) -> None:
    def require_absent(payload: dict[str, Any]) -> None:
        payload["required_blimp_subdatasets"].append("blimp_never_selected")

    _edit(scratch_manifest, require_absent)
    with pytest.raises(ManifestSchemaError, match="blimp_never_selected"):
        load_evaluation_inputs(scratch_manifest)


def test_manifest_cannot_supply_its_own_task_definitions(scratch_manifest: Path) -> None:
    def hijack(payload: dict[str, Any]) -> None:
        payload["task_definition_source"] = "manifest_supplied"

    _edit(scratch_manifest, hijack)
    with pytest.raises(ManifestSchemaError, match="pinned_installed_harness"):
        load_evaluation_inputs(scratch_manifest)


def test_unsupported_manifest_version_is_refused(scratch_manifest: Path) -> None:
    def bump(payload: dict[str, Any]) -> None:
        payload["manifest_version"] = "99"

    _edit(scratch_manifest, bump)
    with pytest.raises(ManifestSchemaError, match="unsupported manifest_version"):
        load_evaluation_inputs(scratch_manifest)


def test_selection_cannot_declare_an_empty_population() -> None:
    with pytest.raises(ManifestSchemaError, match="declares no item ids"):
        EvaluationInputManifest.from_dict(
            {
                "manifest_version": EVAL_INPUT_MANIFEST_VERSION,
                "scope_label": "empty",
                "scope_kind": "authored_fixture",
                "exposure_class": "authored_fixture",
                "tier": "search",
                "harness_version": "0.4.13",
                "task_definition_source": "pinned_installed_harness",
                "selections": [
                    {
                        "task": "piqa",
                        "leaf_task": "piqa",
                        "source_repository": "baber/piqa",
                        "source_revision": "abc",
                        "source_split": "train",
                        "record_schema_version": "v1",
                        "adapter_version": "v1",
                        "item_id_field": "id",
                        "item_ids": [],
                        "data_file": "x.json",
                        "content_sha256": "0" * 64,
                        "content_bytes": 0,
                    }
                ],
            }
        )


def test_selection_cannot_select_more_than_its_declared_source(
    scratch_manifest: Path,
) -> None:
    def shrink_source(payload: dict[str, Any]) -> None:
        _selection(payload, "arc_easy")["source_population_size"] = 1

    _edit(scratch_manifest, shrink_source)
    with pytest.raises(ManifestSchemaError, match="declared source population"):
        load_evaluation_inputs(scratch_manifest)


# ------------------------------------------------------------------ construction


def test_build_reads_membership_from_the_artifact_not_the_operator(tmp_path: Path) -> None:
    """Ids and digests are derived, so they cannot be mistyped or widened."""
    data = tmp_path / "piqa.json"
    data.write_text(
        json.dumps(
            [
                {"xlm_item_locator": "syn/a", "goal": "g1", "sol1": "s1", "sol2": "s2", "label": 0},
                {"xlm_item_locator": "syn/b", "goal": "g2", "sol1": "s1", "sol2": "s2", "label": 1},
            ]
        ),
        encoding="utf-8",
    )
    manifest = build_evaluation_inputs(
        scope_label="built in a test",
        scope_kind="authored_fixture",
        exposure_class="authored_fixture",
        tier=SuiteTier.SEARCH,
        harness_version="0.4.13",
        base_dir=tmp_path,
        entries=[
            {
                "task": "piqa",
                "leaf_task": "piqa",
                "source_repository": "baber/piqa",
                "source_revision": "rev",
                "source_split": "train",
                "record_schema_version": "piqa.official_shape.v1",
                "adapter_version": "xlm_eval_json.v1",
                "item_id_field": "xlm_item_locator",
                "label_field": "label",
                "data_file": "piqa.json",
            }
        ],
    )
    selection = manifest.selections[0]
    assert selection.item_ids == ("syn/a", "syn/b")
    assert selection.content_sha256 == hashlib.sha256(data.read_bytes()).hexdigest()
    assert selection.content_bytes == data.stat().st_size
    verify_evaluation_inputs(manifest, base_dir=tmp_path, tier=SuiteTier.SEARCH)


def test_subset_scope_is_a_valid_narrower_manifest() -> None:
    manifest = load_evaluation_inputs(SUBSET_MANIFEST)
    assert manifest.declared_tasks == ("arc_easy", "piqa")
    verified = verify_evaluation_inputs(manifest, tier=SuiteTier.SEARCH)
    assert len(verified.selections) == 2
