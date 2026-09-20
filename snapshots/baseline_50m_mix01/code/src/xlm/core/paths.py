"""Platform-independent artifact root and directory management for XLM."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def get_artifact_root() -> Path:
    """Resolve the XLM artifact root directory.

    Checks the XLM_HOME environment variable; if unset, defaults to ~/.xlm.
    Resolves symlinks and ensures an absolute path without creating directories.
    """
    env_root = os.environ.get("XLM_HOME")
    if env_root and env_root.strip():
        root = Path(env_root.strip()).expanduser()
    else:
        root = Path.home() / ".xlm"

    return root.resolve()


@dataclass(frozen=True)
class ArtifactPaths:
    """Structured directory paths for XLM storage."""

    root: Path

    @classmethod
    def from_env(cls) -> ArtifactPaths:
        """Create ArtifactPaths from the configured environment root."""
        return cls(root=get_artifact_root())

    @property
    def raw_data(self) -> Path:
        """Directory for bounded raw data downloads."""
        return self.root / "raw"

    @property
    def clean_data(self) -> Path:
        """Directory for immutable cleaned canonical pools."""
        return self.root / "clean"

    @property
    def token_shards(self) -> Path:
        """Directory for immutable token shards."""
        return self.root / "shards"

    @property
    def tokenizers(self) -> Path:
        """Directory for frozen tokenizer artifacts."""
        return self.root / "tokenizers"

    @property
    def runs(self) -> Path:
        """Directory for training and experiment run artifacts."""
        return self.root / "runs"

    @property
    def checkpoints(self) -> Path:
        """Directory for checkpoints."""
        return self.root / "checkpoints"

    @property
    def evaluation(self) -> Path:
        """Directory for development evaluation outputs."""
        return self.root / "eval"

    @property
    def reports(self) -> Path:
        """Directory for generated reports."""
        return self.root / "reports"

    @property
    def ledger(self) -> Path:
        """Directory for local SQLite ledger."""
        return self.root / "ledger"

    def ensure_directories(self) -> None:
        """Create all standard artifact directories if they do not exist."""
        self.root.mkdir(parents=True, exist_ok=True)
        for directory in (
            self.raw_data,
            self.clean_data,
            self.token_shards,
            self.tokenizers,
            self.runs,
            self.checkpoints,
            self.evaluation,
            self.reports,
            self.ledger,
        ):
            directory.mkdir(parents=True, exist_ok=True)

    def resolve_safe_subpath(self, relative_path: str | Path) -> Path:
        """Resolve a path under the artifact root, rejecting path traversal attacks."""
        resolved = (self.root / relative_path).resolve()
        try:
            resolved.relative_to(self.root)
        except ValueError as exc:
            raise ValueError(
                f"Path traversal detected: '{relative_path}' is outside root '{self.root}'"
            ) from exc
        return resolved
