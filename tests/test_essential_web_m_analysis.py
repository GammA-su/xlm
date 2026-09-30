"""Essential-Web Arm-M analysis: adapter, frozen evaluator, comparison, seal.

Every record and receipt here is authored and synthetic (``synthetic:
true``); nothing reads the real Phase-D or development evidence roots, no
test touches the network (sockets are blocked), and no Arm-T material
exists in any fixture. Real-evidence execution is recorded separately in
the M analysis report.
"""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import socket
import sys
from pathlib import Path
from typing import Any

import pytest

from xlm.data.evidence_v2 import canonical, m_analysis, m_input_adapter

_ROOT = Path(__file__).resolve().parents[1]
_CLI_PATH = _ROOT / "scripts" / "essential_web_m_analysis.py"
_SPEC = importlib.util.spec_from_file_location("essential_web_m_analysis", _CLI_PATH)
assert _SPEC is not None and _SPEC.loader is not None
cli = importlib.util.module_from_spec(_SPEC)
sys.modules["essential_web_m_analysis"] = cli
_SPEC.loader.exec_module(cli)

PREPARATION_PATH = (
    _ROOT
    / "docs"
    / "implementation"
    / "evidence"
    / "ESSENTIAL-WEB-EVIDENCE-V4.1-PHASE-D-RESULT-REVIEW"
    / "m-analysis-preparation.json"
)
REV = "ab" * 20
REPO = "test/repo"
POLICY_DIGEST = "f4357f61f434d5105d823266153dac6372e122f12767187631e8796b4899dd07"
FILES = [f"data/crawl=CC-TEST-{i}/train-0000{i}.parquet" for i in range(8)]
CRAWLS = [f"crawl=CC-TEST-{i}" for i in range(8)]
GROUP_FIRST = 1000


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_: Any, **__: Any) -> None:
        raise AssertionError("network access attempted in an offline test")

    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _meta(
    f: str, d: str, k: str, e: float, a: str = "No Artifacts", m: str = "No missing content"
) -> dict[str, Any]:
    return {
        "eai_taxonomy": {
            "free_decimal_correspondence": {"primary": {"code": f}},
            "document_type_v2": {"primary": {"label": d}},
            "bloom_knowledge_domain": {"primary": {"label": k}},
            "extraction_artifacts": {"primary": {"label": a}},
            "missing_content": {"primary": {"label": m}},
            "technical_correctness": {"primary": {"label": "Highly Correct"}},
        },
        "quality_signals": {
            "fasttext": {"english": e},
            "red_pajama_v2": {"rps_doc_word_count": 100},
        },
    }


# Eight authored rows, repeated 64 times per file. Expected B-normal finals:
# science 2 (S5, S61), practical 1, prose 2, unassigned 1, rejected 2.
_POOL = [
    _meta("510.2", "Academic Writing", "Conceptual", 0.95),
    _meta("613.5", "Knowledge Article", "Conceptual", 0.95),
    _meta("005.4", "Tutorial", "Procedural", 0.85),
    _meta("940.0", "News Article", "Factual", 0.95),
    _meta("320.0", "Personal Blog", "Factual", 0.95, m="Missing Images or Figures"),
    _meta("330.0", "Q&A Forum", "Factual", 0.95),
    _meta("790.0", "News Article", "Factual", 0.95, a="Irrelevant Content"),
    _meta("650.0", "Product Page", "Factual", 0.95),
]
# A second pool with more rejections, for a nonzero comparison.
_POOL_ALT = [*_POOL[:4], _POOL[7], _POOL[7], _POOL[6], _POOL[7]]


def _start(ordinal: int) -> int:
    return GROUP_FIRST + 7 + ordinal


def _source_lines(pool: list[dict[str, Any]]) -> list[bytes]:
    lines: list[bytes] = []
    for ordinal, name in enumerate(FILES):
        for offset in range(m_input_adapter.ROWS_PER_FILE):
            row = _start(ordinal) + offset
            record = copy.deepcopy(pool[offset % len(pool)])
            record["_xlm_acquisition"] = {
                "m_ordinal": ordinal,
                "repository": REPO,
                "revision": REV,
                "row": row,
                "row_group": 3,
                "row_in_group": row - GROUP_FIRST,
                "source_file": name,
            }
            lines.append(canonical.canonical_bytes(record) + b"\n")
    return lines


def _write_world(root: Path, lines: list[bytes]) -> tuple[dict[str, Path], dict[str, Any]]:
    """Write a synthetic Phase-D M world whose bindings match ``lines``."""
    root.mkdir(parents=True, exist_ok=True)
    payload = b"".join(lines)
    per = m_input_adapter.ROWS_PER_FILE
    manifest: dict[str, Any] = {
        "kind": m_input_adapter.M_MANIFEST_KIND,
        "status": "COMPLETE",
        "synthetic": True,
        "locator_field": "_xlm_acquisition",
        "projection": ["eai_taxonomy", "quality_signals"],
        "policy_digest": POLICY_DIGEST,
        "selection_digest": "5e" * 32,
        "scientific_namespace": "synthetic-namespace",
        "plan_digest": "9d" * 32,
        "records": m_input_adapter.FROZEN_RECORDS,
        "source": {"repository": REPO, "revision": REV},
        "output": {m_input_adapter.M_OUTPUT_NAME: {"bytes": len(payload), "sha256": _sha(payload)}},
        "files": [
            {
                "file": name,
                "ordinal": ordinal,
                "records": per,
                "records_sha256": _sha(b"".join(lines[ordinal * per : (ordinal + 1) * per])),
                "row_group": 3,
                "row_group_first_row": GROUP_FIRST,
                "row_group_rows": 5000,
                "window": [_start(ordinal), _start(ordinal) + per],
            }
            for ordinal, name in enumerate(FILES)
        ],
    }
    manifest["digest"] = canonical.self_digest(manifest)
    manifest_raw = canonical.canonical_bytes(manifest)
    receipt: dict[str, Any] = {
        "kind": m_input_adapter.RECEIPT_KIND,
        "status": "COMPLETE",
        "run_status": "COMPLETE",
        "arms": {"M": {"status": "COMPLETE"}},
        "policy_digest": POLICY_DIGEST,
        "selection_digest": manifest["selection_digest"],
        "scientific_namespace": manifest["scientific_namespace"],
        "plan_digest": manifest["plan_digest"],
        "outputs": {
            m_input_adapter.M_OUTPUT_NAME: {
                "arm": "M",
                "bytes": len(payload),
                "sha256": _sha(payload),
            },
            m_input_adapter.M_MANIFEST_NAME: {
                "arm": "M",
                "bytes": len(manifest_raw),
                "sha256": _sha(manifest_raw),
            },
        },
    }
    receipt["digest"] = canonical.self_digest(receipt)
    paths = {
        "records": root / m_input_adapter.M_OUTPUT_NAME,
        "manifest": root / m_input_adapter.M_MANIFEST_NAME,
        "receipt": root / "phase_d_receipt.json",
    }
    paths["records"].write_bytes(payload)
    paths["manifest"].write_bytes(manifest_raw)
    paths["receipt"].write_bytes(canonical.canonical_bytes(receipt))
    preparation: dict[str, Any] = {
        "arm": "M",
        "seal_before_T_unblinding": True,
        "analysis_caps": {
            "max_input_bytes": 33554432,
            "max_line_bytes": 1048576,
            "max_output_bytes": 67108864,
            "max_records": 5000,
            "max_runtime_seconds": 120,
            "max_scratch_bytes": 67108864,
        },
        "input": {
            "bytes": len(payload),
            "sha256": _sha(payload),
            "records": m_input_adapter.FROZEN_RECORDS,
            "projection": ["eai_taxonomy", "quality_signals"],
            "manifest": {"bytes": len(manifest_raw), "sha256": _sha(manifest_raw)},
        },
        "policy_digest": POLICY_DIGEST,
        "selection_digest": manifest["selection_digest"],
        "scientific_namespace": manifest["scientific_namespace"],
        "phase_d_plan_digest": manifest["plan_digest"],
        "windows": [
            {"file": name, "records": per, "start": _start(i), "stop": _start(i) + per}
            for i, name in enumerate(FILES)
        ],
    }
    return paths, preparation


def _adapt(root: Path, lines: list[bytes]) -> m_input_adapter.AdaptedInput:
    paths, preparation = _write_world(root, lines)
    return m_input_adapter.adapt_m_input(
        paths["records"], paths["manifest"], paths["receipt"], preparation
    )


def _mutate(line: bytes, **locator: Any) -> bytes:
    record = json.loads(line)
    record["_xlm_acquisition"].update(locator)
    return canonical.canonical_bytes(record) + b"\n"


@pytest.fixture(scope="module")
def evaluator() -> Any:
    return cli.load_evaluator()


@pytest.fixture(scope="module")
def spec(evaluator: Any) -> dict[str, Any]:
    loaded, digest = evaluator.load_policy_spec(cli.POLICY_PATH)
    assert digest == POLICY_DIGEST
    assert isinstance(loaded, dict)
    return loaded


@pytest.fixture(scope="module")
def lines() -> list[bytes]:
    return _source_lines(_POOL)


@pytest.fixture(scope="module")
def adapted(tmp_path_factory: pytest.TempPathFactory, lines: list[bytes]) -> Any:
    return _adapt(tmp_path_factory.mktemp("world"), lines)


def _evaluate(
    evaluator: Any, spec: dict[str, Any], adapted: Any, directory: Path
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Adapter output through the frozen run_sweep plus the kernel pass."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "derived.jsonl"
    path.write_bytes(adapted.payload)
    binding = m_input_adapter.evaluator_binding(adapted.manifest)
    payloads = evaluator.run_sweep(path, binding, spec, spec["caps"])["payloads"]
    kernel = m_analysis.kernel_tables(evaluator, adapted.payload, spec, binding)
    return payloads, kernel


@pytest.fixture(scope="module")
def evaluated(
    tmp_path_factory: pytest.TempPathFactory, evaluator: Any, spec: dict[str, Any], adapted: Any
) -> tuple[dict[str, Any], dict[str, Any]]:
    return _evaluate(evaluator, spec, adapted, tmp_path_factory.mktemp("eval"))


# --------------------------------------------------------------------------
# Adapter.
# --------------------------------------------------------------------------


def test_adapter_preserves_count_order_and_row_index(lines: list[bytes], adapted: Any) -> None:
    out = adapted.payload.splitlines(keepends=True)
    assert len(out) == 4096 == adapted.manifest["records"]
    assert adapted.manifest["kind"] == "derived_analysis_input"
    for index, (source, derived) in enumerate(zip(lines, out, strict=True)):
        before, after = json.loads(source), json.loads(derived)
        locator = after["_xlm_acquisition"]
        expected_row = _start(index // 512) + index % 512
        assert locator["row_index"] == locator["row"] == expected_row
        assert locator["analysis_input_kind"] == "derived_analysis_input"
        assert locator["source_file"] == FILES[index // 512]
        kept = {k: v for k, v in locator.items() if k not in ("row_index", "analysis_input_kind")}
        assert kept == before["_xlm_acquisition"]
        assert set(after) == set(before)


def test_adapter_metadata_unchanged(lines: list[bytes], adapted: Any) -> None:
    source_digest = m_input_adapter.metadata_digest_of(b"".join(lines))
    assert m_input_adapter.metadata_digest_of(adapted.payload) == source_digest
    assert adapted.manifest["metadata_digest"] == source_digest
    for source, derived in zip(lines[:600], adapted.payload.splitlines()[:600], strict=True):
        before, after = json.loads(source), json.loads(derived)
        for field in ("eai_taxonomy", "quality_signals"):
            assert canonical.canonical_bytes(after[field]) == canonical.canonical_bytes(
                before[field]
            )


def test_adapter_manifest_binds_source_code_and_output(lines: list[bytes], adapted: Any) -> None:
    manifest = adapted.manifest
    assert manifest["source"]["records"]["sha256"] == _sha(b"".join(lines))
    assert manifest["output"] == {
        "bytes": len(adapted.payload),
        "sha256": _sha(adapted.payload),
    }
    code = (_ROOT / "src/xlm/data/evidence_v2/m_input_adapter.py").read_bytes()
    assert manifest["adapter"]["code_sha256"] == _sha(code)
    assert manifest["adapter"]["version"] == m_input_adapter.ADAPTER_VERSION
    assert canonical.self_digest(manifest) == manifest["digest"]
    assert len(manifest["row_identity_digest"]) == 64
    assert [f["records"] for f in manifest["files"]] == [512] * 8
    assert [f["crawl"] for f in manifest["files"]] == CRAWLS


def test_adapter_invents_no_legacy_receipts(adapted: Any, evaluator: Any, tmp_path: Path) -> None:
    text = json.dumps(adapted.manifest)
    for invented in ("bundle_digest", "execution_digest", "plan_hash", "acquisition_id"):
        assert invented not in text
    binding = m_input_adapter.evaluator_binding(adapted.manifest)
    assert set(binding["bundle"]) == {"kind", "revision", "combined_sha256", "combined_bytes"}
    assert binding["bundle"]["kind"] == "derived_analysis_input"
    assert all("plan_hash" not in part for part in binding["files"].values())
    # The frozen legacy binding loader must refuse the derived view.
    bundle, execution = tmp_path / "bundle.json", tmp_path / "execution.json"
    bundle.write_text(json.dumps(binding["bundle"]), encoding="utf-8")
    execution.write_text(json.dumps({"repository": REPO}), encoding="utf-8")
    with pytest.raises(evaluator.SweepError):
        evaluator.load_binding(
            bundle,
            execution,
            {"records": 4096, "revision": REV, "projection": ["eai_taxonomy", "quality_signals"]},
        )


def test_adapter_refuses_duplicate(tmp_path: Path, lines: list[bytes]) -> None:
    bad = list(lines)
    bad[5] = bad[4]
    with pytest.raises(m_input_adapter.AdapterError, match="order drift|duplicate"):
        _adapt(tmp_path, bad)


def test_adapter_refuses_missing_record_and_missing_identity(
    tmp_path: Path, lines: list[bytes]
) -> None:
    with pytest.raises(m_input_adapter.AdapterError, match="count 4095 != 4096"):
        _adapt(tmp_path / "short", lines[:-1])
    record = json.loads(lines[9])
    del record["_xlm_acquisition"]["row"]
    bad = list(lines)
    bad[9] = canonical.canonical_bytes(record) + b"\n"
    with pytest.raises(m_input_adapter.AdapterError, match="missing row identity"):
        _adapt(tmp_path / "norow", bad)


def test_adapter_refuses_extra_record(tmp_path: Path, lines: list[bytes]) -> None:
    with pytest.raises(m_input_adapter.AdapterError, match="count 4097 != 4096"):
        _adapt(tmp_path, [*lines, lines[-1]])


def test_adapter_refuses_order_drift_and_foreign_row(tmp_path: Path, lines: list[bytes]) -> None:
    swapped = list(lines)
    swapped[10], swapped[11] = swapped[11], swapped[10]
    with pytest.raises(m_input_adapter.AdapterError, match="order drift"):
        _adapt(tmp_path / "swap", swapped)
    foreign = list(lines)
    foreign[3] = _mutate(foreign[3], source_file=FILES[5])
    with pytest.raises(m_input_adapter.AdapterError, match="foreign row"):
        _adapt(tmp_path / "foreign", foreign)
    drifted = list(lines)
    drifted[3] = _mutate(drifted[3], row_in_group=0)
    with pytest.raises(m_input_adapter.AdapterError, match="provenance disagrees"):
        _adapt(tmp_path / "group", drifted)


def test_adapter_refuses_source_hash_mismatch(tmp_path: Path, lines: list[bytes]) -> None:
    paths, preparation = _write_world(tmp_path, lines)
    raw = paths["records"].read_bytes()
    paths["records"].write_bytes(raw.replace(b"0.95", b"0.96", 1))
    with pytest.raises(m_input_adapter.AdapterError, match="source hash mismatch"):
        m_input_adapter.adapt_m_input(
            paths["records"], paths["manifest"], paths["receipt"], preparation
        )
    paths["records"].write_bytes(raw)
    stale = copy.deepcopy(preparation)
    stale["input"]["manifest"]["sha256"] = "0" * 64
    with pytest.raises(m_input_adapter.AdapterError, match="source hash mismatch"):
        m_input_adapter.adapt_m_input(paths["records"], paths["manifest"], paths["receipt"], stale)


def test_adapter_refuses_unsupported_shape(tmp_path: Path, lines: list[bytes]) -> None:
    extra = json.loads(lines[0])
    extra["text"] = "authored synthetic body"
    bad = list(lines)
    bad[0] = canonical.canonical_bytes(extra) + b"\n"
    with pytest.raises(m_input_adapter.AdapterError, match="unsupported input shape"):
        _adapt(tmp_path / "extra", bad)
    spaced = list(lines)
    spaced[0] = json.dumps(json.loads(lines[0])).encode("utf-8") + b"\n"
    with pytest.raises(m_input_adapter.AdapterError, match="not canonical JSON"):
        _adapt(tmp_path / "spaced", spaced)
    legacy = list(lines)
    legacy[0] = _mutate(legacy[0], row_index=_start(0))
    with pytest.raises(m_input_adapter.AdapterError, match="locator fields"):
        _adapt(tmp_path / "legacy", legacy)


def test_adapter_refuses_incomplete_receipt(tmp_path: Path, lines: list[bytes]) -> None:
    paths, preparation = _write_world(tmp_path, lines)
    receipt = json.loads(paths["receipt"].read_bytes())
    receipt["arms"]["M"]["status"] = "INCOMPLETE"
    receipt["digest"] = canonical.self_digest(receipt)
    paths["receipt"].write_bytes(canonical.canonical_bytes(receipt))
    with pytest.raises(m_input_adapter.AdapterError, match="arm M is not COMPLETE"):
        m_input_adapter.adapt_m_input(
            paths["records"], paths["manifest"], paths["receipt"], preparation
        )


# --------------------------------------------------------------------------
# Frozen evaluator and policy.
# --------------------------------------------------------------------------


def _real_preparation() -> dict[str, Any]:
    preparation, binding = m_analysis.load_preparation(PREPARATION_PATH)
    assert binding["digest"] == m_analysis.PREPARATION_DIGEST
    return preparation


def test_frozen_evaluator_and_policy_unmutated(evaluator: Any) -> None:
    preparation = _real_preparation()
    spec, binding = m_analysis.verify_evaluator(
        cli.EVALUATOR_PATH, cli.POLICY_PATH, preparation, evaluator
    )
    assert binding["sha256"] == preparation["frozen_evaluator"]["git_blob_sha256"]
    assert binding["policy"]["digest"] == POLICY_DIGEST == preparation["policy_digest"]
    assert dict(spec["caps"]) == preparation["analysis_caps"]
    assert m_analysis.COMBOS == (
        "A-normal",
        "A-strict",
        "B-normal",
        "B-strict",
        "C-normal",
        "C-strict",
        "D-normal",
        "D-strict",
    )


def test_mutated_evaluator_or_policy_refused(evaluator: Any, tmp_path: Path) -> None:
    preparation = _real_preparation()
    tampered = tmp_path / "evaluator.py"
    tampered.write_bytes(cli.EVALUATOR_PATH.read_bytes() + b"\n# edit\n")
    with pytest.raises(m_analysis.AnalysisError, match="frozen evaluator hash"):
        m_analysis.verify_evaluator(tampered, cli.POLICY_PATH, preparation, evaluator)
    policy = tmp_path / "policy.yaml"
    policy.write_text(
        cli.POLICY_PATH.read_text(encoding="utf-8").replace(
            "english_min: 0.9", "english_min: 0.91"
        ),
        encoding="utf-8",
    )
    with pytest.raises(m_analysis.AnalysisError, match="policy spec digest"):
        m_analysis.verify_evaluator(cli.EVALUATOR_PATH, policy, preparation, evaluator)


def test_preparation_digest_is_pinned(tmp_path: Path) -> None:
    body = json.loads(PREPARATION_PATH.read_bytes())
    body["policy_digest"] = "0" * 64
    body["digest"] = canonical.self_digest(body)
    path = tmp_path / "preparation.json"
    path.write_bytes(canonical.canonical_bytes(body))
    with pytest.raises(m_analysis.AnalysisError, match="not the frozen M preparation"):
        m_analysis.load_preparation(path)


def _legacy_world(root: Path, lines: list[bytes]) -> dict[str, Path]:
    """The same rows in the evaluator's historical bundle/execution contract."""
    root.mkdir(parents=True, exist_ok=True)
    legacy: list[bytes] = []
    for raw in lines:
        record = json.loads(raw)
        source = record["_xlm_acquisition"]
        record["_xlm_acquisition"] = {
            "source_file": source["source_file"],
            "row_index": source["row"],
            "revision": REV,
            "repository": REPO,
        }
        legacy.append(json.dumps(record, ensure_ascii=False).encode("utf-8") + b"\n")
    payload = b"".join(legacy)
    execution: dict[str, Any] = {
        "kind": "execution",
        "revision": REV,
        "repository": REPO,
        "projection": ["eai_taxonomy", "quality_signals"],
    }
    execution["digest"] = canonical.self_digest(execution)
    bundle: dict[str, Any] = {
        "kind": "bundle",
        "execution_digest": execution["digest"],
        "revision": REV,
        "projection": ["eai_taxonomy", "quality_signals"],
        "parts": [
            {
                "unit": f"{i:02d}",
                "stratum": i,
                "crawl": CRAWLS[i],
                "file": name,
                "row_range": [_start(i), _start(i) + 512],
                "plan_hash": _sha(f"plan{i}".encode()),
                "records": 512,
            }
            for i, name in enumerate(FILES)
        ],
        "total_records": 4096,
        "combined_bytes": len(payload),
        "combined_sha256": _sha(payload),
    }
    bundle["digest"] = canonical.self_digest(bundle)
    paths = {
        "records": root / "records.jsonl",
        "bundle": root / "bundle.json",
        "execution": root / "execution.json",
    }
    paths["records"].write_bytes(payload)
    paths["bundle"].write_text(json.dumps(bundle), encoding="utf-8")
    paths["execution"].write_text(json.dumps(execution), encoding="utf-8")
    return paths


def _legacy_run(evaluator: Any, root: Path, lines: list[bytes]) -> Path:
    """Run the frozen CLI on the legacy-contract fixture; return its output dir."""
    paths = _legacy_world(root, lines)
    bundle = json.loads(paths["bundle"].read_text(encoding="utf-8"))
    out = root / "out"
    code = evaluator.main(
        [
            "run",
            "--records",
            str(paths["records"]),
            "--bundle",
            str(paths["bundle"]),
            "--execution",
            str(paths["execution"]),
            "--expect-bundle-digest",
            bundle["digest"],
            "--expect-execution-digest",
            bundle["execution_digest"],
            "--expect-combined-sha256",
            bundle["combined_sha256"],
            "--expect-revision",
            REV,
            "--expect-policy-digest",
            POLICY_DIGEST,
            "--output-dir",
            str(out),
        ]
    )
    assert code == 0
    return out


@pytest.fixture(scope="module")
def development_dir(
    tmp_path_factory: pytest.TempPathFactory, evaluator: Any, lines: list[bytes]
) -> Path:
    return _legacy_run(evaluator, tmp_path_factory.mktemp("development"), lines)


def test_adapter_path_reproduces_legacy_fixture_results(
    evaluator: Any, evaluated: tuple[dict[str, Any], dict[str, Any]], development_dir: Path
) -> None:
    payloads, _ = evaluated
    for name in m_analysis.SWEEP_PAYLOADS:
        frozen = (development_dir / name).read_text(encoding="utf-8")
        assert evaluator._dumps(payloads[name]) == frozen, name


def test_adapter_output_exact_finals(evaluated: tuple[dict[str, Any], dict[str, Any]]) -> None:
    summary = evaluated[0]["summary.json"]
    unit = 4096 // len(_POOL)
    b_normal = summary["combos"]["B-normal"]["final"]
    assert b_normal == {
        "essential_science": 2 * unit,
        "essential_practical": unit,
        "essential_prose": 2 * unit,
        "unassigned": unit,
        "rejected": 2 * unit,
    }
    assert summary["combos"]["A-normal"]["final"]["essential_science"] == unit
    assert summary["combos"]["C-normal"]["final"]["essential_practical"] == 0
    assert summary["combos"]["D-normal"]["final"]["essential_prose"] == 3 * unit
    assert summary["combos"]["B-strict"]["final"] == summary["combos"]["D-strict"]["final"]
    assert summary["combos"]["B-strict"]["final"]["essential_practical"] == 0


# --------------------------------------------------------------------------
# Analysis.
# --------------------------------------------------------------------------


def test_results_conserve_and_reconcile(evaluated: tuple[dict[str, Any], dict[str, Any]]) -> None:
    payloads, kernel = evaluated
    results = m_analysis.build_results(payloads, kernel, CRAWLS)
    assert sorted(results["conditions"]) == sorted(m_analysis.COMBOS)
    assert len(results["conditions"]) == 8
    for condition in results["conditions"].values():
        assert sum(condition["final"].values()) == 4096
        assert set(condition["final"]) == set(m_analysis.COMPONENTS)
        for crawl in CRAWLS:
            assert sum(condition["by_crawl"][crawl].values()) == 512
        for component in m_analysis.COMPONENTS:
            total = sum(condition["by_crawl"][crawl][component] for crawl in CRAWLS)
            assert total == condition["final"][component]
    assert results["multi_final_violations"] == 0
    assert results["selector_decision"] == "NOT MADE"


def test_no_overlap_after_precedence(evaluated: tuple[dict[str, Any], dict[str, Any]]) -> None:
    _, kernel = evaluated
    assert kernel["multi_final_violations"] == 0
    for combo in m_analysis.COMBOS:
        assert sum(kernel["finals"][f"{combo}|all"].values()) == 4096
    unit = 4096 // len(_POOL)
    # The S61 row matches both science (B) and prose: science must win, once.
    assert kernel["transition_matrices"]["A->B/normal"]["essential_prose->essential_science"] == (
        unit
    )
    assert kernel["d_only_normal"]["essential_prose"]["artifact=Irrelevant Content"] == unit
    assert kernel["b_to_c_losses"]["normal|essential_practical->unassigned"]["rows"] == unit
    assert kernel["b_normal_to_strict_loss_by_gs_reason"]["essential_practical"] == {
        "gate_english": unit
    }
    assert kernel["b_normal_to_strict_loss_by_gs_reason"]["essential_prose"] == {
        "gate_missing_content": unit
    }


def test_results_refuse_broken_accounting(evaluated: tuple[dict[str, Any], dict[str, Any]]) -> None:
    payloads, kernel = evaluated
    bad = copy.deepcopy(payloads)
    bad["summary.json"]["combos"]["B-normal"]["final"]["essential_science"] += 1
    with pytest.raises(m_analysis.AnalysisError, match="do not sum"):
        m_analysis.build_results(bad, kernel, CRAWLS)
    missing = copy.deepcopy(payloads)
    del missing["summary.json"]["combos"]["D-strict"]
    with pytest.raises(m_analysis.AnalysisError, match="eight frozen conditions"):
        m_analysis.build_results(missing, kernel, CRAWLS)
    moved = copy.deepcopy(payloads)
    cell = moved["per_crawl.json"][CRAWLS[0]]["combos"]["A-normal"]["final"]
    cell["essential_prose"] -= 1
    cell["unassigned"] += 1
    with pytest.raises(m_analysis.AnalysisError, match="kernel pass disagrees"):
        m_analysis.build_results(moved, kernel, CRAWLS)
    overlap = copy.deepcopy(payloads)
    overlap["summary.json"]["multi_final_violations"] = 1
    with pytest.raises(m_analysis.AnalysisError, match="more than one final"):
        m_analysis.build_results(overlap, kernel, CRAWLS)


def _development(development_dir: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    manifest_raw = (development_dir / "sweep_manifest.json").read_bytes()
    manifest = json.loads(manifest_raw)
    preparation = {
        "policy_digest": POLICY_DIGEST,
        "development": {
            "artifacts": manifest["artifacts"],
            "binding": manifest["binding"],
            "digest": manifest["digest"],
            "manifest": {"bytes": len(manifest_raw), "sha256": _sha(manifest_raw)},
        },
    }
    artifacts, binding = m_analysis.verify_development(development_dir, preparation)
    return artifacts, binding, preparation


def test_development_artifacts_verified_and_tamper_refused(
    development_dir: Path, tmp_path: Path
) -> None:
    artifacts, binding, preparation = _development(development_dir)
    assert sorted(binding["artifacts"]) == sorted(m_analysis.DEVELOPMENT_ARTIFACTS)
    assert binding["raw_bundle_reevaluated"] is False
    assert artifacts["summary.json"]["input_records"] == 4096
    copied = tmp_path / "copy"
    copied.mkdir()
    for path in development_dir.iterdir():
        (copied / path.name).write_bytes(path.read_bytes())
    summary = copied / "summary.json"
    summary.write_bytes(summary.read_bytes().replace(b"4096", b"4095", 1))
    with pytest.raises(m_analysis.AnalysisError, match=r"summary\.json differs"):
        m_analysis.verify_development(copied, preparation)


def test_comparison_identical_replicates_is_all_zero(
    evaluated: tuple[dict[str, Any], dict[str, Any]], development_dir: Path
) -> None:
    payloads, kernel = evaluated
    development, _, _ = _development(development_dir)
    bindings = {"m_source_sha256": "a" * 64, "development_manifest_digest": "b" * 64}
    comparison = m_analysis.build_comparison(development, payloads, kernel, bindings)
    assert comparison["input_bindings"] == bindings
    assert comparison["selector_decision"] == "NOT MADE"
    assert comparison["policy_ranking"] == "NOT PERFORMED"
    for combo in m_analysis.COMBOS:
        for component in m_analysis.COMPONENTS:
            assert comparison["totals"][combo][component]["difference"] == 0
            matched = comparison["per_crawl"][combo][component]["matched_crawl_difference"]
            assert matched["max_abs_pp"] == 0.0 and matched["crawls_equal"] == 8
    # Zero denominators are undefined, never zero percent.
    c_strict = comparison["strict_attrition"]["C"]
    assert c_strict["m"]["essential_practical"]["normal"] == 0
    assert c_strict["m"]["essential_practical"]["retained_share"] is None
    assert c_strict["retained_share_difference_pp"]["essential_practical"] is None
    assert "confidence" in comparison["statistics_note"]
    text = m_analysis.render_comparison(comparison)
    assert "undefined" in text and "No policy is ranked" in text


def test_comparison_reports_exact_differences(
    tmp_path: Path, evaluator: Any, spec: dict[str, Any], development_dir: Path
) -> None:
    alt = _adapt(tmp_path / "world", _source_lines(_POOL_ALT))
    payloads, kernel = _evaluate(evaluator, spec, alt, tmp_path / "eval")
    development, _, _ = _development(development_dir)
    comparison = m_analysis.build_comparison(development, payloads, kernel, {})
    unit = 4096 // 8
    cell = comparison["totals"]["B-normal"]["essential_prose"]
    assert (cell["development"], cell["m"], cell["difference"]) == (2 * unit, unit, -unit)
    assert cell["difference_pp"] == -12.5
    rejected = comparison["totals"]["B-normal"]["rejected"]
    assert rejected["difference"] == 2 * unit
    per_crawl = comparison["per_crawl"]["B-normal"]["essential_prose"]
    assert per_crawl["cells"][CRAWLS[0]] == {
        "development": 128,
        "m": 64,
        "difference": -64,
        "difference_pp": -12.5,
    }
    assert per_crawl["matched_crawl_difference"]["crawls_down"] == 8
    doctype = comparison["composition"]["input"]["doctype"]
    assert doctype["labels"]["Product Page"]["m"] == 3 * unit
    assert doctype["denominators"] == {"development": 4096, "m": 4096}


# --------------------------------------------------------------------------
# Seal.
# --------------------------------------------------------------------------


def _seal(
    adapted: Any, files: dict[str, bytes], development: dict[str, Any] | None = None
) -> dict[str, Any]:
    return m_analysis.build_seal(
        preparation={
            "scientific_namespace": "synthetic-namespace",
            "selection_digest": "5e" * 32,
            "policy_digest": POLICY_DIGEST,
            "phase_d_plan_digest": "9d" * 32,
        },
        preparation_binding={"sha256": "c" * 64},
        adapted_manifest=adapted.manifest,
        derived_input={"bytes": len(adapted.payload), "sha256": _sha(adapted.payload)},
        evaluator={"sha256": "d" * 64},
        development=development or {"manifest": {"digest": "e" * 64}},
        analysis_code={"m_analysis.py": {"sha256": "f" * 64}},
        manifest=m_analysis.artifact_manifest(files),
        commands=["synthetic"],
        environment={"python": "3.12"},
    )


_RESULT_FILES = {
    "m_sweep_results.json": b'{"a":1}\n',
    "m_comparison.json": b'{"b":2}\n',
    "m_sweep/summary.json": b'{"c":3}\n',
}


@pytest.mark.parametrize("name", sorted(_RESULT_FILES))
def test_seal_changes_with_any_result(adapted: Any, name: str) -> None:
    base = _seal(adapted, _RESULT_FILES)
    changed = _seal(adapted, {**_RESULT_FILES, name: _RESULT_FILES[name] + b" "})
    assert changed["seal_digest"] != base["seal_digest"]
    assert m_analysis.seal_digest_of(base) == base["seal_digest"]


def test_seal_changes_with_adapter_output_and_development_parent(
    tmp_path: Path, adapted: Any
) -> None:
    base = _seal(adapted, _RESULT_FILES)
    other = _adapt(tmp_path, _source_lines(_POOL_ALT))
    assert _seal(other, _RESULT_FILES)["seal_digest"] != base["seal_digest"]
    assert other.manifest["row_identity_digest"] == adapted.manifest["row_identity_digest"]
    moved = _seal(adapted, _RESULT_FILES, {"manifest": {"digest": "0" * 64}})
    assert moved["seal_digest"] != base["seal_digest"]


def test_seal_refuses_and_excludes_other_arm_material(adapted: Any) -> None:
    for name in ("sealed/t_selected_documents.jsonl", "t_acquisition_manifest.json"):
        with pytest.raises(m_analysis.AnalysisError, match="Arm-T"):
            m_analysis.refuse_t_material(Path(name))
        with pytest.raises(m_analysis.AnalysisError, match="Arm-T"):
            m_analysis.artifact_manifest({name: b"{}"})
    text = json.dumps(_seal(adapted, _RESULT_FILES)).lower()
    for token in ("t_selected", "t_provenance", "t_acquisition", "reviewer", "ew2-", '"text"'):
        assert token not in text
    assert '"other_arm_material_bound": false' in text


# --------------------------------------------------------------------------
# End to end through the CLI (synthetic preparation pinned for the test).
# --------------------------------------------------------------------------


def _cli_world(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    lines: list[bytes],
    development_dir: Path,
    spec: dict[str, Any],
) -> list[str]:
    root = tmp_path / "phase-d"
    _, preparation = _write_world(root, lines)
    _, _, development = _development(development_dir)
    real = _real_preparation()
    preparation.update(
        development=development["development"],
        frozen_evaluator=real["frozen_evaluator"],
        analysis_caps=dict(spec["caps"]),
    )
    preparation["digest"] = canonical.self_digest(preparation)
    path = tmp_path / "preparation.json"
    path.write_bytes(canonical.canonical_bytes(preparation))
    monkeypatch.setattr(m_analysis, "PREPARATION_DIGEST", preparation["digest"])
    return [
        "--preparation",
        str(path),
        "--phase-d-root",
        str(root),
        "--development-dir",
        str(development_dir),
        "--work-dir",
        str(tmp_path / "work"),
        "--output-dir",
        str(tmp_path / "out"),
    ]


def test_cli_run_seal_verify_and_tamper(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    lines: list[bytes],
    development_dir: Path,
    spec: dict[str, Any],
    capsys: pytest.CaptureFixture[str],
) -> None:
    args = _cli_world(tmp_path, monkeypatch, lines, development_dir, spec)
    out = tmp_path / "out"
    assert cli.main(["run", *args]) == 0
    facts = json.loads(capsys.readouterr().out)
    seal = json.loads((out / "m_seal.json").read_text(encoding="utf-8"))
    assert seal["status"] == "SEALED" and seal["seal_digest"] == facts["seal_digest"]
    assert seal["conditions"] == list(m_analysis.COMBOS)
    assert seal["adapted_input"]["sha256"] == facts["derived_input_sha256"]
    assert set(seal["results"]) >= {
        "m_sweep_results.json",
        "m_comparison.json",
        "m_comparison.md",
        "adapter_manifest.json",
        "adapted_input_manifest.json",
        "development_binding.json",
        "m_sweep/summary.json",
        "m_sweep/per_crawl.json",
    }
    assert m_analysis.verify_seal(out)["seal_digest"] == facts["seal_digest"]
    assert cli.main(["verify", *args]) == 0
    # A sealed package is never overwritten.
    assert cli.main(["run", *args]) == 1
    assert "already holds a sealed M package" in capsys.readouterr().err
    results = out / "m_sweep_results.json"
    original = results.read_bytes()
    results.write_bytes(original.replace(b'"NOT MADE"', b'"MADE"', 1))
    with pytest.raises(m_analysis.AnalysisError, match="changed after sealing"):
        m_analysis.verify_seal(out)
    assert cli.main(["verify", *args]) == 1
    results.write_bytes(original)
    assert cli.main(["verify", *args]) == 0


def test_cli_writes_nothing_on_refusal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    lines: list[bytes],
    development_dir: Path,
    spec: dict[str, Any],
) -> None:
    args = _cli_world(tmp_path, monkeypatch, lines, development_dir, spec)
    records = tmp_path / "phase-d" / m_input_adapter.M_OUTPUT_NAME
    records.write_bytes(records.read_bytes().replace(b"0.95", b"0.96", 1))
    assert cli.main(["run", *args]) == 1
    assert not (tmp_path / "out").exists()
    assert not (tmp_path / "work").exists()
