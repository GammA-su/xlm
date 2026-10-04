"""Fresh C05 over a Phase-C cleaned corpus: admission, fresh decisions, fresh roots, run.

Authored fixtures only (``c05_cleaned_support``): the real cleaning chain on authored
text, an authored benchmark and an authored trust root. No real corpus, protected
benchmark material, G:/X:, network or signature of a real operator decision.
"""

from __future__ import annotations

import codecs
import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from c05_cleaned_support import (
    CONTAMINATED,
    CONTAMINATED_FAMILY,
    EXACT,
    ISSUER,
    KEY,
    LINEAGE,
    NEAR,
    admit_args,
    evidence,
    fresh_roots,
    make_chain,
    plan_args,
    prepare_benchmark,
    record_decisions,
    signing,
    tree_digest,
    trust,
)
from xlm.data.evidence_v2 import canonical
from xlm.data.exclusion import cleaned
from xlm.data.exclusion.artifacts import ExecutionPlan, InputFile, signed
from xlm.data.exclusion.capacity import probe_geometry
from xlm.data.exclusion.control import (
    OperatorDecision,
    create_plan,
    main,
    parser,
    verify_decision,
)
from xlm.data.exclusion.identity import implementation_identity
from xlm.data.exclusion.inputs import read_metadata
from xlm.data.exclusion.policy import C05Error, ProductionPolicy, Resources
from xlm.data.exclusion.runner import run, verify_completion
from xlm.data.exclusion.transport import open_gate
from xlm.data.quality.production import VERIFICATION_FILE
from xlm.data.quality.scan import QualityError

OLD_DIGEST = "11724d92c011dd01e8e8c3ab944ac2adeef76aa8ff4abc921bf882eb0ac84152"
NEW_DIGEST = "eda4f99499c87b2a404ee544547d26dca2be05a9012d417914a95d36167e1389"
TRUSTED = {ISSUER: KEY.encode()}


@pytest.fixture(scope="module")
def base(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("cleaned-c05")


@pytest.fixture(scope="module")
def chain(base: Path) -> dict[str, Any]:
    return make_chain(base / "chain")


@pytest.fixture(scope="module")
def bench(base: Path) -> dict[str, Path]:
    return prepare_benchmark(base / "bench")


@pytest.fixture(scope="module")
def admission(base: Path, chain: dict[str, Any]) -> Path:
    path = base / "admission.json"
    assert main(admit_args(chain, path)) == 0
    return path


def _plan(path: Path) -> ExecutionPlan:
    return ExecutionPlan.model_validate(read_metadata(path, digested=False))


def _authorize(root: Path, plan_path: Path) -> Path:
    output = plan_path.with_name(plan_path.stem + ".authorization.json")
    digest = _plan(plan_path).identity()
    args = ["authorize", "--plan", str(plan_path), "--plan-digest", digest]
    assert main([*args, "--output", str(output), *signing(root)]) == 0
    return output


def _run_args(
    root: Path, verb: str, plan_path: Path, authorization: Path, bench: dict[str, Path]
) -> list[str]:
    signed_args = signing(root) if verb in {"run", "resume"} else ["--trust", str(trust(root))]
    progress = ["--no-progress"] if verb in {"run", "resume"} else []
    return [
        verb,
        "--plan",
        str(plan_path),
        "--authorization",
        str(authorization),
        "--benchmark-receipt",
        str(bench["receipt"]),
        "--index",
        str(bench["index"]),
        *signed_args,
        *progress,
    ]


def _membership(plan_path: Path) -> bytes:
    plan = _plan(plan_path)
    return (Path(plan.output_root) / plan.identity() / "membership.jsonl").read_bytes()


def _decisions(plan_path: Path) -> dict[str, dict[str, Any]]:
    plan = _plan(plan_path)
    raw = (Path(plan.scratch_root) / plan.identity() / "decisions.jsonl").read_bytes()
    rows = [json.loads(line) for line in raw.splitlines()]
    return {row["doc_id"]: row for row in rows}


def _new_plan(plan_root: Path, before: set[str]) -> Path:
    created = {p.name for p in plan_root.glob("p*.json") if "." not in p.stem} - before
    assert len(created) == 1
    return plan_root / created.pop()


def _plans(plan_root: Path) -> set[str]:
    return {p.name for p in plan_root.glob("p*.json")} if plan_root.is_dir() else set()


def plan_and_run(
    base: Path,
    chain: dict[str, Any],
    bench: dict[str, Path],
    admission: Path,
    *,
    workers: int,
    name: str,
) -> Path:
    record = cleaned.load_admission(admission)
    decisions = record_decisions(
        base, chain["manifest_digest"], record["digest"], workers=workers, name=name
    )
    roots = fresh_roots(base / "clean-v1")
    before = _plans(roots["plans"])
    args = plan_args(base, chain["manifest"], decisions, bench, roots, admission=admission)
    assert main(args) == 0
    plan_path = _new_plan(roots["plans"], before)
    authorization = _authorize(base, plan_path)
    assert main(_run_args(base, "run", plan_path, authorization, bench)) == 0
    return plan_path


@pytest.fixture(scope="module")
def historical(base: Path, chain: dict[str, Any], bench: dict[str, Path]) -> dict[str, Any]:
    """Pre-cleaning C05 (the p0002 analogue): two plans, p0002 run to a signed proof."""
    original = read_metadata(chain["original"])
    decisions = record_decisions(base, original["digest"], "9" * 64, name="historical-decisions")
    roots = fresh_roots(base / "historical")
    assert main(plan_args(base, chain["original"], decisions, bench, roots)) == 0
    other = record_decisions(base, original["digest"], "9" * 64, workers=2, name="historical-2")
    assert main(plan_args(base, chain["original"], other, bench, roots)) == 0
    p0002 = roots["plans"] / "p0002.json"
    authorization = _authorize(base, p0002)
    assert main(_run_args(base, "run", p0002, authorization, bench)) == 0
    plan = _plan(p0002)
    completion = Path(plan.output_root) / plan.identity()
    proof = base / "historical" / "p0002.proof.json"
    (base / "historical-lookup").mkdir()
    canonical.write_canonical_json(
        proof,
        {
            "plan": str(p0002),
            "manifest": str(chain["original"]),
            "completion": str(completion),
            "trust": str(trust(base)),
            "scratch": str(base / "historical-lookup"),
            "plan_digest": plan.identity(),
            "completion_digest": read_metadata(completion / "completion.json", digested=False)[
                "digest"
            ],
        },
    )
    return {
        "roots": roots,
        "decisions": decisions,
        "p0002": p0002,
        "proof": proof,
        "digest": tree_digest(*roots.values()),
    }


@pytest.fixture(scope="module")
def clean_run(
    base: Path,
    chain: dict[str, Any],
    bench: dict[str, Path],
    admission: Path,
    historical: dict[str, Any],
) -> Path:
    return plan_and_run(base, chain, bench, admission, workers=1, name="fresh-w1")


# -- admission ------------------------------------------------------------------------------


def test_production_kind_cleaned_manifest_admitted_through_admission_path(
    tmp_path: Path,
) -> None:
    chain = make_chain(tmp_path / "prod", production_kind=True)
    before = Path(chain["manifest"]).read_bytes()
    record = cleaned.admit(evidence(chain))
    assert record["kind"] == cleaned.ADMISSION_KIND
    assert record["status"] == "ADMITTED_FOR_FRESH_C05_PLANNING"
    assert record["c05"].startswith("NOT RUN")
    assert record["mode"] == "protected"
    assert record["input_manifest"]["kind"] == "xlm_cleaned_input_manifest"
    assert record["input_manifest"]["status"] == "CANDIDATE_REQUIRES_INDEPENDENT_AUDIT_AND_C05"
    assert record["original_manifest"]["kind"] == "c05_global_input_manifest"
    assert record["quality_audit"]["report_sources_rehashed"] is True
    assert record["production_cleaning"]["sources_compared_byte_for_byte"] is True
    assert Path(chain["manifest"]).read_bytes() == before  # never rewritten
    # The protected-kind admission cannot feed an authored plan (and vice versa).
    path = tmp_path / "admission.json"
    cleaned.write_admission(evidence(chain), path)
    dummy = {k: tmp_path / "absent.json" for k in ("lineage-policy", "resources", "policy")}
    args = plan_args(
        tmp_path,
        chain["manifest"],
        dummy,
        {"receipt": tmp_path / "absent.json"},
        fresh_roots(tmp_path / "roots"),
        admission=path,
    )
    with pytest.raises(C05Error, match="admission mode differs"):
        create_plan(parser().parse_args(args), TRUSTED)
    assert not (tmp_path / "roots" / "plans").exists()


def test_admission_cli_is_write_once_and_rederivable(
    chain: dict[str, Any], admission: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    record = cleaned.verify_admission(admission, chain["manifest"])
    assert record["input_manifest"]["digest"] == chain["manifest_digest"]
    assert record["input_manifest"]["file_sha256"] == chain["manifest_sha256"]
    assert main(admit_args(chain, admission)) == 0  # identical rerun is a no-op
    capsys.readouterr()
    assert main(admit_args(chain, tmp_path / "x.json", expect_manifest_digest="0" * 64)) == 1
    refusal = json.loads(capsys.readouterr().out)
    assert refusal["refused"] is True and "operator pin" in refusal["reason"]
    assert not (tmp_path / "x.json").exists()
    # Another manifest path cannot borrow this record.
    with pytest.raises(C05Error, match="another manifest path"):
        cleaned.verify_admission(admission, chain["original"])


def test_cleaned_manifest_semantic_digest_and_totals_bound_exactly(
    chain: dict[str, Any], admission: Path, clean_run: Path
) -> None:
    body = read_metadata(chain["manifest"])
    assert canonical.self_digest(body) == body["digest"] == chain["manifest_digest"]
    assert (
        hashlib.sha256(Path(chain["manifest"]).read_bytes()).hexdigest()
        == (chain["manifest_sha256"])
    )
    record = cleaned.load_admission(admission)
    verification = read_metadata(Path(chain["state"]) / VERIFICATION_FILE)
    totals = body["totals"]
    assert {k: record["input_manifest"][k] for k in totals} == totals == chain["totals"]
    assert (verification["documents"], verification["canonical_bytes"]) == (
        totals["documents"],
        totals["canonical_bytes"],
    )
    assert verification["file_bytes"] == totals["file_bytes"]
    plan = _plan(clean_run)
    assert plan.input_manifest_digest == chain["manifest_digest"]
    assert plan.data_root == body["data_root"]
    assert plan.input_admission is not None
    assert plan.input_admission.admission_digest == record["digest"]
    assert plan.input_admission.original_manifest_digest == record["original_manifest"]["digest"]
    assert plan.input_admission.cleaned_manifest_file_sha256 == chain["manifest_sha256"]
    for field in ("documents", "canonical_bytes", "file_bytes"):
        assert sum(getattr(f, field) for f in plan.files) == totals[field]
    assert [f.path for f in plan.files] == [f["path"] for f in body["files"]]
    completion = read_metadata(
        Path(plan.output_root) / plan.identity() / "completion.json", digested=False
    )["payload"]
    assert completion["documents"] == totals["documents"]
    assert completion["input_manifest_digest"] == chain["manifest_digest"]


def test_manifest_not_matching_verified_cleaning_refused(
    chain: dict[str, Any], tmp_path: Path
) -> None:
    body = read_metadata(chain["manifest"])
    body.pop("digest")
    body["c05"] = "authored tamper: anything other than the verified derivation"
    body["digest"] = canonical.self_digest(body)
    forged = tmp_path / "forged.json"
    canonical.write_canonical_json(forged, body)
    with pytest.raises(C05Error, match="re-derived from the verified cleaning"):
        cleaned.admit(evidence(chain, manifest=forged, expect_manifest_digest=body["digest"]))
    with pytest.raises(C05Error, match="operator pin"):
        cleaned.admit(evidence(chain, expect_manifest_digest="e" * 64))


def test_production_verification_mismatch_refused(chain: dict[str, Any], tmp_path: Path) -> None:
    for pin in (
        "expect_verification_digest",
        "expect_production_result_digest",
        "expect_production_receipt_digest",
    ):
        with pytest.raises(C05Error, match="operator pin"):
            cleaned.admit(evidence(chain, **{pin: "a" * 64}))
    # A self-consistent verification record whose totals disagree with the manifest.
    state = tmp_path / "state"
    shutil.copytree(chain["state"], state)
    record = read_metadata(state / VERIFICATION_FILE)
    record.pop("digest")
    record["documents"] -= 1
    record["digest"] = canonical.digest(record)
    (state / VERIFICATION_FILE).write_bytes(canonical.canonical_bytes(record))
    with pytest.raises(C05Error, match="verified production totals"):
        cleaned.admit(
            evidence(chain, cleaning_state=state, expect_verification_digest=record["digest"])
        )
    # No verification record at all: cleaning is not verified.
    (state / VERIFICATION_FILE).unlink()
    with pytest.raises(QualityError, match="no verification record"):
        cleaned.admit(evidence(chain, cleaning_state=state))


def test_audit_receipt_mismatch_refused(chain: dict[str, Any]) -> None:
    # The pre-cleaning audit audited the ORIGINAL manifest, not the cleaned one.
    with pytest.raises(C05Error, match="did not audit this manifest"):
        cleaned.admit(evidence(chain, audit_output=chain["audit_pre"]))
    with pytest.raises(C05Error, match="audit result digest differs"):
        cleaned.admit(evidence(chain, expect_audit_result_digest="b" * 64))


@pytest.mark.parametrize(
    ("change", "match"),
    [
        ({"sources_rehashed": False}, "did not re-hash the sources"),
        ({"verified": False}, "not verified"),
        ({"result_digest": "c" * 64}, "audit report result digest"),
        ({"artifacts": 1}, "artifact count"),
        ({"extra": True}, "audit report schema"),
    ],
)
def test_audit_report_must_verify_with_source_rehash(
    chain: dict[str, Any], tmp_path: Path, change: dict[str, Any], match: str
) -> None:
    report = {**json.loads(Path(chain["report"]).read_bytes()), **change}
    path = tmp_path / "report.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(C05Error, match=match):
        cleaned.admit(evidence(chain, audit_report=path))


def test_audit_report_saved_by_windows_powershell_is_accepted(
    chain: dict[str, Any], tmp_path: Path
) -> None:
    text = Path(chain["report"]).read_text(encoding="utf-8")
    path = tmp_path / "report-utf16.json"
    path.write_bytes(codecs.BOM_UTF16_LE + text.encode("utf-16-le"))
    record = cleaned.admit(evidence(chain, audit_report=path))
    assert record["quality_audit"]["report_sources_rehashed"] is True


def test_admission_refuses_once_its_evidence_changes(
    base: Path, chain: dict[str, Any], bench: dict[str, Path], tmp_path: Path
) -> None:
    report = tmp_path / "report.json"
    shutil.copyfile(chain["report"], report)
    path = tmp_path / "admission.json"
    cleaned.write_admission(evidence(chain, audit_report=report), path)
    # A valid but different report file: the recorded evidence no longer re-derives.
    report.write_text(report.read_text(encoding="utf-8").strip() + "\n\n", encoding="utf-8")
    with pytest.raises(C05Error, match="no longer re-derives"):
        cleaned.verify_admission(path, chain["manifest"])
    decisions = record_decisions(base, chain["manifest_digest"], "7" * 64, name="evidence-change")
    roots = fresh_roots(tmp_path / "roots")
    assert main(plan_args(base, chain["manifest"], decisions, bench, roots, admission=path)) == 1
    assert _plans(roots["plans"]) == set()


def test_cleaned_manifest_needs_admission_and_original_refuses_it(
    base: Path, chain: dict[str, Any], bench: dict[str, Path], admission: Path, tmp_path: Path
) -> None:
    decisions = record_decisions(base, chain["manifest_digest"], "6" * 64, name="no-admission")
    roots = fresh_roots(tmp_path / "roots")
    assert main(plan_args(base, chain["manifest"], decisions, bench, roots)) == 1
    original = read_metadata(chain["original"])
    old = record_decisions(base, original["digest"], "6" * 64, name="orig-with-admission")
    args = plan_args(base, chain["original"], old, bench, roots, admission=admission)
    assert main(args) == 1
    assert _plans(roots["plans"]) == set()


# -- operator decisions -----------------------------------------------------------------------


def test_old_manifest_decision_refused_and_fresh_decision_accepted(tmp_path: Path) -> None:
    def decision(manifest: str) -> Path:
        path = tmp_path / f"{manifest[:8]}.json"
        body = OperatorDecision(
            purpose="lineage-policy",
            input_manifest_digest=manifest,
            value={"choice": "KNOWN_GROUP_ONLY"},
            evidence_digest="8" * 64,
            operator="authored-operator",
            issuer=ISSUER,
        )
        canonical.write_canonical_json(
            path, signed(body.model_dump(mode="json"), ISSUER, KEY.encode())
        )
        return path

    old, fresh = decision(OLD_DIGEST), decision(NEW_DIGEST)
    with pytest.raises(C05Error, match="stale operator decision"):
        verify_decision(old, TRUSTED, "lineage-policy", NEW_DIGEST)
    accepted = verify_decision(fresh, TRUSTED, "lineage-policy", NEW_DIGEST)
    assert accepted.input_manifest_digest == NEW_DIGEST


def test_historical_decisions_refused_carry_forward_values_only(
    base: Path,
    chain: dict[str, Any],
    bench: dict[str, Path],
    admission: Path,
    historical: dict[str, Any],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    roots = fresh_roots(tmp_path / "roots")
    old = historical["decisions"]
    args = plan_args(base, chain["manifest"], old, bench, roots, admission=admission)
    assert main(args) == 1  # signed decisions bound to the original manifest are stale
    assert _plans(roots["plans"]) == set()
    record = cleaned.load_admission(admission)
    carried: dict[str, Path] = {}
    for purpose, action in (
        ("lineage-policy", "record"),
        ("resources", "record"),
        ("policy", "freeze"),
    ):
        value = tmp_path / f"{purpose}-carried.json"
        capsys.readouterr()
        assert (
            main(
                [
                    purpose,
                    "carry-forward",
                    "--artifact",
                    str(old[purpose]),
                    "--output",
                    str(value),
                    "--trust",
                    str(trust(base)),
                ]
            )
            == 0
        )
        out = json.loads(capsys.readouterr().out)
        assert out["signed_decision"] is False
        assert (
            out["carried_from_input_manifest_digest"] == read_metadata(chain["original"])["digest"]
        )
        body = read_metadata(value, digested=False)
        assert set(body) != {"payload", "digest", "signature"}  # plain value, not a decision
        assert body == read_metadata(old[purpose], digested=False)["payload"]["value"]
        carried[purpose] = tmp_path / f"{purpose}.json"
        assert (
            main(
                [
                    purpose,
                    action,
                    "--value",
                    str(value),
                    "--input-manifest-digest",
                    chain["manifest_digest"],
                    "--evidence-digest",
                    record["digest"],
                    "--operator",
                    "authored-operator",
                    "--output",
                    str(carried[purpose]),
                    *signing(base),
                ]
            )
            == 0
        )
    assert main(plan_args(base, chain["manifest"], carried, bench, roots, admission=admission)) == 0
    assert _plans(roots["plans"]) == {"p0001.json"}  # fresh generation starts at p0001


# -- protected preparation identity ------------------------------------------------------------


def test_stale_preparation_code_identity_refused_fresh_accepted(
    base: Path, chain: dict[str, Any], admission: Path, bench: dict[str, Path], tmp_path: Path
) -> None:
    stale = prepare_benchmark(tmp_path / "stale", code_identity="0" * 64)
    record = cleaned.load_admission(admission)
    decisions = record_decisions(base, chain["manifest_digest"], record["digest"], name="ident")
    roots = fresh_roots(tmp_path / "roots")
    args = plan_args(base, chain["manifest"], decisions, stale, roots, admission=admission)
    with pytest.raises(C05Error, match="preparation code/dependencies stale"):
        create_plan(parser().parse_args(args), TRUSTED)
    assert _plans(roots["plans"]) == set()
    receipt = read_metadata(bench["receipt"], digested=False)["payload"]
    assert receipt["code_identity"] == implementation_identity()["code_identity"]
    args = plan_args(base, chain["manifest"], decisions, bench, roots, admission=admission)
    plan = create_plan(parser().parse_args(args), TRUSTED)
    assert plan.code_identity == receipt["code_identity"]
    assert (
        plan.benchmark_receipt_digest == read_metadata(bench["receipt"], digested=False)["digest"]
    )


# -- historical C05 state ------------------------------------------------------------------


def test_historical_roots_refused_and_never_modified(
    base: Path,
    chain: dict[str, Any],
    bench: dict[str, Path],
    admission: Path,
    historical: dict[str, Any],
    clean_run: Path,
    tmp_path: Path,
) -> None:
    record = cleaned.load_admission(admission)
    decisions = record_decisions(base, chain["manifest_digest"], record["digest"], name="hist")
    old = historical["roots"]
    fresh = fresh_roots(tmp_path / "fresh")
    for role in ("plans", "scratch", "output"):
        roots = {**fresh, role: old[role]}
        args = plan_args(base, chain["manifest"], decisions, bench, roots, admission=admission)
        assert main(args) == 1, role
    assert tree_digest(*old.values()) == historical["digest"]
    assert _plans(old["plans"]) == {
        "p0001.json",
        "p0002.json",
        "p0002.authorization.json",
    }
    # The fresh generation numbers its own attempts from p0001.
    assert clean_run.name == "p0001.json" and clean_run.parent != old["plans"]


def test_historical_p0002_completion_and_proof_refused(
    base: Path,
    chain: dict[str, Any],
    historical: dict[str, Any],
    clean_run: Path,
    tmp_path: Path,
) -> None:
    proof = read_metadata(historical["proof"], digested=False)
    with open_gate(historical["proof"], allow_authored=True) as gate:
        assert gate is not None  # the historical proof stays valid for its OWN corpus
    substituted = tmp_path / "substituted.proof.json"
    canonical.write_canonical_json(substituted, {**proof, "manifest": str(chain["manifest"])})
    with pytest.raises(C05Error, match="manifest differs from C05"):
        with open_gate(substituted, allow_authored=True):
            pass
    clean = _plan(clean_run)
    with pytest.raises(C05Error, match="completion plan/mode mismatch"):
        verify_completion(Path(proof["completion"]), clean, TRUSTED)
    old = _plan(historical["p0002"])
    assert old.input_manifest_digest != clean.input_manifest_digest
    assert old.input_admission is None


def test_admission_is_not_a_c05_result(
    base: Path,
    chain: dict[str, Any],
    bench: dict[str, Path],
    admission: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    record = cleaned.load_admission(admission)
    decisions = record_decisions(base, chain["manifest_digest"], record["digest"], name="nores")
    roots = fresh_roots(tmp_path / "roots")
    assert (
        main(plan_args(base, chain["manifest"], decisions, bench, roots, admission=admission)) == 0
    )
    plan_path = roots["plans"] / "p0001.json"
    assert main(["verify", "--plan", str(plan_path), "--trust", str(trust(base))]) == 1
    authorization = _authorize(base, plan_path)
    args = _run_args(base, "resume-check", plan_path, authorization, bench)
    assert main(args) == 1  # read-only; refuses: no signed state to resume yet
    capsys.readouterr()
    assert main(["status", "--plan", str(plan_path), "--trust", str(trust(base))]) == 0
    assert json.loads(capsys.readouterr().out)["stage"] == "not_started"
    assert not (Path(_plan(plan_path).output_root) / _plan(plan_path).identity()).exists()


# -- the fresh run itself ---------------------------------------------------------------------


def test_fresh_run_dedup_contamination_lineage_splits_membership(
    base: Path, chain: dict[str, Any], clean_run: Path
) -> None:
    rows = _decisions(clean_run)
    assert len(rows) == chain["totals"]["documents"]
    # Exact duplicate across files: one survivor, one duplicate, one group.
    first, second = (rows[d] for d in EXACT)
    assert {first["decision"], second["decision"]} == {"kept", "duplicate"}
    assert first["duplicate_group"] == second["duplicate_group"]
    # Near duplicate (MinHash): the longer document survives.
    short, longer = (rows[d] for d in NEAR)
    assert (short["decision"], longer["decision"]) == ("duplicate", "kept")
    assert short["duplicate_group"] == longer["duplicate_group"]
    # Benchmark contamination and its known-lineage (URL) family are excluded.
    hit, family = rows[CONTAMINATED], rows[CONTAMINATED_FAMILY]
    assert (hit["decision"], family["decision"]) == ("excluded", "excluded")
    assert hit["lineage_group"] == family["lineage_group"]
    # A clean URL-linked family is kept together, in one split.
    k1, k2 = (rows[d] for d in LINEAGE)
    assert (k1["decision"], k2["decision"]) == ("kept", "kept")
    assert k1["lineage_group"] == k2["lineage_group"] and k1["split"] == k2["split"]
    assert {r["split"] for r in rows.values()} <= {"train", "diagnostic_val", "audit"}
    assert {r["split"] for r in rows.values()} >= {"train", "diagnostic_val"}
    # Membership is exactly the kept decisions, in id order.
    membership = [json.loads(line) for line in _membership(clean_run).splitlines()]
    assert membership == sorted(
        (r for r in rows.values() if r["decision"] == "kept"), key=lambda r: r["doc_id"].encode()
    )
    plan = _plan(clean_run)
    completion = read_metadata(
        Path(plan.output_root) / plan.identity() / "completion.json", digested=False
    )["payload"]
    assert completion["kept"] == len(membership)
    assert completion["excluded"] == 2 and completion["duplicates"] >= 2
    # Rows dropped by the cleaner never reach C05.
    original = read_metadata(chain["original"])
    assert completion["documents"] < sum(f["documents"] for f in original["files"])


def test_final_verification_and_proof_over_cleaned_manifest(
    base: Path, chain: dict[str, Any], clean_run: Path, tmp_path: Path
) -> None:
    assert main(["verify", "--plan", str(clean_run), "--trust", str(trust(base))]) == 0
    plan = _plan(clean_run)
    completion = Path(plan.output_root) / plan.identity()
    proof = tmp_path / "clean-v1-p0001.proof.json"

    def proof_args(manifest: Path, output: Path) -> list[str]:
        return [
            "proof",
            "--plan",
            str(clean_run),
            "--trust",
            str(trust(base)),
            "--manifest",
            str(manifest),
            "--scratch",
            str(tmp_path / "lookup"),
            "--output",
            str(output),
        ]

    # The cleaned plan's proof refuses the original (pre-cleaning) manifest; nothing written.
    assert main(proof_args(chain["original"], proof)) == 1
    assert not proof.exists()
    assert main(proof_args(chain["manifest"], proof)) == 0
    assert main(proof_args(chain["manifest"], proof)) == 0  # identical write-once rerun
    body = read_metadata(proof, digested=False)
    assert body["plan_digest"] == plan.identity()
    assert (
        body["completion_digest"]
        == (read_metadata(completion / "completion.json", digested=False)["digest"])
    )
    with open_gate(proof, allow_authored=True) as gate:
        assert gate is not None and gate.input_manifest["digest"] == chain["manifest_digest"]
    # A hand-edited proof pointing at the original manifest is refused downstream.
    canonical.write_canonical_json(
        proof.with_name("orig.json"), {**body, "manifest": str(chain["original"])}
    )
    with pytest.raises(C05Error, match="manifest differs from C05"):
        with open_gate(proof.with_name("orig.json"), allow_authored=True):
            pass
    # Tampered membership fails verification.
    membership = completion / "membership.jsonl"
    saved = membership.read_bytes()
    try:
        membership.write_bytes(saved.replace(b'"train"', b'"audit"', 1))
        assert main(["verify", "--plan", str(clean_run), "--trust", str(trust(base))]) == 1
    finally:
        membership.write_bytes(saved)


@pytest.mark.parametrize("workers", [2, 4, 8, 16])
def test_worker_counts_give_identical_membership(
    base: Path,
    chain: dict[str, Any],
    bench: dict[str, Path],
    admission: Path,
    clean_run: Path,
    workers: int,
) -> None:
    plan_path = plan_and_run(
        base, chain, bench, admission, workers=workers, name=f"fresh-w{workers}"
    )
    assert _plan(plan_path).resources.workers == workers
    assert _membership(plan_path) == _membership(clean_run)
    assert _decisions(plan_path) == _decisions(clean_run)


@pytest.mark.parametrize("event", ["file_committed", "grouped", "before_publication"])
def test_interruption_then_resume_gives_identical_result(
    base: Path,
    chain: dict[str, Any],
    bench: dict[str, Path],
    admission: Path,
    clean_run: Path,
    event: str,
) -> None:
    record = cleaned.load_admission(admission)
    decisions = record_decisions(
        base, chain["manifest_digest"], record["digest"], workers=2, name=f"resume-{event}"
    )
    # A distinct reviewed resource value per case gives each case its own plan digest.
    value = read_metadata(decisions["resources"], digested=False)["payload"]["value"]
    value["stage_seconds"] = 1800 + len(event)
    path = decisions["resources"].with_name("resources-variant-value.json")
    canonical.write_canonical_json(path, value)
    decisions["resources"] = decisions["resources"].with_name("resources-variant.json")
    assert (
        main(
            [
                "resources",
                "record",
                "--value",
                str(path),
                "--input-manifest-digest",
                chain["manifest_digest"],
                "--evidence-digest",
                record["digest"],
                "--operator",
                "authored-operator",
                "--output",
                str(decisions["resources"]),
                *signing(base),
            ]
        )
        == 0
    )
    roots = fresh_roots(base / "clean-v1")
    before = _plans(roots["plans"])
    args = plan_args(base, chain["manifest"], decisions, bench, roots, admission=admission)
    assert main(args) == 0
    plan_path = _new_plan(roots["plans"], before)
    authorization = _authorize(base, plan_path)
    plan = _plan(plan_path)
    identity = implementation_identity()

    def interrupt(name: str) -> None:
        if name == event:
            raise RuntimeError("authored interruption")

    with pytest.raises(RuntimeError, match="authored interruption"):
        run(
            plan,
            read_metadata(authorization, digested=False),
            index=bench["index"],
            benchmark=read_metadata(bench["receipt"], digested=False),
            trusted=TRUSTED,
            issuer=ISSUER,
            key=KEY.encode(),
            current_code=identity["code_identity"],
            current_dependencies=identity["dependency_sha256"],
            checkpoint=interrupt,
        )
    assert not (Path(plan.output_root) / plan.identity() / "completion.json").exists()
    assert main(_run_args(base, "resume-check", plan_path, authorization, bench)) == 0
    assert main(_run_args(base, "resume", plan_path, authorization, bench)) == 0
    assert main(["verify", "--plan", str(plan_path), "--trust", str(trust(base))]) == 0
    assert _membership(plan_path) == _membership(clean_run)


# -- reviewed production values against the cleaned totals --------------------------------


def test_reviewed_production_values_admit_the_cleaned_corpus_shape(tmp_path: Path) -> None:
    """workers 16, RAM 48 GiB, index 64 GiB, scratch 352 GiB, 24 h stage, 72 h overall."""
    gib = 1024**3
    resources = Resources(
        workers=16,
        ram_bytes=48 * gib,
        index_bytes=64 * gib,
        scratch_bytes=352 * gib,
        stage_seconds=86400,
        overall_seconds=259200,
    )
    files, documents, file_bytes, text_bytes = 2035, 15_087_207, 103_993_099_986, 81_365_827_139
    data = tmp_path / "data"
    entries = tuple(
        InputFile(
            path=f"canonical/f{n:05d}/documents.jsonl",
            source_key="authored",
            source_id="authored",
            source_revision="authored-revision",
            component="authored",
            view="authored",
            source_file="authored",
            documents_sha256=hashlib.sha256(str(n).encode()).hexdigest(),
            file_bytes=file_bytes // files + (n < file_bytes % files),
            canonical_bytes=text_bytes // files + (n < text_bytes % files),
            documents=documents // files + (n < documents % files),
        )
        for n in range(files)
    )
    assert sum(f.documents for f in entries) == documents
    assert sum(f.file_bytes for f in entries) == file_bytes
    plan = ExecutionPlan(
        sequence=1,
        mode="authored",
        input_manifest_digest=NEW_DIGEST,
        source_seals={"authored": "2" * 64},
        files=entries,
        benchmark_receipt_digest="3" * 64,
        index_sha256="4" * 64,
        policy=ProductionPolicy(),
        resources=resources,
        storage=probe_geometry(tmp_path / "scratch"),
        data_root=str(data),
        scratch_root=str(tmp_path / "scratch"),
        output_root=str(tmp_path / "output"),
        code_commit="5" * 40,
        code_identity="6" * 64,
        dependency_sha256="7" * 64,
    )
    plan.identity()  # storage worst case, record/file/byte ceilings: all admitted


def test_proof_refused_while_protected_guard_refuses_writes_nothing(
    base: Path,
    chain: dict[str, Any],
    clean_run: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from xlm.data.exclusion import transport

    def mounted(*_: Any) -> None:
        raise C05Error("protected benchmark volume is mounted; detach it")

    monkeypatch.setattr(transport, "protected_guard", mounted)
    proof = tmp_path / "proof.json"
    args = ["proof", "--plan", str(clean_run), "--trust", str(trust(base))]
    args += ["--manifest", str(chain["manifest"]), "--scratch", str(tmp_path / "lookup")]
    assert main([*args, "--output", str(proof)]) == 1
    assert not proof.exists()
