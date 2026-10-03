"""Authored storage-admission, working-index and crash-overlap checks; no real scan.

The compact engine's hard working-index ledger, staging leftovers and publication
overlap are tested here; the historical SQLite journal mechanics remain tested
against the reference engine (``xlm.data.exclusion.reference``).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections import namedtuple
from pathlib import Path
from typing import Any

import pytest

from test_c05_engine import KEY, PROMPT, document, execute, setup_run, small_resources
from xlm.data.evidence_v2 import canonical
from xlm.data.exclusion import reference_capacity
from xlm.data.exclusion.artifacts import ExecutionPlan, authorize, verify_signed
from xlm.data.exclusion.capacity import (
    FILE_SLACK_BYTES,
    FIXED_FILES,
    MAX_SORT_RUNS,
    admit_plan,
    admit_runtime,
    journal_bound,
    probe_geometry,
    storage_bounds,
)
from xlm.data.exclusion.policy import C05Error

Usage = namedtuple("Usage", "total used free")


def corpus(count: int) -> list[Any]:
    texts = [
        " ".join(f"authored{n}word{i}topic{(n * 7 + i) % 13}" for i in range(48))
        for n in range(count)
    ]
    return [document("hit", PROMPT), *(document(f"d{n:03d}", t) for n, t in enumerate(texts))]


def state(plan: ExecutionPlan) -> dict[str, Any]:
    raw = (Path(plan.scratch_root) / plan.identity() / "state.json").read_bytes()
    return verify_signed(canonical.loads_bytes_strict(raw), {"fixture": KEY})


def plan_bounds(plan: ExecutionPlan) -> dict[str, int]:
    return storage_bounds(
        plan.resources, plan.storage, review=plan.policy.review.enabled, files=len(plan.files)
    )


def run_reference(plan: ExecutionPlan, index: Path, receipt: dict[str, Any], **kw: Any) -> Any:
    from xlm.data.exclusion import reference

    return reference.run(
        plan,
        authorize(plan, "fixture", KEY),
        index=index,
        benchmark=receipt,
        trusted={"fixture": KEY},
        issuer="fixture",
        key=KEY,
        current_code="4" * 64,
        current_dependencies="5" * 64,
        **kw,
    )


def test_geometry_and_plan_admission_never_widen(tmp_path: Path) -> None:
    geometry = probe_geometry(tmp_path)
    assert geometry.journal_record_bytes == 4104
    assert 32 <= geometry.journal_header_bytes <= 65536
    resources = small_resources()
    bounds = storage_bounds(resources, geometry, review=True, files=3)
    pages = resources.index_bytes // 4096
    header = geometry.journal_header_bytes
    assert bounds["review_rollback_journal"] == header + pages * (4104 + 2 * header)
    assert bounds["facts_working_index"] == resources.index_bytes
    assert bounds["allocation_slack"] == (3 + FIXED_FILES + MAX_SORT_RUNS) * FILE_SLACK_BYTES
    assert admit_plan(resources, geometry, review=True, files=3)["worst_case_bytes"] == sum(
        bounds.values()
    )
    # Without heuristic review there is no SQLite store, so no review bounds.
    plain = storage_bounds(resources, geometry, review=False, files=3)
    assert plain["review_store"] == plain["review_rollback_journal"] == 0
    short_journal = small_resources(journal_bytes=bounds["review_rollback_journal"] - 1)
    with pytest.raises(C05Error, match="journal ceiling is below"):
        admit_plan(short_journal, geometry, review=True, files=3)
    admit_plan(short_journal, geometry, review=False, files=3)
    plan, _, _ = setup_run(tmp_path / "plan", [document("a", "An authored row.")])
    short_scratch = small_resources(scratch_bytes=sum(plan_bounds(plan).values()) - 1)
    with pytest.raises(C05Error, match="no automatic widening"):
        admit_plan(short_scratch, geometry, review=False, files=1)
    with pytest.raises(C05Error, match="scratch ceiling"):
        plan.model_copy(update={"resources": short_scratch}).identity()
    # The reviewed values are never replaced by the computed requirement.
    assert short_scratch.scratch_bytes == sum(plan_bounds(plan).values()) - 1


def test_insufficient_physical_reserve_refuses_before_any_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan, index, receipt = setup_run(tmp_path, corpus(3))
    work = Path(plan.scratch_root) / plan.identity()
    worst = sum(plan_bounds(plan).values())
    monkeypatch.setattr(shutil, "disk_usage", lambda path: Usage(10**12, 0, worst // 4))
    with pytest.raises(C05Error, match="insufficient physical reserve"):
        execute(plan, index, receipt)
    assert not work.exists()
    assert not (Path(plan.output_root) / plan.identity()).exists()


def test_scratch_exhaustion_mid_run_resumes_without_resetting_accounting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan, index, receipt = setup_run(tmp_path, corpus(4))
    real = shutil.disk_usage
    exhausted = {"on": False}

    def usage(path: Any) -> Any:
        result = real(path)
        return Usage(result.total, result.used, 0) if exhausted["on"] else result

    def exhaust(event: str) -> None:
        if event == "file_committed":
            exhausted["on"] = True

    monkeypatch.setattr(shutil, "disk_usage", usage)
    with pytest.raises(C05Error, match="insufficient physical reserve|free-space reserve"):
        execute(plan, index, receipt, checkpoint=exhaust)
    first = state(plan)
    assert first["storage"]["admissions"] == 1
    assert not (Path(plan.output_root) / plan.identity()).exists()
    with pytest.raises(C05Error, match="insufficient physical reserve"):
        execute(plan, index, receipt)  # Still exhausted: refused before work.
    exhausted["on"] = False
    result = execute(plan, index, receipt)["payload"]
    final = state(plan)
    assert result["storage"]["admissions"] == final["storage"]["admissions"] >= 2
    assert final["spent_bytes_read"] >= first["spent_bytes_read"]
    assert final["started"] == first["started"]
    assert result["peak_scratch_sampled"] >= first["storage"]["peak_aggregate_sampled"]
    assert result["peak_scratch_sampled"] <= result["storage"]["worst_case_bytes"]


CRASH = """
import os,sys
from pathlib import Path
from xlm.data.evidence_v2 import canonical
from xlm.data.exclusion.artifacts import ExecutionPlan,authorize
from xlm.data.exclusion.{module} import run
root=Path(sys.argv[1]); event=sys.argv[2]
plan=ExecutionPlan.model_validate(canonical.loads_bytes_strict((root/'plan.json').read_bytes()))
receipt=canonical.loads_bytes_strict((root/'benchmark.json').read_bytes())
key=b'authored-local-test-key-not-a-protected-issuer'
def checkpoint(point):
    if point==event:
        os._exit(29)
run(plan,authorize(plan,'fixture',key),index=root/'authored-index.jsonl',benchmark=receipt,
    trusted={{'fixture':key}},issuer='fixture',key=key,current_code='4'*64,current_dependencies='5'*64,
    checkpoint=checkpoint)
"""


def crash(
    tmp_path: Path,
    plan: ExecutionPlan,
    receipt: dict[str, Any],
    event: str,
    module: str = "runner",
) -> None:
    canonical.write_canonical_json(tmp_path / "plan.json", plan.model_dump(mode="json"))
    canonical.write_canonical_json(tmp_path / "benchmark.json", receipt)
    process = subprocess.run(
        [sys.executable, "-c", CRASH.format(module=module), str(tmp_path), event],
        capture_output=True,
        timeout=120,
        env={**os.environ},
    )
    assert process.returncode == 29, process.stderr.decode()


@pytest.mark.parametrize("event", ["row", "file_committed", "grouped"])
def test_process_death_leftovers_are_accounted_then_discarded(tmp_path: Path, event: str) -> None:
    plan, index, receipt = setup_run(tmp_path, corpus(40))
    crash(tmp_path, plan, receipt, event)
    work = Path(plan.scratch_root) / plan.identity()
    report = admit_runtime(
        plan.resources,
        plan.storage,
        work,
        Path(plan.output_root),
        plan.identity(),
        index,
        review=False,
        files=len(plan.files),
    )
    leftovers = sum(p.stat().st_size for p in work.rglob("*") if p.is_file())
    assert report["present_bytes"] >= leftovers - (work / "matcher").stat().st_size
    result = execute(plan, index, receipt)["payload"]
    assert not list((work / "facts").glob("*.staging"))
    assert (result["documents"], result["excluded"]) == (41, 1)
    assert result["storage"]["admissions"] == 2


def test_wal_or_shared_memory_growth_is_refused(tmp_path: Path) -> None:
    plan, index, receipt = setup_run(tmp_path, corpus(2))
    work = Path(plan.scratch_root) / plan.identity()
    work.mkdir(parents=True)
    (work / "review.sqlite-wal").write_bytes(b"\0" * 4096)
    with pytest.raises(C05Error, match="unaccounted C05 scratch"):
        execute(plan, index, receipt)
    assert not (work / "state.json").exists()


def test_historical_sqlite_database_is_never_adopted(tmp_path: Path) -> None:
    plan, index, receipt = setup_run(tmp_path, corpus(2))
    work = Path(plan.scratch_root) / plan.identity()
    work.mkdir(parents=True)
    (work / "facts.sqlite").write_bytes(b"\0" * 4096)
    with pytest.raises(C05Error, match="unaccounted C05 scratch"):
        execute(plan, index, receipt)


def test_publication_crash_overlap_is_bounded_and_atomic(tmp_path: Path) -> None:
    plan, index, receipt = setup_run(tmp_path, corpus(6))
    crash(tmp_path, plan, receipt, "before_publication")
    output = Path(plan.output_root)
    staged = output / (plan.identity() + ".partial")
    final = output / plan.identity()
    assert staged.is_dir() and not final.exists()
    staged_bytes = (staged / "membership.jsonl").stat().st_size
    bounds = plan_bounds(plan)
    assert staged_bytes <= bounds["membership_staging_and_publication"]
    work = Path(plan.scratch_root) / plan.identity()
    report = admit_runtime(
        plan.resources,
        plan.storage,
        work,
        output,
        plan.identity(),
        index,
        review=False,
        files=len(plan.files),
    )
    assert report["present_bytes"] >= staged_bytes
    result = execute(plan, index, receipt)
    # Same-directory rename: one membership copy, never staged plus final together.
    assert final.is_dir() and not staged.exists()
    assert (final / "membership.jsonl").stat().st_size == result["payload"]["membership_bytes"]
    assert sorted(p.name for p in final.iterdir()) == ["completion.json", "membership.jsonl"]
    assert execute(plan, index, receipt) == result


def test_hard_working_index_ceiling_refuses_and_rerun_cannot_widen(tmp_path: Path) -> None:
    tiny = small_resources(index_bytes=64 * 1024)
    plan, index, receipt = setup_run(tmp_path, corpus(40), resources=tiny)
    with pytest.raises(C05Error, match="working-index ceiling"):
        execute(plan, index, receipt)
    spent = state(plan)["spent_bytes_read"]
    with pytest.raises(C05Error, match="working-index ceiling"):
        execute(plan, index, receipt)
    assert state(plan)["spent_bytes_read"] >= spent
    assert not (Path(plan.output_root) / plan.identity()).exists()
    work = Path(plan.scratch_root) / plan.identity()
    on_disk = sum(p.stat().st_size for p in (work / "facts").rglob("*") if p.is_file())
    assert on_disk <= tiny.index_bytes
    wider = plan.model_copy(update={"resources": small_resources()})
    assert wider.identity() != plan.identity()
    with pytest.raises(C05Error, match="authorization does not match"):
        from xlm.data.exclusion.runner import run

        run(
            wider,
            authorize(plan, "fixture", KEY),
            index=index,
            benchmark=receipt,
            trusted={"fixture": KEY},
            issuer="fixture",
            key=KEY,
            current_code="4" * 64,
            current_dependencies="5" * 64,
        )


# --- historical SQLite mechanics of the retained reference engine -------------------------


def test_reference_rollback_journal_growth_stays_within_derived_bound(tmp_path: Path) -> None:
    plan, index, receipt = setup_run(tmp_path, corpus(40))
    work = Path(plan.scratch_root) / plan.identity()
    observed: list[int] = []

    def inspect(event: str) -> None:
        if event == "grouped":  # Inside the single grouping transaction, before commit.
            journal = work / "facts.sqlite-journal"
            observed.append(journal.stat().st_size)
            raise RuntimeError("authored interruption inside grouping transaction")

    with pytest.raises(RuntimeError, match="grouping transaction"):
        run_reference(plan, index, receipt, checkpoint=inspect)
    database = plan.resources.index_bytes
    assert 0 < observed[0] <= journal_bound(plan.storage, database)
    assert observed[0] <= plan.resources.journal_bytes
    assert not (work / "facts.sqlite-journal").exists()  # Rolled back.
    result = run_reference(plan, index, receipt)["payload"]
    assert result["excluded"] == 1
    assert result["storage"]["peak_journal_sampled"] <= journal_bound(plan.storage, database)


def test_reference_hot_journal_after_process_death_is_recovered(tmp_path: Path) -> None:
    plan, index, receipt = setup_run(tmp_path, corpus(40))
    crash(tmp_path, plan, receipt, "grouped", module="reference")
    work = Path(plan.scratch_root) / plan.identity()
    hot = work / "facts.sqlite-journal"
    assert hot.is_file() and 0 < hot.stat().st_size <= journal_bound(
        plan.storage, plan.resources.index_bytes
    )
    report = reference_capacity.admit_runtime(
        plan.resources, plan.storage, work, Path(plan.output_root), plan.identity(), index
    )
    assert report["present_bytes"] >= hot.stat().st_size + (work / "facts.sqlite").stat().st_size
    result = run_reference(plan, index, receipt)["payload"]
    assert not hot.exists()
    assert (result["documents"], result["excluded"]) == (41, 1)
    assert result["storage"]["admissions"] == 2


def test_reference_hard_database_page_cap_refuses(tmp_path: Path) -> None:
    import sqlite3

    tiny = small_resources(index_bytes=256 * 1024, journal_bytes=1024**2)
    plan, index, receipt = setup_run(tmp_path, corpus(40), resources=tiny)
    with pytest.raises(sqlite3.OperationalError, match="full"):
        run_reference(plan, index, receipt)
    assert not (Path(plan.output_root) / plan.identity()).exists()


def test_readiness_separates_engineering_decisions_and_evidence(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import json

    from xlm.data.exclusion.operator import main
    from xlm.data.exclusion.policy import ENGINEERING_BLOCKERS

    code = main(
        [
            "plan-readiness",
            "--benchmark-receipt",
            str(tmp_path / "absent-receipt.json"),
            "--lineage-policy",
            str(tmp_path / "absent-lineage.json"),
            "--resources",
            str(tmp_path / "absent-resources.json"),
            "--geometry-probe-dir",
            str(tmp_path / "probe"),
        ]
    )
    report = json.loads(capsys.readouterr().out)
    assert code == 2 and report["ready"] is False and report["plan_digest"] is None
    assert report["engineering_blockers"] == list(ENGINEERING_BLOCKERS) == []
    # Only genuine operator decisions and protected evidence remain.
    assert report["blockers"] and not any(b.startswith("engineering") for b in report["blockers"])
    assert report["operator_decisions"] == {
        "gutenberg_lineage_decision": "missing",
        "reviewed_resource_decision": "missing",
    }
    assert report["protected_evidence"] == {"protected_benchmark_preparation_receipt": "missing"}
    admission = report["engineering_checks"]["proposed_resources_storage_admission"]
    assert admission["proposal_only"] is True
    assert "worst_case_bytes" in admission or "refused" in admission
    assert not any((tmp_path / "probe").iterdir())  # The probe removes its own files.
