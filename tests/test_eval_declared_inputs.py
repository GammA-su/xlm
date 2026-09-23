"""Declared evaluation inputs end to end (D04/D05).

These tests run the **real** pinned harness over **authored** records written in
the official benchmark record schemas, with a tiny authored CPU model. Nothing
is downloaded and no official benchmark content is used. The scorer, coverage
and index code under test is the production code: no part of the path is
replaced by a stub that returns the desired answer.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.optional_dependency
import yaml

pytest.importorskip("torch")
pytest.importorskip("lm_eval")
pytest.importorskip("datasets")

from xlm.evaluation.coverage import CoverageStatus  # noqa: E402
from xlm.evaluation.evidence import (  # noqa: E402
    EvaluationEvidence,
    items_from_harness_samples,
)
from xlm.evaluation.harness import (  # noqa: E402
    HarnessUnavailableError,
    bind_verified_inputs_to_tasks,
    build_task_manager,
    harness_version,
    resolve_official_task_config,
)
from xlm.evaluation.harness_runner import (  # noqa: E402
    MetricResolutionError,
    resolve_metric,
    run_declared_input_suite,
    run_harness_suite,
)
from xlm.evaluation.inputs import (  # noqa: E402
    load_evaluation_inputs,
    verify_evaluation_inputs,
)
from xlm.evaluation.suites import SuiteTier, TaskVariant, load_dataset_pins  # noqa: E402
from xlm.tokenizers.byte import ByteTokenizer  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_INPUTS = REPO_ROOT / "fixtures" / "eval" / "inputs"
COMPLETE_MANIFEST = FIXTURE_INPUTS / "dev_fixture_v1" / "manifest.yaml"
SUBSET_MANIFEST = FIXTURE_INPUTS / "dev_fixture_subset" / "manifest.yaml"
PINS = REPO_ROOT / "manifests" / "eval_dataset_pins.yaml"


@pytest.fixture(scope="module")
def tiny_checkpoint(tmp_path_factory: pytest.TempPathFactory) -> Path:
    from xlm.config.schemas import TransformerBaselineConfig
    from xlm.models.serialization import save_model_to_directory
    from xlm.models.transformer import TransformerBaseline

    config = TransformerBaselineConfig(
        architecture="transformer_baseline",
        vocab_size=260,
        num_layers=2,
        hidden_size=64,
        num_attention_heads=4,
        intermediate_size=128,
        context_length=128,
        attention_backend="eager",
    )
    path = tmp_path_factory.mktemp("declared_ckpt") / "model"
    save_model_to_directory(TransformerBaseline(config, seed=42), path)
    return path


@pytest.fixture(scope="module")
def tiny_model(tiny_checkpoint: Path) -> Any:
    from xlm.models.serialization import load_model_for_inference

    return load_model_for_inference(tiny_checkpoint, device="cpu")


def _verified(manifest_path: Path, base_dir: Path | None = None) -> Any:
    return verify_evaluation_inputs(
        load_evaluation_inputs(manifest_path),
        base_dir=base_dir,
        tier=SuiteTier.SEARCH,
        harness_version=harness_version(),
    )


def _run(model: Any, manifest_path: Path, out: Path, **kwargs: Any) -> EvaluationEvidence:
    return run_declared_input_suite(
        model=model,
        tokenizer=ByteTokenizer(),
        verified=_verified(manifest_path),
        checkpoint_hash="authored_fixture_ckpt",
        device="cpu",
        output_dir=out,
        use_evidence_cache=kwargs.pop("use_evidence_cache", False),
        **kwargs,
    )


def _write_json(path: Path, records: Any) -> None:
    """Write an artifact as LF bytes, matching how the fixtures were authored.

    A manifest records byte digests, so a test that let the OS choose the
    newline would change the size and digest of every file it touched and fail
    for the wrong reason.
    """
    path.write_bytes((json.dumps(records, indent=2) + "\n").encode("utf-8"))


def _copy_scope(tmp_path: Path, source: Path = COMPLETE_MANIFEST) -> Path:
    target = tmp_path / "scope"
    shutil.copytree(source.parent, target)
    return target / "manifest.yaml"


def _reseal(manifest_path: Path, data_path: Path, task: str) -> None:
    """Refresh the recorded digest/size after deliberately editing an artifact."""
    payload = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    for entry in payload["selections"]:
        if entry["task"] == task and entry["data_file"] == data_path.name:
            entry["content_sha256"] = hashlib.sha256(data_path.read_bytes()).hexdigest()
            entry["content_bytes"] = data_path.stat().st_size
    manifest_path.write_bytes(yaml.safe_dump(payload, sort_keys=False).encode("utf-8"))


# ---------------------------------------------------------------- task binding (D04)


def test_official_definitions_resolve_their_inherited_configuration() -> None:
    """include: and !function must survive resolution, or the task is not the task."""
    manager = build_task_manager(None)

    hellaswag, path = resolve_official_task_config("hellaswag", manager)
    assert path.name == "hellaswag.yaml"
    # The official preprocessing is a real callable, not a lost YAML tag.
    assert callable(hellaswag["process_docs"])
    assert hellaswag["doc_to_text"] == "{{query}}"

    blimp, _ = resolve_official_task_config("blimp_adjunct_island", manager)
    # Inherited from _template_yaml via `include:`.
    assert blimp["doc_to_choice"] == "{{[sentence_good, sentence_bad]}}"
    assert blimp["output_type"] == "multiple_choice"
    assert blimp["num_fewshot"] == 0
    # BLiMP officially scores acc only; it has no acc_norm.
    assert [m["metric"] for m in blimp["metric_list"]] == ["acc"]


def test_binding_rewrites_only_loader_keys() -> None:
    manager = build_task_manager(None)
    verified = _verified(COMPLETE_MANIFEST)
    specs, mapping, identities = bind_verified_inputs_to_tasks(verified, manager)

    by_name = {spec["task"]: spec for spec in specs}
    arc = by_name["xlmdev_arc_easy"]
    official, _ = resolve_official_task_config("arc_easy", manager)

    # Behaviour preserved exactly.
    for key in (
        "doc_to_text",
        "doc_to_choice",
        "doc_to_target",
        "output_type",
        "metric_list",
        "should_decontaminate",
    ):
        assert arc[key] == official[key], key
    # Loader keys rewritten, and every split pinned so no upstream default can
    # be selected by omission.
    assert arc["dataset_path"] == "json"
    assert arc["dataset_name"] is None
    assert arc["test_split"] == "train"
    assert arc["validation_split"] is None
    assert arc["training_split"] is None
    assert arc["fewshot_split"] is None
    assert Path(arc["dataset_kwargs"]["data_files"]["train"]).is_file()
    # No remote reference of any kind survives.
    assert "revision" not in arc["dataset_kwargs"]

    # Identity records where the definition came from and what it says.
    assert identities["arc_easy"]["definition_source"].endswith("arc_easy.yaml")
    assert len(identities["arc_easy"]["definition_sha256"]) == 64
    assert set(mapping) == {spec["task"] for spec in specs}


def test_binding_refuses_an_unsupported_task_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unsupported cases fail loudly instead of using a simplified copy."""
    import xlm.evaluation.harness as harness_module

    manager = build_task_manager(None)
    verified = _verified(COMPLETE_MANIFEST)
    real = harness_module.resolve_official_task_config

    def generative(leaf: str, source_manager: Any) -> tuple[dict[str, Any], Path]:
        config, path = real(leaf, source_manager)
        config["output_type"] = "generate_until"
        return config, path

    monkeypatch.setattr(harness_module, "resolve_official_task_config", generative)
    with pytest.raises(HarnessUnavailableError, match="unsupported, not approximated"):
        harness_module.bind_verified_inputs_to_tasks(verified, manager)


def test_binding_refuses_a_few_shot_task(monkeypatch: pytest.MonkeyPatch) -> None:
    import xlm.evaluation.harness as harness_module

    manager = build_task_manager(None)
    verified = _verified(COMPLETE_MANIFEST)
    real = harness_module.resolve_official_task_config

    def few_shot(leaf: str, source_manager: Any) -> tuple[dict[str, Any], Path]:
        config, path = real(leaf, source_manager)
        config["num_fewshot"] = 5
        return config, path

    monkeypatch.setattr(harness_module, "resolve_official_task_config", few_shot)
    with pytest.raises(HarnessUnavailableError, match="zero-shot"):
        harness_module.bind_verified_inputs_to_tasks(verified, manager)


def test_official_formatting_is_applied_to_authored_records(tiny_model: Any) -> None:
    """The real prompt templates and preprocessing must reach the model."""
    from lm_eval import simple_evaluate

    from xlm.evaluation.harness import create_harness_model

    manager = build_task_manager(None)
    specs, _, _ = bind_verified_inputs_to_tasks(_verified(COMPLETE_MANIFEST), manager)
    adapter = create_harness_model(model=tiny_model, tokenizer=ByteTokenizer(), device="cpu")
    results = simple_evaluate(
        model=adapter,
        tasks=specs,
        task_manager=manager,
        log_samples=True,
        use_cache=None,
        bootstrap_iters=0,
    )
    samples = results["samples"]

    arc = samples["xlmdev_arc_easy"][0]
    assert arc["arguments"][0][0].startswith("Question: ")
    assert arc["arguments"][0][0].endswith("\nAnswer:")

    # HellaSwag's official process_docs built `query`, `choices` and `gold`,
    # capitalising ctx_b and prefixing the activity label.
    hs = samples["xlmdev_hellaswag"][0]
    assert {"query", "choices", "gold"} <= set(hs["doc"])
    assert hs["arguments"][0][0].startswith("Fixture ")
    assert ": " in hs["arguments"][0][0]

    # BLiMP renders an empty context and scores the two sentences directly.
    blimp = samples["xlmdev_blimp_adjunct_island"][0]
    assert blimp["arguments"][0][0] == ""
    assert blimp["doc"]["sentence_good"] != blimp["doc"]["sentence_bad"]

    # PIQA uses its official goal/sol1/sol2 expression.
    piqa = samples["xlmdev_piqa"][0]
    assert piqa["arguments"][0][0].startswith("Question: ")
    assert len(piqa["arguments"]) == 2


# ---------------------------------------------------------------- metric resolution


def test_resolve_metric_handles_the_installed_key_shape() -> None:
    """The reproduced D05-0 defect: 'acc' is stored as 'acc,none'."""
    assert resolve_metric({"acc,none": 0.25}, "acc") == 0.25
    # An exact key wins over a suffixed one.
    assert resolve_metric({"acc": 0.5, "acc,none": 0.25}, "acc") == 0.5
    # A valid zero is a score, not a missing metric.
    assert resolve_metric({"acc,none": 0.0}, "acc") == 0.0
    assert resolve_metric({"acc": 0.0}, "acc") == 0.0
    # acc_norm must not satisfy a request for acc.
    with pytest.raises(MetricResolutionError, match="not present"):
        resolve_metric({"acc_norm,none": 0.9}, "acc")
    # Several filters are ambiguous rather than arbitrarily resolved.
    with pytest.raises(MetricResolutionError, match="ambiguous"):
        resolve_metric({"acc,strict": 0.1, "acc,flexible": 0.2}, "acc")


# ---------------------------------------------------------------- positive control


@pytest.fixture(scope="module")
def complete_run(tiny_model: Any, tmp_path_factory: pytest.TempPathFactory) -> Any:
    return _run(tiny_model, COMPLETE_MANIFEST, tmp_path_factory.mktemp("complete"))


def test_complete_declared_scope_publishes_a_labelled_index(complete_run: Any) -> None:
    coverage = complete_run.coverage
    assert coverage.status is CoverageStatus.DECLARED
    assert coverage.complete is True
    assert coverage.covers_full_suite is True
    assert complete_run.index.complete is True
    assert complete_run.index.index is not None
    assert complete_run.index.scope_kind == "authored_fixture"

    # Hand-check the frozen aggregate from the reported components.
    tasks = complete_run.tasks
    blimp_macro = sum(tasks["blimp"].subdataset_scores.values()) / len(
        tasks["blimp"].subdataset_scores
    )
    components = [
        (tasks["arc_easy"].acc_norm - tasks["arc_easy"].chance) / (1 - tasks["arc_easy"].chance),
        (tasks["hellaswag"].acc_norm - 0.25) / 0.75,
        (tasks["piqa"].acc - 0.5) / 0.5,
        (blimp_macro - 0.5) / 0.5,
    ]
    assert complete_run.index.index == pytest.approx(100.0 * sum(components) / 4, abs=1e-9)


def test_complete_fixture_scope_is_never_research_evidence(complete_run: Any) -> None:
    assert complete_run.coverage.research_eligible() is False
    assert any("never a benchmark result" in note for note in complete_run.notes)


def test_every_declared_item_is_accounted_for_by_identity(complete_run: Any) -> None:
    coverage = complete_run.coverage
    assert coverage.tasks["arc_easy"].expected_item_ids == tuple(
        f"arc_easy#syn_arc_{i:04d}" for i in range(4)
    )
    assert coverage.tasks["arc_easy"].scored_item_ids == (
        coverage.tasks["arc_easy"].expected_item_ids
    )
    # BLiMP ids are namespaced by subdataset, so they cannot cross-satisfy.
    blimp_ids = coverage.tasks["blimp"].scored_item_ids
    assert len({i.split("#")[0] for i in blimp_ids}) == 2
    assert coverage.tasks["blimp"].observed_subdatasets == (
        "blimp_adjunct_island",
        "blimp_anaphor_gender_agreement",
    )


def test_declared_ids_survive_preprocessing(complete_run: Any) -> None:
    """HellaSwag's process_docs rewrites the doc; the locator must survive."""
    items = complete_run.tasks["hellaswag"].items
    assert all(i.declared_item_id is not None for i in items)
    assert all(i.declared_item_id.startswith("syn_hellaswag/train/") for i in items)
    # Positional doc_id is retained but is not the identity.
    assert {i.item_id for i in items} == {"0", "1", "2", "3"}
    assert all(i.namespaced_id.startswith("hellaswag#syn_hellaswag/") for i in items)


def test_a_lost_declared_id_is_an_error_not_a_fallback() -> None:
    with pytest.raises(ValueError, match="lost its declared id field"):
        items_from_harness_samples(
            "arc_easy",
            [
                {
                    "doc_id": 0,
                    "target": 0,
                    "resps": [[0.5, False], [0.1, False]],
                    "doc": {"choices": ["aa", "bb"]},
                }
            ],
            item_id_field="id",
        )


# ---------------------------------------------------------------- counterexamples


def test_all_four_tasks_under_a_limit_do_not_produce_an_index(
    tiny_model: Any, tmp_path: Path
) -> None:
    """The reproduced D05 headline, now refused."""
    evidence = _run(tiny_model, COMPLETE_MANIFEST, tmp_path / "limited", limit=2)

    assert set(evidence.tasks) == {"arc_easy", "hellaswag", "piqa", "blimp"}
    assert evidence.index.complete is False
    assert evidence.index.index is None
    assert evidence.coverage.complete is False
    # Per-task metrics remain useful and carry the true expected population.
    assert evidence.tasks["arc_easy"].scored_items == 2
    assert evidence.tasks["arc_easy"].expected_items == 4
    assert evidence.coverage.tasks["arc_easy"].missing_item_ids == (
        "arc_easy#syn_arc_0002",
        "arc_easy#syn_arc_0003",
    )


def test_a_narrower_declared_scope_is_complete_only_within_itself(
    tiny_model: Any, tmp_path: Path
) -> None:
    evidence = _run(tiny_model, SUBSET_MANIFEST, tmp_path / "subset")
    assert evidence.coverage.complete is True
    assert evidence.coverage.covers_full_suite is False
    assert evidence.index.complete is True
    assert set(evidence.tasks) == {"arc_easy", "piqa"}
    assert any("not the full four-task suite" in note for note in evidence.notes)


def test_a_missing_item_in_the_artifact_is_refused_before_execution(
    tiny_model: Any, tmp_path: Path
) -> None:
    """A shrunken artifact fails verification; it never reaches scoring."""
    from xlm.evaluation.inputs import MembershipError

    manifest = _copy_scope(tmp_path)
    data = manifest.parent / "piqa.json"
    records = json.loads(data.read_text(encoding="utf-8"))
    records.pop()
    _write_json(data, records)
    _reseal(manifest, data, "piqa")

    with pytest.raises(MembershipError, match="Missing"):
        _verified(manifest)


def test_results_are_stable_under_input_ordering(tiny_model: Any, tmp_path: Path) -> None:
    baseline = _run(tiny_model, COMPLETE_MANIFEST, tmp_path / "ordered")

    manifest = _copy_scope(tmp_path)
    data = manifest.parent / "arc_easy.json"
    records = json.loads(data.read_text(encoding="utf-8"))
    records.reverse()
    _write_json(data, records)
    payload = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    for entry in payload["selections"]:
        if entry["task"] == "arc_easy":
            entry["item_ids"] = list(reversed(entry["item_ids"]))
            entry["content_sha256"] = hashlib.sha256(data.read_bytes()).hexdigest()
            entry["content_bytes"] = data.stat().st_size
    manifest.write_bytes(yaml.safe_dump(payload, sort_keys=False).encode("utf-8"))

    reordered = run_declared_input_suite(
        model=tiny_model,
        tokenizer=ByteTokenizer(),
        verified=_verified(manifest),
        checkpoint_hash="authored_fixture_ckpt",
        device="cpu",
        output_dir=tmp_path / "reordered",
        use_evidence_cache=False,
    )
    assert reordered.index.index == pytest.approx(baseline.index.index)
    assert reordered.coverage.tasks["arc_easy"].scored_item_ids == (
        baseline.coverage.tasks["arc_easy"].scored_item_ids
    )
    # Same population and same prompts, so the raw identity is unchanged.
    assert reordered.raw_identity_fingerprint == baseline.raw_identity_fingerprint


# ---------------------------------------------------------------- cache identity


def test_identical_request_reuses_the_cached_aggregate(tiny_model: Any, tmp_path: Path) -> None:
    out = tmp_path / "cached"
    first = _run(tiny_model, COMPLETE_MANIFEST, out, use_evidence_cache=True)
    second = _run(tiny_model, COMPLETE_MANIFEST, out, use_evidence_cache=True)
    assert second.aggregate_identity_fingerprint == first.aggregate_identity_fingerprint
    assert second.index.index == pytest.approx(first.index.index)


def test_a_relabelling_keeps_the_raw_identity_but_invalidates_the_aggregate(
    tiny_model: Any, tmp_path: Path
) -> None:
    """Changed labels may permit raw reuse; the aggregate must not be reused."""
    out = tmp_path / "relabel"
    baseline = _run(tiny_model, COMPLETE_MANIFEST, out, use_evidence_cache=True)

    manifest = _copy_scope(tmp_path)
    data = manifest.parent / "arc_easy.json"
    records = json.loads(data.read_text(encoding="utf-8"))
    for record in records:
        record["answerKey"] = "B" if record["answerKey"] == "A" else "A"
    _write_json(data, records)
    _reseal(manifest, data, "arc_easy")

    relabelled = run_declared_input_suite(
        model=tiny_model,
        tokenizer=ByteTokenizer(),
        verified=_verified(manifest),
        checkpoint_hash="authored_fixture_ckpt",
        device="cpu",
        output_dir=out,
        use_evidence_cache=True,
    )
    assert relabelled.raw_identity_fingerprint == baseline.raw_identity_fingerprint
    assert relabelled.aggregate_identity_fingerprint != (baseline.aggregate_identity_fingerprint)
    assert relabelled.tasks["arc_easy"].acc_norm != pytest.approx(
        baseline.tasks["arc_easy"].acc_norm
    )
    assert any("cached aggregate invalidated" in note for note in relabelled.notes)


def test_a_changed_prompt_changes_the_raw_identity(tiny_model: Any, tmp_path: Path) -> None:
    baseline = _run(tiny_model, COMPLETE_MANIFEST, tmp_path / "prompt-a")

    manifest = _copy_scope(tmp_path)
    data = manifest.parent / "arc_easy.json"
    records = json.loads(data.read_text(encoding="utf-8"))
    records[0]["question"] = "A completely different fixture question?"
    _write_json(data, records)
    _reseal(manifest, data, "arc_easy")

    changed = run_declared_input_suite(
        model=tiny_model,
        tokenizer=ByteTokenizer(),
        verified=_verified(manifest),
        checkpoint_hash="authored_fixture_ckpt",
        device="cpu",
        output_dir=tmp_path / "prompt-b",
        use_evidence_cache=False,
    )
    assert changed.raw_identity_fingerprint != baseline.raw_identity_fingerprint


def test_a_changed_runtime_limit_changes_both_identities(tiny_model: Any, tmp_path: Path) -> None:
    full = _run(tiny_model, COMPLETE_MANIFEST, tmp_path / "full")
    limited = _run(tiny_model, COMPLETE_MANIFEST, tmp_path / "lim", limit=2)
    assert limited.raw_identity_fingerprint != full.raw_identity_fingerprint
    assert limited.aggregate_identity_fingerprint != full.aggregate_identity_fingerprint


def test_a_changed_checkpoint_changes_the_raw_identity(tiny_model: Any, tmp_path: Path) -> None:
    baseline = _run(tiny_model, COMPLETE_MANIFEST, tmp_path / "ckpt-a")
    other = run_declared_input_suite(
        model=tiny_model,
        tokenizer=ByteTokenizer(),
        verified=_verified(COMPLETE_MANIFEST),
        checkpoint_hash="a_different_checkpoint",
        device="cpu",
        output_dir=tmp_path / "ckpt-b",
        use_evidence_cache=False,
    )
    assert other.raw_identity_fingerprint != baseline.raw_identity_fingerprint


def test_legacy_evidence_without_coverage_stays_unverified(
    complete_run: Any, tmp_path: Path
) -> None:
    """Historical receipts are labelled, not upgraded."""
    payload = complete_run.to_dict()
    payload.pop("coverage")
    payload.pop("raw_identity_fingerprint")
    payload.pop("aggregate_identity_fingerprint")
    legacy_path = tmp_path / "legacy_evidence.json"
    legacy_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    loaded = EvaluationEvidence.load(legacy_path)
    assert loaded.coverage.status is CoverageStatus.LEGACY_UNVERIFIED
    assert loaded.coverage.complete is False
    assert loaded.coverage.research_eligible() is False
    assert any("legacy evidence" in note for note in loaded.notes)
    # The stored index value is preserved verbatim; it is simply not trusted.
    assert loaded.index.index == pytest.approx(complete_run.index.index)


# ---------------------------------------------------------------- undeclared route


def test_the_undeclared_fixture_route_withholds_the_index(tiny_model: Any, tmp_path: Path) -> None:
    fixture_tasks = REPO_ROOT / "fixtures" / "eval" / "tasks"
    variant = TaskVariant(
        variant_id="xlm_xlm_fixture_mc_search",
        lm_eval_task="xlm_fixture_mc",
        tier=SuiteTier.SEARCH,
        split="train",
        normalized_metric="acc_norm",
        chance_reference="mean_inverse_n_choices",
    )
    evidence = run_harness_suite(
        model=tiny_model,
        tokenizer=ByteTokenizer(),
        variants=[variant],
        checkpoint_hash="fixture_ckpt",
        device="cpu",
        include_path=fixture_tasks,
        output_dir=tmp_path / "undeclared",
        use_evidence_cache=False,
    )
    assert evidence.coverage.status is CoverageStatus.UNDECLARED
    assert evidence.index.complete is False
    assert evidence.tasks["xlm_fixture_mc"].expected_items is None
    # Scored counts are observed; expected is never back-filled from them.
    assert evidence.coverage.tasks["xlm_fixture_mc"].expected_items == 0
    assert evidence.coverage.tasks["xlm_fixture_mc"].scored_items > 0


def test_pinned_remote_revisions_remain_blocked_on_the_undeclared_route(
    tiny_model: Any, tmp_path: Path
) -> None:
    from xlm.evaluation.harness_runner import HarnessRunError
    from xlm.evaluation.suites import resolve_suite

    variants = resolve_suite(SuiteTier.SEARCH, load_dataset_pins(PINS))
    assert any(v.dataset_revision for v in variants)
    with pytest.raises(HarnessRunError, match="BLOCKED"):
        run_harness_suite(
            model=tiny_model,
            tokenizer=ByteTokenizer(),
            variants=variants,
            checkpoint_hash="fixture_ckpt",
            device="cpu",
            output_dir=tmp_path / "blocked",
            use_evidence_cache=False,
        )


# ---------------------------------------------------------------- public CLI


def _cli(args: list[str], tmp_path: Path) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["XLM_HOME"] = str(tmp_path / "xlm_home")
    env["HF_HUB_OFFLINE"] = "1"
    env["HF_DATASETS_OFFLINE"] = "1"
    env["TRANSFORMERS_OFFLINE"] = "1"
    # Nothing may reach a provider: no token, no writable shared cache.
    env.pop("HF_TOKEN", None)
    env.pop("HUGGING_FACE_HUB_TOKEN", None)
    env["HF_HOME"] = str(tmp_path / "hf_home")
    return subprocess.run(
        [sys.executable, "-m", "xlm.cli.main", "evaluate", *args],
        capture_output=True,
        # Decode explicitly: the console locale is not necessarily UTF-8, and a
        # decode failure would silently blank out the output under test.
        encoding="utf-8",
        errors="replace",
        check=False,
        cwd=REPO_ROOT,
        env=env,
    )


@pytest.mark.slow
def test_cli_verify_inputs_only_needs_no_checkpoint(tmp_path: Path) -> None:
    result = _cli(
        ["--suite", "search", "--inputs", str(COMPLETE_MANIFEST), "--verify-inputs-only"],
        tmp_path,
    )
    assert result.returncode == 0, result.stderr
    assert "Evaluation inputs VERIFIED" in result.stdout
    assert "Nothing was evaluated" in result.stdout
    # No run directory was created, so no model was ever allocated.
    assert not (tmp_path / "xlm_home" / "runs").exists()


@pytest.mark.slow
def test_cli_verify_inputs_only_reports_a_bad_manifest(tmp_path: Path) -> None:
    manifest = _copy_scope(tmp_path)
    data = manifest.parent / "arc_easy.json"
    data.write_bytes(data.read_bytes().replace(b"blue", b"bluX", 1))
    result = _cli(
        ["--suite", "search", "--inputs", str(manifest), "--verify-inputs-only"], tmp_path
    )
    assert result.returncode == 1
    assert "evaluation inputs rejected" in result.stderr


@pytest.mark.slow
def test_cli_declared_run_publishes_a_labelled_index(tiny_checkpoint: Path, tmp_path: Path) -> None:
    result = _cli(
        [
            str(tiny_checkpoint),
            "--suite",
            "search",
            "--inputs",
            str(COMPLETE_MANIFEST),
            "--output-dir",
            str(tmp_path / "out"),
        ],
        tmp_path,
    )
    assert result.returncode == 0, result.stderr
    assert "Four-task index:" in result.stdout
    assert "authored fixture suite v1 (complete)" in result.stdout
    assert "NOT research evidence" in result.stdout
    assert "scored 4/4 expected" in result.stdout


@pytest.mark.slow
def test_cli_declared_run_under_a_limit_withholds_the_index(
    tiny_checkpoint: Path, tmp_path: Path
) -> None:
    result = _cli(
        [
            str(tiny_checkpoint),
            "--suite",
            "search",
            "--inputs",
            str(COMPLETE_MANIFEST),
            "--limit",
            "2",
            "--output-dir",
            str(tmp_path / "out"),
        ],
        tmp_path,
    )
    assert result.returncode == 0, result.stderr
    assert "Index WITHHELD" in result.stdout
    assert "Four-task index:" not in result.stdout
    assert "scored 2/4 expected" in result.stdout


@pytest.mark.slow
def test_cli_refuses_to_mix_declared_inputs_with_task_overrides(
    tiny_checkpoint: Path, tmp_path: Path
) -> None:
    result = _cli(
        [
            str(tiny_checkpoint),
            "--suite",
            "search",
            "--inputs",
            str(COMPLETE_MANIFEST),
            "--tasks",
            "arc_easy",
            "--output-dir",
            str(tmp_path / "out"),
        ],
        tmp_path,
    )
    assert result.returncode != 0
    assert "would silently change it" in (result.stdout + result.stderr)


@pytest.mark.slow
def test_cli_requires_a_checkpoint_to_evaluate(tmp_path: Path) -> None:
    result = _cli(["--suite", "search", "--inputs", str(COMPLETE_MANIFEST)], tmp_path)
    assert result.returncode == 1
    assert "a checkpoint is required" in result.stderr


# ------------------------------------------------- acquisition receipts (closeout)


def _receipt_records() -> list[dict[str, object]]:
    return [
        {
            "id": "rcpt_arc_0000",
            "question": "What colour is the receipt sky?",
            "choices": {"text": ["blue", "plaid"], "label": ["A", "B"]},
            "answerKey": "A",
        },
        {
            "id": "rcpt_arc_0001",
            "question": "How many receipt moons orbit?",
            "choices": {"text": ["two", "nine"], "label": ["A", "B"]},
            "answerKey": "B",
        },
    ]


def _write_jsonl(path: Path, records: list[dict[str, object]]) -> None:
    """Single-line JSON records (JSONL framing) with sorted keys, so the bytes
    differ from compact insertion-ordered serializations of the same content."""
    lines = [json.dumps(record, sort_keys=True) for record in records]
    path.write_bytes(("\n".join(lines) + "\n").encode("utf-8"))


def _acquire_whole_file(tmp_path: Path, name: str, records: list[dict[str, object]]) -> Any:
    """Verify a local authored file through the real D02 verifier (no network)."""
    from xlm.data.acquisition.plan import AcquisitionLimits, AcquisitionMode, AcquisitionPlan
    from xlm.data.acquisition.verifier import AcquisitionVerifier

    out = tmp_path / "acquired"
    out.mkdir(exist_ok=True)
    (out / name).write_bytes(
        ("\n".join(json.dumps(record) for record in records) + "\n").encode("utf-8")
    )
    plan = AcquisitionPlan(
        plan_id=f"plan_{name.replace('.', '_')}",
        source_id="authored",
        provider="local",
        repository=str(out),
        revision="authored-test-v1",
        mode=AcquisitionMode.WHOLE_FILE,
        selected_files=[name],
        row_ranges=None,
        limits=AcquisitionLimits(max_transferred_bytes=65536, max_records=100),
        output_artifact_id=f"artifact_{name.replace('.', '_')}",
        is_pilot=True,
    )
    return AcquisitionVerifier(plan, out, journal=None).verify(), out


def _plant_store(store_root: Path, artifact_id: str, receipt: Any) -> Path:
    target = store_root / "raw_dataset" / artifact_id
    target.mkdir(parents=True, exist_ok=True)
    (target / "acquisition_receipt.json").write_text(
        json.dumps(receipt.model_dump(), indent=2, sort_keys=True), encoding="utf-8"
    )
    return target


def _receipt_manifest(data_file: Path, receipts: list[str], base_dir: Path) -> Any:
    from xlm.evaluation.harness import harness_version
    from xlm.evaluation.inputs import SuiteTier, build_evaluation_inputs

    return build_evaluation_inputs(
        scope_label="receipt closure scope v1",
        scope_kind="authored_fixture",
        exposure_class="authored_fixture",
        tier=SuiteTier.SEARCH,
        harness_version=harness_version(),
        entries=[
            {
                "task": "arc_easy",
                "leaf_task": "arc_easy",
                "source_repository": "loopback-authored-test",
                "source_revision": "authored-test-v1",
                "source_split": "train",
                "record_schema_version": "arc_easy.official_shape.v1",
                "adapter_version": "xlm_eval_json.v1",
                "item_id_field": "id",
                "data_file": str(data_file),
                "source_config": "ARC-Easy",
                "label_field": "answerKey",
                "notes": ["SYNTHETIC receipt-closure content"],
            }
        ],
        base_dir=base_dir,
        acquisition_receipts=receipts,
        notes=["receipt verification closeout; authored fixture, not research evidence"],
    )


def _verify_manifest(manifest: Any, store_root: Path | None) -> Any:
    from xlm.evaluation.inputs import SuiteTier, verify_evaluation_inputs

    return verify_evaluation_inputs(
        manifest,
        tier=SuiteTier.SEARCH,
        harness_version=harness_version(),
        acquisition_store_root=store_root,
    )


def test_receipt_exact_lineage_verifies(tmp_path: Path) -> None:
    receipt, out = _acquire_whole_file(tmp_path, "data.jsonl", _receipt_records())
    store = tmp_path / "store"
    _plant_store(store, "artifact_data_jsonl", receipt)
    manifest = _receipt_manifest(out / "data.jsonl", [receipt.receipt_id], tmp_path)
    verified = _verify_manifest(manifest, store)
    assert verified.verified_receipts == (receipt.receipt_id,)
    assert verified.summary()["verified_acquisition_receipts"] == [receipt.receipt_id]


def test_receipt_missing_is_refused(tmp_path: Path) -> None:
    from xlm.evaluation.inputs import InputVerificationError

    receipt, out = _acquire_whole_file(tmp_path, "data.jsonl", _receipt_records())
    manifest = _receipt_manifest(out / "data.jsonl", [receipt.receipt_id], tmp_path)
    with pytest.raises(InputVerificationError, match="was not found"):
        _verify_manifest(manifest, tmp_path / "empty-store")


def test_receipt_unresolvable_without_store_root_is_refused(tmp_path: Path) -> None:
    from xlm.evaluation.inputs import InputVerificationError

    receipt, out = _acquire_whole_file(tmp_path, "data.jsonl", _receipt_records())
    manifest = _receipt_manifest(out / "data.jsonl", [receipt.receipt_id], tmp_path)
    with pytest.raises(InputVerificationError, match="no acquisition store root"):
        _verify_manifest(manifest, None)


def test_receipt_corrupt_is_refused(tmp_path: Path) -> None:
    from xlm.evaluation.inputs import InputVerificationError

    receipt, out = _acquire_whole_file(tmp_path, "data.jsonl", _receipt_records())
    store = tmp_path / "store"
    _plant_store(store, "artifact_data_jsonl", receipt)
    (store / "raw_dataset" / "artifact_data_jsonl" / "acquisition_receipt.json").write_bytes(
        b"{nope"
    )
    manifest = _receipt_manifest(out / "data.jsonl", [receipt.receipt_id], tmp_path)
    with pytest.raises(InputVerificationError, match="corrupt acquisition receipt"):
        _verify_manifest(manifest, store)


def test_receipt_unrelated_substitution_is_refused(tmp_path: Path) -> None:
    from xlm.evaluation.inputs import InputVerificationError

    receipt, out = _acquire_whole_file(tmp_path, "data.jsonl", _receipt_records())
    other_records = [dict(record, id="other_0000") for record in _receipt_records()[:1]]
    other_receipt, other_out = _acquire_whole_file(tmp_path, "other.jsonl", other_records)
    _plant_store(tmp_path / "store", "artifact_other", other_receipt)
    manifest = _receipt_manifest(out / "data.jsonl", [other_receipt.receipt_id], tmp_path)
    with pytest.raises(InputVerificationError, match="binds to no executed selection"):
        _verify_manifest(manifest, tmp_path / "store")


def test_receipt_tampered_digest_is_refused(tmp_path: Path) -> None:
    from xlm.evaluation.inputs import InputVerificationError

    receipt, out = _acquire_whole_file(tmp_path, "data.jsonl", _receipt_records())
    store = tmp_path / "store"
    target = _plant_store(store, "artifact_data_jsonl", receipt)
    payload = json.loads((target / "acquisition_receipt.json").read_text(encoding="utf-8"))
    payload["files"][0]["locally_computed_sha256"] = "0" * 64
    (target / "acquisition_receipt.json").write_text(json.dumps(payload), encoding="utf-8")
    manifest = _receipt_manifest(out / "data.jsonl", [receipt.receipt_id], tmp_path)
    with pytest.raises(InputVerificationError, match="binds to no executed selection"):
        _verify_manifest(manifest, store)


def test_receipt_derived_locator_chain_verifies(tmp_path: Path) -> None:
    receipt, out = _acquire_whole_file(tmp_path, "data.jsonl", _receipt_records())
    store = tmp_path / "store"
    _plant_store(store, "artifact_data_jsonl", receipt)
    derived = []
    for index, record in enumerate(_receipt_records()):
        derived.append(
            {
                **record,
                "_xlm_acquisition": {
                    "source_file": "data.jsonl",
                    "row_index": index,
                    "revision": receipt.revision,
                    "selection_hash": receipt.plan_hash,
                },
            }
        )
    derived_path = out / "derived.jsonl"
    _write_jsonl(derived_path, derived)
    assert derived_path.read_bytes() != (out / "data.jsonl").read_bytes()
    manifest = _receipt_manifest(derived_path, [receipt.receipt_id], tmp_path)
    verified = _verify_manifest(manifest, store)
    assert verified.verified_receipts == (receipt.receipt_id,)


def test_receipt_broken_locator_chain_is_refused(tmp_path: Path) -> None:
    from xlm.evaluation.inputs import InputVerificationError

    receipt, out = _acquire_whole_file(tmp_path, "data.jsonl", _receipt_records())
    store = tmp_path / "store"
    _plant_store(store, "artifact_data_jsonl", receipt)
    derived = []
    for index, record in enumerate(_receipt_records()):
        derived.append(
            {
                **record,
                "_xlm_acquisition": {
                    "source_file": "other.jsonl",
                    "row_index": index,
                    "revision": receipt.revision,
                    "selection_hash": "0" * 64,
                },
            }
        )
    derived_path = out / "derived.jsonl"
    _write_jsonl(derived_path, derived)
    manifest = _receipt_manifest(derived_path, [receipt.receipt_id], tmp_path)
    with pytest.raises(InputVerificationError, match="binds to no executed selection"):
        _verify_manifest(manifest, store)


def test_receipt_changed_derived_input_is_refused(tmp_path: Path) -> None:
    from xlm.evaluation.inputs import InputVerificationError, MembershipError

    receipt, out = _acquire_whole_file(tmp_path, "data.jsonl", _receipt_records())
    store = tmp_path / "store"
    _plant_store(store, "artifact_data_jsonl", receipt)
    manifest = _receipt_manifest(out / "data.jsonl", [receipt.receipt_id], tmp_path)
    with (out / "data.jsonl").open("ab") as stream:
        stream.write(b'{"id": "intruder"}\n')
    with pytest.raises((InputVerificationError, MembershipError)):
        _verify_manifest(manifest, store)


def test_receipt_verify_inputs_only_reports_verified_receipts(tmp_path: Path) -> None:
    receipt, out = _acquire_whole_file(tmp_path, "data.jsonl", _receipt_records())
    store = tmp_path / "store"
    _plant_store(store, "artifact_data_jsonl", receipt)
    manifest = _receipt_manifest(out / "data.jsonl", [receipt.receipt_id], tmp_path)
    manifest_path = tmp_path / "manifest.yaml"
    manifest_path.write_text(yaml.safe_dump(manifest.to_dict(), sort_keys=False), encoding="utf-8")
    result = _cli(
        [
            "--suite",
            "search",
            "--inputs",
            str(manifest_path),
            "--verify-inputs-only",
            "--acquisition-store-root",
            str(store),
        ],
        tmp_path,
    )
    assert result.returncode == 0, result.stderr
    assert f"Acquisition receipt verified: {receipt.receipt_id}" in result.stdout
    missing = _cli(
        ["--suite", "search", "--inputs", str(manifest_path), "--verify-inputs-only"],
        tmp_path,
    )
    assert missing.returncode == 1
    assert "no acquisition store root" in missing.stderr
