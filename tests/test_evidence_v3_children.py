"""Derived CHILD artifacts: exact regeneration and output-path independence.

The builder is run twice in clean interpreters — once with a RELATIVE and
once with an ABSOLUTE output directory — and both outputs must be
byte-identical to the committed child tree. No artifact may embed an
absolute builder/checkout path. Readiness must be derived (not asserted),
authorization NONE and executable false.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from evidence_v3_support import REPO, load_json

from xlm.data.evidence_v2 import canonical
from xlm.data.evidence_v3 import envidentity, plan

CHILD = REPO / plan.CHILD_DIR


def _build(out_dir: str, cwd: Path, commit: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(REPO / "scripts/evidence_v3.py"),
            "build-v3-children",
            "--out-dir",
            out_dir,
            "--implementation-commit",
            commit,
        ],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=1200,
        check=False,
        env={**os.environ, "OMP_NUM_THREADS": "1", "TOKENIZERS_PARALLELISM": "false"},
    )


@pytest.mark.slow
def test_regeneration_is_exact_for_relative_and_absolute_output(tmp_path: Path) -> None:
    commit = load_json(CHILD / plan.MANIFEST_FILE)["producer"]["implementation_commit"]
    relative = _build("rel-out", tmp_path, commit)
    assert relative.returncode == 0, relative.stderr[-3000:]
    absolute = _build(str(tmp_path / "abs-out"), REPO, commit)
    assert absolute.returncode == 0, absolute.stderr[-3000:]
    committed = sorted(p.name for p in CHILD.iterdir() if p.is_file())
    for out in (tmp_path / "rel-out", tmp_path / "abs-out"):
        assert sorted(p.name for p in out.iterdir()) == committed
        for name in committed:
            assert (out / name).read_bytes() == (CHILD / name).read_bytes(), name


def test_children_embed_no_absolute_paths() -> None:
    pattern = re.compile(rb"[A-Za-z]:[\\/]{1,2}(Project|Users|Windows|tmp)", re.IGNORECASE)
    for path in CHILD.iterdir():
        raw = path.read_bytes()
        assert not pattern.search(raw.replace(b"G:/Project/xlm-evidence-v3/essential-web", b"")), (
            path.name
        )


def test_manifest_binds_every_child_by_repository_path() -> None:
    manifest = load_json(CHILD / plan.MANIFEST_FILE)
    assert canonical.self_digest(manifest) == manifest["digest"]
    assert manifest["authorization"] == "NONE" and manifest["executable"] is False
    listed = manifest["payload"]["artifacts"]
    names = {p.name for p in CHILD.iterdir()} - {plan.MANIFEST_FILE}
    assert set(listed) == names
    for name, cell in listed.items():
        raw = (CHILD / name).read_bytes()
        assert cell["path"] == f"{plan.CHILD_DIR}/{name}"
        assert cell["bytes"] == len(raw)
        import hashlib

        assert cell["sha256"] == hashlib.sha256(raw).hexdigest()
        body = canonical.loads_bytes_strict(raw)
        assert cell["canonical_digest"] == body["digest"] == canonical.self_digest(body)
        assert body["producer"] == manifest["producer"]


def test_child_code_identity_is_committed_blob_sha256() -> None:
    producer = load_json(CHILD / plan.MANIFEST_FILE)["producer"]
    commit = producer["implementation_commit"]
    assert envidentity.is_commit(commit)
    code = producer["code_hashes"]
    assert set(code) == set(envidentity.code_paths(REPO))
    assert all(envidentity.is_sha256(v) for v in code.values())
    assert code == envidentity.committed_identity(REPO, commit, list(code))
    assert producer["code_identity_method"] == "sha256-of-committed-git-blob-at-implementation-commit"
    assert set(producer["checkout_representation"].values()) <= {"exact", "crlf"}


def test_readiness_is_derived_and_not_authorized() -> None:
    review = load_json(CHILD / "readiness_review.json")["payload"]
    assert review["authorization"] == "NONE"
    assert review["executable"] is False
    assert review["verdict"] == "READY_FOR_PHASE_P_AUTHORIZATION_REVIEW"
    statuses = {k: v["status"] for k, v in review["mechanisms"].items()}
    assert statuses["live_https_transport"] == "UNVERIFIED_LIVE"
    assert statuses["live_source_identity_and_footers"] == "UNVERIFIED_LIVE"
    assert set(statuses.values()) <= {
        "VERIFIED_STATIC",
        "VERIFIED_SYNTHETIC_INTEGRATION",
        "UNVERIFIED_LIVE",
    }
    for name in (
        "windows_junction_containment",
        "memory_supervision_during_work",
        "future_d_reservation",
        "integrated_phase_p_run",
    ):
        assert statuses[name] == "VERIFIED_SYNTHETIC_INTEGRATION"
