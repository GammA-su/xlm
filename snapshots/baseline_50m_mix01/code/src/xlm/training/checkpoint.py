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

import json
import random
import tempfile
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
    ) -> None:
        self.paths = paths or ArtifactPaths.from_env()
        self.store = artifact_store or ArtifactStore(self.paths)
        self.ledger = run_ledger or RunLedger(self.paths.ledger / "ledger.sqlite")

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
    ) -> Path:
        """Atomically serialize and publish a complete training checkpoint.

        Writes state to a local staging directory, computes checksums, writes
        metadata, and atomically publishes through ArtifactStore.
        """
        now_str = datetime.now(UTC).isoformat()
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

            # 1. Model config and weights
            model_cfg_path = stage_dir / "model_config.json"
            model_cfg_path.write_text(
                json.dumps(model_config_dict, indent=2, sort_keys=True), encoding="utf-8"
            )
            files_to_publish["model_config.json"] = model_cfg_path

            if torch is not None:
                model_pt_path = stage_dir / "model.pt"
                torch.save(model.state_dict(), model_pt_path)
                files_to_publish["model.pt"] = model_pt_path

            # 2. Objective state
            if objective is not None:
                obj_state = objective.get_state()
                if torch is not None:
                    obj_pt_path = stage_dir / "objective.pt"
                    torch.save(obj_state, obj_pt_path)
                    files_to_publish["objective.pt"] = obj_pt_path

            # 3. Optimizer state & manifest
            if optimizer is not None and optimizer_manifest is not None:
                opt_serialized = serialize_optimizer_state(optimizer, optimizer_manifest)
                if torch is not None:
                    opt_pt_path = stage_dir / "optimizer.pt"
                    torch.save(opt_serialized, opt_pt_path)
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
                if torch.cuda.is_available():
                    rng_state["cuda"] = torch.cuda.get_rng_state_all()
                rng_pt_path = stage_dir / "rng_state.pt"
                torch.save(rng_state, rng_pt_path)
                files_to_publish["rng_state.pt"] = rng_pt_path

            # 6b. Gradient scaler state (FP16 mode only; absent otherwise, never faked)
            if scaler is not None and torch is not None:
                scaler_pt_path = stage_dir / "scaler.pt"
                torch.save(scaler.state_dict(), scaler_pt_path)
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
            plan_hash = f"{plan_id}_plan_hash"[:16]
            dest_dir = self.store.publish_artifact(
                artifact_id=checkpoint_id,
                kind="checkpoints",
                files=files_to_publish,
                producer_code_hash="P05_TRAINER_CHECKPOINT",
                dependency_hash="TORCH_P05",
                resolved_config_hash=plan_hash,
                metadata=meta.to_dict(),
            )

            # Record in SQLite ledger
            manifest = self.store.load_manifest(dest_dir)
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
            opt_state = torch.load(opt_pt_path, map_location=device, weights_only=False)
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
            rng_data = torch.load(rng_path, map_location="cpu", weights_only=False)
            if "python" in rng_data:
                random.setstate(rng_data["python"])
            if "torch" in rng_data:
                torch.set_rng_state(rng_data["torch"])
            if "cuda" in rng_data and torch.cuda.is_available():
                torch.cuda.set_rng_state_all(rng_data["cuda"])

        return meta
