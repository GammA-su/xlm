"""Essential-Web selector sweep: bounded offline experiment (authored only).

Every record here is synthetic and invented to exercise selector logic;
nothing is real corpus text, and no test touches the network (sockets are
blocked) or the real X: bundle. Real execution evidence stays separate.
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
import yaml

_PATH = Path(__file__).resolve().parents[1] / "scripts" / "essential_web_selector_sweep.py"
_SPEC = importlib.util.spec_from_file_location("essential_web_selector_sweep", _PATH)
assert _SPEC is not None and _SPEC.loader is not None
sweep = importlib.util.module_from_spec(_SPEC)
sys.modules["essential_web_selector_sweep"] = sweep
_SPEC.loader.exec_module(sweep)

SPEC_PATH = (
    Path(__file__).resolve().parents[1]
    / "recipes"
    / "selectors"
    / "essential_web_selector_sweep_v1.yaml"
)
REV = "ab" * 20
REPO = "test/repo"


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_: Any, **__: Any) -> None:
        raise AssertionError("network access attempted in an offline test")

    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)


@pytest.fixture(scope="module")
def spec() -> dict[str, Any]:
    loaded, _ = sweep.load_policy_spec(SPEC_PATH)
    assert isinstance(loaded, dict)
    return loaded


def _tax(
    f: str = "510.2",
    d: str = "Academic Writing",
    k: str = "Conceptual",
    a: str = "No Artifacts",
    m: str = "No missing content",
    t: str = "Highly Correct",
) -> dict[str, Any]:
    return {
        "free_decimal_correspondence": {"primary": {"code": f}},
        "document_type_v2": {"primary": {"label": d}},
        "bloom_knowledge_domain": {"primary": {"label": k}},
        "extraction_artifacts": {"primary": {"label": a}},
        "missing_content": {"primary": {"label": m}},
        "technical_correctness": {"primary": {"label": t}},
    }


def _row(
    f: str = "510.2",
    d: str = "Academic Writing",
    k: str = "Conceptual",
    a: str = "No Artifacts",
    m: str = "No missing content",
    t: str = "Highly Correct",
    e: float = 0.95,
    text: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "eai_taxonomy": _tax(f, d, k, a, m, t),
        "quality_signals": {"fasttext": {"english": e}},
    }
    if text is not None:
        record["text"] = text
    record.update(extra)
    return record


def _features(spec: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    fields, reasons, _ = sweep.validate_row(_row(**kwargs), spec)
    assert reasons == []
    return fields


# --------------------------------------------------------------------------
# Gates: exact GN/GS/GD semantics.
# --------------------------------------------------------------------------


def test_gate_gn_pass_and_each_condition(spec: dict[str, Any]) -> None:
    assert sweep.gate_gn(_features(spec), spec) == []
    assert sweep.gate_gn(_features(spec, e=0.79), spec) == ["gate_english"]
    assert sweep.gate_gn(_features(spec, e=0.80), spec) == []
    assert sweep.gate_gn(_features(spec, a="Irrelevant Content"), spec) == ["gate_artifacts"]
    assert sweep.gate_gn(_features(spec, m="Missing Images or Figures"), spec) == []
    assert sweep.gate_gn(_features(spec, m="Truncated Snippets"), spec) == ["gate_missing_content"]
    assert sweep.gate_gn(_features(spec, t="Partially Correct"), spec) == ["gate_correctness"]
    assert sweep.gate_gn(_features(spec, t="Technically Flawed"), spec) == ["gate_correctness"]
    assert sweep.gate_gn(_features(spec, d="Product Page"), spec) == ["gate_doctype"]
    both = sweep.gate_gn(_features(spec, e=0.5, d="Spam / Ads"), spec)
    assert both == ["gate_english", "gate_doctype"]


def test_gate_gs_strict(spec: dict[str, Any]) -> None:
    assert sweep.gate_gs(_features(spec, e=0.90), spec) == []
    assert sweep.gate_gs(_features(spec, e=0.89), spec) == ["gate_english"]
    assert sweep.gate_gs(_features(spec, m="Missing Images or Figures"), spec) == [
        "gate_missing_content"
    ]


def test_gate_gd_artifact_sensitivity(spec: dict[str, Any]) -> None:
    assert sweep.gate_gd(_features(spec, a="Irrelevant Content"), spec) == []
    assert sweep.gate_gd(_features(spec, a="Leftover HTML"), spec) == ["gate_artifacts"]
    assert sweep.gate_gd(_features(spec, e=0.79), spec) == ["gate_english"]


# --------------------------------------------------------------------------
# Predicates: S5, S61 allowlist, P branches, R, F6, F789.
# --------------------------------------------------------------------------


def test_predicate_s5(spec: dict[str, Any]) -> None:
    assert sweep.predicate_s5(_features(spec), spec) is True
    assert sweep.predicate_s5(_features(spec, f="610.1"), spec) is False
    assert sweep.predicate_s5(_features(spec, d="Product Page"), spec) is False


def test_predicate_s61_exact_allowlist(spec: dict[str, Any]) -> None:
    good = {"f": "613.5", "d": "Knowledge Article", "k": "Conceptual", "t": "Mostly Correct"}
    assert sweep.predicate_s61(_features(spec, **good), spec) is True
    assert sweep.predicate_s61(_features(spec, f="611.0", d="Knowledge Article"), spec) is False
    assert sweep.predicate_s61(_features(spec, f="619.9", d="Knowledge Article"), spec) is False
    assert sweep.predicate_s61(_features(spec, f="613.5", d="Tutorial"), spec) is False
    assert sweep.predicate_s61(_features(spec, k="Procedural"), spec) is False
    assert sweep.predicate_s61(_features(spec, t="Partially Correct"), spec) is False
    for prefix in ("610", "612", "613", "614", "615", "616", "617", "618"):
        assert sweep.predicate_s61(_features(spec, f=f"{prefix}.0"), spec) is True


def test_predicate_p_branches(spec: dict[str, Any]) -> None:
    matched, branch = sweep.predicate_p(_features(spec, d="Tutorial", k="Factual", e=0.85), spec)
    assert (matched, branch) == (True, "explicit")
    matched, branch = sweep.predicate_p(
        _features(spec, d="Q&A Forum", k="Procedural", e=0.85), spec
    )
    assert (matched, branch) == (True, "conditional")
    assert sweep.predicate_p(_features(spec, d="Q&A Forum", k="Factual"), spec) == (False, "")
    assert sweep.predicate_p(_features(spec, d="Product Page", k="Procedural"), spec) == (False, "")


def test_predicate_r_withholds_procedural(spec: dict[str, Any]) -> None:
    assert sweep.predicate_r(_features(spec, d="Creative Writing", k="Factual"), spec) is True
    assert sweep.predicate_r(_features(spec, d="Creative Writing", k="Procedural"), spec) is False
    assert sweep.predicate_r(_features(spec, d="Tutorial", k="Factual"), spec) is False


def test_predicate_f6_f789_and_leading_zeros(spec: dict[str, Any]) -> None:
    assert sweep.predicate_f6(_features(spec, f="650.1"), spec) is True
    assert sweep.predicate_f6(_features(spec, f="005.4", d="Documentation"), spec) is False
    assert sweep.predicate_f789(_features(spec, f="720.0", d="Creative Writing"), spec) is True
    assert sweep.predicate_f789(_features(spec, f="810.2", d="Creative Writing"), spec) is True
    assert sweep.predicate_f789(_features(spec, f="940.0", d="News Article"), spec) is True
    assert sweep.predicate_f789(_features(spec, f="510.2"), spec) is False


def test_leading_zero_stays_string_and_0xx(spec: dict[str, Any]) -> None:
    fields, reasons, _ = sweep.validate_row(_row(f="005.4", d="Documentation"), spec)
    assert reasons == []
    assert fields["f"] == "005.4" and isinstance(fields["f"], str)
    assert fields["digit1"] == "0" and fields["prefix3"] == "005"


# --------------------------------------------------------------------------
# Validity: anomaly quarantine, unknowns, missing, bad types, English rules.
# --------------------------------------------------------------------------


def test_anomaly_quarantined_never_3xx(spec: dict[str, Any]) -> None:
    fields, reasons, _ = sweep.validate_row(_row(f="320.973/0207"), spec)
    assert reasons == ["invalid_fdc_syntax"]
    assert "f" not in fields and "prefix3" not in fields
    assert fields["e"] == 0.95


def test_unknown_labels_fail_closed(spec: dict[str, Any]) -> None:
    _, reasons, unknowns = sweep.validate_row(_row(d="Something New"), spec)
    assert reasons == ["unknown_label:d"]
    assert unknowns == {"d": "Something New"}
    _, reasons, _ = sweep.validate_row(_row(k="Weird"), spec)
    assert reasons == ["unknown_label:k"]
    _, reasons, _ = sweep.validate_row(_row(a="Glitch"), spec)
    assert reasons == ["unknown_label:a"]


def test_missing_values_all_reasons(spec: dict[str, Any]) -> None:
    _, reasons, _ = sweep.validate_row({}, spec)
    assert "missing_eai_taxonomy" in reasons
    assert "missing_quality_signals" in reasons
    assert "missing_fdc" in reasons
    assert "missing_english" in reasons
    assert "missing_label:d" in reasons
    _, reasons, _ = sweep.validate_row({"eai_taxonomy": None, "quality_signals": None}, spec)
    assert "missing_eai_taxonomy" in reasons
    bad_parent = {"eai_taxonomy": {"free_decimal_correspondence": 42}, "quality_signals": {}}
    _, reasons, _ = sweep.validate_row(bad_parent, spec)
    assert "malformed_fdc_parent" in reasons


def test_bad_types_refused(spec: dict[str, Any]) -> None:
    _, reasons, _ = sweep.validate_row(_row(f=123), spec)  # type: ignore[arg-type]
    assert reasons == ["bad_type_fdc"]
    _, reasons, _ = sweep.validate_row(_row(e="high"), spec)  # type: ignore[arg-type]
    assert reasons == ["bad_type_english"]
    _, reasons, _ = sweep.validate_row(_row(e=True), spec)  # type: ignore[arg-type]
    assert reasons == ["bad_type_english"]
    _, reasons, _ = sweep.validate_row(_row(d=5), spec)  # type: ignore[arg-type]
    assert reasons == ["bad_type_label:d"]
    _, reasons, _ = sweep.validate_row(_row(d="   "), spec)
    assert reasons == ["empty_label:d"]


def test_english_nan_inf_range_refused(spec: dict[str, Any]) -> None:
    _, reasons, _ = sweep.validate_row(_row(e=float("nan")), spec)
    assert reasons == ["nonfinite_english"]
    _, reasons, _ = sweep.validate_row(_row(e=float("inf")), spec)
    assert reasons == ["nonfinite_english"]
    _, reasons, _ = sweep.validate_row(_row(e=1.5), spec)
    assert reasons == ["english_out_of_range"]
    _, reasons, _ = sweep.validate_row(_row(e=-0.1), spec)
    assert reasons == ["english_out_of_range"]
    _, reasons, _ = sweep.validate_row(_row(e=1.0), spec)
    assert reasons == []


def test_text_is_ignored_not_read(spec: dict[str, Any]) -> None:
    plain = _row()
    with_text = _row(text="PAYLOAD THAT MUST NEVER MATTER " * 100)
    for policy in ("A", "B", "C", "D"):
        for tier in ("normal", "strict"):
            left = sweep.evaluate_policy(sweep.validate_row(plain, spec)[0], policy, tier, spec)
            right = sweep.evaluate_policy(
                sweep.validate_row(with_text, spec)[0], policy, tier, spec
            )
            assert left == right
    minimal = {
        "eai_taxonomy": _tax(),
        "quality_signals": {"fasttext": {"english": 0.9}},
    }
    fields, reasons, _ = sweep.validate_row(minimal, spec)
    assert reasons == []
    assert set(fields) >= {"f", "d", "k", "a", "m", "t", "e"}


# --------------------------------------------------------------------------
# Precedence and policy semantics.
# --------------------------------------------------------------------------


def _eval(spec: dict[str, Any], policy: str, tier: str, **kwargs: Any) -> str:
    fields = _features(spec, **kwargs)
    return str(sweep.evaluate_policy(fields, policy, tier, spec)["final"])


def test_precedence_science_over_practical_over_prose(spec: dict[str, Any]) -> None:
    everything = {"f": "510.2", "d": "Tutorial", "k": "Procedural", "e": 0.95}
    assert _eval(spec, "B", "normal", **everything) == "essential_science"
    practical_prose = {"f": "650.1", "d": "Tutorial", "k": "Procedural", "e": 0.95}
    assert _eval(spec, "B", "normal", **practical_prose) == "essential_practical"
    prose_only = {"f": "720.0", "d": "Creative Writing", "k": "Factual", "e": 0.95}
    assert _eval(spec, "B", "normal", **prose_only) == "essential_prose"
    assert _eval(spec, "B", "normal", f="420.0", d="Academic Writing", k="Factual") == (
        "unassigned"
    )
    assert _eval(spec, "B", "normal", e=0.5) == "rejected"


def test_policy_c_restriction_semantics(spec: dict[str, Any]) -> None:
    assert _eval(spec, "C", "normal", f="650.1", d="Tutorial", k="Procedural") == (
        "essential_practical"
    )
    assert _eval(spec, "C", "normal", f="510.2", d="Tutorial", k="Procedural") == (
        "essential_science"
    )
    assert _eval(spec, "C", "normal", f="720.0", d="Creative Writing", k="Factual") == (
        "essential_prose"
    )
    assert _eval(spec, "C", "normal", f="420.0", d="Creative Writing", k="Factual") == (
        "unassigned"
    )


# --------------------------------------------------------------------------
# Synthetic worlds: full 8x512 bundles for binding and sweep tests.
#
# Hand-computed B-normal per-crawl expectations (64 rows per template):
#   science 192 (t0 S5 + t1 S61 + t2 S5/takes-precedence-over-P),
#   practical 128 (t3 P-explicit + t4 P-conditional),
#   prose 64 (t5 R), unassigned 0, rejected 128 (t6 gate + t7 anomaly).
# --------------------------------------------------------------------------

_POOL_V2 = [
    {"f": "510.2", "d": "Academic Writing", "k": "Conceptual", "e": 0.95},
    {"f": "613.5", "d": "Knowledge Article", "k": "Conceptual", "t": "Mostly Correct", "e": 0.92},
    {"f": "510.2", "d": "Tutorial", "k": "Procedural", "e": 0.95},
    {"f": "720.0", "d": "Tutorial", "k": "Factual", "t": "Mostly Correct", "e": 0.85},
    {
        "f": "650.1",
        "d": "Q&A Forum",
        "k": "Procedural",
        "m": "Missing Images or Figures",
        "e": 0.85,
    },
    {
        "f": "510.2",
        "d": "Creative Writing",
        "k": "Factual",
        "t": "Not Applicable/Indeterminate",
        "e": 0.90,
    },
    {"f": "420.0", "d": "Academic Writing", "k": "Factual", "e": 0.5},
    {"f": "320.973/0207", "d": "News Article", "k": "Factual", "e": 0.9},
]


def _world_bundle(
    pools: list[list[dict[str, Any]]] | None = None,
    row_count: int = 512,
    dup_line: int | None = None,
    pad_line: tuple[int, int] | None = None,
    locator_override: tuple[int, dict[str, Any]] | None = None,
) -> tuple[bytes, dict[str, Any], dict[str, Any]]:
    """Build records bytes + bundle/execution dicts with correct digests.

    Optional mutations (applied before digesting, so binding passes and the
    sweep itself must refuse): duplicate a line, pad a line to N bytes, or
    replace one row's locator fields.
    """
    pools = pools if pools is not None else [_POOL_V2] * 8
    assert len(pools) == 8
    lines: list[bytes] = []
    parts: list[dict[str, Any]] = []
    index = 0
    for stratum, pool in enumerate(pools):
        assert pool, "empty pool"
        fname = f"f{stratum}.parquet"
        start = index
        _, remainder = divmod(row_count, len(pool))
        assert remainder == 0, "pool length must divide the row count"
        for j in range(row_count):
            template = dict(pool[j % len(pool)])
            record = _row(**template)
            record["_xlm_acquisition"] = {
                "source_file": fname,
                "row_index": index,
                "revision": REV,
                "repository": REPO,
            }
            if locator_override is not None and len(lines) == locator_override[0]:
                record["_xlm_acquisition"].update(locator_override[1])
            lines.append(json.dumps(record, ensure_ascii=False).encode("utf-8"))
            index += 1
        parts.append(
            {
                "unit": f"{stratum:02d}",
                "stratum": stratum,
                "crawl": f"crawl-{stratum}",
                "file": fname,
                "row_range": [start, index],
                "plan_hash": hashlib.sha256(f"plan{stratum}".encode()).hexdigest(),
                "records": row_count,
                "bytes": 0,
                "sha256": "0" * 64,
            }
        )
    if dup_line is not None:
        lines[dup_line] = lines[0]
    if pad_line is not None:
        at, size = pad_line
        lines[at] = lines[at] + b" " * (size - len(lines[at]))
    payload = b"\n".join(lines) + b"\n"

    def _digest(body: dict[str, Any]) -> str:
        return hashlib.sha256(
            json.dumps(
                {k: v for k, v in body.items() if k != "digest"},
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode()
        ).hexdigest()

    execution: dict[str, Any] = {
        "kind": "execution",
        "revision": REV,
        "repository": REPO,
        "projection": ["eai_taxonomy", "quality_signals"],
    }
    execution["digest"] = _digest(execution)
    bundle: dict[str, Any] = {
        "kind": "bundle",
        "execution_digest": execution["digest"],
        "revision": REV,
        "projection": ["eai_taxonomy", "quality_signals"],
        "order": "stratum",
        "parts": parts,
        "part_count": 8,
        "total_records": index,
        "combined_bytes": len(payload),
        "combined_sha256": hashlib.sha256(payload).hexdigest(),
    }
    bundle["digest"] = _digest(bundle)
    return payload, bundle, execution


def _write_world(
    tmp_path: Path,
    pools: list[list[dict[str, Any]]] | None = None,
    row_count: int = 512,
    **mutations: Any,
) -> dict[str, Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    payload, bundle, execution = _world_bundle(pools, row_count, **mutations)
    paths = {
        "records": tmp_path / "records.jsonl",
        "bundle": tmp_path / "bundle.json",
        "execution": tmp_path / "execution.json",
    }
    paths["records"].write_bytes(payload)
    paths["bundle"].write_text(json.dumps(bundle, indent=2), encoding="utf-8")
    paths["execution"].write_text(json.dumps(execution, indent=2), encoding="utf-8")
    return paths


def _run_args(paths: dict[str, Path], out: Path, **overrides: Any) -> list[str]:
    bundle = json.loads(paths["bundle"].read_text(encoding="utf-8"))
    execution = json.loads(paths["execution"].read_text(encoding="utf-8"))
    payload = paths["records"].read_bytes()
    args: dict[str, Any] = {
        "records": str(paths["records"]),
        "bundle": str(paths["bundle"]),
        "execution": str(paths["execution"]),
        "expect-bundle-digest": bundle["digest"],
        "expect-execution-digest": execution["digest"],
        "expect-combined-sha256": hashlib.sha256(payload).hexdigest(),
        "expect-records": bundle["total_records"],
        "expect-revision": REV,
        "output-dir": str(out),
    }
    args.update(overrides)
    argv = ["run"]
    for key, value in args.items():
        argv += [f"--{key}", str(value)]
    return argv


def _run_world(
    tmp_path: Path,
    pools: list[list[dict[str, Any]]] | None = None,
    row_count: int = 512,
    mutations: dict[str, Any] | None = None,
    **overrides: Any,
) -> tuple[int, Path]:
    paths = _write_world(tmp_path, pools, row_count, **(mutations or {}))
    out = tmp_path / "out"
    code = sweep.main(_run_args(paths, out, **overrides))
    return code, out


def _summary(out: Path) -> dict[str, Any]:
    return json.loads((out / "summary.json").read_text(encoding="utf-8"))


def _expected_b_normal() -> dict[str, int]:
    return {
        "essential_science": 192 * 8,
        "essential_practical": 128 * 8,
        "essential_prose": 64 * 8,
        "unassigned": 0,
        "rejected": 128 * 8,
    }


def test_full_world_exact_finals(tmp_path: Path) -> None:
    code, out = _run_world(tmp_path)
    assert code == 0
    summary = _summary(out)
    assert summary["input_records"] == 4096
    assert summary["combos"]["B-normal"]["final"] == _expected_b_normal()
    assert summary["combos"]["A-normal"]["final"] == {
        "essential_science": 128 * 8,
        "essential_practical": 128 * 8,
        "essential_prose": 128 * 8,
        "unassigned": 0,
        "rejected": 128 * 8,
    }
    assert summary["combos"]["C-normal"]["final"] == {
        "essential_science": 192 * 8,
        "essential_practical": 64 * 8,
        "essential_prose": 0,
        "unassigned": 128 * 8,
        "rejected": 128 * 8,
    }
    assert summary["combos"]["B-strict"]["final"] == {
        "essential_science": 192 * 8,
        "essential_practical": 0,
        "essential_prose": 64 * 8,
        "unassigned": 0,
        "rejected": 256 * 8,
    }
    for key, combo in summary["combos"].items():
        assert sum(combo["final"].values()) == 4096, key
        assert combo["conservation_ok"] is True


def test_full_world_invariants(tmp_path: Path) -> None:
    _, out = _run_world(tmp_path)
    summary = _summary(out)
    invariants = summary["identity_invariants"]
    assert invariants["b_strict_d_strict_agreement"] == 4096
    assert invariants["b_c_science_agreement_normal"] == 4096
    assert summary["multi_final_violations"] == 0
    for key, value in invariants.items():
        if key.startswith(
            ("A_strict_subset", "B_strict_subset", "C_strict_subset", "D_strict_subset")
        ):
            assert value == 0, key
        if key.startswith(("C_subset_B", "d_gate_superset")):
            assert value == 0, key


def test_full_world_overlaps_and_transfers(tmp_path: Path) -> None:
    _, out = _run_world(tmp_path)
    overlaps = json.loads((out / "overlaps.json").read_text(encoding="utf-8"))
    base = overlaps["predicate_overlaps"]["all"]
    assert base["S5&P"] == 512
    assert base["S61&R"] == 512
    assert base.get("P&R", 0) == 0
    transfers = overlaps["component_overlaps"]["B-normal"]["all"]["transfers"]
    assert transfers["practical&science->essential_science"] == 512
    assert transfers["prose&science->essential_science"] == 512
    per_crawl = json.loads((out / "per_crawl.json").read_text(encoding="utf-8"))
    assert sorted(per_crawl) == [f"crawl-{i}" for i in range(8)]
    for crawl, cell in per_crawl.items():
        assert cell["input"] == 512, crawl
        for key, combo in cell["combos"].items():
            assert sum(combo["final"].values()) == 512, (crawl, key)


def test_full_world_anomaly_quarantine(tmp_path: Path) -> None:
    _, out = _run_world(tmp_path)
    diagnostics = json.loads((out / "diagnostics.json").read_text(encoding="utf-8"))
    assert diagnostics["fdc_anomaly"]["count"] == 512
    assert len(diagnostics["fdc_anomaly"]["locators"]) == 50
    summary = _summary(out)
    assert summary["combos"]["B-normal"]["final"]["rejected"] == 128 * 8
    crosstabs = json.loads((out / "crosstabs.json").read_text(encoding="utf-8"))
    science = crosstabs["science_balance"]["B-normal"]
    assert science["science_final"] == 192 * 8
    assert science["s61_final"] == 64 * 8
    assert science["selected_61x_fraction"] == round(512 / 1536, 6)


# --------------------------------------------------------------------------
# Binding unit tests: small hand-built bundle/execution dicts (no rows).
# --------------------------------------------------------------------------


def _mini_bundle(**overrides: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    def _digest(body: dict[str, Any]) -> str:
        return hashlib.sha256(
            json.dumps(
                {k: v for k, v in body.items() if k != "digest"},
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode()
        ).hexdigest()

    parts = [
        {
            "unit": f"{s:02d}",
            "stratum": s,
            "crawl": f"crawl-{s}",
            "file": f"f{s}.parquet",
            "row_range": [s * 512, (s + 1) * 512],
            "plan_hash": hashlib.sha256(f"plan{s}".encode()).hexdigest(),
            "records": 512,
            "bytes": 100,
            "sha256": "0" * 64,
        }
        for s in range(8)
    ]
    execution: dict[str, Any] = {
        "kind": "execution",
        "revision": REV,
        "repository": REPO,
        "projection": ["eai_taxonomy", "quality_signals"],
    }
    execution["digest"] = _digest(execution)
    bundle: dict[str, Any] = {
        "kind": "bundle",
        "execution_digest": execution["digest"],
        "revision": REV,
        "projection": ["eai_taxonomy", "quality_signals"],
        "order": "stratum",
        "parts": parts,
        "part_count": 8,
        "total_records": 4096,
        "combined_bytes": 10,
        "combined_sha256": "x" * 64,
    }
    bundle["digest"] = _digest(bundle)
    bundle.update(overrides.get("bundle", {}))
    if "redigest" not in overrides:
        bundle["digest"] = _digest(bundle)
    return bundle, execution


def _bind(
    tmp_path: Path,
    bundle: dict[str, Any],
    execution: dict[str, Any],
    revision: str = REV,
) -> dict[str, Any]:
    bundle_path = tmp_path / "bundle.json"
    execution_path = tmp_path / "execution.json"
    bundle_path.write_text(json.dumps(bundle), encoding="utf-8")
    execution_path.write_text(json.dumps(execution), encoding="utf-8")
    return sweep.load_binding(
        bundle_path,
        execution_path,
        {"records": 4096, "revision": revision, "projection": ["eai_taxonomy", "quality_signals"]},
    )


def test_binding_accepts_wellformed(tmp_path: Path) -> None:
    bundle, execution = _mini_bundle()
    bound = _bind(tmp_path, bundle, execution)
    assert bound["total_records"] == 4096
    assert bound["crawls"] == [f"crawl-{s}" for s in range(8)]


def test_binding_refusals(tmp_path: Path) -> None:
    bundle, execution = _mini_bundle()
    bad = dict(bundle)
    bad["digest"] = "0" * 64
    with pytest.raises(sweep.SweepError, match="self-digest"):
        _bind(tmp_path, bad, execution)
    _, execution2 = _mini_bundle()
    execution2["revision"] = "zz"
    with pytest.raises(sweep.SweepError, match="self-digest|disagree"):
        _bind(tmp_path, bundle, execution2)
    linked, _ = _mini_bundle()
    linked["execution_digest"] = "0" * 64
    linked["digest"] = sweep._manifest_digest(linked)
    with pytest.raises(sweep.SweepError, match="execution_digest"):
        _bind(tmp_path, linked, execution)
    proj, _ = _mini_bundle()
    proj["projection"] = ["eai_taxonomy"]
    proj["digest"] = sweep._manifest_digest(proj)
    with pytest.raises(sweep.SweepError, match="projection"):
        _bind(tmp_path, proj, execution)
    rev, _ = _mini_bundle()
    rev["revision"] = "main"
    rev["digest"] = sweep._manifest_digest(rev)
    with pytest.raises(sweep.SweepError, match="revision"):
        _bind(tmp_path, rev, execution)
    branch, exec_branch = _mini_bundle()
    branch["revision"] = "main"
    exec_branch["revision"] = "main"
    exec_branch["digest"] = sweep._manifest_digest(exec_branch)
    branch["execution_digest"] = exec_branch["digest"]
    branch["digest"] = sweep._manifest_digest(branch)
    with pytest.raises(sweep.SweepError, match="40-hex"):
        _bind(tmp_path, branch, exec_branch, revision="main")
    seven, _ = _mini_bundle()
    seven["parts"] = seven["parts"][:7]
    seven["digest"] = sweep._manifest_digest(seven)
    with pytest.raises(sweep.SweepError, match="exactly 8 parts"):
        _bind(tmp_path, seven, execution)
    dup, _ = _mini_bundle()
    dup["parts"][1] = dict(dup["parts"][1], plan_hash=dup["parts"][0]["plan_hash"])
    dup["digest"] = sweep._manifest_digest(dup)
    with pytest.raises(sweep.SweepError, match="unique plan hashes"):
        _bind(tmp_path, dup, execution)
    short, _ = _mini_bundle()
    short["parts"][0] = dict(short["parts"][0], records=511, row_range=[0, 511])
    short["digest"] = sweep._manifest_digest(short)
    with pytest.raises(sweep.SweepError, match="512"):
        _bind(tmp_path, short, execution)
    order, _ = _mini_bundle()
    order["parts"] = list(reversed(order["parts"]))
    order["digest"] = sweep._manifest_digest(order)
    with pytest.raises(sweep.SweepError, match="stratum"):
        _bind(tmp_path, order, execution)


def test_run_is_byte_identical(tmp_path: Path) -> None:
    _, first = _run_world(tmp_path / "a")
    _, second = _run_world(tmp_path / "b")
    assert first.is_dir() and second.is_dir()
    for name in (
        "summary.json",
        "per_crawl.json",
        "attrition.json",
        "overlaps.json",
        "crosstabs.json",
        "diagnostics.json",
        "policy_spec.json",
        "summary.md",
        "sweep_manifest.json",
    ):
        assert (first / name).read_bytes() == (second / name).read_bytes(), name


def test_expect_count_and_hash_mismatches(tmp_path: Path) -> None:
    assert _run_world(tmp_path / "a") == (0, tmp_path / "a" / "out")
    code, _ = _run_world(tmp_path / "b", **{"expect-records": 4095})
    assert code == 1
    code, _ = _run_world(tmp_path / "c", **{"expect-combined-sha256": "0" * 64})
    assert code == 1
    paths = _write_world(tmp_path / "d")
    payload = paths["records"].read_bytes() + b"not json\n"
    paths["records"].write_bytes(payload)
    out = tmp_path / "d" / "out"
    assert sweep.main(_run_args(paths, out)) == 1
    paths = _write_world(tmp_path / "e")
    payload = paths["records"].read_bytes() + b"42\n"
    paths["records"].write_bytes(payload)
    assert sweep.main(_run_args(paths, tmp_path / "e" / "out")) == 1


def test_duplicate_and_foreign_locators_refused(tmp_path: Path) -> None:
    code, _ = _run_world(tmp_path / "a", mutations={"dup_line": 5})
    assert code == 1
    code, _ = _run_world(tmp_path / "b", mutations={"locator_override": (7, {"row_index": 99999})})
    assert code == 1
    code, _ = _run_world(
        tmp_path / "c", mutations={"locator_override": (7, {"source_file": "nope.parquet"})}
    )
    assert code == 1


def test_input_line_and_output_caps(tmp_path: Path) -> None:
    big = tmp_path / "big.jsonl"
    big.write_bytes(b"x" * (33 * 1024 * 1024))
    paths = _write_world(tmp_path / "w")
    args = _run_args(paths, tmp_path / "w" / "out")
    args[args.index("--records") + 1] = str(big)
    assert sweep.main(args) == 1
    code, _ = _run_world(tmp_path / "w2", mutations={"pad_line": (3, 2 * 1024 * 1024)})
    assert code == 1
    with pytest.raises(sweep.SweepError, match="cap"):
        sweep.write_artifacts(tmp_path / "cap", {"a.json": "x" * 100}, {"max_output_bytes": 10})
    with pytest.raises(sweep.SweepError, match="runtime cap"):
        sweep._check_deadline(0.0, -1.0)


def test_sensitivity_monotone_and_labeled(tmp_path: Path) -> None:
    _, out = _run_world(tmp_path)
    diagnostics = json.loads((out / "diagnostics.json").read_text(encoding="utf-8"))
    counts = diagnostics["sensitivity_joint_pass_counts"]["B/all"]
    ordered = [counts[f"E>={t}"] for t in ("0.65", "0.8", "0.9", "0.95")]
    assert ordered == sorted(ordered, reverse=True)
    assert counts["A=No Artifacts"] <= counts["A=No Artifacts+Irrelevant Content"]
    assert (
        counts["M=No missing content"] <= counts["M=No missing content+Missing Images or Figures"]
    )
    assert diagnostics["sensitivity_note"].startswith("diagnostic-only")


def test_english_compositions_wordcount_extra(tmp_path: Path) -> None:
    _, out = _run_world(tmp_path)
    crosstabs = json.loads((out / "crosstabs.json").read_text(encoding="utf-8"))
    dist = crosstabs["english_distributions"]["B-normal/essential_science"]
    assert dist["count"] == 192 * 8
    assert dist["min"] <= dist["p10"] <= dist["p50"] <= dist["p90"] <= dist["max"]
    labels = crosstabs["compositions"]["B-normal/essential_science"]["labels"]
    assert labels["D=Academic Writing"] == 512
    assert labels["D=Tutorial"] == 512
    assert labels["D=Knowledge Article"] == 512
    assert crosstabs["word_counts"]["status"] == "unavailable"
    fdk = crosstabs["fdc3_x_doctype_x_knowledge"]["all"]
    assert fdk["510|Academic Writing|Conceptual"] == 512
    assert fdk["613|Knowledge Article|Conceptual"] == 512


def test_extra_fields_and_text_ignored_in_run(tmp_path: Path) -> None:
    pools = [[dict(template, extra_col="x", text="PAYLOAD") for template in _POOL_V2]] * 8
    code, out = _run_world(tmp_path, pools=pools)
    assert code == 0
    assert _summary(out)["combos"]["B-normal"]["final"] == _expected_b_normal()


def test_transitions_b_to_c(tmp_path: Path) -> None:
    _, out = _run_world(tmp_path)
    overlaps = json.loads((out / "overlaps.json").read_text(encoding="utf-8"))
    cell = overlaps["transitions"]["B->C/normal"]
    assert cell == {"additions": 0, "removals": 1024, "reassignments": 0}
    strict = overlaps["transitions"]["B/normal->strict"]
    assert strict["removals"] > 0 and strict["additions"] == 0


def _flag_world(tmp_path: Path, pools: list[list[dict[str, Any]]]) -> dict[str, Any]:
    code, out = _run_world(tmp_path, pools=pools)
    assert code == 0
    return json.loads((out / "diagnostics.json").read_text(encoding="utf-8"))


def test_temporal_zero_and_sparse_flags(tmp_path: Path) -> None:
    low = {"f": "420.0", "d": "Academic Writing", "k": "Factual", "e": 0.5}
    pools = [_POOL_V2] * 7 + [[low]]
    diagnostics = _flag_world(tmp_path, pools)
    cells = diagnostics["temporal_cells"]["B-normal/essential_science"]["cells"]
    assert cells[7]["flag"] == "zero_final_rows"
    assert all("flag" not in cell for cell in cells[:7])


def test_temporal_fold_flag(tmp_path: Path) -> None:
    rich = [{"f": "510.2", "d": "Academic Writing", "k": "Conceptual", "e": 0.95}]
    pools = [rich] + [_POOL_V2] * 7
    diagnostics = _flag_world(tmp_path, pools)
    note = diagnostics["temporal_cells"]["B-normal/essential_science"]["retention_fold_note"]
    assert note is not None and "2-fold" in note


def test_temporal_pp_spread_flag(tmp_path: Path) -> None:
    high = [{**t, "e": 0.95} for t in _POOL_V2]
    low = [{**t, "e": 0.5} for t in _POOL_V2]
    pools = [high, low] + [_POOL_V2] * 6
    diagnostics = _flag_world(tmp_path, pools)
    english = diagnostics["conditional_gate_spreads_pp"]["B-normal"]
    assert english["gate_english"]["flagged"] is True


def test_temporal_s61_fraction_flag(tmp_path: Path) -> None:
    s61 = {
        "f": "613.5",
        "d": "Knowledge Article",
        "k": "Conceptual",
        "t": "Mostly Correct",
        "e": 0.92,
    }
    pools = [[s61]] + [_POOL_V2] * 7
    diagnostics = _flag_world(tmp_path, pools)
    cell = diagnostics["temporal_cells"]["B-normal/essential_science"]
    assert cell.get("s61_note") is not None


def _validate_args(paths: dict[str, Path], out: Path) -> list[str]:
    """validate-input flags are the run flags minus --output-dir."""
    argv = _run_args(paths, out)[1:]
    pruned: list[str] = []
    skip_next = False
    for token in argv:
        if skip_next:
            skip_next = False
            continue
        if token == "--output-dir":
            skip_next = True
            continue
        pruned.append(token)
    return pruned


def test_cli_validate_and_run_codes(tmp_path: Path) -> None:
    paths = _write_world(tmp_path)
    out = tmp_path / "out"
    assert sweep.main(["validate-input"] + _validate_args(paths, out)) == 0
    assert sweep.main(["run"] + _run_args(paths, out)[1:]) == 0
    bad = _run_args(paths, out)
    bad[bad.index("--expect-bundle-digest") + 1] = "0" * 64
    pruned = _validate_args(paths, out)
    pruned[pruned.index("--expect-bundle-digest") + 1] = "0" * 64
    assert sweep.main(["validate-input"] + pruned) == 1
    assert sweep.main(["run"] + bad[1:]) == 1


def test_english_distribution_shape(tmp_path: Path) -> None:
    _, out = _run_world(tmp_path)
    crosstabs = json.loads((out / "crosstabs.json").read_text(encoding="utf-8"))
    dist = crosstabs["english_distributions"]["B-normal/essential_science"]
    assert set(dist) == {"count", "min", "p10", "p25", "p50", "p75", "p90", "max"}
    assert dist["count"] == 192 * 8
    glob = crosstabs["english_distributions"]["input"]
    assert glob["count"] == 4096


_GATE_POOL = [
    {"f": "510.2", "d": "Academic Writing", "k": "Conceptual", "e": 0.95},
    {"f": "613.5", "d": "Knowledge Article", "k": "Conceptual", "t": "Mostly Correct", "e": 0.92},
    {"f": "510.2", "d": "Tutorial", "k": "Procedural", "e": 0.95},
    {
        "f": "720.0",
        "d": "Tutorial",
        "k": "Factual",
        "a": "Irrelevant Content",
        "t": "Mostly Correct",
        "e": 0.85,
    },
    {
        "f": "650.1",
        "d": "Q&A Forum",
        "k": "Procedural",
        "m": "Missing Images or Figures",
        "e": 0.85,
    },
    {
        "f": "810.2",
        "d": "Creative Writing",
        "k": "Factual",
        "t": "Not Applicable/Indeterminate",
        "e": 0.87,
    },
    {"f": "420.0", "d": "Academic Writing", "k": "Factual", "e": 0.5},
    {"f": "320.973/0207", "d": "News Article", "k": "Factual", "e": 0.9},
]


def _gate_vectors(spec: dict[str, Any]):
    """Hand-fed Sweep over GD-distinguishing rows: Irrelevant must not break B⊆D."""
    rows = [
        {
            "f": "510.2",
            "d": "Tutorial",
            "k": "Factual",
            "a": "No Artifacts",
            "m": "No missing content",
            "t": "Mostly Correct",
            "e": 0.95,
            "prefix3": "510",
            "digit1": "5",
            "digit2": "51",
        },
        {
            "f": "720.0",
            "d": "Tutorial",
            "k": "Factual",
            "a": "Irrelevant Content",
            "m": "No missing content",
            "t": "Mostly Correct",
            "e": 0.85,
            "prefix3": "720",
            "digit1": "7",
            "digit2": "72",
        },
        {
            "f": "650.1",
            "d": "Documentation",
            "k": "Procedural",
            "a": "No Artifacts",
            "m": "No missing content",
            "t": "Highly Correct",
            "e": 0.5,
            "prefix3": "650",
            "digit1": "6",
            "digit2": "65",
        },
    ]
    sweep_obj = sweep.Sweep(spec, ["c0"])
    for index, fields in enumerate(rows):
        sweep_obj.rowinfo.append(("c0", "f0.parquet", index, dict(fields)))
        sweep_obj.process_valid("c0", fields, {})
    return sweep_obj


def test_gate_direction_with_irrelevant_rows(spec: dict[str, Any]) -> None:
    """The real failure shape: GD-only rows must not trip the B⊆D invariant.

    Row 2 passes GD (Irrelevant Content) but fails GN: the OLD reversed check
    counted exactly such rows and fired. The fixed check counts B-only rows.
    """
    sweep_obj = _gate_vectors(spec)
    b_gate = sweep_obj.gatepass[("B", "normal")]
    d_gate = sweep_obj.gatepass[("D", "normal")]
    b_only = sum(1 for b, d in zip(b_gate, d_gate, strict=True) if b and not d)
    d_only = sum(1 for b, d in zip(b_gate, d_gate, strict=True) if d and not b)
    assert b_only == 0
    assert d_only == 1
    identities = sweep._check_identities(sweep_obj, 3)
    assert identities["d_gate_superset_b_gate_normal"] == 0
    assert identities["b_final_preserved_in_d_final_essential_practical_normal"] == 0


def test_final_assignment_preserved_across_gate_expansion(spec: dict[str, Any]) -> None:
    sweep_obj = _gate_vectors(spec)
    for component in ("essential_science", "essential_practical", "essential_prose"):
        for tier in ("normal", "strict"):
            key = f"b_final_preserved_in_d_final_{component}_{tier}"
            assert key in sweep._check_identities(sweep_obj, 3)


def test_gate_evaluations_share_no_state(spec: dict[str, Any]) -> None:
    fields = _features(spec)
    first = sweep.gate_gn(fields, spec)
    second = sweep.gate_gn(fields, spec)
    assert first == second == []
    assert first is not second
    other = sweep.gate_gd(fields, spec)
    assert other == []
    first.append("injected")
    assert sweep.gate_gn(fields, spec) == []
    left = sweep.evaluate_policy(dict(fields), "B", "normal", spec)
    right = sweep.evaluate_policy(dict(fields), "B", "normal", spec)
    assert left == right and left is not right
    assert left["matches"] is not right["matches"]


def test_gate_world_full_run(tmp_path: Path) -> None:
    code, out = _run_world(tmp_path, pools=[_GATE_POOL] * 8)
    assert code == 0
    summary = _summary(out)
    assert summary["combos"]["B-normal"]["final"]["essential_practical"] == 64 * 8
    assert summary["combos"]["D-normal"]["final"]["essential_practical"] == 128 * 8
    assert summary["identity_invariants"]["d_gate_superset_b_gate_normal"] == 0


def test_gd_requiring_irrelevant_fails_closed_and_writes_nothing(tmp_path: Path) -> None:
    spec = _mutated_spec(tmp_path, ["Irrelevant Content"])
    world = _write_world(tmp_path / "w")
    out = tmp_path / "w" / "out"
    argv = _run_args(world, out) + ["--policy-spec", str(spec)]
    code = sweep.main(argv)
    assert code == 1
    assert not out.exists()


def _mutated_spec(tmp_path: Path, artifacts: list[str]) -> Path:
    loaded, _ = sweep.load_policy_spec(SPEC_PATH)
    altered = copy.deepcopy(loaded)
    altered["gates"]["GD"]["artifacts"] = artifacts
    path = tmp_path / "mutated_spec.yaml"
    path.write_text(yaml.safe_dump(dict(altered)), encoding="utf-8")
    return path


def test_gate_diagnostic_content(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    spec = _mutated_spec(tmp_path, ["Irrelevant Content"])
    world = _write_world(tmp_path / "w")
    out = tmp_path / "w" / "out"
    argv = _run_args(world, out) + ["--policy-spec", str(spec)]
    assert sweep.main(argv) == 1
    err = capsys.readouterr().err
    assert "GN pass" in err and "GD pass" in err
    assert "|GN-GD|" in err and "|GD-GN|" in err
    assert err.count("row crawl-") <= 20
    assert "F=510.2" in err and "A=No Artifacts" in err
    assert "GN_reasons" in err and "GD_reasons" in err


def test_policy_digest_changes_with_spec(spec: dict[str, Any]) -> None:
    before = sweep.policy_digest_of(spec)
    assert before == sweep.load_policy_spec(SPEC_PATH)[1]
    altered = copy.deepcopy(spec)
    altered["gates"]["GN"]["english_min"] = 0.81
    assert sweep.policy_digest_of(altered) != before
    assert len(before) == 64


def test_spec_caps_frozen(spec: dict[str, Any]) -> None:
    assert spec["caps"] == {
        "max_records": 5000,
        "max_input_bytes": 33554432,
        "max_line_bytes": 1048576,
        "max_output_bytes": 67108864,
        "max_scratch_bytes": 67108864,
        "max_runtime_seconds": 120,
    }
