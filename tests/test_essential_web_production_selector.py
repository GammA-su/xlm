"""Frozen Essential-Web B-normal production selector (authored fixtures, offline).

Every record here is synthetic and invented to exercise the production
adapter; nothing is real corpus text and no test touches the network
(sockets are blocked). The reference for every expected outcome is the
frozen evaluator itself, loaded independently of the production loader.
Real-evidence reproduction on the development and M replicates is a
separate operator command, not a pytest.
"""

from __future__ import annotations

import copy
import importlib.util
import itertools
import json
import shutil
import socket
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml
from typer.testing import CliRunner

from xlm.cli.data_cmd import app as data_app
from xlm.data.acquisition.plan import (
    AcquisitionLimits,
    AcquisitionMode,
    AcquisitionPlan,
    load_acquisition_plan,
    save_acquisition_plan,
)
from xlm.data.adapters import essential_web_selector as selector
from xlm.data.adapters.columns import columns_for, parse_adapter_spec
from xlm.data.adapters.mix01_adapters import (
    ADAPTERS_BY_ID,
    EssentialWebAdapter,
    EssentialWebMalformedRowError,
    EssentialWebSelectedAdapter,
    EssentialWebSelectorOtherComponentError,
    EssentialWebSelectorRejectedError,
    EssentialWebSelectorUnassignedError,
    MissingFieldError,
    RecordRejectedError,
)
from xlm.data.adapters.rejections import is_recordable_rejection, rejection_code
from xlm.data.evidence_v2 import fasttrack_freeze
from xlm.data.sources import essential_web_production as production
from xlm.data.sources.mix01 import load_mix01_views

REPO_ROOT = Path(__file__).resolve().parents[1]
EVALUATOR_PATH = REPO_ROOT / selector.EVALUATOR_RELATIVE_PATH
POLICY_PATH = REPO_ROOT / selector.POLICY_RELATIVE_PATH
VIEWS_PATH = REPO_ROOT / "recipes" / "mixtures" / "mix01_views.yaml"
PRESET_PATH = REPO_ROOT / "recipes" / "mixtures" / "mix01.yaml"
QUOTAS_PATH = REPO_ROOT / "recipes" / "mixtures" / "mix01_quotas_6b.yaml"
FREEZE_PATH = (
    REPO_ROOT
    / "docs"
    / "implementation"
    / "evidence"
    / "ESSENTIAL-WEB-SELECTOR-FASTTRACK-FREEZE"
    / "freeze.json"
)
REVISION = selector.SOURCE_REVISION
SCIENCE, PRACTICAL, PROSE = selector.ADMITTED_COMPONENTS
SOURCE_FILE = "data/crawl=AUTHORED/train-00000-of-00001.parquet"
AUTHORED_TEXT = "Authored fixture paragraph about tidal basins; not corpus text."

_SPEC = importlib.util.spec_from_file_location("production_selector_reference", EVALUATOR_PATH)
assert _SPEC is not None and _SPEC.loader is not None
reference = importlib.util.module_from_spec(_SPEC)
sys.modules["production_selector_reference"] = reference
_SPEC.loader.exec_module(reference)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_: Any, **__: Any) -> None:
        raise AssertionError("network access attempted in an offline test")

    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)


@pytest.fixture(scope="module")
def spec() -> dict[str, Any]:
    loaded, digest = reference.load_policy_spec(POLICY_PATH)
    assert digest == selector.POLICY_DIGEST
    assert isinstance(loaded, dict)
    return loaded


@pytest.fixture(scope="module")
def frozen() -> selector.FrozenEssentialWebSelector:
    return selector.FrozenEssentialWebSelector.load()


@pytest.fixture(scope="module")
def adapters() -> dict[str, EssentialWebSelectedAdapter]:
    return {name: EssentialWebSelectedAdapter(name) for name in selector.ADMITTED_COMPONENTS}


def _row(
    f: Any = "510.2",
    d: Any = "Academic Writing",
    k: Any = "Conceptual",
    a: Any = "No Artifacts",
    m: Any = "No missing content",
    t: Any = "Highly Correct",
    e: Any = 0.95,
) -> dict[str, Any]:
    """Authored full upstream-shaped row (metadata plus the fields the renderer needs)."""
    return {
        "text": AUTHORED_TEXT,
        "id": 7,
        "pid": "authored-pid-7",
        "metadata": {"source_domain": "example.invalid", "snapshot_id": "AUTHORED"},
        "eai_taxonomy": {
            "free_decimal_correspondence": {"primary": {"code": f}},
            "document_type_v2": {"primary": {"label": d}},
            "bloom_knowledge_domain": {"primary": {"label": k}},
            "extraction_artifacts": {"primary": {"label": a}},
            "missing_content": {"primary": {"label": m}},
            "technical_correctness": {"primary": {"label": t}},
        },
        "quality_signals": {"fasttext": {"english": e}},
    }


def _reference_final(record: dict[str, Any], spec: dict[str, Any]) -> str:
    fields, reasons, _ = reference.validate_row(record, spec)
    if reasons:
        return "rejected"
    return str(reference.evaluate_policy(fields, "B", "normal", spec)["final"])


def _adapt(adapter: Any, record: dict[str, Any], row: int = 0) -> Any:
    return adapter.adapt(record, source_file=SOURCE_FILE, source_row=row, source_revision=REVISION)


@pytest.mark.parametrize("level", ["level_1", "level_2", "level_3"])
@pytest.mark.parametrize("shape", ["absent", "null", "empty", "whitespace", "nonempty"])
def test_optional_fdc_labels_preserve_selector_and_text(
    level: str, shape: str, frozen: selector.FrozenEssentialWebSelector
) -> None:
    record = _row()
    record["text"] = " Authored paragraph.\nSecond\u0085part\u2028end.\t "
    primary = record["eai_taxonomy"]["free_decimal_correspondence"]["primary"]
    labels = {"level_1": "Science", "level_2": "Mathematics", "level_3": "Topology"}
    primary["labels"] = labels
    before = frozen.decide(record)
    if shape == "absent":
        del labels[level]
    else:
        primary["labels"][level] = {
            "null": None,
            "empty": "",
            "whitespace": " \t\n\u2028",
            "nonempty": "  Preserved label \t",
        }[shape]
    original = copy.deepcopy(record)
    base = _adapt(EssentialWebAdapter(SCIENCE), record)
    selected = _adapt(EssentialWebSelectedAdapter(SCIENCE), record)
    assert frozen.decide(record) == before
    assert selected.text == base.text == record["text"]
    assert base.source_metadata["fdc_primary_code"] == primary["code"] == "510.2"
    for document in (base, selected):
        if shape == "nonempty":
            assert document.source_metadata[f"fdc_{level}"] == "  Preserved label \t"
        else:
            assert f"fdc_{level}" not in document.source_metadata
        for other in set(labels) - {level}:
            assert document.source_metadata[f"fdc_{other}"] == labels[other]
    assert record == original


@pytest.mark.parametrize("level", ["level_1", "level_2", "level_3"])
@pytest.mark.parametrize("value", [0, 1.5, False, [], {}])
def test_nonstring_optional_fdc_label_remains_malformed(level: str, value: Any) -> None:
    record = _row()
    record["eai_taxonomy"]["free_decimal_correspondence"]["primary"]["labels"] = {level: value}
    with pytest.raises(MissingFieldError, match=level):
        _adapt(EssentialWebAdapter(SCIENCE), record)
    with pytest.raises(EssentialWebMalformedRowError, match="essential_web_unusable_record"):
        _adapt(EssentialWebSelectedAdapter(SCIENCE), record)


def test_authored_live_shape_empty_fdc_levels() -> None:
    record = _row(f="005.4", d="Tutorial", k="Procedural")
    record["eai_taxonomy"]["free_decimal_correspondence"]["primary"]["labels"] = {
        "level_1": "Computer science",
        "level_2": "",
        "level_3": "",
    }
    document = _adapt(EssentialWebSelectedAdapter(PRACTICAL), record)
    assert document.text == AUTHORED_TEXT
    assert document.source_metadata["fdc_primary_code"] == "005.4"
    assert document.source_metadata["fdc_level_1"] == "Computer science"
    assert "fdc_level_2" not in document.source_metadata
    assert "fdc_level_3" not in document.source_metadata


def test_other_optional_strings_and_render_before_policy_remain_strict() -> None:
    record = _row(e=0.1)
    record["metadata"]["source_domain"] = ""
    with pytest.raises(MissingFieldError, match="source_domain"):
        _adapt(EssentialWebAdapter(SCIENCE), record)
    with pytest.raises(EssentialWebMalformedRowError):
        _adapt(EssentialWebSelectedAdapter(SCIENCE), record)


# --------------------------------------------------------------------------
# Identity: the production selector is the frozen evaluator, unchanged.
# --------------------------------------------------------------------------


def test_selector_constants_equal_the_committed_freeze() -> None:
    freeze = fasttrack_freeze.load_freeze(FREEZE_PATH)
    chosen = freeze["production_selector"]
    assert selector.FREEZE_DIGEST == freeze["freeze_digest"]
    assert selector.FREEZE_VERSION == freeze["version"]
    assert (selector.POLICY, selector.TIER) == (chosen["policy"], chosen["tier"])
    assert selector.CONDITION == chosen["condition"] == "B-normal"
    assert selector.SELECTOR_ID == chosen["id"]
    assert selector.POLICY_DIGEST == chosen["policy_digest"]
    assert selector.EVALUATOR_SHA256 == freeze["evaluator"]["sha256"]
    assert selector.POLICY_FILE_SHA256 == freeze["evaluator"]["policy"]["sha256"]
    assert selector.SOURCE_REVISION == freeze["source"]["revision"]
    assert list(selector.ADMITTED_COMPONENTS) == chosen["admitted_components"]
    assert freeze["t_arm"]["status"] == "NOT_RUN_NO_TWO_INDEPENDENT_HUMAN_REVIEWERS"


def _checkout(tmp_path: Path) -> Path:
    for relative in (selector.EVALUATOR_RELATIVE_PATH, selector.POLICY_RELATIVE_PATH):
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO_ROOT / relative, target)
    return tmp_path


def test_loader_accepts_an_exact_copy_and_refuses_any_drift(tmp_path: Path) -> None:
    root = _checkout(tmp_path / "exact")
    assert selector.FrozenEssentialWebSelector.load(root).decide(_row()).final == SCIENCE

    edited = _checkout(tmp_path / "evaluator")
    path = edited / selector.EVALUATOR_RELATIVE_PATH
    path.write_bytes(path.read_bytes().replace(b'"gate_english"', b'"gate_english_x"', 1))
    with pytest.raises(selector.SelectorIdentityError, match="evaluator"):
        selector.FrozenEssentialWebSelector.load(edited)

    tuned = _checkout(tmp_path / "policy")
    policy = tuned / selector.POLICY_RELATIVE_PATH
    policy.write_bytes(policy.read_bytes().replace(b"english_min: 0.8", b"english_min: 0.7", 1))
    with pytest.raises(selector.SelectorIdentityError, match="policy spec"):
        selector.FrozenEssentialWebSelector.load(tuned)

    # A comment-only edit keeps the canonical digest but still changes the file.
    commented = _checkout(tmp_path / "comment")
    policy = commented / selector.POLICY_RELATIVE_PATH
    policy.write_bytes(policy.read_bytes() + b"# note\n")
    with pytest.raises(selector.SelectorIdentityError, match="policy spec"):
        selector.FrozenEssentialWebSelector.load(commented)

    with pytest.raises(selector.SelectorIdentityError, match="cannot read"):
        selector.FrozenEssentialWebSelector.load(tmp_path / "missing")


# --------------------------------------------------------------------------
# Authored edge fixtures: exact B-normal outcomes.
# --------------------------------------------------------------------------

EDGE_CASES: list[tuple[str, dict[str, Any], str]] = [
    ("s5 academic", {}, SCIENCE),
    ("s5 news article", {"d": "News Article", "k": "Factual"}, SCIENCE),
    ("s5 beats practical", {"f": "510", "d": "Tutorial", "k": "Procedural"}, SCIENCE),
    ("s5 beats prose", {"f": "599.5", "d": "Nonfiction Writing", "k": "Factual"}, SCIENCE),
    ("fdc 005.4 is 0xx, not 5xx", {"f": "005.4", "d": "Tutorial"}, PRACTICAL),
    ("s5 genre required", {"f": "510", "d": "Personal Blog"}, PROSE),
    ("s61 allowlisted prefix", {"f": "616.1", "d": "Knowledge Article"}, SCIENCE),
    ("s61 academic", {"f": "610", "d": "Academic Writing", "t": "Mostly Correct"}, SCIENCE),
    ("s61 needs conceptual", {"f": "616.1", "d": "Knowledge Article", "k": "Factual"}, PROSE),
    ("611 is not allowlisted", {"f": "611.0", "d": "Knowledge Article"}, PROSE),
    ("619 is not allowlisted", {"f": "619", "d": "Knowledge Article"}, PROSE),
    (
        "s61 needs highly or mostly correct",
        {"f": "616.1", "t": "Not Applicable/Indeterminate"},
        "unassigned",
    ),
    ("s61 genre required", {"f": "616.1", "d": "News Article"}, PROSE),
    ("practical explicit genre", {"f": "300", "d": "FAQ", "k": "Factual"}, PRACTICAL),
    ("practical customer support", {"f": "650.1", "d": "Customer Support"}, PRACTICAL),
    ("practical documentation", {"f": "004", "d": "Documentation"}, PRACTICAL),
    (
        "practical conditional beats prose",
        {"f": "300", "d": "Personal Blog", "k": "Procedural"},
        PRACTICAL,
    ),
    ("practical conditional forum", {"f": "300", "d": "Q&A Forum", "k": "Procedural"}, PRACTICAL),
    ("forum without procedural", {"f": "300", "d": "Q&A Forum", "k": "Factual"}, "unassigned"),
    ("prose blog", {"f": "300", "d": "Personal Blog"}, PROSE),
    ("prose creative", {"f": "813", "d": "Creative Writing", "k": "Factual"}, PROSE),
    ("creative procedural", {"f": "813", "d": "Creative Writing", "k": "Procedural"}, "unassigned"),
    ("academic outside science", {"f": "300", "d": "Academic Writing"}, "unassigned"),
    ("english at the threshold", {"e": 0.8}, SCIENCE),
    ("english just below", {"e": 0.7999}, "rejected"),
    ("integer english", {"e": 1}, SCIENCE),
    ("irrelevant content is D, not B", {"a": "Irrelevant Content"}, "rejected"),
    ("leftover html", {"a": "Leftover HTML"}, "rejected"),
    ("missing images allowed at normal", {"m": "Missing Images or Figures"}, SCIENCE),
    ("truncated snippets", {"m": "Truncated Snippets"}, "rejected"),
    ("indeterminate missing content", {"m": "Indeterminate"}, "rejected"),
    ("partially correct", {"t": "Partially Correct"}, "rejected"),
    ("technically flawed", {"t": "Technically Flawed"}, "rejected"),
    ("indeterminate correctness passes the gate", {"t": "Not Applicable/Indeterminate"}, SCIENCE),
    ("excluded doctype", {"d": "Product Page"}, "rejected"),
    ("invalid fdc syntax", {"f": "320.973/0207"}, "rejected"),
    ("numeric fdc is not a string", {"f": 510.2}, "rejected"),
    ("unknown doctype label", {"d": "Invented Genre"}, "rejected"),
    ("unknown artifact label", {"a": "Invented Artifact"}, "rejected"),
    ("english out of range", {"e": 1.2}, "rejected"),
    ("null label", {"k": None}, "rejected"),
]


@pytest.mark.parametrize(("name", "kwargs", "expected"), EDGE_CASES, ids=[c[0] for c in EDGE_CASES])
def test_edge_fixture_outcomes(
    name: str,
    kwargs: dict[str, Any],
    expected: str,
    spec: dict[str, Any],
    frozen: selector.FrozenEssentialWebSelector,
    adapters: dict[str, EssentialWebSelectedAdapter],
) -> None:
    record = _row(**kwargs)
    decision = frozen.decide(record)
    assert decision.final == expected == _reference_final(record, spec), name
    assert decision.admitted == (expected in selector.ADMITTED_COMPONENTS)
    admitting = [component for component, adapter in adapters.items() if _admits(adapter, record)]
    assert admitting == ([expected] if decision.admitted else [])


def _admits(adapter: EssentialWebSelectedAdapter, record: dict[str, Any]) -> bool:
    try:
        _adapt(adapter, record)
    except RecordRejectedError:
        return False
    return True


def test_rejection_stages_and_reason_codes(frozen: selector.FrozenEssentialWebSelector) -> None:
    gate = frozen.decide(_row(e=0.5, d="Spam / Ads"))
    assert (gate.final, gate.stage) == ("rejected", "gate")
    assert gate.reasons == ("gate_english", "gate_doctype")
    validity = frozen.decide(_row(f="320.973/0207"))
    assert (validity.stage, validity.reasons) == ("validity", ("invalid_fdc_syntax",))
    assigned = frozen.decide(_row())
    assert (assigned.stage, assigned.reasons) == ("component", ())


def test_grid_equals_the_frozen_evaluator_and_never_overlaps(
    spec: dict[str, Any], adapters: dict[str, EssentialWebSelectedAdapter]
) -> None:
    codes = ("510", "005.4", "610", "616.2", "611", "300", "650.1", "813")
    genres = (*spec["doctype_union"], "Product Page", "Listicle")
    grid = itertools.product(
        codes,
        genres,
        spec["knowledge_labels"],
        ("No Artifacts", "Irrelevant Content"),
        ("No missing content", "Missing Images or Figures", "Truncated Snippets"),
        (*spec["correctness_allowed"], *spec["correctness_rejected"]),
        (0.79, 0.8, 0.95),
    )
    seen: dict[str, int] = dict.fromkeys(selector.FINAL_COMPONENTS, 0)
    rows = 0
    for f, d, k, a, m, t, e in grid:
        record = _row(f=f, d=d, k=k, a=a, m=m, t=t, e=e)
        expected = _reference_final(record, spec)
        finals = {name: adapter.selector_final(record) for name, adapter in adapters.items()}
        assert set(finals.values()) == {expected}
        assert sum(finals[name] == name for name in finals) == (
            1 if expected in selector.ADMITTED_COMPONENTS else 0
        )
        seen[expected] += 1
        rows += 1
    assert rows == 8 * 13 * 3 * 2 * 3 * 5 * 3
    assert sum(seen.values()) == rows and all(count > 0 for count in seen.values())


# --------------------------------------------------------------------------
# Adapter: certified rendering, frozen admission, explicit rejections.
# --------------------------------------------------------------------------


def test_admitted_document_is_the_certified_rendering_plus_selector_identity(
    adapters: dict[str, EssentialWebSelectedAdapter],
) -> None:
    record = _row()
    before = copy.deepcopy(record)
    document = _adapt(adapters[SCIENCE], record, row=3)
    base = _adapt(EssentialWebAdapter(SCIENCE), record, row=3)
    assert record == before
    assert document.text == AUTHORED_TEXT == base.text
    assert document.doc_id == base.doc_id and document.raw_hash == base.raw_hash
    added = {
        "essential_web_selector": "B-normal",
        "essential_web_selector_policy_digest": selector.POLICY_DIGEST,
        "essential_web_selector_freeze_digest": selector.FREEZE_DIGEST,
    }
    assert document.source_metadata == {**base.source_metadata, **added}
    assert document.source_metadata["mix01_component"] == SCIENCE
    assert base.source_metadata.keys().isdisjoint(added)


def test_rejections_are_explicit_recordable_and_text_free(
    adapters: dict[str, EssentialWebSelectedAdapter],
) -> None:
    cases = [
        (adapters[PRACTICAL], _row(), EssentialWebSelectorOtherComponentError, SCIENCE),
        (adapters[PROSE], _row(), EssentialWebSelectorOtherComponentError, SCIENCE),
        (
            adapters[SCIENCE],
            _row(a="Irrelevant Content"),
            EssentialWebSelectorRejectedError,
            "gate_artifacts",
        ),
        (
            adapters[SCIENCE],
            _row(f="300", d="Academic Writing"),
            EssentialWebSelectorUnassignedError,
            "unassigned",
        ),
    ]
    codes = set()
    for adapter, record, error_type, fragment in cases:
        with pytest.raises(error_type) as caught:
            _adapt(adapter, record)
        message = str(caught.value)
        assert fragment in message and "B-normal" in message
        assert AUTHORED_TEXT not in message and "Irrelevant Content" not in message
        assert is_recordable_rejection(caught.value)
        codes.add(rejection_code(caught.value))
    assert codes == {
        "EssentialWebSelectorOtherComponentError",
        "EssentialWebSelectorRejectedError",
        "EssentialWebSelectorUnassignedError",
    }


def test_malformed_rows_are_recordable_even_when_the_selector_would_reject(
    adapters: dict[str, EssentialWebSelectedAdapter],
) -> None:
    rejected = _row(a="Irrelevant Content")
    del rejected["id"]
    with pytest.raises(EssentialWebMalformedRowError):
        _adapt(adapters[SCIENCE], rejected)
    empty_text = _row(d="Product Page")
    empty_text["text"] = ""
    with pytest.raises(EssentialWebMalformedRowError):
        _adapt(adapters[PROSE], empty_text)
    # The selector outcome is unchanged; the production wrapper records renderer faults.
    for kwargs in ({"f": "  "}, {"e": True}):
        malformed = _row(**kwargs)
        assert adapters[SCIENCE].selector_final(malformed) == "rejected"
        with pytest.raises(EssentialWebMalformedRowError):
            _adapt(adapters[SCIENCE], malformed)


def test_adapter_registration_and_configuration() -> None:
    assert ADAPTERS_BY_ID["essential_web_bnormal"] is EssentialWebSelectedAdapter
    assert ADAPTERS_BY_ID["essential_web"] is EssentialWebAdapter
    with pytest.raises(ValueError, match="explicit operator configuration"):
        EssentialWebSelectedAdapter("unassigned")
    with pytest.raises(TypeError):
        EssentialWebSelectedAdapter()  # type: ignore[call-arg]
    contract = EssentialWebSelectedAdapter(PROSE).contract()
    assert contract.adapter_id == "essential_web_bnormal" and contract.text_field == "text"
    for component in selector.ADMITTED_COMPONENTS:
        assert columns_for("essential_web_bnormal", component) == columns_for(
            "essential_web", component
        )
        assert columns_for(*parse_adapter_spec(f"essential_web_bnormal:{component}"))
    with pytest.raises(ValueError, match="explicit config"):
        columns_for("essential_web_bnormal")


# --------------------------------------------------------------------------
# data adapt CLI: the production path configured with the frozen selector.
# --------------------------------------------------------------------------


def _plan(tmp_path: Path) -> tuple[Path, str]:
    plan = AcquisitionPlan(
        plan_id="plan_essential_bnormal",
        source_id="essential_web",
        view_id=PRACTICAL,
        provider="https",
        repository="http://127.0.0.1:9/unused",
        revision=REVISION,
        mode=AcquisitionMode.SELECTED_RECORDS,
        selected_files=[SOURCE_FILE],
        row_ranges={SOURCE_FILE: (0, 6)},
        output_artifact_id="raw_essential_bnormal",
        is_pilot=True,
        limits=AcquisitionLimits(max_transferred_bytes=1024**2, max_records=100),
    )
    plan_path = tmp_path / "plan.json"
    save_acquisition_plan(plan, plan_path)
    return plan_path, load_acquisition_plan(plan_path).compute_behavioral_hash()


def test_data_adapt_three_passes_partition_the_same_raw_rows(tmp_path: Path) -> None:
    plan_path, selection_hash = _plan(tmp_path)
    rows = [
        _row(),
        _row(f="300", d="Tutorial"),
        _row(f="300", d="Personal Blog"),
        _row(f="300", d="FAQ"),
        _row(a="Irrelevant Content"),
        _row(f="300", d="Academic Writing"),
    ]
    lines = [
        json.dumps(
            {
                **row,
                "_xlm_acquisition": {
                    "source_id": "essential_web",
                    "repository": "http://127.0.0.1:9/unused",
                    "revision": REVISION,
                    "source_file": SOURCE_FILE,
                    "row_index": index,
                    "selection_hash": selection_hash,
                },
            }
        )
        for index, row in enumerate(rows)
    ]
    selected = tmp_path / "selected_records.jsonl"
    selected.write_text("\n".join(lines) + "\n", encoding="utf-8")
    admitted: dict[str, list[int]] = {}
    for component in selector.ADMITTED_COMPONENTS:
        out_dir = tmp_path / component
        result = CliRunner().invoke(
            data_app,
            [
                "adapt",
                "--plan",
                str(plan_path),
                "--adapter",
                "essential_web_bnormal",
                "--adapter-config",
                component,
                "--input",
                str(selected),
                "--output-dir",
                str(out_dir),
                "--on-reject",
                "record",
            ],
        )
        assert result.exit_code == 0, result.output
        documents = [
            json.loads(line)
            for line in (out_dir / "documents.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        assert all(d["source_metadata"]["mix01_component"] == component for d in documents)
        assert all(d["source_metadata"]["essential_web_selector"] == "B-normal" for d in documents)
        admitted[component] = [d["source_row"] for d in documents]
        summary = json.loads((out_dir / "adaptation_summary.json").read_text(encoding="utf-8"))
        assert summary["adapter_id"] == "essential_web_bnormal"
        assert summary["total_input_records"] == 6
        codes = summary["rejection_counts_by_code"]
        assert codes["EssentialWebSelectorRejectedError"] == 1
        assert codes["EssentialWebSelectorUnassignedError"] == 1
        assert codes["EssentialWebSelectorOtherComponentError"] == 4 - len(documents)
        ledger = (out_dir / "adaptation_rejections.jsonl").read_text(encoding="utf-8")
        assert AUTHORED_TEXT not in ledger
    assert admitted == {SCIENCE: [0], PRACTICAL: [1, 3], PROSE: [2]}

    refused = CliRunner().invoke(
        data_app,
        [
            "adapt",
            "--plan",
            str(plan_path),
            "--adapter",
            "essential_web_bnormal",
            "--input",
            str(selected),
            "--output-dir",
            str(tmp_path / "no-config"),
        ],
    )
    assert refused.exit_code == 1 and "needs constructor parameters" in refused.output


# --------------------------------------------------------------------------
# Registry and mixture: Essential views bound, nothing else moved.
# --------------------------------------------------------------------------


def test_registry_binds_the_frozen_selector_and_leaves_the_mixture_alone() -> None:
    registry = load_mix01_views(VIEWS_PATH)
    identity = selector.selector_identity()
    for view in production.essential_views(registry):
        assert view.adapter_id == "essential_web_bnormal"
        assert view.observed_revision == selector.SOURCE_REVISION
        bound = view.upstream_selector["frozen_selector"]
        assert {key: bound[key] for key in identity} == identity
        assert bound["precedence"] == ["science", "practical", "prose"]
        assert bound["t_semantic_review"] == "NOT_RUN_NO_TWO_INDEPENDENT_HUMAN_REVIEWERS"
        assert bound["policy_spec"] == selector.POLICY_RELATIVE_PATH
        assert bound["evaluator"] == selector.EVALUATOR_RELATIVE_PATH
    others = {
        v.component_id: v.adapter_id for v in registry.views if v.source_id != "essential_web"
    }
    assert others == {
        "ultrax_ultrafineweb": "ultrax_ultrafineweb",
        "finepdfs_en": "finepdfs_en",
        "synth_en_explanations": "synth_en",
        "nemotron_wiki_rewrite": "wiki_rewrite",
        "finewiki_en": "finewiki_en",
        "ifm_behaviors_general_planning": "ifm_general",
        "common_pile_prose": "common_pile",
        "simple_stories": "simple_stories",
        "txt360_web": "txt360_web",
    }
    weights = yaml.safe_load(PRESET_PATH.read_text(encoding="utf-8"))["weights"]
    assert weights == {
        "essential_science": 0.1,
        "essential_practical": 0.1,
        "essential_prose": 0.05,
        "ultrax_ultrafineweb": 0.2,
        "finepdfs_en": 0.15,
        "synth_en_explanations": 0.15,
        "nemotron_wiki_rewrite": 0.08,
        "finewiki_en": 0.05,
        "ifm_behaviors_general_planning": 0.05,
        "common_pile_prose": 0.05,
        "simple_stories": 0.02,
    }
    freeze = fasttrack_freeze.load_freeze(FREEZE_PATH)
    for name in ("preset", "quotas"):
        bound_file = freeze["mixture"][name]
        raw = (REPO_ROOT / bound_file["path"]).read_bytes()
        assert len(raw) == bound_file["bytes"]


# --------------------------------------------------------------------------
# Replicate reproduction, dry plan and readiness.
# --------------------------------------------------------------------------


def _replicate(spec: dict[str, Any]) -> tuple[bytes, dict[str, str], dict[str, int], Any]:
    """Authored 8 x 512 metadata replicate with reference counts."""
    genres = list(spec["doctype_union"]) + ["Product Page"]
    codes = ("510", "616.2", "300", "813", "005.4")
    crawls = [f"crawl=AUTHORED-{index}" for index in range(8)]
    crawl_of = {f"data/{crawl}/train.parquet": crawl for crawl in crawls}
    total = dict.fromkeys(selector.FINAL_COMPONENTS, 0)
    per_crawl = {crawl: dict.fromkeys(selector.FINAL_COMPONENTS, 0) for crawl in crawls}
    lines = []
    for number in range(production.REPLICATE_ROWS):
        source_file = f"data/{crawls[number // 512]}/train.parquet"
        record = _row(
            f=codes[number % len(codes)],
            d=genres[number % len(genres)],
            k=spec["knowledge_labels"][number % 3],
            a=("No Artifacts", "No Artifacts", "Irrelevant Content")[number % 3],
            e=(0.95, 0.85, 0.6)[(number // 7) % 3],
        )
        metadata = {key: record[key] for key in ("eai_taxonomy", "quality_signals")}
        final = _reference_final(metadata, spec)
        total[final] += 1
        per_crawl[crawls[number // 512]][final] += 1
        metadata["_xlm_acquisition"] = {
            "source_file": source_file,
            "row_index": number,
            "revision": REVISION,
        }
        lines.append(json.dumps(metadata))
    return ("\n".join(lines) + "\n").encode("utf-8"), crawl_of, total, per_crawl


def test_reproduction_matches_reference_counts_and_detects_drift(
    spec: dict[str, Any], adapters: dict[str, EssentialWebSelectedAdapter]
) -> None:
    payload, crawl_of, total, per_crawl = _replicate(spec)
    passes = list(adapters.values())
    result = production.reproduce_b_normal(payload, crawl_of, passes, total, per_crawl)
    assert result["match"] is True and result["rows"] == 4096 == result["sum"]
    assert result["final"] == total and result["per_crawl"] == per_crawl
    assert result["rows_admitted_by_more_than_one_component"] == 0
    assert result["canonical_rendering_exercised"] is False
    assert all(total[name] > 0 for name in selector.FINAL_COMPONENTS)

    wrong = {**total, SCIENCE: total[SCIENCE] + 1, "rejected": total["rejected"] - 1}
    drifted = production.reproduce_b_normal(payload, crawl_of, passes, wrong, per_crawl)
    assert drifted["match"] is False and drifted["mismatches"][0].startswith("all:")

    short = production.reproduce_b_normal(
        b"\n".join(payload.splitlines()[:4095]) + b"\n", crawl_of, passes, total, per_crawl
    )
    assert short["match"] is False and short["conserved"] is False


def test_reproduction_refuses_text_duplicates_and_foreign_rows(
    spec: dict[str, Any], adapters: dict[str, EssentialWebSelectedAdapter]
) -> None:
    payload, crawl_of, total, per_crawl = _replicate(spec)
    passes = list(adapters.values())
    lines = payload.splitlines()

    def run(changed: list[bytes], count: int = 3) -> Any:
        return production.reproduce_b_normal(
            b"\n".join(changed) + b"\n", crawl_of, passes[:count], total, per_crawl
        )

    with_text = json.loads(lines[0])
    with_text["text"] = AUTHORED_TEXT
    with pytest.raises(production.ProductionCheckError, match="text content refused"):
        run([json.dumps(with_text).encode("utf-8"), *lines[1:]])
    with pytest.raises(production.ProductionCheckError, match="duplicate row identity"):
        run([lines[0], lines[0], *lines[2:]])
    foreign = json.loads(lines[0])
    foreign["_xlm_acquisition"]["source_file"] = "data/other/train.parquet"
    with pytest.raises(production.ProductionCheckError, match="outside the bound replicate"):
        run([json.dumps(foreign).encode("utf-8"), *lines[1:]])
    unpinned = json.loads(lines[0])
    unpinned["_xlm_acquisition"]["revision"] = "00" * 20
    with pytest.raises(production.ProductionCheckError, match="revision is not the pin"):
        run([json.dumps(unpinned).encode("utf-8"), *lines[1:]])
    with pytest.raises(production.ProductionCheckError, match="one adapter per component"):
        run(lines, count=2)


def test_dry_plan_binds_units_without_running_or_sizing_anything() -> None:
    freeze = fasttrack_freeze.load_freeze(FREEZE_PATH)
    plan = production.build_dry_plan(
        freeze=freeze,
        registry=load_mix01_views(VIEWS_PATH),
        weights=yaml.safe_load(PRESET_PATH.read_text(encoding="utf-8"))["weights"],
        quotas=yaml.safe_load(QUOTAS_PATH.read_text(encoding="utf-8")),
        limits={"pilot_max_records": 25000},
    )
    assert plan["status"].startswith("DRY") and plan["mixture"]["weights_changed"] is False
    assert plan["selector"] == selector.selector_identity()
    assert plan["t_semantic_review"] == "NOT_RUN_NO_TWO_INDEPENDENT_HUMAN_REVIEWERS"
    units = {unit["component"]: unit for unit in plan["units"]}
    assert list(units) == list(selector.ADMITTED_COMPONENTS)
    assert [units[name]["mix01_weight"] for name in units] == [0.1, 0.1, 0.05]
    assert units[SCIENCE]["final_quota_tokens"] == 600_000_000
    assert units[PROSE]["first_pass_quota_tokens"] == 330_000_000
    assert units[PRACTICAL]["adapter_spec"] == "essential_web_bnormal:essential_practical"
    science = units[SCIENCE]["observed_admission"]
    assert science["development"]["admitted_rows"] == 29
    assert science["m"]["admitted_rows"] == 24
    assert science["both_replicates"] == {
        "admitted_rows": 53,
        "of_rows": 8192,
        "share_percent": 0.647,
    }
    assert units[PRACTICAL]["observed_admission"]["m"]["admitted_rows"] == 117
    assert units[PROSE]["observed_admission"]["m"]["admitted_rows"] == 372
    assert {stage["status"] for stage in plan["stages"]} == {"NOT RUN"}
    assert [stage["stage"] for stage in plan["stages"]] == [
        "probe",
        "inventory",
        "calibrate",
        "plan",
        "admit",
        "fetch_verify",
        "adapt",
    ]
    for unit in units.values():
        assert unit["tokens_per_admitted_row"].startswith("UNKNOWN")
        assert unit["transferred_bytes_per_scanned_row"].startswith("UNKNOWN")


def _store(tmp_path: Path, *, outcome: str = "accessible", adapter: str | None = None) -> Path:
    """Authored operator store; ``adapter=None`` writes no admission decision."""
    home = tmp_path / "xlm-home"
    for component in selector.ADMITTED_COMPONENTS:
        accessible = outcome == "accessible"
        evidence = {
            "source_id": "essential_web",
            "view_id": component,
            "provider": "huggingface",
            "repository": production.REPOSITORY,
            "immutable_revision": REVISION if accessible else None,
            "outcome": outcome,
            "evidence_type": "real_observed",
            "probe_fingerprint": "authored-fingerprint" if accessible else None,
            "verified_schema": {"view_id": component} if accessible else None,
            "declared_license": "odc-by" if accessible else None,
        }
        evidence_dir = home / "probe_evidence" / f"probe_essential_web_{component}"
        evidence_dir.mkdir(parents=True)
        (evidence_dir / "probe_evidence.json").write_text(json.dumps(evidence), encoding="utf-8")
        if adapter is None:
            continue
        from xlm.data.sources.essential_web_bootstrap import REVIEW_FILES, build_decision
        from xlm.data.sources.prober import ProbeEvidenceRecord

        decision = build_decision(
            ProbeEvidenceRecord.model_validate(evidence),
            dict.fromkeys(REVIEW_FILES, "0" * 64),
            "authored operator",
        ).model_dump(mode="json")
        decision["adapter_id"] = adapter
        decision_dir = home / "admission_decision" / f"admission_essential_web_{component}"
        decision_dir.mkdir(parents=True)
        (decision_dir / "admission_decision.json").write_text(
            json.dumps(decision), encoding="utf-8"
        )
    return home


def _readiness(**overrides: Any) -> dict[str, Any]:
    arguments: dict[str, Any] = {
        "freeze": fasttrack_freeze.load_freeze(FREEZE_PATH),
        "registry": load_mix01_views(VIEWS_PATH),
        "reproductions": {"development": {"match": True}, "m": {"match": True}},
        "xlm_home": None,
        "inventory_path": None,
        "calibration_path": None,
    }
    arguments.update(overrides)
    return production.evaluate_readiness(**arguments)


def test_readiness_without_operator_evidence_blocks_bulk_acquisition() -> None:
    readiness = _readiness()
    assert readiness["vector"] == {
        "selector_frozen": True,
        "selector_integration_ok": True,
        "source_revision_ok": True,
        "production_admission_ok": False,
        "inventory_ready": False,
        "acquisition_plan_ready": False,
    }
    assert readiness["ready_for_bulk_acquisition"] is False
    assert set(readiness["reasons"]) == {
        "production_admission_ok",
        "inventory_ready",
        "acquisition_plan_ready",
    }


def test_readiness_needs_matching_reproductions() -> None:
    missing = _readiness(reproductions={"development": None, "m": {"match": True}})
    assert missing["vector"]["selector_integration_ok"] is False
    failed = _readiness(reproductions={"development": {"match": True}, "m": {"match": False}})
    assert failed["vector"]["selector_integration_ok"] is False


def test_readiness_admission_inventory_and_calibration(tmp_path: Path) -> None:
    exhausted = _readiness(xlm_home=_store(tmp_path / "a", outcome="budget_exhausted"))
    assert exhausted["vector"]["production_admission_ok"] is False
    assert "budget_exhausted" in exhausted["reasons"]["production_admission_ok"][0]

    undecided = _readiness(xlm_home=_store(tmp_path / "b"))
    assert undecided["vector"]["production_admission_ok"] is False
    assert "No operator admission decision" in undecided["reasons"]["production_admission_ok"][0]

    legacy = _readiness(xlm_home=_store(tmp_path / "c", adapter="essential_web"))
    assert legacy["vector"]["production_admission_ok"] is False
    assert "requires essential_web_bnormal" in legacy["reasons"]["production_admission_ok"][0]

    inventory = tmp_path / "essential_web.inventory.json"
    inventory.write_text(
        json.dumps(
            {
                "source_id": "essential_web",
                "repository": production.REPOSITORY,
                "revision": REVISION,
                "file_count": 8,
                "inventory_digest": "ab" * 32,
            }
        ),
        encoding="utf-8",
    )
    calibration = tmp_path / "calibration.json"
    calibration.write_text(
        json.dumps(
            {"sources": {name: {"accepted_records": 9} for name in selector.ADMITTED_COMPONENTS}}
        ),
        encoding="utf-8",
    )
    ready = _readiness(
        xlm_home=_store(tmp_path / "d", adapter="essential_web_bnormal"),
        inventory_path=inventory,
        calibration_path=calibration,
    )
    assert ready["vector"] == dict.fromkeys(production.READINESS_KEYS, True)
    assert ready["ready_for_bulk_acquisition"] is True and ready["reasons"] == {}

    stale = tmp_path / "stale.inventory.json"
    stale.write_text(
        inventory.read_text(encoding="utf-8").replace(REVISION, "00" * 20), encoding="utf-8"
    )
    unpinned = _readiness(inventory_path=stale, calibration_path=calibration)
    assert unpinned["vector"]["inventory_ready"] is False
    assert unpinned["vector"]["acquisition_plan_ready"] is False
