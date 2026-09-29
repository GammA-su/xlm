"""Evidence-v4 frozen plan, scientific adoption and CLI surface (offline)."""

from __future__ import annotations

import argparse
import copy
import hashlib
import inspect
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from evidence_v4_support import block_network
from xlm.data.evidence_v2 import canonical
from xlm.data.evidence_v4 import frozen, phase_p, verify
from xlm.data.evidence_v4 import transport as tp

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
import evidence_v4 as cli  # noqa: E402

V3_CHILD = REPO / "docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V3.0-CHILD"


def _load(rel: str) -> dict[str, Any]:
    obj: dict[str, Any] = json.loads((REPO / rel).read_text(encoding="utf-8"))
    return obj


def test_real_plan_exact_membership_and_counts() -> None:
    plan = frozen.load_committed_plan()
    adoption = _load(frozen.ADOPTION_PATH)
    science = adoption["scientific_identity"]
    m = [f for f in plan.files if f.arm == "M"]
    t = [f for f in plan.files if f.arm == "T"]
    assert plan.digest == frozen.PLAN_DIGEST
    assert len(m) == 8 and len(t) == 8
    assert [(f.file, list(f.m.window) if f.m else None) for f in m] == [
        (w["file"], w["window"]) for w in science["M_windows"]
    ]
    assert all(f.m and f.m.window[1] - f.m.window[0] == 512 for f in m)
    assert sum(f.m.window[1] - f.m.window[0] for f in m if f.m) == 4096
    assert all(f.m and f.m.projection == ("eai_taxonomy", "quality_signals") for f in m)
    assert [f.file for f in t] == [d["file"] for d in adoption["T"]["development_files"]]
    assert science["selection_total"] == 118
    assert science["metadata_and_text_seed"] == 20260927
    assert science["review_order_seed"] == 20260928
    assert (science["selection_digest"], science["revision"], science["policy_digest"]) == (
        "975ba3dee4af0598e665ea05c69bbe49336c3e3a35fca777a863073190b78474",
        "ce4eccc7e9604667b6d7f32cb6274b8b41f3113d",
        "f4357f61f434d5105d823266153dac6372e122f12767187631e8796b4899dd07",
    )
    m_ops = [o for o in plan.operations if o.arm == "M"]
    t_ops = [o for o in plan.operations if o.arm == "T"]
    assert len(m_ops) == 16 and len(t_ops) == 24
    assert [o.kind for o in m_ops] == [frozen.HEAD, frozen.M_FOOTER] * 8
    assert [o.kind for o in t_ops] == [frozen.HEAD, frozen.T_TRAILER, frozen.T_FOOTER] * 8
    assert [o.seq for o in plan.operations] == list(range(40))


def test_real_plan_ranges_equal_the_v3_child_dry_plans() -> None:
    plan = frozen.load_committed_plan()
    for arm, name in (("M", "arm_m_phase_p_dry.json"), ("T", "arm_t_phase_p_dry.json")):
        dry = json.loads((V3_CHILD / name).read_text(encoding="utf-8"))["payload"]["files"]
        files = [f for f in plan.files if f.arm == arm]
        assert [d["file"] for d in dry] == [f.file for f in files]
        for f, d in zip(files, dry, strict=True):
            assert (f.remote_length, f.strong_etag) == (d["remote_length"], d["strong_etag"])
            ops = [o for o in plan.operations if o.arm == arm and o.ordinal == f.ordinal]
            assert [list(o.range) if o.range else None for o in ops] == [
                o["range"] for o in d["operations"]
            ]
            if arm == "M":
                assert ops[1].expected_footer_length == d["operations"][1]["expected_footer_length"]
                assert (
                    f.m is not None
                    and {
                        "window": list(f.m.window),
                        "projection": list(f.m.projection),
                        "data_chunk_count": f.m.data_chunk_count,
                        "data_payload_bytes": f.m.data_payload_bytes,
                        "data_uncompressed_bytes": f.m.data_uncompressed_bytes,
                    }
                    == d["bindings"]
                )
            else:
                assert f.t is not None and d["bindings"] == {
                    "text_column": "text",
                    "data_span_half_open": [f.t.span_start, f.t.span_end],
                    "data_range_count": f.t.data_range_count,
                    "data_payload_bytes": f.t.data_payload_bytes,
                }


def test_real_plan_contains_no_phase_d_range() -> None:
    plan = frozen.load_committed_plan()
    for op in plan.operations:
        n = op.source_file.remote_length
        if op.kind == frozen.HEAD:
            assert op.range == (0, 3)
        elif op.kind == frozen.M_FOOTER:
            assert op.range is not None and op.range[1] == n - 1
            assert n - op.range[0] <= frozen.BODY_BYTES_PER_RESPONSE_MAX
        elif op.kind == frozen.T_TRAILER:
            assert op.range == (n - 8, n - 1)
        else:
            assert op.kind == frozen.T_FOOTER and op.range is None
        if op.arm == "T" and op.range is not None and op.source_file.t is not None:
            span = op.source_file.t
            assert op.range[1] < span.span_start or op.range[0] >= span.span_end


def test_t_has_24_structural_ranges_once_trailers_are_known() -> None:
    plan = frozen.load_committed_plan()
    concrete: list[tuple[str, tuple[int, int]]] = []
    for op in plan.operations:
        if op.arm != "T":
            continue
        if op.range is not None:
            concrete.append((op.op_id, op.range))
            continue
        trailer = (150000).to_bytes(4, "little") + b"PAR1"
        start, end, length = frozen.derive_t_footer_range(op, trailer)
        n = op.source_file.remote_length
        assert (start, end, length) == (n - 8 - 150000, n - 9, 150000)
        assert op.source_file.t is not None and start >= op.source_file.t.span_end
        concrete.append((op.op_id, (start, end)))
    assert len(concrete) == 24 and len({op_id for op_id, _ in concrete}) == 24


@pytest.mark.parametrize(
    "trailer",
    [
        (0).to_bytes(4, "little") + b"PAR1",
        (frozen.BODY_BYTES_PER_RESPONSE_MAX + 1).to_bytes(4, "little") + b"PAR1",
        (150000).to_bytes(4, "little") + b"PAR2",
        b"PAR1",
    ],
)
def test_t_footer_derivation_refuses_invalid_trailers(trailer: bytes) -> None:
    op = frozen.load_committed_plan().operation("T-00-footer")
    with pytest.raises(frozen.PlanError):
        frozen.derive_t_footer_range(op, trailer)


def test_real_t_footer_derivation_can_never_reach_the_frozen_text_chunk() -> None:
    for op in frozen.load_committed_plan().operations:
        if op.kind != frozen.T_FOOTER:
            continue
        assert op.source_file.t is not None
        n = op.source_file.remote_length
        largest = frozen.BODY_BYTES_PER_RESPONSE_MAX
        start, _, _ = frozen.derive_t_footer_range(op, largest.to_bytes(4, "little") + b"PAR1")
        assert start == n - 8 - largest > op.source_file.t.span_end
        reaching = n - 8 - (op.source_file.t.span_end - 1)
        with pytest.raises(frozen.PlanError):
            frozen.derive_t_footer_range(op, reaching.to_bytes(4, "little") + b"PAR1")


def _resealed(mutate: Any) -> dict[str, Any]:
    obj: dict[str, Any] = copy.deepcopy(
        canonical.loads_bytes_strict((REPO / frozen.PLAN_PATH).read_bytes())
    )
    mutate(obj)
    obj["digest"] = canonical.self_digest(obj)
    return obj


@pytest.mark.parametrize(
    "mutate",
    [
        lambda o: o["operations"][1]["range"].__setitem__(0, o["operations"][1]["range"][0] - 1),
        lambda o: o["operations"].append(dict(o["operations"][0], seq=40)),
        lambda o: o["files"][0].__setitem__("strong_etag", 'W/"weak"'),
        lambda o: o["source"].__setitem__("revision", "main"),
        lambda o: o["limits"].__setitem__("physical_attempts_per_arm_max", 999),
        lambda o: o["files"].pop(8),
        lambda o: o["files"][0]["bindings"].__setitem__("window", [0, 511]),
        lambda o: o.__setitem__("execution_root", "C:\\elsewhere"),
    ],
)
def test_real_plan_refuses_any_edit(mutate: Any) -> None:
    with pytest.raises(frozen.PlanError):
        frozen.validate_plan(_resealed(mutate), synthetic=False)


def test_unsealed_edit_fails_the_digest_check() -> None:
    obj = canonical.loads_bytes_strict((REPO / frozen.PLAN_PATH).read_bytes())
    obj["operations"][0]["range"] = [0, 7]
    with pytest.raises(frozen.PlanError, match="self-digest"):
        frozen.validate_plan(obj, synthetic=False)


def test_real_plan_is_not_a_synthetic_plan_and_vice_versa() -> None:
    obj = canonical.loads_bytes_strict((REPO / frozen.PLAN_PATH).read_bytes())
    with pytest.raises(frozen.PlanError):
        frozen.validate_plan(obj, synthetic=True)
    fake = _resealed(lambda o: o.__setitem__("synthetic", True))
    with pytest.raises(frozen.PlanError, match="synthetic"):
        frozen.validate_plan(fake, synthetic=True)


def test_verify_reproduces_every_committed_binding() -> None:
    summary = verify.verify_repository()
    assert summary["protocol_sha256"] == frozen.PROTOCOL_SHA256
    assert summary["freeze_digest"] == frozen.FREEZE_DIGEST
    assert summary["plan_digest"] == frozen.PLAN_DIGEST
    assert summary["scientific_adoption_digest"] == frozen.SCIENTIFIC_ADOPTION_DIGEST
    assert (summary["M_logical_operations"], summary["T_logical_operations"]) == (16, 24)
    protocol = (REPO / frozen.PROTOCOL_PATH).read_bytes()
    assert hashlib.sha256(protocol).hexdigest() == frozen.PROTOCOL_SHA256
    freeze = _load(frozen.FREEZE_PATH)
    assert freeze["operational_limits"] == frozen.LIMITS
    assert freeze["network"] == frozen.NETWORK


# -- CLI / public API surface -------------------------------------------------


@pytest.mark.parametrize(
    "argv",
    [
        ["phase-p", "--confirm-plan-digest", frozen.PLAN_DIGEST, "--url", "https://x"],
        ["phase-p", "--confirm-plan-digest", frozen.PLAN_DIGEST, "--range", "0-3"],
        ["phase-p", "--confirm-plan-digest", frozen.PLAN_DIGEST, "--file", "a.parquet"],
        ["phase-p", "--confirm-plan-digest", frozen.PLAN_DIGEST, "--etag", '"x"'],
        ["phase-p", "--confirm-plan-digest", frozen.PLAN_DIGEST, "--root", "C:\\x"],
        ["phase-p", "--confirm-plan-digest", frozen.PLAN_DIGEST, "--force-plan"],
        ["phase-p", "--confirm", frozen.PLAN_DIGEST],
        ["phase-p"],
        ["fetch", "https://huggingface.co/x"],
    ],
)
def test_cli_has_no_arbitrary_input(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        cli.build_parser().parse_args(argv)
    assert exit_info.value.code == 2


def test_cli_options_are_exactly_the_confirmation() -> None:
    parser = cli.build_parser()
    sub = next(a for a in parser._actions if a.dest == "command")
    choices: dict[str, argparse.ArgumentParser] = dict(sub.choices or {})
    assert sorted(choices) == ["phase-p", "phase-p-status", "show-plan", "verify"]
    live_options = sorted(
        o
        for a in choices["phase-p"]._actions
        for o in a.option_strings
        if o not in ("-h", "--help")
    )
    assert live_options == ["--confirm-plan-digest"]
    for name in ("verify", "show-plan", "phase-p-status"):
        assert [o for a in choices[name]._actions for o in a.option_strings] == ["-h", "--help"]


def test_public_entry_points_take_no_url_file_range_or_etag() -> None:
    assert list(inspect.signature(phase_p.run_live).parameters) == ["confirm_plan_digest"]
    assert list(inspect.signature(phase_p.run_offline).parameters) == [
        "root",
        "plan",
        "transport",
        "sleep",
        "clock",
    ]
    assert list(inspect.signature(phase_p.inspect).parameters) == ["root"]


def test_wrong_plan_digest_refuses_without_touching_the_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    block_network(monkeypatch)
    root = tmp_path / "frozen-root"
    monkeypatch.setattr(phase_p, "live_root", lambda: root)
    wrong = "0" * 64
    assert cli.main(["phase-p", "--confirm-plan-digest", wrong]) == 1
    assert "does not equal the frozen v4 plan digest" in capsys.readouterr().err
    assert not root.exists()
    with pytest.raises(phase_p.RefusedError):
        phase_p.run_live(confirm_plan_digest=frozen.PLAN_DIGEST.upper())
    assert not root.exists()


class _RecordingLive:
    """Stands in for the live transport class; answers the first call with a wrong ETag."""

    calls: list[dict[str, Any]] = []

    def open(
        self, url: str, *, start: int, end: int, timeout_seconds: float, deadline: float
    ) -> Any:
        from evidence_v4_support import FakeResponse

        self.calls.append({"url": url, "start": start, "end": end, "timeout": timeout_seconds})
        n = 290684578
        return FakeResponse(
            206, {"Content-Range": f"bytes 0-3/{n}", "ETag": '"not-the-frozen-etag"'}, b"PAR1"
        )


def test_cli_live_path_issues_exactly_the_first_frozen_request_and_stops(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    block_network(monkeypatch)
    root = tmp_path / "frozen-root"
    monkeypatch.setattr(phase_p, "live_root", lambda: root)
    monkeypatch.setattr(tp, "LiveHttpsTransport", _RecordingLive)
    _RecordingLive.calls = []
    assert cli.main(["phase-p", "--confirm-plan-digest", frozen.PLAN_DIGEST]) == 3
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "INCOMPLETE" and result["run_status"] == "STOPPED"
    assert "ETag" in result["stop_reason"]
    assert _RecordingLive.calls == [
        {
            "url": "https://huggingface.co/datasets/EssentialAI/essential-web-v1.0/resolve/"
            "ce4eccc7e9604667b6d7f32cb6274b8b41f3113d/data/crawl=CC-MAIN-2014-15/"
            "train-01860-of-02772.parquet",
            "start": 0,
            "end": 3,
            "timeout": 120,
        }
    ]
    receipt = json.loads((root / phase_p.RECEIPT).read_text(encoding="utf-8"))
    assert receipt["status"] == "INCOMPLETE" and receipt["plan_digest"] == frozen.PLAN_DIGEST
    assert receipt["totals"]["all"]["physical_attempts"] == 1
    assert cli.main(["phase-p-status"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "STOPPED"
    assert cli.main(["phase-p", "--confirm-plan-digest", frozen.PLAN_DIGEST]) == 1
    assert "STOPPED" in capsys.readouterr().err
    assert len(_RecordingLive.calls) == 1


def test_cli_verify_and_show_plan(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["verify"]) == 0
    assert json.loads(capsys.readouterr().out)["plan_digest"] == frozen.PLAN_DIGEST
    assert cli.main(["show-plan"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert len([line for line in lines if line.split()[0].isdigit()]) == 40
