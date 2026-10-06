"""Write-once operator decisions and C05 control plane over the existing runner.

No command acquires data. Trust configuration contains environment variable names,
never keys. Decisions have no default approval and bind the exact corpus lineage.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from filelock import FileLock, Timeout
from pydantic import Field

from xlm.data.acquisition.source_run import write_once
from xlm.data.evidence_v2 import canonical
from xlm.data.exclusion.artifacts import (
    ExecutionPlan,
    Sha,
    authorize,
    load_envelope,
    make_plan,
    signed,
    verify_benchmark,
    verify_signed,
)
from xlm.data.exclusion.capacity import probe_geometry
from xlm.data.exclusion.identity import implementation_identity
from xlm.data.exclusion.inputs import read_metadata, verify_input_manifest
from xlm.data.exclusion.policy import (
    C05Error,
    FrozenModel,
    ProductionPolicy,
    Resources,
    production_policy,
)
from xlm.data.exclusion.preparation import benchmark_requirements
from xlm.data.exclusion.runner import resume_check, run, verify_completion

if TYPE_CHECKING:
    from xlm.data.exclusion.progress import NullProgress, RunProgress


class OperatorDecision(FrozenModel):
    kind: Literal["c05_operator_decision_v1"] = "c05_operator_decision_v1"
    purpose: Literal["lineage-policy", "resources", "policy"]
    input_manifest_digest: Sha
    value: dict[str, Any]
    evidence_digest: Sha
    operator: str = Field(min_length=1)
    issuer: str = Field(min_length=1)


def trust_from_file(path: Path) -> dict[str, bytes]:
    config = read_metadata(path, digested=False)
    if not config or any(not isinstance(v, str) for v in config.values()):
        raise C05Error("trust config must map issuer IDs to key environment variables")
    return {issuer: key_from_env(variable) for issuer, variable in config.items()}


def key_from_env(variable: str) -> bytes:
    value = os.environ.get(variable)
    if value is None or len(value.encode()) < 32:
        raise C05Error("signing or verification key unavailable/too short")
    return value.encode()


def decision_value(purpose: str, value: dict[str, Any]) -> dict[str, Any]:
    if purpose == "lineage-policy":
        if set(value) != {"choice"} or value["choice"] not in {
            "KNOWN_GROUP_ONLY",
            "REQUIRE_VERIFIED_BOOK_LINEAGE",
        }:
            raise C05Error("an explicit Gutenberg lineage choice is required")
        return value
    if purpose == "resources":
        if set(value) != set(Resources.model_fields):
            raise C05Error("reviewed resources must explicitly specify every ceiling")
        return Resources.model_validate(value).model_dump(mode="json")
    policy = production_policy(value)
    policy.identity()
    return policy.model_dump(mode="json")


def verify_decision(
    path: Path, trust: dict[str, bytes], purpose: str, manifest: str
) -> OperatorDecision:
    decision = OperatorDecision.model_validate(verify_signed(load_envelope(path), trust))
    if decision.purpose != purpose or decision.input_manifest_digest != manifest:
        raise C05Error("stale operator decision")
    decision_value(purpose, decision.value)
    return decision


_PLAN_NAME = re.compile(r"p(\d{4,})\.json")


def next_plan_sequence(plan_root: Path) -> int:
    """Next allocation number from exact ``pNNNN.json`` execution-plan names only.

    Sibling artifacts such as ``p0001.authorization.json`` share the prefix and
    must not participate.
    """
    sequences = (
        int(match.group(1))
        for path in plan_root.iterdir()
        if (match := _PLAN_NAME.fullmatch(path.name)) is not None
    )
    return max(sequences, default=0) + 1


def create_plan(args: argparse.Namespace, trust: dict[str, bytes]) -> ExecutionPlan:
    from xlm.data.exclusion import cleaned

    manifest = read_metadata(args.manifest)
    admitted = None
    if cleaned.is_cleaned(manifest):
        # A cleaned corpus cannot be rebuilt from source seals: it is admitted only by a
        # re-derived admission record, and only into roots of its own generation.
        if args.admission is None:
            raise C05Error("a cleaned manifest needs --admission from admit-cleaned")
        record = cleaned.verify_admission(args.admission, args.manifest)
        if record["mode"] != args.mode:
            raise C05Error("admission mode differs from the plan mode")
        cleaned.check_fresh_generation(
            args.plan_root, args.scratch, args.output, manifest["digest"]
        )
        files, seals = cleaned.plan_inputs(manifest, record)
        admitted = (cleaned.binding(record), files, seals)
    elif args.admission is not None:
        raise C05Error("--admission applies to cleaned manifests only")
    elif args.mode == "protected":
        # Real source verification is metadata-only here; run hashes every input byte.
        verify_input_manifest(manifest, Path(manifest["data_root"]), args.source_scratch)
    decisions = {
        purpose: verify_decision(path, trust, purpose, manifest["digest"])
        for purpose, path in (
            ("lineage-policy", args.lineage_policy),
            ("resources", args.resources),
            ("policy", args.policy),
        )
    }
    policy = production_policy(decisions["policy"].value)
    chosen = decisions["lineage-policy"].value["choice"]
    expected = "known_groups_only" if chosen == "KNOWN_GROUP_ONLY" else "require_book_ids"
    if policy.gutenberg != expected:
        raise C05Error("frozen policy differs from operator lineage choice")
    envelope = load_envelope(args.benchmark_receipt)
    actual = implementation_identity()
    pins = benchmark_requirements(args.pins)["tasks"]
    receipt = verify_benchmark(envelope, trust, pins, policy, mode=args.mode)
    if (receipt.code_identity, receipt.dependency_sha256) != (
        actual["code_identity"],
        actual["dependency_sha256"],
    ):
        raise C05Error("preparation code/dependencies stale")
    args.plan_root.mkdir(parents=True, exist_ok=True)
    with FileLock(str(args.plan_root / "allocation.lock"), timeout=0):
        if admitted is not None:
            cleaned.check_fresh_generation(
                args.plan_root, args.scratch, args.output, manifest["digest"]
            )
        sequence = next_plan_sequence(args.plan_root)
        plan = make_plan(
            manifest,
            envelope,
            trust,
            pins,
            policy,
            Resources.model_validate(decisions["resources"].value),
            sequence=sequence,
            # A detached-volume receipt binds the index read in place on its volume.
            index=args.index,
            # Measured on the actual scratch volume; the run re-measures and refuses drift.
            storage=probe_geometry(args.scratch),
            scratch=args.scratch,
            output=args.output,
            code_commit=actual["code_commit"],
            code_identity=actual["code_identity"],
            dependency_sha256=actual["dependency_sha256"],
            mode=args.mode,
            admitted=admitted,
        )
        plan = plan.model_copy(
            update={
                "review_decisions": {
                    p: canonical.digest(d.model_dump(mode="json")) for p, d in decisions.items()
                }
            }
        )
        plan.identity()
        write_once(args.plan_root / f"p{sequence:04d}.json", plan.model_dump(mode="json"))
    return plan


def cleaned_pins() -> tuple[str, ...]:
    from xlm.data.exclusion.cleaned import PINS

    return PINS


def admit_cleaned_command(args: argparse.Namespace) -> int:
    """Read-only admission of a cleaned manifest; writes only the write-once record."""
    from xlm.data.exclusion.cleaned import Evidence, write_admission
    from xlm.data.quality.scan import QualityError

    try:
        record = write_admission(
            Evidence(
                manifest=args.manifest,
                original_manifest=args.original_manifest,
                cleaning_state=args.cleaning_state,
                audit_output=args.audit_output,
                audit_report=args.audit_report,
                **{name: getattr(args, name) for name in cleaned_pins()},
            ),
            args.output,
        )
    except (C05Error, QualityError) as exc:
        # Both raise fixed literal messages (no record values); name the failed check.
        print(json.dumps({"refused": True, "error_type": type(exc).__name__, "reason": str(exc)}))
        return 1
    print(
        json.dumps(
            {
                "admission_digest": record["digest"],
                "input_manifest_digest": record["input_manifest"]["digest"],
                "mode": record["mode"],
                "c05": record["c05"],
            },
            sort_keys=True,
        )
    )
    return 0


def write_proof(
    args: argparse.Namespace, plan: ExecutionPlan, identity: str, trust: dict[str, bytes]
) -> int:
    """Downstream proof specification of a verified completion and its own manifest.

    Refuses unless the completion verifies (signature, plan bindings, membership hash)
    and ``--manifest`` is the plan's exact input manifest; the written specification
    is then opened through the downstream gate before this command reports success.
    """
    from xlm.data.exclusion.transport import ProofSpec, open_gate, protected_guard

    if read_metadata(args.manifest).get("digest") != plan.input_manifest_digest:
        raise C05Error("proof manifest differs from the plan's input manifest")
    directory = Path(plan.output_root) / identity
    # Downstream consumers refuse while the protected volume is mounted: check first,
    # so a refusal never leaves a proof specification behind.
    protected_guard(
        plan, (args.output, args.plan, args.manifest, directory, args.trust, args.scratch)
    )
    envelope = verify_completion(directory, plan, trust)
    spec = ProofSpec(
        plan=args.plan.resolve().as_posix(),
        manifest=args.manifest.resolve().as_posix(),
        completion=directory.resolve().as_posix(),
        trust=args.trust.resolve().as_posix(),
        scratch=args.scratch.resolve().as_posix(),
        plan_digest=identity,
        completion_digest=str(envelope["digest"]),
        signer=args.signer,
        signer_key_env=args.signer_key_env,
    )
    write_once(args.output, spec.model_dump(mode="json", exclude_none=True))
    args.scratch.mkdir(parents=True, exist_ok=True)
    with open_gate(args.output, allow_authored=plan.mode == "authored"):
        pass
    print(
        json.dumps(
            {
                "proof": str(args.output),
                "plan_digest": identity,
                "completion_digest": envelope["digest"],
                "input_manifest_digest": plan.input_manifest_digest,
                "mode": plan.mode,
            },
            sort_keys=True,
        )
    )
    return 0


def _trust(parser: argparse.ArgumentParser, *, signing: bool = False) -> None:
    parser.add_argument("--trust", type=Path, required=True)
    if signing:
        parser.add_argument("--issuer", required=True)
        parser.add_argument("--key-env", required=True)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)
    for purpose in ("lineage-policy", "resources", "policy"):
        group = commands.add_parser(purpose).add_subparsers(dest="action", required=True)
        preview = group.add_parser("preview" if purpose != "policy" else "show-default")
        if purpose != "lineage-policy":
            preview.add_argument("--value", type=Path)
        record = group.add_parser("record" if purpose != "policy" else "freeze")
        record.add_argument("--value", type=Path, required=True)
        record.add_argument("--input-manifest-digest", required=True)
        record.add_argument("--evidence-digest", required=True)
        record.add_argument("--operator", required=True)
        record.add_argument("--output", type=Path, required=True)
        _trust(record, signing=True)
        show = group.add_parser("show")
        show.add_argument("--artifact", type=Path, required=True)
        _trust(show)
        # Reviewed VALUE of an earlier signed decision (e.g. bound to a historical
        # manifest), written as plain data for a FRESH record; never a decision itself.
        carry = group.add_parser("carry-forward")
        carry.add_argument("--artifact", type=Path, required=True)
        carry.add_argument("--output", type=Path, required=True)
        _trust(carry)
    admission = commands.add_parser("admit-cleaned")
    for name in (
        "manifest",
        "original-manifest",
        "cleaning-state",
        "audit-output",
        "audit-report",
        "output",
    ):
        admission.add_argument("--" + name, type=Path, required=True)
    for name in cleaned_pins():
        admission.add_argument("--" + name.replace("_", "-"), required=True)
    bench = commands.add_parser("benchmark-receipt")
    bench.add_argument("action", choices=["verify"])
    bench.add_argument("--receipt", type=Path, required=True)
    bench.add_argument("--policy", type=Path, required=True)
    bench.add_argument("--pins", type=Path, default=Path("manifests/eval_dataset_pins.yaml"))
    bench.add_argument("--mode", choices=["protected", "authored"], default="protected")
    _trust(bench)
    plan = commands.add_parser("plan")
    for name in (
        "manifest",
        "benchmark-receipt",
        "lineage-policy",
        "resources",
        "policy",
        "plan-root",
        "scratch",
        "output",
    ):
        plan.add_argument("--" + name, type=Path, required=True)
    plan.add_argument("--source-scratch", type=Path, default=Path("C:/XLM-scratch"))
    # Required for (and only for) a cleaned manifest: the admit-cleaned record.
    plan.add_argument("--admission", type=Path)
    # Required for detached_volume_v1 receipts: the index inside the protected root.
    plan.add_argument("--index", type=Path)
    plan.add_argument("--pins", type=Path, default=Path("manifests/eval_dataset_pins.yaml"))
    plan.add_argument("--mode", choices=["protected", "authored"], default="protected")
    _trust(plan)
    quota = commands.add_parser("quota-report")
    quota.add_argument("--c05-proof", type=Path, required=True)
    quota.add_argument("--quotas", type=Path, default=Path("recipes/mixtures/mix01_quotas_6b.yaml"))
    quota.add_argument("--ifm-split", type=Path, required=True)
    quota.add_argument("--shard", type=Path, action="append", default=[])
    quota.add_argument("--output", type=Path, required=True)
    bridge = commands.add_parser("final-receipt")
    bridge.add_argument("--plan", type=Path, required=True)
    bridge.add_argument("--benchmark-receipt", type=Path, required=True)
    bridge.add_argument("--pins", type=Path, default=Path("manifests/eval_dataset_pins.yaml"))
    bridge.add_argument("--output", type=Path, required=True)
    # Schema 3 (official claims): the signed freeze of exact selected membership.
    bridge.add_argument("--c05-proof", type=Path)
    bridge.add_argument("--freeze", type=Path)
    _trust(bridge, signing=True)
    # Final allocation chain; each consumes the explicit proof specification.
    # count-tokens is the parallel fast path; count-tokens-reference is the original
    # single-process SQLite path (the oracle). Both publish byte-identical artifacts.
    counting = commands.add_parser("count-tokens")
    counting_reference = commands.add_parser("count-tokens-reference")
    # select is the fast path (no SQLite); select-reference is the original SQLite
    # path (the oracle). Both publish byte-identical artifacts and deficit reports.
    selecting = commands.add_parser("select")
    selecting_reference = commands.add_parser("select-reference")
    tokenizing = commands.add_parser("tokenize-selection")
    freezing = commands.add_parser("freeze")
    binding = commands.add_parser("claim-binding")
    counters = (counting, counting_reference)
    selectors = (selecting, selecting_reference)
    for command in (*counters, *selectors, tokenizing, freezing, binding):
        command.add_argument("--c05-proof", type=Path, required=True)
    for command in (*counters, *selectors, tokenizing, freezing):
        command.add_argument("--tokenizer", type=Path, required=True)
    for command in (*counters, *selectors):
        command.add_argument("--scratch", type=Path, required=True)
    for command in (*counters, *selectors, freezing):
        command.add_argument("--output", type=Path, required=True)
        command.add_argument("--issuer", required=True)
        command.add_argument("--key-env", required=True)
    for command in (*counters, selecting):
        # Operational display only (stderr); never part of an artifact.
        command.add_argument("--progress-interval", type=float, default=5.0)
        command.add_argument("--progress-format", choices=["text", "jsonl"], default="text")
        command.add_argument("--no-progress", action="store_true")
    # Operational only: the worker count never changes any output byte.
    for command in (counting, selecting):
        command.add_argument("--workers", type=int, choices=[1, 2, 4, 8, 16], default=8)
    for command in selectors:
        command.add_argument("--counts", type=Path, required=True)
        command.add_argument(
            "--quotas", type=Path, default=Path("recipes/mixtures/mix01_quotas_6b.yaml")
        )
        command.add_argument("--ifm-split", type=Path, required=True)
        command.add_argument("--deficit-report", type=Path, required=True)
    for command in (tokenizing, freezing):
        command.add_argument("--selection", type=Path, required=True)
    tokenizing.add_argument("--output-root", type=Path, required=True)
    tokenizing.add_argument("--batch-size", type=int, default=1)
    freezing.add_argument("--shards", type=Path, required=True)
    freezing.add_argument("--block-size", type=int, default=8192)
    binding.add_argument("--plan", type=Path, required=True)
    binding.add_argument("--freeze", type=Path, required=True)
    binding.add_argument("--checkpoint-hash", required=True)
    binding.add_argument("--suite-fingerprint", required=True)
    binding.add_argument("--output", type=Path, required=True)
    # C06: the frozen-policy tokenizer fit over this C05 membership, and its checks.
    # fit-tokenizer is the fast path; fit-tokenizer-reference is the bb886bd path.
    fitting = commands.add_parser("fit-tokenizer")
    reference = commands.add_parser("fit-tokenizer-reference")
    checking = commands.add_parser("verify-tokenizer-fit")
    indexing = commands.add_parser("verify-kept-index")
    for command in (fitting, reference, checking, indexing):
        command.add_argument("--c05-proof", type=Path, required=True)
        command.add_argument("--fit-shares", type=Path, required=True)
        command.add_argument(
            "--quotas", type=Path, default=Path("recipes/mixtures/mix01_quotas_6b.yaml")
        )
        command.add_argument("--ifm-split", type=Path, required=True)
        # Optional operator pins of the C05 chain: a proof naming any other plan or
        # completion refuses before any membership or corpus read.
        command.add_argument("--expect-c05-plan-digest")
        command.add_argument("--expect-c05-completion-digest")
    for command in (fitting, reference):
        for name in ("scratch", "output", "deficit-report"):
            command.add_argument("--" + name, type=Path, required=True)
        # Prints the deterministic resource plan; reads metadata only, writes nothing.
        command.add_argument("--plan-only", action="store_true")
        command.add_argument("--resource-plan-digest")
        command.add_argument("--issuer")
        command.add_argument("--key-env")
    for command in (fitting, reference, checking, indexing):
        # Operational display only (stderr); never part of an artifact.
        command.add_argument("--progress-interval", type=float, default=1.0)
        command.add_argument("--progress-format", choices=["text", "jsonl"], default="text")
        command.add_argument("--no-progress", action="store_true")
    # Operational only: worker/thread counts never change any output.
    for command in (fitting, checking, indexing):
        command.add_argument("--workers", type=int, choices=[1, 2, 4, 8, 16], default=8)
    fitting.add_argument("--bpe-threads", type=int, choices=[1, 2, 4, 8, 16], default=16)
    # Total command deadline (starts at dispatch) and reviewed ceilings; all bound
    # into the --plan-only digest, so a reviewed plan cannot run with other values.
    fitting.add_argument("--deadline-seconds", type=float, default=1200.0)
    fitting.add_argument("--rss-ceiling-gib", type=float, default=24.0)
    fitting.add_argument("--free-reserve-gib", type=float, default=16.0)
    checking.add_argument("--fit", type=Path, required=True)
    checking.add_argument("--sources", action="store_true")
    indexing.add_argument("--index", type=Path, required=True)
    indexing.add_argument("--membership", action="store_true")
    indexing.add_argument("--sources", action="store_true")
    claim = commands.add_parser("claim-check")
    claim.add_argument("--receipt", type=Path, required=True)
    claim.add_argument("--binding", type=Path, required=True)
    _trust(claim)
    for name in (
        "authorize",
        "run",
        "resume",
        "status",
        "resume-check",
        "verify",
        "publish",
        "proof",
    ):
        command = commands.add_parser(name)
        command.add_argument("--plan", type=Path, required=True)
        _trust(command, signing=name in {"authorize", "run", "resume"})
        if name == "proof":
            # Write-once downstream proof specification of a VERIFIED completion.
            command.add_argument("--manifest", type=Path, required=True)
            command.add_argument("--scratch", type=Path, required=True)
            command.add_argument("--output", type=Path, required=True)
            command.add_argument("--signer")
            command.add_argument("--signer-key-env")
        if name == "authorize":
            command.add_argument("--plan-digest", required=True)
            command.add_argument("--output", type=Path, required=True)
        if name in {"run", "resume", "resume-check"}:
            command.add_argument("--authorization", type=Path, required=True)
            command.add_argument("--benchmark-receipt", type=Path, required=True)
            command.add_argument("--index", type=Path, required=True)
        if name in {"run", "resume"}:
            # Operational display only (stderr); never part of a plan, state or artifact.
            # The worker count is never a CLI choice: it is the plan's reviewed maximum.
            command.add_argument("--progress-interval", type=float, default=1.0)
            command.add_argument("--progress-format", choices=["text", "jsonl"], default="text")
            command.add_argument("--no-progress", action="store_true")
    return result


def allocation_command(args: argparse.Namespace) -> int:
    """Counts, selection, tokenization and freeze over one verified C05 proof."""
    from xlm.data.exclusion.transport import open_gate

    # Authored chains run the identical code; every artifact records the mode and
    # protected consumers (Mix-01 training, official claims) refuse authored ones.
    consumes = [
        getattr(args, name)
        for name in (
            "tokenizer",
            "scratch",
            "output",
            "counts",
            "selection",
            "output_root",
            "shards",
        )
        if getattr(args, name, None) is not None
    ]
    with open_gate(args.c05_proof, allow_authored=True, consumes=consumes) as gate:
        if gate is None:
            raise C05Error("C05 proof absent")
        if args.command == "claim-binding":
            from dataclasses import asdict

            from xlm.data.exclusion.bridge import claim_binding
            from xlm.data.exclusion.freeze import verify_freeze

            plan = ExecutionPlan.model_validate(read_metadata(args.plan, digested=False))
            expected = claim_binding(
                verify_freeze(args.freeze, gate),
                plan,
                checkpoint_hash=args.checkpoint_hash,
                suite_fingerprint=args.suite_fingerprint,
            )
            write_once(args.output, asdict(expected))
            print(json.dumps({"claim_binding": str(args.output), "mode": gate.mode}))
            return 0
        if args.command == "tokenize-selection":
            from xlm.data.exclusion.freeze import tokenize_selection

            shards = tokenize_selection(
                gate, args.selection, args.tokenizer, args.output_root, batch_size=args.batch_size
            )
            print(json.dumps({"shards": sorted(shards), "mode": gate.mode}))
            return 0
        key = key_from_env(args.key_env)
        if gate.trusted.get(args.issuer) != key:
            raise C05Error("allocation signer is not trusted")
        if args.command == "count-tokens-reference":
            from xlm.data.exclusion.selection import count_tokens

            result = count_tokens(
                gate,
                args.tokenizer,
                args.output,
                args.issuer,
                key,
                scratch=args.scratch,
                progress=_count_progress(args),
            )
        elif args.command == "select-reference":
            from xlm.data.exclusion.selection import SelectionDeficit, select

            try:
                result = select(
                    gate,
                    args.counts,
                    args.tokenizer,
                    args.quotas,
                    args.ifm_split,
                    args.output,
                    args.issuer,
                    key,
                    scratch=args.scratch,
                )
            except SelectionDeficit as deficit:
                write_once(args.deficit_report, deficit.report)
                print(json.dumps({"deficit": True, "report": str(args.deficit_report)}))
                return 2
        else:
            from xlm.data.exclusion.freeze import freeze

            result = freeze(
                gate,
                args.c05_proof,
                args.selection,
                args.shards,
                args.tokenizer,
                args.output,
                args.issuer,
                key,
                block_size=args.block_size,
            )
        print(json.dumps({"digest": result["digest"], "mode": result["payload"]["mode"]}))
        return 0


def _count_progress(args: argparse.Namespace) -> RunProgress | NullProgress:
    from xlm.data.exclusion.progress import NullProgress, RunProgress

    if args.no_progress:
        return NullProgress()
    return RunProgress(interval=args.progress_interval, fmt=args.progress_format, label="COUNT")


def count_command(args: argparse.Namespace) -> int:
    """Fast exact counts over a verified C05 proof (byte-identical to the reference)."""
    from xlm.data.exclusion.countfast import count_tokens_fast

    key = key_from_env(args.key_env)
    envelope = count_tokens_fast(
        args.c05_proof,
        args.tokenizer,
        args.output,
        args.issuer,
        key,
        scratch=args.scratch,
        workers=args.workers,
        progress=_count_progress(args),
    )
    print(json.dumps({"digest": envelope["digest"], "mode": envelope["payload"]["mode"]}))
    return 0


def _select_progress(args: argparse.Namespace) -> RunProgress | NullProgress:
    from xlm.data.exclusion.progress import NullProgress, RunProgress

    if args.no_progress:
        return NullProgress()
    return RunProgress(interval=args.progress_interval, fmt=args.progress_format, label="SELECT")


def select_command(args: argparse.Namespace) -> int:
    """Fast exact selection over a verified C05 proof (byte-identical to the reference)."""
    from xlm.data.exclusion.selectfast import select_fast
    from xlm.data.exclusion.selection import SelectionDeficit

    key = key_from_env(args.key_env)
    try:
        envelope = select_fast(
            args.c05_proof,
            args.counts,
            args.tokenizer,
            args.quotas,
            args.ifm_split,
            args.output,
            args.issuer,
            key,
            scratch=args.scratch,
            workers=args.workers,
            deficit_report=args.deficit_report,
            progress=_select_progress(args),
            consumes=[args.deficit_report],
        )
    except SelectionDeficit as deficit:
        write_once(args.deficit_report, deficit.report)
        print(json.dumps({"deficit": True, "report": str(args.deficit_report)}))
        return 2
    print(json.dumps({"digest": envelope["digest"], "mode": envelope["payload"]["mode"]}))
    return 0


def _fit_progress(args: argparse.Namespace) -> RunProgress | NullProgress:
    from xlm.data.exclusion.progress import NullProgress, RunProgress

    if args.no_progress:
        return NullProgress()
    return RunProgress(interval=args.progress_interval, fmt=args.progress_format, label="C06")


def expect_c05_chain(args: argparse.Namespace) -> None:
    """Refuse a proof that names another C05 plan or completion than the operator pinned.

    Only the proof's claimed digests are compared here. Every C06 path then verifies
    them against the plan file and the completion before using either, so a
    matching pin binds the command to exactly that chain.
    """
    from xlm.data.exclusion.transport import ProofSpec

    pins = {
        "plan_digest": args.expect_c05_plan_digest,
        "completion_digest": args.expect_c05_completion_digest,
    }
    if all(value is None for value in pins.values()):
        return
    spec = ProofSpec.model_validate(read_metadata(args.c05_proof, digested=False))
    for name, value in pins.items():
        if value is not None and getattr(spec, name) != value:
            raise C05Error("C05 proof is not the pinned chain: " + name)


def tokenizer_fit_command(args: argparse.Namespace) -> int:
    """C06 fit/verify; refuses before any corpus read while the protected volume is mounted."""
    from xlm.data.exclusion import fitfast
    from xlm.data.exclusion.tokenizer_fit import (
        FIT_MANIFEST,
        FitDeficit,
        fit_tokenizer,
        load_fit_policy,
        plan_from_proof,
        verify_fit,
    )
    from xlm.data.exclusion.transport import guard_proof, open_gate

    expect_c05_chain(args)
    consumes: list[Path | str] = [args.fit_shares, args.quotas, args.ifm_split]
    reporter = _fit_progress(args)
    if args.command == "verify-kept-index":
        consumes.append(args.index)
        view = fitfast.open_streamed(args.c05_proof, allow_authored=True, consumes=consumes)
        policy, _ = load_fit_policy(args.fit_shares)
        result = fitfast.verify_kept_index(
            view,
            args.index,
            policy,
            args.quotas,
            args.ifm_split,
            membership=args.membership or args.sources,
            sources=args.sources,
            workers=args.workers,
            progress=reporter,
        )
        print(json.dumps(result, sort_keys=True))
        return 0
    if args.command == "verify-tokenizer-fit":
        consumes.append(args.fit)
        guard_proof(args.c05_proof, consumes)
        policy, _ = load_fit_policy(args.fit_shares)
        payload = read_metadata(args.fit / FIT_MANIFEST, digested=False).get("payload", {})
        if payload.get("fit_path") == fitfast.FIT_PATH:
            view = fitfast.open_streamed(args.c05_proof, allow_authored=True, consumes=consumes)
            result = fitfast.verify_fit_fast(
                view,
                args.fit,
                policy,
                args.quotas,
                args.ifm_split,
                workers=args.workers,
                sources=args.sources,
                progress=reporter,
            )
        else:
            with open_gate(args.c05_proof, allow_authored=True, consumes=consumes) as gate:
                if gate is None:
                    raise C05Error("C05 proof absent")
                result = verify_fit(gate, args.fit, policy, args.quotas, args.ifm_split)
        print(json.dumps(result, sort_keys=True))
        return 0
    consumes += [args.scratch, args.output, args.deficit_report]
    if args.command == "fit-tokenizer":
        return _fast_fit_command(args, consumes, reporter)
    reporter.stage("PROOF VERIFY", None, "steps")
    guard_proof(args.c05_proof, consumes)  # Mounted protected volume: refuse first.
    policy, policy_sha = load_fit_policy(args.fit_shares)
    planned = plan_from_proof(args.c05_proof, policy, args.quotas, args.ifm_split)
    if args.plan_only:
        reporter.complete()
        args.scratch.mkdir(parents=True, exist_ok=True)
        print(
            json.dumps(
                {
                    "resource_plan": planned,
                    "resource_plan_digest": planned["digest"],
                    "scratch_free_bytes": shutil.disk_usage(args.scratch).free,
                },
                sort_keys=True,
            )
        )
        return 0
    if args.resource_plan_digest != planned["digest"]:
        raise C05Error("fit requires --resource-plan-digest of the reviewed --plan-only plan")
    if args.issuer is None or args.key_env is None:
        raise C05Error("fit requires --issuer and --key-env")
    if args.output.exists():
        raise C05Error("tokenizer-fit output is write-once")
    key = key_from_env(args.key_env)
    try:
        with open_gate(args.c05_proof, allow_authored=True, consumes=consumes) as gate:
            if gate is None:
                raise C05Error("C05 proof absent")
            if gate.trusted.get(args.issuer) != key:
                raise C05Error("tokenizer-fit signer is not trusted")
            envelope = fit_tokenizer(
                gate,
                policy,
                policy_sha,
                quotas=args.quotas,
                ifm_split=args.ifm_split,
                scratch=args.scratch,
                output=args.output,
                issuer=args.issuer,
                key=key,
                accepted_plan_digest=args.resource_plan_digest,
                progress=reporter,
            )
    except FitDeficit as deficit:
        write_once(args.deficit_report, deficit.report)
        print(json.dumps({"deficit": True, "report": str(args.deficit_report)}))
        return 2
    return _fit_result(args, envelope)


def _fit_result(args: argparse.Namespace, envelope: dict[str, Any]) -> int:
    body = envelope["payload"]
    print(
        json.dumps(
            {
                "fit_digest": envelope["digest"],
                "mode": body["mode"],
                "production": body["production"],
                "tokenizer": str(args.output / "tokenizer"),
                "tokenizer_fingerprint": body["tokenizer"]["fingerprint"],
            },
            sort_keys=True,
        )
    )
    return 0


def operational_envelope(args: argparse.Namespace) -> Any:
    """The reviewed operational settings from the CLI; validation failures refuse."""
    from pydantic import ValidationError

    from xlm.data.exclusion.fitfast import OperationalEnvelope

    try:
        return OperationalEnvelope(
            source_workers=args.workers,
            bpe_threads=args.bpe_threads,
            deadline_seconds=args.deadline_seconds,
            ram_ceiling_bytes=int(round(args.rss_ceiling_gib * 1024**3)),
            free_reserve_bytes=int(round(args.free_reserve_gib * 1024**3)),
        )
    except (ValidationError, ValueError, OverflowError) as exc:
        raise C05Error("operational envelope outside its reviewed bounds") from exc


def _fast_fit_command(
    args: argparse.Namespace, consumes: list[Path | str], reporter: RunProgress | NullProgress
) -> int:
    """Fast fit: ONE supervisor, deadline started at dispatch, owns the whole command."""
    from xlm.data.exclusion import fitfast
    from xlm.data.exclusion.supervisor import Deadline, Supervisor
    from xlm.data.exclusion.supervisor import existing_ancestor as _existing_ancestor
    from xlm.data.exclusion.tokenizer_fit import FitDeficit, load_fit_policy
    from xlm.data.exclusion.transport import guard_proof

    dispatched = getattr(args, "dispatched", None)
    started = dispatched if isinstance(dispatched, float) else time.monotonic()
    envelope = operational_envelope(args)
    locations = fitfast.Locations(args.scratch, args.output, args.deficit_report)
    if args.plan_only:
        reporter.stage("PROOF VERIFY", None, "steps")
        guard_proof(args.c05_proof, consumes)
        policy, _ = load_fit_policy(args.fit_shares)
        planned = fitfast.plan_from_proof_fast(
            args.c05_proof, policy, args.quotas, args.ifm_split, envelope, locations
        )
        reporter.complete()
        args.scratch.mkdir(parents=True, exist_ok=True)
        print(
            json.dumps(
                {
                    "resource_plan": planned,
                    "resource_plan_digest": planned["digest"],
                    "free_bytes": {
                        role: shutil.disk_usage(
                            _existing_ancestor(Path(path))
                            if role == "scratch"
                            else _existing_ancestor(Path(path).parent)
                        ).free
                        for role, path in planned["locations"].items()
                    },
                },
                sort_keys=True,
            )
        )
        return 0
    with Supervisor(
        Deadline(envelope.deadline_seconds, started),
        envelope.ram_ceiling_bytes,
        interval=envelope.monitor_interval_seconds,
        grace=envelope.shutdown_grace_seconds,
        projection=envelope.projection(),
    ) as supervisor:
        reporter.stage("PROOF VERIFY", None, "steps")
        guard_proof(args.c05_proof, consumes)  # Mounted protected volume: refuse first.
        supervisor.check()
        policy, policy_sha = load_fit_policy(args.fit_shares)
        planned = fitfast.plan_from_proof_fast(
            args.c05_proof, policy, args.quotas, args.ifm_split, envelope, locations
        )
        if args.resource_plan_digest != planned["digest"]:
            raise C05Error(
                "fit requires --resource-plan-digest of the reviewed --plan-only plan "
                "with identical operational settings"
            )
        if args.issuer is None or args.key_env is None:
            raise C05Error("fit requires --issuer and --key-env")
        if args.output.exists():
            raise C05Error("tokenizer-fit output is write-once")
        key = key_from_env(args.key_env)
        supervisor.check()
        view = fitfast.open_streamed(args.c05_proof, allow_authored=True, consumes=consumes)
        if view.trusted.get(args.issuer) != key:
            raise C05Error("tokenizer-fit signer is not trusted")
        try:
            result = fitfast.fit_tokenizer_fast(
                view,
                policy,
                policy_sha,
                quotas=args.quotas,
                ifm_split=args.ifm_split,
                scratch=args.scratch,
                output=args.output,
                issuer=args.issuer,
                key=key,
                accepted_plan_digest=args.resource_plan_digest,
                envelope=envelope,
                deficit_report_path=args.deficit_report,
                supervisor=supervisor,
                progress=reporter,
            )
        except FitDeficit as deficit:
            write_once(args.deficit_report, deficit.report)
            print(json.dumps({"deficit": True, "report": str(args.deficit_report)}))
            return 2
    return _fit_result(args, result)


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    # The C06 fast-fit deadline covers the whole command from here (dispatch).
    args.dispatched = time.monotonic()
    try:
        if args.command == "admit-cleaned":
            return admit_cleaned_command(args)
        if args.command in {
            "fit-tokenizer",
            "fit-tokenizer-reference",
            "verify-tokenizer-fit",
            "verify-kept-index",
        }:
            try:
                return tokenizer_fit_command(args)
            except C05Error as exc:
                # C05Error messages are fixed literals (no record values); the fit
                # operator needs to know which frozen check refused.
                print(json.dumps({"refused": True, "error_type": "C05Error", "reason": str(exc)}))
                return 1
            except KeyboardInterrupt:
                # Staging and job scratch were removed; nothing was published.
                print(json.dumps({"refused": True, "error_type": "KeyboardInterrupt"}))
                return 130
        if args.command in {"count-tokens", "select"}:
            fast = count_command if args.command == "count-tokens" else select_command
            try:
                return fast(args)
            except C05Error as exc:
                # Fixed literal reasons (no record values, ids, paths or digests).
                print(json.dumps({"refused": True, "error_type": "C05Error", "reason": str(exc)}))
                return 1
            except KeyboardInterrupt:
                # Workers were reaped and owned files removed; nothing was published.
                print(json.dumps({"refused": True, "error_type": "KeyboardInterrupt"}))
                return 130
            except Exception as exc:  # noqa: BLE001 - redacted, content-free refusal
                # Type, fixed stage literal and numeric errno only: never a path or value.
                errno = getattr(exc, "errno", None)
                print(
                    json.dumps(
                        {
                            "refused": True,
                            "error_type": type(exc).__name__,
                            "stage": getattr(
                                exc, "count_stage", getattr(exc, "select_stage", None)
                            ),
                            "errno": errno if type(errno) is int else None,
                        }
                    )
                )
                return 1
        if args.command in {
            "count-tokens-reference",
            "select-reference",
            "tokenize-selection",
            "freeze",
            "claim-binding",
        }:
            return allocation_command(args)
        if args.command == "claim-check":
            from xlm.data.exclusion.receipt import (
                BenchmarkClaimBinding,
                FinalExclusionReceipt,
                verify_benchmark_claim,
            )

            verify_benchmark_claim(
                FinalExclusionReceipt.load(args.receipt),
                BenchmarkClaimBinding(**read_metadata(args.binding, digested=False)),
                trust_from_file(args.trust),
            )
            print(json.dumps({"official_claim_binding": "verified"}))
            return 0
        if args.command == "quota-report":
            from xlm.data.exclusion.quotas import quota_report, view_requirements
            from xlm.data.exclusion.transport import open_gate

            if len(args.shard) > 4096:
                raise C05Error("quota token shard count ceiling")
            with open_gate(args.c05_proof) as gate:
                if gate is None:
                    raise C05Error("quota C05 proof absent")
                requirements = view_requirements(gate, args.quotas, args.ifm_split)
                report = quota_report(gate, requirements, args.shard)
                write_once(args.output, report)
            print(
                json.dumps({"all_sufficient": report["all_sufficient"], "report": str(args.output)})
            )
            return 0 if report["all_sufficient"] else 2
        if args.command in {"lineage-policy", "resources", "policy"}:
            if args.action in {"preview", "show-default"}:
                if args.command == "lineage-policy":
                    value: Any = {
                        "KNOWN_GROUP_ONLY": "Propagate exact/near duplicates and known lineage; "
                        "unknown book segments may survive.",
                        "REQUIRE_VERIFIED_BOOK_LINEAGE": "Refuse Gutenberg rows without "
                        "verified book identity.",
                        "selected": None,
                    }
                else:
                    value = (
                        read_metadata(args.value, digested=False)
                        if args.value
                        else (
                            Resources() if args.command == "resources" else ProductionPolicy()
                        ).model_dump(mode="json")
                    )
                print(json.dumps({"proposal_only": True, "value": value}, sort_keys=True))
                return 0
            trust = trust_from_file(args.trust)
            if args.action == "show":
                decision = OperatorDecision.model_validate(
                    verify_signed(load_envelope(args.artifact), trust)
                )
                if decision.purpose != args.command:
                    raise C05Error("decision kind mismatch")
                print(decision.model_dump_json())
                return 0
            if args.action == "carry-forward":
                # The signature is verified only to prove WHICH reviewed value is carried.
                # The output is plain data: the operator must record a fresh decision.
                decision = OperatorDecision.model_validate(
                    verify_signed(load_envelope(args.artifact), trust)
                )
                if decision.purpose != args.command:
                    raise C05Error("decision kind mismatch")
                value = decision_value(args.command, decision.value)
                write_once(args.output, value)
                print(
                    json.dumps(
                        {
                            "value": str(args.output),
                            "carried_from_decision_digest": load_envelope(args.artifact)["digest"],
                            "carried_from_input_manifest_digest": decision.input_manifest_digest,
                            "signed_decision": False,
                        },
                        sort_keys=True,
                    )
                )
                return 0
            key = key_from_env(args.key_env)
            if trust.get(args.issuer) != key:
                raise C05Error("decision signer is not trusted")
            decision = OperatorDecision(
                purpose=args.command,
                input_manifest_digest=args.input_manifest_digest,
                value=decision_value(args.command, read_metadata(args.value, digested=False)),
                evidence_digest=args.evidence_digest,
                operator=args.operator,
                issuer=args.issuer,
            )
            artifact = signed(decision.model_dump(mode="json"), args.issuer, key)
            write_once(args.output, artifact)
            print(json.dumps({"decision_digest": artifact["digest"]}))
            return 0
        trust = trust_from_file(args.trust)
        if args.command == "benchmark-receipt":
            policy = production_policy(read_metadata(args.policy, digested=False))
            receipt = verify_benchmark(
                load_envelope(args.receipt),
                trust,
                benchmark_requirements(args.pins)["tasks"],
                policy,
                mode=args.mode,
            )
            print(
                json.dumps(
                    {"verified": True, "mode": receipt.isolation.mode, "items": receipt.items}
                )
            )
            return 0
        if args.command == "plan":
            plan = create_plan(args, trust)
            print(
                json.dumps(
                    {"sequence": plan.sequence, "plan_digest": plan.identity(), "mode": plan.mode}
                )
            )
            return 0
        plan = ExecutionPlan.model_validate(read_metadata(args.plan, digested=False))
        identity = plan.identity()
        if args.command == "final-receipt":
            from xlm.data.exclusion.bridge import final_receipt
            from xlm.data.exclusion.transport import open_gate

            if (args.freeze is None) != (args.c05_proof is None):
                raise C05Error("schema-3 receipts need both the C05 proof and the freeze")
            with open_gate(args.c05_proof, allow_authored=True) as gate:
                final_claim = final_receipt(
                    Path(plan.output_root) / identity,
                    plan,
                    load_envelope(args.benchmark_receipt),
                    benchmark_requirements(args.pins)["tasks"],
                    trust,
                    args.issuer,
                    key_from_env(args.key_env),
                    freeze=args.freeze,
                    gate=gate,
                )
            write_once(args.output, final_claim.to_dict())
            print(json.dumps({"receipt_id": final_claim.receipt_id, "mode": final_claim.mode}))
            return 0
        if args.command == "proof":
            return write_proof(args, plan, identity, trust)
        if args.command == "authorize":
            if identity != args.plan_digest:
                raise C05Error("authorization digest differs from reviewed plan")
            key = key_from_env(args.key_env)
            if trust.get(args.issuer) != key:
                raise C05Error("authorization signer is not trusted")
            write_once(args.output, authorize(plan, args.issuer, key))
        elif args.command in {"run", "resume"}:
            from xlm.data.exclusion.progress import NullProgress, RunProgress

            reporter: RunProgress | NullProgress = (
                NullProgress()
                if args.no_progress
                else RunProgress(interval=args.progress_interval, fmt=args.progress_format)
            )
            actual = implementation_identity()
            key = key_from_env(args.key_env)
            if trust.get(args.issuer) != key:
                raise C05Error("completion signer is not trusted")
            run(
                plan,
                load_envelope(args.authorization),
                index=args.index,
                benchmark=load_envelope(args.benchmark_receipt),
                trusted=trust,
                issuer=args.issuer,
                key=key,
                current_code=actual["code_identity"],
                current_dependencies=actual["dependency_sha256"],
                progress=reporter,
            )
        elif args.command in {"verify", "publish"}:
            # Publication is atomic inside run; this verb verifies that committed export.
            verify_completion(Path(plan.output_root) / identity, plan, trust)
        elif args.command == "resume-check":
            result = resume_check(
                plan,
                load_envelope(args.authorization),
                index=args.index,
                benchmark=load_envelope(args.benchmark_receipt),
                trusted=trust,
            )
            print(json.dumps(result))
            return 0
        else:
            state_path = Path(plan.scratch_root) / identity / "state.json"
            if state_path.exists():
                state = verify_signed(load_envelope(state_path), trust)
                if state.get("plan") != identity:
                    raise C05Error("journal plan identity mismatch")
                print(
                    json.dumps(
                        {
                            "plan_digest": identity,
                            "stage": state["stage"],
                            "resume_validation": "journal_only; run rehashes source and facts",
                        }
                    )
                )
            else:
                print(json.dumps({"plan_digest": identity, "stage": "not_started"}))
            return 0
        print(
            json.dumps(
                {
                    "verified": True,
                    "command": args.command,
                    "plan_digest": identity,
                    "mode": plan.mode,
                }
            )
        )
        return 0
    except (ValueError, OSError, KeyError, TypeError, RuntimeError, sqlite3.Error, Timeout) as exc:
        # Do not expose protected input or validation values through error messages.
        print(json.dumps({"refused": True, "error_type": type(exc).__name__}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
