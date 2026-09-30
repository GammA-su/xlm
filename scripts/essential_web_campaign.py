"""Bounded automatic operator runner for the fast Essential-Web campaign.

An outer layer over ``essential_web_fast.py``; it never replaces it. Every
batch still goes through the same dry plan, the same per-batch authorization
digest, the same gate, C04 admission check, executor, sealing and stop
decision. What the runner adds is a one-time, bounded auto-authorization
envelope and the loop that runs the next batch while the campaign says
CONTINUE.

Commands:

    status        OFFLINE  authoritative state, next batch and its classification,
                           projection; nothing is written
    prepare-auto  OFFLINE  build the bounded envelope, bind the exact child
                           authorization digests, print its digest
    run-auto      NETWORK  only through the unchanged per-batch executor; runs the
                           next batch while the campaign says CONTINUE

Exit codes: 0 success / first-pass targets met, 1 refused, 3 targets already met
(prepare-auto), 5 envelope exhausted, 6 human review required, 7 automation
guard, 130 interrupted.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import essential_web_bulk as historical_tool
import essential_web_calibration_seal as sealer
import essential_web_fast as tool
from filelock import FileLock, Timeout

from xlm.data.acquisition.plan import load_acquisition_plan
from xlm.data.acquisition.source_parquet import identity_path
from xlm.data.evidence_v2 import canonical
from xlm.data.sources import essential_web_bulk as bulk
from xlm.data.sources import essential_web_campaign_runner as runner
from xlm.data.sources import essential_web_fast as fast
from xlm.data.sources import essential_web_recovery as recovery
from xlm.data.sources.essential_web_bulk import GIB

#: The checkout that holds the executing code; tests may relocate it.
CODE_REPO = Path(__file__).resolve().parents[1]
RUNNER_CODE_FILES = (
    "src/xlm/data/sources/essential_web_campaign_runner.py",
    "scripts/essential_web_campaign.py",
)
AUTO_DIR = "auto"
LOG_NAME = "campaign-runner.jsonl"
EXIT_REFUSED, EXIT_TARGET_REACHED = 1, 3
#: Seconds a batch process may take to finish its own cleanup after Ctrl+C.
INTERRUPT_GRACE_SECONDS = 600.0
FAILURES = (ValueError, OSError, KeyError, RuntimeError)

Executor = Callable[[int], int]


@dataclass(frozen=True)
class Context:
    campaign_path: Path
    data_root: Path
    scratch_root: Path | None

    def load(self) -> tool.Campaign:
        return tool.load_campaign(self.campaign_path, self.data_root, self.scratch_root)

    def namespace(self, **extra: Any) -> argparse.Namespace:
        return argparse.Namespace(
            campaign=self.campaign_path,
            data_root=self.data_root,
            scratch_root=self.scratch_root,
            **extra,
        )

    def argv(self) -> list[str]:
        scratch = [] if self.scratch_root is None else ["--scratch-root", str(self.scratch_root)]
        return ["--campaign", str(self.campaign_path), "--data-root", str(self.data_root), *scratch]


def auto_dir(campaign: tool.Campaign) -> Path:
    return campaign.plans / AUTO_DIR


def event_log(campaign: tool.Campaign) -> runner.EventLog:
    return runner.EventLog(auto_dir(campaign) / LOG_NAME)


def reason(exc: BaseException) -> str:
    """Authored refusals keep their text; other errors only their type (no source text)."""
    if isinstance(exc, (bulk.BulkError, OSError)):
        return f"{type(exc).__name__}: {exc}"
    return type(exc).__name__


# ------------------------------------------------------------------- identity


def code_identity(repo: Path) -> dict[str, str]:
    """Running code: transport, local processing, monitor, recovery, driver and runner."""
    code = recovery.code_identity(repo)
    for name in RUNNER_CODE_FILES:
        code[name] = hashlib.sha256((repo / name).read_bytes().replace(b"\r\n", b"\n")).hexdigest()
    return code


def compatibility(repo: Path, campaign: tool.Campaign) -> dict[str, Any]:
    """The recovery amendment in force and every additive code-compatibility record."""
    amendment = campaign.recovery
    records = []
    for relative, kind, _ in recovery.COMPATIBILITY:
        path = repo / relative
        digest = json.loads(path.read_bytes()).get("digest") if path.is_file() else None
        records.append({"path": relative, "kind": kind, "digest": digest})
    return {
        "recovery_amendment": None
        if amendment is None
        else {
            "digest": amendment["digest"],
            "batch": amendment["batch"],
            "file": amendment["file"],
            "max_record_bytes": amendment["max_record_bytes"],
        },
        "records": records,
    }


def identity(campaign: tool.Campaign) -> dict[str, Any]:
    return runner.envelope_identity(
        campaign.config,
        running_code=code_identity(CODE_REPO),
        adapter_code=sealer.code_identity(),
        compatibility=compatibility(CODE_REPO, campaign),
    )


def child(campaign: tool.Campaign, batch: int, workdir: Path) -> dict[str, Any]:
    """Exactly what ``plan`` binds for one batch, minted outside the operator store."""
    config = campaign.config
    files = campaign.members(batch)
    limits = fast.batch_limits(
        len(files),
        config["limits"],
        int(config["scratch"]["cap_bytes"]),
        int(config["concurrency"]["download_workers_max"]),
    ).model_dump()
    directory = workdir / f"b{batch:04d}"
    directory.mkdir(parents=True)
    plan = tool.mint_plan(
        campaign, files, limits, directory, directory / "batch.dry.plan.json", None
    )
    membership = canonical.digest(files)
    return {
        "batch": batch,
        "files": len(files),
        "first_inventory_rank": campaign.rank(batch, 0),
        "membership_digest": membership,
        "plan_hash": plan.plan_hash,
        "selection_hash": plan.compute_selection_hash(),
        "limits_digest": canonical.digest(limits),
        "authorization_digest": fast.authorization_digest(
            config["digest"], batch, membership, plan.plan_hash, limits
        ),
    }


def children(campaign: tool.Campaign, batches: Iterable[int]) -> list[dict[str, Any]]:
    with tempfile.TemporaryDirectory(prefix="ew-auto-") as work:
        return [child(campaign, batch, Path(work)) for batch in batches]


def recorded_child(campaign: tool.Campaign, batch: int) -> dict[str, Any]:
    """The same fields, read from a batch record ``plan`` already wrote."""
    record = tool.batch_record(campaign, batch)
    return {
        "batch": int(record["batch"]),
        "files": len(record["files"]),
        "first_inventory_rank": int(record["inventory_ranks"][0]),
        "membership_digest": record["membership_digest"],
        "plan_hash": record["plan_hash"],
        "selection_hash": record["selection_hash"],
        "limits_digest": canonical.digest(record["limits"]),
        "authorization_digest": record["authorization_digest"],
    }


def membership_proof(campaign: tool.Campaign, batches: Sequence[int]) -> dict[str, Any]:
    """Each batch's members from the fast campaign, the historical one and the raw inventory."""
    config = campaign.config
    historical = historical_tool.load_campaign(
        campaign.repo / config["supersedes"]["path"], campaign.root, campaign.repo
    )
    size = int(config["batch"]["files"])
    names = [str(entry["file"]) for entry in campaign.inventory["files"]]
    proof = []
    for batch in batches:
        fast_members = campaign.members(batch)
        proof.append(
            {
                "batch": batch,
                "membership_digest": canonical.digest(fast_members),
                "historical_campaign_equal": historical.members(batch) == fast_members,
                "inventory_slice_equal": names[batch * size : (batch + 1) * size] == fast_members,
            }
        )
    return {
        "inventory_digest": config["inventory"]["digest"],
        "batches": proof,
        "all_equal": all(
            entry["historical_campaign_equal"] and entry["inventory_slice_equal"] for entry in proof
        ),
    }


# -------------------------------------------------------------- classification


def fingerprint(campaign: tool.Campaign, batch: int) -> dict[str, int]:
    directory = campaign.batch_dir(batch)
    return {
        "events": len(runner.read_events(directory / "events.jsonl")),
        "performance": len(list(directory.glob("performance-*.json"))),
    }


def traces(campaign: tool.Campaign, batch: int) -> list[str]:
    """Evidence that batch execution started; a dry plan or authorization is not."""
    directory = campaign.batch_dir(batch)
    found = [name for name in ("events.jsonl", "top-up.json") if (directory / name).exists()]
    if list(directory.glob("performance-*.json")):
        found.append("performance records")
    units = campaign.unit_dir(batch, campaign.rank(batch, 0)).parent
    if units.is_dir() and any(units.iterdir()):
        found.append("canonical units")
    if campaign.staging(batch).exists():
        found.append("canonical staging")
    scratch = campaign.scratch(f"b{batch:04d}")
    if scratch.is_dir() and any(scratch.iterdir()):
        found.append("scratch checkpoints")
    for name in campaign.members(batch):
        raw = campaign.raw_path(name)
        if raw.exists() or identity_path(raw).exists():
            found.append("retained sources")
            break
    return found


def classify(
    campaign: tool.Campaign, batch: int, log: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """COMPLETE, CLEAN_NOT_STARTED, RESUMABLE or HUMAN_REVIEW_REQUIRED; read-only."""
    state, _ = tool.campaign_state(campaign)
    complete = int(state["complete_batches"])
    if batch < complete:
        return {"batch": batch, "state": runner.COMPLETE, "reasons": ["every unit is sealed"]}
    if batch > complete:
        return {
            "batch": batch,
            "state": runner.HUMAN_REVIEW,
            "reasons": [f"batch {complete} must complete first"],
        }
    directory = campaign.batch_dir(batch)
    review = runner.failed_review(
        batch, runner.matching_mark(batch, fingerprint(campaign, batch), log)
    )
    if review is not None:
        return {"batch": batch, **review}
    found = traces(campaign, batch)
    planned = (directory / "batch.json").is_file()
    authorized = (directory / "batch.plan.json").is_file()
    if not found:
        return {
            "batch": batch,
            "state": runner.CLEAN,
            "reasons": ["no execution evidence"],
            "planned": planned,
            "authorized": authorized,
        }
    try:
        record = tool.batch_record(campaign, batch)
        if not authorized:
            raise bulk.BulkError("execution evidence exists without an authorized plan")
        plan = load_acquisition_plan(directory / "batch.plan.json")
        if plan.plan_hash != record["plan_hash"] or plan.selected_files != record["files"]:
            raise bulk.BulkError("authorized plan is not the planned batch")
        scoped = [
            name
            for position, name in enumerate(record["files"])
            if recovery.unit_scope(campaign, batch, campaign.rank(batch, position), name)
        ]
        if scoped:
            return {
                "batch": batch,
                "state": runner.HUMAN_REVIEW,
                "reasons": [
                    "a recovery amendment is scoped to this batch; only the reviewed "
                    "recovery workflow may resume it"
                ],
                "traces": found,
            }
        resume = recovery.resume_state(campaign, record)
        units, _, _ = tool.prepare_units(
            campaign, plan, record, campaign.scratch(f"b{batch:04d}"), resume
        )
        restart = tool.restart_plan(units, int(campaign.config["limits"]["max_file_bytes"]))
    except FAILURES as exc:
        return {
            "batch": batch,
            "state": runner.HUMAN_REVIEW,
            "reasons": [reason(exc)],
            "traces": found,
        }
    result = runner.classify_started(
        batch, runner.read_events(directory / "events.jsonl"), fingerprint(campaign, batch), log
    )
    return {
        "batch": batch,
        **result,
        "traces": found,
        "sealed": resume["sealed"],
        "total": resume["total"],
        "restart": {key: value for key, value in restart.items() if not key.endswith("_keys")},
    }


# ---------------------------------------------------------------------- status


def free_bytes(path: Path) -> int:
    while not path.exists() and path.parent != path:
        path = path.parent
    return shutil.disk_usage(path).free


def scratch_used(campaign: tool.Campaign) -> int:
    root = campaign.scratch()
    if not root.is_dir():
        return 0
    return sum(path.stat().st_size for path in root.rglob("*") if path.is_file())


def disk(
    campaign: tool.Campaign, state: Mapping[str, Any], guards: Mapping[str, int] | None
) -> dict[str, Any]:
    config = campaign.config
    if campaign.scratch_root is None:
        raise bulk.BulkError("set XLM_SCRATCH_ROOT or --scratch-root")
    return {
        "footprint_bytes": int(state["sealed_including_incomplete_batch"]["footprint_bytes"]),
        "footprint_cap_bytes": int(config["disk"]["footprint_cap_bytes"]),
        "durable_free_bytes": free_bytes(campaign.root),
        "campaign_min_free_bytes": int(config["disk"]["min_free_bytes"]),
        "batch_output_ceiling_bytes": int(config["batch"]["files"])
        * int(config["limits"]["max_durable_bytes_per_file"]),
        "scratch_used_bytes": scratch_used(campaign),
        "scratch_cap_bytes": int(config["scratch"]["cap_bytes"]),
        "scratch_free_bytes": free_bytes(campaign.scratch_root),
        "scratch_min_free_bytes": int(config["scratch"]["min_free_bytes"]),
        "automation_min_durable_free_bytes": None
        if guards is None
        else int(guards["min_durable_free_bytes"]),
        "automation_min_scratch_free_bytes": None
        if guards is None
        else int(guards["min_scratch_free_bytes"]),
    }


def batch_seconds(campaign: tool.Campaign, batch: int) -> float | None:
    """Measured execution wall time of a batch: all its runs, pauses excluded."""
    reports = sorted(campaign.batch_dir(batch).glob("performance-*.json"))
    if not reports:
        return None
    total = 0.0
    for path in reports:
        report = tool.read_json(path)
        wall = (report.get("pipeline") or {}).get("wall_seconds")
        if wall is None:
            wall = (report.get("system") or {}).get("seconds")
        if wall is None:
            return None
        total += float(wall)
    return total


def history(campaign: tool.Campaign, complete: int) -> list[dict[str, Any]]:
    root = campaign.root / str(campaign.config["roots"]["canonical"])
    receipts = [
        tool.load_receipt(path, campaign) for path in sorted(root.glob("b*/f*/receipt.json"))
    ]
    entries = []
    for batch in range(complete):
        mine = [receipt for receipt in receipts if int(receipt["batch"]) == batch]
        entries.append(
            {
                "batch": batch,
                "files": len(mine),
                "rows": sum(int(receipt["rows"]) for receipt in mine),
                "tokens": {
                    view: runner.tokens(
                        sum(int(receipt["views"][view]["canonical_bytes"]) for receipt in mine)
                    )
                    for view in campaign.config["views"]
                },
                "footprint_bytes": sum(int(receipt["footprint_bytes"]) for receipt in mine),
                "seconds": batch_seconds(campaign, batch),
            }
        )
    return entries


def elapsed_since(stamp: str | None) -> float | None:
    if stamp is None:
        return None
    return (datetime.now(UTC) - datetime.fromisoformat(stamp)).total_seconds()


def inspect(
    campaign: tool.Campaign,
    log: Sequence[Mapping[str, Any]],
    *,
    guards: Mapping[str, int] | None = None,
    started_at: str | None = None,
    last_batch_seconds: float | None = None,
    classify_next: bool = True,
) -> dict[str, Any]:
    """The structured campaign state; read-only."""
    config = campaign.config
    state, decision = tool.campaign_state(campaign)
    complete = int(state["complete_batches"])
    sealed = state["sealed_including_incomplete_batch"]
    views = {}
    for view in config["views"]:
        entry = decision["views"][view]
        views[view] = {
            "status": entry["status"],
            "canonical_bytes": int(entry["acquired_canonical_bytes"]),
            "required_canonical_bytes": int(entry["required_canonical_bytes"]),
            "estimated_tokens": float(entry["estimated_tokens"]),
            "target_tokens": int(
                config["stop"]["targets"][view]["first_pass_estimated_token_target"]
            ),
            "deficit_tokens": runner.tokens(int(entry["deficit_canonical_bytes"])),
        }
    entries = history(campaign, complete)
    if started_at is None:
        starts = [r["at"] for r in log if r.get("event") == "automation_start"]
        started_at = starts[0] if starts else None
    ceiling = int(config["ceiling"]["max_batches"])
    result: dict[str, Any] = {
        "kind": "essential_web_auto_campaign_status",
        "campaign": config["digest"],
        "decision": "TARGET_REACHED" if decision["target_reached"] else "CONTINUE",
        "complete_batches": complete,
        "next_batch": complete,
        "ceiling_batches": ceiling,
        "sealed_files": int(sealed["files"]),
        "rows": int(sealed["rows"]),
        "counted_rows": int(state["counted"]["rows"]),
        "views": views,
        "disk": disk(campaign, state, guards),
        "history": entries,
        "projection": runner.projection(entries, views),
        "time": {
            "campaign_elapsed_seconds": elapsed_since(started_at),
            "last_batch_seconds": last_batch_seconds,
        },
        "c05": config["c05"]["status"],
        "training_permitted": bool(decision["training_permitted"]),
        "network": "none",
    }
    if classify_next and not decision["target_reached"] and complete < ceiling:
        result["next_batch_membership_digest"] = canonical.digest(campaign.members(complete))
        result["next_batch_classification"] = classify(campaign, complete, log)
    return result


def guard_reasons(campaign: tool.Campaign, guards: Mapping[str, int]) -> list[str]:
    state, _ = tool.campaign_state(campaign)
    space = disk(campaign, state, guards)
    reasons = []
    if space["durable_free_bytes"] < int(guards["min_durable_free_bytes"]):
        reasons.append(
            f"durable free space {space['durable_free_bytes']:,} B is below the automation "
            f"guard {guards['min_durable_free_bytes']:,} B"
        )
    if space["scratch_free_bytes"] < int(guards["min_scratch_free_bytes"]):
        reasons.append(
            f"scratch free space {space['scratch_free_bytes']:,} B is below the automation "
            f"guard {guards['min_scratch_free_bytes']:,} B"
        )
    if space["scratch_used_bytes"] > space["scratch_cap_bytes"]:
        reasons.append("scratch occupancy already exceeds the campaign scratch cap")
    return reasons


# ------------------------------------------------------------------ commands


def show(lines: Iterable[str], stream: Any = None) -> None:
    output = sys.stdout if stream is None else stream
    output.write("\n".join(lines) + "\n")
    output.flush()


def cmd_status(args: argparse.Namespace) -> int:
    campaign = context(args).load()
    status = inspect(campaign, event_log(campaign).read())
    if args.json:
        print(json.dumps(status, indent=2, sort_keys=True))
        return 0
    show(runner.campaign_header(status))
    if status["decision"] == "TARGET_REACHED":
        show(runner.completion_banner(status))
    elif "next_batch_classification" in status:
        verdict = status["next_batch_classification"]
        show(
            [
                f"next batch {status['next_batch']}: {verdict['state']} "
                f"membership {status['next_batch_membership_digest']}",
                *(f"  {text}" for text in verdict["reasons"]),
            ]
        )
    return 0


def default_guards(
    campaign: tool.Campaign, durable_gib: int | None, scratch_gib: int | None
) -> dict[str, int]:
    scratch = campaign.config["scratch"]
    return {
        "min_durable_free_bytes": runner.DEFAULT_MIN_DURABLE_FREE_BYTES
        if durable_gib is None
        else durable_gib * GIB,
        "min_scratch_free_bytes": int(scratch["cap_bytes"]) + int(scratch["min_free_bytes"])
        if scratch_gib is None
        else scratch_gib * GIB,
    }


def prepare(
    campaign: tool.Campaign,
    *,
    start: int | None,
    max_batches: int,
    operator: str,
    guards: Mapping[str, int],
) -> dict[str, Any]:
    """Offline: the envelope and the evidence the operator reviews before approving it."""
    config = campaign.config
    log = event_log(campaign).read()
    state, decision = tool.campaign_state(campaign)
    if decision["target_reached"]:
        raise bulk.BulkError("first-pass targets are met; there is nothing to automate")
    following = int(state["complete_batches"])
    start = following if start is None else start
    if start != following:
        raise bulk.BulkError(f"start batch {start} is not the authoritative next batch {following}")
    size = int(config["batch"]["files"])
    end, clamped = runner.batch_range(
        start,
        max_batches,
        int(config["ceiling"]["max_batches"]),
        math.ceil(len(campaign.inventory["files"]) / size),
    )
    verdict = classify(campaign, start, log)
    if verdict["state"] not in (runner.CLEAN, runner.RESUMABLE):
        raise bulk.BulkError(f"batch {start} is {verdict['state']}: {verdict['reasons']}")
    kids = children(campaign, range(start, end))
    for kid in kids:
        if (campaign.batch_dir(int(kid["batch"])) / "batch.json").is_file():
            diff = runner.differences(kid, recorded_child(campaign, int(kid["batch"])))
            if diff:
                raise bulk.BulkError(f"batch {kid['batch']} is already planned differently: {diff}")
    # The derivation must reproduce every batch the manual driver already planned.
    planned = [b for b in range(start) if (campaign.batch_dir(b) / "batch.json").is_file()]
    reproduced = []
    for kid in children(campaign, planned):
        equal = kid == recorded_child(campaign, int(kid["batch"]))
        reproduced.append({"batch": kid["batch"], "authorization_digest_equal": equal})
        if not equal:
            raise bulk.BulkError(f"batch {kid['batch']}: derivation differs from its plan record")
    envelope = runner.make_envelope(
        identity(campaign),
        kids,
        start=start,
        end=end,
        max_batches=max_batches,
        clamped=clamped,
        guards=guards,
        operator=operator,
    )
    status = inspect(campaign, log, guards=guards, classify_next=False)
    worst = (end - start) * int(status["disk"]["batch_output_ceiling_bytes"])
    return {
        "kind": "essential_web_auto_campaign_prepare",
        "envelope": envelope,
        "next_batch_classification": verdict,
        "membership_proof": membership_proof(campaign, list(range(start, end))),
        "reproduced_existing_plans": reproduced,
        "status": status,
        "space": {
            "worst_case_durable_bytes": worst,
            "footprint_after_worst_case_bytes": status["disk"]["footprint_bytes"] + worst,
            "footprint_cap_bytes": status["disk"]["footprint_cap_bytes"],
            "projected_additional_footprint_bytes": status["projection"].get(
                "projected_additional_footprint_bytes"
            ),
            "note": "worst case is every envelope batch at its per-batch durable ceiling; the "
            "campaign gate and the footprint cap still refuse any batch that would not fit",
        },
        "first_child": kids[0],
        "stop_conditions": list(runner.STOP_CONDITIONS),
        "network": "none",
    }


def cmd_prepare_auto(args: argparse.Namespace) -> int:
    campaign = context(args).load()
    if tool.campaign_state(campaign)[1]["target_reached"]:
        print("essential_web_campaign: first-pass targets are met; nothing to automate")
        return EXIT_TARGET_REACHED
    report = prepare(
        campaign,
        start=args.start_batch,
        max_batches=args.max_batches,
        operator=args.operator,
        guards=default_guards(campaign, args.min_durable_free_gib, args.min_scratch_free_gib),
    )
    envelope = report["envelope"]
    path = auto_dir(campaign) / "envelopes" / f"{envelope['digest']}.json"
    report["written"] = None
    if not args.dry:
        tool.write_once(path, envelope)
        report["written"] = str(path)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0
    rng, first = envelope["range"], report["first_child"]
    identity_ = envelope["identity"]
    show(runner.campaign_header(report["status"]))
    show(
        [
            "AUTO-CAMPAIGN ENVELOPE (offline; no network request was made)",
            f"  campaign            {identity_['campaign']}",
            f"  source              {identity_['source']['repository']} @ "
            f"{identity_['source']['revision']}",
            f"  selector            {identity_['selector']['selector_id']} freeze "
            f"{identity_['selector']['freeze_digest']}",
            f"  inventory           {identity_['inventory']['digest']}",
            f"  batches             {rng['start_batch']} .. {rng['end_batch_exclusive'] - 1} "
            f"({rng['end_batch_exclusive'] - rng['start_batch']} at most"
            + (
                ", shortened by the campaign ceiling)"
                if rng["clamped_by_campaign_ceiling"]
                else ")"
            ),
            f"  next batch          {first['batch']}: "
            f"{report['next_batch_classification']['state']} "
            f"membership {first['membership_digest']}",
            f"                      plan {first['plan_hash']}",
            f"                      batch authorization {first['authorization_digest']}",
            f"  membership proof    fast = historical campaign = inventory slice: "
            f"{report['membership_proof']['all_equal']}",
            f"  reproduced plans    {len(report['reproduced_existing_plans'])} existing batch "
            "authorizations re-derived identically",
            f"  guards              durable free >= "
            f"{envelope['automation_guards']['min_durable_free_bytes']:,} B, scratch free >= "
            f"{envelope['automation_guards']['min_scratch_free_bytes']:,} B",
            f"  worst-case durable  {report['space']['worst_case_durable_bytes']:,} B "
            f"(footprint cap {report['space']['footprint_cap_bytes']:,} B)",
            f"  operator            {envelope['operator']}",
            "STOPS ON",
            *(f"  - {text}" for text in report["stop_conditions"]),
            "NEVER APPROVES",
            *(f"  - {text}" for text in envelope["never_approves"]),
            f"envelope file: {report['written'] or 'not written (--dry)'}",
            f"AUTO AUTHORIZATION DIGEST: {envelope['digest']}",
        ]
    )
    return 0


# ------------------------------------------------------------------- the loop


class SubprocessExecutor:
    """The unchanged per-batch executor in its own process, on the operator's terminal."""

    def __init__(
        self,
        ctx: Context,
        *,
        python: str = sys.executable,
        script: Path | None = None,
        grace_seconds: float = INTERRUPT_GRACE_SECONDS,
    ) -> None:
        self.ctx, self.python, self.grace = ctx, python, grace_seconds
        self.script = CODE_REPO / "scripts/essential_web_fast.py" if script is None else script

    def command(self, batch: int) -> list[str]:
        return [self.python, str(self.script), *self.ctx.argv(), "run", "--batch", str(batch)]

    def __call__(self, batch: int) -> int:
        process = subprocess.Popen(self.command(batch))
        try:
            while True:
                try:
                    return process.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    continue
        except KeyboardInterrupt:
            # The batch process received the same Ctrl+C; let it record its own state.
            try:
                process.wait(timeout=self.grace)
            except subprocess.TimeoutExpired:
                pass
            raise
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()


class Stop(Exception):
    """A decision to end the loop; ``outcome`` is one of the runner outcomes."""

    def __init__(self, outcome: str, reasons: list[str], batch: int | None = None) -> None:
        super().__init__(outcome, reasons, batch)
        self.outcome, self.reasons, self.batch = outcome, reasons, batch


class Loop:
    """Run the next batch while the campaign says CONTINUE, inside one envelope."""

    def __init__(
        self,
        ctx: Context,
        envelope: dict[str, Any],
        operator: str,
        executor: Executor,
        stream: Any,
        log: runner.EventLog,
    ) -> None:
        self.ctx, self.envelope, self.operator = ctx, envelope, operator
        self.executor, self.stream, self.log = executor, stream, log
        self.digest = str(envelope["digest"])
        self.guards: dict[str, int] = dict(envelope["automation_guards"])
        self.range: dict[str, Any] = dict(envelope["range"])
        self.started_at: str | None = None
        self.last_batch_seconds: float | None = None
        self.completed_this_session: list[int] = []

    def status(self, campaign: tool.Campaign) -> dict[str, Any]:
        return inspect(
            campaign,
            self.log.read(),
            guards=self.guards,
            started_at=self.started_at,
            last_batch_seconds=self.last_batch_seconds,
            classify_next=False,
        )

    def begin(self, campaign: tool.Campaign) -> None:
        self.check_identity(campaign)
        directory = auto_dir(campaign) / "authorizations"
        path = directory / f"{self.digest}.json"
        first = not path.exists()
        tool.write_once(
            path,
            {"kind": runner.ENVELOPE_KIND, "envelope": self.digest, "operator": self.operator},
        )
        if first:
            self.log.append("operator_authorization", envelope=self.digest, operator=self.operator)
        starts = [
            record
            for record in self.log.read()
            if record.get("event") == "automation_start" and record.get("envelope") == self.digest
        ]
        if starts:
            self.started_at = str(starts[0]["at"])
            self.log.append("automation_resume", envelope=self.digest, range=self.range)
        else:
            self.started_at = str(
                self.log.append("automation_start", envelope=self.digest, range=self.range)["at"]
            )

    def check_identity(self, campaign: tool.Campaign) -> None:
        found = runner.differences(self.envelope["identity"], identity(campaign))
        if found:
            raise Stop(runner.REFUSED, [f"identity differs from the envelope at {found[:12]}"])

    def authorize(self, campaign: tool.Campaign, batch: int) -> dict[str, Any]:
        """The manual Prepare + authorize path, gated on the envelope's exact child."""
        expected = runner.child_for(self.envelope, batch)
        if expected is None:
            raise Stop(runner.ENVELOPE_EXHAUSTED, [f"batch {batch} is outside the envelope"])
        (current,) = children(campaign, [batch])
        found = runner.differences(expected, current)
        if found:
            raise Stop(runner.REFUSED, [f"batch {batch} derivation differs at {found}"], batch)
        if not (campaign.batch_dir(batch) / "batch.json").is_file():
            tool.cmd_plan(self.ctx.namespace(batch=batch))
        found = runner.differences(expected, recorded_child(campaign, batch))
        if found:
            raise Stop(runner.REFUSED, [f"batch {batch} record differs at {found}"], batch)
        tool.cmd_authorize(
            self.ctx.namespace(
                batch=batch,
                digest=expected["authorization_digest"],
                operator=f"{self.operator} (auto {self.digest[:16]})",
            )
        )
        plan = load_acquisition_plan(campaign.batch_dir(batch) / "batch.plan.json")
        if plan.plan_hash != expected["plan_hash"]:
            raise Stop(runner.REFUSED, ["authorized plan differs from the envelope"], batch)
        try:
            tool.check_admission(plan)
        except Exception as exc:  # any C04 refusal stops; only its type is reported
            raise Stop(
                runner.REFUSED, [f"C04 production admission failed: {type(exc).__name__}"], batch
            ) from exc
        return expected

    def step(self) -> None:
        """Run one batch to a verified seal, or raise Stop."""
        try:
            campaign = self.ctx.load()
        except FAILURES as exc:
            raise Stop(runner.REFUSED, [f"campaign refused to load: {reason(exc)}"]) from exc
        self.check_identity(campaign)
        state, decision = tool.campaign_state(campaign)
        status = self.status(campaign)
        show(runner.campaign_header(status), self.stream)
        if decision["target_reached"]:
            show(runner.completion_banner(status), self.stream)
            raise Stop(runner.FIRST_PASS_COMPLETE, ["all first-pass targets are sufficient"])
        batch = int(state["complete_batches"])
        if batch < int(self.range["start_batch"]):
            raise Stop(runner.REFUSED, [f"complete batches regressed below the envelope ({batch})"])
        if batch >= int(self.range["end_batch_exclusive"]):
            ceiling = int(campaign.config["ceiling"]["max_batches"])
            raise Stop(
                runner.ENVELOPE_EXHAUSTED,
                [
                    f"the campaign execution ceiling ({ceiling} batches) is reached while "
                    "targets are insufficient; a new reviewed campaign version is required"
                    if batch >= ceiling
                    else f"the envelope ends before batch {batch} while targets are "
                    "insufficient; a new operator authorization is required"
                ],
                batch,
            )
        verdict = classify(campaign, batch, self.log.read())
        if verdict["state"] not in (runner.CLEAN, runner.RESUMABLE):
            raise Stop(runner.HUMAN_REVIEW, list(verdict["reasons"]), batch)
        guard = guard_reasons(campaign, self.guards)
        if guard:
            raise Stop(runner.GUARD_STOP, guard, batch)
        gate = tool.evaluate_gate(campaign, batch, "", None)
        if gate["decision"] != "RUN":
            raise Stop(runner.REFUSED, [f"gate {gate['decision']}", *gate["reasons"]], batch)
        expected = self.authorize(campaign, batch)
        self.log.append(
            "batch_start",
            envelope=self.digest,
            batch=batch,
            classification=verdict["state"],
            sealed_before=verdict.get("sealed", 0),
            authorization_digest=expected["authorization_digest"],
        )
        show([f"AUTO: batch {batch} {verdict['state']} -> run"], self.stream)
        clock = time.monotonic()
        try:
            code = self.executor(batch)
        except KeyboardInterrupt:
            # Tied to the exact artifacts left behind; any later change voids it.
            self.log.append(
                runner.MARK_INTERRUPTED,
                envelope=self.digest,
                batch=batch,
                fingerprint=fingerprint(campaign, batch),
            )
            raise Stop(
                runner.INTERRUPTED, ["operator interruption during the batch"], batch
            ) from None
        except Exception as exc:  # the batch process itself failed; never retried silently
            self.log.append(
                runner.MARK_FAILED,
                envelope=self.digest,
                batch=batch,
                exit_code=None,
                exception=type(exc).__name__,
                fingerprint=fingerprint(campaign, batch),
            )
            raise Stop(
                runner.HUMAN_REVIEW, [f"batch process failed: {type(exc).__name__}"], batch
            ) from exc
        seconds = time.monotonic() - clock
        if code not in (0, tool.EXIT_TARGET_REACHED):
            self.log.append(
                runner.MARK_FAILED,
                envelope=self.digest,
                batch=batch,
                exit_code=code,
                fingerprint=fingerprint(campaign, batch),
            )
            raise Stop(runner.HUMAN_REVIEW, [f"batch process exited {code}"], batch)
        self.verify(batch, seconds)

    def verify(self, batch: int, seconds: float) -> None:
        try:
            campaign = self.ctx.load()
            state, decision = tool.campaign_state(campaign)
            record = tool.batch_record(campaign, batch)
            resume = recovery.resume_state(campaign, record)
        except FAILURES as exc:
            raise Stop(
                runner.HUMAN_REVIEW, [f"post-batch verification: {reason(exc)}"], batch
            ) from exc
        if int(state["complete_batches"]) != batch + 1 or resume["sealed"] != resume["total"]:
            self.log.append(
                runner.MARK_FAILED,
                envelope=self.digest,
                batch=batch,
                exit_code=0,
                fingerprint=fingerprint(campaign, batch),
            )
            raise Stop(runner.HUMAN_REVIEW, [f"batch {batch} is not sealed after its run"], batch)
        self.last_batch_seconds = seconds
        self.completed_this_session.append(batch)
        self.log.append(
            "batch_complete",
            envelope=self.digest,
            batch=batch,
            seconds=seconds,
            sealed=resume["sealed"],
            complete_batches=state["complete_batches"],
            sufficiency={
                view: {
                    "status": entry["status"],
                    "estimated_tokens": entry["estimated_tokens"],
                }
                for view, entry in decision["views"].items()
            },
            target_reached=decision["target_reached"],
        )
        show(
            [
                f"AUTO: batch {batch} sealed and verified: {resume['sealed']}/{resume['total']} "
                f"units in {seconds:.0f} s"
            ],
            self.stream,
        )

    def run(self) -> dict[str, Any]:
        stop: Stop | None = None
        try:
            self.begin(self.ctx.load())
            span = int(self.range["end_batch_exclusive"]) - int(self.range["start_batch"])
            for _ in range(span + 1):
                self.step()
            stop = Stop(runner.REFUSED, ["loop bound reached without a stop decision"])
        except Stop as found:
            stop = found
        except KeyboardInterrupt:
            stop = Stop(runner.INTERRUPTED, ["operator interruption between batches"])
        except FAILURES as exc:
            stop = Stop(runner.REFUSED, [reason(exc)])
        outcome = {
            "kind": "essential_web_auto_campaign_outcome",
            "envelope": self.digest,
            "outcome": stop.outcome,
            "exit_code": runner.EXIT_CODES[stop.outcome],
            "reasons": stop.reasons,
            "batch": stop.batch,
            "completed_this_session": self.completed_this_session,
        }
        kind = (
            "safe_stop"
            if stop.outcome
            in (
                runner.FIRST_PASS_COMPLETE,
                runner.ENVELOPE_EXHAUSTED,
                runner.GUARD_STOP,
                runner.INTERRUPTED,
            )
            else "fatal_stop"
        )
        self.log.append(kind, **{k: v for k, v in outcome.items() if k != "kind"})
        show([f"AUTO STOP: {stop.outcome}", *(f"  {text}" for text in stop.reasons)], self.stream)
        return outcome


def run_auto(
    ctx: Context,
    digest: str,
    operator: str,
    executor: Executor,
    stream: Any = None,
) -> dict[str, Any]:
    stream = sys.stdout if stream is None else stream

    def refuse(text: str) -> dict[str, Any]:
        show([f"AUTO STOP: {runner.REFUSED}", f"  {text}"], stream)
        return {
            "kind": "essential_web_auto_campaign_outcome",
            "envelope": digest,
            "outcome": runner.REFUSED,
            "exit_code": runner.EXIT_CODES[runner.REFUSED],
            "reasons": [text],
            "batch": None,
            "completed_this_session": [],
        }

    try:
        campaign = ctx.load()
    except FAILURES as exc:
        return refuse(f"campaign refused to load: {reason(exc)}")
    path = auto_dir(campaign) / "envelopes" / f"{digest}.json"
    if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest) or not path.is_file():
        return refuse("no prepared envelope has this digest; run prepare-auto first")
    envelope: dict[str, Any] = tool.read_json(path)
    try:
        runner.check_envelope(envelope)
    except bulk.BulkError as exc:
        return refuse(str(exc))
    if envelope["digest"] != digest:
        return refuse("envelope file does not hold this digest")
    if operator.strip() != envelope["operator"]:
        return refuse("the authorizing operator differs from the envelope's operator")
    auto_dir(campaign).mkdir(parents=True, exist_ok=True)
    try:
        with FileLock(str(auto_dir(campaign) / "runner.lock"), timeout=1):
            return Loop(
                ctx, envelope, operator.strip(), executor, stream, event_log(campaign)
            ).run()
    except Timeout:
        return refuse("another automatic runner of this campaign is active")


def cmd_run_auto(args: argparse.Namespace) -> int:
    ctx = context(args)
    outcome = run_auto(ctx, args.authorize, args.operator, SubprocessExecutor(ctx))
    if args.outcome is not None:
        tool.write_json(args.outcome, outcome)
    return int(outcome["exit_code"])


# ------------------------------------------------------------------------ cli


def context(args: argparse.Namespace) -> Context:
    return Context(args.campaign, args.data_root, args.scratch_root)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--campaign", type=Path, default=CODE_REPO / tool.FAST_DIR / "campaign.json"
    )
    parser.add_argument("--data-root", type=Path, default=None)
    parser.add_argument("--scratch-root", type=Path, default=None)
    sub = parser.add_subparsers(dest="command", required=True)
    status = sub.add_parser("status", help="OFFLINE, read-only: state, next batch, projection.")
    status.add_argument("--json", action="store_true")
    status.set_defaults(func=cmd_status)
    prepare_parser = sub.add_parser("prepare-auto", help="OFFLINE: build the bounded envelope.")
    prepare_parser.add_argument("--start-batch", type=int, default=None)
    prepare_parser.add_argument("--max-batches", type=int, default=runner.MAX_AUTO_BATCHES)
    prepare_parser.add_argument("--operator", required=True)
    prepare_parser.add_argument("--min-durable-free-gib", type=int, default=None)
    prepare_parser.add_argument("--min-scratch-free-gib", type=int, default=None)
    prepare_parser.add_argument("--dry", action="store_true", help="Do not write the envelope.")
    prepare_parser.add_argument("--json", action="store_true")
    prepare_parser.set_defaults(func=cmd_prepare_auto)
    run = sub.add_parser("run-auto", help="NETWORK via the batch executor: run while CONTINUE.")
    run.add_argument("--authorize", required=True)
    run.add_argument("--operator", required=True)
    run.add_argument("--outcome", type=Path, default=None, help="Write the outcome JSON here.")
    run.set_defaults(func=cmd_run_auto)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.data_root is None:
        configured = os.environ.get("XLM_DATA_ROOT", "")
        if not configured:
            print(
                "essential_web_campaign: refused: set XLM_DATA_ROOT or --data-root", file=sys.stderr
            )
            return EXIT_REFUSED
        args.data_root = Path(configured)
    if args.scratch_root is None:
        configured = os.environ.get("XLM_SCRATCH_ROOT", "")
        if not configured:
            print(
                "essential_web_campaign: refused: set XLM_SCRATCH_ROOT or --scratch-root",
                file=sys.stderr,
            )
            return EXIT_REFUSED
        args.scratch_root = Path(configured)
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["HF_DATASETS_OFFLINE"] = "1"
    try:
        return int(args.func(args))
    except FAILURES as exc:
        print(f"essential_web_campaign: refused: {reason(exc)}", file=sys.stderr)
        return EXIT_REFUSED


if __name__ == "__main__":
    raise SystemExit(main())
