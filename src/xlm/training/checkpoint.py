"""Atomic checkpoint publication, verification, and reproducible resume.

Complying with XLM Contracts C01, C03, C08, C09, C10 and P05 Amendments 4, 6 & 7:
- Built on P01 ArtifactStore, ArtifactLedger, and ArtifactPaths.
- Atomic publication via staging directory and rename.
- Complete SHA-256 verification against corruption.
- Strict tied-weight equality validation and object identity restoration.
- Python and PyTorch RNG state preservation.
- Plan compatibility checks (architecture, optimizer, schedule).
- Ordinary resume vs explicit fork with parent lineage.
"""

from __future__ import annotations

import io
import json
import random
import tempfile
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from xlm.artifacts.ledger import RunLedger
from xlm.artifacts.store import ArtifactStore
from xlm.core.paths import ArtifactPaths
from xlm.models.base import BaseModel
from xlm.objectives.base import BaseObjective
from xlm.optimizers import (
    ParameterGroupManifest,
    restore_optimizer_state,
    serialize_optimizer_state,
)
from xlm.schedules.base import BaseSchedule
from xlm.training.data import BatcherProtocol
from xlm.training.science import ScientificState

MAX_SCIENCE_STATE_BYTES = 128 * 1024**2

try:
    import torch
    import torch.optim as optim
except ImportError:
    torch = None  # type: ignore[assignment]
    optim = None  # type: ignore[assignment]


class CheckpointError(Exception):
    """Base exception for checkpoint operations."""


class CorruptCheckpointError(CheckpointError):
    """Raised when checkpoint files are missing, truncated, or fail SHA-256 checksums."""


class IncompatibleCheckpointError(CheckpointError):
    """Raised when checkpoint configuration is incompatible with target model/optimizer/plan."""


@dataclass
class CheckpointMetadata:
    """Metadata payload describing checkpoint state and provenance."""

    checkpoint_id: str
    run_id: str
    step: int
    committed_valid_targets: int
    processed_valid_targets: int
    plan_id: str
    model_config: dict[str, Any]
    precision: str = "fp32"
    parent_checkpoint_id: str | None = None
    parent_plan_id: str | None = None
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> CheckpointMetadata:
        return cls(**d)


class CheckpointManager:
    """Manages atomic, checksummed checkpoint save and reload operations."""

    def __init__(
        self,
        artifact_store: ArtifactStore | None = None,
        run_ledger: RunLedger | None = None,
        paths: ArtifactPaths | None = None,
        runtime_config: dict[str, Any] | None = None,
        execution: dict[str, Any] | None = None,
    ) -> None:
        self.paths = paths or ArtifactPaths.from_env()
        self.store = artifact_store or ArtifactStore(self.paths)
        self.ledger = run_ledger or RunLedger(self.paths.ledger / "ledger.sqlite")
        self.runtime_config = runtime_config
        self.execution = execution
        self._published_bytes = 0
        self.boundary_guard: Callable[[], None] | None = None

    def _save_tensor(self, state: Any, path: Path) -> None:
        """Bound private serialization before the artifact store copies these bytes."""
        cap = (
            int(self.execution["observations"].get("max_owned_disk_bytes", 2 * 1024**3))
            if self.execution
            else 2 * 1024**3
        )
        staged = sum(p.stat().st_size for p in path.parent.iterdir() if p.is_file())
        remaining = (cap - self._published_bytes) // 2 - staged - 2 * 1024**2
        if remaining <= 0:
            raise CheckpointError("checkpoint aggregate disk limit exceeded")

        class LimitedWriter(io.BufferedWriter):
            written = 0

            def write(self, data: Any) -> int:
                if self.written + len(data) > remaining:
                    raise CheckpointError("checkpoint serialization disk limit exceeded")
                count = super().write(data)
                self.written += count
                return count

        with path.open("xb", buffering=0) as raw, LimitedWriter(raw) as stream:
            torch.save(state, stream)

    def save_checkpoint(
        self,
        checkpoint_id: str,
        run_id: str,
        step: int,
        committed_valid_targets: int,
        processed_valid_targets: int,
        plan_id: str,
        model: BaseModel,
        objective: BaseObjective | None,
        optimizer: optim.Optimizer | None,
        optimizer_manifest: ParameterGroupManifest | None,
        schedule: BaseSchedule | None,
        batcher: BatcherProtocol | None,
        parent_checkpoint_id: str | None = None,
        parent_plan_id: str | None = None,
        scaler: Any | None = None,
        precision: str = "fp32",
        science: ScientificState | None = None,
    ) -> Path:
        """Atomically serialize and publish a complete training checkpoint.

        Writes state to a local staging directory, computes checksums, writes
        metadata, and atomically publishes through ArtifactStore.
        """
        if self.boundary_guard is not None:
            self.boundary_guard()
        if science is not None and science.update_payloads is not None:
            # A declared payload receipt is part of the committed state: an
            # incoherent receipt set is never published as a checkpoint.
            from xlm.config.science import ScientificPolicyError
            from xlm.training.science import ScientificRuntimeError

            if batcher is None:
                raise CheckpointError("a declared update payload receipt needs the data state")
            try:
                science.check_receipt_alignment(
                    step=step,
                    committed=committed_valid_targets,
                    data_committed=batcher.get_state().get("committed_valid_targets"),
                )
            except (ScientificPolicyError, ScientificRuntimeError) as exc:
                raise CheckpointError(f"receipt history is not coherent: {exc}") from exc
        now_str = datetime.now(UTC).isoformat()
        from xlm.artifacts.manifest import identity_digest
        from xlm.artifacts.store import compute_file_sha256

        if self.execution is not None:
            from xlm.experiments.execution import observed_origins, validate_envelope

            envelope = self.execution["envelope"]
            snapshot_dir = Path(self.execution["observations"]["snapshot_dir"])
            snapshot = validate_envelope(envelope, snapshot_dir, check_environment=False)
            observed_origins(snapshot_dir, snapshot)
            provenance = self.execution
            producer_hash = envelope["code_hash"]
            dependency_hash = envelope["dependency_hash"]
            plan_hash = self.execution["plan_hash"]
            from xlm.experiments.execution import resolve_execution_config

            if self.runtime_config is None:
                raise CheckpointError("frozen checkpoints require bound runtime configuration")
            normalized, bindings = resolve_execution_config(self.runtime_config["plan"])
            if normalized != envelope["config"] or bindings != envelope["bindings"]:
                raise CheckpointError("checkpoint runtime differs from executed envelope")
        else:
            # Domain API diagnostics remain usable, but cannot claim frozen resume.
            import importlib.metadata
            import inspect
            import platform

            source = Path(inspect.getfile(type(model))).resolve()
            provenance = {
                "status": "unresolved-domain-api",
                "frozen_continuation": False,
                "observed_model_source_hash": compute_file_sha256(source),
                "observed_checkpoint_source_hash": compute_file_sha256(Path(__file__)),
                "python": platform.python_version(),
                "torch": importlib.metadata.version("torch"),
            }
            producer_hash = identity_digest(
                {k: v for k, v in provenance.items() if "source_hash" in k}
            )
            dependency_hash = identity_digest({k: provenance[k] for k in ("python", "torch")})
            plan_hash = identity_digest({"unresolved_plan_label": plan_id, "scope": "domain-api"})
        model_config = getattr(model, "config", None)
        model_config_dict: dict[str, Any] = {}
        if model_config is not None:
            if hasattr(model_config, "model_dump") and callable(model_config.model_dump):
                model_config_dict = model_config.model_dump()
            elif hasattr(model_config, "__dict__"):
                model_config_dict = dict(model_config.__dict__)
            else:
                model_config_dict = dict(model_config)

        with tempfile.TemporaryDirectory() as tmpdir:
            stage_dir = Path(tmpdir)
            files_to_publish: dict[str, Path] = {}
            execution_path = stage_dir / "execution.json"
            execution_path.write_text(json.dumps(provenance, sort_keys=True), encoding="utf-8")
            files_to_publish["execution.json"] = execution_path
            if self.runtime_config is not None:
                runtime = dict(self.runtime_config)
                if self.execution is not None:
                    runtime["execution_hash"] = self.execution["envelope"]["execution_hash"]
                    runtime["plan_hash"] = self.execution["plan_hash"]
                runtime_path = stage_dir / "runtime.json"
                runtime_path.write_text(
                    json.dumps(runtime, indent=2, sort_keys=True), encoding="utf-8"
                )
                files_to_publish["runtime.json"] = runtime_path

            # 1. Model config and weights
            model_cfg_path = stage_dir / "model_config.json"
            model_cfg_path.write_text(
                json.dumps(model_config_dict, indent=2, sort_keys=True), encoding="utf-8"
            )
            files_to_publish["model_config.json"] = model_cfg_path

            if torch is not None:
                model_pt_path = stage_dir / "model.pt"
                self._save_tensor(model.state_dict(), model_pt_path)
                files_to_publish["model.pt"] = model_pt_path

            # 2. Objective state
            if objective is not None:
                obj_state = objective.get_state()
                if torch is not None:
                    obj_pt_path = stage_dir / "objective.pt"
                    self._save_tensor(obj_state, obj_pt_path)
                    files_to_publish["objective.pt"] = obj_pt_path

            # 3. Optimizer state & manifest
            if optimizer is not None and optimizer_manifest is not None:
                opt_serialized = serialize_optimizer_state(optimizer, optimizer_manifest)
                if torch is not None:
                    opt_pt_path = stage_dir / "optimizer.pt"
                    self._save_tensor(opt_serialized, opt_pt_path)
                    files_to_publish["optimizer.pt"] = opt_pt_path

            # 4. Schedule state
            if schedule is not None:
                sched_state = schedule.state_dict()
                sched_json_path = stage_dir / "schedule.json"
                sched_json_path.write_text(json.dumps(sched_state, indent=2), encoding="utf-8")
                files_to_publish["schedule.json"] = sched_json_path

            # 5. Data batcher state
            if batcher is not None:
                batcher_state = batcher.get_state()
                data_json_path = stage_dir / "data_state.json"
                data_json_path.write_text(json.dumps(batcher_state, indent=2), encoding="utf-8")
                files_to_publish["data_state.json"] = data_json_path

            # 6. RNG state
            rng_state: dict[str, Any] = {
                "python": random.getstate(),
            }
            if torch is not None:
                rng_state["torch"] = torch.get_rng_state()
                try:
                    import numpy as np
                except ImportError:
                    pass
                else:
                    name, keys, position, has_gauss, cached_gaussian = np.random.get_state()
                    rng_state["numpy"] = (name, keys.tolist(), position, has_gauss, cached_gaussian)
                if torch.cuda.is_available():
                    rng_state["cuda"] = torch.cuda.get_rng_state_all()
                rng_pt_path = stage_dir / "rng_state.pt"
                self._save_tensor(rng_state, rng_pt_path)
                files_to_publish["rng_state.pt"] = rng_pt_path

            # 6a. Science-v1 provenance. Legacy checkpoints keep their historical
            # file set; the absence of science.json is what marks them legacy.
            if science is not None and science.policy.is_science:
                science_path = stage_dir / "science.json"
                science_text = json.dumps(science.to_checkpoint(), sort_keys=True)
                if (
                    science.update_payloads is not None
                    and len(science_text.encode("utf-8")) > MAX_SCIENCE_STATE_BYTES
                ):
                    # Never publish receipts no loader would accept (fail closed here).
                    raise CheckpointError(
                        f"science.json with its update payload receipt exceeds the "
                        f"{MAX_SCIENCE_STATE_BYTES}-byte bound"
                    )
                science_path.write_text(science_text, encoding="utf-8")
                files_to_publish["science.json"] = science_path

            # 6b. Gradient scaler state (FP16 mode only; absent otherwise, never faked)
            if scaler is not None and torch is not None:
                scaler_pt_path = stage_dir / "scaler.pt"
                self._save_tensor(scaler.state_dict(), scaler_pt_path)
                files_to_publish["scaler.pt"] = scaler_pt_path

            # 7. Checkpoint metadata
            meta = CheckpointMetadata(
                checkpoint_id=checkpoint_id,
                run_id=run_id,
                step=step,
                committed_valid_targets=committed_valid_targets,
                processed_valid_targets=processed_valid_targets,
                plan_id=plan_id,
                model_config=model_config_dict,
                precision=precision,
                parent_checkpoint_id=parent_checkpoint_id,
                parent_plan_id=parent_plan_id,
                created_at=now_str,
            )
            meta_json_path = stage_dir / "checkpoint_meta.json"
            meta_json_path.write_text(json.dumps(meta.to_dict(), indent=2), encoding="utf-8")
            files_to_publish["checkpoint_meta.json"] = meta_json_path

            # Publish via ArtifactStore (computes sha256 checksums, writes
            # manifest.json and _COMPLETED)
            dest_dir = self.store.publish_artifact(
                artifact_id=checkpoint_id,
                kind="checkpoints",
                files=files_to_publish,
                producer_code_hash=producer_hash,
                dependency_hash=dependency_hash,
                resolved_config_hash=plan_hash,
                metadata={
                    **meta.to_dict(),
                    "execution_provenance_hash": identity_digest(provenance),
                },
            )

            # Record in SQLite ledger
            manifest = self.store.load_manifest(dest_dir)
            self._published_bytes += sum(
                p.stat().st_size for p in dest_dir.iterdir() if p.is_file()
            )
            self.ledger.record_artifact(
                artifact_id=checkpoint_id,
                kind="checkpoints",
                path=dest_dir,
                manifest_json=manifest.model_dump_json(),
            )

            return dest_dir

    def load_checkpoint(
        self,
        checkpoint_dir: Path,
        model: BaseModel | None = None,
        objective: BaseObjective | None = None,
        optimizer: optim.Optimizer | None = None,
        optimizer_manifest: ParameterGroupManifest | None = None,
        schedule: BaseSchedule | None = None,
        batcher: BatcherProtocol | None = None,
        expected_plan_id: str | None = None,
        device: Any = "cpu",
        is_fork: bool = False,
        scaler: Any | None = None,
        science: ScientificState | None = None,
    ) -> CheckpointMetadata:
        """Verify checksums, validate plan compatibility, and restore state.

        Args:
            checkpoint_dir: Directory containing published checkpoint artifact.
            model: Optional model instance to restore.
            objective: Optional objective instance to restore.
            optimizer: Optional optimizer instance to restore.
            optimizer_manifest: Manifest matching current optimizer parameters.
            schedule: Optional schedule instance to restore.
            batcher: Optional TrainingBatcher to restore.
            expected_plan_id: If set and not forking, ensures plan_id matches.
            device: Map location for tensor deserialization.
            is_fork: Whether this reload is for an explicit forked run.
            science: This run's scientific state. ``None`` means the legacy policy.
                Ordinary resume requires the checkpoint's policy to match; the
                committed LR/RNG history is adopted without any reseed.

        Returns:
            Restored CheckpointMetadata.
        """
        # 1. Integrity check through ArtifactStore
        try:
            self.store.verify_artifact(checkpoint_dir)
        except Exception as exc:
            raise CorruptCheckpointError(
                f"Checkpoint integrity verification failed: {exc}"
            ) from exc

        if self.execution is not None:
            from xlm.artifacts.manifest import identity_digest
            from xlm.experiments.execution import read_json, resolve_execution_config

            saved = read_json(checkpoint_dir / "execution.json")
            manifest = self.store.load_manifest(checkpoint_dir)
            if "envelope" not in saved or manifest.metadata.get(
                "execution_provenance_hash"
            ) != identity_digest(saved):
                raise IncompatibleCheckpointError("legacy/unresolved execution provenance")
            runtime = read_json(checkpoint_dir / "runtime.json")
            normalized, bindings = resolve_execution_config(runtime["plan"])
            if (
                normalized != saved["envelope"]["config"]
                or bindings != saved["envelope"]["bindings"]
                or runtime.get("execution_hash") != saved["envelope"]["execution_hash"]
                or runtime.get("plan_hash") != saved["plan_hash"]
                or manifest.producer_code_hash != saved["envelope"]["code_hash"]
                or manifest.dependency_hash != saved["envelope"]["dependency_hash"]
                or manifest.resolved_config_hash != saved["plan_hash"]
            ):
                raise IncompatibleCheckpointError("checkpoint runtime/envelope identity mismatch")
            if not is_fork and (
                saved["envelope"] != self.execution["envelope"]
                or saved["plan_hash"] != self.execution["plan_hash"]
            ):
                raise IncompatibleCheckpointError(
                    "changed execution identity requires explicit fork"
                )

        # 2. Load and validate metadata
        meta_path = checkpoint_dir / "checkpoint_meta.json"
        if not meta_path.is_file():
            raise CorruptCheckpointError(f"Missing checkpoint_meta.json at {checkpoint_dir}")
        meta_dict = json.loads(meta_path.read_text(encoding="utf-8"))
        meta = CheckpointMetadata.from_dict(meta_dict)

        if not is_fork and expected_plan_id is not None and meta.plan_id != expected_plan_id:
            raise IncompatibleCheckpointError(
                f"Checkpoint plan_id '{meta.plan_id}' does not match expected "
                f"plan '{expected_plan_id}'. "
                "Use --fork to resume an altered plan from this checkpoint."
            )

        # 2b. Scientific policy compatibility, before any state is restored.
        saved_science = self._read_science_state(checkpoint_dir)
        from xlm.config.science import ScientificPolicy, ScientificPolicyError

        try:
            saved_policy = (
                ScientificPolicy.from_identity(saved_science["policy"])
                if saved_science is not None
                else ScientificPolicy.legacy()
            )
        except (KeyError, TypeError, ScientificPolicyError) as exc:
            raise IncompatibleCheckpointError(f"unreadable scientific policy: {exc}") from exc
        current_policy = science.policy if science is not None else ScientificPolicy.legacy()
        if saved_policy != current_policy and not is_fork:
            raise IncompatibleCheckpointError(
                f"scientific policy changed ({saved_policy.identity()} -> "
                f"{current_policy.identity()}); ordinary resume refused, fork or start a "
                "new experiment explicitly"
            )
        if science is not None and saved_science is not None and not is_fork:
            # Evaluation and checkpoint plan identity, also before any state is restored.
            try:
                science._saved_evaluation(saved_science)
                science._saved_checkpoints(saved_science)
            except ScientificPolicyError as exc:
                raise IncompatibleCheckpointError(str(exc)) from exc
        # Update payload receipt lineage, before any state is restored, for ordinary
        # resume and every kind of fork (a no-op when neither side has the receipt).
        self._check_receipt_lineage(
            checkpoint_dir, meta, science, saved_science, saved_policy == current_policy, is_fork
        )

        # 2c. Document-order identity (P35 M5), before any state is restored. Forks
        # keep the data lineage, so they require the identical order as well.
        if batcher is not None:
            from xlm.data.ordering import OrderManifestError, check_order_resume

            data_path = checkpoint_dir / "data_state.json"
            if not data_path.is_file():
                raise CorruptCheckpointError(f"Missing data_state.json at {checkpoint_dir}")
            try:
                check_order_resume(
                    json.loads(data_path.read_text(encoding="utf-8")), batcher.get_state()
                )
            except OrderManifestError as exc:
                raise IncompatibleCheckpointError(str(exc)) from exc

        # 3. Restore model weights & validate tied weights
        if model is not None:
            model_pt_path = checkpoint_dir / "model.pt"
            if not model_pt_path.is_file():
                raise CorruptCheckpointError(f"Missing model.pt at {checkpoint_dir}")

            model_config = getattr(model, "config", None)
            if model_config is not None:
                # Validate critical architectural parameters match
                chk_cfg = meta.model_config
                for field in ("vocab_size", "hidden_size", "num_layers", "num_attention_heads"):
                    if field in chk_cfg and getattr(model_config, field, None) != chk_cfg[field]:
                        raise IncompatibleCheckpointError(
                            f"Model architecture mismatch for '{field}': "
                            f"checkpoint has {chk_cfg[field]}, "
                            f"model has {getattr(model_config, field)}"
                        )

            state_dict = torch.load(model_pt_path, map_location=device, weights_only=True)

            # Tied weights verification
            tie_embeddings = getattr(model_config, "tie_embeddings", True)
            if tie_embeddings:
                if "embed_tokens.weight" in state_dict and "lm_head.weight" in state_dict:
                    if not torch.equal(
                        state_dict["embed_tokens.weight"], state_dict["lm_head.weight"]
                    ):
                        raise CorruptCheckpointError(
                            "Tied weights in checkpoint 'embed_tokens.weight' and 'lm_head.weight' "
                            "are not bitwise equal."
                        )

            model.load_state_dict(state_dict)

            # Re-establish tied Parameter object identity
            if tie_embeddings:
                embed_tokens = getattr(model, "embed_tokens", None)
                lm_head = getattr(model, "lm_head", None)
                if embed_tokens is not None and lm_head is not None:
                    lm_head.weight = embed_tokens.weight

        # 4. Restore objective
        if objective is not None:
            obj_pt_path = checkpoint_dir / "objective.pt"
            if obj_pt_path.is_file():
                obj_state = torch.load(obj_pt_path, map_location=device, weights_only=True)
                objective.load_state(obj_state)

        # 5. Restore optimizer
        if optimizer is not None and optimizer_manifest is not None:
            opt_pt_path = checkpoint_dir / "optimizer.pt"
            if not opt_pt_path.is_file():
                raise CorruptCheckpointError(f"Missing optimizer.pt at {checkpoint_dir}")
            opt_state = torch.load(opt_pt_path, map_location=device, weights_only=True)
            try:
                restore_optimizer_state(optimizer, opt_state, optimizer_manifest)
            except Exception as exc:
                raise IncompatibleCheckpointError(
                    f"Optimizer parameter group manifest mismatch on resume: {exc}"
                ) from exc

        # 6. Restore schedule (for ordinary resume; fork uses its own configured schedule)
        if schedule is not None and not is_fork:
            sched_path = checkpoint_dir / "schedule.json"
            if not sched_path.is_file():
                raise CorruptCheckpointError(f"Missing schedule.json at {checkpoint_dir}")
            sched_state = json.loads(sched_path.read_text(encoding="utf-8"))
            try:
                schedule.load_state_dict(sched_state)
            except Exception as exc:
                raise IncompatibleCheckpointError(f"Schedule resume rejected: {exc}") from exc

        # 7. Restore data batcher
        if batcher is not None:
            data_path = checkpoint_dir / "data_state.json"
            if not data_path.is_file():
                raise CorruptCheckpointError(f"Missing data_state.json at {checkpoint_dir}")
            data_state = json.loads(data_path.read_text(encoding="utf-8"))
            batcher.load_state(data_state)

        # 7b. Restore gradient scaler state when the checkpoint carries one.
        # A checkpoint with scaler state resumed without a scaler is an explicit
        # mismatch, not a silent fresh start: refusing protects FP16 continuity.
        scaler_path = checkpoint_dir / "scaler.pt"
        if scaler_path.is_file():
            if scaler is None and not is_fork:
                raise IncompatibleCheckpointError(
                    "Checkpoint carries gradient scaler state but no scaler was "
                    "provided. Resume an FP16 run with an FP16 scaler, or fork "
                    "explicitly to change precision."
                )
            # An explicit fork may deliberately change precision; the old scaler
            # state is then abandoned by record of the fork, never silently.
            if scaler is not None and torch is not None:
                scaler_state = torch.load(scaler_path, map_location="cpu", weights_only=True)
                scaler.load_state_dict(scaler_state)

        # 8. Restore RNG state
        rng_path = checkpoint_dir / "rng_state.pt"
        if rng_path.is_file() and torch is not None:
            rng_data = torch.load(rng_path, map_location="cpu", weights_only=True)
            if "python" in rng_data:
                random.setstate(rng_data["python"])
            if "torch" in rng_data:
                torch.set_rng_state(rng_data["torch"])
            if "numpy" in rng_data:
                import numpy as np

                name, keys, position, has_gauss, cached_gaussian = rng_data["numpy"]
                np.random.set_state(
                    (name, np.asarray(keys, dtype=np.uint32), position, has_gauss, cached_gaussian)
                )
            if "cuda" in rng_data and torch.cuda.is_available():
                torch.cuda.set_rng_state_all(rng_data["cuda"])

        # 9. Adopt committed science history last; restored RNG is authoritative.
        if science is not None and saved_science is not None and saved_policy == current_policy:
            try:
                science.restore(saved_science, fork=is_fork)
            except (KeyError, TypeError, ScientificPolicyError) as exc:
                raise IncompatibleCheckpointError(f"science state rejected: {exc}") from exc
        if science is not None and is_fork:
            try:
                science.rebase_evaluation(meta.committed_valid_targets)
                science.rebase_checkpoints(meta.committed_valid_targets)
            except ScientificPolicyError as exc:
                raise IncompatibleCheckpointError(str(exc)) from exc

        return meta

    @staticmethod
    def _check_receipt_lineage(
        checkpoint_dir: Path,
        meta: CheckpointMetadata,
        science: ScientificState | None,
        saved_science: dict[str, Any] | None,
        same_policy: bool,
        is_fork: bool,
    ) -> None:
        """Update payload receipt semantics across resume and forks (refuse before restore).

        * Presence and version never change within a lineage, for ordinary
          resume and for every fork (same policy, changed policy, legacy or
          pre-readiness parent): enabling, disabling or re-versioning the receipt
          is a new experiment from initialization.
        * A carried chain must agree with the checkpoint's LR history, step,
          committed count and committed data state (:func:`check_receipt_history`).
        * A same-policy fork inherits the chain with the data lineage. A
          changed-policy fork does not adopt the parent's LR history, so it may
          not continue a chain: it is refused unless it starts from the initial
          C = 0 state, where the child begins its own chain at genesis.
        """
        required = science is not None and science.update_payloads is not None
        saved = saved_science.get("update_payloads") if saved_science is not None else None
        if not required and saved is None:
            return  # neither side declares the receipt: historical behavior
        from xlm.config.science import ScientificPolicyError
        from xlm.data.sampling.update_payload import CHAIN_VERSION, PAYLOAD_VERSION
        from xlm.training.science import check_receipt_history

        route = "fork" if is_fork else "ordinary resume"
        if required != (saved is not None):
            raise IncompatibleCheckpointError(
                "update payload receipt presence differs between checkpoint and run "
                f"({'receipted' if saved is not None else 'receipt-free or pre-readiness'} "
                f"checkpoint, {'receipted' if required else 'receipt-free'} run, {route}); a "
                "chain cannot start mid-lineage or be dropped: enabling or disabling the "
                "receipt is a new experiment from initialization"
            )
        assert science is not None and saved_science is not None
        if (
            not isinstance(saved, dict)
            or saved.get("version") != PAYLOAD_VERSION
            or saved.get("chain_version") != CHAIN_VERSION
        ):
            found = (
                (saved.get("version"), saved.get("chain_version"))
                if isinstance(saved, dict)
                else None
            )
            raise IncompatibleCheckpointError(
                f"update payload receipt version differs (checkpoint {found}, run "
                f"{(PAYLOAD_VERSION, CHAIN_VERSION)}, {route}); a changed receipt version is a "
                "new experiment, never a continuation"
            )
        data_path = checkpoint_dir / "data_state.json"
        if not data_path.is_file():
            raise CorruptCheckpointError(f"Missing data_state.json at {checkpoint_dir}")
        data_state = json.loads(data_path.read_text(encoding="utf-8"))
        try:
            check_receipt_history(
                saved_science,
                step=meta.step,
                committed=meta.committed_valid_targets,
                data_committed=(
                    data_state.get("committed_valid_targets")
                    if isinstance(data_state, dict)
                    else None
                ),
            )
            if same_policy:
                science._saved_update_payloads(saved_science)
        except ScientificPolicyError as exc:
            raise IncompatibleCheckpointError(str(exc)) from exc
        if not same_policy and (meta.step != 0 or meta.committed_valid_targets != 0):
            raise IncompatibleCheckpointError(
                "a changed-scientific-policy fork cannot continue an update payload receipt "
                "chain: the chain certifies one policy's committed lineage from initialization "
                "and the fork does not adopt the parent's LR history; start a new experiment "
                "(a fork of the initial C=0 state begins its own chain at genesis)"
            )

    @staticmethod
    def _read_science_state(checkpoint_dir: Path) -> dict[str, Any] | None:
        path = checkpoint_dir / "science.json"
        if not path.is_file():
            return None
        with path.open("rb") as stream:
            raw = stream.read(MAX_SCIENCE_STATE_BYTES + 1)
        if len(raw) > MAX_SCIENCE_STATE_BYTES:
            raise CorruptCheckpointError("science.json exceeds its byte bound")
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise CorruptCheckpointError("science.json must be an object")
        return value
