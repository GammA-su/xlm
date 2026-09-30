"""Whole-pass malformed budget for the fast Essential-Web campaign (Batch-3 f00110).

Authored synthetic fixtures and the loopback endpoint of the fast-campaign
tests only; no network. The malformed row positions of the real f00110 replay
are used as positions (no content). These tests prove the local worker's
malformed decision, its restart and authorization consequences, and that the
frozen adapter classification is unchanged; they are not live evidence.
"""

from __future__ import annotations

import hashlib
import json
import random
import shutil
from pathlib import Path
from typing import Any

import pytest

from test_essential_web_campaign_runner import Auto, object_hits, receipts
from test_essential_web_campaign_runner import auto as auto
from test_essential_web_fast import (
    REPO,
    REVISION,
    VIEWS,
    adapt_local,
    expected_documents,
    parquet_bytes,
    row,
    served,  # noqa: F401 -- shared authored fixtures
)
from test_essential_web_fast import fast_tool as fast_tool
from test_essential_web_fast import loopback_only as loopback_only
from xlm.data.adapters.essential_web_selector import FrozenEssentialWebSelector
from xlm.data.adapters.malformed import MalformedCounter, MalformedLimitError
from xlm.data.adapters.mix01_adapters import (
    ADAPTERS_BY_ID,
    EssentialWebMalformedRowError,
    EssentialWebSelectorRejectedError,
)
from xlm.data.evidence_v2 import canonical
from xlm.data.sources import essential_web_campaign_runner as runner
from xlm.data.sources import essential_web_local as local
from xlm.data.sources import essential_web_recovery as recovery

#: Malformed row positions of the real f00110 replay (78,689 rows); positions only.
F00110_ROWS = 78_689
F00110_MALFORMED = (
    202, 242, 282, 1241, 1531, 5037, 7578, 8413, 14204, 16914, 17016, 17557, 18203, 19064,
    19304, 19322, 20617, 24399, 25293, 26431, 26995, 30994, 33930, 35144, 36163, 36843,
    38907, 40580, 40611, 49876, 52898, 54079, 57035, 57397, 60527, 64818, 67581, 68678,
    72327, 73822, 77526, 78371,
)  # fmt: skip
#: The value shapes observed in f00110's malformed rows and their frozen reason codes.
F00110_SHAPES = {
    "knowledge_abstain": ({"k": "Abstain"}, "unknown_label:k"),
    "knowledge_metacognitive": ({"k": "Metacognitive"}, "unknown_label:k"),
    "fdc_abstain": ({"f": "-1"}, "invalid_fdc_syntax"),
    "fdc_repeated_decimal": ({"f": "641.563.2"}, "invalid_fdc_syntax"),
    "fdc_two_digit": ({"f": "92"}, "invalid_fdc_syntax"),
    "both": ({"f": "-1", "k": "Abstain"}, "invalid_fdc_syntax, unknown_label:k"),
}


def shaped(index: int, change: dict[str, str], kind: str = "science") -> dict[str, Any]:
    """An otherwise admissible authored row carrying one f00110 value shape."""
    record = row(kind, index)
    taxonomy = record["eai_taxonomy"]
    if "f" in change:
        taxonomy["free_decimal_correspondence"]["primary"]["code"] = change["f"]
    if "k" in change:
        taxonomy["bloom_knowledge_domain"]["primary"]["label"] = change["k"]
    return record


def frozen_observe(counter: MalformedCounter, malformed: bool, pass_rows: int) -> None:
    """The per-row rule exactly as the worker applied it before the amendment."""
    counter.observe(malformed)


def first_stop(observe: Any, rows: int, bad: set[int]) -> int | None:
    counter = MalformedCounter()
    for index in range(rows):
        try:
            observe(counter, index in bad, rows)
        except MalformedLimitError:
            return index
    return None


# --------------------------------------------------------- classification


@pytest.mark.parametrize("shape", sorted(F00110_SHAPES))
def test_f00110_value_shapes_stay_malformed_and_rejected(shape: str) -> None:
    """The frozen contract is unchanged: these rows stay malformed and are never admitted."""
    change, codes = F00110_SHAPES[shape]
    record = shaped(0, change)
    decision = FrozenEssentialWebSelector.load().decide(record)
    assert (decision.final, decision.stage) == ("rejected", "validity")
    assert ", ".join(decision.reasons) == codes
    for view in VIEWS:
        with pytest.raises(EssentialWebMalformedRowError) as caught:
            ADAPTERS_BY_ID["essential_web_bnormal"](view).adapt(
                record, source_file="data/a.parquet", source_row=0, source_revision=REVISION
            )
        assert str(caught.value) == f"essential_web_selector_value: {codes}"


def test_policy_rejection_never_consumes_the_budget_and_true_malformed_does(
    tmp_path: Path,
) -> None:
    name = "data/a.parquet"
    rows = [row("rejected", index) for index in range(150)]  # gate rejections only
    rows[7] = row("science", 7)
    missing = row("science", 9)
    del missing["pid"]  # renderer-level unusable record
    rows[9] = missing
    path = tmp_path / "a.parquet"
    path.write_bytes(parquet_bytes(rows, 50))
    result = adapt_local(path, tmp_path / "out", name, '"e"')
    for view in VIEWS:
        codes = result["views"][view]["rejection_counts_by_code"]
        assert codes["EssentialWebMalformedRowError"] == 1
        ledger = local.read_ledger(
            tmp_path / "out" / view / local.LEDGER_FILENAME,
            result["views"][view]["rejections_uncompressed_bytes"],
        )
        bad = [json.loads(line) for line in ledger.splitlines() if b'"malformed"' in line]
        assert [(entry["source_row"], entry["reason"]) for entry in bad] == [
            (9, "essential_web_unusable_record")
        ]
    assert result["views"]["essential_science"]["documents"] == 1
    with pytest.raises(EssentialWebSelectorRejectedError):
        ADAPTERS_BY_ID["essential_web_bnormal"]("essential_prose").adapt(
            row("rejected", 0), source_file=name, source_row=0, source_revision=REVISION
        )


# ------------------------------------------------------------ budget rule


def test_exact_f00110_condition() -> None:
    """Three malformed rows in the first 283 stopped a file that is 0.053% malformed."""
    bad = set(F00110_MALFORMED)
    assert first_stop(frozen_observe, F00110_ROWS, bad) == 282
    assert first_stop(local.observe_malformed, F00110_ROWS, bad) is None
    assert len(bad) * 100 <= F00110_ROWS  # 42 of 78,689 is inside the frozen 1% budget


def test_genuine_malformed_over_one_percent_still_aborts() -> None:
    spaced = {index * 90 for index in range(11)}
    assert first_stop(local.observe_malformed, 1000, spaced) == 900  # the eleventh row
    assert first_stop(local.observe_malformed, 1000, set(sorted(spaced)[:10])) is None
    # Schema drift or corruption (every row bad) still stops within the first 1% of rows.
    assert first_stop(local.observe_malformed, F00110_ROWS, set(range(F00110_ROWS))) == 786
    assert first_stop(local.observe_malformed, 50, {0, 1, 2}) == 2  # tiny pass: third row


def test_every_whole_pass_stop_is_a_frozen_stop_and_the_end_decision_is_equal() -> None:
    rng = random.Random(110)
    for _ in range(1500):
        rows = rng.choice((1, 40, 99, 100, 150, 250, 400, 1000))
        density = rng.choice((0.0, 0.002, 0.008, 0.012, 0.05, 0.5))
        bad = {index for index in range(rows) if rng.random() < density}
        if rng.random() < 0.3:
            bad |= set(rng.sample(range(min(rows, 300)), min(rows, 3)))
        frozen = first_stop(frozen_observe, rows, bad)
        amended = first_stop(local.observe_malformed, rows, bad)
        if amended is not None:
            assert frozen is not None and frozen <= amended
            assert len({i for i in bad if i <= amended}) * 100 > rows
        if rows >= 100:
            assert (amended is not None) == (len(bad) * 100 > rows)
        else:
            assert amended == frozen


# ----------------------------------------------------------- local worker


def clustered(rows: int, bad: dict[int, str]) -> list[dict[str, Any]]:
    kinds = ("science", "practical", "prose", "unassigned", "rejected")
    return [
        shaped(index, F00110_SHAPES[bad[index]][0])
        if index in bad
        else row(kinds[index % 5], index)
        for index in range(rows)
    ]


def test_worker_completes_a_dense_prefix_and_writes_the_frozen_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    name = "data/a.parquet"
    rows = clustered(300, {100: "both", 101: "knowledge_abstain", 250: "fdc_abstain"})
    path = tmp_path / "a.parquet"
    path.write_bytes(parquet_bytes(rows, 100))
    with monkeypatch.context() as patch:
        patch.setattr(local, "observe_malformed", frozen_observe)
        with pytest.raises(MalformedLimitError):
            adapt_local(path, tmp_path / "before", name, '"e"')
    result = adapt_local(path, tmp_path / "after", name, '"e"')
    for view in VIEWS:
        written = (tmp_path / "after" / view / "documents.jsonl").read_bytes()
        assert written == expected_documents(name, rows, view)
        entry = result["views"][view]
        assert entry["rejection_counts_by_code"]["EssentialWebMalformedRowError"] == 3
        ledger = local.read_ledger(
            tmp_path / "after" / view / local.LEDGER_FILENAME,
            entry["rejections_uncompressed_bytes"],
        )
        bad = [json.loads(line) for line in ledger.splitlines() if b'"malformed"' in line]
        assert [line["source_row"] for line in bad] == [100, 101, 250]
        assert {line["rejection_code"] for line in bad} == {"EssentialWebMalformedRowError"}
    again = adapt_local(path, tmp_path / "again", name, '"e"')
    assert again["views"] == result["views"]  # deterministic
    over = clustered(300, dict.fromkeys((100, 101, 150, 250), "fdc_abstain"))
    path.write_bytes(parquet_bytes(over, 100))
    with pytest.raises(MalformedLimitError):
        adapt_local(path, tmp_path / "over", name, '"e"')  # 4 of 300 exceeds 1%


# --------------------------------------------------------------- restart


def test_batch3_restart_plan_reuses_local_sources_and_resumes_partials(
    tmp_path: Path, fast_tool: Any
) -> None:
    """f00110 retained durably, f00123 complete in scratch, three verified partials."""
    body = b"PAR1" + bytes(range(256)) * 64 + b"PAR1"
    durable = local.Unit("f00110", "d/110", None, tmp_path / "110.part", tmp_path / "110.s", {})
    complete = local.Unit("f00123", "d/123", "http://x", tmp_path / "123.p", tmp_path / "123.s", {})
    complete.partial.write_bytes(body)
    complete.state.write_text(
        json.dumps(
            {"complete": True, "length": len(body), "sha256": hashlib.sha256(body).hexdigest()}
        )
    )
    units = [durable, complete]
    for rank, verified in ((122, 4096), (125, 2048), (126, 1024)):
        unit = local.Unit(
            f"f{rank:05d}",
            f"d/{rank}",
            "http://x",
            tmp_path / f"{rank}.p",
            tmp_path / f"{rank}.s",
            {},
        )
        unit.partial.write_bytes(body[: verified + 7])
        unit.state.write_text(
            json.dumps(
                {
                    "length": len(body),
                    "verified_bytes": verified,
                    "prefix_sha256": hashlib.sha256(body[:verified]).hexdigest(),
                }
            )
        )
        units.append(unit)
    plan = fast_tool.restart_plan(units, 1 << 20)
    assert plan["local_complete_reuse_keys"] == ["f00110", "f00123"]
    assert plan["partial_resume_keys"] == ["f00122", "f00125", "f00126"]
    assert plan["fresh_download"] == 0 and plan["resumable_verified_bytes"] == 4096 + 2048 + 1024
    assert plan["known_network_bytes"] == 3 * len(body) - (4096 + 2048 + 1024)


# ---------------------------------------------------------------- runner


def replace_second_member(auto: Auto, bad: dict[int, str]) -> str:
    """The same ten admitted rows, then policy rejections, with a dense malformed prefix."""
    name = str(auto.campaign().members(0)[1])
    rows = list(auto.world.rows[name])
    rows += [row("rejected", index) for index in range(len(rows), 200)]
    for index, shape in bad.items():
        rows[index] = shaped(index, F00110_SHAPES[shape][0])
    auto.world.state.files[name] = parquet_bytes(rows, 100)
    return name


def test_runner_stops_for_review_and_resumes_after_the_reviewed_amendment(
    auto: Auto, monkeypatch: pytest.MonkeyPatch
) -> None:
    replace_second_member(auto, {100: "both", 101: "knowledge_metacognitive"})
    digest = auto.prepare("--max-batches", "2")["envelope"]["digest"]
    with monkeypatch.context() as patch:
        patch.setattr(local, "observe_malformed", frozen_observe)
        outcome = auto.run(digest)
        assert outcome["outcome"] == runner.HUMAN_REVIEW and auto.calls == [0]
        assert "f00001" not in receipts(auto)  # the root unit is never sealed
        events = auto.world.root / "plans/ew-fast/b0000/events.jsonl"
        failed = [e for e in runner.read_events(events) if e["event"] == "failed"]
        assert [(e["key"], e["exception"]) for e in failed] == [("f00001", "MalformedLimitError")]
        requests = len(auto.world.state.requests)
        assert auto.classify(0)["state"] == runner.HUMAN_REVIEW
        assert auto.run(digest)["outcome"] == runner.HUMAN_REVIEW and auto.calls == [0]
        assert len(auto.world.state.requests) == requests
    # Reviewed: the operator resumes the batch with the fast driver under the amendment.
    assert auto.world.run("run", "--batch", "0") == 0
    assert object_hits(auto, 0) == [1, 1]  # both local sources reused; nothing transferred again
    sealed = receipts(auto)
    assert set(sealed) == {"f00000", "f00001"} and sealed["f00001"]["malformed_rows"] == 2
    resumed = auto.run(digest)
    assert resumed["outcome"] == runner.ENVELOPE_EXHAUSTED and auto.calls == [0, 1]


def test_envelope_bound_to_the_previous_code_is_refused_and_a_new_one_runs(
    auto: Auto, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    copy = tmp_path / "code"
    for name in (*auto.tool.recovery.CODE_FILES, *auto.tool.RUNNER_CODE_FILES):
        (copy / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(REPO / name, copy / name)
    monkeypatch.setattr(auto.tool, "CODE_REPO", copy)
    old = auto.prepare("--max-batches", "1")["envelope"]
    for name in ("essential_web_local.py", "essential_web_recovery.py"):
        with (copy / "src/xlm/data/sources" / name).open("a", encoding="utf-8") as stream:
            stream.write("# reviewed amendment\n")
    (copy / recovery.MALFORMED_FIX).parent.mkdir(parents=True)
    (copy / recovery.MALFORMED_FIX).write_text(json.dumps({"digest": "c" * 64}))
    outcome = auto.run(old["digest"])
    assert outcome["outcome"] == runner.REFUSED and not auto.calls
    assert not auto.world.state.requests
    text = " ".join(outcome["reasons"])
    for path in (
        "code.compatibility.records",
        "code.running_sha256.src/xlm/data/sources/essential_web_local.py",
        "code.running_sha256.src/xlm/data/sources/essential_web_recovery.py",
    ):
        assert path in text
    new = auto.prepare("--max-batches", "1")["envelope"]
    assert new["digest"] != old["digest"] and new["children"] == old["children"]
    assert auto.run(new["digest"])["outcome"] == runner.ENVELOPE_EXHAUSTED and auto.calls == [0]


# --------------------------------------------------------- compatibility


def test_malformed_amendment_extends_the_chain_only_with_its_own_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    names = sorted({n for _, _, allowed in recovery.COMPATIBILITY for n in allowed} | {"c.py"})
    states = [dict.fromkeys(names, "old")]
    for index, (_, _, allowed) in enumerate(recovery.COMPATIBILITY):
        states.append({**states[-1], **dict.fromkeys(allowed, f"v{index}")})
    common = {"campaign": "a" * 64, "recovery_digest": "b" * 64}

    def write(links: list[dict[str, Any]]) -> None:
        for (relative, kind, _), link in zip(recovery.COMPATIBILITY, links, strict=True):
            value = {"kind": kind, **common, **link}
            value["digest"] = canonical.digest(value)
            (tmp_path / relative).parent.mkdir(parents=True, exist_ok=True)
            (tmp_path / relative).write_text(json.dumps(value))

    links = [{"previous_code": states[i], "code": states[i + 1]} for i in range(len(states) - 1)]
    manifest = {"code": states[0], "digest": "b" * 64, "campaign": "a" * 64}
    write(links)
    monkeypatch.setattr(recovery, "code_identity", lambda _: states[-1])
    assert recovery.compatible_code(tmp_path, manifest)
    wider = {**states[-1], "c.py": "v2"}
    write([*links[:-1], {"previous_code": states[-2], "code": wider}])
    monkeypatch.setattr(recovery, "code_identity", lambda _: wider)
    assert not recovery.compatible_code(tmp_path, manifest)
    write([*links[:-1], {"previous_code": states[-3], "code": states[-1]}])
    monkeypatch.setattr(recovery, "code_identity", lambda _: states[-1])
    assert not recovery.compatible_code(tmp_path, manifest)


def test_committed_malformed_amendment_binds_the_running_code() -> None:
    record = json.loads((REPO / recovery.MALFORMED_FIX).read_bytes())
    windows = json.loads((REPO / recovery.WINDOWS_FIX).read_bytes())
    manifest = json.loads((REPO / recovery.MANIFEST).read_bytes())
    assert record["digest"] == canonical.digest({k: v for k, v in record.items() if k != "digest"})
    assert record["kind"] == "essential_web_malformed_whole_pass_v1"
    assert record["code"] == recovery.code_identity(REPO)
    assert record["previous_code"] == windows["code"]
    assert record["previous_digest"] == windows["digest"]
    assert record["malformed_policy"] == local.MALFORMED_POLICY
    assert record["campaign"] == manifest["campaign"] == windows["campaign"]
    changed = {n for n in record["code"] if record["code"][n] != record["previous_code"][n]}
    assert changed == set(recovery.COMPATIBILITY[-1][2])
    assert recovery.compatible_code(REPO, manifest)
