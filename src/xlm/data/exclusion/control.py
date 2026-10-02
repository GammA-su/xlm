"""Write-once operator decisions and C05 control plane over the existing runner.

No command acquires data. Trust configuration contains environment variable names,
never keys. Decisions have no default approval and bind the exact corpus lineage.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Literal

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
from xlm.data.exclusion.policy import C05Error, FrozenModel, ProductionPolicy, Resources
from xlm.data.exclusion.preparation import benchmark_requirements
from xlm.data.exclusion.runner import resume_check, run, verify_completion


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
    policy = ProductionPolicy.model_validate(value)
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


def create_plan(args: argparse.Namespace, trust: dict[str, bytes]) -> ExecutionPlan:
    manifest = read_metadata(args.manifest)
    # Real source verification is metadata-only here; run hashes every input byte.
    if args.mode == "protected":
        verify_input_manifest(manifest, Path(manifest["data_root"]), args.source_scratch)
    decisions = {
        purpose: verify_decision(path, trust, purpose, manifest["digest"])
        for purpose, path in (
            ("lineage-policy", args.lineage_policy),
            ("resources", args.resources),
            ("policy", args.policy),
        )
    }
    policy = ProductionPolicy.model_validate(decisions["policy"].value)
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
        paths = sorted(args.plan_root.glob("p[0-9]*.json"))
        sequence = max((int(p.stem[1:]) for p in paths), default=0) + 1
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
    counting = commands.add_parser("count-tokens")
    selecting = commands.add_parser("select")
    tokenizing = commands.add_parser("tokenize-selection")
    freezing = commands.add_parser("freeze")
    binding = commands.add_parser("claim-binding")
    for command in (counting, selecting, tokenizing, freezing, binding):
        command.add_argument("--c05-proof", type=Path, required=True)
    for command in (counting, selecting, tokenizing, freezing):
        command.add_argument("--tokenizer", type=Path, required=True)
    for command in (counting, selecting):
        command.add_argument("--scratch", type=Path, required=True)
    for command in (counting, selecting, freezing):
        command.add_argument("--output", type=Path, required=True)
        command.add_argument("--issuer", required=True)
        command.add_argument("--key-env", required=True)
    selecting.add_argument("--counts", type=Path, required=True)
    selecting.add_argument(
        "--quotas", type=Path, default=Path("recipes/mixtures/mix01_quotas_6b.yaml")
    )
    selecting.add_argument("--ifm-split", type=Path, required=True)
    selecting.add_argument("--deficit-report", type=Path, required=True)
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
    claim = commands.add_parser("claim-check")
    claim.add_argument("--receipt", type=Path, required=True)
    claim.add_argument("--binding", type=Path, required=True)
    _trust(claim)
    for name in ("authorize", "run", "resume", "status", "resume-check", "verify", "publish"):
        command = commands.add_parser(name)
        command.add_argument("--plan", type=Path, required=True)
        _trust(command, signing=name in {"authorize", "run", "resume"})
        if name == "authorize":
            command.add_argument("--plan-digest", required=True)
            command.add_argument("--output", type=Path, required=True)
        if name in {"run", "resume", "resume-check"}:
            command.add_argument("--authorization", type=Path, required=True)
            command.add_argument("--benchmark-receipt", type=Path, required=True)
            command.add_argument("--index", type=Path, required=True)
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
        if args.command == "count-tokens":
            from xlm.data.exclusion.selection import count_tokens

            result = count_tokens(
                gate, args.tokenizer, args.output, args.issuer, key, scratch=args.scratch
            )
        elif args.command == "select":
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


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command in {
            "count-tokens",
            "select",
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
            from xlm.data.exclusion.quotas import frozen_requirements, quota_report
            from xlm.data.exclusion.transport import open_gate

            if len(args.shard) > 4096:
                raise C05Error("quota token shard count ceiling")
            with open_gate(args.c05_proof) as gate:
                if gate is None:
                    raise C05Error("quota C05 proof absent")
                requirements = frozen_requirements(gate.input_manifest, args.quotas, args.ifm_split)
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
            policy = ProductionPolicy.model_validate(read_metadata(args.policy, digested=False))
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
        if args.command == "authorize":
            if identity != args.plan_digest:
                raise C05Error("authorization digest differs from reviewed plan")
            key = key_from_env(args.key_env)
            if trust.get(args.issuer) != key:
                raise C05Error("authorization signer is not trusted")
            write_once(args.output, authorize(plan, args.issuer, key))
        elif args.command in {"run", "resume"}:
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
