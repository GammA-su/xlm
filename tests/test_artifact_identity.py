"""D01 regressions: immutable request equivalence on bounded authored artifacts."""

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


def snapshot(path: Path) -> dict[str, bytes]:
    return {p.relative_to(path).as_posix(): p.read_bytes() for p in path.rglob("*") if p.is_file()}


def legacy_artifact(root: Path, artifact_id: str = "legacy") -> Path:
    """Independently authored v1 fixture, not a rewritten current artifact."""
    import json

    path = root / "clean" / artifact_id
    path.mkdir(parents=True)
    payload = b"legacy fixture"
    (path / "payload.txt").write_bytes(payload)
    manifest = {
        "schema_version": 1,
        "artifact_id": artifact_id,
        "kind": "clean",
        "status": "completed",
        "created_at": "2026-09-19T00:00:00Z",
        "producer_code_hash": "historical_unknown",
        "dependency_hash": "historical_unknown",
        "resolved_config_hash": digest("legacy declared config"),
        "input_artifact_ids": [],
        "files": [
            {
                "path": "payload.txt",
                "size_bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        ],
        "metadata": {},
    }
    (path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (path / "_COMPLETED").write_text("authored fixture marker", encoding="utf-8")
    return path


def test_cosmetics_reuse_without_touching_original_and_keys_are_separate(tmp_path: Path) -> None:
    store = ArtifactStore(ArtifactPaths(root=tmp_path / "store"))
    args = request()
    args["cosmetic_metadata"] = {"created_at": "yesterday", "display_label": "one"}
    path = store.publish_artifact(**args)
    original = snapshot(path)
    timestamps = {p.name: p.stat().st_mtime_ns for p in path.iterdir()}
    args["cosmetic_metadata"] = {"created_at": "today", "display_label": "two"}
    assert store.publish_artifact(**args) == path
    assert snapshot(path) == original
    assert timestamps == {p.name: p.stat().st_mtime_ns for p in path.iterdir()}
    first = store.verify_artifact(path)
    args["artifact_id"] = "other_explicit_id"
    args["files"] = {"payload.txt": b"BBBB"}
    second = store.verify_artifact(store.publish_artifact(**args))
    assert first.production_key == second.production_key
    assert first.content_hash != second.content_hash


@pytest.mark.parametrize("field", ["seed", "timestamp", "serializer_version"])
def test_behavioral_metadata_and_serializer_changes_conflict(tmp_path: Path, field: str) -> None:
    from xlm.artifacts.store import ArtifactConflictError

    store = ArtifactStore(ArtifactPaths(root=tmp_path / "store"))
    store.publish_artifact(**request())
    args = request()
    if field == "serializer_version":
        args[field] = "changed_serializer/2"
    else:
        args["metadata"][field] = 7
    with pytest.raises(ArtifactConflictError, match="provenance"):
        store.publish_artifact(**args)


@pytest.mark.parametrize("damage", ["payload", "identity", "incomplete", "extra_file"])
def test_damaged_existing_destination_is_never_repaired(tmp_path: Path, damage: str) -> None:
    import json

    from xlm.artifacts.store import ArtifactConflictError

    store = ArtifactStore(ArtifactPaths(root=tmp_path / "store"))
    path = store.publish_artifact(**request())
    if damage == "payload":
        (path / "payload.txt").write_bytes(b"BBBB")
    elif damage == "identity":
        manifest = json.loads((path / "manifest.json").read_text())
        manifest["producer_code_hash"] = digest("different declared producer")
        (path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    elif damage == "incomplete":
        (path / "_COMPLETED").unlink()
    else:
        (path / "extra.txt").write_text("unlisted", encoding="utf-8")
    before = snapshot(path)
    with pytest.raises(ArtifactConflictError, match="corrupt or incomplete"):
        store.publish_artifact(**request())
    assert snapshot(path) == before
    assert list(store.staging_dir.iterdir()) == []


def test_legacy_integrity_is_not_automatic_equivalent_reuse(tmp_path: Path) -> None:
    from xlm.artifacts.store import LegacyArtifactReuseError

    store = ArtifactStore(ArtifactPaths(root=tmp_path / "store"))
    path = legacy_artifact(store.paths.root, "authored_d01")
    before = snapshot(path)
    assert store.verify_artifact(path).identity_scope == "legacy-checksum-only"
    with pytest.raises(LegacyArtifactReuseError, match="legacy checksum-only"):
        store.publish_artifact(**request())
    assert snapshot(path) == before


def test_verified_input_manifest_fingerprint_is_bound_and_checked(tmp_path: Path) -> None:
    store = ArtifactStore(ArtifactPaths(root=tmp_path / "store"))
    parent_args = request()
    parent_args.update(artifact_id="authored_parent_a", input_artifact_ids=[])
    parent = store.publish_artifact(**parent_args)
    args = request()
    args["input_artifact_paths"] = {"authored_parent_a": parent}
    child = store.publish_artifact(**args)
    manifest = store.verify_artifact(child)
    assert manifest.input_manifest_hashes == {
        "authored_parent_a": hashlib.sha256((parent / "manifest.json").read_bytes()).hexdigest()
    }
    assert store.publish_artifact(**args) == child
    (parent / "payload.txt").write_bytes(b"XXXX")
    before = snapshot(child)
    with pytest.raises(ValueError, match="checksum mismatch"):
        store.publish_artifact(**args)
    assert snapshot(child) == before


@pytest.mark.parametrize("field", ["artifact_id", "kind"])
@pytest.mark.parametrize(
    "value",
    [
        "../escape",
        "..\\escape",
        "/absolute",
        "C:\\absolute",
        "C:relative",
        "\\\\server\\share",
        "con",
        "NUL.txt",
        "com¹",
        "name.",
        "name ",
        "a:b",
        ".locks",
        "",
    ],
)
def test_invalid_identifiers_fail_before_store_creation(
    tmp_path: Path, field: str, value: str
) -> None:
    store = ArtifactStore(ArtifactPaths(root=tmp_path / "absent"))
    args = request()
    args[field] = value
    with pytest.raises(ValueError):
        store.publish_artifact(**args)
    assert not store.paths.root.exists()


@pytest.mark.parametrize(
    "names",
    [
        ["../escape"],
        ["..\\escape"],
        ["/absolute"],
        ["C:/absolute"],
        ["C:relative"],
        ["\\\\server\\share"],
        ["a//b"],
        ["a/./b"],
        ["a/../b"],
        ["dir/con.txt"],
        ["a:b"],
        ["manifest.json"],
        ["_COMPLETED"],
        ["MANIFEST.JSON/nested"],
        ["Name", "name"],
        ["prefix", "prefix/child"],
        ["dir/A", "DIR/a"],
        ["dir/A", "DIR/b"],
        ["name."],
    ],
)
def test_payload_escape_marker_injection_and_collisions_have_no_side_effects(
    tmp_path: Path, names: list[str]
) -> None:
    store = ArtifactStore(ArtifactPaths(root=tmp_path / "absent"))
    args = request()
    args["files"] = {name: b"x" for name in names}
    with pytest.raises(ValueError):
        store.publish_artifact(**args)
    assert not store.paths.root.exists()


def test_streaming_limits_preserve_foreign_staging(tmp_path: Path) -> None:
    from xlm.artifacts.store import CHUNK_BYTES

    store = ArtifactStore(ArtifactPaths(root=tmp_path / "store"), max_publication_bytes=3)
    store.staging_dir.mkdir(parents=True)
    foreign = store.staging_dir / "another_attempt"
    foreign.mkdir()
    (foreign / "keep").write_bytes(b"owned by another attempt")
    args = request()
    args["files"] = {"payload.txt": "😀"}  # One character, four UTF-8 bytes.
    with pytest.raises(ValueError, match="byte limit"):
        store.publish_artifact(**args)
    assert list(store.staging_dir.iterdir()) == [foreign]
    assert not (store.paths.root / "clean").exists()
    source = tmp_path / "input.bin"
    source.write_bytes(b"a" * (CHUNK_BYTES * 3 + 1))
    chunks = list(store._chunks(source))
    assert max(map(len, chunks)) <= CHUNK_BYTES
    assert b"".join(chunks) == source.read_bytes()


@pytest.mark.parametrize("site", ["kind", "staging", "locks", "payload"])
def test_existing_junction_cannot_redirect_publication(tmp_path: Path, site: str) -> None:
    import os
    import subprocess

    store = ArtifactStore(ArtifactPaths(root=tmp_path / "store"))
    store.paths.root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "sentinel"
    sentinel.write_bytes(b"unchanged")
    if site == "payload":
        published = store.publish_artifact(**request())
        target = published / "redirect"
    else:
        target = (
            store.paths.root / {"kind": "clean", "staging": ".staging", "locks": ".locks"}[site]
        )
    if os.name == "nt":
        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "New-Item -ItemType Junction -Path $env:XLM_TEST_LINK "
                "-Target $env:XLM_TEST_TARGET -ErrorAction Stop | Out-Null",
            ],
            env={**os.environ, "XLM_TEST_LINK": str(target), "XLM_TEST_TARGET": str(outside)},
            capture_output=True,
            timeout=15,
            check=False,
        )
        assert result.returncode == 0, result.stderr.decode(errors="replace")
    else:
        target.symlink_to(outside, target_is_directory=True)
    before = snapshot(outside)
    with pytest.raises(ValueError, match="symlink/junction"):
        if site == "payload":
            store.verify_artifact(published)
        else:
            store.publish_artifact(**request())
    assert snapshot(outside) == before
    # Unlink only the test-owned link, never recurse through it.
    if os.name == "nt":
        target.rmdir()
    else:
        target.unlink()


def test_actual_public_cli_current_and_legacy_artifacts(tmp_path: Path) -> None:
    import json
    import os
    import subprocess
    import sys

    root = Path(__file__).resolve().parents[1]
    home = tmp_path / "home"
    source = tmp_path / "fixture.txt"
    source.write_text("An authored local fixture for immutable publication.\n", encoding="utf-8")
    env = {**os.environ, "XLM_HOME": str(home), "PYTHONPATH": str(root / "src")}

    def cli(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "xlm.cli.main", *args],
            env=env,
            cwd=tmp_path,
            text=True,
            capture_output=True,
            timeout=30,
        )

    args = (
        "data",
        "import-local",
        "--input",
        str(source),
        "--source-id",
        "d01_cli",
        "--format",
        "text",
        "--output-dir",
        str(tmp_path / "scratch"),
        "--publish",
    )
    first = cli(*args)
    assert first.returncode == 0, first.stderr
    path = home / "clean/canonical_d01_cli"
    original = snapshot(path)
    second = cli(*args)
    assert second.returncode == 0, second.stderr
    assert snapshot(path) == original
    result = cli("artifact", "inspect", "canonical_d01_cli", "--json")
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["schema_version"] == 2 and data["identity_scope"] == "declared-request-v2"
    assert cli("artifact", "verify", "canonical_d01_cli").returncode == 0
    source.write_text("Different authored text with the same publication name.\n", encoding="utf-8")
    changed = cli(*args)
    assert changed.returncode != 0
    assert "Artifact conflict" in changed.stderr + changed.stdout
    assert snapshot(path) == original
    legacy = legacy_artifact(home, "canonical_d01_legacy")
    preserved = snapshot(legacy)
    inspected = cli("artifact", "inspect", legacy.name, "--json")
    assert inspected.returncode == 0
    assert json.loads(inspected.stdout)["identity_scope"] == "legacy-checksum-only"
    verified = cli("artifact", "verify", legacy.name)
    assert verified.returncode == 0 and "Legacy integrity only" in verified.stdout
    legacy_args = tuple("d01_legacy" if arg == "d01_cli" else arg for arg in args)
    refused = cli(*legacy_args)
    assert refused.returncode != 0
    refusal_text = " ".join((refused.stdout + refused.stderr).split())
    assert "legacy checksum-only artifact cannot be reused" in refusal_text
    assert snapshot(legacy) == preserved
    rebuilt = cli("artifact", "rebuild-ledger")
    assert rebuilt.returncode == 0 and "Legacy checksum-only artifacts: 1" in rebuilt.stdout
    assert snapshot(legacy) == preserved
    (legacy / "payload.txt").write_bytes(b"broken")
    assert cli("artifact", "verify", legacy.name).returncode != 0


@pytest.mark.parametrize("conflicting", [False, True])
def test_fresh_process_publishers_have_one_permitted_outcome(
    tmp_path: Path, conflicting: bool
) -> None:
    import json
    import os
    import subprocess
    import sys
    import time

    root = Path(__file__).resolve().parents[1]
    code = """
import json, sys, time
from pathlib import Path
from xlm.artifacts.store import ArtifactStore, ArtifactConflictError
from xlm.core.paths import ArtifactPaths
import xlm.artifacts.store as implementation
root, ready, gate, receipt, payload = map(Path, sys.argv[1:])
ready.write_text('ready')
deadline = time.monotonic() + 15
while not gate.exists():
    if time.monotonic() > deadline:
        raise TimeoutError('test gate expired')
    time.sleep(0.02)
result = {'origin': implementation.__file__}
try:
    path = ArtifactStore(ArtifactPaths(root=root)).publish_artifact(
        artifact_id='shared', kind='clean', files={'payload': str(payload)},
        producer_code_hash='authored_code', dependency_hash='authored_lock',
        resolved_config_hash='authored_config')
    result.update(status='published', payload=(path / 'payload').read_text())
    status = 0
except ArtifactConflictError as exc:
    result.update(status='conflict', error=str(exc))
    status = 2
receipt.write_text(json.dumps(result))
raise SystemExit(status)
"""
    gate = tmp_path / "gate"
    processes = []
    handles = []
    try:
        for index, payload in enumerate(["AAAA", "BBBB" if conflicting else "AAAA"]):
            handle = (tmp_path / f"worker{index}.log").open("w")
            handles.append(handle)
            processes.append(
                subprocess.Popen(
                    [
                        sys.executable,
                        "-c",
                        code,
                        str(tmp_path / "store"),
                        str(tmp_path / f"ready{index}"),
                        str(gate),
                        str(tmp_path / f"result{index}.json"),
                        payload,
                    ],
                    env={**os.environ, "PYTHONPATH": str(root / "src")},
                    cwd=tmp_path,
                    stdout=handle,
                    stderr=handle,
                )
            )
        deadline = time.monotonic() + 15
        while not all((tmp_path / f"ready{i}").exists() for i in range(2)):
            assert time.monotonic() < deadline, "workers did not reach start barrier"
            time.sleep(0.02)
        gate.write_text("go")
        statuses = sorted(p.wait(timeout=20) for p in processes)
        assert statuses == ([0, 2] if conflicting else [0, 0])
        results = [json.loads((tmp_path / f"result{i}.json").read_text()) for i in range(2)]
        assert all(
            Path(r["origin"]).resolve() == root / "src/xlm/artifacts/store.py" for r in results
        )
        store = ArtifactStore(ArtifactPaths(root=tmp_path / "store"))
        published = store.paths.root / "clean/shared"
        assert store.verify_artifact(published).schema_version == 2
        assert (published / "payload").read_text() in ("AAAA", "BBBB")
        assert list(store.staging_dir.iterdir()) == []
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
        for handle in handles:
            handle.close()


@pytest.mark.parametrize("initial,changed", [(1, True), (1, 1.0)])
def test_json_types_are_part_of_request_identity(
    tmp_path: Path, initial: Any, changed: Any
) -> None:
    from xlm.artifacts.store import ArtifactConflictError

    store = ArtifactStore(ArtifactPaths(root=tmp_path / "store"))
    args = request()
    args["metadata"] = {"behavior": initial}
    path = store.publish_artifact(**args)
    before = snapshot(path)
    args["metadata"] = {"behavior": changed}
    with pytest.raises(ArtifactConflictError, match="provenance"):
        store.publish_artifact(**args)
    assert snapshot(path) == before


def test_verification_is_bounded_and_unresolved_lineage_is_explicit(tmp_path: Path) -> None:
    paths = ArtifactPaths(root=tmp_path / "store")
    store = ArtifactStore(paths)
    path = store.publish_artifact(**request())
    manifest = store.verify_artifact(path)
    assert manifest.unresolved_input_artifact_ids == ["authored_parent_a"]
    assert manifest.input_manifest_hashes == {}
    with pytest.raises(ValueError, match="verification exceeds payload byte limit"):
        ArtifactStore(paths, max_publication_bytes=3).verify_artifact(path)


@pytest.mark.parametrize("kind", ["ledger", "runs", "TMP"])
def test_internal_kind_rejected_before_side_effects(tmp_path: Path, kind: str) -> None:
    store = ArtifactStore(ArtifactPaths(root=tmp_path / "absent"))
    args = request()
    args["kind"] = kind
    with pytest.raises(ValueError, match="Reserved artifact kind"):
        store.publish_artifact(**args)
    assert not store.paths.root.exists()


def test_path_alias_and_ambiguous_cli_lookup_are_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from typer.testing import CliRunner

    from xlm.cli.main import app

    store = ArtifactStore(ArtifactPaths(root=tmp_path / "store"))
    args = request()
    original = store.publish_artifact(**args)
    before = snapshot(original)
    args["artifact_id"] = "AUTHORED_D01"
    with pytest.raises(ValueError, match="path alias"):
        store.publish_artifact(**args)
    args = request()
    args["kind"] = "other_kind"
    store.publish_artifact(**args)
    monkeypatch.setenv("XLM_HOME", str(store.paths.root))
    runner = CliRunner()
    result = runner.invoke(app, ["artifact", "inspect", "authored_d01", "--json"])
    assert result.exit_code == 1 and "Ambiguous artifact ID" in result.output
    assert runner.invoke(app, ["artifact", "verify", str(original)]).exit_code == 0
    assert snapshot(original) == before
