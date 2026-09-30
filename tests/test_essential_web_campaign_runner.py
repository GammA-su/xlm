"""Bounded automatic Essential-Web campaign runner.

Authored synthetic fixtures and the loopback endpoint of the fast-campaign
tests only. The runner drives the real fast driver in-process (the production
executor starts the same driver as a child process); these tests prove the
orchestration, authorization and stop logic, not live endpoint behaviour.
"""

from __future__ import annotations

import dataclasses
import hashlib
import importlib
import io
import json
import os
import random
import shutil
import socket
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

import test_essential_web_bulk as historical_tests
from test_essential_web_fast import (
    PRACTICAL,
    PROSE,
    REPO,
    REVISION,
    SCIENCE,
    World,
    make_fast_world,
    parquet_bytes,
    row,
    served,  # noqa: F401 -- shared authored fixtures
)
from test_essential_web_fast import loopback_only as loopback_only
from xlm.data.acquisition import source_parquet as sp
from xlm.data.evidence_v2 import canonical
from xlm.data.sources import essential_web_campaign_runner as runner
from xlm.data.sources.essential_web_bulk import GIB, MIB, BulkError

KINDS = historical_tests.KINDS


def view_bytes_per_batch(kind: str) -> int:
    """Two files of ten rows: the rows of one kind are ``i`` and ``i + 5``."""
    first = KINDS.index(kind)
    return 2 * (
        historical_tests.canonical_bytes(kind, first)
        + historical_tests.canonical_bytes(kind, first + 5)
    )


@dataclass
class Auto:
    world: World
    tool: Any
    ctx: Any
    stream: io.StringIO = field(default_factory=io.StringIO)
    calls: list[int] = field(default_factory=list)

    def main(self, *argv: str) -> int:
        return int(
            self.tool.main(
                [
                    "--campaign",
                    str(self.world.path),
                    "--data-root",
                    str(self.world.root),
                    "--scratch-root",
                    str(self.world.scratch),
                    *argv,
                ]
            )
        )

    def prepare(self, *argv: str) -> dict[str, Any]:
        """Guards are bound at zero unless a test sets them: the test volume is small."""
        output = io.StringIO()
        original = sys.stdout
        sys.stdout = output
        zero = ("--min-durable-free-gib", "0", "--min-scratch-free-gib", "0")
        try:
            code = self.main("prepare-auto", "--operator", "t", "--json", *zero, *argv)
        finally:
            sys.stdout = original
        assert code == 0, output.getvalue()
        report: dict[str, Any] = json.loads(output.getvalue())
        return report

    def execute(self, batch: int) -> int:
        self.calls.append(batch)
        return self.world.run("run", "--batch", str(batch))

    def run(self, digest: str, operator: str = "t", executor: Any = None) -> dict[str, Any]:
        outcome: dict[str, Any] = self.tool.run_auto(
            self.ctx, digest, operator, executor or self.execute, self.stream
        )
        return outcome

    def campaign(self) -> Any:
        return self.world.campaign()

    def log(self) -> list[dict[str, Any]]:
        return runner.EventLog(self.world.root / "plans/ew-fast/auto/campaign-runner.jsonl").read()

    def events(self, kind: str) -> list[dict[str, Any]]:
        return [record for record in self.log() if record["event"] == kind]

    def complete_manually(self, batch: int) -> None:
        self.world.authorize(batch)
        assert self.world.run("run", "--batch", str(batch)) == 0

    def classify(self, batch: int) -> dict[str, Any]:
        result: dict[str, Any] = self.tool.classify(self.campaign(), batch, self.log())
        return result


def make_auto(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, endpoint: Any) -> Auto:
    """Four batches; prose is met by batch 0, practical by batch 1 and science by batch 2."""
    required = {
        SCIENCE: 3 * view_bytes_per_batch("science") - 1,
        PRACTICAL: 2 * view_bytes_per_batch("practical") - 1,
        PROSE: 1,
    }
    world = make_fast_world(tmp_path, monkeypatch, endpoint, required, max_batches=4)
    tool = importlib.import_module("essential_web_campaign")
    world.pass_benchmark()
    return Auto(world=world, tool=tool, ctx=tool.Context(world.path, world.root, world.scratch))


@pytest.fixture
def auto(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, served: Any) -> Auto:  # noqa: F811
    return make_auto(tmp_path, monkeypatch, served)


def object_hits(auto: Auto, batch: int) -> list[int]:
    return [auto.world.state.hits("object", name) for name in auto.campaign().members(batch)]


def receipts(auto: Auto) -> dict[str, dict[str, Any]]:
    return auto.world.receipts()


# --------------------------------------------------------------------- normal


def test_runs_from_the_next_batch_until_every_target_is_met_then_stops(auto: Auto) -> None:
    auto.complete_manually(0)
    status = auto.campaign()
    state, decision = auto.tool.tool.campaign_state(status)
    assert state["complete_batches"] == 1
    assert decision["views"][PROSE]["status"] == "SUFFICIENT"  # already sufficient
    assert decision["views"][PRACTICAL]["status"] == "TOP_UP"
    report = auto.prepare("--max-batches", "3")
    envelope = report["envelope"]
    assert envelope["range"]["start_batch"] == 1 and envelope["range"]["end_batch_exclusive"] == 4
    outcome = auto.run(envelope["digest"])
    assert outcome["outcome"] == runner.FIRST_PASS_COMPLETE and outcome["exit_code"] == 0
    assert outcome["completed_this_session"] == [1, 2] and auto.calls == [1, 2]
    # Practical became sufficient after batch 1, science last after batch 2.
    done = auto.events("batch_complete")
    assert [entry["batch"] for entry in done] == [1, 2]
    assert done[0]["sufficiency"][PRACTICAL]["status"] == "SUFFICIENT"
    assert done[0]["sufficiency"][SCIENCE]["status"] == "TOP_UP"
    assert all(entry["status"] == "SUFFICIENT" for entry in done[1]["sufficiency"].values())
    # No extra batch: batch 3 was never planned or fetched.
    assert not (auto.world.root / "plans/ew-fast/b0003").exists()
    assert object_hits(auto, 3) == [0, 0]
    assert all(hits == 1 for batch in (0, 1, 2) for hits in object_hits(auto, batch))
    assert len(receipts(auto)) == 6
    text = auto.stream.getvalue()
    assert "ESSENTIAL-WEB FIRST-PASS ACQUISITION COMPLETE" in text
    assert "C05 status             NOT RUN" in text and "TRAINING NOT YET PERMITTED" in text
    authorization = json.loads(
        (auto.world.root / "plans/ew-fast/b0001/authorization.json").read_bytes()
    )
    assert authorization["authorization_digest"] == envelope["children"][0]["authorization_digest"]
    assert authorization["operator"] == f"t (auto {envelope['digest'][:16]})"
    kinds = [record["event"] for record in auto.log()]
    assert kinds[:3] == ["operator_authorization", "automation_start", "batch_start"]
    assert kinds[-1] == "safe_stop"
    # The log carries metadata only, never document text.
    assert "Authored fixture paragraph" not in (
        auto.world.root / "plans/ew-fast/auto/campaign-runner.jsonl"
    ).read_text(encoding="utf-8")
    # A restart after completion starts nothing, and prepare has nothing to automate.
    again = auto.run(envelope["digest"])
    assert again["outcome"] == runner.FIRST_PASS_COMPLETE and auto.calls == [1, 2]
    assert auto.main("prepare-auto", "--operator", "t") == 3


# -------------------------------------------------------------- authorization


def test_prepare_is_offline_bounded_and_binds_each_child_exactly(
    auto: Auto, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("prepare-auto attempted a network connection")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    report = auto.prepare("--max-batches", "20")
    envelope = report["envelope"]
    assert not auto.world.state.requests
    assert envelope["range"] == {
        "start_batch": 0,
        "end_batch_exclusive": 4,
        "max_batches": 20,
        "clamped_by_campaign_ceiling": True,
    }
    assert report["membership_proof"]["all_equal"] is True
    config = auto.world.config
    assert auto.tool.default_guards(auto.campaign(), None, None) == {
        "min_durable_free_bytes": 128 * GIB,
        "min_scratch_free_bytes": config["scratch"]["cap_bytes"]
        + config["scratch"]["min_free_bytes"],
    }
    assert report["written"] and Path(report["written"]).is_file()
    # The envelope is deterministic and its dry form writes nothing.
    assert auto.prepare("--max-batches", "20", "--dry")["envelope"] == envelope
    # Each bound child equals what the manual Prepare later writes for that batch.
    for child in envelope["children"]:
        record = auto.world.prepare(int(child["batch"]))
        assert record["authorization_digest"] == child["authorization_digest"]
        assert record["membership_digest"] == child["membership_digest"]
        assert record["plan_hash"] == child["plan_hash"]
    assert not auto.world.state.requests and not receipts(auto)
    identity = envelope["identity"]
    assert identity["campaign"] == auto.world.config["digest"]
    assert identity["source"]["revision"] == REVISION
    assert identity["selector"] == auto.world.config["binding"]["selector"]
    assert "scripts/essential_web_campaign.py" in identity["code"]["running_sha256"]
    assert identity["c05"]["status"] == "NOT RUN"
    # Bounds: no unbounded envelope, only from the authoritative next batch.
    assert auto.main("prepare-auto", "--operator", "t", "--max-batches", "21") == 1
    assert auto.main("prepare-auto", "--operator", "t", "--max-batches", "0") == 1
    assert auto.main("prepare-auto", "--operator", "t", "--start-batch", "1") == 1
    assert auto.main("prepare-auto", "--operator", " ") == 1


def resealed(config: dict[str, Any], change: Any) -> dict[str, Any]:
    altered: dict[str, Any] = json.loads(json.dumps(config))
    change(altered)
    altered["digest"] = canonical.digest({k: v for k, v in altered.items() if k != "digest"})
    return altered


def test_authorization_refusals_start_nothing(auto: Auto) -> None:
    digest = auto.prepare("--max-batches", "2")["envelope"]["digest"]

    def refused(outcome: dict[str, Any], text: str) -> None:
        assert outcome["outcome"] == runner.REFUSED and outcome["exit_code"] == 1, outcome
        assert text in " ".join(outcome["reasons"]), outcome
        assert not auto.calls and not auto.world.state.requests

    refused(auto.run("0" * 64), "no prepared envelope")
    refused(auto.run(digest, operator="someone else"), "operator")
    envelope_path = auto.world.root / f"plans/ew-fast/auto/envelopes/{digest}.json"
    original = envelope_path.read_bytes()
    tampered = json.loads(original)
    tampered["automation_guards"]["min_durable_free_bytes"] = 12345
    envelope_path.write_text(json.dumps(tampered), encoding="utf-8")
    refused(auto.run(digest), "altered")
    envelope_path.write_bytes(original)
    # A changed campaign definition (a non-scientific field, so it still loads).
    config_bytes = auto.world.path.read_bytes()

    def workers(config: dict[str, Any]) -> None:
        config["concurrency"]["download_workers"] = 1

    auto.world.path.write_text(json.dumps(resealed(auto.world.config, workers)), encoding="utf-8")
    refused(auto.run(digest), "identity differs")
    # A changed source revision is a scientific change: the campaign refuses to load.

    def revision(config: dict[str, Any]) -> None:
        config["binding"]["revision"] = "0" * 40

    auto.world.path.write_text(json.dumps(resealed(auto.world.config, revision)), encoding="utf-8")
    refused(auto.run(digest), "campaign refused to load")
    auto.world.path.write_bytes(config_bytes)
    assert not (auto.world.root / "plans/ew-fast/b0000").exists()


def test_changed_code_refuses(auto: Auto, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    copy = tmp_path / "code"
    for name in (*auto.tool.recovery.CODE_FILES, *auto.tool.RUNNER_CODE_FILES):
        (copy / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(REPO / name, copy / name)
    monkeypatch.setattr(auto.tool, "CODE_REPO", copy)
    digest = auto.prepare("--max-batches", "1")["envelope"]["digest"]
    with (copy / "src/xlm/data/sources/essential_web_local.py").open("a", encoding="utf-8") as f:
        f.write("# drift\n")
    outcome = auto.run(digest)
    assert outcome["outcome"] == runner.REFUSED
    assert "code.running_sha256" in " ".join(outcome["reasons"])
    assert not auto.calls and not auto.world.state.requests


def test_envelope_ceiling_stops_while_targets_are_insufficient(auto: Auto) -> None:
    digest = auto.prepare("--max-batches", "1")["envelope"]["digest"]
    outcome = auto.run(digest)
    assert outcome["outcome"] == runner.ENVELOPE_EXHAUSTED and outcome["exit_code"] == 5
    assert auto.calls == [0] and outcome["batch"] == 1
    assert not (auto.world.root / "plans/ew-fast/b0001").exists()
    # The envelope is never widened; a new one starts from the authoritative next batch.
    report = auto.prepare("--max-batches", "1")
    assert report["envelope"]["range"]["start_batch"] == 1
    assert report["envelope"]["digest"] != digest


# ------------------------------------------------------------------- recovery


def seed_retained_source(auto: Auto, name: str) -> None:
    """As if an earlier run died right after the durable copy of this file."""
    scratch = auto.world.scratch / "seed.part"
    scratch.parent.mkdir(parents=True, exist_ok=True)
    scratch.write_bytes(auto.world.state.files[name])
    sha256, size = sp.file_sha256(scratch)
    sp.promote_source(
        scratch,
        auto.world.root / "acq-raw/ew-fast/source" / name,
        sp.identity_record(
            sp.SourceIdentity('"opaque"', size, sha256),
            source_file=name,
            repository=auto.world.config["binding"]["repository"],
            revision=REVISION,
        ),
    )
    scratch.unlink()


def test_completed_skipped_and_clean_partial_batch_resumes(auto: Auto) -> None:
    auto.complete_manually(0)
    assert auto.classify(0)["state"] == runner.COMPLETE
    assert auto.classify(1)["state"] == runner.CLEAN
    auto.world.authorize(1)
    first = auto.campaign().members(1)[0]
    seed_retained_source(auto, first)
    verdict = auto.classify(1)
    assert verdict["state"] == runner.RESUMABLE and "retained sources" in verdict["traces"]
    assert verdict["restart"]["local_complete_reuse"] == 1
    digest = auto.prepare("--max-batches", "1")["envelope"]["digest"]
    outcome = auto.run(digest)
    assert outcome["outcome"] == runner.ENVELOPE_EXHAUSTED and auto.calls == [1]
    assert object_hits(auto, 1) == [0, 1]  # the retained source was not transferred again
    assert object_hits(auto, 0) == [1, 1]  # the complete batch was skipped
    assert auto.events("batch_start")[0]["classification"] == runner.RESUMABLE


def test_unit_without_receipt_needs_human_review(auto: Auto) -> None:
    digest = auto.prepare("--max-batches", "2")["envelope"]["digest"]
    auto.world.authorize(0)
    stray = auto.world.root / "canonical/ew-fast/b0000/f00001"
    stray.mkdir(parents=True)
    verdict = auto.classify(0)
    assert verdict["state"] == runner.HUMAN_REVIEW
    assert "exists without a receipt" in " ".join(verdict["reasons"])
    outcome = auto.run(digest)
    assert outcome["outcome"] == runner.HUMAN_REVIEW and outcome["exit_code"] == 6
    assert not auto.calls and not auto.world.state.requests


def test_a_batch_under_a_recovery_amendment_is_never_resumed_automatically(auto: Auto) -> None:
    auto.world.authorize(0)
    name = auto.campaign().members(0)[1]
    seed_retained_source(auto, name)
    assert auto.classify(0)["state"] == runner.RESUMABLE
    amendment = {
        "campaign": auto.world.config["digest"],
        "batch": 0,
        "file": name,
        "digest": "a" * 64,
        "max_record_bytes": 2 * MIB,
    }
    amended = dataclasses.replace(auto.campaign(), recovery=amendment)
    verdict = auto.tool.classify(amended, 0, auto.log())
    assert verdict["state"] == runner.HUMAN_REVIEW
    assert "recovery amendment" in verdict["reasons"][0]
    # The amendment is part of the envelope identity: a new one voids the envelope.
    current = auto.tool.identity(auto.campaign())
    changed = auto.tool.identity(amended)
    assert runner.differences(current, changed) == ["code.compatibility.recovery_amendment"]


def test_oversized_record_stops_for_human_and_is_never_retried(auto: Auto) -> None:
    name = auto.campaign().members(0)[1]
    large = [row(KINDS[index % 5], index) for index in range(10)]
    # Incompressible, so the 1 MiB record bound refuses it, not the decompression ratio.
    large[3]["text"] = random.Random(0).randbytes(MIB).hex()
    auto.world.state.files[name] = parquet_bytes(large, 5)
    digest = auto.prepare("--max-batches", "2")["envelope"]["digest"]
    outcome = auto.run(digest)
    assert outcome["outcome"] == runner.HUMAN_REVIEW and auto.calls == [0]
    assert "f00001" not in receipts(auto)
    failures = [
        event
        for event in runner.read_events(auto.world.root / "plans/ew-fast/b0000/events.jsonl")
        if event["event"] == "failed"
    ]
    assert [event["exception"] for event in failures] == ["RecordLimitError"]
    requests = len(auto.world.state.requests)
    verdict = auto.classify(0)
    assert verdict["state"] == runner.HUMAN_REVIEW
    again = auto.run(digest)
    assert again["outcome"] == runner.HUMAN_REVIEW and auto.calls == [0]
    assert len(auto.world.state.requests) == requests
    # No automatic recovery amendment or changed record bound exists anywhere.
    assert auto.campaign().recovery is None


# -------------------------------------------------------------------- failure


def test_child_gate_refusal_stops_before_planning(auto: Auto) -> None:
    (auto.world.root / "plans/ew-fast/benchmark/benchmark.json").unlink()
    digest = auto.prepare("--max-batches", "2")["envelope"]["digest"]
    outcome = auto.run(digest)
    assert outcome["outcome"] == runner.REFUSED and "gate REFUSE" in outcome["reasons"][0]
    assert not auto.calls and not (auto.world.root / "plans/ew-fast/b0000/batch.json").exists()


def test_nonzero_exit_stays_in_review_until_the_batch_changes(auto: Auto) -> None:
    digest = auto.prepare("--max-batches", "2")["envelope"]["digest"]
    outcome = auto.run(digest, executor=lambda batch: 2)
    assert outcome["outcome"] == runner.HUMAN_REVIEW and "exited 2" in outcome["reasons"][0]
    assert auto.classify(0)["state"] == runner.HUMAN_REVIEW
    again = auto.run(digest)
    assert again["outcome"] == runner.HUMAN_REVIEW and not auto.calls
    # After the operator's manual run through the fast driver the runner continues.
    assert auto.world.run("run", "--batch", "0") == 0
    resumed = auto.run(digest)
    assert resumed["outcome"] == runner.ENVELOPE_EXHAUSTED and auto.calls == [1]


@pytest.mark.parametrize(
    ("flag", "text"),
    [("--min-durable-free-gib", "durable free space"), ("--min-scratch-free-gib", "scratch free")],
)
def test_automation_guards_stop_before_a_batch(auto: Auto, flag: str, text: str) -> None:
    digest = auto.prepare("--max-batches", "2", flag, str(1 << 40))["envelope"]["digest"]
    outcome = auto.run(digest)
    assert outcome["outcome"] == runner.GUARD_STOP and outcome["exit_code"] == 7
    assert text in outcome["reasons"][0] and not auto.calls
    assert not (auto.world.root / "plans/ew-fast/b0000/batch.json").exists()


def test_campaign_disk_reserve_still_applies_under_automation(auto: Auto) -> None:
    def reserve(altered: dict[str, Any]) -> None:
        altered["disk"]["min_free_bytes"] = 1 << 60

    auto.world.path.write_text(json.dumps(resealed(auto.world.config, reserve)), encoding="utf-8")
    digest = auto.prepare("--max-batches", "2")["envelope"]["digest"]
    outcome = auto.run(digest)
    assert outcome["outcome"] == runner.REFUSED and "reserve" in " ".join(outcome["reasons"])
    assert not auto.calls


def break_hash(auto: Auto, monkeypatch: pytest.MonkeyPatch) -> None:
    name = auto.campaign().members(0)[1]
    auto.world.state.linked[name] = '"' + hashlib.sha256(b"other").hexdigest() + '"'


def break_revision(auto: Auto, monkeypatch: pytest.MonkeyPatch) -> None:
    auto.world.state.commit = "f" * 40


def break_publication(auto: Auto, monkeypatch: pytest.MonkeyPatch) -> None:
    """Canonical publication of the second unit fails as a Windows sharing violation."""
    real = os.rename

    def rename(source: Any, target: Any) -> None:
        if Path(target).name == "f00001" and ".staging" in str(source):
            raise PermissionError(13, "Access is denied")
        real(source, target)

    monkeypatch.setattr(os, "rename", rename)


@pytest.mark.parametrize("breakage", [break_hash, break_revision, break_publication])
def test_execution_failures_stop_for_human(
    auto: Auto, monkeypatch: pytest.MonkeyPatch, breakage: Any
) -> None:
    digest = auto.prepare("--max-batches", "2")["envelope"]["digest"]
    breakage(auto, monkeypatch)
    outcome = auto.run(digest)
    assert outcome["outcome"] == runner.HUMAN_REVIEW and auto.calls == [0], outcome
    assert "f00001" not in receipts(auto)
    assert auto.classify(0)["state"] == runner.HUMAN_REVIEW
    requests = len(auto.world.state.requests)
    assert auto.run(digest)["outcome"] == runner.HUMAN_REVIEW and auto.calls == [0]
    assert len(auto.world.state.requests) == requests
    assert auto.events("fatal_stop")


def test_subprocess_executor_reports_the_exit_code(auto: Auto, tmp_path: Path) -> None:
    script = tmp_path / "fake_driver.py"
    script.write_text(
        "import json, sys\n"
        "json.dump(sys.argv[1:], open(sys.argv[0] + '.args', 'w'))\n"
        "sys.exit(9)\n",
        encoding="utf-8",
    )
    executor = auto.tool.SubprocessExecutor(auto.ctx, script=script)
    assert executor(3) == 9
    argv = json.loads(Path(str(script) + ".args").read_text(encoding="utf-8"))
    assert argv[-3:] == ["run", "--batch", "3"] and "--scratch-root" in argv
    outcome = auto.run(auto.prepare("--max-batches", "1")["envelope"]["digest"], executor=executor)
    assert outcome["outcome"] == runner.HUMAN_REVIEW and "exited 9" in outcome["reasons"][0]


# -------------------------------------------------------------------- restart


def test_ctrl_c_between_batches_resumes_at_the_next_batch(
    auto: Auto, monkeypatch: pytest.MonkeyPatch
) -> None:
    digest = auto.prepare("--max-batches", "4")["envelope"]["digest"]
    real = auto.tool.guard_reasons
    seen: list[int] = []

    def interrupt_second(campaign: Any, guards: Any) -> list[str]:
        seen.append(1)
        if len(seen) == 2:
            raise KeyboardInterrupt
        result: list[str] = real(campaign, guards)
        return result

    monkeypatch.setattr(auto.tool, "guard_reasons", interrupt_second)
    outcome = auto.run(digest)
    assert outcome["outcome"] == runner.INTERRUPTED and outcome["exit_code"] == 130
    assert auto.calls == [0] and auto.events("safe_stop")
    monkeypatch.setattr(auto.tool, "guard_reasons", real)
    assert auto.classify(1)["state"] == runner.CLEAN
    resumed = auto.run(digest)
    assert resumed["outcome"] == runner.FIRST_PASS_COMPLETE and auto.calls == [0, 1, 2]
    assert auto.events("automation_resume")
    names = [receipt["file"] for receipt in receipts(auto).values()]
    assert len(names) == len(set(names)) == 6


def test_ctrl_c_during_a_batch_resumes_its_exact_units(
    auto: Auto, monkeypatch: pytest.MonkeyPatch
) -> None:
    digest = auto.prepare("--max-batches", "4")["envelope"]["digest"]
    real = auto.world.tool.seal_unit

    def seal_then_interrupt(*args: Any, **kwargs: Any) -> Any:
        real(*args, **kwargs)
        raise KeyboardInterrupt

    monkeypatch.setattr(auto.world.tool, "seal_unit", seal_then_interrupt)
    outcome = auto.run(digest)
    assert outcome["outcome"] == runner.INTERRUPTED and auto.calls == [0]
    assert len(receipts(auto)) == 1  # whichever unit sealed first
    mark = auto.events(runner.MARK_INTERRUPTED)[-1]
    assert mark["batch"] == 0 and mark["fingerprint"]["events"] > 0
    monkeypatch.setattr(auto.world.tool, "seal_unit", real)
    verdict = auto.classify(0)
    assert verdict["state"] == runner.RESUMABLE and verdict["sealed"] == 1
    resumed = auto.run(digest)
    assert resumed["outcome"] == runner.FIRST_PASS_COMPLETE and auto.calls == [0, 0, 1, 2]
    assert object_hits(auto, 0) == [1, 1]  # the sealed unit was not fetched again
    names = [receipt["file"] for receipt in receipts(auto).values()]
    assert len(names) == len(set(names)) == 6
    state, _ = auto.tool.tool.campaign_state(auto.campaign())
    assert state["counted"]["files"] == 6 and state["counted"]["rows"] == 60


def test_marks_apply_only_to_the_exact_batch_state() -> None:
    events = [{"event": "run"}, {"event": "sealed"}, {"event": "fatal"}]
    here = {"events": 3, "performance": 1}
    interrupted = [{"event": runner.MARK_INTERRUPTED, "batch": 4, "fingerprint": here}]
    assert runner.classify_started(4, events, here, interrupted)["state"] == runner.RESUMABLE
    moved = {"events": 5, "performance": 2}
    assert runner.classify_started(4, events, moved, interrupted)["state"] == runner.HUMAN_REVIEW
    assert runner.classify_started(5, events, here, interrupted)["state"] == runner.HUMAN_REVIEW
    failed = [{"event": runner.MARK_FAILED, "batch": 4, "fingerprint": here, "exit_code": 1}]
    assert runner.classify_started(4, events[:2], here, failed)["state"] == runner.HUMAN_REVIEW
    # A crash leaves no terminal event; a later attempt supersedes an earlier failure.
    assert runner.classify_started(4, events[:2], here, [])["state"] == runner.RESUMABLE
    retried = [*events, {"event": "resume"}, {"event": "sealed"}]
    assert runner.classify_started(4, retried, moved, [])["state"] == runner.RESUMABLE
    unit = [{"event": "run"}, {"event": "failed", "key": "f00001", "exception": "X"}]
    verdict = runner.classify_started(4, unit, here, [])
    assert verdict["state"] == runner.HUMAN_REVIEW and "f00001:X" in verdict["reasons"][0]


# ----------------------------------------------------------------- display etc.


def history(count: int, *, seconds: float | None = 240.0) -> list[dict[str, Any]]:
    return [
        {
            "batch": index,
            "tokens": {SCIENCE: 40e6, PRACTICAL: 100e6, PROSE: 280e6},
            "footprint_bytes": 11 * GIB,
            "seconds": seconds,
        }
        for index in range(count)
    ]


def views(science: float, practical: float, prose: float) -> dict[str, dict[str, Any]]:
    out = {}
    for view, have, target in (
        (SCIENCE, science, 660e6),
        (PRACTICAL, practical, 660e6),
        (PROSE, prose, 330e6),
    ):
        out[view] = {
            "estimated_tokens": have,
            "target_tokens": target,
            "canonical_bytes": int(have * 4),
            "deficit_tokens": max(0.0, target - have),
            "status": "SUFFICIENT" if have >= target else "TOP_UP",
        }
    return out


def status_for(entries: list[dict[str, Any]], current: dict[str, Any]) -> dict[str, Any]:
    return {
        "campaign": "8e42ba31" * 8,
        "next_batch": len(entries),
        "complete_batches": len(entries),
        "sealed_files": 32 * len(entries),
        "rows": 2_650_000 * len(entries),
        "views": current,
        "disk": {
            "footprint_bytes": 33 * GIB,
            "footprint_cap_bytes": 400 * GIB,
            "durable_free_bytes": 900 * GIB,
            "campaign_min_free_bytes": 64 * GIB,
            "automation_min_durable_free_bytes": 128 * GIB,
            "scratch_used_bytes": 0,
            "scratch_cap_bytes": 64 * GIB,
            "scratch_free_bytes": 800 * GIB,
            "automation_min_scratch_free_bytes": 96 * GIB,
        },
        "time": {"campaign_elapsed_seconds": 3700.0, "last_batch_seconds": 250.0},
        "projection": runner.projection(entries, current),
    }


def test_projection_uses_recent_complete_batches_and_says_so() -> None:
    current = views(120e6, 300e6, 840e6)
    assert runner.projection(history(1), current)["status"] == "calculating"
    assert runner.projection(history(3, seconds=None), current)["status"] == "calculating"
    projected = runner.projection(history(3), current)
    assert projected["status"] == "projected" and projected["basis_batches"] == [0, 1, 2]
    assert projected["views"][SCIENCE]["remaining_batches"] == 14  # ceil(540 / 40)
    assert projected["views"][PRACTICAL]["remaining_batches"] == 4
    assert projected["views"][PROSE]["remaining_batches"] == 0
    assert projected["remaining_batches"] == 14 and projected["eta_seconds"] == 14 * 240.0
    window = history(5)
    window[0]["tokens"][SCIENCE] = 1e9  # outside the rolling window of three
    assert runner.projection(window, current)["basis_batches"] == [2, 3, 4]
    zero = history(3)
    for entry in zero:
        entry["tokens"][SCIENCE] = 0.0
    assert runner.projection(zero, current)["status"] == "unbounded"


def test_campaign_header_and_completion_banner() -> None:
    current = views(118.9e6, 300.9e6, 840.1e6)
    text = "\n".join(runner.campaign_header(status_for(history(3), current)))
    assert "ESSENTIAL-WEB AUTO CAMPAIGN" in text
    assert "SCIENCE   118.9M / 660.0M   18.0%  TOP_UP" in text
    assert "PROSE     840.1M / 330.0M  254.6%  SUFFICIENT" in text
    assert "projected remaining ~14 batches" in text and "not a promise" in text
    assert "campaign elapsed 1h01m" in text and "automation guard 128.0 GiB" in text
    calculating = "\n".join(runner.campaign_header(status_for(history(1), current)))
    assert "ETA calculating..." in calculating
    banner = "\n".join(runner.completion_banner(status_for(history(17), views(700e6, 1e9, 2e9))))
    assert "ESSENTIAL-WEB FIRST-PASS ACQUISITION COMPLETE" in banner
    assert "C05 status             NOT RUN" in banner and "TRAINING NOT YET PERMITTED" in banner
    assert "ready for training" not in banner.lower()


def test_envelope_bounds_digest_and_log(tmp_path: Path) -> None:
    assert runner.batch_range(3, 20, 33, 725) == (23, False)
    assert runner.batch_range(20, 20, 33, 725) == (33, True)
    for start, count in ((3, 21), (3, 0), (33, 1), (-1, 1)):
        with pytest.raises(BulkError):
            runner.batch_range(start, count, 33, 725)
    assert runner.differences({"a": {"b": 1, "c": 2}}, {"a": {"b": 1, "c": 3}}) == ["a.c"]
    assert runner.differences({"a": 1}, {"b": 1}) == ["a", "b"]
    child = {"batch": 3}
    envelope = runner.make_envelope(
        {"campaign": "x"},
        [child],
        start=3,
        end=4,
        max_batches=1,
        clamped=False,
        guards={"min_durable_free_bytes": 1, "min_scratch_free_bytes": 1},
        operator="t",
    )
    runner.check_envelope(envelope)
    with pytest.raises(BulkError, match="exactly the batches"):
        runner.make_envelope(
            {}, [child], start=3, end=5, max_batches=2, clamped=False, guards={}, operator="t"
        )
    log = runner.EventLog(tmp_path / "log.jsonl")
    log.append("automation_start", envelope="d")
    with log.path.open("a", encoding="utf-8") as stream:
        stream.write('{"torn": ')
    assert [record["event"] for record in log.read()] == ["automation_start"]
    log.path.write_text('{"broken": \n{"event": "x"}\n', encoding="utf-8")
    with pytest.raises(BulkError, match="corrupt"):
        log.read()


def test_status_is_read_only_and_structured(auto: Auto) -> None:
    before = sorted(str(path) for path in auto.world.root.rglob("*"))
    output = io.StringIO()
    original = sys.stdout
    sys.stdout = output
    try:
        assert auto.main("status", "--json") == 0
    finally:
        sys.stdout = original
    status = json.loads(output.getvalue())
    assert status["decision"] == "CONTINUE" and status["next_batch"] == 0
    assert status["next_batch_classification"]["state"] == runner.CLEAN
    assert status["projection"]["status"] == "calculating"
    assert status["c05"] == "NOT RUN" and status["training_permitted"] is False
    assert sorted(str(path) for path in auto.world.root.rglob("*")) == before


def test_operator_driver_parses_and_refuses_without_storage() -> None:
    shell = shutil.which("powershell") or shutil.which("pwsh")
    if shell is None:
        pytest.skip("PowerShell unavailable")
    path = REPO / "scripts/operator_essential_web_campaign.ps1"
    parsed = subprocess.run(
        [
            shell,
            "-NoProfile",
            "-Command",
            "$e = $null; [void][System.Management.Automation.Language.Parser]::ParseFile("
            f"'{path}', [ref]$null, [ref]$e); $e.Count",
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert parsed.returncode == 0 and parsed.stdout.strip() == "0", parsed.stderr
    bare = {
        k: v
        for k, v in os.environ.items()
        if k not in ("XLM_DATA_ROOT", "XLM_HOME", "XLM_SCRATCH_ROOT")
    }
    refused = subprocess.run(
        [shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(path), "-Stage", "Status"],
        cwd=REPO,
        env=bare,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert refused.returncode != 0 and "operator_storage.ps1" in refused.stderr
    unbounded = subprocess.run(
        [
            shell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(path),
            "-Stage",
            "PrepareAuto",
            "-MaxBatches",
            "21",
        ],
        cwd=REPO,
        env=bare,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert unbounded.returncode != 0
