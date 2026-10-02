"""detached_volume_v1 protected isolation with an authored volume fixture.

The "protected volume" is a temporary directory mapped to a fixture filesystem device
by an injected volume inspector; no real Windows volume, benchmark material, network
or production key is used. The protected-mode flow runs as the real current OS user
(the same principal as the agent, which is the point of this mechanism) with the
real implementation identity and synthetic trust keys.
"""

from __future__ import annotations

import getpass
import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

from test_c05_engine import document, small_resources
from test_c05_protected_preparation import authored_material
from xlm.data.evidence_v2 import canonical
from xlm.data.exclusion.artifacts import (
    BenchmarkReceipt,
    ExecutionPlan,
    authorize,
    make_plan,
    signed,
    verify_benchmark,
)
from xlm.data.exclusion.capacity import probe_geometry
from xlm.data.exclusion.identity import implementation_identity
from xlm.data.exclusion.isolation import (
    AnyIsolation,
    DetachedVolumeIsolation,
    Isolation,
    RootIdentity,
    VolumeIdentity,
    checkout_root,
    init_protected_root,
    inspect_protected_root,
)
from xlm.data.exclusion.policy import C05Error, MatcherPolicy, ProductionPolicy, Resources
from xlm.data.exclusion.protected import MaterialSpec, build
from xlm.data.exclusion.runner import file_sha, run

KEY = b"detached-volume-fixture-key-not-an-operator-key"
TRUST = {"fixture": KEY}
PROTECTED_DEVICE = VolumeIdentity(device="fixture-protected-volume")
ORDINARY_DEVICE = VolumeIdentity(device="fixture-ordinary-volume")
BENCHMARK_WORDS = (b"copper", b"clever owls", b"sailor", b"wooden shelf")


class Layout:
    """``xvol`` plays the detachable volume; ``gvol`` the ordinary data volume."""

    def __init__(self, tmp: Path) -> None:
        self.tmp = tmp
        self.xvol = tmp / "xvol"
        self.gvol = tmp / "gvol"
        self.root = self.xvol / "C05-Protected"
        self.scratch = self.xvol / "C05-Scratch"
        self.output = self.gvol / "c05-output"
        self.data = self.gvol / "data"
        for path in (self.scratch, self.output, self.data):
            path.mkdir(parents=True)
        init_protected_root(self.root, "c05-fixture-root")
        self.devices = {str(self.xvol.resolve()): PROTECTED_DEVICE}

    def inspector(self, path: Path) -> VolumeIdentity:
        resolved = path.resolve()
        for prefix, device in self.devices.items():
            if resolved.is_relative_to(Path(prefix)):
                return device
        return ORDINARY_DEVICE

    def isolation(self, mode: str, operator: str) -> DetachedVolumeIsolation:
        roots = (
            ("repository", checkout_root()),
            ("data_root", self.data),
            ("c05_scratch", self.scratch),
            ("c05_output", self.output),
        )
        # Validated like an operator-supplied spec, so literals are checked at runtime.
        return DetachedVolumeIsolation.model_validate(
            {
                "mechanism": "detached_volume_v1",
                "mode": mode,
                "operator_principal": operator,
                "agent_principal": operator,
                "protected_root": inspect_protected_root(self.root, self.inspector),
                "separated_roots": [
                    RootIdentity.model_validate(
                        {"role": r, "path": str(p.resolve()), "volume": self.inspector(p)}
                    )
                    for r, p in roots
                ],
                "attestation_sha256": "7" * 64,
            }
        )


def detached_spec(layout: Layout, mode: str, operator: str) -> tuple[MaterialSpec, Any]:
    spec, pins = authored_material(layout.root / "material")
    return spec.model_copy(update={"isolation": layout.isolation(mode, operator)}), pins


def prepare(layout: Layout, spec: MaterialSpec, identity: dict[str, str]) -> dict[str, Any]:
    return build(
        spec,
        layout.root / "material",
        layout.root / "prepared",
        policy=MatcherPolicy(),
        resources=Resources(free_bytes=0),
        issuer="fixture",
        key=KEY,
        receipt_export=layout.gvol / "benchmark-preparation.receipt.json",
        inspector=layout.inspector,
        code_commit=identity["code_commit"],
        code_identity=identity["code_identity"],
        dependency_sha256=identity["dependency_sha256"],
    )


@pytest.fixture(scope="module")
def actual() -> dict[str, str]:
    return implementation_identity()


def protected_plan(
    layout: Layout, envelope: dict[str, Any], pins: Any, identity: dict[str, str]
) -> ExecutionPlan:
    docs = [
        document("clean-a", "A riverside diary entry about planting tomatoes in spring."),
        document("hit", "Why do copper bridges expand during summer? A student wondered."),
    ]
    files = []
    for n, doc in enumerate(docs):
        path = layout.data / f"{n}.jsonl"
        path.write_bytes(canonical.canonical_bytes(doc.to_dict()) + b"\n")
        files.append(
            {
                "path": path.name,
                "source_key": "authored",
                "component": "authored",
                "view": "view",
                "source_file": doc.source_file,
                "documents_sha256": file_sha(path),
                "file_bytes": path.stat().st_size,
                "canonical_bytes": doc.utf8_byte_count,
                "documents": 1,
            }
        )
    manifest: dict[str, Any] = {
        "data_root": str(layout.data),
        "sources": [
            {
                "source_key": "authored",
                "seal_digest": "2" * 64,
                "source": {"source_id": "authored", "revision": "revision"},
            }
        ],
        "files": files,
    }
    manifest["digest"] = canonical.self_digest(manifest)
    # The exact sealed manifest a downstream proof must present again.
    canonical.write_canonical_json(layout.tmp / "manifest.json", manifest)
    return make_plan(
        manifest,
        envelope,
        TRUST,
        pins,
        ProductionPolicy(diagnostic_bytes=0, quick_bytes=0, audit_bytes=0),
        small_resources(),
        sequence=1,
        storage=probe_geometry(layout.scratch),
        scratch=layout.scratch,
        output=layout.output,
        index=layout.root / "prepared" / "index.jsonl",
        inspector=layout.inspector,
        mode="protected",
        code_commit=identity["code_commit"],
        code_identity=identity["code_identity"],
        dependency_sha256=identity["dependency_sha256"],
    )


def execute(
    layout: Layout,
    plan: ExecutionPlan,
    envelope: dict[str, Any],
    identity: dict[str, str],
    **kw: Any,
) -> dict[str, Any]:
    arguments: dict[str, Any] = {
        "index": layout.root / "prepared" / "index.jsonl",
        "benchmark": envelope,
        "trusted": TRUST,
        "issuer": "fixture",
        "key": KEY,
        "current_code": identity["code_identity"],
        "current_dependencies": identity["dependency_sha256"],
        "inspector": layout.inspector,
    }
    arguments.update(kw)
    return run(plan, authorize(plan, "fixture", KEY), **arguments)


@pytest.fixture
def flow(tmp_path: Path, actual: dict[str, str]) -> dict[str, Any]:
    layout = Layout(tmp_path)
    spec, pins = detached_spec(layout, "protected", getpass.getuser())
    envelope = prepare(layout, spec, actual)
    plan = protected_plan(layout, envelope, pins, actual)
    return {"layout": layout, "envelope": envelope, "pins": pins, "plan": plan}


# 1 / 11 --------------------------------------------------------------------------


def test_separate_principal_v1_still_refuses_one_principal(tmp_path: Path) -> None:
    spec, pins = authored_material(tmp_path / "material")
    user = getpass.getuser()
    same = spec.isolation.model_copy(
        update={"mode": "protected", "operator_principal": user, "denied_agent_principal": user}
    )
    with pytest.raises(C05Error, match="separate operator"):
        build(
            spec.model_copy(update={"isolation": same}),
            tmp_path / "material",
            tmp_path / "out",
            policy=MatcherPolicy(),
            resources=Resources(free_bytes=0),
            issuer="fixture",
            key=KEY,
            code_commit="4" * 40,
            code_identity="5" * 64,
            dependency_sha256="6" * 64,
        )
    # A signed historical-schema receipt naming one principal is not protected either.
    authored = build(
        spec,
        tmp_path / "material",
        tmp_path / "prepared",
        policy=MatcherPolicy(),
        resources=Resources(free_bytes=0),
        issuer="fixture",
        key=KEY,
        code_commit="4" * 40,
        code_identity="5" * 64,
        dependency_sha256="6" * 64,
    )
    body = {**authored["payload"], "isolation": same.model_dump(mode="json")}
    with pytest.raises(C05Error, match="same-principal isolation is not protected"):
        verify_benchmark(signed(body, "fixture", KEY), TRUST, pins, ProductionPolicy())


def test_historical_isolation_and_plan_digests_keep_their_contract(tmp_path: Path) -> None:
    from test_c05_engine import setup_run

    spec, _ = authored_material(tmp_path / "material")
    parsed: Isolation | DetachedVolumeIsolation = TypeAdapter(AnyIsolation).validate_python(
        spec.isolation.model_dump(mode="json")
    )
    assert isinstance(parsed, Isolation) and parsed.mechanism == "separate_principal_v1"
    assert "mechanism" not in spec.isolation.model_dump(mode="json")
    plan, _, _ = setup_run(tmp_path / "c", [document("a", "An authored row.")])
    assert plan.isolation is None
    historical = plan.model_dump(mode="json")
    historical.pop("isolation")
    assert plan.identity() == canonical.digest(historical)
    # A historical receipt cannot be reinterpreted as detached by adding fields.
    with pytest.raises(ValidationError):
        TypeAdapter(AnyIsolation).validate_python(
            {**spec.isolation.model_dump(mode="json"), "mechanism": "detached_volume_v1"}
        )


# 2 / 8 / 12 ----------------------------------------------------------------------


def test_same_principal_detached_volume_protected_flow(
    flow: dict[str, Any], actual: dict[str, str]
) -> None:
    from xlm.data.exclusion.bridge import final_receipt
    from xlm.data.exclusion.receipt import (
        BenchmarkClaimBinding,
        ReceiptValidationError,
        verify_benchmark_claim,
    )

    layout, envelope, plan = flow["layout"], flow["envelope"], flow["plan"]
    receipt = BenchmarkReceipt.model_validate(envelope["payload"])
    isolation = receipt.isolation
    assert isinstance(isolation, DetachedVolumeIsolation)
    assert isolation.operator_principal == isolation.agent_principal == getpass.getuser()
    assert isolation.isolation == "detached-volume operational isolation"
    assert isolation.agent_os_access_denial == "not_asserted"
    assert plan.mode == "protected" and plan.isolation is not None
    assert plan.isolation.protected_root == isolation.protected_root
    assert plan.isolation.index_relpath == "prepared/index.jsonl"
    result = execute(layout, plan, envelope, actual)["payload"]
    assert (result["mode"], result["kept"], result["excluded"]) == ("protected", 1, 1)

    # Only the content-free receipt copy and the kept-membership publication leave
    # the protected volume; detailed matches stay in scratch on the volume.
    outside = sorted(
        p.relative_to(layout.gvol).as_posix()
        for p in layout.gvol.rglob("*")
        if p.is_file() and not p.is_relative_to(layout.data)
    )
    assert outside == [
        "benchmark-preparation.receipt.json",
        f"c05-output/{plan.identity()}/completion.json",
        f"c05-output/{plan.identity()}/membership.jsonl",
    ]
    for name in outside:
        raw = (layout.gvol / name).read_bytes().lower()
        assert not any(word in raw for word in BENCHMARK_WORDS), name
    exported = canonical.loads_bytes_strict(
        (layout.gvol / "benchmark-preparation.receipt.json").read_bytes()
    )
    assert exported == envelope
    assert (layout.scratch / plan.identity() / "facts.sqlite").is_file()
    assert (layout.scratch / plan.identity() / "decisions.jsonl").is_file()

    # A protected detached-volume completion alone is not an official claim.
    claim = final_receipt(
        layout.output / plan.identity(), plan, envelope, flow["pins"], TRUST, "fixture", KEY
    )
    assert claim.schema_version == "2" and any("detached_volume_v1" in n for n in claim.notes)
    binding = BenchmarkClaimBinding(
        checkpoint_hash="c" * 64,
        suite_fingerprint="s" * 64,
        corpus_input_digest=plan.input_manifest_digest,
        output_membership_digest=claim.output_membership_digest,
        exclusion_policy_identity=plan.policy.identity(),
        exclusion_index_identity=plan.index_sha256,
        # Non-empty selection identities: the refusal is the schema-3 rule itself.
        selection_digest="1" * 64,
        freeze_digest="2" * 64,
        tokenizer_fingerprint="3" * 64,
        quota_sha256="4" * 64,
        source_seals_digest="5" * 64,
    )
    with pytest.raises(ReceiptValidationError, match="schema-3"):
        verify_benchmark_claim(claim, binding, TRUST)


def test_inspection_output_is_content_free(tmp_path: Path) -> None:
    from xlm.data.exclusion.protected import inspect

    layout = Layout(tmp_path)
    spec, _ = detached_spec(layout, "authored", getpass.getuser())
    raw = json.dumps(inspect(spec, layout.root / "material")).encode().lower()
    assert not any(word in raw for word in BENCHMARK_WORDS)


# 3 / 4 / 5 -----------------------------------------------------------------------


@pytest.mark.parametrize("field", ["data_root", "scratch_root", "output_root"])
def test_plan_roots_overlapping_the_protected_root_refuse(flow: dict[str, Any], field: str) -> None:
    plan = flow["plan"]
    inside = Path(plan.isolation.protected_root.path) / "nested"
    with pytest.raises(C05Error, match="overlap"):
        plan.model_copy(update={field: str(inside)}).identity()
    # Enclosing the protected root overlaps as well.
    with pytest.raises(C05Error, match="overlap"):
        plan.model_copy(update={field: str(flow["layout"].xvol)}).identity()


def test_declared_roots_overlapping_or_sharing_the_volume_refuse(tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    base = layout.isolation("authored", getpass.getuser()).model_dump(mode="json")

    def with_root(role: str, path: Path) -> dict[str, Any]:
        roots = [r for r in base["separated_roots"] if r["role"] != role]
        roots.append(
            {
                "role": role,
                "path": str(path.resolve()),
                "volume": layout.inspector(path).model_dump(),
            }
        )
        return {**base, "separated_roots": roots}

    for role, path, message in (
        ("data_root", layout.root / "data", "overlaps data_root"),
        ("c05_scratch", layout.root / "scratch", "overlaps c05_scratch"),
        ("c05_output", layout.root / "out", "overlaps c05_output"),
        ("data_root", layout.xvol / "data", "shares the protected"),
        ("c05_output", layout.xvol / "public", "shares the protected"),
        ("c05_scratch", layout.gvol / "scratch", "must stay on the protected volume"),
    ):
        with pytest.raises(ValidationError, match=message):
            DetachedVolumeIsolation.model_validate(with_root(role, path))


def test_plan_creation_refuses_actual_roots_on_wrong_volumes(
    flow: dict[str, Any], actual: dict[str, str], tmp_path: Path
) -> None:
    layout = flow["layout"]
    off_volume_scratch = layout.gvol / "scratch"
    with pytest.raises(C05Error, match="must stay on the protected volume"):
        make_plan(
            *_plan_args(flow),
            scratch=off_volume_scratch,
            output=layout.output,
            storage=probe_geometry(off_volume_scratch),
            index=layout.root / "prepared" / "index.jsonl",
            inspector=layout.inspector,
            mode="protected",
            sequence=2,
            code_commit=actual["code_commit"],
            code_identity=actual["code_identity"],
            dependency_sha256=actual["dependency_sha256"],
        )
    with pytest.raises(C05Error, match="must name the protected index"):
        make_plan(
            *_plan_args(flow),
            scratch=layout.scratch,
            output=layout.output,
            storage=probe_geometry(layout.scratch),
            inspector=layout.inspector,
            mode="protected",
            sequence=2,
            code_commit=actual["code_commit"],
            code_identity=actual["code_identity"],
            dependency_sha256=actual["dependency_sha256"],
        )


def _plan_args(flow: dict[str, Any]) -> tuple[Any, ...]:
    layout, plan = flow["layout"], flow["plan"]
    manifest = canonical.loads_bytes_strict((layout.tmp / "manifest.json").read_bytes())
    return (manifest, flow["envelope"], TRUST, flow["pins"], plan.policy, plan.resources)


# 6 / 7 ---------------------------------------------------------------------------


def test_wrong_volume_or_root_identity_refuses_before_work(
    flow: dict[str, Any], actual: dict[str, str]
) -> None:
    layout, plan, envelope = flow["layout"], flow["plan"], flow["envelope"]
    work = layout.scratch / plan.identity()
    layout.devices[str(layout.xvol.resolve())] = VolumeIdentity(device="some-other-volume")
    with pytest.raises(C05Error, match="volume identity differs"):
        execute(layout, plan, envelope, actual)
    layout.devices[str(layout.xvol.resolve())] = PROTECTED_DEVICE
    marker = layout.root / "C05-PROTECTED-ROOT.json"
    original = marker.read_bytes()
    marker.write_bytes(original.replace(b"c05-fixture-root", b"c05-another-root"))
    with pytest.raises(C05Error, match="root or volume identity differs"):
        execute(layout, plan, envelope, actual)
    marker.write_bytes(original)
    # Unmounted at run time: the protected C05 run cannot proceed.
    shutil.move(layout.root, layout.xvol / "detached")
    with pytest.raises(C05Error, match="not mounted"):
        execute(layout, plan, envelope, actual)
    assert not work.exists()


def test_changed_or_relocated_protected_index_refuses(
    flow: dict[str, Any], actual: dict[str, str]
) -> None:
    layout, plan, envelope = flow["layout"], flow["plan"], flow["envelope"]
    index = layout.root / "prepared" / "index.jsonl"
    # An identical copy outside the protected root is not the bound index.
    copy = layout.gvol / "index-copy.jsonl"
    shutil.copy2(index, copy)
    with pytest.raises(C05Error, match="plan-bound index"):
        execute(layout, plan, envelope, actual, index=copy)
    raw = index.read_bytes()
    index.write_bytes(raw.replace(b'"', b"'", 1)[: len(raw)])
    with pytest.raises(C05Error, match="benchmark index"):
        execute(layout, plan, envelope, actual)
    index.write_bytes(raw + bytes([10]))  # One extra byte: size identity changes.
    with pytest.raises(C05Error, match="benchmark index"):
        execute(layout, plan, envelope, actual)


def test_preparation_keeps_material_and_index_inside_the_root(tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    spec, _ = detached_spec(layout, "authored", getpass.getuser())
    common: dict[str, Any] = {
        "policy": MatcherPolicy(),
        "resources": Resources(free_bytes=0),
        "issuer": "fixture",
        "key": KEY,
        "code_commit": "4" * 40,
        "code_identity": "5" * 64,
        "dependency_sha256": "6" * 64,
        "inspector": layout.inspector,
    }
    with pytest.raises(C05Error, match="protected index must stay inside"):
        build(spec, layout.root / "material", layout.gvol / "prepared", **common)
    outside = layout.gvol / "material"
    shutil.copytree(layout.root / "material", outside)
    with pytest.raises(C05Error, match="benchmark material must stay inside"):
        build(spec, outside, layout.root / "prepared", **common)
    with pytest.raises(C05Error, match="receipt export"):
        build(
            spec,
            layout.root / "material",
            layout.root / "prepared",
            receipt_export=layout.root / "receipt.json",
            **common,
        )
    # Separated roots are re-measured at preparation time.
    layout.devices[str(layout.data.resolve())] = PROTECTED_DEVICE
    with pytest.raises(C05Error, match="shares the protected"):
        build(spec, layout.root / "material", layout.root / "prepared", **common)
    assert not (layout.root / "prepared").exists()


# 9 / 10 --------------------------------------------------------------------------


def proof_for(flow: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    plan, layout = flow["plan"], flow["layout"]
    plan_path = layout.gvol / "plan.json"
    canonical.write_canonical_json(plan_path, plan.model_dump(mode="json"))
    manifest_path = layout.tmp / "manifest.json"
    monkeypatch.setenv("XLM_DETACHED_FIXTURE_KEY", KEY.decode())
    trust = layout.gvol / "trust.json"
    canonical.write_canonical_json(trust, {"fixture": "XLM_DETACHED_FIXTURE_KEY"})
    completion = layout.output / plan.identity()
    proof: Path = layout.gvol / "proof.json"
    canonical.write_canonical_json(
        proof,
        {
            "plan": str(plan_path),
            "manifest": str(manifest_path),
            "completion": str(completion),
            "trust": str(trust),
            "scratch": str(tmp_path / "lookup"),
            "plan_digest": plan.identity(),
            "completion_digest": canonical.loads_bytes_strict(
                (completion / "completion.json").read_bytes()
            )["digest"]
            if completion.exists()
            else "0" * 64,
        },
    )
    return proof


def test_mount_guard_refuses_while_mounted_and_proceeds_when_detached(
    flow: dict[str, Any], actual: dict[str, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from xlm.data.exclusion.transport import open_gate

    layout = flow["layout"]
    execute(layout, flow["plan"], flow["envelope"], actual)
    proof = proof_for(flow, tmp_path, monkeypatch)
    with pytest.raises(C05Error, match="volume is mounted"):
        with open_gate(proof):
            pass
    shutil.move(layout.root, tmp_path / "detached-root")  # The volume is detached.
    with open_gate(proof) as gate:
        assert gate is not None and gate.mode == "protected"
        assert gate.plan.isolation is not None


def test_tokenizer_and_training_refuse_benchmark_or_scratch_paths(
    flow: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from typer.testing import CliRunner

    from xlm.cli.main import app
    from xlm.data.exclusion.transport import protected_guard

    layout, plan = flow["layout"], flow["plan"]
    proof = proof_for(flow, tmp_path, monkeypatch)
    material = layout.root / "material" / "arc_easy.jsonl"
    # Mounted: the tokenizer CLI refuses before reading the benchmark file.
    result = CliRunner().invoke(
        app,
        [
            "tokenizer",
            "train",
            "--data-path",
            str(material),
            "--type",
            "byte",
            "--output-dir",
            str(tmp_path / "tok"),
            "--c05-proof",
            str(proof),
        ],
    )
    assert result.exit_code == 1, result.output
    assert not (tmp_path / "tok").exists()
    # Detached: any path under the protected root or the C05 scratch still refuses.
    shutil.move(layout.root, tmp_path / "detached-root")
    for path, name in (
        (material, "protected benchmark root"),
        (layout.scratch / plan.identity() / "decisions.jsonl", "C05 protected scratch"),
        (layout.scratch, "C05 protected scratch"),
    ):
        with pytest.raises(C05Error, match=name):
            protected_guard(plan, [path])
    protected_guard(plan, [layout.data, tmp_path / "tokenizer-output"])  # Ordinary paths.
    # Training-input resolution passes its shard paths through the same guard.
    from xlm.data.exclusion.transport import verify_training_shards

    with pytest.raises(C05Error, match="C05 protected scratch"):
        verify_training_shards({"c05_proof": str(proof)}, {"authored": layout.scratch / "x"})


def test_protected_root_cli_is_write_once_and_refuses_a_shared_real_volume(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Real os.stat devices: a temp root and data root share one volume, so refuse."""
    from xlm.data.exclusion.operator import main

    root = (tmp_path / "C05-Protected").resolve()
    assert main(["protected-root", "init", "--root", str(root), "--logical-id", "c05-cli"]) == 0
    marker = (root / "C05-PROTECTED-ROOT.json").read_bytes()
    assert b"c05-cli" in marker and b"key" not in marker.lower()
    assert main(["protected-root", "init", "--root", str(root), "--logical-id", "c05-cli"]) == 1
    assert (root / "C05-PROTECTED-ROOT.json").read_bytes() == marker
    (tmp_path / "data").mkdir()
    capsys.readouterr()
    code = main(
        [
            "protected-root",
            "describe",
            "--root",
            str(root),
            "--operator",
            getpass.getuser(),
            "--attestation-sha256",
            "7" * 64,
            "--repository",
            str(checkout_root()),
            "--data-root",
            str(tmp_path / "data"),
        ]
    )
    assert code == 1 and json.loads(capsys.readouterr().out)["refused"] is True
    # The refusal is the device rule itself, measured with the real os.stat device.
    import argparse

    from xlm.data.exclusion.operator import protected_root_command

    with pytest.raises(ValidationError, match="data_root shares the protected"):
        protected_root_command(
            argparse.Namespace(
                action="describe",
                root=root,
                operator=getpass.getuser(),
                attestation_sha256="7" * 64,
                mode="protected",
                repository=[checkout_root()],
                data_root=[tmp_path / "data"],
                training_data=[],
                c05_scratch=[],
                c05_output=[],
            )
        )
