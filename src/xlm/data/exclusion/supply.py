"""Stage 2 of the C05 policy audit: counterfactual token supply under the CURRENT tokenizer.

Runs with the protected volume DETACHED (tokenization is never done while it is mounted).
Inputs: the stage-1 state (:mod:`xlm.data.exclusion.counterfactual`, ``--state-out``:
plan file, row, allocation and per-combination train membership of every document whose
train membership differs from production; no text), the C05 plan (corpus files), the
signed exact counts (``counts.json``: current per-allocation valid targets and the
tokenizer fingerprint), the selection deficit report (frozen quota per allocation) and
the current tokenizer directory.

Per combination and allocation::

    projected = current exact valid targets
                - valid targets of production-train documents that leave train
                + valid targets of documents that enter train

Documents leaving train are always tokenized (they can only lower supply). Documents
entering train are tokenized for the ``--token-allocation`` set (default: allocations the
deficit report marks DEFICIT); elsewhere the projection is a stated lower bound. Counts use
``BaseTokenizer.count_valid_targets`` (the c05-valid-targets-v1 rule of count-tokens) on
strict canonical rows of files that must equal the plan (size, SHA-256, rows).

These are "counterfactual supply under current tokenizer" numbers: a fresh C05 under a
changed policy would require refitting C06 (and recounting) afterwards.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
import numpy.typing as npt

from xlm.data.evidence_v2 import canonical
from xlm.data.exclusion.artifacts import ExecutionPlan
from xlm.data.exclusion.counterfactual import (
    STATE_KIND,
    AuditError,
    LineSpan,
    ReadState,
    by_file,
    canonical_document,
    span_tasks,
    verify_content_free,
)
from xlm.data.exclusion.fitscan import OrderedPool
from xlm.data.exclusion.forensics import Telemetry, key_of
from xlm.data.exclusion.inputs import read_metadata
from xlm.data.exclusion.policy import C05Error
from xlm.data.exclusion.progress import MiB, NullProgress, RunProgress
from xlm.data.exclusion.selection import load_tokenizer
from xlm.data.exclusion.supervisor import Deadline, Supervisor

if TYPE_CHECKING:
    from xlm.tokenizers.base import BaseTokenizer

LABEL = "TOKEN SUPPLY"
WORKER_CHOICES = (1, 2, 4, 8, 16)
BATCH_TEXT_BYTES = 4 * MiB
STATE_ARRAYS = ("allocation", "plan_file", "production_train", "row", "train_bits")
Progress = RunProgress | NullProgress


@dataclass(frozen=True)
class TokenTables:
    tokenizer: str
    fingerprint: str


_TOKENIZER: BaseTokenizer | None = None


def init_worker(tables: TokenTables) -> None:
    global _TOKENIZER
    os.environ["TOKENIZERS_PARALLELISM"] = "false"
    os.environ["RAYON_NUM_THREADS"] = "1"
    tokenizer = load_tokenizer(Path(tables.tokenizer))
    if tokenizer.fingerprint != tables.fingerprint:
        raise C05Error("supply tokenizer differs from its verified fingerprint")
    _TOKENIZER = tokenizer


@dataclass(frozen=True)
class TokenTask:
    span: LineSpan
    positions: tuple[int, ...]  # state index of each line
    source_id: str
    revision: str


def count_rows(task: TokenTask) -> tuple[tuple[int, ...], list[int]]:
    """Exact valid targets of each line, batched into bounded native calls."""
    if _TOKENIZER is None:
        raise AuditError("supply worker tokenizer missing")
    counts: list[int] = []
    texts: list[str] = []
    size = 0

    def flush() -> None:
        nonlocal size
        if texts:
            assert _TOKENIZER is not None
            found = _TOKENIZER.count_valid_targets(texts)
            if len(found) != len(texts):
                raise AuditError("tokenizer returned a different number of counts")
            counts.extend(int(c) for c in found)
            texts.clear()
            size = 0

    for line in task.span.lines():
        doc = canonical_document(line, task.source_id, task.revision)
        texts.append(doc.text)
        size += len(doc.text)
        if size >= BATCH_TEXT_BYTES:
            flush()
    flush()
    return task.positions, counts


def tokenizer_files(directory: Path) -> dict[str, str]:
    files: dict[str, str] = {}
    for entry in sorted(directory.iterdir()):
        if not entry.is_file():
            raise AuditError("tokenizer artifact entry is not a regular file")
        files[entry.name] = hashlib.sha256(entry.read_bytes()).hexdigest()
    return files


def load_state(path: Path) -> tuple[dict[str, Any], dict[str, npt.NDArray[Any]]]:
    with np.load(path, allow_pickle=False) as data:
        if set(data.files) != {"header", *STATE_ARRAYS}:
            raise AuditError("policy audit state layout")
        header = canonical.loads_bytes_strict(data["header"].tobytes())
        arrays = {k: np.asarray(data[k]) for k in STATE_ARRAYS}
    if not isinstance(header, dict) or header.get("kind") != STATE_KIND:
        raise AuditError("policy audit state kind")
    digest = hashlib.sha256(
        b"".join(np.ascontiguousarray(arrays[k]).tobytes() for k in sorted(arrays))
    ).hexdigest()
    if digest != header.get("arrays_sha256"):
        raise AuditError("policy audit state arrays changed")
    return header, arrays


def run(args: argparse.Namespace, progress: Progress, stage: list[str]) -> dict[str, Any]:
    started = time.monotonic()
    telemetry = Telemetry()

    def begin(name: str, total: int | None = None, unit: str = "steps") -> None:
        telemetry()
        stage[0] = name
        progress.stage(name, total, unit)

    begin("INPUT VERIFY", 4)
    plan = ExecutionPlan.model_validate(read_metadata(args.plan, digested=False))
    identity = plan.identity()
    header, state = load_state(args.state)
    counts = read_metadata(args.counts, digested=False)
    counts_body = counts.get("payload", counts)
    deficit = json.loads(args.deficit_report.read_bytes())
    deficit_body = deficit.get("payload", deficit)
    for name, value in (
        ("state", header.get("plan_digest")),
        ("counts", counts_body.get("plan_digest")),
        ("deficit report", deficit_body.get("plan_digest")),
    ):
        if value != identity:
            raise AuditError(f"{name} names a different C05 plan")
    completion = header.get("completion_digest")
    if completion is not None and (
        counts_body.get("completion_digest") != completion
        or deficit_body.get("completion_digest") != completion
    ):
        raise AuditError("state, counts and deficit report name different C05 completions")
    progress.update(1)
    expected = (counts_body.get("tokenizer") or {}).get("fingerprint")
    if not isinstance(expected, str):
        raise AuditError("counts carry no tokenizer fingerprint")
    if args.expect_fingerprint is not None and args.expect_fingerprint != expected:
        raise AuditError("counts tokenizer differs from the expected fingerprint")
    before = tokenizer_files(args.tokenizer)
    tokenizer = load_tokenizer(args.tokenizer)
    if tokenizer.fingerprint != expected:
        raise AuditError("tokenizer differs from the one that produced the exact counts")
    progress.update(2)
    names: list[str] = list(header["allocations"])
    combos: list[str] = list(header["combinations"])
    files = list(plan.files)
    plan_names = sorted({key_of(f.component, f.view, f.upstream_component) for f in files})
    if names != plan_names:
        raise AuditError("state allocations differ from the C05 plan")
    current = {k: int(v["valid_targets"]) for k, v in counts_body["allocations"].items()}
    quota = {k: int(v["quota"]) for k, v in deficit_body["allocations"].items()}
    if set(current) != set(names) or set(quota) != set(names):
        raise AuditError("counts or deficit report do not cover every allocation")
    if args.token_allocation == ["all"]:
        token_set = set(range(len(names)))
    elif args.token_allocation:
        token_set = set()
        for item in args.token_allocation:
            component, view, upstream = item.split("/")
            label = key_of(component, view, None if upstream == "-" else upstream)
            if label not in names:
                raise AuditError("token allocation is not in the C05 plan")
            token_set.add(names.index(label))
    else:
        token_set = {
            n
            for n, k in enumerate(names)
            if deficit_body["allocations"][k].get("status") == "DEFICIT"
        }
    progress.update(3)
    file = state["plan_file"].astype(np.int64)
    row = state["row"].astype(np.int64)
    alloc = state["allocation"].astype(np.int64)
    prod = state["production_train"].astype(bool)
    bits = state["train_bits"].astype(np.int64)
    every = (1 << len(combos)) - 1
    leaves = prod & (bits != every)
    enters = ~prod & (bits != 0)
    tokenize = leaves | (enters & np.isin(alloc, sorted(token_set)))
    progress.update(4, force=True)

    picked = np.flatnonzero(tokenize)
    grouped = by_file(picked, file[picked], len(files))
    read = [f for f in range(len(files)) if grouped[f].size]
    wanted = {f: np.sort(row[grouped[f]]) for f in read}
    position = {(int(file[k]), int(row[k])): int(k) for k in picked.tolist()}
    total_docs = sum(int(files[f].documents) for f in read)
    total_bytes = sum(int(files[f].file_bytes) for f in read)
    begin("TOKENIZE", total_docs, "docs")
    valid = np.full(file.size, -1, np.int64)
    root = Path(plan.data_root)
    supervisor = Supervisor(Deadline(None, time.monotonic()), None)
    read_state = ReadState()

    def jobs() -> Iterator[TokenTask]:
        for f, span, rows in span_tasks(root, files, read, wanted, read_state):
            yield TokenTask(
                span,
                tuple(position[(f, r)] for r in rows),
                files[f].source_id,
                files[f].source_revision,
            )

    t0 = time.monotonic()
    with supervisor:
        with OrderedPool(
            args.workers,
            TokenTables(str(args.tokenizer), expected),
            supervisor,
            inline=args.workers == 1,
            initializer=init_worker,
        ) as pool:
            for positions, found in pool.map(count_rows, jobs(), 4 * pool.workers):
                valid[list(positions)] = found
                elapsed = max(time.monotonic() - t0, 1e-9)
                progress.update(
                    read_state.docs,
                    bytes_done=read_state.bytes,
                    bytes_total=total_bytes,
                    files_committed=read_state.files,
                    files_total=len(read),
                    mib_per_s=read_state.bytes / MiB / elapsed,
                    workers=pool.workers,
                    busy=pool.workers,
                )
    scanned_bytes = read_state.bytes
    if read_state.files != len(read):
        raise AuditError("token pass did not reach every wanted file")
    if np.any(valid[picked] < 0):
        raise AuditError("token pass did not reach every wanted document")
    if tokenizer_files(args.tokenizer) != before:
        raise AuditError("tokenizer artifact changed during the token pass")

    begin("PROJECTION", len(combos), "combinations")
    report: dict[str, Any] = {}
    for c, combo in enumerate(combos):
        inside = ((bits >> c) & 1).astype(bool)
        out_mask = prod & ~inside
        in_mask = ~prod & inside
        rows_out: dict[str, Any] = {}
        all_met: bool | None = True
        remaining: dict[str, int] = {}
        for a, name in enumerate(names):
            mine = alloc == a
            out_docs = int(np.count_nonzero(out_mask & mine))
            in_docs = int(np.count_nonzero(in_mask & mine))
            out_vt = int(valid[out_mask & mine].sum())
            exact = a in token_set or in_docs == 0
            in_vt = int(valid[in_mask & mine].sum()) if exact else None
            projected = current[name] - out_vt + (in_vt or 0)
            need = quota[name]
            if projected >= need:
                status = "SUFFICIENT"
            elif exact:
                status = "DEFICIT"
                remaining[name] = need - projected
                all_met = False
            else:
                status = "UNKNOWN_LOWER_BOUND_BELOW_QUOTA"
                if all_met:
                    all_met = None
            rows_out[name] = {
                "quota": need,
                "current_valid_targets": current[name],
                "moved_out_of_train_documents": out_docs,
                "moved_out_of_train_valid_targets": out_vt,
                "moved_into_train_documents": in_docs,
                "moved_into_train_valid_targets": in_vt,
                "projected_valid_targets": projected,
                "projection": "exact" if exact else "lower_bound",
                "deficit": max(0, need - projected) if exact else None,
                "surplus": max(0, projected - need),
                "status": status,
            }
        report[combo] = {
            "allocations": rows_out,
            "all_frozen_quotas_met": all_met,
            "remaining_deficits": remaining,
            "remaining_deficit_total": sum(remaining.values()),
        }
        progress.update(c + 1)
    begin("REPORT VERIFY", 1)
    body = {
        "kind": "c05_policy_counterfactual_supply_v1",
        "content_free": True,
        "label": "counterfactual supply under current tokenizer",
        "caveat": (
            "a fresh C05 under a changed policy requires refitting C06 and recounting; these "
            "numbers use the tokenizer fitted on the current C05 membership"
        ),
        "plan_digest": identity,
        "completion_digest": completion,
        "tokenizer_fingerprint": expected,
        "token_allocations": sorted(names[a] for a in token_set),
        "documents_tokenized": int(picked.size),
        "corpus_files_read": len(read),
        "corpus_bytes_read": scanned_bytes,
        "combinations": report,
        "resources": {
            "elapsed_seconds": round(time.monotonic() - started, 1),
            "peak_rss_bytes": int(telemetry()["peak_rss"]),
        },
    }
    verify_content_free({k: v for k, v in body.items() if k != "tokenizer_fingerprint"})
    progress.update(1)
    return body


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--state", type=Path, required=True, help="stage-1 --state-out file")
    result.add_argument("--plan", type=Path, required=True, help="the C05 plan (pNNNN.json)")
    result.add_argument("--counts", type=Path, required=True, help="signed counts.json")
    result.add_argument("--deficit-report", type=Path, required=True, help="selection deficit")
    result.add_argument("--tokenizer", type=Path, required=True, help="current tokenizer dir")
    result.add_argument("--expect-fingerprint", help="refuse unless counts name this tokenizer")
    result.add_argument(
        "--token-allocation",
        action="append",
        default=[],
        metavar="COMPONENT/VIEW/UPSTREAM|all",
        help="allocations whose entering documents are tokenized (default: DEFICIT ones)",
    )
    result.add_argument("--workers", type=int, choices=WORKER_CHOICES, default=8)
    result.add_argument("--progress-interval", type=float, default=5.0)
    result.add_argument("--progress-format", choices=["text", "jsonl"], default="text")
    result.add_argument("--no-progress", action="store_true")
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    progress: Progress
    if args.no_progress:
        progress = NullProgress()
    else:
        progress = RunProgress(
            interval=args.progress_interval, fmt=args.progress_format, label=LABEL
        )
        progress.attach(Telemetry())
    stage = ["INPUT VERIFY"]
    try:
        body = run(args, progress, stage)
    except KeyboardInterrupt:
        _refusal("KeyboardInterrupt", None, stage[0])
        return 130
    except (AuditError, C05Error) as exc:
        _refusal(type(exc).__name__, str(exc), stage[0])
        return 1
    except Exception as exc:  # noqa: BLE001 - content-free refusal: type and stage only
        _refusal(type(exc).__name__, None, stage[0])
        return 1
    text = json.dumps(body, sort_keys=True, indent=1) + "\n"
    progress.complete()
    sys.stdout.write(text)
    sys.stdout.flush()
    return 0


def _refusal(kind: str, reason: str | None, stage: str) -> None:
    record = {"refused": True, "error_type": kind, "stage": stage}
    if reason is not None:
        record["reason"] = reason
    print(json.dumps(record, sort_keys=True), file=sys.stderr, flush=True)
