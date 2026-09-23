"""D06 real captured-process regressions with bounded authored CPU fixtures."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from xlm.artifacts.ledger import RunLedger
from xlm.artifacts.store import ArtifactStore
from xlm.core.paths import ArtifactPaths
from xlm.experiments.plans import ExecutablePlan, freeze_execution
from xlm.experiments.queue import ExperimentQueue
from xlm.experiments.snapshot import capture_snapshot

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class FrozenSeed:
    """Immutable bytes only; ledgers, queues and model objects are never shared."""

    files: tuple[tuple[str, bytes], ...]
    identity: str


def _seed_identity(files: tuple[tuple[str, bytes], ...]) -> str:
    digest = hashlib.sha256(b"frozen-authored-seed-v1\0")
    for name, payload in files:
        for value in (name.encode(), payload):
            digest.update(len(value).to_bytes(8, "little"))
            digest.update(value)
    return digest.hexdigest()


@pytest.fixture(scope="session")
def frozen_seed(tmp_path_factory: pytest.TempPathFactory) -> FrozenSeed:
    """Run the real generator once, retaining its exact code/config/runtime identity.

    The byte snapshot includes generator output, seed, record count, config and
    dependency hashes. It lives only in this pytest session, never across commits.
    Every consumer gets new files and a new queue. Worker integrity checks still
    hash the actual installed runtime; no verifier is patched or cached.
    """
    root = tmp_path_factory.mktemp("frozen-seed")
    frozen_fixture(root)
    paths = [root / "plan.json"]
    paths += [p for name in ("tree", "snapshot") for p in (root / name).rglob("*") if p.is_file()]
    files = tuple((p.relative_to(root).as_posix(), p.read_bytes()) for p in sorted(paths))
    assert sum(len(data) for _, data in files) < 32 * 1024**2
    return FrozenSeed(files, _seed_identity(files))


def authored_tree(tree: Path, behavior: str | None = None) -> Path:
    files = [
        p
        for p in (ROOT / "src").rglob("*")
        if p.is_file() and p.suffix in (".py", ".yaml") and "__pycache__" not in p.parts
    ]
    files += [ROOT / name for name in ("pyproject.toml", "uv.lock", ".python-version")]
    assert len(files) < 400 and sum(p.stat().st_size for p in files) < 16 * 1024**2
    for source in files:
        target = tree / source.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    if behavior:
        trainer = tree / "src/xlm/training/trainer.py"
        text = trainer.read_text(encoding="utf-8")
        signature = "    def train_step(self) -> TrainingStepMetrics | None:\n"
        assert signature in text
        trainer.write_text(
            text.replace(signature, signature + f"        {behavior}\n", 1), encoding="utf-8"
        )
    return tree


def frozen_fixture(
    tmp_path: Path, behavior: str | None = None, *, seed: FrozenSeed | None = None
) -> tuple[ExecutablePlan, ExperimentQueue, str, Path, Path]:
    if seed is not None:
        if behavior is not None:
            raise ValueError("custom trainer behavior requires its own fresh frozen fixture")
        assert _seed_identity(seed.files) == seed.identity
        for name, payload in seed.files:
            path = tmp_path / name
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("xb") as stream:
                stream.write(payload)
        return _admit_fixture(ExecutablePlan.load(tmp_path / "plan.json"), tmp_path)
    tree = authored_tree(tmp_path / "tree", behavior)
    snapshot_path = tmp_path / "snapshot"
    snapshot = capture_snapshot(tree, snapshot_path, max_snapshot_bytes=16 * 1024**2)
    config = {
        "model": {
            "architecture": "transformer_baseline",
            "vocab_size": 64,
            "num_layers": 2,
            "hidden_size": 32,
            "num_attention_heads": 2,
            "intermediate_size": 64,
            "context_length": 16,
            "attention_backend": "eager",
        },
        "data": {"synthetic_tokens": [4 + i % 60 for i in range(128)]},
        "objective": {"type": "cross_entropy"},
        "optimizer": {"type": "adamw", "lr": 0.01},
        "training": {
            "device": "cpu",
            "precision": "fp32",
            "init_seed": 7,
            "data_seed": 42,
            "context_length": 16,
            "global_batch_valid_targets": 8,
            "budget": {"max_valid_targets": 8, "max_train_seconds": 10},
            "schedule": {
                "type": "warmup_cosine",
                "warmup_valid_targets": 0,
                "horizon_valid_targets": 8,
            },
            "checkpoint_every_valid_targets": 8,
        },
    }
    plan = ExecutablePlan(
        plan_version="1",
        plan_id="draft",
        plan_hash="",
        draft_id="authored_d06",
        track="baseline",
        horizon_kind="standalone",
        resolved_config=config,
        code_snapshot=snapshot,
        dependency_hash="",
        seeds={"init_seed": 7, "data_seed": 42},
        budget_valid_targets=8,
        budget_max_seconds=10,
        estimated_new_disk_gib=0.01,
        gpu_processes=1,
        evaluation_tier="search",
        checkpoint_every_valid_targets=8,
        evaluation_every_valid_targets=8,
        exposure={"fixture": True},
        cost_estimate={"basis": "authored"},
        storage_estimate_gib=0.01,
    )
    freeze_execution(plan, snapshot_path, ["cpu"])
    return _admit_fixture(plan, tmp_path)


def _admit_fixture(
    plan: ExecutablePlan, tmp_path: Path
) -> tuple[ExecutablePlan, ExperimentQueue, str, Path, Path]:
    tree, snapshot_path = tmp_path / "tree", tmp_path / "snapshot"
    path = plan.save(tmp_path / "plan.json")
    paths = ArtifactPaths(root=tmp_path / "home")
    queue = ExperimentQueue(RunLedger(paths.ledger / "ledger.sqlite"), paths, tree_root=tree)
    job_id, _ = queue.submit(plan, path, snapshot_path, "cpu", authorization_token="smoke-policy")
    return plan, queue, job_id, tree, snapshot_path


@pytest.mark.serial
def test_frozen_seed_copies_are_private(tmp_path: Path, frozen_seed: FrozenSeed) -> None:
    left = frozen_fixture(tmp_path / "left", seed=frozen_seed)
    right = frozen_fixture(tmp_path / "right", seed=frozen_seed)
    assert left[0].to_dict() == right[0].to_dict()
    assert left[1].paths.root != right[1].paths.root
    relative = "code/src/xlm/training/trainer.py"
    expected = (right[4] / relative).read_bytes()
    (left[4] / relative).write_text("private mutation\n", encoding="utf-8")
    left[0].resolved_config["training"]["init_seed"] = 999
    assert (right[4] / relative).read_bytes() == expected
    assert right[0].resolved_config["training"]["init_seed"] == 7
    assert _seed_identity(frozen_seed.files) == frozen_seed.identity


@pytest.mark.serial
def test_independent_persisted_bindings_refuse_before_worker(
    tmp_path: Path, frozen_seed: FrozenSeed
) -> None:
    from xlm.experiments.execution import read_json, write_json

    plan, queue, _, _, snapshot = frozen_fixture(tmp_path, seed=frozen_seed)
    for case in ("plan", "job", "authorization", "missing_admission", "invalid_json"):
        job_id, _ = queue.submit(
            plan, tmp_path / "plan.json", snapshot, "cpu", "smoke", allow_duplicate=True
        )
        job = queue.get_job(job_id)
        assert job is not None
        admission = Path(job.work_dir) / "admission.json"
        expected = "identity mismatch"
        if case == "plan":
            document = read_json(Path(job.plan_path))
            document["resolved_config"]["training"]["init_seed"] += 1
            write_json(Path(job.plan_path), document)
        elif case == "job":
            queue._write_job_row(replace(job, max_retries=9))
        elif case == "authorization":
            document = read_json(admission)
            document["authorization"]["max_valid_targets"] = 1
            write_json(admission, document)
        elif case == "missing_admission":
            admission.unlink()
            expected = "admission.json"
        else:
            Path(job.plan_path).write_text("not JSON", encoding="utf-8")
            expected = "Expecting value"
        result = queue.run_job(job_id)
        assert result["state"] == "BLOCKED", (case, result)
        assert expected in result["reason"], (case, result)
        assert not (Path(job.work_dir) / "worker-1").exists()


@pytest.mark.serial
def test_authority_and_recomputed_envelope_fields_are_checked(
    tmp_path: Path, frozen_seed: FrozenSeed
) -> None:
    import copy

    from xlm.artifacts.manifest import identity_digest
    from xlm.experiments.authorization import AuthorizationError, issue_ticket
    from xlm.experiments.execution import validate_envelope

    plan, queue, _, _, snapshot = frozen_fixture(tmp_path, seed=frozen_seed)
    assert plan.execution_envelope is not None
    for wrong_hash, targets in (("wrong", 8), (plan.plan_hash, 1)):
        ticket = issue_ticket(wrong_hash, targets, "authored operator", "test")
        with pytest.raises(AuthorizationError):
            queue.submit(
                plan, tmp_path / "plan.json", snapshot, "cpu", "label", authorization=ticket
            )
    for case in ("data", "tokenizer", "components", "seed", "runtime", "lock"):
        envelope = copy.deepcopy(plan.execution_envelope)
        if case in ("data", "tokenizer", "components"):
            envelope["bindings"][case] = "forged"
        elif case == "seed":
            envelope["config"]["training"].pop("init_seed")
        elif case == "runtime":
            envelope["runtime_policy"]["deterministic_algorithms"] = False
        else:
            envelope["dependency_hash"] = "forged"
        envelope["execution_hash"] = identity_digest(
            {k: v for k, v in envelope.items() if k != "execution_hash"}
        )
        with pytest.raises(ValueError, match="identity mismatch|runtime policy|lock mismatch"):
            validate_envelope(envelope, snapshot, check_environment=False)


@pytest.mark.serial
def test_actual_environment_mismatch_fails_in_real_worker(
    tmp_path: Path, frozen_seed: FrozenSeed
) -> None:
    from xlm.artifacts.manifest import identity_digest

    plan, queue, _, _, snapshot = frozen_fixture(tmp_path, seed=frozen_seed)
    assert plan.execution_envelope is not None
    envelope = plan.execution_envelope
    envelope["environment"]["installed_files_hash"] = "0" * 64
    envelope["execution_hash"] = identity_digest(
        {k: v for k, v in envelope.items() if k != "execution_hash"}
    )
    plan.plan_hash = identity_digest(plan.identity_payload())
    plan.save(tmp_path / "wrong-environment.json")
    job_id, _ = queue.submit(plan, tmp_path / "wrong-environment.json", snapshot, "cpu", "smoke")
    result = queue.run_job(job_id)
    assert result["state"] == "FAILED", result
    assert "actual installed environment differs" in result["reason"]
    job = queue.get_job(job_id)
    assert job is not None
    assert not (Path(job.work_dir) / "artifacts/checkpoints").exists()
    observed = json.loads((Path(job.work_dir) / "worker-1/process.json").read_text())
    assert observed["exit_code"] != 0


@pytest.mark.serial
def test_public_queue_and_diagnostic_receipt_keep_separate_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from typer.testing import CliRunner

    from xlm.artifacts.manifest import identity_digest
    from xlm.cli.main import app

    plan, _, _, _, snapshot = frozen_fixture(tmp_path, 'print("TRAINING_A", flush=True)')
    plan.resolved_config["model"]["vocab_size"] = 260
    plan.resolved_config["data"]["tokenizer_artifact"] = "byte"
    freeze_execution(plan, snapshot, ["cpu"])
    path = plan.save(tmp_path / "public-plan.json")
    home = tmp_path / "public-home"
    monkeypatch.setenv("XLM_HOME", str(home))
    runner = CliRunner()
    submitted = runner.invoke(
        app, ["experiment", "submit", str(path), "--snapshot-dir", str(snapshot), "--device", "cpu"]
    )
    assert submitted.exit_code == 0, (submitted.output, submitted.exception)
    executed = runner.invoke(app, ["queue", "run", "--once"])
    assert executed.exit_code == 0, (executed.output, executed.exception)
    records = list((home / "runs").glob("*/run_record.json"))
    assert len(records) == 1
    record = json.loads(records[0].read_text())
    assert record["status"] == "SUCCEEDED", record
    checkpoint = next((records[0].parent / "artifacts/checkpoints").glob("*_final"))
    evaluated = runner.invoke(
        app, ["evaluate", str(checkpoint), "--json", "--suite", "synthetic_mc"]
    )
    assert evaluated.exit_code == 0, (evaluated.output, evaluated.exception)
    receipt = json.loads(evaluated.output)[0]
    provenance = receipt["execution_provenance"]
    assert receipt["execution_provenance_hash"] == identity_digest(provenance)
    assert provenance["training"]["envelope"] == plan.execution_envelope
    assert provenance["training"]["plan_hash"] == plan.plan_hash
    assert provenance["evaluator"]["purpose"] == "evaluation"
    assert provenance["evaluator"]["code_hash"] != provenance["training"]["envelope"]["code_hash"]
    context = json.loads((checkpoint / "execution.json").read_text())
    assert context["observations"]["worker_pid"] != receipt["execution_observations"]["worker_pid"]
    assert (
        ArtifactStore(ArtifactPaths(root=home)).verify_artifact(checkpoint).resolved_config_hash
        == plan.plan_hash
    )


@pytest.mark.serial
def test_changed_tokenizer_and_real_shard_are_rejected(
    tmp_path: Path, frozen_seed: FrozenSeed
) -> None:
    from test_token_shards import make_doc
    from xlm.data.tokens import TokenShardWriter
    from xlm.experiments.execution import validate_envelope
    from xlm.tokenizers.byte import ByteTokenizer

    plan, _, _, _, snapshot = frozen_fixture(tmp_path, seed=frozen_seed)
    tokenizer = tmp_path / "tokenizer"
    ByteTokenizer().save(tokenizer)
    shard = tmp_path / "shard"
    TokenShardWriter(shard, "authored", "authored", ByteTokenizer()).write_documents(
        [make_doc("doc", "short authored content")]
    )
    plan.resolved_config["model"]["vocab_size"] = 260
    plan.resolved_config["data"] = {
        "pool_artifact": str(shard),
        "tokenizer_artifact": str(tokenizer),
    }
    freeze_execution(plan, snapshot, ["cpu"])
    assert plan.execution_envelope is not None
    manifest = tokenizer / "tokenizer_manifest.json"
    original = manifest.read_bytes()
    manifest.write_bytes(original + b"\n")
    with pytest.raises(ValueError, match="input/tokenizer identity mismatch"):
        validate_envelope(plan.execution_envelope, snapshot, check_environment=False)
    manifest.write_bytes(original)
    tokens = shard / "tokens.bin"
    original_tokens = tokens.read_bytes()
    tokens.write_bytes(bytes([original_tokens[0] ^ 1]) + original_tokens[1:])
    with pytest.raises(ValueError, match="checksum|Checksum|integrity"):
        validate_envelope(plan.execution_envelope, snapshot, check_environment=False)


@pytest.mark.serial
def test_real_worker_output_bound_and_terminal_failure(tmp_path: Path) -> None:
    plan, queue, job_id, _, snapshot = frozen_fixture(
        tmp_path, 'print("x" * (17 * 1024**2), flush=True)'
    )
    result = queue.run_job(job_id)
    assert result["state"] == "FAILED", result
    assert "output byte limit exceeded" in result["reason"]
    job = queue.get_job(job_id)
    assert job is not None
    work = Path(job.work_dir) / "worker-1"
    assert (
        sum((work / name).stat().st_size for name in ("stdout.log", "stderr.log")) <= 16 * 1024**2
    )
    assert queue.run_job(job_id)["ran"] is False
    assert len(queue.attempts(job_id)) == 1
    assert queue.submit(plan, tmp_path / "plan.json", snapshot, "cpu", "smoke") == (job_id, True)
    assert not list((snapshot / "code").rglob("__pycache__"))


@pytest.mark.serial
def test_worker_rejects_cli_override_outside_frozen_envelope(
    tmp_path: Path, frozen_seed: FrozenSeed
) -> None:
    from xlm.experiments.environment import runtime_locations
    from xlm.experiments.execution import write_json
    from xlm.experiments.launcher import launch_worker

    plan, _, _, _, snapshot = frozen_fixture(tmp_path, seed=frozen_seed)
    path = tmp_path / "train.json"
    write_json(path, {**plan.resolved_config, "id": "authored"})
    with pytest.raises(RuntimeError, match="max_targets.*differs from frozen execution"):
        launch_worker(
            {
                "action": "train",
                "envelope": plan.execution_envelope,
                "snapshot_dir": str(snapshot),
                "runtime_locations": runtime_locations(),
                "artifact_home": str(tmp_path / "rejected-home"),
                "plan_hash": plan.plan_hash,
                "plan_path": str(path),
                "options": {"device": "cpu", "max_targets": 16},
            },
            tmp_path / "rejected-worker",
        )
    assert not list((tmp_path / "rejected-home").rglob("model.pt"))
    assert json.loads((tmp_path / "rejected-worker/process.json").read_text())["exit_code"] != 0


def test_unresolved_checkpoint_is_inspectable_but_not_frozen_resume(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from xlm.cli.main import app
    from xlm.config.schemas import TransformerBaselineConfig
    from xlm.models.transformer import TransformerBaseline
    from xlm.training.checkpoint import CheckpointManager

    home = tmp_path / "home"
    manager = CheckpointManager(paths=ArtifactPaths(root=home))
    model = TransformerBaseline(
        TransformerBaselineConfig(
            vocab_size=64,
            hidden_size=16,
            intermediate_size=32,
            num_layers=1,
            num_attention_heads=2,
            attention_backend="eager",
        )
    )
    checkpoint = manager.save_checkpoint(
        "unresolved", "run", 0, 0, 0, "plan", model, None, None, None, None, None
    )
    original = {p.name: p.read_bytes() for p in checkpoint.iterdir() if p.is_file()}
    runner = CliRunner()
    inspected = runner.invoke(app, ["run", "inspect", str(checkpoint), "--json"])
    assert inspected.exit_code == 0
    assert json.loads(inspected.output)["execution_evidence"] == "unresolved-domain-api"
    verified = runner.invoke(app, ["artifact", "verify", str(checkpoint)])
    assert verified.exit_code == 0, verified.output
    for flags in ([], ["--fork"]):
        refused = runner.invoke(
            app, ["resume", str(checkpoint), *flags], env={"XLM_HOME": str(home)}
        )
        assert refused.exit_code != 0
        assert "cannot qualify for frozen continuation" in refused.output
    assert original == {p.name: p.read_bytes() for p in checkpoint.iterdir() if p.is_file()}


def test_snapshot_reuse_conflicts_and_paths(tmp_path: Path) -> None:
    from xlm.experiments.snapshot import SnapshotError, verify_snapshot

    tree = authored_tree(tmp_path / "tree")
    original = capture_snapshot(tree, tmp_path / "snapshot")
    manifest = (tmp_path / "snapshot/manifest.json").read_bytes()
    assert capture_snapshot(tree, tmp_path / "snapshot").code_hash == original.code_hash
    assert (tmp_path / "snapshot/manifest.json").read_bytes() == manifest
    source = tree / "src/xlm/training/trainer.py"
    source.write_text(source.read_text() + "\n# changed authored B\n", encoding="utf-8")
    with pytest.raises(SnapshotError, match="conflict"):
        capture_snapshot(tree, tmp_path / "snapshot")
    assert verify_snapshot(tmp_path / "snapshot/code", original, exact=True) == []
    for path in ("../outside.py", "src/xlm/TRAINING/trainer.py"):
        invalid = replace(original, included_files={**original.included_files, path: "0" * 64})
        with pytest.raises(ValueError):
            verify_snapshot(tmp_path / "snapshot/code", invalid, exact=True)


# Measured environment-inventory timing: exclusive execution only (see
# pyproject serial marker). Flagged for D06 review; behavior unchanged.
@pytest.mark.serial
@pytest.mark.environment_setup
def test_relocated_locked_environment_ignores_editable_hooks_and_bytecode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, frozen_seed: FrozenSeed
) -> None:
    import py_compile
    import subprocess

    from xlm.experiments import environment

    plan, queue, _, _, snapshot = frozen_fixture(tmp_path, seed=frozen_seed)
    venv = tmp_path / "relocated-venv"
    result = subprocess.run(
        ["uv", "sync", "--offline", "--locked", "--extra", "cpu", "--extra", "eval"],
        cwd=ROOT,
        env={**os.environ, "UV_PROJECT_ENVIRONMENT": str(venv)},
        capture_output=True,
        timeout=180,
        check=False,
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    (tmp_path / "uv-sync.log").write_bytes(result.stdout + result.stderr)
    site = venv / ("Lib/site-packages" if os.name == "nt" else "lib/python3.12/site-packages")
    python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    # Break any installer hardlink before authoring a private poison hook.
    hook = site / "_editable_impl_xlm.pth"
    hook.unlink(missing_ok=True)
    hook.write_text("import os; os._exit(86)\n", encoding="utf-8")
    poison = site / "xlm"
    poison.mkdir(exist_ok=True)
    (poison / "__init__.py").write_text('raise RuntimeError("wrong installed XLM")\n')
    authored = tmp_path / "bytecode_poison.py"
    authored.write_text('raise RuntimeError("unchecked bytecode executed")\n')
    cache = site / "filelock/__pycache__/__init__.cpython-312.pyc"
    cache.parent.mkdir(exist_ok=True)
    cache.unlink(missing_ok=True)
    py_compile.compile(
        str(authored),
        cfile=str(cache),
        invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH,
    )
    assert plan.execution_envelope is not None
    observed = environment.installed_runtime(snapshot / "code/uv.lock", ["cpu"], site)
    assert observed == plan.execution_envelope["environment"]
    # Select a different location, not a substitute executor or identity verifier.
    monkeypatch.setattr(
        environment,
        "runtime_locations",
        lambda: {"python": str(python), "site_packages": str(site)},
    )
    job_id, _ = queue.submit(
        plan, tmp_path / "plan.json", snapshot, "cpu", "smoke", allow_duplicate=True
    )
    executed = queue.run_job(job_id)
    assert executed["state"] == "SUCCEEDED", executed
    assert executed["committed_valid_targets"] == 8
    assert Path(executed["execution"]["observations"]["python"]) == python
    assert executed["execution"]["envelope"] == plan.execution_envelope
    # A real source-byte change in that private installation is rejected.
    source = site / "filelock/__init__.py"
    replacement = source.with_suffix(".replacement")
    replacement.write_bytes(source.read_bytes() + b"\n# authored dependency alteration\n")
    replacement.replace(source)
    job_id, _ = queue.submit(
        plan, tmp_path / "plan.json", snapshot, "cpu", "smoke", allow_duplicate=True
    )
    rejected = queue.run_job(job_id)
    assert rejected["state"] == "FAILED", rejected
    assert "actual installed environment differs" in rejected["reason"]
    metadata = next(site.glob("pydantic-*.dist-info"))
    metadata.rename(tmp_path / "removed-pydantic-metadata")
    with pytest.raises(ValueError, match="missing offline dependencies"):
        environment.installed_runtime(snapshot / "code/uv.lock", ["cpu"], site)


@pytest.mark.serial
def test_real_frozen_worker_trains_and_publishes(tmp_path: Path) -> None:
    plan, queue, job_id, _, snapshot = frozen_fixture(
        tmp_path, 'print("D06_AUTHORED_A", flush=True)'
    )
    result = queue.run_job(job_id)
    assert result["state"] == "SUCCEEDED", result
    assert result["committed_valid_targets"] == 8
    assert result["worker_pid"] != os.getpid()
    job = queue.get_job(job_id)
    assert job is not None
    assert "D06_AUTHORED_A" in (Path(job.work_dir) / "worker-1/stdout.log").read_text()
    assert (
        Path(result["execution"]["observations"]["train_step_origin"])
        == snapshot / "code/src/xlm/training/trainer.py"
    )
    checkpoint = Path(job.work_dir) / "artifacts/checkpoints" / result["checkpoint_id"]
    manifest = ArtifactStore(queue.paths).verify_artifact(checkpoint)
    assert manifest.producer_code_hash == plan.code_snapshot.code_hash
    assert manifest.dependency_hash == plan.dependency_hash
    assert manifest.resolved_config_hash == plan.plan_hash
    assert json.loads((checkpoint / "runtime.json").read_text())["plan_hash"] == plan.plan_hash


@pytest.mark.serial
def test_intact_a_executes_after_live_b_and_import_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan, queue, job_id, tree, snapshot = frozen_fixture(
        tmp_path, 'print("D06_AUTHORED_A", flush=True)'
    )
    (tree / "src/xlm/training/trainer.py").write_text('raise RuntimeError("LIVE_B")\n')
    (tree / "pyproject.toml").write_text("changed live configuration B\n")
    poison = tmp_path / "poison"
    poison.mkdir()
    (poison / "xlm").mkdir()
    (poison / "xlm/__init__.py").write_text('raise RuntimeError("PACKAGE_OVERRIDE")\n')
    (poison / "sitecustomize.py").write_text('raise RuntimeError("SITE_OVERRIDE")\n')
    monkeypatch.setenv("PYTHONPATH", str(poison))
    monkeypatch.setenv("PYTHONUSERBASE", str(poison))
    result = queue.run_job(job_id)
    assert result["state"] == "SUCCEEDED", result
    assert result["committed_valid_targets"] == 8 and result["worker_pid"] != os.getpid()
    job = queue.get_job(job_id)
    assert job is not None
    output = (Path(job.work_dir) / "worker-1/stdout.log").read_text()
    assert "D06_AUTHORED_A" in output and "LIVE_B" not in output
    assert result["execution"]["envelope"]["code_hash"] == plan.code_snapshot.code_hash
    assert Path(result["execution"]["observations"]["train_step_origin"]).is_relative_to(snapshot)


@pytest.mark.parametrize("damage", ["tamper", "missing", "extra"])
@pytest.mark.serial
def test_snapshot_damage_rejects_before_worker(
    tmp_path: Path, damage: str, frozen_seed: FrozenSeed
) -> None:
    _, queue, job_id, _, snapshot = frozen_fixture(tmp_path, seed=frozen_seed)
    path = snapshot / "code/src/xlm/training/trainer.py"
    if damage == "missing":
        path.unlink()
    elif damage == "tamper":
        path.write_text('raise RuntimeError("tampered")\n')
    else:
        (snapshot / "code/src/xlm/unlisted.py").write_text("VALUE = 1\n")
    result = queue.run_job(job_id)
    assert result["state"] == "BLOCKED", result
    assert "snapshot" in result["reason"]
    job = queue.get_job(job_id)
    assert job is not None and not (Path(job.work_dir) / "worker-1").exists()
