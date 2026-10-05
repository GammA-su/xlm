"""Post-C05 consumers over a CLEANED-manifest proof: verified original-manifest lineage.

A cleaned C05 manifest carries no ``sources``; quota/adapter/split metadata is
recovered from the ORIGINAL manifest named by the plan's admission, and only after
that admission re-derives and binds every ``plan.input_admission`` digest. File
membership and counts always stay the cleaned manifest's.

Authored fixtures only: the generated Mix-01-shaped corpus (``c05_synthetic_flow``)
with planted rows the cleaner drops, the real cleaning chain over it
(``c05_cleaned_support.clean_chain``), an authored benchmark and trust root, and a
C05 run in authored mode. No real corpus, proof, tokenizer, G:/X: or network.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from scripts.c05_authored_pilot import doc as flow_doc
from scripts.c05_synthetic_flow import VOCAB, prepare

from c05_cleaned_support import (
    ISSUER,
    KEY,
    KEY_ENV,
    admit_args,
    clean_chain,
    evidence,
    fresh_roots,
    plan_args,
    prepare_benchmark,
    record_decisions,
    signing,
    trust,
)
from cleaning_fixtures import DOCS
from xlm.config.composer import load_yaml_str
from xlm.data.evidence_v2 import canonical
from xlm.data.exclusion import cleaned
from xlm.data.exclusion.artifacts import ExecutionPlan, InputAdmission
from xlm.data.exclusion.control import main as control
from xlm.data.exclusion.fitfast import KEPT_INDEX_DIR, open_streamed
from xlm.data.exclusion.gates import MembershipGate
from xlm.data.exclusion.inputs import read_metadata
from xlm.data.exclusion.operator import main as operator
from xlm.data.exclusion.policy import C05Error
from xlm.data.exclusion.quotas import frozen_requirements, view_requirements
from xlm.data.exclusion.runner import file_sha
from xlm.data.exclusion.tokenizer_fit import TOKENIZER_DIR, load_fit_policy, plan_from_proof
from xlm.data.exclusion.transport import ProofSpec, open_gate

REPO = Path(__file__).resolve().parents[1]
FIT_SHARES = REPO / "recipes/tokenizer/mix01_fit_shares_v1.yaml"
# Planted rows the real cleaner drops (hard NUL rule): (file path suffix, count).
# One row in the largest generated file stays within the 2 % component drop guardrail.
DROPS = {"ultrax/UltraX-Ultra-FineWeb/all/documents.jsonl": 1}


def plant_drops(manifest_path: Path) -> None:
    """Append cleaner-dropped rows to generated files; re-seal and re-digest."""
    body = canonical.loads_bytes_strict(manifest_path.read_bytes())
    data = Path(body["data_root"])
    number = 900_000
    for entry in body["files"]:
        count = next((n for s, n in DROPS.items() if entry["path"].endswith(s)), 0)
        if not count:
            continue
        path = data / entry["path"]
        rows = [
            flow_doc(number + k, entry["source_key"], DOCS["ultrax_ultrafineweb"]["nul"])
            for k in range(count)
        ]
        number += count
        with path.open("ab") as stream:
            for row in rows:
                stream.write(canonical.canonical_bytes(row.to_dict()) + b"\n")
        entry.update(
            documents_sha256=file_sha(path),
            file_bytes=path.stat().st_size,
            canonical_bytes=entry["canonical_bytes"] + sum(r.utf8_byte_count for r in rows),
            documents=entry["documents"] + count,
        )
    for source in body["sources"]:
        key = source["source_key"]
        files = [f for f in body["files"] if f["source_key"] == key]
        source["seal_digest"] = canonical.digest([key, files])
    body.pop("digest")
    body["digest"] = canonical.self_digest(body)
    canonical.write_canonical_json(manifest_path, body)


def _plan(path: Path) -> ExecutionPlan:
    return ExecutionPlan.model_validate(read_metadata(path, digested=False))


def _proof(base: Path, plan_path: Path, manifest: Path, output: Path) -> Path:
    args = ["proof", "--plan", str(plan_path), "--trust", str(trust(base))]
    args += ["--manifest", str(manifest), "--scratch", str(base / "lookup")]
    args += ["--output", str(output), "--signer", ISSUER, "--signer-key-env", KEY_ENV]
    assert control(args) == 0
    return output


def _run_plan(base: Path, plan_path: Path, bench: dict[str, Path]) -> None:
    digest = _plan(plan_path).identity()
    authorization = plan_path.with_name(plan_path.stem + ".authorization.json")
    args = ["authorize", "--plan", str(plan_path), "--plan-digest", digest]
    assert control([*args, "--output", str(authorization), *signing(base)]) == 0
    run = ["run", "--plan", str(plan_path), "--authorization", str(authorization)]
    run += ["--benchmark-receipt", str(bench["receipt"]), "--index", str(bench["index"])]
    assert control([*run, *signing(base), "--no-progress"]) == 0
    assert control(["verify", "--plan", str(plan_path), "--trust", str(trust(base))]) == 0


@pytest.fixture(scope="module")
def mix(tmp_path_factory: pytest.TempPathFactory) -> Iterator[dict[str, Any]]:
    """Original Mix-01 manifest -> real cleaning -> admission in the plan root -> C05.

    Also runs the pre-cleaning (historical, p0002-analogue) C05 over the original.
    """
    environment = pytest.MonkeyPatch()
    environment.setenv(KEY_ENV, KEY)
    base = tmp_path_factory.mktemp("cdown")
    prepare(base / "orig")
    original = base / "orig" / "manifest.json"
    plant_drops(original)
    chain = clean_chain(base / "chain", original)
    bench = prepare_benchmark(base / "bench")
    roots = fresh_roots(base / "cv1")
    roots["plans"].mkdir(parents=True)
    admission = roots["plans"] / cleaned.ADMISSION_FILE  # as in the operator runbook
    assert control(admit_args(chain, admission)) == 0
    record = cleaned.load_admission(admission)
    decisions = record_decisions(base, chain["manifest_digest"], record["digest"])
    args = plan_args(base, chain["manifest"], decisions, bench, roots, admission=admission)
    assert control(args) == 0
    plan_path = roots["plans"] / "p0001.json"
    _run_plan(base, plan_path, bench)
    proof = _proof(base, plan_path, chain["manifest"], roots["plans"] / "cv1-p0001.proof.json")
    # Historical generation over the original manifest (no admission), its own roots.
    old_roots = fresh_roots(base / "hist")
    old_digest = read_metadata(original)["digest"]
    old_decisions = record_decisions(base, old_digest, "9" * 64, name="hist-decisions")
    assert control(plan_args(base, original, old_decisions, bench, old_roots)) == 0
    old_plan = old_roots["plans"] / "p0001.json"
    _run_plan(base, old_plan, bench)
    old_proof = _proof(base, old_plan, original, old_roots["plans"] / "p0002.proof.json")
    yield {
        "base": base,
        "chain": chain,
        "original": original,
        "quotas": base / "orig" / "quotas.yaml",
        "ifm": base / "orig" / "ifm-split.json",
        "admission": admission,
        "plan": plan_path,
        "proof": proof,
        "old_plan": old_plan,
        "old_proof": old_proof,
    }
    environment.undo()


@pytest.fixture(autouse=True)
def key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(KEY_ENV, KEY)


def write_policy(path: Path, mix: dict[str, Any]) -> Path:
    """Development fit policy for the generated corpus (frozen Mix-01 fit shares)."""
    body = load_yaml_str(FIT_SHARES.read_text(encoding="utf-8"))
    body.update(
        mode="development",
        policy_id="authored_fit_v1",
        target_sample_bytes=11 * 4096,
        max_document_bytes=100_000,
        seed=7,
    )
    body["tokenizer"]["target_vocab_size"] = VOCAB
    body["internal_splits"]["quotas_sha256"] = file_sha(mix["quotas"])
    body["internal_splits"]["quotas_path"] = "quotas.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(body, sort_keys=False), encoding="utf-8")
    return path


def lineage(mix: dict[str, Any], plan: ExecutionPlan | None = None, **paths: Path) -> Any:
    """``cleaned.requirements_manifest`` over the cleaned plan with substituted inputs."""
    plan_path = paths.get("plan_path", mix["plan"])
    manifest_path = paths.get("manifest_path", mix["chain"]["manifest"])
    return cleaned.requirements_manifest(
        plan or _plan(mix["plan"]), read_metadata(manifest_path), (plan_path, manifest_path)
    )


def plan_elsewhere(mix: dict[str, Any], directory: Path, admission: Path | None = None) -> Path:
    """The unchanged cleaned plan in another directory, optionally beside ``admission``."""
    directory.mkdir(parents=True, exist_ok=True)
    plan_path: Path = directory / mix["plan"].name
    shutil.copyfile(mix["plan"], plan_path)
    if admission is not None:
        shutil.copyfile(admission, directory / cleaned.ADMISSION_FILE)
    return plan_path


# -- accepted lineage -------------------------------------------------------------------


def test_cleaned_proof_requirements_equal_original_manifest_requirements(
    mix: dict[str, Any],
) -> None:
    original = read_metadata(mix["original"])
    historical = frozen_requirements(original, mix["quotas"], mix["ifm"])
    with open_gate(mix["proof"], allow_authored=True) as gate:
        assert gate is not None
        assert gate.input_manifest["digest"] == mix["chain"]["manifest_digest"]
        assert "sources" not in gate.input_manifest
        assert gate.requirements_manifest() == original
        requirements = view_requirements(gate, mix["quotas"], mix["ifm"])
    # Identical requirements, hence an identical requirements digest downstream.
    assert requirements == historical
    assert canonical.digest(requirements) == canonical.digest(historical)
    assert sum(requirements["allocations"].values()) == requirements["valid_target_quota"]
    view = open_streamed(mix["proof"], allow_authored=True, consumes=[])
    assert view.requirements_manifest() == original
    # Without the verified original, the cleaned manifest has no quota provenance.
    with pytest.raises(C05Error, match="needs its verified original"):
        frozen_requirements(read_metadata(mix["chain"]["manifest"]), mix["quotas"], mix["ifm"])


def test_original_manifest_proof_behavior_unchanged(mix: dict[str, Any]) -> None:
    with open_gate(mix["old_proof"], allow_authored=True) as gate:
        assert gate is not None and gate.plan.input_admission is None
        assert gate.requirements_manifest() == gate.input_manifest
        assert view_requirements(gate, mix["quotas"], mix["ifm"]) == frozen_requirements(
            gate.input_manifest, mix["quotas"], mix["ifm"]
        )
    # An original-manifest plan never looks for an admission (none exists beside it).
    assert not (mix["old_plan"].parent / cleaned.ADMISSION_FILE).exists()
    plan = _plan(mix["old_plan"])
    assert cleaned.requirements_manifest(plan, read_metadata(mix["original"]), None) == (
        read_metadata(mix["original"])
    )


def test_cleaned_files_and_counts_are_never_replaced_by_the_original(
    mix: dict[str, Any], tmp_path: Path
) -> None:
    manifest = read_metadata(mix["chain"]["manifest"])
    original = read_metadata(mix["original"])
    plan = _plan(mix["plan"])
    before = sum(f["documents"] for f in original["files"])
    after = sum(f["documents"] for f in manifest["files"])
    assert after == before - sum(DROPS.values())  # the planted rows were cleaned away
    assert [f.path for f in plan.files] == [f["path"] for f in manifest["files"]]
    assert [f.documents for f in plan.files] == [f["documents"] for f in manifest["files"]]
    assert manifest["data_root"] != original["data_root"]
    policy = load_fit_policy(write_policy(tmp_path / "p.yaml", mix))[0]
    planned = plan_from_proof(mix["proof"], policy, mix["quotas"], mix["ifm"])
    assert planned["input_manifest_digest"] == manifest["digest"]
    assert planned["inputs"]["documents"] == after
    assert planned["inputs"]["file_bytes"] == manifest["totals"]["file_bytes"]
    with open_gate(mix["proof"], allow_authored=True) as gate:
        assert gate is not None
        assert gate.input_manifest == manifest
        assert gate.completion["documents"] == after
        gate.requirements_manifest()
        assert gate.input_manifest == manifest  # lineage never substitutes membership
        assert gate.plan.files == plan.files


def test_plan_completion_and_proof_need_no_regeneration(mix: dict[str, Any]) -> None:
    """The persisted plan/proof schemas and digests are unchanged by this layer."""
    assert list(ExecutionPlan.model_fields) == [
        "kind",
        "sequence",
        "mode",
        "input_manifest_digest",
        "source_seals",
        "files",
        "benchmark_receipt_digest",
        "index_sha256",
        "policy",
        "resources",
        "storage",
        "data_root",
        "scratch_root",
        "output_root",
        "code_commit",
        "code_identity",
        "dependency_sha256",
        "output_contract",
        "review_decisions",
        "authorization_contract",
        "isolation",
        "input_admission",
    ]
    assert list(InputAdmission.model_fields) == [
        "kind",
        "admission_digest",
        "cleaned_manifest_file_sha256",
        "original_manifest_digest",
        "production_receipt_digest",
        "production_result_digest",
        "verification_digest",
        "audit_receipt_digest",
        "audit_result_digest",
    ]
    assert list(ProofSpec.model_fields) == [
        "plan",
        "manifest",
        "completion",
        "trust",
        "scratch",
        "plan_digest",
        "completion_digest",
        "signer",
        "signer_key_env",
    ]
    spec = ProofSpec.model_validate(read_metadata(mix["proof"], digested=False))
    raw = mix["plan"].read_bytes()
    with open_gate(mix["proof"], allow_authored=True) as gate:
        assert gate is not None
        gate.requirements_manifest()
        assert gate.plan_digest == spec.plan_digest == _plan(mix["plan"]).identity()
    assert mix["plan"].read_bytes() == raw  # consumed in place, never rewritten


# -- C06 and the allocation chain through the operator CLI ------------------------------


def test_cleaned_proof_c06_fit_verify_count_select_tokenize_freeze(
    mix: dict[str, Any], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    policy = write_policy(tmp_path / "policy.yaml", mix)
    common = ["--c05-proof", str(mix["proof"]), "--fit-shares", str(policy)]
    common += ["--quotas", str(mix["quotas"]), "--ifm-split", str(mix["ifm"])]
    sign = ["--issuer", ISSUER, "--key-env", KEY_ENV]

    def fit_args(command: str, out: Path) -> list[str]:
        args = [command, *common, "--scratch", str(out / "scratch"), "--output", str(out / "fit")]
        args += ["--deficit-report", str(out / "deficit.json"), "--no-progress"]
        if command == "fit-tokenizer":
            args += ["--workers", "1", "--bpe-threads", "1", "--free-reserve-gib", "0"]
        return args

    fits = {}
    for command in ("fit-tokenizer", "fit-tokenizer-reference"):
        out = tmp_path / command
        capsys.readouterr()
        assert operator([*fit_args(command, out), "--plan-only"]) == 0
        planned = json.loads(capsys.readouterr().out)
        assert planned["resource_plan"]["input_manifest_digest"] == mix["chain"]["manifest_digest"]
        digest = ["--resource-plan-digest", planned["resource_plan_digest"]]
        assert operator([*fit_args(command, out), *sign, *digest]) == 0, command
        fits[command] = out / "fit"
        capsys.readouterr()
        verify = ["verify-tokenizer-fit", *common, "--fit", str(out / "fit"), "--no-progress"]
        assert operator(verify) == 0, command
        assert json.loads(capsys.readouterr().out)["verified"] is True
    index = ["verify-kept-index", *common, "--index", str(fits["fit-tokenizer"] / KEPT_INDEX_DIR)]
    assert operator([*index, "--membership", "--workers", "1", "--no-progress"]) == 0
    capsys.readouterr()
    chain = ["--c05-proof", str(mix["proof"])]
    chain += ["--tokenizer", str(fits["fit-tokenizer"] / TOKENIZER_DIR)]
    scratch = ["--scratch", str(tmp_path / "s")]
    counts = tmp_path / "counts"
    assert operator(["count-tokens", *chain, *scratch, "--output", str(counts), *sign]) == 0
    select = ["select", *chain, *scratch, "--counts", str(counts)]
    select += ["--quotas", str(mix["quotas"]), "--ifm-split", str(mix["ifm"])]
    select += ["--deficit-report", str(tmp_path / "select-deficit.json")]
    assert operator([*select, "--output", str(tmp_path / "selection"), *sign]) == 0
    selection = read_metadata(tmp_path / "selection" / "selection.json", digested=False)
    requirements = frozen_requirements(read_metadata(mix["original"]), mix["quotas"], mix["ifm"])
    assert selection["payload"]["requirements_digest"] == canonical.digest(requirements)
    assert selection["payload"]["selected_valid_targets"] == requirements["valid_target_quota"]
    tokenize = ["tokenize-selection", *chain, "--selection", str(tmp_path / "selection")]
    assert operator([*tokenize, "--output-root", str(tmp_path / "shards")]) == 0
    freeze = ["freeze", *chain, "--selection", str(tmp_path / "selection")]
    freeze += ["--shards", str(tmp_path / "shards"), "--output", str(tmp_path / "freeze")]
    assert operator([*freeze, *sign]) == 0


def test_consumers_without_quota_lineage_never_read_the_admission(
    mix: dict[str, Any], tmp_path: Path
) -> None:
    """Gate-only consumers (counts, tokenization, training) are unchanged: lineage is lazy."""
    elsewhere = plan_elsewhere(mix, tmp_path / "no-admission")
    spec = read_metadata(mix["proof"], digested=False)
    proof = tmp_path / "moved.proof.json"
    canonical.write_canonical_json(proof, {**spec, "plan": str(elsewhere)})
    with open_gate(proof, allow_authored=True) as gate:
        assert gate is not None and gate.completion["kept"] > 0
        with pytest.raises(C05Error, match="admission record is missing"):
            gate.requirements_manifest()


# -- refusals -------------------------------------------------------------------------


def test_missing_admission_refused(mix: dict[str, Any], tmp_path: Path) -> None:
    elsewhere = plan_elsewhere(mix, tmp_path / "plans")
    with pytest.raises(C05Error, match="admission record is missing"):
        lineage(mix, plan_path=elsewhere)
    # A gate built without the proof's paths cannot locate any lineage.
    view = open_streamed(mix["proof"], allow_authored=True, consumes=[])
    with pytest.raises(C05Error, match="needs the proof's paths"):
        cleaned.requirements_manifest(view.plan, view.input_manifest, None)
    # The CLI refuses before writing anything.
    policy = write_policy(tmp_path / "policy.yaml", mix)
    spec = read_metadata(mix["proof"], digested=False)
    proof = tmp_path / "moved.proof.json"
    canonical.write_canonical_json(proof, {**spec, "plan": str(elsewhere)})
    args = ["fit-tokenizer-reference", "--c05-proof", str(proof), "--fit-shares", str(policy)]
    args += ["--quotas", str(mix["quotas"]), "--ifm-split", str(mix["ifm"])]
    args += ["--scratch", str(tmp_path / "s"), "--output", str(tmp_path / "fit")]
    args += ["--deficit-report", str(tmp_path / "d.json"), "--plan-only", "--no-progress"]
    assert operator(args) == 1
    assert not (tmp_path / "fit").exists() and not (tmp_path / "d.json").exists()


def test_changed_admission_refused(mix: dict[str, Any], tmp_path: Path) -> None:
    record = read_metadata(mix["admission"])
    record.pop("digest")
    record["original_manifest"]["status"] = "authored tamper"
    record["digest"] = canonical.self_digest(record)
    forged = tmp_path / "forged.json"
    canonical.write_canonical_json(forged, record)
    elsewhere = plan_elsewhere(mix, tmp_path / "plans", forged)
    with pytest.raises(C05Error, match="no longer re-derives"):
        lineage(mix, plan_path=elsewhere)
    # Same bytes but not a self-consistent record.
    raw = mix["admission"].read_bytes().replace(b"ADMITTED_FOR", b"ADMITTED_FOX")
    (elsewhere.parent / cleaned.ADMISSION_FILE).write_bytes(raw)
    with pytest.raises(C05Error, match="self-digest"):
        lineage(mix, plan_path=elsewhere)


def test_admission_digest_mismatch_refused(mix: dict[str, Any], tmp_path: Path) -> None:
    # A genuinely valid admission of the same cleaned manifest, but another record.
    report = tmp_path / "report.json"
    report.write_bytes(Path(mix["chain"]["report"]).read_bytes() + b"\n")
    other = tmp_path / "other-admission.json"
    cleaned.write_admission(evidence(mix["chain"], audit_report=report), other)
    assert (
        cleaned.verify_admission(other, mix["chain"]["manifest"])["digest"]
        != (read_metadata(mix["admission"])["digest"])
    )
    elsewhere = plan_elsewhere(mix, tmp_path / "plans", other)
    with pytest.raises(C05Error, match="admission digest differs from the plan"):
        lineage(mix, plan_path=elsewhere)
    # A plan binding another admission digest refuses this (valid) record too.
    plan = _plan(mix["plan"])
    assert plan.input_admission is not None
    rebound = plan.model_copy(
        update={
            "input_admission": plan.input_admission.model_copy(
                update={"admission_digest": "a" * 64}
            )
        }
    )
    with pytest.raises(C05Error, match="admission digest differs from the plan"):
        lineage(mix, rebound)


def test_original_manifest_digest_mismatch_refused(mix: dict[str, Any]) -> None:
    plan = _plan(mix["plan"])
    assert plan.input_admission is not None
    for field in ("original_manifest_digest", "audit_result_digest", "verification_digest"):
        bound = plan.input_admission.model_copy(update={field: "b" * 64})
        rebound = plan.model_copy(update={"input_admission": bound})
        match = "original manifest digest" if field == "original_manifest_digest" else "bindings"
        with pytest.raises(C05Error, match=match):
            lineage(mix, rebound)
    # Plan inputs that do not re-derive from the admitted lineage.
    seals = {**plan.source_seals, next(iter(plan.source_seals)): "c" * 64}
    with pytest.raises(C05Error, match="plan inputs do not re-derive"):
        lineage(mix, plan.model_copy(update={"source_seals": seals}))


def test_original_manifest_raw_bytes_changed_refused(mix: dict[str, Any]) -> None:
    path = mix["original"]
    saved = path.read_bytes()
    try:
        # Same semantic content and digest, different raw bytes.
        path.write_bytes(json.dumps(json.loads(saved), indent=1).encode())
        assert hashlib.sha256(path.read_bytes()).hexdigest() != hashlib.sha256(saved).hexdigest()
        assert read_metadata(path) == canonical.loads_bytes_strict(saved)
        with pytest.raises(C05Error, match="lineage raw SHA-256"):
            lineage(mix)
        with open_gate(mix["proof"], allow_authored=True) as gate:
            assert gate is not None
            with pytest.raises(C05Error, match="lineage raw SHA-256"):
                view_requirements(gate, mix["quotas"], mix["ifm"])
        path.unlink()
        with pytest.raises(C05Error, match="original manifest is missing"):
            lineage(mix)
    finally:
        path.write_bytes(saved)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == hashlib.sha256(saved).hexdigest()
    assert lineage(mix) == read_metadata(path)


def test_wrong_cleaned_manifest_refused(mix: dict[str, Any], tmp_path: Path) -> None:
    # The identical manifest at another path is not the admitted evidence.
    copy = tmp_path / "cleaned-input-manifest.json"
    shutil.copyfile(mix["chain"]["manifest"], copy)
    with pytest.raises(C05Error, match="another manifest path"):
        lineage(mix, manifest_path=copy)
    # Another cleaned manifest (different digest) is not the plan's input manifest.
    body = read_metadata(mix["chain"]["manifest"])
    body.pop("digest")
    body["c05"] = "authored tamper"
    body["digest"] = canonical.self_digest(body)
    canonical.write_canonical_json(copy, body)
    with pytest.raises(C05Error, match="differs from the plan's input manifest"):
        lineage(mix, manifest_path=copy)
    # Quota provenance from a manifest that is not this cleaned manifest's original.
    old = read_metadata(mix["original"])
    old.pop("digest")
    old["kind"] = "authored_c05_input_variant"
    old["digest"] = canonical.self_digest(old)
    cleaned_body = read_metadata(mix["chain"]["manifest"])
    with pytest.raises(C05Error, match="not this cleaned manifest's original"):
        frozen_requirements(cleaned_body, mix["quotas"], mix["ifm"], provenance=old)


def test_historical_proof_cannot_stand_in_for_the_cleaned_proof(
    mix: dict[str, Any], tmp_path: Path
) -> None:
    old_spec = read_metadata(mix["old_proof"], digested=False)
    new_spec = read_metadata(mix["proof"], digested=False)
    # Historical plan + cleaned manifest, and cleaned plan + original manifest.
    for name, spec in (
        ("old-plan", {**old_spec, "manifest": new_spec["manifest"]}),
        ("new-plan", {**new_spec, "manifest": old_spec["manifest"]}),
    ):
        proof = tmp_path / f"{name}.proof.json"
        canonical.write_canonical_json(proof, spec)
        with pytest.raises(C05Error, match="manifest differs from C05"):
            with open_gate(proof, allow_authored=True):
                pass
    # The historical plan has no admission: it can only describe its own manifest.
    old = _plan(mix["old_plan"])
    with pytest.raises(C05Error, match="differs from the plan's input manifest"):
        cleaned.requirements_manifest(
            old,
            read_metadata(mix["chain"]["manifest"]),
            (mix["old_plan"], mix["chain"]["manifest"]),
        )
    # The cleaned plan refuses the original manifest as its input.
    with pytest.raises(C05Error, match="differs from the plan's input manifest"):
        lineage(mix, manifest_path=mix["original"])
    with open_gate(mix["old_proof"], allow_authored=True) as gate:
        assert gate is not None
        assert gate.plan_digest != _plan(mix["plan"]).identity()
        assert gate.input_manifest["digest"] != mix["chain"]["manifest_digest"]
    # A gate never accepts a cleaned plan's lineage without its proof paths.
    with pytest.raises(C05Error, match="needs the proof's paths"):
        gate_without_paths(mix, tmp_path)


def gate_without_paths(mix: dict[str, Any], tmp_path: Path) -> None:
    spec = ProofSpec.model_validate(read_metadata(mix["proof"], digested=False))
    plan = _plan(Path(spec.plan))
    gate = MembershipGate(
        Path(spec.completion),
        plan,
        read_metadata(Path(spec.manifest)),
        {ISSUER: KEY.encode()},
        tmp_path / "lookup.sqlite",
        authored=True,
    )
    try:
        gate.requirements_manifest()
    finally:
        gate.close()
