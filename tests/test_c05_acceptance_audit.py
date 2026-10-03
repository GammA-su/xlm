"""Independent acceptance-audit falsification tests (authored fixtures only).

Each test tries to break one claimed C05 property: storage accounting across
tampering/crash leftovers, per-allocation selection integrity under a trusted
re-signing, completeness of exact counts, frozen IFM split substitution, and
legacy receipt refusal. Synthetic trust roots only; no real data or network.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from scripts.c05_authored_pilot import KEY
from scripts.c05_synthetic_flow import ISSUER, KEY_ENV, execute

from test_c05_capacity import corpus, state
from test_c05_engine import execute as run_plan
from test_c05_engine import setup_run
from xlm.data.evidence_v2 import canonical
from xlm.data.exclusion.artifacts import signed
from xlm.data.exclusion.capacity import admit_runtime
from xlm.data.exclusion.policy import C05Error
from xlm.data.exclusion.receipt import (
    BenchmarkClaimBinding,
    FinalExclusionReceipt,
    ReceiptValidationError,
    sign_receipt,
    verify_benchmark_claim,
)
from xlm.data.exclusion.runner import file_sha
from xlm.data.exclusion.transport import open_gate

load = canonical.loads_bytes_strict
IFM = "ifm_behaviors_general_planning"


@pytest.fixture(scope="module")
def flow(tmp_path_factory: pytest.TempPathFactory) -> Iterator[dict[str, Any]]:
    monkey = pytest.MonkeyPatch()
    monkey.setenv(KEY_ENV, KEY)
    root = tmp_path_factory.mktemp("audit") / "root"
    summary = execute(root)
    yield {"root": root, "summary": summary, "proof": root / "proof.json"}
    monkey.undo()


@pytest.fixture(autouse=True)
def key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(KEY_ENV, KEY)


def gate_of(flow: dict[str, Any]) -> Any:
    return open_gate(flow["proof"], allow_authored=True)


# --- Storage accounting -------------------------------------------------------


def test_deleted_signed_state_beside_job_files_refuses_instead_of_resetting(
    tmp_path: Path,
) -> None:
    plan, index, receipt = setup_run(tmp_path, corpus(4))

    def stop(event: str) -> None:
        if event == "file_committed":
            raise RuntimeError("authored interruption after one committed file")

    with pytest.raises(RuntimeError, match="authored interruption"):
        run_plan(plan, index, receipt, checkpoint=stop)
    work = Path(plan.scratch_root) / plan.identity()
    spent = state(plan)
    assert spent["spent_bytes_read"] > 0 and (work / "facts").is_dir()
    (work / "state.json").unlink()
    # Spent time/work/storage accounting must never restart from zero.
    with pytest.raises(C05Error, match="signed state"):
        run_plan(plan, index, receipt)
    assert not (Path(plan.output_root) / plan.identity()).exists()


def test_oversized_leftover_staging_refuses_before_any_work(tmp_path: Path) -> None:
    """A crash leftover above the hard working-index bound refuses before any file."""
    plan, index, receipt = setup_run(tmp_path, corpus(2))
    work = Path(plan.scratch_root) / plan.identity()
    staging = work / "facts" / "00000.staging"
    staging.mkdir(parents=True)
    with (staging / "records").open("wb") as stream:
        stream.truncate(plan.resources.index_bytes + 1)
    with pytest.raises(C05Error, match="exceeds its hard bound"):
        run_plan(plan, index, receipt)
    assert not (work / "state.json").exists()


def test_geometry_drift_and_foreign_publication_entries_refuse(tmp_path: Path) -> None:
    plan, index, receipt = setup_run(tmp_path, corpus(2))
    work = Path(plan.scratch_root) / plan.identity()
    output = Path(plan.output_root)
    drifted = plan.storage.model_copy(
        update={"journal_header_bytes": plan.storage.journal_header_bytes * 2}
    )
    with pytest.raises(C05Error, match="geometry differs"):
        admit_runtime(
            plan.resources,
            plan.storage,
            work,
            output,
            plan.identity(),
            index,
            review=plan.policy.review.enabled,
            files=len(plan.files),
            probe=lambda _: drifted,
        )
    staged = output / (plan.identity() + ".partial")
    staged.mkdir(parents=True)
    (staged / "membership.jsonl.bak").write_bytes(b"x")
    with pytest.raises(C05Error, match="unaccounted"):
        run_plan(plan, index, receipt)
    assert not work.exists()


def test_volumes_are_grouped_by_device_not_path_anchor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two directories on one anchor but different devices are separate volumes."""
    from xlm.data.exclusion import capacity

    plan, index, _ = setup_run(tmp_path, corpus(1))
    work = Path(plan.scratch_root) / plan.identity()
    output = Path(plan.output_root)
    output.mkdir(parents=True, exist_ok=True)
    real_stat = os.stat
    output_root = os.fspath(output)

    class Mounted:
        """The output directory reports a different device (a mount point)."""

        def __init__(self, result: os.stat_result) -> None:
            self._result = result
            self.st_dev = result.st_dev + 1

        def __getattr__(self, name: str) -> Any:
            return getattr(self._result, name)

    def mounted_stat(path: Any, *args: Any, **kwargs: Any) -> Any:
        result = real_stat(path, *args, **kwargs)
        return Mounted(result) if os.fspath(path).startswith(output_root) else result

    monkeypatch.setattr(os, "stat", mounted_stat)
    report = capacity.physical_reserve(
        plan.resources,
        plan.storage,
        work,
        output,
        plan.identity(),
        index,
        disk_usage=shutil.disk_usage,
    )
    # Output growth must be checked against the output device, not merged into
    # the scratch device just because both paths share a drive/root anchor.
    assert len(report["volumes"]) == 2, report


# --- Selection integrity ------------------------------------------------------


def resign_selection(flow: dict[str, Any], target: Path, edit: Any) -> Path:
    shutil.copytree(flow["root"] / "selection", target)
    rows = [load(r) for r in (target / "selected.jsonl").read_bytes().splitlines()]
    rows = edit(rows)
    raw = b"".join(canonical.canonical_bytes(r) + b"\n" for r in rows)
    (target / "selected.jsonl").write_bytes(raw)
    body = load((target / "selection.json").read_bytes())["payload"]
    body.update(
        selected_membership_sha256=file_sha(target / "selected.jsonl"),
        selected_membership_bytes=len(raw),
        selected_documents=len(rows),
    )
    (target / "selection.json").unlink()
    canonical.write_canonical_json(target / "selection.json", signed(body, ISSUER, KEY.encode()))
    return target


def shift_between(rows: list[dict[str, Any]], donor: list[Any], taker: list[Any]) -> int:
    give = next(r for r in rows if r["allocation"] == donor and r["selected_valid_targets"] > 1)
    take = next(
        r
        for r in rows
        if r["allocation"] == taker and r["selected_valid_targets"] < r["counted_valid_targets"]
    )
    amount = int(
        min(
            give["selected_valid_targets"] - 1,
            take["counted_valid_targets"] - take["selected_valid_targets"],
        )
    )
    assert amount > 0
    give["selected_valid_targets"] -= amount
    take["selected_valid_targets"] += amount
    return amount


@pytest.mark.parametrize(
    ("donor", "taker"),
    [
        ([IFM, "general", None], [IFM, "planning", None]),
        (
            ["common_pile_prose", "common_pile_prose", "project_gutenberg"],
            ["common_pile_prose", "common_pile_prose", "news"],
        ),
    ],
)
def test_internal_allocation_surplus_cannot_satisfy_a_sibling(
    flow: dict[str, Any], tmp_path: Path, donor: list[Any], taker: list[Any]
) -> None:
    """Even a trusted-issuer re-signing with the same component total must refuse."""
    from xlm.data.exclusion.freeze import tokenize_selection
    from xlm.data.exclusion.selection import SelectionGate

    shifted = resign_selection(
        flow, tmp_path / "shifted", lambda rows: (shift_between(rows, donor, taker), rows)[1]
    )
    with gate_of(flow) as gate, pytest.raises(C05Error, match="allocation"):
        SelectionGate(gate, shifted)
    with gate_of(flow) as gate, pytest.raises(C05Error, match="allocation"):
        tokenize_selection(gate, shifted, flow["root"] / "tokenizer", tmp_path / "shards")


def test_counts_omitting_kept_training_records_cannot_be_selected(
    flow: dict[str, Any], tmp_path: Path
) -> None:
    from test_c05_selection import copy_counts, select_into

    counts = copy_counts(flow, tmp_path / "partial-counts", lambda rows: rows[1:])
    with pytest.raises(C05Error, match="every kept training record"):
        select_into(flow, counts, tmp_path / "partial-selection")
    assert not (tmp_path / "partial-selection").exists()


def test_substituted_ifm_split_refuses(flow: dict[str, Any], tmp_path: Path) -> None:
    from xlm.data.exclusion.quotas import frozen_requirements

    split = load((flow["root"] / "ifm-split.json").read_bytes())
    split["views"]["general"]["final_tokens"] += 10
    split["views"]["planning"]["final_tokens"] -= 10
    split.pop("digest")
    split["digest"] = canonical.self_digest(split)
    canonical.write_canonical_json(tmp_path / "split.json", split)
    with gate_of(flow) as gate, pytest.raises(C05Error, match="IFM split"):
        frozen_requirements(
            gate.input_manifest, flow["root"] / "quotas.yaml", tmp_path / "split.json"
        )


def test_selection_replay_is_byte_identical(flow: dict[str, Any], tmp_path: Path) -> None:
    from test_c05_selection import select_into

    first = select_into(flow, flow["root"] / "counts", tmp_path / "one")["payload"]
    second = select_into(flow, flow["root"] / "counts", tmp_path / "two")["payload"]
    assert (tmp_path / "one/selected.jsonl").read_bytes() == (
        tmp_path / "two/selected.jsonl"
    ).read_bytes()
    stable = {k: v for k, v in first.items() if k not in {"issued_at", "issuer"}}
    assert stable == {k: v for k, v in second.items() if k not in {"issued_at", "issuer"}}
    assert first["valid_target_quota"] == sum(a["quota"] for a in first["allocations"].values())


# --- Gate ---------------------------------------------------------------------


def test_open_engineering_gate_still_requires_protected_evidence(tmp_path: Path) -> None:
    """Without engineering blockers, an authored chain still cannot run protected."""
    from test_c05_engine import KEY as ENGINE_KEY
    from xlm.data.exclusion.artifacts import authorize
    from xlm.data.exclusion.policy import ENGINEERING_BLOCKERS, require_engine_acceptance
    from xlm.data.exclusion.runner import run

    assert ENGINEERING_BLOCKERS == ()
    require_engine_acceptance("protected")
    plan, index, receipt = setup_run(tmp_path, corpus(1))
    protected = plan.model_copy(update={"mode": "protected"})
    common: dict[str, Any] = {
        "index": index,
        "benchmark": receipt,
        "trusted": {"fixture": ENGINE_KEY},
        "issuer": "fixture",
        "key": ENGINE_KEY,
        "current_code": "4" * 64,
        "current_dependencies": "5" * 64,
    }
    # The authored authorization does not authorize the protected plan digest.
    with pytest.raises(C05Error, match="authorization does not match"):
        run(protected, authorize(plan, "fixture", ENGINE_KEY), **common)
    # Even a matching authorization cannot turn an authored benchmark receipt protected.
    with pytest.raises(C05Error, match="benchmark protection mode"):
        run(protected, authorize(protected, "fixture", ENGINE_KEY), **common)
    assert not (Path(protected.scratch_root) / protected.identity()).exists()


# --- Receipts -----------------------------------------------------------------


def test_schema_one_receipt_cannot_make_an_official_claim(flow: dict[str, Any]) -> None:
    receipt = FinalExclusionReceipt.load(flow["root"] / "final-receipt.json")
    binding = BenchmarkClaimBinding(**load((flow["root"] / "claim-binding.json").read_bytes()))
    v1 = sign_receipt(
        replace(
            receipt,
            schema_version="1",
            mode="protected",
            c05_binding=None,
            selection_binding=None,
            signature=None,
        ),
        KEY.encode(),
    )
    with pytest.raises(ReceiptValidationError):
        verify_benchmark_claim(v1, binding, {ISSUER: KEY.encode()})
    # Same protected v3 receipt but an empty claim binding never matches.
    v3 = sign_receipt(replace(receipt, mode="protected", signature=None), KEY.encode())
    empty = BenchmarkClaimBinding(
        checkpoint_hash=binding.checkpoint_hash,
        suite_fingerprint=binding.suite_fingerprint,
        corpus_input_digest=binding.corpus_input_digest,
        output_membership_digest=binding.output_membership_digest,
        exclusion_policy_identity=binding.exclusion_policy_identity,
        exclusion_index_identity=binding.exclusion_index_identity,
    )
    with pytest.raises(ReceiptValidationError):
        verify_benchmark_claim(v3, empty, {ISSUER: KEY.encode()})
