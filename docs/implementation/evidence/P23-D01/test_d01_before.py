"""Pre-approval D01 reproduction; authored artifacts only, no implementation edits."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest

from xlm.artifacts.store import ArtifactStore
from xlm.core.paths import ArtifactPaths


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def request() -> dict[str, Any]:
    return {
        "artifact_id": "authored_d01",
        "kind": "clean",
        "files": {"payload.txt": b"AAAA"},
        "producer_code_hash": digest("authored producer A"),
        "dependency_hash": digest("authored dependency graph A"),
        "resolved_config_hash": digest("authored fixed configuration"),
        "input_artifact_ids": ["authored_parent_a"],
        "metadata": {"serializer_version": "authored-v1"},
    }


def test_identical_request_reuses_unchanged_artifact(tmp_path: Path) -> None:
    store = ArtifactStore(ArtifactPaths(root=tmp_path / "store"))
    published = store.publish_artifact(**request())
    manifest = (published / "manifest.json").read_bytes()
    assert store.publish_artifact(**request()) == published
    assert (published / "manifest.json").read_bytes() == manifest


@pytest.mark.parametrize("change", ["payload", "producer", "dependency", "lineage"])
def test_conflicting_request_must_fail_and_preserve_original(tmp_path: Path, change: str) -> None:
    store = ArtifactStore(ArtifactPaths(root=tmp_path / "store"))
    published = store.publish_artifact(**request())
    before = {file.name: file.read_bytes() for file in published.iterdir()}
    changed = request()
    if change == "payload":
        changed["files"] = {"payload.txt": b"BBBB"}  # Same size and configuration.
    elif change == "producer":
        changed["producer_code_hash"] = digest("authored producer B")
    elif change == "dependency":
        changed["dependency_hash"] = digest("authored dependency graph B")
    else:
        changed["input_artifact_ids"] = ["authored_parent_b"]
    try:
        with pytest.raises(ValueError, match="[Cc]onflict"):
            store.publish_artifact(**changed)
    finally:
        assert {file.name: file.read_bytes() for file in published.iterdir()} == before


def test_publication_must_not_replace_an_incomplete_existing_directory(tmp_path: Path) -> None:
    store = ArtifactStore(ArtifactPaths(root=tmp_path / "store"))
    published = store.publish_artifact(**request())
    (published / "_COMPLETED").unlink()  # Only this test's private fixture marker.
    with pytest.raises(ValueError):
        store.publish_artifact(**request())
